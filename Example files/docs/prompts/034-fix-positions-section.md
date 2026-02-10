<objective>
Fix the Positions Section of the trading bot interface by ensuring the /api/positions endpoint returns the correct JSON structure that matches what the interface expects. Focus ONLY on this section - do not plan or implement changes for other sections.

The interface expects positions data with specific field names, nested funding objects, and proper JOIN queries to combine position data with funding payment information.
</objective>

<context>
This is part of a systematic fix of the trading bot interface. The Positions Section displays:
- Open positions table with asset, side, quantity, entry_price, current_price, pnl, leverage
- Funding information (total_paid, avg_hourly_rate, daily_est) nested within each position
- Source attribution (pacifica_api vs database)

The interface calls GET /api/positions and expects a JSON response with positions array where each position includes a nested funding object. This requires JOIN queries between pacifica_positions and funding_payments tables.
</context>

<requirements>
1. **API Endpoint**: Ensure /api/positions returns proper JSON structure
2. **Database JOIN**: Implement JOIN query between pacifica_positions and funding_payments tables
3. **Nested Funding Object**: Each position must have funding: {total_paid, avg_hourly_rate, daily_est}
4. **Field Mapping**: Ensure correct field names (asset vs symbol, quantity vs size, etc.)
5. **Live Price Integration**: Fetch current_price from Pacifica API in real-time
6. **PNL Calculation**: Calculate unrealized_pnl from (current_price - entry_price) × quantity × side_multiplier
7. **Source Attribution**: Include "source" field indicating data origin
8. **Error Handling**: Graceful fallback when live data unavailable
</requirements>

<implementation>
- Read the current /api/positions endpoint implementation
- Implement proper JOIN query: pacifica_positions LEFT JOIN funding_payments
- Add nested funding object calculation (SUM(amount_paid), AVG(funding_rate), etc.)
- Integrate live price fetching from Pacifica API for current_price
- Calculate unrealized_pnl using live prices
- Ensure field name mapping (symbol → asset, size → quantity)
- Add source attribution and error handling
</implementation>

<output>
Modify: ./api_server.py - Update /api/positions endpoint with proper JOIN queries and nested funding objects
Save changes to: ./api_server.py
</output>

<verification>
After implementation:
1. Test /api/positions endpoint returns valid JSON with positions array
2. Verify each position has nested funding object with total_paid, avg_hourly_rate, daily_est
3. Check that JOIN query works (positions with funding data show non-zero values)
4. Ensure current_price is fetched live from Pacifica API
5. Verify pnl calculations are correct
6. Confirm interface Positions Section displays data without errors
7. Test fallback behavior when live data unavailable
</verification>

<success_criteria>
- /api/positions returns positions array with nested funding objects
- JOIN query successfully combines pacifica_positions and funding_payments data
- Live current_price fetching works from Pacifica API
- Field names match interface expectations (asset, quantity, etc.)
- P&L calculations are mathematically correct
- Interface Positions Section displays complete position data
- Source attribution indicates data origin
- No database query errors or missing data
</success_criteria>