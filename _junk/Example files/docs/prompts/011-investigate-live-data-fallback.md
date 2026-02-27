<objective>
Investigate why the trading bot server is repeatedly falling back to database data instead of fetching live API data from Pacifica, despite successful API calls and loaded credentials.

The server logs show "📊 No live API data available - using database fallback" every few seconds, indicating the live data collection system is not working properly. This prevents real-time trading data from being available to the bot and interface.
</objective>

<context>
This is for the trading bot system that should collect live market data from Pacifica DEX API. The server logs show:

- All API endpoints return 200 OK status
- Pacifica credentials load successfully
- Agent wallet is properly configured
- But market data requests trigger database fallback instead of live data

The system has extensive database-stored historical data but is not fetching current live data, which breaks real-time trading functionality.
</context>

<research>
Thoroughly investigate the data collection pipeline by:
1. Analyzing the market data collection logic in api_server.py
2. Examining how live API calls are made vs database fallbacks
3. Checking error handling in Pacifica API client code
4. Reviewing authentication and connection setup
5. Testing actual API connectivity to Pacifica endpoints
6. Examining rate limiting or throttling logic

Consider multiple potential causes:
- API authentication issues despite credential loading
- Network connectivity problems to Pacifica
- Rate limiting or API quota exceeded
- Incorrect API endpoint URLs or parameters
- Exception handling masking real errors
- Data validation rejecting valid API responses
</research>

<requirements>
1. Trace the exact code path that triggers the database fallback message
2. Test actual connectivity to Pacifica API endpoints
3. Verify authentication and authorization with live API
4. Check for error conditions that cause fallback behavior
5. Identify any configuration issues preventing live data collection
6. Determine if this is expected behavior or a bug
</requirements>

<implementation>
For maximum efficiency, use parallel tool calls when testing multiple API endpoints or examining different code sections.

When investigating, run actual API tests: `curl -v https://api.pacifica.io/markets` to verify external connectivity.

Go beyond basic logging - examine the actual API response handling and data validation logic that determines "live data availability".

Explain WHY live data matters: "Database fallback prevents real-time trading decisions, so understanding why live data isn't available is critical for system functionality"
</implementation>

<output>
Create comprehensive investigation report: ./diagnoses/live-data-fallback-analysis.md
- Document the exact fallback trigger conditions
- Include API connectivity test results
- Detail authentication verification findings
- Explain the root cause and any fixes applied
- Provide recommendations for ensuring live data availability
</output>

<verification>
Before declaring complete, verify:
- API connectivity to Pacifica endpoints works externally
- Authentication credentials are valid for live API calls
- No rate limiting or quota issues are active
- Error handling doesn't mask actual API availability
- Configuration allows live data collection

Test by temporarily disabling database fallback to see actual API errors.
</verification>

<success_criteria>
- Root cause of database fallback behavior identified
- Clear explanation of why live API data is unavailable
- API connectivity and authentication verified
- Investigation report documents findings and recommendations
- System either fixed to use live data or clearly explained why fallback is necessary
</success_criteria>