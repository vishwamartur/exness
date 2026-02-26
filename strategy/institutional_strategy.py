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
from utils.adaptive_position_manager import AdaptivePositionManager
from utils.pre_trade_analyzer import PreTradeAnalyzer
from analysis.market_analyst import MarketAnalyst
from analysis.quant_agent import QuantAgent
from analysis.researcher_agent import ResearcherAgent
from analysis.critic_agent import CriticAgent
from utils.telegram_notifier import get_notifier as _tg

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
        self.timeframe = settings.TIMEFRAME
        
        # --- SHARED RESOURCES --------------------------------------------
        self.risk_manager = RiskManager(mt5_client)
        self.analyst = MarketAnalyst()
        self.quant = QuantAgent()
        self.researcher = ResearcherAgent()
        self.critic = CriticAgent(on_event=on_event)
        self.adaptive_manager = AdaptivePositionManager(mt5_client, self.quant)
        self.pre_trade_analyzer = PreTradeAnalyzer(self.quant, self.analyst)
        
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

        # Telegram startup greeting
        _tg().info(
            f"🤖 <b>MT5 Bot Started</b>\n"
            f"Scanning <b>{len(settings.SYMBOLS)}</b> pairs on M1\n"
            f"Session: London 07-10 UTC | NY 13-16 UTC"
        )

    # =======================================================================
    #  SCANNER LOOP (Orchestrator)
    # =======================================================================

    async def run_scan_loop(self):
        # 0. Manage Positions (Agents + Adaptive Manager)
        # Agents handle their own exits, Adaptive Manager provides ML-based optimization
        manage_tasks = [agent.manage_active_trades() for agent in self.agents.values()]
        
        # Add adaptive position management
        adaptive_actions = self.adaptive_manager.manage_positions()
        if adaptive_actions:
            success_count = self.adaptive_manager.execute_actions(adaptive_actions)
            if success_count > 0:
                print(f"[ADAPTIVE] Executed {success_count} position management actions")
                
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
                # Need approximate SL/TP levels for Profitability Check
                # Use last close as approximate entry
                approx_entry = candidate['attributes'][self.timeframe]['close'].iloc[-1]
                
                sl_price = 0.0
                tp_price = 0.0
                
                if candidate['direction'] == 'BUY':
                    sl_price = approx_entry - candidate['sl_distance']
                    tp_price = approx_entry + candidate['tp_distance']
                else:
                    sl_price = approx_entry + candidate['sl_distance']
                    tp_price = approx_entry - candidate['tp_distance']

                allowed, exec_reason = self.risk_manager.check_execution(
                    candidate['symbol'], 
                    candidate['direction'], 
                    sl_price, 
                    tp_price, 
                    all_positions
                )
                if allowed: 
                    candidates.append(candidate)
                else: 
                    scan_status[symbol] = f"Exec Block: {exec_reason}"

        # -- Report --
        self._print_scan_summary(scan_status)

        # ── Broadcast to dashboard ────────────────────────────────────────
        if self.on_event:
            # 1. Full scan summary with per-symbol reasons
            self.on_event({
                "type": "SCAN_SUMMARY",
                "symbols": scan_status,
                "count": len(self.agents),
                "candidates": len(candidates),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            # 2. Live position update with P&L
            try:
                import MetaTrader5 as _mt5
                raw_positions = _mt5.positions_get() or []
                pos_list = []
                for p in raw_positions:
                    pos_list.append({
                        "ticket":        p.ticket,
                        "symbol":        p.symbol,
                        "type":          p.type,          # 0=BUY, 1=SELL
                        "direction":     "BUY" if p.type == 0 else "SELL",
                        "volume":        p.volume,
                        "entry_price":   p.price_open,
                        "price_current": p.price_current,
                        "sl_price":      p.sl,
                        "tp_price":      p.tp,
                        "profit":        p.profit,
                    })
                self.on_event({
                    "type": "POSITION_UPDATE",
                    "positions": pos_list,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

                # 3. Account info
                acct = _mt5.account_info()
                if acct:
                    self.on_event({
                        "type": "ACCOUNT_UPDATE",
                        "account": {
                            "balance":  acct.balance,
                            "equity":   acct.equity,
                            "profit":   acct.profit,
                            "currency": acct.currency,
                            "leverage": acct.leverage,
                            "day_pl":   round(acct.equity - acct.balance, 2),
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
            except Exception:
                pass

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
                    safe_dir = str(c['direction']).encode('ascii', 'ignore').decode('ascii')
                    print(f"    {safe_sym:>10} | {safe_dir:>4} | {c['score']} | {c['ensemble_score']:.2f} | {c['ml_prob']:.2f} | {safe_det}")
                except Exception as e:
                    try:
                        safe_sym = str(c.get('symbol', 'UNKNOWN')).encode('ascii', 'ignore').decode('ascii')
                        safe_dir = str(c.get('direction', 'UNKNOWN')).encode('ascii', 'ignore').decode('ascii')
                        print(f"    {safe_sym:>10} | {safe_dir:>4} | {c.get('score', 0)} | [Print Error]")
                    except:
                        print(f"    [Print Error]")
            
            best = candidates[0]

            # Telegram: alert on signals
            _tg().scan_candidates(candidates)
            
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
                # Pass 'best' (candidate dict) as quant_data, which has 'features', 'score', etc.
                research = await self.researcher.conduct_research(best['symbol'], best, analyst_mock)
                
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
                # 1. Strong Researcher Agreement (Relaxed to 50%)
                execute = False
                if research['action'] == best['direction'] and research['confidence'] >= 50:
                    execute = True
                # 2. Technical Override (Sureshot from Settings)
                elif best['score'] >= settings.SURESHOT_MIN_SCORE: 
                     execute = True
                     print(f"--> Executing on Turnkey Score (>{settings.SURESHOT_MIN_SCORE}).")
                # 3. Aggressive Mode: Allow HOLD if score meets min confluence
                elif best['score'] >= settings.MIN_CONFLUENCE_SCORE and research['action'] == 'HOLD':
                     execute = True
                     print("--> Executing on Min Score (Aggressive Mode).")
                
                if execute:
                    print(f"  >>> EXECUTE: {best['symbol']} {best['direction']}")
                    # Inject researcher data for logging
                    best['researcher_action'] = research['action']
                    best['researcher_confidence'] = research['confidence']
                    best['researcher_reason'] = research['reason']
                    try:
                        self._execute_trade(best)
                    except Exception as e:
                        print(f"[ERROR] Trade execution failed: {e}")
                        import traceback
                        traceback.print_exc()
                else:
                    print(f"  [X] Candidate rejected by Researcher.")
            except Exception as e:
                print(f"[ERROR] Researcher failed: {e}. Skipping trade.")
                import traceback
                traceback.print_exc()
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
        
        # --- PRE-TRADE ANALYSIS ---
        print(f"[PRE-TRADE] Analyzing {symbol} {direction} entry...")
        analysis = self.pre_trade_analyzer.analyze_entry_opportunity(symbol, direction)
        
        # Check if we should proceed with the trade
        if not analysis['should_enter']:
            print(f"[PRE-TRADE] Entry BLOCKED for {symbol} {direction}: {analysis['recommendation']}")
            print(f"[PRE-TRADE] Confidence: {analysis['confidence_score']:.3f}")
            for reason in analysis['reasoning']:
                print(f"  - {reason}")
            return
        
        print(f"[PRE-TRADE] Entry APPROVED for {symbol} {direction}: {analysis['recommendation']}")
        print(f"[PRE-TRADE] Confidence: {analysis['confidence_score']:.3f}")
        print(f"[PRE-TRADE] Component scores: {analysis['component_scores']}")

        # Guard 1: Never execute NEUTRAL direction
        if direction not in ('BUY', 'SELL'):
            print(f"[RISK] Execution Blocked: direction '{direction}' is not BUY/SELL")
            return

        # Guard 2: Verify symbol is tradeable (not disabled/reference instrument)
        _sym_info = mt5.symbol_info(symbol)
        if _sym_info is None or _sym_info.trade_mode == 0:
            print(f"[RISK] Execution Blocked: {symbol} trade_mode=DISABLED (not a tradeable instrument)")
            return

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
            # Telegram alert
            _tg().trade_executed(symbol, direction, lot, price, sl, tp)
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
                confluence_details=setup.get('details', {}),
                rf_probability=setup.get('ml_prob', 0.5), 
                ai_signal=setup.get('ai_signal', 0),
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
