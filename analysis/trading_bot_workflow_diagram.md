# Trading Bot Workflow Diagram

```mermaid
graph TB
    %% External Data Sources
    subgraph "External Sources"
        WS[Pacifica WebSocket<br/>Real-time Prices]
        REST[Pacifica REST API<br/>Fallback Data]
        CLIENT[Pacifica Client<br/>Trading Operations]
    end

    %% Core Components
    subgraph "Core Components"
        WSC[WebSocket Client<br/>pacifica_ws_client.py]
        CACHE[Price Cache<br/>_price_cache dict<br/>In-Memory]
        TB[Trading Bot<br/>trading_bot.py]
        DB[(SQLite Database<br/>trading_bot.db<br/>Persistent Storage)]
        API[API Server<br/>api_server.py<br/>FastAPI]
    end

    %% Data Flow
    WS --> WSC
    WSC --> CACHE
    CACHE --> TB
    TB --> DB
    TB --> CLIENT
    CLIENT --> TB

    %% API Endpoints
    subgraph "API Endpoints"
        STATUS[/api/status<br/>Bot Status + Prices/]
        TRADES[/api/trades<br/>Trade History/]
        POSITIONS[/api/positions<br/>Open Positions/]
        SYNC[/api/sync-positions<br/>Force Sync/]
    end

    API --> STATUS
    API --> TRADES
    API --> POSITIONS
    API --> SYNC

    %% Database Tables
    subgraph "Database Tables"
        TRADES_TABLE[(trades<br/>Trade Records)]
        POSITIONS_TABLE[(positions<br/>Position Data)]
        SIGNALS_TABLE[(signals<br/>Trading Signals)]
        METRICS_TABLE[(performance_metrics<br/>P&L Tracking)]
    end

    DB --> TRADES_TABLE
    DB --> POSITIONS_TABLE
    DB --> SIGNALS_TABLE
    DB --> METRICS_TABLE

    %% Trading Logic
    subgraph "Trading Logic"
        STRATEGY[Strategy Manager<br/>strategy_manager.py]
        KELLY[Kelly Position Sizer<br/>kelly_position_sizer.py]
        RISK[Rick Manager<br/>risk_manager.py]
    end

    TB --> STRATEGY
    TB --> KELLY
    TB --> RISK

    %% Data Dependencies
    CACHE -.->|"get_ticker_ws()"| TB
    DB -.->|"get_positions()<br/>get_trades()"| TB
    DB -.->|"Historical Data"| KELLY
    CACHE -.->|"Real-time Prices"| STRATEGY

    %% External Connections
    REST -.->|"Fallback"| TB
    STATUS -.->|"Price Data"| CACHE
    TRADES -.->|"Trade Data"| DB
    POSITIONS -.->|"Position Data"| DB

    %% Status Indicators
    classDef working fill:#d4edda,stroke:#155724,stroke-width:2px
    classDef partial fill:#fff3cd,stroke:#856404,stroke-width:2px
    classDef needsFix fill:#f8d7da,stroke:#721c24,stroke-width:2px

    class WS,WSC,CACHE,DB,API,STATUS,TRADES,POSITIONS,SYNC working
    class TB,CLIENT,STRATEGY,KELLY,RISK partial
    class REST needsFix
```

## Current State Analysis

### ✅ Working Components
- **WebSocket Client**: Successfully connects and parses price updates
- **Price Cache**: Stores real-time prices in memory for fast access
- **Database**: SQLite setup with all required tables (trades, positions, signals, metrics)
- **API Server**: FastAPI endpoints serving status, trades, positions
- **Database Tables**: All 15 tables created and functional

### ⚠️ Partially Working
- **Trading Bot**: Core logic exists but needs integration testing
- **Pacifica Client**: Trading operations interface (needs verification)
- **Strategy Manager**: Signal generation logic (needs testing)
- **Kelly Position Sizer**: Risk-adjusted sizing (depends on trade history)
- **Risk Manager**: Circuit breaker and position limits (needs validation)

### ❌ Needs Fixes
- **REST API Fallback**: Should be backup only, not primary data source
- **WebSocket Price Integration**: Ensure `get_ticker_ws` prioritizes cache over REST
- **Strategy Execution**: Verify signals trigger actual trades
- **Position Sync**: Ensure database positions match Pacifica state

## Critical Integration Points

### 1. Price Data Flow
```
WebSocket → Price Cache → Trading Bot → Strategies
```
**Status**: WebSocket parsing fixed, cache working, needs verification that strategies use cache.

### 2. Trade Execution Flow
```
Strategy Signal → Position Sizing → Risk Check → Client Order → Database Save
```
**Status**: Components exist but end-to-end flow needs testing.

### 3. Data Persistence Flow
```
Live Positions → Database → API Endpoints → Monitoring
```
**Status**: Database saves working, API serving data, needs sync verification.

## Required Fixes for Full Integration

1. **Verify Price Priority**: Ensure `get_ticker_ws` returns WebSocket cache data, not REST fallback
2. **Test Strategy Execution**: Run strategies with real signals and verify trade placement
3. **Position Synchronization**: Confirm database positions match Pacifica account state
4. **Error Handling**: Add graceful degradation when WebSocket disconnects
5. **Performance Monitoring**: Add metrics for cache hit rates and response times

## Testing Workflow

```mermaid
graph LR
    A[Start Bot] --> B[Check WebSocket Connection]
    B --> C[Verify Price Cache Population]
    C --> D[Test API Endpoints]
    D --> E[Run Strategy Simulation]
    E --> F[Verify Database Updates]
    F --> G[Check Position Sync]
    G --> H[Monitor Real Trading]
```

## Next Steps Priority

1. **High Priority**: Verify WebSocket price integration in trading decisions
2. **High Priority**: Test end-to-end trade execution flow
3. **Medium Priority**: Add comprehensive error handling and logging
4. **Medium Priority**: Implement automated position synchronization
5. **Low Priority**: Add performance monitoring and alerting</content>
<parameter name="filePath">trading_bot_workflow_diagram.md