# Trading Bot v2 - Performance Monitoring & Maintenance

## Overview

This guide covers the setup, configuration, and operation of the comprehensive performance monitoring system for Trading Bot v2, including real-time metrics collection, alerting, maintenance procedures, and optimization strategies.

---

## Monitoring Architecture

### System Components

```
┌─────────────────────────────────────────────────────────────────┐
│                    Performance Monitor                          │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │   System     │  │   Trading    │  │       Task           │  │
│  │   Metrics    │  │   Metrics    │  │      Metrics         │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         │                 │                     │               │
│         └─────────────────┼─────────────────────┘               │
│                           ▼                                     │
│              ┌────────────────────────┐                        │
│              │    Metrics Collector   │                        │
│              └───────────┬────────────┘                        │
│                          │                                      │
│         ┌────────────────┼────────────────┐                    │
│         ▼                ▼                ▼                    │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   Alert     │  │   Dashboard  │  │  Historical  │          │
│  │   System    │  │     API      │  │   Analysis   │          │
│  └─────────────┘  └──────────────┘  └──────────────┘          │
└─────────────────────────────────────────────────────────────────┘
```

### Metric Types

| Type | Description | Example |
|------|-------------|---------|
| **Counter** | Cumulative count | Total trades executed |
| **Gauge** | Current value | Current CPU usage |
| **Histogram** | Distribution | Trade execution times |
| **Timer** | Duration measurements | API response time |

---

## Setup and Configuration

### Initial Setup

```python
# performance_monitor.py setup
from performance_monitor import get_performance_monitor

async def initialize_monitoring():
    """Initialize performance monitoring."""
    monitor = await get_performance_monitor()
    await monitor.start()
    
    # Configure default alerts
    await setup_default_alerts(monitor)
    
    return monitor

async def setup_default_alerts(monitor):
    """Setup default performance alerts."""
    from performance_monitor import PerformanceAlert, AlertSeverity
    
    alerts = [
        PerformanceAlert(
            alert_id="high_cpu",
            name="High CPU Usage",
            severity=AlertSeverity.HIGH,
            condition="{metric} > {threshold}",
            threshold=80.0,
            metric_name="system.cpu.usage"
        ),
        PerformanceAlert(
            alert_id="high_memory",
            name="High Memory Usage",
            severity=AlertSeverity.HIGH,
            condition="{metric} > {threshold}",
            threshold=85.0,
            metric_name="system.memory.usage"
        ),
        PerformanceAlert(
            alert_id="slow_database",
            name="Slow Database Queries",
            severity=AlertSeverity.MEDIUM,
            condition="{metric} > {threshold}",
            threshold=100.0,  # ms
            metric_name="database.query.time"
        ),
        PerformanceAlert(
            alert_id="high_error_rate",
            name="High Error Rate",
            severity=AlertSeverity.CRITICAL,
            condition="{metric} > {threshold}",
            threshold=10.0,  # errors per minute
            metric_name="errors.count"
        )
    ]
    
    for alert in alerts:
        await monitor.alert_manager.add_alert(alert)
```

### Configuration Options

```bash
# .env configuration
ENABLE_PERFORMANCE_MONITORING=true
METRICS_RETENTION_HOURS=24
ALERT_COOLDOWN_MINUTES=5
METRICS_COLLECTION_INTERVAL=10
```

### Alert Configuration

```python
# config/alerts.py
ALERT_CONFIG = {
    "cpu_high": {
        "threshold": 80,
        "severity": "high",
        "cooldown": 300,  # 5 minutes
        "message": "CPU usage above {value}%"
    },
    "memory_high": {
        "threshold": 85,
        "severity": "high",
        "cooldown": 300,
        "message": "Memory usage above {value}%"
    },
    "database_slow": {
        "threshold": 100,  # ms
        "severity": "medium",
        "cooldown": 600,
        "message": "Database query time {value}ms"
    },
    "api_errors": {
        "threshold": 10,  # per minute
        "severity": "critical",
        "cooldown": 60,
        "message": "{value} API errors in last minute"
    },
    "websocket_lag": {
        "threshold": 1000,  # ms
        "severity": "high",
        "cooldown": 300,
        "message": "WebSocket lag {value}ms"
    }
}
```

---

## Metrics Collection

### System Metrics

```python
async def collect_system_metrics(monitor):
    """Collect system resource metrics."""
    import psutil
    
    # CPU metrics
    cpu_percent = psutil.cpu_percent(interval=1)
    await monitor.metrics_collector.set_gauge(
        "system.cpu.usage", 
        cpu_percent,
        labels={"type": "percent"}
    )
    
    # Memory metrics
    memory = psutil.virtual_memory()
    await monitor.metrics_collector.set_gauge(
        "system.memory.usage",
        memory.percent,
        labels={"type": "percent"}
    )
    await monitor.metrics_collector.set_gauge(
        "system.memory.used_mb",
        memory.used / 1024 / 1024,
        labels={"type": "absolute"}
    )
    
    # Disk metrics
    disk = psutil.disk_usage('/')
    await monitor.metrics_collector.set_gauge(
        "system.disk.usage",
        disk.percent,
        labels={"type": "percent"}
    )
    
    # Network metrics
    net_io = psutil.net_io_counters()
    await monitor.metrics_collector.increment_counter(
        "system.network.bytes_sent",
        net_io.bytes_sent
    )
    await monitor.metrics_collector.increment_counter(
        "system.network.bytes_recv",
        net_io.bytes_recv
    )
```

### Trading Metrics

```python
async def record_trade_metrics(monitor, trade_result):
    """Record metrics for executed trades."""
    # Execution time
    await monitor.metrics_collector.record_timer(
        "trades.execution.time",
        trade_result['execution_time_ms'],
        labels={"strategy": trade_result['strategy']}
    )
    
    # Trade counter
    await monitor.metrics_collector.increment_counter(
        "trades.count",
        1,
        labels={
            "strategy": trade_result['strategy'],
            "side": trade_result['side'],
            "status": "success" if trade_result['success'] else "failed"
        }
    )
    
    # P&L metrics
    if trade_result.get('pnl'):
        await monitor.metrics_collector.record_histogram(
            "trades.pnl",
            trade_result['pnl'],
            labels={"strategy": trade_result['strategy']}
        )

async def record_strategy_performance(monitor, strategy_name, metrics):
    """Record strategy-specific performance metrics."""
    await monitor.metrics_collector.set_gauge(
        f"strategy.{strategy_name}.signals",
        metrics['signals_generated'],
        labels={"metric": "count"}
    )
    
    await monitor.metrics_collector.set_gauge(
        f"strategy.{strategy_name}.win_rate",
        metrics['win_rate'],
        labels={"metric": "percent"}
    )
```

### Task Queue Metrics

```python
async def collect_task_metrics(task_queue, monitor):
    """Collect task queue performance metrics."""
    stats = await task_queue.get_queue_stats()
    
    await monitor.metrics_collector.set_gauge(
        "tasks.pending",
        stats['pending_count']
    )
    
    await monitor.metrics_collector.set_gauge(
        "tasks.processing",
        stats['processing_count']
    )
    
    await monitor.metrics_collector.set_gauge(
        "tasks.completed",
        stats['completed_count']
    )
    
    await monitor.metrics_collector.record_histogram(
        "tasks.waiting_time",
        stats['avg_waiting_time_ms']
    )
```

---

## Alerting System

### Alert Rules

```python
# Define custom alert rules
from performance_monitor import PerformanceAlert, AlertSeverity

# High latency alert
high_latency_alert = PerformanceAlert(
    alert_id="high_api_latency",
    name="High API Latency",
    severity=AlertSeverity.MEDIUM,
    condition="{metric} > {threshold}",
    threshold=500,  # ms
    metric_name="api.response.time",
    cooldown_minutes=10,
    labels={"service": "pacifica_api"}
)

# Database connection alert
db_connection_alert = PerformanceAlert(
    alert_id="database_connection_failed",
    name="Database Connection Failed",
    severity=AlertSeverity.CRITICAL,
    condition="{metric} >= {threshold}",
    threshold=1,
    metric_name="database.connection.errors",
    cooldown_minutes=1
)
```

### Alert Handlers

```python
class AlertHandler:
    """Handle triggered alerts."""
    
    async def handle_alert(self, alert: PerformanceAlert, value: float):
        """Process triggered alert."""
        # Log alert
        logging.warning(
            f"Alert triggered: {alert.name} "
            f"(Severity: {alert.severity.value}, Value: {value})"
        )
        
        # Send notification based on severity
        if alert.severity == AlertSeverity.CRITICAL:
            await self._send_critical_notification(alert, value)
        elif alert.severity == AlertSeverity.HIGH:
            await self._send_high_notification(alert, value)
        
        # Take automated action if needed
        await self._take_automated_action(alert, value)
    
    async def _send_critical_notification(self, alert, value):
        """Send critical alert notification."""
        # Send email
        await self._send_email(
            to="oncall@company.com",
            subject=f"CRITICAL: {alert.name}",
            body=f"Alert {alert.name} triggered with value {value}"
        )
        
        # Send Slack message
        await self._send_slack(
            channel="#trading-alerts",
            message=f":red_circle: CRITICAL: {alert.name} = {value}"
        )
        
        # Send SMS
        await self._send_sms(
            to="+1234567890",
            message=f"CRITICAL Alert: {alert.name}"
        )
    
    async def _take_automated_action(self, alert, value):
        """Take automated action for alerts."""
        if alert.alert_id == "high_cpu":
            # Reduce worker threads
            await self._scale_down_workers()
        
        elif alert.alert_id == "database_connection_failed":
            # Switch to backup database
            await self._failover_database()
        
        elif alert.alert_id == "high_error_rate":
            # Pause trading temporarily
            await self._pause_trading(duration=300)
```

### Notification Channels

```python
# notifications.py
import aiohttp
import json

class NotificationManager:
    """Manage alert notifications."""
    
    async def send_slack(self, webhook_url: str, message: str):
        """Send Slack notification."""
        payload = {
            "text": message,
            "username": "Trading Bot Monitor"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url,
                json=payload
            ) as response:
                if response.status != 200:
                    logging.error(f"Failed to send Slack notification: {response.status}")
    
    async def send_email(self, smtp_config: dict, to: str, subject: str, body: str):
        """Send email notification."""
        import aiosmtplib
        from email.mime.text import MIMEText
        
        message = MIMEText(body)
        message["From"] = smtp_config['from']
        message["To"] = to
        message["Subject"] = subject
        
        await aiosmtplib.send(
            message,
            hostname=smtp_config['host'],
            port=smtp_config['port'],
            username=smtp_config['username'],
            password=smtp_config['password']
        )
    
    async def send_pagerduty(self, service_key: str, alert: dict):
        """Send PagerDuty incident."""
        payload = {
            "service_key": service_key,
            "event_type": "trigger",
            "description": alert['name'],
            "details": alert
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://events.pagerduty.com/generic/2010-04-15/create_event.json",
                json=payload
            ) as response:
                return response.status == 200
```

---

## Dashboard and Visualization

### WebSocket Metrics Endpoint

```python
@app.websocket("/ws/metrics")
async def metrics_websocket(websocket: WebSocket):
    """WebSocket endpoint for real-time metrics."""
    await websocket.accept()
    
    try:
        monitor = await get_performance_monitor()
        
        while True:
            # Get current metrics
            summary = await monitor.get_summary()
            
            # Send to client
            await websocket.send_json({
                "timestamp": datetime.now().isoformat(),
                "metrics": summary
            })
            
            # Update every second
            await asyncio.sleep(1)
            
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logging.error(f"Metrics WebSocket error: {e}")
```

### REST API Metrics Endpoints

```python
@app.get("/api/metrics/performance")
async def get_performance_metrics(timeframe: str = "24h"):
    """Get trading performance metrics."""
    monitor = await get_performance_monitor()
    
    # Calculate time range
    hours = int(timeframe.replace('h', ''))
    since = datetime.now() - timedelta(hours=hours)
    
    metrics = await monitor.trading_collector.get_performance_summary(since)
    
    return {
        "success": True,
        "data": metrics
    }

@app.get("/api/metrics/system")
async def get_system_metrics():
    """Get current system resource metrics."""
    monitor = await get_performance_monitor()
    
    # Get latest system metrics
    cpu = await monitor.metrics_collector.get_current_value("system.cpu.usage")
    memory = await monitor.metrics_collector.get_current_value("system.memory.usage")
    
    return {
        "success": True,
        "data": {
            "timestamp": datetime.now().isoformat(),
            "cpu": {"usage_percent": cpu},
            "memory": {"usage_percent": memory}
        }
    }

@app.get("/api/alerts")
async def get_active_alerts():
    """Get currently active alerts."""
    monitor = await get_performance_monitor()
    alerts = await monitor.alert_manager.get_active_alerts()
    
    return {
        "success": True,
        "data": [alert.to_dict() for alert in alerts]
    }
```

---

## Maintenance Procedures

### Daily Maintenance

```bash
#!/bin/bash
# daily_maintenance.sh

echo "=== Daily Maintenance $(date) ==="

# 1. Check disk space
DISK_USAGE=$(df /opt/trading-bot | tail -1 | awk '{print $5}' | tr -d '%')
if [ $DISK_USAGE -gt 80 ]; then
    echo "WARNING: Disk usage at ${DISK_USAGE}%"
    # Clean old logs
    find /opt/trading-bot/logs -name "*.log.*" -mtime +7 -delete
fi

# 2. Check metrics retention
sqlite3 /opt/trading-bot/data/trading_bot.db << 'EOF'
DELETE FROM performance_metrics 
WHERE timestamp < datetime('now', '-7 days');
VACUUM;
EOF

# 3. Verify alert system
curl -s http://localhost:8000/api/alerts > /dev/null
if [ $? -ne 0 ]; then
    echo "ERROR: Alert API not responding"
fi

echo "=== Daily Maintenance Complete ==="
```

### Weekly Maintenance

```bash
#!/bin/bash
# weekly_maintenance.sh

echo "=== Weekly Maintenance $(date) ==="

# 1. Generate performance report
python scripts/generate_performance_report.py --output reports/weekly_$(date +%Y%m%d).pdf

# 2. Analyze metrics trends
python scripts/analyze_trends.py --weeks 4

# 3. Optimize database
sqlite3 data/trading_bot.db << 'EOF'
ANALYZE;
REINDEX;
VACUUM;
EOF

# 4. Review and tune alerts
python scripts/review_alerts.py --threshold 0.1

# 5. Backup metrics data
sqlite3 data/trading_bot.db ".dump performance_metrics" > backups/metrics_$(date +%Y%m%d).sql

echo "=== Weekly Maintenance Complete ==="
```

### Performance Optimization

```python
# optimization.py
class PerformanceOptimizer:
    """Optimize system performance based on metrics."""
    
    async def optimize_task_queue(self, monitor):
        """Optimize task queue parameters."""
        metrics = await monitor.get_summary()
        
        avg_wait_time = metrics['tasks']['avg_waiting_time_ms']
        pending_count = metrics['tasks']['pending']
        
        if avg_wait_time > 1000 and pending_count > 100:
            # Increase worker threads
            await self._increase_workers()
        elif avg_wait_time < 100 and pending_count < 10:
            # Decrease worker threads to save resources
            await self._decrease_workers()
    
    async def optimize_database(self, monitor):
        """Optimize database performance."""
        query_times = await monitor.metrics_collector.get_histogram("database.query.time")
        
        avg_query_time = sum(query_times) / len(query_times)
        
        if avg_query_time > 50:  # ms
            # Add indexes or optimize queries
            await self._optimize_queries()
    
    async def optimize_memory(self, monitor):
        """Optimize memory usage."""
        memory_usage = await monitor.metrics_collector.get_current_value("system.memory.usage")
        
        if memory_usage > 80:
            # Clear caches
            await self._clear_caches()
            # Trigger garbage collection
            import gc
            gc.collect()
```

---

## Troubleshooting

### Common Monitoring Issues

#### Issue: Metrics not being collected

```bash
# Check if monitoring is enabled
grep ENABLE_PERFORMANCE_MONITORING .env

# Verify monitor is running
curl http://localhost:8000/api/metrics/system

# Check logs for errors
grep -i "monitor\|metric" logs/trading-bot.log
```

#### Issue: Alerts not firing

```bash
# Check alert configuration
curl http://localhost:8000/api/alerts

# Verify alert thresholds
python -c "
from performance_monitor import get_performance_monitor
import asyncio

async def check():
    monitor = await get_performance_monitor()
    alerts = await monitor.alert_manager.list_alerts()
    for alert in alerts:
        print(f'{alert.name}: {alert.threshold}')

asyncio.run(check())
"
```

#### Issue: High metric storage usage

```bash
# Check database size
ls -lh data/trading_bot.db

# Reduce retention period
sed -i 's/METRICS_RETENTION_HOURS=24/METRICS_RETENTION_HOURS=12/' .env

# Clean old metrics
sqlite3 data/trading_bot.db "DELETE FROM performance_metrics WHERE timestamp < datetime('now', '-1 day');"
```

### Performance Diagnostics

```python
# diagnose_performance.py
import asyncio
from performance_monitor import get_performance_monitor

async def diagnose():
    """Run performance diagnostics."""
    monitor = await get_performance_monitor()
    
    print("=== Performance Diagnostics ===")
    
    # System metrics
    summary = await monitor.get_summary()
    
    print(f"\nCPU Usage: {summary['system']['cpu_percent']}%")
    if summary['system']['cpu_percent'] > 80:
        print("  ⚠️  High CPU usage detected")
    
    print(f"Memory Usage: {summary['system']['memory_percent']}%")
    if summary['system']['memory_percent'] > 85:
        print("  ⚠️  High memory usage detected")
    
    # Task metrics
    print(f"\nPending Tasks: {summary['tasks']['pending']}")
    print(f"Avg Task Time: {summary['tasks']['avg_processing_time']}ms")
    
    if summary['tasks']['avg_processing_time'] > 5000:
        print("  ⚠️  Slow task processing detected")
    
    # Trading metrics
    print(f"\nTotal Trades: {summary['trading']['total_trades']}")
    print(f"Win Rate: {summary['trading']['win_rate']}%")
    print(f"Total P&L: ${summary['trading']['total_pnl']:.2f}")
    
    # Active alerts
    alerts = await monitor.alert_manager.get_active_alerts()
    if alerts:
        print(f"\n⚠️  {len(alerts)} Active Alerts:")
        for alert in alerts:
            print(f"  - {alert.name} ({alert.severity.value})")
    else:
        print("\n✅ No active alerts")

if __name__ == "__main__":
    asyncio.run(diagnose())
```

---

**For additional support, see [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) and [API_DOCUMENTATION.md](./API_DOCUMENTATION.md).**
