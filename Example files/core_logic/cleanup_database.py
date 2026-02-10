#!/usr/bin/env python3
"""
Comprehensive Trading Bot Database Cleanup Script
Follows all specifications from DBCALLStxt.txt, Database Schema docs, and cleanup requirements.

ENHANCEMENTS:
- Foreign key enforcement enabled
- Dry-run mode for safe testing
- Improved scientific notation detection
- Progress reporting
"""

import os
import sqlite3
import shutil
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from database import get_db_connection, DATABASE_PATH

# =============================================================================
# Configuration & Logging
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(f"cleanup_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

BACKUP_DIR = Path("backups")
BACKUP_DIR.mkdir(exist_ok=True)

# =============================================================================
# Safety: Backup First
# =============================================================================

def create_backup() -> str:
    """Create timestamped database backup."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"trading_bot_pre_cleanup_{timestamp}.db"
    shutil.copy2(DATABASE_PATH, backup_path)
    logger.info(f"[OK] Database backup created: {backup_path}")
    return str(backup_path)

# =============================================================================
# Core Cleanup Operations (Safe Order)
# =============================================================================

def analyze_database_state(conn) -> Dict[str, Any]:
    """Analyze current database state."""
    cur = conn.cursor()
    tables = [
        'trades', 'positions', 'pacifica_positions', 'signals', 'market_data',
        'funding_payments', 'funding_rate_history', 'market_info_history',
        'balance_history', 'deposits_withdrawals', 'performance_metrics',
        'subaccount_configs', 'account_profiles', 'market_parameter_changes',
        'market_availability_history'
    ]

    stats = {}
    for table in tables:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        stats[table] = cur.fetchone()[0]

    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = [row[0] for row in cur.fetchall()]
    stats['total_tables'] = len(existing_tables)
    stats['db_size_mb'] = os.path.getsize(DATABASE_PATH) / (1024*1024)

    logger.info(f"[STATS] Database analysis complete: {stats['db_size_mb']:.2f} MB, {len(existing_tables)} tables")
    return stats

def remove_duplicate_market_data(conn):
    """Remove duplicate market_data timestamps per symbol."""
    logger.info("[CLEANUP] Removing duplicate market_data timestamps per symbol...")
    deleted = conn.execute("""
        DELETE FROM market_data
        WHERE rowid NOT IN (
            SELECT MIN(rowid)
            FROM market_data
            GROUP BY symbol, timestamp
        )
    """).rowcount
    logger.info(f"   Deleted {deleted} duplicate market_data rows")
    return deleted

def remove_orphaned_positions(conn):
    """Remove orphaned positions without trades."""
    logger.info("[CLEANUP] Removing orphaned positions without trades...")
    deleted = conn.execute("""
        DELETE FROM positions
        WHERE symbol NOT IN (SELECT DISTINCT symbol FROM trades)
          AND symbol NOT IN (SELECT DISTINCT symbol FROM pacifica_positions WHERE closed_at IS NULL)
    """).rowcount
    logger.info(f"   Deleted {deleted} orphaned positions")
    return deleted

def remove_invalid_trades(conn):
    """Remove trades with invalid P&L or malformed data."""
    logger.info("[CLEANUP] Removing trades with invalid P&L or malformed data...")
    deleted = conn.execute("""
        DELETE FROM trades
        WHERE pnl IS NULL
           OR pnl = ''
           OR entry_price <= 0
           OR quantity <= 0
    """).rowcount
    logger.info(f"   Deleted {deleted} invalid trades")
    return deleted

def remove_malformed_signals(conn):
    """Remove signals with malformed JSON indicators."""
    logger.info("[CLEANUP] Removing signals with malformed JSON indicators...")
    cur = conn.cursor()
    cur.execute("SELECT id, indicators FROM signals")
    malformed = 0
    for sig_id, indicators in cur.fetchall():
        if not indicators:
            continue
        try:
            json.loads(indicators)
        except (json.JSONDecodeError, TypeError):
            conn.execute("DELETE FROM signals WHERE id = ?", (sig_id,))
            malformed += 1
    logger.info(f"   Deleted {malformed} signals with malformed JSON")
    return malformed

def remove_future_timestamps(conn):
    """Remove records with future timestamps."""
    logger.info("[CLEANUP] Removing records with future timestamps...")
    future = int(datetime.now().timestamp())
    tables = [
        ('market_data', 'timestamp'),
        ('trades', 'entry_time'),
        ('trades', 'exit_time'),
        ('signals', 'timestamp'),
        ('balance_history', 'timestamp'),
        ('funding_payments', 'timestamp'),
        ('funding_rate_history', 'timestamp')
    ]
    total = 0
    for table, col in tables:
        deleted = conn.execute(f"DELETE FROM {table} WHERE {col} > ?", (future,)).rowcount
        if deleted:
            logger.info(f"   {table}: removed {deleted} future records")
        total += deleted
    logger.info(f"   Total future records removed: {total}")
    return total

def remove_scientific_notation_corruption(conn):
    """Remove scientific notation corruption in text fields (IMPROVED)."""
    logger.info("[CLEANUP] Removing scientific notation corruption in text fields...")

    # More precise regex pattern for scientific notation in JSON
    # Matches: 1.23e+10, 4.56e-7, etc. (numbers only, not words)
    scientific_pattern = re.compile(r'\b\d+\.?\d*e[+-]\d+\b', re.IGNORECASE)

    cur = conn.cursor()
    cur.execute("SELECT id, indicators FROM signals")
    corrupted = 0

    for sig_id, indicators in cur.fetchall():
        if not indicators:
            continue
        # Check if indicators contain scientific notation
        if scientific_pattern.search(indicators):
            conn.execute("DELETE FROM signals WHERE id = ?", (sig_id,))
            corrupted += 1

    logger.info(f"   Removed {corrupted} corrupted signal indicators")
    return corrupted

def vacuum_and_optimize(conn):
    """Run REINDEX and ANALYZE (VACUUM must be run outside transaction)."""
    logger.info("[OPTIMIZE] Running REINDEX and ANALYZE...")
    conn.execute("REINDEX")
    conn.execute("ANALYZE")
    logger.info("   Database indexes rebuilt and statistics updated")

# =============================================================================
# Test Data Reset Mode (Optional)
# =============================================================================

def reset_test_data(conn):
    """Clear all trading data while preserving configs."""
    logger.warning("[RESET] TEST DATA RESET MODE ACTIVATED - Clearing all trading data...")
    trading_tables = [
        'trades', 'positions', 'pacifica_positions', 'signals', 'market_data',
        'funding_payments', 'funding_rate_history', 'balance_history',
        'deposits_withdrawals', 'performance_metrics', 'market_parameter_changes',
        'market_availability_history'
    ]

    total_deleted = 0
    for i, table in enumerate(trading_tables, 1):
        logger.info(f"   [{i}/{len(trading_tables)}] Clearing {table}...")
        deleted = conn.execute(f"DELETE FROM {table}").rowcount
        total_deleted += deleted
        logger.info(f"      Cleared {deleted} rows from {table}")

    # Reset auto-increment
    for table in trading_tables:
        conn.execute(f"DELETE FROM sqlite_sequence WHERE name='{table}'")

    logger.info(f"[OK] Test data reset complete: {total_deleted} total rows deleted")

# =============================================================================
# Main Cleanup Orchestrator
# =============================================================================

def run_full_cleanup(reset_test_data_mode: bool = False, dry_run: bool = False):
    """
    Run comprehensive database cleanup.

    Args:
        reset_test_data_mode: If True, clear all trading data (keep configs)
        dry_run: If True, show what would be deleted but don't commit changes
    """
    if dry_run:
        logger.warning("[DRY RUN] No changes will be committed")

    logger.info("[START] Starting comprehensive database cleanup...")

    backup_path = create_backup()

    initial_stats = {}
    final_stats = {}
    cleanup_summary = {}

    with get_db_connection() as conn:
        # Enable foreign key constraints (CRITICAL for data integrity)
        conn.execute("PRAGMA foreign_keys = ON")
        logger.info("[OK] Foreign key constraints enabled")

        conn.execute("BEGIN")
        try:
            initial_stats = analyze_database_state(conn)

            if reset_test_data_mode:
                reset_test_data(conn)
            else:
                # Run all cleanup operations and track results
                logger.info("\n" + "="*80)
                logger.info("CLEANUP OPERATIONS")
                logger.info("="*80)

                cleanup_summary['duplicates'] = remove_duplicate_market_data(conn)
                cleanup_summary['orphans'] = remove_orphaned_positions(conn)
                cleanup_summary['invalid_trades'] = remove_invalid_trades(conn)
                cleanup_summary['malformed_signals'] = remove_malformed_signals(conn)
                cleanup_summary['future_timestamps'] = remove_future_timestamps(conn)
                cleanup_summary['corrupted_indicators'] = remove_scientific_notation_corruption(conn)

                logger.info("\n" + "="*80)
                logger.info("CLEANUP SUMMARY")
                logger.info("="*80)
                for operation, count in cleanup_summary.items():
                    logger.info(f"   {operation}: {count} records removed")

            vacuum_and_optimize(conn)

            if dry_run:
                conn.execute("ROLLBACK")
                logger.warning("[DRY RUN] Changes rolled back (no data modified)")
            else:
                conn.execute("COMMIT")
                logger.info("[OK] All cleanup operations completed successfully")

        except Exception as e:
            conn.execute("ROLLBACK")
            logger.error(f"[ERROR] Cleanup failed - rolled back: {e}")
            raise

        finally:
            # Get stats after cleanup but before VACUUM
            with get_db_connection() as stats_conn:
                final_stats = analyze_database_state(stats_conn)

    # Run VACUUM outside transaction (SQLite requirement)
    if not dry_run:
        logger.info("[VACUUM] Running VACUUM to reclaim disk space...")
        with get_db_connection() as conn:
            conn.execute("VACUUM")
        logger.info("[OK] VACUUM completed")

        # Get final size after VACUUM
        final_stats['db_size_mb'] = os.path.getsize(DATABASE_PATH) / (1024*1024)

    # Final Report
    size_before = initial_stats.get('db_size_mb', 0)
    size_after = final_stats.get('db_size_mb', 0)
    reduction = size_before - size_after

    logger.info("\n" + "="*80)
    logger.info("[REPORT] CLEANUP COMPLETE")
    logger.info("="*80)
    logger.info(f"   Backup: {backup_path}")
    logger.info(f"   Size before: {size_before:.2f} MB -> after: {size_after:.2f} MB")
    if size_before > 0:
        logger.info(f"   Space reclaimed: {reduction:.2f} MB ({(reduction/size_before*100):.1f}% reduction)")
    logger.info(f"   Final database state: {final_stats.get('total_tables', 0)} tables, healthy")

    if dry_run:
        logger.warning("\n[WARNING] DRY RUN COMPLETED - No actual changes were made to the database")
        logger.info("   Run without --dry-run to apply these changes")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Trading Bot Database Cleanup Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Normal cleanup (safe, with backup)
  python cleanup_database.py

  # Dry run to preview changes
  python cleanup_database.py --dry-run

  # Full test data reset (clears all trading data)
  python cleanup_database.py --reset-test-data

  # Test reset with dry run
  python cleanup_database.py --reset-test-data --dry-run
        """
    )
    parser.add_argument(
        "--reset-test-data",
        action="store_true",
        help="Clear all trading data (keep configs)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without making changes"
    )
    args = parser.parse_args()

    try:
        run_full_cleanup(
            reset_test_data_mode=args.reset_test_data,
            dry_run=args.dry_run
        )
    except KeyboardInterrupt:
        logger.warning("\nCleanup interrupted by user")
    except Exception as e:
        logger.critical(f"Cleanup failed critically: {e}")
        raise
