import random
from datetime import date, timedelta


def get_summary():
	return [
		{"id": "users", "label": "Active Users", "value": 1284, "change": 4.2},
		{"id": "revenue", "label": "Revenue", "value": 18420, "change": -1.3, "prefix": "$"},
		{"id": "orders", "label": "Orders", "value": 342, "change": 8.9},
		{"id": "uptime", "label": "Uptime", "value": 99.98, "change": 0.0, "suffix": "%"}
	]


def get_timeseries(days=14):
	rng = random.Random(42)
	start_date = date.today() - timedelta(days=days - 1)

	points = []
	value = 100

	for i in range(days):
		value += rng.randint(-10, 15)

		points.append({
			"date": (start_date + timedelta(days=i)).isoformat(),
			"value": value
		})

	return points