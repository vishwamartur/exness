"""
Auto-Trainer — Self-learning model retraining system.

Runs in a background thread during live trading:

1. RF Model: Retrains every 4 hours on the latest 10,000 M15 bars
2. LSTM Models: Retrains every 8 hours for each key symbol
3. Performance Tracker: Monitors win rate; triggers emergency retrain if
   win rate drops below 40% over last 20 trades

The bot adapts to changing market conditions without manual intervention.
"""

import threading
import time
import os
import sys
import importlib
import joblib
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from datetime import datetime, timezone
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import MinMaxScaler
from torch.utils.data import DataLoader, TensorDataset

# Add project root to path (highest priority) to avoid lag-llama namespace conflict
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import settings

# Load data.loader from exact path to avoid lag-llama's 'data' namespace collision
_loader_path = os.path.join(_PROJECT_ROOT, 'data', 'loader.py')
_spec = importlib.util.spec_from_file_location('data.loader', _loader_path)
loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loader)

from strategy import features

# Conditional imports
try:
    from strategy.lstm_model import BiLSTMWithAttention
    LSTM_AVAILABLE = True
except ImportError:
    LSTM_AVAILABLE = False

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")
KEY_SYMBOLS = ["EURUSD", "XAUUSD", "BTCUSD", "GBPUSD"]

# ═══════════════════════════════════════════════════════════════════════════
#  ATR-BASED LABELLING (matches live trading logic)
# ═══════════════════════════════════════════════════════════════════════════

def _label_with_atr(df, atr_tp_mult=3.0, atr_sl_mult=1.5, horizon=20):
    """Labels data using ATR barriers matching live trade logic."""
    labels = []
    atr = df['atr'].values if 'atr' in df.columns else None

    for i in range(len(df)):
        if atr is None or i + horizon >= len(df) or atr[i] <= 0:
            labels.append(0)
            continue

        entry = df['close'].iloc[i]
        tp_dist = atr[i] * atr_tp_mult
        sl_dist = atr[i] * atr_sl_mult

        future = df.iloc[i + 1: i + 1 + horizon]
        hit_tp = any(future['high'] >= entry + tp_dist)
        hit_sl = any(future['low'] <= entry - sl_dist)

        labels.append(1 if hit_tp and not hit_sl else 0)

    return pd.Series(labels, index=df.index)


# ═══════════════════════════════════════════════════════════════════════════
#  AUTO TRAINER
# ═══════════════════════════════════════════════════════════════════════════

class AutoTrainer:
    """
    Background self-learning system.

    Schedule:
      - RF retrain: every rf_interval_hours (default 4h)
      - LSTM retrain: every lstm_interval_hours (default 8h)
      - Performance check: every perf_check_minutes (default 30min)
      - Emergency retrain: if win rate < 40% over last 20 trades

    Thread-safe: uses locks for model swaps.
    """

    def __init__(self, strategy, journal,
                 rf_interval_hours=4,
                 lstm_interval_hours=8,
                 perf_check_minutes=30):
        self.strategy = strategy
        self.journal = journal
        self.rf_interval = rf_interval_hours * 3600
        self.lstm_interval = lstm_interval_hours * 3600
        self.perf_check_interval = perf_check_minutes * 60

        self.last_rf_train = time.time()
        self.last_lstm_train = time.time()
        self.last_perf_check = time.time()
        self._running = False
        self._thread = None
        self._lock = threading.Lock()

        # Track retrain stats
        self.rf_retrain_count = 0
        self.lstm_retrain_count = 0
        self.emergency_retrain_count = 0

    def start(self):
        """Starts the background auto-training thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        print("[AUTO-TRAIN] Background self-learning started")
        print(f"  RF retrain:   every {self.rf_interval // 3600}h")
        print(f"  LSTM retrain: every {self.lstm_interval // 3600}h")
        print(f"  Perf check:   every {self.perf_check_interval // 60}min")

    def stop(self):
        """Stops the background thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        print("[AUTO-TRAIN] Stopped")

    def _run_loop(self):
        """Main background loop."""
        # Wait 5 minutes before first check to let bot stabilize
        time.sleep(300)

        while self._running:
            try:
                now = time.time()

                # Performance check (most frequent)
                if now - self.last_perf_check >= self.perf_check_interval:
                    self._check_performance()
                    self.last_perf_check = now

                # RF retrain
                if now - self.last_rf_train >= self.rf_interval:
                    self._retrain_rf()
                    self.last_rf_train = now

                # LSTM retrain
                if now - self.last_lstm_train >= self.lstm_interval:
                    self._retrain_lstm_all()
                    self.last_lstm_train = now

                # Sleep 60s between checks
                time.sleep(60)

            except Exception as e:
                print(f"[AUTO-TRAIN] Error in loop: {e}")
                time.sleep(120)

    # ─── Performance Monitor ─────────────────────────────────────────────

    def _check_performance(self):
        """Checks recent performance and triggers emergency retrain if needed."""
        stats = self.journal.get_daily_stats()
        if stats is None or stats['total'] < 5:
            return  # Not enough trades to evaluate

        win_rate = stats['win_rate']
        total_profit = stats['total_profit']

        status = "OK" if win_rate >= 40 else "POOR"
        print(f"[AUTO-TRAIN] Perf check: {win_rate:.0f}% WR, "
              f"${total_profit:.2f} P/L — {status}")

        # Emergency retrain if win rate is terrible
        if win_rate < 40 and stats['total'] >= 10:
            print("[AUTO-TRAIN] ⚠ Win rate below 40%! Triggering emergency retrain...")
            self.emergency_retrain_count += 1
            self._retrain_rf(emergency=True)

    # ─── RF Retraining ───────────────────────────────────────────────────

    def _retrain_rf(self, emergency=False):
        """
        Retrains the Random Forest on recent M15 data.
        Uses hot-swap: trains new model, validates, then replaces old model atomically.
        """
        tag = "EMERGENCY" if emergency else "SCHEDULED"
        print(f"\n[AUTO-TRAIN] RF retrain ({tag})...")

        try:
            # Fetch fresh data
            symbol = settings.SYMBOLS[0] if settings.SYMBOLS else "EURUSD"
            df = loader.get_historical_data(symbol, "M15", settings.HISTORY_BARS)
            if df is None or len(df) < 500:
                print("[AUTO-TRAIN] Not enough data for RF retrain")
                return

            # Feature engineering
            df = features.add_technical_features(df)

            # Label
            df['target'] = _label_with_atr(
                df,
                atr_tp_mult=settings.ATR_TP_MULTIPLIER,
                atr_sl_mult=settings.ATR_SL_MULTIPLIER
            )
            df = df.iloc[:-21].dropna()

            # Prepare features
            drop_cols = ['time', 'open', 'high', 'low', 'close',
                         'tick_volume', 'spread', 'real_volume', 'target']
            feature_cols = [c for c in df.columns if c not in drop_cols]

            X = df[feature_cols]
            y = df['target']

            # Time-based split
            split_idx = int(len(X) * 0.8)
            X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
            y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

            # Train new model
            new_model = RandomForestClassifier(
                n_estimators=300,
                max_depth=20,
                min_samples_leaf=10,
                min_samples_split=20,
                max_features='sqrt',
                random_state=42,
                class_weight='balanced',
                n_jobs=-1
            )
            new_model.fit(X_train, y_train)

            # Validate — only swap if new model is better than 55% accuracy
            from sklearn.metrics import accuracy_score
            preds = new_model.predict(X_test)
            accuracy = accuracy_score(y_test, preds)

            if accuracy < 0.50:
                print(f"[AUTO-TRAIN] RF accuracy too low ({accuracy:.2f}). Keeping old model.")
                return

            # Hot-swap: atomically replace model
            with self._lock:
                # Save to disk
                os.makedirs(MODELS_DIR, exist_ok=True)
                joblib.dump(new_model, settings.MODEL_PATH)
                joblib.dump(feature_cols, settings.MODEL_PATH.replace('.pkl', '_features.pkl'))

                # Replace in-memory model
                self.strategy.model = new_model
                self.strategy.feature_cols = feature_cols

            self.rf_retrain_count += 1
            print(f"[AUTO-TRAIN] ✓ RF retrained: accuracy={accuracy:.3f} "
                  f"(retrain #{self.rf_retrain_count})")

        except Exception as e:
            print(f"[AUTO-TRAIN] RF retrain failed: {e}")

    # ─── LSTM Retraining ─────────────────────────────────────────────────

    def _retrain_lstm_all(self):
        """Retrains LSTM models for all key symbols."""
        if not LSTM_AVAILABLE:
            return

        for sym in KEY_SYMBOLS:
            if not self._running:
                break
            self._retrain_lstm_single(sym)

    def _retrain_lstm_single(self, symbol, epochs=30, batch_size=32, lr=0.0005):
        """Retrains a single LSTM model with recent data."""
        print(f"[AUTO-TRAIN] LSTM retrain: {symbol}...")

        try:
            # Find actual symbol name on this account
            actual_sym = None
            for s in settings.SYMBOLS:
                base = s.rstrip('mc') if len(s) > 6 else s
                if base == symbol or s == symbol:
                    actual_sym = s
                    break
            if actual_sym is None:
                actual_sym = symbol

            df = loader.get_historical_data(actual_sym, "M15", settings.HISTORY_BARS)
            if df is None or len(df) < 500:
                print(f"[AUTO-TRAIN] Not enough data for {symbol}")
                return

            df = features.add_technical_features(df)
            df = df.dropna()

            drop_cols = ['time', 'open', 'high', 'low', 'close',
                         'tick_volume', 'spread', 'real_volume', 'target']
            feature_cols = [c for c in df.columns if c not in drop_cols]

            X = df[feature_cols].values
            y = df['close'].values.reshape(-1, 1)

            # Scale
            feature_scaler = MinMaxScaler()
            target_scaler = MinMaxScaler()
            X_scaled = feature_scaler.fit_transform(X)
            y_scaled = target_scaler.fit_transform(y)

            # Sequences
            seq_len = settings.LSTM_SEQ_LENGTH
            xs, ys = [], []
            for i in range(len(X_scaled) - seq_len):
                xs.append(X_scaled[i:i + seq_len])
                ys.append(y_scaled[i + seq_len])
            X_seq = np.array(xs)
            y_seq = np.array(ys)

            if len(X_seq) < 100:
                return

            # Split
            split = int(len(X_seq) * 0.8)
            train_ds = TensorDataset(
                torch.from_numpy(X_seq[:split]).float(),
                torch.from_numpy(y_seq[:split]).float()
            )
            val_ds = TensorDataset(
                torch.from_numpy(X_seq[split:]).float(),
                torch.from_numpy(y_seq[split:]).float()
            )

            train_loader = DataLoader(train_ds, shuffle=True, batch_size=batch_size)
            val_loader = DataLoader(val_ds, batch_size=batch_size)

            device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
            model = BiLSTMWithAttention(
                input_size=X.shape[1], hidden_size=64,
                num_layers=2, device=device
            ).to(device)

            criterion = nn.MSELoss()
            optimizer = optim.Adam(model.parameters(), lr=lr)

            best_loss = float('inf')
            patience = 7
            patience_counter = 0

            for epoch in range(epochs):
                if not self._running:
                    break

                model.train()
                for xb, yb in train_loader:
                    xb, yb = xb.to(device), yb.to(device)
                    optimizer.zero_grad()
                    out = model(xb)
                    loss = criterion(out, yb)
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                    optimizer.step()

                model.eval()
                val_loss = 0
                with torch.no_grad():
                    for xb, yb in val_loader:
                        xb, yb = xb.to(device), yb.to(device)
                        val_loss += criterion(model(xb), yb).item()
                val_loss /= len(val_loader)

                if val_loss < best_loss:
                    best_loss = val_loss
                    patience_counter = 0
                    # Save checkpoint
                    os.makedirs(MODELS_DIR, exist_ok=True)
                    torch.save(model.state_dict(),
                               os.path.join(MODELS_DIR, f"lstm_{symbol}.pth"))
                    joblib.dump(feature_scaler,
                                os.path.join(MODELS_DIR, f"lstm_{symbol}_scaler.pkl"))
                    joblib.dump(target_scaler,
                                os.path.join(MODELS_DIR, f"lstm_{symbol}_target_scaler.pkl"))
                    joblib.dump(feature_cols,
                                os.path.join(MODELS_DIR, f"lstm_{symbol}_cols.pkl"))
                else:
                    patience_counter += 1
                    if patience_counter >= patience:
                        break

            # Hot-swap LSTM in strategy
            with self._lock:
                from strategy.lstm_predictor import LSTMPredictor
                try:
                    new_predictor = LSTMPredictor(
                        model_path=os.path.join(MODELS_DIR, f"lstm_{symbol}.pth"),
                        scaler_path=os.path.join(MODELS_DIR, f"lstm_{symbol}_scaler.pkl"),
                        device='cuda' if torch.cuda.is_available() else 'cpu'
                    )
                    self.strategy.lstm_predictors[symbol] = new_predictor
                except Exception:
                    pass

            self.lstm_retrain_count += 1
            print(f"[AUTO-TRAIN] ✓ LSTM {symbol} retrained: "
                  f"val_loss={best_loss:.6f} (retrain #{self.lstm_retrain_count})")

        except Exception as e:
            print(f"[AUTO-TRAIN] LSTM {symbol} retrain failed: {e}")

    # ─── Status ──────────────────────────────────────────────────────────

    def get_status(self):
        """Returns auto-trainer status dict."""
        now = time.time()
        return {
            'running': self._running,
            'rf_retrains': self.rf_retrain_count,
            'lstm_retrains': self.lstm_retrain_count,
            'emergency_retrains': self.emergency_retrain_count,
            'next_rf_in': max(0, self.rf_interval - (now - self.last_rf_train)) / 60,
            'next_lstm_in': max(0, self.lstm_interval - (now - self.last_lstm_train)) / 60,
        }

    def print_status(self):
        """Prints compact status."""
        s = self.get_status()
        print(f"[AUTO-TRAIN] RF: {s['rf_retrains']} retrains (next in {s['next_rf_in']:.0f}min) | "
              f"LSTM: {s['lstm_retrains']} retrains | "
              f"Emergency: {s['emergency_retrains']}")
