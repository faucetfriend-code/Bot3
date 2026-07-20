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
    OVERLAY_ROWS = [
        {
            "strategy": "mean_reversion",
            "regime": "ranging_calm",
            "params": {"rsi_oversold": 28.0, "bogus_key": 1.0},
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
