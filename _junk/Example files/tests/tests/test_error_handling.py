"""
Tests for error handling system.
"""

import pytest
from datetime import datetime
from exceptions import BotError, RiskViolationError, ConnectionError
from error_handler import get_status_code, create_error_response
from error_monitoring import ErrorMonitor


class TestErrorHandling:
    """Test error handling functionality."""

    def test_bot_error_creation(self):
        """Test BotError creation with recovery suggestions."""
        error = RiskViolationError(
            "Test risk violation",
            "RISK_VIOLATION",
            {"position_size": 1000},
            ["Reduce position size", "Check risk limits"]
        )

        assert error.error_code == "RISK_VIOLATION"
        assert error.message == "Test risk violation"
        assert "recovery_suggestions" in error.to_dict()
        assert len(error.recovery_suggestions) == 2

    def test_error_status_codes(self):
        """Test that error codes map to correct HTTP status codes."""
        assert get_status_code("EXCHANGE_CONNECTION_FAILED") == 503
        assert get_status_code("RISK_VIOLATION") == 400
        assert get_status_code("INSUFFICIENT_BALANCE") == 402
        assert get_status_code("UNKNOWN_ERROR") == 500

    def test_error_response_creation(self):
        """Test creation of standardized error responses."""
        response = create_error_response("TEST_ERROR", {"details": "test"})

        assert response["error"] == "TEST_ERROR"
        assert "timestamp" in response
        assert response["details"] == {"details": "test"}

    def test_error_monitor_recording(self):
        """Test error monitoring functionality."""
        monitor = ErrorMonitor()

        # Create and record an error
        error = ConnectionError("Test connection failed")
        monitor.record_error(error)

        # Check that error was recorded (ConnectionError uses its class name as error_code)
        assert monitor.error_counts["ConnectionError"] == 1
        assert len(monitor.errors["ConnectionError"]) == 1

        # Check error stats
        stats = monitor.get_error_stats(hours=1)
        assert "ConnectionError" in stats["error_types"]
        assert stats["error_types"]["ConnectionError"]["count"] == 1

    def test_error_alert_thresholds(self):
        """Test alert threshold functionality."""
        monitor = ErrorMonitor()

        # Set low threshold for testing
        monitor.alert_thresholds["TEST_ERROR"] = 2

        # This should not trigger alert (count = 1)
        error1 = BotError("Test error 1", "TEST_ERROR")
        monitor.record_error(error1)
        assert monitor.error_counts["TEST_ERROR"] == 1

        # This should trigger alert (count = 2)
        error2 = BotError("Test error 2", "TEST_ERROR")
        monitor.record_error(error2)
        assert monitor.error_counts["TEST_ERROR"] == 2

    def test_error_context_addition(self):
        """Test adding context to errors."""
        error = BotError("Test error")
        error.add_context("user_id", "12345")
        error.add_context("request_id", "req-abc")

        assert error.context["user_id"] == "12345"
        assert error.context["request_id"] == "req-abc"
        assert "context" in error.to_dict()

    def test_recovery_suggestions_setting(self):
        """Test setting recovery suggestions."""
        error = BotError("Test error")
        suggestions = ["Try again", "Check logs", "Contact support"]
        error.set_recovery_suggestions(suggestions)

        assert error.recovery_suggestions == suggestions