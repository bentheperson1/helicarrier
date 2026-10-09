import os
from pathlib import Path

base_dir = Path(__file__).resolve().parent.parent.parent


class BaseConfig:
	SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
	FRONTEND_DIST = os.environ.get("FRONTEND_DIST", str(base_dir / "frontend" / "dist"))
	DEBUG = False


class DevelopmentConfig(BaseConfig):
	DEBUG = True


class ProductionConfig(BaseConfig):
	pass


config_by_name = {
	"development": DevelopmentConfig,
	"production": ProductionConfig
}