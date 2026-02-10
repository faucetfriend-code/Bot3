# Pacifica Rate Limits

## Overview

Rate limits prevent API abuse and ensure fair resource allocation. Different limits apply to different endpoint types and account tiers.

## Rate Limit Tiers

### Basic Tier (Default)
- REST API: 60 requests/minute
- Order Placement: 10 orders/second
- WebSocket Connections: 3 concurrent
- WebSocket Messages: 30 messages/second

### Advanced Tier
- REST API: 120 requests/minute
- Order Placement: 20 orders/second
- WebSocket Connections: 5 concurrent
- WebSocket Messages: 50 messages/second

### VIP Tier
- REST API: 300 requests/minute
- Order Placement: 50 orders/second
- WebSocket Connections: 10 concurrent
- WebSocket Messages: 100 messages/second

**Tier Eligibility**: Based on trading volume and account history.

## REST API Rate Limits

### Public Endpoints

**Rate**: 100 requests/minute per IP address

**Endpoints**:
- `GET /markets`
- `GET /markets/prices`
- `GET /markets/klines`
- `GET /markets/orderbook`
- `GET /markets/trades`
- `GET /markets/funding`

**Best Practice**: Cache public data locally to reduce API calls.

### Private Endpoints

**Rate**: 60/120/300 requests/minute (based on tier)

**Endpoints**:
- All `/account/*` endpoints
- All `/orders/*` endpoints
- All `/subaccounts/*` endpoints

**Note**: Rate limit is per account, not per IP.

### Order-Specific Limits

**Rate**: 10/20/50 orders/second (based on tier)

**Applies to**:
- `POST /orders/market`
- `POST /orders/limit`
- `POST /orders/stop`
- `POST /orders/tp-sl`
- `POST /orders/batch` (counts as 1 per order in batch)

**Example**: Batch order with 5 orders counts as 5 toward rate limit.

### Cancel Operation Limits

**Rate**: 20 requests/second

**Applies to**:
- `DELETE /orders/{id}`
- `DELETE /orders` (cancel all)
- `DELETE /orders/stop/{id}`

## WebSocket Rate Limits

### Connection Limits

**Concurrent Connections**: 3/5/10 (based on tier)

**Best Practice**: Use one connection for public data, one for private data.

### Subscription Limits

**Rate**: 10 subscribe/unsubscribe per second

**Best Practice**: Subscribe to all needed channels upfront, avoid frequent sub/unsub.

### Message Send Limits

**Rate**: 30/50/100 messages/second (based on tier)

**Applies to**:
- Order placement via WebSocket
- Subscription requests
- Ping messages (exempt, unlimited)

**Note**: Received messages (from server) are unlimited.

### Order Placement via WebSocket

**Rate**: Same as REST (10/20/50 orders/second)

**Advantage**: Lower latency than REST, but same rate limit.

## Rate Limit Headers

Every API response includes rate limit information:

```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 45
X-RateLimit-Reset: 1699999999
```

**Fields**:
- `X-RateLimit-Limit`: Maximum requests allowed in window
- `X-RateLimit-Remaining`: Requests remaining in current window
- `X-RateLimit-Reset`: Unix timestamp when limit resets

### 429 Response

When rate limit exceeded:

```json
{
    "success": false,
    "error": {
        "code": "RATE_LIMIT_EXCEEDED",
        "message": "API rate limit exceeded"
    }
}
```

**Headers**:
```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1699999999
Retry-After: 30
```

**Retry-After**: Seconds until can retry (recommended wait time).

## Rate Limit Windows

### Rolling Window (REST)

- Window: 60 seconds (rolling)
- Example: If you make 60 requests at 12:00:00, you can't make another until 12:01:00

### Fixed Window (Orders)

- Window: 1 second (fixed)
- Example: Can make 10 orders in any 1-second period
- Resets every second

## Burst Behavior

### Allowed Burst

Short bursts above average are allowed up to the limit:
- Can make 60 requests in 10 seconds
- But then must wait 50 seconds for window to roll

**Not Allowed**:
- Cannot make 61 requests in 60 seconds
- Will receive 429 error

## Handling Rate Limits

### 1. Track Rate Limit Headers

```python
class RateLimiter:
    def __init__(self):
        self.limit = None
        self.remaining = None
        self.reset = None

    def update_from_headers(self, headers):
        self.limit = int(headers.get("X-RateLimit-Limit", 0))
        self.remaining = int(headers.get("X-RateLimit-Remaining", 0))
        self.reset = int(headers.get("X-RateLimit-Reset", 0))

    def should_wait(self) -> bool:
        return self.remaining <= 1

    def wait_time(self) -> float:
        if self.reset:
            import time
            return max(0, self.reset - time.time())
        return 1.0

limiter = RateLimiter()

def make_request():
    response = requests.get(url, headers=headers)
    limiter.update_from_headers(response.headers)

    if limiter.should_wait():
        time.sleep(limiter.wait_time())

    return response
```

### 2. Exponential Backoff

```python
import time

def exponential_backoff(attempt: int, base_delay: float = 1.0) -> float:
    """Calculate exponential backoff delay."""
    return min(base_delay * (2 ** attempt), 60)

def request_with_backoff(url, max_retries=5):
    for attempt in range(max_retries):
        response = requests.get(url)

        if response.status_code != 429:
            return response

        if attempt == max_retries - 1:
            raise Exception("Max retries exceeded")

        # Use Retry-After header if available
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            wait_time = int(retry_after)
        else:
            wait_time = exponential_backoff(attempt)

        time.sleep(wait_time)
```

### 3. Request Queue

```python
import asyncio
from collections import deque

class RequestQueue:
    def __init__(self, max_per_second: int = 10):
        self.max_per_second = max_per_second
        self.queue = deque()
        self.timestamps = deque()

    async def wait_if_needed(self):
        """Wait if rate limit would be exceeded."""
        now = time.time()

        # Remove timestamps older than 1 second
        while self.timestamps and now - self.timestamps[0] > 1.0:
            self.timestamps.popleft()

        # Wait if at limit
        if len(self.timestamps) >= self.max_per_second:
            wait_time = 1.0 - (now - self.timestamps[0])
            if wait_time > 0:
                await asyncio.sleep(wait_time)

        self.timestamps.append(now)

    async def request(self, func):
        """Execute request with rate limiting."""
        await self.wait_if_needed()
        return await func()

# Usage
queue = RequestQueue(max_per_second=10)

async def place_order():
    return await queue.request(lambda: api.create_order(...))
```

### 4. Batch Requests

Reduce API calls by batching operations:

```python
# Instead of individual orders (10 API calls)
for price in prices:
    api.create_limit_order(market, "buy", size, price)

# Use batch order (1 API call)
orders = [
    {"market": market, "side": "buy", "size": size, "price": p}
    for p in prices
]
api.batch_order(orders)
```

### 5. Caching

Cache frequently accessed data:

```python
from functools import lru_cache
import time

class CachedAPI:
    def __init__(self, client):
        self.client = client
        self._market_cache = {}
        self._cache_ttl = 60  # seconds

    def get_markets(self):
        """Cache market info for 60 seconds."""
        now = time.time()

        if "markets" in self._market_cache:
            data, timestamp = self._market_cache["markets"]
            if now - timestamp < self._cache_ttl:
                return data

        # Fetch fresh data
        markets = self.client.get_markets()
        self._market_cache["markets"] = (markets, now)
        return markets
```

### 6. WebSocket for Real-Time Data

Use WebSocket instead of polling REST API:

```python
# Bad: Polling (60 requests/minute)
while True:
    prices = api.get_prices()
    process(prices)
    time.sleep(1)

# Good: WebSocket (0 requests, real-time updates)
ws.subscribe("prices")
ws.on("price_update", lambda data: process(data))
```

## Optimization Strategies

### For High-Frequency Trading

1. **Use WebSocket**: All order operations and data streaming
2. **Batch Orders**: Submit multiple orders at once
3. **Cache Market Data**: Don't re-fetch static info
4. **Minimize Cancels**: Only cancel when necessary
5. **Request VIP Tier**: Higher limits for active traders

### For Market Making

1. **Use Single WebSocket**: Subscribe to orderbook and positions
2. **Batch Order Updates**: Update multiple orders at once
3. **Cancel All Endpoint**: More efficient than individual cancels
4. **Selective Subscriptions**: Only subscribe to active markets
5. **Local State Management**: Track positions locally, sync periodically

### For Long-Term Bots

1. **Poll Less Frequently**: Don't need real-time for daily strategies
2. **Cache Aggressively**: Market specs, account settings rarely change
3. **Use Webhooks**: If available, for account updates
4. **Batch Operations**: Group related operations
5. **Off-Peak API Calls**: Schedule heavy operations during low-use times

## Monitoring Rate Limits

### Log Rate Limit Usage

```python
from loguru import logger

def log_rate_limit(response):
    headers = response.headers
    logger.info(
        f"Rate Limit: {headers.get('X-RateLimit-Remaining')}/{headers.get('X-RateLimit-Limit')} "
        f"(resets at {headers.get('X-RateLimit-Reset')})"
    )
```

### Alert on Low Remaining

```python
def check_rate_limit_health(response):
    remaining = int(response.headers.get("X-RateLimit-Remaining", 100))
    limit = int(response.headers.get("X-RateLimit-Limit", 60))

    usage_percent = ((limit - remaining) / limit) * 100

    if usage_percent > 90:
        logger.warning(f"Rate limit usage: {usage_percent:.1f}%")

    if remaining == 0:
        logger.error("Rate limit exhausted!")
```

### Metrics Dashboard

Track over time:
- Requests per minute
- Rate limit exhaustion events
- Average remaining quota
- 429 error frequency

## Best Practices Summary

1. **Always check rate limit headers**: Monitor remaining quota
2. **Implement exponential backoff**: Handle 429 errors gracefully
3. **Use request queues**: Prevent exceeding limits
4. **Batch operations**: Reduce total API calls
5. **Cache static data**: Market specs, etc.
6. **Use WebSocket for real-time**: Avoid polling
7. **Monitor and alert**: Track rate limit usage
8. **Request tier upgrade**: If hitting limits frequently

## Testing Rate Limits

### Local Rate Limit Simulator

```python
class RateLimitSimulator:
    """Simulate Pacifica rate limits for testing."""

    def __init__(self, limit: int = 60, window: int = 60):
        self.limit = limit
        self.window = window
        self.requests = deque()

    def allow_request(self) -> bool:
        """Check if request is allowed."""
        now = time.time()

        # Remove old requests outside window
        while self.requests and now - self.requests[0] > self.window:
            self.requests.popleft()

        # Check if at limit
        if len(self.requests) >= self.limit:
            return False

        self.requests.append(now)
        return True

    def time_until_available(self) -> float:
        """Time until next request allowed."""
        if not self.requests or len(self.requests) < self.limit:
            return 0.0

        oldest = self.requests[0]
        return max(0, self.window - (time.time() - oldest))
```

## Additional Resources

- See `error_codes.md` for rate limit error handling
- See `api_reference_rest.md` for endpoint documentation
- See `api_reference_websocket.md` for WebSocket limits
