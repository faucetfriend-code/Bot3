# Trading Bot v3 - Project Overview

<!--
RAG Metadata:
- Category: Overview
- Tags: trading-bot, architecture, project-structure, file-types
- Related: 01-core-trading-logic, 02-trading-strategies, 06-hub-system-architecture
-->

## Executive Summary

Trading Bot v3 is a sophisticated, multi-strategy cryptocurrency trading bot designed for perpetual futures trading on Pacifica.fi (Solana-based exchange). The system features 8 trading strategies, real-time WebSocket integration, comprehensive risk management with Kelly criterion, and a professional web interface.

---

## Directory Tree Structure

```
Bot3/
├── .claude/                          # Claude AI settings
│   └── settings.local.json
├── .git/                             # Git repository
├── .pytest_cache/                    # Pytest cache
├── .vscode/                          # VSCode settings
│   └── settings.json
├── Example files/                    # Reference implementations & utilities
│   ├── api/                          # API server examples
│   │   ├── api_server.py
│   │   ├── api_server_backup.py
│   │   ├── api_server_minimal.py
│   │   ├── syntax-agent.js
│   │   └── trading_bot_interface.html
│   ├── config/                       # Configuration examples
│   │   ├── .env.example
│   │   ├── docker-compose.yml
│   │   ├── Dockerfile
│   │   ├── pyproject.toml
│   │   ├── requirements.txt
│   │   └── schema.sql
│   ├── core_logic/                   # Reference core logic (51 files)
│   ├── docs/                         # Documentation
│   ├── tests/                        # Example tests
│   └── utilities/                    # SDK examples & scripts
├── analysis/                         # Analysis reports
├── assessment/                       # System assessments
├── core_logic/                       # Core logic package (simplified)
│   ├── __init__.py
│   ├── indicators.py
│   ├── models.py
│   └── pacifica_client.py
├── data/                             # Data storage
│   └── trading_bot.db
├── prompts/                          # AI prompts for development
│   ├── completed/                    # Completed prompts
│   └── *.md                          # Active prompts
├── research/                         # Research & documentation
├── tests/                            # E2E tests (Playwright)
│   ├── api-integration.spec.ts
│   ├── bot-controls.spec.ts
│   ├── performance-monitoring.spec.ts
│   └── websocket-updates.spec.ts
├── trading_bot_v2/                   # MAIN APPLICATION PACKAGE
│   ├── config/                       # Schema config
│   ├── docs/                         # Package docs
│   ├── migrations/                   # Database migrations
│   ├── scripts/                      # Utility scripts
│   ├── strategies/                   # Trading strategies (8 strategies)
│   ├── tests/                        # Unit/Integration tests
│   ├── api_server.py                 # FastAPI web server
│   ├── trading_bot.py                # Main bot class
│   ├── strategy_manager.py           # Multi-strategy orchestrator
│   ├── risk_manager.py               # Risk management
│   ├── database.py                   # SQLite database
│   ├── hub_system.py                 # Data hub & circuit breaker
│   └── ... (more modules)
├── interface.html                    # Web UI
├── package.json                      # Node.js config
├── playwright.config.ts              # Playwright config
└── AGENTS.md                         # Agent guidelines
```

---

## File Type Distribution

| File Type | Count | Primary Locations |
|-----------|-------|-------------------|
| **Python (.py)** | 100+ | `trading_bot_v2/`, `core_logic/` |
| **TypeScript (.ts)** | 5 | `tests/`, root |
| **JavaScript (.js)** | 2 | `Example files/api/` |
| **Markdown (.md)** | 80+ | `prompts/`, `analysis/` |
| **JSON (.json)** | 10+ | Root, `node_modules/` |
| **HTML (.html)** | 5 | Root, `Example files/api/` |
| **SQL (.sql)** | 2 | `trading_bot_v2/` |
| **Database (.db)** | 1 | `data/` |

---

## Topic Categories

### A. Core Trading Logic (`trading_bot_v2/`)
- Main bot class, strategy orchestration, execution layer

### B. Trading Strategies (`trading_bot_v2/strategies/`)
- 8 trading strategies with regime-based filtering

### C. Market Analysis (`trading_bot_v2/`)
- Regime detection, technical indicators, multi-timeframe data

### D. Exchange Integration (`trading_bot_v2/`)
- REST and WebSocket clients for Pacifica.fi

### E. Data Management (`trading_bot_v2/`)
- SQLite database, data models, grid lifecycle

### F. Hub System Architecture (`trading_bot_v2/`)
- DataHub, circuit breaker, event system, component registry

### G. Risk Management (`trading_bot_v2/`)
- Centralized risk, Kelly criterion position sizing

### H. Web Interface (`trading_bot_v2/`, root)
- FastAPI server, HTML interface, WebSocket updates

### I. Testing (`tests/`, `trading_bot_v2/tests/`)
- E2E Playwright tests, unit tests, performance tests

### J. Configuration (root, `trading_bot_v2/`)
- Environment variables, requirements, package config

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        TRADING BOT v3 ARCHITECTURE                   │
└─────────────────────────────────────────────────────────────────────┘

                    ┌──────────────────┐
                    │   Web Browser    │
                    │  (interface.html)│
                    └────────┬─────────┘
                             │ HTTP/WebSocket
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     API SERVER (FastAPI)                            │
│  - REST endpoints: /api/status, /api/start, /api/stop               │
│  - WebSocket: /ws for real-time updates                             │
└─────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       TRADING BOT                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                   StrategyManager (8 strategies)             │   │
│  │  MeanReversion | MACrossover | GridTrading | Liquidation   │   │
│  │  VWAPScalping | FundingArb | MomentumScalping | OrderBook  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                             │                                        │
│  ┌─────────────┐  ┌─────────────────┐  ┌─────────────────────┐     │
│  │ MarketRegime│  │  RiskManager    │  │  ExecutionLayer     │     │
│  │  Detector   │  │  (Kelly)        │  │  (1m/5m timing)     │     │
│  └─────────────┘  └─────────────────┘  └─────────────────────┘     │
│                             │                                        │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Hub System                                │   │
│  │  DataHub | Circuit Breaker | Event System | Registry        │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    DATA & EXCHANGE LAYER                             │
│  DatabaseManager | PacificaClient (REST) | PacificaWSClient (WS)    │
└─────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
                    ┌──────────────────┐
                    │  Pacifica.fi API │
                    │  (Solana Perps)  │
                    └──────────────────┘
```

---

## Design Patterns Used

1. **Singleton Pattern**: WebSocket client single connection
2. **Factory Pattern**: Strategy creation in StrategyManager
3. **Observer Pattern**: Event system for decoupled communication
4. **Circuit Breaker Pattern**: Database failure protection
5. **State Machine**: Grid lifecycle state transitions
6. **Repository Pattern**: DatabaseManager abstraction
7. **Dependency Injection**: Constructor-based dependencies

---

## Code Conventions

- **Classes**: PascalCase (e.g., `TradingBot`, `StrategyManager`)
- **Functions/Methods**: snake_case (e.g., `calculate_rsi`)
- **Constants**: UPPER_CASE (e.g., `RSI_OVERSOLD`)
- **Type Hints**: Required for all public functions
- **Async/Await**: Used for I/O operations
- **Docstrings**: Google/NumPy style

---

## Summary Statistics

| Metric | Count |
|--------|-------|
| Total Python Files | 100+ |
| Trading Strategies | 8 |
| Market Regimes | 5 |
| Technical Indicators | 10+ |
| API Endpoints | 15+ |
| E2E Test Files | 4 |
| Unit Test Files | 20+ |

---

## Related Reports

- [01-core-trading-logic.md](./01-core-trading-logic.md) - Core bot components
- [02-trading-strategies.md](./02-trading-strategies.md) - All 8 strategies
- [03-market-analysis.md](./03-market-analysis.md) - Indicators and regime detection
- [04-exchange-integration.md](./04-exchange-integration.md) - Pacifica API clients
- [05-data-management.md](./05-data-management.md) - Database and models
- [06-hub-system-architecture.md](./06-hub-system-architecture.md) - Hub system
- [07-risk-management.md](./07-risk-management.md) - Risk and position sizing
- [08-web-interface-api.md](./08-web-interface-api.md) - Web UI and API
- [09-configuration-setup.md](./09-configuration-setup.md) - Configuration
- [10-testing-infrastructure.md](./10-testing-infrastructure.md) - Testing
