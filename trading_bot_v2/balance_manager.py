"""
Balance History Manager for Trading Bot.

Provides access to account balance history for risk management and analysis.
Used by both the API server and trading bot components.
"""

import time
from typing import Dict, List
from datetime import datetime
from .database import get_db_connection
from loguru import logger


class BalanceHistoryManager:
    """Manage balance history data for bot decision making and UI."""

    def __init__(self, db_connection_func=None):
        self.get_db_connection = db_connection_func or get_db_connection

    def get_current_balance(self, account_id: str) -> Dict[str, float]:
        """Get latest balance information."""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT balance, equity, available_balance, margin_used, timestamp
                FROM balance_history
                WHERE account_id = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """,
                (account_id,),
            )

            row = cursor.fetchone()
            conn.close()

            if row:
                return {
                    "balance": float(row[0]),
                    "equity": float(row[1]),
                    "available_balance": float(row[2]),
                    "margin_used": float(row[3]),
                    "timestamp": int(row[4]),
                }
            return {}

        except Exception as e:
            logger.error(f"Failed to get current balance: {e}")
            return {}

    def get_balance_history(self, account_id: str, days: int = 30) -> List[Dict]:
        """Get balance history for analysis."""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            # Calculate timestamp for X days ago
            cutoff_time = int(time.time() - (days * 24 * 60 * 60))

            cursor.execute(
                """
                SELECT balance, equity, available_balance, margin_used, timestamp
                FROM balance_history
                WHERE account_id = ? AND timestamp >= ?
                ORDER BY timestamp ASC
            """,
                (account_id, cutoff_time),
            )

            rows = cursor.fetchall()
            conn.close()

            return [
                {
                    "balance": float(row[0]),
                    "equity": float(row[1]),
                    "available_balance": float(row[2]),
                    "margin_used": float(row[3]),
                    "timestamp": int(row[4]),
                }
                for row in rows
            ]

        except Exception as e:
            logger.error(f"Failed to get balance history: {e}")
            return []

    def calculate_daily_pnl(self, account_id: str, days: int = 30) -> List[Dict]:
        """Calculate daily P&L from balance changes."""
        balance_history = self.get_balance_history(account_id, days)

        if len(balance_history) < 2:
            return []

        daily_pnl = []
        prev_equity = balance_history[0]["equity"]

        for entry in balance_history[1:]:
            current_equity = entry["equity"]
            pnl = current_equity - prev_equity
            daily_pnl.append(
                {
                    "date": datetime.fromtimestamp(entry["timestamp"])
                    .date()
                    .isoformat(),
                    "pnl": pnl,
                    "equity": current_equity,
                }
            )
            prev_equity = current_equity

        return daily_pnl

    def get_equity_curve(self, account_id: str, days: int = 30) -> List[float]:
        """Get equity curve for risk analysis."""
        balance_history = self.get_balance_history(account_id, days)
        return [entry["equity"] for entry in balance_history]

    def calculate_max_drawdown(self, account_id: str, days: int = 30) -> float:
        """Calculate maximum drawdown from balance history."""
        equity_curve = self.get_equity_curve(account_id, days)

        if not equity_curve:
            return 0.0

        max_dd = 0.0
        peak = equity_curve[0]

        for equity in equity_curve:
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            max_dd = max(max_dd, dd)

        return max_dd * 100  # Return as percentage
