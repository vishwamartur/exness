"""
WebSocket Stream Server (FastAPI) — Upgraded for React Dashboard
Provides real-time WebSocket feed + REST endpoints for the React dashboard.
"""
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import asyncio
import json
import threading
import logging
from datetime import datetime, timezone

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("StreamServer")

app = FastAPI(title="MT5 Bot API")

# Allow React dev server CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── In-memory State (updated by push_update) ────────────────────────────────
_state = {
    "account": {},
    "positions": [],
    "scan_summary": {},
    "recent_trades": [],   # last 50 trade executions
    "events": [],          # last 200 raw events
}


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        # Send current state snapshot on connect
        await websocket.send_text(json.dumps({"type": "STATE_SNAPSHOT", "data": _state}))

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        if not self.active_connections:
            return
        json_msg = json.dumps(message, default=str)
        dead = []
        for ws in self.active_connections:
            try:
                await ws.send_text(json_msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


# ─── WebSocket Endpoint ───────────────────────────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep alive
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


# ─── REST Endpoints ───────────────────────────────────────────────────────────
@app.get("/")
def read_root():
    return {"status": "ok", "service": "MT5 Stream Server", "version": "2.0"}

@app.get("/api/account")
def get_account():
    """Live account info directly from MT5."""
    try:
        import MetaTrader5 as mt5
        acct = mt5.account_info()
        if acct:
            return {
                "balance":  acct.balance,
                "equity":   acct.equity,
                "profit":   acct.profit,
                "currency": acct.currency,
                "leverage": acct.leverage,
                "day_pl":   round(acct.equity - acct.balance, 2),
            }
    except Exception:
        pass
    return _state["account"]

@app.get("/api/positions")
def get_positions():
    """Live open positions directly from MT5."""
    try:
        import MetaTrader5 as mt5
        raw = mt5.positions_get() or []
        return [
            {
                "ticket":        p.ticket,
                "symbol":        p.symbol,
                "type":          p.type,
                "direction":     "BUY" if p.type == 0 else "SELL",
                "volume":        p.volume,
                "entry_price":   p.price_open,
                "price_current": p.price_current,
                "sl_price":      p.sl,
                "tp_price":      p.tp,
                "profit":        p.profit,
            }
            for p in raw
        ]
    except Exception:
        pass
    return _state["positions"]

@app.get("/api/trades")
def get_trades():
    return _state["recent_trades"]

@app.get("/api/scan")
def get_scan():
    return _state["scan_summary"]

@app.get("/api/state")
def get_state():
    return _state


# ─── Server Startup ───────────────────────────────────────────────────────────
_loop = None

@app.on_event("startup")
async def startup_event():
    global _loop
    _loop = asyncio.get_running_loop()

_server_thread = None

def start_server(host="0.0.0.0", base_port=8000):
    """Starts Uvicorn in a background thread, auto-picks an open port."""
    import socket
    port = base_port
    for p in range(base_port, base_port + 10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, p))
                port = p
                break
            except OSError:
                continue

    def run(p):
        uvicorn.run(app, host=host, port=p, log_level="warning")

    global _server_thread
    _server_thread = threading.Thread(target=run, args=(port,), daemon=True)
    _server_thread.start()
    print(f"[API] Stream server → http://{host}:{port}  ws://{host}:{port}/ws")
    return port


# ─── Push Update (called by bot) ─────────────────────────────────────────────
def push_update(data: dict):
    """Thread-safe: update state + broadcast to all WebSocket clients."""
    global _loop
    _update_state(data)
    if _loop and _loop.is_running():
        asyncio.run_coroutine_threadsafe(manager.broadcast(data), _loop)


def _update_state(data: dict):
    """Update in-memory state based on event type."""
    ev_type = data.get("type", "")

    # Rolling event log (last 200)
    _state["events"].append(data)
    if len(_state["events"]) > 200:
        _state["events"] = _state["events"][-200:]

    if ev_type == "ACCOUNT_UPDATE":
        _state["account"].update(data.get("account", {}))

    elif ev_type == "POSITION_UPDATE":
        _state["positions"] = data.get("positions", [])

    elif ev_type == "SCAN_SUMMARY":
        _state["scan_summary"] = {
            "symbols": data.get("symbols", {}),
            "timestamp": data.get("timestamp"),
            "count": data.get("count", 0),
        }

    elif ev_type == "TRADE_EXECUTION":
        trade = {k: v for k, v in data.items() if k != "type"}
        _state["recent_trades"].insert(0, trade)
        if len(_state["recent_trades"]) > 50:
            _state["recent_trades"] = _state["recent_trades"][:50]
