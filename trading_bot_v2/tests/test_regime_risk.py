"""
Tests for regime-conditional risk modulation (Jul 2026).

Covers:
- Per-regime position size multipliers in RiskManager (enum/string/None
  regimes, env overrides)
- Regime-conditional minimum-confidence gate in StrategyManager
- Regime-flip position review (trailing arm, regime_exit close, log-only,
  exception safety, disabled flag)
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from trading_bot_v2.event_system import Event, EventType
from trading_bot_v2.market_regime import MarketRegime
from trading_bot_v2.regime_position_review import RegimePositionReviewer
from trading_bot_v2.risk_manager import RiskManager

# Imported at module level so config.py's load_dotenv(override=True) fires
# BEFORE tests monkeypatch environment variables (importing inside a test
# would clobber monkeypatched values with .env contents).
from trading_bot_v2.strategy_manager import StrategyManager


def _make_sizing_signal(entry_price=10.0, stop_loss=9.5):
    """Signal-like object with the attributes get_position_size reads."""
    return SimpleNamespace(
        asset="SUI-PERP",
        entry_price=entry_price,
        stop_loss=stop_loss,
        risk_profile="medium",
        strategy=SimpleNamespace(value="momentum_scalping"),
    )


# ============================================================
# 1. RiskManager per-regime size multipliers
# ============================================================


class TestRegimeSizeMultipliers:
    """RiskManager.get_position_size regime scaling."""

    def _base_size(self, rm, **kwargs):
        return rm.get_position_size(
            _make_sizing_signal(), account_balance=100_000.0,
            current_exposure=0.0, **kwargs,
        )

    def test_none_regime_is_neutral(self):
        rm = RiskManager()
        assert self._base_size(rm) == self._base_size(rm, regime=None)
        assert rm.get_regime_size_multiplier(None) == 1.0

    def test_multiplier_applied_per_regime(self):
        rm = RiskManager()
        base = self._base_size(rm)
        indecisive = self._base_size(rm, regime=MarketRegime.INDECISIVE)
        volatile = self._base_size(rm, regime=MarketRegime.RANGING_VOLATILE)
        strong = self._base_size(rm, regime=MarketRegime.TRENDING_STRONG)

        assert indecisive == pytest.approx(base * 0.4)
        assert volatile == pytest.approx(base * 0.6)
        assert strong == pytest.approx(base * 1.0)

    def test_string_regime_accepted(self):
        rm = RiskManager()
        base = self._base_size(rm)
        calm = self._base_size(rm, regime="ranging_calm")
        assert calm == pytest.approx(base * 0.85)

    def test_default_multiplier_table(self):
        rm = RiskManager()
        assert rm.get_regime_size_multiplier(MarketRegime.TRENDING_STRONG) == 1.0
        assert rm.get_regime_size_multiplier(MarketRegime.TRENDING_MODERATE) == 0.85
        assert rm.get_regime_size_multiplier(MarketRegime.RANGING_VOLATILE) == 0.6
        assert rm.get_regime_size_multiplier(MarketRegime.RANGING_CALM) == 0.85
        assert rm.get_regime_size_multiplier(MarketRegime.INDECISIVE) == 0.4

    def test_unknown_regime_is_neutral(self):
        rm = RiskManager()
        assert rm.get_regime_size_multiplier("no_such_regime") == 1.0

    def test_env_override_respected(self, monkeypatch):
        monkeypatch.setenv("REGIME_SIZE_MULT_INDECISIVE", "0.9")
        rm = RiskManager()
        assert rm.get_regime_size_multiplier(MarketRegime.INDECISIVE) == 0.9

    def test_invalid_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("REGIME_SIZE_MULT_RANGING_CALM", "not-a-float")
        rm = RiskManager()
        assert rm.get_regime_size_multiplier(MarketRegime.RANGING_CALM) == 0.85

    def test_minimum_quantity_floor_still_enforced(self):
        rm = RiskManager()
        # Tiny balance keeps quantities below the 1.0 floor
        sig = _make_sizing_signal(entry_price=100.0, stop_loss=95.0)
        qty = rm.get_position_size(
            sig, account_balance=10.0, current_exposure=0.0,
            regime=MarketRegime.INDECISIVE,
        )
        assert qty >= 1.0


# ============================================================
# 2. StrategyManager regime confidence gate
# ============================================================


def _make_gate_signal(confidence, strategy_value="mean_reversion"):
    return SimpleNamespace(
        confidence=confidence,
        asset="SUI-PERP",
        strategy=SimpleNamespace(value=strategy_value),
    )


def _make_sm():
    """StrategyManager with all strategies disabled and a mock detector."""
    return StrategyManager(
        regime_detector=MagicMock(),
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


class TestRegimeConfidenceGate:
    """StrategyManager._apply_regime_confidence_gate."""

    def test_drops_in_indecisive_passes_in_trending(self, monkeypatch):
        monkeypatch.setenv("MIN_SIGNAL_CONFIDENCE_FLOOR", "0.30")
        sm = _make_sm()
        sig = _make_gate_signal(confidence=0.32)

        # INDECISIVE threshold = 0.30 + 0.05 = 0.35 -> dropped
        assert sm._apply_regime_confidence_gate(
            [sig], MarketRegime.INDECISIVE
        ) == []
        # TRENDING_STRONG threshold = 0.30 + 0.0 -> kept
        assert sm._apply_regime_confidence_gate(
            [sig], MarketRegime.TRENDING_STRONG
        ) == [sig]

    def test_ranging_volatile_adjustment(self, monkeypatch):
        monkeypatch.setenv("MIN_SIGNAL_CONFIDENCE_FLOOR", "0.30")
        sm = _make_sm()
        sig = _make_gate_signal(confidence=0.33)
        assert sm._apply_regime_confidence_gate(
            [sig], MarketRegime.RANGING_VOLATILE
        ) == []
        assert sm._apply_regime_confidence_gate(
            [sig], MarketRegime.RANGING_CALM
        ) == [sig]

    def test_default_floor_is_transparent_for_normal_signals(self):
        sm = _make_sm()
        # Default floor 0.0 + adj 0.05: normal confidences all pass
        sig = _make_gate_signal(confidence=0.45)
        assert sm._apply_regime_confidence_gate(
            [sig], MarketRegime.INDECISIVE
        ) == [sig]

    def test_zero_threshold_short_circuits(self):
        sm = _make_sm()
        sm.min_signal_confidence_floor = 0.0
        sig = _make_gate_signal(confidence=0.01)
        # TRENDING_STRONG has adjustment 0.0 -> threshold 0 -> no filtering
        assert sm._apply_regime_confidence_gate(
            [sig], MarketRegime.TRENDING_STRONG
        ) == [sig]

    def test_env_adjustment_override(self, monkeypatch):
        monkeypatch.setenv("REGIME_CONF_ADJ_INDECISIVE", "0.40")
        sm = _make_sm()
        sig = _make_gate_signal(confidence=0.35)
        assert sm._apply_regime_confidence_gate(
            [sig], MarketRegime.INDECISIVE
        ) == []

    def test_mixed_signals_partial_drop(self, monkeypatch):
        monkeypatch.setenv("MIN_SIGNAL_CONFIDENCE_FLOOR", "0.30")
        sm = _make_sm()
        low = _make_gate_signal(confidence=0.31)
        high = _make_gate_signal(confidence=0.80)
        kept = sm._apply_regime_confidence_gate(
            [low, high], MarketRegime.INDECISIVE
        )
        assert kept == [high]


# ============================================================
# 3. Regime-flip position review
# ============================================================


def _make_reviewer(open_trades, trend="none", price=None, enabled=True):
    """Build a RegimePositionReviewer with fully mocked collaborators."""
    db = MagicMock()
    db.get_open_trades.return_value = open_trades

    client = MagicMock()
    if price is None:
        client.get_ticker.return_value = None
    else:
        client.get_ticker.return_value = {"last": price}

    detector = MagicMock()
    detector.get_active_strategies.return_value = ["MeanReversion", "GridTrading"]
    detector.get_trend_direction.return_value = trend

    mpm = MagicMock()
    mpm.risk_manager.get_migrated_positions.return_value = []

    fetcher = MagicMock()
    fetcher.get_candles_multi_tf.return_value = {
        "4h": {"close": [1.0] * 200, "high": [1.1] * 200, "low": [0.9] * 200}
    }

    reviewer = RegimePositionReviewer(
        db=db,
        client=client,
        regime_detector=detector,
        migrated_position_manager=mpm,
        multi_tf_fetcher=fetcher,
        enabled=enabled,
    )
    return reviewer, db, mpm


def _regime_event(symbol="SUI-PERP", new_regime="ranging_calm"):
    return Event(
        EventType.REGIME_CHANGED,
        {
            "symbol": symbol,
            "old_regime": "trending_strong",
            "new_regime": new_regime,
        },
        source="test",
    )


def _open_trade(strategy="MOMENTUM_SCALPING", side="BUY", entry=1.0, qty=2.0):
    return {
        "id": 1,
        "symbol": "SUI-PERP",
        "side": side,
        "quantity": qty,
        "entry_price": entry,
        "strategy": strategy,
        "regime": "trending_strong",
        "entry_time": "2026-07-01T00:00:00",
    }


class TestRegimePositionReview:
    """RegimePositionReviewer.handle_regime_changed."""

    def test_profitable_misaligned_position_arms_trailing_not_closed(self):
        # Long from 1.0, price 1.2 (profit), trend up (not against)
        reviewer, db, mpm = _make_reviewer(
            [_open_trade(side="BUY", entry=1.0)], trend="up", price=1.2
        )
        reviewer.handle_regime_changed(_regime_event())

        assert mpm.arm_trailing_stop.call_count == 1
        args = mpm.arm_trailing_stop.call_args[0]
        assert args[0] == "SUI-PERP"
        assert args[1]["side"] == "long"
        mpm.close_position.assert_not_called()
        # Position registered for trailing management
        mpm.risk_manager.register_migrated_position.assert_called_once()

    def test_against_trend_position_closed_with_regime_exit(self):
        # Long from 1.0, price 0.9 (loss), trend down -> against trend
        reviewer, db, mpm = _make_reviewer(
            [_open_trade(side="BUY", entry=1.0)], trend="down", price=0.9
        )
        reviewer.handle_regime_changed(_regime_event())

        mpm.arm_trailing_stop.assert_not_called()
        assert mpm.close_position.call_count == 1
        args = mpm.close_position.call_args[0]
        assert args[0] == "SUI-PERP"
        assert args[1]["side"] == "long"
        assert args[2] == "regime_exit"

    def test_short_against_up_trend_closed(self):
        reviewer, db, mpm = _make_reviewer(
            [_open_trade(side="SELL", entry=1.0)], trend="up", price=1.1
        )
        reviewer.handle_regime_changed(_regime_event())
        assert mpm.close_position.call_count == 1
        assert mpm.close_position.call_args[0][2] == "regime_exit"

    def test_not_profit_not_against_trend_logs_only(self):
        # Long losing, trend none -> log only
        reviewer, db, mpm = _make_reviewer(
            [_open_trade(side="BUY", entry=1.0)], trend="none", price=0.95
        )
        reviewer.handle_regime_changed(_regime_event())
        mpm.arm_trailing_stop.assert_not_called()
        mpm.close_position.assert_not_called()

    def test_active_strategy_position_untouched(self):
        # MeanReversion is active in the new regime -> skip
        reviewer, db, mpm = _make_reviewer(
            [_open_trade(strategy="MEAN_REVERSION")], trend="down", price=0.9
        )
        reviewer.handle_regime_changed(_regime_event())
        mpm.arm_trailing_stop.assert_not_called()
        mpm.close_position.assert_not_called()

    def test_overlay_strategy_position_untouched(self):
        reviewer, db, mpm = _make_reviewer(
            [_open_trade(strategy="LIQUIDATION_CAPTURE")], trend="down", price=0.9
        )
        reviewer.handle_regime_changed(_regime_event())
        mpm.close_position.assert_not_called()

    def test_grid_positions_skipped(self):
        reviewer, db, mpm = _make_reviewer(
            [_open_trade(strategy="GRID_TRADING", side="GRID")],
            trend="down",
            price=0.9,
        )
        reviewer.handle_regime_changed(_regime_event())
        mpm.close_position.assert_not_called()
        mpm.arm_trailing_stop.assert_not_called()

    def test_handler_survives_raising_position(self):
        # Two closable positions; the first close raises. The handler must
        # not propagate and must still process the second position.
        first = _open_trade(side="BUY", entry=1.0)
        second = dict(_open_trade(side="SELL", entry=1.0), id=2)
        reviewer, db, mpm = _make_reviewer(
            [first, second], trend="up", price=1.5
        )
        # First reviewed position is profitable long -> arm raises
        mpm.arm_trailing_stop.side_effect = [RuntimeError("boom"), True]

        reviewer.handle_regime_changed(_regime_event())  # Must not raise

        # Second position (short, price above entry, against up trend)
        # was still reviewed and closed
        assert mpm.close_position.call_count == 1
        assert mpm.close_position.call_args[0][2] == "regime_exit"

    def test_disabled_flag_no_ops(self):
        reviewer, db, mpm = _make_reviewer(
            [_open_trade()], trend="down", price=0.9, enabled=False
        )
        reviewer.handle_regime_changed(_regime_event())
        db.get_open_trades.assert_not_called()
        mpm.close_position.assert_not_called()

    def test_malformed_event_is_safe(self):
        reviewer, db, mpm = _make_reviewer([], trend="none", price=None)
        reviewer.handle_regime_changed(
            Event(EventType.REGIME_CHANGED, {"symbol": "SUI-PERP"}, "test")
        )
        reviewer.handle_regime_changed(
            Event(
                EventType.REGIME_CHANGED,
                {"symbol": "SUI-PERP", "new_regime": "bogus_regime"},
                "test",
            )
        )
        mpm.close_position.assert_not_called()

    def test_env_flag_default_true(self, monkeypatch):
        monkeypatch.delenv("ENABLE_REGIME_POSITION_REVIEW", raising=False)
        reviewer, _, _ = _make_reviewer([], enabled=None)
        assert reviewer.enabled is True

    def test_env_flag_false(self, monkeypatch):
        monkeypatch.setenv("ENABLE_REGIME_POSITION_REVIEW", "false")
        reviewer, _, _ = _make_reviewer([], enabled=None)
        assert reviewer.enabled is False
