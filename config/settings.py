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

# List of all pairs to trade
# We prioritize Majors and Liquid Minors for tightest spreads
SYMBOLS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "GBPJPY", "AUDJPY"
]

TIMEFRAME = os.getenv("TIMEFRAME", "M1")
LOT_SIZE = float(os.getenv("LOT_SIZE", 0.01))
SL_PIPS = int(os.getenv("SL_PIPS", 10))
TP_PIPS = int(os.getenv("TP_PIPS", 20))
DEVIATION = int(os.getenv("DEVIATION", 20))
LEVERAGE = int(os.getenv("LEVERAGE", 1000))

# Advanced Strategy Settings
COOLDOWN_SECONDS = 60
RISK_FACTOR_MAX = 2.5
H1_TREND_FILTER = True
USE_LIMIT_ORDERS = True
TRAILING_STOP_ACTIVATE_PERCENT = 0.005 # 0.5% profit
TRAILING_STOP_STEP_PERCENT = 0.001 # 0.1% step

# Data Settings
HISTORY_BARS = 10000
TRAIN_TEST_SPLIT = 0.8

# Model Settings
MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models", "scalper_v1.pkl")

# Lag-Llama Settings
USE_LAG_LLAMA = os.getenv("USE_LAG_LLAMA", "True").lower() == "true"
LAG_LLAMA_CHECKPOINT = os.getenv("LAG_LLAMA_CHECKPOINT", "time-series-foundation-models/Lag-Llama")
LAG_LLAMA_REPO_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "vendor", "lag-llama")
