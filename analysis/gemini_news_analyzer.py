"""
Gemini AI News & Market Intelligence Analyzer
==============================================
Uses Google Gemini 2.0 Flash to analyze real-time global financial news,
central bank policies, and macroeconomic events for each trading pair.

Returns structured sentiment scores that feed into the pre-trade decision pipeline.
"""

import os
import json
import time
from utils.async_utils import run_in_executor
from datetime import datetime, timezone
from typing import Dict, Optional

try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

# ─── Currency Pair Context Mapping ─────────────────────────────────────────
PAIR_CONTEXT = {
    "EURUSD": {"base": "Euro (EUR)", "quote": "US Dollar (USD)", "economies": "Eurozone, United States", "drivers": "ECB rate decisions, Fed policy, US NFP, EU GDP, inflation data"},
    "GBPUSD": {"base": "British Pound (GBP)", "quote": "US Dollar (USD)", "economies": "United Kingdom, United States", "drivers": "BoE rate decisions, Fed policy, UK CPI, Brexit developments"},
    "USDJPY": {"base": "US Dollar (USD)", "quote": "Japanese Yen (JPY)", "economies": "United States, Japan", "drivers": "Fed policy, BoJ yield curve control, Japan CPI, risk sentiment"},
    "AUDUSD": {"base": "Australian Dollar (AUD)", "quote": "US Dollar (USD)", "economies": "Australia, United States", "drivers": "RBA policy, China PMI, commodity prices, risk appetite"},
    "NZDUSD": {"base": "New Zealand Dollar (NZD)", "quote": "US Dollar (USD)", "economies": "New Zealand, United States", "drivers": "RBNZ policy, dairy prices, China trade data"},
    "USDCAD": {"base": "US Dollar (USD)", "quote": "Canadian Dollar (CAD)", "economies": "United States, Canada", "drivers": "Fed/BoC policy, oil prices, Canada employment"},
    "USDCHF": {"base": "US Dollar (USD)", "quote": "Swiss Franc (CHF)", "economies": "United States, Switzerland", "drivers": "Fed/SNB policy, safe haven flows, geopolitical risk"},
    "BTCUSD": {"base": "Bitcoin (BTC)", "quote": "US Dollar (USD)", "economies": "Global crypto market", "drivers": "Crypto regulation, ETF flows, halving cycle, risk sentiment, DeFi activity"},
    "ETHUSD": {"base": "Ethereum (ETH)", "quote": "US Dollar (USD)", "economies": "Global crypto market", "drivers": "ETH staking, DeFi TVL, L2 adoption, SEC regulation"},
    "LTCUSD": {"base": "Litecoin (LTC)", "quote": "US Dollar (USD)", "economies": "Global crypto market", "drivers": "Halving cycle, adoption, BTC correlation"},
    "XAUUSD": {"base": "Gold (XAU)", "quote": "US Dollar (USD)", "economies": "Global", "drivers": "Fed policy, real yields, geopolitical risk, central bank buying, inflation expectations"},
    "XAGUSD": {"base": "Silver (XAG)", "quote": "US Dollar (USD)", "economies": "Global", "drivers": "Industrial demand, gold correlation, solar energy, Fed policy"},
    "USOIL":  {"base": "Crude Oil (WTI)", "quote": "US Dollar (USD)", "economies": "Global energy", "drivers": "OPEC+ decisions, US inventory, geopolitical tensions, global growth outlook"},
}


class GeminiNewsAnalyzer:
    """
    Uses Google Gemini AI to analyze global financial news and 
    generate actionable sentiment scores for trading pairs.
    """

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY", "")
        self.client = None
        self.model_id = "gemini-2.0-flash"
        self.cache = {}  # {symbol: (timestamp, result)}
        self.cache_ttl = 900  # 15 minutes — avoid API spam
        self._initialized = False

        if GEMINI_AVAILABLE and self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
                self._initialized = True
                print(f"[GEMINI] News Analyzer initialized ({self.model_id})")
            except Exception as e:
                print(f"[GEMINI] Init failed: {e}")
        else:
            if not GEMINI_AVAILABLE:
                print("[GEMINI] google-genai not installed. News AI disabled.")
            elif not self.api_key:
                print("[GEMINI] No API key. News AI disabled.")

    def is_available(self) -> bool:
        return self._initialized and self.client is not None

    async def analyze(self, symbol: str) -> Dict:
        """
        Analyze current global news sentiment for a trading pair.
        Returns structured sentiment data.
        """
        if not self.is_available():
            return self._neutral_response(symbol, "Gemini unavailable")

        # Check cache
        if symbol in self.cache:
            cached_time, cached_data = self.cache[symbol]
            if (time.time() - cached_time) < self.cache_ttl:
                return cached_data

        try:
            # Use shared executor to avoid blocking the event loop
            result = await run_in_executor(self._call_gemini, symbol)
            # Cache the result
            self.cache[symbol] = (time.time(), result)
            return result
        except Exception as e:
            print(f"[GEMINI] Error analyzing {symbol}: {e}")
            return self._neutral_response(symbol, f"API error: {str(e)[:50]}")

    def _call_gemini(self, symbol: str) -> Dict:
        """Synchronous Gemini API call using the new google-genai SDK."""
        context = PAIR_CONTEXT.get(symbol, {
            "base": symbol[:3], "quote": symbol[3:6],
            "economies": "Global", "drivers": "price action, macro events"
        })

        prompt = f"""You are an institutional forex/commodity/crypto market analyst. 
Analyze the CURRENT global financial news and macroeconomic environment for the trading pair: {symbol}

Pair Details:
- Base: {context['base']}
- Quote: {context['quote']}  
- Key Economies: {context['economies']}
- Primary Drivers: {context['drivers']}

Based on TODAY's global news, central bank communications, economic data releases, 
geopolitical events, and market sentiment, provide your analysis.

You MUST respond in EXACTLY this JSON format, nothing else:
{{
    "sentiment_score": <float from -1.0 (extremely bearish) to +1.0 (extremely bullish) for {symbol}>,
    "confidence": <float from 0.0 to 1.0 indicating how certain you are>,
    "direction_bias": "<BULLISH or BEARISH or NEUTRAL>",
    "emotion_state": "<PANIC or FEAR or NEUTRAL or GREED or EUPHORIA>",
    "emotion_score": <float from 0.0 (extreme panic/fear) to 1.0 (extreme greed/euphoria), 0.5 is neutral>,
    "key_events": ["<event1>", "<event2>", "<event3>"],
    "risk_level": "<LOW or MEDIUM or HIGH>",
    "reasoning": "<1-2 sentence summary of why>"
}}

IMPORTANT RULES:
- Score should reflect bias for BUYING {symbol} (positive = bullish for base currency)
- Set emotion_state to reflect the current psychological state of the broader market (e.g. Gold often rises in FEAR or PANIC)
- Be conservative with confidence unless there is CLEAR directional evidence
- If no major news, return neutral with low confidence
- Focus on events from the last 24 hours
- Consider both fundamental AND sentiment factors"""

        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,  # Low temperature for consistent analysis
                    max_output_tokens=500,
                )
            )

            # Parse the JSON response
            text = response.text.strip()
            # Remove markdown code fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1]  # Remove first line
                if text.endswith("```"):
                    text = text[:-3]
                elif "```" in text:
                    text = text[:text.rfind("```")]
            text = text.strip()

            data = json.loads(text)

            # Validate and clamp values
            score = max(-1.0, min(1.0, float(data.get("sentiment_score", 0))))
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.3))))
            emotion_score = max(0.0, min(1.0, float(data.get("emotion_score", 0.5))))

            result = {
                "score": round(score, 3),
                "confidence": round(confidence, 3),
                "direction_bias": data.get("direction_bias", "NEUTRAL"),
                "emotion_state": data.get("emotion_state", "NEUTRAL"),
                "emotion_score": round(emotion_score, 3),
                "key_events": data.get("key_events", [])[:5],
                "risk_level": data.get("risk_level", "MEDIUM"),
                "reasoning": data.get("reasoning", ""),
                "source": self.model_id,
                "symbol": symbol,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            print(f"[GEMINI] {symbol}: {result['direction_bias']} ({result['score']:+.2f}) "
                  f"| Emotion: {result['emotion_state']} ({result['emotion_score']:.2f}) "
                  f"| Conf: {result['confidence']:.0%} | {result['reasoning'][:80]}")

            return result

        except Exception as e:
            print(f"[GEMINI] API call/parse failed for {symbol}: {e}")
            return self._neutral_response(symbol, str(e)[:50])

    def _neutral_response(self, symbol: str, reason: str = "") -> Dict:
        return {
            "score": 0.0,
            "confidence": 0.1,
            "direction_bias": "NEUTRAL",
            "emotion_state": "NEUTRAL",
            "emotion_score": 0.5,
            "key_events": [],
            "risk_level": "MEDIUM",
            "reasoning": reason or "No data available",
            "source": "fallback",
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    def should_block_trade(self, direction: str, sentiment: Dict) -> tuple:
        """
        Determines if Gemini sentiment strongly contradicts the proposed trade direction.
        Returns (should_block: bool, reason: str)
        """
        score = sentiment.get("score", 0)
        confidence = sentiment.get("confidence", 0)
        risk_level = sentiment.get("risk_level", "MEDIUM")

        # Only block on HIGH confidence contradictions
        if confidence < 0.6:
            return False, ""

        # Block if strong bearish sentiment vs BUY signal
        if direction == "BUY" and score < -0.5:
            return True, f"Gemini BEARISH ({score:+.2f}, {confidence:.0%} conf): {sentiment.get('reasoning', '')[:60]}"

        # Block if strong bullish sentiment vs SELL signal
        if direction == "SELL" and score > 0.5:
            return True, f"Gemini BULLISH ({score:+.2f}, {confidence:.0%} conf): {sentiment.get('reasoning', '')[:60]}"

        # Block during HIGH risk events regardless of direction
        if risk_level == "HIGH" and confidence > 0.7:
            return True, f"HIGH RISK event detected: {sentiment.get('reasoning', '')[:80]}"

        return False, ""


# ─── Singleton ─────────────────────────────────────────────────────────────
_gemini_analyzer = None

def get_gemini_analyzer() -> GeminiNewsAnalyzer:
    global _gemini_analyzer
    if _gemini_analyzer is None:
        _gemini_analyzer = GeminiNewsAnalyzer()
    return _gemini_analyzer
