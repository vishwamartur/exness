# Institutional Swarm (MT5 Agentic v2.2)

A **Multi-Agent Asynchronous Trading System** for MetaTrader 5 (MT5). This system evolves beyond simple algos by using a swarm of specialized AI agents that debate, execute, and self-reflect on trades in real-time, designed for institutional-grade execution on Exness (and other MT5 brokers).

## 🧠 The Agentic Architecture

The strategy is decomposed into specialized Agents running on an **Async Event Loop**, with a **PairAgent** dedicated to each specific symbol:

1.  **PairAgent (The "Executor")** ⚡
    -   *Role*: Dedicated manager for a single symbol (e.g., EURUSD Agent).
    -   *Action*: Orchestrates scans, manages open positions, and executes trades autonomously.
    -   *Logic*: Combines signals from Analyst, Quant, and Risk agents.

2.  **Analyst Agent (The "Fundamentalist")** 🌍
    -   *Role*: Scans macro news and determines Market Regime.
    -   *Tech*: **Google Gemini 1.5** (Pro/Flash) via REST API (No library conflicts).
    -   *Output*: "Risk-On", "Risk-Off", or "Range-Bound".
    -   *Memory*: Persists regime capability in shared state.

3.  **Quant Agent (The "Technician")** 📊
    -   *Role*: Analyzes price action, technicals, and ML probabilities.
    -   *Tech*: 
        -   **Smart Money Concepts (SMC)**: Order Blocks, Fair Value Gaps (FVG), Liquidity Sweeps, Break of Structure (BOS).
        -   **ML Models**: XGBoost / Random Forest for pattern recognition.
        -   **Lag-Llama**: (Optional) Zero-shot time-series forecasting.
    -   *Output*: Confluence Score and Signal Confidence.

4.  **Researcher Agent (The "Debater")** ⚖️
    -   *Role*: Conducts a Bull vs. Bear debate before **every** trade.
    -   *Process*: Synthesizes Analyst & Quant data to reach a `GO/NO-GO` verdict.
    -   *Tech*: LLM-based reasoning (Gemini/Mistral).
    -   *Output*: Final Trade Decision & Reasoning.

5.  **Risk Agent (The "Warden")** 🛡️
    -   *Role*: Enforces capital preservation.
    -   *Checks*: Dynamic Position Sizing (ATR), Daily Drawdown, Max Correlations.
    -   *Power*: **VETO** capability over all other agents.

6.  **Critic Agent (The "Teacher")** 🎓
    -   *Role*: Post-mortem analysis of closed trades.
    -   *Action*: Reviews P&L events, assigns a **Score (0-10)**, and logs "Lessons Learned".

---

## 🚀 Key Features

### ⚡ Institutional Logic
-   **Smart Money Concepts**: Automatically detects Order Blocks, FVGs, and Liquidity Pools to trade like the banks.
-   **Regime Filtering**: Only trades trend-following setups during "Trending" regimes and mean-reversion during "Ranging".
-   **Session Awareness**:  Filters trades based on London/New York session liquidity.

### ⚡ Async Core & UX
-   **Non-Blocking Scans**: Scans 40+ symbols in parallel using `asyncio`.
-   **Real-Time Dashboard**: `dashboard.html` connects via WebSockets (Port 8000) to show live agent thoughts, scan results, and debates.

### 🛡️ Risk Management
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
    # MISTRAL_API_KEY=your_mistral_key (Optional fallback)
    
    # Operational
    RISK_PERCENT=2.0
    MAX_DAILY_TRADES=5
    ```
    *See `config/settings.py` for all available options.*

3.  **Train Models** (Initial Setup):
    ```bash
    python train_model.py
    ```

---

## 🖥️ Usage

### 🟢 Live Trading
Launch the Async Swarm:
```bash
python main_async.py
```

### 📊 Dashboard
Open `dashboard.html` in your browser. It will automatically connect to `ws://localhost:8000/ws` when the bot is running to visualize the swarm's activity.

### 🛠️ Diagnostics
-   **`debug_async.py`**: Test the loop with mock data (no real trades).
-   **`debug_gemini.py`**: Test the AI connection and prompt analysis.
-   **`inspect_lag_llama.py`**: Check the status of the time-series model (if enabled).

---

## ⚠️ Disclaimer
**Institutional Swarm** is strictly for educational and research purposes. Financial trading involves significant risk of loss. The authors are frequently wrong and not responsible for your financial decisions.
