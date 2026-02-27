#!/usr/bin/env python3
"""
Migrate data from root trading_bot.db to data/trading_bot.db
Preserves all historical data while fixing database path mismatch.
"""

import sqlite3
import shutil
import os
from datetime import datetime

ROOT_DB = "trading_bot.db"
DATA_DB = "data/trading_bot.db"

def migrate_database():
    """Copy all data from root database to data directory database."""

    print("=" * 80)
    print("DATABASE MIGRATION: Root -> Data Directory")
    print("=" * 80)
    print(f"\nSource: {ROOT_DB}")
    print(f"Target: {DATA_DB}")
    print(f"Time: {datetime.now()}\n")

    # Check source exists
    if not os.path.exists(ROOT_DB):
        print(f"ERROR: Source database {ROOT_DB} not found")
        return False

    # Backup target database
    if os.path.exists(DATA_DB):
        backup_path = f"{DATA_DB}.pre_migration_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        print(f"Creating backup: {backup_path}")
        shutil.copy2(DATA_DB, backup_path)

    # Connect to both databases
    source_conn = sqlite3.connect(ROOT_DB)
    source_conn.row_factory = sqlite3.Row
    source_cursor = source_conn.cursor()

    target_conn = sqlite3.connect(DATA_DB)
    target_cursor = target_conn.cursor()

    # Get all tables
    source_cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
    tables = [row[0] for row in source_cursor.fetchall()]

    print(f"Found {len(tables)} tables to migrate\n")
    print("-" * 80)

    migration_stats = {}

    # Migrate each table
    for table in tables:
        # Count source rows
        source_cursor.execute(f"SELECT COUNT(*) FROM {table}")
        source_count = source_cursor.fetchone()[0]

        if source_count == 0:
            print(f"{table:<30} SKIPPED (0 rows)")
            continue

        # Get target count (before migration)
        target_cursor.execute(f"SELECT COUNT(*) FROM {table}")
        target_count_before = target_cursor.fetchone()[0]

        # Strategy: DELETE all from target, then INSERT from source
        # (Ensures clean migration, no duplicates)
        target_cursor.execute(f"DELETE FROM {table}")

        # Get all columns
        source_cursor.execute(f"PRAGMA table_info({table})")
        columns = [col[1] for col in source_cursor.fetchall()]
        column_names = ", ".join(columns)
        placeholders = ", ".join(["?" for _ in columns])

        # Copy all rows
        source_cursor.execute(f"SELECT {column_names} FROM {table}")
        rows = source_cursor.fetchall()

        target_cursor.executemany(
            f"INSERT INTO {table} ({column_names}) VALUES ({placeholders})",
            rows
        )

        # Commit after each table
        target_conn.commit()

        # Verify
        target_cursor.execute(f"SELECT COUNT(*) FROM {table}")
        target_count_after = target_cursor.fetchone()[0]

        if target_count_after == source_count:
            status = "[OK] MIGRATED"
        else:
            status = "[!] MISMATCH"

        print(f"{table:<30} {source_count:>6} rows  {status}")

        migration_stats[table] = {
            "source": source_count,
            "target_before": target_count_before,
            "target_after": target_count_after,
            "success": target_count_after == source_count
        }

    source_conn.close()
    target_conn.close()

    # Summary
    print("\n" + "=" * 80)
    print("MIGRATION SUMMARY")
    print("=" * 80)

    total_rows = sum(stats["source"] for stats in migration_stats.values())
    successful_tables = sum(1 for stats in migration_stats.values() if stats["success"])

    print(f"\nTotal tables migrated: {successful_tables}/{len(migration_stats)}")
    print(f"Total rows migrated: {total_rows:,}")

    # Check for failures
    failures = [table for table, stats in migration_stats.items() if not stats["success"]]
    if failures:
        print(f"\n[!] FAILED TABLES: {', '.join(failures)}")
        return False
    else:
        print("\n[OK] All tables migrated successfully")
        return True

if __name__ == "__main__":
    success = migrate_database()

    if success:
        print("\n" + "=" * 80)
        print("NEXT STEPS:")
        print("=" * 80)
        print("1. Verify data in data/trading_bot.db:")
        print("   python database_diagnostic.py")
        print("\n2. Test API server with new database path")
        print("   python api_server.py")
        print("\n3. If verification passes, remove old database:")
        print(f"   del {ROOT_DB}  # or rename to {ROOT_DB}.old")
        print("=" * 80)
