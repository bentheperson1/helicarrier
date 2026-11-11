import asyncio
import json

from websockets.asyncio.server import broadcast, serve

from config import PORT
from gps import make_gps_stream
from hatch import make_hatch


def handle_message(hatch, message):
	try:
		data = json.loads(message)
	except json.JSONDecodeError:
		print(f"Bad JSON: {message}")
		return
	
	if "open_hatch" in data:
		if data["open_hatch"]:
			hatch.open()
		else:
			hatch.close()


def make_handler(hatch):
	async def handler(websocket):
		print("Client connected")
		
		async for message in websocket:
			print(f"Received: {message}")
			handle_message(hatch, message)
		
		print("Client disconnected")
	
	return handler


async def publish_gps(server):
	async for gps in make_gps_stream():
		broadcast(server.connections, json.dumps(gps))


async def main():
	hatch = make_hatch()
	
	async with serve(make_handler(hatch), "0.0.0.0", PORT) as server:
		print(f"Listening on ws://0.0.0.0:{PORT}")
		await publish_gps(server)


if __name__ == "__main__":
	asyncio.run(main())