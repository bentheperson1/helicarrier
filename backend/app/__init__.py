import os

from flask import Flask

from .config import config_by_name
from .api import register_api
from .web import register_web


def create_app(config_name=None):
	config_name = config_name or os.environ.get("APP_ENV", "development")

	app = Flask(__name__, static_folder=None)
	app.config.from_object(config_by_name[config_name])

	register_api(app)
	register_web(app)

	return app