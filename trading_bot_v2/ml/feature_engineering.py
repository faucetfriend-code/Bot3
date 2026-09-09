"""
Feature Engineering Module for GMM Regime Detection

Extracts 6 statistical features from OHLCV data for market regime classification:

1. Volatility: 20-period rolling standard deviation of log returns
2. Returns: 20-period rolling mean of log returns
3. Skewness: Rolling skewness of returns (distribution asymmetry)
4. ATR ratio: Current ATR / 100-period mean ATR
5. Volume ratio: Current volume / 20-period mean volume
6. BB width: Bollinger Band width as % of middle band

These features capture distinct market characteristics that GMM uses to
identify latent regimes (trending, ranging, volatile) without explicit labels.
"""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
from loguru import logger

from ..indicators import calculate_atr, calculate_bollinger_bands


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_VOLATILITY_PERIOD: int = 20
DEFAULT_RETURNS_PERIOD: int = 20
DEFAULT_SKEWNESS_PERIOD: int = 20
DEFAULT_ATR_PERIOD: int = 14
DEFAULT_ATR_MEAN_PERIOD: int = 100
DEFAULT_VOLUME_PERIOD: int = 20
DEFAULT_BB_PERIOD: int = 20

# Minimum candles needed before any feature can be reliably extracted.
# Dominated by the largest lookback window (100-period ATR mean).
MIN_CANDLES: int = DEFAULT_ATR_MEAN_PERIOD + DEFAULT_ATR_PERIOD + 1


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarketFeatures:
    """Container for the 6 statistical features extracted from OHLCV data.

    All fields are plain floats so the result can be converted to a numpy
    array with a single ``np.array(list(features))`` call for GMM inference.
    """

    volatility: float
    returns: float
    skewness: float
    atr_ratio: float
    volume_ratio: float
    bb_width: float

    def to_array(self) -> np.ndarray:
        """Return features as a 1-D numpy array suitable for GMM prediction.

        Returns:
            numpy array of shape ``(6,)`` with feature values in order.
        """
        return np.array(
            [
                self.volatility,
                self.returns,
                self.skewness,
                self.atr_ratio,
                self.volume_ratio,
                self.bb_width,
            ],
            dtype=np.float64,
        )

    def to_list(self) -> List[float]:
        """Return features as a plain Python list."""
        return [
            self.volatility,
            self.returns,
            self.skewness,
            self.atr_ratio,
            self.volume_ratio,
            self.bb_width,
        ]


# ---------------------------------------------------------------------------
# Feature extractor
# ---------------------------------------------------------------------------


class FeatureExtractor:
    """Extracts statistical features from OHLCV data for GMM regime detection.

    The extractor works with raw Python lists (as used throughout Bot 3) and
    returns :class:`MarketFeatures` instances ready for GMM inference.

    Example::

        extractor = FeatureExtractor()
        features = extractor.extract(closes, highs, lows, volumes)
        array = features.to_array()  # (6,) numpy array
    """

    def __init__(
        self,
        volatility_period: int = DEFAULT_VOLATILITY_PERIOD,
        returns_period: int = DEFAULT_RETURNS_PERIOD,
        skewness_period: int = DEFAULT_SKEWNESS_PERIOD,
        atr_period: int = DEFAULT_ATR_PERIOD,
        atr_mean_period: int = DEFAULT_ATR_MEAN_PERIOD,
        volume_period: int = DEFAULT_VOLUME_PERIOD,
        bb_period: int = DEFAULT_BB_PERIOD,
    ) -> None:
        """Initialise the feature extractor.

        Args:
            volatility_period: Window for rolling std of log returns.
            returns_period: Window for rolling mean of log returns.
            skewness_period: Window for rolling skewness of log returns.
            atr_period: Period for current ATR calculation.
            atr_mean_period: Lookback for mean ATR baseline.
            volume_period: Window for rolling mean volume.
            bb_period: Period for Bollinger Band width.
        """
        self.volatility_period = volatility_period
        self.returns_period = returns_period
        self.skewness_period = skewness_period
        self.atr_period = atr_period
        self.atr_mean_period = atr_mean_period
        self.volume_period = volume_period
        self.bb_period = bb_period

        logger.debug(
            f"FeatureExtractor initialised: vol={volatility_period}, "
            f"ret={returns_period}, skew={skewness_period}, "
            f"atr={atr_period}, atr_mean={atr_mean_period}, "
            f"vol_ratio={volume_period}, bb={bb_period}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(
        self,
        closes: List[float],
        highs: Optional[List[float]] = None,
        lows: Optional[List[float]] = None,
        volumes: Optional[List[float]] = None,
    ) -> MarketFeatures:
        """Extract all 6 features from the latest candle window.

        Args:
            closes: Closing prices (most recent last).
            highs: High prices. Required for ATR ratio.
            lows: Low prices. Required for ATR ratio.
            volumes: Volume values. If ``None``, volume_ratio is 0.

        Returns:
            :class:`MarketFeatures` with the latest feature snapshot.

        Raises:
            ValueError: If ``closes`` is too short for any feature window.
        """
        closes_arr = np.asarray(closes, dtype=np.float64)
        highs_arr = np.asarray(highs, dtype=np.float64) if highs is not None else None
        lows_arr = np.asarray(lows, dtype=np.float64) if lows is not None else None
        volumes_arr = (
            np.asarray(volumes, dtype=np.float64) if volumes is not None else None
        )

        n = len(closes_arr)
        if n < MIN_CANDLES:
            raise ValueError(
                f"Insufficient data for feature extraction. "
                f"Need at least {MIN_CANDLES} candles, got {n}"
            )

        volatility = self._calc_volatility(closes_arr)
        returns = self._calc_returns(closes_arr)
        skewness = self._calc_skewness(closes_arr)
        atr_ratio = self._calc_atr_ratio(closes_arr, highs_arr, lows_arr)
        volume_ratio = self._calc_volume_ratio(volumes_arr)
        bb_width = self._calc_bb_width(closes_arr)

        return MarketFeatures(
            volatility=volatility,
            returns=returns,
            skewness=skewness,
            atr_ratio=atr_ratio,
            volume_ratio=volume_ratio,
            bb_width=bb_width,
        )

    def extract_batch(
        self,
        closes: List[float],
        highs: Optional[List[float]] = None,
        lows: Optional[List[float]] = None,
        volumes: Optional[List[float]] = None,
    ) -> List[MarketFeatures]:
        """Extract features for every valid window position (sliding window).

        Useful for training the GMM model on historical data.  Each returned
        :class:`MarketFeatures` corresponds to a point in time where all 6
        features could be computed.

        Args:
            closes: Full closing price history (oldest first).
            highs: Full high price history.
            lows: Full low price history.
            volumes: Full volume history.

        Returns:
            List of :class:`MarketFeatures`, one per valid time step (newest first).
        """
        closes_arr = np.asarray(closes, dtype=np.float64)
        highs_arr = np.asarray(highs, dtype=np.float64) if highs is not None else None
        lows_arr = np.asarray(lows, dtype=np.float64) if lows is not None else None
        volumes_arr = (
            np.asarray(volumes, dtype=np.float64) if volumes is not None else None
        )

        n = len(closes_arr)
        if n < MIN_CANDLES:
            logger.warning(
                f"Insufficient data for batch extraction: {n} candles "
                f"(need {MIN_CANDLES})"
            )
            return []

        results: List[MarketFeatures] = []

        # We can extract features from index MIN_CANDLES-1 to n-1.
        for end_idx in range(MIN_CANDLES - 1, n):
            window_closes = closes_arr[: end_idx + 1]
            window_highs = highs_arr[: end_idx + 1] if highs_arr is not None else None
            window_lows = lows_arr[: end_idx + 1] if lows_arr is not None else None
            window_vols = (
                volumes_arr[: end_idx + 1] if volumes_arr is not None else None
            )

            try:
                feat = MarketFeatures(
                    volatility=self._calc_volatility(window_closes),
                    returns=self._calc_returns(window_closes),
                    skewness=self._calc_skewness(window_closes),
                    atr_ratio=self._calc_atr_ratio(
                        window_closes, window_highs, window_lows
                    ),
                    volume_ratio=self._calc_volume_ratio(window_vols),
                    bb_width=self._calc_bb_width(window_closes),
                )
                results.append(feat)
            except Exception as exc:
                logger.debug(f"Skipping window ending at index {end_idx}: {exc}")
                continue

        logger.debug(
            f"Batch extraction complete: {len(results)} feature vectors "
            f"from {n} candles"
        )
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _calc_volatility(self, closes: np.ndarray) -> float:
        """20-period rolling standard deviation of log returns.

        Volatility captures the dispersion of price movements.  Higher values
        indicate a more turbulent market.
        """
        log_returns = np.log(closes[1:] / closes[:-1])
        window = log_returns[-self.volatility_period :]
        return float(np.std(window, ddof=1)) if len(window) > 1 else 0.0

    def _calc_returns(self, closes: np.ndarray) -> float:
        """20-period rolling mean of log returns.

        Positive mean indicates a general uptrend over the window; negative
        indicates a downtrend.
        """
        log_returns = np.log(closes[1:] / closes[:-1])
        window = log_returns[-self.returns_period :]
        return float(np.mean(window)) if len(window) > 0 else 0.0

    def _calc_skewness(self, closes: np.ndarray) -> float:
        """Rolling skewness of log returns.

        Skewness measures the asymmetry of the return distribution.  Positive
        skewness suggests more frequent large up-moves; negative skewness
        suggests more frequent large down-moves.
        """
        log_returns = np.log(closes[1:] / closes[:-1])
        window = log_returns[-self.skewness_period :]
        if len(window) < 3:
            return 0.0

        mean = np.mean(window)
        std = np.std(window, ddof=1)
        if std == 0.0:
            return 0.0

        n = len(window)
        skew = (n / ((n - 1) * (n - 2))) * np.sum(((window - mean) / std) ** 3)
        return float(skew)

    def _calc_atr_ratio(
        self,
        closes: np.ndarray,
        highs: Optional[np.ndarray],
        lows: Optional[np.ndarray],
    ) -> float:
        """Current ATR / 100-period mean ATR.

        A ratio > 1 means current volatility exceeds the historical average;
        < 1 means markets are calmer than usual.
        """
        if highs is None or lows is None:
            return 1.0  # neutral default

        try:
            # Current ATR from the full array (indicators expect lists)
            current_atr = calculate_atr(
                highs.tolist(), lows.tolist(), closes.tolist(), period=self.atr_period
            )
        except Exception:
            return 1.0

        if current_atr == 0.0:
            return 1.0

        # Mean ATR over lookback
        atr_values: List[float] = []
        lookback = min(self.atr_mean_period, len(closes) - self.atr_period - 1)
        for i in range(lookback):
            start = len(closes) - lookback + i - self.atr_period
            end = len(closes) - lookback + i + 1
            if start >= 0 and end <= len(closes):
                try:
                    atr_val = calculate_atr(
                        highs[start:end].tolist(),
                        lows[start:end].tolist(),
                        closes[start:end].tolist(),
                        period=self.atr_period,
                    )
                    atr_values.append(atr_val)
                except Exception:
                    continue

        if not atr_values:
            return 1.0

        mean_atr = float(np.mean(atr_values))
        return current_atr / mean_atr if mean_atr > 0 else 1.0

    def _calc_volume_ratio(self, volumes: Optional[np.ndarray]) -> float:
        """Current volume / 20-period mean volume.

        Spikes above 1.0 indicate unusual trading activity that often
        accompanies regime transitions.
        """
        if volumes is None or len(volumes) < 2:
            return 1.0

        current_vol = volumes[-1]
        window = volumes[-self.volume_period :]
        mean_vol = float(np.mean(window))

        return current_vol / mean_vol if mean_vol > 0 else 1.0

    def _calc_bb_width(self, closes: np.ndarray) -> float:
        """Bollinger Band width as a percentage of the middle band.

        Wider bands indicate higher volatility; narrower bands indicate
        consolidation.
        """
        try:
            upper, middle, lower = calculate_bollinger_bands(
                closes.tolist(), period=self.bb_period
            )
        except Exception:
            return 0.0

        if middle == 0.0:
            return 0.0

        return ((upper - lower) / middle) * 100.0
