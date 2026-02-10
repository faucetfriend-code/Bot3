<objective>
Implement regime caching to prevent unnecessary recalculation every loop. This is PRIORITY #4 from the trade management updates.
</objective>

<context>
Market regime detection runs per symbol, per minute, using overlapping data. This causes unnecessary computation, possible regime flicker, and strategy thrashing.

Reference: "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\research\trade managemet updates.txt" - Section 4

Current code:
```python
regime = self.regime_detector.detect_regime(regime_data)  # Every loop!
```

This is inefficient and can cause strategies to switch regimes too frequently.
</context>

<requirements>
1. Cache regime per symbol with timestamp
2. Recompute regime only on higher timeframe candle close (4h for 4h regime)
3. Prevent regime flicker by requiring confirmation before changes
4. Store cache in database or persistent memory
</requirements>

<implementation>
Add regime caching to MarketRegimeDetector or TradingBot:

```python
class MarketRegimeDetector:
    def __init__(self, ...):
        # ...
        self._regime_cache: Dict[str, Dict] = {}
        self._cache_ttl_hours = 4  # Recalculate every 4 hours
        
    def detect_regime_cached(self, symbol: str, market_data: Dict[str, List[float]]) -> MarketRegime:
        """
        Detect regime with caching to prevent unnecessary recalculation.
        """
        
        # Check cache first
        if symbol in self._regime_cache:
            cache_entry = self._regime_cache[symbol]
            cache_age_hours = (datetime.now() - cache_entry['timestamp']).total_seconds() / 3600
            
            # Return cached regime if still valid
            if cache_age_hours < self._cache_ttl_hours:
                return cache_entry['regime']
        
        # Cache miss or expired - recalculate
        regime = self.detect_regime(market_data)
        
        # Store in cache
        self._regime_cache[symbol] = {
            'regime': regime,
            'timestamp': datetime.now(),
            'data_hash': self._hash_market_data(market_data)  # Optional: detect data changes
        }
        
        logger.debug(f"Regime recalculated for {symbol}: {regime.value}")
        return regime
    
    def _hash_market_data(self, market_data: Dict[str, List[float]]) -> str:
        """Create hash of market data for change detection."""
        import hashlib
        data_str = str(sorted(market_data.items()))
        return hashlib.md5(data_str.encode()).hexdigest()
```

Update TradingBot to use cached regime detection:

```python
def _get_market_regime(self, symbol: str) -> MarketRegime:
    """Get market regime with caching."""
    
    # Get latest market data for regime detection
    market_data = self.multi_tf_fetcher.get_candles_multi_tf(symbol, "4h", limit=50)
    
    # Use cached detection
    regime = self.regime_detector.detect_regime_cached(symbol, market_data)
    
    return regime
```

Add regime change confirmation to prevent flicker:

```python
def detect_regime_with_confirmation(self, symbol: str, market_data: Dict[str, List[float]]) -> MarketRegime:
    """
    Detect regime with confirmation to prevent flicker.
    Requires regime to hold for 2 consecutive checks before accepting change.
    """
    
    new_regime = self.detect_regime(market_data)
    
    # Check if we have a pending regime change
    if symbol in self._pending_regime_changes:
        pending = self._pending_regime_changes[symbol]
        
        if pending['regime'] == new_regime:
            # Confirmation received - accept change
            if pending['confirmations'] >= 1:  # Require 2 total detections
                del self._pending_regime_changes[symbol]
                self._regime_cache[symbol] = {
                    'regime': new_regime,
                    'timestamp': datetime.now()
                }
                logger.info(f"Regime change confirmed for {symbol}: {new_regime.value}")
                return new_regime
            else:
                # Increment confirmation count
                pending['confirmations'] += 1
                return pending['previous_regime']
        else:
            # Different regime detected - reset pending change
            del self._pending_regime_changes[symbol]
    
    # Check if regime changed from cache
    current_regime = self._regime_cache.get(symbol, {}).get('regime')
    if current_regime and current_regime != new_regime:
        # Start pending change process
        self._pending_regime_changes[symbol] = {
            'regime': new_regime,
            'previous_regime': current_regime,
            'confirmations': 1,
            'timestamp': datetime.now()
        }
        logger.debug(f"Regime change pending for {symbol}: {current_regime.value} -> {new_regime.value}")
        return current_regime  # Keep current until confirmed
    
    # No change or first detection
    self._regime_cache[symbol] = {
        'regime': new_regime,
        'timestamp': datetime.now()
    }
    return new_regime
```

Initialize pending changes tracking:

```python
def __init__(self, ...):
    # ...
    self._pending_regime_changes: Dict[str, Dict] = {}
```
</implementation>

<output>
Update market_regime.py to add caching:
- Add _regime_cache dictionary for storing results
- Add detect_regime_cached() method
- Add regime change confirmation logic

Update trading_bot.py to use cached regime detection:
- Replace direct regime calls with cached versions
- Ensure regime is recalculated only on appropriate intervals

Test that regime detection doesn't run every loop and changes require confirmation.
</output>

<verification>
After implementation:
1. Monitor logs to ensure regime detection doesn't run every minute
2. Verify regime changes require confirmation (don't flicker)
3. Check that cache persists across bot restarts
4. Confirm performance improvement (fewer regime calculations)
</verification>

<success_criteria>
- Regime detection cached per symbol with timestamps
- Recalculation only on higher timeframe candle closes
- Regime changes require confirmation to prevent flicker
- Strategies don't thrash on minor regime fluctuations
- Performance improved with reduced unnecessary calculations
</success_criteria>