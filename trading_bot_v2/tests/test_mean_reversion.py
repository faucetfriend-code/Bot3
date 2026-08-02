"""Tests for MeanReversionStrategy signal generation.

Covers oversold / overbought entries, the neutral no-signal case, insufficient
data handling, and what the strategy now does when timeframes disagree.

History: this file spent its whole life as a print-only script that imported
from a "../Example files/core_logic" directory which does not exist in this
repo, so it never once collected under pytest. It has been rewritten to assert
rather than print. One old expectation - that a 15m/1h divergence suppresses
the signal - is now inverted, because the multi-timeframe gate was removed on
purpose; see test_divergent_timeframes_still_signal for the citation and the
caveat that came with it.
"""

import random

import pytest

from trading_bot_v2.models import OrderSide
from trading_bot_v2.strategies.mean_reversion import MeanReversionStrategy


@pytest.fixture
def strategy():
    """Build a strategy on the documented default thresholds.

    A fresh instance per test keeps the per-symbol entry cooldown from
    coupling one test's outcome to another's.
    """
    return MeanReversionStrategy(
        rsi_oversold=30.0, rsi_overbought=70.0, min_confidence=0.6
    )


def _multi_tf(closes, rng, spread=0.15):
    """Wrap a close series as the 15m and 1h slices the strategy expects."""
    frame = {
        "high": [c + rng.uniform(spread / 3, spread) for c in closes],
        "low": [c - rng.uniform(spread / 3, spread) for c in closes],
        "close": list(closes),
    }
    return {"15m": frame, "1h": frame}


def _consolidation_then_selloff(rng):
    """Generate a flat base followed by a sharp drop into oversold RSI."""
    closes = [100.0]
    for _ in range(85):
        closes.append(closes[-1] + rng.uniform(-0.2, 0.2))
    for _ in range(14):
        closes.append(closes[-1] + rng.uniform(-1.5, -0.5))
    return closes


def _consolidation_then_rally(rng):
    """Generate a flat base followed by a sharp rally into overbought RSI."""
    closes = [100.0]
    for _ in range(85):
        closes.append(closes[-1] + rng.uniform(-0.2, 0.2))
    for _ in range(14):
        closes.append(closes[-1] + rng.uniform(0.5, 1.5))
    return closes


# ---------------------------------------------------------------------------
# Entry signals
# ---------------------------------------------------------------------------


def test_oversold_conditions_produce_a_long_signal(strategy):
    """A sharp selloff into the lower band must produce a BUY."""
    rng = random.Random(42)
    closes = _consolidation_then_selloff(rng)

    signals = strategy.generate_signals("SUI-PERP", _multi_tf(closes, rng), closes[-1])

    assert len(signals) == 1
    signal = signals[0]
    assert signal.side is OrderSide.BUY
    assert signal.confidence >= 0.6
    assert signal.pattern == "oversold_mean_reversion"


def test_overbought_conditions_produce_a_short_signal(strategy):
    """A sharp rally into the upper band must produce a SELL."""
    rng = random.Random(42)
    closes = _consolidation_then_rally(rng)

    signals = strategy.generate_signals("SUI-PERP", _multi_tf(closes, rng), closes[-1])

    assert len(signals) == 1
    signal = signals[0]
    assert signal.side is OrderSide.SELL
    assert signal.confidence >= 0.6
    assert signal.pattern == "overbought_mean_reversion"


def test_long_signal_geometry_is_coherent(strategy):
    """Stop below entry, target above entry, and the RRR must be honest."""
    rng = random.Random(42)
    closes = _consolidation_then_selloff(rng)

    signals = strategy.generate_signals("SUI-PERP", _multi_tf(closes, rng), closes[-1])
    signal = signals[0]

    assert signal.stop_loss < signal.entry_price < signal.take_profit

    risk = signal.entry_price - signal.stop_loss
    reward = signal.take_profit - signal.entry_price
    assert signal.rrr == pytest.approx(reward / risk, rel=1e-6)
    assert signal.rrr >= strategy.min_rrr


def test_short_signal_geometry_is_coherent(strategy):
    """Stop above entry, target below entry, and the RRR must be honest."""
    rng = random.Random(42)
    closes = _consolidation_then_rally(rng)

    signals = strategy.generate_signals("SUI-PERP", _multi_tf(closes, rng), closes[-1])
    signal = signals[0]

    assert signal.take_profit < signal.entry_price < signal.stop_loss

    risk = signal.stop_loss - signal.entry_price
    reward = signal.entry_price - signal.take_profit
    assert signal.rrr == pytest.approx(reward / risk, rel=1e-6)
    assert signal.rrr >= strategy.min_rrr


def test_confidence_never_exceeds_one(strategy):
    """The confidence score is a probability-shaped quantity, not a sum."""
    rng = random.Random(42)
    closes = _consolidation_then_selloff(rng)

    signals = strategy.generate_signals("SUI-PERP", _multi_tf(closes, rng), closes[-1])

    assert 0.0 <= signals[0].confidence <= 1.0


# ---------------------------------------------------------------------------
# Cases that must not signal
# ---------------------------------------------------------------------------


def test_neutral_rsi_produces_no_signal(strategy):
    """Chop around the mean is not a mean-reversion setup."""
    rng = random.Random(42)
    closes = [100.0 + rng.uniform(-2, 2) for _ in range(100)]

    signals = strategy.generate_signals("SUI-PERP", _multi_tf(closes, rng), closes[-1])

    assert signals == []


def test_insufficient_history_produces_no_signal(strategy):
    """Ten candles cannot support a 14-period RSI or a 20-period band."""
    short = {
        "15m": {"high": [100.0] * 10, "low": [99.0] * 10, "close": [99.5] * 10},
        "1h": {"high": [100.0] * 10, "low": [99.0] * 10, "close": [99.5] * 10},
    }

    assert strategy.generate_signals("SUI-PERP", short, 99.5) == []


def test_missing_15m_slice_produces_no_signal(strategy):
    """The 15m slice carries the structure check and is not optional."""
    rng = random.Random(42)
    closes = _consolidation_then_selloff(rng)
    data = _multi_tf(closes, rng)
    del data["15m"]

    assert strategy.generate_signals("SUI-PERP", data, closes[-1]) == []


def test_entry_cooldown_suppresses_an_immediate_second_signal():
    """Back-to-back identical setups must not stack entries on one symbol.

    The cooldown is passed explicitly rather than left to the environment:
    MEAN_REVERSION_COOLDOWN_MINUTES defaults to 0, which disables the
    mechanism, so a test that relied on the default would assert nothing.
    """
    strategy = MeanReversionStrategy(
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        min_confidence=0.6,
        cooldown_minutes=60,
    )
    rng = random.Random(42)
    closes = _consolidation_then_selloff(rng)
    data = _multi_tf(closes, rng)

    first = strategy.generate_signals("SUI-PERP", data, closes[-1])
    second = strategy.generate_signals("SUI-PERP", data, closes[-1])

    assert len(first) == 1
    assert second == []


def test_cooldown_is_scoped_per_symbol():
    """One symbol's cooldown must not mute a different symbol."""
    strategy = MeanReversionStrategy(
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        min_confidence=0.6,
        cooldown_minutes=60,
    )
    rng = random.Random(42)
    closes = _consolidation_then_selloff(rng)
    data = _multi_tf(closes, rng)

    assert len(strategy.generate_signals("SUI-PERP", data, closes[-1])) == 1
    assert len(strategy.generate_signals("BTC-PERP", data, closes[-1])) == 1


# ---------------------------------------------------------------------------
# Multi-timeframe behaviour
# ---------------------------------------------------------------------------


def test_divergent_timeframes_still_signal(strategy):
    """A 15m oversold setup signals even when the 1h slice is neutral.

    The old script asserted the opposite - that both timeframes had to
    confirm. That gate was removed on purpose: generate_signals carries an
    explicit "REMOVED: Multi-TF RSI alignment requirement" comment on both the
    long and short branches, and the emitted log line says "no MTF alignment
    required". So the script's expectation is the stale side.

    Caveat, reported separately and deliberately NOT asserted here because
    asserting it would pin a defect: the 1h slice is no longer read at all.
    generate_signals passes ``rsi_1h=trigger_rsi`` into the signal builders,
    where trigger_rsi is the 5m RSI when 5m execution data is supplied and the
    15m RSI otherwise. The confidence formula still contains a
    ``mtf_alignment`` term worth 30% that is computed from that value, so with
    no 5m data it is an exact duplicate of the rsi_strength term rather than a
    cross-timeframe check.
    """
    rng = random.Random(42)

    # Same 15m series that test_oversold_conditions_produce_a_long_signal
    # uses, so the only variable changed here is the 1h slice.
    closes_15m = _consolidation_then_selloff(rng)
    aligned = _multi_tf(closes_15m, rng)
    closes_1h = [100.0 + rng.uniform(-1, 1) for _ in range(100)]

    divergent = {
        "15m": aligned["15m"],
        "1h": {
            "high": [c + 0.2 for c in closes_1h],
            "low": [c - 0.2 for c in closes_1h],
            "close": closes_1h,
        },
    }

    signals = strategy.generate_signals("SUI-PERP", divergent, closes_15m[-1])

    assert len(signals) == 1
    assert signals[0].side is OrderSide.BUY


def test_signal_carries_the_indicators_downstream_consumers_read(strategy):
    """SignalLogger and the dashboard read these keys off every signal."""
    rng = random.Random(42)
    closes = _consolidation_then_selloff(rng)

    signals = strategy.generate_signals("SUI-PERP", _multi_tf(closes, rng), closes[-1])

    indicators = signals[0].indicators
    assert "rsi_15m" in indicators
    assert "rsi_1h" in indicators
    for key in ("rsi_15m", "rsi_1h"):
        assert 0.0 <= indicators[key] <= 100.0
