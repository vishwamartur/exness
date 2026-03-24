"""
Unit tests for the Fake News Detector module.
Tests all 5 credibility signals independently and the composite score.
"""

import sys
import os
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# Adjust path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from analysis.fake_news_detector import FakeNewsDetector


class TestSourceReputation(unittest.TestCase):
    """Test Signal 1: Source Reputation scoring."""

    def setUp(self):
        with patch('analysis.fake_news_detector.GEMINI_AVAILABLE', False):
            self.detector = FakeNewsDetector()

    def test_tier1_reuters(self):
        score, tier = self.detector._check_source_reputation("Reuters")
        self.assertEqual(tier, 1)
        self.assertEqual(score, 1.0)

    def test_tier1_bloomberg(self):
        score, tier = self.detector._check_source_reputation("Bloomberg")
        self.assertEqual(tier, 1)
        self.assertEqual(score, 1.0)

    def test_tier2_marketwatch(self):
        score, tier = self.detector._check_source_reputation("MarketWatch")
        self.assertEqual(tier, 2)
        self.assertEqual(score, 0.75)

    def test_tier3_twitter(self):
        score, tier = self.detector._check_source_reputation("Twitter")
        self.assertEqual(tier, 3)
        self.assertEqual(score, 0.2)

    def test_tier3_reddit(self):
        score, tier = self.detector._check_source_reputation("Reddit")
        self.assertEqual(tier, 3)
        self.assertEqual(score, 0.2)

    def test_unknown_source(self):
        score, tier = self.detector._check_source_reputation("randomcryptoblog.xyz")
        self.assertEqual(tier, 4)
        self.assertEqual(score, 0.4)


class TestLinguisticRedFlags(unittest.TestCase):
    """Test Signal 4: Linguistic red flag detection."""

    def setUp(self):
        with patch('analysis.fake_news_detector.GEMINI_AVAILABLE', False):
            self.detector = FakeNewsDetector()

    def test_clean_headline(self):
        score, flags = self.detector._check_linguistic_red_flags(
            "ECB signals rate cut in upcoming policy meeting"
        )
        self.assertEqual(score, 1.0)
        self.assertEqual(len(flags), 0)

    def test_clickbait_breaking(self):
        score, flags = self.detector._check_linguistic_red_flags(
            "BREAKING: Market CRASH imminent!!!"
        )
        self.assertLess(score, 0.5)
        self.assertGreater(len(flags), 0)

    def test_hype_language(self):
        score, flags = self.detector._check_linguistic_red_flags(
            "SHOCKING: Bitcoin to the moon! $100K+ guaranteed!!!"
        )
        self.assertLess(score, 0.4)
        self.assertGreater(len(flags), 1)

    def test_normal_market_news(self):
        score, flags = self.detector._check_linguistic_red_flags(
            "ECB signals potential rate cut in upcoming meeting"
        )
        self.assertGreaterEqual(score, 0.6)


class TestTemporalConsistency(unittest.TestCase):
    """Test Signal 5: Temporal consistency checks."""

    def setUp(self):
        with patch('analysis.fake_news_detector.GEMINI_AVAILABLE', False):
            self.detector = FakeNewsDetector()

    def test_weekday_business_hours(self):
        # Use a recent weekday during business hours
        now = datetime.now(timezone.utc)
        # Find the most recent Tuesday at 14:00 UTC
        days_since_tue = (now.weekday() - 1) % 7  # 1 = Tuesday
        recent_tue = now - timedelta(days=max(days_since_tue, 1))
        ts = recent_tue.replace(hour=14, minute=0, second=0, microsecond=0)
        score, flags = self.detector._check_temporal_consistency(ts)
        self.assertEqual(score, 1.0)
        self.assertEqual(len(flags), 0)

    def test_weekend_publication(self):
        # Saturday — markets closed
        ts = datetime(2024, 3, 9, 14, 0, 0, tzinfo=timezone.utc)
        score, flags = self.detector._check_temporal_consistency(ts)
        self.assertLessEqual(score, 0.4)
        self.assertIn("weekend", flags[0].lower())

    def test_future_timestamp(self):
        ts = datetime.now(timezone.utc) + timedelta(hours=5)
        score, flags = self.detector._check_temporal_consistency(ts)
        self.assertLessEqual(score, 0.2)

    def test_stale_news(self):
        ts = datetime.now(timezone.utc) - timedelta(days=10)
        score, flags = self.detector._check_temporal_consistency(ts)
        self.assertLessEqual(score, 0.5)  # Stale/weekend → reduced score


class TestCrossSource(unittest.TestCase):
    """Test Signal 2: Cross-source corroboration."""

    def setUp(self):
        with patch('analysis.fake_news_detector.GEMINI_AVAILABLE', False):
            self.detector = FakeNewsDetector()

    def test_single_source_low_score(self):
        score = self.detector._check_cross_source(
            "Fed raises rates by 25bp", "Reuters"
        )
        self.assertLessEqual(score, 0.4)

    def test_multiple_sources_high_score(self):
        headline = "Fed raises rates by 25bp"
        self.detector._check_cross_source(headline, "Reuters")
        self.detector._check_cross_source(headline, "Bloomberg")
        score = self.detector._check_cross_source(headline, "CNBC")
        self.assertGreaterEqual(score, 0.8)

    def test_related_headlines_boost(self):
        score = self.detector._check_cross_source(
            "Gold prices surge on safe-haven demand",
            "MarketWatch",
            related_headlines=["Gold rallies", "Safe-haven buying lifts gold"]
        )
        self.assertGreaterEqual(score, 0.6)


class TestOverallCredibility(unittest.TestCase):
    """Test the composite credibility score."""

    def setUp(self):
        with patch('analysis.fake_news_detector.GEMINI_AVAILABLE', False):
            self.detector = FakeNewsDetector()

    @patch('analysis.fake_news_detector.FakeNewsDetector._verify_with_gemini')
    def test_legitimate_news(self, mock_gemini):
        mock_gemini.return_value = 0.9
        result = self.detector.assess_credibility(
            headline="ECB holds interest rates unchanged",
            source="Reuters",
            timestamp=datetime(2024, 3, 5, 14, 0, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(result['is_trusted'])
        self.assertGreaterEqual(result['credibility_score'], 0.6)

    @patch('analysis.fake_news_detector.FakeNewsDetector._verify_with_gemini')
    def test_obviously_fake(self, mock_gemini):
        mock_gemini.return_value = 0.1
        result = self.detector.assess_credibility(
            headline="BREAKING!!! Fed PRINTING $100 TRILLION!!! COLLAPSE IMMINENT!!!",
            source="telegram",
            timestamp=datetime(2024, 3, 9, 3, 0, 0, tzinfo=timezone.utc),  # Weekend 3AM
        )
        self.assertFalse(result['is_trusted'])
        self.assertLess(result['credibility_score'], 0.4)
        self.assertGreater(len(result['flags']), 0)


class TestTrustGate(unittest.TestCase):
    """Test the boolean gate and weight multiplier."""

    def setUp(self):
        with patch('analysis.fake_news_detector.GEMINI_AVAILABLE', False):
            self.detector = FakeNewsDetector()

    def test_trusted_returns_true(self):
        result = {'is_trusted': True, 'credibility_score': 0.8}
        self.assertTrue(self.detector.should_trust_news(result))

    def test_untrusted_returns_false(self):
        result = {'is_trusted': False, 'credibility_score': 0.2}
        self.assertFalse(self.detector.should_trust_news(result))

    def test_weight_multiplier_trusted(self):
        result = {'is_trusted': True}
        self.assertEqual(self.detector.get_news_weight_multiplier(result), 1.0)

    @patch('config.settings')
    def test_weight_multiplier_untrusted(self, mock_settings):
        mock_settings.FAKE_NEWS_DISCOUNT_FACTOR = 0.1
        result = {'is_trusted': False}
        mult = self.detector.get_news_weight_multiplier(result)
        self.assertLessEqual(mult, 0.2)


if __name__ == '__main__':
    unittest.main()
