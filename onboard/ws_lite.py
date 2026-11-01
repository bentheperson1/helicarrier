"""Minimal WebSocket (RFC 6455) helpers using only the Python standard library.

The drone Pis can't always reach the internet to pip install packages, so this
avoids any dependency. Supports what we need: text/binary messages, ping/pong,
close, fragmented messages, and both the server and client side of the handshake.
"""
import asyncio
import base64
import hashlib
import os
import struct
from urllib.parse import urlparse

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
MAX_MESSAGE = 1 << 20  # 1 MB is far more than any telemetry message


class WebSocketClosed(Exception):
	pass


def accept_key(key):
	return base64.b64encode(hashlib.sha1(key.encode() + GUID).digest()).decode()


def encode_frame(payload, opcode = OP_TEXT, mask = False):
	if isinstance(payload, str):
		payload = payload.encode()
	header = bytearray([0x80 | opcode])
	mask_bit = 0x80 if mask else 0
	n = len(payload)
	if n < 126:
		header.append(mask_bit | n)
	elif n < 65536:
		header.append(mask_bit | 126)
		header += struct.pack(">H", n)
	else:
		header.append(mask_bit | 127)
		header += struct.pack(">Q", n)
	if mask:  # clients must mask what they send (RFC 6455 5.3)
		key = os.urandom(4)
		header += key
		payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
	return bytes(header) + payload


async def read_http_head(reader, limit = 8192):
	data = await reader.readuntil(b"\r\n\r\n")
	if len(data) > limit:
		raise ValueError("HTTP header too large")
	lines = data.decode("latin-1").split("\r\n")
	headers = {}
	for line in lines[1:]:
		if ":" in line:
			k, v = line.split(":", 1)
			headers[k.strip().lower()] = v.strip()
	return lines[0], headers


class WebSocket:
	def __init__(self, reader, writer, is_client):
		self.reader = reader
		self.writer = writer
		self.is_client = is_client
		self.closed = False

	async def send(self, text):
		if self.closed:
			raise WebSocketClosed()
		self.writer.write(encode_frame(text, OP_TEXT, mask = self.is_client))
		await self.writer.drain()

	def send_nowait(self, text):
		"""Queue a message without waiting (server broadcast). Returns bytes still buffered."""
		self.writer.write(encode_frame(text, OP_TEXT, mask = self.is_client))
		return self.writer.transport.get_write_buffer_size()

	async def _read_frame(self):
		b1, b2 = await self.reader.readexactly(2)
		fin, opcode = b1 & 0x80, b1 & 0x0F
		masked, n = b2 & 0x80, b2 & 0x7F
		if n == 126:
			n = struct.unpack(">H", await self.reader.readexactly(2))[0]
		elif n == 127:
			n = struct.unpack(">Q", await self.reader.readexactly(8))[0]
		if n > MAX_MESSAGE:
			raise WebSocketClosed("message too large")
		key = await self.reader.readexactly(4) if masked else None
		payload = await self.reader.readexactly(n)
		if key:
			payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
		return fin, opcode, payload

	async def recv(self):
		"""Next text/binary message (str for text, bytes for binary). Handles ping/close."""
		message, message_op = b"", None
		while True:
			try:
				fin, opcode, payload = await self._read_frame()
			except (asyncio.IncompleteReadError, ConnectionError) as e:
				self.closed = True
				raise WebSocketClosed() from e
			if opcode == OP_PING:
				self.writer.write(encode_frame(payload, OP_PONG, mask = self.is_client))
				continue
			if opcode == OP_PONG:
				continue
			if opcode == OP_CLOSE:
				await self.close()
				raise WebSocketClosed()
			if opcode in (OP_TEXT, OP_BINARY):
				message_op, message = opcode, payload
			elif opcode == OP_CONT:
				message += payload
			if len(message) > MAX_MESSAGE:
				raise WebSocketClosed("message too large")
			if fin:
				return message.decode() if message_op == OP_TEXT else message

	async def close(self):
		if self.closed:
			return
		self.closed = True
		try:
			self.writer.write(encode_frame(b"", OP_CLOSE, mask = self.is_client))
			await self.writer.drain()
			self.writer.close()
		except Exception:
			pass


async def connect(url, timeout = 5.0):
	"""Client side: connect to ws://host:port/path and return a WebSocket."""
	u = urlparse(url)
	if u.scheme != "ws":
		raise ValueError("only ws:// URLs are supported")
	host, port, path = u.hostname, u.port or 80, u.path or "/"
	reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
	key = base64.b64encode(os.urandom(16)).decode()
	writer.write((
		f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
		f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
	).encode())
	await writer.drain()
	status, headers = await asyncio.wait_for(read_http_head(reader), timeout)
	if " 101 " not in status + " " or headers.get("sec-websocket-accept") != accept_key(key):
		writer.close()
		raise ConnectionError(f"WebSocket handshake failed: {status}")
	return WebSocket(reader, writer, is_client = True)
