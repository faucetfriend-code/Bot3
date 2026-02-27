# Prompt 013: Database Connection Context Manager Fix

## Problem
Multiple API endpoints failing with: `'_GeneratorContextManager' object has no attribute 'cursor'`

## Root Cause Analysis
The `get_db_connection()` function returns a context manager (generator), not a database connection directly. Code was incorrectly calling `conn = get_db_connection()` and then `conn.cursor()`, but `conn` was the context manager, not the connection object.

## Investigation Steps
1. Identified that `get_db_connection()` is a `@contextmanager` decorated function that yields `sqlite3.Connection`
2. Found 15+ instances of incorrect usage: `conn = get_db_connection()` followed by `conn.cursor()`
3. Discovered kline endpoint trying to import non-existent `get_database_manager` from `database.py`

## Solution Implemented
- Changed all incorrect usage from:
  ```python
  conn = get_db_connection()
  cursor = conn.cursor()
  # ... use cursor ...
  conn.close()
  ```
- To proper context manager usage:
  ```python
  with get_db_connection() as conn:
      cursor = conn.cursor()
      # ... use cursor ...
  ```

## Endpoints Fixed
- `/api/pacifica/balance-history`
- `/api/signals`
- `/api/pacifica/funding-payments`
- `/api/chart/kline/{symbol}`
- `/api/positions` (fallback mode)
- `/api/trades/export`
- `/api/trades` (fallback mode)
- `/api/risk/positions`
- `/api/pacifica/funding-rates`
- `/api/pacifica/positions`
- `/api/deposits-withdrawals`
- `/api/deposits-withdrawals/record`
- `/api/pacifica/funding-tracker/status`

## Additional Fixes
- Removed incorrect import of `get_database_manager` from `database.py` in kline endpoint
- Fixed kline endpoint to use proper context manager instead of accessing private `_conn`

## Verification
- API server starts without import errors
- All endpoints now use proper database connection management
- Context managers ensure automatic connection cleanup

## Files Modified
- `api_server.py`: Fixed 15+ database connection usage patterns

## Impact
- Eliminates `'cursor'` attribute errors on all affected endpoints
- Improves resource management and prevents connection leaks
- Ensures thread-safe database operations

## Next Steps
- Monitor endpoints for proper data return
- Consider adding database health checks
- Test full application functionality