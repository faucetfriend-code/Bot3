"""Offline regressions for order lifecycle, replay timing and net metrics."""

from datetime import datetime, timedelta
import math

import pytest

from trading_bot_v2.backtesting.cost_model import CostTable
from trading_bot_v2.backtesting.engine import BacktestEngine
from trading_bot_v2.backtesting.performance import PerformanceTracker
from trading_bot_v2.backtesting.simulated_exchange import SimulatedExchange
from trading_bot_v2.config import AssetClass, StrategyType
from trading_bot_v2.models import OrderSide, Signal

SYMBOL = "BTC-USDC"


def exchange(capital=1000.0, fee=0.001):
    costs = CostTable(
        profile="legacy",
        base_maker_fee_pct=fee,
        base_taker_fee_pct=fee,
        base_slippage_pct=0.0,
        read_env_overrides=False,
    )
    result = SimulatedExchange(
        initial_capital=capital,
        cost_table=costs,
        funding_hourly_pct=0.0,
        seed=0,
    )
    result.advance(bar(100), "2024-01-01T00:00:00")
    return result


def bar(price, high=None, low=None):
    return {
        "open": price,
        "high": high or price,
        "low": low or price,
        "close": price,
        "volume": 1000,
    }


@pytest.mark.parametrize("capital,quantity", [(100, "2"), (100, "1")])
def test_unaffordable_market_entry_has_no_fill_fee_or_position(capital, quantity):
    ex = exchange(capital)
    result = ex.place_order(SYMBOL, "bid", quantity)
    order = ex._orders[result["order_id"]]
    assert order.status != "filled"
    assert order.filled_qty == 0
    assert order.fee == 0
    assert ex.balance == capital
    assert ex.trade_log == []
    assert ex.get_positions() == []


def test_unaffordable_add_keeps_existing_position_and_cash():
    ex = exchange(150)
    ex.place_order(SYMBOL, "bid", "1")
    before = ex.balance
    result = ex.place_order(SYMBOL, "bid", "1")
    assert ex._orders[result["order_id"]].status != "filled"
    assert ex.balance == before
    assert ex._positions[SYMBOL].quantity == 1
    assert len(ex.trade_log) == 1


def test_unaffordable_flip_is_rejected_atomically():
    ex = exchange(150)
    ex.place_order(SYMBOL, "bid", "1")
    before = ex.balance
    result = ex.place_order(SYMBOL, "ask", "3")
    assert ex._orders[result["order_id"]].status != "filled"
    assert ex.balance == before
    assert ex._positions[SYMBOL].side == "long"
    assert ex._positions[SYMBOL].quantity == 1
    assert len(ex.trade_log) == 1


def signal(side, entry, quantity=1):
    long = side == OrderSide.BUY
    return Signal(
        strategy=StrategyType.MEAN_REVERSION,
        asset=SYMBOL,
        asset_class=AssetClass.CRYPTO,
        side=side,
        entry_price=entry,
        stop_loss=80 if long else 120,
        take_profit=110 if long else 90,
        quantity=quantity,
        confidence=0.9,
    )


@pytest.mark.parametrize(
    "side,entry,away",
    [
        (OrderSide.BUY, 90, bar(110, 111, 99)),
        (OrderSide.SELL, 110, bar(90, 101, 89)),
    ],
)
def test_take_profit_cannot_open_position_before_resting_entry(side, entry, away):
    ex = exchange()
    engine = BacktestEngine()
    assert engine._execute_signal(signal(side, entry), ex, candle_idx=0)
    ex.advance(away, "2024-01-01T00:05:00")
    assert ex.get_positions() == []
    assert ex.trade_log == []
    assert ex.balance == 1000


def test_engine_rejected_market_entry_places_no_exit_orders():
    ex = exchange(100)
    engine = BacktestEngine()
    assert not engine._execute_signal(signal(OrderSide.BUY, 100, 2), ex, 0)
    assert not [order for order in ex._orders.values() if order.status == "open"]
    assert ex.trade_log == []


def test_resting_entry_exits_activate_on_following_candle():
    ex = exchange(fee=0)
    engine = BacktestEngine()
    assert engine._execute_signal(signal(OrderSide.BUY, 90), ex, 0)
    # The path through this candle is unknown, so its earlier high cannot
    # activate a take-profit attached to an entry filled later in the bar.
    ex.advance(bar(100, 111, 89), "2024-01-01T00:05:00")
    assert len(ex.trade_log) == 1
    assert ex._positions[SYMBOL].side == "long"
    ex.advance(bar(110, 111, 100), "2024-01-01T00:10:00")
    assert len(ex.trade_log) == 2
    assert ex.get_positions() == []


@pytest.mark.parametrize(
    "side,entry,stop,close",
    [
        (OrderSide.BUY, 90, 85, 82),
        (OrderSide.SELL, 110, 115, 118),
    ],
)
def test_entry_bar_certain_stop_is_executed(side, entry, stop, close):
    ex = exchange(fee=0)
    engine = BacktestEngine()
    entry_signal = signal(side, entry)
    entry_signal.stop_loss = stop
    assert engine._execute_signal(entry_signal, ex, 0)
    candle = {
        "open": 100,
        "high": max(101, close + 2),
        "low": min(99, close - 2),
        "close": close,
        "volume": 1000,
    }
    ex.advance(candle, "2024-01-01T00:05:00")
    assert ex.get_positions() == []
    assert len(ex.trade_log) == 2
    assert ex.trade_log[-1]["pnl"] == pytest.approx(-5)


def test_rejected_pyramid_add_preserves_existing_stop():
    ex = exchange(150, fee=0)
    engine = BacktestEngine()
    engine._max_pyramid_entries = 2
    assert engine._execute_signal(signal(OrderSide.BUY, 100), ex, 0)
    assert not engine._execute_signal(signal(OrderSide.BUY, 100), ex, 1)
    ex.advance(bar(75, 79, 74), "2024-01-01T00:05:00")
    assert ex.get_positions() == []
    assert len(ex.trade_log) == 2


def test_pending_pyramid_add_keeps_existing_position_protected():
    ex = exchange(fee=0)
    engine = BacktestEngine()
    engine._max_pyramid_entries = 2
    assert engine._execute_signal(signal(OrderSide.BUY, 100), ex, 0)
    engine._execute_signal(signal(OrderSide.BUY, 70), ex, 1)
    # Original stop80 triggers but add70 does not.
    ex.advance(bar(79, 79, 78), "2024-01-01T00:05:00")
    assert ex.get_positions() == []


def test_filled_pyramid_add_stop_covers_entire_position():
    ex = exchange(fee=0)
    engine = BacktestEngine()
    engine._max_pyramid_entries = 2
    assert engine._execute_signal(signal(OrderSide.BUY, 100), ex, 0)
    assert engine._execute_signal(signal(OrderSide.BUY, 90), ex, 1)
    ex.advance(bar(90, 95, 89), "2024-01-01T00:05:00")
    assert ex._positions[SYMBOL].quantity == 2
    ex.advance(bar(79), "2024-01-01T00:10:00")
    assert ex.get_positions() == []


def test_reduce_only_exit_clamps_quantity_after_partial_close():
    ex = exchange()
    ex.place_order(SYMBOL, "bid", "2")
    result = ex.place_order(
        SYMBOL,
        "ask",
        "2",
        order_type="limit",
        price=110,
        reduce_only=True,
    )
    ex.place_order(SYMBOL, "ask", "1")
    ex.advance(bar(110), "2024-01-01T00:05:00")
    order = ex._orders[result["order_id"]]
    assert order.filled_qty == 1
    assert order.fee == pytest.approx(0.11)
    assert ex.trade_log[-1]["quantity"] == 1
    assert ex.get_positions() == []


def test_reduce_only_order_on_empty_position_cannot_open_exposure():
    ex = exchange()
    ex.place_order(SYMBOL, "ask", "1", reduce_only=True)
    assert ex.get_positions() == []
    assert ex.trade_log == []
    assert ex.balance == 1000


def test_partial_closes_allocate_entry_fees_once_per_closed_unit():
    ex = exchange(fee=0.01)
    ex.place_order(SYMBOL, "bid", "2")
    ex.place_order(SYMBOL, "ask", "1")
    ex.place_order(SYMBOL, "ask", "1")
    closes = ex.trade_log[1:]
    assert [trade["closed_qty"] for trade in closes] == [1, 1]
    assert [trade["net_pnl"] for trade in closes] == pytest.approx([-2, -2])
    assert sum(trade["net_pnl"] for trade in closes) == pytest.approx(
        ex.equity() - 1000
    )


@pytest.mark.parametrize(
    "side,exit_side,stop,gap",
    [
        ("bid", "ask", 95, 80),
        ("ask", "bid", 105, 120),
    ],
)
def test_stop_that_gaps_fills_at_open_instead_of_unreachable_stop(
    side, exit_side, stop, gap
):
    ex = exchange(fee=0)
    ex.place_order(SYMBOL, side, "1")
    result = ex.place_order(SYMBOL, exit_side, "1", order_type="stop", price=stop)
    ex.advance(bar(gap, gap + 2, gap - 2), "2024-01-01T00:05:00")
    assert ex._orders[result["order_id"]].fill_price == gap
    assert ex.get_positions() == []


def test_history_before_first_candle_is_empty():
    timestamps = ["2024-01-01T04:00:00"]
    index = BacktestEngine._nearest_idx(
        {timestamps[0]: 0}, timestamps, "2024-01-01T03:00:00"
    )
    assert index == -1
    assert BacktestEngine._history({"close": [999]}, index, 100)["close"] == []


@pytest.mark.parametrize(
    "minutes,replay,expected",
    [
        (240, "2024-01-01T03:50:00", -1),
        (240, "2024-01-01T03:55:00", 0),
        (240, "2024-01-01T04:00:00", 0),
        (240, "2024-01-01T07:55:00", 1),
        (60, "2024-01-01T00:50:00", -1),
        (60, "2024-01-01T00:55:00", 0),
        (15, "2024-01-01T00:05:00", -1),
        (15, "2024-01-01T00:10:00", 0),
    ],
)
def test_only_completed_higher_timeframe_candles_are_visible(minutes, replay, expected):
    timestamps = ["2024-01-01T00:00:00", "2024-01-01T04:00:00"]
    mapping = dict(zip(timestamps, range(2)))
    assert (
        BacktestEngine._completed_idx(mapping, timestamps, replay, minutes) == expected
    )


def test_risk_ratios_use_actual_five_hour_snapshot_spacing():
    values = [1000, 1010, 1005, 1030]
    tracker = PerformanceTracker(1000)
    for i, value in enumerate(values):
        timestamp = datetime(2024, 1, 1) + timedelta(hours=5 * i)
        tracker.record_snapshot(timestamp.isoformat(), value, {})
    result = tracker.finalise(values[-1], [], SYMBOL, "2024-01-01", "2024-02-01")
    returns = [(b - a) / a for a, b in zip(values, values[1:])]
    mean = sum(returns) / len(returns)
    std = math.sqrt(sum((r - mean) ** 2 for r in returns) / len(returns))
    assert result.sharpe_ratio == pytest.approx(mean / std * math.sqrt(8760 / 5))
    assert result.sortino_ratio == pytest.approx(
        mean / abs(returns[1]) * math.sqrt(8760 / 5)
    )


@pytest.mark.parametrize("closing_price", [100, 100.1])
def test_breakeven_and_small_gross_gain_closures_are_counted_as_net_losses(
    closing_price,
):
    ex = exchange()
    ex.place_order(SYMBOL, "bid", "1")
    ex.advance(bar(closing_price), "2024-01-01T00:05:00")
    ex.place_order(SYMBOL, "ask", "1")
    tracker = PerformanceTracker(1000)
    result = tracker.finalise(
        ex.equity(), ex.trade_log, SYMBOL, "2024-01-01", "2024-02-01"
    )
    assert result.closed_trades == 1
    assert result.win_rate_pct == 0
    assert result.profit_factor == 0
