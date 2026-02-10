"""
Critical Path Tests
Tests for the most essential functionality that must work for server stability.

These tests are designed to run quickly and catch the most common breakage
patterns that cause the "fix one thing, break another" cycle.
"""

import pytest
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch
import asyncio

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set test environment
os.environ.setdefault('TESTNET', 'true')
os.environ.setdefault('MOCK_TRADING', 'true')
os.environ.setdefault('DISABLE_EXTERNAL_APIS', 'true')


class TestCriticalImports:
    """Test that critical modules can be imported without errors."""

    def test_core_module_imports(self):
        """Test that all core modules can be imported."""
        core_modules = [
            'config',
            'database',
            'api_server',
            'main',
        ]

        for module_name in core_modules:
            try:
                __import__(module_name)
            except ImportError as e:
                pytest.fail(f"Failed to import critical module '{module_name}': {e}")
            except Exception as e:
                pytest.fail(f"Error importing critical module '{module_name}': {e}")

    def test_api_server_safeguards_import(self):
        """Test that safeguards module can be imported."""
        try:
            import api_server_safeguards
            assert hasattr(api_server_safeguards, 'run_startup_safeguards')
        except ImportError as e:
            pytest.fail(f"Failed to import api_server_safeguards: {e}")


class TestDatabaseConnectivity:
    """Test database connectivity and basic operations."""

    def test_database_path_exists(self):
        """Test that database path is properly configured."""
        try:
            from database import DATABASE_PATH
            assert DATABASE_PATH, "DATABASE_PATH should not be empty"
            # Note: We don't check if file exists as it might be created on first run
        except ImportError:
            pytest.skip("Database module not available")

    def test_database_connection(self):
        """Test basic database connection."""
        try:
            from database import get_db_connection

            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
                assert result[0] == 1, "Basic database query failed"
        except ImportError:
            pytest.skip("Database module not available")
        except Exception as e:
            pytest.fail(f"Database connection test failed: {e}")


class TestConfigurationLoading:
    """Test that configuration loads without errors."""

    def test_config_import(self):
        """Test that config module can be imported."""
        try:
            import config
        except ImportError as e:
            pytest.fail(f"Failed to import config module: {e}")

    def test_pacifica_config_access(self):
        """Test that Pacifica config can be accessed."""
        try:
            from config import get_pacifica_config
            # Just test that function exists and doesn't crash
            config = get_pacifica_config()
            # config can be None, that's OK
        except ImportError:
            pytest.skip("Config module not available")
        except Exception as e:
            pytest.fail(f"Pacifica config access failed: {e}")


class TestAPIServerBasics:
    """Test basic API server functionality."""

    def test_api_server_import(self):
        """Test that API server module can be imported."""
        try:
            import api_server
            assert hasattr(api_server, 'app'), "API server should have FastAPI app"
        except ImportError as e:
            pytest.fail(f"Failed to import api_server: {e}")

    def test_api_response_function(self):
        """Test the api_response helper function."""
        try:
            from api_server import api_response

            # Test success response
            response = api_response(success=True, data={"test": "data"})
            assert response["success"] is True
            assert "data" in response
            assert "timestamp" in response

            # Test error response
            response = api_response(success=False, error="test error")
            assert response["success"] is False
            assert "error" in response
            assert response["error"] == "test error"

        except ImportError:
            pytest.skip("api_server module not available")

    def test_safe_float_function(self):
        """Test the safe_float helper function."""
        try:
            from api_server import safe_float

            assert safe_float("123.45") == 123.45
            assert safe_float(None) == 0.0
            assert safe_float("invalid") == 0.0
            assert safe_float(42) == 42.0

        except ImportError:
            pytest.skip("api_server module not available")


class TestServerSafeguards:
    """Test the server safeguards functionality."""

    def test_safeguards_run(self):
        """Test that safeguards can run without crashing."""
        try:
            from api_server_safeguards import run_startup_safeguards

            can_start, summary = run_startup_safeguards()

            # Verify return types
            assert isinstance(can_start, bool)
            assert isinstance(summary, dict)

            # Verify summary structure
            expected_keys = ['total_checks', 'passed', 'failed', 'skipped', 'can_start_server', 'results']
            for key in expected_keys:
                assert key in summary, f"Summary missing key: {key}"

            assert isinstance(summary['results'], list)

        except ImportError:
            pytest.skip("Safeguards module not available")
        except Exception as e:
            pytest.fail(f"Safeguards test failed: {e}")

    def test_safeguard_result_structure(self):
        """Test that safeguard results have correct structure."""
        try:
            from api_server_safeguards import run_startup_safeguards

            can_start, summary = run_startup_safeguards()

            for result in summary['results']:
                assert 'name' in result
                assert 'status' in result
                assert 'severity' in result
                assert 'message' in result
                # fix_suggestion is optional

                assert result['status'] in ['pass', 'fail', 'skip']
                assert result['severity'] in ['critical', 'warning', 'info']

        except ImportError:
            pytest.skip("Safeguards module not available")


class TestEnvironmentSetup:
    """Test environment setup and required variables."""

    def test_testnet_environment(self):
        """Test that TESTNET environment variable is set correctly."""
        testnet_value = os.getenv('TESTNET', '').lower()
        assert testnet_value in ['true', 'false', ''], "TESTNET must be 'true' or 'false'"

    def test_required_env_vars_exist(self):
        """Test that required environment variables are defined (may be empty)."""
        required_vars = ['TESTNET']

        for var in required_vars:
            value = os.getenv(var)
            assert value is not None, f"Required environment variable {var} is not set"


class TestFileSystemAccess:
    """Test that critical files are accessible."""

    def test_critical_files_exist(self):
        """Test that critical files exist and are readable."""
        critical_files = [
            'api_server.py',
            'database.py',
            'config.py',
            'main.py',
            'trading_bot_interface.html',
            'pyproject.toml'
        ]

        project_root = Path(__file__).parent.parent

        for filename in critical_files:
            filepath = project_root / filename
            assert filepath.exists(), f"Critical file missing: {filename}"
            assert filepath.is_file(), f"Path is not a file: {filename}"
            assert os.access(filepath, os.R_OK), f"File not readable: {filename}"

    def test_scripts_directory_exists(self):
        """Test that scripts directory exists."""
        project_root = Path(__file__).parent.parent
        scripts_dir = project_root / 'scripts'
        assert scripts_dir.exists(), "scripts directory should exist"
        assert scripts_dir.is_dir(), "scripts should be a directory"


class TestBasicFastAPIEndpoints:
    """Test basic FastAPI endpoint functionality."""

    @pytest.mark.asyncio
    async def test_test_endpoint(self):
        """Test the basic /test endpoint."""
        try:
            from api_server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.get("/test")

            assert response.status_code == 200
            data = response.json()
            assert data == {"ok": True}

        except ImportError:
            pytest.skip("FastAPI or test client not available")

    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        """Test the /api/health endpoint."""
        try:
            from api_server import app
            from fastapi.testclient import TestClient

            client = TestClient(app)
            response = client.get("/api/health")

            assert response.status_code == 200
            data = response.json()

            # Check basic structure
            assert "status" in data
            assert "service" in data
            assert "timestamp" in data
            assert "checks" in data

        except ImportError:
            pytest.skip("FastAPI or test client not available")


if __name__ == "__main__":
    # Allow running this test file directly
    pytest.main([__file__, "-v"])