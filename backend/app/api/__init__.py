from .health import health_bp
from .stats import stats_bp

# add new blueprints here
blueprints = [
	health_bp,
	stats_bp
]


def register_api(app):
	for bp in blueprints:
		app.register_blueprint(bp)