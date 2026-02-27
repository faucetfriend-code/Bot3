<objective>
Complete the database schema implementation by adding all missing tables, constraints, and indexes as specified in the Database Schema & Data Sources Representation.md. This will resolve the core issue of missing trades, positions, and Pacifica-specific tables that cause API failures.
</objective>

<context>
Diagnostics confirmed database schema issues with missing tables. The expected 15-table schema includes core trading tables (trades, positions) and Pacifica-specific tables (pacifica_positions, funding_payments, etc.).

Reference @docs/Database Schema & Data Sources Representation.md for complete table specifications. Current database.py has incomplete schema creation.
</context>

<requirements>
1. Review complete schema requirements from documentation
2. Identify all missing tables from the 15-table specification
3. Add comprehensive table creation with proper constraints
4. Include foreign key relationships and indexes
5. Add migration-safe creation (IF NOT EXISTS)
6. Test schema creation and data integrity

Implement the full schema thoroughly, ensuring all Pacifica integration tables are present with correct relationships and data types.
</requirements>

<implementation>
Use established database patterns:
- SQLite with proper foreign keys enabled
- Transaction-safe schema creation
- Performance indexes on frequently queried fields
- Proper data types and constraints

Explain WHY complete schema matters: Missing tables prevent data storage and retrieval, causing API endpoints to fail and the trading bot to be non-functional.
</implementation>

<output>
Complete database schema implementation:

1. **Update ./database.py** with full 15-table schema
2. **Add migration handling** for existing databases
3. **Create schema verification script**: ./scripts/verify_schema.py
4. **Update tests** in ./tests/test_database.py
</output>

<verification>
Before declaring complete:
1. Run `python -c "from database import init_database; init_database()"` and check for errors
2. Run `python scripts/verify_schema.py` and confirm all 15 tables exist
3. Run `python -m pytest tests/test_database.py -v`
4. Test API endpoints return proper responses (not errors)
</verification>

<success_criteria>
- All 15 database tables created successfully
- Foreign key constraints and indexes present
- Schema creation is migration-safe
- Database tests pass with full coverage
- API endpoints function without "no such table" errors
</success_criteria></content>
</xai:function_call name="task">
<parameter name="description">Execute database schema fix prompt