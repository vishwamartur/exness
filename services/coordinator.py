"""
CoordinatorService — The thin orchestrator that replaces InstitutionalStrategy.

Responsibilities:
  1. Periodically triggers SCAN_START events
  2. Aggregates QUANT_SIGNAL + REGIME_UPDATE + SENTIMENT_UPDATE per symbol
  3. Runs PairAgent-style candidate evaluation
  4. Publishes TRADE_CANDIDATE for RiskService to approve
  5. Publishes ACCOUNT_UPDATE + POSITION_UPDATE for dashboard

Replaces: InstitutionalStrategy.run_scan_loop() + PairAgent._analyze() decision logic
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional

import MetaTrader5 as mt5

from core.event_bus import EventBus, Event, EventTypes
from core.base_service import BaseService
from core.mt5_gateway import MT5Gateway
from config import settings
from utils.news_filter import is_news_blackout, get_active_events
from analysis.pattern_memory import get_pattern_memory


logger = logging.getLogger("CoordinatorService")


class CoordinatorService(BaseService):
    """
    Orchestrates scan cycles. Collects analysis results from other services
    and produces TRADE_CANDIDATE events.
    """

    def __init__(self, event_bus: EventBus, gateway: MT5Gateway):
        super().__init__(event_bus)
        self.gateway = gateway

        # Per-symbol aggregation buffers (filled by event handlers)
        self._quant_signals: Dict[str, dict] = {}
        self._regime_data: Dict[str, dict] = {}
        self._sentiment_data: Dict[str, dict] = {}
        self._strategy_signals: Dict[str, dict] = {}
        self._flow_data: Dict[str, dict] = {}
        self._circuit_breakers: Dict[str, bool] = {}

        # Pattern memory
        self.pattern_memory = None
        try:
            self.pattern_memory = get_pattern_memory()
        except Exception as e:
            logger.warning(f"Pattern memory unavailable: {e}")

        # Scan state
        self._scan_count = 0
        self._daily_trade_count = 0
        self._last_reset_date = datetime.now(timezone.utc).date()
        self._last_trade_time: Dict[str, float] = {}

        # Track how many data-ready events we've received this cycle
        self._data_ready_count = 0
        self._expected_symbols = 0

    @property
    def name(self) -> str:
        return "CoordinatorService"

    async def _setup(self):
        # Subscribe to analysis results
        self.bus.subscribe(EventTypes.QUANT_SIGNAL, self._on_quant_signal)
        self.bus.subscribe(EventTypes.REGIME_UPDATE, self._on_regime_update)
        self.bus.subscribe(EventTypes.SENTIMENT_UPDATE, self._on_sentiment)
        self.bus.subscribe("STRATEGY_SIGNAL", self._on_strategy_signal)
        self.bus.subscribe("FLOW_UPDATE", self._on_flow_update)
        self.bus.subscribe("CIRCUIT_BREAKER_UPDATE", self._on_circuit_breaker)
        self.bus.subscribe(EventTypes.TRADE_EXECUTED, self._on_trade_executed)

    async def _run_loop(self):
        """Main scan loop — runs periodically."""
        while self._running:
            await self._run_scan_cycle()

            # Sleep for cooldown
            sleep_time = max(1, settings.COOLDOWN_SECONDS)
            logger.info(f"[SLEEP] Waiting {sleep_time}s...")
            await asyncio.sleep(sleep_time)

    async def _run_scan_cycle(self):
        """Execute one full scan cycle."""
        self._scan_count += 1

        # Daily reset check
        today = datetime.now(timezone.utc).date()
        if today != self._last_reset_date:
            self._daily_trade_count = 0
            self._last_reset_date = today

        # Session filter
        if not self._is_trading_session():
            logger.info("[SCAN] Outside trading session.")
            return

        # Daily limit
        if self._daily_trade_count >= settings.MAX_DAILY_TRADES:
            logger.info("[SCAN] Daily limit reached.")
            return

        # Position limit
        positions = await self.gateway.get_all_positions()
        if len(positions) >= settings.MAX_OPEN_POSITIONS:
            logger.info(f"[SCAN] Max positions ({len(positions)})")
            return

        # News check
        active_news = get_active_events()
        if active_news:
            print(f"[NEWS] {', '.join(active_news)}")

        # Clear aggregation buffers for new cycle
        self._quant_signals.clear()
        self._regime_data.clear()
        self._sentiment_data.clear()
        self._strategy_signals.clear()
        self._flow_data.clear()
        self._data_ready_count = 0
        self._expected_symbols = len(settings.SYMBOLS)

        print(f"\n{'='*60}")
        print(f"  SCAN CYCLE #{self._scan_count} — {len(settings.SYMBOLS)} symbols")
        print(f"{'='*60}")

        # 1. Trigger data fetch + analysis pipeline
        await self.emit(EventTypes.SCAN_START, {
            "count": len(settings.SYMBOLS),
            "cycle": self._scan_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # 2. Wait for analysis pipeline to complete
        # Services will publish QUANT_SIGNAL, REGIME_UPDATE, etc.
        # We wait up to 30 seconds for all signals to arrive
        await self._wait_for_signals(timeout=30)

        # 3. Evaluate candidates from collected signals
        candidates = self._evaluate_candidates()

        # 4. Publish account + position updates for dashboard
        await self._publish_dashboard_updates(positions)

        # 5. Publish scan summary
        scan_status = {}
        for symbol in settings.SYMBOLS:
            if symbol in self._quant_signals:
                qs = self._quant_signals[symbol]
                if qs.get("_is_candidate"):
                    scan_status[symbol] = f"CANDIDATE ({qs.get('direction', '?')})"
                else:
                    scan_status[symbol] = qs.get("_reject_reason", "Low Score")
            else:
                scan_status[symbol] = "No Signal"

        await self.emit(EventTypes.SCAN_SUMMARY, {
            "symbols": scan_status,
            "count": len(settings.SYMBOLS),
            "candidates": len(candidates),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # 6. Submit best candidate
        if candidates:
            candidates.sort(
                key=lambda x: (x.get("score", 0), x.get("ml_prob", 0)),
                reverse=True,
            )
            self._print_candidates(candidates)

            best = candidates[0]
            
            # Pattern Memory RAG check dynamically just for the best candidate
            pattern_info = {"outcome": "UNKNOWN"}
            if self.pattern_memory:
                try:
                    pattern_id = f"{best['symbol']}_{best['direction']}_{int(datetime.now().timestamp())}"
                    from utils.async_utils import run_in_executor
                    # Run embedding comparison in thread
                    mem_result = await run_in_executor(
                        self.pattern_memory.evaluate_candidate, 
                        best['features'], 
                        best['direction']
                    )
                    pattern_info = mem_result
                    logger.debug(f"Pattern Memory info: {mem_result}")

                    # If RAG strongly suggests loss based on historical embedding match, block
                    if getattr(settings, 'USE_PATTERN_MEMORY', False):
                        if mem_result.get('recommendation') == 'AVOID' and mem_result.get('confidence', 0) > 0.8:
                            logger.warning(f"[{best['symbol']}] Pattern Memory strongly suggests AVOID. Skipping.")
                            # You could emit TRADE_REJECTED here or just ignore candidate
                            print("[SCAN] Best candidate rejected by Pattern Memory.")
                            return
                except Exception as e:
                    logger.debug(f"Pattern Memory error: {e}")
                    
            best["pattern_memory"] = pattern_info

            logger.info(f">>> CANDIDATE: {best['symbol']} {best['direction']}")
            await self.emit(EventTypes.TRADE_CANDIDATE, best)
        else:
            print("[SCAN] No candidates found.")

    async def _wait_for_signals(self, timeout: int = 30):
        """Wait for quant signals to arrive from analysis services."""
        start = time.time()
        target = max(1, self._expected_symbols)

        while time.time() - start < timeout:
            if len(self._quant_signals) >= target:
                break
            await asyncio.sleep(0.5)

        elapsed = time.time() - start
        received = len(self._quant_signals)
        logger.debug(f"Received {received}/{target} signals in {elapsed:.1f}s")

    def _evaluate_candidates(self) -> list:
        """Evaluate collected signals to produce trade candidates."""
        candidates = []

        for symbol, qs in self._quant_signals.items():
            # Check Circuit Breakers
            if self._circuit_breakers.get(symbol, False):
                qs["_reject_reason"] = "Circuit Breaker Tripped"
                continue

            direction = qs.get("direction", "NEUTRAL")
            score = qs.get("score", 0)
            ml_prob = qs.get("ml_prob", 0.5)

            # Get strategy and flow modifiers
            strat = self._strategy_signals.get(symbol, {})
            strat_dir = strat.get("direction", "NEUTRAL")
            
            flow = self._flow_data.get(symbol, {})
            flow_dir = flow.get("flow_direction", "NEUTRAL")

            # Basic deterministic confluence
            if strat_dir != "NEUTRAL":
                if strat_dir == direction:
                    score += strat.get("strategy_score", 0)
                elif hasattr(settings, 'BOS_STRICT_MODE') and getattr(settings, 'BOS_STRICT_MODE', True):
                    # Blocking if BOS disagrees in strict mode
                    qs["_reject_reason"] = f"BOS Strategy Conflict ({strat_dir})"
                    continue
                    
            # Flow confirmation
            if flow_dir != "NEUTRAL" and flow_dir == direction:
                score += flow.get("flow_score", 0)

            # Get regime
            regime = self._regime_data.get(symbol, {})
            regime_type = regime.get("regime_type", "UNKNOWN")
            is_tradeable = regime.get("is_tradeable", True)

            if not is_tradeable:
                qs["_reject_reason"] = f"Bad Regime: {regime_type}"
                continue

            # Regime-adaptive min confluence
            regime_key = "RANGING"
            if regime_type in ("TRENDING", "BULL_TREND", "BEAR_TREND"):
                regime_key = "TRENDING"
            elif regime_type in ("VOLATILE", "VOLATILE_HIGH"):
                regime_key = "VOLATILE"

            regime_params = getattr(settings, 'REGIME_PARAMS', {})
            rp = regime_params.get(regime_key, {})
            min_confluence = int(rp.get('MIN_CONFLUENCE_SCORE',
                                       settings.MIN_CONFLUENCE_SCORE))

            if score < min_confluence:
                qs["_reject_reason"] = f"Low Score ({score} < {min_confluence})"
                continue

            # ML prob filter
            prob = ml_prob
            if direction == "SELL":
                prob = 1.0 - prob
            if score < 5 and prob < settings.RF_PROB_THRESHOLD:
                qs["_reject_reason"] = f"Low ML ({prob:.2f})"
                continue

            # Get features for ATR-based SL/TP
            features = qs.get("features", {})
            atr = features.get("atr", 0)
            if atr <= 0:
                qs["_reject_reason"] = "No ATR"
                continue

            # Calculate SL/TP
            sl_dist = atr * settings.ATR_SL_MULTIPLIER
            if getattr(settings, 'SMART_EXIT_ENABLED', False):
                tp_dist = atr * getattr(settings, 'TP_SAFETY_ATR', 10.0)
            else:
                tp_dist = atr * settings.ATR_TP_MULTIPLIER

            # R:R check
            if sl_dist > 0:
                rr = tp_dist / sl_dist
                if rr < settings.MIN_RISK_REWARD_RATIO:
                    qs["_reject_reason"] = f"Low R:R ({rr:.2f})"
                    continue

            # Get sentiment
            sentiment = self._sentiment_data.get(symbol, {})

            # Build candidate
            candidate = {
                "symbol": symbol,
                "direction": direction,
                "score": score,
                "ml_prob": ml_prob,
                "ensemble_score": qs.get("ensemble_score", 0),
                "regime": regime.get("regime", "UNKNOWN"),
                "regime_type": regime_type,
                "sl_distance": sl_dist,
                "tp_distance": tp_dist,
                "scaling_factor": 1.0,
                "emotion_state": sentiment.get("emotion_state", "NEUTRAL"),
                "emotion_score": sentiment.get("emotion_score", 0.5),
                "details": qs.get("details", {}),
                "features": features,
            }

            qs["_is_candidate"] = True
            candidates.append(candidate)

        return candidates

    # ─── Event Handlers ───────────────────────────────────────────────────

    async def _on_quant_signal(self, event: Event):
        symbol = event.payload.get("symbol")
        if symbol:
            self._quant_signals[symbol] = event.payload

    async def _on_regime_update(self, event: Event):
        symbol = event.payload.get("symbol")
        if symbol:
            self._regime_data[symbol] = event.payload

    async def _on_sentiment(self, event: Event):
        symbol = event.payload.get("symbol")
        if symbol:
            self._sentiment_data[symbol] = event.payload

    async def _on_strategy_signal(self, event: Event):
        symbol = event.payload.get("symbol")
        if symbol:
            self._strategy_signals[symbol] = event.payload

    async def _on_flow_update(self, event: Event):
        symbol = event.payload.get("symbol")
        if symbol:
            self._flow_data[symbol] = event.payload

    async def _on_circuit_breaker(self, event: Event):
        symbol = event.payload.get("symbol")
        if symbol:
            self._circuit_breakers[symbol] = event.payload.get("tripped", False)

    async def _on_trade_executed(self, event: Event):
        self._daily_trade_count += 1
        symbol = event.payload.get("symbol", "")
        self._last_trade_time[symbol] = time.time()

    # ─── Dashboard Updates ────────────────────────────────────────────────

    async def _publish_dashboard_updates(self, positions):
        """Publish account + position info for the dashboard."""
        try:
            # Positions
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

            # Account
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

    # ─── Helpers ──────────────────────────────────────────────────────────

    def _is_trading_session(self) -> bool:
        if not settings.SESSION_FILTER:
            return True
        now = datetime.now(timezone.utc)
        current_time = now.hour + now.minute / 60.0
        for _, times in settings.TRADE_SESSIONS.items():
            if times['start'] <= current_time < times['end']:
                return True
        return False

    def _print_candidates(self, candidates):
        print(f"\n{'-'*60}")
        print(f"  {'Symbol':>10} | {'Dir':>4} | Sc | ML")
        print(f"{'-'*60}")
        for c in candidates[:5]:
            sym = c['symbol']
            d = c['direction']
            print(f"    {sym:>10} | {d:>4} | {c['score']} | {c['ml_prob']:.2f}")
