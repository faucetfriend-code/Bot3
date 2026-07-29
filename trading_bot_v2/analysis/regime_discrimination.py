"""Offline regime-label discrimination and mechanism decomposition.

Three questions, one harness:

1. **Discrimination** - does the regime label carry information about what
   the market does NEXT? For each regime, forward realized volatility,
   forward directional efficiency and forward return are compared against
   the unconditional distribution. Reported as EFFECT SIZES (Kruskal-Wallis
   epsilon-squared across the five regimes, Cliff's delta per regime versus
   the rest) with a circular-rotation null that preserves the
   autocorrelation of both the label series and the outcome series.
2. **Decomposition** - how much of a regime's share of the tape comes from
   the raw threshold, versus the hysteresis exit band, versus the minimum
   dwell time, versus the 2-count confirmation? Each mechanism is disabled
   in turn and the shares recomputed.
3. **Knee** - is there any ADX (or volatility-score) level at which forward
   behaviour actually changes? If the relation is smooth and monotone there
   is no natural threshold to pick and every cut point is arbitrary.

Why a re-implementation of the detector's state machine
-------------------------------------------------------
``MarketRegimeDetector`` computes ADX and the volatility score from raw
candles and then folds them through a stateful classifier. The indicators
are INDEPENDENT of every threshold; only the classifier is not. Splitting
the two lets a threshold sweep re-use one indicator pass, which is what
makes the decomposition affordable (26s of indicator work, then each
configuration is a linear scan).

``replay_labels`` therefore mirrors ``detect_regime`` plus
``_detect_regime_with_confirmation`` exactly, and
``test_regime_discrimination.py`` asserts that equivalence bar for bar
against the real detector. If that test fails, this module is lying and
the numbers in ``docs/REGIME-DISCRIMINATION.md`` are void.

Cadence: the backtest engine calls ``detect_regime_cached`` on every 5m
bar with a 1h cache TTL driven by simulated time, so the confirmed regime
is recomputed once per simulated hour off the most recent 4h candle. This
module replays on that same hourly grid, which is why its regime shares
reproduce the campaign's exactly.

Pure analysis - no database writes, no event emission, no order flow.

Usage:
    # Freeze the store: auto-download advances the trailing edge and makes
    # runs non-reproducible (docs/FOLLOW-UPS.md item 8e).
    DATA_AUTODOWNLOAD=false python -m trading_bot_v2.analysis.regime_discrimination \\
        --mode all --data-dir /abs/path/to/backtesting/data

    # BACKTEST_DATA_DIR in .env is RELATIVE and load_dotenv(override=True)
    # beats the shell, so a git worktree MUST pass --data-dir absolutely.
"""

import argparse
import bisect
import json
import math
import os
import random
import statistics as st
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from ..backtesting.data_loader import BacktestDataLoader
from ..indicators import calculate_adx
from ..market_regime import MarketRegime, MarketRegimeDetector
from ..volatility_regime import (
    BUCKET_REGIMES,
    DEFAULT_MIN_OBSERVATIONS,
    DEFAULT_REFERENCE_DAYS,
    DEFAULT_VOL_WINDOW,
    SHIPPED_BUCKETS,
    VolatilityRegimeClassifier,
    trailing_efficiency,
    trailing_realized_vol,
)

#: Regime values, ordered by prevalence over the 8-year campaign.
REGIME_ORDER: Tuple[str, ...] = (
    "trending_strong",
    "ranging_calm",
    "trending_moderate",
    "indecisive",
    "ranging_volatile",
)

#: Candles handed to the detector per timeframe by the backtest engine
#: (config.backtest_history_lookback). NOT the 150 used by
#: analysis/regime_stability.py - matching the engine is what makes these
#: shares comparable with the campaign and the regime census.
ENGINE_LOOKBACK = 60

#: First 4h index the engine will classify (it skips while i_4h < 28).
MIN_BAR_INDEX = 28

#: Forward horizon in 4h bars for the discrimination outcomes (24h).
DEFAULT_HORIZON = 6

#: Horizons swept when the discrimination mode reports stability.
HORIZON_SWEEP: Tuple[int, ...] = (1, 3, 6, 12, 30)

#: Median directional efficiency of a driftless Gaussian random walk over
#: H steps, from 200k Monte Carlo paths per horizon. The benchmark that
#: turns "efficiency 0.38" into a statement: at or below these values the
#: forward path is indistinguishable from a coin flip.
RANDOM_WALK_EFFICIENCY: Dict[int, float] = {
    1: 1.000,
    3: 0.588,
    6: 0.373,
    12: 0.254,
    30: 0.157,
}


@dataclass(frozen=True)
class RegimeParams:
    """Every knob the regime classifier reads, in one immutable bundle.

    Mirrors ``MarketRegimeDetector``'s constructor arguments plus the two
    anti-flap mechanisms, so a configuration can be varied without
    constructing a detector.

    Attributes:
        adx_trending: ADX above this enters TRENDING_STRONG.
        adx_ranging: ADX at or below this is a ranging market.
        adx_moderate: Lower bound of the moderate/indecisive band.
        vol_pct: Volatility score above which a FIRST classification
            (no previous confirmed regime) is RANGING_VOLATILE.
        adx_exit: Hysteresis exit band out of TRENDING_STRONG.
        vol_enter: Hysteresis entry band into RANGING_VOLATILE from
            another confirmed regime.
        vol_exit: Hysteresis exit band out of RANGING_VOLATILE.
        dwell_hours: Switches suppressed for this long after a confirmed
            switch. 0 disables the mechanism.
        confirm: Whether a candidate regime must be detected twice in a
            row before it is confirmed.
    """

    adx_trending: float = 25.0
    adx_ranging: float = 20.0
    adx_moderate: float = 20.0
    vol_pct: float = 65.0
    adx_exit: float = 22.0
    vol_enter: float = 68.0
    vol_exit: float = 60.0
    dwell_hours: float = 4.0
    confirm: bool = True

    @classmethod
    def from_detector(cls, detector: MarketRegimeDetector) -> "RegimeParams":
        """Snapshot a live detector's configuration.

        Args:
            detector: The detector whose settings to copy.

        Returns:
            A RegimeParams carrying that detector's thresholds and bands.
        """
        return cls(
            adx_trending=detector.adx_trending,
            adx_ranging=detector.adx_ranging,
            adx_moderate=detector.adx_moderate,
            vol_pct=detector.volatility_percentile,
            adx_exit=detector.adx_exit_trending,
            vol_enter=detector.vol_score_enter,
            vol_exit=detector.vol_score_exit,
            dwell_hours=detector.min_dwell_hours,
        )


@dataclass(frozen=True)
class BarFeatures:
    """Threshold-independent indicators for one 4h bar.

    Attributes:
        time: Bar close timestamp.
        adx: ADX(14) over the engine's rolling 60-bar window.
        slope_falling: Whether ADX is below its value 3 bars prior.
        vol_score: Detector volatility score (0-100), or None when it
            could not be computed.
        close: Bar close price.
    """

    time: datetime
    adx: float
    slope_falling: bool
    vol_score: Optional[float]
    close: float


# ----------------------------------------------------------------------
# Indicator pass (threshold-independent, computed once)
# ----------------------------------------------------------------------


def _parse_timestamp(raw: Any) -> datetime:
    """Parse a candle timestamp (ISO string, datetime, or epoch number).

    Args:
        raw: Timestamp in any of the forms the data loader emits.

    Returns:
        A naive datetime.
    """
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        value = float(raw)
        if value > 1e12:
            value /= 1000.0
        return datetime.fromtimestamp(value)
    return datetime.fromisoformat(str(raw).strip())


def precompute_features(
    symbol: str,
    start: str,
    end: str,
    data_dir: str,
    lookback: int = ENGINE_LOOKBACK,
) -> List[BarFeatures]:
    """Compute per-4h-bar ADX, ADX slope and volatility score.

    These three quantities are what the classifier reads, and none of
    them depends on any threshold - which is the whole point of splitting
    them out. One pass over 25k bars costs ~10s; every subsequent
    configuration is then a linear scan.

    Args:
        symbol: Trading pair, e.g. "BTC-USDC".
        start: Inclusive start date (YYYY-MM-DD).
        end: Inclusive end date (YYYY-MM-DD).
        data_dir: Candle store directory. Pass an ABSOLUTE path from a
            worktree - BACKTEST_DATA_DIR is relative and .env wins.
        lookback: Candles handed to the detector per call. Defaults to
            the engine's 60 so shares match the campaign.

    Returns:
        BarFeatures per classifiable bar, oldest first.

    Raises:
        SystemExit: When the store has too few candles to classify any bar.
    """
    loader = BacktestDataLoader(symbol=symbol, data_dir=data_dir)
    candles = loader.get_candles("4h", start, end)
    closes = candles["close"]
    n = len(closes)
    if n <= MIN_BAR_INDEX:
        raise SystemExit(
            f"Not enough 4h candles for {symbol} in [{start}, {end}]: "
            f"got {n}, need more than {MIN_BAR_INDEX}"
        )

    # Only used for its _adx_slope_falling / _calculate_volatility_score
    # helpers; thresholds are irrelevant here.
    helper = MarketRegimeDetector()
    out: List[BarFeatures] = []
    for i in range(MIN_BAR_INDEX, n):
        lo = max(0, i - lookback + 1)
        highs = candles["high"][lo : i + 1]
        lows = candles["low"][lo : i + 1]
        window = closes[lo : i + 1]
        try:
            adx = calculate_adx(highs, lows, window, period=helper.adx_period)
        except Exception as exc:  # noqa: BLE001 - one unusable bar
            logger.debug(f"{symbol}: ADX failed at bar {i}: {exc}")
            continue
        try:
            vol = helper._calculate_volatility_score(highs, lows, window)
        except Exception as exc:  # noqa: BLE001 - one unusable bar
            logger.debug(f"{symbol}: vol score failed at bar {i}: {exc}")
            vol = None
        out.append(
            BarFeatures(
                time=_parse_timestamp(candles["timestamp"][i]),
                adx=adx,
                slope_falling=helper._adx_slope_falling(highs, lows, window, adx),
                vol_score=vol,
                close=float(closes[i]),
            )
        )
    return out


# ----------------------------------------------------------------------
# Classifier + confirmation state machine (mirrors MarketRegimeDetector)
# ----------------------------------------------------------------------


def classify(
    params: RegimeParams,
    bar: BarFeatures,
    previous: Optional[str],
) -> str:
    """Classify one bar, mirroring ``MarketRegimeDetector.detect_regime``.

    Args:
        params: Threshold configuration.
        bar: Precomputed indicators for this bar.
        previous: Previously CONFIRMED regime value, or None on the first
            classification (which is what selects the raw ``vol_pct``
            entry threshold over the stricter ``vol_enter`` band).

    Returns:
        A MarketRegime value string.
    """
    if previous == "trending_strong" and bar.adx >= params.adx_exit:
        return "trending_strong"
    if bar.adx > params.adx_trending:
        return "trending_strong"
    if params.adx_moderate < bar.adx <= params.adx_trending:
        return "indecisive" if bar.slope_falling else "trending_moderate"
    if bar.adx <= params.adx_ranging:
        if bar.vol_score is None:
            return "ranging_calm"
        if previous == "ranging_volatile":
            return (
                "ranging_volatile"
                if bar.vol_score >= params.vol_exit
                else "ranging_calm"
            )
        threshold = params.vol_pct if previous is None else params.vol_enter
        return "ranging_volatile" if bar.vol_score > threshold else "ranging_calm"
    return "indecisive"


def hourly_grid(start: datetime, end: datetime) -> List[datetime]:
    """Build the hourly detection grid the engine's 1h cache TTL produces.

    Args:
        start: First detection time (inclusive).
        end: Last detection time (inclusive).

    Returns:
        Hourly datetimes from start through end.
    """
    out: List[datetime] = []
    cursor = start
    while cursor <= end:
        out.append(cursor)
        cursor += timedelta(hours=1)
    return out


def replay_confirmed(
    hours: Sequence[datetime],
    detect: Callable[[Optional[str], datetime], Optional[str]],
    dwell_hours: float,
    confirm: bool,
) -> List[Optional[str]]:
    """Replay the confirmed-regime state machine over a detection grid.

    Mirrors ``MarketRegimeDetector._detect_regime_with_confirmation``,
    including the ordering quirk that the dwell check runs BEFORE the
    2-count confirmation and clears any pending candidate when it fires.

    Factored out of ``replay_labels`` so a second taxonomy runs through
    the SAME anti-flap machinery rather than a lookalike - otherwise a
    comparison between taxonomies would also be comparing two different
    confirmation state machines.

    Args:
        hours: Detection times (the engine's 1h cache-refresh cadence).
        detect: ``(confirmed_state, now) -> candidate label``. Returning
            None means "nothing classifiable at this time" (no bar yet,
            or the classifier is still warming up); the confirmed state
            is left untouched and None is recorded.
        dwell_hours: Switches suppressed for this long after a confirmed
            switch. 0 disables the mechanism.
        confirm: Whether a candidate must be detected twice in a row.

    Returns:
        The confirmed label at each detection time, or None.
    """
    state: Optional[str] = None
    pending: Optional[Tuple[str, int]] = None
    last_switch: Optional[datetime] = None
    labels: List[Optional[str]] = []

    for now in hours:
        detected = detect(state, now)
        if detected is None:
            labels.append(None)
            continue

        if state is None:
            state = detected
        elif detected == state:
            pending = None
        else:
            suppressed = False
            if last_switch is not None:
                elapsed = (now - last_switch).total_seconds() / 3600.0
                if elapsed < dwell_hours:
                    pending = None
                    suppressed = True
            if not suppressed:
                if not confirm:
                    state, last_switch, pending = detected, now, None
                elif pending is not None and pending[0] == detected:
                    if pending[1] >= 1:
                        state, last_switch, pending = detected, now, None
                    else:
                        pending = (detected, pending[1] + 1)
                else:
                    pending = (detected, 1)
        labels.append(state)
    return labels


def replay_labels(
    bars: Sequence[BarFeatures],
    hours: Sequence[datetime],
    params: RegimeParams,
) -> List[Optional[str]]:
    """Replay the ADX classifier + confirmation over an hourly grid.

    Args:
        bars: Precomputed features, oldest first.
        hours: Detection times (the engine's 1h cache-refresh cadence).
        params: Threshold and anti-flap configuration.

    Returns:
        The confirmed regime at each hour; None before the first bar.
    """
    times = [b.time for b in bars]

    def detect(state: Optional[str], now: datetime) -> Optional[str]:
        idx = bisect.bisect_right(times, now) - 1
        if idx < 0:
            return None
        return classify(params, bars[idx], state)

    return replay_confirmed(hours, detect, params.dwell_hours, params.confirm)


#: Label the quantile classifier emits before its trailing reference
#: window is long enough to rank against. It is a confirmed state inside
#: the replay (exactly as in the live detector) and is mapped to None -
#: "unlabelled" - on the way out.
WARMUP_LABEL = MarketRegime.VOL_WARMUP.value


def bucket_names(buckets: int) -> Tuple[str, ...]:
    """Label strings for a k-bucket quantile scheme.

    k = 3 uses the shipped enum values so the harness's labels are
    literally the strings the detector persists.

    Args:
        buckets: Number of buckets.

    Returns:
        One label per bucket, lowest first.
    """
    if buckets == SHIPPED_BUCKETS:
        return tuple(r.value for r in BUCKET_REGIMES)
    return tuple(f"vol_{i + 1}of{buckets}" for i in range(buckets))


def label_bars_quantile(
    bars: Sequence[BarFeatures],
    params: RegimeParams,
    buckets: int = SHIPPED_BUCKETS,
    metric: Any = trailing_realized_vol,
    names: Optional[Sequence[str]] = None,
    vol_window: int = DEFAULT_VOL_WINDOW,
    reference_days: float = DEFAULT_REFERENCE_DAYS,
    min_observations: int = DEFAULT_MIN_OBSERVATIONS,
    lookback: int = ENGINE_LOOKBACK,
) -> List[Optional[str]]:
    """Label each bar by trailing-quantile bucket of a trailing metric.

    Runs the SHIPPED ``VolatilityRegimeClassifier`` - not a
    re-implementation of it - over the same hourly detection grid and
    through the same confirmation state machine the ADX labels use. The
    classifier only ever sees closes up to and including the bar being
    labelled, so this is trailing-only by construction; the seeding path
    excludes the current bar for the same reason.

    Warmup is a REAL state inside the replay - the detector confirms
    ``VOL_WARMUP`` and needs the usual 2-count confirmation to leave it,
    so the harness must too or the two would disagree on the first bars
    of every symbol. It is mapped to None on the way out, which is how
    the statistics below already treat "no label yet" for ADX.

    Args:
        bars: Precomputed features, oldest first.
        params: Supplies the dwell/confirm anti-flap settings only.
        buckets: Number of quantile buckets.
        metric: ``(closes, window) -> value``; trailing-only.
        names: Bucket label strings; defaults to ``bucket_names``.
        vol_window: Bars in the trailing metric.
        reference_days: Calendar span of the trailing reference window.
        min_observations: Observations required before ranking.
        lookback: Candles handed to the classifier per call, matching the
            backtest engine's history slice.

    Returns:
        One label per bar, aligned with ``bars``.
    """
    if not bars:
        return []
    labels_for = tuple(names) if names is not None else bucket_names(buckets)
    times = [b.time for b in bars]
    closes = [b.close for b in bars]
    classifier = VolatilityRegimeClassifier(
        buckets=buckets,
        vol_window=vol_window,
        reference_days=reference_days,
        min_observations=min_observations,
        metric=metric,
    )

    def detect(state: Optional[str], now: datetime) -> Optional[str]:
        idx = bisect.bisect_right(times, now) - 1
        if idx < 0:
            return None
        window = closes[max(0, idx - lookback + 1) : idx + 1]
        result = classifier.classify("_", now, window)
        if result is None:
            return WARMUP_LABEL
        return labels_for[result.index]

    hours = hourly_grid(bars[0].time, bars[-1].time)
    labels = replay_confirmed(hours, detect, params.dwell_hours, params.confirm)
    at_hour = {h: lab for h, lab in zip(hours, labels)}
    return [
        None if at_hour.get(b.time) in (None, WARMUP_LABEL) else at_hour[b.time]
        for b in bars
    ]


def label_bars_two_axis(
    bars: Sequence[BarFeatures],
    params: RegimeParams,
    vol_buckets: int = 3,
    eff_buckets: int = 2,
    **kwargs: Any,
) -> List[Optional[str]]:
    """Label each bar by (volatility bucket, efficiency bucket).

    The second axis is TRAILING directional efficiency - net move over
    path length - which is a more direct measure of "is this trending"
    than ADX is. Both axes are ranked against their own trailing
    distributions by the same machinery, so the 2-D scheme inherits the
    no-lookahead property from the 1-D one.

    Args:
        bars: Precomputed features, oldest first.
        params: Supplies the dwell/confirm anti-flap settings.
        vol_buckets: Buckets on the volatility axis.
        eff_buckets: Buckets on the efficiency axis.
        **kwargs: Forwarded to ``label_bars_quantile``.

    Returns:
        Combined labels like ``"vol_high|eff_hi"``; None where either
        axis is unlabelled.
    """
    vol = label_bars_quantile(bars, params, buckets=vol_buckets, **kwargs)
    eff_names = (
        ("eff_lo", "eff_hi")
        if eff_buckets == 2
        else tuple(f"eff_{i + 1}of{eff_buckets}" for i in range(eff_buckets))
    )
    eff = label_bars_quantile(
        bars,
        params,
        buckets=eff_buckets,
        metric=trailing_efficiency,
        names=eff_names,
        **kwargs,
    )
    return [
        None if v is None or e is None else f"{v}|{e}" for v, e in zip(vol, eff)
    ]


def label_bars(
    bars: Sequence[BarFeatures], params: RegimeParams
) -> List[Optional[str]]:
    """Confirmed regime at each 4h bar close, replayed on the hourly grid.

    Args:
        bars: Precomputed features, oldest first.
        params: Threshold configuration.

    Returns:
        One label per bar, aligned with ``bars``.
    """
    if not bars:
        return []
    hours = hourly_grid(bars[0].time, bars[-1].time)
    labels = replay_labels(bars, hours, params)
    at_hour = {h: lab for h, lab in zip(hours, labels)}
    return [at_hour.get(b.time) for b in bars]


# ----------------------------------------------------------------------
# Forward outcomes
# ----------------------------------------------------------------------


def forward_outcomes(
    bars: Sequence[BarFeatures], horizon: int
) -> Dict[str, List[Optional[float]]]:
    """Measure what the market did over the NEXT ``horizon`` bars.

    No lookahead: the label for bar i is computed from candles up to and
    including bar i, and every outcome starts at bar i's close.

    Args:
        bars: Precomputed features, oldest first.
        horizon: Forward window in 4h bars.

    Returns:
        Dict of outcome name -> per-bar value (None where the forward
        window runs off the end):

        * ``fwd_vol`` - stdev of forward 4h log returns (realized vol).
        * ``efficiency`` - |net move| / sum(|bar moves|), the directional
          efficiency ratio. Scale-free; compare against
          RANDOM_WALK_EFFICIENCY, not against zero.
        * ``abs_ret`` - |forward return|.
        * ``fwd_ret`` - signed forward return.
    """
    closes = [b.close for b in bars]
    n = len(closes)
    fwd_ret: List[Optional[float]] = []
    fwd_vol: List[Optional[float]] = []
    efficiency: List[Optional[float]] = []
    for i in range(n):
        if i + horizon >= n:
            fwd_ret.append(None)
            fwd_vol.append(None)
            efficiency.append(None)
            continue
        segment = closes[i : i + horizon + 1]
        steps = [math.log(segment[k + 1] / segment[k]) for k in range(horizon)]
        gross = sum(abs(segment[k + 1] - segment[k]) for k in range(horizon))
        fwd_ret.append(segment[-1] / segment[0] - 1.0)
        fwd_vol.append(st.pstdev(steps) if horizon > 1 else abs(steps[0]))
        efficiency.append(abs(segment[-1] - segment[0]) / gross if gross > 0 else 0.0)
    return {
        "fwd_vol": fwd_vol,
        "efficiency": efficiency,
        "abs_ret": [None if x is None else abs(x) for x in fwd_ret],
        "fwd_ret": fwd_ret,
    }


# ----------------------------------------------------------------------
# Effect sizes
# ----------------------------------------------------------------------


def average_ranks(values: Sequence[float]) -> List[float]:
    """Compute 1-based average ranks, ties shared.

    Args:
        values: Numeric observations.

    Returns:
        Rank of each observation, in the input order.
    """
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def epsilon_squared(labels: Sequence[str], ranks: Sequence[float], n: int) -> float:
    """Kruskal-Wallis rank epsilon-squared across groups.

    The rank-based analogue of eta-squared: the share of rank variance
    the grouping explains, on 0..1. Ranks are invariant to which label
    sits where, so they are computed once and reused by the permutation
    null.

    Args:
        labels: Group label per observation.
        ranks: Average ranks of the outcome, same order as labels.
        n: Number of observations.

    Returns:
        Epsilon-squared in [0, 1]; 0 when fewer than two groups.
    """
    groups: Dict[str, List[float]] = {}
    for label, rank in zip(labels, ranks):
        entry = groups.get(label)
        if entry is None:
            groups[label] = [rank, 1.0]
        else:
            entry[0] += rank
            entry[1] += 1.0
    if len(groups) < 2 or n < 2:
        return 0.0
    grand = (n + 1) / 2.0
    h_stat = (
        12.0
        / (n * (n + 1))
        * sum(count * (total / count - grand) ** 2 for total, count in groups.values())
    )
    return h_stat / (n - 1)


def cliffs_delta(group_ranks: Sequence[float], n_group: int, n_rest: int) -> float:
    """Cliff's delta of one group against the rest, via the AUC identity.

    Args:
        group_ranks: Ranks of the group's observations within the pooled
            ranking.
        n_group: Size of the group.
        n_rest: Size of everything else.

    Returns:
        Delta in [-1, 1]. Romano's guide: |d| < 0.147 negligible,
        < 0.33 small, < 0.474 medium.
    """
    if n_group == 0 or n_rest == 0:
        return 0.0
    auc = (sum(group_ranks) - n_group * (n_group + 1) / 2.0) / (n_group * n_rest)
    return 2.0 * auc - 1.0


def rotation_pvalue(
    labels: Sequence[str],
    ranks: Sequence[float],
    observed: float,
    iterations: int,
    rng: random.Random,
) -> float:
    """P-value under a circular rotation of the label series.

    A plain shuffle would be far too generous here: both the label series
    (regimes persist for many bars) and the outcome series (volatility
    clusters, forward windows overlap) are strongly autocorrelated, so an
    iid null manufactures significance. Rotating the labels against the
    outcomes preserves the internal autocorrelation of BOTH series
    exactly and destroys only their alignment.

    Args:
        labels: Group label per observation, in time order.
        ranks: Average ranks of the outcome.
        observed: The unrotated epsilon-squared.
        iterations: Number of rotations to draw.
        rng: Seeded RNG, for reproducibility.

    Returns:
        (hits + 1) / (iterations + 1), floored at 1/(iterations+1).
    """
    n = len(labels)
    label_list = list(labels)
    hits = 0
    for _ in range(iterations):
        k = rng.randrange(n)
        rotated = label_list[k:] + label_list[:k]
        if epsilon_squared(rotated, ranks, n) >= observed:
            hits += 1
    return (hits + 1) / (iterations + 1)


def holm_threshold(rank_of_test: int, n_tests: int, alpha: float) -> float:
    """Holm-Bonferroni critical value for the k-th smallest p-value.

    Args:
        rank_of_test: 1-based position of this p-value when sorted
            ascending.
        n_tests: Total number of tests in the family.
        alpha: Family-wise error rate.

    Returns:
        The critical value this p-value must fall below.
    """
    return alpha / (n_tests - rank_of_test + 1)


# ----------------------------------------------------------------------
# Modes
# ----------------------------------------------------------------------


def run_discrimination(
    features: Dict[str, List[BarFeatures]],
    params: RegimeParams,
    horizon: int,
    iterations: int,
    seed: int,
) -> Dict[str, Any]:
    """Task 1: measure whether the regime label discriminates at all.

    Args:
        features: Symbol -> precomputed bars.
        params: Threshold configuration to label with.
        horizon: Primary forward horizon in 4h bars.
        iterations: Rotation-null draws per test.
        seed: RNG seed.

    Returns:
        Dict with "primary" (per symbol, per outcome: epsilon-squared,
        rotation p, per-regime medians and Cliff's deltas) and
        "horizon_sweep" (efficiency effect across HORIZON_SWEEP).
    """
    rng = random.Random(seed)
    result: Dict[str, Any] = {
        "horizon": horizon,
        "iterations": iterations,
        "random_walk_efficiency": RANDOM_WALK_EFFICIENCY.get(horizon),
        "primary": {},
        "horizon_sweep": {},
    }

    for symbol, bars in features.items():
        labels_all = label_bars(bars, params)
        outcomes = forward_outcomes(bars, horizon)
        per_outcome: Dict[str, Any] = {}
        for name, series in outcomes.items():
            paired = [
                (lab, val)
                for lab, val in zip(labels_all, series)
                if lab is not None and val is not None
            ]
            if len(paired) < 100:
                continue
            labels = [p[0] for p in paired]
            values = [p[1] for p in paired]
            n = len(values)
            ranks = average_ranks(values)
            observed = epsilon_squared(labels, ranks, n)
            p_value = rotation_pvalue(labels, ranks, observed, iterations, rng)
            by_regime: Dict[str, Any] = {}
            positions: Dict[str, List[int]] = {}
            for i, lab in enumerate(labels):
                positions.setdefault(lab, []).append(i)
            unconditional = st.median(values)
            for regime, idxs in positions.items():
                group = [values[i] for i in idxs]
                by_regime[regime] = {
                    "n": len(idxs),
                    "median": st.median(group),
                    "ratio_to_unconditional": (
                        st.median(group) / unconditional if unconditional else None
                    ),
                    "cliffs_delta": cliffs_delta(
                        [ranks[i] for i in idxs], len(idxs), n - len(idxs)
                    ),
                }
            per_outcome[name] = {
                "n": n,
                "epsilon_squared": observed,
                "rotation_p": p_value,
                "unconditional_median": unconditional,
                "by_regime": by_regime,
            }
        result["primary"][symbol] = per_outcome

        sweep: Dict[int, Any] = {}
        for h in HORIZON_SWEEP:
            eff = forward_outcomes(bars, h)["efficiency"]
            paired = [
                (lab, val)
                for lab, val in zip(labels_all, eff)
                if lab is not None and val is not None
            ]
            if len(paired) < 100:
                continue
            labels = [p[0] for p in paired]
            values = [p[1] for p in paired]
            n = len(values)
            ranks = average_ranks(values)
            observed = epsilon_squared(labels, ranks, n)
            positions = {}
            for i, lab in enumerate(labels):
                positions.setdefault(lab, []).append(i)
            sweep[h] = {
                "epsilon_squared": observed,
                "rotation_p": rotation_pvalue(labels, ranks, observed, iterations, rng),
                "random_walk_efficiency": RANDOM_WALK_EFFICIENCY.get(h),
                "deltas": {
                    reg: cliffs_delta(
                        [ranks[i] for i in idxs],
                        len(idxs),
                        n - len(idxs),
                    )
                    for reg, idxs in positions.items()
                },
                "medians": {
                    reg: st.median([values[i] for i in idxs])
                    for reg, idxs in positions.items()
                },
            }
        result["horizon_sweep"][symbol] = sweep

    return result


def _spearman(x: Sequence[float], y: Sequence[float]) -> float:
    """Spearman rank correlation.

    Args:
        x: First series.
        y: Second series, same length.

    Returns:
        Rho in [-1, 1]; 0 when either series is constant.
    """
    rx = average_ranks(x)
    ry = average_ranks(y)
    n = len(x)
    mx = sum(rx) / n
    my = sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return num / (dx * dy) if dx and dy else 0.0


def run_baselines(
    features: Dict[str, List[BarFeatures]],
    params: RegimeParams,
    horizon: int,
    trailing_window: int = 14,
) -> Dict[str, Any]:
    """Compare the 5-way label against the continuous signals it discards.

    The label is a lossy function of ADX and the volatility score, and
    the detector computes both anyway. If a single continuous variable
    predicts a forward outcome better than the whole regime taxonomy,
    the taxonomy is destroying information rather than creating it.

    ``epsilon_squared`` and ``rho ** 2`` are both shares of explained
    rank variance, so they are comparable on the same scale.

    Args:
        features: Symbol -> precomputed bars.
        params: Threshold configuration to label with.
        horizon: Forward horizon in 4h bars.
        trailing_window: Bars of trailing realized volatility.

    Returns:
        Symbol -> outcome -> {label epsilon-squared, |rho| and rho^2 for
        ADX, the volatility score, and trailing realized volatility}.
    """
    out: Dict[str, Any] = {}
    for symbol, bars in features.items():
        labels_all = label_bars(bars, params)
        outcomes = forward_outcomes(bars, horizon)
        closes = [b.close for b in bars]
        log_rets = [0.0] + [
            math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
        ]
        trailing: List[Optional[float]] = [None] * len(bars)
        for i in range(trailing_window, len(bars)):
            trailing[i] = st.pstdev(log_rets[i - trailing_window + 1 : i + 1])
        per_outcome: Dict[str, Any] = {}
        for name in ("fwd_vol", "efficiency", "abs_ret"):
            rows = [
                (lab, val, bar.adx, bar.vol_score, tv)
                for lab, val, bar, tv in zip(labels_all, outcomes[name], bars, trailing)
                if lab is not None
                and val is not None
                and bar.vol_score is not None
                and tv is not None
            ]
            if len(rows) < 100:
                continue
            values = [r[1] for r in rows]
            ranks = average_ranks(values)
            entry = {
                "n": len(rows),
                "label_epsilon_squared": epsilon_squared(
                    [r[0] for r in rows], ranks, len(rows)
                ),
            }
            for key, col in (("adx", 2), ("vol_score", 3), ("trailing_vol", 4)):
                rho = _spearman([r[col] for r in rows], values)
                entry[f"{key}_abs_rho"] = abs(rho)
                entry[f"{key}_rho_squared"] = rho * rho
            per_outcome[name] = entry
        out[symbol] = per_outcome
    return out


# ----------------------------------------------------------------------
# Taxonomy comparison (new scheme vs ADX vs the raw continuous variable)
# ----------------------------------------------------------------------


def epsilon_squared_coded(
    codes: Any, ranks: Any, n: int, n_groups: int
) -> float:
    """Vectorised Kruskal-Wallis epsilon-squared over integer group codes.

    Arithmetically identical to ``epsilon_squared`` - the same H statistic
    divided by ``n - 1`` - but ~100x faster, which is what makes a
    rotation null over six taxonomies x four outcomes x three symbols
    affordable. ``tests/test_volatility_regime.py`` pins the two against
    each other.

    Args:
        codes: int array of group codes, one per observation.
        ranks: float array of average ranks, same order.
        n: Number of observations.
        n_groups: Number of distinct group codes.

    Returns:
        Epsilon-squared in [0, 1]; 0 when fewer than two groups.
    """
    import numpy as np

    if n_groups < 2 or n < 2:
        return 0.0
    counts = np.bincount(codes, minlength=n_groups).astype(float)
    totals = np.bincount(codes, weights=ranks, minlength=n_groups)
    live = counts > 0
    if int(live.sum()) < 2:
        return 0.0
    grand = (n + 1) / 2.0
    means = totals[live] / counts[live]
    h_stat = (
        12.0 / (n * (n + 1)) * float(np.sum(counts[live] * (means - grand) ** 2))
    )
    return h_stat / (n - 1)


def rotation_pvalue_coded(
    codes: Any,
    ranks: Any,
    n_groups: int,
    observed: float,
    iterations: int,
    rng: random.Random,
) -> float:
    """Circular-rotation p-value over integer-coded labels.

    Same null as ``rotation_pvalue`` - rotate the label series against
    the outcome series, preserving the autocorrelation of both exactly
    and destroying only their alignment.

    Args:
        codes: int array of group codes, in time order.
        ranks: float array of average ranks.
        n_groups: Number of distinct group codes.
        observed: The unrotated epsilon-squared.
        iterations: Number of rotations to draw.
        rng: Seeded RNG.

    Returns:
        (hits + 1) / (iterations + 1).
    """
    import numpy as np

    n = len(codes)
    hits = 0
    for _ in range(iterations):
        k = rng.randrange(n)
        rotated = np.roll(codes, k)
        if epsilon_squared_coded(rotated, ranks, n, n_groups) >= observed:
            hits += 1
    return (hits + 1) / (iterations + 1)


def _partial(fn: Callable[..., Any], **kwargs: Any) -> Callable[..., Any]:
    """Bind keyword arguments to a labeller.

    Args:
        fn: The labelling function.
        **kwargs: Bound keyword arguments.

    Returns:
        A two-argument callable ``(bars, params) -> labels``.
    """

    def bound(bars: Sequence[BarFeatures], params: RegimeParams) -> List[
        Optional[str]
    ]:
        return fn(bars, params, **kwargs)

    return bound


def default_schemes() -> List[Tuple[str, Callable[..., Any]]]:
    """The taxonomies compared side by side, in report order.

    Returns:
        (name, labeller) pairs. ``adx_5way`` is the shipped taxonomy the
        discrimination study condemned; ``vol_qK`` are quantile buckets
        of trailing realized volatility; ``vol_q3xeff2`` is the 2-D
        (volatility x trailing directional efficiency) scheme.
    """
    return [
        ("adx_5way", label_bars),
        ("vol_q2", _partial(label_bars_quantile, buckets=2)),
        ("vol_q3", _partial(label_bars_quantile, buckets=3)),
        ("vol_q4", _partial(label_bars_quantile, buckets=4)),
        ("vol_q5", _partial(label_bars_quantile, buckets=5)),
        ("vol_q3xeff2", _partial(label_bars_two_axis)),
    ]


def run_taxonomy_comparison(
    features: Dict[str, List[BarFeatures]],
    params: RegimeParams,
    horizon: int,
    iterations: int,
    seed: int,
    schemes: Optional[Sequence[Tuple[str, Callable[..., Any]]]] = None,
    trailing_window: int = DEFAULT_VOL_WINDOW,
    outcomes: Sequence[str] = ("fwd_vol", "efficiency", "abs_ret", "fwd_ret"),
) -> Dict[str, Any]:
    """Score several taxonomies on the SAME bars, the same null, one table.

    Every scheme is evaluated on the intersection of bars that all of
    them label and for which the outcome and every continuous predictor
    exists. Without that common mask the comparison would silently be
    across different samples - the ADX labels start at bar 0 while a
    trailing-quantile scheme needs a reference window first.

    Args:
        features: Symbol -> precomputed bars.
        params: Threshold configuration for the ADX labels, and the
            dwell/confirm settings every scheme shares.
        horizon: Forward horizon in 4h bars.
        iterations: Rotation-null draws per test.
        seed: RNG seed.
        schemes: (name, labeller) pairs; defaults to ``default_schemes``.
        trailing_window: Bars of trailing realized volatility used as the
            continuous baseline.
        outcomes: Forward outcomes to score.

    Returns:
        Dict with "schemes", "by_symbol" (per outcome: n, per-scheme
        epsilon-squared and rotation p, continuous rho^2 baselines) and
        "shares" (per scheme, share of the common sample per label).
    """
    import numpy as np

    rng = random.Random(seed)
    scheme_list = list(schemes) if schemes is not None else default_schemes()
    result: Dict[str, Any] = {
        "horizon": horizon,
        "iterations": iterations,
        "trailing_window": trailing_window,
        "schemes": [name for name, _ in scheme_list],
        "by_symbol": {},
        "shares": {},
    }

    for symbol, bars in features.items():
        logger.info(f"taxonomy comparison: labelling {symbol}")
        labelings = {name: fn(bars, params) for name, fn in scheme_list}
        outcome_series = forward_outcomes(bars, horizon)
        closes = [b.close for b in bars]
        log_rets = [0.0] + [
            math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))
        ]
        trailing: List[Optional[float]] = [None] * len(bars)
        for i in range(trailing_window, len(bars)):
            trailing[i] = st.pstdev(log_rets[i - trailing_window + 1 : i + 1])

        per_outcome: Dict[str, Any] = {}
        shares: Dict[str, Dict[str, float]] = {}
        for name in outcomes:
            series = outcome_series[name]
            keep = [
                i
                for i in range(len(bars))
                if series[i] is not None
                and trailing[i] is not None
                and bars[i].vol_score is not None
                and all(labelings[s][i] is not None for s in labelings)
            ]
            if len(keep) < 100:
                continue
            values = [series[i] for i in keep]
            n = len(values)
            ranks_list = average_ranks(values)
            ranks = np.asarray(ranks_list, dtype=float)
            entry: Dict[str, Any] = {"n": n, "by_scheme": {}}
            for scheme_name in result["schemes"]:
                labels = [labelings[scheme_name][i] for i in keep]
                order = sorted(set(labels))
                code_of = {lab: k for k, lab in enumerate(order)}
                codes = np.asarray([code_of[lab] for lab in labels], dtype=np.int64)
                observed = epsilon_squared_coded(codes, ranks, n, len(order))
                p_value = rotation_pvalue_coded(
                    codes, ranks, len(order), observed, iterations, rng
                )
                entry["by_scheme"][scheme_name] = {
                    "epsilon_squared": observed,
                    "rotation_p": p_value,
                    "groups": len(order),
                }
                if scheme_name not in shares:
                    shares[scheme_name] = {
                        lab: 100.0 * labels.count(lab) / n for lab in order
                    }
            for key, column in (
                ("adx", [bars[i].adx for i in keep]),
                ("vol_score", [bars[i].vol_score for i in keep]),
                ("trailing_vol", [trailing[i] for i in keep]),
            ):
                rho = _spearman(column, values)
                entry[f"{key}_rho_squared"] = rho * rho
            per_outcome[name] = entry
        result["by_symbol"][symbol] = per_outcome
        result["shares"][symbol] = shares

    return result


def run_bucket_detail(
    features: Dict[str, List[BarFeatures]],
    params: RegimeParams,
    horizon: int,
    scheme: str = "vol_q3",
) -> Dict[str, Any]:
    """Per-bucket medians and Cliff's deltas for one taxonomy.

    Args:
        features: Symbol -> precomputed bars.
        params: Threshold / anti-flap configuration.
        horizon: Forward horizon in 4h bars.
        scheme: Name of the scheme in ``default_schemes``.

    Returns:
        Symbol -> outcome -> label -> {n, share, median, ratio, delta}.
    """
    labeller = dict(default_schemes())[scheme]
    out: Dict[str, Any] = {"scheme": scheme, "horizon": horizon, "by_symbol": {}}
    for symbol, bars in features.items():
        labels_all = labeller(bars, params)
        outcomes = forward_outcomes(bars, horizon)
        per_outcome: Dict[str, Any] = {}
        for name in ("fwd_vol", "efficiency", "abs_ret", "fwd_ret"):
            paired = [
                (lab, val)
                for lab, val in zip(labels_all, outcomes[name])
                if lab is not None and val is not None
            ]
            if len(paired) < 100:
                continue
            labels = [p[0] for p in paired]
            values = [p[1] for p in paired]
            n = len(values)
            ranks = average_ranks(values)
            unconditional = st.median(values)
            positions: Dict[str, List[int]] = {}
            for i, lab in enumerate(labels):
                positions.setdefault(lab, []).append(i)
            per_outcome[name] = {
                lab: {
                    "n": len(idxs),
                    "share": 100.0 * len(idxs) / n,
                    "median": st.median([values[i] for i in idxs]),
                    "ratio_to_unconditional": (
                        st.median([values[i] for i in idxs]) / unconditional
                        if unconditional
                        else None
                    ),
                    "cliffs_delta": cliffs_delta(
                        [ranks[i] for i in idxs], len(idxs), n - len(idxs)
                    ),
                }
                for lab, idxs in sorted(positions.items())
            }
        out["by_symbol"][symbol] = per_outcome
    return out


#: Mechanism-decomposition configurations. Each disables exactly one
#: anti-flap or hysteresis mechanism, so the delta against the baseline
#: is that mechanism's contribution.
DECOMPOSITION_CASES: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("baseline (as shipped)", {}),
    ("no ADX exit band (exit = enter)", {"adx_exit": None}),
    ("no vol bands (enter = exit = raw)", {"vol_bands": None}),
    ("no min dwell (0h)", {"dwell_hours": 0.0}),
    ("no 2-count confirmation", {"confirm": False}),
    ("no state at all (raw thresholds)", {"raw": None}),
)


def _case_params(params: RegimeParams, overrides: Dict[str, Any]) -> RegimeParams:
    """Apply one decomposition case's overrides.

    Args:
        params: The baseline configuration.
        overrides: Case overrides; the sentinel keys "adx_exit",
            "vol_bands" and "raw" mean "collapse this band onto its
            entry threshold" rather than "set to this value".

    Returns:
        The configuration for that case.
    """
    if "raw" in overrides:
        return replace(
            params,
            adx_exit=params.adx_trending,
            vol_enter=params.vol_pct,
            vol_exit=params.vol_pct,
            dwell_hours=0.0,
            confirm=False,
        )
    if "adx_exit" in overrides:
        return replace(params, adx_exit=params.adx_trending)
    if "vol_bands" in overrides:
        return replace(params, vol_enter=params.vol_pct, vol_exit=params.vol_pct)
    return replace(params, **overrides)


def _shares(
    features: Dict[str, List[BarFeatures]],
    windows: Dict[str, List[Tuple[str, str]]],
    params: RegimeParams,
) -> Dict[str, float]:
    """Regime shares of the tape, hour-weighted, over a window series.

    Each window replays with a FRESH state machine, matching the engine's
    per-backtest detector.

    Args:
        features: Symbol -> precomputed bars.
        windows: Symbol -> list of (start, end) ISO date pairs.
        params: Threshold configuration.

    Returns:
        Regime value -> percentage share.
    """
    counts: Dict[str, int] = {}
    total = 0
    for symbol, bars in features.items():
        for start, end in windows.get(symbol, []):
            grid = hourly_grid(
                datetime.fromisoformat(start), datetime.fromisoformat(end)
            )
            for label in replay_labels(bars, grid[:-1], params):
                if label is None:
                    continue
                counts[label] = counts.get(label, 0) + 1
                total += 1
    if not total:
        return {}
    return {k: 100.0 * v / total for k, v in counts.items()}


def run_decomposition(
    features: Dict[str, List[BarFeatures]],
    windows: Dict[str, List[Tuple[str, str]]],
    params: RegimeParams,
    extra_thresholds: Sequence[float] = (30.0,),
) -> Dict[str, Any]:
    """Task 2: attribute each regime's share to a specific mechanism.

    Args:
        features: Symbol -> precomputed bars.
        windows: Symbol -> (start, end) window series.
        params: Baseline configuration.
        extra_thresholds: Additional adx_trending values to report,
            holding everything else at baseline.

    Returns:
        Dict with "cases" (label -> regime shares) in report order.
    """
    cases: Dict[str, Dict[str, float]] = {}
    for label, overrides in DECOMPOSITION_CASES:
        cases[label] = _shares(features, windows, _case_params(params, overrides))
    for value in extra_thresholds:
        cases[f"adx_trending={value:g} (else as shipped)"] = _shares(
            features, windows, replace(params, adx_trending=value)
        )
        cases[f"adx_trending={value:g}, exit={value - 3:g}"] = _shares(
            features,
            windows,
            replace(params, adx_trending=value, adx_exit=value - 3.0),
        )
    cases["pre-Jul2026 (30/25/25/75, no state)"] = _shares(
        features,
        windows,
        RegimeParams(
            adx_trending=30.0,
            adx_ranging=25.0,
            adx_moderate=25.0,
            vol_pct=75.0,
            adx_exit=30.0,
            vol_enter=75.0,
            vol_exit=75.0,
            dwell_hours=0.0,
            confirm=False,
        ),
    )
    return {"cases": cases}


#: ADX bucket edges for the knee scan. Deliberately finer around the
#: 20/25/30 region where the thresholds live.
ADX_EDGES: Tuple[float, ...] = (
    0,
    12,
    15,
    17.5,
    20,
    22.5,
    25,
    27.5,
    30,
    35,
    40,
    1e9,
)

#: Volatility-score bucket edges, finer around the 60/65/68 bands.
VOL_EDGES: Tuple[float, ...] = (
    0,
    25,
    35,
    45,
    55,
    60,
    65,
    68,
    75,
    85,
    1e9,
)


def run_knee(features: Dict[str, List[BarFeatures]], horizon: int) -> Dict[str, Any]:
    """Task 3 criterion: does forward behaviour have a threshold structure?

    Pools every symbol's bars after normalising the scale-dependent
    outcomes by that symbol's own median, then reports forward behaviour
    per ADX and per volatility-score bucket. A knee would justify a cut
    point. A smooth monotone relation says every cut point is arbitrary
    and the discretisation is throwing information away.

    Args:
        features: Symbol -> precomputed bars.
        horizon: Forward horizon in 4h bars.

    Returns:
        Dict with "n", "adx_quantiles", "adx_shares", "adx_buckets" and
        "vol_buckets".
    """
    pooled: List[Tuple[float, float, float, float, float]] = []
    for bars in features.values():
        outcomes = forward_outcomes(bars, horizon)
        vols = [v for v in outcomes["fwd_vol"] if v is not None]
        abs_rets = [v for v in outcomes["abs_ret"] if v is not None]
        if not vols or not abs_rets:
            continue
        med_vol = st.median(vols)
        med_abs = st.median(abs_rets)
        for bar, fv, eff, ar in zip(
            bars,
            outcomes["fwd_vol"],
            outcomes["efficiency"],
            outcomes["abs_ret"],
        ):
            if fv is None or bar.vol_score is None:
                continue
            pooled.append(
                (
                    bar.adx,
                    bar.vol_score,
                    fv / med_vol if med_vol else 0.0,
                    eff,
                    ar / med_abs if med_abs else 0.0,
                )
            )
    pooled.sort(key=lambda row: row[0])
    n = len(pooled)
    if not n:
        return {"n": 0}

    def bucket_table(edges: Sequence[float], column: int) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            group = [r for r in pooled if lo <= r[column] < hi]
            if len(group) < 100:
                continue
            rows.append(
                {
                    "lo": lo,
                    "hi": hi,
                    "n": len(group),
                    "fwd_vol": st.median([r[2] for r in group]),
                    "efficiency": st.median([r[3] for r in group]),
                    "abs_ret": st.median([r[4] for r in group]),
                }
            )
        return rows

    return {
        "n": n,
        "horizon": horizon,
        "random_walk_efficiency": RANDOM_WALK_EFFICIENCY.get(horizon),
        "adx_quantiles": {
            f"p{q}": pooled[min(n - 1, int(n * q / 100))][0]
            for q in (1, 5, 10, 25, 50, 75, 90, 95, 99)
        },
        "adx_shares": {
            f"gt{t:g}": sum(1 for r in pooled if r[0] > t) / n for t in (20, 25, 30, 35)
        },
        "adx_buckets": bucket_table(ADX_EDGES, 0),
        "vol_buckets": bucket_table(VOL_EDGES, 1),
    }


# ----------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------


def print_discrimination(result: Dict[str, Any], baselines: Dict[str, Any]) -> None:
    """Print the Task 1 report.

    Args:
        result: Output of run_discrimination.
        baselines: Output of run_baselines.
    """
    horizon = result["horizon"]
    rw = result.get("random_walk_efficiency")
    print("=" * 78)
    print(
        f"TASK 1 - does the regime label discriminate? "
        f"(forward {horizon} x 4h = {horizon * 4}h)"
    )
    print("=" * 78)
    print(
        f"Rotation null: {result['iterations']} circular rotations "
        f"(p floor = {1 / (result['iterations'] + 1):.4f})."
    )
    if rw:
        print(f"Random-walk median efficiency at this horizon: {rw:.3f}.")

    p_values: List[Tuple[float, str]] = []
    for symbol, outcomes in result["primary"].items():
        print(f"\n--- {symbol} ---")
        for name, stats in outcomes.items():
            print(
                f"\n  {name}  n={stats['n']}  "
                f"eps^2={stats['epsilon_squared']:.4f}  "
                f"rotation-p={stats['rotation_p']:.4f}  "
                f"uncond median={stats['unconditional_median']:.5f}"
            )
            p_values.append((stats["rotation_p"], f"{symbol}/{name}"))
            for regime in REGIME_ORDER:
                cell = stats["by_regime"].get(regime)
                if not cell:
                    continue
                print(
                    f"    {regime:<20} n={cell['n']:>6}  "
                    f"median={cell['median']:>10.5f}  "
                    f"x{cell['ratio_to_unconditional']:>5.2f}  "
                    f"cliff_d={cell['cliffs_delta']:>+6.3f}"
                )

    print(
        "\n\nEfficiency effect by horizon "
        "(positive delta = MORE directional than the rest):"
    )
    header = (
        f"  {'symbol':<10}{'H':>4}{'eps^2':>9}{'rot-p':>8}"
        f"{'d(trend_str)':>14}{'d(rang_calm)':>14}"
        f"{'med(trend_str)':>16}{'rand walk':>11}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for symbol, sweep in result["horizon_sweep"].items():
        for h, stats in sorted(sweep.items()):
            print(
                f"  {symbol:<10}{h:>4}{stats['epsilon_squared']:>9.4f}"
                f"{stats['rotation_p']:>8.3f}"
                f"{stats['deltas'].get('trending_strong', 0):>+14.3f}"
                f"{stats['deltas'].get('ranging_calm', 0):>+14.3f}"
                f"{stats['medians'].get('trending_strong', 0):>16.3f}"
                f"{stats['random_walk_efficiency'] or 0:>11.3f}"
            )
            p_values.append((stats["rotation_p"], f"{symbol}/eff@H{h}"))

    print("\n\nThe 5-way label versus the continuous signals it discards")
    print("(share of forward rank variance explained; comparable scale):")
    header = (
        f"  {'symbol':<10}{'outcome':<12}{'label eps^2':>12}"
        f"{'ADX rho^2':>11}{'volscore rho^2':>16}{'trailvol rho^2':>16}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for symbol, outcomes in baselines.items():
        for name, stats in outcomes.items():
            print(
                f"  {symbol:<10}{name:<12}"
                f"{stats['label_epsilon_squared']:>12.4f}"
                f"{stats['adx_rho_squared']:>11.4f}"
                f"{stats['vol_score_rho_squared']:>16.4f}"
                f"{stats['trailing_vol_rho_squared']:>16.4f}"
            )

    alpha = 0.05
    n_tests = len(p_values)
    floor = 1 / (result["iterations"] + 1)
    print(f"\n\nMultiple comparisons: {n_tests} rotation tests in this family.")
    tightest = holm_threshold(1, n_tests, alpha)
    if floor > tightest:
        print(
            f"  UNRESOLVABLE: the p-value floor {floor:.5f} "
            f"({result['iterations']} rotations) is above Holm's tightest "
            f"critical value {tightest:.5f}, so no test CAN survive "
            f"regardless of the evidence. Re-run with "
            f"--iterations {int(1 / tightest) + 1} or more."
        )
        return
    p_values.sort()
    surviving = 0
    for i, (p, name) in enumerate(p_values, start=1):
        crit = holm_threshold(i, n_tests, alpha)
        if p <= crit:
            surviving += 1
        else:
            print(
                f"  Holm stops at rank {i}: {name} p={p:.4f} > "
                f"{crit:.4f}; {surviving} of {n_tests} tests survive at "
                f"alpha={alpha}."
            )
            print(
                "  Surviving a rotation null means the label is not pure "
                "noise. It says NOTHING about whether the effect is big "
                "enough to matter - read the effect sizes above for that."
            )
            break
    else:
        print(f"  All {surviving} tests survive Holm at alpha={alpha}.")


def print_taxonomy(result: Dict[str, Any], detail: Dict[str, Any]) -> None:
    """Print the side-by-side taxonomy comparison.

    Args:
        result: Output of run_taxonomy_comparison.
        detail: Output of run_bucket_detail.
    """
    horizon = result["horizon"]
    schemes = result["schemes"]
    floor = 1 / (result["iterations"] + 1)
    print("\n" + "=" * 78)
    print(
        f"TAXONOMY COMPARISON - same bars, same null, same effect size "
        f"(forward {horizon} x 4h = {horizon * 4}h)"
    )
    print("=" * 78)
    print(
        f"Rotation null: {result['iterations']} circular rotations "
        f"(p floor = {floor:.4f})."
    )
    print(
        "Every scheme is scored on the SAME masked sample (bars every "
        "scheme labels).\n"
        "eps^2 and rho^2 are both shares of explained forward RANK "
        "variance, so the\n"
        "last three columns are the bar the taxonomies have to clear, on "
        "the same scale."
    )

    for symbol, per_outcome in result["by_symbol"].items():
        print(f"\n--- {symbol} ---")
        header = (
            f"  {'outcome':<11}{'n':>7}"
            + "".join(f"{s:>13}" for s in schemes)
            + f"{'trailvol':>11}{'ADX':>9}{'volscore':>10}"
        )
        print(header)
        print("  " + "-" * (len(header) - 2))
        for name, entry in per_outcome.items():
            row = f"  {name:<11}{entry['n']:>7}"
            for scheme in schemes:
                cell = entry["by_scheme"].get(scheme)
                row += f"{cell['epsilon_squared']:>13.4f}" if cell else f"{'-':>13}"
            row += (
                f"{entry['trailing_vol_rho_squared']:>11.4f}"
                f"{entry['adx_rho_squared']:>9.4f}"
                f"{entry['vol_score_rho_squared']:>10.4f}"
            )
            print(row)
        print("  rotation-p:")
        for name, entry in per_outcome.items():
            cells = "  ".join(
                f"{s}={entry['by_scheme'][s]['rotation_p']:.4f}"
                for s in schemes
                if s in entry["by_scheme"]
            )
            print(f"    {name:<11}{cells}")

    print("\n\nShare of the common sample per label (non-degeneracy check):")
    for symbol, shares in result["shares"].items():
        print(f"  {symbol}")
        for scheme in schemes:
            cells = shares.get(scheme, {})
            joined = ", ".join(f"{k} {v:.1f}%" for k, v in sorted(cells.items()))
            print(f"    {scheme:<13}{joined}")

    print(f"\n\nPer-bucket detail for {detail['scheme']}:")
    rw = RANDOM_WALK_EFFICIENCY.get(detail["horizon"])
    if rw:
        print(f"  (random-walk median efficiency at this horizon: {rw:.3f})")
    for symbol, per_outcome in detail["by_symbol"].items():
        print(f"\n  --- {symbol} ---")
        for name, cells in per_outcome.items():
            print(f"    {name}")
            for label, cell in cells.items():
                print(
                    f"      {label:<22} n={cell['n']:>6}  "
                    f"share={cell['share']:>5.1f}%  "
                    f"median={cell['median']:>10.5f}  "
                    f"x{cell['ratio_to_unconditional']:>5.2f}  "
                    f"cliff_d={cell['cliffs_delta']:>+6.3f}"
                )


def print_decomposition(result: Dict[str, Any]) -> None:
    """Print the Task 2 mechanism-decomposition table.

    Args:
        result: Output of run_decomposition.
    """
    print("\n" + "=" * 78)
    print("TASK 2 - what makes trending_strong 60% of the tape?")
    print("=" * 78)
    header = f"{'configuration':<40}" + "".join(f"{r[:9]:>11}" for r in REGIME_ORDER)
    print(header)
    print("-" * len(header))
    for label, shares in result["cases"].items():
        print(
            f"{label:<40}"
            + "".join(f"{shares.get(r, 0.0):>10.1f}%" for r in REGIME_ORDER)
        )


def print_knee(result: Dict[str, Any]) -> None:
    """Print the Task 3 threshold-structure scan.

    Args:
        result: Output of run_knee.
    """
    print("\n" + "=" * 78)
    print("TASK 3 criterion - is there a knee to put a threshold on?")
    print("=" * 78)
    if not result.get("n"):
        print("  no data")
        return
    rw = result.get("random_walk_efficiency")
    print(f"Pooled 4h bars: {result['n']}")
    print("ADX(14) distribution over the engine's rolling 60-bar window:")
    print("  " + "  ".join(f"{k}={v:.1f}" for k, v in result["adx_quantiles"].items()))
    print(
        "  "
        + "  ".join(
            f"share {k.replace('gt', 'ADX>')}: {v:.1%}"
            for k, v in result["adx_shares"].items()
        )
    )
    if rw:
        print(f"Random-walk median efficiency at this horizon: {rw:.3f}")

    for title, key, col in (
        ("Forward behaviour by ADX bucket", "adx_buckets", "ADX"),
        (
            "Forward behaviour by VOLATILITY SCORE bucket",
            "vol_buckets",
            "volscore",
        ),
    ):
        print(f"\n{title} (medians, normalised so unconditional = 1.00):")
        header = f"  {col:<14}{'n':>7}{'fwd_vol':>10}" f"{'efficiency':>12}{'|ret|':>9}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for row in result[key]:
            hi = "+" if row["hi"] > 1e8 else f"{row['hi']:g}"
            span = f"{row['lo']:g}-{hi}"
            print(
                f"  {span:<14}{row['n']:>7}"
                f"{row['fwd_vol']:>10.3f}{row['efficiency']:>12.3f}"
                f"{row['abs_ret']:>9.3f}"
            )


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _resolve_windows(
    symbols: Sequence[str], data_dir: str
) -> Dict[str, List[Tuple[str, str]]]:
    """Resolve the census/campaign window series for the decomposition.

    Uses the same helper the validation runner and the regime census use,
    so the shares are directly comparable with docs/REGIME-CENSUS.md.

    Args:
        symbols: Trading pairs.
        data_dir: Candle store directory.

    Returns:
        Symbol -> list of (start, end) ISO date pairs.
    """
    from ..validation.runner import (
        DEFAULT_SPAN_YEARS,
        DEFAULT_WINDOW_MODE,
        DEFAULT_WINDOWS,
        DEFAULT_WINDOW_MONTHS,
        resolve_chunk_windows,
    )

    resolved = resolve_chunk_windows(
        list(symbols),
        DEFAULT_WINDOW_MONTHS,
        DEFAULT_WINDOWS,
        data_dir=data_dir,
        label="regime-discrimination",
        mode=DEFAULT_WINDOW_MODE,
        span_years=DEFAULT_SPAN_YEARS,
        per_symbol=True,
    )
    if resolved.get("reason"):
        raise SystemExit(f"Cannot resolve windows: {resolved['reason']}")
    shared = resolved["windows"]
    by_symbol = resolved.get("windows_by_symbol") or {}
    return {
        s: [tuple(w) for w in (by_symbol.get(s) or shared)] for s in resolved["symbols"]
    }


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Regime-label discrimination, mechanism decomposition and "
            "threshold-structure scan (4h candles)"
        )
    )
    parser.add_argument(
        "--mode",
        default="all",
        choices=("all", "discriminate", "decompose", "knee", "taxonomy"),
    )
    parser.add_argument("--symbols", default="BTC-USDC,ETH-USDC,SUI-USDC")
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--end", default="2030-01-01")
    parser.add_argument(
        "--data-dir",
        default=os.getenv("BACKTEST_DATA_DIR", "trading_bot_v2/backtesting/data"),
        help=(
            "Candle store. Pass an ABSOLUTE path from a git worktree: "
            "BACKTEST_DATA_DIR is relative and .env wins over the shell."
        ),
    )
    parser.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=20260728)
    parser.add_argument("--json", default=None, help="Write results to path")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.quiet:
        logger.remove()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    features = {
        symbol: precompute_features(symbol, args.start, args.end, args.data_dir)
        for symbol in symbols
    }
    params = RegimeParams.from_detector(MarketRegimeDetector())
    payload: Dict[str, Any] = {"params": params.__dict__}

    if args.mode in ("all", "discriminate"):
        discrimination = run_discrimination(
            features, params, args.horizon, args.iterations, args.seed
        )
        baselines = run_baselines(features, params, args.horizon)
        print_discrimination(discrimination, baselines)
        payload["discrimination"] = discrimination
        payload["baselines"] = baselines

    if args.mode in ("all", "taxonomy"):
        comparison = run_taxonomy_comparison(
            features, params, args.horizon, args.iterations, args.seed
        )
        detail = run_bucket_detail(features, params, args.horizon)
        print_taxonomy(comparison, detail)
        payload["taxonomy"] = comparison
        payload["bucket_detail"] = detail

    if args.mode in ("all", "decompose"):
        windows = _resolve_windows(symbols, args.data_dir)
        decomposition = run_decomposition(features, windows, params)
        decomposition["windows"] = {k: [list(w) for w in v] for k, v in windows.items()}
        print_decomposition(decomposition)
        payload["decomposition"] = decomposition

    if args.mode in ("all", "knee"):
        knee = run_knee(features, args.horizon)
        print_knee(knee)
        payload["knee"] = knee

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, default=str)
        print(f"\nWrote {args.json}")


if __name__ == "__main__":
    main()
