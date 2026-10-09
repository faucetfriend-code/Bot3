"""
Monitoring and Observability System - Phase 3 Integration

Provides comprehensive monitoring, health checks, and observability
for the component-based trading system.
"""

import logging
import time
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from .component_registry import get_component_registry
from .event_system import get_event_bus, EventType

logger = logging.getLogger(__name__)


class MonitoringSystem:
    """
    Comprehensive monitoring system for component health and performance.

    Provides:
    - Component health checks
    - Performance metrics collection
    - Event monitoring and alerting
    - System status dashboard
    """

    def __init__(self) -> None:
        self.component_registry = get_component_registry()
        self.event_bus = get_event_bus()

        # Monitoring data
        self._health_status: Dict[str, Dict[str, Any]] = {}
        self._performance_metrics: Dict[str, List[Dict[str, Any]]] = {}
        self._alerts: List[Dict[str, Any]] = []
        self._system_start_time = datetime.now()

        # Monitoring configuration
        self._health_check_interval = 30  # seconds
        self._metrics_retention_hours = 24
        self._max_alerts = 100

        # Start background monitoring
        self._monitoring_thread = threading.Thread(
            target=self._monitoring_loop, daemon=True
        )
        self._monitoring_thread.start()

        logger.info("MonitoringSystem initialized")

    def _monitoring_loop(self) -> None:
        """Background monitoring loop."""
        while True:
            try:
                self._perform_health_checks()
                self._cleanup_old_metrics()
                self._check_system_alerts()
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")

            time.sleep(self._health_check_interval)

    def _perform_health_checks(self) -> None:
        """Perform health checks on all registered components."""
        component_health = self.component_registry.validate_dependencies()

        for component_name, status in component_health.items():
            # Be more tolerant during startup - assume healthy if no health check method
            if status is True:
                health_status = "healthy"
            elif status is False:
                health_status = "unhealthy"
            else:
                # status contains error details or None (no is_healthy method)
                health_status = "unknown"  # Don't mark as unhealthy if no health check

            self._health_status[component_name] = {
                "status": health_status,
                "last_check": datetime.now(),
                "details": status,
            }

        # Only log actually unhealthy components (not unknown/missing health checks)
        unhealthy = [
            name
            for name, status in self._health_status.items()
            if status["status"] == "unhealthy"
        ]
        if unhealthy:
            logger.warning(f"Unhealthy components detected: {', '.join(unhealthy)}")

        # Log components without health checks (for development)
        unknown = [
            name
            for name, status in self._health_status.items()
            if status["status"] == "unknown"
        ]
        if unknown:
            logger.debug(f"Components without health checks: {', '.join(unknown)}")

    def _cleanup_old_metrics(self) -> None:
        """Clean up old performance metrics."""
        cutoff_time = datetime.now() - timedelta(hours=self._metrics_retention_hours)

        for component_name in self._performance_metrics:
            self._performance_metrics[component_name] = [
                metric
                for metric in self._performance_metrics[component_name]
                if metric["timestamp"] > cutoff_time
            ]

    def _check_system_alerts(self) -> None:
        """Check for system-level alerts."""
        # Only alert on actually unhealthy components (not unknown/missing health checks)
        unhealthy_count = sum(
            1
            for status in self._health_status.values()
            if status["status"] == "unhealthy"
        )

        if unhealthy_count > 0:
            self._add_alert(
                "warning",
                f"{unhealthy_count} components unhealthy",
                {"unhealthy_components": unhealthy_count},
            )

        # Check event bus health - only alert if it's been running for a while with no events
        event_stats = self.event_bus.get_stats()
        total_events = event_stats.get("total_events", 0)

        # Only alert if system has been running for more than 5 minutes with no events
        system_age_minutes = (
            datetime.now() - self._system_start_time
        ).total_seconds() / 60
        if system_age_minutes > 5 and total_events == 0:
            self._add_alert(
                "info",
                "No events processed recently",
                {
                    "total_events": total_events,
                    "system_age_minutes": system_age_minutes,
                },
            )

    def _add_alert(self, level: str, message: str, details: Dict[str, Any]) -> None:
        """Add an alert to the system."""
        alert = {
            "timestamp": datetime.now(),
            "level": level,
            "message": message,
            "details": details,
        }

        self._alerts.append(alert)

        # Keep only recent alerts
        if len(self._alerts) > self._max_alerts:
            self._alerts.pop(0)

        # Log alerts
        log_method = getattr(logger, level, logger.info)
        log_method(f"ALERT [{level.upper()}]: {message}")

    def record_metric(
        self,
        component_name: str,
        metric_name: str,
        value: Any,
        tags: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a performance metric."""
        if component_name not in self._performance_metrics:
            self._performance_metrics[component_name] = []

        metric = {
            "timestamp": datetime.now(),
            "metric_name": metric_name,
            "value": value,
            "tags": tags or {},
        }

        self._performance_metrics[component_name].append(metric)

        # Keep only recent metrics (last 1000 per component)
        if len(self._performance_metrics[component_name]) > 1000:
            self._performance_metrics[component_name].pop(0)

    def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status."""
        event_stats = self.event_bus.get_stats()
        component_info = self.component_registry.list_components()

        return {
            "system_uptime": str(datetime.now() - self._system_start_time),
            "components": {
                "total": component_info.get("total_components", 0),
                "healthy": sum(
                    1
                    for status in self._health_status.values()
                    if status["status"] == "healthy"
                ),
                "unhealthy": sum(
                    1
                    for status in self._health_status.values()
                    if status["status"] == "unhealthy"
                ),
            },
            "events": {
                "total_processed": event_stats.get("total_events", 0),
                "active_subscriptions": event_stats.get("total_subscribers", 0),
            },
            "alerts": {
                "active": len(self._alerts),
                "recent": [alert for alert in self._alerts[-5:]],  # Last 5 alerts
            },
            "performance": {
                "metrics_collected": sum(
                    len(metrics) for metrics in self._performance_metrics.values()
                )
            },
        }

    def get_component_health(self) -> Dict[str, Any]:
        """Get detailed component health status."""
        return {"components": self._health_status, "last_updated": datetime.now()}

    def get_performance_metrics(
        self,
        component_name: Optional[str] = None,
        metric_name: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """Get performance metrics with optional filtering."""
        if component_name:
            metrics = self._performance_metrics.get(component_name, [])
        else:
            metrics = []
            for component_metrics in self._performance_metrics.values():
                metrics.extend(component_metrics)

        # Filter by metric name
        if metric_name:
            metrics = [m for m in metrics if m["metric_name"] == metric_name]

        # Sort by timestamp (newest first) and limit
        metrics.sort(key=lambda x: x["timestamp"], reverse=True)
        metrics = metrics[:limit]

        return {
            "metrics": metrics,
            "total_count": len(metrics),
            "filtered_by": {"component": component_name, "metric": metric_name},
        }

    def get_event_summary(self) -> Dict[str, Any]:
        """Get event processing summary."""
        return self.event_bus.get_stats()

    def trigger_emergency_shutdown(self, reason: str) -> None:
        """Trigger emergency system shutdown."""
        logger.critical(f"EMERGENCY SHUTDOWN triggered: {reason}")

        self._add_alert(
            "critical",
            f"Emergency shutdown: {reason}",
            {"shutdown_reason": reason, "timestamp": datetime.now()},
        )

        # Publish emergency shutdown event
        self.event_bus.publish_event(
            EventType.SYSTEM_SHUTDOWN,
            {"reason": reason, "timestamp": datetime.now()},
            "monitoring_system",
        )

    def is_system_healthy(self) -> bool:
        """Check if the overall system is healthy."""
        # System is healthy if:
        # 1. All critical components are healthy
        # 2. No critical alerts in last hour
        # 3. Event processing is active

        # Check component health
        critical_components = ["trading_bot", "execution_client", "risk_manager"]
        for component in critical_components:
            if component in self._health_status:
                if self._health_status[component]["status"] != "healthy":
                    return False

        # Check for recent critical alerts
        one_hour_ago = datetime.now() - timedelta(hours=1)
        recent_critical = [
            alert
            for alert in self._alerts
            if alert["level"] == "critical" and alert["timestamp"] > one_hour_ago
        ]
        if recent_critical:
            return False

        # Check event processing
        event_stats = self.event_bus.get_stats()
        if event_stats.get("total_events", 0) == 0:
            return False

        return True


# Global monitoring system instance
_monitoring_system: Optional[MonitoringSystem] = None


def get_monitoring_system() -> MonitoringSystem:
    """Get the global monitoring system instance."""
    global _monitoring_system
    if _monitoring_system is None:
        _monitoring_system = MonitoringSystem()
    return _monitoring_system
