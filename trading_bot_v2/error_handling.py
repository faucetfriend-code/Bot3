"""
Enhanced error handling and recovery system for trading bot v2.

This module provides comprehensive error handling patterns, circuit breaker
implementations, retry mechanisms, and error categorization to ensure system
resilience and automatic recovery from failures.

Key Features:
- Enhanced circuit breaker patterns with exponential backoff
- Comprehensive error categorization and response standardization
- Automatic retry mechanisms with intelligent backoff
- Graceful degradation during high load conditions
- Error logging, tracking, and alerting
- Task-specific error handling with recovery strategies
"""

import asyncio
import time
import traceback
import logging
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import (
    Any, Callable, Dict, List, Optional, Union, Type,
    TypeVar, Tuple, Awaitable, Protocol
)
from functools import wraps
import json
import random
from loguru import logger


# Type variables
T = TypeVar('T')
E = TypeVar('E', bound=Exception)


class ErrorCategory(Enum):
    """Categories of errors for different handling strategies."""
    CRITICAL = "critical"      # System failures requiring immediate intervention
    HIGH = "high"             # Trading operation failures requiring retry
    NORMAL = "normal"         # API rate limits and temporary connectivity issues
    LOW = "low"              # Non-critical data inconsistencies


class ErrorSeverity(Enum):
    """Error severity levels."""
    LOW = 1      # Minor issues, logging only
    MEDIUM = 2   # Some impact, may need attention
    HIGH = 3     # Significant impact, requires immediate attention
    CRITICAL = 4 # System failure, emergency intervention needed


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"         # Circuit is open, blocking calls
    HALF_OPEN = "half_open"  # Testing if system has recovered


@dataclass
class ErrorContext:
    """Context information for error tracking."""
    error_id: str
    category: ErrorCategory
    severity: ErrorSeverity
    message: str
    exception: Optional[Exception]
    timestamp: datetime = field(default_factory=datetime.now)
    component: str = ""
    operation: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    retry_count: int = 0
    recovery_attempts: int = 0
    resolved: bool = False
    resolution_time: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'error_id': self.error_id,
            'category': self.category.value,
            'severity': self.severity.value,
            'message': self.message,
            'exception_type': type(self.exception).__name__ if self.exception else None,
            'exception_message': str(self.exception) if self.exception else None,
            'timestamp': self.timestamp.isoformat(),
            'component': self.component,
            'operation': self.operation,
            'metadata': self.metadata,
            'retry_count': self.retry_count,
            'recovery_attempts': self.recovery_attempts,
            'resolved': self.resolved,
            'resolution_time': self.resolution_time.isoformat() if self.resolution_time else None
        }


class RetryPolicy:
    """Defines retry behavior for operations."""
    
    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
        backoff_type: str = "exponential"
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
        self.backoff_type = backoff_type
    
    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt number."""
        if self.backoff_type == "exponential":
            delay = self.base_delay * (self.exponential_base ** (attempt - 1))
        elif self.backoff_type == "linear":
            delay = self.base_delay * attempt
        else:  # fixed
            delay = self.base_delay
        
        # Apply maximum limit
        delay = min(delay, self.max_delay)
        
        # Add jitter if enabled
        if self.jitter:
            delay *= (0.5 + random.random() * 0.5)
        
        return delay
    
    @classmethod
    def immediate(cls) -> 'RetryPolicy':
        """Immediate retry policy."""
        return cls(max_attempts=1, base_delay=0.1)
    
    @classmethod
    def conservative(cls) -> 'RetryPolicy':
        """Conservative retry policy."""
        return cls(
            max_attempts=5,
            base_delay=2.0,
            max_delay=300.0,
            exponential_base=2.5
        )
    
    @classmethod
    def aggressive(cls) -> 'RetryPolicy':
        """Aggressive retry policy."""
        return cls(
            max_attempts=10,
            base_delay=0.5,
            max_delay=30.0,
            exponential_base=1.5
        )


class BaseError(Exception):
    """Base class for all custom errors."""
    
    def __init__(
        self,
        message: str,
        error_id: Optional[str] = None,
        category: ErrorCategory = ErrorCategory.NORMAL,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
        component: str = "",
        operation: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None
    ):
        super().__init__(message)
        self.error_id = error_id or self._generate_error_id()
        self.category = category
        self.severity = severity
        self.component = component
        self.operation = operation
        self.metadata = metadata or {}
        self.cause = cause
        self.timestamp = datetime.now()
    
    def _generate_error_id(self) -> str:
        """Generate unique error ID."""
        import uuid
        return str(uuid.uuid4())
    
    def to_context(self) -> ErrorContext:
        """Convert to error context."""
        return ErrorContext(
            error_id=self.error_id,
            category=self.category,
            severity=self.severity,
            message=str(self),
            exception=self,
            component=self.component,
            operation=self.operation,
            metadata=self.metadata
        )


class TaskError(BaseError):
    """Error in task execution."""
    
    def __init__(
        self,
        message: str,
        task_id: Optional[str] = None,
        **kwargs
    ):
        super().__init__(
            message,
            category=ErrorCategory.HIGH,
            severity=ErrorSeverity.HIGH,
            **kwargs
        )
        self.task_id = task_id
        if task_id:
            self.metadata['task_id'] = task_id


class TimeoutError(BaseError):
    """Operation timeout error."""
    
    def __init__(
        self,
        message: str,
        timeout_duration: Optional[float] = None,
        **kwargs
    ):
        super().__init__(
            message,
            category=ErrorCategory.HIGH,
            severity=ErrorSeverity.HIGH,
            **kwargs
        )
        self.timeout_duration = timeout_duration
        if timeout_duration:
            self.metadata['timeout_duration'] = timeout_duration


class CancellationError(BaseError):
    """Operation cancellation error."""
    
    def __init__(
        self,
        message: str,
        cancelled_by: Optional[str] = None,
        **kwargs
    ):
        super().__init__(
            message,
            category=ErrorCategory.NORMAL,
            severity=ErrorSeverity.MEDIUM,
            **kwargs
        )
        self.cancelled_by = cancelled_by
        if cancelled_by:
            self.metadata['cancelled_by'] = cancelled_by


class DatabaseError(BaseError):
    """Database operation error."""
    
    def __init__(
        self,
        message: str,
        query: Optional[str] = None,
        **kwargs
    ):
        super().__init__(
            message,
            category=ErrorCategory.HIGH,
            severity=ErrorSeverity.HIGH,
            **kwargs
        )
        self.query = query
        if query:
            self.metadata['query'] = query


class APIError(BaseError):
    """API call error."""
    
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        endpoint: Optional[str] = None,
        **kwargs
    ):
        super().__init__(
            message,
            category=ErrorCategory.NORMAL,
            severity=ErrorSeverity.MEDIUM,
            **kwargs
        )
        self.status_code = status_code
        self.endpoint = endpoint
        if status_code:
            self.metadata['status_code'] = status_code
        if endpoint:
            self.metadata['endpoint'] = endpoint


class CircuitBreakerError(BaseError):
    """Circuit breaker is open error."""
    
    def __init__(
        self,
        message: str,
        circuit_name: Optional[str] = None,
        **kwargs
    ):
        super().__init__(
            message,
            category=ErrorCategory.HIGH,
            severity=ErrorSeverity.HIGH,
            **kwargs
        )
        self.circuit_name = circuit_name
        if circuit_name:
            self.metadata['circuit_name'] = circuit_name


class EnhancedCircuitBreaker:
    """Enhanced circuit breaker with monitoring and recovery."""
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: Type[Exception] = Exception,
        name: str = "default",
        success_threshold: int = 3,
        monitoring_enabled: bool = True
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.name = name
        self.success_threshold = success_threshold
        self.monitoring_enabled = monitoring_enabled
        
        # State
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._last_success_time: Optional[float] = None
        self._call_count = 0
        self._lock = asyncio.Lock()
        
        # History for monitoring
        self._history: deque[Tuple[float, bool, str]] = deque(maxlen=1000)
    
    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        return self._state
    
    @property
    def failure_count(self) -> int:
        """Get current failure count."""
        return self._failure_count
    
    @property
    def call_count(self) -> int:
        """Get total call count."""
        return self._call_count
    
    def can_execute(self) -> bool:
        """Check if execution is allowed."""
        if self._state == CircuitState.CLOSED:
            return True
        elif self._state == CircuitState.OPEN:
            return self._should_attempt_reset()
        elif self._state == CircuitState.HALF_OPEN:
            return True
        return False
    
    async def call(self, func: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
        """Execute function with circuit breaker protection."""
        if not self.can_execute():
            raise CircuitBreakerError(
                f"Circuit breaker '{self.name}' is {self._state.value}",
                circuit_name=self.name
            )
        
        self._call_count += 1
        start_time = time.time()
        
        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            
            if self.monitoring_enabled:
                execution_time = time.time() - start_time
                self._history.append((time.time(), True, f"success in {execution_time:.3f}s"))
            
            return result
            
        except self.expected_exception as e:
            await self.record_failure()
            
            if self.monitoring_enabled:
                execution_time = time.time() - start_time
                self._history.append((time.time(), False, f"failed in {execution_time:.3f}s: {str(e)}"))
            
            raise
    
    async def record_success(self):
        """Record a successful operation."""
        async with self._lock:
            self._last_success_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    logger.info(f"Circuit breaker '{self.name}' closed after {self.success_threshold} successes")
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success in closed state
                self._failure_count = max(0, self._failure_count - 1)
    
    async def record_failure(self):
        """Record a failed operation."""
        async with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            
            if self._state == CircuitState.CLOSED:
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    logger.warning(
                        f"Circuit breaker '{self.name}' opened after {self._failure_count} failures"
                    )
            elif self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._success_count = 0
                logger.warning(f"Circuit breaker '{self.name}' reopened from half-open state")
    
    def _should_attempt_reset(self) -> bool:
        """Check if circuit should attempt to reset."""
        if self._last_failure_time is None:
            return True
        
        elapsed = time.time() - self._last_failure_time
        return elapsed >= self.recovery_timeout
    
    async def force_open(self):
        """Force circuit breaker to open state."""
        async with self._lock:
            self._state = CircuitState.OPEN
            logger.info(f"Circuit breaker '{self.name}' forced open")
    
    async def force_close(self):
        """Force circuit breaker to closed state."""
        async with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            logger.info(f"Circuit breaker '{self.name}' forced closed")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics."""
        current_time = time.time()
        recent_failures = sum(
            1 for timestamp, success, _ in self._history
            if not success and current_time - timestamp < 300  # Last 5 minutes
        )
        
        return {
            'name': self.name,
            'state': self._state.value,
            'failure_count': self._failure_count,
            'success_count': self._success_count,
            'call_count': self._call_count,
            'recent_failures': recent_failures,
            'last_failure_time': self._last_failure_time,
            'last_success_time': self._last_success_time,
            'failure_threshold': self.failure_threshold,
            'recovery_timeout': self.recovery_timeout,
            'success_threshold': self.success_threshold
        }


class ErrorRecoveryStrategy(ABC):
    """Abstract base class for error recovery strategies."""
    
    @abstractmethod
    async def recover(self, error_context: ErrorContext) -> bool:
        """Attempt to recover from error. Returns True if successful."""
        pass


class RetryRecoveryStrategy(ErrorRecoveryStrategy):
    """Recovery strategy that retries the operation."""
    
    def __init__(self, retry_policy: RetryPolicy, operation: Callable[..., Awaitable[T]]):
        self.retry_policy = retry_policy
        self.operation = operation
    
    async def recover(self, error_context: ErrorContext) -> bool:
        """Retry the operation with backoff."""
        if error_context.retry_count >= self.retry_policy.max_attempts:
            return False
        
        delay = self.retry_policy.calculate_delay(error_context.retry_count + 1)
        await asyncio.sleep(delay)
        
        try:
            # Note: This assumes the operation can be called without arguments
            # In practice, you'd need to store and re-pass the original arguments
            await self.operation()
            return True
        except Exception:
            return False


class CircuitBreakerRecoveryStrategy(ErrorRecoveryStrategy):
    """Recovery strategy that waits for circuit breaker to close."""
    
    def __init__(self, circuit_breaker: EnhancedCircuitBreaker, timeout: float = 300.0):
        self.circuit_breaker = circuit_breaker
        self.timeout = timeout
    
    async def recover(self, error_context: ErrorContext) -> bool:
        """Wait for circuit breaker to become available."""
        start_time = time.time()
        
        while time.time() - start_time < self.timeout:
            if self.circuit_breaker.can_execute():
                return True
            await asyncio.sleep(1.0)
        
        return False


class GracefulDegradationStrategy(ErrorRecoveryStrategy):
    """Recovery strategy that falls back to alternative behavior."""
    
    def __init__(self, fallback_operation: Callable[..., Awaitable[T]]):
        self.fallback_operation = fallback_operation
    
    async def recover(self, error_context: ErrorContext) -> bool:
        """Execute fallback operation."""
        try:
            await self.fallback_operation()
            return True
        except Exception:
            return False


class ErrorManager:
    """Manages error handling, tracking, and recovery."""
    
    def __init__(self, max_tracked_errors: int = 10000):
        self.max_tracked_errors = max_tracked_errors
        self._error_history: deque[ErrorContext] = deque(maxlen=max_tracked_errors)
        self._active_errors: Dict[str, ErrorContext] = {}
        self._recovery_strategies: Dict[str, List[ErrorRecoveryStrategy]] = defaultdict(list)
        self._circuit_breakers: Dict[str, EnhancedCircuitBreaker] = {}
        self._error_handlers: List[Callable[[ErrorContext], None]] = []
        self._lock = asyncio.Lock()
    
    async def handle_error(
        self,
        error: Exception,
        component: str = "",
        operation: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        attempt_recovery: bool = True
    ) -> ErrorContext:
        """Handle an error with tracking and potential recovery."""
        # Create error context
        if isinstance(error, BaseError):
            error_context = error.to_context()
        else:
            error_context = ErrorContext(
                error_id=self._generate_error_id(),
                category=ErrorCategory.NORMAL,
                severity=ErrorSeverity.MEDIUM,
                message=str(error),
                exception=error,
                component=component,
                operation=operation,
                metadata=metadata or {}
            )
        
        # Override component and operation if provided
        if component:
            error_context.component = component
        if operation:
            error_context.operation = operation
        if metadata:
            error_context.metadata.update(metadata)
        
        # Track the error
        await self._track_error(error_context)
        
        # Attempt recovery if requested
        if attempt_recovery:
            recovery_success = await self._attempt_recovery(error_context)
            if recovery_success:
                error_context.resolved = True
                error_context.resolution_time = datetime.now()
        
        # Notify handlers
        await self._notify_handlers(error_context)
        
        return error_context
    
    async def _track_error(self, error_context: ErrorContext):
        """Track error in history and active errors."""
        async with self._lock:
            # Add to history
            self._error_history.append(error_context)
            
            # Add to active errors
            if error_context.error_id in self._active_errors:
                # Update existing error (retry case)
                existing = self._active_errors[error_context.error_id]
                existing.retry_count += 1
                existing.recovery_attempts += 1
                existing.metadata.update(error_context.metadata)
            else:
                # New error
                self._active_errors[error_context.error_id] = error_context
    
    async def _attempt_recovery(self, error_context: ErrorContext) -> bool:
        """Attempt to recover from error using registered strategies."""
        strategies = self._recovery_strategies.get(error_context.category.value, [])
        
        for strategy in strategies:
            try:
                if await strategy.recover(error_context):
                    logger.info(f"Error recovered using strategy: {type(strategy).__name__}")
                    return True
            except Exception as e:
                logger.error(f"Recovery strategy failed: {e}")
        
        return False
    
    async def _notify_handlers(self, error_context: ErrorContext):
        """Notify all registered error handlers."""
        for handler in self._error_handlers:
            try:
                handler(error_context)
            except Exception as e:
                logger.error(f"Error handler failed: {e}")
    
    def _generate_error_id(self) -> str:
        """Generate unique error ID."""
        import uuid
        return str(uuid.uuid4())
    
    async def register_recovery_strategy(
        self,
        category: ErrorCategory,
        strategy: ErrorRecoveryStrategy
    ):
        """Register recovery strategy for error category."""
        self._recovery_strategies[category.value].append(strategy)
    
    async def register_error_handler(self, handler: Callable[[ErrorContext], None]):
        """Register error handler for notifications."""
        self._error_handlers.append(handler)
    
    def get_circuit_breaker(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0
    ) -> EnhancedCircuitBreaker:
        """Get or create circuit breaker."""
        if name not in self._circuit_breakers:
            self._circuit_breakers[name] = EnhancedCircuitBreaker(
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                name=name
            )
        return self._circuit_breakers[name]
    
    async def get_error_stats(self) -> Dict[str, Any]:
        """Get error statistics."""
        async with self._lock:
            total_errors = len(self._error_history)
            active_errors = len(self._active_errors)
            
            # Category breakdown
            category_counts = defaultdict(int)
            severity_counts = defaultdict(int)
            component_counts = defaultdict(int)
            
            recent_errors = [
                error for error in self._error_history
                if (datetime.now() - error.timestamp).total_seconds() < 3600  # Last hour
            ]
            
            for error in recent_errors:
                category_counts[error.category.value] += 1
                severity_counts[error.severity.value] += 1
                component_counts[error.component] += 1
            
            # Circuit breaker stats
            circuit_stats = {
                name: breaker.get_stats()
                for name, breaker in self._circuit_breakers.items()
            }
            
            return {
                'total_errors': total_errors,
                'active_errors': active_errors,
                'recent_errors_1h': len(recent_errors),
                'category_breakdown': dict(category_counts),
                'severity_breakdown': dict(severity_counts),
                'component_breakdown': dict(component_counts),
                'circuit_breakers': circuit_stats
            }
    
    async def resolve_error(self, error_id: str):
        """Mark an error as resolved."""
        async with self._lock:
            if error_id in self._active_errors:
                error = self._active_errors[error_id]
                error.resolved = True
                error.resolution_time = datetime.now()
                del self._active_errors[error_id]


# Decorators for easy error handling

def safe_execute(
    error_category: ErrorCategory = ErrorCategory.NORMAL,
    retry_policy: Optional[RetryPolicy] = None,
    circuit_breaker_name: Optional[str] = None,
    component: str = "",
    operation: str = ""
):
    """Decorator for safe execution with error handling."""
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[Union[T, None]]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Union[T, None]:
            from .performance_monitor import get_performance_monitor
            
            error_manager = get_error_manager()
            circuit_breaker = None
            
            if circuit_breaker_name:
                circuit_breaker = error_manager.get_circuit_breaker(circuit_breaker_name)
            
            attempt = 0
            max_attempts = retry_policy.max_attempts if retry_policy else 1
            
            while attempt < max_attempts:
                attempt += 1
                
                try:
                    # Execute with circuit breaker if available
                    if circuit_breaker:
                        return await circuit_breaker.call(func, *args, **kwargs)
                    else:
                        return await func(*args, **kwargs)
                
                except Exception as e:
                    # Handle the error
                    error_context = await error_manager.handle_error(
                        error=e,
                        component=component or func.__module__,
                        operation=operation or func.__name__,
                        metadata={
                            'function': func.__name__,
                            'attempt': attempt,
                            'max_attempts': max_attempts
                        },
                        attempt_recovery=False  # We handle retries manually
                    )
                    
                    # Record metrics
                    try:
                        monitor = await get_performance_monitor()
                        await monitor.metrics_collector.increment_counter(
                            "errors.count",
                            labels={
                                'category': error_context.category.value,
                                'severity': error_context.severity.value,
                                'component': error_context.component,
                                'operation': error_context.operation
                            }
                        )
                    except Exception:
                        pass  # Metrics recording shouldn't break error handling
                    
                    # Determine if we should retry
                    if attempt >= max_attempts:
                        logger.error(f"Function {func.__name__} failed after {attempt} attempts")
                        return None
                    
                    # Calculate delay for retry
                    if retry_policy:
                        delay = retry_policy.calculate_delay(attempt)
                        await asyncio.sleep(delay)
                    else:
                        await asyncio.sleep(1.0)
            
            return None
        
        return wrapper
    return decorator


def with_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0
):
    """Decorator for circuit breaker protection."""
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            error_manager = get_error_manager()
            circuit_breaker = error_manager.get_circuit_breaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout
            )
            
            return await circuit_breaker.call(func, *args, **kwargs)
        
        return wrapper
    return decorator


# Global error manager instance
_error_manager: Optional[ErrorManager] = None


def get_error_manager() -> ErrorManager:
    """Get global error manager instance."""
    global _error_manager
    if _error_manager is None:
        _error_manager = ErrorManager()
    return _error_manager


async def handle_error(
    error: Exception,
    component: str = "",
    operation: str = "",
    metadata: Optional[Dict[str, Any]] = None
) -> ErrorContext:
    """Convenience function for error handling."""
    error_manager = get_error_manager()
    return await error_manager.handle_error(error, component, operation, metadata)


# Default error handlers
async def log_error_handler(error_context: ErrorContext):
    """Default error handler that logs errors."""
    level = {
        ErrorSeverity.LOW: "info",
        ErrorSeverity.MEDIUM: "warning",
        ErrorSeverity.HIGH: "error",
        ErrorSeverity.CRITICAL: "critical"
    }.get(error_context.severity, "error")
    
    log_message = (
        f"[{error_context.category.value.upper()}] "
        f"{error_context.component}.{error_context.operation}: {error_context.message}"
    )
    
    if error_context.severity >= ErrorSeverity.HIGH:
        logger.error(log_message)
    else:
        logger.warning(log_message)


# Setup default handlers
async def setup_default_error_handlers():
    """Setup default error handlers."""
    error_manager = get_error_manager()
    await error_manager.register_error_handler(log_error_handler)


# Initialize default handlers on import
asyncio.create_task(setup_default_error_handlers())