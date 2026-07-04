"""
Data Quality Monitoring Dashboard for Trading Bot.

Tracks data quality metrics, monitors data freshness, alerts on gaps
or anomalies, and provides data health reports. Integrates with
Prometheus metrics for production monitoring.

Usage:
    from trading_bot_v2.data_monitoring import DataMonitor

    monitor = DataMonitor()
    monitor.start()  # starts background monitoring thread

    # Get health report
    report = monitor.get_health_report()

    # Get data quality metrics
    metrics = monitor.get_quality_metrics()

    # Manual freshness check
    freshness = monitor.check_freshness("BTC-USDC")

Environment Variables:
    MONITOR_FRESHNESS_CHECK_INTERVAL  = int seconds (default: 60)
    MONITOR_STALENESS_ALERT_SECONDS   = int (default: 300)
    MONITOR_GAP_ALERT_ENABLED         = "true" | "false" (default: "true")
    MONITOR_ANOMALY_ZSCORE_THRESHOLD  = float (default: 3.0)
    MONITOR_METRICS_RETENTION_HOURS   = int (default: 72)
"""

import logging
import os
import statistics
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from .database import get_db_connection, is_postgres, get_backend
from .event_system import get_event_bus, EventType, Event

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

FRESHNESS_CHECK_INTERVAL: int = int(
    os.getenv("MONITOR_FRESHNESS_CHECK_INTERVAL", "60")
)
STALENESS_ALERT_SECONDS: int = int(
    os.getenv("MONITOR_STALENESS_ALERT_SECONDS", "300")
)
GAP_ALERT_ENABLED: bool = os.getenv("MONITOR_GAP_ALERT_ENABLED", "true").lower() in (
    "true",
    "1",
    "yes",
)
ANOMALY_ZSCORE_THRESHOLD: float = float(
    os.getenv("MONITOR_ANOMALY_ZSCORE_THRESHOLD", "3.0")
)
METRICS_RETENTION_HOURS: int = int(
    os.getenv("MONITOR_METRICS_RETENTION_HOURS", "72")
)


class HealthStatus(str, Enum):
    """Component health status levels."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ============================================================================
# Data Structures
# ============================================================================


@dataclass
class DataAlert:
    """Represents a data quality alert."""

    alert_id: str
    severity: AlertSeverity
    category: str
    message: str
    symbol: Optional[str] = None
    table: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "severity": self.severity.value,
            "category": self.category,
            "message": self.message,
            "symbol": self.symbol,
            "table": self.table,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
            "acknowledged": self.acknowledged,
        }


@dataclass
class FreshnessStatus:
    """Data freshness status for a symbol/table combination."""

    symbol: str
    table: str
    latest_timestamp: Optional[datetime] = None
    staleness_seconds: float = 0.0
    status: HealthStatus = HealthStatus.UNKNOWN
    expected_interval_seconds: int = 60
    last_gap_seconds: float = 0.0
    gap_count_24h: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "table": self.table,
            "latest_timestamp": (
                self.latest_timestamp.isoformat()
                if self.latest_timestamp
                else None
            ),
            "staleness_seconds": round(self.staleness_seconds, 1),
            "status": self.status.value,
            "expected_interval_seconds": self.expected_interval_seconds,
            "last_gap_seconds": round(self.last_gap_seconds, 1),
            "gap_count_24h": self.gap_count_24h,
        }


@dataclass
class QualityMetrics:
    """Data quality metrics snapshot."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    total_records: int = 0
    records_last_hour: int = 0
    records_last_24h: int = 0
    gap_count: int = 0
    anomaly_count: int = 0
    validation_error_count: int = 0
    freshness_score: float = 1.0  # 0.0 (stale) to 1.0 (fresh)
    completeness_score: float = 1.0  # 0.0 (gaps) to 1.0 (complete)
    accuracy_score: float = 1.0  # 0.0 (many errors) to 1.0 (clean)
    overall_score: float = 1.0  # weighted average

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "total_records": self.total_records,
            "records_last_hour": self.records_last_hour,
            "records_last_24h": self.records_last_24h,
            "gap_count": self.gap_count,
            "anomaly_count": self.anomaly_count,
            "validation_error_count": self.validation_error_count,
            "freshness_score": round(self.freshness_score, 4),
            "completeness_score": round(self.completeness_score, 4),
            "accuracy_score": round(self.accuracy_score, 4),
            "overall_score": round(self.overall_score, 4),
        }


@dataclass
class HealthReport:
    """Comprehensive data health report."""

    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    overall_status: HealthStatus = HealthStatus.UNKNOWN
    freshness: List[FreshnessStatus] = field(default_factory=list)
    quality_metrics: Optional[QualityMetrics] = None
    active_alerts: List[DataAlert] = field(default_factory=list)
    table_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "overall_status": self.overall_status.value,
            "freshness": [f.to_dict() for f in self.freshness],
            "quality_metrics": (
                self.quality_metrics.to_dict() if self.quality_metrics else None
            ),
            "active_alerts": [a.to_dict() for a in self.active_alerts[:20]],
            "table_stats": self.table_stats,
            "recommendations": self.recommendations,
        }

    def summary(self) -> str:
        """Human-readable summary."""
        status_emoji = {
            HealthStatus.HEALTHY: "OK",
            HealthStatus.DEGRADED: "WARN",
            HealthStatus.UNHEALTHY: "FAIL",
            HealthStatus.UNKNOWN: "???",
        }
        status_str = status_emoji.get(self.overall_status, "???")
        alert_count = len(self.active_alerts)
        q = self.quality_metrics
        score = q.overall_score if q else 0.0
        return (
            f"Data Health: {status_str} "
            f"(score={score:.2f}, alerts={alert_count}, "
            f"freshness_checks={len(self.freshness)})"
        )


# ============================================================================
# Prometheus Metrics Integration
# ============================================================================

# Cache of already-registered collector attributes, keyed by id(registry).
# Prevents "Duplicated timeseries in CollectorRegistry" when
# DataQualityPrometheusMetrics is instantiated more than once against the
# same registry (e.g. the global default REGISTRY in tests).
_DATA_QUALITY_METRICS_CACHE: Dict[int, Dict[str, Any]] = {}


class DataQualityPrometheusMetrics:
    """Prometheus metrics for data quality monitoring.

    Collectors are cached at module level keyed by the target registry so
    that repeated instantiation (e.g. across tests) reuses the existing
    collectors instead of raising ``ValueError: Duplicated timeseries`` on
    the default ``prometheus_client`` REGISTRY.
    """

    def __init__(self, registry: Optional[Any] = None) -> None:
        """Initialize Prometheus metrics. Graceful fallback if unavailable.

        Args:
            registry: Optional prometheus_client CollectorRegistry. Defaults
                to the global REGISTRY when not provided.
        """
        try:
            from prometheus_client import REGISTRY, Counter, Gauge, Histogram

            target_registry = registry if registry is not None else REGISTRY
            cached = _DATA_QUALITY_METRICS_CACHE.get(id(target_registry))
            if cached is not None:
                self.__dict__.update(cached)
                self._available = True
                return

            self.data_records_total = Counter(
                "bot_data_records_total",
                "Total data records ingested",
                ["table", "symbol"],
                registry=target_registry,
            )
            self.data_freshness_seconds = Gauge(
                "bot_data_freshness_seconds",
                "Data staleness in seconds",
                ["symbol", "table"],
                registry=target_registry,
            )
            self.data_quality_score = Gauge(
                "bot_data_quality_score",
                "Data quality score (0-1)",
                ["metric_type"],
                registry=target_registry,
            )
            self.data_gaps_total = Counter(
                "bot_data_gaps_total",
                "Total data gaps detected",
                ["symbol", "table"],
                registry=target_registry,
            )
            self.data_anomalies_total = Counter(
                "bot_data_anomalies_total",
                "Total data anomalies detected",
                ["symbol", "anomaly_type"],
                registry=target_registry,
            )
            self.data_validation_errors_total = Counter(
                "bot_data_validation_errors_total",
                "Total validation errors",
                ["table", "error_code"],
                registry=target_registry,
            )
            self.data_alerts_total = Counter(
                "bot_data_alerts_total",
                "Total data alerts generated",
                ["severity", "category"],
                registry=target_registry,
            )
            self.archival_rows_deleted_total = Counter(
                "bot_archival_rows_deleted_total",
                "Total rows deleted during archival",
                ["table"],
                registry=target_registry,
            )
            self._available = True

            _DATA_QUALITY_METRICS_CACHE[id(target_registry)] = {
                "data_records_total": self.data_records_total,
                "data_freshness_seconds": self.data_freshness_seconds,
                "data_quality_score": self.data_quality_score,
                "data_gaps_total": self.data_gaps_total,
                "data_anomalies_total": self.data_anomalies_total,
                "data_validation_errors_total": self.data_validation_errors_total,
                "data_alerts_total": self.data_alerts_total,
                "archival_rows_deleted_total": self.archival_rows_deleted_total,
            }
        except ImportError:
            logger.debug("prometheus_client not available, metrics disabled")
            self._available = False

    def record_freshness(
        self, symbol: str, table: str, staleness: float
    ) -> None:
        if self._available:
            self.data_freshness_seconds.labels(
                symbol=symbol, table=table
            ).set(staleness)

    def record_quality_score(self, metric_type: str, score: float) -> None:
        if self._available:
            self.data_quality_score.labels(metric_type=metric_type).set(score)

    def record_gap(self, symbol: str, table: str) -> None:
        if self._available:
            self.data_gaps_total.labels(symbol=symbol, table=table).inc()

    def record_anomaly(self, symbol: str, anomaly_type: str) -> None:
        if self._available:
            self.data_anomalies_total.labels(
                symbol=symbol, anomaly_type=anomaly_type
            ).inc()

    def record_alert(self, severity: str, category: str) -> None:
        if self._available:
            self.data_alerts_total.labels(
                severity=severity, category=category
            ).inc()


# ============================================================================
# Anomaly Detector
# ============================================================================


class AnomalyDetector:
    """
    Detects anomalies in time-series data using statistical methods.

    Methods:
      - Z-score based outlier detection
      - Sudden volume spikes
      - Price jump detection
      - Missing data pattern analysis
    """

    def __init__(
        self,
        zscore_threshold: float = ANOMALY_ZSCORE_THRESHOLD,
        window_size: int = 100,
    ) -> None:
        self.zscore_threshold = zscore_threshold
        self.window_size = window_size
        self._price_history: Dict[str, deque] = {}
        self._volume_history: Dict[str, deque] = {}

    def detect_price_anomaly(
        self, symbol: str, price: float
    ) -> Optional[Dict[str, Any]]:
        """
        Detect if a price is anomalous compared to recent history.

        Args:
            symbol: Trading pair symbol
            price: Current price to check

        Returns:
            Anomaly details dict if anomalous, None otherwise
        """
        if symbol not in self._price_history:
            self._price_history[symbol] = deque(maxlen=self.window_size)

        history = self._price_history[symbol]

        if len(history) < 10:
            history.append(price)
            return None

        mean = statistics.mean(history)
        stdev = statistics.stdev(history) if len(history) > 1 else 0.0

        if stdev == 0:
            history.append(price)
            return None

        zscore = abs(price - mean) / stdev

        history.append(price)

        if zscore > self.zscore_threshold:
            return {
                "type": "price_spike",
                "symbol": symbol,
                "price": price,
                "mean": round(mean, 6),
                "stdev": round(stdev, 6),
                "zscore": round(zscore, 2),
                "threshold": self.zscore_threshold,
            }

        return None

    def detect_volume_anomaly(
        self, symbol: str, volume: float
    ) -> Optional[Dict[str, Any]]:
        """
        Detect if a volume reading is anomalous.

        Args:
            symbol: Trading pair symbol
            volume: Current volume to check

        Returns:
            Anomaly details dict if anomalous, None otherwise
        """
        if symbol not in self._volume_history:
            self._volume_history[symbol] = deque(maxlen=self.window_size)

        history = self._volume_history[symbol]

        if len(history) < 10:
            history.append(volume)
            return None

        mean = statistics.mean(history)
        stdev = statistics.stdev(history) if len(history) > 1 else 0.0

        if stdev == 0:
            history.append(volume)
            return None

        zscore = abs(volume - mean) / stdev

        history.append(volume)

        if zscore > self.zscore_threshold:
            direction = "spike" if volume > mean else "drop"
            return {
                "type": f"volume_{direction}",
                "symbol": symbol,
                "volume": volume,
                "mean": round(mean, 4),
                "stdev": round(stdev, 4),
                "zscore": round(zscore, 2),
                "threshold": self.zscore_threshold,
            }

        return None

    def detect_price_jump(
        self,
        symbol: str,
        prev_price: float,
        curr_price: float,
        max_jump_pct: float = 0.10,
    ) -> Optional[Dict[str, Any]]:
        """
        Detect sudden price jumps between consecutive candles.

        Args:
            symbol: Trading pair symbol
            prev_price: Previous close price
            curr_price: Current close price
            max_jump_pct: Maximum acceptable price jump (default 10%)

        Returns:
            Anomaly details dict if jump detected, None otherwise
        """
        if prev_price <= 0:
            return None

        jump_pct = abs(curr_price - prev_price) / prev_price

        if jump_pct > max_jump_pct:
            direction = "up" if curr_price > prev_price else "down"
            return {
                "type": "price_jump",
                "symbol": symbol,
                "prev_price": prev_price,
                "curr_price": curr_price,
                "jump_pct": round(jump_pct * 100, 2),
                "direction": direction,
                "threshold_pct": round(max_jump_pct * 100, 2),
            }

        return None

    def clear_history(self, symbol: Optional[str] = None) -> None:
        """Clear detection history for a symbol or all symbols."""
        if symbol:
            self._price_history.pop(symbol, None)
            self._volume_history.pop(symbol, None)
        else:
            self._price_history.clear()
            self._volume_history.clear()


# ============================================================================
# Data Monitor
# ============================================================================


class DataMonitor:
    """
    Comprehensive data quality monitoring system.

    Provides:
      - Background freshness monitoring with configurable intervals
      - Anomaly detection with statistical methods
      - Alert generation and management
      - Prometheus metrics integration
      - Health report generation
      - Gap detection and alerting

    Usage:
        monitor = DataMonitor()
        monitor.start()

        # Get health report
        report = monitor.get_health_report()

        # Acknowledge an alert
        monitor.acknowledge_alert("alert_id_123")
    """

    def __init__(self) -> None:
        self.event_bus = get_event_bus()
        self.prometheus = DataQualityPrometheusMetrics()
        self.anomaly_detector = AnomalyDetector()

        # State
        self._alerts: Dict[str, DataAlert] = {}
        self._alert_counter: int = 0
        self._freshness_cache: Dict[str, FreshnessStatus] = {}
        self._quality_history: deque = deque(
            maxlen=METRICS_RETENTION_HOURS * 60
        )  # 1 per minute

        # Configuration
        self._running: bool = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Tracked symbols (populated from database)
        self._tracked_symbols: List[str] = []
        self._tracked_tables: List[str] = [
            "market_data",
            "trades",
            "signals",
            "funding_payments",
        ]

        logger.info(
            f"DataMonitor initialized (interval={FRESHNESS_CHECK_INTERVAL}s, "
            f"staleness_threshold={STALENESS_ALERT_SECONDS}s)"
        )

    def start(self) -> None:
        """Start the background monitoring thread."""
        if self._running:
            logger.warning("DataMonitor is already running")
            return

        self._running = True
        self._thread = threading.Thread(
            target=self._monitoring_loop,
            name="data-monitor",
            daemon=True,
        )
        self._thread.start()
        logger.info("DataMonitor started")

    def stop(self) -> None:
        """Stop the background monitoring thread."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        logger.info("DataMonitor stopped")

    def _monitoring_loop(self) -> None:
        """Main monitoring loop running in background thread."""
        while self._running:
            try:
                self._refresh_tracked_symbols()
                self._check_freshness_all()
                self._check_for_anomalies()
                self._cleanup_old_alerts()
                self._compute_quality_metrics()
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}", exc_info=True)

            time.sleep(FRESHNESS_CHECK_INTERVAL)

    # ========================================================================
    # Freshness Monitoring
    # ========================================================================

    def _refresh_tracked_symbols(self) -> None:
        """Refresh the list of tracked symbols from the database."""
        try:
            with get_db_connection() as conn:
                if is_postgres():
                    rows = conn.execute(
                        """
                        SELECT DISTINCT symbol FROM market_data
                        WHERE "timestamp" >= NOW() - INTERVAL '7 days'
                        ORDER BY symbol
                        """
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT DISTINCT symbol FROM market_data
                        WHERE timestamp >= datetime('now', '-7 days')
                        ORDER BY symbol
                        """
                    ).fetchall()

                self._tracked_symbols = [
                    row[0] if not isinstance(row, dict) else row.get("symbol", "")
                    for row in (rows or [])
                ]
        except Exception as e:
            logger.debug(f"Could not refresh tracked symbols: {e}")

    def _check_freshness_all(self) -> None:
        """Check freshness for all tracked symbols."""
        for symbol in self._tracked_symbols:
            try:
                status = self.check_freshness(symbol)
                self._freshness_cache[symbol] = status

                # Publish Prometheus metric
                self.prometheus.record_freshness(
                    symbol, "market_data", status.staleness_seconds
                )

                # Generate alert if stale
                if status.status == HealthStatus.UNHEALTHY:
                    self._create_alert(
                        severity=AlertSeverity.WARNING,
                        category="freshness",
                        message=(
                            f"Data for {symbol} is stale "
                            f"({status.staleness_seconds:.0f}s, "
                            f"threshold={STALENESS_ALERT_SECONDS}s)"
                        ),
                        symbol=symbol,
                        details=status.to_dict(),
                    )
            except Exception as e:
                logger.debug(f"Freshness check failed for {symbol}: {e}")

    def check_freshness(
        self,
        symbol: str,
        table: str = "market_data",
        threshold_seconds: int = STALENESS_ALERT_SECONDS,
    ) -> FreshnessStatus:
        """
        Check data freshness for a specific symbol.

        Args:
            symbol: Trading pair symbol
            table: Table to check
            threshold_seconds: Staleness threshold for unhealthy status

        Returns:
            FreshnessStatus with current freshness information
        """
        status = FreshnessStatus(symbol=symbol, table=table)

        try:
            now = datetime.now(timezone.utc)

            with get_db_connection() as conn:
                if is_postgres():
                    row = conn.execute(
                        f"""
                        SELECT MAX("timestamp") as latest
                        FROM {table}
                        WHERE symbol = %s
                        """,
                        (symbol,),
                    ).fetchone()
                else:
                    row = conn.execute(
                        f"""
                        SELECT MAX(timestamp) as latest
                        FROM {table}
                        WHERE symbol = ?
                        """,
                        (symbol,),
                    ).fetchone()

                if row:
                    latest = row[0] if not isinstance(row, dict) else row.get("latest")
                    if latest is not None:
                        if isinstance(latest, datetime):
                            if latest.tzinfo is None:
                                latest = latest.replace(tzinfo=timezone.utc)
                            status.latest_timestamp = latest
                        elif isinstance(latest, str):
                            try:
                                dt = datetime.fromisoformat(
                                    latest.replace("Z", "+00:00")
                                )
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=timezone.utc)
                                status.latest_timestamp = dt
                            except (ValueError, TypeError):
                                pass

                        if status.latest_timestamp:
                            status.staleness_seconds = (
                                now - status.latest_timestamp
                            ).total_seconds()

                            if status.staleness_seconds <= threshold_seconds:
                                status.status = HealthStatus.HEALTHY
                            elif status.staleness_seconds <= threshold_seconds * 3:
                                status.status = HealthStatus.DEGRADED
                            else:
                                status.status = HealthStatus.UNHEALTHY

                # Count gaps in last 24h
                gap_count = self._count_gaps(symbol, table, hours=24)
                status.gap_count_24h = gap_count

        except Exception as e:
            status.status = HealthStatus.UNKNOWN
            logger.debug(f"Freshness check error for {symbol}: {e}")

        return status

    def _count_gaps(
        self, symbol: str, table: str, hours: int = 24
    ) -> int:
        """Count the number of gaps in data for the given time window."""
        try:
            with get_db_connection() as conn:
                if is_postgres():
                    rows = conn.execute(
                        f"""
                        SELECT "timestamp" FROM {table}
                        WHERE symbol = %s
                          AND "timestamp" >= NOW() - make_interval(hours => %s)
                        ORDER BY "timestamp" ASC
                        """,
                        (symbol, hours),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"""
                        SELECT timestamp FROM {table}
                        WHERE symbol = ?
                          AND timestamp >= datetime('now', ?)
                        ORDER BY timestamp ASC
                        """,
                        (symbol, f"-{hours} hours"),
                    ).fetchall()

                if not rows or len(rows) < 2:
                    return 0

                gap_count = 0
                for i in range(1, len(rows)):
                    prev_ts = rows[i - 1][0] if not isinstance(rows[i - 1], dict) else rows[i - 1].get("timestamp")
                    curr_ts = rows[i][0] if not isinstance(rows[i], dict) else rows[i].get("timestamp")

                    if prev_ts is None or curr_ts is None:
                        continue

                    try:
                        if isinstance(prev_ts, str):
                            prev_dt = datetime.fromisoformat(prev_ts.replace("Z", "+00:00"))
                        elif isinstance(prev_ts, datetime):
                            prev_dt = prev_ts
                        else:
                            continue

                        if isinstance(curr_ts, str):
                            curr_dt = datetime.fromisoformat(curr_ts.replace("Z", "+00:00"))
                        elif isinstance(curr_ts, datetime):
                            curr_dt = curr_ts
                        else:
                            continue

                        diff = (curr_dt - prev_dt).total_seconds()
                        if diff > 180:  # More than 3 minutes gap
                            gap_count += 1
                    except (TypeError, ValueError):
                        continue

                return gap_count

        except Exception:
            return 0

    # ========================================================================
    # Anomaly Detection
    # ========================================================================

    def _check_for_anomalies(self) -> None:
        """Run anomaly detection on recent data."""
        try:
            with get_db_connection() as conn:
                for symbol in self._tracked_symbols[:20]:  # Limit to avoid overload
                    if is_postgres():
                        row = conn.execute(
                            """
                            SELECT close, volume FROM market_data
                            WHERE symbol = %s
                            ORDER BY "timestamp" DESC
                            LIMIT 1
                            """,
                            (symbol,),
                        ).fetchone()
                    else:
                        row = conn.execute(
                            """
                            SELECT close, volume FROM market_data
                            WHERE symbol = ?
                            ORDER BY timestamp DESC
                            LIMIT 1
                            """,
                            (symbol,),
                        ).fetchone()

                    if row:
                        close_val = row[0] if not isinstance(row, dict) else row.get("close")
                        vol_val = row[1] if not isinstance(row, dict) else row.get("volume")

                        if close_val is not None:
                            price_anomaly = self.anomaly_detector.detect_price_anomaly(
                                symbol, float(close_val)
                            )
                            if price_anomaly:
                                self.prometheus.record_anomaly(
                                    symbol, price_anomaly["type"]
                                )
                                self._create_alert(
                                    severity=AlertSeverity.WARNING,
                                    category="anomaly",
                                    message=(
                                        f"Price anomaly for {symbol}: "
                                        f"zscore={price_anomaly['zscore']}"
                                    ),
                                    symbol=symbol,
                                    details=price_anomaly,
                                )

                        if vol_val is not None:
                            vol_anomaly = self.anomaly_detector.detect_volume_anomaly(
                                symbol, float(vol_val)
                            )
                            if vol_anomaly:
                                self.prometheus.record_anomaly(
                                    symbol, vol_anomaly["type"]
                                )
                                self._create_alert(
                                    severity=AlertSeverity.INFO,
                                    category="anomaly",
                                    message=(
                                        f"Volume anomaly for {symbol}: "
                                        f"zscore={vol_anomaly['zscore']}"
                                    ),
                                    symbol=symbol,
                                    details=vol_anomaly,
                                )

        except Exception as e:
            logger.debug(f"Anomaly detection error: {e}")

    # ========================================================================
    # Alert Management
    # ========================================================================

    def _create_alert(
        self,
        severity: AlertSeverity,
        category: str,
        message: str,
        symbol: Optional[str] = None,
        table: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> DataAlert:
        """Create and store a new alert."""
        self._alert_counter += 1
        alert_id = f"alert_{self._alert_counter}_{int(time.time())}"

        alert = DataAlert(
            alert_id=alert_id,
            severity=severity,
            category=category,
            message=message,
            symbol=symbol,
            table=table,
            details=details or {},
        )

        with self._lock:
            self._alerts[alert_id] = alert

        # Publish to Prometheus
        self.prometheus.record_alert(severity.value, category)

        # Publish to EventBus
        try:
            self.event_bus.publish_event(
                EventType.COMPONENT_FAILURE
                if severity in (AlertSeverity.ERROR, AlertSeverity.CRITICAL)
                else EventType.COMPONENT_HEALTH_CHECK,
                {
                    "source": "data_monitor",
                    "alert": alert.to_dict(),
                },
                "DataMonitor",
            )
        except Exception as e:
            logger.debug(f"Failed to publish alert event: {e}")

        logger.log(
            logging.WARNING if severity in (AlertSeverity.WARNING, AlertSeverity.ERROR)
            else logging.INFO,
            f"Data alert [{severity.value}]: {message}",
        )

        return alert

    def _cleanup_old_alerts(self) -> None:
        """Remove alerts older than 24 hours."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        with self._lock:
            old_ids = [
                aid
                for aid, alert in self._alerts.items()
                if alert.timestamp < cutoff
            ]
            for aid in old_ids:
                del self._alerts[aid]

    def acknowledge_alert(self, alert_id: str) -> bool:
        """Acknowledge an alert by ID."""
        with self._lock:
            if alert_id in self._alerts:
                self._alerts[alert_id].acknowledged = True
                return True
        return False

    def get_active_alerts(
        self,
        severity: Optional[AlertSeverity] = None,
        limit: int = 50,
    ) -> List[DataAlert]:
        """Get active (unacknowledged) alerts."""
        with self._lock:
            alerts = list(self._alerts.values())

        if severity:
            alerts = [a for a in alerts if a.severity == severity]

        alerts = [a for a in alerts if not a.acknowledged]
        alerts.sort(key=lambda a: a.timestamp, reverse=True)
        return alerts[:limit]

    # ========================================================================
    # Quality Metrics
    # ========================================================================

    def _compute_quality_metrics(self) -> None:
        """Compute and store current quality metrics snapshot."""
        metrics = QualityMetrics()

        try:
            with get_db_connection() as conn:
                # Total records
                if is_postgres():
                    row = conn.execute(
                        "SELECT count(*) FROM market_data"
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT count(*) FROM market_data"
                    ).fetchone()
                metrics.total_records = row[0] if row else 0

                # Records last hour
                if is_postgres():
                    row = conn.execute(
                        """
                        SELECT count(*) FROM market_data
                        WHERE "timestamp" >= NOW() - INTERVAL '1 hour'
                        """
                    ).fetchone()
                else:
                    row = conn.execute(
                        """
                        SELECT count(*) FROM market_data
                        WHERE timestamp >= datetime('now', '-1 hours')
                        """
                    ).fetchone()
                metrics.records_last_hour = row[0] if row else 0

                # Records last 24h
                if is_postgres():
                    row = conn.execute(
                        """
                        SELECT count(*) FROM market_data
                        WHERE "timestamp" >= NOW() - INTERVAL '24 hours'
                        """
                    ).fetchone()
                else:
                    row = conn.execute(
                        """
                        SELECT count(*) FROM market_data
                        WHERE timestamp >= datetime('now', '-24 hours')
                        """
                    ).fetchone()
                metrics.records_last_24h = row[0] if row else 0

        except Exception as e:
            logger.debug(f"Quality metrics computation error: {e}")

        # Count gaps
        total_gaps = sum(
            fs.gap_count_24h for fs in self._freshness_cache.values()
        )
        metrics.gap_count = total_gaps

        # Count anomalies
        recent_alerts = [
            a
            for a in self._alerts.values()
            if a.category == "anomaly"
            and a.timestamp > datetime.now(timezone.utc) - timedelta(hours=24)
        ]
        metrics.anomaly_count = len(recent_alerts)

        # Compute scores
        # Freshness: based on worst staleness
        if self._freshness_cache:
            worst_staleness = max(
                fs.staleness_seconds for fs in self._freshness_cache.values()
            )
            metrics.freshness_score = max(
                0.0,
                1.0 - (worst_staleness / (STALENESS_ALERT_SECONDS * 10)),
            )
        else:
            metrics.freshness_score = 1.0

        # Completeness: based on gap count
        if metrics.records_last_24h > 0:
            expected_records = 24 * 60  # 1 per minute for 24h
            metrics.completeness_score = max(
                0.0,
                1.0 - (total_gaps / max(expected_records, 1)),
            )
        else:
            metrics.completeness_score = 0.0

        # Accuracy: based on validation errors
        validation_errors = [
            a
            for a in self._alerts.values()
            if a.category == "validation"
            and a.timestamp > datetime.now(timezone.utc) - timedelta(hours=24)
        ]
        metrics.validation_error_count = len(validation_errors)
        if metrics.records_last_24h > 0:
            metrics.accuracy_score = max(
                0.0,
                1.0 - (metrics.validation_error_count / metrics.records_last_24h),
            )
        else:
            metrics.accuracy_score = 1.0

        # Overall score (weighted average)
        metrics.overall_score = (
            0.4 * metrics.freshness_score
            + 0.3 * metrics.completeness_score
            + 0.3 * metrics.accuracy_score
        )

        # Publish to Prometheus
        self.prometheus.record_quality_score("freshness", metrics.freshness_score)
        self.prometheus.record_quality_score("completeness", metrics.completeness_score)
        self.prometheus.record_quality_score("accuracy", metrics.accuracy_score)
        self.prometheus.record_quality_score("overall", metrics.overall_score)

        # Store in history
        self._quality_history.append(metrics)

    # ========================================================================
    # Public API
    # ========================================================================

    def get_quality_metrics(self) -> QualityMetrics:
        """Get the most recent quality metrics snapshot."""
        if self._quality_history:
            return self._quality_history[-1]
        return QualityMetrics()

    def get_quality_history(
        self, hours: int = 24
    ) -> List[QualityMetrics]:
        """Get quality metrics history for the specified time window."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        return [
            m for m in self._quality_history if m.timestamp >= cutoff
        ]

    def get_health_report(self) -> HealthReport:
        """
        Generate a comprehensive data health report.

        Returns:
            HealthReport with all monitoring data
        """
        report = HealthReport()

        # Freshness
        for symbol in self._tracked_symbols:
            if symbol in self._freshness_cache:
                report.freshness.append(self._freshness_cache[symbol])
            else:
                status = self.check_freshness(symbol)
                report.freshness.append(status)

        # Quality metrics
        report.quality_metrics = self.get_quality_metrics()

        # Active alerts
        report.active_alerts = self.get_active_alerts()

        # Table stats
        report.table_stats = self._get_table_stats()

        # Compute overall status
        unhealthy_count = sum(
            1 for f in report.freshness if f.status == HealthStatus.UNHEALTHY
        )
        degraded_count = sum(
            1 for f in report.freshness if f.status == HealthStatus.DEGRADED
        )

        if unhealthy_count > 0:
            report.overall_status = HealthStatus.UNHEALTHY
        elif degraded_count > 0:
            report.overall_status = HealthStatus.DEGRADED
        elif report.freshness:
            report.overall_status = HealthStatus.HEALTHY
        else:
            report.overall_status = HealthStatus.UNKNOWN

        # Recommendations
        report.recommendations = self._generate_recommendations(report)

        return report

    def _get_table_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get row counts and size info for tracked tables."""
        stats: Dict[str, Dict[str, Any]] = {}

        try:
            with get_db_connection() as conn:
                for table in self._tracked_tables:
                    try:
                        if is_postgres():
                            row = conn.execute(
                                f"SELECT count(*) FROM {table}"
                            ).fetchone()
                        else:
                            row = conn.execute(
                                f"SELECT count(*) FROM {table}"
                            ).fetchone()
                        stats[table] = {"row_count": row[0] if row else 0}
                    except Exception:
                        stats[table] = {"row_count": 0, "error": "table not found"}
        except Exception:
            pass

        return stats

    def _generate_recommendations(
        self, report: HealthReport
    ) -> List[str]:
        """Generate actionable recommendations from the health report."""
        recommendations: List[str] = []

        if report.overall_status == HealthStatus.UNHEALTHY:
            recommendations.append(
                "URGENT: One or more symbols have stale data. "
                "Check data ingestion pipeline."
            )

        # Check for high gap counts
        total_gaps = sum(f.gap_count_24h for f in report.freshness)
        if total_gaps > 10:
            recommendations.append(
                f"High gap count detected ({total_gaps} in 24h). "
                "Review data source connectivity."
            )

        # Check quality score
        if report.quality_metrics:
            if report.quality_metrics.overall_score < 0.7:
                recommendations.append(
                    f"Data quality score is low "
                    f"({report.quality_metrics.overall_score:.2f}). "
                    "Investigate validation errors and gaps."
                )
            if report.quality_metrics.freshness_score < 0.8:
                recommendations.append(
                    "Data freshness is degraded. "
                    "Consider increasing data fetch frequency."
                )

        # Alert-based recommendations
        error_alerts = [
            a for a in report.active_alerts if a.severity == AlertSeverity.ERROR
        ]
        if error_alerts:
            recommendations.append(
                f"{len(error_alerts)} error-level alerts active. "
                "Review and address data quality issues."
            )

        if not recommendations:
            recommendations.append("All data quality checks passing.")

        return recommendations

    def _get_time_column(self, table_name: str) -> Optional[str]:
        """Get the primary time column for a table."""
        time_columns = {
            "market_data": "timestamp",
            "signals": "timestamp",
            "trades": "entry_time",
            "funding_payments": "timestamp",
            "funding_rate_history": "timestamp",
        }
        return time_columns.get(table_name)


# ============================================================================
# Singleton
# ============================================================================

_monitor: Optional[DataMonitor] = None


def get_data_monitor() -> DataMonitor:
    """Get or create the global DataMonitor instance."""
    global _monitor
    if _monitor is None:
        _monitor = DataMonitor()
    return _monitor
