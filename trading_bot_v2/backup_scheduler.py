"""
Backup Scheduler - Automated periodic database backups.

Addresses gap H5 (no automated database backup/restore). Runs
Database.backup_database() on a configurable interval from a daemon
background thread, with timestamped filenames and retention pruning.

Usage:
    from trading_bot_v2.backup_scheduler import BackupScheduler
    from trading_bot_v2.database import DatabaseManager

    scheduler = BackupScheduler(db=DatabaseManager())
    scheduler.start()   # runs one backup immediately, then on interval
    ...
    scheduler.stop()    # prompt shutdown via threading.Event
"""

import glob
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from loguru import logger

from .config import config
from .metrics import metrics

_BACKUP_FILENAME_FMT = "trading_bot_%Y%m%d_%H%M%S.db"
_BACKUP_GLOB_PATTERN = "trading_bot_*.db"


@dataclass
class BackupResult:
    """Outcome of a single backup attempt."""

    success: bool
    path: Optional[str] = None
    error: Optional[str] = None


class BackupScheduler:
    """
    Runs periodic database backups on a background daemon thread.

    The scheduler never raises out of its background loop - a failed
    backup is logged as an ERROR and the loop continues to the next
    scheduled attempt. Call `start()` once; it runs an immediate baseline
    backup before waiting for the configured interval.
    """

    def __init__(
        self,
        db,
        backup_dir: Optional[str] = None,
        interval_hours: Optional[float] = None,
        retention_days: Optional[int] = None,
        enabled: Optional[bool] = None,
    ):
        """
        Initialize the backup scheduler.

        Args:
            db: DatabaseManager instance exposing backup_database(path).
            backup_dir: Directory to write backups to. Defaults to
                config.backup_dir (BACKUP_DIR env var, default "./backups").
            interval_hours: Hours between backups. Defaults to
                config.backup_interval_hours (BACKUP_INTERVAL_HOURS, default 24).
            retention_days: Days to retain backup files before pruning.
                Defaults to config.backup_retention_days
                (BACKUP_RETENTION_DAYS, default 30).
            enabled: Whether the scheduler should actually run. Defaults to
                config.backup_enabled (BACKUP_ENABLED, default true).
        """
        self.db = db
        self.backup_dir = backup_dir if backup_dir is not None else config.backup_dir
        self.interval_hours = (
            interval_hours if interval_hours is not None else config.backup_interval_hours
        )
        self.retention_days = (
            retention_days if retention_days is not None else config.backup_retention_days
        )
        self.enabled = enabled if enabled is not None else config.backup_enabled

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def interval_seconds(self) -> float:
        """Backup interval in seconds."""
        return self.interval_hours * 3600

    def start(self) -> None:
        """
        Start the background backup thread.

        Runs one backup immediately (baseline for a fresh deploy), then
        continues on the configured interval until `stop()` is called.
        No-op if the scheduler is disabled or already running.
        """
        if not self.enabled:
            logger.info("BackupScheduler disabled (BACKUP_ENABLED=false) - not starting")
            return

        if self._thread is not None and self._thread.is_alive():
            logger.warning("BackupScheduler already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="backup-scheduler"
        )
        self._thread.start()
        logger.info(
            f"BackupScheduler started (interval={self.interval_hours}h, "
            f"retention={self.retention_days}d, dir={self.backup_dir})"
        )

    def stop(self) -> None:
        """Signal the background thread to stop and wait briefly for exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        logger.info("BackupScheduler stopped")

    def _run_loop(self) -> None:
        """Background loop: immediate backup, then wait/backup on interval."""
        self.run_backup_now()

        while not self._stop_event.is_set():
            # threading.Event.wait() returns promptly when set() is called,
            # unlike time.sleep() which cannot be interrupted early.
            if self._stop_event.wait(self.interval_seconds):
                break
            if self._stop_event.is_set():
                break
            self.run_backup_now()

    def run_backup_now(self) -> BackupResult:
        """
        Perform a single backup immediately, regardless of the schedule.

        Never raises - failures are logged and reflected in the returned
        BackupResult and the backup_failures_total metric.

        Returns:
            BackupResult with success flag, file path (if successful), and
            error message (if not).
        """
        try:
            os.makedirs(self.backup_dir, exist_ok=True)
            filename = datetime.now(timezone.utc).strftime(_BACKUP_FILENAME_FMT)
            backup_path = os.path.join(self.backup_dir, filename)

            success = self.db.backup_database(backup_path)

            if success:
                metrics.record_backup_success()
                logger.info(f"Database backup created: {backup_path}")
                self._prune_old_backups()
                return BackupResult(success=True, path=backup_path)
            else:
                metrics.record_backup_failure()
                logger.error(f"Database backup failed (backup_database returned False)")
                return BackupResult(success=False, error="backup_database returned False")

        except Exception as e:
            metrics.record_backup_failure()
            logger.error(f"Database backup raised an exception: {e}")
            return BackupResult(success=False, error=str(e))

    def _prune_old_backups(self) -> int:
        """
        Delete backup files older than retention_days.

        Never raises - errors deleting individual files are logged and
        skipped so one bad file does not block pruning the rest.

        Returns:
            Number of files deleted.
        """
        deleted = 0
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
            pattern = os.path.join(self.backup_dir, _BACKUP_GLOB_PATTERN)

            for path in glob.glob(pattern):
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
                    if mtime < cutoff:
                        os.remove(path)
                        deleted += 1
                        logger.debug(f"Pruned old backup: {path}")
                except OSError as e:
                    logger.warning(f"Could not prune backup file {path}: {e}")

            if deleted:
                logger.info(f"Backup retention: pruned {deleted} file(s) older than {self.retention_days}d")
        except Exception as e:
            logger.error(f"Error pruning old backups: {e}")

        return deleted
