<objective>
Fix RiskManager position sizing to properly handle notional vs quantity calculations. This is QUALITY CONTROL ISSUE #2 - prevents sizing drift with different contract sizes.
</objective>

<context>
RiskManager.get_position_size() returns notional size (dollar amount), but the bot treats it as quantity/contracts. This causes incorrect position sizing when instruments have different contract sizes or prices.

Reference: "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\research\quality control for the fixes.txt" - Issue #6

Current issue:
```python
# RiskManager returns notional size
position_size = adjusted_risk / stop_distance_pct  # This is dollars

# But bot uses it as quantity
order = self.client.place_order(symbol, side, position_size, 'market')  # Wrong!
```

This will cause position sizing errors, especially with different assets.
</context>

<requirements>
1. Fix RiskManager to return proper quantity, not notional size
2. Use entry_price to convert notional to quantity: quantity = notional / entry_price
3. Ensure consistent sizing across all assets
4. Handle edge cases (zero price, invalid calculations)
5. Maintain risk percentage accuracy
</requirements>

<implementation>
Update RiskManager.get_position_size():

```python
def get_position_size(self, signal: Any, account_balance: float, current_exposure: float) -> float:
    # ... existing risk calculation ...
    
    # Calculate notional size based on risk
    position_notional = adjusted_risk / stop_distance_pct
    
    # Convert to quantity using entry price
    if signal.entry_price <= 0:
        logger.warning(f"Invalid entry price for {signal.asset}: {signal.entry_price}")
        return 1.0  # Minimum safe quantity
    
    quantity = position_notional / signal.entry_price
    
    # Apply exposure limits (convert to notional for comparison)
    max_exposure = account_balance * self.max_portfolio_exposure_pct
    available_exposure = max_exposure - current_exposure
    max_quantity_by_exposure = available_exposure / signal.entry_price
    quantity = min(quantity, max_quantity_by_exposure)
    
    # Ensure minimum position size
    quantity = max(quantity, 1.0)
    
    logger.debug(f"Position sizing: notional=${position_notional:.2f}, quantity={quantity:.4f} @ ${signal.entry_price:.2f}")
    
    return quantity
```

Update method name for clarity:
```python
def get_position_quantity(self, signal: Any, account_balance: float, current_exposure: float) -> float:
    # ... implementation ...
```

Update TradingBot to use the corrected method:
```python
# Old: position_size = self.risk_manager.get_position_size(...)
# New: quantity = self.risk_manager.get_position_quantity(...)
quantity = self.risk_manager.get_position_quantity(signal, account_balance, current_exposure)
```
</implementation>

<output>
Fix RiskManager position sizing calculation:
- Update get_position_size() to return quantity, not notional
- Add proper conversion: quantity = notional / entry_price
- Handle edge cases (zero price, invalid calculations)
- Update TradingBot calls to use corrected method
- Ensure consistent sizing across different assets

Test with different assets to verify correct quantity calculations.
</output>

<verification>
After fix:
1. Test position sizing with different entry prices
2. Verify quantities are appropriate for each asset
3. Check that risk percentages are maintained accurately
4. Test edge cases (very high/low prices)
5. Confirm exposure limits work correctly
</verification>

<success_criteria>
- RiskManager returns quantity, not notional size
- Position sizing is consistent across different assets
- Risk percentages are maintained accurately
- Exposure limits work correctly
- No sizing drift with different contract sizes
</success_criteria>