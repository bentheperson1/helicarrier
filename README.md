# Megadrone Helicarrier

Repo for any on-board code for the UTD AIAA Helicarrier drone.

## Contents

| Path | What it is |
|---|---|
| `realtime_gps.py` | Prints live GPS data (lat/lon/alt/fix/satellites) from the FC |
| `config/main.conf` | mavlink-router config used on the Pi |
| `tools/heartbeat_check.py` | Quick check that the FC is talking through mavlink-router (no pymavlink needed) |

## Hardware setup

- Raspberry Pi 4 Model B, Debian 13 (trixie), user `pilot`, hostname `megadrone`
- Flight controller running ArduPilot
- Pi GPIO UART (`/dev/ttyAMA0`, pins 8 TX / 10 RX / 6 GND) wired to the FC's **TELEM2** port
- FC parameters: `SERIAL2_PROTOCOL = 2` (MAVLink2), `SERIAL2_BAUD = 115` (115200)
- Linux serial console is **disabled** on the UART (no `console=serial0` in `/boot/firmware/cmdline.txt`)

## Wireless ground station: mavlink-router

Only one program can open the serial port at a time. **mavlink-router** owns `/dev/ttyAMA0`
and shares the MAVLink stream over the network, so Mission Planner, QGroundControl, and our
scripts can all be connected at once:

```
FC --(serial 115200)--> Pi: mavlink-router --(TCP :5760)--> Mission Planner / QGC / scripts
```

### Building and installing (on the Pi)

```bash
sudo apt install -y git meson ninja-build pkg-config gcc g++
git clone https://github.com/mavlink-router/mavlink-router.git ~/mavlink-router
cd ~/mavlink-router
git submodule update --init --recursive
meson setup build . -Dprefix=/usr -Dsystemdsystemunitdir=/usr/lib/systemd/system
nohup ninja -C build -j2 > ~/build.log 2>&1 &   # takes a few minutes; survives SSH drops
tail -f ~/build.log                              # Ctrl+C once it reaches [25/25]
sudo ninja -C build install
which mavlink-routerd                            # should print /usr/bin/mavlink-routerd
```

**Why `-Dsystemdsystemunitdir`:** without it, meson looks up the systemd install path via the
`systemd-dev` package and fails with `Dependency "systemd" not found`. Setting the path
directly skips that lookup, so the build works even without internet access.

### Configure and start

```bash
sudo mkdir -p /etc/mavlink-router
sudo cp config/main.conf /etc/mavlink-router/main.conf
sudo systemctl enable --now mavlink-router
systemctl status mavlink-router --no-pager | head -5   # should say active (running)
journalctl -u mavlink-router -n 20 --no-pager          # should show "speed = 115200" and "Opened TCP Server"
```

### Verify the FC is talking

```bash
python3 tools/heartbeat_check.py
```

Expected: `HEARTBEATS: {1: 8}` (FC is system 1, ~1 heartbeat/sec).
Garbled data with no heartbeats means the baud is wrong (57600 and 921600 do **not** work on this setup).

### Connecting a ground station

Laptop must be on the same network as the Pi.

- **QGroundControl** (Mac/Windows/Linux): Q icon → Application Settings → Comm Links → Add →
  Type **TCP**, Server `megadrone.local`, Port `5760` → Connect.
- **Mission Planner** (Windows): connection dropdown (top right) → **TCP** → Connect →
  host `megadrone.local`, port `5760`.

If `megadrone.local` doesn't resolve, run `hostname -I` on the Pi and use that IP.
**Always remove props before connecting on the bench** — the ground station can arm the motors.

### Scripts on the Pi

Scripts must connect through the router, not the serial port (the port is busy):

```python
master = mavutil.mavlink_connection("tcp:127.0.0.1:5760", source_system=200)
```

`source_system=200` avoids clashing with Mission Planner/QGC, which use system ID 255.

## Known issues / TODO

- The Pi currently has no internet on its Wi-Fi, so `apt`/`pip` don't work and the clock drifts.
  `pymavlink` needs to be installed (in a venv: `python3 -m venv ~/venv && ~/venv/bin/pip install pymavlink`).
- `PreArm: Check mag field` appears indoors — needs an outdoor compass calibration before flying.
- Field operation: the Pi needs to host its own hotspot when no router is available.

## Ground control app (`ground_control/`)

Desktop app (PySide6) that connects to the drone through mavlink-router.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python ground_control/app.py
```

Connect to `tcp:megadrone.local:5760` (or `tcp:<pi-ip>:5760`).

| File | What it does |
|---|---|
| `app.py` | Main window: connection bar, **Flight** tab (telemetry, servo panel, log, map) and **Preflight** tab |
| `mavlink_manager.py` | Background MAVLink thread; parses telemetry, runs queued commands |
| `map_view.py` | Leaflet map: drone marker (rotates with heading), flight trail, follow/center, save area offline |
| `tile_cache.py` | Saves every map tile to `~/.megadrone_gcs/tiles/` so the map works without internet |
| `preflight.py` | Preflight tab: each check has its own Run button; thresholds at the top of the file |
| `servo_panel.py` | Sends `MAV_CMD_DO_SET_SERVO` and shows the live output PWM (`SERVO_OUTPUT_RAW`) |
| `web/` | Bundled Leaflet, so the map works offline (imagery tiles still need internet) |

### Servo control notes

- Servos plug into the **flight controller's** output pins. The command goes
  GCS → mavlink-router on the Pi → FC. Output # matches the FC's `SERVOn` numbering
  (on most Pixhawk-style boards, AUX1 = output 9).
- ArduPilot **refuses** outputs assigned to motors (returns `MAV_RESULT_FAILED`). For the
  servo's output, set `SERVOn_FUNCTION = 0` (Disabled) so the FC accepts `DO_SET_SERVO`.
- If outputs don't move, check the safety switch (`BRD_SAFETY_DEFLT = 0` disables it on the bench).
- **Remove props before testing.** The app asks for confirmation if the drone is armed.

### Map at the field (no internet on the drone's Wi-Fi)

1. On school Wi-Fi, open ground control and pan/zoom over the flying area,
   or zoom to the field and click **Save area offline** (saves a few zoom levels around it).
2. Switch the laptop to the drone's network and connect. The saved imagery stays, and the
   drone is drawn on top of it. Tiles are kept between app restarts.

### Preflight tab

Each automatic check (link, disarmed, battery, GPS, EKF, sensors, ArduPilot pre-arm, level,
RC, failsafe settings, flight mode) has its own **Run** button and shows PASS / WARN / FAIL,
the reason, and the time it was checked. Set the **battery cell count** at the top.
Manual items (props, strap, area clear...) are ticked individually.
The log on the Flight tab records every mode change and arm/disarm with a timestamp.

See `docs/FAILSAFE_TESTS.md` for the failsafe test procedure.

