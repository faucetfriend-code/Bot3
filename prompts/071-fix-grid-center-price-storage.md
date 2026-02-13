<objective>
Fix the grid center price storage issue in the trading bot. The grid system is generating signals and executing trades, but failing to properly store and retrieve the grid center price, causing cascading failures.

This is critical because without accurate center price storage, grids cannot function properly - they need the reference price to calculate grid levels and execute trades.
</objective>

<context>
This is a Python cryptocurrency trading bot with a grid trading system. The system was recently repaired and is now operational - signals are being generated and trades executed, but the grid state storage is broken.

Current working:
- Grid signals generating correctly
- Trades executing via ExecutionLayer
- UI showing grid status
- Grid lifecycle manager initializing

Broken:
- Grid center price not being saved to database properly
- Grid state retrieval returning incomplete/corrupted data
- Grid consistency checks failing due to missing center price
- Grid repairs failing because they cannot reconstruct from stored data

The issue manifests as:
- "Orphaned grid detected: missing_center_price" warnings
- Grid validation failures on startup
- Auto-repair constantly reconstructing prices that should be stored
</context>

<requirements>
Fix the grid center price storage and retrieval in these areas:

1. Database Grid State Storage
   - Check if center_price is being included in INSERT/UPDATE queries
   - Verify column names match database schema
   - Ensure value is calculated before save

2. Grid State Retrieval
   - Check if center_price is being selected from database
   - Verify row_factory is set for dictionary access
   - Ensure SELECT query includes all required columns

3. Grid State Model/Schema
   - Check if data class has center_price field
   - Verify field name matches database column

4. Grid Initialization Flow
   - When creating new grid, verify center_price is set from current market price
   - Ensure price is fetched before grid creation

5. Universal Grid State Consistency
   - Check validation logic for center_price field
   - Verify repair properly saves reconstructed center price
</requirements>

<implementation>
Debug and fix the center price storage:

1. Examine database schema for grids table
2. Check save_grid_state function for center_price handling
3. Check load_grid_states function for center_price retrieval
4. Verify grid creation flow sets center_price
5. Fix any issues found in the data flow
6. Test by starting server and checking logs

Focus on ensuring center_price is:
- Saved to database when grid is created
- Retrieved from database when loading
- Properly persisted across restarts
</implementation>

<output>
Fixed files:
- trading_bot_v2/database.py - If center_price column/query needs fixing
- trading_bot_v2/grid_lifecycle_manager.py - If storage/retrieval needs fixing  
- trading_bot_v2/universal_grid_state_consistency.py - If validation needs fixing
</output>

<verification>
After applying fixes:
1. Start server - no "missing_center_price" warnings
2. Query database - center_price has values
3. Restart server - grids load with center_price intact
4. Create new grid - saves with proper center_price
5. Monitor - no center_price related errors
</verification>

<success_criteria>
1. Grid center price properly stored in database
2. Grid center price correctly retrieved on restart
3. No "missing_center_price" warnings in logs
4. Grid validation passes without auto-repair
5. New grids created with proper center_price
6. Grid state persists across server restarts
</success_criteria>
