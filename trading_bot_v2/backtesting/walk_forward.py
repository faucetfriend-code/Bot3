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

CHUNKED walk-forward (run_chunked_walk_forward):
    The multi-symbol, chunk-window form used by the optimizer sweep
    (`run_optimize --chunked`). Folds are cut from the SAME window
    series the validation runner uses (validation.runner
    .resolve_chunk_windows): with windows W1..WN oldest->newest, fold i
    trains an Optuna study on the preceding window(s) and grades the
    winner on Wi, which no trial ever saw.

    Every number is reported in-sample beside out-of-sample, on one
    banded scale, and the gap between them is the headline overfitting
    signal. The out-of-sample side is the headline result.

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

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

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

# Chunked walk-forward defaults (house style: a series of short windows)
DEFAULT_CHUNK_WINDOW_MONTHS = 2
DEFAULT_CHUNK_WINDOWS = 3
DEFAULT_TRAIN_WINDOWS = 1


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
            logger.info(
                f"Window {i + 1}/{len(windows)}: test {test_start} -> {test_end}"
            )
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
            traded_trial_values,
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
                f"WF window {i + 1}/{len(windows)}: "
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
            #
            # Each window's Optuna seed is derived from the study's
            # identity, which INCLUDES (start, end) - so every window
            # searches independently. Until 2026-08-02 the runner
            # hard-coded seed=42, so every window of every walk-forward
            # opened on the identical parameter vectors and the windows
            # were not the independent trials the aggregate OOS
            # statistics (PSR, DSR) assume. See
            # optimization.optuna_runner.study_seed.
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
            # Only trials that traded carry a real objective; zero-trade
            # trials carry reserved band scores (-100 .. -400) that would
            # blow up the DSR variance estimate.
            trial_values.extend(traded_trial_values(completed))

            if best_trial is not None:
                window.params = dict(best_trial.params)
                window.train_objective = best_trial.value
            else:
                logger.warning(
                    f"WF window {i + 1}: no valid trial - testing with default params"
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
        print(f"\n{'=' * 78}")
        print(
            f"WALK-FORWARD OPTIMIZATION: {report.strategy} | {report.symbol} "
            f"| {report.start} -> {report.end}"
        )
        print(f"Objective: {report.objective} | {len(report.windows)} windows")
        print(f"{'=' * 78}")
        print(
            f"  {'Win':<4} {'Test window':<24} {'TrainObj':>9} "
            f"{'TestObj':>9} {'Trades':>7}  Params"
        )
        print(f"  {'-' * 72}")
        for w in report.windows:
            train_s = (
                f"{w.train_objective:.3f}" if w.train_objective is not None else "n/a"
            )
            test_s = (
                f"{w.test_objective:.3f}" if w.test_objective is not None else "n/a"
            )
            trades = w.result.closed_trades if w.result else 0
            params_s = (
                ", ".join(
                    f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}"
                    for k, v in sorted(w.params.items())
                )
                or "(defaults)"
            )
            print(
                f"  {w.index:<4} {w.test_start} -> {w.test_end:<10} "
                f"{train_s:>9} {test_s:>9} {trades:>7}  {params_s}"
            )
        print(f"  {'-' * 72}")
        pf_s = (
            "inf"
            if report.profit_factor == float("inf")
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
        print(f"{'=' * 78}\n")

    def print_summary(self, results: List[BacktestResult]) -> None:
        print(f"\n{'=' * 60}")
        print(f"WALK-FORWARD SUMMARY ({len(results)} windows)")
        print(f"{'=' * 60}")
        if not results:
            print("  No windows produced results.")
            print(f"{'=' * 60}\n")
            return
        returns = [r.total_return_pct for r in results]
        sharpes = [r.sharpe_ratio for r in results]
        drawdowns = [r.max_drawdown_pct for r in results]
        win_rates = [r.win_rate_pct for r in results]
        profitable = sum(1 for r in returns if r > 0)

        print(f"  Profitable windows : {profitable}/{len(results)}")
        print(f"  Avg return         : {sum(returns) / len(returns):+.1f}%")
        print(f"  Avg Sharpe         : {sum(sharpes) / len(sharpes):.2f}")
        print(f"  Avg Max DD         : {sum(drawdowns) / len(drawdowns):.1f}%")
        print(f"  Avg Win Rate       : {sum(win_rates) / len(win_rates):.1f}%")
        print(f"{'=' * 60}\n")

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
            windows.append(
                (
                    train_start.date().isoformat(),
                    train_end.date().isoformat(),
                    test_start.date().isoformat(),
                    test_end.date().isoformat(),
                )
            )
            test_start += timedelta(days=test_months * 30)

        return windows


# ----------------------------------------------------------------------
# Chunked (multi-symbol) rolling-origin walk-forward
# ----------------------------------------------------------------------


@dataclass
class ChunkedFold:
    """One train/test fold of a chunked walk-forward run.

    Attributes:
        index: 1-based fold number.
        train_windows: (start, end) windows the study optimized on.
        test_window: The held-out (start, end) window.
        study_name: Optuna study name of this fold's search.
        n_trials: Configurations this fold explored (COMPLETE + PRUNED,
            infeasible excluded) - what the deflation is charged for.
        params: Best parameters found in-sample.
        in_sample: Best trial value on the train windows (banded scale).
        out_of_sample: The same parameters' value on the test window,
            on the SAME banded scale.
        oos_raw: Unbanded aggregate objective on the test window.
        oos_outcome: Funnel outcome of the out-of-sample evaluation.
        oos_headline: Funnel headline of the out-of-sample evaluation.
        oos_suggested_fix: Suggested fix when the OOS run never traded.
        oos_trades: Closed trades out of sample.
        oos_traded_symbols: Symbols that traded out of sample.
        oos_profitable_symbols: Symbols with a positive OOS objective.
        oos_per_symbol: Per-symbol OOS rollup.
        oos_symbol_returns: Per-symbol OOS per-trade fractional returns.
    """

    index: int
    train_windows: List[Tuple[str, str]]
    test_window: Tuple[str, str]
    study_name: str = ""
    n_trials: int = 0
    params: Dict[str, Any] = field(default_factory=dict)
    in_sample: Optional[float] = None
    out_of_sample: Optional[float] = None
    oos_raw: float = 0.0
    oos_outcome: str = ""
    oos_headline: str = ""
    oos_suggested_fix: str = ""
    oos_trades: int = 0
    oos_traded_symbols: int = 0
    oos_profitable_symbols: int = 0
    oos_per_symbol: Dict[str, Any] = field(default_factory=dict)
    oos_symbol_returns: Dict[str, List[float]] = field(default_factory=dict)

    @property
    def gap(self) -> Optional[float]:
        """In-sample minus out-of-sample value (the overfit gap)."""
        if self.in_sample is None or self.out_of_sample is None:
            return None
        return self.in_sample - self.out_of_sample


@dataclass
class ChunkedWalkForwardReport:
    """Aggregate report of a chunked walk-forward sweep.

    The headline is ``out_of_sample_objective``. ``overfit_gap`` is the
    number that says whether the search found an edge or fitted noise.
    """

    strategy: str
    symbols: List[str] = field(default_factory=list)
    objective: str = "sharpe_ratio"
    #: Regime the whole report is conditioned on, or None for all trades.
    regime: Optional[str] = None
    window_spec: str = ""
    windows: List[Tuple[str, str]] = field(default_factory=list)
    folds: List[ChunkedFold] = field(default_factory=list)
    in_sample_objective: Optional[float] = None
    out_of_sample_objective: Optional[float] = None
    overfit_gap: Optional[float] = None
    oos_symbol_returns: Dict[str, List[float]] = field(default_factory=dict)
    oos_total_return_pct: float = 0.0
    oos_sharpe: float = 0.0
    oos_profit_factor: float = 0.0
    oos_max_drawdown_pct: float = 0.0
    oos_trades: int = 0
    psr: Optional[PSRResult] = None
    dsr: Optional[DSRResult] = None
    n_trials_total: int = 0
    n_trials_registry: Optional[int] = None
    n_trials_deflated: int = 0
    gate: Optional[Any] = None

    @property
    def oos_returns(self) -> List[float]:
        """Pooled out-of-sample per-trade returns across symbols."""
        pooled: List[float] = []
        for values in self.oos_symbol_returns.values():
            pooled.extend(values)
        return pooled


def build_chunk_folds(
    windows: List[Tuple[str, str]],
    train_windows: int = DEFAULT_TRAIN_WINDOWS,
    anchored: bool = False,
) -> List[Tuple[List[Tuple[str, str]], Tuple[str, str]]]:
    """Cut rolling-origin train/test folds out of a chunk window series.

    Rolling-origin (rather than a single random split) is the right
    scheme here because the data is a time series: a random split leaks
    the future into training, and one fixed holdout gives exactly one
    out-of-sample observation. Rolling origin gives several, each one
    strictly after its training data, which is also how the strategy
    would actually be run.

    Args:
        windows: (start, end) pairs ordered oldest -> newest.
        train_windows: Windows per training set.
        anchored: Expanding training set (every window before the test
            one) instead of the rolling fixed-length one.

    Returns:
        List of (train_window_list, test_window) folds, oldest first.
        Empty when there are not enough windows for one fold.
    """
    if train_windows < 1:
        raise ValueError(f"train_windows must be >= 1 (got {train_windows})")
    folds: List[Tuple[List[Tuple[str, str]], Tuple[str, str]]] = []
    for i in range(train_windows, len(windows)):
        train = windows[:i] if anchored else windows[i - train_windows : i]
        folds.append((list(train), windows[i]))
    return folds


def run_chunked_walk_forward(
    strategy: str,
    symbols: List[str],
    n_trials: int = 20,
    sampler: str = "tpe",
    objective: str = "sharpe",
    window_months: int = DEFAULT_CHUNK_WINDOW_MONTHS,
    n_windows: int = DEFAULT_CHUNK_WINDOWS,
    train_windows: int = DEFAULT_TRAIN_WINDOWS,
    anchored: bool = False,
    initial_capital: float = 10000.0,
    timeout: Optional[int] = None,
    data_dir: Optional[str] = None,
    end: Optional[str] = None,
    windows: Optional[List[Tuple[str, str]]] = None,
    runner: Optional[Any] = None,
    optuna_db_path: Optional[str] = None,
    regime: Optional[str] = None,
) -> ChunkedWalkForwardReport:
    """Run a chunked, multi-symbol, rolling-origin walk-forward sweep.

    Each fold runs a chunked Optuna study on its TRAIN windows only,
    then grades the winning parameters on the next window - data no
    trial ever touched. In-sample and out-of-sample values are computed
    by the same function (``OptunaRunner.evaluate_param_set``) on the
    same banded scale, so their difference is meaningful.

    Args:
        strategy: Strategy name (snake_case).
        symbols: Trading pairs, evaluated on identical windows.
        n_trials: Optuna trials PER FOLD.
        sampler: "tpe" or "random".
        objective: Objective metric (short or long form).
        window_months: Months per chunk window.
        n_windows: Number of chunk windows in the series.
        train_windows: Chunk windows per training set.
        anchored: Expanding instead of rolling training set.
        initial_capital: Starting capital per chunk backtest.
        timeout: Per-fold Optuna timeout in seconds.
        data_dir: Candle data dir override.
        end: ISO date anchoring the newest window (only moves earlier).
        windows: Explicit window series, skipping resolution (tests and
            callers that already resolved the series).
        runner: An OptunaRunner to reuse (tests inject a stubbed one).
        optuna_db_path: Study storage path when building a runner.
        regime: Optional regime value. When set, BOTH the per-fold study
            and the out-of-sample grading score only closed trades whose
            entry regime matches - so a per-regime parameter variant is
            falsifiable on data no trial saw, and the pooled OOS returns
            handed to the promotion gate describe that regime alone.
            Check the sample first: ``python -m
            trading_bot_v2.validation.regime_census`` reports whether the
            (strategy, regime) cell has enough trades to support a
            verdict at all.

    Returns:
        A ChunkedWalkForwardReport.

    Raises:
        ValueError: When no usable windows exist, or the series is too
            short for a single train/test fold.
    """
    from ..optimization.optuna_runner import (
        OptunaRunner,
        normalize_objective,
        traded_trial_values,
    )

    canonical_objective = normalize_objective(objective)
    runner = runner or OptunaRunner(db_path=optuna_db_path)

    if windows:
        sweep_symbols = list(symbols)
        series = [tuple(w) for w in windows]
    else:
        from ..validation.runner import resolve_chunk_windows

        resolved = resolve_chunk_windows(
            symbols,
            window_months,
            n_windows,
            data_dir=data_dir,
            label=f"{strategy}/wf-sweep",
            anchor_end=end,
        )
        if resolved.get("reason"):
            raise ValueError(
                f"Cannot sweep {strategy}: {resolved['reason']} "
                f"(symbols={','.join(symbols)})"
            )
        sweep_symbols = resolved["symbols"]
        series = [tuple(w) for w in resolved["windows"]]

    folds = build_chunk_folds(series, train_windows, anchored)
    if not folds:
        raise ValueError(
            f"Need at least {train_windows + 1} chunk windows for a "
            f"train/test fold, got {len(series)}. Raise --windows or "
            f"lower --train-windows."
        )

    if regime is not None:
        from ..regime_param_overlay import normalize_regime_value

        regime = normalize_regime_value(regime)

    report = ChunkedWalkForwardReport(
        strategy=strategy,
        symbols=sweep_symbols,
        objective=canonical_objective,
        regime=regime,
        window_spec=f"{len(series)}x{window_months}mo",
        windows=series,
    )

    logger.info(
        f"Chunked walk-forward: strategy={strategy}, "
        f"symbols={','.join(sweep_symbols)}, windows={len(series)}, "
        f"folds={len(folds)}, train_windows={train_windows}"
        f"{' (anchored)' if anchored else ''}, {n_trials} trials/fold, "
        f"objective={canonical_objective}"
    )

    trial_values: List[float] = []
    for i, (train, test) in enumerate(folds, start=1):
        fold = ChunkedFold(index=i, train_windows=train, test_window=test)
        logger.info(
            f"Fold {i}/{len(folds)}: train "
            f"{train[0][0]} -> {train[-1][1]}, test {test[0]} -> {test[1]}"
        )

        # Same seeding story as the single-symbol path: the fold's seed
        # is derived from the TRAIN window series it is given, so folds
        # search independently rather than all repeating seed=42.
        study = runner.optimize_chunked(
            strategy=strategy,
            symbols=sweep_symbols,
            n_trials=n_trials,
            sampler=sampler,
            objective=canonical_objective,
            initial_capital=initial_capital,
            timeout=timeout,
            windows=train,
            study_suffix=f"_wf{i}",
            regime=regime,
        )
        fold.study_name = getattr(study, "study_name", "")
        fold.n_trials, completed = _fold_trial_counts(study)
        trial_values.extend(traded_trial_values(completed))

        best = _best_trial_or_none(study)
        if best is not None:
            fold.params = dict(best.params)
            fold.in_sample = best.value
        else:
            logger.warning(
                f"Fold {i}: no valid trial - grading default params out of sample"
            )

        evaluation = runner.evaluate_param_set(
            strategy=strategy,
            params=fold.params,
            symbols=sweep_symbols,
            windows=[test],
            objective=canonical_objective,
            initial_capital=initial_capital,
            label=f"{strategy}/oos-fold{i}",
            regime=regime,
        )
        payload = evaluation.funnel.to_dict()
        fold.out_of_sample = evaluation.banded_value
        fold.oos_raw = evaluation.value
        fold.oos_outcome = str(getattr(evaluation.outcome, "value", evaluation.outcome))
        fold.oos_headline = payload.get("headline", "")
        fold.oos_trades = evaluation.total_trades
        fold.oos_traded_symbols = evaluation.traded_symbols
        fold.oos_profitable_symbols = evaluation.profitable_symbols
        fold.oos_per_symbol = evaluation.per_symbol
        fold.oos_symbol_returns = evaluation.symbol_returns
        if fold.oos_trades == 0:
            from ..diagnostics.outcomes import suggest_fix

            fold.oos_suggested_fix = suggest_fix(payload, fold.params)

        for symbol, values in evaluation.symbol_returns.items():
            report.oos_symbol_returns.setdefault(symbol, []).extend(values)
        report.folds.append(fold)

    _finalize_chunked_report(report, initial_capital, trial_values)
    return report


def _fold_trial_counts(study: Any) -> Tuple[int, List[Any]]:
    """Count the configurations a fold's study explored.

    Mirrors OptunaRunner._record_trial_registry: COMPLETE + PRUNED,
    minus trials pruned as structurally infeasible (those explored
    nothing and must not inflate the deflation's N).

    Args:
        study: The fold's Optuna study.

    Returns:
        (n_trials, completed_trials).
    """
    import optuna

    from ..optimization.optuna_runner import _is_infeasible_trial

    state = optuna.trial.TrialState
    trials = getattr(study, "trials", None) or []
    completed = [
        t for t in trials if t.state == state.COMPLETE and not _is_infeasible_trial(t)
    ]
    pruned = [
        t for t in trials if t.state == state.PRUNED and not _is_infeasible_trial(t)
    ]
    return len(completed) + len(pruned), completed


def _best_trial_or_none(study: Any) -> Optional[Any]:
    """Return study.best_trial, or None when every trial failed/pruned."""
    try:
        return study.best_trial
    except (ValueError, AttributeError):
        return None


def _finalize_chunked_report(
    report: ChunkedWalkForwardReport,
    initial_capital: float,
    trial_values: List[float],
) -> None:
    """Compute the aggregate in-sample / out-of-sample metrics.

    The out-of-sample side pools the per-trade returns of every test
    window, which is the only series that was never optimized on. The
    deflation N is read back from the trial registry so the number the
    standing gate will use is the number reported here.

    Args:
        report: Report to fill in-place.
        initial_capital: Common capital base of every chunk.
        trial_values: Traded trial objective values across folds.
    """
    is_values = [f.in_sample for f in report.folds if f.in_sample is not None]
    oos_values = [f.out_of_sample for f in report.folds if f.out_of_sample is not None]
    if is_values:
        report.in_sample_objective = sum(is_values) / len(is_values)
    if oos_values:
        report.out_of_sample_objective = sum(oos_values) / len(oos_values)
    if report.in_sample_objective is not None and (
        report.out_of_sample_objective is not None
    ):
        report.overfit_gap = report.in_sample_objective - report.out_of_sample_objective

    returns = report.oos_returns
    report.oos_trades = len(returns)
    pnls = [r * initial_capital for r in returns]
    report.oos_total_return_pct = sum(returns) * 100
    report.oos_sharpe = sharpe_ratio(returns)

    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    report.oos_profit_factor = (
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
    report.oos_max_drawdown_pct = max_dd * 100

    report.n_trials_total = sum(f.n_trials for f in report.folds)

    # Read the registry back: this is the exact N validation/gate.py
    # will feed to the DSR, so verifying it here is verifying the
    # deflation path end to end rather than assuming it.
    registry_variance: Optional[float] = None
    try:
        from ..database import DatabaseManager

        db = DatabaseManager()
        total = db.get_total_trials(report.strategy)
        if total > 0:
            report.n_trials_registry = total
            registry_variance = db.get_trial_sr_variance(report.strategy)
    except Exception as e:
        logger.warning(f"Trial registry lookup failed: {e}")

    report.n_trials_deflated = max(
        report.n_trials_registry or 0, report.n_trials_total, 1
    )

    report.psr = probabilistic_sharpe_ratio(returns)

    var_across: Optional[float] = None
    if report.objective == "sharpe_ratio" and len(trial_values) >= 2:
        mean_v = sum(trial_values) / len(trial_values)
        var_across = sum((v - mean_v) ** 2 for v in trial_values) / (
            len(trial_values) - 1
        )
    elif registry_variance is not None:
        var_across = registry_variance
    report.dsr = deflated_sharpe_ratio(
        returns,
        n_trials=report.n_trials_deflated,
        var_sharpe_across_trials=var_across,
    )

    try:
        from ..validation.gate import evaluate_strategy_gate

        report.gate = evaluate_strategy_gate(
            strategy=report.strategy,
            symbol_returns=report.oos_symbol_returns,
            n_trials=report.n_trials_deflated,
            sr_variance=var_across,
        )
    except Exception as e:
        logger.warning(f"Gate evaluation failed: {e}")


def _fmt(value: Optional[float], spec: str = "9.3f") -> str:
    """Format an optional float, or 'n/a'."""
    return "n/a" if value is None else f"{value:{spec}}"


def _print_fold_symbols(fold: ChunkedFold) -> None:
    """Print a fold's per-symbol out-of-sample breakdown.

    Cross-symbol consistency is a real gate check, so it stays visible
    instead of being averaged into the fold's single number. A symbol
    that contributed zero bars is called out as a DATA problem, not
    read as "the strategy found nothing".

    Args:
        fold: The fold to print.
    """
    if not fold.oos_per_symbol:
        return
    print(
        f"        {'symbol':<14} {'bars':>9} {'raw sig':>9} "
        f"{'trades':>7} {'objective':>11} {'return %':>9}"
    )
    blind = []
    for symbol in sorted(fold.oos_per_symbol):
        cell = fold.oos_per_symbol[symbol] or {}
        if not cell.get("invoked", 0):
            blind.append(symbol)
        print(
            f"        {symbol:<14} {cell.get('invoked', 0):>9} "
            f"{cell.get('raw_signals', 0):>9} {cell.get('trades', 0):>7} "
            f"{cell.get('objective', 0.0):>11.4f} "
            f"{cell.get('return_pct', 0.0):>9.2f}"
        )
    print(
        f"        symbols that traded out of sample: "
        f"{fold.oos_traded_symbols}/{len(fold.oos_per_symbol)} | "
        f"positive objective: {fold.oos_profitable_symbols}"
        f"/{len(fold.oos_per_symbol)}"
    )
    if blind:
        print(
            f"        WARNING: {', '.join(blind)} contributed 0 bars - "
            f"a data hole, not a parameter result. Re-anchor with --end "
            f"or backfill."
        )


def print_chunked_walk_forward_report(
    report: ChunkedWalkForwardReport,
) -> None:
    """Print the chunked walk-forward report in the house table style.

    The out-of-sample column is the headline, and the IS->OOS gap gets
    its own line: a large gap is the overfitting signal and must not be
    something the reader has to compute.

    Args:
        report: The finished report.
    """
    line = "=" * 78
    print(f"\n{line}")
    print(
        f"CHUNKED WALK-FORWARD: {report.strategy} | "
        f"{','.join(report.symbols)}"
        + (f" | REGIME: {report.regime}" if report.regime else "")
    )
    if report.regime:
        print(
            f"  Every number below counts ONLY trades entered in "
            f"{report.regime}. Trades in other regimes still happen - "
            f"this measures a subset, not a variant that trades less."
        )
    print(
        f"Objective: {report.objective} | windows: {report.window_spec} "
        f"({report.windows[0][0]} .. {report.windows[-1][1]}) | "
        f"{len(report.folds)} fold{'' if len(report.folds) == 1 else 's'}"
    )
    print(line)
    print(
        f"  {'Fold':<5} {'Train':<24} {'Test':<24} "
        f"{'IS':>9} {'OOS':>9} {'Gap':>8} {'Trades':>7}"
    )
    print(f"  {'-' * 90}")
    for fold in report.folds:
        train_s = f"{fold.train_windows[0][0]}..{fold.train_windows[-1][1]}"
        test_s = f"{fold.test_window[0]}..{fold.test_window[1]}"
        print(
            f"  {fold.index:<5} {train_s:<24} {test_s:<24} "
            f"{_fmt(fold.in_sample):>9} {_fmt(fold.out_of_sample):>9} "
            f"{_fmt(fold.gap, '8.3f'):>8} {fold.oos_trades:>7}"
        )
        params_s = (
            ", ".join(
                f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}"
                for k, v in sorted(fold.params.items())
            )
            or "(defaults)"
        )
        print(f"        params: {params_s}")
        _print_fold_symbols(fold)
        if fold.oos_trades == 0:
            print(f"        OOS outcome: {fold.oos_outcome}")
            if fold.oos_headline:
                print(f"        OOS funnel : {fold.oos_headline}")
            if fold.oos_suggested_fix:
                print(f"        fix        : {fold.oos_suggested_fix}")
        if fold.study_name:
            print(
                f"        explain    : python -m "
                f"trading_bot_v2.diagnostics.explain --study "
                f"{fold.study_name}"
            )
    print(f"  {'-' * 90}")

    print("\n  IN-SAMPLE vs OUT-OF-SAMPLE (same banded scale)")
    print(f"    In-sample  (optimized on) : {_fmt(report.in_sample_objective)}")
    print(f"    OUT-OF-SAMPLE  (HEADLINE)  : {_fmt(report.out_of_sample_objective)}")
    print(f"    Overfit gap (IS - OOS)    : {_fmt(report.overfit_gap)}")
    print(f"    {_overfit_verdict(report)}")

    pf_s = (
        "inf"
        if report.oos_profit_factor == float("inf")
        else f"{report.oos_profit_factor:.2f}"
    )
    print(f"\n  AGGREGATE OUT-OF-SAMPLE ({report.oos_trades} closed trades)")
    print(f"    Total return    : {report.oos_total_return_pct:+.2f}%")
    print(f"    Sharpe (trade)  : {report.oos_sharpe:.4f}")
    print(f"    Profit factor   : {pf_s}")
    print(f"    Max drawdown    : {report.oos_max_drawdown_pct:.2f}%")
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
            f"N={report.dsr.n_trials}, benchmark SR "
            f"{report.dsr.benchmark_sr:.4f}{fb})"
        )
    else:
        reason = report.dsr.reason if report.dsr else "not computed"
        print(f"    DSR             : n/a ({reason})")
    print(
        f"    Trials          : {report.n_trials_total} this run, "
        f"registry total {report.n_trials_registry or 'unknown'} -> "
        f"deflated for N={report.n_trials_deflated}"
    )

    if report.gate is not None:
        print("\n  STANDING GATE ON THE OUT-OF-SAMPLE SERIES")
        for check in report.gate.checks:
            tag = "PASS" if check.passed else "FAIL"
            print(f"    {check.name:<26} {tag:<5} {check.value}")
        print(f"    OVERALL: {'PASS' if report.gate.passed else 'FAIL'}")
    print(f"{line}\n")


def _overfit_verdict(report: ChunkedWalkForwardReport) -> str:
    """One-line reading of the in-sample / out-of-sample gap."""
    if report.out_of_sample_objective is None:
        return "VERDICT: no out-of-sample result - nothing to believe yet."
    if report.oos_trades == 0:
        return (
            "VERDICT: the winner never traded out of sample - the "
            "in-sample result is not evidence of anything."
        )
    if report.out_of_sample_objective <= 0:
        if (report.in_sample_objective or 0.0) > 0:
            return (
                "VERDICT: OVERFIT - positive in sample, non-positive out "
                "of sample. Do not deploy these parameters."
            )
        return (
            "VERDICT: NO EDGE - the objective is non-positive out of "
            "sample, and the search never found one in sample either."
        )
    if report.overfit_gap is not None and report.in_sample_objective:
        denom = abs(report.in_sample_objective)
        if denom > 1e-9 and report.overfit_gap / denom > 0.5:
            return (
                "VERDICT: WEAK - out-of-sample keeps less than half the "
                "in-sample edge; treat the in-sample number as noise."
            )
    return (
        "VERDICT: out-of-sample holds up; grade it against the gate "
        "checks above before deploying."
    )


def main() -> int:
    """CLI entry point for walk-forward analysis."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Walk-forward analysis with optional per-window "
        "Optuna optimization (P5)",
    )
    parser.add_argument(
        "--strategy",
        "-s",
        type=str,
        default=None,
        help="Snake_case strategy key (e.g. mean_reversion). Required "
        "unless --no-optimize.",
    )
    parser.add_argument(
        "--symbol", type=str, default=None, help="Trading symbol (default: config)"
    )
    parser.add_argument(
        "--start", type=str, default=None, help="Range start date (default: config)"
    )
    parser.add_argument(
        "--end", type=str, default=None, help="Range end date (default: config)"
    )
    parser.add_argument(
        "--train-months",
        type=int,
        default=None,
        help="Train window size in months (default: config)",
    )
    parser.add_argument(
        "--test-months",
        type=int,
        default=None,
        help="Test window size in months (default: config)",
    )
    parser.add_argument(
        "--trials",
        "-n",
        type=int,
        default=None,
        help=f"Optuna trials per train window "
        f"(default: env WALK_FORWARD_TRIALS or {DEFAULT_WALK_FORWARD_TRIALS})",
    )
    parser.add_argument(
        "--objective",
        "-o",
        type=str,
        default="sharpe",
        help="Objective metric (default: sharpe)",
    )
    parser.add_argument(
        "--capital",
        "-c",
        type=float,
        default=10000.0,
        help="Initial capital per window (default: 10000)",
    )
    parser.add_argument(
        "--no-optimize",
        action="store_true",
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
