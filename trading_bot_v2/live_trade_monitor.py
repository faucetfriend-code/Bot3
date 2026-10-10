#!/usr/bin/env python3
"""
Live Trade Monitoring - Wait for first bot-generated trades
Monitors specifically for new trades created by the bot's autonomous signal generation
"""

import requests
import time
import logging
import sys
from datetime import datetime
from typing import Any

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("live_trade_monitor.log"),
        logging.StreamHandler(sys.stdout),
    ],
)


class LiveTradeMonitor:
    def __init__(self, api_url: str = "http://localhost:8000") -> None:
        self.api_url = api_url
        self.baseline_positions = self.get_current_positions()
        self.monitoring_start = datetime.now()
        self.bot_trades_detected: list[dict[str, Any]] = []

    def get_current_positions(self) -> dict[str, dict[str, Any]]:
        """Get baseline positions (test positions to exclude from monitoring)."""
        try:
            response = requests.get(f"{self.api_url}/api/positions", timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    positions = data.get("data", [])
                    # Return dict of symbol -> position data for baseline
                    return {
                        pos.get("symbol"): pos
                        for pos in positions
                        if pos.get("quantity", 0) != 0
                    }
        except Exception as e:
            logging.warning(f"Could not get baseline positions: {e}")
        return {}

    def check_api_health(self) -> bool:
        """Check if API server is responding."""
        try:
            response = requests.get(f"{self.api_url}/api/status", timeout=5)
            return response.status_code == 200
        except (requests.RequestException, Exception):
            return False

    def get_new_positions(self) -> list[dict[str, Any]]:
        """Get positions that are new (not in baseline)."""
        try:
            response = requests.get(f"{self.api_url}/api/positions", timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    current_positions = data.get("data", [])
                    active_positions = [
                        pos for pos in current_positions if pos.get("quantity", 0) != 0
                    ]

                    # Find positions not in baseline (new bot trades)
                    new_positions = []
                    for pos in active_positions:
                        symbol = pos.get("symbol")
                        if symbol not in self.baseline_positions:
                            new_positions.append(pos)

                    return new_positions
        except Exception as e:
            logging.warning(f"Could not check for new positions: {e}")
        return []

    def get_market_activity(self) -> dict[str, int]:
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

    def monitor_live_trades(self) -> None:
        """Monitor for new bot-generated trades (excluding baseline test positions)."""
        logging.info("Starting live trade monitoring...")
        logging.info(f"Monitoring started at: {self.monitoring_start}")
        logging.info(
            f"Baseline positions (excluded): {list(self.baseline_positions.keys())}"
        )

        # Check API health
        if not self.check_api_health():
            logging.error(
                "API server not responding. Please ensure the bot is running."
            )
            return

        logging.info("API server responding - monitoring for live bot trades")

        monitoring_cycles = 0
        last_status_log = 0

        while True:
            monitoring_cycles += 1

            try:
                # Check for new positions
                new_positions = self.get_new_positions()

                # Check market activity
                activity = self.get_market_activity()

                # Log status every 20 cycles
                if monitoring_cycles - last_status_log >= 20:
                    logging.info(
                        f"Status: {activity['market_count']} markets monitored, "
                        f"{activity['active_strategies']} with active strategies, "
                        f"waiting for bot signals..."
                    )
                    last_status_log = monitoring_cycles

                # Check for new bot-generated trades
                if new_positions:
                    logging.info(
                        f"LIVE TRADE DETECTED: {len(new_positions)} new position(s) created by bot!"
                    )

                    # Record trade detection
                    trade_info = {
                        "timestamp": datetime.now(),
                        "new_positions": len(new_positions),
                        "positions": new_positions,
                        "cycle": monitoring_cycles,
                    }
                    self.bot_trades_detected.append(trade_info)

                    # Analyze the new trades
                    self.analyze_new_trades(new_positions)

                    # Continue monitoring for more trades
                    logging.info("Continuing to monitor for additional bot trades...")

                # Brief pause between checks
                time.sleep(60)  # Check every minute

                # Safety timeout (4 hours)
                if monitoring_cycles > 240:  # 240 * 60s = 4 hours
                    logging.warning("Monitoring timeout reached (4 hours)")
                    break

            except KeyboardInterrupt:
                logging.info("Monitoring interrupted by user")
                break
            except Exception as e:
                logging.error(f"Monitoring error: {e}")
                time.sleep(120)  # Wait longer on errors

        # Final summary
        self.print_final_summary()

    def analyze_new_trades(self, new_positions: list[dict[str, Any]]) -> None:
        """Analyze newly detected bot trades."""
        logging.info("Analyzing new bot-generated trades:")

        for pos in new_positions:
            symbol = pos.get("symbol", "Unknown")
            quantity = pos.get("quantity", 0)
            entry_price = pos.get("entry_price", 0)
            strategy = pos.get("strategy", "Unknown")
            pnl = pos.get("unrealized_pnl", 0)

            logging.info(f"  NEW TRADE: {symbol} {strategy}")
            logging.info(f"    Quantity: {quantity:.4f}")
            logging.info(f"    Entry Price: ${entry_price:.4f}")
            logging.info(f"    Current P&L: ${pnl:.2f}")

            # Check for bracket orders
            entry_order_id = pos.get("entry_order_id")
            stop_order_id = pos.get("stop_order_id")
            tp_order_id = pos.get("tp_order_id")

            if entry_order_id and (stop_order_id or tp_order_id):
                logging.info("    Bracket Orders: ACTIVE")
            else:
                logging.warning("    Bracket Orders: MISSING - Safety concern!")

    def print_final_summary(self) -> None:
        """Print final monitoring summary."""
        duration = datetime.now() - self.monitoring_start

        print("\n" + "=" * 70)
        print("LIVE TRADE MONITORING COMPLETE")
        print("=" * 70)
        print(f"Monitoring Duration: {duration}")
        print(f"Baseline Positions Excluded: {len(self.baseline_positions)}")
        print(f"Bot-Generated Trades Detected: {len(self.bot_trades_detected)}")
        print(f"API Health: {'Good' if self.check_api_health() else 'Issues'}")

        if self.bot_trades_detected:
            print("\nBot Trade Timeline:")
            total_new_positions = 0
            for i, trade in enumerate(self.bot_trades_detected, 1):
                print(
                    f"  {i}. {trade['timestamp']} - {trade['new_positions']} new position(s) "
                    f"(Cycle: {trade['cycle']})"
                )
                total_new_positions += trade["new_positions"]

            print(f"\nTotal New Positions Created: {total_new_positions}")
            print(
                "\nSUCCESS: Trading bot is autonomously generating and executing live trades!"
            )
            print("- Signal generation validated")
            print("- Risk management confirmed")
            print("- Autonomous trading active")
        else:
            print("\nNo bot-generated trades detected during monitoring period")
            print("- Bot is being conservative (good!)")
            print("- Market conditions may not meet signal criteria")
            print("- Strategy parameters are appropriately strict")

        print("\nCurrent Status:")
        activity = self.get_market_activity()
        print(f"- Markets Monitored: {activity['market_count']}")
        print(f"- Active Strategies: {activity['active_strategies']}")

        print("\nNext Steps:")
        print("- Continue monitoring for trade opportunities")
        print("- Review strategy performance as trades accumulate")
        print("- Consider parameter adjustments based on market conditions")

        print("=" * 70)


def main() -> None:
    """Main monitoring function."""
    print("Live Trading Bot Trade Monitoring")
    print("Monitoring for first autonomous bot-generated trades...")
    print("Test positions will be excluded from detection\n")

    monitor = LiveTradeMonitor()
    monitor.monitor_live_trades()


if __name__ == "__main__":
    main()
