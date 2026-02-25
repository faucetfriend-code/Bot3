# Data Management

<!--
RAG Metadata:
- Category: Data
- Tags: database, sqlite, models, position, trade, signal, grid-lifecycle
- Related: 04-exchange-integration, 06-hub-system-architecture, 07-risk-management
-->

## Overview

Data management consists of:
1. **DatabaseManager** - SQLite persistence layer
2. **Data Models** - Signal, Trade, Position, Order classes
3. **GridLifecycleManager** - Grid state machine
4. **DataCache** - In-memory caching

---

## DatabaseManager

**Location**: `trading_bot_v2/database.py`

### Purpose
SQLite-based persistent storage with connection pooling and caching.

### Features
- Connection pooling (max 10 connections)
- In-memory cache with TTL
- Thread-safe operations
- Automatic schema migration
- Circuit breaker integration

### Schema

```sql
-- Positions table
CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    margin_used REAL NOT NULL,
    leverage INTEGER DEFAULT 15,
    strategy TEXT,
    status TEXT DEFAULT 'open',
    entry_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    hourly_funding_rate REAL DEFAULT 0,
    cumulative_funding_paid REAL DEFAULT 0,
    margin_mode TEXT DEFAULT 'cross',
    liquidation_price REAL,
    unrealized_pnl REAL DEFAULT 0
);

-- Trades table
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL NOT NULL,
    quantity REAL NOT NULL,
    entry_time TIMESTAMP NOT NULL,
    exit_time TIMESTAMP NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    pnl_dollar REAL DEFAULT 0,
    pnl_percent REAL DEFAULT 0,
    strategy TEXT,
    quality TEXT,
    reason_entered TEXT,
    reason_exited TEXT,
    commission REAL DEFAULT 0
);

-- Signals table
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    confidence REAL,
    regime TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    executed INTEGER DEFAULT 0,
    outcome TEXT
);

-- Grid states table
CREATE TABLE IF NOT EXISTS grid_states (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    grid_id TEXT NOT NULL,
    level INTEGER NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    filled_at TIMESTAMP,
    position_id TEXT
);

-- Funding history table
CREATE TABLE IF NOT EXISTS funding_history (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    position_id TEXT,
    funding_rate REAL NOT NULL,
    funding_paid REAL NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Key Methods

```python
class DatabaseManager:
    """SQLite database manager with connection pooling and caching."""
    
    def __init__(self, db_path: str = "data/trading_bot.db"):
        """Initialize database with path."""
        
    # Position operations
    def get_positions(self, status: str = 'open') -> List[Dict[str, Any]]:
        """Get all positions with given status."""
        
    def get_position(self, position_id: str) -> Optional[Dict[str, Any]]:
        """Get single position by ID."""
        
    def save_position(self, position: Dict[str, Any]) -> bool:
        """Insert or update position."""
        
    def close_position(self, position_id: str, exit_price: float, 
                       exit_time: datetime) -> bool:
        """Mark position as closed and record exit."""
        
    # Trade operations
    def get_trades(self, limit: int = 100, 
                   strategy: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get trade history."""
        
    def save_trade(self, trade: Dict[str, Any]) -> bool:
        """Record completed trade."""
        
    # Signal operations
    def get_signals(self, limit: int = 100, 
                    executed_only: bool = False) -> List[Dict[str, Any]]:
        """Get signal history."""
        
    def save_signal(self, signal: Dict[str, Any]) -> bool:
        """Record generated signal."""
        
    def mark_signal_executed(self, signal_id: str, outcome: str) -> bool:
        """Mark signal as executed with outcome."""
        
    # Grid operations
    def get_grid_states(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get grid states for symbol or all."""
        
    def save_grid_state(self, grid_state: Dict) -> bool:
        """Save or update grid state."""
        
    # Funding operations
    def record_funding_payment(self, symbol: str, position_id: str,
                                funding_rate: float, funding_paid: float) -> bool:
        """Record hourly funding payment."""
```

### Connection Pool

```python
class ConnectionPool:
    """Simple connection pool for SQLite."""
    
    def __init__(self, max_connections: int = 10, max_idle_time: int = 300):
        """
        Args:
            max_connections: Maximum pool size
            max_idle_time: Seconds before idle connection is closed
        """
        
    def get_connection(self, timeout: float = 30.0) -> sqlite3.Connection:
        """Get connection from pool with timeout."""
        
    def return_connection(self, conn: sqlite3.Connection):
        """Return connection to pool."""
```

### Data Cache

```python
class DataCache:
    """In-memory cache with TTL for frequently accessed data."""
    
    def __init__(self, max_size: int = 1000, default_ttl: int = 300):
        """
        Args:
            max_size: Maximum number of cached items
            default_ttl: Default time-to-live in seconds
        """
        
    def get(self, key: str) -> Any:
        """Get cached value if not expired."""
        
    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """Set cached value with TTL."""
        
    def invalidate(self, key: str):
        """Remove key from cache."""
```

---

## Data Models

**Location**: `trading_bot_v2/models.py`

### Enums

```python
class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"

class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"

class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"

class PositionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    LIQUIDATED = "liquidated"
```

### Signal Dataclass

```python
@dataclass
class Signal:
    """Trading signal generated by a strategy."""
    
    asset: str                    # Trading symbol
    side: OrderSide               # BUY or SELL
    entry_price: float            # Suggested entry price
    stop_loss: float              # Stop loss price
    take_profit: Optional[float]  # Take profit price
    confidence: float             # 0-1 confidence score
    strategy: StrategyType        # Strategy that generated signal
    quality: TradeQuality         # STANDARD, HIGH_CONVICTION, SCALP
    market_state: MarketState     # Market regime at signal time
    asset_class: AssetClass       # CRYPTO, FOREX, etc.
    reason: str = ""              # Human-readable reason
    risk_profile: str = "medium"  # Risk profile for position sizing
    timestamp: datetime = field(default_factory=datetime.now)
```

### Position Dataclass

```python
@dataclass
class Position:
    """Open position with Pacifica-specific fields."""
    
    id: str
    asset: str
    asset_class: AssetClass
    side: OrderSide
    entry_price: float
    quantity: float
    margin_used: float
    leverage: int
    stop_loss: float
    take_profit: Optional[float] = None
    trailing_stop: Optional[float] = None
    entry_time: datetime = field(default_factory=datetime.now)
    status: PositionStatus = PositionStatus.OPEN
    strategy: StrategyType = StrategyType.TREND_FOLLOWING
    
    # Pacifica-specific: Hourly funding (24x per day)
    hourly_funding_rate: float = 0.0
    cumulative_funding_paid: float = 0.0
    last_funding_timestamp: Optional[datetime] = None
    
    # Pacifica-specific: Margin mode
    margin_mode: str = "cross"  # "cross" or "isolated"
    
    # Calculated fields
    unrealized_pnl: float = 0.0
    liquidation_price: float = 0.0
    
    @property
    def position_value(self) -> float:
        """Current position value."""
        return self.margin_used * self.leverage
```

### Trade Dataclass

```python
@dataclass
class Trade:
    """Completed trade with full metrics."""
    
    id: str
    asset: str
    asset_class: AssetClass
    side: OrderSide
    entry_price: float
    exit_price: float
    quantity: float
    entry_time: datetime
    exit_time: datetime
    stop_loss: float
    take_profit: Optional[float] = None
    pnl_dollar: float = 0.0
    pnl_percent: float = 0.0
    actual_rrr: float = 0.0       # Reward-to-risk ratio
    commission: float = 0.0
    status: str = "closed"
    margin_used: float = 0.0
    leverage: int = 15
    strategy: StrategyType = StrategyType.TREND_FOLLOWING
    quality: TradeQuality = TradeQuality.STANDARD
    market_state: MarketState = MarketState.UNKNOWN
    reason_entered: str = ""
    reason_exited: str = ""
    rule_adherence_score: int = 10  # 0-10 scale
    
    @property
    def duration_hours(self) -> float:
        """Trade duration in hours."""
        
    @property
    def is_winner(self) -> bool:
        """Check if trade was profitable."""
```

---

## GridLifecycleManager

**Location**: `trading_bot_v2/grid_lifecycle_manager.py`

### Purpose
Manages grid trading state machine with validation and recovery.

### Grid States

```python
class GridState(Enum):
    PENDING = "pending"      # Order not yet placed
    ACTIVE = "active"        # Order placed, waiting fill
    FILLED = "filled"        # Order filled, position open
    CLOSED = "closed"        # Position closed
    CANCELLED = "cancelled"  # Order cancelled
```

### State Transitions

```
┌───────────────────────────────────────────────────────────────┐
│                    Grid State Machine                          │
├───────────────────────────────────────────────────────────────┤
│                                                                │
│   ┌─────────┐     place_order     ┌─────────┐                │
│   │ PENDING │ ──────────────────► │ ACTIVE  │                │
│   └─────────┘                     └────┬────┘                │
│        │                               │                      │
│        │ cancel                        │ fill                 │
│        │                               │                      │
│        ▼                               ▼                      │
│   ┌───────────┐                  ┌─────────┐                 │
│   │ CANCELLED │                  │ FILLED  │                 │
│   └───────────┘                  └────┬────┘                 │
│                                       │                       │
│                                       │ close                 │
│                                       │                       │
│                                       ▼                       │
│                                  ┌─────────┐                 │
│                                  │ CLOSED  │                 │
│                                  └─────────┘                 │
│                                                                │
└───────────────────────────────────────────────────────────────┘
```

### Key Methods

```python
class GridLifecycleManager:
    """Manages grid trading lifecycle with validation."""
    
    def initialize_grid_system(self):
        """Initialize grid system from database, repair orphans."""
        
    def create_grid(self, symbol: str, center_price: float,
                    levels: int, spacing_atr: float) -> List[Dict]:
        """Create new grid with levels above and below center."""
        
    def get_active_grids(self, symbol: Optional[str] = None) -> List[Dict]:
        """Get all active grid configurations."""
        
    def handle_fill(self, grid_id: str, level: int, fill_price: float):
        """Handle grid level fill, create opposite side order."""
        
    def emergency_unwind(self, symbol: str, reason: str):
        """Emergency close all grid positions for symbol."""
        
    def repair_orphaned_grids(self):
        """Find and repair grids with missing orders."""
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Data Management Layer                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │                      TradingBot                                 │ │
│  └───────────────────────────┬───────────────────────────────────┘ │
│                              │                                      │
│              ┌───────────────┼───────────────┐                     │
│              │               │               │                     │
│              ▼               ▼               ▼                     │
│  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐       │
│  │ DataCache    │  │DatabaseManager│ │GridLifecycleManager│       │
│  │ (In-Memory)  │  │  (SQLite)     │ │ (State Machine)    │       │
│  └──────────────┘  └──────┬───────┘  └────────────────────┘       │
│                           │                                         │
│                           ▼                                         │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    Data Models                                 │  │
│  │  Signal | Position | Trade | Order | GridState               │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                           │                                         │
│                           ▼                                         │
│                    ┌─────────────┐                                  │
│                    │  SQLite DB  │                                  │
│                    │ (.db file)  │                                  │
│                    └─────────────┘                                  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Key Files

| File | Lines | Purpose |
|------|-------|---------|
| `database.py` | 2066 | SQLite database manager |
| `models.py` | 705 | Data model classes |
| `grid_lifecycle_manager.py` | ~600 | Grid state machine |

---

## Related Reports

- [04-exchange-integration.md](./04-exchange-integration.md) - Exchange data
- [06-hub-system-architecture.md](./06-hub-system-architecture.md) - Hub caching
- [07-risk-management.md](./07-risk-management.md) - Position tracking
