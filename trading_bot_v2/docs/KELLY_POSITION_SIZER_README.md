# Kelly Criterion Position Sizing Module

## Overview

The Kelly Position Sizer implements the **Kelly Criterion** formula for optimal position sizing based on historical strategy performance. It dynamically adjusts position sizes as strategies prove themselves, providing larger allocations to winning strategies and smaller allocations to losing ones.

## Formula

```
Kelly % = (Win Rate × Avg Win - Loss Rate × Avg Loss) ÷ Avg Win
```

**Example:**
- Win Rate: 60% (0.6)
- Avg Win: $100
- Avg Loss: $50
- Kelly % = (0.6 × 100 - 0.4 × 50) ÷ 100 = (60 - 20) ÷ 100 = **40%**

**With Fractional Kelly (0.5x for safety):**
- Adjusted Kelly = 40% × 0.5 = **20%** of account per trade

## Features

### ✅ Core Features

1. **Historical Performance Tracking**
   - Queries last 50 trades per strategy from database
   - Calculates win rate, average win, average loss
   - Adapts position sizing to strategy performance

2. **Fractional Kelly (Safety)**
   - Default: 0.5x Kelly (half Kelly)
   - Reduces risk of over-betting
   - Configurable (0.25x, 0.33x, 0.5x, 1.0x)

3. **Fallback Sizing**
   - Used when < 50 trades available
   - Strategy-specific defaults:
     - Trend Following: 2%
     - MA Crossover: 2%
     - Mean Reversion: 1.5%
     - Grid Trading: 0.5%
     - Liquidation Capture: 2.5%

4. **Safety Limits**
   - Maximum 10% of account per trade (hard cap)
   - Minimum 1 contract per trade
   - Negative Kelly → 1% fallback

5. **Multi-Account Support**
   - Account ID parameter for isolated tracking
   - Subaccount-aware performance metrics

## Installation

### File Location
```
C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\trading_bot_v2\kelly_position_sizer.py
```

### Dependencies
```python
# Core dependencies (already in project)
from database import DatabaseManager
from models import Signal
from config import StrategyType
from loguru import logger
```

## Usage

### Basic Usage

```python
from kelly_position_sizer import KellyPositionSizer
from database import DatabaseManager

# Initialize
db = DatabaseManager()
kelly_sizer = KellyPositionSizer(
    db=db,
    kelly_fraction=0.5,  # Half Kelly (conservative)
    min_trades=50        # Need 50+ trades before using Kelly
)

# Calculate position size for a signal
quantity = kelly_sizer.calculate_position_size(
    signal=signal,
    account_balance=10000.0,
    account_id="sub_1"
)
```

### Integration with Trading Bot

```python
class TradingBot:
    def __init__(self):
        self.db = DatabaseManager()
        self.kelly_sizer = KellyPositionSizer(
            db=self.db,
            kelly_fraction=0.5,
            min_trades=50
        )

    def process_signal(self, signal: Signal):
        # Get account balance
        account_balance = self.get_account_balance()

        # Calculate Kelly-optimized position size
        quantity = self.kelly_sizer.calculate_position_size(
            signal=signal,
            account_balance=account_balance,
            account_id=self.account_id
        )

        # Update signal
        signal.quantity = quantity

        # Execute if valid
        if quantity >= 1.0:
            self.execute_trade(signal)
```

## API Reference

### `KellyPositionSizer`

#### Constructor

```python
KellyPositionSizer(
    db: DatabaseManager,
    kelly_fraction: float = 0.5,
    min_trades: int = 50
)
```

**Parameters:**
- `db`: Database instance for querying trade history
- `kelly_fraction`: Fraction of Kelly to use (0.25-1.0, default: 0.5)
- `min_trades`: Minimum trades before using Kelly (default: 50)

#### Methods

##### `calculate_position_size()`

```python
def calculate_position_size(
    self,
    signal: Signal,
    account_balance: float,
    account_id: str = "sub_1"
) -> float
```

Calculate position size using Kelly Criterion.

**Parameters:**
- `signal`: Trading signal with strategy, entry price, stop loss
- `account_balance`: Current account balance
- `account_id`: Account ID for multi-account support

**Returns:**
- Position quantity (float, minimum 1.0)

**Example:**
```python
quantity = kelly_sizer.calculate_position_size(
    signal=my_signal,
    account_balance=10000.0
)
# Output: 1.5 contracts
```

##### `get_strategy_stats()`

```python
def get_strategy_stats(
    self,
    strategy: StrategyType,
    account_id: str = "sub_1"
) -> Dict[str, float]
```

Get performance statistics for a strategy.

**Returns:**
```python
{
    "win_rate": 0.6,        # 60% win rate
    "avg_win": 100.0,       # $100 average win
    "avg_loss": 50.0,       # $50 average loss
    "total_trades": 75      # 75 total trades
}
```

##### `update_kelly_fraction()`

```python
def update_kelly_fraction(self, new_fraction: float)
```

Update Kelly fraction dynamically.

**Example:**
```python
# More conservative after losses
kelly_sizer.update_kelly_fraction(0.25)

# More aggressive after wins
kelly_sizer.update_kelly_fraction(0.5)
```

##### `get_recommended_kelly_fraction()`

```python
def get_recommended_kelly_fraction(
    self,
    strategy: StrategyType,
    account_id: str = "sub_1"
) -> float
```

Get recommended Kelly fraction based on strategy performance.

**Returns:**
- `0.5`: Strong performance (Profit Factor ≥ 2.0, Win Rate ≥ 55%)
- `0.33`: Moderate performance (Profit Factor ≥ 1.5, Win Rate ≥ 45%)
- `0.25`: Weak performance or insufficient data

## Examples

### Example 1: Basic Calculation

```python
# Strategy with 60% win rate
# Avg win: $100, Avg loss: $50
# 60 trades in history

signal = Signal(
    strategy=StrategyType.TREND_FOLLOWING,
    entry_price=50000.0,
    stop_loss=49000.0  # 2% stop
)

quantity = kelly_sizer.calculate_position_size(
    signal=signal,
    account_balance=10000.0
)

# Calculation:
# Kelly = (0.6 × 100 - 0.4 × 50) / 100 = 40%
# Adjusted (×0.5) = 20%
# Capped at 10% max
# Dollar risk = $10,000 × 10% = $1,000
# Stop distance = 2%
# Position value = $1,000 / 0.02 = $50,000
# Quantity = $50,000 / $50,000 = 1.0 contract
```

### Example 2: Insufficient Trade History

```python
# Strategy with only 20 trades (< 50 minimum)

signal = Signal(
    strategy=StrategyType.MEAN_REVERSION,
    entry_price=50000.0,
    stop_loss=49500.0  # 1% stop
)

quantity = kelly_sizer.calculate_position_size(
    signal=signal,
    account_balance=10000.0
)

# Uses fallback: 1.5% for MEAN_REVERSION
# Dollar risk = $10,000 × 1.5% = $150
# Position value = $150 / 0.01 = $15,000
# Quantity = $15,000 / $50,000 = 0.3 → rounds to 1.0
```

### Example 3: Negative Kelly (Losing Strategy)

```python
# Strategy with 30% win rate (losing)
# Avg win: $50, Avg loss: $100

signal = Signal(
    strategy=StrategyType.TREND_FOLLOWING,
    entry_price=50000.0,
    stop_loss=49000.0
)

quantity = kelly_sizer.calculate_position_size(
    signal=signal,
    account_balance=10000.0
)

# Kelly = (0.3 × 50 - 0.7 × 100) / 50 = -1.0 (negative!)
# Uses 1% fallback for negative Kelly
# Dollar risk = $10,000 × 1% = $100
# Position value = $100 / 0.02 = $5,000
# Quantity = $5,000 / $50,000 = 0.1 → rounds to 1.0
```

## Configuration

### Kelly Fraction Guidelines

| Fraction | Risk Level | Use Case |
|----------|------------|----------|
| 0.25 | Very Conservative | New strategies, high volatility |
| 0.33 | Conservative | Moderate performance, after losses |
| 0.5 | Balanced | Strong performance, standard use |
| 1.0 | Aggressive | **NOT RECOMMENDED** (full Kelly is risky) |

### Fallback Percentages

| Strategy | Fallback % | Rationale |
|----------|-----------|-----------|
| Trend Following | 2.0% | Standard directional trades |
| MA Crossover | 2.0% | Similar to trend following |
| Mean Reversion | 1.5% | More frequent, smaller moves |
| Grid Trading | 0.5% | Many small positions |
| Liquidation Capture | 2.5% | High conviction, rare opportunities |
| Breakout | 2.0% | Standard momentum trades |

## Testing

Run the test suite:

```bash
pytest test_kelly_position_sizer.py -v
```

**Test Coverage:**
- ✅ Fallback sizing with 0 trades
- ✅ Fallback sizing with < 50 trades
- ✅ Kelly sizing with 50+ trades
- ✅ Negative Kelly handling
- ✅ Maximum position size caps
- ✅ Different strategy types
- ✅ Invalid stop distance handling
- ✅ Kelly fraction updates
- ✅ Strategy statistics retrieval

## Database Requirements

### Required Tables

The module queries the `trades` table:

```sql
SELECT pnl, entry_price, exit_price, quantity, side
FROM trades
WHERE account_id = ? AND strategy = ? AND status = 'closed'
ORDER BY exit_time DESC
LIMIT 50
```

### Required Columns
- `account_id` (TEXT)
- `strategy` (TEXT)
- `status` (TEXT)
- `pnl` (REAL) - Profit/loss in dollars
- `entry_price` (REAL)
- `exit_price` (REAL)
- `quantity` (REAL)
- `side` (TEXT)
- `exit_time` (TIMESTAMP)

## Performance Considerations

### Caching
- Database queries are executed per calculation
- Consider caching strategy stats for high-frequency trading
- Update cache after each completed trade

### Optimization Tips

```python
# Cache strategy stats if calculating multiple positions
stats_cache = {}
for signal in signals:
    if signal.strategy not in stats_cache:
        stats_cache[signal.strategy] = kelly_sizer.get_strategy_stats(signal.strategy)

    # Use cached stats internally (future enhancement)
    quantity = kelly_sizer.calculate_position_size(signal, account_balance)
```

## Safety Features

### 1. Maximum Position Cap (10%)
- Hard limit prevents over-concentration
- Even if Kelly suggests 50%, capped at 10%

### 2. Minimum Position (1 contract)
- Prevents fractional positions that can't be executed
- Ensures all valid signals result in executable trades

### 3. Negative Kelly Handling
- Strategies with negative expectancy use 1% fallback
- Prevents shorting the account (Kelly can suggest negative positions)

### 4. Fractional Kelly
- Default 0.5x reduces risk of over-betting
- Kelly formula assumes perfect knowledge (never true in reality)

### 5. Insufficient Data Fallback
- New strategies start with conservative fixed %
- Prevents wild swings from small sample sizes

## Monitoring

### Log Output

```
Kelly Position Sizing for TREND_FOLLOWING:
  Trade History: 60 trades
  Win Rate: 60.00%
  Avg Win: $100.00
  Avg Loss: $50.00
  Raw Kelly %: 40.00%
  Adjusted Kelly % (×0.5): 20.00%
  Dollar Risk: $2000.00
  Stop Distance: 2.00%
  Position Value: $100000.00
  Quantity: 2.0000 contracts
```

### Recommended Metrics to Track

1. **Kelly % by Strategy**
   - Monitor which strategies have highest Kelly %
   - Identify underperforming strategies

2. **Actual vs. Recommended Fraction**
   - Compare your kelly_fraction to recommended
   - Adjust based on overall account performance

3. **Fallback Usage Rate**
   - Track how often fallback is used vs. Kelly
   - Goal: Accumulate 50+ trades per strategy

4. **Position Size Distribution**
   - Monitor average position size per strategy
   - Detect over-concentration

## Troubleshooting

### Issue: All positions sized at 1.0 contract

**Cause:** Account balance too small or stop losses too tight

**Solution:**
- Increase account balance
- Use wider stop losses (3-5% instead of 1-2%)
- Lower fallback percentages

### Issue: Negative Kelly warnings

**Cause:** Strategy has negative expectancy (losing money)

**Solution:**
- Review strategy parameters
- Stop trading that strategy until fixed
- Analyze why avg loss > proportional avg win

### Issue: Position sizes seem too large

**Cause:** High Kelly % from strong performance

**Solution:**
- Already capped at 10% maximum (safe)
- Consider lowering kelly_fraction to 0.33 or 0.25
- Review if recent wins are sustainable

### Issue: Database query errors

**Cause:** Missing trade history or database schema issues

**Solution:**
- Ensure trades table exists with correct schema
- Check that `pnl` column is populated
- Verify `status = 'closed'` for completed trades

## Advanced Usage

### Dynamic Kelly Fraction Adjustment

```python
class AdaptiveKellySizer:
    def __init__(self, kelly_sizer):
        self.kelly_sizer = kelly_sizer
        self.performance_tracker = PerformanceTracker()

    def adjust_based_on_drawdown(self, current_drawdown: float):
        """Reduce Kelly fraction during drawdowns."""
        if current_drawdown > 0.15:  # > 15% drawdown
            self.kelly_sizer.update_kelly_fraction(0.25)
        elif current_drawdown > 0.10:  # > 10% drawdown
            self.kelly_sizer.update_kelly_fraction(0.33)
        else:
            self.kelly_sizer.update_kelly_fraction(0.5)

    def adjust_based_on_consecutive_losses(self, losses: int):
        """Reduce Kelly fraction after consecutive losses."""
        if losses >= 5:
            self.kelly_sizer.update_kelly_fraction(0.25)
        elif losses >= 3:
            self.kelly_sizer.update_kelly_fraction(0.33)
        else:
            self.kelly_sizer.update_kelly_fraction(0.5)
```

### Per-Strategy Kelly Fractions

```python
# Different fractions for different strategies
strategy_fractions = {
    StrategyType.TREND_FOLLOWING: 0.5,      # Standard
    StrategyType.MEAN_REVERSION: 0.33,      # Conservative
    StrategyType.LIQUIDATION_CAPTURE: 0.25  # Very conservative
}

def calculate_with_strategy_fraction(signal, account_balance):
    kelly_fraction = strategy_fractions.get(signal.strategy, 0.5)
    kelly_sizer.update_kelly_fraction(kelly_fraction)
    return kelly_sizer.calculate_position_size(signal, account_balance)
```

## References

- **Kelly Criterion**: [Wikipedia](https://en.wikipedia.org/wiki/Kelly_criterion)
- **Fractional Kelly**: Recommended by Thorp, MacLean, Ziemba
- **Position Sizing**: Van Tharp, "Trade Your Way to Financial Freedom"

## License

Internal trading bot module - proprietary.

## Support

For issues or questions, contact the development team.

---

**Last Updated:** 2026-01-10
**Version:** 1.0.0
