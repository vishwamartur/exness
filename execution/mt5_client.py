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

    def detect_available_symbols(self):
        """
        Auto-detects which symbols are available on this Exness account.
        Tries each base name with Exness suffixes ('', 'm', 'c') and
        populates settings.SYMBOLS with the ones that exist.
        """
        print("\n  Detecting available instruments on your Exness account...")
        
        found_symbols = []
        found_majors = []
        found_minors = []
        found_crypto = []
        found_commodities = []
        
        suffix_detected = None  # Track which suffix works
        
        # Quote currencies we do NOT trade (non-USD denominated exotics)
        BLOCKED_QUOTE_CCY = {'KRW', 'CNH', 'CNY', 'ZAR', 'TRY', 'HUF', 'CZK', 'SEK', 'NOK', 'DKK'}

        for base in settings.ALL_BASE_SYMBOLS:
            matched = None
            for suffix in settings.EXNESS_SUFFIXES:
                candidate = base + suffix
                info = mt5.symbol_info(candidate)
                if info is not None:
                    # Skip trade-disabled / reference symbols (e.g. BTCKRW)
                    # trade_mode: 0=DISABLED, 1=LONGONLY, 2=SHORTONLY, 4=FULL
                    if info.trade_mode == 0:
                        break  # disabled — skip all suffixes for this base
                    # Skip non-USD quote currencies
                    if info.currency_profit in BLOCKED_QUOTE_CCY:
                        break
                    # Enable it in Market Watch if not visible
                    if not info.visible:
                        mt5.symbol_select(candidate, True)
                    matched = candidate
                    if suffix_detected is None and suffix:
                        suffix_detected = suffix
                    break
            
            if matched:
                found_symbols.append(matched)
                # Categorize
                if base in settings.SYMBOLS_FOREX_MAJORS_BASE:
                    found_majors.append(matched)
                elif base in settings.SYMBOLS_FOREX_MINORS_BASE:
                    found_minors.append(matched)
                elif base in settings.SYMBOLS_CRYPTO_BASE:
                    found_crypto.append(matched)
                elif base in settings.SYMBOLS_COMMODITIES_BASE:
                    found_commodities.append(matched)
        
        # Update settings at runtime
        settings.SYMBOLS = found_symbols
        settings.SYMBOLS_FOREX_MAJORS = found_majors
        settings.SYMBOLS_FOREX_MINORS = found_minors
        settings.SYMBOLS_CRYPTO = found_crypto
        settings.SYMBOLS_COMMODITIES = found_commodities
        
        # Report
        suffix_label = f"'{suffix_detected}'" if suffix_detected else "none"
        print(f"  Account suffix: {suffix_label}")
        print(f"  Found {len(found_symbols)} instruments:")
        print(f"    Forex Majors:  {len(found_majors)} — {', '.join(found_majors)}")
        print(f"    Forex Minors:  {len(found_minors)} — {', '.join(found_minors[:5])}{'...' if len(found_minors) > 5 else ''}")
        print(f"    Crypto:        {len(found_crypto)} — {', '.join(found_crypto)}")
        print(f"    Commodities:   {len(found_commodities)} — {', '.join(found_commodities)}")
        
        if not found_symbols:
            print("  ⚠ WARNING: No symbols detected! Check your Exness account.")
            return False
        
        return True
        
    def shutdown(self):
        mt5.shutdown()
    
    # ─── Account Info ────────────────────────────────────────────────────
    
    def get_account_balance(self):
        """Returns current account balance."""
        info = mt5.account_info()
        if info is None:
            print("Failed to get account info")
            return 0.0
        return info.balance

    def get_account_equity(self):
        """Returns current account equity."""
        info = mt5.account_info()
        if info is None:
            return 0.0
        return info.equity

    def get_account_info_dict(self):
        """Returns full account info as dict."""
        info = mt5.account_info()
        if info is None:
            return {}
        return {
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.margin_free,
            "profit": info.profit,
            "leverage": info.leverage,
        }

    def get_history_deals(self, date_from, date_to):
        """Returns list of deals within the specified time range."""
        deals = mt5.history_deals_get(date_from, date_to)
        if deals is None:
            return []
        return deals

    # ─── Dynamic Position Sizing ─────────────────────────────────────────

    def calculate_lot_size(self, symbol, sl_distance_price, risk_percent=None):
        """
        Calculates lot size based on account balance and SL distance.
        
        institutional formula:
            risk_amount = balance * risk_percent / 100
            lot_size = risk_amount / (sl_distance_points * tick_value)
        
        Args:
            symbol: Trading symbol
            sl_distance_price: Stop loss distance in price (e.g., 0.00150 for 15 pips)
            risk_percent: Risk % of account (defaults to settings.RISK_PERCENT)
        
        Returns:
            Calculated lot size, clamped to broker min/max
        """
        if risk_percent is None:
            risk_percent = settings.RISK_PERCENT
        
        balance = self.get_account_balance()
        if balance <= 0:
            return settings.LOT_SIZE  # Fallback
        
        symbol_info = mt5.symbol_info(symbol)
        if symbol_info is None:
            return settings.LOT_SIZE  # Fallback
        
        point = symbol_info.point
        tick_value = symbol_info.trade_tick_value  # Value of 1 tick in account currency
        tick_size = symbol_info.trade_tick_size
        
        if tick_value <= 0 or tick_size <= 0 or sl_distance_price <= 0:
            return settings.LOT_SIZE  # Fallback
        
        # Calculate
        risk_amount = balance * risk_percent / 100.0
        sl_ticks = sl_distance_price / tick_size
        lot_size = risk_amount / (sl_ticks * tick_value)
        
        # Clamp to broker limits
        min_lot = symbol_info.volume_min
        max_lot = symbol_info.volume_max
        step = symbol_info.volume_step
        
        # Round to step size
        lot_size = max(min_lot, min(max_lot, lot_size))
        lot_size = round(lot_size / step) * step
        lot_size = round(lot_size, 2)  # Clean floating point
        
        return lot_size
        
    # ─── Positions ───────────────────────────────────────────────────────
    
    def get_positions(self, symbol=None):
        if symbol is None:
            symbol = self.default_symbol
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            return []
        return positions
    
    def get_all_positions(self):
        """Returns all open positions across all symbols."""
        positions = mt5.positions_get()
        if positions is None:
            return []
        return positions

    # ─── Order Placement ─────────────────────────────────────────────────
        
    def place_order(self, order_type, symbol=None, volume=None, 
                    sl_price=None, tp_price=None,
                    stop_loss_pips=0, take_profit_pips=0, 
                    limit_price=None):
        """
        Places an order. Supports both ATR-based (sl_price/tp_price) 
        and legacy pip-based SL/TP.
        """
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
            if order_type == mt5.ORDER_TYPE_BUY:
                order_type = mt5.ORDER_TYPE_BUY_LIMIT
            elif order_type == mt5.ORDER_TYPE_SELL:
                order_type = mt5.ORDER_TYPE_SELL_LIMIT
        else:
            price_to_use = tick.ask if order_type == mt5.ORDER_TYPE_BUY else tick.bid

        # Calculate SL/TP
        if sl_price is not None and tp_price is not None:
            # ATR-based: use exact prices
            sl = sl_price
            tp = tp_price
        else:
            # Legacy pip-based fallback
            if order_type in [mt5.ORDER_TYPE_BUY, mt5.ORDER_TYPE_BUY_LIMIT]:
                sl = price_to_use - stop_loss_pips * point if stop_loss_pips > 0 else 0
                tp = price_to_use + take_profit_pips * point if take_profit_pips > 0 else 0
            else:
                sl = price_to_use + stop_loss_pips * point if stop_loss_pips > 0 else 0
                tp = price_to_use - take_profit_pips * point if take_profit_pips > 0 else 0
        
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
            "comment": "Institutional",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Order failed: {result.comment} ({result.retcode})")
            return None
            
        print(f"Order successful: {result.order} | Vol: {volume} | SL: {sl:.5f} | TP: {tp:.5f}")
        return result

    # ─── Position Management ─────────────────────────────────────────────

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
            pass  # Suppress re-quote noise
        return result

    def partial_close(self, ticket, fraction=0.5):
        """
        Closes a fraction of a position (e.g., 50% at first TP).
        Institutional approach: lock in profits, let the rest run.
        """
        position = mt5.positions_get(ticket=ticket)
        if position is None or len(position) == 0:
            print(f"Position {ticket} not found for partial close")
            return None
            
        pos = position[0]
        close_volume = round(pos.volume * fraction, 2)
        
        # Ensure minimum lot
        symbol_info = mt5.symbol_info(pos.symbol)
        if symbol_info and close_volume < symbol_info.volume_min:
            print(f"Partial close volume {close_volume} below minimum {symbol_info.volume_min}")
            return None
        
        tick = mt5.symbol_info_tick(pos.symbol)
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": close_volume,
            "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if pos.type == mt5.ORDER_TYPE_BUY else tick.ask,
            "deviation": self.deviation,
            "magic": 234000,
            "comment": "Partial TP",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
            "position": ticket,
        }
        
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Partial close failed: {result.comment}")
            return None
        else:
            print(f"Partial close {ticket}: {close_volume} lots closed ({fraction*100:.0f}%)")
            return result

    def close_position(self, ticket, symbol=None):
        """Fully closes a position by ticket."""
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
            "comment": "Close Institutional",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
            "position": ticket,
        }
        
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            print(f"Close failed: {result.comment}")
        else:
            print(f"Position {ticket} closed")
