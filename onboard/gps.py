import asyncio
import time

from config import DRONE_NAME, MAVLINK_CONNECTION, ON_REAL_DRONE


async def mavlink_gps_stream():
	from pymavlink import mavutil
	
	master = mavutil.mavlink_connection(MAVLINK_CONNECTION, source_system = 201)
	
	print(f"Waiting for heartbeat on {MAVLINK_CONNECTION}")
	await asyncio.to_thread(master.wait_heartbeat)
	print(f"Heartbeat received from system {master.target_system} component {master.target_component}")
	
	while True:
		msg = await asyncio.to_thread(master.recv_match, type = "GPS_RAW_INT", blocking = True, timeout = 1)
		
		if not msg:
			continue
		
		yield {
			"drone": DRONE_NAME,
			"time": time.time(),
			"lat": msg.lat / 1e7,
			"lon": msg.lon / 1e7,
			"alt": msg.alt / 1e3,
			"fix_type": msg.fix_type,
			"satellites": msg.satellites_visible,
		}


async def sim_gps_stream():
	while True:
		await asyncio.sleep(1)
		
		yield {
			"drone": "SIM MEGADRONE",
			"time": time.time(),
			"lat": -1.0,
			"lon": -1.0,
			"alt": -1.0,
			"fix_type": 0,
			"satellites": 0,
		}


def make_gps_stream():
	if ON_REAL_DRONE:
		return mavlink_gps_stream()
	
	return sim_gps_stream()