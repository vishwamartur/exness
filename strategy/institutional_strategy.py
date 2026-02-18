"""
Institutional Trading Strategy - v2.2 Agentic Architecture (Per-Pair Agents)
============================================================================
Transitioned to Multi-Agent System (Phase 3):
1. PairAgent: dedicated agent for each symbol.
2. Quant Agent: Shared ML/Analysis resource.
3. Market Analyst: Shared Regime resource.
4. Risk Agent: Global and Per-Pair Risk Management.
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
from strategy.pair_agent import PairAgent
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
    v2.2 Agentic Coordinator.
    Orchestrates specialized PairAgents to Execute Trades.
    """
    def __init__(self, mt5_client, on_event=None):
        self.client = mt5_client
        self.on_event = on_event
        
        # --- SHARED RESOURCES --------------------------------------------
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
        
        # --- PAIR AGENTS -------------------------------------------------
        self.agents = {} # {symbol: PairAgent}
        print(f"[SYSTEM] Initializing Pair Agents for {len(settings.SYMBOLS)} symbols...")
        for symbol in settings.SYMBOLS:
            self.agents[symbol] = PairAgent(
                symbol=symbol,
                quant_agent=self.quant,
                analyst_agent=self.analyst,
                risk_manager=self.risk_manager
            )

    # =======================================================================
    #  SCANNER LOOP (Orchestrator)
    # =======================================================================

    async def run_scan_loop(self):
        # 0. Manage Positions (Agents)
        # Agents now handle their own exits (Regime, Trailing Stop, etc)
        manage_tasks = [agent.manage_active_trades() for agent in self.agents.values()]
        await asyncio.gather(*manage_tasks, return_exceptions=True)

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

        print(f"\n{'='*60}\n  SCANNING {len(self.agents)} PAIR AGENTS (ASYNC)\n{'='*60}")
        if self.on_event:
            self.on_event({
                "type": "SCAN_START",
                "count": len(self.agents),
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

        # Track status for reporting
        scan_status = {}  # {symbol: reason}

        # -- Phase 1: Parallel Agent Scan --
        tasks = []
        active_symbols = []
        
        for symbol, agent in self.agents.items():
            tasks.append(agent.scan())
            active_symbols.append(symbol)
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        candidates = []
        
        for i, res in enumerate(results):
            symbol = active_symbols[i]
            
            if isinstance(res, Exception):
                scan_status[symbol] = f"Error: {str(res)}"
                continue
                
            candidate, status = res
            scan_status[symbol] = status
            
            if candidate:
                # Execution Check (Global Limit)
                allowed, exec_reason = self.risk_manager.check_execution(candidate['symbol'], candidate['direction'], all_positions)
                if allowed: 
                    candidates.append(candidate)
                else: 
                    scan_status[symbol] = f"Exec Block: {exec_reason}"

        # -- Report --
        self._print_scan_summary(scan_status)

        if candidates:
            # Sort by Score desc, then ML prob desc
            candidates.sort(key=lambda x: (x['score'], x['ml_prob']), reverse=True)
            
            # Print top 5
            print(f"\n{'-'*75}")
            print(f"  {'Symbol':>10} | {'Dir':>4} | Sc | Ens  | ML   | Details")
            print(f"{'-'*75}")
            for c in candidates[:5]:
                try:
                    det = ' '.join(f"{k}:{v}" for k,v in c['details'].items())
                    # Force ASCII for Windows Console
                    safe_det = det.encode('ascii', 'ignore').decode('ascii') 
                    safe_sym = c['symbol'].encode('ascii', 'ignore').decode('ascii')
                    print(f"    {safe_sym:>10} | {c['direction']:>4} | {c['score']} | {c['ensemble_score']:.2f} | {c['ml_prob']:.2f} | {safe_det}")
                except Exception as e:
                    print(f"    {c['symbol']:>10} | {c['direction']:>4} | {c['score']} | [Print Error]")
            
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
             print("[SCANNER] No candidates found.")

        # -- Phase 3: Self-Reflection (Critic) --
        if time.time() - self.last_critic_run > 300: # Run every 5 mins
            asyncio.create_task(self.critic.analyze_closed_trades())
            self.last_critic_run = time.time()

    def _execute_trade(self, setup):
        symbol = setup['symbol']
        direction = setup['direction']
        score = setup['score']
        sl_dist = setup['sl_distance']
        tp_dist = setup['tp_distance']
        
        if sl_dist <= 0: return 
        
        # R:R Mandate (Asymmetric Payoff)
        if getattr(settings, "MANDATE_MIN_RR", False):
            rr_ratio = tp_dist / sl_dist
            if rr_ratio < settings.MIN_RISK_REWARD_RATIO:
                print(f"[RISK] Execution Blocked: R:R {rr_ratio:.2f} < {settings.MIN_RISK_REWARD_RATIO}")
                return

        # Execution Risk Check
        pos = self.client.get_all_positions()
        
        # Calculate SL/TP for check
        tick = mt5.symbol_info_tick(symbol)
        if not tick: return
        
        if direction == 'BUY':
            sl = tick.ask - sl_dist
            tp = tick.ask + tp_dist
        else:
            sl = tick.bid + sl_dist
            tp = tick.bid - tp_dist
            
        allowed, reason = self.risk_manager.check_execution(symbol, direction, sl, tp, pos)
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
            
            # Notify Agent
            if symbol in self.agents:
                self.agents[symbol].on_trade_executed(price, direction)
            
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

    def _check_daily_limit(self):
        today = datetime.now(timezone.utc).date()
        if today != self.last_reset_date:
            self.daily_trade_count = 0
            self.last_reset_date = today
            print(f"[SYSTEM] Daily reset.")
        return self.daily_trade_count < settings.MAX_DAILY_TRADES

    def _print_scan_summary(self, scan_status):
        """Prints a grouped summary of scan results."""
        # Simple summary for brevity
        pass 
        # (You can re-implement the detailed summary if desired, but for now 
        # let's keep the scanner output clean or delegate to per-agent logs)
        print(f"\n--- Scan Summary ---")
        
        # Group by reason
        grouped = {}
        for sym, reason in scan_status.items():
            if reason not in grouped: grouped[reason] = []
            grouped[reason].append(sym)
            
        # Print valid candidates first
        for reason, syms in grouped.items():
            if "CANDIDATE" in reason:
                 print(f"  [OK] {reason:<20}: {', '.join(syms)}")
        
        # Print others
        for reason, syms in grouped.items():
            if "CANDIDATE" not in reason:
                if len(syms) > 10:
                    print(f"  [-]  {reason:<20}: {len(syms)} symbols")
                else:
                    print(f"  [-]  {reason:<20}: {', '.join(syms)}")
        print(f"--------------------\n")

    def check_market(self, symbol):
        pass
