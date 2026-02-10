#!/usr/bin/env python3
"""
Monitoring and Alerting System for Trading Bot.
Provides metrics collection, health checks, and alerting capabilities.
"""

import time
import psutil
import asyncio
from typing import Dict, List, Optional, Any, Callable
from datetime import datetime, timedelta
import logging
import json
import aiohttp
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertChannel(Enum):
    LOG = "log"
    EMAIL = "email"
    TELEGRAM = "telegram"
    WEBHOOK = "webhook"
    SLACK = "slack"


@dataclass
class Alert:
    """Alert data structure."""

    id: str
    level: AlertLevel
    title: str
    message: str
    source: str
    timestamp: datetime
    resolved: bool = False
    resolved_at: Optional[datetime] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class HealthCheck:
    """Health check result."""

    name: str
    status: str  # "healthy", "unhealthy", "degraded"
    message: str
    timestamp: datetime
    response_time: float
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class SystemMetrics:
    """System performance metrics."""

    timestamp: datetime
    cpu_percent: float
    memory_percent: float
    disk_usage: float
    network_connections: int
    active_threads: int
    uptime_seconds: float


@dataclass
class TradingMetrics:
    """Trading-specific metrics."""

    timestamp: datetime
    active_positions: int
    total_pnl: float
    win_rate: float
    trades_today: int
    signals_generated: int
    api_requests: int
    database_connections: int
    cache_hit_rate: float


class MetricsCollector:
    """Collects and exposes system and trading metrics."""

    def __init__(self):
        self.system_metrics: List[SystemMetrics] = []
        self.trading_metrics: List[TradingMetrics] = []
        self.max_metrics_history = 1000

        # Prometheus-style metrics
        self._metrics = {
            # System metrics
            "cpu_usage_percent": 0.0,
            "memory_usage_percent": 0.0,
            "disk_usage_percent": 0.0,
            "network_connections": 0,
            # Trading metrics
            "active_positions": 0,
            "total_pnl_usd": 0.0,
            "win_rate_percent": 0.0,
            "trades_per_day": 0,
            "signals_per_minute": 0,
            "api_requests_total": 0,
            "database_query_duration_seconds": 0.0,
            "cache_hit_rate_percent": 0.0,
            # Business metrics
            "portfolio_value_usd": 0.0,
            "risk_level": "low",
            "circuit_breaker_triggered": 0,
            "emergency_stop_triggered": 0,
        }

    def collect_system_metrics(self) -> SystemMetrics:
        """Collect current system metrics."""
        metrics = SystemMetrics(
            timestamp=datetime.now(),
            cpu_percent=psutil.cpu_percent(interval=1),
            memory_percent=psutil.virtual_memory().percent,
            disk_usage=psutil.disk_usage("/").percent,
            network_connections=len(psutil.net_connections()),
            active_threads=len(psutil.Process().threads()),
            uptime_seconds=time.time() - psutil.boot_time(),
        )

        self.system_metrics.append(metrics)
        if len(self.system_metrics) > self.max_metrics_history:
            self.system_metrics.pop(0)

        # Update Prometheus metrics
        self._metrics["cpu_usage_percent"] = metrics.cpu_percent
        self._metrics["memory_usage_percent"] = metrics.memory_percent
        self._metrics["disk_usage_percent"] = metrics.disk_usage
        self._metrics["network_connections"] = metrics.network_connections

        return metrics

    def update_trading_metrics(self, **kwargs):
        """Update trading-specific metrics."""
        metrics = TradingMetrics(
            timestamp=datetime.now(),
            active_positions=kwargs.get("active_positions", 0),
            total_pnl=kwargs.get("total_pnl", 0.0),
            win_rate=kwargs.get("win_rate", 0.0),
            trades_today=kwargs.get("trades_today", 0),
            signals_generated=kwargs.get("signals_generated", 0),
            api_requests=kwargs.get("api_requests", 0),
            database_connections=kwargs.get("database_connections", 0),
            cache_hit_rate=kwargs.get("cache_hit_rate", 0.0),
        )

        self.trading_metrics.append(metrics)
        if len(self.trading_metrics) > self.max_metrics_history:
            self.trading_metrics.pop(0)

        # Update Prometheus metrics
        self._metrics.update(
            {
                "active_positions": metrics.active_positions,
                "total_pnl_usd": metrics.total_pnl,
                "win_rate_percent": metrics.win_rate,
                "trades_per_day": metrics.trades_today,
                "signals_per_minute": metrics.signals_generated,
                "api_requests_total": metrics.api_requests,
                "cache_hit_rate_percent": metrics.cache_hit_rate,
            }
        )

    def get_metrics(self) -> Dict[str, Any]:
        """Get all current metrics in Prometheus format."""
        return self._metrics.copy()

    def get_prometheus_output(self) -> str:
        """Generate Prometheus-formatted metrics output."""
        lines = []
        for key, value in self._metrics.items():
            if isinstance(value, (int, float)):
                lines.append(f"trading_bot_{key} {value}")
            else:
                lines.append(f'trading_bot_{key} "{value}"')

        return "\n".join(lines)


class HealthChecker:
    """Performs health checks on system components."""

    def __init__(self):
        self.checks: Dict[str, Callable] = {}
        self.last_results: Dict[str, HealthCheck] = {}

    def register_check(self, name: str, check_func: Callable):
        """Register a health check function."""
        self.checks[name] = check_func

    async def run_all_checks(self) -> List[HealthCheck]:
        """Run all registered health checks."""
        results = []

        for name, check_func in self.checks.items():
            start_time = time.time()
            try:
                if asyncio.iscoroutinefunction(check_func):
                    status, message, metadata = await check_func()
                else:
                    status, message, metadata = check_func()

                response_time = time.time() - start_time

                health_check = HealthCheck(
                    name=name,
                    status=status,
                    message=message,
                    timestamp=datetime.now(),
                    response_time=response_time,
                    metadata=metadata,
                )

                results.append(health_check)
                self.last_results[name] = health_check

            except Exception as e:
                logger.error(f"Health check {name} failed: {e}")
                health_check = HealthCheck(
                    name=name,
                    status="unhealthy",
                    message=f"Check failed: {str(e)}",
                    timestamp=datetime.now(),
                    response_time=time.time() - start_time,
                    metadata={"error": str(e)},
                )
                results.append(health_check)
                self.last_results[name] = health_check

        return results

    def get_overall_health(self) -> str:
        """Get overall system health status."""
        if not self.last_results:
            return "unknown"

        statuses = [check.status for check in self.last_results.values()]

        if "unhealthy" in statuses:
            return "unhealthy"
        elif "degraded" in statuses:
            return "degraded"
        else:
            return "healthy"


class AlertManager:
    """Manages alerts and notifications."""

    def __init__(self):
        self.alerts: List[Alert] = []
        self.channels: Dict[AlertChannel, Callable] = {}
        self.active_alerts: Dict[str, Alert] = {}

    def register_channel(self, channel: AlertChannel, handler: Callable):
        """Register an alert notification channel."""
        self.channels[channel] = handler

    async def send_alert(self, alert: Alert, channels: List[AlertChannel] = None):
        """Send an alert through specified channels."""
        if channels is None:
            channels = [AlertChannel.LOG]  # Default to logging

        self.alerts.append(alert)
        self.active_alerts[alert.id] = alert

        for channel in channels:
            if channel in self.channels:
                try:
                    if asyncio.iscoroutinefunction(self.channels[channel]):
                        await self.channels[channel](alert)
                    else:
                        self.channels[channel](alert)
                except Exception as e:
                    logger.error(f"Failed to send alert via {channel.value}: {e}")

    async def resolve_alert(self, alert_id: str):
        """Resolve an active alert."""
        if alert_id in self.active_alerts:
            alert = self.active_alerts[alert_id]
            alert.resolved = True
            alert.resolved_at = datetime.now()

            # Send resolution notification
            resolution_alert = Alert(
                id=f"{alert_id}_resolved",
                level=AlertLevel.INFO,
                title=f"Alert Resolved: {alert.title}",
                message=f"Alert has been resolved: {alert.message}",
                source=alert.source,
                timestamp=datetime.now(),
                resolved=True,
                metadata={"original_alert_id": alert_id},
            )

            await self.send_alert(resolution_alert, [AlertChannel.LOG])

    def get_active_alerts(self) -> List[Alert]:
        """Get all active (unresolved) alerts."""
        return [alert for alert in self.active_alerts.values() if not alert.resolved]


# Global instances
metrics_collector = MetricsCollector()
health_checker = HealthChecker()
alert_manager = AlertManager()


# Built-in alert channels
async def log_alert_handler(alert: Alert):
    """Log alerts to the logging system."""
    level_map = {
        AlertLevel.INFO: logging.INFO,
        AlertLevel.WARNING: logging.WARNING,
        AlertLevel.ERROR: logging.ERROR,
        AlertLevel.CRITICAL: logging.CRITICAL,
    }

    logger.log(
        level_map.get(alert.level, logging.INFO),
        f"ALERT [{alert.level.value.upper()}]: {alert.title} - {alert.message}",
        extra={"alert_data": asdict(alert)},
    )


async def telegram_alert_handler(alert: Alert):
    """Send alerts via Telegram bot."""
    import os

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        logger.warning("Telegram credentials not configured")
        return

    try:
        message = f"🚨 *{alert.level.value.upper()}*: {alert.title}\n{alert.message}"

        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            data = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
            async with session.post(url, json=data) as response:
                if response.status != 200:
                    logger.error(f"Telegram alert failed: {response.status}")

    except Exception as e:
        logger.error(f"Telegram alert error: {e}")


async def webhook_alert_handler(alert: Alert):
    """Send alerts via webhook."""
    import os

    webhook_url = os.getenv("ALERT_WEBHOOK_URL")

    if not webhook_url:
        logger.warning("Webhook URL not configured")
        return

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=asdict(alert)) as response:
                if response.status not in [200, 201, 202]:
                    logger.error(f"Webhook alert failed: {response.status}")

    except Exception as e:
        logger.error(f"Webhook alert error: {e}")


# Register default channels
alert_manager.register_channel(AlertChannel.LOG, log_alert_handler)
alert_manager.register_channel(AlertChannel.TELEGRAM, telegram_alert_handler)
alert_manager.register_channel(AlertChannel.WEBHOOK, webhook_alert_handler)


# Built-in health checks
async def database_health_check():
    """Check database connectivity."""
    try:
        from database import DatabaseManager

        db = DatabaseManager()
        stats = db.get_stats()
        return "healthy", f"Database OK - {stats['trades_count']} trades", stats
    except Exception as e:
        return "unhealthy", f"Database error: {str(e)}", {"error": str(e)}


async def api_health_check():
    """Check API server health."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "http://localhost:8000/api/health", timeout=5
            ) as response:
                if response.status == 200:
                    return (
                        "healthy",
                        "API server responding",
                        {"status_code": response.status},
                    )
                else:
                    return (
                        "unhealthy",
                        f"API server error: {response.status}",
                        {"status_code": response.status},
                    )
    except Exception as e:
        return "degraded", f"API server unreachable: {str(e)}", {"error": str(e)}


def system_health_check():
    """Check system resources."""
    try:
        cpu = psutil.cpu_percent()
        memory = psutil.virtual_memory().percent
        disk = psutil.disk_usage("/").percent

        if cpu > 90 or memory > 90 or disk > 95:
            return (
                "unhealthy",
                f"High resource usage: CPU {cpu}%, MEM {memory}%, DISK {disk}%",
                {"cpu": cpu, "memory": memory, "disk": disk},
            )
        elif cpu > 70 or memory > 80 or disk > 90:
            return (
                "degraded",
                f"Elevated resource usage: CPU {cpu}%, MEM {memory}%, DISK {disk}%",
                {"cpu": cpu, "memory": memory, "disk": disk},
            )
        else:
            return (
                "healthy",
                f"Resource usage normal: CPU {cpu}%, MEM {memory}%, DISK {disk}%",
                {"cpu": cpu, "memory": memory, "disk": disk},
            )
    except Exception as e:
        return "unhealthy", f"System check error: {str(e)}", {"error": str(e)}


# Register default health checks
health_checker.register_check("database", database_health_check)
health_checker.register_check("api", api_health_check)
health_checker.register_check("system", system_health_check)
