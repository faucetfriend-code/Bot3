# Neutral-state result: window-dependent, or fold-alignment artifact?

Date: 2026-08-01 (seeding correction appended 2026-08-02)
Status: **complete**. Main question answered from the existing artifacts; one
confirmatory run finished (ETH cross-symbol), one was killed partway and its
completed folds were salvaged from its log. Both are reported below.
Scope: measurement only. No source file, config, or `.env` was modified *by this
investigation*; the follow-up on 2026-08-02 did change
`run_composite_tuning.py`, see "Consequence of the seeding fix" below.

> **Read this before comparing any tuned number in this document to a fresh
> run.** Every tuned-arm figure here was produced under position-derived
> per-fold seeding, which was a bug and was fixed on 2026-08-02. Post-fix tuned
> numbers will differ for reasons that have nothing to do with markets. The
> **default** and **adopted-baseline** arms are unaffected and stay comparable;
> the **prequential-median** arm is built out of fold winners and therefore
> moves with them. Details: finding (a), item 3c, and the dedicated section
> below.

---

## The question

A composite-state walk-forward run on 2026-07-30 (`out/composite_mr_gateenforce.json`)
concluded that all three NEUTRAL composite states lose out-of-sample in both
the tuned and the default arm. That conclusion is what motivated the
`DIRECTIONAL_NEUTRAL_EXCLUDE` knob in `strategy_manager.py`.

The 2026-08-01 monthly re-tune (`out/monthly/composite_mean_reversion_2026-07.json`),
same strategy / symbol / gate / objective but a shorter and newer window,
returned `vol_mid:neutral` **positive in both arms** (tuned +1.369, default +0.457).

Is the neutral result window-dependent (neutral fading genuinely worked in
2023-2026 and the original verdict was driven by 2020-2023), or is it an
artifact of the shorter window / smaller fold count / fold alignment?

---

## Headline answer

**Neither of the two hypotheses the question offered is right, and the honest
answer is worse than either.**

1. **The recency hypothesis is falsified.** Restricting the original 2020-2026
   run to its own newest folds does *not* reproduce the positive number. The
   2020-2023 folds are not carrying the negative. For `vol_mid:neutral` the
   original run's default arm is -0.707 over all 5 folds, -0.744 over its
   newest 3 folds, and -0.230 over the 2 folds that sit inside the monthly
   run's window. It never turns positive.

2. **The two runs are not the same experiment.** The original run stepped folds
   **12 months**, the monthly run steps **6 months** (the runner's default,
   `--step-months` defaults to `--test-months`). The original therefore tested
   **only July-December windows, in every year, for six years** - half the
   calendar was never scored. The monthly run alternates H2 and H1.

3. **The sign flip lives in test windows the original run structurally could
   not reach.** Within the monthly run, `vol_mid:neutral` default is **-0.055**
   on its two H2 folds (agreeing with the original's -0.230) and **+0.969** on
   its two H1 folds. Newly measured early-era H1 folds (2022-01..2022-07 and
   2023-01..2023-07) are **also negative** (-2.917 and -1.182), so this is
   **not** a seasonal effect. Laid out chronologically, `vol_mid:neutral`
   default is negative on every one of the five test windows ending before
   2024-01 and mixed (3 of 6 positive) on everything from 2024-07 onward. The
   original run's H2-only sampling left it with only **two** post-2024
   observations, and both were negative.

4. **Underneath all of it, the per-fold numbers are too noisy to carry a sign.**
   Shifting a 6-month test window by **one month**, with the *same default
   parameters* and near-identical trade counts, flips the sign of 3 of the 6
   states. `vol_mid:neutral` over 2024-07..2025-01 is -0.049 (n=40, PnL -$0.78);
   over 2024-08..2025-02 it is +1.138 (n=38, PnL +$20.60).

5. **On a second symbol the contradiction does not replicate.** ETH-USDC over
   the monthly run's exact window (2023-08..2026-08, same 4 folds) has **all
   three neutral states negative in both arms**, `vol_mid:neutral` included
   (tuned -0.879, default -0.542, default positive on 1 of 4 folds). BTC's
   positive `vol_mid:neutral` on that window is BTC-specific.

**Verdict on `DIRECTIONAL_NEUTRAL_EXCLUDE`: the conclusion happens to survive,
but the evidence that was cited for it does not support it as stated.**

- "All three neutral states lose in both arms" is **not sound as measured** on
  BTC. It was computed on a seasonally-biased half-sample (see below), it is a
  statement about 4-to-5-fold means with t-statistics between -1.5 and -2.2, and
  the state most responsible for the contradiction reverses when the missing
  windows are added.
- The conclusion nevertheless **holds on the evidence available today**: it is
  reproduced independently on ETH-USDC over the monthly window (all three
  neutral states negative in both arms), and `vol_high:neutral` is negative in
  the default arm on **15 of 15 folds across both symbols and every run**
  (555 default trades), with no positive fold anywhere.
- The single genuine exception is BTC `vol_mid:neutral` from roughly 2024-08
  onward, which is 3-of-6 positive on tiny folds (21 to 44 trades) and does not
  appear on ETH.

So: keep the knob's premise provisionally, discard the specific claim that the
2026-07-30 run established it, and treat BTC `vol_mid:neutral` as unsettled.
Do not flip any env knob on the strength of this document - see "What remains
unresolved".

---

## What the two artifacts actually contain

Both files were produced by `trading_bot_v2/optimization/run_composite_tuning.py`
with `--train-months 12 --test-months 6 --trials 25 --objective sharpe_ratio
--directional-gate enforce`, `REGIME_MODE=volatility`,
`BACKTEST_HISTORY_LOOKBACK=100`, seed 0.

| | A: `out/composite_mr_gateenforce.json` | B: `out/monthly/composite_mean_reversion_2026-07.json` |
|---|---|---|
| window | 2020-07-01 .. 2026-07-01 | 2023-08-01 .. 2026-08-01 |
| folds | 5 | 4 |
| **effective step** | **12 months** | **6 months** |
| test windows | 2021-07, 2022-07, 2023-07, 2024-07, 2025-07 (all +6mo) | 2024-08, 2025-02, 2025-08, 2026-02 (all +6mo) |
| calendar covered by tests | 30 of 72 months, **all H2** | 24 of 24 months since 2024-08, continuous |

The 12-month step in A is not recorded anywhere in the artifact or in `docs/`;
it is inferred from the fold dates, which advance in 12-month increments while
the runner's default step is 6. The confirmatory run below settles it: the same
window at the default step produces **10** folds, and A's 5 are exactly folds
1, 3, 5, 7, 9 of that sequence.

**This is not confined to the mean_reversion artifact.** Every composite-state
artifact in `out/` from the 2026-07-30 campaign has the identical 5 fold dates
and the identical 12-month step:

| artifact | strategy | folds | step | test windows |
|---|---|---|---|---|
| `composite_mr_gateenforce.json` | mean_reversion | 5 | 12mo | 2021-07, 2022-07, 2023-07, 2024-07, 2025-07 |
| `composite_mr_gateoff.json` | mean_reversion | 5 | 12mo | same |
| `composite_grid_gateoff.json` | grid_trading | 5 | 12mo | same |
| `composite_grid_pf_gateenforce.json` | grid_trading | 5 | 12mo | same |
| `composite_grid_pf_gateoff.json` | grid_trading | 5 | 12mo | same |
| `composite_vpb_gateenforce.json` | vwap_pullback | 5 | 12mo | same |
| `composite_vpb_gateoff.json` | vwap_pullback | 5 | 12mo | same |

So the whole 2026-07-30 composite-state campaign - all three strategies, both
gate arms - scored only July-December test windows. The gate-off-vs-enforce
comparison is internally consistent (both arms saw the same months), but every
absolute composite-state verdict from that campaign rests on half the calendar.
This document only re-measures mean_reversion; the grid and vwap_pullback
composite conclusions have not been re-checked and inherit the same sampling.

Both runs scored every state on every fold (5/5 and 4/4). No state was averaged
over a different fold subset, so the "folds count only includes folds where both
arms scored" caveat does not bite here.

---

## Per-fold breakdown

Scores are the objective (sharpe_ratio) on the test window. `n` is closed
trades in that state on that fold. `dflt` = default parameters, which depend
only on the test window, not on training - so default columns are directly
comparable across runs whenever the test windows line up.

### A - 2020-07..2026-07, step 12mo (H2 test windows only)

| test window | state | train_n | tuned n | tuned score | tuned PnL | dflt n | dflt score | dflt PnL |
|---|---|---|---|---|---|---|---|---|
| 2021-07..2022-01 | vol_low:trend | 50 | 63 | +2.182 | +38.81 | 61 | +2.579 | +45.17 |
| | vol_low:neutral | 56 | 52 | -0.459 | -9.17 | 58 | -3.191 | -61.12 |
| | vol_mid:trend | 84 | 39 | -0.829 | -10.49 | 34 | -0.387 | -6.46 |
| | vol_mid:neutral | 55 | 35 | +0.515 | +10.66 | 36 | -0.186 | -3.33 |
| | vol_high:trend | 85 | 27 | +1.618 | +33.65 | 37 | +1.650 | +32.73 |
| | vol_high:neutral | 93 | 25 | +0.276 | +7.28 | 30 | -1.862 | -43.62 |
| 2022-07..2023-01 | vol_low:trend | 82 | 55 | +3.011 | +38.00 | 57 | -1.006 | -13.44 |
| | vol_low:neutral | 105 | 70 | -3.246 | -52.74 | 60 | -2.874 | -44.42 |
| | vol_mid:trend | 158 | 86 | -2.078 | -31.39 | 36 | -2.075 | -35.14 |
| | vol_mid:neutral | 77 | 30 | -1.907 | -30.84 | 35 | -1.118 | -18.75 |
| | vol_high:trend | 87 | 19 | +1.387 | +25.14 | 19 | +1.335 | +25.94 |
| | vol_high:neutral | 73 | 20 | -3.289 | -32.80 | 21 | -5.259 | -53.99 |
| 2023-07..2024-01 | vol_low:trend | 107 | 40 | +0.635 | +3.24 | 30 | -0.287 | -1.21 |
| | vol_low:neutral | 132 | 31 | -0.360 | -1.73 | 34 | +0.192 | +1.28 |
| | vol_mid:trend | 79 | 37 | +0.872 | +6.75 | 37 | -0.746 | -5.19 |
| | vol_mid:neutral | 67 | 39 | -0.368 | -4.11 | 43 | -1.771 | -20.40 |
| | vol_high:trend | 92 | 69 | +1.645 | +23.65 | 46 | +3.760 | +54.84 |
| | vol_high:neutral | 49 | 43 | -0.711 | -12.23 | 35 | -0.020 | -0.39 |
| 2024-07..2025-01 | vol_low:trend | 87 | 46 | -0.919 | -6.53 | 42 | +0.725 | +6.97 |
| | vol_low:neutral | 103 | 59 | -1.190 | -14.45 | 41 | -1.015 | -12.99 |
| | vol_mid:trend | 140 | 91 | +3.693 | +52.55 | 57 | +3.591 | +46.02 |
| | vol_mid:neutral | 83 | 36 | -1.611 | -21.62 | 40 | -0.049 | -0.78 |
| | vol_high:trend | 147 | 62 | +0.356 | +7.85 | 56 | +0.219 | +4.86 |
| | vol_high:neutral | 104 | 51 | +0.586 | +13.94 | 38 | -0.101 | -2.25 |
| 2025-07..2026-01 | vol_low:trend | 242 | 53 | +0.752 | +4.15 | 24 | +1.727 | +8.60 |
| | vol_low:neutral | 169 | 76 | +1.048 | +11.68 | 41 | +0.801 | +6.99 |
| | vol_mid:trend | 124 | 67 | +2.128 | +16.32 | 50 | +1.819 | +14.82 |
| | vol_mid:neutral | 89 | 62 | -1.666 | -14.07 | 34 | -0.410 | -3.85 |
| | vol_high:trend | 76 | 57 | +0.523 | +7.59 | 65 | -1.059 | -17.24 |
| | vol_high:neutral | 103 | 86 | -7.204 | -68.39 | 52 | -2.458 | -40.86 |

### B - 2023-08..2026-08, step 6mo

| test window | state | train_n | tuned n | tuned score | tuned PnL | dflt n | dflt score | dflt PnL |
|---|---|---|---|---|---|---|---|---|
| 2024-08..2025-02 | vol_low:trend | 74 | 57 | +1.333 | +12.44 | 46 | +1.369 | +13.07 |
| | vol_low:neutral | 91 | 69 | +0.035 | +0.45 | 43 | -1.788 | -22.01 |
| | vol_mid:trend | 206 | 116 | +3.442 | +50.90 | 52 | +2.673 | +32.32 |
| | vol_mid:neutral | 140 | 57 | +1.297 | +22.81 | 38 | +1.138 | +20.60 |
| | vol_high:trend | 132 | 48 | +0.901 | +17.08 | 49 | +0.517 | +10.80 |
| | vol_high:neutral | 123 | 69 | -1.138 | -25.78 | 40 | -0.972 | -21.33 |
| 2025-02..2025-08 | vol_low:trend | 57 | 47 | +1.932 | +17.58 | 66 | +0.453 | +5.26 |
| | vol_low:neutral | 144 | 144 | -0.265 | -3.61 | 69 | +0.449 | +6.11 |
| | vol_mid:trend | 165 | 65 | +1.992 | +24.17 | 40 | +1.660 | +19.35 |
| | **vol_mid:neutral** | 127 | **29** | **+2.983** | **+40.11** | **21** | **+1.588** | **+18.78** |
| | vol_high:trend | 147 | 48 | -1.023 | -15.00 | 29 | -0.420 | -5.84 |
| | vol_high:neutral | 83 | 23 | +0.120 | +3.00 | 34 | -1.785 | -41.57 |
| 2025-08..2026-02 | vol_low:trend | 251 | 65 | +0.605 | +3.96 | 24 | +2.002 | +10.40 |
| | vol_low:neutral | 93 | 37 | -0.295 | -2.96 | 40 | -0.783 | -7.28 |
| | vol_mid:trend | 166 | 69 | +2.197 | +20.11 | 38 | +1.933 | +15.74 |
| | vol_mid:neutral | 54 | 24 | -0.071 | -0.77 | 25 | -1.247 | -11.53 |
| | vol_high:trend | 45 | 42 | -0.795 | -9.79 | 70 | -0.406 | -6.87 |
| | vol_high:neutral | 60 | 54 | -3.763 | -49.97 | 47 | -2.144 | -35.52 |
| 2026-02..2026-08 | vol_low:trend | 93 | 48 | +3.693 | +39.26 | 51 | +1.040 | +11.20 |
| | vol_low:neutral | 140 | 65 | -1.140 | -12.38 | 53 | -2.545 | -25.66 |
| | vol_mid:trend | 150 | 72 | +1.603 | +16.89 | 48 | +1.651 | +21.49 |
| | **vol_mid:neutral** | 53 | **46** | **+1.267** | **+20.95** | **44** | **+0.351** | **+5.48** |
| | vol_high:trend | 121 | 41 | +0.704 | +9.35 | 32 | +0.881 | +14.74 |
| | vol_high:neutral | 88 | 31 | -2.270 | -25.39 | 31 | -0.748 | -12.02 |

The two bolded rows are the entire contradiction. They are B's two H1 folds -
the seasonal half that A never tested.

---

## Decomposition 1: the recency hypothesis, falsified

Mean of per-fold default score, `vol_mid:neutral`:

| subset | folds | tuned | default | dflt per-fold |
|---|---|---|---|---|
| A, all | 5 | -1.007 | **-0.707** | -0.19 -1.12 -1.77 -0.05 -0.41 |
| A, newest 3 (2023H2, 2024H2, 2025H2) | 3 | -1.215 | **-0.744** | -1.77 -0.05 -0.41 |
| A, the 2 folds inside B's window | 2 | -1.638 | **-0.230** | -0.05 -0.41 |
| B, all | 4 | +1.369 | **+0.457** | +1.14 +1.59 -1.25 +0.35 |

If the story were "neutral fading started working in 2023", A's newest folds
would show it. They do not - A's newest-3 default mean (-0.744) is
indistinguishable from its all-5 mean (-0.707). A's default arm for
`vol_mid:neutral` is negative on **0 of 5** folds turning positive; every single
fold is negative. Dropping 2020-2023 changes nothing.

The same holds for the other two neutral states: A's newest-3 default means are
-0.007 (`vol_low:neutral`) and -0.860 (`vol_high:neutral`), versus -1.217 and
-1.940 over all 5. The early folds do make the *average* worse, but they are not
the reason any state is negative rather than positive.

## Decomposition 2: what the flip actually is

Split B by seasonal alignment:

| state | B H2 folds (2024-08, 2025-08) — same season A sampled | B H1 folds (2025-02, 2026-02) — never sampled by A |
|---|---|---|
| | tuned / default | tuned / default |
| vol_low:trend | +0.969 / +1.685 | +2.812 / +0.746 |
| vol_low:neutral | -0.130 / -1.286 | -0.702 / -1.048 |
| vol_mid:trend | +2.820 / +2.303 | +1.798 / +1.656 |
| **vol_mid:neutral** | **+0.613 / -0.055** | **+2.125 / +0.969** |
| vol_high:trend | +0.053 / +0.056 | -0.159 / +0.230 |
| vol_high:neutral | -2.450 / -1.558 | -1.075 / -1.267 |

B's H2-aligned default for `vol_mid:neutral` is -0.055, which sits between A's
-0.230 (same 2 folds, shifted a month) and zero. B's H1 folds are +0.969. The
positive summary number is produced by the two folds A structurally could not
see. Note this is *not* evidence of a real seasonal effect - two folds is two
folds - it is evidence that the two runs disagree because they looked at
different months, not different years.

## Decomposition 3: the noise floor

The default arm depends only on the test window. A and B have two test windows
that are the same six months shifted by one month, so five of the six months are
shared and the default parameters are identical. Any disagreement here is pure
fold-placement noise:

| state | A 2024-07..2025-01 (n, score, PnL) | B 2024-08..2025-02 (n, score, PnL) |
|---|---|---|
| vol_low:trend | 42, +0.725, +6.97 | 46, +1.369, +13.07 |
| vol_low:neutral | 41, -1.015, -12.99 | 43, -1.788, -22.01 |
| vol_mid:trend | 57, +3.591, +46.02 | 52, +2.673, +32.32 |
| **vol_mid:neutral** | 40, **-0.049**, **-0.78** | 38, **+1.138**, **+20.60** |
| vol_high:trend | 56, +0.219, +4.86 | 49, +0.517, +10.80 |
| vol_high:neutral | 38, -0.101, -2.25 | 40, -0.972, -21.33 |

| state | A 2025-07..2026-01 | B 2025-08..2026-02 |
|---|---|---|
| vol_low:trend | 24, +1.727, +8.60 | 24, +2.002, +10.40 |
| **vol_low:neutral** | 41, **+0.801**, **+6.99** | 40, **-0.783**, **-7.28** |
| vol_mid:trend | 50, +1.819, +14.82 | 38, +1.933, +15.74 |
| vol_mid:neutral | 34, -0.410, -3.85 | 25, -1.247, -11.53 |
| vol_high:trend | 65, -1.059, -17.24 | 70, -0.406, -6.87 |
| vol_high:neutral | 52, -2.458, -40.86 | 47, -2.144, -35.52 |

Swapping a single month in or out of a 6-month window moves `vol_mid:neutral`
by $21.38 of PnL on ~40 trades and flips its sign; it flips `vol_low:neutral`
too. That is the measurement error of a single fold, and it is the same order
of magnitude as the effect the whole exercise is trying to detect.

## Sample sizes

Fold-level trade counts are small enough that per-fold Sharpe is a
low-information statistic. Totals across all folds, default arm:

| state | A: total dflt trades (5 folds) | A: total dflt PnL | B: total dflt trades (4 folds) | B: total dflt PnL |
|---|---|---|---|---|
| vol_low:trend | 214 | +46.1 | 187 | +39.9 |
| vol_low:neutral | 234 | -110.3 | 205 | -48.8 |
| vol_mid:trend | 214 | +14.1 | 178 | +88.9 |
| vol_mid:neutral | **188** | **-47.1** | **128** | **+33.3** |
| vol_high:trend | 223 | +101.1 | 180 | +12.8 |
| vol_high:neutral | 176 | -141.1 | 152 | -110.4 |

The decisive `vol_mid:neutral` verdict in B rests on **128 default trades across
4 folds**, and the fold that supplies the largest positive Sharpe (+1.588 on
2025-02..2025-08) has **21 default trades**. On the tuned side that fold has 29.
A single fold of ~21 trades moves the 4-fold mean by roughly +0.40.

Across-fold t-statistics on the default arm (5 or 4 observations, so these are
indicative only, and the folds are not independent draws from a stationary
process):

| state | A: mean / sd / t (k=5) | B: mean / sd / t (k=4) |
|---|---|---|
| vol_low:trend | +0.748 / 1.454 / +1.15 | +1.216 / 0.647 / +3.76 |
| vol_low:neutral | -1.217 / 1.784 / -1.53 | -1.167 / 1.297 / -1.80 |
| vol_mid:trend | +0.440 / 2.250 / +0.44 | +1.979 / 0.481 / +8.24 |
| vol_mid:neutral | -0.707 / 0.724 / -2.18 | +0.457 / 1.246 / +0.73 |
| vol_high:trend | +1.181 / 1.792 / +1.47 | +0.143 / 0.659 / +0.43 |
| vol_high:neutral | -1.940 / 2.142 / -2.02 | -1.412 / 0.661 / -4.27 |

B's `vol_mid:neutral` t is **+0.73** on 4 folds. It is not a result. The
statement "vol_mid:neutral is now positive in both arms" is true of the point
estimate and unsupported by the dispersion.

## Where the original claim does and does not survive

Even restricted to A's own H2-only sample, the claim "all three neutral states
lose out-of-sample in both arms" is a statement about fold *means*, not about
folds. Per-fold sign counts, A:

| state | default > 0 | tuned > 0 |
|---|---|---|
| vol_low:trend | 3/5 | 4/5 |
| vol_low:neutral | 2/5 | 1/5 |
| vol_mid:trend | 2/5 | 3/5 |
| vol_mid:neutral | **0/5** | 1/5 |
| vol_high:trend | 4/5 | 5/5 |
| vol_high:neutral | **0/5** | 2/5 |

and B:

| state | default > 0 | tuned > 0 |
|---|---|---|
| vol_low:trend | 4/4 | 4/4 |
| vol_low:neutral | 1/4 | 1/4 |
| vol_mid:trend | 4/4 | 4/4 |
| vol_mid:neutral | 3/4 | 3/4 |
| vol_high:trend | 2/4 | 2/4 |
| vol_high:neutral | **0/4** | 1/4 |

The one claim that survives both runs unbroken is **`vol_high:neutral` is
negative in the default arm on every fold of both runs** (0/5 and 0/4, nine
folds, 328 default trades, -$251.5 combined). `vol_low:neutral` is negative on
7 of 9 folds. `vol_mid:neutral` is 0/5 in A and 3/4 in B - it is the one state
where the two runs genuinely conflict, and it is the state the monthly run
flagged.

---

## Answer to the question as posed

**The proximate cause is fold alignment; the underlying effect, to the extent
there is one, is recent and BTC-specific.**

The original run stepped 12 months and scored only July-December test windows
for six consecutive years - 5 of the 10 windows its own span offered. That left
it with exactly two observations after January 2024, both negative, which is
why it could not see the change the monthly run picked up.

But the "H1 windows are better" reading is wrong: newly measured early-era H1
folds (2022-01..2022-07, 2023-01..2023-07) are negative for all three neutral
states. Chronologically, BTC `vol_mid:neutral` default is negative on all five
windows ending before 2024-01 and mixed on the six from 2024-07 onward. So it
is era-dependent, not season-dependent - and the era in question is the last
eighteen months, measured on folds of 21 to 44 trades.

And it does not generalize: on ETH-USDC over the monthly run's exact window and
folds, all three neutral states are negative in both arms, `vol_mid:neutral`
included.

Underneath all of it, the per-fold estimates are noisy enough that a one-month
shift of the same window flips the sign of half the states. No run here is
large enough to settle `vol_mid:neutral` on BTC either way.

**Is `DIRECTIONAL_NEUTRAL_EXCLUDE` justified by the data?**

- As a *conclusion*: **provisionally yes**, but on evidence other than the run
  that was cited for it. It is reproduced independently on ETH-USDC, and
  `vol_high:neutral` is 0/15 positive folds across both symbols.
- As *stated in the 2026-07-30 artifact*: **no.** That run measured half the
  available test windows, its per-fold means carry t-statistics of -1.5 to -2.2
  on 5 non-independent folds, and its tuned-arm numbers are not even
  reproducible across runs with different fold sequences.
- For `vol_high:neutral` specifically: **the strongest case in the data**, 0 of
  15 positive default folds, 555 trades, two symbols - but still overlapping
  walk-forward folds and not a pre-registered test.
- For `vol_mid:neutral` on BTC: **unsettled.** Negative pre-2024, mixed since,
  and negative on ETH over the same window. The contradiction that prompted
  this investigation is real but is within the measurement noise and does not
  replicate cross-symbol.

No knob was changed and none should be until the items below are closed.

---

## New runs launched (like-for-like, no parameter chasing)

Both use the mandated env (`REGIME_MODE=volatility`, per-tercile MeanReversion
admission and weights, `BACKTEST_HISTORY_LOOKBACK=100`) and the mandated flags
(`--train-months 12 --test-months 6 --trials 25 --objective sharpe_ratio
--directional-gate enforce --log-level WARNING`, seed 0 by default). Neither
passes `--step-months`, so both use the runner's default step of 6. Bulk logs
went to `G:\Candle Data\Temp Test holding`.

1. **`out/neutral_probe_btc_full_step6.json`** - BTC-USDC, 2020-07-01..2026-07-01.
   The decisive run: identical window to A, default step, **10 folds**. A's 5
   folds are exactly folds 1, 3, 5, 7, 9 of this sequence, so this run supplies
   the 5 H1 test windows the original never scored, over the full six years.
   This separates "H1 windows are systematically different" from "the two H1
   folds in 2025-2026 happened to be good".
2. **`out/neutral_probe_eth_2023.json`** - ETH-USDC, 2023-08-01..2026-08-01,
   4 folds. Cross-symbol check on B's exact window: is any neutral-state
   finding BTC-specific?

Sanity gate on both: the header confirmed non-zero folds per state
(10 and 4 respectively) with all six composite states requested, so the
`REGIME_MODE=volatility` env took and nothing came back `insufficient_data`.

### Results - BTC full window at default step (COMPLETE, 10/10 folds)

> **Update 2026-08-02: this run has since been completed.** The original
> attempt was killed at fold 5 of 10 and wrote no report. It was re-run in
> full rather than resumed, because the tuned arm is path-dependent (see (a)
> below) and splicing a separately-run fold into a partial result would give
> tuned numbers that are not mutually comparable. All ten folds now exist in
> `out/neutral_probe_btc_full_step6.json`, log at
> `G:\Candle Data\Temp Test holding\neutral_probe_btc_rerun.log`. **The
> verdict is in the "Resolution" section immediately below; the partial-run
> analysis that follows is retained because findings (a) and (b) stand on
> their own.**

#### Resolution - all three neutral states are negative on complete coverage

With every one of the ten windows scored (2021-07 through 2026-07, no H2-only
gap), means across all 10 folds:

| state | tuned | default | edge | folds positive (default arm) |
|---|---|---|---|---|
| vol_low:neutral | -0.442 | -1.068 | +0.627 | 2 / 10 |
| vol_mid:neutral | -0.703 | -0.801 | +0.097 | 1 / 10 |
| vol_high:neutral | -1.386 | -1.822 | +0.436 | 0 / 10 |
| vol_low:trend | +1.549 | +0.838 | +0.711 | - |
| vol_mid:trend | +1.486 | +0.772 | +0.714 | - |
| vol_high:trend | +1.120 | +1.229 | -0.110 | - |

**"All three neutral states lose in both arms" is correct.** It was reached
from a badly sampled window, but complete coverage confirms it rather than
overturning it. `DIRECTIONAL_NEUTRAL_EXCLUDE` is supported by the data.

The contradiction that started this investigation is fully explained.
`vol_mid:neutral` per fold, default arm:

| # | test window | n | default |
|---|---|---|---|
| 1 | 2021-07..2022-01 | 36 | -0.186 |
| 2 | 2022-01..2022-07 | 51 | -2.917 |
| 3 | 2022-07..2023-01 | 35 | -1.118 |
| 4 | 2023-01..2023-07 | 39 | -1.182 |
| 5 | 2023-07..2024-01 | 43 | -1.772 |
| 6 | **2024-01..2024-07** | 39 | **-1.469** (never scored by any prior run) |
| 7 | 2024-07..2025-01 | 40 | -0.048 |
| 8 | 2025-01..2025-07 | **17** | **+1.855** (the only positive fold) |
| 9 | 2025-07..2026-01 | 34 | -0.410 |
| 10 | 2026-01..2026-07 | 41 | -0.758 |

The window that had never been measured is **negative**, and the single
positive fold rests on the **smallest sample of the ten** (n=17, against 34-51
elsewhere). The monthly re-tune's 4-fold window sits entirely in the recent
region and is dominated by that one thin fold - which is why it reported
`vol_mid:neutral` positive in both arms. That was a short-window artifact, not
a regime change.

Note the trend states, measured here on complete coverage for the first time:
`vol_low:trend` +0.711 and `vol_mid:trend` +0.714 (the two adopted states, both
strongly positive), and `vol_high:trend` -0.110, which continues to favour
leaving that state on defaults.

#### Analysis from the original partial run

Two things came out of the four completed folds.

**(a) The step inference is confirmed, and the tuned arm was not reproducible.**
Fold 1 (train 2020-07..2021-07, test 2021-07..2022-01) reproduces run A's fold
1 to three decimals in both arms, including trade counts. Fold 3 has the
identical train and test windows as run A's fold 2, and its **default** arm
reproduces exactly (`vol_low:trend` -1.006, n=57) while its **tuned** arm does
not (+1.082 on n=84 here versus +3.011 on n=55 in A). The difference is that
fold 3 is preceded by two folds here and one fold in A, so **tuned scores
depended on how many folds ran before them** and were not comparable across
runs with different fold sequences, even at the same seed. Default-arm
comparisons are sound; tuned-arm comparisons between runs A and B are not.

**Mechanism - corrected 2026-08-02.** The paragraph above originally attributed
this to "the Optuna sampler state carrying across folds within a run". That is
wrong, and the wrong diagnosis is recorded here rather than deleted because it
was load-bearing for item 3c below. Both the sampler and the study were
constructed fresh inside the fold loop, so no Optuna state ever crossed a fold
boundary. The real cause was the seed: `TPESampler(seed=args.seed + fold_no)`,
where `fold_no` is the fold's **ordinal position in the sequence**. The same
calendar window was seed 0+2 as fold 2 of a five-fold run and seed 0+3 as fold
3 of a ten-fold run - a different seed, hence a different trial sequence, a
different winner and a different tuned score, with the run's fold count as the
only input that changed. **Fixed 2026-08-02**: the per-fold seed is now derived
from the fold's identity, `sha256("composite-fold-seed/v1" | --seed | strategy
| symbol | train_start | train_end | test_start | test_end)` truncated to 32
bits (`run_composite_tuning.fold_seed`), so a given window reproduces at any
position in any fold sequence. The resolved seed is written into each fold
record in the report JSON. See "Consequence of the seeding fix" below for what
this invalidates.

**(b) Early-era H1 folds are negative too, so there is no seasonal effect.**
The two new test windows, neither of which any prior run scored:

| test window | state | tuned n | tuned | dflt n | dflt |
|---|---|---|---|---|---|
| 2022-01..2022-07 | vol_low:neutral | 25 | -0.581 | 35 | -0.980 |
| | **vol_mid:neutral** | 46 | -0.247 | **51** | **-2.917** |
| | vol_high:neutral | 46 | -0.177 | 47 | -1.648 |
| 2023-01..2023-07 | vol_low:neutral | 53 | -1.787 | 35 | -0.132 |
| | **vol_mid:neutral** | 30 | +0.212 | **39** | **-1.182** |
| | vol_high:neutral | 46 | -1.520 | 35 | -2.067 |

If H1 windows were systematically kinder to neutral fading, these would be
positive. They are not. The H1/H2 split in Decomposition 2 is therefore a
description of *which folds A missed*, not evidence of seasonality.

**Every BTC `vol_mid:neutral` default observation, chronologically:**

| test window | source | dflt n | dflt score |
|---|---|---|---|
| 2021-07..2022-01 | A / probe f1 | 36 | -0.186 |
| 2022-01..2022-07 | probe f2 (new) | 51 | -2.917 |
| 2022-07..2023-01 | A / probe f3 | 35 | -1.118 |
| 2023-01..2023-07 | probe f4 (new) | 39 | -1.182 |
| 2023-07..2024-01 | A | 43 | -1.771 |
| 2024-01..2024-07 | **never measured** | - | - |
| 2024-07..2025-01 | A | 40 | -0.049 |
| 2024-08..2025-02 | B | 38 | **+1.138** |
| 2025-02..2025-08 | B | 21 | **+1.588** |
| 2025-07..2026-01 | A | 34 | -0.410 |
| 2025-08..2026-02 | B | 25 | -1.247 |
| 2026-02..2026-08 | B | 44 | **+0.351** |

Five consecutive negatives through 2024-01, then a mixed regime. Note the
2024-01..2024-07 window is not covered by any run - it would have been probe
fold 6.

### Results - ETH-USDC cross-symbol check (COMPLETE)

`out/neutral_probe_eth_2023.json`, 4 folds, identical window and fold dates to
run B. All six states scored on all four folds.

| state | tuned | default | edge | dflt > 0 | dflt trades | dflt PnL |
|---|---|---|---|---|---|---|
| vol_low:trend | +1.340 | +0.958 | +0.382 | 4/4 | 175 | +51.86 |
| vol_low:neutral | -0.996 | -1.171 | +0.175 | 1/4 | 194 | -84.18 |
| vol_mid:trend | +1.063 | +0.633 | +0.430 | 2/4 | 150 | +47.34 |
| **vol_mid:neutral** | **-0.879** | **-0.542** | -0.337 | **1/4** | 167 | -39.40 |
| vol_high:trend | +0.957 | +0.413 | +0.544 | 3/4 | 189 | +50.98 |
| **vol_high:neutral** | **-1.489** | **-1.222** | -0.267 | **0/4** | 145 | -102.14 |

Per fold, `vol_mid:neutral` default: -1.354 (n=37), -0.228 (n=55), -0.675
(n=32), +0.091 (n=43). The only positive is +0.091 on the final fold.

**This is the cleanest result in the document.** Same window, same folds, same
settings, different symbol: BTC says `vol_mid:neutral` is +1.369 tuned /
+0.457 default, ETH says -0.879 tuned / -0.542 default. The state that
generated the whole contradiction does not replicate one symbol over. On ETH,
the original "all three neutral states lose in both arms" claim is exactly
what the data shows.

Combining every run, the default-arm neutral tally across both symbols:

| state | folds where default > 0 | total folds | total dflt trades |
|---|---|---|---|
| vol_low:neutral | 4 | 15 | 703 |
| vol_mid:neutral | 4 | 15 | 573 |
| **vol_high:neutral** | **0** | **15** | **555** |

(15 folds = run A's 5 + run B's 4 + ETH's 4 + the 2 salvaged BTC probe folds.
Folds overlap in calendar time and share training data across runs, so these
are not 15 independent observations - the count is a consistency check, not a
significance test.)

---

## What remains unresolved

State these plainly before anyone ships a change.

1. **No pre-registered test exists for any neutral state.** Everything above is
   a re-cut of runs that were performed for other reasons. Re-slicing a
   walk-forward by season after seeing the result is exactly the move that
   manufactures findings. The H1/H2 split in this document was chosen *because*
   it explained the discrepancy.
2. **Overlapping training windows.** Folds step 6 months but train on 12, so
   adjacent folds share half their training data. Fold scores are not
   independent and the t-statistics above overstate their own significance.
3. **Two symbols, one strategy, one objective.** Everything is mean_reversion /
   sharpe_ratio, and SUI was not tested at all. `DIRECTIONAL_NEUTRAL_EXCLUDE`
   is a strategy-name list applied at runtime across whatever is enabled, so a
   mean_reversion-only finding does not license a global setting.

3b. ~~**The BTC full-window probe is incomplete.**~~ **RESOLVED 2026-08-02.**
   The probe was re-run to completion, all 10 folds. The 2024-01..2024-07 hole
   is filled and is **negative** (-1.469, n=39), and all three neutral states
   are negative in both arms across the full ten windows. See the "Resolution"
   section above. The only positive `vol_mid:neutral` fold in six years is
   2025-01..2025-07 on n=17, the thinnest sample of the ten, which is what the
   monthly re-tune's short window was picking up.

3c. ~~**Tuned-arm scores are path-dependent.**~~ **RESOLVED 2026-08-02, and the
   stated mechanism was wrong.** Fold 3 of the probe and fold 2 of run A have
   identical train and test windows, and their default arms match exactly while
   their tuned arms do not (+1.082 vs +3.011). The claim that "the Optuna
   sampler state carries across folds within a run" was incorrect - the sampler
   and the study are both created inside the fold loop, so nothing carried. The
   two folds also did **not** have identical seeds, which is the whole problem:
   the seed was `--seed + fold_no`, the fold's ordinal position, so the same
   window drew seed 2 in a five-fold run and seed 3 in a ten-fold run. Fixed by
   deriving each fold's seed from the fold's identity instead (finding (a)
   above, `run_composite_tuning.fold_seed`). The verdict on the historical
   artifacts is unchanged: **tuned-arm comparisons between runs A and B remain
   invalid**, because both were produced under the broken scheme. Only the
   default arm is comparable across those runs. Runs performed after
   2026-08-02 are comparable to each other, and to nothing before it.
4. **The step used in the original run is not recorded.** It is inferred from
   fold dates and confirmed by re-running the same window at the default step.
   Nothing in `out/`, `docs/`, or the artifact JSON records the CLI invocation.
   Composite artifacts should record their own `--step-months`, `--start`,
   `--end`, and env; they currently record only strategy, symbol, states,
   trials, and objective. That is a real gap - it is why this investigation was
   needed at all.
5. **`vol_mid:neutral` trade counts are at the edge of meaninglessness.**
   21 to 57 default trades per fold. Nothing at that sample size should gate a
   live behaviour change in either direction.
6. **Funding model.** Both runs charge the flat funding constant
   (`BACKTEST_FUNDING_MODEL=flat`), which `docs/BACKTESTING_GUIDE.md` records as
   roughly 7.5x the measured carry and never negative. Neutral states hold
   positions the same way trend states do, so this is a common-mode cost, but it
   has not been shown to be neutral *between* the states being compared.
7. **The whole 2026-07-30 composite campaign inherits the same sampling.** All
   seven `out/composite_*.json` artifacts - mean_reversion, grid_trading and
   vwap_pullback, gate off and enforce - share the identical H2-only fold dates.
   Only mean_reversion has been re-measured here. The composite-state verdicts
   for grid_trading and vwap_pullback are untouched and unverified, and the
   MR/grid regime-variant conclusions recorded on 2026-07-30 may rest on the
   same half-sample. Someone should re-run those at the default step before
   they are relied on.

### What would actually settle it

A pre-registered holdout: fix the neutral hypothesis and the acceptance
threshold in writing, then score it once on a span not used in any tuning run
above, on at least two symbols. The runner already supports everything needed;
what is missing is the commitment made before the number is seen.

---

## Consequence of the seeding fix (2026-08-02)

This section exists so that nobody later compares a pre-fix tuned number to a
post-fix one and reads the difference as a finding about markets.

### What was wrong

`trading_bot_v2/optimization/run_composite_tuning.py` seeded each fold's Optuna
sampler with `TPESampler(seed=args.seed + fold_no)`. `fold_no` is the fold's
**ordinal position in the sequence**, so the seed depended on how many folds
happened to precede it in that particular invocation. The same calendar window
drew seed `0+2` as fold 2 of a five-fold run and `0+3` as fold 3 of a ten-fold
run: different trial sequence, different winner, different tuned score, with the
run's fold count as the only input that changed.

Note the mechanism recorded earlier in this document — "the Optuna sampler state
carries across folds within a run" — was **wrong**. Both the sampler and the
study were constructed inside the fold loop, so nothing carried. The seed was
the whole of it.

### What was measured

`mean_reversion` / BTC-USDC, `--trials 2`, `--seed 0`, `--train-months 12
--test-months 6 --step-months 6`, `--objective sharpe_ratio`,
`--directional-gate enforce`, the mandated `REGIME_MODE=volatility` env. Two
runs whose fold sequences overlap but are offset by six months, so the shared
window `train 2021-07-01..2022-07-01 / test 2022-07-01..2023-01-01` is **fold 2
of 2** in run A (start 2021-01-01) and **fold 1 of 1** in run B (start
2021-07-01).

**Before the fix** — every default arm identical, not one tuned arm identical:

| state | tuned as fold 2/2 | tuned as fold 1/1 | default, both runs |
|---|---|---|---|
| vol_low:trend | +1.780 (n=96) | -0.574 (n=123) | -1.006 (n=57) |
| vol_low:neutral | -1.044 (n=103) | -1.931 (n=112) | -2.873 (n=60) |
| vol_mid:trend | -2.808 (n=52) | -2.362 (n=57) | -2.075 (n=36) |
| vol_mid:neutral | -4.138 (n=50) | -2.483 (n=73) | -1.118 (n=35) |
| vol_high:trend | +0.689 (n=23) | +0.158 (n=27) | +1.335 (n=19) |
| vol_high:neutral | -3.280 (n=32) | -3.410 (n=34) | -5.259 (n=21) |

That `vol_low:trend` default of -1.006 on n=57 is the same number reported in
finding (a) above, from an unrelated run a day earlier - the default arm has
always been reproducible, and still is.

**After the fix** — the shared window resolves to seed `3027525560` at both
ordinals, and every tuned score, trade count and winner parameter matches:

| state | tuned as fold 2/2 | tuned as fold 1/1 | match | default, both runs |
|---|---|---|---|---|
| vol_low:trend | +2.485 (n=69) | +2.485 (n=69) | yes | -1.006 (n=57) |
| vol_low:neutral | -0.019 (n=121) | -0.019 (n=121) | yes | -2.873 (n=60) |
| vol_mid:trend | -0.524 (n=72) | -0.524 (n=72) | yes | -2.075 (n=36) |
| vol_mid:neutral | -3.217 (n=56) | -3.217 (n=56) | yes | -1.118 (n=35) |
| vol_high:trend | +1.369 (n=22) | +1.369 (n=22) | yes | +1.335 (n=19) |
| vol_high:neutral | -3.254 (n=36) | -3.254 (n=36) | yes | -5.259 (n=21) |

Winner parameter vectors are identical for all six states as well, not just the
resulting scores. Note the default column is unchanged from the BEFORE table -
that arm never depended on the seed and is the control for this experiment.

Note also that the post-fix tuned column is *different from both* pre-fix
columns. That is expected and is exactly the point of the "what this
invalidates" list below: the seed changed, so the search path changed. It says
nothing about which parameters are better.

### The fix

`run_composite_tuning.fold_seed` derives the seed from the fold's **identity**:

    canonical = "composite-fold-seed/v1|{--seed}|{strategy}|{symbol}"
                "|{train_start}|{train_end}|{test_start}|{test_end}"
    seed = int.from_bytes(sha256(canonical.encode()).digest()[:4], "big")

`hashlib`, not the builtin `hash()`, which is salted per process by
`PYTHONHASHSEED` and would have made the problem total rather than positional.
`--seed` still moves every fold together. Strategy and symbol are in the
derivation on purpose: they are treated in this repo as independent
replications, and keying only on the window would hand BTC, ETH and SUI the
identical opening parameter draw, so "N symbols agree" would partly measure the
shared draw. The objective and the gate arm are deliberately *out*, because
gate-off-vs-enforce is a paired A/B over identical folds where a shared opening
draw removes nuisance variance instead of manufacturing agreement.

Each fold record in the report JSON now carries `seed`, `base_seed` and
`seed_namespace`, so a future artifact states its own reproducibility contract
and a reader can recompute the seed by hand without rerunning anything.

### What this invalidates

- **Every tuned-arm number in every existing `out/composite_*.json` and
  `out/monthly/*.json` artifact.** All of them were produced under
  position-derived seeding. They will not reproduce under the current code, and
  the difference will be pure seeding, not signal.
- **`out/composite_mr_gateenforce.json` specifically**, which is the provenance
  of `ADOPTED_PARAMS` in `trading_bot_v2/optimization/monthly_retune.py`. Those
  values are left exactly as they are - this task made no adoption decision -
  but they are no longer re-derivable from a fresh run of that command.
- **Every tuned figure in this document**, including the whole A-versus-B
  comparison that motivated it. Item 3c said as much already; the reason has
  changed, the verdict has not.
- **The prequential-median arm**, which is the coordinate-wise median of fold
  winners and therefore moves when the winners move.

What is **not** invalidated: the **default** arm and the **adopted-baseline**
arm. Their parameters are fixed inputs rather than search output, so no seed
touches them; they reproduce across the fix and remain comparable to every
historical number. The headline conclusions of this document rest on default-arm
evidence and are unaffected.

The monthly re-tune's adoption rule is also unaffected in structure: it compares
the fresh tuned arm against the default and adopted-baseline arms **scored on
the same test windows within the same run**, never against a stored number from
a previous month. Post-fix runs are comparable to each other, and to nothing
before 2026-08-02.

---

## Artifacts

Existing, read only, unmodified:

- `out/composite_mr_gateenforce.json` - run A (2020-07..2026-07, step 12, 5 folds)
- `out/composite_mr_gateoff.json` - gate-off arm of the same 2026-07-30 A/B, identical fold dates
- `out/monthly/composite_mean_reversion_2026-07.json` - run B (2023-08..2026-08, step 6, 4 folds)

Produced by this investigation:

- `out/neutral_probe_eth_2023.json` - **complete**. ETH-USDC 2023-08..2026-08,
  step 6, 4 folds, all six states scored on all folds.
- `out/neutral_probe_btc_full_step6.json` - **complete (2026-08-02).** BTC-USDC
  2020-07..2026-07, step 6, all 10 folds, all six states scored on every fold.
  The first attempt was killed at fold 5 and wrote no report; it was re-run in
  full rather than resumed, because the tuned arm is path-dependent and a
  spliced fold would not be comparable. Log:
  `G:\Candle Data\Temp Test holding\neutral_probe_btc_rerun.log`.
- `G:\Candle Data\Temp Test holding\neutral_probe_btc_full_step6.log` - log of
  the killed first attempt, retained as the evidence for finding (a); contains
  per-fold results for folds 1-4 only.
- `G:\Candle Data\Temp Test holding\neutral_probe_eth_2023.log` - complete run log.

Both probes used the mandated env and flags, seed 0, no parameter variation.
No source file, config, or `.env` was modified. Nothing was committed. The live
bot and its API server were not touched.

Produced by the 2026-08-02 seeding follow-up, all under
`G:\Candle Data\Temp Test holding\seed_repro\`:

- `run_pair.sh` - the probe. Two offset runs (start 2021-01-01 and 2021-07-01,
  both ending 2023-01-01) so the window `train 2021-07..2022-07 / test
  2022-07..2023-01` lands at a different ordinal in each.
- `before_runA.json` / `before_runB.json` (+ `.log`) - run at the pre-fix commit
  state, position-derived seeding. Tuned arms disagree, default arms agree.
- `after_runA.json` / `after_runB.json` (+ `.log`) - identical commands at the
  post-fix state. Tuned arms, trade counts and winner parameters all agree, and
  each fold record carries its resolved seed.

Unlike the 2026-08-01 investigation, this follow-up **did** modify source:
`run_composite_tuning.py` (the seed derivation, the report field, docstrings)
and a provenance comment in `monthly_retune.py`. No `.env` change, no commit, no
adoption decision, `ADOPTED_PARAMS` values untouched, live bot untouched.
