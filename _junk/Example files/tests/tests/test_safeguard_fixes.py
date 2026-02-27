"""
Tests for the server safeguard system fixes.

Focuses on the critical fixes:
- Path resolution bug
- Database table name correction
- Network connectivity improvements
"""

import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from api_server_safeguards import (
    ServerSafeguards,
    SafeguardStatus,
    SafeguardSeverity,
    run_startup_safeguards
)


class TestSafeguardFixes:
    """Test the specific fixes made to the safeguard system."""

    def test_project_root_path_resolution(self):
        """Test that project root is correctly resolved to parent directory."""
        with patch('api_server_safeguards.__file__', str(Path.cwd() / "api_server_safeguards.py")):
            safeguards = ServerSafeguards()
            assert safeguards.project_root == Path.cwd()

    @patch('api_server_safeguards.sqlite3')
    def test_database_schema_checks_correct_table_names(self, mock_sqlite):
        """Test that database schema checks use correct table names."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Mock all required tables exist
        def mock_fetchone():
            if "account_profiles" in str(mock_cursor.execute.call_args):
                return ("account_profiles",)
            elif "trades" in str(mock_cursor.execute.call_args):
                return ("trades",)
            elif "positions" in str(mock_cursor.execute.call_args):
                return ("positions",)
            return None

        mock_cursor.fetchone.side_effect = mock_fetchone
        mock_sqlite.connect.return_value = mock_conn

        safeguards = ServerSafeguards()
        with patch('api_server_safeguards.Path.exists', return_value=True):
            safeguards.check_database_connectivity()

        schema_check = next(r for r in safeguards.results if "database_schema" in r.name)
        assert schema_check.status == SafeguardStatus.PASS
        assert "account_profiles" not in schema_check.message  # Should not mention missing table

    @patch('api_server_safeguards.requests')
    def test_network_connectivity_with_fallbacks(self, mock_requests):
        """Test network connectivity uses fallback endpoints."""
        # First endpoint fails, second succeeds
        mock_requests.get.side_effect = [
            Exception("First failed"),
            MagicMock(status_code=200)
        ]

        safeguards = ServerSafeguards()
        safeguards.check_network_connectivity()

        network_check = next(r for r in safeguards.results if "network_connectivity" in r.name)
        assert network_check.status == SafeguardStatus.PASS

    @patch.dict(os.environ, {'TESTNET': 'true'}, clear=True)
    @patch('api_server_safeguards.sqlite3')
    @patch('api_server_safeguards.requests')
    def test_full_safeguard_run_with_fixes(self, mock_requests, mock_sqlite):
        """Test complete safeguard run works with all fixes applied."""
        # Mock successful database
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ("table_name",)
        mock_sqlite.connect.return_value = mock_conn

        # Mock successful network
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_requests.get.return_value = mock_response

        # Create temp files
        temp_dir = Path(tempfile.mkdtemp())
        try:
            critical_files = [
                "api_server.py", "database.py", "config.py",
                "main.py", "trading_bot_interface.html"
            ]
            for filename in critical_files:
                (temp_dir / filename).write_text("# Test")

            with patch('api_server_safeguards.__file__', str(temp_dir / "api_server_safeguards.py")):
                can_start, summary = run_startup_safeguards()

                assert can_start is True
                assert summary["critical_failures"] == 0
                assert summary["passed"] >= 5  # File checks should pass
        finally:
            import shutil
            shutil.rmtree(temp_dir)

    def test_file_checks_with_correct_path_resolution(self):
        """Test file existence checks work with correct path resolution."""
        temp_dir = Path(tempfile.mkdtemp())
        try:
            # Create files in temp directory
            (temp_dir / "api_server.py").write_text("# Test")

            with patch('api_server_safeguards.__file__', str(temp_dir / "api_server_safeguards.py")):
                safeguards = ServerSafeguards()
                safeguards.check_file_permissions()

                api_server_check = next(r for r in safeguards.results if "api_server.py" in r.name)
                assert api_server_check.status == SafeguardStatus.PASS
                assert "File accessible" in api_server_check.message
        finally:
            import shutil
            shutil.rmtree(temp_dir)