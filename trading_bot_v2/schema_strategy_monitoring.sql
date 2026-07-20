-- Strategy Monitoring Schema
-- Tracks individual trade returns, correlation snapshots, and per-strategy health.
-- Designed for Plan 03-03: Strategy Monitor.

-- strategy_returns: individual trade returns
CREATE TABLE IF NOT EXISTS strategy_returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    pnl_pct REAL NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- strategy_correlations: daily correlation snapshots
CREATE TABLE IF NOT EXISTS strategy_correlations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_a TEXT NOT NULL,
    strategy_b TEXT NOT NULL,
    correlation REAL NOT NULL,
    window_days INTEGER DEFAULT 30,
    calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- strategy_health_snapshots: daily health summaries
CREATE TABLE IF NOT EXISTS strategy_health_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    sharpe_ratio REAL,
    trade_count INTEGER,
    win_rate REAL,
    avg_pnl_pct REAL,
    profit_factor REAL,
    max_drawdown_pct REAL,
    snapshot_date DATE NOT NULL,
    UNIQUE(strategy, snapshot_date)
);

-- Indexes for fast queries
CREATE INDEX IF NOT EXISTS idx_strategy_returns_strategy_time
    ON strategy_returns(strategy, timestamp);

CREATE INDEX IF NOT EXISTS idx_strategy_returns_timestamp
    ON strategy_returns(timestamp);

CREATE INDEX IF NOT EXISTS idx_strategy_correlations_pair
    ON strategy_correlations(strategy_a, strategy_b, calculated_at);

CREATE INDEX IF NOT EXISTS idx_strategy_health_date
    ON strategy_health_snapshots(strategy, snapshot_date);
