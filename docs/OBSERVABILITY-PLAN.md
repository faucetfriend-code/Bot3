# Optimization & Backtest Observability Plan

Status: designed 2026-07-28, not yet implemented.

## Why

The 2026-07-28 validation campaign ran six strategies over 3x2-month windows on
BTC/ETH/SUI. Three produced zero trades, and in every case the reason was
invisible from the output. Each took a multi-hour manual investigation with
hand-written offline replication scripts to diagnose:

1. **ma_crossover** stored the crossover bar as an absolute list index while
   every caller supplies a fixed-length rolling window, so "bars since
   crossover" was permanently 0 and the 1-5 bar entry gate never opened.
   552 crossovers detected, 0 signals.
2. **vwap_scalping** had `VWAP_SD_ENTRY_THRESHOLD=4.037` against a metric whose
   observed maximum over 52,041 real bars was 3.95. Zero bars could ever pass.
3. **momentum_scalping** generated 136 valid signals; all were discarded because
   a constant RRR of 1.20 failed the strategy's own hardcoded `rrr >= 1.5` flag.
   The discard happened outside the strategy file, at warning level,
   unattributed.

The common thread: the system reports the same thing for three completely
different situations - a strategy that legitimately found no opportunities, one
that was structurally incapable of firing, and one that fired constantly and had
everything thrown away downstream.

Root architectural cause: **every gate in the pipeline returns a boolean and
throws away the reason.** `Signal.is_valid()` (models.py:515-530) ANDs eight
flags and returns a bare bool. The discard site (strategy_manager.py:1015) logs
only `"{symbol}: Single signal failed validation"`. The multi-signal path
(strategy_manager.py:1022) does not log at all.

## Key design decisions

### Banded scoring, not pruning, for zero-trade trials

The initial hypothesis was to prune zero-trade Optuna trials so the flat score
landscape stops teaching TPE nothing. That is wrong: pruning deletes the points,
leaving TPE with no signal about the region and free to resample it
(`TPESampler` defaults to `consider_pruned_trials=False`).

The fix is a reserved score band, monotone in funnel depth. TPE is rank-based,
so absolute magnitudes are irrelevant and disjoint bands are safe:

| Outcome | Optuna state | Score |
|---|---|---|
| Traded normally | COMPLETE | `max(-50.0, objective)` (clamped so bands cannot collide) |
| Signals emitted, 0 closed trades | COMPLETE | `-100.0 + progress` |
| Strategy invoked, 0 raw signals | COMPLETE | `-200.0 + near_miss` |
| Params structurally infeasible | PRUNED (pre-backtest) | n/a |
| Backtest raised | FAIL (via `catch=`) | n/a - never `-inf` |

Only structurally infeasible params get pruned, because there the correct action
is to never burn the backtest at all.

Note the current code does not even score 0: `optimization_adapter.py:132-136`
applies a trade penalty giving -1.0, and `optuna_runner.py:443,522` return
`float("-inf")` on empty results and on **any** exception - which poisons
`study.best_value` and records the failure as COMPLETE, indistinguishable from a
real score.

### Live correctness bug found during design

`optuna_runner._record_trial_registry` (lines 344-347) counts `COMPLETE +
PRUNED` trials as "configurations explored" into `trial_registry.n_trials`,
which `validation/gate.py:195-200` feeds to the deflated Sharpe ratio. If we
start pruning infeasible configs pre-backtest, N inflates with configs that
never explored anything, DSR deflates harder than it should, and the gate check
gets harder to pass for no reason. Must be fixed in the same phase that
introduces pre-check pruning.

### Reparameterize, do not just reject

Momentum's search space is `atr_stop_mult (1.0, 2.5)` x `atr_target_mult
(2.0, 4.0)` (search_spaces.py:218-219) against `rrr >= 1.5`, so the infeasible
region `target < 1.5 * stop` is a large corner of the box. Rejection sampling
fixes the waste but distorts the TPE model (it keeps proposing into a hole).
Cleaner: sample `rrr_multiple in [1.5, 3.0]` and derive
`atr_target_mult = atr_stop_mult * rrr_multiple`, making infeasibility
unrepresentable. Rejection-pruning is the general safety net; reparameterization
is the targeted fix.

## Phases

**Phase 0 - Attribution.** `Signal.invalid_flags()` returning the names of False
flags, with `is_valid()` rewritten as `not self.invalid_flags()` so they cannot
drift. Upgrade the two discard log sites to name the failing flags. New
`optimization/feasibility.py` with declared algebraic constraints per strategy
(evaluated with zero data) and `feasible_fraction()` to Monte-Carlo what
fraction of a declared search box can never trade.

**Phase 1 - Signal funnel.** New `trading_bot_v2/diagnostics/` package. A
per-run counter chain: bars evaluated, regime blocked, strategy invoked, raw
signals, confidence-gate dropped, conflict dropped, validity dropped, orders
placed, fills, closed trades - plus interned rejection-reason counters and a
`diagnose()` classifier producing one of: traded, no_opportunities,
structurally_blocked, all_discarded_downstream, never_invoked, no_data. Hot-path
discipline is non-negotiable: interned reason constants (never f-strings), no
per-bar allocation or I/O, `NullFunnel` as the default, pinned by a <3% wall-clock
benchmark test.

Also counts the `_execute_signal` early-return reasons in engine.py
(same-direction skip, hedge-mode block, min-hold block, qty <= 0) - stages
nobody currently counts, each a plausible silent-zero cause.

**Phase 2 - Trial outcomes and banded scoring.** `diagnostics/outcomes.py`,
pre-backtest feasibility check raising `TrialPruned`, deletion of both `-inf`
returns in favour of `catch=(Exception,)`, and the trial-registry DSR fix above.

**Phase 3 - Human-readable reports.** A funnel block rendered in the existing
`gate.print_verdict` house style, shown on BOTH success and failure. On a
passing run it still reports where the largest attrition happened ("82% of raw
signals died at the confidence gate" is actionable even at Sharpe 1.4). New
`python -m trading_bot_v2.diagnostics.explain --study <name>` CLI reading stored
trial attrs - zero new storage.

**Phase 4 - Gate-metric calibration.** Optional `describe_gate_metrics()` hook
per strategy, and a calibration pass recording each gating metric's observed
distribution to a committed JSON artifact. This is the only thing that catches
the VWAP class of bug (threshold above the metric's mathematical ceiling). One
hook pays for three features: the calibration histogram, the near-miss gradient
term in scoring, and the "binding constraint" line in reports.

**Phase 5 - Persistence and surfacing.** No schema migration: the funnel rides
along in the existing `validation_runs.chunks_json`, and two synthetic
`GateCheck` records carry the diagnosis through `checks_json` to the existing
`/api/validation/runs`. Requires an `advisory: bool` field on `GateCheck` so
always-failing diagnostic checks do not change gate semantics
(`gate.py:237` computes `verdict.passed = all(c.passed)`).

Note `interface.html` currently contains zero references to validation -
`/api/validation/runs` and `/api/validation/latest` are built but unconsumed. A
dashboard panel is new UI work, scoped last and optional.

**Phase 6 - Search-space reparameterization.** Deferred: `rrr_multiple` is not a
strategy attribute, so it must be expanded before reaching
`apply_params_to_strategy`, and `regime_param_overlay.get_param_whitelist`
derives from the search space - that coupling makes it a separate phase, not a
quick win.

## What a zero-trade trial outputs, end to end

Trial value `-99.70` (band `-100 + progress`), not 0, not `-inf`. Sorting the
study by value puts every "ran but couldn't trade" trial below every real result
and above every "never fired" trial. Stored in `trial.user_attrs`:

```json
{
  "outcome": "all_discarded_downstream",
  "headline": "136 signals generated, 0 survived validation - all failed rrr_meets_minimum (RRR 1.20 < 1.50)",
  "binding_stage": "validity_dropped",
  "funnel": {"bars_evaluated": 52041, "strategy_invoked": 18220,
             "raw_signals": 136, "validity_dropped": 136,
             "orders_placed": 0, "closed_trades": 0},
  "top_reasons": [["validity:rrr_meets_minimum", 136]],
  "gate_metrics": {"rrr": {"min": 1.20, "max": 1.20, "threshold": 1.50,
                           "n_pass": 0, "closest_approach": 1.20}},
  "progress": 0.30,
  "suggested_fix": "atr_target_mult/atr_stop_mult must be >= 1.5; sampled 2.40/2.00 = 1.20"
}
```

For the VWAP case the same shape yields `outcome: "structurally_blocked"`,
`binding_stage: "raw_signals"`, and a headline stating that the metric's
observed maximum (3.95) is below the configured threshold (4.037), so the
threshold is unreachable on this data.

## Sequencing constraint

Phase 1 touches `strategy_manager.generate_signals_for_market`, the same method
where the momentum fix adds per-strategy discard counters. Land Phase 1 after
that fix, and consume its counters through a `getattr`-based shim rather than
reimplementing them. Phases 0-5 never touch `search_spaces.py` or `.env`.
