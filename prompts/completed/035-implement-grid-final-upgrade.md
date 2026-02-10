<objective>
Implement the final critical grid trading system upgrades to achieve full spec compliance. This addresses the remaining spec-breaking issues that prevent safe live capital deployment, including emergency stop sequencing, regime enforcement, strategy exclusivity, and comprehensive pre-trade assertions.

This is the final upgrade needed to make grid trading production-ready with enterprise-grade safety guarantees.
</objective>

<context>
This final upgrade addresses the remaining spec-breaking issues in the grid trading system as identified in the comprehensive Grid Spec Compliance Review. The system currently has critical gaps in emergency stop sequencing, regime enforcement, and strategy isolation that must be resolved before live deployment.

Key files to examine and modify:
@research/grid final.txt - Detailed compliance review with specific fixes required
@trading_bot.py - Main execution logic requiring fixes
@risk_manager.py - Risk validation needing balance parameter fixes
@strategy_manager.py - Regime enforcement integration needed

The grid system must pass all pre-trade assertions and follow the authoritative execution sequence diagram.
</context>

<requirements>
Implement all 6 critical fixes from the compliance review:

1. **Fix RiskManager Balance Parameter** - Pass real account balance to validation instead of cached value
2. **Add Grid Allocation Assertion** - Verify Σ(level notionals) ≤ grid_capital before placing orders
3. **Enforce Emergency Stop First** - Hard fail if emergency stop placement fails, atomic grid registration
4. **Wire Regime Enforcement** - Connect regime detection to grid lifecycle shutdown
5. **Implement Strategy Exclusivity** - Block non-grid strategies when grid is active on symbol
6. **Add Pre-Trade Assertions** - Implement comprehensive assertion checklist before grid creation

Each fix must follow the execution sequence diagram and assertion checklist from the specification.
</requirements>

<implementation>
Follow the exact fixes specified in the compliance review:

**Fix 1: RiskManager Balance Parameter**
```python
# Change validate_grid_exposure signature and usage
def validate_grid_exposure(self, symbol, proposed_grid_capital, account_balance, current_exposure):
    # Use passed account_balance instead of getattr(self, '_account_balance', 10000)

# Update call in trading_bot.py
self.risk_manager.validate_grid_exposure(symbol, grid_capital, account_balance, current_exposure)
```

**Fix 2: Grid Allocation Assertion**
```python
# After calculating grid_levels, before placing orders
total_allocated = sum(level["notional"] for level in grid_levels)
if total_allocated > grid_capital:
    raise RuntimeError("Grid allocation exceeds approved capital")
```

**Fix 3: Emergency Stop First**
```python
# Enforce strict ordering
stop_order = place_emergency_stop(...)
if not stop_order or not stop_order["id"]:
    raise RuntimeError("Emergency stop placement failed")

self.grid_lifecycle.register_new_grid(symbol, grid_capital, stop_price)
# THEN place grid orders
```

**Fix 4: Regime Enforcement**
```python
# In signal generation or monitoring loop
ALLOWED_GRID_REGIMES = [MarketRegime.RANGING_CALM, MarketRegime.RANGING_VOLATILE]

if regime not in ALLOWED_GRID_REGIMES:
    self.grid_lifecycle.on_regime_disallowed(symbol)
```

**Fix 5: Strategy Exclusivity**
```python
# In _execute_signal()
if self.grid_lifecycle.has_active_grid(symbol) and signal.strategy != StrategyType.GRID_TRADING:
    return  # Block non-grid strategies
```

**Fix 6: Pre-Trade Assertions**
Implement the comprehensive assertion checklist as a function that runs before grid creation and raises RuntimeError on any failure.

**Why These Constraints Matter:**
The grid trading system requires absolute safety guarantees. Any single failure in the pre-trade assertions means the system doesn't have a controlled grid - it has uncontrolled leveraged exposure. These fixes ensure every grid deployment is provably safe.
</implementation>

<output>
Modify the following files with the specified fixes:

- `./trading_bot.py` - Emergency stop sequencing, strategy exclusivity, regime enforcement
- `./risk_manager.py` - Balance parameter fix in validate_grid_exposure
- `./strategy_manager.py` - Regime enforcement integration (if needed)

Create a new file documenting the final upgrade:
- `./GRID_FINAL_UPGRADE_COMPLETE.md` - Summary of all fixes with verification checklist
</output>

<verification>
After implementation, verify all fixes work:

1. **Balance Parameter Test**: RiskManager validation uses real account balance
2. **Allocation Check Test**: Grid creation fails if total allocation exceeds capital
3. **Emergency Stop Test**: Grid creation aborts if emergency stop fails
4. **Regime Test**: Grids automatically unwind when regime becomes disallowed
5. **Exclusivity Test**: Non-grid strategies blocked when grid active on symbol
6. **Assertion Test**: All pre-trade assertions pass for valid grids

Run comprehensive grid creation tests to ensure all safety mechanisms work.
</verification>

<success_criteria>
- All 6 spec-breaking issues from grid final.txt are resolved
- System passes the complete pre-trade assertion checklist
- Grid execution follows the authoritative sequence diagram exactly
- No bypass paths exist for any safety mechanism
- Grid trading system is fully spec-compliant and ready for live capital
- All fixes include proper error handling and logging
</success_criteria></content>
<parameter name="filePath">prompts/035-implement-grid-final-upgrade.md

---
Completed at: 2026-01-13T05:24:25.638Z
