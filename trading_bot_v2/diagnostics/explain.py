"""
Explain CLI
===========

Reads the funnel diagnostics that OptunaRunner stores on each trial and
renders them. Zero new storage: everything comes from
``trial.user_attrs``.

Usage:
    python -m trading_bot_v2.diagnostics.explain --study mean_reversion_20260728_101500
    python -m trading_bot_v2.diagnostics.explain --strategy momentum_scalping
    python -m trading_bot_v2.diagnostics.explain --study <name> --trial 17
    python -m trading_bot_v2.diagnostics.explain --study <name> --all
"""

import argparse
import sys
from typing import Any, Dict, List, Optional

from .report import print_funnel_report

#: user_attr keys written by OptunaRunner._score_trial.
FUNNEL_ATTRS = (
    "outcome",
    "headline",
    "binding_stage",
    "funnel",
    "top_reasons",
    "by_strategy",
    "regimes",
    "progress",
    "suggested_fix",
    "gate_metrics",
)


def payload_from_trial(trial: Any) -> Dict[str, Any]:
    """Rebuild a report payload from a trial's stored attributes.

    Args:
        trial: An Optuna trial (frozen trials work too).

    Returns:
        A payload for diagnostics.report, or {} when the trial predates
        funnel instrumentation.
    """
    attrs = getattr(trial, "user_attrs", None) or {}
    if not any(key in attrs for key in FUNNEL_ATTRS):
        return {}
    return {
        "label": f"trial #{getattr(trial, 'number', '?')}",
        "stages": attrs.get("funnel") or {},
        "by_strategy": attrs.get("by_strategy") or {},
        "regimes": attrs.get("regimes") or {},
        "notes": (
            {"gate_metrics": attrs["gate_metrics"]} if attrs.get("gate_metrics") else {}
        ),
        "diagnosis": attrs.get("outcome", ""),
        "headline": attrs.get("headline", ""),
        "binding_stage": attrs.get("binding_stage", ""),
        "progress": attrs.get("progress", 0.0),
        "top_reasons": attrs.get("top_reasons") or [],
        "suggested_fix": attrs.get("suggested_fix", ""),
    }


def rank_trials(trials: List[Any]) -> List[Any]:
    """Order trials most-informative first.

    Traded trials come first (by value), then non-traded ones by funnel
    depth, so the head of the list always explains the study best.

    Args:
        trials: Trials to rank.

    Returns:
        A new, ordered list.
    """

    def key(trial: Any):
        attrs = getattr(trial, "user_attrs", None) or {}
        traded = 1 if attrs.get("outcome") == "traded" else 0
        try:
            progress = float(attrs.get("progress", 0.0) or 0.0)
        except (TypeError, ValueError):
            progress = 0.0
        value = getattr(trial, "value", None)
        return (traded, progress, value if value is not None else float("-inf"))

    return sorted(trials, key=key, reverse=True)


def summarise(trials: List[Any]) -> Dict[str, int]:
    """Count trials by recorded outcome.

    Args:
        trials: Trials to summarise.

    Returns:
        Outcome name -> count (``unknown`` for uninstrumented trials).
    """
    counts: Dict[str, int] = {}
    for trial in trials:
        attrs = getattr(trial, "user_attrs", None) or {}
        name = str(attrs.get("outcome") or "unknown")
        counts[name] = counts.get(name, 0) + 1
    return counts


def _load_study(study_name: str, db_path: Optional[str]):
    """Load an Optuna study from the optimizer's storage.

    Args:
        study_name: Exact study name.
        db_path: Optional path to the study database.

    Returns:
        The loaded study.
    """
    import optuna

    if db_path is None:
        from pathlib import Path

        from ..optimization.optuna_runner import DEFAULT_DB_PATH

        db_path = str(Path(__file__).parent.parent / "optimization" / DEFAULT_DB_PATH)
    return optuna.load_study(study_name=study_name, storage=f"sqlite:///{db_path}")


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Process exit code (0 on success, 1 when nothing was found).
    """
    parser = argparse.ArgumentParser(
        prog="python -m trading_bot_v2.diagnostics.explain",
        description=("Explain why an optimization study's trials did or did not trade"),
    )
    parser.add_argument("--study", help="Exact study name")
    parser.add_argument("--strategy", help="Strategy key - explains its latest study")
    parser.add_argument("--db", default=None, help="Study database path")
    parser.add_argument(
        "--trial", type=int, default=None, help="Explain one trial by number"
    )
    parser.add_argument("--top", type=int, default=1, help="How many trials to explain")
    parser.add_argument(
        "--all", action="store_true", help="Explain every instrumented trial"
    )
    args = parser.parse_args(argv)

    if not args.study and not args.strategy:
        parser.error("one of --study or --strategy is required")

    study_name = args.study
    if study_name is None:
        from ..optimization.optuna_runner import OptunaRunner

        runner = OptunaRunner(db_path=args.db)
        study_name = runner._find_latest_study(args.strategy)
        if study_name is None:
            print(f"No study found for strategy '{args.strategy}'")
            return 1

    try:
        study = _load_study(study_name, args.db)
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"Could not load study '{study_name}': {exc}")
        return 1

    trials = list(study.trials)
    print(f"\nSTUDY: {study_name}  ({len(trials)} trials)")
    counts = summarise(trials)
    for name in sorted(counts, key=lambda k: -counts[k]):
        print(f"  {name:<28} {counts[name]}")

    if args.trial is not None:
        selected = [t for t in trials if getattr(t, "number", None) == args.trial]
        if not selected:
            print(f"No trial #{args.trial} in this study")
            return 1
    else:
        ranked = [t for t in rank_trials(trials) if payload_from_trial(t)]
        if not ranked:
            print(
                "\nNo funnel diagnostics stored on this study "
                "(it predates the diagnostics package)."
            )
            return 1
        selected = ranked if args.all else ranked[: max(1, args.top)]

    for trial in selected:
        payload = payload_from_trial(trial)
        if not payload:
            print(f"\nTrial #{trial.number}: no diagnostics recorded")
            continue
        value = getattr(trial, "value", None)
        value_s = f"{value:.4f}" if isinstance(value, float) else str(value)
        print_funnel_report(
            payload,
            title=(
                f"{study_name} trial #{trial.number} "
                f"(value {value_s}, state {getattr(trial.state, 'name', '?')})"
            ),
            params=getattr(trial, "params", None),
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
