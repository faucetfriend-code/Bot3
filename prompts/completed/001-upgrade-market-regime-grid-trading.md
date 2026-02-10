<objective>
Upgrade the market regime detection system to activate Grid Trading strategy in additional safe market environments, implementing a new TRENDING_MODERATE regime while maintaining backward compatibility and system safety.

This upgrade aims to increase Grid Trading activation rate by 30-70% in appropriate ranging markets without exposing the strategy to dangerous trending conditions. The changes will also improve MA Crossover strategy utilization by capturing moderate trends that currently fall into the inactive INDECISIVE zone.
</objective>

<context>
The trading bot currently uses a 4-regime classification system:
- TRENDING_STRONG (ADX > 25): Trend Following + MA Crossover
- RANGING_VOLATILE (ADX < 20, high vol): Grid Trading only
- RANGING_CALM (ADX < 20, low vol): Mean Reversion only
- INDECISIVE (ADX 20-25): No strategies active (too conservative)

**Problem**: Grid Trading is only active in RANGING_VOLATILE, missing safe opportunities in RANGING_CALM. The INDECISIVE zone (ADX 20-25) wastes potential moderate trend signals.

**Solution**:
1. Add internal ADX safety filter to Grid Trading strategy (highest priority)
2. Enable Grid Trading in RANGING_CALM regime (safe with filter #1)
3. Split INDECISIVE range → create new TRENDING_MODERATE regime (ADX 22-28)
4. Adjust ADX thresholds to create space for new regime

Read the project conventions from @CLAUDE.md before starting.

**Files to modify**:
- @market_regime.py - Add TRENDING_MODERATE enum, update thresholds and strategy mapping
- @strategy_manager.py - No changes needed (already uses regime_detector.get_active_strategies)
- @strategies/grid_trading.py - Add internal ADX safety filter
- @trading_bot.py - No changes needed (orchestration unchanged)
- @.env - Add new configuration parameters
</context>

<requirements>
### Priority 1: Internal Grid Safety Filter (CRITICAL - Do First)

**File**: `strategies/grid_trading.py`
**Location**: Inside `generate_signals()` method, at the very beginning

Add ADX safety check that blocks grid opening when trends are developing:

```python
def generate_signals(self, symbol: str, multi_tf_data: Dict, current_price: float) -> List[Signal]:
    """Generate grid trading signals with ADX safety filter."""

    # CRITICAL SAFETY: Never open new grids when trend is developing
    # This is the last line of defense even if regime allows it
    regime_data = multi_tf_data.get("4h", multi_tf_data.get("1h", {}))

    if len(regime_data.get("close", [])) >= 50:
        from indicators import calculate_adx

        adx = calculate_adx(
            regime_data["high"],
            regime_data["low"],
            regime_data["close"],
            period=14
        )

        # Block if ADX exceeds regime threshold (default ~20-25)
        if adx > self.adx_regime_threshold:
            logger.info(
                f"{symbol}: Grid blocked - ADX {adx:.1f} > threshold {self.adx_regime_threshold} "
                f"(trend developing, unsafe for grid)"
            )
            return []

    # ... rest of existing grid logic
```

**Why this matters**: This filter prevents grid trading from opening positions during emerging trends, even if the regime detector mistakenly classifies the market as ranging. It's insurance against false regime detection.

### Priority 2: Add TRENDING_MODERATE Regime

**File**: `market_regime.py`

**Step 2a**: Update MarketRegime enum (around line 21-27):

```python
class MarketRegime(Enum):
    """Market regime classification based on ADX and volatility."""

    TRENDING_STRONG = "trending_strong"        # ADX > 28
    TRENDING_MODERATE = "trending_moderate"    # 22 < ADX ≤ 28  ← NEW
    RANGING_VOLATILE = "ranging_volatile"      # ADX ≤ 22, high volatility
    RANGING_CALM = "ranging_calm"              # ADX ≤ 22, low volatility
    INDECISIVE = "indecisive"                  # transitional / choppy (narrower range)
```

**Step 2b**: Update MarketRegimeDetector thresholds (around line 40-46):

```python
def __init__(self,
             adx_trending_threshold: float = 28.0,      # Changed from 25.0
             adx_ranging_threshold: float = 22.0,       # Changed from 20.0
             adx_moderate_threshold: float = 22.0,      # NEW - lower bound for moderate
             volatility_high_percentile: float = 75.0,
             adx_period: int = 14,
             atr_period: int = 14,
             bb_period: int = 20):
    """
    Initialize MarketRegimeDetector.

    Args:
        adx_trending_threshold: ADX above this = TRENDING_STRONG (default: 28)
        adx_ranging_threshold: ADX below this = ranging market (default: 22)
        adx_moderate_threshold: ADX above this but below trending = TRENDING_MODERATE (default: 22)
        ...
    """
    self.adx_trending = adx_trending_threshold
    self.adx_ranging = adx_ranging_threshold
    self.adx_moderate = adx_moderate_threshold  # NEW
    # ... rest of init
```

**Step 2c**: Update detect_regime() logic (around line 72-146):

Replace the existing regime detection logic with:

```python
def detect_regime(self, market_data: Dict[str, List[float]]) -> MarketRegime:
    """
    Detect current market regime from market data.

    Regime classification:
    - ADX > 28: TRENDING_STRONG
    - 22 < ADX ≤ 28: TRENDING_MODERATE (new)
    - ADX ≤ 22 + high volatility: RANGING_VOLATILE
    - ADX ≤ 22 + low volatility: RANGING_CALM
    - Transitional/choppy: INDECISIVE
    """
    # ... existing validation code (lines 86-114) unchanged

    # Step 1: Calculate ADX
    try:
        adx = calculate_adx(highs, lows, closes, period=self.adx_period)
    except Exception as e:
        logger.error(f"Error calculating ADX: {e}")
        raise

    # Step 2: If ADX indicates strong trend, return TRENDING_STRONG
    if adx > self.adx_trending:
        logger.info(f"Regime: TRENDING_STRONG (ADX={adx:.2f} > {self.adx_trending})")
        return MarketRegime.TRENDING_STRONG

    # Step 3: Check for TRENDING_MODERATE (new regime)
    if self.adx_moderate < adx <= self.adx_trending:
        logger.info(
            f"Regime: TRENDING_MODERATE (ADX={adx:.2f} between "
            f"{self.adx_moderate}-{self.adx_trending})"
        )
        return MarketRegime.TRENDING_MODERATE

    # Step 4: ADX ≤ 22 - market is ranging, determine volatility level
    if adx <= self.adx_ranging:
        try:
            volatility_score = self._calculate_volatility_score(highs, lows, closes)
        except Exception as e:
            logger.error(f"Error calculating volatility: {e}")
            return MarketRegime.RANGING_CALM

        # Classify ranging market by volatility
        if volatility_score > self.volatility_percentile:
            logger.info(
                f"Regime: RANGING_VOLATILE (ADX={adx:.2f} ≤ {self.adx_ranging}, "
                f"volatility={volatility_score:.1f}% > {self.volatility_percentile}%)"
            )
            return MarketRegime.RANGING_VOLATILE
        else:
            logger.info(
                f"Regime: RANGING_CALM (ADX={adx:.2f} ≤ {self.adx_ranging}, "
                f"volatility={volatility_score:.1f}% ≤ {self.volatility_percentile}%)"
            )
            return MarketRegime.RANGING_CALM

    # Step 5: Fallback to INDECISIVE (shouldn't reach here with new logic)
    logger.info(f"Regime: INDECISIVE (ADX={adx:.2f} - transitional)")
    return MarketRegime.INDECISIVE
```

**Step 2d**: Update get_active_strategies() mapping (around line 213-233):

```python
def get_active_strategies(self, regime: MarketRegime) -> List[str]:
    """
    Map market regime to list of active strategy names.

    Updated mapping with TRENDING_MODERATE and Grid in RANGING_CALM:
    - TRENDING_STRONG: Trend Following + MA Crossover (aggressive trend)
    - TRENDING_MODERATE: MA Crossover + Trend Following (lighter trend exposure)
    - RANGING_VOLATILE: Grid Trading only (high volatility oscillations)
    - RANGING_CALM: Mean Reversion + Grid Trading (quiet oscillations)
    - INDECISIVE: Grid Trading (optional - can be [] for conservative approach)
    """
    regime_strategy_map = {
        MarketRegime.TRENDING_STRONG: ["TrendFollowing", "MACrossover"],
        MarketRegime.TRENDING_MODERATE: ["MACrossover", "TrendFollowing"],  # NEW
        MarketRegime.RANGING_VOLATILE: ["GridTrading"],
        MarketRegime.RANGING_CALM: ["MeanReversion", "GridTrading"],        # Grid added
        MarketRegime.INDECISIVE: ["GridTrading"]  # Optional - use [] for conservative
    }

    strategies = regime_strategy_map.get(regime, [])
    logger.debug(f"Regime {regime.value} -> Active strategies: {strategies}")

    return strategies
```

**Step 2e**: Update get_strategy_weights() mapping (around line 235-262):

```python
def get_strategy_weights(self, regime: MarketRegime) -> Dict[str, float]:
    """
    Get strategy allocation weights for current regime.

    Updated weights with TRENDING_MODERATE regime.
    """
    weight_map = {
        MarketRegime.TRENDING_STRONG: {
            "TrendFollowing": 0.7,
            "MACrossover": 0.3
        },
        MarketRegime.TRENDING_MODERATE: {      # NEW
            "MACrossover": 0.6,
            "TrendFollowing": 0.4
        },
        MarketRegime.RANGING_VOLATILE: {
            "GridTrading": 1.0
        },
        MarketRegime.RANGING_CALM: {
            "MeanReversion": 0.7,              # Updated
            "GridTrading": 0.3                 # NEW
        },
        MarketRegime.INDECISIVE: {
            "GridTrading": 1.0                 # Optional - use {} for conservative
        }
    }

    weights = weight_map.get(regime, {})
    logger.debug(f"Regime {regime.value} -> Strategy weights: {weights}")

    return weights
```

### Priority 3: Configuration Updates

**File**: `.env`

Add new configuration parameters:

```env
# Market Regime Detection (Updated for TRENDING_MODERATE)
ADX_TRENDING_THRESHOLD=28.0           # Changed from 25.0
ADX_RANGING_THRESHOLD=22.0            # Changed from 20.0
ADX_MODERATE_THRESHOLD=22.0           # NEW - lower bound for moderate trend
VOLATILITY_HIGH_PERCENTILE=75.0

# Grid Trading - Additional Safety Controls
GRID_ADX_THRESHOLD=25.0               # Internal safety filter (blocks grid if ADX > this)
ALLOW_GRID_IN_CALM=true               # Enable grid in RANGING_CALM regime
ALLOW_GRID_IN_INDECISIVE=true         # Enable grid in INDECISIVE (optional, can be false)
```

### Priority 4: Backward Compatibility

Ensure these changes maintain backward compatibility:
- All existing strategies continue to work unchanged
- Configuration parameters have sensible defaults
- Logging clearly shows regime transitions and strategy activation
- No breaking changes to Signal generation or execution flow
</requirements>

<implementation>
Follow this implementation order strictly:

1. **First**: Add internal ADX safety filter to `strategies/grid_trading.py`
   - This must be done BEFORE enabling grid in new regimes
   - Test independently to ensure it blocks grid when ADX > threshold

2. **Second**: Update `market_regime.py` in this order:
   - Add TRENDING_MODERATE to MarketRegime enum
   - Update MarketRegimeDetector.__init__() with new thresholds
   - Update detect_regime() logic
   - Update get_active_strategies() mapping
   - Update get_strategy_weights() mapping

3. **Third**: Update `.env` with new configuration parameters

4. **Fourth**: DO NOT modify `strategy_manager.py` or `trading_bot.py`
   - These files already work correctly with the regime detector
   - strategy_manager.py calls regime_detector.get_active_strategies()
   - No changes needed to orchestration logic

**What to avoid and WHY**:
- Never remove existing regimes - breaks backward compatibility
- Never allow Grid Trading without ADX filter - exposes to trending market risk
- Never modify strategy_manager.py signal resolution logic - works correctly as-is
- Never change database schema - not needed for this upgrade
- Don't add complex nested conditions - keep regime detection logic linear and readable
</implementation>

<verification>
After implementing all changes, verify the following:

**Test 1: Internal Grid Filter Works**
```python
# In strategies/grid_trading.py, simulate high ADX scenario
# Expected: generate_signals() returns [] when ADX > threshold
```

**Test 2: Regime Classification Updated**
```python
# Feed sample data to MarketRegimeDetector
# ADX=30 → TRENDING_STRONG
# ADX=25 → TRENDING_MODERATE (new)
# ADX=21, high vol → RANGING_VOLATILE
# ADX=21, low vol → RANGING_CALM
# ADX=15 → RANGING_CALM or RANGING_VOLATILE (based on volatility)
```

**Test 3: Strategy Activation Correct**
```python
# TRENDING_STRONG → ["TrendFollowing", "MACrossover"]
# TRENDING_MODERATE → ["MACrossover", "TrendFollowing"] (new)
# RANGING_VOLATILE → ["GridTrading"]
# RANGING_CALM → ["MeanReversion", "GridTrading"] (grid added)
# INDECISIVE → ["GridTrading"] or [] (based on ALLOW_GRID_IN_INDECISIVE)
```

**Test 4: Log Output Validation**
- Check logs show regime transitions clearly
- Verify Grid blocking messages when ADX exceeds threshold
- Confirm strategy activation messages match new mapping

**Test 5: Run Bot for 10 Minutes**
- Start bot with `python api_server.py`
- Monitor web interface at http://localhost:8000
- Check "Current Activity" table shows new regimes
- Verify Grid Trading appears in RANGING_CALM markets
- Ensure no Grid positions open when ADX > 25

**Test 6: Check Health Monitor**
```bash
python monitor_bot.py
```
- All 6 checks should pass
- Signal generation should show increased activity
</verification>

<success_criteria>
1. Grid Trading internal ADX filter implemented and blocks grid when ADX > threshold
2. TRENDING_MODERATE regime added to MarketRegime enum
3. ADX thresholds updated to 28/22 (from 25/20)
4. detect_regime() correctly classifies all 5 regimes
5. get_active_strategies() returns correct strategies for each regime:
   - RANGING_CALM includes both MeanReversion and GridTrading
   - TRENDING_MODERATE includes MACrossover and TrendFollowing
6. get_strategy_weights() provides weights for all regimes including new TRENDING_MODERATE
7. Configuration parameters added to .env with defaults
8. All existing tests still pass (no regression)
9. Bot runs without errors for 10+ minutes
10. Logs clearly show regime transitions and grid blocking events
11. Zero cases of Grid positions opening when ADX > 28
12. Grid Trading activation frequency increases 30-70% in ranging markets (observable in logs)

**Expected outcome**: System activates Grid Trading in more appropriate scenarios (RANGING_CALM + optionally INDECISIVE) while maintaining safety through internal ADX filter. MA Crossover strategy now activates in moderate trends (ADX 22-28) instead of staying idle.
</success_criteria>

<output>
Modified files:
- `./trading_bot_v2/strategies/grid_trading.py` - Add internal ADX safety filter at start of generate_signals()
- `./trading_bot_v2/market_regime.py` - Add TRENDING_MODERATE regime, update thresholds, update strategy mappings
- `./trading_bot_v2/.env` - Add new configuration parameters

No changes needed:
- `./trading_bot_v2/strategy_manager.py` - Already uses regime_detector.get_active_strategies()
- `./trading_bot_v2/trading_bot.py` - Orchestration logic unchanged
</output>

<validation>
Before completing this task:

1. Read all 3 files to understand current implementation
2. Implement Priority 1 (Grid safety filter) first - test it independently
3. Implement Priority 2 (regime updates) second - test regime classification
4. Update .env configuration third
5. Run bot for 10 minutes monitoring logs for regime transitions
6. Verify Grid Trading never activates when ADX > 28
7. Confirm increased Grid Trading activity in RANGING_CALM markets
8. Check monitor_bot.py health check passes all 6 checks

Only mark complete after all verification tests pass.
</validation>
