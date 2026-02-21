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
    "EURUSD", "GBPUSD", "USDJPY", # "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
]

SYMBOLS_FOREX_MINORS_BASE = [
    # "EURGBP", "EURJPY", "GBPJPY", "EURAUD", "EURCAD", "EURCHF", "EURNZD",
    # "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD",
    # "AUDJPY", "AUDCAD", "AUDCHF", "AUDNZD",
    # "NZDJPY", "NZDCAD", "NZDCHF",
    # "CADJPY", "CADCHF", "CHFJPY",
]

SYMBOLS_CRYPTO_BASE = [
    "BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD", "BCHUSD",
    "BTCJPY", "BTCKRW",
]

SYMBOLS_COMMODITIES_BASE = [
    "XAUUSD", "XAGUSD",           # Gold, Silver
    "XPTUSD", "XPDUSD",           # Platinum, Palladium
    "USOIL", "UKOIL",             # Crude Oil
    "XNGUSD",                      # Natural Gas
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


TIMEFRAME = "M1"  # Hardcoded for Scalping (User Request)
print(f"[SETTINGS] TIMEFRAME set to: {TIMEFRAME}")
LOT_SIZE = float(os.getenv("LOT_SIZE", 0.10))  # Bulk sizing baseline
DEVIATION = int(os.getenv("DEVIATION", 20))
LEVERAGE = int(os.getenv("LEVERAGE", 1000))

# ─── Institutional Risk Management ───────────────────────────────────────
RISK_PERCENT = float(os.getenv("RISK_PERCENT", 2.0))       # Risk 2% of account per trade
MAX_RISK_PERCENT = float(os.getenv("MAX_RISK_PERCENT", 5.0))  # Max risk for A+ setups (confluence >= 5)

# ATR-Based Dynamic SL/TP (replaces fixed pips)
ATR_SL_MULTIPLIER = float(os.getenv("ATR_SL_MULTIPLIER", 1.5))  # SL = 1.5x ATR
ATR_TP_MULTIPLIER = float(os.getenv("ATR_TP_MULTIPLIER", 3.5))  # TP = 3.5x ATR (Higher Reward)

# Confluence Gating
MIN_CONFLUENCE_SCORE = int(os.getenv("MIN_CONFLUENCE_SCORE", 2))  # AGGRESSIVE: 2 confluences (Was 3)
SURESHOT_MIN_SCORE = int(os.getenv("SURESHOT_MIN_SCORE", 3))     # AGGRESSIVE: Sureshot at 3 (Was 5)
RF_PROB_THRESHOLD = float(os.getenv("RF_PROB_THRESHOLD", 0.50))   # AGGRESSIVE: 50% Confidence (Was 0.65)
MIN_RISK_REWARD_RATIO = float(os.getenv("MIN_RISK_REWARD_RATIO", 1.5)) # Relaxed 1:1.5 R:R

# ─── Kelly Criterion Position Sizing ─────────────────────────────────────
USE_KELLY = os.getenv("USE_KELLY", "True").lower() == "true"  # Enable Kelly Criterion
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", 0.25))  # Quarter-Kelly (safer, avoids ruin)
KELLY_MIN_TRADES = int(os.getenv("KELLY_MIN_TRADES", 20))   # Min trades before Kelly activates

# Cost Awareness
COMMISSION_PER_LOT = float(os.getenv("COMMISSION_PER_LOT", 7.0))  # $7 per lot round turn (Raw Spread)
MIN_NET_PROFIT_RATIO = float(os.getenv("MIN_NET_PROFIT_RATIO", 2.0)) # Profit must cover Commission x 2


# ─── Trade Management ────────────────────────────────────────────────────
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", 300))  # 5 minutes between trades
RISK_FACTOR_MAX = float(os.getenv("RISK_FACTOR_MAX", 3.0))  # Scale up for A+ setups
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", 20))     # Cap daily trade count (Reduced from 100)
MAX_DAILY_LOSS_USD = float(os.getenv("MAX_DAILY_LOSS_USD", 50.0)) # Stop trading if daily loss > $50
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", 5))  # Max simultaneous positions total
MAX_CONCURRENT_TRADES = int(os.getenv("MAX_CONCURRENT_TRADES", 3))  # Hard cap concurrent scalp trades
MAX_SPREAD_PIPS = float(os.getenv("MAX_SPREAD_PIPS", 3.0))   # Reject high-spread entries (forex)
MAX_SPREAD_PIPS_CRYPTO = float(os.getenv("MAX_SPREAD_PIPS_CRYPTO", 20000.0))  # Wider for crypto (~$200 spread allowed)
MAX_SPREAD_PIPS_COMMODITY = float(os.getenv("MAX_SPREAD_PIPS_COMMODITY", 150.0))  # Commodities (~$0.50 spread on Gold)

# ─── Volatility-Adaptive Entry ───────────────────────────────────────────
# Minimum ATR required to enter a scalp trade (avoid dead/ranging markets)
VOLATILITY_ATR_MIN = float(os.getenv("VOLATILITY_ATR_MIN", 0.00015))  # 1.5 pips min for Forex M1
VOLATILITY_ATR_MIN_CRYPTO = float(os.getenv("VOLATILITY_ATR_MIN_CRYPTO", 50.0))  # $50 min for Crypto
VOLATILITY_ATR_MIN_COMMODITY = float(os.getenv("VOLATILITY_ATR_MIN_COMMODITY", 0.5))  # 50c min for Commodities

# ─── Strict Scalp Session Windows (UTC) ──────────────────────────────────
# ONLY trade during London Open and NY Open for tight spreads + volume
SCALP_SESSION_FILTER = os.getenv("SCALP_SESSION_FILTER", "True").lower() == "true"
SCALP_SESSIONS = [
    {"name": "London Open", "start": 7, "end": 10},   # 07:00-10:00 UTC
    {"name": "NY Open",     "start": 13, "end": 16},  # 13:00-16:00 UTC
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

# Trailing Stop (ATR Based)
TRAILING_STOP_ATR_ACTIVATE = float(os.getenv("TRAILING_STOP_ATR_ACTIVATE", 2.0)) # Activate when profit > 2.0 ATR
TRAILING_STOP_ATR_STEP = float(os.getenv("TRAILING_STOP_ATR_STEP", 0.5))         # Trail behind by 0.5 ATR
# Legacy Fixed % (keeping for backwards compatibility if needed, but primary is ATR)
TRAILING_STOP_ACTIVATE_PERCENT = float(os.getenv("TRAILING_ACTIVATE", 0.005))
TRAILING_STOP_STEP_PERCENT = float(os.getenv("TRAILING_STEP", 0.001))

# Partial Profit Taking
PARTIAL_CLOSE_FRACTION = float(os.getenv("PARTIAL_CLOSE_FRACTION", 0.25))  # Close 25% (Let winners run)
BREAKEVEN_RR = float(os.getenv("BREAKEVEN_RR", 0.8))  # Move SL to breakeven at 0.8R (Was 0.6R)

# ─── Multi-Timeframe Trend Filters ───────────────────────────────────────
M5_TREND_FILTER = os.getenv("M5_TREND_FILTER", "True").lower() == "true"   # M5 confirms M1 direction
H1_TREND_FILTER = os.getenv("H1_TREND_FILTER", "True").lower() == "true"
H4_TREND_FILTER = os.getenv("H4_TREND_FILTER", "True").lower() == "true"   # Prevent counter-trend trades

# ─── News Integration ────────────────────────────────────────────────────
NEWS_CALENDAR_URL = os.getenv("NEWS_CALENDAR_URL", "https://nfs.faireconomy.media/ff_calendar_thisweek.json")
NEWS_CALENDAR_CACHE_MINUTES = int(os.getenv("NEWS_CALENDAR_CACHE_MINUTES", 60))  # Refresh every 60 min
NEWS_PRE_MINUTES = int(os.getenv("NEWS_PRE_MINUTES", 15))   # Block 15 min before high-impact news
NEWS_POST_MINUTES = int(os.getenv("NEWS_POST_MINUTES", 15)) # Block 15 min after high-impact news

# ─── Session Awareness (UTC hours) ──────────────────────────────────────
TRADE_SESSIONS = {
    "london":    {"start": 8,  "end": 16},  # London (08:00 - 16:00 UTC)
    "new_york":  {"start": 13, "end": 21},  # New York (13:00 - 21:00 UTC)
    "overlap":   {"start": 13, "end": 16},  # Overlap
}
SESSION_FILTER = os.getenv("SESSION_FILTER", "True").lower() == "true"

# ─── Data Settings ───────────────────────────────────────────────────────
HISTORY_BARS = 10000
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

# Lag-Llama Settings
USE_LAG_LLAMA = False # os.getenv("USE_LAG_LLAMA", "True").lower() == "true"
LAG_LLAMA_CHECKPOINT = os.getenv("LAG_LLAMA_CHECKPOINT", "time-series-foundation-models/Lag-Llama")
LAG_LLAMA_REPO_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "vendor", "lag-llama")

# LSTM Settings
USE_LSTM = os.getenv("USE_LSTM", "True").lower() == "true"
LSTM_MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", f"lstm_{SYMBOL}.pth")
LSTM_SCALER_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", f"lstm_{SYMBOL}_scaler.pkl")
LSTM_SEQ_LENGTH = 60
