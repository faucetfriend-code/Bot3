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
