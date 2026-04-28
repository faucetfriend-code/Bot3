# Mean Reversion Strategy — Comprehensive Validation Report

## Best Configuration
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

## Walk-Forward Optimization Results

| Fold | Train Period | Test Period | OOS Net% | OOS Sharpe | OOS WR% | OOS Trades | OOS MaxDD% |
|------|--------------|-------------|----------|------------|---------|------------|------------|
| 1 | 2018-01 → 2018-12 | 2019-01 → 2019-03 | +3.75 | 1.852 | 100.0 | 3 | -2.10 |
| 2 | 2018-04 → 2019-03 | 2019-04 → 2019-06 | +0.00 | 0.000 | 0.0 | 0 | 0.00 |
| 3 | 2018-07 → 2019-06 | 2019-07 → 2019-09 | +0.00 | 0.000 | 0.0 | 0 | 0.00 |
| 4 | 2018-10 → 2019-09 | 2019-10 → 2019-12 | -0.57 | -0.188 | 0.0 | 1 | -3.55 |
| 5 | 2019-01 → 2019-12 | 2020-01 → 2020-03 | +0.00 | 0.000 | 0.0 | 0 | 0.00 |
| 6 | 2019-04 → 2020-03 | 2020-04 → 2020-06 | +11.86 | 1.806 | 50.0 | 2 | -1.68 |
| 7 | 2019-07 → 2020-06 | 2020-07 → 2020-09 | +0.00 | 0.000 | 0.0 | 0 | 0.00 |
| 8 | 2019-10 → 2020-09 | 2020-10 → 2020-12 | +4.16 | 1.931 | 100.0 | 1 | -0.15 |
| 9 | 2020-01 → 2020-12 | 2021-01 → 2021-03 | +20.75 | 2.133 | 100.0 | 1 | -1.00 |
| 10 | 2020-04 → 2021-03 | 2021-04 → 2021-06 | +0.00 | 0.000 | 0.0 | 0 | 0.00 |
| 11 | 2020-07 → 2021-06 | 2021-07 → 2021-09 | +0.00 | 0.000 | 0.0 | 0 | 0.00 |
| 12 | 2020-10 → 2021-09 | 2021-10 → 2021-12 | +0.00 | 0.000 | 0.0 | 0 | 0.00 |
| 13 | 2021-01 → 2021-12 | 2022-01 → 2022-03 | +0.00 | 0.000 | 0.0 | 0 | 0.00 |
| 14 | 2021-04 → 2022-03 | 2022-04 → 2022-06 | +0.00 | 0.000 | 0.0 | 0 | 0.00 |
| 15 | 2021-07 → 2022-06 | 2022-07 → 2022-09 | +6.95 | 2.355 | 100.0 | 2 | -1.73 |
| 16 | 2021-10 → 2022-09 | 2022-10 → 2022-12 | +0.00 | 0.000 | 0.0 | 0 | 0.00 |
| 17 | 2022-01 → 2022-12 | 2023-01 → 2023-03 | +0.00 | 0.000 | 0.0 | 0 | 0.00 |
| 18 | 2022-04 → 2023-03 | 2023-04 → 2023-06 | +0.00 | 0.000 | 0.0 | 0 | 0.00 |
| 19 | 2022-07 → 2023-06 | 2023-07 → 2023-09 | -9.92 | -2.692 | 0.0 | 4 | -9.92 |
| 20 | 2022-10 → 2023-09 | 2023-10 → 2023-12 | +4.10 | 2.453 | 100.0 | 1 | -1.75 |
| 21 | 2023-01 → 2023-12 | 2024-01 → 2024-03 | +4.77 | 2.720 | 100.0 | 1 | -0.15 |
| 22 | 2023-04 → 2024-03 | 2024-04 → 2024-06 | +2.72 | 1.747 | 66.7 | 3 | -1.39 |
| 23 | 2023-07 → 2024-06 | 2024-07 → 2024-09 | -1.93 | -1.631 | 0.0 | 1 | -2.29 |
| 24 | 2023-10 → 2024-09 | 2024-10 → 2024-12 | +0.00 | 0.000 | 0.0 | 0 | 0.00 |
| 25 | 2024-01 → 2024-12 | 2025-01 → 2025-01 | +0.00 | 0.000 | 0.0 | 0 | 0.00 |

### Walk-Forward Summary

- **Total Folds**: 25
- **Profitable Folds**: 8/25 (32%)
- **Avg OOS Net Return**: +1.87%
- **Avg OOS Sharpe**: 0.499
- **Avg OOS Win Rate**: 28.7%
- **Total OOS Trades**: 20

- **Stitched OOS Equity**: Start=1.0, End=1.5362
- **Stitched OOS Return**: +53.62%
- **Stitched OOS Sharpe**: 0.692

### Optimized Parameter Stability

| Fold | RSI Oversold | RSI Overbought | ADX Max | Trail ATR Mult |
|------|--------------|----------------|---------|----------------|
| 1 | 35 | 70 | 18 | 0.8 |
| 2 | 25 | 70 | 18 | 1.2 |
| 3 | 30 | 70 | 18 | 1.2 |
| 4 | 30 | 70 | 18 | 1.2 |
| 5 | 35 | 75 | 22 | 1.2 |
| 6 | 35 | 70 | 22 | 1.2 |
| 7 | 35 | 70 | 22 | 0.8 |
| 8 | 30 | 75 | 22 | 0.8 |
| 9 | 35 | 75 | 22 | 1.2 |
| 10 | 35 | 80 | 22 | 1.2 |
| 11 | 30 | 75 | 20.0 | 1.0 |
| 12 | 25 | 70 | 20 | 1.2 |
| 13 | 25 | 70 | 20 | 1.2 |
| 14 | 30 | 75 | 20.0 | 1.0 |
| 15 | 30 | 70 | 22 | 0.8 |
| 16 | 30 | 70 | 20 | 0.8 |
| 17 | 35 | 70 | 18 | 0.8 |
| 18 | 30 | 70 | 18 | 0.8 |
| 19 | 35 | 70 | 18 | 0.8 |
| 20 | 30 | 70 | 22 | 1.0 |
| 21 | 35 | 70 | 18 | 1.0 |
| 22 | 30 | 70 | 22 | 1.2 |
| 23 | 25 | 80 | 22 | 0.8 |
| 24 | 25 | 80 | 22 | 1.2 |
| 25 | 25 | 80 | 22 | 0.8 |

## Position Sizing Comparison

| Method | Total Return% | Sharpe | MaxDD% | Win Rate% | Trades | Avg Pos Size |
|--------|---------------|--------|--------|-----------|--------|--------------|
| Fixed (baseline) | +21.70 | 0.412 | -8.74 | 45.0 | 20 | 1.0000 |
| Fixed Fractional | +3.37 | 0.355 | -1.29 | 45.0 | 20 | 0.2070 |
| Kelly Criterion | +0.09 | 0.043 | -0.76 | 45.0 | 20 | 0.0433 |
| Volatility-Adjusted | +5.04 | 0.355 | -1.93 | 45.0 | 20 | 0.3106 |

## Out-of-Sample Testing (2025-2026)

**Period**: 2025-01-01 to 2026-03-15

| Metric | Value |
|--------|-------|
| Period | 2025-01-01 to 2026-03-15 |
| Net% | -2.60 |
| CAGR% | -2.18 |
| Sharpe | -0.248 |
| MaxDD% | -11.19 |
| WinRate% | 71.40 |
| ProfitFactor | 0.79 |
| Trades | 7 |

## Live Trading Simulation

**Starting Capital**: $10,000.00
**Risk per Trade**: 2.0%
**Slippage**: 0.05%
**Fee per Side**: 0.1%
**Max Position**: 20.0%

| Metric | Value |
|--------|-------|
| StartingCapital | $10,000.00 |
| FinalEquity | $10,005.81 |
| TotalReturn% | 0.06% |
| CAGR% | 0.05% |
| Sharpe | 0.052 |
| MaxDD% | -0.65% |
| WinRate% | 57.10% |
| ProfitFactor | 43.05 |
| Trades | 7 |
| AvgPositionSize% | 17.00% |

## Goal Assessment

### 1. Generalizes Across Time (Walk-Forward OOS > 0)
- **Status**: ✅ PASS
- **Profitable Folds**: 8/25
- **Avg OOS Return**: +1.87%

### 2. Position Sizing Improves Risk-Adjusted Returns
- **Status**: ❌ FAIL
- **Baseline Sharpe**: 0.412
- **Best Method**: Fixed (baseline) (Sharpe: 0.412)

### 3. Works on Unseen Data (2025-2026)
- **Status**: ⚠️ MIXED
- **OOS Net Return**: -2.60%
- **OOS Sharpe**: -0.248
- **OOS Trades**: 7

### 4. Safe for Live Trading with Risk Management
- **Status**: ✅ PASS
- **Final Equity**: $10,005.81
- **Max Drawdown**: -0.65% ✅
- **Win Rate**: 57.1% ✅

## Final Verdict

⚠️ **The strategy shows mixed results.** Some criteria pass while others need attention.
Review the detailed results above for specific areas of concern.
