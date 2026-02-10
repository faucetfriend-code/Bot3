<objective>
Implement the regime caching system that was added but never used. This is QUALITY CONTROL ISSUE #6 - removes dead code and provides actual performance benefits.
</objective>

<context>
Regime caching infrastructure was added to market_regime.py but detect_regime() never uses it. This creates false confidence in performance improvements that don't exist.

Reference: "C:\Users\z_shi\Desktop\N8NPROJECTS\Bot 3\research\quality control for the fixes.txt" - Issue #10

Current issue:
```python
# Cache exists but unused
self._regime_cache = {}
self._pending_regime_changes = {}

# But detect_regime() recalculates every time
regime = self.detect_regime(market_data)  # No caching!
```

Dead code provides false confidence in system performance.
</context>

<requirements>
1. Replace detect_regime() calls with detect_regime_cached()
2. Implement symbol-based caching with TTL enforcement
3. Add regime change confirmation delay (2 bars)
4. Ensure cache is persisted and invalidated properly
5. Remove any unused caching code
</requirements>

<implementation>
Update StrategyManager to use cached regime detection:

```python
# In generate_signals_for_market()
try:
    # Use cached regime detection with confirmation
    regime = self.regime_detector.detect_regime_cached(symbol, regime_data)
except Exception as e:
    logger.error(f"Regime detection failed for {symbol}: {e}")
    regime = MarketRegime.INDECISIVE
```

Ensure detect_regime_cached() is properly implemented in MarketRegimeDetector:

```python
def detect_regime_cached(self, symbol: str, market_data: Dict[str, List[float]]) -> MarketRegime:
    """
    Detect regime with intelligent caching.
    
    - Caches results for 4 hours per symbol
    - Requires 2 consecutive confirmations for regime changes
    - Prevents unnecessary recalculations
    """
    from datetime import datetime
    
    # Check cache first
    if symbol in self._regime_cache:
        cache_entry = self._regime_cache[symbol]
        cache_age_hours = (datetime.now() - cache_entry['timestamp']).total_seconds() / 3600
        
        # Return cached regime if still valid
        if cache_age_hours < self._cache_ttl_hours:
            logger.debug(f"Using cached regime for {symbol}: {cache_entry['regime'].value}")
            return cache_entry['regime']
    
    # Cache miss - detect with confirmation
    new_regime = self._detect_regime_with_confirmation(symbol, market_data)
    
    # Update cache
    self._regime_cache[symbol] = {
        'regime': new_regime,
        'timestamp': datetime.now(),
        'data_hash': self._hash_market_data(market_data)
    }
    
    logger.debug(f"Regime recalculated for {symbol}: {new_regime.value}")
    return new_regime

def _detect_regime_with_confirmation(self, symbol: str, market_data: Dict[str, List[float]]) -> MarketRegime:
    """Detect regime with confirmation to prevent flicker."""
    from datetime import datetime
    
    new_regime = self.detect_regime(market_data)
    
    # Check for pending regime change
    if symbol in self._pending_regime_changes:
        pending = self._pending_regime_changes[symbol]
        
        if pending['regime'] == new_regime:
            # Confirmation received
            if pending['confirmations'] >= 1:  # Require 2 total detections
                del self._pending_regime_changes[symbol]
                logger.info(f"Regime change confirmed for {symbol}: {new_regime.value}")
                return new_regime
            else:
                pending['confirmations'] += 1
                return pending['previous_regime']
        else:
            # Different regime - reset pending
            del self._pending_regime_changes[symbol]
    
    # Check if regime changed from cache
    current_regime = self._regime_cache.get(symbol, {}).get('regime')
    if current_regime and current_regime != new_regime:
        # Start confirmation process
        self._pending_regime_changes[symbol] = {
            'regime': new_regime,
            'previous_regime': current_regime,
            'confirmations': 1,
            'timestamp': datetime.now()
        }
        logger.debug(f"Regime change pending for {symbol}: {current_regime.value} -> {new_regime.value}")
        return current_regime  # Keep current until confirmed
    
    return new_regime
```

Add cache management methods:

```python
def clear_cache(self, symbol: Optional[str] = None):
    """Clear regime cache for symbol or all symbols."""
    if symbol:
        self._regime_cache.pop(symbol, None)
        self._pending_regime_changes.pop(symbol, None)
    else:
        self._regime_cache.clear()
        self._pending_regime_changes.clear()
    logger.info(f"Regime cache cleared for {symbol or 'all symbols'}")

def get_cache_stats(self) -> Dict:
    """Get cache statistics for monitoring."""
    return {
        'cached_symbols': len(self._regime_cache),
        'pending_changes': len(self._pending_regime_changes),
        'cache_ttl_hours': self._cache_ttl_hours
    }
```
</implementation>

<output>
Implement actual regime caching functionality:
- Replace detect_regime() calls with detect_regime_cached()
- Implement symbol-based caching with 4-hour TTL
- Add regime change confirmation (2-bar delay)
- Add cache management methods
- Remove any unused caching infrastructure

Provide real performance benefits instead of dead code.
</output>

<verification>
After implementation:
1. Monitor that regime detection doesn't run every signal generation
2. Verify cached regimes are returned when valid
3. Test regime change confirmation requires 2 detections
4. Check cache statistics and TTL enforcement
5. Confirm performance improvement (fewer regime calculations)
</verification>

<success_criteria>
- Regime detection uses caching infrastructure
- Symbol-based caching with proper TTL enforcement
- Regime changes require confirmation to prevent flicker
- Cache management methods work correctly
- Real performance benefits achieved
- No dead code remains
</success_criteria>