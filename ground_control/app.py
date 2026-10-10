import sys

from PySide6.QtWidgets import *
from mavlink_manager import LinkController

class MainWindow(QMainWindow):
	def __init__(self):
		super().__init__()
		
		self.setWindowTitle("Megadrone Ground Control")
		self.resize(900, 600)
		
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
		telemetry_form.addRow("Position", self.position_label)
		telemetry_form.addRow("Altitude", self.alt_label)
		telemetry_form.addRow("Attitude", self.attitude_label)
		telemetry_form.addRow("Battery", self.battery_label)
		
		layout = QVBoxLayout()
		layout.addLayout(top_row)
		layout.addWidget(self.state_label)
		layout.addLayout(telemetry_form)
		layout.addWidget(self.log_box, 1)
		
		central = QWidget()
		central.setLayout(layout)
		self.setCentralWidget(central)
	
	def connect_signals(self):
		self.connect_button.clicked.connect(self.on_connect_clicked)
		
		self.link.state_changed.connect(self.on_state_changed)
		self.link.telemetry_updated.connect(self.on_telemetry)
		self.link.error_occurred.connect(self.on_error)
		self.link.log_message.connect(self.on_error)
	
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