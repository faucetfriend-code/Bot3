<objective>
Thoroughly analyze and classify database issues in the trading bot system, following a systematic diagnostic process to identify root causes of schema inconsistencies and data corruption. This classification will enable targeted fixes to restore database integrity and API functionality.

The end goal is to create a comprehensive issue classification report that clearly identifies all database problems, their impacts, and prioritized remediation steps. This matters because unresolved database issues prevent the trading bot from functioning properly, leading to empty API responses and interface failures.
</objective>

<context>
This is for a Python-based trading bot project using SQLite database with Pacifica.fi integration. The system includes API endpoints (/api/trades, /api/positions), frontend interface (trading_bot_interface.html), and data collection components.

Key files to examine:
@database.py - Database initialization and schema management
@docs/Database Schema & Data Sources Representation.md - Expected 15-table schema
@api_server.py - API endpoints that query the database
@trading_bot_interface.html - Frontend that displays database data
@tests/test_database.py - Existing database tests

The database should contain 15 tables including trades, positions, pacifica_positions, funding_payments, etc. Issues manifest as "no such table" errors and empty API responses.
</context>

<requirements>
Follow this systematic 4-step diagnostic process:

1. **Gather Context**: 
   - Review server logs for database errors
   - Examine database state using PRAGMA table_info and sample queries
   - Check recent trades/errors in journal.py and audit.py
   - Verify system status and data flow indicators

2. **Isolate Component**:
   - Test individual endpoints with curl/Postman simulations
   - Query database directly with sqlite3
   - Run bot components in isolation to identify failure points
   - Check frontend console/network logs for validation failures

3. **Trace Data Flow**:
   - Map data from Pacifica API through collection components to database
   - Identify where data insertion fails due to missing tables
   - Trace how empty database queries affect API responses
   - Analyze how missing data impacts frontend rendering

4. **Apply Fixes**:
   - Implement comprehensive schema creation for all 15 tables
   - Add proper foreign key constraints and indexes
   - Include migration handling for existing databases
   - Update related components to handle restored data flow

Go beyond basic fixes - thoroughly analyze the root cause (incomplete schema in database.py) and implement a complete, production-ready database schema with proper constraints, indexes, and error handling.
</requirements>

<implementation>
Use established project patterns:
- Pydantic BaseModel for data validation
- Loguru for structured logging
- Type hints for all function parameters and return values
- Google-style docstrings for public APIs
- Black formatting (88 chars), double quotes, pytest for testing

Avoid common pitfalls:
- Never create new database instances unnecessarily (use singleton pattern)
- Always use DATABASE_PATH from database.py for consistency
- Implement proper connection management with context managers
- Add comprehensive error handling for database operations

Explain WHY these patterns matter: Following project conventions ensures maintainability and prevents integration issues with existing components.
</implementation>

<output>
Create a comprehensive issue classification report and implement fixes:

1. **Classification Report**: Save analysis to `./diagnoses/database-issue-classification.md`
   - Document all identified issues with evidence
   - Map impact on each component (API, frontend, data flow)
   - Prioritize issues by severity and dependencies

2. **Database Schema Fix**: Update `./database.py` with complete 15-table schema
   - Add all missing tables (trades, positions, pacifica_positions, funding_payments, etc.)
   - Include foreign key constraints and performance indexes
   - Add migration-safe creation with "IF NOT EXISTS"

3. **Testing Updates**: Enhance `./tests/test_database.py`
   - Add schema validation tests
   - Include foreign key constraint tests
   - Add integration tests for API endpoints

4. **Documentation Updates**: Update relevant docs
   - Add schema creation notes to README.md
   - Update docstrings in database.py
   - Document the diagnostic process for future reference
</output>

<verification>
Before declaring complete, verify your work:

1. **Schema Verification**: Run `python -c "from database import init_database; init_database(); import sqlite3; conn = sqlite3.connect('data/trading_bot.db'); cursor = conn.cursor(); cursor.execute('SELECT name FROM sqlite_master WHERE type=\"table\"'); print([row[0] for row in cursor.fetchall()]); conn.close()"` and confirm all 15 tables exist

2. **API Testing**: Start api_server.py and verify /api/trades and /api/positions return proper JSON structures (even if empty) instead of errors

3. **Frontend Testing**: Load trading_bot_interface.html and confirm it no longer shows skeleton loading forever or "No data" errors

4. **Test Suite**: Run `pytest tests/test_database.py` and ensure all new tests pass

5. **Migration Safety**: Test on existing database - confirm no data loss and schema upgrades work
</verification>

<success_criteria>
- All 15 database tables exist and are properly structured
- API endpoints return valid JSON responses without "no such table" errors
- Frontend displays data correctly (or appropriate empty states)
- Database tests pass with 100% coverage for schema functionality
- Migration process works safely on existing databases
- Issue classification report provides clear root cause analysis and remediation roadmap
</success_criteria></content>
</xai:function_call">---

**Prompt created successfully!**

✓ Saved prompt to ./prompts/037-database-issue-classification.md

What's next?

1. Run prompt now
2. Review/edit prompt first  
3. Save for later
4. Other

Choose (1-4): _