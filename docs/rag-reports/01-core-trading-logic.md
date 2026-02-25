# Core Trading Logic

<!--
RAG Metadata:
- Category: Core Logic
- Tags: trading-bot, strategy-manager, execution-layer, signal-logger
- Related: 02-trading-strategies, 07-risk-management, 06-hub-system-architecture
-->

## Overview

The core trading logic consists of four main components that work together to generate, validate, and execute trading signals:

1. **TradingBot** - Main orchestrator class
2. **StrategyManager** - Multi-strategy signal generation
3. **ExecutionLayer** - Entry timing refinement
4. **SignalLogger** - Signal tracking and analysis

---

## TradingBot Class

**Location**: `trading_bot_v2/trading_bot.py`

### Purpose
Main trading bot class that orchestrates all components and manages the trading loop.

### Key Responsibilities
- Initialize and coordinate all trading components
- Manage WebSocket and REST client connections
- Execute trading signals with risk validation
- Handle grid lifecycle management
- Publish updates to API server hub

### Class Definition

```python
class TradingBot:
    """
    Trading bot class for automated trading on Pacifica exchange.
    
    RISK MANAGEMENT WARNING
    ===============================
    DO NOT ADD RISK LOGIC HERE
    All risk calculations must go through RiskManager
    Use: self.risk_manager.get_position_size()
    Use: self.risk_manager.validate_position_size()
    ===============================
    """

    def __init__(self, db=None, client=None, risk_manager=None, hub_publish_func=None):
        """
        Initialize the trading bot.
        
        Args:
            db: Database instance (DatabaseManager). If None, creates new instance.
            client: PacificaClient instance.
            risk_manager: RiskManager instance. If None, creates new instance.
            hub_publish_func: Function to publish status updates to API server hub.
        """
```

### Component Initialization

```python
# Key components initialized in __init__:

# Database and signal logging
self.db = DatabaseManager()
self.signal_logger = SignalLogger(db_manager=self.db)

# Exchange client (REST)
self.client = PacificaClient(
    agent_wallet_private_key=config.agent_wallet_private_key,
    account_public_key=config.account_public_key,
    testnet=config.testnet,
)

# Risk management (AUTHORITATIVE)
self.risk_manager = RiskManager(
    db=self.db,
    client=self.client,
    risk_profile=RiskProfile[config.risk_profile.upper()],
)

# WebSocket client (real-time data)
self.ws_client = get_ws_client()

# Multi-timeframe data fetcher
self.multi_tf_fetcher = MultiTimeframeFetcher(
    self.client,
    ws_client=self.ws_client,
    cache_ttl_seconds=300,
)

# Market regime detector
self.market_regime = MarketRegimeDetector()

# Strategy manager (8 strategies)
self.strategy_manager = StrategyManager(
    regime_detector=self.market_regime,
    risk_manager=self.risk_manager,
    client=self.client,
    ws_client=self.ws_client,
)

# Grid lifecycle manager
self.grid_lifecycle = GridLifecycleManager(
    client=self.client,
    risk_manager=self.risk_manager,
    db=self.db,
    regime_detector=self.market_regime,
)

# Execution layer (1m/5m timing)
self.execution_layer = ExecutionLayer(
    fetcher=self.multi_tf_fetcher,
)
```

### Circuit Breaker

```python
# Circuit breaker protection
self._circuit_breaker_triggered = False
self._circuit_breaker_loss_pct = config.circuit_breaker_loss_pct  # Default 10%
```

---

## StrategyManager Class

**Location**: `trading_bot_v2/strategy_manager.py`

### Purpose
Orchestrates 8 trading strategies with regime-based filtering and conflict resolution.

### Strategy Enable Configuration

Strategies can be enabled/disabled via environment variables or constructor parameters:

| Strategy | Environment Variable | Default |
|----------|---------------------|---------|
| Mean Reversion | `ENABLE_MEAN_REVERSION` | True |
| MA Crossover | `ENABLE_MA_CROSSOVER` | True |
| Grid Trading | `ENABLE_GRID_TRADING` | True |
| Liquidation Capture | `ENABLE_LIQUIDATION_CAPTURE` | True |
| VWAP Scalping | `ENABLE_VWAP_SCALPING` | True |
| Funding Arbitrage | `ENABLE_FUNDING_ARB` | False |
| Momentum Scalping | `ENABLE_MOMENTUM_SCALPING` | True |
| Order Book Imbalance | `ENABLE_ORDERBOOK_IMBALANCE` | True |

### Conflict Resolution Rules

```python
"""
Conflict Resolution Rules:
1. All signals same direction → Combine with weighted average
2. Opposing signals (BUY + SELL):
   - TRENDING regime: Trust higher confidence signal
   - RANGING regime: Trust mean reversion over breakout
3. Quality override: HIGH_CONVICTION always takes priority
4. Maximum 1 signal per symbol per direction
"""
```

### Trade Cooldown System

```python
# Trade cooldown tracking
self._trade_cooldowns: Dict[Tuple[str, str], datetime] = {}
# Format: (symbol, strategy) -> cooldown_until
```

### Key Methods

```python
def generate_signals(
    self,
    symbol: str,
    market_data: Dict[str, List[float]],
    current_positions: List[Dict],
) -> List[Signal]:
    """
    Generate signals from all enabled strategies.
    
    Args:
        symbol: Trading symbol
        market_data: Dict with 'open', 'high', 'low', 'close', 'volume'
        current_positions: List of current positions
        
    Returns:
        List of validated signals after conflict resolution
    """
```

---

## ExecutionLayer Class

**Location**: `trading_bot_v2/execution_layer.py`

### Purpose
Refines entry timing using 1-minute and 5-minute data for better fills.

### Features
- Multi-timeframe confirmation
- Volume spike detection
- Pullback entry timing
- Momentum confirmation

### Key Methods

```python
def refine_entry(
    self,
    signal: Signal,
    market_data_1m: Dict,
    market_data_5m: Dict,
) -> Tuple[bool, Optional[float]]:
    """
    Refine entry timing using shorter timeframes.
    
    Args:
        signal: Original signal from strategy
        market_data_1m: 1-minute candle data
        market_data_5m: 5-minute candle data
        
    Returns:
        Tuple of (should_execute, refined_entry_price)
    """
```

---

## SignalLogger Class

**Location**: `trading_bot_v2/signal_logger.py`

### Purpose
Comprehensive signal tracking for analysis and optimization.

### Features
- CSV auto-save to `signals_log.csv`
- Database persistence
- In-memory cache for quick access
- Signal outcome tracking

### Signal Log Format

| Field | Description |
|-------|-------------|
| timestamp | Signal generation time |
| symbol | Trading pair |
| strategy | Strategy that generated signal |
| side | BUY/SELL |
| entry_price | Suggested entry price |
| stop_loss | Stop loss level |
| take_profit | Take profit level |
| confidence | Signal confidence (0-1) |
| regime | Market regime at time |
| executed | Whether signal was executed |
| outcome | Trade outcome if executed |

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                          TradingBot                                  │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                     Component Initialization                  │   │
│  │  Database | Client | RiskManager | WebSocket | MultiTFFetch │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                              │                                       │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │                     StrategyManager                            │ │
│  │  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐             │ │
│  │  │MeanReversion│ │MACrossover  │ │GridTrading  │ ... 8 total │ │
│  │  └─────────────┘ └─────────────┘ └─────────────┘             │ │
│  │              ↓ Conflict Resolution ↓                           │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                              │                                       │
│  ┌─────────────────────┐  ┌─────────────────────────────────────┐  │
│  │   ExecutionLayer    │  │        SignalLogger                 │  │
│  │  - 1m/5m timing     │  │  - CSV logging                      │  │
│  │  - Volume confirm   │  │  - Database storage                 │  │
│  └─────────────────────┘  └─────────────────────────────────────┘  │
│                              │                                       │
└──────────────────────────────┼───────────────────────────────────────┘
                               │
                               ▼
                    ┌──────────────────┐
                    │   RiskManager    │
                    │  (Validation)    │
                    └──────────────────┘
```

---

## Key Files

| File | Lines | Purpose |
|------|-------|---------|
| `trading_bot.py` | 2481 | Main bot class |
| `strategy_manager.py` | 1221 | Strategy orchestration |
| `execution_layer.py` | ~400 | Entry timing |
| `signal_logger.py` | ~300 | Signal tracking |

---

## Related Reports

- [02-trading-strategies.md](./02-trading-strategies.md) - Individual strategy details
- [07-risk-management.md](./07-risk-management.md) - Risk validation
- [03-market-analysis.md](./03-market-analysis.md) - Regime detection
