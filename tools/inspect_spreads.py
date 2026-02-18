
import MetaTrader5 as mt5
import os
from dotenv import load_dotenv

load_dotenv()

def inspect_spreads():
    if not mt5.initialize():
        print("MT5 Init Failed")
        return

    # List of problem symbols from the user's log
    symbols = ["LTCUSD", "BCHUSD", "BTCJPY", "BTCKRW", "XAUUSD", "XAGUSD", "XPTUSD", "XNGUSD", "EURUSD", "USDJPY"]
    
    print(f"{'Symbol':<10} | {'Bid':<10} | {'Ask':<10} | {'Point':<10} | {'Digits':<6} | {'Spread (Abs)':<12} | {'Calc (Old)':<10}")
    print("-" * 90)
    
    for sym in symbols:
        # Check if symbol exists (might have suffix)
        # Try to find match
        info = mt5.symbol_info(sym)
        if not info:
            # Try suffixes
            for s in ["", "m", "c", ".m", ".c", "_m", "_c"]:
                info = mt5.symbol_info(sym + s)
                if info: break
        
        if not info:
            print(f"{sym:<10} | {'NOT FOUND':<10}")
            continue
            
        tick = mt5.symbol_info_tick(info.name)
        if not tick:
            print(f"{info.name:<10} | {'NO TICK':<10}")
            continue
            
        spread_abs = tick.ask - tick.bid
        
        # Old Calculation
        calc_old = spread_abs / (0.0001 if "JPY" not in info.name else 0.01)
        
        print(f"{info.name:<10} | {tick.bid:<10.5f} | {tick.ask:<10.5f} | {info.point:<10.5f} | {info.digits:<6} | {spread_abs:<12.5f} | {calc_old:<10.1f}")

    mt5.shutdown()

if __name__ == "__main__":
    inspect_spreads()
