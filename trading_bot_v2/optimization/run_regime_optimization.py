"""
Regime-Conditional Optimization Orchestrator (P4)
=================================================

Runs one regime-conditional Optuna study per requested regime for a
strategy, prints a per-regime comparison table of the best parameters,
and optionally persists the winners as runtime overlays.

Usage:
    python -m trading_bot_v2.optimization.run_regime_optimization \\
        --strategy mean_reversion \\
        --regimes RANGING_CALM,RANGING_VOLATILE \\
        --symbol SUI-USDC --start 2024-01-01 --end 2025-01-01 \\
        --trials 30 --objective sharpe [--save-overlay]

Notes:
    - Each trial is scored ONLY on closed trades whose entry regime
      matches the target regime (full-window backtest, or walk-forward
      with --walk-forward).
    - Trials with fewer than REGIME_OPT_MIN_TRADES (env, default 15)
      matching trades are pruned.
    - Overlays saved with --save-overlay stay INERT at runtime until
      ENABLE_REGIME_PARAM_OVERLAYS=true is set.
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger

from .optuna_runner import OptunaRunner, OBJECTIVE_ALIASES
from .run_optimize import (
    get_best_trial_or_none,
    save_overlay_from_study,
    setup_logging,
)
from .search_spaces import get_search_space, list_strategies


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Optional argument list (defaults to sys.argv).

    Returns:
        Parsed argparse namespace.
    """
    parser = argparse.ArgumentParser(
        description="Per-regime parameter optimization for Bot 3 strategies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--strategy", "-s",
        type=str,
        required=True,
        choices=list_strategies(),
        help="Strategy to optimize",
    )
    parser.add_argument(
        "--regimes", "-r",
        type=str,
        required=True,
        help="Comma-separated regimes (e.g. RANGING_CALM,RANGING_VOLATILE)",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        help="Trading symbol (e.g. SUI-USDC). Uses config default if not set.",
    )
    parser.add_argument(
        "--start",
        type=str,
        help="Backtest start date (ISO). Uses config default if not set.",
    )
    parser.add_argument(
        "--end",
        type=str,
        help="Backtest end date (ISO). Uses config default if not set.",
    )
    parser.add_argument(
        "--trials", "-n",
        type=int,
        default=30,
        help="Trials per regime (default: 30)",
    )
    parser.add_argument(
        "--objective", "-o",
        type=str,
        choices=sorted(OBJECTIVE_ALIASES.keys()),
        default="sharpe",
        help="Objective metric (default: sharpe)",
    )
    parser.add_argument(
        "--sampler",
        type=str,
        choices=["tpe", "random"],
        default="tpe",
        help="Sampler type (default: tpe)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=10000.0,
        help="Initial capital (default: 10000.0)",
    )
    parser.add_argument(
        "--walk-forward", "-w",
        action="store_true",
        help="Enable walk-forward validation",
    )
    parser.add_argument(
        "--train-months",
        type=int,
        default=6,
        help="Walk-forward training window in months (default: 6)",
    )
    parser.add_argument(
        "--test-months",
        type=int,
        default=1,
        help="Walk-forward test window in months (default: 1)",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        help="Minimum matching-regime trades per trial before pruning "
             "(default: env REGIME_OPT_MIN_TRADES or 15)",
    )
    parser.add_argument(
        "--save-overlay",
        action="store_true",
        help="Persist best params per regime as active overlays",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        help="Path to SQLite database for study persistence",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help=(
            "Run-level BASE seed. Each study's Optuna seed is derived "
            "from it plus that study's own identity (strategy, symbol, "
            "regime, objective, window - see optuna_runner.study_seed), "
            "so the regimes compared here search independently while "
            "pinning this still reproduces the whole run exactly."
        ),
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress most output",
    )
    return parser.parse_args(argv)


def _summarize_study(study: Any) -> Dict[str, Any]:
    """Collect the reportable facts about a completed regime study.

    Args:
        study: Optuna study (possibly with zero completed trials).

    Returns:
        Dict with study_name, best_value, best_params, trade_count,
        completed and pruned trial counts.
    """
    best_trial = get_best_trial_or_none(study)
    completed = sum(1 for t in study.trials if t.state.name == "COMPLETE")
    pruned = sum(1 for t in study.trials if t.state.name == "PRUNED")
    return {
        "study_name": study.study_name,
        "best_value": study.best_value if best_trial is not None else None,
        "best_params": dict(best_trial.params) if best_trial is not None else {},
        "trade_count": (
            best_trial.user_attrs.get("regime_trade_count")
            if best_trial is not None
            else None
        ),
        "completed": completed,
        "pruned": pruned,
        "total": len(study.trials),
    }


def print_comparison_table(
    strategy: str, objective: str, summaries: Dict[str, Dict[str, Any]]
) -> None:
    """Print a per-regime comparison of best values and parameters.

    Args:
        strategy: Strategy name.
        objective: Objective metric name.
        summaries: Regime -> summary dict from _summarize_study.
    """
    regimes = list(summaries.keys())
    param_names = sorted(get_search_space(strategy).keys())

    print(f"\n{'='*78}")
    print(f"REGIME OPTIMIZATION COMPARISON: {strategy} (objective: {objective})")
    print(f"{'='*78}")

    header = f"  {'metric':<30}" + "".join(f"{r:>22}" for r in regimes)
    print(header)
    print(f"  {'-'*30}" + "".join(f" {'-'*21}" for _ in regimes))

    def _fmt(value: Any) -> str:
        if value is None:
            return "n/a"
        if isinstance(value, float):
            return f"{value:.4f}"
        return str(value)

    rows = [
        ("best_value", [s["best_value"] for s in summaries.values()]),
        ("regime_trade_count", [s["trade_count"] for s in summaries.values()]),
        (
            "trials (complete/pruned)",
            [
                f"{s['completed']}/{s['pruned']} of {s['total']}"
                for s in summaries.values()
            ],
        ),
    ]
    for param in param_names:
        rows.append(
            (
                param,
                [s["best_params"].get(param) for s in summaries.values()],
            )
        )

    for label, values in rows:
        line = f"  {label:<30}" + "".join(f"{_fmt(v):>22}" for v in values)
        print(line)

    print(f"{'='*78}")
    for regime, summary in summaries.items():
        print(f"  {regime}: study={summary['study_name']}")
    print(f"{'='*78}\n")


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point.

    Args:
        argv: Optional argument list (defaults to sys.argv).

    Returns:
        Process exit code (0 when at least one regime produced a valid
        best trial, 1 otherwise).
    """
    args = parse_args(argv)
    setup_logging(args.verbose, args.quiet)

    regimes = [r.strip() for r in args.regimes.split(",") if r.strip()]
    if not regimes:
        logger.error("No regimes given")
        return 1

    runner = OptunaRunner(db_path=args.db_path, seed=args.seed)
    summaries: Dict[str, Dict[str, Any]] = {}

    for i, regime in enumerate(regimes, 1):
        print(
            f"\n[{i}/{len(regimes)}] Optimizing {args.strategy} for "
            f"regime {regime} ({args.trials} trials)..."
        )
        try:
            study = runner.optimize(
                strategy=args.strategy,
                n_trials=args.trials,
                sampler=args.sampler,
                objective=args.objective,
                start=args.start,
                end=args.end,
                symbol=args.symbol,
                initial_capital=args.capital,
                walk_forward=args.walk_forward,
                train_months=args.train_months,
                test_months=args.test_months,
                regime=regime,
                min_regime_trades=args.min_trades,
            )
        except Exception as e:
            logger.error(f"Optimization failed for regime {regime}: {e}")
            if args.verbose:
                import traceback

                traceback.print_exc()
            continue

        summaries[regime.upper()] = _summarize_study(study)

        if args.save_overlay:
            saved = save_overlay_from_study(
                study, args.strategy, regime, args.objective
            )
            if saved:
                print(f"  Overlay saved for ({args.strategy}, {regime})")
            else:
                print(
                    f"  Overlay NOT saved for regime {regime} - no valid "
                    f"best trial, or refused because the best trial lost "
                    f"money (see log for the objective value)"
                )

    if not summaries:
        print("No regime studies completed.")
        return 1

    print_comparison_table(args.strategy, args.objective, summaries)

    any_valid = any(s["best_value"] is not None for s in summaries.values())
    return 0 if any_valid else 1


if __name__ == "__main__":
    sys.exit(main())
