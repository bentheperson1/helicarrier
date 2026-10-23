# Failsafe tests: megababy

Goal: prove that when something goes wrong (transmitter lost, battery low, ground
station lost, drone leaves the fence) the drone does what we configured, *before*
we rely on it with the big drone.

Do the **bench tests first** (props off), and only fly the **flight tests** once
every bench test passes.

## Before any test

- [ ] Ground control **Preflight tab → Failsafe settings → Run** and screenshot the result.
      Write down every value in the table below **before** you change anything.
- [ ] Know how to take control back: after a failsafe, once the transmitter is working,
      **flip the flight-mode switch** (e.g. to STABILIZE or LOITER) and the pilot has control again.
      Practice this on the bench.
- [ ] Ground control log is open: every failsafe shows as a timestamped `MODE: X -> Y` line plus
      the flight controller's own message (e.g. `FC: Radio Failsafe`).

| Parameter | Original value | Meaning |
|---|---|---|
| `FS_THR_ENABLE` | | RC loss action (0 off, 1 RTL, 3 Land) |
| `FS_THR_VALUE` | | Throttle PWM below this = RC lost |
| `BATT_LOW_VOLT` / `BATT_FS_LOW_ACT` | | Low battery voltage / action (2 = RTL) |
| `BATT_CRT_VOLT` / `BATT_FS_CRT_ACT` | | Critical battery voltage / action (1 = Land) |
| `FS_GCS_ENABLE` | | Ground station loss action (0 off) |
| `FENCE_ENABLE`, `FENCE_RADIUS`, `FENCE_ALT_MAX` | | Geofence on / radius / max height |
| `RTL_ALT` | | Height (cm) it climbs to before returning |

## Bench tests: PROPS OFF

Drone on the bench, props removed, battery plugged in, GPS lock if possible (outside / window).

### B1. Receiver failsafe output
1. Transmitter on. Preflight → **RC transmitter → Run**: should PASS with throttle ~1000 µs.
2. Turn the transmitter **off**. Run it again within a few seconds.
3. **Pass:** the check FAILs with "No RC receiver data" *or* throttle drops **below `FS_THR_VALUE`**.
   If throttle stays at a normal value, the receiver is holding the last stick position and
   the FC can't tell the radio is gone. **Fix the receiver failsafe setting before going further.**

### B2. RC-loss failsafe
1. Transmitter on, STABILIZE, arm (props off!), raise throttle a little above minimum.
2. Turn the transmitter **off**.
3. **Pass:** log shows `Radio Failsafe` and `MODE: STABILIZE -> RTL` (or LAND, matching `FS_THR_ENABLE`).
   With throttle at minimum it may simply disarm instead; that is also correct behaviour.
4. Transmitter back on, flip the mode switch, disarm.

### B3. Battery failsafe
1. Note the current battery voltage (Preflight → Battery voltage).
2. Temporarily set `BATT_LOW_VOLT` to **0.2 V above** the current voltage.
3. Arm (props off) and wait ~10 s (`BATT_LOW_TIMER`).
4. **Pass:** log shows a battery failsafe message and the mode changes to the `BATT_FS_LOW_ACT` action.
5. Disarm. **Set `BATT_LOW_VOLT` back to the original value** and re-run the Failsafe settings check to confirm.

### B4. Ground station loss (only if `FS_GCS_ENABLE` is not 0)
1. Arm (props off) with ground control connected.
2. Click **Disconnect** in ground control.
3. **Pass:** after ~5 s (`FS_GCS_TIMEOUT`) the mode changes. Reconnect to see it in the log.

## Flight tests: megababy only

Open field, nobody downrange, experienced pilot on the sticks, a second person on ground
control calling out the log. Hover **5-10 m up, ~20 m away**, in LOITER with a good GPS lock.

| # | Test | Do | Expect | Abort if |
|---|---|---|---|---|
| F1 | RC loss | Hover, then switch transmitter **off** | `MODE -> RTL`: climbs to `RTL_ALT`, flies home, lands | It drifts or doesn't change mode within ~2 s: transmitter back on, flip mode switch |
| F2 | Retake control | During F1's RTL, transmitter on, flip mode switch | Pilot has control again | Mode doesn't change |
| F3 | Geofence | Set a small fence (e.g. `FENCE_RADIUS` 30 m, `FENCE_ALT_MAX` 15 m), fly slowly toward it | Stops / RTLs at the boundary | It passes the boundary by more than a few metres |
| F4 | Low battery (optional) | Fly a battery down normally while watching voltage | RTL triggers at `BATT_LOW_VOLT` | Voltage drops fast or below `BATT_CRT_VOLT` before RTL: land manually |

After the flight tests, **restore any parameters you changed** (fence radius etc.) and re-run the
Preflight checks.

## Record results

| Test | Date | Pass/Fail | Mode change seen in log | Notes |
|---|---|---|---|---|
| B1 Receiver output | | | | |
| B2 RC loss | | | | |
| B3 Battery | | | | |
| B4 GCS loss | | | | |
| F1 RC loss flight | | | | |
| F2 Retake control | | | | |
| F3 Geofence | | | | |
| F4 Low battery | | | | |

Also download the flight controller's dataflash log after each session (QGC → Analyze → Log Download)
so failures can be looked at afterwards.
