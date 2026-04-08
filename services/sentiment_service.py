"""
SentimentService — Wraps Gemini News Analyzer + Sentiment Analyzer
as an event-driven service.

Subscribes to: MARKET_DATA_READY (triggers per-symbol sentiment fetch)
Publishes: SENTIMENT_UPDATE

Rate-limited: only fetches sentiment once per symbol per scan cycle.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Dict

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService

logger = logging.getLogger("SentimentService")


class SentimentService(BaseService):
    """
    Fetches news sentiment for symbols using Gemini AI.
    Publishes SENTIMENT_UPDATE events for downstream decision-making.
    """

    def __init__(self, event_bus: EventBus):
        super().__init__(event_bus)
        self._gemini = None
        self._sentiment_analyzer = None
        self._cache: Dict[str, dict] = {}  # {symbol: sentiment_data}
        self._last_fetch: Dict[str, float] = {}
        self._cache_ttl = 300  # 5 minutes

    @property
    def name(self) -> str:
        return "SentimentService"

    async def _setup(self):
        """Initialize Gemini analyzer if available."""
        try:
            from analysis.gemini_news_analyzer import get_gemini_analyzer
            self._gemini = get_gemini_analyzer()
            if not self._gemini.is_available():
                logger.info("Gemini analyzer not available (no API key)")
                self._gemini = None
        except Exception as e:
            logger.warning(f"Gemini analyzer init failed: {e}")
            self._gemini = None

        try:
            from analysis.sentiment_analyzer import get_sentiment_analyzer
            self._sentiment_analyzer = get_sentiment_analyzer()
        except Exception as e:
            logger.warning(f"Sentiment analyzer init failed: {e}")

        self.bus.subscribe(EventTypes.MARKET_DATA_READY, self._on_market_data)

    async def _on_market_data(self, event: Event):
        """Fetch sentiment when market data arrives (rate-limited)."""
        symbol = event.payload.get("symbol")
        if not symbol:
            return

        # Rate limit: skip if we fetched recently
        last = self._last_fetch.get(symbol, 0)
        if time.time() - last < self._cache_ttl:
            # Emit cached version
            if symbol in self._cache:
                await self.emit(EventTypes.SENTIMENT_UPDATE, self._cache[symbol])
            return

        if not self._gemini:
            # No Gemini — emit neutral sentiment
            neutral = {
                "symbol": symbol,
                "direction_bias": "NEUTRAL",
                "emotion_state": "NEUTRAL",
                "emotion_score": 0.5,
                "available": False,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._cache[symbol] = neutral
            await self.emit(EventTypes.SENTIMENT_UPDATE, neutral)
            return

        try:
            sentiment_data = await self._gemini.analyze(symbol)
            self._last_fetch[symbol] = time.time()

            result = {
                "symbol": symbol,
                "direction_bias": sentiment_data.get("direction_bias", "NEUTRAL"),
                "emotion_state": sentiment_data.get("emotion_state", "NEUTRAL"),
                "emotion_score": sentiment_data.get("emotion_score", 0.5),
                "sentiment_data": sentiment_data,
                "available": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            self._cache[symbol] = result
            await self.emit(EventTypes.SENTIMENT_UPDATE, result)

        except Exception as e:
            logger.warning(f"[{symbol}] Sentiment fetch failed: {e}")
            # Emit neutral on error
            neutral = {
                "symbol": symbol,
                "direction_bias": "NEUTRAL",
                "emotion_state": "NEUTRAL",
                "emotion_score": 0.5,
                "available": False,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._cache[symbol] = neutral
            await self.emit(EventTypes.SENTIMENT_UPDATE, neutral)

    def get_sentiment(self, symbol: str) -> dict:
        """Get cached sentiment for a symbol."""
        return self._cache.get(symbol, {
            "direction_bias": "NEUTRAL",
            "emotion_state": "NEUTRAL",
            "emotion_score": 0.5,
            "available": False,
        })
