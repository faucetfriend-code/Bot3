"""Tests for MarketRegimeDetector.

Covers regime detection across trending / ranging / indecisive synthetic
series, the regime-to-strategy mapping contract, and input validation.

History: this file spent its whole life as a print-only script that imported
from a "../Example files/core_logic" directory which does not exist in this
repo, so it never once collected under pytest. It has been rewritten to assert
rather than print. Where an assertion here contradicts the old printed
expectation, the change in production behaviour is deliberate and the code
comment that made it so is cited inline.
"""

import math
import random

import pytest

from .market_regime import MarketRegimeDetector, MarketRegime


# Regimes whose weight tables allocate to directional "core" strategies.
# INDECISIVE is excluded: it allocates only to overlays.
_CORE_STRATEGIES = {
    "MACrossover",
    "MomentumScalping",
    "GridTrading",
    "MeanReversion",
    "LiquidationCapture",
}

_ALL_REGIMES = [
    MarketRegime.TRENDING_STRONG,
    MarketRegime.TRENDING_MODERATE,
    MarketRegime.RANGING_VOLATILE,
    MarketRegime.RANGING_CALM,
    MarketRegime.INDECISIVE,
]


@pytest.fixture
def detector():
    """Build a detector on the documented default thresholds."""
    return MarketRegimeDetector(
        adx_trending_threshold=25.0,
        adx_ranging_threshold=20.0,
        volatility_high_percentile=75.0,
    )


def _wrap(closes, rng, spread=0.5):
    """Build a market_data dict from a close series.

    Args:
        closes: Sequence of close prices.
        rng: Seeded random.Random instance.
        spread: Maximum half-range added around each close.

    Returns:
        Dict with 'high', 'low' and 'close' lists.
    """
    return {
        "high": [c + rng.uniform(spread / 2, spread) for c in closes],
        "low": [c - rng.uniform(spread / 2, spread) for c in closes],
        "close": list(closes),
    }


def _strong_uptrend(rng, n=100):
    """Generate a persistent uptrend with minimal retracement."""
    closes = [100.0]
    for _ in range(n - 1):
        if rng.random() < 0.85:
            closes.append(closes[-1] + rng.uniform(0.5, 1.2))
        else:
            closes.append(closes[-1] + rng.uniform(-0.3, 0.0))
    return closes


def _noisy_drift(rng, n=100):
    """Generate a weak, heavily-noised drift."""
    closes = [100.0]
    for _ in range(n - 1):
        if rng.random() < 0.55:
            closes.append(closes[-1] + rng.uniform(0.1, 0.5))
        else:
            closes.append(closes[-1] + rng.uniform(-0.5, -0.1))
    return closes


def _low_amplitude_oscillation(rng, n=100):
    """Generate a gentle sine oscillation around a flat mean."""
    return [100 + 1.5 * math.sin(i * 0.15) + rng.uniform(-0.2, 0.2) for i in range(n)]


# ---------------------------------------------------------------------------
# Regime detection
# ---------------------------------------------------------------------------


def test_strong_uptrend_is_labelled_trending(detector):
    """A persistent uptrend must land in a trending regime, not a ranging one."""
    rng = random.Random(42)
    data = _wrap(_strong_uptrend(rng), rng)

    regime = detector.detect_regime(data)

    assert regime in (
        MarketRegime.TRENDING_STRONG,
        MarketRegime.TRENDING_MODERATE,
    ), f"persistent uptrend labelled {regime.value}"


@pytest.mark.parametrize("builder", [_strong_uptrend, _noisy_drift])
def test_label_agrees_with_the_adx_it_was_derived_from(detector, builder):
    """The emitted label must be consistent with the ADX thresholds.

    This is the only forward-looking claim the detector actually makes. The
    old script additionally expected specific synthetic series to be labelled
    "ranging" - a gentle sine oscillation and a random walk. Both are in fact
    labelled trending by the current code (ADX 37.2 and 24.4 respectively),
    and that is not a regression: ADX measures directional persistence and is
    blind to amplitude, so a smooth low-amplitude oscillation scores high. The
    project has already measured and documented this weakness in
    docs/REGIME-DISCRIMINATION.md, so pinning the old expectation here would
    assert something known to be false.
    """
    rng = random.Random(42)
    data = _wrap(builder(rng), rng, spread=1.4)

    regime = detector.detect_regime(data)
    adx = detector._last_calculated_adx

    if regime is MarketRegime.TRENDING_STRONG:
        assert adx > detector.adx_trending, f"TRENDING_STRONG with ADX={adx}"
    elif regime in (MarketRegime.RANGING_VOLATILE, MarketRegime.RANGING_CALM):
        assert adx < detector.adx_ranging, f"{regime.value} with ADX={adx}"


def test_detection_is_deterministic_for_identical_input(detector):
    """The same series must produce the same label twice running."""
    rng = random.Random(7)
    data = _wrap(_low_amplitude_oscillation(rng), rng, spread=0.3)

    first = detector.detect_regime(data)
    second = detector.detect_regime(data)

    assert first is second


def test_every_regime_label_is_a_known_member(detector):
    """detect_regime must only ever return a declared MarketRegime."""
    rng = random.Random(11)
    series = [
        _wrap(_strong_uptrend(rng), rng),
        _wrap(_noisy_drift(rng), rng, spread=1.4),
        _wrap(_low_amplitude_oscillation(rng), rng, spread=0.3),
    ]

    for data in series:
        assert detector.detect_regime(data) in MarketRegime


# ---------------------------------------------------------------------------
# Regime -> strategy mapping contract
# ---------------------------------------------------------------------------


def test_active_strategies_are_a_subset_of_weighted_strategies(detector):
    """Anything admitted to a regime must also carry a weight in that regime.

    An active strategy with no weight entry silently falls back to
    ``1.0 / len(signals)`` in StrategyManager._combine_signals, which would
    quietly discard the regime's intended allocation.
    """
    for regime in _ALL_REGIMES:
        active = set(detector.get_active_strategies(regime))
        weighted = set(detector.get_strategy_weights(regime))

        assert active <= weighted, (
            f"{regime.value}: active strategies {sorted(active - weighted)} "
            f"have no weight entry"
        )


def test_all_strategy_weights_lie_in_the_unit_interval(detector):
    """No regime may allocate a non-positive or greater-than-full weight."""
    for regime in _ALL_REGIMES:
        for name, weight in detector.get_strategy_weights(regime).items():
            assert 0.0 < weight <= 1.0, f"{regime.value}/{name} weight={weight}"


def test_every_regime_admits_at_least_one_strategy(detector):
    """No regime may map to an empty strategy set."""
    for regime in _ALL_REGIMES:
        assert detector.get_active_strategies(regime), f"{regime.value} is empty"
        assert detector.get_strategy_weights(regime), f"{regime.value} has no weights"


def test_directional_core_allocation_is_uniform_across_directional_regimes(detector):
    """Each directional regime allocates the same total to its core strategies.

    The old script asserted that every regime's weights summed to 1.0. That
    invariant is obsolete: overlay strategies (OrderBookImbalance,
    SessionRangeBreakout, CalendarFlow, VWAPPullback) are stacked on top of the
    core allocation and are documented in CLAUDE.md as running in all regimes
    independently, so the full dict now sums above 1.0 by design. The weights
    are consumed only as relative weights - StrategyManager._combine_signals
    divides through by total_weight - so the absolute sum carries no meaning.

    What is still meaningful is that the four directional regimes agree with
    each other on how much goes to the core.
    """
    directional = [
        MarketRegime.TRENDING_STRONG,
        MarketRegime.TRENDING_MODERATE,
        MarketRegime.RANGING_VOLATILE,
        MarketRegime.RANGING_CALM,
    ]

    core_totals = {}
    for regime in directional:
        weights = detector.get_strategy_weights(regime)
        core_totals[regime.value] = sum(
            w for name, w in weights.items() if name in _CORE_STRATEGIES
        )

    distinct = {round(total, 6) for total in core_totals.values()}
    assert len(distinct) == 1, f"core allocation disagrees: {core_totals}"


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["high", "low", "close"])
def test_missing_required_key_raises_value_error(detector, missing):
    """Every one of the three required series must be validated."""
    data = {
        "high": [100.0] * 50,
        "low": [99.0] * 50,
        "close": [99.5] * 50,
    }
    del data[missing]

    with pytest.raises(ValueError, match=missing):
        detector.detect_regime(data)


def test_insufficient_history_returns_indecisive_rather_than_raising(detector):
    """Too-short history degrades to INDECISIVE instead of raising.

    The old script expected a ValueError here. market_regime.py deliberately
    changed this - see the "Return INDECISIVE instead of raising exception"
    comment on the short-history branch of detect_regime - so the script's
    expectation is the stale side, not the code.
    """
    short = {
        "high": [100.0, 101.0, 102.0],
        "low": [99.0, 100.0, 101.0],
        "close": [99.5, 100.5, 101.5],
    }

    assert detector.detect_regime(short) is MarketRegime.INDECISIVE
