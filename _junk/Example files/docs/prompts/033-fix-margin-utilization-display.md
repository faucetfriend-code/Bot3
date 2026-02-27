<objective>
Fix the margin utilization display in the Account Summary section. Instead of showing raw used_margin as a dollar amount, calculate and display it as a percentage: (used_margin / available_margin) * 100.

This will make the margin utilization more meaningful for users to understand their margin usage at a glance.
</objective>

<context>
The current API returns:
- "used_margin": 0.0 (raw dollar amount)
- "available_balance": 10018.51 (available margin)

The interface currently displays used_margin as: `$${safeToFixed(statusData.used_margin, 2)}`

We want to change this to show margin utilization percentage: `${safeToFixed(marginUtilizationPct, 1)}%`

Where marginUtilizationPct = (used_margin / available_margin) * 100
</context>

<requirements>
1. **API Changes**: Add a new field "margin_utilization_pct" to /api/status response that calculates (used_margin / available_margin) * 100
2. **Interface Changes**: Update the used_margin display element to show the percentage instead of dollar amount
3. **Handle edge cases**: 
   - When available_margin is 0, show 0% or handle gracefully
   - Ensure percentage is properly formatted (1 decimal place)
   - Maintain backward compatibility with existing used_margin field
4. **Update element ID**: The interface uses element ID 'usedMargin' - change this to show percentage
</requirements>

<implementation>
- Add margin_utilization_pct calculation in api_server.py /api/status endpoint
- Update trading_bot_interface.html to display percentage instead of dollar amount
- Handle division by zero when available_margin is 0
- Keep existing used_margin field for backward compatibility
</implementation>

<output>
Modify: ./api_server.py - Add margin_utilization_pct calculation
Modify: ./trading_bot_interface.html - Update used_margin display to show percentage

Save changes to both files.
</output>

<verification>
After implementation:
1. Test /api/status endpoint returns margin_utilization_pct field
2. Verify interface displays margin utilization as percentage (e.g., "0.0%")
3. Test with different margin values to ensure calculation is correct
4. Check that existing used_margin field is still available for backward compatibility
5. Ensure no division by zero errors when available_margin is 0
</verification>

<success_criteria>
- API returns "margin_utilization_pct" field with correct percentage calculation
- Interface displays margin utilization as "X.X%" instead of "$X.XX"
- Calculation handles edge cases properly (available_margin = 0)
- Backward compatibility maintained with existing used_margin field
- No JavaScript errors in interface
</success_criteria>