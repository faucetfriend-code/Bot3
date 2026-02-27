"""
Comprehensive tests for the server safeguard system.

Tests cover:
- File existence checks with correct path resolution
- Database schema validation
- Network connectivity with fallbacks
- Environment variable checks
- Critical import validation
- Overall safeguard orchestration
"""

import os
import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

from api_server_safeguards import (
    ServerSafeguards,
    SafeguardStatus,
    SafeguardSeverity,
    run_startup_safeguards
)


class TestServerSafeguards:
    """Test suite for ServerSafeguards class."""

    @pytest.fixture
    def temp_project_dir(self):
        """Create a temporary project directory structure."""
        temp_dir = Path(tempfile.mkdtemp())

        # Create critical files
        critical_files = [
            "api_server.py",
            "database.py",
            "config.py",
            "main.py",
            "trading_bot_interface.html"
        ]

        for filename in critical_files:
            (temp_dir / filename).write_text("# Test file")

        yield temp_dir

        # Cleanup
        shutil.rmtree(temp_dir)

    @pytest.fixture
    def safeguards(self, temp_project_dir):
        """Create ServerSafeguards instance with temp directory."""
        with patch('api_server_safeguards.Path') as mock_path:
            mock_path.__file__ = str(temp_project_dir / "api_server_safeguards.py")
            mock_path.return_value.parent = temp_project_dir
            safeguards = ServerSafeguards()
            safeguards.project_root = temp_project_dir
            return safeguards

    def test_project_root_resolution(self, temp_project_dir):
        """Test that project root is correctly resolved."""
        with patch('api_server_safeguards.__file__', str(temp_project_dir / "api_server_safeguards.py")):
            safeguards = ServerSafeguards()
            assert safeguards.project_root == temp_project_dir

    def test_file_existence_checks_pass(self, safeguards, temp_project_dir):
        """Test file existence checks pass when files exist."""
        safeguards.check_file_permissions()

        file_checks = [r for r in safeguards.results if r.name.startswith("file_access_")]
        assert len(file_checks) == 5

        for check in file_checks:
            assert check.status == SafeguardStatus.PASS
            assert check.severity == SafeguardSeverity.INFO
            assert "File accessible" in check.message

    def test_file_existence_checks_fail_when_missing(self, safeguards, temp_project_dir):
        """Test file existence checks fail when files are missing."""
        # Remove one file
        (temp_project_dir / "api_server.py").unlink()

        safeguards.check_file_permissions()

        api_server_check = next(r for r in safeguards.results if "api_server.py" in r.name)
        assert api_server_check.status == SafeguardStatus.FAIL
        assert api_server_check.severity == SafeguardSeverity.CRITICAL
        assert "Critical file missing" in api_server_check.message

    @patch('api_server_safeguards.sqlite3')
    def test_database_connectivity_success(self, mock_sqlite, safeguards):
        """Test database connectivity check passes."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)
        mock_sqlite.connect.return_value = mock_conn

        with patch('api_server_safeguards.Path.exists', return_value=True):
            safeguards.check_database_connectivity()

        db_checks = [r for r in safeguards.results if "database" in r.name]
        connectivity_check = next(r for r in db_checks if "connectivity" in r.name)
        assert connectivity_check.status == SafeguardStatus.PASS

    @patch('api_server_safeguards.sqlite3')
    def test_database_schema_validation_correct_tables(self, mock_sqlite, safeguards):
        """Test database schema validation with correct table names."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Mock table existence checks - all tables exist
        def mock_fetchone():
            call_count = mock_cursor.execute.call_count
            if "trades" in str(mock_cursor.execute.call_args):
                return ("trades",)
            elif "positions" in str(mock_cursor.execute.call_args):
                return ("positions",)
            elif "account_profiles" in str(mock_cursor.execute.call_args):
                return ("account_profiles",)
            return None

        mock_cursor.fetchone.side_effect = mock_fetchone
        mock_sqlite.connect.return_value = mock_conn

        with patch('api_server_safeguards.Path.exists', return_value=True):
            safeguards.check_database_connectivity()

        schema_check = next(r for r in safeguards.results if "database_schema" in r.name)
        assert schema_check.status == SafeguardStatus.PASS
        assert "Database schema appears complete" in schema_check.message

    @patch('api_server_safeguards.sqlite3')
    def test_database_schema_validation_missing_table(self, mock_sqlite, safeguards):
        """Test database schema validation fails when table is missing."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        # Mock table existence - account_profiles missing
        def mock_fetchone():
            if "account_profiles" in str(mock_cursor.execute.call_args):
                return None  # Table doesn't exist
            return ("table_name",)  # Other tables exist

        mock_cursor.fetchone.side_effect = mock_fetchone
        mock_sqlite.connect.return_value = mock_conn

        with patch('api_server_safeguards.Path.exists', return_value=True):
            safeguards.check_database_connectivity()

        schema_check = next(r for r in safeguards.results if "database_schema" in r.name)
        assert schema_check.status == SafeguardStatus.FAIL
        assert schema_check.severity == SafeguardSeverity.CRITICAL
        assert "account_profiles" in schema_check.message

    @patch('api_server_safeguards.requests')
    def test_network_connectivity_success_first_endpoint(self, mock_requests, safeguards):
        """Test network connectivity succeeds on first endpoint."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_requests.get.return_value = mock_response

        safeguards.check_network_connectivity()

        network_check = next(r for r in safeguards.results if "network_connectivity" in r.name)
        assert network_check.status == SafeguardStatus.PASS
        assert "Network connectivity confirmed" in network_check.message

    @patch('api_server_safeguards.requests')
    def test_network_connectivity_fallback_endpoints(self, mock_requests, safeguards):
        """Test network connectivity uses fallback endpoints."""
        # First endpoint fails, second succeeds
        mock_requests.get.side_effect = [
            Exception("First endpoint failed"),
            MagicMock(status_code=200)  # Second endpoint succeeds
        ]

        safeguards.check_network_connectivity()

        network_check = next(r for r in safeguards.results if "network_connectivity" in r.name)
        assert network_check.status == SafeguardStatus.PASS

    @patch('api_server_safeguards.requests')
    def test_network_connectivity_all_endpoints_fail(self, mock_requests, safeguards):
        """Test network connectivity fails when all endpoints fail."""
        mock_requests.get.side_effect = Exception("All endpoints failed")

        safeguards.check_network_connectivity()

        network_check = next(r for r in safeguards.results if "network_connectivity" in r.name)
        assert network_check.status == SafeguardStatus.FAIL
        assert network_check.severity == SafeguardSeverity.WARNING

    def test_environment_variable_checks(self, safeguards):
        """Test environment variable validation."""
        with patch.dict(os.environ, {'TESTNET': 'true'}, clear=True):
            safeguards.check_environment_variables()

        testnet_check = next(r for r in safeguards.results if "env_testnet" in r.name)
        assert testnet_check.status == SafeguardStatus.PASS

    def test_critical_imports_success(self, safeguards):
        """Test critical import validation succeeds."""
        safeguards.check_critical_imports()

        import_checks = [r for r in safeguards.results if r.name.startswith("import_")]
        # Should have checks for config, database, etc.
        assert len(import_checks) > 0

        # Most should pass (some may fail due to missing dependencies in test env)
        passed_checks = [r for r in import_checks if r.status == SafeguardStatus.PASS]
        assert len(passed_checks) >= 2  # At least config and database should import

    def test_should_block_startup_with_critical_failures(self, safeguards):
        """Test that startup is blocked when critical failures exist."""
        # Add a critical failure
        from api_server_safeguards import SafeguardResult
        safeguards.results = [
            SafeguardResult(
                name="test_critical",
                status=SafeguardStatus.FAIL,
                severity=SafeguardSeverity.CRITICAL,
                message="Critical failure"
            )
        ]

        assert safeguards.should_block_startup() is True

    def test_should_allow_startup_without_critical_failures(self, safeguards):
        """Test that startup is allowed when only warnings exist."""
        # Add only warnings
        from api_server_safeguards import SafeguardResult
        safeguards.results = [
            SafeguardResult(
                name="test_warning",
                status=SafeguardStatus.FAIL,
                severity=SafeguardSeverity.WARNING,
                message="Warning only"
            )
        ]

        assert safeguards.should_block_startup() is False

    def test_get_summary_calculations(self, safeguards):
        """Test summary calculations are correct."""
        # Add mix of results
        from api_server_safeguards import SafeguardResult
        safeguards.results = [
            SafeguardResult("pass1", SafeguardStatus.PASS, SafeguardSeverity.INFO, "Pass"),
            SafeguardResult("pass2", SafeguardStatus.PASS, SafeguardSeverity.INFO, "Pass"),
            SafeguardResult("fail_critical", SafeguardStatus.FAIL, SafeguardSeverity.CRITICAL, "Critical"),
            SafeguardResult("fail_warning", SafeguardStatus.FAIL, SafeguardSeverity.WARNING, "Warning"),
            SafeguardResult("skip1", SafeguardStatus.SKIP, SafeguardSeverity.INFO, "Skip"),
        ]

        summary = safeguards.get_summary()

        assert summary["total_checks"] == 5
        assert summary["passed"] == 2
        assert summary["failed"] == 2
        assert summary["skipped"] == 1
        assert summary["critical_failures"] == 1
        assert summary["warning_failures"] == 1
        assert summary["can_start_server"] is False

    @patch.dict(os.environ, {'TESTNET': 'true'}, clear=True)
    def test_full_safeguard_run_success(self, temp_project_dir):
        """Test complete safeguard run with all prerequisites met."""
        with patch('api_server_safeguards.sqlite3') as mock_sqlite, \
             patch('api_server_safeguards.requests') as mock_requests, \
             patch('api_server_safeguards.Path') as mock_path:

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

            # Mock path resolution
            mock_path.__file__ = str(temp_project_dir / "api_server_safeguards.py")
            mock_path.return_value.parent = temp_project_dir

            can_start, summary = run_startup_safeguards()

            assert can_start is True
            assert summary["critical_failures"] == 0
            assert summary["can_start_server"] is True

    def test_run_startup_safeguards_function(self):
        """Test the run_startup_safeguards function interface."""
        with patch('api_server_safeguards.safeguards.run_all_checks') as mock_run, \
             patch('api_server_safeguards.safeguards.get_summary') as mock_summary, \
             patch('api_server_safeguards.safeguards.should_block_startup') as mock_should_block:

            mock_summary.return_value = {"can_start_server": True}
            mock_should_block.return_value = False

            can_start, summary = run_startup_safeguards()

            assert can_start is True
            mock_run.assert_called_once()
            mock_summary.assert_called_once()


# Run tests with: pytest tests/test_server_safeguards_comprehensive.py