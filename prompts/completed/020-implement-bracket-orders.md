<objective>
Implement bracket order execution for stop-loss and take-profit orders after market entry. This is the MOST CRITICAL fix from the trade management updates document.
</objective>

<context>
The trading bot currently generates signals with stop_loss and take_profit values, but these protective orders are NEVER placed after entry. This creates extreme risk where a single liquidation wick can wipe out the entire account.

Reference: "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\research\trade managemet updates.txt" - Section 1: STOP-LOSS & TAKE-PROFIT ARE NEVER EXECUTED

Current execution code in trading_bot.py::_execute_signal():
```python
order = self.client.place_order(symbol, side, quantity, 'market')
# MISSING: No follow-up calls to place stop-loss or take-profit orders
```

The bot is running in production but completely unprotected.
</context>

<requirements>
1. Modify trading_bot.py::_execute_signal() to place bracket orders after entry
2. After entry order confirmation, place:
   - Stop-loss order (stop order type)
   - Take-profit order (limit order type, if signal.take_profit exists)
3. Add validation: reject signals without stop_loss
4. Persist all order IDs (entry, stop, TP) in database for later management
5. Handle order placement failures gracefully
6. Ensure proper order side logic (sell stop for long positions, buy stop for short positions)
</requirements>

<implementation>
After the entry order is confirmed in _execute_signal():

```python
# Get confirmed entry price
entry_price = float(order.get('price', order.get('avg_price', current_price)))

# Place STOP-LOSS order
stop_side = 'sell' if side == 'buy' else 'buy'
stop_order = self.client.place_order(
    symbol,
    stop_side,
    quantity,
    'stop',
    stop_price=signal.stop_loss
)

# Place TAKE-PROFIT order (if specified)
tp_order = None
if signal.take_profit:
    tp_side = 'sell' if side == 'buy' else 'buy'
    tp_order = self.client.place_order(
        symbol,
        tp_side,
        quantity,
        'limit',
        price=signal.take_profit
    )

# Persist order IDs in database
self._save_order_ids(symbol, order['id'], 
                    stop_order.get('id'), 
                    tp_order.get('id') if tp_order else None)
```

Add validation before execution:
```python
if not signal.stop_loss:
    raise ValueError(f"Signal missing stop-loss: {signal}")
```
</implementation>

<output>
Modify trading_bot.py to implement bracket order execution:
- Update _execute_signal() method
- Add _save_order_ids() helper method
- Ensure proper error handling for order placement failures

Test with a small position to verify stop-loss and take-profit orders are placed correctly.
</output>

<verification>
After implementation:
1. Run bot and generate a test signal
2. Verify in Pacifica UI that entry + stop + TP orders are placed
3. Test stop-loss trigger (small position only)
4. Check database for order ID persistence
5. Verify error handling when stop_loss is missing
</verification>

<success_criteria>
- Stop-loss orders are placed after every entry
- Take-profit orders are placed when specified
- Signals without stop_loss are rejected
- Order IDs are persisted in database
- No breaking changes to existing functionality
- Bot can safely run with real money (not just testnet)
</success_criteria>

---
Completed at: 2026-01-12T21:18:28.487Z
