<objective>
Debug and fix the Grid Order JSON format error that is causing complete failure of grid trading functionality. This is a Priority 1 critical issue where all SUI grid orders (12+ attempts) are failing with: "API error: Json deserialize error: invalid type: string "0.8809692", expected struct StopOrderInfo"

The diagnostic report identified that grid order placement is completely non-functional due to StopOrderInfo structure serialization format mismatch. This prevents the grid trading strategy from executing any orders, making one of the core trading strategies completely broken.
</objective>

<context>
This is a trading bot v2 system with an 8-strategy trading engine including grid trading, mean reversion, MA crossover, and advanced strategies. The grid trading strategy relies on placing multiple stop orders around a center price at calculated spacing levels.

The system uses:
- Pacifica API for order placement and management
- Grid trading strategy with configurable levels (8 levels, 0.4x ATR spacing)
- JSON serialization for API communication
- StopOrderInfo structure for stop order placement
- Error handling and retry logic for failed orders

@trading_bot_v2/trading_bot.py (lines 2071, 2124, 1685)
@trading_bot_v2/pacifica_client.py (for API order placement)

The JSON deserialization error suggests that the order payload structure doesn't match the Pacifica API expectations for StopOrderInfo. All SUI grid orders are failing, indicating a systematic format issue rather than isolated order problems.
</context>

<requirements>
1. Capture and analyze the exact order payload being sent to Pacifica API
2. Identify the specific StopOrderInfo structure requirements from Pacifica API
3. Fix the JSON serialization format for stop orders
4. Add comprehensive order payload validation before submission
5. Ensure price values are correctly formatted (not as strings when numbers expected)
6. Test grid order placement with small test orders to verify the fix
7. Maintain all existing grid trading logic while fixing the format issue
8. Add detailed logging for order payload debugging

The fix must handle:
- StopOrderInfo structure fields and data types
- Price format (numeric vs string issues)
- Order payload serialization
- Grid level order calculations
- Error handling and retry logic
- Integration with existing grid trading strategy

Why this matters: Grid trading is one of the core strategies that generates consistent trading income. Without working order placement, the entire grid trading strategy is useless, reducing the bot's diversification and profitability potential.
</requirements>

<implementation>
Thoroughly analyze the grid order placement code in trading_bot.py around lines 2071 and 2124. Focus on:

1. **Order Payload Structure**: Examine the exact JSON being generated for StopOrderInfo
2. **Data Type Issues**: Identify why price values are being sent as strings instead of numbers
3. **Serialization Method**: Review how StopOrderInfo is being converted to JSON
4. **API Documentation Alignment**: Compare with Pacifica API expected structure
5. **Grid Level Calculations**: Ensure all grid order parameters are correctly calculated

Key areas to investigate:
- `_place_grid_orders` method (around line 2071)
- Grid order payload construction
- StopOrderInfo class/structure definition
- Price formatting and type conversion
- JSON serialization method used
- Order validation before API submission

Debug steps to implement:
1. Add detailed logging to capture exact order payload before submission
2. Log the StopOrderInfo structure in detail
3. Add validation to check data types before serialization
4. Compare payload with working examples from other order types
5. Test with minimal order to isolate the format issue

What to avoid:
- Changing the grid trading strategy logic or algorithms
- Modifying risk management or position sizing
- Changing grid level calculations (only fix formatting)
- Removing existing error handling or retry logic
- Making breaking changes to other order types

Why these constraints matter: The grid trading algorithm is working correctly - it's generating signals and calculating order levels properly. The issue is purely in the API communication format, so we should fix only the serialization and data structure to match API expectations.
</implementation>

<output>
Modify the file: `trading_bot_v2/trading_bot.py`

Key changes required:

1. **Add order payload debugging** (around line 2071):
```python
# Add detailed logging before order submission
logger.debug(f"Grid order payload: {json.dumps(order_data, indent=2)}")
logger.debug(f"StopOrderInfo structure: {stop_order_info}")
```

2. **Fix data type conversion** (prevent string prices):
```python
# Ensure numeric values remain numeric
if isinstance(price, str):
    price = float(price)
if isinstance(quantity, str):
    quantity = float(quantity)
```

3. **Validate order payload structure**:
```python
def validate_stop_order_payload(self, order_data):
    """Validate order payload matches StopOrderInfo structure"""
    required_fields = ['symbol', 'side', 'type', 'quantity', 'price', 'stopPrice']
    for field in required_fields:
        if field not in order_data:
            raise ValueError(f"Missing required field: {field}")
    
    # Validate data types
    if not isinstance(order_data['price'], (int, float)):
        raise ValueError(f"Price must be numeric, got {type(order_data['price'])}")
    if not isinstance(order_data['stopPrice'], (int, float)):
        raise ValueError(f"StopPrice must be numeric, got {type(order_data['stopPrice'])}")
```

4. **Fix StopOrderInfo serialization** (around lines 2071, 2124):
```python
# Ensure proper serialization
stop_order_data = {
    'symbol': symbol,
    'side': side,
    'type': 'STOP_MARKET',
    'quantity': float(quantity),
    'price': float(trigger_price),
    'stopPrice': float(stop_price),
    'timeInForce': 'GTC',
    'reduceOnly': False
}
```

Focus on:
- Line 2071: `_place_grid_orders` method
- Line 2124: Additional grid order placement
- Line 1685: `_execute_grid_signal_coordinated` method
- Any StopOrderInfo structure usage
</output>

<verification>
Before declaring complete, verify your work:

1. **Order Payload Logging**: Confirm detailed order payload is being logged
2. **Data Type Validation**: Test the validation function with various order types
3. **JSON Serialization**: Verify the payload format matches API expectations
4. **Test Order Placement**: Place a small test grid order to verify it succeeds
5. **Error Reduction**: Confirm no more JSON deserialization errors
6. **Integration Test**: Grid trading strategy can place orders successfully
7. **Log Review**: Check for "invalid type: string" errors are resolved

Test with SUI grid trading:
```python
# The grid trading should be able to:
# - Generate grid levels for SUI
# - Place stop orders without JSON errors
# - Successfully submit orders to Pacifica API
# - Receive order confirmations
# - Track grid order status
```

</verification>

<success_criteria>
1. Zero JSON deserialization errors for grid orders
2. StopOrderInfo payload format matches Pacifica API requirements  
3. All data types in order payload are correct (numbers, not strings)
4. Grid order placement success rate > 95%
5. SUI grid trading functionality fully operational
6. No regression in other order types (market orders, limit orders)
7. Comprehensive order payload validation working
8. Detailed logging available for debugging future issues
</success_criteria>