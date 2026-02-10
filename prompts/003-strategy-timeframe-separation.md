<objective>
Refactor strategy signal generation to use proper timeframe separation:
- Permission layer: 1h regime determines IF a strategy can trade
- Structure layer: 15m confirms local market structure
- Setup layer: 5m validates entry setup conditions
- Execution: 1m provides precise entry timing

This makes each strategy generate signals more frequently while maintaining quality.
</objective>

<context>
Prerequisites: Prompts 001 and 002 must be completed first.

Current problem: Strategies require too much multi-timeframe confluence, causing "confluence deadlock" where valid trades are blocked because not all timeframes agree at the exact same moment.

Solution: Decouple permission from execution:
- Higher TFs grant permission (regime = ranging → mean reversion allowed)
- Lower TFs find opportunities (5m RSI extreme → entry candidate)
- 1m confirms timing (volume spike → execute now)

This is standard professional practice - it increases opportunity density without weakening signal quality.
</context>

<requirements>
1. **Mean Reversion Strategy Refactor**
   File: `trading_bot_v2/strategies/mean_reversion.py`

   Current: Requires extreme RSI on both 15m AND 1h
   New:
   - Permission: 1h regime = ranging (already done by StrategyManager)
   - Structure: 15m validates range bounds (price near BB bands)
   - Setup: 5m shows RSI extreme (< 30 or > 70) - THIS is the trigger
   - Entry: 1m volume/wick confirmation via ExecutionLayer

   Key change: Move RSI trigger from 15m+1h agreement to 5m only (with 15m structure validation)

2. **MA Crossover Strategy Refactor**
   File: `trading_bot_v2/strategies/ma_crossover.py`

   Current: Tracks crossover on single timeframe, waits for pullback
   New:
   - Permission: 1h regime = trending (already done)
   - Bias: 1h MA relationship determines long/short bias
   - Pullback: 15m identifies retracement to MA zone
   - Setup: 5m shows momentum resumption
   - Entry: 1m timing via ExecutionLayer

   Key change: Don't require 1m MA signals (too noisy) - use 1m only for timing

3. **Liquidation Capture Strategy Refactor**
   File: `trading_bot_v2/strategies/liquidation_capture.py`

   Current: Detects cascade on higher TFs
   New:
   - Sanity: 1h confirms market is active (not dead volume)
   - Volatility: 15m shows elevated ATR
   - Trigger: 5m OR 1m detects cascade (3%+ move in 5 candles)
   - Entry: Immediate on 1m detection

   Key change: Liquidations happen on low TFs - this is where 1m/5m has MAXIMUM impact

4. **Grid Trading Strategy Refactor**
   File: `trading_bot_v2/strategies/grid_trading.py`

   Current: Uses 1h ATR for spacing, executes on any TF
   New:
   - Permission: 1h regime = ranging_volatile
   - Spacing: 1h ATR (unchanged - this is intentional)
   - Fill tracking: 1m for faster fill detection
   - Emergency: 1m ADX spike detection for early grid exit

   Key change: Grid spacing stays on 1h, but fill management uses 1m

5. **Update generate_signals() Signatures**
   All strategies should accept optional `execution_timeframes` parameter:
   ```python
   def generate_signals(self, symbol: str, multi_tf_data: Dict,
                        current_price: float,
                        execution_tf_data: Optional[Dict] = None) -> List[Signal]:
   ```
   Where `execution_tf_data` contains 1m/5m data from ExecutionLayer

6. **Remove Excessive Confluence Checks**
   Search for patterns like:
   - `if rsi_15m < 30 AND rsi_1h < 35` → Remove 1h RSI requirement for entry
   - Multi-TF alignment gates → Replace with single-TF trigger + higher-TF permission
   - Don't delete the code, but comment it out with `# DISABLED: TF separation - see 002`
</requirements>

<constraints>
- Regime detection MUST stay on 1h only
- Strategy enablement MUST be controlled by StrategyManager (1h regime)
- Signal direction (BUY/SELL) comes from higher TF analysis
- 1m/5m can refine timing but NEVER flip direction
- If removing confluence checks increases bad trades, the problem is elsewhere (not here)
- This is a professional, standard approach - don't second-guess the architecture
</constraints>

<implementation>
Step 1: Read all four strategy files to understand current confluence requirements
Step 2: Map each strategy to the new timeframe layers:
        - Mean Reversion: 1h permission → 15m structure → 5m trigger → 1m timing
        - MA Crossover: 1h permission+bias → 15m pullback → 5m resumption → 1m timing
        - Liquidation Capture: 1h sanity → 15m volatility → 1m/5m trigger → immediate
        - Grid Trading: 1h permission+spacing → 1m fills
Step 3: Refactor Mean Reversion first (easiest, clearest impact)
Step 4: Refactor Liquidation Capture second (highest frequency impact)
Step 5: Refactor MA Crossover (moderate complexity)
Step 6: Refactor Grid Trading (minimal changes, mostly fill tracking)
Step 7: Update strategy manager to pass execution TF data
Step 8: Test each strategy in isolation, then together
</implementation>

<output>
Modified files:
- `./trading_bot_v2/strategies/mean_reversion.py` - 5m trigger, removed 1h RSI gate
- `./trading_bot_v2/strategies/ma_crossover.py` - 5m resumption detection
- `./trading_bot_v2/strategies/liquidation_capture.py` - 1m/5m cascade detection
- `./trading_bot_v2/strategies/grid_trading.py` - 1m fill tracking
- `./trading_bot_v2/strategy_manager.py` - Pass execution TF data to strategies
</output>

<verification>
1. Run bot and count signal generation frequency (should INCREASE)
2. Verify regime detection logs still show 1h-only regime changes
3. Verify no signal direction flips from 1m data
4. Test Mean Reversion: should trigger more often on 5m RSI extremes
5. Test Liquidation Capture: should detect cascades faster on 1m
6. Monitor for false positives - if they increase, the issue is elsewhere
</verification>

<success_criteria>
- Trade frequency increases measurably
- Signal quality maintained (regime-appropriate trades only)
- No confluence deadlock (trades aren't blocked by TF disagreement)
- Each strategy has clear TF responsibilities documented
- 1m/5m never affects regime or strategy enablement
- Easier to debug (clear TF ownership)
</success_criteria>

<expected_outcomes_from_document>
From the source document, implementing this should achieve:
1. Higher trade frequency without loosening risk controls
2. More stable regime behavior
3. Cleaner architecture (clear TF ownership)
4. Better debuggability (fewer hidden blockers)
</expected_outcomes_from_document>
