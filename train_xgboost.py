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


from utils.triple_barrier import apply_triple_barrier as apply_atr_barrier


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
        
        # Fetch fully processed and labeled data from cache (or compute and cache if not present)
        df = loader.get_processed_training_data(symbol, "M15", settings.HISTORY_BARS)
        
        if df is not None and not df.empty:
            all_data.append(df)
        else:
            print(f"❌ Failed to process data for {symbol}")

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
    
    # ---------------------------------------------------------
    # PROBABILITY CALIBRATION (Platt Scaling via Sigmoid)
    # Converts arbitrary ML float outputs to True Probabilities
    # ---------------------------------------------------------
    from sklearn.calibration import CalibratedClassifierCV
    print("\nCalibrating Probabilities (Sigmoid Platt Scaling)...")
    
    # Wrap the fitted model to calibrate its predict_proba outputs
    calibrated_model = CalibratedClassifierCV(xgb_model, method='sigmoid', cv="prefit")
    calibrated_model.fit(X_test, y_test) # Calibrate on the holdout test set to prevent over-optimization
    
    # Evaluate using the calibrated model
    preds = calibrated_model.predict(X_test)
    probs = calibrated_model.predict_proba(X_test)[:, 1]
    
    report = classification_report(y_test, preds)
    cm = confusion_matrix(y_test, preds)
    acc = accuracy_score(y_test, preds)

    output = []
    output.append("="*50)
    output.append("  XGBOOST EVALUATION RESULTS (CALIBRATED)")
    output.append("="*50)
    output.append(f"Training Symbols: {len(all_data)}")
    output.append(f"Total Samples: {len(full_df)}")
    output.append(f"Accuracy: {acc:.4f}")
    output.append(f"Calibrated Prob Mean: {probs.mean():.4f}")
    output.append(report)
    output.append(str(cm))
    
    output_str = "\n".join(output)
    print(output_str)
    
    # Feature importance (Must be extracted from the base estimator)
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
    
    # Save the CALIBRATED model to act as the primary inference engine
    model_path = os.path.join(os.path.dirname(settings.MODEL_PATH), "xgboost_v1.pkl")
    feat_path = model_path.replace('.pkl', '_features.pkl')
    
    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    joblib.dump(calibrated_model, model_path)
    joblib.dump(feature_cols, feat_path)
    
    print(f"\nCalibrated XGBoost model saved to {model_path}")
    print(f"Features saved to {feat_path}")


if __name__ == "__main__":
    train()
