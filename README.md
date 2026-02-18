# Institutional Swarm (MT5 Agentic v2.2)

A **Multi-Agent Asynchronous Trading System** for MetaTrader 5 (MT5). This system evolves beyond simple algos by using a swarm of specialized AI agents that debate, execute, and self-reflect on trades in real-time, designed for institutional-grade execution on Exness (and other MT5 brokers).

## 🧠 The Agentic Architecture

The strategy is decomposed into specialized Agents running on an **Async Event Loop**, with a **PairAgent** dedicated to each specific symbol:

1.  **PairAgent (The "Executor")** ⚡
    -   *Role*: Dedicated manager for a single symbol (e.g., EURUSD Agent).
    -   *Action*: Orchestrates scans, manages open positions, and executes trades autonomously.
    -   *Features*: **Circuit Breaker** (Auto-pauses after N consecutive losses).

2.  **Analyst Agent (The "Fundamentalist")** 🌍
    -   *Role*: Scans macro news and determines Market Regime.
    -   *Tech*: **Google Gemini 1.5** (Pro/Flash) via REST API.
    -   *Memory*: Persists regime capability in **Shared Memory (SQLite)** `shared_state.db`.
    -   *Output*: "Risk-On", "Risk-Off", or "Range-Bound".

3.  **Quant Agent (The "Technician")** 📊
    -   *Role*: Analyzes price action, technicals, and ML probabilities.
    -   *Tech*: 
        -   **Smart Money Concepts (SMC)**: Order Blocks, Fair Value Gaps (FVG), Liquidity Sweeps.
        -   **Ensemble ML**: **XGBoost** & **Random Forest** for pattern recognition.
        -   **Deep Learning**: **LSTM** (Trend Prediction) & **Chronos-t5** / **Lag-Llama** (Zero-shot Time-Series Forecasting).
    -   *Output*: Confluence Score (0-6) and Signal Confidence.

4.  **Researcher Agent (The "Debater")** ⚖️
    -   *Role*: Conducts a Bull vs. Bear debate before **every** trade.
    -   *Process*: Synthesizes Analyst & Quant data to reach a `GO/NO-GO` verdict.
    -   *Tech*: LLM-based reasoning (Gemini/Mistral).
    -   *Output*: Final Trade Decision & Reasoning.

5.  **Risk Agent (The "Warden")** 🛡️
    -   *Role*: Enforces capital preservation and asymmetric payoffs.
    -   *Checks*: 
        -   **Tail Risk Isolation**: Hard limits for Gold/Crypto/Oil.
        -   **Kill Switch**: Auto-disables symbols with poor expectancy.
        -   **Asymmetric Payoff**: Enforces Min Reward:Risk (e.g., 2:1).
    -   *Power*: **VETO** capability over all other agents.

6.  **Critic Agent (The "Teacher")** 🎓
    -   *Role*: Post-mortem analysis of closed trades.
    -   *Action*: Reviews P&L events, assigns a **Score (0-10)**, and logs "Lessons Learned".

---

## 🚀 Key Features

### ⚡ Institutional Logic
-   **Smart Money Concepts**: Automatically detects Order Blocks, FVGs, and Liquidity Pools.
-   **Regime Filtering**: Only trades trend-following setups during "Trending" regimes.
-   **Session Awareness**:  Filters trades based on London/New York session liquidity.

### ⚡ Async Core & UX
-   **Non-Blocking Scans**: Scans 40+ symbols in parallel using `asyncio`.
-   **Shared State**: **SQLite** backend (`shared_state.db`) ensures resilience and state persistence across restarts.
-   **Real-Time Dashboard**: `dashboard.html` connects via WebSockets (Port 8000) to visualize the swarm.

### 🛡️ Advanced Risk Management
-   **Kill Switch**: Automatically disables specific symbols if they hit a losing streak or drawstring threshold.
-   **Tail Risk Caps**: Specific hard stop-loss limits ($) for volatile assets like XAUUSD/BTCUSD.
-   **ATR-Based Stops**: Dynamic SL/TP based on market volatility (e.g., 1.5x ATR SL, 3.5x ATR TP).
-   **Profit Preservation**: 
    -   **Partial Close**: Secures profit at fixed R-multiples.
    -   **Breakeven**: Moves SL to entry after partial take-profit.

---

## 🛠️ Installation

### Prerequisites
*   Windows OS (Required for MT5 Terminal)
*   Python 3.10+
*   MetaTrader 5 Terminal (Installed and Logged in)

### Setup

1.  **Clone & Install**:
    ```bash
    git clone https://github.com/vishwamartur/exness.git
    cd exness
    pip install -r requirements.txt
    ```

2.  **Configuration**:
    Create a `.env` file in the root directory:
    ```env
    MT5_LOGIN=12345678
    MT5_PASSWORD=your_password
    MT5_SERVER=Exness-Real
    
    # AI Keys
    GEMINI_API_KEY=your_google_gemini_key
    
    # Operational
    RISK_PERCENT=2.0
    MAX_DAILY_TRADES=20
    
    # Optional Overrides (See config/settings.py)
    USE_XGBOOST=True
    USE_LSTM=True
    # USE_LAG_LLAMA=False
    ```

3.  **Train Models** (Initial Setup):
    ```bash
    python train_model.py     # Trains Random Forest
    python train_xgboost.py   # Trains XGBoost
    python train_lstm.py      # Trains LSTM (Optional)
    ```

---

## 🖥️ Usage

### 🟢 Live Trading
Launch the Async Swarm:
```bash
python main_async.py
```

### 📊 Dashboard
Open `dashboard.html` in your browser. It will automatically connect to `ws://localhost:8000/ws` when the bot is running.

### 🛠️ Diagnostics
-   **`debug_scan.py`**: detailed single-pass scan of all agents (useful for debugging logic).
-   **`debug_async.py`**: Test the loop with mock data.
-   **`debug_gemini.py`**: Test the AI connection.
-   **`inspect_lag_llama.py`**: Check the status of time-series models.

---

## ⚠️ Disclaimer
**Institutional Swarm** is strictly for educational and research purposes. Financial trading involves significant risk of loss. The authors are frequently wrong and not responsible for your financial decisions.
