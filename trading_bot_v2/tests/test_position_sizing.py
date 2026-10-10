"""Position sizing: Kelly, confidence multiplier, exit sizing, model math.

Covers ``kelly_position_sizer.KellyPositionSizer`` with exact expected
quantities (the existing suite only asserts ``>= 1.0``),
``confidence_sizer.ConfidenceSizer`` / ``IntegratedPositionSizer``,
``exit_sizing`` (how much is actually left to close) and the sizing
arithmetic on ``models.Signal`` / ``models.Position``.

Tests marked ``xfail(strict=True)`` document real defects; a separate
change owns the fixes.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from order_risk_fakes import FakeRawClient, make_signal
from trading_bot_v2.config import AssetClass, StrategyType
from trading_bot_v2.confidence_sizer import ConfidenceSizer, IntegratedPositionSizer
from trading_bot_v2.exchanges.base import PositionSide
from trading_bot_v2.exit_sizing import (
    normalize_position_side,
    normalize_symbol,
    plan_close_quantity,
    remaining_exchange_quantity,
)
from trading_bot_v2.kelly_position_sizer import KellyPositionSizer
from trading_bot_v2.models import OrderSide, Position
from order_risk_fakes import position

BALANCE = 1_000_000.0  # large enough that no path hits the 1-contract floor


class FakeTradeStore:
    """TradeStore stand-in returning canned closed trades."""

    def __init__(self, pnls: Any = ()) -> None:
        self.pnls = pnls
        self.queries: List[Dict[str, Any]] = []

    def get_closed_trades(self, **kw: Any) -> List[Dict[str, float]]:
        self.queries.append(kw)
        if isinstance(self.pnls, Exception):
            raise self.pnls
        return [{"pnl": p} for p in self.pnls]


def kelly(pnls: Any = (), fraction: float = 0.5, min_trades: int = 50):
    """KellyPositionSizer over a fake trade store (no database)."""
    return KellyPositionSizer(
        db=SimpleNamespace(),
        kelly_fraction=fraction,
        min_trades=min_trades,
        trade_store=FakeTradeStore(pnls),
    )


def sig(entry: float = 100.0, stop: Optional[float] = 95.0, **kw: Any):
    """Signal with a 5% stop by default."""
    kw.setdefault("strategy", StrategyType.MA_CROSSOVER)
    return make_signal(entry_price=entry, stop_loss=stop, **kw)


# ======================================================================
# Kelly formula
# ======================================================================


class TestKellyFormula:
    sizer = kelly()

    def test_textbook_values(self) -> None:
        # (0.6 * 100 - 0.4 * 50) / 100 = 0.40
        assert self.sizer._calculate_kelly_pct(0.6, 100.0, 50.0) == pytest.approx(0.40)

    def test_all_winners_is_full_kelly(self) -> None:
        assert self.sizer._calculate_kelly_pct(1.0, 100.0, 0.0) == 1.0

    def test_negative_expectancy_is_negative(self) -> None:
        assert self.sizer._calculate_kelly_pct(0.3, 50.0, 100.0) < 0

    @pytest.mark.parametrize("avg_win", [0.0, -1.0])
    def test_non_positive_average_win_is_zero(self, avg_win: float) -> None:
        assert self.sizer._calculate_kelly_pct(0.9, avg_win, 10.0) == 0.0


# ======================================================================
# Kelly sizing paths
# ======================================================================


class TestKellySizing:
    def test_fallback_uses_strategy_percentage(self) -> None:
        # 2% of 1M = 20_000 risk / 5% stop = 400_000 notional / 100 = 4_000
        assert kelly().calculate_position_size(sig(), BALANCE) == pytest.approx(4_000.0)

    @pytest.mark.parametrize(
        ("strategy", "expected"),
        [
            (StrategyType.MEAN_REVERSION, 3_000.0),  # 1.5%
            (StrategyType.GRID_TRADING, 1_000.0),  # 0.5%
            (StrategyType.LIQUIDATION_CAPTURE, 5_000.0),  # 2.5%
            (StrategyType.VWAP_SCALPING, 4_000.0),  # unlisted -> 2%
        ],
    )
    def test_fallback_table(self, strategy: StrategyType, expected: float) -> None:
        qty = kelly().calculate_position_size(sig(strategy=strategy), BALANCE)
        assert qty == pytest.approx(expected)

    def test_one_trade_short_of_minimum_uses_fallback(self) -> None:
        sizer = kelly([100.0] * 49)
        assert sizer.calculate_position_size(sig(), BALANCE) == pytest.approx(4_000.0)

    def test_exactly_minimum_trades_uses_kelly(self) -> None:
        # 40 wins of 100, 10 losses of 50: kelly = (0.8*100 - 0.2*50)/100 = 0.70
        # half kelly 0.35 -> capped 0.10 -> 100_000 / 0.05 / 100 = 20_000
        sizer = kelly([100.0] * 40 + [-50.0] * 10)
        assert sizer.calculate_position_size(sig(), BALANCE) == pytest.approx(20_000.0)

    def test_kelly_below_the_cap_is_used_as_is(self) -> None:
        # 30 wins of 100, 30 losses of 80: kelly = (0.5*100 - 0.5*80)/100 = 0.10
        # half kelly 0.05 -> 50_000 / 0.05 / 100 = 10_000
        sizer = kelly([100.0] * 30 + [-80.0] * 30)
        assert sizer.calculate_position_size(sig(), BALANCE) == pytest.approx(10_000.0)

    def test_quarter_kelly_halves_the_half_kelly_size(self) -> None:
        trades = [100.0] * 30 + [-80.0] * 30
        half = kelly(trades, fraction=0.5).calculate_position_size(sig(), BALANCE)
        quarter = kelly(trades, fraction=0.25).calculate_position_size(sig(), BALANCE)
        assert quarter == pytest.approx(half / 2)

    def test_negative_kelly_uses_one_percent(self) -> None:
        # 20 wins of 50, 40 losses of 100 -> negative -> 1% of 1M = 10_000
        # 10_000 / 0.05 / 100 = 2_000
        sizer = kelly([50.0] * 20 + [-100.0] * 40)
        assert sizer.calculate_position_size(sig(), BALANCE) == pytest.approx(2_000.0)

    def test_all_flat_trades_use_one_percent(self) -> None:
        sizer = kelly([0.0] * 60)
        assert sizer.calculate_position_size(sig(), BALANCE) == pytest.approx(2_000.0)

    @pytest.mark.parametrize("stop", [None, 100.0])
    def test_zero_stop_distance_sizes_to_zero(self, stop: Optional[float]) -> None:
        assert kelly().calculate_position_size(sig(stop=stop), BALANCE) == 0.0
        sizer = kelly([100.0] * 60)
        assert sizer.calculate_position_size(sig(stop=stop), BALANCE) == 0.0

    def test_sub_contract_quantity_floors_to_one(self) -> None:
        # 2% of 1_000 = 20 / 0.05 = 400 notional / 100 = 4 -> ok; at 50_000
        # per contract 400 / 50_000 = 0.008 -> floored to 1.0 contract
        assert kelly().calculate_position_size(sig(50_000.0, 47_500.0), 1_000.0) == 1.0

    def test_zero_balance_floors_to_one(self) -> None:
        assert kelly().calculate_position_size(sig(), 0.0) == 1.0

    def test_store_error_falls_back(self) -> None:
        sizer = kelly(RuntimeError("db locked"))
        assert sizer.calculate_position_size(sig(), BALANCE) == pytest.approx(4_000.0)

    def test_store_is_queried_by_strategy_and_account(self) -> None:
        sizer = kelly()
        sizer.calculate_position_size(sig(), BALANCE, account_id="acct-9")
        query = sizer.trade_store.queries[0]
        assert query == {
            "strategy": "ma_crossover",
            "account_id": "acct-9",
            "limit": 50,
        }

    def test_strategy_stats(self) -> None:
        stats = kelly([100.0] * 3 + [-50.0] + [0.0]).get_strategy_stats(
            StrategyType.MA_CROSSOVER
        )
        assert stats["total_trades"] == 5
        assert stats["win_rate"] == pytest.approx(0.6)
        assert stats["avg_win"] == 100.0 and stats["avg_loss"] == 50.0


class TestKellyFraction:
    @pytest.mark.parametrize("bad", [0.0, -0.5, 1.5])
    def test_invalid_fraction_is_ignored(self, bad: float) -> None:
        sizer = kelly()
        sizer.update_kelly_fraction(bad)
        assert sizer.kelly_fraction == 0.5

    def test_full_kelly_is_the_upper_bound(self) -> None:
        sizer = kelly()
        sizer.update_kelly_fraction(1.0)
        assert sizer.kelly_fraction == 1.0

    def test_recommendation_tiers(self) -> None:
        strong = kelly([200.0] * 50 + [-50.0] * 10)  # PF 4, WR 0.83
        moderate = kelly([100.0] * 30 + [-50.0] * 30)  # PF 2 but WR 0.5
        weak = kelly([50.0] * 20 + [-100.0] * 40)
        fresh = kelly([100.0] * 10)
        assert strong.get_recommended_kelly_fraction(StrategyType.MA_CROSSOVER) == 0.5
        assert (
            moderate.get_recommended_kelly_fraction(StrategyType.MA_CROSSOVER) == 0.33
        )
        assert weak.get_recommended_kelly_fraction(StrategyType.MA_CROSSOVER) == 0.25
        assert fresh.get_recommended_kelly_fraction(StrategyType.MA_CROSSOVER) == 0.25

    def test_no_losses_is_treated_as_weak(self) -> None:
        # total_losses == 0 -> profit_factor 0 -> most conservative tier.
        # Pinned: an undefined profit factor errs on the safe side.
        perfect = kelly([100.0] * 60)
        assert perfect.get_recommended_kelly_fraction(StrategyType.MA_CROSSOVER) == 0.25


# ======================================================================
# ConfidenceSizer
# ======================================================================


class TestConfidenceSizer:
    @pytest.mark.parametrize(
        ("confidence", "expected"),
        [(0.7, 0.7), (1.0, 1.0), (0.3, 0.3), (0.1, 0.3), (1.5, 1.0), (-0.2, 0.3)],
    )
    def test_linear_multiplier_with_floor_and_ceiling(
        self, confidence: float, expected: float
    ) -> None:
        result = ConfidenceSizer().calculate_multiplier(sig(confidence=confidence))
        assert result.multiplier == pytest.approx(expected)

    def test_clamped_confidence_is_reported(self) -> None:
        result = ConfidenceSizer().calculate_multiplier(sig(confidence=1.5))
        assert result.confidence == 1.0 and "confidence=1.00" in result.reason

    def test_zero_confidence_is_read_as_unset_and_sized_at_half(self) -> None:
        # Signal.confidence defaults to 0.0, which the sizer treats as "not
        # provided" and replaces with 0.5 rather than the 0.3 floor.
        result = ConfidenceSizer().calculate_multiplier(sig(confidence=0.0))
        assert result.multiplier == 0.5

    @pytest.mark.parametrize(
        ("strategy", "floor"),
        [
            (StrategyType.GRID_TRADING, 0.4),
            (StrategyType.LIQUIDATION_CAPTURE, 0.5),
            (StrategyType.TREND_FOLLOWING, 0.35),
            (StrategyType.VWAP_SCALPING, 0.3),
        ],
    )
    def test_strategy_floors(self, strategy: StrategyType, floor: float) -> None:
        low = sig(confidence=0.01, strategy=strategy)
        assert ConfidenceSizer().calculate_multiplier(low).multiplier == floor

    def test_strategy_floor_wins_over_global_floor(self) -> None:
        sizer = ConfidenceSizer(SimpleNamespace(confidence_floor=0.6))
        grid = sig(confidence=0.01, strategy=StrategyType.GRID_TRADING)
        other = sig(confidence=0.01, strategy=StrategyType.VWAP_SCALPING)
        assert sizer.calculate_multiplier(grid).multiplier == 0.4
        assert sizer.calculate_multiplier(other).multiplier == 0.6

    def test_sqrt_scale(self) -> None:
        sizer = ConfidenceSizer(SimpleNamespace(confidence_scale_type="sqrt"))
        assert sizer.calculate_multiplier(sig(confidence=0.25)).multiplier == 0.5
        assert sizer.calculate_multiplier(sig(confidence=0.04)).multiplier == 0.3

    def test_ceiling_from_config(self) -> None:
        sizer = ConfidenceSizer(SimpleNamespace(confidence_ceiling=0.8))
        assert sizer.calculate_multiplier(sig(confidence=0.95)).multiplier == 0.8

    def test_apply_to_size(self) -> None:
        result = ConfidenceSizer().apply_to_size(10.0, sig(confidence=0.6))
        assert result.base_size == 10.0 and result.effective_size == pytest.approx(6.0)

    def test_apply_to_zero_base_is_zero(self) -> None:
        assert ConfidenceSizer().apply_to_size(0.0, sig()).effective_size == 0.0


# ======================================================================
# IntegratedPositionSizer
# ======================================================================


def int_signal(**overrides: Any) -> SimpleNamespace:
    """Signal-like object with the ``symbol`` field the integrated sizer logs."""
    fields = dict(
        symbol="BTC",
        asset="BTC",
        strategy=StrategyType.MA_CROSSOVER,
        entry_price=100.0,
        stop_loss=95.0,
        confidence=0.5,
        side=OrderSide.BUY,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


class StubKelly:
    """Kelly stand-in matching the signature the integrated sizer calls."""

    def __init__(self, result: Any) -> None:
        self.result = result

    def calculate_position_size(self, **kw: Any) -> Any:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class TestIntegratedPositionSizer:
    def _sizer(self, result: Any, **config: Any) -> IntegratedPositionSizer:
        return IntegratedPositionSizer(
            StubKelly(result), ConfidenceSizer(), SimpleNamespace(**config)
        )

    def test_scalar_kelly_times_confidence(self) -> None:
        out = self._sizer(4.0).calculate_position_size(int_signal(), 10_000.0)
        assert out.effective_size == pytest.approx(2.0)

    def test_dict_kelly_result(self) -> None:
        out = self._sizer({"position_size": 6.0}).calculate_position_size(
            int_signal(confidence=1.0), 10_000.0
        )
        assert out.effective_size == pytest.approx(6.0)

    def test_kelly_error_falls_back_to_two_percent(self) -> None:
        # 2% of 10_000 = 200 / 100 = 2 contracts * 0.5 confidence = 1.0
        out = self._sizer(RuntimeError("x")).calculate_position_size(
            int_signal(), 10_000.0
        )
        assert out.effective_size == pytest.approx(1.0)

    def test_hard_cap_at_ten_percent_of_account(self) -> None:
        # 10% of 10_000 = 1_000 / 100 = 10 contracts
        out = self._sizer(500.0).calculate_position_size(
            int_signal(confidence=1.0), 10_000.0
        )
        assert out.effective_size == pytest.approx(10.0)

    def test_cap_exactly_reached_is_kept(self) -> None:
        out = self._sizer(10.0).calculate_position_size(
            int_signal(confidence=1.0), 10_000.0
        )
        assert out.effective_size == pytest.approx(10.0)

    def test_minimum_size_floor(self) -> None:
        out = self._sizer(0.0).calculate_position_size(int_signal(), 10_000.0)
        assert out.effective_size == 0.001
        out = self._sizer(0.0, min_position_size=0.5).calculate_position_size(
            int_signal(), 10_000.0
        )
        assert out.effective_size == 0.5

    def test_zero_entry_price_collapses_to_the_floor(self) -> None:
        out = self._sizer(4.0).calculate_position_size(
            int_signal(entry_price=0.0), 10_000.0
        )
        assert out.effective_size == 0.001

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "IntegratedPositionSizer calls kelly.calculate_position_size("
            "strategy_type=, entry_price=, stop_loss=, account_balance=) but "
            "KellyPositionSizer takes (signal, account_balance, account_id).  "
            "The TypeError is swallowed and every size silently comes from the "
            "2% fallback, never from Kelly."
        ),
    )
    def test_real_kelly_sizer_is_actually_consulted(self) -> None:
        real = kelly([100.0] * 40 + [-50.0] * 10)  # capped 10% -> 20 contracts
        sizer = IntegratedPositionSizer(real, ConfidenceSizer(), SimpleNamespace())
        out = sizer.calculate_position_size(int_signal(confidence=1.0), 10_000.0)
        # Kelly base = 20 contracts; hard cap 10% -> 10, not the 2% fallback 2
        assert out.base_size == pytest.approx(20.0)
        assert out.effective_size == pytest.approx(10.0)

    @pytest.mark.xfail(
        strict=True,
        raises=AttributeError,
        reason=(
            "IntegratedPositionSizer logs signal.symbol, but models.Signal names "
            "the field 'asset', so sizing a real Signal raises AttributeError."
        ),
    )
    def test_accepts_a_real_signal(self) -> None:
        out = self._sizer(4.0).calculate_position_size(sig(confidence=0.5), 10_000.0)
        assert out.effective_size == pytest.approx(2.0)


# ======================================================================
# exit_sizing
# ======================================================================


class TestNormalizers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("btc-perp", "BTC"), ("ETH-USDT", "ETH"), ("sol-usd", "SOL"), (None, "")],
    )
    def test_symbol(self, raw: Any, expected: str) -> None:
        assert normalize_symbol(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("long", "long"),
            ("LONG", "long"),
            ("bid", "long"),
            (OrderSide.BUY, "long"),
            (PositionSide.SHORT, "short"),
            ("sell", "short"),
            ("ask", "short"),
            (None, None),
            ("sideways", None),
        ],
    )
    def test_side(self, raw: Any, expected: Optional[str]) -> None:
        assert normalize_position_side(raw) == expected


class TestRemainingExchangeQuantity:
    def test_sums_matching_rows_only(self) -> None:
        client = FakeRawClient(
            [
                {"symbol": "BTC-PERP", "side": "long", "amount": "1.5"},
                {"symbol": "BTC", "side": "LONG", "size": 0.5},
                {"symbol": "BTC", "side": "short", "amount": "9"},
                {"symbol": "ETH", "side": "long", "amount": "9"},
            ]
        )
        assert remaining_exchange_quantity(client, "btc", "buy") == pytest.approx(2.0)

    def test_dataclass_positions(self) -> None:
        client = SimpleNamespace(
            get_positions=lambda: [
                position("BTC", PositionSide.SHORT, 3.0),
                position("BTC", PositionSide.LONG, 1.0),
            ]
        )
        assert remaining_exchange_quantity(client, "BTC", "short") == 3.0

    def test_confirmed_flat_is_zero(self) -> None:
        assert remaining_exchange_quantity(FakeRawClient([]), "BTC", "long") == 0.0

    def test_transport_failure_is_unknown(self) -> None:
        client = FakeRawClient(ConnectionError("down"))
        assert remaining_exchange_quantity(client, "BTC", "long") is None

    def test_non_list_payload_is_unknown(self) -> None:
        client = FakeRawClient({"error": "x"})
        assert remaining_exchange_quantity(client, "BTC", "long") is None

    def test_unknown_side_is_unknown(self) -> None:
        assert remaining_exchange_quantity(FakeRawClient([]), "BTC", "flat") is None

    def test_unparseable_quantity_counts_as_zero(self) -> None:
        client = FakeRawClient([{"symbol": "BTC", "side": "long", "amount": "n/a"}])
        assert remaining_exchange_quantity(client, "BTC", "long") == 0.0

    def test_negative_quantities_are_taken_absolute(self) -> None:
        client = FakeRawClient([{"symbol": "BTC", "side": "long", "qty": -2}])
        assert remaining_exchange_quantity(client, "BTC", "long") == 2.0


class TestPlanCloseQuantity:
    def test_exact_match_sends_local_quantity(self) -> None:
        client = FakeRawClient([{"symbol": "BTC", "side": "long", "amount": "2"}])
        plan = plan_close_quantity(client, "BTC", "long", 2.0)
        assert (plan.quantity, plan.exchange_quantity) == (2.0, 2.0)
        assert plan.already_flat is False and plan.clamped is False

    def test_local_above_exchange_is_clamped(self) -> None:
        client = FakeRawClient([{"symbol": "BTC", "side": "long", "amount": "1.5"}])
        plan = plan_close_quantity(client, "BTC", "long", 2.0)
        assert plan.quantity == 1.5 and plan.clamped is True

    def test_local_below_exchange_is_kept(self) -> None:
        client = FakeRawClient([{"symbol": "BTC", "side": "long", "amount": "5"}])
        plan = plan_close_quantity(client, "BTC", "long", 2.0)
        assert plan.quantity == 2.0 and plan.clamped is False

    def test_flat_exchange_sends_nothing(self) -> None:
        plan = plan_close_quantity(FakeRawClient([]), "BTC", "long", 2.0)
        assert plan.quantity == 0.0 and plan.already_flat is True
        assert plan.clamped is True

    def test_flat_both_sides_is_not_a_clamp(self) -> None:
        plan = plan_close_quantity(FakeRawClient([]), "BTC", "long", 0.0)
        assert plan.already_flat is True and plan.clamped is False

    def test_unknown_exchange_keeps_local_quantity(self) -> None:
        client = FakeRawClient(RuntimeError("down"))
        plan = plan_close_quantity(client, "BTC", "long", 2.0)
        assert plan.quantity == 2.0 and plan.exchange_quantity is None
        assert plan.already_flat is False and plan.clamped is False

    @pytest.mark.parametrize("local", [None, -3.0, 0.0])
    def test_non_positive_local_quantity_reads_as_zero(self, local: Any) -> None:
        client = FakeRawClient([{"symbol": "BTC", "side": "long", "amount": "2"}])
        assert plan_close_quantity(client, "BTC", "long", local).quantity == 0.0


# ======================================================================
# models: sizing arithmetic on Signal and Position
# ======================================================================


class TestSignalMath:
    def test_stop_distance_and_rrr(self) -> None:
        s = sig(100.0, 95.0, take_profit=110.0)
        assert s.stop_distance_pct == pytest.approx(0.05)
        assert s.rrr == pytest.approx(2.0)

    def test_stopless_signal_has_no_risk_denominator(self) -> None:
        s = sig(100.0, None, take_profit=110.0)
        assert s.stop_distance_pct == 0.0 and s.rrr == 0.0

    def test_zero_entry_price_is_zero_distance(self) -> None:
        assert sig(0.0, 95.0).stop_distance_pct == 0.0

    def test_missing_target_or_zero_risk_is_zero_rrr(self) -> None:
        assert sig(100.0, 95.0, take_profit=None).rrr == 0.0
        assert sig(100.0, 100.0, take_profit=110.0).rrr == 0.0

    def test_validity_flags(self) -> None:
        assert make_signal().is_valid() is True
        bad = make_signal(all_flags=False, account_risk_ok=True)
        assert bad.is_valid() is False
        assert "account_risk_ok" not in bad.failed_validity_flags()
        assert len(bad.failed_validity_flags()) == 7


def _position(side: OrderSide = OrderSide.BUY, leverage: int = 10, stop: float = 95.0):
    return Position(
        id="p1",
        asset="BTC",
        asset_class=AssetClass.CRYPTO,
        side=side,
        entry_price=100.0,
        quantity=1.0,
        margin_used=10.0,
        leverage=leverage,
        stop_loss=stop,
    )


class TestPositionMath:
    def test_long_liquidation_price(self) -> None:
        assert _position().liquidation_price == pytest.approx(90.0)

    def test_short_liquidation_price(self) -> None:
        assert _position(OrderSide.SELL).liquidation_price == pytest.approx(110.0)

    def test_unlevered_position_cannot_be_liquidated(self) -> None:
        pos = _position(leverage=1)
        assert pos.liquidation_price == 0.0
        assert pos.liquidation_buffer_pct == float("inf")
        assert pos.is_liquidation_risk() is False

    def test_liquidation_buffer_and_risk_flag(self) -> None:
        # 10x -> 10% to liquidation; 5% stop leaves a 5% buffer
        pos = _position()
        assert pos.liquidation_buffer_pct == pytest.approx(0.05)
        assert pos.is_liquidation_risk() is False
        tight = _position(stop=91.0)  # 9% stop -> 1% buffer < 1.5%
        assert tight.is_liquidation_risk() is True

    def test_margin_drawdown_if_stopped(self) -> None:
        # notional 100, 5% stop -> 5 lost on 10 margin = 50%
        assert _position().margin_drawdown_pct == pytest.approx(0.5)

    def test_unrealized_pnl_by_side(self) -> None:
        long, short = _position(), _position(OrderSide.SELL)
        long.update_unrealized_pnl(105.0)
        short.update_unrealized_pnl(105.0)
        assert long.unrealized_pnl == 5.0 and short.unrealized_pnl == -5.0
