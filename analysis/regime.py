"""
Market Regime Detector - AI-Powered
Classifies market into detailed regimes using ML-style classification.
"""
import pandas as pd
import numpy as np

class RegimeDetector:
    def __init__(self):
        self.regime_history = []
        self.max_history = 100

    def get_regime(self, df):
        """
        AI-Powered Market Regime Classification.
        Returns: (regime_type: str, details: dict)
        
        Regimes:
        - TRENDING_BULL: Strong uptrend (ADX > 25, price > EMAs)
        - TRENDING_BEAR: Strong downtrend (ADX > 25, price < EMAs)
        - RANGING: Sideways/consolidation (ADX < 20, tight BB)
        - VOLATILE_HIGH: Extreme volatility (ATR spike > 2x)
        - VOLATILE_LOW: Low volatility (ATR compression)
        - BREAKOUT: Breaking out of range (volume + price expansion)
        - REVERSAL: Potential trend reversal (divergence detected)
        - NORMAL: Default state
        """
        if df is None or len(df) < 50:
            return "NORMAL", {}

        last = df.iloc[-1]
        prev = df.iloc[-5] if len(df) >= 5 else last
        
        # Extract indicators
        adx = last.get('adx', 0)
        atr = last.get('atr', 0)
        atr_sma = df['atr'].rolling(window=20).mean().iloc[-1] if 'atr' in df else 0
        bb_width = last.get('bb_width', 0)
        bb_pos = last.get('bb_pos', 0.5)
        rsi = last.get('rsi', 50)
        close = last.get('close', 0)
        ema_9 = last.get('ema_9', close)
        ema_21 = last.get('ema_21', close)
        sma_50 = last.get('sma_50', close)
        macd = last.get('macd', 0)
        macd_signal = last.get('macd_signal', 0)
        
        # Volume analysis (if available)
        volume = last.get('tick_volume', 0)
        vol_sma = df['tick_volume'].rolling(window=20).mean().iloc[-1] if 'tick_volume' in df else volume
        volume_spike = volume > (vol_sma * 1.5) if vol_sma > 0 else False
        
        # Calculate trend direction
        trend_up = close > ema_9 > ema_21 > sma_50
        trend_down = close < ema_9 < ema_21 < sma_50
        
        # Calculate momentum
        momentum = macd - macd_signal
        momentum_rising = momentum > 0 and (macd - macd_signal) > (prev.get('macd', 0) - prev.get('macd_signal', 0))
        momentum_falling = momentum < 0 and (macd - macd_signal) < (prev.get('macd', 0) - prev.get('macd_signal', 0))
        
        # RSI divergence detection
        price_higher = close > prev.get('close', close)
        rsi_lower = rsi < prev.get('rsi', rsi)
        bearish_div = price_higher and rsi_lower and rsi > 60
        
        price_lower = close < prev.get('close', close)
        rsi_higher = rsi > prev.get('rsi', rsi)
        bullish_div = price_lower and rsi_higher and rsi < 40
        
        # Volatility ratio
        vol_ratio = atr / atr_sma if atr_sma > 0 else 1.0
        
        details = {
            'adx': round(adx, 1),
            'bb_width': round(bb_width, 4),
            'bb_pos': round(bb_pos, 2),
            'vol_ratio': round(vol_ratio, 2),
            'rsi': round(rsi, 1),
            'trend_up': trend_up,
            'trend_down': trend_down,
            'momentum': round(momentum, 4),
            'volume_spike': volume_spike,
            'bullish_div': bullish_div,
            'bearish_div': bearish_div
        }

        # 1. Check Extreme Volatility (Skip trades)
        if vol_ratio > 2.0:
            return "VOLATILE_HIGH", details
            
        # 2. Check Low Volatility (Compression - potential breakout setup)
        if vol_ratio < 0.6 and bb_width < 0.01:
            return "VOLATILE_LOW", details

        # 3. Check Breakout (Volume + Price expansion)
        if volume_spike and vol_ratio > 1.3 and (bb_pos > 0.8 or bb_pos < 0.2):
            if bb_pos > 0.8 and trend_up:
                return "BREAKOUT_BULL", details
            elif bb_pos < 0.2 and trend_down:
                return "BREAKOUT_BEAR", details

        # 4. Check Trending with Direction
        if adx > 25:
            if trend_up and momentum > 0:
                return "TRENDING_BULL", details
            elif trend_down and momentum < 0:
                return "TRENDING_BEAR", details
            else:
                return "TRENDING", details

        # 5. Check Reversal Signals
        if bearish_div and adx > 20:
            return "REVERSAL_BEAR", details
        if bullish_div and adx > 20:
            return "REVERSAL_BULL", details

        # 6. Check Ranging (Sideways)
        if adx < 20 and bb_width < 0.015:
            return "RANGING", details

        return "NORMAL", details
    
    def is_tradeable_regime(self, regime):
        """
        Returns True if the regime is suitable for trading.
        Skip ranging and highly volatile markets.
        """
        good_regimes = [
            'TRENDING', 'TRENDING_BULL', 'TRENDING_BEAR',
            'BREAKOUT_BULL', 'BREAKOUT_BEAR',
            'NORMAL'
        ]
        return regime in good_regimes
    
    def get_regime_score(self, regime, direction):
        """
        Score the regime for the given trade direction.
        Returns: score (0-10), reason
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
            'VOLATILE_LOW': {'BUY': 4, 'SELL': 4},
            'RANGING': {'BUY': 1, 'SELL': 1},
            'VOLATILE_HIGH': {'BUY': 0, 'SELL': 0}
        }
        
        scores = regime_direction_match.get(regime, {'BUY': 5, 'SELL': 5})
        score = scores.get(direction, 5)
        
        reasons = {
            'TRENDING_BULL': 'Strong uptrend - good for LONGS',
            'TRENDING_BEAR': 'Strong downtrend - good for SHORTS',
            'BREAKOUT_BULL': 'Bullish breakout confirmed',
            'BREAKOUT_BEAR': 'Bearish breakout confirmed',
            'RANGING': 'Sideways market - AVOID',
            'VOLATILE_HIGH': 'Too volatile - AVOID',
            'VOLATILE_LOW': 'Low volatility - wait for expansion'
        }
        
        return score, reasons.get(regime, 'Normal market conditions')
