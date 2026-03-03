"""
HMM Regime Detector — Probabilistic Market Regime Classification
================================================================
Uses Gaussian Hidden Markov Models to identify market regimes from data,
replacing hardcoded threshold rules with learned state transitions.

The HMM learns 4 latent states from observables (returns, volatility, volume),
then maps them to actionable regime labels.

Falls back to rule-based detection if HMM fitting fails or insufficient data.
"""

import numpy as np
import pandas as pd
import warnings
from typing import Tuple, Dict

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
    """

    def __init__(self, n_states: int = 4, lookback: int = 200):
        self.n_states = n_states
        self.lookback = lookback
        self.model = None
        self._state_labels = {}  # Maps HMM state index → regime label
        self._fitted = False

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

            # Fit HMM on rolling window
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

            # Decode the most likely state sequence
            states = self.model.predict(obs)
            current_state = states[-1]

            # Map HMM states to regime labels using state characteristics
            self._map_states_to_regimes(obs, states)
            
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

            details = {
                "hmm": True,
                "state": int(current_state),
                "state_prob": float(np.max(state_probs)),
                "regime_confidence": float(np.max(state_probs)),
                "transition_probs": {
                    self._state_labels.get(i, f"state_{i}"): round(float(p), 3)
                    for i, p in enumerate(trans_probs)
                },
                "avg_return": round(float(avg_return), 6),
                "vol_level": round(float(obs[-1, 1]), 6),
                "adx": round(float(obs[-1, 3]), 1) if obs.shape[1] > 3 else 0,
            }

            self._fitted = True
            return regime, details

        except Exception as e:
            return "NORMAL", {"hmm": False, "reason": f"hmm_error: {str(e)[:60]}"}

    def _extract_observations(self, df: pd.DataFrame) -> np.ndarray:
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
        
        Strategy:
          - Compute mean volatility and mean |return| per state
          - Lowest vol state → RANGING
          - Highest vol state → VOLATILE_HIGH
          - Highest |return| with moderate vol → TRENDING
          - Remaining → NORMAL or REVERSAL
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

        # Sort states by volatility
        sorted_by_vol = sorted(state_stats.keys(), key=lambda s: state_stats[s]['mean_vol'])
        # Sort states by |returns|
        sorted_by_ret = sorted(state_stats.keys(), key=lambda s: state_stats[s]['mean_abs_ret'])
        # Sort states by ADX
        sorted_by_adx = sorted(state_stats.keys(), key=lambda s: state_stats[s]['mean_adx'])

        labels = {}

        # Lowest volatility state → RANGING
        labels[sorted_by_vol[0]] = "RANGING"

        # Highest volatility state → VOLATILE_HIGH
        labels[sorted_by_vol[-1]] = "VOLATILE_HIGH"

        # Highest ADX state (if not already assigned) → TRENDING
        for s in reversed(sorted_by_adx):
            if s not in labels:
                labels[s] = "TRENDING"
                break

        # Remaining states → NORMAL or BREAKOUT
        for s in range(self.n_states):
            if s not in labels:
                # If high volume ratio → potential breakout
                if state_stats[s]['mean_vol_ratio'] > 1.3:
                    labels[s] = "BREAKOUT"
                elif state_stats[s]['mean_abs_ret'] > state_stats[sorted_by_ret[1]]['mean_abs_ret']:
                    labels[s] = "REVERSAL"
                else:
                    labels[s] = "NORMAL"

        self._state_labels = labels

    def is_tradeable_regime(self, regime: str) -> bool:
        """
        Compatible interface with existing RegimeDetector.
        """
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
