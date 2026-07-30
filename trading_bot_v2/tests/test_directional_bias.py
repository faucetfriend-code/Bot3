"""
Tests for the directional bias module and its StrategyManager gate.

Covers: gate-mode validation, the combined-bias truth table, the trend
leg (EMA stack), the funding leg (percentile + sign guard + causality
cache), and the Step 3.4 gate wiring (off / log / enforce, exemptions).
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from trading_bot_v2.config import AssetClass, StrategyType, TradeQuality
from trading_bot_v2.directional_bias import (
    BIAS_LONG,
    BIAS_NEUTRAL,
    BIAS_SHORT,
    DEFAULT_GATE_MODE,
    GATE_ENFORCE,
    GATE_LOG,
    GATE_OFF,
    DirectionalBias,
    DirectionalBiasEngine,
    resolve_gate_exempt,
    validate_gate_mode,
)
from trading_bot_v2.models import OrderSide, Signal
from trading_bot_v2.strategy_manager import StrategyManager


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _tf_data(closes):
    return {
        "4h": {
            "open": list(closes),
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": list(closes),
            "volume": [100.0] * len(closes),
        }
    }


def _funding_client(rates):
    client = MagicMock()
    client.get_funding_history.return_value = [
        {"funding_time": f"2024-01-01T{i % 24:02d}:00:00", "funding_rate": r}
        for i, r in enumerate(rates)
    ]
    return client


def _signal(side=OrderSide.BUY, strategy=StrategyType.MOMENTUM_SCALPING):
    return Signal(
        strategy=strategy,
        asset="BTC-USDC",
        asset_class=AssetClass.PERPETUAL,
        side=side,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        confidence=0.7,
        quality=TradeQuality.STANDARD,
        volume_confirmation=True,
        multi_timeframe_alignment=True,
        support_resistance_valid=True,
        rrr_meets_minimum=True,
        liquidation_buffer_safe=True,
        account_risk_ok=True,
        margin_drawdown_ok=True,
        forbidden_conditions_clear=True,
    )


def _manager(monkeypatch, mode, client=None):
    monkeypatch.setenv("DIRECTIONAL_GATE", mode)
    return StrategyManager(
        regime_detector=MagicMock(),
        enable_mean_reversion=False,
        enable_ma_crossover=False,
        enable_trend_following=False,
        enable_grid_trading=False,
        enable_liquidation_capture=False,
        enable_vwap_scalping=False,
        enable_funding_arb=False,
        enable_momentum_scalping=False,
        enable_orderbook_imbalance=False,
        enable_session_range_breakout=False,
        enable_calendar_flow=False,
        client=client,
    )


# ----------------------------------------------------------------------
# Mode validation
# ----------------------------------------------------------------------


class TestGateMode:
    def test_valid_modes_pass_through(self):
        assert validate_gate_mode("off") == GATE_OFF
        assert validate_gate_mode("log") == GATE_LOG
        assert validate_gate_mode("enforce") == GATE_ENFORCE
        assert validate_gate_mode(" ENFORCE ") == GATE_ENFORCE

    def test_garbage_falls_back_to_default(self):
        assert validate_gate_mode("banana") == DEFAULT_GATE_MODE

    def test_unset_is_default_off(self):
        assert validate_gate_mode(None) == GATE_OFF
        assert DEFAULT_GATE_MODE == GATE_OFF

    def test_exempt_list_parses(self, monkeypatch):
        monkeypatch.setenv("DIRECTIONAL_GATE_EXEMPT", "A, B ,C,")
        assert resolve_gate_exempt() == {"A", "B", "C"}


# ----------------------------------------------------------------------
# Combined-bias truth table
# ----------------------------------------------------------------------


class TestCombined:
    @pytest.mark.parametrize(
        "trend,funding,expected",
        [
            (BIAS_LONG, BIAS_LONG, BIAS_LONG),
            (BIAS_SHORT, BIAS_SHORT, BIAS_SHORT),
            (BIAS_LONG, BIAS_NEUTRAL, BIAS_LONG),
            (BIAS_NEUTRAL, BIAS_SHORT, BIAS_SHORT),
            (BIAS_NEUTRAL, BIAS_NEUTRAL, BIAS_NEUTRAL),
            # Conflict: the gate stands aside rather than picking a side
            (BIAS_LONG, BIAS_SHORT, BIAS_NEUTRAL),
            (BIAS_SHORT, BIAS_LONG, BIAS_NEUTRAL),
        ],
    )
    def test_truth_table(self, trend, funding, expected):
        assert DirectionalBias(trend=trend, funding=funding).combined == expected

    def test_allows(self):
        long_bias = DirectionalBias(trend=BIAS_LONG)
        assert long_bias.allows(OrderSide.BUY)
        assert not long_bias.allows(OrderSide.SELL)
        neutral = DirectionalBias()
        assert neutral.allows(OrderSide.BUY)
        assert neutral.allows(OrderSide.SELL)


# ----------------------------------------------------------------------
# Trend leg
# ----------------------------------------------------------------------


class TestTrendBias:
    def test_uptrend_is_long(self):
        closes = [100.0 + i for i in range(60)]
        bias, detail = DirectionalBiasEngine().trend_bias(_tf_data(closes))
        assert bias == BIAS_LONG
        assert detail["trend_ema_fast"] > detail["trend_ema_slow"]

    def test_downtrend_is_short(self):
        closes = [200.0 - i for i in range(60)]
        bias, _ = DirectionalBiasEngine().trend_bias(_tf_data(closes))
        assert bias == BIAS_SHORT

    def test_flat_market_is_neutral(self):
        # price == fast == slow: the strict inequalities must not fire
        closes = [100.0] * 60
        bias, _ = DirectionalBiasEngine().trend_bias(_tf_data(closes))
        assert bias == BIAS_NEUTRAL

    def test_insufficient_history_is_neutral(self):
        closes = [100.0 + i for i in range(30)]  # < EMA slow (50)
        bias, detail = DirectionalBiasEngine().trend_bias(_tf_data(closes))
        assert bias == BIAS_NEUTRAL
        assert "insufficient" in detail["trend_reason"]

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("DIRECTIONAL_TREND_EMA_FAST", "5")
        monkeypatch.setenv("DIRECTIONAL_TREND_EMA_SLOW", "10")
        engine = DirectionalBiasEngine()
        closes = [100.0 + i for i in range(12)]
        bias, _ = engine.trend_bias(_tf_data(closes))
        assert bias == BIAS_LONG

    def test_bad_ema_config_is_neutral(self, monkeypatch):
        monkeypatch.setenv("DIRECTIONAL_TREND_EMA_FAST", "50")
        monkeypatch.setenv("DIRECTIONAL_TREND_EMA_SLOW", "20")
        closes = [100.0 + i for i in range(60)]
        bias, detail = DirectionalBiasEngine().trend_bias(_tf_data(closes))
        assert bias == BIAS_NEUTRAL
        assert detail["trend_reason"] == "bad_ema_config"


# ----------------------------------------------------------------------
# Funding leg
# ----------------------------------------------------------------------


class TestFundingBias:
    def test_extreme_positive_funding_is_short(self):
        # 119 ordinary settlements, current one at the top of the window
        rates = [0.0001] * 119 + [0.001]
        engine = DirectionalBiasEngine(client=_funding_client(rates))
        bias, detail = engine.funding_bias("BTC-USDC", now=datetime(2024, 1, 2))
        assert bias == BIAS_SHORT
        assert detail["funding_pct"] == 1.0

    def test_extreme_negative_funding_is_long(self):
        rates = [0.0001] * 119 + [-0.001]
        engine = DirectionalBiasEngine(client=_funding_client(rates))
        bias, _ = engine.funding_bias("BTC-USDC", now=datetime(2024, 1, 2))
        assert bias == BIAS_LONG

    def test_mid_range_funding_is_neutral(self):
        # Current rate sits at the median of a symmetric window
        rates = [0.00002 * (i - 60) for i in range(119)] + [0.0]
        engine = DirectionalBiasEngine(client=_funding_client(rates))
        bias, detail = engine.funding_bias("BTC-USDC", now=datetime(2024, 1, 2))
        assert 0.4 < detail["funding_pct"] < 0.6
        assert bias == BIAS_NEUTRAL

    def test_sign_guard_blocks_negative_extreme_short(self):
        # Rank 1.0 inside an all-negative window: the crowd is SHORT,
        # so a contrarian short signal would be backwards. Sign guard
        # must hold it neutral.
        rates = [-0.001] * 119 + [-0.0001]
        engine = DirectionalBiasEngine(client=_funding_client(rates))
        bias, detail = engine.funding_bias("BTC-USDC", now=datetime(2024, 1, 2))
        assert detail["funding_pct"] == 1.0
        assert bias == BIAS_NEUTRAL

    def test_insufficient_history_is_neutral(self):
        rates = [0.001] * 10
        engine = DirectionalBiasEngine(client=_funding_client(rates))
        bias, detail = engine.funding_bias("BTC-USDC", now=datetime(2024, 1, 2))
        assert bias == BIAS_NEUTRAL
        assert detail["funding_reason"] == "insufficient_history"

    def test_no_client_is_neutral(self):
        bias, detail = DirectionalBiasEngine().funding_bias("BTC-USDC")
        assert bias == BIAS_NEUTRAL
        assert detail["funding_reason"] == "no_client"

    def test_result_is_cached_per_hour(self):
        client = _funding_client([0.0001] * 120)
        engine = DirectionalBiasEngine(client=client)
        t = datetime(2024, 1, 2, 10, 5)
        engine.funding_bias("BTC-USDC", now=t)
        engine.funding_bias("BTC-USDC", now=t.replace(minute=55))
        assert client.get_funding_history.call_count == 1
        # New hour -> recomputed
        engine.funding_bias("BTC-USDC", now=datetime(2024, 1, 2, 11, 0))
        assert client.get_funding_history.call_count == 2

    def test_client_exception_is_neutral_not_fatal(self):
        client = MagicMock()
        client.get_funding_history.side_effect = RuntimeError("api down")
        bias, _ = DirectionalBiasEngine(client=client).funding_bias(
            "BTC-USDC", now=datetime(2024, 1, 2)
        )
        assert bias == BIAS_NEUTRAL


# ----------------------------------------------------------------------
# StrategyManager gate wiring (Step 3.4)
# ----------------------------------------------------------------------


class TestGateWiring:
    def test_off_mode_does_not_touch_signals(self, monkeypatch):
        sm = _manager(monkeypatch, "off")
        sig = _signal(OrderSide.SELL)
        out = sm._apply_directional_gate([sig], "BTC-USDC", _tf_data([100.0] * 60))
        assert out == [sig]
        assert "directional_bias" not in sig.indicators
        assert sig.multi_timeframe_alignment is True

    def test_log_mode_stamps_but_never_drops(self, monkeypatch):
        sm = _manager(monkeypatch, "log")
        closes = [100.0 + i for i in range(60)]  # long bias
        sig = _signal(OrderSide.SELL)  # fights it
        out = sm._apply_directional_gate([sig], "BTC-USDC", _tf_data(closes))
        assert out[0].indicators["directional_bias"]["trend"] == BIAS_LONG
        assert out[0].multi_timeframe_alignment is True

    def test_enforce_fails_alignment_on_fighting_signal(self, monkeypatch):
        sm = _manager(monkeypatch, "enforce")
        closes = [100.0 + i for i in range(60)]  # long bias
        against = _signal(OrderSide.SELL)
        aligned = _signal(OrderSide.BUY)
        out = sm._apply_directional_gate(
            [against, aligned], "BTC-USDC", _tf_data(closes)
        )
        assert out[0].multi_timeframe_alignment is False
        assert not out[0].is_valid()
        assert out[1].multi_timeframe_alignment is True
        assert out[1].is_valid()

    def test_enforce_neutral_bias_gates_nothing(self, monkeypatch):
        sm = _manager(monkeypatch, "enforce")
        flat = [100.0] * 60
        sig = _signal(OrderSide.SELL)
        out = sm._apply_directional_gate([sig], "BTC-USDC", _tf_data(flat))
        assert out[0].multi_timeframe_alignment is True

    def test_exempt_strategy_is_never_gated(self, monkeypatch):
        sm = _manager(monkeypatch, "enforce")
        closes = [100.0 + i for i in range(60)]  # long bias
        grid = _signal(OrderSide.SELL, strategy=StrategyType.GRID_TRADING)
        out = sm._apply_directional_gate([grid], "BTC-USDC", _tf_data(closes))
        assert out[0].multi_timeframe_alignment is True
        # But the bias is still stamped for the census
        assert "directional_bias" in out[0].indicators

    def test_default_exempt_set(self):
        assert resolve_gate_exempt() == {
            "GridTrading",
            "FundingArb",
            "LiquidationCapture",
        }
