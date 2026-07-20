"""
Walk-Forward Analysis
=====================

Splits the full date range into rolling train/test windows.
For each window: trains on N months, tests on M months, steps forward
M months. Default: 6-month train, 1-month test.

Two modes:

optimize=False (legacy, default):
    Backtests each TEST window with default parameters - segmented OOS
    reporting. The aggregate OOS per-trade return series still gets a
    PSR (with n_trials=1: no search was performed).

optimize=True (P5, real walk-forward):
    For each window, runs a small Optuna study on the TRAIN window,
    takes the best parameters, applies them to the TEST window
    backtest, and aggregates the out-of-sample results. Every window's
    study records its trial count in the trial_registry, and the
    aggregate report includes PSR and DSR (deflated by the total trial
    count across windows).

Usage:
    wf = WalkForwardAnalyzer(engine, config)
    results = wf.run(start="2023-01-01", end="2024-12-31", symbol="SUI-USDC")

    report = wf.run(
        start="2024-01-01", end="2025-01-01", symbol="SUI-USDC",
        optimize=True, strategy="mean_reversion", n_trials=20,
        objective="sharpe",
    )

CLI:
    python -m trading_bot_v2.backtesting.walk_forward \\
        --strategy mean_reversion --symbol SUI-USDC \\
        --start 2024-01-01 --end 2025-01-01 \\
        --train-months 3 --test-months 1 --trials 15 --objective sharpe
"""

import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from .engine import BacktestEngine
from .performance import BacktestResult
from ..validation.statistics import (
    DSRResult,
    PSRResult,
    closed_trade_returns,
    deflated_sharpe_ratio,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
)

# Default number of Optuna trials per train window (env-overridable)
DEFAULT_WALK_FORWARD_TRIALS = int(os.getenv("WALK_FORWARD_TRIALS", "20"))


@dataclass
class WalkForwardWindow:
    """Per-window record of a walk-forward optimization run.

    Attributes:
        index: 1-based window number.
        train_start: Train window start date (ISO).
        train_end: Train window end date (ISO).
        test_start: Test window start date (ISO).
        test_end: Test window end date (ISO).
        params: Best parameters found on the train window.
        train_objective: Best objective value on the train window
            (None when every trial failed/pruned).
        test_objective: Objective value of those params on the test
            window.
        n_trials: Trials the window's study explored.
        result: Test-window BacktestResult.
    """

    index: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    params: Dict[str, Any] = field(default_factory=dict)
    train_objective: Optional[float] = None
    test_objective: Optional[float] = None
    n_trials: int = 0
    result: Optional[BacktestResult] = None


@dataclass
class WalkForwardReport:
    """Aggregate out-of-sample report of a walk-forward run.

    All OOS metrics are computed on the concatenated per-trade return
    series across TEST windows only (train windows never contribute).
    """

    strategy: str
    symbol: str
    start: str
    end: str
    objective: str
    windows: List[WalkForwardWindow] = field(default_factory=list)
    oos_returns: List[float] = field(default_factory=list)
    total_return_pct: float = 0.0
    sharpe: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    psr: Optional[PSRResult] = None
    dsr: Optional[DSRResult] = None
    n_trials_total: int = 0


class WalkForwardAnalyzer:
    def __init__(self, engine: BacktestEngine, config=None):
        self.engine = engine
        self.cfg = config or engine.cfg

    def run(
        self,
        start: str,
        end: str,
        symbol: str,
        initial_capital: float = 10000.0,
        optimize: bool = False,
        strategy: Optional[str] = None,
        n_trials: Optional[int] = None,
        objective: str = "sharpe",
        train_months: Optional[int] = None,
        test_months: Optional[int] = None,
        optuna_db_path: Optional[str] = None,
    ):
        """Run walk-forward analysis.

        Args:
            start: Full range start date (ISO).
            end: Full range end date (ISO).
            symbol: Trading symbol (e.g. "SUI-USDC").
            initial_capital: Starting capital per window.
            optimize: When True, run an Optuna study on each TRAIN
                window and apply the best params to the TEST window
                (real walk-forward). When False, legacy behavior: test
                windows are backtested with default params.
            strategy: Snake_case strategy key (required when
                optimize=True; optional strategy filter otherwise).
            n_trials: Optuna trials per train window (default: env
                WALK_FORWARD_TRIALS or 20).
            objective: Objective metric (short or long form, e.g.
                "sharpe").
            train_months: Override the config train window size.
            test_months: Override the config test window size.
            optuna_db_path: Optional Optuna study storage path
                (default: the runner's optimization_studies.db).

        Returns:
            List[BacktestResult] when optimize=False (legacy), or a
            WalkForwardReport when optimize=True.

        Raises:
            ValueError: When optimize=True and no strategy is given.
        """
        train_m = train_months or self.cfg.backtest_walk_forward_train_months
        test_m = test_months or self.cfg.backtest_walk_forward_test_months

        windows = self._build_windows(start, end, train_m, test_m)

        if optimize:
            if not strategy:
                raise ValueError("optimize=True requires a strategy name")
            return self._run_optimized(
                windows=windows,
                strategy=strategy,
                symbol=symbol,
                start=start,
                end=end,
                initial_capital=initial_capital,
                n_trials=n_trials or DEFAULT_WALK_FORWARD_TRIALS,
                objective=objective,
                optuna_db_path=optuna_db_path,
            )

        results = []
        for i, (train_start, train_end, test_start, test_end) in enumerate(windows):
            logger.info(f"Window {i+1}/{len(windows)}: test {test_start} -> {test_end}")
            result = self.engine.run(
                start=test_start,
                end=test_end,
                symbol=symbol,
                initial_capital=initial_capital,
                strategy_filter=strategy,
            )
            result.start = test_start  # Label by test window
            results.append(result)

        self.print_summary(results)

        # P5: even without optimization, report the PSR of the OOS
        # series (n_trials=1 - no parameter search happened here).
        oos_returns: List[float] = []
        for r in results:
            oos_returns.extend(closed_trade_returns(r.trade_log, initial_capital))
        psr = probabilistic_sharpe_ratio(oos_returns)
        if psr.value is not None:
            print(
                f"  OOS PSR (n_trials=1)  : {psr.value:.4f} "
                f"(per-trade SR {psr.sr:.4f}, {psr.n} trades)"
            )
        else:
            print(f"  OOS PSR               : n/a ({psr.reason})")
        print()

        return results

    # ------------------------------------------------------------------
    # Optimized (real) walk-forward
    # ------------------------------------------------------------------

    def _run_optimized(
        self,
        windows: List,
        strategy: str,
        symbol: str,
        start: str,
        end: str,
        initial_capital: float,
        n_trials: int,
        objective: str,
        optuna_db_path: Optional[str] = None,
    ) -> WalkForwardReport:
        """Train-on-window-N, test-on-window-N+1 walk-forward.

        Args:
            windows: (train_start, train_end, test_start, test_end)
                tuples from _build_windows.
            strategy: Snake_case strategy key.
            symbol: Trading symbol.
            start: Full range start (for the report header).
            end: Full range end (for the report header).
            initial_capital: Starting capital per window.
            n_trials: Optuna trials per train window.
            objective: Objective metric (any accepted form).

        Returns:
            WalkForwardReport with per-window rows and aggregate OOS
            metrics including PSR and DSR.
        """
        import optuna

        from ..optimization.optuna_runner import (
            OptunaRunner,
            normalize_objective,
        )

        canonical_objective = normalize_objective(objective)
        runner = OptunaRunner(db_path=optuna_db_path)
        adapter = runner.adapter

        report = WalkForwardReport(
            strategy=strategy,
            symbol=symbol,
            start=start,
            end=end,
            objective=canonical_objective,
        )
        trial_values: List[float] = []

        for i, (train_start, train_end, test_start, test_end) in enumerate(windows):
            logger.info(
                f"WF window {i+1}/{len(windows)}: "
                f"train {train_start} -> {train_end}, "
                f"test {test_start} -> {test_end} ({n_trials} trials)"
            )

            window = WalkForwardWindow(
                index=i + 1,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )

            # --- Optimize on the TRAIN window ---
            study = runner.optimize(
                strategy=strategy,
                n_trials=n_trials,
                objective=canonical_objective,
                start=train_start,
                end=train_end,
                symbol=symbol,
                initial_capital=initial_capital,
            )
            try:
                best_trial = study.best_trial
            except ValueError:
                best_trial = None

            state = optuna.trial.TrialState
            completed = [t for t in study.trials if t.state == state.COMPLETE]
            pruned = [t for t in study.trials if t.state == state.PRUNED]
            window.n_trials = len(completed) + len(pruned)
            trial_values.extend(
                t.value
                for t in completed
                if t.value is not None and math.isfinite(t.value)
            )

            if best_trial is not None:
                window.params = dict(best_trial.params)
                window.train_objective = best_trial.value
            else:
                logger.warning(
                    f"WF window {i+1}: no valid trial - "
                    "testing with default params"
                )

            # --- Apply best params to the TEST window ---
            result = adapter.run_backtest(
                strategy=strategy,
                params=window.params,
                start=test_start,
                end=test_end,
                symbol=symbol,
                initial_capital=initial_capital,
            )
            result.start = test_start
            window.result = result
            window.test_objective = adapter.calculate_objective(
                result, canonical_objective
            )

            report.windows.append(window)
            report.oos_returns.extend(
                closed_trade_returns(result.trade_log, initial_capital)
            )

        # --- Aggregate OOS metrics (test windows only) ---
        report.n_trials_total = sum(w.n_trials for w in report.windows)
        self._finalize_report(report, initial_capital, trial_values)
        self.print_report(report)
        return report

    def _finalize_report(
        self,
        report: WalkForwardReport,
        initial_capital: float,
        trial_values: List[float],
    ) -> None:
        """Compute aggregate OOS metrics on the concatenated trade series.

        Each test window restarts at initial_capital, so the aggregate
        return is additive (sum of pnl over the common capital base)
        rather than compounded across windows.

        Args:
            report: Report to fill in-place.
            initial_capital: Common capital base of every window.
            trial_values: All completed trial objective values across
                windows (used for the DSR variance when the objective
                is sharpe_ratio).
        """
        returns = report.oos_returns
        pnls = [r * initial_capital for r in returns]

        report.total_return_pct = sum(returns) * 100
        report.sharpe = sharpe_ratio(returns)

        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        report.profit_factor = (
            gross_profit / gross_loss if gross_loss > 0 else float("inf")
        )

        equity = initial_capital
        peak = initial_capital
        max_dd = 0.0
        for p in pnls:
            equity += p
            if equity > peak:
                peak = equity
            if peak > 0:
                dd = (peak - equity) / peak
                if dd > max_dd:
                    max_dd = dd
        report.max_drawdown_pct = max_dd * 100

        report.psr = probabilistic_sharpe_ratio(returns)

        # DSR: deflate by the total number of configurations tried
        # across all windows. When the objective was a Sharpe proxy,
        # use the observed variance of trial values; otherwise fall
        # back to the documented 1/(n-1) heuristic inside
        # deflated_sharpe_ratio.
        var_across: Optional[float] = None
        if report.objective == "sharpe_ratio" and len(trial_values) >= 2:
            mean_v = sum(trial_values) / len(trial_values)
            var_across = sum((v - mean_v) ** 2 for v in trial_values) / (
                len(trial_values) - 1
            )
        report.dsr = deflated_sharpe_ratio(
            returns,
            n_trials=max(report.n_trials_total, 1),
            var_sharpe_across_trials=var_across,
        )

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def print_report(self, report: WalkForwardReport) -> None:
        """Print the per-window table and aggregate OOS summary."""
        print(f"\n{'='*78}")
        print(
            f"WALK-FORWARD OPTIMIZATION: {report.strategy} | {report.symbol} "
            f"| {report.start} -> {report.end}"
        )
        print(f"Objective: {report.objective} | {len(report.windows)} windows")
        print(f"{'='*78}")
        print(
            f"  {'Win':<4} {'Test window':<24} {'TrainObj':>9} "
            f"{'TestObj':>9} {'Trades':>7}  Params"
        )
        print(f"  {'-'*72}")
        for w in report.windows:
            train_s = f"{w.train_objective:.3f}" if w.train_objective is not None else "n/a"
            test_s = f"{w.test_objective:.3f}" if w.test_objective is not None else "n/a"
            trades = w.result.closed_trades if w.result else 0
            params_s = ", ".join(
                f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}"
                for k, v in sorted(w.params.items())
            ) or "(defaults)"
            print(
                f"  {w.index:<4} {w.test_start} -> {w.test_end:<10} "
                f"{train_s:>9} {test_s:>9} {trades:>7}  {params_s}"
            )
        print(f"  {'-'*72}")
        pf_s = (
            "inf" if report.profit_factor == float("inf")
            else f"{report.profit_factor:.2f}"
        )
        print(f"\n  Aggregate OOS ({len(report.oos_returns)} closed trades):")
        print(f"    Total return    : {report.total_return_pct:+.2f}%")
        print(f"    Sharpe (trade)  : {report.sharpe:.4f}")
        print(f"    Profit factor   : {pf_s}")
        print(f"    Max drawdown    : {report.max_drawdown_pct:.2f}%")
        if report.psr and report.psr.value is not None:
            print(f"    PSR             : {report.psr.value:.4f}")
        else:
            reason = report.psr.reason if report.psr else "not computed"
            print(f"    PSR             : n/a ({reason})")
        if report.dsr and report.dsr.value is not None:
            tag = "PASS" if report.dsr.passed else "FAIL"
            fb = " [var fallback]" if report.dsr.var_fallback else ""
            print(
                f"    DSR             : {report.dsr.value:.4f} ({tag}, "
                f"N={report.dsr.n_trials}, "
                f"benchmark SR {report.dsr.benchmark_sr:.4f}{fb})"
            )
        else:
            reason = report.dsr.reason if report.dsr else "not computed"
            print(f"    DSR             : n/a ({reason})")
        print(f"    Total trials    : {report.n_trials_total}")
        print(f"{'='*78}\n")

    def print_summary(self, results: List[BacktestResult]) -> None:
        print(f"\n{'='*60}")
        print(f"WALK-FORWARD SUMMARY ({len(results)} windows)")
        print(f"{'='*60}")
        if not results:
            print("  No windows produced results.")
            print(f"{'='*60}\n")
            return
        returns = [r.total_return_pct for r in results]
        sharpes = [r.sharpe_ratio for r in results]
        drawdowns = [r.max_drawdown_pct for r in results]
        win_rates = [r.win_rate_pct for r in results]
        profitable = sum(1 for r in returns if r > 0)

        print(f"  Profitable windows : {profitable}/{len(results)}")
        print(f"  Avg return         : {sum(returns)/len(returns):+.1f}%")
        print(f"  Avg Sharpe         : {sum(sharpes)/len(sharpes):.2f}")
        print(f"  Avg Max DD         : {sum(drawdowns)/len(drawdowns):.1f}%")
        print(f"  Avg Win Rate       : {sum(win_rates)/len(win_rates):.1f}%")
        print(f"{'='*60}\n")

    @staticmethod
    def _build_windows(
        start: str,
        end: str,
        train_months: int,
        test_months: int,
    ) -> List:
        windows = []
        dt_start = datetime.fromisoformat(start)
        dt_end = datetime.fromisoformat(end)

        # First test window starts after initial training period
        test_start = dt_start + timedelta(days=train_months * 30)

        while test_start < dt_end:
            train_start = test_start - timedelta(days=train_months * 30)
            train_end = test_start - timedelta(days=1)
            test_end = min(test_start + timedelta(days=test_months * 30 - 1), dt_end)
            windows.append((
                train_start.date().isoformat(),
                train_end.date().isoformat(),
                test_start.date().isoformat(),
                test_end.date().isoformat(),
            ))
            test_start += timedelta(days=test_months * 30)

        return windows


def main() -> int:
    """CLI entry point for walk-forward analysis."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Walk-forward analysis with optional per-window "
        "Optuna optimization (P5)",
    )
    parser.add_argument(
        "--strategy", "-s", type=str, default=None,
        help="Snake_case strategy key (e.g. mean_reversion). Required "
        "unless --no-optimize.",
    )
    parser.add_argument("--symbol", type=str, default=None,
                        help="Trading symbol (default: config)")
    parser.add_argument("--start", type=str, default=None,
                        help="Range start date (default: config)")
    parser.add_argument("--end", type=str, default=None,
                        help="Range end date (default: config)")
    parser.add_argument("--train-months", type=int, default=None,
                        help="Train window size in months (default: config)")
    parser.add_argument("--test-months", type=int, default=None,
                        help="Test window size in months (default: config)")
    parser.add_argument(
        "--trials", "-n", type=int, default=None,
        help=f"Optuna trials per train window "
        f"(default: env WALK_FORWARD_TRIALS or {DEFAULT_WALK_FORWARD_TRIALS})",
    )
    parser.add_argument("--objective", "-o", type=str, default="sharpe",
                        help="Objective metric (default: sharpe)")
    parser.add_argument("--capital", "-c", type=float, default=10000.0,
                        help="Initial capital per window (default: 10000)")
    parser.add_argument(
        "--no-optimize", action="store_true",
        help="Legacy mode: backtest test windows with default params only",
    )
    args = parser.parse_args()

    from ..config import config as cfg

    symbol = args.symbol or cfg.backtest_symbol
    start = args.start or cfg.backtest_start_date
    end = args.end or cfg.backtest_end_date
    optimize = not args.no_optimize

    if optimize and not args.strategy:
        parser.error("--strategy is required unless --no-optimize is set")

    engine = BacktestEngine()
    wf = WalkForwardAnalyzer(engine)
    wf.run(
        start=start,
        end=end,
        symbol=symbol,
        initial_capital=args.capital,
        optimize=optimize,
        strategy=args.strategy,
        n_trials=args.trials,
        objective=args.objective,
        train_months=args.train_months,
        test_months=args.test_months,
    )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
