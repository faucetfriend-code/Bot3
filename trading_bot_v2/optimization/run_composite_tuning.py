"""
Composite-State Walk-Forward Tuning (regime variants, 2026-07-30)
=================================================================

Tunes per-state parameter variants for one strategy, keyed on the
COMPOSITE state (volatility tercile x directional bias) - the two axes
measured as informative - instead of the ADX labels measured as carrying
~no forward information (docs/REGIME-DISCRIMINATION.md).

Design, and why it is cheap enough to run:
- Walk-forward folds: train window -> Optuna study -> pick winners ->
  score winners on the UNSEEN test window; step forward and repeat.
- ONE shared trial pool per fold serves ALL states: every trial's
  backtest produces trades tagged with every composite state (Phase A
  plumbing), so each trial records a per-state score in user_attrs and
  each state selects its own best trial post-hoc. 6 states cost one
  study, not six.
- Out-of-sample honesty: each state's winner AND the shipped defaults
  are both scored on the fold's test window; the deliverable is
  tuned-vs-default out-of-sample, per state, aggregated across folds.
- Optional THIRD arm: --baseline-params scores a caller-supplied
  per-state parameter set (in practice the CURRENTLY ADOPTED params) on
  the same unseen test windows. Without it, leg 2 of the pre-registered
  adoption rule ("fresh tuned must also beat the adopted set
  out-of-sample") is unmeasurable and the monthly verdict is "no
  change" by construction. The arm is a scoring pass only: it never
  enters the Optuna study and never changes winner selection.
- FOURTH arm, always on: the PREQUENTIAL MEDIAN. What the monthly
  cadence actually deploys is not a fold winner - it is the
  coordinate-wise median of the fold winners
  (``monthly_retune._fresh_medians``), a vector no code had ever scored
  on any window. This arm scores it, leak-free: for fold k the median
  is built from the winners of folds 1..k-1 ONLY (an expanding window)
  and then scored on fold k's unseen test window. Fold 1 has no prior
  winners, so it has no median arm and scoring starts at fold 2. A
  median taken over ALL folds and scored on a fold that helped produce
  it would be contaminated by the future and is deliberately not
  computed here. The arm is a faithful simulation of the live policy
  ("each month, deploy the median of everything tuned so far"), so it -
  not the fold-winner arm - is the number that describes the deployed
  object. It shares the test-window backtest cache, so a median that
  coincides with a winner or the baseline costs nothing extra.
- SELF-DESCRIBING ARTIFACTS (2026-08-02): the report carries a
  ``run_config`` block with every resolved argument (including the
  EFFECTIVE fold step, not the raw ``--step-months`` which is None when
  defaulted), the measurement-relevant environment actually in force,
  and - separately - the values those knobs RESOLVED to, since an unset
  variable still has a default and a raw env capture alone cannot be
  read without knowing what that default was on the day.
  Before it existed, two artifacts could disagree with nothing in the
  repo explaining why: ``out/composite_mr_gateenforce.json`` used a
  12-month step and ``out/monthly/composite_mean_reversion_2026-07.json``
  the default 6-month one, which had to be inferred from the fold dates
  and confirmed by re-running the window
  (docs/NEUTRAL-STATE-WINDOW-CHECK.md). Existing top-level keys are
  unchanged; readers of ``folds``/``summary`` are unaffected.
- REPRODUCIBLE ACROSS RUNS (2026-08-02): each fold's Optuna seed is
  derived from the fold's IDENTITY - ``sha256(--seed | strategy |
  symbol | train/test dates)``, see ``fold_seed`` - not from its ordinal
  position in the sequence. A fold covering a given calendar window now
  yields the same trial sequence, the same winner and the same tuned
  score in any run that contains it, whatever else the run contains.
  The resolved seed is written into every fold record in the report, so
  an artifact is self-describing. NOTE: this CHANGED tuned results.
  Every tuned-arm number in ``out/`` predates the fix and was produced
  under position-derived seeding; it will not reproduce and must not be
  compared with a post-fix tuned number. The DEFAULT and BASELINE arms
  are unaffected (their parameters are fixed inputs, not search output)
  and stay comparable across the fix; the PREQUENTIAL MEDIAN arm is
  built from fold winners, so it moves with them.

Required environment (the caller exports these; none are in .env):
    REGIME_MODE=volatility
    REGIME_VOL_STRATEGIES_LOW/MID/HIGH   include the tuned strategy so
                                         every state is reachable
    REGIME_VOL_WEIGHTS_LOW/MID/HIGH      1.0
    BACKTEST_HISTORY_LOOKBACK=100
    DIRECTIONAL_GATE=off|enforce         the arm being tuned
    LOG_LEVEL=WARNING

Miss the REGIME_* block and the run is a silent no-op: trades come back
tagged with the ADX labels while the composite-state filter matches
"vol_low"/"vol_mid"/"vol_high", so every state reports
"insufficient_data" after the full trial budget has been spent. The
scheduled driver builds this env in
``monthly_retune._retune_env`` and passes it through subprocess env=.
DIRECTIONAL_GATE and LOG_LEVEL are the exception - they ARE in .env, so
a shell export loses to load_dotenv(override=True); use the
--directional-gate / --log-level flags for those.

Usage:
    python -m trading_bot_v2.optimization.run_composite_tuning \\
        --strategy mean_reversion --symbol BTC-USDC \\
        --start 2020-07-01 --end 2026-07-01 \\
        --train-months 12 --test-months 6 --trials 25 \\
        --report out/composite_mr_off.json
"""

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from ..backtesting.optimization_adapter import OptimizationAdapter
from .search_spaces import get_search_space
from .seeding import derive_seed

#: The six composite states: three volatility terciles crossed with
#: directional ("trend" = bull|bear, side-symmetric) vs neutral.
DEFAULT_STATES = (
    "vol_low:trend",
    "vol_low:neutral",
    "vol_mid:trend",
    "vol_mid:neutral",
    "vol_high:trend",
    "vol_high:neutral",
)

#: Trials with fewer matching trades than this in a state are not
#: eligible to be that state's winner (same floor as regime studies).
DEFAULT_MIN_STATE_TRADES = 10

#: Version tag mixed into every per-fold seed (see ``fold_seed``). It
#: exists so a future change to the derivation is an explicit, greppable
#: version bump rather than a silent shift in every tuned result.
_FOLD_SEED_NAMESPACE = "composite-fold-seed/v1"

#: Schema version of the ``run_config`` report block. Bump it when a
#: field changes meaning, so an old artifact is not misread as a new one.
RUN_CONFIG_VERSION = 1

#: Environment variables that change WHAT A RUN MEASURES, recorded into
#: ``run_config.env``. Deliberately an explicit allow-list rather than a
#: dump of os.environ: these reports are written into out/ and read
#: months later, and a dump would put API credentials in them. A
#: variable that is unset is recorded as null, so "unset" stays
#: distinguishable from "set to empty string".
#:
#: The REGIME_* block is here because missing it turns the whole run into
#: a silent no-op (see the module docstring); the BACKTEST_* block
#: because funding model, history lookback and tie-break seed all move
#: the numbers without appearing anywhere else in the artifact.
RECORDED_ENV_VARS = (
    "REGIME_MODE",
    "REGIME_VOL_WINDOW",
    "REGIME_VOL_REFERENCE_DAYS",
    "REGIME_VOL_MIN_OBSERVATIONS",
    "REGIME_VOL_STRATEGIES_LOW",
    "REGIME_VOL_STRATEGIES_MID",
    "REGIME_VOL_STRATEGIES_HIGH",
    "REGIME_VOL_STRATEGIES_WARMUP",
    "REGIME_VOL_WEIGHTS_LOW",
    "REGIME_VOL_WEIGHTS_MID",
    "REGIME_VOL_WEIGHTS_HIGH",
    "REGIME_VOL_WEIGHTS_WARMUP",
    "DIRECTIONAL_GATE",
    "BACKTEST_HISTORY_LOOKBACK",
    "BACKTEST_WARMUP_CANDLES",
    "BACKTEST_FUNDING_MODEL",
    "BACKTEST_FUNDING_HOURLY_PCT",
    "BACKTEST_FUNDING_CONVERSION",
    "BACKTEST_FUNDING_SCALE",
    "BACKTEST_FUNDING_INTERVAL_HOURS",
    "BACKTEST_SEED",
)


def resolved_settings() -> Dict[str, Any]:
    """The values the backtests actually used, not the raw env strings.

    ``run_config.env`` answers "was this variable set in the
    environment". That is not the same question as "what did the code
    use", and on its own it is close to useless: every one of these
    knobs applies a default at its call site (REGIME_MODE -> "adx",
    BACKTEST_HISTORY_LOOKBACK -> 60, BACKTEST_FUNDING_MODEL -> "flat"),
    so a null in the env block means "the default", and reading that
    artifact a year later requires knowing what the default was AT THE
    TIME. Defaults move. This block records the answer directly.

    Every value comes from the same resolver the engine itself calls -
    ``get_regime_mode``, ``validate_funding_model``, the ``config``
    attributes - never a literal copied into this module, so it cannot
    drift away from real behaviour.

    Caveat worth knowing when reading an artifact: the engine lets a
    config proxy override the funding knobs per run
    (``engine._env_or_cfg``), and the optimization adapter uses one.
    What is recorded here is the process-level resolution, which is what
    a re-run from the same environment would reproduce.

    Returns:
        Knob name -> resolved value. On an unexpected resolver failure
        the key maps to an error string rather than being dropped: a
        missing key must always mean "this field did not exist yet".
    """
    from ..backtesting.funding import validate_conversion, validate_funding_model
    from ..config import config
    from ..volatility_regime import get_regime_mode

    def _safe(name, fn):
        try:
            return fn()
        except Exception as exc:  # never lose a report to a resolver
            return f"<unresolved: {type(exc).__name__}: {exc}>"

    return {
        "regime_mode": _safe("regime_mode", get_regime_mode),
        "backtest_history_lookback": _safe(
            "lookback", lambda: config.backtest_history_lookback
        ),
        "backtest_warmup_candles": _safe(
            "warmup", lambda: config.backtest_warmup_candles
        ),
        "backtest_funding_model": _safe(
            "funding_model",
            lambda: validate_funding_model(os.getenv("BACKTEST_FUNDING_MODEL")),
        ),
        "backtest_funding_conversion": _safe(
            "funding_conversion",
            lambda: validate_conversion(os.getenv("BACKTEST_FUNDING_CONVERSION")),
        ),
        "backtest_funding_hourly_pct": _safe(
            "funding_pct", lambda: config.backtest_funding_hourly_pct
        ),
    }


def build_run_config(
    args: argparse.Namespace,
    states: List[str],
    step_months: int,
    argv: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Snapshot the run's resolved configuration for the report.

    Everything needed to re-run the measurement, in one block, so an
    artifact can be read on its own. The single most important field is
    ``args.step_months``: it is the EFFECTIVE step actually used for
    fold generation, whereas ``args.step_months_arg`` preserves the raw
    flag, which is null whenever the caller let it default to
    ``--test-months``. Recording only the raw flag would have left the
    2026-08-01 12-vs-6-month discrepancy exactly as undiagnosable as it
    was (docs/NEUTRAL-STATE-WINDOW-CHECK.md).

    Call this AFTER the ``--directional-gate`` override has been written
    to ``os.environ``, so the recorded ``DIRECTIONAL_GATE`` is the value
    the backtests actually saw and not the one ``.env`` supplied.

    Args:
        args: Parsed CLI namespace.
        states: Composite states this run measures, already normalised.
        step_months: The effective fold step in months, i.e.
            ``args.step_months or args.test_months``.
        argv: Argument vector this run was invoked with. Defaults to
            ``sys.argv[1:]``.

    Returns:
        A JSON-serialisable dict with ``version``, ``args``, ``env``,
        ``seed_namespace`` and ``argv`` keys.
    """
    return {
        "version": RUN_CONFIG_VERSION,
        "args": {
            "strategy": args.strategy,
            "symbol": args.symbol,
            "start": args.start,
            "end": args.end,
            "train_months": args.train_months,
            "test_months": args.test_months,
            "step_months": step_months,
            "step_months_arg": args.step_months,
            "trials": args.trials,
            "states": list(states),
            "min_state_trades": args.min_state_trades,
            "objective": args.objective,
            "capital": args.capital,
            "seed": args.seed,
            "directional_gate": args.directional_gate,
            "baseline_params": args.baseline_params,
            "no_trial_registry": args.no_trial_registry,
            "log_level": args.log_level,
            "report": args.report,
        },
        # env = what was SET (null means "not set"); resolved = what was
        # USED. Keep both: env alone cannot be read without knowing the
        # defaults of the day, and resolved alone loses the distinction
        # between an explicit setting and a default that happened to
        # agree with it.
        "env": {name: os.environ.get(name) for name in RECORDED_ENV_VARS},
        "resolved": resolved_settings(),
        "seed_namespace": _FOLD_SEED_NAMESPACE,
        "argv": list(argv) if argv is not None else list(sys.argv[1:]),
    }


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--strategy", required=True)
    p.add_argument("--symbol", default="BTC-USDC")
    p.add_argument("--start", default="2020-07-01")
    p.add_argument("--end", default="2026-07-01")
    p.add_argument("--train-months", type=int, default=12)
    p.add_argument("--test-months", type=int, default=6)
    p.add_argument("--step-months", type=int, default=None,
                   help="Fold step (default: test-months, non-overlapping tests)")
    p.add_argument("--trials", type=int, default=25)
    p.add_argument("--states", default=",".join(DEFAULT_STATES))
    p.add_argument("--min-state-trades", type=int,
                   default=DEFAULT_MIN_STATE_TRADES)
    p.add_argument(
        "--objective", default="sharpe_ratio",
        choices=(
            "sharpe_ratio", "sortino_ratio", "total_return_pct",
            "calmar_ratio", "profit_factor",
        ),
        help="Canonical long-form metric name (default: sharpe_ratio)",
    )
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument(
        "--seed", type=int, default=0,
        help=(
            "Run-level seed. Each fold's Optuna seed is derived from it "
            "plus the fold's own window/strategy/symbol (see fold_seed), "
            "so the same window reproduces at any position in any fold "
            "sequence, and changing this moves every fold together."
        ),
    )
    p.add_argument("--report", default=None, help="Write JSON report here")
    p.add_argument(
        "--baseline-params", default=None,
        help=(
            "Path to a JSON file mapping composite state -> parameter "
            'dict, e.g. {"vol_low:trend": {"rsi_oversold": 32.75}}. Each '
            "entry is scored as a third arm on every fold's UNSEEN test "
            "window, alongside the fold winner and the shipped "
            "defaults. Use it to give the currently adopted params an "
            "out-of-sample score (leg 2 of the adoption rule). States "
            "absent from the file behave exactly as before."
        ),
    )
    p.add_argument(
        "--no-trial-registry", action="store_true",
        help=(
            "Do not record this run's trials in the trial_registry. "
            "Leave off for any run whose search should count toward the "
            "Deflated Sharpe Ratio's N - which is every real run. Exists "
            "for throwaway probes that would otherwise inflate the "
            "multiple-testing penalty for a search nobody acted on."
        ),
    )
    p.add_argument(
        "--log-level", default="WARNING",
        help=(
            "loguru sink level for this run (default: WARNING). "
            "Deliberately NOT read from LOG_LEVEL: .env pins that to "
            "INFO via load_dotenv(override=True), and INFO wrote "
            "~750 MB/hour over a multi-hour tuning arm."
        ),
    )
    p.add_argument(
        "--directional-gate", default=None,
        choices=("off", "log", "enforce"),
        help=(
            "Force DIRECTIONAL_GATE for this run. Set via os.environ "
            "AFTER config import, because load_dotenv(override=True) "
            "clobbers shell exports whenever the key exists in .env - "
            "the only reliable way to run the off/enforce A/B arms."
        ),
    )
    return p.parse_args(argv)


def _add_months(iso: str, months: int) -> str:
    dt = datetime.fromisoformat(iso)
    month = dt.month - 1 + months
    return dt.replace(
        year=dt.year + month // 12, month=month % 12 + 1
    ).strftime("%Y-%m-%d")


def _folds(start: str, end: str, train_m: int, test_m: int, step_m: int):
    """Yield (train_start, train_end, test_start, test_end) folds."""
    cursor = start
    while True:
        train_end = _add_months(cursor, train_m)
        test_end = _add_months(train_end, test_m)
        if test_end > end:
            return
        yield cursor, train_end, train_end, test_end
        cursor = _add_months(cursor, step_m)


def fold_seed(
    base_seed: int,
    strategy: str,
    symbol: str,
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
) -> int:
    """Derive a fold's Optuna seed from the fold's IDENTITY, not its position.

    Until 2026-08-02 the per-fold seed was ``base_seed + fold_no``, i.e.
    it depended on the fold's ORDINAL POSITION in the sequence. The same
    calendar window therefore got a different seed in a 5-fold run than
    in a 10-fold run, a different trial sequence, a different winner and
    a different tuned score - so tuned-arm numbers were never comparable
    across runs with different fold counts, which is exactly what the
    monthly cadence does month over month. Measured instance: train
    2021-07..2022-07 / test 2022-07..2023-01 scored ``vol_low:trend``
    +1.082 (n=84) as fold 3 and +3.011 (n=55) as fold 2, at the same
    ``--seed 0``, while the default arm reproduced exactly
    (docs/NEUTRAL-STATE-WINDOW-CHECK.md, finding (a)).

    Derivation, recomputable by hand::

        canonical = "|".join([
            "composite-fold-seed/v1", str(base_seed), strategy, symbol,
            train_start, train_end, test_start, test_end,
        ])
        seed = int.from_bytes(
            hashlib.sha256(canonical.encode("utf-8")).digest()[:4], "big"
        ) & 0xFFFFFFFF

    ``hashlib`` and not the builtin ``hash()``: ``hash()`` of a str is
    salted per process by PYTHONHASHSEED, which would make the seed
    differ between two invocations of the same command and turn a
    positional bug into a total one. SHA-256 is stable across processes,
    interpreter versions and platforms. The 32-bit mask keeps the value
    inside the range Optuna's samplers accept. The hashing itself lives
    in ``seeding.derive_seed``, shared with the OptunaRunner's per-study
    seed (``optuna_runner.study_seed``) so the scheme exists once.

    Why ``strategy`` and ``symbol`` are in the derivation, and the
    objective and directional-gate arm are not: symbols and strategies
    are treated in this repo as INDEPENDENT REPLICATIONS - a finding is
    credited when several symbols agree (e.g. the ETH+SUI 4/4 sweep).
    Optuna's startup trials are drawn from the seed alone, so keying
    only on the window would hand BTC, ETH and SUI the identical opening
    parameter vectors; an unlucky opening draw would then push every
    symbol the same way and cross-symbol agreement would partly measure
    the shared draw rather than the market. Keying on symbol and
    strategy keeps those replications genuinely independent. The
    objective and the gate arm are the opposite case: gate off vs
    enforce is a deliberately PAIRED A/B over identical folds, where
    sharing the opening draw removes nuisance variance from the
    comparison rather than manufacturing agreement. So they stay out and
    both arms of a paired comparison keep the same seed.

    Args:
        base_seed: The run-level ``--seed`` value. Changing it moves
            every fold's seed together, so the global knob still works.
        strategy: Strategy identifier being tuned.
        symbol: Symbol being tuned.
        train_start: Fold train window start (ISO date).
        train_end: Fold train window end (ISO date).
        test_start: Fold test window start (ISO date).
        test_end: Fold test window end (ISO date).

    Returns:
        A 32-bit non-negative seed, identical for the same inputs in any
        process, on any platform, at any position in a fold sequence.
    """
    return derive_seed(
        _FOLD_SEED_NAMESPACE,
        base_seed,
        [strategy, symbol, train_start, train_end, test_start, test_end],
    )


#: Objective value returned when no state scored in a trial. Excluded
#: from the recorded Sharpe variance for the same reason OptunaRunner
#: excludes its zero-trade band scores: it is a sentinel, not an
#: estimate, and mixing it in produces absurd DSR benchmarks.
_NO_SCORE_SENTINEL = -1e9


def record_fold_trials(
    study: Any,
    strategy: str,
    symbol: str,
    objective: str,
    train: List[str],
    test: List[str],
) -> Optional[int]:
    """Record one fold's Optuna study in the trial_registry.

    Composite tuning drives Optuna directly rather than through
    ``OptunaRunner``, so until 2026-08-02 it recorded NOTHING - the
    entire campaign from 2026-07-28 on was invisible to the Deflated
    Sharpe Ratio, which reads its N from this table
    (``validation/gate.py``, ``validation/runner.py``). Every DSR
    computed against that campaign therefore understated the search
    size and deflated too little. One row per fold matches
    ``OptunaRunner._record_trial_registry``'s one-row-per-study
    granularity.

    ``regime`` is deliberately NULL rather than a composite state: a
    fold runs ONE shared trial pool that every state selects from, so
    attributing those trials to a single state would be false. NULL-
    regime rows count toward every regime query in
    ``get_total_trials``, which is the correct treatment here.

    Args:
        study: The finished Optuna study for this fold.
        strategy: Snake_case strategy key.
        symbol: Symbol being tuned.
        objective: Canonical objective name; the Sharpe variance is
            recorded only for ``sharpe_ratio``, mirroring OptunaRunner.
        train: ``[start, end]`` of the fold's train window.
        test: ``[start, end]`` of the fold's test window.

    Returns:
        The inserted row id, or None when nothing was recorded (no
        trials, or the database was unreachable - a registry failure
        must never destroy a multi-hour tuning run).
    """
    import optuna

    from ..database import DatabaseManager

    state = optuna.trial.TrialState
    scored = [
        t for t in study.trials
        if t.state in (state.COMPLETE, state.PRUNED) and t.value is not None
    ]
    if not scored:
        return None

    sr_variance: Optional[float] = None
    if objective == "sharpe_ratio":
        values = [
            t.value for t in scored
            if t.state == state.COMPLETE and t.value > _NO_SCORE_SENTINEL / 2
        ]
        if len(values) >= 2:
            sr_variance = statistics.variance(values)

    source = (
        f"composite/{strategy}/{symbol}/"
        f"{train[0]}_{train[1]}/{test[0]}_{test[1]}"
    )
    try:
        return DatabaseManager().save_trial_registry_entry(
            strategy=strategy,
            regime=None,
            scope="optuna_study",
            n_trials=len(scored),
            sr_variance=sr_variance,
            source=source,
        )
    except Exception as exc:
        print(f"  trial registry: NOT recorded ({type(exc).__name__}: {exc})")
        return None


def _suggest(trial, space: Dict[str, Any]) -> Dict[str, Any]:
    """Sample one parameter set from a search-space definition."""
    params: Dict[str, Any] = {}
    for name, spec in space.items():
        if isinstance(spec, tuple) and len(spec) == 2:
            low, high = spec
            if isinstance(low, int) and isinstance(high, int):
                params[name] = trial.suggest_int(name, low, high)
            else:
                params[name] = trial.suggest_float(name, float(low), float(high))
        elif isinstance(spec, list):
            params[name] = trial.suggest_categorical(name, spec)
        else:
            raise ValueError(f"Unhandled search-space spec for {name}: {spec!r}")
    return params


def _state_scores(
    adapter: OptimizationAdapter,
    result,
    states: List[str],
    objective: str,
    capital: float,
    start: str,
    end: str,
) -> Dict[str, Dict[str, Any]]:
    """Score one backtest's trades per composite state."""
    out: Dict[str, Dict[str, Any]] = {}
    for state in states:
        trades = adapter.get_regime_trades(result, state)
        score = (
            adapter.calculate_objective_from_trades(
                trades, objective, capital, start, end
            )
            if trades
            else None
        )
        out[state] = {
            "n": len(trades),
            "score": score,
            "pnl": round(sum(t.get("pnl", 0.0) for t in trades), 4),
        }
    return out


def _load_baseline_params(
    path: Optional[str], states: List[str]
) -> Dict[str, Dict[str, Any]]:
    """Load the per-state baseline (usually: adopted) parameter sets.

    Args:
        path: JSON file mapping state -> parameter dict, or None.
        states: The states this run is measuring; entries outside this
            list are dropped with a notice rather than silently kept.

    Returns:
        State (lower-cased) -> parameter dict. Empty when path is None.

    Raises:
        ValueError: The file is not an object of objects. A malformed
            baseline must fail loudly - silently dropping it would make
            the run look like a clean two-arm result.
    """
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ValueError(
            f"--baseline-params {path}: expected an object keyed by "
            f"composite state, got {type(raw).__name__}"
        )
    out: Dict[str, Dict[str, Any]] = {}
    for state, params in raw.items():
        if not isinstance(params, dict):
            raise ValueError(
                f"--baseline-params {path}: entry for {state!r} is "
                f"{type(params).__name__}, expected an object of params"
            )
        key = str(state).strip().lower()
        if key not in states:
            print(f"  baseline: ignoring {state!r} (not a measured state)")
            continue
        out[key] = dict(params)
    return out


def _cached_backtest(
    adapter: OptimizationAdapter,
    cache: Dict[str, Any],
    strategy: str,
    params: Dict[str, Any],
    start: str,
    end: str,
    symbol: str,
    capital: float,
) -> Any:
    """Run (or reuse) one test-window backtest, keyed on its params."""
    key = json.dumps(params, sort_keys=True)
    if key not in cache:
        cache[key] = adapter.run_backtest(
            strategy, params, start, end,
            symbol=symbol, initial_capital=capital,
        )
    return cache[key]


def _arm_state_scores(
    adapter: OptimizationAdapter,
    cache: Dict[str, Any],
    args: argparse.Namespace,
    arm_params: Dict[str, Dict[str, Any]],
    te_s: str,
    te_e: str,
) -> Dict[str, Dict[str, Any]]:
    """Score one per-state parameter arm on the fold's test window.

    Shares ``cache`` with every other arm, so two arms that land on the
    same parameter vector cost one backtest, not two.

    Args:
        adapter: Backtest adapter.
        cache: Test-window backtest cache, keyed on the param vector.
        args: Parsed CLI namespace (strategy/symbol/objective/capital).
        arm_params: State -> parameter vector for this arm.
        te_s: Test-window start (inclusive).
        te_e: Test-window end (exclusive).

    Returns:
        State -> score cell, for the states present in ``arm_params``.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for state, params in arm_params.items():
        result = _cached_backtest(
            adapter, cache, args.strategy, params, te_s, te_e,
            args.symbol, args.capital,
        )
        out[state] = _state_scores(
            adapter, result, [state], args.objective, args.capital,
            te_s, te_e,
        )[state]
    return out


def _baseline_state_scores(
    adapter: OptimizationAdapter,
    cache: Dict[str, Any],
    args: argparse.Namespace,
    baseline: Dict[str, Dict[str, Any]],
    te_s: str,
    te_e: str,
) -> Dict[str, Dict[str, Any]]:
    """Score each baseline parameter set on the fold's test window."""
    return _arm_state_scores(adapter, cache, args, baseline, te_s, te_e)


def coordinate_median(
    param_sets: List[Dict[str, Any]]
) -> Optional[Dict[str, float]]:
    """Coordinate-wise median of a list of parameter vectors.

    Each parameter's median is taken independently, which is exactly
    what ``monthly_retune._fresh_medians`` does to produce the vector
    that gets deployed - including the 4-decimal rounding, so the two
    agree bit for bit. The result is therefore NOT guaranteed to be a
    vector any fold ever proposed or any trial ever scored.

    Args:
        param_sets: Parameter vectors to aggregate.

    Returns:
        The median vector, or None when there is nothing to aggregate.
    """
    if not param_sets:
        return None
    acc: Dict[str, List[float]] = {}
    for params in param_sets:
        for key, value in params.items():
            acc.setdefault(key, []).append(value)
    return {k: round(statistics.median(v), 4) for k, v in acc.items()}


def prequential_medians(
    prior_winners: Dict[str, List[Dict[str, Any]]], states: List[str]
) -> Dict[str, Dict[str, float]]:
    """Per-state median of the winners from STRICTLY EARLIER folds.

    The caller must hand in winners accumulated up to (not including)
    the fold about to be scored - that ordering is the whole leak-free
    property. States with no prior winner are simply absent, which is
    how fold 1 ends up with no median arm.

    Args:
        prior_winners: State -> winner vectors from folds 1..k-1.
        states: States this run measures, in report order.

    Returns:
        State -> median vector, for states with at least one prior
        winner.
    """
    out: Dict[str, Dict[str, float]] = {}
    for state in states:
        median = coordinate_median(prior_winners.get(state) or [])
        if median is not None:
            out[state] = median
    return out


def _fmt(score: Optional[float]) -> str:
    return "n/a" if score is None else f"{score:+.3f}"


def _paired_mean(
    folds: List[Dict[str, Any]], state: str, arm: str, ref: str
) -> Optional[Dict[str, Any]]:
    """Mean of two arms over the folds where BOTH of them scored.

    Args:
        folds: Report fold records.
        state: Composite state to read.
        arm: Cell key of the arm under test (e.g. "test_median").
        ref: Cell key of the arm it is compared against.

    Returns:
        ``{"n", "arm", "ref"}`` means over the paired folds, or None
        when no fold scored both.
    """
    arm_vals: List[float] = []
    ref_vals: List[float] = []
    for fold in folds:
        cell = fold.get("states", {}).get(state) or {}
        a = (cell.get(arm) or {}).get("score")
        r = (cell.get(ref) or {}).get("score")
        if a is not None and r is not None:
            arm_vals.append(a)
            ref_vals.append(r)
    if not arm_vals:
        return None
    n = len(arm_vals)
    return {"n": n, "arm": sum(arm_vals) / n, "ref": sum(ref_vals) / n}


def _summarize_state(
    folds: List[Dict[str, Any]], state: str
) -> Dict[str, Any]:
    """Aggregate one state's four arms into a single out-of-sample row.

    Every comparison is a PAIRED difference: each pair is averaged only
    over the folds where both of its arms scored, so an arm that is
    absent from a fold (fold 1 has no median arm; an unmeasured state
    has no tuned arm) cannot silently shift the other side.

    Args:
        folds: Report fold records.
        state: Composite state to aggregate.

    Returns:
        Summary dict. Baseline and median keys appear only when that arm
        scored on at least one paired fold, so a state with neither
        keeps exactly the shape it had before those arms existed.
    """
    tuned_default = _paired_mean(folds, state, "test_tuned", "test_default")
    if tuned_default:
        entry: Dict[str, Any] = {
            "folds": tuned_default["n"],
            "tuned": tuned_default["arm"],
            "default": tuned_default["ref"],
            "edge": tuned_default["arm"] - tuned_default["ref"],
        }
    else:
        entry = {"folds": 0}
    tuned_base = _paired_mean(folds, state, "test_tuned", "test_baseline")
    if tuned_base:
        entry.update({
            "baseline_folds": tuned_base["n"],
            "baseline": tuned_base["ref"],
            "baseline_tuned": tuned_base["arm"],
            "edge_vs_baseline": tuned_base["arm"] - tuned_base["ref"],
        })
    entry.update(_median_arm_summary(folds, state))
    return entry


def _median_arm_summary(
    folds: List[Dict[str, Any]], state: str
) -> Dict[str, Any]:
    """Aggregate the prequential-median arm against the other three.

    The median-vs-baseline block is the one that governs adoption: the
    deployed object is a median, the incumbent is the adopted vector,
    and both are scored on the same unseen windows.

    Args:
        folds: Report fold records.
        state: Composite state to aggregate.

    Returns:
        Median keys, or an empty dict when this state had no median arm
        on any fold (which is the pre-existing summary shape).
    """
    out: Dict[str, Any] = {}
    med_def = _paired_mean(folds, state, "test_median", "test_default")
    if med_def:
        out.update({
            "median_folds": med_def["n"],
            "median": med_def["arm"],
            "median_default": med_def["ref"],
            "edge_median_vs_default": med_def["arm"] - med_def["ref"],
        })
    med_tuned = _paired_mean(folds, state, "test_median", "test_tuned")
    if med_tuned:
        out.update({
            "median_tuned_folds": med_tuned["n"],
            "median_on_tuned_folds": med_tuned["arm"],
            "median_tuned": med_tuned["ref"],
            "edge_median_vs_tuned": med_tuned["arm"] - med_tuned["ref"],
        })
    med_base = _paired_mean(folds, state, "test_median", "test_baseline")
    if med_base:
        out.update({
            "median_baseline_folds": med_base["n"],
            "median_on_baseline_folds": med_base["arm"],
            "median_baseline": med_base["ref"],
            "edge_median_vs_baseline": med_base["arm"] - med_base["ref"],
        })
    return out


def _summarize(
    folds: List[Dict[str, Any]], states: List[str]
) -> Dict[str, Any]:
    """Aggregate the per-fold arms into one out-of-sample row per state.

    Args:
        folds: Report fold records.
        states: States to aggregate, in report order.

    Returns:
        State -> summary dict.
    """
    return {state: _summarize_state(folds, state) for state in states}


def _print_summary(summary: Dict[str, Any], states: List[str]) -> None:
    """Print the out-of-sample table (all four arms, when present).

    The ``median`` column is the DEPLOYED object; ``tuned`` is a set of
    per-fold winners that nothing ever deploys. Read them in that order.
    """
    print(f"\n{'=' * 110}")
    print("OUT-OF-SAMPLE SUMMARY (mean across folds; median arm = "
          "prequential, folds 1..k-1 -> fold k)")
    print(f"{'=' * 110}")
    print(f"  {'state':>16} {'folds':>6} {'tuned':>9} {'default':>9} "
          f"{'edge':>8} {'adopted':>9} {'vs adopt':>9} {'median':>9} "
          f"{'med-def':>8} {'med-tuned':>10}")
    for state in states:
        row = summary.get(state) or {}
        tail = (f"{_fmt(row.get('baseline')):>9} "
                f"{_fmt(row.get('edge_vs_baseline')):>9} "
                f"{_fmt(row.get('median')):>9} "
                f"{_fmt(row.get('edge_median_vs_default')):>8} "
                f"{_fmt(row.get('edge_median_vs_tuned')):>10}")
        if not row.get("folds"):
            print(f"  {state:>16} {0:>6} {'n/a':>9} {'n/a':>9} {'n/a':>8} "
                  f"{tail}")
            continue
        print(f"  {state:>16} {row['folds']:>6} {row['tuned']:>+9.3f} "
              f"{row['default']:>+9.3f} {row['edge']:>+8.3f} {tail}")


def run(argv: Optional[List[str]] = None) -> int:
    """Run the composite walk-forward tuning and print/emit the report."""
    args = _parse_args(argv)
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    # Cap loguru explicitly. Its default sink is stderr at DEBUG and
    # this driver replays years of 5m bars per trial - the first full
    # arm wrote 18 GB of DEBUG stderr in 4 hours and filled the volume
    # (2026-07-30). LOG_LEVEL is NOT consulted: .env pins it to INFO
    # through load_dotenv(override=True), and INFO still writes
    # ~750 MB/hour here.
    try:
        logger.remove()
        logger.add(sys.stderr, level=args.log_level.upper())
    except Exception:
        pass

    if args.directional_gate is not None:
        # After config import, so this wins over the .env-loaded value.
        os.environ["DIRECTIONAL_GATE"] = args.directional_gate
        print(f"  DIRECTIONAL_GATE forced to '{args.directional_gate}'")

    states = [s.strip().lower() for s in args.states.split(",") if s.strip()]
    step_m = args.step_months or args.test_months
    space = get_search_space(args.strategy)
    adapter = OptimizationAdapter()
    baseline = _load_baseline_params(args.baseline_params, states)

    folds = list(
        _folds(args.start, args.end, args.train_months, args.test_months, step_m)
    )
    if not folds:
        print("No folds fit the requested span.")
        return 1

    print(f"COMPOSITE TUNING: {args.strategy} {args.symbol}")
    print(f"  states : {states}")
    print(f"  folds  : {len(folds)} (train {args.train_months}mo, "
          f"test {args.test_months}mo, step {step_m}mo)")
    print(f"  trials : {args.trials}/fold, objective {args.objective}")
    print(f"  adopted-baseline arm: "
          f"{sorted(baseline) if baseline else 'none (--baseline-params unset)'}")

    report: Dict[str, Any] = {
        "strategy": args.strategy,
        "symbol": args.symbol,
        "states": states,
        "trials_per_fold": args.trials,
        "objective": args.objective,
        "baseline_params_file": args.baseline_params,
        "baseline_params": baseline or None,
        # Appended, never replacing anything: monthly_retune and the
        # analysis scripts read "folds"/"summary" by name.
        "run_config": build_run_config(args, states, step_m, argv),
        "folds": [],
    }

    # Winners from folds strictly BEFORE the fold being scored. This is
    # the only state carried across folds, and the prequential median
    # arm reads it before this fold's winner is appended - that ordering
    # is what keeps the median free of test-window information.
    prior_winners: Dict[str, List[Dict[str, Any]]] = {}

    for fold_no, (tr_s, tr_e, te_s, te_e) in enumerate(folds, 1):
        # Seed from the fold's IDENTITY (window + strategy + symbol),
        # never its ordinal position: the same window must produce the
        # same trial sequence whether it is fold 2 of five or fold 3 of
        # ten. See ``fold_seed``.
        seed = fold_seed(
            args.seed, args.strategy, args.symbol, tr_s, tr_e, te_s, te_e
        )
        print(f"\nFOLD {fold_no}/{len(folds)}: train {tr_s}..{tr_e} "
              f"-> test {te_s}..{te_e} (seed {seed})", flush=True)

        trial_records: List[Dict[str, Any]] = []

        def objective_fn(trial):
            params = _suggest(trial, space)
            result = adapter.run_backtest(
                args.strategy, params, tr_s, tr_e,
                symbol=args.symbol, initial_capital=args.capital,
            )
            scores = _state_scores(
                adapter, result, states, args.objective,
                args.capital, tr_s, tr_e,
            )
            trial_records.append({"params": params, "scores": scores})
            pooled = [
                s["score"] for s in scores.values() if s["score"] is not None
            ]
            return sum(pooled) / len(pooled) if pooled else -1e9

        sampler = optuna.samplers.TPESampler(seed=seed)
        study = optuna.create_study(direction="maximize", sampler=sampler)
        study.optimize(objective_fn, n_trials=args.trials)

        if not args.no_trial_registry:
            record_fold_trials(
                study, args.strategy, args.symbol, args.objective,
                [tr_s, tr_e], [te_s, te_e],
            )

        # Per-state winner selection from the shared trial pool
        fold_out: Dict[str, Any] = {
            "train": [tr_s, tr_e], "test": [te_s, te_e],
            "seed": seed, "base_seed": args.seed,
            "seed_namespace": _FOLD_SEED_NAMESPACE,
            "states": {},
        }
        default_result = adapter.run_backtest(
            args.strategy, {}, te_s, te_e,
            symbol=args.symbol, initial_capital=args.capital,
        )
        default_scores = _state_scores(
            adapter, default_result, states, args.objective,
            args.capital, te_s, te_e,
        )

        # Cache test backtests: states often elect the same winner
        # params, and the baseline arm shares the cache.
        test_cache: Dict[str, Any] = {}
        baseline_scores = _baseline_state_scores(
            adapter, test_cache, args, baseline, te_s, te_e
        )
        # Snapshot BEFORE this fold's winners are recorded: folds 1..k-1.
        medians = prequential_medians(prior_winners, states)
        median_scores = _arm_state_scores(
            adapter, test_cache, args, medians, te_s, te_e
        )
        for state in states:
            cell: Dict[str, Any] = {}
            if state in baseline:
                cell["baseline_params"] = baseline[state]
                cell["test_baseline"] = baseline_scores[state]
            if state in medians:
                cell["median_params"] = medians[state]
                cell["median_from_folds"] = len(prior_winners[state])
                cell["test_median"] = median_scores[state]
            eligible = [
                r for r in trial_records
                if r["scores"][state]["score"] is not None
                and r["scores"][state]["n"] >= args.min_state_trades
            ]
            if not eligible:
                cell["verdict"] = "insufficient_data"
                fold_out["states"][state] = cell
                print(f"  {state:>16}: insufficient train trades")
                continue
            winner = max(eligible, key=lambda r: r["scores"][state]["score"])
            tuned = _state_scores(
                adapter,
                _cached_backtest(
                    adapter, test_cache, args.strategy, winner["params"],
                    te_s, te_e, args.symbol, args.capital,
                ),
                [state], args.objective, args.capital, te_s, te_e,
            )[state]
            base = default_scores[state]
            cell.update({
                "params": winner["params"],
                "train_score": winner["scores"][state]["score"],
                "train_n": winner["scores"][state]["n"],
                "test_tuned": tuned,
                "test_default": base,
            })
            fold_out["states"][state] = cell
            prior_winners.setdefault(state, []).append(winner["params"])
            line = (
                f"  {state:>16}: train "
                f"{_fmt(winner['scores'][state]['score'])} "
                f"(n={winner['scores'][state]['n']}) | test tuned "
                f"{_fmt(tuned['score'])} (n={tuned['n']}) vs default "
                f"{_fmt(base['score'])} (n={base['n']})"
            )
            if state in baseline:
                adopted_cell = baseline_scores[state]
                line += (f" vs adopted {_fmt(adopted_cell['score'])} "
                         f"(n={adopted_cell['n']})")
            if state in medians:
                med_cell = median_scores[state]
                line += (f" vs median[{len(prior_winners[state]) - 1}f] "
                         f"{_fmt(med_cell['score'])} (n={med_cell['n']})")
            print(line, flush=True)

        report["folds"].append(fold_out)

    # Aggregate: mean out-of-sample tuned vs default (and vs adopted)
    summary = _summarize(report["folds"], states)
    _print_summary(summary, states)
    report["summary"] = summary

    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nreport -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
