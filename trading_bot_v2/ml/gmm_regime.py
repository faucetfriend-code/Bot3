"""
GMM-Based Market Regime Detector

Uses Gaussian Mixture Models (GMM) from scikit-learn to classify market
conditions into latent regimes derived from statistical features.  Serves
as an optional, ML-powered alternative to the ADX-based detector.

The GMM identifies 3 latent clusters from the 6-dimensional feature space:

- Cluster 0 → TRENDING (typically high momentum, low skewness)
- Cluster 1 → RANGING (low volatility, moderate returns)
- Cluster 2 → VOLATILE (high ATR ratio, high BB width)

These 3 latent labels are mapped to the system's 5 existing regimes via a
configurable mapping, and a confidence score governs fallback behaviour.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger

from ..market_regime import MarketRegime
from .feature_engineering import FeatureExtractor, MarketFeatures
from .model_manager import ModelManager


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class GMMConfig:
    """Configuration for the GMM regime detector.

    Attributes:
        n_regimes: Number of GMM components (latent regimes).
        covariance_type: GMM covariance structure ('full', 'tied', 'diag', 'spherical').
        training_lookback_months: How many months of history to use for training.
        confidence_threshold: Minimum prediction probability to trust the GMM.
            Below this threshold the system falls back to ADX detection.
        retrain_interval_hours: How often to retrain (0 = manual only).
        max_train_candles: Maximum candles used for training to bound memory.
        min_train_candles: Minimum candles required before training is allowed.
    """

    n_regimes: int = 3
    covariance_type: str = "full"
    training_lookback_months: int = 6
    confidence_threshold: float = 0.6
    retrain_interval_hours: int = 24
    max_train_candles: int = 50_000
    min_train_candles: int = 2_000


# ---------------------------------------------------------------------------
# Latent regime labels (internal GMM clusters)
# ---------------------------------------------------------------------------

class _LatentRegime(Enum):
    """Internal regime labels before mapping to system regimes.

    The numeric ordering is not meaningful — the mapping from GMM cluster
    index to latent regime is determined by the ``_cluster_to_latent``
    mapping computed during training.
    """

    TRENDING = "trending"
    RANGING = "ranging"
    VOLATILE = "volatile"


# Default mapping from latent regime to system MarketRegime.
# These can be overridden via ``GMMConfig`` if needed.
_DEFAULT_REGIME_MAP: Dict[_LatentRegime, MarketRegime] = {
    _LatentRegime.TRENDING: MarketRegime.TRENDING_MODERATE,
    _LatentRegime.RANGING: MarketRegime.RANGING_CALM,
    _LatentRegime.VOLATILE: MarketRegime.RANGING_VOLATILE,
}


# ---------------------------------------------------------------------------
# Regime detection result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GMMRegimeResult:
    """Result of a GMM regime prediction.

    Attributes:
        system_regime: The mapped :class:`MarketRegime` for the system.
        latent_regime: The raw latent regime identified by GMM.
        confidence: Maximum class probability (0-1).
        probabilities: Per-cluster probabilities.
        used_fallback: ``True`` if the ADX detector was used instead.
    """

    system_regime: MarketRegime
    latent_regime: _LatentRegime
    confidence: float
    probabilities: Dict[str, float]
    used_fallback: bool = False


# ---------------------------------------------------------------------------
# GMM Regime Detector
# ---------------------------------------------------------------------------

class GMMRegimeDetector:
    """Gaussian Mixture Model regime detector.

    Provides ``detect_regime()`` compatible with the existing
    :class:`MarketRegimeDetector` interface while internally using a trained
    GMM to classify market conditions.

    Fallback behaviour:
    - No trained model → use ADX detector
    - Prediction confidence < threshold → use ADX detector
    - GMM training failure → use ADX detector

    Example::

        detector = GMMRegimeDetector()
        regime = detector.detect_regime(market_data)  # MarketRegime enum
    """

    def __init__(self, config: Optional[GMMConfig] = None) -> None:
        """Initialise the GMM regime detector.

        Args:
            config: Optional configuration. Uses defaults if ``None``.
        """
        self.config = config or GMMConfig()
        self._feature_extractor = FeatureExtractor()
        self._model_manager = ModelManager()

        # Lazily loaded trained model
        self._gmm_model = None
        self._cluster_to_latent: Dict[int, _LatentRegime] = {}
        self._model_version: Optional[str] = None
        self._feature_means: Optional[np.ndarray] = None
        self._feature_stds: Optional[np.ndarray] = None

        # ADX fallback detector (imported lazily to avoid circular deps)
        self._adx_detector = None

        logger.info(
            f"GMMRegimeDetector initialised: n_regimes={self.config.n_regimes}, "
            f"covariance={self.config.covariance_type}, "
            f"confidence_threshold={self.config.confidence_threshold}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_regime(self, market_data: Dict[str, List[float]]) -> MarketRegime:
        """Detect current market regime from OHLCV data.

        This is the primary entry point and matches the
        :class:`MarketRegimeDetector.detect_regime()` signature.

        Args:
            market_data: Dict with keys ``'high'``, ``'low'``, ``'close'``,
                         and optionally ``'volume'``.

        Returns:
            :class:`MarketRegime` enum value.
        """
        result = self.predict(market_data)
        return result.system_regime

    def predict(self, market_data: Dict[str, List[float]]) -> GMMRegimeResult:
        """Predict regime with full result details (confidence, fallback info).

        Args:
            market_data: Dict with keys ``'high'``, ``'low'``, ``'close'``,
                         and optionally ``'volume'``.

        Returns:
            :class:`GMMRegimeResult` with regime, confidence, and metadata.
        """
        # Attempt GMM prediction
        if self._gmm_model is None:
            self._try_load_model()

        if self._gmm_model is not None:
            try:
                return self._predict_with_gmm(market_data)
            except Exception as exc:
                logger.warning(f"GMM prediction failed, falling back to ADX: {exc}")

        # Fallback to ADX
        return self._adx_fallback(market_data)

    def train(
        self,
        closes: List[float],
        highs: Optional[List[float]] = None,
        lows: Optional[List[float]] = None,
        volumes: Optional[List[float]] = None,
        auto_save: bool = True,
    ) -> bool:
        """Train the GMM model on historical OHLCV data.

        Args:
            closes: Full closing price history (oldest first).
            highs: Full high price history.
            lows: Full low price history.
            volumes: Full volume history.
            auto_save: If ``True``, persist the model after training.

        Returns:
            ``True`` if training succeeded, ``False`` otherwise.
        """
        try:
            import sklearn.mixture  # noqa: F401
        except ImportError:
            logger.error("scikit-learn is not installed. Cannot train GMM.")
            return False

        n_candles = len(closes)
        if n_candles < self.config.min_train_candles:
            logger.warning(
                f"Insufficient data for training: {n_candles} candles "
                f"(need {self.config.min_train_candles})"
            )
            return False

        logger.info(
            f"Starting GMM training: {n_candles} candles, "
            f"n_regimes={self.config.n_regimes}"
        )

        try:
            # Extract batch features
            batch_features = self._feature_extractor.extract_batch(
                closes, highs, lows, volumes
            )

            if len(batch_features) < self.config.n_regimes * 10:
                logger.warning(
                    f"Too few feature vectors for reliable training: "
                    f"{len(batch_features)}"
                )
                return False

            # Build feature matrix
            X = np.array([f.to_array() for f in batch_features], dtype=np.float64)

            # Handle any NaN/Inf
            X = np.nan_to_num(X, nan=0.0, posinf=10.0, neginf=-10.0)

            # Standardise features for better GMM convergence
            self._feature_means = np.mean(X, axis=0)
            self._feature_stds = np.std(X, axis=0)
            self._feature_stds[self._feature_stds == 0.0] = 1.0  # avoid div-by-zero
            X_scaled = (X - self._feature_means) / self._feature_stds

            # Fit GMM
            from sklearn.mixture import GaussianMixture

            gmm = GaussianMixture(
                n_components=self.config.n_regimes,
                covariance_type=self.config.covariance_type,
                n_init=10,
                max_iter=300,
                random_state=42,
            )
            gmm.fit(X_scaled)

            # Assign latent regime labels based on cluster characteristics
            self._cluster_to_latent = self._assign_cluster_labels(gmm, X_scaled)

            self._gmm_model = gmm
            self._model_version = self._make_version_tag()

            # Compute training statistics
            labels = gmm.predict(X_scaled)
            label_counts = {i: int(np.sum(labels == i)) for i in range(self.config.n_regimes)}
            avg_log_likelihood = float(gmm.score(X_scaled))

            logger.info(
                f"GMM training complete: {len(batch_features)} samples, "
                f"log_likelihood={avg_log_likelihood:.4f}, "
                f"cluster_sizes={label_counts}"
            )

            # Persist
            if auto_save:
                self._model_manager.save_model(
                    gmm_model=gmm,
                    config=self.config,
                    cluster_to_latent=self._cluster_to_latent,
                    feature_means=self._feature_means,
                    feature_stds=self._feature_stds,
                    training_samples=len(batch_features),
                    log_likelihood=avg_log_likelihood,
                )

            return True

        except Exception as exc:
            logger.error(f"GMM training failed: {exc}")
            return False

    def is_model_available(self) -> bool:
        """Check if a trained GMM model is loaded and ready for prediction."""
        return self._gmm_model is not None

    def get_model_info(self) -> Dict[str, object]:
        """Return metadata about the currently loaded model.

        Returns:
            Dictionary with model version, training date, cluster mapping, etc.
        """
        info: Dict[str, object] = {
            "model_loaded": self._gmm_model is not None,
            "model_version": self._model_version,
            "n_regimes": self.config.n_regimes,
            "confidence_threshold": self.config.confidence_threshold,
        }
        if self._gmm_model is not None:
            info["cluster_sizes"] = dict(self._gmm_model.counts_) if hasattr(self._gmm_model, "counts_") else {}
            info["converged"] = self._gmm_model.converged_
            info["n_features"] = self._gmm_model.n_features_in_
        return info

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _try_load_model(self) -> None:
        """Attempt to load a persisted GMM model from disk."""
        try:
            result = self._model_manager.load_model()
            if result is None:
                logger.debug("No persisted GMM model found on disk")
                return

            (
                gmm,
                config,
                cluster_to_latent,
                feature_means,
                feature_stds,
                version,
            ) = result

            self._gmm_model = gmm
            self._config = config
            self._cluster_to_latent = cluster_to_latent
            self._feature_means = feature_means
            self._feature_stds = feature_stds
            self._model_version = version

            logger.info(f"Loaded persisted GMM model: version={version}")

        except Exception as exc:
            logger.warning(f"Failed to load persisted GMM model: {exc}")

    def _predict_with_gmm(
        self, market_data: Dict[str, List[float]]
    ) -> GMMRegimeResult:
        """Run GMM inference on a single market data snapshot."""
        closes = market_data["close"]
        highs = market_data.get("high")
        lows = market_data.get("low")
        volumes = market_data.get("volume")

        features = self._feature_extractor.extract(closes, highs, lows, volumes)
        X = features.to_array().reshape(1, -1)

        # Handle NaN/Inf
        X = np.nan_to_num(X, nan=0.0, posinf=10.0, neginf=-10.0)

        # Standardise with training statistics
        if self._feature_means is not None and self._feature_stds is not None:
            X = (X - self._feature_means) / self._feature_stds

        # Predict
        probs = self._gmm_model.predict_proba(X)[0]
        cluster_idx = int(np.argmax(probs))
        confidence = float(probs[cluster_idx])

        # Map latent regime
        latent = self._cluster_to_latent.get(cluster_idx, _LatentRegime.RANGING)
        system_regime = _DEFAULT_REGIME_MAP.get(latent, MarketRegime.INDECISIVE)

        # Refine: if ADX is very high, upgrade to TRENDING_STRONG
        adx = self._quick_adx(market_data)
        if adx is not None and adx > 30 and latent == _LatentRegime.TRENDING:
            system_regime = MarketRegime.TRENDING_STRONG

        prob_dict = {
            self._cluster_to_latent.get(i, _LatentRegime.RANGING).value: float(probs[i])
            for i in range(len(probs))
        }

        # Check confidence threshold
        if confidence < self.config.confidence_threshold:
            logger.debug(
                f"GMM confidence {confidence:.3f} < threshold "
                f"{self.config.confidence_threshold} — using ADX fallback"
            )
            return self._adx_fallback(market_data)

        logger.debug(
            f"GMM prediction: cluster={cluster_idx}, latent={latent.value}, "
            f"system={system_regime.value}, confidence={confidence:.3f}, "
            f"probs={prob_dict}"
        )

        return GMMRegimeResult(
            system_regime=system_regime,
            latent_regime=latent,
            confidence=confidence,
            probabilities=prob_dict,
        )

    def _adx_fallback(self, market_data: Dict[str, List[float]]) -> GMMRegimeResult:
        """Fall back to ADX-based regime detection."""
        try:
            from ..market_regime import MarketRegimeDetector

            if self._adx_detector is None:
                self._adx_detector = MarketRegimeDetector()

            regime = self._adx_detector.detect_regime(market_data)

            return GMMRegimeResult(
                system_regime=regime,
                latent_regime=_LatentRegime.RANGING,  # neutral
                confidence=1.0,
                probabilities={"adx_fallback": 1.0},
                used_fallback=True,
            )
        except Exception as exc:
            logger.error(f"ADX fallback also failed: {exc}")
            return GMMRegimeResult(
                system_regime=MarketRegime.INDECISIVE,
                latent_regime=_LatentRegime.RANGING,
                confidence=0.0,
                probabilities={},
                used_fallback=True,
            )

    def _quick_adx(self, market_data: Dict[str, List[float]]) -> Optional[float]:
        """Quickly compute ADX for regime refinement without full detection."""
        try:
            from ..indicators import calculate_adx

            highs = market_data.get("high", [])
            lows = market_data.get("low", [])
            closes = market_data.get("close", [])

            if len(closes) < 30:
                return None

            return calculate_adx(highs, lows, closes, period=14)
        except Exception:
            return None

    def _assign_cluster_labels(
        self, gmm, X: np.ndarray
    ) -> Dict[int, _LatentRegime]:
        """Assign human-readable latent regime labels to GMM cluster indices.

        Strategy: rank clusters by their mean feature vectors:
        - Highest volatility/ATR_ratio → VOLATILE
        - Highest returns (absolute) + lowest BB_width → TRENDING
        - Remaining → RANGING
        """
        cluster_centers = gmm.means_  # shape: (n_clusters, n_features)
        n_clusters = cluster_centers.shape[0]

        if n_clusters == 1:
            return {0: _LatentRegime.RANGING}

        # Feature indices: 0=vol, 1=ret, 2=skew, 3=atr_ratio, 4=vol_ratio, 5=bb_width
        vol_scores = cluster_centers[:, 0]  # volatility
        atr_scores = cluster_centers[:, 3]  # atr_ratio
        ret_scores = np.abs(cluster_centers[:, 1])  # abs(returns) for trend strength
        bb_scores = cluster_centers[:, 5]  # bb_width

        # Combined "turbulence" score → highest is VOLATILE
        turbulence = vol_scores + atr_scores + bb_scores
        # Combined "trend" score → highest is TRENDING
        trendiness = ret_scores - vol_scores  # high returns, low vol = trend

        volatile_cluster = int(np.argmax(turbulence))
        trending_cluster = int(np.argmax(trendiness))

        mapping: Dict[int, _LatentRegime] = {}
        mapping[volatile_cluster] = _LatentRegime.VOLATILE

        # If trending == volatile (single-cluster edge case), pick next best
        if trending_cluster == volatile_cluster and n_clusters > 1:
            trendiness_copy = trendiness.copy()
            trendiness_copy[volatile_cluster] = -np.inf
            trending_cluster = int(np.argmax(trendiness_copy))

        mapping[trending_cluster] = _LatentRegime.TRENDING

        # Remaining clusters → RANGING
        for idx in range(n_clusters):
            if idx not in mapping:
                mapping[idx] = _LatentRegime.RANGING

        logger.debug(
            f"Cluster labels assigned: "
            f"{', '.join(f'c{i}={mapping[i].value}' for i in range(n_clusters))}"
        )
        return mapping

    @staticmethod
    def _make_version_tag() -> str:
        """Generate a version tag from the current timestamp."""
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).strftime("gmm_%Y%m%d_%H%M%S")
