# Mean Reversion Improved Backtest Report (2018-2024)

## Configurations Tested

### Baseline (Current Best)
```python
rsi_oversold = 25
rsi_overbought = 75
bb_proximity = 0.05
atr_stop = 3.0
use_regime_filter = False
use_trailing_stop = False
```

### + Regime Filter
```python
# Baseline params +
use_regime_filter = True
adx_max = 25.0
```

### + Trailing Stop
```python
# Baseline params +
use_trailing_stop = True
trailing_atr_mult = 1.5
```

### Both Improvements
```python
# Baseline params +
use_regime_filter = True
adx_max = 25.0
use_trailing_stop = True
trailing_atr_mult = 1.5
```

## Year-by-Year Comparison

| Year | Baseline Net% | +Regime Net% | +Trail Net% | Both Net% |
|------|---------------|--------------|-------------|-----------|
| 2018 | -23.69 | 1.39 | 10.52 | 1.39 |
| 2019 | 5.73 | 8.42 | -34.14 | -8.37 |
| 2020 | 19.75 | -4.72 | 6.93 | -4.72 |
| 2021 | 28.59 | -12.73 | 44.20 | -4.92 |
| 2022 | -16.46 | -3.47 | -5.93 | -13.02 |
| 2023 | 2.93 | 5.57 | 0.84 | -0.42 |
| 2024 | 13.16 | 16.53 | -4.23 | 9.96 |

## Summary

| Config | Avg Net% | Avg WR% | Avg Sharpe | Avg MaxDD | Profitable Years |
|--------|----------|---------|------------|-----------|------------------|
| Baseline | 4.29 | 50.3 | 0.210 | -30.03 | 5/7 |
| +Regime | 1.57 | 52.8 | 0.300 | -12.09 | 4/7 |
| +Trail | 2.60 | 53.3 | 0.121 | -28.51 | 4/7 |
| Both | -2.87 | 51.6 | -0.076 | -11.12 | 2/7 |

## Baseline — Year by Year

| Year | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|-----|-----|------|--------|--------|
| 2018 | 7 | 57.1 | 0.529 | -23.69 | -0.717 | -38.63 |
| 2019 | 12 | 33.3 | 1.330 | 5.73 | 0.337 | -43.20 |
| 2020 | 15 | 40.0 | 1.816 | 19.75 | 0.723 | -25.14 |
| 2021 | 15 | 53.3 | 1.685 | 28.59 | 0.784 | -30.11 |
| 2022 | 10 | 60.0 | 0.587 | -16.46 | -0.468 | -32.50 |
| 2023 | 18 | 55.6 | 1.391 | 2.93 | 0.240 | -13.24 |
| 2024 | 17 | 52.9 | 1.464 | 13.16 | 0.574 | -27.40 |

## +Regime — Year by Year

| Year | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|-----|-----|------|--------|--------|
| 2018 | 1 | 100.0 | inf | 1.39 | 1.267 | -0.15 |
| 2019 | 3 | 33.3 | 1.927 | 8.42 | 0.500 | -13.07 |
| 2020 | 3 | 33.3 | 0.162 | -4.72 | -0.662 | -8.75 |
| 2021 | 4 | 25.0 | 0.695 | -12.73 | -0.450 | -28.25 |
| 2022 | 5 | 60.0 | 0.859 | -3.47 | -0.082 | -13.05 |
| 2023 | 8 | 62.5 | 4.136 | 5.57 | 0.629 | -4.07 |
| 2024 | 9 | 55.6 | 2.027 | 16.53 | 0.895 | -17.31 |

## +Trail — Year by Year

| Year | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|-----|-----|------|--------|--------|
| 2018 | 10 | 60.0 | 1.431 | 10.52 | 0.470 | -31.15 |
| 2019 | 20 | 45.0 | 0.565 | -34.14 | -1.117 | -53.20 |
| 2020 | 16 | 43.8 | 1.369 | 6.93 | 0.370 | -23.27 |
| 2021 | 20 | 65.0 | 1.871 | 44.20 | 1.088 | -30.07 |
| 2022 | 14 | 57.1 | 0.961 | -5.93 | -0.073 | -26.53 |
| 2023 | 30 | 50.0 | 1.206 | 0.84 | 0.145 | -13.06 |
| 2024 | 21 | 52.4 | 1.043 | -4.23 | -0.034 | -22.32 |

## Both — Year by Year

| Year | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|-----|-----|------|--------|--------|
| 2018 | 1 | 100.0 | inf | 1.39 | 1.267 | -0.15 |
| 2019 | 4 | 50.0 | 0.437 | -8.37 | -0.831 | -13.07 |
| 2020 | 3 | 33.3 | 0.162 | -4.72 | -0.662 | -8.75 |
| 2021 | 4 | 25.0 | 0.929 | -4.92 | -0.120 | -21.76 |
| 2022 | 7 | 42.9 | 0.550 | -13.02 | -0.788 | -14.44 |
| 2023 | 8 | 50.0 | 1.175 | -0.42 | -0.011 | -4.07 |
| 2024 | 10 | 60.0 | 1.654 | 9.96 | 0.614 | -15.58 |

## Parameter Sweep — Top 10 by Sharpe

| Config | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|--------|--------|-----|-----|------|--------|--------|
| adx=20_trail=1.0_rsios=30_rsiob=75 | 20 | 65.0 | 2.337 | 27.13 | 0.500 | -10.33 |
| adx=20_trail=1.0_rsios=30_rsiob=70 | 26 | 65.4 | 2.058 | 29.32 | 0.497 | -12.93 |
| adx=20_trail=1.0_rsios=30_rsiob=80 | 18 | 66.7 | 2.401 | 25.31 | 0.486 | -10.33 |
| adx=20_trail=2.0_rsios=30_rsiob=75 | 17 | 64.7 | 2.493 | 28.95 | 0.485 | -10.33 |
| adx=20_trail=1.5_rsios=30_rsiob=75 | 18 | 66.7 | 2.215 | 26.09 | 0.471 | -10.33 |
| adx=20_trail=2.0_rsios=30_rsiob=80 | 15 | 66.7 | 2.552 | 26.44 | 0.470 | -10.33 |
| adx=20_trail=1.5_rsios=30_rsiob=80 | 16 | 68.8 | 2.235 | 23.64 | 0.457 | -10.33 |
| adx=20_trail=1.5_rsios=30_rsiob=70 | 23 | 60.9 | 1.847 | 25.19 | 0.415 | -12.93 |
| adx=20_trail=2.0_rsios=30_rsiob=70 | 21 | 57.1 | 1.954 | 26.61 | 0.414 | -12.93 |
| adx=20_trail=2.0_rsios=25_rsiob=75 | 11 | 72.7 | 2.654 | 19.43 | 0.399 | -10.84 |

## Goal Assessment

Goals:
- Profitable in MORE years than baseline
- HIGHER average return than baseline
- LOWER max drawdown than baseline
- Maintain win rate > 50%

**Baseline Performance:**
- Profitable years: 5/7
- Average Net Return: 4.29%
- Average Max Drawdown: -30.03%
- Average Win Rate: 50.3%

**+Regime:**
- ❌ More profitable years: 4 vs 5
- ❌ Higher avg return: 1.57% vs 4.29%
- ✅ Lower max drawdown: -12.09% vs -30.03%
- ✅ Win rate > 50%: 52.8%

**+Trail:**
- ❌ More profitable years: 4 vs 5
- ❌ Higher avg return: 2.60% vs 4.29%
- ✅ Lower max drawdown: -28.51% vs -30.03%
- ✅ Win rate > 50%: 53.3%

**Both:**
- ❌ More profitable years: 2 vs 5
- ❌ Higher avg return: -2.87% vs 4.29%
- ✅ Lower max drawdown: -11.12% vs -30.03%
- ✅ Win rate > 50%: 51.6%

## Final Verdict

**Best configuration by average Sharpe: +Regime** (Sharpe: 0.300)
