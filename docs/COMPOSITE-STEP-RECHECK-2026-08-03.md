# The 2026-07-30 campaign sampled only July-December. What changes when it doesn't?

Date: 2026-08-03
Status: **complete**. Three re-runs finished; all five affected artifacts accounted for.
Scope: measurement only. No source file, config, or `.env` was modified by this
investigation.
Companion: `docs/NEUTRAL-STATE-WINDOW-CHECK.md`, which found the same defect in
`mean_reversion` and established the method used here.

---

## The defect

Every composite-state artifact from the 2026-07-30 campaign was run with
`--step-months 12` against a `--test-months 6` window. Fold steps and test
lengths are independent, so the test windows never overlapped and never moved
off the same half of the calendar:

    2021-07  2022-07  2023-07  2024-07  2025-07

Six consecutive years, July-December only. January-June was never scored. The
runner's default (`--step-months` unset -> step = test-months) gives 10 folds
alternating H2 and H1 over the same span.

Nothing in those artifacts recorded the step - that is why this took a
re-run to establish rather than a read. Fixed since: `run_composite_tuning`
now writes a `run_config` block carrying the EFFECTIVE step.

---

## What was re-run

| new artifact | strategy | objective | replaces |
|---|---|---|---|
| `out/composite_grid_step6.json` | grid_trading | sharpe_ratio | `composite_grid_gateoff.json` |
| `out/composite_grid_pf_step6.json` | grid_trading | profit_factor | `composite_grid_pf_gateoff.json`, `composite_grid_pf_gateenforce.json` |
| `out/composite_vpb_step6.json` | vwap_pullback | sharpe_ratio | `composite_vpb_gateoff.json`, `composite_vpb_gateenforce.json` |

Identical settings otherwise: BTC-USDC, 2020-07-01..2026-07-01, train 12 /
test 6, 25 trials, seed 0, `REGIME_MODE=volatility` with the tuned strategy in
all three terciles, `BACKTEST_HISTORY_LOOKBACK=100`. Ten folds each, confirmed
in the run header and in `run_config`.

**Three runs, not five.** The two `gateenforce` artifacts are byte-identical to
their `gateoff` twins, and provably so rather than coincidentally:

- `grid_trading` is in `DEFAULT_GATE_EXEMPT`, so `_apply_directional_gate`
  skips it by name.
- `vwap_pullback`'s own 4h EMA stack is the same computation as the gate's
  trend leg (both `close > EMA(20) > EMA(50)` on `multi_tf_data["4h"]`), and
  `DirectionalBias.combined` returns *neutral* on a trend/funding conflict,
  which `allows()` passes. 0 of 9 bias combinations can block a VPB signal.
  `get_search_space("vwap_pullback")` does not tune the EMA periods, so this
  holds across all 25 trials, not only at defaults. Confirmed empirically with
  a control: VPB off-vs-enforce is identical over two windows (786 and 798
  trades, same ordered PnL digest) while `mean_reversion` in the same harness
  goes 231 -> 163 trades.

---

## Two confounds, and what survives them

**1. The per-fold seed changed.** `fold_seed` became identity-derived on
2026-08-02. Every tuned-arm number in the 2026-07-30 artifacts predates that
fix, so a new-vs-old *tuned* comparison would confound the step change with the
seeding change. The **default arm is unaffected** - fixed inputs, not search
output - so the cross-run comparison runs on it.

**2. The candle store was rewritten.** The 2026-08-01 monthly refresh
(`data_manager --update`) rewrote every BTC parquet, *after* the old artifacts
were produced. The two runs therefore do not read identical data.

This was caught by a validity control rather than assumed away: the default arm
on the folds the two runs SHARE must agree, since identical windows and
identical fixed params must give identical scores. Result across the three
comparisons:

- **vwap_pullback: all 15 shared cells reproduce exactly.**
- **grid_trading: 20 of 21 shared cells reproduce**, differing only in the 4th
  to 6th decimal with identical trade counts.
- **One cell is behavioural**, the same one under both objectives:

      vol_low:trend 2024-07-01   sharpe  -0.1875 (n=16) -> +0.1022 (n=17)
      vol_low:trend 2024-07-01   PF      +0.9313 (n=16) -> +1.0375 (n=17)

  One extra trade after the refresh. Cross-run readings for `vol_low:trend` are
  contaminated; every other state is clean.

The **within-run H2-vs-H1 split** is immune to both confounds - one run, one
data store, one seeding regime - and is the primary evidence below.

---

## Results

Verdict is scored against the objective's break-even: **0.0 for sharpe_ratio,
1.0 for profit_factor**. PF 0.84 is a losing state that reads as a positive
number, and scoring PF against zero would have reported "no changes" for the
profit_factor run while three states crossed 1.0.

### grid_trading / sharpe_ratio - 2 of 6 verdicts change

| state | OLD H2x5 | NEW all 10 | NEW H2 | NEW H1 | OLD->NEW | H2/H1 |
|---|---|---|---|---|---|---|
| vol_low:trend | +0.178 | -0.038 | +0.236 | -0.381 | **pass->FAIL** | pass/FAIL |
| vol_low:neutral | +6224.877 | +2766.518 | +6224.877 | -0.170 | pass->pass | pass/FAIL |
| **vol_mid:trend** | **-1.527** | **+1.892** | **-1.527** | **+3.943** | **FAIL->pass** | FAIL/pass |
| vol_mid:neutral | -0.380 | -0.232 | -0.384 | -0.080 | FAIL->FAIL | FAIL/FAIL |
| vol_high:trend | +7.824 | +2.751 | +7.823 | -0.292 | pass->pass | pass/FAIL |
| vol_high:neutral | +1.022 | +2.408 | +1.021 | +3.102 | pass->pass | pass/pass |

### grid_trading / profit_factor - 1 of 6 verdicts changes

| state | OLD H2x5 | NEW all 10 | NEW H2 | NEW H1 | OLD->NEW | H2/H1 |
|---|---|---|---|---|---|---|
| vol_low:trend | +1.139 | +1.023 | +1.160 | +0.851 | pass->pass | pass/FAIL |
| vol_low:neutral | +3.256 | +1.982 | +3.256 | +0.963 | pass->pass | pass/FAIL |
| **vol_mid:trend** | **+0.557** | **+3.479** | **+0.557** | **+5.233** | **FAIL->pass** | FAIL/pass |
| vol_mid:neutral | +0.839 | +0.992 | +0.839 | +1.145 | FAIL->FAIL | FAIL/pass |
| vol_high:trend | +10.000 | +4.403 | +10.000 | +1.045 | pass->pass | pass/pass |
| vol_high:neutral | +6.739 | +4.486 | +6.739 | +3.359 | pass->pass | pass/pass |

### vwap_pullback / sharpe_ratio - nothing changes

| state | OLD H2x5 | NEW all 10 | NEW H2 | NEW H1 | OLD->NEW |
|---|---|---|---|---|---|
| vol_low:trend | +4.400 | +4.456 | +4.400 | +4.512 | pass->pass |
| vol_mid:trend | +4.528 | +4.635 | +4.527 | +4.743 | pass->pass |
| vol_high:trend | +3.878 | +4.111 | +3.878 | +4.344 | pass->pass |
| all three `:neutral` | n/a | n/a | n/a | n/a | no folds |

---

## Findings

**1. `vol_mid:trend` flips FAIL -> pass under BOTH objectives, independently.**
This is the strongest result. The new run's H2 folds reproduce the old verdict
*exactly* (-1.527 Sharpe, 0.557 PF - the same numbers the 12-month-step run
reported), and the H1 folds the old run structurally could not reach come in at
+3.943 and +5.233. Same run, same data, same seeding: the reversal lives
entirely in windows that were never measured. Two objectives agreeing rules out
an objective-specific artifact.

**2. `vol_low:trend` also flips on Sharpe, but that verdict is not safe.** It is
the one state carrying the refreshed fold, so the cross-run number confounds the
step change with the data change. The within-run split (H2 +0.236 / H1 -0.381)
is clean and shows the same H2-favourable direction; the cross-run flip should
be treated as unproven.

**3. H2 and H1 disagree in 4 of 6 states under each grid objective**, and the
direction is mixed - H2 better in some states, H1 better in others. This is not
seasonality. It is the mean_reversion conclusion again: at these sample sizes
the per-fold numbers cannot carry a sign, and which half of the calendar you
sample decides the verdict.

**4. vwap_pullback is immune, and the reason is sample size.**

| | default-arm cells | trades/cell (min / median / max) |
|---|---|---|
| vwap_pullback | 30 | 44 / **124** / 226 |
| grid_trading | 48 | 1 / **8** / 42 |

VPB's default arm is positive on all 10 folds in all three states (+1.33 to
+7.39), with H1 within 0.5 of H2 everywhere. At a median of 8 trades per
state-fold, grid's per-fold score is noise; at 124, VPB's is not.

**5. Several grid cells are degenerate and should not be read as results.**
`vol_low:neutral` Sharpe is dominated by **+24899 on n=2 trades** in the 2025-07
fold, which makes that row's "no change" meaningless. On the PF side the
objective is capped at 10.0 and a fold with no losing trades scores exactly
10.0: **8 of 48** default cells sit at the cap and **26 of 48** have fewer than
10 trades. `vol_high:trend`'s old +10.000 is a ceiling, not a measurement.

**6. All three vwap_pullback `:neutral` states have zero measurable folds, and
always did.** VPB requires a directional 4h EMA stack; the composite `:neutral`
state is *defined* by that same stack being neutral. VPB can never trade there,
so any VPB neutral-state verdict - in the old artifacts or the new ones - is
vacuous rather than merely unlucky.

---

## What this does and does not license

It does **not** license adopting `vol_mid:trend` for grid_trading. The finding
is that the old verdict was produced on half the calendar and does not survive
the other half - not that the state has an edge. Points 3 and 5 argue against
reading any grid composite-state number as a decision input at these trade
counts.

It does mean the five superseded artifacts should not be cited again. The three
new ones carry a `run_config` block recording the effective step, so this
particular ambiguity cannot recur.

`mean_reversion` was re-measured previously and is covered by
`docs/NEUTRAL-STATE-WINDOW-CHECK.md`.
