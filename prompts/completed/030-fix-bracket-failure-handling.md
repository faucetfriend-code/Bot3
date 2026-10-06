<objective>
Fix bracket order failure handling to prevent naked positions. This is QUALITY CONTROL ISSUE #4 - the MOST DANGEROUS remaining flaw that could cause catastrophic losses.
</objective>

<context>
When stop-loss order placement fails after entry, the bot currently logs the error but leaves the position unprotected. This creates naked positions that can lose unlimited amounts.

Reference: "G:\ai-workspace\Bot 3\research\quality control for the fixes.txt" - Issue #8

Current dangerous code:
```python
# Entry succeeds
order = self.client.place_order(...)

# Stop-loss fails
stop_order = self.client.place_order(...)  # FAILS
logger.error(f"Stop-loss failed")  # JUST LOGS

# Position left naked! 💀
```

This violates the fundamental principle: never have unprotected positions.
</context>

<requirements>
1. When stop-loss placement fails after entry, immediately close the position
2. Raise RuntimeError to prevent further execution
3. Add comprehensive error handling for all bracket order failures
4. Log critical alerts for manual intervention if needed
5. Ensure TP failures don't prevent stop-loss protection
</requirements>

<implementation>
Update _execute_standard_signal() bracket order handling:

```python
try:
    # Place ENTRY order
    order = self.client.place_order(symbol, side, quantity, 'market')
    logging.info(f"Entry order placed: {symbol} {side} {quantity} - {order}")

    if not order or not order.get('id'):
        logging.error(f"Entry order failed: {order}")
        return

    # CRITICAL SAFETY: Place STOP-LOSS order (MANDATORY)
    stop_side = 'sell' if side == 'buy' else 'buy'
    try:
        stop_order = self.client.place_order(
            symbol,
            stop_side,
            quantity,
            'limit',  # Use limit for stop-loss since stop orders not supported
            price=signal.stop_loss
        )
        logging.info(f"STOP-LOSS placed: {symbol} {stop_side} {quantity} @ {signal.stop_loss}")
    except Exception as e:
        # CRITICAL: Stop-loss failed - CLOSE POSITION IMMEDIATELY
        logging.critical(f"STOP-LOSS FAILED AFTER ENTRY: {e}")
        try:
            # Attempt to close the position
            close_side = 'sell' if side == 'buy' else 'buy'
            close_order = self.client.place_order(symbol, close_side, quantity, 'market')
            logging.critical(f"EMERGENCY CLOSE executed: {symbol} {close_side} {quantity}")
        except Exception as close_error:
            logging.critical(f"EMERGENCY CLOSE ALSO FAILED: {close_error}")
        
        # Raise error to prevent any further execution
        raise RuntimeError(f"Stop-loss protection failed for {symbol} - position closed")

    # Place TAKE-PROFIT order (optional - don't fail if this fails)
    tp_order = None
    if signal.take_profit:
        tp_side = 'sell' if side == 'buy' else 'buy'
        try:
            tp_order = self.client.place_order(
                symbol,
                tp_side,
                quantity,
                'limit',
                price=signal.take_profit
            )
            logging.info(f"TAKE-PROFIT placed: {symbol} {tp_side} {quantity} @ {signal.take_profit}")
        except Exception as e:
            logging.warning(f"Take-profit order failed: {e}")
            # Don't fail execution - TP is optional, stop-loss is mandatory

    # Persist order IDs
    self._save_order_ids(
        symbol, 
        order['id'], 
        stop_order.get('id') if stop_order else None,
        tp_order.get('id') if tp_order else None
    )

except Exception as e:
    logging.error(f"Error executing standard signal: {e}")
    raise
```

Add emergency position closing method:

```python
def _emergency_close_position(self, symbol: str, side: str, quantity: float):
    """
    Emergency position closure when stop-loss protection fails.
    
    Args:
        symbol: Trading symbol
        side: Original entry side ('buy' or 'sell')
        quantity: Position quantity
    """
    try:
        close_side = 'sell' if side == 'buy' else 'buy'
        close_order = self.client.place_order(symbol, close_side, quantity, 'market')
        logging.critical(f"EMERGENCY POSITION CLOSE: {symbol} {close_side} {quantity} - {close_order}")
        return close_order
    except Exception as e:
        logging.critical(f"EMERGENCY CLOSE FAILED: {symbol} - {e}")
        raise
```
</implementation>

<output>
Implement critical bracket order failure handling:
- When stop-loss fails after entry, immediately close the position
- Raise RuntimeError to halt execution and prevent further trades
- Add emergency position closing method
- Ensure TP failures don't compromise stop-loss protection
- Add comprehensive logging for critical alerts

The bot will never have unprotected positions again.
</output>

<verification>
After implementation:
1. Test with simulated stop-loss failure (force error)
2. Verify position is immediately closed
3. Check that RuntimeError is raised to halt execution
4. Test normal operation still works
5. Verify logging provides clear alerts
</verification>

<success_criteria>
- Stop-loss placement failures trigger immediate position closure
- RuntimeError halts execution when protection fails
- Emergency close method works reliably
- TP failures don't compromise stop-loss protection
- Comprehensive logging for critical alerts
- Bot never operates with unprotected positions
</success_criteria>