"""
CoordinatorService â€” Session-Regime-Aware Orchestrator.

Consumes session regime signals and liquidity sweep signals instead
of generic ML/confluence scoring. Applies macro filter as a trade gate.

Event Flow:
  SCAN_START â†’ MARKET_DATA_READY â†’ SESSION_REGIME_UPDATE + MACRO_FILTER_UPDATE
  â†’ SESSION_TRADE_SIGNAL / SWEEP_TRADE_SIGNAL â†’ TRADE_CANDIDATE
  â†’ TRADE_APPROVED â†’ TRADE_EXECUTED
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict

import MetaTrader5 as mt5

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from core.mt5_gateway import MT5Gateway
from config import settings
from utils.news_filter import is_news_blackout, get_active_events

logger = logging.getLogger("CoordinatorService")


class CoordinatorService(BaseService):
    """
    Session-regime-aware orchestrator. Collects session strategy signals
    and liquidity sweep signals, applies macro filter, and produces
    TRADE_CANDIDATE events.
    """

    def __init__(self, event_bus: EventBus, gateway: MT5Gateway):
        super().__init__(event_bus)
        self.gateway = gateway

        # Signal aggregation buffers (cleared each cycle)
        self._session_signals: Dict[str, dict] = {}
        self._sweep_signals: Dict[str, dict] = {}
        self._macro_filter: dict = {}
        self._session_regime: dict = {}

        # News trading state (preserved from v3)
        self._news_window_symbols: set = set()
        self._news_event_info: Dict[str, dict] = {}

        # Scan state
        self._scan_count = 0
        self._daily_trade_count = 0
        self._last_reset_date = datetime.now(timezone.utc).date()
        self._last_trade_time: Dict[str, float] = {}
        self._expected_symbols = 0

    @property
    def name(self) -> str:
        return "CoordinatorService"

    async def _setup(self):
        # Session regime signals
        self.bus.subscribe(EventTypes.SESSION_TRADE_SIGNAL, self._on_session_signal)
        self.bus.subscribe(EventTypes.SWEEP_TRADE_SIGNAL, self._on_sweep_signal)
        self.bus.subscribe(EventTypes.MACRO_FILTER_UPDATE, self._on_macro_filter)
        self.bus.subscribe(EventTypes.SESSION_REGIME_UPDATE, self._on_session_regime)
        self.bus.subscribe(EventTypes.FLAT_ALL_POSITIONS, self._on_flat_all)
        self.bus.subscribe(EventTypes.TRADE_EXECUTED, self._on_trade_executed)
        self.bus.subscribe(EventTypes.NEWS_TRADE_SIGNAL, self._on_news_trade_signal)

    async def _run_loop(self):
        """Main scan loop."""
        while self._running:
            await self._run_scan_cycle()
            sleep_time = max(1, settings.COOLDOWN_SECONDS)
            logger.info(f"[SLEEP] Waiting {sleep_time}s...")
            await asyncio.sleep(sleep_time)

    async def _run_scan_cycle(self):
        """Execute one full scan cycle."""
        self._scan_count += 1

        # Daily reset
        today = datetime.now(timezone.utc).date()
        if today != self._last_reset_date:
            self._daily_trade_count = 0
            self._last_reset_date = today

        # Daily limit
        if self._daily_trade_count >= settings.MAX_DAILY_TRADES:
            logger.info("[SCAN] Daily trade limit reached.")
            return

        # Position limit
        positions = await self.gateway.get_all_positions()
        if len(positions) >= settings.MAX_OPEN_POSITIONS:
            logger.info(f"[SCAN] Max positions ({len(positions)}/{settings.MAX_OPEN_POSITIONS})")
            return

        # News check
        active_news = get_active_events()
        if active_news:
            print(f"[NEWS] {', '.join(active_news)}")

        # Clear aggregation buffers
        self._session_signals.clear()
        self._sweep_signals.clear()
        self._expected_symbols = len(settings.SYMBOLS)

        # Get current session for display
        session = self._session_regime.get("session", "UNKNOWN")
        regime = self._session_regime.get("regime", "UNKNOWN")

        print(f"\n{'='*60}")
        print(f"  SCAN #{self._scan_count} | {session} session | {regime} regime")
        print(f"  Macro: {self._macro_filter.get('regime', 'NEUTRAL')} | "
              f"DXY={self._macro_filter.get('dxy', 0):.2f} | "
              f"VIX={self._macro_filter.get('vix', 0):.2f}")
        print(f"{'='*60}")

        # 1. Trigger data pipeline
        await self.emit(EventTypes.SCAN_START, {
            "count": len(settings.SYMBOLS),
            "cycle": self._scan_count,
            "session": session,
            "regime": regime,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # 2. Wait for signals
        await self._wait_for_signals(timeout=30)

        # 3. Evaluate candidates
        candidates = self._evaluate_candidates()

        # 4. Dashboard updates
        await self._publish_dashboard_updates(positions)

        # 5. Scan summary
        await self.emit(EventTypes.SCAN_SUMMARY, {
            "symbols": {s: "Active" for s in settings.SYMBOLS},
            "count": len(settings.SYMBOLS),
            "candidates": len(candidates),
            "session": session,
            "regime": regime,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # 6. Submit best candidate
        if candidates:
            candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
            self._print_candidates(candidates)

            best = candidates[0]
            logger.info(f">>> CANDIDATE: {best['symbol']} {best['direction']} "
                        f"(score={best['score']}, {best.get('reason', '')})")
            await self.emit(EventTypes.TRADE_CANDIDATE, best)
        else:
            print(f"[SCAN] No candidates â€” {regime} regime active")

    async def _wait_for_signals(self, timeout: int = 30):
        """Wait for session/sweep signals to arrive."""
        start = time.time()
        while time.time() - start < timeout:
            total = len(self._session_signals) + len(self._sweep_signals)
            if total > 0:
                break
            await asyncio.sleep(0.5)

    def _evaluate_candidates(self) -> list:
        """Evaluate session + sweep signals to produce trade candidates."""
        candidates = []

        # Process session strategy signals
        for symbol, sig in self._session_signals.items():
            direction = sig.get("direction", "NEUTRAL")
            if direction == "NEUTRAL":
                print(f"[SCAN] {symbol}: No session setup")
                continue

            score = sig.get("score", 0)
            if score < settings.MIN_CONFLUENCE_SCORE:
                print(f"[SCAN] {symbol}: Low score ({score} < {settings.MIN_CONFLUENCE_SCORE})")
                continue

            # Macro filter already applied in SessionStrategyService
            # but double-check direction is still allowed
            if not self._check_macro_direction(direction):
                print(f"[SCAN] {symbol}: Macro filter blocks {direction}")
                continue

            # Cooldown check
            last = self._last_trade_time.get(symbol, 0)
            if time.time() - last < settings.COOLDOWN_SECONDS:
                print(f"[SCAN] {symbol}: Cooldown active")
                continue

            candidates.append({
                "symbol": symbol,
                "direction": direction,
                "score": score,
                "sl_distance": sig.get("sl_distance", 0),
                "tp_distance": sig.get("tp_distance", 0),
                "scaling_factor": 1.0,
                "macro_size_factor": sig.get("macro_size_factor", 1.0),
                "emotion_state": "NEUTRAL",
                "emotion_score": 0.5,
                "ml_prob": 0.5,  # Not used in session regime
                "details": {"session": sig.get("session", ""),
                            "regime": sig.get("regime", ""),
                            "reason": sig.get("reason", "")},
                "features": {"atr": sig.get("atr", 0),
                             "rsi": sig.get("rsi", 0)},
                "reason": sig.get("reason", ""),
            })

        # Process liquidity sweep signals (higher priority â€” score 9)
        for symbol, sig in self._sweep_signals.items():
            direction = sig.get("direction", "NEUTRAL")
            if direction == "NEUTRAL":
                continue

            if not self._check_macro_direction(direction):
                continue

            candidates.append({
                "symbol": symbol,
                "direction": direction,
                "score": sig.get("score", 9),
                "sl_distance": sig.get("sl_distance", 0),
                "tp_distance": sig.get("tp_distance", 0),
                "scaling_factor": 1.0,
                "macro_size_factor": self._macro_filter.get("size_factor", 1.0),
                "emotion_state": "NEUTRAL",
                "emotion_score": 0.5,
                "ml_prob": 0.5,
                "details": {"sweep_type": sig.get("sweep_type", ""),
                            "sweep_level": sig.get("sweep_level", 0),
                            "reason": sig.get("reason", "")},
                "features": {"atr": sig.get("atr", 0),
                             "rsi": sig.get("rsi", 0)},
                "reason": sig.get("reason", ""),
            })

        return candidates

    def _check_macro_direction(self, direction: str) -> bool:
        """Check if macro filter allows this direction."""
        if not self._macro_filter:
            return True
        allowed = self._macro_filter.get("allowed_directions", ["BUY", "SELL"])
        return direction in allowed

    # â”€â”€â”€ Event Handlers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    async def _on_session_signal(self, event: Event):
        symbol = event.payload.get("symbol")
        if symbol:
            self._session_signals[symbol] = event.payload

    async def _on_sweep_signal(self, event: Event):
        symbol = event.payload.get("symbol")
        if symbol:
            self._sweep_signals[symbol] = event.payload

    async def _on_macro_filter(self, event: Event):
        self._macro_filter = event.payload

    async def _on_session_regime(self, event: Event):
        self._session_regime = event.payload

    async def _on_flat_all(self, event: Event):
        """NY Afternoon â€” flatten all open positions."""
        try:
            positions = await self.gateway.get_all_positions()
            if positions:
                logger.warning(
                    f"[FLAT ALL] NY Afternoon â€” closing {len(positions)} positions"
                )
                for pos in positions:
                    try:
                        await self.gateway.close_position(pos.ticket)
                        logger.info(f"[FLAT] Closed #{pos.ticket} {pos.symbol}")
                    except Exception as e:
                        logger.error(f"[FLAT] Failed to close #{pos.ticket}: {e}")
        except Exception as e:
            logger.error(f"[FLAT ALL] Error: {e}")

    async def _on_trade_executed(self, event: Event):
        self._daily_trade_count += 1
        symbol = event.payload.get("symbol", "")
        self._last_trade_time[symbol] = time.time()

    async def _on_news_trade_signal(self, event: Event):
        """Track active news trading windows."""
        symbols = event.payload.get("symbols", [])
        event_key = event.payload.get("event_key", "")
        for sym in symbols:
            from utils.news_filter import _strip_suffix
            self._news_window_symbols.add(_strip_suffix(sym).upper())
        self._news_event_info[event_key] = event.payload
        asyncio.create_task(self._clear_news_window_later(symbols, delay_minutes=45))

    async def _clear_news_window_later(self, symbols, delay_minutes=45):
        await asyncio.sleep(delay_minutes * 60)
        for sym in symbols:
            from utils.news_filter import _strip_suffix
            self._news_window_symbols.discard(_strip_suffix(sym).upper())

    # â”€â”€â”€ Dashboard Updates â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    async def _publish_dashboard_updates(self, positions):
        """Publish account + position info for the dashboard."""
        try:
            pos_list = []
            for p in positions:
                pos_list.append({
                    "ticket": p.ticket,
                    "symbol": p.symbol,
                    "type": p.type,
                    "direction": "BUY" if p.type == 0 else "SELL",
                    "volume": p.volume,
                    "entry_price": p.price_open,
                    "price_current": p.price_current,
                    "sl_price": p.sl,
                    "tp_price": p.tp,
                    "profit": p.profit,
                })
            await self.emit(EventTypes.POSITION_UPDATE, {
                "positions": pos_list,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            acct = await self.gateway.get_account_info()
            if acct:
                await self.emit(EventTypes.ACCOUNT_UPDATE, {
                    "account": {
                        "balance": acct["balance"],
                        "equity": acct["equity"],
                        "profit": acct["profit"],
                        "currency": acct.get("currency", "USD"),
                        "leverage": acct.get("leverage", 0),
                        "day_pl": round(acct["equity"] - acct["balance"], 2),
                    },
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        except Exception as e:
            logger.debug(f"Dashboard update error: {e}")

    # â”€â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _print_candidates(self, candidates):
        print(f"\n{'-'*60}")
        print(f"  {'Symbol':>10} | {'Dir':>4} | Sc | Reason")
        print(f"{'-'*60}")
        for c in candidates[:5]:
            sym = c['symbol']
            d = c['direction']
            reason = c.get('reason', '')[:40]
            print(f"    {sym:>10} | {d:>4} | {c['score']} | {reason}")

