import asyncio
import json
import websockets

ros_url = "wss://ros-adg-1794420199.dexisim.io"

param_service = "/mission_node/set_parameters"
param_service_type = "rcl_interfaces/srv/SetParameters"
param_type_integer = 2
reply_timeout = 5.0


async def call(ws, service, service_type, args):
	await ws.send(json.dumps({"op": "call_service", "service": service, "type": service_type, "args": args, "id": service}))

	try:
		while True:
			reply = json.loads(await asyncio.wait_for(ws.recv(), reply_timeout))

			if reply.get("id") == service:
				return reply.get("values", {}) if reply.get("result") else print(f"call failed: {reply.get('values')}")
	except asyncio.TimeoutError:
		print("no reply from drone (timed out)")


async def set_phase(ws, phase):
	param = {"name": "mission_phase", "value": {"type": 2, "integer_value": phase}}
	values = await call(ws, "/mission_node/set_parameters", "rcl_interfaces/srv/SetParameters", {"parameters": [param]})

	if values:
		result = values["results"][0]
		print(f"accepted: {result.get('successful')} | {result.get('reason')}")


async def go(ws):
	values = await call(ws, "/mission_node/go", "std_srvs/srv/Trigger", {})

	if values:
		print(values.get("message"))


async def main():
	async with websockets.connect(ros_url) as ws:
		print("connected to sim")
		print("'g' to continue past a gate, a phase number (0-11) to jump, 'q' to quit")

		while True:
			line = (await asyncio.to_thread(input, "> ")).strip()

			if line == "q":
				break

			if line == "g":
				await go(ws)
			elif line.isdigit():
				await set_phase(ws, int(line))
			else:
				print("type 'g', a phase number, or 'q'")


asyncio.run(main())