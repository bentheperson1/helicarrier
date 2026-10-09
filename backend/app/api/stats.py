from flask import Blueprint, jsonify, request

from ..services import stats_service

stats_bp = Blueprint("stats", __name__, url_prefix="/api/stats")


@stats_bp.get("/summary")
def summary():
	return jsonify(stats_service.get_summary())


@stats_bp.get("/timeseries")
def timeseries():
	days = request.args.get("days", default=14, type=int)
	days = max(1, min(days, 365))

	return jsonify(stats_service.get_timeseries(days))