import argparse
import asyncio
import json
import websockets

fix_names = {
	0: "no gps",
	1: "no fix",
	2: "2d fix",
	3: "3d fix",
	4: "dgps",
	5: "rtk float",
	6: "rtk fixed",
}

async def listen(ws_url):
	async with websockets.connect(ws_url) as ws:
		print(f"connected to {ws_url}")
		
		async for message in ws:
			try:
				gps = json.loads(message)
			except json.JSONDecodeError:
				print(f"[raw] {message}")
				continue
			
			fix = fix_names.get(gps["fix_type"], gps["fix_type"])
			
			print(
				f"{gps['drone']} | "
				f"lat {gps['lat']:.7f} lon {gps['lon']:.7f} alt {gps['alt']:.1f}m | "
				f"{fix} | sats {gps['satellites']}"
			)

async def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--host", default = "megadrone.local", help = "pi hostname or ip")
	parser.add_argument("--port", type = int, default = 8765)
	args = parser.parse_args()
	
	ws_url = f"ws://{args.host}:{args.port}"
	
	while True:
		try:
			await listen(ws_url)
		except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
			print(f"connection lost: {e}, retrying in 3s...")
			await asyncio.sleep(3)

try:
	asyncio.run(main())
except KeyboardInterrupt:
	print("stopped")