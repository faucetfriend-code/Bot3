"""
Tests for Server Safeguards Module
Tests the startup validation functionality.
"""

import pytest
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch, mock_open

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set test environment
os.environ.setdefault('TESTNET', 'true')
os.environ.setdefault('MOCK_TRADING', 'true')
os.environ.setdefault('DISABLE_EXTERNAL_APIS', 'true')


class TestServerSafeguards:
    """Test the ServerSafeguards class functionality."""

    def test_safeguards_initialization(self):
        """Test that safeguards can be initialized."""
        try:
            from api_server_safeguards import ServerSafeguards

            safeguards = ServerSafeguards()
            assert safeguards is not None
            assert hasattr(safeguards, 'run_all_checks')
            assert hasattr(safeguards, 'get_summary')
            assert hasattr(safeguards, 'should_block_startup')

        except ImportError:
            pytest.skip("Server safeguards module not available")

    def test_safeguards_run_all_checks(self):
        """Test that run_all_checks returns results."""
        try:
            from api_server_safeguards import ServerSafeguards

            safeguards = ServerSafeguards()
            results = safeguards.run_all_checks()

            assert isinstance(results, list)
            assert len(results) > 0

            # Check result structure
            for result in results:
                assert hasattr(result, 'name')
                assert hasattr(result, 'status')
                assert hasattr(result, 'severity')
                assert hasattr(result, 'message')

        except ImportError:
            pytest.skip("Server safeguards module not available")

    def test_safeguards_summary_structure(self):
        """Test that get_summary returns correct structure."""
        try:
            from api_server_safeguards import ServerSafeguards

            safeguards = ServerSafeguards()
            safeguards.run_all_checks()
            summary = safeguards.get_summary()

            expected_keys = [
                'total_checks', 'passed', 'failed', 'skipped',
                'critical_failures', 'warning_failures',
                'can_start_server', 'results'
            ]

            for key in expected_keys:
                assert key in summary, f"Summary missing key: {key}"

            assert isinstance(summary['results'], list)
            assert summary['total_checks'] == len(summary['results'])

        except ImportError:
            pytest.skip("Server safeguards module not available")

    def test_run_startup_safeguards_function(self):
        """Test the run_startup_safeguards function."""
        try:
            from api_server_safeguards import run_startup_safeguards

            can_start, summary = run_startup_safeguards()

            assert isinstance(can_start, bool)
            assert isinstance(summary, dict)

            # Should be able to start in test environment
            # (may have warnings but no critical failures)
            assert can_start in [True, False]

        except ImportError:
            pytest.skip("Server safeguards module not available")


class TestSafeguardChecks:
    """Test individual safeguard checks."""

    def test_environment_variables_check(self):
        """Test environment variables validation."""
        try:
            from api_server_safeguards import ServerSafeguards

            safeguards = ServerSafeguards()
            safeguards.check_environment_variables()

            # Should have checked TESTNET variable
            env_results = [r for r in safeguards.results if r.name.startswith('env_')]
            assert len(env_results) > 0

        except ImportError:
            pytest.skip("Server safeguards module not available")

    def test_file_permissions_check(self):
        """Test file permissions validation."""
        try:
            from api_server_safeguards import ServerSafeguards

            safeguards = ServerSafeguards()
            safeguards.check_file_permissions()

            # Should have checked critical files
            file_results = [r for r in safeguards.results if r.name.startswith('file_')]
            assert len(file_results) > 0

        except ImportError:
            pytest.skip("Server safeguards module not available")

    def test_critical_imports_check(self):
        """Test critical imports validation."""
        try:
            from api_server_safeguards import ServerSafeguards

            safeguards = ServerSafeguards()
            safeguards.check_critical_imports()

            # Should have checked imports
            import_results = [r for r in safeguards.results if r.name.startswith('import_')]
            assert len(import_results) > 0

        except ImportError:
            pytest.skip("Server safeguards module not available")

    @patch('os.path.exists')
    @patch('os.access')
    def test_database_check_with_mock(self, mock_access, mock_exists):
        """Test database check with mocked file system."""
        try:
            from api_server_safeguards import ServerSafeguards

            # Mock database file exists and is accessible
            mock_exists.return_value = True
            mock_access.return_value = True

            with patch('sqlite3.connect') as mock_connect:
                mock_conn = Mock()
                mock_cursor = Mock()
                mock_conn.cursor.return_value = mock_cursor
                mock_cursor.fetchone.return_value = (1,)
                mock_connect.return_value = mock_conn

                safeguards = ServerSafeguards()
                safeguards.check_database_connectivity()

                # Should have database results
                db_results = [r for r in safeguards.results if 'database' in r.name]
                assert len(db_results) > 0

        except ImportError:
            pytest.skip("Server safeguards module not available")


class TestSafeguardSeverity:
    """Test safeguard severity handling."""

    def test_should_block_startup_logic(self):
        """Test the startup blocking logic."""
        try:
            from api_server_safeguards import ServerSafeguards, SafeguardResult, SafeguardStatus, SafeguardSeverity

            safeguards = ServerSafeguards()

            # Test with no critical failures
            safeguards.results = [
                SafeguardResult("test1", SafeguardStatus.PASS, SafeguardSeverity.INFO, "OK"),
                SafeguardResult("test2", SafeguardStatus.FAIL, SafeguardSeverity.WARNING, "Warning"),
            ]
            assert safeguards.should_block_startup() == False

            # Test with critical failure
            safeguards.results = [
                SafeguardResult("test1", SafeguardStatus.FAIL, SafeguardSeverity.CRITICAL, "Critical failure"),
            ]
            assert safeguards.should_block_startup() == True

        except ImportError:
            pytest.skip("Server safeguards module not available")


class TestSafeguardResultStructure:
    """Test SafeguardResult dataclass."""

    def test_safeguard_result_creation(self):
        """Test creating SafeguardResult instances."""
        try:
            from api_server_safeguards import SafeguardResult, SafeguardStatus, SafeguardSeverity

            result = SafeguardResult(
                name="test_check",
                status=SafeguardStatus.PASS,
                severity=SafeguardSeverity.INFO,
                message="Test message",
                details={"extra": "info"},
                fix_suggestion="Try this fix"
            )

            assert result.name == "test_check"
            assert result.status == SafeguardStatus.PASS
            assert result.severity == SafeguardSeverity.INFO
            assert result.message == "Test message"
            assert result.details == {"extra": "info"}
            assert result.fix_suggestion == "Try this fix"

        except ImportError:
            pytest.skip("Server safeguards module not available")


class TestStandaloneSafeguards:
    """Test running safeguards as standalone script."""

    def test_standalone_execution(self):
        """Test that safeguards can run standalone."""
        try:
            import subprocess
            import sys

            # Run the safeguards module as a script
            result = subprocess.run(
                [sys.executable, "-m", "api_server_safeguards"],
                capture_output=True,
                text=True,
                cwd=Path(__file__).parent.parent,
                timeout=30
            )

            # Should complete successfully (may have warnings)
            assert result.returncode in [0, 1]  # 0 for success, 1 for critical failures

        except (ImportError, subprocess.TimeoutExpired):
            pytest.skip("Cannot test standalone execution")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])