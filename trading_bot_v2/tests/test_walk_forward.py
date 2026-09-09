"""
Tests for walk-forward analysis (P5): window construction, legacy
mode PSR reporting, optimize mode (mocked), and one small real
integration run with trials=2.
"""

from unittest.mock import MagicMock

import pytest

from trading_bot_v2.backtesting.performance import BacktestResult
from trading_bot_v2.backtesting.walk_forward import (
    WalkForwardAnalyzer,
    WalkForwardReport,
)


def _make_result(pnls, symbol="SUI-USDC", start="2024-04-01", end="2024-04-30"):
    """Build a BacktestResult whose trade_log closes with the given pnls."""
    trade_log = [{"pnl": 0.0}]  # opening fill (ignored)
    trade_log += [{"pnl": p} for p in pnls]
    return BacktestResult(
        symbol=symbol,
        start=start,
        end=end,
        initial_capital=10000.0,
        final_equity=10000.0 + sum(pnls),
        trade_log=trade_log,
        closed_trades=len(pnls),
    )


def _mock_engine(train_months=3, test_months=1):
    engine = MagicMock()
    engine.cfg.backtest_walk_forward_train_months = train_months
    engine.cfg.backtest_walk_forward_test_months = test_months
    return engine


class TestWindows:
    def test_window_count(self):
        wf = WalkForwardAnalyzer(_mock_engine())
        windows = wf._build_windows("2024-01-01", "2025-01-01", 3, 1)
        assert len(windows) >= 9

    def test_train_precedes_test(self):
        wf = WalkForwardAnalyzer(_mock_engine())
        for train_start, train_end, test_start, test_end in wf._build_windows(
            "2024-01-01", "2025-01-01", 3, 1
        ):
            assert train_start < train_end < test_start <= test_end


class TestLegacyMode:
    def test_returns_results_and_prints_psr(self, capsys):
        engine = _mock_engine(train_months=3, test_months=1)
        engine.run.return_value = _make_result([50.0, -20.0, 30.0, 10.0])

        wf = WalkForwardAnalyzer(engine)
        results = wf.run(start="2024-01-01", end="2024-07-01", symbol="SUI-USDC")

        assert isinstance(results, list)
        assert all(isinstance(r, BacktestResult) for r in results)
        out = capsys.readouterr().out
        assert "WALK-FORWARD SUMMARY" in out
        assert "OOS PSR" in out

    def test_optimize_requires_strategy(self):
        wf = WalkForwardAnalyzer(_mock_engine())
        with pytest.raises(ValueError, match="requires a strategy"):
            wf.run(
                start="2024-01-01",
                end="2024-07-01",
                symbol="SUI-USDC",
                optimize=True,
            )


class TestOptimizeModeMocked:
    def _stub_runner_cls(self, best_params, trial_values):
        """Build a stub OptunaRunner class with canned study results."""
        optuna = pytest.importorskip("optuna")
        state = optuna.trial.TrialState

        class StubTrial:
            def __init__(self, v):
                self.state = state.COMPLETE
                self.value = v

        class StubBest:
            params = best_params
            value = max(trial_values)

        class StubStudy:
            study_name = "stub"
            trials = [StubTrial(v) for v in trial_values]
            best_trial = StubBest()

        class StubAdapter:
            def run_backtest(
                self, strategy, params, start, end, symbol, initial_capital
            ):
                assert params == best_params
                return _make_result([40.0, -10.0, 25.0], start=start, end=end)

            def calculate_objective(self, result, objective):
                return 1.23

        class StubRunner:
            def __init__(self, db_path=None):
                self.adapter = StubAdapter()

            def optimize(self, **kwargs):
                return StubStudy()

        return StubRunner

    def test_optimize_mode_report(self, monkeypatch):
        import trading_bot_v2.optimization.optuna_runner as runner_mod

        stub_cls = self._stub_runner_cls(
            best_params={"rsi_oversold": 28.0}, trial_values=[0.5, 1.0, 1.5]
        )
        monkeypatch.setattr(runner_mod, "OptunaRunner", stub_cls)

        wf = WalkForwardAnalyzer(_mock_engine())
        report = wf.run(
            start="2024-01-01",
            end="2024-06-01",
            symbol="SUI-USDC",
            optimize=True,
            strategy="mean_reversion",
            n_trials=3,
            objective="sharpe",
            train_months=3,
            test_months=1,
        )

        assert isinstance(report, WalkForwardReport)
        assert report.objective == "sharpe_ratio"
        assert len(report.windows) >= 2
        for w in report.windows:
            assert w.params == {"rsi_oversold": 28.0}
            assert w.train_objective == 1.5
            assert w.test_objective == 1.23
            assert w.n_trials == 3
        assert report.n_trials_total == 3 * len(report.windows)
        # 3 closed trades per test window
        assert len(report.oos_returns) == 3 * len(report.windows)
        assert report.psr is not None
        assert report.dsr is not None
        assert report.dsr.n_trials == report.n_trials_total
        # Sharpe objective -> observed trial variance, not the fallback
        assert report.dsr.var_fallback is False
        assert report.profit_factor > 1.0
        assert report.total_return_pct == pytest.approx(sum(report.oos_returns) * 100)


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    """DatabaseManager wired to a temporary SQLite file."""
    import trading_bot_v2.database as db_mod

    db_file = str(tmp_path / "wf_test.db")
    monkeypatch.setenv("DATABASE_PATH", db_file)
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")

    original_backend = db_mod._active_backend
    original_path = db_mod.DATABASE_PATH
    original_pool = db_mod._connection_pool
    db_mod._active_backend = "sqlite"
    db_mod.DATABASE_PATH = db_file
    pool = db_mod.ConnectionPool(max_connections=2)
    db_mod._connection_pool = pool

    db_mod.init_database()

    yield db_mod.DatabaseManager()

    pool.close_all()
    db_mod._active_backend = original_backend
    db_mod.DATABASE_PATH = original_path
    db_mod._connection_pool = original_pool


class TestOptimizeModeIntegration:
    def test_real_two_trial_run(self, tmp_db, tmp_path):
        """One window, 2 real Optuna trials on real SUI-USDC data.

        Exercises the full path: train-window study -> best params ->
        test-window backtest -> aggregate report -> registry rows.
        """
        pytest.importorskip("optuna")
        from trading_bot_v2.backtesting.engine import BacktestEngine

        engine = BacktestEngine()
        wf = WalkForwardAnalyzer(engine)
        report = wf.run(
            start="2024-01-01",
            end="2024-03-01",
            symbol="SUI-USDC",
            optimize=True,
            strategy="mean_reversion",
            n_trials=2,
            objective="sharpe",
            train_months=1,
            test_months=1,
            optuna_db_path=str(tmp_path / "wf_studies.db"),
        )

        assert isinstance(report, WalkForwardReport)
        assert len(report.windows) == 1
        window = report.windows[0]
        assert window.n_trials == 2
        assert window.result is not None
        assert report.n_trials_total == 2

        # The registry recorded the study's trials (task 2 wiring)
        assert tmp_db.get_total_trials("mean_reversion") == 2
        rows = tmp_db.get_trial_registry(strategy="mean_reversion")
        assert rows[0]["scope"] == "optuna_study"
