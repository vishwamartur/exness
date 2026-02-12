# MT5 ML Scalping Bot (Exness)

A high-frequency algorithmic trading bot for MetaTrader 5 (MT5), specifically designed for forex scalping. It leverages a **Dual-Engine AI System** combining traditional Machine Learning (Random Forest) with a Transformer-based Time-Series Foundation Model (**Amazon Chronos-T5** from Hugging Face).

## 🚀 Key Features

### 🧠 Dual-AI Intelligence
1.  **Directional Classifier (Random Forest)**: Trained on historical price action with Triple Barrier Method labelling. Predicts if price will hit TP before SL.
2.  **Zero-Shot Forecaster (Chronos-T5)**: A pre-trained Foundation Model that forecasts the next 12 minutes of price action.
    *   **Signal Logic**: Trades are taken only when *both* models agree on direction (Confluence).

### 🛡️ Institutional Risk Management
*   **H1 Trend Filter**: Automatically blocks trades against the dominant trend (50 SMA on H1 charts).
*   **Dynamic Position Sizing**: Scales probability-based risk. High conviction trades (>70% prob) get up to **2.5x** leverage.
*   **Trailing Stops**: Locks in profits once a trade moves **0.5%** in favor.
*   **Limit Orders**: Executes at Bid/Ask prices (Maker) to minimize spreads and slippage.
*   **Cooldown**: Forces disjoint trades (60s cooldown) to prevent over-trading.

### ⚡ Multi-Pair Execution
*   Simultaneously scans and trades **11 Currency Pairs**:
    *   Majors: `EURUSD`, `GBPUSD`, `USDJPY`, `USDCHF`, `AUDUSD`, `USDCAD`, `NZDUSD`
    *   Crosses: `EURGBP`, `EURJPY`, `GBPJPY`, `AUDJPY`

## 🛠️ Installation

### Prerequisites
*   Windows OS (Required for MT5 Terminal)
*   Python 3.8+
*   MetaTrader 5 Terminal (Logged in)

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
    *Note: This installs `torch` and `transformers` for the AI models.*

3.  **Configuration**:
    Create a `.env` file in the root directory:
    ```env
    MT5_LOGIN=12345678
    MT5_PASSWORD=your_password
    MT5_SERVER=Exness-Real
    MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
    ```

## 🖥️ Usage

### 1. Train the Model
Before first run, download data and train the classifier:
```bash
python train_model.py
```

### 2. Verify Execution
Test connection and permissions (places and cancels a pending order):
```bash
python verify_trade.py
```

### 3. Run the Bot
Start the specialized scalper:
```bash
python main.py
```

## 📊 Strategy Logic

The bot operates in a continuous loop:
1.  **Data Ingestion**: Fetches M1 candles for all 11 pairs.
2.  **Feature Engineering**: Calculates log returns, RSI Slope, Volatility (ATR), and Distance from MA.
3.  **Inference**:
    *   RF Model calculates probability `P(Profit)`.
    *   Chronos Model forecasts `Price(t+5)`.
4.  **Filters**:
    *   Is H1 Trend favorable?
    *   Is Spread acceptable?
    *   Is Cooldown active?
5.  **Execution**:
    *   If `P(Profit) > 0.55` AND `Forecast == Direction` AND `Trend == Ok`:
    *   **Place Limit Order**.

## ⚠️ Disclaimer
Trading Forex involves substantial risk. This software is for educational purposes only. Use on Demo accounts first.
