import math
import threading
import time

from pymavlink import mavutil


# try these in order until one connects
#connection_string = "tcp:172.20.10.6:5760"
#connection_string = "udpin:0.0.0.0:14550"
connection_string = "udpout:dexi-114b.local:14550"

source_system = 245
connect_timeout = 30

takeoff_alt = 3
target_alt = 4
target_north_m = 10
target_east_m = 0

arrive_radius = 0.3
alt_tolerance = 0.2
loop_interval = 0.2
step_timeout = 60

setpoint_rate = 20
setpoint_warmup = 1.5

px4_main_mode_offboard = 6

# ignore velocity, accel and yaw rate, use position + yaw only
position_type_mask = 2552

nan = float("nan")

send_lock = threading.Lock()


class SetpointStreamer:
	def __init__(self, master):
		self.master = master
		self.lock = threading.Lock()
		self.running = False
		self.thread = None
		
		self.x = 0
		self.y = 0
		self.z = 0
		self.yaw = 0
	
	def set_target(self, x, y, z, yaw = None):
		with self.lock:
			self.x = x
			self.y = y
			self.z = z
			
			if yaw is not None:
				self.yaw = yaw
	
	def start(self):
		self.running = True
		self.thread = threading.Thread(target = self.loop, daemon = True)
		self.thread.start()
	
	def stop(self):
		self.running = False
		
		if self.thread:
			self.thread.join()
			self.thread = None
	
	def loop(self):
		start_time = time.time()
		last_heartbeat = 0
		
		while self.running:
			with self.lock:
				x, y, z, yaw = self.x, self.y, self.z, self.yaw
			
			time_boot_ms = int((time.time() - start_time) * 1000)
			
			with send_lock:
				self.master.mav.set_position_target_local_ned_send(
					time_boot_ms,
					self.master.target_system,
					self.master.target_component,
					mavutil.mavlink.MAV_FRAME_LOCAL_NED,
					position_type_mask,
					x, y, z,
					0, 0, 0,
					0, 0, 0,
					yaw, 0
				)
				
				# gcs heartbeat so px4 doesn't trigger data link loss
				if time.time() - last_heartbeat > 1:
					self.master.mav.heartbeat_send(
						mavutil.mavlink.MAV_TYPE_GCS,
						mavutil.mavlink.MAV_AUTOPILOT_INVALID,
						0, 0, 0
					)
					last_heartbeat = time.time()
			
			time.sleep(1 / setpoint_rate)


def recv(master, msg_type, timeout = 1):
	# like recv_match, but prints px4 status text instead of throwing it away
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
	
	# 245 so we don't collide with qgroundcontrol (which uses 255)
	master = mavutil.mavlink_connection(connection_string, source_system = source_system)
	
	start_time = time.time()
	
	while True:
		# udpout needs us to send first so the vehicle knows where to reply
		master.mav.heartbeat_send(
			mavutil.mavlink.MAV_TYPE_GCS,
			mavutil.mavlink.MAV_AUTOPILOT_INVALID,
			0, 0, 0
		)
		
		msg = master.recv_match(type = "HEARTBEAT", blocking = True, timeout = 1)
		
		# skip heartbeats from gcs / companion software, only accept the autopilot
		if msg and msg.autopilot != mavutil.mavlink.MAV_AUTOPILOT_INVALID:
			master.target_system = msg.get_srcSystem()
			master.target_component = msg.get_srcComponent()
			break
		
		if time.time() - start_time > connect_timeout:
			raise RuntimeError("no heartbeat from autopilot, check port and tcp vs udp")
	
	print(f"heartbeat from system {master.target_system}, component {master.target_component}")
	
	return master


def get_local_position(master):
	msg = recv(master, "LOCAL_POSITION_NED", timeout = 2)
	
	if msg is None:
		return None
	
	return msg.x, msg.y, msg.z


def get_yaw(master):
	msg = recv(master, "ATTITUDE", timeout = 2)
	
	if msg is None:
		return 0
	
	return msg.yaw


def wait_for_ack(master, command):
	start_time = time.time()
	
	while time.time() - start_time < 5:
		msg = recv(master, "COMMAND_ACK", timeout = 1)
		
		if msg and msg.command == command:
			return msg.result
	
	return None


def send_command_long(master, command, p1 = 0, p2 = 0, p3 = 0, p4 = 0, p5 = 0, p6 = 0, p7 = 0):
	with send_lock:
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
		raise RuntimeError(f"command {command} rejected, result: {result}")


def wait_for_local_position(master):
	print("waiting for local position...")
	start_time = time.time()
	
	while True:
		pos = get_local_position(master)
		
		if pos and not any(math.isnan(v) for v in pos):
			return pos
		
		if time.time() - start_time > 60:
			raise RuntimeError("no local position, check optical flow / rangefinder / gps")


def set_offboard_mode(master):
	print("switching to offboard...")
	
	send_command_long(
		master,
		mavutil.mavlink.MAV_CMD_DO_SET_MODE,
		p1 = mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
		p2 = px4_main_mode_offboard
	)
	
	print("offboard enabled")


def arm(master):
	print("arming...")
	send_command_long(master, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, p1 = 1)
	print("armed")


def fly_to(master, streamer, x, y, z, label):
	print(f"{label}: x {x:.1f}, y {y:.1f}, z {z:.1f}...")
	
	streamer.set_target(x, y, z)
	
	start_time = time.time()
	
	while True:
		pos = get_local_position(master)
		
		if pos is None:
			continue
		
		horizontal_dist = math.hypot(pos[0] - x, pos[1] - y)
		vertical_dist = abs(pos[2] - z)
		
		print(f"  horizontal: {horizontal_dist:.2f} m, vertical: {vertical_dist:.2f} m")
		
		if horizontal_dist <= arrive_radius and vertical_dist <= alt_tolerance:
			print(f"{label} reached")
			break
		
		if time.time() - start_time > step_timeout:
			raise RuntimeError(f"{label} timed out")
		
		time.sleep(loop_interval)


def land(master, streamer):
	print("landing...")
	
	send_command_long(
		master,
		mavutil.mavlink.MAV_CMD_NAV_LAND,
		p4 = nan,
		p5 = nan,
		p6 = nan
	)
	
	# px4 is in auto land now, setpoints are no longer needed
	streamer.stop()
	
	while True:
		msg = recv(master, "HEARTBEAT", timeout = 5)
		
		if msg and msg.get_srcSystem() == master.target_system and msg.get_srcComponent() == master.target_component:
			if not msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED:
				print("landed and disarmed")
				break
		
		pos = get_local_position(master)
		
		if pos:
			print(f"  altitude: {-pos[2]:.1f} m")


def main():
	master = connect(connection_string)
	
	start_x, start_y, start_z = wait_for_local_position(master)
	start_yaw = get_yaw(master)
	
	takeoff_z = start_z - takeoff_alt
	
	goal_x = start_x + target_north_m
	goal_y = start_y + target_east_m
	goal_z = start_z - target_alt
	
	print(f"start: x {start_x:.2f}, y {start_y:.2f}, z {start_z:.2f}, yaw {math.degrees(start_yaw):.0f} deg")
	
	# offboard needs setpoints already flowing before the mode switch
	streamer = SetpointStreamer(master)
	streamer.set_target(start_x, start_y, start_z, start_yaw)
	streamer.start()
	
	time.sleep(setpoint_warmup)
	
	armed = False
	
	try:
		set_offboard_mode(master)
		arm(master)
		armed = True
		
		fly_to(master, streamer, start_x, start_y, takeoff_z, "takeoff")
		fly_to(master, streamer, goal_x, goal_y, goal_z, "goal")
		land(master, streamer)
	except (RuntimeError, KeyboardInterrupt) as e:
		print(f"error: {e}")
		
		# if we got airborne, try to bring it down safely
		if armed:
			print("attempting emergency land...")
			land(master, streamer)
	finally:
		streamer.stop()
	
	print("done")


if __name__ == "__main__":
	main()