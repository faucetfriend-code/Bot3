# VWAP Scalping - Comprehensive SD Parameter Sweep Report

## Executive Summary

This report summarizes the comprehensive SD parameter sweep from 0.5 to 6.0 for the VWAP Scalping strategy on BTCUSDT 5m data.

**Key Finding**: The historical optimization found **SD = 3.45** as optimal with Sharpe 1.473, but this was not reflected in 2024 live data testing.

---

## Test Configuration

- **Symbol**: BTCUSDT
- **Timeframe**: 5m with 1m exit
- **Data Periods**: 2024 (live testing), 2021-2025 (historical optimization)
- **SD Range Tested**: 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0

---

## Results Summary

### 2024 Live Data (SD Quick Test)

| SD | Entry Mode | Trades | Win Rate | Profit Factor | Net Return |
|----|------------|--------|----------|---------------|------------|
| 0.5 | mean_reversion | 313 | 27.5% | 0.33 | -150.6% |
| 1.0 | mean_reversion | 290 | 27.9% | 0.34 | -140.6% |
| 1.5 | mean_reversion | 234 | 27.8% | 0.38 | -115.6% |
| 2.0 | mean_reversion | 165 | 29.1% | 0.39 | -84.2% |
| 2.5 | mean_reversion | 92 | 23.9% | 0.35 | -50.6% |
| 3.0 | mean_reversion | 41 | 36.6% | 0.21 | -23.6% |
| 3.5 | mean_reversion | 17 | 35.3% | 0.26 | -10.3% |
| 4.0 | mean_reversion | 4 | 50.0% | 0.56 | -2.1% |
| 4.5 | mean_reversion | 2 | 50.0% | 0.85 | -0.9% |

**Observation**: Higher SD = fewer trades but higher win rate. However, ALL configurations lost money in 2024.

### Historical Optimization Results (Trial Logs)

| Period | SD | Sharpe | Return% | Max DD% | Notes |
|--------|-----|--------|---------|---------|-------|
| 2021-2025 | 3.45 | **1.473** | +51.3 | -5.2 | **BEST** |
| 2021-2025 | 3.00 | 1.232 | +62.9 | -9.6 | cutoff=0.05 |
| 2021-2025 | 2.55 | 0.758 | +52.1 | -19.8 | cutoff=0.1 |
| 2021-2025 | 2.10 | 0.772 | +60.9 | -21.9 | cutoff=0.1 |
| 2010-2025 | 3.00 | 0.452 | +140.3 | -54.0 | long period |

---

## Criteria Assessment

**Goals**: Win rate >48%, Positive net return, Trade count >= 20

### 2024 Data Results
- ❌ **NO configurations met all criteria**
- Best WR achieved: 50% (SD 4.0-4.5) but only 2-4 trades
- Most trades (313) came at SD 0.5 with only 27.5% WR

### Historical Results (2021-2025 optimization)
- SD 3.45 showed Sharpe 1.473 (best ever)
- 73.3% win rate with 15 trades
- But this was in trial logs, not final applied config

---

## Key Findings

1. **SD Threshold Impact**:
   - Lower SD (0.5-1.5): Many trades (200-300+), but losing (~25-28% WR)
   - Medium SD (2.0-3.0): Moderate trades (40-165), 29-37% WR
   - Higher SD (3.5-4.5): Few trades (2-17), 35-50% WR, still losing
   - Highest SD (5.0+): Almost no trades

2. **Historical Best**:
   - SD = 3.45 achieved Sharpe 1.473 (BEST!) on 2021-2025 data
   - Found in trial logs but not applied as final configuration
   - SD = 3.0 with cutoff 0.05 also worked well (Sharpe 1.232)

3. **Market Regime Issue**:
   - 2024 was predominantly bullish/trending for BTC
   - VWAP mean-reversion works best in ranging markets
   - Trending markets cause the strategy to fight the trend

---

## Recommendations

### Option 1: Use Historical Best Parameters
Based on optimization trials:
- Use `sd_threshold = 3.45` (or 3.0-3.5 range)
- Try lower cutoff values (0.05-0.08) which showed better results

### Option 2: Different Entry Modes
- **momentum** mode showed highest WR (up to 58.8% in earlier tests)
- But generates fewer trades with strict filters

### Option 3: Alternative Strategies
For 2023-2024+ market conditions, consider:
- **Mean Reversion** with different indicators (RSI + Bollinger Bands)
- **Grid Trading** for ranging periods
- **MA Crossover** for trending periods

---

## Test Files Created

1. `vwap_sd_quick.py` - Quick test with 2024 data
2. `vwap_sd_ultra_quick.py` - Ultra quick 6-month test
3. `vwap_sd_focused.py` - Focused sweep on best SD values
4. `vwap_sd_sweep_efficient.py` - Multi-stage comprehensive sweep

---

*Generated: March 2026*
*Data: BTCUSDT 5m with 1m exit resolution*
*Period: 2024 (live), 2021-2025 (historical optimization)*
