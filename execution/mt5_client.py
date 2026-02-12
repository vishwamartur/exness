import MetaTrader5 as mt5
import time
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings

class MT5Client:
    def __init__(self):
        self.default_symbol = settings.SYMBOL
        self.lot = settings.LOT_SIZE
        self.deviation = settings.DEVIATION
        
    def connect(self):
        if not mt5.initialize(path=settings.MT5_PATH):
            print("initialize() failed")
            return False
            
        if not mt5.login(settings.MT5_LOGIN, password=settings.MT5_PASSWORD, server=settings.MT5_SERVER):
            print("login() failed")
            return False
            
        return True
        
    def shutdown(self):
        mt5.shutdown()
        
    def get_positions(self, symbol=None):
        if symbol is None:
            symbol = self.default_symbol
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            return []
        return positions
        
    def place_order(self, order_type, stop_loss_pips, take_profit_pips, symbol=None, volume=None, limit_price=None):
        if symbol is None:
            symbol = self.default_symbol
        if volume is None:
            volume = self.lot
            
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            print(f"{symbol} not found")
            return None
            
        if not symbol_info.visible:
            print(f"{symbol} is not visible, trying to switch on")
            if not mt5.symbol_select(symbol, True):
                print(f"symbol_select({symbol}) failed")
                return None
                
        point = symbol_info.point
        tick = mt5.symbol_info_tick(symbol)
        
        action = mt5.TRADE_ACTION_DEAL
        price_to_use = 0.0
        
        # Determine Price
        if limit_price:
            action = mt5.TRADE_ACTION_PENDING
            price_to_use = limit_price
            # Adjust Limit Order Types
            if order_type == mt5.ORDER_TYPE_BUY:
                order_type = mt5.ORDER_TYPE_BUY_LIMIT
            elif order_type == mt5.ORDER_TYPE_SELL:
                order_type = mt5.ORDER_TYPE_SELL_LIMIT
        else:
            # Market Order
            price_to_use = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

        # Calculate SL/TP
        if order_type in [mt5.ORDER_TYPE_BUY, mt5.ORDER_TYPE_BUY_LIMIT]:
             sl = price_to_use - stop_loss_pips * point
             tp = price_to_use + take_profit_pips * point
        else:
             sl = price_to_use + stop_loss_pips * point
             tp = price_to_use - take_profit_pips * point
        
        request = {
            "action": action,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price_to_use,
            "sl": sl,
            "tp": tp,
            "deviation": self.deviation,
            "magic": 234000,
            "comment": "ML Scalper Advanced",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Order failed: {result.comment} ({result.retcode})")
            return None
            
        print(f"Order successful: {result.order}")
        return result

    def modify_position(self, ticket, sl, tp):
        """Modifies SL/TP of an existing position"""
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "sl": sl,
            "tp": tp,
            "magic": 234000,
        }
        
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
             # print(f"Modify failed: {result.comment}")
             # Improve logging only on real failure (re-quotes happen)
             pass
        return result

    def close_position(self, ticket, symbol=None):
        # We need symbol to get tick price for closing
        position = mt5.positions_get(ticket=ticket)
        if position is None or len(position) == 0:
            print("Position not found")
            return
            
        pos = position[0]
        current_symbol = pos.symbol
        
        tick = mt5.symbol_info_tick(current_symbol)
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": current_symbol,
            "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask,
            "deviation": self.deviation,
            "magic": 234000,
            "comment": "Close ML Scalper",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
            "position": ticket
        }
        
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Close failed: {result.comment}")
        else:
            print(f"Position {ticket} closed")
