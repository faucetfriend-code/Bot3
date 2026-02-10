<objective>
Fix case-sensitive strategy name matching in RiskManager to prevent silent fallback to MEDIUM risk. This is QUALITY CONTROL ISSUE #3 - ensures correct risk profiles are applied.
</objective>

<context>
RiskManager strategy profile mapping uses lowercase keys, but signal.strategy.value returns PascalCase names like "MeanReversion", "MACrossover". This causes silent fallback to MEDIUM risk profile.

Reference: "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\research\quality control for the fixes.txt" - Issue #7

Current issue:
```python
# RiskManager expects lowercase
strategy_profiles = {
    'mean_reversion': RiskProfile.MEDIUM,
    'ma_crossover': RiskProfile.MEDIUM,
}

# But receives PascalCase
signal.strategy.value  # "MeanReversion", "MACrossover"
```

Result: Silent fallback to MEDIUM risk, incorrect risk application.
</context>

<requirements>
1. Normalize strategy names to lowercase before lookup
2. Ensure all strategy mappings use consistent casing
3. Add logging to detect when normalization occurs
4. Verify all strategies have correct risk profiles
5. Test with different strategy types
</requirements>

<implementation>
Update RiskManager.get_strategy_risk_profile():

```python
def get_strategy_risk_profile(self, strategy_type: str) -> str:
    """
    Get default risk profile for strategy type.
    
    Args:
        strategy_type: Strategy type name (any case)
        
    Returns:
        Risk profile name
    """
    # Normalize to lowercase for consistent lookup
    normalized_type = strategy_type.lower()
    
    if normalized_type != strategy_type:
        logger.debug(f"Normalized strategy type: '{strategy_type}' -> '{normalized_type}'")
    
    # Strategy-specific risk profiles (all lowercase keys)
    strategy_profiles = {
        'mean_reversion': RiskProfile.MEDIUM.value,
        'ma_crossover': RiskProfile.MEDIUM.value,
        'macrossover': RiskProfile.MEDIUM.value,  # Handle both formats
        'grid_trading': RiskProfile.LOW.value,
        'gridtrading': RiskProfile.LOW.value,     # Handle both formats
        'liquidation_capture': RiskProfile.HIGH.value,
        'liquidationcapture': RiskProfile.HIGH.value,  # Handle both formats
        'trend_following': RiskProfile.HIGH.value,
        'trendfollowing': RiskProfile.HIGH.value,  # Handle both formats
    }
    
    profile = strategy_profiles.get(normalized_type, RiskProfile.MEDIUM.value)
    
    if normalized_type not in strategy_profiles:
        logger.warning(f"Unknown strategy type '{strategy_type}', using MEDIUM risk profile")
    
    return profile
```

Also normalize in get_position_size():

```python
def get_position_size(self, signal: Any, account_balance: float, current_exposure: float) -> float:
    # ... existing code ...
    
    # Set risk profile on signal if not set
    if not hasattr(signal, 'risk_profile'):
        strategy_name = signal.strategy.value if hasattr(signal.strategy, 'value') else str(signal.strategy)
        signal.risk_profile = self.get_strategy_risk_profile(strategy_name)
    
    # ... rest of method ...
```
</implementation>

<output>
Fix case-sensitive strategy matching in RiskManager:
- Add lowercase normalization in get_strategy_risk_profile()
- Handle multiple naming formats (with/without underscores)
- Add logging for normalization and unknown strategies
- Update strategy profile mappings to be comprehensive
- Test with different strategy name formats

Ensure all strategies get correct risk profiles applied.
</output>

<verification>
After fix:
1. Test with different strategy name formats ("MeanReversion", "MA_Crossover", etc.)
2. Verify correct risk profiles are assigned
3. Check logging for normalization messages
4. Test unknown strategies fall back gracefully
5. Confirm risk calculations use correct profiles
</verification>

<success_criteria>
- Strategy names are normalized to lowercase
- All strategies get correct risk profiles
- No silent fallback to wrong risk levels
- Comprehensive strategy name format support
- Clear logging for debugging
</success_criteria>