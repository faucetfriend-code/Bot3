"""
Unit tests for technical indicators.
"""

import pytest
import sys
import os
import math

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from indicators import (
    calculate_sma, calculate_ema, calculate_rsi, calculate_atr,
    calculate_bollinger_bands, calculate_macd, calculate_volume_ma
)


class TestTechnicalIndicators:
    """Test technical indicator calculations."""

    def test_sma_calculation(self):
        """Test Simple Moving Average calculation."""
        data = [100, 102, 101, 103, 105]
        result = calculate_sma(data, 5)
        expected = sum(data) / len(data)  # 102.2
        assert abs(result - expected) < 0.001

    def test_sma_insufficient_data(self):
        """Test SMA with insufficient data."""
        data = [100, 102]
        with pytest.raises(ValueError, match="Insufficient data"):
            calculate_sma(data, 5)

    def test_sma_invalid_period(self):
        """Test SMA with invalid period."""
        data = [100, 102, 101, 103, 105]
        with pytest.raises(ValueError, match="Period must be positive"):
            calculate_sma(data, 0)

    def test_ema_calculation(self):
        """Test Exponential Moving Average calculation."""
        data = [100, 102, 101, 103, 105]
        result = calculate_ema(data, 5)
        # Should be a valid float
        assert isinstance(result, float)
        assert result > 0

    def test_ema_insufficient_data(self):
        """Test EMA with insufficient data."""
        data = [100, 102]
        with pytest.raises(ValueError, match="Insufficient data"):
            calculate_ema(data, 5)

    def test_rsi_calculation(self):
        """Test RSI calculation."""
        # Test data that is steadily increasing (should give RSI = 100)
        data = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114]
        result = calculate_rsi(data, 14)
        assert result == 100.0  # All gains = RSI 100

    def test_rsi_insufficient_data(self):
        """Test RSI with insufficient data."""
        data = [100, 102]
        with pytest.raises(ValueError, match="Insufficient data"):
            calculate_rsi(data, 14)

    def test_rsi_all_gains(self):
        """Test RSI with all gains (should be 100)."""
        data = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115]
        result = calculate_rsi(data, 14)
        assert abs(result - 100.0) < 0.001

    def test_rsi_all_losses(self):
        """Test RSI with all losses (should be 0)."""
        data = [115, 114, 113, 112, 111, 110, 109, 108, 107, 106, 105, 104, 103, 102, 101, 100]
        result = calculate_rsi(data, 14)
        assert result == 0.0

    def test_atr_calculation(self):
        """Test Average True Range calculation."""
        highs = [105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119]
        lows = [95, 96, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
        closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114]

        result = calculate_atr(highs, lows, closes, 14)
        assert isinstance(result, float)
        assert result > 0

    def test_atr_insufficient_data(self):
        """Test ATR with insufficient data."""
        highs = [105, 106]
        lows = [95, 96]
        closes = [100, 101]
        with pytest.raises(ValueError, match="Insufficient data"):
            calculate_atr(highs, lows, closes, 14)

    def test_atr_mismatched_arrays(self):
        """Test ATR with mismatched array lengths."""
        highs = [105, 106, 107]
        lows = [95, 96]  # Different length
        closes = [100, 101, 102]
        with pytest.raises(ValueError, match="must have the same length"):
            calculate_atr(highs, lows, closes, 14)

    def test_bollinger_bands_calculation(self):
        """Test Bollinger Bands calculation."""
        data = [100, 102, 101, 103, 105, 104, 106, 108, 107, 109,
                111, 110, 112, 114, 113, 115, 117, 116, 118, 120]
        upper, middle, lower = calculate_bollinger_bands(data, 20, 2)

        assert upper > middle > lower
        assert all(isinstance(x, float) for x in [upper, middle, lower])

    def test_bollinger_bands_insufficient_data(self):
        """Test Bollinger Bands with insufficient data."""
        data = [100, 102]
        with pytest.raises(ValueError, match="Insufficient data"):
            calculate_bollinger_bands(data, 20, 2)

    def test_macd_calculation(self):
        """Test MACD calculation."""
        # Need at least 35 data points for MACD (26 + 9)
        data = [100 + i for i in range(40)]  # 40 steadily increasing prices
        macd_line, signal_line, histogram = calculate_macd(data)

        assert isinstance(macd_line, float)
        assert isinstance(signal_line, float)
        assert isinstance(histogram, float)

    def test_macd_insufficient_data(self):
        """Test MACD with insufficient data."""
        data = [100, 102]
        with pytest.raises(ValueError, match="Insufficient data"):
            calculate_macd(data)

    def test_macd_invalid_periods(self):
        """Test MACD with invalid periods."""
        data = [100] * 40  # Enough data
        with pytest.raises(ValueError, match="must be positive"):
            calculate_macd(data, fast_period=0)

        with pytest.raises(ValueError, match="must be less than"):
            calculate_macd(data, fast_period=26, slow_period=12)

    def test_volume_ma_calculation(self):
        """Test Volume Moving Average calculation."""
        volumes = [1000, 1200, 1100, 1300, 1500]
        result = calculate_volume_ma(volumes, 5)
        expected = sum(volumes) / len(volumes)
        assert abs(result - expected) < 0.001

    def test_volume_ma_insufficient_data(self):
        """Test Volume MA with insufficient data."""
        volumes = [1000, 1200]
        with pytest.raises(ValueError, match="Insufficient data"):
            calculate_volume_ma(volumes, 5)
