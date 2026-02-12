import joblib
import pandas as pd
import os
import sys
import MetaTrader5 as mt5
import torch
import time

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings
from data import loader
from strategy import features

# Try importing HF Predictor
try:
    from strategy.hf_predictor import HFPredictor
    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False
    print("HF Predictor not available.")
except Exception as e:
    HF_AVAILABLE = False
    print(f"Error loading HF Predictor: {e}")

class ScalpingStrategy:
    def __init__(self, mt5_client):
        self.client = mt5_client
        self.model = None
        self.feature_cols = None
        self.hf_predictor = None
        
        # Cooldown State
        self.last_trade_time = {} # Symbol -> timestamp
        
        if HF_AVAILABLE:
            try:
                self.hf_predictor = HFPredictor("amazon/chronos-t5-tiny")
            except Exception as e:
                print(f"Failed to init Chronos: {e}")
                self.hf_predictor = None
        
    def load_model(self):
        try:
            self.model = joblib.load(settings.MODEL_PATH)
            feature_path = settings.MODEL_PATH.replace('.pkl', '_features.pkl')
            if os.path.exists(feature_path):
                self.feature_cols = joblib.load(feature_path)
            print("Model loaded successfully.")
            return True
        except Exception as e:
            print(f"Error loading model: {e}")
            return False

    def get_h1_trend(self, symbol):
        """Checks the hourly trend using 50 SMA."""
        if not settings.H1_TREND_FILTER:
            return 0 # Neutral/Disabled
            
        df = loader.get_historical_data(symbol, "H1", 60)
        if df is None or len(df) < 55:
            return 0
            
        sma_50 = df['close'].rolling(window=50).mean().iloc[-1]
        close = df['close'].iloc[-1]
        
        if close > sma_50:
            return 1 # UP Trend
        else:
            return -1 # DOWN Trend

    def manage_positions(self, symbol):
        """Trailing Stop Logic"""
        positions = self.client.get_positions(symbol)
        if not positions:
            return
            
        point = mt5.symbol_info(symbol).point
        current_tick = mt5.symbol_info_tick(symbol)
        
        for pos in positions:
            # Trailing Stop
            if pos.type == mt5.ORDER_TYPE_BUY:
                current_price = current_tick.bid
                profit_percent = (current_price - pos.price_open) / pos.price_open
                
                # Activate if profit > 0.5% (adjustable)
                if profit_percent > settings.TRAILING_STOP_ACTIVATE_PERCENT:
                    new_sl = current_price - (settings.TRAILING_STOP_STEP_PERCENT * pos.price_open)
                    if new_sl > pos.sl:
                         self.client.modify_position(pos.ticket, new_sl, pos.tp)
                         # print(f"[{symbol}] Trailing Stop Updated")
                         
            elif pos.type == mt5.ORDER_TYPE_SELL:
                current_price = current_tick.ask
                # Similar logic for Sell if implemented
                pass

    def check_market(self, symbol):
        # 1. Manage existing positions first
        self.manage_positions(symbol)
        
        # 2. Check Cooldown
        last_time = self.last_trade_time.get(symbol, 0)
        if time.time() - last_time < settings.COOLDOWN_SECONDS:
            return

        # 3. Get Data
        df = loader.get_historical_data(symbol, settings.TIMEFRAME, 200)
        
        if df is None or len(df) < 100:
            print(f"[{symbol}] Not enough data.")
            return
            
        # 4. Multi-Timeframe Filter
        h1_trend = self.get_h1_trend(symbol)
        
        # 5. Features & Prediction
        df_features = features.add_technical_features(df)
        last_row = df_features.iloc[-1:]
        
        if self.feature_cols:
            X = last_row[self.feature_cols]
        else:
            drop_cols = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume', 'target']
            X = last_row.drop(columns=[c for c in drop_cols if c in last_row.columns])
            
        rf_prediction = self.model.predict(X)[0]
        rf_prob = self.model.predict_proba(X)[0][1]
        
        # 6. HF Chronos
        hf_signal = 0 
        
        if self.hf_predictor:
            recent_closes = torch.tensor(df['close'].values[-60:])
            try:
                forecast = self.hf_predictor.predict(recent_closes.unsqueeze(0), prediction_length=12)
                current_price = df['close'].iloc[-1]
                future_price = forecast[0, 5].item()
                
                if future_price > current_price:
                    hf_signal = 1
                else:
                    hf_signal = -1
            except Exception as e:
                pass
        
        # 7. Combined Logic with Filters
        print(f"[{symbol}] RF: {rf_prob:.2f} | HF: {hf_signal} | Trend: {h1_trend}")
        
        # FILTER: Only Buy if H1 Trend is UP or Neutral
        trend_ok = (h1_trend >= 0)
        
        if rf_prediction == 1 and rf_prob > 0.55 and (hf_signal == 1 or not self.hf_predictor) and trend_ok:
            positions = self.client.get_positions(symbol=symbol)
            if not positions:
                print(f"[{symbol}] >>> STRONG BUY SIGNAL <<<")
                
                # Dynamic Sizing
                lot = settings.LOT_SIZE
                if rf_prob > 0.7:
                    lot = lot * settings.RISK_FACTOR_MAX # Increase size for high confidence
                
                # Execution
                result = None
                if settings.USE_LIMIT_ORDERS:
                     # Place Limit at Bid (Better entry)
                     tick = mt5.symbol_info_tick(symbol)
                     result = self.client.place_order(mt5.ORDER_TYPE_BUY, settings.SL_PIPS, settings.TP_PIPS, symbol=symbol, volume=lot, limit_price=tick.bid)
                else:
                     result = self.client.place_order(mt5.ORDER_TYPE_BUY, settings.SL_PIPS, settings.TP_PIPS, symbol=symbol, volume=lot)
                
                if result:
                    print(f"[{symbol}] Order Executed.")
                    self.last_trade_time[symbol] = time.time()
            else:
                print(f"[{symbol}] Position already open.")
