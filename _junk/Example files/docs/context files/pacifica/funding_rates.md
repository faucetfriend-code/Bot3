# Pacifica Funding Rates

## Overview

**CRITICAL**: Pacifica uses **HOURLY funding rates** (24 times per day), which is significantly more frequent than the standard 8-hour funding (3 times per day) used by most perpetual futures platforms. This has major implications for trading costs and strategy.

## Frequency

- **Update Frequency**: Every hour (24x per day)
- **Sampling Rate**: Every 5 seconds
- **Display**: TWAP (Time-Weighted Average Price) of the estimated next 1-hour funding rate
- **Application**: At each 1-hour interval, the average rate is applied to all open positions

## Calculation Formula

```
funding_rate = (premium_index + clamp(interest_rate - premium_index, -0.05%, 0.05%)) / 8
```

### Components:

1. **Premium Index**:
   ```
   premium_index = (impact_price / oracle_price) - 1
   ```
   - `impact_price`: Average execution price for defined Impact Notional amounts
   - `oracle_price`: Reference price from price oracle

2. **Interest Rate**: Fixed at 0.01%

3. **Clamp Function**: Maintains ±0.05% bounds for stability
   - Prevents extreme funding rate volatility
   - `clamp(x, -0.05%, 0.05%)` limits x to [-0.05%, 0.05%] range

4. **Division by 8**: Scales the 8-hour standard rate to 1-hour intervals

### Impact Notional Values:
- **BTC**: $20,000
- **Other Assets**: $6,000

## Payment Mechanism

### Payment Caps
Funding payments are capped at **±4% per hour** to protect traders from extreme funding costs.

### Payment Direction

**Positive Funding Rate** (Premium is positive):
- Long position holders **PAY** funding
- Short position holders **RECEIVE** funding
- Indicates longs are paying shorts (bullish sentiment premium)

**Negative Funding Rate** (Premium is negative):
- Short position holders **PAY** funding
- Long position holders **RECEIVE** funding
- Indicates shorts are paying longs (bearish sentiment premium)

### Payment Processing
- Payments are automatically deducted from or added to account balances
- No manual action required
- Applied to all open positions at the hour mark

## Effect on Positions

### Isolated Margin Positions
**CRITICAL**: For isolated margin positions:
- Funding payments are deducted from the **isolated margin balance**
- This directly affects the position's **liquidation price**
- Liquidation prices are **recalculated after each funding payout** to reflect adjusted margin levels
- Multiple negative funding payments can push positions closer to liquidation

### Cross Margin Positions
- Funding payments affect the overall account balance
- Impact on liquidation is distributed across all positions

## Trading Strategy Implications

### Cost Analysis
With 24 hourly payments vs. 3 eight-hour payments:
- **24x more frequent** cost/income events
- Funding costs accumulate faster
- More opportunities to benefit from favorable funding rates

### Example Calculation:
If funding rate = 0.01% per hour:
- **Hourly system (Pacifica)**: 0.01% × 24 = 0.24% per day
- **8-hour system (standard)**: 0.01% × 3 = 0.03% per day
- Pacifica costs **8x more** for the same hourly rate

### Risk Management Considerations:
1. **Monitor funding rates constantly** - they change every hour
2. **Consider funding costs in strategy P&L** - can significantly impact profitability
3. **Watch for funding rate arbitrage opportunities** between platforms
4. **For isolated margin**: Track how funding affects liquidation prices
5. **High-frequency strategies**: May benefit from or be hurt by frequent funding

## API Integration

### Get Historical Funding
```
GET /markets/funding
```
Returns historical funding rate data for analysis.

### Get Current Funding Rate
Available via:
- REST API: Market info endpoints
- WebSocket: Real-time funding rate updates

## Monitoring Best Practices

1. **Track Funding Costs**: Log all funding payments for P&L analysis
2. **Set Alerts**: Notify when funding rates exceed thresholds (e.g., ±0.05% per hour)
3. **Calculate Daily Impact**: Multiply hourly rate × 24 for daily cost estimation
4. **Compare with PnL**: Ensure funding costs don't exceed trading profits
5. **Isolated Margin Watch**: Monitor margin balance depletion from negative funding

## Common Pitfalls

1. **Underestimating Costs**: Forgetting to multiply by 24 hours
2. **Ignoring Liquidation Impact**: Not accounting for funding's effect on isolated margin
3. **Holding Through High Funding**: Keeping positions open during extreme funding periods
4. **Strategy Backtesting**: Using 8-hour funding models when testing Pacifica strategies

## Formula Reference for Agents

For calculating expected funding costs:
```python
# Hourly funding cost
hourly_cost = position_size * funding_rate

# Daily funding cost (24 hours)
daily_cost = position_size * funding_rate * 24

# Effect on isolated margin
new_margin = current_margin - funding_payment
new_liquidation_price = calculate_liquidation(new_margin, position_size, leverage)
```
