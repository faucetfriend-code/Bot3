# VWAP from scratch: what the signal actually does

**Run date:** 2026-07-29. **Design slice: BTC-USDC 15m, 2018-01-01 .. 2023-12-31
(209,804 bars).** 2024-2026 and ETH/SUI are holdout and were not touched.
Reproduce with `scripts/analysis/vwap_signal_study.py`.

This measures the *signal* - conditional forward returns and excursion
distributions - instead of running a strategy and reading one profit factor off
the end. Session-anchored VWAP (00:00 UTC reset), not the shipped rolling one.

**Verdict: one candidate worth exactly one holdout test, and four structural
findings that invalidate the shipped design regardless of whether it passes.**

---

## The four structural findings

These do not depend on the candidate surviving, and each contradicts something
`vwap_scalping.py` does today.

### 1. Longs and shorts are not symmetric. Shorts are unconditionally bad.

Fading a deviation *above* VWAP loses at every threshold and every horizon:

| horizon | long (bp) | short (bp) |
|---|---|---|
| 15m | +2.25 | -0.15 |
| 1h | +7.65 | -1.00 |
| 4h | +15.79 | -6.27 |
| 8h | +15.06 | -13.86 |
| 1d | +14.65 | **-31.83** (t = -2.63) |

*(deviation_sd >= 2.0, gross, reversion direction)*

Every cell of the short-side stop/target grid is negative, -12 to -19 bp, with
no structure. The shipped strategy trades both sides with identical logic.
**About half its trades - 7,742 of the 14,817 setups at 2.0 SD - are a side
with no edge at any horizon.**

### 2. The path is adverse-skewed, so the setup cannot be traded with a stop

Mean maximum favourable excursion is *smaller* than mean maximum adverse
excursion at every horizon:

| horizon | MFE bp | MAE bp | MFE/\|MAE\| |
|---|---|---|---|
| 15m | 33.4 | -37.2 | 0.90 |
| 1h | 60.8 | -66.8 | 0.91 |
| 4h | 110.0 | -117.9 | 0.93 |
| 8h | 151.4 | -161.5 | 0.94 |
| 1d | 254.7 | -282.9 | 0.90 |

Price goes further against these entries than for them, before it comes back.
So a stop is hit first more often than a target, and the 5x5 grid confirms it -
**every cell is negative, and it improves monotonically as the stop widens**:

| stop \ target (sigma) | 0.25 | 0.5 | 1.0 | 1.5 | 2.0 |
|---|---|---|---|---|---|
| 1.0 | -16.68 | -13.33 | -13.38 | -13.51 | -13.91 |
| 2.0 | -12.32 | -8.92 | -8.27 | -8.08 | -8.97 |
| 3.0 | -10.36 | -7.31 | -6.28 | -5.44 | -6.07 |
| 5.0 | -9.30 | -4.95 | -3.85 | -2.82 | -2.96 |
| 8.0 | -8.76 | -4.46 | -2.71 | **-1.40** | -1.55 |

*(long side, deviation_sd >= 2.0, 4h, net of 14.1 bp, same-bar ties = stop)*

The trend runs all the way to "8 sigma" - about 4% away, i.e. no stop at all -
and still does not reach zero. This says the edge lives in the **terminal
return under a time-based exit**, and that any protective stop destroys it.
`vwap_scalping.py` is built the opposite way: an ATR stop plus a VWAP target.
This is the same finding as the 1m-ATR stop defect, but deeper - it is not that
the stop was the wrong *size*, it is that a stop is the wrong *mechanism*.

### 3. High volume kills reversion

| relative volume | n | mean bp | t |
|---|---|---|---|
| 0 - 0.75 | 1,060 | +9.93 | 1.64 |
| 0.75 - 1.0 | 1,468 | +9.80 | 2.26 |
| 1.0 - 1.5 | 3,139 | +11.76 | 2.03 |
| 1.5 - 3.0 | 5,381 | +0.68 | 0.38 |
| 3.0+ | 3,763 | -0.89 | -0.04 |

*(both sides pooled, deviation_sd >= 2.0, 4h, vs trailing 1-day median volume)*

Deviations on normal volume revert. Deviations on heavy volume are information,
and they continue. The cut is sharp and it lands at about 1.5x. **The shipped
strategy has no volume filter**, so it trades both populations identically -
and more than 60% of its setups fall in the buckets with no edge.

Filtering to `rvol < 1.5` also flips the short side positive at short horizons
(15m +2.54 bp at t=3.39, 1h +4.45 at t=2.07), which is what confirms the
mechanism: it was heavy-volume continuation that made shorts look hopeless.

### 4. It cannot work as scalping

At 15m the pooled signal is **+2.78 bp gross at t = 5.18** - the strongest
t-statistic anywhere in this study, and completely untradeable against a
14.1 bp round trip. The edge only becomes larger than its own cost somewhere
between 2h and 8h.

**"VWAP scalping" is a category error.** Whatever is here is a multi-hour
hold. The name, the 8-minute cooldown, the 1m ATR and the 5m MACD trigger all
encode a timescale the signal does not have.

---

## The candidate

Long only, `deviation_sd >= 2.0`, `rvol < 1.5`, 8-hour time-based exit, no stop:

| | value |
|---|---|
| setups | 2,685 |
| gross | **+21.88 bp** |
| net of 14.1 bp | **+7.78 bp** |
| win rate | 58.8% |
| t (de-overlapped, n=1134) | **1.90** |

Per calendar year, against that year's own drift:

| year | n | gross bp | drift bp | **excess bp** | t | net bp |
|---|---|---|---|---|---|---|
| 2018 | 503 | 31.16 | -8.44 | **+39.61** | 0.23 | +17.06 |
| 2019 | 367 | 10.52 | 8.08 | +2.44 | 1.05 | -3.58 |
| 2020 | 409 | 37.50 | 14.79 | +22.71 | 2.39 | +23.40 |
| 2021 | 435 | 20.78 | 7.47 | +13.31 | 0.90 | +6.68 |
| 2022 | 582 | 7.83 | -7.72 | +15.55 | -0.41 | -6.27 |
| 2023 | 389 | 26.44 | 9.47 | +16.97 | 1.29 | +12.34 |

**Excess over drift is positive in all six years**, and the largest excess is
2018 - a bear year with -8.44 bp of drift - which is the main evidence that
this is not "buy the dip in a bull market" wearing a costume. Net of costs it
is positive in four of six.

Compare the unfiltered 4h version, which is net positive in only **two** of
six years, both of them 2020-2021. The volume filter and the longer hold are
what move it.

## Why this is a candidate and not a finding

**The configuration was chosen after looking at these tables.** Side, threshold,
horizon and the volume cut were all selected from the output above. Counting
generously the study looked at ~200 cells (105 in the conditional table, 12
hour buckets, 5 volume buckets, 50 grid cells, 2 anchor modes). The nominated
configuration's de-overlapped t is **1.90**. That does not survive any
multiplicity correction - it would not survive Bonferroni against 10 tests,
let alone 200.

What is worth something is the *coherence*: monotone in threshold, monotone in
horizon, positive excess in all six years including both bear years, and a
mechanism (heavy volume means continuation) that predicted the short-side flip
before it was measured. Coherence is not a p-value.

**Also not trustworthy:** the hour-of-day table. Twelve buckets, best t = 2.18,
which is what the maximum of twelve draws looks like under the null. Do not
build a session filter on it.

## What to do next, in order

1. **Test the candidate on the holdout, once.** BTC 2024-01-01..2026-07-01,
   then ETH and SUI. One test, pre-registered as: long only, `deviation_sd >=
   2.0`, `rvol < 1.5`, 8h time exit, no stop, net of 14.1 bp. If it does not
   clear zero net, close the file.
2. If it survives, **build it as a new strategy**, not as parameters on
   `vwap_scalping.py`. It shares an indicator and nothing else: no stop, a
   time-based exit, one side, a volume gate, and a multi-hour hold.
3. Do not re-tune on the design slice first. The budget for this idea is one
   holdout test, and spending it on a configuration refined further in-sample
   wastes it.

## Prior

Mine is that it fails the holdout. +7.78 bp net per trade is thin, the t is
1.90 after ~200 looks, and two of six design-slice years are already negative.
The four structural findings are the durable result of this study; the
candidate is a lottery ticket that costs one clean test to settle.
