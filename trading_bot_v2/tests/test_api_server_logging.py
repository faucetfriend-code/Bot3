"""api_server logging-setup guard tests.

Importing trading_bot_v2.api_server used to configure file logging at module
import time unconditionally: it rotated "server logs reports/current.log" and
attached a FileHandler (plus a loguru sink) pointed at it. Under pytest that
meant every collection run either stole the live server's log file or, when
the rotation rename failed (file locked on Windows), appended test debris
into it. The module now skips the entire file-logging block when it detects
pytest; these tests pin that guard.
"""

import logging
from pathlib import Path


class TestPytestFileLoggingGuard:
    """The file-logging setup block must be a no-op under pytest."""

    def test_import_attaches_no_current_log_file_handler(self):
        """Importing api_server under pytest must not add a current.log handler.

        The module may already have been imported by another test; that is
        fine - the guard makes the setup block a no-op either way, so the
        root logger must never hold a FileHandler for current.log.
        """
        import trading_bot_v2.api_server  # noqa: F401  (import is the act under test)

        offenders = [
            handler
            for handler in logging.getLogger().handlers
            if isinstance(handler, logging.FileHandler)
            and Path(handler.baseFilename).name == "current.log"
        ]
        assert offenders == [], (
            "importing trading_bot_v2.api_server under pytest attached a "
            f"FileHandler for current.log: {offenders}"
        )

    def test_guard_flag_detects_pytest(self):
        """The module-level pytest detection must be True in this process."""
        import trading_bot_v2.api_server as api_server

        assert api_server._UNDER_PYTEST is True
