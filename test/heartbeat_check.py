#!/usr/bin/env python3
"""Check that the flight controller is talking through mavlink-router.

Standard library only (works on the Pi without pymavlink installed).
Connects to mavlink-router's TCP port for 8 seconds and counts MAVLink2
HEARTBEAT messages per system ID. A healthy FC sends about 1 per second,
so expect output like  HEARTBEATS: {1: 8}

    python3 tools/heartbeat_check.py [host] [port]
"""
import socket
import sys
import time

host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
port = int(sys.argv[2]) if len(sys.argv) > 2 else 5760
duration = 8

s = socket.create_connection((host, port), timeout=1)
buf, hb, total, end = b"", {}, 0, time.time() + duration
while time.time() < end:
    try:
        d = s.recv(4096)
    except socket.timeout:
        continue
    if not d:
        break
    total += len(d)
    buf += d
    i = 0
    while i < len(buf) - 10:
        # MAVLink2 frame: 0xFD, payload len 9, ..., msgid 0 (HEARTBEAT)
        if buf[i] == 0xFD and buf[i + 1] == 9 and buf[i + 7:i + 10] == b"\x00\x00\x00":
            hb[buf[i + 5]] = hb.get(buf[i + 5], 0) + 1
        i += 1
    buf = buf[-10:]

print(f"received {total} bytes in {duration}s")
if hb:
    print(f"HEARTBEATS: {hb}")
elif total:
    print("NO heartbeats found -- data is arriving but garbled: check the baud rate")
else:
    print("NO data at all -- check FC power and wiring (Pi TX -> FC RX, Pi RX -> FC TX, GND)")
