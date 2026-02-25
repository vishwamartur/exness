"""
Pre-Trade Trend Analysis with RAG Integration

This module provides advanced pre-trade analysis that:
1. Captures and analyzes market trends using AI/ML
2. Uses RAG to retrieve historical context
3. Makes intelligent entry decisions based on combined analysis
4. Prevents poor entry timing by ensuring trend alignment
"""

import numpy as np
import pandas as pd
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import MetaTrader5 as mt5

from config import settings
from market_data import loader
from strategy import features
from analysis.quant_agent import QuantAgent
from analysis.pattern_memory import get_pattern_memory
from analysis.market_analyst import MarketAnalyst


class PreTradeAnalyzer:
    """
    Advanced pre-trade analysis system combining trend analysis with RAG.
    """
    
    def __init__(self, quant_agent: QuantAgent, market_analyst: MarketAnalyst):
        self.quant = quant_agent
        self.analyst = market_analyst
        self.pattern_memory = get_pattern_memory()
        self.cache = {}  # Cache for recent analyses
        
    def analyze_entry_opportunity(self, symbol: str, direction: str, 
                                timeframe: str = "M15") -> Dict:
        """
        Comprehensive pre-trade analysis to determine if entry should proceed.
        
        Args:
            symbol: Trading symbol
            direction: BUY or SELL
            timeframe: Analysis timeframe (default M15)
            
        Returns:
            Dict with analysis results and entry recommendation
        """
        # Fetch multi-timeframe data
        data_dict = self._fetch_multi_timeframe_data(symbol)
        if not data_dict:
            return self._create_analysis_result(False, "No data available", {})
        
        # Get current market regime
        regime = self.analyst.get_regime(symbol, data_dict)
        
        # Analyze trend across multiple timeframes
        trend_analysis = self._analyze_multi_timeframe_trend(data_dict, direction)
        
        # Get ML predictions
        ml_analysis = self._get_ml_analysis(data_dict, symbol)
        
        # Get RAG context
        rag_context = self._get_rag_context(data_dict[timeframe], symbol, direction)
        
        # Get volatility assessment
        volatility_analysis = self._assess_volatility(data_dict[timeframe])
        
        # Get momentum indicators
        momentum_analysis = self._analyze_momentum(data_dict[timeframe])
        
        # Combine all factors for final decision
        final_decision = self._make_entry_decision(
            symbol=symbol,
            direction=direction,
            regime=regime,
            trend_analysis=trend_analysis,
            ml_analysis=ml_analysis,
            rag_context=rag_context,
            volatility_analysis=volatility_analysis,
            momentum_analysis=momentum_analysis
        )
        
        # Cache the analysis
        cache_key = f"{symbol}_{direction}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}"
        self.cache[cache_key] = final_decision
        
        return final_decision
    
    def _fetch_multi_timeframe_data(self, symbol: str) -> Optional[Dict]:
        """Fetch data for multiple timeframes."""
        timeframes = ["M1", "M5", "M15", "H1", "H4"]
        data_dict = {}
        
        for tf in timeframes:
            try:
                # Fetch more bars for better analysis
                bars = 200 if tf in ["H1", "H4"] else 100
                df = loader.get_historical_data(symbol, tf, bars)
                if df is not None and len(df) >= 50:
                    df = features.add_technical_features(df)
                    data_dict[tf] = df
            except Exception as e:
                print(f"[PRE-TRADE] Error fetching {tf} data for {symbol}: {e}")
                continue
                
        return data_dict if data_dict else None
    
    def _analyze_multi_timeframe_trend(self, data_dict: Dict, direction: str) -> Dict:
        """
        Analyze trend across multiple timeframes.
        Returns trend alignment score and details.
        """
        trend_scores = {}
        trend_directions = {}
        
        # Analyze each timeframe
        for timeframe, df in data_dict.items():
            if len(df) < 20:
                continue
                
            last_row = df.iloc[-1]
            
            # Calculate trend indicators
            sma_20 = df['close'].rolling(20).mean().iloc[-1]
            sma_50 = df['close'].rolling(50).mean().iloc[-1]
            current_price = df['close'].iloc[-1]
            
            # Trend direction
            if sma_20 > sma_50:
                trend_dir = "BUY"
            else:
                trend_dir = "SELL"
            
            trend_directions[timeframe] = trend_dir
            
            # Trend strength (0-1)
            strength = abs(sma_20 - sma_50) / sma_50
            trend_scores[timeframe] = min(strength, 1.0)
        
        # Calculate overall trend alignment
        alignment_score = 0
        total_weight = 0
        timeframe_weights = {"M1": 0.1, "M5": 0.2, "M15": 0.3, "H1": 0.25, "H4": 0.15}
        
        for tf in trend_scores:
            weight = timeframe_weights.get(tf, 0.2)
            if trend_directions[tf] == direction:
                alignment_score += trend_scores[tf] * weight
            else:
                alignment_score -= trend_scores[tf] * weight
            total_weight += weight
        
        if total_weight > 0:
            alignment_score = alignment_score / total_weight
        else:
            alignment_score = 0
            
        return {
            'alignment_score': alignment_score,
            'trend_directions': trend_directions,
            'trend_scores': trend_scores,
            'timeframe_consensus': len([d for d in trend_directions.values() if d == direction]) / len(trend_directions) if trend_directions else 0
        }
    
    def _get_ml_analysis(self, data_dict: Dict, symbol: str) -> Dict:
        """Get ML-based analysis for entry decision."""
        # Use M15 timeframe for ML analysis (good balance)
        df_m15 = data_dict.get("M15")
        if df_m15 is None:
            return {'confidence': 0.5, 'prediction': 0, 'reason': 'No M15 data'}
        
        try:
            # Get ML prediction
            ml_prob, ml_pred = self.quant._get_rf_prediction(df_m15, symbol)
            
            # Get XGBoost prediction if available
            xgb_prob = 0.5
            if hasattr(self.quant, 'xgb_model') and self.quant.xgb_model:
                xgb_prob, _ = self.quant._get_xgb_prediction(df_m15, symbol)
            
            # Ensemble probability
            ensemble_prob = (ml_prob + xgb_prob) / 2
            
            return {
                'rf_confidence': ml_prob,
                'xgb_confidence': xgb_prob,
                'ensemble_confidence': ensemble_prob,
                'prediction': ml_pred,
                'trend_alignment': 'aligned' if (ml_pred == 1 and direction == 'BUY') or (ml_pred == -1 and direction == 'SELL') else 'opposing'
            }
        except Exception as e:
            print(f"[PRE-TRADE] ML analysis error: {e}")
            return {'confidence': 0.5, 'prediction': 0, 'reason': f'ML error: {e}'}
    
    def _get_rag_context(self, df, symbol: str, direction: str) -> Dict:
        """Get RAG-based historical context."""
        try:
            context = self.pattern_memory.get_pattern_context(df, symbol, direction)
            return context
        except Exception as e:
            print(f"[PRE-TRADE] RAG context error: {e}")
            return {
                'similar_patterns': [],
                'historical_win_rate': 0.5,
                'avg_pnl': 0,
                'recommendation': 'NEUTRAL',
                'confidence_boost': 1.0,
                'context_text': f'RAG error: {e}'
            }
    
    def _assess_volatility(self, df) -> Dict:
        """Assess current market volatility."""
        if len(df) < 20 or 'atr' not in df.columns:
            return {'level': 'unknown', 'score': 0.5}
        
        last_row = df.iloc[-1]
        atr = last_row['atr']
        close_price = last_row['close']
        
        # Normalize ATR by price
        atr_pct = (atr / close_price) * 100
        
        # Volatility levels
        if atr_pct < 0.05:  # 0.05%
            level = 'low'
            score = 0.2
        elif atr_pct < 0.15:  # 0.15%
            level = 'medium'
            score = 0.5
        elif atr_pct < 0.3:  # 0.3%
            level = 'high'
            score = 0.8
        else:
            level = 'extreme'
            score = 1.0
            
        return {
            'level': level,
            'score': score,
            'atr_pct': atr_pct,
            'atr_value': atr
        }
    
    def _analyze_momentum(self, df) -> Dict:
        """Analyze price momentum indicators."""
        if len(df) < 14:
            return {'rsi': 50, 'macd_histogram': 0, 'momentum_score': 0.5}
        
        last_row = df.iloc[-1]
        
        # RSI analysis
        rsi = last_row.get('rsi', 50)
        if rsi > 70:
            rsi_condition = 'overbought'
            rsi_score = 0.3  # Avoid buying in overbought
        elif rsi < 30:
            rsi_condition = 'oversold'
            rsi_score = 0.3  # Avoid selling in oversold
        else:
            rsi_condition = 'neutral'
            rsi_score = 0.7
            
        # MACD analysis
        macd_hist = last_row.get('macd_histogram', 0)
        if macd_hist > 0:
            macd_condition = 'bullish'
            macd_score = 0.7 if direction == 'BUY' else 0.3
        else:
            macd_condition = 'bearish'
            macd_score = 0.3 if direction == 'BUY' else 0.7
            
        # Combined momentum score
        momentum_score = (rsi_score + macd_score) / 2
        
        return {
            'rsi': rsi,
            'rsi_condition': rsi_condition,
            'macd_histogram': macd_hist,
            'macd_condition': macd_condition,
            'momentum_score': momentum_score
        }
    
    def _make_entry_decision(self, symbol: str, direction: str, regime: str,
                           trend_analysis: Dict, ml_analysis: Dict, 
                           rag_context: Dict, volatility_analysis: Dict,
                           momentum_analysis: Dict) -> Dict:
        """
        Make final entry decision based on all analysis factors.
        """
        # Weight factors (adjustable)
        weights = {
            'trend_alignment': 0.30,
            'ml_confidence': 0.25,
            'rag_context': 0.20,
            'momentum': 0.15,
            'volatility': 0.10
        }
        
        # Calculate component scores
        scores = {}
        
        # Trend alignment score (-1 to 1) → convert to 0-1
        trend_score = max(0, (trend_analysis['alignment_score'] + 1) / 2)
        scores['trend_alignment'] = trend_score
        
        # ML confidence (0-1)
        ml_score = ml_analysis.get('ensemble_confidence', 0.5)
        scores['ml_confidence'] = ml_score
        
        # RAG context (0-1 with boost)
        rag_base_score = rag_context.get('historical_win_rate', 0.5)
        rag_boost = rag_context.get('confidence_boost', 1.0)
        rag_score = min(1.0, rag_base_score * rag_boost)
        scores['rag_context'] = rag_score
        
        # Momentum score (0-1)
        scores['momentum'] = momentum_analysis['momentum_score']
        
        # Volatility adjustment (lower score for extreme volatility)
        vol_score = 1.0 - volatility_analysis['score']  # Invert (lower is better)
        scores['volatility'] = vol_score
        
        # Calculate weighted score
        weighted_score = sum(scores[component] * weights[component] 
                           for component in weights)
        
        # Apply regime filter
        regime_multiplier = self._get_regime_multiplier(regime, direction)
        final_score = weighted_score * regime_multiplier
        
        # Determine entry recommendation
        if final_score >= 0.75:
            recommendation = 'STRONG_ENTRY'
            should_enter = True
        elif final_score >= 0.60:
            recommendation = 'ENTRY'
            should_enter = True
        elif final_score >= 0.45:
            recommendation = 'CAUTION'
            should_enter = False
        else:
            recommendation = 'AVOID'
            should_enter = False
            
        # Build detailed reasoning
        reasoning = self._build_reasoning(
            symbol, direction, regime, scores, weights, 
            trend_analysis, ml_analysis, rag_context, 
            volatility_analysis, momentum_analysis
        )
        
        return {
            'should_enter': should_enter,
            'recommendation': recommendation,
            'confidence_score': final_score,
            'component_scores': scores,
            'reasoning': reasoning,
            'regime': regime,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
    
    def _get_regime_multiplier(self, regime: str, direction: str) -> float:
        """Get regime-based adjustment multiplier."""
        if regime == "RISK_ON":
            return 1.1 if direction == "BUY" else 0.8
        elif regime == "RISK_OFF":
            return 0.8 if direction == "BUY" else 1.1
        else:  # NORMAL
            return 1.0
    
    def _build_reasoning(self, symbol: str, direction: str, regime: str,
                        scores: Dict, weights: Dict, trend_analysis: Dict,
                        ml_analysis: Dict, rag_context: Dict,
                        volatility_analysis: Dict, momentum_analysis: Dict) -> List[str]:
        """Build human-readable reasoning for the decision."""
        reasons = []
        
        # Trend analysis
        trend_score = scores['trend_alignment']
        if trend_score > 0.7:
            reasons.append(f"Strong trend alignment ({trend_score:.2f}) across {len(trend_analysis['trend_directions'])} timeframes")
        elif trend_score < 0.3:
            reasons.append(f"Poor trend alignment ({trend_score:.2f}) - multiple timeframes show opposite direction")
        else:
            reasons.append(f"Moderate trend alignment ({trend_score:.2f})")
        
        # ML analysis
        ml_conf = scores['ml_confidence']
        if ml_conf > 0.7:
            reasons.append(f"High ML confidence ({ml_conf:.2f}) for {direction}")
        elif ml_conf < 0.4:
            reasons.append(f"Low ML confidence ({ml_conf:.2f}) - model uncertain")
        else:
            reasons.append(f"Moderate ML confidence ({ml_conf:.2f})")
        
        # RAG context
        win_rate = rag_context.get('historical_win_rate', 0)
        if win_rate > 0.7:
            reasons.append(f"Strong historical performance ({win_rate*100:.1f}% win rate in similar patterns)")
        elif win_rate < 0.3:
            reasons.append(f"Poor historical performance ({win_rate*100:.1f}% win rate in similar patterns)")
        
        # Volatility
        vol_level = volatility_analysis['level']
        if vol_level == 'extreme':
            reasons.append(f"Extreme volatility detected - increased risk")
        elif vol_level == 'low':
            reasons.append(f"Low volatility - may limit profit potential")
        
        # Momentum
        momentum_score = scores['momentum']
        if momentum_score > 0.7:
            reasons.append(f"Strong momentum alignment")
        elif momentum_score < 0.3:
            reasons.append(f"Poor momentum alignment")
        
        # Regime context
        if regime != "NORMAL":
            reasons.append(f"Current market regime: {regime}")
        
        return reasons
    
    def _create_analysis_result(self, should_enter: bool, reason: str, 
                              details: Dict) -> Dict:
        """Create standardized analysis result."""
        return {
            'should_enter': should_enter,
            'recommendation': 'AVOID' if not should_enter else 'ENTRY',
            'confidence_score': 0.0 if not should_enter else 0.5,
            'component_scores': {},
            'reasoning': [reason],
            'regime': 'UNKNOWN',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'details': details
        }
    
    def get_recent_analysis(self, symbol: str, direction: str) -> Optional[Dict]:
        """Get cached recent analysis."""
        cache_key = f"{symbol}_{direction}"
        # Look for recent entries in cache (last 30 minutes)
        cutoff = datetime.now(timezone.utc).timestamp() - 1800  # 30 minutes
        
        for key, analysis in self.cache.items():
            if key.startswith(f"{symbol}_{direction}") and \
               datetime.fromisoformat(analysis['timestamp']).timestamp() > cutoff:
                return analysis
        return None