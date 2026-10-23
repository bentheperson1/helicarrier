import math
import threading
import time
from queue import Queue, Empty

from PySide6.QtCore import QObject, QThread, Signal, Slot
import os
# MAVLink 2 message definitions: needed for servo outputs 9-16 (AUX pins) and other extension fields
os.environ.setdefault("MAVLINK20", "1")
from pymavlink import mavutil

class MavlinkWorker(QObject):
	state_changed = Signal(str)
	telemetry_updated = Signal(dict)
	error_occurred = Signal(str)
	log_message = Signal(str)
	finished = Signal()
	
	def __init__(self, connection_string, baud = 57600):
		super().__init__()
		
		self.connection_string = connection_string
		self.baud = baud
		
		self.master = None
		self.stop_event = threading.Event()
		self.command_queue = Queue()
		
		self.heartbeat_interval = 1.0
		self.link_timeout = 5.0
		self.reconnect_delay = 2.0
		self.emit_interval = 0.1
		
		self.last_heartbeat_rx = 0
		self.last_heartbeat_tx = 0
		self.last_emit = 0
		
		self.telemetry = {}
	
	@Slot()
	def run(self):
		while not self.stop_event.is_set():
			self.state_changed.emit("connecting")
			
			if self.open_connection():
				self.state_changed.emit("connected")
				self.pump_messages()
				self.close_connection()
				
				if not self.stop_event.is_set():
					self.state_changed.emit("link_lost")
			
			self.stop_event.wait(self.reconnect_delay)
		
		self.state_changed.emit("disconnected")
		self.finished.emit()
	
	def open_connection(self):
		try:
			self.master = mavutil.mavlink_connection(self.connection_string, baud = self.baud)
			
			# poll in short slices so a stop request is honored quickly
			deadline = time.monotonic() + 10
			while time.monotonic() < deadline and not self.stop_event.is_set():
				hb = self.master.recv_match(type = "HEARTBEAT", blocking = True, timeout = 0.5)
				# through mavlink-router we also see heartbeats from other ground
				# stations (QGC, Mission Planner), so skip anything that isn't an autopilot
				if hb is not None and hb.type != mavutil.mavlink.MAV_TYPE_GCS \
						and hb.autopilot != mavutil.mavlink.MAV_AUTOPILOT_INVALID:
					self.master.target_system = hb.get_srcSystem()
					self.master.target_component = hb.get_srcComponent()
					self.last_heartbeat_rx = time.monotonic()
					self.request_streams()
					return True
			
			self.error_occurred.emit("No heartbeat received")
		except Exception as e:
			self.error_occurred.emit(f"Connection failed: {e}")
		
		self.close_connection()
		return False
	
	def pump_messages(self):
		while not self.stop_event.is_set():
			now = time.monotonic()
			
			if now - self.last_heartbeat_tx >= self.heartbeat_interval:
				self.send_gcs_heartbeat()
				self.last_heartbeat_tx = now
			
			self.process_commands()
			
			try:
				msg = self.master.recv_match(blocking = True, timeout = 0.05)
			except Exception as e:
				self.error_occurred.emit(f"Read error: {e}")
				return
			
			if msg is not None:
				self.handle_message(msg)
			
			if now - self.last_heartbeat_rx > self.link_timeout:
				self.error_occurred.emit("Heartbeat timeout")
				return
			
			if now - self.last_emit >= self.emit_interval:
				self.telemetry_updated.emit(dict(self.telemetry))
				self.last_emit = now
	
	def handle_message(self, msg):
		msg_type = msg.get_type()
		
		if msg_type == "HEARTBEAT":
			# ignore heartbeats from other GCSes / components on the link
			if msg.get_srcSystem() != self.master.target_system or \
					msg.get_srcComponent() != self.master.target_component:
				return
			self.last_heartbeat_rx = time.monotonic()
			self.telemetry["hb_time"] = self.last_heartbeat_rx
			self.telemetry["autopilot"] = msg.autopilot
			self.telemetry["vehicle_type"] = msg.type
			self.telemetry["mode"] = mavutil.mode_string_v10(msg)
			self.telemetry["armed"] = bool(msg.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
		elif msg_type == "GLOBAL_POSITION_INT":
			self.telemetry["lat"] = msg.lat / 1e7
			self.telemetry["lon"] = msg.lon / 1e7
			self.telemetry["alt"] = msg.relative_alt / 1000
		elif msg_type == "ATTITUDE":
			self.telemetry["roll"] = msg.roll
			self.telemetry["pitch"] = msg.pitch
			self.telemetry["yaw"] = msg.yaw
			self.telemetry["heading"] = math.degrees(msg.yaw) % 360
		elif msg_type == "GPS_RAW_INT":
			self.telemetry["gps_fix"] = msg.fix_type
			self.telemetry["gps_sats"] = msg.satellites_visible
			self.telemetry["gps_hdop"] = msg.eph / 100 if msg.eph != 65535 else None
		elif msg_type == "SERVO_OUTPUT_RAW" and msg.port == 0:
			self.telemetry["servo_outputs"] = {
				i: getattr(msg, f"servo{i}_raw", 0) for i in range(1, 17)
			}
		elif msg_type == "SYS_STATUS":
			self.telemetry["battery_v"] = msg.voltage_battery / 1000
			self.telemetry["battery_pct"] = msg.battery_remaining  # -1 = unknown
			self.telemetry["sensors_present"] = msg.onboard_control_sensors_present
			self.telemetry["sensors_enabled"] = msg.onboard_control_sensors_enabled
			self.telemetry["sensors_health"] = msg.onboard_control_sensors_health
		elif msg_type == "EKF_STATUS_REPORT":
			self.telemetry["ekf_flags"] = msg.flags
			self.telemetry["ekf_vel_var"] = msg.velocity_variance
			self.telemetry["ekf_pos_var"] = msg.pos_horiz_variance
			self.telemetry["ekf_compass_var"] = msg.compass_variance
		elif msg_type == "RC_CHANNELS":
			self.telemetry["rc_count"] = msg.chancount
			self.telemetry["rc_rssi"] = msg.rssi
			self.telemetry["rc_throttle"] = msg.chan3_raw
		elif msg_type == "PARAM_VALUE":
			# copy-on-write so the GUI thread never sees a dict that's being changed
			params = dict(self.telemetry.get("params", {}))
			params[msg.param_id.rstrip("\x00")] = msg.param_value
			self.telemetry["params"] = params
		elif msg_type == "COMMAND_ACK":
			result_enum = mavutil.mavlink.enums["MAV_RESULT"].get(msg.result)
			result = result_enum.name if result_enum else str(msg.result)
			command_enum = mavutil.mavlink.enums["MAV_CMD"].get(msg.command)
			command = command_enum.name if command_enum else str(msg.command)
			
			self.log_message.emit(f"{command} ack: {result}")
		elif msg_type == "STATUSTEXT":
			self.log_message.emit(f"FC: {msg.text}")
			if msg.text.startswith(("PreArm", "Arm")):
				recent = list(self.telemetry.get("prearm_msgs", []))[-9:]
				recent.append((time.monotonic(), msg.text))
				self.telemetry["prearm_msgs"] = recent
	
	def request_streams(self):
		# ask ArduPilot to stream all telemetry at 4 Hz on this link; otherwise what we
		# get depends on the SRx_ parameters for whichever FC port the Pi is plugged into
		self.master.mav.request_data_stream_send(
			self.master.target_system, self.master.target_component,
			mavutil.mavlink.MAV_DATA_STREAM_ALL, 4, 1
		)
	
	def send_gcs_heartbeat(self):
		self.master.mav.heartbeat_send(
			mavutil.mavlink.MAV_TYPE_GCS,
			mavutil.mavlink.MAV_AUTOPILOT_INVALID,
			0, 0, 0
		)
	
	def process_commands(self):
		try:
			while True:
				command = self.command_queue.get_nowait()
				command(self.master)
		except Empty:
			pass
	
	def close_connection(self):
		if self.master is not None:
			try:
				self.master.close()
			except Exception:
				pass
			self.master = None
	
	# thread-safe, called from the GUI thread
	def send_command(self, command_callable):
		self.command_queue.put(command_callable)
	
	def request_stop(self):
		self.stop_event.set()


class LinkController(QObject):
	state_changed = Signal(str)
	telemetry_updated = Signal(dict)
	error_occurred = Signal(str)
	log_message = Signal(str)
	
	def __init__(self):
		super().__init__()
		
		self.mavlink_worker_thread = None
		self.worker = None
	
	def connect_link(self, connection_string, baud):
		if self.mavlink_worker_thread is not None:
			return
		
		self.mavlink_worker_thread = QThread()
		self.worker = MavlinkWorker(connection_string, baud)
		self.worker.moveToThread(self.mavlink_worker_thread)
  
		self.worker.error_occurred.connect(self.error_occurred)
		self.worker.log_message.connect(self.log_message)
		
		self.mavlink_worker_thread.started.connect(self.worker.run)
		self.worker.finished.connect(self.mavlink_worker_thread.quit)
		self.worker.finished.connect(self.worker.deleteLater)
		self.mavlink_worker_thread.finished.connect(self.on_thread_finished)
		
		self.worker.state_changed.connect(self.on_worker_state)
		self.worker.telemetry_updated.connect(self.telemetry_updated)
		
		self.mavlink_worker_thread.start()
	
	# thread-safe: queue a callable(master) to run on the MAVLink thread
	def send_command(self, command_callable):
		if self.worker is None:
			self.error_occurred.emit("Not connected")
			return False
		self.worker.send_command(command_callable)
		return True
	
	def disconnect_link(self):
		if self.worker is not None:
			self.worker.request_stop()
	
	def shutdown(self):
		if self.mavlink_worker_thread is None:
			return
		
		self.worker.request_stop()
		
		# quit() must be called directly here, the queued finished -> quit
		# connection can't run while the GUI thread is blocked in wait()
		self.mavlink_worker_thread.quit()
		self.mavlink_worker_thread.wait(3000)
	
	@Slot(str)
	def on_worker_state(self, state):
		# "disconnected" is emitted by on_thread_finished instead
		if state != "disconnected":
			self.state_changed.emit(state)
	
	@Slot()
	def on_thread_finished(self):
		self.mavlink_worker_thread.deleteLater()
		self.mavlink_worker_thread = None
		self.worker = None
		
		self.state_changed.emit("disconnected")