from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
	QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSlider, QSpinBox
)
import os
# MAVLink 2 message definitions: needed for servo outputs 9-16 (AUX pins) and other extension fields
os.environ.setdefault("MAVLINK20", "1")
from pymavlink import mavutil

PWM_MIN = 800
PWM_MAX = 2200


def make_set_servo_command(channel, pwm):
	"""Returns a callable for MavlinkWorker's command queue.

	MAV_CMD_DO_SET_SERVO: param1 = servo output number (1-16), param2 = PWM in microseconds.
	The command travels GCS -> mavlink-router on the Pi -> flight controller, which drives
	the output pin. ArduPilot only accepts it for outputs that aren't assigned to motors.
	"""
	def command(master):
		master.mav.command_long_send(
			master.target_system,
			master.target_component,
			mavutil.mavlink.MAV_CMD_DO_SET_SERVO,
			0,
			channel, pwm, 0, 0, 0, 0, 0
		)
	return command


class ServoPanel(QGroupBox):
	# emitted with (channel, pwm) when the user wants to send a command
	servo_command = Signal(int, int)

	def __init__(self):
		super().__init__("Servo Control")

		self.channel_input = QSpinBox()
		self.channel_input.setRange(1, 16)
		self.channel_input.setValue(9)  # SERVO9 = AUX1 on most Pixhawk-style boards

		self.pwm_slider = QSlider(Qt.Horizontal)
		self.pwm_slider.setRange(PWM_MIN, PWM_MAX)
		self.pwm_slider.setValue(1500)

		self.pwm_input = QSpinBox()
		self.pwm_input.setRange(PWM_MIN, PWM_MAX)
		self.pwm_input.setSuffix(" µs")
		self.pwm_input.setValue(1500)

		self.min_button = QPushButton("Min 1000")
		self.center_button = QPushButton("Center 1500")
		self.max_button = QPushButton("Max 2000")
		self.send_button = QPushButton("Send")
		self.live_checkbox = QCheckBox("Send on slider release")

		self.output_label = QLabel("Current output: -")

		layout = QGridLayout()
		layout.addWidget(QLabel("Output #"), 0, 0)
		layout.addWidget(self.channel_input, 0, 1)
		layout.addWidget(self.output_label, 0, 2, 1, 2)
		layout.addWidget(QLabel("PWM"), 1, 0)
		layout.addWidget(self.pwm_slider, 1, 1, 1, 2)
		layout.addWidget(self.pwm_input, 1, 3)

		buttons = QHBoxLayout()
		buttons.addWidget(self.min_button)
		buttons.addWidget(self.center_button)
		buttons.addWidget(self.max_button)
		buttons.addStretch(1)
		buttons.addWidget(self.live_checkbox)
		buttons.addWidget(self.send_button)
		layout.addLayout(buttons, 2, 0, 1, 4)

		self.setLayout(layout)

		self.pwm_slider.valueChanged.connect(self.pwm_input.setValue)
		self.pwm_input.valueChanged.connect(self.pwm_slider.setValue)
		self.pwm_slider.sliderReleased.connect(self.on_slider_released)

		self.min_button.clicked.connect(lambda: self.set_and_send(1000))
		self.center_button.clicked.connect(lambda: self.set_and_send(1500))
		self.max_button.clicked.connect(lambda: self.set_and_send(2000))
		self.send_button.clicked.connect(self.send)
		self.channel_input.valueChanged.connect(lambda _: self.output_label.setText("Current output: -"))

		self.servo_outputs = {}
		self.set_link_active(False)

	def set_and_send(self, pwm):
		self.pwm_input.setValue(pwm)
		self.send()

	def on_slider_released(self):
		if self.live_checkbox.isChecked():
			self.send()

	def send(self):
		self.servo_command.emit(self.channel_input.value(), self.pwm_input.value())

	def set_link_active(self, active):
		for w in (self.send_button, self.min_button, self.center_button, self.max_button):
			w.setEnabled(active)
		if not active:
			self.output_label.setText("Current output: -")

	def update_telemetry(self, t):
		outputs = t.get("servo_outputs")
		if not outputs:
			return
		pwm = outputs.get(self.channel_input.value())
		if pwm:
			self.output_label.setText(f"Current output: {pwm} µs")
		else:
			self.output_label.setText("Current output: not reported")
