<objective>
Fix duplicate/conflicting risk methods by removing legacy versions and ensuring only RiskManager-backed methods remain. This is QUALITY CONTROL ISSUE #1 - critical for maintainability.
</objective>

<context>
Multiple versions of risk methods exist, creating confusion and potential bugs. Legacy methods like the old _validate_position_size(symbol, quantity) still exist alongside new RiskManager-backed versions.

Reference: "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\research\quality control for the fixes.txt" - Issue #5

Current state:
- Legacy _validate_position_size(symbol, quantity) - uses leverage logic directly
- New _validate_position_size(quantity) - uses RiskManager
- Risk of future developers calling wrong method

The bot must have a single source of truth for risk management.
</context>

<requirements>
1. Identify all duplicate/conflicting risk methods in trading_bot.py
2. Remove legacy versions that bypass RiskManager
3. Ensure all risk logic goes through RiskManager exclusively
4. Add clear documentation warnings against adding direct risk logic
5. Verify no remaining references to removed methods
</requirements>

<implementation>
Search trading_bot.py for duplicate methods:

1. **Remove legacy _validate_position_size(symbol, quantity)** that uses leverage logic directly
2. **Keep only RiskManager-backed _validate_position_size(quantity)**
3. **Remove any duplicate _calculate_position_size methods**
4. **Add documentation warnings:**

```python
# RISK MANAGEMENT WARNING
# ===============================
# DO NOT ADD RISK LOGIC HERE
# All risk calculations must go through RiskManager
# Use: self.risk_manager.get_position_size()
# Use: self.risk_manager.validate_position_size()
# ===============================
```

Update all callers to use the correct method signatures.
</implementation>

<output>
Clean up trading_bot.py by removing duplicate risk methods:
- Delete legacy _validate_position_size(symbol, quantity) method
- Delete any duplicate _calculate_position_size methods
- Add risk management warnings in code comments
- Update all method calls to use correct signatures

Ensure RiskManager is the single source of truth for all risk decisions.
</output>

<verification>
After cleanup:
1. Search codebase for any remaining calls to removed methods
2. Verify all risk logic flows through RiskManager
3. Test that position sizing and validation still work correctly
4. Confirm no runtime errors from missing methods
</verification>

<success_criteria>
- No duplicate risk methods exist
- All risk logic flows through RiskManager
- Clear documentation warnings prevent future violations
- Position sizing and validation work correctly
- Code is maintainable with single source of truth
</success_criteria>