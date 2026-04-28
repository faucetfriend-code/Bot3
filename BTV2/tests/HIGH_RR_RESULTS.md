# VWAP High R:R Test Results

## Summary

Tested the hypothesis that with 35-40% win rate, high R:R ratios (7:1, 10:1) could make VWAP mean-reversion profitable after costs.

**Result: Hypothesis NOT SUPPORTED**

## Root Cause Analysis

Through detailed debugging, discovered the fundamental issue:

1. **Mean reversion happens fast**: On 5m timeframe, price reverses within 2-3 bars on average (~10-15 minutes)
2. **High R:R is unrealistic**: With atr_target=3.5 (expected 7:1), price needs to travel ~$456 (3.5× $130 ATR) to hit TP
3. **TP never hits**: With proper 1m resolution exit checking, 0% of trades hit TP - all exit via SL

### Evidence
```
Trades with SL hit: 41 (100%)
Trades with TP hit:  0 (0%)

When SL hit, TP was on average 0.61% away from price
Min distance: 0.18%, Max distance: 3.11%
```

### Expected vs Actual
| Metric | Expected | Actual |
|--------|----------|--------|
| Avg Win | ~3.5% (3.5×ATR) | 0.17% |
| Avg Loss | ~0.5% (0.5×ATR) | 0.21% |
| Profit Factor | 7:1 × 40% = 2.8 | 0.35 |

## Tests Performed

### 1. ATR-based TP with various R:R ratios
```
Config              R:R   Trades  WR%    PF    Net%
sd2.5_atr_7:1       7:1     58   32.8  0.35  -13.7%
sd2.5_atr_10:1     10:1     58   32.8  0.35  -13.7%
sd2.5_atr_15:1     15:1     58   32.8  0.35  -13.7%
sd2.5_stop07_7:1    7:1     58   34.5  0.36  -13.7%
sd2.5_stop10_7:1    7:1     57   36.8  0.32  -14.6%
sd2.5_SFP_7:1       7:1     65   24.6  0.27  -16.0%
sd2.0_atr_7:1       7:1    155   23.2  0.25  -36.4%
sd1.5_atr_7:1       7:1    196   29.1  0.29  -41.3%
```

### 2. VWAP-based TP (theoretical mean-reversion target)
```
Config                    Trades  WR%    PF    Net%
tp_mode='atr' (baseline)    51    39.2  0.48  -10.6%
tp_mode='vwap'              51    39.2  0.52  -10.6%
tp_mode='vwap', stop=1.0    50    44.0  0.40  -12.5%
bull_pullback + vwap        38    42.1  0.84   -6.7%
cross + vwap                31     9.7  0.09   -9.0%
momentum + vwap             55    14.5  0.15  -14.3%
sd=2.0 + vwap              145    27.6  0.33  -33.4%
sd=1.5 + vwap              187    24.1  0.29  -41.1%
```

## Conclusion

**High R:R does not work for VWAP mean-reversion because:**

1. The strategy's fundamental premise (price returns to VWAP) happens quickly (2-3 bars)
2. High R:R targets like 7:1 require price to travel 3.5×ATR (~$456), which takes many bars
3. By then, the mean-reversion signal has already reversed and hit SL

**Options for moving forward:**

1. **Lower R:R**: Use 1:1 or 1.5:1 R:R that actually gets hit (e.g., 0.3×ATR stop, 0.3-0.5×ATR target)
2. **Different timeframe**: Use higher timeframes (15m, 1h) where trends develop slower
3. **Trailing stops**: Let winners run with trailing instead of fixed TP
4. **Momentum mode**: Bull pullback showed best PF (0.84) - higher follow-through than pure mean-reversion

## Best Result Observed
```
bull_pullback + vwap TP: 
- Net: -6.7%
- WR: 42.1%
- PF: 0.84
- Trades: 38
```

Still not profitable, but closer than pure mean-reversion. The momentum-based approach (riding trends rather than fading extremes) performs better in this data.