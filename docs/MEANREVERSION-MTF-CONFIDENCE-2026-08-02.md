# MeanReversion MTF confidence term: the defect, the three arms, the verdict

**Date:** 2026-08-02. **Branch:** `retune-measurement-hardening`.
**Subject:** `trading_bot_v2/strategies/mean_reversion.py`, the 30%-weighted
`mtf_alignment` term in the confidence blend.

## 1. The defect

The confidence blend advertised three terms:

```
confidence = 0.4*rsi_strength + 0.3*bb_proximity + 0.3*mtf_alignment
```

`mtf_alignment` was computed from a value the code called `rsi_1h`. That value
was never a 1h RSI. `generate_signals` passed `rsi_1h=trigger_rsi`, and
`trigger_rsi` is the 5m RSI when execution data is supplied and the 15m RSI
otherwise. The 1h slice of `multi_tf_data` was never read at all - `"1h"`
appeared exactly once outside comments, in a docstring.

Two distinct failure modes followed:

- **No 5m data (live fallback, and every unit test):** `mtf_alignment` and
  `rsi_strength` are the *same expression on the same input*, so the blend
  silently collapses to `0.7*rsi_strength + 0.3*bb_proximity`. The shipped
  debug log shows it directly: `rsi_strength=1.000*0.4=0.400 ...
  mtf_alignment=1.000*0.3=0.300`.
- **With 5m data (the backtest path):** the term is the 5m RSI wearing a 1h
  label. RSI then carries 70% of total confidence weight across two
  timeframes, neither of which is 1h.

Separately and under either fix, the value was **mislabelled on the way out**:
`signal.indicators["rsi_1h"]` and the `notes` string `RSI=x/x` both claimed 1h
data. Those are persisted - `signal_logger` hands `indicators` to
`database.py:2992`, which stores it as a JSON blob in the `signals` table.

The short path (`_create_short_signal`) carried the mirror image of all of it.

## 2. Which fix the history supports

Git cannot answer this: the file's entire history is one squashed import
commit (`a03a75f`), and `git log -S` on every relevant string returns only
that commit. The answer is in the repo's prompt specs instead.

- **`prompts/003-strategy-timeframe-separation.md`** is the verbatim source of
  the live file's `# REMOVED: Multi-TF RSI alignment requirement` comments and
  the "confluence deadlock" phrase. It is written *entirely* in the language of
  gates and entry conditions - "requirement", "gate", "entry", "Don't delete
  the code, but comment it out". The word `confidence` never appears in it.
  The executor did exactly what was asked: commented out the two gate lines
  (which still carry the dictated `# DISABLED: TF separation` marker) and left
  the signal builders untouched.
- **`prompts/058-signal-threshold-reduction.md`**, written *later*, explicitly
  designs MTF-as-soft-confidence-input: "Multi-TF alignment now affects
  confidence, not permission", a `MTF_ALIGNMENT_BONUS`, and helpers computing
  genuinely separate 15m and 4h RSIs. **None of it was ever implemented** -
  `MTF_ALIGNMENT_BONUS` and `_check_mtf_alignment` appear nowhere in
  `trading_bot_v2/`. Only 058's other idea landed, and the live file cites it
  by name: `# Prompt 058: Confidence affects SIZE, not permission`.

The BTV2 archive is silent: no MR ancestor computes a confidence score at all
(zero hits for `confidence` across all nine `mr_*` ancestors), and every `1h`
in there is a `resample("1h")` base-bar idiom, not a higher-timeframe overlay.
Despite its name, `BTV2/mr_analyze_alignment.py` is about *trend* alignment
(trade direction vs SMA-200), not multi-timeframe RSI agreement.

**Conclusion.** The surviving term is vestigial, but *not deliberately*
vestigial - it is dead weight created by an unnoticed side effect of a
gate-focused edit. Reading (b) in its strong form ("abandoned as a bad idea")
is not supported: no artifact records a negative finding about 1h RSI as a
confidence input, and the author still wanted that design afterwards. Of the
two, the repo's own most recent stated intent points at **(a)**. What is
*definitely* unsupported is keeping the collapsed `0.7/0.3` blend and calling
it intentional.

## 3. What was actually changed

The mislabelling is fixed **unconditionally** - it is a bug under every
reading. The blend is now selectable via `MEAN_REVERSION_MTF_CONFIDENCE` so
the alternatives could be measured rather than argued:

| mode | blend |
|---|---|
| `legacy` (default) | `0.4*rsi_strength + 0.3*bb_proximity + 0.3*mtf(trigger_rsi)` - the historical, defective form |
| `rsi_1h` | same weights, but `mtf` reads a genuine 1h RSI computed from `multi_tf_data["1h"]` |
| `off` | `(4/7)*rsi_strength + (3/7)*bb_proximity` - term dropped, surviving 4:3 ratio renormalised |

`legacy` was verified bit-identical to the pre-fix code: 600 signal pairs
generated from the committed `HEAD` version and the new one, with and without
5m execution data, produced **zero mismatches** in confidence, side, stop and
target.

Indicator payload, both directions:

- `rsi_1h` is emitted **only when a real 1h RSI was computed**, never as an
  alias for the trigger.
- `rsi_trigger` and `rsi_trigger_tf` are new and name their own source.
- `notes` now reads `RSI_15m=..., RSI_5m=...` instead of `RSI=x/x`.

## 4. The campaign

Methodology reproduced from `docs/CAMPAIGN-2026-07-30.md` exactly - same
symbols, window geometry, anchor, funding model and lookback; only
`--strategies mean_reversion` was added and the blend varied:

```
python -m trading_bot_v2.validation.runner --strategies mean_reversion \
  --symbols BTC-USDC,ETH-USDC,SUI-USDC --windows 12 --window-months 2 \
  --window-mode spread --span-years 6 --anchor-end 2026-07-01 --once
```
with `BACKTEST_FUNDING_MODEL=historical`, `BACKTEST_HISTORY_LOOKBACK=100`,
`VWAP_STOP_SOURCE=atr_structure`, `DIRECTIONAL_GATE=enforce` (the standing
recommendation). 36 windows, 629,014 bars pooled.

### Results

| arm | verdict | trades | PF | PSR | DSR | consistent |
|---|---|---|---|---|---|---|
| `legacy` (current behaviour) | FAIL | 971 | 1.06 | 0.7728 | 0.0000 | 2/3 |
| `rsi_1h` (fix a) | FAIL | 971 | 1.06 | 0.7728 | 0.0000 | 2/3 |
| `off` (fix b) | FAIL | 971 | 1.06 | 0.7728 | 0.0000 | 2/3 |

**The three arms are identical. Not close - identical.** The check is stronger
than the pooled row: all **36 per-window results match exactly** across the
three arms - same final equity to the cent, same return, Sharpe, max drawdown
and trade count in every window on every symbol.

That is not a null result to shrug at; it is a structural fact about the
harness, and it was predicted from the code before the campaign was run, then
confirmed:

1. MeanReversion is admitted only in `RANGING_CALM`
   (`market_regime.py:1026`). The campaign confirms it: **100% of the 971
   trades entered in `ranging_calm`.**
2. The only thing that reads `signal.confidence` on that path is
   `StrategyManager._apply_regime_confidence_gate`
   (`strategy_manager.py:1597-1644`). Its threshold is the global
   `MIN_SIGNAL_CONFIDENCE_FLOOR` (`0.0`) plus a per-regime adjustment that is
   non-zero only for `INDECISIVE` and `RANGING_VOLATILE`. In `RANGING_CALM`
   the threshold is `0.0`, and the method returns early on `threshold <= 0`.
3. Backtest position sizing is fixed-fractional and never reads confidence
   (`engine.py:1202-1207`). `signal.quality` - the other confidence-derived
   field - appears **zero** times in `engine.py`.

So confidence is inert in the backtest for this strategy, and no campaign of
any length or width could have separated these arms. Running it was still
worth it: it converts "I think this is unobservable" into "it is unobservable,
here is the trade-identical evidence", and it re-established the baseline
(971 trades / PF 1.06 against the documented 985 / 1.02 - the small drift is
expected, since that number predates the per-fold seeding fix `6db2d39` and
the study-seeding fix `db30c6c`, and the data store has advanced).

**Where the fix does bite is live, not backtest.** In the live path confidence
feeds `ConfidenceSizer` (`signal_phases.py:373`), which scales position size.
The blend change is therefore a real change to live risk-taking that the
backtest harness structurally cannot score.

### Confidence distribution (what actually differs)

Same 468 signals under all three blends on BTC-USDC 2024-01-01..2024-03-01,
but very different confidence:

| mode | n | p10 | p25 | median | p75 | p90 | mean |
|---|---|---|---|---|---|---|---|
| `legacy` | 468 | 0.235 | 0.322 | 0.447 | 0.564 | 0.712 | 0.458 |
| `rsi_1h` | 468 | 0.000 | 0.124 | 0.288 | 0.483 | 0.617 | 0.307 |
| `off` | 468 | 0.261 | 0.368 | 0.517 | 0.657 | 0.790 | 0.519 |

`rsi_1h` shifts confidence **down** (a real 1h RSI is usually not at an
extreme, so it withdraws the support the duplicated term was fabricating);
`off` shifts it **up**. Under live sizing those are materially different
position-size profiles.

## 5. ADOPTED_PARAMS

`ADOPTED_PARAMS` in `trading_bot_v2/optimization/monthly_retune.py` carries
`min_confidence` 0.5228 (`vol_low:trend`) and 0.5375 (`vol_mid:trend`).

**Recommendation: leave them unchanged.** Two independent reasons.

**First, the pre-registered rule.** Params change only on out-of-sample
evidence. The campaign produced *no* evidence distinguishing the arms, so
there is nothing to justify a change. "Leave them and re-tune next month" is
the correct answer here.

**Second - and this outranks the first - `min_confidence` is currently inert
for mean_reversion.** `self.min_confidence` is assigned in `__init__` and then
referenced only inside two debug f-strings. There is no
`if confidence < self.min_confidence: return None`, unlike `ma_crossover.py:687`
and `funding_arb.py:325` which do enforce theirs. The only confidence gate on
this path is the global regime gate above, which never binds in
`RANGING_CALM`. So those two adopted values do not currently affect anything,
and Optuna selected them over a dimension that cannot change an outcome. This
is logged as a separate follow-up task; it should not be fixed silently inside
a confidence-formula change, because wiring it would itself move trade counts.

**If it were wired**, the thresholds would need translating, because the blend
moves the distribution under them. Selectivity-preserving equivalents (the
value admitting the same fraction of signals the adopted value admits today),
measured on the distribution above:

| adopted (legacy) | admits | equivalent under `rsi_1h` | equivalent under `off` |
|---|---|---|---|
| 0.5228 | top 32.5% | **0.4115** | **0.6151** |
| 0.5375 | top 29.3% | **0.4415** | **0.6367** |

(The method self-checks: applied to `legacy` it returns 0.5232 and 0.5382
against the adopted 0.5228 and 0.5375.) Treat these as indicative only - one
symbol, one 2-month window, ADX regime labels rather than the volatility
composite states those params actually belong to.

**No ADOPTED_PARAMS value was edited.**

## 6. Knock-on: stored signals are not comparable

Every MeanReversion signal already in `trading_bot.db` was produced under the
legacy blend, and its stored `indicators.rsi_1h` holds the **trigger** RSI
(5m or 15m), not 1h. Consequences:

- Any analysis that read `rsi_1h` off historical MeanReversion signals was
  reading 5m or 15m data under a 1h name.
- Stored `confidence` values predate the mislabelling fix and, if the default
  blend is ever changed, will not be comparable to new ones.

Rows are not being rewritten. New signals are distinguishable by the presence
of the `rsi_trigger` / `rsi_trigger_tf` keys, which no legacy row has.

## 7. Recommendation

1. **Ship the mislabelling fix** - unconditional, no behavioural effect on the
   backtest, and correct under every reading.
2. **Keep `legacy` as the default blend for now.** It is the only arm with
   measured behaviour, the campaign gives no grounds to prefer another, and
   switching would change live position sizing on zero evidence. This is the
   same discipline the repo applies elsewhere.
3. **`rsi_1h` is the candidate to adopt** if and when confidence becomes
   measurable - it is what the code always claimed and what `prompts/058`
   intended. Adopting it needs a harness where confidence can affect an
   outcome (wire `min_confidence`, or raise `MIN_SIGNAL_CONFIDENCE_FLOOR`, or
   admit MeanReversion to regimes carrying a non-zero adjustment).
4. **Do not treat `off` as "the safe minimal fix".** It is the one option with
   no supporting artifact, and it shifts confidence *up*, which under live
   sizing means systematically larger positions.

**mean_reversion still FAILS the gate** (PF 1.06, DSR 0.0000, consistent 2/3),
exactly as it did before this work. Nothing here rescues it, and nothing here
makes it worse.

## Reproduce

Logs: `G:\Candle Data\Temp Test holding\mr-mtf-2026-08-02\`
(`campaign_{legacy,rsi_1h,off}.log`, `conf_dist.log`).
