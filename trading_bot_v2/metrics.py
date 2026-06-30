"""
Prometheus Metrics Module for Bot 3 Trading System.

Provides standardized metrics collection for monitoring trading performance,
system health, data quality, and operational status.

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

    # Record trading loop phase
    metrics.record_loop_phase("generate_signals", 0.15)

    # Record market regime
    metrics.update_market_regime("BTC-USDC", 3)  # TRENDING_STRONG
"""

import time
from typing import Optional
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from loguru import logger


class TradingMetrics:
    """Prometheus metrics collector for trading bot."""

    def __init__(self):
        """Initialize Prometheus metrics."""
        # =====================================================================
        # Trade metrics
        # =====================================================================
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

        # =====================================================================
        # Trading Loop Profiler Metrics (Plan 02-03)
        # =====================================================================
        self.loop_duration = Histogram(
            'bot_trading_loop_duration_seconds',
            'Total trading loop iteration duration',
            buckets=[5, 10, 15, 20, 25, 30, 40, 50, 60, 90, 120],
        )

        self.loop_iteration = Gauge(
            'bot_trading_loop_iteration',
            'Current trading loop iteration number',
        )

        self.loop_phase_duration = Histogram(
            'bot_trading_loop_phase_duration_seconds',
            'Duration of individual trading loop phases',
            ['phase'],
            buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0],
        )

        self.loop_phase_count = Counter(
            'bot_trading_loop_phase_count_total',
            'Total phase executions',
            ['phase'],
        )

        # =====================================================================
        # Signal Metrics (Plan 02-03)
        # =====================================================================
        self.signals_generated = Counter(
            'bot_signals_generated_total',
            'Total signals generated',
            ['strategy', 'symbol', 'side'],
        )

        self.signal_confidence = Gauge(
            'bot_signal_confidence',
            'Signal confidence score (0-1)',
            ['strategy', 'side'],
        )

        # =====================================================================
        # Market Regime Metrics (Plan 02-03)
        # =====================================================================
        self.market_regime = Gauge(
            'bot_market_regime',
            'Current market regime (0=RANGING_CALM, 1=RANGING_VOLATILE, '
            '2=TRENDING_MODERATE, 3=TRENDING_STRONG, 4=INDECISIVE)',
            ['symbol'],
        )

        self.adx_value = Gauge(
            'bot_adx_value',
            'Average Directional Index value',
            ['symbol'],
        )

        self.rsi_value = Gauge(
            'bot_rsi_value',
            'Relative Strength Index value',
            ['symbol'],
        )

        # =====================================================================
        # System Resource Metrics (Plan 02-03)
        # =====================================================================
        self.process_cpu_percent = Gauge(
            'bot_process_cpu_usage_percent',
            'Bot process CPU usage percentage',
        )

        self.process_memory_bytes = Gauge(
            'bot_process_memory_bytes',
            'Bot process memory usage in bytes',
        )

        # =====================================================================
        # Database Pool Metrics (Plan 02-03)
        # =====================================================================
        self.db_pool_active = Gauge(
            'bot_db_pool_active_connections',
            'Active database pool connections',
        )

        self.db_pool_idle = Gauge(
            'bot_db_pool_idle_connections',
            'Idle database pool connections',
        )

        self.db_pool_waiting = Gauge(
            'bot_db_pool_waiting',
            'Number of threads waiting for a database connection',
        )

        # =====================================================================
        # HTTP Metrics (Plan 02-03)
        # =====================================================================
        self.http_requests = Counter(
            'bot_http_requests_total',
            'Total HTTP requests',
            ['method', 'status_code', 'endpoint'],
        )

        self.http_request_duration = Histogram(
            'bot_http_request_duration_seconds',
            'HTTP request duration',
            ['method', 'endpoint'],
            buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
        )

        # =====================================================================
        # WebSocket Metrics (Plan 02-03)
        # =====================================================================
        self.websocket_latency = Gauge(
            'bot_websocket_latency_seconds',
            'WebSocket message latency',
            ['symbol'],
        )

        # =====================================================================
        # Event System Metrics (Plan 02-03)
        # =====================================================================
        self.events_published = Counter(
            'bot_events_published_total',
            'Total events published to event bus',
            ['event_type'],
        )

        self.events_processed = Counter(
            'bot_events_processed_total',
            'Total events processed by event bus',
            ['event_type'],
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

    # =====================================================================
    # Trading Loop Profiler Methods (Plan 02-03)
    # =====================================================================

    def record_loop_phase(self, phase: str, duration_seconds: float) -> None:
        """
        Record a trading loop phase duration.

        Args:
            phase: Phase name (e.g., 'generate_signals', 'update_positions')
            duration_seconds: Phase duration in seconds
        """
        self.loop_phase_duration.labels(phase=phase).observe(duration_seconds)
        self.loop_phase_count.labels(phase=phase).inc()

    def record_loop_duration(self, duration_seconds: float) -> None:
        """
        Record total trading loop iteration duration.

        Args:
            duration_seconds: Total loop duration in seconds
        """
        self.loop_duration.observe(duration_seconds)

    def set_loop_iteration(self, iteration: int) -> None:
        """
        Set current trading loop iteration number.

        Args:
            iteration: Current iteration number
        """
        self.loop_iteration.set(iteration)

    # =====================================================================
    # Signal Metrics Methods (Plan 02-03)
    # =====================================================================

    def record_signal_generated(
        self, strategy: str, symbol: str, side: str, confidence: float
    ) -> None:
        """
        Record a generated signal.

        Args:
            strategy: Strategy name
            symbol: Trading pair symbol
            side: Signal side ('long' or 'short')
            confidence: Signal confidence (0-1)
        """
        self.signals_generated.labels(
            strategy=strategy, symbol=symbol, side=side
        ).inc()
        self.signal_confidence.labels(strategy=strategy, side=side).set(confidence)

    # =====================================================================
    # Market Regime Methods (Plan 02-03)
    # =====================================================================

    def update_market_regime(self, symbol: str, regime: int) -> None:
        """
        Update market regime for a symbol.

        Args:
            symbol: Trading pair symbol
            regime: Regime code (0=RANGING_CALM, 1=RANGING_VOLATILE,
                     2=TRENDING_MODERATE, 3=TRENDING_STRONG, 4=INDECISIVE)
        """
        self.market_regime.labels(symbol=symbol).set(regime)

    def update_adx(self, symbol: str, value: float) -> None:
        """
        Update ADX value for a symbol.

        Args:
            symbol: Trading pair symbol
            value: ADX value (0-100)
        """
        self.adx_value.labels(symbol=symbol).set(value)

    def update_rsi(self, symbol: str, value: float) -> None:
        """
        Update RSI value for a symbol.

        Args:
            symbol: Trading pair symbol
            value: RSI value (0-100)
        """
        self.rsi_value.labels(symbol=symbol).set(value)

    # =====================================================================
    # System Resource Methods (Plan 02-03)
    # =====================================================================

    def update_system_resources(
        self, cpu_percent: float, memory_bytes: int
    ) -> None:
        """
        Update system resource metrics.

        Args:
            cpu_percent: CPU usage percentage
            memory_bytes: Memory usage in bytes
        """
        self.process_cpu_percent.set(cpu_percent)
        self.process_memory_bytes.set(memory_bytes)

    # =====================================================================
    # Database Pool Methods (Plan 02-03)
    # =====================================================================

    def update_db_pool(
        self, active: int, idle: int, waiting: int = 0
    ) -> None:
        """
        Update database connection pool metrics.

        Args:
            active: Active connections count
            idle: Idle connections count
            waiting: Threads waiting for connection
        """
        self.db_pool_active.set(active)
        self.db_pool_idle.set(idle)
        self.db_pool_waiting.set(waiting)

    # =====================================================================
    # HTTP Request Methods (Plan 02-03)
    # =====================================================================

    def record_http_request(
        self,
        method: str,
        endpoint: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        """
        Record an HTTP request.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: Request endpoint
            status_code: HTTP status code
            duration_seconds: Request duration
        """
        self.http_requests.labels(
            method=method, status_code=str(status_code), endpoint=endpoint
        ).inc()
        self.http_request_duration.labels(
            method=method, endpoint=endpoint
        ).observe(duration_seconds)

    # =====================================================================
    # WebSocket Methods (Plan 02-03)
    # =====================================================================

    def update_websocket_latency(self, symbol: str, latency_seconds: float) -> None:
        """
        Update WebSocket latency for a symbol.

        Args:
            symbol: Trading pair symbol
            latency_seconds: Message latency in seconds
        """
        self.websocket_latency.labels(symbol=symbol).set(latency_seconds)

    # =====================================================================
    # Event System Methods (Plan 02-03)
    # =====================================================================

    def record_event_published(self, event_type: str) -> None:
        """
        Record an event published to the event bus.

        Args:
            event_type: Event type name
        """
        self.events_published.labels(event_type=event_type).inc()

    def record_event_processed(self, event_type: str) -> None:
        """
        Record an event processed by the event bus.

        Args:
            event_type: Event type name
        """
        self.events_processed.labels(event_type=event_type).inc()

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
