"""
Tests for VWAPPullbackStrategy (trend-side session-VWAP continuation).

Fixtures build a session that rallies away from VWAP, pulls back into
the band, and closes back on the trend side - then perturb one condition
at a time and assert the signal disappears.
"""

from datetime import datetime, timedelta, timezone

import pytest

from trading_bot_v2.config import StrategyType
from trading_bot_v2.models import OrderSide
from trading_bot_v2.strategies.vwap_pullback import VWAPPullbackStrategy


SESSION_DAY = datetime(2024, 6, 15, tzinfo=timezone.utc)


def _bars_15m(specs):
    """Build a 15m bundle from (open, high, low, close, volume) tuples."""
    data = {"open": [], "high": [], "low": [], "close": [], "volume": [], "timestamp": []}
    for i, (o, h, l, c, v) in enumerate(specs):
        ts = SESSION_DAY + timedelta(minutes=15 * i)
        data["open"].append(o)
        data["high"].append(h)
        data["low"].append(l)
        data["close"].append(c)
        data["volume"].append(v)
        data["timestamp"].append(ts.strftime("%Y-%m-%dT%H:%M:%S"))
    return data


def _tf_4h(closes):
    return {
        "open": list(closes),
        "high": [c + 1.0 for c in closes],
        "low": [c - 1.0 for c in closes],
        "close": list(closes),
        "volume": [1000.0] * len(closes),
    }


BULL_4H = _tf_4h([80.0 + 0.5 * i for i in range(60)])
BEAR_4H = _tf_4h([140.0 - 0.5 * i for i in range(60)])
FLAT_4H = _tf_4h([100.0] * 60)


def _long_session():
    """Rally 100->110, drift back, resumption bar dips to VWAP and closes up."""
    specs = []
    # Rally: 10 bars climbing to 110
    for i in range(10):
        base = 100.0 + i
        specs.append((base, base + 1.2, base - 0.3, base + 1.0, 100.0))
    # Pullback: 9 bars drifting down toward the mean
    for i in range(9):
        base = 110.0 - 0.7 * i
        specs.append((base, base + 0.4, base - 0.9, base - 0.6, 100.0))
    # Resumption bar: deep dip to 100 (through any plausible VWAP band),
    # bullish close well above VWAP
    specs.append((104.0, 108.5, 100.0, 108.0, 100.0))
    return _bars_15m(specs)


def _short_session():
    """Mirror: sell-off 100->90, drift back up, resumption closes down."""
    specs = []
    for i in range(10):
        base = 100.0 - i
        specs.append((base, base + 0.3, base - 1.2, base - 1.0, 100.0))
    for i in range(9):
        base = 90.0 + 0.7 * i
        specs.append((base, base + 0.9, base - 0.4, base + 0.6, 100.0))
    specs.append((96.0, 100.0, 91.5, 92.0, 100.0))
    return _bars_15m(specs)


def _strategy(**overrides):
    params = dict(band_sd=0.25, extension_min_sd=1.0, cooldown_hours=4.0)
    params.update(overrides)
    strategy = VWAPPullbackStrategy(**params)
    # Sim time just after the last fixture bar
    strategy._sim_time = SESSION_DAY + timedelta(minutes=15 * 20)
    return strategy


class TestLongSetup:
    def test_emits_long_signal_under_bullish_stack(self):
        strategy = _strategy()
        signals = strategy.generate_signals(
            "BTC-USDC", {"15m": _long_session(), "4h": BULL_4H}, 108.0
        )
        assert len(signals) == 1
        sig = signals[0]
        assert sig.strategy == StrategyType.VWAP_PULLBACK
        assert sig.side == OrderSide.BUY
        assert sig.stop_loss < sig.entry_price < sig.take_profit
        assert sig.indicators["time_exit_hours"] == 24.0
        assert sig.indicators["extension_sd"] >= 1.0
        assert sig.rrr_meets_minimum  # tp_rr 2.0 >= min_rrr 1.5
        assert sig.multi_timeframe_alignment  # true by measurement here

    def test_neutral_stack_blocks_signal(self):
        strategy = _strategy()
        signals = strategy.generate_signals(
            "BTC-USDC", {"15m": _long_session(), "4h": FLAT_4H}, 108.0
        )
        assert signals == []

    def test_bearish_stack_blocks_long_pattern(self):
        # Long pattern under a short bias: no mirrored short setup exists
        # in this data, so nothing fires
        strategy = _strategy()
        signals = strategy.generate_signals(
            "BTC-USDC", {"15m": _long_session(), "4h": BEAR_4H}, 108.0
        )
        assert signals == []

    def test_no_resumption_no_signal(self):
        data = _long_session()
        # Make the last bar close bearish below its open
        data["close"][-1] = data["open"][-1] - 1.0
        strategy = _strategy()
        signals = strategy.generate_signals(
            "BTC-USDC", {"15m": data, "4h": BULL_4H}, 103.0
        )
        assert signals == []

    def test_no_band_touch_no_signal(self):
        data = _long_session()
        # Last bar never comes near VWAP
        data["low"][-1] = 109.0
        data["open"][-1] = 109.2
        data["close"][-1] = 110.0
        data["high"][-1] = 110.2
        strategy = _strategy()
        signals = strategy.generate_signals(
            "BTC-USDC", {"15m": data, "4h": BULL_4H}, 110.0
        )
        assert signals == []

    def test_no_extension_no_signal(self):
        # Flat session: nothing to pull back from
        specs = [(100.0, 100.4, 99.6, 100.1, 100.0)] * 20
        strategy = _strategy()
        signals = strategy.generate_signals(
            "BTC-USDC", {"15m": _bars_15m(specs), "4h": BULL_4H}, 100.0
        )
        assert signals == []


class TestShortSetup:
    def test_emits_short_signal_under_bearish_stack(self):
        strategy = _strategy()
        signals = strategy.generate_signals(
            "BTC-USDC", {"15m": _short_session(), "4h": BEAR_4H}, 92.0
        )
        assert len(signals) == 1
        sig = signals[0]
        assert sig.side == OrderSide.SELL
        assert sig.take_profit < sig.entry_price < sig.stop_loss

    def test_enable_short_false_blocks_it(self):
        strategy = _strategy(enable_short=False)
        signals = strategy.generate_signals(
            "BTC-USDC", {"15m": _short_session(), "4h": BEAR_4H}, 92.0
        )
        assert signals == []


class TestGuards:
    def test_missing_timestamps_disables_symbol(self):
        data = _long_session()
        del data["timestamp"]
        strategy = _strategy()
        signals = strategy.generate_signals(
            "BTC-USDC", {"15m": data, "4h": BULL_4H}, 108.0
        )
        assert signals == []

    def test_cooldown_blocks_second_signal(self):
        strategy = _strategy()
        market = {"15m": _long_session(), "4h": BULL_4H}
        assert len(strategy.generate_signals("BTC-USDC", market, 108.0)) == 1
        assert strategy.generate_signals("BTC-USDC", market, 108.0) == []
        # After the cooldown expires it can fire again
        strategy._sim_time = strategy._sim_time + timedelta(hours=5)
        assert len(strategy.generate_signals("BTC-USDC", market, 108.0)) == 1

    def test_too_few_session_bars_no_signal(self):
        specs = [(100.0, 105.0, 99.0, 104.0, 100.0)] * 4
        strategy = _strategy()
        signals = strategy.generate_signals(
            "BTC-USDC", {"15m": _bars_15m(specs), "4h": BULL_4H}, 104.0
        )
        assert signals == []

    def test_rvol_floor_requires_history(self):
        # rvol_min on with only 20 bars of history (< window+1) -> skip
        strategy = _strategy(rvol_min=1.0, rvol_window=96)
        signals = strategy.generate_signals(
            "BTC-USDC", {"15m": _long_session(), "4h": BULL_4H}, 108.0
        )
        assert signals == []

    def test_rvol_floor_blocks_quiet_resumption(self):
        strategy = _strategy(rvol_min=1.5, rvol_window=10)
        data = _long_session()  # all volumes equal -> rvol 1.0 < 1.5
        signals = strategy.generate_signals(
            "BTC-USDC", {"15m": data, "4h": BULL_4H}, 108.0
        )
        assert signals == []

    def test_rvol_floor_passes_heavy_resumption(self):
        strategy = _strategy(rvol_min=1.5, rvol_window=10)
        data = _long_session()
        data["volume"][-1] = 200.0  # rvol 2.0 vs median 100
        signals = strategy.generate_signals(
            "BTC-USDC", {"15m": data, "4h": BULL_4H}, 108.0
        )
        assert len(signals) == 1
        assert signals[0].indicators["rvol"] == pytest.approx(2.0)
