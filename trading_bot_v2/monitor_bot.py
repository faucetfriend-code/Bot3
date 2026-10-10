#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Automated health check script for Trading Bot v2
Run every 5 minutes via cron/scheduler

Usage:
    python monitor_bot.py                    # Run all checks
    python monitor_bot.py --json             # Output JSON format
    python monitor_bot.py --check api        # Run specific check
"""

import requests
import sqlite3
import json
import sys
import argparse
import io
from datetime import datetime
from typing import Dict, Any, Optional

# Fix Windows console encoding for emojis
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# Configuration
API_BASE_URL = "http://localhost:8000/api"
DB_PATH = "data/trading_bot.db"
TIMEOUT = 10  # seconds


class HealthCheck:
    """Health check result container."""

    def __init__(
        self,
        name: str,
        status: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.status = status  # OK, WARN, FAIL, CRITICAL
        self.message = message
        self.details = details or {}
        self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
        }

    def is_critical(self) -> bool:
        """Check if status is critical or fail."""
        return self.status in ["CRITICAL", "FAIL"]


def check_api_health() -> HealthCheck:
    """Check if API server is responding."""
    try:
        r = requests.get(f"{API_BASE_URL}/status", timeout=TIMEOUT)

        if r.status_code != 200:
            return HealthCheck(
                "API Server",
                "FAIL",
                f"HTTP {r.status_code}",
                {"status_code": r.status_code},
            )

        data = r.json()

        if not data.get("success"):
            return HealthCheck("API Server", "FAIL", "API returned success=false", data)

        bot_data = data.get("data", {})
        if not bot_data.get("bot_running"):
            return HealthCheck("API Server", "WARN", "Bot not running", bot_data)

        return HealthCheck(
            "API Server",
            "OK",
            f"Healthy - {bot_data.get('positions_count', 0)} positions, P&L: ${bot_data.get('total_pnl', 0):.2f}",
            bot_data,
        )

    except requests.exceptions.Timeout:
        return HealthCheck("API Server", "CRITICAL", "Request timeout (>10s)")
    except requests.exceptions.ConnectionError:
        return HealthCheck(
            "API Server", "CRITICAL", "Connection refused - server may be down"
        )
    except Exception as e:
        return HealthCheck("API Server", "FAIL", f"Unexpected error: {e}")


def check_position_sync() -> HealthCheck:
    """Check if positions are syncing from Pacifica."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        # Get last update time
        cursor = conn.execute("SELECT MAX(updated_at) as last_update FROM positions")
        row = cursor.fetchone()
        last_update = row["last_update"] if row else None

        # Count open positions
        cursor = conn.execute(
            "SELECT COUNT(*) as count FROM positions WHERE quantity > 0"
        )
        position_count = cursor.fetchone()["count"]

        conn.close()

        if not last_update:
            if position_count == 0:
                return HealthCheck(
                    "Position Sync", "OK", "No open positions", {"position_count": 0}
                )
            else:
                return HealthCheck(
                    "Position Sync",
                    "WARN",
                    f"{position_count} positions but no update timestamp",
                    {"position_count": position_count},
                )

        # Parse timestamp and check freshness
        # Format: "2026-01-11 14:21:09"
        try:
            last_update_dt = datetime.strptime(last_update, "%Y-%m-%d %H:%M:%S")
            age_minutes = (datetime.now() - last_update_dt).total_seconds() / 60

            if age_minutes > 10:
                return HealthCheck(
                    "Position Sync",
                    "FAIL",
                    f"Last sync {age_minutes:.1f} minutes ago (stale)",
                    {"last_update": last_update, "age_minutes": age_minutes},
                )
            elif age_minutes > 5:
                return HealthCheck(
                    "Position Sync",
                    "WARN",
                    f"Last sync {age_minutes:.1f} minutes ago",
                    {"last_update": last_update, "age_minutes": age_minutes},
                )
            else:
                return HealthCheck(
                    "Position Sync",
                    "OK",
                    f"{position_count} positions, updated {age_minutes:.1f}m ago",
                    {"position_count": position_count, "last_update": last_update},
                )
        except ValueError:
            # Timestamp format different, assume OK
            return HealthCheck(
                "Position Sync",
                "OK",
                f"{position_count} positions (updated: {last_update})",
                {"position_count": position_count, "last_update": last_update},
            )

    except sqlite3.Error as e:
        return HealthCheck("Position Sync", "FAIL", f"Database error: {e}")
    except Exception as e:
        return HealthCheck("Position Sync", "FAIL", f"Unexpected error: {e}")


def check_funding_tracking() -> HealthCheck:
    """Check if funding P&L is being tracked."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.execute("""
            SELECT
                COUNT(*) as count,
                SUM(ABS(funding_pnl)) as total_funding,
                SUM(CASE WHEN funding_pnl = 0 THEN 1 ELSE 0 END) as zero_funding_count
            FROM positions
            WHERE quantity > 0
        """)

        row = cursor.fetchone()
        count = row[0]
        total_funding = row[1] or 0
        zero_funding_count = row[2]

        conn.close()

        if count == 0:
            return HealthCheck(
                "Funding Tracking", "OK", "No open positions", {"position_count": 0}
            )

        if count > 0 and zero_funding_count == count:
            return HealthCheck(
                "Funding Tracking",
                "WARN",
                f"All {count} positions have zero funding (may be new or not syncing)",
                {"position_count": count, "zero_funding_count": zero_funding_count},
            )

        avg_funding = total_funding / count if count > 0 else 0

        return HealthCheck(
            "Funding Tracking",
            "OK",
            f"Tracked across {count} positions (avg ${avg_funding:.2f})",
            {
                "position_count": count,
                "total_funding": total_funding,
                "avg_funding": avg_funding,
                "zero_funding_count": zero_funding_count,
            },
        )

    except sqlite3.Error as e:
        return HealthCheck("Funding Tracking", "FAIL", f"Database error: {e}")
    except Exception as e:
        return HealthCheck("Funding Tracking", "FAIL", f"Unexpected error: {e}")


def check_signal_generation() -> HealthCheck:
    """Check if strategies are generating signals."""
    try:
        r = requests.get(f"{API_BASE_URL}/activity", timeout=TIMEOUT)

        if r.status_code != 200:
            return HealthCheck("Signal Generation", "FAIL", f"HTTP {r.status_code}")

        data = r.json()

        if not data.get("success"):
            return HealthCheck(
                "Signal Generation", "FAIL", "API returned success=false"
            )

        markets = data.get("data", [])

        if len(markets) == 0:
            return HealthCheck(
                "Signal Generation", "CRITICAL", "No markets being monitored!"
            )

        if len(markets) < 10:
            return HealthCheck(
                "Signal Generation",
                "WARN",
                f"Only {len(markets)} markets monitored (expected 10)",
                {"market_count": len(markets)},
            )

        # Count markets with active strategies
        active_count = sum(
            1 for m in markets if m.get("active_strategies") not in ["None", "", None]
        )

        # Count by regime
        regime_counts: Dict[str, int] = {}
        for m in markets:
            regime = m.get("regime", "Unknown")
            regime_counts[regime] = regime_counts.get(regime, 0) + 1

        if active_count == 0:
            return HealthCheck(
                "Signal Generation",
                "WARN",
                "No markets with active strategies",
                {"market_count": len(markets), "regime_distribution": regime_counts},
            )

        activity_pct = (active_count / len(markets)) * 100

        return HealthCheck(
            "Signal Generation",
            "OK",
            f"{active_count}/{len(markets)} markets active ({activity_pct:.0f}%)",
            {
                "market_count": len(markets),
                "active_count": active_count,
                "activity_percentage": activity_pct,
                "regime_distribution": regime_counts,
            },
        )

    except requests.exceptions.Timeout:
        return HealthCheck("Signal Generation", "FAIL", "Request timeout")
    except requests.exceptions.ConnectionError:
        return HealthCheck("Signal Generation", "FAIL", "Connection refused")
    except Exception as e:
        return HealthCheck("Signal Generation", "FAIL", f"Unexpected error: {e}")


def check_circuit_breaker() -> HealthCheck:
    """Check circuit breaker status and portfolio P&L."""
    try:
        # Get positions from database
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.execute("""
            SELECT
                SUM(unrealized_pnl) as total_unrealized,
                SUM(funding_pnl) as total_funding,
                COUNT(*) as count
            FROM positions
            WHERE quantity > 0
        """)

        row = cursor.fetchone()
        total_unrealized = row[0] or 0
        total_funding = row[1] or 0
        count = row[2]

        conn.close()

        total_pnl = total_unrealized + total_funding

        # Get account balance from API
        r = requests.get(f"{API_BASE_URL}/status", timeout=TIMEOUT)

        if r.status_code == 200:
            r.json()
            # Note: Account balance may not be in status endpoint
            # This is a simplified check
            pass

        # We can't calculate exact percentage without account balance,
        # but we can warn on large losses
        if total_pnl < -1000:
            return HealthCheck(
                "Circuit Breaker",
                "WARN",
                f"Large loss detected: ${total_pnl:.2f} (check if approaching -10% threshold)",
                {
                    "total_pnl": total_pnl,
                    "unrealized_pnl": total_unrealized,
                    "funding_pnl": total_funding,
                    "position_count": count,
                },
            )
        elif total_pnl < 0:
            return HealthCheck(
                "Circuit Breaker",
                "OK",
                f"Portfolio P&L: ${total_pnl:.2f} (negative but not critical)",
                {
                    "total_pnl": total_pnl,
                    "unrealized_pnl": total_unrealized,
                    "funding_pnl": total_funding,
                    "position_count": count,
                },
            )
        else:
            return HealthCheck(
                "Circuit Breaker",
                "OK",
                f"Portfolio P&L: +${total_pnl:.2f}",
                {
                    "total_pnl": total_pnl,
                    "unrealized_pnl": total_unrealized,
                    "funding_pnl": total_funding,
                    "position_count": count,
                },
            )

    except sqlite3.Error as e:
        return HealthCheck("Circuit Breaker", "FAIL", f"Database error: {e}")
    except Exception as e:
        return HealthCheck("Circuit Breaker", "FAIL", f"Unexpected error: {e}")


def check_position_limits() -> HealthCheck:
    """Check if position limits are being respected."""
    try:
        conn = sqlite3.connect(DB_PATH)

        # Total positions
        cursor = conn.execute("SELECT COUNT(*) FROM positions WHERE quantity > 0")
        total_positions = cursor.fetchone()[0]

        # Grid positions (approximate - check if strategy field exists)
        try:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM positions WHERE quantity > 0 AND strategy = 'grid_trading'"
            )
            grid_positions = cursor.fetchone()[0]
        except (sqlite3.OperationalError, sqlite3.Error):
            grid_positions = 0  # Strategy column may not exist

        conn.close()

        # Limits
        MAX_POSITIONS = 15
        MAX_GRID_POSITIONS = 10

        issues = []

        if total_positions > MAX_POSITIONS:
            return HealthCheck(
                "Position Limits",
                "CRITICAL",
                f"EXCEEDED: {total_positions} positions (max {MAX_POSITIONS})",
                {"total_positions": total_positions, "max_positions": MAX_POSITIONS},
            )

        if grid_positions > MAX_GRID_POSITIONS:
            issues.append(f"Grid: {grid_positions}/{MAX_GRID_POSITIONS}")

        if total_positions >= MAX_POSITIONS * 0.9:
            return HealthCheck(
                "Position Limits",
                "WARN",
                f"Near limit: {total_positions}/{MAX_POSITIONS} positions",
                {"total_positions": total_positions, "max_positions": MAX_POSITIONS},
            )

        return HealthCheck(
            "Position Limits",
            "OK",
            f"{total_positions}/{MAX_POSITIONS} positions (Grid: {grid_positions}/{MAX_GRID_POSITIONS})",
            {
                "total_positions": total_positions,
                "max_positions": MAX_POSITIONS,
                "grid_positions": grid_positions,
                "max_grid_positions": MAX_GRID_POSITIONS,
            },
        )

    except sqlite3.Error as e:
        return HealthCheck("Position Limits", "FAIL", f"Database error: {e}")
    except Exception as e:
        return HealthCheck("Position Limits", "FAIL", f"Unexpected error: {e}")


def run_all_checks() -> Dict[str, HealthCheck]:
    """Run all health checks and return results."""
    checks = {
        "api": check_api_health(),
        "position_sync": check_position_sync(),
        "funding": check_funding_tracking(),
        "signals": check_signal_generation(),
        "circuit_breaker": check_circuit_breaker(),
        "position_limits": check_position_limits(),
    }

    return checks


def print_results(
    checks: Dict[str, HealthCheck], json_output: bool = False
) -> Optional[int]:
    """Print check results in human-readable or JSON format."""

    if json_output:
        output = {
            "timestamp": datetime.now().isoformat(),
            "checks": {name: check.to_dict() for name, check in checks.items()},
            "summary": {
                "total": len(checks),
                "ok": sum(1 for c in checks.values() if c.status == "OK"),
                "warn": sum(1 for c in checks.values() if c.status == "WARN"),
                "fail": sum(1 for c in checks.values() if c.status == "FAIL"),
                "critical": sum(1 for c in checks.values() if c.status == "CRITICAL"),
            },
        }
        print(json.dumps(output, indent=2))
        return None

    # Human-readable output
    print(f"\n{'=' * 70}")
    print("Trading Bot v2 - Health Check Report")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 70}\n")

    # Define emoji for each status
    status_emoji = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌", "CRITICAL": "🚨"}

    # Print each check
    for name, check in checks.items():
        emoji = status_emoji.get(check.status, "❓")
        print(f"{emoji} {check.name:25} [{check.status:8}] {check.message}")

    # Summary
    print(f"\n{'=' * 70}")

    critical_count = sum(1 for c in checks.values() if c.is_critical())
    warn_count = sum(1 for c in checks.values() if c.status == "WARN")
    ok_count = sum(1 for c in checks.values() if c.status == "OK")

    if critical_count > 0:
        print(
            f"🚨 {critical_count} CRITICAL issue(s) detected! Immediate action required."
        )
        return 2
    elif warn_count > 0:
        print(f"⚠️  {warn_count} warning(s) detected. Review recommended.")
        return 1
    else:
        print(f"✅ All {ok_count} checks passed. System operational.")
        return 0


def main() -> Optional[int]:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Health check monitoring for Trading Bot v2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python monitor_bot.py                # Run all checks
  python monitor_bot.py --json         # JSON output
  python monitor_bot.py --check api    # Run specific check
        """,
    )

    parser.add_argument(
        "--json", action="store_true", help="Output results in JSON format"
    )

    parser.add_argument(
        "--check",
        choices=[
            "api",
            "position_sync",
            "funding",
            "signals",
            "circuit_breaker",
            "position_limits",
        ],
        help="Run specific check only",
    )

    args = parser.parse_args()

    # Run checks
    if args.check:
        # Run single check
        check_functions = {
            "api": check_api_health,
            "position_sync": check_position_sync,
            "funding": check_funding_tracking,
            "signals": check_signal_generation,
            "circuit_breaker": check_circuit_breaker,
            "position_limits": check_position_limits,
        }

        check = check_functions[args.check]()
        checks = {args.check: check}
    else:
        # Run all checks
        checks = run_all_checks()

    # Print results
    exit_code = print_results(checks, json_output=args.json)

    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nMonitoring interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\nFatal error: {e}", file=sys.stderr)
        sys.exit(1)
