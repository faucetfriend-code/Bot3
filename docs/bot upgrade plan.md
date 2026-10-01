Bot 3 Implementation Plan: Phases 1-3
Planning Structure
.planning/
├── ROADMAP.md
└── phases/
    ├── 01-production-readiness/
    │   ├── 01-01-PLAN.md  (Git + Docker)
    │   ├── 01-02-PLAN.md  (Security)
    │   ├── 01-03-PLAN.md  (CI/CD)
    │   ├── 01-04-PLAN.md  (Prometheus)
    │   └── 01-05-PLAN.md  (Telegram)
    ├── 02-data-performance/
    │   ├── 02-01-PLAN.md  (TimescaleDB)
    │   ├── 02-02-PLAN.md  (Data Quality)
    │   └── 02-03-PLAN.md  (Grafana + Profiling)
    └── 03-ml-optimization/
        ├── 03-01-PLAN.md  (GMM Regime)
        ├── 03-02-PLAN.md  (Optuna)
        └── 03-03-PLAN.md  (Strategy Monitoring)
---
## Phase 1: Production Readiness (5 Plans)
### Plan 01-01: Git Init + Docker Setup
**Scope**: 2 tasks | **Autonomous**: Yes
**Task 1: Initialize Git Repository**
- Run `git init` in Bot 3 directory
- Create `.gitignore` (Python, .env, __pycache__, *.db, node_modules)
- Initial commit with all existing code
**Task 2: Create Dockerfile + docker-compose.yml**
- Multi-stage Python 3.11 slim image
- Install requirements.txt
- Copy trading_bot_v2 package
- Health check endpoint
- docker-compose with bot service + volume mounts for .env and data
**Verification**: `docker-compose build` succeeds, `docker-compose up` starts bot
---
Plan 01-02: Security Scanning
Scope: 3 tasks | Autonomous: Yes
Task 1: Install Security Tools
- Add bandit and safety to requirements.txt
- Run bandit -r trading_bot_v2/ -f json -o bandit-report.json
- Run safety check --json > safety-report.json
Task 2: Fix Critical Security Findings
- Review bandit output for high-severity issues
- Fix hardcoded secrets, SQL injection risks, insecure crypto
- Add .env to .gitignore (verify it's not tracked)
Task 3: Create Security Baseline
- Document known low-risk findings in docs/SECURITY-AUDIT.md
- Add security scan to CI pipeline (plan 01-03)
Verification: bandit -r trading_bot_v2/ returns zero high-severity findings
---
Plan 01-03: CI/CD Pipeline
Scope: 2 tasks | Autonomous: Yes
Task 1: Create GitHub Actions Workflow
- .github/workflows/ci.yml
- Triggers: push to main, pull requests
- Jobs: lint (ruff), type-check (mypy), test (pytest), security (bandit)
Task 2: Add Pre-commit Hooks
- .pre-commit-config.yaml with ruff, mypy, bandit
- Install hooks in setup script
Verification: Push to GitHub triggers CI, all checks pass
---
Plan 01-04: Prometheus Metrics
Scope: 3 tasks | Autonomous: Yes
Task 1: Add prometheus_client Dependency
- Add to requirements.txt
- Create trading_bot_v2/metrics.py module
Task 2: Instrument Core Components
- Counter: bot_trades_total (labels: strategy, symbol, side)
- Gauge: bot_open_positions (label: strategy)
- Histogram: bot_signal_latency_seconds
- Gauge: bot_balance_usd
- Counter: bot_errors_total (label: error_type)
Task 3: Add /metrics Endpoint
- FastAPI route in api_server.py
- Return generate_latest() with proper content type
Verification: curl http://localhost:8000/metrics returns Prometheus format
---
Plan 01-05: Telegram Alerts
Scope: 3 tasks | Autonomous: Yes
Task 1: Create Telegram Alert Module
- trading_bot_v2/telegram_alerts.py
- Use httpx for async HTTP to Telegram Bot API
- Config from .env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Task 2: Add Alert Types
- Trade executed (entry, exit, P&L)
- Error occurred (exchange errors, risk violations)
- Circuit breaker triggered
- Daily summary (trades count, P&L, win rate)
Task 3: Integrate with EventBus
- Subscribe to TRADE_OPENED, TRADE_CLOSED, ERROR_OCCURRED events
- Non-blocking async sends with retry
Verification: Send test alert via script, confirm receipt in Telegram
---
Phase 2: Data & Performance (3 Plans)
Plan 02-01: TimescaleDB Migration
Scope: 3 tasks | Autonomous: Yes
Task 1: Set Up PostgreSQL + TimescaleDB
- Add to docker-compose.yml: timescale/timescaledb:latest-pg16
- Create volume for data persistence
- Configure environment variables for DB connection
Task 2: Create Migration Scripts
- trading_bot_v2/migrations/002_timescale.sql
- Convert candles, trades, positions tables to hypertables
- Add compression policies for data > 7 days
- Create continuous aggregates for 1h, 4h candles
Task 3: Update DatabaseManager
- Add PostgreSQL connection support (asyncpg)
- Keep SQLite fallback for backward compatibility
- Update schema.sql for new tables
Verification: Run migration, query hypertables, verify compression
---
Plan 02-02: Data Quality + Archival
Scope: 3 tasks | Autonomous: Yes
Task 1: Add Data Validation Pipeline
- trading_bot_v2/data_validator.py
- Check for: missing candles, out-of-order timestamps, zero-volume bars
- Log anomalies to data_quality_log table
Task 2: Implement Retention Policy
- Candles > 6 months: compress to TimescaleDB native compression
- Candles > 2 years: archive to Parquet files in data/archive/
- Add retention policy to TimescaleDB
Task 3: Add Data Health Dashboard
- New API endpoint: /api/data-health
- Return: last candle time, gap count, compression ratio, archive size
Verification: Run validator, check logs, verify retention deletes old data
---
Plan 02-03: Grafana Dashboards + Profiling
Scope: 3 tasks | Autonomous: Yes
Task 1: Set Up Grafana
- Add to docker-compose.yml: grafana/grafana:latest
- Configure Prometheus data source
- Create dashboard JSON for trading bot
Task 2: Create Trading Dashboard
- Panel: Trade count (hourly/daily)
- Panel: P&L curve
- Panel: Open positions by strategy
- Panel: Error rate
- Panel: Signal latency histogram
Task 3: Profile Trading Loop
- Add cProfile profiling to _trading_loop()
- Identify bottlenecks in 30-second cycle
- Document findings in docs/PERFORMANCE-PROFILE.md
Verification: Access Grafana at localhost:3000, see live metrics
---
Phase 3: ML & Optimization (3 Plans)
Plan 03-01: GMM Regime Detection
Scope: 3 tasks | Autonomous: Yes
Task 1: Add ML Dependencies
- Add scikit-learn to requirements.txt
- Create trading_bot_v2/ml/ directory
Task 2: Implement GMM Regime Detector
- trading_bot_v2/ml/gmm_regime.py
- Features: volatility (20-period std), returns (20-period mean), skewness
- 3 regimes: trending, ranging, volatile
- Train on 6 months of historical data
- Save/load model with joblib
Task 3: Integrate with MarketRegime
- Add GMM as alternative to ADX-based detection
- Feature flag: USE_ML_REGIME=true
- Compare accuracy in backtesting
Verification: Run backtest with GMM vs ADX, compare regime accuracy
---
Plan 03-02: Optuna Parameter Optimization
Scope: 3 tasks | Autonomous: Yes
Task 1: Add Optuna Dependency
- Add optuna to requirements.txt
- Create trading_bot_v2/optimization/ directory
Task 2: Create Optimization Framework
- trading_bot_v2/optimization/optuna_runner.py
- Define parameter search spaces for each strategy
- Objective function: maximize Sharpe ratio
- Support TPE and Random samplers
- Save study results to SQLite
Task 3: Create CLI Runner
- trading_bot_v2/optimization/run_optimize.py
- CLI flags: --strategy, --trials, --sampler
- Output: best parameters, optimization history plot
Verification: Run 10-trial optimization on mean_reversion, see improved Sharpe
---
Plan 03-03: Strategy Monitoring
Scope: 3 tasks | Autonomous: Yes
Task 1: Add Correlation Tracking
- trading_bot_v2/strategy_monitor.py
- Calculate rolling 30-day correlation between strategy returns
- Store in strategy_correlations table
- Alert if correlation > 0.7 (concentration risk)
Task 2: Add Decay Detection
- Track strategy performance over rolling 30-day windows
- Compare current Sharpe to 90-day average
- Alert if Sharpe drops > 50% from average
Task 3: Add Monitoring Dashboard Panel
- Grafana panel: strategy correlation heatmap
- Grafana panel: strategy Sharpe trend
- API endpoint: /api/strategy-health
Verification: Run monitoring for 7 days, verify correlation and decay alerts
---
Execution Order
Phase 1 (parallel where possible):
├── 01-01: Git + Docker ─────────┐
├── 01-02: Security ─────────────┤── All autonomous, can run in parallel
├── 01-03: CI/CD ────────────────┤
├── 01-04: Prometheus ───────────┤
└── 01-05: Telegram ─────────────┘
Phase 2 (sequential, depends on Phase 1):
├── 02-01: TimescaleDB (needs Docker from 01-01)
├── 02-02: Data Quality (needs TimescaleDB from 02-01)
└── 02-03: Grafana + Profiling (needs Prometheus from 01-04)
Phase 3 (sequential, depends on Phase 2):
├── 03-01: GMM Regime (needs TimescaleDB for historical data)
├── 03-02: Optuna (needs backtest framework + TimescaleDB)
└── 03-03: Strategy Monitoring (needs Prometheus from 01-04)
---
Total Effort
Phase
Phase 1
Phase 2
Phase 3
Total
---
Ready to Execute?