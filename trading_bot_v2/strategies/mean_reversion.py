"""
Mean Reversion Strategy (RSI-Based)

TIMEFRAME HIERARCHY (Multi-TF Execution Model):
- PERMISSION (1h): Regime must be RANGING_CALM (handled by StrategyManager)
- STRUCTURE (15m): Price near Bollinger Band validates range bounds
- TRIGGER (5m): RSI extreme (< 30 or > 70) is the primary entry signal
- TIMING (1m): ExecutionLayer handles precise entry via volume/wick confirmation

Strategy Logic:
- BUY Signal: 5m RSI < 30 (oversold) + 15m price near lower Bollinger Band
- SELL Signal: 5m RSI > 70 (overbought) + 15m price near upper Bollinger Band
- Stop Loss: ATR-based (2x ATR from entry)
- Take Profit: Mean reversion to 20-period SMA

Best For: RANGING_CALM regime (ADX < 20, low volatility)

IMPORTANT: This strategy uses 5m RSI as the trigger, NOT requiring multi-TF RSI alignment.
The 1h regime permission is handled by StrategyManager, not within this strategy.

CONFIDENCE MTF TERM (MEAN_REVERSION_MTF_CONFIDENCE)
---------------------------------------------------
The confidence blend historically carried a third "mtf_alignment" term
weighted 0.3, fed with a value named ``rsi_1h``. That value was never a 1h
RSI: it was the TRIGGER RSI (5m when execution data is supplied, otherwise
the 15m RSI). With no 5m data the term was arithmetically identical to
``rsi_strength`` and the blend silently collapsed to 0.7/0.3 over two
terms; with 5m data it double-counted RSI at 70% of total weight while
labelling the result 1h. The mislabelling is fixed unconditionally - the
1h RSI is now computed from the 1h slice and only ever reported when real.
The blend itself is selectable so the alternatives can be measured:

- ``legacy``  - reproduce the historical (defective) blend exactly. Kept
                only so a campaign can measure against shipped behaviour.
- ``rsi_1h``  - wire the term as the code always claimed: a genuine 1h RSI.
- ``off``     - treat the term as vestigial, drop it, and renormalise the
                two real terms to their original 4:3 ratio.
"""

import os
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from loguru import logger

# Use relative imports from trading_bot_v2 package
from ..models import Signal, OrderSide
from ..indicators import (
    calculate_rsi,
    calculate_atr,
    calculate_bollinger_bands,
    calculate_sma,
)
from ..config import StrategyType, AssetClass, TradeQuality, MarketState

#: Reproduce the historical blend: MTF term fed with the trigger RSI.
MTF_MODE_LEGACY = "legacy"
#: Feed the MTF term a genuine 1h RSI (the design the code documented).
MTF_MODE_RSI_1H = "rsi_1h"
#: Drop the MTF term; renormalise the two real terms.
MTF_MODE_OFF = "off"
MTF_MODES = (MTF_MODE_LEGACY, MTF_MODE_RSI_1H, MTF_MODE_OFF)

#: Three-term blend weights (rsi_strength, bb_proximity, mtf_alignment).
W_RSI, W_BB, W_MTF = 0.4, 0.3, 0.3
#: Two-term blend: the same 4:3 ratio renormalised to sum to 1.0.
W_RSI_2, W_BB_2 = 4.0 / 7.0, 3.0 / 7.0


class MeanReversionStrategy:
    """
    Mean Reversion strategy using RSI and Bollinger Bands.

    Capitalizes on oversold/overbought conditions in ranging markets.

    Parameters can be configured via environment variables:
    - MEAN_REVERSION_RSI_OVERSOLD (default: 35.0)
    - MEAN_REVERSION_RSI_OVERBOUGHT (default: 65.0)
    - MEAN_REVERSION_RSI_PERIOD (default: 14)
    - MEAN_REVERSION_BB_PERIOD (default: 20)
    - MEAN_REVERSION_BB_STD_DEV (default: 2.0)
    - MEAN_REVERSION_SMA_PERIOD (default: 20)
    - MEAN_REVERSION_ATR_PERIOD (default: 14)
    - MEAN_REVERSION_ATR_STOP_MULTIPLIER (default: 2.0)
    - MEAN_REVERSION_MIN_CONFIDENCE (default: 0.45)
    """

    def __init__(
        self,
        rsi_oversold: Optional[float] = None,
        rsi_overbought: Optional[float] = None,
        rsi_period: Optional[int] = None,
        bb_period: Optional[int] = None,
        bb_std_dev: Optional[float] = None,
        bb_proximity: Optional[float] = None,
        sma_period: Optional[int] = None,
        atr_period: Optional[int] = None,
        atr_stop_multiplier: Optional[float] = None,
        min_confidence: Optional[float] = None,
        cooldown_minutes: Optional[int] = None,
        mtf_confidence_mode: Optional[str] = None,
    ):
        """
        Initialize Mean Reversion Strategy.

        Parameters are read from environment variables if not explicitly passed.
        Defaults are the "loosened" thresholds from Prompt 058 for more signals.

        Args:
            rsi_oversold: RSI level for oversold (env: MEAN_REVERSION_RSI_OVERSOLD, default: 35)
            rsi_overbought: RSI level for overbought (env: MEAN_REVERSION_RSI_OVERBOUGHT, default: 65)
            rsi_period: RSI calculation period (env: MEAN_REVERSION_RSI_PERIOD, default: 14)
            bb_period: Bollinger Bands period (env: MEAN_REVERSION_BB_PERIOD, default: 20)
            bb_std_dev: Bollinger Bands standard deviation (env: MEAN_REVERSION_BB_STD_DEV, default: 2.0)
            bb_proximity: Max fractional distance from BB edge to qualify entry (env: MEAN_REVERSION_BB_PROXIMITY, default: 0.20)
            sma_period: SMA period for take profit target (default: 20)
            atr_period: ATR period for stop loss (default: 14)
            atr_stop_multiplier: ATR multiplier for stop loss (env: MEAN_REVERSION_ATR_STOP_MULTIPLIER, default: 2.0)
            min_confidence: NOT A GATE. Retained for logging and for
                config/overlay compatibility only (env:
                MEAN_REVERSION_MIN_CONFIDENCE, default: 0.45). Nothing in
                this strategy compares confidence against it - contrast
                ma_crossover and funding_arb, which do enforce theirs. By
                design (Prompt 058) confidence affects SIZE, not
                permission: live position size scales with it through
                ConfidenceSizer, and the backtest engine ignores it
                entirely. Removed from the tuning search space and from
                ADOPTED_PARAMS on 2026-08-02 because an inert dimension
                cannot be optimised. Setting this env var changes log
                text and nothing else.
        """
        # Read from environment with loosened defaults (Prompt 058)
        self.rsi_oversold = (
            rsi_oversold
            if rsi_oversold is not None
            else float(os.getenv("MEAN_REVERSION_RSI_OVERSOLD", "35.0"))
        )
        self.rsi_overbought = (
            rsi_overbought
            if rsi_overbought is not None
            else float(os.getenv("MEAN_REVERSION_RSI_OVERBOUGHT", "65.0"))
        )
        self.rsi_period = (
            rsi_period
            if rsi_period is not None
            else int(os.getenv("MEAN_REVERSION_RSI_PERIOD", "14"))
        )
        self.bb_period = (
            bb_period
            if bb_period is not None
            else int(os.getenv("MEAN_REVERSION_BB_PERIOD", "20"))
        )
        self.bb_std_dev = (
            bb_std_dev
            if bb_std_dev is not None
            else float(os.getenv("MEAN_REVERSION_BB_STD_DEV", "2.0"))
        )
        self.bb_proximity = (
            bb_proximity
            if bb_proximity is not None
            else float(os.getenv("MEAN_REVERSION_BB_PROXIMITY", "0.20"))
        )
        self.sma_period = (
            sma_period
            if sma_period is not None
            else int(os.getenv("MEAN_REVERSION_SMA_PERIOD", "20"))
        )
        self.atr_period = (
            atr_period
            if atr_period is not None
            else int(os.getenv("MEAN_REVERSION_ATR_PERIOD", "14"))
        )
        self.atr_stop_multiplier = (
            atr_stop_multiplier
            if atr_stop_multiplier is not None
            else float(os.getenv("MEAN_REVERSION_ATR_STOP_MULTIPLIER", "2.0"))
        )
        self.min_confidence = (
            min_confidence
            if min_confidence is not None
            else float(os.getenv("MEAN_REVERSION_MIN_CONFIDENCE", "0.45"))
        )
        self.min_rrr = float(os.getenv("MEAN_REVERSION_MIN_RRR", "0.5"))
        self.cooldown_minutes = (
            cooldown_minutes
            if cooldown_minutes is not None
            else int(os.getenv("MEAN_REVERSION_COOLDOWN_MINUTES", "0"))
        )
        mode = (
            mtf_confidence_mode
            if mtf_confidence_mode is not None
            else os.getenv("MEAN_REVERSION_MTF_CONFIDENCE", MTF_MODE_LEGACY)
        )
        mode = str(mode).strip().lower()
        if mode not in MTF_MODES:
            logger.warning(
                f"Unknown MEAN_REVERSION_MTF_CONFIDENCE={mode!r}; "
                f"falling back to {MTF_MODE_LEGACY!r} "
                f"(valid: {', '.join(MTF_MODES)})"
            )
            mode = MTF_MODE_LEGACY
        self.mtf_confidence_mode = mode

        # Cooldown tracking per symbol (prevents clustered losses at same level)
        self._last_trade_time: Dict[str, datetime] = {}
        # Simulated time injected by backtest engine
        self._sim_time: Optional[datetime] = None

        logger.info(
            f"MeanReversionStrategy initialized: "
            f"RSI {self.rsi_oversold}/{self.rsi_overbought}, "
            f"BB period={self.bb_period}, "
            f"ATR stop={self.atr_stop_multiplier}x, "
            f"min_confidence={self.min_confidence}, "
            f"mtf_confidence={self.mtf_confidence_mode}"
        )

    def _mtf_rsi(self, trigger_rsi: float, rsi_1h: Optional[float]) -> Optional[float]:
        """Return the RSI the MTF confidence term should read.

        Args:
            trigger_rsi: Trigger-timeframe RSI (5m when available, else 15m).
            rsi_1h: Genuine 1h RSI, or None when the 1h slice was unusable.

        Returns:
            The RSI to feed the MTF term, or None to drop the term and use
            the renormalised two-term blend.
        """
        if self.mtf_confidence_mode == MTF_MODE_OFF:
            return None
        if self.mtf_confidence_mode == MTF_MODE_RSI_1H:
            # None here is a real signal: no 1h data, so no MTF term.
            return rsi_1h
        return trigger_rsi

    @staticmethod
    def _blend_confidence(
        rsi_strength: float,
        bb_proximity: float,
        mtf_alignment: Optional[float],
    ) -> float:
        """Combine confidence components, clamped to 0-1.

        Args:
            rsi_strength: Normalised RSI extremity on the 15m structure TF.
            bb_proximity: Normalised closeness to the relevant BB edge.
            mtf_alignment: Normalised MTF RSI extremity, or None to use the
                two-term blend.

        Returns:
            Confidence in [0.0, 1.0].
        """
        if mtf_alignment is None:
            confidence = rsi_strength * W_RSI_2 + bb_proximity * W_BB_2
        else:
            confidence = (
                rsi_strength * W_RSI + bb_proximity * W_BB + mtf_alignment * W_MTF
            )
        return max(0.0, min(1.0, confidence))

    def _now(self) -> datetime:
        """Return current time - simulated candle time in backtesting, wall-clock in live."""
        return (
            self._sim_time if self._sim_time is not None else datetime.now(timezone.utc)
        )

    def _check_cooldown(self, symbol: str) -> bool:
        """Return True if symbol is in cooldown (skip signal generation)."""
        if self.cooldown_minutes <= 0:
            return False
        if symbol not in self._last_trade_time:
            return False
        elapsed = self._now() - self._last_trade_time[symbol]
        if elapsed < timedelta(minutes=self.cooldown_minutes):
            remaining = (
                timedelta(minutes=self.cooldown_minutes) - elapsed
            ).total_seconds() / 60
            logger.debug(
                f"{symbol}: MeanReversion cooldown {remaining:.0f}min remaining"
            )
            return True
        return False

    def _set_cooldown(self, symbol: str) -> None:
        """Record trade time for cooldown enforcement."""
        self._last_trade_time[symbol] = self._now()

    def generate_signals(
        self,
        symbol: str,
        multi_tf_data: Dict[str, Dict[str, List[float]]],
        current_price: float,
        execution_tf_data: Optional[Dict[str, Dict[str, List[float]]]] = None,
    ) -> List[Signal]:
        """
        Generate mean reversion signals using multi-timeframe hierarchy.

        TIMEFRAME RESPONSIBILITIES:
        - 15m: Structure validation (BB bands define range boundaries)
        - 5m: TRIGGER (RSI extreme is the entry signal) - preferred if available
        - 1m: Timing refinement (handled by ExecutionLayer, not here)

        NOTE: 1h regime permission is handled by StrategyManager before this method is called.
        This strategy does NOT require multi-TF RSI alignment - that causes confluence deadlock.

        Args:
            symbol: Trading symbol (e.g., "SUI-PERP")
            multi_tf_data: Dictionary with regime/structure TFs {"15m": {...}, "1h": {...}, "4h": {...}}
            current_price: Current market price
            execution_tf_data: Optional dict with execution TFs {"5m": {...}, "1m": {...}}

        Returns:
            List of Signal objects (0-1 signals)
        """
        try:
            # Check cooldown (prevents clustered stop-outs at same price level)
            if self._check_cooldown(symbol):
                return []

            # Require 15m for structure validation
            if "15m" not in multi_tf_data:
                logger.warning(f"Missing 15m structure data for {symbol}")
                return []

            data_15m = multi_tf_data["15m"]

            # Validate 15m data
            if not self._validate_data(data_15m):
                logger.warning(f"Invalid 15m data for {symbol}")
                return []

            # Calculate 15m indicators for STRUCTURE validation
            upper_bb, middle_bb, lower_bb = calculate_bollinger_bands(
                data_15m["close"], period=self.bb_period, std_dev=self.bb_std_dev
            )
            atr_15m = calculate_atr(
                data_15m["high"],
                data_15m["low"],
                data_15m["close"],
                period=self.atr_period,
            )
            sma_15m = calculate_sma(data_15m["close"], period=self.sma_period)
            rsi_15m = calculate_rsi(data_15m["close"], period=self.rsi_period)

            # Determine TRIGGER RSI - prefer 5m, fall back to 15m
            # Using 5m allows faster signal generation without waiting for 15m+1h alignment
            trigger_rsi = rsi_15m  # Default fallback
            trigger_tf = "15m"

            if execution_tf_data and "5m" in execution_tf_data:
                data_5m = execution_tf_data["5m"]
                if self._validate_data(data_5m):
                    try:
                        trigger_rsi = calculate_rsi(
                            data_5m["close"], period=self.rsi_period
                        )
                        trigger_tf = "5m"
                    except Exception:
                        pass  # Use 15m fallback

            # Genuine 1h RSI for the MTF confidence term. Historically this
            # slice was documented but never read, and the trigger RSI was
            # passed under the name rsi_1h - see the module docstring.
            rsi_1h: Optional[float] = None
            data_1h = multi_tf_data.get("1h")
            if data_1h and self._validate_data(data_1h):
                try:
                    rsi_1h = calculate_rsi(data_1h["close"], period=self.rsi_period)
                except Exception:
                    rsi_1h = None

            logger.debug(
                f"{symbol} mean_reversion: trigger_RSI_{trigger_tf}={trigger_rsi:.2f}, "
                f"BB=({lower_bb:.2f}, {middle_bb:.2f}, {upper_bb:.2f}), "
                f"Price={current_price:.2f}, SMA={sma_15m:.2f}"
            )

            # Check for LONG signal (RSI oversold + price near lower BB)
            # REMOVED: Multi-TF RSI alignment requirement
            if self._check_long_conditions_v2(
                trigger_rsi, current_price, lower_bb, middle_bb, symbol
            ):
                signal = self._create_long_signal(
                    symbol=symbol,
                    current_price=current_price,
                    atr=atr_15m,
                    sma_target=sma_15m,
                    rsi_15m=rsi_15m,
                    trigger_rsi=trigger_rsi,
                    trigger_tf=trigger_tf,
                    rsi_1h=rsi_1h,
                    lower_bb=lower_bb,
                    middle_bb=middle_bb,
                    upper_bb=upper_bb,
                )
                if signal:
                    signal.notes = f"Mean reversion LONG: RSI_{trigger_tf}={trigger_rsi:.1f}, near lower BB"
                    logger.info(
                        f"LONG signal for {symbol}: RSI_{trigger_tf}={trigger_rsi:.2f} (no MTF alignment required)"
                    )
                    self._set_cooldown(symbol)
                    return [signal]

            # Check for SHORT signal (RSI overbought + price near upper BB)
            # REMOVED: Multi-TF RSI alignment requirement
            if self._check_short_conditions_v2(
                trigger_rsi, current_price, upper_bb, middle_bb, symbol
            ):
                signal = self._create_short_signal(
                    symbol=symbol,
                    current_price=current_price,
                    atr=atr_15m,
                    sma_target=sma_15m,
                    rsi_15m=rsi_15m,
                    trigger_rsi=trigger_rsi,
                    trigger_tf=trigger_tf,
                    rsi_1h=rsi_1h,
                    lower_bb=lower_bb,
                    middle_bb=middle_bb,
                    upper_bb=upper_bb,
                )
                if signal:
                    signal.notes = f"Mean reversion SHORT: RSI_{trigger_tf}={trigger_rsi:.1f}, near upper BB"
                    logger.info(
                        f"SHORT signal for {symbol}: RSI_{trigger_tf}={trigger_rsi:.2f} (no MTF alignment required)"
                    )
                    self._set_cooldown(symbol)
                    return [signal]

            # No signal - log summary of why
            logger.debug(
                f"[{symbol}] No mean reversion signal: RSI={trigger_rsi:.2f} "
                f"(oversold={self.rsi_oversold}, overbought={self.rsi_overbought}), "
                f"Price={current_price:.2f} vs BB=({lower_bb:.2f}, {middle_bb:.2f}, {upper_bb:.2f})"
            )
            return []

        except Exception as e:
            logger.error(f"Error generating mean reversion signal for {symbol}: {e}")
            return []

    def _validate_data(self, data: Dict[str, List[float]]) -> bool:
        """Validate that data has required fields and sufficient length."""
        required_keys = ["high", "low", "close"]
        min_length = max(self.rsi_period + 1, self.bb_period, self.atr_period + 1)

        for key in required_keys:
            if key not in data:
                return False
            if len(data[key]) < min_length:
                return False

        return True

    def _check_long_conditions_v2(
        self,
        trigger_rsi: float,
        current_price: float,
        lower_bb: float,
        middle_bb: float,
        symbol: str = "unknown",
    ) -> bool:
        """
        Check if LONG signal conditions are met (v2 - no multi-TF alignment required).

        Conditions:
        1. Trigger RSI (5m or 15m) < oversold threshold (30)
        2. Price within 20% of lower Bollinger Band (structure validation)

        NOTE: 1h regime permission is handled by StrategyManager.
        This method does NOT require both 15m AND 1h RSI to be oversold.
        """
        # RSI oversold on trigger timeframe
        if trigger_rsi >= self.rsi_oversold:
            logger.debug(
                f"[{symbol}] LONG conditions FAILED: RSI {trigger_rsi:.2f} >= oversold threshold {self.rsi_oversold}"
            )
            return False

        # Price near lower Bollinger Band (structure validation)
        bb_range = middle_bb - lower_bb
        distance_from_lower_bb = current_price - lower_bb
        distance_pct = (distance_from_lower_bb / bb_range) if bb_range > 0 else 1.0

        if distance_pct > self.bb_proximity:
            logger.debug(
                f"[{symbol}] LONG conditions FAILED: Price too far from lower BB "
                f"(distance_pct={distance_pct:.2f} > {self.bb_proximity}, price={current_price:.2f}, lower_bb={lower_bb:.2f})"
            )
            return False

        logger.debug(
            f"[{symbol}] LONG conditions PASSED: RSI={trigger_rsi:.2f} < {self.rsi_oversold}, "
            f"BB distance={distance_pct:.2f}"
        )
        return True

    def _check_short_conditions_v2(
        self,
        trigger_rsi: float,
        current_price: float,
        upper_bb: float,
        middle_bb: float,
        symbol: str = "unknown",
    ) -> bool:
        """
        Check if SHORT signal conditions are met (v2 - no multi-TF alignment required).

        Conditions:
        1. Trigger RSI (5m or 15m) > overbought threshold (70)
        2. Price within 20% of upper Bollinger Band (structure validation)

        NOTE: 1h regime permission is handled by StrategyManager.
        This method does NOT require both 15m AND 1h RSI to be overbought.
        """
        # RSI overbought on trigger timeframe
        if trigger_rsi <= self.rsi_overbought:
            logger.debug(
                f"[{symbol}] SHORT conditions FAILED: RSI {trigger_rsi:.2f} <= overbought threshold {self.rsi_overbought}"
            )
            return False

        # Price near upper Bollinger Band (structure validation)
        bb_range = upper_bb - middle_bb
        distance_from_upper_bb = upper_bb - current_price
        distance_pct = (distance_from_upper_bb / bb_range) if bb_range > 0 else 1.0

        if distance_pct > self.bb_proximity:
            logger.debug(
                f"[{symbol}] SHORT conditions FAILED: Price too far from upper BB "
                f"(distance_pct={distance_pct:.2f} > {self.bb_proximity}, price={current_price:.2f}, upper_bb={upper_bb:.2f})"
            )
            return False

        logger.debug(
            f"[{symbol}] SHORT conditions PASSED: RSI={trigger_rsi:.2f} > {self.rsi_overbought}, "
            f"BB distance={distance_pct:.2f}"
        )
        return True

    # DISABLED: Legacy methods that required multi-TF RSI alignment
    # Kept for reference - see _check_long_conditions_v2 for new approach
    def _check_long_conditions(
        self,
        rsi_15m: float,
        rsi_1h: float,
        current_price: float,
        lower_bb: float,
        middle_bb: float,
    ) -> bool:
        """DEPRECATED: Use _check_long_conditions_v2 instead."""
        # DISABLED: TF separation - see 002
        # if rsi_15m >= self.rsi_oversold:
        #     return False
        # if rsi_1h >= self.rsi_oversold:
        #     return False
        return self._check_long_conditions_v2(
            rsi_15m, current_price, lower_bb, middle_bb
        )

    def _check_short_conditions(
        self,
        rsi_15m: float,
        rsi_1h: float,
        current_price: float,
        upper_bb: float,
        middle_bb: float,
    ) -> bool:
        """DEPRECATED: Use _check_short_conditions_v2 instead."""
        # DISABLED: TF separation - see 002
        # if rsi_15m <= self.rsi_overbought:
        #     return False
        # if rsi_1h <= self.rsi_overbought:
        #     return False
        return self._check_short_conditions_v2(
            rsi_15m, current_price, upper_bb, middle_bb
        )

    def _create_long_signal(
        self,
        symbol: str,
        current_price: float,
        atr: float,
        sma_target: float,
        rsi_15m: float,
        trigger_rsi: float,
        trigger_tf: str,
        rsi_1h: Optional[float],
        lower_bb: float,
        middle_bb: float,
        upper_bb: float,
    ) -> Optional[Signal]:
        """Create LONG signal with stop loss and take profit.

        Args:
            symbol: Trading symbol.
            current_price: Entry price.
            atr: 15m ATR for stop placement.
            sma_target: 15m SMA (reference only; TP is the upper BB).
            rsi_15m: 15m structure RSI.
            trigger_rsi: Trigger RSI (5m when available, else 15m).
            trigger_tf: Which timeframe trigger_rsi came from.
            rsi_1h: Genuine 1h RSI, or None when unavailable.
            lower_bb: Lower Bollinger Band.
            middle_bb: Middle Bollinger Band.
            upper_bb: Upper Bollinger Band (take-profit target).

        Returns:
            The Signal, or None when the RRR filter rejects it.
        """
        # Stop Loss: 2x ATR below entry
        stop_loss = current_price - (atr * self.atr_stop_multiplier)

        # Take Profit: Full range reversion to upper Bollinger Band
        # Using upper BB instead of SMA gives a 2:1+ RRR vs the 1:1 from SMA
        take_profit = upper_bb

        # RRR filter: skip entries where reward < min_rrr x risk
        risk = current_price - stop_loss
        reward = take_profit - current_price
        rrr = reward / risk if risk > 0 else 0
        if rrr < self.min_rrr:
            logger.debug(
                f"[{symbol}] LONG skipped: RRR {rrr:.2f} < min_rrr {self.min_rrr} "
                f"(TP={take_profit:.4f}, SL={stop_loss:.4f}, entry={current_price:.4f})"
            )
            return None

        # Calculate confidence (0-1 scale)
        # Higher confidence when:
        # - RSI is more oversold
        # - Price is closer to lower BB
        # - Multi-timeframe RSI alignment is stronger
        rsi_strength = (self.rsi_oversold - rsi_15m) / self.rsi_oversold  # 0-1
        bb_proximity = (
            (current_price - lower_bb) / (middle_bb - lower_bb)
            if (middle_bb - lower_bb) > 0
            else 0
        )  # 0-1
        bb_proximity = 1.0 - bb_proximity  # Invert (closer = higher)
        mtf_rsi = self._mtf_rsi(trigger_rsi, rsi_1h)
        mtf_alignment = (
            None
            if mtf_rsi is None
            else (self.rsi_oversold - mtf_rsi) / self.rsi_oversold
        )

        confidence = self._blend_confidence(rsi_strength, bb_proximity, mtf_alignment)

        # DEBUG: Log confidence components
        logger.debug(
            f"[{symbol}] LONG confidence breakdown "
            f"(mode={self.mtf_confidence_mode}): "
            f"rsi_strength={rsi_strength:.3f}, "
            f"bb_proximity={bb_proximity:.3f}, "
            f"mtf_alignment="
            f"{'none' if mtf_alignment is None else f'{mtf_alignment:.3f}'} => "
            f"TOTAL={confidence:.3f} "
            f"(min_conf={self.min_confidence}, not a gate)"
        )

        # Prompt 058: Confidence affects SIZE, not permission
        # Low confidence = smaller position via ConfidenceSizer, not blocked
        if confidence < 0.3:
            logger.warning(
                f"[{symbol}] LONG signal: Very low confidence {confidence:.2f} (will use minimum position size)"
            )

        # Create signal
        signal = Signal(
            strategy=StrategyType.MEAN_REVERSION,
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
            market_state=MarketState.RANGE,
            timeframe="15m",
            pattern="oversold_mean_reversion",
            volume_confirmation=True,  # Assume volume is ok (can enhance later)
            # NOTE: not a measured 1h/15m RSI agreement - this strategy
            # deliberately requires no MTF alignment (see prompt 003).
            multi_timeframe_alignment=True,
            support_resistance_valid=True,  # Lower BB acts as support
            rrr_meets_minimum=True,  # Will be validated by signal.is_valid()
            liquidation_buffer_safe=True,  # Will be validated externally
            account_risk_ok=True,  # Will be validated externally
            margin_drawdown_ok=True,  # Will be validated externally
            forbidden_conditions_clear=True,  # No forbidden conditions
            indicators=self._build_indicators(
                rsi_15m,
                trigger_rsi,
                trigger_tf,
                rsi_1h,
                atr,
                sma_target,
                lower_bb,
                middle_bb,
                upper_bb,
            ),
            notes=(
                f"Mean reversion LONG: RSI_15m={rsi_15m:.1f}, "
                f"RSI_{trigger_tf}={trigger_rsi:.1f}, Price near lower BB"
            ),
        )

        return signal

    @staticmethod
    def _build_indicators(
        rsi_15m: float,
        trigger_rsi: float,
        trigger_tf: str,
        rsi_1h: Optional[float],
        atr: float,
        sma_target: float,
        lower_bb: float,
        middle_bb: float,
        upper_bb: float,
    ) -> Dict[str, Any]:
        """Build the signal indicator payload with honest timeframe labels.

        ``rsi_1h`` is emitted ONLY when a genuine 1h RSI was computed.
        Historically this key carried the trigger RSI, so every stored
        MeanReversion signal predating this fix mislabels 5m/15m data as 1h.

        Args:
            rsi_15m: 15m structure RSI.
            trigger_rsi: Trigger RSI value.
            trigger_tf: Timeframe trigger_rsi came from ("5m" or "15m").
            rsi_1h: Genuine 1h RSI, or None.
            atr: 15m ATR.
            sma_target: 15m SMA.
            lower_bb: Lower Bollinger Band.
            middle_bb: Middle Bollinger Band.
            upper_bb: Upper Bollinger Band.

        Returns:
            Indicator dict for the Signal.
        """
        indicators: Dict[str, Any] = {
            "rsi_15m": rsi_15m,
            "rsi_trigger": trigger_rsi,
            "rsi_trigger_tf": trigger_tf,
            "atr": atr,
            "sma": sma_target,
            "lower_bb": lower_bb,
            "middle_bb": middle_bb,
            "upper_bb": upper_bb,
        }
        if rsi_1h is not None:
            indicators["rsi_1h"] = rsi_1h
        return indicators

    def _create_short_signal(
        self,
        symbol: str,
        current_price: float,
        atr: float,
        sma_target: float,
        rsi_15m: float,
        trigger_rsi: float,
        trigger_tf: str,
        rsi_1h: Optional[float],
        lower_bb: float,
        middle_bb: float,
        upper_bb: float,
    ) -> Optional[Signal]:
        """Create SHORT signal with stop loss and take profit.

        Args:
            symbol: Trading symbol.
            current_price: Entry price.
            atr: 15m ATR for stop placement.
            sma_target: 15m SMA (reference only; TP is the lower BB).
            rsi_15m: 15m structure RSI.
            trigger_rsi: Trigger RSI (5m when available, else 15m).
            trigger_tf: Which timeframe trigger_rsi came from.
            rsi_1h: Genuine 1h RSI, or None when unavailable.
            lower_bb: Lower Bollinger Band (take-profit target).
            middle_bb: Middle Bollinger Band.
            upper_bb: Upper Bollinger Band.

        Returns:
            The Signal, or None when the RRR filter rejects it.
        """
        # Stop Loss: 2x ATR above entry
        stop_loss = current_price + (atr * self.atr_stop_multiplier)

        # Take Profit: Full range reversion to lower Bollinger Band
        # Using lower BB instead of SMA gives a 2:1+ RRR vs the 1:1 from SMA
        take_profit = lower_bb

        # RRR filter: skip entries where reward < min_rrr x risk
        risk = stop_loss - current_price
        reward = current_price - take_profit
        rrr = reward / risk if risk > 0 else 0
        if rrr < self.min_rrr:
            logger.debug(
                f"[{symbol}] SHORT skipped: RRR {rrr:.2f} < min_rrr {self.min_rrr} "
                f"(TP={take_profit:.4f}, SL={stop_loss:.4f}, entry={current_price:.4f})"
            )
            return None

        # Calculate confidence (0-1 scale)
        rsi_strength = (rsi_15m - self.rsi_overbought) / (
            100 - self.rsi_overbought
        )  # 0-1
        bb_proximity = (
            (upper_bb - current_price) / (upper_bb - middle_bb)
            if (upper_bb - middle_bb) > 0
            else 0
        )  # 0-1
        bb_proximity = 1.0 - bb_proximity  # Invert (closer = higher)
        mtf_rsi = self._mtf_rsi(trigger_rsi, rsi_1h)
        mtf_alignment = (
            None
            if mtf_rsi is None
            else (mtf_rsi - self.rsi_overbought) / (100 - self.rsi_overbought)
        )

        confidence = self._blend_confidence(rsi_strength, bb_proximity, mtf_alignment)

        # DEBUG: Log confidence components
        logger.debug(
            f"[{symbol}] SHORT confidence breakdown "
            f"(mode={self.mtf_confidence_mode}): "
            f"rsi_strength={rsi_strength:.3f}, "
            f"bb_proximity={bb_proximity:.3f}, "
            f"mtf_alignment="
            f"{'none' if mtf_alignment is None else f'{mtf_alignment:.3f}'} => "
            f"TOTAL={confidence:.3f} "
            f"(min_conf={self.min_confidence}, not a gate)"
        )

        # Prompt 058: Confidence affects SIZE, not permission
        # Low confidence = smaller position via ConfidenceSizer, not blocked
        if confidence < 0.3:
            logger.warning(
                f"[{symbol}] SHORT signal: Very low confidence {confidence:.2f} (will use minimum position size)"
            )

        # Create signal
        signal = Signal(
            strategy=StrategyType.MEAN_REVERSION,
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
            market_state=MarketState.RANGE,
            timeframe="15m",
            pattern="overbought_mean_reversion",
            volume_confirmation=True,  # Assume volume is ok (can enhance later)
            # NOTE: not a measured 1h/15m RSI agreement - this strategy
            # deliberately requires no MTF alignment (see prompt 003).
            multi_timeframe_alignment=True,
            support_resistance_valid=True,  # Upper BB acts as resistance
            rrr_meets_minimum=True,  # Will be validated by signal.is_valid()
            liquidation_buffer_safe=True,  # Will be validated externally
            account_risk_ok=True,  # Will be validated externally
            margin_drawdown_ok=True,  # Will be validated externally
            forbidden_conditions_clear=True,  # No forbidden conditions
            indicators=self._build_indicators(
                rsi_15m,
                trigger_rsi,
                trigger_tf,
                rsi_1h,
                atr,
                sma_target,
                lower_bb,
                middle_bb,
                upper_bb,
            ),
            notes=(
                f"Mean reversion SHORT: RSI_15m={rsi_15m:.1f}, "
                f"RSI_{trigger_tf}={trigger_rsi:.1f}, Price near upper BB"
            ),
        )

        return signal
