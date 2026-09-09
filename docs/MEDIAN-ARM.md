# The median arm: measuring the vector the monthly re-tune actually deploys

Status: measured 2026-08-01. Code: `trading_bot_v2/optimization/run_composite_tuning.py`,
`trading_bot_v2/optimization/backfill_median_arm.py`.

## The problem this closes

The monthly re-tune pipeline measured one thing and deployed another.

`run_composite_tuning` runs walk-forward folds. Per fold, per composite
state, it picks an Optuna winner on the train window and scores it on the
unseen test window, alongside the shipped defaults and (since 2026-08-01)
a caller-supplied baseline - in practice the currently adopted params.
Its summary reports `tuned`, `default`, `edge`, `baseline`,
`edge_vs_baseline`.

None of those is what gets deployed. `monthly_retune._fresh_medians`
takes the per-fold winners and computes a **coordinate-wise median** -
each parameter's median across folds, computed independently - and that
median vector is what `ADOPTED_PARAMS` holds and what the adoption rule
installs. Until now no code had ever scored that vector on any window.

Two specific worries motivated the measurement:

1. A coordinate-wise median is not guaranteed to be a jointly sensible
   configuration. With interacting parameters (`rsi_oversold`,
   `rsi_overbought`, `bb_std_dev`, `atr_stop_multiplier`,
   `min_confidence`) the median can land in a region no fold ever
   visited and no trial ever scored.
2. Fold-winner edges are optimistic about what the median delivers, and
   nobody knew the size or even the sign of that gap.

## The construction, stated so it can be checked

The new arm is **prequential**. For fold *k*:

- the median vector is the coordinate-wise median of the winners of
  folds **1 .. k-1 only** - an expanding window;
- it is then scored on **fold k's test window**, which contributed
  nothing to it;
- fold 1 has no prior winners, so fold 1 has **no median arm**; scoring
  starts at fold 2.

A median taken over *all* folds and scored on a fold that helped produce
it would be contaminated by the future and is deliberately never
computed. Where that ordering lives in the code:

- `run_composite_tuning.py:610` declares `prior_winners`, the only state
  carried across folds.
- `run_composite_tuning.py:657-661` snapshots the median from
  `prior_winners` **before** the fold's own winners are recorded, and
  scores it on `te_s..te_e`.
- `run_composite_tuning.py:699` appends this fold's winner - after the
  cell has been written.

The arm shares the fold's test-window backtest cache
(`_cached_backtest`, `run_composite_tuning.py:250`), so a median that
happens to equal a fold winner or the adopted baseline costs zero extra
backtests.

This is a faithful simulation of the live cadence: *"at each monthly
re-tune, deploy the median of everything tuned so far."* That is the
policy actually in use, so this is the number that should govern
adoption.

### Report keys

Per fold, per state, when the arm exists:
`median_params`, `median_from_folds`, `test_median`.

In the summary (`_summarize_state` / `_median_arm_summary`), every
comparison is a **paired** difference - averaged only over the folds
where both of its arms scored, so fold 1's missing median arm cannot
shift the other side:

| key | meaning |
|---|---|
| `median_folds` / `median` / `median_default` / `edge_median_vs_default` | median arm vs shipped defaults |
| `median_tuned_folds` / `median_on_tuned_folds` / `median_tuned` / `edge_median_vs_tuned` | median arm vs the fold winners (the optimism gap) |
| `median_baseline_folds` / `median_on_baseline_folds` / `median_baseline` / `edge_median_vs_baseline` | median arm vs the adopted params - the comparison that governs adoption |

States that never had a median arm keep exactly the summary shape they
had before, so old reports and old readers stay valid.

The monthly report surfaces the arm as `prequential_median_oos` in the
comparison JSON and as three extra columns in the markdown table. The
pre-registered adoption rule itself is **unchanged** - it is still
evaluated on the fold-winner arm. Changing the rule is an adoption
decision and is not made here.

## Part 2 - the answer for 2026-07, without re-tuning

`out/monthly/composite_mean_reversion_2026-07.json` already stores, per
fold per state, the winner `params` and the fold's test window, so the
prequential medians can be reconstructed and scored directly - roughly
3 folds x 6 states of scoring backtests, no Optuna.

    python -m trading_bot_v2.optimization.backfill_median_arm \
        --mode prequential \
        --report out/monthly/composite_mean_reversion_2026-07.json \
        --out out/monthly/median-arm-prequential-2026-07.json

Source run: mean_reversion, BTC-USDC, objective `sharpe_ratio`,
`DIRECTIONAL_GATE=enforce`, 25 trials/fold, 4 folds. Median arm covers
folds 2-4 (test windows 2025-02-01..2025-08-01,
2025-08-01..2026-02-01, 2026-02-01..2026-08-01). All columns below are
means over those same three folds, so the arms are directly comparable;
the fold-winner column therefore differs slightly from the 4-fold
`tuned` figure in the monthly report.

| state | folds | median | tuned | default | adopted | median - tuned | median - default | median - adopted |
|---|---|---|---|---|---|---|---|---|
| vol_low:trend | 3 | +2.316 | +2.077 | +1.165 | +2.201 | **+0.240** | **+1.152** | **+0.115** |
| vol_low:neutral | 3 | -0.244 | -0.567 | -0.960 | n/a | +0.323 | +0.716 | n/a |
| vol_mid:trend | 3 | +2.376 | +1.931 | +1.748 | +1.890 | **+0.445** | **+0.627** | **+0.486** |
| vol_mid:neutral | 3 | +1.285 | +1.393 | +0.230 | n/a | -0.108 | +1.054 | n/a |
| vol_high:trend | 3 | -0.017 | -0.371 | +0.018 | n/a | +0.355 | -0.035 | n/a |
| vol_high:neutral | 3 | -3.092 | -1.971 | -1.559 | n/a | -1.121 | -1.534 | n/a |

For reference, the fold-winner means the monthly report published over
all four folds: vol_low:trend tuned +1.891 / default +1.216 / edge
+0.675; vol_low:neutral +0.751; vol_mid:trend tuned +2.309 / default
+1.979 / edge +0.329; vol_mid:neutral +0.911; vol_high:trend -0.196;
vol_high:neutral -0.351.

### Does the median arm clear the bar the fold-winner arm cleared?

For the two ADOPTED states, yes, and by more:

- **vol_low:trend** - the fold-winner arm's published edge over defaults
  was **+0.675**. The prequential median's edge over defaults is
  **+1.152**. Clears.
- **vol_mid:trend** - published fold-winner edge **+0.329**. Prequential
  median edge **+0.627**. Clears.

The median arm also beats the currently adopted vector on the same
unseen windows (+0.115 and +0.486), which is a different and stronger
statement than anything the report contained before.

### The gap, stated plainly

The pessimistic hypothesis - that the deployed median is systematically
weaker than the fold winners the reports describe - is **not** what the
data shows. The median arm beat the fold-winner arm in **4 of 6** states
(+0.240, +0.323, +0.445, +0.355) and lost in 2 (-0.108 in
vol_mid:neutral, -1.121 in vol_high:neutral). Both losing states are
`neutral`/`vol_high` states that carry no adopted params and that the
2026-07 report already flagged as negative or marginal.

That is a real result, not a rubber stamp: it is the first time the
deployed object has been scored at all, and it could as easily have gone
the other way. It is also a single strategy, a single symbol, three
folds, one objective - it is evidence that the aggregator is not
destroying the edge on this configuration, not proof that a
coordinate-wise median is safe in general.

Two mechanical observations worth keeping:

- The median arm consistently trades **more** than the fold winners
  (e.g. vol_low:trend fold 2: n=87 for the median vs n=47 for the
  winner). Averaging thresholds pulls them toward the middle of the
  search space, which loosens entry selectivity. More trades is why the
  median's per-fold scores are less spiky than the winners'.
- The winners are unstable fold to fold. vol_low:trend's winner
  `rsi_oversold` runs 36.2 -> 27.7 -> 42.9 -> 42.2 over the four folds
  and its fold-3 winner scored **+0.605** out-of-sample against a
  default of **+2.002**. The median's value is mostly that it damps this
  instability, which is a plausible reason it wins.

### Geometry: is the median a vector anyone actually tuned?

Free check, no backtests. A coordinate-wise median is trivially inside
each parameter's own min/max, so that box is checked everywhere and can
only fail on a bug. The informative number is the distance from the
median to the NEAREST fold winner, in search-space-normalised units,
against the mean distance between two winners.

| state | median inside winner range | nearest winner | mean distance to winners | mean pairwise winner distance | unlike any winner |
|---|---|---|---|---|---|
| vol_low:trend | yes | 0.331 | 0.631 | 1.038 | no |
| vol_low:neutral | yes | 0.568 | 0.681 | 1.066 | no |
| vol_mid:trend | yes | 0.392 | 0.592 | 0.932 | no |
| vol_mid:neutral | yes | 0.450 | 0.621 | 1.015 | no |
| vol_high:trend | yes | 0.506 | 0.673 | 1.059 | no |
| vol_high:neutral | yes | 0.543 | 0.619 | 0.991 | no |

No state is flagged. In every state the median sits about 2-3x closer to
the nearest winner than the winners sit to each other, i.e. centrally
inside the cloud of tuned vectors rather than off in a corner nobody
sampled. The absolute distances (0.33-0.57 of a full search-space
diagonal step) are not small - no winner is *near* the median - but the
"median lands in an unvisited region" worry is not realised on this run.

## Part 3 - the specific deployed vectors, cross-symbol

Part 2 validates the procedure; it does not validate the one vector
currently installed. `ADOPTED_PARAMS` was frozen from a 2026-07-30 BTC
run and, per project memory, checked once on ETH and SUI - but no
artifact of that check exists in `out/` and it is not part of the
monthly cadence. This run scores three arms - shipped defaults, the
fresh 2026-07 full-report medians, and the currently adopted vectors -
on ETH-USDC and SUI-USDC over 2023-08-01..2026-08-01, symbols and a
period the fresh tuning never saw.

    python -m trading_bot_v2.optimization.backfill_median_arm \
        --mode cross-symbol --symbol ETH-USDC \
        --start 2023-08-01 --end 2026-08-01 \
        --report out/monthly/composite_mean_reversion_2026-07.json \
        --out out/monthly/median-arm-crosssymbol-ETH.json

Objective `sharpe_ratio`, `DIRECTIONAL_GATE=enforce`, 10k capital. Trade
counts are shown so sample size is visible; every cell here has 187-529
trades, far more than the fold-level cells in Part 2.

### ETH-USDC, 2023-08-01..2026-08-01

| state | default | n | fresh 2026-07 median | n | adopted | n | fresh - default | adopted - default |
|---|---|---|---|---|---|---|---|---|
| vol_low:trend | +1.036 | 187 | +1.682 | 218 | **+1.738** | 210 | +0.646 | **+0.702** |
| vol_low:neutral | -0.469 | 248 | -0.186 | 362 | n/a | - | +0.283 | n/a |
| vol_mid:trend | +0.971 | 253 | +0.656 | 461 | **+1.211** | 344 | -0.314 | **+0.241** |
| vol_mid:neutral | -2.067 | 265 | -1.109 | 281 | n/a | - | +0.958 | n/a |
| vol_high:trend | +0.501 | 338 | +0.904 | 412 | n/a | - | +0.403 | n/a |
| vol_high:neutral | -0.733 | 259 | -0.830 | 316 | n/a | - | -0.097 | n/a |

### SUI-USDC, 2023-08-01..2026-08-01

| state | default | n | fresh 2026-07 median | n | adopted | n | fresh - default | adopted - default |
|---|---|---|---|---|---|---|---|---|
| vol_low:trend | +0.623 | 285 | +1.062 | 275 | **+1.077** | 268 | +0.439 | **+0.455** |
| vol_low:neutral | -0.604 | 288 | -0.176 | 349 | n/a | - | +0.428 | n/a |
| vol_mid:trend | +0.668 | 283 | **+1.178** | 529 | +1.097 | 382 | +0.510 | **+0.430** |
| vol_mid:neutral | -1.698 | 247 | -1.886 | 274 | n/a | - | -0.188 | n/a |
| vol_high:trend | -0.077 | 285 | +0.129 | 328 | n/a | - | +0.207 | n/a |
| vol_high:neutral | -0.640 | 237 | -0.388 | 265 | n/a | - | +0.252 | n/a |

Readings:

- **The currently adopted vectors beat the shipped defaults in all four
  adopted cells** (+0.702, +0.241, +0.455, +0.430) on symbols and a
  period they were never fitted to, with 210-382 trades per cell. That
  independently reproduces the sign of the 2026-07-30 ETH/SUI check
  that had no artifact, and this run *does* leave one
  (`out/monthly/median-arm-crosssymbol-{ETH,SUI}.json`).
- **The adopted vectors are also at least as good as the fresh 2026-07
  medians** in 3 of the 4 adopted cells (ETH vol_low:trend +1.738 vs
  +1.682; ETH vol_mid:trend +1.211 vs +0.656; SUI vol_low:trend +1.077
  vs +1.062). SUI vol_mid:trend is the exception (+1.178 fresh vs
  +1.097 adopted). Nothing here argues for replacing the adopted set -
  and no adoption decision is made in this document either way.
- Across all 12 cells the fresh median beats defaults in **9 of 12**,
  losing on ETH vol_mid:trend, ETH vol_high:neutral and SUI
  vol_mid:neutral. The one loss that matters for a currently deployed
  state is ETH vol_mid:trend at -0.314, which is a real cross-symbol
  failure of the *fresh* median, not of the adopted one.
- The median vectors again trade **more** than defaults almost
  everywhere (ETH vol_mid:trend: 461 vs 253), consistent with the
  loosening effect noted in Part 2.

## Artifacts

| file | what it holds |
|---|---|
| `out/monthly/median-arm-prequential-2026-07.json` | Part 2: per-fold median vectors, scores and trade counts, the per-state aggregate, and the geometry check |
| `out/monthly/median-arm-crosssymbol-ETH.json` | Part 3: ETH-USDC three-arm scores, 2023-08-01..2026-08-01 |
| `out/monthly/median-arm-crosssymbol-SUI.json` | Part 3: SUI-USDC three-arm scores, same window |

The live wiring was also smoke-tested end to end against real data (3
folds, 2 trials, BTC, gate enforce): fold 1 carried no median arm, fold
2 reported `median[1f]`, fold 3 `median[2f]`, and the summary carried
`median`, `edge_median_vs_default`, `edge_median_vs_tuned` and
`edge_median_vs_baseline`.

## Is the coordinate-wise median the right aggregator?

Report only - nothing below is implemented, and no replacement is
proposed for adoption here.

The measurement above says the coordinate-wise median is **defensible on
this configuration**: it beat the fold winners in 4 of 6 states, it sits
centrally inside the cloud of tuned vectors, and it is more stable fold
to fold than any individual winner. That is a lower bar than "correct",
though. The reasons to keep looking:

- **It is justified by an argument nobody has made.** The median wins
  here because fold winners are unstable and averaging damps that
  instability - a shrinkage/robustness argument. But a coordinate-wise
  median is a *strange* shrinkage estimator: it is not the median of the
  vectors (there is no such thing in more than one dimension without
  choosing a definition), it has no optimality property, and it can in
  principle produce a combination none of its inputs would endorse. The
  measurement shows the failure did not happen on this run; it does not
  show the construction prevents it.
- **It throws away the score.** Every winner is weighted equally,
  including vol_low:trend's fold-3 winner, which scored +0.605
  out-of-sample against a default of +2.002. An aggregator that ignores
  how well each input actually did is discarding the one signal the
  walk-forward produced.
- **It throws away recency.** A 36-month rolling window with 12-month
  train folds means fold 1's winner was fitted to data three years old
  and still gets a full vote. If the reason to re-tune monthly is that
  the market moves, equal weighting contradicts the premise.

Candidate alternatives, in the order I would test them:

1. **Nearest-actual-winner (medoid).** Take the winner vector that
   minimises total distance to the other winners, i.e. the most typical
   vector that someone actually tuned and scored. It keeps the
   robustness motivation, removes the "combination nobody proposed"
   risk entirely, and is a one-line change. Cheapest thing to falsify:
   score it as a fifth arm with the same prequential construction.
2. **Robustness-weighted / score-weighted mean.** Weight each winner by
   its out-of-sample score on its own fold (or by rank), so a winner
   that failed out-of-sample contributes less. This directly fixes the
   "throws away the score" problem. Needs a weighting rule chosen
   *before* seeing results, or it becomes another free parameter.
3. **Re-scoring the candidate aggregates.** Rather than any fixed
   formula, generate a handful of candidate vectors (median, medoid,
   mean, most-recent) and pick between them by scoring each on a held-out
   window. This is honest but adds a selection step that itself needs
   an out-of-sample check, so it is the most expensive option.
4. **Most-recent winner.** Simple and maximally responsive, but the
   fold-to-fold instability documented above (rsi_oversold 36.2 -> 27.7
   -> 42.9 -> 42.2) is exactly the noise it would track. I would expect
   it to lose, and it is worth running only as the control that shows
   the aggregation is doing work.

My view: keep the coordinate-wise median for now - it is measured,
it is not broken, and swapping it on the strength of an argument would
repeat the mistake this document exists to fix - but add the medoid as a
fifth prequential arm at the next re-tune. It costs one extra scoring
backtest per fold per state, it answers "is the median's advantage from
robustness or from being a genuinely new point in the space", and unlike
the median it can never be a configuration nobody tuned.

## Verdict

**Does the monthly report now measure the thing it governs?** Yes, from
the next re-tune onward. `run_composite_tuning` scores the prequential
median as a fourth arm on every fold from 2 up, `monthly_retune` carries
it into the comparison JSON as `prequential_median_oos` and into the
markdown as three columns, and this document backfills the answer for
the 2026-07 run that predates the arm. The pre-registered adoption rule
still keys on the fold-winner arm; that is a deliberate separation -
adding the measurement is not the same as changing the gate, and
changing the gate is an adoption decision.

**Is the currently adopted parameter set justified by evidence?** Yes,
better than before, with one honest limit.

- The construction that produced it - a coordinate-wise median of
  walk-forward fold winners - now has an out-of-sample score for the
  first time, and on BTC it is *not* the optimism trap it could have
  been: it beat the fold winners in 4 of 6 states and cleared the
  defaults by more than the published fold-winner edge in both adopted
  states (+1.152 vs the published +0.675; +0.627 vs the published
  +0.329).
- The specific installed vectors now have a cross-symbol artifact: they
  beat the shipped defaults in all four adopted cells on ETH and SUI
  over 2023-08-01..2026-08-01, on 210-382 trades per cell, on data the
  tuning never saw.

The limits, stated so nobody over-reads this: it is one strategy, one
objective, three prequential folds on one symbol plus one three-year
window on two others. The Sharpe differences are point estimates with no
confidence interval attached, several states remain negative in absolute
terms, and the fresh 2026-07 median *did* fail against defaults on ETH
vol_mid:trend (-0.314), which is a live reminder that a median that
looks good on BTC can lose elsewhere. "Justified by evidence" here means
"the deployed object has been measured and did not fail its checks", not
"proven profitable".

