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

## 8. Only 1m has a hard coverage guard

The engine raises a clear error when 1m execution data does not cover the
requested window. No other timeframe has that protection: with
`DATA_AUTODOWNLOAD=false`, a short 5m store silently truncates the window instead.
Discovered when two runs with different `--end` dates returned byte-identical
results.

Same silent-wrong-data class we closed for 1m, still open for everything else.
