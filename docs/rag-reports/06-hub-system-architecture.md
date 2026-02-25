# Hub System Architecture

<!--
RAG Metadata:
- Category: Architecture
- Tags: hub-system, data-hub, circuit-breaker, event-system, component-registry, interfaces
- Related: 01-core-trading-logic, 05-data-management, 08-web-interface-api
-->

## Overview

The Hub System provides centralized data management, component communication, and resilience patterns:

1. **DataHub** - Thread-safe data management
2. **ConnectionManager** - WebSocket pooling
3. **Circuit Breaker** - Failure protection
4. **Event System** - Decoupled communication
5. **Component Registry** - Discovery and health monitoring

---

## DataHub

**Location**: `trading_bot_v2/hub_system.py`

### Purpose
Centralized hub for managing trading bot data and communication.

### Features
- Thread-safe data access with locks
- Synchronization primitives (semaphores, barriers)
- Readiness flags for components
- Circuit breaker for database operations
- Price cache management

### Class Definition

```python
class DataHub:
    """Centralized data hub for managing trading bot data and communication."""

    def __init__(self, config: Any, database: Any, 
                 trading_bot: Any, risk_manager: Any):
        self.config = config
        self.database = database
        self.trading_bot = trading_bot
        self.risk_manager = risk_manager

        # Synchronization primitives
        self._cache_lock = asyncio.Lock()
        self._db_lock = asyncio.Lock()
        self._broadcast_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(10)  # Limit concurrent ops
        self._barrier = asyncio.Barrier(3)  # Startup sync

        # Readiness flags
        self._cache_ready = asyncio.Event()
        self._db_ready = asyncio.Event()
        self._bot_ready = asyncio.Event()

        # Circuit breaker (thread-safe)
        self._circuit_breaker_lock = threading.Lock()
        self._db_circuit_breaker_failures = 0
        self._db_circuit_breaker_threshold = 5
        self._db_circuit_breaker_timeout = 60.0
```

### Key Methods

```python
async def initialize_cache(self):
    """Initialize data cache from database and WebSocket sources."""

async def wait_for_readiness(self):
    """Wait for all components to be ready."""
    await asyncio.gather(
        self._cache_ready.wait(),
        self._db_ready.wait(),
        self._bot_ready.wait()
    )

def mark_component_ready(self, component: str):
    """Mark a component as ready (database, bot, cache)."""

async def get_positions(self) -> List[Dict[str, Any]]:
    """Get positions with circuit breaker protection."""

async def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
    """Get trades with circuit breaker protection."""

async def get_price_cache(self) -> Dict[str, float]:
    """Get current price cache."""
```

### Circuit Breaker Implementation

```python
async def _check_circuit_breaker(self) -> bool:
    """Check if circuit breaker allows operation (thread-safe)."""
    with self._circuit_breaker_lock:
        current_time = time.time()

        if self._db_circuit_breaker_failures >= self._db_circuit_breaker_threshold:
            time_diff = current_time - self._db_last_failure_time
            if time_diff < self._db_circuit_breaker_timeout:
                return False  # Circuit breaker open
            else:
                # Reset after timeout
                self._db_circuit_breaker_failures = 0
                return True
        return True

def _record_failure(self):
    """Record a failure for circuit breaker."""
    with self._circuit_breaker_lock:
        self._db_circuit_breaker_failures += 1
        self._db_last_failure_time = time.time()

async def _execute_with_retry(self, func, *args, max_retries: int = 3, 
                               base_delay: float = 1.0):
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
            delay = base_delay * (2 ** attempt)
            await asyncio.sleep(delay)
```

---

## ConnectionManager

**Location**: `trading_bot_v2/hub_system.py`

### Purpose
Manages WebSocket connections for real-time data distribution.

### Features
- Connection pooling (max 100 connections)
- Authentication support
- Performance monitoring
- Graceful disconnect handling

### Class Definition

```python
class ConnectionManager:
    """Manages WebSocket connections for real-time data distribution."""

    def __init__(self, data_hub: Optional[DataHub] = None, 
                 max_connections: int = 100):
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(max_connections)
        self.data_hub = data_hub
        
        # Performance monitoring
        self._message_count = 0
        self._broadcast_times: List[float] = []
        self._error_count = 0
```

### Key Methods

```python
async def connect(self, websocket: WebSocket):
    """Accept new WebSocket connection."""

async def disconnect(self, websocket: WebSocket):
    """Handle WebSocket disconnect."""

async def broadcast(self, message: dict):
    """Broadcast message to all connected clients."""

async def send_personal(self, message: dict, websocket: WebSocket):
    """Send message to specific client."""

async def authenticate_connection(self, websocket: WebSocket) -> bool:
    """Authenticate WebSocket connection."""
```

---

## Event System

**Location**: `trading_bot_v2/event_system.py`

### Purpose
Decoupled communication between components using pub/sub pattern.

### Event Types

```python
class EventType(Enum):
    # Position events
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    POSITION_UPDATED = "position_updated"
    
    # Order events
    ORDER_PLACED = "order_placed"
    ORDER_FILLED = "order_filled"
    ORDER_CANCELLED = "order_cancelled"
    
    # Signal events
    SIGNAL_GENERATED = "signal_generated"
    SIGNAL_EXECUTED = "signal_executed"
    
    # Market events
    REGIME_CHANGED = "regime_changed"
    PRICE_UPDATE = "price_update"
    
    # System events
    BOT_STARTED = "bot_started"
    BOT_STOPPED = "bot_stopped"
    ERROR_OCCURRED = "error_occurred"
    CIRCUIT_BREAKER_TRIGGERED = "circuit_breaker_triggered"
```

### EventBus

```python
class EventBus:
    """Central event bus for component communication."""

    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = defaultdict(list)
        self._event_queue: asyncio.Queue = asyncio.Queue()

    def subscribe(self, event_type: EventType, callback: Callable):
        """Subscribe to event type."""
        self._subscribers[event_type].append(callback)

    async def publish(self, event_type: EventType, data: Any):
        """Publish event to all subscribers."""
        event = Event(type=event_type, data=data, timestamp=datetime.now())
        await self._event_queue.put(event)

    async def start_processing(self):
        """Start processing event queue."""
        while True:
            event = await self._event_queue.get()
            for callback in self._subscribers[event.type]:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(event)
                    else:
                        callback(event)
                except Exception as e:
                    logger.error(f"Event callback error: {e}")

# Singleton accessor
def get_event_bus() -> EventBus:
    """Get singleton EventBus instance."""
```

### Usage Example

```python
# Subscribe to position events
async def on_position_opened(event: Event):
    logger.info(f"Position opened: {event.data}")
    # Update UI, send notification, etc.

event_bus = get_event_bus()
event_bus.subscribe(EventType.POSITION_OPENED, on_position_opened)

# Publish event
await event_bus.publish(
    EventType.POSITION_OPENED,
    {'symbol': 'SOL-USD', 'side': 'long', 'quantity': 1.0}
)
```

---

## Component Interfaces

**Location**: `trading_bot_v2/component_interfaces.py`

### Purpose
ABC-based interfaces defining contracts for all major components.

### Interface Definitions

```python
from abc import ABC, abstractmethod

class StrategyInterface(ABC):
    """Interface for trading strategies."""
    
    @abstractmethod
    def generate_signal(self, symbol: str, market_data: Dict, 
                        regime: MarketRegime) -> Optional[Signal]:
        """Generate trading signal."""
        pass

class RiskInterface(ABC):
    """Interface for risk management."""
    
    @abstractmethod
    def get_position_size(self, signal: Signal, account_balance: float,
                          current_exposure: float) -> float:
        """Calculate position size."""
        pass
    
    @abstractmethod
    def validate_position(self, signal: Signal, quantity: float) -> bool:
        """Validate position against risk limits."""
        pass

class ExecutionInterface(ABC):
    """Interface for order execution."""
    
    @abstractmethod
    async def place_order(self, symbol: str, side: str, quantity: float,
                          order_type: str = 'market') -> Dict:
        """Place order on exchange."""
        pass

class DatabaseInterface(ABC):
    """Interface for data persistence."""
    
    @abstractmethod
    def get_positions(self) -> List[Dict]:
        """Get all positions."""
        pass
    
    @abstractmethod
    def save_position(self, position: Dict) -> bool:
        """Save position to database."""
        pass

class GridInterface(ABC):
    """Interface for grid trading."""
    
    @abstractmethod
    def create_grid(self, symbol: str, center_price: float,
                    levels: int) -> List[Dict]:
        """Create trading grid."""
        pass

class RegimeInterface(ABC):
    """Interface for market regime detection."""
    
    @abstractmethod
    def detect_regime(self, market_data: Dict) -> MarketRegime:
        """Detect current market regime."""
        pass
```

---

## Component Registry

**Location**: `trading_bot_v2/component_registry.py`

### Purpose
Component discovery and health monitoring.

### Features
- Register components by interface type
- Health check endpoints
- Dependency tracking
- Component lifecycle management

### Class Definition

```python
class ComponentRegistry:
    """Registry for component discovery and health monitoring."""

    def __init__(self):
        self._components: Dict[str, Any] = {}
        self._health_status: Dict[str, ComponentHealth] = {}
        self._dependencies: Dict[str, List[str]] = {}

    def register(self, name: str, component: Any, 
                 interfaces: List[Type], dependencies: List[str] = None):
        """Register component with interfaces and dependencies."""
        self._components[name] = component
        self._dependencies[name] = dependencies or []
        
    def get(self, name: str) -> Optional[Any]:
        """Get component by name."""
        return self._components.get(name)
    
    def get_by_interface(self, interface: Type) -> List[Any]:
        """Get all components implementing interface."""
        return [c for c in self._components.values() 
                if isinstance(c, interface)]
    
    async def health_check(self) -> Dict[str, ComponentHealth]:
        """Run health checks on all components."""
        for name, component in self._components.items():
            try:
                if hasattr(component, 'health_check'):
                    health = await component.health_check()
                else:
                    health = ComponentHealth(
                        name=name, 
                        status='healthy',
                        last_check=datetime.now()
                    )
                self._health_status[name] = health
            except Exception as e:
                self._health_status[name] = ComponentHealth(
                    name=name,
                    status='unhealthy',
                    error=str(e),
                    last_check=datetime.now()
                )
        return self._health_status

# Singleton accessor
def get_component_registry() -> ComponentRegistry:
    """Get singleton ComponentRegistry instance."""
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                        HUB SYSTEM ARCHITECTURE                       │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                        Component Registry                            │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐               │
│  │  Strategies  │ │   Risk Mgr   │ │   Database   │  ...          │
│  │  (8 total)   │ │              │ │              │               │
│  └──────────────┘ └──────────────┘ └──────────────┘               │
│                     │ Health Monitoring │ Discovery │              │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│                           DataHub                                    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐    │
│  │ Price Cache │  │   Locks     │  │    Circuit Breaker      │    │
│  │             │  │  - Cache    │  │  - Failure tracking     │    │
│  │             │  │  - DB       │  │  - Auto reset           │    │
│  │             │  │  - Broadcast│  │  - Exponential backoff  │    │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘    │
│                                                                      │
│  Readiness Events: _cache_ready | _db_ready | _bot_ready           │
└─────────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
              ▼               ▼               ▼
┌──────────────────┐ ┌──────────────┐ ┌──────────────────┐
│ ConnectionManager │ │  EventBus   │ │  Interfaces      │
│                  │ │             │ │                  │
│ - WebSocket pool │ │ - Pub/Sub   │ │ - StrategyIF     │
│ - Auth           │ │ - Event     │ │ - RiskIF         │
│ - Broadcast      │ │   Queue     │ │ - ExecutionIF    │
│ - Monitoring     │ │ - Callbacks │ │ - DatabaseIF     │
└──────────────────┘ └──────────────┘ └──────────────────┘

Communication Flow:
───────────────────
1. Component → Registry: Register with interfaces
2. Component → EventBus: Subscribe to events
3. DataHub → Components: Provide data (with circuit breaker)
4. EventBus → Components: Deliver events
5. Registry → Monitor: Health checks
```

---

## Key Files

| File | Lines | Purpose |
|------|-------|---------|
| `hub_system.py` | 382 | DataHub and ConnectionManager |
| `event_system.py` | ~200 | EventBus implementation |
| `component_interfaces.py` | ~150 | ABC interfaces |
| `component_registry.py` | ~200 | Component registry |

---

## Related Reports

- [01-core-trading-logic.md](./01-core-trading-logic.md) - Component usage
- [05-data-management.md](./05-data-management.md) - Database integration
- [08-web-interface-api.md](./08-web-interface-api.md) - WebSocket API
