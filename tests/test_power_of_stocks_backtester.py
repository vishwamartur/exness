from datetime import date
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

from strategy.power_of_stocks_backtester import BacktestSettings, _resolve_mt5_symbol, backtest_on_candles


def test_ema5_breakout_generates_target_winner():
    candles = pd.DataFrame(
        [
            {"time": "2024-01-02 09:20:00+05:30", "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "ema_5": 100.0},
            {"time": "2024-01-02 09:25:00+05:30", "open": 99.2, "high": 99.5, "low": 98.6, "close": 99.0, "ema_5": 99.8},
            {"time": "2024-01-02 09:30:00+05:30", "open": 94.8, "high": 95.0, "low": 94.0, "close": 94.5, "ema_5": 96.0},
            {"time": "2024-01-02 09:35:00+05:30", "open": 94.9, "high": 95.3, "low": 94.7, "close": 95.1, "ema_5": 95.1},
            {"time": "2024-01-02 09:40:00+05:30", "open": 95.6, "high": 98.3, "low": 95.4, "close": 97.9, "ema_5": 95.4},
        ]
    )

    settings_obj = BacktestSettings(
        symbol="NIFTY_TEST",
        strategy="ema5_breakout",
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 2),
        timeframe="M5",
        reward_risk=3.0,
        entry_window_bars=3,
        initial_capital=100000.0,
        risk_per_trade=1000.0,
    )

    result = backtest_on_candles(candles, settings_obj)

    assert result["summary"]["total_trades"] == 1
    assert result["summary"]["wins"] == 1
    assert result["summary"]["win_rate"] == 100.0
    assert result["summary"]["net_profit"] == 3000.0
    assert result["trades"][0]["direction"] == "LONG"
    assert result["trades"][0]["exit_reason"] == "target"
    assert result["trades"][0]["result_r"] == 3.0


def test_traffic_light_breakout_generates_stop_loss():
    candles = pd.DataFrame(
        [
            {"time": "2024-01-03 09:20:00+05:30", "open": 100.0, "high": 101.0, "low": 100.0, "close": 101.0},
            {"time": "2024-01-03 09:25:00+05:30", "open": 101.0, "high": 101.0, "low": 100.5, "close": 100.5},
            {"time": "2024-01-03 09:30:00+05:30", "open": 100.4, "high": 100.8, "low": 99.9, "close": 100.1},
            {"time": "2024-01-03 09:35:00+05:30", "open": 100.7, "high": 101.2, "low": 100.3, "close": 100.9},
        ]
    )

    settings_obj = BacktestSettings(
        symbol="BANKNIFTY_TEST",
        strategy="traffic_light",
        start_date=date(2024, 1, 3),
        end_date=date(2024, 1, 3),
        timeframe="M5",
        reward_risk=3.0,
        entry_window_bars=3,
        initial_capital=100000.0,
        risk_per_trade=1000.0,
    )

    result = backtest_on_candles(candles, settings_obj)

    assert result["summary"]["total_trades"] == 1
    assert result["summary"]["losses"] == 1
    assert result["summary"]["net_profit"] == -1000.0
    assert result["summary"]["max_drawdown"] == 1000.0
    assert result["trades"][0]["direction"] == "SHORT"
    assert result["trades"][0]["exit_reason"] == "stop"
    assert result["trades"][0]["result_r"] == -1.0


def test_symbol_resolution_handles_suffixes_and_typo_alias():
    class FakeEntry:
        def __init__(self, name, description=''):
            self.name = name
            self.description = description

    class FakeMT5:
        @staticmethod
        def symbols_get():
            return [
                FakeEntry('XAUUSDm', 'Gold vs US Dollar'),
                FakeEntry('EURUSDm', 'Euro vs US Dollar'),
            ]

    resolved_symbol, warning = _resolve_mt5_symbol('XADUSD', FakeMT5)

    assert resolved_symbol == 'XAUUSDm'
    assert warning is not None
