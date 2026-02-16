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

SYMBOLS_FOREX_MAJORS_BASE = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
]

SYMBOLS_FOREX_MINORS_BASE = [
    "EURGBP", "EURJPY", "GBPJPY", "EURAUD", "EURCAD", "EURCHF", "EURNZD",
    "GBPAUD", "GBPCAD", "GBPCHF", "GBPNZD",
    "AUDJPY", "AUDCAD", "AUDCHF", "AUDNZD",
    "NZDJPY", "NZDCAD", "NZDCHF",
    "CADJPY", "CADCHF", "CHFJPY",
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


TIMEFRAME = os.getenv("TIMEFRAME", "M15")  # M15 for institutional quality
LOT_SIZE = float(os.getenv("LOT_SIZE", 0.10))  # Bulk sizing baseline
DEVIATION = int(os.getenv("DEVIATION", 20))
LEVERAGE = int(os.getenv("LEVERAGE", 1000))

# ─── Institutional Risk Management ───────────────────────────────────────
RISK_PERCENT = float(os.getenv("RISK_PERCENT", 2.0))       # Risk 2% of account per trade
MAX_RISK_PERCENT = float(os.getenv("MAX_RISK_PERCENT", 5.0))  # Max risk for A+ setups (confluence >= 5)

# ATR-Based Dynamic SL/TP (replaces fixed pips)
ATR_SL_MULTIPLIER = float(os.getenv("ATR_SL_MULTIPLIER", 1.5))  # SL = 1.5x ATR
ATR_TP_MULTIPLIER = float(os.getenv("ATR_TP_MULTIPLIER", 3.0))  # TP = 3.0x ATR (2:1 R:R)

# Confluence Gating
MIN_CONFLUENCE_SCORE = int(os.getenv("MIN_CONFLUENCE_SCORE", 3))  # Minimum 3 confluences to enter
SURESHOT_MIN_SCORE = int(os.getenv("SURESHOT_MIN_SCORE", 5))     # Sureshot mode: only 5+ score fires
RF_PROB_THRESHOLD = float(os.getenv("RF_PROB_THRESHOLD", 0.70))   # Stricter RF threshold (was 0.65)

# ─── Trade Management ────────────────────────────────────────────────────
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", 300))  # 5 minutes between trades
RISK_FACTOR_MAX = float(os.getenv("RISK_FACTOR_MAX", 3.0))  # Scale up for A+ setups
MAX_DAILY_TRADES = int(os.getenv("MAX_DAILY_TRADES", 5))     # Cap daily trade count
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", 2))  # Max simultaneous positions total
MAX_SPREAD_PIPS = float(os.getenv("MAX_SPREAD_PIPS", 3.0))   # Reject high-spread entries (forex)
MAX_SPREAD_PIPS_CRYPTO = float(os.getenv("MAX_SPREAD_PIPS_CRYPTO", 50.0))  # Wider for crypto
MAX_SPREAD_PIPS_COMMODITY = float(os.getenv("MAX_SPREAD_PIPS_COMMODITY", 10.0))  # Commodities

# Trailing Stop (institutional targets)
TRAILING_STOP_ACTIVATE_PERCENT = float(os.getenv("TRAILING_ACTIVATE", 0.01))  # 1% profit activation
TRAILING_STOP_STEP_PERCENT = float(os.getenv("TRAILING_STEP", 0.002))         # 0.2% step

# Partial Profit Taking
PARTIAL_CLOSE_FRACTION = float(os.getenv("PARTIAL_CLOSE_FRACTION", 0.5))  # Close 50% at first TP
BREAKEVEN_RR = float(os.getenv("BREAKEVEN_RR", 1.0))  # Move SL to breakeven at 1:1 R:R

# ─── Multi-Timeframe Trend Filters ───────────────────────────────────────
H1_TREND_FILTER = True
H4_TREND_FILTER = False  # Disabled to allow ML-based reversals (Scalping)

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

# Lag-Llama Settings
USE_LAG_LLAMA = False # os.getenv("USE_LAG_LLAMA", "True").lower() == "true"
LAG_LLAMA_CHECKPOINT = os.getenv("LAG_LLAMA_CHECKPOINT", "time-series-foundation-models/Lag-Llama")
LAG_LLAMA_REPO_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "vendor", "lag-llama")

# LSTM Settings
USE_LSTM = os.getenv("USE_LSTM", "True").lower() == "true"
LSTM_MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", f"lstm_{SYMBOL}.pth")
LSTM_SCALER_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", f"lstm_{SYMBOL}_scaler.pkl")
LSTM_SEQ_LENGTH = 60
