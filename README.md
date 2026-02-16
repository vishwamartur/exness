# Institutional Swarm v4.0 (Exness)

A **Multi-Agent Asynchronous Trading System** for MetaTrader 5 (MT5). This system evolves beyond simple algos by using a swarm of specialized AI agents that debate, execute, and self-reflect on trades in real-time.

## 🧠 The Swarm Architecture

The monolithic strategy has been decomposed into 5 specialized Agents running on an **Async Event Loop**:

1.  **Analyst Agent (The "Fundamentalist")** 🌍
    -   *Role*: Scans macro news (ForexFactory) and Market Regime.
    -   *Tech*: Mistral 7B / Google Gemini.
    -   *Output*: "Risk-On", "Risk-Off", or "Range-Bound".
    -   *Memory*: Persists regime capability in **Shared Memory (SQLite)**.

2.  **Quant Agent (The "Technician")** 📊
    -   *Role*: Analyzes price action, indicators, and ML probabilities.
    -   *Tech*: XGBoost, RandomForest, Lag-Llama (Time-Series Transformer).
    -   *Output*: Signal Confidence (0-100%).

3.  **Researcher Agent (The "Debater")** ⚖️
    -   *Role*: Conducting a Bull vs. Bear debate before every trade.
    -   *Process*: Synthesizes Analyst & Quant data to reach a `GO/NO-GO` verdict.
    -   *Output*: Final Trade Decision & Reasoning.

4.  **Risk Agent (The "Warden")** 🛡️
    -   *Role*: Enforces capital preservation.
    -   *Checks*: Daily Drawdown, Circuit Breakers (Shared State), Correlation Matrices.
    -   *Power*: VETO capability over all other agents.

5.  **Critic Agent (The "Teacher")** 🎓
    -   *Role*: Post-mortem analysis of closed trades.
    -   *Action*: Reviews every P&L event, assigns a **Score (0-10)**, and logs a "Lesson Learned".

---

## 🚀 Key Features (v4.0)

### ⚡ Async Core
-   **Non-Blocking**: Scans 40+ symbols in parallel using `asyncio` & `ThreadPoolExecutor`.
-   **WebSocket Dashboard**: Streams live agent thoughts to a local UI (`dashboard.html` @ Port 8000).

### 💾 Shared Memory
-   **Persistence**: Uses `utils/shared_state.py` (SQLite) to remember state across restarts.
-   **Resilience**: Intelligent recovery of Daily Bias and Risk Limits after crashes.

### 📈 Tri-Level AI
1.  **XGBoost/RF**: For pattern recognition.
2.  **Lag-Llama**: For zero-shot time-series forecasting.
3.  **LLM (Mistral/Gemini)**: For qualitative reasoning and self-reflection.

---

## 🛠️ Installation

### Prerequisites
*   Windows OS (Required for Mt5)
*   Python 3.10+
*   MetaTrader 5 Terminal (Logged in)

### Setup
1.  **Clone & Install**:
    ```bash
    git clone https://github.com/vishwamartur/exness.git
    cd exness
    pip install -r requirements.txt
    ```

2.  **Configuration**:
    Create `.env`:
    ```env
    MT5_LOGIN=12345678
    MT5_PASSWORD=your_password
    MT5_SERVER=Exness-Real
    MISTRAL_API_KEY=your_key
    ```
    *See `config/settings.py` for advanced tuning.*

3.  **Train Models**:
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
Open `dashboard.html` in your browser to watch the agents think in real-time.

### 🛠️ Diagnostics
-   **`debug_async.py`**: Test the full loop with mock data.
-   **`debug_shared_memory.py`**: Verify database persistence.
-   **`debug_critic.py`**: Force-run the Self-Reflection agent.

---

## ⚠️ Disclaimer
**Institutional Swarm** is strictly for educational and research purposes. Financial trading involves significant risk. The authors are not responsible for losses.
