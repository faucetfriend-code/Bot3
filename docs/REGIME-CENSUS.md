# Regime x strategy trade census

**Run date:** 2026-07-28. Window series: `6x2mo@8y`, per-symbol spans, symbols
BTC-USDC / ETH-USDC / SUI-USDC, corrected Pacifica cost model. Reproduce with:

```bash
python -m trading_bot_v2.validation.regime_census --quiet
```

This document answers one question: **for each (strategy, regime) pair, is
there enough sample to support an honest per-regime parameter variant?**

---

## Why the question has to be settled first

Per-regime variants multiply the search space while fragmenting the sample.
The promotion gate derives its pooled minimum from
`statistics.min_observations_for_sharpe(GATE_REFERENCE_SR, GATE_MIN_PSR)` -
**33 closed trades at the current settings** - and additionally needs
**>= 2 symbols with >= 5 trades each** before the cross-symbol check can vote.
A regime cell below either bar cannot produce a PASS or a FAIL, only
`INSUFFICIENT_DATA`.

There is a second cost. `validation/runner.py` reads
`db.get_total_trials(strategy_key)` with **no regime filter**, so every
per-regime study's trial count is added to the same strategy's deflated-Sharpe
denominator. Five 30-trial regime studies charge 150 trials against the pooled
verdict. Splitting a strategy five ways can therefore turn one gradeable
result into five ungradeable ones *and* make the pooled result harder to pass.

---

## The census

Trades are attributed to the regime **confirmed at entry**: `SimulatedExchange`
tags every opening fill with the detector's confirmed regime on that bar, the
closing fill inherits the position's entry tag, and `PerformanceTracker` rolls
it into `BacktestResult.by_regime`.

`*` = tunable (>= 33 pooled AND >= 2 symbols with >= 5)
`!` = traded, but below the derived requirement

| strategy | trending_strong | ranging_calm | trending_moderate | indecisive | ranging_volatile | total |
|---|---|---|---|---|---|---|
| momentum_scalping | **106\*** | - | 3! | - | - | 109 |
| ma_crossover | 25! | - | 3! | - | - | 28 |
| mean_reversion | - | **641\*** | - | - | - | 641 |
| grid_trading | - | **440\*** | - | - | **76\*** | 516 |
| liquidation_capture | **229\*** | **50\*** | 20! | 20! | 21! | 340 |
| vwap_scalping | - | **1246\*** | - | **337\*** | **226\*** | 1809 |

**9 of 30 cells are tunable. 6 traded but cannot be graded. 15 never traded.**

### Per-cell detail

| strategy | regime | n | win rate | PF | pooled pnl | per-symbol |
|---|---|---|---|---|---|---|
| vwap_scalping | ranging_calm | 1246\* | 16.5% | 0.65 | -293.8 | 451 / 452 / 343 |
| mean_reversion | ranging_calm | 641\* | 38.7% | 0.85 | -208.3 | 217 / 262 / 162 |
| grid_trading | ranging_calm | 440\* | 57.7% | 0.85 | -109.2 | 175 / 160 / 105 |
| vwap_scalping | indecisive | 337\* | 20.2% | **1.05** | **+12.5** | 119 / 97 / 121 |
| liquidation_capture | trending_strong | 229\* | 23.6% | 0.86 | -92.3 | 31 / 78 / 120 |
| vwap_scalping | ranging_volatile | 226\* | 17.7% | 0.77 | -53.7 | 49 / 68 / 109 |
| momentum_scalping | trending_strong | 106\* | 48.1% | **1.17** | **+67.0** | 36 / 29 / 41 |
| grid_trading | ranging_volatile | 76\* | 60.5% | **1.06** | **+10.8** | 20 / 26 / 30 |
| liquidation_capture | ranging_calm | 50\* | 12.0% | 0.70 | -44.2 | 16 / 16 / 18 |
| ma_crossover | trending_strong | 25! | 32.0% | 0.71 | -58.7 | 7 / 11 / 7 |
| liquidation_capture | ranging_volatile | 21! | 19.0% | 0.80 | -12.5 | 2 / 9 / 10 |
| liquidation_capture | trending_moderate | 20! | 15.0% | 0.43 | -40.4 | 7 / 4 / 9 |
| liquidation_capture | indecisive | 20! | 10.0% | 0.36 | -41.8 | 2 / 6 / 12 |
| momentum_scalping | trending_moderate | 3! | 33.3% | 1.02 | +0.3 | - / 2 / 1 |
| ma_crossover | trending_moderate | 3! | 33.3% | 2.09 | +16.6 | 1 / 1 / 1 |

(per-symbol columns are BTC-USDC / ETH-USDC / SUI-USDC)

### grid_trading is not reproducible; everything else is

The pooled totals reproduce the 2026-07-28 campaign **exactly** for
momentum_scalping (109), ma_crossover (28), mean_reversion (641),
liquidation_capture (340) and vwap_scalping (1809) - which is the evidence
that the census measures the same thing the campaign did.

`grid_trading` does not. Three runs of identical code over identical windows
produced **578** (campaign), **539** and **516** closed trades, a 12% spread.
Grid lifecycle state (`GridLifecycleManager._grids`) is in-memory and the
grid's own numbers therefore drift run to run. **Every grid_trading figure in
this document, and the campaign's PF 0.90, should be read as approximate until
that is explained.** The direction of the finding (nominal regime profitable,
off-regime mapping not) held across all three runs; the magnitudes did not.

---

## Concentration: every strategy is effectively single-regime

| strategy | trades | dominant regime | share | tunable cells |
|---|---|---|---|---|
| momentum_scalping | 109 | trending_strong | 97% | 1 |
| ma_crossover | 28 | trending_strong | 89% | 0 |
| mean_reversion | 641 | ranging_calm | 100% | 1 |
| grid_trading | 539 | ranging_calm | 86% | 2 |
| liquidation_capture | 340 | trending_strong | 67% | 2 |
| vwap_scalping | 1809 | ranging_calm | 69% | 3 |

**No strategy trades outside the regimes it is gated to** - the regime gating
works as designed. What the census shows instead is *concentration*: four of
six strategies take 86-100% of their trades in a single regime, so a
"per-regime variant" for them is not five variants, it is one variant plus
four empty cells.

Note that CLAUDE.md's regime table is **stale relative to the code**.
`market_regime.py:934` maps `RANGING_CALM -> ["MeanReversion", "GridTrading"]`,
so grid trading is nominally active in ranging_calm too, and
`strategy_manager.py` adds VWAPScalping in RANGING_VOLATILE / RANGING_CALM /
INDECISIVE and MACrossover in TRENDING_MODERATE. The mapping in CLAUDE.md
does not describe any of that.

---

## The mismatch between the tape and the roster

Regime distribution over the campaign's 315,442 bars:
`trending_strong 59.9%`, `ranging_calm 21.6%`, `trending_moderate 7.6%`,
`indecisive 6.3%`, `ranging_volatile 4.6%`.

Set that against the census:

- **trending_strong is 60% of the tape and produced 360 trades in 8 years**
  (momentum 106 + ma_crossover 25 + liquidation 229). The two strategies
  actually mapped to it contribute 131 between them.
- **ranging_calm is 22% of the tape and produced 2401 trades.**
- **ranging_volatile is 4.6% of the tape**, and it is grid trading's nominal
  home - which is why grid only manages 75 trades there in eight years.

The roster is aimed at the fifth of the tape that ranges, while three fifths
of it trends. That is a strategy-selection finding, not a parameter finding,
and no amount of per-regime tuning changes it.

---

## What the census says about per-regime tuning

**Nine cells clear the sample bar, but seven of them are already losing.**
Tuning a losing subset until it looks profitable is exactly what the deflated
Sharpe exists to punish, and each study makes the pooled verdict harder to
pass. The three cells with a positive profit factor are:

| cell | n | PF | symbols |
|---|---|---|---|
| momentum_scalping / trending_strong | 106 | 1.17 | 3 |
| vwap_scalping / indecisive | 337 | 1.05 | 3 |
| grid_trading / ranging_volatile | 75 | 1.06 | 3 |

Two of those are **new information the pooled campaign hid**:

- `vwap_scalping` graded PF 0.75 pooled and was written off. Its
  `indecisive` subset is PF 1.05 on 337 trades across all three symbols,
  while its `ranging_calm` subset is PF 0.65 on 1246 trades. The pooled
  number is an average over a mapping decision, not a property of the
  strategy.
- `grid_trading` graded PF 0.90 pooled. Its **nominal** regime
  (ranging_volatile) is PF 1.06; the ranging_calm mapping added in
  `market_regime.py` contributes 464 trades at PF 0.81 and is what drags the
  pooled figure under 1.

### The highest-value regime work is subtractive, not additive

Removing a losing regime mapping costs **zero search budget**, so it deflates
nothing, and it is testable with the machinery that already exists. Two
candidates, in order:

1. **Stop mapping VWAPScalping into RANGING_CALM.** 1246 trades at PF 0.65,
   -293.8 pooled pnl. What remains is 563 trades at a blended PF near 0.94,
   with the indecisive subset above 1.
2. **Stop mapping GridTrading into RANGING_CALM.** 464 trades at PF 0.81.
   What remains is 75 trades at PF 1.06 - above the 33 floor, but only just,
   so this one must be validated rather than assumed.

Both are one-line changes to `market_regime.py`'s `regime_strategy_map` and
`strategy_manager.py`'s VWAP regime list, and both are falsifiable today by
re-running `validation.runner`.

### If you do tune per regime

`--regime` now composes with `--chunked`, so the fold winner is graded on the
held-out window and the standing 4-check gate is applied to the
regime-filtered out-of-sample series. Trials matching fewer than the
gate-derived minimum (33) are pruned, not the legacy 15.

**The fold layout has to be sized from the census, and this was measured.**
Two smoke runs on `momentum_scalping / trending_strong`, the strongest cell:

| layout | matching trades in the train set | result |
|---|---|---|
| 1 train window (2mo) x 3 symbols | ~18 | **every trial pruned** |
| 2 train windows (4mo) x 3 symbols | **30** | every trial pruned (< 33) |
| 3 train windows (6mo) x 3 symbols | ~45 (extrapolated) | trials should complete |

And the held-out side is thinner still: the 2-month test window produced
**20** matching trades across all three symbols, which fails
`sample_adequacy` on its own. A single fold therefore cannot grade a
per-regime variant. The pooled OOS series needs several folds:

```bash
# 6 windows, 3 for training -> 3 folds, OOS pooled to ~60 matching trades
python -m trading_bot_v2.optimization --strategy momentum_scalping \
    --chunked --regime TRENDING_STRONG \
    --symbols BTC-USDC,ETH-USDC,SUI-USDC \
    --windows 6 --train-windows 3 --window-months 2 --trials 20
```

**Measured runtime:** ~9s per (symbol, 2-month) chunk backtest on the
optimizer path (193s for 21 backtests). That command runs
`trials x folds x symbols x train_windows + folds x symbols` =
`20 x 3 x 3 x 3 + 9` = **549 backtests, about 1h 20m**. At `--trials 40` it
is ~1090 backtests, about **2h 45m**. The census itself is **8 minutes** for
all six strategies.

This is the real cost of the "tune five regime variants per strategy" plan:
30 such runs, most of them against cells that cannot be graded at all.

---

## What would make wider per-regime tuning justified

- **More symbols.** The three-symbol roster is what caps most cells. Ten
  liquid perps would roughly triple every count, moving all five
  liquidation_capture cells and ma_crossover's trending_strong cell over
  the bar.
- **More windows, not longer ones.** Runtime scales with `windows x
  window-months`, so 12 x 2mo costs the same as 6 x 4mo but doubles the
  number of out-of-sample seams.
- **A trending-regime strategy that actually trades.** 60% of the tape
  producing 360 trades in eight years is the binding constraint on every
  trending cell, and it is a roster problem.

Until at least one of those changes, per-regime tuning beyond the three
positive cells above is fitting noise at five times the search cost.

---

## Audit of the pre-existing regime machinery (2026-07-28)

`optimization/run_regime_optimization.py` and `regime_param_overlay.py` were
written before the signal funnel, banded trial scoring, the rolling-origin
walk-forward, the derived gate and the corrected cost model. What was found:

### Fixed in this change

| # | Finding | Status |
|---|---|---|
| 1 | **11 search-space parameters never reached their strategy.** Overlays and optimizer overrides are applied by `setattr`, and 11 search-space keys did not match the instance attribute name: 5 of liquidation_capture's 7, 3 of grid_trading's 5, 3 of orderbook_imbalance's 8. Every sampled value was a silent no-op, so `liquidation_capture` was searching 2 of its 7 declared dimensions and `grid_trading` 2 of its 5. Same class as the `pullback_range_min/max` bug found earlier. | `PARAM_ATTR_ALIASES` + a regression test asserting every declared parameter resolves |
| 2 | **`vwap_scalping.sd_exit_threshold` was a phantom.** VWAPScalping has no exit-at-SD mechanism at all - it exits on the ATR stop or target - so the parameter had no attribute to alias to. It was a pure noise dimension in every VWAP study ever run. | Removed from the search space |
| 3 | **`--chunked` and `--regime` were declared incompatible** (`run_optimize.py`), so regime tuning was reachable only through the legacy `optimize()`: one symbol, one contiguous window, **fit and scored on the same data**, and never run through the promotion gate. | `--regime` now composes with `--chunked`; the fold winner is graded on the held-out window and the gate is applied to the regime-filtered OOS series |
| 4 | **`REGIME_OPT_MIN_TRADES=15` predated the derived gate floor.** A regime study clearing 15 produces a subset the gate then refuses to grade at 33. | Default is now the gate-derived requirement (`load_gate_policy()["min_closed_trades"]`); the env var still overrides |
| 5 | **The overlay path bypasses every `__init__` repair.** momentum's constant-RRR repair and VWAP's `[1.0, 3.0]` deviation clamp run only in the constructor; a stored overlay `setattr`-ing the raw values skipped both, silently disabling the strategy live. | `apply_params_to_strategy` re-checks `check_param_feasibility` on the resulting combination and rolls the whole application back when it could never trade |
| 6 | **`validate_strategy` resolved a `data_dir` for window cutting but never passed it to the backtest**, so the two could read different candle stores. | `_run_chunk_backtest` takes `data_dir` and pins it in-process |

### Still open

- **`run_regime_optimization.py` remains in-sample.** It calls `optimize()`
  per regime and prints a comparison table; nothing in it is graded out of
  sample or gated. Its `--walk-forward` flag is worse than it sounds:
  `OptimizationAdapter.run_walk_forward` does **not** refit per fold - it
  discards the first `train_months` and evaluates the same parameters on all
  subsequent test windows, which every trial also sees. That is in-sample
  selection with extra steps. Prefer
  `python -m trading_bot_v2.optimization --chunked --regime ...`.
- **Overlays are applied globally off one reference symbol's regime.**
  Strategy instances are shared across symbols, so when BTC is trending and
  SUI is ranging, BTC's positions run under SUI's overlay
  (`OVERLAY_REFERENCE_SYMBOL`). Documented in the module, still wrong if more
  than one symbol is traded.
- **Per-regime studies deflate the pooled verdict.**
  `db.get_total_trials(strategy_key)` is called with no regime filter, so
  regime-tagged trial counts are summed into the pooled strategy's DSR
  denominator. This is arguably correct - they are configurations explored
  against the same strategy - but it means five regime studies make the
  pooled result materially harder to pass, and nothing warns you.
- **`--save-overlay` still saves an in-sample winner.** It is now rejected on
  the chunked walk-forward path (each fold has its own winner, so there is no
  single best trial), which leaves the only overlay-producing path an
  unfalsifiable one.
