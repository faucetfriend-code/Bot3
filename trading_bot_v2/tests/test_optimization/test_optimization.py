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
from unittest.mock import MagicMock, patch

import pytest

# Skip all tests if optuna is not installed
pytest.importorskip("optuna")

from trading_bot_v2.config import StrategyType
from trading_bot_v2.optimization.search_spaces import (
    SEARCH_SPACE_BUILDERS,
    get_search_space,
    list_strategies,
    suggest_params,
    get_param_type,
    check_param_feasibility,
    validate_params,
    InfeasibleParamsError,
    MIN_RRR_CONSTRAINTS,
    RRR_FEASIBILITY_MARGIN,
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
        """list_strategies covers every tunable strategy, exactly once.

        Asserts the invariant rather than a hand-maintained count: the
        list must match the search-space registry exactly (a strategy
        with a space but missing from the list would silently never be
        tuned), carry no duplicates, and name only real strategies.
        """
        strategies = list_strategies()
        assert len(strategies) == len(set(strategies))
        # No drift between the list and the registry get_search_space uses.
        assert set(strategies) == set(SEARCH_SPACE_BUILDERS)
        # Every listed name is a real strategy, not a typo.
        valid = {s.value for s in StrategyType}
        assert set(strategies) <= valid
        # The strategies the campaign runs must stay tunable.
        for expected in (
            "mean_reversion",
            "ma_crossover",
            "grid_trading",
            "liquidation_capture",
            "vwap_scalping",
            "vwap_pullback",
            "funding_arb",
            "momentum_scalping",
            "orderbook_imbalance",
        ):
            assert expected in strategies

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
        """Test specific parameter ranges for MA Crossover.

        The fast < slow invariant is enforced by the ordered-pair
        coupling in suggest_params, not by declaring disjoint ranges, so
        the declared bands are allowed to overlap. What must hold is that
        each band is well-formed and that slow_ma_period stays inside the
        engine's default 60-candle history budget (a slow MA above ~59
        makes _validate_data reject every bar).
        """
        space = get_search_space("ma_crossover")

        fast_low, fast_high = space["fast_ma_period"]
        slow_low, slow_high = space["slow_ma_period"]

        assert fast_low < fast_high
        assert slow_low < slow_high
        # A feasible region must exist at all.
        assert fast_low < slow_high
        # History budget: required_history() = slow_ma_period + 1 <= 60.
        assert slow_high + 1 <= 60

        # The funnel-confirmed binding constraint must be searchable
        # below 1.0 (i.e. the volume gate can be weakened).
        vol_low, vol_high = space["volume_threshold"]
        assert vol_low < 1.0 < vol_high

        # The entry window is tunable, and min_entry_bars stays pinned.
        assert "max_entry_bars" in space
        assert "min_entry_bars" not in space
        assert "min_confidence" in space

    def test_ma_crossover_pairs_are_ordered(self):
        """Sampled MA crossover params always satisfy their orderings."""
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)

        def objective(trial):
            params = suggest_params(trial, "ma_crossover")
            assert params["fast_ma_period"] < params["slow_ma_period"]
            assert params["pullback_range_min"] < params["pullback_range_max"]
            assert params["slow_ma_period"] + 1 <= 60
            assert check_param_feasibility("ma_crossover", params) == []
            return 0.0

        study = optuna.create_study()
        study.optimize(objective, n_trials=40)
        assert len(study.trials) == 40

    def test_ma_crossover_inverted_pairs_are_infeasible(self):
        """An inverted pair is reported as infeasible, not merely bad."""
        reasons = check_param_feasibility(
            "ma_crossover",
            {"fast_ma_period": 30, "slow_ma_period": 20},
        )
        assert any("fast_ma_period" in r for r in reasons)

        reasons = check_param_feasibility(
            "ma_crossover",
            {"pullback_range_min": 0.05, "pullback_range_max": 0.02},
        )
        assert any("pullback_range_min" in r for r in reasons)

    def test_ma_crossover_history_budget_is_enforced(self, monkeypatch):
        """A slow MA beyond the engine's history budget is infeasible."""
        monkeypatch.setenv("BACKTEST_HISTORY_LOOKBACK", "30")
        reasons = check_param_feasibility("ma_crossover", {"slow_ma_period": 50})
        assert any("BACKTEST_HISTORY_LOOKBACK" in r for r in reasons)

        monkeypatch.setenv("BACKTEST_HISTORY_LOOKBACK", "60")
        assert check_param_feasibility("ma_crossover", {"slow_ma_period": 50}) == []


# ---------------------------------------------------------------------------
# Feasibility Constraint Tests
# ---------------------------------------------------------------------------


class TestParamFeasibility:
    """
    Strategies whose stop and target are multiples of the SAME ATR have a
    constant RRR. Combinations below the strategy's rrr_meets_minimum gate
    produce zero trades no matter what the data does, so the optimizer must
    not spend trials on them.
    """

    def test_feasible_momentum_params_pass(self):
        params = {"atr_stop_mult": 2.0, "atr_target_mult": 3.0}  # RRR 1.5
        assert check_param_feasibility("momentum_scalping", params) == []
        validate_params("momentum_scalping", params)  # no raise

    def test_infeasible_momentum_params_are_reported(self):
        params = {"atr_stop_mult": 2.5, "atr_target_mult": 3.0}  # RRR 1.2
        reasons = check_param_feasibility("momentum_scalping", params)
        assert len(reasons) == 1
        assert "rrr_meets_minimum" in reasons[0].lower()

    def test_validate_params_raises_with_reason(self):
        params = {"atr_stop_mult": 2.5, "atr_target_mult": 3.0}
        with pytest.raises(InfeasibleParamsError, match="momentum_scalping"):
            validate_params("momentum_scalping", params)

    def test_zero_stop_is_infeasible(self):
        params = {"atr_stop_mult": 0.0, "atr_target_mult": 3.0}
        assert check_param_feasibility("momentum_scalping", params)

    def test_unconstrained_strategy_is_always_feasible(self):
        params = {"atr_stop_mult": 2.5, "atr_target_mult": 3.0}
        assert check_param_feasibility("mean_reversion", params) == []

    def test_suggest_params_never_yields_infeasible_rrr(self):
        """Every suggestion for a constrained strategy must clear its gate."""
        import optuna

        optuna.logging.set_verbosity(optuna.logging.WARNING)
        for strategy, min_rrr in MIN_RRR_CONSTRAINTS.items():
            study = optuna.create_study(direction="maximize")
            for _ in range(40):
                params = suggest_params(study.ask(), strategy)
                rrr = params["atr_target_mult"] / params["atr_stop_mult"]
                assert rrr >= min_rrr, (
                    f"{strategy} suggested an unbacktestable RRR {rrr:.3f} "
                    f"(min {min_rrr})"
                )

    def test_declared_space_can_satisfy_the_constraint(self):
        """The declared box must not be empty once the coupling is applied."""
        for strategy, min_rrr in MIN_RRR_CONSTRAINTS.items():
            space = get_search_space(strategy)
            stop_low, stop_high = space["atr_stop_mult"]
            _, target_high = space["atr_target_mult"]
            assert stop_high * min_rrr * RRR_FEASIBILITY_MARGIN <= target_high, (
                f"{strategy}: atr_target_mult cap {target_high} cannot reach "
                f"RRR {min_rrr} at atr_stop_mult={stop_high}"
            )
            assert stop_low > 0


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
# Chunked cross-symbol sweep
# ---------------------------------------------------------------------------


class TestChunkedSweep:
    """The chunked sweep must evaluate every (symbol, window) chunk and
    keep the per-symbol picture, because cross-symbol consistency is a
    real gate check that aggregation would hide.
    """

    @staticmethod
    def _result(symbol, trades, sharpe, ret):
        from trading_bot_v2.backtesting.performance import BacktestResult

        return BacktestResult(
            symbol=symbol,
            start="2024-01-01",
            end="2024-02-01",
            initial_capital=10000.0,
            final_equity=10000.0 + ret * 100,
            sharpe_ratio=sharpe,
            total_return_pct=ret,
            max_drawdown_pct=5.0,
            closed_trades=trades,
        )

    def _run(self, per_symbol_spec, n_trials=2):
        """Run a sweep with a stubbed adapter and stubbed windows."""
        windows = [("2024-01-01", "2024-02-01"), ("2024-02-01", "2024-03-01")]
        symbols = list(per_symbol_spec)

        mock_adapter = MagicMock()

        def run_backtest(**kwargs):
            trades, sharpe, ret = per_symbol_spec[kwargs["symbol"]]
            return self._result(kwargs["symbol"], trades, sharpe, ret)

        mock_adapter.run_backtest.side_effect = run_backtest
        mock_adapter.calculate_objective.side_effect = (
            lambda result, objective: result.sharpe_ratio
        )

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            runner = OptunaRunner(db_path=db_path)
            runner.adapter = mock_adapter
            with (
                patch(
                    "trading_bot_v2.validation.runner.resolve_chunk_windows",
                    return_value={
                        "symbols": symbols,
                        "windows": windows,
                        "data_start": "2024-01-01",
                        "data_end": "2024-03-01",
                    },
                ),
                patch.object(runner, "_record_trial_registry"),
            ):
                study = runner.optimize_chunked(
                    strategy="ma_crossover",
                    symbols=symbols,
                    n_trials=n_trials,
                    sampler="random",
                )
            return study, mock_adapter, len(windows)
        finally:
            import gc

            gc.collect()
            try:
                os.unlink(db_path)
            except PermissionError:
                pass

    def test_every_symbol_window_pair_is_backtested(self):
        spec = {
            "BTC-USDC": (12, 0.8, 6.0),
            "ETH-USDC": (9, 0.4, 3.0),
            "SUI-USDC": (15, 1.6, 12.0),
        }
        study, adapter, n_windows = self._run(spec, n_trials=2)
        assert adapter.run_backtest.call_count == 2 * len(spec) * n_windows

    def test_per_symbol_breakdown_is_recorded(self):
        spec = {
            "BTC-USDC": (12, 0.8, 6.0),
            "ETH-USDC": (9, -0.5, -3.0),
            "SUI-USDC": (15, 1.6, 12.0),
        }
        study, _, n_windows = self._run(spec, n_trials=1)
        attrs = study.trials[0].user_attrs
        per_symbol = attrs["per_symbol"]

        assert set(per_symbol) == set(spec)
        assert per_symbol["BTC-USDC"]["trades"] == 12 * n_windows
        assert per_symbol["SUI-USDC"]["objective"] == pytest.approx(1.6)
        # The losing symbol must stay visible, not be averaged away.
        assert per_symbol["ETH-USDC"]["objective"] < 0
        assert attrs["traded_symbols"] == 3
        assert attrs["profitable_symbols"] == 2
        assert attrs["total_trades"] == (12 + 9 + 15) * n_windows

    def test_a_traded_sweep_scores_above_the_zero_trade_bands(self):
        from trading_bot_v2.diagnostics.outcomes import TRADED_SCORE_FLOOR

        spec = {"BTC-USDC": (10, 0.9, 5.0), "SUI-USDC": (10, 1.1, 7.0)}
        study, _, _ = self._run(spec, n_trials=1)
        assert study.trials[0].value >= TRADED_SCORE_FLOOR
        assert study.trials[0].user_attrs["outcome"] == "traded"

    def test_a_zero_trade_sweep_names_the_binding_stage(self):
        """A trial that lands 0 trades must say which gate killed it."""
        from trading_bot_v2.diagnostics.outcomes import TRADED_SCORE_FLOOR

        spec = {"BTC-USDC": (0, 0.0, 0.0), "SUI-USDC": (0, 0.0, 0.0)}
        study, _, _ = self._run(spec, n_trials=1)
        attrs = study.trials[0].user_attrs
        assert attrs["outcome"] != "traded"
        assert "binding_stage" in attrs
        # Below the TRADED floor by construction: a reserved band.
        assert study.trials[0].value < TRADED_SCORE_FLOOR

    def test_unknown_strategy_is_rejected(self):
        runner_cls = OptunaRunner
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            runner = runner_cls(db_path=db_path)
            with pytest.raises(ValueError, match="Unknown strategy"):
                runner.optimize_chunked(
                    strategy="nope", symbols=["BTC-USDC"], n_trials=1
                )
        finally:
            import gc

            gc.collect()
            try:
                os.unlink(db_path)
            except PermissionError:
                pass


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
            "--strategy",
            "mean_reversion",
            "--trials",
            "50",
            "--sampler",
            "random",
            "--objective",
            "sortino_ratio",
            "--walk-forward",
            "--start",
            "2024-01-01",
            "--end",
            "2024-12-31",
            "--symbol",
            "BTC-USDC",
            "--capital",
            "50000",
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

    def test_data_dir_defaults_to_none(self):
        """Unset means "use config", which is the shipped behaviour."""
        import sys

        sys.argv = ["run_optimize", "--strategy", "mean_reversion"]

        from trading_bot_v2.optimization.run_optimize import parse_args

        assert parse_args().data_dir is None

    def test_data_dir_is_parsed(self):
        """`.env` pins BACKTEST_DATA_DIR to a RELATIVE path and config.py
        loads it with override=True, so a shell export cannot redirect the
        candle store. An explicit flag is the only reliable route, and
        run_backtest / validation.runner already have one.
        """
        import sys

        sys.argv = [
            "run_optimize",
            "--strategy",
            "vwap_scalping",
            "--data-dir",
            "/abs/store",
        ]

        from trading_bot_v2.optimization.run_optimize import parse_args

        assert parse_args().data_dir == "/abs/store"

    def test_main_pins_the_data_dir_on_config(self, monkeypatch):
        """The flag has to reach config.backtest_data_dir: both the window
        cutter and the engine's loader read it from there."""
        import sys

        from trading_bot_v2.config import config
        from trading_bot_v2.optimization import run_optimize

        original = config.backtest_data_dir
        seen = {}

        def _fake_single(args, strategy):
            seen["dir"] = config.backtest_data_dir
            return object()

        monkeypatch.setattr(run_optimize, "run_single_strategy", _fake_single)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_optimize",
                "--strategy",
                "vwap_scalping",
                "--data-dir",
                "/abs/store",
            ],
        )
        try:
            assert run_optimize.main() == 0
        finally:
            config.backtest_data_dir = original

        assert seen["dir"] == "/abs/store"


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
