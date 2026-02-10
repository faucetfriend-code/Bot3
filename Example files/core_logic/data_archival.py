"""
Data Archival System for Trading Bot

Implements retention policies and automatic archival of historical data
to prevent unbounded memory growth and maintain database performance.

Retention Policy (default):
- trades: 90 days (3 months of active data)
- market_data: 30 days (1 month of price history)
- signals: 60 days (2 months)
- funding_payments: 90 days (3 months)
- balance_history: 180 days (6 months)
- kline_data: 30 days (1 month)
"""

import asyncio
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from loguru import logger
from database import DATABASE_PATH


class DataArchiver:
    """Manages archival of historical data based on retention policies."""

    # Default retention periods (in days)
    DEFAULT_RETENTION = {
        'trades': 90,  # 3 months
        'market_data': 30,  # 1 month
        'signals': 60,  # 2 months
        'funding_payments': 90,  # 3 months
        'funding_rate_history': 90,  # 3 months
        'balance_history': 180,  # 6 months
        'market_info_history': 90,  # 3 months
        'market_parameter_changes': 180,  # 6 months
        'market_availability_history': 365,  # 1 year
        'kline_data': 30,  # 1 month
        'deposits_withdrawals': 365,  # 1 year - compliance
    }

    def __init__(
        self,
        db_path: str = DATABASE_PATH,
        retention_policies: Optional[Dict[str, int]] = None
    ):
        """
        Initialize DataArchiver.

        Args:
            db_path: Path to database file
            retention_policies: Custom retention periods (days) per table
        """
        self.db_path = db_path
        self.retention_policies = retention_policies or self.DEFAULT_RETENTION
        self._archive_tables_created = False

    def get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        return sqlite3.connect(self.db_path)

    def create_archive_tables(self) -> None:
        """Create archive tables if they don't exist."""
        if self._archive_tables_created:
            return

        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Create archive tables with same structure as originals
            archive_tables = [
                ('trades', 'trades_archive'),
                ('market_data', 'market_data_archive'),
                ('signals', 'signals_archive'),
                ('funding_payments', 'funding_payments_archive'),
                ('funding_rate_history', 'funding_rate_history_archive'),
                ('balance_history', 'balance_history_archive'),
                ('market_info_history', 'market_info_history_archive'),
                ('kline_data', 'kline_data_archive'),
            ]

            for source_table, archive_table in archive_tables:
                # Create archive table with same structure
                cursor.execute(f"""
                    CREATE TABLE IF NOT EXISTS {archive_table}
                    AS SELECT * FROM {source_table} WHERE 1=0
                """)

                # Add archived_at column
                try:
                    cursor.execute(f"""
                        ALTER TABLE {archive_table}
                        ADD COLUMN archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    """)
                except sqlite3.OperationalError:
                    # Column already exists
                    pass

            conn.commit()
            self._archive_tables_created = True
            logger.info("Archive tables created successfully")

        except Exception as e:
            logger.error(f"Failed to create archive tables: {e}")
            conn.rollback()
            raise
        finally:
            conn.close()

    def get_cutoff_date(self, table_name: str) -> datetime:
        """
        Get cutoff date for archival based on retention policy.

        Args:
            table_name: Name of table

        Returns:
            Cutoff datetime (data older than this should be archived)
        """
        retention_days = self.retention_policies.get(table_name, 90)
        return datetime.now() - timedelta(days=retention_days)

    def count_archivable_rows(self, table_name: str) -> Tuple[int, datetime]:
        """
        Count rows eligible for archival.

        Args:
            table_name: Name of table

        Returns:
            Tuple of (row_count, cutoff_date)
        """
        cutoff_date = self.get_cutoff_date(table_name)
        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Determine timestamp column name based on table
            timestamp_col = self._get_timestamp_column(table_name)

            cursor.execute(f"""
                SELECT COUNT(*) FROM {table_name}
                WHERE {timestamp_col} < ?
            """, (cutoff_date,))

            count = cursor.fetchone()[0]
            return count, cutoff_date

        finally:
            conn.close()

    def _get_timestamp_column(self, table_name: str) -> str:
        """Get the name of the timestamp column for a table."""
        # Map table names to their timestamp columns
        timestamp_columns = {
            'trades': 'entry_time',
            'market_data': 'timestamp',
            'signals': 'timestamp',
            'funding_payments': 'timestamp',
            'funding_rate_history': 'timestamp',
            'balance_history': 'fetched_at',
            'market_info_history': 'fetched_at',
            'kline_data': 'fetched_at',
            'market_parameter_changes': 'changed_at',
            'market_availability_history': 'status_changed_at',
            'deposits_withdrawals': 'timestamp',
        }
        return timestamp_columns.get(table_name, 'created_at')

    async def archive_table_data(
        self,
        table_name: str,
        dry_run: bool = False
    ) -> Dict[str, any]:
        """
        Archive old data from a specific table.

        Args:
            table_name: Name of table to archive
            dry_run: If True, only count rows without archiving

        Returns:
            Dict with archival statistics
        """
        if table_name not in self.retention_policies:
            logger.warning(f"No retention policy for table {table_name}, skipping")
            return {"status": "skipped", "reason": "no_policy"}

        cutoff_date = self.get_cutoff_date(table_name)
        timestamp_col = self._get_timestamp_column(table_name)
        archive_table = f"{table_name}_archive"

        conn = self.get_connection()
        cursor = conn.cursor()

        try:
            # Count rows to archive
            cursor.execute(f"""
                SELECT COUNT(*) FROM {table_name}
                WHERE {timestamp_col} < ?
            """, (cutoff_date,))
            rows_to_archive = cursor.fetchone()[0]

            if rows_to_archive == 0:
                logger.info(f"No rows to archive in {table_name}")
                return {
                    "status": "no_data",
                    "table": table_name,
                    "rows_archived": 0,
                    "cutoff_date": cutoff_date.isoformat()
                }

            if dry_run:
                logger.info(
                    f"[DRY RUN] Would archive {rows_to_archive} rows from {table_name} "
                    f"(cutoff: {cutoff_date.isoformat()})"
                )
                return {
                    "status": "dry_run",
                    "table": table_name,
                    "rows_to_archive": rows_to_archive,
                    "cutoff_date": cutoff_date.isoformat()
                }

            # Move data to archive table
            cursor.execute(f"""
                INSERT INTO {archive_table}
                SELECT *, CURRENT_TIMESTAMP as archived_at
                FROM {table_name}
                WHERE {timestamp_col} < ?
            """, (cutoff_date,))

            # Delete from active table
            cursor.execute(f"""
                DELETE FROM {table_name}
                WHERE {timestamp_col} < ?
            """, (cutoff_date,))

            conn.commit()

            logger.info(
                f"Archived {rows_to_archive} rows from {table_name} "
                f"(cutoff: {cutoff_date.isoformat()})"
            )

            return {
                "status": "success",
                "table": table_name,
                "rows_archived": rows_to_archive,
                "cutoff_date": cutoff_date.isoformat()
            }

        except Exception as e:
            logger.error(f"Failed to archive {table_name}: {e}")
            conn.rollback()
            return {
                "status": "error",
                "table": table_name,
                "error": str(e)
            }
        finally:
            conn.close()

    async def archive_all_tables(self, dry_run: bool = False) -> Dict[str, any]:
        """
        Archive data from all tables with retention policies.

        Args:
            dry_run: If True, only count rows without archiving

        Returns:
            Dict with summary statistics
        """
        # Ensure archive tables exist
        if not dry_run:
            self.create_archive_tables()

        results = []
        total_rows_archived = 0

        for table_name in self.retention_policies.keys():
            result = await self.archive_table_data(table_name, dry_run=dry_run)
            results.append(result)

            if result["status"] in ["success", "dry_run"]:
                rows = result.get("rows_archived", result.get("rows_to_archive", 0))
                total_rows_archived += rows

        return {
            "timestamp": datetime.now().isoformat(),
            "dry_run": dry_run,
            "total_rows_archived": total_rows_archived,
            "table_results": results
        }

    def get_database_size_mb(self) -> float:
        """Get current database file size in MB."""
        import os
        if os.path.exists(self.db_path):
            size_bytes = os.path.getsize(self.db_path)
            return size_bytes / (1024 * 1024)
        return 0.0

    def get_table_row_counts(self) -> Dict[str, int]:
        """Get row counts for all tables."""
        conn = self.get_connection()
        cursor = conn.cursor()

        row_counts = {}

        try:
            for table_name in self.retention_policies.keys():
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                row_counts[table_name] = cursor.fetchone()[0]

            return row_counts

        finally:
            conn.close()

    async def run_archival_job(self, dry_run: bool = False) -> None:
        """
        Main archival job - run this periodically (e.g., daily).

        Args:
            dry_run: If True, only simulate archival
        """
        logger.info("Starting data archival job...")

        # Get database size before archival
        size_before_mb = self.get_database_size_mb()
        logger.info(f"Database size before archival: {size_before_mb:.2f} MB")

        # Get row counts before
        rows_before = self.get_table_row_counts()
        logger.info(f"Row counts before archival: {rows_before}")

        # Run archival
        summary = await self.archive_all_tables(dry_run=dry_run)

        # Get database size after archival
        size_after_mb = self.get_database_size_mb()
        space_saved_mb = size_before_mb - size_after_mb

        logger.info(
            f"Archival job complete. "
            f"Rows archived: {summary['total_rows_archived']}, "
            f"Space saved: {space_saved_mb:.2f} MB"
        )

        return summary


# Convenience function for scheduled execution
async def run_daily_archival(dry_run: bool = False):
    """Run daily archival job - call from scheduler."""
    archiver = DataArchiver()
    return await archiver.run_archival_job(dry_run=dry_run)


if __name__ == "__main__":
    # Test archival system
    async def test_archival():
        archiver = DataArchiver()

        # Dry run first
        logger.info("Running dry run...")
        summary = await archiver.run_archival_job(dry_run=True)
        print(f"Dry run summary: {summary}")

        # Uncomment to actually archive
        # logger.info("Running actual archival...")
        # summary = await archiver.run_archival_job(dry_run=False)
        # print(f"Archival summary: {summary}")

    asyncio.run(test_archival())
