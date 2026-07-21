"""
Market Regime Detection Module

Uses ADX and volatility metrics to classify market conditions into 5 regimes:
- TRENDING_STRONG: ADX > 25 (enter); once in, held until ADX < 22 (exit band)
- TRENDING_MODERATE: 20 < ADX <= 25 with flat/rising ADX slope
- INDECISIVE: 20 < ADX <= 25 with falling ADX slope (trend losing strength)
- RANGING_VOLATILE: ADX <= 20, volatility score > 65 (first classification);
  entering from another confirmed regime requires score > 68, and once in,
  held until score < 60 (asymmetric enter/exit bands)
- RANGING_CALM: ADX <= 20, low volatility

Hysteresis hardening (Jul 2026): classification is a function of the raw
indicators AND the previous confirmed regime. Asymmetric exit bands
(ADX_EXIT_TRENDING, VOL_SCORE_ENTER, VOL_SCORE_EXIT) plus a minimum dwell
time (MIN_REGIME_DWELL_HOURS) suppress regime flapping. Confirmed regime
transitions publish EventType.REGIME_CHANGED and persist to the
regime_history table when an event bus / database are wired in.

When the USE_ML_REGIME feature flag is enabled and a trained GMM model is
available, regime detection uses a Gaussian Mixture Model trained on 6
statistical features as the primary classifier, with ADX as a fallback.
"""

import os
from datetime import datetime
from enum import Enum
from typing import List, Dict, Any, Callable, Optional
from loguru import logger
from .event_system import EventType
from .indicators import (
    calculate_adx,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_sma,
)


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back to default on error."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(f"Invalid float for {name}={raw!r}, using default {default}")
        return default


class MarketRegime(Enum):
    """Market regime classification based on ADX and volatility."""

    TRENDING_STRONG = "trending_strong"  # ADX > 25 (held until ADX < 22)
    TRENDING_MODERATE = "trending_moderate"  # 20 < ADX <= 25, ADX flat/rising
    RANGING_VOLATILE = "ranging_volatile"  # ADX <= 20, vol score > 65 (enter 68 / exit 60 bands)
    RANGING_CALM = "ranging_calm"  # ADX <= 20, low volatility
    INDECISIVE = "indecisive"  # 20 < ADX <= 25 with falling ADX slope


class MarketRegimeDetector:
    """
    Detects current market regime using ADX and volatility metrics.

    Uses multi-factor analysis:
    1. ADX: Determines if market is trending or ranging
    2. Bollinger Band Width: Measures price volatility
    3. ATR Percentile: Measures volatility relative to historical range
    """

    def __init__(
        self,
        adx_trending_threshold: float = 25.0,  # Was 30.0 - catch trends earlier
        adx_ranging_threshold: float = 20.0,  # Was 25.0 - tighter ranging band
        adx_moderate_threshold: float = 20.0,  # Was 25.0 - aligned with ranging
        volatility_high_percentile: float = 65.0,  # Was 75.0 - easier volatile classification
        adx_period: int = 14,
        atr_period: int = 14,
        bb_period: int = 20,
        adx_exit_trending: Optional[float] = None,
        vol_score_enter: Optional[float] = None,
        vol_score_exit: Optional[float] = None,
        min_dwell_hours: Optional[float] = None,
        event_bus: Optional[Any] = None,
        db: Optional[Any] = None,
    ):
        """
        Initialize MarketRegimeDetector.

        Args:
            adx_trending_threshold: ADX above this enters TRENDING_STRONG
                (default: 25).
            adx_ranging_threshold: ADX at/below this = ranging market
                (default: 20).
            adx_moderate_threshold: ADX above this but at/below trending =
                TRENDING_MODERATE or INDECISIVE by ADX slope (default: 20).
            volatility_high_percentile: Volatility score (0-100) above which a
                ranging market is classified RANGING_VOLATILE on first
                classification, i.e. when there is no previous confirmed
                regime (default: 65).
            adx_period: Period for ADX calculation (default: 14).
            atr_period: Period for ATR calculation (default: 14).
            bb_period: Period for Bollinger Bands calculation (default: 20).
            adx_exit_trending: Hysteresis exit band - once in TRENDING_STRONG,
                remain until ADX drops below this (env ADX_EXIT_TRENDING,
                default: 22).
            vol_score_enter: Hysteresis entry band - entering RANGING_VOLATILE
                from another confirmed regime requires vol score above this
                (env VOL_SCORE_ENTER, default: 68).
            vol_score_exit: Hysteresis exit band - once in RANGING_VOLATILE,
                remain until vol score drops below this (env VOL_SCORE_EXIT,
                default: 60).
            min_dwell_hours: After a confirmed regime switch, further switches
                are suppressed for this many hours (env MIN_REGIME_DWELL_HOURS,
                default: 4.0).
            event_bus: Optional EventBus; confirmed regime transitions publish
                EventType.REGIME_CHANGED on it. No-op when None.
            db: Optional DatabaseManager (duck-typed: needs
                save_regime_transition); confirmed transitions are persisted
                to regime_history. No-op when None.
        """
        self.adx_trending = adx_trending_threshold
        self.adx_ranging = adx_ranging_threshold
        self.adx_moderate = adx_moderate_threshold
        self.volatility_percentile = volatility_high_percentile
        self.adx_period = adx_period
        self.atr_period = atr_period
        self.bb_period = bb_period

        # Hysteresis bands + minimum dwell time (env-configurable)
        self.adx_exit_trending = (
            adx_exit_trending
            if adx_exit_trending is not None
            else _env_float("ADX_EXIT_TRENDING", 22.0)
        )
        self.vol_score_enter = (
            vol_score_enter
            if vol_score_enter is not None
            else _env_float("VOL_SCORE_ENTER", 68.0)
        )
        self.vol_score_exit = (
            vol_score_exit
            if vol_score_exit is not None
            else _env_float("VOL_SCORE_EXIT", 60.0)
        )
        self.min_dwell_hours = (
            min_dwell_hours
            if min_dwell_hours is not None
            else _env_float("MIN_REGIME_DWELL_HOURS", 4.0)
        )

        # Optional observability wiring (both no-ops when None)
        self._event_bus = event_bus
        self._db = db

        # Injectable clock for deterministic tests / offline replay.
        # All regime cache / dwell logic uses self._clock() instead of
        # datetime.now() directly.
        self._clock: Callable[[], datetime] = datetime.now

        # Regime caching to prevent unnecessary recalculation
        self._regime_cache: Dict[str, Dict] = {}
        self._pending_regime_changes: Dict[str, Dict] = {}
        self._cache_ttl_hours = 1  # Recalculate every 1 hour (was 4h - too slow for regime changes)

        # Dwell-time tracking: when each symbol's confirmed regime last
        # switched (set only on confirmed old -> new transitions) and when
        # the current confirmed regime was first established (for
        # time-in-regime reporting).
        self._last_confirmed_switch: Dict[str, datetime] = {}
        self._regime_since: Dict[str, datetime] = {}

        # Trend direction caching (for partial grid unwind decisions)
        self._trend_direction_cache: Dict[str, Dict] = {}
        self._trend_cache_ttl_minutes = 5  # Trend direction cache TTL

        # ADX value cache - stores the last calculated ADX per symbol so downstream
        # components can use it without recalculating from raw data
        self._last_adx: Dict[str, float] = {}
        self._last_calculated_adx: Optional[float] = None  # Set during detect_regime()
        # Last volatility score computed during detect_regime() (only set when
        # the ranging branch runs; None otherwise). Used for observability.
        self._last_volatility_score: Optional[float] = None

        # ML regime detection (lazy-initialised)
        self._gmm_detector = None
        self._use_ml_regime: bool = False
        self._ml_initialised = False

        # ML shadow mode (P3): observe-and-log only. The ML prediction is
        # computed alongside the ADX result at cache-refresh time and
        # persisted to the regime_shadow table; it NEVER changes the
        # returned regime. Lazy-initialised on first refresh.
        self._shadow_detector: Optional[Any] = None
        self._shadow_model_type: Optional[str] = None
        self._shadow_initialised = False

        logger.info(
            f"MarketRegimeDetector initialized: "
            f"ADX trending={adx_trending_threshold}, "
            f"moderate={adx_moderate_threshold}, "
            f"ranging={adx_ranging_threshold}, "
            f"volatility percentile={volatility_high_percentile}%, "
            f"cache_ttl={self._cache_ttl_hours}h"
        )

    # ------------------------------------------------------------------
    # ML regime detection helpers
    # ------------------------------------------------------------------

    def _initialise_gmm(self) -> bool:
        """Lazily initialise the GMM regime detector.

        Called on first use of ``detect_regime``.  Returns ``True`` if the
        GMM detector is ready and a model is loaded.
        """
        if self._ml_initialised:
            return self._gmm_detector is not None and self._gmm_detector.is_model_available()

        self._ml_initialised = True

        try:
            from .feature_flags import get_feature_flags

            flags = get_feature_flags()
            self._use_ml_regime = flags.use_ml_regime
        except Exception as exc:
            logger.debug(f"Could not read ML regime feature flag: {exc}")
            self._use_ml_regime = False

        if not self._use_ml_regime:
            logger.info("ML regime detection is DISABLED (feature flag off)")
            return False

        try:
            from .ml.gmm_regime import GMMRegimeDetector

            self._gmm_detector = GMMRegimeDetector()
            available = self._gmm_detector.is_model_available()
            if available:
                logger.info("GMM regime detector: model loaded and ready")
            else:
                logger.info(
                    "GMM regime detector: initialised but no trained model "
                    "found — will fall back to ADX until model is trained"
                )
            return available
        except ImportError as exc:
            logger.warning(f"Cannot initialise GMM detector (missing dependency): {exc}")
            self._use_ml_regime = False
            return False
        except Exception as exc:
            logger.error(f"Failed to initialise GMM detector: {exc}")
            self._use_ml_regime = False
            return False

    def get_gmm_detector(self):
        """Return the GMM regime detector instance, or ``None`` if unavailable."""
        self._initialise_gmm()
        return self._gmm_detector

    # ------------------------------------------------------------------
    # ML shadow mode (observe-and-log only; never affects detection)
    # ------------------------------------------------------------------

    @staticmethod
    def _shadow_mode_enabled() -> bool:
        """Read the ML_REGIME_SHADOW env flag (default: enabled)."""
        raw = os.getenv("ML_REGIME_SHADOW", "true").strip().lower()
        return raw in ("true", "1", "yes", "on")

    def _initialise_shadow(self) -> bool:
        """Lazily load the shadow ML detector (once per process).

        Prefers the most recently trained model type (LATEST_MODEL marker,
        normally the HMM), falling back to the other family. Logs once and
        stays disabled when no trained artifact exists.

        Returns:
            ``True`` when a shadow detector with a trained model is ready.
        """
        if self._shadow_initialised:
            return self._shadow_detector is not None
        self._shadow_initialised = True

        try:
            from .ml.gmm_regime import GMMRegimeDetector
            from .ml.hmm_regime import HMMRegimeDetector
            from .ml.model_manager import read_latest_model_type
        except Exception as exc:
            logger.warning(
                f"Shadow regime: ML modules unavailable ({exc}); "
                f"shadow observations disabled"
            )
            return False

        candidates = [("hmm", HMMRegimeDetector), ("gmm", GMMRegimeDetector)]
        try:
            if read_latest_model_type() == "gmm":
                candidates.reverse()
        except Exception:
            pass

        for model_type, detector_cls in candidates:
            try:
                detector = detector_cls()
                if detector.ensure_model_loaded():
                    self._shadow_detector = detector
                    self._shadow_model_type = model_type
                    logger.info(
                        f"Shadow regime detector ready: {model_type} "
                        f"(observe-and-log only; ADX remains authoritative)"
                    )
                    return True
            except Exception as exc:
                logger.warning(
                    f"Shadow regime: failed to load {model_type} model: {exc}"
                )

        logger.info(
            "Shadow regime: no trained ML model artifact found; "
            "shadow observations skipped (train via "
            "python -m trading_bot_v2.ml.train_regime_model)"
        )
        return False

    def _record_shadow_observation(
        self,
        symbol: str,
        market_data: Dict[str, List[float]],
        adx_regime: MarketRegime,
    ) -> None:
        """Compute and persist an ML shadow observation (exception-safe).

        Called only at cache-refresh time from ``detect_regime_cached``.
        Any failure is logged and swallowed - shadow mode must never
        affect the returned (ADX) regime.

        Args:
            symbol: Trading symbol.
            market_data: OHLCV dict used for the ADX detection.
            adx_regime: The authoritative ADX regime just computed.
        """
        try:
            if self._db is None or not self._shadow_mode_enabled():
                return
            if not self._initialise_shadow():
                return

            result = self._shadow_detector.predict(
                market_data, allow_fallback=False
            )
            ml_regime = result.system_regime.value
            self._db.save_regime_shadow(
                symbol=symbol,
                adx_regime=adx_regime.value,
                ml_regime=ml_regime,
                ml_confidence=float(result.confidence),
                ml_model_type=self._shadow_model_type,
                agree=int(ml_regime == adx_regime.value),
                detected_at=self._clock(),
            )
        except Exception as exc:
            logger.warning(
                f"Shadow regime observation failed for {symbol}: {exc}"
            )

    def detect_regime(
        self,
        market_data: Dict[str, List[float]],
        previous_regime: Optional[MarketRegime] = None,
    ) -> MarketRegime:
        """
        Detect current market regime from market data.

        When ML regime detection is enabled (USE_ML_REGIME=True) and a
        trained GMM model is available, uses the GMM classifier first.
        Falls back to ADX-based detection if GMM is unavailable or
        confidence is below the threshold.

        Regime classification (Jul 2026 hysteresis hardening). Raw
        thresholds apply when ``previous_regime`` is None (first
        classification); asymmetric exit/entry bands apply otherwise:

        - Holding TRENDING_STRONG: remain while ADX >= adx_exit_trending (22)
        - ADX > adx_trending (25): TRENDING_STRONG
        - adx_moderate (20) < ADX <= adx_trending (25):
          INDECISIVE when ADX slope is falling (current ADX below the ADX of
          3 bars prior), TRENDING_MODERATE when flat/rising
        - ADX <= adx_ranging (20): ranging - split by volatility score:
          * holding RANGING_VOLATILE: remain while score >= vol_score_exit (60)
          * from another confirmed regime: enter RANGING_VOLATILE only when
            score > vol_score_enter (68)
          * no previous regime: enter RANGING_VOLATILE when score >
            volatility_percentile (65)
          * otherwise RANGING_CALM
        - Insufficient data: INDECISIVE

        Args:
            market_data: Dictionary with keys 'high', 'low', 'close', 'volume'.
                Each key maps to a list of float values.
            previous_regime: The previous confirmed regime for this market, if
                any. Enables the hysteresis exit/entry bands above. Callers
                without regime state (one-shot classification) may omit it.

        Returns:
            MarketRegime enum value

        Raises:
            ValueError: If market_data is missing required keys
        """
        # Validate input
        required_keys = ["high", "low", "close"]
        for key in required_keys:
            if key not in market_data:
                raise ValueError(f"market_data missing required key: '{key}'")

        highs = market_data["high"]
        lows = market_data["low"]
        closes = market_data["close"]

        # Check data length
        min_data_required = max(
            self.adx_period * 2 + 1,  # ADX requirement
            self.atr_period + 1,  # ATR requirement
            self.bb_period,  # Bollinger Bands requirement
        )

        if len(closes) < min_data_required:
            logger.warning(
                f"Insufficient data for regime detection. "
                f"Need at least {min_data_required} candles, got {len(closes)}"
            )
            # Return INDECISIVE instead of raising exception
            return MarketRegime.INDECISIVE

        # --- ML regime detection attempt (when enabled) ---
        if self._initialise_gmm() and self._gmm_detector is not None:
            try:
                from .ml.gmm_regime import GMMRegimeResult

                result = self._gmm_detector.predict(market_data)
                if not result.used_fallback:
                    # GMM was confident enough — use its result
                    logger.info(
                        f"Regime (GMM): {result.system_regime.value} "
                        f"(confidence={result.confidence:.3f})"
                    )
                    # Still store ADX for downstream callers
                    try:
                        self._last_calculated_adx = calculate_adx(
                            highs, lows, closes, period=self.adx_period
                        )
                        self._last_adx["_last"] = self._last_calculated_adx
                    except Exception:
                        pass
                    return result.system_regime
                # GMM fell back to ADX — continue with ADX detection below
                logger.debug("GMM confidence below threshold, using ADX fallback")
            except Exception as exc:
                logger.warning(f"GMM regime detection failed, using ADX: {exc}")

        # --- ADX-based regime detection (primary or fallback) ---

        # Step 1: Calculate ADX
        self._last_volatility_score = None
        try:
            adx = calculate_adx(highs, lows, closes, period=self.adx_period)
            self._last_calculated_adx = adx  # Store for get_last_adx() callers
        except Exception as e:
            logger.error(f"Error calculating ADX: {e}")
            raise

        # Step 2: Hysteresis hold - once in TRENDING_STRONG, remain until
        # ADX drops below the exit band (default 22, not the entry 25).
        if (
            previous_regime == MarketRegime.TRENDING_STRONG
            and adx >= self.adx_exit_trending
        ):
            logger.info(
                f"Regime: TRENDING_STRONG held (ADX={adx:.2f} >= "
                f"exit band {self.adx_exit_trending})"
            )
            return MarketRegime.TRENDING_STRONG

        # Step 3: If ADX indicates strong trend, return TRENDING_STRONG
        if adx > self.adx_trending:
            logger.info(
                f"Regime: TRENDING_STRONG (ADX={adx:.2f} > {self.adx_trending})"
            )
            return MarketRegime.TRENDING_STRONG

        # Step 4: 20 < ADX <= 25 band - TRENDING_MODERATE when ADX slope is
        # flat/rising, INDECISIVE when the trend is losing strength.
        if self.adx_moderate < adx <= self.adx_trending:
            if self._adx_slope_falling(highs, lows, closes, adx):
                logger.info(
                    f"Regime: INDECISIVE (ADX={adx:.2f} in "
                    f"{self.adx_moderate}-{self.adx_trending} band, slope falling)"
                )
                return MarketRegime.INDECISIVE
            logger.info(
                f"Regime: TRENDING_MODERATE (ADX={adx:.2f} between "
                f"{self.adx_moderate}-{self.adx_trending}, slope flat/rising)"
            )
            return MarketRegime.TRENDING_MODERATE

        # Step 5: ADX <= 20 - market is ranging, determine volatility level
        if adx <= self.adx_ranging:
            try:
                volatility_score = self._calculate_volatility_score(highs, lows, closes)
            except Exception as e:
                logger.error(f"Error calculating volatility: {e}")
                # Default to RANGING_CALM if volatility calculation fails
                return MarketRegime.RANGING_CALM

            self._last_volatility_score = volatility_score

            # Hysteresis hold - once in RANGING_VOLATILE, remain until the
            # vol score drops below the exit band (default 60).
            if previous_regime == MarketRegime.RANGING_VOLATILE:
                if volatility_score >= self.vol_score_exit:
                    logger.info(
                        f"Regime: RANGING_VOLATILE held (vol={volatility_score:.1f}% "
                        f">= exit band {self.vol_score_exit}%)"
                    )
                    return MarketRegime.RANGING_VOLATILE
                logger.info(
                    f"Regime: RANGING_CALM (vol={volatility_score:.1f}% < "
                    f"exit band {self.vol_score_exit}%)"
                )
                return MarketRegime.RANGING_CALM

            # Entry threshold: raw 65 on first classification, stricter 68
            # band when switching in from another confirmed regime.
            enter_threshold = (
                self.volatility_percentile
                if previous_regime is None
                else self.vol_score_enter
            )
            if volatility_score > enter_threshold:
                logger.info(
                    f"Regime: RANGING_VOLATILE (ADX={adx:.2f} <= {self.adx_ranging}, "
                    f"volatility={volatility_score:.1f}% > {enter_threshold}%)"
                )
                return MarketRegime.RANGING_VOLATILE
            logger.info(
                f"Regime: RANGING_CALM (ADX={adx:.2f} <= {self.adx_ranging}, "
                f"volatility={volatility_score:.1f}% <= {enter_threshold}%)"
            )
            return MarketRegime.RANGING_CALM

        # Step 6: Fallback - only reachable when adx_moderate is configured
        # above adx_ranging, leaving a gap between the two bands.
        logger.info(f"Regime: INDECISIVE (ADX={adx:.2f} - transitional)")
        return MarketRegime.INDECISIVE

    def _adx_slope_falling(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        current_adx: float,
        lookback: int = 3,
    ) -> bool:
        """
        Check whether ADX is falling versus ``lookback`` bars prior.

        Recomputes ADX on the series truncated by ``lookback`` bars and
        compares it with the current ADX. Used to split the
        20 < ADX <= 25 band into TRENDING_MODERATE (flat/rising) and
        INDECISIVE (falling). Returns False (treat as flat/rising) when
        there is insufficient data or the calculation fails.

        Args:
            highs: High prices, oldest first.
            lows: Low prices, oldest first.
            closes: Close prices, oldest first.
            current_adx: ADX computed on the full series.
            lookback: How many bars back to compare against (default: 3).

        Returns:
            True when current ADX is below the ADX from ``lookback`` bars ago.
        """
        min_required = self.adx_period * 2 + 1
        if len(closes) - lookback < min_required:
            return False
        try:
            prior_adx = calculate_adx(
                highs[:-lookback],
                lows[:-lookback],
                closes[:-lookback],
                period=self.adx_period,
            )
        except Exception as e:
            logger.debug(f"ADX slope calculation failed: {e}")
            return False
        return current_adx < prior_adx

    def detect_regime_cached(
        self, symbol: str, market_data: Dict[str, List[float]]
    ) -> MarketRegime:
        """
        Detect regime with caching to prevent unnecessary recalculation.

        Only recalculates when:
        1. No cached regime exists for symbol
        2. Cache is older than TTL (1 hour)

        Args:
            symbol: Trading symbol
            market_data: OHLCV data dictionary

        Returns:
            Current market regime
        """
        # Check cache first
        if symbol in self._regime_cache:
            cache_entry = self._regime_cache[symbol]
            cache_age_hours = (
                self._clock() - cache_entry["timestamp"]
            ).total_seconds() / 3600

            # Return cached regime if still valid
            if cache_age_hours < self._cache_ttl_hours:
                logger.debug(
                    f"Using cached regime for {symbol}: {cache_entry['regime'].value} ({cache_age_hours:.1f}h old)"
                )
                return cache_entry["regime"]

        # Cache miss or expired - check for pending regime change
        new_regime = self._detect_regime_with_confirmation(symbol, market_data)

        # Store the ADX value that was calculated during regime detection
        # so downstream components can use it without recalculating
        if self._last_calculated_adx is not None:
            self._last_adx[symbol] = self._last_calculated_adx

        # Update cache
        self._regime_cache[symbol] = {
            "regime": new_regime,
            "timestamp": self._clock(),
            "data_hash": self._hash_market_data(market_data),
        }

        # Track when this confirmed regime was first established
        if symbol not in self._regime_since:
            self._regime_since[symbol] = self._clock()

        # ML shadow observation (P3): runs only at cache-refresh time,
        # never on cache hits, and never changes the returned regime.
        self._record_shadow_observation(symbol, market_data, new_regime)

        logger.debug(f"Regime recalculated for {symbol}: {new_regime.value}")
        return new_regime

    def get_last_adx(self, symbol: str) -> Optional[float]:
        """
        Return the last ADX value calculated for this symbol during regime detection.

        This allows downstream components (e.g. GridTradingStrategy) to use the
        regime detector's already-calculated ADX instead of recalculating it from
        the same raw data. Returns None if regime detection hasn't run for this
        symbol yet or if the cache was served (no recalculation occurred).
        """
        return self._last_adx.get(symbol)

    def _detect_regime_with_confirmation(
        self, symbol: str, market_data: Dict[str, List[float]]
    ) -> MarketRegime:
        """
        Detect regime with confirmation and dwell-time to prevent flicker.

        Layered anti-flap protections:
        1. 2-count confirmation: a new regime must be detected on 2
           consecutive checks before it replaces the confirmed regime.
        2. Minimum dwell time: after a confirmed switch, further switches are
           suppressed for ``min_dwell_hours`` (detections still run and log,
           but the confirmed regime holds).

        Confirmed transitions publish EventType.REGIME_CHANGED and persist to
        the regime_history table when an event bus / db are wired in.
        """
        current_regime = self._regime_cache.get(symbol, {}).get("regime")
        new_regime = self.detect_regime(market_data, previous_regime=current_regime)

        # First detection for this symbol - accept immediately
        if current_regime is None:
            return new_regime

        # No change - clear any stale pending state
        if new_regime == current_regime:
            self._pending_regime_changes.pop(symbol, None)
            return current_regime

        # Candidate switch - enforce minimum dwell time after the last
        # confirmed switch. Detection already ran and logged above.
        last_switch = self._last_confirmed_switch.get(symbol)
        if last_switch is not None:
            hours_since_switch = (
                self._clock() - last_switch
            ).total_seconds() / 3600.0
            if hours_since_switch < self.min_dwell_hours:
                logger.info(
                    f"Regime switch suppressed for {symbol} by min dwell: "
                    f"{current_regime.value} held, {new_regime.value} detected "
                    f"({hours_since_switch:.1f}h < {self.min_dwell_hours}h)"
                )
                self._pending_regime_changes.pop(symbol, None)
                return current_regime

        # 2-count confirmation
        pending = self._pending_regime_changes.get(symbol)
        if pending is not None and pending["regime"] == new_regime:
            if pending["confirmations"] >= 1:  # Require 2 total detections
                del self._pending_regime_changes[symbol]
                logger.info(
                    f"Regime change confirmed for {symbol}: "
                    f"{current_regime.value} -> {new_regime.value}"
                )
                now = self._clock()
                self._last_confirmed_switch[symbol] = now
                self._regime_since[symbol] = now
                self._record_regime_transition(symbol, current_regime, new_regime)
                return new_regime
            pending["confirmations"] += 1
            return current_regime

        # Different (or no) pending regime - start a new pending change
        self._pending_regime_changes[symbol] = {
            "regime": new_regime,
            "previous_regime": current_regime,
            "confirmations": 1,
            "timestamp": self._clock(),
        }
        logger.debug(
            f"Regime change pending for {symbol}: {current_regime.value} -> {new_regime.value}"
        )
        return current_regime  # Keep current until confirmed

    def _record_regime_transition(
        self,
        symbol: str,
        old_regime: MarketRegime,
        new_regime: MarketRegime,
    ) -> None:
        """
        Publish and persist a confirmed regime transition.

        Publishes EventType.REGIME_CHANGED on the wired event bus and inserts
        a row into regime_history via the wired db. Both are optional and
        failures are logged without breaking detection.

        Args:
            symbol: Trading symbol.
            old_regime: The regime being exited.
            new_regime: The newly confirmed regime.
        """
        adx = self._last_calculated_adx
        volatility_score = self._last_volatility_score
        timestamp = self._clock()

        if self._event_bus is not None:
            try:
                self._event_bus.publish_event(
                    EventType.REGIME_CHANGED,
                    {
                        "symbol": symbol,
                        "old_regime": old_regime.value,
                        "new_regime": new_regime.value,
                        "adx": adx,
                        "volatility_score": volatility_score,
                        "timestamp": timestamp.isoformat(),
                        "affected_strategies": self.get_active_strategies(new_regime),
                    },
                    source="MarketRegimeDetector",
                )
            except Exception as e:
                logger.error(f"Failed to publish REGIME_CHANGED for {symbol}: {e}")

        if self._db is not None:
            try:
                self._db.save_regime_transition(
                    symbol=symbol,
                    old_regime=old_regime.value,
                    new_regime=new_regime.value,
                    adx=adx,
                    volatility_score=volatility_score,
                    detected_at=timestamp,
                )
            except Exception as e:
                logger.error(
                    f"Failed to persist regime transition for {symbol}: {e}"
                )

    def get_current_regime(self, symbol: str) -> Optional[MarketRegime]:
        """
        Return the current confirmed regime for a symbol without recalculating.

        Alias-style accessor over the regime cache used by trade tagging and
        the API layer. Returns None when no regime has been detected yet.

        Args:
            symbol: Trading symbol

        Returns:
            Confirmed MarketRegime or None
        """
        return self.get_cached_regime(symbol)

    def get_regime_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """
        Return the current confirmed regime per symbol with time-in-regime.

        Returns:
            Dict keyed by symbol with regime value, since timestamp (ISO),
            time_in_regime_seconds, and last known ADX / volatility score.
        """
        snapshot: Dict[str, Dict[str, Any]] = {}
        now = self._clock()
        for symbol, entry in self._regime_cache.items():
            regime = entry.get("regime")
            since = self._regime_since.get(symbol)
            snapshot[symbol] = {
                "regime": regime.value if regime else None,
                "since": since.isoformat() if since else None,
                "time_in_regime_seconds": (
                    (now - since).total_seconds() if since else None
                ),
                "adx": self._last_adx.get(symbol),
            }
        return snapshot

    def _hash_market_data(self, market_data: Dict[str, List[float]]) -> str:
        """Create hash of market data for change detection."""
        import hashlib

        data_str = str(sorted(market_data.items()))
        return hashlib.md5(data_str.encode(), usedforsecurity=False).hexdigest()

    def _calculate_volatility_score(
        self, highs: List[float], lows: List[float], closes: List[float]
    ) -> float:
        """
        Calculate volatility score (0-100) using Bollinger Band width and ATR percentile.

        Algorithm:
        1. Calculate Bollinger Band width as % of middle band
        2. Calculate current ATR
        3. Calculate ATR percentile over lookback period
        4. Combine both metrics into single score

        Returns:
            Volatility score (0-100), where higher = more volatile
        """
        # Calculate Bollinger Band width
        upper, middle, lower = calculate_bollinger_bands(closes, period=self.bb_period)
        bb_width_pct = ((upper - lower) / middle) * 100 if middle > 0 else 0

        # Calculate current ATR
        current_atr = calculate_atr(highs, lows, closes, period=self.atr_period)

        # Calculate ATR percentile over last 100 periods (or available data)
        lookback = min(100, len(closes) - self.atr_period)
        atr_values = []

        for i in range(lookback):
            # Calculate ATR for each historical window
            start_idx = len(closes) - lookback + i - self.atr_period
            end_idx = len(closes) - lookback + i + 1

            if start_idx >= 0 and end_idx <= len(closes):
                try:
                    atr = calculate_atr(
                        highs[start_idx:end_idx],
                        lows[start_idx:end_idx],
                        closes[start_idx:end_idx],
                        period=self.atr_period,
                    )
                    atr_values.append(atr)
                except (ValueError, IndexError, TypeError):
                    continue

        # Calculate percentile rank of current ATR
        if atr_values:
            atr_percentile = (
                sum(1 for atr in atr_values if atr < current_atr) / len(atr_values)
            ) * 100
        else:
            atr_percentile = 50.0  # Default to middle if calculation fails

        # Combine BB width and ATR percentile (weighted average)
        # BB width is normalized to 0-100 range (assuming typical BB width is 0-10%)
        bb_score = min(bb_width_pct * 10, 100)  # Scale to 0-100

        # Weight: 60% ATR percentile, 40% BB width
        volatility_score = (atr_percentile * 0.6) + (bb_score * 0.4)

        logger.debug(
            f"Volatility calculation: BB width={bb_width_pct:.2f}%, "
            f"ATR percentile={atr_percentile:.1f}%, "
            f"combined score={volatility_score:.1f}%"
        )

        return volatility_score

    def get_active_strategies(self, regime: MarketRegime) -> List[str]:
        """
        Map market regime to list of active strategy names.

        Updated mapping (Jan 2026 upgrade):
        - TRENDING_STRONG: MA Crossover (trend following not implemented)
        - TRENDING_MODERATE: MA Crossover + Trend Following (lighter trend exposure)
        - RANGING_VOLATILE: Grid Trading only (high volatility oscillations)
        - RANGING_CALM: Mean Reversion + Grid Trading (quiet oscillations)
        - INDECISIVE: Grid Trading (optional - use [] for conservative approach)

        Args:
            regime: MarketRegime enum value

        Returns:
            List of strategy names that should be active in this regime
        """
        regime_strategy_map = {
            MarketRegime.TRENDING_STRONG: [
                "MACrossover",
                "MomentumScalping",  # Fast EMA crossover for strong trends
            ],
            MarketRegime.TRENDING_MODERATE: [
                "MACrossover",
                "MomentumScalping",  # Fast EMA crossover for moderate trends
            ],
            MarketRegime.RANGING_VOLATILE: ["GridTrading"],
            MarketRegime.RANGING_CALM: ["MeanReversion", "GridTrading"],  # Grid added
            MarketRegime.INDECISIVE: [
                "LiquidationCapture"
            ],  # Conservative - only liquidation capture
        }

        strategies = regime_strategy_map.get(regime, [])
        logger.debug(f"Regime {regime.value} -> Active strategies: {strategies}")

        return strategies

    def is_grid_allowed(self, regime: MarketRegime) -> bool:
        """
        Check if grid trading is allowed in the current regime per Grid Trading Brief.

        Grid trading is only allowed in:
        - RANGING_CALM
        - RANGING_VOLATILE
        - INDECISIVE (conservative)

        NOT allowed in:
        - TRENDING_STRONG
        - TRENDING_MODERATE

        Args:
            regime: Current market regime

        Returns:
            True if grid trading is allowed, False otherwise
        """
        allowed_regimes = [
            MarketRegime.RANGING_CALM,
            MarketRegime.RANGING_VOLATILE,
            MarketRegime.INDECISIVE,
        ]

        is_allowed = regime in allowed_regimes
        logger.debug(
            f"Grid trading {'allowed' if is_allowed else 'not allowed'} in {regime.value} regime"
        )
        return is_allowed

    def get_strategy_weights(self, regime: MarketRegime) -> Dict[str, float]:
        """
        Get strategy allocation weights for current regime.

        Updated weights (Jan 2026 upgrade) with TRENDING_MODERATE regime.

        Args:
            regime: MarketRegime enum value

        Returns:
            Dictionary mapping strategy name to weight (0-1)
        """
        weight_map = {
            MarketRegime.TRENDING_STRONG: {
                "MACrossover": 0.5,  # Primary trend strategy
                "MomentumScalping": 0.3,  # Fast scalping complements MA
                "OrderBookImbalance": 0.2,  # Flow confirmation
                "SessionRangeBreakout": 0.15,  # Time-gated ORB overlay
                "CalendarFlow": 0.1,  # Calendar-gated TOM overlay
            },
            MarketRegime.TRENDING_MODERATE: {
                "MACrossover": 0.4,  # Balanced with momentum
                "MomentumScalping": 0.4,  # Equal weight in moderate trends
                "OrderBookImbalance": 0.2,  # Flow confirmation
                "SessionRangeBreakout": 0.15,  # Time-gated ORB overlay
                "CalendarFlow": 0.1,  # Calendar-gated TOM overlay
            },
            MarketRegime.RANGING_VOLATILE: {
                "GridTrading": 0.8,
                "OrderBookImbalance": 0.2,  # Flow-based overlay
                "SessionRangeBreakout": 0.15,  # Time-gated ORB overlay
                "CalendarFlow": 0.1,  # Calendar-gated TOM overlay
            },
            MarketRegime.RANGING_CALM: {
                "MeanReversion": 0.6,
                "GridTrading": 0.2,
                "OrderBookImbalance": 0.2,  # Flow-based overlay
                "SessionRangeBreakout": 0.15,  # Time-gated ORB overlay
                "CalendarFlow": 0.1,  # Calendar-gated TOM overlay
            },
            MarketRegime.INDECISIVE: {
                "LiquidationCapture": 0.6,  # Conservative approach
                "OrderBookImbalance": 0.4,  # Flow-based (best in choppy markets)
                "SessionRangeBreakout": 0.2,  # ORB thrives on post-chop expansion
                "CalendarFlow": 0.1,  # Calendar-gated TOM overlay
            },
        }

        weights = weight_map.get(regime, {})
        logger.debug(f"Regime {regime.value} -> Strategy weights: {weights}")

        return weights

    def get_trend_direction(self, market_data: Dict[str, List[float]]) -> str:
        """
        Determine trend direction based on MA crossover signals.

        Uses SMA 50 and SMA 200 relationship:
        - SMA 50 > SMA 200: Bullish trend ('up')
        - SMA 50 < SMA 200: Bearish trend ('down')
        - Insufficient data: No clear trend ('none')

        Args:
            market_data: OHLCV data dict with 'high', 'low', 'close', 'open' keys

        Returns:
            'up' - 50 MA above 200 MA (bullish trend / golden cross territory)
            'down' - 50 MA below 200 MA (bearish trend / death cross territory)
            'none' - Insufficient data or no clear trend
        """
        # Validate input
        if "close" not in market_data:
            logger.warning("market_data missing 'close' key for trend direction")
            return "none"

        closes = market_data["close"]

        # Need at least 200 candles for SMA 200
        if len(closes) < 200:
            logger.debug(
                f"Insufficient data for trend direction: {len(closes)} candles (need 200)"
            )
            return "none"

        try:
            # Calculate SMA 50 and SMA 200
            sma_50 = calculate_sma(closes, 50)
            sma_200 = calculate_sma(closes, 200)

            # Determine trend direction
            if sma_50 > sma_200:
                direction = "up"
                logger.debug(
                    f"Trend direction: UP (SMA50={sma_50:.2f} > SMA200={sma_200:.2f})"
                )
            elif sma_50 < sma_200:
                direction = "down"
                logger.debug(
                    f"Trend direction: DOWN (SMA50={sma_50:.2f} < SMA200={sma_200:.2f})"
                )
            else:
                direction = "none"
                logger.debug(
                    f"Trend direction: NONE (SMA50={sma_50:.2f} = SMA200={sma_200:.2f})"
                )

            return direction

        except Exception as e:
            logger.error(f"Error calculating trend direction: {e}")
            return "none"

    def get_trend_direction_cached(
        self, symbol: str, market_data: Dict[str, List[float]]
    ) -> str:
        """
        Get trend direction with caching to avoid redundant calculations.

        Cache TTL: 5 minutes (trends don't change rapidly).

        Args:
            symbol: Trading symbol
            market_data: OHLCV data dictionary

        Returns:
            Trend direction: 'up', 'down', or 'none'
        """
        from datetime import datetime

        # Check cache first
        if symbol in self._trend_direction_cache:
            cache_entry = self._trend_direction_cache[symbol]
            cache_age_minutes = (
                datetime.now() - cache_entry["timestamp"]
            ).total_seconds() / 60

            # Return cached direction if still valid
            if cache_age_minutes < self._trend_cache_ttl_minutes:
                logger.debug(
                    f"Using cached trend direction for {symbol}: "
                    f"{cache_entry['direction']} ({cache_age_minutes:.1f}m old)"
                )
                return cache_entry["direction"]

        # Cache miss or expired - recalculate
        direction = self.get_trend_direction(market_data)

        # Update cache
        self._trend_direction_cache[symbol] = {
            "direction": direction,
            "timestamp": datetime.now(),
        }

        logger.debug(f"Trend direction calculated for {symbol}: {direction}")
        return direction

    def clear_trend_cache(self, symbol: Optional[str] = None):
        """
        Clear trend direction cache.

        Args:
            symbol: Specific symbol to clear, or None to clear all
        """
        if symbol:
            if symbol in self._trend_direction_cache:
                del self._trend_direction_cache[symbol]
                logger.debug(f"Cleared trend cache for {symbol}")
        else:
            self._trend_direction_cache.clear()
            logger.debug("Cleared all trend direction caches")

    def get_cached_regime(self, symbol: str) -> Optional[MarketRegime]:
        """
        Get cached regime for a symbol without recalculating.

        This is a lookup-only method that returns None if no cache exists.
        Use detect_regime_cached() to get regime with automatic calculation.

        Args:
            symbol: Trading symbol

        Returns:
            Cached MarketRegime or None if not cached
        """
        if symbol in self._regime_cache:
            return self._regime_cache[symbol].get("regime")
        return None

    def clear_regime_cache(self, symbol: Optional[str] = None):
        """
        Clear regime cache.

        Args:
            symbol: Specific symbol to clear, or None to clear all
        """
        if symbol:
            if symbol in self._regime_cache:
                del self._regime_cache[symbol]
                logger.debug(f"Cleared regime cache for {symbol}")
            if symbol in self._pending_regime_changes:
                del self._pending_regime_changes[symbol]
            self._regime_since.pop(symbol, None)
            self._last_confirmed_switch.pop(symbol, None)
        else:
            self._regime_cache.clear()
            self._pending_regime_changes.clear()
            self._regime_since.clear()
            self._last_confirmed_switch.clear()
            logger.debug("Cleared all regime caches")
