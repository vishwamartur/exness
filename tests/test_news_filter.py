"""Tests for utils/news_filter.py — Live calendar + hardcoded fallback."""

import threading
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from utils.news_filter import (
    is_news_blackout,
    get_upcoming_events,
    get_active_events,
    _fetch_calendar,
    _hardcoded_blackout_check,
    _extract_currencies,
    _strip_suffix,
    _CALENDAR_CACHE,
    _CACHE_LOCK,
)


# ── Helper fixtures ──────────────────────────────────────────────────────

def _reset_cache():
    """Reset the global calendar cache between tests."""
    with _CACHE_LOCK:
        _CALENDAR_CACHE["data"] = []
        _CALENDAR_CACHE["fetched_at"] = None


# ── Unit tests: helpers ──────────────────────────────────────────────────

class TestHelpers:
    def test_strip_suffix_m(self):
        assert _strip_suffix("EURUSDm") == "EURUSD"

    def test_strip_suffix_c(self):
        assert _strip_suffix("EURUSDc") == "EURUSD"

    def test_strip_suffix_noop(self):
        assert _strip_suffix("EURUSD") == "EURUSD"

    def test_strip_suffix_short_symbol(self):
        assert _strip_suffix("USDm") == "USDm"  # too short to strip

    def test_extract_currencies_pair(self):
        assert _extract_currencies("EURUSD") == ["EUR", "USD"]

    def test_extract_currencies_with_suffix(self):
        assert _extract_currencies("GBPUSDm") == ["GBP", "USD"]

    def test_extract_currencies_short(self):
        assert _extract_currencies("BTC") == ["BTC"]


# ── Unit tests: hardcoded fallback ───────────────────────────────────────

class TestHardcodedBlackout:
    def test_nfp_friday_first_week(self):
        """NFP is first Friday of the month at 13:30 UTC with 45-min buffer."""
        # 2025-01-03 is a Friday, week 1
        nfp_time = datetime(2025, 1, 3, 13, 30, tzinfo=timezone.utc)
        hit, name = _hardcoded_blackout_check("EURUSD", nfp_time)
        assert hit is True
        assert name == "NFP"

    def test_nfp_outside_buffer(self):
        nfp_time = datetime(2025, 1, 3, 15, 0, tzinfo=timezone.utc)
        hit, _ = _hardcoded_blackout_check("EURUSD", nfp_time)
        assert hit is False

    def test_nfp_unaffected_pair(self):
        """NFP only affects USD pairs."""
        nfp_time = datetime(2025, 1, 3, 13, 30, tzinfo=timezone.utc)
        hit, _ = _hardcoded_blackout_check("EURGBP", nfp_time)
        assert hit is False

    def test_daily_avoid_window(self):
        t = datetime(2025, 1, 6, 13, 30, tzinfo=timezone.utc)
        hit, name = _hardcoded_blackout_check("EURUSD", t)
        assert hit is True
        assert name == "US_Open_Volatility"

    def test_no_blackout_normal_time(self):
        t = datetime(2025, 1, 6, 10, 0, tzinfo=timezone.utc)
        hit, name = _hardcoded_blackout_check("EURUSD", t)
        assert hit is False
        assert name == ""


# ── Unit tests: live calendar ────────────────────────────────────────────

class TestLiveCalendar:
    def setup_method(self):
        _reset_cache()

    @patch("utils.news_filter.requests")
    def test_fetch_calendar_success(self, mock_requests):
        """Successful fetch populates cache with high-impact events."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"title": "NFP", "country": "USD", "impact": "High",
             "date": "2025-01-03T13:30:00-05:00"},
            {"title": "GDP", "country": "USD", "impact": "Medium",
             "date": "2025-01-03T15:00:00-05:00"},
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        events = _fetch_calendar()
        assert len(events) == 1  # Only high-impact
        assert events[0]["name"] == "NFP"
        assert events[0]["currency"] == "USD"

    @patch("utils.news_filter.requests")
    def test_fetch_calendar_failure_fallback(self, mock_requests):
        """Failed fetch returns cached (empty) data."""
        mock_requests.get.side_effect = Exception("Network error")
        events = _fetch_calendar()
        assert events == []  # Empty cache

    @patch("utils.news_filter.requests")
    def test_cache_reuse(self, mock_requests):
        """Second call within cache window doesn't re-fetch."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"title": "CPI", "country": "USD", "impact": "High",
             "date": "2025-01-15T13:30:00-05:00"},
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        _fetch_calendar()
        _fetch_calendar()
        assert mock_requests.get.call_count == 1


# ── Integration tests: is_news_blackout ──────────────────────────────────

class TestNewsBlackout:
    def setup_method(self):
        _reset_cache()

    @patch("utils.news_filter._fetch_calendar")
    def test_live_blackout_hit(self, mock_fetch):
        t = datetime(2025, 1, 3, 13, 30, tzinfo=timezone.utc)
        mock_fetch.return_value = [
            {"name": "NFP", "currency": "USD", "dt_utc": t}
        ]
        hit, name = is_news_blackout("EURUSD", t)
        assert hit is True
        assert "FF:NFP" in name

    @patch("utils.news_filter._fetch_calendar")
    def test_live_blackout_miss(self, mock_fetch):
        t = datetime(2025, 1, 3, 10, 0, tzinfo=timezone.utc)
        mock_fetch.return_value = [
            {"name": "NFP", "currency": "USD",
             "dt_utc": datetime(2025, 1, 3, 13, 30, tzinfo=timezone.utc)}
        ]
        hit, _ = is_news_blackout("EURUSD", t)
        assert hit is False

    @patch("utils.news_filter._fetch_calendar")
    def test_fallback_when_live_empty(self, mock_fetch):
        """When live feed returns empty, hardcoded fallback is used."""
        mock_fetch.return_value = []
        # NFP Friday first week at 13:30 UTC
        t = datetime(2025, 1, 3, 13, 30, tzinfo=timezone.utc)
        hit, name = is_news_blackout("EURUSD", t)
        assert hit is True
        assert name == "NFP"


# ── Thread safety ────────────────────────────────────────────────────────

class TestThreadSafety:
    def setup_method(self):
        _reset_cache()

    @patch("utils.news_filter.requests")
    def test_concurrent_fetches(self, mock_requests):
        """Multiple threads calling _fetch_calendar don't corrupt cache."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"title": "ECB", "country": "EUR", "impact": "High",
             "date": "2025-01-16T12:45:00+00:00"},
        ]
        mock_resp.raise_for_status = MagicMock()
        mock_requests.get.return_value = mock_resp

        results = []

        def fetch_and_store():
            r = _fetch_calendar()
            results.append(len(r))

        threads = [threading.Thread(target=fetch_and_store) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should get exactly 1 event
        assert all(r == 1 for r in results)
        # Only 1 actual HTTP call due to caching
        assert mock_requests.get.call_count == 1
