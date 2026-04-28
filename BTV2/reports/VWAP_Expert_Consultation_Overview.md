# VWAP Scalping - Comprehensive Testing Overview

## Executive Summary

We have conducted an extensive parameter optimization campaign for the VWAP Scalping strategy on BTCUSDT (5m timeframe) from 2018-2024. Over **350+ unique parameter combinations** were tested across multiple phases, including V4 market structure features, regime detection, and various entry/exit configurations.

**Bottom Line**: No configuration achieved consistent profitability in 2024 market conditions. The strategy fundamentally struggles with low win rates (20-35%) that cannot be overcome even with high R:R ratios due to the speed of mean-reversion on 5m bars.

---

## Phase 1: Baseline Parameter Testing (2024)

### Timeframe Comparison
| Timeframe | Trades | Win Rate | Net Return | Issue |
|-----------|--------|----------|------------|-------|
| 1m | 2,000-5,000 | 33-38% | -100%+ | Costs destroy profits |
| 5m | Varies | 25-35% | Negative | Not enough wins |
| 15m | ~100-300 | 35-45% | Negative | Too few trades |
| 1h | 6-15 | 67% | +0.8% | Too few trades for significance |

### SD Threshold Tests
| SD | Trades | Win Rate | Net Return | Profit Factor |
|----|--------|----------|------------|---------------|
| 1.0 | 20 | 30.0% | -9.7% | 0.77 |
| 1.5 | 12 | 33.3% | -5.1% | 1.17 |
| **2.0** | **3** | **66.7%** | **+0.6%** | **7.53** |
| 2.5 | 1 | 100% | +1.6% | ∞ |
| 3.0 | 1 | 100% | +1.6% | ∞ |

**Key Finding**: Higher SD = fewer trades but better WR. SD=2.0 was first positive-returning config but only 3 trades (not statistically significant).

---

## Phase 2: Entry Mode Comparison

Tested all 6 entry modes with fixed parameters (SD=2.0, ADX=30, RSI=50, Volume=2.0):

| Entry Mode | Trades | Win Rate | Profit Factor | Net Return |
|------------|--------|----------|---------------|------------|
| **bull_pullback** | 3-5 | 60-67% | 1.32-7.53 | +0.6% to -2% |
| bear_pullback | 3-7 | 14-33% | 0.11-0.14 | -2% to -5% |
| momentum | 0-32 | 0-25% | 0.38 | -17% |
| cross | 1-6 | 0-23% | 0.15 | -29% |
| deviation | 162 | 36% | 0.44 | -84% |
| mean_reversion | 204-622 | 23-33% | 0.23-0.36 | -107% to -270% |

**Key Finding**: `bull_pullback` is clearly the best entry mode (+118% better than mean_reversion).

---

## Phase 3: Filter Testing

### ADX Impact (Ranging Gate)
| ADX | Win Rate | Profit Factor | Best With |
|-----|----------|---------------|-----------|
| 16 | 0% (no trades) | 0 | Too strict |
| 20 | 50% | 0.54 | |
| 25 | 60% | 1.32 | ✅ Best balance |
| 30-35 | 57% | 3.44 | More trades |

### RSI Impact (Strength Gate)
| RSI | Win Rate | Trades |
|-----|----------|--------|
| 35-40 | No trades | 0 |
| 50 | 60% | 5 |
| 55-60 | 50% | 8 |

**Key Finding**: ADX 25-30 and RSI 50 are optimal.

### Volume Multiplier
| Volume Mult | Win Rate | PF | Net Return |
|-------------|----------|-----|------------|
| 1.0 | 37% | 0.32 | -21% |
| 1.5 | 46% | 0.22 | -9% |
| **2.0** | **60%** | **1.32** | **-2%** |

**Key Finding**: Higher volume = better quality trades.

---

## Phase 4: MTF (Multi-Timeframe) Filters

### 15m/1h vs 1h/4h Comparison
| MTF Config | Trades | Win Rate | Net Return |
|------------|--------|----------|------------|
| Original (15m/1h) | 498 | 22.1% | -226% |
| Higher (1h/4h) | 138-420 | 23-24% | -75 to -196% |

**Finding**: Higher MTF reduces trades but doesn't improve win rate.

### HTF Filter Impact
- `use_htf_ema=True` → blocks ALL trades (keep disabled!)
- `use_htf_vwap=True` → minimal impact

---

## Phase 5: Yearly Breakdown (2018-2024)

| Year | Trades | Win Rate | Net Return |
|------|--------|----------|------------|
| 2018 | 371 | 34.8% | -174.9% |
| 2019 | 426 | 23.5% | -200.1% |
| 2020 | 446 | 24.4% | -207.5% |
| 2021 | 395 | 37.2% | -182.5% |
| 2022 | 327 | 23.2% | -162.5% |
| 2023 | 282 | 18.1% | -140.4% |
| 2024 | 394 | 26.1% | -185.3% |

**Finding**: No year was profitable. 2021 had best WR (37.2%).

---

## Phase 6: V4 Market Structure Features

### Reference Levels & TP Modes
| Config | Trades | Win Rate | Net Return |
|--------|--------|----------|------------|
| Baseline (no V4) | 438 | 28.1% | -72.8% |
| nearest TP | 439 | 28.7% | -72.0% |
| Session Filter | 203 | 34.0% | -44.3% |
| Session + nearest | 203 | 34.0% | -43.8% |

### Signal Quality Filters
| Filter | Trades | Impact |
|--------|--------|--------|
| FVG | 0 | ❌ Blocks ALL trades |
| Order Block | 0 | ❌ Blocks ALL trades |
| EQHL | 434 | -10% (negative impact) |

### V4 Features Performance
| Feature | Return Impact | Assessment |
|---------|--------------|------------|
| **OTE (Optimal Trade Entry)** | +37.5% | 🏆 Best V4 feature |
| **MSS** | +26.9% | ✅ Strong |
| **Session Filter** | +19.6% | ✅ Strong |
| **SFP (Reversal Candle)** | +18.6% | ✅ Strong |
| **CVD** | +14.3% | ✅ Positive |

**Best V4 Result**: OTE achieved 48% win rate (meets target!) but only 25 trades with -6.1% net.

---

## Phase 7: Regime Detection Testing

### Methods Tested
1. **ADX-based**: Ranging (<25) vs Trending (>25)
2. **EMA-based**: 1h EMA(9) vs EMA(21) direction
3. **Reference level-based**: Price position relative to PDH/PDL/PWH/PLW
4. **Combined**: All methods together

### Results
| Method | Win Rate | Net Return |
|--------|----------|------------|
| ADX regime switch | 34.3% | -91.6% |
| EMA regime switch | 19.2% | -65.0% |
| Combined | 19.2% | -65.0% |

**Finding**: Regime detection did NOT improve results. The issue is fundamental to the strategy, not market regime identification.

---

## Phase 8: R:R Ratio Testing

### High R:R Hypothesis Test
Tested 7:1, 10:1, 15:1 ratios on best configs.

**Result**: Even extreme R:R ratios failed!

### Critical Discovery
| Metric | Expected | Actual |
|--------|----------|--------|
| Avg Win | ~3.5% (7:1 × 0.5% ATR) | **0.17%** |
| Avg Loss | ~0.5% | 0.21% |
| Profit Factor | ~2.8 | **0.35** |

**Root Cause**: With 1m exit resolution, **0% of trades hit TP**. Price reverses in 2-3 bars (~10 min), traveling only 0.17% before reversal - never reaches high R:R targets.

---

## Phase 9: Comprehensive Multivariate Sweep

### Stage 1: Trade Discovery
Tested all variables simultaneously (144 combinations):

**Best by Trade Count**:
| sd | Mode | ADX | RSI | Vol | Session | Trades | WR% |
|----|------|-----|-----|-----|---------|--------|-----|
| 1.5 | mean_reversion | 25 | 40 | 1.0 | True | 491 | 31% |
| 1.5 | mean_reversion | 25 | 60 | 1.5 | True | 286 | 31% |
| 2.5 | mean_reversion | 35 | 60 | 1.0 | True | 239 | 33% |
| 2.5 | mean_reversion | 35 | 40 | 1.0 | True | 235 | 33% |

**Key Finding**: All top configs use mean_reversion + session filter = True.

### Stage 2: R:R Optimization
Tested 6 R:R ratios on top 5 configs → **No profitable combinations found**.

---

## Phase 10: The 4 Fixes Test

### Fix 1: Tighter R:R (1:1, 1.5:1, 2:1)
| R:R | Trades | WR% | Net% |
|-----|--------|-----|------|
| 1:1 | 125 | 22% | -65% |
| 1.5:1 | 125 | 23% | -65% |
| 2:1 | 125 | 24% | -65% |

### Fix 2: Higher Timeframe (15m, 1h)
| Timeframe | Trades | WR% | Net% |
|-----------|--------|-----|------|
| 15m | 335 | 27% | -164% |
| 1h | ? | ? | ? |

### Fix 3: Trailing Stops
| Config | WR% | Net% |
|--------|-----|------|
| Trail @1.0 ATR | 28% | -64% |
| Trail @1.5 ATR | 30% | -63% |
| Trail @2.0 ATR | 30% | -62% |

### Fix 4: Momentum Mode (bull_pullback)
| Config | Trades | WR% | Net% |
|--------|--------|-----|------|
| bull_pullback 1:1 | 56 | 18% | -30% |
| **cross mode 1:1** | **10** | **30%** | **-5.9%** |

---

## Variable Importance Ranking

| Rank | Variable | Impact Range | Best Value |
|------|----------|--------------|------------|
| 1 | **entry_mode** | +118% | bull_pullback |
| 2 | volume_mult | +19% | 2.0 |
| 3 | sd_threshold | +11% | 2.0-2.5 |
| 4 | use_session_filter | +20% | True |
| 5 | require_reversal_candle | +19% | True |

---

## The Best Configuration Ever Found

**Historical optimization found (in trial logs, never applied)**:
```python
sd_threshold = 3.45
entry_mode = "mean_reversion"
Win Rate: 73.3%
Return: +51.3%
Max Drawdown: -5.17%
Sharpe: 1.473
```

**Our best 2024 result (SD=2.0 + bull_pullback)**:
```python
sd_threshold = 2.0
entry_mode = "bull_pullback"
Win Rate: 66.7%
Profit Factor: 7.53
Net Return: +0.6%
Trades: 3 (not statistically significant)
```

---

## Core Problems Identified

1. **Win rate too low**: 20-35% across all configurations (need 40%+ to be profitable)
2. **Fast mean-reversion**: Price reverses in 2-3 bars on 5m, never reaches high R:R targets
3. **2024 market regime**: Strong bull market favors momentum, not mean-reversion
4. **FVG/OB filters broken**: Block all trades when enabled
5. **Entry signals lack edge**: All entry modes produce similar ~25-30% WR

---

## What Hasn't Been Tried

1. **Different market**: ETH/SOL instead of BTC
2. **Different strategy**: Grid Trading, Momentum Scalping, Mean Reversion on daily
3. **Complete redesign**: Different entry logic entirely
4. **2018-2023 data**: Historical periods where mean-reversion worked better

---

## Testing Summary Statistics

| Metric | Value |
|--------|-------|
| Total parameter combinations tested | 350+ |
| Profitable configurations | 0 |
| Best win rate achieved | 66.7% (SD=2.0 bull_pullback, 3 trades) |
| Best profit factor | 7.53 (same config) |
| Best net return | +0.6% (same config, not significant) |
| Tests with positive return | 3 (all with <5 trades) |
| Data periods tested | 2018-2024 |

---

## Files Generated

- `VWAP_Complete_Results_Summary.md` - Comprehensive results
- `VWAP_Variable_Impact_Report.md` - Variable analysis
- `VWAP_50k_Sweep_Report.md` - Large sweep results
- `VWAP_SD_Sweep_Results.md` - SD threshold analysis
- `VWAP_Historical_Optimization_Findings.md` - Historical best (SD=3.45)
- `vwap_v4_sweep_report.md` - V4 features results
- `regime_vwap_report.md` - Regime detection results
- `vwap_multivariate_sweep_report.md` - Full multivariate results

---

*Report prepared for expert consultation - March 2026*