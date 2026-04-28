# Enhanced Regime Detection — Comprehensive Report

## Overview

Three tasks evaluated the enhanced regime detection system:

1. **Task 1**: Re-test ALL 6 strategies with enhanced regime filter
2. **Task 2**: Mean Reversion with confidence-based position sizing
3. **Task 3**: Liquidation Capture on 4h with enhanced regime filter

---

## Task 1: All Strategies with Enhanced Regime Filter

### Summary Table

| Strategy | Net% (No Filter) | Net% (Enhanced Filter) | Improvement |
|----------|------------------|------------------------|-------------|
| Mean Reversion | -7.14% | +1.22% | +8.36% |
| VWAP Scalping | -9.29% | -1.27% | +8.02% |
| Momentum Scalping | -3.00% | -0.85% | +2.15% |
| Liquidation Capture | -2.73% | +0.00% | +2.73% |
| Grid Trading | -2.38% | -0.50% | +1.89% |
| MA Crossover | +1.71% | +0.00% | -1.71% |

### Mean Reversion — Yearly Breakdown

| Year | Filter | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|--------|-----|-----|------|--------|--------|
| 2018 | no_filter | 12 | 58.3 | 1.083 | -1.81 | 0.074 | -31.35 |
| 2018 | enhanced | 1 | 100.0 | inf | +5.33 | 1.087 | -0.15 |
| 2019 | no_filter | 24 | 50.0 | 0.674 | -21.56 | -0.767 | -38.64 |
| 2019 | enhanced | 9 | 66.7 | 1.579 | +3.83 | 0.450 | -7.05 |
| 2020 | no_filter | 18 | 38.9 | 0.977 | -5.44 | -0.112 | -22.91 |
| 2020 | enhanced | 10 | 30.0 | 0.985 | -2.78 | -0.124 | -16.01 |
| 2021 | no_filter | 25 | 48.0 | 1.451 | +22.03 | 0.701 | -25.51 |
| 2021 | enhanced | 7 | 57.1 | 2.401 | +19.10 | 0.848 | -6.12 |
| 2022 | no_filter | 17 | 41.2 | 0.514 | -32.02 | -1.270 | -39.28 |
| 2022 | enhanced | 9 | 44.4 | 0.458 | -21.66 | -1.098 | -27.79 |
| 2023 | no_filter | 33 | 48.5 | 1.057 | -4.23 | -0.138 | -16.58 |
| 2023 | enhanced | 21 | 47.6 | 1.488 | +5.85 | 0.465 | -7.85 |
| 2024 | no_filter | 23 | 43.5 | 0.957 | -6.94 | -0.272 | -15.66 |
| 2024 | enhanced | 15 | 46.7 | 1.093 | -1.12 | -0.015 | -9.35 |

**Profitable Years**: No Filter: 1/7 → Enhanced: 4/7

### VWAP Scalping — Yearly Breakdown

| Year | Filter | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|--------|-----|-----|------|--------|--------|
| 2018 | no_filter | 28 | 17.9 | 0.410 | -9.38 | -3.058 | -12.13 |
| 2018 | enhanced | 1 | 0.0 | 0.000 | -0.52 | -1.309 | -0.52 |
| 2019 | no_filter | 36 | 19.4 | 0.411 | -9.50 | -3.630 | -10.12 |
| 2019 | enhanced | 6 | 16.7 | 0.063 | -1.71 | -2.348 | -1.71 |
| 2020 | no_filter | 46 | 41.3 | 1.387 | -4.31 | -1.193 | -7.96 |
| 2020 | enhanced | 4 | 50.0 | 1.028 | -0.59 | -1.490 | -0.68 |
| 2021 | no_filter | 39 | 33.3 | 0.708 | -8.29 | -2.616 | -8.42 |
| 2021 | enhanced | 5 | 0.0 | 0.000 | -2.41 | -2.497 | -2.54 |
| 2022 | no_filter | 27 | 22.2 | 0.224 | -8.02 | -4.593 | -8.02 |
| 2022 | enhanced | 3 | 0.0 | 0.000 | -1.21 | -2.225 | -1.21 |
| 2023 | no_filter | 48 | 22.9 | 0.267 | -12.88 | -4.819 | -13.33 |
| 2023 | enhanced | 7 | 42.9 | 2.479 | -0.38 | -0.428 | -1.30 |
| 2024 | no_filter | 54 | 29.6 | 0.431 | -12.65 | -4.612 | -12.75 |
| 2024 | enhanced | 7 | 14.3 | 0.047 | -2.07 | -2.837 | -2.07 |

**Profitable Years**: No Filter: 0/7 → Enhanced: 0/7

### Momentum Scalping — Yearly Breakdown

| Year | Filter | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|--------|-----|-----|------|--------|--------|
| 2018 | no_filter | 11 | 45.5 | 0.715 | -3.50 | -0.851 | -6.34 |
| 2018 | enhanced | 5 | 40.0 | 0.153 | -4.52 | -1.609 | -5.12 |
| 2019 | no_filter | 4 | 0.0 | 0.000 | -3.93 | -2.277 | -3.93 |
| 2019 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2020 | no_filter | 14 | 64.3 | 1.142 | -1.80 | -0.913 | -3.16 |
| 2020 | enhanced | 4 | 75.0 | 1.904 | -0.14 | -0.096 | -1.00 |
| 2021 | no_filter | 10 | 40.0 | 1.022 | -1.45 | -0.524 | -2.12 |
| 2021 | enhanced | 5 | 60.0 | 2.096 | +1.06 | 0.437 | -2.12 |
| 2022 | no_filter | 8 | 50.0 | 0.514 | -3.17 | -1.054 | -4.84 |
| 2022 | enhanced | 2 | 100.0 | inf | +1.60 | 0.923 | -0.15 |
| 2023 | no_filter | 6 | 33.3 | 0.216 | -1.73 | -2.181 | -1.85 |
| 2023 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2024 | no_filter | 13 | 46.2 | 0.336 | -5.39 | -1.688 | -6.16 |
| 2024 | enhanced | 6 | 50.0 | 0.306 | -3.92 | -1.273 | -4.51 |

**Profitable Years**: No Filter: 0/7 → Enhanced: 2/7

### Liquidation Capture — Yearly Breakdown

| Year | Filter | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|--------|-----|-----|------|--------|--------|
| 2018 | no_filter | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2018 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2019 | no_filter | 1 | 0.0 | 0.000 | -19.10 | -1.003 | -19.10 |
| 2019 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2020 | no_filter | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2020 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2021 | no_filter | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2021 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2022 | no_filter | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2022 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2023 | no_filter | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2023 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2024 | no_filter | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2024 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |

**Profitable Years**: No Filter: 0/7 → Enhanced: 0/7

### Grid Trading — Yearly Breakdown

| Year | Filter | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|--------|-----|-----|------|--------|--------|
| 2018 | no_filter | 7 | 42.9 | 1.031 | -1.25 | -0.068 | -11.56 |
| 2018 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2019 | no_filter | 4 | 75.0 | 123.010 | +12.41 | 1.451 | -3.60 |
| 2019 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2020 | no_filter | 2 | 0.0 | 0.000 | -5.64 | -1.333 | -6.52 |
| 2020 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2021 | no_filter | 9 | 55.6 | 1.050 | -1.22 | -0.049 | -9.89 |
| 2021 | enhanced | 2 | 50.0 | 0.700 | -0.93 | -0.250 | -2.34 |
| 2022 | no_filter | 11 | 18.2 | 0.321 | -17.51 | -1.713 | -22.73 |
| 2022 | enhanced | 2 | 0.0 | 0.000 | -2.54 | -0.590 | -4.83 |
| 2023 | no_filter | 2 | 0.0 | 0.000 | -3.39 | -1.543 | -3.39 |
| 2023 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2024 | no_filter | 12 | 58.3 | 1.166 | -0.08 | 0.036 | -6.37 |
| 2024 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |

**Profitable Years**: No Filter: 1/7 → Enhanced: 0/7

### MA Crossover — Yearly Breakdown

| Year | Filter | Trades | WR% | PF | Net% | Sharpe | MaxDD% |
|------|--------|--------|-----|-----|------|--------|--------|
| 2018 | no_filter | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2018 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2019 | no_filter | 1 | 0.0 | 0.000 | -20.23 | -1.545 | -20.23 |
| 2019 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2020 | no_filter | 1 | 100.0 | inf | +16.54 | 1.332 | -3.35 |
| 2020 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2021 | no_filter | 1 | 100.0 | inf | +14.56 | 1.556 | -1.20 |
| 2021 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2022 | no_filter | 1 | 100.0 | inf | +13.56 | 1.737 | -2.84 |
| 2022 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2023 | no_filter | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2023 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |
| 2024 | no_filter | 1 | 0.0 | 0.000 | -12.43 | -1.523 | -12.43 |
| 2024 | enhanced | 0 | 0.0 | 0.000 | +0.00 | 0.000 | 0.00 |

**Profitable Years**: No Filter: 3/7 → Enhanced: 0/7

---

## Task 2: Mean Reversion — Confidence-Based Position Sizing

### Comparison

| Method | Avg Return | Avg MaxDD | Avg Sharpe | Avg Win Rate |
|--------|------------|-----------|------------|--------------|
| Fixed Size | -7.14% | -27.1% | -0.255 | 46.9% |
| Binary Filter | +1.22% | -10.6% | 0.230 | 56.1% |
| Confidence-Weighted | +1.58% | -4.2% | 0.241 | 0.0% |

### Yearly Breakdown

| Year | Fixed Net% | Binary Net% | Confidence Net% | Fixed MaxDD% | Binary MaxDD% | Confidence MaxDD% |
|------|------------|-------------|-----------------|-------------|---------------|---------------------|
| 2018 | -1.81% | +5.33% | +2.61% | -31.35% | -0.15% | -1.04% |
| 2019 | -21.56% | +3.83% | -5.08% | -38.64% | -7.05% | -7.15% |
| 2020 | -5.44% | -2.78% | -3.22% | -22.91% | -16.01% | -5.17% |
| 2021 | +22.03% | +19.10% | +7.66% | -25.51% | -6.12% | -3.76% |
| 2022 | -32.02% | -21.66% | +0.87% | -39.28% | -27.79% | -7.88% |
| 2023 | -4.23% | +5.85% | +2.50% | -16.58% | -7.85% | -1.97% |
| 2024 | -6.94% | -1.12% | +5.74% | -15.66% | -9.35% | -2.72% |

---

## Task 3: Liquidation Capture on 4h with Enhanced Regime Filter

### Yearly Comparison

| Year | Net% (No Filter) | Net% (With Filter) | Trades (No Filter) | Trades (With Filter) |
|------|------------------|--------------------|--------------------|----------------------|
| 2018 | -20.87% | +0.00% | 2 | 0 |
| 2019 | +21.71% | +13.81% | 3 | 2 |
| 2020 | +28.29% | +17.73% | 7 | 6 |
| 2021 | -15.29% | +0.00% | 1 | 0 |
| 2022 | -13.89% | +0.00% | 2 | 0 |
| 2023 | +20.72% | +20.72% | 6 | 6 |
| 2024 | -4.57% | -4.57% | 2 | 2 |

**Total Net% (No Filter)**: +16.10%
**Total Net% (With Filter)**: +47.69%
**Improvement**: +31.59%

---

## Conclusions

### Task 1 Conclusions

- **Mean Reversion**: Enhanced filter improved returns (-7.14% → +1.22%)
- **VWAP Scalping**: Enhanced filter improved returns (-9.29% → -1.27%)
- **Momentum Scalping**: Enhanced filter improved returns (-3.00% → -0.85%)
- **Liquidation Capture**: Enhanced filter improved returns (-2.73% → +0.00%)
- **Grid Trading**: Enhanced filter improved returns (-2.38% → -0.50%)
- **MA Crossover**: Enhanced filter reduced returns (+1.71% → +0.00%), but may have reduced risk

### Task 2 Conclusions

- Confidence-weighted sizing returned +1.58% vs fixed -7.14% and binary +1.22%
- MaxDD: confidence -4.2% vs fixed -27.1% vs binary -10.6%

### Task 3 Conclusions

- Total Net% improved from +16.10% (no filter) to +47.69% (enhanced filter)
- 2018 (bear market): -20.87% → +0.00% (filter skipped trades)
- 2022 (bear market): -13.89% → +0.00% (filter skipped trades)
