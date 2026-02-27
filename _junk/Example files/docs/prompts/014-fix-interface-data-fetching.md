<objective>
Fix missing and incorrect data display in the trading bot interface by ensuring all database-backed data (Open Positions, Funding Rates, and related metrics) loads correctly from the API endpoints. This is critical for traders to make informed decisions and monitor their positions accurately.
</objective>

<context>
This is for a Solana perpetuals trading bot built with Python/FastAPI backend and HTML/JavaScript frontend. The interface should display real-time data from the database via API calls. Users are traders who need accurate position and funding data to manage their portfolios.

@api_server.py - examine API endpoints for positions, funding, and data retrieval
@database.py - check database connection and query patterns
@trading_bot_interface.html - review frontend data fetching logic
@models.py - verify data models match what's expected in the interface
</context>

<requirements>
1. Identify which API endpoints are failing to return data for Open Positions and Funding Rates
2. Fix database queries in api_server.py to ensure they return complete, accurate data
3. Update frontend JavaScript to properly handle API responses and display data
4. Ensure data refreshes automatically and handles connection states properly
5. Verify that all position-related metrics (size, entry price, current price, etc.) are correctly fetched
</requirements>

<implementation>
Thoroughly analyze the current API endpoints and database queries. Look for:
- Missing or incorrect SQL queries
- Improper data serialization in API responses
- Frontend JavaScript errors in data parsing
- Missing error handling for failed API calls

Go beyond basic fixes - ensure the data fetching is robust, handles edge cases, and provides meaningful fallback data when APIs are temporarily unavailable.

For database queries, use proper context managers and ensure queries are optimized for the data being displayed.
</implementation>

<output>
Modify the following files with relative paths:
- ./api_server.py - fix position and funding rate API endpoints
- ./trading_bot_interface.html - update JavaScript data fetching and display logic
- ./database.py - ensure database queries are correct (if needed)
</output>

<verification>
Before declaring complete:
1. Start the API server and verify endpoints return data: GET /api/positions, GET /api/funding
2. Load the interface and check that Open Positions and Funding Rates sections populate with data
3. Test data refresh functionality and ensure no console errors
4. Verify data matches what's in the database using direct queries
</verification>

<success_criteria>
- Open Positions section displays all current positions with accurate data
- Funding Rates section loads and shows current rates
- No JavaScript errors in browser console related to data fetching
- Data updates automatically without manual refresh
- API endpoints return 200 status with complete JSON data
</success_criteria>