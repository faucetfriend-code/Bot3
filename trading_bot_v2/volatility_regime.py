"""Realized-volatility regime detection (switchable alternative to ADX).

Why this module exists
----------------------
``docs/REGIME-DISCRIMINATION.md`` measured the ADX taxonomy over 54,656
4h bars with a circular-rotation null and found:

* forward direction: eps^2 0.0003-0.0008, rotation-p 0.27-0.92 (nothing);
* forward volatility: eps^2 0.013-0.032 (weak);
* trend persistence: ``trending_strong``'s forward directional efficiency
  sits ON the random-walk line while ``ranging_calm`` sits above it - the
  labels' two poles are INVERTED relative to their names;
* plain 14-bar trailing realized volatility scores rho^2 0.159-0.294 on
  the same forward-volatility measure, roughly 20x the whole taxonomy.

The same document found forward behaviour is smooth and monotone in both
ADX and the detector's volatility score with **no knee anywhere**, so any
fixed cut point is an arbitrary slice of a continuum. That is the
argument for **quantile buckets of trailing realized volatility** rather
than hardcoded levels: the boundaries come from the data's own
distribution, every bucket is non-degenerate by construction, and the
scheme re-centres itself as market epochs change.

What this detector claims, and what it does not
-----------------------------------------------
It claims to separate **how violent** the next day is likely to be. It
does NOT claim to say which way price will go: the discrimination study
found nothing predicts forward direction, and realized volatility is
sign-blind by construction. The regime names say ``VOL_LOW`` /
``VOL_MID`` / ``VOL_HIGH`` precisely so they cannot be misread as a
directional green light, which is how ``trending_strong`` was being used.

No lookahead
------------
Every quantile boundary is computed from a TRAILING reference window of
past observations only. The classifier is online: it observes one value
per 4h bar as the replay advances and ranks that value against what it
has already seen. A future volatility explosion cannot change a past
label, and ``tests/test_volatility_regime.py::TestNoLookahead`` pins that
by replaying a series twice - once truncated, once with a 10x volatility
explosion appended - and asserting every shared label is identical.

Default OFF
-----------
``REGIME_MODE`` defaults to ``adx``. Nothing changes until it is set to
``volatility``, which is what makes the two comparable.
"""

import math
import os
import statistics as st
from bisect import bisect_left, bisect_right, insort
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Deque, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from .market_regime import MarketRegime, MarketRegimeDetector, _env_float

#: Bars of 4h log returns in the trailing realized-volatility estimate.
#: 14 is not tuned - it is the exact window whose rho^2 (0.159-0.294)
#: docs/REGIME-DISCRIMINATION.md reports as the bar this scheme has to
#: clear. Changing it would change the bar as well as the scheme.
DEFAULT_VOL_WINDOW = 14

#: Buckets the SHIPPED detector uses. Fixed at 3 because the enum has
#: three named members and regime values are persisted: a stored
#: ``vol_high`` must mean the same thing in every record that carries it.
#: 2/3/4/5 were all measured (docs/REGIME-VOLATILITY.md); the classifier
#: below is k-generic so the analysis harness can sweep them.
SHIPPED_BUCKETS = 3

#: Calendar span of the trailing reference distribution, in days.
#: 120 days is ~720 4h bars: long enough for stable terciles, short
#: enough to re-centre within a market epoch.
DEFAULT_REFERENCE_DAYS = 120.0

#: Minimum trailing observations before a bar can be ranked at all.
#: Below this the detector returns VOL_WARMUP rather than guessing.
DEFAULT_MIN_OBSERVATIONS = 40

#: Hard cap on retained observations (memory guard for long replays).
DEFAULT_MAX_OBSERVATIONS = 4000

#: Spacing assumed between consecutive regime bars, in hours. Regime
#: detection runs on 4h candles throughout this codebase.
REGIME_BAR_HOURS = 4.0

#: Bucket index -> regime, for the shipped 3-bucket scheme.
BUCKET_REGIMES: Tuple[MarketRegime, ...] = (
    MarketRegime.VOL_LOW,
    MarketRegime.VOL_MID,
    MarketRegime.VOL_HIGH,
)

#: The regimes this taxonomy can emit, in report order.
VOLATILITY_REGIMES: Tuple[MarketRegime, ...] = BUCKET_REGIMES + (
    MarketRegime.VOL_WARMUP,
)


def is_volatility_regime(regime: Any) -> bool:
    """Report whether a regime value belongs to the volatility taxonomy.

    Args:
        regime: MarketRegime or its value string.

    Returns:
        True when the value is one of the VOL_* values.
    """
    value = str(getattr(regime, "value", regime))
    return value in {r.value for r in VOLATILITY_REGIMES}


def trailing_realized_vol(
    closes: Sequence[float], window: int = DEFAULT_VOL_WINDOW
) -> Optional[float]:
    """Population stdev of the last ``window`` 4h log returns.

    This is the quantity ``docs/REGIME-DISCRIMINATION.md`` calls
    "trailing-vol" and measures at rho^2 0.159-0.294 against forward
    realized volatility - the trivial baseline any bucketed scheme has to
    be compared against.

    Args:
        closes: Close prices, oldest first.
        window: Number of log returns in the estimate.

    Returns:
        The realized volatility, or None when there is not enough
        history or a non-positive price makes the log return undefined.
    """
    if window < 2 or len(closes) < window + 1:
        return None
    segment = closes[-(window + 1) :]
    returns: List[float] = []
    for i in range(1, len(segment)):
        prev, cur = float(segment[i - 1]), float(segment[i])
        if prev <= 0.0 or cur <= 0.0:
            return None
        returns.append(math.log(cur / prev))
    return st.pstdev(returns)


def trailing_efficiency(
    closes: Sequence[float], window: int = DEFAULT_VOL_WINDOW
) -> Optional[float]:
    """Trailing directional efficiency: net move over path length.

    ``|close[-1] - close[-1-window]| / sum(|bar moves|)``. Scale-free, on
    [0, 1]. This is the second candidate axis evaluated in
    ``docs/REGIME-VOLATILITY.md`` - a more direct measure of "is this
    trending" than ADX. It is computed here so the detector can report it
    for observability; the shipped taxonomy does NOT label on it (see
    that document for the measurement that decided so).

    Args:
        closes: Close prices, oldest first.
        window: Number of bars in the path.

    Returns:
        Efficiency in [0, 1], or None when there is not enough history.
        0.0 when the path length is zero (a flat series).
    """
    if window < 1 or len(closes) < window + 1:
        return None
    segment = [float(c) for c in closes[-(window + 1) :]]
    gross = sum(abs(segment[i] - segment[i - 1]) for i in range(1, len(segment)))
    if gross <= 0.0:
        return 0.0
    return abs(segment[-1] - segment[0]) / gross


@dataclass(frozen=True)
class BucketResult:
    """One classified observation.

    Attributes:
        index: 0-based bucket index, 0 = lowest.
        buckets: Number of buckets the index is drawn from.
        rank: Mid-rank fraction of the value within the trailing
            reference window, on [0, 1].
        value: The raw observation that was ranked.
        reference_n: Size of the trailing reference window used.
    """

    index: int
    buckets: int
    rank: float
    value: float
    reference_n: int


class TrailingQuantileBucketer:
    """Rank an observation against a TRAILING window of past observations.

    The whole point of this class is the word "trailing". Quantile
    boundaries derived from a full series are the single easiest way to
    manufacture a spectacular and completely fake backtest, because a bar
    is then labelled using volatility that had not happened yet. Here the
    reference window only ever contains observations already fed in, so a
    label, once emitted, can never change.

    Consecutive identical values are deduplicated. Regime detection runs
    on a 1h cadence off 4h candles, so the same closed 4h bar is observed
    four times in a row with a bit-identical trailing volatility; keeping
    all four would weight the recent past 4x against the seeded history
    without changing what the distribution means.
    """

    def __init__(
        self,
        buckets: int = SHIPPED_BUCKETS,
        reference_days: float = DEFAULT_REFERENCE_DAYS,
        min_observations: int = DEFAULT_MIN_OBSERVATIONS,
        max_observations: int = DEFAULT_MAX_OBSERVATIONS,
    ) -> None:
        """Initialize the bucketer.

        Args:
            buckets: Number of quantile buckets (>= 2).
            reference_days: Calendar span of the trailing window.
            min_observations: Observations required before ranking.
            max_observations: Hard cap on retained observations.

        Raises:
            ValueError: When buckets < 2 or the window settings are
                non-positive.
        """
        if buckets < 2:
            raise ValueError(f"buckets must be at least 2, got {buckets}")
        if reference_days <= 0:
            raise ValueError(f"reference_days must be positive, got {reference_days}")
        if min_observations < buckets:
            raise ValueError(
                f"min_observations ({min_observations}) must be at least "
                f"buckets ({buckets})"
            )
        self.buckets = buckets
        self.reference_days = reference_days
        self.min_observations = min_observations
        self.max_observations = max(max_observations, min_observations)
        self._obs: Deque[Tuple[datetime, float]] = deque()
        #: The same values, kept sorted, so a rank is a bisect rather
        #: than a linear scan. Insertion and deletion are C-level list
        #: shifts; the rank query is O(log n) instead of O(n).
        self._sorted: List[float] = []

    def __len__(self) -> int:
        """Return the number of retained trailing observations."""
        return len(self._obs)

    def reset(self) -> None:
        """Drop all retained observations."""
        self._obs.clear()
        self._sorted.clear()

    def seed(
        self,
        when: datetime,
        values: Sequence[float],
        spacing_hours: float = REGIME_BAR_HOURS,
    ) -> None:
        """Backfill the reference window from already-observed history.

        The engine hands the detector a rolling window of past candles on
        every call, so a cold start does not have to wait days to acquire
        a reference distribution - the history is right there and it is
        entirely in the past. Seed timestamps are laid out backwards from
        ``when`` at the regime bar spacing.

        Args:
            when: Timestamp of the bar that follows the seeded values.
            values: Past observations, oldest first.
            spacing_hours: Assumed spacing between them.
        """
        n = len(values)
        for i, value in enumerate(values):
            stamp = when - timedelta(hours=spacing_hours * (n - i))
            self._append(stamp, float(value))

    def _append(self, when: datetime, value: float) -> None:
        """Append one observation, deduplicating repeats of the last."""
        if self._obs and self._obs[-1][1] == value:
            return
        self._obs.append((when, value))
        insort(self._sorted, value)

    def _drop_oldest(self) -> None:
        """Remove the oldest observation from both views."""
        _, value = self._obs.popleft()
        index = bisect_left(self._sorted, value)
        del self._sorted[index]

    def _evict(self, when: datetime) -> None:
        """Drop observations outside the trailing window."""
        cutoff = when - timedelta(days=self.reference_days)
        while self._obs and self._obs[0][0] < cutoff:
            self._drop_oldest()
        while len(self._obs) > self.max_observations:
            self._drop_oldest()

    def observe(self, when: datetime, value: float) -> Optional[BucketResult]:
        """Record an observation and rank it against its trailing window.

        The observation itself is included in the reference set - that is
        still strictly past data, and it makes the very first rankable
        bar well defined.

        Args:
            when: Observation timestamp.
            value: The observation (trailing realized volatility).

        Returns:
            The bucket assignment, or None while the trailing window
            holds fewer than ``min_observations`` values.
        """
        self._append(when, float(value))
        self._evict(when)
        n = len(self._obs)
        if n < self.min_observations:
            return None
        below = bisect_left(self._sorted, value)
        equal = bisect_right(self._sorted, value) - below
        rank = (below + 0.5 * equal) / n
        index = min(self.buckets - 1, int(rank * self.buckets))
        return BucketResult(
            index=index,
            buckets=self.buckets,
            rank=rank,
            value=float(value),
            reference_n=n,
        )


class VolatilityRegimeClassifier:
    """Per-symbol online quantile classifier of trailing realized vol.

    Generic in the number of buckets so the analysis harness can sweep
    k = 2..5 over the exact code path the detector runs, and generic in
    the trailing metric so the same trailing-only machinery can rank a
    second axis (directional efficiency) without a second
    implementation. The shipped detector wraps this with
    ``k = SHIPPED_BUCKETS`` and the realized-volatility metric.
    """

    def __init__(
        self,
        buckets: int = SHIPPED_BUCKETS,
        vol_window: int = DEFAULT_VOL_WINDOW,
        reference_days: float = DEFAULT_REFERENCE_DAYS,
        min_observations: int = DEFAULT_MIN_OBSERVATIONS,
        max_observations: int = DEFAULT_MAX_OBSERVATIONS,
        seed_from_history: bool = True,
        metric: Callable[
            [Sequence[float], int], Optional[float]
        ] = trailing_realized_vol,
    ) -> None:
        """Initialize the classifier.

        Args:
            buckets: Number of quantile buckets.
            vol_window: Bars of log returns in the volatility estimate.
            reference_days: Calendar span of the trailing reference.
            min_observations: Observations required before ranking.
            max_observations: Hard cap on retained observations.
            seed_from_history: Backfill the reference window from the
                candle history on a symbol's first classification.
            metric: ``(closes, window) -> value or None``. Must be
                computable from trailing closes ONLY.
        """
        self.buckets = buckets
        self.metric = metric
        self.vol_window = vol_window
        self.reference_days = reference_days
        self.min_observations = min_observations
        self.max_observations = max_observations
        self.seed_from_history = seed_from_history
        self._bucketers: Dict[str, TrailingQuantileBucketer] = {}
        self._seeded: Dict[str, bool] = {}

    def reset(self, symbol: Optional[str] = None) -> None:
        """Drop accumulated state.

        Args:
            symbol: Symbol to reset, or None for all symbols.
        """
        if symbol is None:
            self._bucketers.clear()
            self._seeded.clear()
            return
        self._bucketers.pop(symbol, None)
        self._seeded.pop(symbol, None)

    def _bucketer(self, symbol: str) -> TrailingQuantileBucketer:
        """Return (creating if needed) the bucketer for one symbol."""
        bucketer = self._bucketers.get(symbol)
        if bucketer is None:
            bucketer = TrailingQuantileBucketer(
                buckets=self.buckets,
                reference_days=self.reference_days,
                min_observations=self.min_observations,
                max_observations=self.max_observations,
            )
            self._bucketers[symbol] = bucketer
        return bucketer

    def _seed(
        self, symbol: str, when: datetime, closes: Sequence[float]
    ) -> None:
        """Seed one symbol's reference window from past candles.

        Every seeded value is the trailing volatility as of a bar STRICTLY
        BEFORE the current one, so seeding cannot import future
        information. The current bar's own value is added by
        ``classify``.

        Args:
            symbol: Trading symbol.
            when: Timestamp of the current bar.
            closes: Close prices handed in by the caller, oldest first.
        """
        if self._seeded.get(symbol) or not self.seed_from_history:
            self._seeded[symbol] = True
            return
        self._seeded[symbol] = True
        bucketer = self._bucketer(symbol)
        history: List[float] = []
        # j is the index of the last close in each historical window;
        # len(closes) - 1 is the CURRENT bar and is deliberately excluded.
        for j in range(self.vol_window, len(closes) - 1):
            value = self.metric(closes[: j + 1], self.vol_window)
            if value is not None:
                history.append(value)
        if history:
            bucketer.seed(when, history)

    def classify(
        self, symbol: str, when: datetime, closes: Sequence[float]
    ) -> Optional[BucketResult]:
        """Classify one bar for one symbol.

        Args:
            symbol: Trading symbol (state is kept per symbol).
            when: Bar timestamp. Must be non-decreasing across calls.
            closes: Close prices up to and including this bar.

        Returns:
            The bucket assignment, or None during warmup (not enough
            trailing history, or the volatility itself is undefined).
        """
        value = self.metric(closes, self.vol_window)
        if value is None:
            return None
        self._seed(symbol, when, closes)
        return self._bucketer(symbol).observe(when, value)


# ----------------------------------------------------------------------
# Strategy mapping (proposed, configurable, and NOT wired on by default)
# ----------------------------------------------------------------------

#: Proposed regime -> strategy mapping, grounded in what each strategy
#: needs MECHANICALLY rather than in what the old regime was called.
#: See docs/REGIME-VOLATILITY.md for the full argument, including why
#: the current ADX mapping looks inverted against the measurement.
DEFAULT_VOL_STRATEGIES: Dict[MarketRegime, Tuple[str, ...]] = {
    # Tight bands, small ATR: prices revert inside a narrow envelope and
    # an ATR-scaled stop is cheap. Grid is excluded here - its levels are
    # ATR-spaced, and at the bottom tercile of realized volatility the
    # spacing struggles to clear a round trip in fees.
    MarketRegime.VOL_LOW: ("MeanReversion",),
    # Enough amplitude for an oscillation to pay for itself, not so much
    # that price walks through the whole grid. VWAP deviations are large
    # relative to costs and still mean-revert.
    MarketRegime.VOL_MID: ("GridTrading", "VWAPScalping"),
    # Expansion, gaps and cascades. Reversion strategies get stopped out
    # and a grid gets run over; persistence-seeking strategies are the
    # ones whose payoff shape matches.
    MarketRegime.VOL_HIGH: ("MomentumScalping", "MACrossover"),
    # No reference distribution yet: stay flat rather than guess.
    MarketRegime.VOL_WARMUP: (),
}

#: Static allocation weights per regime, same shape as the ADX detector's
#: table. Overlay strategies keep the weights they have there.
DEFAULT_VOL_WEIGHTS: Dict[MarketRegime, Dict[str, float]] = {
    MarketRegime.VOL_LOW: {
        "MeanReversion": 0.6,
        "OrderBookImbalance": 0.2,
        "SessionRangeBreakout": 0.15,
        "CalendarFlow": 0.1,
        "VWAPPullback": 0.15,
    },
    MarketRegime.VOL_MID: {
        "GridTrading": 0.5,
        "VWAPScalping": 0.3,
        "OrderBookImbalance": 0.2,
        "SessionRangeBreakout": 0.15,
        "CalendarFlow": 0.1,
        "VWAPPullback": 0.15,
    },
    MarketRegime.VOL_HIGH: {
        "MomentumScalping": 0.4,
        "MACrossover": 0.3,
        "LiquidationCapture": 0.3,
        "OrderBookImbalance": 0.2,
        "SessionRangeBreakout": 0.15,
        "CalendarFlow": 0.1,
        "VWAPPullback": 0.25,
    },
    MarketRegime.VOL_WARMUP: {},
}

#: Env var suffix per regime, e.g. REGIME_VOL_STRATEGIES_LOW.
_REGIME_ENV_SUFFIX: Dict[MarketRegime, str] = {
    MarketRegime.VOL_LOW: "LOW",
    MarketRegime.VOL_MID: "MID",
    MarketRegime.VOL_HIGH: "HIGH",
    MarketRegime.VOL_WARMUP: "WARMUP",
}


def _parse_strategy_list(raw: str) -> Tuple[str, ...]:
    """Parse a comma-separated strategy display-name list.

    Args:
        raw: e.g. ``"GridTrading,VWAPScalping"``. Empty means "no
            strategies", which is a meaningful setting (stay flat).

    Returns:
        Tuple of display names.
    """
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _parse_weight_map(raw: str) -> Dict[str, float]:
    """Parse a ``Name:weight`` comma-separated weight map.

    Args:
        raw: e.g. ``"GridTrading:0.5,VWAPScalping:0.3"``.

    Returns:
        Display name -> weight. Malformed entries are skipped with a
        warning rather than taking the process down.
    """
    weights: Dict[str, float] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, value = part.partition(":")
        try:
            weights[name.strip()] = float(value)
        except ValueError:
            logger.warning(
                f"Ignoring malformed regime weight entry {part!r} "
                f"(expected Name:weight)"
            )
    return weights


class VolatilityRegimeDetector(MarketRegimeDetector):
    """Regime detection by trailing-quantile realized volatility.

    Subclasses ``MarketRegimeDetector`` so the caching, 2-count
    confirmation, minimum-dwell suppression, REGIME_CHANGED publication
    and regime_history persistence are shared rather than reimplemented -
    only the classification itself changes. That also means the anti-flap
    machinery measured in ``docs/REGIME-DISCRIMINATION.md`` (dwell and
    confirmation contribute ~0 to regime shares; the ADX exit band
    contributes 5.8pp) applies unchanged.

    There is deliberately NO extra hysteresis band on the rank fraction.
    The inherited 2-count confirmation and minimum dwell already suppress
    flicker, and adding a third mechanism would put a knob into the
    shipped scheme that the discrimination measurement does not isolate.
    """

    def __init__(
        self,
        vol_window: Optional[int] = None,
        reference_days: Optional[float] = None,
        min_observations: Optional[int] = None,
        compute_adx: bool = True,
        **kwargs: Any,
    ) -> None:
        """Initialize the detector.

        Args:
            vol_window: Bars of 4h log returns in the trailing volatility
                estimate (env REGIME_VOL_WINDOW, default 14).
            reference_days: Calendar span of the trailing reference
                distribution (env REGIME_VOL_REFERENCE_DAYS, default 120).
            min_observations: Trailing observations required before a bar
                can be ranked (env REGIME_VOL_MIN_OBSERVATIONS, default
                40).
            compute_adx: Keep computing ADX for observability and for
                downstream callers that read ``get_last_adx`` (grid
                spacing). It plays no part in classification.
            **kwargs: Forwarded to ``MarketRegimeDetector`` (event_bus,
                db, periods, and the ADX thresholds, which are unused
                here).
        """
        super().__init__(**kwargs)
        self.vol_window = (
            vol_window
            if vol_window is not None
            else int(_env_float("REGIME_VOL_WINDOW", float(DEFAULT_VOL_WINDOW)))
        )
        self.reference_days = (
            reference_days
            if reference_days is not None
            else _env_float("REGIME_VOL_REFERENCE_DAYS", DEFAULT_REFERENCE_DAYS)
        )
        self.min_observations = (
            min_observations
            if min_observations is not None
            else int(
                _env_float(
                    "REGIME_VOL_MIN_OBSERVATIONS", float(DEFAULT_MIN_OBSERVATIONS)
                )
            )
        )
        self.compute_adx = compute_adx
        self.classifier = VolatilityRegimeClassifier(
            buckets=SHIPPED_BUCKETS,
            vol_window=self.vol_window,
            reference_days=self.reference_days,
            min_observations=self.min_observations,
        )
        #: Symbol whose classification is in flight. detect_regime()
        #: inherits a symbol-less signature from the base class, so the
        #: cached path stamps it here before delegating.
        self._active_symbol = "_default"
        #: Last trailing directional efficiency, for observability only.
        self._last_efficiency: Optional[float] = None
        self._strategy_map = self._load_strategy_map()
        self._weight_map = self._load_weight_map()
        logger.info(
            f"VolatilityRegimeDetector initialized: "
            f"{SHIPPED_BUCKETS} quantile buckets of {self.vol_window}-bar "
            f"trailing realized volatility, trailing reference "
            f"{self.reference_days:g}d, warmup {self.min_observations} obs. "
            f"This taxonomy predicts VOLATILITY, not direction - see "
            f"docs/REGIME-VOLATILITY.md."
        )

    # -- configuration -------------------------------------------------

    @staticmethod
    def _load_strategy_map() -> Dict[MarketRegime, Tuple[str, ...]]:
        """Read the regime -> strategy mapping, env overriding defaults.

        Returns:
            Regime -> tuple of strategy display names.
        """
        mapping: Dict[MarketRegime, Tuple[str, ...]] = {}
        for regime, suffix in _REGIME_ENV_SUFFIX.items():
            raw = os.getenv(f"REGIME_VOL_STRATEGIES_{suffix}")
            if raw is None:
                mapping[regime] = DEFAULT_VOL_STRATEGIES[regime]
                continue
            mapping[regime] = _parse_strategy_list(raw)
            logger.warning(
                f"REGIME_VOL_STRATEGIES_{suffix}={raw!r} overrides the "
                f"proposed mapping {DEFAULT_VOL_STRATEGIES[regime]}. This "
                f"changes which strategies may trade and invalidates prior "
                f"backtest numbers."
            )
        return mapping

    @staticmethod
    def _load_weight_map() -> Dict[MarketRegime, Dict[str, float]]:
        """Read the regime -> strategy weight table, env overriding defaults.

        Returns:
            Regime -> {strategy display name: weight}.
        """
        mapping: Dict[MarketRegime, Dict[str, float]] = {}
        for regime, suffix in _REGIME_ENV_SUFFIX.items():
            raw = os.getenv(f"REGIME_VOL_WEIGHTS_{suffix}")
            if raw is None:
                mapping[regime] = dict(DEFAULT_VOL_WEIGHTS[regime])
                continue
            mapping[regime] = _parse_weight_map(raw)
        return mapping

    # -- classification ------------------------------------------------

    def detect_regime(
        self,
        market_data: Dict[str, List[float]],
        previous_regime: Optional[MarketRegime] = None,
    ) -> MarketRegime:
        """Classify the current bar by trailing-volatility quantile.

        Args:
            market_data: OHLCV dict; only ``close`` is used for
                classification. ``high``/``low``, when present, are used
                to keep ``get_last_adx`` populated for downstream
                callers.
            previous_regime: Accepted for interface compatibility and
                deliberately ignored - this taxonomy has no hysteresis
                band of its own (see the class docstring).

        Returns:
            One of VOL_LOW / VOL_MID / VOL_HIGH, or VOL_WARMUP while the
            trailing reference window is too short to rank against.

        Raises:
            ValueError: When ``market_data`` has no ``close`` series.
        """
        if "close" not in market_data:
            raise ValueError("market_data missing required key: 'close'")
        closes = market_data["close"]

        self._last_volatility_score = None
        self._last_efficiency = trailing_efficiency(closes, self.vol_window)
        if self.compute_adx:
            self._refresh_adx(market_data)

        result = self.classifier.classify(
            self._active_symbol, self._clock(), closes
        )
        if result is None:
            logger.debug(
                f"Regime: VOL_WARMUP for {self._active_symbol} "
                f"(trailing reference window not yet {self.min_observations} "
                f"observations)"
            )
            return MarketRegime.VOL_WARMUP

        # Report the rank as a 0-100 percentile so it lands in the same
        # field, and means the same kind of thing, as the ADX detector's
        # volatility score.
        self._last_volatility_score = result.rank * 100.0
        regime = BUCKET_REGIMES[result.index]
        logger.info(
            f"Regime: {regime.name} (trailing {self.vol_window}-bar vol "
            f"{result.value:.5f} at percentile {result.rank * 100:.1f} of "
            f"{result.reference_n} trailing observations)"
        )
        return regime

    def _refresh_adx(self, market_data: Dict[str, List[float]]) -> None:
        """Recompute ADX for observability only (never for classification).

        Args:
            market_data: OHLCV dict.
        """
        highs = market_data.get("high")
        lows = market_data.get("low")
        closes = market_data.get("close")
        if not highs or not lows or not closes:
            return
        if len(closes) < self.adx_period * 2 + 1:
            return
        try:
            from .indicators import calculate_adx

            self._last_calculated_adx = calculate_adx(
                highs, lows, closes, period=self.adx_period
            )
        except Exception as exc:  # noqa: BLE001 - observability only
            logger.debug(f"ADX unavailable for observability: {exc}")

    def _detect_regime_with_confirmation(
        self, symbol: str, market_data: Dict[str, List[float]]
    ) -> MarketRegime:
        """Stamp the active symbol, then run the inherited state machine.

        Args:
            symbol: Trading symbol.
            market_data: OHLCV dict.

        Returns:
            The confirmed regime.
        """
        self._active_symbol = symbol
        return super()._detect_regime_with_confirmation(symbol, market_data)

    def clear_regime_cache(self, symbol: Optional[str] = None) -> None:
        """Clear regime caches AND the trailing reference distribution.

        Args:
            symbol: Symbol to clear, or None for all symbols.
        """
        super().clear_regime_cache(symbol)
        self.classifier.reset(symbol)

    # -- mapping -------------------------------------------------------

    def get_active_strategies(self, regime: MarketRegime) -> List[str]:
        """Map a volatility regime to the strategies allowed to trade it.

        Args:
            regime: MarketRegime value.

        Returns:
            Strategy display names. Empty for VOL_WARMUP and for any
            regime outside this taxonomy.
        """
        strategies = list(self._strategy_map.get(regime, ()))
        logger.debug(f"Regime {regime.value} -> Active strategies: {strategies}")
        return strategies

    def get_strategy_weights(self, regime: MarketRegime) -> Dict[str, float]:
        """Return allocation weights for a volatility regime.

        Args:
            regime: MarketRegime value.

        Returns:
            Strategy display name -> weight.
        """
        return dict(self._weight_map.get(regime, {}))

    def is_grid_allowed(self, regime: MarketRegime) -> bool:
        """Report whether grid trading may run in this regime.

        Derived from the strategy mapping rather than a second list, so
        the two can never disagree.

        Args:
            regime: MarketRegime value.

        Returns:
            True when GridTrading is mapped to this regime.
        """
        return "GridTrading" in self._strategy_map.get(regime, ())


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------

#: Accepted REGIME_MODE values.
REGIME_MODES = ("adx", "volatility")


def get_regime_mode() -> str:
    """Read REGIME_MODE, defaulting to the shipped ADX taxonomy.

    Returns:
        "adx" or "volatility". An unrecognised value logs a warning and
        falls back to "adx" - a typo must never silently re-gate every
        strategy on every symbol.
    """
    raw = os.getenv("REGIME_MODE", "adx").strip().lower()
    if raw not in REGIME_MODES:
        logger.warning(
            f"REGIME_MODE={raw!r} is not one of {REGIME_MODES}; "
            f"falling back to 'adx'."
        )
        return "adx"
    return raw


def make_regime_detector(**kwargs: Any) -> MarketRegimeDetector:
    """Construct the regime detector selected by REGIME_MODE.

    ADX remains the default so the two taxonomies can be compared rather
    than swapped. Selecting the volatility detector logs a warning naming
    the blast radius: it re-gates every strategy on every symbol and
    invalidates every stored per-regime number.

    Args:
        **kwargs: Forwarded to the chosen detector's constructor.

    Returns:
        A MarketRegimeDetector or VolatilityRegimeDetector.
    """
    if get_regime_mode() == "volatility":
        logger.warning(
            "REGIME_MODE=volatility: regime detection now uses trailing "
            "realized-volatility terciles instead of ADX. This re-gates "
            "EVERY strategy on EVERY symbol and invalidates the 8-year "
            "campaign, docs/REGIME-CENSUS.md, every stored regime overlay "
            "and every per-regime study. See docs/REGIME-VOLATILITY.md."
        )
        return VolatilityRegimeDetector(**kwargs)
    return MarketRegimeDetector(**kwargs)
