<objective>
Fix all critical grid trading system compliance failures identified in the Grid Spec Compliance Review. The grid trading system currently has 3 spec-breaking failures and 3 risky issues that must be resolved before the system can be considered safe for live capital deployment.

This is a critical safety fix that ensures grid trading operates within the defined risk parameters and cannot create uncontrolled exposure or persist in inappropriate market regimes.
</objective>

<context>
This fix addresses the grid trading system in the Pacifica perpetual futures trading bot. The system must comply with the Grid Trading Brief specifications for single-grid-per-symbol enforcement, proper capital allocation, emergency stops, and regime-based safety controls.

Key files to examine and modify:
@trading_bot.py - Main execution logic and grid state management
@risk_manager.py - Risk validation and capital allocation
@strategy_manager.py - Signal generation and regime handling
@research/grid fail.txt - Detailed compliance review with specific issues

The grid system is currently in a "spec-breaking" state and cannot be safely used with real capital.
</context>

<requirements>
Implement the following 6 critical fixes in order of priority:

1. **Single Grid Per Symbol Enforcement** - Add hard lock preventing multiple grids on same symbol
2. **Regime Auto-Disable** - Implement automatic grid unwinding when regime changes
3. **Emergency Stop Handling** - Make emergency stops trigger full position flattening
4. **Grid Capital Allocation** - Force capital sourcing through RiskManager only
5. **Exposure Accounting Fix** - Correct double-counting in grid exposure tracking
6. **Parameter Normalization** - Centralize grid parameter validation in RiskManager

Each fix must include proper error handling, logging, and state cleanup.
</requirements>

<implementation>
For each fix, follow these implementation patterns:

**State Management**: Use `self._active_grids` as the authoritative source for active grid tracking
**Error Handling**: Log warnings for rejected operations, raise exceptions for critical failures
**Cleanup**: Always clean up state (delete from _active_grids, reset exposure) on grid termination
**Validation**: Add pre-execution checks before any grid operations
**Monitoring**: Integrate with existing risk monitoring loops for regime and emergency stop checks

**Why These Constraints Matter**:
- Grid trading is stateful and can accumulate unlimited exposure if not properly controlled
- Regime violations can cause grids to trade against the market direction
- Emergency stops must be guaranteed to prevent catastrophic losses
- Single-grid enforcement prevents over-allocation of capital to one symbol
</implementation>

<specific_fixes>
1. **Single Grid Lock** (trading_bot.py::_execute_grid_signal)
   - Add check: `if symbol in self._active_grids: return`
   - Add to _active_grids on successful grid creation
   - Remove from _active_grids on emergency stop, full unwind, regime disable

2. **Regime Auto-Disable** (trading_bot.py - new method _handle_regime_transition)
   - Check if current regime allows grids for each symbol
   - If regime changed to non-grid-allowed: cancel orders, close positions, cleanup state
   - Call this from _monitor_risk() or main loop

3. **Emergency Stop Enhancement** (trading_bot.py::_execute_grid_signal + monitoring)
   - Change emergency stop to market order or guaranteed trigger
   - Add monitoring loop to detect emergency stop fills
   - On trigger: cancel_all_orders, _close_all_positions, risk_manager cleanup, delete _active_grids

4. **Capital Allocation** (trading_bot.py::_execute_grid_signal)
   - Replace `signal.grid_capital` with `self.risk_manager.get_grid_capital(...)`
   - Override signal.grid_capital with allocated amount
   - Remove trust in strategy-provided capital values

5. **Exposure Accounting** (trading_bot.py::_execute_grid_signal)
   - Initialize `self.risk_manager.grid_exposure[symbol] = 0.0`
   - Increment exposure only per actual order placement
   - Remove the double-counting where signal.grid_capital is added first

6. **Parameter Normalization** (risk_manager.py - new method normalize_grid_params)
   - Add method to validate and normalize grid_levels, quantity, spacing
   - Treat strategy values as hints, enforce minimums/maximums
   - Return validated parameters for use in execution
</specific_fixes>

<output>
Modify the following files with the specified fixes:

- `./trading_bot.py` - Add grid state enforcement, regime monitoring, emergency stop handling
- `./risk_manager.py` - Add capital allocation method, parameter normalization
- `./strategy_manager.py` - Ensure regime changes trigger grid cleanup (if needed)

Create a new file documenting the fixes:
- `./CRITICAL_GRID_FIXES_COMPLETE.md` - Summary of all implemented fixes with verification steps
</output>

<verification>
After implementing all fixes, verify:

1. **Single Grid Test**: Attempt to create multiple grids on same symbol - should be rejected
2. **Regime Transition Test**: Change market regime to trending - existing grids should unwind
3. **Emergency Stop Test**: Trigger emergency stop - all positions should flatten
4. **Capital Allocation Test**: Verify capital comes from RiskManager, not signal
5. **Exposure Accounting Test**: Check exposure values are accurate, no double-counting
6. **Parameter Validation Test**: Test with invalid grid parameters - should be normalized

Run the bot with test signals and verify all safety mechanisms work correctly.
</verification>

<success_criteria>
- All 6 compliance failures from grid fail.txt are resolved
- Grid system passes all hard checks (single grid, regime disable, emergency stop)
- No spec-breaking issues remain
- System moves from "NOT SAFE FOR LIVE CAPITAL" to "Spec-compliant"
- All fixes include proper error handling and logging
- Grid state is properly managed throughout the lifecycle
</success_criteria></content>
<parameter name="filePath">prompts/033-fix-grid-compliance-failures.md

---
Completed at: 2026-01-12T22:40:01.496Z
