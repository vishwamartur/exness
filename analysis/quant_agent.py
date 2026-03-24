
import os
import joblib
import pandas as pd
import numpy as np
import torch
import xgboost as xgb
from config import settings
from strategy import features


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
        self.feature_cols = None
        
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
        except Exception as e:
            print(f"[QUANT DEBUG] features.add_technical_features failed for {symbol}: {e}")
            import traceback; traceback.print_exc()
            return None
        
        if len(df) < 50:
            print(f"[QUANT DEBUG] {symbol} len(df) < 50")
            return None
        
        # Trends
        h1 = self._compute_trend(data_dict.get('H1'))
        m5 = self._compute_trend(data_dict.get('M5'))
        h4 = self._compute_trend(data_dict.get('H4'))
        
        rf_prob, _ = self._get_rf_prediction(df, symbol)
        xgb_prob, _ = self._get_xgb_prediction(df, symbol)
        
        # Ensemble ML probability (average of available models)
        models_available = sum([self.model is not None, self.xgb_model is not None])
        if models_available > 0:
            ml_prob = (rf_prob * (self.model is not None) + 
                       xgb_prob * (self.xgb_model is not None)) / models_available
        else:
            ml_prob = 0.5
        
        # AI Signal (Deprecated)
        ai_signal = 0
        
        # Scoring
        buy_score, buy_details = self._calculate_confluence(symbol, df, "buy", h1, h4, m5)
        sell_score, sell_details = self._calculate_confluence(symbol, df, "sell", h1, h4, m5)
        
        best_score = max(buy_score, sell_score)
        direction = "BUY" if buy_score >= sell_score else "SELL"
        details = buy_details if direction == "BUY" else sell_details
        
        # Basic Ensemble Voting
        ensemble_score, agreement_count, model_votes = self._ensemble_vote(
            ml_prob, ai_signal, best_score
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

    def _get_ai_signal(self, symbol, df):
        return 0

    def _ensemble_vote(self, rf_prob, ai_signal, confluence_score):
        """
        Basic Ensemble Voting System.
        """
        votes = {
            'rf': {'direction': 'NEUTRAL', 'weight': 0.80, 'confidence': 0},
            'confluence': {'direction': 'NEUTRAL', 'weight': 0.20, 'confidence': 0}
        }
        
        # 1. Random Forest / XGBoost Vote
        if rf_prob >= 0.75:
            votes['rf'] = {'direction': 'BUY', 'weight': 0.80, 'confidence': rf_prob}
        elif rf_prob <= 0.25:
            votes['rf'] = {'direction': 'SELL', 'weight': 0.80, 'confidence': 1 - rf_prob}
        else:
            votes['rf'] = {'direction': 'NEUTRAL', 'weight': 0.80, 'confidence': 0.5}
            
        # 2. Confluence Score Vote
        if confluence_score >= 5:
            votes['confluence'] = {'direction': 'BUY', 'weight': 0.20, 'confidence': confluence_score / 6.0}
        elif confluence_score >= 4:
            votes['confluence'] = {'direction': 'BUY', 'weight': 0.20, 'confidence': confluence_score / 6.0}
        else:
            votes['confluence'] = {'direction': 'NEUTRAL', 'weight': 0.20, 'confidence': confluence_score / 6.0}
        
        # Count agreements for BUY and SELL
        buy_votes = sum(1 for v in votes.values() if v['direction'] == 'BUY')
        sell_votes = sum(1 for v in votes.values() if v['direction'] == 'SELL')
        
        # Calculate weighted ensemble score (0-1 scale)
        buy_score = sum(v['weight'] * v['confidence'] for v in votes.values() if v['direction'] == 'BUY')
        sell_score = sum(v['weight'] * v['confidence'] for v in votes.values() if v['direction'] == 'SELL')
        
        ensemble_score = max(buy_score, sell_score)
        max_agreement = max(buy_votes, sell_votes)
        
        return round(ensemble_score, 3), max_agreement, votes

    def _calculate_confluence(self, symbol, df, direction, h1, h4, m5=0):
        score = 0
        details = {}
        last = df.iloc[-1]
        
        # Trends
        # Check M5
        if (direction=="buy" and m5==1) or (direction=="sell" and m5==-1):
            score+=1; details['M5']='OK'
        else: 
            details['M5']='-'
            if getattr(settings, 'M5_TREND_FILTER', False):
                if (direction=="buy" and m5==-1) or (direction=="sell" and m5==1): 
                    return 0, {'M5':'BLOCK'}

        # Check H4
        if (direction=="buy" and h4==1) or (direction=="sell" and h4==-1):
            score+=1; details['H4']='OK'
        else: 
            details['H4']='-'
            if getattr(settings, 'H4_TREND_FILTER', False):
                if (direction=="buy" and h4==-1) or (direction=="sell" and h4==1): 
                    return 0, {'H4':'BLOCK'}
            
        # Check H1
        if (direction=="buy" and h1>=1) or (direction=="sell" and h1<=-1):
            score+=1; details['H1']='OK'
        else: 
            details['H1']='-'
            if getattr(settings, 'H1_TREND_FILTER', False):
                if (direction=="buy" and h1<=-1) or (direction=="sell" and h1>=1): 
                    return 0, {'H1':'BLOCK'}
            
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
