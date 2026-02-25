import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional, Any, Tuple
import MetaTrader5 as mt5

from config import settings
from utils.async_utils import run_in_executor
from market_data import loader
from utils.trade_journal import TradeJournal
from utils.trade_journal import TradeJournal
from strategy.bos_strategy import BOSStrategy
from utils.news_filter import is_news_blackout
from analysis.sentiment_analyzer import get_sentiment_analyzer
from analysis.pattern_memory import get_pattern_memory
from analysis.rl_trader import get_rl_trader

# Setup logger
logger = logging.getLogger(__name__)
try:
    logger.setLevel(logging.DEBUG if settings.DEBUG_MODE else logging.INFO)
except:
    logger.setLevel(logging.INFO)

class PairAgent:
    """
    Dedicated AI Agent for a single currency pair.
    Manages state, performance, and scanning for its specific symbol.
    """
    def __init__(self, symbol: str, quant_agent, analyst_agent, risk_manager):
        self.symbol = symbol
        self.quant = quant_agent
        self.analyst = analyst_agent
        self.risk_manager = risk_manager
        self.journal = TradeJournal()
        self.sentiment_analyzer = get_sentiment_analyzer()
        self.pattern_memory = get_pattern_memory()  # RAG for historical patterns
        self.rl_trader = get_rl_trader()  # Reinforcement Learning agent
        
        # State
        self.is_active = True
        self.consecutive_losses = 0
        self.total_pnl = 0.0
        self.last_scan_time = 0
        self.last_trade_time = 0
        self.regime = "UNKNOWN"
        self.last_atr_time = 0
        self.latest_atr = 0.0
        self.last_pattern_id = None  # Track last stored pattern for outcome update
        
        # Performance Thresholds (Self-Correction)
        self.max_consecutive_losses = 3

        # BOS Strategy
        self.bos = BOSStrategy()

        # Load capabilities
        self.timeframe = settings.TIMEFRAME
        
        self._load_state()
        print(f"[{self.symbol}] Agent initialized. Losses: {self.consecutive_losses}")

    def _load_state(self):
        """Loads state from the trade journal."""
        trades = self.journal.get_recent_trades(self.symbol, limit=10)
        losses = 0
        for t in trades:
            if t['outcome'] == 'LOSS':
                losses += 1
            else:
                break
        self.consecutive_losses = losses
        
        if self.consecutive_losses >= self.max_consecutive_losses:
             self.is_active = False
             print(f"[{self.symbol}] WARN: Agent restored in PAUSED state (Circuit Breaker).")

    async def scan(self) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        Orchestrates the scanning process for this specific pair.
        Returns (candidate_dict, status_message)
        """
        if not self.is_active:
            return None, "Inactive (Circuit Breaker)"

        # 0. News Filter
        if getattr(settings, 'NEWS_FILTER_ENABLE', False):
            is_blackout, event_name = is_news_blackout(self.symbol)
            if is_blackout:
                return None, f"News Blackout ({event_name})"

        # 1. Pre-Scan Risk Check
        # Check cooldown
        if self.last_trade_time > 0 and (projected_time_now() - self.last_trade_time < settings.COOLDOWN_SECONDS):
             return None, "Cooldown"
             
        allowed, reason = self.risk_manager.check_pre_scan(self.symbol)
        if not allowed:
            return None, f"Risk Block: {reason}"

        # 2. Fetch Data
        data, error = await self._fetch_data()
        if not data:
            return None, error

        # 3. Analysis (Quant + Market Regime)
        candidate, error = await self._analyze(data)
        if not candidate:
             return None, error

        # 4. Success
        return candidate, f"CANDIDATE ({candidate['direction']})"

    async def _fetch_data(self) -> Tuple[Optional[Dict[str, Any]], str]:
        try:
            # Check spread first (optimization)
            # We can't easily check spread without MT5 connection content, but loader usually assumes connection.
            # InstitutionalStrategy had _check_spread logic. We'll rely on RiskManager for now or add it here?
            # Let's add simple spread check if possible, or assume caller handles it.
            # Ideally PairAgent is autonomous.
            
            # Fetch Primary Data
            df = await run_in_executor(loader.get_historical_data, self.symbol, self.timeframe, 2000) # Increased for Scalping Indicators (M1)
            
            if df is None or len(df) < 100:
                return None, "Insufficient Data"

            data_dict = {self.timeframe: df}
            
            # Fetch Multi-Timeframe Data if enabled
            if getattr(settings, 'M5_TREND_FILTER', False):
                 m5 = await run_in_executor(loader.get_historical_data, self.symbol, "M5", 100)
                 if m5 is not None:
                    data_dict['M5'] = m5
            
            if settings.H1_TREND_FILTER:
                 h1 = await run_in_executor(loader.get_historical_data, self.symbol, "H1", 100)
                 if h1 is not None:
                    data_dict['H1'] = h1
            
            if settings.H4_TREND_FILTER:
                 h4 = await run_in_executor(loader.get_historical_data, self.symbol, "H4", 60)
                 if h4 is not None:
                    data_dict['H4'] = h4
                 
            return data_dict, "OK"
            
        except Exception as e:
            # logger.error(f"[{self.symbol}] Data Fetch Error: {e}")
            return None, f"Fetch Error: {str(e)}"

    async def _analyze(self, data_dict: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], str]:
        # 1. Quant Analysis
        q_res = await run_in_executor(self.quant.analyze, self.symbol, data_dict)
        if not q_res:
            return None, "Quant Scan Failed"
        
        # Cache ATR from analysis
        if 'features' in q_res and 'atr' in q_res['features']:
            self.latest_atr = q_res['features']['atr']
            self.last_atr_time = projected_time_now()
            
        # 2. Market Regime Analysis
        # Use DF with features from QuantAgent
        df_scan = q_res.get('data')
        if df_scan is None: df_scan = data_dict.get(self.timeframe)
        
        analysis = self.analyst.analyze_session(self.symbol, df_scan)
        regime = analysis['regime']
        self.regime = regime # Update state for active trade management
        
        # 3. BOS Analysis (Priority)
        bos_res = {}
        if getattr(settings, 'BOS_ENABLE', False):
             bos_res = self.bos.analyze(df_scan)
        
        # 4. Pattern Recognition Analysis
        from analysis.pattern_recognizer import get_pattern_recognizer
        pattern_recognizer = get_pattern_recognizer()
        pattern_analysis = pattern_recognizer.analyze(df_scan)
        
        # 5. Construct Candidate using ML or BOS
        # Filter: Minimum Score
        score = q_res.get('score', 0)
        
        # Basic Filter
        if score < settings.MIN_CONFLUENCE_SCORE:
            return None, f"Low Score ({score})"
            
        # Sureshot Mode Filter (Only boost, don't block if > MIN)
        # if score < settings.SURESHOT_MIN_SCORE:
        #    return None, f"Below Sureshot ({score} < {settings.SURESHOT_MIN_SCORE})"
            
        # ML Prob Filter (Directional)
        prob = q_res.get('ml_prob', 0.5)
        # Fix: Use q_res['direction'] as candidate is not defined yet
        if q_res.get('direction') == 'SELL':
            prob = 1.0 - prob
            
        # Only enforce ML threshold if Score is not Sureshot
        if score < 5:
            if prob < settings.RF_PROB_THRESHOLD:
                 return None, f"Low ML Prob ({prob:.2f} < {settings.RF_PROB_THRESHOLD})"
                 
        # Note: candidate['ml_prob'] assignment removed here, will be set during creation below
             
        # AI-Powered Market Regime Filter (Enhanced)
        signal = q_res.get('direction', 'NEUTRAL')
        
        # Get detailed regime classification
        from analysis.regime import RegimeDetector
        regime_detector = RegimeDetector()
        regime_type, regime_details = regime_detector.get_regime(df_scan)
        
        # Skip trades in bad regimes (RANGING, VOLATILE_HIGH)
        if not regime_detector.is_tradeable_regime(regime_type):
            return None, f"Bad Regime: {regime_type} - Skip trading"
        
        # Score regime alignment with trade direction
        regime_score, regime_reason = regime_detector.get_regime_score(regime_type, signal)
        
        # Require minimum regime score for the direction
        if regime_score < 5:
            return None, f"Weak Regime ({regime_type}: {regime_score}/10) - {regime_reason}"
        
        # Log regime info for debugging
        print(f"[{self.symbol}] Regime: {regime_type} | Score: {regime_score}/10 | {regime_reason}")

        # Construct
        atr = q_res['features'].get('atr', 0)

        # 2. Volatility-Adaptive Entry: skip dead markets
        if atr > 0:
            if self.symbol in getattr(settings, 'SYMBOLS_CRYPTO', []):
                atr_min = getattr(settings, 'VOLATILITY_ATR_MIN_CRYPTO', 50.0)
            elif self.symbol in getattr(settings, 'SYMBOLS_COMMODITIES', []):
                atr_min = getattr(settings, 'VOLATILITY_ATR_MIN_COMMODITY', 0.5)
            else:
                atr_min = getattr(settings, 'VOLATILITY_ATR_MIN', 0.00015)
            if atr < atr_min:
                return None, f"Low Volatility (ATR {atr:.6f} < {atr_min})"

        # 3. Spread-Adjusted TP/SL
        tick = mt5.symbol_info_tick(self.symbol)
        spread_price = (tick.ask - tick.bid) if tick else 0.0

        sl_dist = atr * settings.ATR_SL_MULTIPLIER
        tp_dist = atr * settings.ATR_TP_MULTIPLIER + spread_price  # net of spread cost

        # Enforce minimum TP > 3x spread (ensures net profit is positive)
        min_tp_spread_ratio = getattr(settings, 'MIN_TP_SPREAD_RATIO', 3.0)
        if spread_price > 0 and tp_dist < spread_price * min_tp_spread_ratio:
            return None, f"TP too small vs spread ({tp_dist:.5f} < {spread_price * min_tp_spread_ratio:.5f})"
        
        # Commission-aware minimum profit check
        # Estimate commission cost and ensure TP covers it
        commission_per_lot = getattr(settings, 'COMMISSION_PER_LOT', 7.0)
        # For 0.01 lot, commission is ~$0.07, need at least 5-7 pips to cover
        min_profit_pips = getattr(settings, 'MIN_PROFIT_TARGET_PIPS', 5.0)
        
        # Convert pips to price distance
        if 'JPY' in self.symbol:
            pip_value = 0.01
        elif self.symbol in getattr(settings, 'SYMBOLS_CRYPTO', []):
            pip_value = 1.0  # Crypto uses whole numbers
        else:
            pip_value = 0.0001
        
        min_profit_distance = min_profit_pips * pip_value
        if tp_dist < min_profit_distance:
            return None, f"TP too small for commission ({tp_dist:.5f} < {min_profit_distance:.5f} = {min_profit_pips} pips)"
        min_tp = spread_price * 3
        if tp_dist < min_tp:
            tp_dist = min_tp
        
        # Enforce Min Risk:Reward
        if sl_dist > 0:
            rr_ratio = tp_dist / sl_dist
            if rr_ratio < settings.MIN_RISK_REWARD_RATIO:
                return None, f"Low R:R ({rr_ratio:.2f} < {settings.MIN_RISK_REWARD_RATIO})"
        
        # Pattern Recognition Filter
        should_trade_pattern, pattern_confidence = pattern_recognizer.get_pattern_signal(
            pattern_analysis, signal
        )
        
        if not should_trade_pattern:
            return None, f"Pattern Conflict - {pattern_analysis.get('count', 0)} patterns detected against trade"
        
        # Boost score if patterns support the trade
        if pattern_confidence > 0.6:
            score = min(6, score + 1)  # Boost score by 1 (max 6)
            print(f"[{self.symbol}] Pattern boost: +1 score (confidence: {pattern_confidence:.2f})")
        
        # News Sentiment Analysis
        sentiment = self.sentiment_analyzer.get_sentiment_recommendation({'score': 0})  # Placeholder
        # In async context, we'd use: sentiment = await self.sentiment_analyzer.get_sentiment(self.symbol)
        
        # Check sentiment alignment
        sentiment_aligned = self.sentiment_analyzer.should_trade_with_sentiment(
            self.symbol, signal, {'score': 0, 'confidence': 0}  # Placeholder - integrate real sentiment
        )
        
        if not sentiment_aligned:
            return None, f"Sentiment Conflict - News sentiment against {signal}"
        
        # RAG: Retrieve similar historical patterns
        rag_context = self.pattern_memory.get_pattern_context(df_scan, self.symbol, signal)
        
        # Check RAG recommendation
        if rag_context['recommendation'] == 'AVOID' and rag_context['historical_win_rate'] < 0.25:
            return None, f"RAG Block - Similar patterns have {rag_context['historical_win_rate']*100:.0f}% win rate"
        
        # Boost/reduce ML probability based on RAG
        rag_confidence_boost = rag_context.get('confidence_boost', 1.0)
        prob = min(0.95, prob * rag_confidence_boost)  # Apply RAG confidence boost
        
        if rag_context['recommendation'] in ['PROCEED', 'NEUTRAL']:
            print(f"[{self.symbol}] RAG: {rag_context['context_text']}")
        
        # RL Agent Signal
        rl_state = self.rl_trader.extract_state(df_scan, symbol=self.symbol)
        rl_action, rl_confidence = self.rl_trader.get_trade_signal(rl_state, confidence_threshold=0.6)
        
        # Check RL alignment with ensemble
        rl_aligned = False
        if rl_action == signal or rl_action == 'HOLD':
            rl_aligned = True
            print(f"[{self.symbol}] RL: {rl_action} (conf: {rl_confidence:.2f}) - Aligned")
        elif rl_action in ['BUY', 'SELL'] and rl_action != signal:
            print(f"[{self.symbol}] RL: {rl_action} (conf: {rl_confidence:.2f}) - Conflicts with {signal}")
            # Reduce confidence if RL disagrees
            prob *= 0.8
        
        candidate = {
            'symbol': self.symbol,
            'direction': signal,
            'score': score,
            'entry_price': 0,          # Filled at execution
            'entry_type': 'MARKET',    # Default; BOS overrides to LIMIT
            'ensemble_score': q_res.get('ensemble_score', 0),
            'agreement_count': q_res.get('agreement_count', 0),
            'model_votes': q_res.get('model_votes', {}),
            'ml_prob': prob,
            'regime': regime,
            'regime_type': regime_type,
            'regime_score': regime_score,
            'pattern_analysis': pattern_analysis,
            'pattern_confidence': pattern_confidence,
            'sentiment': sentiment,
            'rag_context': rag_context,
            'rag_win_rate': rag_context.get('historical_win_rate', 0.5),
            'rl_action': rl_action,
            'rl_confidence': rl_confidence,
            'rl_aligned': rl_aligned,
            'sl_distance': sl_dist,
            'tp_distance': tp_dist,
            'scaling_factor': 1.0,
            'm5_trend': q_res.get('m5_trend', 0),  # Log M5 for journal
            'details': q_res.get('details', {}),
            'features': q_res.get('features', {}),
            'attributes': data_dict
        }
        
        # Boost for A+ Setups
        if score >= 8:
            candidate['scaling_factor'] = settings.RISK_FACTOR_MAX

        # BOS Override / Fusion
        if bos_res.get('valid'):
            if bos_res['signal'] == candidate['direction']:
                 candidate['score'] = 10
                 candidate['ml_prob'] = max(candidate['ml_prob'], 0.85)
                 candidate['details']['BOS'] = bos_res['reason']
                 candidate['scaling_factor'] = settings.RISK_FACTOR_MAX
                 # Liquidity Sweep Entry: set LIMIT at 0.5 ATR pullback
                 candidate['entry_type'] = 'LIMIT'
                 if signal == 'BUY':
                     candidate['limit_price'] = bos_res['price'] - (atr * 0.5)
                 else:
                     candidate['limit_price'] = bos_res['price'] + (atr * 0.5)
                 return candidate, f"BOS+ML CANDIDATE ({candidate['direction']})"
            
            elif score < 5: # ML didn't find much, but BOS did
                 # Create a BOS-only candidate
                 bos_candidate = {
                    'symbol': self.symbol,
                    'direction': bos_res['signal'],
                    'score': bos_res['score'], # 10 from BOS strategy
                    'entry_price': bos_res['price'],
                    'ensemble_score': 0,
                    'ml_prob': 0.6, # Default 'technical' prob
                    'regime': regime,
                    'sl_distance': abs(bos_res['price'] - bos_res['sl']), # Specific SL
                    'tp_distance': abs(bos_res['price'] - bos_res['sl']) * settings.BOS_MIN_RISK_REWARD, # Retail R:R
                    'scaling_factor': 1.0,
                    'details': {'BOS': bos_res['reason']},
                    'attributes': data_dict
                 }
                 
                 # Retail Viability Check
                 if not self._check_retail_viability(bos_candidate):
                     return None, "Retail Costs High"
                     
                 return bos_candidate, f"BOS CANDIDATE ({bos_candidate['direction']})"

        return candidate, "OK"

    async def manage_active_trades(self):
        """
        Active trade management:
        1. Standard Risk Management (Trailing/BE/Partial) via RiskManager.
        2. Agent-Specific Logic (e.g. Regime exit).
        """
        # We need the client to get positions and execute actions.
        # Assuming risk_manager has the client.
        client = self.risk_manager.client
        if not client: return

        positions = client.get_positions(self.symbol)
        if not positions: return

        # Get Data for decision making
        tick = mt5.symbol_info_tick(self.symbol)
        if not tick: return
        
        # Calculate ATR for dynamic trailing
        atr = 0.0
        
        # OPTIMIZATION: Use cached ATR if recent (< 5 mins)
        if self.latest_atr > 0 and (projected_time_now() - self.last_atr_time < 300):
             atr = self.latest_atr
        else:
            try:
                # Fetch 200 bars for accurate M1 ATR (was 50 - too small for indicator warmup)
                df = await run_in_executor(loader.get_historical_data, self.symbol, self.timeframe, 200)
                if df is not None:
                    # Add ATR if needed, or use high-low diff of last candle as approx
                    # Ideally use features lib, but let's keep it lightweight or import features
                    from strategy import features
                    df = features.add_technical_features(df)
                    atr = df['atr'].iloc[-1]
                    
                    # Update cache
                    self.latest_atr = atr
                    self.last_atr_time = projected_time_now()
            except Exception:
                pass

        # 1. Standard Risk Actions
        actions = self.risk_manager.monitor_positions(self.symbol, positions, tick, atr=atr)
        
        for act in actions:
            try:
                if act['type'] == 'MODIFY':
                    client.modify_position(act['ticket'], act['sl'], act['tp'])
                    print(f"[{self.symbol}] Agent: {act['reason']} -> SL {act['sl']:.5f}")
                elif act['type'] == 'PARTIAL':
                    client.partial_close(act['ticket'], act['fraction'])
                    print(f"[{self.symbol}] Agent: {act['reason']} -> Partial Close")
            except Exception as e:
                logger.error(f"[{self.symbol}] Management Action Failed: {e}")

        # 2. Agent Intelligence (Regime Guard)
        # If we are holding a LONG but regime turns BEARISH_TREND, exit?
        # Only if we have a valid regime from recent analysis
        if self.regime != "UNKNOWN":
            for pos in positions:
                # Check for Regime Conflict
                exit_reason = None
                
                if pos.type == mt5.ORDER_TYPE_BUY:
                    if "BEARISH" in self.regime:
                        exit_reason = f"Regime Shift ({self.regime})"
                elif pos.type == mt5.ORDER_TYPE_SELL:
                    if "BULLISH" in self.regime:
                        exit_reason = f"Regime Shift ({self.regime})"
                
                if exit_reason:
                    # Close trade
                    try:
                        client.close_position(pos.ticket)
                        print(f"[{self.symbol}] Agent Logic: Closing due to {exit_reason}")
                        # Log to journal? The standard journal logs exit on detection or we can force log
                        # The journal updates on the next scan/check usually, or we can explicit log here
                    except Exception as e:
                         logger.error(f"[{self.symbol}] Agent Exit Failed: {e}")

    def on_trade_executed(self, price, direction):
        self.last_trade_time = datetime.now(timezone.utc).timestamp()
        self.last_action = direction
        # P&L is updated when trade closes, here we just mark activity

    def update_performance(self, profit: float):

        self.total_pnl += profit
        if profit < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
            
        # Circuit Breaker Logic
        if self.consecutive_losses >= self.max_consecutive_losses:
            print(f"[{self.symbol}] PAUSED due to {self.consecutive_losses} consecutive losses. P&L: {self.total_pnl:.2f}")
            self.is_active = False # Require manual intervention or timer to reset?

    def reset_circuit_breaker(self):
        self.is_active = True
        self.consecutive_losses = 0
        print(f"[{self.symbol}] Circuit breaker reset.")

    def _check_retail_viability(self, candidate):
        """
        Enforce Retail Profitability Filters:
        1. Spread / SL Ratio check
        2. Hunting Hours check
        """
        # 1. Spread Check
        tick = mt5.symbol_info_tick(self.symbol)
        if tick:
            spread_pips = (tick.ask - tick.bid)
            # Assuming SL distance is in price units
            sl_dist = candidate['sl_distance']
            
            if sl_dist > 0:
                ratio = spread_pips / sl_dist
                max_ratio = getattr(settings, 'BOS_MAX_SPREAD_RATIO', 0.15)
                if ratio > max_ratio:
                    print(f"[{self.symbol}] Retail Filter: Spread {spread_pips:.5f} is {ratio:.1%} of SL. Max {max_ratio:.1%}")
                    return False
        
        # 2. Hunting Hours Check
        hunting_hours = getattr(settings, 'BOS_HUNTING_HOURS', [])
        current_hour = datetime.now(timezone.utc).hour
        
        if hunting_hours and current_hour not in hunting_hours:
             print(f"[{self.symbol}] Retail Filter: Off-hours ({current_hour}:00). Hunting: {hunting_hours}")
             return False
             
        return True

    def store_trade_pattern(self, df, direction: str, context: str = None) -> int:
        """
        Store current market pattern when trade is opened.
        Called by execution layer after trade is submitted.
        
        Returns pattern_id for later outcome update.
        """
        pattern_id = self.pattern_memory.store_pattern(
            symbol=self.symbol,
            df=df,
            direction=direction,
            outcome=None,  # Will be updated when trade closes
            pnl=0,
            context=context
        )
        self.last_pattern_id = pattern_id
        return pattern_id
    
    def update_pattern_outcome(self, pattern_id: int, outcome: str, pnl: float, 
                               entry_state=None, exit_state=None, action_taken=None):
        """
        Update pattern outcome when trade closes.
        Called by execution layer after trade result is known.
        
        Args:
            pattern_id: The pattern ID returned by store_trade_pattern
            outcome: 'WIN' or 'LOSS'
            pnl: Actual profit/loss
            entry_state: RL state at entry (for training)
            exit_state: RL state at exit (for training)
            action_taken: Action taken by RL agent
        """
        if pattern_id and pattern_id > 0:
            self.pattern_memory.update_outcome(pattern_id, outcome, pnl)
            print(f"[{self.symbol}] RAG: Updated pattern {pattern_id} -> {outcome} (${pnl:.2f})")
        
        # RL Training: Store experience and train
        if entry_state is not None and exit_state is not None and action_taken is not None:
            # Map action string to index
            action_map = {'HOLD': 0, 'BUY': 1, 'SELL': 2, 'CLOSE': 3}
            action_idx = action_map.get(action_taken, 0)
            
            # Calculate reward
            reward = self.rl_trader.calculate_reward(
                pnl_pct=pnl,
                holding_time=0,  # Would need actual duration
                max_drawdown=0,  # Would need to track
                action_taken=action_taken
            )
            
            # Store experience
            done = True  # Episode ends when trade closes
            self.rl_trader.store_experience(entry_state, action_idx, reward, exit_state, done)
            
            # Train RL agent
            loss = self.rl_trader.train_step()
            if loss:
                print(f"[{self.symbol}] RL: Trained on trade outcome (loss: {loss:.4f}, reward: {reward:.2f})")
            
            # Save model periodically
            if random.random() < 0.1:  # 10% chance
                self.rl_trader.save_model()

def projected_time_now():
    return datetime.now(timezone.utc).timestamp()
