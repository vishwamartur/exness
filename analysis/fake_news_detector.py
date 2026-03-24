"""
Fake News Detection Engine for Financial Markets
=================================================
Evaluates incoming financial news for credibility before it influences
trading decisions. Uses 5 heuristic signals:

1. Source Reputation      (25%) — Tiered whitelist/blacklist of news sources
2. Cross-Source Corroboration (25%) — Multiple sources reporting same event
3. Gemini AI Verification (20%) — LLM plausibility check
4. Linguistic Red Flags   (15%) — Clickbait, sensationalism, ALL-CAPS detection
5. Temporal Consistency   (15%) — Event timing vs business hours/calendars

Outputs a credibility score (0.0–1.0). News below the configured threshold
is flagged as potentially fake and discounted from trading decisions.
"""

import re
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

import os

# ─── Source Reputation Tiers ─────────────────────────────────────────────────

# Tier 1: Institutional-grade financial wire services
TIER_1_SOURCES = {
    "reuters", "bloomberg", "associated press", "ap news", "dow jones",
    "financial times", "wall street journal", "wsj", "cnbc", "bbc",
    "the economist", "federal reserve", "ecb", "bank of england",
    "bank of japan", "reserve bank of australia", "imf",
    "world bank", "bis", "bureau of labor statistics",
}

# Tier 2: Reputable financial media
TIER_2_SOURCES = {
    "marketwatch", "investing.com", "forexlive", "fxstreet", "dailyfx",
    "yahoo finance", "google finance", "barrons", "morningstar",
    "seeking alpha", "the motley fool", "zerohedge", "kitco",
    "coindesk", "cointelegraph", "the block", "decrypt",
    "benzinga", "thestreet", "fortune", "forbes",
}

# Tier 3: Social / unverified (high fake-news risk)
TIER_3_SOURCES = {
    "twitter", "x.com", "reddit", "telegram", "discord",
    "tiktok", "youtube", "facebook", "instagram", "threads",
    "4chan", "truth social", "rumble",
}

# ─── Linguistic Red Flag Patterns ────────────────────────────────────────────

# Patterns that should be case-insensitive
_CLICKBAIT_CI = [
    re.compile(r"\b(BREAKING|URGENT|SHOCKING|BOMBSHELL|EXPLOSIVE|LEAKED)\b", re.IGNORECASE),
    re.compile(r"\b(you won't believe|this changes everything|markets in chaos)\b", re.IGNORECASE),
    re.compile(r"\b(to the moon|guaranteed|100%|1000x|infinite|free money)\b", re.IGNORECASE),
    re.compile(r"\b(insider info|secret|confidential|anonymous source says)\b", re.IGNORECASE),
    re.compile(r"\b(CRASH|COLLAPSE|moon|lambo|pump|dump|rug ?pull)\b", re.IGNORECASE),
    re.compile(r"\$\d+[KkMmBb]\+?\b", re.IGNORECASE),
]

# Patterns that MUST be case-sensitive (ALL-CAPS detection, punctuation)
_CLICKBAIT_CS = [
    re.compile(r"[!?]{3,}"),                  # Excessive punctuation
    re.compile(r"(?:[A-Z]{3,}\s+){3,}"),       # 3+ consecutive ALL-CAPS words
]

_CLICKBAIT_RE = _CLICKBAIT_CI + _CLICKBAIT_CS


class FakeNewsDetector:
    """
    Multi-signal credibility scorer for financial news.
    """

    # Signal weights (must sum to 1.0)
    WEIGHTS = {
        "source_reputation": 0.25,
        "cross_source": 0.25,
        "gemini_verification": 0.20,
        "linguistic": 0.15,
        "temporal": 0.15,
    }

    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY", "")
        self.client = None
        self.model_id = "gemini-2.0-flash"
        self._gemini_ready = False

        if GEMINI_AVAILABLE and self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
                self._gemini_ready = True
            except Exception:
                pass

        # Recent headlines cache for cross-source checks
        # Format: {normalised_topic: [(source, timestamp), ...]}
        self._recent_headlines: Dict[str, List[tuple]] = {}
        self._cache_ttl = 3600  # 1 hour

        print(f"[FAKE-NEWS] Detector initialized (Gemini: {'ON' if self._gemini_ready else 'OFF'})")

    # ─── Public API ──────────────────────────────────────────────────────────

    def assess_credibility(
        self,
        headline: str,
        source: str = "unknown",
        timestamp: Optional[datetime] = None,
        symbol: str = "",
        related_headlines: Optional[List[str]] = None,
    ) -> Dict:
        """
        Assess the credibility of a financial news headline.

        Args:
            headline:           The news headline / claim text
            source:             Source name (e.g. "Reuters", "Twitter")
            timestamp:          When the news was published (UTC)
            symbol:             Trading symbol for context (e.g. "EURUSD")
            related_headlines:  Other headlines from different sources on the same topic

        Returns:
            Dict with credibility_score (0-1), signal_scores, is_trusted, flags
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)

        # 1. Source reputation
        source_score, source_tier = self._check_source_reputation(source)

        # 2. Cross-source corroboration
        cross_score = self._check_cross_source(headline, source, related_headlines)

        # 3. Linguistic red flags
        ling_score, ling_flags = self._check_linguistic_red_flags(headline)

        # 4. Temporal consistency
        temporal_score, temporal_flags = self._check_temporal_consistency(timestamp)

        # 5. Gemini AI verification (only for suspicious content)
        preliminary_score = (
            source_score * self.WEIGHTS["source_reputation"]
            + cross_score * self.WEIGHTS["cross_source"]
            + ling_score * self.WEIGHTS["linguistic"]
            + temporal_score * self.WEIGHTS["temporal"]
        )
        # Only call Gemini if the preliminary score is borderline or low
        # to save API quota
        if self._gemini_ready and preliminary_score < 0.7:
            gemini_score = self._verify_with_gemini(headline, symbol)
        else:
            gemini_score = 0.7  # Default neutral-positive when skipped

        # ─── Weighted Score ──────────────────────────────────────────────
        credibility_score = (
            source_score * self.WEIGHTS["source_reputation"]
            + cross_score * self.WEIGHTS["cross_source"]
            + gemini_score * self.WEIGHTS["gemini_verification"]
            + ling_score * self.WEIGHTS["linguistic"]
            + temporal_score * self.WEIGHTS["temporal"]
        )
        credibility_score = round(max(0.0, min(1.0, credibility_score)), 3)

        # Collect all flags
        all_flags = []
        if source_tier == 3:
            all_flags.append(f"Unverified source: {source}")
        all_flags.extend(ling_flags)
        all_flags.extend(temporal_flags)

        # Determine trust
        from config import settings
        threshold = getattr(settings, "FAKE_NEWS_MIN_CREDIBILITY", 0.4)
        is_trusted = credibility_score >= threshold

        result = {
            "credibility_score": credibility_score,
            "is_trusted": is_trusted,
            "signal_scores": {
                "source_reputation": round(source_score, 3),
                "cross_source": round(cross_score, 3),
                "gemini_verification": round(gemini_score, 3),
                "linguistic": round(ling_score, 3),
                "temporal": round(temporal_score, 3),
            },
            "source_tier": source_tier,
            "flags": all_flags,
            "headline": headline[:120],
            "source": source,
            "timestamp": timestamp.isoformat(),
        }

        # Log
        status = "TRUSTED" if is_trusted else "SUSPICIOUS"
        print(
            f"[FAKE-NEWS] {status} ({credibility_score:.2f}) | "
            f"Src:{source_score:.1f} Cross:{cross_score:.1f} "
            f"Gemini:{gemini_score:.1f} Ling:{ling_score:.1f} "
            f"Temp:{temporal_score:.1f} | {headline[:60]}"
        )

        return result

    def should_trust_news(self, credibility_result: Dict) -> bool:
        """Simple boolean gate for the trading pipeline."""
        return credibility_result.get("is_trusted", True)

    def get_news_weight_multiplier(self, credibility_result: Dict) -> float:
        """
        Returns a multiplier (0.0–1.0) to apply to news sentiment weight.
        Trusted news → 1.0, suspicious news → discount factor from settings.
        """
        if self.should_trust_news(credibility_result):
            return 1.0
        from config import settings
        return getattr(settings, "FAKE_NEWS_DISCOUNT_FACTOR", 0.1)

    # ─── Signal Checkers ─────────────────────────────────────────────────────

    def _check_source_reputation(self, source: str) -> tuple:
        """
        Returns (score: 0-1, tier: 1/2/3/4).
        Tier 1 = institutional, Tier 2 = reputable, Tier 3 = social, Tier 4 = unknown.
        """
        src_lower = source.lower().strip()

        if any(t1 in src_lower for t1 in TIER_1_SOURCES):
            return 1.0, 1
        if any(t2 in src_lower for t2 in TIER_2_SOURCES):
            return 0.75, 2
        if any(t3 in src_lower for t3 in TIER_3_SOURCES):
            return 0.2, 3

        # Unknown source
        return 0.4, 4

    def _check_cross_source(
        self, headline: str, source: str, related_headlines: Optional[List[str]] = None
    ) -> float:
        """
        Check if the same topic is reported by multiple sources.
        More corroboration → higher score.
        """
        # Normalise topic key from headline (simple: first 5 significant words)
        topic_key = self._normalise_topic(headline)

        # Register this headline
        now = time.time()
        if topic_key not in self._recent_headlines:
            self._recent_headlines[topic_key] = []

        # Clean old entries
        self._recent_headlines[topic_key] = [
            (s, t) for s, t in self._recent_headlines[topic_key]
            if now - t < self._cache_ttl
        ]

        # Add current
        self._recent_headlines[topic_key].append((source.lower(), now))

        # Count unique sources
        unique_sources = len(set(
            s for s, _ in self._recent_headlines[topic_key]
        ))

        # Also count explicitly provided related headlines
        if related_headlines:
            unique_sources += min(len(related_headlines), 3)

        # Score based on source count
        if unique_sources >= 4:
            return 1.0
        elif unique_sources >= 3:
            return 0.85
        elif unique_sources >= 2:
            return 0.65
        else:
            return 0.3  # Single source — low corroboration

    def _check_linguistic_red_flags(self, headline: str) -> tuple:
        """
        Check for clickbait / sensationalism patterns.
        Returns (score: 0-1, flags: list).
        """
        flags = []
        flag_count = 0

        for i, pattern in enumerate(_CLICKBAIT_RE):
            matches = pattern.findall(headline)
            if matches:
                flag_count += len(matches)
                flags.append(f"Red flag: {pattern.pattern} matched")

        # Score: each flag reduces credibility
        if flag_count == 0:
            score = 1.0
        elif flag_count == 1:
            score = 0.6
        elif flag_count == 2:
            score = 0.35
        else:
            score = max(0.05, 0.35 - (flag_count - 2) * 0.1)

        return score, flags

    def _check_temporal_consistency(self, timestamp: datetime) -> tuple:
        """
        Check if the event timing makes sense for financial markets.
        Returns (score: 0-1, flags: list).
        """
        flags = []

        # Ensure timezone-aware
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        # Weekend check (Forex markets closed Sat/Sun)
        if timestamp.weekday() >= 5:  # Saturday or Sunday
            flags.append("Published on weekend (markets closed)")
            return 0.4, flags

        # Very late night / early morning UTC (low liquidity, unusual for major news)
        hour = timestamp.hour
        if hour < 4 or hour > 22:
            flags.append(f"Unusual publication hour: {hour}:00 UTC")
            return 0.5, flags

        # Far in the future (news claiming events that haven't happened)
        now = datetime.now(timezone.utc)
        if timestamp > now + timedelta(hours=1):
            flags.append("Timestamp is in the future")
            return 0.2, flags

        # Very old news being recirculated
        if (now - timestamp) > timedelta(days=7):
            flags.append("News older than 7 days (stale)")
            return 0.3, flags

        return 1.0, flags

    def _verify_with_gemini(self, headline: str, symbol: str = "") -> float:
        """
        Use Gemini AI to assess whether a financial claim is plausible.
        Returns score 0.0–1.0.
        """
        if not self._gemini_ready or not self.client:
            return 0.5  # Neutral when unavailable

        prompt = f"""You are a financial news fact-checker. Assess the plausibility 
of this financial news headline:

"{headline}"

Context: This headline is being evaluated for use in an automated {symbol or 'forex/crypto'} 
trading system.

Consider:
1. Is this claim consistent with known current market conditions?
2. Does this sound like it could come from a credible financial source?
3. Are there any obvious signs of misinformation, manipulation, or clickbait?
4. Is the claim internally consistent and logically sound?

Respond with ONLY a JSON object, nothing else:
{{
    "plausibility_score": <float from 0.0 (clearly fake) to 1.0 (clearly legitimate)>,
    "reasoning": "<one sentence explanation>"
}}"""

        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=200,
                ),
            )

            text = response.text.strip()
            # Remove markdown fences
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if "```" in text:
                    text = text[: text.rfind("```")]
            text = text.strip()

            import json
            data = json.loads(text)
            score = max(0.0, min(1.0, float(data.get("plausibility_score", 0.5))))
            return score

        except Exception as e:
            print(f"[FAKE-NEWS] Gemini verification failed: {e}")
            return 0.5  # Neutral fallback

    # ─── Helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise_topic(headline: str) -> str:
        """Extract a normalised topic key from a headline for matching."""
        # Remove punctuation, lowercase, take first 5 significant words
        words = re.sub(r"[^\w\s]", "", headline.lower()).split()
        stop_words = {"the", "a", "an", "is", "are", "was", "were", "in", "on",
                       "at", "to", "for", "of", "and", "or", "but", "has", "have",
                       "will", "be", "been", "this", "that", "it", "its"}
        significant = [w for w in words if w not in stop_words and len(w) > 2]
        return " ".join(significant[:5])


# ─── Singleton ───────────────────────────────────────────────────────────────
_detector = None


def get_fake_news_detector() -> FakeNewsDetector:
    global _detector
    if _detector is None:
        _detector = FakeNewsDetector()
    return _detector
