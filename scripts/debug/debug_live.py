
import sys
import os
import time
from datetime import datetime
from config import settings
from execution.mt5_client import MT5Client
from strategy.institutional_strategy import InstitutionalStrategy
import MetaTrader5 as mt5

# Enable Debug
settings.LOG_LEVEL = "DEBUG"

def debug_live():
    print("=== LIVE AGENT DIAGNOSTIC ===")
    
    client = MT5Client()
    if not client.connect():
        print("CRITICAL: Connect Failed")
        return

    # 0. Check Algo Trading Permission
    print(f"Algo Trading Allowed: {mt5.terminal_info().trade_allowed}")
    if not mt5.terminal_info().trade_allowed:
        print("❌ CRITICAL: 'Algo Trading' button is OFF in MT5!")
    
    strategy = InstitutionalStrategy(client)
    # Models loaded by QuantAgent
    
    # 1. Session Check
    if not strategy._is_trading_session():
        print(f"❌ SESSION CLOSED (Current Hour: {datetime.now().hour})")
    else:
        print("✅ Session OPEN")

    # 2. Scan Loop
    symbols = settings.SYMBOLS
    if not symbols:
        client.detect_available_symbols()
        symbols = settings.SYMBOLS
    
    print(f"Scanning {len(symbols)} symbols...")
    
    for symbol in symbols:
        print(f"\n--- {symbol} ---")
        
        # A. Risk Checks (Pre-Scan)
        allowed, reason = strategy.risk_manager.check_pre_scan(symbol)
        if not allowed:
            print(f"  ❌ Risk Block: {reason}")
            continue
            
        # B. Data
        data = strategy._fetch_symbol_data(symbol)
        if not data:
            print("  ❌ Data Fetch Failed (Cooldown, Spread, or News?)")
            continue
            
        # C. Quant Agent
        q_res = strategy.quant.analyze(symbol, data)
        if not q_res:
            print("  ❌ Quant Analysis Failed (Insufficient Data/Features)")
            continue
            
        # D. Analyst Agent
        print(f"DEBUG: Data type: {type(q_res['data'])}")
        a_res = strategy.analyst.analyze_session(symbol, q_res['data'])
        
        print(f"  Regime: {a_res['regime']} ({a_res.get('reason','-')})")
        print(f"  Score: {q_res['score']}/6 ({q_res['direction']})")
        print(f"  ML Prob: {q_res['ml_prob']:.2f}")
        print(f"  Details: {q_res['details']}")
        
        validated = False
        threshold = strategy._get_adaptive_threshold()
        
        if q_res['score'] >= threshold: validated = True
        elif q_res['score'] >= 2 and (q_res['ml_prob'] > 0.85 or q_res['ml_prob'] < 0.15): validated = True
        
        if validated:
            if a_res['regime'] == 'RANGING':
                print("  ❌ Valid Score but RANGING Market (Analyst Veto)")
            else:
                print("  ✅ SETUP VALID")
        else:
            print(f"  ❌ REJECTED (Score < {threshold})")

    client.shutdown()

if __name__ == "__main__":
    debug_live()
