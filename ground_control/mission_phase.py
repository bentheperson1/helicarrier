import time

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFormLayout, QGroupBox, QLabel, QPushButton, QVBoxLayout

# DEXI mission phase protocol. The phase logic and approval live on the drone;
# the GCS only displays what it reports and asks it to advance.
#
# Drone -> GCS: NAMED_VALUE_INT, sent at least once a second and on every change
#   "PHASE"     current mission phase index
#   "SUBPHASE"  current subphase index
#   "PH_READY"  1 when the drone will accept an advance request, else 0
#
# GCS -> Drone: COMMAND_LONG MAV_CMD_USER_1, addressed to whichever system/component
# sent the PHASE messages
#   param1  phase index the GCS is looking at
#   param2  subphase index the GCS is looking at
# The drone should reject (MAV_RESULT_DENIED) if those don't match where it is now,
# so a late or double click can't skip a phase. It answers with COMMAND_ACK.
PHASE_VALUE_NAMES = {
	"PHASE": "mission_phase",
	"SUBPHASE": "mission_subphase",
	"PH_READY": "phase_ready",
}
ADVANCE_PHASE_CMD = 31010  # MAV_CMD_USER_1

# a phase report older than this is treated as unknown
PHASE_TIMEOUT = 3.0
# how long the advance button stays locked waiting for the drone to move on
ADVANCE_LOCKOUT = 3.0


def make_advance_phase_command(phase, subphase, target_system, target_component):
	def command(master):
		master.mav.command_long_send(
			target_system, target_component,
			ADVANCE_PHASE_CMD, 0,
			phase, subphase, 0, 0, 0, 0, 0
		)
	return command


class MissionPhasePanel(QGroupBox):
	# emitted with a callable(master) for LinkController.send_command
	advance_requested = Signal(object, str)

	def __init__(self):
		super().__init__("Mission phase")

		self.phase_label = QLabel("-")
		self.subphase_label = QLabel("-")
		self.ready_label = QLabel("-")
		for label in (self.phase_label, self.subphase_label):
			label.setStyleSheet("font-size: 16px; font-weight: bold;")

		self.advance_button = QPushButton("Advance to next phase")
		self.advance_button.setMinimumHeight(36)

		form = QFormLayout()
		form.addRow("Phase", self.phase_label)
		form.addRow("Subphase", self.subphase_label)
		form.addRow("Can advance", self.ready_label)

		layout = QVBoxLayout()
		layout.addLayout(form)
		layout.addWidget(self.advance_button)
		self.setLayout(layout)

		self.link_active = False
		self.latest = {}
		# (phase, subphase, deadline) of the request we're waiting on
		self.pending = None

		self.advance_button.clicked.connect(self.on_advance_clicked)
		self.reset()

	def reset(self):
		self.latest = {}
		self.pending = None
		self.phase_label.setText("-")
		self.subphase_label.setText("-")
		self.set_ready_text("Unknown (no phase report)", "gray")
		self.advance_button.setEnabled(False)

	def set_ready_text(self, text, color):
		self.ready_label.setText(text)
		self.ready_label.setStyleSheet(f"color: {color}; font-weight: bold;")

	def on_link_state(self, state):
		self.link_active = state == "connected"
		if not self.link_active:
			self.reset()

	def update_telemetry(self, t):
		self.latest = t
		now = time.monotonic()
		fresh = "phase_time" in t and now - t["phase_time"] < PHASE_TIMEOUT

		phase = t.get("mission_phase")
		subphase = t.get("mission_subphase")
		suffix = "" if fresh else "  (stale)"
		self.phase_label.setText("-" if phase is None else f"{phase}{suffix}")
		self.subphase_label.setText("-" if subphase is None else f"{subphase}{suffix}")

		# unlock once the drone has moved on, or gave up waiting for it to
		if self.pending is not None:
			pending_phase, pending_subphase, deadline = self.pending
			if (phase, subphase) != (pending_phase, pending_subphase) or now > deadline:
				self.pending = None

		ready = fresh and t.get("phase_ready", False)
		if not fresh:
			self.set_ready_text("Unknown (no phase report)", "gray")
		elif self.pending is not None:
			self.set_ready_text("Advance requested...", "orange")
		elif ready:
			self.set_ready_text("READY", "green")
		else:
			self.set_ready_text("Not ready", "red")

		self.advance_button.setEnabled(
			self.link_active and ready and self.pending is None
			and phase is not None and subphase is not None and "phase_source" in t
		)

	def on_advance_clicked(self):
		t = self.latest
		phase = t.get("mission_phase")
		subphase = t.get("mission_subphase")
		source = t.get("phase_source")
		if phase is None or subphase is None or source is None:
			return

		self.pending = (phase, subphase, time.monotonic() + ADVANCE_LOCKOUT)
		self.advance_button.setEnabled(False)
		self.advance_requested.emit(
			make_advance_phase_command(phase, subphase, *source),
			f"Requested advance from phase {phase}.{subphase}"
		)
