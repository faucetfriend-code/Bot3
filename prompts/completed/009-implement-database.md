<objective>
Implement the complete SQLite database for Phase 3.5 of the trading bot v2 system, replacing mock data with persistent storage that perfectly matches the API response structures. This implementation is critical for enabling real-time data persistence, accurate trade tracking, and reliable bot operation without data loss.
</objective>

<context>
This is for the Unity Oracle Aggregator trading bot project, specifically Phase 3.5: Database Configuration & Testing. The system uses Python with FastAPI and SQLite for data persistence. The database must exactly follow the schema designed in the database structure plan to ensure seamless integration with existing API endpoints.

Who will use this: The trading bot system for persistent data storage and retrieval.
What it's for: Replacing mock data in API endpoints with real database operations for production-ready functionality.
</context>

<requirements>
Implement a complete Database class in database.py with the following capabilities:
1. Database initialization with table creation using the exact schema from database-structure-plan.md
2. Full CRUD operations for all tables (positions, trades, markets, market_data_history)
3. Thread-safe operations with proper connection management and error handling
4. Data validation and type checking to match API response structures
5. Efficient querying with appropriate indexing for real-time performance
6. Migration support from mock data to database storage
7. Comprehensive error handling and logging for database operations

Be explicit about data types, constraints, and relationships as defined in the plan.
</requirements>

<implementation>
Follow these specific implementation patterns:
- Use sqlite3 with context managers for connection handling
- Implement proper transaction management for data integrity
- Include comprehensive docstrings and type hints for all methods
- Handle SQLite-specific constraints and error codes appropriately
- Ensure all database operations are non-blocking and thread-safe

Avoid common pitfalls:
- Never use string formatting for SQL queries - use parameterized queries to prevent SQL injection
- Always close connections properly to avoid resource leaks
- Validate data before insertion to maintain referential integrity
- Implement proper error recovery for database corruption or lock conflicts
</implementation>

<output>
Create/modify files with relative paths:
- `./trading_bot_v2/database.py` - Complete Database class implementation with all required methods and table operations
</output>

<verification>
Before declaring complete, verify your work:
- All tables create successfully with the exact schema from the plan
- CRUD operations work correctly for all data types and relationships
- Thread safety is maintained under concurrent access
- Data validation prevents invalid entries that would break API responses
- Performance is adequate for real-time trading operations
- Error handling covers common database failure scenarios
</verification>

<success_criteria>
- Database class implements all tables and operations from database-structure-plan.md exactly
- All CRUD methods function correctly with proper error handling
- Thread-safe operations verified through testing
- Data integrity maintained with constraints and validation
- Seamless integration ready for API endpoint updates in Phase 3.5
- Comprehensive docstrings and type hints provided
</success_criteria></content>
<parameter name="filePath">./prompts/009-implement-database.md