<objective>
Fix incorrect calculations in the trading bot interface, specifically Total Unrealized P&L and Performance Overview that incorrectly counts the initial 10k deposit as profits. This ensures traders see accurate financial metrics for proper decision-making.
</objective>

<context>
This is for a Solana perpetuals trading bot with Python backend calculations and HTML/JavaScript frontend display. Calculations must accurately reflect trading performance without including initial deposits as profits. Users are traders who rely on precise P&L and performance metrics.

@api_server.py - examine P&L calculation endpoints and performance overview logic
@models.py - check data models for balance and performance calculations
@trading_bot_interface.html - review frontend calculation display and JavaScript math
@balance_manager.py - verify balance calculation functions
@risk.py - check risk and performance calculation utilities
</context>

<requirements>
1. Identify where Total Unrealized P&L is calculated and ensure it only includes trading profits/losses
2. Fix Performance Overview to exclude initial deposit from profit calculations
3. Verify all P&L calculations use correct formulas (entry price vs current price, position size, leverage)
4. Update frontend to properly display calculated values from API
5. Ensure calculations handle both long and short positions correctly
</requirements>

<implementation>
Thoroughly analyze the calculation logic in both backend and frontend. Look for:
- Incorrect inclusion of initial deposits in profit calculations
- Wrong formulas for unrealized P&L (should be (current_price - entry_price) * position_size * leverage)
- Missing consideration of position direction (long vs short)
- Frontend JavaScript performing calculations instead of using API values

Go beyond basic fixes - implement robust calculation validation and ensure all edge cases are handled (zero positions, negative P&L, different leverage levels).

For performance overview, ensure it tracks realized profits separately from initial capital.
</implementation>

<output>
Modify the following files with relative paths:
- ./api_server.py - fix P&L and performance calculation endpoints
- ./trading_bot_interface.html - update display logic for calculated values
- ./balance_manager.py - correct balance and P&L calculation functions (if needed)
- ./risk.py - fix performance calculation utilities (if needed)
</output>

<verification>
Before declaring complete:
1. Test API endpoints for P&L calculations and verify they return correct values
2. Load interface and check that Total Unrealized P&L shows accurate trading profits only
3. Verify Performance Overview excludes initial 10k deposit from profit metrics
4. Test with sample positions (long/short) to ensure calculations work in all scenarios
5. Compare calculated values with manual calculations using database data
</verification>

<success_criteria>
- Total Unrealized P&L accurately reflects trading profits/losses only
- Performance Overview shows profits separate from initial capital
- All P&L calculations match expected formulas for perpetual futures
- No inclusion of initial deposits in profit metrics
- Calculations work correctly for both long and short positions
</success_criteria>