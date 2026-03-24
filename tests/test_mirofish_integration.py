"""
MiroFish Client & Agent — Test Suite
======================================
Tests the MiroFish integration modules without requiring a live MiroFish service.

Tests:
1. MiroFishClient URL construction and initialization
2. Seed document generation from synthetic data
3. Report parsing and signal extraction
4. Confidence extraction from text
5. Per-asset prediction extraction
6. Cache hit/miss logic in MiroFishAgent
7. Confluence bonus calculation
8. Graceful degradation when service is offline
"""

import sys
import os
import time
import json

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_client_initialization():
    """Test MiroFishClient initializes correctly."""
    print("\n" + "=" * 60)
    print("TEST 1: Client Initialization")
    print("=" * 60)
    
    from analysis.mirofish_client import MiroFishClient
    
    client = MiroFishClient(base_url="http://localhost:5001", timeout=30)
    assert client.base_url == "http://localhost:5001", f"Expected base_url without trailing slash"
    assert client.timeout == 30
    
    # Test trailing slash removal
    client2 = MiroFishClient(base_url="http://localhost:5001/", timeout=60)
    assert client2.base_url == "http://localhost:5001"
    
    print("  [PASS]: Client initialization correct")


def test_client_unavailable():
    """Test graceful handling when MiroFish service is offline."""
    print("\n" + "=" * 60)
    print("TEST 2: Client Unavailable (Graceful Degradation)")
    print("=" * 60)
    
    from analysis.mirofish_client import MiroFishClient
    
    # Connect to a port that definitely won't have MiroFish
    client = MiroFishClient(base_url="http://localhost:59999", timeout=2)
    
    assert not client.is_available(), "Should report unavailable on bad port"
    
    result = client.generate_ontology("test seed", "test requirement")
    assert result is None, "Should return None when service unavailable"
    
    result = client.build_graph("fake_project_id")
    assert result is None, "Should return None when service unavailable"
    
    result = client.run_full_pipeline("test", "test", max_rounds=1)
    assert result is None, "Full pipeline should return None when service unavailable"
    
    print("  [PASS]: Graceful degradation when service offline")


def test_seed_document_generation():
    """Test that MiroFishAgent generates valid seed documents."""
    print("\n" + "=" * 60)
    print("TEST 3: Seed Document Generation")
    print("=" * 60)
    
    from analysis.mirofish_agent import MiroFishAgent
    import pandas as pd
    import numpy as np
    
    agent = MiroFishAgent.__new__(MiroFishAgent)
    agent.state = type('MockState', (), {'get': lambda self, key: None})()
    
    # Test with no data
    doc = agent._generate_seed_document(
        symbols=["EURUSD", "BTCUSD"],
        market_data=None,
        news_events=None
    )
    
    assert "Financial Market Analysis Report" in doc
    assert "EURUSD" in doc or "2 instruments" in doc
    assert len(doc) > 100, f"Seed doc too short: {len(doc)} chars"
    print(f"  No-data seed doc: {len(doc)} chars")
    
    # Test with synthetic market data
    np.random.seed(42)
    dates = pd.date_range('2024-01-01', periods=100, freq='1h')
    df = pd.DataFrame({
        'time': dates,
        'open': np.random.normal(1.1, 0.001, 100),
        'high': np.random.normal(1.101, 0.001, 100),
        'low': np.random.normal(1.099, 0.001, 100),
        'close': np.random.normal(1.1, 0.001, 100),
        'tick_volume': np.random.poisson(100, 100),
    })
    
    market_data = {"EURUSD": {"H1": df}}
    
    doc_with_data = agent._generate_seed_document(
        symbols=["EURUSD"],
        market_data=market_data,
        news_events=["Fed Rate Decision", "NFP Data Release"]
    )
    
    assert "EURUSD" in doc_with_data
    assert "Fed Rate Decision" in doc_with_data
    assert "Price" in doc_with_data or "SMA" in doc_with_data, "Doc with market data should include metrics"
    print(f"  With-data seed doc: {len(doc_with_data)} chars")
    
    print("  [PASS]: Seed document generation works correctly")


def test_report_parsing():
    """Test MiroFishAgent can parse reports into structured signals."""
    print("\n" + "=" * 60)
    print("TEST 4: Report Parsing")
    print("=" * 60)
    
    from analysis.mirofish_agent import MiroFishAgent
    
    agent = MiroFishAgent.__new__(MiroFishAgent)
    
    # Test bullish report
    bullish_report = {
        "content": """
        Based on our multi-agent simulation with 1000 agents over 20 rounds:
        
        Overall Market Outlook: BULLISH
        
        The simulated agents showed strong buying pressure across major forex pairs.
        EURUSD showed significant upward momentum with high confidence (78%).
        BTCUSD agents predicted a rise to 72000 with 65% confidence.
        XAUUSD remained neutral with mixed signals.
        
        Conclusion: The market is likely to trend upward in the next 1-4 hours.
        """,
        "status": "completed"
    }
    
    result = agent._parse_report(bullish_report)
    
    assert result["sentiment"] == "BULLISH", f"Expected BULLISH, got {result['sentiment']}"
    assert result["confidence"] > 50, f"Expected confidence > 50, got {result['confidence']}"
    assert "EURUSD" in result["per_asset"], "Expected EURUSD in per-asset predictions"
    assert result["per_asset"]["EURUSD"]["direction"] == "BULLISH"
    assert len(result["reasoning"]) > 20, "Expected substantive reasoning"
    
    print(f"  Bullish report: sentiment={result['sentiment']}, confidence={result['confidence']}%")
    print(f"  Per-asset: {json.dumps(result['per_asset'], indent=2)}")
    
    # Test bearish report
    bearish_report = {
        "content": """
        Simulation Result: Bearish outlook.
        
        GBPUSD is expected to fall by 50 pips. Sell signal with 70% confidence.
        ETHUSD showing downward pressure. Bearish momentum detected.
        
        Key finding: Central bank hawkishness is driving selling pressure.
        """
    }
    
    result = agent._parse_report(bearish_report)
    assert result["sentiment"] == "BEARISH", f"Expected BEARISH, got {result['sentiment']}"
    print(f"  Bearish report: sentiment={result['sentiment']}, confidence={result['confidence']}%")
    
    # Test neutral/mixed report
    neutral_report = {
        "content": "The market shows both bullish and bearish signals. Uncertain outlook."
    }
    
    result = agent._parse_report(neutral_report)
    assert result["sentiment"] == "NEUTRAL", f"Expected NEUTRAL, got {result['sentiment']}"
    print(f"  Neutral report: sentiment={result['sentiment']}, confidence={result['confidence']}%")
    
    print("  [PASS]: Report parsing works correctly")


def test_confidence_extraction():
    """Test confidence score extraction from various text formats."""
    print("\n" + "=" * 60)
    print("TEST 5: Confidence Extraction")
    print("=" * 60)
    
    from analysis.mirofish_agent import MiroFishAgent
    
    agent = MiroFishAgent.__new__(MiroFishAgent)
    
    # Explicit confidence patterns
    assert agent._extract_confidence("confidence: 85%") == 85
    assert agent._extract_confidence("75% confidence in this prediction") == 75
    assert agent._extract_confidence("Confidence level: 60") == 60
    
    # Heuristic-based
    conf_strong = agent._extract_confidence("This is a very likely and strong prediction with high confidence")
    conf_weak = agent._extract_confidence("The outlook is uncertain and unclear with mixed signals and low confidence")
    
    assert conf_strong > conf_weak, f"Strong ({conf_strong}) should exceed weak ({conf_weak})"
    
    print(f"  Explicit '85%': {agent._extract_confidence('confidence: 85%')}")
    print(f"  Strong heuristic: {conf_strong}")
    print(f"  Weak heuristic: {conf_weak}")
    
    print("  [PASS]: Confidence extraction works correctly")


def test_per_asset_extraction():
    """Test per-asset prediction extraction."""
    print("\n" + "=" * 60)
    print("TEST 6: Per-Asset Prediction Extraction")
    print("=" * 60)
    
    from analysis.mirofish_agent import MiroFishAgent
    
    agent = MiroFishAgent.__new__(MiroFishAgent)
    
    text = """
    EURUSD: Strong bullish momentum, 80% confidence in upward movement.
    GBPUSD: Bearish pressure from UK data, sell signal at 72%.
    BTCUSD: Expected to rise with crypto market rally, buy recommendation.
    USDJPY: Neutral, ranging market with no clear direction.
    """
    
    per_asset = agent._extract_per_asset(text)
    
    assert "EURUSD" in per_asset, "Expected EURUSD"
    assert per_asset["EURUSD"]["direction"] == "BULLISH"
    assert per_asset["EURUSD"]["confidence"] >= 65, f"Expected confidence >= 65, got {per_asset['EURUSD']['confidence']}"
    
    assert "GBPUSD" in per_asset, "Expected GBPUSD"
    assert per_asset["GBPUSD"]["direction"] == "BEARISH", f"Expected BEARISH for GBPUSD, got {per_asset['GBPUSD']['direction']}"
    
    print(f"  Extracted {len(per_asset)} asset predictions:")
    for sym, pred in per_asset.items():
        print(f"    {sym}: {pred['direction']} ({pred['confidence']}%)")
    
    print("  [PASS]: Per-asset extraction works correctly")


def test_confluence_bonus():
    """Test confluence bonus calculation."""
    print("\n" + "=" * 60)
    print("TEST 7: Confluence Bonus Calculation")
    print("=" * 60)
    
    from analysis.mirofish_agent import MiroFishAgent
    
    agent = MiroFishAgent.__new__(MiroFishAgent)
    agent._cache = {
        "global_market": {
            "prediction": {
                "sentiment": "BULLISH",
                "confidence": 75,
                "per_asset": {
                    "EURUSD": {"direction": "BULLISH", "confidence": 80},
                    "GBPUSD": {"direction": "BEARISH", "confidence": 70},
                },
                "reasoning": "Test",
                "timestamp": time.time()
            },
            "timestamp": time.time()
        }
    }
    agent._cache_ttl = 3600
    agent.state = type('MockState', (), {'get': lambda self, key: None})()
    
    # Mock settings
    import config.settings as settings
    original_bonus = getattr(settings, 'MIROFISH_MAX_CONFLUENCE_BONUS', 1)
    settings.MIROFISH_MAX_CONFLUENCE_BONUS = 1
    
    try:
        # BUY EURUSD — MiroFish is BULLISH on EURUSD → +1
        bonus = agent.get_confluence_bonus("EURUSD", "BUY")
        assert bonus == 1, f"Expected +1 bonus for aligned BUY/BULLISH, got {bonus}"
        print(f"  BUY EURUSD (MF: BULLISH 80%): bonus={bonus}")
        
        # SELL EURUSD — MiroFish is BULLISH on EURUSD → 0
        bonus = agent.get_confluence_bonus("EURUSD", "SELL")
        assert bonus == 0, f"Expected 0 bonus for counter SELL/BULLISH, got {bonus}"
        print(f"  SELL EURUSD (MF: BULLISH 80%): bonus={bonus}")
        
        # SELL GBPUSD — MiroFish is BEARISH on GBPUSD → +1
        bonus = agent.get_confluence_bonus("GBPUSD", "SELL")
        assert bonus == 1, f"Expected +1 bonus for aligned SELL/BEARISH, got {bonus}"
        print(f"  SELL GBPUSD (MF: BEARISH 70%): bonus={bonus}")
        
        # Unknown symbol — falls back to global sentiment (BULLISH)
        bonus = agent.get_confluence_bonus("XAUUSD", "BUY")
        assert bonus == 1, f"Expected +1 from global BULLISH fallback, got {bonus}"
        print(f"  BUY XAUUSD (global fallback: BULLISH 75%): bonus={bonus}")
        
    finally:
        settings.MIROFISH_MAX_CONFLUENCE_BONUS = original_bonus
    
    print("  [PASS]: Confluence bonus calculation correct")


def test_cache_logic():
    """Test cache hit/miss and expiry."""
    print("\n" + "=" * 60)
    print("TEST 8: Cache Logic")
    print("=" * 60)
    
    from analysis.mirofish_agent import MiroFishAgent
    
    agent = MiroFishAgent.__new__(MiroFishAgent)
    agent._cache = {}
    agent._cache_ttl = 2  # 2 seconds for testing
    agent._running = False
    agent._bg_lock = __import__('threading').Lock()
    agent._last_simulation_time = time.time()  # Prevent triggering sim
    agent.state = type('MockState', (), {'get': lambda self, key: None})()
    agent.client = type('MockClient', (), {'is_available': lambda self: False})()
    
    # No cache → should return None
    result = agent.get_prediction()
    assert result is None, "Expected None when no cache"
    print("  Empty cache: None (correct)")
    
    # Populate cache
    prediction = {
        "sentiment": "BULLISH", "confidence": 70,
        "per_asset": {}, "reasoning": "Test",
        "timestamp": time.time()
    }
    agent._cache["global_market"] = {
        "prediction": prediction,
        "timestamp": time.time()
    }
    
    # Cache hit
    result = agent.get_prediction()
    assert result is not None, "Expected cache hit"
    assert result["sentiment"] == "BULLISH"
    print(f"  Cache hit: {result['sentiment']} (correct)")
    
    # Wait for cache expiry
    time.sleep(2.5)
    result = agent.get_prediction()
    # After expiry, get_prediction returns stale cache (better than nothing)
    assert result is not None, "Should return stale cache"
    print(f"  Stale cache returned: {result['sentiment']} (correct, better than None)")
    
    print("  [PASS]: Cache logic works correctly")


def test_symbol_suffix_stripping():
    """Test broker suffix stripping for symbol matching."""
    print("\n" + "=" * 60)
    print("TEST 9: Symbol Suffix Stripping")
    print("=" * 60)
    
    from analysis.mirofish_agent import _strip_suffix
    
    assert _strip_suffix("EURUSDm") == "EURUSD"
    assert _strip_suffix("BTCUSDc") == "BTCUSD"
    assert _strip_suffix("EURUSD") == "EURUSD"  # No suffix
    assert _strip_suffix("XAUUSDz") == "XAUUSD"
    assert _strip_suffix("USD") == "USD"  # Too short
    
    print("  EURUSDm → EURUSD ✓")
    print("  BTCUSDc → BTCUSD ✓")
    print("  EURUSD → EURUSD ✓")
    print("  [PASS]: Suffix stripping works correctly")


def test_requirement_generation():
    """Test prediction requirement text generation."""
    print("\n" + "=" * 60)
    print("TEST 10: Requirement Generation")
    print("=" * 60)
    
    from analysis.mirofish_agent import MiroFishAgent
    
    agent = MiroFishAgent.__new__(MiroFishAgent)
    
    req = agent._generate_requirement(["EURUSD", "BTCUSD", "XAUUSD"])
    
    assert "EURUSD" in req
    assert "BTCUSD" in req
    assert "predict" in req.lower() or "direction" in req.lower()
    assert "confidence" in req.lower()
    
    print(f"  Requirement ({len(req)} chars): {req[:100]}...")
    
    print("  [PASS]: Requirement generation works correctly")


if __name__ == '__main__':
    print("=" * 60)
    print("  MIROFISH INTEGRATION — TEST SUITE")
    print("=" * 60)
    
    tests = [
        test_client_initialization,
        test_client_unavailable,
        test_seed_document_generation,
        test_report_parsing,
        test_confidence_extraction,
        test_per_asset_extraction,
        test_confluence_bonus,
        test_cache_logic,
        test_symbol_suffix_stripping,
        test_requirement_generation,
    ]
    
    passed = 0
    failed = 0
    
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL]: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print("\n" + "=" * 60)
    print(f"  RESULTS: {passed}/{passed + failed} tests passed")
    if failed == 0:
        print("  ALL TESTS PASSED ✓")
    else:
        print(f"  {failed} tests FAILED")
    print("=" * 60)
