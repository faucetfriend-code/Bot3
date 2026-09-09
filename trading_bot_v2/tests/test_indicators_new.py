"""
Tests for the 2026-07-30 indicator additions: OBV, SuperTrend, and
relative volume (calculate_relative_volume backs the rvol gate measured
in docs/VWAP-SIGNAL-STUDY.md).
"""

import pytest

from trading_bot_v2.indicators import (
    calculate_obv,
    calculate_relative_volume,
    calculate_supertrend,
)


class TestOBV:
    def test_rising_closes_accumulate_volume(self):
        obv = calculate_obv([1.0, 2.0, 3.0], [10.0, 20.0, 30.0])
        assert obv == [0.0, 20.0, 50.0]

    def test_falling_closes_subtract_volume(self):
        obv = calculate_obv([3.0, 2.0, 1.0], [10.0, 20.0, 30.0])
        assert obv == [0.0, -20.0, -50.0]

    def test_flat_close_leaves_obv_unchanged(self):
        obv = calculate_obv([1.0, 1.0, 2.0], [10.0, 99.0, 5.0])
        assert obv == [0.0, 0.0, 5.0]

    def test_series_length_matches_input(self):
        closes = [float(i % 7) + 1.0 for i in range(50)]
        volumes = [100.0] * 50
        assert len(calculate_obv(closes, volumes)) == 50

    def test_mismatched_lengths_raise(self):
        with pytest.raises(ValueError):
            calculate_obv([1.0, 2.0], [10.0])

    def test_single_bar_raises(self):
        with pytest.raises(ValueError):
            calculate_obv([1.0], [10.0])


class TestSuperTrend:
    @staticmethod
    def _trend_bars(start, step, n):
        closes = [start + step * i for i in range(n)]
        highs = [c + 0.5 for c in closes]
        lows = [c - 0.5 for c in closes]
        return highs, lows, closes

    def test_uptrend_reports_up_with_line_below_price(self):
        highs, lows, closes = self._trend_bars(100.0, 1.0, 40)
        line, direction = calculate_supertrend(highs, lows, closes)
        assert direction == "up"
        assert line < closes[-1]

    def test_downtrend_reports_down_with_line_above_price(self):
        highs, lows, closes = self._trend_bars(200.0, -1.0, 40)
        line, direction = calculate_supertrend(highs, lows, closes)
        assert direction == "down"
        assert line > closes[-1]

    def test_reversal_flips_direction(self):
        # 30 bars up then a hard collapse through the band
        highs, lows, closes = self._trend_bars(100.0, 1.0, 30)
        for i in range(10):
            c = closes[-1] - 8.0
            closes.append(c)
            highs.append(c + 0.5)
            lows.append(c - 0.5)
        _, direction = calculate_supertrend(highs, lows, closes)
        assert direction == "down"

    def test_insufficient_data_raises(self):
        highs, lows, closes = self._trend_bars(100.0, 1.0, 5)
        with pytest.raises(ValueError):
            calculate_supertrend(highs, lows, closes, period=10)

    def test_mismatched_lengths_raise(self):
        with pytest.raises(ValueError):
            calculate_supertrend([1.0] * 20, [1.0] * 20, [1.0] * 19)

    def test_invalid_params_raise(self):
        highs, lows, closes = self._trend_bars(100.0, 1.0, 40)
        with pytest.raises(ValueError):
            calculate_supertrend(highs, lows, closes, period=0)
        with pytest.raises(ValueError):
            calculate_supertrend(highs, lows, closes, multiplier=0.0)


class TestRelativeVolume:
    def test_current_vs_trailing_median(self):
        # 96 trailing bars of volume 100, current bar 150 -> rvol 1.5
        volumes = [100.0] * 96 + [150.0]
        assert calculate_relative_volume(volumes) == pytest.approx(1.5)

    def test_median_not_mean_ignores_spike(self):
        # One 10000x spike in the window must not suppress the ratio -
        # that is the reason median was chosen over mean
        volumes = [100.0] * 95 + [1_000_000.0] + [150.0]
        assert calculate_relative_volume(volumes) == pytest.approx(1.5)

    def test_even_window_uses_midpoint_average(self):
        volumes = [10.0, 20.0, 30.0, 40.0, 25.0]
        # window=4 -> trailing [10,20,30,40], median 25 -> rvol 1.0
        assert calculate_relative_volume(volumes, window=4) == pytest.approx(1.0)

    def test_current_bar_excluded_from_median(self):
        # A huge current bar must not raise its own baseline
        volumes = [100.0] * 96 + [10_000.0]
        assert calculate_relative_volume(volumes) == pytest.approx(100.0)

    def test_dead_market_returns_zero(self):
        volumes = [0.0] * 96 + [50.0]
        assert calculate_relative_volume(volumes) == 0.0

    def test_insufficient_data_raises(self):
        with pytest.raises(ValueError):
            calculate_relative_volume([1.0] * 96)  # needs 97 for window=96

    def test_invalid_window_raises(self):
        with pytest.raises(ValueError):
            calculate_relative_volume([1.0] * 10, window=0)
