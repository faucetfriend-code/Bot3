-- Migration: 001_refactor_foundation.sql
-- Phase 1: Add tables for Grid Lifecycle Manager, Market Regime Detector, and Authoritative Risk Manager
-- Created: 2026-01-15
-- Description: Adds database support for the new layered architecture components

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
    regime TEXT NOT NULL,            -- Market regime classification
    adx_value REAL,                  -- ADX indicator value
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

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_grid_state_symbol ON grid_state(symbol);
CREATE INDEX IF NOT EXISTS idx_grid_state_status ON grid_state(status);
CREATE INDEX IF NOT EXISTS idx_grid_levels_grid_id ON grid_levels(grid_id);
CREATE INDEX IF NOT EXISTS idx_regime_history_symbol_time ON regime_history(symbol, detected_at);
CREATE INDEX IF NOT EXISTS idx_capital_approvals_symbol ON capital_approvals(symbol);

-- Add funding_pnl column to positions table if it doesn't exist
ALTER TABLE positions ADD COLUMN funding_pnl REAL DEFAULT 0;

-- Add account_id columns to existing tables if they don't exist
ALTER TABLE trades ADD COLUMN account_id TEXT NOT NULL DEFAULT 'sub_1';
ALTER TABLE positions ADD COLUMN account_id TEXT NOT NULL DEFAULT 'sub_1';
ALTER TABLE signals ADD COLUMN account_id TEXT NOT NULL DEFAULT 'sub_1';
ALTER TABLE performance_metrics ADD COLUMN account_id TEXT NOT NULL DEFAULT 'sub_1';

-- Migration complete marker
INSERT OR IGNORE INTO performance_metrics (account_id, date, total_pnl)
VALUES ('system', date('now'), 0);