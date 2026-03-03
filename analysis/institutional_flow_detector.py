"""
Institutional Flow Detector - Smart Money Tracking
====================================================
Detects when large institutional players (Jane Street, Citadel, Two Sigma, etc.)
are accumulating or distributing positions by analyzing:

1. Volume Anomalies — abnormal volume spikes signaling institutional entry
2. Absorption Candles — high volume + small body = stealth accumulation/distribution
3. Displacement Candles — large aggressive candles signaling institutional intent
4. Cumulative Volume Delta (CVD) — net buying vs selling pressure
5. Multi-Timeframe Flow Aggregation — higher TF confirmation
6. Institutional Footprint Score — composite score (0-100)
"""

import numpy as np
import pandas as pd
import logging
from config import settings

logger = logging.getLogger(__name__)


class InstitutionalFlowDetector:
    """
    Detects institutional (smart money) order flow patterns.
    Returns a composite score and directional bias.
    """

    def __init__(self):
        self.volume_zscore_threshold = getattr(settings, 'INST_FLOW_VOLUME_ZSCORE_THRESHOLD', 2.0)
        self.absorption_threshold = getattr(settings, 'INST_FLOW_ABSORPTION_THRESHOLD', 0.3)
        self.displacement_multiplier = getattr(settings, 'INST_FLOW_DISPLACEMENT_MULTIPLIER', 3.0)
        self.min_score = getattr(settings, 'INST_FLOW_MIN_SCORE', 60)
        self.block_score = getattr(settings, 'INST_FLOW_BLOCK_SCORE', 70)

    def analyze(self, symbol: str, data_dict: dict) -> dict:
        """
        Main analysis entry point.
        
        Args:
            symbol: Trading symbol (e.g. 'EURUSD')
            data_dict: Dict of timeframe -> DataFrame (must contain primary TF at minimum)
            
        Returns:
            dict with keys:
                - score: 0-100 institutional flow score
                - direction: 'BULLISH' / 'BEARISH' / 'NEUTRAL'
                - details: breakdown of individual signals
                - should_boost: True if score > min_score and aligned
        """
        try:
            # Get primary timeframe data
            primary_tf = getattr(settings, 'TIMEFRAME', 'M1')
            df = data_dict.get(primary_tf)
            if df is None:
                # Fallback: grab the first available DataFrame
                for key, val in data_dict.items():
                    if isinstance(val, pd.DataFrame) and len(val) > 50:
                        df = val
                        break

            if df is None or len(df) < 50:
                return self._empty_result("Insufficient data")

            if 'tick_volume' not in df.columns:
                return self._empty_result("No volume data")

            # --- Individual Signal Detection ---
            vol_anomalies = self._detect_volume_anomalies(df)
            absorption = self._detect_absorption(df)
            displacement = self._detect_displacement(df)
            cvd_signal = self._compute_cvd_signal(df)

            # --- Multi-TF Confirmation ---
            mtf_bias = self._multi_tf_flow(data_dict)

            # --- Compute Composite Score ---
            score, direction, breakdown = self._compute_institutional_score(
                vol_anomalies, absorption, displacement, cvd_signal, mtf_bias
            )

            result = {
                'score': score,
                'direction': direction,
                'should_boost': score >= self.min_score,
                'details': {
                    'volume_anomaly': vol_anomalies,
                    'absorption': absorption,
                    'displacement': displacement,
                    'cvd': cvd_signal,
                    'mtf_bias': mtf_bias,
                    'breakdown': breakdown,
                },
            }

            logger.debug(f"[{symbol}] Institutional Flow: score={score}, dir={direction}")
            return result

        except Exception as e:
            logger.error(f"[{symbol}] InstitutionalFlowDetector error: {e}")
            return self._empty_result(f"Error: {e}")

    # ─── Volume Anomaly Detection ────────────────────────────────────────────

    def _detect_volume_anomalies(self, df: pd.DataFrame) -> dict:
        """
        Detects abnormal volume spikes that signal institutional entry.
        Returns: dict with z-score, is_spike, spike_direction
        """
        vol = df['tick_volume'].values
        
        # Rolling statistics (50-bar window)
        vol_series = pd.Series(vol)
        vol_mean = vol_series.rolling(window=50, min_periods=10).mean()
        vol_std = vol_series.rolling(window=50, min_periods=10).std()
        
        # Z-score of current volume
        current_vol = vol[-1]
        mean_val = vol_mean.iloc[-1]
        std_val = vol_std.iloc[-1]
        
        if std_val > 0 and not np.isnan(std_val):
            zscore = (current_vol - mean_val) / std_val
        else:
            zscore = 0.0

        is_spike = abs(zscore) > self.volume_zscore_threshold

        # Count recent spikes (last 10 bars)
        recent_vol = vol[-10:]
        recent_mean = vol_mean.iloc[-10:].values
        recent_std = vol_std.iloc[-10:].values
        
        valid_mask = (recent_std > 0) & ~np.isnan(recent_std)
        if valid_mask.any():
            recent_zscores = np.where(
                valid_mask,
                (recent_vol - recent_mean) / np.where(recent_std > 0, recent_std, 1),
                0
            )
            spike_count = np.sum(np.abs(recent_zscores) > self.volume_zscore_threshold)
        else:
            spike_count = 0

        # Direction of volume spike: check if the spike candle was bullish or bearish
        last_candle_dir = 'BULLISH' if df['close'].iloc[-1] >= df['open'].iloc[-1] else 'BEARISH'

        return {
            'zscore': round(float(zscore), 2),
            'is_spike': bool(is_spike),
            'spike_count_10': int(spike_count),
            'candle_direction': last_candle_dir,
            'volume_ratio': round(float(current_vol / mean_val) if mean_val > 0 else 1.0, 2),
        }

    # ─── Absorption Candle Detection ─────────────────────────────────────────

    def _detect_absorption(self, df: pd.DataFrame) -> dict:
        """
        Detects absorption candles: high volume but small price movement.
        Institutions absorb opposing flow without moving price much.
        
        Signature: High tick_volume + small body relative to range.
        """
        # Body and range
        body = (df['close'] - df['open']).abs()
        candle_range = df['high'] - df['low']
        
        # Body-to-range ratio (< 0.3 = potential absorption)
        body_ratio = body / candle_range.replace(0, np.nan)
        body_ratio = body_ratio.fillna(1.0)
        
        # Volume relative to average
        vol_ratio = df['tick_volume'] / df['tick_volume'].rolling(window=20, min_periods=5).mean()
        vol_ratio = vol_ratio.fillna(1.0)
        
        # Absorption: high volume (>1.5x avg) + small body (<30% of range)
        is_absorption = (vol_ratio > 1.5) & (body_ratio < self.absorption_threshold)
        
        # Current bar analysis
        current_absorption = bool(is_absorption.iloc[-1])
        
        # Count recent absorption candles (last 20 bars)
        recent_count = int(is_absorption.iloc[-20:].sum())
        
        # Determine absorption bias: if absorbing at highs = bearish (distribution),
        # if absorbing at lows = bullish (accumulation)
        if current_absorption or recent_count >= 2:
            # Check where absorption is happening relative to recent range
            recent_high = df['high'].iloc[-20:].max()
            recent_low = df['low'].iloc[-20:].min()
            recent_range = recent_high - recent_low
            
            if recent_range > 0:
                price_position = (df['close'].iloc[-1] - recent_low) / recent_range
            else:
                price_position = 0.5
            
            # Absorbing at lows = accumulation (BULLISH), at highs = distribution (BEARISH)
            if price_position < 0.35:
                bias = 'BULLISH'  # Accumulation at lows
            elif price_position > 0.65:
                bias = 'BEARISH'  # Distribution at highs
            else:
                bias = 'NEUTRAL'
        else:
            bias = 'NEUTRAL'
            price_position = 0.5

        return {
            'is_absorption': current_absorption,
            'recent_count': recent_count,
            'bias': bias,
            'body_ratio': round(float(body_ratio.iloc[-1]), 3),
            'vol_ratio': round(float(vol_ratio.iloc[-1]), 2),
            'price_position': round(float(price_position), 2),
        }

    # ─── Displacement Candle Detection ───────────────────────────────────────

    def _detect_displacement(self, df: pd.DataFrame) -> dict:
        """
        Detects displacement candles: large aggressive body candles that signal
        institutional intent. These are candles with body > N * average body.
        """
        body = (df['close'] - df['open']).abs()
        avg_body = body.rolling(window=20, min_periods=5).mean()
        
        # Displacement: body > multiplier * average body
        body_multiple = body / avg_body.replace(0, np.nan)
        body_multiple = body_multiple.fillna(0)
        
        is_displacement = body_multiple > self.displacement_multiplier
        
        current_displacement = bool(is_displacement.iloc[-1])
        
        # Direction of displacement
        if current_displacement:
            direction = 'BULLISH' if df['close'].iloc[-1] > df['open'].iloc[-1] else 'BEARISH'
        else:
            direction = 'NEUTRAL'
        
        # Count recent displacements (last 10 bars) — clustering = strong intent
        recent_count = int(is_displacement.iloc[-10:].sum())
        
        # Recent displacement direction bias
        if recent_count > 0:
            recent_bodies = (df['close'].iloc[-10:] - df['open'].iloc[-10:])
            disp_mask = is_displacement.iloc[-10:]
            disp_bodies = recent_bodies[disp_mask]
            if len(disp_bodies) > 0:
                net_direction = disp_bodies.sum()
                direction = 'BULLISH' if net_direction > 0 else 'BEARISH'

        return {
            'is_displacement': current_displacement,
            'body_multiple': round(float(body_multiple.iloc[-1]), 2),
            'direction': direction,
            'recent_count': recent_count,
            'aggression_ratio': round(recent_count / 10.0, 2),
        }

    # ─── Cumulative Volume Delta (CVD) ───────────────────────────────────────

    def _compute_cvd_signal(self, df: pd.DataFrame) -> dict:
        """
        Computes Cumulative Volume Delta (CVD) — net buying vs selling pressure.
        
        Positive CVD = net buying pressure (institutional accumulation)
        Negative CVD = net selling pressure (institutional distribution)
        
        Also detects CVD-Price divergence (institutions positioning against retail).
        """
        # Signed volume: positive for bullish candles, negative for bearish
        candle_dir = np.where(df['close'].values >= df['open'].values, 1, -1)
        delta_vol = df['tick_volume'].values * candle_dir
        
        # CVD at different windows
        delta_series = pd.Series(delta_vol, index=df.index)
        cvd_20 = float(delta_series.iloc[-20:].sum())
        cvd_50 = float(delta_series.iloc[-50:].sum())
        cvd_100 = float(delta_series.iloc[-100:].sum()) if len(df) >= 100 else cvd_50
        
        # Normalize CVD by total volume in window for comparability
        total_vol_20 = float(df['tick_volume'].iloc[-20:].sum())
        total_vol_50 = float(df['tick_volume'].iloc[-50:].sum())
        
        cvd_ratio_20 = cvd_20 / total_vol_20 if total_vol_20 > 0 else 0
        cvd_ratio_50 = cvd_50 / total_vol_50 if total_vol_50 > 0 else 0
        
        # CVD Direction
        if cvd_ratio_20 > 0.15:
            cvd_direction = 'BULLISH'
        elif cvd_ratio_20 < -0.15:
            cvd_direction = 'BEARISH'
        else:
            cvd_direction = 'NEUTRAL'
        
        # CVD-Price Divergence Detection
        # Price going up but CVD going down = bearish divergence (distribution)
        # Price going down but CVD going up = bullish divergence (accumulation)
        price_change_20 = df['close'].iloc[-1] - df['close'].iloc[-20] if len(df) >= 20 else 0
        
        divergence = 'NONE'
        if price_change_20 > 0 and cvd_ratio_20 < -0.1:
            divergence = 'BEARISH'  # Price up, CVD down → distribution
        elif price_change_20 < 0 and cvd_ratio_20 > 0.1:
            divergence = 'BULLISH'  # Price down, CVD up → accumulation

        # CVD momentum: is CVD accelerating?
        if len(df) >= 50:
            cvd_first_half = float(delta_series.iloc[-50:-25].sum())
            cvd_second_half = float(delta_series.iloc[-25:].sum())
            cvd_accelerating = abs(cvd_second_half) > abs(cvd_first_half) * 1.2
        else:
            cvd_accelerating = False

        return {
            'cvd_20': round(cvd_ratio_20, 3),
            'cvd_50': round(cvd_ratio_50, 3),
            'direction': cvd_direction,
            'divergence': divergence,
            'accelerating': cvd_accelerating,
            'raw_cvd_20': round(cvd_20, 0),
        }

    # ─── Multi-Timeframe Flow ────────────────────────────────────────────────

    def _multi_tf_flow(self, data_dict: dict) -> dict:
        """
        Checks higher timeframes for institutional flow confirmation.
        If multiple timeframes show same directional flow, confidence increases.
        """
        directions = {}
        
        for tf_name in ['M5', 'H1', 'H4']:
            df = data_dict.get(tf_name)
            if df is None or not isinstance(df, pd.DataFrame) or len(df) < 20:
                continue
            if 'tick_volume' not in df.columns:
                continue
                
            # Quick CVD check on this timeframe
            candle_dir = np.where(df['close'].values >= df['open'].values, 1, -1)
            delta_vol = df['tick_volume'].values * candle_dir
            cvd = float(pd.Series(delta_vol).iloc[-20:].sum())
            total_vol = float(df['tick_volume'].iloc[-20:].sum())
            
            if total_vol > 0:
                ratio = cvd / total_vol
                if ratio > 0.15:
                    directions[tf_name] = 'BULLISH'
                elif ratio < -0.15:
                    directions[tf_name] = 'BEARISH'
                else:
                    directions[tf_name] = 'NEUTRAL'
            else:
                directions[tf_name] = 'NEUTRAL'

        # Aggregate: if majority agree, that's the MTF bias
        bull_count = sum(1 for d in directions.values() if d == 'BULLISH')
        bear_count = sum(1 for d in directions.values() if d == 'BEARISH')
        total = len(directions)

        if total == 0:
            return {'bias': 'NEUTRAL', 'confidence': 0, 'tf_directions': {}}

        if bull_count > bear_count and bull_count >= total * 0.5:
            bias = 'BULLISH'
            confidence = bull_count / total
        elif bear_count > bull_count and bear_count >= total * 0.5:
            bias = 'BEARISH'
            confidence = bear_count / total
        else:
            bias = 'NEUTRAL'
            confidence = 0

        return {
            'bias': bias,
            'confidence': round(confidence, 2),
            'tf_directions': directions,
        }

    # ─── Composite Institutional Score ───────────────────────────────────────

    def _compute_institutional_score(
        self, vol_anomalies, absorption, displacement, cvd_signal, mtf_bias
    ) -> tuple:
        """
        Computes composite institutional flow score (0-100).
        Returns: (score, direction, breakdown)
        """
        breakdown = {}
        score = 0

        # 1. Volume Anomaly Score (0-25 points)
        vol_score = 0
        if vol_anomalies['is_spike']:
            vol_score += 15
        vol_score += min(10, vol_anomalies['spike_count_10'] * 3)
        breakdown['volume'] = vol_score
        score += vol_score

        # 2. Absorption Score (0-25 points)
        abs_score = 0
        if absorption['is_absorption']:
            abs_score += 10
        abs_score += min(15, absorption['recent_count'] * 4)
        breakdown['absorption'] = abs_score
        score += abs_score

        # 3. Displacement Score (0-20 points)
        disp_score = 0
        if displacement['is_displacement']:
            disp_score += 12
        disp_score += min(8, displacement['recent_count'] * 3)
        breakdown['displacement'] = disp_score
        score += disp_score

        # 4. CVD Score (0-20 points)
        cvd_score = 0
        cvd_strength = abs(cvd_signal['cvd_20'])
        if cvd_strength > 0.3:
            cvd_score += 10
        elif cvd_strength > 0.15:
            cvd_score += 5
        
        if cvd_signal['divergence'] != 'NONE':
            cvd_score += 7  # Divergence is a strong institutional signal
        if cvd_signal['accelerating']:
            cvd_score += 3
        breakdown['cvd'] = cvd_score
        score += cvd_score

        # 5. Multi-TF Confirmation (0-10 points)
        mtf_score = 0
        if mtf_bias['confidence'] >= 0.5:
            mtf_score += 5
        if mtf_bias['confidence'] >= 0.8:
            mtf_score += 5
        breakdown['mtf'] = mtf_score
        score += mtf_score

        # Cap at 100
        score = min(100, score)

        # --- Determine Direction ---
        direction_votes = {
            'BULLISH': 0,
            'BEARISH': 0,
        }
        
        # Volume spike direction (weight: 1)
        if vol_anomalies['is_spike']:
            dir_key = vol_anomalies['candle_direction']
            if dir_key in direction_votes:
                direction_votes[dir_key] += 1

        # Absorption bias (weight: 2 — absorption is strong institutional signal)
        if absorption['bias'] != 'NEUTRAL':
            direction_votes[absorption['bias']] += 2

        # Displacement direction (weight: 2)
        if displacement['direction'] != 'NEUTRAL':
            direction_votes[displacement['direction']] += 2

        # CVD direction (weight: 3 — most reliable flow measure)
        if cvd_signal['direction'] != 'NEUTRAL':
            direction_votes[cvd_signal['direction']] += 3

        # CVD divergence (weight: 2 — advanced signal)
        if cvd_signal['divergence'] != 'NONE':
            direction_votes[cvd_signal['divergence']] += 2

        # MTF bias (weight: 2)
        if mtf_bias['bias'] != 'NEUTRAL':
            direction_votes[mtf_bias['bias']] += 2

        # Determine final direction
        if direction_votes['BULLISH'] > direction_votes['BEARISH'] + 2:
            direction = 'BULLISH'
        elif direction_votes['BEARISH'] > direction_votes['BULLISH'] + 2:
            direction = 'BEARISH'
        else:
            direction = 'NEUTRAL'

        breakdown['direction_votes'] = direction_votes

        return score, direction, breakdown

    # ─── Trade Filtering ─────────────────────────────────────────────────────

    def get_flow_direction(self, score_data: dict) -> str:
        """Returns institutional flow direction: 'BULLISH' / 'BEARISH' / 'NEUTRAL'."""
        return score_data.get('direction', 'NEUTRAL')

    def should_block_trade(self, trade_direction: str, score_data: dict) -> tuple:
        """
        Determines if a trade should be blocked because it goes against
        strong institutional flow.
        
        Args:
            trade_direction: 'BUY' or 'SELL'
            score_data: result from analyze()
            
        Returns:
            (should_block: bool, reason: str)
        """
        score = score_data.get('score', 0)
        flow_dir = score_data.get('direction', 'NEUTRAL')

        if score < self.block_score:
            return False, "Institutional flow too weak to determine"

        # Map trade direction to flow direction
        trade_flow = 'BULLISH' if trade_direction == 'BUY' else 'BEARISH'

        if flow_dir == 'NEUTRAL':
            return False, "Institutional flow is neutral"

        if flow_dir != trade_flow:
            return True, (
                f"Strong institutional {flow_dir} flow (score: {score}/100) "
                f"conflicts with {trade_direction} trade"
            )

        return False, f"Trade aligned with institutional {flow_dir} flow"

    def get_position_scale(self, trade_direction: str, score_data: dict) -> float:
        """
        Returns position scaling factor based on institutional alignment.
        1.0 = normal, 1.5 = very strong alignment.
        """
        score = score_data.get('score', 0)
        flow_dir = score_data.get('direction', 'NEUTRAL')
        trade_flow = 'BULLISH' if trade_direction == 'BUY' else 'BEARISH'

        if flow_dir == trade_flow:
            if score >= 80:
                return 1.5  # Very strong alignment — scale up
            elif score >= 60:
                return 1.2  # Good alignment — moderate scale
        
        return 1.0  # Default: no scaling

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _empty_result(self, reason: str) -> dict:
        return {
            'score': 0,
            'direction': 'NEUTRAL',
            'should_boost': False,
            'details': {'error': reason},
        }


# ─── Singleton ───────────────────────────────────────────────────────────────
_detector_instance = None

def get_institutional_flow_detector() -> InstitutionalFlowDetector:
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = InstitutionalFlowDetector()
    return _detector_instance
