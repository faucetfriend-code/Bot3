"""
Tests for MACrossoverStrategy signal generation.

Regression coverage for the rolling-window bug: the strategy used to store
the crossover bar as an absolute index into the caller's history list
(``len(closes) - 1``). Every caller supplies a FIXED-LENGTH rolling window
(backtest engine: 60 candles, live fetcher: up to 250), so once that window
saturated the stored index equalled the current index forever, "bars since
crossover" collapsed to a constant 0, and the ``1 <= bars <= 5`` entry gate
could never be satisfied. The strategy emitted zero signals in both the
backtest and live paths despite detecting hundreds of crossovers.

The tests below drive the strategy through the backtest engine's real
bundle-slicing helper (``BacktestEngine._history``) rather than handing it a
growing list, so the rolling window is exercised exactly as in production.
"""

from typing import Dict, List

import pytest

from trading_bot_v2.backtesting.engine import BacktestEngine
from trading_bot_v2.models import OrderSide
from trading_bot_v2.strategies.ma_crossover import (
    DEFAULT_MAX_ENTRY_BARS,
    DEFAULT_MIN_ENTRY_BARS,
    MACrossoverStrategy,
    validate_entry_window,
)

# The lookback the backtest engine slices 4h history to by default.
ENGINE_LOOKBACK = 60

FAST_MA = 10
SLOW_MA = 30


def _timestamps(count: int) -> List[str]:
    """Canonical 4h ISO timestamps, matching the parquet candle store."""
    from datetime import datetime, timedelta

    base = datetime(2024, 1, 1)
    return [
        (base + timedelta(hours=4 * i)).strftime("%Y-%m-%dT%H:%M:%S")
        for i in range(count)
    ]


def _sma_at(values: List[float], period: int, end: int) -> float:
    """Simple moving average ending at index ``end`` (inclusive)."""
    window = values[max(0, end - period + 1) : end + 1]
    return sum(window) / len(window)


def _first_cross(closes: List[float], fast: int, slow: int, golden: bool) -> int:
    """Index of the first fast/slow SMA cross, computed independently.

    Deliberately does not use the strategy under test, so fixtures stay
    honest about where the crossover actually is.
    """
    for i in range(slow, len(closes)):
        fast_prev, slow_prev = (
            _sma_at(closes, fast, i - 1),
            _sma_at(closes, slow, i - 1),
        )
        fast_now, slow_now = _sma_at(closes, fast, i), _sma_at(closes, slow, i)
        if golden and fast_prev <= slow_prev and fast_now > slow_now:
            return i
        if not golden and fast_prev >= slow_prev and fast_now < slow_now:
            return i
    raise AssertionError("fixture contains no crossover")


def _as_series(closes: List[float]) -> Dict[str, List]:
    """Wrap a close series into a full OHLCV bundle."""
    return {
        "timestamp": _timestamps(len(closes)),
        "open": list(closes),
        "high": [c * 1.004 for c in closes],
        "low": [c * 0.996 for c in closes],
        "close": closes,
        # Volume picks up once the trend turns, so volume confirmation is
        # plausible without being the gate under test.
        "volume": [1000.0 if i < 120 else 2500.0 for i in range(len(closes))],
    }


def _golden_cross_series() -> Dict[str, List]:
    """A 4h series with one golden cross followed by a pullback.

    The retracement is placed 2-4 bars after the crossover - inside the
    strategy's 1-5 bar entry window - by locating the crossover first
    rather than guessing an index.
    """
    closes: List[float] = [100.0 - 0.25 * i for i in range(120)]  # 100 -> 70.25
    rally_base = closes[-1]
    closes += [rally_base + 0.5 * (k + 1) for k in range(40)]  # rally

    cross = _first_cross(closes, FAST_MA, SLOW_MA, golden=True)
    closes = closes[: cross + 2]  # keep the crossover and one bar after it

    peak = closes[-1]
    closes += [peak * 0.96] * 3  # pullback, at crossover + 2..4 bars
    closes += [peak * 0.96 + 0.5 * (k + 1) for k in range(15)]  # rally resumes

    return _as_series(closes)


def _death_cross_series() -> Dict[str, List]:
    """A 4h series with one death cross followed by a rally, mirrored."""
    closes: List[float] = [70.0 + 0.25 * i for i in range(120)]  # 70 -> 99.75
    top = closes[-1]
    closes += [top - 0.5 * (k + 1) for k in range(40)]  # sell-off

    cross = _first_cross(closes, FAST_MA, SLOW_MA, golden=False)
    closes = closes[: cross + 2]

    trough = closes[-1]
    closes += [trough * 1.04] * 3  # rally above the fast MA
    closes += [trough * 1.04 - 0.5 * (k + 1) for k in range(15)]

    return _as_series(closes)


def _replay(
    strategy: MACrossoverStrategy,
    series: Dict[str, List],
    symbol: str = "BTC-USDC",
    lookback: int = ENGINE_LOOKBACK,
):
    """Replay a series bar-by-bar through the engine's rolling-window slicer.

    Returns:
        (signals, bars_since_history) where bars_since_history records the
        strategy's own elapsed-bar count on every bar that had a tracked
        crossover.
    """
    signals = []
    bars_since_history = []
    for i in range(len(series["close"])):
        bundle = BacktestEngine._history(series, i, lookback)
        emitted = strategy.generate_signals(
            symbol=symbol,
            multi_tf_data={"4h": bundle},
            current_price=series["close"][i],
        )
        info = strategy.last_crossover.get(symbol)
        if info is not None:
            elapsed = strategy._bars_since_crossover(
                bundle, info, strategy._observe_bar(symbol, bundle)
            )
            bars_since_history.append(elapsed)
        signals.extend(emitted)
    return signals, bars_since_history


def _permissive_strategy() -> MACrossoverStrategy:
    """Strategy with the non-timing gates widened.

    The pullback band and confidence floor are deliberately relaxed so these
    tests fail only when the crossover TIMING logic is broken, not when
    parameter tuning shifts.
    """
    return MACrossoverStrategy(
        fast_ma_period=FAST_MA,
        slow_ma_period=SLOW_MA,
        pullback_range=(0.0, 0.30),
        min_confidence=0.0,
    )


class TestConfigurableEntryWindow:
    """The post-crossover entry window is tunable, and guarded.

    At 4h resolution the shipped 1-5 band is only 20 hours, which is a
    plausible reason crossovers expire unentered - so it must be
    reachable from config and from the optimizer, without changing the
    default behaviour.
    """

    def test_defaults_are_unchanged(self, monkeypatch):
        """Absent config, the window is still the class constants."""
        monkeypatch.delenv("MA_CROSSOVER_MIN_ENTRY_BARS", raising=False)
        monkeypatch.delenv("MA_CROSSOVER_MAX_ENTRY_BARS", raising=False)
        strategy = MACrossoverStrategy()
        assert strategy.min_entry_bars == MACrossoverStrategy.MIN_ENTRY_BARS
        assert strategy.max_entry_bars == MACrossoverStrategy.MAX_ENTRY_BARS
        assert (strategy.min_entry_bars, strategy.max_entry_bars) == (1, 5)

    def test_constructor_params_win(self, monkeypatch):
        """Explicit arguments override the environment."""
        monkeypatch.setenv("MA_CROSSOVER_MIN_ENTRY_BARS", "3")
        monkeypatch.setenv("MA_CROSSOVER_MAX_ENTRY_BARS", "9")
        strategy = MACrossoverStrategy(min_entry_bars=2, max_entry_bars=18)
        assert (strategy.min_entry_bars, strategy.max_entry_bars) == (2, 18)

    def test_env_is_read(self, monkeypatch):
        """Env keys configure the window when no argument is passed."""
        monkeypatch.setenv("MA_CROSSOVER_MIN_ENTRY_BARS", "0")
        monkeypatch.setenv("MA_CROSSOVER_MAX_ENTRY_BARS", "12")
        strategy = MACrossoverStrategy()
        assert (strategy.min_entry_bars, strategy.max_entry_bars) == (0, 12)

    @pytest.mark.parametrize("min_bars,max_bars", [(5, 1), (-1, 5), (-3, -1)])
    def test_invalid_pairs_fall_back(self, min_bars, max_bars):
        """An empty/negative window falls back instead of disabling entries."""
        strategy = MACrossoverStrategy(min_entry_bars=min_bars, max_entry_bars=max_bars)
        assert (strategy.min_entry_bars, strategy.max_entry_bars) == (
            DEFAULT_MIN_ENTRY_BARS,
            DEFAULT_MAX_ENTRY_BARS,
        )

    def test_validate_entry_window_accepts_degenerate_single_bar(self):
        """min == max is a legal one-bar window, not an error."""
        assert validate_entry_window(3, 3) == (3, 3)

    def test_unparseable_env_falls_back(self, monkeypatch):
        """Junk in the env warns and uses the default, never crashes."""
        monkeypatch.setenv("MA_CROSSOVER_MAX_ENTRY_BARS", "not-a-number")
        strategy = MACrossoverStrategy()
        assert strategy.max_entry_bars == DEFAULT_MAX_ENTRY_BARS

    def test_a_wider_window_admits_a_later_entry(self):
        """The window actually gates entries, so widening it can only help.

        The default 1-5 band is compared against a wide one on the same
        series; the wide band must not see FEWER signals.
        """
        series = _golden_cross_series()
        narrow = MACrossoverStrategy(
            fast_ma_period=FAST_MA,
            slow_ma_period=SLOW_MA,
            pullback_range=(0.0, 0.30),
            min_confidence=0.0,
            min_entry_bars=1,
            max_entry_bars=1,
        )
        wide = MACrossoverStrategy(
            fast_ma_period=FAST_MA,
            slow_ma_period=SLOW_MA,
            pullback_range=(0.0, 0.30),
            min_confidence=0.0,
            min_entry_bars=1,
            max_entry_bars=20,
        )
        narrow_signals, _ = _replay(narrow, series)
        wide_signals, _ = _replay(wide, series)
        assert len(wide_signals) >= len(narrow_signals)

    def test_pullback_edges_are_settable_attributes(self):
        """The optimizer addresses the band as two scalars.

        regime_param_overlay.apply_params_to_strategy only sets
        attributes that already exist, so without these properties every
        sampled pullback band was silently discarded.
        """
        strategy = MACrossoverStrategy(pullback_range=(0.01, 0.05))
        assert strategy.pullback_range_min == 0.01
        assert strategy.pullback_range_max == 0.05

        strategy.pullback_range_min = 0.0
        strategy.pullback_range_max = 0.10
        assert strategy.pullback_range == (0.0, 0.10)

    def test_whole_search_space_applies_to_the_instance(self):
        """Every declared search-space key must reach the strategy."""
        from trading_bot_v2.optimization.search_spaces import get_search_space
        from trading_bot_v2.regime_param_overlay import (
            apply_params_to_strategy,
        )

        space = get_search_space("ma_crossover")
        sample = {name: (bounds[0] + bounds[1]) / 2 for name, bounds in space.items()}
        sample["fast_ma_period"] = 8
        sample["slow_ma_period"] = 34
        sample["max_entry_bars"] = 12
        applied = apply_params_to_strategy(
            MACrossoverStrategy(), "ma_crossover", sample
        )
        assert set(applied) == set(space), (
            f"search-space keys never reached the strategy: "
            f"{sorted(set(space) - set(applied))}"
        )


class TestRollingWindowCrossoverTracking:
    def test_golden_cross_emits_signal_through_engine_window(self):
        """The core regression: a golden cross must produce >= 1 signal even
        though the caller's 60-candle window has long since saturated."""
        strategy = _permissive_strategy()
        series = _golden_cross_series()

        # Sanity: the window really is saturated well before the crossover,
        # so the old absolute-index scheme had no way to work here.
        assert len(series["close"]) > ENGINE_LOOKBACK * 2

        signals, _ = _replay(strategy, series)

        assert len(signals) >= 1, "no signals from a golden cross + pullback"
        assert all(s.side == OrderSide.BUY for s in signals)
        assert signals[0].pattern == "golden_cross_pullback"
        assert signals[0].stop_loss < signals[0].entry_price
        assert signals[0].take_profit > signals[0].entry_price

    def test_bars_since_crossover_advances_in_saturated_window(self):
        """Elapsed-bar counting must advance past 0 once the window is full.

        This is the direct assertion of the old failure mode: with an
        absolute index into a fixed-length window, every observation was 0.
        """
        strategy = _permissive_strategy()
        series = _golden_cross_series()

        _, bars_since = _replay(strategy, series)

        assert bars_since, "no crossover was ever tracked"
        assert max(bars_since) >= 1, (
            "bars-since-crossover never advanced past 0 - the crossover "
            "reference is still tied to the rolling window position"
        )
        # Counting is consecutive from the crossover bar, not jumpy.
        assert set(bars_since) <= set(range(0, strategy.MAX_ENTRY_BARS + 1))

    def test_crossover_is_dropped_once_entry_window_closes(self):
        """Stale crossovers must not linger in per-symbol state."""
        strategy = _permissive_strategy()
        series = _golden_cross_series()
        _replay(strategy, series)
        # The series ends far past the 5-bar entry window.
        assert "BTC-USDC" not in strategy.last_crossover

    def test_no_signal_without_a_crossover(self):
        """A flat series must not produce signals."""
        strategy = _permissive_strategy()
        count = 180
        closes = [100.0 + (0.01 if i % 2 else -0.01) for i in range(count)]
        signals, _ = _replay(strategy, _as_series(closes))
        assert signals == []

    def test_works_without_timestamps_via_bar_counter(self):
        """Bundles lacking a timestamp series fall back to the bar counter."""
        strategy = _permissive_strategy()
        series = _golden_cross_series()
        series.pop("timestamp")

        signals, bars_since = _replay(strategy, series)

        assert max(bars_since) >= 1
        assert len(signals) >= 1

    def test_repeated_calls_on_same_bar_do_not_age_the_crossover(self):
        """The engine replays 5m candles, so the same 4h bundle arrives 48
        times; the crossover must not age by 48 bars because of it."""
        strategy = _permissive_strategy()
        series = _golden_cross_series()
        symbol = "BTC-USDC"

        # Advance to the bar where the crossover is first recorded.
        cross_idx = None
        for i in range(len(series["close"])):
            bundle = BacktestEngine._history(series, i, ENGINE_LOOKBACK)
            strategy.generate_signals(symbol, {"4h": bundle}, series["close"][i])
            if symbol in strategy.last_crossover:
                cross_idx = i
                break
        assert cross_idx is not None, "no crossover detected in the fixture"

        bundle = BacktestEngine._history(series, cross_idx, ENGINE_LOOKBACK)
        for _ in range(48):
            strategy.generate_signals(
                symbol, {"4h": bundle}, series["close"][cross_idx]
            )

        info = strategy.last_crossover[symbol]
        elapsed = strategy._bars_since_crossover(
            bundle, info, strategy._observe_bar(symbol, bundle)
        )
        assert elapsed == 0


class TestDeathCross:
    def test_death_cross_emits_short_signal(self):
        strategy = _permissive_strategy()
        signals, _ = _replay(strategy, _death_cross_series())

        assert len(signals) >= 1
        assert all(s.side == OrderSide.SELL for s in signals)
        assert signals[0].pattern == "death_cross_rally"


class TestHistoryValidation:
    def test_short_history_warns_loudly(self):
        """A slow MA longer than the caller's window kills the strategy - the
        operator must be told, not left with a silent empty list."""
        from loguru import logger as loguru_logger

        messages: List[str] = []
        sink_id = loguru_logger.add(lambda m: messages.append(str(m)), level="WARNING")
        try:
            # 200-period slow MA against the engine's 60-candle window.
            strategy = MACrossoverStrategy(fast_ma_period=50, slow_ma_period=200)
            series = _golden_cross_series()
            bundle = BacktestEngine._history(series, 179, ENGINE_LOOKBACK)
            signals = strategy.generate_signals("BTC-USDC", {"4h": bundle}, 90.0)
        finally:
            loguru_logger.remove(sink_id)

        assert signals == []
        joined = "\n".join(messages)
        assert "insufficient 4h history" in joined
        assert "201 are required" in joined
        assert "BTC-USDC" in joined

    def test_required_history_reflects_configuration(self):
        assert (
            MACrossoverStrategy(fast_ma_period=10, slow_ma_period=30).required_history()
            == 35
        )  # macd 26 + 9 dominates
        assert (
            MACrossoverStrategy(
                fast_ma_period=50, slow_ma_period=200
            ).required_history()
            == 201
        )


class TestEngineWarmup:
    def test_loader_warmup_extends_start(self, tmp_path):
        """get_candles(warmup_candles=N) must reach back before `start`."""
        import pandas as pd

        from trading_bot_v2.backtesting.data_loader import BacktestDataLoader

        rows = []
        for ts in _timestamps(120):
            rows.append(
                {
                    "timestamp": ts,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 10.0,
                }
            )
        pd.DataFrame(rows).to_csv(tmp_path / "TEST-USDC_4h.csv", index=False)

        loader = BacktestDataLoader(symbol="TEST-USDC", data_dir=str(tmp_path))
        window_start = "2024-01-10T00:00:00"

        plain = loader.get_candles("4h", window_start, "2024-01-15T00:00:00")
        warmed = loader.get_candles(
            "4h", window_start, "2024-01-15T00:00:00", warmup_candles=30
        )

        assert plain["timestamp"][0] == window_start
        assert len(warmed["timestamp"]) == len(plain["timestamp"]) + 30
        assert warmed["timestamp"][0] < window_start
        assert warmed["timestamp"][-1] == plain["timestamp"][-1]

    def test_shift_start_is_a_no_op_for_zero_warmup(self):
        from trading_bot_v2.backtesting.data_loader import BacktestDataLoader

        assert BacktestDataLoader.shift_start("2024-01-10", "4h", 0) == "2024-01-10"
        assert (
            BacktestDataLoader.shift_start("2024-01-10T00:00:00", "4h", 6)
            == "2024-01-09T00:00:00"
        )

    def test_nearest_idx_uses_as_of_lookup_not_position(self):
        """A warmup prefix must not offset higher-timeframe alignment."""
        ts_4h = _timestamps(10)
        idx_map = {ts: i for i, ts in enumerate(ts_4h)}

        # Exact hit
        assert BacktestEngine._nearest_idx(idx_map, ts_4h, ts_4h[7]) == 7
        # 5m timestamp between two 4h bars -> most recent bar at or before it
        assert BacktestEngine._nearest_idx(idx_map, ts_4h, "2024-01-02T06:05:00") == 7
        # Before the series starts -> clamp to 0
        assert BacktestEngine._nearest_idx(idx_map, ts_4h, "2023-12-01T00:00:00") == 0

    def test_first_index_at_or_after(self):
        ts = _timestamps(10)
        assert BacktestEngine._first_index_at_or_after(ts, "2024-01-01") == 0
        assert BacktestEngine._first_index_at_or_after(ts, ts[4]) == 4
        assert BacktestEngine._first_index_at_or_after(ts, "2030-01-01") == len(ts)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
