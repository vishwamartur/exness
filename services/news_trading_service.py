"""
NewsTradingService — Event-driven news-based trading for XAUUSD.

Instead of blocking trades during news, this service ACTIVELY trades
high-impact economic events on Gold.

Two modes:
  STRADDLE:  Pre-positions buy-stop/sell-stop around the pre-news range
  BREAKOUT:  Waits for post-news candle confirmation before entering

Event Flow:
  SCAN_START → Check upcoming news → Capture pre-news snapshot
  MARKET_DATA_READY → (if news window active) Detect breakout / manage straddle
  → TRADE_CANDIDATE (with news-specific params)

Subscribes to: SCAN_START, MARKET_DATA_READY, SENTIMENT_UPDATE
Publishes: TRADE_CANDIDATE, NEWS_TRADE_SIGNAL
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from strategy.news_strategy import NewsStrategy
from utils.news_filter import (
    get_news_trade_opportunities,
    get_pre_news_window,
    classify_event_impact,
    _strip_suffix,
)
from config import settings

logger = logging.getLogger("NewsTradingService")


class NewsTradingService(BaseService):
    """
    Actively trades XAUUSD during high-impact economic news events.
    Plugs into the event-driven architecture alongside normal scalping.
    """

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)
        self.strategy = NewsStrategy()

        # State tracking
        self._active_events: Dict[str, dict] = {}     # {event_key: event_info}
        self._traded_events: Dict[str, int] = {}       # {event_key: trade_count}
        self._last_news_trade_time: float = 0
        self._pre_news_captured: set = set()           # event_keys with snapshots
        self._sentiment_cache: Dict[str, dict] = {}    # {symbol: sentiment}

        # Market data cache for breakout detection
        self._latest_data: Dict[str, dict] = {}        # {symbol: {df, tick}}

        # News window state
        self._news_window_active = False
        self._current_event_key: Optional[str] = None

    @property
    def name(self) -> str:
        return "NewsTradingService"

    async def _setup(self):
        """Subscribe to events."""
        if not getattr(settings, 'NEWS_TRADING_ENABLED', False):
            logger.info("News trading DISABLED in settings")
            return

        self.bus.subscribe(EventTypes.SCAN_START, self._on_scan_start)
        self.bus.subscribe(EventTypes.MARKET_DATA_READY, self._on_market_data)
        self.bus.subscribe(EventTypes.SENTIMENT_UPDATE, self._on_sentiment)

        mode = getattr(settings, 'NEWS_TRADING_MODE', 'BREAKOUT')
        symbols = getattr(settings, 'NEWS_TRADING_SYMBOLS', ['XAUUSD'])
        logger.info(
            f"News trading ENABLED | Mode: {mode} | "
            f"Symbols: {', '.join(symbols)}"
        )

    # ─── Event Handlers ───────────────────────────────────────────────────

    async def _on_scan_start(self, event: Event):
        """
        On each scan cycle, check for upcoming news trade opportunities.
        If an event is within the lookahead window, prepare for it.
        """
        if not getattr(settings, 'NEWS_TRADING_ENABLED', False):
            return

        now = datetime.now(timezone.utc)
        lookahead = getattr(settings, 'NEWS_LOOKAHEAD_MINUTES', 30)
        trading_symbols = self._get_trading_symbols()

        # Find opportunities
        opportunities = get_news_trade_opportunities(
            symbols=trading_symbols,
            now_utc=now,
            lookahead_minutes=lookahead,
        )

        if not opportunities:
            if self._news_window_active:
                logger.info("[NEWS] News window expired — back to normal mode")
                self._news_window_active = False
                self._current_event_key = None
            return

        # Process each opportunity
        for opp in opportunities:
            event_key = self._make_event_key(opp)

            # Skip if we've already traded this event max times
            max_trades = getattr(settings, 'NEWS_MAX_TRADES_PER_EVENT', 1)
            if self._traded_events.get(event_key, 0) >= max_trades:
                continue

            # Check cooldown
            cooldown = getattr(settings, 'NEWS_POST_EVENT_COOLDOWN', 300)
            if time.time() - self._last_news_trade_time < cooldown:
                continue

            minutes_until = opp['minutes_until']
            mode = getattr(settings, 'NEWS_TRADING_MODE', 'BREAKOUT')
            classification = opp['classification']

            print(
                f"\n[NEWS TRADE] ⚡ {opp['name']} ({opp['currency']}) "
                f"in {minutes_until:.0f} min | "
                f"Type: {classification['type']} | "
                f"Pattern: {classification['pattern']} | "
                f"Expected: {classification['expected_move_pips']} pips"
            )

            # Mark news window as active
            self._news_window_active = True
            self._current_event_key = event_key
            self._active_events[event_key] = opp

            # Publish news signal for coordinator (suppress normal XAUUSD trades)
            await self.emit(EventTypes.NEWS_TRADE_SIGNAL, {
                "event_key": event_key,
                "event_name": opp['name'],
                "currency": opp['currency'],
                "minutes_until": minutes_until,
                "classification": classification,
                "mode": mode,
                "symbols": opp['tradeable_symbols'],
                "timestamp": now.isoformat(),
            })

    async def _on_market_data(self, event: Event):
        """
        When XAUUSD market data arrives during a news window:
        - Capture pre-news snapshot if not yet done
        - In BREAKOUT mode: detect post-news breakout
        - In STRADDLE mode: generate straddle order request
        """
        if not getattr(settings, 'NEWS_TRADING_ENABLED', False):
            return

        symbol = event.payload.get("symbol", "")
        data_dict = event.payload.get("data_dict")
        tick = event.payload.get("tick")

        if not symbol or not data_dict:
            return

        # Only process XAUUSD (or configured news symbols)
        if not self._is_news_symbol(symbol):
            return

        # Cache latest data
        self._latest_data[symbol] = {
            "data_dict": data_dict,
            "tick": tick,
        }

        # No active news events → skip
        if not self._active_events:
            return

        mode = getattr(settings, 'NEWS_TRADING_MODE', 'BREAKOUT')
        tf = getattr(settings, 'TIMEFRAME', 'M5')
        df = data_dict.get(tf)

        if df is None or len(df) < 20:
            return

        now = datetime.now(timezone.utc)

        for event_key, opp in list(self._active_events.items()):
            if symbol not in opp.get('tradeable_symbols', []):
                continue

            # Check if already traded max times
            max_trades = getattr(settings, 'NEWS_MAX_TRADES_PER_EVENT', 1)
            if self._traded_events.get(event_key, 0) >= max_trades:
                continue

            event_dt = opp['dt_utc']
            window_start, window_end, action = get_pre_news_window(event_dt, mode)

            # ── Pre-News: Capture Snapshot ────────────────────────────
            if event_key not in self._pre_news_captured:
                if now >= (event_dt - timedelta(minutes=15)):
                    snapshot = self.strategy.capture_pre_news_snapshot(
                        symbol, df, event_key
                    )
                    if snapshot:
                        self._pre_news_captured.add(event_key)
                        print(
                            f"[NEWS] Pre-news snapshot captured for {opp['name']}: "
                            f"Price={snapshot['current_price']:.2f}, "
                            f"ATR={snapshot['atr']:.2f}, "
                            f"Range={snapshot['range_size']:.2f}"
                        )

            snapshot = self.strategy.get_snapshot(event_key)
            if not snapshot:
                continue

            # ── Check ATR minimum ─────────────────────────────────────
            min_atr = getattr(settings, 'NEWS_MIN_ATR_FOR_TRADE', 1.0)
            if snapshot['atr'] < min_atr:
                logger.info(
                    f"[{symbol}] ATR too low for news trade: "
                    f"{snapshot['atr']:.2f} < {min_atr:.2f}"
                )
                continue

            # ── STRADDLE MODE ─────────────────────────────────────────
            if mode == "STRADDLE" and window_start <= now <= window_end:
                await self._handle_straddle(symbol, opp, snapshot)

            # ── BREAKOUT MODE ─────────────────────────────────────────
            elif mode == "BREAKOUT" and now >= event_dt:
                # Only check for breakout AFTER the event time
                post_window = getattr(settings, 'NEWS_LOOKAHEAD_MINUTES', 30)
                if now <= (event_dt + timedelta(minutes=post_window)):
                    await self._handle_breakout(symbol, df, opp, snapshot)
                else:
                    # Window expired
                    logger.info(f"[NEWS] Breakout window expired for {opp['name']}")
                    self._cleanup_event(event_key)

    async def _on_sentiment(self, event: Event):
        """Cache sentiment for use in news direction prediction."""
        symbol = event.payload.get("symbol", "")
        if self._is_news_symbol(symbol):
            self._sentiment_cache[symbol] = event.payload

    # ─── Trading Logic ────────────────────────────────────────────────────

    async def _handle_straddle(self, symbol: str, opp: dict, snapshot: dict):
        """Generate straddle order requests (buy-stop + sell-stop)."""
        event_key = self._make_event_key(opp)

        # Already placed straddle for this event?
        if self._traded_events.get(event_key, 0) > 0:
            return

        distance_atr = getattr(settings, 'NEWS_STRADDLE_DISTANCE_ATR', 1.5)
        levels = self.strategy.calculate_straddle_levels(snapshot, distance_atr)

        classification = opp['classification']
        lot_reduction = getattr(settings, 'NEWS_LOT_REDUCTION', 0.5)

        print(
            f"\n[NEWS STRADDLE] {opp['name']} on {symbol}\n"
            f"  BuyStop:  {levels['buy_stop_price']:.2f} | "
            f"SL: {levels['buy_sl']:.2f} | TP: {levels['buy_tp']:.2f}\n"
            f"  SellStop: {levels['sell_stop_price']:.2f} | "
            f"SL: {levels['sell_sl']:.2f} | TP: {levels['sell_tp']:.2f}\n"
            f"  Width: {levels['straddle_width']:.2f} | "
            f"Risk/side: {levels['risk_per_side']:.2f}"
        )

        # Emit BUY side straddle as TRADE_CANDIDATE
        await self.emit(EventTypes.TRADE_CANDIDATE, {
            "symbol": symbol,
            "direction": "BUY",
            "score": 6,  # News trades bypass normal confluence
            "ml_prob": 0.6,
            "ensemble_score": 6,
            "regime": "VOLATILE",
            "regime_type": "VOLATILE",
            "sl_distance": levels['buy_stop_price'] - levels['buy_sl'],
            "tp_distance": levels['buy_tp'] - levels['buy_stop_price'],
            "scaling_factor": lot_reduction,
            "emotion_state": "NEUTRAL",
            "emotion_score": 0.5,
            "details": {
                "source": "NEWS_STRADDLE",
                "event": opp['name'],
                "event_type": classification['type'],
                "pattern": classification['pattern'],
                "straddle_levels": levels,
            },
            "features": {"atr": snapshot['atr']},
            "is_news_trade": True,
            "news_event_key": event_key,
        })

        # Emit SELL side straddle
        await self.emit(EventTypes.TRADE_CANDIDATE, {
            "symbol": symbol,
            "direction": "SELL",
            "score": 6,
            "ml_prob": 0.6,
            "ensemble_score": 6,
            "regime": "VOLATILE",
            "regime_type": "VOLATILE",
            "sl_distance": levels['sell_sl'] - levels['sell_stop_price'],
            "tp_distance": levels['sell_stop_price'] - levels['sell_tp'],
            "scaling_factor": lot_reduction,
            "emotion_state": "NEUTRAL",
            "emotion_score": 0.5,
            "details": {
                "source": "NEWS_STRADDLE",
                "event": opp['name'],
                "event_type": classification['type'],
                "pattern": classification['pattern'],
                "straddle_levels": levels,
            },
            "features": {"atr": snapshot['atr']},
            "is_news_trade": True,
            "news_event_key": event_key,
        })

        self._traded_events[event_key] = self._traded_events.get(event_key, 0) + 1
        self._last_news_trade_time = time.time()

    async def _handle_breakout(self, symbol: str, df, opp: dict, snapshot: dict):
        """Detect and trade post-news breakout."""
        event_key = self._make_event_key(opp)

        # Already traded this event?
        max_trades = getattr(settings, 'NEWS_MAX_TRADES_PER_EVENT', 1)
        if self._traded_events.get(event_key, 0) >= max_trades:
            return

        confirm_candles = getattr(settings, 'NEWS_BREAKOUT_CONFIRM_CANDLES', 1)
        breakout = self.strategy.detect_news_breakout(df, snapshot, confirm_candles)

        if not breakout:
            return

        direction = breakout['direction']
        classification = opp['classification']
        current_price = breakout['current_price']

        # Get Gemini AI bias for bonus confirmation
        sentiment = self._sentiment_cache.get(symbol, {})
        ai_dir, ai_conf = self.strategy.get_ai_direction_bias(sentiment)

        # AI alignment bonus
        ai_aligned = (ai_dir == direction)
        confidence_boost = 0
        if ai_aligned and ai_conf > 0.3:
            confidence_boost = 1
            logger.info(f"[NEWS] AI confirms {direction} with {ai_conf:.0%} confidence")

        # Calculate news-specific SL/TP
        sl_mult = getattr(settings, 'NEWS_ATR_SL_MULTIPLIER', 2.0)
        tp_mult = getattr(settings, 'NEWS_ATR_TP_MULTIPLIER', 4.0)

        sl, tp = self.strategy.calculate_news_sl_tp(
            direction, current_price, snapshot, classification,
            sl_atr_mult=sl_mult, tp_atr_mult=tp_mult,
        )

        sl_distance = abs(current_price - sl)
        tp_distance = abs(tp - current_price)
        lot_reduction = getattr(settings, 'NEWS_LOT_REDUCTION', 0.5)

        # Compute overall score
        score = breakout['strength'] + confidence_boost

        print(
            f"\n{'='*60}\n"
            f"  ⚡ NEWS BREAKOUT TRADE: {direction} {symbol}\n"
            f"  Event: {opp['name']} ({classification['type']})\n"
            f"  Pattern: {classification['pattern']}\n"
            f"  Price: {current_price:.2f} | SL: {sl:.2f} | TP: {tp:.2f}\n"
            f"  Strength: {breakout['strength']}/10 | "
            f"Volume: {breakout['volume_ratio']:.1f}x\n"
            f"  AI Bias: {ai_dir} ({ai_conf:.0%}) {'✓ ALIGNED' if ai_aligned else '✗'}\n"
            f"  Lot Reduction: {lot_reduction:.0%}\n"
            f"{'='*60}"
        )

        # Emit as TRADE_CANDIDATE — goes through normal RiskService pipeline
        await self.emit(EventTypes.TRADE_CANDIDATE, {
            "symbol": symbol,
            "direction": direction,
            "score": score,
            "ml_prob": 0.5 + (breakout['strength'] / 20),  # Map strength to prob
            "ensemble_score": score,
            "regime": "VOLATILE",
            "regime_type": "VOLATILE",
            "sl_distance": sl_distance,
            "tp_distance": tp_distance,
            "scaling_factor": lot_reduction,
            "emotion_state": sentiment.get("emotion_state", "NEUTRAL"),
            "emotion_score": sentiment.get("emotion_score", 0.5),
            "details": {
                "source": "NEWS_BREAKOUT",
                "event": opp['name'],
                "event_type": classification['type'],
                "pattern": classification['pattern'],
                "breakout": breakout,
                "ai_direction": ai_dir,
                "ai_confidence": ai_conf,
                "ai_aligned": ai_aligned,
            },
            "features": {"atr": snapshot['atr']},
            "is_news_trade": True,
            "news_event_key": event_key,
        })

        self._traded_events[event_key] = self._traded_events.get(event_key, 0) + 1
        self._last_news_trade_time = time.time()

        # Clean up snapshot
        self.strategy.clear_snapshot(event_key)

    # ─── Helpers ──────────────────────────────────────────────────────────

    def _get_trading_symbols(self) -> list:
        """Get the list of news-tradeable symbols (XAUUSD variants)."""
        configured = getattr(settings, 'NEWS_TRADING_SYMBOLS', ['XAUUSD'])
        # Map to actual suffixed symbols available on the account
        available = getattr(settings, 'SYMBOLS', [])
        matched = []
        for base in configured:
            for sym in available:
                stripped = _strip_suffix(sym).upper()
                if stripped == base.upper():
                    matched.append(sym)
        return matched or configured

    def _is_news_symbol(self, symbol: str) -> bool:
        """Check if a symbol is eligible for news trading."""
        stripped = _strip_suffix(symbol).upper()
        configured = getattr(settings, 'NEWS_TRADING_SYMBOLS', ['XAUUSD'])
        return any(stripped == c.upper() for c in configured)

    def _make_event_key(self, opp: dict) -> str:
        """Create a unique key for a news event."""
        dt_str = opp['dt_utc'].strftime('%Y%m%d_%H%M')
        name = opp['name'].replace(' ', '_')[:20]
        return f"{name}_{dt_str}"

    def _cleanup_event(self, event_key: str):
        """Clean up after a news event window expires."""
        self._active_events.pop(event_key, None)
        self._pre_news_captured.discard(event_key)
        self.strategy.clear_snapshot(event_key)

    @property
    def is_news_window_active(self) -> bool:
        """Whether a news trading window is currently active."""
        return self._news_window_active

    @property
    def active_event_symbols(self) -> list:
        """Symbols currently in a news trading window."""
        symbols = []
        for opp in self._active_events.values():
            symbols.extend(opp.get('tradeable_symbols', []))
        return list(set(symbols))
