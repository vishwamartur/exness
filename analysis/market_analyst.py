
from analysis.llm_advisor import get_advisor
from analysis.regime import RegimeDetector
from utils.news_filter import is_news_blackout, get_active_events
from utils.shared_state import SharedState

class MarketAnalyst:
    """
    The 'Fundamentalist' Agent.
    Responsibilities:
    1. AI Market Analysis (Mistral/Gemini)
    2. Regime Detection (Volatile/Trending/Ranging)
    3. News Event Filtering
    """
    def __init__(self):
        self.mistral = get_advisor()
        self.regime_detector = RegimeDetector()
        self.state = SharedState()
        print("[AGENT] MarketAnalyst initialized.")

    def check_news(self, symbol):
        """Fast check for news blackout."""
        return is_news_blackout(symbol)

    def analyze_session(self, symbol, df_features):
        """
        Full market diagnosis.
        Returns: {
            'regime': str,
            'sentiment': str,
            'confidence': int,
            'news_blocked': bool,
            'reason': str
        }
        """
        # 1. News Check
        is_blocked, event = is_news_blackout(symbol)
        if is_blocked:
            return {
                'regime': 'NEWS_EVENT',
                'news_blocked': True,
                'reason': f"News Blackout: {event}",
                'sentiment': 'NEUTRAL',
                'confidence': 0
            }

        # 2. Regime Detection
        regime, details = self.regime_detector.get_regime(df_features)
        
        # Persist to Shared Memory
        try:
            self.state.set(f"regime_{symbol}", {
                "regime": regime,
                "details": details,
                "updated": str(df_features.index[-1])
            })
        except: pass

        # 3. AI Analysis (only if regime is tradeable)
        sentiment = "NEUTRAL"
        confidence = 0
        reason = "No AI analysis"
        
        return {
            'regime': regime,
            'regime_details': details,
            'news_blocked': False,
            'reason': "Market Open",
            'sentiment': sentiment,
            'confidence': confidence
        }

    async def get_ai_opinion(self, symbol, timeframe, indicators):
        """Standardized AI Opinion Request (Async)."""
        try:
            sentiment, confidence, reason = await self.mistral.analyze_market(symbol, timeframe, indicators)
            return sentiment, confidence, reason
        except Exception as e:
            print(f"[ANALYST] AI failed: {e}")
            return "NEUTRAL", 0, "AI Error"
