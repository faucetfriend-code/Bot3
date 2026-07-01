# Phase 3: ML & Optimization - Implementation Plan

**Status**: PLANNING  
**Estimated Duration**: 4-5 weeks (incremental delivery)  
**Dependencies**: Phase 1 (Infrastructure) ✅, Phase 2 (Data Management) ✅

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Plan 03-01: GMM Regime Detection](#plan-03-01-gmm-regime-detection)
3. [Plan 03-02: Optuna Parameter Optimization](#plan-03-02-optuna-parameter-optimization)
4. [Plan 03-03: Strategy Monitoring](#plan-03-03-strategy-monitoring)
5. [Dependency Graph](#dependency-graph)
6. [Implementation Order](#implementation-order)
7. [Testing Strategy](#testing-strategy)
8. [Effort Estimates](#effort-estimates)
9. [File Structure](#file-structure)
10. [Rollback Strategy](#rollback-strategy)

---

## Architecture Overview

### Current System State
- **Regime Detection**: ADX-based `MarketRegimeDetector` (640 lines) in `trading_bot_v2/market_regime.py`
- **Strategies**: 8 strategies in `trading_bot_v2/strategies/` with env-var configurable params
- **Backtesting**: Complete framework with `BacktestEngine`, `WalkForwardAnalyzer`, `PerformanceTracker`
- **Database**: SQLite + PostgreSQL abstraction with connection pooling
- **Feature Flags**: `FeatureFlags` class for gradual rollout (`trading_bot_v2/feature_flags.py`)
- **Event System**: `EventBus` for decoupled component communication

### Phase 3 Additions
```
trading_bot_v2/
  ml/                          # NEW: ML module
    __init__.py
    gmm_regime.py              # GMM-based regime detector
    feature_engineering.py     # Feature extraction for ML
    model_manager.py           # Model save/load/versioning
  
  optimization/                # NEW: Optimization module
    __init__.py
    optuna_runner.py           # Optuna study runner
    search_spaces.py           # Parameter search space definitions
    run_optimize.py            # CLI entry point
  
  strategy_monitor.py          # NEW: Strategy health monitoring
  
  backtesting/
    optimization_adapter.py    # Bridge: Optuna <-> BacktestEngine
```

---

## Plan 03-01: GMM Regime Detection

### Goal
Replace ADX-based regime detection with Gaussian Mixture Model (GMM) that learns regime patterns from historical data, providing more nuanced and adaptive market classification.

### Task Breakdown

#### Task 03-01-1: Add ML Dependencies
**File**: `trading_bot_v2/requirements.txt`  
**Effort**: 0.5 hours

```python
# Add to requirements.txt
scikit-learn>=1.3.0
joblib>=1.3.0
numpy>=1.24.0  # Already present via other deps, but pin for ML stability
```

**Validation**:
- [ ] `pip install -r requirements.txt` succeeds
- [ ] `python -c "import sklearn; print(sklearn.__version__)"` works
- [ ] No conflicts with existing dependencies

---

#### Task 03-01-2: Implement Feature Engineering Module
**File**: `trading_bot_v2/ml/feature_engineering.py`  
**Effort**: 4-6 hours

**Purpose**: Extract ML features from raw OHLCV data for GMM training.

```python
"""
Feature Engineering for ML Regime Detection

Extracts statistical features from OHLCV data:
- Volatility: 20-period rolling standard deviation of returns
- Returns: 20-period rolling mean of log returns
- Skewness: Rolling skewness of returns (distribution asymmetry)
- Additional: ATR ratio, volume ratio, BB width
"""

from typing import Dict, List, Tuple
from dataclasses import dataclass
import numpy as np
from loguru import logger


@dataclass
class MarketFeatures:
    """Container for extracted market features."""
    volatility: float          # 20-period std of returns
    returns_mean: float        # 20-period mean of log returns
    skewness: float            # Rolling skewness
    atr_ratio: float           # Current ATR / 100-period mean ATR
    volume_ratio: float        # Current volume / 20-period mean volume
    bb_width: float            # Bollinger Band width as % of middle
    
    def to_array(self) -> np.ndarray:
        """Convert to numpy array for GMM input."""
        return np.array([
            self.volatility,
            self.returns_mean,
            self.skewness,
            self.atr_ratio,
            self.volume_ratio,
            self.bb_width,
        ])
    
    @classmethod
    def feature_names(cls) -> List[str]:
        return [
            "volatility", "returns_mean", "skewness",
            "atr_ratio", "volume_ratio", "bb_width",
        ]


class FeatureExtractor:
    """Extracts ML features from OHLCV data."""
    
    def __init__(
        self,
        lookback_period: int = 20,
        atr_period: int = 14,
        bb_period: int = 20,
        bb_std_dev: float = 2.0,
    ):
        self.lookback = lookback_period
        self.atr_period = atr_period
        self.bb_period = bb_period
        self.bb_std_dev = bb_std_dev
    
    def extract(
        self,
        closes: List[float],
        highs: List[float],
        lows: List[float],
        volumes: List[float],
    ) -> MarketFeatures:
        """
        Extract features from OHLCV data.
        
        Args:
            closes: List of close prices (min length: lookback_period + 1)
            highs: List of high prices
            lows: List of low prices
            volumes: List of volume values
            
        Returns:
            MarketFeatures dataclass
            
        Raises:
            ValueError: If insufficient data provided
        """
        min_length = max(self.lookback_period + 1, self.atr_period + 1, self.bb_period)
        if len(closes) < min_length:
            raise ValueError(
                f"Insufficient data: need {min_length} candles, got {len(closes)}"
            )
        
        closes_arr = np.array(closes[-self.lookback_period - 1:])
        highs_arr = np.array(highs[-self.lookback_period - 1:])
        lows_arr = np.array(lows[-self.lookback_period - 1:])
        volumes_arr = np.array(volumes[-self.lookback_period - 1:])
        
        # 1. Log returns
        log_returns = np.diff(np.log(closes_arr))
        
        # 2. Volatility: 20-period rolling std of returns
        volatility = float(np.std(log_returns[-self.lookback_period:]))
        
        # 3. Returns mean: 20-period rolling mean
        returns_mean = float(np.mean(log_returns[-self.lookback_period:]))
        
        # 4. Skewness: distribution asymmetry
        skewness = self._calculate_skewness(log_returns[-self.lookback_period:])
        
        # 5. ATR ratio: current ATR / long-term mean ATR
        atr_ratio = self._calculate_atr_ratio(highs_arr, lows_arr, closes_arr)
        
        # 6. Volume ratio: current volume / 20-period mean
        volume_ratio = (
            float(volumes_arr[-1] / np.mean(volumes_arr[-self.lookback_period:]))
            if np.mean(volumes_arr[-self.lookback_period:]) > 0
            else 1.0
        )
        
        # 7. Bollinger Band width
        bb_width = self._calculate_bb_width(closes_arr)
        
        return MarketFeatures(
            volatility=volatility,
            returns_mean=returns_mean,
            skewness=skewness,
            atr_ratio=atr_ratio,
            volume_ratio=volume_ratio,
            bb_width=bb_width,
        )
    
    def extract_batch(
        self,
        closes: List[float],
        highs: List[float],
        lows: List[float],
        volumes: List[float],
    ) -> List[MarketFeatures]:
        """
        Extract features for each timestep (sliding window).
        Returns one MarketFeatures per candle starting from lookback_period.
        """
        features = []
        for i in range(self.lookback_period, len(closes)):
            try:
                feat = self.extract(
                    closes=closes[:i + 1],
                    highs=highs[:i + 1],
                    lows=lows[:i + 1],
                    volumes=volumes[:i + 1],
                )
                features.append(feat)
            except ValueError:
                features.append(None)  # Insufficient data for this window
        return features
    
    @staticmethod
    def _calculate_skewness(data: np.ndarray) -> float:
        """Calculate skewness of distribution."""
        n = len(data)
        if n < 3:
            return 0.0
        mean = np.mean(data)
        std = np.std(data, ddof=1)
        if std == 0:
            return 0.0
        return float(np.mean(((data - mean) / std) ** 3))
    
    def _calculate_atr_ratio(
        self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray
    ) -> float:
        """Calculate current ATR / long-term mean ATR."""
        # Current ATR (14-period)
        tr = np.maximum(
            highs[-self.atr_period:] - lows[-self.atr_period:],
            np.maximum(
                np.abs(highs[-self.atr_period:] - np.roll(closes, 1)[-self.atr_period:]),
                np.abs(lows[-self.atr_period:] - np.roll(closes, 1)[-self.atr_period:]),
            ),
        )
        current_atr = np.mean(tr)
        
        # Long-term mean ATR (100-period if available)
        long_period = min(100, len(closes) - 1)
        tr_long = np.maximum(
            highs[-long_period:] - lows[-long_period:],
            np.maximum(
                np.abs(highs[-long_period:] - np.roll(closes, 1)[-long_period:]),
                np.abs(lows[-long_period:] - np.roll(closes, 1)[-long_period:]),
            ),
        )
        mean_atr = np.mean(tr_long)
        
        return float(current_atr / mean_atr) if mean_atr > 0 else 1.0
    
    def _calculate_bb_width(self, closes: np.ndarray) -> float:
        """Calculate Bollinger Band width as % of middle band."""
        if len(closes) < self.bb_period:
            return 0.0
        middle = np.mean(closes[-self.bb_period:])
        std = np.std(closes[-self.bb_period:])
        upper = middle + (self.bb_std_dev * std)
        lower = middle - (self.bb_std_dev * std)
        width_pct = ((upper - lower) / middle) * 100 if middle > 0 else 0
        return float(width_pct)
```

**Test Criteria**:
- [ ] `FeatureExtractor.extract()` returns correct `MarketFeatures` for valid data
- [ ] Handles edge cases: all-zero volume, constant prices, minimal data length
- [ ] `extract_batch()` produces correct number of features
- [ ] Feature values are within expected ranges (volatility > 0, etc.)

---

#### Task 03-01-3: Implement GMM Regime Detector
**File**: `trading_bot_v2/ml/gmm_regime.py`  
**Effort**: 6-8 hours

```python
"""
GMM-Based Market Regime Detection

Uses Gaussian Mixture Models to learn regime patterns from historical data.
Provides 3 regimes: TRENDING, RANGING, VOLATILE (maps to existing 5 regimes).

Integration:
- Alternative to ADX-based detection via feature flag USE_ML_REGIME=true
- Falls back to ADX detection if model unavailable or prediction fails
"""

import os
import joblib
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
from loguru import logger
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler

from ..market_regime import MarketRegime, MarketRegimeDetector
from .feature_engineering import FeatureExtractor, MarketFeatures


@dataclass
class GMMConfig:
    """Configuration for GMM regime detector."""
    n_regimes: int = 3                    # TRENDING, RANGING, VOLATILE
    covariance_type: str = "full"         # full, tied, diag, spherical
    n_init: int = 10                      # Number of initializations
    max_iter: int = 300                   # Maximum EM iterations
    training_lookback_months: int = 6     # Historical data for training
    retrain_interval_days: int = 30       # Retrain model every N days
    min_training_samples: int = 1000      # Minimum candles for training
    confidence_threshold: float = 0.6     # Min prediction confidence


class GMMRegimeDetector:
    """
    GMM-based market regime detector.
    
    Learns regime patterns from historical OHLCV data using Gaussian Mixture Models.
    Provides continuous regime probability estimates and hard classifications.
    
    Regime Mapping (3 GMM -> 5 system regimes):
    - GMM TRENDING (high returns_mean, low volatility)
      -> TRENDING_STRONG (if volatility < threshold)
      -> TRENDING_MODERATE (if volatility >= threshold)
    - GMM RANGING (near-zero returns, low volatility)
      -> RANGING_CALM
      -> RANGING_VOLATILE (if BB width > threshold)
    - GMM VOLATILE (high volatility regardless of returns)
      -> RANGING_VOLATILE or INDECISIVE
    """
    
    def __init__(
        self,
        config: Optional[GMMConfig] = None,
        model_dir: str = "trading_bot_v2/ml/models",
        fallback_detector: Optional[MarketRegimeDetector] = None,
    ):
        """
        Initialize GMM Regime Detector.
        
        Args:
            config: GMM configuration parameters
            model_dir: Directory to save/load trained models
            fallback_detector: ADX detector to use when GMM unavailable
        """
        self.config = config or GMMConfig()
        self.model_dir = model_dir
        self.fallback = fallback_detector or MarketRegimeDetector()
        
        # ML components
        self._gmm: Optional[GaussianMixture] = None
        self._scaler: Optional[StandardScaler] = None
        self._feature_extractor = FeatureExtractor()
        
        # State
        self._last_train_time: Optional[datetime] = None
        self._regime_probabilities: Dict[str, np.ndarray] = {}
        
        # Ensure model directory exists
        os.makedirs(model_dir, exist_ok=True)
        
        # Try to load existing model
        self._load_model()
        
        logger.info(
            f"GMMRegimeDetector initialized: "
            f"n_regimes={self.config.n_regimes}, "
            f"covariance={self.config.covariance_type}, "
            f"model_loaded={self._gmm is not None}"
        )
    
    def detect_regime(
        self,
        market_data: Dict[str, List[float]],
        symbol: str = "unknown",
    ) -> MarketRegime:
        """
        Detect current market regime using GMM.
        
        Falls back to ADX detection if:
        - No trained model available
        - Model needs retraining
        - Feature extraction fails
        - Prediction confidence too low
        
        Args:
            market_data: OHLCV data dict with 'high', 'low', 'close', 'volume'
            symbol: Trading symbol for logging
            
        Returns:
            MarketRegime enum value
        """
        # Check if model needs retraining
        if self._needs_retraining():
            logger.info(f"GMM model needs retraining for {symbol}")
            if not self._train_model(market_data, symbol):
                logger.warning(f"GMM training failed, falling back to ADX for {symbol}")
                return self.fallback.detect_regime(market_data)
        
        # Check if model is available
        if self._gmm is None or self._scaler is None:
            logger.debug(f"No GMM model available, using ADX for {symbol}")
            return self.fallback.detect_regime(market_data)
        
        try:
            # Extract features
            features = self._feature_extractor.extract(
                closes=market_data["close"],
                highs=market_data["high"],
                lows=market_data["low"],
                volumes=market_data.get("volume", [1.0] * len(market_data["close"])),
            )
            
            # Scale features
            features_array = features.to_array().reshape(1, -1)
            features_scaled = self._scaler.transform(features_array)
            
            # Predict regime
            regime_idx = self._gmm.predict(features_scaled)[0]
            probabilities = self._gmm.predict_proba(features_scaled)[0]
            
            # Store probabilities for monitoring
            self._regime_probabilities[symbol] = probabilities
            
            # Check confidence
            max_prob = float(np.max(probabilities))
            if max_prob < self.config.confidence_threshold:
                logger.warning(
                    f"GMM low confidence for {symbol}: {max_prob:.2f} < "
                    f"{self.config.confidence_threshold}. Using ADX."
                )
                return self.fallback.detect_regime(market_data)
            
            # Map GMM regime to system regime
            regime = self._map_gmm_to_system_regime(
                regime_idx=regime_idx,
                features=features,
                probabilities=probabilities,
            )
            
            logger.info(
                f"GMM regime for {symbol}: {regime.value} "
                f"(confidence={max_prob:.2f}, probs={probabilities.tolist()})"
            )
            
            return regime
            
        except Exception as e:
            logger.error(f"GMM prediction failed for {symbol}: {e}")
            return self.fallback.detect_regime(market_data)
    
    def _map_gmm_to_system_regime(
        self,
        regime_idx: int,
        features: MarketFeatures,
        probabilities: np.ndarray,
    ) -> MarketRegime:
        """
        Map GMM regime index to system MarketRegime.
        
        The GMM learns 3 clusters. We map them based on feature characteristics:
        - Cluster with highest returns_mean -> TRENDING
        - Cluster with lowest volatility -> RANGING_CALM
        - Cluster with highest volatility -> RANGING_VOLATILE / INDECISIVE
        """
        # Get cluster centers for interpretation
        centers = self._scaler.inverse_transform(self._gmm.means_)
        
        # Identify which cluster is which based on features
        # Feature order: volatility, returns_mean, skewness, atr_ratio, volume_ratio, bb_width
        volatility_by_cluster = centers[:, 0]  # volatility is first feature
        returns_by_cluster = centers[:, 1]     # returns_mean is second feature
        
        trending_cluster = int(np.argmax(np.abs(returns_by_cluster)))
        volatile_cluster = int(np.argmax(volatility_by_cluster))
        # Ranging is the remaining one
        ranging_cluster = 3 - trending_cluster - volatile_cluster
        
        # Map current prediction
        if regime_idx == trending_cluster:
            # Distinguish strong vs moderate trending
            if features.volatility < np.median(volatility_by_cluster):
                return MarketRegime.TRENDING_STRONG
            else:
                return MarketRegime.TRENDING_MODERATE
        
        elif regime_idx == volatile_cluster:
            # High volatility regime
            if features.bb_width > 5.0:  # Very wide bands
                return MarketRegime.INDECISIVE
            else:
                return MarketRegime.RANGING_VOLATILE
        
        else:  # ranging_cluster
            # Low volatility, low returns
            if features.bb_width < 2.0:  # Tight bands
                return MarketRegime.RANGING_CALM
            else:
                return MarketRegime.RANGING_VOLATILE
    
    def _needs_retraining(self) -> bool:
        """Check if model needs retraining."""
        if self._gmm is None:
            return True
        if self._last_train_time is None:
            return True
        elapsed = datetime.now() - self._last_train_time
        return elapsed.days >= self.config.retrain_interval_days
    
    def _train_model(
        self,
        market_data: Dict[str, List[float]],
        symbol: str = "unknown",
    ) -> bool:
        """
        Train GMM model on historical data.
        
        Uses sliding window feature extraction to create training samples.
        
        Returns:
            True if training succeeded, False otherwise
        """
        try:
            closes = market_data["close"]
            highs = market_data["high"]
            lows = market_data["low"]
            volumes = market_data.get("volume", [1.0] * len(closes))
            
            if len(closes) < self.config.min_training_samples:
                logger.warning(
                    f"Insufficient training data for {symbol}: "
                    f"{len(closes)} < {self.config.min_training_samples}"
                )
                return False
            
            logger.info(f"Training GMM model on {len(closes)} candles for {symbol}")
            
            # Extract features using sliding window
            all_features = []
            for i in range(self.config.min_training_samples, len(closes)):
                try:
                    feat = self._feature_extractor.extract(
                        closes=closes[:i + 1],
                        highs=highs[:i + 1],
                        lows=lows[:i + 1],
                        volumes=volumes[:i + 1],
                    )
                    all_features.append(feat.to_array())
                except ValueError:
                    continue
            
            if len(all_features) < self.config.min_training_samples:
                logger.warning(
                    f"Insufficient valid training samples for {symbol}: "
                    f"{len(all_features)} < {self.config.min_training_samples}"
                )
                return False
            
            X = np.array(all_features)
            
            # Scale features
            self._scaler = StandardScaler()
            X_scaled = self._scaler.fit_transform(X)
            
            # Train GMM
            self._gmm = GaussianMixture(
                n_components=self.config.n_regimes,
                covariance_type=self.config.covariance_type,
                n_init=self.config.n_init,
                max_iter=self.config.max_iter,
                random_state=42,
            )
            self._gmm.fit(X_scaled)
            
            # Log training metrics
            bic = self._gmm.bic(X_scaled)
            aic = self._gmm.aic(X_scaled)
            logger.info(
                f"GMM training complete for {symbol}: "
                f"BIC={bic:.2f}, AIC={aic:.2f}, "
                f"converged={self._gmm.converged_}"
            )
            
            # Save model
            self._save_model(symbol)
            self._last_train_time = datetime.now()
            
            return True
            
        except Exception as e:
            logger.error(f"GMM training failed for {symbol}: {e}")
            return False
    
    def _save_model(self, symbol: str = "default") -> None:
        """Save trained model to disk."""
        model_path = os.path.join(self.model_dir, f"gmm_model_{symbol}.joblib")
        scaler_path = os.path.join(self.model_dir, f"gmm_scaler_{symbol}.joblib")
        config_path = os.path.join(self.model_dir, f"gmm_config_{symbol}.joblib")
        
        joblib.dump(self._gmm, model_path)
        joblib.dump(self._scaler, scaler_path)
        joblib.dump({
            "config": self.config,
            "last_train_time": self._last_train_time,
            "feature_names": MarketFeatures.feature_names(),
        }, config_path)
        
        logger.info(f"GMM model saved to {model_path}")
    
    def _load_model(self, symbol: str = "default") -> bool:
        """Load trained model from disk."""
        model_path = os.path.join(self.model_dir, f"gmm_model_{symbol}.joblib")
        scaler_path = os.path.join(self.model_dir, f"gmm_scaler_{symbol}.joblib")
        config_path = os.path.join(self.model_dir, f"gmm_config_{symbol}.joblib")
        
        if not all(os.path.exists(p) for p in [model_path, scaler_path]):
            logger.info(f"No saved GMM model found for {symbol}")
            return False
        
        try:
            self._gmm = joblib.load(model_path)
            self._scaler = joblib.load(scaler_path)
            
            if os.path.exists(config_path):
                saved = joblib.load(config_path)
                self._last_train_time = saved.get("last_train_time")
            
            logger.info(f"GMM model loaded for {symbol}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load GMM model for {symbol}: {e}")
            return False
    
    def get_regime_probabilities(self, symbol: str) -> Optional[Dict[str, float]]:
        """
        Get regime probability distribution for a symbol.
        
        Returns:
            Dict mapping regime name to probability, or None if no prediction
        """
        if symbol not in self._regime_probabilities:
            return None
        
        probs = self._regime_probabilities[symbol]
        return {
            "trending": float(probs[0]) if len(probs) > 0 else 0.0,
            "ranging": float(probs[1]) if len(probs) > 1 else 0.0,
            "volatile": float(probs[2]) if len(probs) > 2 else 0.0,
        }
    
    def force_retrain(
        self,
        market_data: Dict[str, List[float]],
        symbol: str = "default",
    ) -> bool:
        """Force immediate model retraining."""
        self._last_train_time = None  # Force retrain
        return self._train_model(market_data, symbol)
```

**Integration Points**:
- Uses `MarketRegime` enum from `market_regime.py`
- Falls back to `MarketRegimeDetector.detect_regime()` when GMM unavailable
- Compatible with existing `detect_regime_cached()` pattern

**Test Criteria**:
- [ ] GMM trains successfully on 6+ months of historical data
- [ ] Prediction accuracy >= 70% when compared to ADX labels (backtesting)
- [ ] Graceful fallback to ADX when model unavailable
- [ ] Model save/load works correctly with joblib
- [ ] Feature extraction produces valid values for edge cases

---

#### Task 03-01-4: Integrate GMM with MarketRegime
**File**: `trading_bot_v2/market_regime.py` (modification)  
**File**: `trading_bot_v2/feature_flags.py` (modification)  
**Effort**: 3-4 hours

**Changes to `feature_flags.py`**:
```python
# Add to FeatureFlags.__init__()
self.enable_ml_regime = self._get_bool_env("USE_ML_REGIME", False)

# Add to get_feature_status()
"features": {
    ...existing features...
    "ml_regime_detection": self.enable_ml_regime,
}
```

**Changes to `market_regime.py`**:
```python
class MarketRegimeDetector:
    def __init__(self, ...):
        ...existing code...
        
        # Phase 3: ML regime detection
        self._ml_detector = None
        self._use_ml = self._get_env_bool("USE_ML_REGIME", False)
        
        if self._use_ml:
            try:
                from .ml.gmm_regime import GMMRegimeDetector
                self._ml_detector = GMMRegimeDetector(fallback_detector=self)
                logger.info("ML regime detection enabled")
            except ImportError:
                logger.warning("ML dependencies not available, using ADX only")
                self._use_ml = False
    
    def detect_regime(self, market_data: Dict[str, List[float]]) -> MarketRegime:
        """Detect regime using ML or ADX based on feature flag."""
        if self._use_ml and self._ml_detector is not None:
            return self._ml_detector.detect_regime(market_data)
        return self._detect_regime_adx(market_data)
    
    def _detect_regime_adx(self, market_data: Dict[str, List[float]]) -> MarketRegime:
        """Original ADX-based detection (renamed from detect_regime)."""
        # ... existing ADX logic unchanged ...
```

**Test Criteria**:
- [ ] `USE_ML_REGIME=false` uses ADX detection (default behavior unchanged)
- [ ] `USE_ML_REGIME=true` uses GMM detection with ADX fallback
- [ ] Feature flag properly logged on startup
- [ ] No performance regression when ML disabled

---

#### Task 03-01-5: GMM Backtesting Comparison
**File**: `trading_bot_v2/backtesting/gmm_comparison.py` (new)  
**Effort**: 4-6 hours

**Purpose**: Compare GMM vs ADX regime detection accuracy using historical data.

```python
"""
GMM vs ADX Regime Comparison

Runs backtesting with both regime detectors and compares:
- Regime classification accuracy (against labeled data)
- Strategy performance per regime
- Signal quality metrics
"""

from typing import Dict, List, Tuple
from loguru import logger
from .engine import BacktestEngine
from .performance import BacktestResult


class GMMComparisonRunner:
    """Compare GMM and ADX regime detection via backtesting."""
    
    def __init__(self, engine: BacktestEngine):
        self.engine = engine
    
    def run_comparison(
        self,
        start: str,
        end: str,
        symbol: str,
        initial_capital: float = 10000.0,
    ) -> Dict[str, BacktestResult]:
        """
        Run backtest with both ADX and GMM regime detection.
        
        Returns:
            Dict with 'adx' and 'gmm' BacktestResult objects
        """
        results = {}
        
        # Run with ADX (default)
        logger.info("Running backtest with ADX regime detection...")
        import os
        os.environ["USE_ML_REGIME"] = "false"
        adx_result = self.engine.run(start, end, symbol, initial_capital)
        results["adx"] = adx_result
        
        # Run with GMM
        logger.info("Running backtest with GMM regime detection...")
        os.environ["USE_ML_REGIME"] = "true"
        gmm_result = self.engine.run(start, end, symbol, initial_capital)
        results["gmm"] = gmm_result
        
        # Restore default
        os.environ.pop("USE_ML_REGIME", None)
        
        # Print comparison
        self._print_comparison(results)
        
        return results
    
    def _print_comparison(self, results: Dict[str, BacktestResult]) -> None:
        """Print side-by-side comparison."""
        adx = results["adx"]
        gmm = results["gmm"]
        
        print(f"\n{'='*70}")
        print(f"REGIME DETECTION COMPARISON: ADX vs GMM")
        print(f"{'='*70}")
        print(f"{'Metric':<30} {'ADX':>15} {'GMM':>15} {'Delta':>10}")
        print(f"{'-'*70}")
        
        metrics = [
            ("Total Return", f"{adx.total_return_pct:+.1f}%", f"{gmm.total_return_pct:+.1f}%"),
            ("Sharpe Ratio", f"{adx.sharpe_ratio:.2f}", f"{gmm.sharpe_ratio:.2f}"),
            ("Max Drawdown", f"{adx.max_drawdown_pct:.1f}%", f"{gmm.max_drawdown_pct:.1f}%"),
            ("Win Rate", f"{adx.win_rate_pct:.1f}%", f"{gmm.win_rate_pct:.1f}%"),
            ("Profit Factor", f"{adx.profit_factor:.2f}", f"{gmm.profit_factor:.2f}"),
            ("Total Trades", f"{adx.total_trades}", f"{gmm.total_trades}"),
        ]
        
        for name, adx_val, gmm_val in metrics:
            print(f"{name:<30} {adx_val:>15} {gmm_val:>15}")
        
        print(f"{'='*70}\n")
```

**Test Criteria**:
- [ ] Comparison runs without errors
- [ ] Both ADX and GMM results are populated
- [ ] Results are saved to HTML reports
- [ ] CLI entry point works: `python -m trading_bot_v2.backtesting.gmm_comparison`

---

## Plan 03-02: Optuna Parameter Optimization

### Goal
Automated hyperparameter optimization for all 8 strategies using Optuna, maximizing Sharpe ratio with walk-forward validation.

### Task Breakdown

#### Task 03-02-1: Add Optuna Dependency
**File**: `trading_bot_v2/requirements.txt`  
**Effort**: 0.5 hours

```python
# Add to requirements.txt
optuna>=3.4.0
plotly>=5.18.0  # For optimization history visualization
```

**Validation**:
- [ ] `pip install optuna` succeeds
- [ ] `python -c "import optuna; print(optuna.__version__)"` works

---

#### Task 03-02-2: Define Search Spaces
**File**: `trading_bot_v2/optimization/search_spaces.py`  
**Effort**: 4-6 hours

```python
"""
Optuna Search Spaces for Strategy Parameters

Defines parameter distributions for each of the 8 strategies.
Ranges based on current defaults and known safe bounds.
"""

from typing import Dict, Any, Callable
import optuna
from ..config import StrategyType


# ============================================================================
# Strategy Search Spaces
# ============================================================================

MEAN_REVERSION_SPACE: Dict[str, Callable[[optuna.Trial], Any]] = {
    "rsi_oversold": lambda t: t.suggest_int("rsi_oversold", 25, 40),
    "rsi_overbought": lambda t: t.suggest_int("rsi_overbought", 60, 75),
    "rsi_period": lambda t: t.suggest_int("rsi_period", 10, 20),
    "bb_period": lambda t: t.suggest_int("bb_period", 15, 30),
    "bb_std_dev": lambda t: t.suggest_float("bb_std_dev", 1.5, 3.0),
    "bb_proximity": lambda t: t.suggest_float("bb_proximity", 0.10, 0.40),
    "sma_period": lambda t: t.suggest_int("sma_period", 15, 30),
    "atr_period": lambda t: t.suggest_int("atr_period", 10, 20),
    "atr_stop_multiplier": lambda t: t.suggest_float("atr_stop_multiplier", 1.5, 3.0),
    "min_confidence": lambda t: t.suggest_float("min_confidence", 0.30, 0.60),
    "min_rrr": lambda t: t.suggest_float("min_rrr", 0.3, 1.0),
    "cooldown_minutes": lambda t: t.suggest_int("cooldown_minutes", 0, 30),
}

MA_CROSSOVER_SPACE: Dict[str, Callable[[optuna.Trial], Any]] = {
    "fast_ma_period": lambda t: t.suggest_int("fast_ma_period", 10, 60),
    "slow_ma_period": lambda t: t.suggest_int("slow_ma_period", 100, 250),
    "pullback_min": lambda t: t.suggest_float("pullback_min", 0.01, 0.03),
    "pullback_max": lambda t: t.suggest_float("pullback_max", 0.03, 0.08),
    "volume_threshold": lambda t: t.suggest_float("volume_threshold", 1.0, 2.0),
    "atr_period": lambda t: t.suggest_int("atr_period", 10, 20),
    "atr_stop_multiplier": lambda t: t.suggest_float("atr_stop_multiplier", 2.0, 4.0),
    "min_confidence": lambda t: t.suggest_float("min_confidence", 0.40, 0.70),
}

GRID_TRADING_SPACE: Dict[str, Callable[[optuna.Trial], Any]] = {
    "grid_levels": lambda t: t.suggest_int("grid_levels", 5, 12),
    "grid_spacing_atr": lambda t: t.suggest_float("grid_spacing_atr", 0.3, 0.8),
    "max_positions_per_symbol": lambda t: t.suggest_int("max_positions_per_symbol", 5, 15),
    "adx_threshold": lambda t: t.suggest_float("adx_threshold", 15.0, 25.0),
    "emergency_stop_pct": lambda t: t.suggest_float("emergency_stop_pct", 0.03, 0.08),
    "min_confidence": lambda t: t.suggest_float("min_confidence", 0.30, 0.60),
}

LIQUIDATION_CAPTURE_SPACE: Dict[str, Callable[[optuna.Trial], Any]] = {
    "price_move_threshold": lambda t: t.suggest_float("price_move_threshold", 0.015, 0.04),
    "volume_spike_multiplier": lambda t: t.suggest_float("volume_spike_multiplier", 2.0, 4.0),
    "cooldown_minutes": lambda t: t.suggest_int("cooldown_minutes", 5, 20),
    "min_confidence": lambda t: t.suggest_float("min_confidence", 0.40, 0.70),
}

VWAP_SCALPING_SPACE: Dict[str, Callable[[optuna.Trial], Any]] = {
    "deviation_threshold": lambda t: t.suggest_float("deviation_threshold", 0.01, 0.03),
    "vwap_period": lambda t: t.suggest_int("vwap_period", 20, 50),
    "atr_period": lambda t: t.suggest_int("atr_period", 10, 20),
    "atr_stop_multiplier": lambda t: t.suggest_float("atr_stop_multiplier", 1.5, 3.0),
    "min_confidence": lambda t: t.suggest_float("min_confidence", 0.40, 0.65),
}

FUNDING_ARB_SPACE: Dict[str, Callable[[optuna.Trial], Any]] = {
    "min_funding_rate": lambda t: t.suggest_float("min_funding_rate", 0.0001, 0.001),
    "max_position_value": lambda t: t.suggest_float("max_position_value", 5000, 20000),
    "hedge_ratio": lambda t: t.suggest_float("hedge_ratio", 0.9, 1.0),
    "min_confidence": lambda t: t.suggest_float("min_confidence", 0.50, 0.80),
}

MOMENTUM_SCALPING_SPACE: Dict[str, Callable[[optuna.Trial], Any]] = {
    "fast_ema": lambda t: t.suggest_int("fast_ema", 5, 15),
    "slow_ema": lambda t: t.suggest_int("slow_ema", 15, 30),
    "signal_ema": lambda t: t.suggest_int("signal_ema", 5, 12),
    "atr_period": lambda t: t.suggest_int("atr_period", 10, 20),
    "atr_stop_multiplier": lambda t: t.suggest_float("atr_stop_multiplier", 1.5, 3.0),
    "min_confidence": lambda t: t.suggest_float("min_confidence", 0.40, 0.65),
}

ORDERBOOK_IMBALANCE_SPACE: Dict[str, Callable[[optuna.Trial], Any]] = {
    "imbalance_threshold": lambda t: t.suggest_float("imbalance_threshold", 0.1, 0.4),
    "depth_levels": lambda t: t.suggest_int("depth_levels", 3, 10),
    "lookback_seconds": lambda t: t.suggest_int("lookback_seconds", 30, 120),
    "min_confidence": lambda t: t.suggest_float("min_confidence", 0.40, 0.70),
}

# Registry of all search spaces
STRATEGY_SPACES = {
    StrategyType.MEAN_REVERSION: MEAN_REVERSION_SPACE,
    StrategyType.MA_CROSSOVER: MA_CROSSOVER_SPACE,
    StrategyType.GRID_TRADING: GRID_TRADING_SPACE,
    StrategyType.LIQUIDATION_CAPTURE: LIQUIDATION_CAPTURE_SPACE,
    StrategyType.VWAP_SCALPING: VWAP_SCALPING_SPACE,
    StrategyType.FUNDING_ARB: FUNDING_ARB_SPACE,
    StrategyType.MOMENTUM_SCALPING: MOMENTUM_SCALPING_SPACE,
    StrategyType.ORDERBOOK_IMBALANCE: ORDERBOOK_IMBALANCE_SPACE,
}


def suggest_strategy_params(
    trial: optuna.Trial,
    strategy_type: StrategyType,
) -> Dict[str, Any]:
    """
    Suggest parameters for a strategy from its search space.
    
    Args:
        trial: Optuna trial object
        strategy_type: Strategy to optimize
        
    Returns:
        Dict of suggested parameter values
    """
    space = STRATEGY_SPACES.get(strategy_type)
    if space is None:
        raise ValueError(f"No search space defined for {strategy_type}")
    
    params = {}
    for name, sampler in space.items():
        params[name] = sampler(trial)
    
    return params
```

**Test Criteria**:
- [ ] All 8 strategy search spaces defined
- [ ] Parameter ranges are within safe bounds (no crashes)
- [ ] `suggest_strategy_params()` returns valid dict

---

#### Task 03-02-3: Implement Optimization Runner
**File**: `trading_bot_v2/optimization/optuna_runner.py`  
**Effort**: 8-10 hours

```python
"""
Optuna Optimization Runner

Runs hyperparameter optimization for trading strategies using:
- TPE sampler (default) or Random sampler
- Sharpe ratio maximization as objective
- Walk-forward validation to prevent overfitting
- SQLite storage for study persistence
"""

import os
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass, asdict
from loguru import logger
import optuna
from optuna.samplers import TPESampler, RandomSampler

from ..config import config, StrategyType
from ..backtesting.engine import BacktestEngine
from ..backtesting.performance import BacktestResult
from .search_spaces import suggest_strategy_params, STRATEGY_SPACES


@dataclass
class OptimizationConfig:
    """Configuration for optimization run."""
    strategy: StrategyType
    n_trials: int = 100
    sampler: str = "tpe"  # "tpe" or "random"
    storage: str = "sqlite:///trading_bot_v2/optimization/studies.db"
    study_name: Optional[str] = None
    walk_forward: bool = True
    train_months: int = 6
    test_months: int = 1
    initial_capital: float = 10000.0
    symbol: str = "SUI-USDC"
    start_date: str = "2024-01-01"
    end_date: str = "2024-12-31"
    n_jobs: int = 1  # Parallel trials (1 = sequential)


@dataclass
class OptimizationResult:
    """Result of an optimization study."""
    strategy: str
    best_params: Dict[str, Any]
    best_value: float  # Best Sharpe ratio
    n_trials: int
    study_name: str
    duration_seconds: float
    trial_history: List[Dict[str, Any]]
    
    def print_summary(self) -> None:
        print(f"\n{'='*60}")
        print(f"OPTIMIZATION RESULT: {self.strategy}")
        print(f"{'='*60}")
        print(f"  Best Sharpe Ratio : {self.best_value:.4f}")
        print(f"  Trials Completed  : {self.n_trials}")
        print(f"  Duration          : {self.duration_seconds:.1f}s")
        print(f"  Best Parameters   :")
        for k, v in self.best_params.items():
            print(f"    {k}: {v}")
        print(f"{'='*60}\n")
    
    def save_json(self, path: str) -> None:
        """Save result to JSON file."""
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2, default=str)
        logger.info(f"Optimization result saved to {path}")


class OptunaRunner:
    """
    Runs Optuna optimization studies for trading strategies.
    
    Usage:
        runner = OptunaRunner()
        result = runner.optimize(
            strategy=StrategyType.MEAN_REVERSION,
            n_trials=100,
        )
        result.print_summary()
    """
    
    def __init__(self, engine: Optional[BacktestEngine] = None):
        self.engine = engine or BacktestEngine()
    
    def optimize(
        self,
        strategy: StrategyType,
        n_trials: int = 100,
        sampler: str = "tpe",
        walk_forward: bool = True,
        **kwargs,
    ) -> OptimizationResult:
        """
        Run optimization for a single strategy.
        
        Args:
            strategy: Strategy to optimize
            n_trials: Number of optimization trials
            sampler: "tpe" or "random"
            walk_forward: Use walk-forward validation
            **kwargs: Additional OptimizationConfig parameters
            
        Returns:
            OptimizationResult with best parameters
        """
        config = OptimizationConfig(
            strategy=strategy,
            n_trials=n_trials,
            sampler=sampler,
            walk_forward=walk_forward,
            **kwargs,
        )
        
        logger.info(
            f"Starting optimization: {strategy.value}, "
            f"trials={n_trials}, sampler={sampler}, "
            f"walk_forward={walk_forward}"
        )
        
        start_time = datetime.now()
        
        # Create Optuna study
        study = self._create_study(config)
        
        # Run optimization
        study.optimize(
            lambda trial: self._objective(trial, config),
            n_trials=n_trials,
            n_jobs=config.n_jobs,
            show_progress_bar=True,
        )
        
        duration = (datetime.now() - start_time).total_seconds()
        
        # Build result
        result = OptimizationResult(
            strategy=strategy.value,
            best_params=study.best_params,
            best_value=study.best_value,
            n_trials=len(study.trials),
            study_name=study.study_name,
            duration_seconds=duration,
            trial_history=[
                {
                    "number": t.number,
                    "value": t.value,
                    "params": t.params,
                    "state": t.state.name,
                }
                for t in study.trials
            ],
        )
        
        result.print_summary()
        
        return result
    
    def _create_study(self, config: OptimizationConfig) -> optuna.Study:
        """Create or load an Optuna study."""
        study_name = config.study_name or (
            f"{config.strategy.value}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        
        if config.sampler == "tpe":
            sampler = TPESampler(seed=42)
        elif config.sampler == "random":
            sampler = RandomSampler(seed=42)
        else:
            raise ValueError(f"Unknown sampler: {config.sampler}")
        
        return optuna.create_study(
            study_name=study_name,
            storage=config.storage,
            direction="maximize",  # Maximize Sharpe ratio
            sampler=sampler,
            load_if_exists=True,
        )
    
    def _objective(
        self,
        trial: optuna.Trial,
        config: OptimizationConfig,
    ) -> float:
        """
        Optuna objective function.
        
        Runs backtest with suggested parameters and returns Sharpe ratio.
        Uses walk-forward if enabled.
        """
        # Suggest parameters
        params = suggest_strategy_params(trial, config.strategy)
        
        # Log trial
        logger.debug(
            f"Trial {trial.number}: {config.strategy.value} "
            f"params={params}"
        )
        
        try:
            if config.walk_forward:
                sharpe = self._walk_forward_objective(params, config)
            else:
                sharpe = self._simple_objective(params, config)
            
            logger.debug(f"Trial {trial.number}: Sharpe={sharpe:.4f}")
            return sharpe
            
        except Exception as e:
            logger.error(f"Trial {trial.number} failed: {e}")
            return -10.0  # Return very bad value for failed trials
    
    def _simple_objective(
        self,
        params: Dict[str, Any],
        config: OptimizationConfig,
    ) -> float:
        """Simple backtest objective (single period)."""
        # Create strategy instance with suggested params
        strategy_instance = self._create_strategy(config.strategy, params)
        
        # Run backtest
        result = self.engine.run(
            start=config.start_date,
            end=config.end_date,
            symbol=config.symbol,
            initial_capital=config.initial_capital,
            strategy_filter=config.strategy.value,
        )
        
        return result.sharpe_ratio
    
    def _walk_forward_objective(
        self,
        params: Dict[str, Any],
        config: OptimizationConfig,
    ) -> float:
        """Walk-forward objective (average across windows)."""
        from ..backtesting.walk_forward import WalkForwardAnalyzer
        
        wf = WalkForwardAnalyzer(self.engine)
        windows = wf._build_windows(
            config.start_date, config.end_date,
            config.train_months, config.test_months,
        )
        
        sharpes = []
        for train_start, train_end, test_start, test_end in windows:
            # Run on test window
            result = self.engine.run(
                start=test_start,
                end=test_end,
                symbol=config.symbol,
                initial_capital=config.initial_capital,
            )
            sharpes.append(result.sharpe_ratio)
        
        # Return average Sharpe across windows
        return sum(sharpes) / len(sharpes) if sharpes else -10.0
    
    def _create_strategy(
        self,
        strategy_type: StrategyType,
        params: Dict[str, Any],
    ):
        """Create strategy instance with given parameters."""
        # Import strategy classes
        from ..strategies.mean_reversion import MeanReversionStrategy
        from ..strategies.ma_crossover import MACrossoverStrategy
        from ..strategies.grid_trading import GridTradingStrategy
        from ..strategies.liquidation_capture import LiquidationCaptureStrategy
        from ..strategies.vwap_scalping import VWAPScalpingStrategy
        from ..strategies.funding_arb import FundingArbStrategy
        from ..strategies.momentum_scalping import MomentumScalpingStrategy
        from ..strategies.orderbook_imbalance import OrderBookImbalanceStrategy
        
        strategy_map = {
            StrategyType.MEAN_REVERSION: MeanReversionStrategy,
            StrategyType.MA_CROSSOVER: MACrossoverStrategy,
            StrategyType.GRID_TRADING: GridTradingStrategy,
            StrategyType.LIQUIDATION_CAPTURE: LiquidationCaptureStrategy,
            StrategyType.VWAP_SCALPING: VWAPScalpingStrategy,
            StrategyType.FUNDING_ARB: FundingArbStrategy,
            StrategyType.MOMENTUM_SCALPING: MomentumScalpingStrategy,
            StrategyType.ORDERBOOK_IMBALANCE: OrderBookImbalanceStrategy,
        }
        
        strategy_class = strategy_map.get(strategy_type)
        if strategy_class is None:
            raise ValueError(f"Unknown strategy: {strategy_type}")
        
        return strategy_class(**params)
    
    def get_study_history(
        self,
        strategy: StrategyType,
        study_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get trial history from a completed study."""
        storage = "sqlite:///trading_bot_v2/optimization/studies.db"
        
        studies = optuna.get_all_study_names(storage)
        matching = [s for s in studies if strategy.value in s]
        
        if not matching:
            logger.warning(f"No studies found for {strategy.value}")
            return []
        
        target_name = study_name or matching[-1]  # Latest study
        
        study = optuna.load_study(study_name=target_name, storage=storage)
        
        return [
            {
                "number": t.number,
                "value": t.value,
                "params": t.params,
                "state": t.state.name,
            }
            for t in study.trials
        ]
```

**Test Criteria**:
- [ ] Optimization completes with TPE sampler
- [ ] Optimization completes with Random sampler
- [ ] Walk-forward validation produces average Sharpe
- [ ] Study results persist to SQLite
- [ ] `get_study_history()` retrieves past results
- [ ] Failed trials return -10.0 (don't crash study)

---

#### Task 03-02-4: Create CLI Runner
**File**: `trading_bot_v2/optimization/run_optimize.py`  
**Effort**: 2-3 hours

```python
"""
CLI Runner for Strategy Optimization

Usage:
    python -m trading_bot_v2.optimization.run_optimize --strategy MeanReversion
    python -m trading_bot_v2.optimization.run_optimize --strategy MACrossover --trials 200
    python -m trading_bot_v2.optimization.run_optimize --strategy GridTrading --sampler random
    python -m trading_bot_v2.optimization.run_optimize --strategy ALL --trials 50
"""

import argparse
import sys
from typing import List
from loguru import logger

from ..config import StrategyType
from .optuna_runner import OptunaRunner


STRATEGY_MAP = {
    "MeanReversion": StrategyType.MEAN_REVERSION,
    "MACrossover": StrategyType.MA_CROSSOVER,
    "GridTrading": StrategyType.GRID_TRADING,
    "LiquidationCapture": StrategyType.LIQUIDATION_CAPTURE,
    "VWAPScalping": StrategyType.VWAP_SCALPING,
    "FundingArb": StrategyType.FUNDING_ARB,
    "MomentumScalping": StrategyType.MOMENTUM_SCALPING,
    "OrderBookImbalance": StrategyType.ORDERBOOK_IMBALANCE,
    "ALL": None,  # Special: optimize all strategies
}


def main():
    parser = argparse.ArgumentParser(
        description="Optimize trading strategy parameters with Optuna"
    )
    parser.add_argument(
        "--strategy",
        choices=list(STRATEGY_MAP.keys()),
        required=True,
        help="Strategy to optimize (or ALL for all strategies)",
    )
    parser.add_argument(
        "--trials",
        type=int,
        default=100,
        help="Number of optimization trials (default: 100)",
    )
    parser.add_argument(
        "--sampler",
        choices=["tpe", "random"],
        default="tpe",
        help="Optuna sampler (default: tpe)",
    )
    parser.add_argument(
        "--symbol",
        default="SUI-USDC",
        help="Trading symbol (default: SUI-USDC)",
    )
    parser.add_argument(
        "--start",
        default="2024-01-01",
        help="Backtest start date (default: 2024-01-01)",
    )
    parser.add_argument(
        "--end",
        default="2024-12-31",
        help="Backtest end date (default: 2024-12-31)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=10000.0,
        help="Initial capital (default: 10000.0)",
    )
    parser.add_argument(
        "--no-walk-forward",
        action="store_true",
        help="Disable walk-forward validation",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output file for results JSON",
    )
    
    args = parser.parse_args()
    
    # Configure logging
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    
    runner = OptunaRunner()
    
    strategies: List[StrategyType]
    if args.strategy == "ALL":
        strategies = list(STRATEGY_MAP.values())
        strategies = [s for s in strategies if s is not None]
    else:
        strategies = [STRATEGY_MAP[args.strategy]]
    
    results = []
    for strategy in strategies:
        logger.info(f"\n{'='*60}")
        logger.info(f"Optimizing: {strategy.value}")
        logger.info(f"{'='*60}")
        
        result = runner.optimize(
            strategy=strategy,
            n_trials=args.trials,
            sampler=args.sampler,
            walk_forward=not args.no_walk_forward,
            start_date=args.start,
            end_date=args.end,
            symbol=args.symbol,
            initial_capital=args.capital,
        )
        
        results.append(result)
        
        # Save individual result
        if args.output:
            output_path = args.output.replace(".json", f"_{strategy.value}.json")
            result.save_json(output_path)
    
    # Print summary for all strategies
    if len(results) > 1:
        print(f"\n{'='*70}")
        print(f"ALL STRATEGIES OPTIMIZATION SUMMARY")
        print(f"{'='*70}")
        for r in results:
            print(f"  {r.strategy:<25} Sharpe: {r.best_value:.4f}")
        print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
```

**Test Criteria**:
- [ ] CLI runs with `--strategy MeanReversion --trials 10`
- [ ] CLI runs with `--strategy ALL --trials 5`
- [ ] Output file saved correctly
- [ ] Walk-forward flag works

---

## Plan 03-03: Strategy Monitoring

### Goal
Real-time monitoring of strategy health including correlation tracking, performance decay detection, and dashboard visualization.

### Task Breakdown

#### Task 03-03-1: Implement Correlation Tracking
**File**: `trading_bot_v2/strategy_monitor.py` (new)  
**Effort**: 6-8 hours

```python
"""
Strategy Health Monitor

Tracks:
1. Correlation between strategy returns (concentration risk)
2. Performance decay detection (Sharpe degradation)
3. Strategy health metrics for dashboard

Storage:
- strategy_correlations table (rolling 30-day window)
- strategy_health_snapshots table (daily aggregates)
"""

import os
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from collections import defaultdict
from loguru import logger
import numpy as np

from .database import get_db_connection
from .event_system import get_event_bus, EventType


@dataclass
class CorrelationAlert:
    """Alert for high strategy correlation."""
    strategy_a: str
    strategy_b: str
    correlation: float
    threshold: float
    timestamp: datetime
    message: str


@dataclass
class DecayAlert:
    """Alert for strategy performance decay."""
    strategy: str
    current_sharpe: float
    average_sharpe: float
    decay_pct: float
    threshold_pct: float
    timestamp: datetime
    message: str


class StrategyMonitor:
    """
    Monitors strategy health and detects anomalies.
    
    Features:
    - Rolling 30-day correlation between strategy returns
    - Performance decay detection (current vs 90-day average Sharpe)
    - Alert generation via EventBus
    """
    
    # Thresholds
    CORRELATION_ALERT_THRESHOLD = 0.7  # Alert if correlation > 0.7
    DECAY_ALERT_THRESHOLD_PCT = 50.0   # Alert if Sharpe drops > 50%
    ROLLING_CORRELATION_DAYS = 30
    ROLLING_SHARPE_DAYS = 90
    MIN_DATA_POINTS = 10  # Minimum trades for correlation calculation
    
    def __init__(self):
        """Initialize Strategy Monitor."""
        self._strategy_returns: Dict[str, List[Tuple[datetime, float]]] = defaultdict(list)
        self._alerts: List[Dict] = []
        
        # Subscribe to trade events
        event_bus = get_event_bus()
        event_bus.subscribe(EventType.SIGNAL_EXECUTED, self._on_trade_executed)
        
        logger.info("StrategyMonitor initialized")
    
    def _on_trade_executed(self, event) -> None:
        """Handle trade execution event."""
        data = event.data
        strategy = data.get("strategy")
        pnl = data.get("pnl", 0.0)
        timestamp = event.timestamp
        
        if strategy and pnl is not None:
            self.record_return(strategy, pnl, timestamp)
    
    def record_return(
        self,
        strategy: str,
        pnl_pct: float,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """
        Record a strategy return for monitoring.
        
        Args:
            strategy: Strategy name
            pnl_pct: PnL as percentage (e.g., 0.02 for 2%)
            timestamp: Trade timestamp (defaults to now)
        """
        ts = timestamp or datetime.now()
        self._strategy_returns[strategy].append((ts, pnl_pct))
        
        # Persist to database
        self._persist_return(strategy, pnl_pct, ts)
        
        # Check for alerts
        self._check_correlation_alerts()
        self._check_decay_alerts()
    
    def calculate_rolling_correlation(
        self,
        strategy_a: str,
        strategy_b: str,
        days: int = 30,
    ) -> Optional[float]:
        """
        Calculate rolling correlation between two strategies.
        
        Args:
            strategy_a: First strategy name
            strategy_b: Second strategy name
            days: Lookback period in days
            
        Returns:
            Correlation coefficient (-1 to 1) or None if insufficient data
        """
        cutoff = datetime.now() - timedelta(days=days)
        
        returns_a = [r for ts, r in self._strategy_returns.get(strategy_a, []) if ts >= cutoff]
        returns_b = [r for ts, r in self._strategy_returns.get(strategy_b, []) if ts >= cutoff]
        
        if len(returns_a) < self.MIN_DATA_POINTS or len(returns_b) < self.MIN_DATA_POINTS:
            return None
        
        # Align by timestamp (use shorter list length)
        min_len = min(len(returns_a), len(returns_b))
        returns_a = returns_a[-min_len:]
        returns_b = returns_b[-min_len:]
        
        # Extract just the PnL values
        pnl_a = np.array([r for _, r in returns_a])
        pnl_b = np.array([r for _, r in returns_b])
        
        # Calculate Pearson correlation
        if np.std(pnl_a) == 0 or np.std(pnl_b) == 0:
            return 0.0  # No variance = no correlation
        
        correlation = float(np.corrcoef(pnl_a, pnl_b)[0, 1])
        
        return correlation
    
    def get_correlation_matrix(
        self,
        strategies: List[str],
        days: int = 30,
    ) -> Dict[Tuple[str, str], float]:
        """
        Calculate full correlation matrix between all strategy pairs.
        
        Returns:
            Dict mapping (strategy_a, strategy_b) to correlation
        """
        matrix = {}
        
        for i, strat_a in enumerate(strategies):
            for strat_b in strategies[i + 1:]:
                corr = self.calculate_rolling_correlation(strat_a, strat_b, days)
                if corr is not None:
                    matrix[(strat_a, strat_b)] = corr
                    matrix[(strat_b, strat_a)] = corr
        
        return matrix
    
    def calculate_rolling_sharpe(
        self,
        strategy: str,
        days: int = 90,
        risk_free_rate: float = 0.0,
    ) -> Optional[float]:
        """
        Calculate rolling Sharpe ratio for a strategy.
        
        Args:
            strategy: Strategy name
            days: Lookback period in days
            risk_free_rate: Annualized risk-free rate
            
        Returns:
            Sharpe ratio or None if insufficient data
        """
        cutoff = datetime.now() - timedelta(days=days)
        returns = [r for ts, r in self._strategy_returns.get(strategy, []) if ts >= cutoff]
        
        if len(returns) < self.MIN_DATA_POINTS:
            return None
        
        returns_arr = np.array(returns)
        
        # Daily Sharpe (assuming ~252 trading days)
        mean_return = np.mean(returns_arr)
        std_return = np.std(returns_arr)
        
        if std_return == 0:
            return 0.0
        
        sharpe = (mean_return - risk_free_rate / 252) / std_return * np.sqrt(252)
        
        return float(sharpe)
    
    def detect_performance_decay(
        self,
        strategy: str,
        current_window_days: int = 30,
        baseline_window_days: int = 90,
    ) -> Optional[DecayAlert]:
        """
        Detect if strategy performance has degraded.
        
        Compares current 30-day Sharpe to 90-day average Sharpe.
        
        Returns:
            DecayAlert if decay detected, None otherwise
        """
        current_sharpe = self.calculate_rolling_sharpe(strategy, current_window_days)
        baseline_sharpe = self.calculate_rolling_sharpe(strategy, baseline_window_days)
        
        if current_sharpe is None or baseline_sharpe is None:
            return None
        
        if baseline_sharpe == 0:
            return None
        
        decay_pct = ((baseline_sharpe - current_sharpe) / abs(baseline_sharpe)) * 100
        
        if decay_pct > self.DECAY_ALERT_THRESHOLD_PCT:
            alert = DecayAlert(
                strategy=strategy,
                current_sharpe=current_sharpe,
                average_sharpe=baseline_sharpe,
                decay_pct=decay_pct,
                threshold_pct=self.DECAY_ALERT_THRESHOLD_PCT,
                timestamp=datetime.now(),
                message=(
                    f"Strategy {strategy} performance decayed: "
                    f"Sharpe {current_sharpe:.2f} vs {baseline_sharpe:.2f} "
                    f"({decay_pct:.1f}% drop)"
                ),
            )
            
            logger.warning(alert.message)
            self._alerts.append(asdict(alert))
            
            return alert
        
        return None
    
    def _check_correlation_alerts(self) -> None:
        """Check all strategy pairs for high correlation."""
        strategies = list(self._strategy_returns.keys())
        
        for i, strat_a in enumerate(strategies):
            for strat_b in strategies[i + 1:]:
                corr = self.calculate_rolling_correlation(strat_a, strat_b)
                
                if corr is not None and abs(corr) > self.CORRELATION_ALERT_THRESHOLD:
                    alert = CorrelationAlert(
                        strategy_a=strat_a,
                        strategy_b=strat_b,
                        correlation=corr,
                        threshold=self.CORRELATION_ALERT_THRESHOLD,
                        timestamp=datetime.now(),
                        message=(
                            f"High correlation detected: {strat_a} <-> {strat_b}: "
                            f"{corr:.3f} > {self.CORRELATION_ALERT_THRESHOLD}"
                        ),
                    )
                    
                    logger.warning(alert.message)
                    self._alerts.append(asdict(alert))
                    
                    # Publish event
                    event_bus = get_event_bus()
                    event_bus.publish_event(
                        EventType.RISK_LIMIT_EXCEEDED,
                        {
                            "type": "correlation_alert",
                            "strategy_a": strat_a,
                            "strategy_b": strat_b,
                            "correlation": corr,
                        },
                        "StrategyMonitor",
                    )
    
    def _check_decay_alerts(self) -> None:
        """Check all strategies for performance decay."""
        for strategy in self._strategy_returns:
            self.detect_performance_decay(strategy)
    
    def _persist_return(
        self,
        strategy: str,
        pnl_pct: float,
        timestamp: datetime,
    ) -> None:
        """Persist return to database."""
        try:
            with get_db_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO strategy_returns (strategy, pnl_pct, timestamp)
                    VALUES (?, ?, ?)
                    """,
                    (strategy, pnl_pct, timestamp.isoformat()),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to persist return: {e}")
    
    def get_health_summary(
        self,
        strategies: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Get comprehensive health summary for all strategies.
        
        Returns:
            Dict with strategy health metrics
        """
        strategies = strategies or list(self._strategy_returns.keys())
        
        summary = {
            "timestamp": datetime.now().isoformat(),
            "strategies": {},
            "alerts": self._alerts[-10:],  # Last 10 alerts
            "correlation_matrix": {},
        }
        
        for strategy in strategies:
            sharpe_30d = self.calculate_rolling_sharpe(strategy, 30)
            sharpe_90d = self.calculate_rolling_sharpe(strategy, 90)
            returns = self._strategy_returns.get(strategy, [])
            
            summary["strategies"][strategy] = {
                "total_trades": len(returns),
                "sharpe_30d": sharpe_30d,
                "sharpe_90d": sharpe_90d,
                "recent_pnl": [r for _, r in returns[-10:]],
                "decay_detected": sharpe_30d is not None and sharpe_90d is not None
                    and ((sharpe_90d - sharpe_30d) / abs(sharpe_90d) * 100) > self.DECAY_ALERT_THRESHOLD_PCT
                    if sharpe_90d != 0 else False,
            }
        
        # Correlation matrix
        corr_matrix = self.get_correlation_matrix(strategies)
        for (a, b), corr in corr_matrix.items():
            summary["correlation_matrix"][f"{a}:{b}"] = corr
        
        return summary
    
    def get_alerts(
        self,
        alert_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict]:
        """
        Get recent alerts.
        
        Args:
            alert_type: Filter by type ("correlation" or "decay")
            limit: Maximum alerts to return
            
        Returns:
            List of alert dicts
        """
        alerts = self._alerts
        
        if alert_type == "correlation":
            alerts = [a for a in alerts if "correlation" in a.get("message", "").lower()]
        elif alert_type == "decay":
            alerts = [a for a in alerts if "decay" in a.get("message", "").lower()]
        
        return alerts[-limit:]


# ============================================================================
# Database Schema Extension
# ============================================================================

STRATEGY_MONITOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    pnl_pct REAL NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_strategy_returns_strategy ON strategy_returns(strategy);
CREATE INDEX IF NOT EXISTS idx_strategy_returns_timestamp ON strategy_returns(timestamp);
CREATE INDEX IF NOT EXISTS idx_strategy_returns_strategy_timestamp ON strategy_returns(strategy, timestamp);

CREATE TABLE IF NOT EXISTS strategy_health_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date DATE NOT NULL,
    strategy TEXT NOT NULL,
    sharpe_30d REAL,
    sharpe_90d REAL,
    total_trades INTEGER,
    win_rate REAL,
    avg_pnl REAL,
    correlation_alerts INTEGER DEFAULT 0,
    decay_alerts INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(snapshot_date, strategy)
);
"""
```

**Test Criteria**:
- [ ] `record_return()` stores data correctly
- [ ] `calculate_rolling_correlation()` returns valid coefficient
- [ ] `calculate_rolling_sharpe()` returns valid Sharpe
- [ ] Correlation alert triggers when threshold exceeded
- [ ] Decay alert triggers when Sharpe drops > 50%
- [ ] `get_health_summary()` returns complete metrics

---

#### Task 03-03-2: Add Monitoring Database Schema
**File**: `trading_bot_v2/schema.sql` (modification)  
**File**: `trading_bot_v2/database.py` (modification)  
**Effort**: 2-3 hours

**Add to `schema.sql`**:
```sql
-- Strategy monitoring tables
CREATE TABLE IF NOT EXISTS strategy_returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    pnl_pct REAL NOT NULL,
    timestamp TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_strategy_returns_strategy ON strategy_returns(strategy);
CREATE INDEX IF NOT EXISTS idx_strategy_returns_timestamp ON strategy_returns(timestamp);

CREATE TABLE IF NOT EXISTS strategy_health_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date DATE NOT NULL,
    strategy TEXT NOT NULL,
    sharpe_30d REAL,
    sharpe_90d REAL,
    total_trades INTEGER,
    win_rate REAL,
    avg_pnl REAL,
    correlation_alerts INTEGER DEFAULT 0,
    decay_alerts INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(snapshot_date, strategy)
);
```

**Add to `database.py`**:
```python
# In _init_sqlite_database(), add table creation
# In _create_minimal_pg_schema(), add table creation
```

**Test Criteria**:
- [ ] Tables created on database init
- [ ] SQLite and PostgreSQL schemas both work
- [ ] Indexes created correctly

---

#### Task 03-03-3: Add API Endpoint
**File**: `trading_bot_v2/api_server.py` (modification)  
**Effort**: 3-4 hours

```python
# Add to api_server.py

@app.get("/api/strategy-health")
async def get_strategy_health():
    """
    Get comprehensive strategy health metrics.
    
    Returns:
    - Per-strategy Sharpe ratios (30d, 90d)
    - Correlation matrix between strategies
    - Recent alerts (correlation, decay)
    - Strategy trade counts and PnL
    """
    from .strategy_monitor import StrategyMonitor
    
    monitor = StrategyMonitor()
    summary = monitor.get_health_summary()
    
    return {
        "status": "success",
        "data": summary,
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/api/strategy-health/alerts")
async def get_strategy_alerts(
    alert_type: Optional[str] = None,
    limit: int = 50,
):
    """Get recent strategy health alerts."""
    from .strategy_monitor import StrategyMonitor
    
    monitor = StrategyMonitor()
    alerts = monitor.get_alerts(alert_type=alert_type, limit=limit)
    
    return {
        "status": "success",
        "data": alerts,
        "count": len(alerts),
    }
```

**Test Criteria**:
- [ ] `/api/strategy-health` returns JSON with strategy metrics
- [ ] `/api/strategy-health/alerts` returns alert list
- [ ] API handles empty data gracefully

---

#### Task 03-03-4: Add Grafana Dashboard Panels
**File**: Grafana dashboard JSON (to be created)  
**Effort**: 4-6 hours

**Panels to Add**:

1. **Strategy Correlation Heatmap**
   - Type: Heatmap
   - Data: strategy_correlation_matrix metric
   - Color scale: -1 (red) to 0 (white) to +1 (blue)
   - Alert threshold: 0.7 (highlighted cells)

2. **Strategy Sharpe Trend**
   - Type: Time Series
   - Series: One line per strategy
   - Data: strategy_sharpe_ratio metric with strategy label
   - Alert: Line at 0 (break-even)

3. **Strategy Health Summary Table**
   - Type: Table
   - Columns: Strategy, Trades, Win Rate, Sharpe 30d, Sharpe 90d, Decay Alert
   - Data: strategy_health_snapshot metric

4. **Correlation Alerts**
   - Type: Alert List
   - Data: strategy_correlation_alert metric
   - Severity: Warning (> 0.7), Critical (> 0.85)

**Prometheus Metrics** (add to `metrics.py`):
```python
# Strategy monitoring metrics
STRATEGY_SHARPE_RATIO = Gauge(
    'trading_bot_strategy_sharpe_ratio',
    'Strategy Sharpe ratio',
    ['strategy', 'window'],  # window: 30d, 90d
)

STRATEGY_CORRELATION = Gauge(
    'trading_bot_strategy_correlation',
    'Strategy correlation matrix',
    ['strategy_a', 'strategy_b'],
)

STRATEGY_CORRELATION_ALERT = Counter(
    'trading_bot_strategy_correlation_alerts_total',
    'Strategy correlation alerts',
    ['strategy_a', 'strategy_b'],
)

STRATEGY_DECAY_ALERT = Counter(
    'trading_bot_strategy_decay_alerts_total',
    'Strategy performance decay alerts',
    ['strategy'],
)
```

**Test Criteria**:
- [ ] Grafana panels render correctly
- [ ] Heatmap shows correlation matrix
- [ ] Sharpe trend lines update in real-time
- [ ] Alerts appear when thresholds exceeded

---

## Dependency Graph

```
Plan 03-01 (GMM Regime)          Plan 03-02 (Optuna)          Plan 03-03 (Monitoring)
    │                                │                              │
    ├── Task 01-1: Dependencies      ├── Task 02-1: Dependencies    ├── Task 03-1: Correlation
    │                                │                              │
    ├── Task 01-2: Feature Eng.      ├── Task 02-2: Search Spaces   ├── Task 03-2: DB Schema
    │                                │                              │
    ├── Task 01-3: GMM Detector      ├── Task 02-3: Optuna Runner   ├── Task 03-3: API Endpoint
    │                                │                              │
    ├── Task 01-4: Integration       ├── Task 02-4: CLI Runner      ├── Task 03-4: Grafana
    │                                │                              │
    └── Task 01-5: Comparison        └── (Uses BacktestEngine)      └── (Uses EventBus)
```

**Cross-Plan Dependencies**:
- Plan 03-02 depends on `BacktestEngine` (Phase 2) - already exists
- Plan 03-03 depends on `EventBus` (Phase 2) - already exists
- Plan 03-01 and Plan 03-02 are **independent** (can be parallel)
- Plan 03-03 is **independent** of 03-01 and 03-02

---

## Implementation Order

### Week 1: Foundation (Parallel Tracks)
| Day | Track A (ML) | Track B (Optimization) | Track C (Monitoring) |
|-----|-------------|----------------------|---------------------|
| 1 | Task 01-1: Dependencies | Task 02-1: Dependencies | Task 03-2: DB Schema |
| 2 | Task 01-2: Feature Eng. | Task 02-2: Search Spaces | Task 03-1: Correlation |
| 3 | Task 01-2: Feature Eng. (cont.) | Task 02-2: Search Spaces (cont.) | Task 03-1: Correlation (cont.) |
| 4 | Task 01-3: GMM Detector | Task 02-3: Optuna Runner | Task 03-3: API Endpoint |
| 5 | Task 01-3: GMM Detector (cont.) | Task 02-3: Optuna Runner (cont.) | Task 03-3: API Endpoint (cont.) |

### Week 2: Integration & Testing
| Day | Track A (ML) | Track B (Optimization) | Track C (Monitoring) |
|-----|-------------|----------------------|---------------------|
| 6 | Task 01-4: Integration | Task 02-4: CLI Runner | Task 03-4: Grafana |
| 7 | Task 01-5: Comparison | Testing & Bug Fixes | Task 03-4: Grafana (cont.) |
| 8 | Testing & Bug Fixes | Documentation | Testing & Bug Fixes |
| 9 | Integration Testing | Integration Testing | Integration Testing |
| 10 | Phase 3 Demo & Handoff | Phase 3 Demo & Handoff | Phase 3 Demo & Handoff |

---

## Testing Strategy

### Unit Tests (Per Task)
Each task includes unit tests in `trading_bot_v2/tests/`:

```
tests/
  test_ml_feature_engineering.py    # Feature extraction tests
  test_ml_gmm_regime.py             # GMM regime detection tests
  test_optuna_search_spaces.py      # Search space validation tests
  test_optuna_runner.py             # Optimization runner tests
  test_strategy_monitor.py          # Strategy monitoring tests
```

### Integration Tests
- **GMM + BacktestEngine**: Verify GMM works within backtesting pipeline
- **Optuna + BacktestEngine**: Verify optimization produces valid results
- **StrategyMonitor + EventBus**: Verify alerts trigger correctly

### Validation Criteria
- [ ] All unit tests pass (`pytest trading_bot_v2/tests/`)
- [ ] No type errors (`mypy trading_bot_v2/`)
- [ ] No lint errors (`ruff check trading_bot_v2/`)
- [ ] Performance: GMM prediction < 50ms, Optuna trial < 30s
- [ ] Memory: GMM model < 100MB, no memory leaks

---

## Effort Estimates

| Task | Hours | Complexity | Risk |
|------|-------|-----------|------|
| 01-1: Dependencies | 0.5 | Low | Low |
| 01-2: Feature Engineering | 4-6 | Medium | Medium |
| 01-3: GMM Detector | 6-8 | High | High |
| 01-4: Integration | 3-4 | Medium | Medium |
| 01-5: Comparison | 4-6 | Medium | Low |
| 02-1: Dependencies | 0.5 | Low | Low |
| 02-2: Search Spaces | 4-6 | Medium | Low |
| 02-3: Optuna Runner | 8-10 | High | Medium |
| 02-4: CLI Runner | 2-3 | Low | Low |
| 03-1: Correlation | 6-8 | High | Medium |
| 03-2: DB Schema | 2-3 | Low | Low |
| 03-3: API Endpoint | 3-4 | Medium | Low |
| 03-4: Grafana | 4-6 | Medium | Low |
| **TOTAL** | **48-64 hours** | | |

**Realistic Timeline**: 4-5 weeks (1 developer, part-time)

---

## File Structure

### New Files
```
trading_bot_v2/
  ml/
    __init__.py
    feature_engineering.py    # Feature extraction
    gmm_regime.py             # GMM regime detector
    model_manager.py          # Model versioning (optional)
    models/                   # Saved model files
      .gitkeep
  
  optimization/
    __init__.py
    search_spaces.py          # Parameter search spaces
    optuna_runner.py          # Optimization runner
    run_optimize.py           # CLI entry point
    studies.db                # Optuna study storage
  
  strategy_monitor.py         # Strategy health monitoring
  
  backtesting/
    optimization_adapter.py   # Bridge: Optuna <-> BacktestEngine
    gmm_comparison.py         # GMM vs ADX comparison

tests/
  test_ml_feature_engineering.py
  test_ml_gmm_regime.py
  test_optuna_search_spaces.py
  test_optuna_runner.py
  test_strategy_monitor.py
```

### Modified Files
```
trading_bot_v2/
  requirements.txt            # Add scikit-learn, optuna, plotly
  feature_flags.py            # Add USE_ML_REGIME flag
  market_regime.py            # Add ML detection path
  database.py                 # Add strategy monitoring tables
  schema.sql                  # Add strategy monitoring schema
  api_server.py               # Add /api/strategy-health endpoints
  metrics.py                  # Add strategy monitoring metrics
```

---

## Rollback Strategy

### Feature Flag Control
```bash
# Disable ML regime detection (revert to ADX)
USE_ML_REGIME=false

# Disable all Phase 3 features
USE_ML_REGIME=false
ENABLE_STRATEGY_MONITOR=false
```

### Emergency Rollback
1. Set `USE_ML_REGIME=false` in `.env`
2. Restart bot: `python -m trading_bot_v2.api_server`
3. ADX detection resumes immediately
4. No data loss (GMM model preserved on disk)

### Data Preservation
- Optuna studies persist in SQLite (can resume)
- GMM models persist in `trading_bot_v2/ml/models/`
- Strategy returns persist in `strategy_returns` table
- All Phase 1 & 2 features unaffected

---

## Success Metrics

### Technical
- [ ] GMM regime detection accuracy >= 70% vs ADX
- [ ] Optuna optimization completes 100 trials in < 1 hour
- [ ] Strategy monitor detects correlation > 0.7 within 5 minutes
- [ ] API response time < 200ms for `/api/strategy-health`
- [ ] Zero production incidents during rollout

### Business
- [ ] Optimized parameters improve Sharpe ratio by >= 10%
- [ ] False signal reduction via better regime detection
- [ ] Early warning for strategy decay (before significant losses)
- [ ] Correlation alerts prevent concentration risk

---

*Document generated: 2026-06-30*  
*Author: Generator (Phase 3 Planning)*  
*Status: Ready for Implementation*
