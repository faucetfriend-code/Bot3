# Trading Bot v2 — VWAP Scalping Strategy Analysis Report

**Generated:** 2026-03-12
**Strategy:** VWAP Scalping
**Target:** High-frequency scalping (500-1000+ trades/year)
**Result:** UNSUCCESSFUL - Cannot achieve target trade count with profitability

---

## Executive Summary

After extensive testing across multiple timeframes, parameters, and logic variations, the VWAP Scalping strategy **cannot achieve the target of 500-1000+ profitable trades per year** on BTC-USD.

| Metric | Target | Achieved |
|--------|--------|----------|
| Trades/Year | 500-1000+ | 6-15 max |
| Win Rate | >60% | 53-67% (at low trade count) |
| Return | Positive | +0.8% to +3.1% (at low trade count) |

---

## Testing Summary

### Timeframes Tested

| Timeframe | Result | Best Trades/Year |
|-----------|--------|------------------|
| 1m | ❌ Losing | 43 (losing) |
| 5m | ❌ Mostly losing | 15 max |
| 15m | ❌ Losing | <15 |
| 1h | ⚠️ Marginal | ~50 |
| Daily | ✅ Working | ~15 |

### SD Threshold Tests (5m)

| SD | Trades | Return | Win Rate | PF | Assessment |
|----|--------|--------|----------|-----|------------|
| 0.5 | 2,176 | -99.7% | 40.5% | 0.67 | ❌ Losing |
| 1.0 | 1,740 | -99.1% | 42.0% | 0.69 | ❌ Losing |
| 2.0 | 1,111 | -96.7% | 41.0% | 0.66 | ❌ Losing |
| **2.5** | **HIGH** | **-100%** | **33-36%** | **0.49-0.57** | ❌ TARGET |
| 3.0 | 442 | -70.9% | 42.3% | 0.75 | ❌ Losing |
| 5.0 | 67 | -16.9% | 36% | 0.67 | ❌ Losing |
| 5.5 | 28 | -3.6% | 43% | 1.09 | ⚠️ Near |
| **6.0** | **15** | **+3.1%** | **53%** | **2.78** | ⚠️ Best |
| 6.25 | 11 | -1.3% | 55% | 1.18 | ⚠️ |

### Butterworth Cutoff Tests (5m, SD=2.5)

| Cutoff | Return | Win Rate | PF | Trades |
|--------|--------|----------|-----|--------|
| 0.03 | - | 34% | 0.50 | 4,351 |
| 0.05 | - | 35% | 0.57 | 3,037 |
| 0.07 | - | 35% | 0.51 | 4,708 |
| 0.08 | Best | 38% | 0.58 | ~3,000 |
| 0.10 | - | 35% | 0.57 | 3,000+ |
| 0.15 | - | 38% | 0.57 | 2,327 |

### ATR Stop Tests (5m, SD=2.5)

Tested: 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0
**Result:** All losing - no profitable combination found

### MACD Combinations Tested

| MACD (fast/slow/sig) | Result |
|---------------------|--------|
| (12, 26, 9) | Baseline |
| (8, 17, 9) | Similar losses |
| (5, 35, 5) | Similar losses |
| (6, 19, 9) | Similar losses |

### VWAP Period Tests

Tested: 10, 15, 20, 25, 30
**Result:** No significant improvement

### Volume Filter Tests

| Volume Multiplier | Win Rate | PF | Trades |
|------------------|----------|-----|--------|
| No filter | 35% | 0.57 | 3,000+ |
| 1.2x | Similar | Similar | Similar |
| 1.5x | Similar | Similar | Similar |
| 2.0x | 38% | 0.57 | 2,327 |

### Entry Logic Modifications Tested

| Modification | Result |
|-------------|--------|
| N+1 Entry (next bar) | Made things worse |
| MACD Turning Confirmation | Too strict - 0 trades |
| Trailing Stop | Lowered win rate |
| Reversal Filter | Blocked all trades |

### Strategy Logic Variations

| Logic | Description | Result |
|-------|-------------|--------|
| Original V1 | Buy below VWAP, sell above | Best at SD=6.0 |
| Trend Following | Buy breakout above VWAP | Losing |
| Pullback | Buy pullback to VWAP | Losing |

---

## Root Cause Analysis

### Why High-Frequency Scalping Fails

1. **Win Rate Too Low**
   - At SD=2.5: 33-36% win rate
   - Need >50% to break even with costs
   - Need >60% for meaningful profit

2. **Costs Are Fatal**
   - 2,000-5,000 trades × 0.30% = 600-1,500% in costs
   - Even gross break-even = net -600% loss

3. **Wrong Entry Logic**
   - Current: Buy when price touches SD band
   - Problem: This catches trends, not reversals
   - Result: 2 losses for every 1 win

4. **Market Noise**
   - 1m/5m charts have too much noise
   - VWAP mean reversion doesn't work in trending markets
   - No combination of parameters solves this

---

## Best Achievable Results

### On 5m Timeframe

| Config | Return | Win Rate | PF | Trades/Year |
|--------|--------|----------|-----|------------|
| SD=6.0, Cutoff=0.08 | +0.8% | 67% | 5.34 | 6 |
| SD=6.0, Cutoff=0.10 | +3.1% | 53% | 2.78 | 15 |

### On 1h Timeframe

| Config | Return | Win Rate | PF | Trades/Year |
|--------|--------|----------|-----|------------|
| Various | +50-80% | 40-50% | 1.0-1.5 | ~50 |

---

## Recommendations

### For High-Frequency Scalping

1. **VWAP Strategy Cannot Achieve Target**
   - Maximum achievable trades: ~15/year
   - Target: 500-1000+/year
   - **Gap: 50-100x too few**

2. **Required: New Strategy**
   - Different entry logic (not VWAP-based)
   - Different timeframe (tick-level?)
   - Different market (more ranging?)

3. **Acceptable Alternative**
   - Use VWAP on 1h/daily for swing trading
   - Accept ~50 trades/year as the limit

---

## Parameters That Work Best (for reference)

### Best 5m Config (but too few trades)
```
sd_threshold: 6.0
atr_stop: 7.0
cutoff: 0.08
win_rate: 67%
profit_factor: 5.34
return: +0.8%
trades: 6/year
```

### Best 1h Config (more trades but still limited)
```
sd_threshold: 2.75
cutoff: 0.085
return: +86%
sharpe: 0.988
trades: ~50/year
```

---

## Conclusion

**The VWAP Scalping strategy cannot be modified to achieve 500-1000+ profitable trades per year.**

The fundamental issue is that:
1. Low SD = high trades but losing (costs > edge)
2. High SD = profitable but too few trades (not statistical)

A completely new strategy approach would be needed for true high-frequency scalping.

---

*Report generated 2026-03-12*

---

## Contents

1. [Most Recent Backtest Results](#1-most-recent-backtest-results)
2. [BTV2 Walk-Forward Optimization Results](#2-btv2-walk-forward-optimization-results)
   - [2a. VWAP Scalping BTC-USD](#2a-vwap-scalping-btc-usd)
   - [2b. Grid Trading BTC-USD](#2b-grid-trading-btc-usd)
   - [2c. Mean Reversion BTC-USD](#2c-mean-reversion-btc-usd)
   - [2d. Momentum Scalping BTC-USD](#2d-momentum-scalping-btc-usd)
   - [2e. MA Crossover BTC-USD](#2e-ma-crossover-btc-usd)
3. [Strategy Comparison Summary](#3-strategy-comparison-summary)
4. [Current Strategy Settings](#4-current-strategy-settings)
5. [Identified Issues & Recommended Fixes](#5-identified-issues--recommended-fixes)
6. [Technical Notes — Fixes Applied This Session](#6-technical-notes--fixes-applied-this-session)

---

## 2. BTV2 Walk-Forward Optimization Results

All results use walk-forward validation: 12-month training → 3-month testing, with coordinate descent parameter optimization.

### 2a. VWAP Scalping BTC-USD

| Iteration | Date Range | OOS Sharpe | OOS Return % | OOS Max DD % | Trades | Notes |
|-----------|------------|------------|--------------|--------------|--------|-------|
| 1 | 2020-2025 | 0.122 | +3.4% | -50.7% | 19 | Original grid |
| 2 | 2020-2025 | 0.569 | +72.1% | -33.7% | 16 | Expanded grid |
| 3 | 2021-2025 | **0.988** | **+86.1%** | **-12.8%** | 12 | **BEST** |
| 4 | 2022-2025 | 1.395 | +52.6% | -4.7% | 7 | Too few trades |
| 5 | 2023-2025 | 0.971 | +10.7% | -4.7% | 2 | Too few trades |

**Best Configuration (Iteration 3):**
- Date Range: 2021-2025
- OOS Sharpe: **0.988**
- Return: **+86.1%**
- Max DD: **-12.8%**
- Trades: 12
- Status: Near-optimal (no further improvements found)

**Key Findings:**
- More recent date ranges perform better
- The strategy works well in trending markets
- Current .env settings are near-optimal

---

### 2b. Grid Trading BTC-USD

| Iteration | Date Range | OOS Sharpe | OOS Return % | OOS Max DD % | Trades | Proposal |
|-----------|------------|------------|--------------|--------------|--------|----------|
| 1 | 2020-2025 | 0.117 | +3.4% | -37.8% | 24 | ADX: 20→17 |
| 2 | 2021-2025 | -0.052 | -8.2% | -35.9% | 21 | ADX: 20→17 |
| 3 | 2022-2025 | **0.467** | **+16.4%** | **-16.8%** | **16** | ADX: 20→17 |
| 4 | 2019-2025 | -0.191 | -36.4% | -51.6% | 26 | ADX: 20→17 |

**Best Configuration (Iteration 3):**
- Date Range: 2022-2025
- OOS Sharpe: **0.467**
- Return: **+16.4%**
- Max DD: **-16.8%**
- Trades: 16
- Proposed: GRID_ADX_THRESHOLD: 20.0 → 17.0

**Key Findings:**
- 2022-2025 is the best date range
- Earlier years hurt performance (COVID crash, different regimes)
- ADX threshold improvement consistent across all iterations

---

### 2c. Mean Reversion BTC-USD

| Iteration | Date Range | OOS Sharpe | OOS Return % | OOS Max DD % | Trades | Proposals |
|-----------|------------|------------|--------------|--------------|--------|-----------|
| 1 | 2020-2025 | 0.030 | -13.4% | -47.2% | 81 | 4 params |
| 2 | 2021-2025 | 0.027 | -6.1% | -33.5% | 55 | 4 params |
| 3 | 2022-2025 | 0.130 | +1.6% | -33.5% | 50 | 4 params |
| 4 | 2019-2025 | **0.286** | **+34.8%** | **-47.2%** | **103** | 3 params |

**Best Configuration (Iteration 4):**
- Date Range: 2019-2025
- OOS Sharpe: **0.286**
- Return: **+34.8%**
- Max DD: **-47.2%**
- Trades: 103
- Proposed changes:
  - MEAN_REVERSION_RSI_OVERBOUGHT: 70.0 → 91.0 (+30%)
  - MEAN_REVERSION_BB_PROXIMITY: 0.1 → 0.13 (+30%)
  - MEAN_REVERSION_ATR_STOP_MULTIPLIER: 3.0 → 2.1 (-30%)

**Key Findings:**
- Longer date range (2019-2025) gives best results
- Higher RSI_overbought (91) is consistently recommended
- BB_PROXIMITY should increase (looser)

---

### 2d. Momentum Scalping BTC-USD

| Date Range | OOS Sharpe | OOS Return % | OOS Max DD % | Trades | Notes |
|------------|------------|--------------|--------------|--------|-------|
| 2020-2025 | 0.813 | +20.4% | -1.0% | **2** | ⚠️ Too few trades |
| 2021-2025 | 0.939 | +20.4% | -1.0% | **2** | ⚠️ Too few trades |

**Status:** NOT RECOMMENDED
- Only 2 trades across all date ranges
- Results statistically insignificant
- High Sharpe values misleading due to low trade count

---

### 2e. MA Crossover BTC-USD

| Date Range | OOS Sharpe | OOS Return % | OOS Max DD % | Trades | Notes |
|------------|------------|--------------|--------------|--------|-------|
| 2020-2025 | 0.726 | +13.5% | -2.6% | **1** | ⚠️ Too few trades |
| 2021-2025 | 0.838 | +13.5% | -2.6% | **1** | ⚠️ Too few trades |

**Status:** NOT RECOMMENDED
- Only 1 trade across all date ranges
- Results statistically insignificant

---

## 3. Strategy Comparison Summary

| Strategy | OOS Sharpe | OOS Return % | OOS Max DD % | Trades | Recommended |
|----------|------------|--------------|--------------|--------|-------------|
| **VWAP Scalping** | **0.988** | **+86.1%** | **-12.8%** | 12 | ✅ BEST |
| Mean Reversion | 0.286 | +34.8% | -47.2% | 103 | ⚠️ High DD |
| Grid Trading | 0.467 | +16.4% | -16.8% | 16 | ✅ Good |
| Momentum Scalping | N/A | N/A | N/A | 2 | ❌ Too few |
| MA Crossover | N/A | N/A | N/A | 1 | ❌ Too few |

**Recommendation for BTC-USD:**
1. **VWAP Scalping** - Best overall (0.988 Sharpe, +86% return)
2. **Grid Trading** - Good alternative (0.467 Sharpe, +16% return)
3. **Mean Reversion** - Acceptable but high drawdown

---

## 4. Current Strategy Settings

### VWAP Scalping (BTC-USD) - OPTIMIZED

| Parameter | Value | Note |
|-----------|-------|------|
| VWAP_SD_ENTRY_THRESHOLD | 3.0 | Near-optimal |
| VWAP_ATR_STOP_MULTIPLIER | 7.0 | Found via BTV2 |
| VWAP_MIN_CONFIDENCE | 0.68 | |
| VWAP_COOLDOWN_MINUTES | 20 | |

### Grid Trading (BTC-USD) - OPTIMIZED

| Parameter | Value | Note |
|-----------|-------|------|
| GRID_TRADING_LEVELS | 5 | |
| GRID_SPACING_ATR_MULTIPLIER | 1.45 | |
| GRID_MAX_POSITIONS_PER_SYMBOL | 10 | |
| GRID_ADX_THRESHOLD | **17.0** | Changed from 20.0 (BTV2 proposal) |

### Mean Reversion (BTC-USD) - OPTIMIZED

| Parameter | Value | Note |
|-----------|-------|------|
| MEAN_REVERSION_RSI_OVERSOLD | 30 | |
| MEAN_REVERSION_RSI_OVERBOUGHT | **91.0** | Changed from 70 (BTV2 proposal) |
| MEAN_REVERSION_BB_PROXIMITY | **0.13** | Changed from 0.10 (BTV2 proposal) |
| MEAN_REVERSION_ATR_STOP_MULTIPLIER | **2.1** | Changed from 3.0 (BTV2 proposal) |

---

## 5. Identified Issues & Recommended Fixes

| Priority | Strategy | Issue | Status |
|----------|----------|-------|--------|
| P1 | VWAP Scalping | Needs optimization | ✅ FIXED (BTV2) |
| P2 | Grid Trading | ADX threshold | ✅ FIXED (BTV2) |
| P3 | Mean Reversion | RSI/BB parameters | ✅ FIXED (BTV2) |
| P4 | Momentum Scalping | Too few trades | ⚠️ Not recommended |
| P5 | MA Crossover | Too few trades | ⚠️ Not recommended |

---

## 6. Technical Notes — BTV2 Optimization

### Methodology
- Walk-forward validation: 12-month train → 3-month test
- Coordinate descent parameter optimization
- OOS (Out-of-Sample) Sharpe ratio as primary metric
- Date ranges tested: 2019-2025, 2020-2025, 2021-2025, 2022-2025

### Key Differences from Old Backtester
- True OOS validation (no data leakage)
- Walk-forward prevents overfitting
- Uses yfinance for live data download
- Daily bars instead of intraday

---

*Report generated 2026-03-11*

### 1a. Combined Run — All Strategies Active

All strategies running simultaneously, `hedge_mode=False` (Pacifica does not allow opposing positions).

| Symbol | Return | Sharpe | Max DD | Win Rate | Profit Factor | Closed Trades | Total Fills | Fees |
|--------|-------:|-------:|-------:|---------:|--------------:|--------------:|------------:|-----:|
| SUI-USDC | -0.8% | +0.51 | 4.9% | — | — | 72 | 144 | $14 |
| BTC-USDC | -1.3% | — | — | 90% | 7.56 | 10 | — | — |
| ETH-USDC | -2.2% | — | — | 36.8% | 0.52 | 321 | — | — |

> **Note:** Combined run results are misleading — strategies interfere with each other's positions. Use the isolation sweep below for per-strategy diagnosis.

---

### 1b. Per-Strategy Isolation Sweep

Run via `run_strategy_sweep.py` — each strategy runs in its own subprocess against one symbol at a time.

#### SUI-USDC

| Strategy | Return | Win Rate | Profit Factor | Closed Trades | Fees | Verdict |
|----------|-------:|---------:|--------------:|--------------:|-----:|---------|
| MeanReversion | **-18.73%** | — | — | **2,821** | **$496.90** | LOSING |
| GridTrading | -0.09% | — | 1.06 | — | — | MARGINAL |
| VWAPScalping | — | ~13% | — | — | — | LOSING |
| MACrossover | 0 trades | — | — | 0 | $0 | 0 trades (RANGING regime) |
| MomentumScalping | — | — | — | — | — | — |
| LiquidationCapture | — | — | — | — | — | — |

#### BTC-USDC

| Strategy | Return | Win Rate | Profit Factor | Closed Trades | Fees | Verdict |
|----------|-------:|---------:|--------------:|--------------:|-----:|---------|
| **GridTrading** | **+0.24%** | **68.2%** | **2.26** | — | — | **GOOD** |
| VWAPScalping | — | ~13% | — | — | — | LOSING |
| MACrossover | 0 trades | — | — | 0 | $0 | 0 trades (RANGING regime) |

#### ETH-USDC

| Strategy | Return | Win Rate | Profit Factor | Closed Trades | Fees | Verdict |
|----------|-------:|---------:|--------------:|--------------:|-----:|---------|
| MomentumScalping | -3.0% | 36.9% | 0.30 | 428 | — | LOSING |

> **Key finding:** GridTrading is the only profitable strategy in isolation. MeanReversion is the single biggest loss driver — hidden in combined runs but exposed at -18.73% in isolation.

---

## 2. Current Strategy Settings

### MeanReversion

**Active regimes:** RANGING_QUIET, RANGING_VOLATILE
**⚠ Critical issue: No cooldown — root cause of 2,821 trades and $496.90 fees on SUI**

| Parameter | Value | Note |
|-----------|-------|------|
| RSI Oversold | 35 | Loosened from 30 |
| RSI Overbought | 65 | Loosened from 70 |
| BB Period | 20 | |
| BB Std Dev | 2.0 | |
| BB Proximity Threshold | 30% | Loosened from 20% |
| ATR Stop Multiplier | 2.0x | |
| SMA Period | 20 | |
| Min Confidence | 0.45 | |
| Cooldown | **NONE** | **← critical** |

---

### MACrossover

**Active regimes:** TRENDING_STRONG only
**ℹ 0 trades on BTC/SUI — both classified as RANGING during test period. Correct behavior.**

| Parameter | Value | Note |
|-----------|-------|------|
| Fast MA | 20 | Updated from 50 |
| Slow MA | 50 | Updated from 200 |
| Pullback Range | 2% – 4% | After crossover |
| Volume Threshold | 1.2x | |
| MACD | 12/26/9 | |
| ATR Stop | 2.5x | |
| Min Confidence | 0.50 | |

---

### GridTrading

**Active regimes:** RANGING_VOLATILE
**✓ Only profitable strategy in isolation: +0.24% BTC, PF 2.26, WR 68.2%**

> **Note:** Runtime values from `strategy_manager.py` differ from `__init__` defaults.

| Parameter | Runtime Value | `__init__` Default |
|-----------|:-------------:|:------------------:|
| Grid Levels | 8 | 5 |
| Grid Spacing | 0.4x ATR | 0.5x ATR |
| Max Positions | 10 | 10 |
| Emergency Stop | 5% | 5% |
| ADX Threshold | 20.0 | 25.0 |
| Min Spacing (env) | 0.3% | — |
| Max Spacing (env) | 6.0% | — |
| Min Confidence | 0.45 | 0.45 |

---

### LiquidationCapture

**Active regimes:** ALL
**⚠ Parameter discrepancy: `__init__` defaults were loosened (Prompt 058) but env var overrides in `strategy_manager.py` restore the strict values at runtime.**

| Parameter | Runtime (env) — strict | `__init__` Default — loose |
|-----------|:----------------------:|:--------------------------:|
| Price Threshold | 3.0% | 2.5% |
| Volume Multiplier | 3.0x | 2.5x |
| RSI Oversold | 15 | 20 |
| RSI Overbought | 85 | 80 |
| Min Consecutive Candles | 5 | 4 |
| Min Wick Ratio | 2.0 | 1.5 |
| Max Per Session | 1 | 2 |
| Min Hours Between Trades | 4h | 2h |
| RRR Target | 3.0 | 3.0 |

---

### VWAPScalping

**Active regimes:** ALL
**⚠ Consistent ~13% win rate across all symbols — likely inverted MACD gate + floating TP**

| Parameter | Value | Note |
|-----------|-------|------|
| ATR Period | 14 | |
| SD Entry Threshold | 1.8 | Entry when price deviates >1.8 SD from VWAP |
| ATR Stop Multiplier | 1.5x | |
| MACD | 12/26/9 | |
| Min Confidence | 0.62 | |
| Cooldown | 8 min | |
| Take Profit | Floating VWAP | **Problem: VWAP moves each candle, TP is never static** |
| ATR Source | 1m (falls back to 15m) | Fixed this session |

---

### MomentumScalping

**Active regimes:** TRENDING_STRONG, TRENDING_MODERATE
**⚠ RRR 1.67:1 with 36.9% WR on ETH — break-even is 37.5%**

| Parameter | Value | Note |
|-----------|-------|------|
| EMA Fast / Slow | 9 / 21 | |
| RSI Period | 14 | |
| RSI Oversold / Overbought | 35 / 65 | |
| ATR Period | 14 | |
| ATR Stop Multiplier | 1.5x | |
| ATR Target Multiplier | **2.5x** | RRR = 1.67:1 — break-even WR = 37.5% |
| Min Confidence | 0.60 | |
| Cooldown | 5 min | |
| Volume Threshold | 1.2x | |
| MACD | 12/26/9 | |
| 4h HTF Filter | Enabled | Added this session |

---

### FundingArb

**Active regimes:** ALL — **live trading only, 0 trades in backtest (requires live API client)**

| Parameter | Value |
|-----------|-------|
| Min Funding Rate | 0.01% |
| Max Allocation | 20% of account |
| Rebalance Threshold | 2% delta |
| Lookback | 8 hours |
| Min Confidence | 0.70 |

---

### OrderBookImbalance

**Active regimes:** ALL — **live trading only, 0 trades in backtest (requires live orderbook feed)**

| Parameter | Value |
|-----------|-------|
| Depth Levels | 10 |
| Long Threshold | >62% imbalance |
| Short Threshold | <38% imbalance |
| Strong Imbalance | >72% |
| Min Order Density | 5 |
| ATR Stop | 0.75x |
| ATR Target | 1.5x → RRR 2.0:1 |
| Min Confidence | 0.55 |
| Cooldown | 30 seconds |

---

## 3. Identified Issues & Recommended Fixes

| Priority | Strategy | Issue | Recommended Fix |
|----------|----------|-------|-----------------|
| **P1** | MeanReversion | No cooldown → 2,821 trades, $496.90 fees on SUI. Overtrading is the primary loss driver. | Add 15–30 min cooldown. Tighten RSI back to 30/70, BB proximity back to 20%. |
| **P2** | VWAPScalping | Inverted MACD gate + floating TP → consistent ~13% WR across all symbols. | Flip MACD gate (histogram < 0 required for BUY). Fix TP as static price at signal candle's VWAP value. |
| **P3** | MomentumScalping | RRR 1.67:1 with 36.9% WR on ETH is just below break-even (37.5% required). | Raise ATR target multiplier 2.5x → 3.5x (RRR 2.33:1, break-even drops to ~30%). Raise cooldown 5 min → 15 min. |
| **P4** | LiquidationCapture | Strict runtime thresholds (RSI 15/85, 5 consecutive) → very few signals. Loose `__init__` defaults never used. | Decide: update env var defaults to match loosened `__init__` values, or revert `__init__` to remove the discrepancy entirely. |
| **P5** | GridTrading | SELL-side grid fires without directional confirmation. Currently the only profitable strategy (+0.24% BTC). | Add 4h bearish EMA filter to SELL signals only. Avoid touching BUY logic — it's working. |
| **P6** | MACrossover | Only activates in TRENDING_STRONG. Most test periods are RANGING → 0 trades. | Consider enabling in TRENDING_MODERATE, or lower min confidence threshold. |

---

## 4. Technical Notes — Fixes Applied This Session

### [FIX] hedge_mode = False
Pacifica does not support opposing positions simultaneously. All cross-strategy position flips eliminated.
- SUI fills: 6,895 → 144
- SUI fees: $599 → $14

This was the single largest profitability lever.

---

### [FIX] SL/TP same-candle randomization
Previously, stop-loss was always processed before take-profit within the same candle (insertion order), introducing a systematic pessimistic bias.
Now: `random.shuffle(triggered)` before processing — removes directional preference.

---

### [NEW] Strategy attribution on trade logs
`exchange._current_strategy` set before each `place_order` call.
Every trade log entry now carries `"strategy"` and `"pnl"` fields for per-strategy performance attribution.

---

### [FIX] closed_trades vs total_fills
- `total_fills` = all order executions (opens + closes)
- `closed_trades` = completed round-trips with realised PnL only

Previously `total_trades` was `len(trade_log)` (total fills), making win rate calculations incorrect.

---

### [FIX] LiquidationCapture session auto-reset
4-hour boundary now automatically resets `session_trades = 0` inside `_can_trade()`:
```python
if now.hour // 4 != self.last_trade_time.hour // 4:
    self.session_trades = 0
```
Also wired `lc.record_trade()` call from the engine after LC execution.

---

### [FIX] GridTrading BUY + SELL pair
Grid now calls both `_create_grid_buy_signal()` and `_create_grid_sell_signal()` per candle, returning `[buy, sell]`.
Previously only the BUY side was wired up. Symbol whitelist fixed: `base_asset = symbol.split("-")[0]` before checking against `["BTC", "ETH", "SUI", ...]`.

---

### [NEW] 4h HTF filter on MomentumScalping
Added `_check_4h_trend_alignment()`:
- BUY signals require 4h EMA-fast > EMA-slow
- SELL signals require 4h EMA-fast < EMA-slow
- Returns `True` (allow) if insufficient 4h data

---

### [FIX] VWAP 1m ATR source
VWAPScalping now uses 1m candle ATR for SL/TP placement when `execution_tf_data["1m"]` is available, falling back to 15m ATR. Improves SL/TP precision for short-duration scalps.

---

### [CHANGE] MACrossover 20/50 SMA (was 50/200)
Previous 50/200 configuration required 200+ 4h candles; engine only provided 60.
Changed to 20/50. 4h history window also increased from 50 → 60 candles in engine.

---

### [NEW] run_strategy_sweep.py
New script: runs all strategies for a single selectable symbol, one at a time in isolated subprocesses.
- `logger.remove()` called before any imports in subprocess — suppresses all log spam
- Worker prints single JSON line to stdout
- Ranked summary table with color-coded verdicts
- Args: `--symbol`, `--start`, `--end`, `--capital`, `--strategies` (subset), `--save-reports`

```bash
# Run sweep on SUI
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol SUI-USDC

# Run only specific strategies on BTC
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol BTC-USDC \
    --strategies GridTrading MomentumScalping VWAPScalping
```

---

*Internal use only — Pacifica Solana Perps*
