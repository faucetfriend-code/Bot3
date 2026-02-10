<objective>
Fix the remaining UI data loading issues where key sections are showing blank data or falling back to database/placeholders instead of live API data. The trading interface partially loads but positions, market data, funding rates, technical indicators, trade signals, and performance overview all need proper data integration.

This matters because the trading interface is the primary user interaction point, and without accurate live data, users cannot make informed trading decisions.
</objective>

<context>
This is for the FastAPI-based trading bot where the server loads successfully and basic endpoints work, but the web interface shows incomplete data. The UI is falling back to database data for positions and showing blank/placeholder content for market data, indicators, signals, and performance metrics.

Key files to examine:
@api_server.py - server endpoints that provide data to the UI
@pacifica_market_data.py - market data handler for indicators and charts
@trading_bot_interface.html - frontend interface that displays the data

Reference the python-sdk directory at "C:\Users\z_shi\Desktop\N8NPROJECTS\trade bot\python-sdk" for correct data structures and API responses.

The positions endpoint is falling back to database, and other data sources are not properly connected to the UI.
</context>

<requirements>
1. Fix positions data loading to use live API data instead of database fallback
2. Connect market data endpoints to provide real-time price and funding rate data
3. Implement technical indicators calculation and display with live data
4. Populate trade signals section with actual signal data instead of placeholders
5. Fix performance overview to properly calculate and display:
   - Initial balance
   - Realized P&L (closed positions)
   - Unrealized P&L (open positions)
   - Funding payments paid
6. Ensure all UI sections update with live data instead of showing blanks

The fixes should make the trading interface fully functional with accurate, real-time data.
</requirements>

<implementation>
Analyze the data flow from API endpoints to UI display:
- Positions: Currently falling back to database instead of using live API
- Market data: Endpoints exist but not connected to UI updates
- Indicators: Calculations may be working but not displayed in UI
- Signals: Placeholder data instead of real signal processing
- Performance: Logic needs to distinguish between different P&L components

Fix approach:
- Update positions endpoint to prioritize live API over database fallback
- Ensure market data handlers properly feed data to UI endpoints
- Connect indicator calculations to UI display components
- Implement signal data population from trading logic
- Enhance performance calculations with proper categorization
</implementation>

<constraints>
- Do not break existing database fallback mechanisms (keep as safety net)
- Maintain API rate limiting and error handling
- Ensure UI updates work in real-time without excessive API calls
- Keep performance calculations accurate and auditable
- Do not modify core trading logic, only data presentation
</constraints>

<output>
Modify file with relative paths:
- ./api_server.py - fix positions and market data endpoints, enhance performance calculations
- ./pacifica_market_data.py - ensure proper data flow to UI
- ./trading_bot_interface.html - update data binding for all sections

Document the specific data sources connected and calculations implemented.
</output>

<verification>
Before declaring complete, verify your work by:

1. Successfully start server and load trading interface
2. Check that positions display live data instead of database fallback
3. Verify market data, funding rates, and indicators show real values
4. Confirm trade signals section has actual data instead of placeholders
5. Test performance overview shows proper breakdown of balance components
6. Ensure all UI sections update with live data on refresh

Use browser developer tools to confirm data is loading from API endpoints.
</verification>

<success_criteria>
- Server starts and trading interface loads completely
- Positions display live API data instead of database fallback
- Market data, funding rates, and technical indicators show real values
- Trade signals section displays actual signal data
- Performance overview properly categorizes initial balance, realized P&L, unrealized P&L, and funding payments
- All UI sections show live data instead of blanks or placeholders
</success_criteria>