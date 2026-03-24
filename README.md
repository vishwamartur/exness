# 🤖 Institutional Swarm — MT5 Agentic Trading System v2.3

A **Multi-Agent Asynchronous Scalping System** for MetaTrader 5. A swarm of specialized AI agents scan all available pairs, debate every trade, manage risk, and push real-time alerts to a React dashboard and Telegram — running fully autonomously.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![MT5](https://img.shields.io/badge/MetaTrader-5-orange)](https://www.metatrader5.com)
[![React](https://img.shields.io/badge/Dashboard-React%20%2B%20Vite-61dafb)](https://vitejs.dev)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688)](https://fastapi.tiangolo.com)

---

## 🧠 Multi-Agent Architecture

Every scan cycle, a dedicated **PairAgent** runs independently for each symbol. Shared agents coordinate across the swarm:

```
InstitutionalStrategy (Orchestrator)
├── PairAgent × N          — one per symbol (EURUSD, BTCUSD, XAUUSD …)
│   ├── QuantAgent         — SMC + Ensemble ML + LSTM signal scoring
│   ├── MarketAnalyst      — Macro regime (Risk-On / Risk-Off / Range)
│   ├── ResearcherAgent    — LLM Bull vs Bear debate → GO / NO-GO verdict
│   ├── CriticAgent        — Post-trade review, lessons learned (async)
│   └── RiskManager        — Kill Switch, Payoff Mandate, ATR sizing
└── InstitutionalStrategy  — Scan loop, position management, Telegram + WebSocket events
```

| Agent | Role | Model |
|-------|------|-------|
| **QuantAgent** | Technical signals: SMC, FVG, confluence score 0–6 | XGBoost + Random Forest + TabTransformer + Sequence Transformer + LSTM |
| **MarketAnalyst** | News-driven regime classification | Groq (Llama 3) / Mistral / Gemini via REST |
| **ResearcherAgent** | Bull vs Bear debate → final GO/NO-GO | Groq (Llama 3) / Mistral / Gemini LLM |
| **CriticAgent** | Async post-mortem, trade score 0–10 | Groq (Llama 3) / Mistral / Gemini LLM |
| **RiskManager** | Pre-trade veto: kill switch, payoff, spread, session | Rule-based |

---

## 🚀 Key Features

### 📡 All-Symbol Scanning
- **Auto-detects** all tradeable pairs on the connected Exness account at startup via `detect_available_symbols()`
- Filters out trade-disabled reference symbols (e.g., BTCKRW) and exotic quote currencies automatically
- No hardcoded symbol list — adapts to whatever the broker provides

### ⚡ Scalping Engine
- **M1 timeframe** with multi-timeframe confirmation (M5, H1, H4 trend filters)
- Session gate — Forex: London (07–10 UTC) & NY (13–16 UTC) only; **Crypto exempt** (24/7)
- Minimum ATR volatility threshold — skips dead markets
- ATR-based dynamic SL/TP (`ATR_SL_MULTIPLIER` × ATR, `ATR_TP_MULTIPLIER` × ATR)
- Walk-forward optimised parameters (`optimize_walkforward.py`)

### 🛡️ Institutional Risk Management
| Feature | Detail |
|---------|--------|
| **Kill Switch** | Auto-disables symbol after sustained losses (configurable threshold) |
| **Payoff Mandate** | Blocks symbols where AvgLoss > 2× AvgWin historically |
| **Risk Override** | Whitelist key pairs (EURUSD, GBPUSD, BTCUSD…) to always allow |
| **ATR Position Sizing** | Kelly-adjusted lot size based on account equity and SL distance |
| **Daily Trade Limit** | Caps total trades per day |
| **Daily Loss Limit** | Hard stop on total daily drawdown |
| **Partial Close / Breakeven** | Locks in profit at 0.8R, closes 25% at first TP |
| **Trailing Stop** | ATR-based — activates at 2× ATR profit, trails 0.5× ATR |
| **News Blackout** | Skips pairs during high-impact calendar events |
| **Fake News Detection** | 5-signal credibility scorer discounts suspicious news before it influences trades |
| **Spread Gate** | Skips pairs with spread > configurable max |
| **NEUTRAL Guard** | Hard block — only BUY or SELL can be executed |
| **Adaptive Position Management** | Real-time ML-based position optimization (hold/expand/close) |

### 📊 React Dashboard (Real-Time)
- Vite + React live dashboard auto-launches when the bot starts
- Connects via WebSocket (`ws://localhost:8000/ws`) + REST polling every 5s
- **5 panels**: Account stats · Scanner grid (all pairs) · Open positions (live P&L) · Trade feed · Event log
- REST endpoints: `/api/account` `/api/positions` `/api/trades` `/api/scan` `/api/state`
- Positions fetched **live from MT5** on every REST call (not stale cache)

### 🤖 Adaptive Position Management

The system now includes intelligent position management that uses real-time ML predictions to optimize profits:

**Key Features**:
- **Real-time Analysis**: Continuously evaluates open positions using ML models
- **Dynamic Position Sizing**: Expands winning positions when market conditions are favorable
- **Profit Protection**: Automatically closes or partially closes positions based on ML predictions
- **Trend Alignment**: Holds positions longer when trend and ML signals align
- **Risk Management**: Closes positions when opposing signals are detected

### 🔍 Pre-Trade Analysis with RAG

Advanced pre-trade analysis system that prevents poor entry timing:

**Key Features**:
- **Multi-Timeframe Trend Analysis**: Analyzes trends across M1, M5, M15, H1, H4
- **ML-Based Entry Validation**: Uses Random Forest and XGBoost for entry confirmation
- **RAG Historical Context**: Retrieves similar historical patterns for context
- **Volatility Assessment**: Evaluates current market volatility for risk management
- **Momentum Analysis**: Checks RSI, MACD, and other momentum indicators
- **Regime Awareness**: Adjusts decisions based on market regime (Risk-On/Risk-Off)

### 🛡️ Fake News Detection

Multi-signal credibility engine that prevents the bot from acting on manipulated or unverified news:

| Signal | Weight | What It Checks |
|--------|--------|----------------|
| **Source Reputation** | 25% | Tiered whitelist (Reuters/Bloomberg → 1.0, Twitter/Telegram → 0.2) |
| **Cross-Source Corroboration** | 25% | Same claim reported by multiple independent sources |
| **Gemini AI Verification** | 20% | LLM plausibility check against current market conditions |
| **Linguistic Red Flags** | 15% | Clickbait, ALL-CAPS, sensationalism, hype language |
| **Temporal Consistency** | 15% | Event timing vs business hours (weekend Fed = suspicious) |

- Credibility score < 0.4 → news weight reduced from 70% to 7%
- Configurable via `FAKE_NEWS_MIN_CREDIBILITY` and `FAKE_NEWS_DISCOUNT_FACTOR`

**Decision Process**:
1. **Trend Capture**: Ensures alignment across multiple timeframes
2. **ML Validation**: Confirms entry with ensemble ML models
3. **RAG Context**: Checks historical performance of similar patterns
4. **Risk Assessment**: Evaluates volatility and momentum conditions
5. **Final Decision**: Weighted scoring system determines entry approval

**Decision Logic**:
- ML prediction confidence and direction
- Current trend strength and alignment
- Market volatility assessment
- Position profit/loss in pips
- Risk-reward ratio

**Actions Taken**:
- **HOLD**: Keep position when conditions are favorable
- **EXPAND**: Increase position size for strong winning trades
- **PARTIAL_CLOSE**: Lock in profits while maintaining exposure
- **CLOSE**: Exit positions when conditions turn unfavorable

### 📱 Telegram Alerts (@vcrpttrade_bot)
Real-time push notifications — non-blocking, never slows the trading loop:

| Alert | Trigger |
|-------|---------|
| 🤖 **Bot Started** | On startup with pair count |
| 📡 **Scan Signals** | When 1+ candidate pairs found |
| 🟢🔴 **Trade Executed** | Symbol, direction, lots, price, SL, TP |
| 🚨 **Kill Switch** | Symbol disabled, loss amount shown |
| ⚠️ **Generic Alert** | Any custom event |

---

## 🛠️ Installation

### Prerequisites
- Windows OS (MT5 terminal requirement)
- Python 3.10+
- MetaTrader 5 terminal installed and logged in
- Node.js 18+ (for the React dashboard)

### 1. Clone & Install
```bash
git clone https://github.com/vishwamartur/exness.git
cd exness
pip install -r requirements.txt
```

### 2. Install Dashboard Dependencies
```bash
cd dashboard
npm install
cd ..
```

### 3. Configure `.env`
```env
# MT5 Credentials
MT5_LOGIN=your_account_number
MT5_PASSWORD=your_password
MT5_SERVER=Exness-MT5Real8
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe

# Trading
TIMEFRAME=M1
LOT_SIZE=0.10
RISK_PERCENT=1.0

# Strategy Tuning (walk-forward optimised)
ATR_SL_MULTIPLIER=1.0
ATR_TP_MULTIPLIER=4.0
MIN_CONFLUENCE_SCORE=2
RF_PROB_THRESHOLD=0.45

# Risk
MAX_DAILY_TRADES=20
MAX_DAILY_LOSS_USD=50
SCALP_SESSION_FILTER=True

# Fake News Detection
FAKE_NEWS_DETECTION_ENABLED=True
FAKE_NEWS_MIN_CREDIBILITY=0.4
FAKE_NEWS_DISCOUNT_FACTOR=0.1

# AI (at least one required for ResearcherAgent)
GROQ_API_KEY=your_groq_key
MISTRAL_API_KEY=your_mistral_key

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 4. Get Telegram Chat ID
```bash
# 1. Message /start to your bot on Telegram
# 2. Run:
python f:\mt5\utils\telegram_notifier.py
# → prints your Chat ID to paste into .env
```

### 5. Train ML Models
```bash
python train_model.py                   # Random Forest
python train_xgboost.py                 # XGBoost
python train_tabtransformer.py          # TabTransformer (Attention-based tabular)
python train_sequence_transformer.py    # Sequence Transformer (High ROI Temporal Attention)
python train_lstm.py                    # LSTM (optional)
```

### 6. (Optional) Walk-Forward Optimisation
```bash
python optimize_walkforward.py
# → writes optimal params to models/best_params.json and updates .env
```

---

## 🖥️ Usage

### 🟢 Start the Bot
```bash
python main_async.py
```

This will:
1. Connect to MT5 and detect all available symbols
2. Start the FastAPI WebSocket server on port 8000
3. Auto-launch the React dashboard at `http://localhost:5173`
4. Send a Telegram startup notification
5. Begin scanning all pairs every ~180s

### 🔍 Diagnostics
| Script | Purpose |
|--------|---------|
| `debug_scan.py` | Single-pass scan of all agents |
| `debug_async.py` | Test async loop with mock data |
| `debug_gemini.py` | Test AI connectivity |
| `debug_researcher.py` | Test Bull/Bear debate |
| `debug_execution.py` | Test order placement |

---

## 📁 Project Structure

```
mt5/
├── main_async.py              # Entry point — connects MT5, starts server + dashboard
├── config/settings.py         # All configuration (loaded from .env)
│
├── strategy/
│   ├── institutional_strategy.py  # Orchestrator — scan loop, trade execution, events
│   ├── pair_agent.py              # Per-symbol agent
│   └── features.py                # Technical feature engineering
│
├── analysis/
│   ├── market_analyst.py      # Macro regime classification (LLM)
│   ├── quant_agent.py         # SMC + ML confluence scoring
│   ├── researcher_agent.py    # Bull vs Bear LLM debate
│   ├── critic_agent.py        # Post-trade review
│   ├── fake_news_detector.py  # 5-signal news credibility scorer
│   ├── sentiment_analyzer.py  # Combined news + technical sentiment
│   └── gemini_news_analyzer.py # Gemini AI news analysis
│
├── utils/
│   ├── risk_manager.py        # Kill switch, payoff mandate, position sizing
│   ├── telegram_notifier.py   # Telegram push alerts
│   ├── trade_journal.py       # SQLite trade log
│   ├── news_filter.py         # High-impact news blackout
│   └── data_cache.py          # In-memory market data cache
│
├── api/
│   └── stream_server.py       # FastAPI WebSocket + REST API (port 8000)
│
├── dashboard/                 # React + Vite frontend
│   ├── src/
│   │   ├── App.jsx
│   │   ├── hooks/useBotWebSocket.js
│   │   └── components/
│   │       ├── AccountCard.jsx
│   │       ├── ScannerGrid.jsx
│   │       ├── PositionsTable.jsx
│   │       ├── TradeFeed.jsx
│   │       └── EventLog.jsx
│   └── package.json
│
├── execution/
│   └── mt5_client.py          # MT5 order placement, symbol detection
│
├── market_data/loader.py      # OHLCV data fetching
├── optimize_walkforward.py    # Rolling in-sample/OOS parameter search
├── train_model.py             # Random Forest trainer
├── train_xgboost.py           # XGBoost trainer
└── train_lstm.py              # LSTM trainer
```

---

## ⚠️ Disclaimer

**Institutional Swarm** is provided for educational and research purposes only. Financial trading involves significant risk of capital loss. Past performance is not indicative of future results. The authors bear no responsibility for your trading decisions.
