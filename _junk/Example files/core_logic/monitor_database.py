#!/usr/bin/env python3
"""Live database monitor - shows data as it's written"""

import sqlite3
import time
from datetime import datetime

DB_PATH = "data/trading_bot.db"

def monitor_database(duration=10):
    """Monitor database for specified duration in seconds."""
    print("="  * 80)
    print(f"LIVE DATABASE MONITOR - Watching for {duration} seconds")
    print("=" * 80)

    start_time = time.time()
    iteration = 0

    while time.time() - start_time < duration:
        iteration += 1
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Quick counts
        cursor.execute("SELECT COUNT(*) FROM market_info_history")
        market_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM positions")
        pos_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM balance_history")
        balance_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM pacifica_positions")
        pac_pos_count = cursor.fetchone()[0]

        print(f"\r[{datetime.now().strftime('%H:%M:%S')}] Iter {iteration}: "
              f"Markets={market_count} | Positions={pos_count} | "
              f"Pacifica Pos={pac_pos_count} | Balance={balance_count}",
              end='', flush=True)

        # If we found data, show sample
        if market_count > 0 or pos_count > 0 or balance_count > 0:
            print("\n\n" + "=" * 80)
            print("DATA FOUND!")
            print("=" * 80)

            if market_count > 0:
                cursor.execute("SELECT symbol, tick_size, max_leverage FROM market_info_history LIMIT 3")
                print(f"\n[MARKET INFO] ({market_count} rows)")
                for row in cursor.fetchall():
                    print(f"  {row[0]}: tick={row[1]}, leverage={row[2]}")

            if pos_count > 0:
                cursor.execute("SELECT symbol, side, quantity, entry_price FROM positions LIMIT 3")
                print(f"\n[POSITIONS] ({pos_count} rows)")
                for row in cursor.fetchall():
                    print(f"  {row[0]}: {row[1]} {row[2]} @ ${row[3]}")

            if balance_count > 0:
                cursor.execute("SELECT balance, equity, datetime(timestamp, 'unixepoch') FROM balance_history ORDER BY timestamp DESC LIMIT 3")
                print(f"\n[BALANCE HISTORY] ({balance_count} rows)")
                for row in cursor.fetchall():
                    print(f"  {row[2]}: Balance=${row[0]}, Equity=${row[1]}")

            break

        conn.close()
        time.sleep(1)

    print("\n\n" + "=" * 80)
    print("Monitoring complete")
    print("=" * 80)

if __name__ == "__main__":
    monitor_database(15)
