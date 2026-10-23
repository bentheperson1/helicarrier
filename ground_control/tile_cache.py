"""Offline map tile cache.

The map page asks for tiles with URLs like  gcstile:esri/17/30123/53210  instead of
going to the internet directly. This module answers those requests:

  1. tile already saved on disk  -> served from disk (works with no internet)
  2. not saved yet               -> downloaded, saved to disk, then served
  3. not saved and no internet   -> left blank

So the workflow at the field is: open ground control on school Wi-Fi, look around
the flying area (or press "Save area offline"), then switch to the drone's network.
Everything you looked at is still there.

Tiles are kept in ~/.megadrone_gcs/tiles/ and survive app restarts.
"""
import math
import os

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWebEngineCore import QWebEngineUrlRequestJob, QWebEngineUrlScheme, QWebEngineUrlSchemeHandler

SCHEME = b"gcstile"
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".megadrone_gcs", "tiles")
USER_AGENT = b"MegadroneGCS/1.0 (UTD AIAA student ground station)"

# name -> (real tile URL template, mime type, file extension)
PROVIDERS = {
	"esri": (
		"https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
		b"image/jpeg", "jpg"
	),
	"carto": (
		"https://a.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png",
		b"image/png", "png"
	),
}

# "Save area offline" limit, to stay a polite user of the free tile servers
MAX_PREFETCH_TILES = 600
PREFETCH_CONCURRENCY = 4


def register_scheme():
	"""Must run before QApplication is created (done when this module is imported)."""
	scheme = QWebEngineUrlScheme(SCHEME)
	scheme.setSyntax(QWebEngineUrlScheme.Syntax.Path)
	scheme.setFlags(
		QWebEngineUrlScheme.Flag.SecureScheme
		| QWebEngineUrlScheme.Flag.CorsEnabled
		| QWebEngineUrlScheme.Flag.ContentSecurityPolicyIgnored
	)
	QWebEngineUrlScheme.registerScheme(scheme)


register_scheme()


def tiles_for_area(south, west, north, east, zoom_min, zoom_max):
	"""All (z, x, y) web-mercator tiles covering a lat/lon box."""
	def to_tile(lat, lon, z):
		lat = max(min(lat, 85.0511), -85.0511)
		n = 2 ** z
		x = int((lon + 180.0) / 360.0 * n)
		y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
		return min(max(x, 0), n - 1), min(max(y, 0), n - 1)

	tiles = []
	for z in range(zoom_min, zoom_max + 1):
		x0, y0 = to_tile(north, west, z)
		x1, y1 = to_tile(south, east, z)
		for x in range(x0, x1 + 1):
			for y in range(y0, y1 + 1):
				tiles.append((z, x, y))
	return tiles


class TileCache(QObject):
	prefetch_progress = Signal(int, int, int)  # done, total, failed
	prefetch_finished = Signal(int, int)       # saved, failed

	def __init__(self, cache_dir = CACHE_DIR):
		super().__init__()
		self.cache_dir = cache_dir
		os.makedirs(self.cache_dir, exist_ok = True)

		self.network = QNetworkAccessManager(self)
		self.network.setTransferTimeout(8000)  # fail fast when there's no internet

		self.waiting = {}  # (provider, z, x, y) -> list of callbacks
		self.prefetch_queue = []
		self.prefetch_total = 0
		self.prefetch_done = 0
		self.prefetch_failed = 0
		self.prefetch_active = 0

	def path_for(self, provider, z, x, y):
		ext = PROVIDERS[provider][2]
		return os.path.join(self.cache_dir, provider, str(z), str(x), f"{y}.{ext}")

	def cached_count(self):
		count = 0
		for _root, _dirs, files in os.walk(self.cache_dir):
			count += len(files)
		return count

	def get(self, provider, z, x, y, callback):
		"""callback(bytes or None). Disk first, then network."""
		path = self.path_for(provider, z, x, y)
		if os.path.exists(path):
			with open(path, "rb") as f:
				callback(f.read())
			return

		key = (provider, z, x, y)
		if key in self.waiting:
			self.waiting[key].append(callback)
			return
		self.waiting[key] = [callback]

		url = PROVIDERS[provider][0].format(z = z, x = x, y = y)
		request = QNetworkRequest(QUrl(url))
		request.setRawHeader(b"User-Agent", USER_AGENT)
		reply = self.network.get(request)
		reply.finished.connect(lambda: self.on_download_finished(reply, key, path))

	def on_download_finished(self, reply, key, path):
		data = None
		if reply.error() == QNetworkReply.NetworkError.NoError:
			data = bytes(reply.readAll())
			if data:
				os.makedirs(os.path.dirname(path), exist_ok = True)
				tmp = path + ".part"
				with open(tmp, "wb") as f:
					f.write(data)
				os.replace(tmp, path)
			else:
				data = None
		reply.deleteLater()

		for callback in self.waiting.pop(key, []):
			callback(data)

	# ---- "Save area offline" ---------------------------------------------
	def prefetch(self, provider, tiles):
		todo = [t for t in tiles if not os.path.exists(self.path_for(provider, *t))]
		self.prefetch_queue = [(provider, *t) for t in todo]
		self.prefetch_total = len(todo)
		self.prefetch_done = 0
		self.prefetch_failed = 0
		self.prefetch_active = 0
		if not todo:
			self.prefetch_finished.emit(0, 0)
			return
		for _ in range(PREFETCH_CONCURRENCY):
			self.prefetch_next()

	def prefetch_next(self):
		if not self.prefetch_queue:
			if self.prefetch_active == 0:
				self.prefetch_finished.emit(self.prefetch_done - self.prefetch_failed, self.prefetch_failed)
			return
		provider, z, x, y = self.prefetch_queue.pop(0)
		self.prefetch_active += 1

		def done(data):
			self.prefetch_active -= 1
			self.prefetch_done += 1
			if data is None:
				self.prefetch_failed += 1
			self.prefetch_progress.emit(self.prefetch_done, self.prefetch_total, self.prefetch_failed)
			self.prefetch_next()

		self.get(provider, z, x, y, done)


class TileSchemeHandler(QWebEngineUrlSchemeHandler):
	"""Answers gcstile:<provider>/<z>/<x>/<y> requests from the map page."""

	def __init__(self, cache):
		super().__init__()
		self.cache = cache
		self.live_jobs = set()

	def requestStarted(self, job):
		try:
			provider, z, x, y = job.requestUrl().path().strip("/").split("/")
			z, x, y = int(z), int(x), int(y.split(".")[0])
			if provider not in PROVIDERS:
				raise ValueError(provider)
		except ValueError:
			job.fail(QWebEngineUrlRequestJob.Error.UrlInvalid)
			return

		# the browser can cancel a tile request (e.g. you zoomed away) and delete the job
		job_id = id(job)
		self.live_jobs.add(job_id)
		job.destroyed.connect(lambda *_: self.live_jobs.discard(job_id))
		mime = PROVIDERS[provider][1]

		def respond(data):
			if job_id not in self.live_jobs:
				return
			try:
				if data is None:
					job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
					return
				buffer = QBuffer(job)
				buffer.setData(QByteArray(data))
				buffer.open(QIODevice.ReadOnly)
				job.reply(QByteArray(mime), buffer)
			except RuntimeError:
				pass  # job was deleted between the check and the reply

		self.cache.get(provider, z, x, y, respond)
