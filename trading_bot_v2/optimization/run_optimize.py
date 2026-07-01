"""
Optimization CLI Entry Point
============================

Command-line interface for running strategy parameter optimization.

Usage:
    # Optimize a single strategy
    python -m trading_bot_v2.optimization.run_optimize --strategy mean_reversion --trials 100

    # Optimize all strategies
    python -m trading_bot_v2.optimization.run_optimize --all --trials 50

    # Walk-forward optimization
    python -m trading_bot_v2.optimization.run_optimize --strategy ma_crossover --walk-forward

    # Export results
    python -m trading_bot_v2.optimization.run_optimize --strategy grid_trading --export results.csv

    # List completed studies
    python -m trading_bot_v2.optimization.run_optimize --list
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from loguru import logger

from .optuna_runner import OptunaRunner
from .search_spaces import list_strategies


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Optuna parameter optimization for Bot 3 trading strategies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Optimize Mean Reversion with 200 trials
  python -m trading_bot_v2.optimization.run_optimize \\
    --strategy mean_reversion --trials 200

  # Walk-forward optimization for MA Crossover
  python -m trading_bot_v2.optimization.run_optimize \\
    --strategy ma_crossover --walk-forward --train-months 12 --test-months 3

  # Optimize all strategies with Random sampler
  python -m trading_bot_v2.optimization.run_optimize \\
    --all --trials 100 --sampler random

  # List all completed studies
  python -m trading_bot_v2.optimization.run_optimize --list

  # Export top 20 results
  python -m trading_bot_v2.optimization.run_optimize \\
    --strategy grid_trading --export results.csv --top 20
        """,
    )

    # Strategy selection
    strategy_group = parser.add_mutually_exclusive_group()
    strategy_group.add_argument(
        "--strategy", "-s",
        type=str,
        choices=list_strategies(),
        help="Strategy to optimize",
    )
    strategy_group.add_argument(
        "--all", "-a",
        action="store_true",
        help="Optimize all strategies sequentially",
    )
    strategy_group.add_argument(
        "--list", "-l",
        action="store_true",
        help="List completed studies",
    )

    # Optimization parameters
    parser.add_argument(
        "--trials", "-n",
        type=int,
        default=100,
        help="Number of optimization trials (default: 100)",
    )
    parser.add_argument(
        "--sampler",
        type=str,
        choices=["tpe", "random"],
        default="tpe",
        help="Sampler type (default: tpe)",
    )
    parser.add_argument(
        "--objective", "-o",
        type=str,
        choices=["sharpe_ratio", "sortino_ratio", "total_return_pct", "calmar_ratio"],
        default="sharpe_ratio",
        help="Objective metric to optimize (default: sharpe_ratio)",
    )

    # Walk-forward options
    parser.add_argument(
        "--walk-forward", "-w",
        action="store_true",
        help="Enable walk-forward validation",
    )
    parser.add_argument(
        "--train-months",
        type=int,
        default=6,
        help="Training window size in months (default: 6)",
    )
    parser.add_argument(
        "--test-months",
        type=int,
        default=1,
        help="Test window size in months (default: 1)",
    )

    # Date range
    parser.add_argument(
        "--start",
        type=str,
        help="Backtest start date (ISO format, e.g., 2024-01-01). Uses config default if not set.",
    )
    parser.add_argument(
        "--end",
        type=str,
        help="Backtest end date (ISO format, e.g., 2024-12-31). Uses config default if not set.",
    )

    # Symbol and capital
    parser.add_argument(
        "--symbol",
        type=str,
        help="Trading symbol (e.g., SUI-USDC). Uses config default if not set.",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=10000.0,
        help="Initial capital (default: 10000.0)",
    )

    # Output options
    parser.add_argument(
        "--export", "-e",
        type=str,
        help="Export results to CSV file",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of top results to show/export (default: 10)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        help="Path to SQLite database for study persistence",
    )

    # Performance options
    parser.add_argument(
        "--jobs", "-j",
        type=int,
        default=1,
        help="Number of parallel jobs (default: 1)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help="Timeout in seconds (no limit if not set)",
    )

    # Verbosity
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

    return parser.parse_args()


def setup_logging(verbose: bool = False, quiet: bool = False) -> None:
    """Configure logging based on verbosity."""
    if quiet:
        logger.remove()
        logger.add(sys.stderr, level="WARNING")
    elif verbose:
        logger.remove()
        logger.add(sys.stderr, level="DEBUG")
    else:
        logger.remove()
        logger.add(sys.stderr, level="INFO")


def print_results_summary(
    study, strategy: str, top_n: int = 10
) -> None:
    """Print a summary of optimization results."""
    print(f"\n{'='*60}")
    print(f"OPTIMIZATION RESULTS: {strategy.upper()}")
    print(f"{'='*60}")

    if study.best_trial is None:
        print("  No valid trials completed.")
        return

    print(f"\n  Best Value: {study.best_value:.4f}")
    print(f"  Best Trial: #{study.best_trial.number}")
    print("\n  Best Parameters:")
    for param, value in study.best_trial.params.items():
        if isinstance(value, float):
            print(f"    {param:30s}: {value:.6f}")
        else:
            print(f"    {param:30s}: {value}")

    # Summary statistics
    completed_trials = [t for t in study.trials if t.state.name == "COMPLETE"]
    if completed_trials:
        values = [t.value for t in completed_trials if t.value is not None and t.value != float("-inf")]
        if values:
            print("\n  Statistics:")
            print(f"    Completed trials: {len(completed_trials)}")
            print(f"    Mean value:       {sum(values)/len(values):.4f}")
            print(f"    Std deviation:    {(sum((v-sum(values)/len(values))**2 for v in values)/len(values))**0.5:.4f}")
            print(f"    Min value:        {min(values):.4f}")
            print(f"    Max value:        {max(values):.4f}")

    print(f"{'='*60}\n")


def print_studies_list(studies: list) -> None:
    """Print list of completed studies."""
    print(f"\n{'='*60}")
    print("COMPLETED OPTIMIZATION STUDIES")
    print(f"{'='*60}")

    if not studies:
        print("  No studies found.")
        print(f"{'='*60}\n")
        return

    print(f"\n  {'Study Name':<45} {'Best Value':>12} {'Trials':>8}")
    print(f"  {'-'*45} {'-'*12} {'-'*8}")

    for study in studies:
        best_str = f"{study['best_value']:.4f}" if study['best_value'] is not None else "N/A"
        print(f"  {study['study_name']:<45} {best_str:>12} {study['trial_count']:>8}")

    print(f"\n  Total: {len(studies)} studies")
    print(f"{'='*60}\n")


def run_single_strategy(
    args: argparse.Namespace, strategy: str
) -> Optional[object]:
    """Run optimization for a single strategy."""
    runner = OptunaRunner(db_path=args.db_path)

    try:
        study = runner.optimize(
            strategy=strategy,
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
            timeout=args.timeout,
            n_jobs=args.jobs,
        )

        # Print results
        print_results_summary(study, strategy, args.top)

        # Export if requested
        if args.export:
            # Add strategy name to export path if it doesn't have it
            export_path = args.export
            if not export_path.endswith(".csv"):
                export_path = f"{export_path}.csv"

            runner.export_results(strategy, export_path, args.top)

        return study

    except Exception as e:
        logger.error(f"Optimization failed for {strategy}: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return None


def run_all_strategies(args: argparse.Namespace) -> dict:
    """Run optimization for all strategies."""
    results = {}
    strategies = list_strategies()

    print(f"\nOptimizing {len(strategies)} strategies...")
    print(f"{'='*60}")

    for i, strategy in enumerate(strategies, 1):
        print(f"\n[{i}/{len(strategies)}] Optimizing {strategy}...")
        study = run_single_strategy(args, strategy)
        results[strategy] = study

    # Summary
    print(f"\n{'='*60}")
    print("ALL STRATEGIES OPTIMIZED")
    print(f"{'='*60}")

    for strategy, study in results.items():
        if study and study.best_trial:
            print(f"  {strategy:<25} Best: {study.best_value:.4f}")
        else:
            print(f"  {strategy:<25} Failed")

    print(f"{'='*60}\n")

    return results


def main() -> int:
    """Main entry point."""
    args = parse_args()
    setup_logging(args.verbose, args.quiet)

    # Handle --list
    if args.list:
        runner = OptunaRunner(db_path=args.db_path)
        studies = runner.list_studies()
        print_studies_list(studies)
        return 0

    # Handle --all
    if args.all:
        results = run_all_strategies(args)
        return 0 if all(s is not None for s in results.values()) else 1

    # Handle --strategy
    if args.strategy:
        study = run_single_strategy(args, args.strategy)
        return 0 if study is not None else 1

    # No action specified
    print("Error: Please specify --strategy, --all, or --list")
    print("Use --help for usage information")
    return 1


if __name__ == "__main__":
    sys.exit(main())
