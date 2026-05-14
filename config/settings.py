import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# MT5 Connection Settings
MT5_LOGIN = int(os.getenv("MT5_LOGIN", 12345678))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "your_password")
MT5_SERVER = os.getenv("MT5_SERVER", "Exness-Real")
MT5_PATH = os.getenv("MT5_PATH", r"C:\Program Files\MetaTrader 5\terminal64.exe")

# Trading Settings
# Primary symbol (used for training if single model)
SYMBOL = os.getenv("SYMBOL", "EURUSD")

# ─── Full Multi-Asset Universe (Exness) ──────────────────────────────────
# Base names — the bot auto-detects the correct suffix for your account
# (e.g., EURUSD, EURUSDm, EURUSDc depending on Standard/Cent account)

# Pruned for Expectancy Improvement (Focus on majors/crypto)
# Temporarily commenting out negative expectancy pairs provided by user analysis
SYMBOLS_FOREX_MAJORS_BASE = [
    # "EURUSD", "GBPUSD", "USDJPY",  # Disabled — XAUUSD only
]

SYMBOLS_FOREX_MINORS_BASE = [
    # "EURGBP", "EURJPY", "GBPJPY", "EURAUD", "EURCAD", "EURCHF", "EURNZD",
    # "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD",
    # "AUDJPY", "AUDCAD", "AUDCHF", "AUDNZD",
    # "NZDJPY", "NZDCAD", "NZDCHF",
    # "CADJPY", "CADCHF", "CHFJPY",
]

SYMBOLS_CRYPTO_BASE = [
    # "BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD", "BCHUSD",  # Disabled — XAUUSD only
    # "BTCJPY", "BTCKRW",
]

SYMBOLS_COMMODITIES_BASE = [
    "XAUUSD",                      # Gold only — focused mode
    # "XAGUSD", "XPTUSD", "XPDUSD",  # Disabled
    # "USOIL", "UKOIL", "XNGUSD",    # Disabled
]

# Exness account suffixes to try during auto-detection
# '' = Raw Spread/Pro, 'm' = Standard, 'c' = Standard Cent
EXNESS_SUFFIXES = ["", "m", "c"]

# All base names combined — actual SYMBOLS list is populated at runtime
ALL_BASE_SYMBOLS = (SYMBOLS_FOREX_MAJORS_BASE + SYMBOLS_FOREX_MINORS_BASE +
                    SYMBOLS_CRYPTO_BASE + SYMBOLS_COMMODITIES_BASE)

# These will be populated at runtime by detect_available_symbols()
SYMBOLS = []
SYMBOLS_FOREX_MAJORS = []
SYMBOLS_FOREX_MINORS = []
SYMBOLS_CRYPTO = []
SYMBOLS_COMMODITIES = []

# Symbol Blacklist (high commission, low liquidity)
BLACKLISTED_SYMBOLS = os.getenv("BLACKLISTED_SYMBOLS", "XPDUSD").split(",")

TIMEFRAME = "M5"  # M5 scalping mode — fast XAUUSD entries
print(f"[SETTINGS] TIMEFRAME set to: {TIMEFRAME}")
LOT_SIZE = float(os.getenv("LOT_SIZE", 0.01))  # Base lot size
DEVIATION = int(os.getenv("DEVIATION", 30))     # Wider deviation for Gold volatility
LEVERAGE = int(os.getenv("LEVERAGE", 1000))

# ─── SESSION REGIME SWITCHING (Edge #1) ──────────────────────────────────
# The most robust edge: detect which session is active and switch strategy.
SESSION_REGIME_ENABLED = os.getenv("SESSION_REGIME_ENABLED", "True").lower() == "true"
ASIAN_SESSION_START = float(os.getenv("ASIAN_SESSION_START", 22.0))   # 22:00 UTC
ASIAN_SESSION_END = float(os.getenv("ASIAN_SESSION_END", 8.0))       # 08:00 UTC
LONDON_SESSION_START = float(os.getenv("LONDON_SESSION_START", 8.0))
LONDON_SESSION_END = float(os.getenv("LONDON_SESSION_END", 13.0))
NY_SESSION_START = float(os.getenv("NY_SESSION_START", 13.0))
NY_SESSION_END = float(os.getenv("NY_SESSION_END", 17.0))
NY_AFTERNOON_START = float(os.getenv("NY_AFTERNOON_START", 17.0))
NY_AFTERNOON_END = float(os.getenv("NY_AFTERNOON_END", 22.0))

# Asian Range Breakout Parameters
BREAKOUT_ATR_MULTIPLIER = float(os.getenv("BREAKOUT_ATR_MULTIPLIER", 0.5))
ASIAN_RSI_OVERBOUGHT = float(os.getenv("ASIAN_RSI_OVERBOUGHT", 70))
ASIAN_RSI_OVERSOLD = float(os.getenv("ASIAN_RSI_OVERSOLD", 30))

# ─── MACRO FILTER (Edge #2 — DXY + VIX) ─────────────────────────────────
MACRO_FILTER_ENABLED = os.getenv("MACRO_FILTER_ENABLED", "True").lower() == "true"
VIX_SAFE_HAVEN_THRESHOLD = float(os.getenv("VIX_SAFE_HAVEN_THRESHOLD", 25))
VIX_CALM_THRESHOLD = float(os.getenv("VIX_CALM_THRESHOLD", 20))
VIX_RANGE_LOW = float(os.getenv("VIX_RANGE_LOW", 15))
DXY_SPIKE_THRESHOLD = float(os.getenv("DXY_SPIKE_THRESHOLD", 0.003))
MACRO_POLL_MINUTES = int(os.getenv("MACRO_POLL_MINUTES", 15))

# ─── LIQUIDITY SWEEP (Edge #3) ───────────────────────────────────────────
SWEEP_ENABLED = os.getenv("SWEEP_ENABLED", "True").lower() == "true"
SWEEP_BUFFER_PIPS = float(os.getenv("SWEEP_BUFFER_PIPS", 3))   # 2-3 pips above PDH
SWEEP_NEWS_GUARD_MINUTES = int(os.getenv("SWEEP_NEWS_GUARD_MINUTES", 15))

print(f"[SETTINGS] SESSION_REGIME: {'ENABLED' if SESSION_REGIME_ENABLED else 'disabled'}")
print(f"[SETTINGS] MACRO_FILTER: {'ENABLED' if MACRO_FILTER_ENABLED else 'disabled'}")
print(f"[SETTINGS] LIQUIDITY_SWEEP: {'ENABLED' if SWEEP_ENABLED else 'disabled'}")

# ─── ⚡ QUICK SCALP MODE (LEGACY — superseded by Session Regime) ─────────
QUICK_SCALP_MODE = os.getenv("QUICK_SCALP_MODE", "False").lower() == "true"
QUICK_SCALP_LOT_SIZE = float(os.getenv("QUICK_SCALP_LOT_SIZE", 0.1))
QUICK_SCALP_MAX_LOT = float(os.getenv("QUICK_SCALP_MAX_LOT", 0.5))
QUICK_SCALP_RISK_PERCENT = float(os.getenv("QUICK_SCALP_RISK_PERCENT", 1.0))
QUICK_SCALP_SL_ATR = float(os.getenv("QUICK_SCALP_SL_ATR", 1.5))
QUICK_SCALP_TP_ATR = float(os.getenv("QUICK_SCALP_TP_ATR", 2.0))
QUICK_SCALP_MIN_RR = float(os.getenv("QUICK_SCALP_MIN_RR", 1.5))
QUICK_SCALP_COOLDOWN = int(os.getenv("QUICK_SCALP_COOLDOWN", 60))
QUICK_SCALP_MIN_CONFLUENCE = int(os.getenv("QUICK_SCALP_MIN_CONFLUENCE", 3))
QUICK_SCALP_MIN_ML_PROB = float(os.getenv("QUICK_SCALP_MIN_ML_PROB", 0.55))
QUICK_SCALP_MAX_POSITIONS = int(os.getenv("QUICK_SCALP_MAX_POSITIONS", 3))
QUICK_SCALP_BREAKEVEN_ATR = float(os.getenv("QUICK_SCALP_BREAKEVEN_ATR", 0.6))
QUICK_SCALP_TRAIL_ATR = float(os.getenv("QUICK_SCALP_TRAIL_ATR", 0.8))
QUICK_SCALP_PARTIAL_FRACTION = float(os.getenv("QUICK_SCALP_PARTIAL_FRACTION", 0.50))
QUICK_SCALP_EARLY_CUT_ATR = float(os.getenv("QUICK_SCALP_EARLY_CUT_ATR", 0.6))
QUICK_SCALP_MARKET_ORDER = os.getenv("QUICK_SCALP_MARKET_ORDER", "True").lower() == "true"

# ─── 🧠 Gemma 4 Brain Settings ─────────────────────────────────────────────
GEMMA_BRAIN_ENABLED = os.getenv("GEMMA_BRAIN_ENABLED", "True").lower() == "true"
GEMMA_MIN_CONFIDENCE = int(os.getenv("GEMMA_MIN_CONFIDENCE", 55))    # Block trades below 55% Gemma confidence
GEMMA_BOOST_THRESHOLD = int(os.getenv("GEMMA_BOOST_THRESHOLD", 75))  # Boost score when Gemma ≥ 75% confident
print(f"[SETTINGS] GEMMA_BRAIN: {'ENABLED 🧠' if GEMMA_BRAIN_ENABLED else 'disabled'}")


# ─── HARD RISK CAPS (Non-Negotiable — Survival First) ────────────────────
FORCE_TEST_TRADES = False
RISK_PERCENT = float(os.getenv("RISK_PERCENT", 1.0))          # 1% max per trade
MAX_RISK_PERCENT = float(os.getenv("MAX_RISK_PERCENT", 1.5))  # 1.5% absolute max
MAX_EFFECTIVE_LEVERAGE = int(os.getenv("MAX_EFFECTIVE_LEVERAGE", 20))  # 1:20 (ignore broker)
MAX_DAILY_LOSS_PERCENT = float(os.getenv("MAX_DAILY_LOSS_PERCENT", 3.0))  # 3% → shutdown
CONSECUTIVE_LOSS_LIMIT = int(os.getenv("CONSECUTIVE_LOSS_LIMIT", 3))  # Kill switch
KILL_SWITCH_REDUCTION = float(os.getenv("KILL_SWITCH_REDUCTION", 0.5))  # 50% size for 24h
SPREAD_REJECT_THRESHOLD = float(os.getenv("SPREAD_REJECT_THRESHOLD", 30))  # 30 pips = $0.30
SWAP_AWARENESS = os.getenv("SWAP_AWARENESS", "True").lower() == "true"

# ATR-Based Dynamic SL/TP
ATR_SL_MULTIPLIER = float(os.getenv("ATR_SL_MULTIPLIER", 1.5))
ATR_TP_MULTIPLIER = float(os.getenv("ATR_TP_MULTIPLIER", 3.0))

# Confluence Gating
MIN_CONFLUENCE_SCORE = int(os.getenv("MIN_CONFLUENCE_SCORE", 5))  # Session signals score 6-9
SURESHOT_MIN_SCORE = int(os.getenv("SURESHOT_MIN_SCORE", 8))
RF_PROB_THRESHOLD = float(os.getenv("RF_PROB_THRESHOLD", 0.55))
MIN_RISK_REWARD_RATIO = float(os.getenv("MIN_RISK_REWARD_RATIO", 2.0))

# Kelly Criterion
USE_KELLY = os.getenv("USE_KELLY", "True").lower() == "true"
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", 0.5))
KELLY_MIN_TRADES = int(os.getenv("KELLY_MIN_TRADES", 20))

# Cost Awareness
COMMISSION_PER_LOT = float(os.getenv("COMMISSION_PER_LOT", 7.0))
MIN_NET_PROFIT_RATIO = float(os.getenv("MIN_NET_PROFIT_RATIO", 3.0))

# ─── Trade Management (HARDENED) ─────────────────────────────────────────
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", 60))
RISK_FACTOR_MAX = float(os.getenv("RISK_FACTOR_MAX", 1.5))
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", 10))       # Reasonable daily limit
MAX_DAILY_LOSS_USD = float(os.getenv("MAX_DAILY_LOSS_USD", 100.0))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", 3))    # Hard cap
LIMIT_ORDER_EXPIRATION_MINUTES = int(os.getenv("LIMIT_ORDER_EXPIRATION_MINUTES", 10))
MAX_CONCURRENT_TRADES = int(os.getenv("MAX_CONCURRENT_TRADES", 3))
MAX_SPREAD_PIPS = float(os.getenv("MAX_SPREAD_PIPS", 3.0))
MAX_SPREAD_PIPS_CRYPTO = float(os.getenv("MAX_SPREAD_PIPS_CRYPTO", 20000.0))
MAX_SPREAD_PIPS_COMMODITY = float(os.getenv("MAX_SPREAD_PIPS_COMMODITY", 30.0))  # 30 pips for Gold

# ─── Volatility-Adaptive Entry ───────────────────────────────────────────
# Minimum ATR required to enter a scalp trade (avoid dead/ranging markets)
VOLATILITY_ATR_MIN = float(os.getenv("VOLATILITY_ATR_MIN", 0.00015))  # 1.5 pips min for Forex M1
VOLATILITY_ATR_MIN_CRYPTO = float(os.getenv("VOLATILITY_ATR_MIN_CRYPTO", 50.0))  # $50 min for Crypto
VOLATILITY_ATR_MIN_COMMODITY = float(os.getenv("VOLATILITY_ATR_MIN_COMMODITY", 0.5))  # 50c min for Commodities

# ─── XAUUSD Scalp Session Windows (UTC) ──────────────────────────────────
# Gold trades 23h/day — trade all liquid sessions
SCALP_SESSION_FILTER = False  # Disabled — Gold is liquid almost 24/5
SCALP_SESSIONS = [
    {"name": "London Open",    "start": 7,  "end": 11},   # 07:00-11:00 UTC
    {"name": "NY Open",        "start": 13, "end": 17},   # 13:00-17:00 UTC
    {"name": "London/NY Overlap", "start": 13, "end": 16}, # Best Gold liquidity
]

# ─── Advanced Risk Controls (P&L Distribution Correction) ────────────────
# 1. Tail Risk Isolation
TAIL_RISK_SYMBOLS = ["XAUUSD", "BTCUSD", "ETHUSD", "USOIL"] # High vol symbols
MAX_TAIL_RISK_LOSS_USD = float(os.getenv("MAX_TAIL_RISK_LOSS_USD", 30.0)) # Hard cap loss per trade for these

# 2. Kill Switch (Auto-Disable Bad Symbols)
KILL_SWITCH_LOOKBACK_TRADES = int(os.getenv("KILL_SWITCH_LOOKBACK_TRADES", 15)) # Relaxed from 5
KILL_SWITCH_LOSS_THRESHOLD = float(os.getenv("KILL_SWITCH_LOSS_THRESHOLD", -60.0)) # If last 15 trades lost > $60, disable

# Override Risk Checks for specific symbols (bypass kill-switch & payoff mandate)
# Add any symbol that should always be allowed to trade regardless of stats
RISK_OVERRIDE_SYMBOLS_BASE = ["EURUSD", "GBPUSD", "USDJPY", "BTCUSD", "XAUUSD", "ETHUSD"]
# Also handle suffixed variants (m, c) at runtime
RISK_OVERRIDE_SYMBOLS = RISK_OVERRIDE_SYMBOLS_BASE + \
    [s + sfx for s in RISK_OVERRIDE_SYMBOLS_BASE for sfx in ('m', 'c', 'z')]

# 3. Asymmetric Payoff Mandate
MANDATE_MIN_RR = True  # Enforce, but only for symbols NOT in RISK_OVERRIDE_SYMBOLS
# Threshold raised 1.0 -> 2.0: only blocks if AvgLoss > 2x AvgWin (truly bad symbols)
AVG_LOSS_RATIO_THRESHOLD = float(os.getenv("AVG_LOSS_RATIO_THRESHOLD", 2.0))

# ─── Smart Exit System (Let Winners Run, Cut Losers Fast) ────────────────
# Philosophy: NO fixed TP. Trailing SL is the only exit for winners.
#             Losers are cut early when momentum reverses against position.
SMART_EXIT_ENABLED = os.getenv("SMART_EXIT_ENABLED", "True").lower() == "true"

# Progressive Trailing — trail distance TIGHTENS as profit grows
TRAIL_ACTIVATE_ATR = float(os.getenv("TRAIL_ACTIVATE_ATR", 0.3))    # Start trailing at 0.3x ATR profit
TRAIL_INITIAL_ATR = float(os.getenv("TRAIL_INITIAL_ATR", 1.0))      # Initial trail: 1.0x ATR behind
TRAIL_TIGHT_ATR = float(os.getenv("TRAIL_TIGHT_ATR", 0.4))          # Tighten to 0.4x ATR at 3R+ profit
TRAIL_RATCHET_FACTOR = float(os.getenv("TRAIL_RATCHET_FACTOR", 0.7)) # Trail can only move IN favor (ratchet)

# Breakeven — move SL to entry ASAP to eliminate risk
BREAKEVEN_ACTIVATE_ATR = float(os.getenv("BREAKEVEN_ACTIVATE_ATR", 0.5))  # BE at 0.5x ATR profit
BREAKEVEN_BUFFER_ATR = float(os.getenv("BREAKEVEN_BUFFER_ATR", 0.05))     # Tiny buffer above entry (covers spread)

# Early Loss Cutting — close losers when market turns against them
EARLY_CUT_ENABLED = os.getenv("EARLY_CUT_ENABLED", "True").lower() == "true"
EARLY_CUT_LOSS_ATR = float(os.getenv("EARLY_CUT_LOSS_ATR", 0.5))    # If losing > 0.5x ATR AND momentum against → cut
EARLY_CUT_RSI_THRESHOLD = float(os.getenv("EARLY_CUT_RSI_THRESHOLD", 35.0))  # RSI below 35 for longs = cut
EARLY_CUT_MACD_BARS = int(os.getenv("EARLY_CUT_MACD_BARS", 3))      # 3 bars of MACD against = cut

# Partial Close at breakeven (lock some profit risk-free)
PARTIAL_AT_BE = os.getenv("PARTIAL_AT_BE", "True").lower() == "true"
PARTIAL_CLOSE_FRACTION = float(os.getenv("PARTIAL_CLOSE_FRACTION", 0.30))  # Close 30% at BE (keep 70% running)

# Remove fixed TP — TP is set very wide (10x ATR) as safety net only
TP_SAFETY_ATR = float(os.getenv("TP_SAFETY_ATR", 10.0))  # Emergency TP at 10x ATR (rarely hit)

# ─── Multi-Timeframe Trend Filters ───────────────────────────────────────
M5_TREND_FILTER = os.getenv("M5_TREND_FILTER", "True").lower() == "true"   # M5 confirms M1 direction
H1_TREND_FILTER = os.getenv("H1_TREND_FILTER", "True").lower() == "true"
H4_TREND_FILTER = os.getenv("H4_TREND_FILTER", "True").lower() == "true"   # Prevent counter-trend trades

# ─── News Integration ────────────────────────────────────────────────────
NEWS_CALENDAR_URL = os.getenv("NEWS_CALENDAR_URL", "https://nfs.faireconomy.media/ff_calendar_thisweek.json")
NEWS_CACHE_HOURS = int(os.getenv("NEWS_CACHE_HOURS", 4))  # Cache calendar for 4 hours
NEWS_CALENDAR_CACHE_MINUTES = NEWS_CACHE_HOURS * 60  # Backward-compatible alias
NEWS_PRE_MINUTES = int(os.getenv("NEWS_PRE_MINUTES", 15))   # Block 15 min before high-impact news
NEWS_POST_MINUTES = int(os.getenv("NEWS_POST_MINUTES", 15)) # Block 15 min after high-impact news

# ─── News-Based Trading (Active News Trading on XAUUSD) ──────────────────
NEWS_TRADING_ENABLED = os.getenv("NEWS_TRADING_ENABLED", "True").lower() == "true"
NEWS_TRADING_MODE = os.getenv("NEWS_TRADING_MODE", "BREAKOUT")  # STRADDLE or BREAKOUT
NEWS_TRADING_SYMBOLS = ["XAUUSD"]  # Only gold — highest news sensitivity
NEWS_LOOKAHEAD_MINUTES = int(os.getenv("NEWS_LOOKAHEAD_MINUTES", 30))  # Pre-position window
NEWS_ATR_SL_MULTIPLIER = float(os.getenv("NEWS_ATR_SL_MULTIPLIER", 2.0))  # Wider SL for news vol
NEWS_ATR_TP_MULTIPLIER = float(os.getenv("NEWS_ATR_TP_MULTIPLIER", 4.0))  # Wider TP for momentum
NEWS_LOT_REDUCTION = float(os.getenv("NEWS_LOT_REDUCTION", 0.5))  # 50% lot size vs normal
NEWS_BREAKOUT_CONFIRM_CANDLES = int(os.getenv("NEWS_BREAKOUT_CONFIRM_CANDLES", 1))  # Candles to confirm
NEWS_STRADDLE_DISTANCE_ATR = float(os.getenv("NEWS_STRADDLE_DISTANCE_ATR", 1.5))  # Straddle distance
NEWS_POST_EVENT_COOLDOWN = int(os.getenv("NEWS_POST_EVENT_COOLDOWN", 300))  # 5 min cooldown after news trade
NEWS_MAX_TRADES_PER_EVENT = int(os.getenv("NEWS_MAX_TRADES_PER_EVENT", 1))  # Max trades per event
NEWS_MIN_ATR_FOR_TRADE = float(os.getenv("NEWS_MIN_ATR_FOR_TRADE", 1.0))  # Min ATR in USD for Gold news

# ─── Fake News Detection ─────────────────────────────────────────────────
FAKE_NEWS_DETECTION_ENABLED = os.getenv("FAKE_NEWS_DETECTION_ENABLED", "True").lower() == "true"
FAKE_NEWS_MIN_CREDIBILITY = float(os.getenv("FAKE_NEWS_MIN_CREDIBILITY", 0.4))    # Below this = flagged suspicious
FAKE_NEWS_DISCOUNT_FACTOR = float(os.getenv("FAKE_NEWS_DISCOUNT_FACTOR", 0.1))    # Reduce news weight to 10% when flagged

# ─── Session Awareness (UTC hours) — widened for XAUUSD scalping ─────────
# Session filter now handled by SessionRegimeService — allow all hours through
# (the regime service itself decides what trades are allowed when)
TRADE_SESSIONS = {
    "asian":       {"start": 22.0, "end": 8.0},   # Asian (wraps midnight)
    "london":      {"start": 8.0, "end": 13.0},   # London session
    "ny":          {"start": 13.0, "end": 17.0},   # New York session
    "ny_afternoon":{"start": 17.0, "end": 22.0},   # NY Afternoon (FLAT)
}
SESSION_FILTER = False  # Disabled — SessionRegimeService handles session logic

USE_PATTERN_MEMORY = True  # Strict RAG historical embedding blocks

# --- Data Settings -----------------------------------------------------------
# 10 years of M15 data: 10 * 252 days * 96 bars/day = ~242,000 bars
HISTORY_BARS = 250000  # 10 years of M15 data
HISTORY_BARS_M1 = 500000  # ~1 year of M1 data for scalping
TRAIN_TEST_SPLIT = 0.8

# ─── Model Settings ─────────────────────────────────────────────────────
MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "scalper_v1.pkl")
XGB_MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "xgboost_v1.pkl")
USE_XGBOOST = True

# BOS Strategy Settings
BOS_ENABLE = True
BOS_MOMENTUM_MULTIPLIER = 1.5
BOS_SWEEP_LOOKBACK = 20
BOS_MAX_SPREAD_RATIO = 0.15      # Spread max 15% of SL capability
BOS_HUNTING_HOURS = [8, 9, 10, 13, 14, 15] # Strict London/NY Open hours
BOS_MIN_RISK_REWARD = 2.5       # Asymmetric Payoff for Retail
NEWS_FILTER_ENABLE = True       # Enable High-Impact News Avoidance
BOS_REQUIRE_CONFIRMATION = True  # Require confirmation candle after BOS break
BOS_MIN_PULLBACK_PCT = 0.3      # Min pullback as fraction of break candle range
BOS_STRICT_MODE = True          # Block any trade where BOS direction contradicts ML direction

# ─── HMM Regime Detection ───────────────────────────────────────────────
USE_HMM_REGIME = True            # Use HMM-based regime detection for adaptive params

# ─── Regime-Adaptive Parameters ─────────────────────────────────────────
REGIME_PARAMS = {
    "TRENDING": {
        "ATR_TP_MULTIPLIER": 5.0,
        "ATR_SL_MULTIPLIER": 1.8,
        "MIN_CONFLUENCE_SCORE": 2,  # Normal confluence needed in trend
        "MAX_DAILY_TRADES": 4,
    },
    "RANGING": {
        "ATR_TP_MULTIPLIER": 2.5,
        "ATR_SL_MULTIPLIER": 1.5,
        "MIN_CONFLUENCE_SCORE": 3,  # Strict: Tough to trade ranges
        "MAX_DAILY_TRADES": 2,
    },
    "VOLATILE": {
        "ATR_TP_MULTIPLIER": 3.0,
        "ATR_SL_MULTIPLIER": 2.5,
        "MIN_CONFLUENCE_SCORE": 3,  # Strict: Avoid chaotic whipsaws
        "MAX_DAILY_TRADES": 1,
    },
}

# ─── Trailing Stop (New ATR-Based) ──────────────────────────────────────
USE_TRAILING_STOP = True         # Enable ATR-based trailing stops on open positions
TRAILING_ATR_MULTIPLIER = 1.5    # Trail SL by ATR * this multiplier behind price

# ─── Institutional Flow Tracking (Smart Money) ───────────────────────────
INST_FLOW_ENABLE = os.getenv("INST_FLOW_ENABLE", "True").lower() == "true"
INST_FLOW_MIN_SCORE = int(os.getenv("INST_FLOW_MIN_SCORE", 60))           # Min score to boost trade
INST_FLOW_BLOCK_SCORE = int(os.getenv("INST_FLOW_BLOCK_SCORE", 70))       # Score to block counter-flow trades
INST_FLOW_VOLUME_ZSCORE_THRESHOLD = float(os.getenv("INST_FLOW_VOLUME_ZSCORE_THRESHOLD", 2.0))
INST_FLOW_ABSORPTION_THRESHOLD = float(os.getenv("INST_FLOW_ABSORPTION_THRESHOLD", 0.3))
INST_FLOW_DISPLACEMENT_MULTIPLIER = float(os.getenv("INST_FLOW_DISPLACEMENT_MULTIPLIER", 3.0))

# Lag-Llama Settings
USE_LAG_LLAMA = False # os.getenv("USE_LAG_LLAMA", "True").lower() == "true"
LAG_LLAMA_CHECKPOINT = os.getenv("LAG_LLAMA_CHECKPOINT", "time-series-foundation-models/Lag-Llama")
LAG_LLAMA_REPO_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "vendor", "lag-llama")

# LSTM Settings
USE_LSTM = os.getenv("USE_LSTM", "True").lower() == "true"
LSTM_MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", f"lstm_{SYMBOL}.pth")
LSTM_SCALER_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", f"lstm_{SYMBOL}_scaler.pkl")
LSTM_SEQ_LENGTH = 60

# ─── Telegram Notifications ───────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── Statistical Arbitrage Settings ──────────────────────────────────────
# Define known macro-correlated pairs to constantly monitor for divergence
STAT_ARB_PAIRS = [("XAUUSD", "DXY")]  # Focus on Gold vs USD divergence
STAT_ARB_MAX_ZSCORE = float(os.getenv("STAT_ARB_MAX_ZSCORE", 2.0)) # ±2.0 Standard Deviations triggers a hedge trade
STAT_ARB_LOT_SIZE = float(os.getenv("STAT_ARB_LOT_SIZE", 0.01))    # Fixed micro-lot sizing for hedge legs

# ─── Covariance Risk Matrix ──────────────────────────────────────────────
# How much exposure to a single currency (e.g. USD) is allowed across the entire portfolio?
# If buying GBPUSD pushes our net USD short exposure past MAX_PORTFOLIO_CORRELATION, block it.
MAX_PORTFOLIO_CORRELATION = float(os.getenv("MAX_PORTFOLIO_CORRELATION", 0.75))

# ─── MiroFish Prediction Engine ──────────────────────────────────────────
MIROFISH_ENABLED = os.getenv("MIROFISH_ENABLED", "False").lower() == "true"
MIROFISH_API_URL = os.getenv("MIROFISH_API_URL", "http://localhost:5001")
MIROFISH_SIMULATION_ROUNDS = int(os.getenv("MIROFISH_SIMULATION_ROUNDS", 20))
MIROFISH_CACHE_MINUTES = int(os.getenv("MIROFISH_CACHE_MINUTES", 60))
MIROFISH_MAX_CONFLUENCE_BONUS = 1  # Max +1 to confluence score from MiroFish

# ─── Massive.com Financial Data ──────────────────────────────────────────
MASSIVE_ENABLED = os.getenv("MASSIVE_ENABLED", "True").lower() == "true"
MASSIVE_API_KEY = os.getenv("MASSIVE_API_KEY", "")
MASSIVE_ACCESS_KEY_ID = os.getenv("MASSIVE_ACCESS_KEY_ID", "")
MASSIVE_SECRET_ACCESS_KEY = os.getenv("MASSIVE_SECRET_ACCESS_KEY", "")
MASSIVE_S3_ENDPOINT = os.getenv("MASSIVE_S3_ENDPOINT", "https://files.massive.com")
MASSIVE_S3_BUCKET = os.getenv("MASSIVE_S3_BUCKET", "flatfiles")
MASSIVE_WS_ENABLED = os.getenv("MASSIVE_WS_ENABLED", "True").lower() == "true"  # Real-time WebSocket feed
MASSIVE_REST_FALLBACK = os.getenv("MASSIVE_REST_FALLBACK", "True").lower() == "true"  # Use REST when MT5 fails

# ─── Backtesting Settings ────────────────────────────────────────────────
BACKTEST_SPREAD_PIPS = float(os.getenv("BACKTEST_SPREAD_PIPS", 2.0))       # Default spread for XAUUSD backtest
BACKTEST_SLIPPAGE_PIPS = float(os.getenv("BACKTEST_SLIPPAGE_PIPS", 0.5))   # Default slippage
BACKTEST_INITIAL_EQUITY = float(os.getenv("BACKTEST_INITIAL_EQUITY", 10000.0))  # Starting equity
BACKTEST_RISK_PERCENT = float(os.getenv("BACKTEST_RISK_PERCENT", 1.0))     # Risk per trade (%)

# ─── Adaptive Signal Scoring ─────────────────────────────────────────────
VOLUME_CONFIRMATION_MULTIPLIER = float(os.getenv("VOLUME_CONFIRMATION_MULTIPLIER", 1.5))  # Volume must exceed N x 20-period average for breakout confirmation
SIGNAL_DECAY_CANDLES = int(os.getenv("SIGNAL_DECAY_CANDLES", 2))  # Cancel pending signals not filled within N candles (N * 5 min for M5)
MTF_ALIGNMENT_REQUIRED = os.getenv("MTF_ALIGNMENT_REQUIRED", "True").lower() == "true"  # Require M5+M15+H1 agreement for max score

# ─── Performance Analytics Settings ──────────────────────────────────────
PERF_ROLLING_WINDOW = int(os.getenv("PERF_ROLLING_WINDOW", 20))
PERF_MIN_WIN_RATE = float(os.getenv("PERF_MIN_WIN_RATE", 0.35))
PERF_MIN_SHARPE = float(os.getenv("PERF_MIN_SHARPE", -0.5))
PERF_DEGRADED_SIZE_FACTOR = float(os.getenv("PERF_DEGRADED_SIZE_FACTOR", 0.5))
PERF_RECOVERY_SIZE_FACTOR = float(os.getenv("PERF_RECOVERY_SIZE_FACTOR", 0.75))
PERF_CONSECUTIVE_LOSS_THRESHOLD = int(os.getenv("PERF_CONSECUTIVE_LOSS_THRESHOLD", 5))

# ─── Enhanced Risk Management Settings ───────────────────────────────────
MAX_PORTFOLIO_HEAT_PERCENT = float(os.getenv("MAX_PORTFOLIO_HEAT_PERCENT", 3.0))
SESSION_CLOSE_RISK_REDUCTION = float(os.getenv("SESSION_CLOSE_RISK_REDUCTION", 0.3))
DYNAMIC_DAILY_LIMIT_ENABLED = os.getenv("DYNAMIC_DAILY_LIMIT_ENABLED", "True").lower() == "true"
DRAWDOWN_TIER_1_PERCENT = float(os.getenv("DRAWDOWN_TIER_1_PERCENT", 1.0))
DRAWDOWN_TIER_2_PERCENT = float(os.getenv("DRAWDOWN_TIER_2_PERCENT", 2.0))
DRAWDOWN_SHUTDOWN_PERCENT = float(os.getenv("DRAWDOWN_SHUTDOWN_PERCENT", 2.5))

# ─── Trade Quality Filter Settings ──────────────────────────────────────
TRADE_QUALITY_FILTER_ENABLED = os.getenv("TRADE_QUALITY_FILTER_ENABLED", "True").lower() == "true"
NEWS_QUALITY_BUFFER_MINUTES = int(os.getenv("NEWS_QUALITY_BUFFER_MINUTES", 15))
SPREAD_QUALITY_MULTIPLIER = float(os.getenv("SPREAD_QUALITY_MULTIPLIER", 2.0))
VOLATILITY_SPIKE_MULTIPLIER = float(os.getenv("VOLATILITY_SPIKE_MULTIPLIER", 3.0))
ROUND_NUMBER_BUFFER_PIPS = int(os.getenv("ROUND_NUMBER_BUFFER_PIPS", 30))
ROUND_NUMBER_INTERVAL = int(os.getenv("ROUND_NUMBER_INTERVAL", 100))

# ─── Strategy Parameter Auto-Tuning ─────────────────────────────────────
AUTO_TUNE_ENABLED = os.getenv("AUTO_TUNE_ENABLED", "False").lower() == "true"
AUTO_TUNE_TRAIN_MONTHS = int(os.getenv("AUTO_TUNE_TRAIN_MONTHS", 3))
AUTO_TUNE_TEST_MONTHS = int(os.getenv("AUTO_TUNE_TEST_MONTHS", 1))
AUTO_TUNE_GUARDRAIL_PCT = float(os.getenv("AUTO_TUNE_GUARDRAIL_PCT", 0.2))
AUTO_TUNE_MIN_OOS_SHARPE = float(os.getenv("AUTO_TUNE_MIN_OOS_SHARPE", 0.5))
