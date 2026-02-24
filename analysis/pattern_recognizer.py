"""
Pattern Recognition Module
Detects chart patterns using technical analysis and simplified ML.
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional

class PatternRecognizer:
    """
    Recognizes chart patterns in price data.
    Uses technical analysis rules to detect common patterns.
    """
    
    def __init__(self):
        self.patterns = {
            'HEAD_AND_SHOULDERS': self._detect_head_and_shoulders,
            'DOUBLE_TOP': self._detect_double_top,
            'DOUBLE_BOTTOM': self._detect_double_bottom,
            'TRIANGLE_ASCENDING': self._detect_ascending_triangle,
            'TRIANGLE_DESCENDING': self._detect_descending_triangle,
            'FLAG_BULL': self._detect_bull_flag,
            'FLAG_BEAR': self._detect_bear_flag,
            'SUPPORT_BREAK': self._detect_support_break,
            'RESISTANCE_BREAK': self._detect_resistance_break
        }
    
    def analyze(self, df: pd.DataFrame) -> Dict:
        """
        Analyze DataFrame for all patterns.
        Returns: {'patterns': list, 'signals': dict, 'confidence': float}
        """
        if df is None or len(df) < 50:
            return {'patterns': [], 'signals': {}, 'confidence': 0}
        
        detected_patterns = []
        signals = {}
        
        for pattern_name, detector_func in self.patterns.items():
            try:
                detected, confidence, direction = detector_func(df)
                if detected:
                    detected_patterns.append({
                        'name': pattern_name,
                        'confidence': confidence,
                        'direction': direction
                    })
                    signals[pattern_name] = {'detected': True, 'confidence': confidence, 'direction': direction}
            except Exception as e:
                continue
        
        # Calculate overall pattern confidence
        avg_confidence = np.mean([p['confidence'] for p in detected_patterns]) if detected_patterns else 0
        
        return {
            'patterns': detected_patterns,
            'signals': signals,
            'confidence': round(avg_confidence, 3),
            'count': len(detected_patterns)
        }
    
    def _detect_head_and_shoulders(self, df: pd.DataFrame, lookback: int = 30) -> Tuple[bool, float, str]:
        """Detect Head and Shoulders pattern."""
        if len(df) < lookback:
            return False, 0, 'NEUTRAL'
        
        highs = df['high'].tail(lookback).values
        
        # Find local peaks
        peaks = []
        for i in range(2, len(highs) - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
                peaks.append((i, highs[i]))
        
        if len(peaks) < 3:
            return False, 0, 'NEUTRAL'
        
        # Check for H&S formation (middle peak higher than sides)
        for i in range(len(peaks) - 2):
            left_shoulder = peaks[i]
            head = peaks[i+1]
            right_shoulder = peaks[i+2]
            
            if head[1] > left_shoulder[1] and head[1] > right_shoulder[1]:
                # Check if shoulders are at similar levels (within 2%)
                shoulder_diff = abs(left_shoulder[1] - right_shoulder[1]) / left_shoulder[1]
                if shoulder_diff < 0.02:
                    confidence = 0.7 - shoulder_diff * 10  # Higher confidence if shoulders are equal
                    return True, round(confidence, 2), 'SELL'
        
        return False, 0, 'NEUTRAL'
    
    def _detect_double_top(self, df: pd.DataFrame, lookback: int = 30) -> Tuple[bool, float, str]:
        """Detect Double Top pattern."""
        if len(df) < lookback:
            return False, 0, 'NEUTRAL'
        
        highs = df['high'].tail(lookback).values
        
        # Find two similar peaks
        peaks = []
        for i in range(2, len(highs) - 2):
            if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
                peaks.append((i, highs[i]))
        
        if len(peaks) < 2:
            return False, 0, 'NEUTRAL'
        
        # Check for double top (two peaks at similar levels)
        for i in range(len(peaks) - 1):
            for j in range(i+1, len(peaks)):
                peak_diff = abs(peaks[i][1] - peaks[j][1]) / peaks[i][1]
                if peak_diff < 0.015:  # Within 1.5%
                    confidence = 0.75 - peak_diff * 10
                    return True, round(confidence, 2), 'SELL'
        
        return False, 0, 'NEUTRAL'
    
    def _detect_double_bottom(self, df: pd.DataFrame, lookback: int = 30) -> Tuple[bool, float, str]:
        """Detect Double Bottom pattern."""
        if len(df) < lookback:
            return False, 0, 'NEUTRAL'
        
        lows = df['low'].tail(lookback).values
        
        # Find two similar lows
        bottoms = []
        for i in range(2, len(lows) - 2):
            if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
                bottoms.append((i, lows[i]))
        
        if len(bottoms) < 2:
            return False, 0, 'NEUTRAL'
        
        # Check for double bottom
        for i in range(len(bottoms) - 1):
            for j in range(i+1, len(bottoms)):
                bottom_diff = abs(bottoms[i][1] - bottoms[j][1]) / bottoms[i][1]
                if bottom_diff < 0.015:
                    confidence = 0.75 - bottom_diff * 10
                    return True, round(confidence, 2), 'BUY'
        
        return False, 0, 'NEUTRAL'
    
    def _detect_ascending_triangle(self, df: pd.DataFrame, lookback: int = 25) -> Tuple[bool, float, str]:
        """Detect Ascending Triangle (bullish)."""
        if len(df) < lookback:
            return False, 0, 'NEUTRAL'
        
        highs = df['high'].tail(lookback).values
        lows = df['low'].tail(lookback).values
        
        # Check for flat resistance and rising support
        recent_highs = highs[-10:]
        recent_lows = lows[-10:]
        
        # Flat resistance (low variance in highs)
        high_variance = np.std(recent_highs) / np.mean(recent_highs)
        
        # Rising support (lows increasing)
        low_slope = np.polyfit(range(len(recent_lows)), recent_lows, 1)[0]
        
        if high_variance < 0.005 and low_slope > 0:
            confidence = min(0.8, 0.6 + low_slope * 1000)
            return True, round(confidence, 2), 'BUY'
        
        return False, 0, 'NEUTRAL'
    
    def _detect_descending_triangle(self, df: pd.DataFrame, lookback: int = 25) -> Tuple[bool, float, str]:
        """Detect Descending Triangle (bearish)."""
        if len(df) < lookback:
            return False, 0, 'NEUTRAL'
        
        highs = df['high'].tail(lookback).values
        lows = df['low'].tail(lookback).values
        
        recent_highs = highs[-10:]
        recent_lows = lows[-10:]
        
        # Flat support (low variance in lows)
        low_variance = np.std(recent_lows) / np.mean(recent_lows)
        
        # Falling resistance (highs decreasing)
        high_slope = np.polyfit(range(len(recent_highs)), recent_highs, 1)[0]
        
        if low_variance < 0.005 and high_slope < 0:
            confidence = min(0.8, 0.6 + abs(high_slope) * 1000)
            return True, round(confidence, 2), 'SELL'
        
        return False, 0, 'NEUTRAL'
    
    def _detect_bull_flag(self, df: pd.DataFrame, lookback: int = 20) -> Tuple[bool, float, str]:
        """Detect Bull Flag (continuation)."""
        if len(df) < lookback:
            return False, 0, 'NEUTRAL'
        
        closes = df['close'].tail(lookback).values
        
        # Strong move up followed by consolidation
        first_half = closes[:lookback//2]
        second_half = closes[lookback//2:]
        
        first_slope = np.polyfit(range(len(first_half)), first_half, 1)[0]
        second_slope = np.polyfit(range(len(second_half)), second_half, 1)[0]
        
        # Strong up move, then flat/slight down
        if first_slope > 0 and second_slope <= 0 and abs(second_slope) < abs(first_slope) * 0.3:
            confidence = 0.7
            return True, confidence, 'BUY'
        
        return False, 0, 'NEUTRAL'
    
    def _detect_bear_flag(self, df: pd.DataFrame, lookback: int = 20) -> Tuple[bool, float, str]:
        """Detect Bear Flag (continuation)."""
        if len(df) < lookback:
            return False, 0, 'NEUTRAL'
        
        closes = df['close'].tail(lookback).values
        
        first_half = closes[:lookback//2]
        second_half = closes[lookback//2:]
        
        first_slope = np.polyfit(range(len(first_half)), first_half, 1)[0]
        second_slope = np.polyfit(range(len(second_half)), second_half, 1)[0]
        
        # Strong down move, then flat/slight up
        if first_slope < 0 and second_slope >= 0 and abs(second_slope) < abs(first_slope) * 0.3:
            confidence = 0.7
            return True, confidence, 'SELL'
        
        return False, 0, 'NEUTRAL'
    
    def _detect_support_break(self, df: pd.DataFrame, lookback: int = 30) -> Tuple[bool, float, str]:
        """Detect Support Level Break."""
        if len(df) < lookback:
            return False, 0, 'NEUTRAL'
        
        lows = df['low'].tail(lookback).values
        close = df['close'].iloc[-1]
        
        # Find support level (multiple touches)
        support_level = np.percentile(lows, 10)
        touches = sum(1 for low in lows if abs(low - support_level) / support_level < 0.01)
        
        if touches >= 3 and close < support_level * 0.995:
            confidence = min(0.85, 0.6 + touches * 0.05)
            return True, round(confidence, 2), 'SELL'
        
        return False, 0, 'NEUTRAL'
    
    def _detect_resistance_break(self, df: pd.DataFrame, lookback: int = 30) -> Tuple[bool, float, str]:
        """Detect Resistance Level Break."""
        if len(df) < lookback:
            return False, 0, 'NEUTRAL'
        
        highs = df['high'].tail(lookback).values
        close = df['close'].iloc[-1]
        
        # Find resistance level (multiple touches)
        resistance_level = np.percentile(highs, 90)
        touches = sum(1 for high in highs if abs(high - resistance_level) / resistance_level < 0.01)
        
        if touches >= 3 and close > resistance_level * 1.005:
            confidence = min(0.85, 0.6 + touches * 0.05)
            return True, round(confidence, 2), 'BUY'
        
        return False, 0, 'NEUTRAL'
    
    def get_pattern_signal(self, analysis: Dict, trade_direction: str) -> Tuple[bool, float]:
        """
        Check if patterns support the trade direction.
        Returns: (should_trade, pattern_confidence)
        """
        patterns = analysis.get('patterns', [])
        
        if not patterns:
            return True, 0.5  # No patterns, neutral
        
        supporting_patterns = [
            p for p in patterns 
            if p['direction'] == trade_direction and p['confidence'] > 0.6
        ]
        
        contradicting_patterns = [
            p for p in patterns 
            if p['direction'] != trade_direction and p['confidence'] > 0.6
        ]
        
        # If strong contradicting pattern, don't trade
        if contradicting_patterns and max(p['confidence'] for p in contradicting_patterns) > 0.75:
            return False, 0
        
        # If supporting patterns, boost confidence
        if supporting_patterns:
            avg_confidence = np.mean([p['confidence'] for p in supporting_patterns])
            return True, avg_confidence
        
        return True, 0.5


# Singleton instance
_pattern_recognizer = None

def get_pattern_recognizer():
    global _pattern_recognizer
    if _pattern_recognizer is None:
        _pattern_recognizer = PatternRecognizer()
    return _pattern_recognizer
