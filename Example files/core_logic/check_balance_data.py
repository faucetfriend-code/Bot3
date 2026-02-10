#!/usr/bin/env python3
"""Check balance history and deposits/withdrawals data."""

from database import get_db_connection


def check_data():
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Check balance history
        cursor.execute("SELECT COUNT(*) FROM balance_history")
        balance_count = cursor.fetchone()[0]
        print(f"Balance history records: {balance_count}")

        if balance_count > 0:
            cursor.execute(
                """
                SELECT account_id, balance, equity, timestamp
                FROM balance_history
                ORDER BY timestamp DESC
                LIMIT 5
            """
            )
            print("Recent balance history:")
            for row in cursor.fetchall():
                print(
                    f"  Account: {row[0]}, Balance: ${row[1]:.2f}, Equity: ${row[2]:.2f}, Time: {row[3]}"
                )

        # Check deposits/withdrawals
        try:
            cursor.execute("SELECT COUNT(*) FROM deposits_withdrawals")
            dw_count = cursor.fetchone()[0]
            print(f"Deposits/withdrawals records: {dw_count}")

            if dw_count > 0:
                cursor.execute(
                    """
                    SELECT account_id, amount, timestamp, detected, source
                    FROM deposits_withdrawals
                    ORDER BY timestamp DESC
                    LIMIT 5
                """
                )
                print("Recent deposits/withdrawals:")
                for row in cursor.fetchall():
                    print(
                        f"  Account: {row[0]}, Amount: ${row[1]:.2f}, Time: {row[2]}, Detected: {row[3]}, Source: {row[4]}"
                    )
        except Exception as e:
            print(f"Deposits/withdrawals table may not exist yet: {e}")


if __name__ == "__main__":
    check_data()
