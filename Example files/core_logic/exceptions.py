"""
Custom exceptions for the trading bot.
All exceptions inherit from BotError for consistent error handling.
"""

from typing import Optional, Dict, Any, List
from datetime import datetime, timezone


class BotError(Exception):
    """
    Base exception for all bot-related errors.

    Provides structured error information for logging and handling.
    """

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        recovery_suggestions: Optional[List[str]] = None,
    ):
        """
        Initialize bot error.

        Args:
            message: Human-readable error message
            error_code: Machine-readable error code
            details: Additional error details
            recovery_suggestions: List of suggested recovery actions
        """
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self.__class__.__name__
        self.details = details or {}
        self.recovery_suggestions = recovery_suggestions or []
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.context = {}  # Additional context for debugging

    def to_dict(self) -> Dict[str, Any]:
        """Convert error to dictionary for logging."""
        return {
            "error_type": self.__class__.__name__,
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
            "recovery_suggestions": self.recovery_suggestions,
            "timestamp": self.timestamp,
            "context": self.context,
        }

    def add_context(self, key: str, value: Any):
        """Add context information for debugging."""
        self.context[key] = value

    def set_recovery_suggestions(self, suggestions: List[str]):
        """Set recovery suggestions for this error."""
        self.recovery_suggestions = suggestions


class ConfigurationError(BotError):
    """Raised when configuration is invalid or missing."""

    pass


class ValidationError(BotError):
    """Raised when data validation fails."""

    pass


class RiskViolationError(BotError):
    """Raised when risk management rules are violated."""

    pass


class InsufficientFundsError(BotError):
    """Raised when account has insufficient funds."""

    pass


class ExchangeError(BotError):
    """Base class for exchange-related errors."""

    pass


class ConnectionError(ExchangeError):
    """Raised when exchange connection fails."""

    pass


class OrderPlacementError(ExchangeError):
    """Raised when order placement fails."""

    pass


class OrderCancellationError(ExchangeError):
    """Raised when order cancellation fails."""

    pass


class MarketDataError(ExchangeError):
    """Raised when market data fetch fails."""

    pass


class StrategyError(BotError):
    """Raised when strategy execution fails."""

    pass


class SignalGenerationError(StrategyError):
    """Raised when signal generation fails."""

    pass


class JournalError(BotError):
    """Raised when journal operations fail."""

    pass


class PerformanceError(BotError):
    """Raised when performance calculations fail."""

    pass


class CircuitBreakerError(BotError):
    """Raised when circuit breaker is triggered."""

    pass


class EmergencyShutdownError(BotError):
    """Raised during emergency shutdown."""

    pass


# Error codes for consistent error handling
ERROR_CODES = {
    # Configuration errors
    "CONFIG_INVALID": "Configuration file is invalid or corrupted",
    "CONFIG_MISSING": "Required configuration parameter is missing",
    "CONFIG_TYPE_ERROR": "Configuration parameter has wrong type",
    # Validation errors
    "VALIDATION_FAILED": "Data validation failed",
    "SCHEMA_ERROR": "Data does not match expected schema",
    # Risk errors
    "RISK_VIOLATION": "Risk management rule violated",
    "STOP_LOSS_INVALID": "Stop loss is invalid or too wide",
    "LEVERAGE_TOO_HIGH": "Leverage exceeds maximum allowed",
    "ACCOUNT_RISK_TOO_HIGH": "Account risk exceeds limits",
    # Exchange errors
    "EXCHANGE_CONNECTION_FAILED": "Failed to connect to exchange",
    "EXCHANGE_AUTH_FAILED": "Exchange authentication failed",
    "ORDER_PLACEMENT_FAILED": "Order placement failed",
    "ORDER_CANCEL_FAILED": "Order cancellation failed",
    "INSUFFICIENT_BALANCE": "Insufficient account balance",
    # Strategy errors
    "STRATEGY_EXECUTION_FAILED": "Strategy execution failed",
    "SIGNAL_GENERATION_FAILED": "Signal generation failed",
    "INVALID_MARKET_DATA": "Market data is invalid or incomplete",
    # Journal errors
    "JOURNAL_SAVE_FAILED": "Failed to save journal data",
    "JOURNAL_LOAD_FAILED": "Failed to load journal data",
    "PERFORMANCE_CALC_FAILED": "Performance calculation failed",
    # System errors
    "EMERGENCY_SHUTDOWN": "Emergency shutdown triggered",
    "CIRCUIT_BREAKER": "Circuit breaker activated",
    "SYSTEM_OVERLOAD": "System resources overloaded",
}


def create_error(error_code: str, details: Optional[Dict[str, Any]] = None) -> BotError:
    """
    Create a BotError from an error code.

    Args:
        error_code: Error code from ERROR_CODES
        details: Additional error details

    Returns:
        Appropriate BotError subclass
    """
    message = ERROR_CODES.get(error_code, f"Unknown error: {error_code}")

    # Map error codes to exception classes
    error_class_map = {
        "CONFIG_INVALID": ConfigurationError,
        "CONFIG_MISSING": ConfigurationError,
        "CONFIG_TYPE_ERROR": ConfigurationError,
        "VALIDATION_FAILED": ValidationError,
        "SCHEMA_ERROR": ValidationError,
        "RISK_VIOLATION": RiskViolationError,
        "STOP_LOSS_INVALID": RiskViolationError,
        "LEVERAGE_TOO_HIGH": RiskViolationError,
        "ACCOUNT_RISK_TOO_HIGH": RiskViolationError,
        "INSUFFICIENT_BALANCE": InsufficientFundsError,
        "EXCHANGE_CONNECTION_FAILED": ConnectionError,
        "EXCHANGE_AUTH_FAILED": ExchangeError,
        "ORDER_PLACEMENT_FAILED": OrderPlacementError,
        "ORDER_CANCEL_FAILED": OrderCancellationError,
        "STRATEGY_EXECUTION_FAILED": StrategyError,
        "SIGNAL_GENERATION_FAILED": SignalGenerationError,
        "JOURNAL_SAVE_FAILED": JournalError,
        "JOURNAL_LOAD_FAILED": JournalError,
        "PERFORMANCE_CALC_FAILED": PerformanceError,
        "EMERGENCY_SHUTDOWN": EmergencyShutdownError,
        "CIRCUIT_BREAKER": CircuitBreakerError,
    }

    error_class = error_class_map.get(error_code, BotError)
    return error_class(message, error_code, details)
