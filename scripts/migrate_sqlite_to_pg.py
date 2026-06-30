"""
SQLite to PostgreSQL/TimescaleDB migration script for Bot 3.

Reads all data from the existing SQLite database and inserts into PostgreSQL.
Handles type conversions: SQLite strings -> PostgreSQL TIMESTAMPTZ/DOUBLE PRECISION/JSONB.
Creates a backup of the SQLite database before migration.

Usage:
    python scripts/migrate_sqlite_to_pg.py
    python scripts/migrate_sqlite_to_pg.py --sqlite-path data/trading_bot.db --batch-size 500
    python scripts/migrate_sqlite_to_pg.py --dry-run  # Preview without inserting
"""

import argparse
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load .env from project root (one level up from scripts/)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
SQLITE_PATH = os.getenv("DATABASE_PATH", "trading_bot.db")
PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
PG_DB = os.getenv("POSTGRES_DB", "trading_bot")
PG_USER = os.getenv("POSTGRES_USER", "trading_bot")
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", "trading_bot_pass")

# ---------------------------------------------------------------------------
# Table migration order (respects implicit FK dependencies)
# ---------------------------------------------------------------------------
TABLES_TO_MIGRATE: List[str] = [
    "account_profiles",
    "subaccount_configs",
    "trades",
    "positions",
    "market_data",
    "signals",
    "performance_metrics",
    "funding_payments",
    "funding_rate_history",
    "pacifica_positions",
    "grid_state",
    "grid_levels",
    "regime_history",
    "capital_approvals",
    "balance_history",
    "market_info_history",
    "market_parameter_changes",
    "market_availability_history",
]

# ---------------------------------------------------------------------------
# Type conversion helpers
# ---------------------------------------------------------------------------


def _parse_iso_timestamp(value: Any) -> Optional[datetime]:
    """Convert an ISO-8601 string (or datetime) to a timezone-aware datetime.

    Handles the common SQLite formats:
      - '2024-01-15 12:30:45'
      - '2024-01-15T12:30:45'
      - '2024-01-15T12:30:45.123456'
      - '2024-01-15T12:30:45+00:00'
      - Already a datetime object
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, (int, float)):
        # Could be a Unix epoch (seconds) - handle BIGINT timestamps
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OSError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    # Try ISO format with timezone
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass
    # Try common SQLite formats
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    logger.warning(f"Could not parse timestamp: {value!r}")
    return None


def _parse_iso_timestamp_nn(value: Any) -> datetime:
    """Like _parse_iso_timestamp but returns NOW() on None (for non-nullable columns)."""
    result = _parse_iso_timestamp(value)
    if result is None:
        return datetime.now(tz=timezone.utc)
    return result


def _parse_unix_epoch(value: Any) -> Optional[datetime]:
    """Convert a Unix epoch integer/float to TIMESTAMPTZ."""
    if value is None:
        return None
    try:
        ts = float(value)
        if ts <= 0:
            return None
        # Distinguish seconds vs milliseconds (if > 1e12, assume ms)
        if ts > 1e12:
            ts = ts / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (OSError, ValueError, TypeError):
        return None


def _parse_json(value: Any) -> Any:
    """Parse a JSON string into a Python object for JSONB insertion.

    If value is already a dict/list, return it directly.
    If value is a string, attempt json.loads.
    If parsing fails, return an empty dict.
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    s = str(value).strip()
    if not s or s == "{}" or s == "[]":
        return {} if s != "[]" else []
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        logger.warning(f"Invalid JSON, defaulting to empty object: {s[:100]}")
        return {}


def _to_float(value: Any) -> Optional[float]:
    """Cast to float, returning None for NULL/empty/non-numeric."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _to_int(value: Any) -> Optional[int]:
    """Cast to int, returning None for NULL/empty."""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def _to_bool(value: Any) -> bool:
    """Cast to bool, handling SQLite 0/1 convention."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    s = str(value).strip().lower()
    return s in ("1", "true", "yes", "t", "y")


# ---------------------------------------------------------------------------
# Per-table column mapping: sqlite_col -> (pg_col, converter)
# None as pg_col means the column is skipped (e.g., auto-generated id).
# ---------------------------------------------------------------------------

# Columns that PostgreSQL auto-generates via BIGSERIAL -- we omit them from
# the INSERT so PG assigns new IDs.
_SKIP_ID_COLUMNS = {"id"}

# Generic mapping: skip `id`, convert everything else with sensible defaults.
# Tables that need special handling override this via _TABLE_TRANSFORMS.

_TABLE_TRANSFORMS: Dict[str, Dict[str, Tuple[Optional[str], Any]]] = {
    # ---- market_data: SQLite has `price`; PG has open/high/low/close + timeframe
    "market_data": {
        "id": (None, None),
        "symbol": ("symbol", str),
        "price": ("close", _to_float),  # single price -> close
        "volume": ("volume", _to_float),
        "timestamp": ("timestamp", _parse_iso_timestamp_nn),
        "source": ("source", str),
        # Extra PG columns not in SQLite -- handled via _DEFAULT_VALUES
    },
    # ---- signals: PG has extra columns (strategy, side, confidence, timeframe, regime)
    "signals": {
        "id": (None, None),
        "account_id": ("account_id", str),
        "symbol": ("symbol", str),
        "asset_class": ("asset_class", str),
        "signal_type": ("signal_type", str),
        "strength": ("strength", _to_float),
        "indicators": ("indicators", _parse_json),
        "timestamp": ("timestamp", _parse_iso_timestamp_nn),
        "executed": ("executed", _to_bool),
        "created_at": ("created_at", _parse_iso_timestamp_nn),
    },
    # ---- performance_metrics: SQLite `date` -> PG `metric_date`
    "performance_metrics": {
        "id": (None, None),
        "account_id": ("account_id", str),
        "date": ("metric_date", str),  # DATE column -- keep as string 'YYYY-MM-DD'
        "total_pnl": ("total_pnl", _to_float),
        "win_rate": ("win_rate", _to_float),
        "total_trades": ("total_trades", _to_int),
        "avg_rrr": ("avg_rrr", _to_float),
        "max_drawdown": ("max_drawdown", _to_float),
        "sharpe_ratio": ("sharpe_ratio", _to_float),
        "created_at": ("created_at", _parse_iso_timestamp_nn),
    },
    # ---- balance_history: timestamp is BIGINT (Unix epoch)
    "balance_history": {
        "id": (None, None),
        "account_id": ("account_id", str),
        "subaccount_id": ("subaccount_id", str),
        "balance": ("balance", _to_float),
        "equity": ("equity", _to_float),
        "available_balance": ("available_balance", _to_float),
        "margin_used": ("margin_used", _to_float),
        "timestamp": ("timestamp", _parse_unix_epoch),
        "fetched_at": ("created_at", _parse_iso_timestamp),
    },
    # ---- market_info_history: created_at is BIGINT, PG has raw_data JSONB
    "market_info_history": {
        "id": (None, None),
        "symbol": ("symbol", str),
        "tick_size": ("tick_size", str),
        "min_tick": ("min_tick", str),
        "max_tick": ("max_tick", str),
        "lot_size": ("lot_size", str),
        "max_leverage": ("max_leverage", _to_int),
        "isolated_only": ("isolated_only", _to_bool),
        "min_order_size": ("min_order_size", str),
        "max_order_size": ("max_order_size", str),
        "funding_rate": ("funding_rate", str),
        "next_funding_rate": ("next_funding_rate", str),
        "fetched_at": ("fetched_at", _parse_iso_timestamp),
    },
    # ---- grid_state: PG has extra columns with defaults
    "grid_state": {
        "id": (None, None),
        "symbol": ("symbol", str),
        "grid_capital": ("grid_capital", _to_float),
        "emergency_stop_price": ("emergency_stop_price", _to_float),
        "active_levels": ("active_levels", _to_int),
        "total_levels": ("total_levels", _to_int),
        "status": ("status", str),
        "created_at": ("created_at", _parse_iso_timestamp),
        "updated_at": ("updated_at", _parse_iso_timestamp),
        "closed_at": ("closed_at", _parse_iso_timestamp),
    },
    # ---- grid_levels: grid_id needs remapping after grid_state migration
    "grid_levels": {
        "id": (None, None),
        "grid_id": ("grid_id", _to_int),  # Remapped in _migrate_grid_levels
        "level_number": ("level_number", _to_int),
        "side": ("side", str),
        "price": ("price", _to_float),
        "quantity": ("quantity", _to_float),
        "capital_allocated": ("capital_allocated", _to_float),
        "order_id": ("order_id", str),
        "status": ("status", str),
        "created_at": ("created_at", _parse_iso_timestamp),
        "updated_at": ("updated_at", _parse_iso_timestamp),
    },
    # ---- funding_payments: all columns map directly
    "funding_payments": {
        "id": (None, None),
        "account_id": ("account_id", str),
        "subaccount_id": ("subaccount_id", str),
        "position_id": ("position_id", str),
        "symbol": ("symbol", str),
        "funding_rate": ("funding_rate", _to_float),
        "payment_amount": ("payment_amount", _to_float),
        "position_value": ("position_value", _to_float),
        "margin_mode": ("margin_mode", str),
        "timestamp": ("timestamp", _parse_iso_timestamp),
        "created_at": ("created_at", _parse_iso_timestamp),
    },
}

# Default values for PG columns that don't exist in SQLite (extra columns)
_DEFAULT_VALUES: Dict[str, Dict[str, Any]] = {
    "market_data": {
        "open": None,
        "high": None,
        "low": None,
        "timeframe": "1m",
        "created_at": datetime.now(tz=timezone.utc),
    },
    "signals": {
        "strategy": None,
        "side": None,
        "confidence": 0.0,
        "timeframe": None,
        "regime": None,
        "execution_price": None,
    },
    "performance_metrics": {
        "winning_trades": 0,
        "losing_trades": 0,
        "sortino_ratio": None,
        "calmar_ratio": None,
        "profit_factor": None,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "tags": {},
    },
    "market_info_history": {
        "raw_data": None,
    },
    "grid_state": {
        "center_price": None,
        "initial_center": None,
        "orders_placed": 0,
        "refresh_count": 0,
        "last_refresh": None,
        "consistency_checked_at": None,
        "repair_history": None,
        "atr_at_creation": None,
        "grid_spacing": None,
        "realized_pnl": 0.0,
        "total_fees": 0.0,
    },
    "grid_levels": {},
}


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


@contextmanager
def _get_sqlite_conn(db_path: str):
    """Open an SQLite connection with Row factory."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _get_pg_conn():
    """Open a PostgreSQL connection with RealDictCursor."""
    conn = psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASSWORD,
        options="-c statement_timeout=300000",  # 5-minute statement timeout
    )
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _get_pg_conn_args(
    host: str, port: int, dbname: str, user: str, password: str
):
    """Open a PostgreSQL connection with explicit parameters."""
    conn = psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        options="-c statement_timeout=300000",
    )
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------


def _get_sqlite_tables(sqlite_conn: sqlite3.Connection) -> List[str]:
    """Return list of user tables in the SQLite database."""
    cursor = sqlite_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [row["name"] for row in cursor.fetchall()]


def _get_sqlite_columns(sqlite_conn: sqlite3.Connection, table: str) -> List[str]:
    """Return ordered column names for a SQLite table."""
    cursor = sqlite_conn.execute(f"PRAGMA table_info('{table}')")
    return [row["name"] for row in cursor.fetchall()]


def _get_sqlite_row_count(sqlite_conn: sqlite3.Connection, table: str) -> int:
    """Return total row count for a SQLite table."""
    cursor = sqlite_conn.execute(f"SELECT COUNT(*) AS cnt FROM '{table}'")
    return cursor.fetchone()["cnt"]


def _get_pg_tables(pg_conn: psycopg2.extensions.connection) -> List[str]:
    """Return list of user tables in the PostgreSQL database."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Migration logic
# ---------------------------------------------------------------------------


def _build_column_map(
    sqlite_columns: List[str],
    table_name: str,
) -> Tuple[List[str], List[Optional[str]], List[Any]]:
    """Build the (pg_columns, sqlite_cols, converters) for a table.

    Returns:
        pg_cols:     Column names for the PG INSERT
        sqlite_cols: Matching column names to read from SQLite rows
        converters:  One converter callable per column pair
    """
    transform = _TABLE_TRANSFORMS.get(table_name, {})
    pg_cols: List[str] = []
    sqlite_cols: List[Optional[str]] = []
    converters: List[Any] = []

    for col in sqlite_columns:
        if col in _SKIP_ID_COLUMNS:
            continue
        if col in transform:
            pg_col, converter = transform[col]
            if pg_col is None:
                # Column is skipped
                continue
            pg_cols.append(pg_col)
            sqlite_cols.append(col)
            converters.append(converter)
        else:
            # Default: same name, identity converter
            pg_cols.append(col)
            sqlite_cols.append(col)
            converters.append(lambda v: v)

    # Append extra PG columns that don't exist in SQLite
    defaults = _DEFAULT_VALUES.get(table_name, {})
    existing_pg = set(pg_cols)
    for pg_col, default_val in defaults.items():
        if pg_col not in existing_pg:
            pg_cols.append(pg_col)
            sqlite_cols.append(None)  # sentinel: no SQLite source
            converters.append(lambda v, dv=default_val: dv)

    return pg_cols, sqlite_cols, converters


def _transform_row(
    sqlite_row: sqlite3.Row,
    sqlite_cols: Sequence[Optional[str]],
    converters: List[Any],
) -> Tuple[Any, ...]:
    """Transform a single SQLite row into a tuple of PG values."""
    values: List[Any] = []
    for col, converter in zip(sqlite_cols, converters):
        if col is None:
            # Extra PG column with default -- converter returns the default
            values.append(converter(None))
        else:
            raw = sqlite_row[col]
            values.append(converter(raw))
    return tuple(values)


def _migrate_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn: psycopg2.extensions.connection,
    table_name: str,
    batch_size: int = 1000,
    dry_run: bool = False,
) -> int:
    """Migrate a single table from SQLite to PostgreSQL.

    Returns the number of rows migrated.
    """
    sqlite_columns = _get_sqlite_columns(sqlite_conn, table_name)
    row_count = _get_sqlite_row_count(sqlite_conn, table_name)

    if row_count == 0:
        logger.info(f"  {table_name}: 0 rows (skipping)")
        return 0

    pg_cols, sqlite_cols, converters = _build_column_map(sqlite_columns, table_name)

    if not pg_cols:
        logger.warning(f"  {table_name}: no mappable columns, skipping")
        return 0

    # Build INSERT statement
    placeholders = ", ".join(["%s"] * len(pg_cols))
    col_list = ", ".join(f'"{c}"' for c in pg_cols)
    insert_sql = (
        f"INSERT INTO {table_name} ({col_list}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT DO NOTHING"
    )

    if dry_run:
        logger.info(
            f"  {table_name}: {row_count} rows (dry-run, {len(pg_cols)} columns)"
        )
        return row_count

    migrated = 0
    cursor = sqlite_conn.execute(f"SELECT * FROM '{table_name}'")
    pg_cursor = pg_conn.cursor()

    batch: List[Tuple[Any, ...]] = []
    while True:
        row = cursor.fetchone()
        if row is None:
            break
        try:
            values = _transform_row(row, sqlite_cols, converters)
            batch.append(values)
        except Exception as e:
            logger.warning(f"  {table_name}: row transform error: {e}")
            continue

        if len(batch) >= batch_size:
            psycopg2.extras.execute_batch(
                pg_cursor, insert_sql, batch, page_size=batch_size
            )
            migrated += len(batch)
            logger.info(f"  {table_name}: {migrated}/{row_count} rows migrated")
            batch.clear()

    # Flush remaining rows
    if batch:
        psycopg2.extras.execute_batch(
            pg_cursor, insert_sql, batch, page_size=batch_size
        )
        migrated += len(batch)

    pg_conn.commit()
    logger.info(f"  {table_name}: {migrated}/{row_count} rows migrated (complete)")
    return migrated


def _migrate_grid_levels_with_remapping(
    sqlite_conn: sqlite3.Connection,
    pg_conn: psycopg2.extensions.connection,
    batch_size: int = 1000,
    dry_run: bool = False,
) -> int:
    """Migrate grid_levels with grid_id remapping.

    After grid_state is migrated, PG assigns new IDs. We need to map old
    SQLite grid_state.id -> new PG grid_state.id using the unique `symbol` column.
    """
    # Build old_id -> new_id mapping via symbol lookup
    symbol_to_new_id: Dict[int, str] = {}

    # Get all grid_state rows from SQLite (old IDs and symbols)
    old_rows = sqlite_conn.execute(
        "SELECT id, symbol FROM grid_state"
    ).fetchall()
    if not old_rows:
        logger.info("  grid_levels: no grid_state rows, skipping remapping")
        return 0

    with pg_conn.cursor() as cur:
        for old_row in old_rows:
            old_id = old_row["id"] if isinstance(old_row, sqlite3.Row) else old_row[0]
            symbol = old_row["symbol"] if isinstance(old_row, sqlite3.Row) else old_row[1]
            cur.execute(
                "SELECT id FROM grid_state WHERE symbol = %s", (symbol,)
            )
            result = cur.fetchone()
            if result:
                symbol_to_new_id[old_id] = result[0]

    if not symbol_to_new_id:
        logger.warning("  grid_levels: could not map any grid_state IDs")
        return 0

    logger.info(
        f"  grid_levels: mapped {len(symbol_to_new_id)} grid_state IDs"
    )

    # Now migrate grid_levels with remapped grid_id
    sqlite_columns = _get_sqlite_columns(sqlite_conn, "grid_levels")
    row_count = _get_sqlite_row_count(sqlite_conn, "grid_levels")

    if row_count == 0:
        logger.info("  grid_levels: 0 rows (skipping)")
        return 0

    pg_cols, sqlite_cols, converters = _build_column_map(sqlite_columns, "grid_levels")
    placeholders = ", ".join(["%s"] * len(pg_cols))
    col_list = ", ".join(f'"{c}"' for c in pg_cols)
    insert_sql = (
        f"INSERT INTO grid_levels ({col_list}) "
        f"VALUES ({placeholders}) "
        f"ON CONFLICT DO NOTHING"
    )

    if dry_run:
        logger.info(f"  grid_levels: {row_count} rows (dry-run)")
        return row_count

    migrated = 0
    cursor = sqlite_conn.execute("SELECT * FROM grid_levels")
    pg_cursor = pg_conn.cursor()
    batch: List[Tuple[Any, ...]] = []

    while True:
        row = cursor.fetchone()
        if row is None:
            break
        try:
            values = _transform_row(row, sqlite_cols, converters)
            # Remap grid_id: find the grid_id column index in pg_cols
            grid_id_idx = pg_cols.index("grid_id") if "grid_id" in pg_cols else None
            if grid_id_idx is not None:
                old_grid_id = values[grid_id_idx]
                new_grid_id = symbol_to_new_id.get(old_grid_id)
                if new_grid_id is None:
                    logger.warning(
                        f"  grid_levels: no mapping for grid_id={old_grid_id}, skipping row"
                    )
                    continue
                values_list = list(values)
                values_list[grid_id_idx] = new_grid_id
                values = tuple(values_list)
            batch.append(values)
        except Exception as e:
            logger.warning(f"  grid_levels: row transform error: {e}")
            continue

        if len(batch) >= batch_size:
            psycopg2.extras.execute_batch(
                pg_cursor, insert_sql, batch, page_size=batch_size
            )
            migrated += len(batch)
            logger.info(f"  grid_levels: {migrated}/{row_count} rows migrated")
            batch.clear()

    if batch:
        psycopg2.extras.execute_batch(
            pg_cursor, insert_sql, batch, page_size=batch_size
        )
        migrated += len(batch)

    pg_conn.commit()
    logger.info(f"  grid_levels: {migrated}/{row_count} rows migrated (complete)")
    return migrated


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def _backup_sqlite(sqlite_path: str) -> Optional[str]:
    """Create a timestamped backup of the SQLite database.

    Returns the backup file path, or None if backup failed.
    """
    if not os.path.exists(sqlite_path):
        logger.warning(f"SQLite file not found at {sqlite_path}, no backup created")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{sqlite_path}.backup_{timestamp}"

    try:
        shutil.copy2(sqlite_path, backup_path)
        logger.info(f"SQLite backup created: {backup_path}")
        return backup_path
    except Exception as e:
        logger.error(f"Failed to create backup: {e}")
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Run the SQLite -> PostgreSQL migration.

    Returns 0 on success, 1 on failure.
    """
    # Local config that can be overridden by CLI args
    pg_host = PG_HOST
    pg_port = PG_PORT
    pg_db = PG_DB
    pg_user = PG_USER
    pg_password = PG_PASSWORD

    parser = argparse.ArgumentParser(
        description="Migrate Bot 3 SQLite database to PostgreSQL/TimescaleDB"
    )
    parser.add_argument(
        "--sqlite-path",
        default=SQLITE_PATH,
        help=f"Path to SQLite database (default: {SQLITE_PATH})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Rows per batch insert (default: 1000)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview migration without inserting data",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip SQLite backup before migration",
    )
    parser.add_argument(
        "--pg-host", default=PG_HOST, help=f"PostgreSQL host (default: {PG_HOST})"
    )
    parser.add_argument(
        "--pg-port", type=int, default=PG_PORT, help=f"PostgreSQL port (default: {PG_PORT})"
    )
    parser.add_argument("--pg-db", default=PG_DB, help=f"PostgreSQL database (default: {PG_DB})")
    parser.add_argument("--pg-user", default=PG_USER, help=f"PostgreSQL user (default: {PG_USER})")
    parser.add_argument(
        "--pg-password", default=PG_PASSWORD, help="PostgreSQL password"
    )
    args = parser.parse_args()

    # Override config from CLI args
    pg_host = args.pg_host
    pg_port = args.pg_port
    pg_db = args.pg_db
    pg_user = args.pg_user
    pg_password = args.pg_password

    # Resolve SQLite path relative to project root
    sqlite_path = args.sqlite_path
    if not os.path.isabs(sqlite_path):
        sqlite_path = str(_PROJECT_ROOT / sqlite_path)

    logger.info("=" * 60)
    logger.info("  Bot 3: SQLite -> PostgreSQL/TimescaleDB Migration")
    logger.info("=" * 60)
    logger.info(f"  SQLite:  {sqlite_path}")
    logger.info(f"  PG:      {pg_user}@{pg_host}:{pg_port}/{pg_db}")
    logger.info(f"  Batch:   {args.batch_size}")
    logger.info(f"  Dry run: {args.dry_run}")
    logger.info("")

    # Check SQLite exists
    if not os.path.exists(sqlite_path):
        logger.error(f"SQLite database not found: {sqlite_path}")
        return 1

    start_time = time.time()

    # Step 1: Backup SQLite
    backup_path = None
    if not args.no_backup and not args.dry_run:
        backup_path = _backup_sqlite(sqlite_path)

    # Step 2: Connect and enumerate tables
    try:
        with _get_sqlite_conn(sqlite_path) as sqlite_conn:
            available_tables = _get_sqlite_tables(sqlite_conn)
            logger.info(
                f"SQLite tables found: {len(available_tables)} -> {', '.join(available_tables)}"
            )

            # Filter to only tables that exist in SQLite
            tables_to_migrate = [t for t in TABLES_TO_MIGRATE if t in available_tables]
            missing = [t for t in TABLES_TO_MIGRATE if t not in available_tables]
            if missing:
                logger.info(f"Tables not in SQLite (will skip): {', '.join(missing)}")

            # Step 3: Connect to PG and migrate
            with _get_pg_conn_args(pg_host, pg_port, pg_db, pg_user, pg_password) as pg_conn:
                pg_tables = _get_pg_tables(pg_conn)
                logger.info(f"PG tables found: {len(pg_tables)}")

                # Verify required PG tables exist
                missing_pg = [t for t in tables_to_migrate if t not in pg_tables]
                if missing_pg:
                    logger.error(
                        f"PG tables missing (run schema_timescaledb.sql first): "
                        f"{', '.join(missing_pg)}"
                    )
                    return 1

                # Migrate each table
                results: Dict[str, int] = {}
                errors: List[str] = []

                for table_name in tables_to_migrate:
                    try:
                        if table_name == "grid_levels":
                            count = _migrate_grid_levels_with_remapping(
                                sqlite_conn,
                                pg_conn,
                                batch_size=args.batch_size,
                                dry_run=args.dry_run,
                            )
                        else:
                            count = _migrate_table(
                                sqlite_conn,
                                pg_conn,
                                table_name,
                                batch_size=args.batch_size,
                                dry_run=args.dry_run,
                            )
                        results[table_name] = count
                    except Exception as e:
                        error_msg = f"{table_name}: {e}"
                        logger.error(f"  FAILED: {error_msg}")
                        errors.append(error_msg)
                        results[table_name] = -1

                # Step 4: Summary
                elapsed = time.time() - start_time
                logger.info("")
                logger.info("=" * 60)
                logger.info("  Migration Summary")
                logger.info("=" * 60)
                total_rows = 0
                for table_name, count in results.items():
                    status = "OK" if count >= 0 else "FAILED"
                    logger.info(f"  {table_name:40s} {count:>8} rows  [{status}]")
                    if count > 0:
                        total_rows += count

                logger.info(f"  {'TOTAL':40s} {total_rows:>8} rows")
                logger.info(f"  Elapsed: {elapsed:.1f}s")
                if backup_path:
                    logger.info(f"  Backup:  {backup_path}")

                if errors:
                    logger.error("")
                    logger.error(f"  {len(errors)} table(s) failed:")
                    for err in errors:
                        logger.error(f"    - {err}")
                    return 1

                logger.info("")
                logger.info("Migration completed successfully.")
                if args.dry_run:
                    logger.info("(Dry run -- no data was inserted)")
                return 0

    except psycopg2.OperationalError as e:
        logger.error(f"PostgreSQL connection failed: {e}")
        logger.error(
            "Ensure PostgreSQL is running and credentials are correct in .env"
        )
        return 1
    except Exception as e:
        logger.error(f"Migration failed with unexpected error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
