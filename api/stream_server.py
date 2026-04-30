"""
WebSocket Stream Server (FastAPI) - Upgraded for React Dashboard
Provides real-time WebSocket feed + REST endpoints for the React dashboard.
"""
from datetime import date, datetime, timezone

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn
import asyncio
import json
import threading
import logging

from utils.trade_journal import TradeJournal
from strategy.power_of_stocks_backtester import BacktestSettings, run_backtest, search_symbols

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


class BacktestRequest(BaseModel):
    symbol: str = Field(..., min_length=1)
    strategy: str = Field(default="ema5_breakout")
    start_date: date
    end_date: date
    timeframe: str = Field(default="M5")
    timezone_name: str = Field(default="Asia/Kolkata")
    reward_risk: float = Field(default=3.0, gt=0)
    entry_window_bars: int = Field(default=3, ge=1, le=20)
    initial_capital: float = Field(default=100000.0, gt=0)
    risk_per_trade: float = Field(default=1000.0, gt=0)
    session_start: str | None = Field(default="09:20")
    session_end: str | None = Field(default="15:15")
    exclude_start: str | None = Field(default=None)
    exclude_end: str | None = Field(default=None)
    square_off_time: str | None = Field(default="15:20")
    enter_on_close: bool = Field(default=False)
    allow_long: bool = Field(default=True)
    allow_short: bool = Field(default=True)
    ema_period: int = Field(default=5, ge=2, le=50)
    max_trades_per_day: int = Field(default=10, ge=1, le=100)
    traffic_light_max_range: float | None = Field(default=None, gt=0)
    same_bar_exit_priority: str = Field(default="stop")


def _build_backtest_settings(payload: BacktestRequest) -> BacktestSettings:
    return BacktestSettings(
        symbol=payload.symbol.strip(),
        strategy=payload.strategy,
        start_date=payload.start_date,
        end_date=payload.end_date,
        timeframe=payload.timeframe,
        timezone_name=payload.timezone_name,
        reward_risk=payload.reward_risk,
        entry_window_bars=payload.entry_window_bars,
        initial_capital=payload.initial_capital,
        risk_per_trade=payload.risk_per_trade,
        session_start=payload.session_start,
        session_end=payload.session_end,
        exclude_start=payload.exclude_start,
        exclude_end=payload.exclude_end,
        square_off_time=payload.square_off_time,
        enter_on_close=payload.enter_on_close,
        allow_long=payload.allow_long,
        allow_short=payload.allow_short,
        ema_period=payload.ema_period,
        max_trades_per_day=payload.max_trades_per_day,
        traffic_light_max_range=payload.traffic_light_max_range,
        same_bar_exit_priority=payload.same_bar_exit_priority,
    )


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    def _serialize(self, obj):
        """Custom JSON serializer for objects not serializable by default."""
        import pandas as pd
        import numpy as np
        
        if isinstance(obj, pd.Series):
            return obj.tolist()
        elif isinstance(obj, pd.DataFrame):
            return obj.to_dict('records')
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, (np.integer, np.floating)):
            return float(obj)
        elif hasattr(obj, '__dict__'):
            return str(obj)
        return str(obj)

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        # Send current state snapshot on connect
        await websocket.send_text(json.dumps({"type": "STATE_SNAPSHOT", "data": _state}, default=self._serialize))

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        if not self.active_connections:
            return
        json_msg = json.dumps(message, default=self._serialize)
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


@app.get("/api/backtest/strategies")
def get_backtest_strategies():
    return {
        "strategies": [
            {
                "id": "ema5_breakout",
                "name": "5 EMA Breakout",
                "description": "Alert candle fully away from the 5 EMA, entry on the break of that candle within the next N candles.",
                "defaults": {
                    "timeframe": "M5",
                    "reward_risk": 3.0,
                    "entry_window_bars": 3,
                    "session_start": None,
                    "session_end": None,
                    "square_off_time": None,
                    "enter_on_close": False,
                    "ema_period": 5,
                },
            },
            {
                "id": "traffic_light",
                "name": "Traffic Light",
                "description": "Breakout of the high or low of a two-candle red/green contrast range within the configured entry window.",
                "defaults": {
                    "timeframe": "M5",
                    "reward_risk": 3.0,
                    "entry_window_bars": 3,
                    "session_start": None,
                    "session_end": None,
                    "square_off_time": None,
                    "enter_on_close": False,
                    "traffic_light_max_range": None,
                },
            },
        ],
        "presets": [
            {"label": "XAUUSD Search", "query": "xauusd"},
            {"label": "EURUSD Search", "query": "eurusd"},
        ],
    }


@app.get("/api/backtest/symbols")
def get_backtest_symbols(query: str = "", limit: int = 20):
    try:
        return {"symbols": search_symbols(query=query, limit=min(limit, 50))}
    except Exception as exc:
        logger.warning("Backtest symbol search failed: %s", exc)
        raise HTTPException(status_code=503, detail=f"Unable to load MT5 symbols: {exc}") from exc


@app.post("/api/backtest/run")
def run_backtest_route(payload: BacktestRequest):
    try:
        settings_obj = _build_backtest_settings(payload)
        return run_backtest(settings_obj)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Backtest run failed")
        raise HTTPException(status_code=500, detail=f"Backtest failed: {exc}") from exc

@app.get("/api/journal/daily")
def get_journal_daily():
    journal = TradeJournal()
    stats = journal.get_daily_stats()
    return stats if stats else {"total": 0, "wins": 0, "losses": 0, "total_profit": 0, "win_rate": 0, "avg_rr": 0}

@app.get("/api/journal/confluence")
def get_journal_confluence(limit: int = 200):
    journal = TradeJournal()
    return journal.get_confluence_analysis(last_n_trades=limit)

@app.get("/api/journal/trades")
def get_historical_trades(limit: int = 50):
    journal = TradeJournal()
    import sqlite3
    conn = sqlite3.connect(journal.db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ticket, symbol, direction, lot_size, entry_price, exit_price, profit, outcome, entry_time 
        FROM trades 
        ORDER BY entry_time DESC 
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            "ticket": r[0],
            "symbol": r[1],
            "direction": r[2],
            "lot_size": r[3],
            "entry_price": r[4],
            "exit_price": r[5],
            "profit": r[6],
            "outcome": r[7],
            "entry_time": r[8]
        }
        for r in rows
    ]

@app.get("/api/quote")
def get_quote(symbol: str = "XAUUSDm"):
    """Live quote for synthetic orderbook."""
    try:
        import MetaTrader5 as mt5
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            return {
                "symbol": symbol,
                "bid": tick.bid,
                "ask": tick.ask,
                "time": tick.time
            }
    except Exception:
        pass
    # Basic fallback if mt5 fails or symbol not found
    return {"symbol": symbol, "bid": 2345.50, "ask": 2345.80, "time": 0}

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
    print(f"[API] Stream server -> http://{host}:{port}  ws://{host}:{port}/ws")
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
