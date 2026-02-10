# Pacifica Market Specifications

## Overview

Each market on Pacifica has specific parameters defining trading rules. Understanding these specifications is critical for order validation and risk management.

## Market Parameters

### Common Specifications

Every market has these parameters:

1. **Symbol**: Market identifier (e.g., "BTC-PERP")
2. **Base Currency**: Underlying asset (e.g., BTC)
3. **Quote Currency**: Pricing currency (usually USD)
4. **Tick Size**: Minimum price increment
5. **Lot Size**: Minimum size increment
6. **Min Order Size**: Smallest allowed order
7. **Max Order Size**: Largest single order
8. **Min Leverage**: Minimum leverage (usually 5x)
9. **Max Leverage**: Maximum leverage (varies by market)
10. **Trading Status**: active, halted, or closed

### Getting Market Specifications

**API**: `GET /markets`

```python
markets = client.get_markets()

for market in markets["markets"]:
    print(f"{market['symbol']}: "
          f"Leverage {market['min_leverage']}x-{market['max_leverage']}x, "
          f"Tick ${market['tick_size']}, "
          f"Lot {market['lot_size']}")
```

## Major Markets (Examples)

### BTC-PERP (Bitcoin Perpetual)

```json
{
    "symbol": "BTC-PERP",
    "base_currency": "BTC",
    "quote_currency": "USD",
    "tick_size": 0.5,
    "lot_size": 0.001,
    "min_order_size": 0.001,
    "max_order_size": 100,
    "min_leverage": 5,
    "max_leverage": 50,
    "maker_fee": 0.0002,
    "taker_fee": 0.0005,
    "status": "active"
}
```

**Specifications**:
- Price moves in $0.50 increments
- Size moves in 0.001 BTC (1/1000th) increments
- Minimum order: 0.001 BTC (~$50 at $50k BTC)
- Maximum order: 100 BTC
- Leverage: 5x to 50x

### ETH-PERP (Ethereum Perpetual)

```json
{
    "symbol": "ETH-PERP",
    "base_currency": "ETH",
    "quote_currency": "USD",
    "tick_size": 0.1,
    "lot_size": 0.01,
    "min_order_size": 0.01,
    "max_order_size": 1000,
    "min_leverage": 5,
    "max_leverage": 50,
    "maker_fee": 0.0002,
    "taker_fee": 0.0005,
    "status": "active"
}
```

### SOL-PERP (Solana Perpetual)

```json
{
    "symbol": "SOL-PERP",
    "base_currency": "SOL",
    "quote_currency": "USD",
    "tick_size": 0.01,
    "lot_size": 0.1,
    "min_order_size": 1.0,
    "max_order_size": 10000,
    "min_leverage": 5,
    "max_leverage": 50,
    "maker_fee": 0.0002,
    "taker_fee": 0.0005,
    "status": "active"
}
```

## Order Size Validation

### Tick Size Validation

Price must be a multiple of tick size:

```python
def validate_price(price: float, tick_size: float) -> bool:
    """Validate price matches tick size."""
    return (price % tick_size) == 0

def round_to_tick(price: float, tick_size: float) -> float:
    """Round price to nearest tick."""
    return round(price / tick_size) * tick_size

# Example
price = 50001.7  # Invalid for BTC (tick size 0.5)
valid_price = round_to_tick(price, 0.5)  # 50001.5
```

### Lot Size Validation

Order size must be a multiple of lot size:

```python
def validate_size(size: float, lot_size: float) -> bool:
    """Validate size matches lot size."""
    return (size % lot_size) == 0

def round_to_lot(size: float, lot_size: float) -> float:
    """Round size to nearest lot."""
    return round(size / lot_size) * lot_size

# Example
size = 0.0156  # Invalid for BTC (lot size 0.001)
valid_size = round_to_lot(size, 0.001)  # 0.016
```

### Min/Max Size Validation

```python
def validate_size_limits(size: float, min_size: float, max_size: float) -> bool:
    """Validate size within limits."""
    return min_size <= size <= max_size

# Example
if not validate_size_limits(0.0005, 0.001, 100):
    raise ValueError("Order size below minimum")
```

### Complete Order Validator

```python
class OrderValidator:
    def __init__(self, market_specs: dict):
        self.tick_size = market_specs["tick_size"]
        self.lot_size = market_specs["lot_size"]
        self.min_size = market_specs["min_order_size"]
        self.max_size = market_specs["max_order_size"]
        self.min_leverage = market_specs["min_leverage"]
        self.max_leverage = market_specs["max_leverage"]

    def validate_price(self, price: float) -> tuple[bool, str]:
        """Validate order price."""
        if (price % self.tick_size) != 0:
            rounded = round_to_tick(price, self.tick_size)
            return False, f"Price must be multiple of {self.tick_size}. Use {rounded}"
        return True, ""

    def validate_size(self, size: float) -> tuple[bool, str]:
        """Validate order size."""
        # Check lot size
        if (size % self.lot_size) != 0:
            rounded = round_to_lot(size, self.lot_size)
            return False, f"Size must be multiple of {self.lot_size}. Use {rounded}"

        # Check min/max
        if size < self.min_size:
            return False, f"Size below minimum {self.min_size}"
        if size > self.max_size:
            return False, f"Size above maximum {self.max_size}"

        return True, ""

    def validate_leverage(self, leverage: int) -> tuple[bool, str]:
        """Validate leverage."""
        if leverage < self.min_leverage:
            return False, f"Leverage below minimum {self.min_leverage}x"
        if leverage > self.max_leverage:
            return False, f"Leverage above maximum {self.max_leverage}x"
        return True, ""

    def validate_order(self, price: float, size: float, leverage: int = None) -> tuple[bool, list[str]]:
        """Validate complete order."""
        errors = []

        valid, msg = self.validate_price(price)
        if not valid:
            errors.append(msg)

        valid, msg = self.validate_size(size)
        if not valid:
            errors.append(msg)

        if leverage:
            valid, msg = self.validate_leverage(leverage)
            if not valid:
                errors.append(msg)

        return len(errors) == 0, errors

# Usage
validator = OrderValidator(market_specs)
valid, errors = validator.validate_order(50001.5, 0.1, 20)
if not valid:
    for error in errors:
        print(f"Error: {error}")
```

## Fee Structure

### Fee Tiers

Fees based on 30-day trading volume:

| Tier | Volume (USD) | Maker Fee | Taker Fee |
|------|--------------|-----------|-----------|
| 1 | < $1M | 0.02% | 0.05% |
| 2 | $1M - $10M | 0.015% | 0.04% |
| 3 | $10M - $50M | 0.01% | 0.03% |
| 4 | $50M - $100M | 0.005% | 0.025% |
| 5 | $100M+ | 0.00% (rebate -0.005%) | 0.02% |

### Fee Calculation

```python
def calculate_trading_fee(
    position_value: float,
    fee_rate: float,
    is_maker: bool = False
) -> float:
    """Calculate trading fee."""
    fee = position_value * fee_rate
    return fee if fee > 0 else 0  # Rebates capped at 0

# Example
position_value = 50000.0  # $50k position
maker_fee_rate = 0.0002  # 0.02%
fee = calculate_trading_fee(position_value, maker_fee_rate, is_maker=True)
# fee = 50000 * 0.0002 = $10
```

### Fee Impact on P&L

```python
def calculate_pnl_with_fees(
    entry_price: float,
    exit_price: float,
    size: float,
    leverage: int,
    maker_fee_rate: float = 0.0002,
    taker_fee_rate: float = 0.0005
) -> dict:
    """Calculate P&L including fees."""
    position_value = size * entry_price

    # Entry fee (assuming taker)
    entry_fee = position_value * taker_fee_rate

    # Exit fee (assuming taker)
    exit_value = size * exit_price
    exit_fee = exit_value * taker_fee_rate

    # Price P&L
    price_pnl = (exit_price - entry_price) * size

    # Total P&L
    total_pnl = price_pnl - entry_fee - exit_fee

    # ROI on margin
    margin = position_value / leverage
    roi = (total_pnl / margin) * 100

    return {
        "price_pnl": price_pnl,
        "entry_fee": entry_fee,
        "exit_fee": exit_fee,
        "total_fees": entry_fee + exit_fee,
        "net_pnl": total_pnl,
        "roi": roi
    }

# Example
result = calculate_pnl_with_fees(
    entry_price=50000,
    exit_price=51000,
    size=1.0,
    leverage=10
)
# price_pnl: $1000
# entry_fee: $25
# exit_fee: $25.50
# net_pnl: $949.50
```

## Price Types

### Mark Price

- Fair value price used for liquidation calculations
- Derived from oracle price and impact price
- More stable than last trade price
- Prevents manipulation

### Index Price

- Reference price from external exchanges
- Oracle-provided
- Used in mark price calculation

### Last Price

- Most recent trade execution price
- Used for display
- Can be volatile in thin markets

### Best Bid/Ask

- Top of orderbook
- Used for market orders
- Shows current liquidity

## Market Status

### Status Values

1. **active**: Normal trading
2. **post_only**: Only post-only orders accepted
3. **reduce_only**: Only position-reducing orders accepted
4. **halted**: Trading suspended
5. **closed**: Market permanently closed

### Handling Market Status

```python
def can_place_order(market_status: str, order_type: str, reduce_only: bool) -> bool:
    """Check if order can be placed given market status."""
    if market_status == "active":
        return True
    elif market_status == "post_only":
        return order_type == "limit" and post_only
    elif market_status == "reduce_only":
        return reduce_only
    else:  # halted or closed
        return False
```

## Contract Specifications

### Settlement

- **Type**: Cash-settled
- **Settlement Currency**: USD (stablecoin)
- **Settlement**: Continuous (no expiry)

### Funding

- **Frequency**: Every hour (24 times per day)
- **Rate**: See `funding_rates.md` for calculation
- **Payment**: Automatically applied to positions

### Liquidation

- **Method**: Multi-tiered liquidation
- **Price**: Based on mark price
- **Fee**: Varies by market

## Market Data Update Frequency

### REST API

- Market info: Updated every 1 second
- Prices: Updated real-time
- Orderbook: Snapshot on request
- Trades: Real-time

### WebSocket

- Prices: Real-time (sub-second)
- Orderbook: Real-time updates
- Trades: Real-time
- Candles: Updated as trades occur

## Position Limits

### Per Market

- Maximum position size varies by market
- Larger markets (BTC, ETH) have higher limits
- Limits prevent market manipulation

### Per Account

- Total exposure across all markets
- Based on account equity
- Dynamically adjusted

## Order Book Depth

### Available Levels

- Default: 20 levels
- Maximum: 100 levels (REST)
- WebSocket: Configurable depth

### Orderbook Data

```json
{
    "symbol": "BTC-PERP",
    "bids": [
        [50000.0, 1.5],  // [price, size]
        [49999.5, 2.0],
        [49999.0, 1.2]
    ],
    "asks": [
        [50000.5, 1.2],
        [50001.0, 3.0],
        [50001.5, 0.8]
    ]
}
```

## Impact Notional

Used in funding rate calculation:

- **BTC**: $20,000
- **Other Assets**: $6,000

See `funding_rates.md` for details.

## Best Practices

### 1. Cache Market Specs

```python
class MarketSpecCache:
    def __init__(self, client):
        self.client = client
        self.cache = {}
        self.last_update = 0
        self.ttl = 3600  # 1 hour

    def get_specs(self, symbol: str) -> dict:
        """Get cached or fresh market specs."""
        now = time.time()

        if symbol not in self.cache or now - self.last_update > self.ttl:
            markets = self.client.get_markets()
            self.cache = {m["symbol"]: m for m in markets["markets"]}
            self.last_update = now

        return self.cache.get(symbol)
```

### 2. Validate Before Submitting

Always validate orders locally before API submission to avoid errors and rate limit waste.

### 3. Monitor Market Status

```python
def wait_for_market_active(client, symbol: str, timeout: int = 300):
    """Wait for market to become active."""
    start = time.time()

    while time.time() - start < timeout:
        market = client.get_market_info(symbol)
        if market["status"] == "active":
            return True
        time.sleep(5)

    return False
```

### 4. Handle Rounding Correctly

```python
import math

def round_down_to_lot(size: float, lot_size: float) -> float:
    """Round down to nearest lot (conservative)."""
    return math.floor(size / lot_size) * lot_size

def round_up_to_tick(price: float, tick_size: float, direction: str) -> float:
    """Round price in favorable direction."""
    if direction == "buy":
        # Round down for buy limit orders
        return math.floor(price / tick_size) * tick_size
    else:
        # Round up for sell limit orders
        return math.ceil(price / tick_size) * tick_size
```

### 5. Check Minimum Order Value

Some markets may have minimum order value in USD:

```python
def validate_min_order_value(size: float, price: float, min_value: float = 10.0) -> bool:
    """Validate order meets minimum USD value."""
    order_value = size * price
    return order_value >= min_value
```

## Additional Resources

- See `api_reference_rest.md` for API endpoints
- See `order_types.md` for order specifications
- See `funding_rates.md` for funding details
- See `error_codes.md` for validation errors
