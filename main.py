import asyncio
import json
import math
import threading
import time
import urllib.request
from contextlib import AsyncExitStack
from tkinter import messagebox

import customtkinter as ctk
from tkintermapview import TkinterMapView
from PIL import Image, ImageDraw
import pystray

from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.exceptions import PyMobileDevice3Exception, InvalidServiceError
from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

SINGLE_POINT_REFRESH_SECONDS = 30
MOVING_REFRESH_SECONDS = 2
DEFAULT_LAT = 37.3346
DEFAULT_LON = -122.0090
WALKING_KMH = 5
RUNNING_KMH = 10
TRUCK_KMH = 90
CAR_KMH = 120
PEDESTRIAN_THRESHOLD_KMH = 15
OSRM_BASE_URL = "https://router.project-osrm.org/route/v1"


async def open_location_simulation(stack):
    try:
        lockdown = await create_using_usbmux()
        await stack.enter_async_context(lockdown)
        dvt = await stack.enter_async_context(DvtProvider(lockdown))
        return await stack.enter_async_context(LocationSimulation(dvt))
    except InvalidServiceError:
        pass
    rsd = await stack.enter_async_context(UserspaceRsdTunnel())
    dvt = await stack.enter_async_context(DvtProvider(rsd))
    return await stack.enter_async_context(LocationSimulation(dvt))


def haversine_km(point_a, point_b):
    lat1, lon1 = point_a
    lat2, lon2 = point_b
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return 2 * radius_km * math.asin(math.sqrt(a))


def build_cumulative_distances(points):
    cumulative = [0.0]
    for index in range(1, len(points)):
        cumulative.append(cumulative[-1] + haversine_km(points[index - 1], points[index]))
    return cumulative


def interpolate_along_route(points, cumulative, traveled_km):
    if traveled_km <= 0:
        return points[0]
    if traveled_km >= cumulative[-1]:
        return points[-1]
    for index in range(1, len(cumulative)):
        if cumulative[index] >= traveled_km:
            segment_start = cumulative[index - 1]
            segment_length = cumulative[index] - segment_start
            fraction = 0 if segment_length == 0 else (traveled_km - segment_start) / segment_length
            lat1, lon1 = points[index - 1]
            lat2, lon2 = points[index]
            return (lat1 + (lat2 - lat1) * fraction, lon1 + (lon2 - lon1) * fraction)
    return points[-1]


def fetch_route(start, end, profile):
    start_lat, start_lon = start
    end_lat, end_lon = end
    coordinates = f"{start_lon},{start_lat};{end_lon},{end_lat}"
    url = f"{OSRM_BASE_URL}/{profile}/{coordinates}?overview=full&geometries=geojson"
    request = urllib.request.Request(url, headers={"User-Agent": "iphone-location-spoofer/1.0"})
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("code") != "Ok":
        raise ValueError(data.get("code", "routing failed"))
    coordinates_lonlat = data["routes"][0]["geometry"]["coordinates"]
    return [(lat, lon) for lon, lat in coordinates_lonlat]


def fetch_route_with_fallback(start, end, speed_kmh):
    if speed_kmh <= PEDESTRIAN_THRESHOLD_KMH:
        candidate_profiles = ["foot", "walking", "driving"]
    else:
        candidate_profiles = ["driving"]
    for profile in candidate_profiles:
        try:
            return fetch_route(start, end, profile), True
        except Exception:
            continue
    return [start, end], False


class SinglePointTask:
    def __init__(self, lat, lon):
        self.lat = lat
        self.lon = lon

    def begin(self, now):
        pass

    def current_position(self, now):
        return self.lat, self.lon

    def refresh_interval(self, now):
        return SINGLE_POINT_REFRESH_SECONDS

    def status_text(self, lat, lon, now):
        return f"Location set to {lat:.6f}, {lon:.6f}"


class MovingTask:
    def __init__(self, route_points, speed_kmh):
        self.route_points = route_points
        self.cumulative = build_cumulative_distances(route_points)
        self.total_km = self.cumulative[-1]
        self.speed_kmh = speed_kmh
        self.start_time = None

    def begin(self, now):
        if self.start_time is None:
            self.start_time = now

    def traveled_km(self, now):
        hours = (now - self.start_time) / 3600.0
        return self.speed_kmh * hours

    def is_finished(self, now):
        return self.traveled_km(now) >= self.total_km

    def current_position(self, now):
        traveled = min(self.traveled_km(now), self.total_km)
        return interpolate_along_route(self.route_points, self.cumulative, traveled)

    def refresh_interval(self, now):
        return SINGLE_POINT_REFRESH_SECONDS if self.is_finished(now) else MOVING_REFRESH_SECONDS

    def status_text(self, lat, lon, now):
        if self.is_finished(now):
            return f"Arrived at {lat:.6f}, {lon:.6f}"
        traveled = self.traveled_km(now)
        percent = 0 if self.total_km == 0 else min(100, traveled / self.total_km * 100)
        return f"Moving: {traveled:.2f} / {self.total_km:.2f} km ({percent:.0f}%)"


class LocationSpooferApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("iPhone Location Spoofer")
        self.geometry("980x780")
        self.minsize(760, 620)

        self.mode = "single"
        self.selected_coords = None
        self.current_marker = None
        self.route_start = None
        self.route_end = None
        self.start_marker = None
        self.end_marker = None
        self.route_path = None
        self.current_task = None
        self.spoof_thread = None
        self.stop_event = threading.Event()
        self.loop = None
        self.location_simulation = None
        self.tray_icon = None

        self.map_widget = TkinterMapView(self, corner_radius=0)
        self.map_widget.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        self.map_widget.set_position(DEFAULT_LAT, DEFAULT_LON)
        self.map_widget.set_zoom(14)
        self.map_widget.add_left_click_map_command(self.on_map_click)
        self.map_widget.add_right_click_menu_command(
            label="Set Location Here",
            command=self.on_map_click,
            pass_coords=True
        )

        self.status_label = ctk.CTkLabel(self, text="Click anywhere on the map to drop a pin.")
        self.status_label.pack(pady=(0, 6))

        self.mode_selector = ctk.CTkSegmentedButton(
            self, values=["Single Point", "Moving Route"], command=self.on_mode_change
        )
        self.mode_selector.set("Single Point")
        self.mode_selector.pack(pady=(0, 8))

        self.speed_frame = ctk.CTkFrame(self, fg_color="transparent")

        speed_label = ctk.CTkLabel(self.speed_frame, text="Speed (km/h):")
        speed_label.grid(row=0, column=0, padx=(0, 6))

        self.speed_entry = ctk.CTkEntry(self.speed_frame, width=70)
        self.speed_entry.insert(0, str(WALKING_KMH))
        self.speed_entry.grid(row=0, column=1, padx=(0, 12))

        ctk.CTkButton(
            self.speed_frame, text="Walking", width=80, command=lambda: self.set_speed_preset(WALKING_KMH)
        ).grid(row=0, column=2, padx=3)
        ctk.CTkButton(
            self.speed_frame, text="Running", width=80, command=lambda: self.set_speed_preset(RUNNING_KMH)
        ).grid(row=0, column=3, padx=3)
        ctk.CTkButton(
            self.speed_frame, text="Car", width=80, command=lambda: self.set_speed_preset(CAR_KMH)
        ).grid(row=0, column=4, padx=3)
        ctk.CTkButton(
            self.speed_frame, text="Truck", width=80, command=lambda: self.set_speed_preset(TRUCK_KMH)
        ).grid(row=0, column=5, padx=3)

        self.button_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.button_frame.pack(pady=(0, 12))

        self.primary_button = ctk.CTkButton(
            self.button_frame, text="Change Location", width=180, command=self.on_primary_action
        )
        self.primary_button.grid(row=0, column=0, padx=6)

        self.minimize_button = ctk.CTkButton(
            self.button_frame, text="Minimize to Tray", width=180, command=self.hide_to_tray
        )
        self.minimize_button.grid(row=0, column=1, padx=6)

        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

    def on_mode_change(self, value):
        self.mode = "single" if value == "Single Point" else "moving"
        if self.mode == "single":
            self.clear_route_overlays()
            self.speed_frame.pack_forget()
            self.primary_button.configure(text="Change Location")
            self.set_status("Click anywhere on the map to drop a pin.")
        else:
            if self.current_marker is not None:
                self.current_marker.delete()
                self.current_marker = None
            self.selected_coords = None
            self.speed_frame.pack(pady=(0, 8), before=self.button_frame)
            self.primary_button.configure(text="Start Route")
            self.set_status("Click the map to set a start point.")

    def clear_route_overlays(self):
        if self.start_marker is not None:
            self.start_marker.delete()
            self.start_marker = None
        if self.end_marker is not None:
            self.end_marker.delete()
            self.end_marker = None
        if self.route_path is not None:
            self.route_path.delete()
            self.route_path = None
        self.route_start = None
        self.route_end = None

    def set_speed_preset(self, value):
        self.speed_entry.delete(0, "end")
        self.speed_entry.insert(0, str(value))

    def get_speed_kmh(self):
        try:
            value = float(self.speed_entry.get())
        except ValueError:
            return None
        return value if value > 0 else None

    def on_map_click(self, coords):
        if self.mode == "single":
            self.set_single_point(coords)
        else:
            self.set_route_point(coords)

    def set_single_point(self, coords):
        lat, lon = coords
        self.selected_coords = (lat, lon)
        if self.current_marker is not None:
            self.current_marker.delete()
        self.current_marker = self.map_widget.set_marker(lat, lon, text="Selected")
        self.set_status(f"Pin dropped at {lat:.6f}, {lon:.6f}")

    def set_route_point(self, coords):
        lat, lon = coords
        if self.route_start is None:
            self.route_start = (lat, lon)
            self.start_marker = self.map_widget.set_marker(
                lat, lon, text="Start",
                marker_color_circle="#1B7F3B", marker_color_outside="#2FA854"
            )
            self.set_status("Start set. Click the map again to set the end point.")
        elif self.route_end is None:
            self.route_end = (lat, lon)
            self.end_marker = self.map_widget.set_marker(lat, lon, text="End")
            self.set_status("End set. Choose a speed and press Start Route.")
        else:
            self.clear_route_overlays()
            self.route_start = (lat, lon)
            self.start_marker = self.map_widget.set_marker(
                lat, lon, text="Start",
                marker_color_circle="#1B7F3B", marker_color_outside="#2FA854"
            )
            self.set_status("Start set. Click the map again to set the end point.")

    def set_status(self, message):
        self.after(0, lambda: self.status_label.configure(text=message))

    def on_primary_action(self):
        if self.mode == "single":
            if self.selected_coords is None:
                messagebox.showwarning(
                    "No Location Selected",
                    "Click on the map to drop a pin before changing the location."
                )
                return
            self.launch_task(SinglePointTask(*self.selected_coords))
        else:
            if self.route_start is None or self.route_end is None:
                messagebox.showwarning(
                    "Route Incomplete",
                    "Click the map to set both a start and an end point first."
                )
                return
            speed_kmh = self.get_speed_kmh()
            if speed_kmh is None:
                messagebox.showwarning(
                    "Invalid Speed",
                    "Enter a valid speed in km/h, or choose one of the preset buttons."
                )
                return
            start = self.route_start
            end = self.route_end
            self.set_status("Calculating route...")
            threading.Thread(
                target=self.prepare_and_launch_route, args=(start, end, speed_kmh), daemon=True
            ).start()

    def prepare_and_launch_route(self, start, end, speed_kmh):
        route_points, routed = fetch_route_with_fallback(start, end, speed_kmh)
        self.after(0, lambda: self.draw_route_path(route_points))
        if not routed:
            self.set_status("Routing service unavailable, using a straight line instead.")
        self.launch_task(MovingTask(route_points, speed_kmh))

    def draw_route_path(self, route_points):
        if self.route_path is not None:
            self.route_path.delete()
        self.route_path = self.map_widget.set_path(route_points)

    def launch_task(self, task):
        if self.spoof_thread is not None and self.spoof_thread.is_alive():
            if self.loop is not None:
                try:
                    asyncio.run_coroutine_threadsafe(self.switch_task(task), self.loop)
                except RuntimeError:
                    pass
            return
        self.stop_event.clear()
        self.spoof_thread = threading.Thread(target=self.run_spoof_worker, args=(task,), daemon=True)
        self.spoof_thread.start()

    async def switch_task(self, task):
        self.current_task = task
        now = time.monotonic()
        task.begin(now)
        if self.location_simulation is not None:
            lat, lon = task.current_position(now)
            try:
                await self.location_simulation.set(lat, lon)
                self.set_status(task.status_text(lat, lon, now))
            except Exception as error:
                self.set_status(f"Failed to set location: {error}")

    def run_spoof_worker(self, task):
        asyncio.run(self.spoof_worker_main(task))

    async def spoof_worker_main(self, task):
        self.current_task = task
        try:
            self.set_status("Connecting to iPhone...")
            async with AsyncExitStack() as stack:
                location_simulation = await open_location_simulation(stack)
                self.location_simulation = location_simulation
                self.loop = asyncio.get_running_loop()
                while not self.stop_event.is_set():
                    active_task = self.current_task
                    now = time.monotonic()
                    active_task.begin(now)
                    lat, lon = active_task.current_position(now)
                    try:
                        await location_simulation.set(lat, lon)
                        self.set_status(active_task.status_text(lat, lon, now))
                    except Exception as error:
                        self.set_status(f"Failed to set location: {error}")
                    interval = active_task.refresh_interval(now)
                    for _ in range(interval):
                        if self.stop_event.is_set():
                            break
                        await asyncio.sleep(1)
                await location_simulation.clear()
                self.set_status("Spoofing stopped and location cleared.")
        except PyMobileDevice3Exception as error:
            self.set_status(f"Device error: {error}")
        except Exception as error:
            self.set_status(f"Failed to connect: {error}")
        finally:
            self.location_simulation = None
            self.loop = None

    def stop_spoofing(self):
        self.stop_event.set()
        self.set_status("Stopping...")

    def create_tray_image(self):
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((12, 4, 52, 44), fill=(10, 132, 255, 255))
        draw.polygon([(18, 38), (46, 38), (32, 60)], fill=(10, 132, 255, 255))
        draw.ellipse((24, 14, 40, 30), fill=(0, 0, 0, 0))
        return image

    def hide_to_tray(self):
        self.withdraw()
        if self.tray_icon is None:
            menu = pystray.Menu(
                pystray.MenuItem("Restore", self.on_tray_restore),
                pystray.MenuItem("Stop Spoofing", self.on_tray_stop_spoofing),
                pystray.MenuItem("Quit", self.on_tray_quit)
            )
            self.tray_icon = pystray.Icon(
                "iphone_location_spoofer",
                self.create_tray_image(),
                "iPhone Location Spoofer",
                menu
            )
            threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def on_tray_restore(self, icon, item):
        icon.stop()
        self.after(0, self.show_window)

    def show_window(self):
        self.tray_icon = None
        self.deiconify()
        self.lift()
        self.focus_force()

    def on_tray_stop_spoofing(self, icon, item):
        self.stop_spoofing()

    def on_tray_quit(self, icon, item):
        self.stop_event.set()
        if self.spoof_thread is not None:
            self.spoof_thread.join(timeout=15)
        icon.stop()
        self.after(0, self.destroy)


if __name__ == "__main__":
    app = LocationSpooferApp()
    app.mainloop()