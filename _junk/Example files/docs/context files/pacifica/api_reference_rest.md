# Pacifica REST API Reference

## Overview

Base URL: `https://api.pacifica.fi` (mainnet)

All endpoints return JSON responses. Authentication required for account-specific endpoints.

## Authentication

See `authentication.md` for detailed authentication methods including:
- API Agent Keys
- Hardware Wallet signing
- HMAC authentication

## Response Format

### Success Response
```json
{
    "success": true,
    "data": { ... }
}
```

### Error Response
```json
{
    "success": false,
    "error": {
        "code": "ERROR_CODE",
        "message": "Human readable error message"
    }
}
```

## Markets Endpoints

### Get Market Info

Get information about available markets.

**Endpoint**: `GET /info`

**Authentication**: Not required

**Parameters**: None

**Response**:
```json
{
    "success": true,
    "data": [
        {
            "symbol": "BTC",
            "base_currency": "BTC",
            "quote_currency": "USD",
            "min_order_size": 0.001,
            "max_order_size": 100,
            "tick_size": 0.5,
            "lot_size": 0.001,
            "max_leverage": 50,
            "min_leverage": 5,
            "status": "active"
        }
    ]
}
```

### Get Prices

Get current prices for markets.

**Endpoint**: `GET /prices`

**Authentication**: Not required

**Query Parameters**:
- `symbol` (optional): Specific market symbol (e.g., "BTC" - without -PERP suffix)

**Response**:
```json
{
    "success": true,
    "data": {
        "prices": [
            {
                "symbol": "BTC",
                "mark_price": 50000.5,
                "index_price": 50001.0,
                "last_price": 50000.0,
                "best_bid": 49999.5,
                "best_ask": 50000.5,
                "funding_rate": 0.0001,
                "next_funding_time": "2025-11-10T15:00:00Z"
            }
        ]
    }
}
```

### Get Kline (Candle) Data

Get OHLCV candlestick data.

**Endpoint**: `GET /kline`

**Authentication**: Not required

**Query Parameters**:
- `symbol` (required): Market symbol (e.g., "BTC" - without -PERP suffix)
- `interval` (required): Time interval (1m, 3m, 5m, 15m, 30m, 1h, 2h, 4h, 8h, 12h, 1d)
- `start_time` (required): Start timestamp in milliseconds
- `end_time` (optional): End timestamp in milliseconds (defaults to current time)

**Example Request**:
```
/api/v1/kline?symbol=BTC&interval=1h&start_time=1742243160000
```

**Response**:
```json
{
    "success": true,
    "data": [
        {
            "timestamp": 1699999999,
            "open": 50000.0,
            "high": 50500.0,
            "low": 49800.0,
            "close": 50200.0,
            "volume": 1500.5
        }
    ]
}
```

### Get Orderbook

Get current orderbook depth.

**Endpoint**: `GET /book`

**Authentication**: Not required

**Query Parameters**:
- `symbol` (required): Market symbol (e.g., "BTC" - without -PERP suffix)

**Response**:
```json
{
    "success": true,
    "data": {
        "symbol": "BTC",
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

### Get Recent Trades

Get recent trades for a market.

**Endpoint**: `GET /trades`

**Authentication**: Not required

**Query Parameters**:
- `symbol` (required): Market symbol (e.g., "BTC" - without -PERP suffix)
- `limit` (optional): Number of trades (default 50, max 500)

**Response**:
```json
{
    "success": true,
    "data": [
        {
            "id": "12345",
            "timestamp": 1699999999,
            "price": 50000.0,
            "size": 0.5,
            "side": "buy"
        }
    ]
}
```

### Get Historical Funding

Get historical funding rate data.

**Endpoint**: `GET /funding`

**Authentication**: Not required

**Query Parameters**:
- `symbol` (required): Market symbol (e.g., "BTC" - without -PERP suffix)
- `start_time` (optional): Start timestamp
- `end_time` (optional): End timestamp
- `limit` (optional): Number of records (default 100, max 1000)

**Response**:
```json
{
    "success": true,
    "data": [
        {
            "timestamp": 1699999999,
            "funding_rate": 0.0001,
            "mark_price": 50000.0,
            "index_price": 50001.0
        }
    ]
}
```

## Account Endpoints

### Get Account Info

Get account information including balance and margin.

**Endpoint**: `GET /account`

**Authentication**: Required

**Response**:
```json
{
    "success": true,
    "data": {
        "account_id": "abc123",
        "balance": 10000.0,
        "available_balance": 8000.0,
        "used_margin": 2000.0,
        "unrealized_pnl": 500.0,
        "total_equity": 10500.0,
        "margin_ratio": 0.2,
        "leverage": 10,
        "margin_mode": "cross"
    }
}
```

### Get Account Settings

Get account configuration settings.

**Endpoint**: `GET /account/settings`

**Authentication**: Required

**Response**:
```json
{
    "success": true,
    "data": {
        "default_leverage": 10,
        "default_margin_mode": "cross",
        "notifications_enabled": true,
        "api_trading_enabled": true
    }
}
```

### Update Leverage

Update leverage for a specific market.

**Endpoint**: `POST /account/leverage`

**Authentication**: Required

**Request Body**:
```json
{
    "market": "BTC-PERP",
    "leverage": 20
}
```

**Response**:
```json
{
    "success": true,
    "data": {
        "market": "BTC-PERP",
        "leverage": 20,
        "updated_at": "2025-11-10T14:30:00Z"
    }
}
```

### Update Margin Mode

Switch between cross and isolated margin.

**Endpoint**: `POST /account/margin-mode`

**Authentication**: Required

**Request Body**:
```json
{
    "market": "BTC-PERP",
    "margin_mode": "isolated"  // or "cross"
}
```

**Response**:
```json
{
    "success": true,
    "data": {
        "market": "BTC-PERP",
        "margin_mode": "isolated",
        "updated_at": "2025-11-10T14:30:00Z"
    }
}
```

### Get Positions

Get all open positions.

**Endpoint**: `GET /account/positions`

**Authentication**: Required

**Query Parameters**:
- `market` (optional): Filter by specific market

**Response**:
```json
{
    "success": true,
    "data": {
        "positions": [
            {
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
                "opened_at": "2025-11-10T10:00:00Z"
            }
        ]
    }
}
```

### Get Trade History

Get historical trades for account.

**Endpoint**: `GET /account/trades`

**Authentication**: Required

**Query Parameters**:
- `market` (optional): Filter by market
- `start_time` (optional): Start timestamp
- `end_time` (optional): End timestamp
- `limit` (optional): Number of records (default 50, max 500)

**Response**:
```json
{
    "success": true,
    "data": {
        "trades": [
            {
                "trade_id": "12345",
                "order_id": "67890",
                "market": "BTC-PERP",
                "side": "buy",
                "price": 50000.0,
                "size": 1.0,
                "fee": 5.0,
                "timestamp": "2025-11-10T10:00:00Z"
            }
        ]
    }
}
```

### Get Funding History

Get funding payment history.

**Endpoint**: `GET /account/funding`

**Authentication**: Required

**Query Parameters**:
- `market` (optional): Filter by market
- `start_time` (optional): Start timestamp
- `end_time` (optional): End timestamp
- `limit` (optional): Number of records (default 100, max 1000)

**Response**:
```json
{
    "success": true,
    "data": {
        "funding_payments": [
            {
                "timestamp": "2025-11-10T14:00:00Z",
                "market": "BTC-PERP",
                "position_size": 1.5,
                "funding_rate": 0.0001,
                "payment": -7.5,
                "balance_after": 9992.5
            }
        ]
    }
}
```

### Get Account Equity History

Get historical account equity.

**Endpoint**: `GET /account/equity-history`

**Authentication**: Required

**Query Parameters**:
- `start_time` (optional): Start timestamp
- `end_time` (optional): End timestamp
- `interval` (optional): Time interval (1h, 1d)
- `limit` (optional): Number of records

**Response**:
```json
{
    "success": true,
    "data": {
        "equity_history": [
            {
                "timestamp": "2025-11-10T14:00:00Z",
                "equity": 10500.0,
                "balance": 10000.0,
                "unrealized_pnl": 500.0
            }
        ]
    }
}
```

### Get Account Balance History

Get balance change history.

**Endpoint**: `GET /account/balance-history`

**Authentication**: Required

**Query Parameters**: Same as equity history

**Response**:
```json
{
    "success": true,
    "data": {
        "balance_history": [
            {
                "timestamp": "2025-11-10T14:00:00Z",
                "balance": 10000.0,
                "change": -7.5,
                "reason": "funding_payment",
                "market": "BTC-PERP"
            }
        ]
    }
}
```

### Request Withdrawal

Request withdrawal of funds.

**Endpoint**: `POST /account/withdrawal`

**Authentication**: Required

**Request Body**:
```json
{
    "amount": 1000.0,
    "destination": "wallet_address_here",
    "currency": "USD"
}
```

**Response**:
```json
{
    "success": true,
    "data": {
        "withdrawal_id": "wd_12345",
        "amount": 1000.0,
        "fee": 1.0,
        "net_amount": 999.0,
        "status": "pending",
        "estimated_completion": "2025-11-10T15:00:00Z"
    }
}
```

## Orders Endpoints

### Create Market Order

Execute order at current market price.

**Endpoint**: `POST /orders/market`

**Authentication**: Required

**Request Body**:
```json
{
    "market": "BTC-PERP",
    "side": "buy",  // or "sell"
    "size": 1.0,
    "reduce_only": false  // optional
}
```

**Response**:
```json
{
    "success": true,
    "data": {
        "order_id": "12345",
        "market": "BTC-PERP",
        "side": "buy",
        "size": 1.0,
        "filled_size": 1.0,
        "avg_fill_price": 50000.0,
        "status": "filled",
        "created_at": "2025-11-10T14:30:00Z"
    }
}
```

### Create Limit Order

Execute order at specified price or better.

**Endpoint**: `POST /orders/limit`

**Authentication**: Required

**Request Body**:
```json
{
    "market": "BTC-PERP",
    "side": "buy",
    "size": 1.0,
    "price": 49500.0,
    "post_only": false,  // optional
    "reduce_only": false,  // optional
    "time_in_force": "GTC"  // GTC, IOC, FOK
}
```

**Response**:
```json
{
    "success": true,
    "data": {
        "order_id": "12345",
        "market": "BTC-PERP",
        "side": "buy",
        "size": 1.0,
        "price": 49500.0,
        "filled_size": 0.0,
        "status": "open",
        "created_at": "2025-11-10T14:30:00Z"
    }
}
```

### Create Stop Order

Conditional order triggered at stop price.

**Endpoint**: `POST /orders/stop`

**Authentication**: Required

**Request Body**:
```json
{
    "market": "BTC-PERP",
    "side": "sell",
    "size": 1.0,
    "stop_price": 48000.0,
    "order_type": "market",  // or "limit"
    "limit_price": 47900.0,  // required if order_type is "limit"
    "reduce_only": true
}
```

**Response**:
```json
{
    "success": true,
    "data": {
        "order_id": "12345",
        "market": "BTC-PERP",
        "side": "sell",
        "size": 1.0,
        "stop_price": 48000.0,
        "status": "pending",
        "created_at": "2025-11-10T14:30:00Z"
    }
}
```

### Create Position TP/SL

Add take-profit and stop-loss to existing position.

**Endpoint**: `POST /orders/tp-sl`

**Authentication**: Required

**Request Body**:
```json
{
    "market": "BTC-PERP",
    "take_profit_price": 55000.0,  // optional
    "stop_loss_price": 48000.0  // optional
}
```

**Response**:
```json
{
    "success": true,
    "data": {
        "tp_order_id": "12345",
        "sl_order_id": "12346",
        "market": "BTC-PERP",
        "created_at": "2025-11-10T14:30:00Z"
    }
}
```

### Cancel Order

Cancel a specific order.

**Endpoint**: `DELETE /orders/{order_id}`

**Authentication**: Required

**Response**:
```json
{
    "success": true,
    "data": {
        "order_id": "12345",
        "status": "cancelled",
        "cancelled_at": "2025-11-10T14:30:00Z"
    }
}
```

### Cancel All Orders

Cancel all open orders.

**Endpoint**: `DELETE /orders`

**Authentication**: Required

**Query Parameters**:
- `market` (optional): Cancel only orders for specific market

**Response**:
```json
{
    "success": true,
    "data": {
        "cancelled_orders": ["12345", "12346", "12347"],
        "count": 3
    }
}
```

### Cancel Stop Order

Cancel a stop order.

**Endpoint**: `DELETE /orders/stop/{order_id}`

**Authentication**: Required

**Response**: Same as Cancel Order

### Batch Order

Submit multiple orders at once.

**Endpoint**: `POST /orders/batch`

**Authentication**: Required

**Request Body**:
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
            "side": "sell",
            "size": 10.0,
            "type": "market"
        }
    ]
}
```

**Response**:
```json
{
    "success": true,
    "data": {
        "orders": [
            {
                "order_id": "12345",
                "status": "open"
            },
            {
                "order_id": "12346",
                "status": "filled"
            }
        ]
    }
}
```

### Get Open Orders

Get all open orders.

**Endpoint**: `GET /orders/open`

**Authentication**: Required

**Query Parameters**:
- `market` (optional): Filter by market

**Response**:
```json
{
    "success": true,
    "data": {
        "orders": [
            {
                "order_id": "12345",
                "market": "BTC-PERP",
                "side": "buy",
                "size": 1.0,
                "price": 49500.0,
                "filled_size": 0.0,
                "status": "open",
                "created_at": "2025-11-10T14:30:00Z"
            }
        ]
    }
}
```

### Get Order History

Get historical orders.

**Endpoint**: `GET /orders/history`

**Authentication**: Required

**Query Parameters**:
- `market` (optional): Filter by market
- `start_time` (optional): Start timestamp
- `end_time` (optional): End timestamp
- `limit` (optional): Number of records (default 50, max 500)

**Response**: Same structure as Get Open Orders

### Get Order by ID

Get specific order details.

**Endpoint**: `GET /orders/history/{order_id}`

**Authentication**: Required

**Response**:
```json
{
    "success": true,
    "data": {
        "order_id": "12345",
        "market": "BTC-PERP",
        "side": "buy",
        "size": 1.0,
        "price": 49500.0,
        "filled_size": 1.0,
        "avg_fill_price": 49500.0,
        "status": "filled",
        "created_at": "2025-11-10T14:30:00Z",
        "filled_at": "2025-11-10T14:31:00Z",
        "trades": ["trade_123", "trade_124"]
    }
}
```

## Subaccounts Endpoints

### Create Subaccount

Create a new subaccount.

**Endpoint**: `POST /subaccounts`

**Authentication**: Required

**Request Body**:
```json
{
    "name": "Trading Bot 1",
    "description": "Automated trading strategy"
}
```

**Response**:
```json
{
    "success": true,
    "data": {
        "subaccount_id": "sub_12345",
        "name": "Trading Bot 1",
        "balance": 0.0,
        "created_at": "2025-11-10T14:30:00Z"
    }
}
```

### List Subaccounts

Get all subaccounts.

**Endpoint**: `GET /subaccounts`

**Authentication**: Required

**Response**:
```json
{
    "success": true,
    "data": {
        "subaccounts": [
            {
                "subaccount_id": "sub_12345",
                "name": "Trading Bot 1",
                "balance": 5000.0,
                "equity": 5250.0,
                "created_at": "2025-11-10T14:30:00Z"
            }
        ]
    }
}
```

### Subaccount Fund Transfer

Transfer funds between main account and subaccount.

**Endpoint**: `POST /subaccounts/transfer`

**Authentication**: Required

**Request Body**:
```json
{
    "from": "main",  // or subaccount_id
    "to": "sub_12345",  // or "main"
    "amount": 1000.0
}
```

**Response**:
```json
{
    "success": true,
    "data": {
        "transfer_id": "trans_12345",
        "from": "main",
        "to": "sub_12345",
        "amount": 1000.0,
        "timestamp": "2025-11-10T14:30:00Z"
    }
}
```

## Rate Limiting

See `rate_limits.md` for detailed rate limit information.

**General Limits**:
- Public endpoints: 100 requests/minute
- Private endpoints: 60 requests/minute
- Order placement: 20 requests/second

**Headers**:
```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 45
X-RateLimit-Reset: 1699999999
```

## Error Handling

See `error_codes.md` for complete error code reference.

**Common Error Codes**:
- `INSUFFICIENT_BALANCE`: Not enough funds
- `INVALID_ORDER_SIZE`: Order size outside limits
- `MARKET_CLOSED`: Market not available
- `RATE_LIMIT_EXCEEDED`: Too many requests
- `INVALID_LEVERAGE`: Leverage outside allowed range

## Python SDK

Official Python SDK available: `https://github.com/pacifica-fi/python-sdk`

```python
from pacifica import PacificaClient

client = PacificaClient(api_key="your_key", api_secret="your_secret")

# Get markets
markets = client.get_markets()

# Place order
order = client.create_market_order(
    market="BTC-PERP",
    side="buy",
    size=1.0
)

# Get positions
positions = client.get_positions()
```

## Additional Resources

- See `authentication.md` for auth details
- See `error_codes.md` for error handling
- See `rate_limits.md` for rate limit details
- See `order_types.md` for order type specifications
