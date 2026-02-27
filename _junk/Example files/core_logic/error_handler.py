"""
Error Handler Module

Provides standardized error handling utilities for the trading bot.
Maps error types to HTTP status codes and creates consistent error responses.
"""

from datetime import datetime
from typing import Dict, Optional


def get_status_code(error_type: str) -> int:
    """
    Map error types to HTTP status codes.

    Args:
        error_type: String identifier for the error type

    Returns:
        HTTP status code (defaults to 500 for unknown errors)
    """
    status_map = {
        # Client errors (4xx)
        "RISK_VIOLATION": 400,
        "INVALID_PARAMETERS": 400,
        "INVALID_ORDER": 400,
        "INVALID_SYMBOL": 400,
        "INSUFFICIENT_BALANCE": 402,
        "UNAUTHORIZED": 401,
        "FORBIDDEN": 403,
        "NOT_FOUND": 404,
        "ORDER_NOT_FOUND": 404,
        "POSITION_NOT_FOUND": 404,
        "RATE_LIMIT_EXCEEDED": 429,

        # Server errors (5xx)
        "EXCHANGE_CONNECTION_FAILED": 503,
        "DATABASE_ERROR": 500,
        "INTERNAL_ERROR": 500,
        "SERVICE_UNAVAILABLE": 503,
        "TIMEOUT": 504,

        # Trading errors
        "INSUFFICIENT_MARGIN": 400,
        "LIQUIDATION_RISK": 400,
        "POSITION_SIZE_EXCEEDED": 400,
        "LEVERAGE_TOO_HIGH": 400,
    }
    return status_map.get(error_type, 500)


def create_error_response(
    error_code: str,
    details: Optional[Dict] = None,
    message: Optional[str] = None
) -> Dict:
    """
    Create standardized error response.

    Args:
        error_code: Error code identifier
        details: Optional dict with additional error details
        message: Optional custom error message

    Returns:
        Dictionary with standardized error structure
    """
    response = {
        "error": error_code,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "details": details or {}
    }

    if message:
        response["message"] = message

    return response


def format_exception_response(
    exception: Exception,
    error_code: Optional[str] = None
) -> Dict:
    """
    Format an exception into a standardized error response.

    Args:
        exception: The exception to format
        error_code: Optional error code (defaults to exception class name)

    Returns:
        Dictionary with standardized error structure
    """
    if error_code is None:
        error_code = exception.__class__.__name__

    return create_error_response(
        error_code=error_code,
        message=str(exception),
        details={"exception_type": exception.__class__.__name__}
    )
