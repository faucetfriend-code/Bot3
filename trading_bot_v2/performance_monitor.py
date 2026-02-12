"""
Comprehensive performance monitoring system for trading bot v2.

This module provides real-time monitoring, metrics collection, performance
analytics, and alerting capabilities for the trading system.

Key Features:
- Real-time system metrics collection (CPU, memory, database, API)
- Performance dashboard with WebSocket updates
- Bottleneck detection and automatic optimization
- Trading performance analytics
- Resource usage monitoring and alerting
- Historical data analysis and trend detection
"""

import asyncio
import time
import threading
import json
import psutil
from collections import deque, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import (
    Any, Dict, List, Optional, Callable, Set, Union,
    Tuple, Deque
)
import logging
from loguru import logger
import sqlite3
import aiosqlite
from pathlib import Path


class MetricType(Enum):
    """Types of metrics collected."""
    COUNTER = "counter"      # Cumulative counter
    GAUGE = "gauge"         # Current value
    HISTOGRAM = "histogram"  # Distribution of values
    TIMER = "timer"         # Time measurements


class AlertSeverity(Enum):
    """Alert severity levels."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Metric:
    """Individual metric data point."""
    name: str
    value: float
    timestamp: datetime = field(default_factory=datetime.now)
    labels: Dict[str, str] = field(default_factory=dict)
    metric_type: MetricType = MetricType.GAUGE
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'name': self.name,
            'value': self.value,
            'timestamp': self.timestamp.isoformat(),
            'labels': self.labels,
            'type': self.metric_type.value
        }


@dataclass
class PerformanceAlert:
    """Performance alert definition."""
    alert_id: str
    name: str
    severity: AlertSeverity
    condition: str  # Python expression for evaluation
    threshold: float
    metric_name: str
    labels: Dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    last_triggered: Optional[datetime] = None
    trigger_count: int = 0
    cooldown_minutes: int = 5
    
    def evaluate(self, metrics: Dict[str, float]) -> bool:
        """Evaluate alert condition against current metrics."""
        if not self.enabled:
            return False
        
        # Check cooldown period
        if self.last_triggered:
            cooldown_elapsed = (datetime.now() - self.last_triggered).total_seconds() / 60
            if cooldown_elapsed < self.cooldown_minutes:
                return False
        
        # Get metric value
        metric_value = metrics.get(self.metric_name)
        if metric_value is None:
            return False
        
        # Evaluate condition
        try:
            condition_result = eval(self.condition.format(
                metric=metric_value,
                threshold=self.threshold
            ))
            return condition_result
        except Exception as e:
            logger.error(f"Error evaluating alert {self.alert_id}: {e}")
            return False
    
    def trigger(self):
        """Trigger the alert."""
        self.last_triggered = datetime.now()
        self.trigger_count += 1
        
        logger.warning(
            f"Performance alert triggered: {self.name} "
            f"(Severity: {self.severity.value}, "
            f"Metric: {self.metric_name}, "
            f"Trigger count: {self.trigger_count})"
        )


class MetricsCollector:
    """Collects and manages metrics data."""
    
    def __init__(self, retention_hours: int = 24):
        self.retention_hours = retention_hours
        self._metrics: Dict[str, Deque[Metric]] = defaultdict(lambda: deque(maxlen=10000))
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = defaultdict(float)
        self._histograms: Dict[str, Deque[float]] = defaultdict(lambda: deque(maxlen=1000))
        self._timers: Dict[str, List[float]] = defaultdict(list)
        self._lock = asyncio.Lock()
    
    async def record_metric(
        self,
        name: str,
        value: float,
        metric_type: MetricType = MetricType.GAUGE,
        labels: Optional[Dict[str, str]] = None
    ):
        """Record a metric value."""
        async with self._lock:
            metric = Metric(
                name=name,
                value=value,
                labels=labels or {},
                metric_type=metric_type
            )
            
            self._metrics[name].append(metric)
            
            # Update type-specific storage
            if metric_type == MetricType.COUNTER:
                self._counters[name] += value
            elif metric_type == MetricType.GAUGE:
                self._gauges[name] = value
            elif metric_type == MetricType.HISTOGRAM:
                self._histograms[name].append(value)
            elif metric_type == MetricType.TIMER:
                self._timers[name].append(value)
                # Keep only last 1000 timer values
                if len(self._timers[name]) > 1000:
                    self._timers[name] = self._timers[name][-1000:]
    
    async def increment_counter(
        self,
        name: str,
        value: float = 1.0,
        labels: Optional[Dict[str, str]] = None
    ):
        """Increment a counter metric."""
        await self.record_metric(name, value, MetricType.COUNTER, labels)
    
    async def set_gauge(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None
    ):
        """Set a gauge metric value."""
        await self.record_metric(name, value, MetricType.GAUGE, labels)
    
    async def record_histogram(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None
    ):
        """Record a histogram metric value."""
        await self.record_metric(name, value, MetricType.HISTOGRAM, labels)
    
    async def record_timer(
        self,
        name: str,
        duration: float,
        labels: Optional[Dict[str, str]] = None
    ):
        """Record a timer metric duration."""
        await self.record_metric(name, duration, MetricType.TIMER, labels)
    
    async def get_metric(
        self,
        name: str,
        since: Optional[datetime] = None
    ) -> List[Metric]:
        """Get metrics for a specific name since given time."""
        async with self._lock:
            if name not in self._metrics:
                return []
            
            metrics = list(self._metrics[name])
            
            if since:
                metrics = [m for m in metrics if m.timestamp >= since]
            
            return metrics
    
    async def get_current_value(self, name: str) -> Optional[float]:
        """Get current value for a metric."""
        async with self._lock:
            if name in self._gauges:
                return self._gauges[name]
            elif name in self._counters:
                return self._counters[name]
            elif name in self._metrics and self._metrics[name]:
                return self._metrics[name][-1].value
            return None
    
    async def get_summary_stats(self, name: str) -> Dict[str, float]:
        """Get summary statistics for a metric."""
        async with self._lock:
            if name in self._histograms:
                values = list(self._histograms[name])
            elif name in self._timers:
                values = self._timers[name]
            else:
                # Get recent gauge values
                metrics = list(self._metrics[name])
                values = [m.value for m in metrics[-100:]]  # Last 100 values
            
            if not values:
                return {}
            
            values.sort()
            count = len(values)
            
            return {
                'count': count,
                'sum': sum(values),
                'min': values[0],
                'max': values[-1],
                'mean': sum(values) / count,
                'median': values[count // 2] if count else 0,
                'p95': values[int(count * 0.95)] if count >= 20 else values[-1],
                'p99': values[int(count * 0.99)] if count >= 100 else values[-1]
            }
    
    async def cleanup_old_metrics(self):
        """Remove metrics older than retention period."""
        cutoff_time = datetime.now() - timedelta(hours=self.retention_hours)
        
        async with self._lock:
            for name, metric_deque in self._metrics.items():
                # Remove old metrics
                while metric_deque and metric_deque[0].timestamp < cutoff_time:
                    metric_deque.popleft()


class SystemMetricsCollector:
    """Collects system-level metrics."""
    
    def __init__(self, metrics_collector: MetricsCollector):
        self.metrics_collector = metrics_collector
        self._running = False
        self._collection_task: Optional[asyncio.Task] = None
        self._collection_interval = 30  # seconds
    
    async def start(self):
        """Start system metrics collection."""
        if self._running:
            return
        
        self._running = True
        self._collection_task = asyncio.create_task(self._collection_loop())
        logger.info("System metrics collection started")
    
    async def stop(self):
        """Stop system metrics collection."""
        if not self._running:
            return
        
        self._running = False
        if self._collection_task:
            self._collection_task.cancel()
            try:
                await self._collection_task
            except asyncio.CancelledError:
                pass
        
        logger.info("System metrics collection stopped")
    
    async def _collection_loop(self):
        """Main collection loop."""
        while self._running:
            try:
                await self._collect_system_metrics()
                await asyncio.sleep(self._collection_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in system metrics collection: {e}")
                await asyncio.sleep(self._collection_interval)
    
    async def _collect_system_metrics(self):
        """Collect system metrics."""
        # CPU metrics
        cpu_percent = psutil.cpu_percent(interval=1)
        await self.metrics_collector.set_gauge("system.cpu.usage", cpu_percent)
        
        # Memory metrics
        memory = psutil.virtual_memory()
        await self.metrics_collector.set_gauge("system.memory.usage", memory.percent)
        await self.metrics_collector.set_gauge("system.memory.available", memory.available)
        await self.metrics_collector.set_gauge("system.memory.used", memory.used)
        
        # Disk metrics
        disk = psutil.disk_usage('/')
        await self.metrics_collector.set_gauge("system.disk.usage", (disk.used / disk.total) * 100)
        await self.metrics_collector.set_gauge("system.disk.free", disk.free)
        
        # Network metrics
        network = psutil.net_io_counters()
        await self.metrics_collector.increment_counter("system.network.bytes_sent", network.bytes_sent)
        await self.metrics_collector.increment_counter("system.network.bytes_recv", network.bytes_recv)
        
        # Process metrics
        process = psutil.Process()
        await self.metrics_collector.set_gauge("process.memory.rss", process.memory_info().rss)
        await self.metrics_collector.set_gauge("process.memory.vms", process.memory_info().vms)
        await self.metrics_collector.set_gauge("process.cpu.percent", process.cpu_percent())
        await self.metrics_collector.set_gauge("process.threads", process.num_threads())
        
        # File descriptors
        try:
            await self.metrics_collector.set_gauge("process.fd_count", process.num_fds())
        except (AttributeError, psutil.AccessDenied):
            # Not available on all platforms
            pass


class DatabaseMetricsCollector:
    """Collects database performance metrics."""
    
    def __init__(self, metrics_collector: MetricsCollector, db_path: str):
        self.metrics_collector = metrics_collector
        self.db_path = db_path
        self._running = False
        self._collection_task: Optional[asyncio.Task] = None
        self._collection_interval = 60  # seconds
    
    async def start(self):
        """Start database metrics collection."""
        if self._running:
            return
        
        self._running = True
        self._collection_task = asyncio.create_task(self._collection_loop())
        logger.info("Database metrics collection started")
    
    async def stop(self):
        """Stop database metrics collection."""
        if not self._running:
            return
        
        self._running = False
        if self._collection_task:
            self._collection_task.cancel()
            try:
                await self._collection_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Database metrics collection stopped")
    
    async def _collection_loop(self):
        """Main collection loop."""
        while self._running:
            try:
                await self._collect_database_metrics()
                await asyncio.sleep(self._collection_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in database metrics collection: {e}")
                await asyncio.sleep(self._collection_interval)
    
    async def _collect_database_metrics(self):
        """Collect database performance metrics."""
        try:
            # Database file size
            if Path(self.db_path).exists():
                db_size = Path(self.db_path).stat().st_size
                await self.metrics_collector.set_gauge("database.size_bytes", db_size)
            
            # Connection pool metrics (if available)
            # This would need to be implemented based on actual connection pool
            
            # Query performance metrics
            async with aiosqlite.connect(self.db_path) as db:
                # Get table statistics
                cursor = await db.execute(
                    "SELECT name, COUNT(*) as row_count FROM sqlite_master "
                    "WHERE type='table' GROUP BY name"
                )
                tables = await cursor.fetchall()
                
                for table_name, row_count in tables:
                    await self.metrics_collector.set_gauge(
                        f"database.table.{table_name}.rows",
                        row_count
                    )
                
                # Database page count
                cursor = await db.execute("PRAGMA page_count")
                page_count = (await cursor.fetchone())[0]
                await self.metrics_collector.set_gauge("database.page_count", page_count)
                
                # Database page size
                cursor = await db.execute("PRAGMA page_size")
                page_size = (await cursor.fetchone())[0]
                await self.metrics_collector.set_gauge("database.page_size", page_size)
                
        except Exception as e:
            logger.error(f"Error collecting database metrics: {e}")


class TradingMetricsCollector:
    """Collects trading-specific performance metrics."""
    
    def __init__(self, metrics_collector: MetricsCollector):
        self.metrics_collector = metrics_collector
    
    async def record_trade_execution(
        self,
        symbol: str,
        strategy: str,
        side: str,
        quantity: float,
        price: float,
        execution_time: float,
        success: bool
    ):
        """Record trade execution metrics."""
        # Basic trade metrics
        await self.metrics_collector.increment_counter(
            "trading.trades.total",
            labels={'symbol': symbol, 'strategy': strategy, 'side': side}
        )
        
        if success:
            await self.metrics_collector.increment_counter(
                "trading.trades.successful",
                labels={'symbol': symbol, 'strategy': strategy, 'side': side}
            )
        else:
            await self.metrics_collector.increment_counter(
                "trading.trades.failed",
                labels={'symbol': symbol, 'strategy': strategy, 'side': side}
            )
        
        # Execution time metrics
        await self.metrics_collector.record_timer(
            "trading.execution.time",
            execution_time,
            labels={'symbol': symbol, 'strategy': strategy}
        )
        
        # Volume metrics
        await self.metrics_collector.record_histogram(
            "trading.volume.size",
            quantity,
            labels={'symbol': symbol, 'strategy': strategy, 'side': side}
        )
    
    async def record_signal_generation(
        self,
        symbol: str,
        strategy: str,
        signal_strength: float,
        generation_time: float
    ):
        """Record signal generation metrics."""
        await self.metrics_collector.increment_counter(
            "trading.signals.generated",
            labels={'symbol': symbol, 'strategy': strategy}
        )
        
        await self.metrics_collector.record_histogram(
            "trading.signals.strength",
            signal_strength,
            labels={'symbol': symbol, 'strategy': strategy}
        )
        
        await self.metrics_collector.record_timer(
            "trading.signals.generation_time",
            generation_time,
            labels={'symbol': symbol, 'strategy': strategy}
        )
    
    async def record_pnl(
        self,
        symbol: str,
        strategy: str,
        pnl: float,
        pnl_percent: float
    ):
        """Record P&L metrics."""
        await self.metrics_collector.record_histogram(
            "trading.pnl.absolute",
            pnl,
            labels={'symbol': symbol, 'strategy': strategy}
        )
        
        await self.metrics_collector.record_histogram(
            "trading.pnl.percent",
            pnl_percent,
            labels={'symbol': symbol, 'strategy': strategy}
        )
        
        # Track running P&L
        current_total = await self.metrics_collector.get_current_value("trading.pnl.total") or 0
        await self.metrics_collector.set_gauge("trading.pnl.total", current_total + pnl)


class AlertManager:
    """Manages performance alerts and notifications."""
    
    def __init__(self, metrics_collector: MetricsCollector):
        self.metrics_collector = metrics_collector
        self._alerts: Dict[str, PerformanceAlert] = {}
        self._alert_handlers: List[Callable[[PerformanceAlert], None]] = []
        self._evaluation_task: Optional[asyncio.Task] = None
        self._running = False
        self._evaluation_interval = 30  # seconds
    
    async def start(self):
        """Start alert evaluation."""
        if self._running:
            return
        
        self._running = True
        self._evaluation_task = asyncio.create_task(self._evaluation_loop())
        logger.info("Alert manager started")
    
    async def stop(self):
        """Stop alert evaluation."""
        if not self._running:
            return
        
        self._running = False
        if self._evaluation_task:
            self._evaluation_task.cancel()
            try:
                await self._evaluation_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Alert manager stopped")
    
    async def add_alert(self, alert: PerformanceAlert):
        """Add a new alert."""
        self._alerts[alert.alert_id] = alert
        logger.info(f"Added alert: {alert.name}")
    
    async def remove_alert(self, alert_id: str):
        """Remove an alert."""
        if alert_id in self._alerts:
            del self._alerts[alert_id]
            logger.info(f"Removed alert: {alert_id}")
    
    async def add_alert_handler(self, handler: Callable[[PerformanceAlert], None]):
        """Add alert notification handler."""
        self._alert_handlers.append(handler)
    
    async def _evaluation_loop(self):
        """Main alert evaluation loop."""
        while self._running:
            try:
                await self._evaluate_alerts()
                await asyncio.sleep(self._evaluation_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in alert evaluation: {e}")
                await asyncio.sleep(self._evaluation_interval)
    
    async def _evaluate_alerts(self):
        """Evaluate all enabled alerts."""
        # Get current metric values
        current_metrics = {}
        metric_names = set(alert.metric_name for alert in self._alerts.values() if alert.enabled)
        
        for metric_name in metric_names:
            value = await self.metrics_collector.get_current_value(metric_name)
            if value is not None:
                current_metrics[metric_name] = value
        
        # Evaluate alerts
        for alert in self._alerts.values():
            if alert.evaluate(current_metrics):
                alert.trigger()
                
                # Notify handlers
                for handler in self._alert_handlers:
                    try:
                        handler(alert)
                    except Exception as e:
                        logger.error(f"Alert handler error: {e}")


class PerformanceMonitor:
    """Main performance monitoring system."""
    
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or "data/performance.db"
        self.metrics_collector = MetricsCollector()
        self.system_collector = SystemMetricsCollector(self.metrics_collector)
        self.database_collector = DatabaseMetricsCollector(self.metrics_collector, self.db_path)
        self.trading_collector = TradingMetricsCollector(self.metrics_collector)
        self.alert_manager = AlertManager(self.metrics_collector)
        
        self._running = False
        self._cleanup_task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Start the performance monitoring system."""
        if self._running:
            logger.warning("Performance monitor is already running")
            return
        
        self._running = True
        
        # Start collectors
        await self.system_collector.start()
        await self.database_collector.start()
        await self.alert_manager.start()
        
        # Start cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        
        # Setup default alerts
        await self._setup_default_alerts()
        
        logger.info("Performance monitoring system started")
    
    async def stop(self):
        """Stop the performance monitoring system."""
        if not self._running:
            return
        
        self._running = False
        
        # Stop collectors
        await self.system_collector.stop()
        await self.database_collector.stop()
        await self.alert_manager.stop()
        
        # Stop cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        logger.info("Performance monitoring system stopped")
    
    async def _cleanup_loop(self):
        """Cleanup old metrics data."""
        while self._running:
            try:
                await self.metrics_collector.cleanup_old_metrics()
                await asyncio.sleep(3600)  # Run cleanup every hour
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
                await asyncio.sleep(3600)
    
    async def _setup_default_alerts(self):
        """Setup default performance alerts."""
        default_alerts = [
            PerformanceAlert(
                alert_id="high_cpu_usage",
                name="High CPU Usage",
                severity=AlertSeverity.HIGH,
                condition="metric > threshold",
                threshold=80.0,
                metric_name="system.cpu.usage"
            ),
            PerformanceAlert(
                alert_id="high_memory_usage",
                name="High Memory Usage",
                severity=AlertSeverity.HIGH,
                condition="metric > threshold",
                threshold=85.0,
                metric_name="system.memory.usage"
            ),
            PerformanceAlert(
                alert_id="slow_trade_execution",
                name="Slow Trade Execution",
                severity=AlertSeverity.MEDIUM,
                condition="metric > threshold",
                threshold=5.0,
                metric_name="trading.execution.time"
            ),
            PerformanceAlert(
                alert_id="database_size_large",
                name="Database Size Too Large",
                severity=AlertSeverity.LOW,
                condition="metric > threshold",
                threshold=1024 * 1024 * 1024,  # 1GB
                metric_name="database.size_bytes"
            )
        ]
        
        for alert in default_alerts:
            await self.alert_manager.add_alert(alert)
    
    # Convenience methods for recording metrics
    
    async def record_task_execution(
        self,
        task_id: str,
        execution_time: float,
        status: str
    ):
        """Record task execution metrics."""
        await self.metrics_collector.record_timer(
            "tasks.execution.time",
            execution_time,
            labels={'status': status}
        )
        
        await self.metrics_collector.increment_counter(
            "tasks.executed",
            labels={'status': status}
        )
    
    async def record_api_call(
        self,
        endpoint: str,
        response_time: float,
        status_code: int
    ):
        """Record API call metrics."""
        await self.metrics_collector.record_timer(
            "api.response.time",
            response_time,
            labels={'endpoint': endpoint, 'status': str(status_code)}
        )
        
        await self.metrics_collector.increment_counter(
            "api.requests",
            labels={'endpoint': endpoint, 'status': str(status_code)}
        )
    
    async def get_summary(self) -> Dict[str, Any]:
        """Get comprehensive performance summary."""
        summary = {
            'timestamp': datetime.now().isoformat(),
            'metrics': {},
            'alerts': {
                'total': len(self.alert_manager._alerts),
                'enabled': len([a for a in self.alert_manager._alerts.values() if a.enabled]),
                'triggered_24h': len([a for a in self.alert_manager._alerts.values() 
                                   if a.last_triggered and 
                                   (datetime.now() - a.last_triggered).total_seconds() < 86400])
            }
        }
        
        # Add key metric summaries
        key_metrics = [
            "system.cpu.usage",
            "system.memory.usage",
            "trading.execution.time",
            "api.response.time",
            "tasks.execution.time"
        ]
        
        for metric_name in key_metrics:
            stats = await self.metrics_collector.get_summary_stats(metric_name)
            current_value = await self.metrics_collector.get_current_value(metric_name)
            
            summary['metrics'][metric_name] = {
                'current': current_value,
                'stats': stats
            }
        
        return summary
    
    async def export_metrics(
        self,
        start_time: datetime,
        end_time: datetime,
        metric_names: Optional[List[str]] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Export metrics data for analysis."""
        exported_data = {}
        
        names_to_export = metric_names or list(self.metrics_collector._metrics.keys())
        
        for metric_name in names_to_export:
            metrics = await self.metrics_collector.get_metric(metric_name, start_time)
            # Filter by end time as well
            metrics = [m for m in metrics if m.timestamp <= end_time]
            
            exported_data[metric_name] = [m.to_dict() for m in metrics]
        
        return exported_data


# Global performance monitor instance
_performance_monitor: Optional[PerformanceMonitor] = None


async def get_performance_monitor() -> PerformanceMonitor:
    """Get or create global performance monitor instance."""
    global _performance_monitor
    if _performance_monitor is None:
        _performance_monitor = PerformanceMonitor()
    return _performance_monitor


async def record_trading_metrics(
    symbol: str,
    strategy: str,
    execution_time: float,
    success: bool = True
):
    """Record trading performance metrics."""
    monitor = await get_performance_monitor()
    await monitor.trading_collector.record_trade_execution(
        symbol, strategy, "buy", 0, 0, execution_time, success
    )


async def record_system_metrics():
    """Trigger system metrics collection."""
    monitor = await get_performance_monitor()
    await monitor.system_collector._collect_system_metrics()