"""
HMM-Based Market Regime Detector

Uses a Gaussian Hidden Markov Model (hmmlearn) over the same 6 statistical
features as the GMM detector. The key difference from the GMM is the learned
transition matrix: high self-transition probabilities encode regime
persistence (hysteresis) directly in the model, so classification is done
from the FORWARD-FILTERED posterior over a recent feature window rather
than from a lone frame.

Public interface mirrors :class:`GMMRegimeDetector` (train / predict /
detect_regime / save-load semantics) and reuses :class:`GMMRegimeResult`
so downstream code does not care which implementation is active.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
from loguru import logger

from ..market_regime import MarketRegime
from .feature_engineering import FeatureExtractor
from .gmm_regime import (
    GMMRegimeResult,
    _DEFAULT_REGIME_MAP,
    _LatentRegime,
    assign_cluster_labels,
)
from .model_manager import ModelManager


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class HMMConfig:
    """Configuration for the HMM regime detector.

    Attributes:
        n_states: Number of hidden states (latent regimes).
        covariance_type: Emission covariance structure.
        confidence_threshold: Minimum filtered posterior probability to
            trust the HMM. Below this threshold the system falls back to
            ADX detection (when fallback is allowed).
        n_iter: Maximum EM iterations during training.
        window_frames: Number of recent feature frames the forward filter
            runs over at predict time.
        min_train_candles: Minimum candles required before training.
        max_train_candles: Maximum candles used for training.
    """

    n_states: int = 3
    covariance_type: str = "full"
    confidence_threshold: float = 0.6
    n_iter: int = 200
    window_frames: int = 64
    min_train_candles: int = 2_000
    max_train_candles: int = 50_000

    @property
    def n_regimes(self) -> int:
        """Alias so ModelManager metadata handling works for both configs."""
        return self.n_states


# ---------------------------------------------------------------------------
# HMM Regime Detector
# ---------------------------------------------------------------------------


class HMMRegimeDetector:
    """Gaussian HMM regime detector with forward-filtered classification.

    Fallback behaviour matches :class:`GMMRegimeDetector`:
    - No trained model -> ADX detector
    - Filtered posterior confidence < threshold -> ADX detector
    - Training failure -> ADX detector

    Example::

        detector = HMMRegimeDetector()
        regime = detector.detect_regime(market_data)  # MarketRegime enum
    """

    MODEL_FILE_PREFIX = "hmm_regime"
    LATEST_MARKER = "LATEST_HMM"

    def __init__(self, config: Optional[HMMConfig] = None) -> None:
        """Initialise the HMM regime detector.

        Args:
            config: Optional configuration. Uses defaults if ``None``.
        """
        self.config = config or HMMConfig()
        self._feature_extractor = FeatureExtractor()
        self._model_manager = ModelManager(
            file_prefix=self.MODEL_FILE_PREFIX,
            latest_marker=self.LATEST_MARKER,
        )

        # Lazily loaded trained model
        self._hmm_model = None
        self._state_to_latent: Dict[int, _LatentRegime] = {}
        self._model_version: Optional[str] = None
        self._feature_means: Optional[np.ndarray] = None
        self._feature_stds: Optional[np.ndarray] = None
        self._load_attempted = False

        # ADX fallback detector (imported lazily to avoid circular deps)
        self._adx_detector = None

        logger.info(
            f"HMMRegimeDetector initialised: n_states={self.config.n_states}, "
            f"covariance={self.config.covariance_type}, "
            f"confidence_threshold={self.config.confidence_threshold}"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_regime(self, market_data: Dict[str, List[float]]) -> MarketRegime:
        """Detect current market regime from OHLCV data.

        Args:
            market_data: Dict with keys ``'high'``, ``'low'``, ``'close'``,
                         and optionally ``'volume'``.

        Returns:
            :class:`MarketRegime` enum value.
        """
        result = self.predict(market_data)
        return result.system_regime

    def predict(
        self,
        market_data: Dict[str, List[float]],
        allow_fallback: bool = True,
    ) -> GMMRegimeResult:
        """Predict regime from the forward-filtered posterior.

        Args:
            market_data: Dict with keys ``'high'``, ``'low'``, ``'close'``,
                         and optionally ``'volume'``.
            allow_fallback: When ``True`` (default) low confidence, a missing
                model, or a prediction error falls back to the ADX detector.
                When ``False`` (shadow-mode observation) the raw ML result is
                always returned, and a missing model or prediction error
                raises instead.

        Returns:
            :class:`GMMRegimeResult` with regime, confidence, and metadata.

        Raises:
            RuntimeError: If ``allow_fallback`` is ``False`` and no trained
                model is available.
        """
        if not self.ensure_model_loaded():
            if not allow_fallback:
                raise RuntimeError("No trained HMM model available for prediction")
            return self._adx_fallback(market_data)

        if not allow_fallback:
            return self._predict_with_hmm(market_data, allow_fallback=False)

        try:
            return self._predict_with_hmm(market_data)
        except Exception as exc:
            logger.warning(f"HMM prediction failed, falling back to ADX: {exc}")
            return self._adx_fallback(market_data)

    def train(
        self,
        closes: List[float],
        highs: Optional[List[float]] = None,
        lows: Optional[List[float]] = None,
        volumes: Optional[List[float]] = None,
        auto_save: bool = True,
    ) -> bool:
        """Train the HMM on historical OHLCV data.

        Args:
            closes: Full closing price history (oldest first).
            highs: Full high price history.
            lows: Full low price history.
            volumes: Full volume history.
            auto_save: If ``True``, persist the model after training.

        Returns:
            ``True`` if training succeeded, ``False`` otherwise.
        """
        n_candles = len(closes)
        if n_candles < self.config.min_train_candles:
            logger.warning(
                f"Insufficient data for training: {n_candles} candles "
                f"(need {self.config.min_train_candles})"
            )
            return False

        logger.info(
            f"Starting HMM training: {n_candles} candles, "
            f"n_states={self.config.n_states}"
        )

        try:
            batch_features = self._feature_extractor.extract_batch(
                closes, highs, lows, volumes
            )
            X = np.array([f.to_array() for f in batch_features], dtype=np.float64)
            summary = self.train_on_features(X, auto_save=auto_save)
            return summary is not None
        except Exception as exc:
            logger.error(f"HMM training failed: {exc}")
            return False

    def train_on_features(
        self,
        X: np.ndarray,
        lengths: Optional[List[int]] = None,
        auto_save: bool = True,
    ) -> Optional[Dict[str, object]]:
        """Fit the HMM on a prebuilt feature matrix.

        Args:
            X: Feature matrix of shape ``(n_samples, 6)`` in raw feature
                units. May be a concatenation of several per-symbol
                sequences; pass ``lengths`` so EM never learns transitions
                across sequence boundaries.
            lengths: Per-sequence sample counts (must sum to ``len(X)``).
                ``None`` treats ``X`` as a single sequence.
            auto_save: If ``True``, persist the model after training.

        Returns:
            Summary dict with ``version``, ``converged``,
            ``log_likelihood``, ``state_sizes``, ``state_to_latent``,
            ``state_means_raw``, and ``transmat`` on success; ``None`` on
            failure.
        """
        try:
            from hmmlearn.hmm import GaussianHMM
        except ImportError:
            logger.error("hmmlearn is not installed. Cannot train HMM.")
            return None

        X = np.asarray(X, dtype=np.float64)
        X = np.nan_to_num(X, nan=0.0, posinf=10.0, neginf=-10.0)

        if len(X) < self.config.n_states * 10:
            logger.warning(f"Too few feature vectors for reliable training: {len(X)}")
            return None

        # Standardise features (same convention as the GMM detector)
        self._feature_means = np.mean(X, axis=0)
        self._feature_stds = np.std(X, axis=0)
        self._feature_stds[self._feature_stds == 0.0] = 1.0
        X_scaled = (X - self._feature_means) / self._feature_stds

        model = GaussianHMM(
            n_components=self.config.n_states,
            covariance_type=self.config.covariance_type,
            n_iter=self.config.n_iter,
            random_state=42,
        )
        model.fit(X_scaled, lengths=lengths)

        self._state_to_latent = assign_cluster_labels(model.means_)
        self._hmm_model = model

        states = model.predict(X_scaled, lengths=lengths)
        state_sizes = {i: int(np.sum(states == i)) for i in range(self.config.n_states)}
        avg_log_likelihood = float(
            model.score(X_scaled, lengths=lengths) / len(X_scaled)
        )
        converged = bool(model.monitor_.converged)
        transmat = np.array(model.transmat_, dtype=np.float64)

        state_means_raw = model.means_ * self._feature_stds + self._feature_means

        logger.info(
            f"HMM training complete: {len(X)} samples, "
            f"avg_log_likelihood={avg_log_likelihood:.4f}, "
            f"state_sizes={state_sizes}, "
            f"self_transitions={np.diag(transmat).round(4).tolist()}"
        )

        if auto_save:
            self._model_version = self._model_manager.save_model(
                gmm_model=model,
                config=self.config,
                cluster_to_latent=self._state_to_latent,
                feature_means=self._feature_means,
                feature_stds=self._feature_stds,
                training_samples=len(X),
                log_likelihood=avg_log_likelihood,
                model_type="hmm",
                extra_payload={"transmat": transmat},
            )

        return {
            "version": self._model_version,
            "converged": converged,
            "log_likelihood": avg_log_likelihood,
            "state_sizes": state_sizes,
            "state_to_latent": {
                idx: latent.value for idx, latent in self._state_to_latent.items()
            },
            "state_means_raw": state_means_raw,
            "transmat": transmat,
        }

    def is_model_available(self) -> bool:
        """Check if a trained HMM model is loaded and ready for prediction."""
        return self._hmm_model is not None

    def ensure_model_loaded(self) -> bool:
        """Load the persisted model if not already loaded.

        Returns:
            ``True`` when a trained model is loaded and ready.
        """
        if self._hmm_model is None and not self._load_attempted:
            self._try_load_model()
        return self._hmm_model is not None

    def get_model_info(self) -> Dict[str, object]:
        """Return metadata about the currently loaded model."""
        info: Dict[str, object] = {
            "model_loaded": self._hmm_model is not None,
            "model_version": self._model_version,
            "n_states": self.config.n_states,
            "confidence_threshold": self.config.confidence_threshold,
        }
        if self._hmm_model is not None:
            info["self_transitions"] = (
                np.diag(self._hmm_model.transmat_).round(4).tolist()
            )
        return info

    def predict_from_features(self, features: np.ndarray) -> GMMRegimeResult:
        """Classify from a prebuilt (raw-unit) feature window.

        Runs the forward filter over the whole window (via
        ``predict_proba`` on the sequence) and classifies from the LAST
        row's posterior, so the learned transition matrix smooths the
        classification. No confidence-threshold fallback and no ADX
        refinement is applied.

        Args:
            features: Array of shape ``(n_frames, 6)`` (or ``(6,)``) in raw
                feature units, oldest frame first.

        Returns:
            :class:`GMMRegimeResult` for the final frame.

        Raises:
            RuntimeError: If no trained model is loaded.
        """
        if not self.ensure_model_loaded():
            raise RuntimeError("No trained HMM model available for prediction")

        X = np.asarray(features, dtype=np.float64)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        X = np.nan_to_num(X, nan=0.0, posinf=10.0, neginf=-10.0)
        X = X[-self.config.window_frames :]

        if self._feature_means is not None and self._feature_stds is not None:
            X = (X - self._feature_means) / self._feature_stds

        posteriors = self._hmm_model.predict_proba(X)
        probs = posteriors[-1]
        state_idx = int(np.argmax(probs))
        confidence = float(probs[state_idx])

        latent = self._state_to_latent.get(state_idx, _LatentRegime.RANGING)
        system_regime = _DEFAULT_REGIME_MAP.get(latent, MarketRegime.INDECISIVE)

        prob_dict = {
            self._state_to_latent.get(i, _LatentRegime.RANGING).value: float(probs[i])
            for i in range(len(probs))
        }

        return GMMRegimeResult(
            system_regime=system_regime,
            latent_regime=latent,
            confidence=confidence,
            probabilities=prob_dict,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _try_load_model(self) -> None:
        """Attempt to load a persisted HMM model from disk."""
        self._load_attempted = True
        try:
            result = self._model_manager.load_model()
            if result is None:
                logger.debug("No persisted HMM model found on disk")
                return

            (
                model,
                config,
                state_to_latent,
                feature_means,
                feature_stds,
                version,
            ) = result

            self._hmm_model = model
            # Canonical config attribute: the loaded model's training-time
            # config replaces the constructor default (same convention as
            # the GMM detector after the config-attribute bug fix).
            if config is not None:
                self.config = config
            self._state_to_latent = state_to_latent
            self._feature_means = feature_means
            self._feature_stds = feature_stds
            self._model_version = version

            logger.info(f"Loaded persisted HMM model: version={version}")

        except Exception as exc:
            logger.warning(f"Failed to load persisted HMM model: {exc}")

    def _predict_with_hmm(
        self,
        market_data: Dict[str, List[float]],
        allow_fallback: bool = True,
    ) -> GMMRegimeResult:
        """Run forward-filtered HMM inference on a market data snapshot."""
        closes = market_data["close"]
        highs = market_data.get("high")
        lows = market_data.get("low")
        volumes = market_data.get("volume")

        batch = self._feature_extractor.extract_batch(closes, highs, lows, volumes)
        if not batch:
            raise ValueError(
                f"Insufficient data for HMM feature extraction ({len(closes)} candles)"
            )

        X = np.array([f.to_array() for f in batch], dtype=np.float64)
        result = self.predict_from_features(X)

        # Refine: if ADX is very high, upgrade TRENDING to TRENDING_STRONG
        # (same refinement rule as the GMM detector).
        adx = self._quick_adx(market_data)
        if (
            adx is not None
            and adx > 30
            and result.latent_regime == _LatentRegime.TRENDING
        ):
            result = GMMRegimeResult(
                system_regime=MarketRegime.TRENDING_STRONG,
                latent_regime=result.latent_regime,
                confidence=result.confidence,
                probabilities=result.probabilities,
            )

        if allow_fallback and result.confidence < self.config.confidence_threshold:
            logger.debug(
                f"HMM confidence {result.confidence:.3f} < threshold "
                f"{self.config.confidence_threshold} - using ADX fallback"
            )
            return self._adx_fallback(market_data)

        return result

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
