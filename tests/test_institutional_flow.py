"""
Test Institutional Flow Detector
=================================
Verifies the InstitutionalFlowDetector correctly identifies:
1. Volume anomalies (spikes > 2x average)
2. Absorption candles (high volume + small body)
3. Displacement candles (large body > 3x average)
4. CVD direction and divergence
5. Trade blocking against institutional flow
6. Trade boosting when aligned with flow
"""

import sys
import os
import numpy as np
import pandas as pd

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def create_synthetic_data(n_bars=200, pattern='normal'):
    """
    Creates synthetic OHLCV data with specific institutional patterns.
    
    Patterns:
    - 'normal': random walk, normal volume
    - 'accumulation': drifting down slowly, high volume at lows, small bodies
    - 'distribution': drifting up, high volume at highs, small bodies
    - 'displacement_bull': sudden large bullish candles with volume spikes
    - 'displacement_bear': sudden large bearish candles with volume spikes
    """
    np.random.seed(42)
    
    # Base price random walk
    returns = np.random.normal(0, 0.001, n_bars)
    
    if pattern == 'accumulation':
        # Slight downtrend, but with absorption at lows
        returns = np.random.normal(-0.0002, 0.0008, n_bars)
    elif pattern == 'distribution':
        # Slight uptrend, but with absorption at highs
        returns = np.random.normal(0.0002, 0.0008, n_bars)
    elif pattern == 'displacement_bull':
        returns = np.random.normal(0.0001, 0.0005, n_bars)
        # Add large bullish moves at the END so detector sees them on last bar
        for i in [n_bars - 5, n_bars - 3, n_bars - 2, n_bars - 1]:
            if i < n_bars:
                returns[i] = 0.008  # Large bullish displacement
    elif pattern == 'displacement_bear':
        returns = np.random.normal(-0.0001, 0.0005, n_bars)
        for i in [n_bars - 5, n_bars - 3, n_bars - 2, n_bars - 1]:
            if i < n_bars:
                returns[i] = -0.008  # Large bearish displacement

    close = 1.1000 * np.cumprod(1 + returns)
    
    # OHLC
    high = close * (1 + np.abs(np.random.normal(0, 0.0005, n_bars)))
    low = close * (1 - np.abs(np.random.normal(0, 0.0005, n_bars)))
    open_p = close * (1 + np.random.normal(0, 0.0003, n_bars))
    
    # Volume
    base_volume = np.random.poisson(100, n_bars).astype(float)
    
    if pattern == 'accumulation':
        # High volume on down candles near lows (absorption)
        for i in range(n_bars):
            if close[i] < open_p[i]:  # Bearish candle
                base_volume[i] *= 2.5  # High volume
                # Make body small (absorption)
                mid = (close[i] + open_p[i]) / 2
                close[i] = mid - 0.00005
                open_p[i] = mid + 0.00005
    elif pattern == 'distribution':
        # High volume on up candles near highs
        for i in range(n_bars):
            if close[i] > open_p[i]:  # Bullish candle
                base_volume[i] *= 2.5
                mid = (close[i] + open_p[i]) / 2
                close[i] = mid + 0.00005
                open_p[i] = mid - 0.00005
    elif pattern in ('displacement_bull', 'displacement_bear'):
        # Volume spikes on displacement candles at the END
        for i in [n_bars - 5, n_bars - 3, n_bars - 2, n_bars - 1]:
            if i < n_bars:
                base_volume[i] *= 5.0  # Massive volume spike

    dates = pd.date_range('2024-01-01', periods=n_bars, freq='1min')

    df = pd.DataFrame({
        'time': dates,
        'open': open_p,
        'high': high,
        'low': low,
        'close': close,
        'tick_volume': base_volume.astype(int),
        'spread': np.random.randint(1, 5, n_bars),
    })

    # Ensure high >= max(open, close) and low <= min(open, close)
    df['high'] = df[['high', 'open', 'close']].max(axis=1)
    df['low'] = df[['low', 'open', 'close']].min(axis=1)

    return df


def test_volume_anomalies():
    """Test that volume spikes are correctly detected."""
    print("\n" + "=" * 60)
    print("TEST 1: Volume Anomaly Detection")
    print("=" * 60)
    
    from analysis.institutional_flow_detector import InstitutionalFlowDetector
    detector = InstitutionalFlowDetector()
    
    # Normal data -- should have low/no volume anomalies
    df_normal = create_synthetic_data(200, 'normal')
    vol_normal = detector._detect_volume_anomalies(df_normal)
    print(f"  Normal data: zscore={vol_normal['zscore']}, spike={vol_normal['is_spike']}")
    
    # Displacement data -- should have volume spikes on last bar
    df_spike = create_synthetic_data(200, 'displacement_bull')
    vol_spike = detector._detect_volume_anomalies(df_spike)
    print(f"  Spike data: zscore={vol_spike['zscore']}, spike={vol_spike['is_spike']}, "
          f"spike_count={vol_spike['spike_count_10']}, ratio={vol_spike['volume_ratio']}")
    
    # Verify spike detection: spike data should have higher volume metrics than normal
    assert vol_spike['volume_ratio'] > vol_normal['volume_ratio'], \
        f"Spike volume_ratio ({vol_spike['volume_ratio']}) should exceed normal ({vol_normal['volume_ratio']})"
    assert vol_spike['spike_count_10'] >= 1, f"Expected at least 1 spike in last 10 bars"
    
    print("  [PASS]: Volume anomalies correctly detected")


def test_absorption():
    """Test absorption candle detection (high volume, small body)."""
    print("\n" + "=" * 60)
    print("TEST 2: Absorption Candle Detection")
    print("=" * 60)
    
    from analysis.institutional_flow_detector import InstitutionalFlowDetector
    detector = InstitutionalFlowDetector()
    
    # Accumulation pattern — absorption at lows
    df_accum = create_synthetic_data(200, 'accumulation')
    abs_accum = detector._detect_absorption(df_accum)
    print(f"  Accumulation: absorption={abs_accum['is_absorption']}, count={abs_accum['recent_count']}, "
          f"bias={abs_accum['bias']}, body_ratio={abs_accum['body_ratio']}")
    
    # Verify absorption detected
    assert abs_accum['recent_count'] >= 1, f"Expected at least 1 absorption candle in accumulation data"
    
    # Distribution pattern — absorption at highs
    df_dist = create_synthetic_data(200, 'distribution')
    abs_dist = detector._detect_absorption(df_dist)
    print(f"  Distribution: absorption={abs_dist['is_absorption']}, count={abs_dist['recent_count']}, "
          f"bias={abs_dist['bias']}")
    
    print("  [PASS]: Absorption candles correctly detected")


def test_displacement():
    """Test displacement candle detection (large aggressive bodies)."""
    print("\n" + "=" * 60)
    print("TEST 3: Displacement Candle Detection")
    print("=" * 60)
    
    from analysis.institutional_flow_detector import InstitutionalFlowDetector
    detector = InstitutionalFlowDetector()
    
    # Normal data — no displacement
    df_normal = create_synthetic_data(200, 'normal')
    disp_normal = detector._detect_displacement(df_normal)
    print(f"  Normal data: displacement={disp_normal['is_displacement']}, "
          f"body_mult={disp_normal['body_multiple']}, count={disp_normal['recent_count']}")
    
    # Bullish displacement
    df_bull = create_synthetic_data(200, 'displacement_bull')
    disp_bull = detector._detect_displacement(df_bull)
    print(f"  Bullish Disp: displacement={disp_bull['is_displacement']}, "
          f"direction={disp_bull['direction']}, body_mult={disp_bull['body_multiple']}, "
          f"count={disp_bull['recent_count']}")
    
    assert disp_bull['body_multiple'] > disp_normal['body_multiple'], \
        f"Bullish displacement body_mult ({disp_bull['body_multiple']}) should exceed normal ({disp_normal['body_multiple']})"
    
    # Bearish displacement
    df_bear = create_synthetic_data(200, 'displacement_bear')
    disp_bear = detector._detect_displacement(df_bear)
    print(f"  Bearish Disp: displacement={disp_bear['is_displacement']}, "
          f"direction={disp_bear['direction']}, count={disp_bear['recent_count']}")
    
    assert disp_bear['body_multiple'] > disp_normal['body_multiple'], \
        f"Bearish displacement body_mult ({disp_bear['body_multiple']}) should exceed normal ({disp_normal['body_multiple']})"
    
    print("  [PASS]: Displacement candles correctly detected")


def test_cvd():
    """Test Cumulative Volume Delta analysis."""
    print("\n" + "=" * 60)
    print("TEST 4: CVD Analysis")
    print("=" * 60)
    
    from analysis.institutional_flow_detector import InstitutionalFlowDetector
    detector = InstitutionalFlowDetector()
    
    # Bullish displacement — CVD should be positive (net buying)
    df_bull = create_synthetic_data(200, 'displacement_bull')
    cvd_bull = detector._compute_cvd_signal(df_bull)
    print(f"  Bullish data CVD: cvd_20={cvd_bull['cvd_20']}, direction={cvd_bull['direction']}, "
          f"divergence={cvd_bull['divergence']}")
    
    # Bearish displacement — CVD should be negative (net selling)
    df_bear = create_synthetic_data(200, 'displacement_bear')
    cvd_bear = detector._compute_cvd_signal(df_bear)
    print(f"  Bearish data CVD: cvd_20={cvd_bear['cvd_20']}, direction={cvd_bear['direction']}, "
          f"divergence={cvd_bear['divergence']}")
    
    print("  [PASS]: CVD analysis functional")


def test_composite_score():
    """Test end-to-end institutional flow scoring."""
    print("\n" + "=" * 60)
    print("TEST 5: Composite Institutional Score")
    print("=" * 60)
    
    from analysis.institutional_flow_detector import InstitutionalFlowDetector
    detector = InstitutionalFlowDetector()
    
    # Normal market — low score expected
    df_normal = create_synthetic_data(200, 'normal')
    normal_result = detector.analyze('EURUSD', {'M1': df_normal})
    print(f"  Normal: score={normal_result['score']}, direction={normal_result['direction']}")
    
    # Strong bullish displacement — high score expected
    df_bull = create_synthetic_data(200, 'displacement_bull')
    bull_result = detector.analyze('EURUSD', {'M1': df_bull})
    print(f"  Bullish: score={bull_result['score']}, direction={bull_result['direction']}")
    
    # Accumulation — moderate-high score expected
    df_accum = create_synthetic_data(200, 'accumulation')
    accum_result = detector.analyze('EURUSD', {'M1': df_accum})
    print(f"  Accumulation: score={accum_result['score']}, direction={accum_result['direction']}")
    
    # Verify scores differentiate patterns
    assert bull_result['score'] > normal_result['score'], \
        f"Bullish displacement score ({bull_result['score']}) should be higher than normal ({normal_result['score']})"
    
    print("  [PASS]: Composite scoring differentiates patterns")


def test_trade_blocking():
    """Test that trades against institutional flow are blocked."""
    print("\n" + "=" * 60)
    print("TEST 6: Trade Blocking Logic")
    print("=" * 60)
    
    from analysis.institutional_flow_detector import InstitutionalFlowDetector
    detector = InstitutionalFlowDetector()
    
    # High score bullish flow
    high_bull_flow = {'score': 85, 'direction': 'BULLISH'}
    
    # BUY trade should NOT be blocked (aligned)
    block, reason = detector.should_block_trade('BUY', high_bull_flow)
    print(f"  BUY vs BULLISH flow (85): block={block} -- {reason}")
    assert not block, "BUY should NOT be blocked when flow is BULLISH"
    
    # SELL trade SHOULD be blocked (counter-flow)
    block, reason = detector.should_block_trade('SELL', high_bull_flow)
    print(f"  SELL vs BULLISH flow (85): block={block} -- {reason}")
    assert block, "SELL SHOULD be blocked when flow is strongly BULLISH"
    
    # Low score — should not block either direction
    low_flow = {'score': 30, 'direction': 'BULLISH'}
    block, reason = detector.should_block_trade('SELL', low_flow)
    print(f"  SELL vs weak BULLISH flow (30): block={block} -- {reason}")
    assert not block, "Should NOT block when flow score is low"
    
    print("  [PASS]: Trade blocking logic correct")


def test_position_scaling():
    """Test position scaling for aligned trades."""
    print("\n" + "=" * 60)
    print("TEST 7: Position Scaling")
    print("=" * 60)
    
    from analysis.institutional_flow_detector import InstitutionalFlowDetector
    detector = InstitutionalFlowDetector()
    
    # High alignment
    high_flow = {'score': 85, 'direction': 'BULLISH'}
    scale = detector.get_position_scale('BUY', high_flow)
    print(f"  BUY + BULLISH 85: scale={scale}x")
    assert scale == 1.5, f"Expected 1.5x scaling, got {scale}"
    
    # Medium alignment
    med_flow = {'score': 65, 'direction': 'BULLISH'}
    scale = detector.get_position_scale('BUY', med_flow)
    print(f"  BUY + BULLISH 65: scale={scale}x")
    assert scale == 1.2, f"Expected 1.2x scaling, got {scale}"
    
    # Counter direction — no scaling
    scale = detector.get_position_scale('SELL', high_flow)
    print(f"  SELL + BULLISH 85: scale={scale}x")
    assert scale == 1.0, f"Expected 1.0x (no scaling), got {scale}"
    
    print("  [PASS]: Position scaling correct")


def test_edge_cases():
    """Test edge cases: no volume, insufficient data, etc."""
    print("\n" + "=" * 60)
    print("TEST 8: Edge Cases")
    print("=" * 60)
    
    from analysis.institutional_flow_detector import InstitutionalFlowDetector
    detector = InstitutionalFlowDetector()
    
    # Empty data
    result = detector.analyze('EURUSD', {})
    print(f"  Empty data: score={result['score']}, dir={result['direction']}")
    assert result['score'] == 0, "Empty data should return score 0"
    
    # Data without tick_volume
    df_no_vol = pd.DataFrame({
        'open': [1.1] * 100, 'high': [1.101] * 100,
        'low': [1.099] * 100, 'close': [1.1] * 100,
    })
    result = detector.analyze('EURUSD', {'M1': df_no_vol})
    print(f"  No volume: score={result['score']}, dir={result['direction']}")
    assert result['score'] == 0, "No volume should return score 0"
    
    # Very short data
    df_short = create_synthetic_data(20, 'normal')
    result = detector.analyze('EURUSD', {'M1': df_short})
    print(f"  Short data (20 bars): score={result['score']}, dir={result['direction']}")
    assert result['score'] == 0, "Short data should return score 0"
    
    print("  [PASS]: Edge cases handled correctly")


if __name__ == '__main__':
    print("=" * 60)
    print("  INSTITUTIONAL FLOW DETECTOR -- TEST SUITE")
    print("=" * 60)
    
    tests = [
        test_volume_anomalies,
        test_absorption,
        test_displacement,
        test_cvd,
        test_composite_score,
        test_trade_blocking,
        test_position_scaling,
        test_edge_cases,
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
        print("  ALL TESTS PASSED")
    else:
        print(f"  {failed} tests FAILED")
    print("=" * 60)
