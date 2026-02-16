
import asyncio
import websockets
import json
import sys

async def test_client():
    uri = "ws://localhost:8000/ws"
    print(f"Connecting to {uri}...")
    
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected! Waiting for data...")
            
            # Wait for 3 messages or 20 seconds
            for _ in range(5):
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=20.0)
                    data = json.loads(message)
                    print(f"[CLIENT] Received: {data.get('type')} from {data.get('symbol')}")
                except asyncio.TimeoutError:
                    print("Timeout.")
                    break
                    
    except Exception as e:
        print(f"Connection failed: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(test_client())
    except KeyboardInterrupt:
        pass
