"""
Error Monitoring Module

Provides error tracking and monitoring capabilities for the trading bot.
Tracks error counts, error rates, and maintains error history.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from loguru import logger


class ErrorMonitor:
    """
    Monitor and track errors across the trading bot.

    Features:
    - Error counting by type
    - Error history tracking
    - Alert thresholds
    - Time-based statistics
    """

    def __init__(self):
        """Initialize the error monitor."""
        self.error_counts = defaultdict(int)
        self.errors = defaultdict(list)
        self.alert_thresholds = {}
        self._start_time = datetime.now()

    def record_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Record an error occurrence.

        Args:
            error: The exception that occurred
            context: Optional context information (e.g., function name, parameters)
        """
        error_code = getattr(error, 'error_code', str(type(error).__name__))
        self.error_counts[error_code] += 1

        error_entry = {
            'timestamp': datetime.now(),
            'error': str(error),
            'error_type': type(error).__name__,
            'context': context or {}
        }

        self.errors[error_code].append(error_entry)

        # Log the error
        logger.error(
            f"Error recorded: {error_code}",
            error=str(error),
            context=context
        )

        # Check if alert threshold exceeded
        self._check_alert_threshold(error_code)

    def get_error_stats(self, hours: int = 1) -> Dict[str, Any]:
        """
        Get error statistics for a time window.

        Args:
            hours: Number of hours to look back

        Returns:
            Dictionary with error statistics
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        recent_errors = {}
        total_recent = 0

        for error_type, errors in self.errors.items():
            recent = [e for e in errors if e['timestamp'] > cutoff]
            if recent:
                recent_errors[error_type] = {
                    'count': len(recent),
                    'errors': recent,
                    'latest': recent[-1] if recent else None
                }
                total_recent += len(recent)

        return {
            'time_window_hours': hours,
            'error_types': recent_errors,
            'total_recent': total_recent,
            'unique_error_types': len(recent_errors)
        }

    def get_all_time_stats(self) -> Dict[str, Any]:
        """
        Get all-time error statistics.

        Returns:
            Dictionary with cumulative error statistics
        """
        total_errors = sum(self.error_counts.values())
        uptime = datetime.now() - self._start_time

        return {
            'total_errors': total_errors,
            'unique_error_types': len(self.error_counts),
            'error_counts_by_type': dict(self.error_counts),
            'uptime_hours': uptime.total_seconds() / 3600,
            'error_rate_per_hour': total_errors / (uptime.total_seconds() / 3600) if uptime.total_seconds() > 0 else 0
        }

    def set_alert_threshold(self, error_code: str, threshold: int, hours: int = 1) -> None:
        """
        Set an alert threshold for a specific error type.

        Args:
            error_code: The error code to monitor
            threshold: Number of errors to trigger alert
            hours: Time window for threshold
        """
        self.alert_thresholds[error_code] = {
            'threshold': threshold,
            'hours': hours
        }
        logger.info(
            f"Alert threshold set: {error_code}",
            threshold=threshold,
            hours=hours
        )

    def _check_alert_threshold(self, error_code: str) -> None:
        """
        Check if error count exceeds alert threshold.

        Args:
            error_code: The error code to check
        """
        if error_code not in self.alert_thresholds:
            return

        config = self.alert_thresholds[error_code]
        stats = self.get_error_stats(hours=config['hours'])

        if error_code in stats['error_types']:
            count = stats['error_types'][error_code]['count']
            if count >= config['threshold']:
                logger.warning(
                    f"ALERT: Error threshold exceeded for {error_code}",
                    count=count,
                    threshold=config['threshold'],
                    hours=config['hours']
                )

    def clear_old_errors(self, hours: int = 24) -> int:
        """
        Clear errors older than specified hours to prevent memory growth.

        Args:
            hours: Clear errors older than this many hours

        Returns:
            Number of errors cleared
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        cleared_count = 0

        for error_type in list(self.errors.keys()):
            original_count = len(self.errors[error_type])
            self.errors[error_type] = [
                e for e in self.errors[error_type]
                if e['timestamp'] > cutoff
            ]
            cleared_count += original_count - len(self.errors[error_type])

            # Remove error type if no errors remain
            if not self.errors[error_type]:
                del self.errors[error_type]

        if cleared_count > 0:
            logger.info(f"Cleared {cleared_count} old errors")

        return cleared_count

    def get_top_errors(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get the most frequent error types.

        Args:
            limit: Maximum number of error types to return

        Returns:
            List of error types with counts, sorted by frequency
        """
        sorted_errors = sorted(
            self.error_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )

        return [
            {
                'error_code': code,
                'count': count,
                'latest': self.errors[code][-1] if self.errors.get(code) else None
            }
            for code, count in sorted_errors[:limit]
        ]


# Global error monitor instance
_error_monitor = None


def get_error_monitor() -> ErrorMonitor:
    """
    Get the global error monitor instance.

    Returns:
        The global ErrorMonitor instance
    """
    global _error_monitor
    if _error_monitor is None:
        _error_monitor = ErrorMonitor()
    return _error_monitor
