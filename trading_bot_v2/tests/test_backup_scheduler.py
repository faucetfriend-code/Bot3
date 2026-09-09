"""
Unit tests for BackupScheduler (gap H5: automated database backup).

Covers: immediate backup on start, timestamped filenames, retention
pruning of old backup files, manual trigger via run_backup_now, backup
failure never raising/crashing, and prompt stop() via threading.Event.
"""

import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from trading_bot_v2.backup_scheduler import BackupScheduler, BackupResult


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.backup_database.return_value = True
    return db


@pytest.fixture
def backup_dir(tmp_path):
    return str(tmp_path / "backups")


class TestManualTrigger:
    def test_run_backup_now_creates_file_and_calls_db(self, mock_db, backup_dir):
        scheduler = BackupScheduler(db=mock_db, backup_dir=backup_dir, enabled=True)

        result = scheduler.run_backup_now()

        assert isinstance(result, BackupResult)
        assert result.success is True
        assert result.path is not None
        mock_db.backup_database.assert_called_once()
        called_path = mock_db.backup_database.call_args[0][0]
        assert called_path == result.path
        assert os.path.dirname(called_path) == backup_dir

    def test_backup_dir_created_if_missing(self, mock_db, backup_dir):
        assert not os.path.exists(backup_dir)
        scheduler = BackupScheduler(db=mock_db, backup_dir=backup_dir)
        scheduler.run_backup_now()
        assert os.path.exists(backup_dir)

    def test_filename_matches_timestamp_pattern(self, mock_db, backup_dir):
        scheduler = BackupScheduler(db=mock_db, backup_dir=backup_dir)
        result = scheduler.run_backup_now()

        filename = os.path.basename(result.path)
        assert filename.startswith("trading_bot_")
        assert filename.endswith(".db")
        # trading_bot_YYYYMMDD_HHMMSS.db
        timestamp_part = filename[len("trading_bot_") : -len(".db")]
        # Should parse without raising
        datetime.strptime(timestamp_part, "%Y%m%d_%H%M%S")


class TestBackupFailureHandling:
    def test_backup_database_returns_false_does_not_raise(self, mock_db, backup_dir):
        mock_db.backup_database.return_value = False
        scheduler = BackupScheduler(db=mock_db, backup_dir=backup_dir)

        result = scheduler.run_backup_now()

        assert result.success is False
        assert result.error is not None

    def test_backup_database_raises_does_not_propagate(self, mock_db, backup_dir):
        mock_db.backup_database.side_effect = RuntimeError("disk full")
        scheduler = BackupScheduler(db=mock_db, backup_dir=backup_dir)

        # Should not raise
        result = scheduler.run_backup_now()

        assert result.success is False
        assert "disk full" in result.error

    def test_makedirs_failure_does_not_raise(self, mock_db):
        # Use a path that cannot be created (nested under a file, not a dir)
        scheduler = BackupScheduler(db=mock_db, backup_dir="\x00invalid")
        result = scheduler.run_backup_now()
        assert result.success is False


class TestRetentionPruning:
    def test_prunes_files_older_than_retention(self, mock_db, backup_dir):
        os.makedirs(backup_dir, exist_ok=True)
        old_file = os.path.join(backup_dir, "trading_bot_20200101_000000.db")
        with open(old_file, "w") as f:
            f.write("old")

        old_time = (datetime.now(timezone.utc) - timedelta(days=60)).timestamp()
        os.utime(old_file, (old_time, old_time))

        scheduler = BackupScheduler(
            db=mock_db, backup_dir=backup_dir, retention_days=30
        )
        scheduler.run_backup_now()

        assert not os.path.exists(old_file)

    def test_does_not_prune_recent_files(self, mock_db, backup_dir):
        os.makedirs(backup_dir, exist_ok=True)
        recent_file = os.path.join(backup_dir, "trading_bot_recent.db")
        with open(recent_file, "w") as f:
            f.write("recent")

        scheduler = BackupScheduler(
            db=mock_db, backup_dir=backup_dir, retention_days=30
        )
        scheduler.run_backup_now()

        assert os.path.exists(recent_file)

    def test_prune_ignores_non_matching_files(self, mock_db, backup_dir):
        os.makedirs(backup_dir, exist_ok=True)
        unrelated_file = os.path.join(backup_dir, "notes.txt")
        with open(unrelated_file, "w") as f:
            f.write("keep me")

        old_time = (datetime.now(timezone.utc) - timedelta(days=90)).timestamp()
        os.utime(unrelated_file, (old_time, old_time))

        scheduler = BackupScheduler(
            db=mock_db, backup_dir=backup_dir, retention_days=30
        )
        scheduler.run_backup_now()

        assert os.path.exists(unrelated_file)


class TestStartStop:
    def test_disabled_scheduler_does_not_start_thread(self, mock_db, backup_dir):
        scheduler = BackupScheduler(db=mock_db, backup_dir=backup_dir, enabled=False)
        scheduler.start()
        assert scheduler._thread is None
        mock_db.backup_database.assert_not_called()

    def test_start_runs_immediate_baseline_backup(self, mock_db, backup_dir):
        scheduler = BackupScheduler(
            db=mock_db, backup_dir=backup_dir, interval_hours=24, enabled=True
        )
        scheduler.start()
        try:
            # Give the daemon thread a brief moment to run the immediate backup
            deadline = time.time() + 2
            while mock_db.backup_database.call_count == 0 and time.time() < deadline:
                time.sleep(0.05)
            assert mock_db.backup_database.call_count >= 1
        finally:
            scheduler.stop()

    def test_stop_is_prompt(self, mock_db, backup_dir):
        # Long interval so the loop would otherwise wait a long time
        scheduler = BackupScheduler(
            db=mock_db, backup_dir=backup_dir, interval_hours=1000, enabled=True
        )
        scheduler.start()
        time.sleep(0.1)  # let the immediate backup complete

        start = time.time()
        scheduler.stop()
        elapsed = time.time() - start

        # stop() should return quickly (threading.Event.wait is interruptible),
        # not block for anything close to the 1000-hour interval.
        assert elapsed < 5

    def test_double_start_is_noop(self, mock_db, backup_dir):
        scheduler = BackupScheduler(
            db=mock_db, backup_dir=backup_dir, interval_hours=1000, enabled=True
        )
        scheduler.start()
        try:
            first_thread = scheduler._thread
            scheduler.start()
            assert scheduler._thread is first_thread
        finally:
            scheduler.stop()


class TestConfigDefaults:
    def test_defaults_come_from_config_when_not_overridden(self, mock_db):
        scheduler = BackupScheduler(db=mock_db)
        assert scheduler.backup_dir  # non-empty
        assert scheduler.interval_hours > 0
        assert scheduler.retention_days > 0

    def test_interval_seconds_property(self, mock_db, backup_dir):
        scheduler = BackupScheduler(db=mock_db, backup_dir=backup_dir, interval_hours=2)
        assert scheduler.interval_seconds == 7200
