"""
Train LSTM - Unified Global Model (Memory-Efficient).

Pools data from ALL active symbols into one dataset and trains
a single global BiLSTM-Attention model. Memory-efficient design:
  1. Limits bars per symbol to keep total RAM under 8GB
  2. Creates sequences per-symbol to avoid cross-symbol contamination
  3. Uses PyTorch DataLoader with pin_memory for GPU streaming
  4. Zero-copy stride tricks for sequence generation

Run: python train_lstm.py           (trains global model on all symbols)
Run: python train_lstm.py --epochs 30
"""

import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import joblib
import os
import sys
import time
import shutil
import argparse
from sklearn.preprocessing import MinMaxScaler
from torch.utils.data import DataLoader, TensorDataset
from datetime import datetime
from tqdm import tqdm
from torch.utils.tensorboard import SummaryWriter

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from strategy import features
from strategy.lstm_model import BiLSTMWithAttention
from market_data import loader
from config import settings

# Max bars per symbol to keep memory reasonable
# 20K bars × 17 symbols × 60 seq_len × 107 features × 4 bytes ≈ 8.7 GB
MAX_BARS_PER_SYMBOL = 20000


def create_sequences_fast(data, target, seq_length):
    """Zero-copy sequence generation using numpy stride tricks."""
    n = len(data) - seq_length
    if n <= 0:
        return np.empty((0, seq_length, data.shape[1]), dtype=np.float32), np.empty((0, 1), dtype=np.float32)
    
    from numpy.lib.stride_tricks import sliding_window_view
    X_seq = sliding_window_view(data, window_shape=(seq_length, data.shape[1]))
    X_seq = np.ascontiguousarray(X_seq[:n].reshape(n, seq_length, data.shape[1]))
    y_seq = target[seq_length:seq_length + n]
    
    return X_seq, y_seq


def train_global_lstm(epochs=50, batch_size=256, lr=0.001, timeframe="M15"):
    """Trains ONE global LSTM model on data pooled from all active symbols."""
    
    print("\n" + "=" * 60)
    print("  GLOBAL LSTM TRAINING - Unified Multi-Symbol Model")
    print("=" * 60)
    
    # ── Device Setup ──────────────────────────────────────────────────────
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    if device.type == 'cuda':
        torch.backends.cudnn.benchmark = True
        print(f"[INFO] GPU: {torch.cuda.get_device_name(0)} (cuDNN Benchmark Enabled)")
    else:
        print(f"[INFO] Device: {device}")
    
    # ── Stage 1: Collect & Process Data Symbol-by-Symbol ──────────────────
    symbols = settings.ALL_BASE_SYMBOLS
    print(f"\n[STAGE 1] Collecting data from {len(symbols)} symbols "
          f"(max {MAX_BARS_PER_SYMBOL:,} bars each)...")
    
    feature_cols = None
    feature_scaler = MinMaxScaler()
    target_scaler = MinMaxScaler()
    
    # First pass: collect raw 2D data for scaler fitting
    all_raw_X = []
    all_raw_y = []
    valid_symbols_data = []  # (symbol, X_2d, y_2d) for second pass
    
    for i, symbol in enumerate(symbols):
        tag = f"[{i+1}/{len(symbols)}] {symbol:<12}"
        try:
            result = loader.get_historical_data(symbol, timeframe, settings.HISTORY_BARS)
            if isinstance(result, tuple):
                df, _ = result
            else:
                df = result
            if df is None or len(df) < 200:
                print(f"  {tag} ✗ Insufficient data, skipping")
                continue
            
            df = features.add_technical_features(df)
            df = df.dropna()
            
            if len(df) < 200:
                print(f"  {tag} ✗ Too few rows after features, skipping")
                continue
            
            # Keep only the most recent MAX_BARS_PER_SYMBOL bars
            if len(df) > MAX_BARS_PER_SYMBOL:
                df = df.tail(MAX_BARS_PER_SYMBOL).reset_index(drop=True)
            
            # Determine feature columns on first symbol
            drop_cols = ['time', 'open', 'high', 'low', 'close', 'tick_volume',
                         'spread', 'real_volume', 'target']
            if feature_cols is None:
                feature_cols = [c for c in df.columns if c not in drop_cols]
            
            X = df[feature_cols].values.astype(np.float32)
            y = df['close'].values.reshape(-1, 1).astype(np.float32)
            
            all_raw_X.append(X)
            all_raw_y.append(y)
            valid_symbols_data.append((symbol, X, y))
            print(f"  {tag} ✓ {len(df):,} bars")
            
        except Exception as e:
            print(f"  {tag} ✗ Error: {e}")
            continue
    
    if not all_raw_X:
        print("[ERROR] No data loaded. Exiting.")
        return False
    
    print(f"\n  Symbols loaded: {len(valid_symbols_data)}/{len(symbols)}")
    
    # ── Stage 2: Fit Global Scalers on 2D data ────────────────────────────
    print(f"\n[STAGE 2] Global Scaling & Sequence Creation")
    t0 = time.time()
    
    # Stack 2D data for scaler fitting (this is manageable: ~340K × 107 × 4 = ~145 MB)
    X_2d_all = np.vstack(all_raw_X)
    y_2d_all = np.vstack(all_raw_y)
    
    feature_scaler.fit(X_2d_all)
    target_scaler.fit(y_2d_all)
    
    total_2d_rows = len(X_2d_all)
    print(f"  Global scaler fit on {total_2d_rows:,} rows × {X_2d_all.shape[1]} features")
    
    # Free the 2D stacked arrays immediately
    del X_2d_all, y_2d_all, all_raw_X, all_raw_y
    
    # ── Stage 3: Create Sequences per-symbol (memory-efficient) ───────────
    seq_length = settings.LSTM_SEQ_LENGTH
    all_X_seq = []
    all_y_seq = []
    total_sequences = 0
    
    for symbol, X_raw, y_raw in valid_symbols_data:
        # Scale this symbol's data
        X_scaled = feature_scaler.transform(X_raw)
        y_scaled = target_scaler.transform(y_raw)
        
        # Create sequences
        X_seq, y_seq = create_sequences_fast(X_scaled, y_scaled, seq_length)
        
        if len(X_seq) > 0:
            all_X_seq.append(X_seq)
            all_y_seq.append(y_seq)
            total_sequences += len(X_seq)
            print(f"  [{symbol}] {len(X_seq):,} sequences")
        
        # Free intermediate scaled arrays
        del X_scaled, y_scaled
    
    # Free raw data references
    del valid_symbols_data
    
    # ── Stack all sequences ───────────────────────────────────────────────
    print(f"\n  Stacking {total_sequences:,} sequences...")
    X_combined = np.vstack(all_X_seq)
    y_combined = np.vstack(all_y_seq)
    
    # Free the per-symbol lists
    del all_X_seq, all_y_seq
    
    elapsed = time.time() - t0
    print(f"  Total Sequences:  {total_sequences:,}")
    print(f"  Features:         {X_combined.shape[2]}")
    print(f"  Sequence Length:   {seq_length} steps")
    mem_gb = X_combined.nbytes / (1024**3)
    print(f"  Memory Usage:     {mem_gb:.1f} GB")
    print(f"  Prep Time:        {elapsed:.1f}s")
    
    # ── Stage 4: Train/Val Split ──────────────────────────────────────────
    print(f"\n[STAGE 3] Train-Val Split")
    
    indices = np.random.permutation(len(X_combined))
    X_combined = X_combined[indices]
    y_combined = y_combined[indices]
    del indices
    
    train_size = int(len(X_combined) * 0.8)
    X_train, X_val = X_combined[:train_size], X_combined[train_size:]
    y_train, y_val = y_combined[:train_size], y_combined[train_size:]
    del X_combined, y_combined
    
    print(f"  Train: {len(X_train):,} sequences")
    print(f"  Val:   {len(X_val):,} sequences")
    
    # Convert to tensors (use as_tensor to avoid copy when possible)
    train_data = TensorDataset(
        torch.as_tensor(X_train, dtype=torch.float32),
        torch.as_tensor(y_train, dtype=torch.float32)
    )
    val_data = TensorDataset(
        torch.as_tensor(X_val, dtype=torch.float32),
        torch.as_tensor(y_val, dtype=torch.float32)
    )
    
    train_loader = DataLoader(train_data, shuffle=True, batch_size=batch_size, 
                              num_workers=0, pin_memory=(device.type == 'cuda'))
    val_loader = DataLoader(val_data, batch_size=batch_size,
                            num_workers=0, pin_memory=(device.type == 'cuda'))
    
    # ── Stage 5: Model Architecture & Training ───────────────────────────
    n_features = X_train.shape[2]
    del X_train, X_val, y_train, y_val  # Free numpy, tensors are in DataLoader
    
    print(f"\n[STAGE 4] Model Architecture & Training")
    
    model = BiLSTMWithAttention(
        input_size=n_features,
        hidden_size=64,
        num_layers=2,
        device=device
    ).to(device)
    
    total_params = sum(p.numel() for p in model.parameters())
    print(f"  Model Parameters: {total_params:,}")
    print(f"  Batch Size:       {batch_size}")
    print(f"  Training for up to {epochs} epochs (patience 10)...")
    
    criterion = nn.MSELoss()
    optimizer = optim.Adam(model.parameters(), lr=lr)
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=5, factor=0.5)
    
    best_loss = float('inf')
    patience = 10
    patience_counter = 0
    models_dir = os.path.join(os.path.dirname(__file__), "models")
    os.makedirs(models_dir, exist_ok=True)
    
    # TensorBoard setup
    run_name = datetime.now().strftime("%Y%m%d-%H%M%S")
    tb_dir = os.path.join(os.path.dirname(__file__), "tensorboard_logs", "lstm_global", run_name)
    writer = SummaryWriter(log_dir=tb_dir)
    print(f"  TensorBoard Logs: {tb_dir}")
    print()
    
    epoch_pbar = tqdm(range(epochs), desc="Global Training Progress", unit="epoch")
    for epoch in epoch_pbar:
        # ── Training ──
        model.train()
        train_loss = 0
        train_batches = tqdm(train_loader, desc=f"  Epoch {epoch+1} (Train)", leave=False, unit="batch")
        for X_batch, y_batch in train_batches:
            X_batch, y_batch = X_batch.to(device), y_batch.to(device)
            
            optimizer.zero_grad()
            output = model(X_batch)
            loss = criterion(output, y_batch)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            
            train_loss += loss.item()
            train_batches.set_postfix({'loss': f"{loss.item():.6f}"})
        
        # ── Validation ──
        model.eval()
        val_loss = 0
        val_batches = tqdm(val_loader, desc=f"  Epoch {epoch+1} (Val)", leave=False, unit="batch")
        with torch.no_grad():
            for X_batch, y_batch in val_batches:
                X_batch, y_batch = X_batch.to(device), y_batch.to(device)
                output = model(X_batch)
                loss = criterion(output, y_batch)
                val_loss += loss.item()
                val_batches.set_postfix({'loss': f"{loss.item():.6f}"})
        
        train_loss /= len(train_loader)
        val_loss /= len(val_loader)
        
        scheduler.step(val_loss)
        
        # Progress logging
        lr_current = optimizer.param_groups[0]['lr']
        is_best = val_loss < best_loss
        best_marker = "★ Best" if is_best else ""
        
        tqdm.write(f"  Epoch {epoch+1:>3}/{epochs} | "
                   f"Train: {train_loss:.6f} | Val: {val_loss:.6f} | "
                   f"LR: {lr_current:.1e} | "
                   f"{best_marker}")
        epoch_pbar.set_postfix({'Train': f"{train_loss:.6f}", 'Val': f"{val_loss:.6f}", 'LR': f"{lr_current:.1e}", 'Best': is_best})
        
        # Tensorboard Logging
        writer.add_scalar('Loss/Train', train_loss, epoch)
        writer.add_scalar('Loss/Validation', val_loss, epoch)
        writer.add_scalar('Learning_Rate', lr_current, epoch)
        
        if val_loss < best_loss:
            best_loss = val_loss
            patience_counter = 0
            
            # Save as Global model
            torch.save(model.state_dict(), os.path.join(models_dir, "lstm_global.pth"))
            joblib.dump(feature_scaler, os.path.join(models_dir, "lstm_global_scaler.pkl"))
            joblib.dump(target_scaler, os.path.join(models_dir, "lstm_global_target_scaler.pkl"))
            joblib.dump(feature_cols, os.path.join(models_dir, "lstm_global_cols.pkl"))
        else:
            patience_counter += 1
            if patience_counter >= patience:
                tqdm.write(f"\n  Early stopping at epoch {epoch+1}")
                break
    
    writer.close()
    
    # ── Stage 6: Save Per-Symbol Copies (Backward Compatible) ────────────
    print(f"\n[STAGE 5] Saving per-symbol model copies for backward compatibility...")
    
    global_model_path = os.path.join(models_dir, "lstm_global.pth")
    global_scaler_path = os.path.join(models_dir, "lstm_global_scaler.pkl")
    global_target_scaler_path = os.path.join(models_dir, "lstm_global_target_scaler.pkl")
    global_cols_path = os.path.join(models_dir, "lstm_global_cols.pkl")
    
    for sym in symbols:
        shutil.copy2(global_model_path, os.path.join(models_dir, f"lstm_{sym}.pth"))
        shutil.copy2(global_scaler_path, os.path.join(models_dir, f"lstm_{sym}_scaler.pkl"))
        shutil.copy2(global_target_scaler_path, os.path.join(models_dir, f"lstm_{sym}_target_scaler.pkl"))
        shutil.copy2(global_cols_path, os.path.join(models_dir, f"lstm_{sym}_cols.pkl"))
    
    print(f"  Saved model copies for {len(symbols)} symbols")
    
    print(f"\n{'=' * 60}")
    print(f"  GLOBAL LSTM TRAINING COMPLETE")
    print(f"  Best Val Loss: {best_loss:.6f}")
    print(f"  Symbols:       {len(symbols)}")
    print(f"  Sequences:     {total_sequences:,}")
    print(f"{'=' * 60}")
    
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train Global LSTM model")
    parser.add_argument('--epochs', type=int, default=50)
    parser.add_argument('--batch-size', type=int, default=256)
    parser.add_argument('--lr', type=float, default=0.001)
    parser.add_argument('--timeframe', type=str, default='M15')
    args = parser.parse_args()
    
    if not loader.initial_connect():
        print("Failed to connect to MT5.")
        sys.exit(1)
    
    train_global_lstm(
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        timeframe=args.timeframe
    )
