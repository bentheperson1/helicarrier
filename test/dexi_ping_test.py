import asyncio
import json
import websockets

ros_url = "wss://ros-gaa-1793260697.dexisim.io"

param_service = "/mission_node/set_parameters"
param_service_type = "rcl_interfaces/srv/SetParameters"
param_type_integer = 2
reply_timeout = 5.0


async def set_phase(ws, phase, note):
	request_id = f"set_phase_{phase}"
	request = {
		"op": "call_service",
		"service": param_service,
		"type": param_service_type,
		"args": {
			"parameters": [
				{
					"name": "mission_phase",
					"value": {"type": param_type_integer, "integer_value": phase}
				}
			]
		},
		"id": request_id
	}
	
	await ws.send(json.dumps(request))
	
	try:
		while True:
			reply = json.loads(await asyncio.wait_for(ws.recv(), reply_timeout))
			
			if reply.get("id") == request_id:
				break
	except asyncio.TimeoutError:
		print("no reply from drone (timed out)")
		return
	
	if not reply.get("result"):
		print(f"call failed: {reply.get('values')}")
		return
	
	result = reply.get("values", {}).get("results", [{}])[0]
	print(f"accepted: {result.get('successful')} | {result.get('reason')}")


async def main():
	async with websockets.connect(ros_url) as ws:
		print("connected to sim")
		print("type a phase number (1-6), e.g. '1'. 'q' to quit.")
		
		while True:
			line = (await asyncio.to_thread(input, "> ")).strip()
			
			if line == "q":
				break
			
			parts = line.split(" ", 1)
			
			if not parts[0].isdigit():
				print("first word must be a phase number")
				continue
			
			phase = int(parts[0])
			note = parts[1] if len(parts) > 1 else ""
			
			await set_phase(ws, phase, note)


asyncio.run(main())