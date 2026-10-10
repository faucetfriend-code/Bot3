"""Order construction, validation and execution flow.

Covers, in the order an entry travels:

* ``models.Order.is_valid_for_pacifica`` - the static order validator;
* ``order_result.OrderResult`` - ack and fill-lookup normalization;
* ``execution_layer.ExecutionLayer`` - the 1m/5m entry refinement, with
  hand-built deterministic candles instead of seeded random walks;
* ``TradingBot._execute_standard_signal_coordinated`` and the fill /
  pending / rejection accounting behind it;
* ``TradingBot._emergency_close_position`` - the reduce-only close.

Everything runs against the in-memory fakes in ``order_risk_fakes``;
no network, no exchange, no live database.  Tests marked
``xfail(strict=True)`` document real defects found while writing the
suite; a separate change owns the fixes.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from order_risk_fakes import FakeExchange, FakeRawClient, make_bot, make_signal
from trading_bot_v2.event_system import EventType
from trading_bot_v2.execution_layer import ExecutionContext, ExecutionLayer
from trading_bot_v2.models import Order, OrderSide, OrderStatus, OrderType
from trading_bot_v2.order_result import OrderResult, OrderResultStatus

# ======================================================================
# models.Order validation
# ======================================================================


def _order(**overrides: Any) -> Order:
    """Build a market order that passes every Pacifica check."""
    fields: Dict[str, Any] = dict(
        id="o1",
        asset="BTC",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1.0,
        leverage=10,
    )
    fields.update(overrides)
    return Order(**fields)


class TestOrderValidation:
    def test_default_market_order_is_valid(self) -> None:
        ok, errors = _order().is_valid_for_pacifica()
        assert ok is True and errors == []

    def test_limit_order_requires_price(self) -> None:
        ok, errors = _order(order_type=OrderType.LIMIT).is_valid_for_pacifica()
        assert ok is False
        assert errors == ["Limit order requires price"]

    @pytest.mark.parametrize("kind", [OrderType.STOP_MARKET, OrderType.STOP_LIMIT])
    def test_stop_orders_require_stop_price(self, kind: OrderType) -> None:
        ok, errors = _order(order_type=kind, price=100.0).is_valid_for_pacifica()
        assert ok is False
        assert "Stop order requires stop_price" in errors

    def test_price_off_tick_is_rejected(self) -> None:
        order = _order(order_type=OrderType.LIMIT, price=100.3, tick_size=0.5)
        ok, errors = order.is_valid_for_pacifica()
        assert ok is False and "tick size" in errors[0]

    def test_price_on_tick_is_accepted(self) -> None:
        order = _order(order_type=OrderType.LIMIT, price=100.5, tick_size=0.5)
        assert order.is_valid_for_pacifica() == (True, [])

    @pytest.mark.parametrize(
        ("quantity", "valid"),
        [(0.5, True), (1.0, True), (0.49, False), (1.01, False)],
    )
    def test_size_limits_are_inclusive(self, quantity: float, valid: bool) -> None:
        order = _order(quantity=quantity, min_size=0.5, max_size=1.0)
        assert order.is_valid_for_pacifica()[0] is valid

    @pytest.mark.parametrize(
        ("leverage", "valid"), [(5, True), (50, True), (4, False), (51, False)]
    )
    def test_leverage_bounds_are_inclusive(self, leverage: int, valid: bool) -> None:
        assert _order(leverage=leverage).is_valid_for_pacifica()[0] is valid

    def test_zero_quantity_below_minimum(self) -> None:
        ok, errors = _order(quantity=0.0, min_size=0.001).is_valid_for_pacifica()
        assert ok is False and "below minimum" in errors[0]

    def test_multiple_errors_are_all_reported(self) -> None:
        order = _order(order_type=OrderType.LIMIT, leverage=1, quantity=0.0)
        order.min_size = 0.001
        ok, errors = order.is_valid_for_pacifica()
        assert ok is False and len(errors) == 3

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "is_valid_for_pacifica uses float modulo for the lot-size check: "
            "0.3 % 0.1 is 0.0999..., so a quantity that IS a whole number of "
            "lots is rejected.  Needs a tolerance or Decimal arithmetic."
        ),
    )
    def test_whole_lot_quantity_with_decimal_lot_size_is_valid(self) -> None:
        assert _order(quantity=0.3, lot_size=0.1).is_valid_for_pacifica()[0] is True

    def test_to_dict_serializes_enums(self) -> None:
        data = _order().to_dict()
        assert data["side"] == "buy"
        assert data["order_type"] == "market"
        assert data["status"] == OrderStatus.PENDING.value
        assert isinstance(data["created_at"], str)


# ======================================================================
# order_result.OrderResult
# ======================================================================


class TestOrderResultFromAck:
    def test_accepted_ack_with_order_id(self) -> None:
        result = OrderResult.from_ack(
            {"success": True, "data": {"order_id": 42, "client_order_id": "c1"}}
        )
        assert result.accepted is True and result.success is True
        assert result.order_id == "42" and result.client_order_id == "c1"
        assert result.status == OrderResultStatus.ACCEPTED.value
        assert result.error is None and result.has_fills is False

    def test_bare_success_ack_is_accepted_without_id(self) -> None:
        result = OrderResult.from_ack(
            {"success": True, "data": {}, "status": "success"}
        )
        assert result.accepted is True and result.order_id is None

    def test_success_without_id_or_status_is_unknown(self) -> None:
        result = OrderResult.from_ack({"success": True, "data": {}})
        assert result.accepted is False
        assert result.status == OrderResultStatus.UNKNOWN.value
        assert result.error == "order not accepted"

    def test_rejection_carries_error(self) -> None:
        result = OrderResult.from_ack({"success": False, "error": "insufficient"})
        assert result.rejected is True and result.error == "insufficient"

    def test_error_key_alone_means_rejected(self) -> None:
        result = OrderResult.from_ack({"error": "bad symbol"})
        assert result.rejected is True

    @pytest.mark.parametrize("ack", ["success", None, 42, ["x"]])
    def test_non_dict_ack_is_unknown_never_success(self, ack: Any) -> None:
        result = OrderResult.from_ack(ack)
        assert result.success is False and result.accepted is False
        assert result.status == OrderResultStatus.UNKNOWN.value
        assert "unexpected ack type" in (result.error or "")

    def test_fill_data_in_ack_marks_filled(self) -> None:
        ack = {
            "success": True,
            "data": {"order_id": "1", "filled_size": "2.5", "avg_price": "99.5"},
        }
        result = OrderResult.from_ack(ack)
        assert result.is_filled is True and result.has_fills is True
        assert result.filled_quantity == 2.5 and result.avg_fill_price == 99.5

    def test_zero_fill_in_ack_stays_accepted(self) -> None:
        ack = {"success": True, "data": {"order_id": "1", "filled_quantity": "0"}}
        result = OrderResult.from_ack(ack)
        assert result.status == OrderResultStatus.ACCEPTED.value
        assert result.filled_quantity == 0.0

    def test_non_dict_data_is_tolerated(self) -> None:
        result = OrderResult.from_ack({"success": True, "data": "weird", "order_id": 7})
        assert result.accepted is True and result.order_id == "7"

    def test_unparseable_fill_fields_are_skipped(self) -> None:
        ack = {"success": True, "data": {"order_id": "1", "filled_size": "n/a"}}
        assert OrderResult.from_ack(ack).filled_quantity == 0.0


class TestOrderResultFromFillLookup:
    def test_full_fill(self) -> None:
        result = OrderResult.from_fill_lookup("1", "FILLED", 2.0, 101.0, 2.0)
        assert result.is_filled and result.success and result.accepted
        assert result.avg_fill_price == 101.0

    def test_partial_fill_by_quantity(self) -> None:
        result = OrderResult.from_fill_lookup("1", "partially_filled", 1.0, 100.0, 2.0)
        assert result.status == OrderResultStatus.PARTIAL.value
        assert result.has_fills is True and result.is_filled is False

    def test_fill_at_requested_quantity_is_complete_despite_state(self) -> None:
        result = OrderResult.from_fill_lookup("1", "partially_filled", 2.0, 100.0, 2.0)
        assert result.is_filled is True

    def test_fill_without_requested_quantity_in_open_state_is_partial(self) -> None:
        result = OrderResult.from_fill_lookup("1", "live", 1.0, 100.0, None)
        assert result.status == OrderResultStatus.PARTIAL.value

    @pytest.mark.parametrize("state", ["live", "OPEN", "pending", "new"])
    def test_open_states_are_accepted(self, state: str) -> None:
        result = OrderResult.from_fill_lookup("1", state, 0.0, None)
        assert result.status == OrderResultStatus.ACCEPTED.value
        assert result.accepted is True and result.success is False

    @pytest.mark.parametrize("state", ["canceled", "cancelled", "rejected", "failed"])
    def test_terminal_states_are_rejected(self, state: str) -> None:
        result = OrderResult.from_fill_lookup("1", state, 0.0, None)
        assert result.rejected is True and result.error == f"order {state}"

    def test_unknown_state_without_fills_is_unknown(self) -> None:
        result = OrderResult.from_fill_lookup("1", "", 0.0, None)
        assert result.status == OrderResultStatus.UNKNOWN.value
        assert result.accepted is False

    def test_negative_fill_quantity_clamps_to_zero(self) -> None:
        result = OrderResult.from_fill_lookup("1", "live", -1.0, 100.0)
        assert result.filled_quantity == 0.0 and result.avg_fill_price is None

    def test_to_dict_keeps_legacy_shape(self) -> None:
        data = OrderResult.from_fill_lookup("9", "filled", 1.0, 50.0, 1.0).to_dict()
        assert data["success"] is True and data["data"]["order_id"] == "9"
        assert data["status"] == "filled"


# ======================================================================
# ExecutionLayer with deterministic candles
# ======================================================================


def candles(
    closes: List[float],
    volumes: Optional[List[float]] = None,
    last_candle: Optional[Dict[str, float]] = None,
) -> Dict[str, List[float]]:
    """Build an OHLCV dict from a close series.

    Each candle opens at the previous close and extends 0.1 beyond the
    body on both sides, so ATR is driven by the close-to-close step.
    ``last_candle`` overrides the final open/high/low/close to shape a
    specific wick pattern.

    Args:
        closes: Close prices, oldest first.
        volumes: Volume series (defaults to a flat 1000).
        last_candle: Optional override for the final candle.

    Returns:
        Dict with open/high/low/close/volume lists.
    """
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + 0.1 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.1 for o, c in zip(opens, closes)]
    data = {
        "open": opens,
        "high": highs,
        "low": lows,
        "close": list(closes),
        "volume": list(volumes) if volumes else [1000.0] * len(closes),
    }
    for key, value in (last_candle or {}).items():
        data[key][-1] = value
    return data


def walk(steps: List[float], start: float = 100.0, count: int = 30) -> List[float]:
    """Close series produced by repeating ``steps`` from ``start``."""
    closes = [start]
    for i in range(count):
        closes.append(closes[-1] + steps[i % len(steps)])
    return closes


RISING = walk([1.0])  # RSI 100
FALLING = walk([-1.0])  # RSI 0
UP_BIAS = walk([2.0, -1.0])  # RSI ~66, last 3 closes rising
DOWN_BIAS = walk([-2.0, 1.0])  # RSI ~33, last 3 closes falling
SPIKE = [1000.0] * 30 + [2000.0]  # last / avg(last 20) ~ 1.9
FLAT = [1000.0] * 31


def _layer(data_5m: Any, data_1m: Any, enabled: bool = True) -> ExecutionLayer:
    """ExecutionLayer over a fetcher that returns the given frames."""
    fetcher = SimpleNamespace(
        get_candles_multi_tf=lambda **kw: {"5m": data_5m, "1m": data_1m}
    )
    return ExecutionLayer(fetcher=fetcher, enabled=enabled)


class TestWickRejection:
    layer = ExecutionLayer(fetcher=SimpleNamespace(), enabled=True)

    def test_bullish_long_lower_wick(self) -> None:
        wick = self.layer._detect_wick_rejection(99.0, 100.0, 90.0, 99.5)
        assert wick == "bullish"

    def test_bearish_long_upper_wick(self) -> None:
        wick = self.layer._detect_wick_rejection(91.0, 100.0, 90.0, 90.5)
        assert wick == "bearish"

    def test_zero_range_is_none(self) -> None:
        assert self.layer._detect_wick_rejection(100.0, 100.0, 100.0, 100.0) is None

    def test_balanced_candle_is_none(self) -> None:
        assert self.layer._detect_wick_rejection(95.0, 100.0, 90.0, 96.0) is None

    def test_lower_wick_at_exactly_sixty_percent_is_not_bullish(self) -> None:
        # range 10, lower wick 6.0 (strict > 0.6 required), body 1.0
        assert self.layer._detect_wick_rejection(96.0, 100.0, 90.0, 97.0) is None

    def test_large_body_with_long_wick_is_none(self) -> None:
        # lower wick 6.5 of 10 but body 3.5 (>= 30%)
        assert self.layer._detect_wick_rejection(96.5, 100.0, 90.0, 100.0) is None


class TestFiveMinuteSetup:
    layer = ExecutionLayer(fetcher=SimpleNamespace(), enabled=True)

    def _check(self, side: OrderSide, rsi: Optional[float], momentum: Any = None):
        ctx = ExecutionContext(rsi_5m=rsi, momentum_5m=momentum)
        return self.layer._validate_5m_setup(ctx, side)

    def test_missing_rsi_never_blocks(self) -> None:
        assert self._check(OrderSide.BUY, None, "counter") == (
            True,
            "RSI unavailable",
        )

    @pytest.mark.parametrize(
        ("side", "rsi", "fragment"),
        [
            (OrderSide.BUY, 75.1, "too high"),
            (OrderSide.BUY, 39.9, "bearish"),
            (OrderSide.SELL, 24.9, "too low"),
            (OrderSide.SELL, 60.1, "bullish"),
        ],
    )
    def test_rsi_outside_band_is_invalid(
        self, side: OrderSide, rsi: float, fragment: str
    ) -> None:
        valid, reason = self._check(side, rsi)
        assert valid is False and fragment in reason

    @pytest.mark.parametrize(
        ("side", "rsi"),
        [
            (OrderSide.BUY, 75.0),
            (OrderSide.BUY, 40.0),
            (OrderSide.SELL, 25.0),
            (OrderSide.SELL, 60.0),
        ],
    )
    def test_rsi_exactly_on_threshold_is_valid(
        self, side: OrderSide, rsi: float
    ) -> None:
        assert self._check(side, rsi) == (True, "5m setup valid")

    def test_counter_momentum_is_invalid(self) -> None:
        valid, reason = self._check(OrderSide.BUY, 55.0, "counter")
        assert valid is False and "momentum counter" in reason

    def test_aligned_momentum_is_valid(self) -> None:
        assert self._check(OrderSide.SELL, 45.0, "aligned")[0] is True


class TestOneMinuteTiming:
    layer = ExecutionLayer(fetcher=SimpleNamespace(), enabled=True)

    def _check(self, side: OrderSide, ratio: Optional[float], wick: Any = None):
        ctx = ExecutionContext(volume_ratio_1m=ratio, wick_rejection=wick)
        return self.layer._validate_1m_timing(ctx, side)

    def test_no_data_is_neutral_and_not_good(self) -> None:
        good, text = self._check(OrderSide.BUY, None)
        assert not good and text == "neutral"

    def test_volume_exactly_at_spike_ratio_is_good(self) -> None:
        good, text = self._check(OrderSide.BUY, 1.2)
        assert bool(good) is True and "volume 1.2x" in text

    def test_volume_just_under_spike_ratio_is_not_good(self) -> None:
        good, text = self._check(OrderSide.BUY, 1.19)
        assert not good and "low volume" in text

    def test_aligned_wick_alone_is_good(self) -> None:
        good, text = self._check(OrderSide.SELL, 0.5, "bearish")
        assert bool(good) is True and "wick rejection confirmed" in text

    def test_counter_wick_is_reported_and_not_good(self) -> None:
        good, text = self._check(OrderSide.BUY, 0.5, "bearish")
        assert not good and "wick rejection counter" in text


class TestStopRefinement:
    layer = ExecutionLayer(fetcher=SimpleNamespace(), enabled=True)

    def test_buy_tightens_when_atr_stop_is_closer(self) -> None:
        sig = make_signal(side=OrderSide.BUY, entry_price=100.0, stop_loss=90.0)
        assert self.layer._refine_stop_loss(sig, atr_5m=2.0) == pytest.approx(97.0)

    def test_buy_keeps_tighter_original(self) -> None:
        sig = make_signal(side=OrderSide.BUY, entry_price=100.0, stop_loss=99.0)
        assert self.layer._refine_stop_loss(sig, atr_5m=2.0) is None

    def test_sell_tightens_when_atr_stop_is_closer(self) -> None:
        sig = make_signal(side=OrderSide.SELL, entry_price=100.0, stop_loss=110.0)
        assert self.layer._refine_stop_loss(sig, atr_5m=2.0) == pytest.approx(103.0)

    def test_sell_keeps_tighter_original(self) -> None:
        sig = make_signal(side=OrderSide.SELL, entry_price=100.0, stop_loss=101.0)
        assert self.layer._refine_stop_loss(sig, atr_5m=2.0) is None

    def test_equal_stop_is_not_a_refinement(self) -> None:
        sig = make_signal(side=OrderSide.BUY, entry_price=100.0, stop_loss=97.0)
        assert self.layer._refine_stop_loss(sig, atr_5m=2.0) is None


class TestBuildContext:
    layer = ExecutionLayer(fetcher=SimpleNamespace(), enabled=True)

    def test_full_frames_populate_every_field(self) -> None:
        ctx = self.layer._build_context(
            candles(UP_BIAS), candles(UP_BIAS, SPIKE), OrderSide.BUY
        )
        assert ctx.rsi_5m == pytest.approx(66.67, abs=0.1)
        assert ctx.atr_5m is not None and ctx.atr_5m > 0
        assert ctx.momentum_5m == "aligned"
        assert ctx.price_1m == UP_BIAS[-1]
        assert ctx.volume_ratio_1m == pytest.approx(2000.0 / 1050.0)
        assert ctx.wick_rejection is None

    def test_momentum_is_relative_to_side(self) -> None:
        ctx = self.layer._build_context(
            candles(UP_BIAS), candles(UP_BIAS), OrderSide.SELL
        )
        assert ctx.momentum_5m == "counter"

    def test_short_frames_leave_indicators_unset(self) -> None:
        short = candles(walk([1.0], count=5))
        ctx = self.layer._build_context(short, short, OrderSide.BUY)
        assert ctx.rsi_5m is None and ctx.atr_5m is None
        assert ctx.volume_ratio_1m is None
        assert ctx.price_1m == short["close"][-1]
        assert ctx.momentum_5m == "aligned"

    def test_exactly_fourteen_candles_still_builds_a_context(self) -> None:
        frame = candles(walk([1.0], count=13))
        assert len(frame["close"]) == 14
        ctx = self.layer._build_context(frame, frame, OrderSide.BUY)
        assert ctx.price_1m == frame["close"][-1]

    def test_zero_average_volume_gives_ratio_one(self) -> None:
        zero = candles(UP_BIAS, [0.0] * 31)
        ctx = self.layer._build_context(candles(UP_BIAS), zero, OrderSide.BUY)
        assert ctx.volume_ratio_1m == 1.0

    def test_missing_open_falls_back_to_previous_close(self) -> None:
        # open falls back to UP_BIAS[-2] == 116: lower wick 16 of range 18,
        # body 1 of 18 -> bullish rejection
        assert UP_BIAS[-2] == 116.0
        frame = candles(UP_BIAS, FLAT, {"high": 118.0, "low": 100.0, "close": 117.0})
        del frame["open"]
        ctx = self.layer._build_context(candles(UP_BIAS), frame, OrderSide.BUY)
        assert ctx.wick_rejection == "bullish"

    def test_malformed_frame_yields_default_context(self) -> None:
        ctx = self.layer._build_context({"close": None}, {"close": None}, OrderSide.BUY)
        assert ctx == ExecutionContext()


class TestRefineEntry:
    def test_disabled_layer_counts_but_does_not_fetch(self) -> None:
        fetcher = SimpleNamespace(
            get_candles_multi_tf=lambda **kw: pytest.fail("must not fetch")
        )
        layer = ExecutionLayer(fetcher=fetcher, enabled=False)
        sig = make_signal(confidence=0.5)
        assert layer.refine_entry(sig, "BTC") is sig
        assert layer.get_stats()["signals_received"] == 1
        assert layer.get_stats()["enabled"] is False

    def test_empty_frame_falls_back_to_original(self) -> None:
        layer = _layer({}, candles(UP_BIAS))
        sig = make_signal(confidence=0.5, notes="n")
        assert layer.refine_entry(sig, "BTC") is sig
        assert sig.confidence == 0.5 and sig.notes == "n"
        assert layer.get_stats()["fallback_to_original"] == 1

    def test_good_setup_and_volume_spike_boosts_and_refines_stop(self) -> None:
        layer = _layer(candles(UP_BIAS), candles(UP_BIAS, SPIKE))
        sig = make_signal(side=OrderSide.BUY, entry_price=100.0, stop_loss=90.0)
        sig.confidence = 0.7
        out = layer.refine_entry(sig, "BTC")
        assert out.confidence == pytest.approx(0.77)
        assert "1m confirmed" in out.notes and "volume 1.9x" in out.notes
        assert 90.0 < out.stop_loss < 100.0 and "Stop refined" in out.notes
        stats = layer.get_stats()
        assert stats["signals_refined"] == 1 and stats["refinement_rate"] == 1.0

    def test_already_tight_stop_is_left_alone(self) -> None:
        layer = _layer(candles(UP_BIAS), candles(UP_BIAS, SPIKE))
        sig = make_signal(side=OrderSide.BUY, entry_price=100.0, stop_loss=99.9)
        out = layer.refine_entry(sig, "BTC")
        assert out.stop_loss == 99.9 and "Stop refined" not in out.notes

    def test_extreme_rsi_reduces_confidence_without_blocking(self) -> None:
        layer = _layer(candles(RISING), candles(RISING, FLAT))
        sig = make_signal(side=OrderSide.BUY, confidence=0.7)
        out = layer.refine_entry(sig, "BTC")
        assert out is sig
        assert out.confidence == pytest.approx(0.56)
        assert "RSI too high" in out.notes and "1m timing" in out.notes
        assert layer.get_stats()["signals_refined"] == 0

    def test_sell_against_falling_rsi_is_too_low(self) -> None:
        layer = _layer(candles(FALLING), candles(FALLING, FLAT))
        out = layer.refine_entry(make_signal(side=OrderSide.SELL), "BTC")
        assert "RSI too low" in out.notes

    def test_sell_with_down_bias_is_a_valid_setup(self) -> None:
        layer = _layer(candles(DOWN_BIAS), candles(DOWN_BIAS, FLAT))
        sig = make_signal(side=OrderSide.SELL, confidence=0.6)
        out = layer.refine_entry(sig, "BTC")
        assert out.confidence == pytest.approx(0.6)
        assert "5m:" not in out.notes

    def test_confidence_floor_after_penalty(self) -> None:
        layer = _layer(candles(RISING), candles(RISING, FLAT))
        out = layer.refine_entry(make_signal(confidence=0.05), "BTC")
        assert out.confidence == pytest.approx(0.1)

    def test_confidence_cap_after_boost(self) -> None:
        layer = _layer(candles(UP_BIAS), candles(UP_BIAS, SPIKE))
        out = layer.refine_entry(make_signal(confidence=0.95), "BTC")
        assert out.confidence == 1.0

    def test_fetcher_exception_is_a_fallback(self) -> None:
        def boom(**kw: Any) -> Dict[str, Any]:
            raise TimeoutError("slow")

        layer = ExecutionLayer(fetcher=SimpleNamespace(get_candles_multi_tf=boom))
        sig = make_signal()
        assert layer.refine_entry(sig, "BTC") is sig
        assert layer.get_stats()["fallback_to_original"] == 1

    def test_zero_signals_gives_zero_refinement_rate(self) -> None:
        assert _layer({}, {}).get_stats()["refinement_rate"] == 0


# ======================================================================
# TradingBot execution flow
# ======================================================================

ALLOC = {"allocated_amount": 500.0, "approved": True}


class TestStandardExecution:
    def test_order_is_built_from_allocation_and_signal(self) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange)
        sig = make_signal(side=OrderSide.SELL, entry_price=250.0, stop_loss=260.0)
        bot._execute_standard_signal_coordinated(sig, ALLOC)
        assert len(exchange.orders) == 1
        order = exchange.orders[0]
        assert order["symbol"] == "BTC" and order["side"] is OrderSide.SELL
        assert order["quantity"] == pytest.approx(2.0)
        assert order["order_type"] is OrderType.MARKET
        assert len(order["client_order_id"]) == 32
        assert "reduce_only" not in order and "stop_loss" not in order

    def test_stop_rides_on_entry_when_venue_holds_stops(self) -> None:
        exchange = FakeExchange(supports_venue_stops=True, stops=[])
        bot = make_bot(exchange)
        bot._execute_standard_signal_coordinated(make_signal(stop_loss=95.0), ALLOC)
        assert exchange.orders[0]["stop_loss"] == 95.0

    def test_stopless_signal_sends_no_stop_kwarg(self) -> None:
        exchange = FakeExchange(supports_venue_stops=True, stops=[])
        bot = make_bot(exchange, policy="local")
        bot._execute_standard_signal_coordinated(make_signal(stop_loss=None), ALLOC)
        assert "stop_loss" not in exchange.orders[0]

    @pytest.mark.parametrize("alloc", [{}, {"allocated_amount": 0.0}])
    def test_zero_capital_sends_nothing(self, alloc: Dict[str, float]) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange)
        bot._execute_standard_signal_coordinated(make_signal(), alloc)
        assert exchange.orders == []
        assert bot.signal_logger.failed[0]["error"] == "Invalid quantity (<=0)"

    def test_zero_entry_price_sends_nothing(self) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange)
        bot._execute_standard_signal_coordinated(make_signal(entry_price=0.0), ALLOC)
        assert exchange.orders == [] and len(bot.signal_logger.failed) == 1

    def test_execution_layer_skip_is_logged_as_rejected(self) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange)
        bot.execution_layer = SimpleNamespace(refine_entry=lambda s, sym: None)
        bot._execute_standard_signal_coordinated(make_signal(), ALLOC)
        assert exchange.orders == []
        assert bot.signal_logger.rejected[0]["reason"] == "ExecutionLayer timing skip"

    def test_missing_execution_layer_sends_signal_unrefined(self) -> None:
        exchange = FakeExchange(supports_venue_stops=True, stops=[])
        bot = make_bot(exchange)
        bot.execution_layer = None
        signal = make_signal(stop_loss=95.0)
        bot._execute_standard_signal_coordinated(signal, ALLOC)
        assert exchange.orders[0]["stop_loss"] == 95.0
        assert bot.signal_logger.pending[0]["signal"] is signal
        assert bot.signal_logger.rejected == []

    def test_refined_signal_is_the_one_sent(self) -> None:
        exchange = FakeExchange(supports_venue_stops=True, stops=[])
        bot = make_bot(exchange)
        refined = make_signal(stop_loss=98.0)
        bot.execution_layer = SimpleNamespace(refine_entry=lambda s, sym: refined)
        bot._execute_standard_signal_coordinated(make_signal(stop_loss=95.0), ALLOC)
        assert exchange.orders[0]["stop_loss"] == 98.0
        assert bot.signal_logger.pending[0]["signal"] is refined

    def test_rejected_ack_is_failed_and_never_looked_up(self) -> None:
        exchange = FakeExchange(ack={"success": False, "error": "margin"})
        bot = make_bot(exchange)
        bot._execute_standard_signal_coordinated(make_signal(), ALLOC)
        assert bot.signal_logger.failed[0]["error"] == "margin"
        assert exchange.fill_lookups == [] and bot.signal_logger.executed == []

    def test_string_ack_is_failed(self) -> None:
        exchange = FakeExchange(ack="success")
        bot = make_bot(exchange)
        bot._execute_standard_signal_coordinated(make_signal(), ALLOC)
        assert "unexpected ack type" in bot.signal_logger.failed[0]["error"]

    def test_transport_exception_is_failed_not_raised(self) -> None:
        exchange = FakeExchange(ack=ConnectionError("down"))
        bot = make_bot(exchange)
        bot._execute_standard_signal_coordinated(make_signal(), ALLOC)
        failed = bot.signal_logger.failed[0]
        assert failed["error"].startswith("ConnectionError")
        assert failed["notes"] == "Exception during execution"

    def test_filled_entry_is_recorded_from_the_fill(self) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange)
        bot._execute_standard_signal_coordinated(make_signal(entry_price=100.0), ALLOC)
        executed = bot.signal_logger.executed[0]
        assert executed["execution_result"] == "success"
        assert executed["filled_quantity"] == pytest.approx(5.0)
        assert executed["filled_price"] == 100.0 and executed["order_id"] == "o1"
        event = bot.event_bus.events[0]
        assert event["type"] is EventType.ORDER_PLACED
        assert event["data"]["requested_quantity"] == pytest.approx(5.0)
        assert event["data"]["fill_status"] == "filled"
        assert bot._pending_entries == {}

    def test_partial_fill_is_labelled(self) -> None:
        fill = OrderResult.from_fill_lookup("o1", "partially_filled", 2.0, 101.0, 5.0)
        bot = make_bot(FakeExchange(fill=fill))
        bot._execute_standard_signal_coordinated(make_signal(), ALLOC)
        executed = bot.signal_logger.executed[0]
        assert executed["execution_result"] == "partial_fill"
        assert executed["filled_quantity"] == 2.0 and executed["filled_price"] == 101.0

    def test_fill_in_ack_skips_the_lookup(self) -> None:
        ack = {"success": True, "data": {"order_id": "o1", "filled_size": 5.0}}
        exchange = FakeExchange(ack=ack)
        bot = make_bot(exchange)
        bot._execute_standard_signal_coordinated(make_signal(), ALLOC)
        assert exchange.fill_lookups == []
        assert bot.signal_logger.executed[0]["filled_price"] == 100.0

    def test_unknown_fill_parks_entry_as_pending(self) -> None:
        unknown = OrderResult(status=OrderResultStatus.UNKNOWN.value)
        bot = make_bot(FakeExchange(fill=unknown))
        bot._execute_standard_signal_coordinated(make_signal(), ALLOC)
        assert bot.signal_logger.executed == []
        (entry,) = bot._pending_entries.values()
        assert entry["order_id"] == "o1" and entry["lookups"] == 1
        assert entry["quantity"] == pytest.approx(5.0)
        assert len(bot.signal_logger.pending) == 2

    def test_fill_lookup_exception_parks_entry_as_pending(self) -> None:
        bot = make_bot(FakeExchange(fill=RuntimeError("timeout")))
        bot._execute_standard_signal_coordinated(make_signal(), ALLOC)
        assert len(bot._pending_entries) == 1

    def test_cancel_after_accept_is_failed(self) -> None:
        cancelled = OrderResult.from_fill_lookup("o1", "canceled", 0.0, None)
        bot = make_bot(FakeExchange(fill=cancelled))
        bot._execute_standard_signal_coordinated(make_signal(), ALLOC)
        assert bot._pending_entries == {}
        assert "rejected_after_accept" in bot.signal_logger.failed[0]["error"]

    def test_lookup_passes_both_ids_and_requested_quantity(self) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange)
        bot._execute_standard_signal_coordinated(make_signal(), ALLOC)
        lookup = exchange.fill_lookups[0]
        assert lookup["order_id"] == "o1"
        assert lookup["client_order_id"] == exchange.orders[0]["client_order_id"]
        assert lookup["requested_quantity"] == pytest.approx(5.0)


def _park(bot: Any, key: str = "c1") -> None:
    """Insert a pending entry as the entry path would have left it."""
    bot._pending_entries[key] = {
        "signal": make_signal(),
        "symbol": "BTC",
        "quantity": 2.0,
        "capital": 200.0,
        "order_id": "o9",
        "client_order_id": key,
        "submitted_at": 0.0,
        "lookups": 1,
    }


class TestPendingEntries:
    def test_nothing_pending_makes_no_exchange_call(self) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange)
        bot._complete_pending_entries()
        assert exchange.fill_lookups == []

    def test_delayed_fill_is_recorded_and_cleared(self) -> None:
        bot = make_bot(FakeExchange())
        _park(bot)
        bot._complete_pending_entries()
        assert bot._pending_entries == {}
        assert bot.signal_logger.executed[0]["execution_result"] == "delayed_fill"

    def test_delayed_partial_fill_is_labelled(self) -> None:
        fill = OrderResult.from_fill_lookup("o9", "partially_filled", 1.0, 99.0, 2.0)
        bot = make_bot(FakeExchange(fill=fill))
        _park(bot)
        bot._complete_pending_entries()
        result = bot.signal_logger.executed[0]["execution_result"]
        assert result == "delayed_partial_fill"

    def test_cancelled_pending_entry_is_failed(self) -> None:
        fill = OrderResult.from_fill_lookup("o9", "cancelled", 0.0, None)
        bot = make_bot(FakeExchange(fill=fill))
        _park(bot)
        bot._complete_pending_entries()
        assert bot._pending_entries == {}
        assert "rejected_after_accept" in bot.signal_logger.failed[0]["error"]

    def test_unresolved_entry_is_retried_until_the_lookup_limit(self) -> None:
        unknown = OrderResult(status=OrderResultStatus.UNKNOWN.value)
        bot = make_bot(FakeExchange(fill=unknown))
        bot._entry_fill_max_lookups = 3
        _park(bot)
        bot._complete_pending_entries()  # lookups -> 2, still pending
        assert bot._pending_entries["c1"]["lookups"] == 2
        bot._complete_pending_entries()  # lookups -> 3, given up
        assert bot._pending_entries == {}
        assert "fill_unconfirmed after 3" in bot.signal_logger.failed[0]["error"]
        assert bot.signal_logger.executed == []


# ======================================================================
# Emergency close (reduce-only exit)
# ======================================================================

LONG_ROW = {"symbol": "BTC", "side": "long", "amount": "3", "entry_price": "100"}


class TestEmergencyClose:
    def test_long_closes_with_reduce_only_sell(self) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange, raw_client=FakeRawClient([LONG_ROW]))
        assert bot._emergency_close_position("BTC", "LONG", 3.0) is True
        order = exchange.orders[0]
        assert order["side"] is OrderSide.SELL and order["reduce_only"] is True
        assert order["quantity"] == 3.0 and order["order_type"] is OrderType.MARKET

    def test_short_closes_with_buy(self) -> None:
        exchange = FakeExchange()
        row = dict(LONG_ROW, side="short")
        bot = make_bot(exchange, raw_client=FakeRawClient([row]))
        bot._emergency_close_position("BTC", "short", 3.0)
        assert exchange.orders[0]["side"] is OrderSide.BUY

    def test_quantity_is_clamped_to_exchange_position(self) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange, raw_client=FakeRawClient([LONG_ROW]))
        bot._emergency_close_position("BTC", "LONG", 10.0)
        assert exchange.orders[0]["quantity"] == 3.0

    def test_already_flat_sends_nothing_and_reports_success(self) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange, raw_client=FakeRawClient([]))
        assert bot._emergency_close_position("BTC", "LONG", 3.0) is True
        assert exchange.orders == []

    def test_unknown_exchange_state_sends_local_quantity_reduce_only(self) -> None:
        exchange = FakeExchange()
        bot = make_bot(exchange, raw_client=FakeRawClient(RuntimeError("down")))
        assert bot._emergency_close_position("BTC", "LONG", 3.0) is True
        assert exchange.orders[0]["quantity"] == 3.0
        assert exchange.orders[0]["reduce_only"] is True

    def test_rejected_close_returns_false(self) -> None:
        exchange = FakeExchange(ack={"success": False, "error": "no"})
        bot = make_bot(exchange, raw_client=FakeRawClient([LONG_ROW]))
        assert bot._emergency_close_position("BTC", "LONG", 3.0) is False

    def test_transport_error_returns_false(self) -> None:
        exchange = FakeExchange(ack=OSError("socket"))
        bot = make_bot(exchange, raw_client=FakeRawClient([LONG_ROW]))
        assert bot._emergency_close_position("BTC", "LONG", 3.0) is False
