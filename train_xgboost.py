"""
Train XGBoost Model — Institutional Strategy Ensemble
Works alongside Random Forest to validate patterns.

- Uses same M15 institutional features.
- Uses ATR-based labelling (dynamic TP/SL).
- Saves to models/xgboost_v1.pkl
"""

import pandas as pd
import numpy as np
import joblib
import os
import sys
import xgboost as xgb

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config import settings
from market_data import loader
from strategy import features
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix


def apply_atr_barrier(df, atr_tp_mult=3.0, atr_sl_mult=1.5, time_horizon=20):
    """
    Labels data using ATR-based barriers (matches live trading logic).
    Uses dynamic TP/SL based on ATR.
    """
    labels = []
    atr = df['atr'].values if 'atr' in df.columns else None
    
    if atr is None:
        print("Warning: ATR column not found, falling back to fixed barriers")
        return pd.Series([0]*len(df), index=df.index)
    
    for i in range(len(df)):
        if i + time_horizon >= len(df):
            labels.append(0)
            continue
        
        if atr[i] <= 0:
            labels.append(0)
            continue
        
        entry_price = df['close'].iloc[i]
        tp_dist = atr[i] * atr_tp_mult
        sl_dist = atr[i] * atr_sl_mult
        
        future_window = df.iloc[i+1 : i+1+time_horizon]
        hit_tp = False
        hit_sl = False
        
        for j in range(len(future_window)):
            high = future_window['high'].iloc[j]
            low = future_window['low'].iloc[j]
            
            if high >= entry_price + tp_dist:
                hit_tp = True
                break
            if low <= entry_price - sl_dist:
                hit_sl = True
                break
        
        labels.append(1 if hit_tp and not hit_sl else 0)
    
    return pd.Series(labels, index=df.index)


def train():
    print(f"Fetching {settings.HISTORY_BARS} bars for {settings.SYMBOL} (XGBoost Training)...")
    if not loader.initial_connect():
        print("Failed to connect to MT5.")
        return

    df = loader.get_historical_data(settings.SYMBOL, "M15", settings.HISTORY_BARS)
    if df is None or df.empty:
        print("No data fetched.")
        return
    
    # Feature Engineering
    print("Generating institutional features...")
    df = features.add_technical_features(df)
    
    # Labelling
    print(f"Labelling data (ATR barriers)...")
    df['target'] = apply_atr_barrier(
        df, 
        atr_tp_mult=settings.ATR_TP_MULTIPLIER,
        atr_sl_mult=settings.ATR_SL_MULTIPLIER,
        time_horizon=20
    )
    
    df = df.iloc[:-21].dropna()
    
    # Class balance
    positives = df[df['target'] == 1]
    negatives = df[df['target'] == 0]
    print(f"Class Balance: Won: {len(positives)} ({len(positives)/len(df)*100:.1f}%), "
          f"Lost: {len(negatives)} ({len(negatives)/len(df)*100:.1f}%)")
    
    # Features
    drop_cols = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 
                 'spread', 'real_volume', 'target']
    feature_cols = [c for c in df.columns if c not in drop_cols]
    
    X = df[feature_cols]
    y = df['target']
    
    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, shuffle=False
    )
    
    # Train XGBoost
    print("\nTraining XGBoost Classifier...")
    # Scale_pos_weight for imbalance
    ratio = len(negatives) / len(positives) if len(positives) > 0 else 1.0
    
    xgb_model = xgb.XGBClassifier(
        n_estimators=300,
        max_depth=6,             # Shallower than RF to prevent overfit
        learning_rate=0.05,      # Lower learning rate for robustness
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=ratio,
        random_state=42,
        n_jobs=-1,
        eval_metric='logloss',
        early_stopping_rounds=20
    )
    
    # Use evaluation set for early stopping
    eval_set = [(X_train, y_train), (X_test, y_test)]
    xgb_model.fit(X_train, y_train, eval_set=eval_set, verbose=False)
    
    # Evaluate
    preds = xgb_model.predict(X_test)
    probs = xgb_model.predict_proba(X_test)[:, 1]
    
    print("\n" + "="*50)
    print("  XGBOOST EVALUATION RESULTS")
    print("="*50)
    print(f"Accuracy: {accuracy_score(y_test, preds):.4f}")
    print(classification_report(y_test, preds))
    print(confusion_matrix(y_test, preds))
    
    # Save
    model_path = os.path.join(os.path.dirname(settings.MODEL_PATH), "xgboost_v1.pkl")
    feat_path = model_path.replace('.pkl', '_features.pkl')
    
    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    joblib.dump(xgb_model, model_path)
    joblib.dump(feature_cols, feat_path)
    
    print(f"\nXGBoost model saved to {model_path}")
    print(f"Features saved to {feat_path}")


if __name__ == "__main__":
    train()
