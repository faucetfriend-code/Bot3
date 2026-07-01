"""
Model Manager for GMM Regime Detection

Handles persistence, versioning, and retrieval of trained GMM models
using joblib for serialisation.  Models are stored under the
``trading_bot_v2/ml/models/`` directory with timestamped filenames.

Features:
- Save/load with joblib compression
- Automatic versioning via timestamps
- Model metadata (training date, accuracy, features used)
- Cleanup of old model versions
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import joblib
from loguru import logger


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODELS_DIR = Path(__file__).parent / "models"
MODEL_FILE_PREFIX = "gmm_regime"
MODEL_FILE_SUFFIX = ".joblib"
METADATA_SUFFIX = ".meta.json"


# ---------------------------------------------------------------------------
# Model metadata
# ---------------------------------------------------------------------------

@dataclass
class ModelMetadata:
    """Metadata stored alongside each persisted GMM model.

    Attributes:
        version: Unique version tag (e.g. ``gmm_20260630_120000``).
        training_date: ISO-8601 UTC timestamp of training.
        training_samples: Number of feature vectors used for training.
        log_likelihood: Average log-likelihood on training data.
        n_regimes: Number of GMM components.
        covariance_type: GMM covariance structure used.
        features: Names of the features used for training.
    """

    version: str
    training_date: str
    training_samples: int
    log_likelihood: float
    n_regimes: int
    covariance_type: str
    features: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to a JSON-compatible dictionary."""
        return {
            "version": self.version,
            "training_date": self.training_date,
            "training_samples": self.training_samples,
            "log_likelihood": self.log_likelihood,
            "n_regimes": self.n_regimes,
            "covariance_type": self.covariance_type,
            "features": self.features,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelMetadata":
        """Deserialise from a dictionary."""
        return cls(
            version=data["version"],
            training_date=data["training_date"],
            training_samples=data["training_samples"],
            log_likelihood=data["log_likelihood"],
            n_regimes=data["n_regimes"],
            covariance_type=data["covariance_type"],
            features=data.get("features", []),
        )


# ---------------------------------------------------------------------------
# Model manager
# ---------------------------------------------------------------------------

class ModelManager:
    """Manages persistence and versioning of GMM regime detection models.

    Models are stored as ``<models_dir>/gmm_regime_<version>.joblib`` with a
    companion ``.meta.json`` file for lightweight metadata queries.

    Example::

        manager = ModelManager()
        manager.save_model(gmm_model, config, ...)
        result = manager.load_model()
        if result is not None:
            gmm, config, cluster_map, means, stds, version = result
    """

    def __init__(self, models_dir: Optional[Path] = None) -> None:
        """Initialise the model manager.

        Args:
            models_dir: Directory to store model files. Defaults to
                        ``trading_bot_v2/ml/models/``.
        """
        self.models_dir = models_dir or MODELS_DIR
        self.models_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"ModelManager initialised: models_dir={self.models_dir}")

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def save_model(
        self,
        gmm_model: Any,
        config: Any,
        cluster_to_latent: Dict[int, Any],
        feature_means: Any,
        feature_stds: Any,
        training_samples: int,
        log_likelihood: float,
    ) -> str:
        """Persist a trained GMM model and its associated metadata.

        Args:
            gmm_model: Trained ``GaussianMixture`` instance.
            config: :class:`GMMConfig` used during training.
            cluster_to_latent: Mapping from cluster index to latent regime.
            feature_means: Training feature means for standardisation.
            feature_stds: Training feature stds for standardisation.
            training_samples: Number of samples used for training.
            log_likelihood: Average log-likelihood on training data.

        Returns:
            The version tag assigned to this model.

        Raises:
            OSError: If the file cannot be written.
        """
        from datetime import datetime, timezone

        version = datetime.now(timezone.utc).strftime("gmm_%Y%m%d_%H%M%S")

        # Build the model payload
        payload = {
            "gmm_model": gmm_model,
            "config": config,
            "cluster_to_latent": cluster_to_latent,
            "feature_means": feature_means,
            "feature_stds": feature_stds,
            "version": version,
        }

        # Write model file
        model_path = self.models_dir / f"{MODEL_FILE_PREFIX}_{version}{MODEL_FILE_SUFFIX}"
        joblib.dump(payload, model_path, compress=3)
        logger.info(f"GMM model saved: {model_path}")

        # Write metadata file
        metadata = ModelMetadata(
            version=version,
            training_date=datetime.now(timezone.utc).isoformat(),
            training_samples=training_samples,
            log_likelihood=log_likelihood,
            n_regimes=config.n_regimes,
            covariance_type=config.covariance_type,
            features=[
                "volatility",
                "returns",
                "skewness",
                "atr_ratio",
                "volume_ratio",
                "bb_width",
            ],
        )
        meta_path = self.models_dir / f"{MODEL_FILE_PREFIX}_{version}{METADATA_SUFFIX}"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata.to_dict(), f, indent=2)
        logger.debug(f"Model metadata saved: {meta_path}")

        # Write latest symlink/file
        self._write_latest(version)

        # Prune old models
        self._prune_old_models(keep=5)

        return version

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load_model(
        self, version: Optional[str] = None
    ) -> Optional[Tuple]:
        """Load a persisted GMM model.

        Args:
            version: Specific version tag to load. If ``None``, loads the
                     most recently saved model.

        Returns:
            Tuple of ``(gmm_model, config, cluster_to_latent, feature_means,
            feature_stds, version)`` or ``None`` if no model is found.
        """
        if version is None:
            version = self._read_latest()

        if version is None:
            logger.debug("No model version found")
            return None

        model_path = self.models_dir / f"{MODEL_FILE_PREFIX}_{version}{MODEL_FILE_SUFFIX}"

        if not model_path.exists():
            logger.warning(f"Model file not found: {model_path}")
            return None

        try:
            payload = joblib.load(model_path)

            result = (
                payload["gmm_model"],
                payload["config"],
                payload["cluster_to_latent"],
                payload["feature_means"],
                payload["feature_stds"],
                payload["version"],
            )

            logger.info(f"GMM model loaded: version={version}")
            return result

        except Exception as exc:
            logger.error(f"Failed to load GMM model {version}: {exc}")
            return None

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_metadata(self, version: Optional[str] = None) -> Optional[ModelMetadata]:
        """Load metadata for a model version without loading the full model.

        Args:
            version: Version tag. If ``None``, uses the latest version.

        Returns:
            :class:`ModelMetadata` or ``None`` if not found.
        """
        if version is None:
            version = self._read_latest()

        if version is None:
            return None

        meta_path = self.models_dir / f"{MODEL_FILE_PREFIX}_{version}{METADATA_SUFFIX}"

        if not meta_path.exists():
            return None

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return ModelMetadata.from_dict(data)
        except Exception as exc:
            logger.warning(f"Failed to load metadata for {version}: {exc}")
            return None

    def list_versions(self) -> List[str]:
        """List all persisted model versions (newest first).

        Returns:
            Sorted list of version strings.
        """
        versions: List[str] = []
        for path in self.models_dir.glob(f"{MODEL_FILE_PREFIX}_*{MODEL_FILE_SUFFIX}"):
            name = path.stem  # e.g. gmm_20260630_120000
            version = name.replace(f"{MODEL_FILE_PREFIX}_", "")
            if version:
                versions.append(version)

        versions.sort(reverse=True)
        return versions

    def delete_version(self, version: str) -> bool:
        """Delete a specific model version.

        Args:
            version: Version tag to delete.

        Returns:
            ``True`` if deleted, ``False`` if not found.
        """
        model_path = self.models_dir / f"{MODEL_FILE_PREFIX}_{version}{MODEL_FILE_SUFFIX}"
        meta_path = self.models_dir / f"{MODEL_FILE_PREFIX}_{version}{METADATA_SUFFIX}"

        deleted = False
        if model_path.exists():
            model_path.unlink()
            deleted = True
        if meta_path.exists():
            meta_path.unlink()
            deleted = True

        if deleted:
            logger.info(f"Deleted model version: {version}")

        return deleted

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _write_latest(self, version: str) -> None:
        """Write the latest version marker file."""
        latest_path = self.models_dir / "LATEST"
        with open(latest_path, "w", encoding="utf-8") as f:
            f.write(version)
        logger.debug(f"Latest model marker updated: {version}")

    def _read_latest(self) -> Optional[str]:
        """Read the latest version marker file."""
        latest_path = self.models_dir / "LATEST"
        if not latest_path.exists():
            return None
        try:
            return latest_path.read_text(encoding="utf-8").strip()
        except Exception:
            return None

    def _prune_old_models(self, keep: int = 5) -> None:
        """Remove old model files, keeping the most recent ``keep`` versions."""
        versions = self.list_versions()

        if len(versions) <= keep:
            return

        to_remove = versions[keep:]
        for version in to_remove:
            self.delete_version(version)
            logger.debug(f"Pruned old model: {version}")
