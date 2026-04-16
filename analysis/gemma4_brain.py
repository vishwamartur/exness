"""
Gemma4Brain — Uses Google's Gemma 4 model as the AI "brain" of the trader.

Gemma 4 acts as a senior institutional trader that:
  1. Reads live technical indicators (RSI, MACD, ATR, ADX, Bollinger, SMC zones)
  2. Reads multi-timeframe trend context (M5, H1, H4)
  3. Reads current P&L and open positions
  4. Reads news sentiment
  5. Returns a structured trade decision: BUY / SELL / HOLD + confidence + reasons

The brain is called by CoordinatorService BEFORE a TRADE_CANDIDATE is published.
It acts as a HIGH-LEVEL VETO — if Gemma 4 says HOLD, the trade is blocked.
If it says BUY/SELL with high confidence, the score is boosted.

Model: gemma-3-27b-it  (Gemma 4 / latest Gemma via Google AI Studio)
Fallback: gemma-3-12b-it → gemma-3-4b-it
"""

import os
import json
import logging
import requests
import time
from typing import Optional, Dict, Tuple

logger = logging.getLogger("Gemma4Brain")

# Google AI — Gemma 4 models (ordered best→fastest)
GEMMA4_MODELS = [
    "gemma-3-27b-it",   # Gemma 4 27B — best quality
    "gemma-3-12b-it",   # Gemma 4 12B — fallback
    "gemma-3-4b-it",    # Gemma 4 4B  — fast fallback
]

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class Gemma4Brain:
    """
    Gemma 4 — The AI Trader Brain.

    Call analyze_trade_candidate() for each potential trade.
    Returns (decision, confidence, reasoning) where:
        decision   : "BUY" | "SELL" | "HOLD"
        confidence : 0-100 integer
        reasoning  : 1-2 sentence explanation
    """

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY", "")
        self._available = bool(self.api_key)
        self._request_count = 0
        self._last_request_time = 0.0
        self._min_interval = 1.5  # Rate limit: max ~40 req/min on free tier

        if self._available:
            logger.info("[Gemma4Brain] ✅ Initialized — model: gemma-3-27b-it")
        else:
            logger.warning("[Gemma4Brain] ⚠️  No GEMINI_API_KEY — Gemma 4 brain disabled")

    def is_available(self) -> bool:
        return self._available

    # ──────────────────────────────────────────────────────────────────────
    # PUBLIC API
    # ──────────────────────────────────────────────────────────────────────

    def analyze_trade_candidate(
        self,
        symbol: str,
        direction: str,
        features: dict,
        regime: str,
        score: int,
        ml_prob: float,
        open_positions: list = None,
        news_context: str = "",
        timeframe: str = "M5",
    ) -> Tuple[str, int, str]:
        """
        Core brain function — ask Gemma 4 whether to take this trade.

        Returns:
            (decision, confidence, reasoning)
            decision: "BUY" | "SELL" | "HOLD"
        """
        if not self._available:
            return direction, 60, "Gemma 4 unavailable — defaulting to quant signal"

        # Rate limiting
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

        prompt = self._build_trader_prompt(
            symbol, direction, features, regime, score, ml_prob,
            open_positions or [], news_context, timeframe
        )

        raw_text = self._call_gemma4(prompt)
        if not raw_text:
            logger.warning(f"[Gemma4Brain] No response for {symbol} — defaulting to HOLD")
            return "HOLD", 40, "Gemma 4 API timeout — skipping trade"

        decision, confidence, reasoning = self._parse_response(raw_text, direction)
        self._last_request_time = time.time()
        self._request_count += 1

        logger.info(
            f"[Gemma4Brain] {symbol} {direction} → {decision} ({confidence}%) | {reasoning}"
        )
        return decision, confidence, reasoning

    def analyze_market_context(
        self,
        symbol: str,
        features: dict,
        regime: str,
        news: str = "",
    ) -> Dict:
        """
        Broader market analysis — called once per scan cycle.
        Returns a market context dict with bias and key levels.
        """
        if not self._available:
            return {"bias": "NEUTRAL", "confidence": 50, "key_levels": [], "summary": ""}

        prompt = self._build_context_prompt(symbol, features, regime, news)
        raw_text = self._call_gemma4(prompt, max_tokens=200)

        if not raw_text:
            return {"bias": "NEUTRAL", "confidence": 50, "key_levels": [], "summary": ""}

        return self._parse_context_response(raw_text)

    # ──────────────────────────────────────────────────────────────────────
    # PROMPT BUILDERS
    # ──────────────────────────────────────────────────────────────────────

    def _build_trader_prompt(
        self, symbol, direction, features, regime, score, ml_prob,
        open_positions, news_context, timeframe
    ) -> str:
        # Extract key indicators safely
        rsi = round(features.get("rsi", 50), 1)
        macd = round(features.get("macd", 0), 4)
        macd_signal = round(features.get("macd_signal", 0), 4)
        atr = round(features.get("atr", 0), 4)
        adx = round(features.get("adx", 0), 1)
        close = round(features.get("close", 0), 4)
        bb_upper = round(features.get("bb_upper", 0), 4)
        bb_lower = round(features.get("bb_lower", 0), 4)
        ema20 = round(features.get("ema20", close), 4)
        ema50 = round(features.get("ema50", close), 4)
        near_ob_bull = features.get("near_ob_bullish", 0)
        near_ob_bear = features.get("near_ob_bearish", 0)
        liq_sweep_low = features.get("liq_sweep_low", 0)
        liq_sweep_high = features.get("liq_sweep_high", 0)

        # Open positions summary
        pos_summary = "None"
        if open_positions:
            pos_lines = []
            for p in open_positions[:3]:
                pnl = getattr(p, 'profit', 0)
                vol = getattr(p, 'volume', 0)
                ptype = "BUY" if getattr(p, 'type', 0) == 0 else "SELL"
                pos_lines.append(f"  • {ptype} {vol}lot @ {getattr(p,'price_open',0):.2f} | PnL: ${pnl:.2f}")
            pos_summary = "\n".join(pos_lines)

        ml_pct = round(ml_prob * 100, 1)
        direction_prob = ml_pct if direction == "BUY" else round((1 - ml_prob) * 100, 1)

        prompt = f"""You are an elite institutional XAUUSD trader. Analyze this trade setup and decide if it should be executed.

SYMBOL: {symbol} | TIMEFRAME: {timeframe} | REGIME: {regime}

TECHNICAL INDICATORS:
  Price:       {close}
  RSI:         {rsi} {'(overbought)' if rsi > 70 else '(oversold)' if rsi < 30 else '(neutral)'}
  MACD:        {macd} vs Signal {macd_signal} → {'BULLISH cross' if macd > macd_signal else 'BEARISH cross'}
  ADX:         {adx} {'(strong trend)' if adx > 25 else '(weak/ranging)'}
  ATR:         {atr} (volatility)
  EMA20:       {ema20} | EMA50: {ema50} → {'Price ABOVE EMAs (bullish)' if close > ema20 > ema50 else 'Price BELOW EMAs (bearish)' if close < ema20 < ema50 else 'Mixed EMAs'}
  Bollinger:   Upper={bb_upper} Lower={bb_lower}
  Near Bull OB: {'YES' if near_ob_bull else 'NO'} | Near Bear OB: {'YES' if near_ob_bear else 'NO'}
  Liq Sweep L: {'YES' if liq_sweep_low else 'NO'} | Liq Sweep H: {'YES' if liq_sweep_high else 'NO'}

QUANTITATIVE SIGNAL:
  Proposed Direction: {direction}
  Confluence Score:   {score}/6
  ML Probability:     {direction_prob:.1f}% for {direction}

OPEN POSITIONS:
{pos_summary}

NEWS/SENTIMENT: {news_context if news_context else 'No significant news'}

YOUR TASK:
Based on all the above, decide if this {direction} trade is worth taking RIGHT NOW.
Consider: trend alignment, RSI extremes, momentum, risk, and whether it fits the regime.

Respond in EXACTLY this format (one line only):
DECISION | CONFIDENCE | REASON

Where:
  DECISION   = BUY, SELL, or HOLD
  CONFIDENCE = integer 0-100
  REASON     = one concise sentence (max 15 words)

Example: BUY | 78 | Strong bullish confluence with ADX momentum and clean OB entry.
Example: HOLD | 30 | RSI overbought and price at Bollinger upper — high reversal risk.
"""
        return prompt

    def _build_context_prompt(self, symbol, features, regime, news) -> str:
        close = round(features.get("close", 0), 4)
        rsi = round(features.get("rsi", 50), 1)
        adx = round(features.get("adx", 0), 1)

        return f"""As a senior XAUUSD trader, briefly summarize the current market bias for {symbol}.

Price: {close} | RSI: {rsi} | ADX: {adx} | Regime: {regime}
News: {news if news else 'None'}

Respond in EXACTLY this format:
BIAS | CONFIDENCE | SUMMARY

Example: BULLISH | 70 | Gold consolidating above key support with DXY weakness.
"""

    # ──────────────────────────────────────────────────────────────────────
    # API CALLS
    # ──────────────────────────────────────────────────────────────────────

    def _call_gemma4(self, prompt: str, max_tokens: int = 80) -> Optional[str]:
        """Call Gemma 4 via Google AI API with model fallback."""
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.15,        # Low temperature — decisive, not creative
                "maxOutputTokens": max_tokens,
                "topP": 0.9,
                "topK": 40,
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            ]
        }

        for model in GEMMA4_MODELS:
            url = f"{GEMINI_API_BASE}/{model}:generateContent?key={self.api_key}"
            try:
                resp = requests.post(url, json=payload, timeout=12)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        text = candidates[0]["content"]["parts"][0]["text"]
                        return text.strip()
                    logger.warning(f"[Gemma4Brain] Empty candidates from {model}")
                elif resp.status_code == 429:
                    logger.warning(f"[Gemma4Brain] Rate limited on {model} — trying next")
                    time.sleep(2)
                elif resp.status_code == 404:
                    logger.warning(f"[Gemma4Brain] Model {model} not found — trying next")
                else:
                    logger.warning(f"[Gemma4Brain] {model} returned {resp.status_code}")
            except requests.Timeout:
                logger.warning(f"[Gemma4Brain] Timeout on {model}")
            except Exception as e:
                logger.warning(f"[Gemma4Brain] Error calling {model}: {e}")

        return None

    # ──────────────────────────────────────────────────────────────────────
    # RESPONSE PARSERS
    # ──────────────────────────────────────────────────────────────────────

    def _parse_response(
        self, raw_text: str, proposed_direction: str
    ) -> Tuple[str, int, str]:
        """Parse Gemma 4 response into (decision, confidence, reasoning)."""
        try:
            # Find the line with the pipe-separated answer
            for line in raw_text.strip().split("\n"):
                line = line.strip()
                if "|" in line:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 3:
                        decision_raw = parts[0].upper()
                        conf_raw = parts[1]
                        reason = "|".join(parts[2:]).strip()

                        # Validate decision
                        if decision_raw in ("BUY", "SELL", "HOLD"):
                            decision = decision_raw
                        elif "BUY" in decision_raw:
                            decision = "BUY"
                        elif "SELL" in decision_raw:
                            decision = "SELL"
                        else:
                            decision = "HOLD"

                        # Parse confidence
                        conf_digits = "".join(c for c in conf_raw if c.isdigit())
                        confidence = int(conf_digits) if conf_digits else 50
                        confidence = max(0, min(100, confidence))

                        return decision, confidence, reason[:100]

        except Exception as e:
            logger.debug(f"[Gemma4Brain] Parse error: {e} | raw: {raw_text[:100]}")

        # Fallback — if we can't parse, be conservative
        return "HOLD", 40, f"Could not parse Gemma 4 response — skipping"

    def _parse_context_response(self, raw_text: str) -> Dict:
        """Parse market context response."""
        try:
            for line in raw_text.strip().split("\n"):
                if "|" in line:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 3:
                        bias = parts[0].upper()
                        if bias not in ("BULLISH", "BEARISH", "NEUTRAL"):
                            bias = "NEUTRAL"
                        conf_digits = "".join(c for c in parts[1] if c.isdigit())
                        conf = int(conf_digits) if conf_digits else 50
                        summary = parts[2].strip()
                        return {"bias": bias, "confidence": conf, "summary": summary,
                                "key_levels": []}
        except Exception:
            pass
        return {"bias": "NEUTRAL", "confidence": 50, "key_levels": [], "summary": ""}


# ── Singleton ──────────────────────────────────────────────────────────────
_brain_instance: Optional[Gemma4Brain] = None

def get_gemma4_brain() -> Gemma4Brain:
    global _brain_instance
    if _brain_instance is None:
        _brain_instance = Gemma4Brain()
    return _brain_instance
