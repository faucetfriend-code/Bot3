# Kelly Position Sizer - Quick Reference

## Formula
```
Kelly % = (Win Rate × Avg Win - Loss Rate × Avg Loss) ÷ Avg Win
Position Size = (Account × Kelly % × Kelly Fraction) ÷ Stop Distance %
```

## Quick Start

```python
from kelly_position_sizer import KellyPositionSizer
from database import DatabaseManager

# Initialize
db = DatabaseManager()
kelly_sizer = KellyPositionSizer(db=db, kelly_fraction=0.5, min_trades=50)

# Calculate position size
quantity = kelly_sizer.calculate_position_size(signal, account_balance=10000.0)
```

## Key Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `kelly_fraction` | 0.5 | Fraction of Kelly to use (0.25-1.0) |
| `min_trades` | 50 | Minimum trades before using Kelly |
| `max_position_pct` | 0.10 | Maximum 10% of account per trade |

## Kelly Fraction Guide

| Fraction | Risk Level | When to Use |
|----------|------------|-------------|
| 0.25 | Very Conservative | New strategies, high volatility, after losses |
| 0.33 | Conservative | Moderate performance, uncertain conditions |
| 0.50 | Balanced | Strong performance, normal conditions |
| 1.00 | Aggressive | **NOT RECOMMENDED** (too risky) |

## Fallback Percentages (< 50 trades)

| Strategy | % | Rationale |
|----------|---|-----------|
| Trend Following | 2.0% | Standard directional |
| MA Crossover | 2.0% | Similar to trend |
| Mean Reversion | 1.5% | Frequent, smaller moves |
| Grid Trading | 0.5% | Many small positions |
| Liquidation Capture | 2.5% | High conviction, rare |
| Breakout | 2.0% | Standard momentum |

## Methods

### Calculate Position Size
```python
quantity = kelly_sizer.calculate_position_size(
    signal=signal,              # Signal with strategy, entry, stop
    account_balance=10000.0,    # Current account balance
    account_id="sub_1"          # Account ID (optional)
)
```

### Get Strategy Stats
```python
stats = kelly_sizer.get_strategy_stats(StrategyType.TREND_FOLLOWING)
# Returns: {"win_rate": 0.6, "avg_win": 100.0, "avg_loss": 50.0, "total_trades": 75}
```

### Update Kelly Fraction
```python
kelly_sizer.update_kelly_fraction(0.25)  # More conservative
kelly_sizer.update_kelly_fraction(0.5)   # Back to balanced
```

### Get Recommended Fraction
```python
recommended = kelly_sizer.get_recommended_kelly_fraction(StrategyType.TREND_FOLLOWING)
# Returns: 0.5 (strong), 0.33 (moderate), or 0.25 (weak/insufficient data)
```

## Calculation Example

**Given:**
- Account: $10,000
- Strategy: 60% win rate, $100 avg win, $50 avg loss, 75 trades
- Entry: $50,000, Stop: $49,000 (2% stop)
- Kelly Fraction: 0.5

**Calculation:**
1. Kelly % = (0.6 × 100 - 0.4 × 50) / 100 = 40%
2. Adjusted Kelly = 40% × 0.5 = 20%
3. Capped at 10% max = **10%**
4. Dollar Risk = $10,000 × 10% = **$1,000**
5. Position Value = $1,000 / 0.02 = **$50,000**
6. Quantity = $50,000 / $50,000 = **1.0 contract**

## Safety Features

✅ **10% Maximum Cap** - Never risk more than 10% of account
✅ **1.0 Minimum** - All positions are at least 1 contract
✅ **Negative Kelly → 1% Fallback** - Losing strategies use minimal size
✅ **Fractional Kelly** - Reduces risk of over-betting
✅ **Insufficient Data Fallback** - Conservative sizing for new strategies

## Integration Pattern

```python
class TradingBot:
    def __init__(self):
        self.kelly_sizer = KellyPositionSizer(
            db=self.db,
            kelly_fraction=0.5,
            min_trades=50
        )

    def process_signal(self, signal: Signal):
        quantity = self.kelly_sizer.calculate_position_size(
            signal=signal,
            account_balance=self.get_account_balance()
        )
        signal.quantity = quantity
        if quantity >= 1.0:
            self.execute_trade(signal)
```

## Monitoring Checklist

- [ ] Track Kelly % by strategy (identify winners/losers)
- [ ] Monitor fallback vs. Kelly usage (goal: 50+ trades per strategy)
- [ ] Compare actual vs. recommended Kelly fraction
- [ ] Watch for negative Kelly warnings (strategy issues)
- [ ] Review position size distribution (avoid over-concentration)

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| All positions = 1.0 | Small account or tight stops | Increase balance, wider stops |
| Negative Kelly warnings | Losing strategy | Review strategy, stop trading it |
| Sizes too large | High Kelly % | Already capped at 10%, reduce fraction if needed |
| Database errors | Missing trades table | Check schema, ensure trades logged |

## Common Commands

```python
# Get performance stats
stats = kelly_sizer.get_strategy_stats(StrategyType.TREND_FOLLOWING)
print(f"Win Rate: {stats['win_rate']:.2%}, Trades: {stats['total_trades']}")

# Adjust risk level
kelly_sizer.update_kelly_fraction(0.25)  # After drawdown
kelly_sizer.update_kelly_fraction(0.5)   # Back to normal

# Check recommendation
rec = kelly_sizer.get_recommended_kelly_fraction(StrategyType.MEAN_REVERSION)
print(f"Recommended fraction: {rec}")
```

## Performance Metrics

**Profit Factor** = (Win Rate × Avg Win) / ((1 - Win Rate) × Avg Loss)

| Profit Factor | Win Rate | Recommended Fraction |
|--------------|----------|---------------------|
| ≥ 2.0 | ≥ 55% | 0.5 (strong) |
| ≥ 1.5 | ≥ 45% | 0.33 (moderate) |
| < 1.5 | < 45% | 0.25 (weak) |

## Testing

```bash
# Run tests
pytest test_kelly_position_sizer.py -v

# Run examples
python kelly_position_sizer_example.py
```

## Files

- `kelly_position_sizer.py` - Main module
- `test_kelly_position_sizer.py` - Test suite
- `kelly_position_sizer_example.py` - Usage examples
- `KELLY_POSITION_SIZER_README.md` - Full documentation
- `KELLY_QUICK_REFERENCE.md` - This file

---

**Version:** 1.0.0 | **Updated:** 2026-01-10
