"""
Train RF Model - Updated for M15 institutional strategy.

Changes from original M1 version:
- Uses M15 timeframe data
- Includes all institutional features (SMC, ADX, VWAP, etc.)
- ATR-based barrier method (dynamic TP/SL based on volatility)
- 5-year history for robust training
- Gradient Boosting option for comparison
"""

import pandas as pd
import numpy as np
import joblib
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config import settings
from market_data import loader
from strategy import features
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix


def apply_atr_barrier(df, atr_tp_mult=3.0, atr_sl_mult=1.5, time_horizon=20):
    """
    Labels data using ATR-based barriers (matches live trading logic).
    Uses dynamic TP/SL based on ATR, not fixed pips.
    
    1 (Buy): Price hits ATR*TP_mult before ATR*SL_mult within time_horizon bars.
    0 (No Trade): Price hits SL or times out.
    """
    labels = []
    atr = df['atr'].values if 'atr' in df.columns else None
    
    if atr is None:
        print("Warning: ATR column not found, falling back to fixed barriers")
        return apply_fixed_barrier(df, time_horizon)
    
    for i in range(len(df)):
        if i + time_horizon >= len(df):
            labels.append(0)
            continue
        
        entry_price = df['close'].iloc[i]
        current_atr = atr[i]
        
        if current_atr <= 0:
            labels.append(0)
            continue
        
        tp_dist = current_atr * atr_tp_mult
        sl_dist = current_atr * atr_sl_mult
        
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
        
        if hit_tp and not hit_sl:
            labels.append(1)
        else:
            labels.append(0)
    
    return pd.Series(labels, index=df.index)


def apply_fixed_barrier(df, time_horizon=20):
    """Fallback: fixed pip barriers like original."""
    labels = []
    point = 0.00001 if "JPY" not in settings.SYMBOL else 0.01
    tp_dist = 15 * point  # 15 pips for M15
    sl_dist = 10 * point  # 10 pips
    
    for i in range(len(df)):
        if i + time_horizon >= len(df):
            labels.append(0)
            continue
        
        entry_price = df['close'].iloc[i]
        future = df.iloc[i+1 : i+1+time_horizon]
        
        hit_tp = any(future['high'] >= entry_price + tp_dist)
        hit_sl = any(future['low'] <= entry_price - sl_dist)
        
        labels.append(1 if hit_tp and not hit_sl else 0)
    
    return pd.Series(labels, index=df.index)


def train():
    # 1. Fetch Data - M15 with maximum history
    print(f"Fetching {settings.HISTORY_BARS} bars of M15 data for {settings.SYMBOL}...")
    if not loader.initial_connect():
        print("Failed to connect to MT5.")
        return

    df = loader.get_historical_data(settings.SYMBOL, "M15", settings.HISTORY_BARS)
    if df is None or df.empty:
        print("No data fetched.")
        return
    
    print(f"Fetched {len(df)} bars")

    # 2. Generate ALL institutional features
    print("Generating institutional features (SMC, ADX, VWAP, order blocks, FVGs)...")
    df = features.add_technical_features(df)
    print(f"Features: {len([c for c in df.columns if c not in ['time','open','high','low','close','tick_volume','spread','real_volume']])} indicators")
    
    # 3. Label with ATR-based barriers (matches live trading)
    print(f"Labelling data (ATR barriers: TP={settings.ATR_TP_MULTIPLIER}x, SL={settings.ATR_SL_MULTIPLIER}x)...")
    df['target'] = apply_atr_barrier(
        df, 
        atr_tp_mult=settings.ATR_TP_MULTIPLIER,
        atr_sl_mult=settings.ATR_SL_MULTIPLIER,
        time_horizon=20
    )
    
    # Drop last N rows where target couldn't be computed
    df = df.iloc[:-21].dropna()
    
    # Class balance
    positives = df[df['target'] == 1]
    negatives = df[df['target'] == 0]
    print(f"Class Balance: Wins: {len(positives)} ({len(positives)/len(df)*100:.1f}%), "
          f"Losses: {len(negatives)} ({len(negatives)/len(df)*100:.1f}%)")
    
    # 4. Filter Features
    drop_cols = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 
                 'spread', 'real_volume', 'target']
    feature_cols = [c for c in df.columns if c not in drop_cols]
    
    X = df[feature_cols]
    y = df['target']
    
    print(f"Training on {len(X)} samples with {len(feature_cols)} features")
    
    # 5. Time-based split (no shuffle - respects temporal order)
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, shuffle=False
    )
    
    # 6. Train Random Forest (optimized for M15)
    print("\nTraining Random Forest (M15 optimized)...")
    rf_model = RandomForestClassifier(
        n_estimators=300,      # More trees for M15 (more data per candle)
        max_depth=20,          # Deeper for complex patterns
        min_samples_leaf=10,   # Higher to avoid noise
        min_samples_split=20,
        max_features='sqrt',
        random_state=42,
        class_weight='balanced',
        n_jobs=-1              # Use all CPU cores
    )
    rf_model.fit(X_train, y_train)
    
    # 7. Evaluate
    preds = rf_model.predict(X_test)
    probs = rf_model.predict_proba(X_test)[:, 1]
    
    print("\n" + "="*50)
    print("  EVALUATION RESULTS")
    print("="*50)
    print(f"Accuracy: {accuracy_score(y_test, preds):.4f}")
    print(classification_report(y_test, preds))
    print("Confusion Matrix:")
    print(confusion_matrix(y_test, preds))
    
    # Cross-validation score
    print("\nCross-validation (5-fold)...")
    cv_scores = cross_val_score(rf_model, X, y, cv=5, scoring='accuracy', n_jobs=-1)
    print(f"CV Accuracy: {cv_scores.mean():.4f} ± {cv_scores.std():.4f}")
    
    # Feature importance (top 15)
    importances = pd.Series(rf_model.feature_importances_, index=feature_cols)
    top_features = importances.nlargest(15)
    print("\nTop 15 Most Important Features:")
    for feat, imp in top_features.items():
        print(f"  {feat:30s} {imp:.4f}")
    
    # 8. Save
    os.makedirs(os.path.dirname(settings.MODEL_PATH), exist_ok=True)
    joblib.dump(rf_model, settings.MODEL_PATH)
    joblib.dump(feature_cols, settings.MODEL_PATH.replace('.pkl', '_features.pkl'))
    print(f"\nModel saved to {settings.MODEL_PATH}")
    print(f"Features saved ({len(feature_cols)} features)")


if __name__ == "__main__":
    train()
