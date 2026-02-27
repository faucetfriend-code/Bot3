#!/usr/bin/env python3
"""
Database Diagnostic Tool
Inspects trading_bot.db and generates comprehensive report
"""

import sqlite3
import json
from datetime import datetime
from pathlib import Path

DB_PATH = "data/trading_bot.db"

def run_diagnostic():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    report = {}

    # 1. Table statistics
    print("=" * 80)
    print("DATABASE DIAGNOSTIC REPORT")
    print("=" * 80)
    print(f"\nGenerated: {datetime.now()}")
    print(f"Database: {DB_PATH}\n")

    # Get all tables
    cursor.execute("""
        SELECT name FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
    """)
    tables = [row[0] for row in cursor.fetchall()]

    print(f"\nFound {len(tables)} tables\n")
    print("-" * 80)
    print(f"{'Table Name':<30} {'Row Count':<15} {'Status'}")
    print("-" * 80)

    for table in tables:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        status = "[OK] POPULATED" if count > 0 else "[!] EMPTY"
        print(f"{table:<30} {count:<15} {status}")
        report[table] = {"count": count}

    # 2. Data freshness
    print("\n" + "=" * 80)
    print("DATA FRESHNESS CHECK")
    print("=" * 80)

    freshness_queries = {
        "positions": "SELECT MAX(updated_at) FROM positions",
        "pacifica_positions": "SELECT MAX(updated_at) FROM pacifica_positions",
        "funding_payments": "SELECT MAX(timestamp) FROM funding_payments",
        "balance_history": "SELECT MAX(fetched_at) FROM balance_history",
        "market_info_history": "SELECT MAX(fetched_at) FROM market_info_history"
    }

    for table, query in freshness_queries.items():
        try:
            cursor.execute(query)
            last_update = cursor.fetchone()[0]
            if last_update:
                print(f"{table:<30}: {last_update}")
            else:
                print(f"{table:<30}: NO DATA")
        except Exception as e:
            print(f"{table:<30}: ERROR - {e}")

    # 3. Critical data checks
    print("\n" + "=" * 80)
    print("CRITICAL DATA CHECKS")
    print("=" * 80)

    # Check funding payment frequency
    cursor.execute("""
        SELECT
            symbol,
            DATE(timestamp) as date,
            COUNT(*) as payments
        FROM funding_payments
        GROUP BY symbol, DATE(timestamp)
        HAVING COUNT(*) < 23
        ORDER BY date DESC
        LIMIT 10
    """)

    missing_funding = cursor.fetchall()
    if missing_funding:
        print("\n[!] MISSING FUNDING PAYMENTS (should be 24/day):")
        for row in missing_funding:
            print(f"  {row['symbol']} on {row['date']}: {row['payments']} payments")
    else:
        print("\n[OK] Funding payment frequency OK (or no funding data yet)")

    # Check balance snapshot frequency
    cursor.execute("""
        SELECT
            account_id,
            DATE(datetime(timestamp, 'unixepoch')) as date,
            COUNT(*) as snapshots
        FROM balance_history
        GROUP BY account_id, DATE(datetime(timestamp, 'unixepoch'))
        HAVING COUNT(*) < 280
        ORDER BY date DESC
        LIMIT 5
    """)

    missing_snapshots = cursor.fetchall()
    if missing_snapshots:
        print("\n[!] MISSING BALANCE SNAPSHOTS (should be ~288/day):")
        for row in missing_snapshots:
            print(f"  Account {row['account_id']} on {row['date']}: {row['snapshots']} snapshots")
    else:
        print("\n[OK] Balance snapshot frequency OK (or no balance data yet)")

    # Check precision storage
    cursor.execute("""
        SELECT symbol, tick_size
        FROM market_info_history
        WHERE tick_size LIKE '%e%' OR tick_size LIKE '%E%'
        LIMIT 5
    """)

    precision_errors = cursor.fetchall()
    if precision_errors:
        print("\n[!] PRECISION ERRORS (scientific notation in TEXT fields):")
        for row in precision_errors:
            print(f"  {row['symbol']}: {row['tick_size']}")
    else:
        print("\n[OK] Precision storage OK")

    # 4. Sample data
    print("\n" + "=" * 80)
    print("SAMPLE DATA (Latest 5 rows from key tables)")
    print("=" * 80)

    # Pacifica positions
    print("\n[PACIFICA POSITIONS]")
    cursor.execute("""
        SELECT symbol, side, size, entry_price, current_price,
               cumulative_funding_paid, unrealized_pnl
        FROM pacifica_positions
        WHERE closed_at IS NULL
        ORDER BY opened_at DESC
        LIMIT 5
    """)

    positions = cursor.fetchall()
    if positions:
        for row in positions:
            print(f"  {row['symbol']}: {row['side']} {row['size']} @ ${row['entry_price']} "
                  f"| Current: ${row['current_price']} | Funding: ${row['cumulative_funding_paid']} "
                  f"| P&L: ${row['unrealized_pnl']}")
    else:
        print("  (No open positions)")

    # Balance history
    print("\n[BALANCE HISTORY - Latest]")
    cursor.execute("""
        SELECT balance, equity, available_balance, margin_used,
               datetime(timestamp, 'unixepoch') as time
        FROM balance_history
        ORDER BY timestamp DESC
        LIMIT 3
    """)

    balances = cursor.fetchall()
    if balances:
        for row in balances:
            print(f"  {row['time']}: Balance=${row['balance']}, Equity=${row['equity']}, "
                  f"Available=${row['available_balance']}, Margin=${row['margin_used']}")
    else:
        print("  (No balance history)")

    # Market info
    print("\n[MARKET INFO - Sample]")
    cursor.execute("""
        SELECT symbol, tick_size, lot_size, max_leverage, funding_rate, fetched_at
        FROM market_info_history
        WHERE id IN (
            SELECT MAX(id) FROM market_info_history GROUP BY symbol
        )
        ORDER BY symbol
        LIMIT 5
    """)

    markets = cursor.fetchall()
    if markets:
        for row in markets:
            print(f"  {row['symbol']}: tick={row['tick_size']}, lot={row['lot_size']}, "
                  f"leverage={row['max_leverage']}, funding={row['funding_rate']}")
    else:
        print("  (No market info)")

    conn.close()

    print("\n" + "=" * 80)
    print("END OF REPORT")
    print("=" * 80)

if __name__ == "__main__":
    run_diagnostic()
