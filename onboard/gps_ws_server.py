#!/usr/bin/env python3
"""GPS / telemetry WebSocket server for a drone's Raspberry Pi.

Reads MAVLink from mavlink-router (or any MAVLink TCP/UDP source) and broadcasts
the drone's position as JSON to every connected WebSocket client, a few times a
second. The other drone (or ground control, or a laptop) connects to:

    ws://<this-pi>:8765        live stream of JSON messages
    http://<this-pi>:8765/     latest message once (handy for testing with curl / a browser)

Standard library only: runs on the Pi without pip installing anything.

Usage:
    python3 gps_ws_server.py --name megadrone
    python3 gps_ws_server.py --name dexi --mavlink tcp:127.0.0.1:5760 --port 8765 --rate 5

Message format (one JSON object per WebSocket message):
    {
      "type": "telemetry", "drone": "megadrone", "seq": 1234, "time": 1761950000.12,
      "fc_connected": true, "heartbeat_age": 0.4,
      "position": {"lat": 32.99, "lon": -96.75, "alt_msl": 180.2, "alt_rel": 3.1,
                   "age": 0.1, "stale": false},   # null until GPS position; stale = no update for 3+ s
      "velocity": {"north": 0.1, "east": -0.2, "down": 0.0, "ground_speed": 0.22},              # m/s
      "heading": 271.5, "attitude": {"roll": 1.2, "pitch": -0.4, "yaw": -88.5},               # degrees
      "gps": {"fix": 3, "fix_name": "3D fix", "sats": 18, "hdop": 0.6},
      "armed": false, "mode": "LOITER", "battery_v": 49.8
    }

This server only SENDS data. It ignores anything clients send, so it can't be
used to control the drone.
"""
import argparse
import asyncio
import json
import logging
import math
import struct
import time

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # find ws_lite.py next to this file
import ws_lite

log = logging.getLogger("gps_ws")

# ---- minimal MAVLink decoding (layouts checked against pymavlink) ---------------
# msg id -> (name, crc_extra, struct format of the base fields, field names)
MESSAGES = {
	0: ("HEARTBEAT", 50, "<IBBBBB", ("custom_mode", "type", "autopilot", "base_mode", "system_status", "mavlink_version")),
	1: ("SYS_STATUS", 124, "<IIIHHhHHHHHHb", ("present", "enabled", "health", "load", "voltage_battery", "current_battery",
		"drop_rate_comm", "errors_comm", "e1", "e2", "e3", "e4", "battery_remaining")),
	24: ("GPS_RAW_INT", 24, "<QiiiHHHHBB", ("time_usec", "lat", "lon", "alt", "eph", "epv", "vel", "cog", "fix_type", "satellites_visible")),
	30: ("ATTITUDE", 39, "<Iffffff", ("time_boot_ms", "roll", "pitch", "yaw", "rollspeed", "pitchspeed", "yawspeed")),
	33: ("GLOBAL_POSITION_INT", 104, "<IiiiihhhH", ("time_boot_ms", "lat", "lon", "alt", "relative_alt", "vx", "vy", "vz", "hdg")),
}
MSG_REQUEST_DATA_STREAM = (66, 148)
MAV_TYPE_GCS = 6
MAV_TYPE_ONBOARD_CONTROLLER = 18
MAV_AUTOPILOT_INVALID = 8
MAV_AUTOPILOT_ARDUPILOTMEGA = 3
MAV_AUTOPILOT_PX4 = 12
STALE_AFTER = 3.0  # s without a position update before it's flagged stale
MAV_MODE_FLAG_SAFETY_ARMED = 128
MAV_COMP_ID_ONBOARD_COMPUTER = 191

COPTER_MODES = {0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO", 4: "GUIDED", 5: "LOITER", 6: "RTL", 7: "CIRCLE",
	9: "LAND", 11: "DRIFT", 13: "SPORT", 14: "FLIP", 15: "AUTOTUNE", 16: "POSHOLD", 17: "BRAKE", 18: "THROW",
	19: "AVOID_ADSB", 20: "GUIDED_NOGPS", 21: "SMART_RTL", 22: "FLOWHOLD", 23: "FOLLOW", 24: "ZIGZAG",
	25: "SYSTEMID", 26: "AUTOROTATE", 27: "AUTO_RTL", 28: "TURTLE"}
PX4_MAIN_MODES = {1: "MANUAL", 2: "ALTCTL", 3: "POSCTL", 4: "AUTO", 5: "ACRO", 6: "OFFBOARD", 7: "STABILIZED", 8: "RATTITUDE"}
PX4_AUTO_MODES = {1: "READY", 2: "TAKEOFF", 3: "LOITER", 4: "MISSION", 5: "RTL", 6: "LAND", 8: "FOLLOW_TARGET", 9: "PRECLAND"}
FIX_NAMES = {0: "No GPS", 1: "No fix", 2: "2D fix", 3: "3D fix", 4: "DGPS", 5: "RTK float", 6: "RTK fixed"}


def x25_crc(data, crc = 0xFFFF):
	for b in data:
		tmp = b ^ (crc & 0xFF)
		tmp = (tmp ^ (tmp << 4)) & 0xFF
		crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
	return crc


class MavlinkParser:
	"""Splits a byte stream into MAVLink v1/v2 frames, checks CRC, decodes the messages we know."""

	def __init__(self):
		self.buf = bytearray()

	def feed(self, data):
		self.buf += data
		out = []
		while True:
			starts = [i for i in (self.buf.find(b"\xfd"), self.buf.find(b"\xfe")) if i >= 0]
			if not starts:
				self.buf.clear()
				return out
			del self.buf[:min(starts)]
			if len(self.buf) < 3:
				return out
			v2 = self.buf[0] == 0xFD
			plen = self.buf[1]
			if v2 and self.buf[2] & ~0x01:
				del self.buf[:1]  # only the "signed" flag is valid: false start byte
				continue
			if v2:
				size = 12 + plen + (13 if self.buf[2] & 0x01 else 0)
				header_len = 10
			else:
				size = 8 + plen
				header_len = 6
			if len(self.buf) < size:
				return out
			frame = bytes(self.buf[:size])
			if v2:
				sysid, compid = frame[5], frame[6]
				msgid = frame[7] | (frame[8] << 8) | (frame[9] << 16)
			else:
				sysid, compid, msgid = frame[3], frame[4], frame[5]
			payload = frame[header_len:header_len + plen]
			info = MESSAGES.get(msgid)
			if info is None:
				# can't check the CRC of a message we don't know, so only skip it whole if
				# another frame starts right after it; otherwise it was a false start byte
				if len(self.buf) == size or self.buf[size] in (0xFD, 0xFE):
					del self.buf[:size]
				else:
					del self.buf[:1]
				continue
			name, crc_extra, fmt, fields = info
			crc = x25_crc(frame[1:header_len + plen])
			crc = x25_crc(bytes([crc_extra]), crc)
			if crc != struct.unpack("<H", frame[header_len + plen:header_len + plen + 2])[0]:
				del self.buf[:1]  # corrupt: resync on the next start byte
				continue
			del self.buf[:size]
			need = struct.calcsize(fmt)
			payload = payload[:need].ljust(need, b"\x00")  # MAVLink 2 trims trailing zeros
			out.append((name, sysid, compid, dict(zip(fields, struct.unpack(fmt, payload)))))


def encode_v2(msgid, crc_extra, payload, seq, sysid, compid):
	header = struct.pack("<BBBBBBBHB", 0xFD, len(payload), 0, 0, seq & 0xFF, sysid, compid, msgid & 0xFFFF, msgid >> 16)
	crc = x25_crc(header[1:] + payload)
	crc = x25_crc(bytes([crc_extra]), crc)
	return header + payload + struct.pack("<H", crc)


# ---- drone state ----------------------------------------------------------------
class DroneState:
	def __init__(self, name):
		self.name = name
		self.target = None  # (sysid, compid) of the autopilot
		self.autopilot = None
		self.fc_connected = False
		self.last_heartbeat = None
		self.data = {}
		self.position_time = None
		self.gps_heading = None   # from GLOBAL_POSITION_INT, when the FC provides it
		self.yaw_heading = None   # from ATTITUDE yaw, as a fallback
		self.seq = 0

	def update(self, name, sysid, compid, m):
		if name == "HEARTBEAT":
			if m["type"] == MAV_TYPE_GCS or m["autopilot"] == MAV_AUTOPILOT_INVALID:
				return  # another ground station / companion, not the flight controller
			if self.target is None:
				self.target = (sysid, compid)
				log.info("locked onto flight controller sys %d comp %d", sysid, compid)
			if (sysid, compid) != self.target:
				return
			self.last_heartbeat = time.monotonic()
			self.autopilot = m["autopilot"]
			self.data["armed"] = bool(m["base_mode"] & MAV_MODE_FLAG_SAFETY_ARMED)
			if m["autopilot"] == MAV_AUTOPILOT_ARDUPILOTMEGA:
				self.data["mode"] = COPTER_MODES.get(m["custom_mode"], f"MODE_{m['custom_mode']}")
			elif m["autopilot"] == MAV_AUTOPILOT_PX4:
				main, sub = (m["custom_mode"] >> 16) & 0xFF, (m["custom_mode"] >> 24) & 0xFF
				name = PX4_MAIN_MODES.get(main, f"MODE_{main}")
				if main == 4 and sub in PX4_AUTO_MODES:
					name = f"AUTO_{PX4_AUTO_MODES[sub]}"
				self.data["mode"] = name
			else:
				self.data["mode"] = f"custom_{m['custom_mode']}"
			return
		if self.target and sysid != self.target[0]:
			return
		if name == "GLOBAL_POSITION_INT":
			if m["lat"] == 0 and m["lon"] == 0:
				return  # no position estimate yet
			self.position_time = time.monotonic()
			self.data["position"] = {
				"lat": m["lat"] / 1e7, "lon": m["lon"] / 1e7,
				"alt_msl": m["alt"] / 1000, "alt_rel": m["relative_alt"] / 1000,
			}
			vn, ve, vd = m["vx"] / 100, m["vy"] / 100, m["vz"] / 100
			self.data["velocity"] = {"north": vn, "east": ve, "down": vd, "ground_speed": round(math.hypot(vn, ve), 2)}
			self.gps_heading = m["hdg"] / 100 if m["hdg"] != 65535 else None
		elif name == "GPS_RAW_INT":
			self.data["gps"] = {
				"fix": m["fix_type"], "fix_name": FIX_NAMES.get(m["fix_type"], str(m["fix_type"])),
				"sats": m["satellites_visible"], "hdop": m["eph"] / 100 if m["eph"] != 65535 else None,
			}
		elif name == "ATTITUDE":
			self.data["attitude"] = {k: round(math.degrees(m[k]), 2) for k in ("roll", "pitch", "yaw")}
			self.yaw_heading = round(math.degrees(m["yaw"]) % 360, 1)
		elif name == "SYS_STATUS":
			self.data["battery_v"] = m["voltage_battery"] / 1000

	def snapshot(self):
		now = time.monotonic()
		self.seq += 1
		hb_age = None if self.last_heartbeat is None else round(now - self.last_heartbeat, 2)
		msg = {
			"type": "telemetry", "drone": self.name, "seq": self.seq, "time": round(time.time(), 3),
			"fc_connected": self.fc_connected and hb_age is not None and hb_age < 3,
			"heartbeat_age": hb_age,
			"position": None, "velocity": None, "heading": None, "attitude": None, "gps": None,
			"armed": None, "mode": None, "battery_v": None,
		}
		msg.update(self.data)
		msg["heading"] = self.gps_heading if self.gps_heading is not None else self.yaw_heading
		if msg["position"] is not None:
			age = round(now - self.position_time, 2)
			msg["position"] = dict(msg["position"], age = age, stale = age > STALE_AFTER)
		return msg


# ---- MAVLink connection -----------------------------------------------------------
async def mavlink_loop(state, conn, stream_rate):
	kind, host, port = conn.split(":")
	port = int(port)
	while True:
		parser = MavlinkParser()
		try:
			if kind == "tcp":
				reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 5)
				send = lambda b: writer.write(b)
				log.info("connected to MAVLink at %s", conn)
				state.fc_connected = True
				requested_for = None
				seq = 0
				while True:
					data = await asyncio.wait_for(reader.read(4096), 10)
					if not data:
						raise ConnectionError("MAVLink connection closed")
					for name, sysid, compid, m in parser.feed(data):
						state.update(name, sysid, compid, m)
					# once we know the FC, ask it to stream telemetry on this link
					if state.target and requested_for != state.target:
						requested_for = state.target
						msgid, crc_extra = MSG_REQUEST_DATA_STREAM
						payload = struct.pack("<HBBBB", stream_rate, state.target[0], state.target[1], 0, 1)
						send(encode_v2(msgid, crc_extra, payload, seq, state.target[0], MAV_COMP_ID_ONBOARD_COMPUTER))
						seq += 1
						log.info("requested telemetry streams at %d Hz", stream_rate)
			elif kind == "udpin":
				loop = asyncio.get_running_loop()
				queue = asyncio.Queue()

				class Proto(asyncio.DatagramProtocol):
					def datagram_received(self, data, addr):
						queue.put_nowait(data)

				transport, _ = await loop.create_datagram_endpoint(Proto, local_addr = (host, port))
				log.info("listening for MAVLink on UDP %s:%d", host, port)
				state.fc_connected = True
				try:
					while True:
						data = await asyncio.wait_for(queue.get(), 10)
						for name, sysid, compid, m in parser.feed(data):
							state.update(name, sysid, compid, m)
				finally:
					transport.close()
			else:
				raise ValueError(f"unsupported connection {conn!r} (use tcp:HOST:PORT or udpin:HOST:PORT)")
		except (OSError, ConnectionError, asyncio.TimeoutError) as e:
			log.warning("MAVLink link problem (%s), retrying in 2 s", e or type(e).__name__)
		state.fc_connected = False
		await asyncio.sleep(2)


# ---- WebSocket / HTTP server --------------------------------------------------------
class Broadcaster:
	def __init__(self, state, rate):
		self.state = state
		self.period = 1.0 / rate
		self.clients = set()
		self.latest = None

	async def handle(self, reader, writer):
		peer = writer.get_extra_info("peername")
		try:
			request_line, headers = await asyncio.wait_for(ws_lite.read_http_head(reader), 5)
		except Exception:
			writer.close()
			return

		if headers.get("upgrade", "").lower() != "websocket":
			# plain HTTP: return the latest message once (for curl / browser testing)
			body = json.dumps(self.latest or self.state.snapshot(), indent = 2).encode()
			writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\n"
				+ f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
			await writer.drain()
			writer.close()
			return

		key = headers.get("sec-websocket-key")
		if not key:
			writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
			writer.close()
			return
		writer.write((
			"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
			f"Sec-WebSocket-Accept: {ws_lite.accept_key(key)}\r\n\r\n"
		).encode())
		await writer.drain()
		ws = ws_lite.WebSocket(reader, writer, is_client = False)
		self.clients.add(ws)
		log.info("client connected: %s (%d total)", peer, len(self.clients))
		try:
			while True:
				await ws.recv()  # we don't act on client messages; this just handles ping/close
		except ws_lite.WebSocketClosed:
			pass
		finally:
			self.clients.discard(ws)
			log.info("client left: %s (%d total)", peer, len(self.clients))

	async def broadcast_loop(self):
		while True:
			self.latest = self.state.snapshot()
			text = json.dumps(self.latest, separators = (",", ":"))
			for ws in list(self.clients):
				try:
					if ws.send_nowait(text) > 256 * 1024:  # client stopped reading
						self.clients.discard(ws)
						await ws.close()
				except Exception:
					self.clients.discard(ws)
			await asyncio.sleep(self.period)


async def main():
	p = argparse.ArgumentParser(description = __doc__.split("\n\n")[0])
	p.add_argument("--name", required = True, help = "this drone's name, e.g. megadrone or dexi")
	p.add_argument("--mavlink", default = "tcp:127.0.0.1:5760", help = "tcp:HOST:PORT (mavlink-router) or udpin:HOST:PORT")
	p.add_argument("--host", default = "0.0.0.0", help = "address to serve on (0.0.0.0 = all networks)")
	p.add_argument("--port", type = int, default = 8765)
	p.add_argument("--rate", type = float, default = 5.0, help = "messages per second to clients")
	p.add_argument("--stream-rate", type = int, default = 5, help = "Hz to request from the flight controller")
	args = p.parse_args()

	logging.basicConfig(level = logging.INFO, format = "%(asctime)s %(levelname)s %(message)s")
	state = DroneState(args.name)
	bc = Broadcaster(state, args.rate)
	server = await asyncio.start_server(bc.handle, args.host, args.port)
	log.info("serving %s telemetry on ws://%s:%d", args.name, args.host, args.port)
	await asyncio.gather(server.serve_forever(), bc.broadcast_loop(), mavlink_loop(state, args.mavlink, args.stream_rate))


if __name__ == "__main__":
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		pass
