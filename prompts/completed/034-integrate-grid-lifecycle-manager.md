<objective>
Integrate the GridLifecycleManager from grid_lifecycle_manager.py into the trading bot, replacing all existing grid state management code. This manager provides hard safety guarantees for grid trading lifecycle management and must be properly wired into the existing architecture.

This is a critical refactoring that centralizes grid state management and ensures all grid operations go through a single, well-tested authority.
</objective>

<context>
This integration addresses the grid trading system in the Pacifica perpetual futures trading bot. The GridLifecycleManager provides authoritative state management for grid lifecycles, replacing the inline grid state tracking that was previously implemented.

Key files to examine and modify:
@trading_bot_v2/grid_lifecycle_manager.py - The new grid lifecycle manager to integrate
@research/grid manager.txt - Integration instructions and guarantees
@trading_bot.py - Current implementation with inline grid state management

The GridLifecycleManager ensures single-grid-per-symbol enforcement, guaranteed exits, and proper state cleanup.
</context>

<requirements>
Implement the exact 5-step integration process from the grid manager documentation:

1. **Instantiate GridLifecycleManager** in TradingBot.__init__
2. **Enforce single-grid check** in _execute_grid_signal before grid creation
3. **Register grid through manager** after emergency stop placement succeeds
4. **Wire regime change handling** to call on_regime_disallowed
5. **Wire emergency stop monitoring** to call on_emergency_stop_triggered

Remove all existing grid state management code that is replaced by the manager:
- _active_grids dictionary and related logic
- _handle_regime_transition method
- _monitor_emergency_stops method
- _unwind_grid_for_symbol method
- Any inline grid state tracking
</requirements>

<implementation>
Follow the exact integration steps from the documentation:

**Instantiation (Step 1):**
```python
from grid_lifecycle_manager import GridLifecycleManager

self.grid_lifecycle = GridLifecycleManager(
    client=self.client,
    risk_manager=self.risk_manager
)
```

**Single-Grid Enforcement (Step 2):**
Replace existing checks with:
```python
if self.grid_lifecycle.has_active_grid(symbol):
    logging.warning(f"Grid already active for {symbol}")
    return
```

**Grid Registration (Step 3):**
Replace _active_grids writes with:
```python
self.grid_lifecycle.register_new_grid(
    symbol=symbol,
    grid_capital=grid_capital,
    emergency_stop_price=emergency_stop_price
)
```

**Regime Handling (Step 4):**
Replace _handle_regime_transition with:
```python
if not self.market_regime.is_grid_allowed(symbol):
    self.grid_lifecycle.on_regime_disallowed(symbol)
```

**Emergency Stop Handling (Step 5):**
Replace _monitor_emergency_stops with proper monitoring that calls:
```python
self.grid_lifecycle.on_emergency_stop_triggered(symbol)
```

**Why This Architecture Matters:**
- GridLifecycleManager provides hard safety boundaries that cannot be bypassed
- All grid exits go through one code path (_force_exit) ensuring consistency
- RiskManager remains the source of truth for sizing, while lifecycle manages state
- This separation matches production-grade trading system architecture
</implementation>

<output>
Modify the following files with the specified changes:

- `./trading_bot.py` - Remove old grid state management, integrate GridLifecycleManager
- Ensure no references to removed methods remain

Create a new file documenting the integration:
- `./GRID_LIFECYCLE_INTEGRATION_COMPLETE.md` - Summary of integration and verification steps
</output>

<verification>
After integration, verify:

1. **Instantiation Test**: GridLifecycleManager is properly instantiated in __init__
2. **Single-Grid Test**: Attempting multiple grids on same symbol is rejected
3. **Regime Test**: Regime changes trigger automatic grid unwinding
4. **Emergency Stop Test**: Emergency stop fills trigger full position flattening
5. **State Cleanup Test**: No references to removed methods (_active_grids, etc.)
6. **Import Test**: grid_lifecycle_manager imports correctly

Run the bot and verify all grid operations work through the new manager.
</verification>

<success_criteria>
- GridLifecycleManager is fully integrated and operational
- All old grid state management code has been removed
- Single-grid-per-symbol enforcement works through the manager
- Regime changes and emergency stops trigger guaranteed unwinding
- No bypass paths exist - all grid operations go through the manager
- System maintains all safety guarantees from the grid compliance fixes
</success_criteria></content>
<parameter name="filePath">prompts/034-integrate-grid-lifecycle-manager.md

---
Completed at: 2026-01-12T23:01:13.546Z
