"""
Strategy Monitor - Tracks strategy performance, correlation, and decay.

Provides rolling correlation analysis between strategies, Sharpe ratio
computation, and decay detection to alert when a strategy's performance
degrades significantly from its historical baseline.

Subscribes to SIGNAL_EXECUTED events via EventBus to automatically record
trade returns and trigger analysis.

Usage:
    from trading_bot_v2.strategy_monitor import StrategyMonitor

    monitor = StrategyMonitor()
    monitor.record_return("mean_reversion", 1.5)
    report = monitor.get_health_report()
"""

import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, date, timezone
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
from loguru import logger

from trading_bot_v2.database import get_db_connection, is_postgres
from trading_bot_v2.event_system import Event, EventType, get_event_bus
from trading_bot_v2.history import TradeStore
from trading_bot_v2.history import metrics as history_metrics


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
CORRELATION_ALERT: float = 0.7
DECAY_ALERT_PCT: float = 50.0
ROLLING_DAYS: int = 30
SHARPE_LOOKBACK: int = 90
MIN_TRADES_FOR_SHARPE: int = 20
MIN_TRADES_FOR_CORRELATION: int = 10


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class CorrelationAlert:
    """Raised when two strategies become highly correlated."""

    strategy_a: str
    strategy_b: str
    correlation: float
    window_days: int
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DecayAlert:
    """Raised when a strategy's Sharpe drops significantly below its baseline."""

    strategy: str
    current_sharpe: float
    baseline_sharpe: float
    decay_pct: float
    trade_count: int
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyHealth:
    """Per-strategy health summary."""

    strategy: str
    sharpe_ratio: Optional[float]
    trade_count: int
    win_rate: float
    avg_pnl_pct: float
    total_pnl_pct: float
    profit_factor: Optional[float] = None
    max_drawdown_pct: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HealthReport:
    """Full health report returned by get_health_report()."""

    strategies: Dict[str, StrategyHealth]
    correlation_matrix: Dict[str, Dict[str, Optional[float]]]
    correlation_alerts: List[CorrelationAlert]
    decay_alerts: List[DecayAlert]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategies": {k: v.to_dict() for k, v in self.strategies.items()},
            "correlation_matrix": self.correlation_matrix,
            "correlation_alerts": [a.to_dict() for a in self.correlation_alerts],
            "decay_alerts": [a.to_dict() for a in self.decay_alerts],
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# StrategyMonitor
# ---------------------------------------------------------------------------
class StrategyMonitor:
    """
    Monitors strategy performance, inter-strategy correlation, and decay.

    Thread-safe: all internal state is protected by a lock. Database
    operations use the shared ``get_db_connection()`` context manager.
    """

    def __init__(
        self,
        correlation_threshold: float = CORRELATION_ALERT,
        decay_threshold_pct: float = DECAY_ALERT_PCT,
        rolling_days: int = ROLLING_DAYS,
        sharpe_lookback: int = SHARPE_LOOKBACK,
    ) -> None:
        self._correlation_threshold = correlation_threshold
        self._decay_threshold_pct = decay_threshold_pct
        self._rolling_days = rolling_days
        self._sharpe_lookback = sharpe_lookback
        self._lock = threading.Lock()

        # In-memory cache of returns keyed by strategy name.
        # Each value is a list of (timestamp_str, pnl_pct) tuples, newest last.
        self._returns_cache: Dict[str, List[Tuple[str, float]]] = {}

        # Single owner of trade-history access (regime attribution).
        self._trade_store = TradeStore()

        # Register with EventBus
        self._subscribe_to_events()
        self._ensure_schema()
        logger.info(
            "StrategyMonitor initialised "
            f"(correlation_threshold={correlation_threshold}, "
            f"decay_threshold={decay_threshold_pct}%, "
            f"rolling_days={rolling_days}, sharpe_lookback={sharpe_lookback})"
        )

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------
    def _ensure_schema(self) -> None:
        """Create monitoring tables if they do not exist."""
        import os

        schema_path = os.path.join(
            os.path.dirname(__file__), "schema_strategy_monitoring.sql"
        )
        if not os.path.exists(schema_path):
            logger.warning(
                f"schema_strategy_monitoring.sql not found at {schema_path}; "
                "creating tables inline"
            )
            self._create_tables_inline()
            self._migrate_schema()
            return

        try:
            with open(schema_path, "r", encoding="utf-8") as fh:
                sql = fh.read()
            with get_db_connection() as conn:
                conn.executescript(sql)
                conn.commit()
            logger.debug("Strategy monitoring schema ensured")
        except Exception as exc:
            logger.error(f"Failed to apply strategy monitoring schema: {exc}")

        self._migrate_schema()

    def _migrate_schema(self) -> None:
        """Add columns introduced after the initial schema (idempotent).

        ``CREATE TABLE IF NOT EXISTS`` does not alter pre-existing tables,
        so newer columns are added via ``ALTER TABLE``.  Duplicate-column
        errors (SQLite: "duplicate column name", Postgres: "already
        exists") are expected on re-runs and silently ignored.
        """
        alter_statements = [
            "ALTER TABLE strategy_health_snapshots ADD COLUMN profit_factor REAL",
            "ALTER TABLE strategy_health_snapshots "
            "ADD COLUMN max_drawdown_pct REAL",
        ]
        try:
            with get_db_connection() as conn:
                for alter_sql in alter_statements:
                    try:
                        conn.execute(alter_sql)
                    except Exception as exc:
                        msg = str(exc).lower()
                        if (
                            "duplicate column name" not in msg
                            and "already exists" not in msg
                        ):
                            logger.warning(
                                f"Failed to add health snapshot column: {exc}"
                            )
                conn.commit()
        except Exception as exc:
            logger.error(f"Failed to migrate strategy monitoring schema: {exc}")

    def _create_tables_inline(self) -> None:
        """Fallback: create tables directly without the SQL file."""
        create_sql = """
            CREATE TABLE IF NOT EXISTS strategy_returns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                pnl_pct REAL NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS strategy_correlations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_a TEXT NOT NULL,
                strategy_b TEXT NOT NULL,
                correlation REAL NOT NULL,
                window_days INTEGER DEFAULT 30,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS strategy_health_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                sharpe_ratio REAL,
                trade_count INTEGER,
                win_rate REAL,
                avg_pnl_pct REAL,
                profit_factor REAL,
                max_drawdown_pct REAL,
                snapshot_date DATE NOT NULL,
                UNIQUE(strategy, snapshot_date)
            );
        """
        try:
            with get_db_connection() as conn:
                conn.executescript(create_sql)
                conn.commit()
        except Exception as exc:
            logger.error(f"Failed to create monitoring tables inline: {exc}")

    # ------------------------------------------------------------------
    # EventBus integration
    # ------------------------------------------------------------------
    def _subscribe_to_events(self) -> None:
        """Subscribe to SIGNAL_EXECUTED events on the global EventBus."""
        try:
            bus = get_event_bus()
            bus.subscribe(EventType.SIGNAL_EXECUTED, self._on_signal_executed)
            logger.debug("Subscribed to SIGNAL_EXECUTED events")
        except Exception as exc:
            logger.warning(f"Could not subscribe to EventBus: {exc}")

    def _on_signal_executed(self, event: Event) -> None:
        """
        Handle SIGNAL_EXECUTED events.

        Extracts strategy name and P&L from the event payload and records
        the return.  Gracefully ignores events that lack the expected data.
        """
        try:
            data = event.data
            strategy = data.get("strategy") or data.get("strategy_name", "")
            pnl_pct = data.get("pnl_pct") or data.get("pnl_percent")
            if not strategy or pnl_pct is None:
                return
            self.record_return(strategy, float(pnl_pct))
        except Exception as exc:
            logger.error(f"Error processing SIGNAL_EXECUTED event: {exc}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def record_return(
        self, strategy: str, pnl_pct: float, timestamp: Optional[str] = None
    ) -> None:
        """
        Record a single trade return for a strategy.

        Args:
            strategy: Strategy name (e.g. ``"mean_reversion"``).
            pnl_pct: Percentage P&L of the trade (e.g. ``1.5`` for +1.5%).
            timestamp: ISO-8601 timestamp; defaults to ``datetime.utcnow()``.
        """
        ts = timestamp or datetime.now(timezone.utc).isoformat()

        # Persist to database
        try:
            with get_db_connection() as conn:
                conn.execute(
                    "INSERT INTO strategy_returns (strategy, pnl_pct, timestamp) "
                    "VALUES (?, ?, ?)",
                    (strategy, pnl_pct, ts),
                )
                conn.commit()
        except Exception as exc:
            logger.error(f"Failed to persist return for {strategy}: {exc}")

        # Update in-memory cache
        with self._lock:
            if strategy not in self._returns_cache:
                self._returns_cache[strategy] = []
            self._returns_cache[strategy].append((ts, pnl_pct))

        logger.debug(f"Recorded return for {strategy}: {pnl_pct:+.2f}%")

    def calculate_rolling_correlation(
        self,
        strategy_a: str,
        strategy_b: str,
        days: Optional[int] = None,
    ) -> Optional[float]:
        """
        Calculate the rolling Pearson correlation between two strategies.

        Returns ``None`` when fewer than ``MIN_TRADES_FOR_CORRELATION``
        trades are available for either strategy within the window.
        """
        window = days or self._rolling_days
        cutoff = datetime.now(timezone.utc) - timedelta(days=window)
        cutoff_str = cutoff.isoformat()

        returns_a = self._get_returns_in_window(strategy_a, cutoff_str)
        returns_b = self._get_returns_in_window(strategy_b, cutoff_str)

        if len(returns_a) < MIN_TRADES_FOR_CORRELATION:
            return None
        if len(returns_b) < MIN_TRADES_FOR_CORRELATION:
            return None

        # Align by truncating to the shorter series
        n = min(len(returns_a), len(returns_b))
        a = np.array(returns_a[-n:], dtype=np.float64)
        b = np.array(returns_b[-n:], dtype=np.float64)

        if np.std(a) == 0 or np.std(b) == 0:
            return None

        correlation = float(np.corrcoef(a, b)[0, 1])

        # Persist snapshot
        self._save_correlation(strategy_a, strategy_b, correlation, window)

        return correlation

    def get_correlation_matrix(
        self,
        strategies: Optional[List[str]] = None,
        days: Optional[int] = None,
    ) -> Dict[str, Dict[str, Optional[float]]]:
        """
        Compute the full pairwise correlation matrix for the given strategies.

        Args:
            strategies: List of strategy names.  If *None*, uses all strategies
                with recorded returns.
            days: Rolling window in days.

        Returns:
            Nested dict ``{strat_a: {strat_b: correlation_or_None}}``.
        """
        if strategies is None:
            with self._lock:
                strategies = list(self._returns_cache.keys())

        matrix: Dict[str, Dict[str, Optional[float]]] = {}
        for sa in strategies:
            matrix[sa] = {}
            for sb in strategies:
                if sa == sb:
                    matrix[sa][sb] = 1.0
                elif sb in matrix and sa in matrix[sb]:
                    matrix[sa][sb] = matrix[sb][sa]
                else:
                    matrix[sa][sb] = self.calculate_rolling_correlation(
                        sa, sb, days
                    )

        return matrix

    def detect_decay(self, strategy: str) -> Optional[DecayAlert]:
        """
        Detect if a strategy's recent Sharpe has decayed vs its baseline.

        The baseline is the Sharpe computed over the full available history
        (up to ``SHARPE_LOOKBACK`` trades).  The current Sharpe uses only
        the most recent half of trades.

        Returns a ``DecayAlert`` when the drop exceeds
        ``_decay_threshold_pct`` percent, otherwise ``None``.
        """
        with self._lock:
            all_returns = self._returns_cache.get(strategy, [])

        if len(all_returns) < MIN_TRADES_FOR_SHARPE:
            return None

        baseline_returns = [r for _, r in all_returns[-self._sharpe_lookback :]]
        baseline_sharpe = self._compute_sharpe(baseline_returns)

        mid = len(baseline_returns) // 2
        recent_returns = baseline_returns[mid:]
        recent_sharpe = self._compute_sharpe(recent_returns)

        if baseline_sharpe is None or recent_sharpe is None:
            return None

        # Calculate percentage decay (negative means improvement)
        if abs(baseline_sharpe) < 1e-9:
            # Baseline near zero; use absolute difference
            decay_pct = (baseline_sharpe - recent_sharpe) * 100.0
        else:
            decay_pct = (
                (baseline_sharpe - recent_sharpe) / abs(baseline_sharpe)
            ) * 100.0

        if decay_pct > self._decay_threshold_pct:
            alert = DecayAlert(
                strategy=strategy,
                current_sharpe=round(recent_sharpe, 4),
                baseline_sharpe=round(baseline_sharpe, 4),
                decay_pct=round(decay_pct, 2),
                trade_count=len(recent_returns),
            )
            logger.warning(
                f"Decay detected for {strategy}: "
                f"Sharpe {baseline_sharpe:.4f} -> {recent_sharpe:.4f} "
                f"({decay_pct:+.1f}%)"
            )
            self._publish_decay_alert(alert)
            return alert

        return None

    def get_health_report(self) -> Dict[str, Any]:
        """
        Generate a comprehensive health report.

        Returns a dict (not a ``HealthReport`` dataclass) for easy JSON
        serialisation by FastAPI.
        """
        with self._lock:
            strategies = list(self._returns_cache.keys())

        health_map: Dict[str, StrategyHealth] = {}
        decay_alerts: List[DecayAlert] = []
        correlation_alerts: List[CorrelationAlert] = []

        for strat in strategies:
            with self._lock:
                returns = [r for _, r in self._returns_cache.get(strat, [])]

            sharpe = self._compute_sharpe(returns) if returns else None
            trade_count = len(returns)
            wins = sum(1 for r in returns if r > 0)
            win_rate = wins / trade_count if trade_count > 0 else 0.0
            avg_pnl = float(np.mean(returns)) if returns else 0.0
            total_pnl = float(np.sum(returns)) if returns else 0.0
            profit_factor = self._compute_profit_factor(returns)
            max_dd = self._compute_max_drawdown_pct(returns)

            health_map[strat] = StrategyHealth(
                strategy=strat,
                sharpe_ratio=round(sharpe, 4) if sharpe is not None else None,
                trade_count=trade_count,
                win_rate=round(win_rate, 4),
                avg_pnl_pct=round(avg_pnl, 4),
                total_pnl_pct=round(total_pnl, 4),
                profit_factor=(
                    round(profit_factor, 4) if profit_factor is not None else None
                ),
                max_drawdown_pct=round(max_dd, 4),
            )

            decay = self.detect_decay(strat)
            if decay is not None:
                decay_alerts.append(decay)

        # Correlation matrix & alerts
        corr_matrix = self.get_correlation_matrix(strategies)
        seen_pairs: set = set()
        for sa in strategies:
            for sb in strategies:
                if sa >= sb:
                    continue
                pair_key = (sa, sb)
                if pair_key in seen_pairs:
                    continue
                corr_val = corr_matrix.get(sa, {}).get(sb)
                if corr_val is not None and abs(corr_val) >= self._correlation_threshold:
                    alert = CorrelationAlert(
                        strategy_a=sa,
                        strategy_b=sb,
                        correlation=round(corr_val, 4),
                        window_days=self._rolling_days,
                    )
                    correlation_alerts.append(alert)
                seen_pairs.add(pair_key)

        report = HealthReport(
            strategies=health_map,
            correlation_matrix=corr_matrix,
            correlation_alerts=correlation_alerts,
            decay_alerts=decay_alerts,
        )

        # Persist health snapshot
        self._save_health_snapshot(health_map)

        return report.to_dict()

    def get_regime_attribution(
        self, exchange: Optional[str] = None
    ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """
        Compute per-(strategy, regime) performance from closed trades.

        Reads closed trades via ``history.TradeStore`` and groups them by
        strategy and the regime tag recorded at trade creation. Trades
        without a regime tag are grouped under "UNTAGGED" so tagging
        coverage stays visible.

        Per-trade pnl_pct is derived as pnl / (entry_price * quantity) * 100
        (0.0 when the notional is non-positive).

        Args:
            exchange: Optional exchange filter (e.g. "pacifica"); default
                includes trades from all exchanges.

        Returns:
            JSON-serialisable dict keyed strategy -> regime -> stats, where
            stats contains trade_count, win_rate, profit_factor, avg_pnl_pct,
            and total_pnl_pct.
        """
        try:
            trades = self._trade_store.get_closed_trades(exchange=exchange)
        except Exception as exc:
            logger.warning(f"Regime attribution query failed: {exc}")
            return {}

        # strategy -> regime -> list of pnl_pct
        grouped: Dict[str, Dict[str, List[float]]] = {}
        for trade in trades:
            strategy = trade.get("strategy")
            regime = trade.get("regime")
            strat_key = str(strategy) if strategy else "UNKNOWN"
            regime_key = str(regime) if regime else "UNTAGGED"
            pnl = trade.get("pnl")
            try:
                pnl_val = float(pnl) if pnl is not None else 0.0
                notional = float(trade.get("entry_price") or 0.0) * float(
                    trade.get("quantity") or 0.0
                )
            except (TypeError, ValueError):
                continue
            # Non-positive notional counts as 0.0 so tagging coverage
            # stays visible.
            pnl_pct = (pnl_val / notional) * 100.0 if notional > 0 else 0.0
            grouped.setdefault(strat_key, {}).setdefault(regime_key, []).append(
                pnl_pct
            )

        attribution: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for strat_key, regimes in grouped.items():
            attribution[strat_key] = {}
            for regime_key, returns in regimes.items():
                trade_count = len(returns)
                wins = sum(1 for r in returns if r > 0)
                win_rate = wins / trade_count if trade_count > 0 else 0.0
                profit_factor = self._compute_profit_factor(returns)
                avg_pnl = float(np.mean(returns)) if returns else 0.0
                total_pnl = float(np.sum(returns)) if returns else 0.0
                attribution[strat_key][regime_key] = {
                    "trade_count": trade_count,
                    "win_rate": round(win_rate, 4),
                    "profit_factor": (
                        round(profit_factor, 4)
                        if profit_factor is not None
                        else None
                    ),
                    "avg_pnl_pct": round(avg_pnl, 4),
                    "total_pnl_pct": round(total_pnl, 4),
                }
        return attribution

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _get_returns_in_window(
        self, strategy: str, cutoff_iso: str
    ) -> List[float]:
        """Return list of pnl_pct values after ``cutoff_iso`` for a strategy."""
        # Try database first for large histories
        try:
            with get_db_connection() as conn:
                cursor = conn.execute(
                    "SELECT pnl_pct FROM strategy_returns "
                    "WHERE strategy = ? AND timestamp >= ? "
                    "ORDER BY timestamp ASC",
                    (strategy, cutoff_iso),
                )
                rows = cursor.fetchall()
                if rows:
                    return [float(row[0]) for row in rows]
        except Exception as exc:
            logger.warning(f"DB query failed, falling back to cache: {exc}")

        # Fall back to in-memory cache
        with self._lock:
            cache = self._returns_cache.get(strategy, [])
        return [
            pnl
            for ts, pnl in cache
            if ts >= cutoff_iso
        ]

    def _compute_sharpe(
        self, returns: List[float], risk_free_rate: float = 0.0
    ) -> Optional[float]:
        """
        Compute annualised Sharpe ratio from a list of percentage returns.

        Uses a simplified annualisation assuming daily trading frequency.
        Returns ``None`` if fewer than 2 returns or zero variance.
        """
        if len(returns) < 2:
            return None
        arr = np.array(returns, dtype=np.float64)
        std = float(np.std(arr, ddof=1))
        if std == 0:
            return None
        mean = float(np.mean(arr))
        return (mean - risk_free_rate) / std

    def _compute_profit_factor(self, returns: List[float]) -> Optional[float]:
        """
        Compute profit factor (gross profit / gross loss) from pnl_pct values.

        Thin wrapper over :func:`trading_bot_v2.history.metrics.profit_factor`
        (single source of truth): ``None`` when there are no trades or no
        losing trades, ``0.0`` when there are losses but no winning trades.
        """
        return history_metrics.profit_factor(returns)

    def _compute_max_drawdown_pct(self, returns: List[float]) -> float:
        """
        Compute maximum drawdown from a time-ordered list of pnl_pct values.

        Thin wrapper over
        :func:`trading_bot_v2.history.metrics.max_drawdown_pct`: compounds
        an equity curve starting at 100.0 and returns the largest
        peak-to-trough decline as a positive percentage (``0.0`` for an
        empty list).
        """
        return history_metrics.max_drawdown_pct(returns)

    def _save_correlation(
        self,
        strategy_a: str,
        strategy_b: str,
        correlation: float,
        window_days: int,
    ) -> None:
        """Persist a correlation snapshot to the database."""
        try:
            with get_db_connection() as conn:
                conn.execute(
                    "INSERT INTO strategy_correlations "
                    "(strategy_a, strategy_b, correlation, window_days) "
                    "VALUES (?, ?, ?, ?)",
                    (strategy_a, strategy_b, correlation, window_days),
                )
                conn.commit()
        except Exception as exc:
            logger.warning(f"Failed to persist correlation: {exc}")

    def _save_health_snapshot(
        self, health_map: Dict[str, StrategyHealth]
    ) -> None:
        """Persist daily health snapshots using INSERT OR REPLACE logic."""
        today = date.today().isoformat()
        try:
            with get_db_connection() as conn:
                for strat, health in health_map.items():
                    if is_postgres():
                        conn.execute(
                            "INSERT INTO strategy_health_snapshots "
                            "(strategy, sharpe_ratio, trade_count, win_rate, "
                            "avg_pnl_pct, profit_factor, max_drawdown_pct, "
                            "snapshot_date) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT (strategy, snapshot_date) DO UPDATE SET "
                            "sharpe_ratio = EXCLUDED.sharpe_ratio, "
                            "trade_count = EXCLUDED.trade_count, "
                            "win_rate = EXCLUDED.win_rate, "
                            "avg_pnl_pct = EXCLUDED.avg_pnl_pct, "
                            "profit_factor = EXCLUDED.profit_factor, "
                            "max_drawdown_pct = EXCLUDED.max_drawdown_pct",
                            (
                                strat,
                                health.sharpe_ratio,
                                health.trade_count,
                                health.win_rate,
                                health.avg_pnl_pct,
                                health.profit_factor,
                                health.max_drawdown_pct,
                                today,
                            ),
                        )
                    else:
                        conn.execute(
                            "INSERT OR REPLACE INTO strategy_health_snapshots "
                            "(strategy, sharpe_ratio, trade_count, win_rate, "
                            "avg_pnl_pct, profit_factor, max_drawdown_pct, "
                            "snapshot_date) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                strat,
                                health.sharpe_ratio,
                                health.trade_count,
                                health.win_rate,
                                health.avg_pnl_pct,
                                health.profit_factor,
                                health.max_drawdown_pct,
                                today,
                            ),
                        )
                conn.commit()
        except Exception as exc:
            logger.warning(f"Failed to persist health snapshot: {exc}")

    def _publish_decay_alert(self, alert: DecayAlert) -> None:
        """Publish a decay alert event to the EventBus."""
        try:
            bus = get_event_bus()
            bus.publish_event(
                EventType.RISK_LIMIT_EXCEEDED,
                {
                    "alert_type": "strategy_decay",
                    "strategy": alert.strategy,
                    "current_sharpe": alert.current_sharpe,
                    "baseline_sharpe": alert.baseline_sharpe,
                    "decay_pct": alert.decay_pct,
                    "trade_count": alert.trade_count,
                },
                "strategy_monitor",
            )
        except Exception as exc:
            logger.warning(f"Failed to publish decay alert: {exc}")

    def _publish_correlation_alert(self, alert: CorrelationAlert) -> None:
        """Publish a correlation alert event to the EventBus."""
        try:
            bus = get_event_bus()
            bus.publish_event(
                EventType.RISK_LIMIT_EXCEEDED,
                {
                    "alert_type": "strategy_correlation",
                    "strategy_a": alert.strategy_a,
                    "strategy_b": alert.strategy_b,
                    "correlation": alert.correlation,
                    "window_days": alert.window_days,
                },
                "strategy_monitor",
            )
        except Exception as exc:
            logger.warning(f"Failed to publish correlation alert: {exc}")

    # ------------------------------------------------------------------
    # Cache loading (call on startup to hydrate from DB)
    # ------------------------------------------------------------------
    def load_returns_from_db(
        self, days: Optional[int] = None
    ) -> Dict[str, int]:
        """
        Hydrate the in-memory returns cache from the database.

        Args:
            days: How many days of history to load.  Defaults to the
                ``sharpe_lookback`` value.

        Returns:
            Dict mapping strategy name to number of returns loaded.
        """
        window = days or self._sharpe_lookback
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window)).isoformat()

        loaded: Dict[str, int] = {}
        try:
            with get_db_connection() as conn:
                cursor = conn.execute(
                    "SELECT strategy, pnl_pct, timestamp "
                    "FROM strategy_returns "
                    "WHERE timestamp >= ? "
                    "ORDER BY strategy, timestamp ASC",
                    (cutoff,),
                )
                rows = cursor.fetchall()

            with self._lock:
                for strategy, pnl_pct, ts in rows:
                    if strategy not in self._returns_cache:
                        self._returns_cache[strategy] = []
                    self._returns_cache[strategy].append((ts, float(pnl_pct)))
                    loaded[strategy] = len(self._returns_cache[strategy])

            logger.info(
                f"Loaded returns from DB: "
                + ", ".join(f"{s}: {n}" for s, n in loaded.items())
            )
        except Exception as exc:
            logger.error(f"Failed to load returns from DB: {exc}")

        return loaded


# ---------------------------------------------------------------------------
# Singleton convenience accessor
# ---------------------------------------------------------------------------
_monitor: Optional[StrategyMonitor] = None
_monitor_lock = threading.Lock()


def get_strategy_monitor() -> StrategyMonitor:
    """Get or create the global StrategyMonitor instance."""
    global _monitor
    if _monitor is None:
        with _monitor_lock:
            if _monitor is None:
                _monitor = StrategyMonitor()
    return _monitor
