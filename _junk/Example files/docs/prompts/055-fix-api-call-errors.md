<objective>
Fix API call errors in the trading bot that are causing it to fall back to mock data instead of using live market data. The bot is making incorrect API requests with missing required parameters and failing to handle error responses properly, resulting in JSON parsing errors and degraded functionality.

This matters because the trading bot relies on accurate real-time market data for indicators, charts, and trading decisions, and the current fallback to mock data compromises its effectiveness.
</objective>

<context>
This is for the FastAPI-based trading bot that integrates with Pacifica.fi API for market data. The server is successfully making some API calls but failing on others due to missing required parameters and improper error handling.

Key files to examine:
@pacifica_client.py - API client implementation with request/response handling
@pacifica_market_data.py - Market data handler making API calls for candles and indicators
@api_server.py - Server endpoints that call market data functions

Reference the python-sdk directory at "G:\ai-workspace\trade bot\python-sdk" for correct API usage patterns and parameter requirements.

The errors show missing 'symbol' parameters in API calls and JSON parsing failures when error responses aren't handled correctly.
</context>

<requirements>
1. Identify all API endpoints with missing required parameters (especially 'symbol' field)
2. Fix parameter passing to include all required fields for each endpoint
3. Improve error response handling to prevent JSON parsing errors
4. Ensure API calls return valid data instead of falling back to mock data
5. Maintain backward compatibility with existing functionality
6. Fix both direct API calls and calls through the market data handler

The fixes should ensure the trading bot can access live market data for indicators, charts, and trading operations.
</requirements>

<implementation>
Analyze the failing API calls:
- Positions endpoint: Requires 'symbol' parameter but none provided
- Kline/candles endpoint: Missing required parameters causing 400 errors
- Error responses: Not properly handled, leading to JSON parsing failures

Fix approach:
- Review Pacifica API documentation for required parameters per endpoint
- Update method calls to include all required parameters
- Add proper error response parsing and handling
- Ensure consistent parameter passing across all API calls
- Test with real API responses to verify fixes
</implementation>

<constraints>
- Do not break existing API authentication or connection logic
- Maintain the fallback to mock data as a safety mechanism (only when API truly fails)
- Keep API rate limiting and error handling intact
- Ensure fixes work in both testnet and mainnet environments
- Do not modify core trading logic, only API call mechanics
</constraints>

<output>
Modify file with relative paths:
- ./pacifica_client.py - fix parameter passing and error handling
- ./pacifica_market_data.py - update API calls with correct parameters
- ./api_server.py - ensure proper parameter passing to market data functions

Document the specific API endpoints fixed and parameters added.
</output>

<verification>
Before declaring complete, verify your work by:

1. Successfully start the server without API parameter errors
2. Test API calls return valid responses (check logs for 200 status codes)
3. Verify indicators and chart endpoints use live data instead of mock data
4. Ensure no more "missing field" or JSON parsing errors
5. Confirm trading bot can access real market data for operations

Use server logs to confirm successful API calls and live data usage.
</verification>

<success_criteria>
- Server starts without API parameter errors
- All API calls return valid JSON responses (200 status codes)
- No more "Query deserialize error: missing field `symbol`" errors
- No more "Network error: Expecting value: line 1 column 1 (char 0)" errors
- Indicators and chart endpoints use live data instead of mock data
- Trading bot has access to real-time market data for all operations
</success_criteria>