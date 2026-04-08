"""
BroadcastService — WebSocket + REST state broadcaster.

Subscribes to: ALL events (wildcard)
Publishes: Nothing (terminal output service)

Replaces: push_update() function and _state management in stream_server.py
"""

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Dict, List

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService

logger = logging.getLogger("BroadcastService")


class BroadcastService(BaseService):
    """
    Listens to ALL events from the bus and:
    1. Maintains in-memory state for REST API snapshots
    2. Broadcasts events to WebSocket clients via the FastAPI server
    3. Provides backward compatibility with the old push_update() interface
    """

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)
        self._state: Dict = {
            "account": {},
            "positions": [],
            "scan_summary": {},
            "recent_trades": [],
            "events": [],
        }
        self._ws_loop = None  # FastAPI's event loop

    @property
    def name(self) -> str:
        return "BroadcastService"

    async def _setup(self):
        """Subscribe to ALL events."""
        self.bus.subscribe_all(self._on_any_event)

        # Start the FastAPI server in a background thread
        self._start_api_server()

    def _start_api_server(self):
        """Start the FastAPI/Uvicorn server in a daemon thread."""
        try:
            from api.stream_server import start_server, app
            import socket

            port = 8000
            for p in range(8000, 8010):
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    try:
                        s.bind(("0.0.0.0", p))
                        port = p
                        break
                    except OSError:
                        continue

            # Inject our state into stream_server's shared state
            from api import stream_server
            stream_server._state = self._state

            import uvicorn
            def run():
                uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

            t = threading.Thread(target=run, daemon=True)
            t.start()
            logger.info(f"API server started on port {port}")

        except Exception as e:
            logger.warning(f"Could not start API server: {e}")

    async def _on_any_event(self, event: Event):
        """Handle any event — update state and broadcast."""
        ev_type = event.type

        # Skip internal events
        if ev_type.startswith("__") or ev_type in (
            EventTypes.SERVICE_STARTED, EventTypes.SERVICE_STOPPED
        ):
            return

        # Update in-memory state
        self._update_state(event)

        # Broadcast to WebSocket clients
        await self._broadcast_ws(event)

    def _update_state(self, event: Event):
        """Update in-memory state based on event type."""
        ev_type = event.type
        payload = event.payload

        # Rolling event log (last 200)
        safe_event = {
            "type": ev_type,
            "source": event.source,
            "timestamp": event.timestamp,
        }
        # Only include serializable payload keys
        for k, v in payload.items():
            try:
                json.dumps(v)
                safe_event[k] = v
            except (TypeError, ValueError):
                safe_event[k] = str(v)

        self._state["events"].append(safe_event)
        if len(self._state["events"]) > 200:
            self._state["events"] = self._state["events"][-200:]

        if ev_type == EventTypes.ACCOUNT_UPDATE:
            self._state["account"].update(payload.get("account", payload))

        elif ev_type == EventTypes.POSITION_UPDATE:
            self._state["positions"] = payload.get("positions", [])

        elif ev_type in (EventTypes.SCAN_SUMMARY, EventTypes.SCAN_COMPLETE):
            self._state["scan_summary"] = {
                "symbols": payload.get("symbols", payload.get("status", {})),
                "timestamp": payload.get("timestamp", event.timestamp),
                "count": payload.get("count", 0),
            }

        elif ev_type in (EventTypes.TRADE_EXECUTED, EventTypes.TRADE_EXECUTION):
            trade = {k: v for k, v in payload.items() if k != "type"}
            self._state["recent_trades"].insert(0, trade)
            if len(self._state["recent_trades"]) > 50:
                self._state["recent_trades"] = self._state["recent_trades"][:50]

    async def _broadcast_ws(self, event: Event):
        """Broadcast event to WebSocket clients."""
        try:
            from api.stream_server import manager, _loop

            if not manager.active_connections:
                return

            # Build message
            msg = {
                "type": event.type,
                **event.payload,
                "timestamp": event.timestamp,
            }

            # Serialize safely
            def _serialize(obj):
                try:
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
                except ImportError:
                    pass
                return str(obj)

            json_msg = json.dumps(msg, default=_serialize)

            # Use stream_server's event loop if available
            if _loop and _loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._ws_broadcast_coro(json_msg), _loop
                )
            else:
                # Try direct broadcast if we're on the same loop
                await self._ws_broadcast_coro(json_msg)

        except Exception:
            pass  # Never let broadcasting crash the service

    async def _ws_broadcast_coro(self, json_msg: str):
        """Actual WebSocket broadcast coroutine."""
        try:
            from api.stream_server import manager
            dead = []
            for ws in manager.active_connections:
                try:
                    await ws.send_text(json_msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                manager.disconnect(ws)
        except Exception:
            pass

    @property
    def state(self) -> Dict:
        """Get current state snapshot."""
        return self._state
