
import sys
import os
import time
from config import settings
from execution.mt5_client import MT5Client
from utils.risk_manager import RiskManager
from analysis.mistral_advisor import MistralAdvisor
import MetaTrader5 as mt5

def debug_execution():
    print("=== DEBUG EXECUTION ===")
    
    # 1. MT5 Connection
    client = MT5Client()
    if not client.connect():
        print("CRITICAL: MT5 Connect Failed")
        return
        
    # 2. Account Info
    account = mt5.account_info()
    if not account:
        print("CRITICAL: Could not get account info")
    else:
        print(f"Account: {account.login}")
        print(f"Balance: {account.balance}")
        print(f"Equity:  {account.equity}")
        print(f"Leverage: {account.leverage}")
        print(f"Server:  {account.server}")
        
    # 3. Symbol Checks (Spread)
    symbol = settings.SYMBOL # "EURUSD"
    print(f"\nChecking Symbol: {symbol} (M5)")
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        print(f"CRITICAL: No tick data for {symbol}")
    else:
        spread = (tick.ask - tick.bid)
        point = mt5.symbol_info(symbol).point
        spread_pips = spread / point / 10 # standard pip
        print(f"Bid: {tick.bid}, Ask: {tick.ask}")
        print(f"Spread: {spread_pips:.2f} pips (Max: {settings.MAX_SPREAD_PIPS})")
        
        if spread_pips > settings.MAX_SPREAD_PIPS:
            print("❌ SPREAD TOO HIGH - Trade would be rejected")
        else:
            print("✅ Spread OK")

    # 4. Risk Manager
    print("\nChecking Risk Manager...")
    rm = RiskManager(client)
    try:
        # Mock setup
        setup = {'symbol': symbol, 'direction': 'BUY', 'score': 5}
        # We need to simulate signal
        # check_risk(symbol, direction, score) ?? 
        # No, check_trade_allowed(symbol)
        allowed, reason = rm.check_trade_allowed(symbol, "BUY", 0.01) # Check min lot logic
        print(f"Trade Allowed: {allowed}")
        print(f"Reason: {reason}")
    except Exception as e:
        print(f"Risk Logic Error: {e}")

    # 5. Mistral Check
    print("\nChecking Mistral API...")
    try:
        mistral = MistralAdvisor()
        # Simple health check
        start = time.time()
        # analyze_market(symbol, timeframe, indicators)
        indicators = {
            'close': tick.bid, 'adx': 30, 'rsi': 50, 
            'regime': 'TRENDING', 'ml_prob': 0.9, 'h4_trend': 1
        }
        print("Sending request to Mistral...")
        s, c, r = mistral.analyze_market(symbol, "M5", indicators)
        print(f"Mistral verify: {s} ({c}%) - {r}")
        print(f"Latency: {time.time() - start:.2f}s")
    except Exception as e:
        print(f"Mistral Error: {e}")

    client.shutdown()

if __name__ == "__main__":
    debug_execution()
