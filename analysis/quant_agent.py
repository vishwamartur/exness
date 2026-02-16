
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
    1. ML Inference (RF, XGBoost, LSTM)
    2. Technical Analysis (Trend, Confluence)
    3. Signal Generation (Score 0-6)
    """
    def __init__(self):
        self.model = None       # RF
        self.xgb_model = None   # XGBoost
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
                
            feat_path = settings.MODEL_PATH.replace('.pkl', '_features.pkl')
            if os.path.exists(feat_path):
                self.feature_cols = joblib.load(feat_path)
        except Exception as e:
            print(f"[QUANT] Model load error: {e}")

        # 2. LSTM
        if settings.USE_LSTM and LSTM_AVAILABLE:
            self._load_lstm_models()

        # 3. Lag-Llama/Chronos
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
        Returns dict with score, direction, indicators, and ML probs.
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
        h4 = self._compute_trend(data_dict.get('H4'))
        
        # ML Predictions
        rf_prob, _ = self._get_rf_prediction(df)
        xgb_prob, _ = self._get_xgb_prediction(df)
        ml_prob = (rf_prob + xgb_prob) / 2 if self.xgb_model else rf_prob
        
        # AI Signal
        ai_signal = self._get_ai_signal(symbol, df)
        
        # Scoring
        buy_score, buy_details = self._calculate_confluence(symbol, df, "buy", h1, h4)
        sell_score, sell_details = self._calculate_confluence(symbol, df, "sell", h1, h4)
        
        best_score = max(buy_score, sell_score)
        direction = "BUY" if buy_score >= sell_score else "SELL"
        details = buy_details if direction == "BUY" else sell_details
        
        ensemble = self._ensemble_vote(ml_prob, ai_signal, best_score)
        
        return {
            'symbol': symbol,
            'direction': direction,
            'score': best_score,
            'details': details,
            'ml_prob': ml_prob,
            'ai_signal': ai_signal,
            'ensemble_score': ensemble,
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

    def _get_rf_prediction(self, df):
        if self.model is None: return 0.5, 0
        last = df.iloc[-1:]
        X = self._prepare_X(last)
        try:
            return self.model.predict_proba(X)[0][1], self.model.predict(X)[0]
        except: return 0.5, 0
        
    def _get_xgb_prediction(self, df):
        if self.xgb_model is None: return 0.5, 0
        last = df.iloc[-1:]
        X = self._prepare_X(last)
        try:
            return self.xgb_model.predict_proba(X)[0][1], self.xgb_model.predict(X)[0]
        except: return 0.5, 0

    def _prepare_X(self, row):
        if self.feature_cols:
            cols = [c for c in self.feature_cols if c in row.columns]
            if cols: return row[cols]
        exclude = ['time','open','high','low','close','tick_volume','spread','real_volume','target']
        return row.drop(columns=[c for c in exclude if c in row.columns], errors='ignore')

    def _get_ai_signal(self, symbol, df):
        # Implementation of AI combination (kept brief for artifact)
        # Assuming copied from strategy with minor adjustments
        signals = []
        # ... logic ...
        # Simplified for brevity in this write (I will ensure full logic in act)
        # Using self.hf_predictor and self.lstm_predictors
        
        # 1. Chronos/Lag-Llama
        if self.hf_predictor and 'close' in df.columns:
            try:
                recent = torch.tensor(df['close'].values[-60:])
                pred = self.hf_predictor.predict(recent.unsqueeze(0), prediction_length=12)
                curr = df['close'].iloc[-1]
                fut = pred[0, 5].item()
                if fut > curr * 1.0003: signals.append(1)
                elif fut < curr * 0.9997: signals.append(-1)
                else: signals.append(0)
            except: pass
            
        # 2. LSTM
        base = _strip_suffix(symbol)
        lstm = self.lstm_predictors.get(base) or self.lstm_predictors.get('default')
        if lstm:
            try:
                if lstm.predict(df) > df['close'].iloc[-1] * 1.0003: signals.append(1)
                elif lstm.predict(df) < df['close'].iloc[-1] * 0.9997: signals.append(-1)
                else: signals.append(0)
            except: pass
            
        if not signals: return 0
        avg = sum(signals)/len(signals)
        return 1 if avg > 0.3 else -1 if avg < -0.3 else 0

    def _ensemble_vote(self, rf, ai, conf):
        return round(0.3*rf + 0.25*((ai+1)/2) + 0.45*(conf/6.0), 3)

    def _calculate_confluence(self, symbol, df, direction, h1, h4):
        score = 0
        details = {}
        last = df.iloc[-1]
        
        # Trends
        if settings.H4_TREND_FILTER:
            if direction=="buy" and h4==-1: return 0, {'H4':'BLOCK'}
            if direction=="sell" and h4==1: return 0, {'H4':'BLOCK'}
            if (direction=="buy" and h4==1) or (direction=="sell" and h4==-1):
                score+=1; details['H4']='✓'
            else: details['H4']='-'
            
        if settings.H1_TREND_FILTER:
            if (direction=="buy" and h1>=1) or (direction=="sell" and h1<=-1):
                score+=1; details['H1']='✓'
            else: details['H1']='-'
            
        # ML & AI
        threshold = settings.RF_PROB_THRESHOLD
        prob, _ = self._get_rf_prediction(df) # Recalc or pass? 
        # Better to pass. But method signature... 
        # I'll re-call or optimize later. Re-calling is cheap (cached in memory if efficient, but here it's fast)
        
        if direction=="buy":
            if prob > 0.85: score+=2; details['ML']='✓✓'
            elif prob > threshold: score+=1; details['ML']='✓'
            else: details['ML']='✗'
        else:
            if prob < 0.15: score+=2; details['ML']='✓✓'
            elif prob < (1-threshold): score+=1; details['ML']='✓'
            else: details['ML']='✗'
            
        ai = self._get_ai_signal(symbol, df)
        if (direction=="buy" and ai==1) or (direction=="sell" and ai==-1):
            score+=1; details['AI']='✓'
        else: details['AI']='✗'
        
        # SMC Confluence
        smc_hit = False
        if direction == "buy":
            if last.get('near_ob_bullish', 0) == 1 or last.get('near_fvg_bullish', 0) == 1: smc_hit = True
            if last.get('liq_sweep_low', 0) == 1: smc_hit = True
        else:
            if last.get('near_ob_bearish', 0) == 1 or last.get('near_fvg_bearish', 0) == 1: smc_hit = True
            if last.get('liq_sweep_high', 0) == 1: smc_hit = True
            
        if smc_hit: score+=1; details['SMC']='✓'
        else: details['SMC']='✗'
        
        # ADX
        if last.get('adx',0) > 25: score+=1; details['ADX']='✓'
        else: details['ADX']='✗'
        
        return score, details
