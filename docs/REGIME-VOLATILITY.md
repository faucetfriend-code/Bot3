# A volatility regime taxonomy, and what it is worth

**Run date:** 2026-07-28. Full available 4h history per symbol (BTC-USDC
25,329 bars from 2015-01-01, ETH-USDC 22,265 from 2016-05-18, SUI-USDC
7,062 from 2023-05-03). Store frozen with `DATA_AUTODOWNLOAD=false`.
Reproduce with:

```bash
DATA_AUTODOWNLOAD=false python -m trading_bot_v2.analysis.regime_discrimination \
    --mode taxonomy --iterations 2000 --quiet \
    --data-dir /ABSOLUTE/path/to/trading_bot_v2/backtesting/data
```

`docs/REGIME-DISCRIMINATION.md` measured the ADX taxonomy and found it
does not carry the information the architecture assumes. This document
builds a replacement, runs it through **the same harness that condemned
the old one**, and reports the result - including the parts that are
unflattering to it.

**Nothing is switched on.** `REGIME_MODE` defaults to `adx`. The ADX
detector is unchanged and remains the default so the two can be
compared rather than swapped.

---

## Verdict

| claim | measured |
|---|---|
| The new taxonomy discriminates forward volatility better than ADX | **Yes: eps^2 0.078-0.082 vs 0.013-0.032. 2.6x on ETH, 4.6x on SUI, 6.0x on BTC.** |
| It discriminates forward direction | **No. eps^2 0.0010-0.0045, rotation-p 0.03-0.08. Nothing predicts direction, and this does not either.** |
| It beats the trivial baseline it is built from | **No, and it cannot. Continuous trailing volatility scores 0.159-0.294; the tercile label recovers 27-51% of that.** |
| A second (efficiency) axis adds enough to justify doubling the cells | **No. +0.000 to +0.005 eps^2 on forward volatility, +0.002 on efficiency, for 6 cells instead of 3.** |
| Buckets are non-degenerate | **Yes, by construction: 30-38% per tercile, against 2.5-63% for the five ADX regimes.** |

The honest summary is: **the label is now a decent volatility
conditioner and still not a direction signal.** That is a real
improvement over a label whose two poles were inverted, and it is a much
smaller improvement than the raw number it is computed from would give
you if you stopped bucketing at all.

---

## The scheme

`trading_bot_v2/volatility_regime.py`.

1. **Trailing realized volatility.** Population standard deviation of
   the last **14** 4h log returns. Not tuned: 14 is the exact window
   whose `rho^2` (0.159-0.294) `docs/REGIME-DISCRIMINATION.md` reports
   as the bar to clear, so tuning it would move the goalposts along with
   the shot.
2. **Quantile buckets, not levels.** The discrimination study found
   forward behaviour smooth and monotone in both ADX and the volatility
   score with **no knee anywhere**, so every fixed cut point is an
   arbitrary slice of a continuum, and `ADX > 25` happened to land on
   the median of its own distribution (pooled 26.4), which is why one
   regime ate 60% of the tape. Ranking against the data's own
   distribution makes that failure mode impossible: each bucket is
   ~1/k of the tape by construction, and the boundaries re-centre as
   epochs change.
3. **Three buckets.** Justified below.
4. **The reference distribution is TRAILING.** 120 calendar days of past
   observations, one per closed 4h bar, ranked by mid-rank fraction. A
   cold start is seeded from the candle history the engine already hands
   the detector (60 bars), which is also strictly past data, so warmup is
   ~25 bars rather than 10 days.
5. **No new anti-flap mechanism.** The inherited 2-count confirmation
   and 4h minimum dwell apply unchanged. The decomposition in the
   earlier study showed both contribute ~0 to regime shares, and adding
   a third knob would put something into the shipped scheme that the
   measurement does not isolate.
6. **Names that cannot lie.** `VOL_LOW` / `VOL_MID` / `VOL_HIGH` /
   `VOL_WARMUP`. Volatility is sign-blind, so no name in this taxonomy
   can be read as a directional green light - which is exactly how
   `trending_strong` was being used.

### Why quantile buckets are not a lookahead trap, and how that is pinned

Computing quantile boundaries over the whole series would label a bar
using volatility that had not happened yet, and would produce a
spectacular, entirely fake result. The classifier is therefore **online**:
it observes one value per bar as the replay advances and ranks it against
what it has already seen. Once emitted, a label can never change.

`tests/test_volatility_regime.py::TestNoLookahead` pins this four ways.
Each builds a 500-bar calm series (sigma 0.004) and a continuation with
a **10x volatility explosion** (sigma 0.04) appended, then asserts that
every label on the shared 500-bar prefix is bit-identical between the two
replays - at the bucketer level, at the analysis-harness level, at the
2-D level, and through `VolatilityRegimeDetector.detect_regime_cached`
itself. If boundaries were computed over the full series, the calm prefix
would be pushed wholesale into the bottom bucket and all four would fail.
The test also asserts the explosion actually moved the distribution, so
it cannot pass vacuously on a series where nothing happens.

---

## The side-by-side table

Forward 6 x 4h = 24h. Every scheme scored on the **same masked sample**
(bars all six schemes label), the same circular-rotation null (2000
rotations, p floor 0.0005), the same effect-size measure. `eps^2` and
`rho^2` are both shares of explained forward rank variance, so the last
three columns are directly comparable with the first six.

### Forward volatility - the one axis anything discriminates

| symbol | n | **adx_5way** | vol_q2 | **vol_q3** | vol_q4 | vol_q5 | vol_q3xeff2 | *trailvol rho^2* | *ADX rho^2* | *volscore rho^2* |
|---|---|---|---|---|---|---|---|---|---|---|
| BTC-USDC | 25259 | 0.0129 | 0.0607 | **0.0780** | 0.0828 | 0.0872 | 0.0816 | *0.2934* | *0.0233* | *0.0838* |
| ETH-USDC | 22205 | 0.0321 | 0.0688 | **0.0822** | 0.0898 | 0.0918 | 0.0871 | *0.2697* | *0.0471* | *0.0832* |
| SUI-USDC | 7002 | 0.0179 | 0.0617 | **0.0819** | 0.0831 | 0.0849 | 0.0864 | *0.1593* | *0.0208* | *0.0766* |

All rotation-p at or near the floor (0.0005-0.0055) for every scheme.

### Forward directional efficiency - nothing, for anyone

| symbol | **adx_5way** | vol_q3 | vol_q5 | vol_q3xeff2 | *trailvol rho^2* |
|---|---|---|---|---|---|
| BTC-USDC | 0.0038 | 0.0038 | 0.0044 | 0.0057 | *0.0028* |
| ETH-USDC | 0.0024 | 0.0043 | 0.0046 | 0.0057 | *0.0030* |
| SUI-USDC | 0.0030 | 0.0034 | 0.0042 | 0.0038 | *0.0020* |

0.4-0.6% of rank variance. The 2-D scheme is the best of them and is
still explaining half a percent.

### Forward return - nothing, confirmed for the new scheme too

| symbol | adx_5way | vol_q3 | rotation-p (vol_q3) |
|---|---|---|---|
| BTC-USDC | 0.0003 | 0.0012 | 0.0515 |
| ETH-USDC | 0.0008 | 0.0010 | 0.0775 |
| SUI-USDC | 0.0006 | 0.0045 | 0.0295 |

SUI's 0.0045 at p = 0.03 is the largest directional effect in the table
and it explains **0.45%** of rank variance on the shortest history of
the three symbols, with no support from BTC or ETH. Under Holm across
this family it does not survive. It is noise, and it is reported here so
that nobody rediscovers it later and calls it a signal.

### Share of the tape per label

The non-degeneracy claim, checked rather than asserted:

| scheme | BTC-USDC | ETH-USDC | SUI-USDC |
|---|---|---|---|
| adx_5way | 2.5 - 63.3% | 3.5 - 60.3% | 6.7 - 55.7% |
| vol_q3 | 30.3 / 33.2 / 36.6% | 31.4 / 33.1 / 35.5% | 30.6 / 31.3 / 38.1% |
| vol_q5 | 18.4 - 24.0% | 18.6 - 22.4% | 17.9 - 24.0% |

The ADX taxonomy's smallest regime is `ranging_volatile` at **2.5% of
BTC's tape** - and that is grid trading's nominal home, which is why the
census found only 76 grid trades there in eight years. Quantile buckets
cannot do that.

### The same table at H = 30 (5 days)

`--horizon 30`, everything else identical. The ordering is unchanged, so
the result is not an artefact of the 24h horizon:

| symbol | outcome | adx_5way | **vol_q3** | vol_q5 | vol_q3xeff2 | *trailvol rho^2* |
|---|---|---|---|---|---|---|
| BTC-USDC | fwd_vol | 0.0180 | **0.0899** | 0.0989 | 0.0935 | *0.3614* |
| ETH-USDC | fwd_vol | 0.0382 | **0.0833** | 0.0919 | 0.0873 | *0.3246* |
| SUI-USDC | fwd_vol | 0.0133 | **0.0898** | 0.0953 | 0.0954 | *0.1970* |
| BTC-USDC | efficiency | 0.0062 | 0.0098 | 0.0116 | 0.0101 | *0.0109* |
| ETH-USDC | efficiency | 0.0077 | 0.0164 | 0.0209 | 0.0174 | *0.0178* |
| SUI-USDC | efficiency | 0.0021 | 0.0036 | 0.0060 | 0.0043 | *0.0042* |

Note SUI's `adx_5way` fwd_vol at H = 30: eps^2 0.0133 at **rotation-p
0.065**, i.e. the shipped label fails to separate forward volatility on
SUI at all at this horizon, while `vol_q3` scores 0.0898 at p = 0.0055.
Efficiency creeps up for everything at the longer horizon (1-2% of rank
variance) but the 2-D scheme is still not the best of them - `vol_q5` is
- which is another way of saying the gain is coming from finer
volatility resolution, not from the efficiency axis.

### Per-bucket detail, vol_q3, H = 6

Cliff's delta versus all other bars. Random-walk median efficiency at
this horizon is 0.373.

| symbol | bucket | share | fwd_vol ratio | d(fwd_vol) | median fwd efficiency | d(efficiency) |
|---|---|---|---|---|---|---|
| BTC-USDC | vol_low | 36.6% | 0.81 | **-0.273** | 0.423 | +0.072 |
| BTC-USDC | vol_mid | 30.3% | 0.97 | -0.034 | 0.388 | -0.022 |
| BTC-USDC | vol_high | 33.2% | 1.31 | **+0.317** | 0.371 | -0.054 |
| ETH-USDC | vol_low | 35.5% | 0.81 | **-0.287** | 0.438 | +0.077 |
| ETH-USDC | vol_mid | 31.4% | 0.98 | -0.023 | 0.395 | -0.023 |
| ETH-USDC | vol_high | 33.1% | 1.28 | **+0.319** | 0.373 | -0.056 |
| SUI-USDC | vol_low | 38.1% | 0.83 | **-0.279** | 0.408 | +0.069 |
| SUI-USDC | vol_mid | 30.6% | 0.99 | -0.022 | 0.364 | -0.031 |
| SUI-USDC | vol_high | 31.3% | 1.25 | **+0.328** | 0.364 | -0.045 |

Two things to read here.

**The volatility separation is real and now large enough to name.** The
ADX taxonomy's best forward-volatility deltas were +0.131 / -0.123 (BTC)
and +0.200 / -0.208 (ETH), all "negligible-to-small" on Romano's
convention. These are +0.32 / -0.28 on all three symbols - still "small"
(< 0.33) but at the top of that band, consistent across symbols, and the
median forward volatility ratio spreads 0.81 to 1.31 rather than 0.90 to
1.09.

**`vol_low` is the bucket with above-random-walk forward efficiency**
(0.408-0.438 against 0.373), and `vol_high` sits exactly on the
random-walk line (0.364-0.373). This replicates the ADX study's finding
from the other direction: there, the only bucket materially above the
line was `ADX < 12`, the *calmest* one. Quiet markets are very slightly
more directional over the next day than violent ones. The effect is
|d| <= 0.077 - not tradable - but it is the second independent
measurement pointing the same way, and it is the opposite of what the
trend-following mapping assumes.

---

## Why three buckets

Measured, not assumed. Marginal eps^2 on forward volatility, averaged
across the three symbols:

| k | mean eps^2 | gain over k-1 | cells | tape per cell |
|---|---|---|---|---|
| 2 | 0.0637 | - | 2 | ~50% |
| **3** | **0.0807** | **+0.0170** | 3 | ~33% |
| 4 | 0.0852 | +0.0045 | 4 | ~25% |
| 5 | 0.0880 | +0.0028 | 5 | ~20% |

The step from 2 to 3 is worth nearly four times the step from 3 to 4, and the
curve is flat after that - which is what you expect when discretising a
monotone continuum: the first few cuts recover most of the rank
information and the rest is diminishing.

The cost side is the census. `docs/REGIME-CENSUS.md` measured that a
gradeable (strategy, regime) cell needs **33 pooled closed trades and
2+ symbols with 5+ each**, and that under the current five-way split
**15 of 30 cells never traded and 6 more traded but could not be
graded**. Cell count is what fragments the sample, and every per-regime
study is also charged to the pooled strategy's deflated-Sharpe
denominator. Paying 4 more cells' worth of sample fragmentation for
+0.0073 eps^2 (k = 3 to k = 5) is a bad trade. **k = 3.**

The shipped detector is fixed at 3 because regime values are persisted
and a stored `vol_high` must mean the same thing in every record that
carries it. The classifier is k-generic so the harness can keep sweeping.

---

## The 2-D evaluation, and why it was rejected

The brief asked for a serious look at a second, orthogonal axis:
**trailing directional efficiency** (net move / path length), which is a
more direct measure of "is this trending" than ADX is. It was built
(`trailing_efficiency`, ranked by the identical trailing-quantile
machinery so it inherits the no-lookahead property) and measured as
`vol_q3xeff2`: 3 volatility terciles x 2 efficiency halves = 6 cells.

| outcome | vol_q3 (mean) | vol_q3xeff2 (mean) | delta |
|---|---|---|---|
| fwd_vol | 0.0807 | 0.0850 | +0.0043 |
| efficiency | 0.0038 | 0.0051 | +0.0013 |
| abs_ret | 0.0164 | 0.0168 | +0.0004 |
| fwd_ret | 0.0022 | 0.0025 | +0.0003 |

**Rejected for the shipped taxonomy.** On forward volatility it gains
less than simply going to `vol_q4` (0.0852) while doubling the cells. On
forward efficiency - the axis it exists to capture - it moves 0.0038 to
0.0051, i.e. from explaining 0.4% of rank variance to explaining 0.5%.
Trailing directional efficiency does not predict forward directional
efficiency, which is the same null result the earlier study found for
ADX, reached with a better instrument. That is worth knowing: it is not
that ADX was a bad proxy for trend persistence, it is that **trend
persistence over the next 24h is not forecastable from the trailing path
at all** on this data.

The one place a 2-D scheme still has a mechanical argument is grid
trading, which needs *oscillation* - high volatility AND low directional
efficiency - and a 1-D volatility split genuinely cannot separate
"violent chop" from "violent trend". That argument is about strategy
mechanics, not about forward-outcome discrimination, and it should be
settled by running the census under both mappings rather than by this
table. The code to label it is in the harness
(`label_bars_two_axis`), so that experiment costs nothing to set up.

---

## The comparison the scheme loses

Stated plainly, because it is the most important line in this document:

**A bucketed regime scores below its own input variable.**

| symbol | vol_q3 eps^2 | trailing vol rho^2 (continuous) | recovered |
|---|---|---|---|
| BTC-USDC | 0.0780 | 0.2934 | 27% |
| ETH-USDC | 0.0822 | 0.2697 | 30% |
| SUI-USDC | 0.0819 | 0.1593 | 51% |

Discretising a monotone continuum always throws information away; that
is what `docs/REGIME-DISCRIMINATION.md` section 1d measured for ADX and
it is no less true here. The three-way label keeps a quarter to a half
of what the number it is computed from already knows.

The reason to bucket at all is that the architecture's *gating* decision
is discrete - a strategy is either allowed to trade or it is not - so
some partition has to exist. It is not a reason to throw the continuous
value away. Under `REGIME_MODE=volatility` the detector already exposes
the trailing volatility **percentile** (0-100) on
`_last_volatility_score`, and the highest-value use of this work is
probably not the gate at all: it is feeding that percentile into
position sizing, where it conditions size continuously and forbids
nothing. That is the same conclusion the earlier study reached, and this
measurement does not overturn it.

---

## Proposed strategy mapping

Under a volatility taxonomy the existing maps are meaningless - they
name ADX states. The proposal below is argued from **what each strategy
needs mechanically**, and it is deliberately NOT tuned against P&L: a
regime map is one global object that gates every strategy at once, and
the deflated Sharpe never sees that search.

| regime | strategies | mechanical argument |
|---|---|---|
| `VOL_LOW` | MeanReversion | Prices oscillate inside a narrow envelope, and an ATR-scaled stop is cheap, so reversion has room to work before the stop. Grid is excluded: its levels are ATR-spaced, and in the bottom volatility tercile the spacing struggles to clear a round trip in fees (`GRID_SPACING_ATR_MULTIPLIER` 0.4 against a small ATR). |
| `VOL_MID` | GridTrading, VWAPScalping | Enough amplitude for an oscillation to pay for itself, not so much that price walks straight through the grid and leaves inventory. VWAP deviations are large relative to costs and still revert. |
| `VOL_HIGH` | MomentumScalping, MACrossover | Expansion, gaps and liquidation cascades. Reversion gets stopped out and a grid gets run over; strategies whose payoff needs a large move are the ones whose shape matches. |
| `VOL_WARMUP` | (none) | No trailing reference distribution yet. Stay flat rather than guess. |

Overlays (LiquidationCapture, FundingArb, OrderBookImbalance,
SessionRangeBreakout, CalendarFlow) keep running in all regimes, exactly
as today - `strategy_manager` adds them independently of the map.

Configurable, in the established style:
`REGIME_VOL_STRATEGIES_{LOW,MID,HIGH,WARMUP}` (comma-separated display
names) and `REGIME_VOL_WEIGHTS_{LOW,MID,HIGH,WARMUP}` (`Name:weight`
pairs). A non-default value logs a warning naming the blast radius.
`is_grid_allowed` is **derived** from the map rather than being a second
list, so the two cannot disagree.

The census points the same way on the one strategy where it can, with the
caveat that it is confounded (the gate decides which strategy runs, so a
per-regime PF is not a clean test): `grid_trading` scores **PF 1.06 in
`ranging_volatile`** - high volatility score - and **PF 0.81 in
`ranging_calm`**, and the ranging_calm mapping is what drags its pooled
figure under 1. Its nominal home is 2.5-7.9% of the tape, which is why it
managed 76 trades there in eight years; `VOL_MID` is ~31%. That is the
single largest practical consequence of this taxonomy: the strategy most
starved of opportunity gets an order of magnitude more of it, in the
volatility band where it already performs best.

### The current mapping may be inverted, and here is the evidence

This is the uncomfortable part.

- The trend strategies (MACrossover, MomentumScalping) are gated to
  `TRENDING_STRONG`, whose median forward directional efficiency sits
  **on** the random-walk line (0.382 vs 0.373). The reversion strategies
  (MeanReversion, GridTrading, VWAPScalping) are gated to
  `ranging_calm`, which is the regime with **positive** forward-
  efficiency delta at every horizon on every symbol.
- The measurement here reaches the same place from the volatility side:
  `vol_low` has forward efficiency 0.408-0.438 against a 0.373
  benchmark, and `vol_high` has 0.364-0.373, i.e. exactly random.

Both effects are tiny (|d| <= 0.10) and neither is tradable on its own.
They are not a reason to run trend strategies in calm markets. They are a
reason to stop believing that the *current* gate is pointing them at the
persistent part of the tape, because on eight years of measurement it is
pointing them at the least persistent part.

The falsifiable version: re-run `validation.regime_census` under
`REGIME_MODE=volatility` with the mapping above and compare per-cell PF
against the ADX census. That is one 8-minute run and it is the only
honest way to settle this. **It has not been done**, which is why the
flag ships off.

---

## Backward compatibility: new enum values, not redefined ones

`MarketRegime` gains `VOL_LOW` / `VOL_MID` / `VOL_HIGH` / `VOL_WARMUP`.
The five ADX values are untouched.

The alternative - reusing `trending_strong` and friends with new
definitions - is cheaper in code and much worse in practice. Regime
values are **persisted**: every trade is tagged with the regime confirmed
at entry, `validation_runs.chunks_json` stores per-chunk regime
histograms, `regime_param_overlays` is keyed by regime, and
`docs/REGIME-CENSUS.md` is a table of those values. Redefining
`trending_strong` to mean "top volatility tercile" would leave every
existing record looking valid and meaning something different, and would
apply an overlay tuned on ADX `ranging_calm` to a regime that no longer
resembles it.

Disjoint values give three things for free: a stored row always says
which taxonomy produced it, an ADX-era overlay simply fails to match
under volatility mode instead of being misapplied, and a census run under
either mode is self-describing.

The cost is real and worth naming, because widening a shared enum is
exactly the kind of change that breaks things quietly:

- **Code that enumerates `MarketRegime` now sees nine values.**
  `test_market_regime.py::test_get_strategy_weights` looped over the
  whole enum asserting every regime has overlay weights, and the ADX
  detector legitimately has none for a taxonomy it never emits. The loop
  now iterates an explicit `ADX_REGIMES` tuple, and a new test asserts
  the ADX detector returns `{}` / `[]` for foreign regimes rather than
  guessing.
- **`strategy_manager`'s two hardcoded grid-regime lists were replaced
  with `regime_detector.is_grid_allowed(regime)`**, because a membership
  test against ADX names is silently False under any other taxonomy.
  **For the ADX detector that method returns True for exactly
  `RANGING_CALM`, `RANGING_VOLATILE` and `INDECISIVE`, which is what both
  lists said**, so ADX-mode behaviour is unchanged and a test pins it.
  The delegation did break one test: `test_signal_routing.py`'s mock
  detector answered "yes" to everything, which would have silently
  disabled its grid-gating assertions. The mock now delegates
  `is_grid_allowed` to a real detector.
- **Eight `patch("trading_bot_v2.trading_bot.MarketRegimeDetector")`
  call sites stopped stubbing anything** once the construction site moved
  to `make_regime_detector`. They now patch the factory. Nothing failed
  when they were silently no-ops, which is the point: a stub that stops
  stubbing does not announce itself.

---

## What must be re-run if this is adopted

Switching `REGIME_MODE` re-gates every strategy on every symbol. It
invalidates, completely:

- The **2026-07-28 8-year campaign** - every PF, Sharpe, PSR, DSR and
  gate verdict, because every strategy is regime-gated and the gate
  changed.
- **`docs/REGIME-CENSUS.md` in full** - every cell, every trade count,
  every per-regime PF, and the tunable/thin/none classification. The
  regime axis itself is different: 30 cells become 18 (6 strategies x 3
  regimes), and trade attribution changes because the regime confirmed
  at entry changes.
- **Every stored regime overlay** (`regime_param_overlays`). They are
  keyed by regime value, so ADX-era rows will not match VOL-era regimes -
  they will silently stop applying rather than misapply, which is the
  intended failure mode, but it means every overlay must be re-derived.
- **Every per-regime optimization study** in
  `optimization_studies.db`. Its trials were selected against a
  different partition of the tape.
- **`docs/tuning-log.md`** entries that attribute an outcome to regime
  distribution, and **`docs/REGIME-DISCRIMINATION.md`'s** Task 2
  decomposition, which decomposes ADX mechanisms that no longer run.
- **The ML shadow comparison** (`analysis/regime_shadow_report.py`): it
  compares an ML classifier against the ADX label, and the
  `regime_shadow` table's `adx_regime` column would be storing a
  volatility regime.

Re-running means: the census (~8 minutes), then the validation campaign,
with `--end` pinned and `DATA_AUTODOWNLOAD=false` (see
`docs/FOLLOW-UPS.md` item 8e - without both, "the same" campaign
legitimately returns different numbers).

**Nothing above needs re-running for this change**, because
`REGIME_MODE` defaults to `adx` and no ADX-path behaviour moved.

---

## What this does not fix

1. **The gate is still the expensive part.** The earlier study's third
   conclusion stands: the label's real cost is not that it is slightly
   wrong, it is that it *forbids* strategies from trading most of the
   tape on the strength of a small effect. A better label makes the
   forbidding better targeted; it does not make forbidding cheap.
2. **It is still not a direction signal**, and the mapping above still
   assigns directional strategies to a bucket chosen by a sign-blind
   measure. `VOL_HIGH` says "big moves are coming", not "up".
3. **The roster problem is untouched.** The census's finding that the
   strategy roster is aimed at the quiet fifth of the tape while three
   fifths trend is a strategy-selection problem, and re-partitioning the
   tape does not add a strategy.
4. **The bar set for the ML shadow track is unchanged.** Any replacement
   classifier should still clear trailing realized volatility's
   `rho^2` of ~0.16-0.29 on forward volatility. This scheme does not
   clear it either - it clears the ADX label, which is a lower bar.
