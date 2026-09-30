from pymavlink import mavutil
import time

master = mavutil.mavlink_connection("/dev/ttyAMA0", baud=115200)

master.wait_heartbeat()
print("Heartbeat received from system (system %u component %u)" % 
      (master.target_system, master.target_component))

while True:
    msg = master.recv_match(type='GPS_RAW_INT', blocking=True)
    if not msg:
        continue

    print("\n")
    print("=" * 50)

    local_time = time.localtime()
    time_str = f"{local_time.tm_hour}:{local_time.tm_min}:{local_time.tm_sec}"
    print(time_str)

    # Access fields
    print(f"Lat: {msg.lat / 1e7} deg")       # Scaled by 1E7 (degE7)
    print(f"Lon: {msg.lon / 1e7} deg")       # Scaled by 1E7 (degE7)
    print(f"Alt: {msg.alt / 1e3} m")         # Millimeters to meters (MSL)
    print(f"Fix Type: {msg.fix_type}")       # 0-1: no fix, 2: 2D, 3: 3D
    print(f"Satellites: {msg.satellites_visible}")
    
    print("=" * 50)