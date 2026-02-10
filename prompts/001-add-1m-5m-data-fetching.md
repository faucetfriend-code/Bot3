<objective>
Add 1-minute and 5-minute timeframe data fetching to the trading bot infrastructure.

This is a data pipeline expansion - NO strategy logic changes, NO execution behavior changes.
The goal is to make 1m and 5m candle data available for future use while validating the infrastructure works correctly.
</objective>

<context>
The trading bot currently uses three timeframes: 15m, 1h, 4h.
A multi-timeframe execution model is being implemented to increase trade frequency by:
- Using higher timeframes (1h, 15m) for regime/context
- Using lower timeframes (5m, 1m) for precise entry execution

This prompt implements ONLY the data fetching layer - no strategy changes yet.

Key files to modify:
- `trading_bot_v2/multi_timeframe_fetcher.py` - Add 1m/5m to default timeframes
- `trading_bot_v2/api_server.py` - Bootstrap 1m/5m in kline cache
- `trading_bot_v2/pacifica_ws_client.py` - Subscribe to 1m/5m WebSocket streams

Reference the project CLAUDE.md for architecture details.
</context>

<requirements>
1. **MultiTimeframeFetcher Updates**
   - Update default timeframes from `["15m", "1h", "4h"]` to `["1m", "5m", "15m", "1h", "4h"]`
   - The `_interval_to_minutes()` method already handles 1m and 5m correctly
   - Ensure cache TTL is appropriate (shorter for 1m data - consider 60s vs current 300s)
   - Add a `EXECUTION_TIMEFRAMES` constant = `["1m", "5m"]` for documentation
   - Add a `REGIME_TIMEFRAMES` constant = `["15m", "1h", "4h"]` for documentation

2. **API Server Bootstrap Updates**
   - In `BotIntegration.initialize()`, update the `intervals` list to include 1m and 5m
   - Current: `intervals = ["15m", "1h", "4h"]`
   - New: `intervals = ["1m", "5m", "15m", "1h", "4h"]`
   - Consider bootstrap priority: fetch 15m/1h/4h first (needed for regime), then 1m/5m

3. **WebSocket Client Updates** (if applicable)
   - Ensure `pacifica_ws_client.py` subscribes to 1m and 5m kline streams
   - Check `DEFAULT_INTERVALS` or similar constant and add 1m/5m

4. **Logging and Validation**
   - Add INFO log when 1m/5m data is successfully fetched: "Lower timeframe data available: 1m ({count} candles), 5m ({count} candles)"
   - Do NOT wire 1m/5m data into any strategy or regime detection logic
   - This is infrastructure-only

5. **Cache Considerations**
   - 1m candles generate 60x more data than 1h candles
   - Consider a tiered TTL: 1m=60s, 5m=120s, 15m+=300s
   - Or keep simple with single TTL but document the tradeoff
</requirements>

<constraints>
- Do NOT modify any strategy files (mean_reversion.py, ma_crossover.py, etc.)
- Do NOT modify regime detection logic in market_regime.py
- Do NOT modify StrategyManager signal generation
- Do NOT modify TradingBot execution logic
- This is DATA PIPELINE ONLY - fetch and cache, nothing more
- Preserve all existing functionality for 15m/1h/4h
</constraints>

<implementation>
Step 1: Read multi_timeframe_fetcher.py and identify the default timeframes list
Step 2: Add constants for EXECUTION_TIMEFRAMES and REGIME_TIMEFRAMES
Step 3: Update default parameter but keep backward compatibility
Step 4: Read api_server.py and update bootstrap intervals
Step 5: Read pacifica_ws_client.py and verify/update subscription intervals
Step 6: Add logging to confirm 1m/5m availability without consumption
Step 7: Test by starting the server and checking logs for successful 1m/5m fetching
</implementation>

<output>
Modified files:
- `./trading_bot_v2/multi_timeframe_fetcher.py` - Added timeframe constants, updated defaults
- `./trading_bot_v2/api_server.py` - Updated bootstrap intervals
- `./trading_bot_v2/pacifica_ws_client.py` - Updated WebSocket subscriptions (if needed)
</output>

<verification>
1. Start the API server: `python trading_bot_v2/api_server.py`
2. Check logs for: "Lower timeframe data available: 1m (...), 5m (...)"
3. Verify existing 15m/1h/4h functionality is unchanged
4. Confirm no strategy logic was modified (grep for changes in strategies/)
5. Test API endpoint `/api/activity` still works correctly
</verification>

<success_criteria>
- 1m and 5m candle data is fetched and cached
- WebSocket streams include 1m and 5m
- Existing regime detection on 15m/1h/4h is unaffected
- No strategy logic changes
- Server starts without errors
- Logs confirm data availability
</success_criteria>
