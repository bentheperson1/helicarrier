"""Pre-flight check page.

Every check has its own Run button and is judged on the telemetry at the moment
you press it. Results are PASS / WARN / FAIL with the reason next to them, and
the time it was checked, so you can see how fresh each result is.

To add a check: write a function  check_xxx(t, settings) -> (status, detail)
and add it to AUTO_CHECKS. `t` is the telemetry dict from mavlink_manager.
Thresholds are at the top of the file so they're easy to tune.
"""
import math
import time

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
	QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
	QScrollArea, QSpinBox, QVBoxLayout, QWidget
)
from pymavlink import mavutil

PASS, WARN, FAIL, RUNNING, NOT_RUN = "PASS", "WARN", "FAIL", "RUNNING", "NOT RUN"

# ---- thresholds -------------------------------------------------------------
HEARTBEAT_MAX_AGE = 3.0      # s
CELL_V_PASS = 3.80           # V per cell, resting (no load)
CELL_V_FAIL = 3.60
GPS_MIN_SATS_FAIL = 6
GPS_MIN_SATS_WARN = 8
GPS_HDOP_WARN = 1.4          # ArduPilot's own "good" limit (GPS_HDOP_GOOD = 140)
GPS_HDOP_FAIL = 2.5
EKF_VAR_WARN = 0.5           # same colors Mission Planner uses
EKF_VAR_FAIL = 0.8
LEVEL_WARN_DEG = 5
LEVEL_FAIL_DEG = 10
RC_THROTTLE_LOW_MAX = 1100   # µs, throttle stick must be down to arm
RC_THROTTLE_FAILSAFE = 900   # µs, below this the receiver is in failsafe / off
SAFE_TAKEOFF_MODES = {"STABILIZE", "ALT_HOLD", "LOITER", "POSHOLD"}
PARAM_TIMEOUT = 4.0          # s to wait for parameters to come back

FAILSAFE_PARAMS = [
	"FS_THR_ENABLE", "FS_THR_VALUE",
	"BATT_FS_LOW_ACT", "BATT_LOW_VOLT", "BATT_FS_CRT_ACT", "BATT_CRT_VOLT",
	"FS_GCS_ENABLE", "FENCE_ENABLE", "RTL_ALT",
]

FIX_NAMES = {0: "No GPS", 1: "No fix", 2: "2D fix", 3: "3D fix", 4: "DGPS", 5: "RTK float", 6: "RTK fixed"}


def link_alive(t):
	hb = t.get("hb_time")
	return hb is not None and time.monotonic() - hb < HEARTBEAT_MAX_AGE


# ---- automated checks -------------------------------------------------------
def check_link(t, settings):
	hb = t.get("hb_time")
	if hb is None:
		return FAIL, "No heartbeat from the flight controller. Connect first."
	age = time.monotonic() - hb
	if age >= HEARTBEAT_MAX_AGE:
		return FAIL, f"Last heartbeat {age:.0f} s ago: link is down"
	autopilot = "ArduPilot" if t.get("autopilot") == mavutil.mavlink.MAV_AUTOPILOT_ARDUPILOTMEGA else f"autopilot {t.get('autopilot')}"
	return PASS, f"Heartbeat {age:.1f} s ago ({autopilot})"


def check_disarmed(t, settings):
	if t.get("armed") is None:
		return FAIL, "Armed state unknown"
	if t["armed"]:
		return FAIL, "Drone is ARMED. Disarm before doing pre-flight checks."
	return PASS, "Disarmed"


def check_battery(t, settings):
	v = t.get("battery_v")
	cells = settings.get("cells", 6)
	if not v:
		return FAIL, "No battery voltage reported (battery plugged in? battery monitor set up?)"
	per_cell = v / cells
	detail = f"{v:.2f} V = {per_cell:.2f} V/cell ({cells}S)"
	if t.get("battery_pct", -1) >= 0:
		detail += f", FC estimate {t['battery_pct']}%"
	if per_cell > 4.35:
		return WARN, detail + ". Higher than a full LiPo: is the cell count right?"
	if per_cell < CELL_V_FAIL:
		return FAIL, detail + f". Below {CELL_V_FAIL} V/cell, charge or swap"
	if per_cell < CELL_V_PASS:
		return WARN, detail + f". Below {CELL_V_PASS} V/cell, short flight only"
	return PASS, detail


def check_gps(t, settings):
	fix = t.get("gps_fix")
	sats = t.get("gps_sats")
	hdop = t.get("gps_hdop")
	if fix is None:
		return FAIL, "No GPS data received"
	detail = f"{FIX_NAMES.get(fix, f'fix {fix}')}, {sats} sats" + (f", HDOP {hdop:.2f}" if hdop is not None else "")
	if fix < 3:
		return FAIL, detail + ". Needs a 3D fix (go outside / wait)"
	if sats < GPS_MIN_SATS_FAIL or (hdop is not None and hdop > GPS_HDOP_FAIL):
		return FAIL, detail
	if sats < GPS_MIN_SATS_WARN or (hdop is not None and hdop > GPS_HDOP_WARN):
		return WARN, detail + ". Usable but weak, give it a minute"
	return PASS, detail


def check_ekf(t, settings):
	flags = t.get("ekf_flags")
	if flags is None:
		return WARN, "No EKF_STATUS_REPORT received yet"
	m = mavutil.mavlink
	needed = {
		"attitude": m.EKF_ATTITUDE,
		"horizontal velocity": m.EKF_VELOCITY_HORIZ,
		"absolute position": m.EKF_POS_HORIZ_ABS,
	}
	missing = [name for name, bit in needed.items() if not flags & bit]
	worst = max(t.get("ekf_vel_var", 0), t.get("ekf_pos_var", 0), t.get("ekf_compass_var", 0))
	detail = (f"velocity var {t.get('ekf_vel_var', 0):.2f}, position var {t.get('ekf_pos_var', 0):.2f}, "
		f"compass var {t.get('ekf_compass_var', 0):.2f}")
	if flags & m.EKF_CONST_POS_MODE:
		return FAIL, "EKF in constant-position mode (no GPS position yet). " + detail
	if missing:
		return FAIL, "EKF missing: " + ", ".join(missing) + ". " + detail
	if worst > EKF_VAR_FAIL:
		return FAIL, detail
	if worst > EKF_VAR_WARN:
		return WARN, detail
	return PASS, detail


def sensor_names(mask):
	names = []
	for bit, entry in mavutil.mavlink.enums["MAV_SYS_STATUS_SENSOR"].items():
		if mask & bit and bit != mavutil.mavlink.MAV_SYS_STATUS_PREARM_CHECK:
			names.append(entry.name.replace("MAV_SYS_STATUS_SENSOR_", "").replace("MAV_SYS_STATUS_", ""))
	return names


def check_sensors(t, settings):
	present = t.get("sensors_present")
	if present is None:
		return FAIL, "No SYS_STATUS received"
	enabled = t.get("sensors_enabled", 0)
	health = t.get("sensors_health", 0)
	bad = sensor_names(present & enabled & ~health)
	if bad:
		return FAIL, "Unhealthy: " + ", ".join(bad)
	return PASS, f"{len(sensor_names(present & enabled))} sensors enabled and healthy"


def check_prearm(t, settings):
	bit = mavutil.mavlink.MAV_SYS_STATUS_PREARM_CHECK
	recent = [text for when, text in t.get("prearm_msgs", []) if time.monotonic() - when < 60]
	if t.get("sensors_present", 0) & bit:
		if t.get("sensors_health", 0) & bit:
			return PASS, "ArduPilot reports all pre-arm checks passing"
		reasons = "; ".join(dict.fromkeys(recent)) or "reason not received yet (ArduPilot repeats it every 30 s)"
		return FAIL, reasons
	if recent:
		return FAIL, "; ".join(dict.fromkeys(recent))
	return WARN, "Flight controller doesn't report pre-arm status; no PreArm errors seen in the last minute"


def check_level(t, settings):
	if t.get("roll") is None:
		return FAIL, "No attitude data"
	roll = math.degrees(t["roll"])
	pitch = math.degrees(t["pitch"])
	worst = max(abs(roll), abs(pitch))
	detail = f"roll {roll:+.1f}°, pitch {pitch:+.1f}°"
	if worst > LEVEL_FAIL_DEG:
		return FAIL, detail + ". Is it on flat ground? If so, recalibrate the accelerometer"
	if worst > LEVEL_WARN_DEG:
		return WARN, detail + ". Slightly off level"
	return PASS, detail


def check_rc(t, settings):
	count = t.get("rc_count")
	if count is None:
		return FAIL, "No RC_CHANNELS received"
	if count == 0:
		return FAIL, "No RC receiver data: is the transmitter on and bound?"
	thr = t.get("rc_throttle", 0)
	rssi = t.get("rc_rssi", 255)
	detail = f"{count} channels, throttle {thr} µs" + (f", RSSI {rssi}" if rssi != 255 else "")
	if thr < RC_THROTTLE_FAILSAFE:
		return FAIL, detail + ". Throttle in failsafe range (receiver lost signal?)"
	if thr > RC_THROTTLE_LOW_MAX:
		return WARN, detail + ". Throttle stick is not at the bottom"
	return PASS, detail


def check_failsafes(t, settings):
	p = t.get("params", {})
	missing = [name for name in FAILSAFE_PARAMS if name not in p]
	if len(missing) == len(FAILSAFE_PARAMS):
		return FAIL, "Couldn't read failsafe parameters from the flight controller"

	thr_actions = {0: "Disabled", 1: "RTL", 3: "Land"}
	batt_actions = {0: "None", 1: "Land", 2: "RTL", 3: "SmartRTL or RTL", 4: "SmartRTL or Land", 5: "TERMINATE"}
	gcs_actions = {0: "Disabled", 1: "RTL", 5: "Land"}

	def show(name, table = None):
		if name not in p:
			return f"{name}=?"
		v = p[name]
		v_txt = f"{v:g}"
		return f"{name}={v_txt}" + (f" ({table[int(v)]})" if table and int(v) in table else "")

	lines = [
		"RC loss: " + show("FS_THR_ENABLE", thr_actions) + ", " + show("FS_THR_VALUE"),
		"Low batt: " + show("BATT_FS_LOW_ACT", batt_actions) + " at " + show("BATT_LOW_VOLT"),
		"Critical batt: " + show("BATT_FS_CRT_ACT", batt_actions) + " at " + show("BATT_CRT_VOLT"),
		"GCS loss: " + show("FS_GCS_ENABLE", gcs_actions) + " | " + show("FENCE_ENABLE") + " | " + show("RTL_ALT") + " cm",
	]
	detail = "\n".join(lines)

	if p.get("FS_THR_ENABLE", 1) == 0:
		return FAIL, "RC-loss failsafe is DISABLED.\n" + detail
	if 5 in (p.get("BATT_FS_LOW_ACT"), p.get("BATT_FS_CRT_ACT")):
		return WARN, "A battery failsafe is set to TERMINATE (motors stop in the air).\n" + detail
	if p.get("BATT_FS_LOW_ACT", 1) == 0 or p.get("BATT_LOW_VOLT", 1) == 0:
		return WARN, "No low-battery failsafe action.\n" + detail
	if missing:
		return WARN, "Some parameters didn't arrive: " + ", ".join(missing) + "\n" + detail
	return PASS, detail


def check_mode(t, settings):
	mode = t.get("mode")
	if mode is None:
		return FAIL, "Flight mode unknown"
	if mode in SAFE_TAKEOFF_MODES:
		return PASS, f"{mode}"
	if mode in ("AUTO", "GUIDED"):
		return WARN, f"{mode} is autonomous: the drone may fly on its own when armed"
	return WARN, f"{mode}: not a usual takeoff mode"


# key, name, what it checks, function, parameters it needs
AUTO_CHECKS = [
	("link", "Telemetry link", "Heartbeat from the flight controller is live", check_link, None),
	("disarmed", "Motors disarmed", "Must be disarmed while checking", check_disarmed, None),
	("battery", "Battery voltage", "Resting voltage per cell", check_battery, None),
	("gps", "GPS", "3D fix, satellite count, HDOP", check_gps, None),
	("ekf", "Position estimate (EKF)", "EKF has attitude/velocity/position, low variances", check_ekf, None),
	("sensors", "Sensor health", "Gyro, accel, compass, baro, GPS... all healthy", check_sensors, None),
	("prearm", "ArduPilot pre-arm", "The flight controller's own arming checks", check_prearm, None),
	("level", "Level / IMU sanity", "Roll and pitch near zero on flat ground", check_level, None),
	("rc", "RC transmitter", "Receiver sees the transmitter, throttle low", check_rc, None),
	("failsafes", "Failsafe settings", "RC loss, battery, GCS loss, fence configured", check_failsafes, FAILSAFE_PARAMS),
	("mode", "Flight mode", "In a manual / position-hold takeoff mode", check_mode, None),
]

MANUAL_CHECKS = [
	"Propellers tight, undamaged, correct direction",
	"Battery strapped down, connector secure",
	"Frame and arms locked, no loose wires",
	"Servo / payload moves freely and is secured",
	"Transmitter on, flight-mode switch in takeoff position",
	"Flying area clear, spotters briefed",
	"Safety pilot ready to take over",
]

STATUS_COLORS = {
	PASS: ("#ffffff", "#2e9e4f"),
	WARN: ("#000000", "#f2b705"),
	FAIL: ("#ffffff", "#d93025"),
	RUNNING: ("#ffffff", "#5f6368"),
	NOT_RUN: ("#5f6368", "#e0e0e0"),
}


class CheckRow:
	def __init__(self, grid, row, key, name, description):
		self.key = key
		self.status = NOT_RUN

		self.chip = QLabel()
		self.chip.setAlignment(Qt.AlignCenter)
		self.chip.setFixedWidth(80)

		title = QLabel(f"<b>{name}</b><br><span style='color:gray'>{description}</span>")
		title.setMinimumWidth(230)

		self.detail = QLabel("-")
		self.detail.setWordWrap(True)
		self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)

		self.when = QLabel("")
		self.when.setStyleSheet("color: gray")

		self.button = QPushButton("Run")

		grid.addWidget(self.chip, row, 0)
		grid.addWidget(title, row, 1)
		grid.addWidget(self.detail, row, 2)
		grid.addWidget(self.when, row, 3)
		grid.addWidget(self.button, row, 4)
		self.set(NOT_RUN, "-")

	def set(self, status, detail, stamp = False):
		self.status = status
		fg, bg = STATUS_COLORS[status]
		self.chip.setText(status)
		self.chip.setStyleSheet(f"color: {fg}; background: {bg}; border-radius: 4px; padding: 4px; font-weight: bold;")
		self.detail.setText(detail)
		self.when.setText(time.strftime("%H:%M:%S") if stamp else "")


class PreflightPage(QWidget):
	# emitted with a list of parameter names the page needs read from the FC
	params_requested = Signal(list)

	def __init__(self, cells_key = "battery_cells"):
		super().__init__()

		self.latest = {}
		self.store = QSettings("UTD AIAA", "Megadrone GCS")

		self.cells_input = QSpinBox()
		self.cells_input.setRange(1, 14)
		self.cells_input.setSuffix(" S")
		self.cells_input.setValue(int(self.store.value(cells_key, 6)))
		self.cells_input.valueChanged.connect(lambda v: self.store.setValue(cells_key, v))

		self.run_all_button = QPushButton("Run all automatic checks")
		self.reset_button = QPushButton("Reset")
		self.summary = QLabel()

		header = QHBoxLayout()
		header.addWidget(QLabel("Battery cells:"))
		header.addWidget(self.cells_input)
		header.addSpacing(20)
		header.addWidget(self.summary, 1)
		header.addWidget(self.reset_button)
		header.addWidget(self.run_all_button)

		auto_box = QGroupBox("Automatic checks (each one runs on its own)")
		grid = QGridLayout()
		grid.setColumnStretch(2, 1)
		grid.setVerticalSpacing(10)
		self.rows = {}
		self.check_funcs = {}
		for i, (key, name, desc, func, params) in enumerate(AUTO_CHECKS):
			row = CheckRow(grid, i, key, name, desc)
			row.button.clicked.connect(lambda _=False, k=key: self.run_check(k))
			self.rows[key] = row
			self.check_funcs[key] = (func, params)
		auto_box.setLayout(grid)

		manual_box = QGroupBox("Manual checks (tick each one yourself)")
		manual_layout = QVBoxLayout()
		self.manual_checks = []
		for text in MANUAL_CHECKS:
			box = QCheckBox(text)
			box.toggled.connect(self.update_summary)
			manual_layout.addWidget(box)
			self.manual_checks.append(box)
		manual_box.setLayout(manual_layout)

		content = QWidget()
		content_layout = QVBoxLayout(content)
		content_layout.addWidget(auto_box)
		content_layout.addWidget(manual_box)
		content_layout.addStretch(1)

		scroll = QScrollArea()
		scroll.setWidgetResizable(True)
		scroll.setWidget(content)

		layout = QVBoxLayout(self)
		layout.addLayout(header)
		layout.addWidget(scroll, 1)

		self.run_all_button.clicked.connect(self.run_all)
		self.reset_button.clicked.connect(self.reset)
		self.update_summary()

	def settings(self):
		return {"cells": self.cells_input.value()}

	def update_telemetry(self, t):
		self.latest = t

	def on_link_state(self, state):
		# never judge checks on telemetry from a link that's gone
		if state != "connected":
			self.latest = {}

	def run_check(self, key):
		func, params = self.check_funcs[key]
		row = self.rows[key]

		if key != "link" and not link_alive(self.latest):
			row.set(FAIL, "No live telemetry: connect to the drone first", stamp = True)
			self.update_summary()
			return

		if params:
			row.set(RUNNING, "Reading parameters from the flight controller...")
			self.params_requested.emit(params)
			self.wait_for_params(key, params, time.monotonic() + PARAM_TIMEOUT)
			return

		status, detail = func(self.latest, self.settings())
		row.set(status, detail, stamp = True)
		self.update_summary()

	def wait_for_params(self, key, params, deadline):
		have = self.latest.get("params", {})
		if all(p in have for p in params) or time.monotonic() > deadline:
			func, _ = self.check_funcs[key]
			status, detail = func(self.latest, self.settings())
			self.rows[key].set(status, detail, stamp = True)
			self.update_summary()
			return
		QTimer.singleShot(200, lambda: self.wait_for_params(key, params, deadline))

	def run_all(self):
		for key, *_ in AUTO_CHECKS:
			self.run_check(key)

	def reset(self):
		for row in self.rows.values():
			row.set(NOT_RUN, "-")
		for box in self.manual_checks:
			box.setChecked(False)
		self.update_summary()

	def update_summary(self, *_):
		counts = {s: 0 for s in (PASS, WARN, FAIL, NOT_RUN, RUNNING)}
		for row in self.rows.values():
			counts[row.status] += 1
		manual_done = sum(b.isChecked() for b in self.manual_checks)
		text = (f"<b>{counts[PASS]}</b> pass &nbsp; <b style='color:#b38600'>{counts[WARN]}</b> warn &nbsp; "
			f"<b style='color:#d93025'>{counts[FAIL]}</b> fail &nbsp; {counts[NOT_RUN]} not run &nbsp;|&nbsp; "
			f"manual {manual_done}/{len(self.manual_checks)}")
		if counts[PASS] == len(self.rows) and manual_done == len(self.manual_checks):
			text += " &nbsp; <b style='color:#2e9e4f'>READY TO FLY</b>"
		self.summary.setText(text)
