"""
Verify migration completeness by comparing row counts and spot-checking data.

Compares SQLite and PostgreSQL databases table-by-table, verifies hypertables,
checks compression policies, and generates a verification report.

Usage:
    python scripts/verify_migration.py
    python scripts/verify_migration.py --sqlite-path data/trading_bot.db --sample-size 20
"""

import argparse
import json
import logging
import os
import random
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load .env
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SQLITE_PATH = os.getenv("DATABASE_PATH", "trading_bot.db")
PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
PG_DB = os.getenv("POSTGRES_DB", "trading_bot")
PG_USER = os.getenv("POSTGRES_USER", "trading_bot")
PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", "trading_bot_pass")

# Tables to verify (same order as migration)
TABLES_TO_VERIFY: List[str] = [
    "account_profiles",
    "subaccount_configs",
    "trades",
    "positions",
    "market_data",
    "signals",
    "performance_metrics",
    "funding_payments",
    "funding_rate_history",
    "pacifica_positions",
    "grid_state",
    "grid_levels",
    "regime_history",
    "capital_approvals",
    "balance_history",
    "market_info_history",
    "market_parameter_changes",
    "market_availability_history",
]

# Expected hypertables in TimescaleDB
EXPECTED_HYPERTABLES: List[str] = [
    "trades",
    "market_data",
    "signals",
    "funding_payments",
    "funding_rate_history",
    "performance_metrics",
    "performance_snapshots",
    "balance_history",
    "regime_history",
    "market_info_history",
    "market_parameter_changes",
    "grid_events",
]


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def _get_sqlite_conn(db_path: str) -> sqlite3.Connection:
    """Open SQLite connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _get_pg_conn() -> psycopg2.extensions.connection:
    """Open PostgreSQL connection."""
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASSWORD,
    )


# ---------------------------------------------------------------------------
# Row count comparison
# ---------------------------------------------------------------------------


def _get_sqlite_row_count(conn: sqlite3.Connection, table: str) -> int:
    """Count rows in a SQLite table."""
    try:
        cur = conn.execute(f"SELECT COUNT(*) FROM '{table}'")
        return cur.fetchone()[0]
    except Exception as e:
        logger.debug(f"  SQLite table '{table}' not accessible: {e}")
        return -1


def _get_pg_row_count(conn: psycopg2.extensions.connection, table: str) -> int:
    """Count rows in a PostgreSQL table."""
    try:
        with conn.cursor() as cur:
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            result = cur.fetchone()
            return result[0] if result is not None else -1
    except Exception as e:
        logger.debug(f"  PG table '{table}' not accessible: {e}")
        return -1


def _compare_row_counts(
    sqlite_conn: sqlite3.Connection,
    pg_conn: psycopg2.extensions.connection,
    tables: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Compare row counts between SQLite and PostgreSQL for each table.

    Returns a dict of {table: {sqlite_count, pg_count, match, status}}.
    """
    results: Dict[str, Dict[str, Any]] = {}

    for table in tables:
        sqlite_count = _get_sqlite_row_count(sqlite_conn, table)
        pg_count = _get_pg_row_count(pg_conn, table)

        if sqlite_count < 0 and pg_count < 0:
            status = "BOTH_MISSING"
            match = False
        elif sqlite_count < 0:
            status = "SQLITE_MISSING"
            match = False
        elif pg_count < 0:
            status = "PG_MISSING"
            match = False
        elif sqlite_count == pg_count:
            status = "MATCH"
            match = True
        else:
            status = "MISMATCH"
            match = False

        results[table] = {
            "sqlite_count": sqlite_count,
            "pg_count": pg_count,
            "match": match,
            "status": status,
        }

    return results


# ---------------------------------------------------------------------------
# Spot-check data integrity
# ---------------------------------------------------------------------------


def _spot_check_table(
    sqlite_conn: sqlite3.Connection,
    pg_conn: psycopg2.extensions.connection,
    table: str,
    sample_size: int = 10,
) -> Dict[str, Any]:
    """Spot-check data integrity by comparing random samples.

    Returns a dict with spot-check results.
    """
    sqlite_count = _get_sqlite_row_count(sqlite_conn, table)
    pg_count = _get_pg_row_count(pg_conn, table)

    if sqlite_count <= 0 or pg_count <= 0:
        return {"table": table, "samples_checked": 0, "passed": 0, "status": "SKIPPED"}

    # Get column names from SQLite
    try:
        cur = sqlite_conn.execute(f"PRAGMA table_info('{table}')")
        sqlite_columns = [row["name"] for row in cur.fetchall()]
    except Exception:
        return {"table": table, "samples_checked": 0, "passed": 0, "status": "ERROR"}

    if not sqlite_columns:
        return {"table": table, "samples_checked": 0, "passed": 0, "status": "NO_COLUMNS"}

    # Get a random sample of rows from SQLite
    sample_count = min(sample_size, sqlite_count)
    try:
        rows = sqlite_conn.execute(
            f"SELECT * FROM '{table}' ORDER BY RANDOM() LIMIT ?", (sample_count,)
        ).fetchall()
    except Exception as exc:
        return {"table": table, "samples_checked": 0, "passed": 0, "status": "ERROR", "detail": str(exc)}

    checked = 0
    passed = 0
    errors: List[str] = []

    for row in rows:
        # Try to find a matching row in PG using a reasonable key
        match_found = False
        try:
            with pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Strategy 1: Try matching on a unique ID if available
                if "id" in sqlite_columns:
                    row_id = row["id"]
                    # Map old SQLite id to check if any row exists (PG has new ids)
                    # Instead, match on business keys
                    pass

                # Strategy 2: Match on first few non-id columns as business keys
                key_columns = [
                    c for c in sqlite_columns
                    if c != "id" and c != "created_at" and c != "updated_at"
                ][:3]  # Use up to 3 business key columns

                if key_columns:
                    where_parts = []
                    params = []
                    for kc in key_columns:
                        val = row[kc]
                        if val is not None:
                            where_parts.append(f'"{kc}" = %s')
                            params.append(val)

                    if where_parts:
                        where_clause = " AND ".join(where_parts)
                        cur.execute(
                            f'SELECT COUNT(*) FROM "{table}" WHERE {where_clause}',
                            params,
                        )
                        pg_result = cur.fetchone()
                        pg_match_count = pg_result[0] if pg_result is not None else 0
                        match_found = pg_match_count > 0
        except Exception as e:
            errors.append(str(e))
            continue

        checked += 1
        if match_found:
            passed += 1

    return {
        "table": table,
        "samples_checked": checked,
        "passed": passed,
        "failed": checked - passed,
        "status": "PASS" if passed == checked else "PARTIAL" if passed > 0 else "FAIL",
        "errors": errors[:3] if errors else [],
    }


# ---------------------------------------------------------------------------
# Hypertable verification
# ---------------------------------------------------------------------------


def _verify_hypertables(pg_conn: psycopg2.extensions.connection) -> Dict[str, Any]:
    """Verify that expected hypertables exist in TimescaleDB."""
    results: Dict[str, Any] = {
        "found": [],
        "missing": [],
        "all_present": False,
    }

    try:
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT hypertable_name FROM timescaledb_information.hypertables "
                "WHERE hypertable_schema = 'public'"
            )
            found = {row[0] for row in cur.fetchall()}
    except Exception as e:
        results["error"] = str(e)
        return results

    for ht in EXPECTED_HYPERTABLES:
        if ht in found:
            results["found"].append(ht)
        else:
            results["missing"].append(ht)

    results["all_present"] = len(results["missing"]) == 0
    return results


# ---------------------------------------------------------------------------
# Compression policy verification
# ---------------------------------------------------------------------------


def _verify_compression_policies(
    pg_conn: psycopg2.extensions.connection,
) -> Dict[str, Any]:
    """Verify that compression policies are active."""
    results: Dict[str, Any] = {
        "policies": [],
        "count": 0,
    }

    try:
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT hypertable_name, config "
                "FROM timescaledb_information.jobs "
                "WHERE proc_name = 'policy_compression' "
                "AND hypertable_schema = 'public'"
            )
            for row in cur.fetchall():
                results["policies"].append(
                    {"table": row[0], "config": row[1]}
                )
            results["count"] = len(results["policies"])
    except Exception as e:
        results["error"] = str(e)

    return results


# ---------------------------------------------------------------------------
# Time-range query performance test
# ---------------------------------------------------------------------------


def _test_query_performance(
    pg_conn: psycopg2.extensions.connection,
) -> Dict[str, Any]:
    """Run sample time-range queries to confirm performance."""
    results: Dict[str, Any] = {}

    queries = [
        {
            "name": "market_data_7d_count",
            "sql": (
                "SELECT COUNT(*) FROM market_data "
                "WHERE timestamp >= NOW() - INTERVAL '7 days'"
            ),
        },
        {
            "name": "trades_30d_count",
            "sql": (
                "SELECT COUNT(*) FROM trades "
                "WHERE entry_time >= NOW() - INTERVAL '30 days'"
            ),
        },
        {
            "name": "signals_7d_count",
            "sql": (
                "SELECT COUNT(*) FROM signals "
                "WHERE timestamp >= NOW() - INTERVAL '7 days'"
            ),
        },
    ]

    try:
        with pg_conn.cursor() as cur:
            for q in queries:
                start = time.time()
                cur.execute(q["sql"])
                row = cur.fetchone()
                count = row[0] if row is not None else 0
                elapsed_ms = (time.time() - start) * 1000
                results[q["name"]] = {
                    "count": count,
                    "elapsed_ms": round(elapsed_ms, 2),
                }
    except Exception as e:
        results["error"] = str(e)

    return results


# ---------------------------------------------------------------------------
# Data type spot-check
# ---------------------------------------------------------------------------


def _verify_data_types(
    pg_conn: psycopg2.extensions.connection,
) -> Dict[str, Any]:
    """Verify that critical data types are correct in PostgreSQL."""
    results: Dict[str, Any] = {}

    checks = [
        {
            "name": "trades_entry_time_timestamptz",
            "sql": (
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'trades' AND column_name = 'entry_time'"
            ),
            "expected": "timestamp with time zone",
        },
        {
            "name": "signals_indicators_jsonb",
            "sql": (
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'signals' AND column_name = 'indicators'"
            ),
            "expected": "jsonb",
        },
        {
            "name": "market_data_timestamp_timestamptz",
            "sql": (
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'market_data' AND column_name = 'timestamp'"
            ),
            "expected": "timestamp with time zone",
        },
        {
            "name": "trades_quantity_double",
            "sql": (
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'trades' AND column_name = 'quantity'"
            ),
            "expected": "double precision",
        },
        {
            "name": "funding_payments_timestamp_timestamptz",
            "sql": (
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'funding_payments' AND column_name = 'timestamp'"
            ),
            "expected": "timestamp with time zone",
        },
    ]

    try:
        with pg_conn.cursor() as cur:
            for check in checks:
                cur.execute(check["sql"])
                row = cur.fetchone()
                actual = row[0] if row else "NOT_FOUND"
                match = actual == check["expected"]
                results[check["name"]] = {
                    "actual": actual,
                    "expected": check["expected"],
                    "match": match,
                }
    except Exception as e:
        results["error"] = str(e)

    return results


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def _print_report(
    row_count_results: Dict[str, Dict[str, Any]],
    spot_check_results: List[Dict[str, Any]],
    hypertable_results: Dict[str, Any],
    compression_results: Dict[str, Any],
    performance_results: Dict[str, Any],
    data_type_results: Dict[str, Any],
) -> bool:
    """Print a formatted verification report. Returns True if all checks pass."""
    all_pass = True

    logger.info("")
    logger.info("=" * 80)
    logger.info("  MIGRATION VERIFICATION REPORT")
    logger.info("=" * 80)
    logger.info("")

    # --- Section 1: Row Count Comparison ---
    logger.info("  1. ROW COUNT COMPARISON")
    logger.info("  " + "-" * 76)
    header = f"  {'Table':<40s} {'SQLite':>10s} {'PG':>10s} {'Status':<12s}"
    logger.info(header)
    logger.info("  " + "-" * 76)

    for table, data in row_count_results.items():
        sc = str(data["sqlite_count"]) if data["sqlite_count"] >= 0 else "N/A"
        pc = str(data["pg_count"]) if data["pg_count"] >= 0 else "N/A"
        status = data["status"]
        marker = "  " if data["match"] or status == "BOTH_MISSING" else " !"
        logger.info(f"  {table:<40s} {sc:>10s} {pc:>10s} {status:<12s}{marker}")
        if not data["match"] and status not in ("BOTH_MISSING",):
            all_pass = False

    logger.info("  " + "-" * 76)
    logger.info("")

    # --- Section 2: Spot-Check Results ---
    logger.info("  2. SPOT-CHECK DATA INTEGRITY")
    logger.info("  " + "-" * 76)
    header = f"  {'Table':<40s} {'Checked':>8s} {'Passed':>8s} {'Status':<10s}"
    logger.info(header)
    logger.info("  " + "-" * 76)

    for sc in spot_check_results:
        status = sc["status"]
        marker = "  " if status in ("PASS", "SKIPPED") else " !"
        logger.info(
            f"  {sc['table']:<40s} {sc['samples_checked']:>8d} "
            f"{sc['passed']:>8d} {status:<10s}{marker}"
        )
        if status not in ("PASS", "SKIPPED"):
            all_pass = False

    logger.info("  " + "-" * 76)
    logger.info("")

    # --- Section 3: Hypertable Verification ---
    logger.info("  3. TIMESCALEDB HYPERTABLES")
    logger.info("  " + "-" * 76)
    if "error" in hypertable_results:
        logger.info(f"  ERROR: {hypertable_results['error']}")
        all_pass = False
    else:
        for ht in hypertable_results.get("found", []):
            logger.info(f"  [OK] {ht}")
        for ht in hypertable_results.get("missing", []):
            logger.info(f"  [MISSING] {ht}")
            all_pass = False
        logger.info(
            f"  Found: {len(hypertable_results.get('found', []))} / "
            f"{len(EXPECTED_HYPERTABLES)} expected"
        )
    logger.info("")

    # --- Section 4: Compression Policies ---
    logger.info("  4. COMPRESSION POLICIES")
    logger.info("  " + "-" * 76)
    if "error" in compression_results:
        logger.info(f"  ERROR: {compression_results['error']}")
    else:
        for policy in compression_results.get("policies", []):
            logger.info(f"  [ACTIVE] {policy['table']}")
        logger.info(f"  Total active policies: {compression_results.get('count', 0)}")
    logger.info("")

    # --- Section 5: Query Performance ---
    logger.info("  5. QUERY PERFORMANCE (sample time-range queries)")
    logger.info("  " + "-" * 76)
    if "error" in performance_results:
        logger.info(f"  ERROR: {performance_results['error']}")
    else:
        for name, data in performance_results.items():
            logger.info(
                f"  {name:<40s} {data['count']:>10} rows  {data['elapsed_ms']:>8.2f}ms"
            )
    logger.info("")

    # --- Section 6: Data Type Verification ---
    logger.info("  6. DATA TYPE VERIFICATION")
    logger.info("  " + "-" * 76)
    if "error" in data_type_results:
        logger.info(f"  ERROR: {data_type_results['error']}")
    else:
        for name, data in data_type_results.items():
            marker = "[OK]" if data["match"] else "[FAIL]"
            logger.info(
                f"  {marker} {name}: {data['actual']} (expected: {data['expected']})"
            )
            if not data["match"]:
                all_pass = False
    logger.info("")

    # --- Final Verdict ---
    logger.info("=" * 80)
    if all_pass:
        logger.info("  VERDICT: ALL CHECKS PASSED")
    else:
        logger.info("  VERDICT: SOME CHECKS FAILED -- review details above")
    logger.info("=" * 80)
    logger.info("")

    return all_pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    """Run verification. Returns 0 on success, 1 on failure."""
    # Local config that can be overridden by CLI args
    pg_host = PG_HOST
    pg_port = PG_PORT
    pg_db = PG_DB
    pg_user = PG_USER
    pg_password = PG_PASSWORD

    parser = argparse.ArgumentParser(
        description="Verify Bot 3 SQLite -> PostgreSQL migration"
    )
    parser.add_argument(
        "--sqlite-path",
        default=SQLITE_PATH,
        help=f"Path to SQLite database (default: {SQLITE_PATH})",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=10,
        help="Number of random rows to spot-check per table (default: 10)",
    )
    parser.add_argument("--pg-host", default=PG_HOST)
    parser.add_argument("--pg-port", type=int, default=PG_PORT)
    parser.add_argument("--pg-db", default=PG_DB)
    parser.add_argument("--pg-user", default=PG_USER)
    parser.add_argument("--pg-password", default=PG_PASSWORD)
    args = parser.parse_args()

    pg_host = args.pg_host
    pg_port = args.pg_port
    pg_db = args.pg_db
    pg_user = args.pg_user
    pg_password = args.pg_password

    sqlite_path = args.sqlite_path
    if not os.path.isabs(sqlite_path):
        sqlite_path = str(_PROJECT_ROOT / sqlite_path)

    logger.info("=" * 60)
    logger.info("  Bot 3: Migration Verification")
    logger.info("=" * 60)
    logger.info(f"  SQLite: {sqlite_path}")
    logger.info(f"  PG:     {pg_user}@{pg_host}:{pg_port}/{pg_db}")
    logger.info("")

    # Check SQLite exists
    if not os.path.exists(sqlite_path):
        logger.error(f"SQLite database not found: {sqlite_path}")
        return 1

    start_time = time.time()

    try:
        sqlite_conn = _get_sqlite_conn(sqlite_path)
    except Exception as e:
        logger.error(f"Failed to connect to SQLite: {e}")
        return 1

    try:
        pg_conn = psycopg2.connect(
            host=pg_host, port=pg_port, dbname=pg_db,
            user=pg_user, password=pg_password,
        )
    except Exception as e:
        logger.error(f"Failed to connect to PostgreSQL: {e}")
        sqlite_conn.close()
        return 1

    try:
        # 1. Row count comparison
        logger.info("Comparing row counts...")
        row_count_results = _compare_row_counts(
            sqlite_conn, pg_conn, TABLES_TO_VERIFY
        )

        # 2. Spot-check data integrity
        logger.info("Spot-checking data integrity...")
        spot_check_results: List[Dict[str, Any]] = []
        for table in TABLES_TO_VERIFY:
            sc = _spot_check_table(sqlite_conn, pg_conn, table, args.sample_size)
            spot_check_results.append(sc)

        # 3. Hypertable verification
        logger.info("Verifying hypertables...")
        hypertable_results = _verify_hypertables(pg_conn)

        # 4. Compression policy verification
        logger.info("Verifying compression policies...")
        compression_results = _verify_compression_policies(pg_conn)

        # 5. Query performance test
        logger.info("Testing query performance...")
        performance_results = _test_query_performance(pg_conn)

        # 6. Data type verification
        logger.info("Verifying data types...")
        data_type_results = _verify_data_types(pg_conn)

        # Generate report
        elapsed = time.time() - start_time
        all_pass = _print_report(
            row_count_results,
            spot_check_results,
            hypertable_results,
            compression_results,
            performance_results,
            data_type_results,
        )

        logger.info(f"Verification completed in {elapsed:.1f}s")

        return 0 if all_pass else 1

    except Exception as e:
        logger.error(f"Verification failed: {e}", exc_info=True)
        return 1
    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    sys.exit(main())
