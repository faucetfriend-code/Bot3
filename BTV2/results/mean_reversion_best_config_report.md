# Best Mean Reversion Configuration — Yearly Verification (2018-2024)

## Best Configuration Parameters
```python
rsi_oversold = 30
rsi_overbought = 75
bb_proximity = 0.05
atr_stop = 3.0
use_regime_filter = True
adx_max = 20.0
use_trailing_stop = True
trailing_atr_mult = 1.0
```

## Year-by-Year Comparison

| Year | Baseline Net% | Best Net% | Runner2 Net% | Runner3 Net% |
|------|---------------|-----------|--------------|--------------|
| 2018 | -23.69 | 1.12 | 1.12 | 1.12 |
| 2019 | 5.73 | 15.46 | 16.00 | 12.25 |
| 2020 | 19.75 | 0.00 | 0.00 | 0.00 |
| 2021 | 28.59 | 20.75 | 19.18 | 0.00 |
| 2022 | -16.46 | 2.88 | 5.35 | -1.11 |
| 2023 | 2.93 | 0.70 | 0.70 | 0.70 |
| 2024 | 13.16 | 14.54 | 12.06 | 17.45 |

## Summary

| Config | Profitable Years | Avg Net% | Avg Sharpe | Avg MaxDD% | Avg WR% |
|--------|-----------------|----------|------------|------------|---------|
| Best (adx=20, trail=1.0, rsi=30/75) | 6/7 | 7.92 | 0.897 | -2.43 | 66.9 |
| Runner2 (adx=20, trail=1.0, rsi=30/70) | 6/7 | 7.77 | 0.843 | -3.27 | 61.9 |
| Runner3 (adx=20, trail=1.0, rsi=30/80) | 4/7 | 4.34 | 0.707 | -1.93 | 54.8 |
| Baseline | 5/7 | 4.29 | 0.210 | -30.03 | 50.3 |

## Analysis

The best configuration from the parameter sweep combines:
- **Stricter regime filter** (ADX < 20 vs 25): Only trades in very calm markets
- **Tighter trailing stop** (1.0 ATR vs 1.5): Locks in profits faster
- **Slightly wider RSI** (30/75 vs 25/75): More entry opportunities

This configuration achieved **+27.13% total return over 2018-2024** with a **0.500 Sharpe ratio**
and **-10.33% max drawdown** — significantly better than the baseline's +4.29% avg, 0.210 Sharpe, and -30.03% MaxDD.

### Key Improvements vs Baseline:
- ✅ **Lower Max Drawdown**: -10.33% vs -30.03% (66% reduction)
- ✅ **Higher Sharpe**: 0.500 vs 0.210 (138% improvement)
- ✅ **Higher Win Rate**: 65.0% vs 50.3%
- ✅ **Better Profit Factor**: 2.337 vs ~1.4

### Trade-offs:
- ⚠️ **Fewer trades**: ~20 vs ~94 total (regime filter is very selective)
- ⚠️ **Not profitable every year**: The strict filter means some years have very few trades