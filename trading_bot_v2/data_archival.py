"""
Data Archival Policy Engine for Trading Bot.

Manages the complete data lifecycle: hot -> warm -> cold -> delete.
Integrates with TimescaleDB compression policies and provides
configurable retention periods for all time-series data.

Usage:
    from trading_bot_v2.data_archival import ArchivalManager

    manager = ArchivalManager()
    manager.run_archival_cycle()
    manager.compress_old_data()
    manager.enforce_retention()

Environment Variables:
    ARCHIVAL_HOT_RETENTION_HOURS     = int (default: 72)
    ARCHIVAL_WARM_RETENTION_DAYS    = int (default: 30)
    ARCHIVAL_COLD_RETENTION_DAYS    = int (default: 365)
    ARCHIVAL_COMPRESS_AFTER_DAYS    = int (default: 7)
    ARCHIVAL_RETENTION_MARKET_DATA  = int days (default: 365)
    ARCHIVAL_RETENTION_SIGNALS      = int days (default: 180)
    ARCHIVAL_RETENTION_TRADES       = int days (default: 730)  # 2 years
    ARCHIVAL_RETENTION_FUNDING      = int days (default: 365)
    ARCHIVAL_RETENTION_PERFORMANCE  = int days (default: 365)
    ARCHIVAL_BATCH_SIZE             = int (default: 10000)
    ARCHIVAL_DRY_RUN                = "true" | "false" (default: "false")
"""

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .database import get_db_connection, is_postgres, get_backend
from .event_system import get_event_bus, EventType

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================


class DataTier(str, Enum):
    """Data lifecycle tier classification."""

    HOT = "hot"  # Recently ingested, full fidelity, no compression
    WARM = "warm"  # Older data, compressed, still queryable
    COLD = "cold"  # Archive-ready, minimal access patterns
    DELETED = "deleted"  # Past retention, marked for removal


@dataclass
class RetentionPolicy:
    """Retention configuration for a specific table."""

    table_name: str
    hot_hours: int = 72
    warm_days: int = 30
    cold_days: int = 365
    compress_after_days: int = 7
    delete_after_days: Optional[int] = None  # None = keep forever

    @property
    def delete_after_hours(self) -> Optional[int]:
        """Delete threshold in hours."""
        if self.delete_after_days is None:
            return None
        return self.delete_after_days * 24

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table": self.table_name,
            "hot_hours": self.hot_hours,
            "warm_days": self.warm_days,
            "cold_days": self.cold_days,
            "compress_after_days": self.compress_after_days,
            "delete_after_days": self.delete_after_days,
        }


@dataclass
class ArchivalResult:
    """Result of an archival operation."""

    operation: str
    table_name: str
    rows_affected: int = 0
    bytes_freed: int = 0
    duration_seconds: float = 0.0
    success: bool = True
    error_message: str = ""
    tier_from: Optional[DataTier] = None
    tier_to: Optional[DataTier] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation,
            "table": self.table_name,
            "rows_affected": self.rows_affected,
            "bytes_freed": self.bytes_freed,
            "duration_seconds": round(self.duration_seconds, 3),
            "success": self.success,
            "error_message": self.error_message,
            "tier_from": self.tier_from.value if self.tier_from else None,
            "tier_to": self.tier_to.value if self.tier_to else None,
        }


@dataclass
class ArchivalReport:
    """Aggregated report from a full archival cycle."""

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    results: List[ArchivalResult] = field(default_factory=list)
    dry_run: bool = False

    @property
    def total_rows_affected(self) -> int:
        return sum(r.rows_affected for r in self.results)

    @property
    def total_bytes_freed(self) -> int:
        return sum(r.bytes_freed for r in self.results)

    @property
    def total_duration(self) -> float:
        return sum(r.duration_seconds for r in self.results)

    @property
    def all_successful(self) -> bool:
        return all(r.success for r in self.results)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "dry_run": self.dry_run,
            "total_rows_affected": self.total_rows_affected,
            "total_bytes_freed": self.total_bytes_freed,
            "total_duration_seconds": round(self.total_duration, 3),
            "all_successful": self.all_successful,
            "results": [r.to_dict() for r in self.results],
        }

    def summary(self) -> str:
        status = "OK" if self.all_successful else "ERRORS"
        return (
            f"Archival {status}: {len(self.results)} operations, "
            f"{self.total_rows_affected:,} rows, "
            f"{self.total_bytes_freed:,} bytes freed, "
            f"{self.total_duration:.2f}s"
        )


# ============================================================================
# Default Retention Policies
# ============================================================================

DEFAULT_POLICIES: Dict[str, RetentionPolicy] = {
    "market_data": RetentionPolicy(
        table_name="market_data",
        hot_hours=72,
        warm_days=30,
        cold_days=365,
        compress_after_days=7,
        delete_after_days=int(os.getenv("ARCHIVAL_RETENTION_MARKET_DATA", "365")),
    ),
    "signals": RetentionPolicy(
        table_name="signals",
        hot_hours=48,
        warm_days=14,
        cold_days=180,
        compress_after_days=7,
        delete_after_days=int(os.getenv("ARCHIVAL_RETENTION_SIGNALS", "180")),
    ),
    "trades": RetentionPolicy(
        table_name="trades",
        hot_hours=168,  # 7 days
        warm_days=90,
        cold_days=365,
        compress_after_days=30,
        delete_after_days=int(os.getenv("ARCHIVAL_RETENTION_TRADES", "730")),
    ),
    "funding_payments": RetentionPolicy(
        table_name="funding_payments",
        hot_hours=168,
        warm_days=30,
        cold_days=365,
        compress_after_days=7,
        delete_after_days=int(os.getenv("ARCHIVAL_RETENTION_FUNDING", "365")),
    ),
    "funding_rate_history": RetentionPolicy(
        table_name="funding_rate_history",
        hot_hours=168,
        warm_days=30,
        cold_days=365,
        compress_after_days=7,
        delete_after_days=365,
    ),
    "performance_metrics": RetentionPolicy(
        table_name="performance_metrics",
        hot_hours=24,
        warm_days=90,
        cold_days=365,
        compress_after_days=30,
        delete_after_days=int(os.getenv("ARCHIVAL_RETENTION_PERFORMANCE", "365")),
    ),
    "balance_history": RetentionPolicy(
        table_name="balance_history",
        hot_hours=24,
        warm_days=30,
        cold_days=365,
        compress_after_days=7,
        delete_after_days=365,
    ),
    "regime_history": RetentionPolicy(
        table_name="regime_history",
        hot_hours=24,
        warm_days=30,
        cold_days=180,
        compress_after_days=7,
        delete_after_days=180,
    ),
    "grid_events": RetentionPolicy(
        table_name="grid_events",
        hot_hours=48,
        warm_days=14,
        cold_days=90,
        compress_after_days=7,
        delete_after_days=90,
    ),
}


# ============================================================================
# Archival Manager
# ============================================================================


class ArchivalManager:
    """
    Manages the complete data lifecycle for the trading bot.

    Provides:
      - Tier-based data classification (hot/warm/cold/deleted)
      - TimescaleDB compression for PostgreSQL
      - SQLite archive table creation and data migration
      - Configurable retention policies per table
      - Dry-run mode for safe testing
      - Event publishing for archival notifications

    PostgreSQL (TimescaleDB):
      - Uses native compression policies
      - Uses native retention policies
      - Can decompress for queries when needed

    SQLite:
      - Creates archive tables (_archive suffix)
      - Moves old data to archive tables
      - Deletes data past retention period
    """

    def __init__(
        self,
        policies: Optional[Dict[str, RetentionPolicy]] = None,
        dry_run: bool = False,
    ) -> None:
        """
        Initialize the archival manager.

        Args:
            policies: Custom retention policies (uses defaults if None)
            dry_run: If True, only report what would be done
        """
        self.policies = policies or DEFAULT_POLICIES.copy()
        self.dry_run = dry_run or os.getenv("ARCHIVAL_DRY_RUN", "false").lower() in (
            "true",
            "1",
            "yes",
        )
        self.batch_size = int(os.getenv("ARCHIVAL_BATCH_SIZE", "10000"))
        self.event_bus = get_event_bus()

        logger.info(
            f"ArchivalManager initialized "
            f"(backend={get_backend()}, dry_run={self.dry_run}, "
            f"tables={len(self.policies)})"
        )

    def get_tier_for_age(self, table_name: str, age_hours: float) -> DataTier:
        """
        Determine the data tier based on age.

        Args:
            table_name: Table name to look up policy
            age_hours: Age of the data in hours

        Returns:
            DataTier classification
        """
        policy = self.policies.get(table_name)
        if policy is None:
            return DataTier.HOT

        if age_hours <= policy.hot_hours:
            return DataTier.HOT
        elif age_hours <= policy.warm_days * 24:
            return DataTier.WARM
        elif age_hours <= policy.cold_days * 24:
            return DataTier.COLD
        else:
            return DataTier.DELETED

    # ========================================================================
    # PostgreSQL (TimescaleDB) Operations
    # ========================================================================

    def _compress_timescaledb_table(self, table_name: str) -> ArchivalResult:
        """
        Trigger compression on a TimescaleDB hypertable.

        Args:
            table_name: Table to compress

        Returns:
            ArchivalResult with operation details
        """
        result = ArchivalResult(operation="compress", table_name=table_name)
        start = time.monotonic()

        try:
            with get_db_connection() as conn:
                # Check if compression policy already exists
                policy_check = conn.execute(
                    """
                    SELECT count(*) FROM timescaledb_information.jobs
                    WHERE hypertable_name = %s
                      AND proc_name = 'policy_compression'
                    """,
                    (table_name,),
                ).fetchone()
                has_policy = (policy_check[0] if policy_check else 0) > 0

                if not has_policy:
                    # Add compression policy based on retention config
                    policy = self.policies.get(table_name)
                    compress_days = policy.compress_after_days if policy else 7

                    if not self.dry_run:
                        conn.execute(
                            f"""
                            SELECT add_compression_policy(
                                '{table_name}',
                                compress_after => INTERVAL '{compress_days} days',
                                if_not_exists => TRUE
                            )
                            """
                        )
                        result.rows_affected = 1
                        logger.info(
                            f"Added compression policy for {table_name} "
                            f"(after {compress_days} days)"
                        )
                    else:
                        result.rows_affected = 0
                        logger.info(
                            f"[DRY RUN] Would add compression policy for "
                            f"{table_name} (after {compress_days} days)"
                        )

                # Manually compress any eligible uncompressed chunks
                if not self.dry_run:
                    compress_result = conn.execute(
                        f"""
                        SELECT compress_chunk(c.chunk_schema || '.' || c.chunk_name)
                        FROM timescaledb_information.chunks c
                        WHERE c.hypertable_name = '{table_name}'
                          AND NOT c.is_compressed
                          AND c.range_start < NOW() - INTERVAL '7 days'
                        """
                    )
                    compressed_count = (
                        compress_result.rowcount if compress_result else 0
                    )
                    result.rows_affected += compressed_count
                    if compressed_count > 0:
                        logger.info(
                            f"Compressed {compressed_count} chunks in {table_name}"
                        )

        except Exception as e:
            result.success = False
            result.error_message = str(e)
            logger.error(f"Compression failed for {table_name}: {e}")

        result.duration_seconds = time.monotonic() - start
        return result

    def _enforce_retention_timescaledb(self, table_name: str) -> ArchivalResult:
        """
        Enforce retention policy on a TimescaleDB hypertable.

        Args:
            table_name: Table to enforce retention on

        Returns:
            ArchivalResult with operation details
        """
        result = ArchivalResult(operation="retention", table_name=table_name)
        start = time.monotonic()
        policy = self.policies.get(table_name)

        if policy is None or policy.delete_after_days is None:
            result.error_message = f"No retention policy for {table_name}"
            return result

        try:
            with get_db_connection() as conn:
                # Check if retention policy exists
                policy_check = conn.execute(
                    """
                    SELECT count(*) FROM timescaledb_information.jobs
                    WHERE hypertable_name = %s
                      AND proc_name = 'policy_retention'
                    """,
                    (table_name,),
                ).fetchone()
                has_policy = (policy_check[0] if policy_check else 0) > 0

                if not has_policy:
                    if not self.dry_run:
                        conn.execute(
                            f"""
                            SELECT add_retention_policy(
                                '{table_name}',
                                drop_after => INTERVAL '{policy.delete_after_days} days',
                                if_not_exists => TRUE
                            )
                            """
                        )
                        logger.info(
                            f"Added retention policy for {table_name}: "
                            f"drop after {policy.delete_after_days} days"
                        )
                    else:
                        logger.info(
                            f"[DRY RUN] Would add retention policy for "
                            f"{table_name}: drop after {policy.delete_after_days} days"
                        )

                # Count how many rows would be dropped
                time_col = self._get_time_column(table_name)
                if time_col:
                    count_result = conn.execute(
                        f"""
                        SELECT count(*) FROM {table_name}
                        WHERE {time_col} < NOW() - INTERVAL '{policy.delete_after_days} days'
                        """
                    )
                    count_row = count_result.fetchone()
                    result.rows_affected = count_row[0] if count_row else 0

        except Exception as e:
            result.success = False
            result.error_message = str(e)
            logger.error(f"Retention enforcement failed for {table_name}: {e}")

        result.duration_seconds = time.monotonic() - start
        return result

    # ========================================================================
    # SQLite Operations
    # ========================================================================

    def _archive_sqlite_table(self, table_name: str) -> ArchivalResult:
        """
        Archive old data in SQLite by moving to an archive table.

        Creates a {table_name}_archive table and moves rows older
        than the cold threshold.

        Args:
            table_name: Table to archive from

        Returns:
            ArchivalResult with operation details
        """
        result = ArchivalResult(operation="archive", table_name=table_name)
        start = time.monotonic()
        policy = self.policies.get(table_name)

        if policy is None:
            result.error_message = f"No policy for {table_name}"
            result.duration_seconds = time.monotonic() - start
            return result

        time_col = self._get_time_column(table_name)
        if time_col is None:
            result.error_message = f"Cannot determine time column for {table_name}"
            result.duration_seconds = time.monotonic() - start
            return result

        cutoff_hours = policy.cold_days * 24
        archive_table = f"{table_name}_archive"

        try:
            with get_db_connection() as conn:
                # Ensure archive table exists
                conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {archive_table}
                    AS SELECT * FROM {table_name} WHERE 1=0
                    """
                )

                # Count rows to archive
                count_result = conn.execute(
                    f"""
                    SELECT count(*) FROM {table_name}
                    WHERE {time_col} < datetime('now', ?)
                    """,
                    (f"-{cutoff_hours} hours",),
                )
                count_row = count_result.fetchone()
                rows_to_archive = count_row[0] if count_row else 0

                result.rows_affected = rows_to_archive

                if rows_to_archive > 0:
                    if not self.dry_run:
                        # Copy to archive
                        conn.execute(
                            f"""
                            INSERT OR IGNORE INTO {archive_table}
                            SELECT * FROM {table_name}
                            WHERE {time_col} < datetime('now', ?)
                            """,
                            (f"-{cutoff_hours} hours",),
                        )

                        # Delete from original
                        conn.execute(
                            f"""
                            DELETE FROM {table_name}
                            WHERE {time_col} < datetime('now', ?)
                            """,
                            (f"-{cutoff_hours} hours",),
                        )

                        conn.commit()
                        logger.info(
                            f"Archived {rows_to_archive} rows from "
                            f"{table_name} to {archive_table}"
                        )
                    else:
                        logger.info(
                            f"[DRY RUN] Would archive {rows_to_archive} rows "
                            f"from {table_name} to {archive_table}"
                        )

        except Exception as e:
            result.success = False
            result.error_message = str(e)
            logger.error(f"SQLite archive failed for {table_name}: {e}")

        result.duration_seconds = time.monotonic() - start
        return result

    def _enforce_retention_sqlite(self, table_name: str) -> ArchivalResult:
        """
        Delete data past retention period in SQLite.

        Args:
            table_name: Table to enforce retention on

        Returns:
            ArchivalResult with operation details
        """
        result = ArchivalResult(operation="delete", table_name=table_name)
        start = time.monotonic()
        policy = self.policies.get(table_name)

        if policy is None or policy.delete_after_days is None:
            result.error_message = f"No retention policy for {table_name}"
            result.duration_seconds = time.monotonic() - start
            return result

        time_col = self._get_time_column(table_name)
        if time_col is None:
            result.error_message = f"Cannot determine time column for {table_name}"
            result.duration_seconds = time.monotonic() - start
            return result

        try:
            with get_db_connection() as conn:
                # Count rows to delete
                count_result = conn.execute(
                    f"""
                    SELECT count(*) FROM {table_name}
                    WHERE {time_col} < datetime('now', ?)
                    """,
                    (f"-{policy.delete_after_days} days",),
                )
                count_row = count_result.fetchone()
                result.rows_affected = count_row[0] if count_row else 0

                # Also check archive table
                archive_table = f"{table_name}_archive"
                archive_count = 0
                try:
                    ac_result = conn.execute(
                        f"""
                        SELECT count(*) FROM {archive_table}
                        WHERE {time_col} < datetime('now', ?)
                        """,
                        (f"-{policy.delete_after_days} days",),
                    )
                    ac_row = ac_result.fetchone()
                    archive_count = ac_row[0] if ac_row else 0
                except Exception:
                    pass  # Archive table may not exist

                total_delete = result.rows_affected + archive_count

                if total_delete > 0:
                    if not self.dry_run:
                        # Delete from main table
                        if result.rows_affected > 0:
                            conn.execute(
                                f"""
                                DELETE FROM {table_name}
                                WHERE {time_col} < datetime('now', ?)
                                """,
                                (f"-{policy.delete_after_days} days",),
                            )

                        # Delete from archive table
                        if archive_count > 0:
                            conn.execute(
                                f"""
                                DELETE FROM {archive_table}
                                WHERE {time_col} < datetime('now', ?)
                                """,
                                (f"-{policy.delete_after_days} days",),
                            )

                        conn.commit()
                        result.rows_affected = total_delete
                        logger.info(
                            f"Deleted {total_delete} rows from "
                            f"{table_name} (main={result.rows_affected}, "
                            f"archive={archive_count})"
                        )
                    else:
                        result.rows_affected = total_delete
                        logger.info(
                            f"[DRY RUN] Would delete {total_delete} rows "
                            f"from {table_name}"
                        )

        except Exception as e:
            result.success = False
            result.error_message = str(e)
            logger.error(f"SQLite retention failed for {table_name}: {e}")

        result.duration_seconds = time.monotonic() - start
        return result

    # ========================================================================
    # Public API
    # ========================================================================

    def compress_old_data(self) -> List[ArchivalResult]:
        """
        Compress data that has aged past the compression threshold.

        For PostgreSQL: Uses TimescaleDB native compression.
        For SQLite: Moves data to archive tables.

        Returns:
            List of ArchivalResult for each table processed
        """
        results: List[ArchivalResult] = []

        for table_name in self.policies:
            if is_postgres():
                result = self._compress_timescaledb_table(table_name)
            else:
                result = self._archive_sqlite_table(table_name)
            results.append(result)

            if not result.success:
                logger.warning(
                    f"Compression/archive failed for {table_name}: "
                    f"{result.error_message}"
                )

        return results

    def enforce_retention(self) -> List[ArchivalResult]:
        """
        Enforce retention policies, deleting data past the configured limit.

        Returns:
            List of ArchivalResult for each table processed
        """
        results: List[ArchivalResult] = []

        for table_name in self.policies:
            if is_postgres():
                result = self._enforce_retention_timescaledb(table_name)
            else:
                result = self._enforce_retention_sqlite(table_name)
            results.append(result)

            if not result.success:
                logger.warning(
                    f"Retention enforcement failed for {table_name}: "
                    f"{result.error_message}"
                )

        return results

    def run_archival_cycle(self) -> ArchivalReport:
        """
        Run a complete archival cycle: compress then enforce retention.

        This is the primary entry point for scheduled archival operations.

        Returns:
            ArchivalReport with all operation results
        """
        report = ArchivalReport(dry_run=self.dry_run)

        logger.info(f"Starting archival cycle (dry_run={self.dry_run})")

        # Phase 1: Compress old data
        logger.info("Phase 1: Compressing old data...")
        compress_results = self.compress_old_data()
        report.results.extend(compress_results)

        # Phase 2: Enforce retention
        logger.info("Phase 2: Enforcing retention policies...")
        retention_results = self.enforce_retention()
        report.results.extend(retention_results)

        report.completed_at = datetime.now(timezone.utc)

        # Publish event
        try:
            self.event_bus.publish_event(
                EventType.COMPONENT_HEALTH_CHECK,
                {
                    "source": "archival_manager",
                    "report": report.to_dict(),
                    "status": "completed"
                    if report.all_successful
                    else "partial_failure",
                },
                "ArchivalManager",
            )
        except Exception as e:
            logger.warning(f"Failed to publish archival event: {e}")

        logger.info(report.summary())
        return report

    def get_table_sizes(self) -> Dict[str, Dict[str, Any]]:
        """
        Get size information for all managed tables.

        Returns:
            Dictionary mapping table names to size info
        """
        sizes: Dict[str, Dict[str, Any]] = {}

        try:
            with get_db_connection() as conn:
                if is_postgres():
                    rows = conn.execute(
                        """
                        SELECT
                            hypertable_name,
                            pg_size_pretty(hypertable_size(format('%I', hypertable_name)::regclass)),
                            hypertable_size(format('%I', hypertable_name)::regclass),
                            (SELECT count(*) FROM information_schema.columns
                             WHERE table_name = h.hypertable_name) as column_count
                        FROM timescaledb_information.hypertables h
                        ORDER BY hypertable_size(format('%I', hypertable_name)::regclass) DESC
                        """
                    ).fetchall()

                    for row in rows:
                        sizes[row[0]] = {
                            "size_pretty": row[1],
                            "size_bytes": row[2],
                        }
                else:
                    # SQLite: query page count and page size
                    for table_name in self.policies:
                        try:
                            # Check if table exists
                            check = conn.execute(
                                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                                (table_name,),
                            ).fetchone()
                            if not check:
                                continue

                            count_result = conn.execute(
                                f"SELECT count(*) FROM {table_name}"
                            )
                            count_row = count_result.fetchone()
                            row_count = count_row[0] if count_row else 0

                            # Check archive table
                            archive_count = 0
                            try:
                                ac = conn.execute(
                                    f"SELECT count(*) FROM {table_name}_archive"
                                )
                                ac_row = ac.fetchone()
                                archive_count = ac_row[0] if ac_row else 0
                            except Exception:
                                pass

                            sizes[table_name] = {
                                "row_count": row_count,
                                "archive_row_count": archive_count,
                            }
                        except Exception as e:
                            logger.debug(f"Could not get size for {table_name}: {e}")

        except Exception as e:
            logger.error(f"Failed to get table sizes: {e}")

        return sizes

    def get_tier_distribution(self) -> Dict[str, Dict[str, int]]:
        """
        Get the distribution of data across tiers for each table.

        Returns:
            Dictionary mapping table names to tier counts
        """
        distribution: Dict[str, Dict[str, int]] = {}

        try:
            with get_db_connection() as conn:
                for table_name, policy in self.policies.items():
                    time_col = self._get_time_column(table_name)
                    if time_col is None:
                        continue

                    tier_counts: Dict[str, int] = {}

                    for tier, (min_hours, max_hours) in {
                        "hot": (0, policy.hot_hours),
                        "warm": (policy.hot_hours, policy.warm_days * 24),
                        "cold": (policy.warm_days * 24, policy.cold_days * 24),
                        "deleted": (policy.cold_days * 24, None),
                    }.items():
                        try:
                            if is_postgres():
                                if max_hours is not None:
                                    row = conn.execute(
                                        f"""
                                        SELECT count(*) FROM {table_name}
                                        WHERE {time_col} >= NOW() - INTERVAL '{max_hours} hours'
                                          AND {time_col} < NOW() - INTERVAL '{min_hours} hours'
                                        """
                                    ).fetchone()
                                else:
                                    row = (
                                        conn.execute(
                                            f"""
                                        SELECT count(*) FROM {table_name}
                                        WHERE {time_col} < NOW() - INTERVAL '{max_hours} hours'
                                    """
                                        ).fetchone()
                                        if max_hours is not None
                                        else None
                                    )
                            else:
                                if max_hours is not None:
                                    row = conn.execute(
                                        f"""
                                        SELECT count(*) FROM {table_name}
                                        WHERE {time_col} >= datetime('now', ?)
                                          AND {time_col} < datetime('now', ?)
                                        """,
                                        (f"-{max_hours} hours", f"-{min_hours} hours"),
                                    ).fetchone()
                                else:
                                    row = conn.execute(
                                        f"""
                                        SELECT count(*) FROM {table_name}
                                        WHERE {time_col} < datetime('now', ?)
                                        """,
                                        (f"-{min_hours} hours",),
                                    ).fetchone()

                            tier_counts[tier] = row[0] if row else 0
                        except Exception:
                            tier_counts[tier] = 0

                    distribution[table_name] = tier_counts

        except Exception as e:
            logger.error(f"Failed to get tier distribution: {e}")

        return distribution

    def health_check(self) -> Dict[str, Any]:
        """
        Perform a health check on archival subsystem.

        Returns:
            Dictionary with health status
        """
        status: Dict[str, Any] = {
            "backend": get_backend(),
            "dry_run": self.dry_run,
            "managed_tables": len(self.policies),
            "policies": {
                name: policy.to_dict() for name, policy in self.policies.items()
            },
        }

        try:
            sizes = self.get_table_sizes()
            status["table_sizes"] = sizes
        except Exception as e:
            status["size_error"] = str(e)

        try:
            dist = self.get_tier_distribution()
            status["tier_distribution"] = dist
        except Exception as e:
            status["distribution_error"] = str(e)

        status["is_healthy"] = True
        return status

    # ========================================================================
    # Helpers
    # ========================================================================

    @staticmethod
    def _get_time_column(table_name: str) -> Optional[str]:
        """
        Determine the primary time column for a table.

        Returns the column name used for time-based partitioning/queries.
        """
        # Map table names to their time columns
        time_columns = {
            "market_data": "timestamp",
            "signals": "timestamp",
            "trades": "entry_time",
            "funding_payments": "timestamp",
            "funding_rate_history": "timestamp",
            "performance_metrics": "metric_date",
            "balance_history": "timestamp",
            "regime_history": "timestamp",
            "grid_events": "timestamp",
        }
        return time_columns.get(table_name)

    def update_policy(
        self,
        table_name: str,
        **kwargs: Any,
    ) -> None:
        """
        Update a retention policy for a specific table.

        Args:
            table_name: Table to update policy for
            **kwargs: Fields to update on RetentionPolicy
        """
        if table_name in self.policies:
            policy = self.policies[table_name]
            for key, value in kwargs.items():
                if hasattr(policy, key):
                    setattr(policy, key, value)
            logger.info(f"Updated policy for {table_name}: {kwargs}")
        else:
            # Create new policy
            valid_fields = {k for k in RetentionPolicy.__dataclass_fields__}
            filtered = {k: v for k, v in kwargs.items() if k in valid_fields}
            self.policies[table_name] = RetentionPolicy(
                table_name=table_name, **filtered
            )
            logger.info(f"Created new policy for {table_name}: {filtered}")


# ============================================================================
# CLI Entry Point
# ============================================================================


def main() -> None:
    """CLI entry point for manual archival operations."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Trading Bot Data Archival Manager")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # run command
    run_parser = subparsers.add_parser("run", help="Run full archival cycle")
    run_parser.add_argument(
        "--dry-run", action="store_true", help="Preview without changes"
    )

    # compress command
    compress_parser = subparsers.add_parser("compress", help="Compress old data")
    compress_parser.add_argument(
        "--dry-run", action="store_true", help="Preview without changes"
    )

    # retain command
    retain_parser = subparsers.add_parser("retain", help="Enforce retention policies")
    retain_parser.add_argument(
        "--dry-run", action="store_true", help="Preview without changes"
    )

    # status command
    subparsers.add_parser("status", help="Show archival status")

    # health command
    subparsers.add_parser("health", help="Health check")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    dry_run = getattr(args, "dry_run", False)
    manager = ArchivalManager(dry_run=dry_run)

    if args.command == "run":
        report = manager.run_archival_cycle()
        print(json.dumps(report.to_dict(), indent=2, default=str))

    elif args.command == "compress":
        results = manager.compress_old_data()
        for r in results:
            print(
                f"  {r.table_name}: {r.operation} - "
                f"{'OK' if r.success else 'FAIL'} "
                f"({r.rows_affected} rows, {r.duration_seconds:.2f}s)"
            )

    elif args.command == "retain":
        results = manager.enforce_retention()
        for r in results:
            print(
                f"  {r.table_name}: {r.operation} - "
                f"{'OK' if r.success else 'FAIL'} "
                f"({r.rows_affected} rows, {r.duration_seconds:.2f}s)"
            )

    elif args.command == "status":
        sizes = manager.get_table_sizes()
        dist = manager.get_tier_distribution()
        print("Table Sizes:")
        for name, info in sizes.items():
            print(f"  {name}: {info}")
        print("\nTier Distribution:")
        for name, tiers in dist.items():
            print(f"  {name}: {tiers}")

    elif args.command == "health":
        health = manager.health_check()
        print(json.dumps(health, indent=2, default=str))


if __name__ == "__main__":
    main()
