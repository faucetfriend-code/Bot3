<objective>
Fix the server runtime errors identified in the error logs, including rate limiting issues, missing attributes, and data availability problems.
</objective>

<context>
This is for the trading bot project to resolve runtime errors that are occurring during normal operation. The server error logs show several issues that need systematic resolution.

Reference the error details in:
- @research/server errors.txt (contains the specific errors and when they occurred)

Key issues identified:
- Rate limiting (429 errors) from Pacifica API causing JSON parsing failures
- Missing 'enable_grid_trading' attribute on StrategyManager
- Insufficient historical data for some markets (ADA, TRUMP only have 2 candles)
- InsecureRequestWarning for HTTPS requests (expected but could be suppressed)

Who will use this: The trading bot users and developers, to ensure stable operation without runtime crashes.
End goal: Server runs without the identified errors and handles edge cases gracefully.
</context>

<requirements>
1. Implement proper rate limiting handling with exponential backoff for 429 errors
2. Add missing 'enable_grid_trading' attribute to StrategyManager class
3. Handle cases where markets have insufficient historical data
4. Suppress expected HTTPS warnings for test environment
5. Add fallback mechanisms for missing timeframe data
6. Improve error handling for WebSocket and API failures
</requirements>

<implementation>
- Add rate limiting detection and retry logic with increasing delays
- Initialize missing attributes in StrategyManager constructor
- Add data validation before regime detection (minimum candle requirements)
- Use warnings.filterwarnings to suppress expected SSL warnings
- Implement graceful degradation when timeframe data is missing
- Add try/catch blocks around WebSocket operations
</implementation>

<output>
Modify files with relative paths to fix runtime errors:
- ./trading_bot_v2/strategy_manager.py - Add missing enable_grid_trading attribute
- ./Example files/core_logic/pacifica_client.py - Improve rate limiting and error handling
- ./trading_bot_v2/multi_timeframe_fetcher.py - Handle insufficient data gracefully
- ./trading_bot_v2/trading_bot.py - Suppress expected warnings
- ./trading_bot_v2/strategies/ - Update strategies to handle missing data
</output>

<verification>
Before declaring complete, verify:
- Server starts without the identified errors
- Rate limiting is handled gracefully with retries
- Missing attributes no longer cause crashes
- Insufficient data cases are handled without errors
- WebSocket connections remain stable
</verification>

<success_criteria>
- No runtime errors from the identified issues
- Server operates stably under normal conditions
- Rate limiting is handled appropriately
- Missing data scenarios don't crash the system
- All trading strategies can handle edge cases
</success_criteria>

---
Completed at: 2026-01-14T01:49:41.406Z
