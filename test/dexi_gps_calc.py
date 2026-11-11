import argparse
import math
import time

from pymavlink import mavutil


earth_radius = 6371000.0

default_connection_string = "udpout:dexi-114b.local:14550"
source_system = 245
connect_timeout = 30
position_timeout = 10


def ned_to_gps(ref_lat, ref_lon, north, east):
	ref_lat_rad = math.radians(ref_lat)
	ref_lon_rad = math.radians(ref_lon)
	ref_sin_lat = math.sin(ref_lat_rad)
	ref_cos_lat = math.cos(ref_lat_rad)

	x_rad = north / earth_radius
	y_rad = east / earth_radius
	c = math.hypot(x_rad, y_rad)

	if c < 1e-12:
		return ref_lat, ref_lon

	sin_c = math.sin(c)
	cos_c = math.cos(c)

	lat_rad = math.asin(cos_c * ref_sin_lat + x_rad * sin_c * ref_cos_lat / c)
	lon_rad = ref_lon_rad + math.atan2(y_rad * sin_c, c * ref_cos_lat * cos_c - x_rad * ref_sin_lat * sin_c)

	lat = math.degrees(lat_rad)
	lon = (math.degrees(lon_rad) + 180) % 360 - 180

	return lat, lon


def gps_to_ned(ref_lat, ref_lon, lat, lon):
	lat_rad = math.radians(lat)
	lon_rad = math.radians(lon)
	ref_lat_rad = math.radians(ref_lat)
	ref_lon_rad = math.radians(ref_lon)

	sin_lat = math.sin(lat_rad)
	cos_lat = math.cos(lat_rad)
	ref_sin_lat = math.sin(ref_lat_rad)
	ref_cos_lat = math.cos(ref_lat_rad)
	cos_d_lon = math.cos(lon_rad - ref_lon_rad)

	arg = max(-1.0, min(1.0, ref_sin_lat * sin_lat + ref_cos_lat * cos_lat * cos_d_lon))
	c = math.acos(arg)
	k = 1.0 if abs(c) < 1e-9 else c / math.sin(c)

	north = k * (ref_cos_lat * sin_lat - ref_sin_lat * cos_lat * cos_d_lon) * earth_radius
	east = k * cos_lat * math.sin(lon_rad - ref_lon_rad) * earth_radius

	return north, east


def recv(master, msg_type, timeout = 1):
	end_time = time.time() + timeout

	while True:
		remaining = end_time - time.time()

		if remaining <= 0:
			return None

		msg = master.recv_match(type = [msg_type, "STATUSTEXT"], blocking = True, timeout = remaining)

		if msg is None:
			return None

		if msg.get_type() == "STATUSTEXT":
			print(f"  [px4] {msg.text}")
			continue

		return msg


def connect(connection_string):
	print(f"connecting to {connection_string}...")

	master = mavutil.mavlink_connection(connection_string, source_system = source_system)

	start_time = time.time()

	while True:
		master.mav.heartbeat_send(
			mavutil.mavlink.MAV_TYPE_GCS,
			mavutil.mavlink.MAV_AUTOPILOT_INVALID,
			0, 0, 0
		)

		msg = master.recv_match(type = "HEARTBEAT", blocking = True, timeout = 1)

		if msg and msg.autopilot != mavutil.mavlink.MAV_AUTOPILOT_INVALID:
			master.target_system = msg.get_srcSystem()
			master.target_component = msg.get_srcComponent()
			break

		if time.time() - start_time > connect_timeout:
			raise RuntimeError("no heartbeat from autopilot, check port and tcp vs udp")

	print(f"heartbeat from system {master.target_system}, component {master.target_component}")

	return master


def get_global_position(master):
	print("waiting for global position...")

	start_time = time.time()

	while time.time() - start_time < position_timeout:
		msg = recv(master, "GLOBAL_POSITION_INT", timeout = 2)

		if msg and (msg.lat != 0 or msg.lon != 0):
			return msg.lat / 1e7, msg.lon / 1e7, msg.alt / 1000, msg.relative_alt / 1000

	raise RuntimeError("no global position, check gps fix")


def prompt_float(prompt, default = 0.0):
	while True:
		text = input(f"{prompt} [{default}]: ").strip()

		if not text:
			return default

		try:
			return float(text)
		except ValueError:
			print("  enter a number")


def main():
	parser = argparse.ArgumentParser(description = "offset gps coordinates by meters north and east")
	parser.add_argument("--north", type = float, help = "meters north, negative for south")
	parser.add_argument("--east", type = float, help = "meters east, negative for west")
	parser.add_argument("--lat", type = float, help = "start latitude, skips connecting to the drone")
	parser.add_argument("--lon", type = float, help = "start longitude, skips connecting to the drone")
	parser.add_argument("--connection", default = default_connection_string, help = "mavlink connection string")
	args = parser.parse_args()

	if (args.lat is None) != (args.lon is None):
		parser.error("give both --lat and --lon, or neither")

	if args.lat is None:
		master = connect(args.connection)
		start_lat, start_lon, start_alt, start_rel_alt = get_global_position(master)
		master.close()

		print(f"drone at {start_lat:.7f}, {start_lon:.7f}, {start_alt:.1f} m amsl, {start_rel_alt:.1f} m above home")
	else:
		start_lat = args.lat
		start_lon = args.lon

	north = args.north if args.north is not None else prompt_float("meters north")
	east = args.east if args.east is not None else prompt_float("meters east")

	offset_lat, offset_lon = ned_to_gps(start_lat, start_lon, north, east)
	check_north, check_east = gps_to_ned(start_lat, start_lon, offset_lat, offset_lon)

	distance = math.hypot(north, east)
	bearing = math.degrees(math.atan2(east, north)) % 360

	print()
	print(f"start:  {start_lat:.7f}, {start_lon:.7f}")
	print(f"offset: {north:.2f} m north, {east:.2f} m east ({distance:.2f} m at {bearing:.0f} deg)")
	print(f"target: {offset_lat:.7f}, {offset_lon:.7f}")
	print(f"check:  {check_north:.3f} m north, {check_east:.3f} m east")
	print()
	print(f"launch args: target_lat:={offset_lat:.7f} target_lon:={offset_lon:.7f}")


if __name__ == "__main__":
	main()

# python dexi_gps_calc.py --north 10 --east 5
# python dexi_gps_calc.py --lat 47.397742 --lon 8.545594 --north 10 --east 5