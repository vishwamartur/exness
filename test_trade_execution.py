
import sys
import os
import MetaTrader5 as mt5
from config import settings
from execution.mt5_client import MT5Client
from utils.risk_manager import RiskManager

def test_trade():
    print("=== FORCE TRADE EXECUTION TEST ===")
    
    # 1. Connect
    client = MT5Client()
    if not client.connect():
        print("CRITICAL: Connect Failed")
        return
        
    symbol = "EURUSD"
    print(f"Target: {symbol}")
    
    # 2. Check Stops Level
    info = mt5.symbol_info(symbol)
    if not info:
        print("CRITICAL: Symbol info failed")
        return
        
    print(f"Stops Level: {info.trade_stops_level}")
    print(f"Point: {info.point}")
    print(f"Ask: {info.ask}")
    
    # 3. Calculate Lot Logic
    rm = RiskManager(client)
    # Check lot size calculation
    # We pass 0.01 stop loss distance roughly?
    # calculate_lot_size(symbol, stop_loss_pips)
    # calculate_lot_size(symbol, stop_loss_pips)
    try:
        # risk_manager.calculate_position_size uses client.calculate_lot_size
        # But here we want direct access
        sl_points = 100 # 10 pips = 100 points
        sl_price_dist = 0.0010 # 10 pips
        lot_size = client.calculate_lot_size(symbol, sl_price_dist)
        print(f"Calculated Lot Size for 10 pip SL: {lot_size}")
    except Exception as e:
        print(f"Lot Calc Failed: {e}")
        lot_size = 0.01

    if lot_size <= 0:
        print("WARNING: Lot size is 0. Using 0.01 backup.")
        lot_size = 0.01

    # 4. Place Pending Order (Buy Limit) well below price
    price = info.ask - 0.0050 # 50 pips below
    sl = price - 0.0020
    tp = price + 0.0040
    
    print(f"Attempting BUY LIMIT @ {price:.5f} (Lot: {lot_size})")
    
    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": float(lot_size),
        "type": mt5.ORDER_TYPE_BUY_LIMIT,
        "price": price,
        "sl": sl,
        "tp": tp,
        "deviation": settings.DEVIATION,
        "magic": 123456,
        "comment": "ANTIGRAVITY_TEST",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    
    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        print(f"❌ ORDER FAILED: {result.retcode} - {result.comment}")
        # Try adjusting filling mode
        print("Retrying with RETURN filling...")
        request['type_filling'] = mt5.ORDER_FILLING_RETURN
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
             print(f"❌ ORDER FAILED AGAIN: {result.retcode} - {result.comment}")
        else:
             print(f"✅ ORDER PLACED (RETURN filling): Ticket {result.order}")
             # Cleanup
             print("Cleaning up (Cancelling)...")
             del_request = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": result.order,
            }
             mt5.order_send(del_request)
             print("Order Cancelled.")
    else:
        print(f"✅ ORDER PLACED: Ticket {result.order}")
        # Cleanup
        print("Cleaning up (Cancelling)...")
        del_request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": result.order,
        }
        mt5.order_send(del_request)
        print("Order Cancelled.")

    client.shutdown()

if __name__ == "__main__":
    test_trade()
