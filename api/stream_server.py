"""
WebSocket Stream Server (FastAPI)
Provides real-time data feed for external dashboards.
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn
import asyncio
import json
import threading
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("StreamServer")

app = FastAPI()

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Client connected. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"Client disconnected. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Sends message to all connected clients."""
        if not self.active_connections:
            return
        
        json_msg = json.dumps(message)
        for connection in self.active_connections:
            try:
                await connection.send_text(json_msg)
            except Exception as e:
                logger.error(f"Error sending to client: {e}")
                self.disconnect(connection)

manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep connection alive, maybe wait for commands (optional)
            data = await websocket.receive_text()
            # Echo or process commands if needed
            await websocket.send_text(f"Command received: {data}")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket)

@app.get("/")
def read_root():
    return {"status": "ok", "service": "MT5 Stream Server"}

# Global instance for thread-safe access from main bot
_server_thread = None
_loop = None

def start_server(host="0.0.0.0", port=8000):
    """Starts the Uvicorn server in a separate thread."""
    def run():
        # Uvicorn run blocking
        uvicorn.run(app, host=host, port=port, log_level="warning")
    
    global _server_thread
    _server_thread = threading.Thread(target=run, daemon=True)
    _server_thread.start()
    logger.info(f"Stream Server started on ws://{host}:{port}/ws")

def broadcast_data(data: dict):
    """
    Called by the main bot to push data. 
    Since Uvicorn runs in its own loop, we need to schedule the broadcast there.
    However, purely async broadcast from sync context is tricky with Uvicorn in thread.
    
    Simplified approach: 
    We just use the manager's list. But sending 'await' requires an event loop.
    The run_in_executor or similar is needed.
    
    Actually, simpler implementation for this context:
    FastAPI/Uvicorn controls the loop. We can't easily inject from outside thread 
    unless we have reference to that loop.
    
    BETTER APPROACH:
    The bot can POST functionality? No, overhead.
    We need to access the loop uvicorn uses? Hard.
    
    Workaround:
    We'll use a queue or just rely on the fact that 'manager' is global,
    but we need to run_coroutine_threadsafe.
    
    Let's capture the loop on startup event?
    """
    # For now, we will print a warning because ensuring thread-safety 
    # between main sync bot and async fastapi thread is complex without a queue.
    # But let's try to grab the loop.
    pass

# Improved Broadcast Logic
# We need to capture the loop running the server.
@app.on_event("startup")
async def startup_event():
    global _loop
    _loop = asyncio.get_running_loop()

def push_update(data: dict):
    """Thread-safe push update."""
    global _loop
    if _loop and _loop.is_running():
        asyncio.run_coroutine_threadsafe(manager.broadcast(data), _loop)
