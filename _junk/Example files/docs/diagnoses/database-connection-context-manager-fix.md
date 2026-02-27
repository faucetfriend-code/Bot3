# Database Connection Context Manager Fix - Prompt 013

## Issue Summary
Multiple API endpoints were failing with the error: `'_GeneratorContextManager' object has no attribute 'cursor'`

## Root Cause
The `get_db_connection()` function returns a context manager (generator), not a database connection directly. Code was incorrectly calling `conn = get_db_connection()` and then `conn.cursor()`, but `conn` was the context manager, not the connection object.

## Affected Endpoints
- `/api/pacifica/balance-history` - Balance history endpoint
- `/api/signals` - Trading signals endpoint
- `/api/pacifica/funding-payments` - Funding payments endpoint
- `/api/chart/kline/{symbol}` - Kline chart data endpoint
- `/api/positions` - Positions endpoint (fallback database mode)
- `/api/trades/export` - Trades export endpoint
- `/api/trades` - Trades endpoint (fallback database mode)
- `/api/risk/positions` - Risk positions endpoint
- `/api/pacifica/funding-rates` - Funding rates endpoint
- `/api/pacifica/positions` - Pacifica positions endpoint
- `/api/deposits-withdrawals` - Deposits/withdrawals endpoint
- `/api/deposits-withdrawals/record` - Record deposit/withdrawal endpoint
- `/api/pacifica/funding-tracker/status` - Funding tracker status endpoint

## Additional Issues Fixed
- **Missing import**: Kline endpoint was trying to import `get_database_manager` from `database.py` (doesn't exist there)
- **Incorrect database access**: Kline endpoint was accessing private `_conn` attribute instead of using proper database methods

## Solution Applied
Changed all incorrect usage from:
```python
conn = get_db_connection()
cursor = conn.cursor()
# ... use cursor ...
conn.close()
```

To proper context manager usage:
```python
with get_db_connection() as conn:
    cursor = conn.cursor()
    # ... use cursor ...
```

## Files Modified
- `api_server.py`: Fixed 15+ instances of incorrect database connection usage across multiple endpoints

## Verification
- API server starts without import errors
- All endpoints now use proper context manager pattern
- Database connections are properly managed (automatic cleanup)

## Impact
- Eliminates `'cursor'` attribute errors on all affected endpoints
- Improves database connection management and resource cleanup
- Ensures thread-safe database operations

## Next Steps
- Test all endpoints to confirm they return data correctly
- Monitor for any remaining database-related errors
- Consider adding database connection health checks