# Universal Regime Filter Report

## Overview

This report evaluates the universal regime detection system across all
BTV2 strategies. The regime detector identifies market conditions and
filters out trades in regimes unsuitable for each strategy.

## Regime Detection Accuracy

| Year | Detected Regime | Actual Market | Accuracy |
|------|-----------------|---------------|----------|
| 2018 | ranging | Bear market (-73%) | FAIL |
| 2019 | ranging | Recovery (+95%) | PASS |
| 2020 | bull_strong | Bull run (+300%) | PASS |
| 2021 | bull_weak | Peak + crash | PASS |
| 2022 | ranging | Bear market (-65%) | FAIL |
| 2023 | bull_strong | Recovery (+155%) | FAIL |
| 2024 | ranging | New ATH (+120%) | FAIL |

### Regime Distribution by Year

**2018**: {'ranging': 31.5, 'bear_strong': 30.4, 'bear_weak': 28.2, 'bull_weak': 6.0, 'bull_strong': 3.8}
**2019**: {'ranging': 37.3, 'bull_strong': 24.9, 'bear_weak': 14.0, 'bull_weak': 12.1, 'bear_strong': 11.8}
**2020**: {'bull_strong': 38.0, 'ranging': 32.0, 'bull_weak': 19.7, 'bear_strong': 5.7, 'bear_weak': 4.6}
**2021**: {'bull_weak': 29.9, 'ranging': 21.4, 'bear_strong': 20.3, 'bull_strong': 20.0, 'bear_weak': 8.5}
**2022**: {'ranging': 38.1, 'bear_weak': 32.1, 'bear_strong': 25.2, 'bull_weak': 4.7}
**2023**: {'bull_strong': 44.7, 'ranging': 28.2, 'bear_weak': 13.2, 'bull_weak': 11.8, 'bear_strong': 2.2}
**2024**: {'ranging': 33.9, 'bull_weak': 23.2, 'bull_strong': 22.7, 'bear_weak': 15.0, 'bear_strong': 5.2}

## Strategy Regime Compatibility

| Strategy | Allowed Regimes |
|----------|----------------|
| Mean Reversion | ranging, bull_weak |
| VWAP Scalping | ranging, bull_weak |
| Momentum Scalping | bull_strong, bear_strong |
| Liquidation Capture | bull_weak, ranging |
| Grid Trading | ranging |
| MA Crossover | bull_strong, bear_strong |

## Mean Reversion

| Year | Filter | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|--------|-----|-----|------|--------|--------|
| 2018 | none | 12 | 58.3 | 1.083 | -1.81 | 0.074 | -31.35 |
| 2018 | regime | 3 | 66.7 | 0.900 | -1.69 | -0.185 | -9.01 |
| 2019 | none | 24 | 50.0 | 0.674 | -21.56 | -0.767 | -38.64 |
| 2019 | regime | 7 | 57.1 | 0.359 | -14.33 | -1.273 | -17.37 |
| 2020 | none | 18 | 38.9 | 0.977 | -5.44 | -0.112 | -22.91 |
| 2020 | regime | 6 | 33.3 | 1.501 | 2.90 | 0.289 | -9.30 |
| 2021 | none | 25 | 48.0 | 1.451 | 22.03 | 0.701 | -25.51 |
| 2021 | regime | 8 | 50.0 | 1.451 | 7.25 | 0.397 | -15.46 |
| 2022 | none | 17 | 41.2 | 0.514 | -32.02 | -1.270 | -39.28 |
| 2022 | regime | 9 | 44.4 | 0.485 | -19.84 | -1.031 | -26.11 |
| 2023 | none | 33 | 48.5 | 1.057 | -4.23 | -0.138 | -16.58 |
| 2023 | regime | 8 | 25.0 | 0.027 | -9.98 | -2.287 | -9.98 |
| 2024 | none | 23 | 43.5 | 0.957 | -6.94 | -0.272 | -15.66 |
| 2024 | regime | 11 | 54.5 | 1.811 | 8.28 | 0.714 | -9.35 |

### Mean Reversion - Summary

- **Avg Net Return (no filter)**: -7.14%
- **Avg Net Return (regime filter)**: -3.92%
- **Improvement**: +3.22%
- **Avg Max Drawdown (no filter)**: -27.13%
- **Avg Max Drawdown (regime filter)**: -13.80%
- **Drawdown Improvement**: +13.34%
- **Avg Trades (no filter)**: 21.7
- **Avg Trades (regime filter)**: 7.4
- **Trade Reduction**: 65.8%

## VWAP Scalping

| Year | Filter | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|--------|-----|-----|------|--------|--------|
| 2018 | none | 13 | 38.5 | 0.376 | -3.94 | -1.567 | -4.27 |
| 2018 | regime | 5 | 0.0 | 0.000 | -2.65 | -2.413 | -2.65 |
| 2019 | none | 15 | 40.0 | 1.324 | -1.37 | -0.513 | -3.58 |
| 2019 | regime | 7 | 42.9 | 3.024 | 0.79 | 0.423 | -1.45 |
| 2020 | none | 24 | 45.8 | 1.914 | -0.57 | -0.159 | -3.16 |
| 2020 | regime | 8 | 37.5 | 1.341 | -0.76 | -0.354 | -1.45 |
| 2021 | none | 8 | 37.5 | 0.305 | -3.00 | -1.578 | -3.37 |
| 2021 | regime | 6 | 33.3 | 0.432 | -1.44 | -1.391 | -2.14 |
| 2022 | none | 15 | 60.0 | 1.653 | -0.20 | -0.048 | -2.89 |
| 2022 | regime | 9 | 55.6 | 0.403 | -2.50 | -1.528 | -2.70 |
| 2023 | none | 27 | 37.0 | 0.482 | -6.34 | -3.390 | -6.34 |
| 2023 | regime | 16 | 43.8 | 0.564 | -3.20 | -2.830 | -3.70 |
| 2024 | none | 26 | 42.3 | 1.300 | -2.76 | -1.149 | -3.87 |
| 2024 | regime | 9 | 11.1 | 0.288 | -2.77 | -2.878 | -2.77 |

### VWAP Scalping - Summary

- **Avg Net Return (no filter)**: -2.60%
- **Avg Net Return (regime filter)**: -1.79%
- **Improvement**: +0.81%
- **Avg Max Drawdown (no filter)**: -3.93%
- **Avg Max Drawdown (regime filter)**: -2.41%
- **Drawdown Improvement**: +1.52%
- **Avg Trades (no filter)**: 18.3
- **Avg Trades (regime filter)**: 8.6
- **Trade Reduction**: 53.1%

## Momentum Scalping

| Year | Filter | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|--------|-----|-----|------|--------|--------|
| 2018 | none | 11 | 45.5 | 0.715 | -3.50 | -0.851 | -6.34 |
| 2018 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2019 | none | 4 | 0.0 | 0.000 | -3.93 | -2.277 | -3.93 |
| 2019 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2020 | none | 14 | 64.3 | 1.142 | -1.80 | -0.913 | -3.16 |
| 2020 | regime | 1 | 0.0 | 0.000 | -0.66 | -1.054 | -0.66 |
| 2021 | none | 10 | 40.0 | 1.022 | -1.45 | -0.524 | -2.12 |
| 2021 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2022 | none | 8 | 50.0 | 0.514 | -3.17 | -1.054 | -4.84 |
| 2022 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2023 | none | 6 | 33.3 | 0.216 | -1.73 | -2.181 | -1.85 |
| 2023 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2024 | none | 13 | 46.2 | 0.336 | -5.39 | -1.688 | -6.16 |
| 2024 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |

### Momentum Scalping - Summary

- **Avg Net Return (no filter)**: -3.00%
- **Avg Net Return (regime filter)**: -0.09%
- **Improvement**: +2.90%
- **Avg Max Drawdown (no filter)**: -4.06%
- **Avg Max Drawdown (regime filter)**: -0.09%
- **Drawdown Improvement**: +3.96%
- **Avg Trades (no filter)**: 9.4
- **Avg Trades (regime filter)**: 0.1
- **Trade Reduction**: 98.5%

## Liquidation Capture

| Year | Filter | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|--------|-----|-----|------|--------|--------|
| 2018 | none | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2018 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2019 | none | 1 | 0.0 | 0.000 | -19.10 | -1.003 | -19.10 |
| 2019 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2020 | none | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2020 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2021 | none | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2021 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2022 | none | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2022 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2023 | none | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2023 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2024 | none | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2024 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |

### Liquidation Capture - Summary

- **Avg Net Return (no filter)**: -2.73%
- **Avg Net Return (regime filter)**: 0.00%
- **Improvement**: +2.73%
- **Avg Max Drawdown (no filter)**: -2.73%
- **Avg Max Drawdown (regime filter)**: 0.00%
- **Drawdown Improvement**: +2.73%
- **Avg Trades (no filter)**: 0.1
- **Avg Trades (regime filter)**: 0.0
- **Trade Reduction**: 100.0%

## Grid Trading

| Year | Filter | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|--------|-----|-----|------|--------|--------|
| 2018 | none | 7 | 42.9 | 1.031 | -1.25 | -0.068 | -11.56 |
| 2018 | regime | 7 | 42.9 | 1.031 | -1.25 | -0.068 | -11.56 |
| 2019 | none | 4 | 75.0 | 123.010 | 12.41 | 1.451 | -3.60 |
| 2019 | regime | 4 | 75.0 | 123.010 | 12.41 | 1.451 | -3.60 |
| 2020 | none | 2 | 0.0 | 0.000 | -5.64 | -1.333 | -6.52 |
| 2020 | regime | 2 | 0.0 | 0.000 | -5.64 | -1.333 | -6.52 |
| 2021 | none | 9 | 55.6 | 1.050 | -1.22 | -0.049 | -9.89 |
| 2021 | regime | 9 | 55.6 | 1.050 | -1.22 | -0.049 | -9.89 |
| 2022 | none | 11 | 18.2 | 0.321 | -17.51 | -1.713 | -22.73 |
| 2022 | regime | 11 | 18.2 | 0.321 | -17.51 | -1.713 | -22.73 |
| 2023 | none | 2 | 0.0 | 0.000 | -3.39 | -1.543 | -3.39 |
| 2023 | regime | 2 | 0.0 | 0.000 | -3.39 | -1.543 | -3.39 |
| 2024 | none | 12 | 58.3 | 1.166 | -0.08 | 0.036 | -6.37 |
| 2024 | regime | 12 | 58.3 | 1.166 | -0.08 | 0.036 | -6.37 |

### Grid Trading - Summary

- **Avg Net Return (no filter)**: -2.38%
- **Avg Net Return (regime filter)**: -2.38%
- **Improvement**: +0.00%
- **Avg Max Drawdown (no filter)**: -9.15%
- **Avg Max Drawdown (regime filter)**: -9.15%
- **Drawdown Improvement**: +0.00%
- **Avg Trades (no filter)**: 6.7
- **Avg Trades (regime filter)**: 6.7
- **Trade Reduction**: 0.0%

## MA Crossover

| Year | Filter | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|--------|-----|-----|------|--------|--------|
| 2018 | none | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2018 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2019 | none | 1 | 0.0 | 0.000 | -20.23 | -1.545 | -20.23 |
| 2019 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2020 | none | 1 | 100.0 | inf | 16.54 | 1.332 | -3.35 |
| 2020 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2021 | none | 1 | 100.0 | inf | 14.56 | 1.556 | -1.20 |
| 2021 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2022 | none | 1 | 100.0 | inf | 13.56 | 1.737 | -2.84 |
| 2022 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2023 | none | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2023 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |
| 2024 | none | 1 | 0.0 | 0.000 | -12.43 | -1.523 | -12.43 |
| 2024 | regime | 0 | 0.0 | 0.000 | 0.00 | 0.000 | 0.00 |

### MA Crossover - Summary

- **Avg Net Return (no filter)**: 1.71%
- **Avg Net Return (regime filter)**: 0.00%
- **Improvement**: -1.71%
- **Avg Max Drawdown (no filter)**: -5.72%
- **Avg Max Drawdown (regime filter)**: 0.00%
- **Drawdown Improvement**: +5.72%
- **Avg Trades (no filter)**: 0.7
- **Avg Trades (regime filter)**: 0.0
- **Trade Reduction**: 100.0%

## Overall Summary: All Strategies

| Strategy | Net% (No Filter) | Net% (With Filter) | Improvement | MaxDD% (No Filter) | MaxDD% (With Filter) | DD Improvement |
|----------|------------------|--------------------|-------------|--------------------|----------------------|----------------|
| Mean Reversion | -7.14 | -3.92 | +3.22 | -27.13 | -13.80 | +13.34 |
| VWAP Scalping | -2.60 | -1.79 | +0.81 | -3.93 | -2.41 | +1.52 |
| Momentum Scalping | -3.00 | -0.09 | +2.90 | -4.06 | -0.09 | +3.96 |
| Liquidation Capture | -2.73 | 0.00 | +2.73 | -2.73 | 0.00 | +2.73 |
| Grid Trading | -2.38 | -2.38 | +0.00 | -9.15 | -9.15 | +0.00 |
| MA Crossover | 1.71 | 0.00 | -1.71 | -5.72 | 0.00 | +5.72 |

## Final Verdict

**4/6 strategies improved by regime filtering.**

### Key Findings

- **Biggest improvement**: Mean Reversion (+3.22%)
- **Least improvement**: MA Crossover (-1.71%)

### Recommendations

1. Regime filtering is most beneficial for strategies that trade in specific market conditions (e.g., mean reversion in ranging markets).
2. Strategies that already have built-in regime filters (e.g., VWAP Scalping with ADX gate) see less additional benefit.
3. The combined detection method provides the most robust regime classification by weighting ADX, EMA alignment, and volatility.
4. Consider using regime detection as a position sizing multiplier (reduce size in low-confidence regimes) rather than a binary filter.