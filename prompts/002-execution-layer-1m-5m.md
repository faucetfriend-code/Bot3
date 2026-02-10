<objective>
Implement a precision execution layer using 1-minute and 5-minute timeframes for trade entries and stop management.

This separates execution timing from regime detection:
- Regime/permission: 1h (primary), 15m (secondary) - UNCHANGED
- Entry execution: 5m setup + 1m precision timing - NEW
</objective>

<context>
Prerequisites: Prompt 001 must be completed first (1m/5m data fetching available).

Current state:
- Regime detection uses 1h/4h ADX
- Signals generated on 15m timeframe
- Entries executed immediately when signals are valid

New execution model:
- Regime detection: 1h ADX (unchanged)
- Structure validation: 15m (unchanged)
- Entry setup: 5m indicator alignment
- Execution timing: 1m price/volume confirmation

This increases trade frequency by allowing faster entries once higher-TF permission is granted.

Key principle: 1m/5m NEVER affect regime state or strategy enablement.
</context>

<requirements>
1. **Create ExecutionLayer Class**
   Create `trading_bot_v2/execution_layer.py`:
   - Responsible for timing entries using 1m/5m data
   - Receives valid signals from StrategyManager
   - Confirms entry conditions on lower timeframes before execution

   ```python
   class ExecutionLayer:
       def __init__(self, fetcher: MultiTimeframeFetcher):
           self.fetcher = fetcher

       def refine_entry(self, signal: Signal, symbol: str) -> Optional[Signal]:
           """
           Refine signal entry using 1m/5m data.
           Returns refined signal with better entry price/timing, or None if conditions not met.

           Does NOT change:
           - signal.strategy (regime-determined)
           - signal.quality (strategy-determined)
           - signal.side (direction from higher TF)

           CAN refine:
           - signal.entry_price (tighter entry)
           - signal.stop_loss (ATR-based on 5m)
           - signal.confidence (boost if 1m confirms)
           """
   ```

2. **5-Minute Entry Setup Validation**
   In `refine_entry()`, check 5m data for:
   - RSI alignment with signal direction (RSI < 40 for SELL, > 60 for BUY)
   - Not in the middle of a 5m candle wick (avoid noise)
   - 5m momentum aligns with intended direction
   - Return None (skip this entry) if 5m shows counter-momentum

3. **1-Minute Execution Timing**
   If 5m setup is valid, check 1m for:
   - Volume spike confirmation (1.2x average)
   - Price not at local extreme (avoid buying highs, selling lows)
   - Wick rejection pattern (bullish wick for BUY, bearish for SELL)
   - Tighter stop placement using 1m ATR

4. **Integration with TradingBot**
   In `trading_bot.py`:
   - After `strategy_manager.generate_signals_for_market()` returns valid signals
   - Pass signals through `execution_layer.refine_entry()` before execution
   - Only execute if refined signal is returned (not None)
   - Log both: "Signal generated (15m)" and "Entry confirmed (1m/5m)" or "Entry skipped (1m/5m conditions not met)"

5. **Grid Trading Special Case**
   Grid orders should use 1m for:
   - Fill tracking (detect partial fills faster)
   - Emergency stop detection (price moving against grid)
   - Do NOT change grid spacing (still uses 1h ATR)

6. **Liquidation Capture Special Case**
   Liquidation detection should use 1m/5m for:
   - Cascade detection (3%+ move in 5 candles on 1m = liquidation cascade)
   - Faster trigger response
   - This is where 1m/5m has highest impact
</requirements>

<constraints>
- 1m/5m data NEVER affects regime detection (that stays on 1h)
- 1m/5m data NEVER enables/disables strategies (StrategyManager unchanged)
- 1m/5m data NEVER flips signal direction (higher TF determines BUY/SELL)
- All entry refinements are optional improvements, not gates
- If 1m/5m data is unavailable, fall back to current behavior (immediate execution)
- Do NOT stack confluence checks across all 5 timeframes - that defeats the purpose
</constraints>

<implementation>
Step 1: Create execution_layer.py with ExecutionLayer class skeleton
Step 2: Implement 5m setup validation (RSI, momentum)
Step 3: Implement 1m timing confirmation (volume, wick patterns)
Step 4: Add ATR-based stop refinement using 5m data
Step 5: Integrate ExecutionLayer into TradingBot._check_signals()
Step 6: Add appropriate logging for entry refinement decisions
Step 7: Special handling for grid fills and liquidation detection
Step 8: Test with bot running - verify signals are refined, not blocked
</implementation>

<output>
New files:
- `./trading_bot_v2/execution_layer.py` - Execution timing logic

Modified files:
- `./trading_bot_v2/trading_bot.py` - Integration of ExecutionLayer
- `./trading_bot_v2/strategies/grid_trading.py` - 1m fill tracking (optional)
- `./trading_bot_v2/strategies/liquidation_capture.py` - 1m/5m trigger detection
</output>

<verification>
1. Start bot and observe logs for "Entry confirmed" or "Entry skipped" messages
2. Verify regime detection still uses 1h only (grep logs for regime changes)
3. Verify signal direction comes from higher TF strategies (not flipped by 1m)
4. Test that entries are refined (better timing) not blocked entirely
5. If 1m/5m data is unavailable, verify fallback to immediate execution
6. Monitor trade frequency - should INCREASE, not decrease
</verification>

<success_criteria>
- ExecutionLayer class created and integrated
- Entries use 1m/5m for timing without changing regime logic
- Trade frequency increases (more entry opportunities detected)
- Fallback works when lower TF data unavailable
- Grid and liquidation strategies leverage 1m appropriately
- No regression in existing strategy behavior
</success_criteria>

<explicit_do_not_rules>
These are critical - the document explicitly warns against these anti-patterns:

1. Do NOT run regime detection on 1m or 5m
2. Do NOT require all 5 timeframes to agree
3. Do NOT let 1m flip strategy state
4. Do NOT stack confluence checks across all TFs

If you find yourself adding multi-TF agreement requirements, STOP - that's the opposite of what this change is for.
</explicit_do_not_rules>
