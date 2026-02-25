# Exchange Integration

<!--
RAG Metadata:
- Category: Integration
- Tags: pacifica, rest-api, websocket, exchange, solana, perpetuals
- Related: 05-data-management, 01-core-trading-logic
-->

## Overview

Exchange integration is handled through two clients:
1. **PacificaClient** - REST API for order execution
2. **PacificaWebSocketClient** - Real-time data streaming

Both connect to **Pacifica.fi**, a Solana-based perpetual futures exchange.

---

## PacificaClient (REST)

**Location**: `trading_bot_v2/pacifica_client.py`

### Purpose
REST API client for Pacifica exchange operations.

### Features
- Order placement and management
- Account and position queries
- Market data retrieval
- Funding rate information
- Rate limiting and retry logic

### Configuration

```python
from .config import config

client = PacificaClient(
    agent_wallet_private_key=config.agent_wallet_private_key,
    account_public_key=config.account_public_key,
    testnet=config.testnet,  # True for testnet, False for mainnet
)
```

### Environments

| Environment | Base URL | Config |
|-------------|----------|--------|
| Testnet | `https://testnet-api.pacifica.fi` | `testnet=True` |
| Mainnet | `https://api.pacifica.fi` | `testnet=False` |

### Key Methods

#### Account Operations

```python
async def get_account_info(self) -> Dict[str, Any]:
    """
    Get account information including balance and margin.
    
    Returns:
        Dict with account details:
        {
            'balance': float,
            'available_margin': float,
            'used_margin': float,
            'unrealized_pnl': float,
            'margin_ratio': float,
        }
    """

async def get_positions(self) -> List[Dict[str, Any]]:
    """
    Get all open positions.
    
    Returns:
        List of position dicts:
        [{
            'symbol': str,
            'side': 'long' | 'short',
            'quantity': float,
            'entry_price': float,
            'unrealized_pnl': float,
            'liquidation_price': float,
            'margin_used': float,
            'leverage': int,
        }]
    """
```

#### Order Operations

```python
async def place_order(
    self,
    symbol: str,
    side: str,  # 'buy' | 'sell'
    quantity: float,
    order_type: str = 'market',  # 'market' | 'limit'
    price: Optional[float] = None,  # Required for limit orders
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    reduce_only: bool = False,
) -> Dict[str, Any]:
    """
    Place a new order.
    
    Args:
        symbol: Trading pair (e.g., 'SOL-USD')
        side: 'buy' or 'sell'
        quantity: Position size
        order_type: 'market' or 'limit'
        price: Limit price (required for limit orders)
        stop_loss: Stop loss price
        take_profit: Take profit price
        reduce_only: If True, only reduces existing position
        
    Returns:
        Order response with order_id and status
    """

async def cancel_order(self, order_id: str) -> bool:
    """Cancel an open order by ID."""

async def get_order(self, order_id: str) -> Dict[str, Any]:
    """Get order status and details."""

async def get_open_orders(self, symbol: Optional[str] = None) -> List[Dict]:
    """Get all open orders, optionally filtered by symbol."""
```

#### Market Data

```python
async def get_candles(
    self,
    symbol: str,
    interval: str = '1h',  # '1m', '5m', '15m', '1h', '4h', '1d'
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """
    Get historical candlestick data.
    
    Args:
        symbol: Trading pair
        interval: Timeframe
        limit: Number of candles (max 1000)
        
    Returns:
        List of candle dicts:
        [{
            'timestamp': int,
            'open': float,
            'high': float,
            'low': float,
            'close': float,
            'volume': float,
        }]
    """

async def get_ticker(self, symbol: str) -> Dict[str, Any]:
    """Get current price and 24h stats for a symbol."""

async def get_funding_rate(self, symbol: str) -> Dict[str, Any]:
    """
    Get current funding rate for a perpetual.
    
    Returns:
        {
            'funding_rate': float,  # Hourly rate
            'next_funding_time': int,
            'predicted_rate': float,
        }
    """

async def get_orderbook(self, symbol: str, depth: int = 10) -> Dict[str, Any]:
    """
    Get order book depth.
    
    Returns:
        {
            'bids': [[price, quantity], ...],
            'asks': [[price, quantity], ...],
        }
    """
```

### Rate Limiting

```python
# Rate limiting configuration
RATE_LIMITS = {
    'orders': {'limit': 10, 'window': 1},      # 10 orders per second
    'queries': {'limit': 50, 'window': 1},     # 50 queries per second
    'market_data': {'limit': 100, 'window': 1}, # 100 market data calls per second
}

# Retry configuration
MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds
MAX_DELAY = 30.0  # seconds
```

---

## PacificaWebSocketClient

**Location**: `trading_bot_v2/pacifica_ws_client.py`

### Purpose
Real-time data streaming for prices, positions, and order updates.

### Features
- Singleton pattern for single connection
- Automatic reconnection with exponential backoff
- Subscription management
- Heartbeat/ping-pong handling

### Singleton Access

```python
from .pacifica_ws_client import get_ws_client

# Get singleton instance
ws_client = get_ws_client()

# Start connection (usually done by API server)
await ws_client.connect()

# Stop connection
await ws_client.disconnect()
```

### WebSocket URLs

| Environment | URL |
|-------------|-----|
| Testnet | `wss://testnet-ws.pacifica.fi` |
| Mainnet | `wss://ws.pacifica.fi` |

### Subscription Channels

```python
# Price updates
await ws_client.subscribe_ticker(symbol='SOL-USD')

# Order book updates
await ws_client.subscribe_orderbook(symbol='SOL-USD', depth=10)

# Position updates
await ws_client.subscribe_positions()

# Order updates
await ws_client.subscribe_orders()

# Funding rate updates
await ws_client.subscribe_funding(symbol='SOL-USD')
```

### Callback Registration

```python
# Register callbacks for specific channels
def on_price_update(data: Dict):
    print(f"Price update: {data['symbol']} = {data['price']}")

ws_client.on_ticker('SOL-USD', on_price_update)

# Order book callback
def on_orderbook_update(data: Dict):
    bids = data['bids']
    asks = data['asks']
    # Process order book...

ws_client.on_orderbook('SOL-USD', on_orderbook_update)
```

### Message Types

| Type | Channel | Description |
|------|---------|-------------|
| `ticker` | Price | Real-time price updates |
| `orderbook` | Depth | Order book changes |
| `position` | Account | Position updates |
| `order` | Account | Order status changes |
| `funding` | Market | Funding rate updates |

### Reconnection Logic

```python
# Automatic reconnection with exponential backoff
RECONNECT_CONFIG = {
    'initial_delay': 1.0,
    'max_delay': 60.0,
    'multiplier': 2.0,
    'max_retries': 10,
}
```

---

## Error Handling

### Common Error Codes

| Code | Description | Action |
|------|-------------|--------|
| 400 | Bad Request | Check parameters |
| 401 | Unauthorized | Check API keys |
| 403 | Forbidden | Check permissions |
| 429 | Rate Limited | Implement backoff |
| 500 | Server Error | Retry with backoff |
| 503 | Service Unavailable | Wait and retry |

### Error Handling Pattern

```python
from .pacifica_client import PacificaAPIError

try:
    order = await client.place_order(
        symbol='SOL-USD',
        side='buy',
        quantity=1.0,
    )
except PacificaAPIError as e:
    if e.code == 429:
        # Rate limited - wait and retry
        await asyncio.sleep(e.retry_after or 1.0)
    elif e.code == 400:
        logger.error(f"Invalid order parameters: {e.message}")
    else:
        raise
```

---

## WebSocket Authority Pattern

As of Jan 2026, the system enforces **WebSocket-only price feeds** with REST fallback disabled:

```python
# WebSocket is the authoritative source for price data
if ws_client and ws_client.is_connected():
    price = await ws_client.get_latest_price(symbol)
else:
    # No REST fallback - raise error or wait for reconnect
    raise ConnectionError("WebSocket not connected")
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                       Exchange Integration                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │                      TradingBot                                 │ │
│  │                                                                │ │
│  │   ┌─────────────────┐      ┌──────────────────────────┐      │ │
│  │   │ PacificaClient  │      │ PacificaWebSocketClient  │      │ │
│  │   │    (REST)       │      │      (Real-time)         │      │ │
│  │   │                 │      │                          │      │ │
│  │   │ - Orders        │      │ - Price streaming        │      │ │
│  │   │ - Account       │      │ - Position updates       │      │ │
│  │   │ - History       │      │ - Order updates          │      │ │
│  │   │ - Funding       │      │ - Orderbook data         │      │ │
│  │   └────────┬────────┘      └────────────┬─────────────┘      │ │
│  │            │                            │                     │ │
│  └────────────┼────────────────────────────┼─────────────────────┘ │
│               │                            │                       │
│               ▼                            ▼                       │
│  ┌────────────────────────────────────────────────────────────────┐│
│  │                      Pacifica.fi API                            ││
│  │  ┌──────────────────┐          ┌───────────────────┐          ││
│  │  │   REST API       │          │   WebSocket API   │          ││
│  │  │   (HTTP)         │          │   (WSS)           │          ││
│  │  └──────────────────┘          └───────────────────┘          ││
│  └────────────────────────────────────────────────────────────────┘│
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Key Files

| File | Lines | Purpose |
|------|-------|---------|
| `pacifica_client.py` | ~1000 | REST API client |
| `pacifica_ws_client.py` | ~500 | WebSocket client |

---

## Related Reports

- [05-data-management.md](./05-data-management.md) - Database integration
- [01-core-trading-logic.md](./01-core-trading-logic.md) - Bot usage
- [06-hub-system-architecture.md](./06-hub-system-architecture.md) - Connection management
