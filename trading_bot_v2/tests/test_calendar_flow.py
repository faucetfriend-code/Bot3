"""
Tests for the Calendar Flow (turn-of-the-month) strategy.

Covers: window-boundary math including month-end edge cases (February,
leap year, 31-day months, Dec->Jan year rollover), the long window
firing once per month per symbol, dedup across repeated calls, the
optional mid-month short window (disabled by default), time_exit_hours
matching the remaining window length, graceful no-op when timestamps
are absent, ATR stop / nominal TP math, and constructor validation.
"""

from datetime import datetime, timedelta, timezone

import pytest

from trading_bot_v2.models import OrderSide
from trading_bot_v2.config import StrategyType
from trading_bot_v2.strategies.calendar_flow import CalendarFlowStrategy


BASE = 100.0


def make_4h(
    num_candles=30,
    high=102.0,
    low=98.0,
    close=100.0,
    start=datetime(2024, 1, 20, 0, 0, tzinfo=timezone.utc),
    with_timestamps=True,
):
    """
    Build synthetic 4h candles with a constant true range.

    With high=102, low=98 and close=100 every candle, the true range is
    a constant 4.0, so ATR(14) == 4.0 exactly.
    """
    data = {
        "high": [high] * num_candles,
        "low": [low] * num_candles,
        "close": [close] * num_candles,
        "open": [close] * num_candles,
        "volume": [1000.0] * num_candles,
    }
    if with_timestamps:
        data["timestamp"] = [
            int((start + timedelta(hours=4 * i)).timestamp() * 1000)
            for i in range(num_candles)
        ]
    return data


def make_strategy(**overrides):
    """Build a strategy with the documented defaults."""
    params = {
        "long_entry_day": -2,
        "long_exit_day": 3,
        "enable_short": False,
        "short_entry_dom": 10,
        "short_exit_dom": 15,
        "atr_stop_mult": 3.0,
        "confidence": 0.6,
    }
    params.update(overrides)
    return CalendarFlowStrategy(**params)


def set_now(strategy, dt):
    """Inject simulated time (naive, mimicking the backtest engine)."""
    strategy._sim_time = dt.replace(tzinfo=None)


def signals_at(strategy, dt, symbol="SUI-USDC", data=None, price=BASE):
    """Generate signals at a simulated time with default 4h data."""
    set_now(strategy, dt)
    if data is None:
        data = make_4h()
    return strategy.generate_signals(symbol, {"4h": data}, price)


class TestWindowBoundaryMath:
    """_active_window resolves the correct window and anchor month."""

    def _window(self, dt, **overrides):
        strategy = make_strategy(**overrides)
        return strategy._active_window(dt)

    def test_31_day_month_window_opens_two_days_before_boundary(self):
        # Feb 1 boundary - 2 days = Jan 30 00:00
        assert self._window(datetime(2024, 1, 30, 0, 0, tzinfo=timezone.utc))
        assert (
            self._window(datetime(2024, 1, 29, 23, 59, tzinfo=timezone.utc))
            is None
        )

    def test_window_spans_boundary_with_single_anchor(self):
        # Late Jan and early Feb resolve to the SAME window (anchor 2024-02)
        w_late = self._window(datetime(2024, 1, 31, 12, 0, tzinfo=timezone.utc))
        w_early = self._window(datetime(2024, 2, 2, 6, 0, tzinfo=timezone.utc))
        assert w_late is not None and w_early is not None
        assert w_late[0] == w_early[0] == "tom_long"
        assert w_late[1] == w_early[1] == "2024-02"

    def test_window_closes_at_midnight_on_the_fourth(self):
        w = self._window(datetime(2024, 2, 3, 23, 59, tzinfo=timezone.utc))
        assert w is not None
        assert w[4] == datetime(2024, 2, 4, 0, 0, tzinfo=timezone.utc)
        assert (
            self._window(datetime(2024, 2, 4, 0, 0, tzinfo=timezone.utc))
            is None
        )

    def test_february_non_leap_month_end(self):
        # Mar 1 2023 boundary - 2 days = Feb 27 00:00
        assert (
            self._window(datetime(2023, 2, 26, 23, 59, tzinfo=timezone.utc))
            is None
        )
        w = self._window(datetime(2023, 2, 27, 0, 0, tzinfo=timezone.utc))
        assert w is not None and w[1] == "2023-03"

    def test_february_leap_year_month_end(self):
        # Mar 1 2024 boundary - 2 days = Feb 28 00:00 (leap year has Feb 29)
        w = self._window(datetime(2024, 2, 29, 12, 0, tzinfo=timezone.utc))
        assert w is not None and w[1] == "2024-03"
        assert (
            self._window(datetime(2024, 2, 27, 23, 59, tzinfo=timezone.utc))
            is None
        )

    def test_year_rollover_dec_to_jan(self):
        w = self._window(datetime(2024, 12, 30, 0, 0, tzinfo=timezone.utc))
        assert w is not None
        assert w[1] == "2025-01"
        assert w[3] == datetime(2024, 12, 30, 0, 0, tzinfo=timezone.utc)
        assert w[4] == datetime(2025, 1, 4, 0, 0, tzinfo=timezone.utc)

    def test_mid_month_is_outside_all_windows_by_default(self):
        assert (
            self._window(datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc))
            is None
        )

    def test_short_window_when_enabled(self):
        w = self._window(
            datetime(2024, 1, 12, 0, 0, tzinfo=timezone.utc),
            enable_short=True,
        )
        assert w is not None
        assert w[0] == "mid_short" and w[2] == "short"
        assert w[3] == datetime(2024, 1, 10, 0, 0, tzinfo=timezone.utc)
        assert w[4] == datetime(2024, 1, 15, 0, 0, tzinfo=timezone.utc)


class TestLongWindowSignals:
    """Long entries fire once per window per symbol with correct fields."""

    def test_long_fires_inside_window(self):
        strategy = make_strategy()
        signals = signals_at(
            strategy, datetime(2024, 1, 30, 0, 0, tzinfo=timezone.utc)
        )
        assert len(signals) == 1
        s = signals[0]
        assert s.strategy == StrategyType.CALENDAR_FLOW
        assert s.side == OrderSide.BUY
        assert s.timeframe == "4h"
        assert s.confidence == 0.6
        assert s.indicators["window"] == "tom_long"
        assert s.indicators["window_anchor"] == "2024-02"
        # All 8 validation flags set
        assert s.volume_confirmation
        assert s.multi_timeframe_alignment
        assert s.support_resistance_valid
        assert s.rrr_meets_minimum
        assert s.liquidation_buffer_safe
        assert s.account_risk_ok
        assert s.margin_drawdown_ok
        assert s.forbidden_conditions_clear

    def test_no_signal_outside_window(self):
        strategy = make_strategy()
        signals = signals_at(
            strategy, datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
        )
        assert signals == []

    def test_dedup_across_repeated_calls_in_same_window(self):
        strategy = make_strategy()
        first = signals_at(
            strategy, datetime(2024, 1, 30, 0, 0, tzinfo=timezone.utc)
        )
        second = signals_at(
            strategy, datetime(2024, 1, 30, 4, 0, tzinfo=timezone.utc)
        )
        third = signals_at(
            strategy, datetime(2024, 2, 2, 12, 0, tzinfo=timezone.utc)
        )
        assert len(first) == 1
        assert second == []
        assert third == []  # same window across the boundary - still deduped

    def test_fires_again_next_month(self):
        strategy = make_strategy()
        jan = signals_at(
            strategy, datetime(2024, 1, 30, 0, 0, tzinfo=timezone.utc)
        )
        feb = signals_at(
            strategy, datetime(2024, 2, 28, 0, 0, tzinfo=timezone.utc)
        )
        assert len(jan) == 1 and len(feb) == 1
        assert jan[0].indicators["window_anchor"] == "2024-02"
        assert feb[0].indicators["window_anchor"] == "2024-03"

    def test_dedup_is_per_symbol(self):
        strategy = make_strategy()
        dt = datetime(2024, 1, 30, 0, 0, tzinfo=timezone.utc)
        a = signals_at(strategy, dt, symbol="SUI-USDC")
        b = signals_at(strategy, dt, symbol="BTC-USDC")
        assert len(a) == 1 and len(b) == 1

    def test_late_entry_inside_window_still_fires_once(self):
        # First call happens mid-window (bot restarted): entry still fires
        strategy = make_strategy()
        signals = signals_at(
            strategy, datetime(2024, 2, 1, 8, 0, tzinfo=timezone.utc)
        )
        assert len(signals) == 1


class TestShortWindow:
    """Optional mid-month short window."""

    def test_short_disabled_by_default(self):
        strategy = make_strategy()
        signals = signals_at(
            strategy, datetime(2024, 1, 12, 0, 0, tzinfo=timezone.utc)
        )
        assert signals == []

    def test_short_fires_when_enabled(self):
        strategy = make_strategy(enable_short=True)
        signals = signals_at(
            strategy, datetime(2024, 1, 10, 0, 0, tzinfo=timezone.utc)
        )
        assert len(signals) == 1
        s = signals[0]
        assert s.side == OrderSide.SELL
        assert s.indicators["window"] == "mid_short"
        assert s.indicators["window_anchor"] == "2024-01"

    def test_short_deduped_within_month(self):
        strategy = make_strategy(enable_short=True)
        first = signals_at(
            strategy, datetime(2024, 1, 10, 0, 0, tzinfo=timezone.utc)
        )
        second = signals_at(
            strategy, datetime(2024, 1, 13, 0, 0, tzinfo=timezone.utc)
        )
        assert len(first) == 1
        assert second == []


class TestTimeExit:
    """time_exit_hours reflects the remaining window length."""

    def test_time_exit_matches_full_window_length_at_open(self):
        # Window: Jan 30 00:00 -> Feb 4 00:00 = 5 days = 120 hours
        strategy = make_strategy()
        signals = signals_at(
            strategy, datetime(2024, 1, 30, 0, 0, tzinfo=timezone.utc)
        )
        assert len(signals) == 1
        assert signals[0].indicators["time_exit_hours"] == pytest.approx(120.0)

    def test_time_exit_is_remaining_hours_for_late_entry(self):
        strategy = make_strategy()
        signals = signals_at(
            strategy, datetime(2024, 2, 1, 0, 0, tzinfo=timezone.utc)
        )
        assert len(signals) == 1
        assert signals[0].indicators["time_exit_hours"] == pytest.approx(72.0)

    def test_no_entry_with_under_an_hour_remaining(self):
        strategy = make_strategy()
        signals = signals_at(
            strategy, datetime(2024, 2, 3, 23, 30, tzinfo=timezone.utc)
        )
        assert signals == []

    def test_short_window_time_exit(self):
        # Short window: Jan 10 -> Jan 15 = 120 hours
        strategy = make_strategy(enable_short=True)
        signals = signals_at(
            strategy, datetime(2024, 1, 10, 0, 0, tzinfo=timezone.utc)
        )
        assert len(signals) == 1
        assert signals[0].indicators["time_exit_hours"] == pytest.approx(120.0)


class TestStopMath:
    """ATR stop and nominal 2x take profit."""

    def test_long_stop_and_tp(self):
        # Constant TR 4.0 -> ATR(14) == 4.0; stop = 3 x 4 = 12 below entry
        strategy = make_strategy()
        signals = signals_at(
            strategy,
            datetime(2024, 1, 30, 0, 0, tzinfo=timezone.utc),
            price=100.0,
        )
        assert len(signals) == 1
        s = signals[0]
        assert s.indicators["atr"] == pytest.approx(4.0)
        assert s.stop_loss == pytest.approx(100.0 - 12.0)
        assert s.take_profit == pytest.approx(100.0 + 24.0)

    def test_short_stop_and_tp(self):
        strategy = make_strategy(enable_short=True)
        signals = signals_at(
            strategy,
            datetime(2024, 1, 12, 0, 0, tzinfo=timezone.utc),
            price=100.0,
        )
        assert len(signals) == 1
        s = signals[0]
        assert s.stop_loss == pytest.approx(100.0 + 12.0)
        assert s.take_profit == pytest.approx(100.0 - 24.0)

    def test_custom_atr_stop_mult(self):
        strategy = make_strategy(atr_stop_mult=2.0)
        signals = signals_at(
            strategy,
            datetime(2024, 1, 30, 0, 0, tzinfo=timezone.utc),
            price=100.0,
        )
        assert signals[0].stop_loss == pytest.approx(100.0 - 8.0)
        assert signals[0].take_profit == pytest.approx(100.0 + 16.0)

    def test_degenerate_bracket_is_skipped(self):
        # Stop distance (12) exceeds the price (10) -> stop would be negative
        strategy = make_strategy()
        signals = signals_at(
            strategy,
            datetime(2024, 1, 30, 0, 0, tzinfo=timezone.utc),
            price=10.0,
        )
        assert signals == []


class TestDataGuards:
    """Missing/insufficient data never raises - it just yields no signal."""

    def test_no_timestamps_is_noop(self):
        strategy = make_strategy()
        data = make_4h(with_timestamps=False)
        set_now(strategy, datetime(2024, 1, 30, 0, 0, tzinfo=timezone.utc))
        signals = strategy.generate_signals("SUI-USDC", {"4h": data}, BASE)
        assert signals == []
        # Second call also silent (warn-once bookkeeping)
        signals = strategy.generate_signals("SUI-USDC", {"4h": data}, BASE)
        assert signals == []
        assert "SUI-USDC" in strategy._warned_no_timestamp

    def test_no_4h_data_is_noop(self):
        strategy = make_strategy()
        set_now(strategy, datetime(2024, 1, 30, 0, 0, tzinfo=timezone.utc))
        assert strategy.generate_signals("SUI-USDC", {}, BASE) == []

    def test_insufficient_history_for_atr_is_noop(self):
        strategy = make_strategy()
        data = make_4h(num_candles=10)
        set_now(strategy, datetime(2024, 1, 30, 0, 0, tzinfo=timezone.utc))
        assert strategy.generate_signals("SUI-USDC", {"4h": data}, BASE) == []

    def test_nonpositive_price_is_noop(self):
        strategy = make_strategy()
        signals = signals_at(
            strategy,
            datetime(2024, 1, 30, 0, 0, tzinfo=timezone.utc),
            price=0.0,
        )
        assert signals == []


class TestConstructorValidation:
    """Inverted or out-of-range windows are rejected."""

    def test_inverted_long_window_rejected(self):
        with pytest.raises(ValueError):
            make_strategy(long_entry_day=3, long_exit_day=-2)

    def test_inverted_short_window_rejected(self):
        with pytest.raises(ValueError):
            make_strategy(short_entry_dom=15, short_exit_dom=10)

    def test_short_dom_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            make_strategy(short_entry_dom=10, short_exit_dom=30)
