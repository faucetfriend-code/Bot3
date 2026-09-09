"""Tests for the regime-discrimination harness.

The load-bearing test is ``TestDetectorEquivalence``: the harness
re-implements ``MarketRegimeDetector``'s classifier and confirmation
state machine so a threshold sweep can re-use one indicator pass. If the
re-implementation drifts, every number in docs/REGIME-DISCRIMINATION.md
becomes fiction, so the equivalence is asserted bar-for-bar against the
real detector on synthetic candles.
"""

import math
import random
from datetime import datetime, timedelta

import pytest

from trading_bot_v2.analysis.regime_discrimination import (
    BarFeatures,
    RegimeParams,
    average_ranks,
    classify,
    cliffs_delta,
    epsilon_squared,
    forward_outcomes,
    holm_threshold,
    hourly_grid,
    label_bars,
    replay_labels,
    rotation_pvalue,
)
from trading_bot_v2.market_regime import MarketRegime, MarketRegimeDetector


def _synthetic_bars(n: int, seed: int) -> list:
    """Build BarFeatures spanning the whole ADX / vol-score domain.

    Args:
        n: Number of bars.
        seed: RNG seed.

    Returns:
        List of BarFeatures on a 4h grid.
    """
    rng = random.Random(seed)
    start = datetime(2024, 1, 1)
    bars = []
    price = 100.0
    for i in range(n):
        price *= math.exp(rng.gauss(0, 0.01))
        bars.append(
            BarFeatures(
                time=start + timedelta(hours=4 * i),
                adx=rng.uniform(5.0, 60.0),
                slope_falling=rng.random() < 0.5,
                vol_score=rng.uniform(0.0, 100.0),
                close=price,
            )
        )
    return bars


class TestDetectorEquivalence:
    """The harness must classify exactly like the shipped detector."""

    @pytest.mark.parametrize("previous", [None] + [r.value for r in MarketRegime])
    def test_classify_matches_detect_regime(self, previous):
        """classify() reproduces detect_regime() over the whole domain."""
        detector = MarketRegimeDetector()
        params = RegimeParams.from_detector(detector)
        prev_enum = None if previous is None else MarketRegime(previous)
        # detect_regime's branch structure depends only on (adx,
        # slope, vol_score, previous), so exercising a dense grid of
        # those four covers every path.
        for adx in [x / 2 for x in range(10, 121)]:
            for falling in (False, True):
                for vol in (0.0, 55.0, 59.9, 60.0, 64.9, 65.0, 68.0, 68.1, 99.0):
                    bar = BarFeatures(
                        time=datetime(2024, 1, 1),
                        adx=adx,
                        slope_falling=falling,
                        vol_score=vol,
                        close=100.0,
                    )
                    expected = _detect_via_detector(
                        detector, adx, falling, vol, prev_enum
                    )
                    assert classify(params, bar, previous) == expected.value, (
                        f"adx={adx} falling={falling} vol={vol} prev={previous}"
                    )

    def test_replay_matches_confirmation_state_machine(self):
        """replay_labels() reproduces _detect_regime_with_confirmation()."""
        bars = _synthetic_bars(400, seed=11)
        detector = MarketRegimeDetector()
        params = RegimeParams.from_detector(detector)
        hours = hourly_grid(bars[0].time, bars[-1].time)
        got = replay_labels(bars, hours, params)

        expected = _drive_real_detector(bars, hours)
        assert got == expected

    def test_replay_matches_with_mechanisms_disabled(self):
        """Equivalence survives the decomposition's disabled mechanisms."""
        bars = _synthetic_bars(300, seed=23)
        hours = hourly_grid(bars[0].time, bars[-1].time)
        for kwargs in (
            {"min_dwell_hours": 0.0},
            {"adx_exit_trending": 25.0},
            {"vol_score_enter": 65.0, "vol_score_exit": 65.0},
        ):
            detector = MarketRegimeDetector(**kwargs)
            params = RegimeParams.from_detector(detector)
            assert replay_labels(bars, hours, params) == _drive_real_detector(
                bars, hours, **kwargs
            ), kwargs


def _detect_via_detector(detector, adx, falling, vol, previous):
    """Run detect_regime's decision tree with indicators pinned.

    Args:
        detector: Configured detector.
        adx: ADX value to inject.
        falling: ADX slope-falling flag to inject.
        vol: Volatility score to inject.
        previous: Previous confirmed MarketRegime, or None.

    Returns:
        The MarketRegime the detector would return.
    """
    market_data = {"high": [1.0] * 60, "low": [1.0] * 60, "close": [1.0] * 60}

    import trading_bot_v2.market_regime as module

    saved_calc = module.calculate_adx
    module.calculate_adx = lambda *a, **k: adx
    detector._adx_slope_falling = lambda *a, **k: falling
    detector._calculate_volatility_score = lambda *a, **k: vol
    try:
        return detector.detect_regime(market_data, previous_regime=previous)
    finally:
        module.calculate_adx = saved_calc
        del detector._adx_slope_falling
        del detector._calculate_volatility_score


def _drive_real_detector(bars, hours, **detector_kwargs):
    """Drive the real detector over the hourly grid, indicators pinned.

    Mirrors what the backtest engine does: inject simulated time, call
    ``detect_regime_cached`` once per hour, and read back the confirmed
    regime.

    Args:
        bars: Precomputed BarFeatures.
        hours: Hourly detection grid.
        **detector_kwargs: Passed to MarketRegimeDetector.

    Returns:
        List of confirmed regime values (or None) per hour.
    """
    import bisect

    import trading_bot_v2.market_regime as module

    detector = MarketRegimeDetector(**detector_kwargs)
    detector._cache_ttl_hours = 1
    times = [b.time for b in bars]
    market_data = {"high": [1.0] * 60, "low": [1.0] * 60, "close": [1.0] * 60}
    saved_calc = module.calculate_adx
    out = []
    try:
        for now in hours:
            idx = bisect.bisect_right(times, now) - 1
            if idx < 0:
                out.append(None)
                continue
            bar = bars[idx]
            module.calculate_adx = lambda *a, _v=bar.adx, **k: _v
            detector._adx_slope_falling = lambda *a, _v=bar.slope_falling, **k: _v
            detector._calculate_volatility_score = lambda *a, _v=bar.vol_score, **k: _v
            detector._clock = lambda _t=now: _t
            # TTL is 1h and the grid is hourly, so every call is a miss
            # except the very first - force the miss explicitly rather
            # than relying on float comparison at the boundary.
            detector._regime_cache.pop("_cache_probe", None)
            entry = detector._regime_cache.get("SYN")
            if entry is not None:
                entry["timestamp"] = now - timedelta(hours=2)
            out.append(detector.detect_regime_cached("SYN", market_data).value)
    finally:
        module.calculate_adx = saved_calc
    return out


class TestStatistics:
    """Effect-size and null-distribution helpers."""

    def test_average_ranks_shares_ties(self):
        assert average_ranks([10.0, 20.0, 20.0, 30.0]) == [1.0, 2.5, 2.5, 4.0]

    def test_epsilon_squared_is_zero_for_one_group(self):
        ranks = average_ranks([1.0, 2.0, 3.0, 4.0])
        assert epsilon_squared(["a"] * 4, ranks, 4) == 0.0

    def test_epsilon_squared_approaches_one_for_perfect_separation(self):
        """Five perfectly separated equal groups saturate near 1.

        The ceiling depends on the number of groups: a two-group split
        tops out around 0.75 however clean it is, five equal groups
        around 0.96. Both bounds are checked so a regression in the
        scaling term cannot hide.
        """
        n = 500
        values = [float(i) for i in range(n)]
        ranks = average_ranks(values)
        five = [str(i // (n // 5)) for i in range(n)]
        assert epsilon_squared(five, ranks, n) == pytest.approx(0.96, abs=0.02)
        two = [str(i // (n // 2)) for i in range(n)]
        assert epsilon_squared(two, ranks, n) == pytest.approx(0.75, abs=0.02)

    def test_epsilon_squared_near_zero_for_interleaved_groups(self):
        values = [float(i) for i in range(200)]
        ranks = average_ranks(values)
        labels = ["a" if i % 2 == 0 else "b" for i in range(200)]
        assert epsilon_squared(labels, ranks, 200) < 0.01

    def test_cliffs_delta_extremes(self):
        values = [1.0, 2.0, 3.0, 4.0]
        ranks = average_ranks(values)
        assert cliffs_delta(ranks[2:], 2, 2) == pytest.approx(1.0)
        assert cliffs_delta(ranks[:2], 2, 2) == pytest.approx(-1.0)
        assert cliffs_delta([], 0, 4) == 0.0

    def test_rotation_pvalue_is_floored_and_bounded(self):
        rng = random.Random(3)
        values = [float(i) for i in range(300)]
        ranks = average_ranks(values)
        labels = ["a"] * 150 + ["b"] * 150
        p = rotation_pvalue(labels, ranks, 1.0, 50, rng)
        assert 1 / 51 <= p <= 1.0

    def test_rotation_pvalue_finds_no_signal_in_a_meaningless_split(self):
        rng = random.Random(5)
        values = [float((i * 37) % 101) for i in range(400)]
        ranks = average_ranks(values)
        labels = ["a" if i < 200 else "b" for i in range(400)]
        observed = epsilon_squared(labels, ranks, 400)
        assert rotation_pvalue(labels, ranks, observed, 200, rng) > 0.01

    def test_holm_threshold_tightens_for_the_smallest_pvalue(self):
        assert holm_threshold(1, 10, 0.05) == pytest.approx(0.005)
        assert holm_threshold(10, 10, 0.05) == pytest.approx(0.05)


class TestForwardOutcomes:
    """Forward-outcome construction."""

    def test_no_lookahead_tail_is_none(self):
        bars = _synthetic_bars(20, seed=7)
        out = forward_outcomes(bars, 6)
        assert all(v is None for v in out["fwd_ret"][-6:])
        assert out["fwd_ret"][13] is not None

    def test_efficiency_is_one_for_a_monotone_path(self):
        start = datetime(2024, 1, 1)
        bars = [
            BarFeatures(start + timedelta(hours=4 * i), 30.0, False, 50.0, 100.0 + i)
            for i in range(10)
        ]
        assert forward_outcomes(bars, 6)["efficiency"][0] == pytest.approx(1.0)

    def test_efficiency_is_zero_for_a_round_trip(self):
        start = datetime(2024, 1, 1)
        prices = [100.0, 101.0, 102.0, 101.0, 100.0, 99.0, 100.0, 100.0]
        bars = [
            BarFeatures(start + timedelta(hours=4 * i), 30.0, False, 50.0, p)
            for i, p in enumerate(prices)
        ]
        assert forward_outcomes(bars, 6)["efficiency"][0] == pytest.approx(0.0)

    def test_fwd_vol_is_zero_for_a_flat_path(self):
        start = datetime(2024, 1, 1)
        bars = [
            BarFeatures(start + timedelta(hours=4 * i), 30.0, False, 50.0, 100.0)
            for i in range(10)
        ]
        assert forward_outcomes(bars, 6)["fwd_vol"][0] == pytest.approx(0.0)


class TestLabelBars:
    """Per-bar labelling."""

    def test_one_label_per_bar(self):
        bars = _synthetic_bars(120, seed=13)
        labels = label_bars(bars, RegimeParams())
        assert len(labels) == len(bars)
        assert all(lab is not None for lab in labels)

    def test_empty_input(self):
        assert label_bars([], RegimeParams()) == []


class TestThresholdEnvWiring:
    """The four regime boundaries are env-configurable (defaults intact)."""

    BOUNDARIES = (
        ("ADX_TRENDING_THRESHOLD", "adx_trending", 25.0),
        ("ADX_RANGING_THRESHOLD", "adx_ranging", 20.0),
        ("ADX_MODERATE_THRESHOLD", "adx_moderate", 20.0),
        ("VOLATILITY_HIGH_PERCENTILE", "volatility_percentile", 65.0),
    )

    @pytest.mark.parametrize("env_name,attr,default", BOUNDARIES)
    def test_default_when_unset(self, env_name, attr, default, monkeypatch):
        monkeypatch.delenv(env_name, raising=False)
        assert getattr(MarketRegimeDetector(), attr) == default

    @pytest.mark.parametrize("env_name,attr,default", BOUNDARIES)
    def test_env_is_read(self, env_name, attr, default, monkeypatch):
        monkeypatch.setenv(env_name, str(default + 3.0))
        assert getattr(MarketRegimeDetector(), attr) == default + 3.0

    @pytest.mark.parametrize("env_name,attr,default", BOUNDARIES)
    def test_explicit_argument_beats_env(self, env_name, attr, default, monkeypatch):
        monkeypatch.setenv(env_name, str(default + 3.0))
        kwargs = {
            "ADX_TRENDING_THRESHOLD": "adx_trending_threshold",
            "ADX_RANGING_THRESHOLD": "adx_ranging_threshold",
            "ADX_MODERATE_THRESHOLD": "adx_moderate_threshold",
            "VOLATILITY_HIGH_PERCENTILE": "volatility_high_percentile",
        }[env_name]
        detector = MarketRegimeDetector(**{kwargs: default})
        assert getattr(detector, attr) == default

    @pytest.mark.parametrize("env_name,attr,default", BOUNDARIES)
    def test_garbage_env_falls_back_to_default(
        self, env_name, attr, default, monkeypatch
    ):
        monkeypatch.setenv(env_name, "not-a-number")
        assert getattr(MarketRegimeDetector(), attr) == default

    def test_params_snapshot_round_trips(self, monkeypatch):
        for name, _, _ in self.BOUNDARIES:
            monkeypatch.delenv(name, raising=False)
        params = RegimeParams.from_detector(MarketRegimeDetector())
        assert params.adx_trending == 25.0
        assert params.adx_ranging == 20.0
        assert params.adx_moderate == 20.0
        assert params.vol_pct == 65.0
        assert params.adx_exit == 22.0
        assert params.vol_enter == 68.0
        assert params.vol_exit == 60.0
        assert params.dwell_hours == 4.0
