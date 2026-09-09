"""
Execution Layer for Precise Entry Timing

Uses 1-minute and 5-minute timeframes to refine trade entries AFTER
higher-timeframe signals are validated.

CRITICAL RULES:
- 1m/5m data NEVER affects regime detection (that stays on 1h)
- 1m/5m data NEVER enables/disables strategies
- 1m/5m data NEVER flips signal direction (higher TF determines BUY/SELL)
- All refinements are OPTIONAL improvements, not hard gates
- If 1m/5m data is unavailable, fall back to immediate execution

SCOPE - THIS MODULE IS LIVE-ONLY:
    ``trading_bot.py`` is the only importer. ``BacktestEngine`` does not build
    or call an ExecutionLayer, and it cannot: this class needs a live
    MultiTimeframeFetcher, and SimulatedExchange does not provide one. So the
    ``exec:*`` rejection reasons on a backtest funnel come exclusively from
    ``backtesting/engine.py::_execute_signal`` - none of them originate here.

    That is currently harmless for live/backtest parity, because
    :meth:`ExecutionLayer.refine_entry` never returns None: every path returns
    a signal, adjusting only confidence, stop_loss and notes. The
    ``refined_signal is None`` branch in ``trading_bot.py`` and the
    ``signals_skipped`` counter below are therefore both unreachable today.
    Anything that makes this class start skipping entries would silently
    diverge live from every backtest, so it needs a matching gate in the
    engine. ``test_execution_layer_no_hard_block`` pins this.
"""

from typing import Optional, Dict, Any
from dataclasses import dataclass
from loguru import logger

from .models import Signal, OrderSide
from .indicators import calculate_rsi, calculate_atr
from .multi_timeframe_fetcher import MultiTimeframeFetcher, EXECUTION_TIMEFRAMES


@dataclass
class ExecutionContext:
    """Context data for execution refinement decisions."""

    rsi_5m: Optional[float] = None
    rsi_1m: Optional[float] = None
    atr_5m: Optional[float] = None
    atr_1m: Optional[float] = None
    volume_ratio_1m: Optional[float] = None  # Current volume / average volume
    price_1m: Optional[float] = None
    wick_rejection: Optional[str] = None  # "bullish", "bearish", or None
    momentum_5m: Optional[str] = None  # "aligned", "counter", or "neutral"
    skip_reason: Optional[str] = None  # If set, explains why entry was skipped


class ExecutionLayer:
    """
    Refines trade entries using 1m/5m data for better timing.

    This layer sits between signal generation (StrategyManager) and execution (TradingBot).
    It does NOT change regime or strategy decisions - only improves entry timing.
    """

    # RSI thresholds for entry validation
    RSI_BULLISH_THRESHOLD = 60  # RSI > 60 favorable for BUY
    RSI_BEARISH_THRESHOLD = 40  # RSI < 40 favorable for SELL
    RSI_EXTREME_BULLISH = 75  # RSI very high - wait for pullback
    RSI_EXTREME_BEARISH = 25  # RSI very low - wait for bounce

    # Volume confirmation threshold
    VOLUME_SPIKE_RATIO = 1.2  # Require 1.2x average volume

    # Momentum lookback
    MOMENTUM_LOOKBACK = 3  # Number of candles to check for momentum

    def __init__(self, fetcher: MultiTimeframeFetcher, enabled: bool = True):
        """
        Initialize ExecutionLayer.

        Args:
            fetcher: MultiTimeframeFetcher for getting 1m/5m data
            enabled: If False, always returns original signal (passthrough mode)
        """
        self.fetcher = fetcher
        self.enabled = enabled
        self._stats = {
            "signals_received": 0,
            "signals_refined": 0,
            "signals_skipped": 0,
            "fallback_to_original": 0,
        }
        logger.info(f"ExecutionLayer initialized (enabled={enabled})")

    def refine_entry(self, signal: Signal, symbol: str) -> Optional[Signal]:
        """
        Refine a signal's entry using 1m/5m data.

        This is the main entry point. It does NOT change:
        - signal.strategy (regime-determined)
        - signal.side (direction from higher TF)
        - signal.quality (strategy-determined)

        It CAN refine:
        - signal.stop_loss (tighter stop using 5m ATR)
        - signal.confidence (boost if 1m confirms)
        - signal.notes (add execution context)

        Args:
            signal: The validated signal from StrategyManager
            symbol: Trading symbol (e.g., "BTC" or "BTC-PERP")

        Returns:
            Refined signal if conditions met, None if entry should be skipped,
            or original signal if execution layer is disabled or data unavailable
        """
        self._stats["signals_received"] += 1

        # Passthrough mode
        if not self.enabled:
            logger.debug(f"ExecutionLayer disabled - passing through {symbol} signal")
            return signal

        # Fetch execution timeframe data
        try:
            exec_data = self.fetcher.get_candles_multi_tf(
                symbol=symbol,
                timeframes=EXECUTION_TIMEFRAMES,  # ["1m", "5m"]
                lookback_candles=50,  # Enough for indicators
            )
        except Exception as e:
            logger.warning(f"Could not fetch execution TF data for {symbol}: {e}")
            self._stats["fallback_to_original"] += 1
            return signal  # Fallback to original signal

        # Check if we got both timeframes
        data_5m = exec_data.get("5m")
        data_1m = exec_data.get("1m")

        if not data_5m or not data_1m:
            logger.warning(
                f"Missing execution TF data for {symbol} (5m={bool(data_5m)}, 1m={bool(data_1m)})"
            )
            self._stats["fallback_to_original"] += 1
            return signal  # Fallback to original signal

        # Build execution context
        context = self._build_context(data_5m, data_1m, signal.side)

        # Log context for debugging
        logger.debug(
            f"ExecutionLayer {symbol}: RSI_5m={context.rsi_5m:.1f}, RSI_1m={context.rsi_1m:.1f}, "
            f"momentum_5m={context.momentum_5m}, vol_ratio={context.volume_ratio_1m:.2f}"
            if context.rsi_5m
            else f"ExecutionLayer {symbol}: context incomplete"
        )

        # Validate 5m setup
        setup_valid, setup_reason = self._validate_5m_setup(context, signal.side)
        if not setup_valid:
            # 5m conditions not favorable - but DON'T hard-block
            # Instead, reduce confidence and add note
            logger.info(f"5m setup not ideal for {symbol}: {setup_reason}")
            # Still allow entry but with lower confidence
            signal.confidence = max(0.1, signal.confidence * 0.8)
            signal.notes = f"{signal.notes} | 5m: {setup_reason}"

        # Validate 1m timing
        timing_good, timing_context = self._validate_1m_timing(context, signal.side)
        if timing_good:
            # Good timing - boost confidence
            self._stats["signals_refined"] += 1
            signal.confidence = min(1.0, signal.confidence * 1.1)
            signal.notes = f"{signal.notes} | 1m confirmed: {timing_context}"
            logger.info(f"✅ Entry confirmed for {symbol}: {timing_context}")

            # Optionally refine stop loss using 5m ATR
            if context.atr_5m and context.atr_5m > 0:
                refined_stop = self._refine_stop_loss(signal, context.atr_5m)
                if refined_stop:
                    signal.stop_loss = refined_stop
                    signal.notes = f"{signal.notes} | Stop refined to 5m ATR"

        else:
            # Timing not ideal but still proceed (don't hard-block)
            signal.notes = f"{signal.notes} | 1m timing: {timing_context}"
            logger.info(f"⚠️ 1m timing suboptimal for {symbol}: {timing_context}")

        return signal

    def _build_context(
        self, data_5m: Dict, data_1m: Dict, side: OrderSide
    ) -> ExecutionContext:
        """Build execution context from candle data."""
        context = ExecutionContext()

        try:
            # Calculate 5m indicators
            closes_5m = data_5m.get("close", [])
            highs_5m = data_5m.get("high", [])
            lows_5m = data_5m.get("low", [])

            if len(closes_5m) >= 14:
                context.rsi_5m = calculate_rsi(closes_5m, period=14)
            if len(closes_5m) >= 14 and len(highs_5m) >= 14 and len(lows_5m) >= 14:
                context.atr_5m = calculate_atr(highs_5m, lows_5m, closes_5m, period=14)

            # Calculate 5m momentum (last N candles direction)
            if len(closes_5m) >= self.MOMENTUM_LOOKBACK + 1:
                recent_closes = closes_5m[-self.MOMENTUM_LOOKBACK :]
                momentum_up = recent_closes[-1] > recent_closes[0]

                if side == OrderSide.BUY:
                    context.momentum_5m = "aligned" if momentum_up else "counter"
                else:  # SELL
                    context.momentum_5m = "aligned" if not momentum_up else "counter"

            # Calculate 1m indicators
            closes_1m = data_1m.get("close", [])
            highs_1m = data_1m.get("high", [])
            lows_1m = data_1m.get("low", [])
            volumes_1m = data_1m.get("volume", [])

            if len(closes_1m) >= 14:
                context.rsi_1m = calculate_rsi(closes_1m, period=14)
            if len(closes_1m) >= 14 and len(highs_1m) >= 14 and len(lows_1m) >= 14:
                context.atr_1m = calculate_atr(highs_1m, lows_1m, closes_1m, period=14)

            # Current price from 1m
            if closes_1m:
                context.price_1m = closes_1m[-1]

            # Volume ratio (current vs 20-period average)
            if len(volumes_1m) >= 20:
                avg_volume = sum(volumes_1m[-20:]) / 20
                current_volume = volumes_1m[-1]
                context.volume_ratio_1m = (
                    current_volume / avg_volume if avg_volume > 0 else 1.0
                )

            # Wick rejection detection on 1m
            if len(closes_1m) >= 1 and len(highs_1m) >= 1 and len(lows_1m) >= 1:
                context.wick_rejection = self._detect_wick_rejection(
                    data_1m.get("open", [])[-1]
                    if data_1m.get("open")
                    else closes_1m[-2]
                    if len(closes_1m) > 1
                    else closes_1m[-1],
                    highs_1m[-1],
                    lows_1m[-1],
                    closes_1m[-1],
                )

        except Exception as e:
            logger.error(f"Error building execution context: {e}")

        return context

    def _validate_5m_setup(
        self, context: ExecutionContext, side: OrderSide
    ) -> tuple[bool, str]:
        """
        Validate 5m setup conditions.

        Returns (is_valid, reason_string).
        """
        if context.rsi_5m is None:
            return True, "RSI unavailable"  # Don't block if data missing

        # Check RSI alignment with trade direction
        if side == OrderSide.BUY:
            if context.rsi_5m > self.RSI_EXTREME_BULLISH:
                return False, f"RSI too high ({context.rsi_5m:.0f}) - wait for pullback"
            if context.rsi_5m < self.RSI_BEARISH_THRESHOLD:
                return False, f"RSI bearish ({context.rsi_5m:.0f}) - momentum counter"
        else:  # SELL
            if context.rsi_5m < self.RSI_EXTREME_BEARISH:
                return False, f"RSI too low ({context.rsi_5m:.0f}) - wait for bounce"
            if context.rsi_5m > self.RSI_BULLISH_THRESHOLD:
                return False, f"RSI bullish ({context.rsi_5m:.0f}) - momentum counter"

        # Check momentum alignment
        if context.momentum_5m == "counter":
            return False, "5m momentum counter to signal direction"

        return True, "5m setup valid"

    def _validate_1m_timing(
        self, context: ExecutionContext, side: OrderSide
    ) -> tuple[bool, str]:
        """
        Validate 1m timing conditions.

        Returns (timing_good, context_string).
        """
        reasons = []

        # Volume confirmation
        if (
            context.volume_ratio_1m
            and context.volume_ratio_1m >= self.VOLUME_SPIKE_RATIO
        ):
            reasons.append(f"volume {context.volume_ratio_1m:.1f}x")
        elif context.volume_ratio_1m:
            reasons.append(f"low volume ({context.volume_ratio_1m:.1f}x)")

        # Wick rejection confirmation
        if context.wick_rejection:
            if (side == OrderSide.BUY and context.wick_rejection == "bullish") or (
                side == OrderSide.SELL and context.wick_rejection == "bearish"
            ):
                reasons.append("wick rejection confirmed")
            elif (side == OrderSide.BUY and context.wick_rejection == "bearish") or (
                side == OrderSide.SELL and context.wick_rejection == "bullish"
            ):
                reasons.append("wick rejection counter")

        # Determine if timing is good
        # Good timing: volume spike + wick aligned, or just volume spike
        timing_good = (
            context.volume_ratio_1m
            and context.volume_ratio_1m >= self.VOLUME_SPIKE_RATIO
        ) or (
            context.wick_rejection
            and (
                (side == OrderSide.BUY and context.wick_rejection == "bullish")
                or (side == OrderSide.SELL and context.wick_rejection == "bearish")
            )
        )

        return timing_good, ", ".join(reasons) if reasons else "neutral"

    def _detect_wick_rejection(
        self, open_price: float, high: float, low: float, close: float
    ) -> Optional[str]:
        """
        Detect wick rejection pattern on a candle.

        Bullish wick rejection: Long lower wick, small body, closing near high
        Bearish wick rejection: Long upper wick, small body, closing near low
        """
        try:
            body_size = abs(close - open_price)
            total_range = high - low

            if total_range == 0:
                return None

            upper_wick = high - max(open_price, close)
            lower_wick = min(open_price, close) - low

            # Bullish: lower wick > 60% of range, body < 30%
            if lower_wick / total_range > 0.6 and body_size / total_range < 0.3:
                return "bullish"

            # Bearish: upper wick > 60% of range, body < 30%
            if upper_wick / total_range > 0.6 and body_size / total_range < 0.3:
                return "bearish"

        except Exception:
            pass

        return None

    def _refine_stop_loss(self, signal: Signal, atr_5m: float) -> Optional[float]:
        """
        Refine stop loss using 5m ATR for tighter risk management.

        Only tightens the stop, never loosens it.
        """
        # Calculate tighter stop based on 5m ATR (1.5x ATR from entry)
        stop_distance = atr_5m * 1.5

        if signal.side == OrderSide.BUY:
            new_stop = signal.entry_price - stop_distance
            # Only use if tighter than original
            if new_stop > signal.stop_loss:
                return new_stop
        else:  # SELL
            new_stop = signal.entry_price + stop_distance
            # Only use if tighter than original
            if new_stop < signal.stop_loss:
                return new_stop

        return None

    def get_stats(self) -> Dict[str, Any]:
        """Get execution layer statistics."""
        return {
            **self._stats,
            "enabled": self.enabled,
            "refinement_rate": (
                self._stats["signals_refined"] / self._stats["signals_received"]
                if self._stats["signals_received"] > 0
                else 0
            ),
        }
