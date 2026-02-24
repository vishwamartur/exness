# Data Loader Architecture

<cite>
**Referenced Files in This Document**
- [loader.py](file://market_data/loader.py)
- [mt5_client.py](file://execution/mt5_client.py)
- [settings.py](file://config/settings.py)
- [data_cache.py](file://utils/data_cache.py)
- [async_utils.py](file://utils/async_utils.py)
- [main.py](file://main.py)
- [institutional_strategy.py](file://strategy/institutional_strategy.py)
- [risk_manager.py](file://utils/risk_manager.py)
- [news_filter.py](file://utils/news_filter.py)
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
This document describes the MarketDataLoader architecture responsible for connecting to the MetaTrader 5 terminal, authenticating, managing connections, and fetching historical market data. It covers the timeframe mapping system, historical data retrieval functions, error handling strategies, connection management, symbol availability checks, market hours validation, and performance optimizations such as caching and asynchronous execution.

## Project Structure
The MarketDataLoader sits within the market_data package and integrates with configuration, execution, and utility modules. The primary entry points for MT5 connectivity are centralized in the execution layer, while data fetching is encapsulated in the loader module. Utilities provide caching and async execution helpers.

```mermaid
graph TB
subgraph "Market Data"
MDL["market_data/loader.py"]
DC["utils/data_cache.py"]
end
subgraph "Execution"
MTC["execution/mt5_client.py"]
end
subgraph "Configuration"
CFG["config/settings.py"]
end
subgraph "Utilities"
AU["utils/async_utils.py"]
RM["utils/risk_manager.py"]
NF["utils/news_filter.py"]
end
subgraph "Application"
MAIN["main.py"]
STRAT["strategy/institutional_strategy.py"]
end
MAIN --> MTC
MAIN --> STRAT
STRAT --> MDL
STRAT --> DC
MDL --> CFG
MTC --> CFG
DC --> MDL
STRAT --> RM
STRAT --> NF
STRAT --> AU
```

**Diagram sources**
- [loader.py](file://market_data/loader.py#L1-L83)
- [mt5_client.py](file://execution/mt5_client.py#L1-L385)
- [settings.py](file://config/settings.py#L1-L201)
- [data_cache.py](file://utils/data_cache.py#L1-L77)
- [async_utils.py](file://utils/async_utils.py#L1-L45)
- [main.py](file://main.py#L1-L122)
- [institutional_strategy.py](file://strategy/institutional_strategy.py#L1-L200)
- [risk_manager.py](file://utils/risk_manager.py#L117-L176)
- [news_filter.py](file://utils/news_filter.py#L176-L204)

**Section sources**
- [loader.py](file://market_data/loader.py#L1-L83)
- [mt5_client.py](file://execution/mt5_client.py#L1-L385)
- [settings.py](file://config/settings.py#L1-L201)
- [data_cache.py](file://utils/data_cache.py#L1-L77)
- [async_utils.py](file://utils/async_utils.py#L1-L45)
- [main.py](file://main.py#L1-L122)
- [institutional_strategy.py](file://strategy/institutional_strategy.py#L1-L200)

## Core Components
- MarketDataLoader: Provides MT5 connection initialization, authentication, and historical data retrieval functions.
- MT5Client: Encapsulates MT5 connection lifecycle and symbol availability detection.
- DataCache: TTL-based caching layer to reduce redundant MT5 API calls.
- Async utilities: Thread pool executor and rate limiter for non-blocking operations.
- Configuration: Centralized settings for MT5 credentials, server path, and trading parameters.

Key responsibilities:
- Initialize MT5 terminal and authenticate with credentials from environment variables.
- Convert string timeframes to MT5 constants and fetch historical bars.
- Provide multi-timeframe data aggregation for analysis.
- Manage cache TTLs per timeframe to optimize performance.
- Integrate with risk and news filters for session-aware trading decisions.

**Section sources**
- [loader.py](file://market_data/loader.py#L12-L83)
- [mt5_client.py](file://execution/mt5_client.py#L12-L105)
- [data_cache.py](file://utils/data_cache.py#L16-L77)
- [settings.py](file://config/settings.py#L7-L11)

## Architecture Overview
The system follows a layered architecture:
- Application layer orchestrates scanning and trading.
- Execution layer manages MT5 connectivity and symbol detection.
- Market data layer handles historical data retrieval and caching.
- Utilities provide async execution and risk/session validation.

```mermaid
sequenceDiagram
participant App as "Application (main.py)"
participant Strat as "Strategy (institutional_strategy.py)"
participant Loader as "MarketDataLoader (loader.py)"
participant Cache as "DataCache (data_cache.py)"
participant MT5 as "MetaTrader 5 Terminal"
App->>Strat : Initialize and start scanner
Strat->>Cache : get(symbol, timeframe, n_bars)
Cache->>Loader : get_historical_data(symbol, timeframe, n_bars)
Loader->>MT5 : copy_rates_from_pos(...)
MT5-->>Loader : DataFrame rates
Loader-->>Cache : DataFrame
Cache-->>Strat : DataFrame (cached or fresh)
Strat-->>App : Scan results and candidates
```

**Diagram sources**
- [main.py](file://main.py#L75-L105)
- [institutional_strategy.py](file://strategy/institutional_strategy.py#L99-L184)
- [data_cache.py](file://utils/data_cache.py#L30-L48)
- [loader.py](file://market_data/loader.py#L40-L59)

## Detailed Component Analysis

### MT5 Connection Initialization and Authentication
The connection process is handled by the execution layer and the loader module:
- MT5Client.connect initializes the terminal and logs in using credentials from settings.
- MarketDataLoader.initial_connect performs a secondary initialization and login if needed.
- Settings are loaded from environment variables for login, server, and terminal path.

```mermaid
sequenceDiagram
participant App as "Application"
participant MTC as "MT5Client"
participant Loader as "MarketDataLoader"
participant MT5 as "MetaTrader 5 Terminal"
App->>MTC : connect()
MTC->>MT5 : initialize(path)
MT5-->>MTC : success/failure
MTC->>MT5 : login(login, password, server)
MT5-->>MTC : authorized/failed
App->>Loader : initial_connect() (fallback)
Loader->>MT5 : initialize(path)
Loader->>MT5 : login(login, password, server)
MT5-->>Loader : success/failure
```

**Diagram sources**
- [mt5_client.py](file://execution/mt5_client.py#L18-L27)
- [loader.py](file://market_data/loader.py#L24-L37)
- [settings.py](file://config/settings.py#L8-L11)

**Section sources**
- [mt5_client.py](file://execution/mt5_client.py#L18-L27)
- [loader.py](file://market_data/loader.py#L24-L37)
- [settings.py](file://config/settings.py#L8-L11)

### Timeframe Mapping System
The loader defines a mapping from string timeframes to MT5 constants. This ensures consistent conversion across data retrieval functions.

```mermaid
flowchart TD
Start(["Input: timeframe_str"]) --> Lookup["Lookup TF_MAP"]
Lookup --> Found{"Found?"}
Found --> |Yes| UseConst["Use MT5 constant"]
Found --> |No| Default["Use default (M15)"]
UseConst --> End(["Return MT5 constant"])
Default --> End
```

**Diagram sources**
- [loader.py](file://market_data/loader.py#L13-L21)

**Section sources**
- [loader.py](file://market_data/loader.py#L13-L21)

### Historical Data Retrieval Functions
- get_historical_data: Validates terminal connection, converts timeframe, fetches rates, and returns a DataFrame with timestamps converted.
- get_multi_timeframe_data: Aggregates M15, H1, and H4 data for a given symbol, scaling bar counts per timeframe.

```mermaid
sequenceDiagram
participant Strat as "Strategy"
participant Loader as "MarketDataLoader"
participant MT5 as "MetaTrader 5 Terminal"
Strat->>Loader : get_historical_data(symbol, timeframe_str, n_bars)
Loader->>Loader : validate terminal connection
Loader->>Loader : map timeframe to MT5 constant
Loader->>MT5 : copy_rates_from_pos(symbol, timeframe, 0, n_bars)
MT5-->>Loader : rates array
Loader->>Loader : convert to DataFrame and timestamps
Loader-->>Strat : DataFrame
```

**Diagram sources**
- [loader.py](file://market_data/loader.py#L40-L59)

**Section sources**
- [loader.py](file://market_data/loader.py#L40-L82)

### Multi-Timeframe Data Aggregation
The multi-timeframe function computes bar counts per timeframe and aggregates results into a dictionary keyed by timeframe strings.

```mermaid
flowchart TD
Start(["Input: symbol, n_bars_primary"]) --> Compute["Compute tf_bars for M15/H1/H4"]
Compute --> Loop["For each timeframe"]
Loop --> Fetch["Call get_historical_data(symbol, tf, bars)"]
Fetch --> Success{"DataFrame returned?"}
Success --> |Yes| Store["Store DataFrame in dict"]
Success --> |No| Warn["Print warning and continue"]
Store --> Next["Next timeframe"]
Warn --> Next
Next --> Done(["Return dict of DataFrames"])
```

**Diagram sources**
- [loader.py](file://market_data/loader.py#L62-L82)

**Section sources**
- [loader.py](file://market_data/loader.py#L62-L82)

### Connection Management and Symbol Availability
- MT5Client.detect_available_symbols auto-detects available instruments on the account by testing base symbols with Exness suffixes and categorizes them by asset class.
- Settings are updated at runtime with discovered symbols and categories.

```mermaid
flowchart TD
Start(["detect_available_symbols()"]) --> Iterate["Iterate base symbols"]
Iterate --> TrySuffix["Try suffixes ['', 'm', 'c']"]
TrySuffix --> Exists{"symbol_info exists?"}
Exists --> |Yes| Visible{"trade_mode != DISABLED<br/>and currency allowed?"}
Visible --> |Yes| Select["Enable in Market Watch if hidden"]
Select --> Categorize["Categorize by asset class"]
Categorize --> Collect["Add to found_* lists"]
Exists --> |No| NextBase["Next base symbol"]
Visible --> |No| NextBase
Collect --> Update["Update settings.SYMBOLS_*"]
Update --> Report["Print summary"]
Report --> End(["Return success/failure"])
```

**Diagram sources**
- [mt5_client.py](file://execution/mt5_client.py#L29-L101)
- [settings.py](file://config/settings.py#L17-L60)

**Section sources**
- [mt5_client.py](file://execution/mt5_client.py#L29-L101)
- [settings.py](file://config/settings.py#L17-L60)

### Market Hours Validation and Session Awareness
Risk manager enforces session gating for non-crypto symbols based on configured sessions. News blackout detection prevents trading during high-impact events.

```mermaid
flowchart TD
Start(["check_execution()"]) --> Spread["Check spread vs thresholds"]
Spread --> News["Check news blackout"]
News --> Session{"SCALP_SESSION_FILTER enabled?"}
Session --> |Yes| Crypto{"Is symbol crypto?"}
Crypto --> |Yes| Allow["Allow trading 24/7"]
Crypto --> |No| HourCheck["Check current UTC hour in configured sessions"]
HourCheck --> InSession{"Within active session?"}
InSession --> |Yes| Allow
InSession --> |No| Block["Block trading"]
Session --> |No| Allow
Allow --> End(["Return allowed/rationale"])
Block --> End
```

**Diagram sources**
- [risk_manager.py](file://utils/risk_manager.py#L117-L163)
- [settings.py](file://config/settings.py#L110-L116)
- [news_filter.py](file://utils/news_filter.py#L176-L204)

**Section sources**
- [risk_manager.py](file://utils/risk_manager.py#L117-L163)
- [settings.py](file://config/settings.py#L110-L116)
- [news_filter.py](file://utils/news_filter.py#L176-L204)

### Data Caching and Performance Optimization
DataCache reduces redundant MT5 API calls by caching DataFrames with TTL per timeframe. It invalidates entries selectively and reports cache statistics.

```mermaid
flowchart TD
Start(["get(symbol, timeframe, n_bars)"]) --> Key["Build cache key"]
Key --> CheckCache{"Key exists and fresh?"}
CheckCache --> |Yes| ReturnCache["Return cached DataFrame"]
CheckCache --> |No| Fetch["loader.get_historical_data(...)"]
Fetch --> CacheMiss{"DataFrame returned?"}
CacheMiss --> |Yes| Save["Save to cache with timestamp"]
CacheMiss --> |No| ReturnNone["Return None"]
Save --> ReturnDF["Return DataFrame"]
ReturnCache --> End(["Done"])
ReturnDF --> End
ReturnNone --> End
```

**Diagram sources**
- [data_cache.py](file://utils/data_cache.py#L30-L48)
- [loader.py](file://market_data/loader.py#L40-L59)

**Section sources**
- [data_cache.py](file://utils/data_cache.py#L16-L77)
- [loader.py](file://market_data/loader.py#L40-L59)

### Asynchronous Execution and Rate Limiting
Async utilities provide a thread pool executor to run blocking MT5 calls without blocking the asyncio event loop. A simple token bucket rate limiter can throttle requests.

```mermaid
classDiagram
class AsyncRateLimiter {
+float rate_limit
+float period
+float tokens
+float last_update
+Lock lock
+acquire() void
}
class run_in_executor {
+func func
+args args
+kwargs kwargs
+return Future
}
```

**Diagram sources**
- [async_utils.py](file://utils/async_utils.py#L18-L45)

**Section sources**
- [async_utils.py](file://utils/async_utils.py#L6-L16)
- [async_utils.py](file://utils/async_utils.py#L18-L45)

## Dependency Analysis
The MarketDataLoader depends on:
- MT5 constants and functions for data retrieval.
- Settings for credentials and terminal path.
- DataCache for performance optimization.
- Risk and news utilities for session-aware trading decisions.

```mermaid
graph TB
Loader["market_data/loader.py"] --> Settings["config/settings.py"]
Loader --> MT5["MetaTrader 5 SDK"]
Cache["utils/data_cache.py"] --> Loader
Strategy["strategy/institutional_strategy.py"] --> Loader
Strategy --> Cache
Strategy --> Risk["utils/risk_manager.py"]
Strategy --> News["utils/news_filter.py"]
Client["execution/mt5_client.py"] --> Settings
```

**Diagram sources**
- [loader.py](file://market_data/loader.py#L1-L10)
- [data_cache.py](file://utils/data_cache.py#L12-L13)
- [institutional_strategy.py](file://strategy/institutional_strategy.py#L23-L35)
- [risk_manager.py](file://utils/risk_manager.py#L117-L163)
- [news_filter.py](file://utils/news_filter.py#L176-L204)
- [mt5_client.py](file://execution/mt5_client.py#L1-L9)

**Section sources**
- [loader.py](file://market_data/loader.py#L1-L10)
- [data_cache.py](file://utils/data_cache.py#L12-L13)
- [institutional_strategy.py](file://strategy/institutional_strategy.py#L23-L35)

## Performance Considerations
- Caching: DataCache reduces MT5 API calls by caching per timeframe with TTLs tailored to data volatility.
- Batched multi-timeframe retrieval: get_multi_timeframe_data consolidates multiple requests into a single scan cycle.
- Asynchronous execution: run_in_executor allows non-blocking MT5 calls in an asyncio loop.
- Rate limiting: AsyncRateLimiter can smooth request bursts to avoid throttling.
- Session-aware filtering: Risk manager and news filter prevent unnecessary data fetches during off-hours or blackout periods.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common connection problems and resolutions:
- Initialization failure: Verify MT5 terminal path in settings and ensure the executable exists.
- Login failure: Confirm login credentials and server name in environment variables.
- No terminal info: The loader attempts a fallback initialization; ensure the terminal is running and accessible.
- No rates returned: Check symbol availability and visibility; use MT5Client.detect_available_symbols to auto-detect instruments.
- Spread too high: Adjust thresholds in settings or avoid trading during high-spread periods.
- Off-session trading: Enable session filters and align trading windows with London/NY opens.
- News blackout: Avoid trading during scheduled high-impact events.

Practical steps:
- Validate environment variables for MT5 credentials and terminal path.
- Use MT5Client.detect_available_symbols to confirm instrument availability.
- Monitor cache hit ratio via DataCache.stats to assess effectiveness.
- Review logs for MT5 error codes printed during initialization and login.

**Section sources**
- [loader.py](file://market_data/loader.py#L24-L37)
- [mt5_client.py](file://execution/mt5_client.py#L29-L101)
- [data_cache.py](file://utils/data_cache.py#L66-L77)
- [risk_manager.py](file://utils/risk_manager.py#L117-L163)
- [news_filter.py](file://utils/news_filter.py#L176-L204)

## Conclusion
The MarketDataLoader architecture provides a robust foundation for MT5 data retrieval with clear separation of concerns. It integrates connection management, timeframe mapping, caching, and session-aware risk controls to support efficient and reliable trading workflows. By leveraging caching and async execution, the system minimizes API overhead and improves responsiveness, while built-in validation ensures data quality and compliance with trading policies.