import asyncio
from websockets.asyncio.server import serve

# This handler runs whenever a new client connects
async def echo_handler(websocket):
    print(f"Client connected: {websocket.remote_address}")
    try:
        # Loop to continuously receive messages from the client
        async for message in websocket:
            print(f"Received: {message}")
            
            # Send a response back to the client (Echo)
            response = f"Server received: {message}"
            await websocket.send(response)
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        print(f"Client disconnected: {websocket.remote_address}")

async def main():
    # Start the server on localhost, port 8765
    async with serve(echo_handler, "localhost", 8765) as server:
        print("WebSocket server running on ws://localhost:8765")
        await server.serve_forever() # Keeps the server running

if __name__ == "__main__":
    asyncio.run(main())
