# VWAP Scalping Comprehensive Parameter Sweep Results
## Test Period: 2023-2024 BTCUSDT 5m

---

## Executive Summary

After systematic testing of VWAP Scalping parameters on BTCUSDT 5m data for 2023-2024, **no configuration met all three criteria**:
- ❌ Win rate >45%
- ❌ Positive net return
- ✅ Trade count >= 20 (achievable)

---

## Key Findings

### 1. Entry Mode Performance

| Entry Mode | Trade Count | Win Rate | Profit Factor | Net Return |
|------------|-------------|----------|---------------|------------|
| momentum | 17-76 | 17-59% | 0.15-0.61 | -8% to -43% |
| cross | 28-158 | 14-39% | 0.08-0.29 | -15% to -77% |
| mean_reversion | 79-425 | 15-29% | 0.10-0.41 | -45% to -200% |
| bull_pullback | 0-44 | 0-27% | 0-0.59 | 0% to -22% |
| bear_pullback | 1-9 | 0-22% | 0-0.10 | -1% to -6% |

### 2. Best Performing Configurations

#### Top 5 Results (sorted by Net Return)

| Rank | Entry Mode | Stoch (O/OB) | ADX | RSI | Vol | ATR-S | ATR-T | Trades | WR% | PF | Net% |
|------|------------|---------------|-----|-----|-----|-------|-------|--------|-----|-----|------|
| 1 | momentum | (20,80) | 25 | 45 | 2.0 | 0.5 | 1.5 | 17 | 58.8% | 0.61 | -8.1% |
| 2 | cross | (20,80) | 25 | 45 | 2.0 | 0.7 | 2.0 | 28 | 39.3% | 0.29 | -15.4% |
| 3 | cross | (20,80) | 25 | 45 | 2.0 | 0.6 | 1.8 | 28 | 32.1% | 0.16 | -16.2% |
| 4 | deviation | (20,80) | 25 | 45 | 2.0 | 0.5 | 1.5 | 71 | 31.0% | 0.49 | -37.8% |
| 5 | momentum | (15,85) | 30 | 50 | 2.0 | 0.5 | 1.5 | 76 | 17.1% | 0.15 | -43.0% |

### 3. Historical Comparison

From earlier testing (2020-2024 data), the strategy **did work** with:
- **sd_threshold**: 3.45 (NOT 2.5!)
- **Win rate**: 63.16%
- **Profit factor**: 1.24
- **Sharpe**: 0.53
- **Trades**: 19

This suggests the **SD threshold of 2.5 is too low** for the current market conditions.

---

## Why No Configurations Met Criteria

1. **Market Regime Change**: 2023-2024 was predominantly bullish/trending for BTC
   - VWAP mean-reversion works best in ranging markets
   - Trending markets cause the strategy to fight the trend

2. **Win Rate Too Low**: 
   - Best achieved: 58.8% (momentum mode, only 17 trades)
   - Most configs: 15-35% win rate
   - Even with high R:R, can't overcome low WR + transaction costs

3. **Profit Factor < 1.0**:
   - All configurations show gross losses > gross wins
   - Even winning trades are too small relative to losing trades

---

## Recommendations

### Option 1: Use Higher SD Threshold
Based on 2020-2024 results, try:
- `sd_threshold = 3.45` (or even 3.0-4.0)
- This filters to only the most extreme deviations

### Option 2: Try Different Entry Modes
- **momentum** shows highest WR (up to 58.8%)
- But generates fewer trades with strict filters

### Option 3: Alternative Strategies
For 2023-2024 market conditions, consider:
- **Mean Reversion** (RSI + Bollinger Bands)
- **Grid Trading** (for ranging periods)
- **MA Crossover** (for trending periods)

---

## Test Files Created

1. `vwap_param_sweep_quick.py` - Quick test with 2024 data
2. `vwap_param_sweep_2yr.py` - Full 2-year test with various configs
3. `vwap_atr_test.py` - ATR stop/target optimization
4. `vwap_momentum_test.py` - Momentum mode focus

---

## Parameters Fixed at sd_threshold = 2.5

As requested, all tests used `sd_threshold = 2.5`. This may explain the poor results - historical data suggests higher thresholds work better.

---

*Generated: March 2026*
*Test Data: BTCUSDT 5m with 1m exit resolution*
*Period: 2023-01-01 to 2025-01-01*
