<objective>
Implement partial grid unwind in GridLifecycleManager when regime changes from ranging to trending.

Instead of closing ALL positions (current behavior), this feature will:
- Close positions that are AGAINST the trend (would lose in trending market)
- KEEP positions that are WITH the trend (could profit from trend continuation)
- Migrate kept positions to trend-following management
</objective>

<context>
Prerequisites: Prompt 049 must be completed first (trend direction detection).

Current behavior (in GridLifecycleManager._force_exit):
- When regime changes to trending, ALL grid positions are closed
- This is conservative but misses potential profits from trend-aligned positions

New behavior:
- Query trend direction from MarketRegimeDetector
- Close only positions that are against the trend
- Keep positions aligned with trend direction
- Fall back to full exit if trend direction is unclear ('none')

Key files to modify:
- `trading_bot_v2/grid_lifecycle_manager.py` - Main target
- `trading_bot_v2/trading_bot.py` - May need to pass trend direction

Reference the project CLAUDE.md for architecture details.
</context>

<requirements>
1. **Add _partial_exit() method to GridLifecycleManager**

   ```python
   def _partial_exit(self, symbol: str, reason: str, trend_direction: str) -> Dict[str, Any]:
       """
       Selective unwind: Close against-trend positions, keep with-trend.

       Args:
           symbol: Trading symbol
           reason: Why the unwind is happening
           trend_direction: 'up', 'down', or 'none' (from MarketRegimeDetector)

       Returns:
           Dict with 'closed_positions', 'kept_positions', 'success' keys
       """
   ```

2. **Partial Exit Logic**
   - If trend_direction is 'none', fall back to full _force_exit (safety first)
   - Cancel ALL open orders (pending orders are neutral, don't keep them)
   - For each open position:
     - If trend='up' and position is SHORT → CLOSE (against trend)
     - If trend='up' and position is LONG → KEEP (with trend)
     - If trend='down' and position is SHORT → KEEP (with trend)
     - If trend='down' and position is LONG → CLOSE (against trend)
   - Log each decision (kept vs closed) with reason

3. **Update on_regime_disallowed() to use partial exit**
   - Fetch trend direction from MarketRegimeDetector
   - Call _partial_exit instead of _force_exit when trend is clear
   - Fall back to _force_exit if partial exit fails

4. **Position Migration for Kept Positions**
   - Mark kept positions with a 'trend_aligned' flag in grid state
   - Update grid state to DISABLED_BY_REGIME but with 'has_migrated_positions' = True
   - These positions should be managed by trend-following logic (not grid)

5. **Safety Requirements**
   - Always cancel ALL orders (even for kept positions - no pending grid orders in trending)
   - If any error during partial exit, fall back to full _force_exit
   - Add config flag: GRID_PARTIAL_UNWIND_ENABLED (default: True)
   - Log critical warnings for all decisions

6. **Database Updates**
   - Update grid_positions table for closed positions (status='closed', exit_reason='against_trend')
   - Update grid_positions table for kept positions (status='migrated', exit_reason='trend_aligned')
</requirements>

<constraints>
- DO NOT keep any open grid orders - all orders must be cancelled
- Always fall back to full exit on any error (safety first)
- Do not break existing _force_exit behavior - keep it as fallback
- Position side mapping: 'long'/'bid' = LONG, 'short'/'ask' = SHORT
- Kept positions are NO LONGER grid positions - they become regular positions
</constraints>

<implementation>
Step 1: Read grid_lifecycle_manager.py to understand current _force_exit
Step 2: Read market_regime.py to import get_trend_direction_cached
Step 3: Add GRID_PARTIAL_UNWIND_ENABLED config check
Step 4: Implement _partial_exit with try/except wrapper
Step 5: Update on_regime_disallowed to use partial exit
Step 6: Add database updates for closed/migrated positions
Step 7: Add comprehensive logging for each decision
Step 8: Test with mock positions in different trend directions
</implementation>

<output>
Modified files:
- `./trading_bot_v2/grid_lifecycle_manager.py` - Added partial unwind logic
- `./trading_bot_v2/config.py` - Add GRID_PARTIAL_UNWIND_ENABLED flag (if needed)
</output>

<verification>
1. Test with trend='up', positions=[LONG, SHORT] → SHORT closed, LONG kept
2. Test with trend='down', positions=[LONG, SHORT] → LONG closed, SHORT kept
3. Test with trend='none' → falls back to full exit (all closed)
4. Test with error in partial exit → falls back to full exit
5. Verify database records show correct status/exit_reason
6. Verify all grid orders are cancelled in all scenarios
</verification>

<success_criteria>
- _partial_exit() method implemented and working
- Against-trend positions closed, with-trend positions kept
- All grid orders cancelled (no pending orders remain)
- Fallback to full exit on errors or unclear trend
- Database updated correctly for closed/migrated positions
- Ready for RiskManager integration (next prompt)
</success_criteria>

<explicit_do_not_rules>
From the original document, these are critical safety rules:

1. DO NOT keep any open grid orders in trending regime
2. DO NOT skip the fallback to full exit on errors
3. DO NOT assume position side - always check actual side from API
4. DO NOT leave positions in ambiguous state - either grid or trend-following, not both
</explicit_do_not_rules>
