<objective>
Fix error handling and status displays in the trading bot interface, specifically technical indicators returning errors, connection button showing "connection failed", and exchange health showing "unknown". This ensures users see accurate system status and can troubleshoot issues effectively.
</objective>

<context>
This is for a Solana perpetuals trading bot with real-time data requirements. Users need reliable status indicators to know when the system is functioning properly and when there are connection or data issues. The interface should gracefully handle errors and provide clear feedback.

@api_server.py - examine error handling in indicator and status endpoints
@trading_bot_interface.html - review error display logic and status indicators
@pacifica_client.py - check connection and health status functions
@market_data_collector.py - verify data collection error handling
@config.py - check connection configuration and timeouts
</context>

<requirements>
1. Fix technical indicators endpoint to return valid data instead of errors
2. Update connection status logic to accurately reflect API connectivity
3. Implement proper exchange health monitoring and display
4. Add comprehensive error handling for all data fetching operations
5. Ensure status indicators update in real-time and show meaningful messages
</requirements>

<implementation>
Thoroughly analyze error sources and implement robust error handling. Look for:
- Missing try/catch blocks around API calls
- Incorrect error propagation from backend to frontend
- Missing validation of data before display
- Improper connection status detection
- Exchange health checks that aren't implemented

Go beyond basic fixes - implement graceful degradation (show cached data when live data fails), meaningful error messages, and automatic retry logic for transient failures.

For status indicators, ensure they reflect actual system state rather than hardcoded values.
</implementation>

<output>
Modify the following files with relative paths:
- ./api_server.py - fix indicator endpoints and add proper error handling
- ./trading_bot_interface.html - update status display logic and error messages
- ./pacifica_client.py - implement connection status and health checks
- ./market_data_collector.py - add error handling for data collection (if needed)
</output>

<verification>
Before declaring complete:
1. Test technical indicators endpoint and verify it returns valid data
2. Check connection button status updates correctly based on actual connectivity
3. Verify exchange health shows real status instead of "unknown"
4. Test error scenarios (disconnect API, invalid data) to ensure graceful handling
5. Confirm status indicators update automatically without manual refresh
</verification>

<success_criteria>
- Technical indicators load without errors and display valid data
- Connection button shows accurate status ("connected" vs "connection failed")
- Exchange health displays meaningful status (not "unknown")
- All error states show helpful messages instead of generic failures
- Status indicators update in real-time based on actual system state
</success_criteria>