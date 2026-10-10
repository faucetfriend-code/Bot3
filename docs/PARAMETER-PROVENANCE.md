# Parameter provenance

Answers one question, per parameter: **where did this number come from, and did
that source measure the code we actually run?**

Written 2026-07-28 in response to `docs/FOLLOW-UPS.md` items 3 and 5, after two
"validated" claims collapsed under inspection (VWAP, MA crossover). This
document is the systematic version of those two spot-checks.

The short version: **almost nothing in the current configuration is entitled to
the word "validated".** One number is traceable to a run on this codebase with
an out-of-sample split. The rest is single-year in-sample tuning on a pre-fix
engine, artifacts from a different codebase, or convention.

---

## Contents

- [How to read the tables](#how-to-read-the-tables)
- [Finding 1: the BTV2 Monte Carlo verdict carries no information](#finding-1-the-btv2-monte-carlo-verdict-carries-no-information)
- [Finding 2: the evidence sources, ranked](#finding-2-the-evidence-sources-ranked)
- [Per-strategy provenance](#per-strategy-provenance)
- [Finding 3: the grid trading contradiction](#finding-3-the-grid-trading-contradiction)
- [What could not be verified](#what-could-not-be-verified)
- [Code defects found while auditing](#code-defects-found-while-auditing)
- [Recommended actions](#recommended-actions)

---

## How to read the tables

### Provenance classes

| Class | Meaning | Trust |
|---|---|---|
| **A** | Measured on this codebase, post-fix, with an out-of-sample split | Use it |
| **B** | Measured on this codebase, but pre-fix and/or in-sample single-window | Directional only. Re-measure before relying on it |
| **C** | From a BTV2 artifact - a *different implementation* | Not evidence about this code. Discard |
| **D** | Hand-picked / convention / library default, no measurement on record | Folklore. Free to change |
| **E** | Unknown - value exists, no record found anywhere | Folklore, and nobody knows why |

Class B is the biggest bucket and the most dangerous, because it looks like
evidence. Everything in `docs/tuning-log.md` is class B: the runs are real, they
used the in-repo engine, and they measured this code - but they were made on
2026-04-28 or earlier, which puts them behind every fix landed on 2026-07-27/28:

| Commit | Date | What it changed under those results |
|---|---|---|
| `431aa2d` | 07-27 | Canonical candle store; the data underneath every earlier run was different |
| `b3722ba` | 07-27 | Data-coverage guard - short stores previously truncated windows silently |
| `fcd44d0` | 07-27 | Chunk windows clamped to 1m coverage |
| `cf20c8b` | 07-28 | VWAP `sd_entry_threshold` above the metric ceiling silenced the strategy |
| `2cc9102` | 07-28 | Momentum RRR gate discarded signals silently |
| `4be716b` | 07-28 | MA crossover crossover-bar keyed on rolling-window index - zero signals since `a03a75f` (2026-02-10) |
| `7556e43` | 07-28 | `_execute_signal` discarded signals *after* all eight validity flags passed. On MeanReversion/SUI 2024-06..09 it ate **546 of 601** raw signals |

`7556e43` is the one that voids the widest area. It sat at the end of the
pipeline, downstream of every gate anyone was tuning, and nobody had looked at
it. Any measured effect of a parameter change from before that date was observed
through a filter that was discarding most of the population.

### Value columns

`.env` is gitignored and **absent from this worktree**, so the live values could
not be read. "Code default" is what the process resolves to with no `.env`
present - traced through `StrategyManager.__init__`
(`trading_bot_v2/strategy_manager.py:284-603`), which is the only real
instantiation site and which reads `os.getenv` itself for most strategies.
"On record" is the value `docs/tuning-log.md` says was selected. Where the two
differ, the tuned value lives only in `.env` and is unverifiable from here.

Note that `trading_bot_v2/config.py` holds **no strategy tunables at all** -
every strategy parameter is read by `os.getenv` in the manager or the strategy
constructor.

---

## Finding 1: the BTV2 Monte Carlo verdict carries no information

`docs/FOLLOW-UPS.md` item 3 claims `monte_carlo_validate` is near-tautological.
**Confirmed, and it is worse than "near".**

The function is `BTV2/strategies.py:2007-2106`. Its loop:

```python
for _ in range(n_sims):
    shuffled = list(trades)
    rng.shuffle(shuffled)
    eq = starting_equity
    for r in shuffled:
        eq *= (1.0 + r)
    final_returns.append((eq / starting_equity - 1.0) * 100.0)
```

`eq` is `prod(1 + r)` over the trade list. Multiplication is commutative, so
**every one of the 1000 permutations terminates at the identical equity**.
`final_returns` is a constant list.

Run against four synthetic trade sets, 1000 sims each:

| Case | Distinct final returns / 1000 | Spread | p5 = p50 = p95 | P(Loss) | Verdict |
|---|---|---|---|---|---|
| 45 trades, net positive | **1** | 2.2e-13 | +3.527% | 0.0% | ROBUST |
| 45 trades, net negative | **1** | 1.2e-13 | -27.939% | 100.0% | FRAGILE |
| 1 huge win + 40 small losses | **1** | 6.7e-14 | +7.035% | 0.0% | ROBUST |
| 200 pure coin flips, zero skill | **1** | 5.6e-13 | +32.318% | 0.0% | **ROBUST** |

(The residual e-13 spread is floating-point summation order, not signal.)

Three consequences:

1. **`verdict` is a restatement of the sign of the backtest return.** ROBUST iff
   the strategy made money, FRAGILE iff it lost money. The last row is the
   demonstration: a zero-edge coin flip grades ROBUST.
2. **The `MARGINAL` branch is unreachable.** It needs `10 <= prob_loss < 25`;
   `prob_loss` can only ever be exactly 0.0 or exactly 100.0.
3. **`overfitting_flag` is structurally always `False`.** It requires
   `p5 < -5.0 AND spread_ratio > 4.0`. When `p5 < 0`, `p95 == p5`, so
   `spread_ratio = p5/|p5| = -1.0`, never `> 4`. When `p5 >= 0` the ratio is
   `inf` but the first condition fails. It is `False` in all four committed
   artifacts, as predicted.

The one output that *is* order-dependent is max drawdown - `worst5_maxdd_pct`
and `median_maxdd_pct` vary genuinely (997 distinct values out of 1000 in the
first case above, range -39.8% to -10.6%). Those two numbers are real. **They
feed no part of the verdict.**

So: every "MC=ROBUST" and "P(Loss)=0%" citation in `.env`, in
`BTV2/CLI_AGENT_GUIDE.md`, and in the four `regime_aware_*.json` files should be
read as "the backtest total return was positive". Nothing more. A correct
implementation would resample trades *with replacement* (bootstrap) or permute a
*returns series* rather than a closed trade set.

---

## Finding 2: the evidence sources, ranked

Everything the current configuration cites, and what each is actually worth.

### `BTV2/results/regime_aware_*.json` - class C

Four files. All BTCUSDT, all 2018-01-01 to 2025-01-01, all produced by
`BTV2/regime_aware_validation.py`.

| Artifact | Sharpe | Return | Trades | MC verdict |
|---|---|---|---|---|
| `regime_aware_ma_crossover_4h_2018-01-01_2025-01-01.json` | +0.696 | +135.1% | 45 / 39 folds | ROBUST |
| `regime_aware_grid_trading_2018-01-01_2025-01-01.json` | -0.294 | -47.5% | 282 / 39 folds | FRAGILE |
| `regime_aware_martingale_mr_1d_2018-01-01_2025-01-01.json` | +0.057 | -11.9% | 82 / 24 folds | FRAGILE |
| `regime_aware_sl_fade_mr_2018-01-01_2025-01-01.json` | -8.079 | -100.0% | 3749 / 81 folds | FRAGILE |

These artifacts are **not fabricated** - the batch contains losers, including a
total-ruin case, which is what a real run looks like. They are simply about
different code. Beyond that:

- **The MA crossover trade count is not a sample.** 45 trades over 7 years, with
  **15 folds producing zero trades**, 24 folds producing 1-4, and the whole of
  2018 producing none. The +135% headline rides on a handful of single-trade
  folds (fold 34: 1 trade, Sharpe +3.04). The `mc_bear` "ROBUST" verdict is
  computed on **6 trades**.
- **Zero-trade folds are recorded as `sharpe 0.0 / return_pct 0.0`** and then
  averaged into `year_summary.avg_fold_return_pct` as if they were neutral
  observations. They are absences, not results.
- **`baseline_sharpe = -0.12` is a hardcoded literal**
  (`BTV2/regime_aware_validation.py:422`), not measured. Every
  `improvement_vs_baseline` figure is an offset from a magic number.
- **Sharpe is computed on bar returns, not trade returns**, annualised by
  `INTERVAL_BARS_PER_YEAR` (4h -> 2190). A fold holding 0-2 positions is mostly
  flat bars, so the denominator collapses and |Sharpe| inflates in both
  directions. That is why single-trade folds report values like +3.325 and -5.356.
- **The runs are not reproducible from their artifacts.** MA Crossover and
  Martingale MR were run with `--train-months` / `--test-months` overrides
  (deducible only from the fold counts), and neither the filename nor the JSON
  records them.
- **`mc_neutral` is never computed**, yet the neutral bucket holds the majority
  of trades in three of the four files.

**Only four of the eight registry strategies were ever run this way.** There is
**no `regime_aware_vwap*` artifact anywhere in the repo** - the VWAP "failed all
7 OOS years" claim does not trace to one of these files at all.

### `docs/tuning-log.md` - class B

Authored `6dc058b` (2026-04-28), amended once (`09d044a`, 2026-07-28, MA
crossover only). Real runs on the in-repo backtest engine
(`trading_bot_v2/backtesting/`, created `05eb360` 2026-02-28). Method throughout:

- **One year** (2024-01-01 to 2024-12-31), **one symbol per strategy**
- **No out-of-sample split.** Parameters were selected on the same window they
  were scored on
- Sweeps of 6-24 configs picking the argmax; several "selected" configs have
  13-22 trades, below the log's own stated 30-trade minimum
- Pre-`7556e43`, so every measured delta was observed through an undiagnosed
  execution-stage filter

The tuning log is the **best** evidence in the project and it is still class B.
Where it disagrees with a BTV2 artifact, prefer the tuning log - it at least
measured the right code.

### `trading_bot_v2/optimization/optimization_studies.db` - does not exist here

Gitignored (`.gitignore:17`) and absent. **No Optuna study contributed to any
current parameter**, or if one did, the record is not in the repo.

`BTV2/optimization_studies.db` *is* committed and is an abandoned stub: **one**
study (`VWAP_Scalping_BTC-USD_normal`), **one** completed trial (objective
**-5.26**), and a second trial still marked `RUNNING` from **2026-04-14**. It
contributed nothing.

### `validation_runs` / `trial_registry` in `trading_bot.db` - empty

Both tables are defined in code (`trading_bot_v2/database.py`,
`validation/gate.py`, `validation/runner.py`) but **neither exists in any
committed database**. The four `.db` files in the tree contain only
`signals` (1612 rows), `balance_history` (31), `positions`, `grid_states`,
`account_profiles`. `trades` is **0 rows**.

Consequence: the promotion gate's deflated-Sharpe path has no trial counts to
work from, and there is no persisted validation history anywhere in the repo. The
2026-07-28 campaign results cited in `docs/FOLLOW-UPS.md` (grid SUI PF 1.60 /
PSR 0.98, BTC PF 1.31) are **not committed as artifacts** - they exist only as
prose in that file.

### `.env` comments - class C or E, never a source

`.env` is not in the repo, but its comment text is recoverable, and it traces to
one place. `BTV2/CLI_AGENT_GUIDE.md:1544-1556` carries a "Strategy Status
Summary (post v9 validation - final initial pass 2026-04-30)" table whose rows
are the `.env` comments nearly verbatim:

> `| **MA Crossover (4h)** | **YES** - Sharpe +0.696, +135.1%, MC=ROBUST, 45 trades | Should switch | **PASS** ... |`
>
> `| Grid Trading | **NO** - Sharpe -0.294, MC=FRAGILE, regime-aware run done (2026-04-30) | In use | **DISABLE** - ENABLE_GRID_TRADING=false ... |`

So the `.env` comments are a transcription of a BTV2 recommendation table. They
are not an independent record and should never be cited as one.

---

## Per-strategy provenance

### MeanReversion

Constructor `strategies/mean_reversion.py:56-136`. Instantiated with **no
arguments** (`strategy_manager.py:288`) - fully env-driven.

| Parameter | Code default | On record | Class | Source / note |
|---|---|---|---|---|
| `atr_stop_multiplier` | 2.0 | **3.0** | **B** | tuning-log iteration 11: -5.04% -> -3.01% on SUI 2024. Best single result in the log, and still a **loss**. In-sample, one year, one symbol |
| `bb_proximity` | 0.20 | **0.10** | **B** | tuning-log iteration 6, same run. Selected over 0.05 and 0.15 by ~0.05% of return |
| `min_rrr` | 0.5 | 1.0 | **B** | tuning-log iteration 1 recorded "no change"; raised anyway |
| `rsi_oversold` / `rsi_overbought` | 35.0 / 65.0 | 30 / 70 | **B** | Both alternatives tested (25/75 and 35/65) were worse. 30/70 is the untested incumbent that won by default |
| `min_confidence` | 0.45 | 0.50 | **B** | tuning-log iteration 8: "no change - confidence not bottleneck" |
| `cooldown_minutes` | 0 | 0 | **B** | 30 min tested and worse |
| `rsi_period` 14, `bb_period` 20, `bb_std_dev` 2.0, `sma_period` 20, `atr_period` 14 | as listed | - | **D** | Textbook defaults. Never swept |

**Caveat that dominates this row:** MeanReversion/SUI is the strategy where
`7556e43` found `_execute_signal` eating 546 of 601 raw signals. Every one of the
12 tuning iterations above was scored through that filter. Treat the whole
section as void until re-run.

### MACrossover

Constructor `strategies/ma_crossover.py:150-242`. Instantiated with no arguments
(`strategy_manager.py:292`).

| Parameter | Code default | On record | Class | Source / note |
|---|---|---|---|---|
| `fast_ma_period` / `slow_ma_period` | 20 / 50 | **10 / 30** | **C** | 10/30 is the BTV2 `bull_optimised_params` from `regime_aware_ma_crossover_4h_*.json`. Different implementation. The file header (`ma_crossover.py:24-31`) already says so |
| `pullback_range` | (0.02, 0.04) | 0.0-0.10 | **C** | BTV2 reports `pullback_max: 0.1` in **all 39 folds** - which is the *low edge* of `MA_GRID`, i.e. the optimizer pinned to the boundary in every fold. That is a search-space artifact, not an optimum |
| `volume_confirmation_threshold` | 1.2 | 1.2 | **E -> now A-adjacent** | No provenance found for 1.2. The 2026-07-28 funnel measured its effect on this codebase: on SUI 2024-01..04 it killed **183 of 183** raw signals (100% at `validity:volume_confirmation`). That is a measurement of the *binding constraint*, not a validation of the value |
| `min_entry_bars` / `max_entry_bars` | 1 / 5 | 1 / 5 | **D** | `DEFAULT_MIN_ENTRY_BARS` / `DEFAULT_MAX_ENTRY_BARS`, `ma_crossover.py:54-55`. Chosen to mirror BTV2's `if 0 < (i - last_golden_bar) <= 5` |
| `atr_stop_multiplier` 2.5, `min_confidence` 0.50, MACD 12/26/9, `atr_period` 14 | as listed | - | **D** | Never swept |

The `.env` claim ("Sharpe +0.696, ROBUST MC, 45 trades") is **class C on two
counts**: different implementation, *and* the ROBUST half is the degenerate MC
verdict from Finding 1. `trading_bot_v2/strategies/ma_crossover.py` carried the
rolling-window bug from `a03a75f` (2026-02-10) to `4be716b` (2026-07-28), so no
in-repo run could ever have produced those 45 trades.

### GridTrading

Constructor `strategies/grid_trading.py:39-113`. Manager reads env at
`strategy_manager.py:301-316` and passes all values.

| Parameter | Code default | On record | Class | Source / note |
|---|---|---|---|---|
| `grid_spacing_atr_multiplier` | 0.4 (mgr) / 0.65 (ctor) | **1.45-1.5** | **B** | tuning-log BTC 2024, 15 configs. Finding: 0.4-0.8 negative or flat, 1.0 negative, **1.5 optimal**, 2.0+ negative. Single year, single symbol, in-sample |
| `grid_levels` | 8 (mgr) / 10 (ctor) | **3 or 5** | **B, contradictory** | tuning-log's own summary table says "Current: 3"; its "Final Optimal Config" says 5. The log contradicts itself. Runs 4, 13, 14, 15 all returned **identical** +0.59% for levels 3/4/5/7, which says levels barely matter at spacing 1.5 |
| `max_positions_per_symbol` | 10 | 5 or 10 | **B, contradictory** | Summary says "Current: 5"; Final Config says 10. Run 5 (5->3) was worse |
| `min_confidence` | 0.45 | 0.55 | **E** | Summary table lists a change 0.45 -> 0.55 with **no supporting run** anywhere in the log |
| `adx_regime_threshold` | 20.0 | 20.0 | **B** | Run 6 tested 25, "no improvement" |
| `emergency_stop_loss_pct` 0.05, `min_spacing_pct` 0.003, `max_spacing_pct` 0.06, `atr_period` 14, `adx_period` 14 | as listed | - | **D** | Safety rails, never swept |

The `.env` "DISABLED / Sharpe -0.294 / do not re-enable" comment is **class C and
void** - see Finding 3.

### VWAPScalping

Constructor `strategies/vwap_scalping.py:179-286`.

| Parameter | Code default | On record | Class | Source / note |
|---|---|---|---|---|
| `sd_entry_threshold` | **2.0** | previously 4.037 | **D (current) / C (previous)** | `DEFAULT_SD_ENTRY_THRESHOLD`, `vwap_scalping.py:83`, explicitly labelled "Interim default, pending re-optimization". The old 4.037 came from the BTV2 harness, which uses a **daily session-anchored** VWAP whose sigma resets at 00:00 UTC. This strategy uses a **rolling cumulative** VWAP with a far tighter deviation distribution (mean ~1.0 SD, empirical max ~4.0 over six months). 4.037 was unreachable. Now clamped to [1.0, 3.0] by `validate_sd_entry_threshold()`. **Swept 2026-07-29** over 48 contiguous 2-month BTC windows: PF rises monotonically with the threshold (1.5 -> 0.69 / 2.0 -> 0.74 / 2.5 -> 0.82 / 3.0 -> 0.86) while trades fall 8907 -> 448. Its own curve extrapolates to break-even near 4.2 SD, above the p99.9 of the deviation distribution (3.142) - i.e. the threshold that would make VWAP profitable is the threshold that silences it. `docs/VWAP-LEVERS.md` |
| `atr_stop_multiplier` | 1.5 | **7.0** | **B** | tuning-log SUI 2024, 9-point sweep 2.0 -> 8.0. Monotonic improvement to 7.0 (+1.32%, PF 1.67, 109 trades) then regression at 8.0. Genuinely the strongest signal in the tuning log - and a 7x ATR stop is extreme enough to deserve suspicion that it is fitting one year of SUI |
| `min_confidence` | 0.62 | 0.68 | **E** | Listed as changed 0.62 -> 0.68 with no supporting run |
| `cooldown_minutes` | 8 | 20 | **E** | Listed as changed 8 -> 20 with no supporting run |
| `sd_multipliers` | [1.0, 2.0, 3.0] | - | **D** | Band levels, never swept |
| `rsi_period` 14, `rsi_oversold` 35, `rsi_overbought` 65 | as listed | - | **D, inert** | Accepted and stored; **do not gate entries** (RSI only annotates notes). Not passed by the manager |
| 11 `VWAP_*` env vars in `UNSUPPORTED_ENV_VARS` | - | - | **C, dead** | `vwap_scalping.py:88-95`. Leftovers from the BTV2 harness. Setting them has no effect; construction now warns |

The `.env` "walkforward validation failed on all 7 OOS years, do not re-enable"
claim is **void twice over**: those runs used the unreachable 4.037 threshold, so
they measured a strategy that could not trade - *and* no
`regime_aware_vwap*.json` artifact exists in the repo to substantiate them. The
nearest thing is `BTV2/tests/results/regime_vwap_comparison_2024.csv`, produced
by a hand-rolled script (`BTV2/tests/test_regime_aware_vwap.py`) with its own
inline backtest, no walk-forward, and a metric-key bug (`compute_results()` reads
`sharpe_ratio`/`max_drawdown`, `compute_metrics()` returns `sharpe`/`max_dd_pct`)
that makes **every Sharpe and every max-drawdown in that file literally `0`**.

### LiquidationCapture

Constructor `strategies/liquidation_capture.py:58-95`. Manager reads env at
`strategy_manager.py:336-351`.

| Parameter | Ctor default | Manager env default | On record | Class |
|---|---|---|---|---|
| `price_move_threshold` | 0.025 | **0.03** | 0.026 | **B** |
| `volume_spike_multiplier` | 2.5 | **3.0** | 2.6 | **B** |
| `rsi_oversold_threshold` | 20.0 | **15.0** | 18 | **B** |
| `rsi_overbought_threshold` | 80.0 | **85.0** | 82 | **B** |
| `min_consecutive_moves` | 4 | **5** | 4 | **B** |
| `min_wick_ratio` | 1.5 | **2.0** | 1.7 | **B** |
| `max_per_session` | 2 | **1** | - | **E** |
| `min_hours_between_trades` | 2 | **4** | - | **E** |
| `rrr_target` 3.0, `rsi_period` 14 | 3.0 / 14 | same | - | **D** |

Three different value sets are in play for the same six parameters, and **all
three disagree**. The tuning-log "Final ETH Config" (0.026 / 2.6 / 18 / 82 / 4 /
1.7) came from a 6-config sweep on ETH-USDC 2024 that selected on **20 trades**,
below the log's own 30-trade floor, for **+0.07%** return. The intermediate
"Mid settings" it beat had 13 trades. At that sample size the ranking is noise.

Separately: the manager's env defaults are **stricter** than the constructor's on
every one of the first six rows, so a code-reading of `liquidation_capture.py`
gives the wrong picture of what production runs. See defect 2 below.

`BTV2/CLI_AGENT_GUIDE.md` additionally claims "LONG-only Sharpe +0.726, ROBUST" -
**class C**, and the ROBUST is the degenerate verdict.

### MomentumScalping

Constructor `strategies/momentum_scalping.py:177-240`.

| Parameter | Code default | On record | Class | Source / note |
|---|---|---|---|---|
| `atr_stop_mult` / `atr_target_mult` | 1.5 / 2.5 | 2.0 / 3.0 | **B** | tuning-log 24-config sweep on ETH 2024. **No configuration crossed PF 1.0.** Best was 0.71 |
| `min_atr_pct` | 0.0 | 0.0 | **B** | 9-point sweep, no threshold reached PF 1.0. Retained disabled |
| `rsi_lower` / `rsi_upper` | 35.0 / 65.0 | 25 / 75 | **E** | tuning-log: "Pre-session tuning (exact session unknown) ... Effect not measured separately" |
| `min_confidence` | **0.60** (mgr) / 0.55 (ctor) | 0.65 | **E** | Same unmeasured pre-session block |
| `cooldown_minutes` | 5 | 20 | **E** | Same |
| `volume_threshold` | 1.2 | 1.5 | **E** | Same |
| `ema_fast` / `ema_slow` | 9 / 21 | 9 / 21 | **D** | Convention |
| `rsi_period` 14, MACD 12/26/9, `atr_period` 14, `min_rrr` 1.5 | as listed | - | **D** | Never swept |

The tuning log's own conclusion is that this is a **structural exit-logic
problem, not a parameter problem**: at `min_atr_pct=0.2%` the win rate rose to
57.7% while PF stayed at 0.44, meaning average win = 0.32x average loss. The
parameter values above are therefore all irrelevant until the exits change - and
four of them are class E regardless.

### OrderBookImbalance

Constructor `strategies/orderbook_imbalance.py:60-89`. **Every one of its 13
parameters is class D.** No sweep, no artifact, no tuning-log section beyond a
"what the strategy does" stub. It is also recorded as producing **0 trades**.

`levels` 10, `imbalance_long_threshold` 0.62, `imbalance_short_threshold` 0.38,
`strong_imbalance_threshold` 0.72, `min_order_density` 5, `spoof_detection` True,
`spoof_size_ratio` 5.0, `atr_period` 14, `atr_stop_mult` 0.75,
`atr_target_mult` 1.5, `min_confidence` 0.55, `cooldown_seconds` 30,
`update_interval_ms` 500.

### FundingArb

> **Removed 2026-10-10.** The strategy was deleted as never tested or used; none of the
> parameters below exist any more and `ENABLE_FUNDING_ARB` / `FUNDING_ARB_*` are ignored.
> The section is kept as the provenance record that motivated the removal.

Constructor `strategies/funding_arb.py:32-57`. **All class D**, same as above -
no sweep, no artifact, 0 trades on record. Disabled by default
(`ENABLE_FUNDING_ARB` default False).

`min_funding_rate` 0.0001, `max_allocation_pct` 0.20, `rebalance_threshold` 0.02,
`lookback_hours` 8, `min_confidence` 0.70.

One genuine improvement here: `funding_interval_hours` is **not** a magic number -
it comes from `get_exchange_capabilities().funding_interval_hours`
(`strategy_manager.py:420`), giving 1 for Pacifica and 8 for Blofin. That is the
pattern the rest of the config should follow.

**Update 2026-07-29 - `min_funding_rate` is mis-scaled against that very interval.**
Now that real funding history is ingested, the number can be checked instead of
guessed. `min_funding_rate` is compared against a rate PER SETTLEMENT INTERVAL. On
Pacifica that interval is one hour, and the pro-rata mapping of Binance's measured
history puts the mean hourly BTC rate at **0.0000133** (mean 8h rate 0.00010651 / 8).
So a 0.0001 hourly threshold demands an 87.6% annualized carry - roughly the top 1%
of all settlements since 2019. Measured consequence: over six 2-month windows spread
across eight years, `funding_arb` fired in exactly one of them (March 2020) for 9
closed trades.

0.0001 is almost certainly an **8-hour** number - it is within 6% of the mean Binance
8h rate - written into a strategy whose venue settles hourly. Same root cause as
`BACKTEST_FUNDING_HOURLY_PCT=0.0001` in the simulator, which charged the 8h mean once
an hour and so modelled an 87.6%/yr carry (see BACKTESTING_GUIDE, "Funding").

Class D remains correct: the value has no artifact behind it. Rescaling it is a
defensible fix, but it must be done as a stated hypothesis and re-validated, not
tuned until the verdict improves.

### SessionRangeBreakout (ORB)

Constructor `strategies/session_range_breakout.py:45-105`. Disabled
(`ENABLE_SESSION_RANGE_BREAKOUT` default False).

All 11 env-exposed parameters are **class B**: the strategy landed `1ee0b5f`
(2026-07-20) and **failed its backtest gate**. `docs/orb-regime-deep-dive.md:4`
records PF 0.34-0.74 across BTC/ETH/SUI against a gate of `GATE_MIN_PF` 1.3,
which is why it ships disabled. The values above are the ones that failed - and
that document's own conclusion is that no parameter setting will fix it, because
crypto has no market open for an opening-range breakout to exploit. `atr_period`
and `min_confidence` are class D *and* unreachable - see defect 3.

### CalendarFlow

Constructor `strategies/calendar_flow.py:54-105`. Disabled
(`ENABLE_CALENDAR_FLOW` default False).

**Class A - the only one in the document, and it is a negative result.** Commit
`910c26c` (2026-07-21) is titled "turn-of-month hypothesis TESTED AND REJECTED".
The parameters (`long_entry_day` -2, `long_exit_day` 3, etc.) encode a hypothesis
that was measured on this codebase and refused. That is exactly the provenance
every other row should have.

### TrendFollowing

`ENABLE_TREND_FOLLOWING` exists (default False) but **no class exists**.
`strategy_manager.py:295-297` logs a warning and moves on.

---

## Finding 3: the grid trading contradiction

`docs/FOLLOW-UPS.md` item 5: `.env` says grid is disabled with "Sharpe -0.294,
P(Loss)=100%, FRAGILE ... Do not re-enable", while `ENABLE_GRID_TRADING=true`
and grid was the best performer in the 2026-07-28 campaign.

**The verdict is void. Three independent reasons, any one of which is sufficient.**

Source artifact confirmed:
`BTV2/results/regime_aware_grid_trading_2018-01-01_2025-01-01.json` (Sharpe
-0.294065002181771, return -47.480%, 282 trades, 39 folds, `mc_overall` P(Loss)
100.0, verdict FRAGILE). Reproduced by
`python BTV2/regime_aware_validation.py --strategy "Grid Trading" --start 2018-01-01 --end 2025-01-01`.

### 1. It measured a different strategy - and not a grid at all

`BTV2/strategies.py:1252-1334::run_grid_trading` says so in its own docstring:

> "Real grids are multi-position; this single-position approximation captures the
> core edge (buy near support, sell near resistance in low-ADX periods)."

It maintains **one boolean `in_pos` flag**. There is one buy line
(`lo20 + spacing_mult*ATR`), one sell line (`hi20 - spacing_mult*ATR`), a take
profit at the 20-bar midpoint, and a hardcoded 2x ATR stop. No ladder, no
inventory, no averaging down, no re-arm.

`GridTradingStrategy` is a multi-level ATR-spaced ladder holding **up to 10
concurrent positions per symbol**, with an emergency stop, dynamic spacing and an
ADX regime gate. The proxy omits the mechanism that makes real grids *lose* money
(inventory accumulation into a trend) **and** the mechanism that makes them
*make* money (many small round trips). It is not a conservative approximation in
either direction - it is a different bet.

### 2. The search space never contained the value that works

BTV2 optimised `spacing_mult` over `{0.30, 0.50, 0.65, 0.80}`
(`GT_GRID`, `BTV2/strategies.py:1773-1775`) - 12 combinations with
`adx_threshold`, exhaustive, maximising Sharpe.

The tuning log's sweep of the **real** implementation on BTC 2024 found:

> 0.4-0.8: negative or flat / 1.0: negative / **1.5: optimal +0.59%** / 2.0+: negative

BTV2's grid tops out at 0.80. Its entire search space sits inside the band the
real strategy is known to lose money in. Worse, `spacing_mult` **does not mean
the same thing** in the two implementations - in BTV2 it is an offset from the
range extreme, in `GridTradingStrategy` it is inter-level spacing on a ladder.
The numbers are not comparable units, let alone comparable optima.

### 3. "FRAGILE" adds nothing to "-47.5%"

Per Finding 1, `verdict` is a deterministic function of the sign of the return
and `P(Loss)=100%` means "the return was negative". The MC block contributes no
robustness information. The citation format - Sharpe, then P(Loss), then a
capitalised verdict - implies three independent checks. There is one.

Supporting rot in the same artifact: two folds (9 and 10) produced **zero trades**
and were recorded as `sharpe 0.0 / return 0.0`, then averaged into the year
summaries as neutral. Twelve more folds have 1-4 trades, and the three
highest-Sharpe folds in the whole run are **single-trade folds** (+2.467, +2.187,
+2.509 on one trade each).

### What this does and does not license

**Strike the `.env` comment.** It is a transcription of
`BTV2/CLI_AGENT_GUIDE.md:1552`, which is a recommendation about BTV2's range-fade
proxy. `ENABLE_GRID_TRADING=true` is not the half that is wrong.

**But grid is not thereby validated.** Voiding bad evidence produces *absence* of
evidence, not evidence of absence. The in-repo record for `GridTradingStrategy`
is class B and mixed:

| Symbol | Return | PF | Trades | Source |
|---|---|---|---|---|
| BTC-USDC 2024 | +0.49% | 2.51 | 22 | tuning-log, in-sample |
| SUI-USDC 2024 | -0.41% | 0.96 | 160 | tuning-log, using BTC-tuned params |

That is one year, in-sample, pre-`7556e43`, and it disagrees with itself across
symbols. The tuning log also records three structural limitations not addressable
by parameters (no re-centering, no max hold time, no regime-based closing) that
would each change a multi-year result.

The imminent campaign run is the right way to settle this. **The provenance
finding is that the old verdict should not be an input to it.**

---

## What could not be verified

Stated explicitly, because the gaps matter:

1. **All live `.env` values.** `.env` is gitignored and absent. Every "code
   default" above is what the process resolves to with no `.env` present. Where
   `docs/tuning-log.md` records a different selected value, that value is
   reported as "on record" and is **unconfirmed**.
2. **The exact `.env` comment text.** Recovered from
   `BTV2/CLI_AGENT_GUIDE.md:1544-1556` and from the strategy file headers
   (`ma_crossover.py:24-31`, `vwap_scalping.py:40-46`), which quote it. Not read
   from `.env` itself.
3. **The 2026-07-28 campaign numbers** (grid SUI PF 1.60 / PSR 0.98, BTC PF
   1.31). No committed artifact. They appear only as prose in
   `docs/FOLLOW-UPS.md:86-87`. `validation_runs` is empty.
4. **`trading_bot_v2/optimization/optimization_studies.db`.** Gitignored and
   absent. If Optuna studies informed any current value, the record is not here.
5. **Whether any parameter was changed without appearing in the tuning log.**
   `.env` is not version-controlled, so undocumented edits leave no trace at all.
   Several class E rows above are probably this.

---

## Code defects found while auditing

Reported, not fixed. Nothing here was changed by this audit.

**In `trading_bot_v2/` (production):**

1. **`GRID_MAX_POSITIONS` is dead.** `grid_trading.py:87` reads it, but the
   manager passes `GRID_MAX_POSITIONS_PER_SYMBOL` (`strategy_manager.py:307`),
   which wins. Setting the former in `.env` has no effect in production.
2. **Manager env defaults silently override constructor defaults.** For
   LiquidationCapture this reverts **all ten** "loosened" constructor values to
   stricter ones; for GridTrading, levels 10 -> 8 and spacing 0.65 -> 0.4; for
   MomentumScalping, `min_confidence` 0.55 -> 0.60. Reading a strategy file gives
   the wrong picture of what runs.
3. **Four parameters have no env override path**: ORB `atr_period` and
   `min_confidence`, CalendarFlow `atr_period` and `min_remaining_hours`. The
   manager does not pass them and the constructors do not read env.
4. **`api_server.py:281-290` hard-codes five enable flags as literals**, so
   `ENABLE_MEAN_REVERSION`, `ENABLE_MA_CROSSOVER`, `ENABLE_TREND_FOLLOWING`,
   `ENABLE_GRID_TRADING` and `ENABLE_LIQUIDATION_CAPTURE` in `.env` are ignored
   on that code path. Two sources of truth for what is enabled.

**In `BTV2/` (evidence generator - affects every artifact it produced):**

5. **`monte_carlo_validate` is order-invariant** (`strategies.py:2007-2106`).
   `MARGINAL` unreachable, `overfitting_flag` always `False`, `verdict` a
   restatement of the sign of the return. Finding 1.
6. **`test_regime_aware_vwap.py` reads the wrong metric keys.**
   `compute_results()` reads `sharpe_ratio` / `max_drawdown`; `compute_metrics()`
   (`strategies.py:1951`) returns `sharpe` / `max_dd_pct`. Every Sharpe and
   max-drawdown in `BTV2/tests/results/regime_vwap_comparison_2024.csv` is `0`.
7. **Zero-trade folds are averaged in as neutral results** (`sharpe 0.0 /
   return_pct 0.0` feeding `year_summary.avg_fold_return_pct`). Absences counted
   as observations.
8. **`baseline_sharpe = -0.12` is a hardcoded literal**
   (`regime_aware_validation.py:422`) driving every `improvement_vs_baseline`.
9. **`optimize_strategy` swallows all exceptions** (`except Exception: pass`) and
   falls back to defaults silently, so a systematically failing combination is
   indistinguishable from a poorly-scoring one.
10. **`BTV2/optimization_studies.db` has a trial stuck in `RUNNING`** since
    2026-04-14. Dead state committed to the repo.

---

## Recommended actions

Ordered by how much folklore each removes per unit of work.

1. **Strike the four `.env` verdict comments** (grid, VWAP, MA crossover,
   momentum) and replace each with a pointer to this file. They are transcriptions
   of `BTV2/CLI_AGENT_GUIDE.md`, and every one of them is class C.
2. **Stop citing MC verdicts.** Either delete `monte_carlo_validate` or replace
   the resampling with a bootstrap (sample trades **with replacement**) so the
   distribution is non-degenerate. Until then, `ROBUST` and `FRAGILE` should not
   appear in any comment or report.
3. **Put a provenance header on every strategy file**, in the style
   `ma_crossover.py:24-31` and `vwap_scalping.py:40-46` already use. Those two are
   the only files where a reader is warned. `grid_trading.py` cites nothing at all
   despite being the subject of a "do not re-enable" instruction.
4. **Re-run the tuning log's sweeps post-`7556e43`, with an out-of-sample split.**
   Every class B row above is a candidate to move to class A or to be deleted.
   Highest value: VWAP `atr_stop_multiplier` 7.0 (largest measured effect, most
   likely to be a single-year artifact) and MeanReversion (worst hit by the
   execution filter).
5. **Commit campaign artifacts.** The grid PF 1.60 / PSR 0.98 result currently
   exists only as a sentence. `validation_runs` and `trial_registry` are empty,
   so the promotion gate has no history and this document had nothing to cite.
6. **Sweep the class D bulk.** OrderBookImbalance (13 parameters) and FundingArb
   (5) have never been measured at all, and both produce 0 trades. Either measure
   them or stop shipping them enabled. (FundingArb: resolved by removal on
   2026-10-10.)
7. **Delete or quarantine `BTV2/CLI_AGENT_GUIDE.md:1544-1556`.** That table is
   the upstream source of the folklore. As long as it reads as a recommendation
   table for the live bot, it will be re-transcribed.

---

## Appendix: on `research/BACKTESTING_GUIDE.md`

Deleted in the same commit as this document (`docs/FOLLOW-UPS.md` item 7),
after reading it in full rather than on the strength of the second-hand report.
What was verified before deleting:

- **The API references are invented.** `DataWarehouse`, `StrategyRegistry`,
  `RealisticCostModel` and `ValidationFramework` appear nowhere in the codebase.
  `BacktestingEngine` exists only in `_junk/Example files/core_logic/backtesting.py`
  with an unrelated constructor.
- **Its code was never run.** `calculate_realistic_costs` (lines 329-352) builds
  a dict containing `'total': sum(total_cost.values())`, referencing `total_cost`
  inside the literal that defines it - an unconditional `NameError`.
- **Its project-specific numbers are wrong.** Regime boundaries given as ADX >30
  / 25-30 / <=25; `market_regime.py:73-75` uses 25.0 / 20.0 / 20.0. Fees given as
  Pacifica taker 0.04% / maker 0.02%; `cost_model.py:26` uses taker 0.06% and
  models no maker fee. "MA Crossover (50/200)" against a 20/50 code default.
- **Nothing references it** - zero inbound links repo-wide.
- **It recommends the flawed Monte Carlo** this document just refuted
  (shuffle the trade sequence).

The remainder is generic, correct, freely-available material on look-ahead bias,
survivorship bias, overfitting and cost modelling, adding nothing that
`docs/BACKTESTING_GUIDE.md` does not cover with accurate project detail.

**One idea was worth keeping, and is preserved here rather than in 552 lines:**
its Monte Carlo sketch resamples over a *range* of slippage and fee assumptions
(`slippage_range=(0.1, 0.5)`, `fee_multiplier=(1.0, 1.5)`) rather than a point
estimate. That is a better design than either the BTV2 implementation or the
current single global constant, and it is directly relevant to
`docs/FOLLOW-UPS.md` item 1 - which observes that every profit factor in the
project is a function of one hardcoded `slippage_pct=0.002` that nobody has
checked against reality. Sensitivity to the cost assumption should be a reported
output, not an unstated premise.
