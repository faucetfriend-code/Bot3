"""
Queue-based task processing system for trading bot v2.

This module implements a priority-based queue system to eliminate synchronous
bottlenecks and enable efficient task processing with proper error handling,
recovery mechanisms, and performance optimization.

Key Features:
- Priority-based task scheduling (CRITICAL, HIGH, NORMAL, LOW)
- Async task processing with load balancing
- Task result caching and deduplication
- Timeout and retry mechanisms
- Task cancellation and cleanup procedures
- Performance monitoring and metrics collection
"""

import asyncio
import time
import uuid
import threading
from collections import deque, defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import (
    Any, Callable, Dict, List, Optional, Set, Tuple, Union, 
    AsyncGenerator, Awaitable, TypeVar, Generic
)
import logging
from loguru import logger
import weakref
import psutil
import json

# Import enhanced components when available
try:
    from .performance_monitor import PerformanceMonitor
    from .error_handling import (
        ErrorCategory, EnhancedCircuitBreaker, RetryPolicy,
        TaskError, TimeoutError, CancellationError
    )
except ImportError:
    # Fallback implementations for standalone usage
    PerformanceMonitor = None
    ErrorCategory = None
    EnhancedCircuitBreaker = None
    
    class RetryPolicy:
        def __init__(self, max_attempts=3, base_delay=1.0, max_delay=60.0, **kwargs):
            self.max_attempts = max_attempts
            self.base_delay = base_delay
            self.max_delay = max_delay
        
        def calculate_delay(self, attempt):
            delay = self.base_delay * (2 ** (attempt - 1))
            return min(delay, self.max_delay)
        
        @classmethod
        def conservative(cls):
            return cls(max_attempts=5, base_delay=2.0, max_delay=300.0)
        
        @classmethod
        def aggressive(cls):
            return cls(max_attempts=10, base_delay=0.5, max_delay=30.0)
    
    class TaskError(Exception):
        def __init__(self, message, task_id=None, **kwargs):
            super().__init__(message)
            self.task_id = task_id
    
    class TimeoutError(Exception):
        pass
    
    class CancellationError(Exception):
        pass

# Type variables for generic task handling
T = TypeVar('T')
R = TypeVar('R')


class TaskPriority(Enum):
    """Priority levels for task scheduling."""
    CRITICAL = 4  # Trading operations (place/cancel orders)
    HIGH = 3      # Market data updates needed for trading decisions
    NORMAL = 2    # Background processing and maintenance
    LOW = 1       # Statistics, logging, and cleanup tasks


class TaskStatus(Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class TaskResult:
    """Result of task execution with metadata."""
    task_id: str
    status: TaskStatus
    result: Any = None
    error: Optional[Exception] = None
    execution_time: float = 0.0
    retry_count: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            'task_id': self.task_id,
            'status': self.status.value,
            'result': self.result,
            'error': str(self.error) if self.error else None,
            'execution_time': self.execution_time,
            'retry_count': self.retry_count,
            'created_at': self.created_at.isoformat(),
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'metadata': self.metadata
        }


@dataclass
class Task(Generic[T, R]):
    """Task definition with execution metadata."""
    task_id: str
    func: Callable[..., Awaitable[R]]
    args: Tuple = field(default_factory=tuple)
    kwargs: Dict[str, Any] = field(default_factory=dict)
    priority: TaskPriority = TaskPriority.NORMAL
    timeout: Optional[float] = None
    retry_policy: Optional[RetryPolicy] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    scheduled_at: Optional[datetime] = None
    dependencies: Set[str] = field(default_factory=set)
    
    # Runtime fields
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[TaskResult] = None
    retry_count: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    def __hash__(self) -> int:
        return hash(self.task_id)
    
    def __lt__(self, other: 'Task') -> bool:
        """Tasks are ordered by priority and creation time."""
        if self.priority.value != other.priority.value:
            return self.priority.value > other.priority.value  # Higher priority first
        return self.created_at < other.created_at  # Earlier tasks first


class TaskDeduplicator:
    """Prevents duplicate tasks from being queued."""
    
    def __init__(self, ttl: int = 300):  # 5 minutes TTL
        self._recent_tasks: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self.ttl = ttl
    
    async def is_duplicate(self, task_key: str) -> bool:
        """Check if task is a duplicate within TTL."""
        async with self._lock:
            current_time = time.time()
            
            # Clean expired entries
            expired_keys = [
                key for key, timestamp in self._recent_tasks.items()
                if current_time - timestamp > self.ttl
            ]
            for key in expired_keys:
                del self._recent_tasks[key]
            
            # Check if current task is duplicate
            if task_key in self._recent_tasks:
                return True
            
            # Register new task
            self._recent_tasks[task_key] = current_time
            return False
    
    def generate_task_key(self, task: Task) -> str:
        """Generate unique key for task deduplication."""
        func_name = getattr(task.func, '__name__', 'unknown')
        args_key = str(hash(str(task.args)))
        kwargs_key = str(hash(str(sorted(task.kwargs.items()))))
        return f"{func_name}:{args_key}:{kwargs_key}"


class TaskResultCache:
    """Caches task results to avoid redundant computations."""
    
    def __init__(self, max_size: int = 1000, default_ttl: int = 300):
        self._cache: Dict[str, Tuple[TaskResult, float]] = {}
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._lock = asyncio.Lock()
    
    async def get(self, task_key: str) -> Optional[TaskResult]:
        """Get cached result if valid."""
        async with self._lock:
            if task_key in self._cache:
                result, expiry_time = self._cache[task_key]
                if time.time() < expiry_time:
                    return result
                else:
                    del self._cache[task_key]
            return None
    
    async def set(self, task_key: str, result: TaskResult, ttl: Optional[int] = None):
        """Cache task result."""
        async with self._lock:
            # Remove oldest entries if cache is full
            if len(self._cache) >= self._max_size:
                oldest_key = min(
                    self._cache.keys(),
                    key=lambda k: self._cache[k][1]  # Sort by expiry time
                )
                del self._cache[oldest_key]
            
            cache_ttl = ttl or self._default_ttl
            expiry_time = time.time() + cache_ttl
            self._cache[task_key] = (result, expiry_time)
    
    async def clear(self):
        """Clear all cached results."""
        async with self._lock:
            self._cache.clear()
    
    async def cleanup_expired(self):
        """Remove expired entries."""
        async with self._lock:
            current_time = time.time()
            expired_keys = [
                key for key, (_, expiry_time) in self._cache.items()
                if current_time >= expiry_time
            ]
            for key in expired_keys:
                del self._cache[key]


class TaskExecutor:
    """Executes tasks with timeout, retry, and error handling."""
    
    def __init__(self, max_concurrent_tasks: int = 10):
        self.max_concurrent_tasks = max_concurrent_tasks
        self._semaphore = asyncio.Semaphore(max_concurrent_tasks)
        self._active_tasks: Dict[str, asyncio.Task] = {}
        if EnhancedCircuitBreaker:
            self._circuit_breaker = EnhancedCircuitBreaker(
                failure_threshold=5,
                recovery_timeout=30,
                expected_exception=Exception
            )
        else:
            self._circuit_breaker = None
        
        self._performance_monitor = PerformanceMonitor() if PerformanceMonitor else None
    
    async def execute(self, task: Task) -> TaskResult:
        """Execute a task with proper error handling and monitoring."""
        task_id = task.task_id
        start_time = time.time()
        
        try:
            # Check circuit breaker
            if self._circuit_breaker and hasattr(self._circuit_breaker, 'can_execute'):
                if not self._circuit_breaker.can_execute():
                    raise TaskError("Circuit breaker is open", task_id=task_id)
            
            # Acquire semaphore for concurrency control
            async with self._semaphore:
                task.status = TaskStatus.RUNNING
                task.started_at = datetime.now()
                
                # Create execution task
                execution_task = asyncio.create_task(
                    self._execute_with_timeout(task)
                )
                self._active_tasks[task_id] = execution_task
                
                try:
                    # Execute with timeout
                    result = await execution_task
                    task.result = result
                    if self._circuit_breaker and hasattr(self._circuit_breaker, 'record_success'):
                        self._circuit_breaker.record_success()
                        
                except asyncio.TimeoutError:
                    task.status = TaskStatus.TIMEOUT
                    raise TimeoutError(f"Task {task_id} timed out")
                
                except Exception as e:
                    task.status = TaskStatus.FAILED
                    if self._circuit_breaker and hasattr(self._circuit_breaker, 'record_failure'):
                        self._circuit_breaker.record_failure()
                    raise TaskError(f"Task {task_id} failed: {str(e)}", task_id=task_id)
                
                finally:
                    self._active_tasks.pop(task_id, None)
                    task.completed_at = datetime.now()
                    execution_time = time.time() - start_time
                    task.result.execution_time = execution_time
                    
                    # Record performance metrics
                    if self._performance_monitor and hasattr(self._performance_monitor, 'metrics_collector'):
                        await self._performance_monitor.metrics_collector.record_timer(
                            "tasks.execution.time", execution_time,
                            labels={'status': task.status.value}
                        )
                
                return task.result
                
        except Exception as e:
            task.completed_at = datetime.now()
            execution_time = time.time() - start_time
            
            error_result = TaskResult(
                task_id=task_id,
                status=TaskStatus.FAILED,
                error=e,
                execution_time=execution_time,
                created_at=task.created_at,
                completed_at=task.completed_at,
                metadata=task.metadata
            )
            
            task.result = error_result
            return task.result or error_result
    
    async def _execute_with_timeout(self, task: Task) -> TaskResult:
        """Execute task with timeout and retry logic."""
        timeout = task.timeout or 60.0  # Default 60 second timeout
        
        while True:
            try:
                # Execute task with timeout
                if asyncio.iscoroutinefunction(task.func):
                    result = await asyncio.wait_for(
                        task.func(*task.args, **task.kwargs),
                        timeout=timeout
                    )
                else:
                    # Handle synchronous functions
                    loop = asyncio.get_event_loop()
                    result = await asyncio.wait_for(
                        loop.run_in_executor(None, task.func, *task.args, **task.kwargs),
                        timeout=timeout
                    )
                
                # Create successful result
                task_result = TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.COMPLETED,
                    result=result,
                    execution_time=time.time(),
                    retry_count=task.retry_count,
                    created_at=task.created_at,
                    completed_at=datetime.now(),
                    metadata=task.metadata
                )
                
                return task_result
                
            except Exception as e:
                # Handle retry logic
                if task.retry_policy and task.retry_count < task.retry_policy.max_attempts:
                    task.retry_count += 1
                    delay = task.retry_policy.calculate_delay(task.retry_count)
                    logger.warning(
                        f"Task {task.task_id} failed (attempt {task.retry_count}), "
                        f"retrying in {delay:.2f}s: {str(e)}"
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    raise e
    
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        if task_id in self._active_tasks:
            task = self._active_tasks[task_id]
            task.cancel()
            return True
        return False
    
    async def get_active_task_count(self) -> int:
        """Get count of currently executing tasks."""
        return len(self._active_tasks)
    
    async def shutdown(self):
        """Shutdown executor and cancel all active tasks."""
        for task_id, task in self._active_tasks.items():
            task.cancel()
            logger.info(f"Cancelled task {task_id} during shutdown")
        
        if self._active_tasks:
            await asyncio.gather(*self._active_tasks.values(), return_exceptions=True)
        
        self._active_tasks.clear()


class TaskQueue:
    """Priority-based task queue with dependency management."""
    
    def __init__(self, max_size: int = 10000):
        self._queues: Dict[TaskPriority, deque] = {
            priority: deque() for priority in TaskPriority
        }
        self._tasks: Dict[str, Task] = {}
        self._dependencies: Dict[str, Set[str]] = defaultdict(set)
        self._dependents: Dict[str, Set[str]] = defaultdict(set)
        self._max_size = max_size
        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Condition(self._lock)
    
    async def put(self, task: Task) -> bool:
        """Add task to queue respecting priority and size limits."""
        async with self._lock:
            if len(self._tasks) >= self._max_size:
                logger.warning(f"Task queue is full (max_size={self._max_size})")
                return False
            
            if task.task_id in self._tasks:
                logger.warning(f"Task {task.task_id} already exists in queue")
                return False
            
            # Add to appropriate priority queue
            self._queues[task.priority].append(task)
            self._tasks[task.task_id] = task
            
            # Register dependencies
            for dep_id in task.dependencies:
                self._dependencies[task.task_id].add(dep_id)
                self._dependents[dep_id].add(task.task_id)
            
            # Notify waiting consumers
            self._not_empty.notify()
            
            return True
    
    async def get(self) -> Optional[Task]:
        """Get next available task respecting dependencies."""
        async with self._not_empty:
            while True:
                # Find highest priority task with all dependencies satisfied
                for priority in sorted(TaskPriority, key=lambda p: p.value, reverse=True):
                    queue = self._queues[priority]
                    
                    for i, task in enumerate(queue):
                        if self._dependencies_satisfied(task):
                            # Remove from queue and return
                            queue.remove(task)
                            return task
                
                # No available tasks, wait for notification
                await self._not_empty.wait()
    
    def _dependencies_satisfied(self, task: Task) -> bool:
        """Check if all task dependencies are satisfied."""
        for dep_id in task.dependencies:
            if dep_id not in self._tasks:
                continue  # Dependency not in queue, assume satisfied
            dep_task = self._tasks[dep_id]
            if dep_task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                return False
        return True
    
    async def remove(self, task_id: str) -> bool:
        """Remove task from queue."""
        async with self._lock:
            if task_id not in self._tasks:
                return False
            
            task = self._tasks[task_id]
            
            # Remove from priority queue
            self._queues[task.priority].remove(task)
            
            # Clean up dependencies
            for dep_id in self._dependencies[task_id]:
                self._dependents[dep_id].discard(task_id)
            
            for dependent_id in self._dependents[task_id]:
                self._dependencies[dependent_id].discard(task_id)
            
            del self._tasks[task_id]
            del self._dependencies[task_id]
            del self._dependents[task_id]
            
            return True
    
    async def size(self) -> int:
        """Get total number of tasks in queue."""
        async with self._lock:
            return len(self._tasks)
    
    async def get_stats(self) -> Dict[str, int]:
        """Get queue statistics by priority."""
        async with self._lock:
            stats = {
                'total': len(self._tasks),
                'critical': len(self._queues[TaskPriority.CRITICAL]),
                'high': len(self._queues[TaskPriority.HIGH]),
                'normal': len(self._queues[TaskPriority.NORMAL]),
                'low': len(self._queues[TaskPriority.LOW])
            }
            return stats


class TaskQueueSystem:
    """Main task queue system orchestrating all components."""
    
    def __init__(
        self,
        max_concurrent_tasks: int = 10,
        max_queue_size: int = 10000,
        cache_size: int = 1000,
        enable_deduplication: bool = True
    ):
        self.max_concurrent_tasks = max_concurrent_tasks
        
        # Core components
        self._queue = TaskQueue(max_queue_size)
        self._executor = TaskExecutor(max_concurrent_tasks)
        self._deduplicator = TaskDeduplicator() if enable_deduplication else None
        self._result_cache = TaskResultCache(max_size=cache_size)
        
        # Management
        self._running = False
        self._worker_tasks: List[asyncio.Task] = []
        self._completion_handlers: Dict[str, List[Callable]] = defaultdict(list)
        self._performance_monitor = None  # Will be initialized separately to avoid circular import
        
        # Statistics
        self._stats = {
            'tasks_processed': 0,
            'tasks_failed': 0,
            'tasks_cancelled': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'duplicate_tasks_prevented': 0
        }
    
    async def start(self, num_workers: int = None):
        """Start the task queue system with worker processes."""
        if self._running:
            logger.warning("Task queue system is already running")
            return
        
        self._running = True
        num_workers = num_workers or self.max_concurrent_tasks
        
        logger.info(f"Starting task queue system with {num_workers} workers")
        
        # Start worker tasks
        self._worker_tasks = [
            asyncio.create_task(self._worker_loop(f"worker-{i}"))
            for i in range(num_workers)
        ]
        
        # Start maintenance tasks
        asyncio.create_task(self._maintenance_loop())
        
        logger.info("Task queue system started successfully")
    
    async def stop(self):
        """Stop the task queue system gracefully."""
        if not self._running:
            return
        
        logger.info("Stopping task queue system...")
        self._running = False
        
        # Cancel worker tasks
        for worker_task in self._worker_tasks:
            worker_task.cancel()
        
        # Wait for workers to finish
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        
        # Shutdown executor
        await self._executor.shutdown()
        
        logger.info("Task queue system stopped")
    
    async def submit_task(
        self,
        func: Callable[..., Awaitable[R]],
        *args,
        priority: TaskPriority = TaskPriority.NORMAL,
        timeout: Optional[float] = None,
        retry_policy: Optional[RetryPolicy] = None,
        metadata: Optional[Dict[str, Any]] = None,
        dependencies: Optional[Set[str]] = None,
        deduplication_key: Optional[str] = None
    ) -> str:
        """Submit a task for execution."""
        task_id = str(uuid.uuid4())
        
        task = Task(
            task_id=task_id,
            func=func,
            args=args,
            priority=priority,
            timeout=timeout,
            retry_policy=retry_policy,
            metadata=metadata or {},
            dependencies=dependencies or set()
        )
        
        # Check deduplication
        task_key = None
        if self._deduplicator:
            task_key = deduplication_key or self._deduplicator.generate_task_key(task)
            if await self._deduplicator.is_duplicate(task_key):
                self._stats['duplicate_tasks_prevented'] += 1
                logger.debug(f"Duplicate task prevented: {task_key}")
                return task_id
        
        # Check result cache
        if self._deduplicator and task_key:
            cached_result = await self._result_cache.get(task_key)
            if cached_result:
                self._stats['cache_hits'] += 1
                logger.debug(f"Returning cached result for task: {task_key}")
                return task_id
            else:
                self._stats['cache_misses'] += 1
        
        # Add to queue
        success = await self._queue.put(task)
        if not success:
            raise TaskError(f"Failed to add task {task_id} to queue", task_id=task_id)
        
        logger.debug(f"Task {task_id} submitted with priority {priority.name}")
        return task_id
    
    async def get_task_result(self, task_id: str) -> Optional[TaskResult]:
        """Get result of a completed task."""
        # Try cache first
        if self._deduplicator:
            # For now, we don't store completed tasks in cache
            # This could be enhanced to cache results by task_id
            pass
        
        # The task result would be available through completion handlers
        # or stored in a results database - implementation dependent
        return None
    
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a pending or running task."""
        # Try to remove from queue first
        if await self._queue.remove(task_id):
            logger.info(f"Task {task_id} removed from queue")
            return True
        
        # Try to cancel running task
        if await self._executor.cancel_task(task_id):
            logger.info(f"Task {task_id} cancelled during execution")
            return True
        
        return False
    
    async def get_queue_stats(self) -> Dict[str, Any]:
        """Get comprehensive queue statistics."""
        queue_stats = await self._queue.get_stats()
        active_tasks = await self._executor.get_active_task_count()
        
        return {
            'queue': queue_stats,
            'active_tasks': active_tasks,
            'stats': self._stats.copy(),
            'performance': await self._performance_monitor.get_summary() if self._performance_monitor else {},
            'system': {
                'cpu_usage': psutil.cpu_percent(),
                'memory_usage': psutil.virtual_memory().percent,
                'workers_active': len(self._worker_tasks)
            }
        }
    
    async def _worker_loop(self, worker_name: str):
        """Main worker loop processing tasks."""
        logger.info(f"Worker {worker_name} started")
        
        while self._running:
            try:
                # Get next task from queue
                task = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                
                if task is None:
                    continue
                
                logger.debug(f"Worker {worker_name} processing task {task.task_id}")
                
                # Execute task
                result = await self._executor.execute(task)
                
                # Update statistics
                if result.status == TaskStatus.COMPLETED:
                    self._stats['tasks_processed'] += 1
                elif result.status == TaskStatus.FAILED:
                    self._stats['tasks_failed'] += 1
                elif result.status == TaskStatus.CANCELLED:
                    self._stats['tasks_cancelled'] += 1
                
                # Cache result if deduplication is enabled
                if self._deduplicator and result.status == TaskStatus.COMPLETED:
                    task_key = self._deduplicator.generate_task_key(task)
                    await self._result_cache.set(task_key, result)
                
                # Call completion handlers
                for handler in self._completion_handlers.get(task.task_id, []):
                    try:
                        await handler(result)
                    except Exception as e:
                        logger.error(f"Completion handler failed for task {task.task_id}: {e}")
                
                logger.debug(f"Worker {worker_name} completed task {task.task_id}")
                
            except asyncio.TimeoutError:
                # No task available, continue
                continue
            except Exception as e:
                logger.error(f"Worker {worker_name} error: {e}")
                await asyncio.sleep(0.1)  # Brief pause before continuing
    
    async def _maintenance_loop(self):
        """Maintenance loop for cleanup and optimization."""
        while self._running:
            try:
                # Clean up expired cache entries
                await self._result_cache.cleanup_expired()
                
                # Collect performance metrics
                if self._performance_monitor and hasattr(self._performance_monitor, 'collect_system_metrics'):
                    await self._performance_monitor.collect_system_metrics()
                
                # Sleep before next maintenance cycle
                await asyncio.sleep(60)  # Run maintenance every minute
                
            except Exception as e:
                logger.error(f"Maintenance loop error: {e}")
                await asyncio.sleep(60)


# Global task queue system instance
_task_queue_system: Optional[TaskQueueSystem] = None


async def get_task_queue_system() -> TaskQueueSystem:
    """Get or create global task queue system instance."""
    global _task_queue_system
    if _task_queue_system is None:
        _task_queue_system = TaskQueueSystem()
    return _task_queue_system


async def submit_trading_task(
    func: Callable[..., Awaitable[R]],
    *args,
    priority: TaskPriority = TaskPriority.CRITICAL,
    timeout: float = 30.0,
    **kwargs
) -> str:
    """Submit a trading-related task with high priority."""
    queue_system = await get_task_queue_system()
    return await queue_system.submit_task(
        func, *args,
        priority=priority,
        timeout=timeout,
        **kwargs
    )


async def submit_market_data_task(
    func: Callable[..., Awaitable[R]],
    *args,
    priority: TaskPriority = TaskPriority.HIGH,
    timeout: float = 10.0,
    **kwargs
) -> str:
    """Submit a market data task with high priority."""
    queue_system = await get_task_queue_system()
    return await queue_system.submit_task(
        func, *args,
        priority=priority,
        timeout=timeout,
        **kwargs
    )


async def submit_background_task(
    func: Callable[..., Awaitable[R]],
    *args,
    priority: TaskPriority = TaskPriority.NORMAL,
    timeout: float = 60.0,
    **kwargs
) -> str:
    """Submit a background maintenance task."""
    queue_system = await get_task_queue_system()
    return await queue_system.submit_task(
        func, *args,
        priority=priority,
        timeout=timeout,
        **kwargs
    )