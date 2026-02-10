<objective>
Update RiskManager to handle partial grid exposure after selective unwind.

When partial grid unwind keeps some positions (migrated to trend-following), RiskManager must:
- Track migrated positions separately from grid positions
- Recalculate grid exposure excluding migrated positions
- Apply appropriate risk limits to migrated positions
</objective>

<context>
Prerequisites: Prompts 049 and 050 must be completed first.

After partial unwind:
- Some positions are closed (against-trend) - exposure reduced
- Some positions are kept (with-trend) - still have exposure but NOT grid exposure
- RiskManager currently tracks all grid positions in `grid_exposure[symbol]`

Problem: If we keep positions but mark grid as DISABLED, RiskManager might:
1. Think exposure is zero (wrong - migrated positions still exist)
2. Allow new positions that exceed total risk limits
3. Lose track of migrated position risk

Solution: Add separate tracking for migrated positions with appropriate risk management.

Key files to modify:
- `trading_bot_v2/risk.py` or `trading_bot_v2/risk_manager.py` - Main target
- `trading_bot_v2/grid_lifecycle_manager.py` - Call new RiskManager methods

Reference the project CLAUDE.md for architecture details.
</context>

<requirements>
1. **Add migrated_positions tracking to RiskManager**

   ```python
   def __init__(self):
       # Existing
       self.grid_exposure: Dict[str, float] = {}

       # New
       self.migrated_positions: Dict[str, List[Dict]] = {}
       # Format: {symbol: [{side, qty, entry_price, migrated_at, original_grid_id}]}
   ```

2. **Add register_migrated_position() method**

   ```python
   def register_migrated_position(self, symbol: str, position: Dict) -> bool:
       """
       Register a position migrated from grid to trend-following.

       Args:
           symbol: Trading symbol
           position: Dict with side, qty, entry_price, original_grid_id

       Returns:
           True if registered successfully
       """
   ```

3. **Add get_total_exposure() method**

   ```python
   def get_total_exposure(self, symbol: str) -> Dict[str, float]:
       """
       Get combined exposure for symbol (grid + migrated + other).

       Returns:
           Dict with 'grid_exposure', 'migrated_exposure', 'total_exposure'
       """
   ```

4. **Update validate_grid_exposure() to exclude migrated**
   - Current: Checks if grid_exposure[symbol] exceeds limit
   - New: Check grid exposure only (migrated tracked separately)
   - Log warning if total exposure (grid + migrated) approaches limits

5. **Add validate_migrated_position() method**
   - Ensure migrated positions have appropriate stops
   - Check if migrated exposure exceeds trend-following limits
   - Different risk limits than grid (grid is 5%, trend can be 10%)

6. **Add unregister_migrated_position() for cleanup**
   - Called when migrated position is closed (hit TP/SL)
   - Removes from tracking, logs final P&L

7. **Integration with GridLifecycleManager**
   - After _partial_exit, call register_migrated_position for kept positions
   - Pass original grid_id for audit trail
</requirements>

<constraints>
- DO NOT break existing grid exposure validation
- Migrated positions must ALWAYS have stops (safety requirement)
- Total account exposure (all types) must not exceed account limits
- Keep existing interface for validate_grid_exposure (backwards compatible)
- Thread-safe: Use locks for migrated_positions dict
</constraints>

<implementation>
Step 1: Read current RiskManager implementation
Step 2: Add migrated_positions dict to __init__
Step 3: Implement register_migrated_position with validation
Step 4: Implement get_total_exposure combining all sources
Step 5: Update validate_grid_exposure to log total exposure
Step 6: Add validate_migrated_position with trend-following limits
Step 7: Add unregister_migrated_position for cleanup
Step 8: Update GridLifecycleManager._partial_exit to call register
Step 9: Add unit tests for new methods
</implementation>

<output>
Modified files:
- `./trading_bot_v2/risk.py` (or risk_manager.py) - Added migrated position tracking
- `./trading_bot_v2/grid_lifecycle_manager.py` - Integration with RiskManager
- `./trading_bot_v2/config.py` - Add MIGRATED_POSITION_MAX_EXPOSURE config (if needed)
</output>

<verification>
1. Register migrated position → appears in migrated_positions dict
2. get_total_exposure returns correct combined values
3. Closing migrated position → unregister removes it
4. Total exposure limits still enforced across all position types
5. Backwards compatible with existing grid validation
6. Thread safety: concurrent access doesn't corrupt state
</verification>

<success_criteria>
- Migrated positions tracked separately from grid positions
- Total exposure accurately calculated (grid + migrated + other)
- Risk limits enforced appropriately for each position type
- Clean integration with GridLifecycleManager partial exit
- No regression in existing risk management
</success_criteria>

<explicit_do_not_rules>
1. DO NOT allow migrated positions without stop losses
2. DO NOT let total exposure exceed account limits
3. DO NOT lose track of positions during migration
4. DO NOT break existing grid exposure validation
</explicit_do_not_rules>
