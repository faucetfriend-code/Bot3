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

    The caveat this docstring used to carry - that the 1h slice was never
    read, and that the 30%-weighted ``mtf_alignment`` term was fed the trigger
    RSI under the name ``rsi_1h`` - has since been fixed. The blend is now
    selectable via MEAN_REVERSION_MTF_CONFIDENCE and the defective form is
    reachable only as ``legacy``; see the MTF confidence section below.
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


# ---------------------------------------------------------------------------
# MTF confidence term (MEAN_REVERSION_MTF_CONFIDENCE)
#
# The confidence blend carried a third term worth 30% fed with a value named
# rsi_1h that was never a 1h RSI - it was the trigger RSI (5m when execution
# data was supplied, else the 15m RSI). With no 5m data that term was
# arithmetically identical to rsi_strength, collapsing the blend to 0.7/0.3.
# These tests pin all three blends, both signal directions, and the
# no-5m-data case that makes the two terms collide.
# ---------------------------------------------------------------------------


def _neutral_1h(rng, n=100):
    """A 1h slice sitting mid-range, so its RSI is nowhere near an extreme."""
    closes = [100.0 + rng.uniform(-1.0, 1.0) for _ in range(n)]
    return {
        "high": [c + 0.2 for c in closes],
        "low": [c - 0.2 for c in closes],
        "close": closes,
    }


def _long_components(signal, strategy):
    """Recover (rsi_strength, bb_proximity) for a LONG from published data.

    Derived only from the indicator payload the signal publishes, so the
    expectation is independent of the strategy's internal arithmetic.
    """
    ind = signal.indicators
    rsi_strength = (strategy.rsi_oversold - ind["rsi_15m"]) / strategy.rsi_oversold
    span = ind["middle_bb"] - ind["lower_bb"]
    bb_proximity = 1.0 - ((signal.entry_price - ind["lower_bb"]) / span)
    return rsi_strength, bb_proximity


def _short_components(signal, strategy):
    """Recover (rsi_strength, bb_proximity) for a SHORT from published data."""
    ind = signal.indicators
    top = 100.0 - strategy.rsi_overbought
    rsi_strength = (ind["rsi_15m"] - strategy.rsi_overbought) / top
    span = ind["upper_bb"] - ind["middle_bb"]
    bb_proximity = 1.0 - ((ind["upper_bb"] - signal.entry_price) / span)
    return rsi_strength, bb_proximity


def _clamp(value):
    return max(0.0, min(1.0, value))


def _build(mode):
    return MeanReversionStrategy(
        rsi_oversold=30.0,
        rsi_overbought=70.0,
        min_confidence=0.6,
        mtf_confidence_mode=mode,
    )


def test_legacy_blend_collapses_to_two_terms_without_5m_data():
    """The defect, pinned: with no 5m data legacy degenerates to 0.7/0.3.

    mtf_alignment is computed from the trigger RSI, which without execution
    data IS the 15m RSI, making it an exact duplicate of rsi_strength.
    """
    strategy = _build("legacy")
    rng = random.Random(42)
    closes = _consolidation_then_selloff(rng)

    signal = strategy.generate_signals(
        "SUI-PERP", _multi_tf(closes, rng), closes[-1]
    )[0]

    rsi_strength, bb_proximity = _long_components(signal, strategy)
    assert signal.confidence == pytest.approx(
        _clamp(0.7 * rsi_strength + 0.3 * bb_proximity), rel=1e-9
    )


def test_legacy_blend_collapses_on_the_short_path_too():
    """Mirror image of the collapse on _create_short_signal."""
    strategy = _build("legacy")
    rng = random.Random(42)
    closes = _consolidation_then_rally(rng)

    signal = strategy.generate_signals(
        "SUI-PERP", _multi_tf(closes, rng), closes[-1]
    )[0]

    assert signal.side is OrderSide.SELL
    rsi_strength, bb_proximity = _short_components(signal, strategy)
    assert signal.confidence == pytest.approx(
        _clamp(0.7 * rsi_strength + 0.3 * bb_proximity), rel=1e-9
    )


@pytest.mark.parametrize(
    "series,components",
    [
        (_consolidation_then_selloff, _long_components),
        (_consolidation_then_rally, _short_components),
    ],
)
def test_off_blend_renormalises_the_two_real_terms(series, components):
    """Dropping the vestigial term keeps the surviving 4:3 ratio."""
    strategy = _build("off")
    rng = random.Random(42)
    closes = series(rng)

    signal = strategy.generate_signals(
        "SUI-PERP", _multi_tf(closes, rng), closes[-1]
    )[0]

    rsi_strength, bb_proximity = components(signal, strategy)
    expected = (4.0 / 7.0) * rsi_strength + (3.0 / 7.0) * bb_proximity
    assert signal.confidence == pytest.approx(_clamp(expected), rel=1e-9)


def test_rsi_1h_blend_actually_reads_the_1h_slice():
    """A neutral 1h slice must move confidence in rsi_1h mode - and only there.

    Legacy never reads the 1h slice, so swapping an oversold 1h series for a
    neutral one must leave legacy confidence untouched while lowering it under
    the real-1h blend.
    """
    rng = random.Random(42)
    closes = _consolidation_then_selloff(rng)
    aligned = _multi_tf(closes, rng)
    divergent = {"15m": aligned["15m"], "1h": _neutral_1h(random.Random(7))}

    legacy_aligned = _build("legacy").generate_signals(
        "SUI-PERP", aligned, closes[-1]
    )[0]
    legacy_divergent = _build("legacy").generate_signals(
        "SUI-PERP", divergent, closes[-1]
    )[0]
    real_aligned = _build("rsi_1h").generate_signals(
        "SUI-PERP", aligned, closes[-1]
    )[0]
    real_divergent = _build("rsi_1h").generate_signals(
        "SUI-PERP", divergent, closes[-1]
    )[0]

    # Legacy is blind to the 1h slice.
    assert legacy_aligned.confidence == pytest.approx(
        legacy_divergent.confidence, rel=1e-9
    )
    # The real blend is not: a neutral 1h withdraws support.
    assert real_divergent.confidence < real_aligned.confidence


def test_indicators_never_relabel_the_trigger_rsi_as_1h():
    """rsi_1h must carry the 1h RSI, never the 5m trigger it once carried."""
    from trading_bot_v2.indicators import calculate_rsi

    strategy = _build("rsi_1h")
    rng = random.Random(42)
    closes = _consolidation_then_selloff(rng)
    frames = {"15m": _multi_tf(closes, rng)["15m"], "1h": _neutral_1h(random.Random(7))}
    execution = {"5m": _multi_tf(_consolidation_then_selloff(random.Random(3)), rng)["15m"]}

    signal = strategy.generate_signals(
        "SUI-PERP", frames, closes[-1], execution_tf_data=execution
    )[0]

    ind = signal.indicators
    expected_1h = calculate_rsi(frames["1h"]["close"], period=strategy.rsi_period)
    expected_trigger = calculate_rsi(
        execution["5m"]["close"], period=strategy.rsi_period
    )

    assert ind["rsi_trigger_tf"] == "5m"
    assert ind["rsi_trigger"] == pytest.approx(expected_trigger, rel=1e-9)
    assert ind["rsi_1h"] == pytest.approx(expected_1h, rel=1e-9)
    # The whole point: these are different numbers now.
    assert ind["rsi_1h"] != pytest.approx(ind["rsi_trigger"], rel=1e-6)


def test_rsi_1h_key_is_absent_when_there_is_no_1h_slice():
    """Better no key than a key holding 15m data under a 1h name."""
    strategy = _build("rsi_1h")
    rng = random.Random(42)
    closes = _consolidation_then_selloff(rng)

    signal = strategy.generate_signals(
        "SUI-PERP", {"15m": _multi_tf(closes, rng)["15m"]}, closes[-1]
    )[0]

    assert "rsi_1h" not in signal.indicators
    assert signal.indicators["rsi_trigger_tf"] == "15m"


def test_notes_label_every_rsi_with_its_own_timeframe():
    """The old notes rendered 'RSI=x/x', implying a 15m/1h pair."""
    strategy = _build("rsi_1h")
    rng = random.Random(42)
    closes = _consolidation_then_selloff(rng)

    signal = strategy.generate_signals(
        "SUI-PERP", _multi_tf(closes, rng), closes[-1]
    )[0]

    assert "RSI_15m=" in signal.notes


def test_unknown_mtf_mode_falls_back_to_legacy():
    """A typo must not silently invent a fourth blend."""
    strategy = MeanReversionStrategy(mtf_confidence_mode="nonsense")
    assert strategy.mtf_confidence_mode == "legacy"
