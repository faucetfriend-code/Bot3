"""
Unit tests for MarketRegimeDetector component.

Tests market regime classification and volatility analysis.
"""

import pytest
from unittest.mock import patch
from trading_bot_v2.market_regime import MarketRegimeDetector, MarketRegime

#: The five values this detector can emit. MarketRegime also carries the
#: VOL_* values of the realized-volatility taxonomy, which only
#: VolatilityRegimeDetector produces (REGIME_MODE=volatility).
ADX_REGIMES = (
    MarketRegime.TRENDING_STRONG,
    MarketRegime.TRENDING_MODERATE,
    MarketRegime.RANGING_VOLATILE,
    MarketRegime.RANGING_CALM,
    MarketRegime.INDECISIVE,
)


class TestMarketRegimeDetector:
    """Test suite for MarketRegimeDetector."""

    @pytest.fixture
    def regime_detector(self):
        """Create MarketRegimeDetector instance with test parameters."""
        return MarketRegimeDetector(
            adx_trending_threshold=28.0,
            adx_ranging_threshold=22.0,
            adx_moderate_threshold=22.0,
            volatility_high_percentile=75.0,
        )

    @pytest.fixture
    def sample_market_data(self):
        """Sample OHLCV market data for testing (30 candles - meets min 29 requirement)."""
        return {
            "high": [
                1.05, 1.08, 1.06, 1.09, 1.07,
                1.10, 1.08, 1.11, 1.09, 1.12,
                1.10, 1.13, 1.11, 1.14, 1.12,
                1.15, 1.13, 1.16, 1.14, 1.17,
                1.15, 1.18, 1.16, 1.19, 1.17,
                1.20, 1.18, 1.21, 1.19, 1.22,
            ],
            "low": [
                1.02, 1.05, 1.03, 1.06, 1.04,
                1.07, 1.05, 1.08, 1.06, 1.09,
                1.07, 1.10, 1.08, 1.11, 1.09,
                1.12, 1.10, 1.13, 1.11, 1.14,
                1.12, 1.15, 1.13, 1.16, 1.14,
                1.17, 1.15, 1.18, 1.16, 1.19,
            ],
            "close": [
                1.04, 1.07, 1.05, 1.08, 1.06,
                1.09, 1.07, 1.10, 1.08, 1.11,
                1.09, 1.12, 1.10, 1.13, 1.11,
                1.14, 1.12, 1.15, 1.13, 1.16,
                1.14, 1.17, 1.15, 1.18, 1.16,
                1.19, 1.17, 1.20, 1.18, 1.21,
            ],
            "volume": [
                1000, 1100, 1050, 1150, 1080,
                1180, 1120, 1200, 1140, 1220,
                1160, 1240, 1180, 1260, 1200,
                1280, 1220, 1300, 1240, 1320,
                1260, 1340, 1280, 1360, 1300,
                1380, 1320, 1400, 1340, 1420,
            ],
        }

    def test_initialization(self, regime_detector):
        """Test proper initialization with custom parameters."""
        assert regime_detector.adx_trending == 28.0
        assert regime_detector.adx_ranging == 22.0
        assert regime_detector.adx_moderate == 22.0
        assert regime_detector.volatility_percentile == 75.0
        assert regime_detector._regime_cache == {}
        assert regime_detector._cache_ttl_hours == 1

    def test_detect_regime_trending_strong(self, regime_detector, sample_market_data):
        """Test detection of strong trending market (ADX > 28)."""
        # Mock ADX calculation to return high value
        with patch("trading_bot_v2.market_regime.calculate_adx", return_value=35.0):
            with patch("trading_bot_v2.market_regime.calculate_atr"):
                with patch("trading_bot_v2.market_regime.calculate_bollinger_bands"):
                    regime = regime_detector.detect_regime(sample_market_data)
                    assert regime == MarketRegime.TRENDING_STRONG

    def test_detect_regime_trending_moderate(self, regime_detector, sample_market_data):
        """Test detection of moderate trending market (22 < ADX ≤ 28)."""
        with patch("trading_bot_v2.market_regime.calculate_adx", return_value=25.0):
            with patch("trading_bot_v2.market_regime.calculate_atr"):
                with patch("trading_bot_v2.market_regime.calculate_bollinger_bands"):
                    regime = regime_detector.detect_regime(sample_market_data)
                    assert regime == MarketRegime.TRENDING_MODERATE

    def test_detect_regime_ranging_volatile(self, regime_detector, sample_market_data):
        """Test detection of ranging volatile market."""
        with patch("trading_bot_v2.market_regime.calculate_adx", return_value=18.0):
            with patch.object(
                regime_detector, "_calculate_volatility_score", return_value=80.0
            ):
                regime = regime_detector.detect_regime(sample_market_data)
                assert regime == MarketRegime.RANGING_VOLATILE

    def test_detect_regime_ranging_calm(self, regime_detector, sample_market_data):
        """Test detection of ranging calm market."""
        with patch("trading_bot_v2.market_regime.calculate_adx", return_value=18.0):
            with patch.object(
                regime_detector, "_calculate_volatility_score", return_value=60.0
            ):
                regime = regime_detector.detect_regime(sample_market_data)
                assert regime == MarketRegime.RANGING_CALM

    def test_detect_regime_insufficient_data(self, regime_detector):
        """Test handling of insufficient data."""
        insufficient_data = {
            "high": [1.05, 1.08],
            "low": [1.02, 1.05],
            "close": [1.04, 1.07],
        }

        regime = regime_detector.detect_regime(insufficient_data)
        assert regime == MarketRegime.INDECISIVE

    def test_detect_regime_missing_keys(self, regime_detector):
        """Test handling of missing required data keys."""
        incomplete_data = {
            "high": [1.05, 1.08],
            "low": [1.02, 1.05],
            # Missing 'close'
        }

        with pytest.raises(ValueError, match="market_data missing required key"):
            regime_detector.detect_regime(incomplete_data)

    def test_detect_regime_cached(self, regime_detector, sample_market_data):
        """Test cached regime detection."""
        symbol = "SUI"

        # First call should calculate
        with patch.object(
            regime_detector, "detect_regime", return_value=MarketRegime.TRENDING_STRONG
        ) as mock_detect:
            regime1 = regime_detector.detect_regime_cached(symbol, sample_market_data)
            assert mock_detect.call_count == 1
            assert regime1 == MarketRegime.TRENDING_STRONG

        # Second call should use cache
        with patch.object(regime_detector, "detect_regime") as mock_detect:
            regime2 = regime_detector.detect_regime_cached(symbol, sample_market_data)
            assert mock_detect.call_count == 0  # Should not be called again
            assert regime2 == MarketRegime.TRENDING_STRONG

    def test_regime_change_confirmation(self, regime_detector, sample_market_data):
        """Test regime change requires confirmation."""
        symbol = "SUI"

        # First detection
        with patch.object(
            regime_detector,
            "_detect_regime_with_confirmation",
            return_value=MarketRegime.TRENDING_STRONG,
        ):
            regime1 = regime_detector.detect_regime_cached(symbol, sample_market_data)
            assert regime1 == MarketRegime.TRENDING_STRONG

        # Attempt regime change - should require confirmation
        with patch.object(
            regime_detector,
            "_detect_regime_with_confirmation",
            return_value=MarketRegime.RANGING_VOLATILE,
        ):
            regime2 = regime_detector.detect_regime_cached(symbol, sample_market_data)
            # Should still return old regime until confirmed
            assert regime2 == MarketRegime.TRENDING_STRONG

    def test_calculate_volatility_score(self, regime_detector):
        """Test volatility score calculation."""
        highs = [
            1.05,
            1.08,
            1.06,
            1.09,
            1.07,
            1.10,
            1.08,
            1.11,
            1.09,
            1.12,
            1.10,
            1.13,
            1.11,
            1.14,
            1.12,
            1.15,
            1.13,
            1.16,
            1.14,
            1.17,
        ]
        lows = [
            1.02,
            1.05,
            1.03,
            1.06,
            1.04,
            1.07,
            1.05,
            1.08,
            1.06,
            1.09,
            1.07,
            1.10,
            1.08,
            1.11,
            1.09,
            1.12,
            1.10,
            1.13,
            1.11,
            1.14,
        ]
        closes = [
            1.04,
            1.07,
            1.05,
            1.08,
            1.06,
            1.09,
            1.07,
            1.10,
            1.08,
            1.11,
            1.09,
            1.12,
            1.10,
            1.13,
            1.11,
            1.14,
            1.12,
            1.15,
            1.13,
            1.16,
        ]

        score = regime_detector._calculate_volatility_score(highs, lows, closes)

        # Score should be between 0 and 100
        assert 0 <= score <= 100

    def test_get_active_strategies_trending_strong(self, regime_detector):
        """Test strategy mapping for trending strong regime."""
        strategies = regime_detector.get_active_strategies(MarketRegime.TRENDING_STRONG)
        # Updated Jan 2026: MomentumScalping added to TRENDING_STRONG
        assert strategies == ["MACrossover", "MomentumScalping"]

    def test_get_active_strategies_ranging_volatile(self, regime_detector):
        """Test strategy mapping for ranging volatile regime."""
        strategies = regime_detector.get_active_strategies(
            MarketRegime.RANGING_VOLATILE
        )
        assert strategies == ["GridTrading"]

    def test_get_active_strategies_ranging_calm(self, regime_detector):
        """Test strategy mapping for ranging calm regime."""
        strategies = regime_detector.get_active_strategies(MarketRegime.RANGING_CALM)
        assert strategies == ["MeanReversion", "GridTrading"]

    def test_get_active_strategies_indecisive(self, regime_detector):
        """Test strategy mapping for indecisive regime."""
        strategies = regime_detector.get_active_strategies(MarketRegime.INDECISIVE)
        assert strategies == ["LiquidationCapture"]

    def test_is_grid_allowed(self, regime_detector):
        """Test grid trading permission by regime."""
        # Grid allowed in ranging regimes
        assert regime_detector.is_grid_allowed(MarketRegime.RANGING_VOLATILE)
        assert regime_detector.is_grid_allowed(MarketRegime.RANGING_CALM)
        assert regime_detector.is_grid_allowed(MarketRegime.INDECISIVE)

        # Grid not allowed in trending regimes
        assert not regime_detector.is_grid_allowed(MarketRegime.TRENDING_STRONG)
        assert not regime_detector.is_grid_allowed(MarketRegime.TRENDING_MODERATE)

    def test_get_strategy_weights(self, regime_detector):
        """Test strategy weight allocation by regime (Jul 2026 weights with OrderBookImbalance + SessionRangeBreakout + CalendarFlow + VWAPPullback overlays)."""
        weights_volatile = regime_detector.get_strategy_weights(
            MarketRegime.RANGING_VOLATILE
        )
        assert weights_volatile == {
            "GridTrading": 0.8,
            "OrderBookImbalance": 0.2,
            "SessionRangeBreakout": 0.15,
            "CalendarFlow": 0.1,
            "VWAPPullback": 0.15,
        }

        weights_calm = regime_detector.get_strategy_weights(MarketRegime.RANGING_CALM)
        assert weights_calm == {
            "MeanReversion": 0.6,
            "GridTrading": 0.2,
            "OrderBookImbalance": 0.2,
            "SessionRangeBreakout": 0.15,
            "CalendarFlow": 0.1,
            "VWAPPullback": 0.15,
        }

        weights_trending = regime_detector.get_strategy_weights(
            MarketRegime.TRENDING_STRONG
        )
        assert weights_trending == {
            "MACrossover": 0.5,
            "MomentumScalping": 0.3,
            "OrderBookImbalance": 0.2,
            "SessionRangeBreakout": 0.15,
            "CalendarFlow": 0.1,
            "VWAPPullback": 0.25,
        }

        # SessionRangeBreakout, CalendarFlow and VWAPPullback are overlays
        # in all five ADX regimes. MarketRegime also carries the VOL_*
        # values of the realized-volatility taxonomy
        # (REGIME_MODE=volatility), which this detector never emits and
        # has no weights for - see volatility_regime.VolatilityRegimeDetector.
        for regime in ADX_REGIMES:
            regime_weights = regime_detector.get_strategy_weights(regime)
            assert "SessionRangeBreakout" in regime_weights
            assert "CalendarFlow" in regime_weights
            assert "VWAPPullback" in regime_weights

    def test_foreign_taxonomy_regimes_get_no_weights(self, regime_detector):
        """The ADX detector does not claim to map another taxonomy."""
        for regime in MarketRegime:
            if regime in ADX_REGIMES:
                continue
            assert regime_detector.get_strategy_weights(regime) == {}
            assert regime_detector.get_active_strategies(regime) == []

    def test_hash_market_data(self, regime_detector, sample_market_data):
        """Test market data hashing for cache invalidation."""
        hash1 = regime_detector._hash_market_data(sample_market_data)
        hash2 = regime_detector._hash_market_data(sample_market_data)

        # Same data should produce same hash
        assert hash1 == hash2

        # Different data should produce different hash
        modified_data = sample_market_data.copy()
        modified_data["close"] = modified_data["close"][:-1]  # Remove last element
        hash3 = regime_detector._hash_market_data(modified_data)
        assert hash3 != hash1
