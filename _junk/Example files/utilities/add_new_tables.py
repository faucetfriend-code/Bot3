#!/usr/bin/env python3
"""
Add new tables to trading_bot.db for market history and balance tracking features.
"""

import sqlite3
from database import DATABASE_PATH


def add_new_tables():
    """Add market_info_history, market_parameter_changes, market_availability_history, and balance_history tables."""

    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    # Create market_info_history table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS market_info_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            tick_size TEXT,
            min_tick TEXT,
            max_tick TEXT,
            lot_size TEXT,
            max_leverage INTEGER,
            isolated_only BOOLEAN DEFAULT FALSE,
            min_order_size TEXT,
            max_order_size TEXT,
            funding_rate TEXT,
            next_funding_rate TEXT,
            created_at BIGINT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Create market_parameter_changes table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS market_parameter_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            parameter_name TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            change_type TEXT NOT NULL,
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Create market_availability_history table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS market_availability_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            is_available BOOLEAN NOT NULL DEFAULT TRUE,
            status_changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            reason TEXT
        )
    """
    )

    # Create balance_history table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS balance_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            subaccount_id TEXT,
            balance REAL NOT NULL,
            equity REAL NOT NULL,
            available_balance REAL NOT NULL,
            margin_used REAL NOT NULL,
            timestamp BIGINT NOT NULL,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Create deposits_withdrawals table
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS deposits_withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id TEXT NOT NULL,
            subaccount_id TEXT,
            amount REAL NOT NULL,
            balance_before REAL,
            balance_after REAL,
            equity_before REAL,
            equity_after REAL,
            timestamp BIGINT NOT NULL,
            detected BOOLEAN DEFAULT FALSE,
            source TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    # Create indexes
    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_info_symbol_time
        ON market_info_history(symbol, fetched_at DESC)
    """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_changes_symbol
        ON market_parameter_changes(symbol, changed_at DESC)
    """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_changes_parameter
        ON market_parameter_changes(parameter_name, changed_at DESC)
    """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_market_availability_symbol
        ON market_availability_history(symbol, status_changed_at DESC)
    """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_balance_history_account_time
        ON balance_history(account_id, timestamp)
    """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_balance_history_subaccount_time
        ON balance_history(subaccount_id, timestamp)
    """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_deposits_withdrawals_account_time
        ON deposits_withdrawals(account_id, timestamp)
    """
    )

    cursor.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_deposits_withdrawals_subaccount_time
        ON deposits_withdrawals(subaccount_id, timestamp)
    """
    )

    conn.commit()
    conn.close()

    print("Successfully created new tables:")
    print("   - market_info_history")
    print("   - market_parameter_changes")
    print("   - market_availability_history")
    print("   - balance_history")
    print("   - deposits_withdrawals")
    print("   - All indexes created")


if __name__ == "__main__":
    add_new_tables()
