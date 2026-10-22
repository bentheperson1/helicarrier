import os

from PySide6.QtCore import QUrl, Slot
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

# Leaflet map rendered inside Qt's embedded browser. Python pushes updates
# into the page by calling the JS functions defined below (updateDrone, etc).
# Leaflet is bundled in ground_control/web/ so the map, drone marker, and trail
# work offline (e.g. on the Pi's hotspot at the field). Only the background
# imagery tiles need internet; without it the map is blank but still tracks.
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
MAP_HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<link rel="stylesheet" href="leaflet.css">
<script src="leaflet.js"></script>
<style>
	html, body, #map { height: 100%; margin: 0; background: #1e1e1e; }
	.drone-icon svg { transform-origin: 50% 50%; }
	#status {
		position: absolute; top: 10px; left: 50%; transform: translateX(-50%);
		z-index: 1000; background: rgba(0, 0, 0, 0.7); color: #fff;
		font: 13px -apple-system, sans-serif; padding: 6px 12px; border-radius: 6px;
	}
</style>
</head>
<body>
<div id="map"></div>
<script>
	if (typeof L === "undefined") {
		document.body.innerHTML = '<p style="color:#fff;font:14px sans-serif;padding:20px">' +
			'Map library failed to load: check that ground_control/web/leaflet.js exists.</p>';
		throw new Error("Leaflet missing");
	}
</script>
<div id="status">Waiting for GPS...</div>
<script>
	const map = L.map("map", { zoomControl: true }).setView([32.9857, -96.7502], 16);

	const satellite = L.tileLayer(
		"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
		{ maxZoom: 20, maxNativeZoom: 19, attribution: "Tiles &copy; Esri" }
	);
	const streets = L.tileLayer(
		"https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
		{ maxZoom: 20, attribution: "&copy; OpenStreetMap contributors &copy; CARTO" }
	);
	satellite.addTo(map);
	L.control.layers({ "Satellite": satellite, "Streets": streets }).addTo(map);
	L.control.scale({ imperial: true, metric: true }).addTo(map);

	const droneIcon = L.divIcon({
		className: "drone-icon",
		iconSize: [32, 32],
		iconAnchor: [16, 16],
		html: '<svg id="drone-svg" width="32" height="32" viewBox="0 0 32 32">' +
			'<polygon points="16,2 28,28 16,22 4,28" fill="#ff3b30" stroke="#fff" stroke-width="2"/></svg>'
	});

	let marker = null;
	let follow = true;
	let trail = L.polyline([], { color: "#ffcc00", weight: 3, opacity: 0.85 }).addTo(map);
	let firstFix = true;

	function updateDrone(lat, lon, heading) {
		const pos = [lat, lon];
		if (marker === null) {
			marker = L.marker(pos, { icon: droneIcon }).addTo(map);
		} else {
			marker.setLatLng(pos);
		}
		const svg = document.getElementById("drone-svg");
		if (svg) svg.style.transform = "rotate(" + heading + "deg)";

		const pts = trail.getLatLngs();
		if (pts.length === 0 || map.distance(pts[pts.length - 1], pos) > 0.5) {
			trail.addLatLng(pos);
			if (pts.length > 5000) { pts.shift(); trail.setLatLngs(pts); }
		}

		if (firstFix) {
			map.setView(pos, 18);
			firstFix = false;
		} else if (follow) {
			map.panTo(pos, { animate: false });
		}
	}

	function setStatus(text) {
		const el = document.getElementById("status");
		el.textContent = text;
		el.style.display = text ? "block" : "none";
	}

	function setFollow(on) { follow = on; }
	function clearTrail() { trail.setLatLngs([]); }
	function centerOnDrone() { if (marker) map.setView(marker.getLatLng(), Math.max(map.getZoom(), 17)); }
</script>
</body>
</html>
"""


class MapView(QWidget):
	def __init__(self):
		super().__init__()

		self.page_ready = False
		self.pending_js = []

		self.web = QWebEngineView()
		settings = self.web.settings()
		# page is loaded from local files (leaflet) but fetches map tiles online
		settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
		settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
		self.web.loadFinished.connect(self.on_load_finished)
		self.web.setHtml(MAP_HTML, QUrl.fromLocalFile(WEB_DIR + os.sep))

		self.follow_checkbox = QCheckBox("Follow drone")
		self.follow_checkbox.setChecked(True)
		self.center_button = QPushButton("Center")
		self.clear_button = QPushButton("Clear trail")
		self.gps_label = QLabel("GPS: -")

		controls = QHBoxLayout()
		controls.addWidget(self.gps_label, 1)
		controls.addWidget(self.follow_checkbox)
		controls.addWidget(self.center_button)
		controls.addWidget(self.clear_button)

		layout = QVBoxLayout()
		layout.setContentsMargins(0, 0, 0, 0)
		layout.addLayout(controls)
		layout.addWidget(self.web, 1)
		self.setLayout(layout)

		self.follow_checkbox.toggled.connect(lambda on: self.run_js(f"setFollow({'true' if on else 'false'})"))
		self.center_button.clicked.connect(lambda: self.run_js("centerOnDrone()"))
		self.clear_button.clicked.connect(lambda: self.run_js("clearTrail()"))

	@Slot(bool)
	def on_load_finished(self, ok):
		self.page_ready = ok
		if ok:
			for js in self.pending_js:
				self.web.page().runJavaScript(js)
		self.pending_js.clear()

	def run_js(self, js):
		if self.page_ready:
			self.web.page().runJavaScript(js)
		else:
			# only the most recent drone position matters before the page loads
			self.pending_js = [j for j in self.pending_js if not j.startswith(js.split("(")[0])]
			self.pending_js.append(js)

	def update_telemetry(self, t):
		sats = t.get("gps_sats")
		fix = t.get("gps_fix")
		fix_names = {0: "No GPS", 1: "No fix", 2: "2D fix", 3: "3D fix", 4: "DGPS", 5: "RTK float", 6: "RTK fixed"}
		if sats is not None:
			self.gps_label.setText(f"GPS: {fix_names.get(fix, f'fix {fix}')}, {sats} sats")

		lat = t.get("lat")
		lon = t.get("lon")

		# the FC reports 0,0 until it has a position estimate
		if lat is None or (lat == 0 and lon == 0):
			if sats is not None:
				self.run_js(f"setStatus('Waiting for position: {fix_names.get(fix, '')}, {sats} sats')")
			return

		heading = t.get("heading", 0.0)
		self.run_js("setStatus('')")
		self.run_js(f"updateDrone({lat:.7f}, {lon:.7f}, {heading:.1f})")
