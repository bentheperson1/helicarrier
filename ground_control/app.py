import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import *
from mavlink_manager import LinkController
from map_view import MapView
from servo_panel import ServoPanel, make_set_servo_command

class MainWindow(QMainWindow):
	def __init__(self):
		super().__init__()
		
		self.setWindowTitle("Megadrone Ground Control")
		self.resize(1400, 800)
		
		self.link = LinkController()
		self.is_active = False
		
		self.build_ui()
		self.connect_signals()
		self.on_state_changed("disconnected")
	
	def build_ui(self):
		self.conn_input = QLineEdit()
		self.conn_input.setPlaceholderText("udpin:0.0.0.0:14550  |  tcp:127.0.0.1:5760  |  COM3")
		self.conn_input.setText("tcp:127.0.0.1:5760")
		
		self.baud_input = QComboBox()
		self.baud_input.addItems(["57600", "115200", "921600"])
		
		self.connect_button = QPushButton("Connect")
		
		self.state_label = QLabel()
		
		self.mode_label = QLabel("-")
		self.armed_label = QLabel("-")
		self.position_label = QLabel("-")
		self.alt_label = QLabel("-")
		self.attitude_label = QLabel("-")
		self.battery_label = QLabel("-")
		self.gps_label = QLabel("-")
		
		self.servo_panel = ServoPanel()
		self.map_view = MapView()
		
		self.log_box = QPlainTextEdit()
		self.log_box.setReadOnly(True)
		self.log_box.setMaximumBlockCount(200)
		
		top_row = QHBoxLayout()
		top_row.addWidget(self.conn_input, 1)
		top_row.addWidget(self.baud_input)
		top_row.addWidget(self.connect_button)
		
		telemetry_form = QFormLayout()
		telemetry_form.addRow("Mode", self.mode_label)
		telemetry_form.addRow("Armed", self.armed_label)
		telemetry_form.addRow("GPS", self.gps_label)
		telemetry_form.addRow("Position", self.position_label)
		telemetry_form.addRow("Altitude", self.alt_label)
		telemetry_form.addRow("Attitude", self.attitude_label)
		telemetry_form.addRow("Battery", self.battery_label)
		
		left_layout = QVBoxLayout()
		left_layout.setContentsMargins(0, 0, 0, 0)
		left_layout.addWidget(self.state_label)
		left_layout.addLayout(telemetry_form)
		left_layout.addWidget(self.servo_panel)
		left_layout.addWidget(self.log_box, 1)
		
		left_panel = QWidget()
		left_panel.setLayout(left_layout)
		
		splitter = QSplitter(Qt.Horizontal)
		splitter.addWidget(left_panel)
		splitter.addWidget(self.map_view)
		splitter.setStretchFactor(0, 0)
		splitter.setStretchFactor(1, 1)
		splitter.setSizes([480, 920])
		
		layout = QVBoxLayout()
		layout.addLayout(top_row)
		layout.addWidget(splitter, 1)
		
		central = QWidget()
		central.setLayout(layout)
		self.setCentralWidget(central)
	
	def connect_signals(self):
		self.connect_button.clicked.connect(self.on_connect_clicked)
		
		self.link.state_changed.connect(self.on_state_changed)
		self.link.telemetry_updated.connect(self.on_telemetry)
		self.link.error_occurred.connect(self.on_error)
		self.link.log_message.connect(self.on_error)
		
		self.servo_panel.servo_command.connect(self.on_servo_command)
	
	def on_connect_clicked(self):
		if self.is_active:
			self.link.disconnect_link()
			self.connect_button.setEnabled(False)
			return
		
		conn_string = self.conn_input.text().strip()
		
		if not conn_string:
			self.on_error("Enter a connection string")
			return
		
		self.link.connect_link(conn_string, int(self.baud_input.currentText()))
	
	def on_state_changed(self, state):
		self.is_active = state != "disconnected"
		
		colors = {
			"disconnected": "gray",
			"connecting": "orange",
			"connected": "green",
			"link_lost": "red"
		}
		
		self.state_label.setText(f"State: {state}")
		self.state_label.setStyleSheet(f"color: {colors.get(state, 'black')}; font-weight: bold;")
		
		self.connect_button.setText("Disconnect" if self.is_active else "Connect")
		self.connect_button.setEnabled(True)
		
		self.conn_input.setEnabled(not self.is_active)
		self.baud_input.setEnabled(not self.is_active)
		
		self.servo_panel.set_link_active(state == "connected")
	
	def on_telemetry(self, t):
		if "mode" in t:
			self.mode_label.setText(str(t["mode"]))
		if "armed" in t:
			self.armed_label.setText("Yes" if t["armed"] else "No")
		if "lat" in t:
			self.position_label.setText(f"{t['lat']:.6f}, {t['lon']:.6f}")
		if "alt" in t:
			self.alt_label.setText(f"{t['alt']:.1f} m")
		if "roll" in t:
			self.attitude_label.setText(f"R {t['roll']:.2f}  P {t['pitch']:.2f}  Y {t['yaw']:.2f}")
		if "battery_v" in t:
			self.battery_label.setText(f"{t['battery_v']:.2f} V")
		if "gps_sats" in t:
			fix_names = {0: "No GPS", 1: "No fix", 2: "2D fix", 3: "3D fix", 4: "DGPS", 5: "RTK float", 6: "RTK fixed"}
			self.gps_label.setText(f"{fix_names.get(t.get('gps_fix'), '?')}, {t['gps_sats']} sats")
		
		self.map_view.update_telemetry(t)
		self.servo_panel.update_telemetry(t)
	
	def on_servo_command(self, channel, pwm):
		if self.armed_label.text() == "Yes":
			answer = QMessageBox.question(
				self, "Drone is armed",
				f"The drone is ARMED. Send servo {channel} -> {pwm} µs anyway?"
			)
			if answer != QMessageBox.Yes:
				return
		
		if self.link.send_command(make_set_servo_command(channel, pwm)):
			self.on_error(f"Sent DO_SET_SERVO: output {channel} -> {pwm} µs")
	
	def on_error(self, text):
		self.log_box.appendPlainText(text)
	
	def closeEvent(self, event):
		self.link.shutdown()
		event.accept()


if __name__ == "__main__":
	app = QApplication(sys.argv)
	
	window = MainWindow()
	window.show()
	
	sys.exit(app.exec())