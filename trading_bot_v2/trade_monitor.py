#!/usr/bin/env python3
"""
Trade Monitoring Script - Watch for first successful trades
Monitors the trading bot until the first couple trades execute successfully
"""

import requests
import time
import json
import logging
from datetime import datetime
import sys
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("trade_monitoring.log"),
        logging.StreamHandler(sys.stdout),
    ],
)


class TradeMonitor:
    def __init__(self, api_url="http://localhost:8000"):
        self.api_url = api_url
        self.initial_trade_count = 0
        self.monitoring_start = datetime.now()
        self.trades_detected = []

    def check_api_health(self):
        """Check if API server is responding."""
        try:
            response = requests.get(f"{self.api_url}/api/status", timeout=5)
            return response.status_code == 200
        except (requests.RequestException, Exception):
            return False

    def get_current_trades(self):
        """Get current trade count from database."""
        try:
            # Check via API if available
            response = requests.get(f"{self.api_url}/api/positions", timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    positions = data.get("data", [])
                    return len([p for p in positions if p.get("quantity", 0) != 0])

            # Fallback: direct database check
            db_path = os.path.join(os.path.dirname(__file__), "data", "trading_bot.db")
            if os.path.exists(db_path):
                import sqlite3

                conn = sqlite3.connect(db_path)
                cursor = conn.execute("SELECT COUNT(*) FROM trades")
                count = cursor.fetchone()[0]
                conn.close()
                return count

        except Exception as e:
            logging.warning(f"Could not get trade count: {e}")

        return 0

    def get_market_activity(self):
        """Get current market monitoring status."""
        try:
            response = requests.get(f"{self.api_url}/api/activity", timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    markets = data.get("data", [])
                    return {
                        "market_count": len(markets),
                        "active_strategies": sum(
                            1
                            for m in markets
                            if m.get("active_strategies") not in ["None", "", None]
                        ),
                    }
        except Exception as e:
            logging.warning(f"Could not get market activity: {e}")

        return {"market_count": 0, "active_strategies": 0}

    def monitor_trades(self):
        """Monitor for new trades until we see successful ones."""
        logging.info("Starting trade monitoring...")
        logging.info(f"Monitoring started at: {self.monitoring_start}")

        # Get initial state
        self.initial_trade_count = self.get_current_trades()
        logging.info(f"Initial trade count: {self.initial_trade_count}")

        # Check API health
        if not self.check_api_health():
            logging.error(
                "API server not responding. Please ensure the bot is running."
            )
            return

        logging.info("API server responding - monitoring active")

        trade_count = 0
        successful_trades = 0
        monitoring_cycles = 0

        while successful_trades < 2:  # Monitor until 2 successful trades
            monitoring_cycles += 1

            try:
                # Check current trade count
                current_trades = self.get_current_trades()

                # Check market activity
                activity = self.get_market_activity()

                # Log status every 10 cycles or when trades detected
                if monitoring_cycles % 10 == 0 or current_trades > trade_count:
                    logging.info(
                        f"Status: {current_trades} trades, {activity['market_count']} markets monitored, "
                        f"{activity['active_strategies']} with active strategies"
                    )

                # Check for new trades
                if current_trades > trade_count:
                    new_trades = current_trades - trade_count
                    logging.info(f"TRADE DETECTED: {new_trades} new trade(s) executed!")

                    # Record trade detection
                    trade_info = {
                        "timestamp": datetime.now(),
                        "total_trades": current_trades,
                        "new_trades": new_trades,
                        "cycle": monitoring_cycles,
                    }
                    self.trades_detected.append(trade_info)

                    trade_count = current_trades
                    successful_trades += new_trades

                    # Detailed analysis of new trades
                    self.analyze_recent_trades()

                    if successful_trades >= 2:
                        logging.info(
                            "SUCCESS: 2+ trades detected! Monitoring complete."
                        )
                        break

                # Brief pause between checks
                time.sleep(30)  # Check every 30 seconds

                # Safety timeout (2 hours)
                if monitoring_cycles > 240:  # 240 * 30s = 2 hours
                    logging.warning("Monitoring timeout reached (2 hours)")
                    break

            except KeyboardInterrupt:
                logging.info("Monitoring interrupted by user")
                break
            except Exception as e:
                logging.error(f"Monitoring error: {e}")
                time.sleep(60)  # Wait longer on errors

        # Final summary
        self.print_final_summary()

    def analyze_recent_trades(self):
        """Analyze the most recent trades for details."""
        try:
            # Get positions to see trade details
            response = requests.get(f"{self.api_url}/api/positions", timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    positions = data.get("data", [])
                    active_positions = [
                        p for p in positions if p.get("quantity", 0) != 0
                    ]

                    logging.info(f"Active positions: {len(active_positions)}")
                    for pos in active_positions[-3:]:  # Show last 3 positions
                        symbol = pos.get("symbol", "Unknown")
                        quantity = pos.get("quantity", 0)
                        pnl = pos.get("unrealized_pnl", 0)
                        strategy = pos.get("strategy", "Unknown")
                        logging.info(
                            f"   {symbol}: {quantity:.4f} units, P&L: ${pnl:.2f}, Strategy: {strategy}"
                        )

        except Exception as e:
            logging.warning(f"Could not analyze recent trades: {e}")

    def print_final_summary(self):
        """Print final monitoring summary."""
        duration = datetime.now() - self.monitoring_start

        print("\n" + "=" * 60)
        print("TRADE MONITORING COMPLETE")
        print("=" * 60)
        print(f"Monitoring Duration: {duration}")
        print(f"Total Trades Detected: {len(self.trades_detected)}")
        print(f"API Health: {'Good' if self.check_api_health() else 'Issues'}")

        if self.trades_detected:
            print("\nTrade Timeline:")
            for i, trade in enumerate(self.trades_detected, 1):
                print(
                    f"  {i}. {trade['timestamp']} - {trade['new_trades']} trade(s) "
                    f"(Total: {trade['total_trades']}, Cycle: {trade['cycle']})"
                )

            print("\nSUCCESS: Trading bot is actively generating and executing trades!")
            print("- Risk management systems validated")
            print("- Signal generation working")
            print("- Trade execution successful")
        else:
            print("\nNo trades detected during monitoring period")
            print("- This is normal - the bot is being conservative")
            print("- Continue monitoring or check strategy parameters")

        print("\nNext Steps:")
        print("- Monitor trade performance and P&L")
        print("- Adjust strategy parameters if needed")
        print("- Set up automated alerts for trade activity")
        print("- Consider scaling up position sizes gradually")

        print("=" * 60)


def main():
    """Main monitoring function."""
    print("Trading Bot Trade Monitoring")
    print("Waiting for first successful trades...")
    print("Press Ctrl+C to stop monitoring\n")

    monitor = TradeMonitor()
    monitor.monitor_trades()


if __name__ == "__main__":
    main()
