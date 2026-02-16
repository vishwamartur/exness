"""
WebSocket Test Client
Connects to the bot's data stream and prints received messages.
"""
import asyncio
import websockets
import json

async def test_client():
    uri = "ws://localhost:8000/ws"
    print(f"Connecting to {uri}...")
    
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected! Waiting for data...")
            while True:
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=60.0)
                    data = json.loads(message)
                    print(f"\n[STREAM] {data.get('symbol', 'UNKNOWN')} "
                          f"Score:{data.get('score')} "
                          f"Dir:{data.get('direction')} "
                          f"Setup:{data.get('is_setup')}")
                    # print(f"Raw: {message[:100]}...") # Debug
                except asyncio.TimeoutError:
                    print("No data received for 60s")
                    break
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_client())
