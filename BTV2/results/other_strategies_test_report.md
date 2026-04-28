# BTV2 — Other Strategies Test Report

**Asset:** BTCUSDT | **Year:** 2024 | **Cutoff:** 0.1

## All Results (sorted by Net Return)

| Strategy | Config | Interval | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|----------|--------|----------|--------|-----|-----|------|--------|--------|
| Liquidation Capture | var 2 | 1d | 2 | 100.0 | inf | 26.72 | 2.194 | -4.07 |
| Liquidation Capture | var 3 | 1d | 2 | 100.0 | inf | 26.72 | 2.194 | -4.07 |
| Liquidation Capture | var 6 | 1d | 2 | 100.0 | inf | 26.72 | 2.194 | -4.07 |
| Liquidation Capture | var 9 | 1d | 2 | 100.0 | inf | 26.72 | 2.194 | -4.07 |
| Grid Trading | var 3 | 4h | 27 | 63.0 | 2.055 | 22.89 | 1.432 | -5.8 |
| Grid Trading | var 7 | 4h | 87 | 55.2 | 1.466 | 21.62 | 0.915 | -13.62 |
| Liquidation Capture | var 1 | 1d | 1 | 100.0 | inf | 20.01 | 1.792 | -4.07 |
| Grid Trading | var 6 | 4h | 70 | 55.7 | 1.446 | 17.42 | 0.824 | -17.34 |
| Grid Trading | defaults | 4h | 41 | 58.5 | 1.596 | 15.64 | 0.905 | -7.91 |
| Grid Trading | var 5 | 4h | 41 | 58.5 | 1.596 | 15.64 | 0.905 | -7.91 |
| Mean Reversion | var 1 | 1d | 17 | 52.9 | 1.464 | 13.16 | 0.574 | -27.4 |
| Mean Reversion | var 6 | 1d | 26 | 50.0 | 1.414 | 12.62 | 0.533 | -31.41 |
| Grid Trading | var 4 | 4h | 36 | 58.3 | 1.481 | 10.44 | 0.661 | -11.31 |
| MA Crossover | var 2 | 1d | 2 | 50.0 | 1.745 | 5.82 | 0.512 | -12.1 |
| Mean Reversion | var 5 | 1d | 28 | 46.4 | 1.223 | 4.36 | 0.294 | -35.64 |
| Mean Reversion | var 2 | 1d | 25 | 48.0 | 1.192 | 2.52 | 0.235 | -34.3 |
| Mean Reversion | var 7 | 1d | 26 | 46.2 | 1.151 | 0.13 | 0.156 | -36.26 |
| Liquidation Capture | defaults | 1d | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| MA Crossover | var 3 | 1d | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| MA Crossover | var 5 | 1d | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| MA Crossover | var 6 | 1d | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| MA Crossover | var 7 | 1d | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| Momentum Scalping | var 5 | 15m | 13 | 61.5 | 0.854 | -2.85 | -0.746 | -5.2 |
| Momentum Scalping | var 3 | 15m | 26 | 53.8 | 1.013 | -3.79 | -0.845 | -6.87 |
| Mean Reversion | defaults | 1d | 24 | 41.7 | 1.052 | -4.88 | 0.004 | -39.04 |
| Momentum Scalping | var 4 | 15m | 22 | 40.9 | 0.591 | -8.49 | -1.721 | -10.78 |
| Momentum Scalping | var 8 | 15m | 34 | 41.2 | 0.523 | -10.45 | -2.407 | -11.67 |
| MA Crossover | defaults | 1d | 1 | 0.0 | 0.0 | -12.43 | -1.523 | -12.43 |
| Momentum Scalping | var 7 | 15m | 61 | 49.2 | 0.779 | -14.24 | -2.091 | -16.22 |
| Momentum Scalping | defaults | 15m | 112 | 45.5 | 0.692 | -28.34 | -3.202 | -31.74 |

## Success Criteria Check

Goal: Net Return > 0%, Win Rate > 40%, Trades >= 10

| Strategy | Config | Net% | WR% | Trades | PASS? |
|----------|--------|------|-----|--------|-------|
| Liquidation Capture | var 2 | 26.72 | 100.0 | 2 | FAIL |
| Liquidation Capture | var 3 | 26.72 | 100.0 | 2 | FAIL |
| Liquidation Capture | var 6 | 26.72 | 100.0 | 2 | FAIL |
| Liquidation Capture | var 9 | 26.72 | 100.0 | 2 | FAIL |
| Grid Trading | var 3 | 22.89 | 63.0 | 27 | PASS |
| Grid Trading | var 7 | 21.62 | 55.2 | 87 | PASS |
| Liquidation Capture | var 1 | 20.01 | 100.0 | 1 | FAIL |
| Grid Trading | var 6 | 17.42 | 55.7 | 70 | PASS |
| Grid Trading | defaults | 15.64 | 58.5 | 41 | PASS |
| Grid Trading | var 5 | 15.64 | 58.5 | 41 | PASS |
| Mean Reversion | var 1 | 13.16 | 52.9 | 17 | PASS |
| Mean Reversion | var 6 | 12.62 | 50.0 | 26 | PASS |
| Grid Trading | var 4 | 10.44 | 58.3 | 36 | PASS |
| MA Crossover | var 2 | 5.82 | 50.0 | 2 | FAIL |
| Mean Reversion | var 5 | 4.36 | 46.4 | 28 | PASS |
| Mean Reversion | var 2 | 2.52 | 48.0 | 25 | PASS |
| Mean Reversion | var 7 | 0.13 | 46.2 | 26 | PASS |
| Liquidation Capture | defaults | 0.0 | 0.0 | 0 | FAIL |
| MA Crossover | var 3 | 0.0 | 0.0 | 0 | FAIL |
| MA Crossover | var 5 | 0.0 | 0.0 | 0 | FAIL |
| MA Crossover | var 6 | 0.0 | 0.0 | 0 | FAIL |
| MA Crossover | var 7 | 0.0 | 0.0 | 0 | FAIL |
| Momentum Scalping | var 5 | -2.85 | 61.5 | 13 | FAIL |
| Momentum Scalping | var 3 | -3.79 | 53.8 | 26 | FAIL |
| Mean Reversion | defaults | -4.88 | 41.7 | 24 | FAIL |
| Momentum Scalping | var 4 | -8.49 | 40.9 | 22 | FAIL |
| Momentum Scalping | var 8 | -10.45 | 41.2 | 34 | FAIL |
| MA Crossover | defaults | -12.43 | 0.0 | 1 | FAIL |
| Momentum Scalping | var 7 | -14.24 | 49.2 | 61 | FAIL |
| Momentum Scalping | defaults | -28.34 | 45.5 | 112 | FAIL |

## Best Config Per Strategy

### Mean Reversion
- **Best config:** var 1
- **Net Return:** 13.16%
- **Win Rate:** 52.9%
- **Profit Factor:** 1.464
- **Sharpe:** 0.574
- **Max Drawdown:** -27.4%
- **Trades:** 17
- **Params:** {'rsi_oversold': 25, 'rsi_overbought': 75, 'bb_proximity': 0.05}

### Momentum Scalping
- **Best config:** var 5
- **Net Return:** -2.85%
- **Win Rate:** 61.5%
- **Profit Factor:** 0.854
- **Sharpe:** -0.746
- **Max Drawdown:** -5.2%
- **Trades:** 13
- **Params:** {'ema_fast': 20, 'ema_slow': 50}

### Liquidation Capture
- **Best config:** var 2
- **Net Return:** 26.72%
- **Win Rate:** 100.0%
- **Profit Factor:** inf
- **Sharpe:** 2.194
- **Max Drawdown:** -4.07%
- **Trades:** 2
- **Params:** {'price_threshold': 0.02, 'volume_mult': 2.0}

### Grid Trading
- **Best config:** var 3
- **Net Return:** 22.89%
- **Win Rate:** 63.0%
- **Profit Factor:** 2.055
- **Sharpe:** 1.432
- **Max Drawdown:** -5.8%
- **Trades:** 27
- **Params:** {'spacing_mult': 0.3}

### MA Crossover
- **Best config:** var 2
- **Net Return:** 5.82%
- **Win Rate:** 50.0%
- **Profit Factor:** 1.745
- **Sharpe:** 0.512
- **Max Drawdown:** -12.1%
- **Trades:** 2
- **Params:** {'fast_period': 10, 'slow_period': 100}


## Parameter Details for Top 5 Performers

### Liquidation Capture (var 2)
```
{'price_threshold': 0.02, 'volume_mult': 2.0}
```

### Liquidation Capture (var 3)
```
{'price_threshold': 0.02, 'volume_mult': 2.0, 'rsi_threshold': 22.0}
```

### Liquidation Capture (var 6)
```
{'price_threshold': 0.025, 'volume_mult': 2.0}
```

### Liquidation Capture (var 9)
```
{'volume_mult': 2.0}
```

### Grid Trading (var 3)
```
{'spacing_mult': 0.3}
```

