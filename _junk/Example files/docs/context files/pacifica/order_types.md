# Pacifica Order Types

## Overview

Pacifica supports multiple order types for flexible trading strategies. Each order type has specific use cases and execution characteristics.

## Market Orders

**Description**: Execute immediately at the best available price.

**Use Cases**:
- Need immediate execution
- High liquidity markets
- Closing positions quickly

**Characteristics**:
- Immediate execution (or rejection if insufficient liquidity)
- No price guarantee
- Takes liquidity (taker fees)
- Cannot be cancelled (executes immediately)

**API**: `POST /orders/market`

**Parameters**:
- `market`: Market symbol (required)
- `side`: "buy" or "sell" (required)
- `size`: Order size in base currency (required)
- `reduce_only`: Only reduce existing position (optional, default false)

**Example**:
```json
{
    "market": "BTC-PERP",
    "side": "buy",
    "size": 1.0,
    "reduce_only": false
}
```

**Risks**:
- Slippage in volatile/low liquidity markets
- May get worse price than expected
- Cannot control execution price

## Limit Orders

**Description**: Execute at specified price or better.

**Use Cases**:
- Price control important
- Willing to wait for specific price
- Providing liquidity for rebates

**Characteristics**:
- Execution only at limit price or better
- May not execute if price not reached
- Can provide liquidity (maker fees/rebates)
- Can be cancelled while open

**API**: `POST /orders/limit`

**Parameters**:
- `market`: Market symbol (required)
- `side`: "buy" or "sell" (required)
- `size`: Order size (required)
- `price`: Limit price (required)
- `post_only`: Only make, never take (optional, default false)
- `reduce_only`: Only reduce position (optional, default false)
- `time_in_force`: Order lifetime (optional, default "GTC")

**Time in Force Options**:
- `GTC` (Good-Til-Cancel): Remains open until filled or cancelled
- `IOC` (Immediate-Or-Cancel): Fill immediately, cancel remainder
- `FOK` (Fill-Or-Kill): Fill entire order immediately or cancel

**Example**:
```json
{
    "market": "BTC-PERP",
    "side": "buy",
    "size": 1.0,
    "price": 49500.0,
    "post_only": true,
    "time_in_force": "GTC"
}
```

**Post-Only Orders**:
- Guarantees maker fees (never takes liquidity)
- Rejected if would execute immediately
- Useful for market making strategies

**Risks**:
- May not execute if price moves away
- Opportunity cost of waiting

## Stop Orders

**Description**: Conditional order triggered when stop price is reached.

**Use Cases**:
- Stop-loss protection
- Breakout entry strategies
- Risk management

**Characteristics**:
- Dormant until stop price reached
- Converts to market or limit order when triggered
- Uses mark price (not last price) to prevent manipulation

**API**: `POST /orders/stop`

**Parameters**:
- `market`: Market symbol (required)
- `side`: "buy" or "sell" (required)
- `size`: Order size (required)
- `stop_price`: Trigger price (required)
- `order_type`: "market" or "limit" (required)
- `limit_price`: If order_type is "limit" (conditional)
- `reduce_only`: Only reduce position (optional, default true for stop-loss)

**Stop Market Order Example**:
```json
{
    "market": "BTC-PERP",
    "side": "sell",
    "size": 1.0,
    "stop_price": 48000.0,
    "order_type": "market",
    "reduce_only": true
}
```

**Stop Limit Order Example**:
```json
{
    "market": "BTC-PERP",
    "side": "sell",
    "size": 1.0,
    "stop_price": 48000.0,
    "order_type": "limit",
    "limit_price": 47900.0,
    "reduce_only": true
}
```

**Trigger Logic**:
- **Buy stop**: Triggers when mark price >= stop price
- **Sell stop**: Triggers when mark price <= stop price

**Risks**:
- Stop market orders may execute at worse price (slippage)
- Stop limit orders may not execute if price gaps through
- Stop running (triggered then price reverses)

## Position TP/SL Orders

**Description**: Take-profit and stop-loss orders attached to existing position.

**Use Cases**:
- Automated profit taking
- Risk management
- Position-level automation

**Characteristics**:
- Automatically close position at target prices
- Can set both TP and SL simultaneously
- Cancels when position closes
- One cancels the other (OCO)

**API**: `POST /orders/tp-sl`

**Parameters**:
- `market`: Market symbol (required)
- `take_profit_price`: TP trigger price (optional)
- `stop_loss_price`: SL trigger price (optional)

**Example**:
```json
{
    "market": "BTC-PERP",
    "take_profit_price": 55000.0,
    "stop_loss_price": 48000.0
}
```

**Behavior**:
- If long position: TP sells at higher price, SL sells at lower price
- If short position: TP buys at lower price, SL buys at higher price
- When one executes, the other is automatically cancelled
- Can update TP/SL by sending new request (replaces existing)

**Best Practices**:
1. Set SL immediately after opening position
2. Use realistic TP targets (consider fees and funding)
3. Update SL to breakeven as position becomes profitable
4. Factor in funding costs for longer holds

## Batch Orders

**Description**: Submit multiple orders in single request.

**Use Cases**:
- Opening multiple positions simultaneously
- Complex order strategies
- Reducing API calls

**Characteristics**:
- Execute multiple orders atomically (all or none option may exist)
- Faster than individual requests
- Rate limit friendly

**API**: `POST /orders/batch`

**Parameters**:
- `orders`: Array of order objects

**Example**:
```json
{
    "orders": [
        {
            "market": "BTC-PERP",
            "side": "buy",
            "size": 1.0,
            "type": "limit",
            "price": 49500.0
        },
        {
            "market": "ETH-PERP",
            "side": "buy",
            "size": 10.0,
            "type": "market"
        },
        {
            "market": "SOL-PERP",
            "side": "sell",
            "size": 100.0,
            "type": "limit",
            "price": 110.0,
            "post_only": true
        }
    ]
}
```

**Response**: Returns status for each order individually.

## Reduce-Only Orders

**Description**: Order flag that prevents increasing position size.

**Use Cases**:
- Closing positions without risk of reversal
- Partial profit taking
- Risk management

**Characteristics**:
- Can only reduce or close existing position
- Rejected if would open or increase position
- Useful for stop-loss and take-profit orders
- Available on market, limit, and stop orders

**Example**:
```json
{
    "market": "BTC-PERP",
    "side": "sell",
    "size": 0.5,
    "type": "limit",
    "price": 52000.0,
    "reduce_only": true
}
```

**Use Case**: If you have 1.0 BTC long position:
- Reduce-only sell for 0.5 BTC: ✅ Reduces position to 0.5 BTC
- Reduce-only sell for 1.5 BTC: ❌ Rejected (would create short)
- Regular sell for 1.5 BTC: ✅ Closes long and opens 0.5 BTC short

## Order Status Lifecycle

### Status Values:
1. **pending**: Stop orders waiting for trigger
2. **open**: Active order in orderbook
3. **partially_filled**: Some fills, still open
4. **filled**: Completely filled
5. **cancelled**: Cancelled by user
6. **rejected**: Rejected by system
7. **expired**: Expired (for time-limited orders)

### Lifecycle Flow:
```
Market Order:    open -> filled (or rejected)
Limit Order:     open -> partially_filled -> filled (or cancelled)
Stop Order:      pending -> open -> filled (or cancelled)
```

## Order Priority

**Price Priority**: Better prices execute first
- Buy orders: Higher price has priority
- Sell orders: Lower price has priority

**Time Priority**: At same price, earlier orders execute first (FIFO)

**Order Types Priority**:
1. Post-only orders (provide liquidity)
2. Regular limit orders
3. Market orders (take liquidity)

## Fee Structure

### Maker Fees (Provide Liquidity):
- Limit orders that don't execute immediately
- Post-only orders
- May receive rebate depending on volume tier

### Taker Fees (Take Liquidity):
- Market orders
- Limit orders that execute immediately
- IOC/FOK orders

**Fee Calculation**:
```
fee = filled_size * fill_price * fee_rate
```

See market specifications for specific fee rates.

## Order Validation

Orders are rejected if:
- Insufficient margin
- Order size below minimum or above maximum
- Invalid price (outside tick size)
- Invalid size (outside lot size)
- Would cause self-trade (some scenarios)
- Market is closed/halted
- Leverage limit exceeded
- Rate limit exceeded

## Advanced Order Strategies

### Iceberg Orders
Not directly supported, but can be simulated:
- Place small visible order
- When filled, place another
- Repeat until total size achieved

### Trailing Stop
Not directly supported, but can be implemented in bot:
1. Monitor position
2. Calculate trailing stop price
3. Update stop order as price moves favorably

### Grid Trading
Use batch orders to place multiple limit orders at intervals:
```python
base_price = 50000
for i in range(-5, 6):
    price = base_price + (i * 100)
    side = "buy" if i < 0 else "sell"
    # Place limit order at price
```

### TWAP/VWAP
Execute large orders over time:
- Split into smaller chunks
- Execute at intervals (TWAP)
- Execute based on volume (VWAP)

## WebSocket vs REST for Orders

**REST API**:
- Simpler implementation
- Request-response model
- Good for occasional trading

**WebSocket**:
- Lower latency
- Real-time order updates
- Better for high-frequency trading
- More complex implementation

## Error Handling

Common order errors:
- `INSUFFICIENT_BALANCE`: Not enough margin
- `INVALID_ORDER_SIZE`: Size outside [min, max]
- `INVALID_PRICE`: Price outside tick size
- `MARKET_CLOSED`: Market not available
- `POSITION_LIMIT_EXCEEDED`: Too large position
- `RATE_LIMIT_EXCEEDED`: Too many orders

See `error_codes.md` for complete error reference.

## Best Practices for Bots

1. **Use Limit Orders**: More predictable costs
2. **Set Post-Only**: Avoid taker fees when possible
3. **Always Set Stop-Loss**: Risk management
4. **Validate Before Submitting**: Check margin, size, price
5. **Handle Partial Fills**: Don't assume complete fills
6. **Monitor Order Status**: Don't assume success
7. **Use Batch Orders**: Reduce API calls
8. **Implement Retry Logic**: Handle network errors
9. **Track Order IDs**: Match responses to requests
10. **Consider Funding Costs**: Factor into profit targets

## Python Examples

### Place Market Order
```python
order = client.create_market_order(
    market="BTC-PERP",
    side="buy",
    size=1.0
)
```

### Place Limit Order with TP/SL
```python
# Open position with limit order
order = client.create_limit_order(
    market="BTC-PERP",
    side="buy",
    size=1.0,
    price=49500.0
)

# Wait for fill, then set TP/SL
if order["status"] == "filled":
    client.create_tp_sl(
        market="BTC-PERP",
        take_profit_price=55000.0,
        stop_loss_price=48000.0
    )
```

### Batch Order Strategy
```python
orders = [
    {
        "market": "BTC-PERP",
        "side": "buy",
        "type": "limit",
        "size": 0.5,
        "price": 49000.0,
        "post_only": True
    },
    {
        "market": "BTC-PERP",
        "side": "buy",
        "type": "limit",
        "size": 0.5,
        "price": 48500.0,
        "post_only": True
    }
]

response = client.batch_order(orders)
```

## Additional Resources

- See `api_reference_rest.md` for API details
- See `api_reference_websocket.md` for WebSocket trading
- See `trading_mechanics.md` for leverage and margin
- See `market_specs.md` for size/price limits
