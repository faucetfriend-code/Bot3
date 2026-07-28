"""
Optuna Runner for Strategy Parameter Optimization
=================================================

Manages Optuna studies for trading strategy optimization.
Supports TPE and Random samplers with walk-forward validation.

Usage:
    from trading_bot_v2.optimization.optuna_runner import OptunaRunner

    runner = OptunaRunner()
    study = runner.optimize(
        strategy="mean_reversion",
        n_trials=100,
        sampler="tpe",
    )
    best_params = runner.get_best_params("mean_reversion")
"""

import math
import os
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
from loguru import logger

try:
    import optuna
    OPTUNA_AVAILABLE = True
except ImportError:
    OPTUNA_AVAILABLE = False
    logger.warning("Optuna not installed. Install with: pip install optuna")

import pandas as pd

from .search_spaces import (
    InfeasibleParamsError,
    get_search_space,
    suggest_params,
    list_strategies,
)
from ..backtesting.optimization_adapter import OptimizationAdapter
from ..diagnostics.funnel import (
    STAGE_BARS_EVALUATED,
    STAGE_CLOSED_TRADES,
    STAGE_ORDERS_PLACED,
    STAGE_RAW_SIGNALS,
    STAGE_STRATEGY_INVOKED,
    SignalFunnel,
)
from ..diagnostics.outcomes import TrialOutcome, score_for_outcome, suggest_fix
from ..regime_param_overlay import normalize_regime_value


# Default database path
DEFAULT_DB_PATH = "optimization_studies.db"

# Version of the trial-scoring scheme. Stored on every study as the
# "scoring_schema" user attr; resuming a study written under a different
# (or missing) schema is refused, because mixing raw objective values
# with banded zero-trade scores would make best_trial meaningless.
SCORING_SCHEMA = 2

# Abort a run whose trials are mostly crashing, rather than burning the
# whole budget on a broken configuration.
FAIL_RATE_ABORT_THRESHOLD = 0.5
FAIL_RATE_MIN_TRIALS = 10

# Default minimum matching-regime trades before a trial is pruned
DEFAULT_REGIME_OPT_MIN_TRADES = 15

# Objective aliases (CLI short forms -> canonical metric names)
OBJECTIVE_ALIASES = {
    "sharpe": "sharpe_ratio",
    "sharpe_ratio": "sharpe_ratio",
    "sortino": "sortino_ratio",
    "sortino_ratio": "sortino_ratio",
    "total_return": "total_return_pct",
    "total_return_pct": "total_return_pct",
    "calmar": "calmar_ratio",
    "calmar_ratio": "calmar_ratio",
    "pf": "profit_factor",
    "profit_factor": "profit_factor",
}


def normalize_objective(objective: str) -> str:
    """Normalize an objective name (short or long form) to canonical form.

    Args:
        objective: Objective name, e.g. "sharpe" or "sharpe_ratio".

    Returns:
        Canonical metric name, e.g. "sharpe_ratio".

    Raises:
        ValueError: If the objective is not recognised.
    """
    key = str(objective).strip().lower()
    if key not in OBJECTIVE_ALIASES:
        raise ValueError(
            f"Unknown objective: '{objective}'. "
            f"Available: {sorted(set(OBJECTIVE_ALIASES.values()))}"
        )
    return OBJECTIVE_ALIASES[key]


def _objective_short_name(objective: str) -> str:
    """Return the short study-name form of a canonical objective."""
    return {
        "sharpe_ratio": "sharpe",
        "sortino_ratio": "sortino",
        "total_return_pct": "total_return",
        "calmar_ratio": "calmar",
        "profit_factor": "pf",
    }.get(objective, objective)


def _is_infeasible_trial(trial: Any) -> bool:
    """Return True when a trial was pruned as structurally infeasible.

    Args:
        trial: An Optuna trial (or any object with ``user_attrs``).

    Returns:
        True if the trial's outcome attr is ``infeasible_config``.
    """
    attrs = getattr(trial, "user_attrs", None) or {}
    return attrs.get("outcome") == TrialOutcome.INFEASIBLE_CONFIG.value


def _funnel_for_result(result: Any) -> SignalFunnel:
    """Return the signal funnel for a backtest result.

    Instrumented runs carry ``result.diagnostics``. Uninstrumented ones
    (stubs, older callers) are reconstructed conservatively: a run that
    produced closed trades is classified as ``traded``, anything else as
    ``no_data`` - never as a confident zero-signal diagnosis we did not
    actually observe.

    Args:
        result: A BacktestResult (or anything exposing diagnostics /
            closed_trades).

    Returns:
        A SignalFunnel.
    """
    payload = getattr(result, "diagnostics", None) or {}
    if payload:
        return SignalFunnel.from_dict(payload)

    funnel = SignalFunnel(label="uninstrumented")
    closed = int(getattr(result, "closed_trades", 0) or 0)
    if closed > 0:
        funnel.set_stage(STAGE_BARS_EVALUATED, 1)
        funnel.set_stage(STAGE_STRATEGY_INVOKED, 1)
        funnel.set_stage(STAGE_RAW_SIGNALS, closed)
        funnel.set_stage(STAGE_ORDERS_PLACED, closed)
        funnel.set_stage(STAGE_CLOSED_TRADES, closed)
    return funnel


def _assert_scoring_schema(study: Any) -> None:
    """Stamp or verify a study's scoring schema.

    Trial values written under different scoring schemes are not
    comparable: a raw Sharpe of 0.4 and a banded -99.5 in the same study
    would make ``best_trial`` meaningless. A study that already has
    trials but no schema stamp predates banded scoring and must not be
    appended to.

    Args:
        study: The Optuna study.

    Raises:
        ValueError: If the study was scored under another schema.
    """
    attrs = getattr(study, "user_attrs", None) or {}
    existing = attrs.get("scoring_schema")
    if existing is None:
        if getattr(study, "trials", None):
            raise ValueError(
                f"Study '{getattr(study, 'study_name', '?')}' has trials "
                f"scored under an older scheme (no scoring_schema attr). "
                f"Refusing to append banded scores to it - start a new "
                f"study instead."
            )
        study.set_user_attr("scoring_schema", SCORING_SCHEMA)
        return
    if existing != SCORING_SCHEMA:
        raise ValueError(
            f"Study '{getattr(study, 'study_name', '?')}' uses scoring "
            f"schema {existing}, this runner writes {SCORING_SCHEMA}. "
            f"Start a new study instead of mixing schemes."
        )


class _FailRateGuard:
    """Optuna callback that stops a study whose trials mostly crash.

    Attributes:
        study_name: Name used in the abort message.
        aborted: Set once the threshold was breached.
    """

    def __init__(self, study_name: str) -> None:
        """Initialize the guard.

        Args:
            study_name: Study name, for the error message.
        """
        self.study_name = study_name
        self.aborted = False
        self._message = ""

    def __call__(self, study: Any, trial: Any) -> None:
        """Check the running fail rate after each trial.

        Args:
            study: The running study.
            trial: The trial that just finished (unused).
        """
        if self.aborted:
            return
        state = optuna.trial.TrialState
        finished = [
            t
            for t in study.trials
            if t.state in (state.COMPLETE, state.PRUNED, state.FAIL)
        ]
        if len(finished) < FAIL_RATE_MIN_TRIALS:
            return
        failed = sum(1 for t in finished if t.state == state.FAIL)
        rate = failed / len(finished)
        if rate <= FAIL_RATE_ABORT_THRESHOLD:
            return
        self.aborted = True
        self._message = (
            f"Aborting study '{self.study_name}': {failed}/{len(finished)} "
            f"trials failed ({rate:.0%} > "
            f"{FAIL_RATE_ABORT_THRESHOLD:.0%}). The backtest is broken, not "
            f"the parameters - fix the error before burning more budget."
        )
        logger.error(self._message)
        study.stop()

    def raise_if_aborted(self) -> None:
        """Raise the abort error, if the guard tripped.

        Raises:
            RuntimeError: When the fail-rate threshold was breached.
        """
        if self.aborted:
            raise RuntimeError(self._message)


class OptunaRunner:
    """
    Optuna study manager for trading strategy optimization.

    Features:
    - TPE and Random samplers
    - Walk-forward validation
    - SQLite persistence for studies
    - Comprehensive logging and reporting
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        config: Optional[Any] = None,
    ):
        """
        Initialize the Optuna runner.

        Args:
            db_path: Path to SQLite database for study persistence.
                     Defaults to "optimization_studies.db" in the module directory.
            config: Optional config override for backtesting.
        """
        if not OPTUNA_AVAILABLE:
            raise ImportError(
                "Optuna is required. Install with: pip install optuna"
            )

        # Set up database path
        if db_path is None:
            module_dir = Path(__file__).parent
            db_path = str(module_dir / DEFAULT_DB_PATH)
        self.db_path = db_path

        # Storage URL for Optuna
        self.storage_url = f"sqlite:///{db_path}"

        # Config and adapter
        self.config = config
        self.adapter = OptimizationAdapter(config=config)

        logger.info(f"OptunaRunner initialized: db={db_path}")

    def optimize(
        self,
        strategy: str,
        n_trials: int = 100,
        sampler: str = "tpe",
        objective: str = "sharpe_ratio",
        start: Optional[str] = None,
        end: Optional[str] = None,
        symbol: Optional[str] = None,
        initial_capital: float = 10000.0,
        walk_forward: bool = False,
        train_months: int = 6,
        test_months: int = 1,
        timeout: Optional[int] = None,
        n_jobs: int = 1,
        regime: Optional[str] = None,
        min_regime_trades: Optional[int] = None,
    ) -> optuna.Study:
        """
        Run optimization for a single strategy.

        Args:
            strategy: Strategy name (e.g., "mean_reversion")
            n_trials: Number of optimization trials
            sampler: Sampler type ("tpe" or "random")
            objective: Metric to optimize ("sharpe_ratio", "sortino_ratio",
                      "total_return_pct", "calmar_ratio", "profit_factor";
                      short forms like "sharpe" are accepted)
            start: Backtest start date (ISO format, e.g., "2024-01-01")
            end: Backtest end date (ISO format, e.g., "2024-12-31")
            symbol: Trading symbol (uses config default if None)
            initial_capital: Starting capital
            walk_forward: Enable walk-forward validation
            train_months: Training window for walk-forward
            test_months: Test window for walk-forward
            timeout: Timeout in seconds (None = no limit)
            n_jobs: Number of parallel jobs (-1 for all CPUs)
            regime: Optional target regime (e.g. "RANGING_CALM"). When
                set, each trial is scored ONLY on closed trades whose
                entry regime matches, and trials with fewer than
                min_regime_trades matching trades are pruned.
            min_regime_trades: Minimum matching trades per trial before
                pruning (default: env REGIME_OPT_MIN_TRADES or 15).

        Returns:
            Completed Optuna study with results
        """
        # Validate strategy
        available = list_strategies()
        if strategy not in available:
            raise ValueError(
                f"Unknown strategy: '{strategy}'. Available: {available}"
            )

        # Get search space
        get_search_space(strategy)

        # Normalize objective and regime
        objective = normalize_objective(objective)
        if regime is not None:
            regime = normalize_regime_value(regime)
        if min_regime_trades is None:
            min_regime_trades = int(
                os.getenv("REGIME_OPT_MIN_TRADES", str(DEFAULT_REGIME_OPT_MIN_TRADES))
            )

        # Set up dates from config if not provided
        from ..config import config as default_config
        cfg = self.config or default_config

        if start is None:
            start = cfg.backtest_start_date
        if end is None:
            end = cfg.backtest_end_date
        if symbol is None:
            symbol = cfg.backtest_symbol

        # Create sampler
        if sampler.lower() == "tpe":
            optuna_sampler = optuna.samplers.TPESampler(
                seed=42,
                n_startup_trials=min(20, n_trials // 5),
            )
        elif sampler.lower() == "random":
            optuna_sampler = optuna.samplers.RandomSampler(seed=42)
        else:
            raise ValueError(f"Unknown sampler: '{sampler}'. Use 'tpe' or 'random'.")

        # Create study name. Regime-conditional studies embed the regime
        # and objective so per-regime studies never collide, e.g.
        # "mean_reversion_RANGING_CALM_sharpe_20260720_120000".
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if regime is not None:
            study_name = (
                f"{strategy}_{regime.upper()}_"
                f"{_objective_short_name(objective)}_{timestamp}"
            )
        else:
            study_name = f"{strategy}_{timestamp}"

        # Create or load study
        study = optuna.create_study(
            study_name=study_name,
            storage=self.storage_url,
            load_if_exists=True,
            direction="maximize",
            sampler=optuna_sampler,
        )
        _assert_scoring_schema(study)

        logger.info(
            f"Starting optimization: strategy={strategy}, trials={n_trials}, "
            f"sampler={sampler}, objective={objective}, walk_forward={walk_forward}"
            + (
                f", regime={regime}, min_regime_trades={min_regime_trades}"
                if regime is not None
                else ""
            )
        )

        # Define objective function
        def objective_fn(trial: optuna.Trial) -> float:
            return self._objective(
                trial=trial,
                strategy=strategy,
                objective=objective,
                start=start,
                end=end,
                symbol=symbol,
                initial_capital=initial_capital,
                walk_forward=walk_forward,
                train_months=train_months,
                test_months=test_months,
                regime=regime,
                min_regime_trades=min_regime_trades,
            )

        # Run optimization.
        #
        # catch=(Exception,) records a crashing trial as FAIL and keeps
        # the study going. Previously the objective swallowed every
        # exception and returned float("-inf"), which recorded the
        # failure as COMPLETE (indistinguishable from a real score) and
        # poisoned study.best_value.
        abort = _FailRateGuard(study_name)
        try:
            study.optimize(
                objective_fn,
                n_trials=n_trials,
                timeout=timeout,
                n_jobs=n_jobs,
                show_progress_bar=True,
                catch=(Exception,),
                callbacks=[abort],
            )
        except Exception as e:
            logger.error(f"Optimization failed: {e}")
            raise
        abort.raise_if_aborted()

        # Log results. best_trial raises ValueError when every trial was
        # pruned (possible in regime mode with min-trades pruning).
        try:
            best_trial = study.best_trial
        except ValueError:
            best_trial = None

        if best_trial is not None:
            logger.info(
                f"Optimization complete: best_value={study.best_value:.4f}, "
                f"best_params={study.best_params}"
            )
        else:
            logger.warning(
                "Optimization completed with no valid trials "
                "(all pruned or failed)"
            )

        # P5: record how many configurations this study tried so the
        # Deflated Sharpe Ratio can discount for search size later.
        try:
            self._record_trial_registry(study, strategy, regime, objective)
        except Exception as e:
            logger.warning(f"Trial registry recording failed: {e}")

        return study

    def _record_trial_registry(
        self,
        study: "optuna.Study",
        strategy: str,
        regime: Optional[str],
        objective: str,
    ) -> None:
        """Record this study's trial count in the trial_registry (P5).

        Counts COMPLETE + PRUNED trials (both explored a configuration;
        FAIL/RUNNING/WAITING did not produce a scored config), MINUS
        trials pruned as ``infeasible_config``.

        Excluding infeasible configs matters: validation/gate.py feeds
        this N to the deflated Sharpe ratio, which deflates harder as N
        grows. Pre-backtest feasibility pruning rejects params that never
        explored anything - counting them would make the gate harder to
        pass for no reason, purely as a side effect of adding the
        pre-check.

        When the objective is sharpe_ratio, also stores the sample variance
        (ddof=1) of the completed trials' objective values as an
        sr-variance proxy for DSR benchmarks; for other objectives the
        variance column stays NULL.

        Note: the stored variance is a proxy - objective values carry
        the adapter's drawdown/trade-count penalties and (for full-run
        backtests) annualization, so downstream DSR consumers using
        non-annualized per-trade Sharpe should prefer their own trial
        values when available.

        Args:
            study: The finished Optuna study.
            strategy: Snake_case strategy key.
            regime: Normalized regime value, or None.
            objective: Canonical objective name.
        """
        from ..database import DatabaseManager

        state = optuna.trial.TrialState
        completed = [
            t
            for t in study.trials
            if t.state == state.COMPLETE and not _is_infeasible_trial(t)
        ]
        pruned = [
            t
            for t in study.trials
            if t.state == state.PRUNED and not _is_infeasible_trial(t)
        ]
        infeasible = sum(1 for t in study.trials if _is_infeasible_trial(t))
        n_trials = len(completed) + len(pruned)
        if n_trials == 0:
            return

        sr_variance: Optional[float] = None
        if objective == "sharpe_ratio":
            values = [
                t.value
                for t in completed
                if t.value is not None and math.isfinite(t.value)
            ]
            if len(values) >= 2:
                mean_v = sum(values) / len(values)
                sr_variance = sum((v - mean_v) ** 2 for v in values) / (
                    len(values) - 1
                )

        row_id = DatabaseManager().save_trial_registry_entry(
            strategy=strategy,
            regime=regime,
            scope="optuna_study",
            n_trials=n_trials,
            sr_variance=sr_variance,
            source=study.study_name,
        )
        logger.info(
            f"Trial registry: recorded {n_trials} trials "
            f"(completed={len(completed)}, pruned={len(pruned)}, "
            f"infeasible_excluded={infeasible}) "
            f"for {strategy}"
            + (f"/{regime}" if regime else "")
            + f" as row {row_id}"
        )

    def _objective(
        self,
        trial: optuna.Trial,
        strategy: str,
        objective: str,
        start: str,
        end: str,
        symbol: str,
        initial_capital: float,
        walk_forward: bool,
        train_months: int,
        test_months: int,
        regime: Optional[str] = None,
        min_regime_trades: int = DEFAULT_REGIME_OPT_MIN_TRADES,
    ) -> float:
        """
        Optuna objective function for a single trial.

        Args:
            trial: Optuna trial object
            strategy: Strategy name
            objective: Metric to optimize (canonical form)
            start: Backtest start date
            end: Backtest end date
            symbol: Trading symbol
            initial_capital: Starting capital
            walk_forward: Enable walk-forward validation
            train_months: Training window size
            test_months: Test window size
            regime: Optional target regime (normalized value). When set,
                the objective is computed only on closed trades whose
                entry regime matches; trials with too few matching trades
                raise optuna.TrialPruned.
            min_regime_trades: Pruning threshold for regime mode

        Returns:
            Objective value (higher is better). A trial that ran but
            never traded returns a banded score (see
            diagnostics/outcomes.py), never 0 and never -inf.

        Raises:
            optuna.TrialPruned: In regime mode, when fewer than
                min_regime_trades matching trades were produced, or when
                the sampled params are structurally infeasible.
            Exception: Any backtest failure propagates so Optuna records
                the trial as FAIL (see catch= in optimize()).
        """
        # Suggest parameters. Combinations that could never produce a
        # tradeable signal (e.g. a constant RRR below the strategy's gate)
        # are pruned up front with the reason attached, instead of being
        # backtested into a silent zero-trade result.
        try:
            params = suggest_params(trial, strategy)
        except InfeasibleParamsError as e:
            trial.set_user_attr("outcome", TrialOutcome.INFEASIBLE_CONFIG.value)
            trial.set_user_attr("headline", str(e))
            trial.set_user_attr(
                "suggested_fix",
                "reparameterize the search space so this region is "
                "unrepresentable, or widen the bounds",
            )
            logger.debug(f"Trial {trial.number} pruned as infeasible: {e}")
            raise optuna.TrialPruned(str(e))

        # Log trial
        logger.debug(f"Trial {trial.number}: params={params}")

        try:
            if walk_forward:
                # Walk-forward validation
                results = self.adapter.run_walk_forward(
                    strategy=strategy,
                    params=params,
                    start=start,
                    end=end,
                    symbol=symbol,
                    initial_capital=initial_capital,
                    train_months=train_months,
                    test_months=test_months,
                )

                if not results:
                    # A window-less walk-forward is a setup failure, not a
                    # score. Raising records it as FAIL instead of the old
                    # float("-inf") COMPLETE.
                    raise RuntimeError(
                        f"walk-forward produced no windows for {strategy} "
                        f"{start}..{end}"
                    )

                if regime is not None:
                    # Per-window objective on the regime-filtered subset
                    window_trades = [
                        (r, self.adapter.get_regime_trades(r, regime))
                        for r in results
                    ]
                    total_matching = sum(len(t) for _, t in window_trades)
                    trial.set_user_attr("regime_trade_count", total_matching)
                    if total_matching < min_regime_trades:
                        raise optuna.TrialPruned(
                            f"Only {total_matching} {regime} trades "
                            f"(< {min_regime_trades})"
                        )
                    # Windows with no matching trades are excluded from
                    # the average rather than scored as zero.
                    values = [
                        self.adapter.calculate_objective_from_trades(
                            trades=t,
                            objective=objective,
                            initial_capital=initial_capital,
                            start=r.start,
                            end=r.end,
                        )
                        for r, t in window_trades
                        if t
                    ]
                else:
                    values = [
                        self.adapter.calculate_objective(r, objective)
                        for r in results
                    ]

                avg_value = sum(values) / len(values)

                # Penalize high variance across windows
                if len(values) > 1:
                    variance = sum((v - avg_value) ** 2 for v in values) / len(values)
                    std_dev = variance ** 0.5
                    # Penalize if std > 1.0
                    if std_dev > 1.0:
                        avg_value -= (std_dev - 1.0) * 0.2

                funnel = SignalFunnel(label=f"{strategy}/{symbol}")
                for window in results:
                    funnel.merge(_funnel_for_result(window))
                return self._score_trial(
                    trial,
                    funnel,
                    avg_value,
                    params,
                    traded_override=regime is not None,
                )

            else:
                # Single period backtest
                result = self.adapter.run_backtest(
                    strategy=strategy,
                    params=params,
                    start=start,
                    end=end,
                    symbol=symbol,
                    initial_capital=initial_capital,
                )

                funnel = _funnel_for_result(result)
                # Regime mode scores a regime-filtered subset of the
                # trades, so clearing min_regime_trades is direct
                # evidence the trial traded regardless of what the
                # (possibly uninstrumented) funnel carries.
                traded_override = False

                if regime is not None:
                    trades = self.adapter.get_regime_trades(result, regime)
                    trial.set_user_attr("regime_trade_count", len(trades))
                    if len(trades) < min_regime_trades:
                        raise optuna.TrialPruned(
                            f"Only {len(trades)} {regime} trades "
                            f"(< {min_regime_trades})"
                        )
                    value = self.adapter.calculate_objective_from_trades(
                        trades=trades,
                        objective=objective,
                        initial_capital=initial_capital,
                        start=start,
                        end=end,
                    )
                    traded_override = True
                else:
                    value = self.adapter.calculate_objective(result, objective)

                return self._score_trial(
                    trial,
                    funnel,
                    value,
                    params,
                    traded_override=traded_override,
                )

        except optuna.TrialPruned:
            raise
        except Exception as e:
            # No float("-inf") here: swallowing the exception recorded a
            # crash as a COMPLETE trial. Re-raise so Optuna's catch=
            # records it as FAIL and the study keeps going.
            logger.warning(f"Trial {trial.number} failed: {e}")
            raise

    def _score_trial(
        self,
        trial: "optuna.Trial",
        funnel: SignalFunnel,
        objective_value: float,
        params: Dict[str, Any],
        traded_override: bool = False,
    ) -> float:
        """Map a finished trial's funnel to a banded score and annotate it.

        A trial that traded keeps its objective, clamped at
        TRADED_SCORE_FLOOR so it can never fall into a zero-trade band.
        A trial that ran but never traded gets a reserved band plus its
        funnel depth, so TPE can rank "fired but was blocked at the last
        gate" above "never fired at all".

        Args:
            trial: The running Optuna trial (user_attrs are written here).
            funnel: Merged signal funnel for the trial's backtest(s).
            objective_value: The raw objective the adapter computed.
            params: Sampled parameters (used for the suggested fix).
            traded_override: Direct evidence of closed trades that the
                funnel may not carry (regime mode scores a filtered
                subset of trades, so the caller already knows).

        Returns:
            The trial value to report to Optuna.
        """
        payload = funnel.to_dict()
        if traded_override:
            outcome = TrialOutcome.TRADED
        else:
            outcome = TrialOutcome.from_diagnosis(funnel.diagnose())

        if outcome is TrialOutcome.TRADED:
            value = score_for_outcome(outcome, objective_value=objective_value)
        else:
            value = score_for_outcome(outcome, progress=funnel.progress())

        fix = suggest_fix(payload, params)
        trial.set_user_attr("outcome", outcome.value)
        trial.set_user_attr("headline", payload.get("headline", ""))
        trial.set_user_attr("binding_stage", payload.get("binding_stage", ""))
        trial.set_user_attr("funnel", payload.get("stages", {}))
        trial.set_user_attr("top_reasons", payload.get("top_reasons", []))
        trial.set_user_attr("by_strategy", payload.get("by_strategy", {}))
        trial.set_user_attr("regimes", payload.get("regimes", {}))
        trial.set_user_attr("progress", payload.get("progress", 0.0))
        trial.set_user_attr("suggested_fix", fix)
        # gate_metrics only exists once a strategy declares one (the
        # calibration phase). Omitted entirely when unavailable.
        gate_metrics = (payload.get("notes") or {}).get("gate_metrics")
        if gate_metrics:
            trial.set_user_attr("gate_metrics", gate_metrics)

        if outcome is not TrialOutcome.TRADED:
            logger.info(
                f"Trial {trial.number} scored {value:.2f} "
                f"[{outcome.value}]: {payload.get('headline', '')}"
            )
        return value

    def get_best_params(self, strategy: str) -> Dict[str, Any]:
        """
        Get the best parameters for a strategy.

        Args:
            strategy: Strategy name

        Returns:
            Dictionary of best parameters

        Raises:
            ValueError: If no completed study exists for the strategy
        """
        study_name = self._find_latest_study(strategy)
        if study_name is None:
            raise ValueError(f"No completed study found for strategy: {strategy}")

        study = optuna.load_study(
            study_name=study_name,
            storage=self.storage_url,
        )

        # best_trial raises ValueError when every trial was pruned/failed
        try:
            best_trial = study.best_trial
        except ValueError:
            best_trial = None

        if best_trial is None:
            return {}

        return best_trial.params

    def get_study_results(
        self, strategy: str, n_trials: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Get optimization results as a DataFrame.

        Args:
            strategy: Strategy name
            n_trials: Number of top trials to include (None = all)

        Returns:
            DataFrame with trial results sorted by objective value
        """
        study_name = self._find_latest_study(strategy)
        if study_name is None:
            return pd.DataFrame()

        study = optuna.load_study(
            study_name=study_name,
            storage=self.storage_url,
        )

        # Convert trials to DataFrame
        records = []
        for trial in study.trials:
            if trial.state == optuna.trial.TrialState.COMPLETE:
                record = {
                    "trial": trial.number,
                    "value": trial.value,
                    **trial.params,
                }
                records.append(record)

        df = pd.DataFrame(records)

        if df.empty:
            return df

        # Sort by value (descending)
        df = df.sort_values("value", ascending=False)

        if n_trials is not None:
            df = df.head(n_trials)

        return df.reset_index(drop=True)

    def list_studies(self, strategy: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        List all stored studies, optionally filtered by strategy.

        Args:
            strategy: Optional strategy name filter

        Returns:
            List of study metadata dictionaries
        """
        studies = []

        try:
            import sqlite3

            if not os.path.exists(self.db_path):
                return studies

            conn = sqlite3.connect(self.db_path, timeout=5)
            cursor = conn.cursor()

            # Check if studies table exists
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='studies'"
            )
            if not cursor.fetchone():
                conn.close()
                return studies

            cursor.execute("SELECT study_id, study_name FROM studies")
            rows = cursor.fetchall()

            for study_id, study_name in rows:
                if strategy and not study_name.startswith(strategy):
                    continue

                # Get trial count
                cursor.execute(
                    "SELECT COUNT(*) FROM trials WHERE study_id = ?", (study_id,)
                )
                trial_count = cursor.fetchone()[0]

                # Get best trial value (try different state representations)
                best_value = None
                for state_val in ("COMPLETE", 1, "1"):
                    cursor.execute(
                        "SELECT value FROM trials WHERE study_id = ? "
                        "AND state = ? AND value IS NOT NULL "
                        "ORDER BY value DESC LIMIT 1",
                        (study_id, state_val),
                    )
                    best_row = cursor.fetchone()
                    if best_row is not None:
                        best_value = best_row[0]
                        break

                studies.append({
                    "study_id": study_id,
                    "study_name": study_name,
                    "best_value": best_value,
                    "trial_count": trial_count,
                })

            conn.close()

        except Exception as e:
            logger.warning(f"Error listing studies: {e}")

        return studies

    def _find_latest_study(self, strategy: str) -> Optional[str]:
        """Find the most recent study for a strategy."""
        # Primary: use Optuna's native API (most reliable)
        try:
            storage = optuna.storages.RDBStorage(url=self.storage_url)
            all_studies = storage.get_all_studies()

            matching_names = [
                s.study_name for s in all_studies
                if s.study_name.startswith(strategy)
            ]

            if matching_names:
                # Return the latest by timestamp in name (lexicographic works for ISO timestamps)
                return max(matching_names)
        except Exception as e:
            logger.debug(f"Optuna API lookup failed, falling back to SQLite: {e}")

        # Fallback: scan SQLite directly
        studies = self.list_studies(strategy)
        if not studies:
            return None

        latest = max(studies, key=lambda s: s["study_name"])
        return latest["study_name"]

    def delete_study(self, strategy: str, study_name: Optional[str] = None) -> bool:
        """
        Delete a study.

        Args:
            strategy: Strategy name
            study_name: Specific study name (deletes latest if None)

        Returns:
            True if deleted, False if not found
        """
        if study_name is None:
            study_name = self._find_latest_study(strategy)

        if study_name is None:
            return False

        try:
            optuna.delete_study(
                study_name=study_name,
                storage=self.storage_url,
            )
            logger.info(f"Deleted study: {study_name}")
            return True
        except KeyError:
            logger.warning(f"Study not found: {study_name}")
            return False

    def export_results(
        self, strategy: str, output_path: str, n_trials: Optional[int] = None
    ) -> str:
        """
        Export optimization results to CSV.

        Args:
            strategy: Strategy name
            output_path: Output file path
            n_trials: Number of top trials to include

        Returns:
            Path to exported file
        """
        df = self.get_study_results(strategy, n_trials)

        if df.empty:
            logger.warning(f"No results to export for strategy: {strategy}")
            return ""

        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        df.to_csv(output_path, index=False)
        logger.info(f"Results exported to: {output_path}")

        return output_path
