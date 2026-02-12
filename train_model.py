import pandas as pd
import numpy as np
import joblib
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config import settings
from data import loader
from strategy import features
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix

def apply_triple_barrier(df, tp_pips, sl_pips, time_horizon, point=0.00001):
    """
    Labels data based on Triple Barrier Method.
    1 (Buy): Price hits TP before SL within time_horizon.
    0 (No Trade): Price hits SL or generic exit.
    """
    labels = []
    
    # Convert pips to price difference
    tp_dist = tp_pips * point
    sl_dist = sl_pips * point
    
    # Iterate through indices (slow but clear logic)
    # Vectorization is harder for path-dependent barriers
    for i in range(len(df)):
        if i + time_horizon >= len(df):
            labels.append(0)
            continue
            
        entry_price = df['close'].iloc[i]
        # Future window
        future_window = df.iloc[i+1 : i+1+time_horizon]
        
        # Check barriers
        # Highs for TP, Lows for SL (Buying)
        hit_tp = False
        hit_sl = False
        
        for j in range(len(future_window)):
            high = future_window['high'].iloc[j]
            low = future_window['low'].iloc[j]
            
            if high >= entry_price + tp_dist:
                hit_tp = True
                break # Hit TP
                
            if low <= entry_price - sl_dist:
                hit_sl = True
                break # Hit SL first
        
        if hit_tp and not hit_sl:
            labels.append(1)
        else:
            labels.append(0)
            
    return pd.Series(labels, index=df.index)

def train():
    # 1. Fetch Data
    print(f"Fetching {settings.HISTORY_BARS} bars for {settings.SYMBOL}...")
    if not loader.initial_connect():
        print("Failed to connect to MT5.")
        return

    df = loader.get_historical_data(settings.SYMBOL, settings.TIMEFRAME, settings.HISTORY_BARS)
    if df is None or df.empty:
        print("No data fetched.")
        return

    # 2. Prepare Data & Features
    print("Generating features...")
    df = features.add_technical_features(df)
    
    # 3. Create Target (Barrier Method)
    print("Labelling data (Triple Barrier)...")
    # Assuming standard forex point, rough adjust if JPY
    point = 0.00001 if "JPY" not in settings.SYMBOL else 0.01 
    
    # Target: Hit 10 pips profit before 5 pips loss within 20 bars
    df['target'] = apply_triple_barrier(df, tp_pips=10, sl_pips=5, time_horizon=20, point=point)
    
    # Drop last N rows where target couldn't be computed clearly
    df = df.iloc[:-21]
    
    # Balance Classes (Optional but good for imbalanced datasets)
    positives = df[df['target'] == 1]
    negatives = df[df['target'] == 0]
    print(f"Class Balance: 1: {len(positives)}, 0: {len(negatives)}")
    
    # 4. Filter Features
    drop_cols = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume', 'target']
    feature_cols = [c for c in df.columns if c not in drop_cols]
    
    X = df[feature_cols]
    y = df['target']
    
    # 5. Split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)
    
    # 6. Train
    print("Training Random Forest...")
    # Increased minimum samples to reduce noise fitting
    model = RandomForestClassifier(
        n_estimators=200, 
        max_depth=15, 
        min_samples_leaf=5,
        random_state=42, 
        class_weight='balanced'
    )
    model.fit(X_train, y_train)
    
    # 7. Evaluate
    preds = model.predict(X_test)
    probs = model.predict_proba(X_test)[:, 1]
    
    print("--- Evaluation ---")
    print(f"Accuracy: {accuracy_score(y_test, preds):.4f}")
    print(classification_report(y_test, preds))
    print("Confusion Matrix:")
    print(confusion_matrix(y_test, preds))
    
    # 8. Save
    os.makedirs(os.path.dirname(settings.MODEL_PATH), exist_ok=True)
    joblib.dump(model, settings.MODEL_PATH)
    joblib.dump(feature_cols, settings.MODEL_PATH.replace('.pkl', '_features.pkl'))
    print(f"Model saved to {settings.MODEL_PATH}")

if __name__ == "__main__":
    train()
