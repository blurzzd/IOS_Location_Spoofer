# iOS Location Spoofer

**CustomTkinter desktop GUI for iOS location simulation and route navigation via USB/RSD tunnels.**

A modern, user-friendly tool to simulate GPS locations on physical iOS devices (no jailbreak required). Supports single-point spoofing and animated route navigation with realistic speeds. Tested on **iOS 27 Dev Beta 2 (build 24A5370H)**.

> **Strictly for development and testing purposes.**

## Features

- **Interactive map** – Click to select locations (powered by TkinterMapView)
- **Single Point mode** – Instantly set a fixed location
- **Moving Route mode** – Simulate movement along a real road route
  - Automatic route calculation via OSRM (Open Source Routing Machine)
  - Speed presets: Walking (5 km/h), Running (10 km/h), Car (120 km/h), Truck (90 km/h)
  - Custom speed support
  - Smooth interpolation along the route
- **System tray support** – Minimize to tray and continue spoofing in the background
- **iOS 17+ compatible** – Uses RSD (Remote Service Discovery) tunnels with automatic fallback
- **Cross-platform** – Works on macOS, Windows, and Linux (via `pymobiledevice3`)

## Requirements

- Python 3.9+
- A physical iOS device connected via USB
- Developer Mode enabled on the iPhone/iPad
- Trusted computer (device trusts the host)

### Dependencies

```
customtkinter
tkintermapview>=1.30
pystray
pillow
pymobiledevice3>=11.3.1
```

## Installation

1. Clone the repository:

```bash
git clone https://github.com/blurzzd/IOS_Location_Spoofer.git
cd IOS_Location_Spoofer
```

2. Create a virtual environment (recommended):

```bash
python -m venv venv
source venv/bin/activate        # macOS/Linux
# or
venv\Scripts\activate           # Windows
```

3. Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage

1. Connect your iOS device via USB and unlock it.
2. Ensure the device trusts this computer.
3. Run the application:

```bash
python main.py
```

### Single Point Mode

1. Select **Single Point** in the mode selector.
2. Click anywhere on the map to drop a pin.
3. Click **Change Location**.

The location will be set and periodically refreshed.

### Moving Route Mode

1. Select **Moving Route**.
2. Click the map to set a **Start** point.
3. Click again to set an **End** point.
4. Choose a speed (or enter a custom value in km/h).
5. Click **Start Route**.

The app will fetch a real driving/walking route (when available) and smoothly move the simulated location along it.

### System Tray

- Click **Minimize to Tray** or close the window to hide the app.
- Right-click the tray icon for options: **Restore**, **Stop Spoofing**, or **Quit**.

## How It Works

The tool uses Apple’s official **Location Simulation** developer service (via `pymobiledevice3`):

- On older iOS versions it connects through classic lockdown.
- On iOS 17+ it automatically falls back to a **Userspace RSD tunnel** (no root required in most cases).

Location updates are sent using the DVT `LocationSimulation` instrument. The simulated location is treated as a real GPS fix by the system.

## Compatibility

| iOS Version       | Status                          |
|-------------------|---------------------------------|
| iOS 17+           | Fully supported (RSD tunnel)    |
| iOS 27 Dev Beta 2 | Tested (build 24A5370H)         |
| Older iOS         | Supported via lockdown          |

> Note: Developer Mode must be enabled and the device must trust the host computer.

## Disclaimer

This project is intended **strictly for development, testing, and educational purposes**.

- Do not use it to violate any app’s terms of service, location-based restrictions, or applicable laws.
- The authors assume no responsibility for misuse.

## License

This project is licensed under the **GNU General Public License v3.0** – see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) – Excellent pure-Python library for interacting with iOS devices
- [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter) – Modern UI components
- [TkinterMapView](https://github.com/TomSchimansky/TkinterMapView) – Interactive map widget
- OSRM – Open Source Routing Machine for route calculation
```
