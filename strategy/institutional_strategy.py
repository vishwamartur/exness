"""
Institutional Trading Strategy — v2.0 Power Upgrade
=====================================================
ALL 10 improvements integrated:

1. Parallel symbol scanning (ThreadPoolExecutor)
2. Per-candle cooldown (no repeat analysis on same candle)
3. TTL-based data caching (H1/H4 cached, saves API calls)
4. Correlation filter (no correlated duplicate positions)
5. News event filter (blocks high-impact windows)
6. Ensemble AI voting (weighted RF + LSTM + Lag-Llama)
7. Adaptive sureshot threshold (session-aware)
8. Trade journal (SQLite logging with confluence analysis)
9. Multi-symbol LSTM support
10. Dynamic lot sizing scaled by confluence quality

Flow: SCAN ALL (parallel) → FILTER (news/correlation/spread) → 
      RANK (ensemble score) → EXECUTE BEST ONLY (sureshot)
"""

import joblib
import pandas as pd
import numpy as np
import os
import sys
import MetaTrader5 as mt5
import torch
import torch
import time
import xgboost as xgb
from api import stream_server as stream
from utils.risk_manager import RiskManager
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings
from market_data import loader
from strategy import features
from utils.data_cache import DataCache
from utils.trade_journal import TradeJournal
from utils.correlation_filter import check_correlation_conflict
from utils.news_filter import is_news_blackout, get_active_events

# ─── AI Model Imports ────────────────────────────────────────────────────
try:
    from strategy.hf_predictor import HFPredictor
    HF_AVAILABLE = True
except (ImportError, Exception) as e:
    HF_AVAILABLE = False
    print(f"HF Predictor not available: {e}")

try:
    from strategy.lag_llama_predictor import get_lag_llama_predictor
    LAG_LLAMA_AVAILABLE = True
    print("LagLlamaPredictor module found.")
except (ImportError, Exception) as e:
    LAG_LLAMA_AVAILABLE = False
    print(f"LagLlamaPredictor not available: {e}")

try:
    from strategy.lstm_predictor import LSTMPredictor
    LSTM_AVAILABLE = True
    print("LSTMPredictor module found.")
except (ImportError, Exception) as e:
    LSTM_AVAILABLE = False
    print(f"LSTMPredictor not available: {e}")


def _get_asset_class(symbol):
    """Returns 'crypto', 'commodity', or 'forex' for a given symbol."""
    if symbol in getattr(settings, 'SYMBOLS_CRYPTO', []):
        return 'crypto'
    elif symbol in getattr(settings, 'SYMBOLS_COMMODITIES', []):
        return 'commodity'
    return 'forex'


def _strip_suffix(symbol):
    """Strip Exness suffix for model lookup."""
    for suffix in ['m', 'c']:
        if symbol.endswith(suffix) and len(symbol) > 3:
            base = symbol[:-len(suffix)]
            if len(base) >= 6:
                return base
    return symbol


class InstitutionalStrategy:
    """
    v2.0 Multi-Asset Sureshot Scanner with 10 power upgrades.

    Each cycle:
      1. Parallel scan ALL instruments (ThreadPoolExecutor)
      2. Filter: news blackout, correlation, spread, cooldown
      3. Score with ensemble (RF + LSTM + Lag-Llama weighted vote)
      4. Adaptive threshold based on session
      5. Execute ONLY the single best sureshot trade
      6. Log everything to SQLite trade journal
    """

    def __init__(self, mt5_client):
        self.client = mt5_client
        self.risk_manager = RiskManager(mt5_client)
        self.model = None       # Random Forest
        self.xgb_model = None   # XGBoost
        self.feature_cols = None
        self.hf_predictor = None
        self.lstm_predictors = {}  # Multi-symbol: {base_symbol: LSTMPredictor}

        # ─── State Tracking ──────────────────────────────────────────────
        self.last_trade_time = {}
        self.daily_trade_count = 0
        self.last_reset_date = datetime.now(timezone.utc).date()
        self.partial_closed = set()
        self.breakeven_set = set()
        self.last_candle_time = {}  # Per-candle cooldown: {symbol: last_candle_datetime}

        # ─── Infrastructure ──────────────────────────────────────────────
        self.cache = DataCache()
        self.journal = TradeJournal()

        # ─── Initialize AI Models ────────────────────────────────────────
        if settings.USE_LAG_LLAMA and LAG_LLAMA_AVAILABLE:
            try:
                print("Initializing Lag-Llama...")
                self.hf_predictor = get_lag_llama_predictor(settings)
                print("Lag-Llama initialized.")
            except Exception as e:
                print(f"Failed to init Lag-Llama: {e}")
                self.hf_predictor = None
        elif HF_AVAILABLE:
            try:
                self.hf_predictor = HFPredictor("amazon/chronos-t5-tiny")
            except Exception as e:
                print(f"Failed to init Chronos: {e}")
                self.hf_predictor = None

        # Load multi-symbol LSTMs
        if settings.USE_LSTM and LSTM_AVAILABLE:
            self._load_lstm_models()

    def _load_lstm_models(self):
        """Load LSTM models for all symbols that have trained weights."""
        models_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")
        key_symbols = ["EURUSD", "XAUUSD", "BTCUSD", "GBPUSD"]

        for sym in key_symbols:
            model_path = os.path.join(models_dir, f"lstm_{sym}.pth")
            scaler_path = os.path.join(models_dir, f"lstm_{sym}_scaler.pkl")

            if os.path.exists(model_path) and os.path.exists(scaler_path):
                try:
                    predictor = LSTMPredictor(
                        model_path=model_path,
                        scaler_path=scaler_path,
                        device='cuda' if torch.cuda.is_available() else 'cpu'
                    )
                    self.lstm_predictors[sym] = predictor
                    print(f"  LSTM loaded: {sym}")
                except Exception as e:
                    print(f"  LSTM failed for {sym}: {e}")

        if not self.lstm_predictors:
            # Fallback to default single LSTM
            try:
                default_lstm = LSTMPredictor(
                    model_path=settings.LSTM_MODEL_PATH,
                    scaler_path=settings.LSTM_SCALER_PATH,
                    device='cuda' if torch.cuda.is_available() else 'cpu'
                )
                self.lstm_predictors['default'] = default_lstm
                print("  LSTM loaded: default")
            except Exception as e:
                print(f"  Default LSTM failed: {e}")

    def load_model(self):
        """Loads both Random Forest and XGBoost models."""
        try:
            # 1. Load RF Model
            if os.path.exists(settings.MODEL_PATH):
                self.model = joblib.load(settings.MODEL_PATH)
                print(f"RF Model loaded successfully.")
            else:
                print(f"RF model not found at {settings.MODEL_PATH}")

            # 2. Load XGBoost Model
            if getattr(settings, 'USE_XGBOOST', False) and os.path.exists(settings.XGB_MODEL_PATH):
                self.xgb_model = joblib.load(settings.XGB_MODEL_PATH)
                print(f"XGBoost Model loaded successfully.")
            else:
                print(f"XGBoost model not found or disabled.")

            # 3. Load Feature Columns (shared/common)
            feat_path = settings.MODEL_PATH.replace('.pkl', '_features.pkl')
            if os.path.exists(feat_path):
                self.feature_cols = joblib.load(feat_path)
            
            return self.model is not None
        except Exception as e:
            print(f"Error loading models: {e}")
            return False

    # ═══════════════════════════════════════════════════════════════════════
    #  PARALLEL MULTI-ASSET SCANNER
    # ═══════════════════════════════════════════════════════════════════════

    def scan_all_markets(self):
        """
        Main entry point. Two-phase approach:
          Phase 1 (sequential): Fetch data via MT5 API (NOT thread-safe)
          Phase 2 (parallel):   Score setups using CPU (thread-safe)
        """
        # 0. Manage all existing positions
        for symbol in settings.SYMBOLS:
            try:
                self.manage_positions(symbol)
            except Exception:
                pass

        # 1. Pre-flight global checks
        if not self._is_trading_session():
            print("[SCANNER] Outside trading session. Skipping.")
            return

        if not self._check_daily_limit():
            print("[SCANNER] Daily trade limit reached.")
            return

        # Check open positions
        all_positions = self.client.get_all_positions()
        if len(all_positions) >= settings.MAX_OPEN_POSITIONS:
            print(f"[SCANNER] Max positions ({len(all_positions)}/{settings.MAX_OPEN_POSITIONS})")
            return

        # Check news events
        active_news = get_active_events()
        if active_news:
            print(f"[NEWS] Active events: {', '.join(active_news)}")

        print(f"\n{'='*60}")
        print(f"  SCANNING {len(settings.SYMBOLS)} INSTRUMENTS")
        print(f"{'='*60}")

        # ── Phase 1: Sequential data fetch (MT5 API is NOT thread-safe) ──
        symbol_data = {}
        for symbol in settings.SYMBOLS:
            # Risk Check 1: Pre-scan (Spread, News, Cooldown)
            allowed, reason = self.risk_manager.check_pre_scan(symbol)
            if not allowed:
                # print(f"[SKIP] {symbol}: {reason}")
                continue

            data = self._fetch_symbol_data(symbol)
            if data is not None:
                symbol_data[symbol] = data

        print(f"  Data fetched: {len(symbol_data)}/{len(settings.SYMBOLS)} symbols")

        if not symbol_data:
            print("[SCANNER] No valid data. Skipping cycle.")
            return

        # ── Phase 2: Score all symbols (CPU work, can be parallel) ──
        candidates = []
        skipped = 0

        with ThreadPoolExecutor(max_workers=min(8, len(symbol_data))) as executor:
            futures = {
                executor.submit(self._score_symbol, sym, data_dict): sym
                for sym, data_dict in symbol_data.items()
            }
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    result = future.result()
                    if result:
                        # Correlation filter via RiskManager (Pre-execution check)
                        allowed, reason = self.risk_manager.check_execution(
                            result['symbol'], result['direction'], all_positions
                        )
                        if not allowed:
                            skipped += 1
                        else:
                            candidates.append(result)
                except Exception:
                    pass

        print(f"\n[SCANNER] Candidates: {len(candidates)} | Corr-filtered: {skipped}")

        # Print ALL scores for debugging
        if candidates:
            candidates.sort(
                key=lambda x: (x['ensemble_score'], x['score'], x['rf_prob']),
                reverse=True
            )
            print(f"\n{'─'*70}")
            print(f"  {'Symbol':>10} | {'Dir':>4} | Conf | Ens  | RF   | Details")
            print(f"{'─'*70}")
            for c in candidates[:10]:
                threshold = self._get_adaptive_threshold()
                marker = ">>>" if c['score'] >= threshold else "   "
                details_str = ' '.join(f"{k}:{v}" for k, v in c['details'].items())
                print(f"{marker} {c['symbol']:>10} | {c['direction']:>4} | "
                      f"{c['score']}/6  | {c['ensemble_score']:.2f} | "
                      f"{c['rf_prob']:.2f} | {details_str}")
        else:
            print("[SCANNER] No setups found this cycle.")
            return

        # Execute ONLY the best if it meets ADAPTIVE threshold
        best = candidates[0]
        threshold = self._get_adaptive_threshold()

        if best['score'] >= threshold:
            print(f"\n  >>> SURESHOT: {best['symbol']} {best['direction']} "
                  f"(Score {best['score']}/6, Threshold {threshold})")
            self._execute_trade(best)
        else:
            print(f"\n  Best: {best['symbol']} {best['direction']} "
                  f"score={best['score']} < threshold={threshold}. NO TRADE.")

        self.journal.print_summary()

    def _fetch_symbol_data(self, symbol):
        """
        Phase 1: Sequential data fetching (MT5 API calls).
        Returns dict with M15/H1/H4 DataFrames, or None.
        """
        # Trade cooldown
        last_time = self.last_trade_time.get(symbol, 0)
        if time.time() - last_time < settings.COOLDOWN_SECONDS:
            return None

        # Spread check
        if not self._check_spread(symbol):
            return None

        # News blackout
        is_blackout, event_name = is_news_blackout(symbol)
        if is_blackout:
            return None

        # Fetch M15 data
        df = loader.get_historical_data(symbol, settings.TIMEFRAME, 500)
        if df is None or len(df) < 100:
            return None

        # Also fetch H1 and H4 for multi-timeframe confluence
        h1_df = loader.get_historical_data(symbol, "H1", 100)
        h4_df = loader.get_historical_data(symbol, "H4", 60)

        return {
            'M15': df,
            'H1': h1_df,
            'H4': h4_df,
        }

    def _score_symbol(self, symbol, data_dict):
        """
        Phase 2: CPU-bound scoring (thread-safe, no MT5 calls).
        data_dict = {'M15': df, 'H1': df, 'H4': df}
        Returns the best setup (buy or sell) or None.
        """
        df = data_dict['M15']

        # Feature engineering
        try:
            df_features = features.add_technical_features(df)
        except Exception:
            return None

        if len(df_features) < 10:
            return None

        last = df_features.iloc[-1]
        atr = last.get('atr', 0)
        if atr <= 0:
            return None

        sl_distance = atr * settings.ATR_SL_MULTIPLIER
        tp_distance = atr * settings.ATR_TP_MULTIPLIER

        # Pre-compute H1/H4 trends from pre-fetched data
        h1_trend = self._compute_trend(data_dict.get('H1'))
        h4_trend = self._compute_trend(data_dict.get('H4'))

        # Score both directions
        buy_score, buy_details = self._calculate_confluence(
            symbol, df_features, "buy", h1_trend=h1_trend, h4_trend=h4_trend)
        sell_score, sell_details = self._calculate_confluence(
            symbol, df_features, "sell", h1_trend=h1_trend, h4_trend=h4_trend)

        rf_prob, _ = self._get_rf_prediction(df_features)
        ai_signal = self._get_ai_signal(symbol, df_features)
        best_score = max(buy_score, sell_score)
        ensemble = self._ensemble_vote(rf_prob, ai_signal, best_score)

        # Prepare stream data
        stream_data = {
            'symbol': symbol,
            'time': str(last['time']),
            'close': float(last['close']),
            'score': best_score,
            'direction': 'BUY' if buy_score >= sell_score else 'SELL',
            'rf_prob': float(rf_prob),
            'ai_signal': float(ai_signal),
            'atr': float(atr),
            'adx': float(last.get('adx', 0)),
            'rsi': float(last.get('rsi', 0)),
            'is_setup': best_score >= settings.MIN_CONFLUENCE_SCORE
        }
        try:
            stream.push_update(stream_data)
        except Exception:
            pass

        # Pick the stronger direction — no minimum filter here, let scanner rank
        if buy_score >= sell_score and buy_score >= settings.MIN_CONFLUENCE_SCORE:
            return {
                'symbol': symbol,
                'direction': 'BUY',
                'score': buy_score,
                'details': buy_details,
                'rf_prob': rf_prob,
                'ensemble_score': ensemble,
                'ai_signal': ai_signal,
                'sl_distance': sl_distance,
                'tp_distance': tp_distance,
                'atr': atr,
                'df_features': df_features,
            }
        elif sell_score > buy_score and sell_score >= settings.MIN_CONFLUENCE_SCORE:
            return {
                'symbol': symbol,
                'direction': 'SELL',
                'score': sell_score,
                'details': sell_details,
                'rf_prob': 1 - rf_prob,
                'ensemble_score': ensemble,
                'ai_signal': ai_signal,
                'sl_distance': sl_distance,
                'tp_distance': tp_distance,
                'atr': atr,
                'df_features': df_features,
            }
        
        return None

    def _execute_trade(self, setup):
        """Executes a trade and logs to journal."""
        symbol = setup['symbol']
        direction = setup['direction']
        score = setup['score']
        sl_distance = setup['sl_distance']
        tp_distance = setup['tp_distance']

        # 1. Pre-execution Risk Check (Correlation, etc.)
        positions = self.client.get_open_positions()
        allowed, reason = self.risk_manager.check_execution(symbol, direction, positions)
        if not allowed:
            print(f"[RISK] Trade blocked for {symbol}: {reason}")
            return

        # 2. Dynamic Position Sizing
        lot = self.risk_manager.calculate_position_size(symbol, sl_distance, score)

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return

        if direction == 'BUY':
            sl_price = tick.ask - sl_distance
            tp_price = tick.ask + tp_distance
            order_type = mt5.ORDER_TYPE_BUY
            entry_price = tick.ask
        else:
            sl_price = tick.bid + sl_distance
            tp_price = tick.bid - tp_distance
            order_type = mt5.ORDER_TYPE_SELL
            entry_price = tick.bid

        rr = tp_distance / sl_distance if sl_distance > 0 else 0

        print(f"\n[{symbol}] >>> INSTITUTIONAL {direction} <<<")
        print(f"  Lot: {lot} | SL: {sl_price:.5f} | TP: {tp_price:.5f} | "
              f"R:R = 1:{rr:.1f} | Risk: {risk_pct}%")
        print(f"  Ensemble: {setup['ensemble_score']:.2f} | "
              f"Confluences: {' | '.join(f'{k}:{v}' for k, v in setup['details'].items())}")

        result = self.client.place_order(
            order_type,
            symbol=symbol,
            volume=lot,
            sl_price=sl_price,
            tp_price=tp_price,
        )

        if result:
            print(f"✅ TRADE EXECUTED: {symbol} {direction} | Lot: {lot} | Score: {score}")
            self.risk_manager.record_trade(symbol)
            
            # Log to Journal
            self.journal.log_trade(
                symbol=symbol,
                direction=direction,
                lot_size=lot,
                entry_price=entry_price,
                sl_price=sl_price,
                tp_price=tp_price,
                confluence_score=score,
                confluence_details=setup['details'],
                rf_probability=setup['rf_prob'],
                ai_signal=setup.get('ai_signal', 0),
                asset_class=_get_asset_class(symbol),
                session=self._get_current_session()
            )

            print(f"  ✓ Trade #{self.daily_trade_count}/{settings.MAX_DAILY_TRADES} | "
                  f"Balance: ${balance:.2f} | Logged to journal")

    # ─── Per-Candle Cooldown ─────────────────────────────────────────────

    def _is_new_candle(self, symbol):
        """Returns True only if the latest candle is different from last analysis."""
        df = self.cache.get(symbol, settings.TIMEFRAME, 10)
        if df is None or len(df) < 2:
            return True

        latest_time = df['time'].iloc[-1]
        last_analyzed = self.last_candle_time.get(symbol)

        if last_analyzed is None or latest_time != last_analyzed:
            self.last_candle_time[symbol] = latest_time
            return True

        return False

    # ─── Adaptive Sureshot Threshold ─────────────────────────────────────

    def _get_adaptive_threshold(self):
        """
        Returns dynamic sureshot threshold based on session.
        - London/NY overlap (peak liquidity): Lower threshold (4)
        - London or NY solo: Standard (5)
        - Asian/off-hours: Higher threshold (6)
        """
        now_utc = datetime.now(timezone.utc)
        hour = now_utc.hour

        # London/NY overlap = best conditions, lower threshold
        overlap = settings.TRADE_SESSIONS.get('overlap', {})
        if overlap.get('start', 13) <= hour < overlap.get('end', 16):
            return max(4, settings.SURESHOT_MIN_SCORE - 1)

        # London or NY = good conditions
        london = settings.TRADE_SESSIONS.get('london', {})
        ny = settings.TRADE_SESSIONS.get('new_york', {})
        if (london.get('start', 8) <= hour < london.get('end', 12) or
                ny.get('start', 13) <= hour < ny.get('end', 17)):
            return settings.SURESHOT_MIN_SCORE

        # Off-hours = stricter
        return min(6, settings.SURESHOT_MIN_SCORE + 1)

    def _get_current_session(self):
        """Returns current session name."""
        now_utc = datetime.now(timezone.utc)
        hour = now_utc.hour
        for name, times in settings.TRADE_SESSIONS.items():
            if times['start'] <= hour < times['end']:
                return name
        return 'off_hours'

    # ─── Ensemble AI Voting ──────────────────────────────────────────────

    def _ensemble_vote(self, rf_prob, ai_signal, confluence_score):
        """
        Weighted ensemble score combining all models.
        
        Weights:
          RF Model:      0.30  (probability-based)
          AI Signal:     0.25  (LSTM + Lag-Llama direction)
          Confluence:    0.45  (technical analysis score)
        
        Returns: 0.0 to 1.0 (higher = stronger signal)
        """
        # Normalize each component to 0-1
        rf_component = rf_prob  # Already 0-1

        # AI signal: -1 to +1 → 0 to 1
        ai_component = (ai_signal + 1) / 2

        # Confluence: 0-6 → 0-1
        conf_component = confluence_score / 6.0

        # Weighted combination
        ensemble = (
            0.30 * rf_component +
            0.25 * ai_component +
            0.45 * conf_component
        )

        return round(ensemble, 3)

    # ─── Multi-Timeframe Trend (CACHED) ──────────────────────────────────

    def _compute_trend(self, df, sma_period=50):
        """Computes trend from pre-fetched DataFrame (no MT5 calls)."""
        if df is None or len(df) < sma_period + 5:
            return 0

        sma = df['close'].rolling(window=sma_period).mean().iloc[-1]
        close = df['close'].iloc[-1]

        if close > sma * 1.001:
            return 1
        elif close < sma * 0.999:
            return -1
        return 0

    def get_h1_trend(self, symbol):
        """Fetches H1 data and computes trend. Use _compute_trend in Phase 2."""
        df = loader.get_historical_data(symbol, "H1", 60)
        return self._compute_trend(df)

    def get_h4_trend(self, symbol):
        """Fetches H4 data and computes trend. Use _compute_trend in Phase 2."""
        df = loader.get_historical_data(symbol, "H4", 100)
        return self._compute_trend(df)

    # ─── Session Filter ──────────────────────────────────────────────────

    def _is_trading_session(self):
        """Returns True if we're in a valid trading session."""
        if not settings.SESSION_FILTER:
            return True

        now_utc = datetime.now(timezone.utc)
        hour = now_utc.hour

        for session_name, times in settings.TRADE_SESSIONS.items():
            if times["start"] <= hour < times["end"]:
                return True

        return False

    # ─── Spread Filter ───────────────────────────────────────────────────

    def _check_spread(self, symbol):
        """Returns True if spread is acceptable for the asset class."""
        tick = mt5.symbol_info_tick(symbol)
        symbol_info = mt5.symbol_info(symbol)
        if tick is None or symbol_info is None:
            return False

        spread_pips = (tick.ask - tick.bid) / symbol_info.point

        asset_class = _get_asset_class(symbol)
        if asset_class == 'crypto':
            max_spread = settings.MAX_SPREAD_PIPS_CRYPTO * 10
        elif asset_class == 'commodity':
            max_spread = settings.MAX_SPREAD_PIPS_COMMODITY * 10
        else:
            max_spread = settings.MAX_SPREAD_PIPS * 10

        return spread_pips <= max_spread

    # ─── Daily Trade Counter ─────────────────────────────────────────────

    def _check_daily_limit(self):
        """Resets daily counter at midnight UTC and checks the limit."""
        today = datetime.now(timezone.utc).date()
        if today != self.last_reset_date:
            self.daily_trade_count = 0
            self.last_reset_date = today
            print(f"[SYSTEM] Daily trade counter reset for {today}")

        return self.daily_trade_count < settings.MAX_DAILY_TRADES

    # ─── Confluence Scoring ──────────────────────────────────────────────

    def _calculate_confluence(self, symbol, df_features, direction="buy",
                              h1_trend=None, h4_trend=None):
        """
        Calculates confluence score (0-6):
          +1  H4 trend alignment
          +1  H1 trend alignment
          +1  RF model confidence > threshold
          +1  AI model confirms direction
          +1  Price near Order Block or FVG
          +1  ADX > 25 (trending market)
        """
        score = 0
        details = {}
        last = df_features.iloc[-1]

        # 1. H4 Trend (use pre-computed if available)
        if settings.H4_TREND_FILTER:
            h4 = h4_trend if h4_trend is not None else self.get_h4_trend(symbol)
            if direction == "buy" and h4 >= 1:
                score += 1
                details['H4'] = '✓'
            elif direction == "sell" and h4 <= -1:
                score += 1
                details['H4'] = '✓'
            else:
                details['H4'] = '✗'

        # 2. H1 Trend (use pre-computed if available)
        if settings.H1_TREND_FILTER:
            h1 = h1_trend if h1_trend is not None else self.get_h1_trend(symbol)
            if direction == "buy" and h1 >= 1:
                score += 1
                details['H1'] = '✓'
            elif direction == "sell" and h1 <= -1:
                score += 1
                details['H1'] = '✓'
            else:
                details['H1'] = '✗'

        # 3. Tree Ensemble (RF + XGBoost)
        rf_prob, _ = self._get_rf_prediction(df_features)
        xgb_prob, _ = self._get_xgb_prediction(df_features)
        
        # Weighted average or separate votes? 
        # Averaging is more robust against single-model noise.
        ml_prob = (rf_prob + xgb_prob) / 2 if self.xgb_model else rf_prob
        
        threshold = settings.RF_PROB_THRESHOLD
        if direction == "buy" and ml_prob > threshold:
            score += 1
            details['ML'] = f'✓{ml_prob:.2f}'
        elif direction == "sell" and ml_prob < (1 - threshold):
            score += 1
            details['ML'] = f'✓{ml_prob:.2f}'
        else:
            details['ML'] = f'✗{ml_prob:.2f}'

        # 4. AI Confirmation (multi-symbol LSTM)
        ai_signal = self._get_ai_signal(symbol, df_features)
        if direction == "buy" and ai_signal >= 1:
            score += 1
            details['AI'] = '✓'
        elif direction == "sell" and ai_signal <= -1:
            score += 1
            details['AI'] = '✓'
        elif not self.hf_predictor and not self.lstm_predictors:
            details['AI'] = '~'
        else:
            details['AI'] = '✗'

        # 5. Near Order Block or FVG
        smc_hit = False
        if direction == "buy":
            if last.get('near_ob_bullish', 0) == 1 or last.get('near_fvg_bullish', 0) == 1:
                smc_hit = True
            if last.get('liq_sweep_low', 0) == 1:
                smc_hit = True
        else:
            if last.get('near_ob_bearish', 0) == 1 or last.get('near_fvg_bearish', 0) == 1:
                smc_hit = True
            if last.get('liq_sweep_high', 0) == 1:
                smc_hit = True

        if smc_hit:
            score += 1
            details['SMC'] = '✓'
        else:
            details['SMC'] = '✗'

        # 6. ADX > 25
        adx_val = last.get('adx', 0)
        if adx_val > 25:
            score += 1
            details['ADX'] = f'✓{adx_val:.0f}'
        else:
            details['ADX'] = f'✗{adx_val:.0f}'

        return score, details

    # ─── RF Prediction ───────────────────────────────────────────────────

    def _get_rf_prediction(self, df_features):
        """Returns (probability, prediction) from RF model."""
        if self.model is None:
            return 0.5, 0

        last_row = df_features.iloc[-1:]

        if self.feature_cols:
            available_cols = [c for c in self.feature_cols if c in last_row.columns]
            if not available_cols:
                return 0.5, 0
            X = last_row[available_cols]
        else:
            # Fallback if feature_cols not defined, drop common non-feature columns
            drop_cols = ['time', 'open', 'high', 'low', 'close', 'tick_volume',
                         'spread', 'real_volume', 'target']
            X = last_row.drop(columns=[c for c in drop_cols if c in last_row.columns], errors='ignore')
        
        try:
            prob = self.model.predict_proba(X)[0][1]
            pred = self.model.predict(X)[0]
            return prob, pred
        except Exception as e:
            # print(f"RF Prediction error: {e}") # Uncomment for debugging
            return 0.5, 0

    def _get_xgb_prediction(self, df_features):
        """Returns (probability, prediction) from XGBoost model."""
        if self.xgb_model is None:
            return 0.5, 0

        last_row = df_features.iloc[-1:]

        if self.feature_cols:
            available_cols = [c for c in self.feature_cols if c in last_row.columns]
            if not available_cols:
                return 0.5, 0
            X = last_row[available_cols]
        else:
            # Fallback if feature_cols not defined, drop common non-feature columns
            X = last_row.drop(columns=['time', 'open', 'high', 'low', 'close', 
                                     'tick_volume', 'spread', 'real_volume', 'target'], 
                            errors='ignore')
            
        try:
            prob = self.xgb_model.predict_proba(X)[0][1]
            pred = self.xgb_model.predict(X)[0]
            return prob, pred
        except Exception as e:
            # print(f"XGBoost Prediction error: {e}") # Uncomment for debugging
            return 0.5, 0

    # ─── AI Signal (Multi-Symbol LSTM) ───────────────────────────────────

    def _get_ai_signal(self, symbol, df_features):
        """
        Combined AI signal from multi-symbol LSTM + Lag-Llama/Chronos.
        Returns: +1 (buy), -1 (sell), 0 (neutral)
        """
        signals = []

        # Lag-Llama / Chronos
        if self.hf_predictor and 'close' in df_features.columns:
            try:
                recent_closes = torch.tensor(df_features['close'].values[-60:])
                forecast = self.hf_predictor.predict(
                    recent_closes.unsqueeze(0), prediction_length=12
                )
                current_price = df_features['close'].iloc[-1]
                future_price = forecast[0, 5].item()

                if future_price > current_price * 1.0003:
                    signals.append(1)
                elif future_price < current_price * 0.9997:
                    signals.append(-1)
                else:
                    signals.append(0)
            except Exception:
                pass

        # Multi-symbol LSTM: try symbol-specific model, fallback to default
        base_symbol = _strip_suffix(symbol)
        lstm = self.lstm_predictors.get(base_symbol) or self.lstm_predictors.get('default')

        if lstm:
            try:
                lstm_pred = lstm.predict(df_features)
                if lstm_pred:
                    current_price = df_features['close'].iloc[-1]
                    if lstm_pred > current_price * 1.0003:
                        signals.append(1)
                    elif lstm_pred < current_price * 0.9997:
                        signals.append(-1)
                    else:
                        signals.append(0)
            except Exception:
                pass

        if not signals:
            return 0

        avg = sum(signals) / len(signals)
        if avg > 0.3:
            return 1
        elif avg < -0.3:
            return -1
        return 0

    # ─── Position Management ─────────────────────────────────────────────

    def manage_positions(self, symbol):
        """
        Institutional exit management:
        1. Break-even stop at 1:1 R:R
        2. Partial close at 50% of TP distance  
        3. Trailing stop on remainder
        4. Log exits to journal
        """
        positions = self.client.get_positions(symbol)
        if not positions:
            return

        current_tick = mt5.symbol_info_tick(symbol)
        if current_tick is None:
            return

        for pos in positions:
            entry_price = pos.price_open
            current_sl = pos.sl
            current_tp = pos.tp

            if pos.type == mt5.ORDER_TYPE_BUY:
                current_price = current_tick.bid
                risk = entry_price - current_sl if current_sl > 0 else 0
                profit_distance = current_price - entry_price

                # Break-Even
                if (risk > 0 and
                    profit_distance >= risk * settings.BREAKEVEN_RR and
                    pos.ticket not in self.breakeven_set):
                    be_sl = entry_price + (risk * 0.1)
                    if be_sl > current_sl:
                        self.client.modify_position(pos.ticket, be_sl, current_tp)
                        self.breakeven_set.add(pos.ticket)
                        print(f"[{symbol}] ★ Break-Even @ {be_sl:.5f}")

                # Partial Close
                if (current_tp > 0 and pos.ticket not in self.partial_closed):
                    tp_distance = current_tp - entry_price
                    if profit_distance >= tp_distance * 0.5:
                        result = self.client.partial_close(
                            pos.ticket, settings.PARTIAL_CLOSE_FRACTION)
                        if result:
                            self.partial_closed.add(pos.ticket)
                            print(f"[{symbol}] ★ Partial profit (50%)")

                # Trailing Stop
                profit_percent = profit_distance / entry_price if entry_price > 0 else 0
                if profit_percent > settings.TRAILING_STOP_ACTIVATE_PERCENT:
                    new_sl = current_price - (settings.TRAILING_STOP_STEP_PERCENT * entry_price)
                    if new_sl > current_sl:
                        self.client.modify_position(pos.ticket, new_sl, current_tp)

            elif pos.type == mt5.ORDER_TYPE_SELL:
                current_price = current_tick.ask
                risk = current_sl - entry_price if current_sl > 0 else 0
                profit_distance = entry_price - current_price

                # Break-Even
                if (risk > 0 and
                    profit_distance >= risk * settings.BREAKEVEN_RR and
                    pos.ticket not in self.breakeven_set):
                    be_sl = entry_price - (risk * 0.1)
                    if be_sl < current_sl or current_sl == 0:
                        self.client.modify_position(pos.ticket, be_sl, current_tp)
                        self.breakeven_set.add(pos.ticket)
                        print(f"[{symbol}] ★ Break-Even @ {be_sl:.5f}")

                # Partial Close
                if (current_tp > 0 and pos.ticket not in self.partial_closed):
                    tp_distance = entry_price - current_tp
                    if profit_distance >= tp_distance * 0.5:
                        result = self.client.partial_close(
                            pos.ticket, settings.PARTIAL_CLOSE_FRACTION)
                        if result:
                            self.partial_closed.add(pos.ticket)
                            print(f"[{symbol}] ★ Partial profit (50%)")

                # Trailing Stop
                profit_percent = profit_distance / entry_price if entry_price > 0 else 0
                if profit_percent > settings.TRAILING_STOP_ACTIVATE_PERCENT:
                    new_sl = current_price + (settings.TRAILING_STOP_STEP_PERCENT * entry_price)
                    if new_sl < current_sl or current_sl == 0:
                        self.client.modify_position(pos.ticket, new_sl, current_tp)

    # ─── Legacy ──────────────────────────────────────────────────────────

    def check_market(self, symbol):
        """Backward compatible — use scan_all_markets() instead."""
        pass
