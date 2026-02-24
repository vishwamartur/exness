"""
News Sentiment Analyzer
Scrapes financial news and analyzes sentiment using AI/ML.
"""
import os
import aiohttp
import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional
import re

class SentimentAnalyzer:
    """
    Analyzes market sentiment from news and social media.
    Returns sentiment score (-1.0 to +1.0) for symbols.
    """
    
    def __init__(self):
        self.api_key = os.getenv("NEWS_API_KEY", "")
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
        
        # Get news sentiment
        news_sentiment = await self._fetch_news_sentiment(symbol)
        
        # Get technical sentiment (from price action)
        tech_sentiment = await self._analyze_technical_sentiment(symbol)
        
        # Combine
        combined_score = (news_sentiment['score'] * 0.6 + tech_sentiment['score'] * 0.4)
        combined_confidence = max(news_sentiment['confidence'], tech_sentiment['confidence'])
        
        result = {
            'score': round(combined_score, 3),
            'confidence': round(combined_confidence, 3),
            'news_score': news_sentiment['score'],
            'tech_score': tech_sentiment['score'],
            'source': 'combined',
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        # Cache result
        self.cache[symbol] = (datetime.now(timezone.utc).timestamp(), result)
        
        return result
    
    async def _fetch_news_sentiment(self, symbol: str) -> Dict:
        """Fetch and analyze news sentiment."""
        # Simplified implementation - can be enhanced with real news API
        # For now, return neutral sentiment
        
        # Map forex/crypto to keywords
        keywords = {
            'EURUSD': 'EUR USD euro dollar forex',
            'GBPUSD': 'GBP USD pound dollar forex',
            'USDJPY': 'USD JPY dollar yen forex',
            'BTCUSD': 'BTC bitcoin crypto cryptocurrency',
            'ETHUSD': 'ETH ethereum crypto cryptocurrency',
            'XAUUSD': 'XAU gold precious metals',
            'USOIL': 'oil crude petroleum WTI'
        }
        
        # Default to symbol name
        keyword = keywords.get(symbol, symbol)
        
        # Placeholder for news API integration
        # In production, integrate with:
        # - NewsAPI.org
        # - Finnhub
        # - Twitter/X API
        # - Reddit API
        
        return {
            'score': 0.0,  # Neutral
            'confidence': 0.3,
            'source': 'news_placeholder'
        }
    
    async def _analyze_technical_sentiment(self, symbol: str) -> Dict:
        """Analyze sentiment from technical indicators."""
        # This would integrate with your existing technical analysis
        # For now, return neutral
        
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
        
        # Sentiment contradicts trade direction
        return False
    
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
