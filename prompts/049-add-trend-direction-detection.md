<objective>
Add trend direction detection to MarketRegimeDetector to enable directional decision-making during grid unwind.

This is the foundation for partial grid unwinding - when market transitions from ranging to trending,
we need to know which direction the trend is heading to decide which positions to keep.
</objective>

<context>
Currently, MarketRegimeDetector detects regime (RANGING_CALM, TRENDING_STRONG, etc.) but does NOT
expose trend direction. When a grid is disabled due to regime change, all positions are closed.

The new feature will:
1. Detect trend direction (up/down/none) using MA crossover logic
2. Allow GridLifecycleManager to selectively close positions against the trend
3. Keep positions that align with the trend for potential profit continuation

Key files:
- `trading_bot_v2/market_regime.py` - MarketRegimeDetector class
- `trading_bot_v2/strategies/ma_crossover.py` - Has trend detection via MA crossover

Reference the project CLAUDE.md for architecture details.
</context>

<requirements>
1. **Add get_trend_direction() method to MarketRegimeDetector**

   ```python
   def get_trend_direction(self, market_data: Dict[str, List[float]]) -> str:
       """
       Determine trend direction based on MA crossover signals.

       Args:
           market_data: OHLCV data dict with 'high', 'low', 'close', 'open' keys

       Returns:
           'up' - 50 MA above 200 MA (bullish trend)
           'down' - 50 MA below 200 MA (bearish trend)
           'none' - Insufficient data or no clear trend
       """
   ```

2. **Implementation Details**
   - Calculate 50-period and 200-period SMA from close prices
   - Return 'up' if SMA_50 > SMA_200 (golden cross territory)
   - Return 'down' if SMA_50 < SMA_200 (death cross territory)
   - Return 'none' if insufficient data (< 200 candles)
   - Use indicators module for SMA calculation (don't duplicate)

3. **Add get_trend_direction_for_symbol() cached version**
   - Similar to `detect_regime_cached`, cache trend direction per symbol
   - Cache TTL: 5 minutes (trends don't change rapidly)
   - This avoids recalculating on every call

4. **Integrate with existing regime detection**
   - When `detect_regime_cached` is called, also update trend direction cache
   - Log trend direction along with regime in debug output

5. **DO NOT modify GridLifecycleManager yet** - that's the next prompt
</requirements>

<constraints>
- Use existing `calculate_sma` from indicators module
- Do NOT create circular imports (don't import MACrossoverStrategy)
- Trend detection must be independent, using just MA calculations
- Fail gracefully with 'none' if data is insufficient
- Do NOT modify any other files besides market_regime.py
</constraints>

<implementation>
Step 1: Read market_regime.py to understand current structure
Step 2: Read indicators.py to find calculate_sma function
Step 3: Add _trend_direction_cache dict to __init__
Step 4: Implement get_trend_direction() method using SMA 50/200
Step 5: Implement get_trend_direction_cached() with TTL
Step 6: Add trend direction logging in detect_regime_cached
Step 7: Test by calling the method directly with sample data
</implementation>

<output>
Modified files:
- `./trading_bot_v2/market_regime.py` - Added trend direction detection
</output>

<verification>
1. Import MarketRegimeDetector and call get_trend_direction() with test data
2. Verify 'up' returned when SMA_50 > SMA_200
3. Verify 'down' returned when SMA_50 < SMA_200
4. Verify 'none' returned with < 200 candles
5. Verify caching works (same result within TTL)
</verification>

<success_criteria>
- get_trend_direction() method added and working
- Returns 'up', 'down', or 'none' based on MA relationship
- Cached version available for performance
- No circular imports or breaking changes
- Ready for GridLifecycleManager integration (next prompt)
</success_criteria>
