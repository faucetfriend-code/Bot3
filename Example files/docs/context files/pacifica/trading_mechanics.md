# Pacifica Trading Mechanics

## Overview

Pacifica is a Solana-based perpetual futures DEX offering high leverage trading with sophisticated risk management systems. This document covers leverage, margin modes, liquidation mechanics, and position management.

## Leverage

### Leverage Range
- **Minimum**: 5x
- **Maximum**: 50x
- **Variation**: Leverage limits vary by market/asset
- **Configuration**: Can be adjusted per market via API endpoints

### Leverage Calculation
```
position_size = margin × leverage
required_margin = position_size / leverage
```

### Leverage Adjustment
- Can be modified while position is open
- Requires sufficient margin to support new leverage
- API Endpoint: `POST /account/leverage`

### Leverage Considerations
1. **Higher Leverage = Higher Risk**: Closer to liquidation price
2. **Margin Requirements**: Increase with leverage
3. **Funding Costs**: Applied to full position size (margin × leverage)
4. **Liquidation Distance**: Inversely proportional to leverage

## Margin Modes

Pacifica supports two distinct margin systems. Choose based on risk tolerance and trading strategy.

### Cross-Margin Mode

**Definition**: Pool collateral across all positions

**Characteristics**:
- All available account balance acts as collateral
- Losing positions can draw from winning positions
- More capital efficient
- Shared liquidation risk across portfolio

**Advantages**:
- Lower liquidation risk for individual positions
- Better capital efficiency
- Profitable positions support losing ones
- Ideal for correlated positions (hedging)

**Disadvantages**:
- One bad position can affect entire account
- Less position-level control
- Harder to track per-position risk

**Use Cases**:
- Market making strategies
- Hedged positions
- Portfolio approach to risk management
- Traders comfortable with account-level risk

### Isolated-Margin Mode

**Definition**: Segregate collateral per individual position

**Characteristics**:
- Each position has dedicated margin
- Maximum loss limited to position's margin
- Independent liquidation per position
- Better risk compartmentalization

**Advantages**:
- Risk contained to single position
- Clear per-position risk/reward
- No cascading failures across positions
- **CRITICAL**: Funding payments deducted from isolated margin
- Precise control over liquidation points

**Disadvantages**:
- Less capital efficient
- Higher liquidation risk per position
- Cannot benefit from profitable positions
- Requires more margin overall

**Use Cases**:
- High-risk/high-leverage trades
- Testing new strategies
- Speculative positions
- Limiting downside to specific amount

**CRITICAL for Bot Trading**:
- Funding payments reduce isolated margin balance
- Can push position toward liquidation
- Must monitor margin level after each hourly funding payment

### Switching Margin Modes

**API Endpoint**: `POST /account/margin-mode`

**Requirements**:
- No open positions in the market (usually)
- Or sufficient margin to support mode change

**Best Practice**: Set margin mode before opening positions

## Liquidation Mechanics

### Multi-Tiered Liquidation System

Pacifica uses a sophisticated **multi-tiered liquidation approach** rather than instant full liquidation. This helps prevent cascading failures and protects traders.

### Liquidation Price Calculation

**For Long Positions**:
```
liquidation_price = entry_price × (1 - 1/leverage + fees)
```

**For Short Positions**:
```
liquidation_price = entry_price × (1 + 1/leverage - fees)
```

### Factors Affecting Liquidation

1. **Leverage**: Higher leverage = closer liquidation price
2. **Entry Price**: Base calculation point
3. **Margin Balance**: More margin = further liquidation price
4. **Trading Fees**: Reduce effective margin
5. **Funding Payments**: Adjust margin balance (especially isolated margin)
6. **Mark Price**: Used for liquidation calculations (not last trade price)

### Mark Price vs Last Price

**Mark Price**:
- Fair price derived from oracle and index
- Used for liquidation calculations
- Prevents manipulation via thin order books
- More stable than last trade price

**Last Price**:
- Most recent trade execution price
- Used for display purposes
- Can be volatile in low liquidity

### Liquidation Process

1. **Monitoring**: System continuously monitors mark price vs liquidation price
2. **Tier 1 Warning**: Position health drops below threshold (e.g., 90%)
3. **Tier 2 Partial**: May partially reduce position to improve health
4. **Tier 3 Full**: Complete position liquidation if health continues dropping
5. **Insurance Fund**: Absorbs losses if liquidation doesn't cover

### Liquidation Price Updates

**Isolated Margin - CRITICAL**:
- Liquidation price recalculated **after each hourly funding payment**
- Negative funding reduces margin → moves liquidation price closer
- Positive funding increases margin → moves liquidation price further
- Must track 24 funding events per day

**Cross Margin**:
- Liquidation based on overall account balance
- Less affected by individual funding payments
- More stable liquidation levels

### Preventing Liquidation

1. **Add Margin**: Increase collateral for isolated positions
2. **Reduce Leverage**: Lower position size relative to margin
3. **Close Partial Position**: Reduce exposure
4. **Monitor Funding**: Close before high negative funding periods
5. **Set Stop-Loss**: Exit before liquidation
6. **Maintain Buffer**: Keep margin well above minimum

### Liquidation Fees

- Charged when position is liquidated
- Typically covers liquidation engine costs
- Reduces final payout to trader
- Should be factored into risk calculations

## Position Management

### Opening Positions

**Market Order**: Immediate execution at best available price
**Limit Order**: Execution at specified price or better

**Required Information**:
- Market symbol (e.g., "BTC-PERP")
- Side (LONG/SHORT)
- Size (in base currency or USD)
- Leverage (if not using account default)
- Margin mode (cross/isolated)

### Modifying Positions

**Increase Position**:
- Place additional order in same direction
- Adjusts average entry price

**Decrease Position**:
- Place order in opposite direction
- Reduces position size

**Reverse Position**:
- Close current position and open opposite
- Order size must exceed current position size

### Position Monitoring

**Key Metrics to Track**:
1. **Unrealized PnL**: Current profit/loss
2. **Realized PnL**: Closed position profit/loss
3. **Margin Ratio**: Available margin / required margin
4. **Liquidation Price**: Current liquidation level
5. **Funding Costs**: Accumulated funding payments
6. **Position Health**: Distance to liquidation (%)

**API Endpoints**:
- `GET /account/positions` - Get all positions
- `GET /account` - Get account info with margin details

### Take-Profit and Stop-Loss

**TP/SL Features**:
- Attached to existing positions
- Automatically close position at target prices
- Can be added after position is opened
- API: `POST /orders/tp-sl`

**Best Practices**:
1. Set stop-loss immediately after opening position
2. Use realistic take-profit targets
3. Update as position becomes profitable
4. Consider funding costs in profit targets

## Risk Management Best Practices

### For Automated Trading Bots

1. **Pre-Trade Checks**:
   - Verify sufficient margin
   - Calculate liquidation price before opening
   - Ensure leverage is within limits
   - Check current funding rate

2. **During Trade**:
   - Monitor position health continuously
   - Track hourly funding payments (24x/day)
   - Recalculate liquidation after each funding
   - Watch mark price vs last price divergence

3. **Position Limits**:
   - Set max position size per market
   - Limit total account exposure
   - Cap leverage based on volatility
   - Reserve margin for adverse moves

4. **Emergency Procedures**:
   - Auto-close if health drops below threshold
   - Reduce leverage during high volatility
   - Close positions before extreme funding events
   - Maintain minimum account balance

### Isolated vs Cross Margin Decision Tree

Choose **Isolated Margin** if:
- Testing new strategy with real funds
- Making high-risk/high-leverage trades
- Want to limit maximum loss
- Trading unrelated markets simultaneously
- Need precise risk control per position

Choose **Cross Margin** if:
- Running market making strategies
- Trading correlated pairs (hedging)
- Want maximum capital efficiency
- Comfortable with account-level risk
- Have multiple profitable positions

## API Integration Examples

### Set Leverage
```python
# POST /account/leverage
{
    "market": "BTC-PERP",
    "leverage": 10
}
```

### Set Margin Mode
```python
# POST /account/margin-mode
{
    "market": "BTC-PERP",
    "margin_mode": "isolated"  # or "cross"
}
```

### Get Position Details
```python
# GET /account/positions
# Returns:
{
    "positions": [
        {
            "market": "BTC-PERP",
            "side": "long",
            "size": 1.5,
            "entry_price": 50000,
            "mark_price": 51000,
            "liquidation_price": 45000,
            "unrealized_pnl": 1500,
            "margin": 7500,
            "leverage": 10,
            "margin_mode": "isolated"
        }
    ]
}
```

### Calculate Position Metrics
```python
def calculate_liquidation_price(entry_price, leverage, side, fees=0.001):
    """Calculate liquidation price for a position."""
    if side == "long":
        return entry_price * (1 - 1/leverage + fees)
    else:  # short
        return entry_price * (1 + 1/leverage - fees)

def calculate_position_health(mark_price, liquidation_price, side):
    """Calculate position health percentage."""
    if side == "long":
        return ((mark_price - liquidation_price) / mark_price) * 100
    else:  # short
        return ((liquidation_price - mark_price) / mark_price) * 100

def adjust_liquidation_for_funding(current_liq_price, funding_payment,
                                   position_size, leverage, side):
    """Adjust liquidation price after funding payment (isolated margin only)."""
    # Funding reduces/increases effective margin
    margin_change = funding_payment
    # Recalculate liquidation with new margin
    # Implementation depends on exact margin formula
    pass
```

## Additional Resources

- See `funding_rates.md` for detailed funding calculation
- See `order_types.md` for order execution details
- See `api_reference_rest.md` for complete API documentation
