"""
Tests for the Optuna Parameter Optimization Module
==================================================

Tests cover:
- Search space definitions
- Parameter suggestion
- Optimization adapter
- OptunaRunner
- CLI interface
"""

import os
import tempfile
from pathlib import Path
from typing import Dict, Any
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

# Skip all tests if optuna is not installed
pytest.importorskip("optuna")

from trading_bot_v2.optimization.search_spaces import (
    get_search_space,
    list_strategies,
    suggest_params,
    get_param_type,
    PARAMETER_TYPES,
)
from trading_bot_v2.optimization.optuna_runner import OptunaRunner
from trading_bot_v2.backtesting.optimization_adapter import OptimizationAdapter


# ---------------------------------------------------------------------------
# Search Space Tests
# ---------------------------------------------------------------------------


class TestSearchSpaces:
    """Tests for search space definitions."""

    def test_list_strategies(self):
        """Test that list_strategies returns all 8 strategies."""
        strategies = list_strategies()
        assert len(strategies) == 8
        assert "mean_reversion" in strategies
        assert "ma_crossover" in strategies
        assert "grid_trading" in strategies
        assert "liquidation_capture" in strategies
        assert "vwap_scalping" in strategies
        assert "funding_arb" in strategies
        assert "momentum_scalping" in strategies
        assert "orderbook_imbalance" in strategies

    def test_get_search_space_valid(self):
        """Test that get_search_space returns valid spaces for all strategies."""
        for strategy in list_strategies():
            space = get_search_space(strategy)
            assert isinstance(space, dict)
            assert len(space) > 0

            # Check that all values are tuples or lists
            for param_name, param_range in space.items():
                assert isinstance(param_name, str)
                if isinstance(param_range, tuple):
                    assert len(param_range) == 2
                    low, high = param_range
                    assert low < high
                elif isinstance(param_range, list):
                    assert len(param_range) > 0
                else:
                    pytest.fail(f"Invalid param range type: {type(param_range)}")

    def test_get_search_space_invalid(self):
        """Test that get_search_space raises ValueError for unknown strategy."""
        with pytest.raises(ValueError, match="Unknown strategy"):
            get_search_space("nonexistent_strategy")

    def test_get_param_type(self):
        """Test that get_param_type returns correct types."""
        for strategy, params in PARAMETER_TYPES.items():
            for param_name, param_type in params.items():
                assert param_type in ("int", "float")
                returned_type = get_param_type(strategy, param_name)
                assert returned_type == param_type

    def test_suggest_params(self):
        """Test that suggest_params returns valid parameters."""
        import optuna

        for strategy in list_strategies():
            study = optuna.create_study(direction="maximize")
            trial = study.ask()

            params = suggest_params(trial, strategy)

            assert isinstance(params, dict)
            assert len(params) > 0

            # Check all parameters have correct types
            space = get_search_space(strategy)
            for param_name in space:
                assert param_name in params
                param_type = get_param_type(strategy, param_name)
                value = params[param_name]

                if param_type == "int":
                    assert isinstance(value, int)
                else:
                    assert isinstance(value, (int, float))

    def test_mean_reversion_space_ranges(self):
        """Test specific parameter ranges for Mean Reversion."""
        space = get_search_space("mean_reversion")

        assert space["rsi_oversold"] == (25.0, 45.0)
        assert space["rsi_overbought"] == (55.0, 75.0)
        assert space["bb_std_dev"] == (1.5, 3.0)
        assert space["atr_stop_multiplier"] == (1.5, 3.0)

    def test_ma_crossover_space_ranges(self):
        """Test specific parameter ranges for MA Crossover."""
        space = get_search_space("ma_crossover")

        fast_low, fast_high = space["fast_ma_period"]
        slow_low, slow_high = space["slow_ma_period"]

        # Fast should be < Slow
        assert fast_high < slow_low


# ---------------------------------------------------------------------------
# OptimizationAdapter Tests
# ---------------------------------------------------------------------------


class TestOptimizationAdapter:
    """Tests for the optimization adapter."""

    def test_adapter_init(self):
        """Test adapter initialization."""
        adapter = OptimizationAdapter()
        assert adapter.config is None
        assert adapter._engine_cache is None

    def test_adapter_init_with_config(self):
        """Test adapter initialization with config."""
        mock_config = MagicMock()
        adapter = OptimizationAdapter(config=mock_config)
        assert adapter.config is mock_config

    def test_calculate_objective_sharpe(self):
        """Test objective calculation for Sharpe ratio."""
        from trading_bot_v2.backtesting.performance import BacktestResult

        result = BacktestResult(
            symbol="TEST-USDC",
            start="2024-01-01",
            end="2024-12-31",
            initial_capital=10000.0,
            final_equity=12000.0,
            sharpe_ratio=1.5,
            sortino_ratio=2.0,
            total_return_pct=20.0,
            calmar_ratio=2.5,
            max_drawdown_pct=10.0,
            closed_trades=50,
        )

        adapter = OptimizationAdapter()
        value = adapter.calculate_objective(result, "sharpe_ratio")

        # With 10% drawdown, no penalty
        assert value == 1.5

    def test_calculate_objective_drawdown_penalty(self):
        """Test that high drawdown triggers penalty."""
        from trading_bot_v2.backtesting.performance import BacktestResult

        result = BacktestResult(
            symbol="TEST-USDC",
            start="2024-01-01",
            end="2024-12-31",
            initial_capital=10000.0,
            final_equity=12000.0,
            sharpe_ratio=1.5,
            max_drawdown_pct=30.0,  # > 20% threshold
            closed_trades=50,
        )

        adapter = OptimizationAdapter()
        value = adapter.calculate_objective(result, "sharpe_ratio")

        # 30% DD - 20% threshold = 10% excess * 0.5 penalty = 5.0
        expected = 1.5 - 5.0
        assert abs(value - expected) < 0.01

    def test_calculate_objective_trade_penalty(self):
        """Test that low trade count triggers penalty."""
        from trading_bot_v2.backtesting.performance import BacktestResult

        result = BacktestResult(
            symbol="TEST-USDC",
            start="2024-01-01",
            end="2024-12-31",
            initial_capital=10000.0,
            final_equity=12000.0,
            sharpe_ratio=1.5,
            max_drawdown_pct=10.0,
            closed_trades=5,  # < 10 trades
        )

        adapter = OptimizationAdapter()
        value = adapter.calculate_objective(result, "sharpe_ratio")

        # (10 - 5) * 0.1 = 0.5 penalty
        expected = 1.5 - 0.5
        assert abs(value - expected) < 0.01

    def test_calculate_objective_invalid(self):
        """Test that invalid objective raises ValueError."""
        from trading_bot_v2.backtesting.performance import BacktestResult

        result = BacktestResult(
            symbol="TEST-USDC",
            start="2024-01-01",
            end="2024-12-31",
            initial_capital=10000.0,
            final_equity=10000.0,
        )

        adapter = OptimizationAdapter()
        with pytest.raises(ValueError, match="Unknown objective"):
            adapter.calculate_objective(result, "invalid_metric")


# ---------------------------------------------------------------------------
# OptunaRunner Tests
# ---------------------------------------------------------------------------


class TestOptunaRunner:
    """Tests for the OptunaRunner."""

    def test_runner_init(self):
        """Test runner initialization."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            runner = OptunaRunner(db_path=db_path)
            assert runner.db_path == db_path
            assert "sqlite:///" in runner.storage_url
        finally:
            os.unlink(db_path)

    def test_runner_init_default_db(self):
        """Test runner initialization with default database path."""
        runner = OptunaRunner()
        assert runner.db_path.endswith("optimization_studies.db")

    def test_list_studies_empty(self):
        """Test listing studies when database is empty."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            runner = OptunaRunner(db_path=db_path)
            studies = runner.list_studies()
            assert studies == []
        finally:
            os.unlink(db_path)

    def test_optimize_invalid_strategy(self):
        """Test that optimize raises ValueError for invalid strategy."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            runner = OptunaRunner(db_path=db_path)
            with pytest.raises(ValueError, match="Unknown strategy"):
                runner.optimize(
                    strategy="nonexistent",
                    n_trials=1,
                    start="2024-01-01",
                    end="2024-06-30",
                )
        finally:
            os.unlink(db_path)

    def test_optimize_invalid_sampler(self):
        """Test that optimize raises ValueError for invalid sampler."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            runner = OptunaRunner(db_path=db_path)
            with pytest.raises(ValueError, match="Unknown sampler"):
                runner.optimize(
                    strategy="mean_reversion",
                    n_trials=1,
                    sampler="invalid",
                    start="2024-01-01",
                    end="2024-06-30",
                )
        finally:
            os.unlink(db_path)

    @patch("trading_bot_v2.optimization.optuna_runner.OptimizationAdapter")
    def test_optimize_mock(self, mock_adapter_class):
        """Test optimization with mocked backtest."""
        mock_adapter = MagicMock()
        mock_adapter_class.return_value = mock_adapter

        # Mock the backtest result
        from trading_bot_v2.backtesting.performance import BacktestResult

        mock_result = BacktestResult(
            symbol="TEST-USDC",
            start="2024-01-01",
            end="2024-06-30",
            initial_capital=10000.0,
            final_equity=11000.0,
            sharpe_ratio=1.2,
            total_return_pct=10.0,
            max_drawdown_pct=5.0,
            closed_trades=25,
        )
        mock_adapter.run_backtest.return_value = mock_result
        mock_adapter.calculate_objective.return_value = 1.2

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            runner = OptunaRunner(db_path=db_path)
            runner.adapter = mock_adapter

            study = runner.optimize(
                strategy="mean_reversion",
                n_trials=5,
                sampler="tpe",
                start="2024-01-01",
                end="2024-06-30",
            )

            # Verify study was created
            assert study is not None
            assert len(study.trials) == 5

            # Verify best params exist
            assert study.best_trial is not None
            assert study.best_value is not None

        finally:
            # Force cleanup before deleting
            import gc
            gc.collect()
            try:
                os.unlink(db_path)
            except PermissionError:
                pass  # Windows may keep file locked


# ---------------------------------------------------------------------------
# CLI Tests
# ---------------------------------------------------------------------------


class TestCLI:
    """Tests for the CLI interface."""

    def test_cli_help(self):
        """Test that CLI shows help."""
        from trading_bot_v2.optimization.run_optimize import parse_args

        with pytest.raises(SystemExit) as exc_info:
            import sys
            sys.argv = ["run_optimize", "--help"]
            parse_args()

        assert exc_info.value.code == 0

    def test_cli_parse_args(self):
        """Test CLI argument parsing."""
        import sys

        sys.argv = [
            "run_optimize",
            "--strategy", "mean_reversion",
            "--trials", "50",
            "--sampler", "random",
            "--objective", "sortino_ratio",
            "--walk-forward",
            "--start", "2024-01-01",
            "--end", "2024-12-31",
            "--symbol", "BTC-USDC",
            "--capital", "50000",
        ]

        from trading_bot_v2.optimization.run_optimize import parse_args

        args = parse_args()

        assert args.strategy == "mean_reversion"
        assert args.trials == 50
        assert args.sampler == "random"
        assert args.objective == "sortino_ratio"
        assert args.walk_forward is True
        assert args.start == "2024-01-01"
        assert args.end == "2024-12-31"
        assert args.symbol == "BTC-USDC"
        assert args.capital == 50000.0


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------


class TestIntegration:
    """Integration tests (require Optuna and may be slow)."""

    @pytest.mark.slow
    def test_end_to_end_minimal(self):
        """Test minimal end-to-end optimization."""
        from trading_bot_v2.optimization.optuna_runner import OptunaRunner
        import gc
        import shutil

        tmpdir = tempfile.mkdtemp()
        try:
            db_path = os.path.join(tmpdir, "test.db")
            runner = OptunaRunner(db_path=db_path)

            # Run with minimal trials
            study = runner.optimize(
                strategy="mean_reversion",
                n_trials=3,
                sampler="random",
                start="2024-01-01",
                end="2024-03-31",
                walk_forward=False,
            )

            # Verify study completed
            assert study is not None
            completed = [t for t in study.trials if t.state.name == "COMPLETE"]
            assert len(completed) > 0

            # Get best params
            params = runner.get_best_params("mean_reversion")
            assert isinstance(params, dict)

            # Get results DataFrame
            import pandas as pd
            df = runner.get_study_results("mean_reversion")
            assert isinstance(df, pd.DataFrame)
            assert not df.empty

        finally:
            # Force cleanup of SQLite connections
            del runner
            gc.collect()
            import time
            time.sleep(0.5)
            try:
                shutil.rmtree(tmpdir, ignore_errors=True)
            except Exception:
                pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
