# Hub system components for API communication
# This module contains hub-related classes that can be imported without dependency issues

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
import asyncio
import logging
import statistics
import threading
import time
from typing import Dict, Any, List, Optional
from datetime import datetime


class DataHub:
    """Centralized data hub for managing trading bot data and communication."""

    def __init__(self, config: Any, database: Any, trading_bot: Any, risk_manager: Any):
        self.config = config
        self.database = database
        self.trading_bot = trading_bot
        self.risk_manager = risk_manager

        # Synchronization primitives
        self._cache_lock = asyncio.Lock()
        self._db_lock = asyncio.Lock()
        self._broadcast_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(10)  # Limit concurrent operations
        self._barrier = asyncio.Barrier(3)  # Startup synchronization barrier

        # Readiness flags
        self._cache_ready = asyncio.Event()
        self._db_ready = asyncio.Event()
        self._bot_ready = asyncio.Event()

        # Circuit breaker for database operations (thread-safe)
        self._circuit_breaker_lock = threading.Lock()
        self._db_circuit_breaker_failures = 0
        self._db_circuit_breaker_threshold = 5
        self._db_circuit_breaker_timeout = 60.0  # seconds
        self._db_last_failure_time = 0

        # Data structures
        self._price_cache: Dict[str, float] = {}
        self._subscriber_count = 0

    async def initialize_cache(self):
        """Initialize data cache from database and WebSocket sources."""
        async with self._cache_lock:
            # Initialize from database with circuit breaker
            try:
                if await self._check_circuit_breaker():
                    positions = await self._execute_with_retry(
                        self.database.get_positions
                    )
                    logging.info(
                        f"Initialized cache with {len(positions) if positions else 0} positions"
                    )
                else:
                    logging.warning(
                        "Database circuit breaker open, skipping cache initialization"
                    )
            except Exception as e:
                logging.error(f"Error initializing cache from database: {e}")
                self._record_failure()

            # Mark cache as ready
            self._cache_ready.set()

    async def _check_circuit_breaker(self) -> bool:
        """Check if circuit breaker allows operation (thread-safe)."""
        with self._circuit_breaker_lock:
            current_time = time.time()

            if self._db_circuit_breaker_failures >= self._db_circuit_breaker_threshold:
                time_diff = current_time - self._db_last_failure_time
                if time_diff < self._db_circuit_breaker_timeout:
                    return False  # Circuit breaker open
                else:
                    # Reset circuit breaker after timeout
                    self._db_circuit_breaker_failures = 0
                    return True  # Allow operation after reset
            return True

    def _record_failure(self):
        """Record a failure for circuit breaker (thread-safe)."""
        with self._circuit_breaker_lock:
            self._db_circuit_breaker_failures += 1
            self._db_last_failure_time = time.time()

    async def _execute_with_retry(
        self, func, *args, max_retries: int = 3, base_delay: float = 1.0
    ):
        """Execute function with exponential backoff retry."""
        for attempt in range(max_retries):
            try:
                if asyncio.iscoroutinefunction(func):
                    return await func(*args)
                else:
                    return func(*args)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise e

                delay = base_delay * (2**attempt)
                logging.warning(
                    f"Operation failed (attempt {attempt + 1}/{max_retries}), retrying in {delay:.1f}s: {e}"
                )
                await asyncio.sleep(delay)

    async def wait_for_readiness(self):
        """Wait for all components to be ready."""
        await asyncio.gather(
            self._cache_ready.wait(), self._db_ready.wait(), self._bot_ready.wait()
        )

    def mark_component_ready(self, component: str):
        """Mark a component as ready."""
        if component == "database":
            self._db_ready.set()
        elif component == "bot":
            self._bot_ready.set()
        elif component == "cache":
            self._cache_ready.set()

    async def get_price_cache(self) -> Dict[str, float]:
        """Get current price cache with thread safety."""
        await self._cache_ready.wait()  # Ensure cache is initialized
        async with self._cache_lock:
            return self._price_cache.copy()

    async def update_price_cache(self, symbol: str, price: float):
        """Update price cache with new price data."""
        async with self._cache_lock:
            self._price_cache[symbol] = price

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Get positions from database with enhanced synchronization and error handling."""
        async with self._semaphore:  # Limit concurrent operations
            async with self._db_lock:  # Protect database operations
                try:
                    if not await self._check_circuit_breaker():
                        logging.warning(
                            "Database circuit breaker open, returning empty positions"
                        )
                        return []

                    positions = await self._execute_with_retry(
                        self.database.get_positions
                    )
                    return positions if positions else []
                except Exception as e:
                    logging.error(f"Error getting positions: {e}")
                    self._record_failure()
                    return []

    async def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get trades from database with enhanced synchronization."""
        async with self._semaphore:
            async with self._db_lock:
                try:
                    if not await self._check_circuit_breaker():
                        logging.warning(
                            "Database circuit breaker open, returning empty trades"
                        )
                        return []

                    trades = await self._execute_with_retry(
                        self.database.get_trades, limit
                    )
                    return trades if trades else []
                except Exception as e:
                    logging.error(f"Error getting trades: {e}")
                    self._record_failure()
                    return []


class ConnectionManager:
    """Manages WebSocket connections for real-time data distribution."""

    def __init__(self, data_hub: Optional[DataHub] = None, max_connections: int = 100):
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_connections)  # Connection limits
        self.data_hub = data_hub
        self._connection_count = 0
        self._auth_tokens: Dict[str, datetime] = {}  # For authentication

        # Performance monitoring
        self._message_count = 0
        self._broadcast_times: List[float] = []
        self._connection_times: List[float] = []
        self._error_count = 0
        self._performance_lock = asyncio.Lock()

    async def authenticate_connection(self, websocket: WebSocket) -> bool:
        """Authenticate WebSocket connection using token or API key."""
        try:
            # Check for authentication token in query parameters or headers
            token = websocket.query_params.get("token") or websocket.headers.get(
                "authorization"
            )
            if not token:
                # Allow unauthenticated connections for now (can be made strict later)
                logging.warning("WebSocket connection without authentication token")
                return True

            # Validate token (simple check for now)
            if token.startswith("ws_token_"):
                self._auth_tokens[token] = datetime.now()
                return True

            logging.error(f"Invalid authentication token: {token[:10]}...")
            return False
        except Exception as e:
            logging.error(f"Authentication error: {e}")
            return False

    async def connect(self, websocket: WebSocket):
        """Accept and register a new WebSocket connection with authentication and rate limiting."""
        start_time = asyncio.get_event_loop().time()

        # Rate limiting
        await self._semaphore.acquire()

        try:
            # Must accept BEFORE any other operations (including auth check)
            # Cannot close a WebSocket that hasn't been accepted
            await websocket.accept()

            # Authentication (after accept so we can close properly if needed)
            if not await self.authenticate_connection(websocket):
                await websocket.close(code=1008)  # Policy violation
                self._semaphore.release()  # Release semaphore on early return
                return

            async with self._lock:
                self.active_connections.append(websocket)
                self._connection_count += 1

            # Record connection timing
            connection_duration = asyncio.get_event_loop().time() - start_time
            self.record_connection_time(connection_duration)

            logging.info(
                f"WebSocket client connected. Total connections: {len(self.active_connections)}"
            )
        except Exception as e:
            self._semaphore.release()
            self._error_count += 1
            logging.error(f"Error accepting WebSocket connection: {e}")
            raise

    async def disconnect(self, websocket: WebSocket):
        """Remove a disconnected WebSocket connection."""
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
                self._connection_count -= 1
        self._semaphore.release()  # Release rate limiting semaphore
        logging.info(
            f"WebSocket client disconnected. Total connections: {len(self.active_connections)}"
        )

    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast a message to all connected WebSocket clients with circuit breaker and performance monitoring."""
        start_time = asyncio.get_event_loop().time()

        if not message or not isinstance(message, dict):
            logging.error(f"Invalid message format for broadcast: {type(message)}")
            return

        # Input validation and sanitization
        sanitized_message = self._sanitize_message(message)

        disconnected = []
        failed_count = 0
        max_failures = len(self.active_connections) // 2  # Circuit breaker threshold

        async with self._lock:
            for connection in self.active_connections:
                try:
                    if connection.client_state == WebSocketState.CONNECTED:
                        await asyncio.wait_for(
                            connection.send_json(sanitized_message),
                            timeout=5.0,  # Timeout to prevent hanging
                        )
                    else:
                        disconnected.append(connection)
                except asyncio.TimeoutError:
                    logging.warning("WebSocket send timeout, marking as disconnected")
                    disconnected.append(connection)
                    failed_count += 1
                    self._error_count += 1
                except Exception as e:
                    logging.error(f"Error sending message to WebSocket client: {e}")
                    disconnected.append(connection)
                    failed_count += 1
                    self._error_count += 1

            # Clean up disconnected clients
            for conn in disconnected:
                if conn in self.active_connections:
                    self.active_connections.remove(conn)
                    self._connection_count -= 1
                    self._semaphore.release()  # Release semaphore for cleaned up connections

        # Update performance metrics
        self._message_count += 1
        broadcast_duration = asyncio.get_event_loop().time() - start_time
        self.record_broadcast_time(broadcast_duration)

        # Circuit breaker: if too many failures, log warning
        if failed_count > max_failures:
            logging.warning(
                f"Circuit breaker triggered: {failed_count}/{len(self.active_connections)} broadcast failures"
            )

    def _sanitize_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize message content for security."""
        import copy

        # Deep copy and sanitize sensitive data
        sanitized = copy.deepcopy(message)

        def sanitize_dict(d: Dict[str, Any]) -> Dict[str, Any]:
            """Recursively sanitize a dictionary."""
            sensitive_fields = ["password", "token", "secret", "key"]
            for key, value in d.items():
                if key in sensitive_fields:
                    d[key] = "[REDACTED]"
                elif isinstance(value, dict):
                    d[key] = sanitize_dict(value)
            return d

        return sanitize_dict(sanitized)

    async def get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics for monitoring."""
        async with self._performance_lock:
            return {
                "active_connections": len(self.active_connections),
                "total_connections": self._connection_count,
                "messages_broadcast": self._message_count,
                "errors": self._error_count,
                "avg_broadcast_time": statistics.mean(self._broadcast_times)
                if self._broadcast_times
                else 0,
                "avg_connection_time": statistics.mean(self._connection_times)
                if self._connection_times
                else 0,
                "auth_tokens_active": len(self._auth_tokens),
            }

    def record_broadcast_time(self, duration: float):
        """Record broadcast performance timing."""
        self._broadcast_times.append(duration)
        # Keep only last 100 measurements
        if len(self._broadcast_times) > 100:
            self._broadcast_times.pop(0)

    def record_connection_time(self, duration: float):
        """Record connection establishment timing."""
        self._connection_times.append(duration)
        # Keep only last 100 measurements
        if len(self._connection_times) > 100:
            self._connection_times.pop(0)


# Factory functions for dependency injection
def create_data_hub(
    config: Any, database: Any, trading_bot: Any, risk_manager: Any
) -> DataHub:
    """Create DataHub instance with injected dependencies."""
    return DataHub(config, database, trading_bot, risk_manager)


def create_connection_manager(
    data_hub: Optional[DataHub] = None, max_connections: int = 100
) -> ConnectionManager:
    """Create ConnectionManager instance with optional DataHub integration."""
    return ConnectionManager(data_hub, max_connections)
