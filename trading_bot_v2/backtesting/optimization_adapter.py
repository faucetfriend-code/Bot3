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

import math
from datetime import datetime
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
        elif objective == "profit_factor":
            # Cap so a small all-winner sample cannot dominate
            value = min(result.profit_factor, 10.0)
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

    def _create_strategy_config(self, strategy: str, params: Dict[str, Any]) -> Any:
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

    def get_regime_trades(
        self, result: BacktestResult, regime: str
    ) -> List[Dict[str, Any]]:
        """
        Filter closed trades whose entry state matches the target.

        Accepts a plain regime ("RANGING_CALM", "vol_low") or a
        composite "REGIME:DIRECTION" key ("VOL_LOW:TREND") matching the
        trade log's (regime, direction) tags. Direction "trend" matches
        bull OR bear; "bull"/"bear"/"neutral" match exactly.

        Args:
            result: BacktestResult with a regime/direction-tagged trade_log
            regime: Target state (enum value or name, case-insensitive)

        Returns:
            List of closed-trade dicts (pnl != 0) matching the state
        """
        target = str(getattr(regime, "value", regime)).strip().lower()
        direction = None
        if ":" in target:
            target, direction = target.split(":", 1)

        def _direction_matches(trade: Dict[str, Any]) -> bool:
            if direction is None:
                return True
            tag = str(trade.get("direction", "")).strip().lower()
            if direction == "trend":
                return tag in ("bull", "bear")
            return tag == direction

        return [
            t
            for t in result.trade_log
            if t.get("pnl", 0) != 0
            and str(t.get("regime", "")).strip().lower() == target
            and _direction_matches(t)
        ]

    def calculate_objective_from_trades(
        self,
        trades: List[Dict[str, Any]],
        objective: str,
        initial_capital: float,
        start: str,
        end: str,
        penalty_factor: float = 0.5,
    ) -> float:
        """
        Compute an objective value from a subset of closed trades.

        Used for regime-conditional optimization, where full-run metrics
        (equity-curve Sharpe etc.) cannot be reused because they mix
        trades from all regimes. Metrics here are trade-based:

        - sharpe_ratio / sortino_ratio: mean/std of per-trade returns,
          annualised by the subset's trade frequency over the window
        - total_return_pct: sum(pnl) / initial_capital * 100
        - calmar_ratio: annualised return over max drawdown of the
          subset's cumulative-PnL curve
        - profit_factor: gross profit / gross loss, capped at 10

        The existing drawdown (>20%) and negative-return penalties are
        applied on the subset; the low-trade-count penalty is replaced
        by min-trades pruning in the runner.

        Args:
            trades: Closed-trade dicts carrying "pnl"
            objective: Metric name (canonical long form)
            initial_capital: Starting capital of the backtest
            start: Window start date (ISO)
            end: Window end date (ISO)
            penalty_factor: Penalty per excess drawdown percent

        Returns:
            Objective value (higher is better)

        Raises:
            ValueError: If trades is empty or objective unknown
        """
        if not trades:
            raise ValueError("Cannot compute objective from empty trade list")

        pnls = [float(t.get("pnl", 0)) for t in trades]
        n = len(pnls)
        total_pnl = sum(pnls)
        total_return_pct = total_pnl / initial_capital * 100

        try:
            days = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
        except (ValueError, TypeError):
            days = 365
        years = max(days, 1) / 365.25
        trades_per_year = n / years
        annualised_return_pct = total_return_pct / years

        returns = [p / initial_capital for p in pnls]
        mean_r = sum(returns) / n
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / n)

        # Max drawdown of the subset's cumulative-PnL equity curve
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
        max_dd_pct = max_dd * 100

        if objective == "sharpe_ratio":
            value = mean_r / std_r * math.sqrt(trades_per_year) if std_r > 0 else 0.0
        elif objective == "sortino_ratio":
            downside = [r for r in returns if r < 0]
            if downside:
                downside_std = math.sqrt(sum(r**2 for r in downside) / len(downside))
                value = (
                    mean_r / downside_std * math.sqrt(trades_per_year)
                    if downside_std > 0
                    else 0.0
                )
            else:
                # No losing trades: fall back to the Sharpe form
                value = (
                    mean_r / std_r * math.sqrt(trades_per_year) if std_r > 0 else 0.0
                )
        elif objective == "total_return_pct":
            value = total_return_pct
        elif objective == "calmar_ratio":
            value = (
                annualised_return_pct / max_dd_pct
                if max_dd_pct > 0
                else annualised_return_pct
            )
        elif objective == "profit_factor":
            gross_profit = sum(p for p in pnls if p > 0)
            gross_loss = abs(sum(p for p in pnls if p < 0))
            if gross_loss > 0:
                value = min(gross_profit / gross_loss, 10.0)
            else:
                value = 10.0 if gross_profit > 0 else 0.0
        else:
            raise ValueError(f"Unknown objective: {objective}")

        # Drawdown penalty (mirror calculate_objective)
        if max_dd_pct > 20.0:
            value -= (max_dd_pct - 20.0) * penalty_factor

        # Negative-return penalty (mirror calculate_objective)
        if total_return_pct < 0:
            value -= abs(total_return_pct) * 0.2

        return value

    def get_best_params(self, strategy: str, study: Any) -> Dict[str, Any]:
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
