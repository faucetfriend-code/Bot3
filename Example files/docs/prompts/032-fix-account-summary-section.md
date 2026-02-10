<objective>
Fix the Account Summary section of the trading bot interface by ensuring the /api/status endpoint returns the correct JSON structure that matches what the interface expects. Focus ONLY on this section - do not plan or implement changes for other sections.

The interface expects specific field names and data types for the Account Summary card. Test the current API response, show the user the actual JSON received, and allow them to choose alternative data sources if the current ones don't provide the required data.
</objective>

<context>
This is part of a systematic fix of the trading bot interface. The Account Summary section displays:
- Account Balance (from balance_history table)
- Total P&L (calculated from trades table)
- Win Rate (calculated from closed trades)
- Open Positions Count (from pacifica_positions table)

The interface calls GET /api/status and expects a JSON response with these fields:
- account_balance (number)
- total_pnl (number)
- win_rate (number, as percentage)
- open_positions (number)

Current implementation may have issues with:
- Win rate calculation requiring closed trades with exit_timestamp
- Account balance needing sync from Pacifica API
- Field name mismatches between database columns and interface expectations
</context>

<requirements>
1. Test the current /api/status endpoint and capture the exact JSON response
2. Show the user the actual JSON received from the API
3. Compare it against what the interface expects (see the HTML reference document)
4. Identify any missing fields, wrong field names, or incorrect data types
5. If current data sources are inadequate, suggest and implement alternatives:
   - Database queries with proper JOINs
   - Direct Pacifica API calls
   - WebSocket feed integration for real-time data
6. Ensure the response includes a "status" field indicating bot state ("running", "stopped", etc.)
7. Verify calculations are correct (win rate should be percentage of profitable trades)
</requirements>

<implementation>
- Read the current api_server.py implementation of the /api/status endpoint
- Test the endpoint with curl or direct API calls
- If database queries are insufficient, consider:
  - Adding JOIN queries between trades and positions tables
  - Implementing real-time balance fetching from Pacifica API
  - Using WebSocket connections for live status updates
- Maintain backward compatibility - don't break existing functionality
- Use proper error handling and logging
</implementation>

<output>
Modify the /api/status endpoint in api_server.py to return correct JSON structure.

Show the user:
1. Current API response JSON
2. Expected interface format
3. Any discrepancies found
4. Alternative data source options if current ones are inadequate

Save any code changes to: ./api_server.py
</output>

<verification>
After implementation:
1. Test the endpoint: curl http://localhost:8000/api/status | jq
2. Verify response contains all required fields with correct types
3. Check that calculations are mathematically correct
4. Ensure interface loads without errors when calling this endpoint
5. Confirm win rate is expressed as a percentage (not decimal)
</verification>

<success_criteria>
- /api/status returns valid JSON with all expected fields
- account_balance, total_pnl, win_rate, and open_positions are present and correctly typed
- win_rate is a percentage value (e.g., 62.5 for 62.5%)
- status field indicates current bot state
- Interface Account Summary section displays correct data without errors
- No existing functionality is broken
</success_criteria>