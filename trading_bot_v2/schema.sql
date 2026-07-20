-- Trading Bot Database Schema
-- SQLite database for persistent storage

-- Account profiles table (for persistent user credentials)
CREATE TABLE IF NOT EXISTS account_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,  -- Display name for the account
    private_key_encrypted TEXT NOT NULL,  -- Encrypted private key
    public_key TEXT,  -- Optional public key
    is_default BOOLEAN DEFAULT FALSE,  -- Only one can be default
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Core trading tables
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL DEFAULT 'sub_1',  -- Account identifier
    symbol TEXT NOT NULL,
    asset_class TEXT NOT NULL,  -- 'crypto', 'stock', 'forex', 'commodity'
    side TEXT NOT NULL,  -- 'long' or 'short'
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    entry_time TIMESTAMP NOT NULL,
    exit_time TIMESTAMP,
    pnl REAL DEFAULT 0,
    commission REAL DEFAULT 0,
    strategy TEXT,
    status TEXT DEFAULT 'open',  -- 'open', 'closed', 'cancelled'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL DEFAULT 'sub_1',  -- Account identifier
    symbol TEXT NOT NULL,
    asset_class TEXT NOT NULL,  -- 'crypto', 'stock', 'forex', 'commodity'
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    current_price REAL,
    unrealized_pnl REAL DEFAULT 0,
    opened_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_id, symbol, side)
);

CREATE TABLE IF NOT EXISTS market_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    price REAL NOT NULL,
    volume REAL,
    timestamp TIMESTAMP NOT NULL,
    source TEXT DEFAULT 'api'
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL DEFAULT 'sub_1',  -- Account identifier
    symbol TEXT NOT NULL,
    asset_class TEXT NOT NULL,  -- 'crypto', 'stock', 'forex', 'commodity'
    signal_type TEXT NOT NULL,  -- 'buy', 'sell', 'hold'
    strength REAL NOT NULL,  -- 0.0 to 1.0
    indicators TEXT,  -- JSON string of indicator values
    timestamp TIMESTAMP NOT NULL,
    executed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS performance_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL DEFAULT 'sub_1',  -- Account identifier
    date DATE NOT NULL,
    total_pnl REAL DEFAULT 0,
    win_rate REAL DEFAULT 0,
    total_trades INTEGER DEFAULT 0,
    avg_rrr REAL DEFAULT 0,
    max_drawdown REAL DEFAULT 0,
    sharpe_ratio REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(account_id, date)
);

-- ===========================
-- PACIFICA-SPECIFIC TABLES
-- ===========================

-- Subaccount configurations table
-- Stores trading strategy and risk parameters per subaccount
CREATE TABLE IF NOT EXISTS subaccount_configs (
    subaccount_id TEXT PRIMARY KEY,
    subaccount_name TEXT NOT NULL,
    subaccount_public_key TEXT,
    trading_strategy TEXT NOT NULL DEFAULT 'balanced',
    max_position_size REAL NOT NULL DEFAULT 10000.0,
    risk_per_trade REAL NOT NULL DEFAULT 0.02,
    max_leverage INTEGER NOT NULL DEFAULT 20,
    enabled BOOLEAN NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Funding payments table - tracks EVERY hourly payment
-- CRITICAL: Pacifica charges funding 24x per day (hourly)
CREATE TABLE IF NOT EXISTS funding_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL DEFAULT 'sub_1',
    subaccount_id TEXT,
    position_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    funding_rate REAL NOT NULL,
    payment_amount REAL NOT NULL,
    position_value REAL NOT NULL,
    margin_mode TEXT NOT NULL DEFAULT 'cross',
    timestamp DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (subaccount_id) REFERENCES subaccount_configs(subaccount_id)
);

-- Enhanced positions table for Pacifica with funding tracking
CREATE TABLE IF NOT EXISTS pacifica_positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL DEFAULT 'sub_1',
    subaccount_id TEXT,
    position_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    size REAL NOT NULL,
    entry_price REAL NOT NULL,
    current_price REAL,
    leverage INTEGER NOT NULL DEFAULT 10,
    margin_mode TEXT NOT NULL DEFAULT 'cross',
    margin_used REAL NOT NULL,
    cumulative_funding_paid REAL DEFAULT 0.0,
    last_funding_timestamp DATETIME,
    unrealized_pnl REAL DEFAULT 0.0,
    liquidation_price REAL,
    tick_size REAL,
    lot_size REAL,
    opened_at DATETIME NOT NULL,
    closed_at DATETIME,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (subaccount_id) REFERENCES subaccount_configs(subaccount_id)
);

-- Funding rate history table - hourly snapshots
CREATE TABLE IF NOT EXISTS funding_rate_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    funding_rate REAL NOT NULL,
    premium_index REAL,
    interest_rate REAL,
    timestamp DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ===========================
-- INDEXES
-- ===========================

-- Account profiles indexes
CREATE INDEX IF NOT EXISTS idx_account_profiles_name ON account_profiles(name);
CREATE INDEX IF NOT EXISTS idx_account_profiles_default ON account_profiles(is_default);

-- Market data indexes (optimized for time-series queries)
CREATE INDEX IF NOT EXISTS idx_market_data_symbol ON market_data(symbol);
CREATE INDEX IF NOT EXISTS idx_market_data_timestamp ON market_data(timestamp);
CREATE INDEX IF NOT EXISTS idx_market_data_symbol_time ON market_data(symbol, timestamp DESC);

-- Subaccount config indexes
CREATE INDEX IF NOT EXISTS idx_subaccount_configs_strategy ON subaccount_configs(trading_strategy, enabled);

-- Funding payments indexes
CREATE INDEX IF NOT EXISTS idx_funding_payments_position ON funding_payments(position_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_funding_payments_symbol_time ON funding_payments(symbol, timestamp);
CREATE INDEX IF NOT EXISTS idx_funding_payments_account ON funding_payments(account_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_funding_payments_subaccount ON funding_payments(subaccount_id, timestamp);



-- ===========================
-- MARKET INFO HISTORY TABLES
-- ===========================

-- Comprehensive market information history from /info endpoint
CREATE TABLE IF NOT EXISTS market_info_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    tick_size TEXT,  -- Store as text to preserve precision
    min_tick TEXT,
    max_tick TEXT,
    lot_size TEXT,
    max_leverage INTEGER,
    isolated_only BOOLEAN DEFAULT FALSE,
    min_order_size TEXT,
    max_order_size TEXT,
    funding_rate TEXT,
    next_funding_rate TEXT,
    created_at BIGINT,  -- Pacifica timestamp
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Track changes in market parameters
CREATE TABLE IF NOT EXISTS market_parameter_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    parameter_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    change_type TEXT NOT NULL,  -- 'added', 'removed', 'modified'
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Market availability tracking
CREATE TABLE IF NOT EXISTS market_availability_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    is_available BOOLEAN NOT NULL DEFAULT TRUE,
    status_changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reason TEXT  -- 'new_market', 'delisted', 'maintenance', etc.
);

-- ===========================
-- INDEXES (created after all tables)
-- ===========================

-- Pacifica positions indexes
CREATE INDEX IF NOT EXISTS idx_pacifica_positions_symbol ON pacifica_positions(symbol);
CREATE INDEX IF NOT EXISTS idx_pacifica_positions_account ON pacifica_positions(account_id);
CREATE INDEX IF NOT EXISTS idx_pacifica_positions_subaccount ON pacifica_positions(subaccount_id);

-- Funding rate history indexes
CREATE INDEX IF NOT EXISTS idx_funding_history_symbol_time ON funding_rate_history(symbol, timestamp);

-- Market info history indexes
CREATE INDEX IF NOT EXISTS idx_market_info_symbol_time ON market_info_history(symbol, fetched_at DESC);

-- Market parameter changes indexes
CREATE INDEX IF NOT EXISTS idx_market_changes_symbol ON market_parameter_changes(symbol, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_market_changes_parameter ON market_parameter_changes(parameter_name, changed_at DESC);

-- Market availability history indexes
CREATE INDEX IF NOT EXISTS idx_market_availability_symbol ON market_availability_history(symbol, status_changed_at DESC);

-- ===========================
-- BALANCE HISTORY TABLES
-- ===========================

-- Account balance history from Pacifica API
CREATE TABLE IF NOT EXISTS balance_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL,
    subaccount_id TEXT,
    balance REAL NOT NULL,
    equity REAL NOT NULL,
    available_balance REAL NOT NULL,
    margin_used REAL NOT NULL,
    timestamp BIGINT NOT NULL,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (subaccount_id) REFERENCES subaccount_configs(subaccount_id)
);

-- Balance history indexes
CREATE INDEX IF NOT EXISTS idx_balance_history_account_time ON balance_history(account_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_balance_history_subaccount_time ON balance_history(subaccount_id, timestamp);

-- ===========================
-- GRID LIFECYCLE TABLES (Phase 1)
-- ===========================

-- Grid state persistence - single source of truth for grid status
CREATE TABLE IF NOT EXISTS grid_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL UNIQUE,  -- One grid per symbol
    grid_capital REAL NOT NULL,   -- Approved capital allocation
    emergency_stop_price REAL NOT NULL,  -- Emergency exit trigger
    active_levels INTEGER DEFAULT 0,     -- Currently active grid levels
    total_levels INTEGER DEFAULT 0,      -- Total levels in grid
    status TEXT NOT NULL DEFAULT 'active', -- 'active', 'paused', 'emergency_exit', 'closed'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP
);

-- Grid level tracking - individual buy/sell orders in grid
CREATE TABLE IF NOT EXISTS grid_levels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grid_id INTEGER NOT NULL,        -- References grid_state.id
    level_number INTEGER NOT NULL,   -- Level identifier (1, 2, 3...)
    side TEXT NOT NULL,              -- 'buy' or 'sell'
    price REAL NOT NULL,             -- Order price
    quantity REAL NOT NULL,          -- Order quantity
    capital_allocated REAL NOT NULL, -- Capital for this level
    order_id TEXT,                   -- Pacifica order ID when placed
    status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'placed', 'filled', 'cancelled'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (grid_id) REFERENCES grid_state(id) ON DELETE CASCADE
);

-- Regime detection history - track market regime changes
CREATE TABLE IF NOT EXISTS regime_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    regime TEXT NOT NULL,            -- Market regime classification (mirrors new_regime)
    old_regime TEXT,                 -- Regime exited (confirmed transitions)
    new_regime TEXT,                 -- Regime entered (confirmed transitions)
    adx REAL,                        -- ADX at transition detection
    adx_value REAL,                  -- ADX indicator value (legacy alias of adx)
    volatility_score REAL,           -- Volatility percentile
    confidence REAL,                 -- Detection confidence (0-1)
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    duration_minutes INTEGER         -- How long regime lasted
);

-- Capital allocation approvals - audit trail for risk management
CREATE TABLE IF NOT EXISTS capital_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id TEXT NOT NULL UNIQUE, -- Unique approval identifier
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    requested_amount REAL NOT NULL,
    allocated_amount REAL NOT NULL,
    account_balance REAL NOT NULL,
    current_exposure REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'approved', -- 'approved', 'used', 'expired', 'revoked'
    approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    used_at TIMESTAMP,
    actual_usage REAL,
    revoked_at TIMESTAMP,
    revocation_reason TEXT
);

-- ===========================
-- GRID LIFECYCLE INDEXES
-- ===========================

-- Grid state indexes
CREATE INDEX IF NOT EXISTS idx_grid_state_symbol ON grid_state(symbol);
CREATE INDEX IF NOT EXISTS idx_grid_state_status ON grid_state(status);
CREATE INDEX IF NOT EXISTS idx_grid_state_created ON grid_state(created_at);

-- Grid levels indexes
CREATE INDEX IF NOT EXISTS idx_grid_levels_grid_id ON grid_levels(grid_id);
CREATE INDEX IF NOT EXISTS idx_grid_levels_status ON grid_levels(status);
CREATE INDEX IF NOT EXISTS idx_grid_levels_order_id ON grid_levels(order_id);

-- Regime history indexes
CREATE INDEX IF NOT EXISTS idx_regime_history_symbol_time ON regime_history(symbol, detected_at);
CREATE INDEX IF NOT EXISTS idx_regime_history_regime ON regime_history(regime);

-- Capital approvals indexes
CREATE INDEX IF NOT EXISTS idx_capital_approvals_symbol ON capital_approvals(symbol);
CREATE INDEX IF NOT EXISTS idx_capital_approvals_strategy ON capital_approvals(strategy);
CREATE INDEX IF NOT EXISTS idx_capital_approvals_status ON capital_approvals(status);
CREATE INDEX IF NOT EXISTS idx_capital_approvals_approved ON capital_approvals(approved_at);