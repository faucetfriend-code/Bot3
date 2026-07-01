"""
Optimization Module for Bot 3 Trading System
============================================

Provides automated parameter optimization using Optuna framework.

Components:
- search_spaces: Parameter search space definitions for all strategies
- optuna_runner: Optuna study runner with walk-forward validation
- run_optimize: CLI entry point for running optimizations
- optimization_adapter: Bridge between Optuna and BacktestEngine

Usage:
    # Import the main classes
    from trading_bot_v2.optimization import OptunaRunner, get_search_space

    # Run optimization
    runner = OptunaRunner()
    study = runner.optimize(
        strategy="mean_reversion",
        n_trials=100,
        objective="sharpe_ratio"
    )
    print(study.best_params)

    # Or use CLI
    # python -m trading_bot_v2.optimization --strategy mean_reversion --trials 100
"""

from .search_spaces import get_search_space, list_strategies, suggest_params
from .optuna_runner import OptunaRunner
from .run_optimize import main as run_optimize_cli

# Lazy import to avoid circular imports and optional dependencies
try:
    from ..backtesting.optimization_adapter import OptimizationAdapter
except ImportError:
    OptimizationAdapter = None

__all__ = [
    "OptunaRunner",
    "OptimizationAdapter",
    "get_search_space",
    "list_strategies",
    "suggest_params",
    "run_optimize_cli",
]

__version__ = "1.0.0"
