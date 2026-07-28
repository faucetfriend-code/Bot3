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

    # CHUNKED CROSS-SYMBOL SWEEP (house style: several short windows, not
    # one long run; per-symbol results reported, never averaged away)
    python -m trading_bot_v2.optimization.run_optimize \\
        --strategy ma_crossover --chunked \\
        --symbols BTC-USDC,ETH-USDC,SUI-USDC \\
        --windows 3 --window-months 2 --trials 40

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

from .optuna_runner import (
    OptunaRunner,
    normalize_objective,
    OBJECTIVE_ALIASES,
)
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

  # Chunked cross-symbol sweep (3 x 2-month windows on BTC/ETH/SUI)
  python -m trading_bot_v2.optimization.run_optimize \\
    --strategy ma_crossover --chunked \\
    --symbols BTC-USDC,ETH-USDC,SUI-USDC \\
    --windows 3 --window-months 2 --trials 40

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
        choices=sorted(OBJECTIVE_ALIASES.keys()),
        default="sharpe_ratio",
        help="Objective metric to optimize (default: sharpe_ratio; "
             "short forms like 'sharpe' accepted)",
    )

    # Regime-conditional optimization (P4)
    parser.add_argument(
        "--regime",
        type=str,
        help="Target regime (e.g. RANGING_CALM). Scores each trial only "
             "on closed trades whose entry regime matches; trials with "
             "fewer than REGIME_OPT_MIN_TRADES matching trades are pruned.",
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
        help="After the run, persist the best params as the active "
             "overlay for (strategy, regime). Requires --regime.",
    )

    # Chunked cross-symbol sweep
    parser.add_argument(
        "--chunked",
        action="store_true",
        help="Score every trial across a SERIES of short windows and "
             "several symbols (house style) instead of one long "
             "single-symbol backtest. Uses the same window series as "
             "the validation runner and reports per-symbol results.",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="BTC-USDC,ETH-USDC,SUI-USDC",
        help="Comma-separated symbols for --chunked "
             "(default: BTC-USDC,ETH-USDC,SUI-USDC)",
    )
    parser.add_argument(
        "--windows",
        type=int,
        default=3,
        help="Number of chunk windows for --chunked (default: 3)",
    )
    parser.add_argument(
        "--window-months",
        type=int,
        default=2,
        help="Months per chunk window for --chunked (default: 2)",
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
        help="Backtest end date (ISO format, e.g., 2024-12-31). Uses config "
             "default if not set. With --chunked this anchors the newest "
             "window (only ever moves it earlier).",
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


def get_best_trial_or_none(study):
    """Return study.best_trial, or None when no trial completed.

    Optuna raises ValueError from best_trial when every trial was
    pruned/failed (possible with regime min-trades pruning).
    """
    try:
        return study.best_trial
    except ValueError:
        return None


def save_overlay_from_study(
    study,
    strategy: str,
    regime: str,
    objective: str,
) -> bool:
    """Persist the best trial of a regime study as the active overlay.

    Args:
        study: Completed Optuna study.
        strategy: Snake_case strategy key.
        regime: Target regime (any accepted form).
        objective: Objective metric the study optimized (any form).

    Returns:
        True when an overlay was saved, False when the study had no
        valid best trial.
    """
    from ..database import DatabaseManager
    from ..regime_param_overlay import normalize_regime_value

    best_trial = get_best_trial_or_none(study)
    if best_trial is None or not best_trial.params:
        logger.warning(
            f"No valid best trial for {strategy}/{regime} - overlay not saved"
        )
        return False

    regime_value = normalize_regime_value(regime)
    trade_count = best_trial.user_attrs.get("regime_trade_count")

    db = DatabaseManager()
    row_id = db.save_regime_param_overlay(
        strategy=strategy,
        regime=regime_value,
        params=dict(best_trial.params),
        objective=normalize_objective(objective),
        objective_value=study.best_value,
        trade_count=trade_count,
        study_name=study.study_name,
    )
    logger.info(
        f"Saved overlay id={row_id} for ({strategy}, {regime_value}): "
        f"value={study.best_value:.4f}, trades={trade_count}, "
        f"params={best_trial.params}"
    )
    return True


def deepest_trial(study):
    """Return the trial that got furthest down the signal funnel.

    Used to explain a study where nothing traded: the trial with the
    highest recorded funnel progress is the most informative one, since
    it names the stage that actually blocked the search.

    Args:
        study: An Optuna study.

    Returns:
        The deepest trial, or None when no trial recorded a funnel.
    """
    best = None
    best_progress = -1.0
    for trial in getattr(study, "trials", []) or []:
        attrs = getattr(trial, "user_attrs", None) or {}
        if "funnel" not in attrs:
            continue
        try:
            progress = float(attrs.get("progress", 0.0) or 0.0)
        except (TypeError, ValueError):
            progress = 0.0
        if progress > best_progress:
            best = trial
            best_progress = progress
    return best


def trial_funnel_payload(trial) -> dict:
    """Rebuild a funnel report payload from a trial's user_attrs.

    Args:
        trial: An Optuna trial annotated by OptunaRunner._score_trial.

    Returns:
        A payload accepted by diagnostics.report (possibly empty).
    """
    attrs = getattr(trial, "user_attrs", None) or {}
    if "funnel" not in attrs and "outcome" not in attrs:
        return {}
    return {
        "label": f"trial #{getattr(trial, 'number', '?')}",
        "stages": attrs.get("funnel") or {},
        "by_strategy": attrs.get("by_strategy") or {},
        "regimes": attrs.get("regimes") or {},
        "notes": (
            {"gate_metrics": attrs["gate_metrics"]}
            if attrs.get("gate_metrics")
            else {}
        ),
        "diagnosis": attrs.get("outcome", ""),
        "headline": attrs.get("headline", ""),
        "binding_stage": attrs.get("binding_stage", ""),
        "progress": attrs.get("progress", 0.0),
        "top_reasons": attrs.get("top_reasons") or [],
        "suggested_fix": attrs.get("suggested_fix", ""),
    }


def print_per_symbol_table(trial) -> None:
    """Print a trial's per-symbol breakdown, when it has one.

    Aggregating a sweep to a single number hides the failure mode this
    strategy actually has: one symbol carrying the average while the
    others lose money. Cross-symbol consistency is a real gate check
    (validation/gate.py), so it is printed, not summarised away.

    Args:
        trial: An Optuna trial annotated by _chunked_objective.
    """
    attrs = getattr(trial, "user_attrs", None) or {}
    per_symbol = attrs.get("per_symbol") or {}
    if not per_symbol:
        return

    print("\n  PER-SYMBOL BREAKDOWN")
    print(
        f"    {'Symbol':<14} {'Bars':>9} {'Raw sig':>9} {'Trades':>8} "
        f"{'Objective':>12} {'Return %':>10}"
    )
    print(f"    {'-' * 66}")
    blind = []
    for symbol in sorted(per_symbol):
        cell = per_symbol[symbol] or {}
        if not cell.get("invoked", 0):
            blind.append(symbol)
        print(
            f"    {symbol:<14} {cell.get('invoked', 0):>9} "
            f"{cell.get('raw_signals', 0):>9} {cell.get('trades', 0):>8} "
            f"{cell.get('objective', 0.0):>12.4f} "
            f"{cell.get('return_pct', 0.0):>10.2f}"
        )
    print(f"    {'-' * 66}")
    print(
        f"    total trades: {attrs.get('total_trades', 0)} | "
        f"symbols that traded: {attrs.get('traded_symbols', 0)}"
        f"/{len(per_symbol)} | "
        f"symbols with positive objective: "
        f"{attrs.get('profitable_symbols', 0)}/{len(per_symbol)}"
    )
    if blind:
        # Zero bars is a DATA problem, not a parameter result - saying so
        # here stops it being read as "the strategy found nothing".
        print(
            f"    WARNING: {', '.join(blind)} contributed 0 bars - the "
            f"strategy's timeframe has no candles in these windows. "
            f"Re-anchor with --end or backfill the data; their rows above "
            f"are not evidence about the parameters."
        )


def print_results_summary(
    study, strategy: str, top_n: int = 10
) -> None:
    """Print a summary of optimization results."""
    from ..diagnostics.report import print_funnel_report

    print(f"\n{'='*60}")
    print(f"OPTIMIZATION RESULTS: {strategy.upper()}")
    print(f"{'='*60}")

    best = get_best_trial_or_none(study)
    if best is None:
        # A bare "nothing completed" is exactly the message that cost
        # multi-hour manual investigations. Show the funnel of the trial
        # that got furthest instead.
        probe = deepest_trial(study)
        if probe is None:
            print("  No valid trials completed (all pruned or failed).")
            print(f"{'='*60}\n")
            return
        print(
            f"  No valid trials completed - explaining trial "
            f"#{probe.number}, the one that got furthest:"
        )
        print(f"{'='*60}")
        print_funnel_report(
            trial_funnel_payload(probe),
            title=f"{strategy} (deepest trial #{probe.number})",
            params=getattr(probe, "params", None),
        )
        print_per_symbol_table(probe)
        return

    # Shown on success too: where the largest attrition happened is
    # actionable even at a good objective value.
    payload = trial_funnel_payload(best)
    if payload:
        print_funnel_report(
            payload,
            title=f"{strategy} (best trial #{best.number})",
            params=getattr(best, "params", None),
        )

    print_per_symbol_table(best)

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

    save_overlay = getattr(args, "save_overlay", False)
    regime = getattr(args, "regime", None)
    if save_overlay and not regime:
        logger.error("--save-overlay requires --regime")
        return None

    try:
        if getattr(args, "chunked", False):
            if regime:
                logger.error("--chunked and --regime are not compatible")
                return None
            symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
            if not symbols:
                logger.error("--chunked requires at least one --symbols entry")
                return None
            study = runner.optimize_chunked(
                strategy=strategy,
                symbols=symbols,
                n_trials=args.trials,
                sampler=args.sampler,
                objective=args.objective,
                window_months=args.window_months,
                n_windows=args.windows,
                initial_capital=args.capital,
                timeout=args.timeout,
                end=args.end,
            )
            print_results_summary(study, strategy, args.top)
            if args.export:
                export_path = args.export
                if not export_path.endswith(".csv"):
                    export_path = f"{export_path}.csv"
                runner.export_results(strategy, export_path, args.top)
            return study

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
            regime=regime,
            min_regime_trades=getattr(args, "min_trades", None),
        )

        # Print results
        print_results_summary(study, strategy, args.top)

        # Persist best params as the active overlay for (strategy, regime)
        if save_overlay:
            save_overlay_from_study(
                study, strategy, regime, args.objective
            )

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
