import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional, Any, Tuple
import MetaTrader5 as mt5

from config import settings
from utils.async_utils import run_in_executor
from market_data import loader
from utils.trade_journal import TradeJournal

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
        
        # State
        self.is_active = True
        self.consecutive_losses = 0
        self.total_pnl = 0.0
        self.last_scan_time = 0
        self.last_trade_time = 0
        self.regime = "UNKNOWN"
        self.last_atr_time = 0
        self.latest_atr = 0.0
        
        # Performance Thresholds (Self-Correction)
        self.max_consecutive_losses = 3

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
             print(f"[{self.symbol}] ⚠️ Agent restored in PAUSED state (Circuit Breaker).")

    async def scan(self) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        Orchestrates the scanning process for this specific pair.
        Returns (candidate_dict, status_message)
        """
        if not self.is_active:
            return None, "Inactive (Circuit Breaker)"

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
            df = await run_in_executor(loader.get_historical_data, self.symbol, self.timeframe, 500)
            if df is None or len(df) < 100:
                return None, "Insufficient Data"

            data_dict = {self.timeframe: df}
            
            # Fetch Multi-Timeframe Data if enabled
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
        
        # 3. Construct Candidate
        # Filter: Minimum Score
        score = q_res.get('score', 0)
        
        # Basic Filter
        if score < settings.MIN_CONFLUENCE_SCORE:
            return None, f"Low Score ({score})"
            
        # Sureshot Mode Filter
        if score < settings.SURESHOT_MIN_SCORE:
            return None, f"Below Sureshot ({score} < {settings.SURESHOT_MIN_SCORE})"
            
        # ML Prob Filter
        if q_res.get('ml_prob', 0) < settings.RF_PROB_THRESHOLD:
             return None, f"Low ML Prob ({q_res.get('ml_prob', 0):.2f})"
             
        # Regime Filter (Basic)
        signal = q_res['signal']
        if signal == "BUY" and "BEARISH" in regime:
             # Allow if score is high (Counter-trend)?
             if score < 7: return None, f"Regime Conflict ({regime})"
        if signal == "SELL" and "BULLISH" in regime:
             if score < 7: return None, f"Regime Conflict ({regime})"

        # Construct
        atr = q_res['features'].get('atr', 0)
        sl_dist = atr * settings.ATR_SL_MULTIPLIER
        tp_dist = atr * settings.ATR_TP_MULTIPLIER
        
        # Enforce Min Risk:Reward
        if sl_dist > 0:
            rr_ratio = tp_dist / sl_dist
            if rr_ratio < settings.MIN_RISK_REWARD_RATIO:
                return None, f"Low R:R ({rr_ratio:.2f} < {settings.MIN_RISK_REWARD_RATIO})"
        
        candidate = {
            'symbol': self.symbol,
            'direction': signal,
            'score': score,
            'entry_price': 0, # Filled at execution
            'ensemble_score': q_res.get('ensemble_score', 0), # Add ensemble score
            'ml_prob': q_res.get('ml_prob', 0),
            'regime': regime,
            'sl_distance': sl_dist,
            'tp_distance': tp_dist,
            'scaling_factor': 1.0, # Could be dynamic based on regime
            'details': q_res.get('details', {}),
            'attributes': data_dict # For Researcher
        }
        
        # Boost for A+ Setups
        if score >= 8:
            candidate['scaling_factor'] = settings.RISK_FACTOR_MAX
            
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
                # We can use the data from the last scan or fetch fresh
                # Fetching fresh M15 50 candles is fast
                df = await run_in_executor(loader.get_historical_data, self.symbol, self.timeframe, 50)
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
                    print(f"[{self.symbol}] 🛡️ Agent: {act['reason']} -> SL {act['sl']:.5f}")
                elif act['type'] == 'PARTIAL':
                    client.partial_close(act['ticket'], act['fraction'])
                    print(f"[{self.symbol}] 💰 Agent: {act['reason']} -> Partial Close")
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
                        print(f"[{self.symbol}] 🧠 Agent Logic: Closing due to {exit_reason}")
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
            print(f"[{self.symbol}] ⛔ PAUSED due to {self.consecutive_losses} consecutive losses. P&L: {self.total_pnl:.2f}")
            self.is_active = False # Require manual intervention or timer to reset?

    def reset_circuit_breaker(self):
        self.is_active = True
        self.consecutive_losses = 0
        print(f"[{self.symbol}] 🟢 Circuit breaker reset.")

def projected_time_now():
    return datetime.now(timezone.utc).timestamp()
