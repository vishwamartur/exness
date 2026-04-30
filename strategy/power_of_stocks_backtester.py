from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo
import math
import re

import numpy as np
import pandas as pd

from config import settings

StrategyName = Literal["ema5_breakout", "traffic_light"]

TIMEFRAME_MINUTES = {
    "M1": 1,
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
    "D1": 1440,
}


@dataclass(slots=True)
class BacktestSettings:
    symbol: str
    strategy: StrategyName
    start_date: date
    end_date: date
    timeframe: str = "M5"
    timezone_name: str = "Asia/Kolkata"
    reward_risk: float = 3.0
    entry_window_bars: int = 3
    initial_capital: float = 100000.0
    risk_per_trade: float = 1000.0
    session_start: str | None = "09:20"
    session_end: str | None = "15:15"
    exclude_start: str | None = None
    exclude_end: str | None = None
    square_off_time: str | None = "15:20"
    enter_on_close: bool = False
    allow_long: bool = True
    allow_short: bool = True
    ema_period: int = 5
    max_trades_per_day: int = 10
    traffic_light_max_range: float | None = None
    same_bar_exit_priority: Literal["stop", "target"] = "stop"

    def __post_init__(self) -> None:
        self.strategy = str(self.strategy)
        self.timeframe = str(self.timeframe).upper()
        if self.strategy not in {"ema5_breakout", "traffic_light"}:
            raise ValueError(f"Unsupported strategy: {self.strategy}")
        if self.timeframe not in TIMEFRAME_MINUTES:
            raise ValueError(f"Unsupported timeframe: {self.timeframe}")
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.entry_window_bars < 1:
            raise ValueError("entry_window_bars must be >= 1")
        if self.reward_risk <= 0:
            raise ValueError("reward_risk must be > 0")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be > 0")
        if self.risk_per_trade <= 0:
            raise ValueError("risk_per_trade must be > 0")
        if self.max_trades_per_day < 1:
            raise ValueError("max_trades_per_day must be >= 1")
        if self.traffic_light_max_range is not None and self.traffic_light_max_range <= 0:
            raise ValueError("traffic_light_max_range must be > 0 when provided")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone_name)


@dataclass(slots=True)
class PendingSetup:
    strategy: StrategyName
    direction: Literal["LONG", "SHORT", "BOTH"]
    setup_index: int
    setup_time: pd.Timestamp
    expires_at_index: int
    trigger_price: float | None = None
    stop_price: float | None = None
    trigger_high: float | None = None
    trigger_low: float | None = None
    reference_close: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OpenTrade:
    strategy: StrategyName
    symbol: str
    direction: Literal["LONG", "SHORT"]
    entry_index: int
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: float
    target_price: float
    risk_points: float
    position_size: float
    setup_time: pd.Timestamp
    setup_index: int
    metadata: dict[str, Any] = field(default_factory=dict)


def run_backtest(settings_obj: BacktestSettings) -> dict[str, Any]:
    candles = fetch_mt5_candles(
        symbol=settings_obj.symbol,
        start_date=settings_obj.start_date,
        end_date=settings_obj.end_date,
        timeframe=settings_obj.timeframe,
        timezone_name=settings_obj.timezone_name,
    )
    resolved_symbol = candles.attrs.get("resolved_symbol")
    if resolved_symbol:
        settings_obj.symbol = resolved_symbol
    return backtest_on_candles(candles, settings_obj)


def backtest_on_candles(candles: pd.DataFrame, settings_obj: BacktestSettings) -> dict[str, Any]:
    df = _prepare_candles(candles, settings_obj)
    if df.empty:
        empty_warnings = list(candles.attrs.get("warnings", []))
        empty_warnings.append("No candles returned for the requested range.")
        return _empty_payload(settings_obj, warnings_list=empty_warnings)

    if settings_obj.strategy == "ema5_breakout" and "ema_5" not in df.columns:
        df["ema_5"] = df["close"].ewm(span=settings_obj.ema_period, adjust=False).mean()

    open_trades: list[OpenTrade] = []
    closed_trades: list[dict[str, Any]] = []
    equity_points: list[dict[str, Any]] = []
    warnings_list: list[str] = list(candles.attrs.get("warnings", []))
    daily_trade_count: dict[date, int] = {}
    equity = settings_obj.initial_capital
    peak_equity = equity

    ema_setups: dict[str, PendingSetup | None] = {"LONG": None, "SHORT": None}
    traffic_setup: PendingSetup | None = None

    previous_row: pd.Series | None = None
    active_day: date | None = None

    for index, row in df.iterrows():
        candle_time = row["time"]
        candle_day = candle_time.date()

        if active_day is None:
            active_day = candle_day
        elif candle_day != active_day:
            if previous_row is not None and open_trades:
                forced_trades, equity = _close_all_trades(
                    open_trades,
                    exit_time=previous_row["time"],
                    exit_price=float(previous_row["close"]),
                    reason="session_rollover",
                    risk_per_trade=settings_obj.risk_per_trade,
                    equity=equity,
                    peak_equity=peak_equity,
                    exit_index=int(previous_row.name),
                )
                peak_equity = max([peak_equity, *(trade["equity_after"] for trade in forced_trades)], default=peak_equity)
                closed_trades.extend(forced_trades)
                equity_points.extend(_equity_points_from_trades(forced_trades))
                open_trades = []
            ema_setups = {"LONG": None, "SHORT": None}
            traffic_setup = None
            active_day = candle_day

        closed_now, still_open = _resolve_open_trades(
            open_trades,
            row=row,
            risk_per_trade=settings_obj.risk_per_trade,
            same_bar_exit_priority=settings_obj.same_bar_exit_priority,
        )
        if closed_now:
            for trade in closed_now:
                equity += trade["pnl"]
                peak_equity = max(peak_equity, equity)
                trade["equity_after"] = equity
                trade["drawdown"] = equity - peak_equity
                trade["drawdown_pct"] = ((equity - peak_equity) / peak_equity * 100.0) if peak_equity else 0.0
            closed_trades.extend(closed_now)
            equity_points.extend(_equity_points_from_trades(closed_now))
        open_trades = still_open

        if _should_square_off(candle_time, settings_obj):
            if open_trades:
                forced_trades, equity = _close_all_trades(
                    open_trades,
                    exit_time=candle_time,
                    exit_price=float(row["close"]),
                    reason="square_off",
                    risk_per_trade=settings_obj.risk_per_trade,
                    equity=equity,
                    peak_equity=peak_equity,
                    exit_index=index,
                )
                peak_equity = max([peak_equity, *(trade["equity_after"] for trade in forced_trades)], default=peak_equity)
                closed_trades.extend(forced_trades)
                equity_points.extend(_equity_points_from_trades(forced_trades))
                open_trades = []
            previous_row = row
            continue

        if settings_obj.strategy == "ema5_breakout":
            for direction in ("LONG", "SHORT"):
                setup = ema_setups[direction]
                created_trade = None
                setup, created_trade = _maybe_open_from_single_direction_setup(
                    setup=setup,
                    row=row,
                    settings_obj=settings_obj,
                    symbol=settings_obj.symbol,
                    daily_trade_count=daily_trade_count,
                )
                ema_setups[direction] = setup
                if created_trade is not None:
                    open_trades.append(created_trade)
                if created_trade is not None and created_trade.entry_index == index:
                    intrabar_closed, intrabar_still_open = _resolve_open_trades(
                        open_trades,
                        row=row,
                        risk_per_trade=settings_obj.risk_per_trade,
                        same_bar_exit_priority=settings_obj.same_bar_exit_priority,
                        entered_trade_ids={id(created_trade)},
                    )
                    if intrabar_closed:
                        for trade in intrabar_closed:
                            equity += trade["pnl"]
                            peak_equity = max(peak_equity, equity)
                            trade["equity_after"] = equity
                            trade["drawdown"] = equity - peak_equity
                            trade["drawdown_pct"] = ((equity - peak_equity) / peak_equity * 100.0) if peak_equity else 0.0
                        closed_trades.extend(intrabar_closed)
                        equity_points.extend(_equity_points_from_trades(intrabar_closed))
                    open_trades = intrabar_still_open

            if _can_scan_bar(candle_time, settings_obj):
                ema_setups = _update_ema_setups(ema_setups, row, index, settings_obj)

        elif settings_obj.strategy == "traffic_light":
            created_trade = None
            traffic_setup, created_trade = _maybe_open_from_traffic_setup(
                setup=traffic_setup,
                row=row,
                settings_obj=settings_obj,
                symbol=settings_obj.symbol,
                daily_trade_count=daily_trade_count,
            )
            if created_trade is not None:
                open_trades.append(created_trade)
                intrabar_closed, intrabar_still_open = _resolve_open_trades(
                    open_trades,
                    row=row,
                    risk_per_trade=settings_obj.risk_per_trade,
                    same_bar_exit_priority=settings_obj.same_bar_exit_priority,
                    entered_trade_ids={id(created_trade)},
                )
                if intrabar_closed:
                    for trade in intrabar_closed:
                        equity += trade["pnl"]
                        peak_equity = max(peak_equity, equity)
                        trade["equity_after"] = equity
                        trade["drawdown"] = equity - peak_equity
                        trade["drawdown_pct"] = ((equity - peak_equity) / peak_equity * 100.0) if peak_equity else 0.0
                    closed_trades.extend(intrabar_closed)
                    equity_points.extend(_equity_points_from_trades(intrabar_closed))
                open_trades = intrabar_still_open

            if previous_row is not None and _can_scan_bar(candle_time, settings_obj):
                traffic_setup = _maybe_create_traffic_setup(previous_row, row, index, settings_obj)

        previous_row = row

    if open_trades and previous_row is not None:
        forced_trades, equity = _close_all_trades(
            open_trades,
            exit_time=previous_row["time"],
            exit_price=float(previous_row["close"]),
            reason="range_end",
            risk_per_trade=settings_obj.risk_per_trade,
            equity=equity,
            peak_equity=peak_equity,
            exit_index=int(previous_row.name),
        )
        peak_equity = max([peak_equity, *(trade["equity_after"] for trade in forced_trades)], default=peak_equity)
        closed_trades.extend(forced_trades)
        equity_points.extend(_equity_points_from_trades(forced_trades))

    if not closed_trades:
        warnings_list.append("No trades matched the current rules and time filters.")

    summary = _calculate_summary(closed_trades, settings_obj)

    return {
        "meta": {
            "symbol": settings_obj.symbol,
            "requested_symbol": candles.attrs.get("requested_symbol", settings_obj.symbol),
            "resolved_symbol": candles.attrs.get("resolved_symbol", settings_obj.symbol),
            "strategy": settings_obj.strategy,
            "timeframe": settings_obj.timeframe,
            "timezone": settings_obj.timezone_name,
            "bars": int(len(df)),
            "start": settings_obj.start_date.isoformat(),
            "end": settings_obj.end_date.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "parameters": _serialize_settings(settings_obj),
        },
        "summary": summary,
        "equity_curve": _equity_curve_payload(equity_points, settings_obj.initial_capital),
        "daily": _aggregate_performance(closed_trades, bucket="date"),
        "monthly": _aggregate_performance(closed_trades, bucket="month"),
        "trades": closed_trades,
        "warnings": warnings_list,
    }


def fetch_mt5_candles(
    symbol: str,
    start_date: date,
    end_date: date,
    timeframe: str = "M5",
    timezone_name: str = "Asia/Kolkata",
) -> pd.DataFrame:
    import MetaTrader5 as mt5

    timeframe_const = _mt5_timeframe_constant(timeframe, mt5)
    _ensure_mt5_connection(mt5)
    resolved_symbol, resolution_warning = _resolve_mt5_symbol(symbol, mt5)

    cache_dir = Path(__file__).resolve().parents[1] / "data_cache" / "backtests"
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_symbol = re.sub(r"[^A-Za-z0-9_.-]+", "_", resolved_symbol.strip())
    cache_path = cache_dir / f"{safe_symbol}_{timeframe}_{start_date.isoformat()}_{end_date.isoformat()}_{timezone_name.replace('/', '-')}.pkl"
    if cache_path.exists():
        cached = pd.read_pickle(cache_path)
        cached.attrs["requested_symbol"] = symbol
        cached.attrs["resolved_symbol"] = resolved_symbol
        cached.attrs["warnings"] = [resolution_warning] if resolution_warning else []
        return cached

    tz = ZoneInfo(timezone_name)
    range_start_local = datetime.combine(start_date, time(0, 0), tzinfo=tz)
    range_end_local = datetime.combine(end_date, time(23, 59), tzinfo=tz)

    try:
        mt5.symbol_select(resolved_symbol, True)
    except Exception:
        pass

    chunk_start = range_start_local
    chunk_size = timedelta(days=45)
    frames: list[pd.DataFrame] = []

    while chunk_start <= range_end_local:
        chunk_end = min(chunk_start + chunk_size, range_end_local)
        rates = mt5.copy_rates_range(
            resolved_symbol,
            timeframe_const,
            chunk_start.astimezone(timezone.utc),
            chunk_end.astimezone(timezone.utc),
        )
        if rates is not None and len(rates) > 0:
            frames.append(pd.DataFrame(rates))
        chunk_start = chunk_end + timedelta(minutes=TIMEFRAME_MINUTES[timeframe])

    if not frames:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "tick_volume"])

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(tz)
    df.attrs["requested_symbol"] = symbol
    df.attrs["resolved_symbol"] = resolved_symbol
    df.attrs["warnings"] = [resolution_warning] if resolution_warning else []
    df.to_pickle(cache_path)
    return df


def search_symbols(query: str = "", limit: int = 20) -> list[dict[str, Any]]:
    import MetaTrader5 as mt5

    _ensure_mt5_connection(mt5)
    symbols = mt5.symbols_get()
    if not symbols:
        return []

    normalized_query = query.strip().lower()
    preferred_terms = ["xauusd", "gold", "eurusd", "euro"]
    matches: list[dict[str, Any]] = []

    for symbol in symbols:
        name = getattr(symbol, "name", "")
        description = getattr(symbol, "description", "")
        haystack = f"{name} {description}".lower()
        if normalized_query:
            if normalized_query not in haystack:
                continue
        elif not any(term in haystack for term in preferred_terms):
            continue
        matches.append(
            {
                "name": name,
                "description": description,
                "path": getattr(symbol, "path", ""),
                "currency_base": getattr(symbol, "currency_base", ""),
                "currency_profit": getattr(symbol, "currency_profit", ""),
            }
        )

    matches.sort(key=lambda item: (0 if normalized_query and item["name"].lower().startswith(normalized_query) else 1, len(item["name"])))
    return matches[:limit]


def _resolve_mt5_symbol(symbol: str, mt5_module: Any) -> tuple[str, str | None]:
    requested_symbol = symbol.strip()
    symbols = mt5_module.symbols_get() or []
    if not symbols or not requested_symbol:
        return requested_symbol, None

    alias_map = {
        "xadusd": "xauusd",
    }

    requested_key = _normalize_symbol_key(requested_symbol)
    requested_key = alias_map.get(requested_key, requested_key)

    for entry in symbols:
        name = getattr(entry, "name", "")
        if name.lower() == requested_symbol.lower():
            return name, None

    normalized_map: dict[str, str] = {}
    for entry in symbols:
        name = getattr(entry, "name", "")
        description = getattr(entry, "description", "")
        normalized_name = _normalize_symbol_key(name)
        normalized_description = _normalize_symbol_key(description)
        normalized_map[normalized_name] = name
        if normalized_name == requested_key or normalized_description == requested_key:
            warning = None if name.lower() == requested_symbol.lower() else f"Using broker symbol {name} for requested symbol {symbol}."
            return name, warning

    contains_matches: list[str] = []
    for entry in symbols:
        name = getattr(entry, "name", "")
        description = getattr(entry, "description", "")
        normalized_name = _normalize_symbol_key(name)
        normalized_description = _normalize_symbol_key(description)
        if requested_key in normalized_name or requested_key in normalized_description:
            contains_matches.append(name)

    if contains_matches:
        resolved = sorted(contains_matches, key=lambda item: (len(item), item))[0]
        return resolved, f"Using broker symbol {resolved} for requested symbol {symbol}."

    fuzzy_match = get_close_matches(requested_key, list(normalized_map.keys()), n=1, cutoff=0.72)
    if fuzzy_match:
        resolved = normalized_map[fuzzy_match[0]]
        return resolved, f"Using closest broker symbol {resolved} for requested symbol {symbol}."

    return requested_symbol, None


def _normalize_symbol_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _prepare_candles(candles: pd.DataFrame, settings_obj: BacktestSettings) -> pd.DataFrame:
    if candles is None or candles.empty:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close"])

    df = candles.copy()
    missing = {"time", "open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise ValueError(f"Candle data is missing required columns: {sorted(missing)}")

    if not pd.api.types.is_datetime64_any_dtype(df["time"]):
        df["time"] = pd.to_datetime(df["time"], utc=True).dt.tz_convert(settings_obj.tz)
    elif getattr(df["time"].dt, "tz", None) is None:
        df["time"] = df["time"].dt.tz_localize(settings_obj.tz)
    else:
        df["time"] = df["time"].dt.tz_convert(settings_obj.tz)

    numeric_columns = [column for column in ["open", "high", "low", "close"] if column in df.columns]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = df.dropna(subset=["time", "open", "high", "low", "close"])
    df = df.sort_values("time").reset_index(drop=True)
    return df


def _empty_payload(settings_obj: BacktestSettings, warnings_list: list[str] | None = None) -> dict[str, Any]:
    return {
        "meta": {
            "symbol": settings_obj.symbol,
            "strategy": settings_obj.strategy,
            "timeframe": settings_obj.timeframe,
            "timezone": settings_obj.timezone_name,
            "bars": 0,
            "start": settings_obj.start_date.isoformat(),
            "end": settings_obj.end_date.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "parameters": _serialize_settings(settings_obj),
        },
        "summary": _calculate_summary([], settings_obj),
        "equity_curve": [{"time": settings_obj.start_date.isoformat(), "equity": settings_obj.initial_capital, "drawdown_pct": 0.0}],
        "daily": [],
        "monthly": [],
        "trades": [],
        "warnings": warnings_list or [],
    }


def _update_ema_setups(
    setups: dict[str, PendingSetup | None],
    row: pd.Series,
    index: int,
    settings_obj: BacktestSettings,
) -> dict[str, PendingSetup | None]:
    ema_value = float(row["ema_5"])

    if settings_obj.allow_long and float(row["high"]) < ema_value:
        setups["LONG"] = PendingSetup(
            strategy="ema5_breakout",
            direction="LONG",
            setup_index=index,
            setup_time=row["time"],
            expires_at_index=index + settings_obj.entry_window_bars,
            trigger_price=float(row["high"]),
            stop_price=float(row["low"]),
            reference_close=float(row["close"]),
            metadata={"alert_side": "below_ema", "ema": ema_value},
        )

    if settings_obj.allow_short and float(row["low"]) > ema_value:
        setups["SHORT"] = PendingSetup(
            strategy="ema5_breakout",
            direction="SHORT",
            setup_index=index,
            setup_time=row["time"],
            expires_at_index=index + settings_obj.entry_window_bars,
            trigger_price=float(row["low"]),
            stop_price=float(row["high"]),
            reference_close=float(row["close"]),
            metadata={"alert_side": "above_ema", "ema": ema_value},
        )

    return setups


def _maybe_open_from_single_direction_setup(
    setup: PendingSetup | None,
    row: pd.Series,
    settings_obj: BacktestSettings,
    symbol: str,
    daily_trade_count: dict[date, int],
) -> tuple[PendingSetup | None, OpenTrade | None]:
    if setup is None:
        return None, None

    candle_index = int(row.name)
    candle_day = row["time"].date()
    if candle_day != setup.setup_time.date() or candle_index <= setup.setup_index:
        if candle_day != setup.setup_time.date():
            return None, None
        return setup, None

    if candle_index > setup.expires_at_index:
        return None, None

    if not _can_enter_bar(row["time"], settings_obj):
        return setup, None

    if daily_trade_count.get(candle_day, 0) >= settings_obj.max_trades_per_day:
        return setup, None

    if setup.direction == "LONG":
        triggered = float(row["close"]) > float(setup.trigger_price) if settings_obj.enter_on_close else float(row["high"]) >= float(setup.trigger_price)
        if not triggered:
            return setup, None
        entry_price = float(row["close"]) if settings_obj.enter_on_close else max(float(setup.trigger_price), float(row["open"]))
        stop_price = float(setup.stop_price)
        target_price = entry_price + ((entry_price - stop_price) * settings_obj.reward_risk)
    else:
        triggered = float(row["close"]) < float(setup.trigger_price) if settings_obj.enter_on_close else float(row["low"]) <= float(setup.trigger_price)
        if not triggered:
            return setup, None
        entry_price = float(row["close"]) if settings_obj.enter_on_close else min(float(setup.trigger_price), float(row["open"]))
        stop_price = float(setup.stop_price)
        target_price = entry_price - ((stop_price - entry_price) * settings_obj.reward_risk)

    risk_points = abs(entry_price - stop_price)
    if risk_points <= 0:
        return None, None

    daily_trade_count[candle_day] = daily_trade_count.get(candle_day, 0) + 1
    trade = OpenTrade(
        strategy="ema5_breakout",
        symbol=symbol,
        direction=setup.direction,
        entry_index=candle_index,
        entry_time=row["time"],
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        risk_points=risk_points,
        position_size=settings_obj.risk_per_trade / risk_points,
        setup_time=setup.setup_time,
        setup_index=setup.setup_index,
        metadata={
            **setup.metadata,
            "trigger_price": float(setup.trigger_price),
            "entry_window_bars": settings_obj.entry_window_bars,
        },
    )
    return None, trade


def _maybe_create_traffic_setup(
    previous_row: pd.Series,
    row: pd.Series,
    index: int,
    settings_obj: BacktestSettings,
) -> PendingSetup | None:
    prev_open = float(previous_row["open"])
    prev_close = float(previous_row["close"])
    curr_open = float(row["open"])
    curr_close = float(row["close"])

    prev_green = prev_close > prev_open
    prev_red = prev_close < prev_open
    curr_green = curr_close > curr_open
    curr_red = curr_close < curr_open

    if not ((prev_green and curr_red) or (prev_red and curr_green)):
        return None

    range_high = max(float(previous_row["high"]), float(row["high"]))
    range_low = min(float(previous_row["low"]), float(row["low"]))
    pattern_range = range_high - range_low
    if settings_obj.traffic_light_max_range is not None and pattern_range > settings_obj.traffic_light_max_range:
        return None

    return PendingSetup(
        strategy="traffic_light",
        direction="BOTH",
        setup_index=index,
        setup_time=row["time"],
        expires_at_index=index + settings_obj.entry_window_bars,
        trigger_high=range_high,
        trigger_low=range_low,
        reference_close=float(row["close"]),
        metadata={
            "pattern": "traffic_light",
            "pattern_range": pattern_range,
            "candle_pair_start": previous_row["time"].isoformat(),
            "candle_pair_end": row["time"].isoformat(),
        },
    )


def _maybe_open_from_traffic_setup(
    setup: PendingSetup | None,
    row: pd.Series,
    settings_obj: BacktestSettings,
    symbol: str,
    daily_trade_count: dict[date, int],
) -> tuple[PendingSetup | None, OpenTrade | None]:
    if setup is None:
        return None, None

    candle_index = int(row.name)
    candle_day = row["time"].date()
    if candle_day != setup.setup_time.date() or candle_index <= setup.setup_index:
        if candle_day != setup.setup_time.date():
            return None, None
        return setup, None

    if candle_index > setup.expires_at_index:
        return None, None

    if not _can_enter_bar(row["time"], settings_obj):
        return setup, None

    if daily_trade_count.get(candle_day, 0) >= settings_obj.max_trades_per_day:
        return setup, None

    long_triggered = settings_obj.allow_long and (
        float(row["close"]) > float(setup.trigger_high) if settings_obj.enter_on_close else float(row["high"]) >= float(setup.trigger_high)
    )
    short_triggered = settings_obj.allow_short and (
        float(row["close"]) < float(setup.trigger_low) if settings_obj.enter_on_close else float(row["low"]) <= float(setup.trigger_low)
    )

    if long_triggered and short_triggered:
        if not settings_obj.enter_on_close:
            return None, None
        close_price = float(row["close"])
        if close_price > float(setup.trigger_high):
            short_triggered = False
        elif close_price < float(setup.trigger_low):
            long_triggered = False
        else:
            return None, None

    if not long_triggered and not short_triggered:
        return setup, None

    if long_triggered:
        entry_price = float(row["close"]) if settings_obj.enter_on_close else max(float(setup.trigger_high), float(row["open"]))
        stop_price = float(setup.trigger_low)
        target_price = entry_price + ((entry_price - stop_price) * settings_obj.reward_risk)
        direction = "LONG"
    else:
        entry_price = float(row["close"]) if settings_obj.enter_on_close else min(float(setup.trigger_low), float(row["open"]))
        stop_price = float(setup.trigger_high)
        target_price = entry_price - ((stop_price - entry_price) * settings_obj.reward_risk)
        direction = "SHORT"

    risk_points = abs(entry_price - stop_price)
    if risk_points <= 0:
        return None, None

    daily_trade_count[candle_day] = daily_trade_count.get(candle_day, 0) + 1
    trade = OpenTrade(
        strategy="traffic_light",
        symbol=symbol,
        direction=direction,
        entry_index=candle_index,
        entry_time=row["time"],
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        risk_points=risk_points,
        position_size=settings_obj.risk_per_trade / risk_points,
        setup_time=setup.setup_time,
        setup_index=setup.setup_index,
        metadata={
            **setup.metadata,
            "trigger_high": float(setup.trigger_high),
            "trigger_low": float(setup.trigger_low),
            "entry_window_bars": settings_obj.entry_window_bars,
        },
    )
    return None, trade


def _resolve_open_trades(
    open_trades: list[OpenTrade],
    row: pd.Series,
    risk_per_trade: float,
    same_bar_exit_priority: Literal["stop", "target"],
    entered_trade_ids: set[int] | None = None,
) -> tuple[list[dict[str, Any]], list[OpenTrade]]:
    closed: list[dict[str, Any]] = []
    remaining: list[OpenTrade] = []
    entered_trade_ids = entered_trade_ids or set()

    for trade in open_trades:
        if trade.entry_index > int(row.name):
            remaining.append(trade)
            continue

        exit_record = _trade_exit_for_bar(
            trade=trade,
            row=row,
            risk_per_trade=risk_per_trade,
            same_bar_exit_priority=same_bar_exit_priority,
            allow_open_gap=id(trade) not in entered_trade_ids and int(row.name) > trade.entry_index,
        )
        if exit_record is None:
            remaining.append(trade)
        else:
            closed.append(exit_record)

    return closed, remaining


def _trade_exit_for_bar(
    trade: OpenTrade,
    row: pd.Series,
    risk_per_trade: float,
    same_bar_exit_priority: Literal["stop", "target"],
    allow_open_gap: bool,
) -> dict[str, Any] | None:
    bar_high = float(row["high"])
    bar_low = float(row["low"])
    bar_open = float(row["open"])

    if trade.direction == "LONG":
        stop_hit = bar_low <= trade.stop_price
        target_hit = bar_high >= trade.target_price
    else:
        stop_hit = bar_high >= trade.stop_price
        target_hit = bar_low <= trade.target_price

    if not stop_hit and not target_hit:
        return None

    if stop_hit and target_hit:
        exit_reason = "stop" if same_bar_exit_priority == "stop" else "target"
    else:
        exit_reason = "stop" if stop_hit else "target"

    if exit_reason == "stop":
        if allow_open_gap:
            exit_price = _gap_adjusted_price(trade.direction, bar_open, trade.stop_price, exit_reason)
        else:
            exit_price = trade.stop_price
    else:
        if allow_open_gap:
            exit_price = _gap_adjusted_price(trade.direction, bar_open, trade.target_price, exit_reason)
        else:
            exit_price = trade.target_price

    return _finalize_trade(
        trade,
        exit_time=row["time"],
        exit_price=float(exit_price),
        reason=exit_reason,
        risk_per_trade=risk_per_trade,
        exit_index=int(row.name),
    )


def _gap_adjusted_price(
    direction: Literal["LONG", "SHORT"],
    bar_open: float,
    target_level: float,
    exit_reason: Literal["stop", "target"],
) -> float:
    if direction == "LONG":
        if exit_reason == "stop":
            return min(bar_open, target_level)
        return max(bar_open, target_level)
    if exit_reason == "stop":
        return max(bar_open, target_level)
    return min(bar_open, target_level)


def _close_all_trades(
    open_trades: list[OpenTrade],
    exit_time: pd.Timestamp,
    exit_price: float,
    reason: str,
    risk_per_trade: float,
    equity: float,
    peak_equity: float,
    exit_index: int | None = None,
) -> tuple[list[dict[str, Any]], float]:
    closed: list[dict[str, Any]] = []
    for trade in open_trades:
        closed_trade = _finalize_trade(
            trade,
            exit_time=exit_time,
            exit_price=exit_price,
            reason=reason,
            risk_per_trade=risk_per_trade,
            exit_index=trade.entry_index if exit_index is None else exit_index,
        )
        equity += closed_trade["pnl"]
        peak_equity = max(peak_equity, equity)
        closed_trade["equity_after"] = equity
        closed_trade["drawdown"] = equity - peak_equity
        closed_trade["drawdown_pct"] = ((equity - peak_equity) / peak_equity * 100.0) if peak_equity else 0.0
        closed.append(closed_trade)
    return closed, equity


def _finalize_trade(
    trade: OpenTrade,
    exit_time: pd.Timestamp,
    exit_price: float,
    reason: str,
    risk_per_trade: float,
    exit_index: int,
) -> dict[str, Any]:
    sign = 1.0 if trade.direction == "LONG" else -1.0
    points = (exit_price - trade.entry_price) * sign
    result_r = points / trade.risk_points if trade.risk_points else 0.0
    pnl = result_r * risk_per_trade
    hold_bars = _bar_distance(trade.entry_index, exit_index)

    return {
        "strategy": trade.strategy,
        "symbol": trade.symbol,
        "direction": trade.direction,
        "setup_time": trade.setup_time.isoformat(),
        "entry_time": trade.entry_time.isoformat(),
        "exit_time": exit_time.isoformat(),
        "entry_price": round(trade.entry_price, 4),
        "exit_price": round(exit_price, 4),
        "stop_price": round(trade.stop_price, 4),
        "target_price": round(trade.target_price, 4),
        "risk_points": round(trade.risk_points, 4),
        "reward_risk": round(abs(trade.target_price - trade.entry_price) / trade.risk_points, 4) if trade.risk_points else 0.0,
        "points": round(points, 4),
        "result_r": round(result_r, 4),
        "pnl": round(pnl, 2),
        "exit_reason": reason,
        "position_size": round(trade.position_size, 4),
        "hold_bars": hold_bars,
        "setup_index": trade.setup_index,
        "entry_index": trade.entry_index,
        "tags": trade.metadata,
    }


def _bar_distance(entry_index: int, exit_index: int) -> int:
    return max(1, exit_index - entry_index + 1)


def _equity_points_from_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "time": trade["exit_time"],
            "equity": trade.get("equity_after"),
            "drawdown_pct": abs(float(trade.get("drawdown_pct", 0.0))),
        }
        for trade in trades
    ]


def _equity_curve_payload(equity_points: list[dict[str, Any]], initial_capital: float) -> list[dict[str, Any]]:
    if not equity_points:
        return [{"time": None, "equity": initial_capital, "drawdown_pct": 0.0}]
    return [{"time": None, "equity": initial_capital, "drawdown_pct": 0.0}, *equity_points]


def _aggregate_performance(trades: list[dict[str, Any]], bucket: Literal["date", "month"]) -> list[dict[str, Any]]:
    if not trades:
        return []

    records = []
    for trade in trades:
        exit_dt = pd.Timestamp(trade["exit_time"])
        bucket_key = exit_dt.strftime("%Y-%m") if bucket == "month" else exit_dt.strftime("%Y-%m-%d")
        records.append(
            {
                "bucket": bucket_key,
                "pnl": trade["pnl"],
                "result_r": trade["result_r"],
                "win": 1 if trade["pnl"] > 0 else 0,
                "loss": 1 if trade["pnl"] < 0 else 0,
            }
        )

    frame = pd.DataFrame(records)
    grouped = frame.groupby("bucket", sort=True).agg(
        pnl=("pnl", "sum"),
        result_r=("result_r", "sum"),
        trades=("pnl", "count"),
        wins=("win", "sum"),
        losses=("loss", "sum"),
    )
    grouped["win_rate"] = np.where(grouped["trades"] > 0, grouped["wins"] / grouped["trades"] * 100.0, 0.0)
    grouped = grouped.reset_index()
    label = "month" if bucket == "month" else "date"

    return [
        {
            label: row["bucket"],
            "pnl": round(float(row["pnl"]), 2),
            "result_r": round(float(row["result_r"]), 4),
            "trades": int(row["trades"]),
            "wins": int(row["wins"]),
            "losses": int(row["losses"]),
            "win_rate": round(float(row["win_rate"]), 2),
        }
        for _, row in grouped.iterrows()
    ]


def _calculate_summary(trades: list[dict[str, Any]], settings_obj: BacktestSettings) -> dict[str, Any]:
    total_trades = len(trades)
    wins = sum(1 for trade in trades if trade["pnl"] > 0)
    losses = sum(1 for trade in trades if trade["pnl"] < 0)
    gross_profit = sum(trade["pnl"] for trade in trades if trade["pnl"] > 0)
    gross_loss = abs(sum(trade["pnl"] for trade in trades if trade["pnl"] < 0))
    net_profit = sum(trade["pnl"] for trade in trades)
    total_r = sum(trade["result_r"] for trade in trades)
    expectancy = (total_r / total_trades) if total_trades else 0.0
    average_win_r = np.mean([trade["result_r"] for trade in trades if trade["result_r"] > 0]) if wins else 0.0
    average_loss_r = np.mean([trade["result_r"] for trade in trades if trade["result_r"] < 0]) if losses else 0.0
    profit_factor = None
    if gross_loss == 0:
        profit_factor = None if gross_profit == 0 else math.inf
    else:
        profit_factor = gross_profit / gross_loss

    curve = [settings_obj.initial_capital]
    for trade in trades:
        curve.append(trade.get("equity_after", curve[-1]))
    peaks = np.maximum.accumulate(curve) if curve else np.array([settings_obj.initial_capital])
    drawdowns = peaks - np.array(curve)
    max_drawdown = float(drawdowns.max()) if len(drawdowns) else 0.0
    max_drawdown_pct = float(((drawdowns / peaks).max() * 100.0)) if len(drawdowns) and np.all(peaks > 0) else 0.0

    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "breakeven": total_trades - wins - losses,
        "win_rate": round((wins / total_trades * 100.0) if total_trades else 0.0, 2),
        "profit_factor": None if profit_factor is None else ("Infinity" if math.isinf(profit_factor) else round(float(profit_factor), 2)),
        "net_profit": round(float(net_profit), 2),
        "gross_profit": round(float(gross_profit), 2),
        "gross_loss": round(float(gross_loss), 2),
        "total_r": round(float(total_r), 4),
        "expectancy_r": round(float(expectancy), 4),
        "average_win_r": round(float(average_win_r), 4),
        "average_loss_r": round(float(average_loss_r), 4),
        "max_drawdown": round(float(max_drawdown), 2),
        "max_drawdown_pct": round(float(max_drawdown_pct), 2),
        "ending_equity": round(float(settings_obj.initial_capital + net_profit), 2),
        "return_pct": round((net_profit / settings_obj.initial_capital * 100.0) if settings_obj.initial_capital else 0.0, 2),
    }


def _serialize_settings(settings_obj: BacktestSettings) -> dict[str, Any]:
    payload = asdict(settings_obj)
    payload["start_date"] = settings_obj.start_date.isoformat()
    payload["end_date"] = settings_obj.end_date.isoformat()
    return payload


def _ensure_mt5_connection(mt5_module: Any) -> None:
    if mt5_module.terminal_info():
        return

    initialized = mt5_module.initialize(path=getattr(settings, "MT5_PATH", None))
    if not initialized:
        raise RuntimeError(f"MT5 initialize() failed: {mt5_module.last_error()}")

    login = getattr(settings, "MT5_LOGIN", None)
    password = getattr(settings, "MT5_PASSWORD", None)
    server = getattr(settings, "MT5_SERVER", None)
    if login and password and server:
        authorized = mt5_module.login(login, password=password, server=server)
        if not authorized:
            raise RuntimeError(f"MT5 login failed: {mt5_module.last_error()}")


def _mt5_timeframe_constant(timeframe: str, mt5_module: Any) -> int:
    mapping = {
        "M1": mt5_module.TIMEFRAME_M1,
        "M5": mt5_module.TIMEFRAME_M5,
        "M15": mt5_module.TIMEFRAME_M15,
        "M30": mt5_module.TIMEFRAME_M30,
        "H1": mt5_module.TIMEFRAME_H1,
        "H4": mt5_module.TIMEFRAME_H4,
        "D1": mt5_module.TIMEFRAME_D1,
    }
    return mapping[timeframe]


def _parse_clock(value: str | None) -> time | None:
    if not value:
        return None
    hour_text, minute_text = value.split(":")
    return time(hour=int(hour_text), minute=int(minute_text))


def _clock_in_range(current: time, start: time | None, end: time | None) -> bool:
    if start is None or end is None:
        return True
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _can_scan_bar(candle_time: pd.Timestamp, settings_obj: BacktestSettings) -> bool:
    return _can_enter_bar(candle_time, settings_obj)


def _can_enter_bar(candle_time: pd.Timestamp, settings_obj: BacktestSettings) -> bool:
    current_clock = candle_time.timetz().replace(tzinfo=None)
    in_session = _clock_in_range(current_clock, _parse_clock(settings_obj.session_start), _parse_clock(settings_obj.session_end))
    if not in_session:
        return False

    exclude_start = _parse_clock(settings_obj.exclude_start)
    exclude_end = _parse_clock(settings_obj.exclude_end)
    if exclude_start and exclude_end and _clock_in_range(current_clock, exclude_start, exclude_end):
        return False

    square_off = _parse_clock(settings_obj.square_off_time)
    if square_off and current_clock >= square_off:
        return False

    return True


def _should_square_off(candle_time: pd.Timestamp, settings_obj: BacktestSettings) -> bool:
    square_off = _parse_clock(settings_obj.square_off_time)
    if square_off is None:
        return False
    return candle_time.timetz().replace(tzinfo=None) >= square_off
