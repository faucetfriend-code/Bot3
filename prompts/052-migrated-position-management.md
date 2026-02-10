<objective>
Implement trend-following management for positions migrated from grid unwind.

After partial grid unwind, kept positions need active management:
- Trailing stop loss that follows the trend
- Take profit targets based on trend strength
- Exit when trend weakens or reverses
</objective>

<context>
Prerequisites: Prompts 049, 050, and 051 must be completed first.

After partial unwind completes:
- Positions are marked as 'migrated' in database
- RiskManager tracks them in migrated_positions dict
- They have no grid orders (all cancelled)
- They need NEW management logic (not grid, not original strategy)

Current gap:
- Grid positions managed by GridLifecycleManager
- Strategy positions managed by their respective strategies
- Migrated positions: NO management after creation

This prompt adds a MigratedPositionManager to handle these orphaned positions.

Key files to create/modify:
- `trading_bot_v2/migrated_position_manager.py` - NEW file
- `trading_bot_v2/trading_bot.py` - Integration in main loop
</context>

<requirements>
1. **Create MigratedPositionManager class**

   ```python
   class MigratedPositionManager:
       """
       Manages positions migrated from grid to trend-following.

       Responsibilities:
       - Set and update trailing stops
       - Monitor trend direction for exit signals
       - Close positions when trend reverses
       """

       def __init__(self, client: PacificaClient, risk_manager: RiskManager,
                    regime_detector: MarketRegimeDetector):
           self.client = client
           self.risk_manager = risk_manager
           self.regime_detector = regime_detector
           self.trailing_stops: Dict[str, float] = {}  # position_id -> stop_price
   ```

2. **Implement manage_positions() method**
   - Called every loop iteration from TradingBot
   - For each migrated position:
     - Get current price
     - Update trailing stop if price moved favorably
     - Check if stop hit → close position
     - Check if trend reversed → close position

3. **Trailing Stop Logic**
   ```python
   def update_trailing_stop(self, symbol: str, position: Dict, current_price: float):
       """
       Update trailing stop for migrated position.

       - LONG: Stop trails below price by 2x ATR
       - SHORT: Stop trails above price by 2x ATR
       - Stop only moves in favorable direction (never tightens against you)
       """
   ```

4. **Trend Reversal Detection**
   - Use get_trend_direction_cached from MarketRegimeDetector
   - If trend direction changes (up→down or down→up):
     - Log warning
     - Close position at market
     - Unregister from RiskManager

5. **Take Profit (Optional but recommended)**
   - If position gains 3x ATR, take partial profit (50%)
   - Let remaining 50% ride with trailing stop
   - Log partial take profit

6. **Integration with TradingBot**
   ```python
   # In TradingBot.__init__
   self.migrated_manager = MigratedPositionManager(
       client=self.client,
       risk_manager=self.risk_manager,
       regime_detector=self.regime_detector
   )

   # In TradingBot._trading_loop
   if self.migrated_manager.has_positions():
       self.migrated_manager.manage_positions()
   ```

7. **Database Updates**
   - When position closed: update grid_positions with final P&L
   - Set exit_reason to 'trailing_stop', 'trend_reversal', or 'take_profit'
   - Include migrated_duration (time from migration to close)
</requirements>

<constraints>
- Migrated positions MUST have stops at all times
- Never increase position size (only reduce or close)
- ATR calculated from 4h timeframe (same as grid)
- Minimum stop distance: 0.5x ATR (prevent tiny stops)
- Maximum positions to manage: 10 per symbol (safety cap)
</constraints>

<implementation>
Step 1: Create migrated_position_manager.py skeleton
Step 2: Implement trailing stop calculation using ATR
Step 3: Implement update_trailing_stop with direction check
Step 4: Implement trend reversal detection using regime_detector
Step 5: Implement manage_positions main loop
Step 6: Add partial take profit logic (optional)
Step 7: Integrate into TradingBot._trading_loop
Step 8: Add database updates for closed migrated positions
Step 9: Add comprehensive logging
Step 10: Test with mock positions
</implementation>

<output>
New files:
- `./trading_bot_v2/migrated_position_manager.py` - Main manager class

Modified files:
- `./trading_bot_v2/trading_bot.py` - Integration
</output>

<verification>
1. Trailing stop updates correctly (only moves in favorable direction)
2. Position closed when stop hit
3. Position closed when trend reverses
4. Database updated with final P&L and exit_reason
5. RiskManager notified when position closed
6. No orphaned positions (all managed until closed)
</verification>

<success_criteria>
- All migrated positions actively managed
- Trailing stops protect profits
- Trend reversal triggers exit
- Clean handoff from grid to trend-following
- Positions tracked until closed
- Database audit trail complete
</success_criteria>

<explicit_do_not_rules>
1. DO NOT leave migrated positions without stops
2. DO NOT increase position size after migration
3. DO NOT ignore trend reversal signals
4. DO NOT let positions run indefinitely without management
</explicit_do_not_rules>
