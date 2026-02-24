# Getting Started

<cite>
**Referenced Files in This Document**
- [README.md](file://README.md)
- [requirements.txt](file://requirements.txt)
- [.env](file://.env)
- [config/settings.py](file://config/settings.py)
- [main_async.py](file://main_async.py)
- [main.py](file://main.py)
- [execution/mt5_client.py](file://execution/mt5_client.py)
- [api/stream_server.py](file://api/stream_server.py)
- [utils/telegram_notifier.py](file://utils/telegram_notifier.py)
- [dashboard/package.json](file://dashboard/package.json)
- [dashboard/vite.config.js](file://dashboard/vite.config.js)
- [train_model.py](file://train_model.py)
- [optimize_walkforward.py](file://optimize_walkforward.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Prerequisites](#prerequisites)
3. [Installation](#installation)
4. [Environment Variables](#environment-variables)
5. [Initial System Startup](#initial-system-startup)
6. [Basic Verification](#basic-verification)
7. [Troubleshooting](#troubleshooting)
8. [Next Steps](#next-steps)

## Introduction
This guide helps you install and run the Institutional SureShot Scanner, a multi-agent asynchronous scalping system for MetaTrader 5. It covers prerequisites, step-by-step installation, environment configuration, startup, and verification steps. The system integrates a React dashboard and Telegram notifications, and it auto-detects available symbols on your Exness account.

## Prerequisites
Ensure your environment meets the following requirements before proceeding:
- Operating system: Windows (required by the MetaTrader 5 terminal)
- Python: 3.10 or newer
- MetaTrader 5 terminal: Installed and logged in
- Node.js: 18 or newer (for the React dashboard)

These requirements are documented in the project’s README and installation section.

**Section sources**
- [README.md](file://README.md#L87-L91)

## Installation
Follow these steps to install the system:

1) Clone the repository and install Python dependencies
- Clone the repository to your local machine.
- Navigate to the project root and install Python packages:
  - Command: pip install -r requirements.txt

2) Install dashboard dependencies
- Change directory to the dashboard folder and install Node.js dependencies:
  - Commands:
    - cd dashboard
    - npm install
  - Return to the project root when finished.

3) Configure environment variables
- Copy the provided .env.example to .env and set your credentials and preferences.
- See the Environment Variables section for a complete list and explanations.

4) (Optional) Train machine learning models
- Train models for Random Forest, XGBoost, and optionally LSTM:
  - python train_model.py
  - python train_xgboost.py
  - python train_lstm.py

5) (Optional) Run walk-forward optimization
- Optimize parameters across in-sample and out-of-sample windows:
  - python optimize_walkforward.py
  - The script writes best parameters to models/best_params.json and updates .env accordingly.

6) Start the system
- Launch the async orchestrator:
  - python main_async.py

**Section sources**
- [README.md](file://README.md#L85-L158)
- [requirements.txt](file://requirements.txt#L1-L17)
- [dashboard/package.json](file://dashboard/package.json#L1-L24)

## Environment Variables
Configure your .env file with the following categories and parameters. The system loads these variables via config/settings.py.

- MT5 Credentials
  - MT5_LOGIN: Your Exness account number
  - MT5_PASSWORD: Your Exness account password
  - MT5_SERVER: Your Exness server name
  - MT5_PATH: Path to MetaTrader 5 terminal executable

- Trading Settings
  - TIMEFRAME: Trading timeframe (hardcoded to M1 for scalping)
  - LOT_SIZE: Base lot size for orders
  - DEVIATION: Maximum slippage tolerance in points

- Risk Management
  - LEVERAGE: Account leverage
  - RISK_PERCENT: Percentage of account risk per trade
  - MAX_RISK_PERCENT: Upper bound for high-confidence setups

- Walk-Forward Optimized Parameters
  - ATR_SL_MULTIPLIER: Stop loss multiplier based on ATR
  - ATR_TP_MULTIPLIER: Take profit multiplier based on ATR
  - MIN_CONFLUENCE_SCORE: Minimum confluence score threshold
  - SURESHOT_MIN_SCORE: Minimum score for “SureShot” setups
  - RF_PROB_THRESHOLD: Minimum probability threshold for Random Forest predictions
  - MIN_RISK_REWARD_RATIO: Minimum acceptable risk-reward ratio

- Trade Management
  - COOLDOWN_SECONDS: Interval between scan cycles
  - MAX_DAILY_TRADES: Daily trade cap
  - MAX_OPEN_POSITIONS: Maximum open positions across symbols
  - MAX_CONCURRENT_TRADES: Concurrent scalp trades cap
  - MAX_SPREAD_PIPS: Maximum allowable spread for forex
  - MAX_SPREAD_PIPS_CRYPTO: Maximum allowable spread for crypto
  - MAX_SPREAD_PIPS_COMMODITY: Maximum allowable spread for commodities

- Spread & Session
  - MAX_SPREAD_PIPS: Maximum spread for forex
  - SESSION_FILTER: Enable/disable session gating
  - SCALP_SESSION_FILTER: Enable strict London/NY scalp sessions

- Trend Filters
  - M5_TREND_FILTER: Use M5 trend confirmation
  - H1_TREND_FILTER: Use H1 trend confirmation
  - H4_TREND_FILTER: Use H4 trend confirmation

- Kelly Criterion
  - USE_KELLY: Enable Kelly-based position sizing
  - KELLY_FRACTION: Fraction of Kelly to apply
  - KELLY_MIN_TRADES: Minimum trades before Kelly activates

- Volatility Entry
  - VOLATILITY_ATR_MIN: Minimum ATR threshold for entry
  - VOLATILITY_ATR_MIN_CRYPTO: Minimum ATR threshold for crypto
  - VOLATILITY_ATR_MIN_COMMODITY: Minimum ATR threshold for commodities

- Risk Limits
  - MAX_DAILY_LOSS_USD: Daily drawdown cap

- API Keys
  - MISTRAL_API_KEY: API key for Mistral (required for macro analysis and researcher agent)

- Telegram Notifications
  - TELEGRAM_BOT_TOKEN: Telegram bot token
  - TELEGRAM_CHAT_ID: Your Telegram chat ID

Notes:
- The system auto-detects available symbols on your Exness account and prunes non-USD-denominated exotic quotes.
- Some parameters are loaded from .env and others are derived from the optimization script.

**Section sources**
- [.env](file://.env#L1-L59)
- [config/settings.py](file://config/settings.py#L7-L201)
- [README.md](file://README.md#L107-L137)

## Initial System Startup
To start the system:

1) Ensure MetaTrader 5 is installed and logged in on Windows.
2) Confirm Python 3.10+ and Node.js 18+ are installed.
3) From the project root:
   - Start the async orchestrator:
     - python main_async.py

What happens at startup:
- The orchestrator initializes MT5, detects available symbols, and logs account details.
- It starts the FastAPI WebSocket server on port 8000 (auto-selected if 8000 is busy).
- It launches the React dashboard at http://localhost:5173 and opens it in your browser.
- It begins scanning all detected symbols asynchronously at intervals defined by COOLDOWN_SECONDS.

Integration points:
- MT5 client handles connection, symbol detection, and order placement.
- Stream server exposes REST endpoints and a WebSocket for the dashboard.
- Telegram notifier sends real-time alerts.

**Section sources**
- [main_async.py](file://main_async.py#L20-L96)
- [execution/mt5_client.py](file://execution/mt5_client.py#L18-L27)
- [api/stream_server.py](file://api/stream_server.py#L153-L173)
- [utils/telegram_notifier.py](file://utils/telegram_notifier.py#L30-L64)

## Basic Verification
After startup, verify the system is functioning:

1) Confirm MT5 connection
- The orchestrator prints account balance, equity, and leverage upon successful connection.

2) Verify symbol detection
- The system logs the number and categories of detected symbols (majors, minors, crypto, commodities).

3) Check the dashboard
- Visit http://localhost:5173 to confirm the React dashboard launched and is loading data.

4) Validate WebSocket and REST endpoints
- The stream server binds to port 8000 and serves:
  - WebSocket endpoint: ws://localhost:8000/ws
  - REST endpoints: /api/account, /api/positions, /api/trades, /api/scan, /api/state

5) Test Telegram notifications
- Ensure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set.
- Use the helper script to retrieve your Chat ID if needed.

6) Review logs
- The orchestrator prints periodic scan summaries and cycle timing.

**Section sources**
- [main_async.py](file://main_async.py#L24-L62)
- [api/stream_server.py](file://api/stream_server.py#L68-L141)
- [utils/telegram_notifier.py](file://utils/telegram_notifier.py#L154-L174)

## Troubleshooting
Common setup issues and resolutions:

- Python dependencies
  - Symptom: Import errors or missing modules.
  - Resolution: Reinstall dependencies using pip install -r requirements.txt.

- Node.js dependencies for the dashboard
  - Symptom: npm install fails or dashboard does not launch.
  - Resolution: Ensure Node.js 18+ is installed and run npm install inside the dashboard directory.

- MetaTrader 5 connection failures
  - Symptom: MT5 initialization or login fails.
  - Resolution: Verify MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, and MT5_PATH in .env. Ensure the MT5 terminal is installed and logged in.

- Port conflicts for the stream server
  - Symptom: Port 8000 already in use.
  - Resolution: The server attempts to bind to an open port in the 8000–8009 range. Check console output for the actual port.

- Telegram notifications not sending
  - Symptom: No messages delivered.
  - Resolution: Confirm TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set. Use the helper script to retrieve your Chat ID if needed.

- No symbols detected
  - Symptom: Zero instruments found.
  - Resolution: Ensure your Exness account has tradable instruments and is not restricted. The system filters out non-USD-denominated exotic quotes.

- Dashboard not opening automatically
  - Symptom: Vite dev server starts but browser does not open.
  - Resolution: Manually navigate to http://localhost:5173. Ensure the dashboard directory exists and npm install was run.

- Parameter conflicts after optimization
  - Symptom: Unexpected behavior after running optimize_walkforward.py.
  - Resolution: The script writes best parameters to models/best_params.json and updates .env. Confirm these values are present and loaded by config/settings.py.

**Section sources**
- [requirements.txt](file://requirements.txt#L1-L17)
- [dashboard/package.json](file://dashboard/package.json#L6-L10)
- [execution/mt5_client.py](file://execution/mt5_client.py#L18-L27)
- [api/stream_server.py](file://api/stream_server.py#L153-L173)
- [utils/telegram_notifier.py](file://utils/telegram_notifier.py#L30-L64)
- [optimize_walkforward.py](file://optimize_walkforward.py#L229-L236)

## Next Steps
- Train models: Run train_model.py, train_xgboost.py, and optionally train_lstm.py to enable ML-powered agents.
- Optimize parameters: Periodically run optimize_walkforward.py to update .env with walk-forward optimized parameters.
- Monitor and iterate: Use the dashboard and Telegram alerts to track performance and adjust settings as needed.

**Section sources**
- [README.md](file://README.md#L147-L158)
- [train_model.py](file://train_model.py#L108-L123)
- [optimize_walkforward.py](file://optimize_walkforward.py#L100-L117)