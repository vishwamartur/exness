"""
Market Regime Detector
Classifies market into TRENDING, RANGING, or VOLATILE states using technical indicators.
"""
import pandas as pd
import numpy as np

class RegimeDetector:
    def __init__(self):
        pass

    def get_regime(self, df):
        """
        Analyzes the DataFrame to determine the current market regime.
        Returns: (regime_type: str, details: dict)
        
        Regimes:
        - TRENDING: Strong directional movement (ADX > 25).
        - RANGING:  Sideways movement (ADX < 20, Low volatility).
        - VOLATILE: High variance (ATR spike).
        - NORMAL:   Default state.
        """
        if df is None or len(df) < 50:
            return "NORMAL", {}

        last = df.iloc[-1]
        
        # Extract indicators (assumes df has features from features.py)
        adx = last.get('adx', 0)
        atr = last.get('atr', 0)
        atr_sma = df['atr'].rolling(window=20).mean().iloc[-1] if 'atr' in df else 0
        bb_width = last.get('bb_width', 0)
        
        details = {
            'adx': round(adx, 1),
            'bb_width': round(bb_width, 4),
            'vol_ratio': round(atr / atr_sma, 2) if atr_sma > 0 else 1.0
        }

        # 1. Check Volatility (Panic/Crash/News)
        # If current ATR is > 1.5x average ATR, it's highly volatile.
        if atr_sma > 0 and atr > 1.5 * atr_sma:
            return "VOLATILE", details

        # 2. Check Trending
        # ADX > 25 indicates a strong trend.
        if adx > 25:
            return "TRENDING", details

        # 3. Check Ranging
        # Low ADX and narrow Bollinger Bands
        # bb_width threshold depends on asset class, but < 0.002 (0.2%) is tight for forex.
        # Let's use relative percentile or fixed low ADX.
        if adx < 20:
            return "RANGING", details

        return "NORMAL", details
