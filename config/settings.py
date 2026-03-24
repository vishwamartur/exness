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

# ─── Institutional Risk Management ───────────────────────────────────────
RISK_PERCENT = float(os.getenv("RISK_PERCENT", 1.0))       # 1% risk per scalp (XAUUSD focus)
MAX_RISK_PERCENT = float(os.getenv("MAX_RISK_PERCENT", 2.0))  # 2% max for A+ Gold setups

# ATR-Based Dynamic SL/TP — tight SL, wide TP for Gold scalping
ATR_SL_MULTIPLIER = float(os.getenv("ATR_SL_MULTIPLIER", 0.8))  # 0.8x ATR — tight SL for quick scalps
ATR_TP_MULTIPLIER = float(os.getenv("ATR_TP_MULTIPLIER", 2.0))  # 2.0x ATR — 1:2.5 R:R target

# Confluence Gating — relaxed for more trade opportunities
MIN_CONFLUENCE_SCORE = int(os.getenv("MIN_CONFLUENCE_SCORE", 2))  # Relaxed: 2 modules agree
SURESHOT_MIN_SCORE = int(os.getenv("SURESHOT_MIN_SCORE", 4))     # Sureshot at 4
RF_PROB_THRESHOLD = float(os.getenv("RF_PROB_THRESHOLD", 0.52))   # Relaxed: >52% ML edge is enough
MIN_RISK_REWARD_RATIO = float(os.getenv("MIN_RISK_REWARD_RATIO", 1.2)) # 1:1.2 minimum R:R

# ─── Kelly Criterion Position Sizing ─────────────────────────────────────
USE_KELLY = os.getenv("USE_KELLY", "True").lower() == "true"  # Enable Kelly Criterion
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", 0.25))  # Quarter-Kelly (safer, avoids ruin)
KELLY_MIN_TRADES = int(os.getenv("KELLY_MIN_TRADES", 20))   # Min trades before Kelly activates

# Cost Awareness (Critical for Retail Traders)
COMMISSION_PER_LOT = float(os.getenv("COMMISSION_PER_LOT", 7.0))  # $7 per lot round turn (Raw Spread)
MIN_NET_PROFIT_RATIO = float(os.getenv("MIN_NET_PROFIT_RATIO", 3.0)) # Profit must cover Commission x 3


#─── Trade Management────────────────────────────────────────────────────
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", 120))    # 2-minute cooldown for scalping
RISK_FACTOR_MAX = float(os.getenv("RISK_FACTOR_MAX", 2.0))    # Scale up to 2x on A+ setups
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", 9999))   # Unlimited trades (XAUUSD focus)
MAX_DAILY_LOSS_USD = float(os.getenv("MAX_DAILY_LOSS_USD", 100.0)) # Hard stop if daily loss > $100
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", 50))  # Unlimited positions (XAUUSD focus)
LIMIT_ORDER_EXPIRATION_MINUTES = int(os.getenv("LIMIT_ORDER_EXPIRATION_MINUTES", 10)) # Short expiry for scalps
MAX_CONCURRENT_TRADES = int(os.getenv("MAX_CONCURRENT_TRADES", 10))  # Allow multiple Gold positions
MAX_SPREAD_PIPS = float(os.getenv("MAX_SPREAD_PIPS", 3.0))   # Reject high-spread entries (forex)
MAX_SPREAD_PIPS_CRYPTO = float(os.getenv("MAX_SPREAD_PIPS_CRYPTO", 20000.0))  # Wider for crypto
MAX_SPREAD_PIPS_COMMODITY = float(os.getenv("MAX_SPREAD_PIPS_COMMODITY", 80.0))  # Tighter for Gold scalping

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

# Trailing Stop (ATR Based)
TRAILING_STOP_ATR_ACTIVATE = float(os.getenv("TRAILING_STOP_ATR_ACTIVATE", 1.5)) # Activate trail at 1.5R profit (Gold scalps)
TRAILING_STOP_ATR_STEP = float(os.getenv("TRAILING_STOP_ATR_STEP", 0.5))         # Trail behind by 0.5 ATR (tight for scalps)
# Legacy Fixed % (keeping for backwards compatibility if needed, but primary is ATR)
TRAILING_STOP_ACTIVATE_PERCENT = float(os.getenv("TRAILING_ACTIVATE", 0.005))
TRAILING_STOP_STEP_PERCENT = float(os.getenv("TRAILING_STEP", 0.001))

# Partial Profit Taking
PARTIAL_CLOSE_FRACTION = float(os.getenv("PARTIAL_CLOSE_FRACTION", 0.50))  # Close 50% at TP1 for scalps (lock profit)
BREAKEVEN_RR = float(os.getenv("BREAKEVEN_RR", 0.5))  # Breakeven at 0.5R — protect capital fast

# ─── Multi-Timeframe Trend Filters ───────────────────────────────────────
M5_TREND_FILTER = os.getenv("M5_TREND_FILTER", "True").lower() == "true"   # M5 confirms M1 direction
H1_TREND_FILTER = os.getenv("H1_TREND_FILTER", "True").lower() == "true"
H4_TREND_FILTER = os.getenv("H4_TREND_FILTER", "True").lower() == "true"   # Prevent counter-trend trades

# ─── News Integration ────────────────────────────────────────────────────
NEWS_CALENDAR_URL = os.getenv("NEWS_CALENDAR_URL", "https://nfs.faireconomy.media/ff_calendar_thisweek.json")
NEWS_CALENDAR_CACHE_MINUTES = int(os.getenv("NEWS_CALENDAR_CACHE_MINUTES", 60))  # Refresh every 60 min
NEWS_PRE_MINUTES = int(os.getenv("NEWS_PRE_MINUTES", 15))   # Block 15 min before high-impact news
NEWS_POST_MINUTES = int(os.getenv("NEWS_POST_MINUTES", 15)) # Block 15 min after high-impact news

# ─── Fake News Detection ─────────────────────────────────────────────────
FAKE_NEWS_DETECTION_ENABLED = os.getenv("FAKE_NEWS_DETECTION_ENABLED", "True").lower() == "true"
FAKE_NEWS_MIN_CREDIBILITY = float(os.getenv("FAKE_NEWS_MIN_CREDIBILITY", 0.4))    # Below this = flagged suspicious
FAKE_NEWS_DISCOUNT_FACTOR = float(os.getenv("FAKE_NEWS_DISCOUNT_FACTOR", 0.1))    # Reduce news weight to 10% when flagged

# ─── Session Awareness (UTC hours) — widened for XAUUSD scalping ─────────
TRADE_SESSIONS = {
    "london":      {"start": 7.0, "end": 11.0},  # London session (4 hours)
    "ny":          {"start": 13.0, "end": 17.0},  # New York session (4 hours)
    "overlap":     {"start": 13.0, "end": 16.0},  # London/NY overlap (peak Gold)
}
SESSION_FILTER = os.getenv("SESSION_FILTER", "False").lower() == "true"  # Disabled — Gold 24/5

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
