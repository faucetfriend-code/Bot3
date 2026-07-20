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

from .search_spaces import get_search_space, suggest_params, list_strategies
from ..backtesting.optimization_adapter import OptimizationAdapter
from ..regime_param_overlay import normalize_regime_value


# Default database path
DEFAULT_DB_PATH = "optimization_studies.db"

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

        # Run optimization
        try:
            study.optimize(
                objective_fn,
                n_trials=n_trials,
                timeout=timeout,
                n_jobs=n_jobs,
                show_progress_bar=True,
            )
        except Exception as e:
            logger.error(f"Optimization failed: {e}")
            raise

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
        FAIL/RUNNING/WAITING did not produce a scored config). When the
        objective is sharpe_ratio, also stores the sample variance
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
        completed = [t for t in study.trials if t.state == state.COMPLETE]
        pruned = [t for t in study.trials if t.state == state.PRUNED]
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
            f"(completed={len(completed)}, pruned={len(pruned)}) "
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
            Objective value (higher is better)

        Raises:
            optuna.TrialPruned: In regime mode, when fewer than
                min_regime_trades matching trades were produced.
        """
        # Suggest parameters
        params = suggest_params(trial, strategy)

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
                    return float("-inf")

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

                return avg_value

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

                if regime is not None:
                    trades = self.adapter.get_regime_trades(result, regime)
                    trial.set_user_attr("regime_trade_count", len(trades))
                    if len(trades) < min_regime_trades:
                        raise optuna.TrialPruned(
                            f"Only {len(trades)} {regime} trades "
                            f"(< {min_regime_trades})"
                        )
                    return self.adapter.calculate_objective_from_trades(
                        trades=trades,
                        objective=objective,
                        initial_capital=initial_capital,
                        start=start,
                        end=end,
                    )

                return self.adapter.calculate_objective(result, objective)

        except optuna.TrialPruned:
            raise
        except Exception as e:
            logger.warning(f"Trial {trial.number} failed: {e}")
            return float("-inf")

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
