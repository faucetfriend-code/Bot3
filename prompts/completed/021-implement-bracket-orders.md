<objective>
Implement bracket order execution for stop-loss and take-profit orders after market entry. This is the MOST CRITICAL safety fix from the trade management updates.
</objective>

<context>
The trading bot currently places market orders for entry but NEVER places the stop-loss and take-profit orders that should protect the position. This creates extreme risk where a single adverse price movement can wipe out the entire account.

Reference: "G:\ai-workspace\Bot 3\research\trade managemet updates.txt" - Section 1: STOP-LOSS & TAKE-PROFIT ARE NEVER EXECUTED

Current dangerous code:
```python
order = self.client.place_order(symbol, side, quantity, 'market')
# NO STOP-LOSS OR TAKE-PROFIT ORDERS PLACED!
```

The bot is running unprotected in production.
</context>

<requirements>
1. Modify trading_bot.py::_execute_signal() to place bracket orders after entry
2. After entry order confirmation, place:
   - Stop-loss order (stop order type)
   - Take-profit order (limit order type, if signal.take_profit exists)
3. Add validation: reject signals without stop_loss
4. Persist all order IDs (entry, stop, TP) in database
5. Handle order placement failures gracefully
6. Ensure proper order side logic (sell stop for longs, buy stop for shorts)
</requirements>

<implementation>
Update _execute_signal() in trading_bot.py:

```python
def _execute_signal(self, signal: Signal):
    """Execute signal with bracket order protection."""
    
    # Validate stop-loss exists (CRITICAL SAFETY CHECK)
    if not signal.stop_loss:
        raise ValueError(f"CRITICAL: Signal missing stop-loss protection: {signal}")
    
    # Calculate position size and validate
    position_size = self._calculate_position_size(signal)
    if not self._validate_position_size(position_size):
        logger.warning(f"Position size validation failed: {position_size}")
        return
    
    # Get current price for entry
    current_price = self._get_current_price(signal.asset)
    
    # Execute ENTRY order
    side = 'buy' if signal.side == OrderSide.BUY else 'sell'
    order = self.client.place_order(
        signal.asset, 
        side, 
        position_size, 
        'market'
    )
    
    if not order or not order.get('id'):
        logger.error(f"Entry order failed: {order}")
        return
    
    logger.info(f"Entry order placed: {signal.asset} {side} {position_size} @ market")
    
    # CRITICAL: Place BRACKET ORDERS for protection
    
    # Get confirmed entry price
    entry_price = float(order.get('price', order.get('avg_price', current_price)))
    
    # Place STOP-LOSS order
    stop_side = 'sell' if side == 'buy' else 'buy'
    try:
        stop_order = self.client.place_order(
            signal.asset,
            stop_side,
            position_size,
            'stop',
            stop_price=signal.stop_loss
        )
        logger.info(f"STOP-LOSS placed: {signal.asset} {stop_side} {position_size} @ {signal.stop_loss}")
    except Exception as e:
        logger.error(f"CRITICAL: Stop-loss order failed: {e}")
        # Consider canceling entry order here
        raise
    
    # Place TAKE-PROFIT order (if specified)
    tp_order = None
    if signal.take_profit:
        tp_side = 'sell' if side == 'buy' else 'buy'
        try:
            tp_order = self.client.place_order(
                signal.asset,
                tp_side,
                position_size,
                'limit',
                price=signal.take_profit
            )
            logger.info(f"TAKE-PROFIT placed: {signal.asset} {tp_side} {position_size} @ {signal.take_profit}")
        except Exception as e:
            logger.warning(f"Take-profit order failed: {e}")
            # Don't fail execution if TP fails
    
    # Persist order IDs
    self._save_order_ids(
        signal.asset, 
        order['id'], 
        stop_order.get('id') if stop_order else None,
        tp_order.get('id') if tp_order else None
    )
```

Add _save_order_ids helper method:

```python
def _save_order_ids(self, symbol: str, entry_id: str, stop_id: Optional[str], tp_id: Optional[str]):
    """Save order IDs to database for position management."""
    try:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Update positions table with order IDs
        cursor.execute('''
            UPDATE positions 
            SET entry_order_id = ?, stop_order_id = ?, tp_order_id = ?,
                updated_at = ?
            WHERE symbol = ? AND quantity > 0
        ''', (entry_id, stop_id, tp_id, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), symbol))
        
        conn.commit()
        conn.close()
        
        logger.debug(f"Order IDs saved for {symbol}: entry={entry_id}, stop={stop_id}, tp={tp_id}")
        
    except Exception as e:
        logger.error(f"Failed to save order IDs: {e}")
```
</implementation>

<output>
Modify trading_bot.py to implement bracket order execution:
- Update _execute_signal() with bracket order placement after entry
- Add _save_order_ids() method for database persistence
- Add critical validation for stop_loss requirement
- Ensure proper error handling and logging

The bot will now place protective stop-loss orders after every entry, making it safe for production trading.
</output>

<verification>
After implementation:
1. Run bot and generate a test signal
2. Verify in Pacifica UI that 3 orders are placed: entry + stop + TP
3. Check database has order IDs saved
4. Test signal without stop_loss (should be rejected)
5. Verify stop-loss triggers properly (use small test position)
</verification>

<success_criteria>
- ✅ Stop-loss orders placed after EVERY entry (MANDATORY)
- ✅ Take-profit orders placed when specified
- ✅ Signals without stop_loss rejected with error
- ✅ Order IDs persisted in database
- ✅ No breaking changes to existing functionality
- ✅ Bot now has proper risk management for production use
</success_criteria>

---
Completed at: 2026-01-12T21:30:04.663Z
