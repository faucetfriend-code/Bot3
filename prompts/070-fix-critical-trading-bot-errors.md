<objective>
Repair the trading bot v2 system by fixing the critical errors identified in the diagnostic reports. The system currently has a 35/100 health score and is NOT safe for live trading. The grid trading system is completely non-functional due to async/await programming errors and database integration issues.

This repair is critical because the current state prevents any grid trading operations, causing 0 orders to be placed despite active signal generation.
</objective>

<context>
This is a Python-based cryptocurrency trading bot using FastAPI, SQLite, and WebSocket connections to Pacifica exchange. The system was recently enhanced but contains critical bugs that broke the grid trading functionality.

Reference documents to understand the issues:
- @./TRADING_BOT_DIAGNOSTIC_REPORT.md - Full diagnostic report with all issues
- @./MONITORING_SUMMARY.md - Quick executive summary

Current working components (DO NOT break):
- WebSocket connection to Pacifica
- Real-time price feeds (60+ symbols)
- API server on port 8000
- 8 trading strategies initialized
- Signal generation (4-8 per cycle)

Broken components to fix:
- Grid Lifecycle Manager (async/await error)
- Universal Grid State Consistency (database tuple/dict mismatch)
- Grid API endpoint (response format error)
</context>

<requirements>
Fix the following CRITICAL errors in order of priority:

### 1. Grid System Async/Await Error
**File**: `trading_bot_v2/grid_lifecycle_manager.py`
**Line**: ~263
**Error**: `'coroutine' object has no attribute 'consistent_symbols'`
**Fix**: Add `await` keyword to the async function call that initializes the grid system

The error occurs because an async function is being called without await:
```python
# BEFORE (broken):
self._initialize_grid_system()

# AFTER (fixed):
await self._initialize_grid_system()
```

### 2. Database Query Result Format
**File**: `trading_bot_v2/universal_grid_state_consistency.py`  
**Line**: ~350
**Error**: `tuple indices must be integers or slices, not str`
**Fix**: Ensure the database query returns dictionaries instead of tuples by adding proper row_factory

The code expects dictionary access but receives tuple results from SQLite:
```python
# Add to database connection:
conn.row_factory = sqlite3.Row
# Or use aiosqlite Row factory
```

### 3. Grid API Response Format Error
**File**: `trading_bot_v2/api_server.py`
**Error**: `'list' object has no attribute 'items'`
**Location**: Grids endpoint (~line 489)
**Fix**: Handle the response format properly - the endpoint returns a list but code tries to call .items() on it

### 4. Additional Async Issues in Grid Lifecycle Manager
**File**: `trading_bot_v2/grid_lifecycle_manager.py`
**Lines**: ~2563, ~2575
**Error**: Functions returning Coroutine instead of Dict
**Fix**: Ensure async functions are properly awaited or called

### 5. FastAPI Deprecation Warnings (MEDIUM Priority)
**File**: `trading_bot_v2/api_server.py`
**Lines**: 1085, 1097
**Warning**: Using deprecated `@app.on_event` instead of lifespan handlers
**Fix**: Replace with asynccontextmanager lifespan pattern

### 6. Task Queue System Async Errors (if present)
**File**: `trading_bot_v2/task_queue_system.py`  
**Lines**: ~295, ~304
**Error**: Async function results not awaited
**Fix**: Add await or properly handle async function calls
</requirements>

<implementation>
Follow these steps precisely:

1. **First**, examine the specific error locations in each file to understand the exact code context
2. **Apply fixes** in this order (critical first):
   - Fix async/await in grid_lifecycle_manager.py (line 263)
   - Fix async returns in grid_lifecycle_manager.py (lines 2563, 2575)  
   - Fix database query in universal_grid_state_consistency.py (line 350)
   - Fix grid API response in api_server.py (line 489)
   - Fix FastAPI deprecation (lines 1085, 1097)
   - Fix task_queue_system async issues (lines 295, 304)
3. **Test after each major fix** to ensure no regressions
4. **Verify** the grid system initializes correctly

For the async fixes, ensure proper patterns:
- If function is async, it MUST be awaited
- If calling async method from sync code, use asyncio.run() or create_task
- Check LSP/type checker warnings for similar issues

For database fixes:
- Use parameterized queries
- Ensure row_factory is set for dictionary-style access
- Test queries manually if needed
</implementation>

<output>
Modified files:
- `trading_bot_v2/grid_lifecycle_manager.py` - Fixed async/await errors
- `trading_bot_v2/universal_grid_state_consistency.py` - Fixed database query format
- `trading_bot_v2/api_server.py` - Fixed grid API response and FastAPI deprecation
- `trading_bot_v2/task_queue_system.py` - Fixed async function handling

Output verification file:
- `./REPAIR_VERIFICATION.md` - Test results and confirmation of fixes
</output>

<verification>
After applying fixes, verify:

1. **Start the server** and check for ERROR messages:
   ```
   ./run_bot.bat
   ```

2. **Expected results** (no errors should appear):
   - Grid system initializes successfully
   - No "'coroutine' object has no attribute" errors
   - No "tuple indices must be integers" errors
   - No "'list' object has no attribute 'items'" errors
   - Grid state validation completes with 3/3 consistent grids

3. **Check grid status** via API:
   - Visit http://localhost:8000/api/grids
   - Should return grid data without errors

4. **Run health check**:
   - Check that WebSocket still connects
   - Verify price feeds still work
   - Confirm signal generation active

5. **System health target**: Improve from 35/100 to 70+/100
</verification>

<success_criteria>
1. All critical errors fixed - no ERROR level messages in startup log
2. Grid system initializes successfully 
3. Database queries return proper format (dictionaries not tuples)
4. Grid API endpoint returns valid response
5. WebSocket and price feeds continue working
6. Signal generation remains active
7. System health score improves to 70+/100
8. No regressions introduced
</success_criteria>