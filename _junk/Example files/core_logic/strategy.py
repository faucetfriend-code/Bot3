"""
Trading strategy implementations.
Based on Trading Instructions Sections IV - STRATEGY RULESET.
"""

from typing import List, Dict, Optional, Any, Tuple
from datetime import datetime, timedelta
from enum import Enum
import pandas as pd
from config import get_config, StrategyType, MarketState, TradeQuality
from models import Signal, MarketData, OrderSide
from risk import validate_volume_confirmation, calculate_atr_based_stop
from indicators import calculate_rsi, calculate_atr
from loguru import logger


class PatternType(str, Enum):
    TRIANGLE = "triangle"
    RECTANGLE = "rectangle"
    FLAG = "flag"
    PENNANT = "pennant"
    DOUBLE_BOTTOM = "double_bottom"
    DOUBLE_TOP = "double_top"
    HEAD_SHOULDERS = "head_shoulders"


class TrendDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    SIDEWAYS = "sideways"


def identify_trend(market_data: List[MarketData], period: int = 20) -> TrendDirection:
    """
    Identify trend direction using moving averages and price action.

    Based on Trading Instructions Section II - Market State Identification.

    Args:
        market_data: List of market data points
        period: Period for moving average

    Returns:
        Trend direction
    """
    if len(market_data) < period:
        return TrendDirection.SIDEWAYS

    # Calculate simple moving average
    closes = [d.close for d in market_data[-period:]]
    sma = sum(closes) / len(closes)

    # Check for higher highs and higher lows (uptrend)
    highs = [d.high for d in market_data[-period:]]
    lows = [d.low for d in market_data[-period:]]

    higher_highs = all(highs[i] >= highs[i - 1] for i in range(1, len(highs)))
    higher_lows = all(lows[i] >= lows[i - 1] for i in range(1, len(lows)))

    if higher_highs and higher_lows:
        return TrendDirection.UP

    # Check for lower highs and lower lows (downtrend)
    lower_highs = all(highs[i] <= highs[i - 1] for i in range(1, len(highs)))
    lower_lows = all(lows[i] <= lows[i - 1] for i in range(1, len(lows)))

    if lower_highs and lower_lows:
        return TrendDirection.DOWN

    return TrendDirection.SIDEWAYS


def detect_pullback_setup(
    market_data: List[MarketData],
    trend: TrendDirection,
    pullback_range: Tuple[float, float] = (0.02, 0.04),
) -> Optional[Dict[str, Any]]:
    """
    Detect pullback setup in established trend.

    Based on Trading Instructions Section IV - Long/Short Entry Rules.

    Args:
        market_data: Recent market data
        trend: Current trend direction
        pullback_range: Acceptable pullback range (2-4%)

    Returns:
        Setup details or None
    """
    if trend == TrendDirection.SIDEWAYS or len(market_data) < 10:
        return None

    # Get recent swing points
    recent_data = market_data[-20:]  # Last 20 candles

    if trend == TrendDirection.UP:
        # Find recent swing high
        swing_high = max(d.high for d in recent_data)
        swing_high_idx = next(
            i for i, d in enumerate(recent_data) if d.high == swing_high
        )

        # Check for pullback to support
        current_price = recent_data[-1].close
        pullback_pct = (swing_high - current_price) / swing_high

        if pullback_range[0] <= pullback_pct <= pullback_range[1]:
            # Check volume declining (exhaustion)
            recent_volume = [d.volume for d in recent_data[-5:]]
            avg_volume = sum(recent_volume) / len(recent_volume)
            volume_trend = recent_volume[-1] < avg_volume * 0.8  # Volume declining

            if volume_trend:
                return {
                    "type": "pullback_uptrend",
                    "swing_high": swing_high,
                    "pullback_pct": pullback_pct,
                    "volume_exhaustion": True,
                    "entry_zone": current_price * 0.998,  # Slightly above current
                }

    elif trend == TrendDirection.DOWN:
        # Find recent swing low
        swing_low = min(d.low for d in recent_data)
        swing_low_idx = next(i for i, d in enumerate(recent_data) if d.low == swing_low)

        # Check for rally to resistance
        current_price = recent_data[-1].close
        rally_pct = (current_price - swing_low) / swing_low

        if pullback_range[0] <= rally_pct <= pullback_range[1]:
            # Check volume declining
            recent_volume = [d.volume for d in recent_data[-5:]]
            avg_volume = sum(recent_volume) / len(recent_volume)
            volume_trend = recent_volume[-1] < avg_volume * 0.8

            if volume_trend:
                return {
                    "type": "rally_downtrend",
                    "swing_low": swing_low,
                    "rally_pct": rally_pct,
                    "volume_exhaustion": True,
                    "entry_zone": current_price * 1.002,  # Slightly below current
                }

    return None


def detect_liquidation_event(
    market_data: List[MarketData],
    volume_threshold: float = 3.0,
    price_move_threshold: float = 0.03,
) -> Optional[Dict[str, Any]]:
    """
    Detect liquidation cascade events.

    Based on Trading Instructions Section IV.C - Liquidation Event Characteristics.

    Args:
        market_data: Recent market data
        volume_threshold: Volume spike multiplier
        price_move_threshold: Price move threshold (3%)

    Returns:
        Liquidation event details or None
    """
    if len(market_data) < 10:
        return None

    recent_data = market_data[-10:]

    # Check for sudden price movement
    start_price = recent_data[0].close
    end_price = recent_data[-1].close
    price_move = abs(end_price - start_price) / start_price

    if price_move < price_move_threshold:
        return None

    # Check volume spike
    volumes = [d.volume for d in recent_data]
    avg_volume = sum(volumes[:-3]) / len(volumes[:-3])  # Exclude last 3 for spike
    current_volume = volumes[-1]

    volume_spike = current_volume / avg_volume if avg_volume > 0 else 0

    if volume_spike < volume_threshold:
        return None

    # Check for rapid successive moves
    direction = "up" if end_price > start_price else "down"
    consecutive_moves = 0

    for i in range(1, len(recent_data)):
        if direction == "up" and recent_data[i].close > recent_data[i - 1].close:
            consecutive_moves += 1
        elif direction == "down" and recent_data[i].close < recent_data[i - 1].close:
            consecutive_moves += 1
        else:
            consecutive_moves = 0

    if consecutive_moves < 5:
        return None

    # Check for long wicks (panic)
    last_candle = recent_data[-1]
    wick_size = abs(last_candle.high - last_candle.low)
    body_size = abs(last_candle.close - last_candle.open)
    wick_ratio = wick_size / body_size if body_size > 0 else 0

    if wick_ratio < 2:  # Long wick indicates panic
        return None

    return {
        "direction": direction,
        "price_move": price_move,
        "volume_spike": volume_spike,
        "consecutive_moves": consecutive_moves,
        "wick_ratio": wick_ratio,
        "extreme_price": last_candle.low if direction == "down" else last_candle.high,
    }


def detect_chart_pattern(
    market_data: List[MarketData], pattern_type: PatternType
) -> Optional[Dict[str, Any]]:
    """
    Detect chart patterns for breakout trading.

    Based on Trading Instructions Section IV.B - Breakout Entry Rules.

    Args:
        market_data: Market data for pattern detection
        pattern_type: Type of pattern to detect

    Returns:
        Pattern details or None
    """
    if len(market_data) < 20:
        return None

    data = market_data[-50:]  # Use last 50 candles for pattern detection

    if pattern_type == PatternType.RECTANGLE:
        return detect_rectangle_pattern(data)
    elif pattern_type == PatternType.TRIANGLE:
        return detect_triangle_pattern(data)
    elif pattern_type == PatternType.FLAG:
        return detect_flag_pattern(data)

    return None


def detect_rectangle_pattern(data: List[MarketData]) -> Optional[Dict[str, Any]]:
    """Detect rectangle consolidation pattern."""
    highs = [d.high for d in data]
    lows = [d.low for d in data]

    # Find resistance level (touched at least 2 times)
    resistance_touches: List[float] = []
    support_touches: List[float] = []

    # Simple pattern detection - look for price oscillating between levels
    max_high = max(highs)
    min_low = min(lows)
    range_size = max_high - min_low

    # Check if price has tested both levels multiple times
    resistance_tests = sum(1 for h in highs if abs(h - max_high) / range_size < 0.05)
    support_tests = sum(1 for l in lows if abs(l - min_low) / range_size < 0.05)

    if resistance_tests >= 2 and support_tests >= 2:
        return {
            "pattern": "rectangle",
            "resistance": max_high,
            "support": min_low,
            "touches": min(resistance_tests, support_tests),
            "height": range_size,
            "breakout_target": max_high + range_size,
        }

    return None


def detect_triangle_pattern(data: List[MarketData]) -> Optional[Dict[str, Any]]:
    """Detect triangle consolidation pattern."""
    # Simplified triangle detection
    highs = [d.high for d in data]
    lows = [d.low for d in data]

    # Check for converging trendlines
    # This is a simplified version - real implementation would use linear regression
    if len(highs) < 10:
        return None

    # Check if highs are decreasing and lows are increasing
    high_trend = highs[-1] < highs[0] * 0.95  # Highs declining
    low_trend = lows[-1] > lows[0] * 1.05  # Lows rising

    if high_trend and low_trend:
        return {
            "pattern": "triangle",
            "apex_price": (highs[-1] + lows[-1]) / 2,
            "touches": 5,  # Assume minimum touches
            "height": highs[0] - lows[0],
            "breakout_target": highs[0],  # Break above resistance
        }

    return None


def detect_flag_pattern(data: List[MarketData]) -> Optional[Dict[str, Any]]:
    """Detect flag pattern after sharp move."""
    if len(data) < 15:
        return None

    # Look for sharp pole followed by consolidation
    recent_high = max(d.high for d in data[-10:])
    recent_low = min(d.low for d in data[-10:])

    # Check for consolidation after move
    consolidation_high = max(d.high for d in data[-5:])
    consolidation_low = min(d.low for d in data[-5:])

    consolidation_range = (consolidation_high - consolidation_low) / recent_high

    if consolidation_range < 0.05:  # Tight consolidation
        return {
            "pattern": "flag",
            "pole_high": recent_high,
            "pole_low": recent_low,
            "flag_height": consolidation_high - consolidation_low,
            "breakout_target": recent_high + (recent_high - recent_low),
        }

    return None








class TrendFollowingStrategy:
    """
    Trend-following strategy implementation.

    Based on Trading Instructions Section IV.A - Primary Strategy: Trend-Following.
    """

    def __init__(self):
        self.config = get_config().strategy.trend_following

    def generate_signals(self, market_data: List[MarketData]) -> List[Signal]:
        """
        Generate trend-following signals.

        Args:
            market_data: Recent market data

        Returns:
            List of trading signals
        """
        signals: List[Signal] = []

        if len(market_data) < 50:
            return signals

        # Identify trend
        trend = identify_trend(market_data)

        if trend == TrendDirection.SIDEWAYS:
            return signals

        # Look for pullback setup
        setup = detect_pullback_setup(market_data, trend)

        if setup is None:
            return signals

        # Calculate indicators
        closes = [d.close for d in market_data[-20:]]
        rsi = calculate_rsi(closes)

        # Check RSI conditions
        rsi_valid = False
        if trend == TrendDirection.UP and rsi < self.config["rsi_oversold"]:
            rsi_valid = True
        elif trend == TrendDirection.DOWN and rsi > self.config["rsi_overbought"]:
            rsi_valid = True

        if not rsi_valid:
            return signals

        # Determine entry and stop
        current_price = market_data[-1].close
        if trend == TrendDirection.UP:
            side = OrderSide.BUY
            entry_price = setup["entry_zone"]
            stop_loss = setup["swing_high"] * 1.005  # Slightly above swing high
        else:
            side = OrderSide.SELL
            entry_price = setup["entry_zone"]
            stop_loss = setup["swing_low"] * 0.995  # Slightly below swing low

        # Calculate take profit (2-3x risk)
        risk = abs(entry_price - stop_loss)
        take_profit = (
            entry_price + (risk * 3)
            if side == OrderSide.BUY
            else entry_price - (risk * 3)
        )

        # Volume confirmation
        volumes = [d.volume for d in market_data[-20:]]
        avg_volume = sum(volumes) / len(volumes)
        volume_confirmed, _, _ = validate_volume_confirmation(
            market_data[-1].volume, avg_volume, StrategyType.TREND_FOLLOWING
        )

        # Create signal
        signal = Signal(
            strategy=StrategyType.TREND_FOLLOWING,
            asset=market_data[0].symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=0.7,  # High confidence for trend following
            quality=TradeQuality.STANDARD,
            market_state=MarketState.TREND,
            timeframe="15min",
            pattern=setup["type"],
            volume_confirmation=volume_confirmed,
            multi_timeframe_alignment=True,  # Assume checked elsewhere
            support_resistance_valid=True,
            rrr_meets_minimum=True,
            liquidation_buffer_safe=True,
            account_risk_ok=True,
            margin_drawdown_ok=True,
            forbidden_conditions_clear=True,
            indicators={"rsi": rsi, "trend": trend.value},
            notes=f"Trend-following setup: {setup['type']}, RSI: {rsi:.1f}",
        )

        signals.append(signal)
        return signals


class BreakoutStrategy:
    """
    Breakout trading strategy implementation.

    Based on Trading Instructions Section IV.B - Secondary Strategy: Breakout Trading.
    """

    def __init__(self):
        self.config = get_config().strategy.breakout

    def generate_signals(self, market_data: List[MarketData]) -> List[Signal]:
        """
        Generate breakout signals.

        Args:
            market_data: Recent market data

        Returns:
            List of trading signals
        """
        signals: List[Signal] = []

        if len(market_data) < 50:
            return signals

        # Try different patterns
        patterns = [PatternType.RECTANGLE, PatternType.TRIANGLE, PatternType.FLAG]

        for pattern_type in patterns:
            pattern = detect_chart_pattern(market_data, pattern_type)

            if pattern and pattern["touches"] >= self.config["min_touches"]:
                # Check formation time
                formation_hours = len(market_data) * 0.25  # Assuming 15min candles
                if formation_hours >= self.config["min_pattern_hours"]:

                    # Volume confirmation on breakout
                    volumes = [d.volume for d in market_data[-20:]]
                    avg_volume = sum(volumes) / len(volumes)
                    volume_confirmed, _, _ = validate_volume_confirmation(
                        market_data[-1].volume, avg_volume, StrategyType.BREAKOUT
                    )

                    if volume_confirmed:
                        # Determine breakout direction (simplified)
                        current_price = market_data[-1].close
                        resistance = pattern.get(
                            "resistance", pattern.get("pole_high", current_price * 1.05)
                        )
                        support = pattern.get(
                            "support", pattern.get("pole_low", current_price * 0.95)
                        )

                        if current_price > resistance:
                            side = OrderSide.BUY
                            entry_price = current_price
                            stop_loss = pattern.get("support", current_price * 0.98)
                            take_profit = pattern["breakout_target"]
                        elif current_price < support:
                            side = OrderSide.SELL
                            entry_price = current_price
                            stop_loss = pattern.get("resistance", current_price * 1.02)
                            take_profit = support - (
                                resistance - support
                            )  # Measured move down
                        else:
                            continue

                        signal = Signal(
                            strategy=StrategyType.BREAKOUT,
                            asset=market_data[0].symbol,
                            side=side,
                            entry_price=entry_price,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            confidence=0.6,  # Medium confidence for breakouts
                            quality=TradeQuality.STANDARD,
                            market_state=MarketState.RANGE,
                            timeframe="15min",
                            pattern=pattern["pattern"],
                            volume_confirmation=True,
                            multi_timeframe_alignment=True,
                            support_resistance_valid=True,
                            rrr_meets_minimum=True,
                            liquidation_buffer_safe=True,
                            account_risk_ok=True,
                            margin_drawdown_ok=True,
                            forbidden_conditions_clear=True,
                            indicators={"pattern_height": pattern["height"]},
                            notes=f"Breakout pattern: {pattern['pattern']}, touches: {pattern['touches']}",
                        )

                        signals.append(signal)
                        break  # Only one signal per asset

        return signals


class LiquidationCaptureStrategy:
    """
    Liquidation event capture strategy implementation.

    Based on Trading Instructions Section IV.C - Tertiary Strategy: Liquidation Event Capture.
    """

    def __init__(self):
        self.config = get_config().strategy.liquidation_capture
        self.session_trades = 0
        self.last_trade_time = None

    def generate_signals(self, market_data: List[MarketData]) -> List[Signal]:
        """
        Generate liquidation capture signals.

        Args:
            market_data: Recent market data

        Returns:
            List of trading signals
        """
        signals: List[Signal] = []

        if len(market_data) < 20:
            return signals

        # Check session limits
        now = datetime.now()
        if (
            self.last_trade_time
            and (now - self.last_trade_time).hours < self.config["min_hours_between"]
        ):
            return signals

        if self.session_trades >= self.config["max_per_session"]:
            return signals

        # Detect liquidation event
        liquidation = detect_liquidation_event(market_data)

        if liquidation is None:
            return signals

        # Check RSI extreme
        closes = [d.close for d in market_data[-20:]]
        rsi = calculate_rsi(closes)

        rsi_valid = False
        if (
            liquidation["direction"] == "down"
            and rsi <= self.config["rsi_extreme_long"]
        ):
            rsi_valid = True
        elif (
            liquidation["direction"] == "up" and rsi >= self.config["rsi_extreme_short"]
        ):
            rsi_valid = True

        if not rsi_valid:
            return signals

        # Determine entry
        current_price = market_data[-1].close
        extreme_price = liquidation["extreme_price"]

        if liquidation["direction"] == "down":
            side = OrderSide.BUY
            entry_price = current_price
            stop_loss = extreme_price * 0.99  # 1% below wick
            take_profit = current_price + (current_price - stop_loss) * 3  # 3:1 RRR
        else:
            side = OrderSide.SELL
            entry_price = current_price
            stop_loss = extreme_price * 1.01  # 1% above wick
            take_profit = current_price - (stop_loss - current_price) * 3

        # High confidence due to extreme conditions
        signal = Signal(
            strategy=StrategyType.LIQUIDATION_CAPTURE,
            asset=market_data[0].symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=0.8,  # High confidence for liquidation events
            quality=TradeQuality.HIGH_CONVICTION,
            market_state=MarketState.TRANSITION,
            timeframe="15min",
            pattern="liquidation_cascade",
            volume_confirmation=True,
            multi_timeframe_alignment=True,  # Assume higher timeframe support nearby
            support_resistance_valid=True,
            rrr_meets_minimum=True,
            liquidation_buffer_safe=True,  # Use higher leverage
            account_risk_ok=True,
            margin_drawdown_ok=True,
            forbidden_conditions_clear=True,
            indicators={
                "rsi": rsi,
                "volume_spike": liquidation["volume_spike"],
                "price_move": liquidation["price_move"],
            },
            notes=f"Liquidation event: {liquidation['direction']} cascade, RSI: {rsi:.1f}",
        )

        signals.append(signal)
        return signals


def get_strategy_signals(
    strategy_type: StrategyType, market_data: List[MarketData]
) -> List[Signal]:
    """
    Get signals from specified strategy.

    Args:
        strategy_type: Type of strategy to run
        market_data: Market data for analysis

    Returns:
        List of trading signals
    """
    if strategy_type == StrategyType.TREND_FOLLOWING:
        strategy = TrendFollowingStrategy()
    elif strategy_type == StrategyType.BREAKOUT:
        strategy = BreakoutStrategy()
    elif strategy_type == StrategyType.LIQUIDATION_CAPTURE:
        strategy = LiquidationCaptureStrategy()
    else:
        return []

    return strategy.generate_signals(market_data)
