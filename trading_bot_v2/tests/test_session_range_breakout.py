"""
Tests for the Session Range Breakout (ORB) strategy.

Covers: opening range computation for both sessions, range-forming and
entry-window time gates, long/short breakouts with volume confirmation,
stop/TP math (including the mid-range fallback for wide ranges),
per-session trade limits, range sanity filters, graceful handling of
missing timestamps, and the timestamp plumbing added to
MultiTimeframeFetcher._parse_candles.
"""

from datetime import datetime, timedelta, timezone

import pytest

from trading_bot_v2.models import OrderSide
from trading_bot_v2.config import StrategyType
from trading_bot_v2.strategies.session_range_breakout import (
    SessionRangeBreakoutStrategy,
)


BASE = 100.0
UTC_OPEN = datetime(2024, 3, 5, 0, 0, tzinfo=timezone.utc)
US_OPEN = datetime(2024, 3, 5, 13, 30, tzinfo=timezone.utc)


def make_5m(
    session_open,
    num_candles=30,
    range_high=101.0,
    range_low=99.0,
    breakout_close=None,
    breakout_volume=200.0,
    base_volume=100.0,
    filler_wick=1.0,
    ts_as_iso=False,
):
    """
    Build synthetic 5m candles starting at session_open (aware UTC).

    First six candles span [range_low, range_high] (the opening range).
    Middle candles oscillate around mid-range with +/- filler_wick.
    The last candle closes at breakout_close (if given) with
    breakout_volume; all other candles carry base_volume.

    Timestamps are epoch milliseconds by default, or naive ISO-8601
    strings (backtest loader format) when ts_as_iso=True.
    """
    highs, lows, opens, closes, vols, ts = [], [], [], [], [], []
    mid = (range_high + range_low) / 2.0
    for i in range(num_candles):
        t = session_open + timedelta(minutes=5 * i)
        if ts_as_iso:
            ts.append(t.replace(tzinfo=None).isoformat())
        else:
            ts.append(int(t.timestamp() * 1000))
        if i < 6:
            highs.append(range_high)
            lows.append(range_low)
            opens.append(mid)
            closes.append(mid)
            vols.append(base_volume)
        elif i == num_candles - 1 and breakout_close is not None:
            highs.append(max(breakout_close, mid) + 0.1)
            lows.append(min(breakout_close, mid) - 0.1)
            opens.append(mid)
            closes.append(breakout_close)
            vols.append(breakout_volume)
        else:
            highs.append(mid + filler_wick)
            lows.append(mid - filler_wick)
            opens.append(mid)
            closes.append(mid)
            vols.append(base_volume)
    return {
        "high": highs,
        "low": lows,
        "close": closes,
        "open": opens,
        "volume": vols,
        "timestamp": ts,
    }


def make_strategy(**overrides):
    """Build a strategy with test-friendly defaults (UTC session only)."""
    params = {
        "enable_utc_open": True,
        "enable_us_open": False,
        "range_minutes": 30,
        "entry_window_hours": 4.0,
        "volume_mult": 1.5,
        "min_range_pct": 0.15,
        "max_range_pct": 3.0,
        "tp_range_mult": 1.5,
        "time_exit_hours": 4.0,
        "max_trades_per_session": 1,
        "min_rrr": 1.2,
    }
    params.update(overrides)
    return SessionRangeBreakoutStrategy(**params)


def set_now(strategy, dt):
    """Inject simulated time (naive, mimicking the backtest engine)."""
    strategy._sim_time = dt.replace(tzinfo=None)


class TestOpeningRange:
    """Range is built from exactly the first six 5m candles of the session."""

    def test_utc_session_range_uses_first_six_candles(self):
        strategy = make_strategy()
        data = make_5m(UTC_OPEN, breakout_close=101.5)
        # Spike a post-range candle: must NOT extend the range
        data["high"][6] = 105.0
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        signals = strategy.generate_signals("SUI-USDC", {"5m": data}, 101.2)

        assert len(signals) == 1
        assert signals[0].indicators["range_high"] == 101.0
        assert signals[0].indicators["range_low"] == 99.0
        assert signals[0].indicators["session"] == "utc_open"

    def test_us_session_range_uses_first_six_candles(self):
        strategy = make_strategy(enable_utc_open=False, enable_us_open=True)
        data = make_5m(US_OPEN, breakout_close=101.5)
        data["high"][7] = 106.0  # post-range spike must be ignored
        set_now(strategy, US_OPEN + timedelta(minutes=150))

        signals = strategy.generate_signals("SUI-USDC", {"5m": data}, 101.2)

        assert len(signals) == 1
        assert signals[0].indicators["range_high"] == 101.0
        assert signals[0].indicators["range_low"] == 99.0
        assert signals[0].indicators["session"] == "us_open"

    def test_iso_string_timestamps_supported(self):
        """Backtest loader emits naive ISO strings - must parse identically."""
        strategy = make_strategy()
        data = make_5m(UTC_OPEN, breakout_close=101.5, ts_as_iso=True)
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        signals = strategy.generate_signals("SUI-USDC", {"5m": data}, 101.2)

        assert len(signals) == 1
        assert signals[0].side == OrderSide.BUY

    def test_execution_tf_data_kwarg_preferred(self):
        strategy = make_strategy()
        data = make_5m(UTC_OPEN, breakout_close=101.5)
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        signals = strategy.generate_signals(
            "SUI-USDC", {}, 101.2, execution_tf_data={"5m": data}
        )

        assert len(signals) == 1


class TestTimeGates:
    def test_no_signal_while_range_forming(self):
        strategy = make_strategy()
        data = make_5m(UTC_OPEN, num_candles=4)
        set_now(strategy, UTC_OPEN + timedelta(minutes=20))

        assert strategy.generate_signals("SUI-USDC", {"5m": data}, BASE) == []

    def test_no_signal_outside_entry_window(self):
        strategy = make_strategy()
        data = make_5m(UTC_OPEN, num_candles=55, breakout_close=101.5)
        # Window closes at 00:30 + 4h = 04:30; now is 04:35
        set_now(strategy, UTC_OPEN + timedelta(hours=4, minutes=35))

        assert strategy.generate_signals("SUI-USDC", {"5m": data}, 101.2) == []

    def test_signal_allowed_inside_entry_window(self):
        strategy = make_strategy()
        data = make_5m(UTC_OPEN, breakout_close=101.5)
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        assert len(strategy.generate_signals("SUI-USDC", {"5m": data}, 101.2)) == 1


class TestBreakouts:
    def test_long_breakout_with_volume(self):
        strategy = make_strategy()
        data = make_5m(UTC_OPEN, breakout_close=101.5, breakout_volume=200.0)
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        signals = strategy.generate_signals("SUI-USDC", {"5m": data}, 101.2)

        assert len(signals) == 1
        signal = signals[0]
        assert signal.side == OrderSide.BUY
        assert signal.strategy == StrategyType.SESSION_RANGE_BREAKOUT
        assert signal.volume_confirmation is True
        assert signal.timeframe == "5m"
        assert signal.indicators["time_exit_hours"] == 4.0
        assert signal.is_valid()

    def test_short_breakout_with_volume(self):
        strategy = make_strategy()
        data = make_5m(UTC_OPEN, breakout_close=98.5, breakout_volume=200.0)
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        signals = strategy.generate_signals("SUI-USDC", {"5m": data}, 98.8)

        assert len(signals) == 1
        signal = signals[0]
        assert signal.side == OrderSide.SELL
        assert signal.stop_loss == pytest.approx(101.0)  # opposite side of range
        assert signal.take_profit == pytest.approx(99.0 - 1.5 * 2.0)  # 96.0

    def test_breakout_without_volume_no_signal(self):
        """Chosen behavior: weak-volume breakouts emit NO signal at all."""
        strategy = make_strategy()
        data = make_5m(UTC_OPEN, breakout_close=101.5, breakout_volume=100.0)
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        assert strategy.generate_signals("SUI-USDC", {"5m": data}, 101.2) == []

    def test_close_inside_range_no_signal(self):
        strategy = make_strategy()
        data = make_5m(UTC_OPEN, breakout_close=100.5, breakout_volume=200.0)
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        assert strategy.generate_signals("SUI-USDC", {"5m": data}, 100.5) == []


class TestStopAndTarget:
    def test_full_range_stop_and_tp_math(self):
        """Narrow range vs ATR: stop on the opposite side of the range."""
        strategy = make_strategy()
        # filler_wick=1.0 keeps ATR ~= range height, so range < 2x ATR
        data = make_5m(UTC_OPEN, breakout_close=101.5, filler_wick=1.0)
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        signals = strategy.generate_signals("SUI-USDC", {"5m": data}, 101.2)

        assert len(signals) == 1
        signal = signals[0]
        assert signal.indicators["mid_range_stop"] is False
        assert signal.stop_loss == pytest.approx(99.0)
        # TP = breakout level + 1.5 x range height = 101 + 3
        assert signal.take_profit == pytest.approx(104.0)

    def test_mid_range_stop_when_range_exceeds_2x_atr(self):
        strategy = make_strategy()
        # Tight fillers crush ATR below half the range height
        data = make_5m(UTC_OPEN, breakout_close=101.5, filler_wick=0.15)
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        signals = strategy.generate_signals("SUI-USDC", {"5m": data}, 101.2)

        assert len(signals) == 1
        signal = signals[0]
        assert signal.indicators["mid_range_stop"] is True
        assert signal.stop_loss == pytest.approx(100.0)  # mid-range
        assert signal.take_profit == pytest.approx(104.0)

    def test_rrr_flag_reflects_actual_rrr(self):
        strategy = make_strategy()
        data = make_5m(UTC_OPEN, breakout_close=101.5, filler_wick=0.15)
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        signal = strategy.generate_signals("SUI-USDC", {"5m": data}, 101.2)[0]

        risk = abs(signal.entry_price - signal.stop_loss)
        reward = abs(signal.take_profit - signal.entry_price)
        assert signal.rrr_meets_minimum == (reward / risk >= strategy.min_rrr)


class TestSessionLimits:
    def test_one_trade_per_session_enforced(self):
        strategy = make_strategy()
        data = make_5m(UTC_OPEN, breakout_close=101.5)
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        first = strategy.generate_signals("SUI-USDC", {"5m": data}, 101.2)
        assert len(first) == 1

        # Next bar, still breaking out - session limit must block it
        data2 = make_5m(UTC_OPEN, num_candles=31, breakout_close=101.8)
        set_now(strategy, UTC_OPEN + timedelta(minutes=155))
        assert strategy.generate_signals("SUI-USDC", {"5m": data2}, 101.6) == []

    def test_second_session_same_day_allowed(self):
        strategy = make_strategy(enable_us_open=True)
        data = make_5m(UTC_OPEN, breakout_close=101.5)
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))
        assert len(strategy.generate_signals("SUI-USDC", {"5m": data}, 101.2)) == 1

        # US session opens 13:30 same day - fresh range, fresh allowance
        us_data = make_5m(US_OPEN, breakout_close=101.5)
        set_now(strategy, US_OPEN + timedelta(minutes=150))
        signals = strategy.generate_signals("SUI-USDC", {"5m": us_data}, 101.2)
        assert len(signals) == 1
        assert signals[0].indicators["session"] == "us_open"

    def test_per_symbol_isolation(self):
        strategy = make_strategy()
        data = make_5m(UTC_OPEN, breakout_close=101.5)
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        assert len(strategy.generate_signals("SUI-USDC", {"5m": data}, 101.2)) == 1
        assert len(strategy.generate_signals("BTC-USDC", {"5m": data}, 101.2)) == 1


class TestRangeSanity:
    def test_rejects_too_narrow_range(self):
        strategy = make_strategy()
        # 0.1% range < 0.15% minimum
        data = make_5m(
            UTC_OPEN,
            range_high=100.05,
            range_low=99.95,
            breakout_close=100.2,
            filler_wick=0.02,
        )
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        assert strategy.generate_signals("SUI-USDC", {"5m": data}, 100.15) == []

    def test_rejects_too_wide_range(self):
        strategy = make_strategy()
        # 4% range > 3% maximum
        data = make_5m(
            UTC_OPEN,
            range_high=102.0,
            range_low=98.0,
            breakout_close=102.5,
        )
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        assert strategy.generate_signals("SUI-USDC", {"5m": data}, 102.3) == []


class TestMissingTimestamps:
    def test_returns_empty_when_timestamp_key_missing(self):
        strategy = make_strategy()
        data = make_5m(UTC_OPEN, breakout_close=101.5)
        del data["timestamp"]
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        # Graceful [] both times (warns once, no exception)
        assert strategy.generate_signals("SUI-USDC", {"5m": data}, 101.2) == []
        assert strategy.generate_signals("SUI-USDC", {"5m": data}, 101.2) == []

    def test_returns_empty_when_no_5m_data(self):
        strategy = make_strategy()
        set_now(strategy, UTC_OPEN + timedelta(minutes=150))

        assert strategy.generate_signals("SUI-USDC", {}, 101.2) == []


class TestParseCandlesTimestamp:
    """MultiTimeframeFetcher._parse_candles must now emit a timestamp list."""

    @pytest.fixture
    def fetcher(self):
        from trading_bot_v2.multi_timeframe_fetcher import MultiTimeframeFetcher

        return MultiTimeframeFetcher(client=None)

    def test_abbreviated_keys_emit_timestamp(self, fetcher):
        base_ms = int(UTC_OPEN.timestamp() * 1000)
        raw = [
            {
                "t": base_ms + i * 300_000,
                "o": 100.0,
                "h": 101.0,
                "l": 99.0,
                "c": 100.5,
                "v": 1000.0,
            }
            for i in range(5)
        ]

        parsed = fetcher._parse_candles(raw)

        assert "timestamp" in parsed
        assert parsed["timestamp"] == [base_ms + i * 300_000 for i in range(5)]
        assert len(parsed["timestamp"]) == len(parsed["close"])

    def test_full_keys_emit_timestamp(self, fetcher):
        base_ms = int(UTC_OPEN.timestamp() * 1000)
        raw = [
            {
                "timestamp": base_ms + i * 300_000,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
                "volume": 1000.0,
            }
            for i in range(5)
        ]

        parsed = fetcher._parse_candles(raw)

        assert parsed["timestamp"] == [base_ms + i * 300_000 for i in range(5)]

    def test_empty_input_includes_timestamp_key(self, fetcher):
        parsed = fetcher._parse_candles([])
        assert parsed["timestamp"] == []
