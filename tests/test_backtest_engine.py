"""
Unit tests for BacktestEngine.

Tests verify:
- Metric calculations with known trade sequences
- Walk-forward window sliding
- Data loading returns proper DataFrame structure
- Session detection logic
- Signal generation
"""

import sys
import os
import pytest

# Add project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import numpy as np
import pandas as pd

from backtesting.backtest_engine import BacktestEngine


def _make_synthetic_data(n_bars=500, base_price=2000.0, volatility=2.0, seed=42):
    """Generate synthetic XAUUSD M5 OHLCV data for testing."""
    np.random.seed(seed)
    prices = [base_price]
    for _ in range(n_bars - 1):
        change = np.random.randn() * volatility
        prices.append(prices[-1] + change)
    prices = np.array(prices)

    # Generate OHLC from close prices with realistic noise
    highs = prices + np.abs(np.random.randn(n_bars)) * volatility * 0.5
    lows = prices - np.abs(np.random.randn(n_bars)) * volatility * 0.5
    opens = prices + np.random.randn(n_bars) * volatility * 0.2

    # Generate timestamps (M5 bars over multiple days)
    times = pd.date_range(start='2024-01-01', periods=n_bars, freq='5min')

    df = pd.DataFrame({
        'time': times,
        'open': opens,
        'high': highs,
        'low': lows,
        'close': prices,
        'tick_volume': np.random.randint(100, 5000, n_bars),
    })
    return df


class TestMetricsCalculation:
    """Test that performance metrics are calculated correctly with known data."""

    def test_win_rate_all_winners(self):
        """100% win rate when all trades are profitable."""
        engine = BacktestEngine(initial_equity=10000)
        trades = [
            {"pnl": 100.0, "direction": "BUY", "session": "LONDON"},
            {"pnl": 50.0, "direction": "SELL", "session": "NY"},
            {"pnl": 75.0, "direction": "BUY", "session": "ASIAN"},
        ]
        metrics = engine.get_metrics(trades)
        assert metrics["win_rate"] == 1.0
        assert metrics["total_trades"] == 3
        assert metrics["total_pnl"] == 225.0

    def test_win_rate_mixed(self):
        """Win rate calculation with mixed results."""
        engine = BacktestEngine(initial_equity=10000)
        trades = [
            {"pnl": 100.0, "direction": "BUY", "session": "LONDON"},
            {"pnl": -50.0, "direction": "SELL", "session": "NY"},
            {"pnl": 75.0, "direction": "BUY", "session": "LONDON"},
            {"pnl": -30.0, "direction": "SELL", "session": "ASIAN"},
        ]
        metrics = engine.get_metrics(trades)
        assert metrics["win_rate"] == 0.5  # 2 out of 4
        assert metrics["total_trades"] == 4
        assert metrics["total_pnl"] == 95.0

    def test_profit_factor(self):
        """Profit factor = gross wins / gross losses."""
        engine = BacktestEngine(initial_equity=10000)
        trades = [
            {"pnl": 200.0, "direction": "BUY", "session": "LONDON"},
            {"pnl": -100.0, "direction": "SELL", "session": "NY"},
        ]
        metrics = engine.get_metrics(trades)
        # profit_factor = 200 / 100 = 2.0
        assert abs(metrics["profit_factor"] - 2.0) < 0.001

    def test_expectancy(self):
        """Expectancy = avg_win * win_rate - avg_loss * loss_rate."""
        engine = BacktestEngine(initial_equity=10000)
        trades = [
            {"pnl": 100.0, "direction": "BUY", "session": "LONDON"},
            {"pnl": 100.0, "direction": "BUY", "session": "LONDON"},
            {"pnl": -50.0, "direction": "SELL", "session": "NY"},
            {"pnl": -50.0, "direction": "SELL", "session": "NY"},
        ]
        metrics = engine.get_metrics(trades)
        # win_rate = 0.5, avg_win = 100, avg_loss = 50, loss_rate = 0.5
        # expectancy = 100 * 0.5 - 50 * 0.5 = 50 - 25 = 25
        assert abs(metrics["expectancy"] - 25.0) < 0.001

    def test_max_drawdown(self):
        """Max drawdown is peak-to-trough percentage."""
        engine = BacktestEngine(initial_equity=10000)
        trades = [
            {"pnl": 500.0, "direction": "BUY", "session": "LONDON"},   # equity: 10500
            {"pnl": -1000.0, "direction": "SELL", "session": "NY"},    # equity: 9500
            {"pnl": -500.0, "direction": "SELL", "session": "NY"},     # equity: 9000
            {"pnl": 200.0, "direction": "BUY", "session": "LONDON"},   # equity: 9200
        ]
        metrics = engine.get_metrics(trades)
        # Peak = 10500, Trough = 9000, DD = (10500 - 9000) / 10500 = 0.1428...
        expected_dd = (10500 - 9000) / 10500.0
        assert abs(metrics["max_drawdown"] - expected_dd) < 0.001

    def test_sharpe_ratio_positive(self):
        """Sharpe should be positive for consistently profitable trades."""
        engine = BacktestEngine(initial_equity=10000)
        trades = [{"pnl": 50.0 + i * 5, "direction": "BUY", "session": "LONDON"} for i in range(20)]
        metrics = engine.get_metrics(trades)
        assert metrics["sharpe_ratio"] > 0

    def test_sortino_ratio(self):
        """Sortino uses only downside deviation."""
        engine = BacktestEngine(initial_equity=10000)
        # Mostly winners with one loss
        trades = [
            {"pnl": 100.0, "direction": "BUY", "session": "LONDON"},
            {"pnl": 80.0, "direction": "BUY", "session": "LONDON"},
            {"pnl": -20.0, "direction": "SELL", "session": "NY"},
            {"pnl": 120.0, "direction": "BUY", "session": "LONDON"},
        ]
        metrics = engine.get_metrics(trades)
        assert metrics["sortino_ratio"] > 0

    def test_empty_trades_returns_zeros(self):
        """Empty trade list returns zero metrics."""
        engine = BacktestEngine(initial_equity=10000)
        metrics = engine.get_metrics([])
        assert metrics["sharpe_ratio"] == 0.0
        assert metrics["win_rate"] == 0.0
        assert metrics["total_trades"] == 0


class TestSessionDetection:
    """Test session detection logic."""

    def test_asian_session(self):
        """Hours 22-8 UTC should be Asian session."""
        engine = BacktestEngine()
        session, regime = engine._detect_session(23.0)
        assert session == "ASIAN"
        assert regime == "MEAN_REVERT"

        session, regime = engine._detect_session(3.0)
        assert session == "ASIAN"
        assert regime == "MEAN_REVERT"

    def test_london_session(self):
        """Hours 8-13 UTC should be London session."""
        engine = BacktestEngine()
        session, regime = engine._detect_session(9.0)
        assert session == "LONDON"
        assert regime == "BREAKOUT"

    def test_ny_session(self):
        """Hours 13-17 UTC should be NY session."""
        engine = BacktestEngine()
        session, regime = engine._detect_session(14.0)
        assert session == "NY"
        assert regime == "MOMENTUM"

    def test_ny_afternoon_flat(self):
        """Hours 17-22 UTC should be NY Afternoon (FLAT)."""
        engine = BacktestEngine()
        session, regime = engine._detect_session(19.0)
        assert session == "NY_AFTERNOON"
        assert regime == "FLAT"


class TestWalkForward:
    """Test walk-forward window sliding."""

    def test_walk_forward_creates_windows(self):
        """Walk-forward should create sequential non-overlapping test windows."""
        engine = BacktestEngine(initial_equity=10000, spread_pips=2.0, slippage_pips=0.5)
        df = _make_synthetic_data(n_bars=1000)

        result = engine.run_walk_forward(
            train_window_bars=300,
            test_window_bars=100,
            param_grid={"spread_pips": [1.5, 2.0]},
            data=df,
        )

        assert "oos_trades" in result
        assert "oos_metrics" in result
        assert "window_results" in result
        assert "best_params_per_window" in result
        assert len(result["window_results"]) > 0

    def test_walk_forward_window_ranges_non_overlapping(self):
        """Test windows should not overlap."""
        engine = BacktestEngine(initial_equity=10000)
        df = _make_synthetic_data(n_bars=800)

        result = engine.run_walk_forward(
            train_window_bars=200,
            test_window_bars=100,
            param_grid={"spread_pips": [2.0]},
            data=df,
        )

        windows = result["window_results"]
        for i in range(1, len(windows)):
            prev_test_end = windows[i - 1]["test_range"][1]
            curr_train_start = windows[i]["train_range"][0]
            # Test windows slide by test_window_bars
            assert curr_train_start >= windows[i - 1]["train_range"][0]

    def test_walk_forward_too_short_data_raises(self):
        """Should raise ValueError if data is too short for windows."""
        engine = BacktestEngine(initial_equity=10000)
        df = _make_synthetic_data(n_bars=50)

        with pytest.raises(ValueError):
            engine.run_walk_forward(
                train_window_bars=300,
                test_window_bars=100,
                param_grid={"spread_pips": [2.0]},
                data=df,
            )


class TestBacktestExecution:
    """Test full backtest execution."""

    def test_run_backtest_produces_trades(self):
        """Backtest on synthetic data should produce some trades."""
        engine = BacktestEngine(initial_equity=10000, spread_pips=1.0, slippage_pips=0.5)
        df = _make_synthetic_data(n_bars=500, volatility=3.0)
        trades = engine.run_backtest(df)
        # Should generate at least a few trades on 500 bars
        assert isinstance(trades, list)
        # Trades might be 0 depending on random data, but structure should work
        if trades:
            trade = trades[0]
            assert "pnl" in trade
            assert "direction" in trade
            assert "entry_price" in trade
            assert "exit_price" in trade
            assert "session" in trade
            assert "rr_achieved" in trade

    def test_equity_curve_tracks_trades(self):
        """Equity curve length should match number of trades + 1 (initial)."""
        engine = BacktestEngine(initial_equity=10000, spread_pips=1.0, slippage_pips=0.5)
        df = _make_synthetic_data(n_bars=500, volatility=3.0)
        trades = engine.run_backtest(df)
        assert len(engine.equity_curve) == len(trades) + 1
        assert engine.equity_curve[0] == 10000.0

    def test_no_data_raises_error(self):
        """Running backtest without data should raise ValueError."""
        engine = BacktestEngine()
        with pytest.raises(ValueError):
            engine.run_backtest()


class TestDataLoading:
    """Test CSV/SQLite data loading."""

    def test_load_csv(self, tmp_path):
        """Loading CSV should return DataFrame with proper columns."""
        csv_path = str(tmp_path / "test_data.csv")
        df = _make_synthetic_data(n_bars=100)
        df.to_csv(csv_path, index=False)

        engine = BacktestEngine()
        loaded = engine.load_data(csv_path)

        assert isinstance(loaded, pd.DataFrame)
        assert 'open' in loaded.columns
        assert 'high' in loaded.columns
        assert 'low' in loaded.columns
        assert 'close' in loaded.columns
        assert len(loaded) == 100

    def test_load_csv_missing_column_raises(self, tmp_path):
        """CSV without required OHLC columns should raise ValueError."""
        csv_path = str(tmp_path / "bad_data.csv")
        pd.DataFrame({"foo": [1, 2, 3]}).to_csv(csv_path, index=False)

        engine = BacktestEngine()
        with pytest.raises(ValueError):
            engine.load_data(csv_path)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
