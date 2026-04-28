# VWAP Scalping Strategy Test Results

## Summary

Testing was performed on BTCUSDT 5m data for 2024 (105,408 bars) to evaluate:
1. Different entry logic modifications
2. Higher R:R ratios (3:1, 4:1, 5:1)
3. Target: >1000 trades/year, >45% WR, positive return

## Test Results

### Entry Modes Tested:
1. **cross** (original): Price crosses ABOVE/BELOW VWAP
2. **deviation**: Enter when price is X% below/above VWAP
3. **momentum**: Price crossing VWAP + momentum confirmation (price moved in entry direction for N bars)
4. **mean_reversion**: Enter when price is FAR from VWAP (3-4 SD) expecting pullback

### Best Results Found:

| Entry Mode | R:R | Trades | Win Rate | Net Return |
|------------|-----|--------|----------|------------|
| momentum (2 bars) | 4:1 | 75 | 44.0% | -41.0% |
| momentum (2 bars) | 5:1 | 75 | 44.0% | -40.8% |
| momentum (3 bars) | 3:1 | 40 | 35.0% | -21.9% |
| deviation (1.0%) | 3:1 | 61 | 31.1% | -34.7% |
| cross (ADX=25, RSI=50) | 4:1 | 94 | 39.4% | -55.6% |

### Key Observations:

1. **Win Rate**: Best achieved was ~44% with momentum mode (2 bars, R:R 4-5)
2. **Higher R:R**: Increasing R:R from 2:1 to 4:1 improves WR (from ~20% to ~39-44%)
3. **Trade Count**: Proper filters limit trades to ~60-100 per year, not 1000+
4. **Transaction Costs**: With 0.30% round-trip cost, even 44% WR is not enough to be profitable

### Target Criteria Assessment:

**>1000 trades/year**: NOT MET
- With proper filters: ~60-100 trades/year
- Relaxed filters: Too many losing trades

**>45% Win Rate**: NOT MET  
- Best achieved: ~44% (momentum mode)
- Cross mode: ~34-39%
- Mean reversion: ~18-23%

**Positive Net Return**: NOT MET
- Best net: -21.9% (still losing)
- All combinations tested resulted in losses

## Analysis

The target of combining >45% WR with >1000 trades and positive returns could not be achieved because:

1. **Market Regime**: BTC 5m data in 2024 was challenging for VWAP-based scalping
2. **Transaction Costs**: 0.30% per round-trip is too high for the trade frequency needed
3. **Signal Quality**: VWAP cross/breakout signals on 5m have low edge

### Mathematical Reality:
- 44% WR × 4:1 R:R = 0.44×4 - 0.56×1 = 1.76 - 0.56 = 1.20 (gross ratio)
- After costs (0.30% × 75 trades = 22.5%): 1.20 - 22.5 = negative

## Recommendations

For improved results, consider:
1. **Lower timeframe** (1m) for more trade opportunities
2. **Higher R:R** (6:1 or 8:1) to further compensate for low WR
3. **Additional filters** to improve WR (but reduces trade count further)
4. **Different market** (not BTC) or different year
5. **Lower costs** (reduce from 0.30% to 0.10% if possible)

## Updated Parameters

Based on testing, the following parameters performed best:

```python
VS_DEFAULTS = {
    "entry_mode": "momentum",
    "momentum_bars": 2,
    "atr_target": 4.0,  # Higher R:R
    "atr_stop": 1.0,
    "adx_max": 30.0,     # Relaxed for more trades
    "rsi_max": 50.0,     # Relaxed for more trades
    "use_trend_filter": True,
    "use_volume_filter": False,  # Disable for more trades
}
```

Note: Even with best parameters, the strategy still loses money on 2024 BTC 5m data.
