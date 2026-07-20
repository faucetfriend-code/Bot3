"""
Machine Learning Module for Bot 3 Trading System

Provides ML-based regime detection using Gaussian Mixture Models (GMM)
as an alternative to the ADX-based detector.

Components:
- feature_engineering: Extract statistical features from OHLCV data
- gmm_regime: GMM-based market regime detector
- model_manager: Model save/load/versioning

Usage:
    from trading_bot_v2.ml import GMMRegimeDetector, FeatureExtractor

    detector = GMMRegimeDetector()
    regime = detector.detect_regime(market_data)
"""

from .feature_engineering import FeatureExtractor, MarketFeatures
from .gmm_regime import GMMConfig, GMMRegimeDetector, GMMRegimeResult
from .hmm_regime import HMMConfig, HMMRegimeDetector
from .model_manager import (
    ModelManager,
    ModelMetadata,
    read_latest_model_type,
    write_latest_model_type,
)

__all__ = [
    "FeatureExtractor",
    "MarketFeatures",
    "GMMConfig",
    "GMMRegimeDetector",
    "GMMRegimeResult",
    "HMMConfig",
    "HMMRegimeDetector",
    "ModelManager",
    "ModelMetadata",
    "read_latest_model_type",
    "write_latest_model_type",
]
