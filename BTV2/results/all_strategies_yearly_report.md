# All Strategies Yearly Backtest Report (2018-2024)

## Parameters Used

### Grid Trading (4h)
```python
adx_threshold = 15
spacing_mult = 0.30
```

### Mean Reversion (daily)
```python
rsi_oversold = 25
rsi_overbought = 75
bb_proximity = 0.05
```

### Momentum Scalping (15m)
```python
ema_fast = 20
ema_slow = 50
atr_stop = 1.0
atr_target = 2.0
```

### MA Crossover (daily)
```python
ma_fast = 20
ma_slow = 50
```

### Liquidation Capture (daily)
```python
drop_threshold = 10  (price_threshold=0.030)
recovery_bars = 10   (volume_mult=3.0, rsi_threshold=18.0)
```

## Grid Trading (4h)

| Year | Trades | WR% | PF | Net% | Sharpe | MaxDD |
|------|--------|-----|-----|------|--------|-------|
| 2018 | 7 | 42.9 | 1.031 | -1.25 | -0.068 | -11.56 |
| 2019 | 4 | 75.0 | 123.010 | 12.41 | 1.451 | -3.60 |
| 2020 | 2 | 0.0 | 0.000 | -5.64 | -1.333 | -6.52 |
| 2021 | 9 | 55.6 | 1.050 | -1.22 | -0.049 | -9.89 |
| 2022 | 11 | 18.2 | 0.321 | -17.51 | -1.713 | -22.73 |
| 2023 | 2 | 0.0 | 0.000 | -3.39 | -1.543 | -3.39 |
| 2024 | 12 | 58.3 | 1.166 | -0.08 | 0.036 | -6.37 |

## Mean Reversion (daily)

| Year | Trades | WR% | PF | Net% | Sharpe | MaxDD |
|------|--------|-----|-----|------|--------|-------|
| 2018 | 7 | 57.1 | 0.529 | -23.69 | -0.717 | -38.63 |
| 2019 | 12 | 33.3 | 1.330 | 5.73 | 0.337 | -43.20 |
| 2020 | 15 | 40.0 | 1.816 | 19.75 | 0.723 | -25.14 |
| 2021 | 15 | 53.3 | 1.685 | 28.59 | 0.784 | -30.11 |
| 2022 | 10 | 60.0 | 0.587 | -16.46 | -0.468 | -32.50 |
| 2023 | 18 | 55.6 | 1.391 | 2.93 | 0.240 | -13.24 |
| 2024 | 17 | 52.9 | 1.464 | 13.16 | 0.574 | -27.40 |

## Momentum Scalping (15m)

| Year | Trades | WR% | PF | Net% | Sharpe | MaxDD |
|------|--------|-----|-----|------|--------|-------|
| 2018 | 11 | 45.5 | 0.715 | -3.50 | -0.851 | -6.34 |
| 2019 | 4 | 0.0 | 0.000 | -3.93 | -2.277 | -3.93 |
| 2020 | 14 | 64.3 | 1.142 | -1.80 | -0.913 | -3.16 |
| 2021 | 10 | 40.0 | 1.022 | -1.45 | -0.524 | -2.12 |
| 2022 | 8 | 50.0 | 0.514 | -3.17 | -1.054 | -4.84 |
| 2023 | 6 | 33.3 | 0.216 | -1.73 | -2.181 | -1.85 |
| 2024 | 13 | 46.2 | 0.336 | -5.39 | -1.688 | -6.16 |

## MA Crossover (daily)

| Year | Trades | WR% | PF | Net% | Sharpe | MaxDD |
|------|--------|-----|-----|------|--------|-------|
| 2018 | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2019 | 1 | 0.0 | 0.000 | -20.23 | -1.545 | -20.23 |
| 2020 | 1 | 100.0 | inf | 16.54 | 1.332 | -3.35 |
| 2021 | 1 | 100.0 | inf | 14.56 | 1.556 | -1.20 |
| 2022 | 1 | 100.0 | inf | 13.56 | 1.737 | -2.84 |
| 2023 | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2024 | 1 | 0.0 | 0.000 | -12.43 | -1.523 | -12.43 |

## Liquidation Capture (daily)

| Year | Trades | WR% | PF | Net% | Sharpe | MaxDD |
|------|--------|-----|-----|------|--------|-------|
| 2018 | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2019 | 1 | 0.0 | 0.000 | -19.10 | -1.003 | -19.10 |
| 2020 | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2021 | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2022 | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2023 | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2024 | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |

## Consistency Analysis

A strategy is **consistently profitable** if it has positive Net% in ALL 7 years.

### Grid Trading: [MIXED 1/7 years]

- **Profitable years**: 1/7
- **Average Net Return**: -2.38%
- **Average Win Rate**: 35.7%
- **Average Sharpe**: -0.460
- **Average Profit Factor**: 18.083
- **Average Max Drawdown**: -9.15%

- **Profitable years**: [2019]
- **Unprofitable years**: [2018, 2020, 2021, 2022, 2023, 2024]

### Mean Reversion: [MIXED 5/7 years]

- **Profitable years**: 5/7
- **Average Net Return**: 4.29%
- **Average Win Rate**: 50.3%
- **Average Sharpe**: 0.210
- **Average Profit Factor**: 1.257
- **Average Max Drawdown**: -30.03%

- **Profitable years**: [2019, 2020, 2021, 2023, 2024]
- **Unprofitable years**: [2018, 2022]

### Momentum Scalping: [NEVER PROFITABLE]

- **Profitable years**: 0/7
- **Average Net Return**: -3.00%
- **Average Win Rate**: 39.9%
- **Average Sharpe**: -1.355
- **Average Profit Factor**: 0.564
- **Average Max Drawdown**: -4.06%

- **Unprofitable years**: [2018, 2019, 2020, 2021, 2022, 2023, 2024]

### MA Crossover: [MIXED 3/7 years]

- **Profitable years**: 3/7
- **Average Net Return**: 1.71%
- **Average Win Rate**: 42.9%
- **Average Sharpe**: 0.222
- **Average Profit Factor**: inf
- **Average Max Drawdown**: -5.72%

- **Profitable years**: [2020, 2021, 2022]
- **Unprofitable years**: [2018, 2019, 2023, 2024]

### Liquidation Capture: [NEVER PROFITABLE]

- **Profitable years**: 0/7
- **Average Net Return**: -2.73%
- **Average Win Rate**: 0.0%
- **Average Sharpe**: -0.143
- **Average Profit Factor**: 0.000
- **Average Max Drawdown**: -2.73%

- **Unprofitable years**: [2018, 2019, 2020, 2021, 2022, 2023, 2024]

## Summary: All Strategies Side-by-Side

### Net Return (%) by Year

| Strategy | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | Avg |
|--------|--------|--------|--------|--------|--------|--------|--------|--------|
| Grid Trading | -1.25 | 12.41 | -5.64 | -1.22 | -17.51 | -3.39 | -0.08 | -2.38 |
| Mean Reversion | -23.69 | 5.73 | 19.75 | 28.59 | -16.46 | 2.93 | 13.16 | 4.29 |
| Momentum Scalping | -3.50 | -3.93 | -1.80 | -1.45 | -3.17 | -1.73 | -5.39 | -3.00 |
| MA Crossover | 0.00 | -20.23 | 16.54 | 14.56 | 13.56 | 0.00 | -12.43 | 1.71 |
| Liquidation Capture | 0.00 | -19.10 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | -2.73 |

### Win Rate (%) by Year

| Strategy | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | Avg |
|--------|--------|--------|--------|--------|--------|--------|--------|--------|
| Grid Trading | 42.9 | 75.0 | 0.0 | 55.6 | 18.2 | 0.0 | 58.3 | 35.7 |
| Mean Reversion | 57.1 | 33.3 | 40.0 | 53.3 | 60.0 | 55.6 | 52.9 | 50.3 |
| Momentum Scalping | 45.5 | 0.0 | 64.3 | 40.0 | 50.0 | 33.3 | 46.2 | 39.9 |
| MA Crossover | 0.0 | 0.0 | 100.0 | 100.0 | 100.0 | 0.0 | 0.0 | 42.9 |
| Liquidation Capture | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |

### Sharpe Ratio by Year

| Strategy | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | Avg |
|--------|--------|--------|--------|--------|--------|--------|--------|--------|
| Grid Trading | -0.068 | 1.451 | -1.333 | -0.049 | -1.713 | -1.543 | 0.036 | -0.460 |
| Mean Reversion | -0.717 | 0.337 | 0.723 | 0.784 | -0.468 | 0.240 | 0.574 | 0.210 |
| Momentum Scalping | -0.851 | -2.277 | -0.913 | -0.524 | -1.054 | -2.181 | -1.688 | -1.355 |
| MA Crossover | 0.000 | -1.545 | 1.332 | 1.556 | 1.737 | 0.000 | -1.523 | 0.222 |
| Liquidation Capture | 0.000 | -1.003 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | -0.143 |

## Final Verdict

**No strategy was profitable in ALL 7 years.** This is normal — market regimes change, and no single set of parameters works in every environment.

### Ranking by Average Sharpe Ratio

| Rank | Strategy | Avg Sharpe |
|------|----------|------------|
| 1 | MA Crossover | 0.222 |
| 2 | Mean Reversion | 0.210 |
| 3 | Liquidation Capture | -0.143 |
| 4 | Grid Trading | -0.460 |
| 5 | Momentum Scalping | -1.355 |
