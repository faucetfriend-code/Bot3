<objective>
Clean up trend following strategy references since it's not implemented. This is PRIORITY #6 from the trade management updates.
</objective>

<context>
Market regime detection enables TrendFollowing strategy, but no implementation exists. This causes confusion and potential runtime errors.

Reference: "G:\ai-workspace\Bot 3\research\trade managemet updates.txt" - Section 6

Current code references trend following but it's not implemented:
```python
MarketRegime.TRENDING_STRONG: ["TrendFollowing", "MACrossover"]
```

This should be disabled until properly implemented.
</context>

<requirements>
1. Remove or disable trend following strategy references
2. Update market regime detection to not include trend following
3. Ensure no runtime errors from missing implementation
4. Add clear documentation about what's not implemented
</requirements>

<implementation>
Update market_regime.py to remove trend following:

```python
def get_active_strategies(self, regime: MarketRegime) -> List[str]:
    """
    Get strategies active for current regime.
    """
    
    strategy_map = {
        MarketRegime.TRENDING_STRONG: ["MACrossover"],  # Removed TrendFollowing
        MarketRegime.TRENDING_MODERATE: ["MACrossover"],
        MarketRegime.RANGING_VOLATILE: ["GridTrading"],
        MarketRegime.RANGING_CALM: ["MeanReversion"],
        MarketRegime.INDECISIVE: ["LiquidationCapture"],
    }
    
    return strategy_map.get(regime, [])
```

Update strategy_manager.py to disable trend following:

```python
def __init__(self,
             # ...
             enable_trend_following: bool = False):  # Keep False until implemented
             
    # Remove trend following initialization
    if enable_trend_following:
        logger.warning("Trend Following strategy not yet implemented - skipping")
        # Don't try to import or initialize
```

Add documentation comments:

```python
# TODO: Implement TrendFollowing strategy
# This strategy should:
# - Use ADX > 25 for trend confirmation
# - Implement moving average crossovers for entry
# - Use trend-following indicators (MACD, momentum)
# - Include proper risk management for trending markets
```

Update any configuration or documentation to clarify:

```python
# In config files or README:
# Trend Following Strategy: NOT IMPLEMENTED
# - Referenced in regime detection but not available
# - Will be added in future update
# - Currently using MA Crossover for trending markets
```

Ensure no imports fail:

```python
# Remove any trend following imports that might cause errors
# from strategies.trend_following import TrendFollowingStrategy  # Not implemented
```
</implementation>

<output>
Update market_regime.py:
- Remove "TrendFollowing" from TRENDING_STRONG regime
- Keep only implemented strategies

Update strategy_manager.py:
- Ensure enable_trend_following defaults to False
- Add warning if someone tries to enable it
- Remove any broken imports

Add documentation:
- Clear comments about what's not implemented
- TODO items for future development

Test that bot starts without trend following errors and regime detection works correctly.
</output>

<verification>
After implementation:
1. Verify bot starts without trend following import errors
2. Check that TRENDING_STRONG regime only enables MACrossover
3. Confirm no runtime errors related to trend following
4. Test that other strategies still work correctly
</verification>

<success_criteria>
- No references to unimplemented trend following strategy
- Bot starts and runs without trend following errors
- Regime detection uses only implemented strategies
- Clear documentation about missing functionality
- No breaking changes to existing working strategies
</success_criteria>