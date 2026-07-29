# Does the regime label discriminate?

**Run date:** 2026-07-28. Full available 4h history per symbol (BTC-USDC
25,329 bars from 2015-01-01, ETH-USDC 22,265 from 2016-05-18, SUI-USDC
7,062 from 2023-05-03; 54,656 bars pooled). Store frozen with
`DATA_AUTODOWNLOAD=false`. Reproduce with:

```bash
DATA_AUTODOWNLOAD=false python -m trading_bot_v2.analysis.regime_discrimination \
    --mode all --quiet --data-dir /ABSOLUTE/path/to/trading_bot_v2/backtesting/data
```

`docs/REGIME-CENSUS.md` asked whether each (strategy, regime) cell has
enough sample to tune. This document asks the question underneath it:
**does the regime label mean anything at all?**

---

## Verdict

**The label does not carry the information the architecture assumes.**

| what the label claims | what it does | effect size |
|---|---|---|
| `trending_strong` marks bars whose trend will continue | those bars are followed by paths **slightly LESS directional than average**, on all three symbols, at every horizon from 12h to 5 days | Cliff's d **-0.05 to -0.10** (wrong sign) |
| the five regimes separate different market states | they separate forward **volatility**, weakly | eps^2 **0.013 - 0.032** (1-3% of rank variance) |
| the regime tells you which way to lean | it does not | eps^2 **0.0003 - 0.0008**, rotation-p 0.27 - 0.92 |

Recalibrating `ADX_TRENDING_THRESHOLD` would be rearranging deck chairs.
**The regime architecture needs rethinking, not the thresholds.** The rest
of this document is the evidence for that sentence.

---

## Method

The harness (`trading_bot_v2/analysis/regime_discrimination.py`) replays
the real detector's classifier and confirmation state machine on the
**same hourly cadence the backtest engine produces** (5m loop, 1h regime
cache TTL, 4h candles, 60-bar history). Its baseline regime shares
reproduce the campaign's to the decimal - 59.9 / 21.6 / 7.6 / 6.3 / 4.6 -
which is the evidence that it is measuring the same object the census and
the campaign measured.

The classifier is re-implemented rather than called, because ADX and the
volatility score are *independent of every threshold*: computing them once
(26s over 54k bars) makes an arbitrary threshold sweep a linear scan.
`tests/test_regime_discrimination.py::TestDetectorEquivalence` asserts the
re-implementation bar-for-bar against `MarketRegimeDetector` over a dense
grid of (ADX, slope, vol score, previous regime) and over 400-bar replays
with each anti-flap mechanism disabled. **If that test fails, every number
below is void.**

Outcomes are measured over the next H 4h bars, starting at the labelled
bar's close (no lookahead):

- **`fwd_vol`** - stdev of forward 4h log returns.
- **`efficiency`** - `|net move| / sum(|bar moves|)`, the directional
  efficiency ratio. Scale-free. Compare against the **random-walk
  benchmark** (0.373 at H=6, from 200k Monte Carlo paths), not against
  zero: a driftless coin flip scores 0.373, so 0.38 is not "somewhat
  trending", it is "a coin flip".
- **`abs_ret`**, **`fwd_ret`** - magnitude and signed forward return.

Effect sizes are Kruskal-Wallis rank **epsilon-squared** across the five
regimes (share of forward rank variance the grouping explains) and
**Cliff's delta** per regime versus the rest. Significance uses a
**circular-rotation null**: both series are heavily autocorrelated -
regimes persist for many bars, volatility clusters, forward windows
overlap - so an iid shuffle would manufacture significance. Rotating the
label series against the outcome series preserves the autocorrelation of
both exactly and destroys only their alignment.

---

## Task 1 - the label barely discriminates, and the one thing it names it gets backwards

### 1a. Forward volatility: real, small, and consistent

H = 6 bars (24h). Cliff's delta versus all other bars.

| symbol | eps^2 | rot-p | d(trending_strong) | d(ranging_calm) | median vol ratio, strong / calm |
|---|---|---|---|---|---|
| BTC-USDC | 0.0130 | 0.0020 | +0.131 | -0.123 | 1.06 / 0.90 |
| ETH-USDC | 0.0320 | 0.0015 | +0.200 | -0.208 | 1.09 / 0.82 |
| SUI-USDC | 0.0179 | 0.0040 | +0.102 | -0.185 | 1.05 / 0.85 |

Same sign on all three symbols, and it survives Holm. But by Romano's
convention every one of those deltas is **negligible-to-small** (|d| <
0.147 negligible, < 0.33 small), and the label explains **1.3% to 3.2%**
of forward-volatility rank variance.

### 1b. Trend persistence: the sign is wrong, on every symbol, at every horizon

This is the finding that matters. `trending_strong` is supposed to be the
bars where the trend continues.

| symbol | H | eps^2 | rot-p | d(trending_strong) | d(ranging_calm) | median eff, strong | random walk |
|---|---|---|---|---|---|---|---|
| BTC-USDC | 3 | 0.0013 | 0.002 | **-0.032** | +0.047 | 0.575 | 0.588 |
| BTC-USDC | 6 | 0.0039 | 0.002 | **-0.056** | +0.082 | 0.382 | 0.373 |
| BTC-USDC | 12 | 0.0040 | 0.006 | **-0.072** | +0.075 | 0.265 | 0.254 |
| BTC-USDC | 30 | 0.0062 | 0.004 | **-0.091** | +0.088 | 0.170 | 0.157 |
| ETH-USDC | 3 | 0.0017 | 0.002 | **-0.032** | +0.055 | 0.591 | 0.588 |
| ETH-USDC | 6 | 0.0024 | 0.006 | **-0.046** | +0.063 | 0.387 | 0.373 |
| ETH-USDC | 12 | 0.0044 | 0.002 | **-0.074** | +0.079 | 0.258 | 0.254 |
| ETH-USDC | 30 | 0.0075 | 0.006 | **-0.097** | +0.088 | 0.164 | 0.157 |
| SUI-USDC | 6 | 0.0029 | 0.070 | **-0.055** | +0.061 | 0.366 | 0.373 |
| SUI-USDC | 30 | 0.0021 | 0.617 | **-0.022** | +0.059 | 0.160 | 0.157 |

Read the last two columns together. `trending_strong`'s median forward
efficiency sits **on the random-walk benchmark** at every horizon - 0.382
vs 0.373, 0.265 vs 0.254, 0.164 vs 0.157. The regime the system treats as
its trend-following green light selects bars whose subsequent price path
is indistinguishable from a coin flip.

Meanwhile `ranging_calm` - the regime that gates mean reversion and grid -
is the one with **positive** delta at every horizon on every symbol. The
label's two poles are inverted relative to their names.

The effect is tiny (|d| <= 0.10) so this is not a tradable reversal
signal. It is a statement about the label: it is not merely uninformative
about trend persistence, it leans very slightly the wrong way.

At H=1 every effect is exactly zero (eps^2 = 0.0000, all p > 0.06), which
is the sanity check: a one-bar efficiency ratio is 1.0 by construction, so
nothing can separate it. The harness correctly finds nothing where there
is nothing to find.

### 1c. Direction: nothing

| symbol | eps^2 (fwd_ret) | rotation-p |
|---|---|---|
| BTC-USDC | 0.0003 | 0.72 |
| ETH-USDC | 0.0008 | 0.27 |
| SUI-USDC | 0.0005 | 0.92 |

No regime predicts the sign of the next 24h. Expected - the label is
direction-blind by construction (ADX ignores sign) - but worth stating,
because the regime-to-strategy map behaves as if `trending_strong` were a
directional green light.

### 1d. The label is worse than the raw numbers it is built from

The detector computes ADX and the volatility score, then throws most of
both away by collapsing them into five buckets. Is the taxonomy adding
anything? `eps^2` and `rho^2` are both shares of explained rank variance,
so they are directly comparable.

| symbol | outcome | **label eps^2** | ADX rho^2 | vol-score rho^2 | trailing-vol rho^2 |
|---|---|---|---|---|---|
| BTC-USDC | fwd_vol | 0.0132 | 0.0239 | 0.0846 | **0.2939** |
| ETH-USDC | fwd_vol | 0.0320 | 0.0471 | 0.0834 | **0.2694** |
| SUI-USDC | fwd_vol | 0.0182 | 0.0207 | 0.0755 | **0.1590** |
| BTC-USDC | abs_ret | 0.0021 | 0.0038 | 0.0153 | **0.0876** |
| ETH-USDC | abs_ret | 0.0062 | 0.0115 | 0.0158 | **0.0750** |
| BTC-USDC | efficiency | 0.0038 | 0.0031 | 0.0076 | 0.0027 |
| ETH-USDC | efficiency | 0.0024 | 0.0016 | 0.0055 | 0.0031 |

On the one axis where the label discriminates at all:

- **Raw ADX beats the label** on every symbol (0.024 vs 0.013, 0.047 vs
  0.032, 0.021 vs 0.018). The discretisation destroys information.
- **The detector's own volatility score beats the label 4-6x**, and it is
  already computed on every ranging bar - then collapsed to one bit
  (calm/volatile) inside a branch that only runs on the 26% of bars where
  ADX <= 20.
- **Plain 14-bar trailing realized volatility beats the label 9-22x.**
  One line of arithmetic explains ~20x more forward-volatility rank
  variance than the entire five-regime taxonomy.

For efficiency, everything is near zero: nothing here predicts trend
persistence, so the label is not losing to a better predictor, it is
losing to nothing.

### 1e. Multiple comparisons

27 rotation tests in the family (4 outcomes x 3 symbols, plus efficiency
at 5 horizons x 3 symbols). Under Holm-Bonferroni at alpha = 0.05, **12 of
27 survive**; the sequence stops at ETH-USDC efficiency@H30 (p = 0.0045 >
0.0033). The survivors are the `fwd_vol` tests and the BTC/ETH efficiency
tests - i.e. exactly the effects described above.

Surviving a rotation null only says the label is not pure noise. It says
nothing about whether the effect is large enough to gate a trading system
on, and the effect sizes say it is not.

### 1f. What the census corroborates, and its confound

`docs/REGIME-CENSUS.md` is confounded as evidence about the label - the
regime gate decides which strategy runs, so a strategy's PF "in" a regime
is not a clean test. But two of its observations are what you would expect
if the labels were near-noise:

- `trending_strong` is 60% of the tape and produced **360 trades in eight
  years**; the two strategies actually mapped to it contributed 131.
- The single best-performing cell in the whole census is
  `vwap_scalping / indecisive` (PF 1.05, n=337) - the **residual bucket**,
  the one nobody designed a strategy for. When the accidental cell wins,
  the taxonomy is not doing the work.

---

## Task 2 - decomposing the 60%

Regime shares over the census window series (`6x2mo@8y`, per-symbol spans,
three symbols, 26,300 detector-hours). The baseline row reproduces
`docs/REGIME-CENSUS.md` exactly.

| configuration | trending_strong | ranging_calm | trending_moderate | indecisive | ranging_volatile |
|---|---|---|---|---|---|
| **baseline (as shipped)** | **59.9%** | 21.6% | 7.6% | 6.3% | 4.6% |
| no ADX exit band (exit = enter 25) | 54.1% | 21.7% | 8.3% | 11.3% | 4.7% |
| no vol bands (enter = exit = raw 65) | 59.9% | 21.4% | 7.6% | 6.3% | 4.8% |
| no min dwell (0h) | 60.0% | 21.7% | 7.6% | 6.1% | 4.6% |
| no 2-count confirmation | 60.0% | 21.7% | 7.6% | 6.1% | 4.6% |
| **no state at all (raw thresholds)** | **54.2%** | 21.6% | 8.2% | 11.2% | 4.7% |
| adx_trending=30 (else as shipped) | 51.4% | 21.6% | 13.4% | 9.0% | 4.6% |
| adx_trending=30, exit=27 | 43.1% | 21.7% | 14.9% | 15.7% | 4.7% |
| pre-Jul2026 (30/25/25/75, no state) | 38.5% | 38.4% | 7.5% | 8.3% | 7.4% |

### Attribution

| mechanism | contribution to trending_strong | share of the 59.9% |
|---|---|---|
| **raw `ADX > 25`** | **54.2 pp** | **90.5%** |
| hysteresis exit band (25 enter / 22 exit) | +5.8 pp | 9.7% |
| minimum dwell (4h) | **-0.1 pp** | ~0% |
| 2-count confirmation | **-0.1 pp** | ~0% |

The premise that "two mechanisms likely compound" is half right. The
hysteresis exit band does add 5.8 points, and it comes almost entirely out
of `indecisive` (11.3% -> 6.3%), because a bar in the 22-25 band that
would have been classified moderate-or-indecisive is instead held as
trending. **The dwell time and the 2-count confirmation contribute
nothing** - not "a little", nothing measurable. They are doing their
actual job (suppressing flicker within a regime) without moving the
shares.

But 90% of the answer is that **ADX > 25 is a coin flip on 4h crypto**.
Pooled median ADX is **26.4**; the threshold sits essentially on the
median of its own distribution:

```
p1=10.6  p5=13.4  p10=15.4  p25=19.7  p50=26.4  p75=35.8  p90=46.4  p95=52.4  p99=62.8
share ADX>20: 73.8%   ADX>25: 55.0%   ADX>30: 39.1%   ADX>35: 26.6%
```

There is no configuration of hysteresis that fixes a threshold placed at
the median.

### The undocumented effect of the 30.0 -> 25.0 change

`market_regime.py` carries the comment "Was 30.0 - catch trends earlier".
Its effect was never measured. It is:

- **At 30.0 with today's bands: 51.4%.** Only 8.5 points lower, because
  a 30-enter / 22-exit band is *much* wider than 25/22, so hysteresis
  absorbs most of the change. Widening the entry threshold without moving
  the exit band mostly converts raw-threshold time into hysteresis time.
- **At 30.0 with a proportional exit band (27): 43.1%.**
- **The full pre-Jul-2026 configuration (30/25/25/75, no hysteresis, no
  dwell, no confirmation): 38.5% trending_strong and 38.4% ranging_calm.**

That last row is the honest comparison, and it shows the headline blames
the wrong parameter. The share of ranging_calm collapsed from 38.4% to
21.6% mostly because **`adx_ranging` moved 25.0 -> 20.0**, not because
`adx_trending` moved 30.0 -> 25.0. Two of the four "Was ..." comments on
those constructor defaults describe changes at least as consequential as
the one that got the comment.

---

## Task 3 - nothing was recalibrated, and here is why

The brief's criterion was to prefer a threshold picked from the data's own
structure over one fitted to P&L. Applying that criterion returns a null
result: **there is no structure to pick from.**

Forward behaviour by ADX bucket, pooled across symbols, each outcome
normalised by its own symbol's median (so 1.00 = unconditional). H = 6.

| ADX bucket | n | fwd_vol | efficiency | abs_ret |
|---|---|---|---|---|
| 0-12 | 1362 | 0.838 | **0.500** | 1.001 |
| 12-15 | 3383 | 0.848 | 0.421 | 0.940 |
| 15-17.5 | 4371 | 0.875 | 0.425 | 0.944 |
| 17.5-20 | 5183 | 0.907 | 0.419 | 0.932 |
| 20-22.5 | 5404 | 0.929 | 0.405 | 0.924 |
| 22.5-25 | 4895 | 0.942 | 0.382 | 0.895 |
| **25-27.5** | 4611 | 0.977 | 0.390 | 0.955 |
| 27.5-30 | 4049 | 0.994 | 0.369 | 0.943 |
| 30-35 | 6852 | 1.035 | 0.379 | 0.989 |
| 35-40 | 4914 | 1.084 | 0.381 | 1.087 |
| 40+ | 9614 | 1.264 | 0.380 | 1.232 |

Random-walk efficiency at H=6 is **0.373**.

Two things fall out:

1. **`fwd_vol` is smooth and monotone in ADX** - 0.838 to 1.264 with no
   knee anywhere, least of all at 25. Any cut point is an arbitrary slice
   of a continuum, and slicing a monotone continuum into buckets is
   strictly worse than using the continuum (which is what 1d measured).
2. **`efficiency` is flat at the random-walk line above ADX 15, and the
   only bucket materially above it is the LOWEST one.** ADX < 12 scores
   0.500 against a 0.373 benchmark; every bucket from 22.5 upward scores
   0.369-0.390. Trend strength over the last 14 bars is not merely
   uninformative about the next 24 hours, it is weakly *anti*-informative,
   and monotonically so.

The volatility score behaves the same way - smooth, monotone, no knee at
60, 65 or 68:

| volscore bucket | n | fwd_vol | efficiency | abs_ret |
|---|---|---|---|---|
| 0-25 | 7446 | 0.707 | 0.452 | 0.767 |
| 25-35 | 5954 | 0.820 | 0.428 | 0.883 |
| 35-45 | 7188 | 0.962 | 0.406 | 0.995 |
| 45-55 | 6475 | 1.006 | 0.391 | 1.002 |
| 55-60 | 3104 | 0.998 | 0.377 | 0.980 |
| 60-65 | 2939 | 1.057 | 0.382 | 1.011 |
| 65-68 | 1617 | 1.008 | 0.381 | 0.972 |
| 68-75 | 3778 | 1.038 | 0.382 | 1.002 |
| 75-85 | 5457 | 1.124 | 0.378 | 1.058 |
| 85+ | 10680 | 1.341 | 0.362 | 1.257 |

Note the spread: the volatility score separates forward volatility from
0.707 to 1.341, nearly twice the range ADX manages - and the detector
already computes it, then uses it only to split the 26% of bars where ADX
<= 20.

### So: no default was changed

Changing a regime threshold is one global parameter that re-gates every
strategy on every symbol. Tuning it against backtest P&L would be
overfitting with an unusually large blast radius, and tuning it against
discriminating power is impossible here because discriminating power is
flat in the threshold. **Every shipped default is unchanged.** The
only code change is that four of them are now readable from the
environment at all (below).

---

## What DID change

### 1. Four documented env vars did not exist

`CLAUDE.md` documents `ADX_TRENDING_THRESHOLD=25.0` as a key environment
variable. Nothing in `trading_bot_v2` ever read it. The same was true of
`ADX_RANGING_THRESHOLD`, `ADX_MODERATE_THRESHOLD` and
`VOLATILITY_HIGH_PERCENTILE`: all four were constructor defaults only,
while `ADX_EXIT_TRENDING`, `VOL_SCORE_ENTER`, `VOL_SCORE_EXIT` and
`MIN_REGIME_DWELL_HOURS` were properly env-wired. Anyone acting on this
document could not have changed a threshold without editing code.

They are now wired in the existing style (explicit argument wins, then
env, then the shipped default). **Behaviour is unchanged when the env is
unset**, which is the current state of `.env`, and a regression test
pins each default.

A non-default value now logs a WARNING naming the blast radius, because
these are global boundaries, not strategy parameters.

### 2. Blast-radius hazard this creates - read before setting the var

`BTV2/optimizer_agent.py` maps a **per-strategy 5m ADX ceiling filter**
(`adx_max`, for mean_reversion and vwap_scalping) onto the env key
`ADX_TRENDING_THRESHOLD`, and writes it into `Bot3/.env` when a study
improves. Those two parameters are not the same thing. Until today that
write was a silent no-op for the live detector. Now it is not: a BTV2
optimizer run with `--apply` can retune the **global regime detector** as
a side effect of tuning one strategy's entry filter.

That mapping should be renamed to a strategy-scoped key. It was left alone
here because BTV2 is a separate codebase and the change belongs with
whoever owns it.

---

## What must be re-run if a threshold is ever changed

A regime threshold change invalidates, completely:

- The **2026-07-28 8-year campaign** (every PF, Sharpe, PSR and gate
  verdict; every strategy is regime-gated).
- **`docs/REGIME-CENSUS.md`** in full - every cell, every trade count,
  every per-regime PF, and the tunable/thin/none classification, since the
  regime confirmed at entry is what tags each trade.
- Every **stored regime overlay** (`regime_param_overlay.py`), which is
  keyed by regime.
- Every **per-regime optimization study** in the database, since its
  trials were selected against a different partition of the tape.
- **`docs/tuning-log.md`** entries that attribute an outcome to regime
  distribution.

Re-running means: the census (~8 minutes), then the validation campaign,
with `--end` pinned and `DATA_AUTODOWNLOAD=false` (see
`docs/FOLLOW-UPS.md` item 8e - without both, "the same" campaign
legitimately returns different numbers).

Nothing above needs re-running for **this** change, because no default
moved.

---

## Where this points

The census's conclusion was that the highest-value regime work is
subtractive. This measurement says the same thing one level down, and more
bluntly:

1. **`trending_strong` is not a regime, and neither is the taxonomy.**
   A five-way partition that explains 1-3% of forward volatility rank
   variance and has the wrong sign on trend persistence is not a
   description of market state. It is a coarse, lossy, and slightly
   inverted proxy for realized volatility.
2. **If the goal is a volatility conditioner, use volatility.** Trailing
   realized vol explains ~20x more forward-volatility variance than the
   label. Feeding a continuous volatility measure into position sizing
   would deliver more than any threshold placement can, without gating
   anything on or off.
3. **The gating decision is the expensive part, not the threshold.** The
   label's real cost is not that it is slightly wrong, it is that it
   *forbids* strategies from trading over 60% of the tape on the strength
   of a signal with a negligible effect size. The census already showed
   the consequence: 60% of the tape, 360 trades, eight years.
4. **The ML shadow track is the right shape of answer** (`USE_ML_REGIME`,
   `ML_REGIME_SHADOW`, `analysis/regime_shadow_report.py`), and this
   document gives it a bar to clear: any replacement classifier should be
   held to a **larger** eps^2 than trailing realized volatility's rho^2 on
   the same outcome, not merely to a better dwell profile. On the evidence
   here that bar is ~0.16-0.29 for forward volatility, and no classifier
   built on ADX will get near it.

The falsifiable version of item 1: re-run the census with the regime gate
**removed** (every strategy allowed in every regime) and compare. If the
gate is carrying real information, removing it should hurt. Given the
effect sizes above, the prior is that it mostly changes trade counts.

---

## Follow-up (2026-07-28): item 2 was acted on

`docs/REGIME-VOLATILITY.md` builds the volatility taxonomy this document
argues for - quantile terciles of the same 14-bar trailing realized
volatility, computed from a trailing reference window only - and runs it
through **this harness** (`--mode taxonomy`, same rotation null, same
horizons, same symbols). Headline:

| | adx_5way | vol_q3 | trailing vol (continuous) |
|---|---|---|---|
| fwd_vol eps^2 / rho^2 | 0.0129 - 0.0321 | **0.0780 - 0.0822** | 0.1593 - 0.2934 |
| d(highest bucket) | +0.13 / +0.20 | **+0.32 on all three symbols** | - |
| smallest bucket's share of tape | 2.5% | 30.3% | - |
| forward direction | nothing | nothing | nothing |

So the taxonomy improves 2.6-6x on the one axis anything discriminates,
and still recovers only 27-51% of what the continuous variable already
knows - which is item 2 of this section restated as a measurement rather
than a prediction. It ships behind `REGIME_MODE=volatility`, default
off. Two of this document's findings replicated with the new instrument:
the calmest bucket is again the one with above-random-walk forward
efficiency, and a 2-D scheme using trailing directional efficiency as a
second axis moves forward-efficiency eps^2 from 0.0038 to 0.0051 - i.e.
trend persistence is not forecastable from the trailing path, and ADX
was not the reason.
