"""
MiroFish Agent — The 'Simulator' Agent
========================================
Swarm Intelligence Market Prediction Agent.

Uses MiroFish to run multi-agent simulations that predict market movements.
Generates seed documents from live market data, runs simulations in background,
and provides cached prediction signals to the trading pipeline.
"""

import re
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Tuple

from config import settings
from analysis.mirofish_client import MiroFishClient
from market_data.massive_client import get_rest_client, mt5_to_massive
from utils.shared_state import SharedState

logger = logging.getLogger(__name__)


class MiroFishAgent:
    """
    The 'Simulator' Agent — Swarm Intelligence Market Prediction.
    
    Responsibilities:
    1. Seed Data Generation from live market data
    2. Background MiroFish simulation orchestration
    3. Prediction signal extraction from reports
    4. Cached predictions with TTL
    """
    
    def __init__(self):
        self.client = MiroFishClient(
            base_url=settings.MIROFISH_API_URL,
            timeout=120
        )
        self.state = SharedState()
        self._cache: Dict[str, Dict] = {}   # key -> {prediction, timestamp}
        self._cache_ttl = settings.MIROFISH_CACHE_MINUTES * 60  # seconds
        self._bg_thread: Optional[threading.Thread] = None
        self._bg_lock = threading.Lock()
        self._running = False
        self._last_simulation_time = 0
        
        # Check availability on init
        if self.client.is_available():
            print("[MIROFISH] Agent initialized — MiroFish service is online")
        else:
            print("[MIROFISH] Agent initialized — MiroFish service is OFFLINE (will retry)")
    
    # ── Public API ────────────────────────────────────────────────────────
    
    def get_prediction(self, symbols: List[str] = None,
                       market_data: Dict[str, Any] = None,
                       news_events: List[str] = None) -> Optional[Dict]:
        """
        Get the latest market prediction from MiroFish.
        
        Returns cached prediction if available and fresh.
        Otherwise triggers a background simulation.
        
        Returns:
            Dict with keys:
                - sentiment: 'BULLISH' | 'BEARISH' | 'NEUTRAL'
                - confidence: 0-100
                - per_asset: {symbol: {'direction': str, 'confidence': int}}
                - reasoning: str
                - timestamp: float
            Or None if no prediction available yet.
        """
        cache_key = "global_market"
        
        # Check cache
        cached = self._cache.get(cache_key)
        if cached and (time.time() - cached["timestamp"]) < self._cache_ttl:
            return cached["prediction"]
        
        # Also check SharedState for persistence across restarts
        stored = self.state.get("mirofish_prediction")
        if stored and (time.time() - stored.get("timestamp", 0)) < self._cache_ttl:
            self._cache[cache_key] = {
                "prediction": stored,
                "timestamp": stored["timestamp"]
            }
            return stored
        
        # Trigger background simulation if not already running
        self._trigger_background_simulation(symbols, market_data, news_events)
        
        # Return stale cache if available (better than nothing)
        if cached:
            return cached["prediction"]
        if stored:
            return stored
            
        return None
    
    def get_symbol_signal(self, symbol: str) -> Tuple[str, int]:
        """
        Get MiroFish prediction for a specific symbol.
        
        Returns:
            (direction, confidence) tuple.
            direction: 'BULLISH', 'BEARISH', or 'NEUTRAL'
            confidence: 0-100
        """
        prediction = self.get_prediction()
        if not prediction:
            return "NEUTRAL", 0
        
        # Check per-asset predictions first
        per_asset = prediction.get("per_asset", {})
        
        # Try exact match and stripped match (handle suffixes like 'm', 'c')
        for try_sym in [symbol, _strip_suffix(symbol)]:
            if try_sym in per_asset:
                asset_pred = per_asset[try_sym]
                return asset_pred.get("direction", "NEUTRAL"), asset_pred.get("confidence", 0)
        
        # Fall back to global sentiment
        return prediction.get("sentiment", "NEUTRAL"), prediction.get("confidence", 0)
    
    def get_confluence_bonus(self, symbol: str, trade_direction: str) -> int:
        """
        Calculate confluence bonus from MiroFish prediction.
        
        Returns:
            +1 if MiroFish strongly agrees with trade direction
             0 otherwise
        """
        direction, confidence = self.get_symbol_signal(symbol)
        
        if confidence < 60:
            return 0
        
        # Map trade direction to expected MiroFish direction
        mf_agrees = (
            (trade_direction.upper() in ("BUY", "LONG") and direction == "BULLISH") or
            (trade_direction.upper() in ("SELL", "SHORT") and direction == "BEARISH")
        )
        
        if mf_agrees:
            return min(settings.MIROFISH_MAX_CONFLUENCE_BONUS, 1)
        
        return 0
    
    def is_available(self) -> bool:
        """Check if MiroFish service is reachable."""
        return self.client.is_available()
    
    # ── Background Simulation ─────────────────────────────────────────────
    
    def _trigger_background_simulation(self, symbols=None, market_data=None, 
                                        news_events=None):
        """Start a background simulation if not already running."""
        with self._bg_lock:
            if self._running:
                return
            
            # Rate limit — don't start new sim if last one was recent
            min_interval = self._cache_ttl * 0.5  # At least half the cache TTL
            if time.time() - self._last_simulation_time < min_interval:
                return
            
            self._running = True
        
        self._bg_thread = threading.Thread(
            target=self._run_simulation_bg,
            args=(symbols, market_data, news_events),
            daemon=True,
            name="MiroFish-Simulation"
        )
        self._bg_thread.start()
        logger.info("[MIROFISH] Background simulation thread started")
    
    def _run_simulation_bg(self, symbols=None, market_data=None, news_events=None):
        """Background thread: runs the full MiroFish pipeline."""
        try:
            # Check service availability
            if not self.client.is_available():
                logger.warning("[MIROFISH] Service unavailable, skipping simulation")
                return
            
            # Generate seed document
            seed_doc = self._generate_seed_document(symbols, market_data, news_events)
            requirement = self._generate_requirement(symbols)
            
            logger.info(f"[MIROFISH] Running simulation with {len(seed_doc)} char seed document")
            
            # Run full pipeline
            report = self.client.run_full_pipeline(
                seed_text=seed_doc,
                requirement=requirement,
                max_rounds=settings.MIROFISH_SIMULATION_ROUNDS,
                project_name=f"Market_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}"
            )
            
            if report:
                # Parse and cache the prediction
                prediction = self._parse_report(report)
                prediction["timestamp"] = time.time()
                
                self._cache["global_market"] = {
                    "prediction": prediction,
                    "timestamp": time.time()
                }
                
                # Persist to SharedState
                self.state.set("mirofish_prediction", prediction)
                
                self._last_simulation_time = time.time()
                
                logger.info(
                    f"[MIROFISH] Prediction cached: {prediction['sentiment']} "
                    f"(confidence: {prediction['confidence']}%)"
                )
                print(
                    f"[MIROFISH] ✓ Prediction: {prediction['sentiment']} "
                    f"| Confidence: {prediction['confidence']}% "
                    f"| Assets: {len(prediction.get('per_asset', {}))} signals"
                )
            else:
                logger.warning("[MIROFISH] Pipeline returned no report")
                
        except Exception as e:
            logger.error(f"[MIROFISH] Background simulation error: {e}")
        finally:
            with self._bg_lock:
                self._running = False
    
    # ── Seed Document Generation ──────────────────────────────────────────
    
    def _generate_seed_document(self, symbols=None, market_data=None,
                                 news_events=None) -> str:
        """
        Create a markdown market analysis document as seed data for MiroFish.
        
        Combines:
        - Current prices and trends across all tracked symbols
        - Regime detection results
        - Active news events
        - Recent trading performance
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        symbols = symbols or settings.SYMBOLS or ["EURUSD", "BTCUSD", "XAUUSD"]
        
        sections = [
            f"# Financial Market Analysis Report",
            f"**Generated:** {now}",
            f"**Scope:** {len(symbols)} instruments across Forex, Crypto, and Commodities\n",
        ]
        
        # Market data section
        if market_data:
            sections.append("## Current Market Conditions\n")
            for sym in symbols:
                sym_data = market_data.get(sym, {})
                if not sym_data:
                    continue
                    
                df = sym_data.get("H1")
                if df is None:
                    df = sym_data.get("M15")
                if df is None:
                    df = sym_data.get("M1")
                if df is not None and len(df) > 0:
                    last = df.iloc[-1]
                    close = last.get("close", 0)
                    
                    # Calculate simple metrics
                    if len(df) >= 20:
                        sma20 = df["close"].tail(20).mean()
                        change_pct = ((close - df["close"].iloc[-20]) / df["close"].iloc[-20]) * 100
                        trend = "UPTREND" if close > sma20 else "DOWNTREND"
                    else:
                        sma20 = close
                        change_pct = 0
                        trend = "UNKNOWN"
                else:
                    # Fallback to Massive.com REST API if MT5 data is missing
                    if getattr(settings, 'MASSIVE_ENABLED', False):
                        massive_ticker = mt5_to_massive(sym)
                        if massive_ticker:
                            try:
                                massive_client = get_rest_client()
                                massive_df = massive_client.get_aggregates(sym, "H1", n_bars=20)
                                if massive_df is not None and len(massive_df) > 0:
                                    last = massive_df.iloc[-1]
                                    close = last.get("close", 0)
                                    if len(massive_df) >= 20:
                                        sma20 = massive_df["close"].mean()
                                        change_pct = ((close - massive_df["close"].iloc[0]) / massive_df["close"].iloc[0]) * 100
                                        trend = "UPTREND" if close > sma20 else "DOWNTREND"
                                    else:
                                        sma20 = close
                                        change_pct = 0
                                        trend = "UNKNOWN"
                                else:
                                    continue
                            except Exception as e:
                                logger.debug(f"[MIROFISH] Massive fallback failed for {sym}: {e}")
                                continue
                        else:
                            continue
                    else:
                        continue
                    
                    sections.append(
                        f"### {sym}\n"
                        f"- **Price:** {close:.5f}\n"
                        f"- **20-period SMA:** {sma20:.5f}\n"
                        f"- **20-period Change:** {change_pct:+.2f}%\n"
                        f"- **Trend:** {trend}\n"
                    )
        else:
            # No live data — provide generic market context
            sections.append(
                "## Market Context\n"
                "Analysis covers major forex pairs (EUR/USD, GBP/USD, USD/JPY), "
                "cryptocurrencies (BTC, ETH, LTC, XRP), and commodities (Gold, Silver).\n"
                "The analysis should consider current macro trends, central bank policies, "
                "and cross-asset correlations.\n"
            )
        
        # Regime information from SharedState
        sections.append("## Market Regimes\n")
        for sym in symbols[:10]:  # Limit to avoid huge docs
            regime_data = self.state.get(f"regime_{sym}")
            if regime_data:
                regime = regime_data.get("regime", "UNKNOWN")
                sections.append(f"- **{sym}:** {regime}")
        
        if not any("regime" in s.lower() for s in sections[4:]):
            sections.append("- Regime data will be updated from live market feed\n")
        
        # News events
        if news_events:
            sections.append("\n## Active Economic Events\n")
            for event in news_events[:15]:
                sections.append(f"- {event}")
        else:
            sections.append(
                "\n## Macro Context\n"
                "Consider the impact of upcoming central bank decisions, "
                "employment data, inflation reports, and geopolitical events "
                "on the tracked instruments.\n"
            )
        
        # Trading performance from SharedState
        perf = self.state.get("trading_performance")
        if perf:
            sections.append(
                f"\n## Recent Trading Performance\n"
                f"- Win Rate: {perf.get('win_rate', 'N/A')}%\n"
                f"- Net P&L: ${perf.get('net_pnl', 'N/A')}\n"
                f"- Total Trades: {perf.get('total_trades', 'N/A')}\n"
            )
        
        return "\n".join(sections)
    
    def _generate_requirement(self, symbols=None) -> str:
        """Generate the simulation requirement (prediction question)."""
        symbols = symbols or settings.SYMBOLS or ["EURUSD", "BTCUSD", "XAUUSD"]
        sym_str = ", ".join(symbols[:10])
        
        return (
            f"Predict the short-term (next 1-4 hours) price direction for the following "
            f"financial instruments: {sym_str}. "
            f"For each instrument, determine if the price is more likely to go UP (BULLISH), "
            f"DOWN (BEARISH), or remain SIDEWAYS (NEUTRAL). "
            f"Consider cross-asset correlations, institutional flow patterns, "
            f"and macro-economic sentiment. "
            f"Provide a confidence level (0-100) for each prediction."
        )
    
    # ── Report Parsing ────────────────────────────────────────────────────
    
    def _parse_report(self, report: Dict) -> Dict:
        """
        Extract structured prediction signals from a MiroFish report.
        
        Returns:
            Dict with:
                - sentiment: overall market sentiment
                - confidence: overall confidence (0-100)
                - per_asset: per-symbol predictions
                - reasoning: key reasoning text
        """
        content = ""
        
        # Extract report text from various possible formats
        if isinstance(report, dict):
            content = (
                report.get("content", "") or 
                report.get("report_text", "") or
                report.get("summary", "") or
                str(report)
            )
            
            # Check for chapters/sections
            chapters = report.get("chapters", [])
            if chapters:
                for ch in chapters:
                    if isinstance(ch, dict):
                        content += "\n" + (ch.get("content", "") or ch.get("text", ""))
                    elif isinstance(ch, str):
                        content += "\n" + ch
        elif isinstance(report, str):
            content = report
        
        content_lower = content.lower()
        
        # Extract overall sentiment
        sentiment = "NEUTRAL"
        if any(w in content_lower for w in ["bullish", "upward", "rise", "positive outlook", "buy signal"]):
            if any(w in content_lower for w in ["bearish", "downward", "fall", "negative outlook", "sell signal"]):
                sentiment = "NEUTRAL"  # Mixed signals
            else:
                sentiment = "BULLISH"
        elif any(w in content_lower for w in ["bearish", "downward", "fall", "negative outlook", "sell signal"]):
            sentiment = "BEARISH"
        
        # Extract confidence from text patterns
        confidence = self._extract_confidence(content)
        
        # Extract per-asset predictions
        per_asset = self._extract_per_asset(content)
        
        # Extract key reasoning (first 500 chars of substantive content)
        reasoning = self._extract_reasoning(content)
        
        return {
            "sentiment": sentiment,
            "confidence": confidence,
            "per_asset": per_asset,
            "reasoning": reasoning,
            "raw_length": len(content),
        }
    
    def _extract_confidence(self, text: str) -> int:
        """Extract a confidence score from report text."""
        # Look for explicit confidence patterns
        patterns = [
            r'confidence[:\s]+(\d{1,3})%',
            r'confidence[:\s]+(\d{1,3})\s*(?:out of|/)\s*100',
            r'(\d{1,3})%\s*confidence',
            r'confidence\s*(?:level|score)?[:\s]+(\d{1,3})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                val = int(match.group(1))
                if 0 <= val <= 100:
                    return val
        
        # Heuristic: count sentiment keywords to estimate confidence
        text_lower = text.lower()
        strong_words = ["strong", "high confidence", "very likely", "significant", "decisive"]
        moderate_words = ["moderate", "likely", "probable", "expected"]
        weak_words = ["uncertain", "unclear", "mixed", "volatile", "low confidence"]
        
        strong_count = sum(1 for w in strong_words if w in text_lower)
        moderate_count = sum(1 for w in moderate_words if w in text_lower)
        weak_count = sum(1 for w in weak_words if w in text_lower)
        
        if strong_count > weak_count:
            return min(85, 60 + strong_count * 5)
        elif weak_count > strong_count:
            return max(20, 50 - weak_count * 5)
        else:
            return 50 + moderate_count * 3
    
    def _extract_per_asset(self, text: str) -> Dict[str, Dict]:
        """Extract per-symbol predictions from report text."""
        per_asset = {}
        
        # Known symbols to look for
        all_symbols = list(settings.ALL_BASE_SYMBOLS) if hasattr(settings, 'ALL_BASE_SYMBOLS') else [
            "EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "ETHUSD", 
            "XAUUSD", "LTCUSD", "XRPUSD"
        ]
        
        text_lower = text.lower()
        
        for symbol in all_symbols:
            sym_lower = symbol.lower()
            # Also try with slash: EUR/USD
            sym_slash = sym_lower[:3] + "/" + sym_lower[3:] if len(sym_lower) >= 6 else sym_lower
            
            # Find symbol mentions and surrounding context
            for pattern in [sym_lower, sym_slash]:
                idx = text_lower.find(pattern)
                if idx == -1:
                    continue
                
                # Use line-level window to avoid cross-contamination
                # Find the start and end of the line containing the symbol
                line_start = text_lower.rfind("\n", 0, idx)
                line_start = line_start + 1 if line_start != -1 else 0
                line_end = text_lower.find("\n", idx)
                if line_end == -1:
                    line_end = len(text_lower)
                
                window = text_lower[line_start:line_end]
                
                direction = "NEUTRAL"
                conf = 50
                
                if any(w in window for w in ["bullish", "buy", "upward", "rise", "long"]):
                    direction = "BULLISH"
                    conf = 65
                elif any(w in window for w in ["bearish", "sell", "downward", "fall", "short"]):
                    direction = "BEARISH"
                    conf = 65
                
                # Check for confidence near symbol
                conf_match = re.search(r'(\d{1,3})%', window)
                if conf_match:
                    val = int(conf_match.group(1))
                    if 0 <= val <= 100:
                        conf = val
                
                if direction != "NEUTRAL" or conf > 50:
                    per_asset[symbol] = {
                        "direction": direction,
                        "confidence": conf
                    }
                break  # Found this symbol, move on
        
        return per_asset
    
    def _extract_reasoning(self, text: str) -> str:
        """Extract key reasoning summary from report text."""
        if not text:
            return "No report available"
        
        # Try to find a conclusion or summary section
        for marker in ["conclusion", "summary", "key findings", "overall"]:
            idx = text.lower().find(marker)
            if idx != -1:
                # Extract up to 500 chars from this section
                excerpt = text[idx:idx + 500].strip()
                # Clean up to next paragraph
                para_end = excerpt.find("\n\n")
                if para_end > 50:
                    return excerpt[:para_end].strip()
                return excerpt
        
        # Fall back to first substantive paragraph
        paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 50]
        if paragraphs:
            return paragraphs[0][:500]
        
        return text[:500] if text else "No reasoning available"


def _strip_suffix(symbol: str) -> str:
    """Strip broker suffixes like 'm', 'c' from symbol names."""
    if symbol and symbol[-1] in ('m', 'c', 'z'):
        return symbol[:-1]
    return symbol
