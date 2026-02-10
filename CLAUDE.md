# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a multi-strategy cryptocurrency trading bot for Pacifica.fi perpetual futures exchange. The bot implements **8 trading strategies** with regime-based strategy selection, Kelly Criterion position sizing, and comprehensive risk management with a focus on testnet-first development.

**Active Strategies:**
1. Mean Reversion - RSI + Bollinger Bands (RANGING_CALM)
2. MA Crossover - 50/200 MA golden/death cross (TRENDING_STRONG)
3. Grid Trading - ATR-spaced grid levels (RANGING_VOLATILE)
4. Liquidation Capture - Extreme cascade detection (ALL regimes)
5. VWAP Scalping - VWAP deviation trading (RANGING regimes)
6. Funding Arbitrage - Hourly funding rate exploitation (ALL regimes)
7. Momentum Scalping - EMA 9/21 crossover (TRENDING regimes)
8. Order Book Imbalance - Level 2 bid/ask analysis (ALL regimes)

## Quick Start

```bash
# 1. Start the bot (from Bot 3 directory)
python -m trading_bot_v2.api_server

# 2. Open browser to http://localhost:8000
# 3. Click "Start Bot" to begin trading

# Kill if port 8000 is busy (Windows)
netstat -ano | findstr :8000
taskkill /F /PID <pid>
```

## Development Commands

### Running the Bot

```bash
# Run as module from Bot 3 directory (REQUIRED for relative imports)
cd "Bot 3"
python -m trading_bot_v2.api_server

# Alternative: Run standalone bot (no web interface)
python -m trading_bot_v2.trading_bot
```

### Testing

```bash
# Run all tests (from Bot 3 directory)
cd "Bot 3"
pytest trading_bot_v2/

# Run specific test file
pytest trading_bot_v2/test_strategy_manager.py

# Run tests with verbose output
pytest trading_bot_v2/ -v

# Run tests with coverage
pytest trading_bot_v2/ --cov=trading_bot_v2

# Run single test by name pattern
pytest trading_bot_v2/ -k "mean_reversion"
pytest trading_bot_v2/ -k "strategy_manager"

# Run integration tests
pytest trading_bot_v2/ -k "integration"
```

### Database

The SQLite database (`trading_bot.db`) is auto-created on first run. Tables: `trades`, `positions`, `market_data`, `account_balances`.

## Architecture

### Directory Structure

```
Bot 3/
├── trading_bot_v2/              # Main production bot (v2 - streamlined)
│   ├── trading_bot.py           # Core bot loop and orchestration (COORDINATOR role)
│   ├── api_server.py            # FastAPI web server + WebSocket endpoints
│   ├── strategy_manager.py      # Multi-strategy orchestration
│   ├── market_regime.py         # ADX-based regime detection
│   ├── multi_timeframe_fetcher.py # Candle data fetcher with caching
│   ├── kelly_position_sizer.py  # Kelly Criterion position sizing
│   ├── database.py              # SQLite persistence layer
│   ├── pacifica_client.py       # Pacifica.fi REST API wrapper
│   ├── pacifica_ws_client.py    # Real-time WebSocket client (singleton)
│   ├── config.py                # Environment configuration
│   ├── risk_manager.py          # AUTHORITATIVE risk management (single source of truth)
│   ├── grid_lifecycle_manager.py # Grid state machine (ACTIVE/DISABLED/EMERGENCY)
│   ├── execution_layer.py       # 1m/5m entry timing refinement
│   ├── event_system.py          # Decoupled component communication
│   ├── component_registry.py    # Dependency injection container
│   ├── migrated_position_manager.py # Grid→trend-following position handoff
│   └── strategies/              # Individual strategy implementations (8 total)
│       ├── mean_reversion.py    # RSI + Bollinger Bands (RANGING_CALM)
│       ├── ma_crossover.py      # 50/200 MA crossover (TRENDING_STRONG)
│       ├── grid_trading.py      # Grid scalping (RANGING_VOLATILE)
│       ├── liquidation_capture.py # Extreme move detection (ALL regimes)
│       ├── vwap_scalping.py     # VWAP deviation scalping (RANGING regimes)
│       ├── funding_arb.py       # Funding rate arbitrage (ALL regimes)
│       ├── momentum_scalping.py # EMA 9/21 crossover (TRENDING regimes)
│       └── orderbook_imbalance.py # Level 2 imbalance (ALL regimes, overlay)
│
└── Example files/               # Reference implementations (legacy)
```

### Import Patterns

The `trading_bot_v2/` directory is a Python package. Use **relative imports** within the package:

```python
# In trading_bot_v2/strategies/*.py files:
from ..models import Signal, OrderSide
from ..config import StrategyType, AssetClass, MarketState, TradeQuality

# In trading_bot_v2/*.py files (top level):
from .models import Signal, OrderSide
from .config import StrategyType, config
```

**Key Point**: Run the bot as a module (`python -m trading_bot_v2.api_server`) to ensure relative imports work correctly.

### Component-Based Architecture

The bot uses a **component registry** with **event-driven communication**:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           TradingBot (COORDINATOR)                           │
│  - Does NOT own risk logic (delegates to RiskManager)                        │
│  - Does NOT own grid state (delegates to GridLifecycleManager)               │
│  - Orchestrates components via ComponentRegistry                             │
└──────────────────────────────────────────────────────────────────────────────┘
                                      │
           ┌─────────────────────────┬┴┬─────────────────────────┐
           ▼                         ▼ ▼                         ▼
   ┌───────────────┐       ┌───────────────┐           ┌───────────────┐
   │  RiskManager  │       │GridLifecycle  │           │StrategyManager│
   │ (AUTHORITATIVE)│       │   Manager     │           │               │
   │ - Position sizing│     │ - Grid state  │           │ - Signal gen  │
   │ - Exposure limits│     │ - Emergency   │           │ - Regime→strat│
   │ - Capital approval│    │   stops       │           │ - Conflict res│
   └───────────────┘       └───────────────┘           └───────────────┘
           │                         │                         │
           └─────────────────────────┴─────────────────────────┘
                                     │
                              EventBus (pub/sub)
                                     │
           ┌─────────────────────────┴─────────────────────────┐
           ▼                                                   ▼
   ┌───────────────┐                                   ┌───────────────┐
   │ExecutionLayer │                                   │PacificaWS     │
   │ - 1m/5m timing│                                   │Client (singleton)│
   │ - Entry refine│                                   │ - Real-time data│
   └───────────────┘                                   └───────────────┘
```

**Key Component Responsibilities:**
- **RiskManager** (`risk_manager.py`): AUTHORITATIVE for all position sizing and exposure limits. All other classes delegate here.
- **GridLifecycleManager** (`grid_lifecycle_manager.py`): State machine for grid trading (ACTIVE/DISABLED/EMERGENCY_EXIT). Tracks fills, calculates P&L, ensures one grid per symbol.
- **ExecutionLayer** (`execution_layer.py`): Uses 1m/5m data for entry timing AFTER higher timeframe signals. Never changes signal direction.
- **EventBus** (`event_system.py`): Decoupled pub/sub for SIGNAL_GENERATED, ORDER_PLACED, GRID_EMERGENCY_STOP, etc.
- **ComponentRegistry** (`component_registry.py`): Dependency injection container for retrieving components by interface.

### Multi-Strategy System Architecture

The bot uses **regime-based strategy selection** to adapt to market conditions:

```
Market Data → Regime Detection → Strategy Selection → Signal Generation → Conflict Resolution → Position Sizing → Execution
```

#### 1. **Regime Detection** (`market_regime.py`)

Uses ADX (Average Directional Index) to classify market state:
- **TRENDING_STRONG** (ADX > 25): Directional momentum
- **RANGING_VOLATILE** (ADX < 20, high volatility): Choppy sideways
- **RANGING_CALM** (ADX < 20, low volatility): Quiet oscillations
- **INDECISIVE** (ADX 20-25): Stay flat

Volatility measured via Bollinger Band width percentile (75th = high volatility threshold).

#### 2. **Strategy Manager** (`strategy_manager.py`)

**Orchestration Logic:**
1. Detect current regime using 4h timeframe data
2. Get active strategies for regime (via `get_active_strategies()`)
3. Call `generate_signals()` on each active strategy
4. Resolve conflicts if multiple signals generated

**Conflict Resolution Rules:**
- **HIGH_CONVICTION quality → automatic priority** (overrides all else)
- Same direction signals → weighted combination based on regime weights
- Opposing signals (BUY + SELL):
  - TRENDING regime: trust higher confidence
  - RANGING regime: prefer mean reversion over breakout
  - If confidence difference < 10%: stay flat (too close to call)

**Regime-to-Strategy Mapping:**
```python
{
    MarketRegime.TRENDING_STRONG: ["MACrossover", "MomentumScalping"],  # 60/40 weight split
    MarketRegime.TRENDING_MODERATE: ["MomentumScalping"],               # 100% weight
    MarketRegime.RANGING_VOLATILE: ["GridTrading", "VWAPScalping"],     # 70/30 weight split
    MarketRegime.RANGING_CALM: ["MeanReversion", "VWAPScalping"],       # 70/30 weight split
    MarketRegime.INDECISIVE: []                                         # Stay flat
}
# Overlay strategies (run independently in ALL regimes):
# - LiquidationCapture (extreme cascades)
# - FundingArb (funding rate opportunities)
# - OrderBookImbalance (Level 2 imbalance detection)
```

#### 3. **Strategy Implementations**

All strategies inherit from base pattern and implement:
```python
def generate_signals(self, symbol: str, multi_tf_data: Dict, current_price: float) -> List[Signal]
```

**Mean Reversion** (`strategies/mean_reversion.py`):
- Active in: RANGING_CALM
- Logic: RSI oversold/overbought + Bollinger Band proximity
- Requires: Multi-timeframe RSI alignment (15m + 1h both extreme)
- Stop: 2.0x ATR
- Confidence: Weighted scoring (RSI strength 40% + BB proximity 30% + MTF alignment 30%)

**MA Crossover** (`strategies/ma_crossover.py`):
- Active in: TRENDING_STRONG
- Logic: 50/200 MA golden/death cross with pullback entry
- **Stateful**: Tracks crossovers in `self.last_crossover[symbol]` dict
- Entry: Waits 1-5 candles after cross for 2-4% retracement
- Stop: 2.5x ATR
- Volume confirmation: 1.2x average volume required

**Grid Trading** (`strategies/grid_trading.py`):
- Active in: RANGING_VOLATILE
- Logic: Place buy/sell orders at ATR-spaced intervals
- Safety: Emergency stop if ADX rises above 20 (regime change to trending)
- Max: 10 grid positions per symbol, 5% portfolio emergency stop
- Spacing: 0.5x ATR between grid levels

**Liquidation Capture** (`strategies/liquidation_capture.py`):
- Active in: ALL regimes (independent overlay)
- **Bidirectional**: Detects both long liquidations (downward cascade) and short squeezes (upward cascade)
- Triggers: 3%+ price move in 5 candles + 3x volume spike + extreme RSI (≤15 or ≥85)
- Session limits: Max 1 trade per 4 hours
- RRR: Fixed 3:1 target

**VWAP Scalping** (`strategies/vwap_scalping.py`):
- Active in: RANGING_VOLATILE, RANGING_CALM
- Logic: Trade deviation from VWAP (Volume Weighted Average Price)
- Entry: Price 0.3-0.8% from VWAP with RSI confirmation
- Stop: 1.5x ATR
- Target: Return to VWAP
- Volume filter: Requires above-average volume for confirmation

**Funding Arbitrage** (`strategies/funding_arb.py`):
- Active in: ALL regimes (independent overlay)
- Logic: Exploit extreme funding rates on Pacifica (hourly funding = 24x/day)
- Entry: |funding_rate| > 0.02% (annualized > 175%)
- Direction: Short when funding highly positive, Long when highly negative
- Hold period: Until funding normalizes or next funding payment
- Risk: 1% of portfolio per position

**Momentum Scalping** (`strategies/momentum_scalping.py`):
- Active in: TRENDING_STRONG, TRENDING_MODERATE
- Logic: EMA 9/21 crossover with RSI and MACD confirmation
- Timeframes: 5m for entry, 15m for trend confirmation
- Entry: EMA crossover + RSI 40-60 range (not overbought/oversold) + MACD alignment
- Stop: 1.0x ATR (tight stops for scalping)
- Target: 1.5x ATR (quick profits)
- Cooldown: 5 minutes between signals

**Order Book Imbalance** (`strategies/orderbook_imbalance.py`):
- Active in: ALL regimes (independent overlay)
- Logic: Analyze Level 2 orderbook for bid/ask volume imbalances
- Entry: Imbalance ratio > 2.0 (configurable) with sufficient volume
- Features:
  - Weighted imbalance calculation (closer levels weighted higher)
  - Spoof detection (filters orders likely to be cancelled)
  - Imbalance history tracking for trend confirmation
- Stop: 0.75x ATR (very tight)
- Target: 1.5x ATR
- Cooldown: 30 seconds (fast-acting strategy)
- **WebSocket Required**: Uses real-time orderbook subscriptions

#### 4. **Signal Validation** (`models.py` - Signal class)

Every signal passes through **8 validation flags** before execution:
1. `volume_confirmation`: Volume > threshold
2. `multi_timeframe_alignment`: Indicators align across timeframes
3. `support_resistance_valid`: Entry near S/R levels
4. `rrr_meets_minimum`: Risk-reward ratio ≥ 2:1
5. `liquidation_buffer_safe`: Not too close to liquidation price
6. `account_risk_ok`: Position size within account risk limits
7. `margin_drawdown_ok`: Drawdown within acceptable range
8. `forbidden_conditions_clear`: No forbidden market conditions

Signal is valid only if `is_valid()` returns `True` (checks all 8 flags).

#### 5. **Position Sizing** (`kelly_position_sizer.py`)

**Hybrid Approach:**
- **Kelly Criterion** when 50+ closed trades exist for strategy
- **Fixed Percentage Fallback** when insufficient history

**Formula:**
```
Kelly % = (Win Rate × Avg Win - Loss Rate × Avg Loss) ÷ Avg Win
Adjusted Kelly = Kelly % × Kelly Fraction (default 0.5)
Position Size = (Account Balance × Adjusted Kelly ÷ Stop Distance %)
```

**Safety Limits:**
- Maximum 10% of account per trade (hard cap)
- Minimum 1 contract per trade
- Negative Kelly → 1% fallback
- Fractional Kelly (0.5x) prevents over-betting

**Database Integration:**
Queries `trades` table for last 50 closed trades per strategy:
```sql
SELECT pnl, entry_price, exit_price, quantity, side
FROM trades
WHERE account_id = ? AND strategy = ? AND status = 'closed'
ORDER BY exit_time DESC
LIMIT 50
```

**Fallback Percentages** (when < 50 trades):
- Trend Following: 2.0%
- MA Crossover: 2.0%
- Mean Reversion: 1.5%
- Grid Trading: 0.5%
- Liquidation Capture: 2.5%
- VWAP Scalping: 1.5%
- Funding Arbitrage: 1.0%
- Momentum Scalping: 1.5%
- Order Book Imbalance: 1.0%

#### 6. **Multi-Timeframe Data** (`multi_timeframe_fetcher.py`)

Fetches candles across 3 timeframes: **15m, 1h, 4h**
- **Caching**: 60-second TTL to avoid redundant API calls
- **Lookback**: 200-250 candles (needed for 200 MA calculation)
- Returns structured dict:
```python
{
    "15m": {"high": [...], "low": [...], "close": [...], "volume": [...]},
    "1h": {...},
    "4h": {...}
}
```

### Database Schema

**Key Tables:**
- `trades`: Historical closed trades with PnL, strategy, timestamps
- `positions`: Current open positions with unrealized P&L
- `market_data`: OHLCV candle data
- `account_balances`: Account balance snapshots

**Important Columns for Kelly Criterion:**
- `trades.pnl`: Required for win/loss calculation
- `trades.strategy`: Strategy type (for per-strategy performance)
- `trades.status`: Must be 'closed' to count in Kelly calc
- `trades.exit_time`: For ordering by recency

### Configuration System

**Environment Variables** (`.env`):
- **Strategy Enable Flags**: `ENABLE_MEAN_REVERSION=true`, `ENABLE_MOMENTUM_SCALPING=true`, etc.
- **Regime Thresholds**: `ADX_TRENDING_THRESHOLD=25.0`
- **Strategy Parameters**: 100+ variables for fine-tuning all 8 strategies
- **Kelly Settings**: `KELLY_FRACTION=0.5`, `KELLY_MIN_TRADES=50`
- **Orderbook Settings**: `ORDERBOOK_IMBALANCE_THRESHOLD=2.0`, `ORDERBOOK_SPOOF_DETECTION=true`, etc.

**Access Pattern:**
```python
from config import config

# Accessed via config object
max_positions = config.max_positions
kelly_fraction = config.kelly_fraction  # If added to config.py
```

### Trading Bot Main Loop (`trading_bot.py`)

**Execution Flow:**
1. `_trading_loop()` runs every 60 seconds
2. `_update_positions()`: Sync positions from Pacifica API → database
3. `_check_signals()`:
   - Fetch markets
   - Get current price via `get_ticker()`
   - Fetch multi-timeframe data (15m, 1h, 4h)
   - Generate signals via `strategy_manager.generate_signals_for_market()`
   - Execute valid signals
4. `_monitor_risk()`: Check circuit breaker (10% portfolio loss)

**Position Sizing Integration:**
```python
def _calculate_position_size(self, signal: Signal) -> float:
    # Strategy-specific risk allocation
    risk_per_trade = {
        StrategyType.TREND_FOLLOWING: 0.02,
        StrategyType.MA_CROSSOVER: 0.02,
        StrategyType.MEAN_REVERSION: 0.015,
        StrategyType.GRID_TRADING: 0.005,
        StrategyType.LIQUIDATION_CAPTURE: 0.025,
        StrategyType.VWAP_SCALPING: 0.015,
        StrategyType.FUNDING_ARB: 0.01,
        StrategyType.MOMENTUM_SCALPING: 0.015,
        StrategyType.ORDERBOOK_IMBALANCE: 0.01
    }

    risk_amount = account_balance * risk_per_trade[signal.strategy]
    quantity = risk_amount / abs(signal.entry_price - signal.stop_loss)
    return max(quantity, 1.0)  # Minimum 1 contract
```

**Future Enhancement**: Replace with `kelly_sizer.calculate_position_size()` when ready to activate Kelly Criterion.

### Strategy Cooldowns

Each strategy has a configurable cooldown period to prevent overtrading:

| Strategy | Cooldown | Rationale |
|----------|----------|-----------|
| Mean Reversion | 15 min | Wait for RSI to normalize |
| MA Crossover | 60 min | Higher timeframe signals |
| Grid Trading | 5 min | Frequent grid adjustments |
| Liquidation Capture | 240 min (4h) | Rare event, max 1 trade/session |
| VWAP Scalping | 10 min | Allow VWAP deviation to reset |
| Funding Arbitrage | 60 min | Wait for next funding period |
| Momentum Scalping | 5 min | Fast scalping strategy |
| Order Book Imbalance | 0.5 min | Very fast-acting overlay |

### Circuit Breaker

**Percentage-Based** (not fixed dollar amount):
- Triggers at 10% portfolio loss (configurable via `CIRCUIT_BREAKER_LOSS_PCT`)
- Warning at 80% of threshold
- Automatically stops trading and calls `self.stop()`

**Implementation:**
```python
pnl_percentage = total_pnl / account_balance
if pnl_percentage <= -self._circuit_breaker_loss_pct:
    self._circuit_breaker_triggered = True
    self.stop()
```

### Pacifica.fi API Integration

**REST Client** (`pacifica_client.py`):
- `get_markets()`: List available perpetual contracts
- `get_candles(market, interval, start_time, limit)`: Historical OHLCV data
- `place_order(symbol, side, quantity, order_type, price)`: Execute trades
- `get_positions()`: Current open positions
- `get_balance()`: Account balance/equity
- `get_orders()`: Open orders

**WebSocket Client** (`pacifica_ws_client.py`):
- **Singleton pattern**: Use `get_ws_client()` to access
- Real-time price updates via `_price_cache`
- Kline (candle) streaming with disk cache persistence
- **Orderbook subscriptions**: `subscribe_orderbook(symbol)` for Level 2 data
  - `get_orderbook(symbol)`: Returns full orderbook snapshot
  - `get_orderbook_imbalance(symbol, levels)`: Calculates bid/ask imbalance
- Auto-reconnection with exponential backoff
- Thread-safe caches for prices, positions, balance, orderbooks

**Authentication:**
- Uses Ed25519 signatures with agent wallet keypair
- Environment variables: `AGENT_WALLET_PRIVATE_KEY`, `ACCOUNT_PUBLIC_KEY`
- Agent wallet must be authorized for account on Pacifica.fi dashboard
- IP whitelisting required for agent keys

**Order Payload Requirements:**
- `reduce_only`: Required boolean field (False for new positions, True to close only)
- `side`: "bid" (buy) or "ask" (sell)
- `amount`: String representation of quantity

**Rate Limiting:**
- Basic tier: 10 requests/second
- Multi-timeframe fetcher uses caching to stay under limits

**Funding Payments:**
- Pacifica has **hourly funding** (24x per day, unlike 8h standard)
- Important for grid trading (can accumulate funding costs)

## Key Patterns and Conventions

### Strategy Development Pattern

When adding a new strategy:

1. **Create file** in `trading_bot_v2/strategies/your_strategy.py`
2. **Use relative imports** (strategies are part of the package):
   ```python
   from ..models import Signal, OrderSide
   from ..config import StrategyType, AssetClass, TradeQuality, MarketState
   ```
3. **For indicator functions**, either import from package or define locally:
   ```python
   from ..indicators import calculate_atr, calculate_rsi
   # OR define calculate functions locally in the strategy file
   ```
4. **Implement `generate_signals()` method**
5. **Add to StrategyType enum** in `trading_bot_v2/config.py`
6. **Export from `strategies/__init__.py`**
7. **Register in StrategyManager** `__init__()` with enable flag
8. **Add to regime mapping** in `market_regime.py`
9. **Update .env** with strategy-specific parameters and `ENABLE_YOUR_STRATEGY=true`

### Testing Pattern

**Use realistic market data** to test strategies:
```python
# Create trending data (for MA crossover testing)
closes_trending = [100 + (i * 0.3) for i in range(250)]  # Steady uptrend

# Create ranging data (for mean reversion testing)
closes_ranging = [100 + random.uniform(-2, 2) for _ in range(250)]  # Sideways

# Create cascade data (for liquidation capture testing)
closes_cascade = [100.0] * 45 + [sharp 15% drop over 5 candles]
```

**Multi-step test execution** (for stateful strategies like MA crossover):
```python
# Step 1: Detect crossover
signals_crossover = strategy.generate_signals(symbol, multi_tf_data, price)

# Step 2: Wait for pullback (simulate next candle)
signals_entry = strategy.generate_signals(symbol, multi_tf_data_updated, price_after_pullback)
```

### Error Handling

**Database errors**: Wrap in try/catch, use `with self._data_lock` for thread safety
**API errors**: Catch exceptions, log with `logging.error()`, continue loop
**Validation errors**: Signal validation happens via `is_valid()` - check before execution

### Logging

Uses standard `logging` module:
```python
import logging

logging.info("Bot started")
logging.warning("Approaching circuit breaker threshold")
logging.error(f"Error executing signal: {e}")
logging.critical("CIRCUIT BREAKER TRIGGERED")
```

Kelly Position Sizer uses `loguru`:
```python
from loguru import logger

logger.info("Kelly Position Sizer initialized")
logger.debug("Calculating Kelly %...")
```

## Important Cross-File Relationships

1. **Signal Flow:**
   ```
   strategies/*.py → Signal objects → strategy_manager.py → execution_layer.py → trading_bot.py → pacifica_client.py
   ```

2. **Data Flow:**
   ```
   pacifica_ws_client.py (real-time) ─┬→ multi_timeframe_fetcher.py → strategies/*.py
   pacifica_client.py (REST fallback) ─┘
   pacifica_client.py → database.py ← trading_bot.py
   ```

3. **Configuration:**
   ```
   .env → config.py (loaded with override=True) → trading_bot.py + all modules
   ```

4. **Shared Models:**
   ```
   trading_bot_v2/models.py → Signal, OrderSide, Trade dataclasses
   trading_bot_v2/config.py → StrategyType, AssetClass, MarketState enums + config object
   ```

5. **Risk Flow (AUTHORITATIVE):**
   ```
   RiskManager ← TradingBot (position sizing requests)
   RiskManager ← GridLifecycleManager (capital allocation)
   RiskManager ← StrategyManager (risk profile lookup)
   ```

6. **Grid Trading Flow:**
   ```
   GridTradingStrategy → Signal → GridLifecycleManager → order placement → fill tracking → P&L
   Regime change → GridLifecycleManager.handle_regime_change() → cancel orders or migrate positions
   ```

7. **Event-Driven Communication:**
   ```
   EventBus.publish(SIGNAL_GENERATED) → subscribed components react
   EventBus.publish(GRID_EMERGENCY_STOP) → GridLifecycleManager cancels all orders
   ```

## Windows-Specific Notes

- Use **Git Bash** for Unix-like commands
- Forward slashes work: `/c/Users/...`
- PowerShell commands available via `powershell -Command`
- File paths in code use `os.path.join()` for cross-platform compatibility

## Grid Trading Specifics

**State Machine** (`grid_lifecycle_manager.py`):
- `IDLE`: No grid active
- `ACTIVE`: Grid running, orders placed
- `EMERGENCY_EXIT`: Flattening positions due to loss threshold
- `DISABLED_BY_REGIME`: Regime changed to trending, orders cancelled

**Fill Tracking:**
- `GridFill` dataclass tracks each fill with matching status
- Round-trip detection: Matches buy fills with sell fills for P&L calculation
- `GridMetrics`: Aggregates total buys/sells, realized/unrealized P&L

**Position Migration:**
- When regime changes from RANGING_VOLATILE to TRENDING_STRONG:
  - Grid orders cancelled
  - Net positions migrated to `MigratedPositionManager`
  - Trend-following management takes over

## Troubleshooting

**"Verification failed" on order placement:**
- Check `AGENT_WALLET_PRIVATE_KEY` is the base58-encoded private key (not public key)
- Verify agent wallet is authorized for account on Pacifica dashboard
- Confirm IP address is whitelisted for the agent key
- Use `/api/debug/key-config` endpoint to verify loaded keys

**Environment variables not updating:**
- `config.py` uses `load_dotenv(override=True)` to force reload
- Restart server completely (uvicorn with `reload=False`)
- Delete any duplicate `.env` files in subdirectories

**Port 8000 already in use:**
- Kill existing process: `taskkill /F /PID <pid>` (Windows) or `kill -9 <pid>` (Unix)
- Find process: `netstat -ano | findstr :8000` (Windows)

## Production Readiness Checklist

Before deploying to mainnet:

1. **Test on testnet** for at least 7 days
2. **Verify all 8 validation flags** working correctly
3. **Confirm circuit breaker triggers** at 10% loss
4. **Accumulate 50+ trades per strategy** for Kelly activation
5. **Monitor regime detection accuracy** (ADX calculations)
6. **Check position sizing** doesn't exceed limits
7. **Verify funding payment tracking** for grid positions
8. **Review logs** for any errors or warnings
9. **Ensure .env secrets** are not committed
10. **Set `TESTNET=false`** and update API credentials
11. **Whitelist production IP** for agent wallet on Pacifica
12. **Verify WebSocket client reconnection** under network issues

## Common Workflows

### Adding a New Indicator

1. Add function to `Example files/core_logic/indicators.py`
2. Import in strategy file: `from indicators import your_new_indicator`
3. Use in `generate_signals()` method
4. Update strategy confidence calculation if needed

### Adjusting Strategy Parameters

1. Update `.env` with new parameter values
2. Optionally: Add to `config.py` if needs Python access
3. Restart bot to reload configuration
4. Monitor performance changes

### Debugging Signal Generation

1. Add debug logging in strategy `generate_signals()`:
   ```python
   logging.debug(f"RSI: {rsi}, BB distance: {bb_distance}")
   ```
2. Set `LOG_LEVEL=DEBUG` in `.env`
3. Run bot and check console output
4. Verify indicator values are as expected

### Reviewing Kelly Criterion Performance

```bash
# Check strategy stats
python -c "
from kelly_position_sizer import KellyPositionSizer
from database import DatabaseManager
from config import StrategyType

db = DatabaseManager()
kelly = KellyPositionSizer(db)
stats = kelly.get_strategy_stats(StrategyType.MEAN_REVERSION)
print(stats)
"
```
