<objective>
Investigate and fix the SQLite database locking errors that occur when syncing positions to the database, preventing the trading bot from properly storing live position data.

The server logs show repeated "database is locked" errors during position synchronization, which blocks the system from updating position data in real-time. This prevents accurate portfolio tracking and trading decisions.
</objective>

<context>
This is for the trading bot system that retrieves live positions from Pacifica API but fails to sync them to the local SQLite database due to locking issues. The system shows:

- Successful retrieval of live positions from Pacifica API
- Repeated "Failed to sync positions to database: database is locked" errors
- Database operations work for reads but fail for writes during concurrent access

The SQLite database is being accessed concurrently by multiple threads/processes, causing write operations to fail with locking errors.
</context>

<research>
Thoroughly investigate the database locking by:
1. Analyzing the position sync code in api_server.py
2. Examining database connection patterns and pooling
3. Checking for long-running transactions or uncommitted changes
4. Reviewing concurrent access patterns from multiple threads
5. Testing database file permissions and locking status

Consider multiple potential causes:
- Multiple database connections without proper pooling
- Long-running read transactions blocking writes
- Concurrent write operations from different threads
- Improper transaction handling and commits
- Database file corruption or permission issues
</research>

<requirements>
1. Identify the exact cause of database locking during position sync
2. Implement proper database connection pooling and management
3. Fix transaction handling to prevent long-running locks
4. Ensure thread-safe database operations
5. Add proper error handling and retry logic for locking scenarios
</requirements>

<implementation>
For maximum efficiency, use parallel tool calls when examining database operations and connection patterns.

When fixing locking issues, implement proper SQLite practices:
- Use connection pooling with SQLAlchemy or similar
- Ensure transactions are short and properly committed
- Add retry logic with exponential backoff for locking errors
- Use WAL mode for better concurrent access

Explain WHY proper database handling matters: "Database locking prevents data consistency and can cause the entire trading system to fail during critical operations"
</implementation>

<output>
Modify database handling code to fix locking issues:
- Update database connection management in database.py
- Fix position sync logic in api_server.py
- Add proper transaction handling and connection pooling

Create database locking analysis report: ./diagnoses/database-locking-fix.md
- Document the locking causes and fixes applied
- Include before/after performance metrics
</output>

<verification>
Before declaring complete, verify:
- Position sync operations complete without locking errors
- Database writes succeed during concurrent operations
- No "database is locked" errors in logs during normal operation
- Multiple API calls can sync positions simultaneously
- Database integrity maintained during concurrent access

Test by triggering multiple position sync operations simultaneously.
</verification>

<success_criteria>
- No database locking errors during position synchronization
- Position data successfully stored in database from API
- Concurrent database operations work without conflicts
- System handles high-frequency position updates reliably
- Database performance maintained under load
</success_criteria>