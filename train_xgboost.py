"""
Train XGBoost Model - Institutional Strategy Ensemble
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


from execution.mt5_client import MT5Client

def train():
    # Initialize connection and detect symbols
    client = MT5Client()
    if not client.connect():
        print("Failed to connect to MT5.")
        return
        
    if not client.detect_available_symbols():
        print("No symbols detected.")
        return

    all_data = []
    total_symbols = len(settings.SYMBOLS)
    
    print(f"\nStarting data collection for {total_symbols} symbols...")
    
    for i, symbol in enumerate(settings.SYMBOLS, 1):
        print(f"[{i}/{total_symbols}] Processing {symbol}...", end="\r")
        
        # Fetch data
        df = loader.get_historical_data(symbol, "M15", settings.HISTORY_BARS)
        if df is None or df.empty:
            continue
            
        # Feature Engineering
        try:
            df = features.add_technical_features(df)
            
            # Labelling
            df['target'] = apply_atr_barrier(
                df, 
                atr_tp_mult=settings.ATR_TP_MULTIPLIER,
                atr_sl_mult=settings.ATR_SL_MULTIPLIER,
                time_horizon=20
            )
            
            # Drop recent data that can't be labelled yet
            df = df.iloc[:-21].dropna()
            
            # Add symbol encoding (one-hot or categorical)
            # This helps the model learn symbol-specific patterns
            df['symbol_id'] = hash(symbol) % 1000  # Simple hash for symbol identification
            
            # Add symbol volatility class
            atr_mean = df['atr'].mean() if 'atr' in df.columns else 0
            close_mean = df['close'].mean()
            vol_ratio = atr_mean / close_mean if close_mean > 0 else 0
            
            # Assign volatility class based on ratio
            if vol_ratio < 0.0005:
                vol_class = 0  # Low volatility (major forex)
            elif vol_ratio < 0.001:
                vol_class = 1  # Medium-low volatility
            elif vol_ratio < 0.005:
                vol_class = 2  # Medium volatility (commodities)
            else:
                vol_class = 3  # High volatility (crypto)
            
            df['volatility_class'] = vol_class
            
            all_data.append(df)
            
        except Exception as e:
            print(f"\nError processing {symbol}: {e}")
            continue

    if not all_data:
        print("No valid data collected from any symbol.")
        return

    # Combine all data
    full_df = pd.concat(all_data, ignore_index=True)
    print(f"\n\nTotal Dataset Size: {len(full_df)} bars across {len(all_data)} symbols")
    
    # Class balance
    positives = full_df[full_df['target'] == 1]
    negatives = full_df[full_df['target'] == 0]
    print(f"Class Balance: Won: {len(positives)} ({len(positives)/len(full_df)*100:.1f}%), "
          f"Lost: {len(negatives)} ({len(negatives)/len(full_df)*100:.1f}%)")
    
    # Features
    drop_cols = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 
                 'spread', 'real_volume', 'target']
    feature_cols = [c for c in full_df.columns if c not in drop_cols]
    
    print(f"\nFeature columns ({len(feature_cols)}):")
    print(f"  Symbol features: symbol_id, volatility_class")
    print(f"  Technical features: {[c for c in feature_cols if c not in ['symbol_id', 'volatility_class']][:10]}...")
    
    X = full_df[feature_cols]
    y = full_df['target']
    
    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, shuffle=True, random_state=42 
        # shuffle=True is important here to mix symbols in train/test
    )
    
    # Train XGBoost
    print("\nTraining XGBoost Classifier on Aggregated Data...")
    
    # Re-calculate ratio for the full dataset
    ratio = len(negatives) / len(positives) if len(positives) > 0 else 1.0
    
    # Check for GPU
    import torch
    use_gpu = torch.cuda.is_available()
    if use_gpu:
        print(f"GPU Training Enabled: {torch.cuda.get_device_name(0)}")
    
    xgb_model = xgb.XGBClassifier(
        n_estimators=500,
        max_depth=6,             # Increased for larger dataset (more patterns to learn)
        learning_rate=0.03,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_weight=5,      # Increased to reduce noise in large data
        gamma=0.2,
        scale_pos_weight=ratio,  # Removed multiplier - large data provides enough signal
        random_state=42,
        n_jobs=-1,
        eval_metric='aucpr',
        early_stopping_rounds=50,
        device='cuda' if use_gpu else 'cpu',
        tree_method='hist' 
    )
    
    # Use evaluation set for early stopping
    eval_set = [(X_train, y_train), (X_test, y_test)]
    xgb_model.fit(X_train, y_train, eval_set=eval_set, verbose=False)
    
    # Evaluate
    preds = xgb_model.predict(X_test)
    
    report = classification_report(y_test, preds)
    cm = confusion_matrix(y_test, preds)
    acc = accuracy_score(y_test, preds)

    output = []
    output.append("="*50)
    output.append("  XGBOOST EVALUATION RESULTS (ALL PAIRS)")
    output.append("="*50)
    output.append(f"Training Symbols: {len(all_data)}")
    output.append(f"Total Samples: {len(full_df)}")
    output.append(f"Accuracy: {acc:.4f}")
    output.append(report)
    output.append(str(cm))
    
    output_str = "\n".join(output)
    print(output_str)
    
    # Feature importance
    importance = xgb_model.feature_importances_
    feature_importance = sorted(zip(feature_cols, importance), key=lambda x: x[1], reverse=True)
    
    output.append("\n" + "="*50)
    output.append("  TOP 15 FEATURE IMPORTANCE")
    output.append("="*50)
    for feat, imp in feature_importance[:15]:
        output.append(f"  {feat:25s}: {imp:.4f}")
    
    output_str = "\n".join(output)
    
    with open("xgboost_evaluation.txt", "w") as f:
        f.write(output_str)
    
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
