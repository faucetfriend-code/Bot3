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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, Optional, List, Sequence, Tuple
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
from ..diagnostics.outcomes import (
    TRADED_SCORE_FLOOR,
    TrialOutcome,
    score_for_outcome,
    suggest_fix,
)
from .seeding import derive_seed
from ..regime_param_overlay import normalize_regime_value
from ..validation.statistics import closed_trade_returns


# Default database path
DEFAULT_DB_PATH = "optimization_studies.db"

#: Version tag mixed into every per-study sampler seed (see
#: ``study_seed``). It exists so a future change to the derivation is an
#: explicit, greppable version bump rather than a silent shift in every
#: study's opening draw.
STUDY_SEED_NAMESPACE = "optuna-study-seed/v1"

#: Default run-level seed for OptunaRunner. Every study's sampler seed
#: is derived from this plus the study's identity; moving it moves every
#: study together, which is the deliberate-reproduction knob.
DEFAULT_BASE_SEED = 0

# Version of the trial-scoring scheme. Stored on every study as the
# "scoring_schema" user attr; resuming a study written under a different
# (or missing) schema is refused, because mixing raw objective values
# with banded zero-trade scores would make best_trial meaningless.
SCORING_SCHEMA = 2

# Abort a run whose trials are mostly crashing, rather than burning the
# whole budget on a broken configuration.
FAIL_RATE_ABORT_THRESHOLD = 0.5
FAIL_RATE_MIN_TRIALS = 10

# Fallback minimum matching-regime trades before a trial is pruned, used
# only when the derived requirement cannot be computed. The literal 15 is
# LEGACY: it predates the promotion gate's derived sample floor, and a
# regime study that clears 15 produces a subset the gate then rejects as
# INSUFFICIENT_DATA. See regime_opt_min_trades().
DEFAULT_REGIME_OPT_MIN_TRADES = 15


def regime_opt_min_trades() -> int:
    """Minimum matching-regime trades a trial must produce to be scored.

    Derived from the SAME statistic the promotion gate uses
    (``validation.statistics.min_observations_for_sharpe`` at
    GATE_REFERENCE_SR / GATE_MIN_PSR, 33 at the defaults), so a regime
    study cannot spend its budget optimizing a subset that the gate would
    afterwards refuse to grade. ``REGIME_OPT_MIN_TRADES`` still overrides
    it explicitly.

    Returns:
        Minimum matching trades per trial (at least 1).
    """
    raw = os.getenv("REGIME_OPT_MIN_TRADES")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            logger.warning(
                f"REGIME_OPT_MIN_TRADES={raw!r} is not an integer - "
                f"falling back to the derived requirement"
            )
    try:
        from ..validation.gate import load_gate_policy

        derived = int(load_gate_policy()["min_closed_trades"])
        if derived > 0:
            return derived
    except Exception as e:  # noqa: BLE001 - never block a run on this
        logger.debug(f"Derived regime min-trades unavailable: {e}")
    return DEFAULT_REGIME_OPT_MIN_TRADES


def canonical_windows(
    windows: Optional[Sequence[Sequence[str]]],
) -> List[str]:
    """Canonicalise a window series for seed derivation.

    Sorted, so the same set of windows supplied in a different order
    cannot change the seed - a window series is a set of periods, not an
    ordered experiment.

    Args:
        windows: (start, end) ISO pairs, or None when the search has no
            window (nothing is contributed then).

    Returns:
        Sorted ``"start..end"`` strings.
    """
    if not windows:
        return []
    return sorted(f"{w[0]}..{w[1]}" for w in windows)


def study_seed(
    base_seed: int,
    strategy: str,
    symbols: Sequence[str],
    regime: Optional[str],
    objective: str,
    windows: Optional[Sequence[Sequence[str]]] = None,
) -> int:
    """Derive a study's Optuna sampler seed from the study's IDENTITY.

    Until 2026-08-02 ``_build_sampler`` hard-coded ``seed=42`` for every
    study it ever built, for every strategy, symbol, regime, objective
    and window. Measured consequence across the 13 studies stored in
    ``optimization_studies.db``: every one opens on the identical trial
    0. ``n_startup_trials`` is a FLOOR, not a cap - study 2 pruned every
    trial, so TPE acquired no observations and kept drawing from the
    seeded path, leaving 11 of its 25 trials identical to study 1's
    despite being a DIFFERENT REGIME. Heavy pruning is the normal
    failure mode of a regime study (the min-matching-trades gate), so
    the shared draw dominated exactly where it hurt most - and
    ``run_regime_optimization`` loops regimes in one process and prints
    them side by side as per-regime findings.

    The identity is the set of things that make two studies DIFFERENT
    EXPERIMENTS. It is exactly what already distinguishes a study name
    (strategy, regime, objective) plus the two things the name omits but
    that unambiguously change what is being searched: the symbols and
    the window series. Deliberately excluded: the sampler kind (tpe vs
    random over one identity is a paired methodological comparison, and
    sharing the opening draw removes nuisance variance from it), the
    trial budget (a 50-trial study must be the 100-trial study's prefix,
    which is what makes a short pilot informative about a long run), and
    the walk-forward scoring flags (a scoring variant over the same
    window, again paired).

    Args:
        base_seed: Run-level seed (``OptunaRunner(seed=...)``). Changing
            it moves every study together, so deliberate reproduction
            and deliberate re-draws both stay possible.
        strategy: Snake_case strategy key.
        symbols: Symbols scored inside the objective. Sorted before
            hashing, so the caller's ordering cannot change the seed -
            ``optimize_chunked`` scores several symbols per trial and
            the same sweep must reproduce whatever order they arrive in.
        regime: Normalized regime value, or None for a pooled study.
        objective: Canonical objective name.
        windows: (start, end) ISO pairs the study trains on.

    Returns:
        A 32-bit non-negative seed, identical for the same identity in
        any process, on any platform.
    """
    return derive_seed(
        STUDY_SEED_NAMESPACE,
        base_seed,
        [
            strategy,
            ",".join(sorted(str(s) for s in symbols)),
            (regime or "").upper(),
            objective,
            ";".join(canonical_windows(windows)),
        ],
    )


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


def classify_and_score(
    funnel: SignalFunnel,
    objective_value: float,
    traded_override: bool = False,
) -> Tuple[TrialOutcome, float]:
    """Map a funnel + raw objective onto the banded score scale.

    Shared by in-sample trial scoring and out-of-sample evaluation so
    both live on ONE scale. Comparing a raw out-of-sample Sharpe with a
    banded in-sample trial value would make the overfitting gap
    meaningless, and an out-of-sample run that never traded would look
    like "0.0" instead of naming the gate that blocked it.

    Args:
        funnel: Merged signal funnel of the run(s).
        objective_value: The raw objective the adapter computed.
        traded_override: Direct evidence of closed trades the funnel may
            not carry (regime mode scores a filtered trade subset).

    Returns:
        (outcome, banded score).
    """
    if traded_override:
        outcome = TrialOutcome.TRADED
    else:
        outcome = TrialOutcome.from_diagnosis(funnel.diagnose())
    if outcome is TrialOutcome.TRADED:
        return outcome, score_for_outcome(outcome, objective_value=objective_value)
    return outcome, score_for_outcome(outcome, progress=funnel.progress())


def traded_trial_values(trials: List[Any]) -> List[float]:
    """Objective values of trials that actually traded.

    Zero-trade trials carry reserved band scores (-100, -200, -300,
    -400), which are POSITIONS ON A RANK SCALE, not Sharpe estimates.
    Feeding them to a variance estimator inflates it by orders of
    magnitude, which inflates the DSR's expected-max-Sharpe benchmark,
    which makes the gate unpassable for reasons that have nothing to do
    with the search. Only values at or above TRADED_SCORE_FLOOR are real
    objectives.

    Args:
        trials: Optuna trials (or stubs exposing value/user_attrs).

    Returns:
        Finite objective values of the traded trials.
    """
    values: List[float] = []
    for trial in trials:
        value = getattr(trial, "value", None)
        if value is None or not math.isfinite(value):
            continue
        if value < TRADED_SCORE_FLOOR:
            continue
        values.append(float(value))
    return values


def _sample_variance(values: List[float]) -> Optional[float]:
    """Sample variance (ddof=1) of a value list, or None below n=2."""
    if len(values) < 2:
        return None
    mean_v = sum(values) / len(values)
    return sum((v - mean_v) ** 2 for v in values) / (len(values) - 1)


@dataclass
class ChunkEvaluation:
    """One parameter set evaluated over a (symbol, window) chunk grid.

    Attributes:
        value: Aggregate objective (mean across chunks, penalized for
            dispersion) - the raw, unbanded number.
        mean_objective: Plain mean across chunks, before the penalty.
        dispersion: Standard deviation of the per-chunk objectives.
        outcome: Funnel-derived outcome of the whole grid.
        banded_value: ``value`` mapped onto the banded score scale, so
            it is directly comparable with an Optuna trial value.
        funnel: Merged signal funnel across every chunk.
        per_symbol: Per-symbol rollup (trades, invoked, raw_signals,
            objective, return_pct).
        symbol_returns: Per-symbol pooled per-trade fractional returns.
        chunks: Per-chunk log rows (symbol, start, end, trades, value).
        total_trades: Closed trades across the grid.
        traded_symbols: Symbols with at least one closed trade.
        profitable_symbols: Symbols with a positive mean objective.
    """

    value: float
    mean_objective: float
    dispersion: float
    outcome: TrialOutcome
    banded_value: float
    funnel: SignalFunnel
    per_symbol: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    symbol_returns: Dict[str, List[float]] = field(default_factory=dict)
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    total_trades: int = 0
    traded_symbols: int = 0
    profitable_symbols: int = 0

    @property
    def returns(self) -> List[float]:
        """Pooled per-trade fractional returns across every symbol."""
        pooled: List[float] = []
        for values in self.symbol_returns.values():
            pooled.extend(values)
        return pooled


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
        seed: int = DEFAULT_BASE_SEED,
    ):
        """
        Initialize the Optuna runner.

        Args:
            db_path: Path to SQLite database for study persistence.
                     Defaults to "optimization_studies.db" in the module directory.
            config: Optional config override for backtesting.
            seed: Run-level BASE seed. Every study's sampler seed is
                derived from this plus the study's own identity (see
                ``study_seed``), so distinct studies stay independent
                while a pinned base still reproduces a whole run
                exactly - the same contract ``--seed`` has in the
                composite tuner.
        """
        if not OPTUNA_AVAILABLE:
            raise ImportError("Optuna is required. Install with: pip install optuna")

        # Set up database path
        if db_path is None:
            module_dir = Path(__file__).parent
            db_path = str(module_dir / DEFAULT_DB_PATH)
        self.db_path = db_path

        # Storage URL for Optuna
        self.storage_url = f"sqlite:///{db_path}"

        # Run-level base seed (see study_seed)
        self.seed = int(seed)

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
            raise ValueError(f"Unknown strategy: '{strategy}'. Available: {available}")

        # Get search space
        get_search_space(strategy)

        # Normalize objective and regime
        objective = normalize_objective(objective)
        if regime is not None:
            regime = normalize_regime_value(regime)
        if min_regime_trades is None:
            min_regime_trades = regime_opt_min_trades()

        # Set up dates from config if not provided
        from ..config import config as default_config

        cfg = self.config or default_config

        if start is None:
            start = cfg.backtest_start_date
        if end is None:
            end = cfg.backtest_end_date
        if symbol is None:
            symbol = cfg.backtest_symbol

        # Create sampler. The seed comes from the study's IDENTITY, not
        # a constant: a per-regime study must not open on the same draw
        # as its neighbour, or "the regimes agree" is partly an artifact
        # of the shared startup trials. See study_seed.
        seed = study_seed(
            base_seed=self.seed,
            strategy=strategy,
            symbols=[symbol],
            regime=regime,
            objective=objective,
            windows=[(start, end)],
        )
        optuna_sampler = self._build_sampler(sampler, n_trials, seed)

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
        self._record_sampler_seed(study, seed)

        logger.info(
            f"Starting optimization: strategy={strategy}, trials={n_trials}, "
            f"sampler={sampler}, objective={objective}, "
            f"walk_forward={walk_forward}, seed={seed}"
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
                "Optimization completed with no valid trials (all pruned or failed)"
            )

        # P5: record how many configurations this study tried so the
        # Deflated Sharpe Ratio can discount for search size later.
        try:
            self._record_trial_registry(study, strategy, regime, objective)
        except Exception as e:
            logger.warning(f"Trial registry recording failed: {e}")

        return study

    def optimize_chunked(
        self,
        strategy: str,
        symbols: List[str],
        n_trials: int = 50,
        sampler: str = "tpe",
        objective: str = "sharpe_ratio",
        window_months: int = 2,
        n_windows: int = 3,
        initial_capital: float = 10000.0,
        timeout: Optional[int] = None,
        data_dir: Optional[str] = None,
        end: Optional[str] = None,
        windows: Optional[List[Tuple[str, str]]] = None,
        study_suffix: str = "",
        record_registry: bool = True,
        regime: Optional[str] = None,
        min_regime_trades: Optional[int] = None,
    ) -> "optuna.Study":
        """Optimize a strategy over chunked windows and multiple symbols.

        Each trial backtests the sampled parameters on every
        (symbol, window) chunk and scores the aggregate, so the search
        cannot latch onto one lucky symbol or one lucky quarter. Window
        boundaries come from validation.runner.resolve_chunk_windows -
        the same series the standing validation gate uses, so a sweep
        result and a gate verdict describe the same periods.

        Per-symbol results are attached to every trial (``per_symbol``
        user attr) and printed by the CLI, so cross-symbol consistency -
        an actual gate check - stays visible instead of being averaged
        away. The merged signal funnel is attached too, so a trial that
        lands zero trades still names the gate that killed it.

        Args:
            strategy: Strategy name (snake_case, e.g. "ma_crossover").
            symbols: Trading pairs to sweep (evaluated on identical
                windows; symbols with no candle data are dropped).
            n_trials: Number of optimization trials.
            sampler: "tpe" or "random".
            objective: Metric to optimize (short forms accepted).
            window_months: Months per chunk window.
            n_windows: Number of chunk windows (newest-first, abutting).
            initial_capital: Starting capital per chunk backtest.
            timeout: Timeout in seconds (None = no limit).
            data_dir: Candle data dir override (default: config).
            end: Optional ISO date to anchor the newest window on. Use it
                when the automatic anchor (data end, clamped to 1m
                coverage) lands in a gap of the timeframe the strategy
                actually reads - 5m coverage cannot see a hole in the 4h
                store, and the affected symbol would silently contribute
                zero bars.
            windows: Explicit (start, end) window series. When given,
                window resolution is skipped entirely and the symbols
                are used as supplied. This is the hook the chunked
                walk-forward uses to train a fold on its TRAIN windows
                only - a test window must never reach a training study.
            study_suffix: Appended to the generated study name, so the
                per-fold studies of one walk-forward run stay distinct
                and individually inspectable with diagnostics.explain.
            record_registry: Write this study's trial count to the
                trial_registry. Leave True: the deflation path reads it,
                and the walk-forward records every fold so the gate's N
                covers every configuration explored.
            regime: Optional regime value. When set, every trial is
                scored only on closed trades whose ENTRY regime matches,
                and trials matching fewer than min_regime_trades are
                pruned. Note the trial_registry entry is tagged with the
                regime, but validation.runner reads the strategy's TOTAL
                trial count - so per-regime studies deflate the pooled
                strategy's Sharpe too. That is intentional: they are
                configurations explored against the same strategy.
            min_regime_trades: Pruning threshold for regime mode
                (default: the gate-derived requirement, 33 at the
                current gate settings).

        Returns:
            The completed Optuna study.

        Raises:
            ValueError: If the strategy is unknown or no usable windows
                exist for the requested symbols.
            RuntimeError: If most trials crash (see _FailRateGuard).
        """
        available = list_strategies()
        if strategy not in available:
            raise ValueError(f"Unknown strategy: '{strategy}'. Available: {available}")
        get_search_space(strategy)
        objective = normalize_objective(objective)
        if regime is not None:
            regime = normalize_regime_value(regime)
        if min_regime_trades is None and regime is not None:
            min_regime_trades = regime_opt_min_trades()

        if windows:
            sweep_symbols: List[str] = list(symbols)
            sweep_windows: List[Any] = [tuple(w) for w in windows]
        else:
            from ..validation.runner import resolve_chunk_windows

            resolved = resolve_chunk_windows(
                symbols,
                window_months,
                n_windows,
                data_dir=data_dir,
                label=f"{strategy}/sweep",
                anchor_end=end,
            )
            if resolved.get("reason"):
                raise ValueError(
                    f"Cannot sweep {strategy}: {resolved['reason']} "
                    f"(symbols={','.join(symbols)})"
                )
            sweep_symbols = resolved["symbols"]
            sweep_windows = resolved["windows"]

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        regime_tag = f"_{regime.upper()}" if regime else ""
        study_name = f"{strategy}_chunked{regime_tag}_{timestamp}{study_suffix}"
        # Identity-derived seed. Note this is also what makes the
        # chunked WALK-FORWARD sound: each fold trains on a different
        # window series, so each fold's study now draws differently
        # instead of every fold repeating the same opening trials.
        seed = study_seed(
            base_seed=self.seed,
            strategy=strategy,
            symbols=sweep_symbols,
            regime=regime,
            objective=objective,
            windows=sweep_windows,
        )
        study = optuna.create_study(
            study_name=study_name,
            storage=self.storage_url,
            load_if_exists=True,
            direction="maximize",
            sampler=self._build_sampler(sampler, n_trials, seed),
        )
        _assert_scoring_schema(study)
        self._record_sampler_seed(study, seed)
        study.set_user_attr("sweep_symbols", sweep_symbols)
        study.set_user_attr("sweep_windows", sweep_windows)
        study.set_user_attr("sweep_objective", objective)
        if regime:
            study.set_user_attr("sweep_regime", regime)

        logger.info(
            f"Chunked sweep: strategy={strategy}, trials={n_trials}, "
            f"symbols={','.join(sweep_symbols)}, "
            f"windows={len(sweep_windows)}x{window_months}mo "
            f"({sweep_windows[0][0]} .. {sweep_windows[-1][1]}), "
            f"objective={objective}, seed={seed}"
            + (
                f", regime={regime} (min {min_regime_trades} matching trades/trial)"
                if regime
                else ""
            )
            + f" -> {len(sweep_symbols) * len(sweep_windows)} backtests/trial"
        )

        def objective_fn(trial: "optuna.Trial") -> float:
            return self._chunked_objective(
                trial=trial,
                strategy=strategy,
                objective=objective,
                symbols=sweep_symbols,
                windows=sweep_windows,
                initial_capital=initial_capital,
                regime=regime,
                min_regime_trades=min_regime_trades,
            )

        abort = _FailRateGuard(study_name)
        try:
            study.optimize(
                objective_fn,
                n_trials=n_trials,
                timeout=timeout,
                show_progress_bar=True,
                catch=(Exception,),
                callbacks=[abort],
            )
        except Exception as e:
            logger.error(f"Chunked sweep failed: {e}")
            raise
        abort.raise_if_aborted()

        if record_registry:
            try:
                self._record_trial_registry(study, strategy, regime, objective)
            except Exception as e:
                logger.warning(f"Trial registry recording failed: {e}")

        return study

    def _record_sampler_seed(self, study: "optuna.Study", seed: int) -> None:
        """Persist the resolved sampler seed on the study.

        Written into the study's own metadata so a stored study is
        self-describing: a reader can see which seed produced its trial
        sequence, and which base seed and derivation version to pass to
        reproduce it, without re-deriving anything by hand.

        Args:
            study: The freshly created (or resumed) study.
            seed: The resolved sampler seed.
        """
        study.set_user_attr("sampler_seed", int(seed))
        study.set_user_attr("sampler_seed_base", int(self.seed))
        study.set_user_attr("sampler_seed_namespace", STUDY_SEED_NAMESPACE)

    def _build_sampler(self, sampler: str, n_trials: int, seed: int) -> Any:
        """Construct the Optuna sampler for a study.

        The seed is REQUIRED and comes from ``study_seed``; it is not
        defaulted here on purpose, because the defect this replaced was
        precisely a default (``seed=42``) that every caller silently
        inherited.

        Args:
            sampler: "tpe" or "random".
            n_trials: Trial budget (sets TPE's startup trials).
            seed: Identity-derived sampler seed.

        Returns:
            An Optuna sampler.

        Raises:
            ValueError: On an unknown sampler name.
        """
        if sampler.lower() == "tpe":
            return optuna.samplers.TPESampler(
                seed=seed,
                n_startup_trials=min(20, n_trials // 5),
            )
        if sampler.lower() == "random":
            return optuna.samplers.RandomSampler(seed=seed)
        raise ValueError(f"Unknown sampler: '{sampler}'. Use 'tpe' or 'random'.")

    def _suggest_or_prune(self, trial: "optuna.Trial", strategy: str) -> Dict[str, Any]:
        """Sample parameters, pruning structurally infeasible regions.

        Combinations that could never produce a tradeable signal are
        pruned up front with the reason attached, instead of being
        backtested into a silent zero-trade result.

        Args:
            trial: The running Optuna trial.
            strategy: Strategy name.

        Returns:
            The sampled parameters.

        Raises:
            optuna.TrialPruned: When the sampled region is infeasible.
        """
        try:
            return suggest_params(trial, strategy)
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

    def evaluate_param_set(
        self,
        strategy: str,
        params: Dict[str, Any],
        symbols: List[str],
        windows: List[Any],
        objective: str,
        initial_capital: float = 10000.0,
        label: Optional[str] = None,
        regime: Optional[str] = None,
    ) -> ChunkEvaluation:
        """Evaluate ONE parameter set over a (symbol, window) chunk grid.

        This is the single scoring path used both for in-sample trials
        and for out-of-sample evaluation of a fold's winner. Sharing it
        is the point: an in-sample number and an out-of-sample number
        computed by different code would not be comparable, and the
        overfitting gap between them is the headline signal.

        Args:
            strategy: Strategy name (snake_case).
            params: Strategy parameters to apply.
            symbols: Trading pairs to evaluate.
            windows: (start, end) ISO date pairs.
            objective: Canonical objective name.
            initial_capital: Starting capital per chunk backtest.
            label: Funnel label (default: "<strategy>/chunks").
            regime: Optional regime value. When set, every chunk is
                scored ONLY on closed trades whose ENTRY regime matches,
                and trade counts / pooled returns describe that subset.
                Chunks with no matching trade are excluded from the mean
                rather than scored as zero, mirroring the walk-forward
                branch of ``_objective``.

        Returns:
            A ChunkEvaluation with the aggregate value, the banded
            score, the merged funnel and the per-symbol breakdown.

        Raises:
            RuntimeError: When the grid produced no backtests at all.
        """
        target_regime = normalize_regime_value(regime) if regime is not None else None
        funnel = SignalFunnel(label=label or f"{strategy}/chunks")
        chunk_values: List[float] = []
        per_symbol: Dict[str, Dict[str, Any]] = {}
        symbol_returns: Dict[str, List[float]] = {}
        chunk_log: List[Dict[str, Any]] = []
        backtests_run = 0

        for symbol in symbols:
            values: List[float] = []
            pooled: List[float] = []
            trades = 0
            invoked = 0
            raw_signals = 0
            return_pct = 0.0
            for start, end in windows:
                result = self.adapter.run_backtest(
                    strategy=strategy,
                    params=params,
                    start=start,
                    end=end,
                    symbol=symbol,
                    initial_capital=initial_capital,
                )
                backtests_run += 1
                chunk_funnel = _funnel_for_result(result)
                funnel.merge(chunk_funnel)
                invoked += chunk_funnel.get(STAGE_STRATEGY_INVOKED)
                raw_signals += chunk_funnel.get(STAGE_RAW_SIGNALS)

                if target_regime is not None:
                    matched = self.adapter.get_regime_trades(result, target_regime)
                    closed = len(matched)
                    pnls = [float(t.get("pnl", 0)) for t in matched]
                    return_pct += (
                        sum(pnls) / initial_capital * 100
                        if initial_capital > 0
                        else 0.0
                    )
                    if initial_capital > 0:
                        pooled.extend(p / initial_capital for p in pnls)
                    value = (
                        self.adapter.calculate_objective_from_trades(
                            trades=matched,
                            objective=objective,
                            initial_capital=initial_capital,
                            start=start,
                            end=end,
                        )
                        if matched
                        else None
                    )
                else:
                    closed = int(getattr(result, "closed_trades", 0) or 0)
                    return_pct += float(getattr(result, "total_return_pct", 0.0) or 0.0)
                    pooled.extend(
                        closed_trade_returns(
                            getattr(result, "trade_log", None) or [],
                            initial_capital,
                        )
                    )
                    value = self.adapter.calculate_objective(result, objective)

                trades += closed
                if value is not None:
                    values.append(value)
                    chunk_values.append(value)
                chunk_log.append(
                    {
                        "symbol": symbol,
                        "start": start,
                        "end": end,
                        "trades": closed,
                        "value": round(value, 6) if value is not None else None,
                    }
                )
            # invoked / raw are carried per symbol so a symbol that
            # contributed NO BARS AT ALL (a hole in the timeframe the
            # strategy reads, which 5m-based window resolution cannot
            # see) is distinguishable from one that ran and found
            # nothing.
            per_symbol[symbol] = {
                "trades": trades,
                "invoked": invoked,
                "raw_signals": raw_signals,
                "objective": (round(sum(values) / len(values), 6) if values else 0.0),
                "return_pct": round(return_pct, 4),
            }
            symbol_returns[symbol] = pooled

        if not backtests_run:
            raise RuntimeError(
                f"chunked evaluation produced no backtests for {strategy} "
                f"(symbols={symbols}, windows={windows})"
            )

        # In regime mode a parameter set can run every chunk and still
        # match no trade in the target regime. That is a zero-trade
        # outcome to be banded by the funnel, not a setup failure.
        mean_value = sum(chunk_values) / len(chunk_values) if chunk_values else 0.0
        std_dev = 0.0
        value = mean_value
        # Penalize dispersion across chunks, mirroring the walk-forward
        # branch: a parameter set that only works in one window/symbol
        # should not outrank a steadier one at the same mean.
        if len(chunk_values) > 1:
            variance = sum((v - mean_value) ** 2 for v in chunk_values) / len(
                chunk_values
            )
            std_dev = variance**0.5
            if std_dev > 1.0:
                value -= (std_dev - 1.0) * 0.2

        matched_trades = sum(s["trades"] for s in per_symbol.values())
        if target_regime is not None and matched_trades == 0:
            # The run traded, but nothing in the target regime. The
            # merged funnel says "traded" and would score this 0.0,
            # ranking a regime no-show above every genuine loss. Band it
            # as a no-opportunity outcome instead.
            outcome = TrialOutcome.NO_OPPORTUNITIES
            banded = score_for_outcome(outcome, progress=funnel.progress())
        else:
            outcome, banded = classify_and_score(
                funnel, value, traded_override=target_regime is not None
            )
        return ChunkEvaluation(
            value=value,
            mean_objective=mean_value,
            dispersion=std_dev,
            outcome=outcome,
            banded_value=banded,
            funnel=funnel,
            per_symbol=per_symbol,
            symbol_returns=symbol_returns,
            chunks=chunk_log,
            total_trades=sum(s["trades"] for s in per_symbol.values()),
            traded_symbols=sum(1 for s in per_symbol.values() if s["trades"] > 0),
            profitable_symbols=sum(
                1 for s in per_symbol.values() if s["objective"] > 0
            ),
        )

    def _chunked_objective(
        self,
        trial: "optuna.Trial",
        strategy: str,
        objective: str,
        symbols: List[str],
        windows: List[Any],
        initial_capital: float,
        regime: Optional[str] = None,
        min_regime_trades: Optional[int] = None,
    ) -> float:
        """Score one trial across every (symbol, window) chunk.

        Args:
            trial: The running Optuna trial.
            strategy: Strategy name.
            objective: Canonical objective name.
            symbols: Trading pairs to evaluate.
            windows: (start, end) ISO date pairs.
            initial_capital: Starting capital per chunk.
            regime: Optional regime value. When set, only closed trades
                entered in that regime are scored, and a trial matching
                fewer than min_regime_trades of them is pruned.
            min_regime_trades: Pruning threshold for regime mode
                (default: the gate-derived requirement).

        Returns:
            The trial value: the mean chunk objective (penalized for
            dispersion across chunks) when the trial traded, else the
            reserved band for its funnel diagnosis.

        Raises:
            optuna.TrialPruned: On infeasible params, or on too few
                matching-regime trades.
            Exception: Any backtest failure, so Optuna records a FAIL.
        """
        params = self._suggest_or_prune(trial, strategy)
        logger.debug(f"Trial {trial.number}: params={params}")

        evaluation = self.evaluate_param_set(
            strategy=strategy,
            params=params,
            symbols=symbols,
            windows=windows,
            objective=objective,
            initial_capital=initial_capital,
            label=f"{strategy}/sweep",
            regime=regime,
        )

        trial.set_user_attr("per_symbol", evaluation.per_symbol)
        trial.set_user_attr("chunks", evaluation.chunks)
        trial.set_user_attr("total_trades", evaluation.total_trades)
        trial.set_user_attr("traded_symbols", evaluation.traded_symbols)
        trial.set_user_attr("profitable_symbols", evaluation.profitable_symbols)

        if regime is not None:
            trial.set_user_attr("regime_trade_count", evaluation.total_trades)
            threshold = (
                regime_opt_min_trades()
                if min_regime_trades is None
                else min_regime_trades
            )
            if evaluation.total_trades < threshold:
                raise optuna.TrialPruned(
                    f"Only {evaluation.total_trades} {regime} trades (< {threshold})"
                )

        return self._score_trial(
            trial,
            evaluation.funnel,
            evaluation.value,
            params,
            traded_override=regime is not None,
        )

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
        (ddof=1) of the TRADED completed trials' objective values as an
        sr-variance proxy for DSR benchmarks; for other objectives the
        variance column stays NULL. Zero-trade trials are excluded: their
        values are reserved rank bands, not Sharpe estimates.

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
            # Only trials that TRADED carry a real objective. Zero-trade
            # trials carry reserved band scores (-100 .. -400); mixing
            # them in produced variances in the thousands, an absurd
            # expected-max-Sharpe benchmark, and a DSR pinned at 0 for
            # reasons unrelated to the search.
            sr_variance = _sample_variance(traded_trial_values(completed))

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
            f"for {strategy}" + (f"/{regime}" if regime else "") + f" as row {row_id}"
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
        params = self._suggest_or_prune(trial, strategy)

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
                        (r, self.adapter.get_regime_trades(r, regime)) for r in results
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
                        self.adapter.calculate_objective(r, objective) for r in results
                    ]

                avg_value = sum(values) / len(values)

                # Penalize high variance across windows
                if len(values) > 1:
                    variance = sum((v - avg_value) ** 2 for v in values) / len(values)
                    std_dev = variance**0.5
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
        outcome, value = classify_and_score(
            funnel, objective_value, traded_override=traded_override
        )

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

                studies.append(
                    {
                        "study_id": study_id,
                        "study_name": study_name,
                        "best_value": best_value,
                        "trial_count": trial_count,
                    }
                )

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
                s.study_name for s in all_studies if s.study_name.startswith(strategy)
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
