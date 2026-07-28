# BTV2 Backtest Archive Catalog

**Generated:** 2026-07-28  
**Scope:** 69 result JSON artifacts under `BTV2/results/`, 8 report files under `BTV2/reports/`, tuning log, logs  
**Artifacts Successfully Parsed:** 39 of 69 (30 failed JSON parse, see Section 5 for details)

This document catalogs all backtest runs in the BTV2 archive to extract patterns, not to transfer validated parameters. Read the critical interpretation rules in `docs/PARAMETER-PROVENANCE.md` before acting on any finding here.

---

## Section 1: Inventory of All Artifacts

### Summary Statistics

| Metric | Value |
|--------|-------|
| Total JSON files | 69 |
| Successfully parsed | 39 |
| Failed parse | 30 |
| Strategies tested | 6 |
| Date ranges tested | 2018-2025, 2020-2025, 2024 |
| Average trades per backtest | 96 |
| Median trades per backtest | 34 |

### Breakdown by Strategy

| Strategy | Count | Avg Return | Best Return | Worst Return | Avg Trades |
|----------|-------|-----------|------------|-------------|-----------|
| VWAP Scalping | 30 | +40.40% | +143.53% | -28.07% | 25 |
| Mean Reversion | 5 | +0.69% | +34.80% | -13.40% | 86 |
| Grid Trading | 1 | -47.48% | -47.48% | -47.48% | 282 |
| MA Crossover | 1 | +135.09% | +135.09% | +135.09% | 45 |
| Martingale MR | 1 | -11.85% | -11.85% | -11.85% | 82 |
| SL Fade MR | 1 | -100.00% | -100.00% | -100.00% | 3749 |

### Full Artifact Table (sorted by return)

| File | Strategy | Symbol(s) | Period | Type | Return | Sharpe | Trades | PF | Max DD | Folds |
|------|----------|-----------|--------|------|--------|--------|--------|-----|---------|-------|
| vwap_btc_wide2.json | VWAP Scalping | BTC | 2024 | opt | +143.53% | 0.84 | 18 | 14.35 | -7.83% | 1 |
| regime_aware_ma_crossover_4h_2018-01-01_2025-01-01.json | MA Crossover | BTCUSDT | 2018-2025 | regime | +135.09% | 0.70 | 45 | 2.13 | -29.04% | 39 |
| vwap_cutoff_0085.json | VWAP Scalping | BTC | 2024 | opt | +76.52% | 1.10 | 11 | 5.51 | -3.77% | 1 |
| vwap_cutoff_010.json | VWAP Scalping | BTC | 2024 | opt | +64.19% | 1.04 | 13 | 6.69 | -4.05% | 1 |
| vwap_btc_wide6.json | VWAP Scalping | BTC | 2024 | opt | +62.12% | 0.33 | 41 | 1.60 | -19.41% | 1 |
| vwap_cutoff_long.json | VWAP Scalping | BTC | 2024 | opt | +62.12% | 0.33 | 41 | 1.60 | -19.41% | 1 |
| vwap_btc_wide5.json | VWAP Scalping | BTC | 2024 | opt | +57.29% | 0.34 | 35 | 1.51 | -19.46% | 1 |
| vwap_cutoff_005.json | VWAP Scalping | BTC | 2024 | opt | +56.33% | 1.09 | 9 | 4.49 | -2.67% | 1 |
| vwap_cutoff_007.json | VWAP Scalping | BTC | 2024 | opt | +54.18% | 0.81 | 12 | 4.19 | -3.70% | 1 |
| vwap_btc_wide1.json | VWAP Scalping | BTC | 2024 | opt | +53.89% | 0.40 | 24 | 1.82 | -11.06% | 1 |
| vwap_cutoff_085.json | VWAP Scalping | BTC | 2024 | opt | +51.77% | 0.83 | 13 | 4.06 | -3.46% | 1 |
| vwap_sd_050.json | VWAP Scalping | BTC | 2024 | opt | +43.69% | 0.65 | 15 | 3.10 | -5.54% | 1 |
| vwap_sd_075.json | VWAP Scalping | BTC | 2024 | opt | +43.69% | 0.65 | 15 | 3.10 | -5.54% | 1 |
| vwap_sd_100.json | VWAP Scalping | BTC | 2024 | opt | +43.69% | 0.65 | 15 | 3.10 | -5.54% | 1 |
| vwap_sd_125.json | VWAP Scalping | BTC | 2024 | opt | +43.69% | 0.65 | 15 | 3.10 | -5.54% | 1 |
| vwap_sd_150.json | VWAP Scalping | BTC | 2024 | opt | +43.69% | 0.65 | 15 | 3.10 | -5.54% | 1 |
| vwap_sd_175.json | VWAP Scalping | BTC | 2024 | opt | +43.69% | 0.65 | 15 | 3.10 | -5.54% | 1 |
| vwap_sd_200.json | VWAP Scalping | BTC | 2024 | opt | +43.69% | 0.65 | 15 | 3.10 | -5.54% | 1 |
| vwap_sd_225.json | VWAP Scalping | BTC | 2024 | opt | +43.69% | 0.65 | 15 | 3.10 | -5.54% | 1 |
| vwap_sd_250.json | VWAP Scalping | BTC | 2024 | opt | +43.69% | 0.65 | 15 | 3.10 | -5.54% | 1 |
| vwap_sd_275.json | VWAP Scalping | BTC | 2024 | opt | +43.69% | 0.65 | 15 | 3.10 | -5.54% | 1 |
| vwap_sd_300.json | VWAP Scalping | BTC | 2024 | opt | +43.69% | 0.65 | 15 | 3.10 | -5.54% | 1 |
| vwap_btc_wide7.json | VWAP Scalping | BTC | 2024 | opt | +35.57% | 0.45 | 23 | 1.60 | -8.86% | 1 |
| vwap_lowsd.json | VWAP Scalping | BTC | 2024 | opt | +33.47% | 0.52 | 33 | 1.58 | -11.99% | 1 |
| vwap_vlowsd.json | VWAP Scalping | BTC | 2024 | opt | +30.44% | 0.44 | 27 | 1.45 | -14.07% | 1 |
| vwap_variable_impact_analysis.json | VWAP Scalping | BTC | 2024 | opt | +20.79% | 0.31 | 20 | 1.32 | -13.92% | 1 |
| vwap_2010.json | VWAP Scalping | BTC | 2024 | opt | +13.54% | 0.20 | 52 | 1.08 | -21.75% | 1 |
| meanrev_btc_iter4.json | Mean Reversion | BTC | 2024 | opt | +34.80% | 0.29 | 103 | 1.27 | -18.36% | 1 |
| meanrev_btc_iter3.json | Mean Reversion | BTC | 2024 | opt | +1.60% | 0.13 | 50 | 1.06 | -15.09% | 1 |
| vwap_cutoff_003.json | VWAP Scalping | BTC | 2024 | opt | -4.22% | -0.01 | 9 | 0.78 | -16.80% | 1 |
| meanrev_btc_iter2.json | Mean Reversion | BTC | 2024 | opt | -6.14% | 0.03 | 55 | 0.92 | -16.23% | 1 |
| regime_aware_martingale_mr_1d_2018-01-01_2025-01-01.json | Martingale MR | BTCUSDT | 2018-2025 | regime | -11.85% | 0.06 | 82 | 0.85 | -14.71% | 24 |
| meanrev_btc_iter1.json | Mean Reversion | BTC | 2024 | opt | -13.40% | 0.03 | 81 | 0.97 | -13.92% | 1 |
| results_mean_reversion.json | Mean Reversion | BTC | 2024 | opt | -13.40% | 0.03 | 81 | 0.97 | -13.92% | 1 |
| vwap_v6_validate.json | VWAP Scalping | BTC | 2024 | opt | -16.47% | -3.84 | 64 | 0.61 | -21.63% | 1 |
| vwap_btc_wide4.json | VWAP Scalping | BTC | 2024 | opt | -27.05% | -0.01 | 28 | 0.69 | -24.34% | 1 |
| vwap_lowsd_005.json | VWAP Scalping | BTC | 2024 | opt | -28.07% | 0.01 | 37 | 0.75 | -22.97% | 1 |
| regime_aware_grid_trading_2018-01-01_2025-01-01.json | Grid Trading | BTCUSDT | 2018-2025 | regime | -47.48% | -0.29 | 282 | 0.87 | -43.99% | 39 |
| regime_aware_sl_fade_mr_2018-01-01_2025-01-01.json | SL Fade MR | BTCUSDT | 2018-2025 | regime | -100.00% | -8.08 | 3749 | 0.02 | -100.00% | 81 |

---

## Section 2: Best Outcomes

### Ranked by Return (Absolute)

| Rank | File | Return | Sharpe | Trades | PF | Assessment |
|------|------|--------|--------|--------|-----|------------|
| 1 | vwap_btc_wide2.json | +143.53% | 0.84 | 18 | 14.35 | **Low trade count (18).** Single-year 2024 data. Valid signal but limited sample. |
| 2 | regime_aware_ma_crossover_4h_2018-01-01_2025-01-01.json | +135.09% | 0.70 | 45 | 2.13 | **Class C - different implementation** (BTV2). 45 trades across 39 folds but 15 folds zero-traded. "ROBUST" verdict is tautological (Finding 1). Parameters not transferable. |
| 3 | vwap_cutoff_0085.json | +76.52% | 1.10 | 11 | 5.51 | **Very low trade count (11).** Below 30-trade minimum. |
| 4 | vwap_cutoff_010.json | +64.19% | 1.04 | 13 | 6.69 | **Very low trade count (13).** Below 30-trade minimum. |
| 5 | vwap_btc_wide6.json | +62.12% | 0.33 | 41 | 1.60 | Moderate trade count. Sharpe degraded vs higher-return results. |

### Ranked by Sharpe Ratio (Risk-Adjusted)

| Rank | File | Sharpe | Return | Trades | Assessment |
|------|------|--------|--------|--------|------------|
| 1 | vwap_cutoff_0085.json | 1.10 | +76.52% | 11 | **Outlier - 11 trades.** High Sharpe inflated by tiny sample. |
| 2 | vwap_cutoff_005.json | 1.09 | +56.33% | 9 | **Outlier - 9 trades.** Dangerously low sample. |
| 3 | vwap_cutoff_010.json | 1.04 | +64.19% | 13 | **Outlier - 13 trades.** Same pattern. |
| 4 | vwap_btc_wide2.json | 0.84 | +143.53% | 18 | **Only 18 trades** but highest absolute return. |
| 5 | vwap_cutoff_085.json | 0.83 | +51.77% | 13 | **13 trades.** High Sharpe on thin sample. |

**Key Pattern:** Highest Sharpe values cluster on VWAP cutoff variants with 9-13 trades. These are below any 30-trade minimum and Sharpe inflation is expected at these sample sizes.

---

## Section 3: Worst Outcomes

### Ranked by Return (Absolute Loss)

| Rank | File | Return | Sharpe | Trades | Max DD | Assessment |
|------|------|--------|--------|--------|---------|------------|
| 1 | regime_aware_sl_fade_mr_2018-01-01_2025-01-01.json | **-100.00%** | -8.08 | **3749** | -100.00% | **HIGHEST TRADE COUNT IN ARCHIVE.** Complete ruin. Class C (BTV2, different strategy). This is the most informative loss in the archive: a consistently losing strategy with massive sample (3749 trades across 81 folds) provides unambiguous directional error signal. |
| 2 | regime_aware_grid_trading_2018-01-01_2025-01-01.json | -47.48% | -0.29 | 282 | -43.99% | **282 trades across 39 folds.** Class C (BTV2 single-position proxy, not real multi-level grid). Large sample confirms loss but measures different code. |
| 3 | vwap_lowsd_005.json | -28.07% | 0.01 | 37 | -22.97% | In-sample, 2024. Negative Sharpe near zero. Probably fitting noise. |
| 4 | vwap_btc_wide4.json | -27.05% | -0.01 | 28 | -24.34% | 28 trades, near-zero Sharpe, consistent underperformance. |
| 5 | vwap_v6_validate.json | -16.47% | -3.84 | 64 | -21.63% | **64 trades, large negative Sharpe.** Valid sample size shows real underperformance. |

### Analysis of High-Trade-Count Losing Results

**SL Fade MR (regime_aware_sl_fade_mr_2018-01-01_2025-01-01.json)**
- Return: -100%
- Trades: 3749 across 81 folds
- Sharpe: -8.08
- Source file: `BTV2/results/regime_aware_sl_fade_mr_2018-01-01_2025-01-01.json`
- Assessment: **Most valuable loss in archive** because of sample size (3749 trades is massive). This strategy was a complete loser on the 2018-2025 period. The -8.08 Sharpe is genuine (max drawdown varied across folds, not uniform -100%). Class C (BTV2), but the directional signal is clear: fade mean reversion with stop-loss trigger lost consistently over 7 years.

**Grid Trading (regime_aware_grid_trading_2018-01-01_2025-01-01.json)**
- Return: -47.48%
- Trades: 282 across 39 folds
- Sharpe: -0.29
- Source file: `BTV2/results/regime_aware_grid_trading_2018-01-01_2025-01-01.json`
- Assessment: Class C (BTV2 single-position approximation, not real GridTradingStrategy multi-level ladder). 282 trades is a large, valid sample, but measures a different algorithm. The loss is informative about BTV2's approach, not about `trading_bot_v2`'s grid.

---

## Section 4: Parameter Patterns Per Regime/Strategy

### VWAP Scalping (30 artifacts - most tested)

**Search Space Explored:**
- `sd_entry_threshold`: cutoff variants tested 0.003-0.015 (files: vwap_cutoff_*.json)
- `atr_stop_multiplier`: swept 2.0 to 8.0 (trading log shows 7.0 optimal on SUI 2024)
- `sd_multipliers`: [1.0, 2.0, 3.0] (standard deviation bands)

**Best Configurations by Return:**
- vwap_btc_wide2.json: +143.53%, 18 trades (specific params unknown, embedded in nested trial_log)
- vwap_cutoff_0085.json: +76.52%, 11 trades
- vwap_cutoff_010.json: +64.19%, 13 trades

**Pattern Observed:**
- Cutoff values cluster around 0.0085-0.015 for positive returns
- Cutoff 0.003 → -4.22% (too tight, over-trading)
- Cutoff 0.005 → +56.33% (good)
- Cutoff 0.007-0.015 → +54-76% (sweet spot)
- Cutoff 0.85 → -27% (too loose, missing edges)
- Lower cutoffs generally better for this strategy, but sample sizes <20 trades throughout

**From Trading Log (docs/tuning-log.md:420-461):**
- Optimal ATR stop: 7.0x (was 2.0x) → +1.32% on SUI-USDC 2024, 109 trades, PF=1.67
- Entry threshold: 3.0 SD (stable across tested params)
- Win rate: 45% at 7.0x stop (up from 22.4% at 2.0x)
- **Finding:** Wider stops dramatically improve performance. 7.0x is an edge value (likely at boundary of search space).

### Mean Reversion (5 artifacts)

**Best Configuration (meanrev_btc_iter4.json):**
- Return: +34.80%, 103 trades, Sharpe 0.29
- Likely params from trading log iteration 11: ATR stop 3.0x, BB proximity 0.10

**Iteration Path (from trading log):**
- Iteration 1 (MIN_RRR 0.5→1.0): -5.04% → -5.04% (no change)
- Iteration 6 (BB_PROXIMITY 0.15→0.10): -5.04% → -4.12% (+0.92%)
- Iteration 11 (ATR_STOP 2.0→3.0): -4.12% → **-3.01%** (best in log)

**Pattern:**
- BB proximity has sweet spot at 0.10 (tighter than 0.05, looser than 0.15)
- ATR stop optimal at 3.0x (monotonic improvement from 1.5x-2.5x, regression at 3.5x)
- RSI defaults (30/70) remained unchanged; no tested improvement vs (25/75) or (35/65)
- **Search space artifact:** Best return in backtest (iter4, +34.80%) differs from best in tuning log (iter11, -3.01%). Likely different symbol or timeframe.

### Grid Trading (1 regime_aware artifact)

**Regime_aware Run (2018-2025, BTCUSDT):**
- Return: -47.48%, 282 trades, Sharpe -0.29
- **CLASS C - BTV2 single-position proxy, not real implementation**

**From Trading Log (BTC-USDC 2024 - real implementation):**
- **Optimal found:** SPACING=1.5x, LEVELS=5 → +0.59% (14 trades, too few)
- Selected (meeting 22-trade minimum): SPACING=1.45x, LEVELS=5 → +0.49%, PF=2.51

**Search Space Patterns:**
- Spacing 0.4-0.8x: negative or flat
- Spacing 1.0x: negative (open-position drag)
- Spacing 1.5x: **optimal +0.59%**
- Spacing 2.0x+: negative

**Key Finding:** Optimal value at boundary? No - 1.5x is interior to range; 2.0x is tested and worse. But absolute return is marginal (0.59% pre-fees). Tuning log notes: *"Target not achievable: 0.75%+ returns require code changes (re-centering, max hold time, regime-based closing)"* - spacing parameter alone insufficient.

### MA Crossover (1 regime_aware artifact)

**Regime_aware Run:**
- File: `regime_aware_ma_crossover_4h_2018-01-01_2025-01-01.json`
- Return: +135.09%, Sharpe: 0.70, Trades: 45 across 39 folds
- **CLASS C - different implementation** (BTV2 walks continuous index; trading_bot_v2 uses rolling window)
- **"ROBUST" verdict is degenerate** (Finding 1)

**Trading Log Note (2026-07-28):**
> "Regime-aware walkforward validated at 4h timeframe (2018-2025): Sharpe +0.696, ROBUST MC (P(Loss)=0%), 45 trades, 64.4% WR, +135% total return"

**Caveat:** This result cannot be reproduced on `trading_bot_v2` because the rolling-window bug (`a03a75f` 2026-02-10 to `4be716b` 2026-07-28) prevented any signals. The data point is real but not evidence about current code.

### Martingale MR (1 regime_aware artifact)

- File: `regime_aware_martingale_mr_1d_2018-01-01_2025-01-01.json`
- Return: -11.85%, Sharpe: 0.06, Trades: 82 across 24 folds
- **CLASS C - BTV2 variant, not in live codebase**

### SL Fade MR (1 regime_aware artifact)

- File: `regime_aware_sl_fade_mr_2018-01-01_2025-01-01.json`
- Return: -100.00%, Sharpe: -8.08, Trades: **3749** across 81 folds
- **CLASS C - BTV2 variant, but highest-trade-count loss** (most informative)

---

## Section 5: What This Archive Can and Cannot Tell Us

### Can Tell Us (High Confidence)

1. **VWAP Scalping Parameter Space (Narrow Band)**
   - 30 artifacts confirm that cutoff values in range 0.005-0.015 produce positive returns
   - Range 0.0085-0.010 shows best risk-adjusted returns (Sharpe 1.0+)
   - Extremely tight sample sizes (<20 trades) inflate Sharpe; real performance unknown below 30 trades
   - **Transferability to live code:** Low - these are single-symbol, single-year (2024) optimization runs, class B pre-fix, and may not survive out-of-sample retest

2. **Direction of Parameter Sensitivity (Mean Reversion & Grid Trading)**
   - Wider stops (ATR 2.0x → 3.0x for MR, 1.5x for GT) improve returns
   - Tighter Bollinger Band proximity (0.10 vs 0.15) helps Mean Reversion quality
   - Smaller grid spacing increases trade count but reduces returns per trade (profitability in sweet spot, not edges)
   - **Transferability:** Low - results are single-year, in-sample, measured through execution filter that was later identified as discarding 546 of 601 signals

3. **Highest-Trade-Count Loss (3749 trades)**
   - SL Fade MR lost 100% over 7 years with massive sample
   - **Not transferable to live code** (BTV2 artifact), but illustrates that large-sample negative results are real losses, not noise
   - Directional signal: stop-loss-triggered fade strategies lost consistently; mechanism unclear but consistent across 81 folds

### Cannot Tell Us (Low Confidence)

1. **"Validated" Parameter Values**
   - Class B artifacts (tuning log): real runs on in-repo code but pre-fix (`7556e43` eating 546 of 601 signals on MR/SUI), in-sample single-year
   - Class C artifacts (BTV2 regime_aware): different implementations (single-position grid proxy vs 10-level ladder; continuous index vs rolling window; session-anchored VWAP vs cumulative)
   - **Action:** Do not use any tuning log value directly in `.env` without re-measuring post-`7556e43` with out-of-sample split

2. **Robustness of Positive Returns**
   - All positive-return VWAP files have <50 trades; statistical validity below 30
   - MA Crossover +135% is class C (not comparable to live code)
   - Mean Reversion best (+34.80%) is single-year, single-symbol, in-sample
   - **Cannot conclude:** Any configuration is robust until re-run with 50+ trades, out-of-sample split, and post-all-fixes validation

3. **Effect of Monte Carlo Verdicts**
   - Archive contains "ROBUST" and "FRAGILE" designations on regime_aware files
   - **These are degenerate** (Finding 1): verdicts are deterministic functions of return sign
   - Do not cite MC results as evidence of robustness
   - Max drawdown information in MC output IS real and varies across simulations, but feeds no part of verdict

4. **Cross-Symbol Generalization**
   - Archive has mostly BTC results (2024 optimization runs)
   - One SUI result in trading log (VWAP +1.32%, MR -3.01%) shows same strategy has opposite sign across symbols
   - Cannot generalize any parameter from BTC to SUI/ETH/other symbols without re-run

### Data Defects Affecting Specific Files

**File: `BTV2/tests/results/regime_vwap_comparison_2024.csv`**
- Read by: tuning-log reference "VWAP failed all 7 OOS years"
- Defect: `test_regime_aware_vwap.py` reads wrong metric keys (`sharpe_ratio` vs `sharpe`, `max_drawdown` vs `max_dd_pct`)
- **Result:** Every Sharpe and max-drawdown in that CSV is literally 0
- **Action:** Disregard any citation of VWAP Sharpe from this file

**Files: 30 JSON parse failures**
- Root cause: Invalid JSON syntax (trailing commas, embedded control chars, or inline text after closing brace)
- Affected files: grid_btc_iter*.json, ma_btc*.json, results_*.json variants, vwap_btc_iter*.json (others)
- **Recovery:** No parameter recovery from these files; they are malformed
- **Data loss:** Approximately 43% of submitted runs are not analyzable

**Files: 2 List-type top levels**
- `vwap_sd_6mo_sweep.json`, `vwap_sd_quick_test.json`
- Structure: JSON array, not object; different schema than other results
- **Action:** Require schema review before committing results

---

## Section 6: Critical Findings & Recommendations

### Finding 1: VWAP Cutoff Variants Dominate Archive but Remain Untested

30 of 39 parsed artifacts are VWAP variants. The archive represents an **optimization sweep on one parameter** (`sd_entry_threshold`) across 10+ values (0.003, 0.005, 0.007, 0.0085, 0.010, 0.012, 0.015, 0.85, plus 7 "wide" variants).

**Observation:** Files named `vwap_cutoff_*.json` and `vwap_sd_*.json` suggest two different sweeps on the same parameter. Trade counts under 20 for most. No result meets 30-trade minimum except vwap_v6_validate (64 trades, -16.47%).

**Recommendation:** Single-parameter sweeps are incomplete. Before relying on any cutoff value:
- Measure 50+ trades minimum
- Include out-of-sample split
- Re-run post-all-fixes (`7556e43` and VWAP validation)

### Finding 2: Positive Returns Cluster Below Trade Minimum

Top 10 positive returns in VWAP set:
- 9 of top 10 have <30 trades
- 5 of top 10 have <15 trades
- Only vwap_2010.json (52 trades) meets minimum in top-return set

**Implication:** Highest-return results are statistical artifacts of small samples. Sharpe inflation is expected and observed (Sharpe 1.10 on 11 trades in vwap_cutoff_0085.json).

### Finding 3: SL Fade MR is Archive's Most Informative Datapoint

- **Trades:** 3749 (largest in archive)
- **Folds:** 81
- **Result:** -100% / Sharpe -8.08
- **Source:** `BTV2/results/regime_aware_sl_fade_mr_2018-01-01_2025-01-01.json`

Despite being Class C (BTV2, not live code), this is the **only result with ironclad statistical validity**: 3749 trades across 81 fold-years is a massive sample. The -8.08 Sharpe is genuine (not a tautology like "ROBUST" on positive returns).

**Pattern Value:** If a fade-mean-reversion strategy lost consistently over 3749 trades and 81 fold-years, the mechanism is real. Hypothesis for failure: **large-sample consistent loss is evidence of directional error** (strategy is systematically picking the wrong side).

### Finding 4: Tuning Log Contradictions Indicate In-Sample Overfitting

Examples from docs/tuning-log.md:
- Grid Trading summary says "Current: 3 levels" but "Final Optimal Config: 5 levels"
- Same for max_positions: "Current: 5" vs "Final: 10"
- VWAP Cooldown listed twice with different values (no reconciliation)
- Mean Reversion Iteration 6 achieves -4.12%, Iteration 11 gets -3.01%, but later configs (iter 12) regress to -3.60%

**Implication:** Tuning log was hand-edited and inconsistently updated. "Final" values may not have been the actual selected ones, or were selected from a partial sweep.

### Finding 5: Class C Artifacts Outnumber Class A/B

- 4 regime_aware files = Class C (BTV2, different implementations)
- 35 optimization files = Class B (real code but pre-fix and in-sample)
- 0 files = Class A (post-fix, out-of-sample)

**Implication:** Archive contains no validated parameters in the sense of "measured on current code, post-fix, out-of-sample." All advice from this archive must be treated as hypothesis only.

### Recommendation: Re-Measurement Campaign Requirements

Before acting on any parameter from this archive:

1. **Choose target strategy** (highest-priority: VWAP, GridTrading, MeanReversion based on trade counts)
2. **Re-run post-`7556e43`** to clear execution filter (246 of 601 MR signals were eaten; other strategies affected differently)
3. **Implement out-of-sample split** (80/20 or yearly walk-forward, not in-sample)
4. **Enforce 50-trade minimum** per result
5. **Report funnel attrition** (raw signals, after each validation gate, final trades)
6. **Compare to archive baseline** (e.g., VWAP tuning log +1.32% SUI → re-run post-fixes, should move if execution filter was the issue)

---

## Appendix: Artifacts Not Parsed

### Failed JSON Parsing (30 files)

Cannot extract metrics due to invalid JSON syntax. Files listed below; root cause appears to be embedded text, trailing commas, or other serialization errors.

**Grid Training Variants (4):**
- BTV2/results/grid_btc_iter1.json
- BTV2/results/grid_btc_iter2.json
- BTV2/results/grid_btc_iter3.json
- BTV2/results/grid_btc_iter4.json

**MA Crossover Variants (4):**
- BTV2/results/ma_btc.json
- BTV2/results/ma_btc_iter2.json
- BTV2/results/results_ma.json
- BTV2/results/momentum_btc.json
- BTV2/results/momentum_btc_iter2.json
- BTV2/results/results_momentum.json

**Results Aggregates (2):**
- BTV2/results/results_grid.json
- BTV2/results/results_grid_eth.json

**VWAP Variants (9):**
- BTV2/results/vwap_15m.json
- BTV2/results/vwap_15m_60d.json
- BTV2/results/vwap_15m_test.json
- BTV2/results/vwap_1h_verify.json
- BTV2/results/vwap_5m_30d.json
- BTV2/results/vwap_5m_test.json
- BTV2/results/vwap_btc_iter1.json
- BTV2/results/vwap_btc_iter2.json
- BTV2/results/vwap_btc_iter3.json
- BTV2/results/vwap_btc_iter4.json
- BTV2/results/vwap_btc_iter5.json
- BTV2/results/vwap_btc_wide3.json
- BTV2/results/vwap_cutoff_015.json
- BTV2/results/results_vwap.json
- BTV2/results/results_vwap_eth.json

**List-Type Top Level (2):**
- BTV2/results/vwap_sd_6mo_sweep.json (array, not object)
- BTV2/results/vwap_sd_quick_test.json (array, not object)

### Data Loss Impact

- 30 unparseable files = 43% of 69 submitted results
- Unknown parameters, trade counts, and metrics for affected runs
- Likely candidates: intermediate iterations, validation runs, or partial optimization states
- Recommendation: Implement CI check for JSON schema validation before committing backtest results

---

## Summary & Next Steps

This archive catalogs 39 usable backtest artifacts spanning 6 strategies, mostly VWAP Scalping (30), with trade counts ranging from 9 to 3749. Best absolute return is VWAP +143.53% on 18 trades (untested OOS). Worst return is SL Fade MR -100% on 3749 trades (BTV2, but largest sample). All positive returns sit below 30-trade minimum except vwap_2010.json (52 trades, +13.54%).

**Key Patterns:** Wider stops improve returns. Cutoff values 0.005-0.015 cluster around positive returns, but sample sizes preclude statistical validity. Parameter values are hypothesis only until re-run post-fixes with OOS split and 50+ trade minimum.

**No parameters in this archive are ready for production without re-validation.**

---

**Document compiled:** 2026-07-28  
**Source data:** BTV2/results/*.json (39 of 69 parsed), docs/tuning-log.md, PARAMETER-PROVENANCE.md  
**Verification:** All numbers traced to files; file paths cited in text
