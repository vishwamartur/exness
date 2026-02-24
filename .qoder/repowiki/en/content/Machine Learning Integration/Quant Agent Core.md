# Quant Agent Core

<cite>
**Referenced Files in This Document**
- [quant_agent.py](file://analysis/quant_agent.py)
- [features.py](file://strategy/features.py)
- [lstm_predictor.py](file://strategy/lstm_predictor.py)
- [lstm_model.py](file://strategy/lstm_model.py)
- [hf_predictor.py](file://strategy/hf_predictor.py)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py)
- [settings.py](file://config/settings.py)
- [best_params.json](file://models/best_params.json)
- [README.md](file://README.md)
- [pair_agent.py](file://strategy/pair_agent.py)
- [main.py](file://main.py)
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
10. [Appendices](#appendices)

## Introduction
This document provides comprehensive documentation for the QuantAgent core class, which orchestrates multi-model inference (Random Forest, XGBoost), technical analysis (trend and confluence scoring), and AI forecasting signals (Lag-Llama/Chronos) to produce a unified ensemble score for trading decisions. It explains model loading, feature preparation, prediction scoring, and the confluence calculation system including trend analysis and technical indicator integration. It also covers model availability detection, fallback mechanisms, and performance considerations.

## Project Structure
The QuantAgent resides in the analysis module and integrates with:
- Feature engineering utilities in strategy.features
- LSTM predictor and model in strategy.lstm_predictor and strategy.lstm_model
- Hugging Face forecasting via strategy.hf_predictor or strategy.lag_llama_predictor
- Configuration in config.settings and model metadata in models/best_params.json
- Orchestration in strategy.pair_agent and the main entry point in main.py

```mermaid
graph TB
subgraph "Analysis"
QA["QuantAgent<br/>analysis/quant_agent.py"]
end
subgraph "Strategy"
FEAT["Technical Features<br/>strategy/features.py"]
LSTMP["LSTMPredictor<br/>strategy/lstm_predictor.py"]
LSTMM["BiLSTMWithAttention<br/>strategy/lstm_model.py"]
HFP["HFPredictor<br/>strategy/hf_predictor.py"]
LLP["LagLlamaPredictor<br/>strategy/lag_llama_predictor.py"]
end
subgraph "Config"
SET["Settings<br/>config/settings.py"]
BP["Best Params<br/>models/best_params.json"]
end
subgraph "Orchestration"
PA["PairAgent<br/>strategy/pair_agent.py"]
MAIN["Main Loop<br/>main.py"]
end
QA --> FEAT
QA --> LSTMP
QA --> HFP
QA --> LLP
LSTMP --> LSTMM
QA --> SET
PA --> QA
MAIN --> PA
SET --> BP
```

**Diagram sources**
- [quant_agent.py](file://analysis/quant_agent.py#L34-L294)
- [features.py](file://strategy/features.py#L6-L98)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L9-L142)
- [lstm_model.py](file://strategy/lstm_model.py#L27-L70)
- [hf_predictor.py](file://strategy/hf_predictor.py#L15-L53)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py#L31-L235)
- [settings.py](file://config/settings.py#L173-L196)
- [best_params.json](file://models/best_params.json#L1-L6)
- [pair_agent.py](file://strategy/pair_agent.py#L71-L295)
- [main.py](file://main.py#L19-L122)

**Section sources**
- [README.md](file://README.md#L187-L235)
- [quant_agent.py](file://analysis/quant_agent.py#L34-L294)
- [settings.py](file://config/settings.py#L173-L196)

## Core Components
- QuantAgent: Central orchestrator for ML inference, technical analysis, AI forecasting, and confluence scoring.
- Feature Engineering: TA indicators and market structure features (order blocks, fair value gaps, liquidity).
- LSTM Predictor: PyTorch-based multi-symbol predictors with scaling and attention.
- Hugging Face Forecasters: Chronos (HFPredictor) and Lag-Llama (LagLlamaPredictor) for probabilistic forecasts.
- Configuration: Model paths, feature lists, and runtime flags for enabling components.

Key responsibilities:
- Multi-model inference (RF/XGBoost) with feature preparation and probability extraction.
- Trend computation across multiple timeframes.
- AI signal aggregation from LSTM and Hugging Face models.
- Confluence scoring (0–6) integrating ML, AI, trend, and structure filters.
- Ensemble scoring combining ML probability, AI signal, and confluence score.

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L34-L294)
- [features.py](file://strategy/features.py#L6-L98)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L9-L142)
- [hf_predictor.py](file://strategy/hf_predictor.py#L15-L53)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py#L31-L235)
- [settings.py](file://config/settings.py#L173-L196)

## Architecture Overview
The QuantAgent integrates:
- Model loading from disk with graceful fallbacks.
- Feature engineering pipeline for technical indicators and structure.
- Multi-model inference (RF/XGBoost) and optional AI forecasting.
- Trend analysis across M5/H1/H4.
- Confluence scoring and ensemble weighting.

```mermaid
sequenceDiagram
participant PA as "PairAgent"
participant QA as "QuantAgent"
participant FEAT as "Features"
participant RF as "Random Forest"
participant XGB as "XGBoost"
participant LSTMP as "LSTMPredictor"
participant HF as "HFPredictor/LagLlama"
PA->>QA : analyze(symbol, data_dict)
QA->>FEAT : add_technical_features(df)
QA->>QA : _compute_trend(H1,M5,H4)
QA->>RF : _get_rf_prediction(df)
QA->>XGB : _get_xgb_prediction(df)
QA->>LSTMP : _get_ai_signal(symbol, df)
QA->>HF : _get_ai_signal(symbol, df)
QA->>QA : _calculate_confluence(...)
QA->>QA : _ensemble_vote(...)
QA-->>PA : {direction, score, details, ensemble_score}
```

**Diagram sources**
- [quant_agent.py](file://analysis/quant_agent.py#L109-L159)
- [features.py](file://strategy/features.py#L6-L98)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L115-L142)
- [hf_predictor.py](file://strategy/hf_predictor.py#L34-L53)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py#L181-L229)

## Detailed Component Analysis

### QuantAgent Class
Responsibilities:
- Load ML models (joblib) and feature columns.
- Load optional LSTM predictors per symbol and default.
- Load optional Hugging Face forecasting models (Chronos or Lag-Llama).
- Compute trends across multiple timeframes.
- Perform ML inference (RF/XGBoost) and prepare features.
- Aggregate AI signals from LSTM and Hugging Face models.
- Calculate confluence score (0–6) and ensemble score.
- Return structured analysis results.

Key methods:
- __init__: Initializes fields and loads models.
- _load_models: Loads RF/XGBoost, LSTM, and AI predictors with guarded imports.
- _load_lstm_models: Loads per-symbol and default LSTM predictors.
- analyze: Orchestrates the full analysis pipeline.
- _compute_trend: Computes trend using SMA comparison.
- _get_rf_prediction/_get_xgb_prediction: Extracts probabilities and predictions.
- _prepare_X: Prepares feature matrix from feature columns or drops non-feature columns.
- _get_ai_signal: Aggregates AI signals from Hugging Face and LSTM.
- _calculate_confluence: Scores trend, ML, AI, structure, and ADX filters.
- _ensemble_vote: Combines ML probability, AI signal, and confluence into ensemble score.

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
- [quant_agent.py](file://analysis/quant_agent.py#L34-L294)

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L42-L108)
- [quant_agent.py](file://analysis/quant_agent.py#L109-L159)
- [quant_agent.py](file://analysis/quant_agent.py#L163-L294)

### Feature Preparation Pipeline
The feature engineering pipeline adds:
- Price returns and basic returns.
- Momentum indicators (RSI, Stochastic RSI).
- Volatility indicators (Bollinger Bands, ATR).
- Trend indicators (SMA/EMA, MACD, ADX).
- Volume indicators (VWAP, delta volume).
- Market structure (swings, BOS), order blocks, fair value gaps, liquidity.
- Lag features for returns, RSI, MACD difference.
- Drops NaN rows.

```mermaid
flowchart TD
Start(["DataFrame"]) --> Price["Compute log returns"]
Price --> Momentum["RSI + StochRSI"]
Momentum --> Vol["BB + ATR"]
Vol --> Trend["SMA/EMA + MACD + ADX"]
Trend --> Volume["VWAP + Delta Volume"]
Volume --> Structure["Swings + BOS"]
Structure --> OB["Order Blocks"]
OB --> FVG["Fair Value Gaps"]
FVG --> Liquidity["Liquidity Levels"]
Liquidity --> Lags["Lag Features"]
Lags --> Clean["Drop NaN"]
Clean --> Out(["Enhanced DataFrame"])
```

**Diagram sources**
- [features.py](file://strategy/features.py#L6-L98)
- [features.py](file://strategy/features.py#L101-L224)

**Section sources**
- [features.py](file://strategy/features.py#L6-L98)
- [features.py](file://strategy/features.py#L101-L224)

### LSTM Predictor and Model
The LSTM predictor:
- Loads feature scaler, target scaler, and feature column list.
- Builds a BiLSTM with attention model and evaluates it.
- Preprocesses sequences by scaling and slicing the last sequence_length rows.
- Predicts next value and inverse transforms if a target scaler exists.

```mermaid
classDiagram
class LSTMPredictor {
-model_path
-feature_scaler_path
-target_scaler_path
-cols_path
-model
-feature_scaler
-target_scaler
-feature_cols
+load_artifacts()
+preprocess(df)
+predict(df)
}
class BiLSTMWithAttention {
+lstm
+attention
+fc
+forward(x)
}
LSTMPredictor --> BiLSTMWithAttention : "loads and uses"
```

**Diagram sources**
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L9-L142)
- [lstm_model.py](file://strategy/lstm_model.py#L27-L70)

**Section sources**
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L37-L142)
- [lstm_model.py](file://strategy/lstm_model.py#L27-L70)

### Hugging Face Forecasters
Two forecasting options are supported:
- HFPredictor: Chronos pipeline with quantile median forecast.
- LagLlamaPredictor: Downloads and reconstructs Lag-Llama weights, creates predictor, and returns median forecast.

```mermaid
classDiagram
class HFPredictor {
-pipeline
+predict(context_tensor, prediction_length)
}
class LagLlamaPredictor {
-predictor
+predict(context_tensor, prediction_length)
}
```

**Diagram sources**
- [hf_predictor.py](file://strategy/hf_predictor.py#L15-L53)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py#L31-L235)

**Section sources**
- [hf_predictor.py](file://strategy/hf_predictor.py#L15-L53)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py#L31-L235)

### Confluence Calculation System
The confluence scoring aggregates:
- Trend filters (M5/H1/H4) aligned with direction.
- ML probability thresholds (RF probability) with directional checks.
- AI signal alignment (averaged from Hugging Face and LSTM).
- Structure filters (near order blocks/FVG and liquidity sweeps).
- ADX strength filter.

```mermaid
flowchart TD
Start(["Inputs: direction, h1,h4,m5, df"]) --> Trends["Apply trend filters"]
Trends --> ML["Compute ML probability score"]
ML --> AI["Compute AI signal"]
AI --> Structure["Structure filters (OB/FVG/Liquidity)"]
Structure --> ADX["ADX strength filter"]
ADX --> Sum["Sum components to score 0–6"]
Sum --> Out(["Final score and details"])
```

**Diagram sources**
- [quant_agent.py](file://analysis/quant_agent.py#L231-L293)

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L231-L293)

### Ensemble Voting Methodology
The ensemble combines:
- ML probability (RF/XGBoost) normalized to 0–1.
- AI signal normalized to 0–1 scale.
- Confluence score normalized to 0–1.

Weights: 0.3 (ML), 0.25 (AI), 0.45 (Confluence).

```mermaid
flowchart TD
Start(["ML prob, AI signal, Confluence score"]) --> Normalize["Normalize to 0–1"]
Normalize --> Weight["Apply weights (0.3, 0.25, 0.45)"]
Weight --> Sum["Sum and round to 3 decimals"]
Sum --> Out(["Ensemble score"])
```

**Diagram sources**
- [quant_agent.py](file://analysis/quant_agent.py#L228-L229)

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L228-L229)

## Dependency Analysis
- QuantAgent depends on:
  - settings for model paths, feature lists, and runtime flags.
  - features for technical indicators.
  - LSTM predictor for symbol-specific and default forecasts.
  - Hugging Face predictors for probabilistic forecasts.
- PairAgent orchestrates QuantAgent usage and applies additional risk and regime filters.

```mermaid
graph LR
SET["settings.py"] --> QA["QuantAgent"]
FEAT["features.py"] --> QA
LSTMP["lstm_predictor.py"] --> QA
HFP["hf_predictor.py"] --> QA
LLP["lag_llama_predictor.py"] --> QA
QA --> PA["pair_agent.py"]
PA --> MAIN["main.py"]
```

**Diagram sources**
- [settings.py](file://config/settings.py#L173-L196)
- [quant_agent.py](file://analysis/quant_agent.py#L52-L84)
- [pair_agent.py](file://strategy/pair_agent.py#L145-L161)
- [main.py](file://main.py#L57-L63)

**Section sources**
- [settings.py](file://config/settings.py#L173-L196)
- [quant_agent.py](file://analysis/quant_agent.py#L52-L84)
- [pair_agent.py](file://strategy/pair_agent.py#L145-L161)
- [main.py](file://main.py#L57-L63)

## Performance Considerations
- Model loading optimization:
  - XGBoost single-row inference optimization sets device to CPU to avoid warnings.
  - LSTM devices selected automatically (GPU if available).
- Feature preparation:
  - Uses pre-saved feature columns when available to minimize drift.
  - Drops non-feature columns to reduce overhead.
- Inference:
  - RF/XGBoost predict_proba calls operate on a single-row slice.
  - LSTM and Hugging Face predictions are guarded with try/except to prevent failures from blocking.
- Memory and I/O:
  - Feature scalers and model artifacts are loaded once per predictor.
  - Per-symbol LSTM predictors are cached in a dictionary keyed by base symbol.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and remedies:
- Model load errors:
  - RF/XGBoost model path not found or unreadable.
  - Feature columns file missing; ensure _features.pkl exists alongside the model.
  - XGBoost device parameter setting may fail on some environments; code attempts to set device and continues on failure.
- LSTM predictor errors:
  - Missing scaler or column files cause warnings; ensure scaler and column files exist for the symbol.
  - Not enough data for sequence length raises an error; ensure sufficient historical bars.
  - Target scaler not found; inverse transform falls back to raw prediction.
- Hugging Face predictor errors:
  - Chronos not installed leads to ImportError; install via documented command.
  - Lag-Llama checkpoint download or weight reconstruction may fail; verify network access and repository path.
- Confluence scoring:
  - If insufficient data or missing indicators, the analysis returns None; ensure adequate bars and indicator availability.
- Ensemble scoring:
  - If no AI signals are available, AI component defaults to neutral; ensemble remains valid.

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L68-L84)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L37-L78)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L108-L113)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L139-L141)
- [hf_predictor.py](file://strategy/hf_predictor.py#L17-L18)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py#L46-L79)

## Conclusion
The QuantAgent provides a robust, modular framework for multi-model inference and confluence scoring. It integrates classical ML (RF/XGBoost), deep learning (LSTM), and modern forecasting (Chronos/Lag-Llama) with technical analysis and trend filters. Its design emphasizes resilience through guarded imports and fallbacks, efficient feature preparation, and a clear scoring methodology that balances ML confidence, AI signals, and structural/market context.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Model Loading and Availability Detection
- RF/XGBoost:
  - Load model and feature columns from configured paths.
  - Optionally set XGBoost device to CPU for single-row inference.
- LSTM:
  - Attempt to load per-symbol predictors for EURUSD, XAUUSD, BTCUSD, GBPUSD.
  - Fall back to a default model if per-symbol models are unavailable.
- Hugging Face:
  - Try Lag-Llama predictor factory; otherwise fall back to Chronos HFPredictor if available.

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L52-L84)
- [settings.py](file://config/settings.py#L173-L196)

### Prediction Execution and Scoring Examples
- Model loading:
  - See QuantAgent initialization and model loading routines.
- Prediction execution:
  - RF/XGBoost: call _get_rf_prediction or _get_xgb_prediction with a single-row DataFrame slice prepared by _prepare_X.
  - LSTM: call _get_ai_signal to compute symbol-specific or default LSTM prediction.
  - Hugging Face: call _get_ai_signal to compute median forecast and derive signal.
- Scoring:
  - Confluence score computed via _calculate_confluence.
  - Ensemble score computed via _ensemble_vote.

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L171-L186)
- [quant_agent.py](file://analysis/quant_agent.py#L202-L227)
- [quant_agent.py](file://analysis/quant_agent.py#L231-L293)
- [quant_agent.py](file://analysis/quant_agent.py#L228-L229)

### Best Practices and Configuration
- Use best_params.json to align thresholds with walk-forward optimization.
- Ensure feature columns match the model’s expectations.
- Monitor trend filters and session gating to avoid counter-trend entries.
- Validate spread and volatility thresholds before entering trades.

**Section sources**
- [best_params.json](file://models/best_params.json#L1-L6)
- [settings.py](file://config/settings.py#L77-L108)
- [pair_agent.py](file://strategy/pair_agent.py#L171-L234)