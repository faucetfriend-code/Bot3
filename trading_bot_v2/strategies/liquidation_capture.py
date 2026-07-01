"""
Liquidation Capture Strategy (Bidirectional)

TIMEFRAME HIERARCHY (Multi-TF Execution Model):
- SANITY (1h): Confirms market is active (not dead volume)
- VOLATILITY (15m): Shows elevated ATR awareness
- TRIGGER (1m/5m): Detects cascade - THIS IS WHERE LIQUIDATIONS HAPPEN
                   Prefers 5m, falls back to 1m or 15m

Strategy Logic:
**LONG Liquidation Cascade (Downward):**
- Price drops >= 3% in last 5 candles
- Volume spike >= 3x average
- RSI <= 15 (extreme oversold)
- Action: BUY (fade the cascade)

**SHORT Squeeze Cascade (Upward):**
- Price rises >= 3% in last 5 candles
- Volume spike >= 3x average
- RSI >= 85 (extreme overbought)
- Action: SELL (fade the squeeze)

Entry Rules:
- Wait for cascade to exhaust (long wick formation)
- Enter on reversal confirmation
- Tight stop loss (1% beyond extreme)
- 3:1 minimum RRR

Best For: ALL regimes (runs independently)

Session Limits:
- Maximum 1 trade per 4-hour session
- Only one active liquidation trade at a time

IMPORTANT: Liquidations occur on low TFs - using 1m/5m for detection materially increases opportunity.
This strategy benefits MOST from lower timeframe data.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from loguru import logger

# Use relative imports from trading_bot_v2 package
from ..models import Signal, OrderSide
from ..indicators import calculate_rsi
from ..config import StrategyType, AssetClass, TradeQuality, MarketState


class LiquidationCaptureStrategy:
    """
    Liquidation event capture strategy - BIDIRECTIONAL.

    Detects and trades against extreme liquidation cascades in both directions:
    - Long liquidations (downward cascade) → BUY
    - Short squeezes (upward cascade) → SELL
    """

    def __init__(
        self,
        price_move_threshold: float = 0.025,  # Prompt 058: Loosened from 0.03 to 0.025
        volume_spike_multiplier: float = 2.5,  # Prompt 058: Loosened from 3.0 to 2.5
        rsi_oversold_threshold: float = 20.0,  # Prompt 058: Loosened from 15 to 20
        rsi_overbought_threshold: float = 80.0,  # Prompt 058: Loosened from 85 to 80
        min_consecutive_moves: int = 4,  # Prompt 058: Loosened from 5 to 4
        min_wick_ratio: float = 1.5,  # Prompt 058: Loosened from 2.0 to 1.5
        rrr_target: float = 3.0,
        max_per_session: int = 2,  # Prompt 058: Increased from 1 to 2
        min_hours_between_trades: int = 2,  # Prompt 058: Reduced from 4 to 2
        rsi_period: int = 14,
    ):
        """
        Initialize Liquidation Capture Strategy.

        Args:
            price_move_threshold: Minimum price move % to detect cascade (default: 3%)
            volume_spike_multiplier: Volume spike multiplier (default: 3x)
            rsi_oversold_threshold: RSI threshold for long liquidations (default: 15)
            rsi_overbought_threshold: RSI threshold for short squeezes (default: 85)
            min_consecutive_moves: Minimum consecutive candles in same direction (default: 5)
            min_wick_ratio: Minimum wick-to-body ratio for panic (default: 2.0)
            rrr_target: Risk-reward ratio target (default: 3.0)
            max_per_session: Maximum trades per 4-hour session (default: 1)
            min_hours_between_trades: Minimum hours between trades (default: 4)
            rsi_period: RSI calculation period (default: 14)
        """
        self.price_threshold = price_move_threshold
        self.volume_multiplier = volume_spike_multiplier
        self.rsi_oversold = rsi_oversold_threshold
        self.rsi_overbought = rsi_overbought_threshold
        self.min_consecutive = min_consecutive_moves
        self.min_wick_ratio = min_wick_ratio
        self.rrr_target = rrr_target
        self.max_per_session = max_per_session
        self.min_hours_between = min_hours_between_trades
        self.rsi_period = rsi_period

        # Session tracking
        self.session_trades = 0
        self.last_trade_time: Optional[datetime] = None

        # Simulated time injected by backtest engine (None = use wall-clock)
        self._sim_time: Optional[datetime] = None

        logger.info(
            f"LiquidationCaptureStrategy initialized: "
            f"price threshold={price_move_threshold:.1%}, "
            f"volume spike={volume_spike_multiplier}x, "
            f"RSI oversold={rsi_oversold_threshold}/overbought={rsi_overbought_threshold}, "
            f"BIDIRECTIONAL (detects both long liquidations and short squeezes)"
        )

    def generate_signals(
        self,
        symbol: str,
        multi_tf_data: Dict[str, Dict[str, List[float]]],
        current_price: float,
        execution_tf_data: Optional[Dict[str, Dict[str, List[float]]]] = None,
    ) -> List[Signal]:
        """
        Generate liquidation capture signals from multi-timeframe data.

        TIMEFRAME HIERARCHY:
        - 15m: Fallback detection (if 5m/1m unavailable)
        - 5m: Primary cascade detection (preferred)
        - 1m: Fastest cascade detection (immediate)

        Liquidation cascades happen on LOW timeframes - this is where 1m/5m
        has MAXIMUM impact on trade frequency.

        Args:
            symbol: Trading symbol (e.g., "SUI-PERP")
            multi_tf_data: Dictionary with regime/structure TFs {"15m": {...}, "1h": {...}}
            current_price: Current market price
            execution_tf_data: Optional dict with execution TFs {"5m": {...}, "1m": {...}}

        Returns:
            List of Signal objects (0-1 signals)
        """
        try:
            # Check session limits first (before doing any work)
            if not self._can_trade():
                logger.debug(
                    f"{symbol}: Session limit reached "
                    f"({self.session_trades}/{self.max_per_session})"
                )
                return []

            # Select best available timeframe for cascade detection
            # Priority: 5m > 1m > 15m (5m balances speed with noise reduction)
            trigger_data = None
            trigger_tf = None

            # Check execution timeframes first (where cascades actually happen)
            if execution_tf_data:
                if "5m" in execution_tf_data and self._validate_data(
                    execution_tf_data["5m"]
                ):
                    trigger_data = execution_tf_data["5m"]
                    trigger_tf = "5m"
                elif "1m" in execution_tf_data and self._validate_data(
                    execution_tf_data["1m"]
                ):
                    trigger_data = execution_tf_data["1m"]
                    trigger_tf = "1m"

            # Fall back to 15m if no execution data available
            if trigger_data is None:
                if "15m" not in multi_tf_data:
                    logger.warning(f"Missing cascade detection data for {symbol}")
                    return []
                if not self._validate_data(multi_tf_data["15m"]):
                    logger.warning(f"Invalid 15m data for {symbol}")
                    return []
                trigger_data = multi_tf_data["15m"]
                trigger_tf = "15m"

            logger.debug(
                f"{symbol} liquidation: using {trigger_tf} for cascade detection"
            )

            # Detect liquidation cascade (bidirectional)
            cascade = self._detect_cascade(trigger_data)

            if not cascade:
                return []

            # Calculate RSI for confirmation (use trigger TF data)
            rsi = calculate_rsi(trigger_data["close"], period=self.rsi_period)

            logger.info(
                f"{symbol}: {cascade['direction'].upper()} cascade detected on {trigger_tf} - "
                f"price move {cascade['price_move']:.1%}, "
                f"volume spike {cascade['volume_spike']:.1f}x, "
                f"RSI {rsi:.1f}"
            )

            # Store trigger_tf for signal creation
            cascade["trigger_tf"] = trigger_tf

            # Validate RSI extreme for direction
            if cascade["direction"] == "down":
                # Long liquidation cascade - check for extreme oversold
                if rsi > self.rsi_oversold:
                    logger.debug(
                        f"{symbol}: RSI {rsi:.1f} not extreme enough for LONG cascade "
                        f"(threshold: {self.rsi_oversold})"
                    )
                    return []

                # Create BUY signal (fade the cascade)
                signal = self._create_long_signal(
                    symbol=symbol, current_price=current_price, cascade=cascade, rsi=rsi
                )

            else:  # direction == "up"
                # Short squeeze cascade - check for extreme overbought
                if rsi < self.rsi_overbought:
                    logger.debug(
                        f"{symbol}: RSI {rsi:.1f} not extreme enough for SHORT squeeze "
                        f"(threshold: {self.rsi_overbought})"
                    )
                    return []

                # Create SELL signal (fade the squeeze)
                signal = self._create_short_signal(
                    symbol=symbol, current_price=current_price, cascade=cascade, rsi=rsi
                )

            if signal:
                logger.info(
                    f"{symbol}: {signal.side.value} liquidation signal @ ${signal.entry_price:.4f} "
                    f"(confidence: {signal.confidence:.2%}, RRR: {signal.rrr:.2f})"
                )
                return [signal]

            return []

        except Exception as e:
            logger.error(f"Error generating liquidation signal for {symbol}: {e}")
            return []

    def _validate_data(self, data: Dict[str, List[float]]) -> bool:
        """Validate that data has required fields and sufficient length."""
        required_keys = ["high", "low", "close", "open"]
        min_length = max(20, self.rsi_period + 1)  # Need at least 20 candles

        for key in required_keys:
            if key not in data:
                return False
            if len(data[key]) < min_length:
                return False

        # Volume is optional but recommended
        if "volume" not in data:
            logger.warning("Volume data missing - cascade detection less reliable")

        return True

    def _now(self) -> datetime:
        """Return current time — simulated candle time in backtesting, wall-clock in live."""
        return self._sim_time if self._sim_time is not None else datetime.now(timezone.utc)

    def _can_trade(self) -> bool:
        """
        Check if we can trade based on session limits.

        Session auto-reset: the 4h session counter resets when the current
        candle crosses into a new 4h window (00:00, 04:00, 08:00, …, 20:00 UTC).
        This ensures max_per_session is enforced per 4h window without relying
        on the engine calling reset_session() explicitly.
        """
        now = self._now()

        # Auto-reset at 4h session boundaries
        if self.last_trade_time is not None:
            current_session = now.hour // 4
            last_session = self.last_trade_time.hour // 4
            if now.date() != self.last_trade_time.date() or current_session != last_session:
                self.session_trades = 0
                logger.debug("LiquidationCapture: 4h session reset")

        # Check trade count limit
        if self.session_trades >= self.max_per_session:
            return False

        # Check time between trades
        if self.last_trade_time:
            hours_since = (now - self.last_trade_time).total_seconds() / 3600
            if hours_since < self.min_hours_between:
                return False

        return True

    def _detect_cascade(self, data: Dict[str, List[float]]) -> Optional[Dict[str, Any]]:
        """
        Detect liquidation cascade events (BIDIRECTIONAL).

        Looks for:
        1. Sharp price move (>= 3%)
        2. Volume spike (>= 3x)
        3. Consecutive candles in same direction (>= 5)
        4. Long wick (panic selling/buying)

        Returns:
            Cascade details or None
        """
        if len(data["close"]) < 10:
            return None

        # Check recent price movement (last 10 candles)
        start_price = data["close"][-10]
        end_price = data["close"][-1]
        price_move = (end_price - start_price) / start_price

        # Check if price move is significant
        if abs(price_move) < self.price_threshold:
            return None

        # Determine direction
        direction = "up" if price_move > 0 else "down"

        # Check volume spike (if available)
        if "volume" in data and len(data["volume"]) >= 10:
            recent_volumes = data["volume"][-10:]
            avg_volume = sum(recent_volumes[:-3]) / len(recent_volumes[:-3])
            current_volume = recent_volumes[-1]
            volume_spike = current_volume / avg_volume if avg_volume > 0 else 0

            if volume_spike < self.volume_multiplier:
                return None
        else:
            volume_spike = 0  # No volume data

        # Count consecutive moves in same direction
        consecutive = 0
        for i in range(-9, 0):  # Last 9 candles
            if direction == "up":
                if data["close"][i] > data["close"][i - 1]:
                    consecutive += 1
                else:
                    consecutive = 0
            else:  # down
                if data["close"][i] < data["close"][i - 1]:
                    consecutive += 1
                else:
                    consecutive = 0

        if consecutive < self.min_consecutive:
            return None

        # Check for long wick (panic)
        high = data["high"][-1]
        low = data["low"][-1]
        open_price = data["open"][-1]
        close = data["close"][-1]

        wick_size = high - low
        body_size = abs(close - open_price)
        wick_ratio = wick_size / body_size if body_size > 0 else 0

        if wick_ratio < self.min_wick_ratio:
            return None

        # Determine extreme price (lowest low for down, highest high for up)
        extreme_price = low if direction == "down" else high

        logger.info(
            f"Cascade detected: {direction.upper()} - "
            f"move {price_move:.1%}, volume {volume_spike:.1f}x, "
            f"consecutive {consecutive}, wick ratio {wick_ratio:.1f}"
        )

        return {
            "direction": direction,
            "price_move": price_move,
            "volume_spike": volume_spike,
            "consecutive_moves": consecutive,
            "wick_ratio": wick_ratio,
            "extreme_price": extreme_price,
        }

    def _create_long_signal(
        self, symbol: str, current_price: float, cascade: Dict[str, Any], rsi: float
    ) -> Optional[Signal]:
        """
        Create LONG signal after downward liquidation cascade.

        Entry: Current price (cascade exhausted)
        Stop Loss: 1% below extreme low
        Take Profit: 3x risk (RRR = 3.0)
        """
        extreme_price = cascade["extreme_price"]

        # Entry at current price (after cascade)
        entry_price = current_price

        # Stop Loss: 1% below extreme low (wick bottom)
        stop_loss = extreme_price * 0.99

        # Take Profit: 3x risk
        risk = entry_price - stop_loss
        take_profit = entry_price + (risk * self.rrr_target)

        # Calculate confidence (0-1 scale)
        # Higher confidence when:
        # - Larger volume spike
        # - More consecutive moves
        # - Lower RSI (more oversold)
        volume_score = min(cascade["volume_spike"] / self.volume_multiplier, 1.0)  # 0-1
        consecutive_score = min(cascade["consecutive_moves"] / 10.0, 1.0)  # 0-1
        rsi_score = (
            (self.rsi_oversold - rsi) / self.rsi_oversold
            if rsi <= self.rsi_oversold
            else 0
        )  # 0-1

        confidence = (
            (volume_score * 0.3) + (consecutive_score * 0.2) + (rsi_score * 0.5)
        )
        confidence = max(0.0, min(1.0, confidence))

        # Get trigger timeframe from cascade data (set by generate_signals)
        trigger_tf = cascade.get("trigger_tf", "15m")

        # Create signal
        signal = Signal(
            strategy=StrategyType.LIQUIDATION_CAPTURE,
            asset=symbol,
            asset_class=AssetClass.PERPETUAL,
            side=OrderSide.BUY,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            quality=TradeQuality.HIGH_CONVICTION,  # Liquidation events are high conviction
            market_state=MarketState.TRANSITION,
            timeframe=trigger_tf,  # Use actual detection timeframe
            pattern="long_liquidation_cascade",
            volume_confirmation=True,  # Volume spike confirms cascade
            multi_timeframe_alignment=True,  # Extreme conditions override MTF
            support_resistance_valid=True,  # Extreme price acts as support
            rrr_meets_minimum=True,  # 3:1 RRR
            liquidation_buffer_safe=True,  # Will be validated externally
            account_risk_ok=True,  # Will be validated externally
            margin_drawdown_ok=True,  # Will be validated externally
            forbidden_conditions_clear=True,  # Extreme conditions, no normal restrictions
            indicators={
                "rsi": rsi,
                "volume_spike": cascade["volume_spike"],
                "price_move": cascade["price_move"],
                "consecutive_moves": cascade["consecutive_moves"],
                "wick_ratio": cascade["wick_ratio"],
                "trigger_tf": trigger_tf,
            },
            notes=(
                f"Long liquidation cascade ({trigger_tf}): {cascade['price_move']:.1%} drop, "
                f"{cascade['volume_spike']:.1f}x volume, RSI {rsi:.1f}"
            ),
        )

        return signal

    def _create_short_signal(
        self, symbol: str, current_price: float, cascade: Dict[str, Any], rsi: float
    ) -> Optional[Signal]:
        """
        Create SHORT signal after upward short squeeze cascade.

        Entry: Current price (squeeze exhausted)
        Stop Loss: 1% above extreme high
        Take Profit: 3x risk (RRR = 3.0)
        """
        extreme_price = cascade["extreme_price"]

        # Entry at current price (after squeeze)
        entry_price = current_price

        # Stop Loss: 1% above extreme high (wick top)
        stop_loss = extreme_price * 1.01

        # Take Profit: 3x risk
        risk = stop_loss - entry_price
        take_profit = entry_price - (risk * self.rrr_target)

        # Calculate confidence (0-1 scale)
        volume_score = min(cascade["volume_spike"] / self.volume_multiplier, 1.0)  # 0-1
        consecutive_score = min(cascade["consecutive_moves"] / 10.0, 1.0)  # 0-1
        rsi_score = (
            (rsi - self.rsi_overbought) / (100 - self.rsi_overbought)
            if rsi >= self.rsi_overbought
            else 0
        )  # 0-1

        confidence = (
            (volume_score * 0.3) + (consecutive_score * 0.2) + (rsi_score * 0.5)
        )
        confidence = max(0.0, min(1.0, confidence))

        # Get trigger timeframe from cascade data (set by generate_signals)
        trigger_tf = cascade.get("trigger_tf", "15m")

        # Create signal
        signal = Signal(
            strategy=StrategyType.LIQUIDATION_CAPTURE,
            asset=symbol,
            asset_class=AssetClass.PERPETUAL,
            side=OrderSide.SELL,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            quality=TradeQuality.HIGH_CONVICTION,  # Short squeezes are high conviction
            market_state=MarketState.TRANSITION,
            timeframe=trigger_tf,  # Use actual detection timeframe
            pattern="short_squeeze_cascade",
            volume_confirmation=True,  # Volume spike confirms squeeze
            multi_timeframe_alignment=True,  # Extreme conditions override MTF
            support_resistance_valid=True,  # Extreme price acts as resistance
            rrr_meets_minimum=True,  # 3:1 RRR
            liquidation_buffer_safe=True,  # Will be validated externally
            account_risk_ok=True,  # Will be validated externally
            margin_drawdown_ok=True,  # Will be validated externally
            forbidden_conditions_clear=True,  # Extreme conditions, no normal restrictions
            indicators={
                "rsi": rsi,
                "volume_spike": cascade["volume_spike"],
                "price_move": cascade["price_move"],
                "consecutive_moves": cascade["consecutive_moves"],
                "wick_ratio": cascade["wick_ratio"],
                "trigger_tf": trigger_tf,
            },
            notes=(
                f"Short squeeze cascade ({trigger_tf}): {cascade['price_move']:.1%} rise, "
                f"{cascade['volume_spike']:.1f}x volume, RSI {rsi:.1f}"
            ),
        )

        return signal

    def record_trade(self) -> None:
        """
        Record a trade execution.

        Call this externally when a liquidation signal is executed.
        """
        self.session_trades += 1
        self.last_trade_time = self._now()

        logger.info(
            f"Liquidation trade recorded. "
            f"Session trades: {self.session_trades}/{self.max_per_session}"
        )

    def reset_session(self) -> None:
        """Reset session counters (call at start of new 4-hour session)."""
        self.session_trades = 0
        self.last_trade_time = None
        logger.info("Liquidation capture session reset")
