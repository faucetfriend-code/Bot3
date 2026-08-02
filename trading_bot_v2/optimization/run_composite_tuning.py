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
import hashlib
import json
import os
import statistics
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from ..backtesting.optimization_adapter import OptimizationAdapter
from .search_spaces import get_search_space

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
    inside the range Optuna's samplers accept.

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
    canonical = "|".join(
        [
            _FOLD_SEED_NAMESPACE,
            str(int(base_seed)),
            strategy,
            symbol,
            train_start,
            train_end,
            test_start,
            test_end,
        ]
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") & 0xFFFFFFFF


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
