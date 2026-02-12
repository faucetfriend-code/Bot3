# Trading Bot v2 - API Documentation

## Overview

The Trading Bot v2 provides a comprehensive REST API and WebSocket interface for programmatic access to all trading operations, market data, and system monitoring.

**Base URL**: `http://localhost:8000`  
**API Version**: 2.0.0  
**Documentation**: OpenAPI/Swagger UI available at `/docs`

---

## Authentication

All API requests to Pacifica endpoints use HMAC-SHA256 signed authentication. Credentials are configured via environment variables:

```bash
# Required environment variables
AGENT_WALLET_PRIVATE_KEY=your_private_key
ACCOUNT_PUBLIC_KEY=your_public_key
TESTNET=true  # or false for mainnet
```

### Request Signing

API requests are automatically signed using HMAC-SHA256:

```python
# Example signature generation
timestamp = int(time.time() * 1000)
message = f"{timestamp}{method.upper()}{endpoint}{body}"
signature = hmac.new(
    private_key.encode(),
    message.encode(),
    hashlib.sha256
).hexdigest()
```

---

## REST API Endpoints

### System Status

#### GET `/api/status`

Returns comprehensive system status information.

**Response**:
```json
{
  "success": true,
  "data": {
    "is_running": true,
    "positions_count": 5,
    "trades_count": 150,
    "total_pnl": 2500.75,
    "account_balance": 10000.00,
    "active_grids": 3,
    "current_regime": "TRENDING_STRONG",
    "circuit_breaker_triggered": false,
    "timestamp": "2026-02-12T10:30:00Z"
  }
}
```

**Fields**:
- `is_running` (boolean): Whether the trading bot is active
- `positions_count` (int): Number of open positions
- `trades_count` (int): Total number of trades executed
- `total_pnl` (float): Total profit/loss across all closed trades
- `account_balance` (float): Current account balance
- `active_grids` (int): Number of active grid trading configurations
- `current_regime` (string): Current market regime classification
- `circuit_breaker_triggered` (boolean): Whether emergency stop is active

---

### Positions Management

#### GET `/api/positions`

Returns all current open positions.

**Response**:
```json
{
  "success": true,
  "data": [
    {
      "id": 1,
      "symbol": "BTC/USD",
      "side": "long",
      "quantity": 0.5,
      "entry_price": 45000.00,
      "current_price": 46000.00,
      "unrealized_pnl": 500.00,
      "liquidation_price": 35000.00,
      "margin_used": 2250.00,
      "leverage": 10,
      "opened_at": "2026-02-12T08:00:00Z",
      "updated_at": "2026-02-12T10:30:00Z"
    }
  ]
}
```

#### GET `/api/positions/{symbol}`

Returns positions for a specific symbol.

**Path Parameters**:
- `symbol` (string): Trading pair (e.g., "BTC/USD")

**Response**: Same format as `/api/positions`

---

### Trade History

#### GET `/api/trades`

Returns trade history with optional filtering.

**Query Parameters**:
- `limit` (int, optional): Maximum trades to return (default: 100, max: 1000)
- `offset` (int, optional): Pagination offset (default: 0)
- `symbol` (string, optional): Filter by symbol
- `status` (string, optional): Filter by status ("open", "closed", "all")
- `start_date` (string, optional): Start date (ISO 8601 format)
- `end_date` (string, optional): End date (ISO 8601 format)

**Response**:
```json
{
  "success": true,
  "data": {
    "trades": [
      {
        "id": 1,
        "symbol": "BTC/USD",
        "side": "buy",
        "quantity": 0.5,
        "entry_price": 45000.00,
        "exit_price": 46000.00,
        "entry_time": "2026-02-12T08:00:00Z",
        "exit_time": "2026-02-12T09:30:00Z",
        "pnl": 500.00,
        "pnl_percent": 2.22,
        "status": "closed",
        "strategy": "mean_reversion",
        "fees": 45.00
      }
    ],
    "total": 150,
    "limit": 100,
    "offset": 0
  }
}
```

---

### Bot Controls

#### POST `/api/bot/start`

Starts the automated trading bot.

**Request Body** (optional):
```json
{
  "strategies": ["mean_reversion", "grid_trading"],
  "symbols": ["BTC", "ETH"],
  "dry_run": false
}
```

**Response**:
```json
{
  "success": true,
  "message": "Bot started successfully",
  "data": {
    "started_at": "2026-02-12T10:30:00Z",
    "strategies_enabled": ["mean_reversion", "grid_trading"],
    "symbols": ["BTC", "ETH"]
  }
}
```

**Error Response**:
```json
{
  "success": false,
  "error": "Bot already running",
  "code": "BOT_ALREADY_RUNNING"
}
```

#### POST `/api/bot/stop`

Stops the automated trading bot.

**Request Body** (optional):
```json
{
  "close_positions": false,
  "emergency": false
}
```

**Response**:
```json
{
  "success": true,
  "message": "Bot stopped successfully",
  "data": {
    "stopped_at": "2026-02-12T10:35:00Z",
    "positions_remaining": 5
  }
}
```

#### GET `/api/bot/health`

Returns detailed health status of all bot components.

**Response**:
```json
{
  "success": true,
  "data": {
    "overall_status": "healthy",
    "components": {
      "database": {
        "status": "healthy",
        "response_time_ms": 5,
        "last_check": "2026-02-12T10:30:00Z"
      },
      "pacifica_api": {
        "status": "healthy",
        "response_time_ms": 150,
        "rate_limit_remaining": 95
      },
      "websocket": {
        "status": "connected",
        "subscriptions": 15,
        "reconnect_count": 0
      },
      "task_queue": {
        "status": "running",
        "pending_tasks": 3,
        "processed_tasks": 1250
      }
    }
  }
}
```

---

### Market Data

#### GET `/api/markets`

Returns available trading markets with current prices.

**Response**:
```json
{
  "success": true,
  "data": [
    {
      "symbol": "BTC/USD",
      "base_asset": "BTC",
      "quote_asset": "USD",
      "price": 46000.00,
      "change_24h": 2.5,
      "volume_24h": 1500000000,
      "high_24h": 47000.00,
      "low_24h": 44500.00,
      "bid": 45990.00,
      "ask": 46010.00,
      "spread": 0.04
    }
  ]
}
```

#### GET `/api/markets/{symbol}/ticker`

Returns real-time ticker data for a specific symbol.

**Path Parameters**:
- `symbol` (string): Trading pair (e.g., "BTC/USD")

**Response**:
```json
{
  "success": true,
  "data": {
    "symbol": "BTC/USD",
    "price": 46000.00,
    "bid": 45990.00,
    "ask": 46010.00,
    "volume_24h": 1500000000,
    "change_24h": 2.5,
    "change_percent_24h": 5.7,
    "timestamp": "2026-02-12T10:30:00Z"
  }
}
```

#### GET `/api/markets/{symbol}/orderbook`

Returns orderbook data for a specific symbol.

**Path Parameters**:
- `symbol` (string): Trading pair (e.g., "BTC/USD")

**Query Parameters**:
- `depth` (int, optional): Orderbook depth (default: 10, max: 100)

**Response**:
```json
{
  "success": true,
  "data": {
    "symbol": "BTC/USD",
    "bids": [
      {"price": 45990.00, "quantity": 1.5},
      {"price": 45980.00, "quantity": 2.0}
    ],
    "asks": [
      {"price": 46010.00, "quantity": 1.2},
      {"price": 46020.00, "quantity": 3.0}
    ],
    "spread": 20.00,
    "spread_percent": 0.04,
    "timestamp": "2026-02-12T10:30:00Z"
  }
}
```

---

### Strategy Management

#### GET `/api/strategies`

Returns all available trading strategies and their status.

**Response**:
```json
{
  "success": true,
  "data": [
    {
      "name": "mean_reversion",
      "display_name": "Mean Reversion",
      "enabled": true,
      "description": "RSI-based mean reversion strategy",
      "parameters": {
        "rsi_period": 14,
        "oversold": 35,
        "overbought": 65,
        "confidence_threshold": 0.45
      },
      "performance_24h": {
        "signals_generated": 15,
        "trades_executed": 8,
        "win_rate": 62.5,
        "pnl": 125.50
      }
    },
    {
      "name": "grid_trading",
      "display_name": "Grid Trading",
      "enabled": true,
      "description": "Automated grid trading with dynamic levels",
      "parameters": {
        "grid_levels": 8,
        "atr_multiplier": 0.4,
        "max_grids_per_symbol": 3
      },
      "active_grids": 3
    }
  ]
}
```

#### POST `/api/strategies/{name}/toggle`

Enable or disable a specific strategy.

**Path Parameters**:
- `name` (string): Strategy name (e.g., "mean_reversion")

**Request Body**:
```json
{
  "enabled": true
}
```

**Response**:
```json
{
  "success": true,
  "message": "Strategy 'mean_reversion' enabled",
  "data": {
    "strategy": "mean_reversion",
    "enabled": true,
    "updated_at": "2026-02-12T10:30:00Z"
  }
}
```

---

### Grid Trading

#### GET `/api/grids`

Returns all active grid configurations.

**Response**:
```json
{
  "success": true,
  "data": [
    {
      "id": "grid_btc_001",
      "symbol": "BTC/USD",
      "status": "active",
      "levels": 8,
      "upper_price": 50000.00,
      "lower_price": 40000.00,
      "grid_spacing": 1250.00,
      "capital_allocated": 5000.00,
      "orders_placed": 6,
      "orders_filled": 2,
      "unrealized_pnl": 150.00,
      "created_at": "2026-02-12T08:00:00Z"
    }
  ]
}
```

#### POST `/api/grids/create`

Create a new grid trading configuration.

**Request Body**:
```json
{
  "symbol": "BTC/USD",
  "levels": 8,
  "upper_price": 50000.00,
  "lower_price": 40000.00,
  "capital": 5000.00,
  "leverage": 10
}
```

**Response**:
```json
{
  "success": true,
  "message": "Grid created successfully",
  "data": {
    "grid_id": "grid_btc_002",
    "symbol": "BTC/USD",
    "status": "initializing",
    "orders_to_place": 8
  }
}
```

#### POST `/api/grids/{id}/stop`

Stop and optionally close a grid.

**Path Parameters**:
- `id` (string): Grid ID

**Request Body**:
```json
{
  "close_positions": true,
  "cancel_orders": true
}
```

---

### Performance Metrics

#### GET `/api/metrics/performance`

Returns system performance metrics.

**Query Parameters**:
- `timeframe` (string, optional): "1h", "24h", "7d", "30d" (default: "24h")

**Response**:
```json
{
  "success": true,
  "data": {
    "timeframe": "24h",
    "trading": {
      "total_trades": 25,
      "winning_trades": 16,
      "losing_trades": 9,
      "win_rate": 64.0,
      "total_pnl": 850.00,
      "avg_trade_pnl": 34.00,
      "largest_win": 250.00,
      "largest_loss": -100.00,
      "sharpe_ratio": 1.85
    },
    "system": {
      "avg_task_processing_time_ms": 45,
      "avg_database_query_time_ms": 8,
      "avg_api_response_time_ms": 120,
      "error_rate": 0.02,
      "uptime_percent": 99.95
    }
  }
}
```

#### GET `/api/metrics/system`

Returns current system resource metrics.

**Response**:
```json
{
  "success": true,
  "data": {
    "timestamp": "2026-02-12T10:30:00Z",
    "cpu": {
      "usage_percent": 35.5,
      "cores": 8,
      "load_average": [2.1, 1.8, 1.5]
    },
    "memory": {
      "total_mb": 16384,
      "used_mb": 8192,
      "free_mb": 8192,
      "usage_percent": 50.0
    },
    "disk": {
      "total_gb": 500,
      "used_gb": 250,
      "free_gb": 250,
      "usage_percent": 50.0
    },
    "network": {
      "connections_active": 15,
      "bytes_sent": 1250000,
      "bytes_received": 2500000
    }
  }
}
```

---

### Alerts and Notifications

#### GET `/api/alerts`

Returns active alerts and recent alert history.

**Query Parameters**:
- `severity` (string, optional): Filter by severity ("low", "medium", "high", "critical")
- `limit` (int, optional): Maximum alerts to return (default: 50)

**Response**:
```json
{
  "success": true,
  "data": {
    "active_alerts": [
      {
        "id": "alert_001",
        "name": "High CPU Usage",
        "severity": "medium",
        "message": "CPU usage above 80% for 5 minutes",
        "triggered_at": "2026-02-12T10:25:00Z",
        "acknowledged": false
      }
    ],
    "alert_history": [
      {
        "id": "alert_000",
        "name": "Low Memory",
        "severity": "low",
        "message": "Memory usage above 85%",
        "triggered_at": "2026-02-12T09:30:00Z",
        "resolved_at": "2026-02-12T09:35:00Z"
      }
    ]
  }
}
```

#### POST `/api/alerts/{id}/acknowledge`

Acknowledge an active alert.

**Path Parameters**:
- `id` (string): Alert ID

**Response**:
```json
{
  "success": true,
  "message": "Alert acknowledged"
}
```

---

## WebSocket API

### Connection

Connect to the WebSocket endpoint for real-time updates:

```javascript
const ws = new WebSocket('ws://localhost:8000/ws');

ws.onopen = () => {
  console.log('Connected to trading bot');
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  console.log('Received:', data);
};
```

### Message Types

#### Server → Client

**Status Update**:
```json
{
  "type": "status_update",
  "timestamp": "2026-02-12T10:30:00Z",
  "data": {
    "is_running": true,
    "positions_count": 5,
    "total_pnl": 2500.75,
    "current_regime": "TRENDING_STRONG"
  }
}
```

**Trade Notification**:
```json
{
  "type": "trade_executed",
  "timestamp": "2026-02-12T10:30:00Z",
  "data": {
    "trade_id": 152,
    "symbol": "BTC/USD",
    "side": "buy",
    "quantity": 0.5,
    "price": 46000.00,
    "strategy": "mean_reversion",
    "pnl": null
  }
}
```

**Price Update**:
```json
{
  "type": "price_update",
  "timestamp": "2026-02-12T10:30:00Z",
  "data": {
    "symbol": "BTC/USD",
    "price": 46000.00,
    "change_24h": 2.5,
    "volume_24h": 1500000000
  }
}
```

**Alert Notification**:
```json
{
  "type": "alert",
  "timestamp": "2026-02-12T10:30:00Z",
  "data": {
    "alert_id": "alert_001",
    "severity": "high",
    "name": "Circuit Breaker Triggered",
    "message": "Portfolio loss exceeded 10%, trading halted"
  }
}
```

**Metrics Update**:
```json
{
  "type": "metrics_update",
  "timestamp": "2026-02-12T10:30:00Z",
  "data": {
    "cpu_usage": 35.5,
    "memory_usage": 50.0,
    "active_connections": 15,
    "pending_tasks": 3
  }
}
```

#### Client → Server

**Subscribe to Symbol**:
```json
{
  "action": "subscribe",
  "channel": "prices",
  "symbols": ["BTC/USD", "ETH/USD"]
}
```

**Unsubscribe**:
```json
{
  "action": "unsubscribe",
  "channel": "prices"
}
```

**Request Status**:
```json
{
  "action": "get_status"
}
```

---

## Error Handling

### HTTP Status Codes

| Status Code | Meaning | Description |
|-------------|---------|-------------|
| 200 | OK | Request successful |
| 400 | Bad Request | Invalid request parameters |
| 401 | Unauthorized | Authentication required |
| 403 | Forbidden | Insufficient permissions |
| 404 | Not Found | Resource not found |
| 409 | Conflict | Resource conflict (e.g., bot already running) |
| 429 | Too Many Requests | Rate limit exceeded |
| 500 | Internal Server Error | Server error |
| 503 | Service Unavailable | Service temporarily unavailable |

### Error Response Format

```json
{
  "success": false,
  "error": "Descriptive error message",
  "code": "ERROR_CODE",
  "details": {
    "field": "additional context"
  },
  "timestamp": "2026-02-12T10:30:00Z",
  "request_id": "req_abc123"
}
```

### Common Error Codes

| Code | Description | Resolution |
|------|-------------|------------|
| `BOT_ALREADY_RUNNING` | Bot is already active | Stop bot first or check status |
| `BOT_NOT_RUNNING` | Bot is not active | Start bot first |
| `INSUFFICIENT_BALANCE` | Not enough funds | Deposit funds or reduce position size |
| `RATE_LIMIT_EXCEEDED` | API rate limit hit | Wait and retry |
| `INVALID_SYMBOL` | Symbol not supported | Check available markets |
| `STRATEGY_NOT_FOUND` | Strategy doesn't exist | Check strategy name |
| `GRID_NOT_FOUND` | Grid ID not found | Check grid ID |
| `CIRCUIT_BREAKER_ACTIVE` | Emergency stop triggered | Manual reset required |

---

## Rate Limiting

API requests are subject to rate limiting:

- **General Endpoints**: 100 requests per minute
- **Trading Endpoints**: 20 requests per minute
- **Market Data**: 200 requests per minute

Rate limit headers are included in all responses:

```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 95
X-RateLimit-Reset: 1644672000
```

---

## SDK Examples

### Python

```python
import requests
import json

BASE_URL = "http://localhost:8000"

# Get status
response = requests.get(f"{BASE_URL}/api/status")
status = response.json()
print(f"Bot running: {status['data']['is_running']}")

# Start bot
response = requests.post(f"{BASE_URL}/api/bot/start")
print(response.json()['message'])

# Get positions
response = requests.get(f"{BASE_URL}/api/positions")
positions = response.json()['data']
for pos in positions:
    print(f"{pos['symbol']}: {pos['unrealized_pnl']}")
```

### JavaScript

```javascript
const BASE_URL = 'http://localhost:8000';

// Get status
fetch(`${BASE_URL}/api/status`)
  .then(res => res.json())
  .then(data => console.log('Bot running:', data.data.is_running));

// Start bot
fetch(`${BASE_URL}/api/bot/start`, { method: 'POST' })
  .then(res => res.json())
  .then(data => console.log(data.message));

// WebSocket connection
const ws = new WebSocket(`ws://localhost:8000/ws`);
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.type === 'trade_executed') {
    console.log(`Trade: ${data.data.symbol} at ${data.data.price}`);
  }
};
```

### cURL

```bash
# Get status
curl http://localhost:8000/api/status

# Start bot
curl -X POST http://localhost:8000/api/bot/start

# Get positions
curl http://localhost:8000/api/positions

# Create grid
curl -X POST http://localhost:8000/api/grids/create \
  -H "Content-Type: application/json" \
  -d '{
    "symbol": "BTC/USD",
    "levels": 8,
    "upper_price": 50000,
    "lower_price": 40000,
    "capital": 5000
  }'
```

---

## Changelog

### v2.0.0 (February 2026)

- Added queue-based task processing system
- Implemented comprehensive performance monitoring
- Enhanced error handling with circuit breakers
- Added WebSocket real-time updates
- New grid trading management endpoints
- Strategy management and toggling
- Alert system with acknowledgments
- Performance metrics and analytics

### v1.5.0 (January 2026)

- Added Kelly criterion position sizing
- Market regime detection (5 regimes)
- Multi-timeframe data fetching
- Enhanced circuit breaker protection

### v1.0.0 (December 2025)

- Initial API release
- Basic bot controls
- Position and trade management
- Market data endpoints

---

**For additional support, refer to the [TROUBLESHOOTING.md](./TROUBLESHOOTING.md) guide.**
