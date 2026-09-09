"""
P3 ML regime tests: config-bug regression, trainer guardrails, HMM
smoothing, and shadow-mode wiring.

Covers:
- GMM loaded-model config regression (predict must honor the persisted
  confidence threshold, not the constructor default)
- Trainer refusal below the minimum candle count + synthetic-feature
  train/persist/reload round-trips for both detectors
- HMM forward-filtered classification flips strictly less than raw GMM
  frame classification on persistent synthetic data; label mapping sanity
- Shadow mode: writes a row at cache refresh when a model is present,
  skips gracefully when absent, never changes the ADX result, and
  swallows shadow exceptions
- regime_shadow DB round-trip + summary computation
"""

import csv
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from trading_bot_v2.database import summarize_regime_shadow
from trading_bot_v2.market_regime import MarketRegime, MarketRegimeDetector
from trading_bot_v2.ml.feature_engineering import FeatureExtractor
from trading_bot_v2.ml.gmm_regime import (
    GMMConfig,
    GMMRegimeDetector,
    GMMRegimeResult,
    _LatentRegime,
    assign_cluster_labels,
)
from trading_bot_v2.ml.hmm_regime import HMMConfig, HMMRegimeDetector
from trading_bot_v2.ml.model_manager import (
    ModelManager,
    read_latest_model_type,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_price_series(n: int = 300, seed: int = 7):
    """Build a synthetic OHLCV dict long enough for feature extraction."""
    rng = np.random.default_rng(seed)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n)))
    highs = closes * (1.0 + np.abs(rng.normal(0.0, 0.004, n)))
    lows = closes * (1.0 - np.abs(rng.normal(0.0, 0.004, n)))
    volumes = np.abs(rng.normal(1000.0, 200.0, n))
    return {
        "close": closes.tolist(),
        "high": highs.tolist(),
        "low": lows.tolist(),
        "volume": volumes.tolist(),
    }


def _synthetic_two_cluster_features(
    n_segments: int = 8, seg_len: int = 60, seed: int = 3
) -> np.ndarray:
    """Two overlapping 6-dim feature clusters with temporal persistence."""
    rng = np.random.default_rng(seed)
    mean_a = np.zeros(6)
    mean_b = np.array([0.9, 0.0, 0.0, 0.9, 0.3, 0.9])
    rows = []
    for seg in range(n_segments):
        mean = mean_a if seg % 2 == 0 else mean_b
        rows.append(rng.normal(mean, 1.0, size=(seg_len, 6)))
    return np.vstack(rows)


def _gmm_with_tmp_manager(tmp_path, config=None) -> GMMRegimeDetector:
    detector = GMMRegimeDetector(config)
    detector._model_manager = ModelManager(models_dir=Path(tmp_path))
    return detector


def _hmm_with_tmp_manager(tmp_path, config=None) -> HMMRegimeDetector:
    detector = HMMRegimeDetector(config)
    detector._model_manager = ModelManager(
        models_dir=Path(tmp_path),
        file_prefix=HMMRegimeDetector.MODEL_FILE_PREFIX,
        latest_marker=HMMRegimeDetector.LATEST_MARKER,
    )
    return detector


@pytest.fixture
def market_data():
    return _make_price_series()


@pytest.fixture
def temp_db():
    """Point trading_bot_v2.database at a temporary SQLite file."""
    import trading_bot_v2.database as db_mod

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()

    original_env = os.environ.get("DATABASE_PATH")
    os.environ["DATABASE_PATH"] = tmp_path
    os.environ["DATABASE_BACKEND"] = "sqlite"

    original_backend = db_mod._active_backend
    original_path = db_mod.DATABASE_PATH
    original_pool = db_mod._connection_pool
    db_mod._active_backend = "sqlite"
    db_mod.DATABASE_PATH = tmp_path
    pool = db_mod.ConnectionPool(max_connections=2)
    db_mod._connection_pool = pool

    db_mod.init_database()

    yield db_mod

    pool.close_all()
    db_mod._active_backend = original_backend
    db_mod.DATABASE_PATH = original_path
    db_mod._connection_pool = original_pool
    if original_env is not None:
        os.environ["DATABASE_PATH"] = original_env
    elif "DATABASE_PATH" in os.environ:
        del os.environ["DATABASE_PATH"]
    try:
        os.unlink(tmp_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Task 1: loaded-model config regression
# ---------------------------------------------------------------------------


class TestLoadedConfigRegression:
    """A persisted model's config must be what predict-time code reads."""

    def test_loaded_config_replaces_constructor_default(self, tmp_path, market_data):
        extractor = FeatureExtractor()
        feats = extractor.extract_batch(
            market_data["close"],
            market_data["high"],
            market_data["low"],
            market_data["volume"],
        )
        X = np.array([f.to_array() for f in feats])

        # Train + save with a non-default confidence threshold
        trained = _gmm_with_tmp_manager(tmp_path, GMMConfig(confidence_threshold=2.0))
        assert trained.train_on_features(X, auto_save=True) is not None

        # Fresh detector with the DEFAULT config loads the artifact
        loaded = _gmm_with_tmp_manager(tmp_path)
        assert loaded.config.confidence_threshold == pytest.approx(0.6)
        assert loaded.ensure_model_loaded()

        # Regression: the loaded config is canonical (was silently ignored
        # via a stray self._config assignment before the fix).
        assert loaded.config.confidence_threshold == pytest.approx(2.0)

        # Behavioral check: confidence can never reach 2.0, so predict()
        # must take the ADX fallback path with the loaded threshold.
        result = loaded.predict(market_data)
        assert result.used_fallback is True

    def test_predict_without_fallback_returns_raw_result(self, tmp_path, market_data):
        extractor = FeatureExtractor()
        feats = extractor.extract_batch(
            market_data["close"],
            market_data["high"],
            market_data["low"],
            market_data["volume"],
        )
        X = np.array([f.to_array() for f in feats])

        trained = _gmm_with_tmp_manager(tmp_path, GMMConfig(confidence_threshold=2.0))
        assert trained.train_on_features(X, auto_save=True) is not None

        loaded = _gmm_with_tmp_manager(tmp_path)
        result = loaded.predict(market_data, allow_fallback=False)
        # Below-threshold confidence must NOT trigger fallback here
        assert result.used_fallback is False
        assert 0.0 <= result.confidence <= 1.0

    def test_predict_without_fallback_raises_when_no_model(self, tmp_path):
        detector = _gmm_with_tmp_manager(tmp_path)
        with pytest.raises(RuntimeError):
            detector.predict(_make_price_series(), allow_fallback=False)


# ---------------------------------------------------------------------------
# Task 2: trainer guardrails + round-trip
# ---------------------------------------------------------------------------


class TestTrainerPipeline:
    """Training pipeline guardrails and persist/reload round-trips."""

    def _write_candles_csv(self, data_dir: Path, symbol: str, n: int):
        path = data_dir / f"{symbol}_4h.csv"
        start = datetime(2024, 1, 1)
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            price = 100.0
            for i in range(n):
                ts = (start + timedelta(hours=4 * i)).isoformat()
                writer.writerow([ts, price, price * 1.01, price * 0.99, price, 1000.0])
        return path

    def test_refuses_insufficient_candles(self, tmp_path):
        from trading_bot_v2.ml.train_regime_model import run_training

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        self._write_candles_csv(data_dir, "TEST-USDC", 300)

        with pytest.raises(SystemExit):
            run_training(
                symbols=["TEST-USDC"],
                start="2024-01-01",
                end="2024-12-31",
                model="gmm",
                data_dir=str(data_dir),
                save=False,
            )

    def test_gmm_round_trip_on_synthetic_features(self, tmp_path):
        X = _synthetic_two_cluster_features()
        trained = _gmm_with_tmp_manager(tmp_path, GMMConfig(n_regimes=2))
        summary = trained.train_on_features(X, auto_save=True)
        assert summary is not None
        assert summary["version"] is not None

        loaded = _gmm_with_tmp_manager(tmp_path)
        assert loaded.ensure_model_loaded()
        assert loaded.config.n_regimes == 2
        assert loaded._model_version == summary["version"]

        result = loaded.predict_from_features(X[:5])
        assert isinstance(result, GMMRegimeResult)
        assert 0.0 <= result.confidence <= 1.0

    def test_hmm_round_trip_on_synthetic_features(self, tmp_path):
        pytest.importorskip("hmmlearn")
        X = _synthetic_two_cluster_features()
        trained = _hmm_with_tmp_manager(tmp_path, HMMConfig(n_states=2))
        summary = trained.train_on_features(X, lengths=[len(X)], auto_save=True)
        assert summary is not None
        transmat = np.asarray(summary["transmat"])
        assert transmat.shape == (2, 2)
        assert np.allclose(transmat.sum(axis=1), 1.0)

        loaded = _hmm_with_tmp_manager(tmp_path)
        assert loaded.ensure_model_loaded()
        assert loaded.config.n_states == 2
        result = loaded.predict_from_features(X[:80])
        assert isinstance(result, GMMRegimeResult)
        assert 0.0 <= result.confidence <= 1.0

    def test_hmm_save_updates_latest_model_type(self, tmp_path):
        pytest.importorskip("hmmlearn")
        X = _synthetic_two_cluster_features()

        gmm = _gmm_with_tmp_manager(tmp_path, GMMConfig(n_regimes=2))
        assert gmm.train_on_features(X, auto_save=True) is not None
        assert read_latest_model_type(Path(tmp_path)) == "gmm"

        hmm = _hmm_with_tmp_manager(tmp_path, HMMConfig(n_states=2))
        assert hmm.train_on_features(X, lengths=[len(X)], auto_save=True) is not None
        assert read_latest_model_type(Path(tmp_path)) == "hmm"


# ---------------------------------------------------------------------------
# Task 3: HMM smoothing + label mapping
# ---------------------------------------------------------------------------


class TestHMMSmoothing:
    """Filtered HMM classification is stickier than raw GMM frames."""

    def test_hmm_flips_strictly_less_than_gmm(self, tmp_path):
        pytest.importorskip("hmmlearn")
        X = _synthetic_two_cluster_features(n_segments=8, seg_len=60)

        gmm = _gmm_with_tmp_manager(tmp_path, GMMConfig(n_regimes=2))
        assert gmm.train_on_features(X, auto_save=False) is not None

        hmm = _hmm_with_tmp_manager(tmp_path, HMMConfig(n_states=2))
        assert hmm.train_on_features(X, lengths=[len(X)], auto_save=False) is not None

        warmup = 20  # let the forward filter accumulate evidence
        gmm_labels = []
        hmm_labels = []
        for t in range(warmup, len(X)):
            gmm_labels.append(gmm.predict_from_features(X[: t + 1]).latent_regime)
            hmm_labels.append(hmm.predict_from_features(X[: t + 1]).latent_regime)

        def count_flips(labels):
            return sum(1 for i in range(1, len(labels)) if labels[i] != labels[i - 1])

        gmm_flips = count_flips(gmm_labels)
        hmm_flips = count_flips(hmm_labels)

        # The data has 7 true segment changes; overlapping clusters make
        # frame-wise GMM flip far more often, while the HMM's learned
        # self-transition probabilities smooth the classification.
        assert hmm_flips < gmm_flips
        # Both must still track the alternation (not collapse to one label)
        assert len(set(hmm_labels)) > 1

    def test_label_mapping_sanity(self):
        # idx 0: calm/ranging, idx 1: turbulent, idx 2: strong directional
        means = np.array(
            [
                [0.1, 0.0, 0.0, 0.1, 0.0, 0.1],  # low everything -> RANGING
                [2.0, 0.1, 0.0, 2.0, 1.0, 2.0],  # high vol/atr/bb -> VOLATILE
                [-0.5, 1.5, 0.0, 0.2, 0.2, 0.3],  # high ret, low vol -> TRENDING
            ]
        )
        mapping = assign_cluster_labels(means)
        assert mapping[0] == _LatentRegime.RANGING
        assert mapping[1] == _LatentRegime.VOLATILE
        assert mapping[2] == _LatentRegime.TRENDING


# ---------------------------------------------------------------------------
# Task 4: shadow-mode wiring in MarketRegimeDetector
# ---------------------------------------------------------------------------


def _fake_ml_result(regime=MarketRegime.RANGING_CALM, confidence=0.91):
    return GMMRegimeResult(
        system_regime=regime,
        latent_regime=_LatentRegime.RANGING,
        confidence=confidence,
        probabilities={"ranging": confidence},
    )


class TestShadowMode:
    """Shadow observations at cache refresh; ADX always authoritative."""

    def _detector_with_shadow(self, shadow_detector, db=None):
        det = MarketRegimeDetector()
        det._db = db if db is not None else MagicMock()
        det._shadow_initialised = True
        det._shadow_detector = shadow_detector
        det._shadow_model_type = "hmm" if shadow_detector else None
        return det

    def _drive(self, det, regime, at, market_data, sym="SUI"):
        det._clock = lambda: at
        with patch.object(det, "detect_regime", return_value=regime):
            return det.detect_regime_cached(sym, market_data)

    def test_refresh_writes_shadow_row_agree(self, market_data, monkeypatch):
        monkeypatch.setenv("ML_REGIME_SHADOW", "true")
        fake_ml = MagicMock()
        fake_ml.predict.return_value = _fake_ml_result(MarketRegime.RANGING_CALM)
        det = self._detector_with_shadow(fake_ml)

        result = self._drive(
            det, MarketRegime.RANGING_CALM, datetime(2026, 1, 1), market_data
        )
        assert result == MarketRegime.RANGING_CALM

        assert det._db.save_regime_shadow.call_count == 1
        kwargs = det._db.save_regime_shadow.call_args[1]
        assert kwargs["symbol"] == "SUI"
        assert kwargs["adx_regime"] == "ranging_calm"
        assert kwargs["ml_regime"] == "ranging_calm"
        assert kwargs["agree"] == 1
        assert kwargs["ml_model_type"] == "hmm"
        # Shadow prediction must never take the ADX fallback path
        assert fake_ml.predict.call_args[1]["allow_fallback"] is False

    def test_refresh_writes_shadow_row_disagree(self, market_data, monkeypatch):
        monkeypatch.setenv("ML_REGIME_SHADOW", "true")
        fake_ml = MagicMock()
        fake_ml.predict.return_value = _fake_ml_result(MarketRegime.TRENDING_MODERATE)
        det = self._detector_with_shadow(fake_ml)

        result = self._drive(
            det, MarketRegime.RANGING_CALM, datetime(2026, 1, 1), market_data
        )
        # ADX result unchanged despite ML disagreement
        assert result == MarketRegime.RANGING_CALM
        kwargs = det._db.save_regime_shadow.call_args[1]
        assert kwargs["agree"] == 0
        assert kwargs["ml_regime"] == "trending_moderate"

    def test_cache_hit_does_not_record(self, market_data, monkeypatch):
        monkeypatch.setenv("ML_REGIME_SHADOW", "true")
        fake_ml = MagicMock()
        fake_ml.predict.return_value = _fake_ml_result()
        det = self._detector_with_shadow(fake_ml)

        t0 = datetime(2026, 1, 1)
        self._drive(det, MarketRegime.RANGING_CALM, t0, market_data)
        # Second call within the 1h cache TTL: served from cache
        self._drive(
            det,
            MarketRegime.RANGING_CALM,
            t0 + timedelta(minutes=10),
            market_data,
        )
        assert det._db.save_regime_shadow.call_count == 1

    def test_no_artifact_skips_gracefully(self, market_data, monkeypatch):
        monkeypatch.setenv("ML_REGIME_SHADOW", "true")
        det = self._detector_with_shadow(None)

        result = self._drive(
            det, MarketRegime.RANGING_CALM, datetime(2026, 1, 1), market_data
        )
        assert result == MarketRegime.RANGING_CALM
        assert det._db.save_regime_shadow.call_count == 0

    def test_shadow_exception_does_not_propagate(self, market_data, monkeypatch):
        monkeypatch.setenv("ML_REGIME_SHADOW", "true")
        fake_ml = MagicMock()
        fake_ml.predict.side_effect = RuntimeError("model exploded")
        det = self._detector_with_shadow(fake_ml)

        result = self._drive(
            det, MarketRegime.RANGING_CALM, datetime(2026, 1, 1), market_data
        )
        assert result == MarketRegime.RANGING_CALM
        assert det._db.save_regime_shadow.call_count == 0

    def test_env_flag_disables_shadow(self, market_data, monkeypatch):
        monkeypatch.setenv("ML_REGIME_SHADOW", "false")
        fake_ml = MagicMock()
        det = self._detector_with_shadow(fake_ml)

        result = self._drive(
            det, MarketRegime.RANGING_CALM, datetime(2026, 1, 1), market_data
        )
        assert result == MarketRegime.RANGING_CALM
        fake_ml.predict.assert_not_called()
        assert det._db.save_regime_shadow.call_count == 0

    def test_no_db_skips_shadow(self, market_data, monkeypatch):
        monkeypatch.setenv("ML_REGIME_SHADOW", "true")
        fake_ml = MagicMock()
        det = self._detector_with_shadow(fake_ml)
        det._db = None

        result = self._drive(
            det, MarketRegime.RANGING_CALM, datetime(2026, 1, 1), market_data
        )
        assert result == MarketRegime.RANGING_CALM
        fake_ml.predict.assert_not_called()


# ---------------------------------------------------------------------------
# Shadow persistence + endpoint summary shape
# ---------------------------------------------------------------------------


class TestShadowPersistence:
    """regime_shadow table round-trip and summary computation."""

    def test_save_and_get_round_trip(self, temp_db):
        dbm = temp_db.DatabaseManager()
        row_id = dbm.save_regime_shadow(
            symbol="SUI",
            adx_regime="ranging_calm",
            ml_regime="ranging_calm",
            ml_confidence=0.87,
            ml_model_type="hmm",
            detected_at=datetime(2026, 7, 20, 12, 0, 0),
        )
        assert row_id is not None
        dbm.save_regime_shadow(
            symbol="SUI",
            adx_regime="trending_strong",
            ml_regime="trending_moderate",
            ml_confidence=0.55,
            ml_model_type="hmm",
            detected_at=datetime(2026, 7, 20, 16, 0, 0),
        )
        dbm.save_regime_shadow(
            symbol="BTC",
            adx_regime="ranging_calm",
            ml_regime="ranging_volatile",
            ml_confidence=0.61,
            ml_model_type="gmm",
            detected_at=datetime(2026, 7, 20, 16, 0, 0),
        )

        rows = dbm.get_regime_shadow(symbol="SUI")
        assert len(rows) == 2
        # newest first
        assert rows[0]["adx_regime"] == "trending_strong"
        assert rows[0]["agree"] == 0
        assert rows[1]["agree"] == 1
        assert rows[1]["ml_model_type"] == "hmm"

        all_rows = dbm.get_regime_shadow()
        assert len(all_rows) == 3

    def test_agree_computed_when_omitted(self, temp_db):
        dbm = temp_db.DatabaseManager()
        dbm.save_regime_shadow(
            symbol="ETH",
            adx_regime="ranging_calm",
            ml_regime="ranging_calm",
        )
        rows = dbm.get_regime_shadow(symbol="ETH")
        assert rows[0]["agree"] == 1

    def test_summary_shape(self):
        rows = [
            {
                "adx_regime": "ranging_calm",
                "ml_regime": "ranging_calm",
                "agree": 1,
            },
            {
                "adx_regime": "ranging_calm",
                "ml_regime": "ranging_volatile",
                "agree": 0,
            },
            {
                "adx_regime": "trending_strong",
                "ml_regime": "trending_moderate",
                "agree": 0,
            },
            {
                "adx_regime": "trending_strong",
                "ml_regime": "trending_strong",
                "agree": 1,
            },
        ]
        summary = summarize_regime_shadow(rows)
        assert summary["observations"] == 4
        assert summary["agreement_pct"] == pytest.approx(50.0)
        by_adx = summary["agreement_by_adx_regime"]
        assert by_adx["ranging_calm"]["observations"] == 2
        assert by_adx["ranging_calm"]["agreement_pct"] == pytest.approx(50.0)
        assert summary["ml_regime_distribution"]["ranging_calm"] == 1
        assert summary["ml_regime_distribution"]["trending_strong"] == 1

    def test_summary_empty(self):
        summary = summarize_regime_shadow([])
        assert summary["observations"] == 0
        assert summary["agreement_pct"] is None
        assert summary["agreement_by_adx_regime"] == {}
        assert summary["ml_regime_distribution"] == {}
