"""
Rollback script for SQLite-to-PostgreSQL migration.

Restores the SQLite database from backup and optionally drops PostgreSQL tables.
Provides safety checks before performing any destructive operations.

Usage:
    python scripts/rollback_migration.py                        # Interactive
    python scripts/rollback_migration.py --force                # Skip confirmation
    python scripts/rollback_migration.py --drop-pg-tables       # Also drop PG tables
    python scripts/rollback_migration.py --backup-path data/trading_bot.db.backup_20260630_120000
"""

import argparse
import glob
import logging
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import psycopg2
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
# Load .env
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SQLITE_PATH = os.getenv("DATABASE_PATH", "trading_bot.db")
PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
PG_DB = os.getenv("POSTGRES_DB", "trading_bot")
PG_USER = os.getenv("POSTGRES_USER", "trading_bot")
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", "trading_bot_pass")

# All tables that may have been migrated
ALL_MIGRATED_TABLES: List[str] = [
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

# Hypertables (require special handling -- cannot simply TRUNCATE)
HYPERTABLES: List[str] = [
    "trades",
    "market_data",
    "signals",
    "funding_payments",
    "funding_rate_history",
    "performance_metrics",
    "performance_snapshots",
    "balance_history",
    "regime_history",
    "market_info_history",
    "market_parameter_changes",
    "grid_events",
]

# Continuous aggregate views (must be dropped before their base tables)
CONTINUOUS_AGGREGATES: List[str] = [
    "candles_1h",
    "daily_pnl_summary",
    "hourly_funding_summary",
]


# ---------------------------------------------------------------------------
# Backup discovery
# ---------------------------------------------------------------------------


def _find_backups(sqlite_path: str) -> List[str]:
    """Find all backup files matching the pattern {sqlite_path}.backup_*."""
    pattern = f"{sqlite_path}.backup_*"
    backups = sorted(glob.glob(pattern), reverse=True)
    return backups


def _get_backup_info(backup_path: str) -> Optional[dict]:
    """Get info about a backup file."""
    if not os.path.exists(backup_path):
        return None
    stat = os.stat(backup_path)
    return {
        "path": backup_path,
        "size_mb": round(stat.st_size / (1024 * 1024), 2),
        "modified": time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)
        ),
    }


# ---------------------------------------------------------------------------
# SQLite restore
# ---------------------------------------------------------------------------


def _restore_sqlite(sqlite_path: str, backup_path: str) -> bool:
    """Restore SQLite database from backup.

    Creates a safety backup of the current (post-migration) SQLite before
    overwriting.
    """
    if not os.path.exists(backup_path):
        logger.error(f"Backup file not found: {backup_path}")
        return False

    # Create a safety backup of the current state
    safety_ts = time.strftime("%Y%m%d_%H%M%S")
    safety_backup = f"{sqlite_path}.pre_rollback_{safety_ts}"
    if os.path.exists(sqlite_path):
        try:
            shutil.copy2(sqlite_path, safety_backup)
            logger.info(f"Current SQLite saved as safety backup: {safety_backup}")
        except Exception as e:
            logger.error(f"Failed to create safety backup: {e}")
            return False

    # Restore from backup
    try:
        shutil.copy2(backup_path, sqlite_path)
        logger.info(f"SQLite restored from: {backup_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to restore SQLite: {e}")
        # Attempt to restore the safety backup
        if os.path.exists(safety_backup):
            try:
                shutil.copy2(safety_backup, sqlite_path)
                logger.info("Rolled back safety backup (restore failed)")
            except Exception:
                logger.error("CRITICAL: Could not restore safety backup!")
        return False


# ---------------------------------------------------------------------------
# PostgreSQL cleanup
# ---------------------------------------------------------------------------


def _get_pg_tables(pg_conn: psycopg2.extensions.connection) -> List[str]:
    """Get list of user tables in PostgreSQL."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        return [row[0] for row in cur.fetchall()]


def _drop_pg_continuous_aggregates(
    pg_conn: psycopg2.extensions.connection,
) -> int:
    """Drop continuous aggregate views (must be done before base tables)."""
    dropped = 0
    with pg_conn.cursor() as cur:
        for view_name in CONTINUOUS_AGGREGATES:
            try:
                cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view_name} CASCADE")
                dropped += 1
                logger.info(f"  Dropped continuous aggregate: {view_name}")
            except Exception as e:
                logger.warning(f"  Could not drop {view_name}: {e}")
    pg_conn.commit()
    return dropped


def _drop_pg_tables(
    pg_conn: psycopg2.extensions.connection,
    tables: List[str],
) -> Dict[str, bool]:
    """Drop specified PostgreSQL tables.

    Returns a dict of {table: success}.
    """
    results: Dict[str, bool] = {}

    with pg_conn.cursor() as cur:
        for table in tables:
            try:
                cur.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
                results[table] = True
                logger.info(f"  Dropped table: {table}")
            except Exception as e:
                results[table] = False
                logger.error(f"  Failed to drop {table}: {e}")

    pg_conn.commit()
    return results


def _drop_pg_hypertables(
    pg_conn: psycopg2.extensions.connection,
) -> int:
    """Remove hypertable designations (needed before dropping TimescaleDB tables)."""
    dropped = 0
    with pg_conn.cursor() as cur:
        for ht in HYPERTABLES:
            try:
                # Remove compression policies first
                cur.execute(
                    f"SELECT remove_compression_policy('{ht}', if_exists => TRUE)"
                )
                # Remove retention policies
                cur.execute(
                    f"SELECT remove_retention_policy('{ht}', if_exists => TRUE)"
                )
                dropped += 1
            except Exception:
                pass  # Policy may not exist
    pg_conn.commit()
    return dropped


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Run rollback. Returns 0 on success, 1 on failure."""
    # Local config that can be overridden by CLI args
    pg_host = PG_HOST
    pg_port = PG_PORT
    pg_db = PG_DB
    pg_user = PG_USER
    pg_password = PG_PASSWORD

    parser = argparse.ArgumentParser(
        description="Rollback Bot 3 SQLite -> PostgreSQL migration"
    )
    parser.add_argument(
        "--sqlite-path",
        default=SQLITE_PATH,
        help=f"Path to SQLite database (default: {SQLITE_PATH})",
    )
    parser.add_argument(
        "--backup-path",
        help="Explicit path to backup file (auto-detected if omitted)",
    )
    parser.add_argument(
        "--drop-pg-tables",
        action="store_true",
        help="Also drop all migrated tables from PostgreSQL",
    )
    parser.add_argument(
        "--drop-cascade",
        action="store_true",
        help="Drop PG tables with CASCADE (removes dependent objects)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip interactive confirmation prompts",
    )
    parser.add_argument("--pg-host", default=PG_HOST)
    parser.add_argument("--pg-port", type=int, default=PG_PORT)
    parser.add_argument("--pg-db", default=PG_DB)
    parser.add_argument("--pg-user", default=PG_USER)
    parser.add_argument("--pg-password", default=PG_PASSWORD)
    args = parser.parse_args()

    pg_host = args.pg_host
    pg_port = args.pg_port
    pg_db = args.pg_db
    pg_user = args.pg_user
    pg_password = args.pg_password

    sqlite_path = args.sqlite_path
    if not os.path.isabs(sqlite_path):
        sqlite_path = str(_PROJECT_ROOT / sqlite_path)

    logger.info("=" * 60)
    logger.info("  Bot 3: Migration Rollback")
    logger.info("=" * 60)
    logger.info(f"  SQLite: {sqlite_path}")
    logger.info(f"  PG:     {pg_user}@{pg_host}:{pg_port}/{pg_db}")
    logger.info("")

    # --- Step 1: Find backup ---
    backup_path = args.backup_path
    if not backup_path:
        backups = _find_backups(sqlite_path)
        if not backups:
            logger.error("No backup files found.")
            logger.error(f"Expected pattern: {sqlite_path}.backup_*")
            return 1
        backup_path = backups[0]
        logger.info(f"Found {len(backups)} backup(s), using most recent:")
        for i, bp in enumerate(backups[:5]):
            info = _get_backup_info(bp)
            prefix = " --> " if i == 0 else "     "
            if info:
                logger.info(
                    f"{prefix}{info['path']}  "
                    f"({info['size_mb']} MB, {info['modified']})"
                )
        logger.info("")

    if not os.path.exists(backup_path):
        logger.error(f"Backup file not found: {backup_path}")
        return 1

    backup_info = _get_backup_info(backup_path)
    if backup_info:
        logger.info(f"Selected backup: {backup_info['path']}")
        logger.info(f"  Size: {backup_info['size_mb']} MB")
        logger.info(f"  Modified: {backup_info['modified']}")
    logger.info("")

    # --- Step 2: Confirm ---
    if not args.force:
        logger.warning("This will:")
        logger.warning(
            f"  1. Overwrite current SQLite ({sqlite_path}) with backup"
        )
        if args.drop_pg_tables:
            logger.warning(
                f"  2. Drop ALL migrated tables from PostgreSQL ({PG_DB})"
            )
            if args.drop_cascade:
                logger.warning("     (with CASCADE -- dependent views will also be dropped)")
        logger.info("")

        # Show current SQLite stats
        if os.path.exists(sqlite_path):
            current_size = round(os.path.getsize(sqlite_path) / (1024 * 1024), 2)
            logger.info(f"Current SQLite size: {current_size} MB")

        response = input("Proceed with rollback? [y/N] ").strip().lower()
        if response not in ("y", "yes"):
            logger.info("Rollback cancelled.")
            return 0
        logger.info("")

    start_time = time.time()

    # --- Step 3: Restore SQLite ---
    logger.info("Step 1: Restoring SQLite from backup...")
    if not _restore_sqlite(sqlite_path, backup_path):
        logger.error("SQLite restore failed. Aborting.")
        return 1
    logger.info("SQLite restored successfully.")
    logger.info("")

    # --- Step 4: Optionally drop PG tables ---
    if args.drop_pg_tables:
        logger.info("Step 2: Cleaning up PostgreSQL...")
        try:
            pg_conn = psycopg2.connect(
                host=pg_host,
                port=pg_port,
                dbname=pg_db,
                user=pg_user,
                password=pg_password,
            )
        except Exception as e:
            logger.error(f"Could not connect to PostgreSQL: {e}")
            logger.warning("SQLite was restored, but PG tables were not dropped.")
            return 0

        try:
            pg_tables = _get_pg_tables(pg_conn)
            migrated_tables = [t for t in ALL_MIGRATED_TABLES if t in pg_tables]

            if not migrated_tables:
                logger.info("  No migrated tables found in PostgreSQL.")
            else:
                logger.info(f"  Found {len(migrated_tables)} migrated table(s) in PG")

                # Drop continuous aggregates first
                logger.info("  Dropping continuous aggregates...")
                _drop_pg_continuous_aggregates(pg_conn)

                # Remove hypertable policies
                logger.info("  Removing hypertable policies...")
                _drop_pg_hypertables(pg_conn)

                # Drop tables
                logger.info("  Dropping tables...")
                results = _drop_pg_tables(pg_conn, migrated_tables)

                success_count = sum(1 for v in results.values() if v)
                fail_count = sum(1 for v in results.values() if not v)
                logger.info(
                    f"  Dropped {success_count} tables, {fail_count} failures"
                )

        finally:
            pg_conn.close()
        logger.info("")

    # --- Summary ---
    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info("  Rollback Complete")
    logger.info("=" * 60)
    logger.info(f"  SQLite restored from: {backup_path}")
    if args.drop_pg_tables:
        logger.info(f"  PostgreSQL tables dropped: {PG_DB}")
    logger.info(f"  Elapsed: {elapsed:.1f}s")
    logger.info("")
    logger.info("To switch the application back to SQLite:")
    logger.info("  Set DATABASE_BACKEND=sqlite in .env")
    logger.info("")

    return 0


if __name__ == "__main__":
    sys.exit(main())
