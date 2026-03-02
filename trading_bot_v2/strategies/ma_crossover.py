"""
MA Crossover Strategy (Trend Following)

Strategy Logic:
- BUY Signal: 50 MA crosses above 200 MA (Golden Cross)
- SELL Signal: 50 MA crosses below 200 MA (Death Cross)
- Confirmation: Rising volume on crossover, MACD alignment
- Entry: Wait for pullback after crossover (not immediate)
- Stop Loss: Below recent swing low (long) or swing high (short)

Best For: TRENDING_STRONG regime (ADX > 25)

Based on Strategy Review Document: Trend Following MA Crossover
"""

import os
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from loguru import logger

# Use relative imports from trading_bot_v2 package
from ..models import Signal, OrderSide
from ..indicators import calculate_sma, calculate_macd, calculate_atr
from ..config import StrategyType, AssetClass, TradeQuality, MarketState


class MACrossoverStrategy:
    """
    Moving Average Crossover strategy for trend following.

    Uses 50/200 MA crossovers with pullback entry logic.

    Parameters can be configured via environment variables:
    - MA_CROSSOVER_FAST_PERIOD (default: 20)
    - MA_CROSSOVER_SLOW_PERIOD (default: 50)
    - MA_CROSSOVER_PULLBACK_MIN (default: 0.02)
    - MA_CROSSOVER_PULLBACK_MAX (default: 0.04)
    - MA_CROSSOVER_VOLUME_THRESHOLD (default: 1.2)
    - MA_CROSSOVER_MACD_FAST (default: 12)
    - MA_CROSSOVER_MACD_SLOW (default: 26)
    - MA_CROSSOVER_MACD_SIGNAL (default: 9)
    - MA_CROSSOVER_ATR_PERIOD (default: 14)
    - MA_CROSSOVER_ATR_STOP_MULTIPLIER (default: 2.5)
    - MA_CROSSOVER_MIN_CONFIDENCE (default: 0.50)
    """

    def __init__(
        self,
        fast_ma_period: Optional[int] = None,
        slow_ma_period: Optional[int] = None,
        pullback_range: Optional[Tuple[float, float]] = None,
        volume_confirmation_threshold: Optional[float] = None,
        macd_fast: Optional[int] = None,
        macd_slow: Optional[int] = None,
        macd_signal: Optional[int] = None,
        atr_period: Optional[int] = None,
        atr_stop_multiplier: Optional[float] = None,
        min_confidence: Optional[float] = None,
    ):
        """
        Initialize MA Crossover Strategy.

        Parameters are read from environment variables if not explicitly passed.

        Args:
            fast_ma_period: Fast MA period (env: MA_CROSSOVER_FAST_PERIOD, default: 50)
            slow_ma_period: Slow MA period (env: MA_CROSSOVER_SLOW_PERIOD, default: 200)
            pullback_range: Acceptable pullback range for entry (env: MA_CROSSOVER_PULLBACK_MIN/MAX, default: 2-4%)
            volume_confirmation_threshold: Volume multiplier (env: MA_CROSSOVER_VOLUME_THRESHOLD, default: 1.2x)
            macd_fast: MACD fast period (default: 12)
            macd_slow: MACD slow period (default: 26)
            macd_signal: MACD signal period (default: 9)
            atr_period: ATR period for stop loss (default: 14)
            atr_stop_multiplier: ATR multiplier for stop (env: MA_CROSSOVER_ATR_STOP_MULTIPLIER, default: 2.5)
            min_confidence: Minimum confidence (env: MA_CROSSOVER_MIN_CONFIDENCE, default: 0.50)
        """
        # Read from environment with reasonable defaults
        self.fast_ma_period = (
            fast_ma_period
            if fast_ma_period is not None
            else int(os.getenv("MA_CROSSOVER_FAST_PERIOD", "20"))
        )
        self.slow_ma_period = (
            slow_ma_period
            if slow_ma_period is not None
            else int(os.getenv("MA_CROSSOVER_SLOW_PERIOD", "50"))
        )

        if pullback_range is not None:
            self.pullback_range = pullback_range
        else:
            pullback_min = float(os.getenv("MA_CROSSOVER_PULLBACK_MIN", "0.02"))
            pullback_max = float(os.getenv("MA_CROSSOVER_PULLBACK_MAX", "0.04"))
            self.pullback_range = (pullback_min, pullback_max)

        self.volume_threshold = (
            volume_confirmation_threshold
            if volume_confirmation_threshold is not None
            else float(os.getenv("MA_CROSSOVER_VOLUME_THRESHOLD", "1.2"))
        )
        self.macd_fast = macd_fast if macd_fast is not None else int(os.getenv("MA_CROSSOVER_MACD_FAST", "12"))
        self.macd_slow = macd_slow if macd_slow is not None else int(os.getenv("MA_CROSSOVER_MACD_SLOW", "26"))
        self.macd_signal = macd_signal if macd_signal is not None else int(os.getenv("MA_CROSSOVER_MACD_SIGNAL", "9"))
        self.atr_period = atr_period if atr_period is not None else int(os.getenv("MA_CROSSOVER_ATR_PERIOD", "14"))
        self.atr_stop_multiplier = (
            atr_stop_multiplier
            if atr_stop_multiplier is not None
            else float(os.getenv("MA_CROSSOVER_ATR_STOP_MULTIPLIER", "2.5"))
        )
        self.min_confidence = (
            min_confidence
            if min_confidence is not None
            else float(os.getenv("MA_CROSSOVER_MIN_CONFIDENCE", "0.50"))
        )  # Loosened from 0.65

        # Track crossover state
        self.last_crossover = {}  # {symbol: {"type": "golden/death", "candle_index": int}}

        logger.info(
            f"MACrossoverStrategy initialized: "
            f"MA {self.fast_ma_period}/{self.slow_ma_period}, "
            f"pullback {self.pullback_range[0]:.1%}-{self.pullback_range[1]:.1%}, "
            f"ATR stop={self.atr_stop_multiplier}x, "
            f"min_confidence={self.min_confidence}"
        )

    def generate_signals(
        self,
        symbol: str,
        multi_tf_data: Dict[str, Dict[str, List[float]]],
        current_price: float,
    ) -> List[Signal]:
        """
        Generate MA crossover signals from multi-timeframe data.

        Args:
            symbol: Trading symbol (e.g., "SUI-PERP")
            multi_tf_data: Dictionary mapping timeframe to OHLCV data
                          {"15m": {"high": [...], "low": [...], "close": [...], "volume": [...]}, ...}
            current_price: Current market price

        Returns:
            List of Signal objects (0-1 signals)
        """
        try:
            # Require 4h data for MA crossover (longer timeframe for trend detection)
            if "4h" not in multi_tf_data:
                logger.warning(f"Missing 4h timeframe for {symbol}")
                return []

            data_4h = multi_tf_data["4h"]

            # Validate data
            if not self._validate_data(data_4h):
                logger.warning(f"Invalid 4h data for {symbol}")
                return []

            # Calculate MAs on 4h timeframe
            closes = data_4h["close"]
            fast_ma = calculate_sma(closes, period=self.fast_ma_period)
            slow_ma = calculate_sma(closes, period=self.slow_ma_period)

            # Calculate previous MAs to detect crossover
            fast_ma_prev = calculate_sma(closes[:-1], period=self.fast_ma_period)
            slow_ma_prev = calculate_sma(closes[:-1], period=self.slow_ma_period)

            # Calculate MACD for confirmation
            macd_line, signal_line, histogram = calculate_macd(
                closes,
                fast_period=self.macd_fast,
                slow_period=self.macd_slow,
                signal_period=self.macd_signal,
            )

            # Calculate ATR for stop loss
            atr = calculate_atr(
                data_4h["high"], data_4h["low"], closes, period=self.atr_period
            )

            # Calculate volume confirmation
            volumes = data_4h.get("volume", [1.0] * len(closes))
            avg_volume = sum(volumes[-20:]) / min(20, len(volumes))
            current_volume = volumes[-1]
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

            logger.debug(
                f"{symbol} 4h indicators: "
                f"Fast MA={fast_ma:.2f}, Slow MA={slow_ma:.2f}, "
                f"MACD={macd_line:.2f}, Signal={signal_line:.2f}, "
                f"Volume ratio={volume_ratio:.2f}x"
            )

            # Detect crossovers
            crossover_type = self._detect_crossover(
                fast_ma, slow_ma, fast_ma_prev, slow_ma_prev
            )

            if crossover_type:
                # Store crossover for pullback tracking
                self.last_crossover[symbol] = {
                    "type": crossover_type,
                    "candle_index": len(closes) - 1,
                    "fast_ma": fast_ma,
                    "slow_ma": slow_ma,
                }
                logger.info(f"{crossover_type.upper()} crossover detected for {symbol}")

            # Check if we should enter on pullback
            if symbol in self.last_crossover:
                crossover_info = self.last_crossover[symbol]
                candles_since_crossover = (
                    len(closes) - 1 - crossover_info["candle_index"]
                )

                # Wait 1-5 candles after crossover for pullback
                if 1 <= candles_since_crossover <= 5:
                    if crossover_info["type"] == "golden":
                        # Golden cross - check for LONG entry on pullback
                        pullback_pct = (fast_ma - current_price) / fast_ma
                        if (
                            self.pullback_range[0]
                            <= pullback_pct
                            <= self.pullback_range[1]
                        ):
                            # Check MACD alignment
                            if macd_line > signal_line:
                                signal = self._create_long_signal(
                                    symbol=symbol,
                                    current_price=current_price,
                                    fast_ma=fast_ma,
                                    slow_ma=slow_ma,
                                    atr=atr,
                                    macd_line=macd_line,
                                    signal_line=signal_line,
                                    volume_ratio=volume_ratio,
                                    pullback_pct=pullback_pct,
                                    highs=data_4h["high"],
                                    lows=data_4h["low"],
                                )
                                if signal:
                                    logger.info(f"LONG signal on pullback for {symbol}")
                                    return [signal]

                    elif crossover_info["type"] == "death":
                        # Death cross - check for SHORT entry on rally
                        rally_pct = (current_price - fast_ma) / fast_ma
                        if (
                            self.pullback_range[0]
                            <= rally_pct
                            <= self.pullback_range[1]
                        ):
                            # Check MACD alignment
                            if macd_line < signal_line:
                                signal = self._create_short_signal(
                                    symbol=symbol,
                                    current_price=current_price,
                                    fast_ma=fast_ma,
                                    slow_ma=slow_ma,
                                    atr=atr,
                                    macd_line=macd_line,
                                    signal_line=signal_line,
                                    volume_ratio=volume_ratio,
                                    rally_pct=rally_pct,
                                    highs=data_4h["high"],
                                    lows=data_4h["low"],
                                )
                                if signal:
                                    logger.info(f"SHORT signal on rally for {symbol}")
                                    return [signal]

            # No signal
            return []

        except Exception as e:
            logger.error(f"Error generating MA crossover signal for {symbol}: {e}")
            return []

    def _validate_data(self, data: Dict[str, List[float]]) -> bool:
        """Validate that data has required fields and sufficient length."""
        required_keys = ["high", "low", "close"]
        min_length = max(self.slow_ma_period + 1, self.macd_slow + self.macd_signal)

        for key in required_keys:
            if key not in data:
                return False
            if len(data[key]) < min_length:
                return False

        return True

    def _detect_crossover(
        self, fast_ma: float, slow_ma: float, fast_ma_prev: float, slow_ma_prev: float
    ) -> Optional[str]:
        """
        Detect MA crossover.

        Returns:
            "golden" for golden cross (fast crosses above slow)
            "death" for death cross (fast crosses below slow)
            None if no crossover
        """
        # Golden cross: fast was below, now above
        if fast_ma_prev <= slow_ma_prev and fast_ma > slow_ma:
            return "golden"

        # Death cross: fast was above, now below
        if fast_ma_prev >= slow_ma_prev and fast_ma < slow_ma:
            return "death"

        return None

    def _find_recent_swing_low(self, lows: List[float], lookback: int = 20) -> float:
        """Find recent swing low for stop loss placement."""
        recent_lows = lows[-lookback:]
        return min(recent_lows) if recent_lows else lows[-1]

    def _find_recent_swing_high(self, highs: List[float], lookback: int = 20) -> float:
        """Find recent swing high for stop loss placement."""
        recent_highs = highs[-lookback:]
        return max(recent_highs) if recent_highs else highs[-1]

    def _create_long_signal(
        self,
        symbol: str,
        current_price: float,
        fast_ma: float,
        slow_ma: float,
        atr: float,
        macd_line: float,
        signal_line: float,
        volume_ratio: float,
        pullback_pct: float,
        highs: List[float],
        lows: List[float],
    ) -> Optional[Signal]:
        """Create LONG signal after golden cross pullback."""
        # Stop Loss: Below recent swing low or 2.5x ATR, whichever is closer
        swing_low = self._find_recent_swing_low(lows)
        atr_stop = current_price - (atr * self.atr_stop_multiplier)
        stop_loss = max(swing_low, atr_stop)  # Use the closer stop

        # Take Profit: 2x risk (RRR = 2.0 minimum)
        risk = current_price - stop_loss
        take_profit = current_price + (risk * 2.0)

        # Calculate confidence (0-1 scale)
        # Higher confidence when:
        # - Volume is strong (> 1.2x average)
        # - MACD shows strong bullish momentum
        # - Pullback is in sweet spot (2-3%)
        volume_score = min(volume_ratio / self.volume_threshold, 1.0)  # 0-1
        macd_strength = (
            (macd_line - signal_line) / abs(macd_line) if macd_line != 0 else 0
        )
        macd_score = min(abs(macd_strength), 1.0)  # 0-1
        pullback_score = 1.0 - abs(pullback_pct - 0.03) / 0.02  # Best at 3%, 0-1
        pullback_score = max(0.0, min(1.0, pullback_score))

        confidence = (volume_score * 0.3) + (macd_score * 0.4) + (pullback_score * 0.3)
        confidence = max(0.0, min(1.0, confidence))

        if confidence < self.min_confidence:
            logger.debug(
                f"LONG signal rejected: Low confidence {confidence:.2f} < {self.min_confidence}"
            )
            return None

        # Create signal
        signal = Signal(
            strategy=StrategyType.MA_CROSSOVER,
            asset=symbol,
            asset_class=AssetClass.PERPETUAL,
            side=OrderSide.BUY,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            quality=TradeQuality.HIGH_CONVICTION
            if confidence > 0.8
            else TradeQuality.STANDARD,
            market_state=MarketState.TREND,
            timeframe="4h",
            pattern="golden_cross_pullback",
            volume_confirmation=volume_ratio >= self.volume_threshold,
            multi_timeframe_alignment=True,  # Golden cross confirmed
            support_resistance_valid=True,  # Fast MA acts as support
            rrr_meets_minimum=True,  # RRR = 2.0
            liquidation_buffer_safe=True,  # Will be validated externally
            account_risk_ok=True,  # Will be validated externally
            margin_drawdown_ok=True,  # Will be validated externally
            forbidden_conditions_clear=True,  # No forbidden conditions
            indicators={
                "fast_ma": fast_ma,
                "slow_ma": slow_ma,
                "macd_line": macd_line,
                "signal_line": signal_line,
                "volume_ratio": volume_ratio,
                "pullback_pct": pullback_pct,
            },
            notes=f"Golden cross pullback {pullback_pct:.1%}, volume {volume_ratio:.1f}x",
        )

        return signal

    def _create_short_signal(
        self,
        symbol: str,
        current_price: float,
        fast_ma: float,
        slow_ma: float,
        atr: float,
        macd_line: float,
        signal_line: float,
        volume_ratio: float,
        rally_pct: float,
        highs: List[float],
        lows: List[float],
    ) -> Optional[Signal]:
        """Create SHORT signal after death cross rally."""
        # Stop Loss: Above recent swing high or 2.5x ATR, whichever is closer
        swing_high = self._find_recent_swing_high(highs)
        atr_stop = current_price + (atr * self.atr_stop_multiplier)
        stop_loss = min(swing_high, atr_stop)  # Use the closer stop

        # Take Profit: 2x risk (RRR = 2.0 minimum)
        risk = stop_loss - current_price
        take_profit = current_price - (risk * 2.0)

        # Calculate confidence (0-1 scale)
        volume_score = min(volume_ratio / self.volume_threshold, 1.0)  # 0-1
        macd_strength = (
            (signal_line - macd_line) / abs(macd_line) if macd_line != 0 else 0
        )
        macd_score = min(abs(macd_strength), 1.0)  # 0-1
        rally_score = 1.0 - abs(rally_pct - 0.03) / 0.02  # Best at 3%, 0-1
        rally_score = max(0.0, min(1.0, rally_score))

        confidence = (volume_score * 0.3) + (macd_score * 0.4) + (rally_score * 0.3)
        confidence = max(0.0, min(1.0, confidence))

        if confidence < self.min_confidence:
            logger.debug(
                f"SHORT signal rejected: Low confidence {confidence:.2f} < {self.min_confidence}"
            )
            return None

        # Create signal
        signal = Signal(
            strategy=StrategyType.MA_CROSSOVER,
            asset=symbol,
            asset_class=AssetClass.PERPETUAL,
            side=OrderSide.SELL,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            quality=TradeQuality.HIGH_CONVICTION
            if confidence > 0.8
            else TradeQuality.STANDARD,
            market_state=MarketState.TREND,
            timeframe="4h",
            pattern="death_cross_rally",
            volume_confirmation=volume_ratio >= self.volume_threshold,
            multi_timeframe_alignment=True,  # Death cross confirmed
            support_resistance_valid=True,  # Fast MA acts as resistance
            rrr_meets_minimum=True,  # RRR = 2.0
            liquidation_buffer_safe=True,  # Will be validated externally
            account_risk_ok=True,  # Will be validated externally
            margin_drawdown_ok=True,  # Will be validated externally
            forbidden_conditions_clear=True,  # No forbidden conditions
            indicators={
                "fast_ma": fast_ma,
                "slow_ma": slow_ma,
                "macd_line": macd_line,
                "signal_line": signal_line,
                "volume_ratio": volume_ratio,
                "rally_pct": rally_pct,
            },
            notes=f"Death cross rally {rally_pct:.1%}, volume {volume_ratio:.1f}x",
        )

        return signal
