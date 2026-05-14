"""
BacktestEngine - Walk-Forward Backtesting for Session Regime Strategy.

Loads historical M5/H1 OHLCV data, simulates the session regime strategy
with realistic spread/slippage, tracks equity curve and drawdowns,
supports walk-forward optimization, and outputs comprehensive performance metrics.
"""

import os
import sys
import logging
import itertools
import sqlite3
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple, Any

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import settings

logger = logging.getLogger("BacktestEngine")


class BacktestEngine:
    """Walk-forward backtesting engine for XAUUSD session regime strategy."""

    def __init__(
        self,
        spread_pips: float = None,
        slippage_pips: float = None,
        initial_equity: float = None,
        risk_percent: float = None,
    ):
        self.spread_pips = spread_pips if spread_pips is not None else getattr(
            settings, 'BACKTEST_SPREAD_PIPS', 2.0)
        self.slippage_pips = slippage_pips if slippage_pips is not None else getattr(
            settings, 'BACKTEST_SLIPPAGE_PIPS', 0.5)
        self.initial_equity = initial_equity if initial_equity is not None else getattr(
            settings, 'BACKTEST_INITIAL_EQUITY', 10000.0)
        self.risk_percent = risk_percent if risk_percent is not None else getattr(
            settings, 'BACKTEST_RISK_PERCENT', 1.0)

        # For XAUUSD, 1 pip = $0.01 per unit
        self.pip_value = 0.01

        # State
        self.data: Optional[pd.DataFrame] = None
        self.trades: List[Dict[str, Any]] = []
        self.equity_curve: List[float] = []
        self._reset()

    def _reset(self):
        """Reset internal state for a fresh backtest run."""
        self.trades = []
        self.equity_curve = [self.initial_equity]

    # ─── Data Loading ─────────────────────────────────────────────────────

    def load_data(self, source: str, timeframe: str = "M5", **kwargs) -> pd.DataFrame:
        """Load historical OHLCV data from CSV or SQLite.

        Args:
            source: File path to CSV or SQLite database.
            timeframe: Timeframe string (M5, H1, etc.) used as table name for SQLite.
            **kwargs: Additional arguments (e.g., table_name for SQLite).

        Returns:
            DataFrame with columns: time, open, high, low, close, tick_volume
        """
        if source.endswith('.csv'):
            df = self._load_csv(source)
        elif source.endswith('.db') or source.endswith('.sqlite'):
            table = kwargs.get('table_name', timeframe)
            df = self._load_sqlite(source, table)
        else:
            raise ValueError(f"Unsupported file format: {source}")

        self.data = df
        return df

    def _load_csv(self, path: str) -> pd.DataFrame:
        """Load OHLCV data from CSV file."""
        df = pd.read_csv(path)
        df.columns = [c.lower().strip() for c in df.columns]

        required = ['open', 'high', 'low', 'close']
        for col in required:
            if col not in df.columns:
                raise ValueError(f"CSV missing required column: {col}")

        if 'time' not in df.columns and 'datetime' in df.columns:
            df.rename(columns={'datetime': 'time'}, inplace=True)
        if 'time' in df.columns:
            df['time'] = pd.to_datetime(df['time'])

        if 'tick_volume' not in df.columns and 'volume' in df.columns:
            df.rename(columns={'volume': 'tick_volume'}, inplace=True)
        if 'tick_volume' not in df.columns:
            df['tick_volume'] = 0

        return df.reset_index(drop=True)

    def _load_sqlite(self, path: str, table: str) -> pd.DataFrame:
        """Load OHLCV data from SQLite database.

        Args:
            path: Path to the SQLite database file.
            table: Table name. Must contain only alphanumeric characters and underscores.

        Raises:
            ValueError: If table name contains invalid characters.
        """
        import re
        if not re.match(r'^[A-Za-z0-9_]+$', table):
            raise ValueError(
                f"Invalid table name '{table}': only alphanumeric characters "
                f"and underscores are allowed."
            )
        conn = sqlite3.connect(path)
        df = pd.read_sql_query(f"SELECT * FROM {table}", conn)
        conn.close()

        df.columns = [c.lower().strip() for c in df.columns]
        if 'time' in df.columns:
            df['time'] = pd.to_datetime(df['time'])

        return df.reset_index(drop=True)

    # ─── Session Detection ────────────────────────────────────────────────

    def _detect_session(self, hour: float) -> Tuple[str, str]:
        """Detect session and regime based on UTC hour.

        Returns:
            Tuple of (session_name, regime_type)
        """
        asian_start = getattr(settings, 'ASIAN_SESSION_START', 22.0)
        asian_end = getattr(settings, 'ASIAN_SESSION_END', 8.0)
        london_start = getattr(settings, 'LONDON_SESSION_START', 8.0)
        london_end = getattr(settings, 'LONDON_SESSION_END', 13.0)
        ny_start = getattr(settings, 'NY_SESSION_START', 13.0)
        ny_end = getattr(settings, 'NY_SESSION_END', 17.0)

        # Asian wraps midnight
        if hour >= asian_start or hour < asian_end:
            return "ASIAN", "MEAN_REVERT"
        elif london_start <= hour < london_end:
            return "LONDON", "BREAKOUT"
        elif ny_start <= hour < ny_end:
            return "NY", "MOMENTUM"
        else:
            return "NY_AFTERNOON", "FLAT"

    # ─── Technical Indicators ─────────────────────────────────────────────

    @staticmethod
    def _calc_rsi(close: np.ndarray, period: int = 14) -> float:
        """Calculate RSI from close prices."""
        if len(close) < period + 1:
            return 50.0
        deltas = np.diff(close)
        gain = np.where(deltas > 0, deltas, 0)
        loss = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gain[-period:])
        avg_loss = np.mean(loss[-period:])
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def _calc_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
        """Calculate ATR."""
        if len(close) < period + 1:
            return 0.0
        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1])
            )
        )
        return float(np.mean(tr[-period:]))

    @staticmethod
    def _calc_ema(data: np.ndarray, period: int) -> float:
        """Calculate EMA (last value)."""
        if len(data) < period:
            return float(data[-1]) if len(data) > 0 else 0.0
        weights = np.exp(np.linspace(-1., 0., period))
        weights /= weights.sum()
        ema = np.convolve(data, weights, mode='valid')
        return float(ema[-1]) if len(ema) > 0 else float(data[-1])

    @staticmethod
    def _calc_macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[float, float]:
        """Calculate MACD and signal line."""
        def ema_series(data, n):
            a = 2.0 / (n + 1)
            result = np.zeros_like(data, dtype=float)
            result[0] = data[0]
            for i in range(1, len(data)):
                result[i] = a * data[i] + (1 - a) * result[i - 1]
            return result

        if len(close) < slow:
            return 0.0, 0.0
        ema_fast = ema_series(close, fast)
        ema_slow = ema_series(close, slow)
        macd_line = ema_fast - ema_slow
        signal_line = ema_series(macd_line, signal)
        return float(macd_line[-1]), float(signal_line[-1])

    # ─── Signal Generation ────────────────────────────────────────────────

    def _generate_signal(
        self, idx: int, df: pd.DataFrame, session: str, regime: str
    ) -> Optional[Dict[str, Any]]:
        """Generate signal at bar index using session regime logic.

        Mirrors the logic from SessionStrategyService._generate_signal.
        """
        lookback = 50
        if idx < lookback:
            return None

        close = df['close'].values[max(0, idx - lookback):idx + 1]
        high = df['high'].values[max(0, idx - lookback):idx + 1]
        low = df['low'].values[max(0, idx - lookback):idx + 1]
        current_price = float(df['close'].values[idx])

        rsi = self._calc_rsi(close, 14)
        atr = self._calc_atr(high, low, close, 14)
        ema20 = self._calc_ema(close, 20)
        ema50 = self._calc_ema(close, min(50, len(close)))
        macd, signal_line = self._calc_macd(close)

        if atr <= 0:
            return None

        # Asian: Mean Reversion
        if regime == "MEAN_REVERT":
            if rsi > 70:
                return {"direction": "SELL", "sl_distance": atr * 1.5,
                        "tp_distance": atr * 3.0, "session": session}
            elif rsi < 30:
                return {"direction": "BUY", "sl_distance": atr * 1.5,
                        "tp_distance": atr * 3.0, "session": session}

        # London: Breakout
        elif regime == "BREAKOUT":
            # Use recent Asian range (last 12 bars ~ 1 hour lookback on M5)
            asian_lookback = min(24, idx)
            recent_high = float(np.max(high[-asian_lookback:]))
            recent_low = float(np.min(low[-asian_lookback:]))
            breakout_buffer = atr * 0.5

            if (current_price > recent_high + breakout_buffer
                    and macd > signal_line and rsi > 50 and rsi < 80):
                return {"direction": "BUY", "sl_distance": atr * 2.0,
                        "tp_distance": atr * 6.0, "session": session}
            elif (current_price < recent_low - breakout_buffer
                  and macd < signal_line and rsi > 20 and rsi < 50):
                return {"direction": "SELL", "sl_distance": atr * 2.0,
                        "tp_distance": atr * 6.0, "session": session}

        # NY: Momentum / Trend Continuation
        elif regime == "MOMENTUM":
            ema_trend_up = ema20 > ema50 and current_price > ema20
            ema_trend_down = ema20 < ema50 and current_price < ema20

            if ema_trend_up and macd > signal_line and 45 < rsi < 70:
                return {"direction": "BUY", "sl_distance": atr * 1.5,
                        "tp_distance": atr * 5.0, "session": session}
            elif ema_trend_down and macd < signal_line and 30 < rsi < 55:
                return {"direction": "SELL", "sl_distance": atr * 1.5,
                        "tp_distance": atr * 5.0, "session": session}

        return None

    # ─── Backtest Execution ───────────────────────────────────────────────

    def run_backtest(self, data: pd.DataFrame = None) -> List[Dict[str, Any]]:
        """Run backtest on loaded data or provided DataFrame.

        Iterates through bars, generates signals, simulates fills with
        spread/slippage, and tracks P&L.

        Returns:
            List of trade results.
        """
        if data is not None:
            self.data = data

        if self.data is None or self.data.empty:
            raise ValueError("No data loaded. Call load_data() first or pass data to run_backtest().")

        self._reset()
        df = self.data
        equity = self.initial_equity
        cooldown = 0

        for idx in range(50, len(df)):
            if cooldown > 0:
                cooldown -= 1
                continue

            # Determine session/regime from time or index
            if 'time' in df.columns and df['time'].iloc[idx] is not None:
                try:
                    ts = pd.Timestamp(df['time'].iloc[idx])
                    hour = ts.hour + ts.minute / 60.0
                except Exception:
                    hour = (idx % 288) / 12.0  # Synthetic hour from bar index
            else:
                hour = (idx % 288) / 12.0

            session, regime = self._detect_session(hour)

            if regime == "FLAT":
                continue

            signal = self._generate_signal(idx, df, session, regime)
            if signal is None:
                continue

            # Simulate trade execution
            trade = self._execute_trade(signal, idx, df, equity)
            if trade is not None:
                equity += trade['pnl']
                self.equity_curve.append(equity)
                self.trades.append(trade)
                cooldown = 3  # Skip 3 bars after trade (cooldown)

        return self.trades

    def _execute_trade(
        self, signal: Dict, entry_idx: int, df: pd.DataFrame, equity: float
    ) -> Optional[Dict[str, Any]]:
        """Simulate a single trade with spread and slippage.

        Returns trade result dict or None if trade cannot be placed.
        """
        direction = signal['direction']
        sl_distance = signal['sl_distance']
        tp_distance = signal['tp_distance']
        session = signal.get('session', 'UNKNOWN')

        entry_price = float(df['close'].values[entry_idx])

        # Apply spread and slippage costs
        total_cost_pips = self.spread_pips + self.slippage_pips
        cost = total_cost_pips * self.pip_value

        if direction == "BUY":
            entry_price += cost / 2  # Worse fill for buys
        else:
            entry_price -= cost / 2  # Worse fill for sells

        # Position sizing based on risk percent
        risk_amount = equity * (self.risk_percent / 100.0)
        if sl_distance <= 0:
            return None
        position_size = risk_amount / sl_distance

        # Simulate trade outcome using future bars
        max_bars = min(50, len(df) - entry_idx - 1)
        if max_bars <= 0:
            return None

        sl_price = entry_price - sl_distance if direction == "BUY" else entry_price + sl_distance
        tp_price = entry_price + tp_distance if direction == "BUY" else entry_price - tp_distance

        exit_price = None
        exit_reason = "timeout"
        exit_idx = entry_idx + max_bars

        for i in range(1, max_bars + 1):
            bar_idx = entry_idx + i
            bar_high = float(df['high'].values[bar_idx])
            bar_low = float(df['low'].values[bar_idx])

            if direction == "BUY":
                if bar_low <= sl_price:
                    exit_price = sl_price
                    exit_reason = "stop_loss"
                    exit_idx = bar_idx
                    break
                elif bar_high >= tp_price:
                    exit_price = tp_price
                    exit_reason = "take_profit"
                    exit_idx = bar_idx
                    break
            else:  # SELL
                if bar_high >= sl_price:
                    exit_price = sl_price
                    exit_reason = "stop_loss"
                    exit_idx = bar_idx
                    break
                elif bar_low <= tp_price:
                    exit_price = tp_price
                    exit_reason = "take_profit"
                    exit_idx = bar_idx
                    break

        if exit_price is None:
            exit_price = float(df['close'].values[exit_idx])
            exit_reason = "timeout"

        # Calculate P&L
        if direction == "BUY":
            pnl = (exit_price - entry_price) * position_size
        else:
            pnl = (entry_price - exit_price) * position_size

        rr_achieved = 0.0
        if sl_distance > 0:
            if direction == "BUY":
                rr_achieved = (exit_price - entry_price) / sl_distance
            else:
                rr_achieved = (entry_price - exit_price) / sl_distance

        return {
            "entry_idx": entry_idx,
            "exit_idx": exit_idx,
            "direction": direction,
            "session": session,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "sl_distance": sl_distance,
            "tp_distance": tp_distance,
            "pnl": pnl,
            "rr_achieved": rr_achieved,
            "exit_reason": exit_reason,
            "position_size": position_size,
        }

    # ─── Walk-Forward Optimization ────────────────────────────────────────

    def run_walk_forward(
        self,
        train_window_bars: int,
        test_window_bars: int,
        param_grid: Dict[str, List[Any]],
        data: pd.DataFrame = None,
    ) -> Dict[str, Any]:
        """Run walk-forward optimization.

        Slides through data using train/test windows.
        Optimizes parameters on each train window, then tests on next window.

        Args:
            train_window_bars: Number of bars in training window.
            test_window_bars: Number of bars in test window.
            param_grid: Dict of parameter names to lists of values to search.
            data: Optional DataFrame (uses self.data if not provided).

        Returns:
            Dict with 'oos_trades', 'oos_metrics', 'window_results', 'best_params_per_window'.
        """
        if data is not None:
            self.data = data

        if self.data is None or self.data.empty:
            raise ValueError("No data loaded for walk-forward optimization.")

        df = self.data
        n = len(df)
        window_size = train_window_bars + test_window_bars

        if n < window_size:
            raise ValueError(
                f"Data too short ({n} bars) for window size ({window_size} bars)."
            )

        # Build windows
        windows = []
        start = 0
        while start + window_size <= n:
            train_end = start + train_window_bars
            test_end = train_end + test_window_bars
            windows.append((start, train_end, train_end, min(test_end, n)))
            start += test_window_bars  # Slide by test window size

        if not windows:
            raise ValueError("Cannot create any walk-forward windows.")

        all_param_combos = list(itertools.product(*param_grid.values()))
        param_keys = list(param_grid.keys())

        all_oos_trades = []
        window_results = []
        best_params_list = []

        for w_idx, (train_start, train_end, test_start, test_end) in enumerate(windows):
            train_df = df.iloc[train_start:train_end].reset_index(drop=True)
            test_df = df.iloc[test_start:test_end].reset_index(drop=True)

            # Optimize on train window
            best_score = -np.inf
            best_params = None

            for combo in all_param_combos:
                params = dict(zip(param_keys, combo))
                score = self._evaluate_params(train_df, params)
                if score > best_score:
                    best_score = score
                    best_params = params

            # Test on OOS window with best params
            if best_params:
                oos_trades = self._run_with_params(test_df, best_params)
            else:
                oos_trades = []

            all_oos_trades.extend(oos_trades)
            window_results.append({
                "window": w_idx,
                "train_range": (train_start, train_end),
                "test_range": (test_start, test_end),
                "best_params": best_params,
                "train_score": best_score,
                "oos_trade_count": len(oos_trades),
            })
            best_params_list.append(best_params)

        # Calculate aggregated OOS metrics
        oos_metrics = self._calculate_metrics(all_oos_trades)

        return {
            "oos_trades": all_oos_trades,
            "oos_metrics": oos_metrics,
            "window_results": window_results,
            "best_params_per_window": best_params_list,
        }

    def _evaluate_params(self, df: pd.DataFrame, params: Dict[str, Any]) -> float:
        """Evaluate a parameter set on a data segment. Returns Sharpe-like score."""
        trades = self._run_with_params(df, params)
        if not trades:
            return -999.0

        pnls = [t['pnl'] for t in trades]
        if len(pnls) < 2:
            return -999.0

        mean_pnl = np.mean(pnls)
        std_pnl = np.std(pnls)
        if std_pnl == 0:
            return -999.0

        return float(mean_pnl / std_pnl * np.sqrt(len(pnls)))

    def _run_with_params(self, df: pd.DataFrame, params: Dict[str, Any]) -> List[Dict]:
        """Run a backtest with specific parameters on a DataFrame segment."""
        # Apply params temporarily
        orig_spread = self.spread_pips
        orig_slippage = self.slippage_pips

        if 'spread_pips' in params:
            self.spread_pips = params['spread_pips']
        if 'slippage_pips' in params:
            self.slippage_pips = params['slippage_pips']

        # Store original data and run
        orig_data = self.data
        self.data = df
        trades = self.run_backtest(df)

        # Restore
        self.data = orig_data
        self.spread_pips = orig_spread
        self.slippage_pips = orig_slippage

        return trades

    # ─── Performance Metrics ──────────────────────────────────────────────

    def get_metrics(self, trades: List[Dict[str, Any]] = None) -> Dict[str, float]:
        """Calculate comprehensive performance metrics.

        Args:
            trades: List of trade dicts. Uses self.trades if not provided.

        Returns:
            Dict with: sharpe_ratio, sortino_ratio, max_drawdown, win_rate,
                       expectancy, profit_factor, total_trades, total_pnl.
        """
        if trades is None:
            trades = self.trades
        return self._calculate_metrics(trades)

    def _calculate_metrics(self, trades: List[Dict[str, Any]]) -> Dict[str, float]:
        """Internal metrics calculation from a list of trades."""
        if not trades:
            return {
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "max_drawdown": 0.0,
                "win_rate": 0.0,
                "expectancy": 0.0,
                "profit_factor": 0.0,
                "total_trades": 0,
                "total_pnl": 0.0,
            }

        pnls = np.array([t['pnl'] for t in trades])
        total_pnl = float(np.sum(pnls))
        total_trades = len(trades)

        # Win rate
        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = win_count / total_trades if total_trades > 0 else 0.0

        # Expectancy: avg_win * win_rate - avg_loss * loss_rate
        avg_win = float(np.mean(wins)) if len(wins) > 0 else 0.0
        avg_loss = float(np.abs(np.mean(losses))) if len(losses) > 0 else 0.0
        loss_rate = loss_count / total_trades if total_trades > 0 else 0.0
        expectancy = avg_win * win_rate - avg_loss * loss_rate

        # Profit Factor: gross_wins / gross_losses
        gross_wins = float(np.sum(wins)) if len(wins) > 0 else 0.0
        gross_losses = float(np.abs(np.sum(losses))) if len(losses) > 0 else 0.0
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else float('inf') if gross_wins > 0 else 0.0

        # Sharpe Ratio (annualized, assuming 252 trading days)
        mean_pnl = float(np.mean(pnls))
        std_pnl = float(np.std(pnls))
        sharpe_ratio = (mean_pnl / std_pnl) * np.sqrt(252) if std_pnl > 0 else 0.0

        # Sortino Ratio (only downside deviation)
        downside = pnls[pnls < 0]
        downside_std = float(np.std(downside)) if len(downside) > 1 else 0.0
        sortino_ratio = (mean_pnl / downside_std) * np.sqrt(252) if downside_std > 0 else 0.0

        # Max Drawdown from equity curve
        max_drawdown = self._calc_max_drawdown(trades)

        return {
            "sharpe_ratio": float(sharpe_ratio),
            "sortino_ratio": float(sortino_ratio),
            "max_drawdown": float(max_drawdown),
            "win_rate": float(win_rate),
            "expectancy": float(expectancy),
            "profit_factor": float(profit_factor),
            "total_trades": total_trades,
            "total_pnl": float(total_pnl),
        }

    def _calc_max_drawdown(self, trades: List[Dict[str, Any]] = None) -> float:
        """Calculate maximum drawdown (peak-to-trough) from trades."""
        if not trades:
            return 0.0

        equity = self.initial_equity
        peak = equity
        max_dd = 0.0

        for trade in trades:
            equity += trade['pnl']
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        return max_dd
