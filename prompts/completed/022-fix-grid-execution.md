<objective>
Fix grid trading execution so it actually creates laddered limit orders instead of independent market orders. This is PRIORITY #2 from the trade management updates.
</objective>

<context>
Grid strategies generate BUY + SELL signals, but execution treats them as independent market orders. This creates no laddered orders, no grid spacing logic, and allows risk to stack rapidly.

Reference: "G:\ai-workspace\Bot 3\research\trade managemet updates.txt" - Section 2

Current conflict resolution:
```python
# strategy_manager.py::_handle_grid_signals()
return [buy_signal, sell_signal]

# Execution:
for signal in signals:
    self._execute_signal(signal)  # Each becomes market order
```

This defeats the entire purpose of grid trading.
</context>

<requirements>
1. Introduce grid execution path in trading_bot.py::_execute_signal()
2. When signal.strategy == StrategyType.GRID_TRADING, use _execute_grid() instead of market order
3. Grid executor should place limit orders, not market orders
4. Use ATR-based spacing for grid levels
5. Cap total grid exposure per symbol
6. Prevent multiple grids from stacking risk
</requirements>

<implementation>
Add grid execution path in _execute_signal():

```python
if signal.strategy == StrategyType.GRID_TRADING:
    self._execute_grid(signal)
    return
```

Implement _execute_grid() method:

```python
def _execute_grid(self, signal: Signal):
    """Execute grid trading signal with laddered limit orders."""
    
    # Check grid limits
    active_grids = self._count_active_grids(signal.asset)
    if active_grids >= self._max_grids_per_symbol:
        logger.warning(f"Grid limit reached for {signal.asset}: {active_grids}/{self._max_grids_per_symbol}")
        return
    
    # Calculate grid levels using ATR spacing
    grid_levels = self._calculate_grid_levels(signal)
    
    # Place limit orders for each grid level
    for level in grid_levels:
        try:
            order = self.client.place_order(
                signal.asset,
                signal.side,
                level['quantity'],  # Smaller quantity per level
                'limit',
                price=level['price']
            )
            
            # Register grid position
            self._register_grid_position(
                signal.asset, 
                signal.side, 
                level['level_number'], 
                order['id']
            )
            
            logger.info(f"Placed grid {signal.side} order for {signal.asset}: level {level['level_number']} @ {level['price']}")
            
        except Exception as e:
            logger.error(f"Failed to place grid order for {signal.asset}: {e}")
            break  # Stop placing further grid levels if one fails
```

Add helper methods:

```python
def _calculate_grid_levels(self, signal: Signal) -> List[Dict]:
    """Calculate grid price levels using ATR spacing."""
    # Get ATR for spacing
    atr = self._get_atr_for_symbol(signal.asset)
    grid_spacing = atr * 0.5  # ATR multiplier from config
    
    # Calculate levels above/below current price
    levels = []
    current_price = self._get_current_price(signal.asset)
    
    if signal.side == OrderSide.BUY:
        # Buy grid: place below current price
        for i in range(1, self._grid_levels + 1):
            price = current_price - (grid_spacing * i)
            levels.append({
                'level_number': i,
                'price': price,
                'quantity': signal.quantity / self._grid_levels  # Split quantity
            })
    else:
        # Sell grid: place above current price  
        for i in range(1, self._grid_levels + 1):
            price = current_price + (grid_spacing * i)
            levels.append({
                'level_number': i,
                'price': price,
                'quantity': signal.quantity / self._grid_levels
            })
    
    return levels

def _register_grid_position(self, symbol: str, side: str, level: int, order_id: str):
    """Register active grid position for tracking."""
    # Store in database or memory
    pass

def _count_active_grids(self, symbol: str) -> int:
    """Count active grid positions for symbol."""
    # Query database for active grids
    pass
```
</implementation>

<output>
Modify trading_bot.py to implement proper grid execution:
- Add _execute_grid() method for laddered limit orders
- Add grid level calculation using ATR spacing
- Add grid position tracking and limits
- Update _execute_signal() to route grid signals properly

Test by generating a grid signal and verifying limit orders are placed at spaced intervals.
</output>

<verification>
After implementation:
1. Generate a grid trading signal
2. Verify limit orders are placed (not market orders)
3. Check that orders are spaced using ATR
4. Confirm grid position tracking works
5. Test grid limits prevent over-exposure
</verification>

<success_criteria>
- Grid signals create laddered limit orders, not market orders
- Orders are properly spaced using ATR
- Grid exposure is capped per symbol
- Grid positions are tracked for management
- No risk stacking from multiple grid signals
</success_criteria>