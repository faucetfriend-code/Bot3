<objective>
Implement real SQLite CRUD operations in database.py, replacing all mock methods with actual database interactions for storing positions, trades, and configuration.
This creates the persistent data layer needed for the trading bot to maintain state and history, enabling proper risk management and audit trails.
</objective>

<context>
The database layer is designed to use SQLite for simplicity and zero-configuration deployment. The schema is already defined based on Pacifica API data structures.
Current database.py contains mock implementations that return hardcoded data. Need to implement real SQLite operations.
Examine these files:
@trading_bot_v2/database.py
@trading_bot_v2/config.py
Reference the schema design from project documentation.
</context>

<requirements>
1. Implement SQLite connection and table creation
2. Replace all mock methods with real database operations:
   - save_position(), get_positions()
   - save_trade(), get_trades()
   - save_config(), get_config()
3. Ensure proper data serialization/deserialization (JSON for complex objects)
4. Implement proper error handling for database operations
5. Create tables on first run if they don't exist
6. Maintain data integrity and handle concurrent access
</requirements>

<implementation>
- Use sqlite3 module for database operations
- Create tables in __init__ or separate init method:
  ```sql
  CREATE TABLE IF NOT EXISTS positions (id TEXT PRIMARY KEY, data TEXT)
  CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, data TEXT)
  CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)
  ```
- For save methods: INSERT OR REPLACE with JSON serialization
- For get methods: SELECT with JSON deserialization
- Use context managers for connection handling
- Add proper indexing for performance if needed
- Handle SQLite-specific errors (constraint violations, disk full, etc.)
</implementation>

<output>
Modify the existing file:
- `./trading_bot_v2/database.py` - Replace all mock implementations with real SQLite operations
</output>

<verification>
Before declaring complete:
1. Run `python -c "from trading_bot_v2.database import Database; db = Database(':memory:'); db.save_position({'id': 'test', 'symbol': 'BTC', 'size': 1.0})"` to test basic operations
2. Verify data persists by saving and retrieving positions/trades
3. Check that tables are created automatically
4. Test error handling with invalid data or connection issues
</verification>

<success_criteria>
- All database methods work with real SQLite operations
- Data persists correctly across connections
- Tables created automatically on first use
- Proper error handling for database issues
- No data corruption or integrity issues
- Methods return data in expected formats
</success_criteria>