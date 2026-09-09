"""Tests for the realized-volatility regime taxonomy.

The load-bearing test is ``TestNoLookahead``. A regime scheme built on
quantiles is one careless line away from computing its boundaries over
the whole series, which labels a bar with volatility that had not
happened yet and produces a spectacular, completely fake result. Every
test in that class feeds a series whose FUTURE contains a volatility
explosion and asserts that not one already-emitted label moves.

``TestDetectorEquivalence`` is the second: the analysis harness and the
shipped detector must produce the same labels bar for bar, or the
side-by-side table in docs/REGIME-VOLATILITY.md is measuring something
the bot does not do.
"""

import bisect
import math
import random
import statistics as st
from datetime import datetime, timedelta

import pytest

from trading_bot_v2.analysis.regime_discrimination import (
    BarFeatures,
    RegimeParams,
    average_ranks,
    bucket_names,
    epsilon_squared,
    epsilon_squared_coded,
    hourly_grid,
    label_bars_quantile,
    label_bars_two_axis,
)
from trading_bot_v2.market_regime import MarketRegime, MarketRegimeDetector
from trading_bot_v2.regime_param_overlay import normalize_regime_value
from trading_bot_v2.volatility_regime import (
    BUCKET_REGIMES,
    DEFAULT_VOL_WINDOW,
    SHIPPED_BUCKETS,
    TrailingQuantileBucketer,
    VolatilityRegimeClassifier,
    VolatilityRegimeDetector,
    get_regime_mode,
    is_volatility_regime,
    make_regime_detector,
    trailing_efficiency,
    trailing_realized_vol,
)

START = datetime(2024, 1, 1)


def _prices(n, seed, sigma=0.01, drift=0.0):
    """Build a geometric random-walk close series.

    Args:
        n: Number of closes.
        seed: RNG seed.
        sigma: Per-bar log-return standard deviation.
        drift: Per-bar log-return drift.

    Returns:
        List of closes, oldest first.
    """
    rng = random.Random(seed)
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(closes[-1] * math.exp(drift + rng.gauss(0, sigma)))
    return closes


def _bars(closes):
    """Wrap a close series in BarFeatures on a 4h grid.

    ADX and the volatility score are filled with placeholders - the
    volatility taxonomy reads neither.

    Args:
        closes: Close prices, oldest first.

    Returns:
        List of BarFeatures.
    """
    return [
        BarFeatures(
            time=START + timedelta(hours=4 * i),
            adx=20.0,
            slope_falling=False,
            vol_score=50.0,
            close=close,
        )
        for i, close in enumerate(closes)
    ]


class TestTrailingMetrics:
    """The two trailing inputs behave as documented."""

    def test_realized_vol_matches_manual_stdev(self):
        """The metric is the population stdev of the last N log returns."""
        closes = _prices(60, seed=1)
        expected = st.pstdev(
            [
                math.log(closes[i] / closes[i - 1])
                for i in range(len(closes) - DEFAULT_VOL_WINDOW, len(closes))
            ]
        )
        assert trailing_realized_vol(closes) == pytest.approx(expected)

    def test_realized_vol_uses_only_the_trailing_window(self):
        """Prices before the window cannot influence the value."""
        closes = _prices(60, seed=2)
        mutated = [c * 3.0 for c in closes[:20]] + closes[20:]
        assert trailing_realized_vol(closes) == pytest.approx(
            trailing_realized_vol(mutated)
        )

    def test_realized_vol_none_on_short_or_invalid_series(self):
        """Too little history, or a non-positive price, returns None."""
        assert trailing_realized_vol([1.0, 2.0, 3.0]) is None
        bad = _prices(40, seed=3)
        bad[-2] = 0.0
        assert trailing_realized_vol(bad) is None

    def test_efficiency_is_one_on_a_monotone_path(self):
        """A straight line has net move equal to path length."""
        closes = [100.0 + i for i in range(40)]
        assert trailing_efficiency(closes) == pytest.approx(1.0)

    def test_efficiency_is_zero_on_a_closed_zigzag(self):
        """A path that returns to its start has zero net move."""
        closes = [100.0 + (i % 2) for i in range(41)]
        assert trailing_efficiency(closes, window=14) == pytest.approx(0.0)


class TestTrailingQuantileBucketer:
    """Ranking against a trailing window, with the documented guards."""

    def test_warmup_returns_none(self):
        """Below min_observations no label is emitted."""
        bucketer = TrailingQuantileBucketer(buckets=3, min_observations=10)
        for i in range(9):
            assert bucketer.observe(START + timedelta(hours=4 * i), float(i)) is None
        assert bucketer.observe(START + timedelta(hours=40), 9.0) is not None

    def test_buckets_are_non_degenerate_by_construction(self):
        """Every bucket is populated on a continuous input."""
        bucketer = TrailingQuantileBucketer(
            buckets=3, min_observations=10, reference_days=10_000
        )
        rng = random.Random(11)
        seen = set()
        for i in range(600):
            result = bucketer.observe(START + timedelta(hours=4 * i), rng.random())
            if result is not None:
                seen.add(result.index)
        assert seen == {0, 1, 2}

    def test_consecutive_duplicates_are_deduplicated(self):
        """The same closed 4h bar seen four times counts once."""
        bucketer = TrailingQuantileBucketer(buckets=2, min_observations=2)
        for i in range(4):
            bucketer.observe(START + timedelta(hours=i), 1.0)
        assert len(bucketer) == 1

    def test_observations_outside_the_reference_window_are_evicted(self):
        """The reference window is a trailing calendar span."""
        bucketer = TrailingQuantileBucketer(
            buckets=2, min_observations=2, reference_days=1.0
        )
        for i in range(48):
            bucketer.observe(START + timedelta(hours=i), float(i))
        assert len(bucketer) <= 25

    def test_rank_is_monotone_in_the_value(self):
        """A larger value never ranks below a smaller one."""
        rng = random.Random(5)
        values = [rng.random() for _ in range(300)]
        low = TrailingQuantileBucketer(
            buckets=5, min_observations=10, reference_days=10_000
        )
        high = TrailingQuantileBucketer(
            buckets=5, min_observations=10, reference_days=10_000
        )
        for i, value in enumerate(values):
            when = START + timedelta(hours=4 * i)
            low.observe(when, value)
            high.observe(when, value)
        a = low.observe(START + timedelta(hours=4 * 400), 0.1)
        b = high.observe(START + timedelta(hours=4 * 400), 0.9)
        assert a.rank < b.rank
        assert a.index <= b.index

    def test_rejects_degenerate_configuration(self):
        """A one-bucket or negative-window scheme is a configuration error."""
        with pytest.raises(ValueError):
            TrailingQuantileBucketer(buckets=1)
        with pytest.raises(ValueError):
            TrailingQuantileBucketer(buckets=3, reference_days=0)
        with pytest.raises(ValueError):
            TrailingQuantileBucketer(buckets=5, min_observations=2)


class TestNoLookahead:
    """A future volatility explosion cannot change a past label."""

    #: Calm prefix, then a 10x volatility regime. If quantile boundaries
    #: were computed over the whole series, the prefix would be pushed
    #: into the bottom bucket wholesale and these tests would fail.
    PREFIX = 500
    SUFFIX = 300

    def _series(self):
        """Build the calm prefix and the exploded continuation."""
        calm = _prices(self.PREFIX, seed=42, sigma=0.004)
        rng = random.Random(99)
        explosive = list(calm)
        for _ in range(self.SUFFIX):
            explosive.append(explosive[-1] * math.exp(rng.gauss(0, 0.04)))
        return calm, explosive

    def test_bucketer_labels_are_append_only(self):
        """Ranks emitted before the explosion are unchanged by it."""
        calm, explosive = self._series()
        short = TrailingQuantileBucketer(
            buckets=3, min_observations=40, reference_days=10_000
        )
        long = TrailingQuantileBucketer(
            buckets=3, min_observations=40, reference_days=10_000
        )
        short_out = [
            short.observe(START + timedelta(hours=4 * i), v) for i, v in enumerate(calm)
        ]
        long_out = [
            long.observe(START + timedelta(hours=4 * i), v)
            for i, v in enumerate(explosive)
        ]
        for i in range(len(calm)):
            a, b = short_out[i], long_out[i]
            assert (a is None) == (b is None)
            if a is not None:
                assert a.index == b.index
                assert a.rank == pytest.approx(b.rank)

    def test_analysis_labels_are_append_only(self):
        """label_bars_quantile agrees on the shared prefix."""
        calm, explosive = self._series()
        params = RegimeParams()
        short = label_bars_quantile(_bars(calm), params)
        long = label_bars_quantile(_bars(explosive), params)
        assert short == long[: len(short)]
        # The test is only meaningful if the explosion actually moved the
        # distribution - otherwise it would pass on a constant series.
        assert MarketRegime.VOL_HIGH.value in long[len(short) :]

    def test_two_axis_labels_are_append_only(self):
        """The 2-D scheme inherits the property from both axes."""
        calm, explosive = self._series()
        params = RegimeParams()
        short = label_bars_two_axis(_bars(calm), params)
        long = label_bars_two_axis(_bars(explosive), params)
        assert short == long[: len(short)]

    def test_detector_labels_are_append_only(self):
        """The live detector agrees with its own truncated replay."""
        calm, explosive = self._series()

        def replay(closes):
            detector = VolatilityRegimeDetector(compute_adx=False)
            out = []
            for i in range(60, len(closes)):
                now = START + timedelta(hours=4 * i)
                detector._clock = (lambda t: (lambda: t))(now)
                out.append(
                    detector.detect_regime_cached(
                        "X", {"close": closes[max(0, i - 59) : i + 1]}
                    ).value
                )
            return out

        short = replay(calm)
        long = replay(explosive)
        assert short == long[: len(short)]


class TestDetectorEquivalence:
    """The harness must label exactly like the shipped detector."""

    def test_analysis_matches_detect_regime_cached(self):
        """Bar-for-bar equality over the engine's hourly refresh grid.

        The replay must be hourly, not per 4h bar: the backtest engine
        calls detect_regime_cached on every 5m bar with a 1h cache TTL,
        so the confirmation state machine sees four detections per closed
        4h candle. Replaying once per bar would be a different state
        machine, and the harness would be measuring something the bot
        does not do.
        """
        closes = _prices(700, seed=17, sigma=0.012)
        bars = _bars(closes)
        times = [b.time for b in bars]
        params = RegimeParams()
        analysis = label_bars_quantile(bars, params)

        detector = VolatilityRegimeDetector(compute_adx=False)
        at_hour = {}
        for hour in hourly_grid(bars[0].time, bars[-1].time):
            idx = bisect.bisect_right(times, hour) - 1
            detector._clock = (lambda t: (lambda: t))(hour)
            window = [b.close for b in bars[max(0, idx - 59) : idx + 1]]
            regime = detector.detect_regime_cached("SYM", {"close": window})
            at_hour[hour] = None if regime is MarketRegime.VOL_WARMUP else regime.value
        live = [at_hour[bar.time] for bar in bars]
        assert analysis == live
        assert any(label is not None for label in live)

    def test_bucket_names_match_the_shipped_enum(self):
        """k = 3 labels are literally the persisted enum values."""
        assert bucket_names(SHIPPED_BUCKETS) == tuple(r.value for r in BUCKET_REGIMES)
        assert bucket_names(4) == (
            "vol_1of4",
            "vol_2of4",
            "vol_3of4",
            "vol_4of4",
        )


class TestEffectSizeFastPath:
    """The vectorised epsilon-squared must equal the pure-Python one."""

    def test_agreement_on_random_groupings(self):
        """Same statistic, to floating-point tolerance."""
        import numpy as np

        rng = random.Random(3)
        for n_groups in (2, 3, 5):
            labels = [f"g{rng.randrange(n_groups)}" for _ in range(2000)]
            values = [rng.gauss(0, 1) for _ in range(2000)]
            ranks = average_ranks(values)
            order = sorted(set(labels))
            codes = np.asarray([order.index(lab) for lab in labels], dtype=np.int64)
            slow = epsilon_squared(labels, ranks, len(values))
            fast = epsilon_squared_coded(
                codes, np.asarray(ranks, dtype=float), len(values), len(order)
            )
            assert fast == pytest.approx(slow, rel=1e-9, abs=1e-12)

    def test_single_group_is_zero(self):
        """A degenerate partition explains nothing."""
        import numpy as np

        ranks = np.arange(1.0, 51.0)
        codes = np.zeros(50, dtype=np.int64)
        assert epsilon_squared_coded(codes, ranks, 50, 1) == 0.0


class TestClassifierWarmupAndSeeding:
    """Warmup, seeding and reset behave as documented."""

    def test_warmup_before_enough_trailing_history(self):
        """A short history cannot be ranked."""
        classifier = VolatilityRegimeClassifier(min_observations=40)
        closes = _prices(20, seed=8)
        assert classifier.classify("A", START, closes) is None

    def test_seeding_excludes_the_current_bar(self):
        """The seed is strictly past; the current value is added once."""
        classifier = VolatilityRegimeClassifier(min_observations=5)
        closes = _prices(40, seed=9)
        classifier.classify("A", START, closes)
        bucketer = classifier._bucketer("A")
        # 40 closes -> trailing vol defined for indexes 14..39; index 39
        # is the current bar, so 25 seeded values plus the current one.
        assert len(bucketer) == 26

    def test_reset_clears_symbol_state(self):
        """Reset returns a symbol to warmup."""
        classifier = VolatilityRegimeClassifier(min_observations=5)
        closes = _prices(40, seed=10)
        assert classifier.classify("A", START, closes) is not None
        classifier.reset("A")
        assert len(classifier._bucketer("A")) == 0

    def test_detector_cache_clear_resets_the_reference_window(self):
        """clear_regime_cache drops the trailing distribution too."""
        detector = VolatilityRegimeDetector(compute_adx=False)
        closes = _prices(120, seed=12)
        detector._clock = lambda: START
        detector.detect_regime_cached("A", {"close": closes})
        assert len(detector.classifier._bucketer("A")) > 0
        detector.clear_regime_cache("A")
        assert len(detector.classifier._bucketer("A")) == 0


class TestFactoryAndFlag:
    """REGIME_MODE gates the new detector, and ADX stays the default."""

    def test_default_is_adx(self, monkeypatch):
        """Unset REGIME_MODE reproduces the shipped detector exactly."""
        monkeypatch.delenv("REGIME_MODE", raising=False)
        assert get_regime_mode() == "adx"
        assert type(make_regime_detector()) is MarketRegimeDetector

    def test_volatility_mode_selects_the_new_detector(self, monkeypatch):
        """The flag is the only way to switch taxonomy."""
        monkeypatch.setenv("REGIME_MODE", "volatility")
        assert isinstance(make_regime_detector(), VolatilityRegimeDetector)

    def test_unknown_mode_falls_back_to_adx(self, monkeypatch):
        """A typo must never silently re-gate every strategy."""
        monkeypatch.setenv("REGIME_MODE", "volatilty")
        assert get_regime_mode() == "adx"
        assert type(make_regime_detector()) is MarketRegimeDetector


class TestStrategyMapping:
    """The proposed mapping, and its configurability."""

    def test_default_mapping(self, monkeypatch):
        """Mechanically grounded defaults, warmup stays flat."""
        for suffix in ("LOW", "MID", "HIGH", "WARMUP"):
            monkeypatch.delenv(f"REGIME_VOL_STRATEGIES_{suffix}", raising=False)
        detector = VolatilityRegimeDetector(compute_adx=False)
        assert detector.get_active_strategies(MarketRegime.VOL_LOW) == ["MeanReversion"]
        assert detector.get_active_strategies(MarketRegime.VOL_MID) == [
            "GridTrading",
            "VWAPScalping",
        ]
        assert detector.get_active_strategies(MarketRegime.VOL_HIGH) == [
            "MomentumScalping",
            "MACrossover",
        ]
        assert detector.get_active_strategies(MarketRegime.VOL_WARMUP) == []

    def test_mapping_is_env_configurable(self, monkeypatch):
        """The proposal is a default, not a decision baked into code."""
        monkeypatch.setenv("REGIME_VOL_STRATEGIES_LOW", "GridTrading, MeanReversion")
        detector = VolatilityRegimeDetector(compute_adx=False)
        assert detector.get_active_strategies(MarketRegime.VOL_LOW) == [
            "GridTrading",
            "MeanReversion",
        ]
        assert detector.is_grid_allowed(MarketRegime.VOL_LOW) is True

    def test_grid_permission_follows_the_mapping(self, monkeypatch):
        """is_grid_allowed cannot disagree with the strategy map."""
        for suffix in ("LOW", "MID", "HIGH", "WARMUP"):
            monkeypatch.delenv(f"REGIME_VOL_STRATEGIES_{suffix}", raising=False)
        detector = VolatilityRegimeDetector(compute_adx=False)
        assert detector.is_grid_allowed(MarketRegime.VOL_MID) is True
        assert detector.is_grid_allowed(MarketRegime.VOL_LOW) is False
        assert detector.is_grid_allowed(MarketRegime.VOL_HIGH) is False
        assert detector.is_grid_allowed(MarketRegime.VOL_WARMUP) is False

    def test_adx_grid_permission_is_unchanged(self):
        """The shipped ADX rule is exactly what strategy_manager now asks."""
        detector = MarketRegimeDetector()
        allowed = {
            regime for regime in MarketRegime if detector.is_grid_allowed(regime)
        }
        assert allowed == {
            MarketRegime.RANGING_CALM,
            MarketRegime.RANGING_VOLATILE,
            MarketRegime.INDECISIVE,
        }

    def test_weights_are_env_configurable(self, monkeypatch):
        """Weight overrides parse Name:weight pairs."""
        monkeypatch.setenv("REGIME_VOL_WEIGHTS_HIGH", "MomentumScalping:0.7,bogus")
        detector = VolatilityRegimeDetector(compute_adx=False)
        assert detector.get_strategy_weights(MarketRegime.VOL_HIGH) == {
            "MomentumScalping": 0.7
        }


class TestBackwardCompatibility:
    """The new values coexist with the ADX ones instead of redefining them."""

    def test_adx_values_are_untouched(self):
        """The five persisted ADX values still mean what they meant."""
        assert MarketRegime.TRENDING_STRONG.value == "trending_strong"
        assert MarketRegime.RANGING_CALM.value == "ranging_calm"

    def test_taxonomies_are_disjoint(self):
        """No value belongs to both taxonomies."""
        adx = {
            MarketRegime.TRENDING_STRONG,
            MarketRegime.TRENDING_MODERATE,
            MarketRegime.RANGING_VOLATILE,
            MarketRegime.RANGING_CALM,
            MarketRegime.INDECISIVE,
        }
        assert not any(is_volatility_regime(r) for r in adx)
        assert all(is_volatility_regime(r) for r in BUCKET_REGIMES)

    def test_overlay_normalisation_accepts_the_new_values(self):
        """Stored-overlay plumbing keeps working under either taxonomy."""
        assert normalize_regime_value(MarketRegime.VOL_HIGH) == "vol_high"
        assert normalize_regime_value("vol_low") == "vol_low"

    def test_volatility_detector_ignores_adx_thresholds(self):
        """Classification does not read any ADX boundary."""
        detector = VolatilityRegimeDetector(
            adx_trending_threshold=99.0, compute_adx=False
        )
        closes = _prices(300, seed=21)
        seen = set()
        for i in range(60, len(closes)):
            detector._clock = (lambda t: (lambda: t))(START + timedelta(hours=4 * i))
            seen.add(
                detector.detect_regime_cached(
                    "A", {"close": closes[max(0, i - 59) : i + 1]}
                )
            )
        assert seen <= set(BUCKET_REGIMES) | {MarketRegime.VOL_WARMUP}
        assert len(seen & set(BUCKET_REGIMES)) >= 2

    def test_missing_close_series_raises(self):
        """The input contract is still enforced."""
        detector = VolatilityRegimeDetector(compute_adx=False)
        with pytest.raises(ValueError):
            detector.detect_regime({"high": [1.0], "low": [1.0]})
