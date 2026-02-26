"""
Train TabTransformer Model - Next Generation Ensemble
====================================================

Trains a TabTransformer model to work alongside XGBoost/RF.
Better at capturing feature interactions through attention mechanisms.

- Uses same M15 institutional features
- Uses ATR-based labelling (dynamic TP/SL) 
- Saves to models/tabtransformer_v1.pt
- GPU-accelerated training when available
"""

import pandas as pd
import numpy as np
import os
import sys
import torch
import joblib
from pathlib import Path

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config import settings
from market_data import loader
from strategy import features
from strategy.tabtransformer_predictor import TabTransformerPredictor
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, roc_auc_score
from execution.mt5_client import MT5Client

import warnings
warnings.filterwarnings('ignore')


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
    """Train TabTransformer model on all available symbols."""
    
    print("\n" + "="*70)
    print("  TABTRANSFORMER TRAINING - Multi-Symbol Ensemble Model")
    print("="*70)
    
    # GPU info
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"\n[INFO] Using device: {device}")
    if device == 'cuda':
        print(f"[INFO] GPU: {torch.cuda.get_device_name(0)}")
    
    # Initialize connection and detect symbols
    client = MT5Client()
    if not client.connect():
        print("Failed to connect to MT5.")
        return
        
    if not client.detect_available_symbols():
        print("No symbols detected.")
        return

    # Data collection
    all_data = []
    total_symbols = len(settings.SYMBOLS)
    
    print(f"\n[STAGE 1] Collecting data for {total_symbols} symbols...")
    
    for i, symbol in enumerate(settings.SYMBOLS, 1):
        print(f"  [{i}/{total_symbols}] {symbol:12} ", end="", flush=True)
        
        # Fetch data
        df = loader.get_historical_data(symbol, "M15", settings.HISTORY_BARS)
        if df is None or df.empty:
            print("❌ No data")
            continue
            
        # Feature Engineering
        try:
            df = features.add_technical_features(df)
            if df.empty or len(df) < 100:
                print(f"❌ Too few samples ({len(df)})")
                continue
        except Exception as e:
            print(f"❌ Feature error: {e}")
            continue
        
        # Labelling
        df['target'] = apply_atr_barrier(df, settings.ATR_TP_MULTIPLIER, settings.ATR_SL_MULTIPLIER)
        
        # Balance check
        label_dist = df['target'].value_counts()
        if 1 not in label_dist:
            print(f"❌ No positive labels")
            continue
        
        print(f"✓ {len(df)} bars | {label_dist.get(1, 0)} wins")
        all_data.append(df)
    
    if not all_data:
        print("\n❌ No data collected. Exiting.")
        return
    
    # Combine all data
    combined_df = pd.concat(all_data, ignore_index=True)
    combined_df.dropna(inplace=True)
    
    print(f"\n[STAGE 2] Data Preparation")
    print(f"  Total bars: {len(combined_df):,}")
    print(f"  Win rate: {combined_df['target'].mean():.2%}")
    
    # Feature selection (exclude non-numeric and target)
    exclude_cols = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 
                    'spread', 'real_volume', 'target', 'symbol']
    feature_cols = [c for c in combined_df.columns 
                    if c not in exclude_cols and combined_df[c].dtype in [np.float64, np.float32, np.int64, np.int32]]
    
    print(f"  Features: {len(feature_cols)}")
    
    X = combined_df[feature_cols].copy()
    y = combined_df['target'].copy()
    
    # Handle NaN/Inf
    X = X.replace([np.inf, -np.inf], np.nan)
    X = X.fillna(X.mean())
    y = y.astype(int)
    
    # Train/Val split (80/20)
    X_train, X_val, y_train, y_val = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    
    print(f"\n[STAGE 3] Train-Val Split")
    print(f"  Train: {len(X_train):,} samples ({y_train.mean():.2%} win rate)")
    print(f"  Val:   {len(X_val):,} samples ({y_val.mean():.2%} win rate)")
    
    # Initialize TabTransformer
    print(f"\n[STAGE 4] Model Training")
    print(f"  Architecture: {len(feature_cols)} features → 32-dim embeddings")
    print(f"                → 3 transformer blocks (4 heads)")
    print(f"                → Classification head")
    
    predictor = TabTransformerPredictor(
        num_numerical_features=len(feature_cols),
        embedding_dim=32,
        num_transformer_blocks=3,
        num_heads=4,
        ffn_dim=128,
        dropout=0.15,
        device=device,
        learning_rate=0.001
    )
    
    # Train
    print(f"  Training for up to 100 epochs (early stopping at patience 10)...\n")
    predictor.fit(
        X_train, y_train,
        X_val, y_val,
        epochs=100,
        batch_size=64,
        verbose=True
    )
    
    # Evaluation
    print(f"\n[STAGE 5] Validation & Evaluation")
    y_pred_proba = predictor.predict_proba(X_val)
    y_pred = np.argmax(y_pred_proba, axis=1)
    
    acc = accuracy_score(y_val, y_pred)
    try:
        auc = roc_auc_score(y_val, y_pred_proba[:, 1])
    except:
        auc = 0.5
    
    print(f"  Accuracy: {acc:.4f}")
    print(f"  ROC-AUC:  {auc:.4f}")
    print(f"\n  Classification Report:")
    print(classification_report(y_val, y_pred, target_names=['LOSS', 'WIN']))
    print(f"\n  Confusion Matrix:")
    cm = confusion_matrix(y_val, y_pred)
    print(f"  [[{cm[0,0]:6d} {cm[0,1]:6d}]")
    print(f"   [{cm[1,0]:6d} {cm[1,1]:6d}]]")
    
    # Save
    print(f"\n[STAGE 6] Model Persistence")
    model_dir = Path(settings.MODEL_PATH).parent
    model_path = model_dir / 'tabtransformer_v1.pt'
    
    try:
        predictor.save(str(model_path))
        
        # Save feature columns for later reference
        joblib.dump(feature_cols, str(model_dir / 'tabtransformer_features.pkl'))
        print(f"  Features saved: {len(feature_cols)} columns")
        
        print(f"\n✅ Training complete!")
        print(f"   Model: {model_path}")
    except Exception as e:
        print(f"❌ Save error: {e}")
        return
    
    # Summary
    print(f"\n" + "="*70)
    print(f"  Training Summary")
    print(f"  Accuracy: {acc:.2%} | ROC-AUC: {auc:.4f}")
    print(f"  TP: {cm[1,1]} | TN: {cm[0,0]} | FP: {cm[0,1]} | FN: {cm[1,0]}")
    print(f"  Ready for ensemble with XGBoost & Random Forest")
    print("="*70)


if __name__ == "__main__":
    train()
