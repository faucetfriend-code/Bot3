"""
Prometheus Metrics Module for Bot 3 Trading System.

Provides standardized metrics collection for monitoring trading performance,
system health, and operational status.

Usage:
    from trading_bot_v2.metrics import metrics

    # Record a trade
    metrics.record_trade("mean_reversion", "BTC-USDC", "long", 100.0)

    # Update positions
    metrics.update_positions("mean_reversion", 3)

    # Record signal latency
    metrics.record_signal_latency(0.15)

    # Update balance
    metrics.update_balance(50000.0)

    # Record error
    metrics.record_error("exchange_timeout")
"""

import time
from typing import Optional
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from loguru import logger


class TradingMetrics:
    """Prometheus metrics collector for trading bot."""

    def __init__(self):
        """Initialize Prometheus metrics."""
        # Trade metrics
        self.trades_total = Counter(
            'bot_trades_total',
            'Total number of trades executed',
            ['strategy', 'symbol', 'side']
        )

        # Position metrics
        self.open_positions = Gauge(
            'bot_open_positions',
            'Number of open positions',
            ['strategy']
        )

        # Latency metrics
        self.signal_latency = Histogram(
            'bot_signal_latency_seconds',
            'Signal processing latency in seconds',
            buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        )

        # Balance metrics
        self.balance_usd = Gauge(
            'bot_balance_usd',
            'Account balance in USD'
        )

        # Error metrics
        self.errors_total = Counter(
            'bot_errors_total',
            'Total number of errors',
            ['error_type']
        )

        # System metrics
        self.uptime_seconds = Gauge(
            'bot_uptime_seconds',
            'Bot uptime in seconds'
        )

        self.last_trade_timestamp = Gauge(
            'bot_last_trade_timestamp',
            'Timestamp of last trade'
        )

        # Strategy performance
        self.strategy_pnl = Gauge(
            'bot_strategy_pnl_usd',
            'Strategy P&L in USD',
            ['strategy']
        )

        self.strategy_win_rate = Gauge(
            'bot_strategy_win_rate',
            'Strategy win rate (0-1)',
            ['strategy']
        )

        # Circuit breaker
        self.circuit_breaker_active = Gauge(
            'bot_circuit_breaker_active',
            'Circuit breaker status (0=inactive, 1=active)'
        )

        # Initialize start time
        self._start_time = time.time()

        logger.info("Prometheus metrics initialized")

    def record_trade(
        self,
        strategy: str,
        symbol: str,
        side: str,
        pnl: Optional[float] = None
    ) -> None:
        """
        Record a trade execution.

        Args:
            strategy: Strategy name (e.g., 'mean_reversion')
            symbol: Trading pair (e.g., 'BTC-USDC')
            side: Trade side ('long' or 'short')
            pnl: Profit/loss in USD (optional)
        """
        self.trades_total.labels(
            strategy=strategy,
            symbol=symbol,
            side=side
        ).inc()

        self.last_trade_timestamp.set(time.time())

        if pnl is not None:
            self.strategy_pnl.labels(strategy=strategy).inc(pnl)

        logger.debug(f"Trade recorded: {strategy} {symbol} {side}")

    def update_positions(self, strategy: str, count: int) -> None:
        """
        Update open position count for a strategy.

        Args:
            strategy: Strategy name
            count: Number of open positions
        """
        self.open_positions.labels(strategy=strategy).set(count)

    def record_signal_latency(self, latency_seconds: float) -> None:
        """
        Record signal processing latency.

        Args:
            latency_seconds: Latency in seconds
        """
        self.signal_latency.observe(latency_seconds)

    def update_balance(self, balance_usd: float) -> None:
        """
        Update account balance.

        Args:
            balance_usd: Current balance in USD
        """
        self.balance_usd.set(balance_usd)

    def record_error(self, error_type: str) -> None:
        """
        Record an error occurrence.

        Args:
            error_type: Error type (e.g., 'exchange_timeout', 'risk_violation')
        """
        self.errors_total.labels(error_type=error_type).inc()
        logger.debug(f"Error recorded: {error_type}")

    def update_uptime(self) -> None:
        """Update uptime metric."""
        uptime = time.time() - self._start_time
        self.uptime_seconds.set(uptime)

    def update_strategy_performance(
        self,
        strategy: str,
        win_rate: float,
        pnl: float
    ) -> None:
        """
        Update strategy performance metrics.

        Args:
            strategy: Strategy name
            win_rate: Win rate (0-1)
            pnl: Total P&L in USD
        """
        self.strategy_win_rate.labels(strategy=strategy).set(win_rate)
        self.strategy_pnl.labels(strategy=strategy).set(pnl)

    def set_circuit_breaker(self, active: bool) -> None:
        """
        Set circuit breaker status.

        Args:
            active: True if circuit breaker is active
        """
        self.circuit_breaker_active.set(1 if active else 0)

    def get_metrics(self) -> bytes:
        """
        Get all metrics in Prometheus format.

        Returns:
            Metrics in Prometheus exposition format
        """
        self.update_uptime()
        return generate_latest()

    def get_content_type(self) -> str:
        """Get Prometheus content type."""
        return CONTENT_TYPE_LATEST


# Global metrics instance
metrics = TradingMetrics()
