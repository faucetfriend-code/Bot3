"""
Technical indicators module for trading bot.
Provides calculations for various technical analysis indicators.
"""

from typing import List, Tuple, Optional
import math
from loguru import logger


def calculate_sma(prices: List[float], period: int) -> float:
    """
    Calculate Simple Moving Average.

    Args:
        prices: List of price values
        period: Period for SMA calculation

    Returns:
        Simple moving average value

    Raises:
        ValueError: If insufficient data or invalid period
    """
    if len(prices) < period:
        raise ValueError(
            f"Insufficient data for SMA calculation. Need {period} prices, got {len(prices)}"
        )

    if period <= 0:
        raise ValueError("Period must be positive")

    return sum(prices[-period:]) / period


def calculate_ema(prices: List[float], period: int) -> float:
    """
    Calculate Exponential Moving Average.

    Args:
        prices: List of price values
        period: Period for EMA calculation

    Returns:
        Exponential moving average value

    Raises:
        ValueError: If insufficient data or invalid period
    """
    if len(prices) < period:
        raise ValueError(
            f"Insufficient data for EMA calculation. Need {period} prices, got {len(prices)}"
        )

    if period <= 0:
        raise ValueError("Period must be positive")

    # Calculate multiplier
    multiplier = 2 / (period + 1)

    # Start with SMA for first value
    ema = prices[0]

    # Calculate EMA for remaining values
    for price in prices[1:]:
        ema = (price * multiplier) + (ema * (1 - multiplier))

    return ema


def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """
    Calculate Relative Strength Index.

    Args:
        prices: List of price values
        period: Period for RSI calculation (default: 14)

    Returns:
        RSI value between 0 and 100

    Raises:
        ValueError: If insufficient data or invalid period
    """
    if len(prices) < period + 1:
        raise ValueError(
            f"Insufficient data for RSI calculation. Need {period + 1} prices, got {len(prices)}"
        )

    if period <= 0:
        raise ValueError("Period must be positive")

    gains = []
    losses = []

    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


def calculate_atr(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> float:
    """
    Calculate Average True Range.

    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of close prices
        period: Period for ATR calculation (default: 14)

    Returns:
        Average True Range value

    Raises:
        ValueError: If data arrays have different lengths or insufficient data
    """
    if len(highs) != len(lows) or len(highs) != len(closes):
        raise ValueError("High, low, and close arrays must have the same length")

    if len(highs) < period + 1:
        raise ValueError(
            f"Insufficient data for ATR calculation. Need {period + 1} periods, got {len(highs)}"
        )

    if period <= 0:
        raise ValueError("Period must be positive")

    true_ranges = []

    for i in range(1, len(highs)):
        # True Range = max(high - low, |high - prev_close|, |low - prev_close|)
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])

        true_range = max(tr1, tr2, tr3)
        true_ranges.append(true_range)

    # Calculate ATR as simple moving average of true ranges
    return sum(true_ranges[-period:]) / period


def calculate_bollinger_bands(
    prices: List[float], period: int = 20, std_dev: float = 2.0
) -> Tuple[float, float, float]:
    """
    Calculate Bollinger Bands.

    Args:
        prices: List of price values
        period: Period for moving average (default: 20)
        std_dev: Standard deviation multiplier (default: 2.0)

    Returns:
        Tuple of (upper_band, middle_band, lower_band)

    Raises:
        ValueError: If insufficient data or invalid parameters
    """
    if len(prices) < period:
        raise ValueError(
            f"Insufficient data for Bollinger Bands. Need {period} prices, got {len(prices)}"
        )

    if period <= 0 or std_dev <= 0:
        raise ValueError("Period and std_dev must be positive")

    # Calculate middle band (SMA)
    middle_band = calculate_sma(prices, period)

    # Calculate standard deviation
    recent_prices = prices[-period:]
    variance = sum((price - middle_band) ** 2 for price in recent_prices) / period
    std_deviation = math.sqrt(variance)

    # Calculate upper and lower bands
    upper_band = middle_band + (std_deviation * std_dev)
    lower_band = middle_band - (std_deviation * std_dev)

    return upper_band, middle_band, lower_band


def calculate_macd(
    prices: List[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> Tuple[float, float, float]:
    """
    Calculate MACD (Moving Average Convergence Divergence).

    Args:
        prices: List of price values
        fast_period: Fast EMA period (default: 12)
        slow_period: Slow EMA period (default: 26)
        signal_period: Signal line EMA period (default: 9)

    Returns:
        Tuple of (macd_line, signal_line, histogram)

    Raises:
        ValueError: If insufficient data or invalid parameters
    """
    if fast_period <= 0 or slow_period <= 0 or signal_period <= 0:
        raise ValueError("All periods must be positive")

    if fast_period >= slow_period:
        raise ValueError("Fast period must be less than slow period")

    min_periods = max(fast_period, slow_period) + signal_period
    if len(prices) < min_periods:
        raise ValueError(
            f"Insufficient data for MACD. Need {min_periods} prices, got {len(prices)}"
        )

    min_periods = max(fast_period, slow_period) + signal_period

    # Calculate fast and slow EMAs
    fast_ema = calculate_ema(prices, fast_period)
    slow_ema = calculate_ema(prices, slow_period)

    # Calculate MACD line
    macd_line = fast_ema - slow_ema

    # For signal line, we need historical MACD values
    # This is a simplified implementation - in practice you'd calculate
    # MACD for each period in the series
    macd_values = []
    for i in range(max(fast_period, slow_period) - 1, len(prices)):
        fast_ema_i = calculate_ema(prices[: i + 1], fast_period)
        slow_ema_i = calculate_ema(prices[: i + 1], slow_period)
        macd_values.append(fast_ema_i - slow_ema_i)

    if len(macd_values) < signal_period:
        # Fallback if insufficient data for signal line
        signal_line = macd_line
    else:
        signal_line = calculate_ema(macd_values, signal_period)

    # Calculate histogram
    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram


def calculate_volume_ma(volumes: List[float], period: int = 20) -> float:
    """
    Calculate Volume Moving Average.

    Args:
        volumes: List of volume values
        period: Period for volume MA calculation (default: 20)

    Returns:
        Volume moving average value

    Raises:
        ValueError: If insufficient data or invalid period
    """
    if len(volumes) < period:
        raise ValueError(
            f"Insufficient data for Volume MA. Need {period} volumes, got {len(volumes)}"
        )

    if period <= 0:
        raise ValueError("Period must be positive")

    return sum(volumes[-period:]) / period


def calculate_adx(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> float:
    """
    Calculate Average Directional Index (ADX) for trend strength.

    ADX measures the strength of a trend (regardless of direction) on a scale of 0-100.
    Uses Wilder's smoothing method.

    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of close prices
        period: Period for ADX calculation (default: 14)

    Returns:
        ADX value (0-100 scale):
        - ADX > 25: Strong trend
        - ADX < 20: Weak trend or ranging market
        - ADX 20-25: Indecisive

    Raises:
        ValueError: If data arrays have different lengths or insufficient data
    """
    if len(highs) != len(lows) or len(highs) != len(closes):
        raise ValueError("High, low, and close arrays must have the same length")

    # Need at least period * 2 data points for reliable ADX
    min_data = period * 2 + 1
    if len(highs) < min_data:
        raise ValueError(
            f"Insufficient data for ADX calculation. Need at least {min_data} periods, got {len(highs)}"
        )

    if period <= 0:
        raise ValueError("Period must be positive")

    # Step 1: Calculate True Range (TR), +DM, and -DM
    true_ranges = []
    plus_dms = []
    minus_dms = []

    for i in range(1, len(highs)):
        # True Range = max(high - low, |high - prev_close|, |low - prev_close|)
        tr1 = highs[i] - lows[i]
        tr2 = abs(highs[i] - closes[i - 1])
        tr3 = abs(lows[i] - closes[i - 1])
        true_range = max(tr1, tr2, tr3)
        true_ranges.append(true_range)

        # Directional Movement
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        # +DM: positive when high increases and up_move > down_move
        if up_move > down_move and up_move > 0:
            plus_dm = up_move
        else:
            plus_dm = 0
        plus_dms.append(plus_dm)

        # -DM: positive when low decreases and down_move > up_move
        if down_move > up_move and down_move > 0:
            minus_dm = down_move
        else:
            minus_dm = 0
        minus_dms.append(minus_dm)

    # Step 2: Apply Wilder's smoothing (similar to EMA with alpha = 1/period)
    # First smoothed value = sum of first 'period' values
    smoothed_tr = sum(true_ranges[:period])
    smoothed_plus_dm = sum(plus_dms[:period])
    smoothed_minus_dm = sum(minus_dms[:period])

    # Store smoothed values for DX calculation
    smoothed_trs = [smoothed_tr]
    smoothed_plus_dms = [smoothed_plus_dm]
    smoothed_minus_dms = [smoothed_minus_dm]

    # Continue smoothing for remaining periods: smoothed = prev_smoothed - (prev_smoothed/period) + current
    for i in range(period, len(true_ranges)):
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + true_ranges[i]
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dms[i]
        smoothed_minus_dm = (
            smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dms[i]
        )

        smoothed_trs.append(smoothed_tr)
        smoothed_plus_dms.append(smoothed_plus_dm)
        smoothed_minus_dms.append(smoothed_minus_dm)

    # Step 3: Calculate +DI and -DI
    plus_dis = []
    minus_dis = []

    for i in range(len(smoothed_trs)):
        if smoothed_trs[i] != 0:
            plus_di = (smoothed_plus_dms[i] / smoothed_trs[i]) * 100
            minus_di = (smoothed_minus_dms[i] / smoothed_trs[i]) * 100
        else:
            plus_di = 0
            minus_di = 0

        plus_dis.append(plus_di)
        minus_dis.append(minus_di)

    # Step 4: Calculate DX (Directional Index)
    dx_values = []

    for i in range(len(plus_dis)):
        di_sum = plus_dis[i] + minus_dis[i]
        if di_sum != 0:
            dx = (abs(plus_dis[i] - minus_dis[i]) / di_sum) * 100
        else:
            dx = 0
        dx_values.append(dx)

    # Step 5: Calculate ADX (average of DX)
    # First ADX = average of first 'period' DX values
    if len(dx_values) < period:
        # Not enough data for full ADX calculation
        return sum(dx_values) / len(dx_values) if dx_values else 0

    adx = sum(dx_values[:period]) / period

    # Continue smoothing ADX: adx = ((prev_adx * (period - 1)) + current_dx) / period
    for i in range(period, len(dx_values)):
        adx = ((adx * (period - 1)) + dx_values[i]) / period

    return adx
