"""
Strategy Parameter Auto-Tuner
==============================
Walk-forward optimization with per-session parameter tuning.
Uses ATR barrier labeling (same approach as optimize_walkforward.py)
to grid-search optimal parameters, applies guardrails to prevent
overfitting, and logs all parameter changes for audit.

Usage:
    from optimization.parameter_tuner import ParameterTuner

    tuner = ParameterTuner()
    best = tuner.run_optimization(data_df)
    per_session = tuner.optimize_per_session(data_df)
    tuner.apply_optimized_params()
"""

import os
import sys
import json
import logging
import itertools
from datetime import datetime, timezone

try:
    import numpy as np
except ImportError:
    np = None

try:
    import pandas as pd
except ImportError:
    pd = None

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from config import settings
except ImportError:
    settings = None

logger = logging.getLogger(__name__)


class ParameterTuner:
    """
    Walk-forward parameter optimizer with guardrails and per-session tuning.

    Stores tunable strategy parameters with defaults and allowed ranges,
    runs walk-forward optimization on rolling windows, outputs optimal
    ATR multipliers, RSI thresholds, and score weights per session,
    and applies guardrails so parameters can only shift +/-20% from defaults.
    """

    # Tunable parameters: name -> {default, min, max}
    TUNABLE_PARAMS = {
        "ATR_SL_MULTIPLIER": {"default": 1.5, "min": 1.0, "max": 2.5},
        "ATR_TP_MULTIPLIER": {"default": 3.0, "min": 2.0, "max": 5.0},
        "BREAKOUT_ATR_MULTIPLIER": {"default": 0.5, "min": 0.3, "max": 0.8},
        "ASIAN_RSI_OVERBOUGHT": {"default": 70, "min": 65, "max": 80},
        "ASIAN_RSI_OVERSOLD": {"default": 30, "min": 20, "max": 35},
        "MIN_CONFLUENCE_SCORE": {"default": 7, "min": 6, "max": 9},
        "VOLUME_CONFIRMATION_MULTIPLIER": {"default": 1.5, "min": 1.2, "max": 2.0},
    }

    # Session regime to hour ranges (UTC)
    SESSION_HOURS = {
        "MEAN_REVERT": (22, 8),    # Asian session hours
        "BREAKOUT": (8, 13),       # London session hours
        "MOMENTUM": (13, 17),      # New York session hours
    }

    # Grid steps for each parameter during optimization
    GRID_STEPS = {
        "ATR_SL_MULTIPLIER": [1.0, 1.25, 1.5, 1.75, 2.0, 2.5],
        "ATR_TP_MULTIPLIER": [2.0, 2.5, 3.0, 3.5, 4.0, 5.0],
        "BREAKOUT_ATR_MULTIPLIER": [0.3, 0.4, 0.5, 0.6, 0.8],
        "ASIAN_RSI_OVERBOUGHT": [65, 68, 70, 73, 75, 80],
        "ASIAN_RSI_OVERSOLD": [20, 25, 28, 30, 32, 35],
        "MIN_CONFLUENCE_SCORE": [6, 7, 8, 9],
        "VOLUME_CONFIRMATION_MULTIPLIER": [1.2, 1.4, 1.5, 1.7, 2.0],
    }

    def __init__(self, data_dir="optimization"):
        """
        Initialize the parameter tuner.

        Args:
            data_dir: Directory for storing optimization results and logs.
        """
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.optimal_params_path = os.path.join(self.data_dir, "optimal_params.json")
        self.change_log_path = os.path.join(self.data_dir, "param_change_log.jsonl")

    def run_optimization(self, data_df, train_months=3, test_months=1):
        """
        Run walk-forward optimization on the provided data.

        Splits data into rolling train/test windows (default 3 months train,
        1 month test). Grid-searches parameter combinations on train data using
        ATR barrier labeling (same approach as optimize_walkforward.py).
        Evaluates on test data using Sharpe-like score.

        Args:
            data_df: DataFrame with columns: time/datetime, open, high, low, close, atr.
            train_months: Number of months for in-sample training window.
            test_months: Number of months for out-of-sample test window.

        Returns:
            dict with keys: "best_params" (dict), "oos_sharpe" (float),
            "windows_evaluated" (int).
        """
        if train_months is None:
            train_months = getattr(settings, 'AUTO_TUNE_TRAIN_MONTHS', 3)
        if test_months is None:
            test_months = getattr(settings, 'AUTO_TUNE_TEST_MONTHS', 1)

        # Determine time column
        time_col = self._get_time_column(data_df)
        if time_col is None:
            logger.error("No time/datetime column found in data.")
            return {"best_params": {}, "oos_sharpe": -999.0, "windows_evaluated": 0}

        df = data_df.sort_values(time_col).reset_index(drop=True)

        # Build rolling windows based on month boundaries
        windows = self._build_rolling_windows(df, time_col, train_months, test_months)

        if not windows:
            logger.warning("Not enough data for walk-forward windows.")
            return {"best_params": {}, "oos_sharpe": -999.0, "windows_evaluated": 0}

        # Grid search parameters - use a subset focused on ATR params for speed
        search_params = ["ATR_SL_MULTIPLIER", "ATR_TP_MULTIPLIER", "MIN_CONFLUENCE_SCORE"]
        grid_values = [self.GRID_STEPS[p] for p in search_params]
        all_combos = list(itertools.product(*grid_values))

        # Track OOS performance per combo
        combo_oos_scores = {}

        for w_idx, (train_start, train_end, test_start, test_end) in enumerate(windows):
            df_train = df.iloc[train_start:train_end]
            df_test = df.iloc[test_start:test_end]

            best_is_score = -np.inf
            best_combo = None

            # Grid search on training data
            for combo in all_combos:
                params = dict(zip(search_params, combo))
                score = self._simulate_strategy(df_train, params)
                if score > best_is_score:
                    best_is_score = score
                    best_combo = params

            # Evaluate best IS combo on OOS data
            if best_combo:
                oos_score = self._simulate_strategy(df_test, best_combo)
                combo_key = str(sorted(best_combo.items()))
                if combo_key not in combo_oos_scores:
                    combo_oos_scores[combo_key] = []
                combo_oos_scores[combo_key].append(oos_score)

            logger.info(
                f"Window {w_idx + 1}/{len(windows)}: "
                f"IS={best_is_score:.3f}, OOS={oos_score:.3f}"
            )

        # Aggregate: find combo with best average OOS Sharpe
        if not combo_oos_scores:
            return {"best_params": {}, "oos_sharpe": -999.0, "windows_evaluated": len(windows)}

        best_key = max(combo_oos_scores, key=lambda k: np.mean(combo_oos_scores[k]))
        best_oos_sharpe = np.mean(combo_oos_scores[best_key])

        # Parse params from key
        best_params = dict(eval(best_key))

        # Fill in defaults for params not searched
        for param_name, param_info in self.TUNABLE_PARAMS.items():
            if param_name not in best_params:
                best_params[param_name] = param_info["default"]

        # Apply guardrails
        best_params = self._apply_guardrails(best_params)

        return {
            "best_params": best_params,
            "oos_sharpe": float(best_oos_sharpe),
            "windows_evaluated": len(windows),
        }

    def optimize_per_session(self, data_df):
        """
        Run separate optimization for each session regime.

        Filters data by session hours (using hour-based session detection)
        and runs optimization for MEAN_REVERT (Asian), BREAKOUT (London),
        and MOMENTUM (NY).

        Args:
            data_df: DataFrame with time column and OHLC + ATR data.

        Returns:
            dict: {"MEAN_REVERT": {...params}, "BREAKOUT": {...params}, "MOMENTUM": {...params}}
            Also saves results to optimization/optimal_params.json.
        """
        time_col = self._get_time_column(data_df)
        if time_col is None:
            logger.error("No time/datetime column found in data.")
            return {}

        df = data_df.copy()
        df['_hour'] = pd.to_datetime(df[time_col]).dt.hour

        results = {}

        for regime, (start_hour, end_hour) in self.SESSION_HOURS.items():
            # Filter data for this session's hours
            if start_hour > end_hour:
                # Wraps midnight (e.g., Asian: 22-8)
                session_mask = (df['_hour'] >= start_hour) | (df['_hour'] < end_hour)
            else:
                session_mask = (df['_hour'] >= start_hour) & (df['_hour'] < end_hour)

            session_df = df[session_mask].reset_index(drop=True)

            if len(session_df) < 100:
                logger.warning(
                    f"Insufficient data for {regime} session "
                    f"({len(session_df)} rows). Using defaults."
                )
                results[regime] = {
                    k: v["default"] for k, v in self.TUNABLE_PARAMS.items()
                }
                continue

            opt_result = self.run_optimization(session_df)

            if opt_result["oos_sharpe"] > getattr(settings, 'AUTO_TUNE_MIN_OOS_SHARPE', 0.5):
                results[regime] = opt_result["best_params"]
            else:
                logger.info(
                    f"{regime}: OOS Sharpe {opt_result['oos_sharpe']:.3f} below "
                    f"minimum threshold. Keeping defaults."
                )
                results[regime] = {
                    k: v["default"] for k, v in self.TUNABLE_PARAMS.items()
                }

        # Log parameter changes if we have previous params
        old_params = self._load_existing_params()
        for regime, new_params in results.items():
            old_regime_params = old_params.get(regime, {})
            if old_regime_params and old_regime_params != new_params:
                oos_sharpe = 0.0  # Default if not available
                self._log_parameter_change(
                    old_regime_params, new_params, regime, oos_sharpe
                )

        # Save results
        self._save_optimal_params(results)

        # Clean up temp column
        df.drop('_hour', axis=1, inplace=True, errors='ignore')

        return results

    def _apply_guardrails(self, proposed_params, default_params=None):
        """
        Apply guardrails: clip each parameter to within +/-20% of its default value.

        Uses the TUNABLE_PARAMS defaults unless default_params is provided.
        Logs a warning when a parameter hits the guardrail boundary.

        Args:
            proposed_params: Dict of parameter name -> proposed value.
            default_params: Optional dict of parameter name -> default value.
                           If None, uses TUNABLE_PARAMS defaults.

        Returns:
            Dict of clipped parameter values.
        """
        guardrail_pct = getattr(settings, 'AUTO_TUNE_GUARDRAIL_PCT', 0.2)
        clipped = {}

        for param_name, proposed_value in proposed_params.items():
            if default_params and param_name in default_params:
                default_val = default_params[param_name]
            elif param_name in self.TUNABLE_PARAMS:
                default_val = self.TUNABLE_PARAMS[param_name]["default"]
            else:
                # Unknown parameter, pass through
                clipped[param_name] = proposed_value
                continue

            lower_bound = default_val * (1.0 - guardrail_pct)
            upper_bound = default_val * (1.0 + guardrail_pct)

            # Also respect the absolute min/max from TUNABLE_PARAMS
            if param_name in self.TUNABLE_PARAMS:
                abs_min = self.TUNABLE_PARAMS[param_name]["min"]
                abs_max = self.TUNABLE_PARAMS[param_name]["max"]
                lower_bound = max(lower_bound, abs_min)
                upper_bound = min(upper_bound, abs_max)

            clipped_value = max(lower_bound, min(upper_bound, proposed_value))

            if clipped_value != proposed_value:
                logger.warning(
                    f"Guardrail hit for {param_name}: proposed={proposed_value}, "
                    f"clipped={clipped_value} (bounds=[{lower_bound:.4f}, {upper_bound:.4f}])"
                )

            clipped[param_name] = clipped_value

        return clipped

    def _log_parameter_change(self, old_params, new_params, regime, oos_sharpe):
        """
        Append parameter change record to the audit log.

        Each line is a JSON object with timestamp, regime, old values,
        new values, and OOS Sharpe that justified the change.

        Args:
            old_params: Previous parameter values.
            new_params: New parameter values being applied.
            regime: Session regime name (MEAN_REVERT, BREAKOUT, MOMENTUM).
            oos_sharpe: Out-of-sample Sharpe ratio for the new params.
        """
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "regime": regime,
            "old": old_params,
            "new": new_params,
            "oos_sharpe": float(oos_sharpe),
        }

        try:
            with open(self.change_log_path, 'a') as f:
                f.write(json.dumps(record) + "\n")
            logger.info(
                f"Logged parameter change for {regime} "
                f"(OOS Sharpe: {oos_sharpe:.3f})"
            )
        except OSError as e:
            logger.error(f"Failed to write parameter change log: {e}")

    def apply_optimized_params(self):
        """
        Read optimal parameters and apply them to runtime settings.

        Reads optimization/optimal_params.json and updates the relevant
        settings attributes at runtime (settings.ATR_SL_MULTIPLIER, etc).

        This should be called on a weekly schedule. Integration with a scheduler
        (e.g., APScheduler, cron, or the bot's internal event loop) is recommended.
        Example: schedule this method to run every Sunday at 00:00 UTC.

        Returns:
            dict: The applied parameters, or empty dict if file not found.
        """
        if not os.path.exists(self.optimal_params_path):
            logger.warning(
                f"No optimal params file found at {self.optimal_params_path}. "
                "Run optimize_per_session() first."
            )
            return {}

        try:
            with open(self.optimal_params_path, 'r') as f:
                params_by_regime = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load optimal params: {e}")
            return {}

        # Apply the aggregate best params (average across regimes for global settings)
        # For regime-specific params, the SessionStrategyService can read the file directly
        applied = {}
        all_param_values = {}

        for regime, params in params_by_regime.items():
            for param_name, value in params.items():
                if param_name not in all_param_values:
                    all_param_values[param_name] = []
                all_param_values[param_name].append(value)

        # Set global settings to the average of per-regime optimized values
        for param_name, values in all_param_values.items():
            avg_value = sum(values) / len(values)
            # Apply guardrails one more time
            guarded = self._apply_guardrails({param_name: avg_value})
            final_value = guarded[param_name]

            if settings is not None and hasattr(settings, param_name):
                setattr(settings, param_name, final_value)
                applied[param_name] = final_value
                logger.info(f"Applied {param_name} = {final_value:.4f}")

        return applied

    # ─── Private Helper Methods ──────────────────────────────────────────────

    def _get_time_column(self, df):
        """Find the time/datetime column in the DataFrame."""
        for col in ['time', 'datetime', 'date', 'timestamp']:
            if col in df.columns:
                return col
        return None

    def _build_rolling_windows(self, df, time_col, train_months, test_months):
        """
        Build rolling train/test window indices based on month boundaries.

        Returns list of tuples: (train_start_idx, train_end_idx, test_start_idx, test_end_idx)
        """
        total_months = train_months + test_months
        n = len(df)

        # Estimate bars per month from the data
        times = pd.to_datetime(df[time_col])
        total_days = (times.iloc[-1] - times.iloc[0]).days
        if total_days <= 0:
            return []

        bars_per_day = n / max(total_days, 1)
        bars_per_month = int(bars_per_day * 30)

        if bars_per_month < 50:
            bars_per_month = n // (total_months + 1)

        train_size = bars_per_month * train_months
        test_size = bars_per_month * test_months
        step_size = bars_per_month  # Slide by 1 month

        windows = []
        start = 0

        while start + train_size + test_size <= n:
            train_end = start + train_size
            test_end = min(train_end + test_size, n)
            windows.append((start, train_end, train_end, test_end))
            start += step_size

        return windows

    def _simulate_strategy(self, df_segment, params):
        """
        Simulate strategy on a data segment using ATR barrier labeling.

        Returns Sharpe-like score: mean(outcomes) / std(outcomes) * sqrt(N).
        Same approach as optimize_walkforward.py.

        Args:
            df_segment: DataFrame with close, high, low, atr columns.
            params: Dict with at least ATR_SL_MULTIPLIER, ATR_TP_MULTIPLIER.

        Returns:
            float: Sharpe-like score, or -999.0 if no valid trades.
        """
        sl_mult = params.get("ATR_SL_MULTIPLIER", 1.5)
        tp_mult = params.get("ATR_TP_MULTIPLIER", 3.0)
        horizon = 5  # 5-bar exit window (same as optimize_walkforward.py)

        closes = df_segment['close'].values
        highs = df_segment['high'].values
        lows = df_segment['low'].values

        if 'atr' in df_segment.columns:
            atrs = df_segment['atr'].values
        else:
            # Fallback: estimate ATR from high-low range
            atrs = (df_segment['high'] - df_segment['low']).rolling(14).mean().values

        labels = np.zeros(len(df_segment), dtype=int)

        for i in range(len(df_segment) - horizon):
            atr = atrs[i] if (not np.isnan(atrs[i]) and atrs[i] > 0) else 0.0005
            tp = closes[i] + atr * tp_mult
            sl = closes[i] - atr * sl_mult
            hit_tp = np.any(highs[i + 1: i + 1 + horizon] >= tp)
            hit_sl = np.any(lows[i + 1: i + 1 + horizon] <= sl)
            if hit_tp and not hit_sl:
                labels[i] = 1

        if labels.sum() == 0:
            return -999.0

        rr = tp_mult / sl_mult
        outcomes = np.where(labels == 1, rr, -1.0)
        std = outcomes.std()
        if std == 0:
            return -999.0

        return float((outcomes.mean() / std) * np.sqrt(len(outcomes)))

    def _load_existing_params(self):
        """Load previously saved optimal parameters, if any."""
        if os.path.exists(self.optimal_params_path):
            try:
                with open(self.optimal_params_path, 'r') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_optimal_params(self, params_by_regime):
        """Save optimal parameters to JSON file."""
        try:
            with open(self.optimal_params_path, 'w') as f:
                json.dump(params_by_regime, f, indent=2)
            logger.info(f"Saved optimal params to {self.optimal_params_path}")
        except OSError as e:
            logger.error(f"Failed to save optimal params: {e}")
