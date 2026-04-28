"""
Meta-Regime Strategy System
=====================
Uses meta-regime detection to switch between optimized strategies.

REGIMES:
- CRASH:     Large drawdowns (>40% year) → Buy the dip, trailing stops
- BEAR:      Moderate decline → Short only, tight stops  
- BASE:      Sideways/small moves → Grid/mean reversion
- BULL:      Normal uptrend → Standard momentum
- STRONG_BULL: Strong uptrend (>50%) → Aggressive momentum, scale in
"""

from enum import Enum
from typing import Dict, Any, Optional
from loguru import logger
from trading_bot_v2.indicators import calculate_adx


class MetaRegime(Enum):
    """Meta-regime classification based on yearly performance."""
    CRASH = "crash"
    BEAR = "bear"
    BASE = "base" 
    BULL = "bull"
    STRONG_BULL = "strong_bull"
    UNKNOWN = "unknown"


# Strategy configs per meta-regime (TUNED from 2018-2025 data)
REGIME_STRATEGIES: Dict[MetaRegime, Dict[str, Any]] = {
    MetaRegime.CRASH: {
        "strategy": "no_trade",
        "position_size_pct": 0.0,
        "description": "Do not trade - wait for regime change",
    },
    MetaRegime.BEAR: {
        "strategy": "no_trade",
        "position_size_pct": 0.0,
        "description": "Do not trade - wait for regime change",
    },
    MetaRegime.BASE: {
        "strategy": "momentum_long_light",
        "fast_ema": 21,
        "slow_ema": 50,
        "adx_min": 15,
        "stop_type": "trailing",
        "trailing_stop_pct": 0.15,
        "position_size_pct": 0.02,  # Light size
        "description": "Light momentum, smaller positions",
    },
    MetaRegime.BULL: {
        "strategy": "momentum_long",
        "fast_ema": 21,
        "slow_ema": 50,
        "adx_min": 15,
        "stop_type": "trailing",
        "trailing_stop_pct": 0.15,
        "position_size_pct": 0.05,
        "description": "Standard momentum, 15% trailing",
    },
    MetaRegime.STRONG_BULL: {
        "strategy": "momentum_long_aggressive",
        "fast_ema": 21,
        "slow_ema": 50,
        "adx_min": 15,  # Tuned: lower ADX = more trades
        "stop_type": "trailing",
        "trailing_stop_pct": 0.15,
        "position_size_pct": 0.08,  # Larger size in strong bull
        "long_only": True,  # KEY: LONG only performs best
        "description": "Long-only aggressive momentum (TUNED: +62% avg on STRONG_BULL years)",
    },
}


class MetaRegimeDetector:
    """
    Detects current meta-regime for strategy selection.
    
    Uses two-level detection:
    1. Primary: Year-over-year performance (backward looking)
    2. Secondary: ADX trend strength (current period)
    """
    
    def __init__(
        self,
        crash_threshold: float = -40.0,  # % change
        strong_bull_threshold: float = 50.0,
        bull_threshold: float = 0.0,
        base_threshold: float = -20.0,
        lookback_years: int = 1,
    ):
        self.crash_threshold = crash_threshold
        self.strong_bull_threshold = strong_bull_threshold
        self.bull_threshold = bull_threshold
        self.base_threshold = base_threshold
        self.lookback_years = lookback_years
        
        # Cache yearly performance
        self._yearly_returns: Dict[int, float] = {}
        
        logger.info(
            f"MetaRegimeDetector initialized: "
            f"crash={crash_threshold}%, strong_bull={strong_bull_threshold}%"
        )
    
    def update_yearly_performance(self, year: int, yearly_return: float):
        """Update yearly performance cache."""
        self._yearly_returns[year] = yearly_return
    
    def get_current_regime(self, prices, year: int) -> MetaRegime:
        """Detect current meta-regime based on yearly performance."""
        
        # Check this year's performance
        yearly_change = self._yearly_returns.get(year, 0.0)
        
        # Override with estimate if not cached
        if yearly_change == 0 and len(prices) > 24:
            # Estimate from current year's bars
            start = prices[0]
            end = prices[-1]
            yearly_change = (end - start) / start * 100
        
        # Classify
        if yearly_change < self.crash_threshold:
            return MetaRegime.CRASH
        elif yearly_change < -self.base_threshold:
            return MetaRegime.BEAR
        elif yearly_change < self.bull_threshold:
            return MetaRegime.BASE
        elif yearly_change < self.strong_bull_threshold:
            return MetaRegime.BULL
        else:
            return MetaRegime.STRONG_BULL
    
    def get_strategy_config(self, regime: MetaRegime) -> Dict[str, Any]:
        """Get strategy config for regime."""
        return REGIME_STRATEGIES.get(regime, REGIME_STRATEGIES[MetaRegime.BASE])
    
    def get_adaptive_config(self, prices, adx: float, year: int) -> Dict[str, Any]:
        """Get config that adapts to both regime AND current conditions."""
        
        # Get base regime
        regime = self.get_current_regime(prices, year)
        config = self.get_strategy_config(regime).copy()
        
        # Override ADX if trending very strongly
        if adx > 40 and regime not in [MetaRegime.CRASH]:
            # Can be more aggressive currently
            config["adx_min"] = max(config["adx_min"] - 5, 15)
            config["position_size_pct"] = min(config["position_size_pct"] * 1.25, 0.10)
        
        return config


# Simple standalone detector for quick use
def detect_meta_regime(prices, yearly_change: float) -> MetaRegime:
    """
    Simple meta-regime detection.
    
    Args:
        prices: Recent price history (last ~168 bars)
        yearly_change: Year-over-year % change
    
    Returns:
        MetaRegime classification
    """
    if yearly_change < -40:
        return MetaRegime.CRASH
    elif yearly_change < -20:
        return MetaRegime.BEAR
    elif yearly_change < 0:
        return MetaRegime.BASE
    elif yearly_change < 50:
        return MetaRegime.BULL
    else:
        return MetaRegime.STRONG_BULL


def get_strategy_for_regime(regime: MetaRegime) -> Dict[str, Any]:
    """Get pre-configured strategy for regime."""
    return REGIME_STRATEGIES.get(regime, REGIME_STRATEGIES[MetaRegime.BASE])