"""
Initialize the trading bot database.

This script creates the database file and all required tables according to schema.sql.
Safe to run multiple times (uses CREATE TABLE IF NOT EXISTS).
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from .database import DatabaseManager, DATABASE_PATH, get_db_connection


def main():
    """Initialize database and verify tables were created."""
    print("=" * 60)
    print("Trading Bot Database Initialization")
    print("=" * 60)

    print(f"\nDatabase path: {DATABASE_PATH}")

    # Check if database already exists
    db_exists = os.path.exists(DATABASE_PATH)
    if db_exists:
        print(f"[!] Database file already exists")
    else:
        print("[*] Creating new database file")

    # Initialize DatabaseManager (this automatically runs init_database())
    print("\n[*] Initializing DatabaseManager...")
    db = DatabaseManager()

    # Verify tables were created
    print("\n[*] Verifying database tables...")
    with get_db_connection() as conn:
        cursor = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table'
            ORDER BY name;
        """)
        tables = [row[0] for row in cursor.fetchall()]

    print(f"\n[+] Found {len(tables)} tables:")
    for i, table in enumerate(tables, 1):
        print(f"   {i}. {table}")

    # Verify indexes were created
    with get_db_connection() as conn:
        cursor = conn.execute("""
            SELECT name FROM sqlite_master
            WHERE type='index'
            ORDER BY name;
        """)
        indexes = [row[0] for row in cursor.fetchall()]

    print(f"\n[+] Found {len(indexes)} indexes:")
    for i, index in enumerate(indexes, 1):
        print(f"   {i}. {index}")

    # Expected tables for verification
    expected_tables = [
        "account_profiles",
        "balance_history",
        "funding_payments",
        "funding_rate_history",
        "market_availability_history",
        "market_data",
        "market_info_history",
        "market_parameter_changes",
        "pacifica_positions",
        "performance_metrics",
        "positions",
        "signals",
        "subaccount_configs",
        "trades",
    ]

    missing_tables = set(expected_tables) - set(tables)
    if missing_tables:
        print(f"\n[!] WARNING: Missing expected tables: {', '.join(missing_tables)}")
    else:
        print("\n[+] All expected core tables found!")

    print("\n" + "=" * 60)
    print("Database initialization complete!")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
