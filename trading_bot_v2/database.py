"""
Database management for trading bot.
Provides persistent storage with SQLite (default) or PostgreSQL backend.

Supports:
  - SQLite for local development and zero-config deployment
  - PostgreSQL + TimescaleDB for production time-series workloads
  - Transparent SQL translation (? -> %s, INSERT OR REPLACE, etc.)
  - Connection pooling for both backends

⚠️ CRITICAL: Includes hourly funding tracking for Pacifica.fi (24x per day)

Environment variables:
  DATABASE_BACKEND  = "sqlite" | "postgres" (default: "sqlite")
  DATABASE_PATH     = SQLite file path (default: "data/trading_bot.db")
  PG_HOST           = PostgreSQL host (default: "localhost")
  PG_PORT           = PostgreSQL port (default: 5432)
  PG_DATABASE       = PostgreSQL database name (default: "trading_bot")
  PG_USER           = PostgreSQL user (default: "postgres")
  PG_PASSWORD       = PostgreSQL password (default: "")
  PG_SCHEMA         = PostgreSQL schema (default: "public")
  PG_POOL_MIN       = Min pool connections (default: 2)
  PG_POOL_MAX       = Max pool connections (default: 10)
  PG_POOL_TIMEOUT   = Pool connection timeout seconds (default: 30)
"""

import re
import sqlite3
import json
import os
import threading
import time
from contextlib import contextmanager
from typing import Generator, List, Dict, Any, Optional, Tuple, Union
from datetime import datetime, date, timedelta
import logging

logger = logging.getLogger(__name__)

DATABASE_PATH = os.path.abspath(os.getenv("DATABASE_PATH", "data/trading_bot.db"))
DATABASE_BACKEND: str = os.getenv("DATABASE_BACKEND", "sqlite").lower()

# ============================================================================
# SQL Translation Utilities
# ============================================================================
# Transparently converts SQLite SQL to PostgreSQL-compatible SQL.
# Used by the connection wrapper to maintain backward compatibility.


def _translate_placeholders(sql: str) -> str:
    """
    Convert SQLite '?' placeholders to PostgreSQL '%s' placeholders.

    Only replaces ? that are NOT inside quoted strings to avoid
    corrupting string literals.
    """
    # Simple approach: replace ? with %s. This works for all SQL in this codebase
    # because none of the string literals contain bare ? characters.
    return sql.replace("?", "%s")


def _translate_insert_or_replace(sql: str) -> str:
    """
    Convert SQLite INSERT OR REPLACE to PostgreSQL INSERT ... ON CONFLICT.

    Handles the patterns used in this codebase:
      INSERT OR REPLACE INTO table (...) VALUES (...)
    Becomes:
      INSERT INTO table (...) VALUES (...) ON CONFLICT (...) DO UPDATE SET ...
    """
    match = re.match(
        r"INSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES",
        sql,
        re.IGNORECASE,
    )
    if not match:
        return sql

    table_name = match.group(1)
    columns_str = match.group(2)
    columns = [c.strip() for c in columns_str.split(",")]

    # Find the VALUES clause
    values_match = re.search(r"VALUES\s*\(([^)]+)\)", sql, re.IGNORECASE)
    if not values_match:
        return sql

    # Reconstruct as INSERT ... ON CONFLICT ... DO UPDATE
    # Use the first column as the conflict target (usually 'id' or unique constraint)
    # For our codebase, the main OR REPLACE targets are:
    #   - pacifica_positions (UNIQUE on position_id)
    #   - performance_metrics (UNIQUE on account_id, date)
    new_sql = sql[: match.start()]  # Preserve any leading comments/whitespace
    new_sql += f"INSERT INTO {table_name} ({columns_str}) VALUES ({values_match.group(1)}) "

    # Determine conflict columns based on table
    if table_name == "pacifica_positions":
        new_sql += "ON CONFLICT (position_id) DO UPDATE SET "
        update_parts = []
        for col in columns:
            if col != "position_id":
                update_parts.append(f"{col} = EXCLUDED.{col}")
        new_sql += ", ".join(update_parts)
    elif table_name == "performance_metrics":
        new_sql += "ON CONFLICT (account_id, metric_date) DO UPDATE SET "
        update_parts = []
        for col in columns:
            if col not in ("account_id", "metric_date"):
                update_parts.append(f"{col} = EXCLUDED.{col}")
        new_sql += ", ".join(update_parts)
    else:
        # Generic fallback: use all columns except 'id' for conflict update
        new_sql += "ON CONFLICT DO UPDATE SET "
        update_parts = []
        for col in columns:
            if col != "id":
                update_parts.append(f"{col} = EXCLUDED.{col}")
        if update_parts:
            new_sql += ", ".join(update_parts)
        else:
            # No columns to update (just id), do nothing
            new_sql = new_sql.replace("ON CONFLICT DO UPDATE SET", "ON CONFLICT DO NOTHING")

    return new_sql


def _translate_current_timestamp(sql: str) -> str:
    """Convert SQLite CURRENT_TIMESTAMP to PostgreSQL NOW() if needed."""
    # CURRENT_TIMESTAMP works in both SQLite and PostgreSQL, so no change needed.
    return sql


def _translate_sql(sql: str, is_postgres: bool) -> str:
    """Apply all SQL translations for the target backend."""
    if not is_postgres:
        return sql

    sql = _translate_current_timestamp(sql)
    sql = _translate_insert_or_replace(sql)
    sql = _translate_placeholders(sql)
    return sql


def _split_statements(sql: str) -> List[str]:
    """Split a multi-statement SQL string into individual statements."""
    statements = []
    current = []
    in_quote = False
    quote_char = None

    for char in sql:
        if char in ("'", '"') and not in_quote:
            in_quote = True
            quote_char = char
        elif char == quote_char and in_quote:
            in_quote = False
            quote_char = None
        elif char == ";" and not in_quote:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
            continue
        current.append(char)

    # Handle trailing statement without semicolon
    stmt = "".join(current).strip()
    if stmt:
        statements.append(stmt)

    return statements


# ============================================================================
# Connection Wrapper Classes
# ============================================================================
# These wrappers make PostgreSQL connections look like SQLite connections,
# maintaining full backward compatibility with existing code.


class _CursorWrapper:
    """
    Wraps a database cursor to provide a unified interface.

    For PostgreSQL, provides `lastrowid` by fetching the RETURNING result.
    For SQLite, passes through directly.
    """

    def __init__(self, cursor: Any, is_postgres: bool = False):
        self._cursor = cursor
        self._is_postgres = is_postgres
        self._lastrowid: Optional[int] = None
        self._has_returning = False

    def _set_returning_result(self, row: Any) -> None:
        """Store the RETURNING result for lastrowid access."""
        self._has_returning = True
        if row:
            self._lastrowid = int(row[0]) if row[0] is not None else None

    @property
    def lastrowid(self) -> Optional[int]:
        """Return the last inserted row ID."""
        if self._is_postgres and self._has_returning:
            return self._lastrowid
        # For SQLite, try the native attribute
        try:
            return self._cursor.lastrowid
        except AttributeError:
            return None

    @property
    def rowcount(self) -> int:
        """Return the number of rows affected."""
        return self._cursor.rowcount

    @property
    def description(self) -> Any:
        """Return cursor description."""
        return self._cursor.description

    def fetchone(self) -> Any:
        """Fetch the next row."""
        return self._cursor.fetchone()

    def fetchall(self) -> Any:
        """Fetch all remaining rows."""
        return self._cursor.fetchall()

    def fetchmany(self, size: int = None) -> Any:
        """Fetch the next size rows."""
        if size is not None:
            return self._cursor.fetchmany(size)
        return self._cursor.fetchmany()

    def __iter__(self) -> Any:
        """Iterate over cursor results."""
        return iter(self._cursor)

    def __next__(self) -> Any:
        """Get next row from iterator."""
        return next(self._cursor)


class _ConnectionWrapper:
    """
    Wraps a database connection to provide a unified interface.

    For PostgreSQL:
      - Translates ? placeholders to %s
      - Translates INSERT OR REPLACE to ON CONFLICT
      - Translates executescript to individual statements
      - Returns _CursorWrapper with lastrowid support via RETURNING
    """

    def __init__(self, conn: Any, is_postgres: bool = False):
        self._conn = conn
        self._is_postgres = is_postgres
        self._closed = False

    def execute(self, sql: str, params: Optional[Union[tuple, list]] = None) -> _CursorWrapper:
        """
        Execute a SQL statement and return a wrapped cursor.

        Args:
            sql: SQL statement (with ? placeholders for both backends)
            params: Query parameters (tuple or list)

        Returns:
            _CursorWrapper with unified interface
        """
        translated_sql = _translate_sql(sql, self._is_postgres)

        # Convert list params to tuple for compatibility
        if params is not None and isinstance(params, list):
            params = tuple(params)

        if self._is_postgres:
            # Check if this is an INSERT without RETURNING - add RETURNING id
            # to get lastrowid support
            upper_sql = translated_sql.strip().upper()
            if upper_sql.startswith("INSERT ") and "RETURNING" not in upper_sql:
                # Add RETURNING id to get lastrowid
                translated_sql = translated_sql.rstrip().rstrip(";") + " RETURNING id"
                if params:
                    cursor = self._conn.execute(translated_sql, params)
                else:
                    cursor = self._conn.execute(translated_sql)
                wrapped = _CursorWrapper(cursor, is_postgres=True)
                # Fetch the RETURNING result for lastrowid
                row = cursor.fetchone()
                wrapped._set_returning_result(row)
                return wrapped
            else:
                if params:
                    cursor = self._conn.execute(translated_sql, params)
                else:
                    cursor = self._conn.execute(translated_sql)
                return _CursorWrapper(cursor, is_postgres=True)
        else:
            if params:
                cursor = self._conn.execute(translated_sql, params)
            else:
                cursor = self._conn.execute(translated_sql)
            return _CursorWrapper(cursor, is_postgres=False)

    def executemany(self, sql: str, params_list: list) -> _CursorWrapper:
        """
        Execute a SQL statement against multiple parameter sets.

        Args:
            sql: SQL statement (with ? placeholders)
            params_list: List of parameter tuples

        Returns:
            _CursorWrapper with unified interface
        """
        translated_sql = _translate_sql(sql, self._is_postgres)

        if self._is_postgres:
            # executemany doesn't need RETURNING for bulk inserts
            cursor = self._conn.cursor()
            cursor.executemany(translated_sql, params_list)
            return _CursorWrapper(cursor, is_postgres=True)
        else:
            cursor = self._conn.executemany(translated_sql, params_list)
            return _CursorWrapper(cursor, is_postgres=False)

    def executescript(self, sql: str) -> None:
        """
        Execute a multi-statement SQL script.

        For PostgreSQL, splits the script into individual statements.
        For SQLite, uses native executescript.
        """
        if self._is_postgres:
            statements = _split_statements(sql)
            for stmt in statements:
                # Skip PostgreSQL-incompatible SQLite pragmas
                upper_stmt = stmt.upper().strip()
                if upper_stmt.startswith("PRAGMA"):
                    continue
                try:
                    self._conn.execute(stmt)
                except Exception as e:
                    # Check if it's a "table already exists" error
                    if "already exists" in str(e).lower():
                        try:
                            self._conn.rollback()
                        except Exception:
                            pass
                        continue
                    # Log but continue with other statements
                    logger.warning(f"SQL statement failed (continuing): {e}")
                    try:
                        self._conn.rollback()
                    except Exception:
                        pass
        else:
            self._conn.executescript(sql)

    def commit(self) -> None:
        """Commit the current transaction."""
        self._conn.commit()

    def rollback(self) -> None:
        """Rollback the current transaction."""
        self._conn.rollback()

    def close(self) -> None:
        """Close the connection."""
        if not self._closed:
            self._closed = True
            try:
                self._conn.close()
            except Exception:
                pass

    @property
    def row_factory(self) -> Any:
        """Get the row factory (SQLite-specific)."""
        if not self._is_postgres:
            return getattr(self._conn, "row_factory", None)
        return None

    @row_factory.setter
    def row_factory(self, factory: Any) -> None:
        """Set the row factory (SQLite-specific, ignored for PostgreSQL)."""
        if not self._is_postgres:
            self._conn.row_factory = factory

    def execute_returning(self, sql: str, params: Optional[tuple] = None) -> _CursorWrapper:
        """
        Execute a SQL statement and return a wrapped cursor with RETURNING support.

        This is explicitly for PostgreSQL INSERT statements that need lastrowid.
        For SQLite, this behaves like regular execute().
        """
        return self.execute(sql, params)


# ============================================================================
# PostgreSQL Connection Pool
# ============================================================================


class PostgreSQLPool:
    """
    Thread-safe connection pool for PostgreSQL using psycopg2.

    Uses psycopg2.pool.ThreadedConnectionPool for production-grade
    connection management with automatic reconnection.
    """

    def __init__(
        self,
        min_conn: int = 2,
        max_conn: int = 10,
        timeout: int = 30,
    ):
        """
        Initialize the PostgreSQL connection pool.

        Args:
            min_conn: Minimum number of connections
            max_conn: Maximum number of connections
            timeout: Connection timeout in seconds
        """
        self._min_conn = min_conn
        self._max_conn = max_conn
        self._timeout = timeout
        self._pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
        self._lock = threading.Lock()
        self._local = threading.local()

    def _get_connection_params(self) -> Dict[str, Any]:
        """Build connection parameters from environment/config."""
        return {
            "host": os.getenv("PG_HOST", "localhost"),
            "port": int(os.getenv("PG_PORT", "5432")),
            "dbname": os.getenv("PG_DATABASE", "trading_bot"),
            "user": os.getenv("PG_USER", "postgres"),
            "password": os.getenv("PG_PASSWORD", ""),
            "options": f"-c search_path={os.getenv('PG_SCHEMA', 'public')}",
        }

    def _ensure_pool(self) -> None:
        """Create the connection pool if it doesn't exist."""
        if self._pool is not None:
            return

        with self._lock:
            # Double-check after acquiring lock
            if self._pool is not None:
                return

            params = self._get_connection_params()
            logger.info(
                f"Creating PostgreSQL connection pool: "
                f"{params['host']}:{params['port']}/{params['dbname']} "
                f"(min={self._min_conn}, max={self._max_conn})"
            )

            try:
                self._pool = psycopg2.pool.ThreadedConnectionPool(
                    self._min_conn,
                    self._max_conn,
                    **params,
                    connect_timeout=self._timeout,
                )
                logger.info("PostgreSQL connection pool created successfully")
            except psycopg2.OperationalError as e:
                logger.error(f"Failed to create PostgreSQL connection pool: {e}")
                raise

    def get_connection(self) -> _ConnectionWrapper:
        """
        Get a connection from the pool.

        Returns:
            _ConnectionWrapper with PostgreSQL connection
        """
        self._ensure_pool()

        try:
            conn = self._pool.getconn()
            # Set default isolation level
            conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_READ_COMMITTED)
            return _ConnectionWrapper(conn, is_postgres=True)
        except psycopg2.pool.PoolError as e:
            logger.error(f"Failed to get PostgreSQL connection from pool: {e}")
            raise

    def release_connection(self, wrapper: _ConnectionWrapper) -> None:
        """
        Release a connection back to the pool.

        Args:
            wrapper: _ConnectionWrapper to release
        """
        if wrapper._closed:
            return

        try:
            # Reset connection state
            if not wrapper._conn.closed:
                wrapper._conn.reset()
            self._pool.putconn(wrapper._conn)
            wrapper._closed = True
        except Exception as e:
            logger.warning(f"Error releasing PostgreSQL connection: {e}")

    def close_all(self) -> None:
        """Close all connections in the pool."""
        with self._lock:
            if self._pool:
                self._pool.closeall()
                self._pool = None
                logger.info("PostgreSQL connection pool closed")


# Global PostgreSQL pool instance
_postgresql_pool: Optional[PostgreSQLPool] = None


def _get_postgresql_pool() -> PostgreSQLPool:
    """Get or create the global PostgreSQL connection pool."""
    global _postgresql_pool
    if _postgresql_pool is None:
        _postgresql_pool = PostgreSQLPool(
            min_conn=int(os.getenv("PG_POOL_MIN", "2")),
            max_conn=int(os.getenv("PG_POOL_MAX", "10")),
            timeout=int(os.getenv("PG_POOL_TIMEOUT", "30")),
        )
    return _postgresql_pool


# ============================================================================
# Database Initialization Helper
# ============================================================================


def _init_postgres_schema(conn: _ConnectionWrapper) -> None:
    """
    Initialize PostgreSQL schema from schema_timescaledb.sql.

    This runs the TimescaleDB schema which includes hypertables,
    compression policies, and continuous aggregates.
    """
    schema_path = os.path.join(os.path.dirname(__file__), "schema_timescaledb.sql")

    if os.path.exists(schema_path):
        logger.info(f"Initializing PostgreSQL schema from {schema_path}")
        with open(schema_path, "r") as f:
            schema_sql = f.read()
        conn.executescript(schema_sql)
        logger.info("PostgreSQL schema initialized successfully")
    else:
        logger.warning(f"schema_timescaledb.sql not found at {schema_path}")
        # Create minimal tables as fallback
        _create_minimal_pg_schema(conn)


def _create_minimal_pg_schema(conn: _ConnectionWrapper) -> None:
    """Create minimal PostgreSQL schema as fallback when schema file is missing."""
    minimal_sql = """
        CREATE TABLE IF NOT EXISTS account_profiles (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            private_key_encrypted TEXT NOT NULL,
            public_key TEXT,
            is_default BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_used_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS trades (
            id BIGSERIAL,
            account_id TEXT NOT NULL DEFAULT 'sub_1',
            symbol TEXT NOT NULL,
            asset_class TEXT NOT NULL DEFAULT 'crypto',
            side TEXT NOT NULL,
            quantity DOUBLE PRECISION NOT NULL,
            entry_price DOUBLE PRECISION NOT NULL,
            exit_price DOUBLE PRECISION,
            entry_time TIMESTAMPTZ NOT NULL,
            exit_time TIMESTAMPTZ,
            pnl DOUBLE PRECISION DEFAULT 0,
            commission DOUBLE PRECISION DEFAULT 0,
            strategy TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS positions (
            id BIGSERIAL PRIMARY KEY,
            account_id TEXT NOT NULL DEFAULT 'sub_1',
            symbol TEXT NOT NULL,
            asset_class TEXT NOT NULL DEFAULT 'crypto',
            side TEXT NOT NULL,
            quantity DOUBLE PRECISION NOT NULL,
            entry_price DOUBLE PRECISION NOT NULL,
            current_price DOUBLE PRECISION,
            unrealized_pnl DOUBLE PRECISION DEFAULT 0,
            funding_pnl DOUBLE PRECISION DEFAULT 0,
            exit_price DOUBLE PRECISION,
            realized_pnl DOUBLE PRECISION DEFAULT 0,
            opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            closed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (account_id, symbol, side)
        );

        CREATE TABLE IF NOT EXISTS market_data (
            id BIGSERIAL,
            symbol TEXT NOT NULL,
            "timestamp" TIMESTAMPTZ NOT NULL,
            open DOUBLE PRECISION NOT NULL,
            high DOUBLE PRECISION NOT NULL,
            low DOUBLE PRECISION NOT NULL,
            close DOUBLE PRECISION NOT NULL,
            volume DOUBLE PRECISION NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'api',
            timeframe TEXT NOT NULL DEFAULT '1m',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS signals (
            id BIGSERIAL,
            account_id TEXT NOT NULL DEFAULT 'sub_1',
            symbol TEXT NOT NULL,
            asset_class TEXT NOT NULL DEFAULT 'crypto',
            strategy TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            side TEXT,
            confidence DOUBLE PRECISION NOT NULL DEFAULT 0,
            strength DOUBLE PRECISION NOT NULL DEFAULT 0,
            indicators JSONB DEFAULT '{}',
            timeframe TEXT,
            regime TEXT,
            executed BOOLEAN NOT NULL DEFAULT FALSE,
            execution_price DOUBLE PRECISION,
            "timestamp" TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS performance_metrics (
            id BIGSERIAL,
            account_id TEXT NOT NULL DEFAULT 'sub_1',
            metric_date DATE NOT NULL,
            total_pnl DOUBLE PRECISION DEFAULT 0,
            win_rate DOUBLE PRECISION DEFAULT 0,
            total_trades INTEGER DEFAULT 0,
            avg_rrr DOUBLE PRECISION DEFAULT 0,
            max_drawdown DOUBLE PRECISION DEFAULT 0,
            sharpe_ratio DOUBLE PRECISION,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (account_id, metric_date)
        );

        CREATE TABLE IF NOT EXISTS regime_history (
            id BIGSERIAL,
            symbol TEXT NOT NULL,
            regime TEXT NOT NULL,
            old_regime TEXT,
            new_regime TEXT,
            adx DOUBLE PRECISION,
            adx_value DOUBLE PRECISION,
            volatility_score DOUBLE PRECISION,
            confidence DOUBLE PRECISION,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            duration_minutes INTEGER
        );

        CREATE TABLE IF NOT EXISTS regime_shadow (
            id BIGSERIAL PRIMARY KEY,
            detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            symbol TEXT NOT NULL,
            adx_regime TEXT NOT NULL,
            ml_regime TEXT NOT NULL,
            ml_confidence DOUBLE PRECISION,
            ml_model_type TEXT,
            agree INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS adaptive_weights (
            id BIGSERIAL PRIMARY KEY,
            computed_at TIMESTAMPTZ NOT NULL,
            regime TEXT NOT NULL,
            strategy TEXT NOT NULL,
            trade_count INTEGER NOT NULL DEFAULT 0,
            recent_expectancy DOUBLE PRECISION,
            lifetime_expectancy DOUBLE PRECISION,
            multiplier DOUBLE PRECISION NOT NULL DEFAULT 1.0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS regime_param_overlays (
            id BIGSERIAL PRIMARY KEY,
            strategy TEXT NOT NULL,
            regime TEXT NOT NULL,
            params_json TEXT NOT NULL,
            objective TEXT,
            objective_value DOUBLE PRECISION,
            trade_count INTEGER,
            study_name TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS grid_state (
            id BIGSERIAL PRIMARY KEY,
            symbol TEXT NOT NULL UNIQUE,
            grid_capital DOUBLE PRECISION NOT NULL,
            center_price DOUBLE PRECISION,
            initial_center DOUBLE PRECISION,
            emergency_stop_price DOUBLE PRECISION NOT NULL,
            active_levels INTEGER DEFAULT 0,
            total_levels INTEGER DEFAULT 0,
            orders_placed INTEGER DEFAULT 0,
            refresh_count INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            atr_at_creation DOUBLE PRECISION,
            grid_spacing DOUBLE PRECISION,
            realized_pnl DOUBLE PRECISION DEFAULT 0,
            total_fees DOUBLE PRECISION DEFAULT 0,
            last_refresh TIMESTAMPTZ,
            consistency_checked_at TIMESTAMPTZ,
            repair_history JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            closed_at TIMESTAMPTZ
        );

        CREATE TABLE IF NOT EXISTS grid_levels (
            id BIGSERIAL PRIMARY KEY,
            grid_id BIGINT NOT NULL,
            level_number INTEGER NOT NULL,
            side TEXT NOT NULL,
            price DOUBLE PRECISION NOT NULL,
            quantity DOUBLE PRECISION NOT NULL,
            capital_allocated DOUBLE PRECISION NOT NULL,
            order_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS funding_payments (
            id BIGSERIAL,
            account_id TEXT NOT NULL DEFAULT 'sub_1',
            subaccount_id TEXT,
            position_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            funding_rate DOUBLE PRECISION NOT NULL,
            payment_amount DOUBLE PRECISION NOT NULL,
            position_value DOUBLE PRECISION NOT NULL,
            margin_mode TEXT NOT NULL DEFAULT 'cross',
            "timestamp" TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS funding_rate_history (
            id BIGSERIAL,
            symbol TEXT NOT NULL,
            funding_rate DOUBLE PRECISION NOT NULL,
            premium_index DOUBLE PRECISION,
            interest_rate DOUBLE PRECISION,
            "timestamp" TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS pacifica_positions (
            id BIGSERIAL PRIMARY KEY,
            account_id TEXT NOT NULL DEFAULT 'sub_1',
            subaccount_id TEXT,
            position_id TEXT NOT NULL UNIQUE,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            size DOUBLE PRECISION NOT NULL,
            entry_price DOUBLE PRECISION NOT NULL,
            current_price DOUBLE PRECISION,
            leverage INTEGER NOT NULL DEFAULT 10,
            margin_mode TEXT NOT NULL DEFAULT 'cross',
            margin_used DOUBLE PRECISION NOT NULL,
            cumulative_funding_paid DOUBLE PRECISION DEFAULT 0.0,
            last_funding_timestamp TIMESTAMPTZ,
            unrealized_pnl DOUBLE PRECISION DEFAULT 0.0,
            liquidation_price DOUBLE PRECISION,
            tick_size DOUBLE PRECISION,
            lot_size DOUBLE PRECISION,
            opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            closed_at TIMESTAMPTZ,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS subaccount_configs (
            subaccount_id TEXT PRIMARY KEY,
            subaccount_name TEXT NOT NULL,
            subaccount_public_key TEXT,
            trading_strategy TEXT NOT NULL DEFAULT 'balanced',
            max_position_size DOUBLE PRECISION NOT NULL DEFAULT 10000.0,
            risk_per_trade DOUBLE PRECISION NOT NULL DEFAULT 0.02,
            max_leverage INTEGER NOT NULL DEFAULT 20,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            description TEXT,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            checksum TEXT
        );
    """
    conn.executescript(minimal_sql)

# Check for psycopg2 availability
try:
    import psycopg2
    import psycopg2.pool
    import psycopg2.extras
    import psycopg2.extensions

    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
    if DATABASE_BACKEND == "postgres":
        logger.error("psycopg2 not available but DATABASE_BACKEND=postgres")
    else:
        logger.info("psycopg2 not available, PostgreSQL backend disabled")


class DataCache:
    """Simple in-memory cache with TTL for frequently accessed data."""

    def __init__(self, max_size: int = 1000, default_ttl: int = 300):
        self.cache: Dict[str, Tuple[Any, float]] = {}
        self.max_size = max_size
        self.default_ttl = default_ttl

    def get(self, key: str) -> Any:
        """Get cached value if not expired."""
        if key in self.cache:
            value, expiry = self.cache[key]
            if time.time() < expiry:
                return value
            else:
                del self.cache[key]
        return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """Set cached value with TTL."""
        if len(self.cache) >= self.max_size:
            # Simple LRU: remove oldest entries
            oldest_keys = sorted(self.cache.keys(), key=lambda k: self.cache[k][1])[
                : self.max_size // 10
            ]
            for k in oldest_keys:
                del self.cache[k]

        expiry = time.time() + (ttl or self.default_ttl)
        self.cache[key] = (value, expiry)

    def invalidate(self, key: str):
        """Remove key from cache."""
        self.cache.pop(key, None)

    def clear(self):
        """Clear all cached data."""
        self.cache.clear()


# Global cache instance
_data_cache = DataCache()

logger = logging.getLogger(__name__)


class ConnectionPool:
    """Simple connection pool for SQLite."""

    def __init__(self, max_connections: int = 10, max_idle_time: int = 300):
        self.max_connections = max_connections
        self.max_idle_time = max_idle_time
        self._connections: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._local = threading.local()

    def get_connection(self, timeout: float = 30.0) -> sqlite3.Connection:
        """
        Get a connection from the pool with timeout.

        Args:
            timeout: Maximum time to wait for a connection (seconds)

        Returns:
            sqlite3.Connection object

        Raises:
            TimeoutError: If no connection becomes available within timeout period
        """
        # Check for thread-local connection first
        if hasattr(self._local, "connection"):
            conn = self._local.connection
            if self._is_connection_valid(conn):
                return conn

        start_time = time.time()

        with self._lock:
            # Clean up expired connections
            self._cleanup_expired_connections()

            # Try to reuse an existing connection
            for conn_info in self._connections:
                if not conn_info["in_use"]:
                    conn_info["in_use"] = True
                    conn_info["last_used"] = time.time()
                    self._local.connection = conn_info["connection"]
                    return conn_info["connection"]

            # Create new connection if pool not full
            if len(self._connections) < self.max_connections:
                conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn_info = {
                    "connection": conn,
                    "in_use": True,
                    "created_at": time.time(),
                    "last_used": time.time(),
                }
                self._connections.append(conn_info)
                self._local.connection = conn
                return conn

        # Wait for a connection to become available with timeout
        while time.time() - start_time < timeout:
            with self._lock:
                for conn_info in self._connections:
                    if not conn_info["in_use"]:
                        conn_info["in_use"] = True
                        conn_info["last_used"] = time.time()
                        self._local.connection = conn_info["connection"]
                        return conn_info["connection"]
            time.sleep(0.01)  # Small delay to prevent busy waiting

        # Timeout reached - raise error
        raise TimeoutError(
            f"Database connection timeout after {timeout}s - pool exhausted "
            f"({len(self._connections)}/{self.max_connections} connections in use)"
        )

    def release_connection(self, conn: sqlite3.Connection):
        """Release a connection back to the pool."""
        with self._lock:
            for conn_info in self._connections:
                if conn_info["connection"] is conn:
                    conn_info["in_use"] = False
                    conn_info["last_used"] = time.time()
                    break

    def _is_connection_valid(self, conn: sqlite3.Connection) -> bool:
        """Check if connection is still valid."""
        try:
            conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def _cleanup_expired_connections(self):
        """Remove expired connections."""
        current_time = time.time()
        self._connections = [
            conn_info
            for conn_info in self._connections
            if current_time - conn_info["last_used"] < self.max_idle_time
        ]

    def close_all(self):
        """Close all connections in the pool."""
        with self._lock:
            for conn_info in self._connections:
                try:
                    conn_info["connection"].close()
                except Exception:
                    pass
            self._connections.clear()


# Global connection pool (SQLite)
_connection_pool = ConnectionPool()

# Flag to track which backend is active
_active_backend: str = DATABASE_BACKEND


def get_backend() -> str:
    """Return the active database backend ('sqlite' or 'postgres')."""
    return _active_backend


def is_postgres() -> bool:
    """Return True if the active backend is PostgreSQL."""
    return _active_backend == "postgres"


@contextmanager
def get_db_connection() -> Generator[_ConnectionWrapper, None, None]:
    """
    Database connection context manager with pooling.

    Returns a _ConnectionWrapper that transparently handles both
    SQLite and PostgreSQL backends. All existing code that uses
    `with get_db_connection() as conn:` will work unchanged.

    Returns:
        _ConnectionWrapper for the active backend
    """
    if _active_backend == "postgres":
        pool = _get_postgresql_pool()
        wrapper = pool.get_connection()
        try:
            yield wrapper
        finally:
            pool.release_connection(wrapper)
    else:
        # SQLite path (existing behavior)
        conn = _connection_pool.get_connection()
        wrapped = _ConnectionWrapper(conn, is_postgres=False)
        try:
            yield wrapped
        finally:
            _connection_pool.release_connection(conn)


def init_database():
    """Initialize database with schema and performance indexes."""
    try:
        if _active_backend == "postgres":
            _init_postgres_database()
        else:
            _init_sqlite_database()
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise


def _init_postgres_database():
    """Initialize PostgreSQL database from schema_timescaledb.sql."""
    with get_db_connection() as conn:
        _init_postgres_schema(conn)
    logger.info("PostgreSQL database initialized successfully")


def _init_sqlite_database():
    """Initialize SQLite database with schema and performance indexes."""
    # Ensure database directory exists
    db_dir = os.path.dirname(DATABASE_PATH)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)

    # Get schema file path relative to this module
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")

    with get_db_connection() as conn:
        # Create tables from schema file
        if os.path.exists(schema_path):
            with open(schema_path, "r") as f:
                conn.executescript(f.read())
        else:
            logger.warning(
                f"schema.sql not found at {schema_path}, creating minimal schema"
            )
            # Create essential tables if schema.sql is missing
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS account_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    private_key_encrypted TEXT NOT NULL,
                    public_key TEXT,
                    is_default BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL DEFAULT 'sub_1',
                    symbol TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    entry_time TIMESTAMP NOT NULL,
                    exit_time TIMESTAMP,
                    pnl REAL DEFAULT 0,
                    commission REAL DEFAULT 0,
                    strategy TEXT,
                    status TEXT DEFAULT 'open',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL DEFAULT 'sub_1',
                    symbol TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    current_price REAL,
                    unrealized_pnl REAL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'open',
                    exit_price REAL,
                    realized_pnl REAL DEFAULT 0,
                    opened_at TIMESTAMP NOT NULL,
                    closed_at TIMESTAMP,
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
                    account_id TEXT NOT NULL DEFAULT 'sub_1',
                    symbol TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    strength REAL NOT NULL,
                    indicators TEXT,
                    timestamp TIMESTAMP NOT NULL,
                    executed BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS performance_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL DEFAULT 'sub_1',
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
            """)

        # Grid state persistence table
        conn.execute("""
                CREATE TABLE IF NOT EXISTS grid_states (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL UNIQUE,
                    state TEXT NOT NULL DEFAULT 'active',
                    regime_on_creation TEXT,
                    grid_capital REAL DEFAULT 0,
                    emergency_stop REAL DEFAULT 0,
                    atr_at_creation REAL DEFAULT 0,
                    grid_spacing REAL DEFAULT 0,
                    num_levels INTEGER DEFAULT 10,
                    order_ids TEXT,
                    total_buy_fills INTEGER DEFAULT 0,
                    total_sell_fills INTEGER DEFAULT 0,
                    realized_pnl REAL DEFAULT 0,
                    total_fees REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

        # Enhanced grid state columns for consistency management
        grid_state_alter_statements = [
            "ALTER TABLE grid_states ADD COLUMN center_price REAL",
            "ALTER TABLE grid_states ADD COLUMN initial_center REAL",
            "ALTER TABLE grid_states ADD COLUMN orders_placed INTEGER DEFAULT 0",
            "ALTER TABLE grid_states ADD COLUMN refresh_count INTEGER DEFAULT 0",
            "ALTER TABLE grid_states ADD COLUMN last_refresh TIMESTAMP",
            "ALTER TABLE grid_states ADD COLUMN consistency_checked_at TIMESTAMP",
            "ALTER TABLE grid_states ADD COLUMN repair_history TEXT",
        ]

        for alter_sql in grid_state_alter_statements:
            try:
                conn.execute(alter_sql)
            except Exception as e:
                if "duplicate column name" not in str(e).lower():
                    logger.warning(f"Failed to add grid state column: {e}")

        # Regime transition history (P0 regime observability).
        # schema.sql already ships a snapshot-style regime_history table
        # (regime, adx_value, confidence, duration_minutes); this CREATE is a
        # superset fallback for installs without schema.sql, and the ALTERs
        # below upgrade the legacy shape with transition columns.
        conn.execute("""
                CREATE TABLE IF NOT EXISTS regime_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    old_regime TEXT,
                    new_regime TEXT,
                    adx REAL,
                    adx_value REAL,
                    volatility_score REAL,
                    confidence REAL,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    duration_minutes INTEGER
                );
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_regime_history_symbol_time "
            "ON regime_history(symbol, detected_at)"
        )

        regime_history_alter_statements = [
            "ALTER TABLE regime_history ADD COLUMN old_regime TEXT",
            "ALTER TABLE regime_history ADD COLUMN new_regime TEXT",
            "ALTER TABLE regime_history ADD COLUMN adx REAL",
        ]
        for alter_sql in regime_history_alter_statements:
            try:
                conn.execute(alter_sql)
            except Exception as e:
                err = str(e).lower()
                if "duplicate column name" not in err and "already exists" not in err:
                    logger.warning(f"Failed to add regime_history column: {e}")

        # ML regime shadow-mode observations (P3): one row per regime cache
        # refresh comparing the authoritative ADX result against the ML
        # detector's prediction. Observability only - never read by the
        # trading path.
        conn.execute("""
                CREATE TABLE IF NOT EXISTS regime_shadow (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    symbol TEXT NOT NULL,
                    adx_regime TEXT NOT NULL,
                    ml_regime TEXT NOT NULL,
                    ml_confidence REAL,
                    ml_model_type TEXT,
                    agree INTEGER NOT NULL DEFAULT 0
                );
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_regime_shadow_symbol_time "
            "ON regime_shadow(symbol, detected_at)"
        )

        # Adaptive per-regime strategy weight snapshots (observability for
        # AdaptiveWeightManager recomputes - see adaptive_weights.py).
        conn.execute("""
                CREATE TABLE IF NOT EXISTS adaptive_weights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    computed_at TIMESTAMP NOT NULL,
                    regime TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    trade_count INTEGER NOT NULL DEFAULT 0,
                    recent_expectancy REAL,
                    lifetime_expectancy REAL,
                    multiplier REAL NOT NULL DEFAULT 1.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_adaptive_weights_computed "
            "ON adaptive_weights(computed_at)"
        )

        # Per-regime optimized parameter overlays (P4). One ACTIVE row per
        # (strategy, regime); saving a new overlay deactivates the previous
        # one so history is preserved. Runtime application is gated by
        # ENABLE_REGIME_PARAM_OVERLAYS (see regime_param_overlay.py).
        conn.execute("""
                CREATE TABLE IF NOT EXISTS regime_param_overlays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy TEXT NOT NULL,
                    regime TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    objective TEXT,
                    objective_value REAL,
                    trade_count INTEGER,
                    study_name TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_regime_param_overlays_lookup "
            "ON regime_param_overlays(strategy, regime, active)"
        )

        # Add account_id columns to existing tables if they don't exist
        alter_statements = [
            "ALTER TABLE trades ADD COLUMN account_id TEXT NOT NULL DEFAULT 'sub_1'",
            "ALTER TABLE positions ADD COLUMN account_id TEXT NOT NULL DEFAULT 'sub_1'",
            "ALTER TABLE signals ADD COLUMN account_id TEXT NOT NULL DEFAULT 'sub_1'",
            "ALTER TABLE performance_metrics ADD COLUMN account_id TEXT NOT NULL DEFAULT 'sub_1'",
            "ALTER TABLE positions ADD COLUMN funding_pnl REAL DEFAULT 0",
            "ALTER TABLE positions ADD COLUMN exit_price REAL",
            "ALTER TABLE trades ADD COLUMN regime TEXT",
        ]

        for alter_sql in alter_statements:
            try:
                conn.execute(alter_sql)
            except Exception as e:
                # Column might already exist, ignore error
                # (SQLite says "duplicate column name", Postgres "already exists")
                err = str(e).lower()
                if "duplicate column name" not in err and "already exists" not in err:
                    logger.warning(f"Failed to add column: {e}")

        # Create indexes for account_id columns
        account_indexes = [
            "CREATE INDEX IF NOT EXISTS idx_trades_account_symbol ON trades(account_id, symbol)",
            "CREATE INDEX IF NOT EXISTS idx_trades_account_status ON trades(account_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_trades_account_entry_time ON trades(account_id, entry_time)",
            "CREATE INDEX IF NOT EXISTS idx_trades_account_exit_time ON trades(account_id, exit_time)",
            "CREATE INDEX IF NOT EXISTS idx_trades_account_symbol_status ON trades(account_id, symbol, status)",
            "CREATE INDEX IF NOT EXISTS idx_trades_account_status_time ON trades(account_id, status, entry_time)",
            "CREATE INDEX IF NOT EXISTS idx_positions_account_symbol ON positions(account_id, symbol)",
            "CREATE INDEX IF NOT EXISTS idx_positions_account_updated ON positions(account_id, updated_at)",
            "CREATE INDEX IF NOT EXISTS idx_signals_account_symbol ON signals(account_id, symbol)",
            "CREATE INDEX IF NOT EXISTS idx_signals_account_timestamp ON signals(account_id, timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_signals_account_type_time ON signals(account_id, signal_type, timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_signals_account_executed ON signals(account_id, executed)",
            "CREATE INDEX IF NOT EXISTS idx_performance_account_date ON performance_metrics(account_id, date)",
        ]

        for index_sql in account_indexes:
            try:
                conn.execute(index_sql)
            except Exception as e:
                logger.warning(f"Failed to create account index: {e}")

        # Create performance indexes for time-series queries
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_market_data_symbol_timestamp ON market_data(symbol, timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_market_data_timestamp ON market_data(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_trades_symbol_entry_time ON trades(symbol, entry_time)",
            "CREATE INDEX IF NOT EXISTS idx_trades_status_entry_time ON trades(status, entry_time)",
            "CREATE INDEX IF NOT EXISTS idx_signals_symbol_timestamp ON signals(symbol, timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_signals_timestamp ON signals(timestamp)",
            "CREATE INDEX IF NOT EXISTS idx_performance_metrics_date ON performance_metrics(date)",
        ]

        for index_sql in indexes:
            try:
                conn.execute(index_sql)
            except Exception as e:
                logger.warning(f"Failed to create index: {e}")

        conn.commit()
    logger.info("SQLite database initialized successfully with performance indexes")


def summarize_regime_shadow(
    rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Summarise ML shadow-mode observations.

    Pure function over rows shaped like
    :meth:`DatabaseManager.get_regime_shadow` output; used by the
    /api/regimes/shadow endpoint and unit-testable without a database.

    Args:
        rows: Shadow observation dicts (adx_regime, ml_regime, agree, ...).

    Returns:
        Dict with total observations, overall agreement pct, agreement pct
        per ADX regime, and the ML regime distribution.
    """
    total = len(rows)
    if total == 0:
        return {
            "observations": 0,
            "agreement_pct": None,
            "agreement_by_adx_regime": {},
            "ml_regime_distribution": {},
        }

    agree_count = sum(1 for r in rows if r.get("agree"))

    by_adx: Dict[str, Dict[str, int]] = {}
    ml_dist: Dict[str, int] = {}
    for r in rows:
        adx_regime = r.get("adx_regime") or "unknown"
        ml_regime = r.get("ml_regime") or "unknown"
        bucket = by_adx.setdefault(adx_regime, {"total": 0, "agree": 0})
        bucket["total"] += 1
        bucket["agree"] += 1 if r.get("agree") else 0
        ml_dist[ml_regime] = ml_dist.get(ml_regime, 0) + 1

    return {
        "observations": total,
        "agreement_pct": round(100.0 * agree_count / total, 2),
        "agreement_by_adx_regime": {
            regime: {
                "observations": b["total"],
                "agreement_pct": round(100.0 * b["agree"] / b["total"], 2),
            }
            for regime, b in sorted(by_adx.items())
        },
        "ml_regime_distribution": {
            regime: count for regime, count in sorted(ml_dist.items())
        },
    }


class DatabaseManager:
    """Database operations for trading bot.

    Supports both SQLite and PostgreSQL backends transparently.
    All methods use `with get_db_connection() as conn:` which returns
    a _ConnectionWrapper that handles SQL translation automatically.
    """

    def __init__(self):
        """Initialize database manager.

        Creates the database directory (SQLite only) and ensures all
        tables exist via init_database().
        """
        # Ensure database directory exists (SQLite only)
        if _active_backend == "sqlite":
            db_dir = os.path.dirname(DATABASE_PATH)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)

        # Always run init_database() to ensure all tables exist
        # (uses CREATE TABLE IF NOT EXISTS, safe to run multiple times)
        init_database()

        # Store a connection reference for execute/commit pattern
        self._conn = None

    @property
    def backend(self) -> str:
        """Return the active database backend name."""
        return _active_backend

    def execute(self, sql: str, params: Optional[tuple] = None):
        """
        Execute SQL statement and return cursor.

        Args:
            sql: SQL query to execute (uses ? placeholders for both backends)
            params: Query parameters

        Returns:
            _CursorWrapper object
        """
        if self._conn is None:
            if _active_backend == "postgres":
                pool = _get_postgresql_pool()
                self._conn = pool.get_connection()
            else:
                self._conn = _connection_pool.get_connection()

        if params:
            return self._conn.execute(sql, params)
        else:
            return self._conn.execute(sql)

    def commit(self):
        """Commit current transaction."""
        if self._conn:
            self._conn.commit()

    def close(self):
        """Close the database connection."""
        if self._conn:
            if _active_backend == "postgres":
                pool = _get_postgresql_pool()
                pool.release_connection(self._conn)
            else:
                _connection_pool.release_connection(self._conn)
            self._conn = None

    def get_connection(self):
        """
        Get an async database connection context manager for aiosqlite.

        Returns an async context manager that provides an aiosqlite connection.
        Used by MarketDataCollector and other async database operations.

        For PostgreSQL backend, this falls back to synchronous operations.

        Usage:
            async with db_manager.get_connection() as conn:
                await conn.execute(...)
        """
        if _active_backend == "postgres":
            # PostgreSQL doesn't support aiosqlite - return sync wrapper
            raise RuntimeError(
                "async get_connection() is not supported with PostgreSQL backend. "
                "Use get_db_connection() context manager instead."
            )

        if not HAS_AIOSQLITE:
            raise RuntimeError(
                "aiosqlite not available - async operations require aiosqlite"
            )
        return aiosqlite.connect(DATABASE_PATH)

    def save_trade(
        self, trade_data: Dict[str, Any], account_id: str = "sub_1"
    ) -> Optional[int]:
        """Save a trade to database."""
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades (account_id, symbol, asset_class, side, quantity, entry_price,
                                  exit_price, entry_time, exit_time, pnl,
                                  commission, strategy, status, regime)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    account_id,
                    trade_data["symbol"],
                    trade_data.get("asset_class", "crypto"),
                    trade_data["side"],
                    trade_data["quantity"],
                    trade_data["entry_price"],
                    trade_data.get("exit_price"),
                    trade_data["entry_time"],
                    trade_data.get("exit_time"),
                    trade_data.get("pnl", 0),
                    trade_data.get("commission", 0),
                    trade_data.get("strategy"),
                    trade_data.get("status", "open"),
                    trade_data.get("regime"),
                ),
            )
            conn.commit()

            # Invalidate related caches
            _data_cache.invalidate(f"trades_recent_10_{account_id}")
            _data_cache.invalidate(f"trades_recent_20_{account_id}")
            _data_cache.invalidate(f"trades_all_10_{account_id}")
            _data_cache.invalidate(f"trades_all_20_{account_id}")

            return cursor.lastrowid  # type: ignore

    def bulk_save_trades(
        self, trades_data: List[Dict[str, Any]], account_id: str = "sub_1"
    ) -> int:
        """Bulk save multiple trades efficiently."""
        if not trades_data:
            return 0

        with get_db_connection() as conn:
            conn.executemany(
                """
                INSERT INTO trades (account_id, symbol, asset_class, side, quantity, entry_price,
                                  exit_price, entry_time, exit_time, pnl,
                                  commission, strategy, status, regime)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        account_id,
                        trade["symbol"],
                        trade.get("asset_class", "crypto"),
                        trade["side"],
                        trade["quantity"],
                        trade["entry_price"],
                        trade.get("exit_price"),
                        trade["entry_time"],
                        trade.get("exit_time"),
                        trade.get("pnl", 0),
                        trade.get("commission", 0),
                        trade.get("strategy"),
                        trade.get("status", "open"),
                        trade.get("regime"),
                    )
                    for trade in trades_data
                ],
            )
            conn.commit()

            # Invalidate related caches
            _data_cache.invalidate(f"trades_recent_10_{account_id}")
            _data_cache.invalidate(f"trades_recent_20_{account_id}")
            _data_cache.invalidate(f"trades_all_10_{account_id}")
            _data_cache.invalidate(f"trades_all_20_{account_id}")

            return len(trades_data)

    def update_trade(self, trade_id: int, update_data: Dict[str, Any]):
        """Update an existing trade."""
        with get_db_connection() as conn:
            # Build dynamic update query
            set_parts = []
            values = []
            for key, value in update_data.items():
                if key in ["exit_price", "exit_time", "pnl", "status"]:
                    set_parts.append(f"{key} = ?")
                    values.append(value)

            if set_parts:
                query = f"UPDATE trades SET {', '.join(set_parts)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
                values.append(trade_id)
                conn.execute(query, tuple(values))
                conn.commit()

    def save_regime_transition(
        self,
        symbol: str,
        old_regime: str,
        new_regime: str,
        adx: Optional[float] = None,
        volatility_score: Optional[float] = None,
        detected_at: Optional[Any] = None,
    ) -> Optional[int]:
        """Persist a confirmed regime transition to regime_history.

        Args:
            symbol: Trading symbol.
            old_regime: Regime value being exited (e.g. "ranging_calm").
            new_regime: Newly confirmed regime value.
            adx: ADX at detection time, if available.
            volatility_score: Volatility score (0-100) at detection, if
                available (only computed in ranging classifications).
            detected_at: Transition timestamp (datetime or ISO string).
                Defaults to now.

        Returns:
            Row id of the inserted transition, or None on Postgres.
        """
        if detected_at is None:
            detected_at = datetime.now()
        if isinstance(detected_at, datetime):
            detected_at = detected_at.isoformat()

        with get_db_connection() as conn:
            # "regime" mirrors new_regime and "adx_value" mirrors adx for
            # compatibility with the legacy snapshot-style table shape.
            cursor = conn.execute(
                """
                INSERT INTO regime_history
                    (symbol, regime, old_regime, new_regime, adx, adx_value,
                     volatility_score, detected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    new_regime,
                    old_regime,
                    new_regime,
                    adx,
                    adx,
                    volatility_score,
                    detected_at,
                ),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore

    def get_regime_history(
        self, symbol: Optional[str] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Fetch recent regime transitions, newest first.

        Args:
            symbol: Optional symbol filter.
            limit: Maximum rows to return (default: 50).

        Returns:
            List of dicts with id, symbol, old_regime, new_regime, adx,
            volatility_score, detected_at.
        """
        query = (
            "SELECT id, symbol, old_regime, new_regime, adx, volatility_score, "
            "detected_at FROM regime_history"
        )
        params: List[Any] = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY detected_at DESC, id DESC LIMIT ?"
        params.append(limit)

        with get_db_connection() as conn:
            cursor = conn.execute(query, tuple(params))
            rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "symbol": row[1],
                "old_regime": row[2],
                "new_regime": row[3],
                "adx": row[4],
                "volatility_score": row[5],
                "detected_at": str(row[6]) if row[6] is not None else None,
            }
            for row in rows
        ]

    def save_regime_shadow(
        self,
        symbol: str,
        adx_regime: str,
        ml_regime: str,
        ml_confidence: Optional[float] = None,
        ml_model_type: Optional[str] = None,
        agree: Optional[int] = None,
        detected_at: Optional[Any] = None,
    ) -> Optional[int]:
        """Persist an ML shadow-mode regime observation to regime_shadow.

        Args:
            symbol: Trading symbol.
            adx_regime: The authoritative ADX regime value.
            ml_regime: The ML detector's regime value.
            ml_confidence: ML prediction confidence (0-1), if available.
            ml_model_type: Which ML model produced the prediction
                ("gmm" or "hmm").
            agree: 1 when the two regimes match, 0 otherwise. Computed
                from the regime values when ``None``.
            detected_at: Observation timestamp (datetime or ISO string).
                Defaults to now.

        Returns:
            Row id of the inserted observation, or None on Postgres.
        """
        if detected_at is None:
            detected_at = datetime.now()
        if isinstance(detected_at, datetime):
            detected_at = detected_at.isoformat()
        if agree is None:
            agree = int(adx_regime == ml_regime)

        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO regime_shadow
                    (detected_at, symbol, adx_regime, ml_regime,
                     ml_confidence, ml_model_type, agree)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    detected_at,
                    symbol,
                    adx_regime,
                    ml_regime,
                    ml_confidence,
                    ml_model_type,
                    int(agree),
                ),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore

    def get_regime_shadow(
        self, symbol: Optional[str] = None, limit: int = 200
    ) -> List[Dict[str, Any]]:
        """Fetch recent shadow-mode observations, newest first.

        Args:
            symbol: Optional symbol filter.
            limit: Maximum rows to return (default: 200).

        Returns:
            List of dicts with id, detected_at, symbol, adx_regime,
            ml_regime, ml_confidence, ml_model_type, agree.
        """
        query = (
            "SELECT id, detected_at, symbol, adx_regime, ml_regime, "
            "ml_confidence, ml_model_type, agree FROM regime_shadow"
        )
        params: List[Any] = []
        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol)
        query += " ORDER BY detected_at DESC, id DESC LIMIT ?"
        params.append(limit)

        with get_db_connection() as conn:
            cursor = conn.execute(query, tuple(params))
            rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "detected_at": str(row[1]) if row[1] is not None else None,
                "symbol": row[2],
                "adx_regime": row[3],
                "ml_regime": row[4],
                "ml_confidence": row[5],
                "ml_model_type": row[6],
                "agree": row[7],
            }
            for row in rows
        ]

    def save_adaptive_weight_snapshot(
        self, rows: List[Dict[str, Any]]
    ) -> int:
        """Persist an adaptive weight recompute snapshot.

        Args:
            rows: List of dicts with computed_at, regime, strategy,
                trade_count, recent_expectancy, lifetime_expectancy,
                multiplier (one row per (regime, strategy) cell).

        Returns:
            Number of rows inserted.
        """
        if not rows:
            return 0

        with get_db_connection() as conn:
            conn.executemany(
                """
                INSERT INTO adaptive_weights
                    (computed_at, regime, strategy, trade_count,
                     recent_expectancy, lifetime_expectancy, multiplier)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["computed_at"],
                        row["regime"],
                        row["strategy"],
                        row.get("trade_count", 0),
                        row.get("recent_expectancy"),
                        row.get("lifetime_expectancy"),
                        row.get("multiplier", 1.0),
                    )
                    for row in rows
                ],
            )
            conn.commit()
            return len(rows)

    def get_adaptive_weight_snapshot(self) -> List[Dict[str, Any]]:
        """Fetch the most recent adaptive weight snapshot.

        Returns:
            List of dicts (computed_at, regime, strategy, trade_count,
            recent_expectancy, lifetime_expectancy, multiplier) for the
            latest computed_at; empty list when no snapshot exists.
        """
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                SELECT computed_at, regime, strategy, trade_count,
                       recent_expectancy, lifetime_expectancy, multiplier
                FROM adaptive_weights
                WHERE computed_at = (
                    SELECT MAX(computed_at) FROM adaptive_weights
                )
                ORDER BY regime, strategy
                """
            )
            rows = cursor.fetchall()

        return [
            {
                "computed_at": str(row[0]) if row[0] is not None else None,
                "regime": row[1],
                "strategy": row[2],
                "trade_count": row[3],
                "recent_expectancy": row[4],
                "lifetime_expectancy": row[5],
                "multiplier": row[6],
            }
            for row in rows
        ]

    def save_regime_param_overlay(
        self,
        strategy: str,
        regime: str,
        params: Dict[str, Any],
        objective: Optional[str] = None,
        objective_value: Optional[float] = None,
        trade_count: Optional[int] = None,
        study_name: Optional[str] = None,
    ) -> Optional[int]:
        """Persist a per-regime parameter overlay (P4).

        Deactivates any previously active overlay for the same
        (strategy, regime) pair - history rows are kept - then inserts
        the new overlay as active and writes the JSON export
        (config/regime_param_overlays.json) through.

        Args:
            strategy: Snake_case strategy key (e.g. "mean_reversion").
            regime: Regime value (e.g. "ranging_calm").
            params: Optimized parameter dict to store as JSON.
            objective: Objective metric name the overlay was tuned for.
            objective_value: Best objective value achieved.
            trade_count: Matching-regime trade count of the best trial.
            study_name: Optuna study name that produced the overlay.

        Returns:
            Row id of the inserted overlay, or None on Postgres.
        """
        params_json = json.dumps(params, sort_keys=True)
        created_at = datetime.now().isoformat()

        with get_db_connection() as conn:
            conn.execute(
                "UPDATE regime_param_overlays SET active = 0 "
                "WHERE strategy = ? AND regime = ? AND active = 1",
                (strategy, regime),
            )
            cursor = conn.execute(
                """
                INSERT INTO regime_param_overlays
                    (strategy, regime, params_json, objective,
                     objective_value, trade_count, study_name, active,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    strategy,
                    regime,
                    params_json,
                    objective,
                    objective_value,
                    trade_count,
                    study_name,
                    created_at,
                ),
            )
            conn.commit()
            row_id = cursor.lastrowid

        try:
            self.export_regime_param_overlays()
        except Exception as e:
            logger.warning(f"Regime overlay JSON export failed: {e}")

        return row_id  # type: ignore

    def get_regime_param_overlays(
        self,
        active_only: bool = True,
        strategy: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch stored per-regime parameter overlays, newest first.

        Args:
            active_only: Only return the currently active overlays.
            strategy: Optional snake_case strategy filter.

        Returns:
            List of dicts with id, strategy, regime, params (parsed),
            objective, objective_value, trade_count, study_name, active,
            created_at.
        """
        query = (
            "SELECT id, strategy, regime, params_json, objective, "
            "objective_value, trade_count, study_name, active, created_at "
            "FROM regime_param_overlays"
        )
        clauses: List[str] = []
        params: List[Any] = []
        if active_only:
            clauses.append("active = 1")
        if strategy:
            clauses.append("strategy = ?")
            params.append(strategy)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, id DESC"

        with get_db_connection() as conn:
            cursor = conn.execute(query, tuple(params) if params else None)
            rows = cursor.fetchall()

        overlays: List[Dict[str, Any]] = []
        for row in rows:
            try:
                parsed = json.loads(row[3]) if row[3] else {}
            except (TypeError, ValueError):
                parsed = {}
            overlays.append(
                {
                    "id": row[0],
                    "strategy": row[1],
                    "regime": row[2],
                    "params": parsed,
                    "objective": row[4],
                    "objective_value": row[5],
                    "trade_count": row[6],
                    "study_name": row[7],
                    "active": row[8],
                    "created_at": str(row[9]) if row[9] is not None else None,
                }
            )
        return overlays

    def export_regime_param_overlays(
        self, path: Optional[str] = None
    ) -> str:
        """Write the active overlays to a JSON file for inspection.

        Args:
            path: Output path (default: env REGIME_OVERLAY_EXPORT_PATH,
                then config/regime_param_overlays.json at the project
                root).

        Returns:
            The path written.
        """
        if path is None:
            path = os.getenv("REGIME_OVERLAY_EXPORT_PATH") or None
        if path is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(
                project_root, "config", "regime_param_overlays.json"
            )

        overlays = self.get_regime_param_overlays(active_only=True)
        payload = {
            "exported_at": datetime.now().isoformat(),
            "active_overlays": overlays,
        }

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)

        logger.info(
            f"Exported {len(overlays)} active regime overlay(s) to {path}"
        )
        return path

    def get_closed_trades_for_weights(self) -> List[Dict[str, Any]]:
        """Fetch closed trades with the fields adaptive weighting needs.

        Returns:
            List of dicts with strategy, regime, pnl, entry_price,
            quantity, entry_time, exit_time for all closed trades.
        """
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                SELECT strategy, regime, pnl, entry_price, quantity,
                       entry_time, exit_time
                FROM trades
                WHERE status = 'closed'
                """
            )
            rows = cursor.fetchall()

        return [
            {
                "strategy": row[0],
                "regime": row[1],
                "pnl": row[2],
                "entry_price": row[3],
                "quantity": row[4],
                "entry_time": row[5],
                "exit_time": row[6],
            }
            for row in rows
        ]

    def get_open_trades(
        self, symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Fetch open trades including their owning strategy and regime tag.

        Used by the regime-flip position review to map open positions back
        to the strategy that opened them.

        Args:
            symbol: Optional symbol filter.

        Returns:
            List of dicts with id, symbol, side, quantity, entry_price,
            strategy, regime, entry_time.
        """
        query = (
            "SELECT id, symbol, side, quantity, entry_price, strategy, "
            "regime, entry_time FROM trades WHERE status = 'open'"
        )
        params: List[Any] = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        query += " ORDER BY entry_time DESC"

        with get_db_connection() as conn:
            cursor = conn.execute(query, tuple(params) if params else None)
            rows = cursor.fetchall()

        return [
            {
                "id": row[0],
                "symbol": row[1],
                "side": row[2],
                "quantity": row[3],
                "entry_price": row[4],
                "strategy": row[5],
                "regime": row[6],
                "entry_time": str(row[7]) if row[7] is not None else None,
            }
            for row in rows
        ]

    def get_trades(
        self, limit: int = 100, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get recent trades with caching."""
        cache_key = f"trades_{status or 'all'}_{limit}"

        # Try cache first for small requests
        if limit <= 20:
            cached = _data_cache.get(cache_key)
            if cached:
                return cached

        with get_db_connection() as conn:
            if status:
                cursor = conn.execute(
                    """
                    SELECT
                        id, symbol, asset_class, side, quantity, entry_price, exit_price,
                        pnl, entry_time, exit_time, 'trade' as type, status
                    FROM trades
                    WHERE status = ?
                    ORDER BY entry_time DESC LIMIT ?
                """,
                    (status, limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT
                        id, symbol, asset_class, side, quantity, entry_price, exit_price,
                        pnl, entry_time, exit_time, 'trade' as type, status
                    FROM trades
                    ORDER BY entry_time DESC LIMIT ?
                """,
                    (limit,),
                )

            result = [dict(row) for row in cursor.fetchall()]

            # Cache result for small requests
            if limit <= 20:
                _data_cache.set(cache_key, result, ttl=30)  # Cache for 30 seconds

            return result

    def save_position(self, position_data: Dict[str, Any]) -> Optional[int]:
        """Save or update a position."""
        funding_pnl_received = position_data.get("funding_pnl", "KEY_NOT_FOUND")
        logger.warning(f"🔍 save_position RECEIVED: funding_pnl={funding_pnl_received}")
        with get_db_connection() as conn:
            # Try to update existing position first
            funding_value = position_data.get("funding_pnl", 0)
            logger.warning(
                f"🔍 save_position EXTRACTED: funding_value={funding_value}, type={type(funding_value)}"
            )
            cursor = conn.execute(
                """
                UPDATE positions
                SET quantity = ?, entry_price = ?, current_price = ?, unrealized_pnl = ?, funding_pnl = ?, updated_at = CURRENT_TIMESTAMP
                WHERE symbol = ? AND side = ?
            """,
                (
                    position_data["quantity"],
                    position_data[
                        "entry_price"
                    ],  # Now updates entry_price from Pacifica
                    position_data["current_price"],
                    position_data.get("unrealized_pnl", 0),
                    funding_value,
                    position_data["symbol"],
                    position_data["side"],
                ),
            )
            logger.warning(
                f"🔍 save_position UPDATE: symbol={position_data['symbol']}, rowcount={cursor.rowcount}, funding sent to SQL={funding_value}"
            )

            if cursor.rowcount == 0:
                # Insert new position
                print(
                    f"DEBUG save_position: Inserting new position with funding_pnl={funding_value}"
                )
                cursor = conn.execute(
                    """
                    INSERT INTO positions (symbol, asset_class, side, quantity, entry_price,
                                         current_price, unrealized_pnl, funding_pnl, opened_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        position_data["symbol"],
                        position_data.get("asset_class", "crypto"),
                        position_data["side"],
                        position_data["quantity"],
                        position_data["entry_price"],
                        position_data["current_price"],
                        position_data.get("unrealized_pnl", 0),
                        funding_value,
                        position_data["opened_at"],
                    ),
                )

            conn.commit()
            print(f"DEBUG save_position: COMMITTED to database")

            # Invalidate positions cache
            _data_cache.invalidate("positions_all")

            return cursor.lastrowid  # type: ignore

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get all open positions with caching."""
        cache_key = "positions_all"

        # Try cache first
        cached = _data_cache.get(cache_key)
        if cached:
            logger.debug("get_positions: cache hit")
            return cached

        start_time = time.time()
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM positions
                ORDER BY opened_at DESC
                """
            )
            result = [dict(row) for row in cursor.fetchall()]

        query_time = time.time() - start_time
        logger.info(
            f"get_positions: fetched {len(result)} positions in {query_time:.3f}s"
        )

        # Cache result for 30 seconds
        _data_cache.set(cache_key, result, ttl=30)

        return result

    def close_position(
        self, symbol: str, side: str, exit_price: Optional[float] = None
    ):
        """Mark a position as closed with exit price and realized P&L calculation."""
        with get_db_connection() as conn:
            # First get the current position data
            cursor = conn.execute(
                "SELECT entry_price, unrealized_pnl FROM positions WHERE symbol = ? AND side = ?",
                (symbol, side),
            )
            position = cursor.fetchone()

            if position:
                entry_price = position["entry_price"]
                unrealized_pnl = position["unrealized_pnl"] or 0

                # Use provided exit price or current entry price as fallback
                final_exit_price = exit_price if exit_price is not None else entry_price

                # Calculate realized P&L (unrealized becomes realized)
                realized_pnl = unrealized_pnl

                # Update position as closed (delete the position since we don't have status column)
                conn.execute(
                    "DELETE FROM positions WHERE symbol = ? AND side = ?",
                    (symbol, side),
                )
                conn.commit()  # CRITICAL: Commit the delete!
                logging.info(
                    f"Closed position: {symbol} {side} at ${final_exit_price}, realized P&L: ${realized_pnl}"
                )

                # Invalidate positions cache
                _data_cache.invalidate("positions_all")
            else:
                logging.warning(f"No open position found for {symbol} {side}")

    def clear_positions(self):
        """Clear all positions from the database."""
        with get_db_connection() as conn:
            conn.execute("DELETE FROM positions")
            logging.info("Cleared all positions from database")

            # Invalidate positions cache
            _data_cache.invalidate("positions_all")

    def deduplicate_positions(self):
        """Remove duplicate positions, keeping only the most recent for each symbol/side."""
        with get_db_connection() as conn:
            # Find duplicates and keep only the most recent
            cursor = conn.execute("""
                SELECT symbol, side, COUNT(*) as count
                FROM positions
                WHERE status = 'open' OR status IS NULL
                GROUP BY symbol, side
                HAVING COUNT(*) > 1
            """)

            duplicates = cursor.fetchall()

            for dup in duplicates:
                symbol, side = dup["symbol"], dup["side"]
                logging.info(
                    f"Found {dup['count']} duplicates for {symbol} {side}, keeping most recent"
                )

                # Keep the most recent, delete others
                conn.execute(
                    """
                    DELETE FROM positions
                    WHERE symbol = ? AND side = ? AND id NOT IN (
                        SELECT id FROM positions
                        WHERE symbol = ? AND side = ?
                        ORDER BY opened_at DESC LIMIT 1
                    )
                """,
                    (symbol, side, symbol, side),
                )

            if duplicates:
                logging.info(f"Removed duplicates for {len(duplicates)} position pairs")

                # Invalidate positions cache
                _data_cache.invalidate("positions_all")

            conn.commit()

    def save_market_data(
        self,
        symbol: str,
        price: float,
        volume: Optional[float] = None,
        source: str = "api",
        timestamp: Optional[str] = None,
        extra_data: Optional[Dict[str, Any]] = None,
    ):
        """Save market data point with optional OHLC data."""
        if timestamp is None:
            timestamp = datetime.now().isoformat()

        with get_db_connection() as conn:
            # Insert with extended data support
            conn.execute(
                """
                INSERT INTO market_data (symbol, price, volume, timestamp, source)
                VALUES (?, ?, ?, ?, ?)
            """,
                (symbol, price, volume, timestamp, source),
            )
            conn.commit()

            # Invalidate related caches
            _data_cache.invalidate(f"market_data_{symbol}_1")
            _data_cache.invalidate(f"market_data_{symbol}_5")
            _data_cache.invalidate(f"market_data_{symbol}_10")

    def bulk_save_market_data(self, data_points: List[Dict[str, Any]]):
        """Bulk save multiple market data points efficiently."""
        if not data_points:
            return

        with get_db_connection() as conn:
            conn.executemany(
                """
                INSERT INTO market_data (symbol, price, volume, timestamp, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        point["symbol"],
                        point["price"],
                        point.get("volume"),
                        point.get("timestamp", datetime.now().isoformat()),
                        point.get("source", "bulk_import"),
                    )
                    for point in data_points
                ],
            )
            conn.commit()

            # Invalidate caches for affected symbols
            symbols = set(point["symbol"] for point in data_points)
            for symbol in symbols:
                _data_cache.invalidate(f"market_data_{symbol}_1")
                _data_cache.invalidate(f"market_data_{symbol}_5")
                _data_cache.invalidate(f"market_data_{symbol}_10")

    def save_market_data_async(
        self,
        symbol: str,
        price: float,
        volume: Optional[float] = None,
        source: str = "api",
        extra_data: Optional[Dict[str, Any]] = None,
    ):
        """Async version of save_market_data for compatibility."""
        # For now, just call the sync version
        # In a real implementation, this would use async database operations
        return self.save_market_data(
            symbol, price, volume, source, extra_data=extra_data
        )

    def get_market_data(
        self,
        symbol: str,
        limit: int = 100,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get market data for symbol with optional date range and caching."""
        cache_key = f"market_data_{symbol}_{limit}_{start_date}_{end_date}"

        # Try cache first for recent data
        if (
            limit <= 10 and not start_date and not end_date
        ):  # Only cache small requests without date filters
            cached = _data_cache.get(cache_key)
            if cached:
                return cached

        with get_db_connection() as conn:
            query = """
                SELECT * FROM market_data
                WHERE symbol = ?
            """
            params: List[Any] = [symbol]

            if start_date:
                query += " AND timestamp >= ?"
                params.append(start_date)
            if end_date:
                query += " AND timestamp <= ?"
                params.append(end_date)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)
            result = [dict(row) for row in cursor.fetchall()]

            # Sort by timestamp ascending for time-series analysis
            result.sort(key=lambda x: x["timestamp"])

            # Cache result for small requests
            if limit <= 10 and not start_date and not end_date:
                _data_cache.set(cache_key, result, ttl=60)  # Cache for 1 minute

            return result

    def save_signal(self, signal_data: Dict[str, Any]) -> int:
        """Save a trading signal."""
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO signals (symbol, asset_class, signal_type, strength, indicators, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                (
                    signal_data["symbol"],
                    signal_data.get("asset_class", "crypto"),
                    signal_data["signal_type"],
                    signal_data["strength"],
                    json.dumps(signal_data.get("indicators", {})),
                    signal_data["timestamp"],
                ),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore

    def bulk_save_signals(self, signals_data: List[Dict[str, Any]]) -> int:
        """Bulk save multiple signals efficiently."""
        if not signals_data:
            return 0

        with get_db_connection() as conn:
            conn.executemany(
                """
                INSERT INTO signals (symbol, asset_class, signal_type, strength, indicators, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        signal["symbol"],
                        signal.get("asset_class", "crypto"),
                        signal["signal_type"],
                        signal["strength"],
                        json.dumps(signal.get("indicators", {})),
                        signal["timestamp"],
                    )
                    for signal in signals_data
                ],
            )
            conn.commit()
            return len(signals_data)

    def get_signals(
        self, limit: int = 50, executed: Optional[bool] = None
    ) -> List[Dict[str, Any]]:
        """Get recent signals."""
        with get_db_connection() as conn:
            if executed is not None:
                cursor = conn.execute(
                    """
                    SELECT * FROM signals
                    WHERE executed = ?
                    ORDER BY timestamp DESC LIMIT ?
                """,
                    (executed, limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM signals
                    ORDER BY timestamp DESC LIMIT ?
                """,
                    (limit,),
                )

            signals = []
            for row in cursor.fetchall():
                signal = dict(row)
                try:
                    signal["indicators"] = json.loads(signal["indicators"] or "{}")
                except json.JSONDecodeError as e:
                    logger.warning(
                        f"Invalid JSON in signal indicators for signal {signal.get('id', 'unknown')}: {e}"
                    )
                    signal["indicators"] = {}  # Default to empty dict
                signals.append(signal)
            return signals

    def update_performance_metrics(self, metrics_data: Dict[str, Any]):
        """Update daily performance metrics."""
        with get_db_connection() as conn:
            today = date.today().isoformat()
            conn.execute(
                """
                INSERT OR REPLACE INTO performance_metrics
                (date, total_pnl, win_rate, total_trades, avg_rrr, max_drawdown, sharpe_ratio)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    today,
                    metrics_data.get("total_pnl", 0),
                    metrics_data.get("win_rate", 0),
                    metrics_data.get("total_trades", 0),
                    metrics_data.get("avg_rrr", 0),
                    metrics_data.get("max_drawdown", 0),
                    metrics_data.get("sharpe_ratio"),
                ),
            )
            conn.commit()

    def get_performance_metrics(self, days: int = 30) -> List[Dict[str, Any]]:
        """Get recent performance metrics."""
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM performance_metrics
                ORDER BY date DESC LIMIT ?
            """,
                (days,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def export_data(self) -> Dict[str, Any]:
        """Export all data for backup/migration."""
        return {
            "trades": self.get_trades(limit=10000),  # Get all trades
            "positions": self.get_positions(),
            "signals": self.get_signals(limit=10000),
            "performance": self.get_performance_metrics(days=365),
            "exported_at": datetime.now().isoformat(),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get database statistics."""
        with get_db_connection() as conn:
            stats: Dict[str, Any] = {"backend": _active_backend}

            # Count records in each table
            for table in [
                "trades",
                "positions",
                "market_data",
                "signals",
                "performance_metrics",
            ]:
                cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
                stats[f"{table}_count"] = cursor.fetchone()[0]

            # Get database file size (SQLite only)
            if _active_backend == "sqlite" and os.path.exists(DATABASE_PATH):
                stats["db_size_mb"] = os.path.getsize(DATABASE_PATH) / (1024 * 1024)
            elif _active_backend == "postgres":
                # Get PostgreSQL database size
                try:
                    cursor = conn.execute(
                        "SELECT pg_size_pretty(pg_database_size(current_database()))"
                    )
                    row = cursor.fetchone()
                    if row:
                        stats["db_size"] = row[0]
                except Exception:
                    pass

            # Get date ranges
            for table in ["trades", "market_data", "signals"]:
                try:
                    # Use the appropriate timestamp column
                    ts_col = "entry_time" if table == "trades" else "timestamp"
                    cursor = conn.execute(
                        f"SELECT MIN({ts_col}), MAX({ts_col}) FROM {table}"
                    )
                    min_ts, max_ts = cursor.fetchone()
                    if min_ts and max_ts:
                        stats[f"{table}_date_range"] = {"start": str(min_ts), "end": str(max_ts)}
                except Exception:
                    pass  # Table might not have timestamp column

            return stats

    def validate_data_integrity(self) -> Dict[str, Any]:
        """Validate data integrity and return issues found."""
        issues = {"critical": [], "warnings": [], "info": []}

        with get_db_connection() as conn:
            # Check for orphaned records
            cursor = conn.execute("""
                SELECT COUNT(*) FROM positions p
                LEFT JOIN trades t ON p.symbol = t.symbol
                WHERE t.id IS NULL
            """)
            orphaned_positions = cursor.fetchone()[0]
            if orphaned_positions > 0:
                issues["warnings"].append(
                    f"Found {orphaned_positions} positions without corresponding trades"
                )

            # Check for trades with invalid P&L calculations
            cursor = conn.execute("""
                SELECT COUNT(*) FROM trades
                WHERE pnl IS NOT NULL AND pnl != 0
                AND (entry_price IS NULL OR exit_price IS NULL OR quantity IS NULL)
            """)
            invalid_pnl = cursor.fetchone()[0]
            if invalid_pnl > 0:
                issues["critical"].append(
                    f"Found {invalid_pnl} trades with invalid P&L calculations"
                )

            # Check for duplicate market data timestamps per symbol
            cursor = conn.execute("""
                SELECT symbol, timestamp, COUNT(*) as cnt
                FROM market_data
                GROUP BY symbol, timestamp
                HAVING cnt > 1
            """)
            duplicates = cursor.fetchall()
            if duplicates:
                issues["warnings"].append(
                    f"Found {len(duplicates)} duplicate market data timestamps"
                )

            # Check for signals without indicators
            cursor = conn.execute("""
                SELECT COUNT(*) FROM signals
                WHERE indicators IS NULL OR indicators = '{}' OR indicators = ''
            """)
            empty_signals = cursor.fetchone()[0]
            if empty_signals > 0:
                issues["info"].append(
                    f"Found {empty_signals} signals without indicator data"
                )

            # Check for trades with future timestamps
            now = datetime.now().isoformat()
            cursor = conn.execute(
                """
                SELECT COUNT(*) FROM trades
                WHERE entry_time > ? OR (exit_time IS NOT NULL AND exit_time > ?)
            """,
                (now, now),
            )
            future_trades = cursor.fetchone()[0]
            if future_trades > 0:
                issues["critical"].append(
                    f"Found {future_trades} trades with future timestamps"
                )

        return issues

    def backup_database(self, backup_path: str) -> bool:
        """Create a backup of the database.

        For SQLite: uses VACUUM INTO or file copy.
        For PostgreSQL: uses pg_dump if available, otherwise exports to JSON.
        """
        try:
            # Ensure backup directory exists
            backup_dir = os.path.dirname(backup_path)
            if backup_dir and not os.path.exists(backup_dir):
                os.makedirs(backup_dir, exist_ok=True)

            if _active_backend == "postgres":
                return self._backup_postgres(backup_path)
            else:
                return self._backup_sqlite(backup_path)

        except Exception as e:
            logger.error(f"Failed to create database backup: {e}")
            return False

    def _backup_sqlite(self, backup_path: str) -> bool:
        """Create SQLite backup using VACUUM INTO or file copy."""
        try:
            # SQLite backup using VACUUM INTO (SQLite 3.27+)
            with get_db_connection() as conn:
                conn.execute(f"VACUUM INTO '{backup_path}'")

            logger.info(f"SQLite database backup created at {backup_path}")
            return True

        except Exception as e:
            logger.warning(f"VACUUM INTO backup failed: {e}, trying file copy")
            try:
                import shutil

                shutil.copy2(DATABASE_PATH, backup_path)
                logger.info(f"SQLite database backup created using file copy at {backup_path}")
                return True
            except Exception as e2:
                logger.error(f"File copy backup also failed: {e2}")
                return False

    def _backup_postgres(self, backup_path: str) -> bool:
        """Create PostgreSQL backup using pg_dump or JSON export."""
        import subprocess

        # Try pg_dump first
        try:
            pg_host = os.getenv("PG_HOST", "localhost")
            pg_port = os.getenv("PG_PORT", "5432")
            pg_database = os.getenv("PG_DATABASE", "trading_bot")
            pg_user = os.getenv("PG_USER", "postgres")

            cmd = [
                "pg_dump",
                "-h", pg_host,
                "-p", pg_port,
                "-U", pg_user,
                "-d", pg_database,
                "-f", backup_path,
                "--no-owner",
                "--no-privileges",
            ]

            # Set password via environment variable
            env = os.environ.copy()
            pg_password = os.getenv("PG_PASSWORD", "")
            if pg_password:
                env["PGPASSWORD"] = pg_password

            result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)

            if result.returncode == 0:
                logger.info(f"PostgreSQL backup created via pg_dump at {backup_path}")
                return True
            else:
                logger.warning(f"pg_dump failed: {result.stderr}")

        except FileNotFoundError:
            logger.info("pg_dump not available, using JSON export fallback")
        except Exception as e:
            logger.warning(f"pg_dump failed: {e}")

        # Fallback: export to JSON
        try:
            export_data = self.export_data()
            import json

            with open(backup_path, "w") as f:
                json.dump(export_data, f, indent=2, default=str)

            logger.info(f"PostgreSQL backup created via JSON export at {backup_path}")
            return True

        except Exception as e:
            logger.error(f"JSON export backup also failed: {e}")
            return False

    def restore_database(self, backup_path: str) -> bool:
        """Restore database from backup.

        For SQLite: restores from file backup.
        For PostgreSQL: restores from pg_dump or JSON import.
        """
        if not os.path.exists(backup_path):
            logger.error(f"Backup file does not exist: {backup_path}")
            return False

        try:
            if _active_backend == "postgres":
                return self._restore_postgres(backup_path)
            else:
                return self._restore_sqlite(backup_path)

        except Exception as e:
            logger.error(f"Failed to restore database: {e}")
            return False

    def _restore_sqlite(self, backup_path: str) -> bool:
        """Restore SQLite database from file backup."""
        try:
            import shutil

            # Close all connections first
            global _connection_pool
            _connection_pool.close_all()

            # Replace database file
            shutil.copy2(backup_path, DATABASE_PATH)

            # Reinitialize connection pool
            _connection_pool = ConnectionPool()

            logger.info(f"SQLite database restored from {backup_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to restore SQLite database: {e}")
            return False

    def _restore_postgres(self, backup_path: str) -> bool:
        """Restore PostgreSQL database from pg_dump or JSON import."""
        import subprocess

        # Check if it's a pg_dump file
        try:
            with open(backup_path, "r") as f:
                first_line = f.readline()
        except Exception:
            first_line = ""

        if "PostgreSQL" in first_line or "pg_dump" in first_line:
            # It's a pg_dump file
            try:
                pg_host = os.getenv("PG_HOST", "localhost")
                pg_port = os.getenv("PG_PORT", "5432")
                pg_database = os.getenv("PG_DATABASE", "trading_bot")
                pg_user = os.getenv("PG_USER", "postgres")

                cmd = [
                    "psql",
                    "-h", pg_host,
                    "-p", pg_port,
                    "-U", pg_user,
                    "-d", pg_database,
                    "-f", backup_path,
                ]

                env = os.environ.copy()
                pg_password = os.getenv("PG_PASSWORD", "")
                if pg_password:
                    env["PGPASSWORD"] = pg_password

                result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)

                if result.returncode == 0:
                    logger.info(f"PostgreSQL database restored from pg_dump: {backup_path}")
                    return True
                else:
                    logger.error(f"psql restore failed: {result.stderr}")

            except FileNotFoundError:
                logger.error("psql not available for restore")
            except Exception as e:
                logger.error(f"pg_dump restore failed: {e}")

        # Fallback: try JSON import
        try:
            import json

            with open(backup_path, "r") as f:
                data = json.load(f)

            logger.info("Importing data from JSON backup...")
            # Import trades
            if "trades" in data:
                for trade in data["trades"]:
                    try:
                        self.save_trade(trade)
                    except Exception as e:
                        logger.warning(f"Failed to import trade: {e}")

            # Import positions
            if "positions" in data:
                for position in data["positions"]:
                    try:
                        self.save_position(position)
                    except Exception as e:
                        logger.warning(f"Failed to import position: {e}")

            # Import signals
            if "signals" in data:
                for signal in data["signals"]:
                    try:
                        self.save_signal(signal)
                    except Exception as e:
                        logger.warning(f"Failed to import signal: {e}")

            logger.info("JSON backup import completed")
            return True

        except json.JSONDecodeError:
            logger.error("Backup file is not a valid JSON file")
            return False
        except Exception as e:
            logger.error(f"JSON import failed: {e}")
            return False

    def cleanup_old_data(self, days_to_keep: int = 365) -> Dict[str, int]:
        """Clean up old data beyond retention period."""
        cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).isoformat()

        deleted_counts = {}

        with get_db_connection() as conn:
            # Delete old market data
            cursor = conn.execute(
                "DELETE FROM market_data WHERE timestamp < ?", (cutoff_date,)
            )
            deleted_counts["market_data"] = cursor.rowcount

            # Delete old signals
            cursor = conn.execute(
                "DELETE FROM signals WHERE timestamp < ?", (cutoff_date,)
            )
            deleted_counts["signals"] = cursor.rowcount

            # Delete old performance metrics (keep more history)
            perf_cutoff = (
                datetime.now() - timedelta(days=days_to_keep * 2)
            ).isoformat()
            cursor = conn.execute(
                "DELETE FROM performance_metrics WHERE date < ?", (perf_cutoff,)
            )
            deleted_counts["performance_metrics"] = cursor.rowcount

            conn.commit()

        logger.info(f"Cleaned up old data: {deleted_counts}")
        return deleted_counts

    # Async methods for non-blocking database operations
    async def save_trade_async(self, trade_data: Dict[str, Any]) -> Optional[int]:
        """Async version of save_trade."""
        if not HAS_AIOSQLITE:  # type: ignore
            return self.save_trade(trade_data)

        async with aiosqlite.connect(DATABASE_PATH) as db:  # type: ignore
            await db.execute(
                """
                INSERT INTO trades (symbol, side, quantity, entry_price,
                                  exit_price, entry_time, exit_time, pnl,
                                  commission, strategy, status, regime)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    trade_data["symbol"],
                    trade_data["side"],
                    trade_data["quantity"],
                    trade_data["entry_price"],
                    trade_data.get("exit_price"),
                    trade_data["entry_time"],
                    trade_data.get("exit_time"),
                    trade_data.get("pnl", 0),
                    trade_data.get("commission", 0),
                    trade_data.get("strategy"),
                    trade_data.get("status", "open"),
                    trade_data.get("regime"),
                ),
            )
            await db.commit()
            return db.last_insert_rowid()  # type: ignore

    async def save_signal_async(self, signal_data: Dict[str, Any]) -> int:
        """Async version of save_signal."""
        if not HAS_AIOSQLITE:  # type: ignore
            return self.save_signal(signal_data)

        async with aiosqlite.connect(DATABASE_PATH) as db:  # type: ignore
            await db.execute(
                """
                INSERT INTO signals (symbol, signal_type, strength, indicators, timestamp)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    signal_data["symbol"],
                    signal_data["signal_type"],
                    signal_data["strength"],
                    json.dumps(signal_data.get("indicators", {})),
                    signal_data["timestamp"],
                ),
            )
            await db.commit()
            return db.last_insert_rowid()  # type: ignore

    async def get_recent_trades_async(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Async version of get_trades."""
        if not HAS_AIOSQLITE:  # type: ignore
            return self.get_trades(limit)

        async with aiosqlite.connect(DATABASE_PATH) as db:  # type: ignore
            db.row_factory = aiosqlite.Row  # type: ignore
            async with db.execute(
                """
                SELECT * FROM trades
                ORDER BY entry_time DESC LIMIT ?
            """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    # Account Profile Management

    def create_profile(
        self,
        name: str,
        private_key_encrypted: str,
        public_key: Optional[str] = None,
        is_default: bool = False,
    ) -> int:
        """
        Create a new account profile.

        Args:
            name: Display name for the account
            private_key_encrypted: Encrypted private key
            public_key: Optional public key
            is_default: Whether this should be the default profile

        Returns:
            Profile ID
        """
        with get_db_connection() as conn:
            # If setting as default, clear other defaults first
            if is_default:
                conn.execute("UPDATE account_profiles SET is_default = 0")

            cursor = conn.execute(
                """
                INSERT INTO account_profiles (name, private_key_encrypted, public_key, is_default)
                VALUES (?, ?, ?, ?)
            """,
                (name, private_key_encrypted, public_key, 1 if is_default else 0),
            )
            conn.commit()
            logger.info(f"Created profile '{name}' (ID: {cursor.lastrowid})")
            return cursor.lastrowid  # type: ignore

    def get_all_profiles(self) -> List[Dict[str, Any]]:
        """
        Get all account profiles (without decrypted private keys).

        Returns:
            List of profile dictionaries
        """
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, name, public_key, is_default, created_at, updated_at, last_used_at
                FROM account_profiles
                ORDER BY is_default DESC, last_used_at DESC
            """
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_profile_by_id(
        self, profile_id: int, include_private_key: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Get a specific profile by ID.

        Args:
            profile_id: Profile ID
            include_private_key: Whether to include encrypted private key

        Returns:
            Profile dictionary or None if not found
        """
        with get_db_connection() as conn:
            if include_private_key:
                query = "SELECT * FROM account_profiles WHERE id = ?"
            else:
                query = """
                    SELECT id, name, public_key, is_default, created_at, updated_at, last_used_at
                    FROM account_profiles WHERE id = ?
                """

            cursor = conn.execute(query, (profile_id,))
            row = cursor.fetchone()

            if row:
                # Update last_used_at
                conn.execute(
                    "UPDATE account_profiles SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (profile_id,),
                )
                conn.commit()

            return dict(row) if row else None

    def get_profile_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a profile by name."""
        with get_db_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM account_profiles WHERE name = ?",
                (name,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_profile(
        self,
        profile_id: int,
        name: Optional[str] = None,
        private_key_encrypted: Optional[str] = None,
        public_key: Optional[str] = None,
    ) -> bool:
        """
        Update an existing profile.

        Args:
            profile_id: Profile ID
            name: New name (optional)
            private_key_encrypted: New encrypted private key (optional)
            public_key: New public key (optional)

        Returns:
            True if updated, False if not found
        """
        # Build dynamic update query
        updates = []
        params = []

        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if private_key_encrypted is not None:
            updates.append("private_key_encrypted = ?")
            params.append(private_key_encrypted)
        if public_key is not None:
            updates.append("public_key = ?")
            params.append(public_key)

        if not updates:
            return True  # Nothing to update

        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.append(profile_id)

        with get_db_connection() as conn:
            cursor = conn.execute(
                f"UPDATE account_profiles SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            conn.commit()
            success = cursor.rowcount > 0
            if success:
                logger.info(f"Updated profile ID {profile_id}")
            return success

    def delete_profile(self, profile_id: int) -> bool:
        """
        Delete a profile.

        Args:
            profile_id: Profile ID

        Returns:
            True if deleted, False if not found
        """
        with get_db_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM account_profiles WHERE id = ?", (profile_id,)
            )
            conn.commit()
            success = cursor.rowcount > 0
            if success:
                logger.info(f"Deleted profile ID {profile_id}")
            return success

    def set_default_profile(self, profile_id: int) -> bool:
        """
        Set a profile as the default.

        Args:
            profile_id: Profile ID

        Returns:
            True if set as default, False if not found
        """
        with get_db_connection() as conn:
            # First check if profile exists
            cursor = conn.execute(
                "SELECT id FROM account_profiles WHERE id = ?", (profile_id,)
            )
            if not cursor.fetchone():
                return False

            # Clear all defaults
            conn.execute("UPDATE account_profiles SET is_default = 0")

            # Set new default
            conn.execute(
                "UPDATE account_profiles SET is_default = 1 WHERE id = ?", (profile_id,)
            )
            conn.commit()
            logger.info(f"Set profile ID {profile_id} as default")
            return True

    def get_default_profile(self) -> Optional[Dict[str, Any]]:
        """
        Get the default profile.

        Returns:
            Default profile dictionary or None if no default set
        """
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM account_profiles
                WHERE is_default = 1
                LIMIT 1
            """
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    # ===========================
    # PACIFICA FUNDING TRACKING
    # ===========================

    def init_pacifica_tables(self):
        """
        Initialize Pacifica-specific tables for hourly funding tracking.

        For PostgreSQL: tables are created by schema_timescaledb.sql during init.
        For SQLite: creates tables with IF NOT EXISTS.

        CRITICAL: Tracks funding payments that occur 24 times per day
        """
        if _active_backend == "postgres":
            # PostgreSQL: tables already created by schema_timescaledb.sql
            # Just verify they exist
            with get_db_connection() as conn:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_name IN ('funding_payments', 'pacifica_positions', 'funding_rate_history')"
                )
                count = cursor.fetchone()[0]
                if count < 3:
                    logger.warning("PostgreSQL Pacifica tables missing, creating minimal schema")
                    self._create_minimal_pacifica_pg(conn)
                else:
                    logger.info("PostgreSQL Pacifica funding tracking tables verified")
        else:
            # SQLite: create tables with IF NOT EXISTS
            with get_db_connection() as conn:
                # Subaccount configurations table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS subaccount_configs (
                        subaccount_id TEXT PRIMARY KEY,
                        subaccount_name TEXT NOT NULL,
                        subaccount_public_key TEXT,
                        trading_strategy TEXT NOT NULL DEFAULT 'balanced',
                        max_position_size REAL NOT NULL DEFAULT 10000.0,
                        risk_per_trade REAL NOT NULL DEFAULT 0.02,
                        max_leverage INTEGER NOT NULL DEFAULT 20,
                        enabled BOOLEAN NOT NULL DEFAULT 1,
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Funding payments table - tracks EVERY hourly payment
                conn.execute("""
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
                    )
                """)

                # Enhanced positions table for Pacifica
                conn.execute("""
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
                    )
                """)

                # Funding rate history table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS funding_rate_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        funding_rate REAL NOT NULL,
                        premium_index REAL,
                        interest_rate REAL,
                        timestamp DATETIME NOT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Create indexes for efficient queries
                indexes = [
                    "CREATE INDEX IF NOT EXISTS idx_subaccount_configs_strategy ON subaccount_configs(trading_strategy, enabled)",
                    "CREATE INDEX IF NOT EXISTS idx_funding_payments_position ON funding_payments(position_id, timestamp)",
                    "CREATE INDEX IF NOT EXISTS idx_funding_payments_symbol_time ON funding_payments(symbol, timestamp)",
                    "CREATE INDEX IF NOT EXISTS idx_funding_payments_account ON funding_payments(account_id, timestamp)",
                    "CREATE INDEX IF NOT EXISTS idx_funding_payments_subaccount ON funding_payments(subaccount_id, timestamp)",
                    "CREATE INDEX IF NOT EXISTS idx_pacifica_positions_symbol ON pacifica_positions(symbol)",
                    "CREATE INDEX IF NOT EXISTS idx_pacifica_positions_account ON pacifica_positions(account_id)",
                    "CREATE INDEX IF NOT EXISTS idx_pacifica_positions_subaccount ON pacifica_positions(subaccount_id)",
                    "CREATE INDEX IF NOT EXISTS idx_funding_history_symbol_time ON funding_rate_history(symbol, timestamp)",
                ]

                for index_sql in indexes:
                    conn.execute(index_sql)

                conn.commit()
                logger.info("SQLite Pacifica funding tracking tables initialized")

    def _create_minimal_pacifica_pg(self, conn: _ConnectionWrapper) -> None:
        """Create minimal Pacifica tables for PostgreSQL fallback."""
        sql = """
            CREATE TABLE IF NOT EXISTS subaccount_configs (
                subaccount_id TEXT PRIMARY KEY,
                subaccount_name TEXT NOT NULL,
                subaccount_public_key TEXT,
                trading_strategy TEXT NOT NULL DEFAULT 'balanced',
                max_position_size DOUBLE PRECISION NOT NULL DEFAULT 10000.0,
                risk_per_trade DOUBLE PRECISION NOT NULL DEFAULT 0.02,
                max_leverage INTEGER NOT NULL DEFAULT 20,
                enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS funding_payments (
                id BIGSERIAL,
                account_id TEXT NOT NULL DEFAULT 'sub_1',
                subaccount_id TEXT,
                position_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                funding_rate DOUBLE PRECISION NOT NULL,
                payment_amount DOUBLE PRECISION NOT NULL,
                position_value DOUBLE PRECISION NOT NULL,
                margin_mode TEXT NOT NULL DEFAULT 'cross',
                "timestamp" TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS pacifica_positions (
                id BIGSERIAL PRIMARY KEY,
                account_id TEXT NOT NULL DEFAULT 'sub_1',
                subaccount_id TEXT,
                position_id TEXT NOT NULL UNIQUE,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                size DOUBLE PRECISION NOT NULL,
                entry_price DOUBLE PRECISION NOT NULL,
                current_price DOUBLE PRECISION,
                leverage INTEGER NOT NULL DEFAULT 10,
                margin_mode TEXT NOT NULL DEFAULT 'cross',
                margin_used DOUBLE PRECISION NOT NULL,
                cumulative_funding_paid DOUBLE PRECISION DEFAULT 0.0,
                last_funding_timestamp TIMESTAMPTZ,
                unrealized_pnl DOUBLE PRECISION DEFAULT 0.0,
                liquidation_price DOUBLE PRECISION,
                tick_size DOUBLE PRECISION,
                lot_size DOUBLE PRECISION,
                opened_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                closed_at TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS funding_rate_history (
                id BIGSERIAL,
                symbol TEXT NOT NULL,
                funding_rate DOUBLE PRECISION NOT NULL,
                premium_index DOUBLE PRECISION,
                interest_rate DOUBLE PRECISION,
                "timestamp" TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """
        conn.executescript(sql)
        logger.info("PostgreSQL Pacifica minimal schema created")

    def save_funding_payment(
        self,
        position_id: str,
        symbol: str,
        funding_rate: float,
        payment_amount: float,
        position_value: float,
        margin_mode: str = "cross",
        account_id: str = "sub_1",
        subaccount_id: Optional[str] = None,
        timestamp: Optional[datetime] = None,
    ) -> int:
        """
        Save a single hourly funding payment.

        ⚠️ CRITICAL: Called EVERY HOUR for each open position (24x per day)

        Args:
            position_id: Position ID
            symbol: Market symbol
            funding_rate: Hourly funding rate
            payment_amount: Amount paid/received
            position_value: Position value at time of payment
            margin_mode: "cross" or "isolated"
            account_id: Account ID
            subaccount_id: Subaccount ID (optional)
            timestamp: Payment timestamp (default: now)

        Returns:
            Payment record ID
        """
        if timestamp is None:
            timestamp = datetime.now()

        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO funding_payments
                (account_id, subaccount_id, position_id, symbol, funding_rate, payment_amount,
                 position_value, margin_mode, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    subaccount_id,
                    position_id,
                    symbol,
                    funding_rate,
                    payment_amount,
                    position_value,
                    margin_mode,
                    timestamp.isoformat(),
                ),
            )
            conn.commit()

            logger.debug(
                f"💰 Funding payment saved: {symbol} ${payment_amount:.2f} "
                f"(Rate: {funding_rate * 100:.4f}% per hour) [Subaccount: {subaccount_id or 'default'}]"
            )

            return cursor.lastrowid  # type: ignore

    def bulk_save_funding_payments(
        self, payments: List[Dict[str, Any]], account_id: str = "sub_1"
    ) -> int:
        """
        Bulk save multiple funding payments (for batch processing).

        Args:
            payments: List of payment dicts
            account_id: Account ID

        Returns:
            Number of payments saved
        """
        if not payments:
            return 0

        with get_db_connection() as conn:
            conn.executemany(
                """
                INSERT INTO funding_payments
                (account_id, position_id, symbol, funding_rate, payment_amount,
                 position_value, margin_mode, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        account_id,
                        p["position_id"],
                        p["symbol"],
                        p["funding_rate"],
                        p["payment_amount"],
                        p["position_value"],
                        p.get("margin_mode", "cross"),
                        p.get("timestamp", datetime.now().isoformat()),
                    )
                    for p in payments
                ],
            )
            conn.commit()

            logger.info(f"💰 Bulk saved {len(payments)} funding payments")
            return len(payments)

    def get_funding_payments(
        self,
        position_id: Optional[str] = None,
        symbol: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        account_id: str = "sub_1",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Get funding payment history with filters.

        Args:
            position_id: Filter by position
            symbol: Filter by symbol
            start_time: Start timestamp
            end_time: End timestamp
            account_id: Account ID
            limit: Max results

        Returns:
            List of funding payments
        """
        with get_db_connection() as conn:
            query = """
                SELECT * FROM funding_payments
                WHERE account_id = ?
            """
            params: List[Any] = [account_id]

            if position_id:
                query += " AND position_id = ?"
                params.append(position_id)

            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)

            if start_time:
                query += " AND timestamp >= ?"
                params.append(start_time.isoformat())

            if end_time:
                query += " AND timestamp <= ?"
                params.append(end_time.isoformat())

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_funding_summary(
        self, position_id: str, account_id: str = "sub_1"
    ) -> Dict[str, Any]:
        """
        Get funding payment summary for a position.

        Returns:
            Summary with total paid, payment count, avg rate, etc.
        """
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                SELECT
                    COUNT(*) as payment_count,
                    SUM(payment_amount) as total_paid,
                    AVG(funding_rate) as avg_rate,
                    MIN(funding_rate) as min_rate,
                    MAX(funding_rate) as max_rate,
                    MIN(timestamp) as first_payment,
                    MAX(timestamp) as last_payment
                FROM funding_payments
                WHERE account_id = ? AND position_id = ?
                """,
                (account_id, position_id),
            )

            row = cursor.fetchone()
            if row:
                summary = dict(row)
                # Calculate hourly stats
                if summary["payment_count"] and summary["payment_count"] > 0:
                    summary["funding_per_hour_avg"] = (
                        summary["total_paid"] / summary["payment_count"]
                    )
                    summary["funding_per_day_avg"] = (
                        summary["funding_per_hour_avg"] * 24
                    )
                return summary

            return {
                "payment_count": 0,
                "total_paid": 0.0,
                "avg_rate": 0.0,
                "min_rate": 0.0,
                "max_rate": 0.0,
            }

    def save_funding_rate_history(
        self,
        symbol: str,
        funding_rate: float,
        premium_index: Optional[float] = None,
        interest_rate: Optional[float] = None,
        timestamp: Optional[datetime] = None,
    ) -> int:
        """
        Save historical funding rate snapshot.

        Args:
            symbol: Market symbol
            funding_rate: Hourly funding rate
            premium_index: Premium index component
            interest_rate: Interest rate component
            timestamp: Snapshot timestamp (default: now)

        Returns:
            Record ID
        """
        if timestamp is None:
            timestamp = datetime.now()

        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO funding_rate_history
                (symbol, funding_rate, premium_index, interest_rate, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    funding_rate,
                    premium_index,
                    interest_rate,
                    timestamp.isoformat(),
                ),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore

    def get_funding_rate_history(
        self,
        symbol: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 168,  # Default: 1 week of hourly rates
    ) -> List[Dict[str, Any]]:
        """
        Get historical funding rates for a symbol.

        Args:
            symbol: Market symbol
            start_time: Start timestamp
            end_time: End timestamp
            limit: Max results (default: 168 = 1 week of hourly rates)

        Returns:
            List of funding rate snapshots
        """
        with get_db_connection() as conn:
            query = "SELECT * FROM funding_rate_history WHERE symbol = ?"
            params: List[Any] = [symbol]

            if start_time:
                query += " AND timestamp >= ?"
                params.append(start_time.isoformat())

            if end_time:
                query += " AND timestamp <= ?"
                params.append(end_time.isoformat())

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def save_pacifica_position(
        self,
        position_id: str,
        symbol: str,
        side: str,
        quantity: float,
        entry_price: float,
        leverage: int,
        margin_mode: str,
        margin_used: float,
        account_id: str = "sub_1",
    ) -> int:
        """
        Save Pacifica position with funding tracking.

        Args:
            position_id: Unique position ID
            symbol: Market symbol
            side: "long" or "short"
            quantity: Position size
            entry_price: Entry price
            leverage: Leverage (5-50)
            margin_mode: "cross" or "isolated"
            margin_used: Margin allocated
            account_id: Account ID

        Returns:
            Record ID
        """
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR REPLACE INTO pacifica_positions
                (account_id, position_id, symbol, side, quantity, entry_price,
                 leverage, margin_mode, margin_used, opened_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    position_id,
                    symbol,
                    side,
                    quantity,
                    entry_price,
                    leverage,
                    margin_mode,
                    margin_used,
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore

    def update_pacifica_position_funding(
        self,
        position_id: str,
        cumulative_funding_paid: float,
        last_funding_timestamp: datetime,
        account_id: str = "sub_1",
    ):
        """
        Update position's cumulative funding after hourly payment.

        Args:
            position_id: Position ID
            cumulative_funding_paid: Total funding paid to date
            last_funding_timestamp: Timestamp of last funding payment
            account_id: Account ID
        """
        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE pacifica_positions
                SET cumulative_funding_paid = ?,
                    last_funding_timestamp = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE account_id = ? AND position_id = ?
                """,
                (
                    cumulative_funding_paid,
                    last_funding_timestamp.isoformat(),
                    account_id,
                    position_id,
                ),
            )
            conn.commit()

    def close_pacifica_position(self, position_id: str, account_id: str = "sub_1"):
        """
        Mark Pacifica position as closed.

        Args:
            position_id: Position ID
            account_id: Account ID
        """
        with get_db_connection() as conn:
            conn.execute(
                """
                UPDATE pacifica_positions
                SET closed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE account_id = ? AND position_id = ?
                """,
                (account_id, position_id),
            )
            conn.commit()

    def get_pacifica_positions(
        self, account_id: str = "sub_1", open_only: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get Pacifica positions with funding information.

        Args:
            account_id: Account ID
            open_only: Only return open positions

        Returns:
            List of positions with funding data
        """
        with get_db_connection() as conn:
            query = "SELECT * FROM pacifica_positions WHERE account_id = ?"
            params = [account_id]

            if open_only:
                query += " AND closed_at IS NULL"

            query += " ORDER BY opened_at DESC"

            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
