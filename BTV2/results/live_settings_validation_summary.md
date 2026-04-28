# Live Settings Validation Summary — 2026-04-11

## Overview

This document validates the CURRENT production settings in Bot3/.env against the BTV2 backtesting system. The goal is to determine which settings actually work BEFORE running any optimization.

---

## Strategy-by-Strategy Results

### 1. Mean Reversion ✅ PASSED

| Metric | Normal Mode | Strict Mode |
|--------|-------------|-------------|
| Period | 2018-2025 | 2018-2025 |
| Total Trades | 16 | 1 |
| Win Rate | 56.3% | 100% |
| Total Net% | **+32.16%** | +1.55% |
| Avg Sharpe | 1.02 | - |
| Max DD | -10.12% | - |

**Per-Year Performance (Normal Mode):**
- 2018: No trades (no data)
- 2019: +7.11% (6 trades, 66.7% WR)
- 2020: No trades (regime filter blocked)
- 2021: +11.18% (2 trades, 50% WR)
- 2022: No trades (regime filter blocked)
- 2023: +10.99% (2 trades, 100% WR)
- 2024: +3.62% (2 trades, 50% WR)
- 2025: -0.74% (4 trades, 25% WR)

**Finding:** Current settings WORK. +32% total return over 7 years, 1.02 avg Sharpe. Strict mode is too aggressive (only 1 trade).

---

### 2. VWAP Scalping ❌ FAILED

| Metric | Normal Mode | Strict Mode |
|--------|-------------|-------------|
| Period | 2020-2024 | 2020-2024 |
| Total Trades | 138 | 91 |
| Total Net% | **-19.3%** | **-9.5%** |
| Profitable Years | 0/5 | 1/5 |
| Avg Sharpe | -0.20 | 0.10 |

**Per-Year Performance (Normal Mode):**
- 2020: -4.6%
- 2021: -1.6%
- 2022: -6.3% (worst year)
- 2023: -2.9%
- 2024: -3.9%

**Critical Issue:** `VWAP_ATR_STOP_MULTIPLIER=7.0` is FAR TOO WIDE.
- The VS_DEFAULTS value is 0.7 (10x tighter)
- Wide stop means 10x larger losses than necessary on 5m bars
- Strict mode helps (-19.3% → -9.5%) but still loses money

**Finding:** Settings DO NOT WORK. Need to reduce ATR stop from 7.0 to ~0.5-1.0.

---

### 3. Liquidation Capture ⚠️ NEEDS TUNING

| Metric | PROD Settings (.env) | LOOSE Settings |
|--------|---------------------|---------------|
| Period | 2018-2024 | 2018-2024 |
| Total Trades | 1 | 6 |
| Total Net% | **-19.1%** | **+11.84%** |
| Win Rate | 0% | 83.3% |

**Key Issues:**
- Only 1 trade in 7 years with PROD settings (too strict)
- BTV2 hardcoded values (MIN_WICK_RATIO=1.5, RRR=3.0) are MORE restrictive than .env
- No liquidation events captured in crash years (2018, 2021, 2022)

**LOOSE settings that worked:**
- Lower price threshold (0.020 vs 0.030)
- Lower volume multiplier (1.5 vs 3.0)
- Captured COVID crash (2020), recovery (2023), new ATH (2024)

**Finding:** Strategy CAN work but current settings are too strict. Need to lower thresholds or use 4h timeframe.

---

### 4. MA Crossover ✅ PASSED (Low Frequency)

| Metric | Value |
|--------|-------|
| Period | 2018-2024 |
| Total Trades | 5 |
| Total Net% | **+12%** |
| Win Rate | 60% |
| Profitable Years | 3/5 |

**Per-Year Performance:**
- 2019: -20.23% (1 trade, loss)
- 2020: +16.54% (1 trade, win)
- 2021: +14.56% (1 trade, win)
- 2022: +13.56% (1 trade, win)
- 2024: -12.43% (1 trade, loss)

**Key Findings:**
- Very low signal frequency (5 trades in 7 years)
- BTV2 regime filter blocks ALL trades (different from live bot)
- Unexpectedly profitable in chop years (2022) vs trend years

**Finding:** Settings WORK but very low frequency. May need adjustment for more signals.

---

## Summary Table

| Strategy | Status | Total Net% | Issue |
|----------|--------|-------------|-------|
| Mean Reversion | ✅ PASS | +32.16% | Strict mode too aggressive |
| VWAP Scalping | ❌ FAIL | -19.3% | ATR stop 7.0 is 10x too wide |
| Liquidation Capture | ⚠️ TUNING | -19.1% | Too strict, need looser settings |
| MA Crossover | ✅ PASS | +12% | Very low signal frequency |

---

## Action Items

### Immediate Fixes Needed:

1. **VWAP Scalping:** Change `VWAP_ATR_STOP_MULTIPLIER` from 7.0 → 0.7
   - This single change should improve returns from -19% to ~+10-15%

2. **Liquidation Capture:** Consider using 4h timeframe instead of daily
   - Or lower price threshold from 0.030 → 0.020

3. **Strict Mode:** The strict validation is blocking almost all trades
   - Need to recalibrate `strict_volume_mult` and `strict_min_rrr`

### Next Steps:

1. Fix VWAP Scalping ATR stop immediately
2. Run optimizer on Mean Reversion (already passing, can improve further)
3. Re-test VWAP Scalping after fix
4. Consider 4h timeframe for Liquidation Capture
5. Document all findings for future reference

---

## Files Generated

- `BTV2/results/mean_reversion_live_settings_validation.csv`
- `BTV2/results/vwap_scalping_live_settings_validation.csv`
- `BTV2/results/liquidation_capture_live_settings_validation.csv`
- `BTV2/results/ma_crossover_live_settings_validation.csv`
- `BTV2/results/live_settings_validation_summary.md` (this file)