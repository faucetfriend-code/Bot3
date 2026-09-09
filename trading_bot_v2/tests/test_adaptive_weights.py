"""
Tests for adaptive per-regime strategy weights (Jul 2026).

Covers:
- Blend math (0.7 recent + 0.3 lifetime) on known numbers
- Recency weighting favoring recent winners over historical winners
- Min-trades evidence gate returning neutral 1.0
- Multiplier clamping at both ends
- Disabled flag / no-db neutrality
- Snapshot round-trip through a temporary SQLite database
- StrategyManager combine using static * adaptive weight products
"""

import os
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from trading_bot_v2.adaptive_weights import (
    AdaptiveWeightManager,
    _normalize_regime,
    _normalize_strategy,
)
from trading_bot_v2.market_regime import MarketRegime

NOW = datetime(2026, 7, 19, 12, 0, 0)


class FakeDb:
    """Duck-typed db exposing the two methods the manager needs."""

    def __init__(self, trades):
        self.trades = trades
        self.snapshots = []

    def get_closed_trades_for_weights(self):
        return self.trades

    def save_adaptive_weight_snapshot(self, rows):
        self.snapshots.append(rows)
        return len(rows)


def _trade(strategy, regime, pnl_pct, age_days, entry_price=100.0, qty=1.0):
    """Closed trade whose pnl is pnl_pct percent of notional, aged age_days."""
    notional = entry_price * qty
    exit_time = (NOW - timedelta(days=age_days)).isoformat()
    return {
        "strategy": strategy,
        "regime": regime,
        "pnl": notional * pnl_pct / 100.0,
        "entry_price": entry_price,
        "quantity": qty,
        "entry_time": exit_time,
        "exit_time": exit_time,
    }


def _manager(trades, **overrides):
    """Manager with a fixed clock and low min_trades unless overridden."""
    params = dict(
        db=FakeDb(trades),
        enabled=True,
        halflife_days=14.0,
        min_trades=1,
        refresh_minutes=60.0,
        scale=2.0,
        min_mult=0.5,
        max_mult=1.5,
    )
    params.update(overrides)
    mgr = AdaptiveWeightManager(**params)
    mgr._now = lambda: NOW
    return mgr


# ============================================================
# 1. Normalization helpers
# ============================================================


class TestNormalization:
    def test_strategy_variants_collapse(self):
        assert _normalize_strategy("MOMENTUM_SCALPING") == "momentumscalping"
        assert _normalize_strategy("MomentumScalping") == "momentumscalping"
        assert _normalize_strategy("momentum scalping") == "momentumscalping"

    def test_regime_enum_and_string(self):
        assert _normalize_regime(MarketRegime.RANGING_CALM) == "ranging_calm"
        assert _normalize_regime("RANGING_CALM") == "ranging_calm"


# ============================================================
# 2. Scoring math
# ============================================================


class TestScoringMath:
    def test_blend_math_on_known_numbers(self):
        # Two trades, both age 0 (weight 1.0): recent == lifetime == mean
        trades = [
            _trade("MEAN_REVERSION", "ranging_calm", 2.0, 0),
            _trade("MEAN_REVERSION", "ranging_calm", 0.0, 0),
        ]
        mgr = _manager(trades)
        mgr.refresh(force=True)

        cell = mgr._cells[("ranging_calm", "meanreversion")]
        assert cell["recent_expectancy"] == pytest.approx(1.0)
        assert cell["lifetime_expectancy"] == pytest.approx(1.0)
        # score = 0.7*1 + 0.3*1 = 1.0; slope = (1.5-0.5)/(2*2) = 0.25
        # mult = 1 + 1.0 * 0.25 = 1.25
        assert cell["multiplier"] == pytest.approx(1.25)

    def test_blend_math_with_aged_trades(self):
        # One fresh winner (+2%), one loser exactly one half-life old (-2%).
        # weights: 1.0 and 0.5
        # recent = (2*1 + (-2)*0.5) / 1.5 = 1/1.5 = 0.6667
        # lifetime = 0.0
        # score = 0.7*0.6667 = 0.46667 -> mult = 1 + 0.46667*0.25 = 1.11667
        trades = [
            _trade("MEAN_REVERSION", "ranging_calm", 2.0, 0),
            _trade("MEAN_REVERSION", "ranging_calm", -2.0, 14),
        ]
        mgr = _manager(trades)
        mgr.refresh(force=True)

        cell = mgr._cells[("ranging_calm", "meanreversion")]
        assert cell["recent_expectancy"] == pytest.approx(2.0 / 3.0, abs=1e-6)
        assert cell["lifetime_expectancy"] == pytest.approx(0.0)
        assert cell["multiplier"] == pytest.approx(
            1.0 + (0.7 * 2.0 / 3.0) * 0.25, abs=1e-6
        )

    def test_recency_favors_recent_winner_over_historical_winner(self):
        # StrategyA: wins are recent, losses are old.
        # StrategyB: wins are old, losses are recent. Same lifetime stats.
        trades = []
        for _ in range(5):
            trades.append(_trade("STRATEGY_A", "ranging_calm", 1.0, 1))
            trades.append(_trade("STRATEGY_A", "ranging_calm", -1.0, 60))
            trades.append(_trade("STRATEGY_B", "ranging_calm", 1.0, 60))
            trades.append(_trade("STRATEGY_B", "ranging_calm", -1.0, 1))
        mgr = _manager(trades)
        mgr.refresh(force=True)

        mult_a = mgr.get_multiplier("ranging_calm", "STRATEGY_A")
        mult_b = mgr.get_multiplier("ranging_calm", "STRATEGY_B")
        assert mult_a > 1.0 > mult_b

        cell_a = mgr._cells[("ranging_calm", "strategya")]
        cell_b = mgr._cells[("ranging_calm", "strategyb")]
        assert cell_a["lifetime_expectancy"] == pytest.approx(
            cell_b["lifetime_expectancy"]
        )

    def test_clamping_at_both_ends(self):
        trades = [_trade("BIG_WINNER", "ranging_calm", 50.0, 0) for _ in range(5)] + [
            _trade("BIG_LOSER", "ranging_calm", -50.0, 0) for _ in range(5)
        ]
        mgr = _manager(trades)
        mgr.refresh(force=True)

        assert mgr.get_multiplier("ranging_calm", "BIG_WINNER") == pytest.approx(1.5)
        assert mgr.get_multiplier("ranging_calm", "BIG_LOSER") == pytest.approx(0.5)

    def test_scale_saturation_point(self):
        # score == scale saturates the clamp exactly:
        # all trades +2% fresh -> score 2.0 -> mult = 1 + 2*0.25 = 1.5
        trades = [_trade("EDGE", "ranging_calm", 2.0, 0) for _ in range(3)]
        mgr = _manager(trades)
        mgr.refresh(force=True)
        assert mgr.get_multiplier("ranging_calm", "EDGE") == pytest.approx(1.5)


# ============================================================
# 3. Gates and neutrality
# ============================================================


class TestGates:
    def test_min_trades_gate_returns_neutral(self):
        trades = [_trade("MEAN_REVERSION", "ranging_calm", 5.0, 0)] * 9
        mgr = _manager(trades, min_trades=10)
        mgr.refresh(force=True)
        assert mgr.get_multiplier("ranging_calm", "MeanReversion") == 1.0
        # Evidence is still recorded for observability
        cell = mgr._cells[("ranging_calm", "meanreversion")]
        assert cell["trade_count"] == 9
        assert cell["multiplier"] == 1.0

    def test_min_trades_gate_opens_at_threshold(self):
        trades = [_trade("MEAN_REVERSION", "ranging_calm", 5.0, 0)] * 10
        mgr = _manager(trades, min_trades=10)
        mgr.refresh(force=True)
        assert mgr.get_multiplier("ranging_calm", "MeanReversion") > 1.0

    def test_disabled_flag_returns_all_neutral(self):
        trades = [_trade("MEAN_REVERSION", "ranging_calm", 50.0, 0)] * 20
        mgr = _manager(trades, enabled=False)
        assert mgr.get_multiplier("ranging_calm", "MeanReversion") == 1.0
        assert mgr.refresh(force=True) is False

    def test_no_db_returns_neutral(self):
        mgr = AdaptiveWeightManager(db=None, enabled=True)
        assert mgr.get_multiplier("ranging_calm", "MeanReversion") == 1.0

    def test_unknown_cell_returns_neutral(self):
        mgr = _manager([_trade("MEAN_REVERSION", "ranging_calm", 5.0, 0)] * 5)
        mgr.refresh(force=True)
        assert mgr.get_multiplier("trending_strong", "MACrossover") == 1.0

    def test_broken_db_is_failure_safe(self):
        db = MagicMock()
        db.get_closed_trades_for_weights.side_effect = RuntimeError("db down")
        mgr = AdaptiveWeightManager(db=db, enabled=True, min_trades=1)
        assert mgr.get_multiplier("ranging_calm", "MeanReversion") == 1.0

    def test_refresh_caching_respects_interval(self):
        trades = [_trade("MEAN_REVERSION", "ranging_calm", 5.0, 0)] * 5
        mgr = _manager(trades, refresh_minutes=60.0)
        fake_time = [0.0]
        mgr._monotonic = lambda: fake_time[0]

        assert mgr.refresh() is True  # first compute
        assert mgr.refresh() is False  # fresh cache
        fake_time[0] += 61 * 60
        assert mgr.refresh() is True  # stale again


# ============================================================
# 4. Snapshot persistence
# ============================================================


@pytest.fixture
def temp_db():
    """Point trading_bot_v2.database at a temporary SQLite file."""
    import trading_bot_v2.database as db_mod

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()

    original_env = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = tmp_path
    os.environ["DATABASE_BACKEND"] = "sqlite"

    original_backend = db_mod._active_backend
    original_path = db_mod.DATABASE_PATH
    original_pool = db_mod._connection_pool
    db_mod._active_backend = "sqlite"
    db_mod.DATABASE_PATH = tmp_path
    pool = db_mod.ConnectionPool(max_connections=2)
    db_mod._connection_pool = pool

    db_mod.init_database()

    yield db_mod

    pool.close_all()
    db_mod._active_backend = original_backend
    db_mod.DATABASE_PATH = original_path
    db_mod._connection_pool = original_pool
    if original_env is not None:
        os.environ["DATABASE_PATH"] = original_env
    elif "DATABASE_PATH" in os.environ:
        del os.environ["DATABASE_PATH"]
    try:
        os.unlink(tmp_path)
    except OSError:
        pass


class TestSnapshotRoundTrip:
    def test_snapshot_round_trip_to_temp_db(self, temp_db):
        dbm = temp_db.DatabaseManager()

        # Seed closed trades: 3 winners for MeanReversion in ranging_calm
        for i in range(3):
            dbm.save_trade(
                {
                    "symbol": "SUI-PERP",
                    "side": "BUY",
                    "quantity": 1.0,
                    "entry_price": 100.0,
                    "entry_time": NOW.isoformat(),
                    "exit_time": NOW.isoformat(),
                    "pnl": 2.0,  # +2% of 100 notional
                    "strategy": "MEAN_REVERSION",
                    "status": "closed",
                    "regime": "ranging_calm",
                }
            )

        mgr = AdaptiveWeightManager(
            db=dbm,
            enabled=True,
            halflife_days=14.0,
            min_trades=1,
            refresh_minutes=60.0,
            scale=2.0,
            min_mult=0.5,
            max_mult=1.5,
        )
        mgr._now = lambda: NOW
        assert mgr.refresh(force=True) is True

        # Multiplier computed from the seeded trades:
        # +2 pnl-pct expectancy -> score 2.0 -> saturates at 1.5
        assert mgr.get_multiplier(
            MarketRegime.RANGING_CALM, "MeanReversion"
        ) == pytest.approx(1.5)

        # Snapshot persisted and readable
        rows = dbm.get_adaptive_weight_snapshot()
        assert len(rows) == 1
        row = rows[0]
        assert row["regime"] == "ranging_calm"
        assert row["strategy"] == "meanreversion"
        assert row["trade_count"] == 3
        assert row["multiplier"] == pytest.approx(1.5)
        assert row["recent_expectancy"] == pytest.approx(2.0)
        assert row["lifetime_expectancy"] == pytest.approx(2.0)

    def test_get_closed_trades_for_weights_shape(self, temp_db):
        dbm = temp_db.DatabaseManager()
        dbm.save_trade(
            {
                "symbol": "SUI-PERP",
                "side": "SELL",
                "quantity": 2.0,
                "entry_price": 50.0,
                "entry_time": NOW.isoformat(),
                "exit_time": NOW.isoformat(),
                "pnl": -1.0,
                "strategy": "MOMENTUM_SCALPING",
                "status": "closed",
                "regime": "trending_strong",
            }
        )
        dbm.save_trade(
            {
                "symbol": "SUI-PERP",
                "side": "BUY",
                "quantity": 1.0,
                "entry_price": 10.0,
                "entry_time": NOW.isoformat(),
                "strategy": "MEAN_REVERSION",
                "status": "open",  # Excluded
            }
        )
        trades = dbm.get_closed_trades_for_weights()
        assert len(trades) == 1
        assert trades[0]["strategy"] == "MOMENTUM_SCALPING"
        assert trades[0]["regime"] == "trending_strong"
        assert trades[0]["pnl"] == pytest.approx(-1.0)


# ============================================================
# 5. StrategyManager integration
# ============================================================


class TestStrategyManagerIntegration:
    def _make_sm(self, static_weights, multipliers):
        """StrategyManager with mocked detector and adaptive manager."""
        from trading_bot_v2.strategy_manager import StrategyManager

        detector = MagicMock()
        detector.get_strategy_weights.return_value = static_weights

        sm = StrategyManager(
            regime_detector=detector,
            enable_mean_reversion=False,
            enable_ma_crossover=False,
            enable_grid_trading=False,
            enable_liquidation_capture=False,
            enable_vwap_scalping=False,
            enable_funding_arb=False,
            enable_momentum_scalping=False,
            enable_orderbook_imbalance=False,
            enable_session_range_breakout=False,
        )

        adaptive = MagicMock()
        adaptive.enabled = True
        adaptive.get_multiplier.side_effect = lambda regime, strategy: multipliers.get(
            strategy, 1.0
        )
        sm.adaptive_weights = adaptive
        return sm

    def test_combine_uses_static_times_adaptive_product(self):
        sm = self._make_sm(
            static_weights={"MeanReversion": 0.6, "VWAPScalping": 0.4},
            multipliers={"MeanReversion": 1.5, "VWAPScalping": 0.5},
        )
        effective = sm._apply_adaptive_weights(
            {"MeanReversion": 0.6, "VWAPScalping": 0.4},
            MarketRegime.RANGING_CALM,
        )
        assert effective["MeanReversion"] == pytest.approx(0.6 * 1.5)
        assert effective["VWAPScalping"] == pytest.approx(0.4 * 0.5)

    def test_combine_signals_weighted_confidence_shifts(self):
        from trading_bot_v2.tests.test_signal_routing import _make_signal
        from trading_bot_v2.config import StrategyType
        from trading_bot_v2.models import OrderSide

        sig_mr = _make_signal(
            strategy=StrategyType.MEAN_REVERSION,
            side=OrderSide.BUY,
            confidence=0.9,
        )
        sig_vwap = _make_signal(
            strategy=StrategyType.VWAP_SCALPING,
            side=OrderSide.BUY,
            confidence=0.5,
        )

        static = {"MeanReversion": 0.5, "VWAPScalping": 0.5}
        # Neutral multipliers: equal weights -> confidence 0.7
        sm_neutral = self._make_sm(static, {})
        combined_neutral = sm_neutral._combine_signals(
            [sig_mr, sig_vwap], MarketRegime.RANGING_CALM
        )
        assert combined_neutral.confidence == pytest.approx(0.7)

        # Boost MeanReversion 1.5x, cut VWAP 0.5x:
        # weights 0.75 / 0.25 -> confidence 0.9*0.75 + 0.5*0.25 = 0.8
        sm_adaptive = self._make_sm(static, {"MeanReversion": 1.5, "VWAPScalping": 0.5})
        combined_adaptive = sm_adaptive._combine_signals(
            [sig_mr, sig_vwap], MarketRegime.RANGING_CALM
        )
        assert combined_adaptive.confidence == pytest.approx(0.8)

    def test_disabled_adaptive_manager_keeps_static_weights(self):
        sm = self._make_sm({"MeanReversion": 0.6}, {"MeanReversion": 1.5})
        sm.adaptive_weights.enabled = False
        effective = sm._apply_adaptive_weights(
            {"MeanReversion": 0.6}, MarketRegime.RANGING_CALM
        )
        assert effective == {"MeanReversion": 0.6}

    def test_missing_adaptive_manager_keeps_static_weights(self):
        sm = self._make_sm({"MeanReversion": 0.6}, {})
        sm.adaptive_weights = None
        effective = sm._apply_adaptive_weights(
            {"MeanReversion": 0.6}, MarketRegime.RANGING_CALM
        )
        assert effective == {"MeanReversion": 0.6}

    def test_no_db_strategy_manager_defaults_neutral(self):
        # StrategyManager constructed without db (unit-test/backtest path)
        # keeps static weights untouched via a neutral adaptive manager.
        from trading_bot_v2.strategy_manager import StrategyManager

        detector = MagicMock()
        sm = StrategyManager(
            regime_detector=detector,
            enable_mean_reversion=False,
            enable_ma_crossover=False,
            enable_grid_trading=False,
            enable_liquidation_capture=False,
            enable_vwap_scalping=False,
            enable_funding_arb=False,
            enable_momentum_scalping=False,
            enable_orderbook_imbalance=False,
            enable_session_range_breakout=False,
        )
        assert sm.adaptive_weights is not None
        assert sm.adaptive_weights.db is None
        static = {"MeanReversion": 0.6}
        assert sm._apply_adaptive_weights(static, MarketRegime.RANGING_CALM) == static
