import MetaTrader5 as mt5
import time
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config import settings
from execution.mt5_client import MT5Client

def verify_execution():
    print("--- Verifying Trade Execution ---")
    
    # 1. Connect
    client = MT5Client()
    if not client.connect():
        print("FAIL: Could not connect to MT5.")
        return

    # 2. Check Balance
    account_info = mt5.account_info()
    if account_info is None:
        print("FAIL: Could not retrieve account info.")
        return
        
    print(f"Account: {account_info.login}")
    print(f"Balance: {account_info.balance} {account_info.currency}")
    print(f"Leverage: {account_info.leverage}")
    
    if account_info.leverage < 1000:
        print(f"WARNING: Leverage is {account_info.leverage}, user requested 1000x.")
        
    # 3. Place Pending Order (Buy Limit far below price)
    print("Placing test Pending Order (Buy Limit)...")
    symbol = settings.SYMBOL
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        print(f"FAIL: No tick data for {symbol}")
        return

    price = tick.ask - 0.0050 # 50 pips below current price
    sl = price - 0.0010
    tp = price + 0.0010
    
    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": 0.01,
        "type": mt5.ORDER_TYPE_BUY_LIMIT,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": 999999,
        "comment": "Verification Order",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"FAIL: Order send failed. Retcode: {result.retcode}")
        print(f"Comment: {result.comment}")
        return
        
    print(f"SUCCESS: Order placed! Ticket: {result.order}")
    
    # 4. Cancel Order immediately
    print("Cancelling test order...")
    time.sleep(1)
    
    cancel_request = {
        "action": mt5.TRADE_ACTION_REMOVE,
        "order": result.order,
        "magic": 999999,
    }
    
    cancel_result = mt5.order_send(cancel_request)
    if cancel_result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"WARNING: Could not cancel order {result.order}. Check terminal.")
        print(f"Error: {cancel_result.comment}")
    else:
        print(f"SUCCESS: Order {result.order} cancelled.")
        
    client.shutdown()
    print("--- Verification Complete ---")

if __name__ == "__main__":
    verify_execution()
