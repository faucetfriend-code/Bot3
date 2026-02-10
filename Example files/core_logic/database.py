"""
Database management for trading bot.
Provides persistent storage with SQLite backend.

⚠️ CRITICAL: Includes hourly funding tracking for Pacifica.fi (24x per day)
"""

import sqlite3
import json
import os
import threading
import time
from contextlib import contextmanager
from typing import Generator, List, Dict, Any, Optional
from datetime import datetime, date, timedelta
import logging

logger = logging.getLogger(__name__)

DATABASE_PATH = os.path.abspath(os.getenv("DATABASE_PATH", "data/trading_bot.db"))

# Check for aiosqlite availability
try:
    import aiosqlite

    HAS_AIOSQLITE = True
except ImportError:
    HAS_AIOSQLITE = False
    logger.warning("aiosqlite not available, async operations will be synchronous")


class DataCache:
    """Simple in-memory cache with TTL for frequently accessed data."""

    def __init__(self, max_size: int = 1000, default_ttl: int = 300):
        self.cache = {}
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
        self._connections = []
        self._lock = threading.Lock()
        self._local = threading.local()

    def get_connection(self) -> sqlite3.Connection:
        """Get a connection from the pool."""
        # Check for thread-local connection first
        if hasattr(self._local, "connection"):
            conn = self._local.connection
            if self._is_connection_valid(conn):
                return conn

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

            # Wait for a connection to become available (simple spin wait)
            while True:
                for conn_info in self._connections:
                    if not conn_info["in_use"]:
                        conn_info["in_use"] = True
                        conn_info["last_used"] = time.time()
                        self._local.connection = conn_info["connection"]
                        return conn_info["connection"]
                time.sleep(0.01)  # Small delay to prevent busy waiting

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


# Global connection pool
_connection_pool = ConnectionPool()


@contextmanager
def get_db_connection() -> Generator[sqlite3.Connection, None, None]:
    """Database connection context manager with pooling."""
    conn = _connection_pool.get_connection()
    try:
        yield conn
    finally:
        _connection_pool.release_connection(conn)


def init_database():
    """Initialize database with schema and performance indexes."""
    try:
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
                logger.warning(f"schema.sql not found at {schema_path}, creating minimal schema")
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

            # Add account_id columns to existing tables if they don't exist
            alter_statements = [
                "ALTER TABLE trades ADD COLUMN account_id TEXT NOT NULL DEFAULT 'sub_1'",
                "ALTER TABLE positions ADD COLUMN account_id TEXT NOT NULL DEFAULT 'sub_1'",
                "ALTER TABLE signals ADD COLUMN account_id TEXT NOT NULL DEFAULT 'sub_1'",
                "ALTER TABLE performance_metrics ADD COLUMN account_id TEXT NOT NULL DEFAULT 'sub_1'",
            ]

            for alter_sql in alter_statements:
                try:
                    conn.execute(alter_sql)
                except Exception as e:
                    # Column might already exist, ignore error
                    if "duplicate column name" not in str(e).lower():
                        logger.warning(f"Failed to add account_id column: {e}")

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
        logger.info("Database initialized successfully with performance indexes")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise


class DatabaseManager:
    """Database operations for trading bot."""

    def __init__(self):
        """Initialize database manager."""
        # Ensure database directory exists
        db_dir = os.path.dirname(DATABASE_PATH)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        # Always run init_database() to ensure all tables exist
        # (uses CREATE TABLE IF NOT EXISTS, safe to run multiple times)
        init_database()

        # Store a connection reference for execute/commit pattern
        self._conn = None

    def execute(self, sql: str, params: tuple = None):
        """
        Execute SQL statement and return cursor.

        Args:
            sql: SQL query to execute
            params: Query parameters

        Returns:
            Cursor object
        """
        if self._conn is None:
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
            self._conn.close()
            self._conn = None

    def get_connection(self):
        """
        Get an async database connection context manager for aiosqlite.

        Returns an async context manager that provides an aiosqlite connection.
        Used by MarketDataCollector and other async database operations.

        Usage:
            async with db_manager.get_connection() as conn:
                await conn.execute(...)
        """
        if not HAS_AIOSQLITE:
            raise RuntimeError("aiosqlite not available - async operations require aiosqlite")
        return aiosqlite.connect(DATABASE_PATH)

    def save_trade(self, trade_data: Dict[str, Any], account_id: str = "sub_1") -> int:
        """Save a trade to database."""
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades (account_id, symbol, asset_class, side, quantity, entry_price,
                                  exit_price, entry_time, exit_time, pnl,
                                  commission, strategy, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            conn.commit()

            # Invalidate related caches
            _data_cache.invalidate(f"trades_recent_10_{account_id}")
            _data_cache.invalidate(f"trades_recent_20_{account_id}")
            _data_cache.invalidate(f"trades_all_10_{account_id}")
            _data_cache.invalidate(f"trades_all_20_{account_id}")

            return cursor.lastrowid

    def bulk_save_trades(self, trades_data: List[Dict[str, Any]], account_id: str = "sub_1") -> int:
        """Bulk save multiple trades efficiently."""
        if not trades_data:
            return 0

        with get_db_connection() as conn:
            conn.executemany(
                """
                INSERT INTO trades (account_id, symbol, asset_class, side, quantity, entry_price,
                                  exit_price, entry_time, exit_time, pnl,
                                  commission, strategy, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                conn.execute(query, values)
                conn.commit()

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
                    SELECT * FROM trades
                    WHERE status = ?
                    ORDER BY entry_time DESC LIMIT ?
                """,
                    (status, limit),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM trades
                    ORDER BY entry_time DESC LIMIT ?
                """,
                    (limit,),
                )

            result = [dict(row) for row in cursor.fetchall()]

            # Cache result for small requests
            if limit <= 20:
                _data_cache.set(cache_key, result, ttl=30)  # Cache for 30 seconds

            return result

    def save_position(self, position_data: Dict[str, Any]) -> int:
        """Save or update a position."""
        with get_db_connection() as conn:
            # Try to update existing position first
            cursor = conn.execute(
                """
                UPDATE positions
                SET quantity = ?, current_price = ?, unrealized_pnl = ?, funding_pnl = ?, updated_at = CURRENT_TIMESTAMP
                WHERE symbol = ? AND side = ?
            """,
                (
                    position_data["quantity"],
                    position_data["current_price"],
                    position_data.get("unrealized_pnl", 0),
                    position_data.get("funding_pnl", 0),
                    position_data["symbol"],
                    position_data["side"],
                ),
            )

            if cursor.rowcount == 0:
                # Insert new position
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
                        position_data.get("funding_pnl", 0),
                        position_data["opened_at"],
                    ),
                )

            conn.commit()
            return cursor.lastrowid

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get all open positions."""
        with get_db_connection() as conn:
            cursor = conn.execute(
                """
                SELECT * FROM positions
                WHERE quantity > 0
                ORDER BY opened_at DESC
            """
            )
            return [dict(row) for row in cursor.fetchall()]

    def close_position(self, symbol: str, side: str):
        """Close a position."""
        with get_db_connection() as conn:
            conn.execute(
                """
                DELETE FROM positions
                WHERE symbol = ? AND side = ?
            """,
                (symbol, side),
            )
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
        return self.save_market_data(symbol, price, volume, source, extra_data=extra_data)

    def get_market_data(
        self,
        symbol: str,
        limit: int = 100,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get market data for symbol with optional date range and caching."""
        cache_key = f"market_data_{symbol}_{limit}_{start_date}_{end_date}"

        # Try cache first for recent data
        if limit <= 10 and not start_date and not end_date:  # Only cache small requests without date filters
            cached = _data_cache.get(cache_key)
            if cached:
                return cached

        with get_db_connection() as conn:
            query = """
                SELECT * FROM market_data
                WHERE symbol = ?
            """
            params = [symbol]

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
            result.sort(key=lambda x: x['timestamp'])

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
            return cursor.lastrowid

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
                    logger.warning(f"Invalid JSON in signal indicators for signal {signal.get('id', 'unknown')}: {e}")
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
            stats = {}

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

            # Get database file size
            if os.path.exists(DATABASE_PATH):
                stats["db_size_mb"] = os.path.getsize(DATABASE_PATH) / (1024 * 1024)

            # Get date ranges
            for table in ["trades", "market_data", "signals"]:
                try:
                    cursor = conn.execute(f"SELECT MIN(timestamp), MAX(timestamp) FROM {table}")
                    min_ts, max_ts = cursor.fetchone()
                    if min_ts and max_ts:
                        stats[f"{table}_date_range"] = {
                            "start": min_ts,
                            "end": max_ts
                        }
                except Exception:
                    pass  # Table might not have timestamp column

            return stats

    def validate_data_integrity(self) -> Dict[str, Any]:
        """Validate data integrity and return issues found."""
        issues = {
            "critical": [],
            "warnings": [],
            "info": []
        }

        with get_db_connection() as conn:
            # Check for orphaned records
            cursor = conn.execute("""
                SELECT COUNT(*) FROM positions p
                LEFT JOIN trades t ON p.symbol = t.symbol
                WHERE t.id IS NULL
            """)
            orphaned_positions = cursor.fetchone()[0]
            if orphaned_positions > 0:
                issues["warnings"].append(f"Found {orphaned_positions} positions without corresponding trades")

            # Check for trades with invalid P&L calculations
            cursor = conn.execute("""
                SELECT COUNT(*) FROM trades
                WHERE pnl IS NOT NULL AND pnl != 0
                AND (entry_price IS NULL OR exit_price IS NULL OR quantity IS NULL)
            """)
            invalid_pnl = cursor.fetchone()[0]
            if invalid_pnl > 0:
                issues["critical"].append(f"Found {invalid_pnl} trades with invalid P&L calculations")

            # Check for duplicate market data timestamps per symbol
            cursor = conn.execute("""
                SELECT symbol, timestamp, COUNT(*) as cnt
                FROM market_data
                GROUP BY symbol, timestamp
                HAVING cnt > 1
            """)
            duplicates = cursor.fetchall()
            if duplicates:
                issues["warnings"].append(f"Found {len(duplicates)} duplicate market data timestamps")

            # Check for signals without indicators
            cursor = conn.execute("""
                SELECT COUNT(*) FROM signals
                WHERE indicators IS NULL OR indicators = '{}' OR indicators = ''
            """)
            empty_signals = cursor.fetchone()[0]
            if empty_signals > 0:
                issues["info"].append(f"Found {empty_signals} signals without indicator data")

            # Check for trades with future timestamps
            now = datetime.now().isoformat()
            cursor = conn.execute("""
                SELECT COUNT(*) FROM trades
                WHERE entry_time > ? OR (exit_time IS NOT NULL AND exit_time > ?)
            """, (now, now))
            future_trades = cursor.fetchone()[0]
            if future_trades > 0:
                issues["critical"].append(f"Found {future_trades} trades with future timestamps")

        return issues

    def backup_database(self, backup_path: str) -> bool:
        """Create a backup of the database."""
        try:
            import shutil

            # Ensure backup directory exists
            backup_dir = os.path.dirname(backup_path)
            if backup_dir and not os.path.exists(backup_dir):
                os.makedirs(backup_dir, exist_ok=True)

            # SQLite backup using VACUUM INTO (SQLite 3.27+)
            with get_db_connection() as conn:
                conn.execute(f"VACUUM INTO '{backup_path}'")

            logger.info(f"Database backup created at {backup_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to create database backup: {e}")
            # Fallback to file copy
            try:
                shutil.copy2(DATABASE_PATH, backup_path)
                logger.info(f"Database backup created using file copy at {backup_path}")
                return True
            except Exception as e2:
                logger.error(f"File copy backup also failed: {e2}")
                return False

    def restore_database(self, backup_path: str) -> bool:
        """Restore database from backup."""
        if not os.path.exists(backup_path):
            logger.error(f"Backup file does not exist: {backup_path}")
            return False

        try:
            import shutil

            # Close all connections first
            global _connection_pool
            _connection_pool.close_all()

            # Replace database file
            shutil.copy2(backup_path, DATABASE_PATH)

            # Reinitialize connection pool
            _connection_pool = ConnectionPool()

            logger.info(f"Database restored from {backup_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to restore database: {e}")
            return False

    def cleanup_old_data(self, days_to_keep: int = 365) -> Dict[str, int]:
        """Clean up old data beyond retention period."""
        cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).isoformat()

        deleted_counts = {}

        with get_db_connection() as conn:
            # Delete old market data
            cursor = conn.execute(
                "DELETE FROM market_data WHERE timestamp < ?",
                (cutoff_date,)
            )
            deleted_counts["market_data"] = cursor.rowcount

            # Delete old signals
            cursor = conn.execute(
                "DELETE FROM signals WHERE timestamp < ?",
                (cutoff_date,)
            )
            deleted_counts["signals"] = cursor.rowcount

            # Delete old performance metrics (keep more history)
            perf_cutoff = (datetime.now() - timedelta(days=days_to_keep * 2)).isoformat()
            cursor = conn.execute(
                "DELETE FROM performance_metrics WHERE date < ?",
                (perf_cutoff,)
            )
            deleted_counts["performance_metrics"] = cursor.rowcount

            conn.commit()

        logger.info(f"Cleaned up old data: {deleted_counts}")
        return deleted_counts

    # Async methods for non-blocking database operations
    async def save_trade_async(self, trade_data: Dict[str, Any]) -> int:
        """Async version of save_trade."""
        if not HAS_AIOSQLITE:
            return self.save_trade(trade_data)

        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute(
                """
                INSERT INTO trades (symbol, side, quantity, entry_price,
                                  exit_price, entry_time, exit_time, pnl,
                                  commission, strategy, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            await db.commit()
            return db.last_insert_rowid()

    async def save_signal_async(self, signal_data: Dict[str, Any]) -> int:
        """Async version of save_signal."""
        if not HAS_AIOSQLITE:
            return self.save_signal(signal_data)

        async with aiosqlite.connect(DATABASE_PATH) as db:
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
            return db.last_insert_rowid()

    async def get_recent_trades_async(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Async version of get_trades."""
        if not HAS_AIOSQLITE:
            return self.get_trades(limit)

        async with aiosqlite.connect(DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
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
        self, name: str, private_key_encrypted: str, public_key: Optional[str] = None, is_default: bool = False
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
            return cursor.lastrowid

    def get_all_profiles(self) -> List[Dict[str, Any]]:
        """
        Get all account profiles (without decrypted private keys).

        Returns:
            List of profile dictionaries
        """
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT id, name, public_key, is_default, created_at, updated_at, last_used_at
                FROM account_profiles
                ORDER BY is_default DESC, last_used_at DESC
            """
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_profile_by_id(self, profile_id: int, include_private_key: bool = False) -> Optional[Dict[str, Any]]:
        """
        Get a specific profile by ID.

        Args:
            profile_id: Profile ID
            include_private_key: Whether to include encrypted private key

        Returns:
            Profile dictionary or None if not found
        """
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row

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
            conn.row_factory = sqlite3.Row
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
                params,
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
            cursor = conn.execute("DELETE FROM account_profiles WHERE id = ?", (profile_id,))
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
            cursor = conn.execute("SELECT id FROM account_profiles WHERE id = ?", (profile_id,))
            if not cursor.fetchone():
                return False

            # Clear all defaults
            conn.execute("UPDATE account_profiles SET is_default = 0")

            # Set new default
            conn.execute("UPDATE account_profiles SET is_default = 1 WHERE id = ?", (profile_id,))
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
            conn.row_factory = sqlite3.Row
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

        ⚠️ CRITICAL: Tracks funding payments that occur 24 times per day
        """
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
            logger.info("✅ Pacifica funding tracking tables initialized")

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
        timestamp: Optional[datetime] = None
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
                (account_id, subaccount_id, position_id, symbol, funding_rate, payment_amount,
                 position_value, margin_mode, timestamp.isoformat())
            )
            conn.commit()

            logger.debug(
                f"💰 Funding payment saved: {symbol} ${payment_amount:.2f} "
                f"(Rate: {funding_rate*100:.4f}% per hour) [Subaccount: {subaccount_id or 'default'}]"
            )

            return cursor.lastrowid

    def bulk_save_funding_payments(
        self,
        payments: List[Dict[str, Any]],
        account_id: str = "sub_1"
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
                        p.get("timestamp", datetime.now().isoformat())
                    )
                    for p in payments
                ]
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
        limit: int = 100
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
            params = [account_id]

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
        self,
        position_id: str,
        account_id: str = "sub_1"
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
                (account_id, position_id)
            )

            row = cursor.fetchone()
            if row:
                summary = dict(row)
                # Calculate hourly stats
                if summary["payment_count"] and summary["payment_count"] > 0:
                    summary["funding_per_hour_avg"] = summary["total_paid"] / summary["payment_count"]
                    summary["funding_per_day_avg"] = summary["funding_per_hour_avg"] * 24
                return summary

            return {
                "payment_count": 0,
                "total_paid": 0.0,
                "avg_rate": 0.0,
                "min_rate": 0.0,
                "max_rate": 0.0
            }

    def save_funding_rate_history(
        self,
        symbol: str,
        funding_rate: float,
        premium_index: Optional[float] = None,
        interest_rate: Optional[float] = None,
        timestamp: Optional[datetime] = None
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
                (symbol, funding_rate, premium_index, interest_rate, timestamp.isoformat())
            )
            conn.commit()
            return cursor.lastrowid

    def get_funding_rate_history(
        self,
        symbol: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 168  # Default: 1 week of hourly rates
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
            params = [symbol]

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
        account_id: str = "sub_1"
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
                (account_id, position_id, symbol, side, quantity, entry_price,
                 leverage, margin_mode, margin_used, datetime.now().isoformat())
            )
            conn.commit()
            return cursor.lastrowid

    def update_pacifica_position_funding(
        self,
        position_id: str,
        cumulative_funding_paid: float,
        last_funding_timestamp: datetime,
        account_id: str = "sub_1"
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
                (cumulative_funding_paid, last_funding_timestamp.isoformat(),
                 account_id, position_id)
            )
            conn.commit()

    def close_pacifica_position(
        self,
        position_id: str,
        account_id: str = "sub_1"
    ):
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
                (account_id, position_id)
            )
            conn.commit()

    def get_pacifica_positions(
        self,
        account_id: str = "sub_1",
        open_only: bool = True
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
