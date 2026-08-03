"""
Tests for P4: Per-Regime Parameter Optimization + Runtime Overlays
==================================================================

Covers:
- Backtest trades carry regime tags (entry regime preserved on close)
- BacktestResult per-regime breakdown
- Regime-filtered objective computed on the correct trade subset
- Min-trades pruning of regime trials (single-window and walk-forward)
- Study naming includes the regime
- Overlay save deactivates the prior overlay + JSON write-through
- Whitelist: non-whitelisted stored params are ignored with a warning
- Runtime manager: applies on regime change, restores baseline, inert
  when ENABLE_REGIME_PARAM_OVERLAYS is false
"""

import gc
import json
import os
import tempfile
from types import SimpleNamespace

import pytest

from trading_bot_v2.backtesting.optimization_adapter import OptimizationAdapter
from trading_bot_v2.backtesting.performance import (
    BacktestResult,
    PerformanceTracker,
)
from trading_bot_v2.backtesting.simulated_exchange import SimulatedExchange
from trading_bot_v2.regime_param_overlay import (
    DISPLAY_TO_STRATEGY_KEY,
    RegimeParamOverlayManager,
    STRATEGY_KEY_TO_DISPLAY,
    apply_params_to_strategy,
    get_param_whitelist,
    normalize_regime_value,
    resolve_strategy_display_name,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candle(price: float) -> dict:
    return {
        "open": price,
        "high": price,
        "low": price,
        "close": price,
        "volume": 1.0,
    }


def _result(trade_log, start="2024-01-01", end="2024-12-31"):
    return BacktestResult(
        symbol="TEST-USDC",
        start=start,
        end=end,
        initial_capital=10000.0,
        final_equity=10000.0,
        trade_log=trade_log,
    )


class DummyStrategy:
    """Bare object mimicking MeanReversionStrategy's tunable attrs."""

    def __init__(self):
        self.rsi_oversold = 35.0
        self.rsi_overbought = 65.0
        self.bb_std_dev = 2.0
        self.atr_stop_multiplier = 2.0
        self.min_confidence = 0.45


class DummyStrategyManager:
    def __init__(self):
        self.strategies = {"MeanReversion": DummyStrategy()}


class FakeOverlayDb:
    """Duck-typed db exposing get_regime_param_overlays."""

    def __init__(self, rows):
        self.rows = rows

    def get_regime_param_overlays(self, active_only=True, strategy=None):
        return self.rows


def _event(symbol, new_regime, old_regime="indecisive"):
    return SimpleNamespace(
        data={
            "symbol": symbol,
            "old_regime": old_regime,
            "new_regime": new_regime,
        }
    )


# ---------------------------------------------------------------------------
# 1. Regime tags on backtest trades
# ---------------------------------------------------------------------------


class TestBacktestRegimeTags:
    def test_open_and_close_fills_are_regime_tagged(self):
        ex = SimulatedExchange(
            initial_capital=10000.0,
            slippage_pct=0.0,
            taker_fee_pct=0.0,
            maker_fee_pct=0.0,
        )
        ex.advance(_candle(100.0), "2024-01-01T00:05:00")
        ex._current_regime = "ranging_calm"
        ex._current_strategy = "mean_reversion"
        ex.place_order("SUI-USDC", "bid", "1", order_type="market")

        # Regime flips while the position is open
        ex._current_regime = "trending_strong"
        ex.advance(_candle(110.0), "2024-01-01T01:05:00")
        ex.place_order("SUI-USDC", "ask", "1", order_type="market")

        assert len(ex.trade_log) == 2
        open_row, close_row = ex.trade_log
        assert open_row["pnl"] == 0
        assert open_row["regime"] == "ranging_calm"
        # Closing fill carries the regime at ENTRY, not the current one
        assert close_row["pnl"] > 0
        assert close_row["regime"] == "ranging_calm"

    def test_new_position_after_flat_uses_current_regime(self):
        ex = SimulatedExchange(
            initial_capital=10000.0,
            slippage_pct=0.0,
            taker_fee_pct=0.0,
            maker_fee_pct=0.0,
        )
        ex.advance(_candle(100.0), "2024-01-01T00:05:00")
        ex._current_regime = "ranging_volatile"
        ex.place_order("SUI-USDC", "bid", "1", order_type="market")
        assert ex._positions["SUI-USDC"].entry_regime == "ranging_volatile"
        assert ex.trade_log[-1]["regime"] == "ranging_volatile"

    def test_finalise_populates_by_regime(self):
        tracker = PerformanceTracker(initial_capital=10000.0)
        tracker.record_snapshot("2024-01-01T00:00:00", 10000.0, {})
        tracker.record_snapshot("2024-01-02T00:00:00", 10080.0, {})
        trade_log = [
            {"pnl": 0, "regime": "ranging_calm", "fee": 0},
            {"pnl": 100.0, "regime": "ranging_calm", "fee": 0},
            {"pnl": -20.0, "regime": "ranging_volatile", "fee": 0},
        ]
        result = tracker.finalise(
            final_equity=10080.0,
            trade_log=trade_log,
            symbol="TEST-USDC",
            start="2024-01-01",
            end="2024-01-31",
        )
        assert result.by_regime["ranging_calm"]["closed_trades"] == 1
        assert result.by_regime["ranging_calm"]["pnl"] == pytest.approx(100.0)
        assert result.by_regime["ranging_calm"]["wins"] == 1
        assert result.by_regime["ranging_volatile"]["closed_trades"] == 1
        assert result.by_regime["ranging_volatile"]["wins"] == 0


# ---------------------------------------------------------------------------
# 2. Regime-filtered objective on synthetic trades
# ---------------------------------------------------------------------------


class TestRegimeFilteredObjective:
    def setup_method(self):
        self.adapter = OptimizationAdapter()
        self.trade_log = [
            {"pnl": 0, "regime": "ranging_calm"},  # opening fill - excluded
            {"pnl": 100.0, "regime": "ranging_calm"},
            {"pnl": -50.0, "regime": "ranging_calm"},
            {"pnl": 200.0, "regime": "trending_strong"},
            {"pnl": 25.0, "regime": ""},  # untagged - excluded
        ]

    def test_filters_correct_subset(self):
        result = _result(self.trade_log)
        trades = self.adapter.get_regime_trades(result, "RANGING_CALM")
        assert [t["pnl"] for t in trades] == [100.0, -50.0]

        trades_ts = self.adapter.get_regime_trades(result, "trending_strong")
        assert [t["pnl"] for t in trades_ts] == [200.0]

    def test_total_return_objective_on_subset(self):
        result = _result(self.trade_log)
        trades = self.adapter.get_regime_trades(result, "ranging_calm")
        value = self.adapter.calculate_objective_from_trades(
            trades=trades,
            objective="total_return_pct",
            initial_capital=10000.0,
            start="2024-01-01",
            end="2024-12-31",
        )
        # (100 - 50) / 10000 * 100 = 0.5; no penalties apply
        assert value == pytest.approx(0.5)

    def test_profit_factor_objective_on_subset(self):
        result = _result(self.trade_log)
        trades = self.adapter.get_regime_trades(result, "ranging_calm")
        value = self.adapter.calculate_objective_from_trades(
            trades=trades,
            objective="profit_factor",
            initial_capital=10000.0,
            start="2024-01-01",
            end="2024-12-31",
        )
        assert value == pytest.approx(100.0 / 50.0)

    def test_sharpe_positive_for_profitable_subset(self):
        trades = [{"pnl": 50.0}, {"pnl": 30.0}, {"pnl": -10.0}]
        value = self.adapter.calculate_objective_from_trades(
            trades=trades,
            objective="sharpe_ratio",
            initial_capital=10000.0,
            start="2024-01-01",
            end="2024-12-31",
        )
        assert value > 0

    def test_empty_subset_raises(self):
        with pytest.raises(ValueError, match="empty trade list"):
            self.adapter.calculate_objective_from_trades(
                trades=[],
                objective="sharpe_ratio",
                initial_capital=10000.0,
                start="2024-01-01",
                end="2024-12-31",
            )


# ---------------------------------------------------------------------------
# 3. Min-trades pruning + study naming (requires optuna)
# ---------------------------------------------------------------------------


optuna = pytest.importorskip("optuna")

from trading_bot_v2.optimization.optuna_runner import (  # noqa: E402
    OptunaRunner,
    normalize_objective,
)


class StubAdapter:
    """Adapter stub with controllable regime-matching trades."""

    def __init__(self, matching_trades, n_windows=1):
        self.matching = matching_trades
        self.n_windows = n_windows

    def run_backtest(self, **kwargs):
        return _result([], start=kwargs["start"], end=kwargs["end"])

    def run_walk_forward(self, **kwargs):
        return [
            _result([], start="2024-07-01", end="2024-07-31")
            for _ in range(self.n_windows)
        ]

    def get_regime_trades(self, result, regime):
        return self.matching

    def calculate_objective_from_trades(
        self, trades, objective, initial_capital, start, end
    ):
        return 1.23

    def calculate_objective(self, result, objective):
        return 0.0


@pytest.fixture
def optuna_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    gc.collect()
    try:
        os.unlink(db_path)
    except (PermissionError, OSError):
        pass  # Windows may keep the file locked


class TestMinTradesPruning:
    def test_pruning_fires_when_too_few_matching_trades(self, optuna_db):
        runner = OptunaRunner(db_path=optuna_db)
        runner.adapter = StubAdapter(matching_trades=[{"pnl": 1.0}] * 3)

        study = runner.optimize(
            strategy="mean_reversion",
            n_trials=3,
            sampler="random",
            objective="sharpe",
            start="2024-01-01",
            end="2024-12-31",
            symbol="TEST-USDC",
            regime="RANGING_CALM",
            min_regime_trades=15,
        )

        states = [t.state.name for t in study.trials]
        assert states == ["PRUNED"] * 3
        assert all(
            t.user_attrs.get("regime_trade_count") == 3 for t in study.trials
        )

    def test_enough_trades_completes_and_names_study_with_regime(
        self, optuna_db
    ):
        runner = OptunaRunner(db_path=optuna_db)
        runner.adapter = StubAdapter(matching_trades=[{"pnl": 1.0}] * 20)

        study = runner.optimize(
            strategy="mean_reversion",
            n_trials=2,
            sampler="random",
            objective="sharpe",
            start="2024-01-01",
            end="2024-12-31",
            symbol="TEST-USDC",
            regime="RANGING_CALM",
            min_regime_trades=15,
        )

        completed = [t for t in study.trials if t.state.name == "COMPLETE"]
        assert len(completed) == 2
        assert study.best_value == pytest.approx(1.23)
        # Study name embeds regime + objective (no collisions per regime)
        assert "RANGING_CALM" in study.study_name
        assert "sharpe" in study.study_name
        assert study.study_name.startswith("mean_reversion_")

    def test_walk_forward_regime_mode(self, optuna_db):
        runner = OptunaRunner(db_path=optuna_db)
        runner.adapter = StubAdapter(
            matching_trades=[{"pnl": 1.0}] * 10, n_windows=2
        )

        study = runner.optimize(
            strategy="mean_reversion",
            n_trials=1,
            sampler="random",
            objective="sharpe",
            start="2024-01-01",
            end="2024-12-31",
            symbol="TEST-USDC",
            walk_forward=True,
            regime="RANGING_CALM",
            min_regime_trades=15,  # 2 windows x 10 = 20 >= 15
        )

        completed = [t for t in study.trials if t.state.name == "COMPLETE"]
        assert len(completed) == 1
        # Both windows scored 1.23, zero variance -> no penalty
        assert study.best_value == pytest.approx(1.23)
        assert study.trials[0].user_attrs["regime_trade_count"] == 20

    def test_normalize_objective(self):
        assert normalize_objective("sharpe") == "sharpe_ratio"
        assert normalize_objective("sharpe_ratio") == "sharpe_ratio"
        assert normalize_objective("pf") == "profit_factor"
        with pytest.raises(ValueError, match="Unknown objective"):
            normalize_objective("nonsense")


# ---------------------------------------------------------------------------
# 4. Overlay store round-trip (temp SQLite)
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    """DatabaseManager wired to a temporary SQLite file."""
    import trading_bot_v2.database as db_mod

    db_file = str(tmp_path / "overlay_test.db")
    export_path = str(tmp_path / "regime_param_overlays.json")
    monkeypatch.setenv("DATABASE_PATH", db_file)
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")
    monkeypatch.setenv("REGIME_OVERLAY_EXPORT_PATH", export_path)

    original_backend = db_mod._active_backend
    original_path = db_mod.DATABASE_PATH
    original_pool = db_mod._connection_pool
    db_mod._active_backend = "sqlite"
    db_mod.DATABASE_PATH = db_file
    pool = db_mod.ConnectionPool(max_connections=2)
    db_mod._connection_pool = pool

    db_mod.init_database()

    yield db_mod.DatabaseManager(), export_path

    pool.close_all()
    db_mod._active_backend = original_backend
    db_mod.DATABASE_PATH = original_path
    db_mod._connection_pool = original_pool


class TestOverlayStore:
    def test_save_round_trip(self, tmp_db):
        db, _ = tmp_db
        params = {"rsi_oversold": 30.5, "min_confidence": 0.52}
        row_id = db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params=params,
            objective="sharpe_ratio",
            objective_value=1.42,
            trade_count=33,
            study_name="mean_reversion_RANGING_CALM_sharpe_x",
        )
        assert row_id is not None

        active = db.get_regime_param_overlays(active_only=True)
        assert len(active) == 1
        row = active[0]
        assert row["strategy"] == "mean_reversion"
        assert row["regime"] == "ranging_calm"
        assert row["params"] == params
        assert row["objective_value"] == pytest.approx(1.42)
        assert row["trade_count"] == 33
        assert row["active"] == 1

    def test_new_save_deactivates_prior_and_keeps_history(self, tmp_db):
        db, _ = tmp_db
        db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params={"rsi_oversold": 30.0},
        )
        db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params={"rsi_oversold": 27.0},
        )
        # Different regime stays independently active
        db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_volatile",
            params={"rsi_oversold": 40.0},
        )

        active = db.get_regime_param_overlays(active_only=True)
        assert len(active) == 2
        calm = [r for r in active if r["regime"] == "ranging_calm"]
        assert len(calm) == 1
        assert calm[0]["params"] == {"rsi_oversold": 27.0}

        # History preserved: 3 rows total, deactivated row kept
        all_rows = db.get_regime_param_overlays(active_only=False)
        assert len(all_rows) == 3
        inactive = [r for r in all_rows if r["active"] == 0]
        assert len(inactive) == 1
        assert inactive[0]["params"] == {"rsi_oversold": 30.0}

    def test_json_write_through(self, tmp_db):
        db, export_path = tmp_db
        db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params={"bb_std_dev": 1.75},
        )
        assert os.path.exists(export_path)
        with open(export_path) as f:
            payload = json.load(f)
        assert len(payload["active_overlays"]) == 1
        assert payload["active_overlays"][0]["params"] == {"bb_std_dev": 1.75}

        # Overwriting updates the export in place
        db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params={"bb_std_dev": 2.25},
        )
        with open(export_path) as f:
            payload = json.load(f)
        assert len(payload["active_overlays"]) == 1
        assert payload["active_overlays"][0]["params"] == {"bb_std_dev": 2.25}


class TestLosingOverlayGuard:
    """A study whose best trial lost money must not become an overlay.

    Optuna reports a best trial even when every candidate lost; that
    "winner" is just the least-bad loser. Storing it active means the
    strategy silently adopts a measured-losing parameter set the moment
    ENABLE_REGIME_PARAM_OVERLAYS is turned on. This is exactly how
    overlay id=1 (mean_reversion / ranging_calm, Sharpe -1.486) came to
    be stored on 2026-07-20.
    """

    def test_losing_sharpe_is_refused_and_nothing_is_written(self, tmp_db):
        from trading_bot_v2.database import LosingOverlayRefused

        db, _ = tmp_db
        with pytest.raises(LosingOverlayRefused) as excinfo:
            db.save_regime_param_overlay(
                strategy="mean_reversion",
                regime="ranging_calm",
                params={"rsi_oversold": 44.96},
                objective="sharpe_ratio",
                objective_value=-1.4860599305891151,
                trade_count=246,
                study_name="mean_reversion_RANGING_CALM_sharpe_20260720_070349",
            )

        # The refusal names the offending value so it cannot be missed
        message = str(excinfo.value)
        assert "-1.4860599305891151" in message
        assert "sharpe_ratio" in message
        assert "mean_reversion" in message
        assert excinfo.value.objective_value == pytest.approx(-1.48605993)
        assert excinfo.value.break_even == 0.0

        # Refusing loudly must also mean refusing completely
        assert db.get_regime_param_overlays(active_only=False) == []

    def test_zero_sharpe_is_refused_break_even_is_not_an_edge(self, tmp_db):
        from trading_bot_v2.database import LosingOverlayRefused

        db, _ = tmp_db
        with pytest.raises(LosingOverlayRefused):
            db.save_regime_param_overlay(
                strategy="mean_reversion",
                regime="ranging_calm",
                params={"rsi_oversold": 30.0},
                objective="sharpe_ratio",
                objective_value=0.0,
            )
        assert db.get_regime_param_overlays(active_only=False) == []

    def test_winning_study_is_stored(self, tmp_db):
        db, _ = tmp_db
        row_id = db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params={"rsi_oversold": 30.0},
            objective="sharpe_ratio",
            objective_value=1.42,
        )
        assert row_id is not None
        active = db.get_regime_param_overlays(active_only=True)
        assert len(active) == 1
        assert active[0]["objective_value"] == pytest.approx(1.42)

    def test_profit_factor_break_even_is_one_not_zero(self, tmp_db):
        """PF 0.9 is losing even though it is a positive number."""
        from trading_bot_v2.database import LosingOverlayRefused

        db, _ = tmp_db
        with pytest.raises(LosingOverlayRefused) as excinfo:
            db.save_regime_param_overlay(
                strategy="grid_trading",
                regime="ranging_volatile",
                params={"grid_levels": 6},
                objective="profit_factor",
                objective_value=0.90,
            )
        assert excinfo.value.break_even == 1.0

        # PF above 1.0 stores normally
        row_id = db.save_regime_param_overlay(
            strategy="grid_trading",
            regime="ranging_volatile",
            params={"grid_levels": 6},
            objective="profit_factor",
            objective_value=1.35,
        )
        assert row_id is not None

    def test_short_objective_aliases_are_recognised(self, tmp_db):
        """'pf' must resolve to profit_factor's 1.0 break-even."""
        from trading_bot_v2.database import LosingOverlayRefused

        db, _ = tmp_db
        with pytest.raises(LosingOverlayRefused) as excinfo:
            db.save_regime_param_overlay(
                strategy="grid_trading",
                regime="ranging_volatile",
                params={"grid_levels": 6},
                objective="pf",
                objective_value=0.95,
            )
        assert excinfo.value.break_even == 1.0

    def test_unnamed_objective_still_refuses_a_negative_value(self, tmp_db):
        """objective=None must not be a hole in the guard."""
        from trading_bot_v2.database import LosingOverlayRefused

        db, _ = tmp_db
        with pytest.raises(LosingOverlayRefused):
            db.save_regime_param_overlay(
                strategy="mean_reversion",
                regime="ranging_calm",
                params={"rsi_oversold": 30.0},
                objective=None,
                objective_value=-0.5,
            )

    def test_no_objective_value_is_not_judged(self, tmp_db):
        """Callers that store params only keep working."""
        db, _ = tmp_db
        row_id = db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params={"rsi_oversold": 30.0},
        )
        assert row_id is not None
        assert len(db.get_regime_param_overlays(active_only=True)) == 1

    def test_allow_losing_kwarg_overrides(self, tmp_db):
        db, _ = tmp_db
        row_id = db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params={"rsi_oversold": 44.96},
            objective="sharpe_ratio",
            objective_value=-1.486,
            allow_losing=True,
        )
        assert row_id is not None
        active = db.get_regime_param_overlays(active_only=True)
        assert len(active) == 1
        assert active[0]["objective_value"] == pytest.approx(-1.486)

    def test_env_opt_in_overrides(self, tmp_db, monkeypatch):
        db, _ = tmp_db
        monkeypatch.setenv("ALLOW_LOSING_REGIME_OVERLAYS", "true")
        row_id = db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params={"rsi_oversold": 44.96},
            objective="sharpe_ratio",
            objective_value=-1.486,
        )
        assert row_id is not None
        assert len(db.get_regime_param_overlays(active_only=True)) == 1

    def test_env_opt_in_defaults_off(self, tmp_db, monkeypatch):
        from trading_bot_v2.database import LosingOverlayRefused

        db, _ = tmp_db
        monkeypatch.delenv("ALLOW_LOSING_REGIME_OVERLAYS", raising=False)
        with pytest.raises(LosingOverlayRefused):
            db.save_regime_param_overlay(
                strategy="mean_reversion",
                regime="ranging_calm",
                params={"rsi_oversold": 44.96},
                objective="sharpe_ratio",
                objective_value=-1.486,
            )

    # -- The restored-backup hazard -----------------------------------
    #
    # The write guard above only sees rows written through the save API.
    # A restore is shutil.copy2 (database.py _restore_sqlite) and never
    # touches it, so every one of the 11 snapshots under backups/ can
    # reinstate overlay id=1 with active=1 - a measured Sharpe of
    # -1.486. The snapshots are audit material and are deliberately NOT
    # rewritten; the defence is that active=1 is not the same claim as
    # "may be applied", enforced wherever a row leaves the table.

    @staticmethod
    def _insert_unguarded(db, objective_value, objective="sharpe_ratio"):
        """Write an active overlay by raw SQL, bypassing the save guard.

        This is what a restored snapshot or a hand-edited database looks
        like from the reader's side: a row the write path would never
        have produced.
        """
        import json as _json

        from trading_bot_v2.database import get_db_connection

        with get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO regime_param_overlays
                    (strategy, regime, params_json, objective,
                     objective_value, trade_count, study_name, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    "mean_reversion", "ranging_calm",
                    _json.dumps({"rsi_oversold": 44.96}),
                    objective, objective_value, 246,
                    "mean_reversion_RANGING_CALM_sharpe_20260720_070349",
                ),
            )
            conn.commit()

    def test_restored_losing_row_is_active_but_never_loaded(self, tmp_db):
        """The row survives the copy; the manager still refuses it."""
        from trading_bot_v2.regime_param_overlay import (
            RegimeParamOverlayManager,
        )

        db, _ = tmp_db
        self._insert_unguarded(db, -1.4860599305891151)
        assert len(db.get_regime_param_overlays(active_only=True)) == 1

        manager = RegimeParamOverlayManager(
            SimpleNamespace(strategies={}), db=db, enabled=True
        )

        assert manager._overlays == {}
        assert ("mean_reversion", "ranging_calm") in manager._refused
        assert "-1.486" in manager._refused[("mean_reversion", "ranging_calm")]

    def test_restored_losing_row_is_refused_again_at_apply(self, tmp_db):
        """Defence in depth: reload is not the only checkpoint.

        _overlays can be populated by something other than reload(), so
        the guarantee has to be "nothing losing reaches a strategy", not
        "nothing losing survives reload".
        """
        from trading_bot_v2.regime_param_overlay import (
            RegimeParamOverlayManager,
        )

        db, _ = tmp_db
        manager = RegimeParamOverlayManager(
            SimpleNamespace(strategies={}), db=db, enabled=True
        )
        key = ("mean_reversion", "ranging_calm")
        manager._overlays[key] = {"rsi_oversold": 44.96}
        manager._overlay_scores[key] = ("sharpe_ratio", -1.486)

        assert manager._refuse_at_apply(key) is True

    def test_unscored_restored_row_is_refused_too(self, tmp_db):
        """No score is not the same as a good score.

        The write guard is permissive about a missing value because
        storing an unscored row is harmless audit material. Applying one
        is not - it is exactly the shape of a row that never went
        through a measured write path.
        """
        from trading_bot_v2.regime_param_overlay import (
            RegimeParamOverlayManager,
        )

        db, _ = tmp_db
        self._insert_unguarded(db, None, objective=None)

        manager = RegimeParamOverlayManager(
            SimpleNamespace(strategies={}), db=db, enabled=True
        )

        assert manager._overlays == {}
        assert ("mean_reversion", "ranging_calm") in manager._refused

    def test_export_does_not_launder_a_refused_row(self, tmp_db):
        """The export was the last way out of the guarded database.

        config/regime_param_overlays.json looks authoritative; a losing
        row listed there under "active_overlays" would outlive the
        database it came from.
        """
        import json as _json

        db, export_path = tmp_db
        self._insert_unguarded(db, -1.4860599305891151)

        db.export_regime_param_overlays()

        with open(export_path, encoding="utf-8") as fh:
            payload = _json.load(fh)

        assert payload["active_overlays"] == []
        assert len(payload["refused_overlays"]) == 1
        refused = payload["refused_overlays"][0]
        assert refused["strategy"] == "mean_reversion"
        assert "break-even" in refused["refused_reason"]
        assert payload["allow_losing_opt_in"] is False

    def test_export_still_carries_a_winning_row(self, tmp_db):
        """The filter must be about the score, not about exporting."""
        import json as _json

        db, export_path = tmp_db
        db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params={"rsi_oversold": 30.0},
            objective="sharpe_ratio",
            objective_value=1.42,
        )

        db.export_regime_param_overlays()

        with open(export_path, encoding="utf-8") as fh:
            payload = _json.load(fh)

        assert len(payload["active_overlays"]) == 1
        assert payload["refused_overlays"] == []

    def test_export_keeps_an_unscored_row_active(self, tmp_db):
        """Deliberate asymmetry with the apply side, pinned here.

        A params-only save carries no score, is permitted by the write
        guard, and this file is the store's mirror - so it stays under
        active_overlays. The apply side still refuses it fail-closed
        (test_unscored_restored_row_is_refused_too). Unknown provenance
        is a reason not to trade a row, not a reason to relabel it in an
        inspection file.
        """
        import json as _json

        db, export_path = tmp_db
        db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params={"rsi_oversold": 30.0},
        )

        db.export_regime_param_overlays()

        with open(export_path, encoding="utf-8") as fh:
            payload = _json.load(fh)

        assert len(payload["active_overlays"]) == 1
        assert payload["refused_overlays"] == []

    def test_export_honours_the_operator_opt_in(self, tmp_db, monkeypatch):
        """The file must describe what would actually be applied."""
        import json as _json

        db, export_path = tmp_db
        self._insert_unguarded(db, -1.486)
        monkeypatch.setenv("ALLOW_LOSING_REGIME_OVERLAYS", "true")

        db.export_regime_param_overlays()

        with open(export_path, encoding="utf-8") as fh:
            payload = _json.load(fh)

        assert len(payload["active_overlays"]) == 1
        assert payload["refused_overlays"] == []
        assert payload["allow_losing_opt_in"] is True

    def test_the_real_backup_snapshots_are_refused(self, tmp_path):
        """Run the guard against the actual files, not a reconstruction.

        Skips rather than fails when backups/ is absent - the snapshots
        are operational artifacts, not fixtures, and are expected to
        move when the working files are migrated off this drive.
        """
        import glob
        import shutil
        import sqlite3

        import trading_bot_v2.database as db_mod
        from trading_bot_v2.regime_param_overlay import (
            RegimeParamOverlayManager,
        )

        snapshots = sorted(glob.glob("backups/trading_bot_*.db"))
        if not snapshots:
            pytest.skip("no backup snapshots present")

        carriers = []
        for snapshot in snapshots:
            probe = sqlite3.connect(snapshot)
            try:
                rows = probe.execute(
                    "SELECT objective, objective_value FROM "
                    "regime_param_overlays WHERE active = 1"
                ).fetchall()
            except sqlite3.Error:
                continue
            finally:
                probe.close()
            if any(v is not None and v <= 0.0 for _, v in rows):
                carriers.append(snapshot)

        if not carriers:
            pytest.skip("no snapshot carries an active losing overlay")

        for snapshot in carriers:
            restored = str(tmp_path / "restored.db")
            shutil.copy2(snapshot, restored)

            original_path = db_mod.DATABASE_PATH
            original_pool = db_mod._connection_pool
            db_mod.DATABASE_PATH = restored
            pool = db_mod.ConnectionPool(max_connections=2)
            db_mod._connection_pool = pool
            try:
                db = db_mod.DatabaseManager()
                assert db.get_regime_param_overlays(active_only=True), snapshot

                manager = RegimeParamOverlayManager(
                    SimpleNamespace(strategies={}), db=db, enabled=True
                )
                assert manager._overlays == {}, snapshot
                assert manager._refused, snapshot
            finally:
                pool.close_all()
                db_mod.DATABASE_PATH = original_path
                db_mod._connection_pool = original_pool

    def test_guard_sits_at_the_chokepoint_not_the_caller(self, tmp_db):
        """save_overlay_from_study cannot write past the guard.

        The check lives in save_regime_param_overlay, so the CLI path
        inherits it rather than reimplementing it. Here the refusal is
        surfaced as False (the sweep continues) but nothing is stored.
        """
        from trading_bot_v2.optimization.run_optimize import (
            save_overlay_from_study,
        )

        db, _ = tmp_db
        best_trial = SimpleNamespace(
            params={"rsi_oversold": 44.96},
            user_attrs={"regime_trade_count": 246},
        )
        study = SimpleNamespace(
            best_trial=best_trial,
            best_value=-1.4860599305891151,
            study_name="mean_reversion_RANGING_CALM_sharpe_20260720_070349",
        )

        saved = save_overlay_from_study(
            study, "mean_reversion", "RANGING_CALM", "sharpe"
        )
        assert saved is False
        assert db.get_regime_param_overlays(active_only=False) == []

        # ...and the same path stores a winner
        study.best_value = 1.42
        assert save_overlay_from_study(
            study, "mean_reversion", "RANGING_CALM", "sharpe"
        )
        assert len(db.get_regime_param_overlays(active_only=True)) == 1

    def test_save_overlay_from_study_honours_allow_losing(self, tmp_db):
        from trading_bot_v2.optimization.run_optimize import (
            save_overlay_from_study,
        )

        db, _ = tmp_db
        study = SimpleNamespace(
            best_trial=SimpleNamespace(
                params={"rsi_oversold": 44.96},
                user_attrs={"regime_trade_count": 246},
            ),
            best_value=-1.486,
            study_name="mean_reversion_RANGING_CALM_sharpe_x",
        )
        assert save_overlay_from_study(
            study,
            "mean_reversion",
            "RANGING_CALM",
            "sharpe",
            allow_losing=True,
        )
        assert len(db.get_regime_param_overlays(active_only=True)) == 1


class TestOverlayDeactivation:
    """Deactivation neutralises an overlay without losing the record."""

    def test_deactivate_by_id_keeps_history(self, tmp_db):
        db, _ = tmp_db
        row_id = db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params={"rsi_oversold": 44.96},
            objective="sharpe_ratio",
            objective_value=1.42,
            trade_count=246,
            study_name="mean_reversion_RANGING_CALM_sharpe_x",
        )

        changed = db.deactivate_regime_param_overlay(overlay_id=row_id)
        assert changed == 1

        # Nothing is applied any more...
        assert db.get_regime_param_overlays(active_only=True) == []

        # ...but the audit trail is fully intact
        all_rows = db.get_regime_param_overlays(active_only=False)
        assert len(all_rows) == 1
        row = all_rows[0]
        assert row["active"] == 0
        assert row["id"] == row_id
        assert row["params"] == {"rsi_oversold": 44.96}
        assert row["objective_value"] == pytest.approx(1.42)
        assert row["trade_count"] == 246
        assert row["study_name"] == "mean_reversion_RANGING_CALM_sharpe_x"
        assert row["created_at"] is not None

    def test_deactivate_is_idempotent(self, tmp_db):
        db, _ = tmp_db
        row_id = db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params={"rsi_oversold": 30.0},
        )
        assert db.deactivate_regime_param_overlay(overlay_id=row_id) == 1
        assert db.deactivate_regime_param_overlay(overlay_id=row_id) == 0
        assert len(db.get_regime_param_overlays(active_only=False)) == 1

    def test_deactivate_by_strategy_and_regime_is_targeted(self, tmp_db):
        db, _ = tmp_db
        db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params={"rsi_oversold": 30.0},
        )
        db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_volatile",
            params={"rsi_oversold": 40.0},
        )
        db.save_regime_param_overlay(
            strategy="grid_trading",
            regime="ranging_calm",
            params={"grid_levels": 6},
        )

        changed = db.deactivate_regime_param_overlay(
            strategy="mean_reversion", regime="ranging_calm"
        )
        assert changed == 1

        active = db.get_regime_param_overlays(active_only=True)
        assert len(active) == 2
        assert {(r["strategy"], r["regime"]) for r in active} == {
            ("mean_reversion", "ranging_volatile"),
            ("grid_trading", "ranging_calm"),
        }
        assert len(db.get_regime_param_overlays(active_only=False)) == 3

    def test_deactivate_without_a_selector_is_refused(self, tmp_db):
        db, _ = tmp_db
        db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params={"rsi_oversold": 30.0},
        )
        with pytest.raises(ValueError, match="at least one of"):
            db.deactivate_regime_param_overlay()
        # The active overlay is untouched
        assert len(db.get_regime_param_overlays(active_only=True)) == 1

    def test_deactivation_rewrites_the_json_mirror(self, tmp_db):
        """config/regime_param_overlays.json must not drift from the DB."""
        db, export_path = tmp_db
        row_id = db.save_regime_param_overlay(
            strategy="mean_reversion",
            regime="ranging_calm",
            params={"rsi_oversold": 44.96},
            objective="sharpe_ratio",
            objective_value=1.42,
        )
        with open(export_path) as f:
            assert len(json.load(f)["active_overlays"]) == 1

        db.deactivate_regime_param_overlay(overlay_id=row_id)

        with open(export_path) as f:
            payload = json.load(f)
        assert payload["active_overlays"] == []


# ---------------------------------------------------------------------------
# 5. Whitelist enforcement
# ---------------------------------------------------------------------------


class TestWhitelist:
    def test_whitelist_derived_from_search_space(self):
        whitelist = get_param_whitelist("mean_reversion")
        assert "rsi_oversold" in whitelist
        assert "bb_std_dev" in whitelist
        assert "not_a_param" not in whitelist
        assert get_param_whitelist("unknown_strategy") == set()

    def test_non_whitelisted_key_ignored_with_warning(self):
        from loguru import logger as loguru_logger

        strategy = DummyStrategy()
        messages = []
        sink_id = loguru_logger.add(
            lambda m: messages.append(str(m)), level="WARNING"
        )
        try:
            applied = apply_params_to_strategy(
                strategy,
                "mean_reversion",
                {"rsi_oversold": 30.0, "malicious_attr": 999},
            )
        finally:
            loguru_logger.remove(sink_id)

        assert applied == {"rsi_oversold": 30.0}
        assert strategy.rsi_oversold == 30.0
        assert not hasattr(strategy, "malicious_attr")
        assert any("non-whitelisted" in m for m in messages)

    def test_whitelisted_but_missing_attribute_skipped(self):
        class Bare:
            pass

        applied = apply_params_to_strategy(
            Bare(), "mean_reversion", {"rsi_oversold": 30.0}
        )
        assert applied == {}


# ---------------------------------------------------------------------------
# 6. Runtime overlay manager
# ---------------------------------------------------------------------------


class TestRuntimeManager:
    # Rows carry the objective/objective_value a real stored overlay has:
    # the apply-side guard refuses anything that was not measured to win.
    OVERLAY_ROWS = [
        {
            "strategy": "mean_reversion",
            "regime": "ranging_calm",
            "params": {"rsi_oversold": 28.0, "bogus_key": 1.0},
            "objective": "sharpe_ratio",
            "objective_value": 1.31,
        }
    ]

    def _manager(self, sm, enabled=True):
        return RegimeParamOverlayManager(
            strategy_manager=sm,
            db=FakeOverlayDb(self.OVERLAY_ROWS),
            enabled=enabled,
            reference_symbol="SUI-USDC",
        )

    def test_applies_on_reference_symbol_regime_change(self):
        sm = DummyStrategyManager()
        mgr = self._manager(sm)

        mgr.handle_regime_changed(_event("SUI-USDC", "ranging_calm"))

        strategy = sm.strategies["MeanReversion"]
        assert strategy.rsi_oversold == 28.0
        # Non-whitelisted key from stored JSON never applied
        assert not hasattr(strategy, "bogus_key")
        status = mgr.get_status()
        assert status["current_regime"] == "ranging_calm"
        assert status["last_applied"]["mean_reversion"]["source"] == "overlay"

    def test_ignores_non_reference_symbols(self):
        sm = DummyStrategyManager()
        mgr = self._manager(sm)

        mgr.handle_regime_changed(_event("BTC-USDC", "ranging_calm"))

        assert sm.strategies["MeanReversion"].rsi_oversold == 35.0
        assert mgr.get_status()["current_regime"] is None

    def test_restores_baseline_when_no_overlay_for_new_regime(self):
        sm = DummyStrategyManager()
        mgr = self._manager(sm)
        strategy = sm.strategies["MeanReversion"]

        mgr.handle_regime_changed(_event("SUI-USDC", "ranging_calm"))
        assert strategy.rsi_oversold == 28.0

        mgr.handle_regime_changed(_event("SUI-USDC", "trending_strong"))
        assert strategy.rsi_oversold == 35.0  # baseline restored
        status = mgr.get_status()
        assert status["last_applied"]["mean_reversion"]["source"] == "baseline"

    def test_inert_when_flag_false(self):
        sm = DummyStrategyManager()
        mgr = self._manager(sm, enabled=False)

        assert mgr.get_status()["loaded_overlays"] == []
        mgr.handle_regime_changed(_event("SUI-USDC", "ranging_calm"))
        assert sm.strategies["MeanReversion"].rsi_oversold == 35.0
        assert mgr.apply_for_regime("ranging_calm") == {}

    def test_default_flag_is_off(self, monkeypatch):
        monkeypatch.delenv("ENABLE_REGIME_PARAM_OVERLAYS", raising=False)
        mgr = RegimeParamOverlayManager(
            strategy_manager=DummyStrategyManager(),
            db=FakeOverlayDb(self.OVERLAY_ROWS),
        )
        assert mgr.enabled is False


# ---------------------------------------------------------------------------
# 7. Helper coverage
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_strategy_name_resolution(self):
        assert resolve_strategy_display_name("mean_reversion") == "MeanReversion"
        assert resolve_strategy_display_name("MeanReversion") == "MeanReversion"
        assert resolve_strategy_display_name("nope") is None
        assert DISPLAY_TO_STRATEGY_KEY["GridTrading"] == "grid_trading"
        assert STRATEGY_KEY_TO_DISPLAY["vwap_scalping"] == "VWAPScalping"

    def test_normalize_regime_value(self):
        assert normalize_regime_value("RANGING_CALM") == "ranging_calm"
        assert normalize_regime_value("ranging_calm") == "ranging_calm"
        from trading_bot_v2.market_regime import MarketRegime

        assert (
            normalize_regime_value(MarketRegime.TRENDING_STRONG)
            == "trending_strong"
        )
        with pytest.raises(ValueError, match="Unknown regime"):
            normalize_regime_value("sideways")


# ---------------------------------------------------------------------------
# 8. Search-space reachability (regression guard)
# ---------------------------------------------------------------------------


class TestSearchSpaceReachability:
    """Every declared search-space parameter must reach the strategy.

    Overlays and optimizer overrides are applied by setattr, so a
    search-space key that does not resolve to an instance attribute is a
    SILENT NO-OP: the trial samples a value, the value is never read, and
    every sampled value scores identically. Eleven such parameters were
    found on 2026-07-28 (5 of liquidation_capture's 7, 3 of
    grid_trading's 5, 3 of orderbook_imbalance's 8) plus one phantom
    (vwap's sd_exit_threshold, which had no mechanism behind it at all).
    """

    @staticmethod
    def _live_strategies():
        from trading_bot_v2.risk_manager import RiskManager
        from trading_bot_v2.strategy_manager import StrategyManager

        return StrategyManager(risk_manager=RiskManager(), client=None)

    def test_every_search_space_param_resolves_to_an_attribute(self):
        from trading_bot_v2.optimization.search_spaces import (
            get_search_space,
            list_strategies,
        )
        from trading_bot_v2.regime_param_overlay import resolve_param_attr

        manager = self._live_strategies()
        unreachable = []
        for key in list_strategies():
            display = STRATEGY_KEY_TO_DISPLAY[key]
            instance = manager.strategies.get(display)
            if instance is None:
                continue
            for param in get_search_space(key):
                attr = resolve_param_attr(key, param)
                if not hasattr(instance, attr):
                    unreachable.append(f"{key}.{param} -> {attr}")

        assert not unreachable, (
            "search-space parameters that never reach the strategy "
            "instance (silent no-ops): " + ", ".join(sorted(unreachable))
        )

    def test_aliases_only_declare_real_renames(self):
        from trading_bot_v2.optimization.search_spaces import get_search_space
        from trading_bot_v2.regime_param_overlay import PARAM_ATTR_ALIASES

        for key, aliases in PARAM_ATTR_ALIASES.items():
            space = set(get_search_space(key))
            stale = sorted(set(aliases) - space)
            assert not stale, (
                f"PARAM_ATTR_ALIASES[{key}] maps parameters that are no "
                f"longer in the search space: {stale}"
            )

    def test_alias_is_applied_on_setattr(self):
        class Grid:
            grid_spacing_multiplier = 0.4

        applied = apply_params_to_strategy(
            Grid(), "grid_trading", {"grid_spacing_atr_multiplier": 0.7},
            check_feasibility=False,
        )
        assert applied == {"grid_spacing_atr_multiplier": 0.7}


# ---------------------------------------------------------------------------
# 9. Overlay feasibility guard
# ---------------------------------------------------------------------------


class TestOverlayFeasibility:
    """setattr bypasses the repairs that strategy __init__ applies.

    momentum_scalping raises its ATR target when the implied (constant)
    RRR falls below min_rrr; an overlay writing the raw values skips that
    and every signal would then fail rrr_meets_minimum. The application
    is rolled back instead.
    """

    class Momentum:
        def __init__(self):
            self.atr_stop_mult = 1.5
            self.atr_target_mult = 3.0
            self.min_rrr = 1.5
            self.ema_fast = 9
            self.ema_slow = 21
            self.rsi_lower = 30.0
            self.rsi_upper = 70.0
            self.volume_threshold = 1.2
            self.min_confidence = 0.5

    def test_infeasible_combination_is_rolled_back(self):
        strategy = self.Momentum()
        applied = apply_params_to_strategy(
            strategy,
            "momentum_scalping",
            {"atr_stop_mult": 2.5, "atr_target_mult": 3.0},
        )
        assert applied == {}
        assert strategy.atr_stop_mult == 1.5
        assert strategy.atr_target_mult == 3.0

    def test_feasible_combination_is_applied(self):
        strategy = self.Momentum()
        applied = apply_params_to_strategy(
            strategy,
            "momentum_scalping",
            {"atr_stop_mult": 2.0, "atr_target_mult": 4.0},
        )
        assert applied == {
            "atr_stop_mult": 2.0,
            "atr_target_mult": 4.0,
        }
        assert strategy.atr_stop_mult == 2.0


# ---------------------------------------------------------------------------
# 10. Derived regime min-trades
# ---------------------------------------------------------------------------


class TestRegimeMinTrades:
    def test_defaults_to_the_gate_derived_requirement(self, monkeypatch):
        from trading_bot_v2.optimization.optuna_runner import (
            regime_opt_min_trades,
        )
        from trading_bot_v2.validation.gate import load_gate_policy

        monkeypatch.delenv("REGIME_OPT_MIN_TRADES", raising=False)
        assert (
            regime_opt_min_trades()
            == load_gate_policy()["min_closed_trades"]
        )

    def test_env_override_wins(self, monkeypatch):
        from trading_bot_v2.optimization.optuna_runner import (
            regime_opt_min_trades,
        )

        monkeypatch.setenv("REGIME_OPT_MIN_TRADES", "7")
        assert regime_opt_min_trades() == 7

    def test_derived_requirement_exceeds_the_legacy_literal(self):
        from trading_bot_v2.optimization.optuna_runner import (
            DEFAULT_REGIME_OPT_MIN_TRADES,
            regime_opt_min_trades,
        )

        # The legacy 15 was more permissive than the gate the result is
        # later judged by; that gap is what this change closes.
        assert regime_opt_min_trades() > DEFAULT_REGIME_OPT_MIN_TRADES


# ---------------------------------------------------------------------------
# 11. Regime census
# ---------------------------------------------------------------------------


class TestRegimeCensus:
    REQ = {"pooled": 33, "per_symbol": 5, "min_symbols": 2}

    def test_cell_with_enough_pooled_and_symbols_is_tunable(self):
        from trading_bot_v2.validation.regime_census import classify_cell

        cell = {"trades": 106, "by_symbol": {"BTC": 36, "ETH": 29, "SUI": 41}}
        assert classify_cell(cell, self.REQ) == "tunable"

    def test_cell_below_pooled_requirement_is_thin(self):
        from trading_bot_v2.validation.regime_census import classify_cell

        cell = {"trades": 25, "by_symbol": {"BTC": 7, "ETH": 11, "SUI": 7}}
        assert classify_cell(cell, self.REQ) == "thin"

    def test_cell_on_one_symbol_only_is_thin(self):
        from trading_bot_v2.validation.regime_census import classify_cell

        cell = {"trades": 90, "by_symbol": {"BTC": 88, "ETH": 2}}
        assert classify_cell(cell, self.REQ) == "thin"

    def test_untraded_cell_is_none(self):
        from trading_bot_v2.validation.regime_census import classify_cell

        assert classify_cell({"trades": 0, "by_symbol": {}}, self.REQ) == "none"

    def test_requirements_come_from_the_gate_policy(self):
        from trading_bot_v2.validation.gate import load_gate_policy
        from trading_bot_v2.validation.regime_census import (
            derived_requirements,
        )

        policy = load_gate_policy()
        req = derived_requirements()
        assert req["pooled"] == policy["min_closed_trades"]
        assert req["per_symbol"] == policy["min_trades_per_symbol"]

    def test_regime_columns_cover_every_market_regime(self):
        from trading_bot_v2.market_regime import MarketRegime
        from trading_bot_v2.validation.regime_census import regime_columns

        assert set(regime_columns()) == {r.value for r in MarketRegime}

    def test_table_marks_tunable_and_thin_cells(self):
        from trading_bot_v2.validation.regime_census import (
            format_census_table,
        )

        census = {
            "requirements": self.REQ,
            "symbols": ["BTC-USDC", "SUI-USDC"],
            "window_spec": "6x2mo@8y",
            "windows_by_symbol": {},
            "cells": {
                "momentum_scalping": {
                    "trending_strong": {
                        "trades": 106,
                        "wins": 51,
                        "pnl": 67.0,
                        "gross_win": 470.0,
                        "gross_loss": 403.0,
                        "by_symbol": {"BTC-USDC": 60, "SUI-USDC": 46},
                    },
                    "trending_moderate": {
                        "trades": 3,
                        "wins": 1,
                        "pnl": 0.3,
                        "gross_win": 10.0,
                        "gross_loss": 9.7,
                        "by_symbol": {"SUI-USDC": 3},
                    },
                }
            },
            "regime_bars": {},
            "elapsed_seconds": {"momentum_scalping": 70.0},
        }
        table = format_census_table(census)
        assert "106*" in table
        assert "3!" in table
        assert "1 tunable cell(s)" in table


# ---------------------------------------------------------------------------
# 12. Apply-side losing-overlay guard (second, independent guard)
# ---------------------------------------------------------------------------


class TestApplySideOverlayGuard:
    """A losing overlay must never reach a running strategy.

    The write guard in save_regime_param_overlay only sees rows written
    through the save API. It is blind to a database restored from
    backups/*.db (restore_backup is a plain shutil.copy2), a hand-edited
    or externally supplied file, and any row written before it existed -
    all eleven snapshots under backups/ still carry the -1.486 Sharpe
    mean_reversion/ranging_calm overlay with active=1. So the decision is
    taken again at the point of use.
    """

    @pytest.fixture(autouse=True)
    def _no_env_opt_in(self, monkeypatch):
        monkeypatch.delenv("ALLOW_LOSING_REGIME_OVERLAYS", raising=False)

    @staticmethod
    def _row(**overrides):
        row = {
            "strategy": "mean_reversion",
            "regime": "ranging_calm",
            "params": {"rsi_oversold": 28.0},
            "objective": "sharpe_ratio",
            "objective_value": 1.2,
        }
        row.update(overrides)
        return row

    def _manager(self, rows, sm=None):
        sm = sm or DummyStrategyManager()
        mgr = RegimeParamOverlayManager(
            strategy_manager=sm,
            db=FakeOverlayDb(rows),
            enabled=True,
            reference_symbol="SUI-USDC",
        )
        return mgr, sm

    def test_winning_overlay_is_applied(self):
        mgr, sm = self._manager([self._row()])
        mgr.apply_for_regime("ranging_calm")
        assert sm.strategies["MeanReversion"].rsi_oversold == 28.0
        assert mgr.get_status()["refused_overlays"] == []

    def test_losing_overlay_is_refused_and_logged(self):
        """The exact shape of the row that shipped active on 2026-07-20."""
        from loguru import logger as loguru_logger

        rows = [self._row(objective_value=-1.4860599305891151)]
        messages = []
        sink_id = loguru_logger.add(
            lambda m: messages.append(str(m)), level="ERROR"
        )
        try:
            mgr, sm = self._manager(rows)
        finally:
            loguru_logger.remove(sink_id)

        # Not applied - the strategy keeps its normal parameters.
        assert sm.strategies["MeanReversion"].rsi_oversold == 35.0
        assert mgr.apply_for_regime("ranging_calm") == {}
        assert sm.strategies["MeanReversion"].rsi_oversold == 35.0

        refused = mgr.get_status()["refused_overlays"]
        assert len(refused) == 1
        assert refused[0]["strategy"] == "mean_reversion"
        assert refused[0]["regime"] == "ranging_calm"
        assert "break-even" in refused[0]["reason"]

        text = "".join(messages)
        assert "REFUSED" in text
        assert "mean_reversion" in text
        assert "ranging_calm" in text
        assert "sharpe_ratio" in text
        assert "-1.486" in text

    def test_exactly_break_even_is_refused(self):
        """Break-even is not an edge; the write guard refuses it too."""
        mgr, sm = self._manager([self._row(objective_value=0.0)])
        assert mgr.apply_for_regime("ranging_calm") == {}
        assert sm.strategies["MeanReversion"].rsi_oversold == 35.0

    def test_profit_factor_break_even_is_one(self):
        losing, sm_losing = self._manager(
            [self._row(objective="profit_factor", objective_value=0.95)]
        )
        assert losing.apply_for_regime("ranging_calm") == {}
        assert sm_losing.strategies["MeanReversion"].rsi_oversold == 35.0

        winning, sm_winning = self._manager(
            [self._row(objective="profit_factor", objective_value=1.4)]
        )
        winning.apply_for_regime("ranging_calm")
        assert sm_winning.strategies["MeanReversion"].rsi_oversold == 28.0

    def test_null_objective_value_is_refused(self):
        """Unknown is not the same as good - unscored rows are refused.

        The write guard is permissive about a missing score because it
        cannot judge a row it was handed without one; applying such a
        row is a different question, and an unscored overlay is exactly
        the shape of a row that never went through a measured write.
        """
        mgr, sm = self._manager([self._row(objective_value=None)])
        assert mgr.apply_for_regime("ranging_calm") == {}
        assert sm.strategies["MeanReversion"].rsi_oversold == 35.0
        reason = mgr.get_status()["refused_overlays"][0]["reason"]
        assert "no recorded objective value" in reason

    def test_non_numeric_objective_value_is_refused(self):
        mgr, sm = self._manager([self._row(objective_value="excellent")])
        assert mgr.apply_for_regime("ranging_calm") == {}
        assert sm.strategies["MeanReversion"].rsi_oversold == 35.0

    def test_null_or_unknown_objective_uses_zero_break_even(self):
        """Same treatment the write guard gives an unnamed objective."""
        for objective in (None, "mystery_metric"):
            losing, sm_losing = self._manager(
                [self._row(objective=objective, objective_value=-0.25)]
            )
            assert losing.apply_for_regime("ranging_calm") == {}
            assert sm_losing.strategies["MeanReversion"].rsi_oversold == 35.0

            winning, sm_winning = self._manager(
                [self._row(objective=objective, objective_value=0.25)]
            )
            winning.apply_for_regime("ranging_calm")
            assert sm_winning.strategies["MeanReversion"].rsi_oversold == 28.0

    def test_apply_side_agrees_with_write_guard(self, tmp_db):
        """Anything the write guard refuses, the apply guard refuses."""
        from trading_bot_v2.database import LosingOverlayRefused
        from trading_bot_v2.regime_param_overlay import refuse_overlay_reason

        db, _ = tmp_db
        cases = [
            ("sharpe_ratio", -1.49),
            ("sharpe_ratio", 0.0),
            ("sharpe", -0.01),
            ("profit_factor", 1.0),
            ("pf", 0.5),
            (None, -0.3),
            ("mystery_metric", 0.0),
        ]
        for objective, value in cases:
            with pytest.raises(LosingOverlayRefused):
                db.save_regime_param_overlay(
                    strategy="mean_reversion",
                    regime="ranging_calm",
                    params={"rsi_oversold": 28.0},
                    objective=objective,
                    objective_value=value,
                )
            assert (
                refuse_overlay_reason(
                    "mean_reversion", "ranging_calm", objective, value
                )
                is not None
            ), f"apply guard accepted what the write guard refused: {objective}={value}"

    def test_refusal_does_not_raise_into_the_trading_loop(self):
        """A bad stored row must not take the bot down."""
        mgr, sm = self._manager([self._row(objective_value=-1.0)])
        mgr.handle_regime_changed(_event("SUI-USDC", "ranging_calm"))
        assert sm.strategies["MeanReversion"].rsi_oversold == 35.0

    def test_env_opt_in_applies_a_losing_overlay(self, monkeypatch):
        monkeypatch.setenv("ALLOW_LOSING_REGIME_OVERLAYS", "true")
        mgr, sm = self._manager([self._row(objective_value=-1.0)])
        mgr.apply_for_regime("ranging_calm")
        assert sm.strategies["MeanReversion"].rsi_oversold == 28.0

    def test_overlay_injected_past_reload_is_still_refused(self):
        """_overlays populated by anything but reload() is unscored."""
        mgr, sm = self._manager([])
        mgr._overlays[("mean_reversion", "ranging_calm")] = {"rsi_oversold": 28.0}
        assert mgr.apply_for_regime("ranging_calm") == {}
        assert sm.strategies["MeanReversion"].rsi_oversold == 35.0

    def test_previously_applied_strategy_is_restored_on_refusal(self):
        """A refusal behaves exactly like 'no overlay for this regime'."""
        rows = [
            self._row(regime="ranging_calm", objective_value=1.2),
            self._row(
                regime="ranging_volatile",
                params={"rsi_oversold": 20.0},
                objective_value=-2.0,
            ),
        ]
        mgr, sm = self._manager(rows)
        strategy = sm.strategies["MeanReversion"]

        mgr.apply_for_regime("ranging_calm")
        assert strategy.rsi_oversold == 28.0

        mgr.apply_for_regime("ranging_volatile")
        assert strategy.rsi_oversold == 35.0  # baseline, not the losing 20.0

    def test_row_inserted_straight_into_the_db_is_refused(self, tmp_db):
        """THE case this guard exists for: a restored backup.

        The row is INSERTed with raw SQL, bypassing
        save_regime_param_overlay entirely - which is what
        restore_backup's shutil.copy2 of a backups/*.db snapshot amounts
        to. The write guard never sees it; the apply guard must.
        """
        import trading_bot_v2.database as db_mod

        db, _ = tmp_db
        with db_mod.get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO regime_param_overlays
                    (strategy, regime, params_json, objective,
                     objective_value, trade_count, study_name, active,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    "mean_reversion",
                    "ranging_calm",
                    json.dumps({"rsi_oversold": 28.0}),
                    "sharpe_ratio",
                    -1.4860599305891151,
                    246,
                    "restored_from_backup",
                    "2026-07-20T07:18:28.256336",
                ),
            )
            conn.commit()

        # The row really is active and readable - the write guard is gone.
        stored = db.get_regime_param_overlays(active_only=True)
        assert len(stored) == 1
        assert stored[0]["objective_value"] == pytest.approx(-1.4860599305891151)

        sm = DummyStrategyManager()
        mgr = RegimeParamOverlayManager(
            strategy_manager=sm,
            db=db,
            enabled=True,
            reference_symbol="SUI-USDC",
        )
        assert mgr.get_status()["loaded_overlays"] == []
        assert len(mgr.get_status()["refused_overlays"]) == 1

        mgr.handle_regime_changed(_event("SUI-USDC", "ranging_calm"))
        assert sm.strategies["MeanReversion"].rsi_oversold == 35.0


class TestOverlayQualityRulesAreShared:
    """One copy of the break-even rules, reachable without a cycle."""

    def test_database_reexports_the_shared_helpers(self):
        import trading_bot_v2.database as db_mod
        import trading_bot_v2.overlay_quality as oq

        assert db_mod.LosingOverlayRefused is oq.LosingOverlayRefused
        assert db_mod.overlay_break_even is oq.overlay_break_even
        assert (
            db_mod.OVERLAY_BREAK_EVEN_BY_OBJECTIVE
            is oq.OVERLAY_BREAK_EVEN_BY_OBJECTIVE
        )

    def test_apply_guard_uses_the_same_rules(self):
        import trading_bot_v2.overlay_quality as oq
        import trading_bot_v2.regime_param_overlay as rpo

        assert rpo.overlay_rejection_reason is oq.overlay_rejection_reason

    def test_overlay_quality_has_no_package_dependencies(self):
        """It must stay a leaf, or the cycle it dodges comes back."""
        import ast
        import pathlib

        import trading_bot_v2.overlay_quality as oq

        source = pathlib.Path(oq.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        relative = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level > 0
        ]
        assert not relative, (
            "overlay_quality must import nothing from the package - both "
            "database.py and regime_param_overlay.py depend on it"
        )

    def test_objective_aliases_agree_with_the_optimizer(self):
        """The mirrored alias table must not contradict optuna_runner."""
        from trading_bot_v2.optimization.optuna_runner import OBJECTIVE_ALIASES
        from trading_bot_v2.overlay_quality import OVERLAY_OBJECTIVE_ALIASES

        for short, canonical in OBJECTIVE_ALIASES.items():
            if short in OVERLAY_OBJECTIVE_ALIASES:
                assert OVERLAY_OBJECTIVE_ALIASES[short] == canonical, (
                    f"objective alias {short!r} means {canonical!r} to the "
                    f"optimizer but "
                    f"{OVERLAY_OBJECTIVE_ALIASES[short]!r} to the overlay "
                    f"guard"
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
