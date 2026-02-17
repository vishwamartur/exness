"""
Institutional Trading Strategy - v2.1 Agentic Architecture
==========================================================
Transitioned to Multi-Agent System (Phase 2):
1. Quant Agent: Handles ML (RF/XGB/LSTM) and Signal Generation.
2. Market Analyst: Handles Regime Detection and News.
3. Risk Agent: Handles Position Management (Trailling/BE/Partial).
4. Application Layer: Strategy Class orchestrates agents.
"""

import os
import sys
import time
import asyncio
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timezone
from utils.async_utils import run_in_executor

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings
from market_data import loader
from strategy import features
from utils.data_cache import DataCache
from utils.trade_journal import TradeJournal
from utils.risk_manager import RiskManager
from utils.news_filter import is_news_blackout, get_active_events
from analysis.market_analyst import MarketAnalyst
from analysis.quant_agent import QuantAgent
from analysis.researcher_agent import ResearcherAgent
from analysis.critic_agent import CriticAgent

def _get_asset_class(symbol):
    if symbol in getattr(settings, 'SYMBOLS_CRYPTO', []): return 'crypto'
    elif symbol in getattr(settings, 'SYMBOLS_COMMODITIES', []): return 'commodity'
    return 'forex'

def _strip_suffix(symbol):
    for suffix in ['m', 'c']:
        if symbol.endswith(suffix) and len(symbol) > 3:
            base = symbol[:-len(suffix)]
            if len(base) >= 6: return base
    return symbol

class InstitutionalStrategy:
    """
    v2.1 Agentic Coordinator.
    Orchestrates specialized agents to Execute Trades.
    """
    def __init__(self, mt5_client, on_event=None):
        self.client = mt5_client
        self.on_event = on_event
        
        # --- AGENTS ------------------------------------------------------
        self.risk_manager = RiskManager(mt5_client)
        self.analyst = MarketAnalyst()
        self.quant = QuantAgent()
        self.researcher = ResearcherAgent()
        self.critic = CriticAgent(on_event=on_event)
        
        # --- STATE -------------------------------------------------------
        self.last_trade_time = {}
        self.daily_trade_count = 0
        self.last_reset_date = datetime.now(timezone.utc).date()
        self.last_candle_time = {} 
        self.last_critic_run = 0 

        # --- INFRASTRUCTURE ----------------------------------------------
        self.cache = DataCache()
        self.journal = TradeJournal()

    # =======================================================================
    #  SCANNER LOOP (Orchestrator)
    # =======================================================================

    async def run_scan_loop(self):
        # 0. Manage Positions (Risk Agent)
        for symbol in settings.SYMBOLS:
            try: self.manage_positions(symbol)
            except: pass

        # 1. Global Checks
        if not self._is_trading_session():
            print("[SCANNER] Outside trading session.")
            return
        if not self._check_daily_limit():
            print("[SCANNER] Daily limit reached.")
            return

        all_positions = self.client.get_all_positions()
        if len(all_positions) >= settings.MAX_OPEN_POSITIONS:
            print(f"[SCANNER] Max positions ({len(all_positions)})")
            return

        active_news = get_active_events()
        if active_news: print(f"[NEWS] {', '.join(active_news)}")

        print(f"\n{'='*60}\n  SCANNING {len(settings.SYMBOLS)} INSTRUMENTS (ASYNC)\n{'='*60}")
        if self.on_event:
            self.on_event({
                "type": "SCAN_START",
                "count": len(settings.SYMBOLS),
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

        # -- Phase 1: Parallel Fetch --
        # Filter symbols first
        valid_symbols = []
        for symbol in settings.SYMBOLS:
            allowed, reason = self.risk_manager.check_pre_scan(symbol)
            if allowed: valid_symbols.append(symbol)
            
        if not valid_symbols: return

        # Fetch in parallel
        tasks = [self._fetch_symbol_data(sym) for sym in valid_symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        symbol_data = {}
        for sym, res in zip(valid_symbols, results):
            if isinstance(res, Exception):
                print(f"[ERROR] Fetch {sym}: {res}")
            elif res:
                symbol_data[sym] = res

        if not symbol_data:
            print("[SCANNER] No data.")
            return

        # -- Phase 2: Parallel Scoring (Agents) --
        candidates = []
        skipped = 0
        
        # Score in parallel
        score_tasks = [self._score_symbol(sym, data) for sym, data in symbol_data.items()]
        score_results = await asyncio.gather(*score_tasks, return_exceptions=True)
        
        for res in score_results:
            if isinstance(res, Exception):
                continue
            if res:
                # Execution Check
                allowed, _ = self.risk_manager.check_execution(res['symbol'], res['direction'], all_positions)
                if allowed: candidates.append(res)
                else: skipped += 1

        print(f"\n[SCANNER] Candidates: {len(candidates)} | Filtered: {skipped}")

        if candidates:
            # Sort by Score desc, then ML prob desc
            candidates.sort(key=lambda x: (x['score'], x['ml_prob']), reverse=True)
            
            # Print top 5
            print(f"\n{'-'*70}")
            print(f"  {'Symbol':>10} | {'Dir':>4} | Sc | Ens  | ML   | Details")
            print(f"{'-'*70}")
            for c in candidates[:5]:
                det = ' '.join(f"{k}:{v}" for k,v in c['details'].items())
                print(f"    {c['symbol']:>10} | {c['direction']:>4} | {c['score']} | {c['ensemble_score']:.2f} | {c['ml_prob']:.2f} | {det}")
            
            best = candidates[0]
            
            # --- AGENT DEBATE (Researcher) -------------------------------
            print(f"\n[RESEARCHER] Reviewing best candidate: {best['symbol']}...")
            if self.on_event:
                self.on_event({
                    "type": "RESEARCH_START",
                    "symbol": best['symbol'],
                    "data": best,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
            
            attributes = best.pop('attributes') # Detach raw data for context
            analyst_mock = {'regime': best['regime']}
            
            try:
                # Async Call to Researcher
                research = await self.researcher.conduct_research(best['symbol'], attributes, analyst_mock)
                
                print(f"--> Debate Result: {research['action']} (Conf: {research['confidence']}%)")
                print(f"--> Reason: {research['reason']}")

                if self.on_event:
                    self.on_event({
                        "type": "RESEARCH_RESULT",
                        "symbol": best['symbol'],
                        "action": research['action'],
                        "confidence": research['confidence'],
                        "reason": research['reason'],
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
                
                # Decision Logic
                # 1. Strong Researcher Agreement
                execute = False
                if research['action'] == best['direction'] and research['confidence'] >= 70:
                    execute = True
                # 2. Strong Technical Override (with Researcher not blocking hard)
                elif best['score'] >= 5 and research['action'] != 'HOLD': 
                     execute = True
                     print("--> Executing on High Technical Score (Override).")
                
                if execute:
                    print(f"  >>> EXECUTE: {best['symbol']} {best['direction']}")
                    # Inject researcher data for logging
                    best['researcher_action'] = research['action']
                    best['researcher_confidence'] = research['confidence']
                    best['researcher_reason'] = research['reason']
                    self._execute_trade(best)
                else:
                    print(f"  [X] Candidate rejected by Researcher.")
            except Exception as e:
                print(f"[ERROR] Researcher failed: {e}. Skipping trade.")
                
        else:
            print("[SCANNER] No valid setups.")

        # -- Phase 3: Self-Reflection (Critic) --
        if time.time() - self.last_critic_run > 300: # Run every 5 mins
            asyncio.create_task(self.critic.analyze_closed_trades())
            self.last_critic_run = time.time()

    async def _fetch_symbol_data(self, symbol):
        # Cooldown
        last = self.last_trade_time.get(symbol, 0)
        if time.time() - last < settings.COOLDOWN_SECONDS: return None
        
        # Spread
        if not self._check_spread(symbol): return None
        
        # News (Analyst Agent)
        blocked, _ = self.analyst.check_news(symbol)
        if blocked: return None
        
        # Data (Blocking Call Wrapped)
        df = await run_in_executor(loader.get_historical_data, symbol, settings.TIMEFRAME, 500)
        if df is None or len(df) < 100: return None
        
        h1 = await run_in_executor(loader.get_historical_data, symbol, "H1", 100)
        h4 = await run_in_executor(loader.get_historical_data, symbol, "H4", 60)
        
        return {settings.TIMEFRAME: df, 'H1': h1, 'H4': h4}

    async def _score_symbol(self, symbol, data_dict):
        # 1. Quant Analysis (CPU Bound -> Executor)
        q_res = await run_in_executor(self.quant.analyze, symbol, data_dict)
        if not q_res: return None
        
        # 2. Analyst Analysis (Regime)
        a_res = self.analyst.analyze_session(symbol, q_res['data'])
        if a_res['regime'] == "RANGING": return None
        
        # 3. Validation
        score = q_res['score']
        threshold = self._get_adaptive_threshold()
        
        is_valid = False
        if score >= threshold: is_valid = True
        elif score >= 2 and (q_res['ml_prob'] > 0.85 or q_res['ml_prob'] < 0.15): is_valid = True
        
        if not is_valid: return None
        
        # 4. Return merged result (Researcher will debate later)
        return {
            'symbol': symbol,
            'direction': q_res['direction'],
            'score': score,
            'ensemble_score': q_res['ensemble_score'],
            'ml_prob': q_res['ml_prob'],
            'ai_signal': q_res['ai_signal'],
            'details': q_res['details'],
            'attributes': q_res, # raw quant result
            'regime': a_res['regime'],
            'sl_distance': q_res['features'].get('atr', 0) * settings.ATR_SL_MULTIPLIER,
            'tp_distance': q_res['features'].get('atr', 0) * settings.ATR_TP_MULTIPLIER,
            'scaling_factor': 0.5 if a_res['regime'] == "VOLATILE" else 1.0
        }

    def _execute_trade(self, setup):
        symbol = setup['symbol']
        direction = setup['direction']
        score = setup['score']
        sl_dist = setup['sl_distance']
        tp_dist = setup['tp_distance']
        
        # Execution Risk Check
        pos = self.client.get_all_positions()
        allowed, reason = self.risk_manager.check_execution(symbol, direction, pos)
        if not allowed:
            print(f"[RISK] Execution Blocked: {reason}")
            return

        # Sizing
        lot = self.risk_manager.calculate_position_size(symbol, sl_dist, score, setup['scaling_factor'])
        
        tick = mt5.symbol_info_tick(symbol)
        if not tick: return
        
        if direction == 'BUY':
            sl = tick.ask - sl_dist
            tp = tick.ask + tp_dist
            cmd = mt5.ORDER_TYPE_BUY
            price = tick.ask
        else:
            sl = tick.bid + sl_dist
            tp = tick.bid - tp_dist
            cmd = mt5.ORDER_TYPE_SELL
            price = tick.bid
            
        print(f"[{symbol}] EXECUTE {direction} | Lot: {lot} | SL: {sl:.5f} | TP: {tp:.5f}")
        
        res = self.client.place_order(cmd, symbol, lot, sl, tp)
        if res:
            print(f"[OK] ORDER FILLED: {symbol}")
            if self.on_event:
                self.on_event({
                    "type": "TRADE_EXECUTION",
                    "symbol": symbol,
                    "direction": direction,
                    "price": price,
                    "lot": lot,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
            self.risk_manager.record_trade(symbol)
            self.last_trade_time[symbol] = time.time()
            
            self.journal.log_entry(
                ticket=res.order,
                symbol=symbol, 
                direction=direction, 
                lot_size=lot,
                entry_price=price, 
                sl_price=sl, 
                tp_price=tp,
                confluence_score=score, 
                confluence_details=setup['details'],
                rf_probability=setup['ml_prob'], 
                ai_signal=setup['ai_signal'],
                asset_class=_get_asset_class(symbol), 
                session=self._get_current_session(),
                researcher_action=setup.get('researcher_action', 'NONE'),
                researcher_confidence=setup.get('researcher_confidence', 0),
                researcher_reason=setup.get('researcher_reason', 'N/A')
            )
            self.daily_trade_count += 1

    def manage_positions(self, symbol):
        """Delegates to Risk Agent. Passing ATR for dynamic management."""
        pos = self.client.get_positions(symbol)
        if not pos: return
        tick = mt5.symbol_info_tick(symbol)
        if not tick: return
        
        # Get ATR for dynamic trailing stop
        atr = 0.0
        try:
            # Use data cache to get recent bars
            df = self.cache.get(symbol, settings.TIMEFRAME, 50)
            if df is not None and not df.empty:
                # Ensure technical features are added
                if 'atr' not in df.columns:
                    df = features.add_technical_features(df)
                    
                atr = df['atr'].iloc[-1]
                # Fallback if ATR is 0 or NaN
                if pd.isna(atr) or atr <= 0:
                    atr = 0.0
        except Exception as e:
            print(f"[{symbol}] Failed to get ATR for Risk Manager: {e}")
            atr = 0.0
        
        actions = self.risk_manager.monitor_positions(symbol, pos, tick, atr=atr)
        for act in actions:
            try:
                if act['type'] == 'MODIFY':
                    self.client.modify_position(act['ticket'], act['sl'], act['tp'])
                    print(f"[{symbol}] Risk Agent: {act['reason']} -> SL {act['sl']:.5f}")
                elif act['type'] == 'PARTIAL':
                    self.client.partial_close(act['ticket'], act['fraction'])
                    print(f"[{symbol}] Risk Agent: {act['reason']} -> Partial")
            except Exception as e:
                print(f"[{symbol}] Risk Action Failed: {e}")

    # --- HELPERS ---------------------------------------------------------

    def _is_new_candle(self, symbol):
        df = self.cache.get(symbol, settings.TIMEFRAME, 10)
        if df is None or len(df) < 2: return True
        last = self.last_candle_time.get(symbol)
        curr = df['time'].iloc[-1]
        if last is None or curr != last:
            self.last_candle_time[symbol] = curr
            return True
        return False

    def _get_adaptive_threshold(self):
        now = datetime.now(timezone.utc).hour
        # Overlap
        ov = settings.TRADE_SESSIONS.get('overlap', {})
        if ov.get('start', 13) <= now < ov.get('end', 16): return max(4, settings.SURESHOT_MIN_SCORE - 1)
        # Session
        lon = settings.TRADE_SESSIONS.get('london', {})
        ny = settings.TRADE_SESSIONS.get('new_york', {})
        if (lon.get('start', 8) <= now < lon.get('end', 12)) or \
           (ny.get('start', 13) <= now < ny.get('end', 17)):
            return settings.SURESHOT_MIN_SCORE
        return min(6, settings.SURESHOT_MIN_SCORE + 1)

    def _get_current_session(self):
        now = datetime.now(timezone.utc).hour
        for name, times in settings.TRADE_SESSIONS.items():
            if times['start'] <= now < times['end']: return name
        return 'off_hours'

    def _is_trading_session(self):
        if not settings.SESSION_FILTER: return True
        now = datetime.now(timezone.utc).hour
        for _, times in settings.TRADE_SESSIONS.items():
            if times['start'] <= now < times['end']: return True
        return False

    def _check_spread(self, symbol):
        tick = mt5.symbol_info_tick(symbol)
        info = mt5.symbol_info(symbol)
        if not tick or not info: return False
        spread = (tick.ask - tick.bid) / info.point
        cls = _get_asset_class(symbol)
        lim = settings.MAX_SPREAD_PIPS * 10
        if cls == 'crypto': lim = settings.MAX_SPREAD_PIPS_CRYPTO * 10
        elif cls == 'commodity': lim = settings.MAX_SPREAD_PIPS_COMMODITY * 10
        return spread <= lim

    def _check_daily_limit(self):
        today = datetime.now(timezone.utc).date()
        if today != self.last_reset_date:
            self.daily_trade_count = 0
            self.last_reset_date = today
            print(f"[SYSTEM] Daily reset.")
        return self.daily_trade_count < settings.MAX_DAILY_TRADES

    def check_market(self, symbol):
        pass
