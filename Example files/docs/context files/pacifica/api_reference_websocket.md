# Pacifica WebSocket API Reference

## Overview

WebSocket URL: `wss://ws.pacifica.fi`

WebSocket provides real-time market data and account updates with lower latency than REST API polling.

## Connection

### Connecting

```python
import websockets
import json

async def connect():
    uri = "wss://ws.pacifica.fi"
    async with websockets.connect(uri) as ws:
        # Connected
        await subscribe(ws)
```

### Authentication

For private channels, send authentication message after connection:

```json
{
    "type": "authenticate",
    "api_key": "your_api_key",
    "signature": "hmac_signature",
    "timestamp": 1699999999
}
```

**Response**:
```json
{
    "type": "authenticated",
    "success": true,
    "account_id": "abc123"
}
```

## Message Format

### Subscribe Message

```json
{
    "type": "subscribe",
    "channel": "channel_name",
    "market": "BTC-PERP"  // optional, for market-specific channels
}
```

### Unsubscribe Message

```json
{
    "type": "unsubscribe",
    "channel": "channel_name",
    "market": "BTC-PERP"
}
```

### Subscription Confirmation

```json
{
    "type": "subscribed",
    "channel": "channel_name",
    "market": "BTC-PERP"
}
```

## Public Channels

### Prices Channel

Real-time price updates for all markets or specific market.

**Subscribe**:
```json
{
    "type": "subscribe",
    "channel": "prices",
    "market": "BTC-PERP"  // optional, omit for all markets
}
```

**Message Format**:
```json
{
    "type": "price_update",
    "channel": "prices",
    "data": {
        "market": "BTC-PERP",
        "mark_price": 50000.5,
        "index_price": 50001.0,
        "last_price": 50000.0,
        "best_bid": 49999.5,
        "best_ask": 50000.5,
        "funding_rate": 0.0001,
        "next_funding_time": "2025-11-10T15:00:00Z",
        "timestamp": 1699999999
    }
}
```

### Orderbook Channel

Real-time orderbook updates.

**Subscribe**:
```json
{
    "type": "subscribe",
    "channel": "orderbook",
    "market": "BTC-PERP",
    "depth": 20  // optional, default 20
}
```

**Snapshot Message** (first message after subscription):
```json
{
    "type": "orderbook_snapshot",
    "channel": "orderbook",
    "data": {
        "market": "BTC-PERP",
        "timestamp": 1699999999,
        "bids": [
            [50000.0, 1.5],  // [price, size]
            [49999.5, 2.0]
        ],
        "asks": [
            [50000.5, 1.2],
            [50001.0, 3.0]
        ]
    }
}
```

**Update Message** (incremental updates):
```json
{
    "type": "orderbook_update",
    "channel": "orderbook",
    "data": {
        "market": "BTC-PERP",
        "timestamp": 1699999999,
        "bids": [
            [50000.0, 2.5]  // updated level
        ],
        "asks": [
            [50000.5, 0]  // size 0 = removed
        ]
    }
}
```

### Trades Channel

Real-time trade executions.

**Subscribe**:
```json
{
    "type": "subscribe",
    "channel": "trades",
    "market": "BTC-PERP"
}
```

**Message Format**:
```json
{
    "type": "trade",
    "channel": "trades",
    "data": {
        "market": "BTC-PERP",
        "trade_id": "12345",
        "price": 50000.0,
        "size": 0.5,
        "side": "buy",  // aggressor side
        "timestamp": 1699999999
    }
}
```

### Candles Channel

Real-time OHLCV candle updates.

**Subscribe**:
```json
{
    "type": "subscribe",
    "channel": "candles",
    "market": "BTC-PERP",
    "interval": "1m"  // 1m, 5m, 15m, 1h, 4h, 1d
}
```

**Message Format**:
```json
{
    "type": "candle_update",
    "channel": "candles",
    "data": {
        "market": "BTC-PERP",
        "interval": "1m",
        "timestamp": 1699999999,
        "open": 50000.0,
        "high": 50500.0,
        "low": 49800.0,
        "close": 50200.0,
        "volume": 1500.5,
        "closed": false  // true when candle is complete
    }
}
```

## Private Channels (Authentication Required)

### Account Margin Channel

Real-time account margin and balance updates.

**Subscribe**:
```json
{
    "type": "subscribe",
    "channel": "account_margin"
}
```

**Message Format**:
```json
{
    "type": "account_margin_update",
    "channel": "account_margin",
    "data": {
        "balance": 10000.0,
        "available_balance": 8000.0,
        "used_margin": 2000.0,
        "unrealized_pnl": 500.0,
        "total_equity": 10500.0,
        "margin_ratio": 0.2,
        "timestamp": 1699999999
    }
}
```

### Account Leverage Channel

Leverage setting changes.

**Subscribe**:
```json
{
    "type": "subscribe",
    "channel": "account_leverage"
}
```

**Message Format**:
```json
{
    "type": "account_leverage_update",
    "channel": "account_leverage",
    "data": {
        "market": "BTC-PERP",
        "leverage": 20,
        "margin_mode": "isolated",
        "timestamp": 1699999999
    }
}
```

### Account Info Channel

General account information updates.

**Subscribe**:
```json
{
    "type": "subscribe",
    "channel": "account_info"
}
```

**Message Format**:
```json
{
    "type": "account_info_update",
    "channel": "account_info",
    "data": {
        "account_id": "abc123",
        "status": "active",
        "api_trading_enabled": true,
        "timestamp": 1699999999
    }
}
```

### Account Positions Channel

Real-time position updates.

**Subscribe**:
```json
{
    "type": "subscribe",
    "channel": "account_positions",
    "market": "BTC-PERP"  // optional, omit for all markets
}
```

**Message Format**:
```json
{
    "type": "position_update",
    "channel": "account_positions",
    "data": {
        "market": "BTC-PERP",
        "side": "long",
        "size": 1.5,
        "entry_price": 50000.0,
        "mark_price": 51000.0,
        "liquidation_price": 45000.0,
        "unrealized_pnl": 1500.0,
        "realized_pnl": 0.0,
        "margin": 7500.0,
        "leverage": 10,
        "margin_mode": "isolated",
        "timestamp": 1699999999
    }
}
```

### Account Orders Channel

Real-time order status updates.

**Subscribe**:
```json
{
    "type": "subscribe",
    "channel": "account_orders",
    "market": "BTC-PERP"  // optional
}
```

**Message Format**:
```json
{
    "type": "order_update",
    "channel": "account_orders",
    "data": {
        "order_id": "12345",
        "market": "BTC-PERP",
        "side": "buy",
        "type": "limit",
        "size": 1.0,
        "price": 49500.0,
        "filled_size": 0.5,
        "avg_fill_price": 49500.0,
        "status": "partially_filled",  // open, partially_filled, filled, cancelled
        "timestamp": 1699999999
    }
}
```

### Account Order Updates Channel

Detailed order lifecycle events.

**Subscribe**:
```json
{
    "type": "subscribe",
    "channel": "account_order_updates"
}
```

**Message Format**:
```json
{
    "type": "order_event",
    "channel": "account_order_updates",
    "data": {
        "event": "order_placed",  // order_placed, order_filled, order_cancelled, order_rejected
        "order_id": "12345",
        "market": "BTC-PERP",
        "side": "buy",
        "type": "limit",
        "size": 1.0,
        "price": 49500.0,
        "reason": "",  // filled for rejections/cancellations
        "timestamp": 1699999999
    }
}
```

### Account Trades Channel

Real-time trade executions for account.

**Subscribe**:
```json
{
    "type": "subscribe",
    "channel": "account_trades",
    "market": "BTC-PERP"  // optional
}
```

**Message Format**:
```json
{
    "type": "account_trade",
    "channel": "account_trades",
    "data": {
        "trade_id": "12345",
        "order_id": "67890",
        "market": "BTC-PERP",
        "side": "buy",
        "price": 50000.0,
        "size": 1.0,
        "fee": 5.0,
        "fee_currency": "USD",
        "is_maker": true,
        "timestamp": 1699999999
    }
}
```

## Trading Operations via WebSocket

### Create Market Order

```json
{
    "type": "order",
    "operation": "create_market_order",
    "data": {
        "market": "BTC-PERP",
        "side": "buy",
        "size": 1.0,
        "reduce_only": false
    },
    "request_id": "req_12345"  // optional, for matching responses
}
```

**Response**:
```json
{
    "type": "order_response",
    "request_id": "req_12345",
    "success": true,
    "data": {
        "order_id": "12345",
        "market": "BTC-PERP",
        "status": "filled",
        "filled_size": 1.0,
        "avg_fill_price": 50000.0
    }
}
```

### Create Limit Order

```json
{
    "type": "order",
    "operation": "create_limit_order",
    "data": {
        "market": "BTC-PERP",
        "side": "buy",
        "size": 1.0,
        "price": 49500.0,
        "post_only": false,
        "reduce_only": false,
        "time_in_force": "GTC"
    },
    "request_id": "req_12346"
}
```

### Cancel Order

```json
{
    "type": "order",
    "operation": "cancel_order",
    "data": {
        "order_id": "12345"
    },
    "request_id": "req_12347"
}
```

**Response**:
```json
{
    "type": "order_response",
    "request_id": "req_12347",
    "success": true,
    "data": {
        "order_id": "12345",
        "status": "cancelled"
    }
}
```

### Cancel All Orders

```json
{
    "type": "order",
    "operation": "cancel_all_orders",
    "data": {
        "market": "BTC-PERP"  // optional
    },
    "request_id": "req_12348"
}
```

## Heartbeat and Ping/Pong

### Ping

Send periodically to keep connection alive:

```json
{
    "type": "ping"
}
```

**Response**:
```json
{
    "type": "pong",
    "timestamp": 1699999999
}
```

**Recommendation**: Send ping every 30 seconds to prevent connection timeout.

## Error Messages

```json
{
    "type": "error",
    "error": {
        "code": "ERROR_CODE",
        "message": "Human readable error message"
    },
    "request_id": "req_12345"  // if related to specific request
}
```

## Best Practices

### Connection Management

1. **Reconnection Logic**: Implement exponential backoff
2. **Heartbeat**: Send ping every 30 seconds
3. **Subscription Recovery**: Resubscribe after reconnection
4. **Multiple Connections**: Use separate connections for public/private data

### Example Reconnection

```python
import asyncio
import websockets

async def connect_with_retry():
    retry_delay = 1
    max_delay = 60

    while True:
        try:
            async with websockets.connect("wss://ws.pacifica.fi") as ws:
                await handle_connection(ws)
        except Exception as e:
            print(f"Connection error: {e}")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_delay)
```

### Message Handling

1. **Parse Type Field**: Route based on message type
2. **Handle Snapshots**: Clear local state on orderbook snapshots
3. **Track Request IDs**: Match responses to requests
4. **Buffer Messages**: Handle bursts without dropping data

### Performance Optimization

1. **Selective Subscriptions**: Only subscribe to needed channels
2. **Market Filtering**: Use market parameter to limit data
3. **Batch Processing**: Process multiple messages together
4. **Async Processing**: Don't block on message handling

## Rate Limiting

WebSocket has different rate limits than REST:

- **Subscribe/Unsubscribe**: 10 requests/second
- **Order Operations**: 20 orders/second
- **Message Receive**: Unlimited (server-side rate)

See `rate_limits.md` for detailed information.

## Example Implementation

### Python WebSocket Client

```python
import asyncio
import json
import websockets
from typing import Callable, Dict

class PacificaWebSocket:
    def __init__(self, url: str = "wss://ws.pacifica.fi"):
        self.url = url
        self.ws = None
        self.handlers: Dict[str, Callable] = {}

    async def connect(self):
        """Connect to WebSocket."""
        self.ws = await websockets.connect(self.url)

    async def authenticate(self, api_key: str, signature: str, timestamp: int):
        """Authenticate for private channels."""
        await self.send({
            "type": "authenticate",
            "api_key": api_key,
            "signature": signature,
            "timestamp": timestamp
        })

    async def subscribe(self, channel: str, market: str = None):
        """Subscribe to a channel."""
        msg = {"type": "subscribe", "channel": channel}
        if market:
            msg["market"] = market
        await self.send(msg)

    async def send(self, message: dict):
        """Send message to server."""
        await self.ws.send(json.dumps(message))

    async def listen(self):
        """Listen for messages."""
        async for message in self.ws:
            data = json.loads(message)
            msg_type = data.get("type")

            # Call registered handler
            if msg_type in self.handlers:
                await self.handlers[msg_type](data)

    def on(self, message_type: str, handler: Callable):
        """Register message handler."""
        self.handlers[message_type] = handler

    async def ping_loop(self):
        """Send periodic pings."""
        while True:
            await asyncio.sleep(30)
            await self.send({"type": "ping"})

# Usage
async def main():
    ws = PacificaWebSocket()
    await ws.connect()

    # Register handlers
    ws.on("price_update", lambda data: print(f"Price: {data}"))
    ws.on("position_update", lambda data: print(f"Position: {data}"))

    # Subscribe
    await ws.subscribe("prices", "BTC-PERP")
    await ws.subscribe("account_positions")

    # Start listening and pinging
    await asyncio.gather(
        ws.listen(),
        ws.ping_loop()
    )

asyncio.run(main())
```

## Additional Resources

- See `api_reference_rest.md` for REST API details
- See `authentication.md` for auth implementation
- See `rate_limits.md` for rate limit details
- See `error_codes.md` for error handling
