# VWAP SD=3.45 Yearly Backtest Report

## Historical Best Configuration

```python
sd_threshold = 3.45
entry_mode = "mean_reversion"
adx_max = 25
rsi_max = 50
volume_mult = 2.0
atr_stop = 0.7
atr_target = 3.5
pullback_bars = 3
use_session_filter = True
use_htf_vwap = True
use_htf_ema = False
htf_adx_max = 25
```

## Claimed Performance

- Win Rate: 73.3%
- Return: +51.3%
- Max Drawdown: -5.17%
- Sharpe: 1.473

## Yearly Results

| Year | Mode | SD | Trades | WR% | PF | Net% | Sharpe | MaxDD% | CAGR% |
|------|------|-----|--------|-----|-----|------|--------|--------|-------|
| 2018 | mean_reversion | 3.45 | 14 | 42.9 | 1.387 | -1.26 | -0.490 | -2.40 | -1.27 |
| 2018 | bull_pullback | 3.45 | 9 | 22.2 | 0.215 | -4.33 | -1.809 | -4.33 | -4.34 |
| 2018 | mean_reversion | 3.0 | 31 | 29.0 | 0.733 | -6.45 | -1.674 | -7.27 | -6.47 |
| 2018 | mean_reversion | 4.0 | 5 | 80.0 | 5.840 | 1.47 | 0.646 | -0.61 | 1.47 |
| 2019 | mean_reversion | 3.45 | 10 | 40.0 | 0.518 | -1.83 | -1.703 | -1.87 | -1.84 |
| 2019 | bull_pullback | 3.45 | 6 | 50.0 | 2.963 | 0.26 | 0.231 | -0.97 | 0.27 |
| 2019 | mean_reversion | 3.0 | 25 | 36.0 | 0.401 | -6.05 | -2.939 | -6.12 | -6.07 |
| 2019 | mean_reversion | 4.0 | 2 | 50.0 | 0.248 | -0.43 | -1.548 | -0.43 | -0.44 |
| 2020 | mean_reversion | 3.45 | 9 | 22.2 | 0.432 | -2.60 | -1.814 | -2.97 | -2.60 |
| 2020 | bull_pullback | 3.45 | 6 | 16.7 | 0.005 | -7.58 | -1.202 | -7.58 | -7.59 |
| 2020 | mean_reversion | 3.0 | 26 | 19.2 | 0.302 | -9.45 | -3.610 | -9.78 | -9.45 |
| 2020 | mean_reversion | 4.0 | 1 | 0.0 | 0.000 | -0.16 | -1.055 | -0.16 | -0.16 |
| 2021 | mean_reversion | 3.45 | 9 | 55.6 | 2.931 | -0.18 | -0.115 | -1.10 | -0.18 |
| 2021 | bull_pullback | 3.45 | 4 | 50.0 | 2.917 | 0.28 | 0.222 | -0.76 | 0.28 |
| 2021 | mean_reversion | 3.0 | 26 | 34.6 | 0.468 | -7.31 | -2.402 | -7.80 | -7.33 |
| 2021 | mean_reversion | 4.0 | 2 | 0.0 | 0.000 | -0.45 | -1.894 | -0.45 | -0.45 |
| 2022 | mean_reversion | 3.45 | 7 | 42.9 | 1.183 | -0.76 | -0.514 | -2.08 | -0.77 |
| 2022 | bull_pullback | 3.45 | 7 | 28.6 | 0.850 | -1.47 | -0.759 | -3.56 | -1.47 |
| 2022 | mean_reversion | 3.0 | 19 | 26.3 | 0.609 | -4.54 | -1.912 | -5.53 | -4.55 |
| 2022 | mean_reversion | 4.0 | 2 | 100.0 | inf | 1.51 | 1.327 | -0.23 | 1.51 |
| 2023 | mean_reversion | 3.45 | 17 | 17.6 | 0.191 | -4.78 | -3.186 | -5.10 | -4.79 |
| 2023 | bull_pullback | 3.45 | 13 | 15.4 | 0.562 | -3.23 | -2.042 | -4.16 | -3.24 |
| 2023 | mean_reversion | 3.0 | 34 | 23.5 | 0.511 | -7.40 | -3.572 | -7.97 | -7.42 |
| 2023 | mean_reversion | 4.0 | 2 | 0.0 | 0.000 | -0.74 | -1.663 | -0.74 | -0.74 |
| 2024 | mean_reversion | 3.45 | 10 | 10.0 | 0.089 | -4.63 | -3.417 | -4.92 | -4.63 |
| 2024 | bull_pullback | 3.45 | 6 | 16.7 | 1.630 | 0.11 | 0.054 | -2.39 | 0.11 |
| 2024 | mean_reversion | 3.0 | 36 | 33.3 | 0.140 | -11.64 | -4.921 | -11.92 | -11.65 |
| 2024 | mean_reversion | 4.0 | 2 | 50.0 | 0.998 | -0.30 | -0.609 | -0.61 | -0.30 |

## Analysis

### SD=3.45 Mean Reversion Performance

- **Average Win Rate**: 33.0%
- **Average Net Return**: -2.29%
- **Average Sharpe**: -1.606
- **Average Max Drawdown**: -2.92%

### Conclusion

SD=3.45 mean_reversion was profitable in only 0/7 years. This suggests the configuration may be overfit to specific market conditions.

### Comparison with Claimed Performance

| Metric | Claimed | Actual (Avg) | Difference |
|--------|---------|--------------|------------|
| Win Rate | 73.3% | 33.0% | -40.3% |
| Net Return | +51.3% | -2.29% | -53.59% |
| Sharpe | 1.473 | -1.606 | -3.079 |
| Max Drawdown | -5.17% | -2.92% | +2.25% |

### SD Comparison

| SD | Avg WR% | Avg Net% | Avg Sharpe |
|-----|---------|----------|------------|
| 3.0 | 28.8 | -7.55 | -3.004 |
| 3.45 | 33.0 | -2.29 | -1.606 |
| 4.0 | 40.0 | 0.13 | -0.685 |