# Model Management and Storage

<cite>
**Referenced Files in This Document**
- [settings.py](file://config/settings.py)
- [quant_agent.py](file://analysis/quant_agent.py)
- [lstm_predictor.py](file://strategy/lstm_predictor.py)
- [lstm_model.py](file://strategy/lstm_model.py)
- [features.py](file://strategy/features.py)
- [train_model.py](file://train_model.py)
- [train_xgboost.py](file://train_xgboost.py)
- [train_lstm.py](file://train_lstm.py)
- [auto_trainer.py](file://utils/auto_trainer.py)
- [best_params.json](file://models/best_params.json)
- [hf_predictor.py](file://strategy/hf_predictor.py)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py)
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
This document describes the model management and storage system used by the automated trading framework. It covers:
- Trained model storage and directory layout under models/
- Loading mechanisms in QuantAgent, including path resolution, validation, and error handling
- Training pipeline components for Random Forest, XGBoost, and LSTM
- Deployment procedures, automatic retraining triggers, and performance monitoring
- Compatibility, versioning, and migration strategies

## Project Structure
Models are stored under the models/ directory with the following naming conventions:
- Random Forest (scalper) models:
  - scalper_v1.pkl and scalper_m1_v1.pkl (with feature metadata)
  - scalper_v1_features.pkl and scalper_m1_v1_features.pkl
- XGBoost models:
  - xgboost_v1.pkl and xgboost_v1_features.pkl
- LSTM models:
  - lstm_<SYMBOL>.pth (weights)
  - lstm_<SYMBOL>_scaler.pkl (feature scaler)
  - lstm_<SYMBOL>_target_scaler.pkl (target scaler)
  - lstm_<SYMBOL>_cols.pkl (feature column list)
- Best parameters:
  - models/best_params.json

Key configuration constants define model paths and flags controlling model usage.

```mermaid
graph TB
subgraph "models/"
RF["scalper_v1.pkl<br/>scalper_v1_features.pkl"]
RFM1["scalper_m1_v1.pkl<br/>scalper_m1_v1_features.pkl"]
XGB["xgboost_v1.pkl<br/>xgboost_v1_features.pkl"]
LSTM_EURUSD["lstm_EURUSD.pth<br/>lstm_EURUSD_scaler.pkl<br/>lstm_EURUSD_target_scaler.pkl<br/>lstm_EURUSD_cols.pkl"]
LSTM_XAUUSD["lstm_XAUUSD.pth<br/>lstm_XAUUSD_scaler.pkl<br/>lstm_XAUUSD_target_scaler.pkl<br/>lstm_XAUUSD_cols.pkl"]
LSTM_BTCUSD["lstm_BTCUSD.pth<br/>lstm_BTCUSD_scaler.pkl<br/>lstm_BTCUSD_target_scaler.pkl<br/>lstm_BTCUSD_cols.pkl"]
LSTM_GBPUSD["lstm_GBPUSD.pth<br/>lstm_GBPUSD_scaler.pkl<br/>lstm_GBPUSD_target_scaler.pkl<br/>lstm_GBPUSD_cols.pkl"]
BP["best_params.json"]
end
```

**Diagram sources**
- [settings.py](file://config/settings.py#L173-L196)
- [best_params.json](file://models/best_params.json#L1-L6)

**Section sources**
- [settings.py](file://config/settings.py#L173-L196)
- [best_params.json](file://models/best_params.json#L1-L6)

## Core Components
- Configuration-driven model paths and flags:
  - Random Forest model path and feature metadata path
  - XGBoost model path and flag to enable/disable
  - LSTM model path and scaler path for a single symbol, plus flags and sequence length
- QuantAgent loads and orchestrates models:
  - Loads Random Forest and optional XGBoost
  - Loads LSTM predictors for key symbols and a default predictor
  - Integrates high-frequency forecasting (Chronos/Lag-Llama) when available
- AutoTrainer performs background retraining and hot-swapping of models

**Section sources**
- [settings.py](file://config/settings.py#L173-L196)
- [quant_agent.py](file://analysis/quant_agent.py#L52-L107)
- [auto_trainer.py](file://utils/auto_trainer.py#L80-L136)

## Architecture Overview
The model lifecycle spans training, storage, loading, inference, and continuous adaptation.

```mermaid
graph TB
subgraph "Training"
TM["train_model.py"]
TX["train_xgboost.py"]
TL["train_lstm.py"]
end
subgraph "Storage"
MD["models/ directory"]
end
subgraph "Runtime"
QA["QuantAgent"]
LP["LSTMPredictor"]
AT["AutoTrainer"]
end
subgraph "Config"
ST["settings.py"]
end
TM --> MD
TX --> MD
TL --> MD
ST --> QA
ST --> AT
QA --> LP
AT --> MD
AT --> QA
```

**Diagram sources**
- [train_model.py](file://train_model.py#L223-L230)
- [train_xgboost.py](file://train_xgboost.py#L200-L209)
- [train_lstm.py](file://train_lstm.py#L173-L186)
- [settings.py](file://config/settings.py#L173-L196)
- [quant_agent.py](file://analysis/quant_agent.py#L52-L107)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L37-L78)
- [auto_trainer.py](file://utils/auto_trainer.py#L196-L275)

## Detailed Component Analysis

### Model Directory Structure and Formats
- Random Forest (joblib .pkl) with feature metadata (.pkl)
- XGBoost (joblib .pkl) with feature metadata (.pkl)
- LSTM (PyTorch state_dict .pth) with feature and target scalers (.pkl) and feature columns (.pkl)
- Best parameters JSON for operational tuning

```mermaid
graph TB
RF["Random Forest<br/>scalper_v1.pkl<br/>scalper_v1_features.pkl"]
XGB["XGBoost<br/>xgboost_v1.pkl<br/>xgboost_v1_features.pkl"]
LSTM["LSTM<br/>lstm_<SYMBOL>.pth<br/>lstm_<SYMBOL>_scaler.pkl<br/>lstm_<SYMBOL>_target_scaler.pkl<br/>lstm_<SYMBOL>_cols.pkl"]
BP["Best Params<br/>best_params.json"]
```

**Diagram sources**
- [settings.py](file://config/settings.py#L173-L196)
- [best_params.json](file://models/best_params.json#L1-L6)

**Section sources**
- [settings.py](file://config/settings.py#L173-L196)
- [best_params.json](file://models/best_params.json#L1-L6)

### Model Loading in QuantAgent
QuantAgent resolves model paths from configuration, loads models with graceful fallbacks, and initializes LSTM predictors for multiple symbols.

```mermaid
sequenceDiagram
participant QA as "QuantAgent"
participant ST as "settings.py"
participant FS as "Filesystem"
participant LP as "LSTMPredictor"
QA->>ST : Read MODEL_PATH, XGB_MODEL_PATH, USE_XGBOOST
QA->>FS : Check existence of RF model and features
FS-->>QA : Exists/Not found
QA->>FS : Check existence of XGBoost model and features
FS-->>QA : Exists/Not found
QA->>ST : Read USE_LSTM, LSTM_MODEL_PATH, LSTM_SCALER_PATH
QA->>FS : Enumerate lstm_* files for key symbols
FS-->>QA : Found/Not found
QA->>LP : Instantiate LSTMPredictor(symbol-specific paths)
LP-->>QA : Ready or error
```

**Diagram sources**
- [quant_agent.py](file://analysis/quant_agent.py#L52-L107)
- [settings.py](file://config/settings.py#L173-L196)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L37-L78)

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L52-L107)
- [settings.py](file://config/settings.py#L173-L196)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L37-L78)

### LSTM Artifact Loading and Validation
LSTMPredictor loads model weights and scalers, validates feature columns, and prepares tensors for inference. It logs warnings for missing artifacts and raises errors for critical mismatches.

```mermaid
flowchart TD
Start(["Initialize LSTMPredictor"]) --> LoadScalers["Load feature/target scalers and feature columns"]
LoadScalers --> CheckModel["Load model weights (.pth)"]
CheckModel --> Validate["Validate input size from scaler"]
Validate --> Preprocess["Preprocess DataFrame to tensor"]
Preprocess --> Predict["Run inference and optional inverse transform"]
Predict --> End(["Return prediction"])
```

**Diagram sources**
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L37-L141)
- [lstm_model.py](file://strategy/lstm_model.py#L27-L69)

**Section sources**
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L37-L141)
- [lstm_model.py](file://strategy/lstm_model.py#L27-L69)

### Training Pipeline Components

#### Random Forest Trainer (train_model.py)
- Collects M1 data for configured symbols, computes ATR-based targets, builds features, splits data, trains a Random Forest, evaluates, and saves model and feature metadata.

```mermaid
flowchart TD
A["Connect to MT5 and detect symbols"] --> B["Fetch M1 data per symbol"]
B --> C["Add technical features"]
C --> D["Compute ATR-based targets"]
D --> E["Drop incomplete windows and filter rows"]
E --> F["Concatenate datasets"]
F --> G["Prepare features and labels"]
G --> H["Train Random Forest"]
H --> I["Evaluate and log metrics"]
I --> J["Save model and features"]
```

**Diagram sources**
- [train_model.py](file://train_model.py#L108-L230)
- [features.py](file://strategy/features.py#L6-L98)

**Section sources**
- [train_model.py](file://train_model.py#L108-L230)
- [features.py](file://strategy/features.py#L6-L98)

#### XGBoost Trainer (train_xgboost.py)
- Similar pipeline to RF trainer but trains XGBoost with early stopping and evaluation metrics, saving model and feature metadata.

```mermaid
flowchart TD
A["Connect to MT5 and detect symbols"] --> B["Fetch M15 data per symbol"]
B --> C["Add technical features"]
C --> D["Compute ATR-based targets"]
D --> E["Drop incomplete windows and filter rows"]
E --> F["Concatenate datasets"]
F --> G["Prepare features and labels"]
G --> H["Train XGBoost with eval set"]
H --> I["Evaluate and save metrics"]
I --> J["Save model and features"]
```

**Diagram sources**
- [train_xgboost.py](file://train_xgboost.py#L74-L209)
- [features.py](file://strategy/features.py#L6-L98)

**Section sources**
- [train_xgboost.py](file://train_xgboost.py#L74-L209)
- [features.py](file://strategy/features.py#L6-L98)

#### LSTM Trainer (train_lstm.py)
- Supports multi-symbol training with configurable epochs/timeframe. Saves model weights, feature scaler, target scaler, and feature columns.

```mermaid
flowchart TD
A["Parse arguments and connect to MT5"] --> B["Select symbols (--all or explicit)"]
B --> C["Fetch M15 data per symbol"]
C --> D["Add technical features and clean"]
D --> E["Scale features and target"]
E --> F["Create sequences with sequence length"]
F --> G["Split into train/test loaders"]
G --> H["Initialize BiLSTMWithAttention"]
H --> I["Train with early stopping"]
I --> J["Save best checkpoint and scalers"]
```

**Diagram sources**
- [train_lstm.py](file://train_lstm.py#L51-L186)
- [lstm_model.py](file://strategy/lstm_model.py#L27-L69)

**Section sources**
- [train_lstm.py](file://train_lstm.py#L51-L186)
- [lstm_model.py](file://strategy/lstm_model.py#L27-L69)

### Automatic Retraining and Performance Monitoring
AutoTrainer runs in a background thread, periodically:
- Checking performance metrics and triggering emergency retraining if win rate falls below threshold
- Retraining Random Forest and XGBoost on recent M15 data with hot-swapping
- Retraining LSTM models for key symbols with hot-swapping

```mermaid
sequenceDiagram
participant AT as "AutoTrainer"
participant DL as "Data Loader"
participant FE as "Features"
participant RF as "Random Forest"
participant XGB as "XGBoost"
participant LSTM as "LSTM Retrain"
participant ST as "Strategy"
AT->>DL : Fetch recent M15 data
DL-->>AT : DataFrame
AT->>FE : Add technical features
FE-->>AT : Enhanced DataFrame
AT->>RF : Retrain and validate
RF-->>AT : New model or keep old
AT->>XGB : Retrain and validate
XGB-->>AT : New model or keep old
AT->>LSTM : Retrain per key symbol
LSTM-->>AT : New predictors or keep old
AT->>ST : Hot-swap models in memory
```

**Diagram sources**
- [auto_trainer.py](file://utils/auto_trainer.py#L137-L172)
- [auto_trainer.py](file://utils/auto_trainer.py#L196-L275)
- [auto_trainer.py](file://utils/auto_trainer.py#L278-L347)
- [auto_trainer.py](file://utils/auto_trainer.py#L351-L494)

**Section sources**
- [auto_trainer.py](file://utils/auto_trainer.py#L80-L136)
- [auto_trainer.py](file://utils/auto_trainer.py#L175-L193)
- [auto_trainer.py](file://utils/auto_trainer.py#L196-L275)
- [auto_trainer.py](file://utils/auto_trainer.py#L278-L347)
- [auto_trainer.py](file://utils/auto_trainer.py#L351-L494)

### Model Compatibility, Versioning, and Migration
- Versioning strategy:
  - Use distinct filenames to indicate versions (e.g., scalper_v1 vs scalper_m1_v1)
  - Maintain separate feature metadata files per model
- Compatibility:
  - Feature metadata ensures inference uses the same columns used during training
  - LSTM predictors require matching input size from the saved scaler
- Migration:
  - Hot-swapping replaces models atomically under a lock to prevent race conditions
  - When upgrading model types (e.g., switching from RF to M1 variant), update configuration paths accordingly

**Section sources**
- [train_model.py](file://train_model.py#L223-L230)
- [train_xgboost.py](file://train_xgboost.py#L200-L209)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L60-L71)
- [auto_trainer.py](file://utils/auto_trainer.py#L258-L271)
- [auto_trainer.py](file://utils/auto_trainer.py#L338-L344)
- [auto_trainer.py](file://utils/auto_trainer.py#L476-L491)

## Dependency Analysis
- QuantAgent depends on settings for model paths and flags, and on LSTMPredictor for LSTM inference
- AutoTrainer depends on settings for intervals and model paths, and on data loader and features for retraining
- Training scripts depend on features and market data loader, and save to models/

```mermaid
graph TB
ST["settings.py"]
QA["quant_agent.py"]
LP["lstm_predictor.py"]
AT["auto_trainer.py"]
TM["train_model.py"]
TX["train_xgboost.py"]
TL["train_lstm.py"]
ST --> QA
QA --> LP
ST --> AT
AT --> TM
AT --> TX
AT --> TL
TM --> ST
TX --> ST
TL --> ST
```

**Diagram sources**
- [settings.py](file://config/settings.py#L173-L196)
- [quant_agent.py](file://analysis/quant_agent.py#L52-L107)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L37-L78)
- [auto_trainer.py](file://utils/auto_trainer.py#L196-L275)
- [train_model.py](file://train_model.py#L223-L230)
- [train_xgboost.py](file://train_xgboost.py#L200-L209)
- [train_lstm.py](file://train_lstm.py#L173-L186)

**Section sources**
- [settings.py](file://config/settings.py#L173-L196)
- [quant_agent.py](file://analysis/quant_agent.py#L52-L107)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L37-L78)
- [auto_trainer.py](file://utils/auto_trainer.py#L196-L275)
- [train_model.py](file://train_model.py#L223-L230)
- [train_xgboost.py](file://train_xgboost.py#L200-L209)
- [train_lstm.py](file://train_lstm.py#L173-L186)

## Performance Considerations
- Prefer hot-swapping models to minimize downtime during retraining
- Use early stopping and patience to avoid overfitting in LSTM training
- Validate new models against a held-out test set before swapping
- Monitor win rate thresholds to trigger emergency retraining proactively

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- Missing model files:
  - QuantAgent logs warnings when model or scaler files are not found; ensure correct paths and filenames
- Feature mismatch:
  - LSTMPredictor raises errors if required feature columns are missing; ensure feature metadata matches training
- Device allocation:
  - LSTM initialization selects CUDA if available; ensure drivers and libraries are installed
- Auto-retraining failures:
  - AutoTrainer catches exceptions and continues; review logs for specific errors and adjust intervals or thresholds

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L68-L70)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L43-L51)
- [lstm_predictor.py](file://strategy/lstm_predictor.py#L94-L95)
- [auto_trainer.py](file://utils/auto_trainer.py#L169-L171)

## Conclusion
The model management system combines explicit configuration-driven paths, robust loading with validation, and an adaptive auto-training mechanism. By maintaining versioned artifacts, validating feature sets, and hot-swapping models, the system supports reliable deployment and continuous improvement of trading models.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Model Paths and Flags Reference
- Random Forest model path and feature metadata path
- XGBoost model path and flag to enable/disable
- LSTM model path, scaler path, sequence length, and usage flag

**Section sources**
- [settings.py](file://config/settings.py#L173-L196)

### High-Frequency Forecasting Integration
- Optional integration with Chronos and Lag-Llama for long-horizon forecasting signals

**Section sources**
- [quant_agent.py](file://analysis/quant_agent.py#L75-L83)
- [hf_predictor.py](file://strategy/hf_predictor.py#L15-L32)
- [lag_llama_predictor.py](file://strategy/lag_llama_predictor.py#L31-L44)