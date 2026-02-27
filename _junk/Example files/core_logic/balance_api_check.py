#!/usr/bin/env python3
"""Test balance history API directly"""
import sqlite3

# Query database directly
conn = sqlite3.connect('data/trading_bot.db')
cursor = conn.cursor()

print("=" * 80)
print("DIRECT DATABASE QUERY")
print("=" * 80)

# Count total
cursor.execute("SELECT COUNT(*) FROM balance_history")
total = cursor.fetchone()[0]
print(f"\nTotal rows in balance_history: {total}")

# Get latest 10 with all details
cursor.execute("""
    SELECT account_id, balance, equity, available_balance, margin_used,
           datetime(timestamp, 'unixepoch') as time, timestamp
    FROM balance_history
    ORDER BY timestamp DESC
    LIMIT 10
""")

print(f"\nLatest 10 snapshots:")
print("-" * 80)
for i, row in enumerate(cursor.fetchall(), 1):
    print(f"{i}. {row[5]} | Account: {row[0]} | Balance: ${row[1]:.2f} | Equity: ${row[2]:.2f}")

conn.close()

print("\n" + "=" * 80)
print("Now test what API endpoint returns...")
print("=" * 80)

# Simulate what API does
from api_server import get_db_connection

conn = get_db_connection()
cursor = conn.cursor()

query = """
    SELECT account_id, subaccount_id, balance, equity,
           available_balance, margin_used, timestamp, fetched_at
    FROM balance_history
    WHERE 1=1
    ORDER BY timestamp DESC LIMIT ?
"""

cursor.execute(query, [100])
rows = cursor.fetchall()
conn.close()

print(f"\nAPI would return: {len(rows)} rows")
if rows:
    print("\nFirst 3 rows:")
    for i, row in enumerate(rows[:3], 1):
        print(f"{i}. Account: {row[0]} | Balance: {row[2]} | Equity: {row[3]} | Timestamp: {row[6]}")
