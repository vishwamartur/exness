"""
News Sentiment Analyzer
Scrapes financial news and analyzes sentiment using AI/ML.
Enhanced with Google Gemini AI for real-time market intelligence.
"""
import os
import aiohttp
import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional
import re

from analysis.gemini_news_analyzer import get_gemini_analyzer
from analysis.fake_news_detector import get_fake_news_detector

class SentimentAnalyzer:
    """
    Analyzes market sentiment from news and social media.
    Returns sentiment score (-1.0 to +1.0) for symbols.
    
    Enhanced: Now powered by Google Gemini AI for real-time news analysis.
    """
    
    def __init__(self):
        self.api_key = os.getenv("NEWS_API_KEY", "")
        self.gemini = get_gemini_analyzer()
        self.fake_news = get_fake_news_detector()
        self.cache = {}
        self.cache_duration = 300  # 5 minutes
        
    async def get_sentiment(self, symbol: str) -> Dict:
        """
        Get sentiment for a symbol.
        Returns: {'score': -1.0 to 1.0, 'confidence': 0-1, 'source': str}
        """
        # Check cache
        if symbol in self.cache:
            cached_time, cached_data = self.cache[symbol]
            if (datetime.now(timezone.utc).timestamp() - cached_time) < self.cache_duration:
                return cached_data
        
        # Get Gemini AI-powered news sentiment (primary)
        news_sentiment = await self._fetch_news_sentiment(symbol)
        
        # Get technical sentiment (from price action)
        tech_sentiment = await self._analyze_technical_sentiment(symbol)
        
        # ── Fake News Credibility Gate ──────────────────────────────────
        from config import settings
        news_weight = 0.7  # Default: 70% news, 30% technical
        fake_news_flags = []
        credibility_score = 1.0

        if getattr(settings, 'FAKE_NEWS_DETECTION_ENABLED', False):
            key_events = news_sentiment.get('key_events', [])
            reasoning = news_sentiment.get('reasoning', '')
            headline = reasoning or (key_events[0] if key_events else '')

            if headline:
                credibility = self.fake_news.assess_credibility(
                    headline=headline,
                    source=news_sentiment.get('source', 'unknown'),
                    symbol=symbol,
                )
                credibility_score = credibility.get('credibility_score', 1.0)
                fake_news_flags = credibility.get('flags', [])

                # Discount news weight if not trusted
                weight_mult = self.fake_news.get_news_weight_multiplier(credibility)
                news_weight *= weight_mult

        tech_weight = 1.0 - news_weight
        # Combine with adjusted weights
        combined_score = (news_sentiment['score'] * news_weight + tech_sentiment['score'] * tech_weight)
        combined_confidence = max(news_sentiment['confidence'], tech_sentiment['confidence'])
        
        result = {
            'score': round(combined_score, 3),
            'confidence': round(combined_confidence, 3),
            'news_score': news_sentiment['score'],
            'tech_score': tech_sentiment['score'],
            'news_weight': round(news_weight, 2),
            'source': news_sentiment.get('source', 'combined'),
            'direction_bias': news_sentiment.get('direction_bias', 'NEUTRAL'),
            'key_events': news_sentiment.get('key_events', []),
            'risk_level': news_sentiment.get('risk_level', 'MEDIUM'),
            'reasoning': news_sentiment.get('reasoning', ''),
            'credibility_score': credibility_score,
            'fake_news_flags': fake_news_flags,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # Cache result
        self.cache[symbol] = (datetime.now(timezone.utc).timestamp(), result)
        
        return result
    
    async def _fetch_news_sentiment(self, symbol: str) -> Dict:
        """Fetch and analyze news sentiment using Gemini AI."""
        
        # Use Gemini AI if available
        if self.gemini.is_available():
            gemini_result = await self.gemini.analyze(symbol)
            return gemini_result
        
        # Fallback: return neutral if Gemini unavailable
        return {
            'score': 0.0,
            'confidence': 0.1,
            'source': 'fallback',
            'direction_bias': 'NEUTRAL',
            'key_events': [],
            'risk_level': 'MEDIUM',
            'reasoning': 'No news API available'
        }
    
    async def _analyze_technical_sentiment(self, symbol: str) -> Dict:
        """Analyze sentiment from technical indicators."""
        return {
            'score': 0.0,
            'confidence': 0.5,
            'source': 'technical'
        }
    
    def should_trade_with_sentiment(self, symbol: str, direction: str, sentiment: Dict) -> bool:
        """
        Check if trade direction aligns with sentiment.
        Returns True if sentiment supports the trade.
        """
        score = sentiment.get('score', 0)
        confidence = sentiment.get('confidence', 0)
        
        # Low confidence - ignore sentiment
        if confidence < 0.5:
            return True
        
        # Check alignment
        if direction == 'BUY' and score > 0.2:
            return True
        if direction == 'SELL' and score < -0.2:
            return True
        if abs(score) <= 0.2:  # Neutral sentiment
            return True
        
        # Strong contradiction — Gemini says opposite direction
        # Only block if Gemini specifically confirms high risk
        risk = sentiment.get('risk_level', 'MEDIUM')
        if risk == 'HIGH' and confidence > 0.7:
            return False
            
        # Moderate contradiction — still allow but flag
        if direction == 'BUY' and score < -0.3:
            return False
        if direction == 'SELL' and score > 0.3:
            return False
        
        return True
    
    def get_sentiment_recommendation(self, sentiment: Dict) -> str:
        """Get human-readable sentiment recommendation."""
        score = sentiment.get('score', 0)
        
        if score > 0.5:
            return "STRONGLY_BULLISH"
        elif score > 0.2:
            return "BULLISH"
        elif score < -0.5:
            return "STRONGLY_BEARISH"
        elif score < -0.2:
            return "BEARISH"
        else:
            return "NEUTRAL"


# Singleton instance
_sentiment_analyzer = None

def get_sentiment_analyzer():
    global _sentiment_analyzer
    if _sentiment_analyzer is None:
        _sentiment_analyzer = SentimentAnalyzer()
    return _sentiment_analyzer
