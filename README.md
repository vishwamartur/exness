# ⚡ XAUUSD Session Regime Engine v4.0

An **event-driven algorithmic trading engine** for MetaTrader 5, purpose-built to trade **XAUUSD (Gold)** using session-aware regime switching, macro filtering, and institutional liquidity sweep detection.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://python.org)
[![MT5](https://img.shields.io/badge/MetaTrader-5-orange)](https://www.metatrader5.com)
[![React](https://img.shields.io/badge/Dashboard-React%20%2B%20Vite-61dafb)](https://vitejs.dev)
[![Exness](https://img.shields.io/badge/Broker-Exness-yellow)](https://www.exness.com)

---

## 🎯 Trading Strategy

The engine exploits **three proven edges** that work specifically for XAUUSD:

### Edge #1 — Session Regime Switching (Primary)

Gold behaves differently across the trading day. The engine detects which session is active and applies the optimal strategy:

| Session | UTC Hours | Regime | Strategy | Why It Works |
|---------|-----------|--------|----------|--------------|
| **Asian** | 22:00–08:00 | `MEAN_REVERT` | RSI fade at range extremes | Low institutional volume → mean-reverting behavior |
| **London** | 08:00–13:00 | `BREAKOUT` | Asian range breakout | Institutions enter → price breaks overnight range |
| **New York** | 13:00–17:00 | `MOMENTUM` | EMA20/50 trend pullback | Momentum continuation from London move |
| **NY Afternoon** | 17:00–22:00 | `FLAT` | No new trades, flatten all | Declining liquidity → whipsaw risk |

#### Asian Range Backfill
If the bot starts mid-session (e.g. during London), it automatically **backfills the Asian range** from historical M5 data. This ensures the London BREAKOUT strategy always has a valid high/low range to work with.

### Edge #2 — Macro Filter (DXY + VIX)

The engine polls DXY (US Dollar Index) and VIX (Volatility Index) to classify the macro environment and filter out low-edge trades:

| Macro Regime | Condition | Action |
|-------------|-----------|--------|
| **SAFE_HAVEN** | VIX ≥ 25 | Favor longs only, full size |
| **HEADWIND** | DXY spiking + VIX < 20 | Shorts only, 50% size |
| **CHOP** | DXY flat + VIX 15–20 | **Block all trades** — no edge |
| **NEUTRAL** | Otherwise | Both directions, full size |

### Edge #3 — Liquidity Sweep Detection

Detects institutional **stop-hunt** patterns at Previous Day High/Low levels. When price spikes through a key level and reverses, the engine fades the move for a high-conviction reversal trade (score 9).

---

## 📐 Entry Logic — Big Trade Mode

The engine prioritizes **quality over quantity**: fewer trades, bigger winners.

### London BREAKOUT (Score 7–8)
```
Conditions:
  ✓ Price breaks Asian High + 0.3×ATR (or Asian Low − 0.3×ATR)
  ✓ M5 MACD confirms direction
  ✓ H1 trend aligns (EMA20 + MACD + RSI on H1)
  ✓ RSI not overbought/oversold
  ✓ Volume surge boosts score to 8

  SL: 2.0× ATR    TP: 6.0× ATR    R:R ≈ 3:1
```

### NY MOMENTUM Pullback (Score 7–8)
```
Conditions:
  ✓ EMA20 > EMA50 (uptrend structure) or EMA20 < EMA50 (downtrend)
  ✓ Price within 1.5× ATR of EMA20 (pullback zone)
  ✓ M5 MACD confirms direction
  ✓ H1 trend aligns
  ✓ RSI in healthy range (45–70 for longs, 30–55 for shorts)

  SL: 1.5× ATR    TP: 5.0× ATR    R:R ≈ 3.3:1
```

### NY MOMENTUM Continuation (Score 7)
```
Conditions:
  ✓ EMA20 > EMA50 + price above EMA20
  ✓ M5 MACD confirms
  ✓ H1 trend CONFIRMS (not just neutral)
  ✓ RSI 55–75 (longs) or 25–45 (shorts)

  SL: 2.0× ATR    TP: 5.0× ATR    R:R ≈ 2.5:1
```

### Asian MEAN REVERSION (Score 7)
```
Conditions:
  ✓ Price at Asian range extreme (high or low)
  ✓ RSI overbought (>70) or oversold (<30)
  ✓ Below-median volume (no institutional activity)

  SL: 1.5× ATR    TP: mid-range distance or 3× ATR
```

---

## 🛡️ Risk Management

Hard-coded survival rules — non-negotiable:

| Rule | Setting | Purpose |
|------|---------|---------|
| **Max Risk Per Trade** | 1% of equity | Single trade can't hurt you |
| **Max Effective Leverage** | 1:20 (code-enforced) | Ignores broker's 1:1000 |
| **Daily Loss Limit** | 3% equity or $50 USD | Bot shuts down until next day |
| **Kill Switch** | 3 consecutive losses | 50% size reduction for 24h |
| **Min R:R Ratio** | 2.5:1 | Every trade must justify the risk |
| **Min Confluence Score** | 7 | Only high-conviction signals pass |
| **Max Daily Trades** | 5 | Quality over quantity |
| **Max Open Positions** | 2 | Focus capital on best setups |
| **Spread Gate** | 5 pips max on Gold | Avoids wide-spread entries |
| **Cooldown** | 300s between trades | Prevents revenge trading |

### Smart Exit System
- **Trailing Stop**: 2.0× ATR behind price (ratchet — only moves in your favor)
- **Breakeven Move**: SL moves to entry + buffer after 1.0× ATR profit
- **Partial Close**: 30% of position closed at breakeven (lock risk-free profit)
- **Early Loss Cut**: Closes losers early when momentum reverses
- **Safety TP**: Emergency take-profit at 15× ATR (rarely hit — trailing handles it)

---

## 🏗️ Architecture

Event-driven microservices communicating through an async EventBus:

```
main_async.py
│
├── Core
│   ├── EventBus              — Async pub/sub message broker
│   └── MT5Gateway            — Thread-safe MT5 API proxy
│
├── Data Layer
│   └── MarketDataService     — Fetches M5/H1 candles, publishes MARKET_DATA_READY
│
├── Strategy Layer (3 Edges)
│   ├── SessionRegimeService  — Detects session + tracks Asian range + PDH/PDL
│   ├── SessionStrategyService — Generates BUY/SELL signals per regime
│   ├── MacroFilterService    — DXY/VIX polling + regime classification
│   └── LiquiditySweepService — PDH/PDL stop-hunt detection
│
├── Decision Layer
│   ├── CoordinatorService    — Orchestrates scan cycles, emits TRADE_CANDIDATE
│   └── RiskService           — Hardened risk gatekeeper → TRADE_APPROVED
│
├── Execution Layer
│   ├── ExecutionService      — Places market/limit orders on MT5
│   └── TradeManagerService   — Trailing stops, breakeven, partial closes
│
├── Intelligence Layer
│   ├── GemmaBrainService     — AI trade vetting (optional)
│   └── NewsTradingService    — News event breakout/straddle trading
│
└── Output Layer
    ├── BroadcastService      — WebSocket feed to dashboard
    ├── TelegramService       — Push notifications
    └── JournalService        — SQLite trade journal
```

### Event Flow
```
SCAN_START → MARKET_DATA_READY → SESSION_REGIME_UPDATE + MACRO_FILTER_UPDATE
  → SESSION_TRADE_SIGNAL / SWEEP_TRADE_SIGNAL → TRADE_CANDIDATE
  → TRADE_APPROVED → TRADE_EXECUTED → (Journal, Telegram, Dashboard)
```

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

### 2. Install Dashboard
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

# Trading — Big Trade Mode
TIMEFRAME=M5
LOT_SIZE=0.02
RISK_PERCENT=1.0

# Entry Quality — STRICT
MIN_CONFLUENCE_SCORE=7
MIN_RISK_REWARD_RATIO=2.5
BREAKOUT_ATR_MULTIPLIER=0.3

# Trade Limits
COOLDOWN_SECONDS=300
MAX_DAILY_TRADES=5
MAX_OPEN_POSITIONS=2

# H1 Trend Confirmation
H1_TREND_FILTER=True

# Smart Exit — Let Winners Run
SMART_EXIT_ENABLED=True
TRAIL_ACTIVATE_ATR=1.0
TRAILING_ATR_MULTIPLIER=2.0
TP_SAFETY_ATR=15.0

# Risk
MAX_DAILY_LOSS_USD=50

# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 4. Start the Bot
```bash
python main_async.py
```

This will:
1. Connect to MT5 and auto-detect XAUUSD symbol (with broker suffix)
2. Backfill Asian range from historical M5 data if starting mid-session
3. Auto-launch the React dashboard at `http://localhost:5173`
4. Begin session-aware scan cycles every 300s
5. Send Telegram alerts on trades

---

## 📊 React Dashboard

Real-time monitoring via Vite + React, auto-launches on bot startup:

- **Account Stats**: Balance, equity, daily P&L
- **Scanner Status**: Current session, regime, macro filter, Asian range
- **Open Positions**: Live P&L with trailing stop visualization
- **Trade Feed**: Recent entries/exits with scores and reasons
- **Event Log**: Full event bus activity

Connects via WebSocket (`ws://localhost:8000/ws`) + REST polling.

---

## 📁 Project Structure

```
mt5/
├── main_async.py                  # Entry point — MT5 + dashboard + scan loop
├── config/settings.py             # All configuration (loaded from .env)
│
├── core/
│   ├── event_bus.py               # Async pub/sub EventBus
│   ├── base_service.py            # Service lifecycle management
│   └── mt5_gateway.py             # Thread-safe MT5 API proxy
│
├── services/
│   ├── coordinator.py             # Scan orchestrator
│   ├── session_regime_service.py  # Session detection + Asian range tracking
│   ├── session_strategy_service.py # Signal generation per regime
│   ├── macro_filter_service.py    # DXY/VIX macro filter
│   ├── liquidity_sweep_service.py # PDH/PDL stop-hunt detector
│   ├── market_data_service.py     # M5/H1 data fetcher
│   ├── risk_service.py            # Hardened risk gatekeeper
│   ├── execution_service.py       # MT5 order placement
│   ├── trade_manager_service.py   # Trailing/BE/partial close manager
│   ├── gemma_brain_service.py     # AI trade vetting (optional)
│   ├── news_trading_service.py    # News event trading
│   ├── broadcast_service.py       # WebSocket dashboard feed
│   ├── telegram_service.py        # Telegram notifications
│   └── journal_service.py         # SQLite trade journal
│
├── strategy/
│   ├── features.py                # Technical feature engineering
│   ├── institutional_strategy.py  # Legacy orchestrator
│   └── ...                        # ML predictors (XGBoost, LSTM, etc.)
│
├── utils/
│   ├── risk_manager.py            # Position sizing, kill switch, payoff mandate
│   ├── news_filter.py             # Economic calendar blackout
│   └── correlation_filter.py      # Portfolio correlation check
│
├── dashboard/                     # React + Vite frontend
│   └── src/
│       ├── App.jsx
│       └── components/
│
├── execution/
│   └── mt5_client.py              # Low-level MT5 operations
│
└── market_data/
    └── loader.py                  # OHLCV data fetching
```

---

## 📱 Telegram Alerts

Real-time push notifications via `@vcrpttrade_bot`:

| Alert | Trigger |
|-------|---------|
| 🤖 **Bot Started** | Startup with symbol count and session |
| 📡 **Trade Signal** | Candidate found with score and reason |
| 🟢🔴 **Trade Executed** | Symbol, direction, lot, price, SL, TP |
| ⚠️ **Kill Switch** | Consecutive losses — size reduced |
| 🛑 **Daily Limit** | 3% loss hit — shutdown until tomorrow |
| 📊 **Session Change** | Session transition (e.g. London → NY) |

---

## ⚙️ Key Configuration Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `BREAKOUT_ATR_MULTIPLIER` | 0.3 | How far beyond Asian range = confirmed breakout |
| `MIN_CONFLUENCE_SCORE` | 7 | Minimum signal quality to trade (scores 6–9) |
| `MIN_RISK_REWARD_RATIO` | 2.5 | Minimum reward:risk ratio |
| `TRAILING_ATR_MULTIPLIER` | 2.0 | Trailing stop distance behind price |
| `TRAIL_ACTIVATE_ATR` | 1.0 | Start trailing after 1× ATR profit |
| `BREAKEVEN_ACTIVATE_ATR` | 1.0 | Move SL to entry after 1× ATR profit |
| `COOLDOWN_SECONDS` | 300 | Minimum seconds between trades |
| `MAX_DAILY_TRADES` | 5 | Hard cap on daily trade count |
| `MAX_EFFECTIVE_LEVERAGE` | 20 | Code-enforced leverage cap (ignores broker) |
| `H1_TREND_FILTER` | True | Require H1 timeframe trend confirmation |
| `MACRO_FILTER_ENABLED` | True | Enable DXY/VIX regime filtering |
| `SWEEP_ENABLED` | True | Enable liquidity sweep detection |

---

## ⚠️ Disclaimer

This software is provided for **educational and research purposes only**. Financial trading involves significant risk of capital loss. Past performance is not indicative of future results. The authors bear no responsibility for trading decisions made using this system.
