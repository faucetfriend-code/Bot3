"""
Market Regime Detection Module

Uses ADX and volatility metrics to classify market conditions into 4 regimes:
- TRENDING_STRONG: ADX > 30 (use MA crossover - trend following not implemented)
- TRENDING_MODERATE: ADX 25-30 (use MA crossover)
- RANGING_VOLATILE: ADX ≤ 25, high volatility (use grid trading)
- RANGING_CALM: ADX ≤ 25, low volatility (use mean reversion)
- INDECISIVE: Transitional (use liquidation capture)
"""

from enum import Enum
from typing import List, Dict, Any, Optional
from loguru import logger
from .indicators import (
    calculate_adx,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_sma,
)


class MarketRegime(Enum):
    """Market regime classification based on ADX and volatility."""

    TRENDING_STRONG = "trending_strong"  # ADX > 30
    TRENDING_MODERATE = "trending_moderate"  # 25 < ADX ≤ 30
    RANGING_VOLATILE = "ranging_volatile"  # ADX ≤ 25, high volatility (>65%)
    RANGING_CALM = "ranging_calm"  # ADX ≤ 25, low volatility
    INDECISIVE = "indecisive"  # transitional / choppy


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
        adx_trending_threshold: float = 30.0,  # Was 28.0 - require stronger trend
        adx_ranging_threshold: float = 25.0,  # Was 22.0 - more ranging markets
        adx_moderate_threshold: float = 25.0,  # Was 22.0 - aligned with ranging
        volatility_high_percentile: float = 65.0,  # Was 75.0 - easier volatile classification
        adx_period: int = 14,
        atr_period: int = 14,
        bb_period: int = 20,
    ):
        """
        Initialize MarketRegimeDetector.

        Args:
            adx_trending_threshold: ADX above this = TRENDING_STRONG (default: 28)
            adx_ranging_threshold: ADX below this = ranging market (default: 22)
            adx_moderate_threshold: ADX above this but below trending = TRENDING_MODERATE (default: 22)
            volatility_high_percentile: Percentile for high volatility classification (default: 75)
            adx_period: Period for ADX calculation (default: 14)
            atr_period: Period for ATR calculation (default: 14)
            bb_period: Period for Bollinger Bands calculation (default: 20)
        """
        self.adx_trending = adx_trending_threshold
        self.adx_ranging = adx_ranging_threshold
        self.adx_moderate = adx_moderate_threshold
        self.volatility_percentile = volatility_high_percentile
        self.adx_period = adx_period
        self.atr_period = atr_period
        self.bb_period = bb_period

        # Regime caching to prevent unnecessary recalculation
        self._regime_cache: Dict[str, Dict] = {}
        self._pending_regime_changes: Dict[str, Dict] = {}
        self._cache_ttl_hours = 4  # Recalculate every 4 hours

        # Trend direction caching (for partial grid unwind decisions)
        self._trend_direction_cache: Dict[str, Dict] = {}
        self._trend_cache_ttl_minutes = 5  # Trend direction cache TTL

        logger.info(
            f"MarketRegimeDetector initialized: "
            f"ADX trending={adx_trending_threshold}, "
            f"moderate={adx_moderate_threshold}, "
            f"ranging={adx_ranging_threshold}, "
            f"volatility percentile={volatility_high_percentile}%, "
            f"cache_ttl={self._cache_ttl_hours}h"
        )

    def detect_regime(self, market_data: Dict[str, List[float]]) -> MarketRegime:
        """
        Detect current market regime from market data.

        Regime classification (Updated Jan 2026 - loosened for more trades):
        - ADX > 30: TRENDING_STRONG
        - 25 < ADX ≤ 30: TRENDING_MODERATE
        - ADX ≤ 25 + high volatility (>65%): RANGING_VOLATILE
        - ADX ≤ 25 + low volatility: RANGING_CALM
        - Transitional/choppy: INDECISIVE

        Args:
            market_data: Dictionary with keys 'high', 'low', 'close', 'volume'
                        Each key maps to a list of float values

        Returns:
            MarketRegime enum value

        Raises:
            ValueError: If market_data is missing required keys or has insufficient data
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

        # Step 1: Calculate ADX
        try:
            adx = calculate_adx(highs, lows, closes, period=self.adx_period)
        except Exception as e:
            logger.error(f"Error calculating ADX: {e}")
            raise

        # Step 2: If ADX indicates strong trend, return TRENDING_STRONG
        if adx > self.adx_trending:
            logger.info(
                f"Regime: TRENDING_STRONG (ADX={adx:.2f} > {self.adx_trending})"
            )
            return MarketRegime.TRENDING_STRONG

        # Step 3: Check for TRENDING_MODERATE (new regime - Jan 2026)
        if self.adx_moderate < adx <= self.adx_trending:
            logger.info(
                f"Regime: TRENDING_MODERATE (ADX={adx:.2f} between "
                f"{self.adx_moderate}-{self.adx_trending})"
            )
            return MarketRegime.TRENDING_MODERATE

        # Step 4: ADX ≤ 22 - market is ranging, determine volatility level
        if adx <= self.adx_ranging:
            try:
                volatility_score = self._calculate_volatility_score(highs, lows, closes)
            except Exception as e:
                logger.error(f"Error calculating volatility: {e}")
                # Default to RANGING_CALM if volatility calculation fails
                return MarketRegime.RANGING_CALM

            # Classify ranging market by volatility
            if volatility_score > self.volatility_percentile:
                logger.info(
                    f"Regime: RANGING_VOLATILE (ADX={adx:.2f} ≤ {self.adx_ranging}, "
                    f"volatility={volatility_score:.1f}% > {self.volatility_percentile}%)"
                )
                return MarketRegime.RANGING_VOLATILE
            else:
                logger.info(
                    f"Regime: RANGING_CALM (ADX={adx:.2f} ≤ {self.adx_ranging}, "
                    f"volatility={volatility_score:.1f}% ≤ {self.volatility_percentile}%)"
                )
                return MarketRegime.RANGING_CALM

        # Step 5: Fallback to INDECISIVE (shouldn't reach here with new logic)
        logger.info(f"Regime: INDECISIVE (ADX={adx:.2f} - transitional)")
        return MarketRegime.INDECISIVE

    def detect_regime_cached(
        self, symbol: str, market_data: Dict[str, List[float]]
    ) -> MarketRegime:
        """
        Detect regime with caching to prevent unnecessary recalculation.

        Only recalculates when:
        1. No cached regime exists for symbol
        2. Cache is older than TTL (4 hours)
        3. Significant data change detected

        Args:
            symbol: Trading symbol
            market_data: OHLCV data dictionary

        Returns:
            Current market regime
        """
        from datetime import datetime

        # Check cache first
        if symbol in self._regime_cache:
            cache_entry = self._regime_cache[symbol]
            cache_age_hours = (
                datetime.now() - cache_entry["timestamp"]
            ).total_seconds() / 3600

            # Return cached regime if still valid
            if cache_age_hours < self._cache_ttl_hours:
                logger.debug(
                    f"Using cached regime for {symbol}: {cache_entry['regime'].value} ({cache_age_hours:.1f}h old)"
                )
                return cache_entry["regime"]

        # Cache miss or expired - check for pending regime change
        new_regime = self._detect_regime_with_confirmation(symbol, market_data)

        # Update cache
        self._regime_cache[symbol] = {
            "regime": new_regime,
            "timestamp": datetime.now(),
            "data_hash": self._hash_market_data(market_data),
        }

        logger.debug(f"Regime recalculated for {symbol}: {new_regime.value}")
        return new_regime

    def _detect_regime_with_confirmation(
        self, symbol: str, market_data: Dict[str, List[float]]
    ) -> MarketRegime:
        """
        Detect regime with confirmation to prevent flicker.
        Requires regime to hold for 2 consecutive checks before accepting change.
        """
        from datetime import datetime

        new_regime = self.detect_regime(market_data)

        # Check if we have a pending regime change
        if symbol in self._pending_regime_changes:
            pending = self._pending_regime_changes[symbol]

            if pending["regime"] == new_regime:
                # Confirmation received - accept change
                if pending["confirmations"] >= 1:  # Require 2 total detections
                    del self._pending_regime_changes[symbol]
                    logger.info(
                        f"Regime change confirmed for {symbol}: {new_regime.value}"
                    )
                    return new_regime
                else:
                    # Increment confirmation count
                    pending["confirmations"] += 1
                    return pending["previous_regime"]
            else:
                # Different regime detected - reset pending change
                del self._pending_regime_changes[symbol]

        # Check if regime changed from cache
        current_regime = self._regime_cache.get(symbol, {}).get("regime")
        if current_regime and current_regime != new_regime:
            # Start pending change process
            self._pending_regime_changes[symbol] = {
                "regime": new_regime,
                "previous_regime": current_regime,
                "confirmations": 1,
                "timestamp": datetime.now(),
            }
            logger.debug(
                f"Regime change pending for {symbol}: {current_regime.value} -> {new_regime.value}"
            )
            return current_regime  # Keep current until confirmed

        # No change or first detection
        return new_regime

    def _hash_market_data(self, market_data: Dict[str, List[float]]) -> str:
        """Create hash of market data for change detection."""
        import hashlib

        data_str = str(sorted(market_data.items()))
        return hashlib.md5(data_str.encode()).hexdigest()

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
            },
            MarketRegime.TRENDING_MODERATE: {
                "MACrossover": 0.4,  # Balanced with momentum
                "MomentumScalping": 0.4,  # Equal weight in moderate trends
                "OrderBookImbalance": 0.2,  # Flow confirmation
            },
            MarketRegime.RANGING_VOLATILE: {
                "GridTrading": 0.8,
                "OrderBookImbalance": 0.2,  # Flow-based overlay
            },
            MarketRegime.RANGING_CALM: {
                "MeanReversion": 0.6,
                "GridTrading": 0.2,
                "OrderBookImbalance": 0.2,  # Flow-based overlay
            },
            MarketRegime.INDECISIVE: {
                "LiquidationCapture": 0.6,  # Conservative approach
                "OrderBookImbalance": 0.4,  # Flow-based (best in choppy markets)
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
        else:
            self._regime_cache.clear()
            self._pending_regime_changes.clear()
            logger.debug("Cleared all regime caches")
