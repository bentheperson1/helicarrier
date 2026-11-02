import argparse
import asyncio
import json
import time

from pymavlink import mavutil
from websockets.asyncio.server import broadcast, serve

# Reads GPS_RAW_INT from the flight controller (same as test/realtime_gps.py)
# and publishes each one as JSON to every client connected to ws://<pi>:8765


async def handler(websocket):
	# clients only listen, so just keep the connection open until they leave
	await websocket.wait_closed()


async def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--name", default = "megadrone", help = "drone name included in each message")
	parser.add_argument("--mavlink", default = "tcp:127.0.0.1:5760", help = "pymavlink connection string")
	parser.add_argument("--port", type = int, default = 8765)
	args = parser.parse_args()

	master = mavutil.mavlink_connection(args.mavlink, source_system = 201)

	print(f"Waiting for heartbeat on {args.mavlink}")
	await asyncio.to_thread(master.wait_heartbeat)
	print(f"Heartbeat received from system {master.target_system} component {master.target_component}")

	async with serve(handler, "0.0.0.0", args.port) as server:
		print(f"Publishing GPS on ws://0.0.0.0:{args.port}")

		while True:
			msg = await asyncio.to_thread(master.recv_match, type = "GPS_RAW_INT", blocking = True, timeout = 1)
			if not msg:
				continue

			gps = {
				"drone": args.name,
				"time": time.time(),
				"lat": msg.lat / 1e7,               # degE7 -> deg
				"lon": msg.lon / 1e7,               # degE7 -> deg
				"alt": msg.alt / 1e3,               # mm -> m (MSL)
				"fix_type": msg.fix_type,           # 0-1: no fix, 2: 2D, 3: 3D
				"satellites": msg.satellites_visible,
			}

			broadcast(server.connections, json.dumps(gps))


if __name__ == "__main__":
	asyncio.run(main())
