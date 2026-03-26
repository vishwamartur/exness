"""
HMM Regime Detector — Probabilistic Market Regime Classification
================================================================
Uses Gaussian Hidden Markov Models to identify market regimes from data,
replacing hardcoded threshold rules with learned state transitions.

The HMM learns 4 latent states from observables (returns, volatility, volume),
then maps them to actionable regime labels.

Performance: fit() is expensive (iterative, n_iter=100) so it is cached and
only re-run when enough new data arrives or a time interval elapses.
predict() is cheap and called on every get_regime() invocation.

Falls back to rule-based detection if HMM fitting fails or insufficient data.
"""

import logging
import time as _time
import numpy as np
import pandas as pd
import warnings
from typing import Tuple, Dict, Optional

logger = logging.getLogger(__name__)

# Suppress hmmlearn convergence warnings which spam the console
logging.getLogger("hmmlearn").setLevel(logging.ERROR)
logging.getLogger("hmmlearn.base").setLevel(logging.ERROR)

try:
    from hmmlearn.hmm import GaussianHMM
    HMM_AVAILABLE = True
except ImportError:
    HMM_AVAILABLE = False


class HMMRegimeDetector:
    """
    Probabilistic market regime detection using Hidden Markov Models.
    
    Observes 4 features per bar:
      1. Log returns (direction + magnitude)
      2. Realized volatility (rolling std of returns)
      3. Volume ratio (current vs average)
      4. ADX (trend strength)
    
    Learns 4 hidden states that naturally correspond to:
      - Low-vol ranging
      - Trending (bull or bear, determined by return sign)
      - High volatility / breakout
      - Mean-reverting / transition
    
    Performance:
      - fit() is called at most once per `refit_interval` seconds (default 300s).
      - predict() is cheap — just a forward pass on the fitted model.
    """

    def __init__(self, n_states: int = 4, lookback: int = 200,
                 refit_interval: int = 300, min_new_bars: int = 10):
        self.n_states = n_states
        self.lookback = lookback
        self.model: Optional[GaussianHMM] = None
        self._state_labels: Dict[int, str] = {}
        self._fitted = False

        # ── Caching / throttle ─────────────────────────────────────────────
        self._refit_interval = refit_interval   # seconds between refits
        self._min_new_bars = min_new_bars       # min new bars before refit
        self._last_fit_ts: float = 0.0          # monotonic time of last fit()
        self._last_fit_len: int = 0             # len(obs) at last fit()
        self._last_obs: Optional[np.ndarray] = None  # cached observations
        self._total_bars_seen: int = 0          # cumulative bar counter
        self._bars_at_last_fit: int = 0         # _total_bars_seen at last fit

    # ===================================================================
    #  PUBLIC: get_regime  (cheap — uses cached model)
    # ===================================================================

    def get_regime(self, df: pd.DataFrame) -> Tuple[str, Dict]:
        """
        Classify the current market regime using HMM.
        
        Drop-in compatible with RegimeDetector.get_regime() interface.
        Returns: (regime_type: str, details: dict)
        """
        if df is None or len(df) < 60:
            return "NORMAL", {"hmm": False, "reason": "insufficient_data"}

        if not HMM_AVAILABLE:
            return "NORMAL", {"hmm": False, "reason": "hmmlearn_not_installed"}

        try:
            # Extract observable features
            obs = self._extract_observations(df)
            if obs is None or len(obs) < 60:
                return "NORMAL", {"hmm": False, "reason": "feature_extraction_failed"}

            # Track cumulative bars (len(df) grows as market moves)
            self._total_bars_seen = len(df)

            # Fit only when needed (time elapsed or enough new bars)
            self._maybe_fit(obs)

            if not self._fitted or self.model is None:
                return "NORMAL", {"hmm": False, "reason": "model_not_fitted"}

            # Predict is cheap — just a forward pass
            return self._predict(obs)

        except (ValueError, TypeError) as e:
            # Expected errors from data issues or shape mismatches
            return "NORMAL", {"hmm": False, "reason": f"hmm_data_error: {str(e)[:80]}"}
        except Exception as e:
            # Unexpected errors — log full trace for debugging
            logger.exception("HMM regime detection failed unexpectedly")
            return "NORMAL", {"hmm": False, "reason": f"hmm_error: {type(e).__name__}"}

    # ===================================================================
    #  INTERNAL: fit / predict split
    # ===================================================================

    def _maybe_fit(self, obs: np.ndarray) -> None:
        """
        Refit the HMM only when:
          1. Model has never been fitted, OR
          2. At least `_refit_interval` seconds have elapsed since last fit, OR
          3. At least `_min_new_bars` new bars have arrived (cumulative, not
             capped by lookback window).
        """
        now = _time.monotonic()
        # Use cumulative bar counter so the trigger works even when
        # _extract_observations truncates via tail(lookback)
        new_bars = self._total_bars_seen - self._bars_at_last_fit
        time_elapsed = now - self._last_fit_ts

        needs_refit = (
            not self._fitted or
            time_elapsed >= self._refit_interval or
            new_bars >= self._min_new_bars
        )

        if not needs_refit:
            return

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.model = GaussianHMM(
                n_components=self.n_states,
                covariance_type="full",
                n_iter=100,
                random_state=42,
                tol=0.01
            )
            self.model.fit(obs)

        # Map states after fitting
        states = self.model.predict(obs)
        self._map_states_to_regimes(obs, states)

        # Update cache timestamps
        self._last_fit_ts = now
        self._last_fit_len = len(obs)
        self._bars_at_last_fit = self._total_bars_seen
        self._last_obs = obs
        self._fitted = True

    def _predict(self, obs: np.ndarray) -> Tuple[str, Dict]:
        """
        Cheap prediction using the already-fitted model.
        Only does a forward pass — no fitting.
        """
        states = self.model.predict(obs)
        current_state = states[-1]

        regime = self._state_labels.get(current_state, "NORMAL")

        # Compute transition probabilities for the current state
        trans_probs = self.model.transmat_[current_state]

        # Get state probabilities for current observation
        state_probs = self.model.predict_proba(obs[-1:].reshape(1, -1))[0]

        # Determine direction for trending regimes
        recent_returns = obs[-5:, 0]  # Last 5 returns
        avg_return = np.mean(recent_returns)

        if regime == "TRENDING":
            regime = "TRENDING_BULL" if avg_return > 0 else "TRENDING_BEAR"
        elif regime == "BREAKOUT":
            regime = "BREAKOUT_BULL" if avg_return > 0 else "BREAKOUT_BEAR"
        elif regime == "REVERSAL":
            regime = "REVERSAL_BULL" if avg_return > 0 else "REVERSAL_BEAR"

        # Build transition_probs with unique keys (labels can collide across states)
        tp_dict = {}
        for i, p in enumerate(trans_probs):
            label = self._state_labels.get(i, f"state_{i}")
            key = label if label not in tp_dict else f"{label}_{i}"
            tp_dict[key] = round(float(p), 3)

        details = {
            "hmm": True,
            "state": int(current_state),
            "state_prob": float(np.max(state_probs)),
            "regime_confidence": float(np.max(state_probs)),
            "transition_probs": tp_dict,
            "avg_return": round(float(avg_return), 6),
            "vol_level": round(float(obs[-1, 1]), 6),
            "adx": round(float(obs[-1, 3]), 1) if obs.shape[1] > 3 else 0,
        }

        return regime, details

    # ===================================================================
    #  Feature extraction + state mapping
    # ===================================================================

    def _extract_observations(self, df: pd.DataFrame) -> Optional[np.ndarray]:
        """
        Extract the observation matrix for HMM from the DataFrame.
        Uses the last `lookback` bars.
        """
        # Use at most `lookback` bars
        data = df.tail(self.lookback).copy()

        # Feature 1: Log returns
        if 'log_ret' in data.columns:
            returns = data['log_ret'].values
        else:
            returns = np.log(data['close'] / data['close'].shift(1)).values

        # Feature 2: Realized volatility (rolling std of returns, window=10)
        ret_series = pd.Series(returns)
        vol = ret_series.rolling(window=10, min_periods=3).std().values

        # Feature 3: Volume ratio
        if 'tick_volume' in data.columns:
            vol_sma = data['tick_volume'].rolling(window=20, min_periods=5).mean()
            vol_ratio = (data['tick_volume'] / vol_sma.replace(0, np.nan)).values
        else:
            vol_ratio = np.ones(len(data))

        # Feature 4: ADX (trend strength)
        if 'adx' in data.columns:
            adx = data['adx'].values / 100.0  # Normalize to 0-1
        else:
            adx = np.full(len(data), 0.25)  # Default medium

        # Stack into observation matrix
        obs = np.column_stack([returns, vol, vol_ratio, adx])

        # Clean NaN/Inf
        obs = np.nan_to_num(obs, nan=0.0, posinf=0.0, neginf=0.0)

        # Remove leading rows with zeros (from rolling calculations)
        valid_start = 0
        for i in range(len(obs)):
            if obs[i, 1] != 0:  # First non-zero volatility
                valid_start = i
                break
        obs = obs[valid_start:]

        return obs if len(obs) >= 60 else None

    def _map_states_to_regimes(self, obs: np.ndarray, states: np.ndarray):
        """
        Map HMM state indices to human-readable regime labels based on
        the statistical characteristics of each state.
        """
        state_stats = {}
        for s in range(self.n_states):
            mask = states == s
            if mask.sum() < 3:
                state_stats[s] = {
                    'mean_vol': 0, 'mean_abs_ret': 0,
                    'mean_vol_ratio': 1, 'mean_adx': 0.25, 'count': 0
                }
                continue
            state_obs = obs[mask]
            state_stats[s] = {
                'mean_vol': np.mean(np.abs(state_obs[:, 1])),
                'mean_abs_ret': np.mean(np.abs(state_obs[:, 0])),
                'mean_vol_ratio': np.mean(state_obs[:, 2]),
                'mean_adx': np.mean(state_obs[:, 3]),
                'count': int(mask.sum())
            }

        sorted_by_vol = sorted(state_stats.keys(), key=lambda s: state_stats[s]['mean_vol'])
        sorted_by_ret = sorted(state_stats.keys(), key=lambda s: state_stats[s]['mean_abs_ret'])
        sorted_by_adx = sorted(state_stats.keys(), key=lambda s: state_stats[s]['mean_adx'])

        labels = {}
        labels[sorted_by_vol[0]] = "RANGING"
        labels[sorted_by_vol[-1]] = "VOLATILE_HIGH"

        for s in reversed(sorted_by_adx):
            if s not in labels:
                labels[s] = "TRENDING"
                break

        for s in range(self.n_states):
            if s not in labels:
                if state_stats[s]['mean_vol_ratio'] > 1.3:
                    labels[s] = "BREAKOUT"
                elif state_stats[s]['mean_abs_ret'] > state_stats[sorted_by_ret[1]]['mean_abs_ret']:
                    labels[s] = "REVERSAL"
                else:
                    labels[s] = "NORMAL"

        self._state_labels = labels

    def is_tradeable_regime(self, regime: str) -> bool:
        """Compatible interface with existing RegimeDetector."""
        good_regimes = [
            'TRENDING', 'TRENDING_BULL', 'TRENDING_BEAR',
            'BREAKOUT_BULL', 'BREAKOUT_BEAR',
            'NORMAL',
            'RANGING',
            'REVERSAL_BULL', 'REVERSAL_BEAR',
            'VOLATILE_LOW'
        ]
        return regime in good_regimes

    def get_regime_score(self, regime: str, direction: str) -> Tuple[int, str]:
        """
        Compatible interface with existing RegimeDetector.
        Score the regime for the given trade direction.
        Returns: (score: int 0-10, reason: str)
        """
        regime_direction_match = {
            'TRENDING_BULL': {'BUY': 10, 'SELL': 2},
            'TRENDING_BEAR': {'BUY': 2, 'SELL': 10},
            'BREAKOUT_BULL': {'BUY': 9, 'SELL': 1},
            'BREAKOUT_BEAR': {'BUY': 1, 'SELL': 9},
            'TRENDING': {'BUY': 7, 'SELL': 7},
            'NORMAL': {'BUY': 5, 'SELL': 5},
            'REVERSAL_BULL': {'BUY': 6, 'SELL': 3},
            'REVERSAL_BEAR': {'BUY': 3, 'SELL': 6},
            'VOLATILE_LOW': {'BUY': 5, 'SELL': 5},
            'RANGING': {'BUY': 3, 'SELL': 3},
            'VOLATILE_HIGH': {'BUY': 0, 'SELL': 0}
        }

        scores = regime_direction_match.get(regime, {'BUY': 5, 'SELL': 5})
        score = scores.get(direction, 5)

        reasons = {
            'TRENDING_BULL': 'HMM: Strong uptrend state detected',
            'TRENDING_BEAR': 'HMM: Strong downtrend state detected',
            'BREAKOUT_BULL': 'HMM: Bullish breakout state',
            'BREAKOUT_BEAR': 'HMM: Bearish breakout state',
            'RANGING': 'HMM: Low volatility / ranging state',
            'VOLATILE_HIGH': 'HMM: Extreme volatility state — AVOID',
            'VOLATILE_LOW': 'HMM: Compression state',
            'REVERSAL_BULL': 'HMM: Potential bullish reversal',
            'REVERSAL_BEAR': 'HMM: Potential bearish reversal',
        }

        return score, reasons.get(regime, 'HMM: Normal market conditions')
