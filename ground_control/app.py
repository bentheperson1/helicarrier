import sys

from PySide6.QtWidgets import *
from vehicle_page import VehiclePage
from mission_phase import MissionPhasePanel

class MainWindow(QMainWindow):
	def __init__(self):
		super().__init__()

		self.setWindowTitle("Helicarrier Ground Control")
		self.resize(1400, 800)

		self.megadrone_page = VehiclePage("tcp:megadrone.local:5760")

		self.phase_panel = MissionPhasePanel()
		# PX4 sends its GCS stream to UDP 14550
		self.dexi_page = VehiclePage(
			"udpin:0.0.0.0:14550", cells_key = "dexi/battery_cells", extra_panels = [self.phase_panel]
		)
		self.phase_panel.advance_requested.connect(self.dexi_page.send_command)

		self.tabs = QTabWidget()
		self.tabs.addTab(self.megadrone_page, "Megadrone")
		self.tabs.addTab(self.dexi_page, "DEXI")
		self.setCentralWidget(self.tabs)

	def closeEvent(self, event):
		self.megadrone_page.shutdown()
		self.dexi_page.shutdown()
		event.accept()


if __name__ == "__main__":
	app = QApplication(sys.argv)

	window = MainWindow()
	window.show()

	sys.exit(app.exec())
