<objective>
Fix the database connection context manager errors and missing imports that are causing repeated failures in balance history, signals, funding data, and kline chart endpoints.

The server logs show "'_GeneratorContextManager' object has no attribute 'cursor'" errors and "cannot import name 'get_database_manager' from 'database'" failures, preventing proper database operations and breaking multiple API endpoints.
</objective>

<context>
This is for the trading bot system where database operations are failing due to incorrect usage of database connection context managers. The code is trying to access .cursor on context manager objects instead of the actual database connections, and missing imports are preventing kline data retrieval.

The errors are occurring in:
- Balance history fetching
- Signals generation
- Funding data retrieval
- Kline chart data
- Position synchronization

These failures prevent the interface from displaying complete trading data.
</context>

<research>
Thoroughly investigate the database connection usage by:
1. Analyzing how database connections are obtained and used in api_server.py
2. Examining the database.py module for proper connection management
3. Checking context manager usage patterns across the codebase
4. Reviewing import statements for missing database functions
5. Testing database connection lifecycle and cleanup

Consider multiple potential causes:
- Incorrect context manager unwrapping (using `with conn:` instead of proper connection access)
- Missing database manager functions
- Improper async/await patterns with database operations
- Connection pooling issues causing context manager confusion
</research>

<requirements>
1. Fix all "'_GeneratorContextManager' object has no attribute 'cursor'" errors
2. Add missing 'get_database_manager' import from database module
3. Correct database connection context manager usage throughout api_server.py
4. Ensure proper connection cleanup and resource management
5. Resolve any remaining database locking issues
</requirements>

<implementation>
For maximum efficiency, use parallel tool calls when examining multiple database operation functions.

When fixing context manager issues, ensure proper patterns:
- Use `with get_db_connection() as conn:` for synchronous operations
- Use `async with get_db_connection() as conn:` for async operations
- Access connection.cursor() correctly within context blocks

Explain WHY proper database handling matters: "Incorrect context manager usage can cause connection leaks and prevent proper transaction management, leading to database corruption and performance issues"
</implementation>

<output>
Modify database handling code to fix connection errors:
- Update api_server.py with correct database connection usage
- Add missing imports to database.py or api_server.py
- Fix context manager unwrapping in all affected functions

Create database connection fix report: ./diagnoses/database-connection-errors-fixed.md
- Document all errors found and fixes applied
- Include before/after code examples
</output>

<verification>
Before declaring complete, verify:
- Balance history endpoint returns data without errors
- Signals endpoint works without cursor attribute errors
- Funding data can be fetched successfully
- Kline chart data loads without import errors
- Position sync completes without database locking
- All database operations use proper connection management

Test by calling all previously failing API endpoints.
</verification>

<success_criteria>
- No "'_GeneratorContextManager' object has no attribute 'cursor'" errors
- All database imports resolve correctly
- Balance history, signals, funding, and kline endpoints work properly
- Database connections are managed correctly with proper cleanup
- System handles concurrent database operations without locking
</success_criteria>