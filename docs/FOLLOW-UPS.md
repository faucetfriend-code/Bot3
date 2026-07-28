# Deferred follow-ups

Items identified 2026-07-28 that were deliberately NOT acted on, pending the four
major fixes then in flight (execution-layer signal loss, out-of-sample discipline
in sweeps, multi-year validation windows, frequency-calibrated promotion gate).

Revisit once those land. Ordered roughly by expected value.

---

## 1. Cost model is a single global constant, and probably pessimistic

`trading_bot_v2/backtesting/cost_model.py` hardcodes `slippage_pct=0.002` and
`taker_fee_pct=0.0006`, giving a round-trip cost of 0.32%.

Twenty basis points of slippage is high for liquid BTC perps — plausibly by an
order of magnitude. If it is overstated, then marginal strategies are better than
they currently appear: liquidation capture graded PF 1.05–1.14, which is exactly
the band where a cost assumption decides pass or fail. SUI is thinner and could
go the other way.

The cost is also identical for every symbol and every order type, and the model
does not distinguish maker from taker fills.

**Worth doing:** make costs per-symbol, distinguish maker/taker, and calibrate
against real fill data. Note the caveat — the live execution rate was ~1.2%
because of the Pacifica per-key IP whitelisting problem, so there may not be
enough real fills yet to calibrate from. The Blofin adapter path may be the
better source.

**Why it matters:** every profit factor in every result is a function of this
constant, and nobody has checked it against reality.

## 2. Phase 4 of the observability plan: gate-metric calibration

See `docs/OBSERVABILITY-PLAN.md`. Phases 0–3 are implemented; 4, 5 and 6 are not.

Phase 4 is the only remaining defense against the VWAP class of bug — a threshold
configured above the mathematical ceiling of the metric it gates on. Everything
built so far catches failures *downstream* of signal generation; this catches the
configuration being impossible in the first place.

It needs a `describe_gate_metrics()` hook per strategy plus a calibration pass
recording each gating metric's observed distribution to a committed JSON artifact.
One hook pays for three features: the calibration histogram, the near-miss
gradient term in trial scoring, and the "binding constraint" line in reports.

Phase 5 (persistence of diagnostics into `validation_runs` / the API / a dashboard
panel) and Phase 6 (search-space reparameterization) are lower value — the CLI and
funnel already deliver most of the benefit. Note `interface.html` does not consume
the validation API at all today, so a dashboard panel is new UI work rather than
an edit.

## 3. Audit parameter provenance — BTV2 Monte Carlo verdicts are near-tautological

`BTV2/regime_aware_validation.py::monte_carlo_validate` permutes trade ORDER over a
fixed trade set. Cumulative return is invariant under reordering, so its
"P(Loss)=0%, ROBUST" verdicts carry far less information than they appear to.

Many current `.env` parameter values cite exactly that provenance. Two such claims
have already collapsed under inspection:

- VWAP's "failed all 7 OOS years, do not re-enable" measured a strategy whose
  entry threshold made it structurally incapable of trading.
- MA crossover's "walkforward validated, 45 trades, Sharpe +0.696" came from
  `BTV2/strategies.py::run_ma_crossover`, a *different implementation* that never
  had the rolling-window bug. It is not evidence about `trading_bot_v2`.

**Worth doing:** go strategy by strategy and record, for each parameter, where its
value actually came from and whether that source measured this codebase. Anything
whose provenance is a BTV2 artifact or a pre-fix run should be treated as
unvalidated rather than validated.

## 4. Portfolio-level validation

Every strategy is validated in isolation, but they run together. Conflict
resolution, correlated drawdowns, and shared exposure limits are unmeasured. A set
of individually-acceptable strategies can still be a bad portfolio — particularly
the overlays, which run in all regimes alongside whatever the regime-mapped
strategy is doing.

## 5. Grid trading's config contradiction

`.env` says `DISABLED: Regime-aware walkforward (2018-2025): Sharpe -0.294,
P(Loss)=100%, FRAGILE ... Do not re-enable`, while `ENABLE_GRID_TRADING=true` —
and grid was the best performer in the 2026-07-28 campaign (SUI PF 1.60, PSR 0.98;
BTC PF 1.31).

Given items 3 above, that old verdict deserves the same scrutiny the VWAP and MA
crossover claims got. Either it measured something broken, or the 2026 window
flatters grid. Both are worth knowing, and they point in opposite directions.

## 6. Two timing-sensitive tests cry wolf

`test_profiler_benchmark.py::TestPerformanceBenchmark::test_check_regression` and
`test_diagnostics.py::TestFunnelOverhead::test_overhead_under_three_percent` both
fail intermittently under full-suite load and pass in isolation. The first names a
different "regressing" benchmark on each run.

They are measuring real properties worth keeping, but as written they train the
reader to ignore failures. Either mark them so they are excluded from the default
run, or make them robust to load (more iterations, median rather than mean,
wider tolerance under contention).

## 7. Duplicate backtesting guide

`research/BACKTESTING_GUIDE.md` is a separate, generic, AI-written document
containing invented API references. The real guide now lives at
`docs/BACKTESTING_GUIDE.md`. The duplicate is confusable and probably should be
deleted, but it was left alone pending a decision from the owner.

## 8e. Campaign runs are not reproducible unless the end date is pinned

The regime census reported grid_trading yielding 578 / 539 / 516 trades across three
"identical" campaign runs. This is NOT engine nondeterminism — a single-window grid
backtest reproduces exactly (verified: 17 trades, PF 1.79, three consecutive runs).

The cause is that the data moves underneath the run. `DATA_AUTODOWNLOAD` defaults on and
tops the 5m store up to *now*, and the validation runner anchors its newest window to the
coverage end. Observed within one day: the 5m trailing edge advanced from ~11:35 to 17:40.
So consecutive campaign runs evaluate a slightly different final window, and the
highest-frequency strategy shows the largest swing.

Consequences:

- **Any campaign whose results you intend to compare must pin `--end`**, or run with
  `DATA_AUTODOWNLOAD=false`. Otherwise re-running "the same" campaign legitimately gives
  different numbers and it looks like a bug.
- The 2026-07-28 8-year campaign did not pin `--end`, so its exact trade counts are not
  reproducible. The verdicts are robust (they do not turn on a handful of trades in one
  window) but the counts are not.

Worth doing: have the runner record the resolved anchor and the store's trailing edge in
`validation_runs`, so a result carries the data state that produced it. Consider defaulting
campaigns to a pinned anchor with an explicit `--to-now` opt-in.

## 8a. Calibration's hard criterion should probably not be `max`

Phase 4 shipped (commit 9cbc86b) and works, but calibrating over 2.5 years qualified the
rule this plan specified. The original VWAP investigation measured a 6-month window and
found `deviation_sd` maxing at 3.95, which made the configured 4.037 look mathematically
unreachable. Over 2.5 years the extreme tail actually reaches **4.16-4.56** across all
three symbols. So against the shipped artifacts, 4.037 produces a **warning, not a
prune** — the `max`-based hard rule as designed would NOT have caught the original bug
on a long calibration window.

What does catch it is the p99 warning: 4.037 sits far above p99.9 (~3.1). The threshold
is not literally unreachable, it is merely useless — it would fire a handful of times in
years.

Consider changing the hard criterion to `p999`, or better, to a minimum absolute
**pass count** over the calibration window (e.g. "fewer than N bars in 2.5 years could
ever pass this gate"), which expresses the thing we actually care about rather than a
proxy for it.

Two live thresholds worth watching, from the same calibration:

- **`momentum` `rrr` = 1.53 against `min_rrr` = 1.5 is the tightest margin in the
  system**, and because RRR is a constant (`atr_target_mult / atr_stop_mult`) there is no
  distribution — every signal sits exactly 0.03 above its gate. Dropping
  `MOMENTUM_ATR_TARGET_MULTIPLIER` from 3.825 to 3.75 puts it at exactly 1.50, where
  float representation (`1.4999999999999998`) starts failing a `>=` — the precise failure
  that silently discarded all 136 signals in the 2026-07-28 campaign.
- **`bars_since_cross` has a structural ceiling of 29**, not a market-derived one: with
  `BACKTEST_HISTORY_LOOKBACK=60` and `slow_ma_period=30`, the crossover bar scrolls out of
  the window after 29 bars. The search space caps `max_entry_bars` at 24 so it fits today,
  but raising either without raising the lookback truncates silently.

Also unhooked (one line each, in a file that agent did not own): `near_miss()` is
implemented but nothing calls it from `optuna_runner`, and `calibration_warnings()` is not
attached to trial `user_attrs`.

## 8b. Config bugs surfaced by the provenance audit (2026-07-28)

Found while auditing, reported not fixed. See `docs/PARAMETER-PROVENANCE.md`.

**`api_server.py:281-290` hard-codes five strategy enable flags**, ignoring `.env`
entirely:

```python
enable_mean_reversion=True, enable_ma_crossover=True,
enable_trend_following=False, enable_grid_trading=True,
enable_liquidation_capture=True,
```

They currently coincide with `.env`, so nothing diverges today — but setting
`ENABLE_GRID_TRADING=false` or `ENABLE_MA_CROSSOVER=false` in `.env` would have **no
effect on the live bot**. This is the config-that-looks-like-it-works failure mode that
produced three separate silent-strategy bugs already. Fix before relying on any enable
flag to disable something.

Also from the same audit:

- `GRID_MAX_POSITIONS` is dead config — the manager passes
  `GRID_MAX_POSITIONS_PER_SYMBOL`.
- `strategy_manager` env defaults silently override constructor defaults for all 10
  LiquidationCapture params, grid levels/spacing, and momentum confidence. A caller
  passing an explicit value can be overridden by an env default it never set.
- Four parameters (ORB, CalendarFlow) have no env path at all.
- In BTV2: `test_regime_aware_vwap.py` reads the wrong metric keys, so every Sharpe in
  `regime_vwap_comparison_2024.csv` is literally `0`; zero-trade folds are averaged in
  as neutral `sharpe 0.0`; `baseline_sharpe = -0.12` is hardcoded; `optimize_strategy`
  swallows all exceptions.

## 8c. RiskManager is bypassed in every backtest (found 2026-07-28)

`CLAUDE.md` states RiskManager is AUTHORITATIVE for position sizing and exposure, and
that other components delegate to it. In backtests it does not:
`BacktestEngine._execute_signal` sizes from `signal.quantity`, falling back to a flat
`max_risk_per_trade` (2%) fraction of balance. `RiskManager` is constructed and handed
to `StrategyManager`, but `max_portfolio_exposure_pct` is never consulted on the sizing
path, and only GridTrading asks it for capital.

Measured consequence: in a portfolio run the largest single fill was $221 against a
$1,500 notional cap, and max concurrent positions was 1 — the limits never bound, so
they are untested. **Every backtest number this project has produced used sizing that
does not match live behaviour.** Until this is closed, backtest returns and drawdowns
are not directly comparable to what the live bot would do.

## 8d. Portfolio capacity is the binding constraint, not correlation

From `trading_bot_v2/validation/portfolio.py` (new): running six strategies together on
one symbol is materially WORSE than the equal-weight blend of running them alone (mean
return edge -0.73%, drawdown edge -1.04%).

The cause is not correlated bets — mean pairwise correlation is +0.04, i.e. these
strategies are close to independent and real diversification IS available. The
portfolio cannot collect it because there is effectively **one position slot per
symbol, occupied 69% of the time**, with six strategies competing for it. Retention
alone-to-portfolio ran 6-71%: mean_reversion kept 4 of its 68 trades, ma_crossover was
shut out entirely, grid_trading won 382 conflict resolutions. `exec:hedge_mode_block`
more than doubled (552 vs 262 across isolated runs).

So the live system is close to "grid_trading plus whatever fits in the gaps" rather
than a diversified portfolio. Worth deciding deliberately: raise capacity (more
concurrent positions, per-strategy slots, or per-strategy capital allocation), or
accept that most enabled strategies are decorative.

Related engine hooks that would sharpen this measurement, all small:
`_execute_signal`'s six `funnel.reject(REASON_EXEC_*)` calls do not pass `strategy=`,
so execution-stage crowd-out cannot be attributed per strategy;
`run(strategy_filter=...)` accepts only a single strategy, so portfolio subsets must be
selected via `ENABLE_*` environment scoping; and `strategy_manager` counts conflict
DROPS but not conflict INVOCATIONS.

## 9. Campaign results live only in an uncommitted database

`validation_runs` and `trial_registry` do not exist in any committed `trading_bot.db` —
they exist only in the working-copy DB. The 2026-07-28 campaign verdicts are therefore
not reproducible from a fresh clone, and an agent auditing from a worktree cannot see
them. Decide whether validation history should be an artifact (exported JSON/CSV
alongside the DB) rather than living only in a gitignored-in-practice binary.

## 10. Regime census findings not yet acted on (2026-07-28)

See `docs/REGIME-CENSUS.md` for the full table and the audit of the regime
machinery. Fixed there: 11 silent no-op search-space parameters, one phantom
parameter, `--regime` now composes with `--chunked`, the regime min-trades is
derived rather than the legacy 15, and infeasible overlays are rolled back.
Left open deliberately:

**a. `grid_trading` is not reproducible.** Three runs of identical code over
identical windows gave 578 / 539 / 516 closed trades (12% spread). Grid
lifecycle state is in-memory. Every grid number this project has produced,
including the campaign's PF 0.90, is approximate until this is explained. It
is also the only strategy where this happens - the other five reproduce to
the trade.

**b. Two regime mappings lose money and could just be removed.** This costs
zero search budget, so unlike tuning it deflates nothing:
- VWAPScalping in RANGING_CALM: 1246 trades at PF 0.65 (its INDECISIVE
  subset is PF 1.05 on 337 trades). `strategy_manager.py` `_vwap_regimes`.
- GridTrading in RANGING_CALM: 440 trades at PF 0.85, against PF 1.06 in its
  nominal RANGING_VOLATILE. `market_regime.py:934`. Only 76 trades remain
  after removal, so this one needs validating rather than assuming.

**c. CLAUDE.md's regime-to-strategy table does not match the code.**
`market_regime.py` maps GridTrading into RANGING_CALM and MACrossover into
TRENDING_MODERATE, and `strategy_manager.py` adds VWAPScalping in three
regimes and LiquidationCapture/FundingArb in all of them. None of that is in
the CLAUDE.md table.

**d. The roster is aimed at the wrong fifth of the tape.** trending_strong is
59.9% of bars and produced 360 trades in eight years; ranging_calm is 21.6%
and produced 2401. That is a strategy-selection problem no parameter change
addresses.

**e. `run_regime_optimization.py` is still in-sample only**, and its
`--walk-forward` does not refit per fold (`OptimizationAdapter.
run_walk_forward` just skips the first `train_months`). Either point it at
`optimize_chunked(regime=...)` or retire it.

## 8. Only 1m has a hard coverage guard

The engine raises a clear error when 1m execution data does not cover the
requested window. No other timeframe has that protection: with
`DATA_AUTODOWNLOAD=false`, a short 5m store silently truncates the window instead.
Discovered when two runs with different `--end` dates returned byte-identical
results.

Same silent-wrong-data class we closed for 1m, still open for everything else.
