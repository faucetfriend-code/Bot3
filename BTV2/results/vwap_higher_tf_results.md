# VWAP Scalping - Higher Timeframe Test Results

## Test Configuration

**Data:** BTCUSDT 5m for 2023-2024  
**Exit Resolution:** 1m  
**MTF Changes:**
- VWAP alignment: 15min → 60min (1h)
- EMA/ADX filter: 60min → 240min (4h)

## Test Results Summary

### MTF Comparison (Original vs Higher)

| Configuration | Net Return | Win Rate | Profit Factor | Trades |
|--------------|------------|----------|--------------|--------|
| Original MTF (15m/1h) - tight params | -83.6% | 23.1% | 0.19 | 156 |
| Higher MTF (1h/4h) - tight params | **-75.7%** | **23.9%** | **0.20** | **138** |
| Original MTF (15m/1h) - original params | -226.0% | 22.1% | 0.20 | 498 |
| Higher MTF (1h/4h) - original params | **-196.5%** | **24.0%** | **0.21** | **420** |

### Goals Analysis

| Goal | Target | Achieved? |
|------|--------|-----------|
| Reduce trade count | 100-300 | ✅ YES (138-420 with higher MTF) |
| Improve win rate | >40% | ❌ NO (23-24%) |
| Positive/break-even net return | >0% | ❌ NO (-75% to -196%) |

## Key Findings

1. **Higher MTF reduces trades** - The 1h VWAP + 4h EMA/ADX configuration produces fewer signals than the original 15m/1h setup, as expected.

2. **Higher MTF performs slightly better** - With the same parameters, higher MTF shows ~10-15% improvement in net return.

3. **Win rates remain low** - Regardless of MTF configuration, win rates stay around 22-24%, far below the 40% target.

4. **Strategy fundamentally loses in 2023-2024** - The issue is not just MTF configuration but likely:
   - Market conditions (strong trending vs ranging)
   - Entry/exit logic fit for the period
   - Risk management parameters

## Best Configuration Found

```python
# Best performing with higher MTF
{
    "entry_mode": "mean_reversion",
    "sd_threshold": 2.5,
    "adx_max": 18.0,
    "rsi_max": 35.0,
    "volume_mult": 2.5,
    "atr_stop": 0.5,
    "atr_target": 2.5,
    "use_htf_vwap": True,
    "use_htf_ema": True,
    "htf_adx_max": 30.0,
    "htf_vwap_interval": "60min",   # 1h VWAP
    "htf_ema_interval": "240min",  # 4h EMA/ADX
}
```

**Results:** Net: -75.7%, WR: 23.9%, PF: 0.20, Trades: 138

## Recommendations

1. **MTF change works as intended** - Higher MTF reduces trades and slightly improves performance.

2. **Strategy needs fundamental changes** - The low win rate suggests the entry logic may need:
   - Different entry modes (deviation, momentum)
   - Opposite direction entries (trend following instead of mean reversion)
   - Dynamic parameters based on market regime

3. **Alternative approaches to try:**
   - Use trend-following entry mode instead of mean reversion
   - Invert the strategy (fade breakouts instead of reversals)
   - Add more sophisticated regime detection
   - Try different market periods (2020-2022 vs 2023-2024)

## Code Changes Made

Modified `strategies.py` to add configurable MTF intervals:
- Added `htf_vwap_interval` parameter (default: "15min")
- Added `htf_ema_interval` parameter (default: "60min")

These can now be set to "60min" or "240min" for higher timeframe testing.
