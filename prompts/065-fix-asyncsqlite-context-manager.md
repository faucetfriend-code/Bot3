<objective>
Fix the AsyncSQLite context manager protocol error that is preventing grid state consistency validation from working properly. This is a Priority 1 critical issue that must be resolved immediately to restore grid state management functionality.

The diagnostic report identified that `universal_grid_state_consistency.py` is using synchronous context manager syntax with async database connections, causing the error: "'aiosqlite.core.Connection' object does not support context manager protocol (missed __exit__ method) but it supports asynchronous context manager protocol. Did you mean to use 'async with'?"

This error occurs during every grid validation attempt and completely breaks the universal grid state consistency system.
</objective>

<context>
This is a trading bot v2 system with multiple cryptocurrency trading strategies and grid lifecycle management. The universal grid state consistency manager is responsible for maintaining synchronization between memory and database grid states, detecting orphaned grids, and automatically repairing grid state inconsistencies.

The system uses:
- SQLite database with aiosqlite for async operations
- Grid lifecycle manager for grid state management
- Multiple trading strategies that depend on consistent grid states
- FastAPI web server for monitoring and control

@universal_grid_state_consistency.py
@trading_bot_v2/database.py

Previous attempts to fix this issue have been incomplete - the context manager protocol mismatch persists and prevents the grid consistency system from functioning.
</context>

<requirements>
1. Fix all instances of synchronous context manager usage with async aiosqlite connections
2. Convert `with conn:` syntax to `async with conn:` throughout the file
3. Add `await` keywords to all database operations within async context blocks
4. Ensure all cursor operations use async patterns
5. Maintain all existing functionality while fixing the protocol issue
6. Test that grid state validation works without protocol errors
7. Verify that the universal grid consistency system can properly validate and repair grid states

The fix must handle:
- Database connections obtained via `self.db_manager.get_connection()`
- All SQL cursor operations
- Transaction management within async context
- Error handling and logging within async context blocks
- Method signatures that need to be async where database operations occur

Why this matters: The grid state consistency system is critical for preventing orphaned grid states and maintaining trading integrity. Without this working, the trading bot can experience grid state drift, memory-database synchronization failures, and orphaned grid conditions that compromise trading performance and risk management.
</requirements>

<implementation>
Thoroughly analyze the entire `universal_grid_state_consistency.py` file to identify ALL instances where synchronous context manager syntax is used with async database connections.

Search for patterns like:
- `with self.db_manager.get_connection() as conn:`
- `with conn:` (where conn is an aiosqlite connection)
- Any synchronous database operations that should be async

For each instance found:
1. Change `with` to `async with` 
2. Add `await` to all database operations within the block
3. Ensure any calling methods are properly marked as async
4. Verify error handling works with async patterns
5. Test that the change doesn't break existing logic flow

What to avoid:
- Do not change the overall logic or algorithms
- Do not remove existing error handling or logging
- Do not change database schema or queries
- Do not mix synchronous and async patterns in the same method

Why these constraints matter: The goal is to fix the protocol mismatch without changing the business logic. The grid state validation algorithms are correct; only the database access pattern needs updating for async compatibility.
</implementation>

<output>
Modify the file: `universal_grid_state_consistency.py`

Ensure all database context manager usage follows async patterns:

```python
# BEFORE (incorrect):
with self.db_manager.get_connection() as conn:
    cursor = conn.execute("SELECT * FROM grid_states")
    results = cursor.fetchall()

# AFTER (correct):
async with self.db_manager.get_connection() as conn:
    cursor = await conn.execute("SELECT * FROM grid_states")
    results = await cursor.fetchall()
```

Key changes required:
- Line 311: `with self.db_manager.get_connection() as conn:` → `async with self.db_manager.get_connection() as conn:`
- Line 771: `with conn:` → `async with conn:`
- Any other instances found in the file
- Add await keywords to all cursor operations
- Make methods async where needed for database operations

No changes to:
- SQL queries or database schema
- Business logic for grid validation
- Error handling patterns (just adapt to async)
- Logging statements (just ensure they work in async context)
</output>

<verification>
Before declaring complete, verify your work:

1. **Syntax Check**: Python file compiles without syntax errors
2. **Protocol Compatibility**: No more context manager protocol errors in logs
3. **Functionality Test**: Grid state validation attempts succeed without protocol errors
4. **Async Consistency**: All database operations use async patterns consistently
5. **Integration Test**: Universal grid consistency system can validate and repair grid states
6. **Error Log Review**: No more "aiosqlite.core.Connection' object does not support context manager protocol" errors

Test with a simple grid validation to confirm the fix works:
```python
# The universal grid consistency manager should be able to:
# - Get database grid states without protocol errors
# - Validate grid states without crashing
# - Repair orphaned grids successfully
# - Complete consistency checks without exceptions
```

</verification>

<success_criteria>
1. Zero AsyncSQLite context manager protocol errors in logs
2. Grid state validation functions execute successfully
3. Universal grid consistency system operates without protocol errors
4. All database operations use proper async patterns
5. Grid state detection and repair functionality working
6. No regression in existing grid management features
7. Server starts and runs without database protocol errors
</success_criteria>