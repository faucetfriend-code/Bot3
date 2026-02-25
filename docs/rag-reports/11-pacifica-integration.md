# Pacifica.fi Exchange Integration

<!--
RAG Metadata:
- Category: Exchange Integration
- Tags: pacificafi, rest-api, websocket, python-sdk, typescript-sdk, perpetual-futures, solana
- Related: 04-exchange-integration, 11-pacifica-integration, 12-solana-bot-repos
-->

## Overview

Pacifica.fi is a Solana-based perpetual futures exchange offering up to 100x leverage on crypto assets. This document covers the official SDKs, API endpoints, and integration patterns for building trading bots.

---

## API Endpoints

### Base URLs

| Environment | REST API | WebSocket |
|-------------|----------|-----------|
| **Mainnet** | `https://api.pacifica.fi/api/v1` | `wss://ws.pacifica.fi/ws` |
| **Testnet** | `https://test-api.pacifica.fi/api/v1` | `wss://test-ws.pacifica.fi/ws` |

---

## Official SDKs

### Python SDK

**Repository**: https://github.com/pacifica-fi/python-sdk

**Installation**:
```bash
git clone https://github.com/pacifica-fi/python-sdk
cd python-sdk
pip3 install -r requirements.txt
```

**Usage**:
```bash
# Set your private key in the example file
python3 -m rest.create_market_order
python3 -m ws.create_market_order
```

**Repository Structure**:
```
python-sdk/
├── common/           # Shared utilities
├── rest/             # REST API examples
│   ├── create_market_order.py
│   ├── api_config_keys.py
│   └── ...
├── ws/               # WebSocket examples
│   ├── create_market_order.py
│   └── ...
└── requirements.txt
```

---

### TypeScript SDK (Community)

**Repository**: https://github.com/Bvvvp009/pacifica-ts-sdk
**License**: MIT

**Installation**:
```bash
npm install pacifica-ts-sdk
# or
yarn add pacifica-ts-sdk
```

**Quick Start**:
```typescript
import { SignClient, ApiClient } from 'pacifica-ts-sdk';

// For authenticated operations
const signClient = new SignClient('your-private-key-hex', {
  accountPublicKey: 'your-public-key-hex',
  expiryWindow: 300,
});

// For read-only operations
const apiClient = new ApiClient();

// Create an order
const order = await signClient.createOrder({
  symbol: 'BTC',
  side: 'bid',
  amount: '0.1',
  price: '50000',
  reduce_only: false,
  tif: 'GTC',
  client_order_id: `order-${Date.now()}`,
});
```

---

## REST API Endpoints

### Market Data (No Auth Required)

```
GET /api/v1/markets                    # List all markets
GET /api/v1/prices                     # Current prices
GET /api/v1/orderbook?symbol=BTC       # Orderbook data
GET /api/v1/trades?symbol=BTC          # Recent trades
GET /api/v1/candle?symbol=BTC&interval=1m  # Candlestick data
```

### Account Operations (Signing Required)

```
POST /api/v1/account/info              # Account information
POST /api/v1/account/leverage          # Update leverage
POST /api/v1/account/margin            # Update margin mode
POST /api/v1/account/withdraw          # Withdraw funds
```

### Order Operations (Signing Required)

```
POST /api/v1/order                     # Create limit order
POST /api/v1/order/market              # Create market order
POST /api/v1/order/cancel              # Cancel order
POST /api/v1/order/cancel_all          # Cancel all orders
POST /api/v1/order/edit                # Edit order
```

### Funding & History

```
GET /api/v1/funding/history?account=...&limit=20  # Funding history
```

---

## WebSocket API

### Connection

```typescript
import { WebSocketClient } from 'pacifica-ts-sdk';

const wsClient = new WebSocketClient({
  url: 'wss://api.pacifica.fi/ws',
  privateKey: 'your-private-key-hex',
  accountPublicKey: 'your-public-key-hex',
  reconnect: true,
});

await wsClient.connect();
```

### Subscriptions

**Price Subscription**:
```json
{
    "method": "subscribe",
    "params": {
        "source": "prices"
    }
}
```

**Orderbook Subscription**:
```json
{
    "method": "subscribe",
    "params": {
        "source": "book",
        "symbol": "SOL",
        "agg_level": 1
    }
}
```

**Trades Subscription**:
```json
{
    "method": "subscribe",
    "params": {
        "source": "trades",
        "symbol": "SOL"
    }
}
```

**Account Orders**:
```json
{
    "method": "subscribe",
    "params": {
        "source": "account_orders",
        "account": "your_wallet_address"
    }
}
```

### Trading via WebSocket

**Create Market Order**:
```json
{
  "id": "unique-request-id",
  "params": {
    "create_market_order": {
      "account": "AwX6321...",
      "signature": "5vnYpt...",
      "timestamp": 1749223025396,
      "expiry_window": 5000,
      "symbol": "BTC",
      "reduce_only": false,
      "amount": "0.001",
      "side": "bid",
      "slippage_percent": "0.5",
      "client_order_id": "unique-order-id"
    }
  }
}
```

**Create Limit Order**:
```json
{
  "id": "unique-request-id",
  "params": {
    "create_limit_order": {
      "account": "AwX6321...",
      "signature": "5vnYpt...",
      "timestamp": 1749223025396,
      "symbol": "BTC",
      "amount": "0.001",
      "price": "50000",
      "side": "bid",
      "tif": "GTC"
    }
  }
}
```

---

## Order Types & Parameters

### Available Order Types

| Order Type | Description |
|------------|-------------|
| **Market** | Executes immediately at best available prices |
| **Limit** | Executes at specified price or better |
| **Stop Market** | Market order triggered by price condition |
| **Stop Limit** | Limit order triggered by price condition |

### Time-In-Force (TIF)

| TIF | Description |
|-----|-------------|
| **GTC** | Good-Til-Cancelled - remains active until filled or cancelled |
| **IOC** | Immediate-or-Cancel - fills immediately, unfilled portion cancelled |
| **ALO** | Add-Liquidity-Only (Post Only) - cancelled if would cross |
| **TOB** | Top-of-Book - Post Only orders that cross get placed at best bid/ask |

**Note**: Market, GTC, and IOC orders have a randomized 50-100ms delay for liquidity protection.

---

## Rate Limiting

### Credit Quotas

| Tier | Credits/60s |
|------|-------------|
| Unidentified IP | 125 |
| Valid API Config Key | 300 |
| Tier 1 | 300 |
| Tier 2 | 600 |
| Tier 3 | 1200 |
| Tier 4 | 2400 |
| Tier 5 | 6000 |
| VIP1 | 12,000-20,000 |
| VIP2 | 24,000-30,000 |
| VIP3 | 30,000-40,000 |

### Credit Costs

| Action | Cost |
|--------|------|
| Standard request/action | 1 credit |
| Order cancellation | 0.5 credits |
| Heavy GET requests | 1-12 credits |

### WebSocket Limits
- Max 300 concurrent connections per IP
- Max 20 subscriptions per channel per connection

### Checking Your Quota

**REST Headers**:
```
ratelimit: "credits";r=1200;t=32
ratelimit-policy: "credits";q=1250;w=60
```

**WebSocket Response**:
```json
{"rl": {"r": 1200, "q": 1250, "t": 32}}
```

---

## Error Handling

### HTTP Response Codes

| Code | Description |
|------|-------------|
| 400 | Bad Request |
| 403 | Forbidden: no access code/restricted region |
| 404 | Not Found |
| 409 | Conflict |
| 422 | Business Logic Error |
| 429 | Too Many Requests - Rate limit exceeded |
| 500 | Internal Server Error |
| 503 | Service Unavailable |
| 504 | Gateway Timeout |

### Business Logic Errors (Code 422)

| Code | Error |
|------|-------|
| 0 | UNKNOWN |
| 1 | ACCOUNT_NOT_FOUND |
| 2 | BOOK_NOT_FOUND |
| 3 | INVALID_TICK_LEVEL |
| 4 | INSUFFICIENT_BALANCE |
| 5 | ORDER_NOT_FOUND |
| 6 | OVER_WITHDRAWAL |
| 7 | INVALID_LEVERAGE |
| 8 | CANNOT_UPDATE_MARGIN |
| 9 | POSITION_NOT_FOUND |
| 10 | POSITION_TPSL_LIMIT_EXCEEDED |

### WebSocket Error Codes

| Code | Description |
|------|-------------|
| 200 | SUCCESS_CODE |
| 400 | INVALID_REQUEST_CODE |
| 401 | INVALID_SIGNATURE_CODE |
| 402 | INVALID_SIGNER_CODE |
| 403 | UNAUTHORIZED_REQUEST_CODE |
| 420 | ENGINE_ERROR_CODE |
| 429 | RATE_LIMIT_EXCEEDED_CODE |
| 500 | UNKNOWN_ERROR_CODE |

---

## Margin & Leverage

### Margin Modes

| Mode | Description |
|------|-------------|
| **Cross Margin** | Default. Uses entire account balance for all positions |
| **Isolated Margin** | Dedicated margin for each individual position |

### Update Leverage

```python
POST /api/v1/account/leverage

{
  "account": "42trU9A5...",
  "symbol": "BTC",
  "leverage": 10,
  "timestamp": 1716200000000,
  "expiry_window": 30000,
  "signature": "5j1Vy9UqY..."
}
```

### Withdrawable Balance

```
withdrawable_balance = account_balance + unrealized_pnl 
  - max(initial_margin_required, 0.1 * total_position_value)
```

---

## Funding Rates

### Funding Rate Calculation

```
funding_rate = (premium_index + clamp(interest_rate - premium_index, -0.05%, 0.05%)) / 8
```

Where:
- **Premium Index**: `impact_price / oracle_price - 1`
- **Interest Rate**: Fixed at 0.01% (8-hour)
- **Clamp**: ±0.05% keeps funding static for small fluctuations

### Funding Schedule
- **Sampling**: Every 5 seconds
- **Application**: End of each 1-hour interval
- **Cap**: ±4% per hour

---

## Agent Wallet & Subaccounts

### Agent Wallet Setup

Allows programs to trade on behalf of your account without exposing your main wallet's private key.

**Usage Pattern**:
```python
# For all POST requests:
# 1. Add agent_wallet to payload
# 2. Use agent wallet's private key for signing
# 3. Use original wallet's public key for 'account' field

{
  "account": "ORIGINAL_WALLET_PUBLIC_KEY",
  "agent_wallet": "AGENT_WALLET_PUBLIC_KEY",
  "signature": "signed_with_agent_private_key",
  ...
}
```

### Subaccounts

**Create Subaccount**:
```
POST /api/v1/account/subaccount/create

{
  "main_account": "42trU9A5...",
  "subaccount": "69trU9A5...",
  "main_account_signature": "...",
  "subaccount_signature": "...",
  "timestamp": 1716200000000,
  "expiry_window": 30000
}
```

---

## Example Bot: Pacifica Scalping Bot

**Repository**: https://github.com/gammahazard/auto-trade

**Key Features**:
- Resilient WebSocket architecture with auto-reconnection
- 5-Factor signal generation strategy
- Server-side TP/SL orders immediately upon trade entry
- Time-based kill-switch for stale trades

**Configuration**:
| Parameter | Default | Description |
|-----------|---------|-------------|
| `LEVERAGE` | 8x | Position leverage multiplier |
| `COLLATERAL_USD` | $400 | Capital per trade |
| `TAKE_PROFIT_PERCENT` | 0.06% | TP threshold |
| `STOP_LOSS_PERCENT` | 0.03% | SL threshold |
| `MAX_TRADE_DURATION_MS` | 5 min | Kill-switch timeout |
| `IMBALANCE_RATIO` | 1.85 | Min bid/ask volume ratio |

---

## Key Resources

| Resource | URL |
|----------|-----|
| Main Documentation | https://docs.pacifica.fi |
| API Documentation | https://docs.pacifica.fi/api-documentation/api |
| Official Python SDK | https://github.com/pacifica-fi/python-sdk |
| Community TS SDK | https://github.com/Bvvvp009/pacifica-ts-sdk |
| Scalping Bot Example | https://github.com/gammahazard/auto-trade |
| Mainnet App | https://app.pacifica.fi |
| Testnet App | https://test-app.pacifica.fi |
| Discord API Channel | https://discord.com/channels/1325864651816435822/1378723526957334548 |

---

## Related Reports

- [04-exchange-integration.md](./04-exchange-integration.md) - Current bot's Pacifica client
- [12-solana-bot-repos.md](./12-solana-bot-repos.md) - Other Solana bot examples
- [14-bot-operations.md](./14-bot-operations.md) - Production deployment
