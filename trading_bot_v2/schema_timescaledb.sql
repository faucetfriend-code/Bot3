-- ============================================================================
-- Trading Bot PostgreSQL + TimescaleDB Schema
-- Plan 02-01 Task 1: Hypertable schema with compression policies
-- ============================================================================
--
-- This schema replaces the SQLite schema (schema.sql) when DATABASE_BACKEND=postgres.
-- It uses TimescaleDB hypertables for efficient time-series storage and querying.
--
-- DESIGN DECISIONS:
--   - All timestamps use TIMESTAMPTZ (timezone-aware) for correctness across DST
--   - All numeric values use DOUBLE PRECISION for financial precision
--   - JSONB used for flexible indicator/metadata storage (indexed, queryable)
--   - Hypertables partitioned by time for automatic data lifecycle management
--   - Compression policies applied to older data to reduce storage costs
--   - Continuous aggregates for common dashboard queries
--   - UNIQUE constraints use partial indexes (hypertables don't support inline UNIQUE)
--   - Foreign keys are intentionally omitted on hypertables for performance;
--     referential integrity is enforced at the application layer
--
-- MIGRATION NOTES:
--   - SQLite AUTOINCREMENT -> PostgreSQL SERIAL/BIGSERIAL
--   - SQLite REAL -> PostgreSQL DOUBLE PRECISION
--   - SQLite TEXT -> PostgreSQL TEXT
--   - SQLite BOOLEAN -> PostgreSQL BOOLEAN
--   - SQLite TIMESTAMP -> PostgreSQL TIMESTAMPTZ
--   - SQLite JSON (TEXT) -> PostgreSQL JSONB
--   - SQLite INSERT OR REPLACE -> PostgreSQL INSERT ... ON CONFLICT DO UPDATE
-- ============================================================================

-- ============================================================================
-- Enable required extensions
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS pgcrypto;        -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS btree_gist;      -- GiST index support

-- ============================================================================
-- CORE TRADING TABLES
-- ============================================================================

-- -------------------------
-- trades: Complete trade history
-- -------------------------
-- Stores every trade lifecycle event from open to close.
-- Partitioned by entry_time for efficient time-range queries.
CREATE TABLE IF NOT EXISTS trades (
    id              BIGSERIAL,
    account_id      TEXT NOT NULL DEFAULT 'sub_1',
    symbol          TEXT NOT NULL,
    asset_class     TEXT NOT NULL DEFAULT 'crypto',
    side            TEXT NOT NULL,           -- 'long' or 'short'
    quantity        DOUBLE PRECISION NOT NULL,
    entry_price     DOUBLE PRECISION NOT NULL,
    exit_price      DOUBLE PRECISION,
    entry_time      TIMESTAMPTZ NOT NULL,
    exit_time       TIMESTAMPTZ,
    pnl             DOUBLE PRECISION DEFAULT 0,
    commission      DOUBLE PRECISION DEFAULT 0,
    strategy        TEXT,
    status          TEXT NOT NULL DEFAULT 'open',  -- 'open', 'closed', 'cancelled'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Convert to hypertable partitioned by entry_time
-- chunk_time_interval: 7 days (balances write performance vs query efficiency)
SELECT create_hypertable(
    'trades',
    by_range('entry_time', INTERVAL '7 days'),
    if_not_exists => TRUE
);

-- Add metadata columns (not part of hypertable partitioning)
-- Note: account_id index for multi-tenant queries
CREATE INDEX IF NOT EXISTS idx_trades_account_symbol
    ON trades (account_id, symbol, entry_time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_status_entry
    ON trades (status, entry_time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_strategy
    ON trades (strategy, entry_time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_symbol_time
    ON trades (symbol, entry_time DESC);

-- -------------------------
-- positions: Active/open positions
-- -------------------------
-- Current positions (not time-series, but included for completeness).
-- This is a regular table (not hypertable) since positions are stateful.
CREATE TABLE IF NOT EXISTS positions (
    id              BIGSERIAL PRIMARY KEY,
    account_id      TEXT NOT NULL DEFAULT 'sub_1',
    symbol          TEXT NOT NULL,
    asset_class     TEXT NOT NULL DEFAULT 'crypto',
    side            TEXT NOT NULL,           -- 'long' or 'short'
    quantity        DOUBLE PRECISION NOT NULL,
    entry_price     DOUBLE PRECISION NOT NULL,
    current_price   DOUBLE PRECISION,
    unrealized_pnl  DOUBLE PRECISION DEFAULT 0,
    funding_pnl     DOUBLE PRECISION DEFAULT 0,
    exit_price      DOUBLE PRECISION,
    realized_pnl    DOUBLE PRECISION DEFAULT 0,
    opened_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at       TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (account_id, symbol, side)
);

CREATE INDEX IF NOT EXISTS idx_positions_account_symbol
    ON positions (account_id, symbol);
CREATE INDEX IF NOT EXISTS idx_positions_status
    ON positions (updated_at DESC);

-- -------------------------
-- market_data: OHLCV candle data
-- -------------------------
-- High-frequency candle data (1m, 5m, 15m, 1h, 4h, 1d).
-- This is the largest table by volume - hypertable with compression is critical.
-- Chunk interval: 1 day (candles are dense and queried by time range)
CREATE TABLE IF NOT EXISTS market_data (
    id              BIGSERIAL,
    symbol          TEXT NOT NULL,
    "timestamp"     TIMESTAMPTZ NOT NULL,
    open            DOUBLE PRECISION NOT NULL,
    high            DOUBLE PRECISION NOT NULL,
    low             DOUBLE PRECISION NOT NULL,
    close           DOUBLE PRECISION NOT NULL,
    volume          DOUBLE PRECISION NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'api',  -- 'api', 'ws', 'backfill'
    timeframe       TEXT NOT NULL DEFAULT '1m',   -- '1m', '5m', '15m', '1h', '4h', '1d'
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable(
    'market_data',
    by_range('timestamp', INTERVAL '1 day'),
    if_not_exists => TRUE
);

-- Composite index for the most common query: get candles for a symbol in a time range
CREATE INDEX IF NOT EXISTS idx_market_data_symbol_time_tf
    ON market_data (symbol, "timestamp" DESC, timeframe);
CREATE INDEX IF NOT EXISTS idx_market_data_time
    ON market_data ("timestamp" DESC);
CREATE INDEX IF NOT EXISTS idx_market_data_symbol_tf
    ON market_data (symbol, timeframe);

-- Unique constraint: prevent duplicate candles per symbol/timeframe/timestamp
CREATE UNIQUE INDEX IF NOT EXISTS uq_market_data_symbol_tf_ts
    ON market_data (symbol, timeframe, "timestamp");

-- -------------------------
-- signals: Strategy signal logs
-- -------------------------
-- Every signal generated by every strategy, with full indicator snapshot.
-- JSONB indicators allow flexible querying without schema changes.
-- Chunk interval: 1 day
CREATE TABLE IF NOT EXISTS signals (
    id              BIGSERIAL,
    account_id      TEXT NOT NULL DEFAULT 'sub_1',
    symbol          TEXT NOT NULL,
    asset_class     TEXT NOT NULL DEFAULT 'crypto',
    strategy        TEXT NOT NULL,           -- strategy name
    signal_type     TEXT NOT NULL,           -- 'buy', 'sell', 'hold', 'close'
    side            TEXT,                    -- 'long', 'short', NULL
    confidence      DOUBLE PRECISION NOT NULL DEFAULT 0,  -- 0.0 to 1.0
    strength        DOUBLE PRECISION NOT NULL DEFAULT 0,  -- alias for confidence
    indicators      JSONB DEFAULT '{}',      -- full indicator snapshot
    timeframe       TEXT,                    -- signal timeframe
    regime          TEXT,                    -- market regime at signal time
    executed        BOOLEAN NOT NULL DEFAULT FALSE,
    execution_price DOUBLE PRECISION,
    "timestamp"     TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable(
    'signals',
    by_range('timestamp', INTERVAL '1 day'),
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_signals_strategy_time
    ON signals (strategy, "timestamp" DESC);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_time
    ON signals (symbol, "timestamp" DESC);
CREATE INDEX IF NOT EXISTS idx_signals_type_time
    ON signals (signal_type, "timestamp" DESC);
CREATE INDEX IF NOT EXISTS idx_signals_executed
    ON signals (executed, "timestamp" DESC);
-- GIN index for JSONB indicator queries (e.g., find signals where RSI < 30)
CREATE INDEX IF NOT EXISTS idx_signals_indicators
    ON signals USING GIN (indicators);

-- ============================================================================
-- PACIFICA-SPECIFIC TABLES
-- ============================================================================

-- -------------------------
-- funding_payments: Hourly funding payment records
-- -------------------------
-- Pacifica charges/credits funding every hour (24x/day).
-- This is high-volume time-series data ideal for hypertables.
-- Chunk interval: 1 day
CREATE TABLE IF NOT EXISTS funding_payments (
    id              BIGSERIAL,
    account_id      TEXT NOT NULL DEFAULT 'sub_1',
    subaccount_id   TEXT,
    position_id     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    funding_rate    DOUBLE PRECISION NOT NULL,
    payment_amount  DOUBLE PRECISION NOT NULL,
    position_value  DOUBLE PRECISION NOT NULL,
    margin_mode     TEXT NOT NULL DEFAULT 'cross',
    "timestamp"     TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable(
    'funding_payments',
    by_range('timestamp', INTERVAL '1 day'),
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_funding_payments_symbol_time
    ON funding_payments (symbol, "timestamp" DESC);
CREATE INDEX IF NOT EXISTS idx_funding_payments_account_time
    ON funding_payments (account_id, "timestamp" DESC);
CREATE INDEX IF NOT EXISTS idx_funding_payments_position
    ON funding_payments (position_id, "timestamp" DESC);

-- -------------------------
-- funding_rate_history: Historical funding rates
-- -------------------------
-- Hourly snapshots of funding rates for all tracked symbols.
-- Used for funding arbitrage strategy analysis.
-- Chunk interval: 1 day
CREATE TABLE IF NOT EXISTS funding_rate_history (
    id              BIGSERIAL,
    symbol          TEXT NOT NULL,
    funding_rate    DOUBLE PRECISION NOT NULL,
    premium_index   DOUBLE PRECISION,
    interest_rate   DOUBLE PRECISION,
    "timestamp"     TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable(
    'funding_rate_history',
    by_range('timestamp', INTERVAL '1 day'),
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_funding_rate_history_symbol_time
    ON funding_rate_history (symbol, "timestamp" DESC);

-- Unique constraint: one rate per symbol per timestamp
CREATE UNIQUE INDEX IF NOT EXISTS uq_funding_rate_history_symbol_ts
    ON funding_rate_history (symbol, "timestamp");

-- -------------------------
-- pacifica_positions: Enhanced positions with funding tracking
-- -------------------------
-- Pacifica-specific position data with margin and funding details.
-- Regular table (stateful, not time-series).
CREATE TABLE IF NOT EXISTS pacifica_positions (
    id                      BIGSERIAL PRIMARY KEY,
    account_id              TEXT NOT NULL DEFAULT 'sub_1',
    subaccount_id           TEXT,
    position_id             TEXT NOT NULL UNIQUE,
    symbol                  TEXT NOT NULL,
    side                    TEXT NOT NULL,
    size                    DOUBLE PRECISION NOT NULL,
    entry_price             DOUBLE PRECISION NOT NULL,
    current_price           DOUBLE PRECISION,
    leverage                INTEGER NOT NULL DEFAULT 10,
    margin_mode             TEXT NOT NULL DEFAULT 'cross',
    margin_used             DOUBLE PRECISION NOT NULL,
    cumulative_funding_paid DOUBLE PRECISION DEFAULT 0.0,
    last_funding_timestamp  TIMESTAMPTZ,
    unrealized_pnl          DOUBLE PRECISION DEFAULT 0.0,
    liquidation_price       DOUBLE PRECISION,
    tick_size               DOUBLE PRECISION,
    lot_size                DOUBLE PRECISION,
    opened_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at               TIMESTAMPTZ,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pacifica_positions_symbol
    ON pacifica_positions (symbol);
CREATE INDEX IF NOT EXISTS idx_pacifica_positions_account
    ON pacifica_positions (account_id, symbol);

-- -------------------------
-- subaccount_configs: Strategy and risk parameters per subaccount
-- -------------------------
-- Configuration table (not time-series).
CREATE TABLE IF NOT EXISTS subaccount_configs (
    subaccount_id           TEXT PRIMARY KEY,
    subaccount_name         TEXT NOT NULL,
    subaccount_public_key   TEXT,
    trading_strategy        TEXT NOT NULL DEFAULT 'balanced',
    max_position_size       DOUBLE PRECISION NOT NULL DEFAULT 10000.0,
    risk_per_trade          DOUBLE PRECISION NOT NULL DEFAULT 0.02,
    max_leverage            INTEGER NOT NULL DEFAULT 20,
    enabled                 BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- PERFORMANCE & METRICS TABLES
-- ============================================================================

-- -------------------------
-- performance_metrics: Daily performance snapshots
-- -------------------------
-- End-of-day performance metrics for dashboarding and analysis.
-- Chunk interval: 30 days (one row per day per account)
CREATE TABLE IF NOT EXISTS performance_metrics (
    id              BIGSERIAL,
    account_id      TEXT NOT NULL DEFAULT 'sub_1',
    metric_date     DATE NOT NULL,
    total_pnl       DOUBLE PRECISION DEFAULT 0,
    win_rate        DOUBLE PRECISION DEFAULT 0,
    total_trades    INTEGER DEFAULT 0,
    winning_trades  INTEGER DEFAULT 0,
    losing_trades   INTEGER DEFAULT 0,
    avg_rrr         DOUBLE PRECISION DEFAULT 0,
    max_drawdown    DOUBLE PRECISION DEFAULT 0,
    sharpe_ratio    DOUBLE PRECISION,
    sortino_ratio   DOUBLE PRECISION,
    calmar_ratio    DOUBLE PRECISION,
    profit_factor   DOUBLE PRECISION,
    avg_win         DOUBLE PRECISION DEFAULT 0,
    avg_loss        DOUBLE PRECISION DEFAULT 0,
    tags            JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable(
    'performance_metrics',
    by_range('metric_date', INTERVAL '30 days'),
    if_not_exists => TRUE
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_performance_metrics_account_date
    ON performance_metrics (account_id, metric_date);

CREATE INDEX IF NOT EXISTS idx_performance_metrics_date
    ON performance_metrics (metric_date DESC);

-- -------------------------
-- performance_snapshots: Real-time performance snapshots
-- -------------------------
-- High-frequency snapshots for live dashboard (PnL, equity curve, etc.).
-- Chunk interval: 1 hour
CREATE TABLE IF NOT EXISTS performance_snapshots (
    id              BIGSERIAL,
    account_id      TEXT NOT NULL DEFAULT 'sub_1',
    snapshot_time   TIMESTAMPTZ NOT NULL,
    equity          DOUBLE PRECISION NOT NULL,
    balance         DOUBLE PRECISION NOT NULL,
    unrealized_pnl  DOUBLE PRECISION DEFAULT 0,
    realized_pnl    DOUBLE PRECISION DEFAULT 0,
    margin_used     DOUBLE PRECISION DEFAULT 0,
    open_positions  INTEGER DEFAULT 0,
    open_orders     INTEGER DEFAULT 0,
    win_rate        DOUBLE PRECISION,
    sharpe_ratio    DOUBLE PRECISION,
    max_drawdown    DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable(
    'performance_snapshots',
    by_range('snapshot_time', INTERVAL '1 hour'),
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_perf_snapshots_account_time
    ON performance_snapshots (account_id, snapshot_time DESC);

-- -------------------------
-- balance_history: Account balance over time
-- -------------------------
-- Tracks balance/equity changes for equity curve analysis.
-- Chunk interval: 1 day
CREATE TABLE IF NOT EXISTS balance_history (
    id                  BIGSERIAL,
    account_id          TEXT NOT NULL,
    subaccount_id       TEXT,
    balance             DOUBLE PRECISION NOT NULL,
    equity              DOUBLE PRECISION NOT NULL,
    available_balance   DOUBLE PRECISION NOT NULL,
    margin_used         DOUBLE PRECISION NOT NULL,
    "timestamp"         TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable(
    'balance_history',
    by_range('timestamp', INTERVAL '1 day'),
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_balance_history_account_time
    ON balance_history (account_id, "timestamp" DESC);

-- ============================================================================
-- GRID TRADING TABLES
-- ============================================================================

-- -------------------------
-- grid_state: Active grid configuration
-- -------------------------
-- Regular table (stateful, not time-series).
CREATE TABLE IF NOT EXISTS grid_state (
    id                      BIGSERIAL PRIMARY KEY,
    symbol                  TEXT NOT NULL UNIQUE,
    grid_capital            DOUBLE PRECISION NOT NULL,
    center_price            DOUBLE PRECISION,
    initial_center          DOUBLE PRECISION,
    emergency_stop_price    DOUBLE PRECISION NOT NULL,
    active_levels           INTEGER DEFAULT 0,
    total_levels            INTEGER DEFAULT 0,
    orders_placed           INTEGER DEFAULT 0,
    refresh_count           INTEGER DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'active',
    atr_at_creation         DOUBLE PRECISION,
    grid_spacing            DOUBLE PRECISION,
    realized_pnl            DOUBLE PRECISION DEFAULT 0,
    total_fees              DOUBLE PRECISION DEFAULT 0,
    last_refresh            TIMESTAMPTZ,
    consistency_checked_at  TIMESTAMPTZ,
    repair_history          JSONB,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at               TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_grid_state_status
    ON grid_state (status);

-- -------------------------
-- grid_levels: Individual grid orders
-- -------------------------
CREATE TABLE IF NOT EXISTS grid_levels (
    id                  BIGSERIAL PRIMARY KEY,
    grid_id             BIGINT NOT NULL,
    level_number        INTEGER NOT NULL,
    side                TEXT NOT NULL,
    price               DOUBLE PRECISION NOT NULL,
    quantity            DOUBLE PRECISION NOT NULL,
    capital_allocated   DOUBLE PRECISION NOT NULL,
    order_id            TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_grid_levels_grid_id
    ON grid_levels (grid_id);
CREATE INDEX IF NOT EXISTS idx_grid_levels_status
    ON grid_levels (status);
CREATE INDEX IF NOT EXISTS idx_grid_levels_order_id
    ON grid_levels (order_id);

-- -------------------------
-- grid_events: Grid lifecycle events (time-series)
-- -------------------------
-- Records every grid event (creation, fill, refresh, emergency) for auditing.
-- Chunk interval: 7 days
CREATE TABLE IF NOT EXISTS grid_events (
    id              BIGSERIAL,
    grid_id         BIGINT NOT NULL,
    symbol          TEXT NOT NULL,
    event_type      TEXT NOT NULL,           -- 'created', 'filled', 'refreshed', 'emergency', 'closed'
    event_data      JSONB DEFAULT '{}',
    "timestamp"     TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable(
    'grid_events',
    by_range('timestamp', INTERVAL '7 days'),
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_grid_events_grid_time
    ON grid_events (grid_id, "timestamp" DESC);
CREATE INDEX IF NOT EXISTS idx_grid_events_symbol_time
    ON grid_events (symbol, "timestamp" DESC);

-- ============================================================================
-- MARKET INFORMATION TABLES
-- ============================================================================

-- -------------------------
-- regime_history: Market regime changes over time
-- -------------------------
-- Chunk interval: 1 day
CREATE TABLE IF NOT EXISTS regime_history (
    id                  BIGSERIAL,
    symbol              TEXT NOT NULL,
    regime              TEXT NOT NULL,
    adx_value           DOUBLE PRECISION,
    volatility_score    DOUBLE PRECISION,
    confidence          DOUBLE PRECISION,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    duration_minutes    INTEGER
);

SELECT create_hypertable(
    'regime_history',
    by_range('detected_at', INTERVAL '1 day'),
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_regime_history_symbol_time
    ON regime_history (symbol, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_regime_history_regime
    ON regime_history (regime, detected_at DESC);

-- -------------------------
-- market_info_history: Market parameter snapshots
-- -------------------------
-- Chunk interval: 1 day
CREATE TABLE IF NOT EXISTS market_info_history (
    id                  BIGSERIAL,
    symbol              TEXT NOT NULL,
    tick_size           TEXT,
    min_tick            TEXT,
    max_tick            TEXT,
    lot_size            TEXT,
    max_leverage        INTEGER,
    isolated_only       BOOLEAN DEFAULT FALSE,
    min_order_size      TEXT,
    max_order_size      TEXT,
    funding_rate        TEXT,
    next_funding_rate   TEXT,
    raw_data            JSONB,                -- full API response for future use
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable(
    'market_info_history',
    by_range('fetched_at', INTERVAL '1 day'),
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_market_info_symbol_time
    ON market_info_history (symbol, fetched_at DESC);

-- -------------------------
-- market_parameter_changes: Tracks changes in market parameters
-- -------------------------
-- Chunk interval: 7 days
CREATE TABLE IF NOT EXISTS market_parameter_changes (
    id              BIGSERIAL,
    symbol          TEXT NOT NULL,
    parameter_name  TEXT NOT NULL,
    old_value       TEXT,
    new_value       TEXT,
    change_type     TEXT NOT NULL,           -- 'added', 'removed', 'modified'
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable(
    'market_parameter_changes',
    by_range('changed_at', INTERVAL '7 days'),
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_market_changes_symbol
    ON market_parameter_changes (symbol, changed_at DESC);

-- -------------------------
-- market_availability_history: Market availability tracking
-- -------------------------
CREATE TABLE IF NOT EXISTS market_availability_history (
    id                  BIGSERIAL,
    symbol              TEXT NOT NULL,
    is_available        BOOLEAN NOT NULL DEFAULT TRUE,
    status_changed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason              TEXT
);

-- ============================================================================
-- RISK MANAGEMENT TABLES
-- ============================================================================

-- -------------------------
-- capital_approvals: Audit trail for capital allocation
-- -------------------------
CREATE TABLE IF NOT EXISTS capital_approvals (
    id                  BIGSERIAL PRIMARY KEY,
    approval_id         TEXT NOT NULL UNIQUE,
    symbol              TEXT NOT NULL,
    strategy            TEXT NOT NULL,
    requested_amount    DOUBLE PRECISION NOT NULL,
    allocated_amount    DOUBLE PRECISION NOT NULL,
    account_balance     DOUBLE PRECISION NOT NULL,
    current_exposure    DOUBLE PRECISION NOT NULL,
    status              TEXT NOT NULL DEFAULT 'approved',
    approved_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    used_at             TIMESTAMPTZ,
    actual_usage        DOUBLE PRECISION,
    revoked_at          TIMESTAMPTZ,
    revocation_reason   TEXT
);

CREATE INDEX IF NOT EXISTS idx_capital_approvals_symbol
    ON capital_approvals (symbol);
CREATE INDEX IF NOT EXISTS idx_capital_approvals_status
    ON capital_approvals (status, approved_at DESC);

-- ============================================================================
-- ACCOUNT PROFILES
-- ============================================================================

-- Regular table (stateful, small volume).
CREATE TABLE IF NOT EXISTS account_profiles (
    id                      BIGSERIAL PRIMARY KEY,
    name                    TEXT NOT NULL UNIQUE,
    private_key_encrypted   TEXT NOT NULL,
    public_key              TEXT,
    is_default              BOOLEAN DEFAULT FALSE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- COMPRESSION POLICIES
-- ============================================================================
-- TimescaleDB native compression reduces storage by 90%+ for time-series data.
-- Data is compressed automatically after the specified interval.
-- Compressed data is still queryable (transparent to application code).

-- Compress market_data after 3 days (largest table, most benefit)
SELECT add_compression_policy(
    'market_data',
    compress_after => INTERVAL '3 days',
    if_not_exists => TRUE
);

-- Compress trades after 7 days
SELECT add_compression_policy(
    'trades',
    compress_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- Compress signals after 3 days (high volume, rarely queried for recent data)
SELECT add_compression_policy(
    'signals',
    compress_after => INTERVAL '3 days',
    if_not_exists => TRUE
);

-- Compress funding_payments after 7 days
SELECT add_compression_policy(
    'funding_payments',
    compress_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- Compress funding_rate_history after 7 days
SELECT add_compression_policy(
    'funding_rate_history',
    compress_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- Compress performance_snapshots after 1 day (high frequency, older data is summary only)
SELECT add_compression_policy(
    'performance_snapshots',
    compress_after => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Compress balance_history after 7 days
SELECT add_compression_policy(
    'balance_history',
    compress_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- Compress regime_history after 30 days (lower volume, but still benefit)
SELECT add_compression_policy(
    'regime_history',
    compress_after => INTERVAL '30 days',
    if_not_exists => TRUE
);

-- Compress market_info_history after 30 days
SELECT add_compression_policy(
    'market_info_history',
    compress_after => INTERVAL '30 days',
    if_not_exists => TRUE
);

-- Compress market_parameter_changes after 30 days
SELECT add_compression_policy(
    'market_parameter_changes',
    compress_after => INTERVAL '30 days',
    if_not_exists => TRUE
);

-- Compress grid_events after 7 days
SELECT add_compression_policy(
    'grid_events',
    compress_after => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- ============================================================================
-- DATA RETENTION POLICIES
-- ============================================================================
-- Automatically drop old data beyond retention period.
-- This is a safety net; the application should manage primary retention.

-- Drop market_data older than 1 year (backtesting data is separate)
SELECT add_retention_policy(
    'market_data',
    drop_after => INTERVAL '1 year',
    if_not_exists => TRUE
);

-- Drop signals older than 6 months
SELECT add_retention_policy(
    'signals',
    drop_after => INTERVAL '6 months',
    if_not_exists => TRUE
);

-- Drop funding_payments older than 1 year
SELECT add_retention_policy(
    'funding_payments',
    drop_after => INTERVAL '1 year',
    if_not_exists => TRUE
);

-- Drop performance_snapshots older than 30 days (daily snapshots are in performance_metrics)
SELECT add_retention_policy(
    'performance_snapshots',
    drop_after => INTERVAL '30 days',
    if_not_exists => TRUE
);

-- Drop balance_history older than 1 year
SELECT add_retention_policy(
    'balance_history',
    drop_after => INTERVAL '1 year',
    if_not_exists => TRUE
);

-- ============================================================================
-- CONTINUOUS AGGREGATES
-- ============================================================================
-- Pre-computed materialized views that update automatically.
-- These dramatically speed up common dashboard queries.

-- -------------------------
-- 1-hour OHLCV candles (aggregated from 1-minute data)
-- -------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS candles_1h
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', "timestamp") AS bucket,
    symbol,
    first(open, "timestamp") AS open,
    max(high) AS high,
    min(low) AS low,
    last(close, "timestamp") AS close,
    sum(volume) AS volume,
    count(*) AS candle_count
FROM market_data
WHERE timeframe = '1m'
GROUP BY bucket, symbol
WITH NO DATA;

-- Refresh policy: update the continuous aggregate every 15 minutes
SELECT add_continuous_aggregate_policy('candles_1h',
    start_offset => INTERVAL '3 hours',
    end_offset => INTERVAL '15 minutes',
    schedule_interval => INTERVAL '15 minutes',
    if_not_exists => TRUE
);

-- -------------------------
-- Daily P&L summary (aggregated from trades)
-- -------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS daily_pnl_summary
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', entry_time) AS bucket,
    account_id,
    strategy,
    count(*) AS total_trades,
    count(*) FILTER (WHERE pnl > 0) AS winning_trades,
    count(*) FILTER (WHERE pnl <= 0) AS losing_trades,
    sum(pnl) AS total_pnl,
    avg(pnl) AS avg_pnl,
    max(pnl) AS best_trade,
    min(pnl) AS worst_trade,
    CASE
        WHEN count(*) > 0 THEN
            round((count(*) FILTER (WHERE pnl > 0)::numeric / count(*)::numeric) * 100, 2)
        ELSE 0
    END AS win_rate_pct
FROM trades
WHERE status = 'closed'
GROUP BY bucket, account_id, strategy
WITH NO DATA;

SELECT add_continuous_aggregate_policy('daily_pnl_summary',
    start_offset => INTERVAL '7 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- -------------------------
-- Hourly funding summary (aggregated from funding_payments)
-- -------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS hourly_funding_summary
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', "timestamp") AS bucket,
    account_id,
    symbol,
    sum(payment_amount) AS total_funding,
    avg(funding_rate) AS avg_rate,
    min(funding_rate) AS min_rate,
    max(funding_rate) AS max_rate,
    count(*) AS payment_count
FROM funding_payments
GROUP BY bucket, account_id, symbol
WITH NO DATA;

SELECT add_continuous_aggregate_policy('hourly_funding_summary',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- ============================================================================
-- HELPER VIEWS
-- ============================================================================

-- View: Latest price for each symbol
CREATE OR REPLACE VIEW v_latest_prices AS
SELECT DISTINCT ON (symbol)
    symbol,
    close AS latest_price,
    volume AS latest_volume,
    "timestamp" AS last_update
FROM market_data
ORDER BY symbol, "timestamp" DESC;

-- View: Open positions with current P&L
CREATE OR REPLACE VIEW v_open_positions AS
SELECT
    p.*,
    md.close AS current_market_price,
    CASE
        WHEN p.side = 'long' THEN (md.close - p.entry_price) * p.quantity
        WHEN p.side = 'short' THEN (p.entry_price - md.close) * p.quantity
        ELSE 0
    END AS calculated_pnl
FROM positions p
LEFT JOIN LATERAL (
    SELECT close
    FROM market_data
    WHERE symbol = p.symbol
    ORDER BY "timestamp" DESC
    LIMIT 1
) md ON TRUE
WHERE p.closed_at IS NULL;

-- View: Strategy performance comparison
CREATE OR REPLACE VIEW v_strategy_performance AS
SELECT
    strategy,
    count(*) AS total_trades,
    count(*) FILTER (WHERE pnl > 0) AS wins,
    count(*) FILTER (WHERE pnl <= 0) AS losses,
    round(avg(pnl)::numeric, 4) AS avg_pnl,
    round(sum(pnl)::numeric, 4) AS total_pnl,
    round(
        CASE WHEN count(*) > 0 THEN
            (count(*) FILTER (WHERE pnl > 0))::numeric / count(*)::numeric * 100
        ELSE 0 END,
        2
    ) AS win_rate_pct,
    round(max(pnl)::numeric, 4) AS best_trade,
    round(min(pnl)::numeric, 4) AS worst_trade,
    min(entry_time) AS first_trade,
    max(entry_time) AS last_trade
FROM trades
WHERE status = 'closed'
GROUP BY strategy
ORDER BY total_pnl DESC;

-- ============================================================================
-- SCHEMA VERSION TRACKING
-- ============================================================================
-- Allows the application to detect schema version mismatches.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version         INTEGER PRIMARY KEY,
    description     TEXT,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    checksum        TEXT                    -- hash of the migration SQL
);

-- Record this schema version
INSERT INTO schema_migrations (version, description, checksum)
VALUES (
    1,
    'Initial TimescaleDB schema: hypertables, compression, continuous aggregates',
    encode(digest('schema_timescaledb_v1', 'sha256'), 'hex')
)
ON CONFLICT (version) DO NOTHING;

-- ============================================================================
-- SCHEMA COMPLETE
-- ============================================================================
-- Total hypertables: 10 (market_data, trades, signals, funding_payments,
--   funding_rate_history, performance_metrics, performance_snapshots,
--   balance_history, regime_history, market_info_history,
--   market_parameter_changes, grid_events)
-- Compression policies: 11
-- Retention policies: 5
-- Continuous aggregates: 3 (candles_1h, daily_pnl_summary, hourly_funding_summary)
-- ============================================================================
