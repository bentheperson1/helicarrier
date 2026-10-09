from pathlib import Path

from flask import abort, send_from_directory
from werkzeug.exceptions import NotFound


def register_web(app):
	dist_dir = Path(app.config["FRONTEND_DIST"])

	@app.route("/", defaults={"path": ""})
	@app.route("/<path:path>")
	def serve_frontend(path):
		# unmatched api routes should 404, not fall back to the react app
		if path.startswith("api/"):
			abort(404)

		if not (dist_dir / "index.html").exists():
			return "Frontend not built. Run 'npm run build' in /frontend, or use the Vite dev server.", 503

		if path:
			try:
				return send_from_directory(dist_dir, path)
			except NotFound:
				pass

		# spa fallback so client side routes work on refresh
		return send_from_directory(dist_dir, "index.html")