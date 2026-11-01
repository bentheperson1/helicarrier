#!/usr/bin/env python3
"""Receive the other drone's telemetry from its gps_ws_server.

Run on one drone (or a laptop) to see the other drone's position live:

    python3 peer_client.py --peer ws://dexi.local:8765
    python3 peer_client.py --peer ws://dexi.local:8765 --self ws://127.0.0.1:8765   # + distance between the drones
    python3 peer_client.py --peer ws://dexi.local:8765 --json                       # raw JSON lines

Standard library only. Reconnects automatically if either drone drops off the network.

To use the other drone's position in your own code, reuse `subscribe()`:

    async for msg in subscribe("ws://dexi.local:8765"):
        print(msg["drone"], msg["position"])
"""
import argparse
import asyncio
import json
import math
import time

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # find ws_lite.py next to this file
import ws_lite


async def subscribe(url, retry_delay = 2.0, on_status = None):
	"""Yields each telemetry dict from a gps_ws_server forever, reconnecting as needed."""
	while True:
		try:
			ws = await ws_lite.connect(url)
			if on_status:
				on_status(f"connected to {url}")
			try:
				while True:
					text = await asyncio.wait_for(ws.recv(), 10)
					yield json.loads(text)
			finally:
				await ws.close()
		except (OSError, ConnectionError, ws_lite.WebSocketClosed, asyncio.TimeoutError, ValueError) as e:
			if on_status:
				on_status(f"lost {url} ({type(e).__name__}), retrying in {retry_delay:.0f} s")
		await asyncio.sleep(retry_delay)


def distance_bearing(a, b):
	"""Great-circle distance (m) and bearing (deg) from position a to position b."""
	lat1, lon1, lat2, lon2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
	dlat, dlon = lat2 - lat1, lon2 - lon1
	h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
	dist = 2 * 6371000 * math.asin(math.sqrt(h))
	y = math.sin(dlon) * math.cos(lat2)
	x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
	return dist, (math.degrees(math.atan2(y, x)) + 360) % 360


PEER_TIMEOUT = 3.0  # s without a message from a server before we say it's gone


def fresh_position(msg):
	"""The message's position, or None if there isn't a current one."""
	pos = msg.get("position") if msg else None
	if not pos or pos.get("stale") or not msg.get("fc_connected"):
		return None
	return pos


def describe(msg):
	pos = fresh_position(msg)
	gps = msg.get("gps") or {}
	if not msg.get("fc_connected"):
		return f"{msg['drone']}: flight controller not connected"
	where = (f"{pos['lat']:.7f}, {pos['lon']:.7f}  alt {pos['alt_rel']:.1f} m" if pos else "NO CURRENT POSITION")
	return (f"{msg['drone']}: {where}  | {gps.get('fix_name', '?')} {gps.get('sats', '?')} sats"
		f"  | {msg.get('mode')} {'ARMED' if msg.get('armed') else 'disarmed'}")


async def main():
	p = argparse.ArgumentParser(description = "Show the other drone's live telemetry")
	p.add_argument("--peer", required = True, help = "the other drone's server, e.g. ws://dexi.local:8765")
	p.add_argument("--self", dest = "self_url", help = "this drone's own server, to print the distance between them")
	p.add_argument("--json", action = "store_true", help = "print raw JSON messages")
	args = p.parse_args()

	latest = {}
	status = lambda text: print(f"[{time.strftime('%H:%M:%S')}] {text}", flush = True)

	async def follow(key, url):
		async for msg in subscribe(url, on_status = status):
			msg["_received"] = time.monotonic()
			latest[key] = msg

	async def printer():
		while True:
			await asyncio.sleep(1.0)
			peer = latest.get("peer")
			if peer is None:
				continue
			silent = time.monotonic() - peer["_received"]
			if silent > PEER_TIMEOUT:
				print(f"[{time.strftime('%H:%M:%S')}] {peer['drone']}: NO DATA for {silent:.0f} s (link down)", flush = True)
				continue
			if args.json:
				peer = {k: v for k, v in peer.items() if k != "_received"}
				print(json.dumps(peer), flush = True)
				continue
			line = describe(peer)
			me = latest.get("self")
			if me and time.monotonic() - me["_received"] > PEER_TIMEOUT:
				me = None
			mine, theirs = fresh_position(me), fresh_position(peer)
			if args.self_url and not (mine and theirs):
				line += "  | distance unknown (no current position for " + ("this drone" if not mine else peer["drone"]) + ")"
			elif mine and theirs:
				dist, bearing = distance_bearing(mine, theirs)
				dalt = theirs["alt_rel"] - mine["alt_rel"]
				line += f"  | {dist:.1f} m away, bearing {bearing:.0f}°, {dalt:+.1f} m higher"
			print(f"[{time.strftime('%H:%M:%S')}] {line}", flush = True)

	tasks = [follow("peer", args.peer), printer()]
	if args.self_url:
		tasks.append(follow("self", args.self_url))
	await asyncio.gather(*tasks)


if __name__ == "__main__":
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		pass
