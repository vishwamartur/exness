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
from market_data import loader
from strategy import features

# Try importing HF Predictor and Lag-Llama
try:
    from strategy.hf_predictor import HFPredictor
    HF_AVAILABLE = True
except ImportError:
    HF_AVAILABLE = False
    print("HF Predictor not available.")
except Exception as e:
    HF_AVAILABLE = False
    print(f"Error loading HF Predictor: {e}")

try:
    from strategy.lag_llama_predictor import get_lag_llama_predictor
    LAG_LLAMA_AVAILABLE = True
    print("LagLlamaPredictor module found.")
except ImportError:
    LAG_LLAMA_AVAILABLE = False
    print("LagLlamaPredictor module not found.")
except Exception as e:
    LAG_LLAMA_AVAILABLE = False
    print(f"Error loading LagLlamaPredictor: {e}")

try:
    from strategy.lstm_predictor import LSTMPredictor
    LSTM_AVAILABLE = True
    print("LSTMPredictor module found.")
except ImportError:
    LSTM_AVAILABLE = False
    print("LSTMPredictor module not found.")
except Exception as e:
    LSTM_AVAILABLE = False
    print(f"Error loading LSTMPredictor: {e}")

class ScalpingStrategy:
    def __init__(self, mt5_client):
        self.client = mt5_client
        self.model = None
        self.feature_cols = None
        self.hf_predictor = None
        self.lstm_predictor = None
        
        # Cooldown State
        self.last_trade_time = {} # Symbol -> timestamp
        
        if settings.USE_LAG_LLAMA:
            if LAG_LLAMA_AVAILABLE:
                try:
                    print("Initializing Lag-Llama...")
                    self.hf_predictor = get_lag_llama_predictor(settings)
                    print("Lag-Llama initialized.")
                except Exception as e:
                     print(f"Failed to init Lag-Llama: {e}")
                     self.hf_predictor = None
            else:
                 print("Lag-Llama enabled in settings but module not available.")
        
        elif HF_AVAILABLE: # Fallback to Chronos if Lag-Llama not used/available
            try:
                self.hf_predictor = HFPredictor("amazon/chronos-t5-tiny")
            except Exception as e:
                print(f"Failed to init Chronos: {e}")
                self.hf_predictor = None
                
        if settings.USE_LSTM:
            if LSTM_AVAILABLE:
                try:
                    print("Initializing LSTM...")
                    self.lstm_predictor = LSTMPredictor(
                        model_path=settings.LSTM_MODEL_PATH,
                        scaler_path=settings.LSTM_SCALER_PATH,
                        device='cuda' if torch.cuda.is_available() else 'cpu'
                    )
                    print("LSTM initialized.")
                except Exception as e:
                    print(f"Failed to init LSTM: {e}")
                    self.lstm_predictor = None
            else:
                 print("LSTM enabled in settings but module not available.")
        
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
            
        # Convert to numpy array to avoid feature names warning
        X_array = X.values if hasattr(X, 'values') else X
        rf_prediction = self.model.predict(X_array)[0]
        rf_prob = self.model.predict_proba(X_array)[0][1]
        
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
                
        # 7. LSTM Prediction
        lstm_signal = 0
        lstm_pred_price = 0
        if self.lstm_predictor:
            try:
                # LSTM needs df with features
                lstm_pred_price = self.lstm_predictor.predict(df_features)
                if lstm_pred_price:
                     current_price = df['close'].iloc[-1]
                     if lstm_pred_price > current_price:
                         lstm_signal = 1
                     else:
                         lstm_signal = -1
            except Exception as e:
                print(f"LSTM Prediction Error: {e}")
        
        # 8. Combined Logic with Filters
        print(f"[{symbol}] RF: {rf_prob:.2f} | HF: {hf_signal} | LSTM: {lstm_signal} ({lstm_pred_price:.5f}) | Trend: {h1_trend}")
        
        # FILTER: Only Buy if H1 Trend is UP or Neutral
        trend_ok = (h1_trend >= 0)
        
        # Combined Signal: RF + (HF or LSTM)
        # We can implement a voting mechanism or priority
        # Let's say: RF must be > 0.55 AND (HF==1 OR LSTM==1)
        
        ai_confirmation = (hf_signal == 1) or (lstm_signal == 1)
        # If both are missing, we rely on RF? Or fail safe?
        if not self.hf_predictor and not self.lstm_predictor:
            ai_confirmation = True # Fallback to just RF if no AI available
            
        if rf_prediction == 1 and rf_prob > 0.55 and ai_confirmation and trend_ok:
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
                    print(f"[{symbol}] Buy Order Placed: {result}")
                    self.last_trade_time[symbol] = time.time()
                    return

        # --- SHORT ENTRY LOGIC ---
        # RF Prob < 0.45 means high probability of Class 0 (Down)
        # AI Confirmation: HF or LSTM predicts Drop (-1)
        # Trend: Down or Neutral (<= 0)
        
        ai_sell_confirmation = (hf_signal == -1) or (lstm_signal == -1)
        if not self.hf_predictor and not self.lstm_predictor:
             ai_sell_confirmation = True # Fallback

        if rf_prob < 0.50 and ai_sell_confirmation and h1_trend <= 0:
            positions = self.client.get_positions(symbol=symbol)
            if not positions:
                print(f"[{symbol}] >>> STRONG SELL SIGNAL <<<")
                
                # Dynamic Sizing
                lot = settings.LOT_SIZE
                if rf_prob < 0.3: # Very strong Down signal
                    lot = lot * settings.RISK_FACTOR_MAX
                
                # Execution
                result = None
                tick = mt5.symbol_info_tick(symbol)
                if settings.USE_LIMIT_ORDERS:
                     # Place Sell Limit at Ask (or slightly better? Standard is Sell Limit above market, 
                     # but for immediate entry strategy we might want to just hit Bid or sit on Ask)
                     # For symmetry with Buy (which used Bid), let's use Ask.
                     result = self.client.place_order(mt5.ORDER_TYPE_SELL, settings.SL_PIPS, settings.TP_PIPS, symbol=symbol, volume=lot, limit_price=tick.ask)
                else:
                     result = self.client.place_order(mt5.ORDER_TYPE_SELL, settings.SL_PIPS, settings.TP_PIPS, symbol=symbol, volume=lot)
                
                if result:
                     print(f"[{symbol}] Sell Order Placed: {result}")
                     self.last_trade_time[symbol] = time.time()
                     return
            else:
                print(f"[{symbol}] Position already open.")
