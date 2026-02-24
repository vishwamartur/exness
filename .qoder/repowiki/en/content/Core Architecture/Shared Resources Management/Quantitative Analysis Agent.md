# Quantitative Analysis Agent

<cite>
**Referenced Files in This Document**
- [quant_agent.py](file://analysis/quant_agent.py)
- [features.py](file://strategy/features.py)
- [lstm_predictor.py](file://strategy/lstm_predictor.py)
- [lstm_model.py](file://strategy/lstm_model.py)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py)
- [hf_predictor.py](file://strategy/hf_predictor.py)
- [settings.py](file://config/settings.py)
- [pair_agent.py](file://strategy/pair_agent.py)
- [institutional_strategy.py](file://strategy/institutional_strategy.py)
- [xgboost_v1_symbols.txt](file://models/xgboost_v1_symbols.txt)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)

## Introduction
The QuantAgent subsystem serves as the central quantitative analysis engine responsible for generating predictions and confluence-based trading signals for all pair agents. It orchestrates a multi-model ensemble combining classical machine learning (Random Forest and XGBoost), deep learning (LSTM neural networks), and modern foundation models (transformer-based forecasting via Lag-Llama/Chronos). The agent performs technical analysis, computes trend signals across multiple timeframes, and synthesizes an ensemble score that guides trade decisions.

Key responsibilities:
- Load and maintain trained models (RF/XGBoost, LSTM, Lag-Llama/Chronos)
- Engineer rich technical features from OHLCV data
- Compute trend signals across M5/H1/H4
- Generate AI signals from high-frequency forecasting models
- Score confluence combining trend, technical indicators, and ML predictions
- Produce an ensemble signal suitable for downstream pair agents

## Project Structure
The QuantAgent integrates with several modules:
- Analysis: QuantAgent orchestrates inference and scoring
- Strategy: Feature engineering, LSTM predictor/model, Lag-Llama/Chronos predictors
- Config: Centralized settings controlling model availability and thresholds
- Models: Saved artifacts for RF/XGBoost and LSTM

```mermaid
graph TB
subgraph "Analysis"
QA["QuantAgent"]
end
subgraph "Strategy"
FE["features.py<br/>Technical Indicators"]
LP["LSTMPredictor"]
LM["BiLSTMWithAttention"]
LL["LagLlamaPredictor"]
HF["HFPredictor (Chronos)"]
end
subgraph "Config"
ST["settings.py"]
end
subgraph "Models"
RF["scalper_v1.pkl"]
XGB["xgboost_v1.pkl"]
LSTMS["lstm_* artifacts"]
end
QA --> FE
QA --> LP
QA --> LL
QA --> HF
LP --> LM
LP --> LSTMS
QA --> RF
QA --> XGB
QA --> ST
```

**Diagram sources**
- [quant_agent.py](file://analysis/quant_agent.py#L34-L159)
- [features.py](file://strategy/features.py#L6-L98)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L9-L36)
- [lstm_model.py](file://strategy/lstm_model.py#L27-L69)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py#L31-L44)
- [hf_predictor.py](file://strategy/hf_predictor.py#L15-L32)
- [settings.py](file://config/settings.py#L173-L200)

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L34-L159)
- [settings.py](file://config/settings.py#L173-L200)

## Core Components
- QuantAgent: Central orchestrator for model loading, feature engineering, trend computation, AI signal generation, and confluence scoring.
- Feature Engine: Comprehensive TA pipeline adding momentum, volatility, trend, volume, order blocks, fair value gaps, liquidity sweeps, and structure scores.
- LSTM Predictor: Loads pre-trained BiLSTM with attention and scales/sequences input for inference.
- Transformer-based Predictors: Lag-Llama and Chronos pipelines for long-horizon forecasting.
- Settings: Controls model availability, thresholds, and multi-timeframe filters.

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L34-L159)
- [features.py](file://strategy/features.py#L6-L98)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L9-L36)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py#L31-L44)
- [hf_predictor.py](file://strategy/hf_predictor.py#L15-L32)
- [settings.py](file://config/settings.py#L173-L200)

## Architecture Overview
The QuantAgent receives multi-timeframe data dictionaries, engineers features, computes trends, queries ML models, generates AI signals, and returns a structured analysis including direction, score, and details.

```mermaid
sequenceDiagram
participant PA as "PairAgent"
participant QA as "QuantAgent"
participant FE as "features.add_technical_features"
participant RF as "joblib model"
participant XGB as "joblib xgb_model"
participant LP as "LSTMPredictor"
participant HF as "HFPredictor/LagLlama"
participant ST as "settings"
PA->>QA : analyze(symbol, data_dict)
QA->>FE : add_technical_features(df)
QA->>QA : _compute_trend(H1,H4,M5)
QA->>RF : predict_proba(X)
RF-->>QA : rf_prob
QA->>XGB : predict_proba(X)
XGB-->>QA : xgb_prob
QA->>LP : predict(df)
LP-->>QA : lstm_pred
QA->>HF : predict(close_series)
HF-->>QA : forecast
QA->>QA : _get_ai_signal(symbol, df)
QA->>QA : _calculate_confluence(...)
QA->>QA : _ensemble_vote(...)
QA-->>PA : {direction, score, details, ensemble_score, ...}
```

**Diagram sources**
- [quant_agent.py](file://analysis/quant_agent.py#L109-L159)
- [features.py](file://strategy/features.py#L6-L98)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L115-L142)
- [hf_predictor.py](file://strategy/hf_predictor.py#L34-L52)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py#L181-L228)
- [settings.py](file://config/settings.py#L150-L154)

## Detailed Component Analysis

### QuantAgent: Multi-Model Ensemble and Confluence Scoring
Responsibilities:
- Model loading for RF/XGBoost, LSTM, and transformer-based forecasting
- Feature engineering via technical indicators
- Trend computation across M5/H1/H4
- AI signal generation from high-frequency models
- Confluence scoring combining trend, technical signals, and ML probability
- Ensemble aggregation of ML probability, AI signal, and confluence score

Key methods and flows:
- Initialization loads models based on settings and availability
- Analysis orchestrates feature engineering, trend computation, ML predictions, AI signals, and scoring
- Confluence scoring applies configurable trend filters and evaluates ML probability and AI alignment
- Ensemble vote blends ML, AI, and confluence into a normalized score

```mermaid
classDiagram
class QuantAgent {
-model
-xgb_model
-feature_cols
-hf_predictor
-lstm_predictors
+__init__()
-_load_models()
-_load_lstm_models()
+analyze(symbol, data_dict)
-_compute_trend(df, sma_period)
-_get_rf_prediction(df)
-_get_xgb_prediction(df)
-_prepare_X(row)
-_get_ai_signal(symbol, df)
-_calculate_confluence(symbol, df, direction, h1, h4, m5)
-_ensemble_vote(rf, ai, conf)
}
```

**Diagram sources**
- [quant_agent.py](file://analysis/quant_agent.py#L34-L159)

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L42-L84)
- [quant_agent.py](file://analysis/quant_agent.py#L109-L159)
- [quant_agent.py](file://analysis/quant_agent.py#L231-L293)

### Feature Engineering Pipeline
The feature engine adds:
- Price returns and derived series
- Momentum: RSI, Stochastic RSI
- Volatility: Bollinger Bands, ATR
- Trend: SMAs, EMAs, MACD, ADX (+DI/-DI)
- Volume: VWAP, delta volume, volume ratios
- Market structure: swing highs/lows, BOS
- Order blocks and fair value gaps
- Liquidity sweeps
- Lagged features for temporal dependencies

```mermaid
flowchart TD
Start(["DataFrame"]) --> Price["Compute log returns"]
Price --> Mom["RSI + StochRSI"]
Mom --> Vol["BBands + ATR"]
Vol --> Trend["SMAs/EMAs + MACD + ADX"]
Trend --> Vol2["VWAP + delta volume"]
Vol2 --> Structure["Swing highs/lows + BOS"]
Structure --> OB["Order Blocks"]
OB --> FVG["Fair Value Gaps"]
FVG --> Liquidity["Liquidity Levels + Sweeps"]
Liquidity --> Lags["Lagged features"]
Lags --> Clean["Drop NaN"]
Clean --> Out(["Enhanced DataFrame"])
```

**Diagram sources**
- [features.py](file://strategy/features.py#L6-L98)
- [features.py](file://strategy/features.py#L101-L128)
- [features.py](file://strategy/features.py#L131-L170)
- [features.py](file://strategy/features.py#L173-L203)
- [features.py](file://strategy/features.py#L206-L224)

**Section sources**
- [features.py](file://strategy/features.py#L6-L98)

### LSTM Neural Network Predictor
The LSTM predictor loads a trained BiLSTM with attention and associated scalers and feature columns. It sequences recent data and predicts the next value, optionally inverse-transforming the target scale.

```mermaid
classDiagram
class LSTMPredictor {
-device
-sequence_length
-hidden_size
-num_layers
-model_path
-feature_scaler_path
-target_scaler_path
-cols_path
-model
-feature_scaler
-target_scaler
-feature_cols
+__init__(model_path, scaler_path, device, ...)
+load_artifacts()
+preprocess(df)
+predict(df)
}
class BiLSTMWithAttention {
+forward(x)
}
LSTMPredictor --> BiLSTMWithAttention : "uses"
```

**Diagram sources**
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L9-L36)
- [lstm_model.py](file://strategy/lstm_model.py#L27-L69)

**Section sources**
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L37-L78)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L79-L142)
- [lstm_model.py](file://strategy/lstm_model.py#L27-L69)

### Transformer-Based Forecasting (Lag-Llama/Chronos)
Two pathways are supported:
- Lag-Llama: Loads a foundation model checkpoint, reconstructs estimator/module, and creates a predictor for median forecasts
- Chronos (Hugging Face): Uses the ChronosPipeline for fast inference and median quantile forecasts

Both feed into the AI signal calculation by comparing a short-horizon forecast to the current price.

```mermaid
sequenceDiagram
participant QA as "QuantAgent"
participant HF as "HFPredictor"
participant LL as "LagLlamaPredictor"
participant DF as "DataFrame"
QA->>DF : Extract recent close series
alt HF Available
QA->>HF : predict(close_series, pred_len)
HF-->>QA : forecast_median
else Lag-Llama Available
QA->>LL : predict(close_series, pred_len)
LL-->>QA : forecast_median
end
QA->>QA : Compare forecast vs current price
QA-->>QA : AI signal (+1/-1/0)
```

**Diagram sources**
- [quant_agent.py](file://analysis/quant_agent.py#L202-L226)
- [hf_predictor.py](file://strategy/hf_predictor.py#L34-L52)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py#L181-L228)

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L202-L226)
- [hf_predictor.py](file://strategy/hf_predictor.py#L15-L52)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py#L31-L180)

### Confluence Scoring System
The confluence score aggregates:
- Trend filters: optional M5/H1/H4 alignment gating
- Machine Learning: RF/XGBoost probability thresholds
- AI signal: alignment with transformer forecasts
- Structure signals: near-order-block/fair-value-gap and liquidity sweep confirmations
- ADX strength filter

```mermaid
flowchart TD
Start(["Inputs: df, direction, h1,h4,m5"]) --> Trends["Apply trend filters (optional)"]
Trends --> ML["Compute ML probability (RF/XGBoost)"]
ML --> AISig["Compute AI signal (HF/Lag-Llama + LSTM)"]
AISig --> Structure["Check SMC + Liquidity conditions"]
Structure --> ADX["Check ADX strength"]
ADX --> Combine["Combine into score with details"]
Combine --> End(["Return score and details"])
```

**Diagram sources**
- [quant_agent.py](file://analysis/quant_agent.py#L231-L293)

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L231-L293)

### Ensemble Voting Mechanism
The ensemble combines three components:
- ML probability (RF/XGBoost)
- AI signal (normalized to 0..1 scale)
- Confluence score (0..6 scale)

Weights are applied and normalized to produce a final ensemble score.

```mermaid
flowchart TD
A["ML Probability"] --> W1["Weight 0.3"]
B["AI Signal (0..1)"] --> W2["Weight 0.25"]
C["Confluence (0..6)"] --> W3["Weight 0.45"]
W1 --> Sum["Sum"]
W2 --> Sum
W3 --> Sum
Sum --> Norm["Normalize to 0..1"]
Norm --> Out["Ensemble Score"]
```

**Diagram sources**
- [quant_agent.py](file://analysis/quant_agent.py#L228-L229)

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L228-L229)

### Model Loading Mechanism
- RF/XGBoost: Loaded via joblib from configured paths; XGBoost is optimized for CPU inference
- LSTM: Attempts to load per-symbol predictors for EURUSD, XAUUSD, BTCUSD, GBPUSD, with a fallback default model
- Transformers: Attempts Lag-Llama predictor factory or Chronos pipeline depending on availability

```mermaid
flowchart TD
Init["QuantAgent.__init__"] --> LoadRF["Load RF model"]
LoadRF --> LoadXGB["Load XGBoost if enabled"]
LoadXGB --> LoadLSTM["Load per-symbol LSTM predictors"]
LoadLSTM --> LoadHF["Load Lag-Llama or Chronos"]
LoadHF --> Ready["Models Ready"]
```

**Diagram sources**
- [quant_agent.py](file://analysis/quant_agent.py#L52-L84)
- [settings.py](file://config/settings.py#L173-L200)

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L52-L84)
- [settings.py](file://config/settings.py#L173-L200)

### Signal Generation Workflow
- Feature engineering on the primary timeframe
- Trend computation across M5/H1/H4
- ML predictions (RF/XGBoost)
- AI signal from transformer/LSTM forecasts
- Confluence scoring and gating
- Ensemble aggregation
- Direction and score returned to pair agents

```mermaid
sequenceDiagram
participant Data as "Market Data"
participant QA as "QuantAgent"
participant PA as "PairAgent"
Data-->>QA : data_dict (M1, H1, H4, M5)
QA->>QA : analyze(symbol, data_dict)
QA-->>PA : {direction, score, details, ensemble_score, features, data}
```

**Diagram sources**
- [quant_agent.py](file://analysis/quant_agent.py#L109-L159)
- [institutional_strategy.py](file://strategy/institutional_strategy.py#L252-L279)

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L109-L159)
- [institutional_strategy.py](file://strategy/institutional_strategy.py#L252-L279)

## Dependency Analysis
- QuantAgent depends on:
  - settings for model paths and gating flags
  - features for TA engineering
  - LSTM predictor/model for sequence-based forecasting
  - HuggingFace/Chronos or Lag-Llama for transformer-based forecasting
  - Joblib for model persistence

```mermaid
graph LR
QA["QuantAgent"] --> ST["settings.py"]
QA --> FE["features.py"]
QA --> LP["LSTMPredictor"]
LP --> LM["BiLSTMWithAttention"]
QA --> HF["HFPredictor"]
QA --> LL["LagLlamaPredictor"]
QA --> RF["scalper_v1.pkl"]
QA --> XGB["xgboost_v1.pkl"]
```

**Diagram sources**
- [quant_agent.py](file://analysis/quant_agent.py#L34-L159)
- [settings.py](file://config/settings.py#L173-L200)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L9-L36)
- [lstm_model.py](file://strategy/lstm_model.py#L27-L69)
- [hf_predictor.py](file://strategy/hf_predictor.py#L15-L32)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py#L31-L44)

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L34-L159)
- [settings.py](file://config/settings.py#L173-L200)

## Performance Considerations
- Device selection: LSTM and transformers automatically use GPU if available; RF/XGBoost is forced to CPU to avoid warnings
- Efficient preprocessing: LSTM requires sufficient historical bars; ensure sequence length is met
- Feature stability: Keep feature columns aligned between training and inference
- Concurrency: Pair agents coordinate calls to QuantAgent; avoid redundant computations by caching recent features when appropriate

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- Missing models or artifacts:
  - Verify model paths in settings and that files exist
  - Ensure feature columns and scalers match the model’s expectations
- LSTM preprocessing failures:
  - Confirm sufficient history length relative to sequence length
  - Validate presence of required feature columns
- Transformer model loading:
  - Lag-Llama requires a patched checkpoint loading path; ensure vendor path exists
  - Chronos requires explicit installation; ensure library is available
- Confluence gating:
  - Adjust trend filters and thresholds in settings to reduce false blocks
- Ensemble sensitivity:
  - Tune weights in ensemble vote to balance ML, AI, and confluence contributions

**Section sources**
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L79-L113)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py#L46-L179)
- [hf_predictor.py](file://strategy/hf_predictor.py#L15-L32)
- [settings.py](file://config/settings.py#L150-L154)
- [quant_agent.py](file://analysis/quant_agent.py#L228-L229)

## Conclusion
The QuantAgent subsystem consolidates multiple modeling modalities—classical ML, deep learning, and transformer-based forecasting—into a unified confluence-driven decision framework. By combining robust technical indicators, trend filters, and ensemble weighting, it provides reliable, interpretable signals for pair agents. Proper configuration of model paths, feature engineering, and gating parameters ensures consistent performance across diverse instruments and market regimes.