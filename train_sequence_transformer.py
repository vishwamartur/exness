"""
Train Sequence Transformer Architecture
=======================================

Trains a time-series Transformer with Attention mechanisms.
Uses sequence windows (e.g., 60 M15 bars) to predict the outcome of the immediate future,
incorporating the ATR-driven take-profit/stop-loss logic.
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
from strategy.sequence_transformer import SequenceTransformerPredictor
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, roc_auc_score
from execution.mt5_client import MT5Client

import warnings
warnings.filterwarnings('ignore')

def apply_atr_barrier(df, atr_tp_mult=3.0, atr_sl_mult=1.5, time_horizon=20):
    """
    Labels data using ATR-based barriers (matches live trading logic).
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

def create_sequences(X, y, seq_length=60):
    """
    Builds sliding windows.
    X: (samples, features)
    y: (samples)
    Returns: X_seq (samples-seq_len, seq_len, features), y_seq (samples-seq_len,)
    """
    xs, ys = [], []
    for i in range(len(X) - seq_length):
        x_window = X.iloc[i:i+seq_length].values
        # The target corresponds to the prediction for the bar immediately following the window!
        y_val = y.iloc[i+seq_length-1]
        xs.append(x_window)
        ys.append(y_val)
        
    return np.array(xs), np.array(ys)

def train():
    """Train Sequence Transformer Model on Key Symbols."""
    print("\n" + "="*70)
    print("  SEQUENCE TRANSFORMER TRAINING - Temporal Attention Model")
    print("="*70)
    
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"\n[INFO] Using device: {device}")
    
    client = MT5Client()
    if not client.connect() or not client.detect_available_symbols():
        print("Failed to initialize MT5 client or find symbols.")
        return

    all_X_seq = []
    all_y_seq = []
    
    # Prioritise key pairs for the base model, but can iterate all if memory allows
    train_symbols = ["EURUSD", "GBPUSD", "BTCUSD", "XAUUSD"]
    print(f"\n[STAGE 1] Collecting Sequences for Core Symbols: {train_symbols}")
    
    SEQ_LENGTH = 60 # 60 M15 bars = 15 hours of microstructure
    
    for i, symbol in enumerate(train_symbols, 1):
        print(f"  [{i}/{len(train_symbols)}] {symbol:12} ", end="", flush=True)
        df = loader.get_historical_data(symbol, "M15", settings.HISTORY_BARS)
        if df is None or len(df) < 500:
            print("❌ Not enough data")
            continue
            
        try:
            df = features.add_technical_features(df)
            df['target'] = apply_atr_barrier(df, settings.ATR_TP_MULTIPLIER, settings.ATR_SL_MULTIPLIER)
            df.dropna(inplace=True)
        except Exception as e:
            print(f"❌ Feature error: {e}")
            continue
            
        # Select Features
        exclude_cols = ['time', 'open', 'high', 'low', 'close', 'tick_volume', 'spread', 'real_volume', 'target', 'symbol']
        feature_cols = [c for c in df.columns if c not in exclude_cols and df[c].dtype in [np.float64, np.float32, np.int64, np.int32]]
        
        X = df[feature_cols].copy()
        X = X.replace([np.inf, -np.inf], np.nan).fillna(X.mean())
        # DOWNCAST to float32 immediately to halve RAM footprint before creating sequences
        X = X.astype(np.float32)
        y = df['target'].copy().astype(int)
        
        # Build Windows
        X_seq, y_seq = create_sequences(X, y, seq_length=SEQ_LENGTH)
        
        print(f"✓ {len(X_seq)} sequences | Win Rate: {y_seq.mean():.2%}")
        all_X_seq.append((X_seq, symbol))
        all_y_seq.append(y_seq)
        
    if not all_X_seq:
        print("\n❌ No sequences collected. Exiting.")
        return
        
    # We must horizontally stack the numpy arrays
    X_combined = np.concatenate([x for x, _ in all_X_seq], axis=0)
    y_combined = np.concatenate(all_y_seq, axis=0)
    
    print(f"\n[STAGE 2] Sequence Preparation")
    print(f"  Total Sequences:      {len(X_combined):,}")
    print(f"  Input Features:       {X_combined.shape[2]}")
    print(f"  Sequence Length:      {SEQ_LENGTH} steps")
    print(f"  Global Win Rate:      {y_combined.mean():.2%}")
    
    # Train/Val split
    X_train, X_val, y_train, y_val = train_test_split(
        X_combined, y_combined, test_size=0.2, random_state=42, stratify=y_combined
    )
    
    print(f"\n[STAGE 3] Train-Val Split")
    print(f"  Train: {len(X_train):,} sequences")
    print(f"  Val:   {len(X_val):,} sequences")
    
    # Initialize Model
    print(f"\n[STAGE 4] Model Architecture & Training")
    predictor = SequenceTransformerPredictor(
        input_features=X_combined.shape[2],
        seq_len=SEQ_LENGTH,
        embed_dim=64,
        num_layers=2, # 2 layers of multi-head attention
        num_heads=4,
        ffn_dim=128,
        dropout=0.20,
        device=device,
        lr=0.001
    )
    
    # Ensure predictor has reference to columns
    predictor.feature_cols = feature_cols
    
    print(f"  Training for up to 50 epochs (patience 10)...")
    predictor.fit(X_train, y_train, X_val, y_val, epochs=50, batch_size=32, verbose=True)
    
    # Evaluation (Batch the predictions to avoid memory spikes)
    print(f"\n[STAGE 5] Validation & Evaluation")
    
    val_probs = []
    batch_size = 128
    predictor.model.eval()
    
    for i in range(0, len(X_val), batch_size):
        X_batch = X_val[i:i+batch_size].reshape(-1, X_combined.shape[2])
        X_scaled_flat = predictor.scaler.transform(X_batch)
        X_scaled = X_scaled_flat.reshape(-1, SEQ_LENGTH, X_combined.shape[2])
        
        with torch.no_grad():
            t_batch = torch.tensor(X_scaled, dtype=torch.float32).to(device)
            logits = predictor.model(t_batch)
            probs = torch.softmax(logits, dim=1).cpu().numpy()
            val_probs.extend(probs)
            
    val_probs = np.array(val_probs)
    y_pred = np.argmax(val_probs, axis=1)
    
    acc = accuracy_score(y_val, y_pred)
    try: auc = roc_auc_score(y_val, val_probs[:, 1])
    except: auc = 0.5
    
    print(f"  Accuracy: {acc:.4f} | ROC-AUC: {auc:.4f}")
    
    cm = confusion_matrix(y_val, y_pred)
    print(f"\n  Confusion Matrix:")
    print(f"  [[{cm[0,0]:5d} {cm[0,1]:5d}]")
    print(f"   [{cm[1,0]:5d} {cm[1,1]:5d}]]")
    
    # Save Model
    print(f"\n[STAGE 6] Model Persistence")
    model_dir = Path(settings.MODEL_PATH).parent
    model_path = model_dir / 'seq_transformer_v1.pth'
    
    predictor.save(str(model_path))
    print(f"✅ Training and saving complete! Saved to {model_path}.")

if __name__ == "__main__":
    train()
