# Trading Bot v2 - Development Guide

## Overview

This guide provides comprehensive development guidelines, architectural patterns, coding standards, and best practices for extending and maintaining the Trading Bot v2 system.

---

## Development Environment Setup

### Prerequisites

```bash
# Python 3.8 or higher
python3 --version

# Install development dependencies
pip install -r requirements-dev.txt
```

**requirements-dev.txt**:
```
-r requirements.txt
pytest>=7.4.0
pytest-asyncio>=0.21.0
pytest-cov>=4.1.0
black>=23.0.0
ruff>=0.1.0
mypy>=1.5.0
pre-commit>=3.4.0
```

### Pre-commit Hooks

```bash
# Install pre-commit
pip install pre-commit

# Setup hooks
pre-commit install

# Run manually
pre-commit run --all-files
```

**.pre-commit-config.yaml**:
```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.1.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.5.0
    hooks:
      - id: mypy
        additional_dependencies: [types-requests]
```

---

## Code Style Guidelines

### Python Conventions

```python
"""
Module docstring with brief description.

More detailed description of module functionality.
"""

# Standard library imports first
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# Third-party imports second
import aiohttp
from fastapi import FastAPI
from pydantic import BaseModel

# Local imports last
from .config import config
from .database import DatabaseManager


class MyClass:
    """
    Class docstring describing purpose.
    
    Attributes:
        attribute1: Description of attribute1
        attribute2: Description of attribute2
    """
    
    # Constants
    MAX_RETRIES: int = 3
    DEFAULT_TIMEOUT: float = 30.0
    
    def __init__(self, param1: str, param2: int = 10) -> None:
        """
        Initialize instance.
        
        Args:
            param1: Description of param1
            param2: Description of param2 (default: 10)
        """
        self.param1 = param1
        self.param2 = param2
        self._private_attr: Optional[str] = None
    
    async def public_method(self, arg: str) -> Dict[str, any]:
        """
        Public method description.
        
        Args:
            arg: Description of argument
            
        Returns:
            Dictionary containing results
            
        Raises:
            ValueError: When input is invalid
        """
        try:
            result = await self._async_operation(arg)
            return {'success': True, 'data': result}
        except Exception as e:
            logging.error(f"Operation failed: {e}")
            raise ValueError(f"Invalid input: {arg}")
    
    def _private_method(self) -> None:
        """Private method description."""
        pass
```

### Type Hints

```python
from typing import Dict, List, Optional, Union, Any
from dataclasses import dataclass

# Function type hints
def calculate_position_size(
    balance: float,
    risk_percent: float,
    stop_loss_pips: int
) -> float:
    """Calculate position size with type safety."""
    return (balance * risk_percent) / stop_loss_pips

# Async function type hints
async def fetch_market_data(
    symbol: str,
    timeframe: str = "1h"
) -> Dict[str, Any]:
    """Fetch market data with async type hints."""
    pass

# Complex types
PriceData = Dict[str, Union[float, int, str]]
PositionList = List[Dict[str, Any]]

# Optional parameters
def process_signal(
    signal: Dict[str, Any],
    validate: bool = True,
    metadata: Optional[Dict[str, str]] = None
) -> Optional[str]:
    """Process trading signal."""
    pass
```

### Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Classes | PascalCase | `TradingBot`, `GridManager` |
| Functions/Methods | snake_case | `calculate_position()`, `get_balance()` |
| Variables | snake_case | `current_price`, `position_size` |
| Constants | UPPER_CASE | `MAX_POSITIONS`, `DEFAULT_LEVERAGE` |
| Private members | _leading_underscore | `_internal_method()`, `_cache` |
| Modules | lowercase | `trading_bot.py`, `risk_manager.py` |

---

## Architecture Patterns

### Component-Based Architecture

```python
# component_interfaces.py
from abc import ABC, abstractmethod
from typing import Dict, Any

class ComponentInterface(ABC):
    """Base interface for all components."""
    
    @abstractmethod
    async def initialize(self) -> None:
        """Initialize component."""
        pass
    
    @abstractmethod
    async def get_status(self) -> Dict[str, Any]:
        """Get component status."""
        pass
    
    @abstractmethod
    async def shutdown(self) -> None:
        """Graceful shutdown."""
        pass

class TradingInterface(ComponentInterface):
    """Interface for trading operations."""
    
    @abstractmethod
    async def place_order(self, symbol: str, side: str, quantity: float) -> Dict[str, Any]:
        """Place trading order."""
        pass
```

### Event-Driven Communication

```python
# event_system.py
from typing import Callable, Dict, Any, List
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Event:
    """Event definition."""
    type: str
    data: Dict[str, Any]
    timestamp: datetime = datetime.now()
    source: str = ""

class EventBus:
    """Event bus for decoupled communication."""
    
    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = {}
    
    def subscribe(self, event_type: str, handler: Callable) -> None:
        """Subscribe to event type."""
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        self._subscribers[event_type].append(handler)
    
    async def publish(self, event: Event) -> None:
        """Publish event to subscribers."""
        handlers = self._subscribers.get(event.type, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                logging.error(f"Event handler failed: {e}")

# Usage
async def on_signal_generated(event: Event):
    """Handle signal generated event."""
    signal = event.data['signal']
    await process_signal(signal)

event_bus.subscribe('SIGNAL_GENERATED', on_signal_generated)
```

### Repository Pattern

```python
# repositories/position_repository.py
from abc import ABC, abstractmethod
from typing import List, Optional
from dataclasses import dataclass

@dataclass
class Position:
    symbol: str
    side: str
    quantity: float
    entry_price: float

class PositionRepository(ABC):
    """Abstract repository for positions."""
    
    @abstractmethod
    async def get_all(self) -> List[Position]:
        pass
    
    @abstractmethod
    async def get_by_symbol(self, symbol: str) -> Optional[Position]:
        pass
    
    @abstractmethod
    async def save(self, position: Position) -> None:
        pass

class SQLitePositionRepository(PositionRepository):
    """SQLite implementation."""
    
    def __init__(self, db: DatabaseManager):
        self.db = db
    
    async def get_all(self) -> List[Position]:
        """Get all open positions."""
        rows = await self.db.fetch_all(
            "SELECT * FROM positions WHERE status = 'open'"
        )
        return [Position(**row) for row in rows]
```

---

## Error Handling Patterns

### Structured Error Handling

```python
# error_handling.py
from enum import Enum
from dataclasses import dataclass
from typing import Optional

class ErrorCategory(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"

@dataclass
class ErrorContext:
    """Error context information."""
    message: str
    category: ErrorCategory
    component: str
    operation: str
    retry_count: int = 0
    original_error: Optional[Exception] = None

class TradingBotError(Exception):
    """Base exception for trading bot."""
    
    def __init__(
        self,
        message: str,
        category: ErrorCategory = ErrorCategory.NORMAL,
        component: str = "",
        operation: str = ""
    ):
        super().__init__(message)
        self.context = ErrorContext(
            message=message,
            category=category,
            component=component,
            operation=operation
        )

# Usage
try:
    await place_order(symbol, side, quantity)
except ConnectionError as e:
    raise TradingBotError(
        message="Failed to connect to exchange",
        category=ErrorCategory.HIGH,
        component="pacifica_client",
        operation="place_order",
        original_error=e
    )
```

### Circuit Breaker Pattern

```python
from enum import Enum
import time
from typing import Callable, Any
from functools import wraps

class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"         # Failing, rejecting calls
    HALF_OPEN = "half_open"  # Testing recovery

class CircuitBreaker:
    """Circuit breaker for external service calls."""
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_failure_time: Optional[float] = None
    
    def can_execute(self) -> bool:
        """Check if call is allowed."""
        if self.state == CircuitState.CLOSED:
            return True
        
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        
        return True  # HALF_OPEN
    
    def record_success(self) -> None:
        """Record successful call."""
        self.failures = 0
        self.state = CircuitState.CLOSED
    
    def record_failure(self) -> None:
        """Record failed call."""
        self.failures += 1
        self.last_failure_time = time.time()
        
        if self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logging.warning(f"Circuit breaker '{self.name}' opened")

def with_circuit_breaker(breaker: CircuitBreaker):
    """Decorator for circuit breaker protection."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            if not breaker.can_execute():
                raise TradingBotError(
                    f"Circuit breaker '{breaker.name}' is open",
                    category=ErrorCategory.HIGH
                )
            
            try:
                result = await func(*args, **kwargs)
                breaker.record_success()
                return result
            except Exception as e:
                breaker.record_failure()
                raise
        
        return wrapper
    return decorator
```

---

## Testing Guidelines

### Unit Testing

```python
# tests/test_trading_bot.py
import pytest
from unittest.mock import Mock, AsyncMock
from trading_bot import TradingBot

@pytest.fixture
def trading_bot():
    """Create trading bot instance for testing."""
    db = Mock()
    client = Mock()
    risk_manager = Mock()
    return TradingBot(db, client, risk_manager)

@pytest.mark.asyncio
async def test_place_order_success(trading_bot):
    """Test successful order placement."""
    # Arrange
    trading_bot.client.place_order = AsyncMock(return_value={'order_id': '123'})
    
    # Act
    result = await trading_bot.place_order('BTC/USD', 'buy', 0.5)
    
    # Assert
    assert result['order_id'] == '123'
    trading_bot.client.place_order.assert_called_once_with('BTC/USD', 'buy', 0.5)

@pytest.mark.asyncio
async def test_place_order_insufficient_balance(trading_bot):
    """Test order placement with insufficient balance."""
    # Arrange
    trading_bot.client.place_order = AsyncMock(
        side_effect=Exception("Insufficient balance")
    )
    
    # Act & Assert
    with pytest.raises(Exception) as exc_info:
        await trading_bot.place_order('BTC/USD', 'buy', 1000)
    
    assert "Insufficient balance" in str(exc_info.value)
```

### Integration Testing

```python
# tests/test_integration.py
import pytest
import asyncio
from httpx import AsyncClient
from api_server import app

@pytest.fixture
async def client():
    """Create test client."""
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

@pytest.mark.asyncio
async def test_api_status(client):
    """Test status endpoint."""
    response = await client.get("/api/status")
    assert response.status_code == 200
    assert response.json()["success"] is True

@pytest.mark.asyncio
async def test_bot_start_stop(client):
    """Test bot start and stop."""
    # Start bot
    response = await client.post("/api/bot/start")
    assert response.status_code == 200
    
    # Check status
    response = await client.get("/api/status")
    assert response.json()["data"]["is_running"] is True
    
    # Stop bot
    response = await client.post("/api/bot/stop")
    assert response.status_code == 200
```

### Performance Testing

```python
# tests/test_performance.py
import pytest
import asyncio
import time
from performance_monitor import get_performance_monitor

@pytest.mark.asyncio
async def test_task_queue_throughput():
    """Test task processing throughput."""
    from task_queue_system import get_task_queue_system
    
    task_queue = await get_task_queue_system()
    await task_queue.start()
    
    # Submit 100 tasks
    start_time = time.time()
    task_ids = []
    for i in range(100):
        task_id = await task_queue.submit_task(
            lambda x: x * 2,
            i,
            priority=TaskPriority.NORMAL
        )
        task_ids.append(task_id)
    
    # Wait for completion
    await asyncio.sleep(2)
    
    execution_time = time.time() - start_time
    throughput = 100 / execution_time
    
    assert throughput > 50  # At least 50 tasks per second
```

---

## Database Development

### Migration Pattern

```python
# migrations/001_add_performance_metrics.py
import sqlite3
from pathlib import Path

MIGRATION = """
-- Migration: Add performance metrics table

CREATE TABLE IF NOT EXISTS performance_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    metric_name VARCHAR(100) NOT NULL,
    metric_value REAL NOT NULL,
    labels TEXT
);

CREATE INDEX IF NOT EXISTS idx_metrics_timestamp 
    ON performance_metrics(timestamp);

CREATE INDEX IF NOT EXISTS idx_metrics_name 
    ON performance_metrics(metric_name);
"""

def migrate():
    """Execute migration."""
    db_path = Path(__file__).parent.parent / "data" / "trading_bot.db"
    
    with sqlite3.connect(db_path) as conn:
        conn.executescript(MIGRATION)
        conn.commit()
        print("Migration 001 completed successfully")

if __name__ == "__main__":
    migrate()
```

### Database Query Best Practices

```python
# Good: Parameterized query
async def get_position(self, symbol: str) -> Optional[dict]:
    """Get position with parameterized query."""
    query = "SELECT * FROM positions WHERE symbol = ? AND status = 'open'"
    async with self.conn.execute(query, (symbol,)) as cursor:
        return await cursor.fetchone()

# Bad: String concatenation (SQL injection risk)
async def get_position_bad(self, symbol: str) -> Optional[dict]:
    """DON'T DO THIS - SQL injection vulnerability."""
    query = f"SELECT * FROM positions WHERE symbol = '{symbol}'"
    # Never use f-strings for SQL queries!
```

---

## Git Workflow

### Branch Naming

```
feature/add-new-strategy
bugfix/fix-memory-leak
hotfix/critical-api-issue
refactor/extract-components
docs/update-readme
```

### Commit Message Format

```
type(scope): subject

body (optional)

footer (optional)
```

**Types**:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Code style (formatting)
- `refactor`: Code refactoring
- `test`: Tests
- `chore`: Maintenance

**Examples**:
```
feat(trading): add momentum scalping strategy

Implement momentum-based scalping strategy using 1m candles.
Includes stop-loss and take-profit logic.

fix(api): resolve rate limiting issues

Add exponential backoff for API retries to handle
rate limit errors gracefully.

docs(readme): update installation instructions

Add detailed steps for Windows and macOS installation.
```

### Pull Request Template

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Unit tests added/updated
- [ ] Integration tests pass
- [ ] Manual testing completed

## Checklist
- [ ] Code follows style guidelines
- [ ] Self-review completed
- [ ] Documentation updated
- [ ] No new warnings

## Screenshots (if applicable)
```

---

## Performance Optimization

### Async Best Practices

```python
# Good: Concurrent execution
async def fetch_multiple_symbols(symbols: List[str]) -> Dict[str, Any]:
    """Fetch data for multiple symbols concurrently."""
    tasks = [fetch_symbol_data(symbol) for symbol in symbols]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return dict(zip(symbols, results))

# Bad: Sequential execution
async def fetch_multiple_symbols_bad(symbols: List[str]) -> Dict[str, Any]:
    """DON'T DO THIS - too slow."""
    results = {}
    for symbol in symbols:  # Sequential - slow!
        results[symbol] = await fetch_symbol_data(symbol)
    return results
```

### Memory Management

```python
# Use generators for large datasets
def get_trades_generator(self):
    """Yield trades one at a time to save memory."""
    cursor = self.conn.execute("SELECT * FROM trades")
    while True:
        rows = cursor.fetchmany(1000)
        if not rows:
            break
        for row in rows:
            yield row

# Clear caches periodically
class DataCache:
    def __init__(self, ttl: int = 300):
        self.cache = {}
        self.timestamps = {}
        self.ttl = ttl
    
    async def cleanup_expired(self):
        """Remove expired cache entries."""
        current_time = time.time()
        expired = [
            key for key, timestamp in self.timestamps.items()
            if current_time - timestamp > self.ttl
        ]
        for key in expired:
            del self.cache[key]
            del self.timestamps[key]
```

---

## Debugging Tips

### Logging

```python
import logging
from loguru import logger

# Configure structured logging
logger.add(
    "logs/trading_bot_{time}.log",
    rotation="1 day",
    retention="30 days",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {name} | {message}"
)

# Usage
logger.info("Starting trading bot")
logger.warning("High memory usage: {}%", memory_percent)
logger.error("Trade failed: {}", error_message)
```

### Debugging with pdb

```python
import pdb

async def complex_operation():
    result = await step_one()
    
    # Set breakpoint
    pdb.set_trace()
    
    result = await step_two(result)
    return result
```

### Profiling

```python
import cProfile
import pstats

def profile_function():
    profiler = cProfile.Profile()
    profiler.enable()
    
    # Code to profile
    result = heavy_computation()
    
    profiler.disable()
    stats = pstats.Stats(profiler)
    stats.sort_stats('cumulative')
    stats.print_stats(20)  # Top 20 functions
    
    return result
```

---

**For more information, see [API_DOCUMENTATION.md](./API_DOCUMENTATION.md) and [TESTING.md](./TESTING.md).**
