<objective>
Implement trade cooldowns and post-trade feedback loop to prevent signal re-firing. This is PRIORITY #5 from the trade management updates.
</objective>

<context>
After execution, StrategyManager is unaware of trades, allowing same signals to re-fire endlessly. No cooldown prevents over-trading and position stacking.

Reference: "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\research\trade managemet updates.txt" - Section 5

Current issue:
```python
# After execution, no feedback to strategy manager
# Same signal can re-fire in next loop
```

This causes rapid position accumulation and strategy conflicts.
</context>

<requirements>
1. Add trade cooldown per strategy/symbol combination
2. Prevent re-entry before cooldown expires
3. Track executed trades for strategy feedback
4. Optional: Implement confidence decay after losses
5. Ensure cooldowns prevent signal spam
</requirements>

<implementation>
Add trade tracking to StrategyManager:

```python
class StrategyManager:
    def __init__(self, ...):
        # ...
        self._trade_cooldowns: Dict[Tuple[str, str], datetime] = {}  # (symbol, strategy) -> cooldown_until
        self._executed_trades: List[Dict] = []  # Track recent trades
        
    def should_skip_signal(self, signal: Signal) -> bool:
        """
        Check if signal should be skipped due to cooldown or other rules.
        """
        
        key = (signal.asset, signal.strategy.value)
        
        # Check cooldown
        if key in self._trade_cooldowns:
            cooldown_until = self._trade_cooldowns[key]
            if datetime.now() < cooldown_until:
                remaining = (cooldown_until - datetime.now()).total_seconds() / 60
                logger.debug(f"Signal skipped for {key}: cooldown {remaining:.1f}min remaining")
                return True
        
        # Check recent trade frequency (anti-spam)
        recent_trades = [t for t in self._executed_trades 
                        if t['symbol'] == signal.asset and 
                           t['strategy'] == signal.strategy.value and
                           (datetime.now() - t['timestamp']).total_seconds() < 300]  # Last 5 min
        
        if len(recent_trades) >= 2:  # Max 2 trades per strategy/symbol per 5 min
            logger.warning(f"Signal skipped for {key}: too many recent trades ({len(recent_trades)})")
            return True
            
        return False
    
    def register_trade_execution(self, signal: Signal, order_result: Dict):
        """
        Register successful trade execution for feedback loop.
        """
        
        key = (signal.asset, signal.strategy.value)
        
        # Set cooldown (strategy-specific)
        cooldown_minutes = self._get_strategy_cooldown(signal.strategy.value)
        self._trade_cooldowns[key] = datetime.now() + timedelta(minutes=cooldown_minutes)
        
        # Record trade
        trade_record = {
            'symbol': signal.asset,
            'strategy': signal.strategy.value,
            'side': signal.side.value,
            'quantity': order_result.get('quantity', 0),
            'price': order_result.get('price', 0),
            'timestamp': datetime.now(),
            'order_id': order_result.get('id'),
            'signal_confidence': signal.confidence
        }
        
        self._executed_trades.append(trade_record)
        
        # Keep only recent trades (last 24 hours)
        cutoff = datetime.now() - timedelta(hours=24)
        self._executed_trades = [t for t in self._executed_trades if t['timestamp'] > cutoff]
        
        logger.info(f"Trade registered: {key} cooldown {cooldown_minutes}min")
    
    def _get_strategy_cooldown(self, strategy_name: str) -> int:
        """
        Get cooldown period in minutes for strategy.
        """
        cooldowns = {
            'mean_reversion': 15,    # 15 min between MR trades
            'ma_crossover': 30,      # 30 min for trend signals
            'grid_trading': 5,       # 5 min for grid adjustments
            'liquidation_capture': 10, # 10 min for liquidation plays
            'trend_following': 60,   # 1 hour for major trend changes
        }
        
        return cooldowns.get(strategy_name, 10)  # Default 10 min
```

Update TradingBot to provide feedback:

```python
def _execute_signal(self, signal: Signal):
    """Execute signal with strategy manager feedback."""
    
    # Check with strategy manager first
    if self.strategy_manager.should_skip_signal(signal):
        logger.info(f"Signal skipped by strategy manager: {signal.asset} {signal.strategy.value}")
        return
    
    # Execute trade...
    order = self.client.place_order(...)
    
    # Register execution with strategy manager
    if order and order.get('id'):
        self.strategy_manager.register_trade_execution(signal, order)
    
    # Continue with bracket orders...
```

Add confidence decay after losses (optional):

```python
def adjust_signal_confidence(self, signal: Signal) -> float:
    """
    Adjust signal confidence based on recent performance.
    """
    
    key = (signal.asset, signal.strategy.value)
    
    # Get recent trades for this strategy/symbol
    recent_trades = [t for t in self._executed_trades 
                    if t['symbol'] == signal.asset and 
                       t['strategy'] == signal.strategy.value and
                       (datetime.now() - t['timestamp']).total_seconds() < 3600]  # Last hour
    
    if not recent_trades:
        return signal.confidence
    
    # Calculate win rate
    profitable_trades = sum(1 for t in recent_trades if t.get('pnl', 0) > 0)
    win_rate = profitable_trades / len(recent_trades) if recent_trades else 0.5
    
    # Decay confidence if win rate < 50%
    if win_rate < 0.5:
        decay_factor = 0.8  # Reduce confidence by 20%
        new_confidence = signal.confidence * decay_factor
        logger.debug(f"Confidence decayed for {key}: {signal.confidence:.2f} -> {new_confidence:.2f} (win_rate={win_rate:.1f})")
        return new_confidence
    
    return signal.confidence
```
</implementation>

<output>
Update strategy_manager.py with trade feedback loop:
- Add _trade_cooldowns tracking
- Add should_skip_signal() method
- Add register_trade_execution() method
- Add strategy-specific cooldown periods

Update trading_bot.py to integrate feedback:
- Check should_skip_signal() before execution
- Call register_trade_execution() after successful trades
- Optional: Implement confidence decay

Test that signals are properly cooled down and don't re-fire immediately.
</output>

<verification>
After implementation:
1. Execute a trade and verify cooldown is set
2. Try to execute same signal again (should be blocked)
3. Wait for cooldown to expire and verify signal works again
4. Check that different strategies have different cooldowns
5. Verify trade history is maintained
</verification>

<success_criteria>
- Trade cooldowns prevent signal re-firing per strategy/symbol
- Strategy manager tracks executed trades
- Cooldown periods are strategy-appropriate
- No endless signal loops
- Feedback loop prevents over-trading
</success_criteria>