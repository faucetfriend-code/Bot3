"""
Tests for the P5 validation statistics, trial registry, and sweep
integration.

Reference values were computed independently with numpy/scipy
(population moments, non-excess kurtosis) for the series
REF_RETURNS below.
"""

import math

import pytest

from trading_bot_v2.validation.statistics import (
    DSRResult,
    PSRResult,
    closed_trade_returns,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    min_track_record_length,
    probabilistic_sharpe_ratio,
    sharpe_ratio,
)

# Fixed reference series (10 per-trade returns)
REF_RETURNS = [0.01, 0.02, -0.005, 0.015, -0.01, 0.02, 0.005, -0.015, 0.01, 0.03]

# Independently computed with numpy + scipy.stats (ddof=0, bias=True):
REF_SR = 0.5865884600854131
REF_PSR = 0.9445070690701923
REF_MINTRL = 10.586092140971193


class TestSharpeRatio:
    def test_reference_value(self):
        assert sharpe_ratio(REF_RETURNS) == pytest.approx(REF_SR, abs=1e-9)

    def test_annualization_optional(self):
        sr = sharpe_ratio(REF_RETURNS)
        sr_ann = sharpe_ratio(REF_RETURNS, periods_per_year=252)
        assert sr_ann == pytest.approx(sr * math.sqrt(252), abs=1e-9)

    def test_zero_variance_returns_zero(self):
        assert sharpe_ratio([0.01] * 10) == 0.0

    def test_too_short_returns_zero(self):
        assert sharpe_ratio([0.01]) == 0.0
        assert sharpe_ratio([]) == 0.0

    def test_negative_mean_gives_negative_sr(self):
        assert sharpe_ratio([-0.01, -0.02, 0.005, -0.015]) < 0


class TestProbabilisticSharpeRatio:
    def test_reference_value(self):
        res = probabilistic_sharpe_ratio(REF_RETURNS)
        assert isinstance(res, PSRResult)
        assert res.value == pytest.approx(REF_PSR, abs=1e-6)
        assert res.reason is None
        assert res.n == 10
        assert res.sr == pytest.approx(REF_SR, abs=1e-9)

    def test_zero_benchmark_zero_sr_gives_half(self):
        # Symmetric series with mean 0 -> sr = 0 -> PSR(0) = Phi(0) = 0.5
        res = probabilistic_sharpe_ratio([0.01, -0.01, 0.02, -0.02])
        assert res.value == pytest.approx(0.5, abs=1e-9)

    def test_higher_benchmark_lowers_psr(self):
        low = probabilistic_sharpe_ratio(REF_RETURNS, benchmark_sr=0.0)
        high = probabilistic_sharpe_ratio(REF_RETURNS, benchmark_sr=0.3)
        assert high.value < low.value

    def test_guard_short_series(self):
        res = probabilistic_sharpe_ratio([0.01, 0.02])
        assert res.value is None
        assert "insufficient observations" in res.reason

    def test_guard_zero_variance(self):
        res = probabilistic_sharpe_ratio([0.01] * 5)
        assert res.value is None
        assert "zero variance" in res.reason


class TestExpectedMaxSharpe:
    def test_single_trial_is_zero(self):
        assert expected_max_sharpe(1, 1.0) == 0.0
        assert expected_max_sharpe(0, 1.0) == 0.0

    def test_zero_variance_is_zero(self):
        assert expected_max_sharpe(100, 0.0) == 0.0

    def test_monotonic_in_n_trials(self):
        values = [expected_max_sharpe(n, 1.0) for n in (2, 5, 10, 50, 100, 1000)]
        assert all(b > a for a, b in zip(values, values[1:]))

    def test_scales_with_sqrt_variance(self):
        full = expected_max_sharpe(100, 1.0)
        quarter = expected_max_sharpe(100, 0.25)
        assert quarter == pytest.approx(full * 0.5, abs=1e-9)

    def test_reference_value(self):
        # sqrt(1) * ((1-gamma)*z(0.9) + gamma*z(1 - 1/(10*e)))
        assert expected_max_sharpe(10, 1.0) == pytest.approx(
            1.57459830134575, abs=1e-6
        )


class TestDeflatedSharpeRatio:
    def test_n_trials_one_equals_psr(self):
        dsr = deflated_sharpe_ratio(REF_RETURNS, n_trials=1)
        assert isinstance(dsr, DSRResult)
        assert dsr.benchmark_sr == 0.0
        assert dsr.value == pytest.approx(REF_PSR, abs=1e-6)

    def test_deflation_lowers_value(self):
        base = deflated_sharpe_ratio(REF_RETURNS, n_trials=1)
        deflated = deflated_sharpe_ratio(REF_RETURNS, n_trials=50)
        assert deflated.value < base.value
        assert deflated.benchmark_sr > 0

    def test_fallback_variance_documented(self):
        dsr = deflated_sharpe_ratio(REF_RETURNS, n_trials=10)
        assert dsr.var_fallback is True
        # 1/(n-1) heuristic with n=10 observations
        assert dsr.var_sharpe == pytest.approx(1.0 / 9.0, abs=1e-12)

    def test_explicit_variance_used(self):
        dsr = deflated_sharpe_ratio(
            REF_RETURNS, n_trials=10, var_sharpe_across_trials=0.04
        )
        assert dsr.var_fallback is False
        assert dsr.var_sharpe == 0.04
        assert dsr.benchmark_sr == pytest.approx(
            expected_max_sharpe(10, 0.04), abs=1e-12
        )

    def test_pass_flag(self):
        # Strongly positive series, no deflation -> PSR ~ 1 -> pass
        strong = [0.01] * 40 + [-0.001] * 5
        dsr = deflated_sharpe_ratio(strong, n_trials=1)
        assert dsr.passed is True
        weak = deflated_sharpe_ratio(REF_RETURNS, n_trials=100)
        assert weak.passed is False

    def test_guard_short_series(self):
        dsr = deflated_sharpe_ratio([0.01], n_trials=5)
        assert dsr.value is None
        assert dsr.reason is not None


class TestMinTrackRecordLength:
    def test_reference_value(self):
        assert min_track_record_length(REF_RETURNS, 0.0) == pytest.approx(
            REF_MINTRL, abs=1e-6
        )

    def test_sr_below_benchmark_returns_none(self):
        assert min_track_record_length(REF_RETURNS, 5.0) is None

    def test_short_series_returns_none(self):
        assert min_track_record_length([0.01, 0.02], 0.0) is None

    def test_closer_benchmark_needs_longer_track(self):
        far = min_track_record_length(REF_RETURNS, 0.0)
        close = min_track_record_length(REF_RETURNS, REF_SR * 0.8)
        assert close > far


class TestClosedTradeReturns:
    def test_filters_opening_fills(self):
        log = [
            {"pnl": 0, "fee": 1.0},
            {"pnl": 50.0},
            {"pnl": -25.0},
            {"pnl": 0},
        ]
        assert closed_trade_returns(log, 10000.0) == [0.005, -0.0025]

    def test_zero_capital_returns_empty(self):
        assert closed_trade_returns([{"pnl": 5.0}], 0.0) == []


# ---------------------------------------------------------------------------
# Trial registry (database round-trip + runner recording)
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    """DatabaseManager wired to a temporary SQLite file."""
    import trading_bot_v2.database as db_mod

    db_file = str(tmp_path / "trial_registry_test.db")
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


class TestTrialRegistry:
    def test_round_trip(self, tmp_db):
        row_id = tmp_db.save_trial_registry_entry(
            strategy="mean_reversion",
            n_trials=25,
            scope="optuna_study",
            regime="ranging_calm",
            sr_variance=0.12,
            source="mean_reversion_20260720_000000",
        )
        assert row_id is not None

        rows = tmp_db.get_trial_registry(strategy="mean_reversion")
        assert len(rows) == 1
        row = rows[0]
        assert row["strategy"] == "mean_reversion"
        assert row["regime"] == "ranging_calm"
        assert row["scope"] == "optuna_study"
        assert row["n_trials"] == 25
        assert row["sr_variance"] == pytest.approx(0.12)
        assert row["source"] == "mean_reversion_20260720_000000"

    def test_get_total_trials_aggregates(self, tmp_db):
        tmp_db.save_trial_registry_entry(
            strategy="mean_reversion", n_trials=20, scope="optuna_study"
        )
        tmp_db.save_trial_registry_entry(
            strategy="mean_reversion",
            n_trials=15,
            scope="optuna_study",
            regime="ranging_calm",
        )
        tmp_db.save_trial_registry_entry(
            strategy="vwap_scalping", n_trials=99, scope="optuna_study"
        )
        assert tmp_db.get_total_trials("mean_reversion") == 35
        # Regime filter includes regime-agnostic (NULL) rows
        assert tmp_db.get_total_trials("mean_reversion", "ranging_calm") == 35
        assert tmp_db.get_total_trials("vwap_scalping") == 99
        assert tmp_db.get_total_trials("never_optimized") == 0

    def test_sr_variance_weighted_mean(self, tmp_db):
        tmp_db.save_trial_registry_entry(
            strategy="mean_reversion", n_trials=10, scope="optuna_study",
            sr_variance=0.1,
        )
        tmp_db.save_trial_registry_entry(
            strategy="mean_reversion", n_trials=30, scope="optuna_study",
            sr_variance=0.2,
        )
        # NULL-variance rows are ignored
        tmp_db.save_trial_registry_entry(
            strategy="mean_reversion", n_trials=100, scope="sweep",
        )
        expected = (10 * 0.1 + 30 * 0.2) / 40
        assert tmp_db.get_trial_sr_variance("mean_reversion") == pytest.approx(
            expected
        )
        assert tmp_db.get_trial_sr_variance("never_optimized") is None

    def test_runner_records_trials(self, tmp_db):
        """OptunaRunner._record_trial_registry writes completed+pruned."""
        optuna = pytest.importorskip("optuna")
        from trading_bot_v2.optimization.optuna_runner import OptunaRunner

        state = optuna.trial.TrialState

        class StubTrial:
            def __init__(self, trial_state, value=None):
                self.state = trial_state
                self.value = value

        class StubStudy:
            study_name = "mean_reversion_stub_20260720"
            trials = [
                StubTrial(state.COMPLETE, 1.0),
                StubTrial(state.COMPLETE, 2.0),
                StubTrial(state.COMPLETE, 3.0),
                StubTrial(state.PRUNED),
                StubTrial(state.FAIL),  # not counted
                StubTrial(state.COMPLETE, float("-inf")),  # counted, var-excluded
            ]

        runner = OptunaRunner.__new__(OptunaRunner)  # skip heavy __init__
        runner._record_trial_registry(
            StubStudy(), "mean_reversion", None, "sharpe_ratio"
        )

        rows = tmp_db.get_trial_registry(strategy="mean_reversion")
        assert len(rows) == 1
        # 4 COMPLETE + 1 PRUNED = 5 (FAIL excluded)
        assert rows[0]["n_trials"] == 5
        # Sample variance of [1, 2, 3] = 1.0 (-inf excluded)
        assert rows[0]["sr_variance"] == pytest.approx(1.0)
        assert rows[0]["scope"] == "optuna_study"
        assert rows[0]["source"] == "mean_reversion_stub_20260720"

    def test_runner_skips_empty_study(self, tmp_db):
        optuna = pytest.importorskip("optuna")
        from trading_bot_v2.optimization.optuna_runner import OptunaRunner

        class StubStudy:
            study_name = "empty"
            trials = []

        runner = OptunaRunner.__new__(OptunaRunner)
        runner._record_trial_registry(StubStudy(), "mean_reversion", None, "sharpe_ratio")
        assert tmp_db.get_trial_registry(strategy="mean_reversion") == []


# ---------------------------------------------------------------------------
# Sweep integration smoke (PSR column + DSR verdict)
# ---------------------------------------------------------------------------


class TestSweepValidationIntegration:
    def _result(self, name="MeanReversion"):
        from trading_bot_v2.backtesting.run_strategy_sweep import StrategyResult

        r = StrategyResult(name)
        r.ok = True
        r.closed_trades = 45
        r.trade_returns = [0.01] * 30 + [-0.005] * 15
        r.profit_factor = 4.0
        return r

    def test_apply_validation_stats_sets_psr_and_dsr(self, tmp_db):
        from trading_bot_v2.backtesting.run_strategy_sweep import (
            apply_validation_stats,
        )

        tmp_db.save_trial_registry_entry(
            strategy="mean_reversion", n_trials=40, scope="optuna_study",
            sr_variance=0.05,
        )
        r = self._result()
        apply_validation_stats([r])
        assert r.psr is not None and 0.0 < r.psr <= 1.0
        assert r.n_trials == 40
        assert r.dsr is not None and r.dsr < r.psr

    def test_apply_validation_stats_without_registry(self, tmp_db):
        from trading_bot_v2.backtesting.run_strategy_sweep import (
            apply_validation_stats,
        )

        r = self._result()
        apply_validation_stats([r])
        assert r.psr is not None
        assert r.n_trials == 0
        assert r.dsr is None

    def test_row_renders_psr_column(self):
        from trading_bot_v2.backtesting.run_strategy_sweep import _row

        r = self._result()
        r.psr = 0.973
        row = _row(r)
        assert "0.973" in row

        r.psr = None
        assert "--" in _row(r)

    def test_verdict_flags(self):
        from trading_bot_v2.backtesting.run_strategy_sweep import _verdict

        r = self._result()
        r.n_trials = 0
        assert "UNVALIDATED" in _verdict(r)

        r.n_trials = 40
        r.dsr = 0.99
        assert "DSR-PASS" in _verdict(r)

        r.dsr = 0.42
        assert "DSR-FAIL" in _verdict(r)
