"""
Tests for banded trial scoring, the -inf removal, the scoring-schema
guard, and the trial-registry DSR fix in OptunaRunner.

Everything here runs off hand-built funnel states and stub adapters -
no candle data is required.
"""

import pytest

from trading_bot_v2.backtesting.performance import BacktestResult
from trading_bot_v2.diagnostics.funnel import (
    REASON_VALIDITY,
    STAGE_BARS_EVALUATED,
    STAGE_CLOSED_TRADES,
    STAGE_ORDERS_PLACED,
    STAGE_RAW_SIGNALS,
    STAGE_STRATEGY_INVOKED,
    STAGE_VALIDITY_DROPPED,
    SignalFunnel,
)
from trading_bot_v2.diagnostics.outcomes import TrialOutcome

optuna = pytest.importorskip("optuna")

from trading_bot_v2.optimization.optuna_runner import (  # noqa: E402
    SCORING_SCHEMA,
    OptunaRunner,
    _assert_scoring_schema,
    _FailRateGuard,
    _funnel_for_result,
    _is_infeasible_trial,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def zero_trade_funnel(n_signals: int = 136) -> SignalFunnel:
    """Funnel where every emitted signal died at the validity gate.

    Args:
        n_signals: Signals emitted before being discarded.

    Returns:
        A populated SignalFunnel.
    """
    funnel = SignalFunnel(label="momentum_scalping")
    funnel.count(STAGE_BARS_EVALUATED, 52041)
    funnel.count_strategy("momentum_scalping", STAGE_STRATEGY_INVOKED, 18220)
    funnel.count_strategy("momentum_scalping", STAGE_RAW_SIGNALS, n_signals)
    funnel.count_strategy(
        "momentum_scalping", STAGE_VALIDITY_DROPPED, n_signals
    )
    funnel.reject(
        REASON_VALIDITY["rrr_meets_minimum"], n_signals, "momentum_scalping"
    )
    funnel.set_stage(STAGE_ORDERS_PLACED, 0)
    funnel.set_stage(STAGE_CLOSED_TRADES, 0)
    return funnel


def traded_funnel() -> SignalFunnel:
    """Funnel for a run that produced closed trades.

    Returns:
        A populated SignalFunnel.
    """
    funnel = SignalFunnel(label="mean_reversion")
    funnel.count(STAGE_BARS_EVALUATED, 5000)
    funnel.count_strategy("mean_reversion", STAGE_STRATEGY_INVOKED, 4000)
    funnel.count_strategy("mean_reversion", STAGE_RAW_SIGNALS, 80)
    funnel.set_stage(STAGE_ORDERS_PLACED, 60)
    funnel.set_stage(STAGE_CLOSED_TRADES, 55)
    return funnel


class StubTrial:
    """Minimal stand-in for an Optuna trial."""

    def __init__(self, number: int = 0, state=None, value=None, attrs=None):
        self.number = number
        self.state = state
        self.value = value
        self.params = {}
        self.user_attrs = dict(attrs or {})

    def set_user_attr(self, key, value):
        self.user_attrs[key] = value


class StubStudy:
    """Minimal stand-in for an Optuna study."""

    def __init__(self, trials=None, attrs=None, name="stub_study"):
        self.study_name = name
        self.trials = list(trials or [])
        self.user_attrs = dict(attrs or {})
        self.stopped = False

    def set_user_attr(self, key, value):
        self.user_attrs[key] = value

    def stop(self):
        self.stopped = True


# ---------------------------------------------------------------------------
# Banded scoring through the runner
# ---------------------------------------------------------------------------


class TestScoreTrial:
    def _runner(self):
        return OptunaRunner.__new__(OptunaRunner)  # skip heavy __init__

    def test_zero_trade_trial_is_banded_never_zero_never_inf(self):
        trial = StubTrial()
        value = self._runner()._score_trial(
            trial,
            zero_trade_funnel(),
            objective_value=0.0,
            params={"atr_stop_mult": 2.0, "atr_target_mult": 2.4},
        )
        assert -100.0 <= value < -99.0
        assert value != 0.0
        assert value != float("-inf")

    def test_zero_trade_trial_records_the_full_payload(self):
        trial = StubTrial()
        self._runner()._score_trial(
            trial,
            zero_trade_funnel(),
            objective_value=0.0,
            params={"atr_stop_mult": 2.0, "atr_target_mult": 2.4},
        )
        attrs = trial.user_attrs
        assert attrs["outcome"] == "all_discarded_downstream"
        assert attrs["binding_stage"] == STAGE_VALIDITY_DROPPED
        assert attrs["funnel"][STAGE_RAW_SIGNALS] == 136
        assert attrs["top_reasons"][0] == ["validity:rrr_meets_minimum", 136]
        assert "momentum_scalping" in attrs["by_strategy"]
        assert 0.0 < attrs["progress"] < 1.0
        assert "2.40/2.00" in attrs["suggested_fix"]
        assert "rrr_meets_minimum" in attrs["headline"]
        # gate_metrics is a calibration-phase artifact: omitted, not empty.
        assert "gate_metrics" not in attrs

    def test_traded_trial_keeps_its_objective(self):
        trial = StubTrial()
        value = self._runner()._score_trial(
            trial, traded_funnel(), objective_value=1.42, params={}
        )
        assert value == pytest.approx(1.42)
        assert trial.user_attrs["outcome"] == "traded"

    def test_traded_trial_clamps_so_bands_cannot_collide(self):
        trial = StubTrial()
        value = self._runner()._score_trial(
            trial, traded_funnel(), objective_value=-980.0, params={}
        )
        assert value == -50.0

    def test_traded_override_wins_over_an_uninstrumented_funnel(self):
        """Regime mode knows trades exist even when the funnel does not."""
        trial = StubTrial()
        value = self._runner()._score_trial(
            trial,
            SignalFunnel(),
            objective_value=1.23,
            params={},
            traded_override=True,
        )
        assert value == pytest.approx(1.23)
        assert trial.user_attrs["outcome"] == "traded"

    def test_never_invoked_ranks_below_all_discarded(self):
        runner = self._runner()
        blocked = SignalFunnel()
        blocked.count(STAGE_BARS_EVALUATED, 500)
        low = runner._score_trial(StubTrial(), blocked, 0.0, {})
        high = runner._score_trial(StubTrial(), zero_trade_funnel(), 0.0, {})
        assert low < high


class TestFunnelForResult:
    def test_uses_stored_diagnostics(self):
        result = BacktestResult(
            symbol="S", start="a", end="b", initial_capital=1.0,
            final_equity=1.0, diagnostics=zero_trade_funnel().to_dict(),
        )
        assert _funnel_for_result(result).diagnose() == "all_discarded_downstream"

    def test_uninstrumented_run_with_trades_is_traded(self):
        """Stubbed adapters must not be mislabelled as zero-signal."""
        result = BacktestResult(
            symbol="S", start="a", end="b", initial_capital=1.0,
            final_equity=1.0, closed_trades=25,
        )
        assert _funnel_for_result(result).diagnose() == "traded"

    def test_uninstrumented_run_without_trades_is_no_data(self):
        result = BacktestResult(
            symbol="S", start="a", end="b", initial_capital=1.0,
            final_equity=1.0,
        )
        assert _funnel_for_result(result).diagnose() == "no_data"


# ---------------------------------------------------------------------------
# No more -inf
# ---------------------------------------------------------------------------


class TestNoNegativeInfinity:
    def test_source_has_no_negative_infinity_returns(self):
        """Both `return float("-inf")` sites must stay deleted."""
        from pathlib import Path

        import trading_bot_v2.optimization.optuna_runner as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert 'return float("-inf")' not in source

    def test_backtest_failure_propagates_as_fail(self):
        """A crashing backtest raises so Optuna records FAIL, not COMPLETE."""

        class BoomAdapter:
            def run_backtest(self, **kwargs):
                raise RuntimeError("boom")

        runner = OptunaRunner.__new__(OptunaRunner)
        runner.adapter = BoomAdapter()

        study = optuna.create_study(direction="maximize")

        def objective(trial):
            return runner._objective(
                trial=trial,
                strategy="mean_reversion",
                objective="sharpe_ratio",
                start="2024-01-01",
                end="2024-02-01",
                symbol="SUI-USDC",
                initial_capital=10000.0,
                walk_forward=False,
                train_months=6,
                test_months=1,
            )

        study.optimize(objective, n_trials=2, catch=(Exception,))
        states = [t.state for t in study.trials]
        assert all(s == optuna.trial.TrialState.FAIL for s in states)
        assert all(t.value is None for t in study.trials)

    def test_empty_walk_forward_raises_instead_of_scoring(self):
        class EmptyAdapter:
            def run_walk_forward(self, **kwargs):
                return []

        runner = OptunaRunner.__new__(OptunaRunner)
        runner.adapter = EmptyAdapter()
        study = optuna.create_study(direction="maximize")
        trial = study.ask()
        with pytest.raises(RuntimeError, match="no windows"):
            runner._objective(
                trial=trial,
                strategy="mean_reversion",
                objective="sharpe_ratio",
                start="2024-01-01",
                end="2024-02-01",
                symbol="SUI-USDC",
                initial_capital=10000.0,
                walk_forward=True,
                train_months=6,
                test_months=1,
            )


# ---------------------------------------------------------------------------
# Trial registry DSR fix
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    """DatabaseManager wired to a temporary SQLite file."""
    import trading_bot_v2.database as db_mod

    db_file = str(tmp_path / "trial_registry_outcomes.db")
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


class TestTrialRegistryInfeasibleExclusion:
    def test_is_infeasible_trial_detection(self):
        infeasible = StubTrial(
            attrs={"outcome": TrialOutcome.INFEASIBLE_CONFIG.value}
        )
        regular = StubTrial(attrs={"outcome": "no_opportunities"})
        assert _is_infeasible_trial(infeasible) is True
        assert _is_infeasible_trial(regular) is False
        # Trials without user_attrs at all (older studies, stubs)
        assert _is_infeasible_trial(object()) is False

    def test_trial_registry_excludes_infeasible(self, tmp_db):
        """Infeasible configs explored nothing - counting them over-deflates DSR.

        validation/gate.py feeds trial_registry.n_trials to the deflated
        Sharpe ratio, which deflates harder as N grows. Pre-backtest
        feasibility pruning must not make the gate harder to pass.
        """
        state = optuna.trial.TrialState
        infeasible = {"outcome": TrialOutcome.INFEASIBLE_CONFIG.value}
        study = StubStudy(
            name="momentum_scalping_stub",
            trials=[
                StubTrial(0, state.COMPLETE, 1.0),
                StubTrial(1, state.COMPLETE, 2.0),
                StubTrial(2, state.COMPLETE, 3.0),
                StubTrial(3, state.PRUNED, attrs={"outcome": "regime_trades"}),
                StubTrial(4, state.PRUNED, attrs=infeasible),
                StubTrial(5, state.PRUNED, attrs=infeasible),
                StubTrial(6, state.PRUNED, attrs=infeasible),
                StubTrial(7, state.FAIL),
            ],
        )

        runner = OptunaRunner.__new__(OptunaRunner)
        runner._record_trial_registry(
            study, "momentum_scalping", None, "sharpe_ratio"
        )

        rows = tmp_db.get_trial_registry(strategy="momentum_scalping")
        assert len(rows) == 1
        # 3 COMPLETE + 1 real PRUNED. FAIL and the 3 infeasible are excluded.
        assert rows[0]["n_trials"] == 4
        assert rows[0]["sr_variance"] == pytest.approx(1.0)

    def test_all_infeasible_study_records_nothing(self, tmp_db):
        state = optuna.trial.TrialState
        infeasible = {"outcome": TrialOutcome.INFEASIBLE_CONFIG.value}
        study = StubStudy(
            name="all_infeasible",
            trials=[StubTrial(i, state.PRUNED, attrs=infeasible) for i in range(5)],
        )
        runner = OptunaRunner.__new__(OptunaRunner)
        runner._record_trial_registry(
            study, "momentum_scalping", None, "sharpe_ratio"
        )
        assert tmp_db.get_trial_registry(strategy="momentum_scalping") == []


# ---------------------------------------------------------------------------
# Scoring schema guard and fail-rate abort
# ---------------------------------------------------------------------------


class TestScoringSchemaGuard:
    def test_stamps_a_fresh_study(self):
        study = StubStudy()
        _assert_scoring_schema(study)
        assert study.user_attrs["scoring_schema"] == SCORING_SCHEMA

    def test_accepts_a_matching_study(self):
        study = StubStudy(attrs={"scoring_schema": SCORING_SCHEMA})
        _assert_scoring_schema(study)  # must not raise

    def test_refuses_an_unstamped_study_with_trials(self):
        study = StubStudy(trials=[StubTrial(0)])
        with pytest.raises(ValueError, match="older scheme"):
            _assert_scoring_schema(study)

    def test_refuses_a_different_schema(self):
        study = StubStudy(attrs={"scoring_schema": 1})
        with pytest.raises(ValueError, match="scoring schema"):
            _assert_scoring_schema(study)


class TestFailRateGuard:
    def _study_with(self, n_fail: int, n_ok: int) -> StubStudy:
        state = optuna.trial.TrialState
        trials = [StubTrial(i, state.FAIL) for i in range(n_fail)]
        trials += [
            StubTrial(n_fail + i, state.COMPLETE, 1.0) for i in range(n_ok)
        ]
        return StubStudy(trials=trials)

    def test_quiet_below_the_minimum_trial_count(self):
        guard = _FailRateGuard("s")
        guard(self._study_with(5, 0), None)
        assert guard.aborted is False
        guard.raise_if_aborted()

    def test_quiet_when_failures_are_a_minority(self):
        guard = _FailRateGuard("s")
        guard(self._study_with(4, 8), None)
        assert guard.aborted is False

    def test_aborts_loudly_above_fifty_percent(self):
        guard = _FailRateGuard("s")
        study = self._study_with(9, 3)
        guard(study, None)
        assert guard.aborted is True
        assert study.stopped is True
        with pytest.raises(RuntimeError, match="trials failed"):
            guard.raise_if_aborted()
