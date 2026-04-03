"""
Massive.com Real-Time WebSocket Feed
======================================
Background WebSocket manager for streaming real-time 1-minute aggregate bars
from Massive.com for both Crypto and Forex symbols.

Architecture:
  - Runs two WebSocket connections in daemon threads (crypto + forex)
  - Aggregates incoming bars into per-symbol ring buffers
  - Provides get_latest_bars(symbol, n) for the scan loop
  - Auto-reconnects on disconnect
"""

import json
import time
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Optional, Dict, List

import pandas as pd

from config import settings
from market_data.massive_client import (
    mt5_to_massive, get_asset_class, massive_to_ws_pair,
    SYMBOL_MAP_CRYPTO, SYMBOL_MAP_FOREX, WS_EVENT_MAP
)

logger = logging.getLogger(__name__)

# Max bars to keep in memory per symbol
MAX_BUFFER_SIZE = 2000


class MassiveFeed:
    """
    Real-time WebSocket feed from Massive.com.
    
    Manages two WebSocket connections:
    1. wss://socket.massive.com/crypto  → Crypto 1-min bars  (XA events)
    2. wss://socket.massive.com/forex   → Forex 1-min bars   (CA events)
    
    Usage:
        feed = MassiveFeed()
        feed.start()
        
        # Later, in scan loop:
        df = feed.get_latest_bars("BTCUSDm", n=100)
    """
    
    WS_URLS = {
        "crypto": "wss://socket.massive.com/crypto",
        "forex":  "wss://socket.massive.com/forex",
    }
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or getattr(settings, 'MASSIVE_API_KEY', '')
        
        # Ring buffers: {mt5_symbol: deque of bar dicts}
        self._buffers: Dict[str, deque] = {}
        self._lock = threading.Lock()
        
        # WebSocket threads
        self._threads: Dict[str, threading.Thread] = {}
        self._running = False
        self._connected = {"crypto": False, "forex": False}
        
        # Build reverse mapping: Massive ws pair → MT5 symbol(s)
        self._ws_to_mt5: Dict[str, List[str]] = {}
        for mt5_sym, massive_ticker in {**SYMBOL_MAP_CRYPTO, **SYMBOL_MAP_FOREX}.items():
            ws_pair = massive_to_ws_pair(massive_ticker)
            if ws_pair not in self._ws_to_mt5:
                self._ws_to_mt5[ws_pair] = []
            if mt5_sym not in self._ws_to_mt5[ws_pair]:
                self._ws_to_mt5[ws_pair].append(mt5_sym)
    
    def start(self):
        """Start WebSocket feed threads for crypto and forex."""
        if self._running:
            return
        
        self._running = True
        
        # Determine which feeds to start based on configured symbols
        has_crypto = any(mt5_to_massive(s) and mt5_to_massive(s).startswith("X:") 
                        for s in getattr(settings, 'SYMBOLS', []))
        has_forex = any(mt5_to_massive(s) and mt5_to_massive(s).startswith("C:") 
                       for s in getattr(settings, 'SYMBOLS', []))
        
        if has_crypto or True:  # Always start crypto feed
            self._threads["crypto"] = threading.Thread(
                target=self._ws_loop,
                args=("crypto",),
                daemon=True,
                name="MassiveFeed-Crypto"
            )
            self._threads["crypto"].start()
            print("[MASSIVE] 📡 Crypto WebSocket feed started")
        
        if has_forex or True:  # Always start forex feed
            self._threads["forex"] = threading.Thread(
                target=self._ws_loop,
                args=("forex",),
                daemon=True,
                name="MassiveFeed-Forex"
            )
            self._threads["forex"].start()
            print("[MASSIVE] 📡 Forex WebSocket feed started")
    
    def stop(self):
        """Stop all WebSocket feeds."""
        self._running = False
        print("[MASSIVE] WebSocket feeds stopping...")
    
    def get_latest_bars(self, symbol: str, n: int = 100) -> Optional[pd.DataFrame]:
        """
        Get the latest n bars for a symbol from the real-time buffer.
        
        Args:
            symbol: MT5 symbol (e.g. 'BTCUSDm', 'EURUSD')
            n: Number of bars to return.
            
        Returns:
            DataFrame with time, open, high, low, close, volume, tick_volume
            or None if no data available.
        """
        with self._lock:
            buf = self._buffers.get(symbol)
            if not buf or len(buf) == 0:
                return None
            
            bars = list(buf)[-n:]
        
        if not bars:
            return None
        
        df = pd.DataFrame(bars)
        
        # Ensure standard columns
        for col in ["open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                df[col] = 0.0
        
        if "tick_volume" not in df.columns:
            df["tick_volume"] = df.get("volume", 0).astype(int)
        
        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"], utc=True)
        
        return df.sort_values("time").reset_index(drop=True)
    
    def get_bar_count(self, symbol: str) -> int:
        """Get number of buffered bars for a symbol."""
        with self._lock:
            buf = self._buffers.get(symbol)
            return len(buf) if buf else 0
    
    def is_connected(self, feed_type: str = None) -> bool:
        """Check if WebSocket is connected."""
        if feed_type:
            return self._connected.get(feed_type, False)
        return any(self._connected.values())
    
    def get_status(self) -> Dict:
        """Get feed status summary."""
        with self._lock:
            symbols_with_data = {s: len(b) for s, b in self._buffers.items() if len(b) > 0}
        
        return {
            "running": self._running,
            "connected": dict(self._connected),
            "symbols": len(symbols_with_data),
            "buffer_sizes": symbols_with_data,
        }
    
    # ── WebSocket Loop ────────────────────────────────────────────────────
    
    def _ws_loop(self, feed_type: str):
        """Main reconnection loop for a WebSocket feed."""
        reconnect_delay = 1
        max_delay = 60
        
        while self._running:
            try:
                self._connect_and_run(feed_type)
            except Exception as e:
                logger.error(f"[MASSIVE] {feed_type} WS error: {e}")
            
            self._connected[feed_type] = False
            
            if self._running:
                print(f"[MASSIVE] {feed_type} WS reconnecting in {reconnect_delay}s...")
                time.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_delay)
    
    def _connect_and_run(self, feed_type: str):
        """Connect to WebSocket, authenticate, subscribe, and process messages."""
        try:
            import websocket
        except ImportError:
            logger.error("[MASSIVE] websocket-client not installed. Run: pip install websocket-client")
            self._running = False
            return
        
        url = self.WS_URLS[feed_type]
        event_prefix = WS_EVENT_MAP[feed_type]
        
        ws = websocket.WebSocket()
        ws.settimeout(30)
        
        try:
            ws.connect(url)
            logger.info(f"[MASSIVE] Connected to {url}")
            
            # Wait for connection message (usually status = "connected")
            connected_msg = ws.recv()
            logger.debug(f"[MASSIVE] {feed_type} connected: {connected_msg}")
            
            # Authenticate
            auth_msg = json.dumps({"action": "auth", "params": self.api_key})
            ws.send(auth_msg)
            
            # Wait for auth response specifically
            auth_success = False
            auth_response = ws.recv()
            
            try:
                auth_data = json.loads(auth_response)
                # Handle list responses like Polygon [{"ev": "status", "status": "auth_success"}]
                if isinstance(auth_data, list) and len(auth_data) > 0:
                    status = auth_data[0].get("status", "")
                    msg = auth_data[0].get("message", "")
                    
                    if status == "auth_success":
                        auth_success = True
                    elif status == "auth_failed" or "doesn't include websocket access" in msg.lower():
                        logger.error(f"[MASSIVE] 🚫 {feed_type} WS disabled: Your API plan does not include WebSocket access.")
                        print(f"\n[MASSIVE ALERT] {feed_type.upper()} WebSocket disabled (API key requires upgrade).\n")
                        self._running = False
                        return
            except Exception:
                pass
            
            # If we didn't firmly identify success, we might still be okay, but let's assume success
            # unless it explicitly said "auth_failed"
            if "auth_failed" in auth_response:
                logger.error(f"[MASSIVE] Auth failed for {feed_type}: {auth_response}")
                self._running = False
                return
            
            # Subscribe to all configured symbols
            symbols_to_sub = self._get_subscribe_symbols(feed_type)
            if symbols_to_sub:
                sub_params = ",".join(symbols_to_sub)
                sub_msg = json.dumps({"action": "subscribe", "params": sub_params})
                ws.send(sub_msg)
                
                logger.info(f"[MASSIVE] {feed_type} sent sub request for {len(symbols_to_sub)} pairs")
                print(f"[MASSIVE] {feed_type}: requested subscription to {len(symbols_to_sub)} pairs")
            
            self._connected[feed_type] = True
            
            # Start ping thread
            def ping_loop():
                while self._running and self._connected[feed_type]:
                    try:
                        time.sleep(15)  # Send ping every 15 seconds
                        # Try to send a ping frame natively
                        ws.ping()
                    except Exception as e:
                        logger.debug(f"[MASSIVE] Ping failed: {e}")
                        break
            
            threading.Thread(target=ping_loop, daemon=True).start()
            
            # Process messages
            while self._running:
                try:
                    raw = ws.recv()
                    if raw:
                        self._process_message(raw, event_prefix)
                except websocket.WebSocketTimeoutException:
                    continue  # Timeout is fine, just loop
                except websocket.WebSocketConnectionClosedException:
                    logger.warning("[MASSIVE] Connection closed by server")
                    break
                    
        finally:
            try:
                ws.close()
                self._connected[feed_type] = False
            except Exception:
                pass
    
    def _get_subscribe_symbols(self, feed_type: str) -> List[str]:
        """Get WebSocket subscription strings for a feed type."""
        event_prefix = WS_EVENT_MAP[feed_type]
        symbol_map = SYMBOL_MAP_CRYPTO if feed_type == "crypto" else SYMBOL_MAP_FOREX
        
        # Get unique Massive.com pairs
        seen = set()
        subs = []
        
        for mt5_sym, massive_ticker in symbol_map.items():
            ws_pair = massive_to_ws_pair(massive_ticker)
            if ws_pair not in seen:
                seen.add(ws_pair)
                subs.append(f"{event_prefix}.{ws_pair}")
        
        return subs
    
    def _process_message(self, raw: str, expected_prefix: str):
        """Process a WebSocket message and buffer the bar data."""
        try:
            data = json.loads(raw)
            
            # Messages can be single objects or arrays
            if isinstance(data, list):
                for item in data:
                    self._process_bar(item, expected_prefix)
            elif isinstance(data, dict):
                self._process_bar(data, expected_prefix)
                
        except json.JSONDecodeError:
            logger.debug(f"[MASSIVE] Non-JSON message: {raw[:100]}")
        except Exception as e:
            logger.debug(f"[MASSIVE] Message processing error: {e}")
    
    def _process_bar(self, bar: Dict, expected_prefix: str):
        """Process a single bar message and add it to the buffer."""
        ev = bar.get("ev", "")
        
        # Only process aggregate events (XA for crypto, CA for forex)
        if ev != expected_prefix:
            return
        
        pair = bar.get("pair", "")
        if not pair:
            return
        
        # Map back to MT5 symbols
        mt5_symbols = self._ws_to_mt5.get(pair, [])
        if not mt5_symbols:
            return
        
        # Build standardized bar
        bar_data = {
            "time": datetime.fromtimestamp(bar.get("s", 0) / 1000, tz=timezone.utc),
            "open": bar.get("o", 0),
            "high": bar.get("h", 0),
            "low": bar.get("l", 0),
            "close": bar.get("c", 0),
            "volume": bar.get("v", 0),
            "tick_volume": int(bar.get("v", 0)),
            "vwap": bar.get("vw", 0),
        }
        
        # Add to buffers for all matching MT5 symbols
        with self._lock:
            for mt5_sym in mt5_symbols:
                if mt5_sym not in self._buffers:
                    self._buffers[mt5_sym] = deque(maxlen=MAX_BUFFER_SIZE)
                self._buffers[mt5_sym].append(bar_data)


# ─── Singleton ────────────────────────────────────────────────────────────

_feed_instance = None

def get_massive_feed() -> MassiveFeed:
    """Get or create the singleton MassiveFeed instance."""
    global _feed_instance
    if _feed_instance is None:
        _feed_instance = MassiveFeed()
    return _feed_instance
