"""
Backfill the prequential-median arm onto an existing tuning report.

Why this exists
---------------
``monthly_retune`` deploys the COORDINATE-WISE MEDIAN of the walk-forward
fold winners (``_fresh_medians``). Until the median arm landed in
``run_composite_tuning``, no code had ever scored that vector on any
window: every edge in every monthly report described fold winners, each
fitted to its own train window. This module answers the question for
reports that were already written, without re-running Optuna.

Two modes:

``prequential``
    Reconstructs, for each fold k >= 2, the coordinate-wise median of the
    winners from folds 1..k-1 ONLY, and scores it on fold k's stored -
    and, for that median, entirely unseen - test window. Fold 1 has no
    prior winners and therefore no median arm. A median taken over all
    folds and scored on a fold that helped build it would be
    contaminated by the future; that number is never computed.
    Also scores the currently adopted params on the same windows, and
    runs a free geometry check on the full-report median (is it inside
    the winners' per-parameter range, and how far is it from the nearest
    vector anyone actually tuned).

``cross-symbol``
    Scores three arms - shipped defaults, the report's full-report fresh
    medians, and the currently adopted params - on a symbol and period
    the tuning never saw. This validates the SPECIFIC deployed vectors
    rather than the procedure.

Environment
-----------
Backtests need ``REGIME_MODE=volatility`` and the admission lists, or
every composite state comes back empty (trades carry ADX labels while
the state filter matches "vol_low"). That env has to be in place BEFORE
``config`` is imported, so the module re-executes itself once in a child
process with ``monthly_retune._retune_env`` applied - the same env the
scheduled driver builds. DIRECTIONAL_GATE and LOG_LEVEL are in .env and
would be clobbered by ``load_dotenv(override=True)``, so they are
applied after the config import instead, exactly as
``run_composite_tuning`` does.

Usage (from Bot3 root):

    python -m trading_bot_v2.optimization.backfill_median_arm \\
        --mode prequential \\
        --report out/monthly/composite_mean_reversion_2026-07.json \\
        --out out/monthly/median-arm-prequential-2026-07.json

    python -m trading_bot_v2.optimization.backfill_median_arm \\
        --mode cross-symbol --symbol ETH-USDC \\
        --start 2023-08-01 --end 2026-08-01 \\
        --report out/monthly/composite_mean_reversion_2026-07.json \\
        --out out/monthly/median-arm-eth.json
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any, Dict, List, Optional

#: Set in the child process so the re-exec happens exactly once.
CHILD_ENV_FLAG = "BACKFILL_MEDIAN_ARM_CHILD"


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--report", required=True,
                   help="Composite tuning report JSON to backfill")
    p.add_argument("--mode", default="prequential",
                   choices=("prequential", "cross-symbol"))
    p.add_argument("--symbol", default=None,
                   help="cross-symbol mode: symbol to score on")
    p.add_argument("--start", default=None,
                   help="cross-symbol mode: window start (inclusive)")
    p.add_argument("--end", default=None,
                   help="cross-symbol mode: window end (exclusive)")
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--directional-gate", default="enforce",
                   choices=("off", "log", "enforce"))
    p.add_argument("--log-level", default="WARNING")
    p.add_argument("--out", default=None, help="Write JSON results here")
    return p.parse_args(argv)


def _reexec_in_child(strategy: str, argv: List[str]) -> int:
    """Re-run this module with the composite-tuning env in place.

    Args:
        strategy: Strategy the report tuned, used to build the
            admission lists.
        argv: Arguments to forward unchanged.

    Returns:
        The child's exit code.
    """
    from .monthly_retune import _retune_env

    env = dict(os.environ, **_retune_env(strategy))
    env[CHILD_ENV_FLAG] = "1"
    cmd = [sys.executable, "-m", __spec__.name] + list(argv)
    print(f"[backfill] re-exec with "
          f"{', '.join(f'{k}={v}' for k, v in sorted(_retune_env(strategy).items()))}")
    return subprocess.run(cmd, env=env).returncode


def _fold_winner_params(fold: Dict[str, Any], state: str) -> Optional[Dict]:
    """The stored winner vector for one state on one fold, if any."""
    cell = fold.get("states", {}).get(state) or {}
    params = cell.get("params")
    return dict(params) if params else None


def _cell_score(fold: Dict[str, Any], state: str, key: str) -> Optional[float]:
    cell = fold.get("states", {}).get(state) or {}
    return (cell.get(key) or {}).get("score")


def _cell_n(fold: Dict[str, Any], state: str, key: str) -> Optional[int]:
    cell = fold.get("states", {}).get(state) or {}
    return (cell.get(key) or {}).get("n")


def _mean(values: List[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def score_prequential(
    adapter: Any, report: Dict[str, Any], ns: Namespace,
    adopted: Dict[str, Dict[str, float]],
) -> List[Dict[str, Any]]:
    """Score the leak-free median arm fold by fold.

    For fold k the median comes from the winners of folds 1..k-1 and is
    scored on fold k's test window. This function appends fold k's own
    winner to the accumulator only AFTER that fold has been scored,
    which is the entire correctness property.

    Args:
        adapter: OptimizationAdapter.
        report: Parsed tuning report.
        ns: Namespace carrying strategy/symbol/objective/capital.
        adopted: Currently adopted per-state params, scored alongside.

    Returns:
        One record per (fold, state) that had a median arm.
    """
    from .run_composite_tuning import _arm_state_scores, prequential_medians

    states = report["states"]
    prior: Dict[str, List[Dict[str, Any]]] = {}
    rows: List[Dict[str, Any]] = []
    for fold_no, fold in enumerate(report["folds"], 1):
        te_s, te_e = fold["test"]
        cache: Dict[str, Any] = {}
        medians = prequential_medians(prior, states)
        med_scores = _arm_state_scores(adapter, cache, ns, medians, te_s, te_e)
        adopted_scores = _arm_state_scores(
            adapter, cache, ns, adopted, te_s, te_e)
        for state in states:
            if state in medians:
                rows.append({
                    "fold": fold_no,
                    "test": [te_s, te_e],
                    "state": state,
                    "median_params": medians[state],
                    "median_from_folds": len(prior[state]),
                    "test_median": med_scores[state],
                    "test_adopted": adopted_scores.get(state),
                    "test_tuned": _cell_score(fold, state, "test_tuned"),
                    "test_tuned_n": _cell_n(fold, state, "test_tuned"),
                    "test_default": _cell_score(fold, state, "test_default"),
                    "test_default_n": _cell_n(fold, state, "test_default"),
                })
            winner = _fold_winner_params(fold, state)
            if winner:
                prior.setdefault(state, []).append(winner)
        print(f"[backfill] fold {fold_no} scored "
              f"{sum(1 for r in rows if r['fold'] == fold_no)} median cells "
              f"({te_s}..{te_e})", flush=True)
    return rows


def aggregate_prequential(
    rows: List[Dict[str, Any]], report: Dict[str, Any]
) -> Dict[str, Any]:
    """Per-state paired means over the folds that carried a median arm.

    Args:
        rows: Output of ``score_prequential``.
        report: Parsed tuning report (for the fold-winner summary).

    Returns:
        State -> aggregate, including the gap between the fold-winner
        arm and the median arm on the SAME folds.
    """
    out: Dict[str, Any] = {}
    for state in report["states"]:
        cells = [r for r in rows if r["state"] == state
                 and r["test_median"]["score"] is not None]
        if not cells:
            out[state] = {"median_folds": 0}
            continue
        med = [c["test_median"]["score"] for c in cells]
        tuned = [c["test_tuned"] for c in cells if c["test_tuned"] is not None]
        dflt = [c["test_default"] for c in cells
                if c["test_default"] is not None]
        adopted = [c["test_adopted"]["score"] for c in cells
                   if c.get("test_adopted")
                   and c["test_adopted"]["score"] is not None]
        entry = {
            "median_folds": len(med),
            "median": _mean(med),
            "median_trades": [c["test_median"]["n"] for c in cells],
            "tuned_same_folds": _mean(tuned),
            "default_same_folds": _mean(dflt),
            "adopted_same_folds": _mean(adopted) if adopted else None,
            "full_report_tuned_mean": (report.get("summary") or {})
            .get(state, {}).get("tuned"),
            "full_report_default_mean": (report.get("summary") or {})
            .get(state, {}).get("default"),
            "full_report_edge": (report.get("summary") or {})
            .get(state, {}).get("edge"),
        }
        entry["gap_median_minus_tuned"] = (
            None if entry["tuned_same_folds"] is None
            else entry["median"] - entry["tuned_same_folds"])
        entry["edge_median_vs_default"] = (
            None if entry["default_same_folds"] is None
            else entry["median"] - entry["default_same_folds"])
        entry["edge_median_vs_adopted"] = (
            None if entry["adopted_same_folds"] is None
            else entry["median"] - entry["adopted_same_folds"])
        out[state] = entry
    return out


def median_geometry(
    report: Dict[str, Any], space: Dict[str, Any]
) -> Dict[str, Any]:
    """Is the deployed median a vector anyone actually tuned?

    A coordinate-wise median is trivially inside each parameter's own
    min/max, so that check can only fail on a bug. The informative
    number is the distance from the median to the NEAREST fold winner,
    measured in search-space-normalised units, against the typical
    distance between two winners. A median that is no closer to any
    winner than winners are to each other is a point in a region nobody
    sampled.

    Args:
        report: Parsed tuning report.
        space: Search space, used to normalise each coordinate.

    Returns:
        State -> geometry record.
    """
    out: Dict[str, Any] = {}
    for state in report["states"]:
        winners = [w for w in (_fold_winner_params(f, state)
                               for f in report["folds"]) if w]
        if len(winners) < 2:
            out[state] = {"winners": len(winners)}
            continue
        keys = sorted(winners[0])
        median = {k: round(statistics.median([w[k] for w in winners]), 4)
                  for k in keys}
        in_range = all(
            min(w[k] for w in winners) <= median[k] <= max(w[k] for w in winners)
            for k in keys
        )
        dists = [_normalised_distance(median, w, space, keys) for w in winners]
        pairwise = [
            _normalised_distance(a, b, space, keys)
            for i, a in enumerate(winners) for b in winners[i + 1:]
        ]
        out[state] = {
            "winners": len(winners),
            "full_report_median": median,
            "median_within_winner_range": in_range,
            "nearest_winner_distance": round(min(dists), 4),
            "mean_distance_to_winners": round(_mean(dists), 4),
            "mean_pairwise_winner_distance": round(_mean(pairwise), 4),
            "unlike_any_winner": min(dists) >= _mean(pairwise),
        }
    return out


def _normalised_distance(
    a: Dict[str, float], b: Dict[str, float],
    space: Dict[str, Any], keys: List[str],
) -> float:
    """Euclidean distance with each axis scaled to its search range."""
    total = 0.0
    for k in keys:
        spec = space.get(k)
        span = (float(spec[1]) - float(spec[0])) if isinstance(spec, tuple) else 1.0
        if span == 0:
            continue
        total += ((a[k] - b[k]) / span) ** 2
    return total ** 0.5


def run_cross_symbol(
    adapter: Any, report: Dict[str, Any], ns: Namespace,
    adopted: Dict[str, Dict[str, float]], start: str, end: str,
) -> Dict[str, Any]:
    """Score defaults, fresh medians and adopted params on one symbol.

    Args:
        adapter: OptimizationAdapter.
        report: Parsed tuning report (source of the fresh medians).
        ns: Namespace carrying strategy/symbol/objective/capital.
        adopted: Currently adopted per-state params.
        start: Window start (inclusive).
        end: Window end (exclusive).

    Returns:
        State -> the three arms' scores and trade counts.
    """
    from .monthly_retune import _fresh_medians
    from .run_composite_tuning import _arm_state_scores, _state_scores

    states = report["states"]
    cache: Dict[str, Any] = {}
    medians = {s: p for s, p in _fresh_medians(report).items() if s in states}
    default_result = adapter.run_backtest(
        ns.strategy, {}, start, end, symbol=ns.symbol,
        initial_capital=ns.capital,
    )
    defaults = _state_scores(
        adapter, default_result, states, ns.objective, ns.capital, start, end)
    print(f"[backfill] {ns.symbol}: defaults scored", flush=True)
    fresh = _arm_state_scores(adapter, cache, ns, medians, start, end)
    print(f"[backfill] {ns.symbol}: fresh medians scored", flush=True)
    adopted_scores = _arm_state_scores(adapter, cache, ns, adopted, start, end)
    print(f"[backfill] {ns.symbol}: adopted scored", flush=True)
    out: Dict[str, Any] = {}
    for state in states:
        entry: Dict[str, Any] = {
            "default": defaults[state],
            "fresh_median_params": medians.get(state),
            "fresh_median": fresh.get(state),
            "adopted_params": adopted.get(state),
            "adopted": adopted_scores.get(state),
        }
        entry["fresh_vs_default"] = _delta(entry["fresh_median"],
                                           entry["default"])
        entry["adopted_vs_default"] = _delta(entry["adopted"],
                                             entry["default"])
        out[state] = entry
    return out


def _delta(arm: Optional[Dict[str, Any]],
           ref: Optional[Dict[str, Any]]) -> Optional[float]:
    """Score difference between two arms, or None when either is absent."""
    if not arm or not ref:
        return None
    a, r = arm.get("score"), ref.get("score")
    return None if a is None or r is None else a - r


def _build_namespace(report: Dict[str, Any], args: argparse.Namespace,
                     symbol: str) -> Namespace:
    return Namespace(
        strategy=report["strategy"], symbol=symbol,
        objective=report["objective"], capital=args.capital,
    )


def _print_prequential(agg: Dict[str, Any], geo: Dict[str, Any]) -> None:
    print(f"\n{'=' * 104}")
    print("PREQUENTIAL MEDIAN ARM (median of folds 1..k-1, scored on "
          "fold k's unseen test window)")
    print(f"{'=' * 104}")
    print(f"  {'state':>16} {'folds':>6} {'median':>9} {'tuned':>9} "
          f"{'default':>9} {'adopted':>9} {'med-tuned':>10} {'med-def':>9} "
          f"{'med-adopt':>10}")
    for state, row in agg.items():
        if not row.get("median_folds"):
            print(f"  {state:>16} {0:>6}   (no median arm)")
            continue
        print(f"  {state:>16} {row['median_folds']:>6} "
              f"{row['median']:>+9.3f} {_f(row['tuned_same_folds']):>9} "
              f"{_f(row['default_same_folds']):>9} "
              f"{_f(row['adopted_same_folds']):>9} "
              f"{_f(row['gap_median_minus_tuned']):>10} "
              f"{_f(row['edge_median_vs_default']):>9} "
              f"{_f(row['edge_median_vs_adopted']):>10}")
    print("\nGEOMETRY of the full-report median vs the winners it came from")
    for state, row in geo.items():
        if row.get("winners", 0) < 2:
            continue
        print(f"  {state:>16} in-range={row['median_within_winner_range']} "
              f"nearest_winner={row['nearest_winner_distance']} "
              f"mean_pairwise={row['mean_pairwise_winner_distance']} "
              f"unlike_any_winner={row['unlike_any_winner']}")


def _f(value: Optional[float]) -> str:
    return "n/a" if value is None else f"{value:+.3f}"


def _print_cross_symbol(symbol: str, result: Dict[str, Any]) -> None:
    print(f"\n{'=' * 104}")
    print(f"CROSS-SYMBOL: {symbol}")
    print(f"{'=' * 104}")
    print(f"  {'state':>16} {'default':>9} {'n':>5} {'fresh med':>10} "
          f"{'n':>5} {'adopted':>9} {'n':>5} {'fresh-def':>10} "
          f"{'adopt-def':>10}")
    for state, row in result.items():
        fresh = row.get("fresh_median") or {}
        adopted = row.get("adopted") or {}
        print(f"  {state:>16} {_f(row['default']['score']):>9} "
              f"{row['default']['n']:>5} {_f(fresh.get('score')):>10} "
              f"{str(fresh.get('n', '-')):>5} "
              f"{_f(adopted.get('score')):>9} "
              f"{str(adopted.get('n', '-')):>5} "
              f"{_f(row['fresh_vs_default']):>10} "
              f"{_f(row['adopted_vs_default']):>10}")


def _child_main(args: argparse.Namespace, report: Dict[str, Any]) -> int:
    """Do the scoring work, with the composite-tuning env already set."""
    from loguru import logger

    logger.remove()
    logger.add(sys.stderr, level=args.log_level.upper())
    from ..backtesting.optimization_adapter import OptimizationAdapter
    from .monthly_retune import ADOPTED_PARAMS
    from .search_spaces import get_search_space

    # After the config import, so it wins over load_dotenv(override=True).
    os.environ["DIRECTIONAL_GATE"] = args.directional_gate
    adapter = OptimizationAdapter()
    adopted = ADOPTED_PARAMS.get(report["strategy"], {})
    payload: Dict[str, Any] = {
        "source_report": args.report,
        "strategy": report["strategy"],
        "objective": report["objective"],
        "mode": args.mode,
        "directional_gate": args.directional_gate,
    }
    if args.mode == "prequential":
        ns = _build_namespace(report, args, report["symbol"])
        payload["symbol"] = report["symbol"]
        rows = score_prequential(adapter, report, ns, adopted)
        payload["folds"] = rows
        payload["summary"] = aggregate_prequential(rows, report)
        payload["geometry"] = median_geometry(
            report, get_search_space(report["strategy"]))
        _print_prequential(payload["summary"], payload["geometry"])
    else:
        symbol = args.symbol or report["symbol"]
        ns = _build_namespace(report, args, symbol)
        payload.update({"symbol": symbol, "window": [args.start, args.end]})
        payload["summary"] = run_cross_symbol(
            adapter, report, ns, adopted, args.start, args.end)
        _print_cross_symbol(symbol, payload["summary"])
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(
            json.dumps(payload, indent=2, default=str), encoding="utf-8")
        print(f"\nresults -> {args.out}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point; re-executes itself once with the tuning env."""
    raw = list(sys.argv[1:] if argv is None else argv)
    args = _parse_args(raw)
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    if not os.environ.get(CHILD_ENV_FLAG):
        return _reexec_in_child(report["strategy"], raw)
    return _child_main(args, report)


if __name__ == "__main__":
    sys.exit(main())
