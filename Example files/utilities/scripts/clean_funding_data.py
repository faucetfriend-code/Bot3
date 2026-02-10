#!/usr/bin/env python3
"""
Clean existing funding rate data by normalizing symbols.
Removes ':USD' suffix from all symbols in funding_rate_history table.
"""

import sqlite3
import sys
import os

# Add parent directory to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.symbol_utils import normalize_symbol

def clean_existing_funding_data():
    """Clean existing funding rate data."""
    db_path = "data/trading_bot.db"

    if not os.path.exists(db_path):
        print(f"Database {db_path} not found")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # Get all symbols that need cleaning
        cursor.execute("""
            SELECT DISTINCT symbol
            FROM funding_rate_history
            WHERE symbol LIKE '%:USD'
        """)

        symbols_to_clean = [row[0] for row in cursor.fetchall()]
        print(f"Found {len(symbols_to_clean)} symbols to clean")

        cleaned_count = 0
        for old_symbol in symbols_to_clean:
            new_symbol = normalize_symbol(old_symbol)
            if new_symbol != old_symbol:
                cursor.execute("""
                    UPDATE funding_rate_history
                    SET symbol = ?
                    WHERE symbol = ?
                """, (new_symbol, old_symbol))
                cleaned_count += 1
                print(f"Cleaned: {old_symbol} → {new_symbol}")

        conn.commit()
        print(f"[SUCCESS] Cleaned {cleaned_count} symbols in funding_rate_history")

        # Verify the cleaning
        cursor.execute("""
            SELECT COUNT(*) FROM funding_rate_history
            WHERE symbol LIKE '%:USD'
        """)
        remaining = cursor.fetchone()[0]
        print(f"Symbols with :USD suffix remaining: {remaining}")

        # Show current symbol distribution
        cursor.execute("""
            SELECT symbol, COUNT(*) as count
            FROM funding_rate_history
            GROUP BY symbol
            ORDER BY count DESC
            LIMIT 5
        """)

        print("\nCurrent funding rate symbols:")
        for row in cursor.fetchall():
            print(f"  {row[0]}: {row[1]} records")

    except Exception as e:
        print(f"[ERROR] Cleaning failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    clean_existing_funding_data()