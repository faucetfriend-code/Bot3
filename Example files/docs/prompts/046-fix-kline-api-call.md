<objective>
Examine and fix the kline API call implementation to ensure it provides all required information that Pacifica expects. The current implementation appears to be missing critical parameters or formatting that Pacifica's API requires for proper candle data retrieval.

This is critical for accurate historical price data retrieval, which affects trading strategy analysis and backtesting accuracy.
</objective>

<context>
The trading bot integrates with Pacifica.fi API for market data. The kline endpoint (/api/v1/kline) retrieves historical candle data, but the current implementation seems incomplete based on Pacifica's documentation.

Key requirements from Pacifica API docs:
- Required parameters: symbol, interval, start_time
- Optional: end_time (defaults to current time)
- Response format includes: t, T, s, i, o, c, h, l, v, n fields
- Times in milliseconds, prices as decimal strings

Reference the current kline implementation in the codebase to identify what's missing or incorrect.
</context>

<requirements>
1. Locate the current kline API call implementation
2. Compare with Pacifica API documentation requirements
3. Identify missing or incorrect parameters/formatting
4. Fix parameter handling (symbol, interval, start_time, end_time)
5. Ensure proper response parsing and data structure
6. Verify timestamp handling (milliseconds)
7. Confirm price/volume data formatting (decimal strings)

Thoroughly analyze the differences between current implementation and Pacifica requirements to ensure complete compatibility.
</requirements>

<implementation>
Follow established API integration patterns:
- Use proper parameter validation and type conversion
- Handle optional parameters with sensible defaults
- Implement robust error handling for API failures
- Ensure response data matches expected schema
- Use consistent logging for debugging API issues

Explain WHY proper API implementation matters: Incorrect kline data can lead to flawed trading signals and financial losses.
</implementation>

<output>
Update the kline API implementation:

1. **Examine current implementation** - Locate and analyze existing kline calls
2. **Fix API parameters** - Ensure all required fields are included
3. **Update response handling** - Parse data according to Pacifica schema
4. **Add validation** - Verify response format and data integrity
5. **Test integration** - Confirm API calls work with real Pacifica endpoints

Save updated implementation to: ./pacifica_client.py (or relevant API client file)
</output>

<verification>
Before declaring complete:
1. Test API call with sample parameters: symbol=BTC, interval=1m, start_time=timestamp
2. Verify response contains all required fields: t, T, s, i, o, c, h, l, v, n
3. Check timestamp format (milliseconds)
4. Validate price data as decimal strings
5. Confirm error handling for invalid parameters
</verification>

<success_criteria>
- Kline API calls include all required Pacifica parameters
- Response data matches documented schema exactly
- Timestamps are in correct millisecond format
- Price/volume data properly formatted as decimal strings
- API integration passes validation tests
- No missing or malformed data in responses
</success_criteria></content>
</xai:function_call name="write">
<parameter name="filePath">prompts/047-implement-kline-data-storage.md