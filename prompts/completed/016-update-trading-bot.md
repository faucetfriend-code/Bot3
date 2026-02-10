<objective>
Update the trading_bot.py file to replace mock storage operations with real database calls, enabling persistent storage of trading data and positions.
This enables the bot to maintain state across restarts and track actual trading history, which is essential for risk management and position tracking in live trading.
</objective>

<context>
This is part of completing the trading bot for Pacifica.fi. The bot currently uses in-memory storage for positions and trades. The real database layer is designed in database.py with SQLite operations.
The bot needs to persist position data, trade history, and configuration to enable proper risk management and state recovery.
Examine these files for integration:
@trading_bot_v2/trading_bot.py
@trading_bot_v2/database.py
@trading_bot_v2/config.py
</context>

<requirements>
1. Import the real Database class instead of mock storage
2. Initialize database connection with proper configuration
3. Replace all mock storage method calls with real database operations:
   - Store/retrieve positions
   - Store/retrieve trade history
   - Store/retrieve bot configuration
4. Maintain existing bot logic and threading structure
5. Ensure data persistence across bot restarts
6. Handle database connection errors gracefully
</requirements>

<implementation>
- Replace `from .mock_storage import MockStorage` with `from .database import Database`
- Update initialization: `self.db = Database(db_path=config.DATABASE_PATH)`
- For each storage operation, replace mock calls with real database methods:
  - `self.storage.get_positions()` → `self.db.get_positions()`
  - `self.storage.save_position(pos)` → `self.db.save_position(pos)`
  - `self.storage.get_trades()` → `self.db.get_trades()`
  - `self.storage.save_trade(trade)` → `self.db.save_trade(trade)`
- Preserve all existing logic for position limits, risk management, and trading signals
- Add try/catch blocks around database operations to handle connection issues
- Ensure thread safety for concurrent access if needed
</implementation>

<output>
Modify the existing file:
- `./trading_bot_v2/trading_bot.py` - Update imports and replace all storage operations with database calls
</output>

<verification>
Before declaring complete:
1. Run `python -c "from trading_bot_v2.trading_bot import TradingBot; bot = TradingBot()"` to verify imports work
2. Check that database file is created when bot initializes
3. Manually test position saving/loading by creating a position and verifying it persists
4. Ensure bot can start and run trading loop without storage errors
</verification>

<success_criteria>
- Bot initializes successfully with real database
- Positions and trades persist across restarts
- No breaking changes to trading logic
- Proper error handling for database operations
- Database file created and populated correctly
</success_criteria>