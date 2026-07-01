"""
Optimization Adapter
====================

Bridges Optuna parameter optimization with the BacktestEngine.
Converts Optuna trial parameters to strategy configurations and
runs backtests to evaluate performance.

Usage:
    from trading_bot_v2.backtesting.optimization_adapter import OptimizationAdapter

    adapter = OptimizationAdapter()
    sharpe = adapter.run_backtest(
        strategy="mean_reversion",
        params={"rsi_oversold": 30.0, "rsi_overbought": 70.0},
        start="2024-01-01",
        end="2024-06-30",
    )
"""

from typing import Dict, Any, Optional, List

from .engine import BacktestEngine
from .performance import BacktestResult


class OptimizationAdapter:
    """
    Adapter between Optuna optimization and BacktestEngine.

    Converts Optuna trial parameters into strategy configurations
    and runs backtests to return objective values (e.g., Sharpe ratio).
    """

    def __init__(self, config: Optional[Any] = None):
        """
        Initialize the optimization adapter.

        Args:
            config: Optional configuration override. Uses default config if None.
        """
        self.config = config
        self._engine_cache: Optional[BacktestEngine] = None

    def _get_engine(self) -> BacktestEngine:
        """Get or create a BacktestEngine instance."""
        if self._engine_cache is None:
            self._engine_cache = BacktestEngine(override_config=self.config)
        return self._engine_cache

    def run_backtest(
        self,
        strategy: str,
        params: Dict[str, Any],
        start: str,
        end: str,
        symbol: Optional[str] = None,
        initial_capital: float = 10000.0,
    ) -> BacktestResult:
        """
        Run a backtest with the given strategy parameters.

        Args:
            strategy: Strategy name (e.g., "mean_reversion")
            params: Dictionary of strategy parameters from Optuna
            start: Backtest start date (ISO format)
            end: Backtest end date (ISO format)
            symbol: Trading symbol (uses config default if None)
            initial_capital: Starting capital

        Returns:
            BacktestResult with performance metrics
        """
        # Create a config override with the optimized parameters
        config = self._create_strategy_config(strategy, params)

        # Create engine with the modified config
        engine = BacktestEngine(override_config=config)

        # Run the backtest
        result = engine.run(
            start=start,
            end=end,
            symbol=symbol,
            initial_capital=initial_capital,
            strategy_filter=strategy,
        )

        return result

    def calculate_objective(
        self,
        result: BacktestResult,
        objective: str = "sharpe_ratio",
        penalty_factor: float = 0.5,
    ) -> float:
        """
        Calculate the objective value from a backtest result.

        Args:
            result: BacktestResult from run_backtest()
            objective: Metric to optimize ("sharpe_ratio", "sortino_ratio",
                      "total_return_pct", "calmar_ratio")
            penalty_factor: Penalty for excessive drawdown (0-1)

        Returns:
            Objective value (higher is better)
        """
        # Get the primary metric
        if objective == "sharpe_ratio":
            value = result.sharpe_ratio
        elif objective == "sortino_ratio":
            value = result.sortino_ratio
        elif objective == "total_return_pct":
            value = result.total_return_pct
        elif objective == "calmar_ratio":
            value = result.calmar_ratio
        else:
            raise ValueError(f"Unknown objective: {objective}")

        # Apply drawdown penalty (penalize strategies with > 20% max drawdown)
        drawdown_penalty = 0.0
        if result.max_drawdown_pct > 20.0:
            excess_dd = result.max_drawdown_pct - 20.0
            drawdown_penalty = excess_dd * penalty_factor

        # Apply trade count penalty (penalize strategies with < 10 trades)
        trade_penalty = 0.0
        if result.closed_trades < 10:
            trade_penalty = (10 - result.closed_trades) * 0.1

        # Penalize negative returns
        return_penalty = 0.0
        if result.total_return_pct < 0:
            return_penalty = abs(result.total_return_pct) * 0.2

        return value - drawdown_penalty - trade_penalty - return_penalty

    def _create_strategy_config(
        self, strategy: str, params: Dict[str, Any]
    ) -> Any:
        """
        Create a config object with the optimized strategy parameters.

        This creates a proxy config that inherits from the base config
        but overrides specific strategy parameters.

        Args:
            strategy: Strategy name
            params: Parameters to override

        Returns:
            Config object with overridden parameters
        """
        from ..config import config as base_config

        # Create a simple config proxy that overrides attributes
        class StrategyConfigProxy:
            """Proxy that delegates to base config but allows attribute overrides."""

            def __init__(self, base: Any, overrides: Dict[str, Any]):
                object.__setattr__(self, "_base", base)
                object.__setattr__(self, "_overrides", overrides)

            def __getattr__(self, name: str) -> Any:
                overrides = object.__getattribute__(self, "_overrides")
                if name in overrides:
                    return overrides[name]
                base = object.__getattribute__(self, "_base")
                return getattr(base, name)

            def __setattr__(self, name: str, value: Any) -> None:
                overrides = object.__getattribute__(self, "_overrides")
                overrides[name] = value

        # Map strategy names to config attribute prefixes
        prefix_map = {
            "mean_reversion": "mean_reversion",
            "ma_crossover": "ma_crossover",
            "grid_trading": "grid",
            "liquidation_capture": "liquidation",
            "vwap_scalping": "vwap",
            "funding_arb": "funding",
            "momentum_scalping": "momentum",
            "orderbook_imbalance": "orderbook",
        }

        prefix_map.get(strategy, strategy)

        # Build overrides dict with proper naming convention
        overrides = {}
        for param_name, param_value in params.items():
            # Convert snake_case to the attribute name used in config
            # For strategy-specific params, we pass them directly to the strategy
            overrides[f"optimization_{strategy}_{param_name}"] = param_value

        # Also store the raw params for the adapter to use
        overrides[f"_optimization_params_{strategy}"] = params

        return StrategyConfigProxy(base_config, overrides)

    def run_walk_forward(
        self,
        strategy: str,
        params: Dict[str, Any],
        start: str,
        end: str,
        symbol: Optional[str] = None,
        initial_capital: float = 10000.0,
        train_months: int = 6,
        test_months: int = 1,
    ) -> List[BacktestResult]:
        """
        Run walk-forward validation with the given parameters.

        Args:
            strategy: Strategy name
            params: Strategy parameters
            start: Full date range start
            end: Full date range end
            symbol: Trading symbol
            initial_capital: Starting capital
            train_months: Training window size
            test_months: Test window size

        Returns:
            List of BacktestResult for each test window
        """
        from datetime import datetime, timedelta

        results = []
        dt_start = datetime.fromisoformat(start)
        dt_end = datetime.fromisoformat(end)

        # Build windows
        test_start = dt_start + timedelta(days=train_months * 30)

        while test_start < dt_end:
            test_end = min(
                test_start + timedelta(days=test_months * 30 - 1),
                dt_end,
            )

            # Run backtest on test window
            result = self.run_backtest(
                strategy=strategy,
                params=params,
                start=test_start.date().isoformat(),
                end=test_end.date().isoformat(),
                symbol=symbol,
                initial_capital=initial_capital,
            )
            result.start = test_start.date().isoformat()
            results.append(result)

            # Step forward
            test_start = test_start + timedelta(days=test_months * 30)

        return results

    def get_best_params(
        self, strategy: str, study: Any
    ) -> Dict[str, Any]:
        """
        Extract best parameters from an Optuna study.

        Args:
            strategy: Strategy name (for validation)
            study: Optuna study object

        Returns:
            Dictionary of best parameters
        """
        if study.best_trial is None:
            return {}

        return study.best_trial.params
