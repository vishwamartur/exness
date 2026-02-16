# Institutional Scalping Bot v2.8 (Exness)

A high-frequency algorithmic trading bot for MetaTrader 5 (MT5), engineered for institutional-grade execution on Exness. It leverages a **Tri-Level AI System** combining classical Machine Learning, Time-Series Foundation Models, and Large Language Models (LLM) for market analysis.

## 🚀 Key Features (v2.8)

### 🧠 Tri-Level AI Intelligence
1.  **Directional Classifier (Random Forest)**: 
    - Trained on historical price action with Triple Barrier Method labelling.
    - **ML Boost**: Signals with **>85% confidence** automatically override neutral trend filters.
2.  **Zero-Shot Forecaster (Lag-Llama / Chronos)**: 
    - A Time-Series Foundation Model that forecasts the next 12-minute trajectory.
    - Acts as a confirmation layer for the RF model.
3.  **LLM Validator (Mistral AI)**: 
    - The "Second Opinion" layer.
    - **Mistral-Small** analyzes technical indicators (RSI, ADX, Trend) and gives a qualitative Veto.
    - If Mistral says **BEARISH** when the bot says **BUY**, the trade is blocked.

### 🛡️ Institutional Risk Management
*   **H4 Trend Veto**: Strictly blocks trades against the 4-Hour trend structure ("The Trend is Your Friend").
*   **Dynamic Position Sizing**: 
    - Base Risk: 1% per trade.
    - Scaled Risk: Up to 2% for high-probability setups (>70%).
*   **Correlation Filter**: Prevents double exposure (e.g., won't buy `EURUSD` and `GBPUSD` simultaneously).
*   **News Filter**: Automatically halts trading 30 mins before/after high-impact news (ForexFactory).

### ⚡ Execution Engine
*   **Multi-Pair Scanning**: Monitors 11 pairs in parallel (`EURUSD`, `GBPUSD`, `XAUUSD`, etc.).
*   **Smart Entry**: Uses Limit Orders at Bid/Ask to minimize slippage.
*   **Stream Server**: WebSocket server pushes real-time telemetry to a local dashboard (Port 8000).

---

## 🛠️ Installation

### Prerequisites
*   Windows OS (Required for MT5 Terminal)
*   Python 3.10+
*   MetaTrader 5 Terminal (Logged in to Exness)

### Setup
1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/vishwamartur/exness.git
    cd exness
    ```

2.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
    *Note: Installs `torch`, `gluonts`, `xgboost`, and `mistralai` compatible libraries.*

3.  **Configuration**:
    Create a `.env` file in the root directory:
    ```env
    # MT5 Credentials
    MT5_LOGIN=12345678
    MT5_PASSWORD=your_password
    MT5_SERVER=Exness-Real
    MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe

    # AI Configuration
    MISTRAL_API_KEY=your_mistral_key_here
    
    # Strategy Settings
    MIN_CONFLUENCE_SCORE=3
    RF_PROB_THRESHOLD=0.65
    MAX_RISK_PER_TRADE=0.02
    ```

---

## 🖥️ Usage

### 1. Train the Models
Before first run, download data and train the classifier:
```bash
python train_model.py
```

### 2. Run the Bot
Start the institutional scanner:
```bash
python main.py
```
*The bot will start the WebSocket server and begin scanning M15 candles.*

### 3. Diagnostics
If trades are not executing, run the debug tools:
*   **`python debug_scan.py`**: Analyzes connection, logic, and why trades are rejected.
*   **`python debug_ml.py`**: checks ML model probability outputs and H4 trend alignment.

---

## 📊 Strategy Logic (Sureshot)

The bot scores every setup on a scale of **0 to 6** (Confluence Score):

| Factor | Points | Condition |
| :--- | :--- | :--- |
| **H4 Trend** | +1 | Trend Aligned |
| **H1 Trend** | +1 | Trend Aligned |
| **ML Signal** | +1 | Prob > Threshold (0.65) |
| **ML Boost** | **+2** | Prob > **0.85** (Confident) |
| **AI (Mistral)** | +1 | Bullish/Bearish Confirmation |
| **SMC** | +1 | Near Order Block / FVG |
| **ADX** | +1 | Volatility Present (>25) |

**Execution Rules:**
1.  **Standard**: Score ≥ **3**.
2.  **Override**: Score ≥ **2** IF ML Confidence > **85%**.
3.  **Veto**: If Mistral Strongly Disagrees -> **BLOCK**.

---

## ⚠️ Disclaimer
Trading Forex/CFDs involves substantial risk of loss. This software is for **educational purposes only**. The authors are not responsible for financial losses. Always test on a Demo account first.
