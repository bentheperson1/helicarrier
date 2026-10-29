import math
import time

from pymavlink import mavutil


connection_string = "udpin:dexi-114b.local:14540"

takeoff_alt = 3
target_alt = 3

target_lat = None
target_lon = None
target_north_m = 10
target_east_m = 0

arrive_radius = 1.0
alt_tolerance = 0.9
loop_interval = 0.5
step_timeout = 60

nan = float("nan")


def get_distance_m(lat_1, lon_1, lat_2, lon_2):
	earth_radius = 6371000
	
	d_lat = math.radians(lat_2 - lat_1)
	d_lon = math.radians(lon_2 - lon_1)
	
	a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat_1)) * math.cos(math.radians(lat_2)) * math.sin(d_lon / 2) ** 2
	
	return earth_radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def offset_coords(lat, lon, north_m, east_m):
	new_lat = lat + north_m / 111320
	new_lon = lon + east_m / (111320 * math.cos(math.radians(lat)))
	
	return new_lat, new_lon


def get_position(master):
	msg = master.recv_match(type = "GLOBAL_POSITION_INT", blocking = True, timeout = 5)
	
	if msg is None:
		return None
	
	lat = msg.lat / 1e7
	lon = msg.lon / 1e7
	amsl_alt = msg.alt / 1000
	rel_alt = msg.relative_alt / 1000
	
	return lat, lon, amsl_alt, rel_alt


def print_status_text(master):
	while True:
		msg = master.recv_match(type = "STATUSTEXT", blocking = False)
		
		if msg is None:
			break
		
		print(f"  [px4] {msg.text}")


def wait_for_ack(master, command):
	start_time = time.time()
	
	while time.time() - start_time < 5:
		msg = master.recv_match(type = "COMMAND_ACK", blocking = True, timeout = 1)
		
		if msg and msg.command == command:
			return msg.result
	
	return None


def send_command_long(master, command, p1 = 0, p2 = 0, p3 = 0, p4 = 0, p5 = 0, p6 = 0, p7 = 0):
	master.mav.command_long_send(
		master.target_system,
		master.target_component,
		command,
		0,
		p1, p2, p3, p4,
		p5, p6, p7
	)
	
	result = wait_for_ack(master, command)
	
	if result != mavutil.mavlink.MAV_RESULT_ACCEPTED:
		print_status_text(master)
		raise RuntimeError(f"command {command} rejected, result: {result}")


def wait_for_gps(master):
	print("waiting for 3d gps fix...")
	start_time = time.time()
	
	while True:
		msg = master.recv_match(type = "GPS_RAW_INT", blocking = True, timeout = 5)
		
		if msg and msg.fix_type >= 3:
			print(f"gps fix acquired, satellites: {msg.satellites_visible}")
			break
		
		if time.time() - start_time > 120:
			raise RuntimeError("no gps fix after 2 minutes")
	
	print("waiting for valid global position...")
	
	while True:
		pos = get_position(master)
		
		if pos and pos[0] != 0 and pos[1] != 0:
			return pos


def arm(master):
	print("arming...")
	send_command_long(master, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, p1 = 1)
	print("armed")


def takeoff(master, home_amsl, alt):
	print(f"taking off to {alt} m...")
	
	# px4 wants takeoff altitude as amsl, not relative
	send_command_long(
		master,
		mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
		p1 = -1,
		p4 = nan,
		p5 = nan,
		p6 = nan,
		p7 = home_amsl + alt
	)
	
	start_time = time.time()
	
	while True:
		pos = get_position(master)
		
		if pos is None:
			continue
		
		print(f"  altitude: {pos[3]:.1f} m")
		
		if pos[3] >= alt * alt_tolerance:
			print("takeoff altitude reached")
			break
		
		if time.time() - start_time > step_timeout:
			raise RuntimeError("takeoff timed out")
		
		time.sleep(loop_interval)


def goto(master, home_amsl, lat, lon, alt):
	print(f"flying to {lat:.7f}, {lon:.7f} at {alt} m...")
	
	command = mavutil.mavlink.MAV_CMD_DO_REPOSITION
	change_mode_flag = 1
	
	master.mav.command_int_send(
		master.target_system,
		master.target_component,
		mavutil.mavlink.MAV_FRAME_GLOBAL,
		command,
		0,
		0,
		-1, change_mode_flag, 0, nan,
		int(lat * 1e7),
		int(lon * 1e7),
		home_amsl + alt
	)
	
	result = wait_for_ack(master, command)
	
	if result != mavutil.mavlink.MAV_RESULT_ACCEPTED:
		print_status_text(master)
		raise RuntimeError(f"reposition rejected, result: {result}")
	
	start_time = time.time()
	
	while True:
		pos = get_position(master)
		
		if pos is None:
			continue
		
		dist = get_distance_m(pos[0], pos[1], lat, lon)
		print(f"  distance to target: {dist:.1f} m")
		
		if dist <= arrive_radius:
			print("target reached")
			break
		
		if time.time() - start_time > step_timeout:
			raise RuntimeError("goto timed out")
		
		time.sleep(loop_interval)


def land(master):
	print("landing...")
	
	send_command_long(
		master,
		mavutil.mavlink.MAV_CMD_NAV_LAND,
		p4 = nan,
		p5 = nan,
		p6 = nan
	)
	
	while True:
		msg = master.recv_match(type = "HEARTBEAT", blocking = True, timeout = 5)
		
		if msg and msg.get_srcSystem() == master.target_system and not msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED:
			print("landed and disarmed")
			break
		
		pos = get_position(master)
		
		if pos:
			print(f"  altitude: {pos[3]:.1f} m")


def main():
	print(f"connecting to {connection_string}...")
	master = mavutil.mavlink_connection(connection_string)
	master.wait_heartbeat()
	print(f"heartbeat from system {master.target_system}, component {master.target_component}")
	
	home_pos = wait_for_gps(master)
	home_lat = home_pos[0]
	home_lon = home_pos[1]
	home_amsl = home_pos[2] - home_pos[3]
	
	if target_lat is None or target_lon is None:
		goal_lat, goal_lon = offset_coords(home_lat, home_lon, target_north_m, target_east_m)
	else:
		goal_lat = target_lat
		goal_lon = target_lon
	
	print(f"home: {home_lat:.7f}, {home_lon:.7f}, amsl {home_amsl:.1f} m")
	
	try:
		arm(master)
		takeoff(master, home_amsl, takeoff_alt)
		goto(master, home_amsl, goal_lat, goal_lon, target_alt)
		land(master)
	except (KeyboardInterrupt, RuntimeError) as err:
		print(f"aborting: {err}")
		land(master)
	
	print("done")


if __name__ == "__main__":
	main()