# Pacifica Error Codes Reference

## Error Response Format

```json
{
    "success": false,
    "error": {
        "code": "ERROR_CODE",
        "message": "Human readable error message",
        "details": {}  // optional additional context
    }
}
```

## Authentication Errors (1000-1099)

### INVALID_API_KEY (1001)
**Message**: "API key not found or disabled"
**Cause**: Public key not registered or account disabled
**Fix**: Verify API key is correct and account is active

### INVALID_SIGNATURE (1002)
**Message**: "Request signature verification failed"
**Cause**: Signature doesn't match expected value
**Fix**: Check signature algorithm, string to sign format, private key

### TIMESTAMP_EXPIRED (1003)
**Message**: "Request timestamp too old or in future"
**Cause**: Timestamp more than 5 seconds from server time
**Fix**: Synchronize system clock, use current timestamp

### AUTHENTICATION_REQUIRED (1004)
**Message**: "This endpoint requires authentication"
**Cause**: Accessing private endpoint without auth headers
**Fix**: Include X-API-Key, X-Signature, X-Timestamp headers

### INSUFFICIENT_PERMISSIONS (1005)
**Message**: "Account lacks required permissions"
**Cause**: API key doesn't have permission for this action
**Fix**: Check account permissions, use key with correct access

## Order Errors (2000-2099)

### INSUFFICIENT_BALANCE (2001)
**Message**: "Insufficient balance for order"
**Cause**: Not enough margin to open position
**Fix**: Reduce order size, add more funds, or close other positions

### INVALID_ORDER_SIZE (2002)
**Message**: "Order size outside allowed range"
**Cause**: Size below minimum or above maximum
**Fix**: Check market specs for min/max order size

### INVALID_PRICE (2003)
**Message**: "Price does not match tick size"
**Cause**: Price not a multiple of tick size
**Fix**: Round price to nearest tick size

### MARKET_CLOSED (2004)
**Message**: "Market is currently closed or halted"
**Cause**: Trading halted or market maintenance
**Fix**: Wait for market to reopen, check market status

### ORDER_NOT_FOUND (2005)
**Message**: "Order ID not found"
**Cause**: Order doesn't exist or already processed
**Fix**: Verify order ID, check order history

### POSITION_LIMIT_EXCEEDED (2006)
**Message**: "Order would exceed position limits"
**Cause**: Position size too large for account/market
**Fix**: Reduce order size or leverage

### SELF_TRADE_PREVENTION (2007)
**Message**: "Order would trade against own order"
**Cause**: Self-trading not allowed
**Fix**: Cancel existing opposite side orders first

### POST_ONLY_WOULD_TRADE (2008)
**Message**: "Post-only order would execute immediately"
**Cause**: Post-only order price crosses spread
**Fix**: Adjust price to not cross spread, or don't use post-only

### REDUCE_ONLY_INVALID (2009)
**Message**: "Reduce-only order would increase position"
**Cause**: No position or order on wrong side
**Fix**: Check position side and size, adjust order

### INVALID_LEVERAGE (2010)
**Message**: "Leverage outside allowed range"
**Cause**: Leverage below minimum or above maximum for market
**Fix**: Use leverage between 5x and max leverage for market

## Position Errors (3000-3099)

### NO_POSITION (3001)
**Message**: "No position found for market"
**Cause**: Trying to modify non-existent position
**Fix**: Open position first

### POSITION_LIQUIDATED (3002)
**Message**: "Position is being liquidated"
**Cause**: Position health dropped below minimum
**Fix**: Cannot be prevented at this point, add margin earlier

### INSUFFICIENT_MARGIN (3003)
**Message**: "Insufficient margin for operation"
**Cause**: Not enough margin to support position after operation
**Fix**: Add margin or reduce position size

### MARGIN_MODE_CHANGE_BLOCKED (3004)
**Message**: "Cannot change margin mode with open positions"
**Cause**: Trying to switch margin mode with active positions
**Fix**: Close all positions first, then change margin mode

### LEVERAGE_CHANGE_BLOCKED (3005)
**Message**: "Cannot change leverage with insufficient margin"
**Cause**: New leverage would require more margin than available
**Fix**: Add margin or reduce position before changing leverage

## Market Data Errors (4000-4099)

### MARKET_NOT_FOUND (4001)
**Message**: "Market symbol not found"
**Cause**: Invalid market symbol
**Fix**: Use valid market symbol (e.g., "BTC-PERP")

### INVALID_INTERVAL (4002)
**Message**: "Invalid candle interval"
**Cause**: Unsupported time interval
**Fix**: Use valid interval (1m, 5m, 15m, 1h, 4h, 1d)

### INVALID_TIME_RANGE (4003)
**Message**: "Invalid start/end time range"
**Cause**: End time before start time, or range too large
**Fix**: Check time parameters, limit range size

### DATA_NOT_AVAILABLE (4004)
**Message**: "Historical data not available for this range"
**Cause**: Requesting data before market launch
**Fix**: Adjust time range to available data

## Account Errors (5000-5099)

### ACCOUNT_SUSPENDED (5001)
**Message**: "Account is suspended"
**Cause**: Account violate terms or under investigation
**Fix**: Contact support

### WITHDRAWAL_LIMIT_EXCEEDED (5002)
**Message**: "Daily withdrawal limit exceeded"
**Cause**: Requested withdrawal exceeds daily limit
**Fix**: Wait for limit reset (24 hours) or reduce amount

### INSUFFICIENT_WITHDRAWAL_BALANCE (5003)
**Message**: "Insufficient available balance for withdrawal"
**Cause**: Balance tied up in positions/orders
**Fix**: Close positions or cancel orders to free balance

### SUBACCOUNT_LIMIT_EXCEEDED (5004)
**Message**: "Maximum subaccount limit reached"
**Cause**: Too many subaccounts created
**Fix**: Delete unused subaccounts

### INVALID_TRANSFER_AMOUNT (5005)
**Message**: "Transfer amount invalid"
**Cause**: Amount negative, zero, or exceeds balance
**Fix**: Check transfer amount and available balance

## Rate Limiting Errors (6000-6099)

### RATE_LIMIT_EXCEEDED (6001)
**Message**: "API rate limit exceeded"
**Cause**: Too many requests in time window
**Fix**: Implement rate limiting, use exponential backoff

**Headers on 429 Response**:
```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1699999999
Retry-After: 30
```

### ORDER_RATE_LIMIT_EXCEEDED (6002)
**Message**: "Order rate limit exceeded"
**Cause**: Too many order requests per second
**Fix**: Reduce order frequency, use batch orders

### WEBSOCKET_MESSAGE_RATE_EXCEEDED (6003)
**Message**: "WebSocket message rate exceeded"
**Cause**: Too many messages sent via WebSocket
**Fix**: Reduce subscription/order frequency

## System Errors (7000-7099)

### INTERNAL_SERVER_ERROR (7001)
**Message**: "Internal server error"
**Cause**: Server-side error
**Fix**: Retry with exponential backoff, contact support if persists

### SERVICE_UNAVAILABLE (7002)
**Message**: "Service temporarily unavailable"
**Cause**: Maintenance or overload
**Fix**: Wait and retry, check status page

### INVALID_REQUEST (7003)
**Message**: "Invalid request format"
**Cause**: Malformed JSON or missing required fields
**Fix**: Validate request format, check API documentation

### UNSUPPORTED_OPERATION (7004)
**Message**: "Operation not supported"
**Cause**: Feature not available or deprecated
**Fix**: Use alternative endpoint/method

## WebSocket Errors (8000-8099)

### WS_AUTHENTICATION_FAILED (8001)
**Message**: "WebSocket authentication failed"
**Cause**: Invalid credentials or expired signature
**Fix**: Regenerate signature, check credentials

### WS_SUBSCRIPTION_FAILED (8002)
**Message**: "Channel subscription failed"
**Cause**: Invalid channel name or insufficient permissions
**Fix**: Verify channel name, authenticate for private channels

### WS_ALREADY_SUBSCRIBED (8003)
**Message**: "Already subscribed to this channel"
**Cause**: Duplicate subscription request
**Fix**: Ignore, already subscribed

### WS_NOT_SUBSCRIBED (8004)
**Message**: "Not subscribed to this channel"
**Cause**: Trying to unsubscribe from channel not subscribed to
**Fix**: Ignore or resubscribe first

### WS_CONNECTION_LIMIT_EXCEEDED (8005)
**Message**: "Too many concurrent WebSocket connections"
**Cause**: Exceeded max connections per account
**Fix**: Close unused connections, consolidate subscriptions

## Validation Errors (9000-9099)

### INVALID_MARKET_SYMBOL (9001)
**Message**: "Invalid market symbol format"
**Cause**: Symbol doesn't match expected format
**Fix**: Use format like "BTC-PERP"

### INVALID_SIDE (9002)
**Message**: "Invalid order side"
**Cause**: Side not "buy" or "sell"
**Fix**: Use "buy" or "sell"

### INVALID_ORDER_TYPE (9003)
**Message**: "Invalid order type"
**Cause**: Unsupported order type
**Fix**: Use "market", "limit", or "stop"

### INVALID_TIME_IN_FORCE (9004)
**Message**: "Invalid time in force"
**Cause**: Invalid TIF value
**Fix**: Use "GTC", "IOC", or "FOK"

### MISSING_REQUIRED_FIELD (9005)
**Message**: "Required field missing"
**Cause**: Request missing required parameter
**Fix**: Include all required fields in request

### INVALID_MARGIN_MODE (9006)
**Message**: "Invalid margin mode"
**Cause**: Margin mode not "cross" or "isolated"
**Fix**: Use "cross" or "isolated"

## Error Handling Best Practices

### Retry Strategy

```python
import time

def exponential_backoff_retry(func, max_retries=3):
    for attempt in range(max_retries):
        try:
            return func()
        except RateLimitError as e:
            if attempt == max_retries - 1:
                raise
            wait_time = 2 ** attempt
            time.sleep(wait_time)
        except ServerError as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(5)
```

### Error Classification

```python
class PacificaError(Exception):
    """Base exception for Pacifica API errors."""
    pass

class AuthenticationError(PacificaError):
    """Authentication failed (1000-1099)."""
    pass

class OrderError(PacificaError):
    """Order rejected (2000-2099)."""
    pass

class RateLimitError(PacificaError):
    """Rate limit exceeded (6000-6099)."""
    pass

class ServerError(PacificaError):
    """Server error (7000-7099)."""
    pass

def handle_api_error(error_code: str) -> Exception:
    if 1000 <= int(error_code) < 1100:
        return AuthenticationError(error_code)
    elif 2000 <= int(error_code) < 2100:
        return OrderError(error_code)
    elif 6000 <= int(error_code) < 6100:
        return RateLimitError(error_code)
    elif 7000 <= int(error_code) < 7100:
        return ServerError(error_code)
    else:
        return PacificaError(error_code)
```

### Logging Errors

```python
from loguru import logger

def log_api_error(error_response):
    error = error_response.get("error", {})
    logger.error(
        f"API Error: {error.get('code')} - {error.get('message')}",
        extra={"details": error.get("details")}
    )
```

### User-Friendly Messages

```python
ERROR_MESSAGES = {
    "INSUFFICIENT_BALANCE": "Not enough funds. Please deposit or close other positions.",
    "INVALID_ORDER_SIZE": "Order size is too small or too large for this market.",
    "RATE_LIMIT_EXCEEDED": "Too many requests. Please slow down.",
    "MARKET_CLOSED": "Market is currently closed. Please try again later.",
}

def get_user_message(error_code: str) -> str:
    return ERROR_MESSAGES.get(error_code, "An error occurred. Please try again.")
```

## HTTP Status Codes

- **200 OK**: Success
- **400 Bad Request**: Invalid request (validation errors)
- **401 Unauthorized**: Authentication failed
- **403 Forbidden**: Insufficient permissions
- **404 Not Found**: Resource not found
- **429 Too Many Requests**: Rate limit exceeded
- **500 Internal Server Error**: Server error
- **503 Service Unavailable**: Service down

## Additional Resources

- See `api_reference_rest.md` for endpoint documentation
- See `rate_limits.md` for rate limiting details
- See `authentication.md` for auth implementation
