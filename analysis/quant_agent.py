
import os
import joblib
import pandas as pd
import numpy as np
import torch
import xgboost as xgb
from config import settings
from strategy import features

# Try imports
try:
    from strategy.hf_predictor import HFPredictor
    HF_AVAILABLE = True
except: HF_AVAILABLE = False

try:
    from strategy.lag_llama_predictor import get_lag_llama_predictor
    LAG_LLAMA_AVAILABLE = True
except: LAG_LLAMA_AVAILABLE = False

try:
    from strategy.lstm_predictor import LSTMPredictor
    LSTM_AVAILABLE = True
except: LSTM_AVAILABLE = False

try:
    from strategy.tabtransformer_predictor import TabTransformerPredictor, load_tabtransformer_predictor
    TABTRANSFORMER_AVAILABLE = True
except: TABTRANSFORMER_AVAILABLE = False

try:
    from strategy.sequence_transformer import SequenceTransformerPredictor, load_sequence_transformer
    SEQ_TRANSFORMER_AVAILABLE = True
except: SEQ_TRANSFORMER_AVAILABLE = False

def _strip_suffix(symbol):
    for suffix in ['m', 'c']:
        if symbol.endswith(suffix) and len(symbol) > 3:
            base = symbol[:-len(suffix)]
            if len(base) >= 6: return base
    return symbol

class QuantAgent:
    """
    The 'Technician' Agent.
    Responsibilities:
    1. ML Inference (RF, XGBoost, LSTM, TabTransformer)
    2. Technical Analysis (Trend, Confluence)
    3. Signal Generation (Score 0-6)
    """
    def __init__(self):
        self.model = None       # RF
        self.xgb_model = None   # XGBoost
        self.tabtransformer_predictor = None  # TabTransformer (NEW)
        self.seq_transformer_predictor = None # Sequence Transformer (NEW)
        self.feature_cols = None
        self.hf_predictor = None
        self.lstm_predictors = {}
        
        self._load_models()
        print("[AGENT] QuantAgent initialized.")

    def _load_models(self):
        # 1. RF/XGBoost
        try:
            if os.path.exists(settings.MODEL_PATH):
                self.model = joblib.load(settings.MODEL_PATH)
            
            if getattr(settings, 'USE_XGBOOST', False) and os.path.exists(settings.XGB_MODEL_PATH):
                self.xgb_model = joblib.load(settings.XGB_MODEL_PATH)
                # Optimize for single-row inference and avoid warning
                try:
                    self.xgb_model.get_booster().set_param({'device': 'cpu'})
                except: pass
                
            feat_path = settings.MODEL_PATH.replace('.pkl', '_features.pkl')
            if os.path.exists(feat_path):
                self.feature_cols = joblib.load(feat_path)
        except Exception as e:
            print(f"[QUANT] Model load error: {e}")

        # 2. TabTransformer (NEW - Industry-Leading Architecture)
        if TABTRANSFORMER_AVAILABLE and getattr(settings, 'USE_TABTRANSFORMER', True):
            try:
                models_dir = os.path.dirname(settings.MODEL_PATH)
                tabtransformer_path = os.path.join(models_dir, 'tabtransformer_v1.pt')
                if os.path.exists(tabtransformer_path):
                    device = 'cuda' if torch.cuda.is_available() else 'cpu'
                    self.tabtransformer_predictor = load_tabtransformer_predictor(tabtransformer_path, device=device)
                    print(f"[QUANT] TabTransformer loaded from {tabtransformer_path}")
            except Exception as e:
                print(f"[QUANT] TabTransformer load error: {e}")

        # 2b. Sequence Transformer (NEW - Attention-based temporal model)
        if hasattr(settings, 'USE_SEQ_TRANSFORMER') and getattr(settings, 'USE_SEQ_TRANSFORMER', True) and SEQ_TRANSFORMER_AVAILABLE:
            try:
                models_dir = os.path.dirname(settings.MODEL_PATH)
                seq_transformer_path = os.path.join(models_dir, 'seq_transformer_v1.pth')
                if os.path.exists(seq_transformer_path):
                    device = 'cuda' if torch.cuda.is_available() else 'cpu'
                    self.seq_transformer_predictor = load_sequence_transformer(seq_transformer_path, device=device)
                    print(f"[QUANT] Sequence Transformer loaded from {seq_transformer_path}")
            except Exception as e:
                print(f"[QUANT] Sequence Transformer load error: {e}")

        # 3. LSTM
        if settings.USE_LSTM and LSTM_AVAILABLE:
            self._load_lstm_models()

        # 4. Lag-Llama/Chronos
        if settings.USE_LAG_LLAMA and LAG_LLAMA_AVAILABLE:
            try:
                self.hf_predictor = get_lag_llama_predictor(settings)
            except: pass
        elif HF_AVAILABLE:
            try:
                self.hf_predictor = HFPredictor("amazon/chronos-t5-tiny")
            except: pass

    def _load_lstm_models(self):
        key_symbols = ["EURUSD", "XAUUSD", "BTCUSD", "GBPUSD"]
        models_dir = os.path.dirname(settings.LSTM_MODEL_PATH)
        
        for sym in key_symbols:
            m_path = os.path.join(models_dir, f"lstm_{sym}.pth")
            s_path = os.path.join(models_dir, f"lstm_{sym}_scaler.pkl")
            if os.path.exists(m_path) and os.path.exists(s_path):
                try:
                    self.lstm_predictors[sym] = LSTMPredictor(
                        model_path=m_path, scaler_path=s_path,
                        device='cuda' if torch.cuda.is_available() else 'cpu'
                    )
                except: pass
        
        # Default
        try:
            self.lstm_predictors['default'] = LSTMPredictor(
                model_path=settings.LSTM_MODEL_PATH,
                scaler_path=settings.LSTM_SCALER_PATH,
                device='cuda' if torch.cuda.is_available() else 'cpu'
            )
        except: pass

    def analyze(self, symbol, data_dict):
        """
        Full Quant Analysis.
        Orchestrates signal generation.
        """
        df = data_dict.get(settings.TIMEFRAME)
        if df is None: return None
        
        # Feature Engineering here? 
        # Strategy used to do it. Better to do it in Agent.
        try:
            df = features.add_technical_features(df)
        except: return None
        
        if len(df) < 50: return None
        
        # Trends
        h1 = self._compute_trend(data_dict.get('H1'))
        m5 = self._compute_trend(data_dict.get('M5'))
        h4 = self._compute_trend(data_dict.get('H4'))
        
        # ML Predictions (with symbol for multi-pair model)
        rf_prob, _ = self._get_rf_prediction(df, symbol)
        xgb_prob, _ = self._get_xgb_prediction(df, symbol)
        tabtransformer_prob, _ = self._get_tabtransformer_prediction(df, symbol)  # NEW
        seq_transformer_prob, _ = self._get_seq_transformer_prediction(df)        # NEW
        
        # Ensemble ML probability (average of available models)
        models_available = sum([self.model is not None, self.xgb_model is not None, self.tabtransformer_predictor is not None, self.seq_transformer_predictor is not None])
        if models_available > 0:
            ml_prob = (rf_prob * (self.model is not None) + 
                      xgb_prob * (self.xgb_model is not None) + 
                      tabtransformer_prob * (self.tabtransformer_predictor is not None) +
                      seq_transformer_prob * (self.seq_transformer_predictor is not None)) / models_available
        else:
            ml_prob = 0.5
        
        # AI Signal
        ai_signal = self._get_ai_signal(symbol, df)
        
        # Get LSTM prediction
        lstm_pred = self._get_lstm_prediction(symbol, df)
        
        # Get HF (Chronos/Lag-Llama) prediction
        hf_pred = self._get_hf_prediction(symbol, df)
        
        # Scoring
        buy_score, buy_details = self._calculate_confluence(symbol, df, "buy", h1, h4, m5)
        sell_score, sell_details = self._calculate_confluence(symbol, df, "sell", h1, h4, m5)
        
        best_score = max(buy_score, sell_score)
        direction = "BUY" if buy_score >= sell_score else "SELL"
        details = buy_details if direction == "BUY" else sell_details
        
        # Enhanced Ensemble Voting with TabTransformer & Sequence Transformer
        ensemble_score, agreement_count, model_votes = self._ensemble_vote(
            ml_prob, ai_signal, best_score, lstm_pred, hf_pred, tabtransformer_prob, seq_transformer_prob
        )
        
        return {
            'symbol': symbol,
            'direction': direction,
            'score': best_score,
            'details': details,
            'ml_prob': ml_prob,
            'ai_signal': ai_signal,
            'ensemble_score': ensemble_score,
            'agreement_count': agreement_count,
            'model_votes': model_votes,
            'lstm_pred': lstm_pred,
            'hf_pred': hf_pred,
            'h4_trend': h4,
            'features': df.iloc[-1], # For quick access
            'data': df # Full history for Regime Detector
        }

    # ─── Copied helpers ──────────────────────────────────────────────────

    def _compute_trend(self, df, sma_period=50):
        if df is None or len(df) < sma_period + 5: return 0
        sma = df['close'].rolling(window=sma_period).mean().iloc[-1]
        close = df['close'].iloc[-1]
        if close > sma * 1.001: return 1
        elif close < sma * 0.999: return -1
        return 0

    def _get_rf_prediction(self, df, symbol=None):
        if self.model is None: return 0.5, 0
        last = df.iloc[-1:]
        X = self._prepare_X(last, symbol)
        try:
            # Convert to numpy array to avoid feature names warning
            X_array = X.values if hasattr(X, 'values') else X
            return self.model.predict_proba(X_array)[0][1], self.model.predict(X_array)[0]
        except: return 0.5, 0
        
    def _get_xgb_prediction(self, df, symbol=None):
        if self.xgb_model is None: return 0.5, 0
        last = df.iloc[-1:]
        X = self._prepare_X(last, symbol)
        try:
            # Convert to numpy array to avoid feature names warning
            X_array = X.values if hasattr(X, 'values') else X
            return self.xgb_model.predict_proba(X_array)[0][1], self.xgb_model.predict(X_array)[0]
        except: return 0.5, 0

    def _get_tabtransformer_prediction(self, df, symbol=None):
        """Get TabTransformer prediction (NEW - Industry-Leading Attention-Based Model)."""
        if self.tabtransformer_predictor is None: return 0.5, 0
        last = df.iloc[-1:]
        X = self._prepare_X(last, symbol)
        try:
            X_df = pd.DataFrame([X.iloc[0]]) if isinstance(X, pd.DataFrame) and len(X) > 0 else X
            proba = self.tabtransformer_predictor.predict_proba(X_df)
            prob_class_1 = proba[0][1] if len(proba) > 0 else 0.5
            predicted_class = 1 if prob_class_1 > 0.5 else 0
            return prob_class_1, predicted_class
        except Exception as e:
            return 0.5, 0

    def _get_seq_transformer_prediction(self, df):
        """Get sequence transformer prediction using a sliding window of historical bars."""
        if self.seq_transformer_predictor is None: return 0.5, 0
        try:
            probs, _ = self.seq_transformer_predictor.predict(df)
            prob_class_1 = probs[1]
            predicted_class = 1 if prob_class_1 > 0.5 else 0
            return prob_class_1, predicted_class
        except Exception as e:
            return 0.5, 0

    def _prepare_X(self, row, symbol=None):
        """
        Prepare feature matrix for prediction.
        Adds symbol-specific features if training included them.
        """
        # Make a copy to avoid modifying original
        X = row.copy()
        
        # Add symbol features if they were in training
        if symbol and self.feature_cols:
            if 'symbol_id' in self.feature_cols and 'symbol_id' not in X.columns:
                X['symbol_id'] = hash(symbol) % 1000
            if 'volatility_class' in self.feature_cols and 'volatility_class' not in X.columns:
                # Calculate volatility class on the fly
                if 'atr' in X.columns and 'close' in X.columns:
                    atr_mean = X['atr'].mean() if hasattr(X['atr'], 'mean') else X['atr'].iloc[0]
                    close_mean = X['close'].mean() if hasattr(X['close'], 'mean') else X['close'].iloc[0]
                    vol_ratio = atr_mean / close_mean if close_mean > 0 else 0
                    if vol_ratio < 0.0005:
                        X['volatility_class'] = 0
                    elif vol_ratio < 0.001:
                        X['volatility_class'] = 1
                    elif vol_ratio < 0.005:
                        X['volatility_class'] = 2
                    else:
                        X['volatility_class'] = 3
                else:
                    X['volatility_class'] = 1  # Default medium volatility
        
        if self.feature_cols:
            cols = [c for c in self.feature_cols if c in X.columns]
            if cols: return X[cols]
        exclude = ['time','open','high','low','close','tick_volume','spread','real_volume','target']
        return X.drop(columns=[c for c in exclude if c in X.columns], errors='ignore')

    def _get_lstm_prediction(self, symbol, df):
        """Get raw LSTM prediction as percentage change."""
        base = _strip_suffix(symbol)
        lstm = self.lstm_predictors.get(base) or self.lstm_predictors.get('default')
        if lstm and 'close' in df.columns:
            try:
                pred_price = lstm.predict(df)
                curr_price = df['close'].iloc[-1]
                return (pred_price - curr_price) / curr_price  # Return % change
            except:
                return None
        return None
        
    def _get_hf_prediction(self, symbol, df):
        """Get HF model (Chronos/Lag-Llama) prediction as percentage change."""
        if self.hf_predictor and 'close' in df.columns:
            try:
                recent = torch.tensor(df['close'].values[-60:], dtype=torch.float32)
                pred = self.hf_predictor.predict(recent.unsqueeze(0), prediction_length=12)
                curr = df['close'].iloc[-1]
                fut = pred[0, 5].item()
                return (fut - curr) / curr  # Return % change
            except:
                return None
        return None

    def _get_ai_signal(self, symbol, df):
        """Combined AI signal from all available models."""
        signals = []
        
        # 1. Chronos/Lag-Llama
        hf_pred = self._get_hf_prediction(symbol, df)
        if hf_pred is not None:
            if hf_pred > 0.0003: signals.append(1)
            elif hf_pred < -0.0003: signals.append(-1)
            else: signals.append(0)
            
        # 2. LSTM
        lstm_pred = self._get_lstm_prediction(symbol, df)
        if lstm_pred is not None:
            try:
                if lstm.predict(df) > df['close'].iloc[-1] * 1.0003: signals.append(1)
                elif lstm.predict(df) < df['close'].iloc[-1] * 0.9997: signals.append(-1)
                else: signals.append(0)
            except: pass
            
        if not signals: return 0
        avg = sum(signals)/len(signals)
        return 1 if avg > 0.3 else -1 if avg < -0.3 else 0

    def _ensemble_vote(self, rf_prob, ai_signal, confluence_score, lstm_pred=None, hf_pred=None, tabtransformer_prob=None, seq_transformer_prob=None):
        """
        Enhanced Ensemble Voting System with TabTransformer and Sequence Transformer.
        Combines multiple AI models (RF, XGBoost, TabTransformer, Sequence Transformer, LSTM, HF) with weighted voting.
        Returns: (ensemble_score, agreement_count, model_votes)
        """
        votes = {
            'rf': {'direction': 'NEUTRAL', 'weight': 0.10, 'confidence': 0},
            'tabtransformer': {'direction': 'NEUTRAL', 'weight': 0.20, 'confidence': 0},  # NEW - Higher weight
            'seq_transformer': {'direction': 'NEUTRAL', 'weight': 0.25, 'confidence': 0}, # NEW - Highest weight (Sequence Attention)
            'lstm': {'direction': 'NEUTRAL', 'weight': 0.10, 'confidence': 0},
            'hf': {'direction': 'NEUTRAL', 'weight': 0.15, 'confidence': 0},
            'ai': {'direction': 'NEUTRAL', 'weight': 0.15, 'confidence': 0},
            'confluence': {'direction': 'NEUTRAL', 'weight': 0.05, 'confidence': 0}
        }
        
        # 1. Random Forest / XGBoost Vote (Calibrated Probability >= 0.75)
        # Using 0.75 threshold as the probabilities are now mathematically scaled to [0, 1] 
        # via CalibratedClassifierCV, so 0.75 directly means 75% true win likelihood.
        if rf_prob >= 0.75:
            votes['rf'] = {'direction': 'BUY', 'weight': 0.20, 'confidence': rf_prob}
        elif rf_prob <= 0.25:
            votes['rf'] = {'direction': 'SELL', 'weight': 0.20, 'confidence': 1 - rf_prob}
        else:
            votes['rf'] = {'direction': 'NEUTRAL', 'weight': 0.20, 'confidence': 0.5}
        
        # 2. TabTransformer Vote (NEW - Higher weight due to attention-based architecture)
        if tabtransformer_prob is not None and tabtransformer_prob > 0:
            if tabtransformer_prob > 0.65:  # Slightly higher threshold (better confidence)
                votes['tabtransformer'] = {'direction': 'BUY', 'weight': 0.25, 'confidence': tabtransformer_prob}
            elif tabtransformer_prob < 0.35:
                votes['tabtransformer'] = {'direction': 'SELL', 'weight': 0.25, 'confidence': 1 - tabtransformer_prob}
            else:
                votes['tabtransformer'] = {'direction': 'NEUTRAL', 'weight': 0.20, 'confidence': 0.5}
                
        # 2b. Sequence Transformer Vote (Highest Weight due to Temporal Attention)
        if seq_transformer_prob is not None and seq_transformer_prob > 0:
            if seq_transformer_prob > 0.65:
                votes['seq_transformer'] = {'direction': 'BUY', 'weight': 0.25, 'confidence': seq_transformer_prob}
            elif seq_transformer_prob < 0.35:
                votes['seq_transformer'] = {'direction': 'SELL', 'weight': 0.25, 'confidence': 1 - seq_transformer_prob}
            else:
                votes['seq_transformer'] = {'direction': 'NEUTRAL', 'weight': 0.25, 'confidence': 0.5}
            
        # 3. LSTM Vote (if available)
        if lstm_pred is not None:
            if lstm_pred > 0.001:  # Bullish prediction
                votes['lstm'] = {'direction': 'BUY', 'weight': 0.15, 'confidence': min(abs(lstm_pred) * 100, 1.0)}
            elif lstm_pred < -0.001:  # Bearish prediction
                votes['lstm'] = {'direction': 'SELL', 'weight': 0.15, 'confidence': min(abs(lstm_pred) * 100, 1.0)}
            else:
                votes['lstm'] = {'direction': 'NEUTRAL', 'weight': 0.15, 'confidence': 0.5}
                
        # 4. HF Model Vote (Chronos/Lag-Llama)
        if hf_pred is not None:
            if hf_pred > 0.0003:
                votes['hf'] = {'direction': 'BUY', 'weight': 0.15, 'confidence': 0.7}
            elif hf_pred < -0.0003:
                votes['hf'] = {'direction': 'SELL', 'weight': 0.15, 'confidence': 0.7}
            else:
                votes['hf'] = {'direction': 'NEUTRAL', 'weight': 0.15, 'confidence': 0.5}
                
        # 5. AI Signal Vote (LSTM + HF combined)
        if ai_signal == 1:
            votes['ai'] = {'direction': 'BUY', 'weight': 0.15, 'confidence': 0.75}
        elif ai_signal == -1:
            votes['ai'] = {'direction': 'SELL', 'weight': 0.15, 'confidence': 0.75}
        else:
            votes['ai'] = {'direction': 'NEUTRAL', 'weight': 0.15, 'confidence': 0.5}
            
        # 6. Confluence Score Vote (reduced weight, now supporting role)
        if confluence_score >= 5:
            votes['confluence'] = {'direction': 'BUY', 'weight': 0.10, 'confidence': confluence_score / 6.0}
        elif confluence_score >= 4:
            votes['confluence'] = {'direction': 'BUY', 'weight': 0.10, 'confidence': confluence_score / 6.0}
        else:
            votes['confluence'] = {'direction': 'NEUTRAL', 'weight': 0.10, 'confidence': confluence_score / 6.0}
        
        # Count agreements for BUY and SELL
        buy_votes = sum(1 for v in votes.values() if v['direction'] == 'BUY')
        sell_votes = sum(1 for v in votes.values() if v['direction'] == 'SELL')
        neutral_votes = sum(1 for v in votes.values() if v['direction'] == 'NEUTRAL')
        
        # Calculate weighted ensemble score (0-1 scale)
        buy_score = sum(v['weight'] * v['confidence'] for v in votes.values() if v['direction'] == 'BUY')
        sell_score = sum(v['weight'] * v['confidence'] for v in votes.values() if v['direction'] == 'SELL')
        
        # Normalize to 0-1
        ensemble_score = max(buy_score, sell_score)
        
        # Determine agreement level
        max_agreement = max(buy_votes, sell_votes)
        
        return round(ensemble_score, 3), max_agreement, votes

    def _calculate_confluence(self, symbol, df, direction, h1, h4, m5=0):
        score = 0
        details = {}
        last = df.iloc[-1]
        
        # Trends
        if getattr(settings, 'M5_TREND_FILTER', False):
            # Strict M5 Alignment for Scalping
            if direction=="buy" and m5==-1: return 0, {'M5':'BLOCK'}
            if direction=="sell" and m5==1: return 0, {'M5':'BLOCK'}
            if (direction=="buy" and m5==1) or (direction=="sell" and m5==-1):
                score+=1; details['M5']='OK'
            else: details['M5']='-'

        if settings.H4_TREND_FILTER:
            if direction=="buy" and h4==-1: return 0, {'H4':'BLOCK'}
            if direction=="sell" and h4==1: return 0, {'H4':'BLOCK'}
            if (direction=="buy" and h4==1) or (direction=="sell" and h4==-1):
                score+=1; details['H4']='OK'
            else: details['H4']='-'
            
        if settings.H1_TREND_FILTER:
            if (direction=="buy" and h1>=1) or (direction=="sell" and h1<=-1):
                score+=1; details['H1']='OK'
            else: details['H1']='-'
            
        # ML & AI
        threshold = settings.RF_PROB_THRESHOLD
        prob, _ = self._get_rf_prediction(df) # Recalc or pass? 
        # Better to pass. But method signature... 
        # I'll re-call or optimize later. Re-calling is cheap (cached in memory if efficient, but here it's fast)
        
        if direction=="buy":
            if prob > 0.85: score+=2; details['ML']='OK+'
            elif prob > threshold: score+=1; details['ML']='OK'
            else: details['ML']='NO'
        else:
            if prob < 0.15: score+=2; details['ML']='OK+'
            elif prob < (1-threshold): score+=1; details['ML']='OK'
            else: details['ML']='NO'
            
        ai = self._get_ai_signal(symbol, df)
        if (direction=="buy" and ai==1) or (direction=="sell" and ai==-1):
            score+=1; details['AI']='OK'
        else: details['AI']='NO'
        
        # SMC Confluence
        smc_hit = False
        if direction == "buy":
            if last.get('near_ob_bullish', 0) == 1 or last.get('near_fvg_bullish', 0) == 1: smc_hit = True
            if last.get('liq_sweep_low', 0) == 1: smc_hit = True
        else:
            if last.get('near_ob_bearish', 0) == 1 or last.get('near_fvg_bearish', 0) == 1: smc_hit = True
            if last.get('liq_sweep_high', 0) == 1: smc_hit = True
            
        if smc_hit: score+=1; details['SMC']='OK'
        else: details['SMC']='NO'
        
        # ADX
        if last.get('adx',0) > 25: score+=1; details['ADX']='OK'
        else: details['ADX']='NO'
        
        return score, details
