"""
Order Book Imbalance Strategy

Analyzes Level 2 market depth to detect buying/selling pressure.
Uses bid/ask volume imbalance to generate directional signals.

DESIGN NOTES:
- Real-time overlay strategy (tick/sub-second)
- Runs in ALL regimes
- Complements price-based strategies with flow analysis
- All sizing delegated to RiskManager

RISK NOTES:
- Requires low-latency execution
- High frequency = higher fee drag
- Spoofing can create false signals
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
from loguru import logger

from ..models import Signal, OrderSide
from ..config import StrategyType, TradeQuality, MarketState, AssetClass


def calculate_atr_simple(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> float:
    """Calculate ATR for stop/target calculation."""
    if len(highs) < period + 1:
        return 0.0

    true_ranges = []
    for i in range(1, len(highs)):
        high_low = highs[i] - lows[i]
        high_close = abs(highs[i] - closes[i - 1])
        low_close = abs(lows[i] - closes[i - 1])
        true_ranges.append(max(high_low, high_close, low_close))

    if len(true_ranges) < period:
        return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

    # Wilder's smoothing
    atr = sum(true_ranges[:period]) / period
    for i in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[i]) / period

    return atr


class OrderBookImbalanceStrategy:
    """
    Order book imbalance analysis for directional signals.

    Monitors bid/ask depth to detect institutional flow
    and generate short-term directional signals.
    """

    def __init__(
        self,
        levels: int = 10,  # Price levels to analyze
        imbalance_long_threshold: float = 0.62,  # Imbalance > this = long
        imbalance_short_threshold: float = 0.38,  # Imbalance < this = short
        strong_imbalance_threshold: float = 0.72,  # High conviction threshold
        min_order_density: int = 5,  # Min orders on winning side
        spoof_detection: bool = True,  # Filter potential spoofs
        spoof_size_ratio: float = 5.0,  # Size/orders ratio for spoof
        atr_period: int = 14,
        atr_stop_mult: float = 0.75,  # Tight stop for fast trades
        atr_target_mult: float = 1.5,  # Quick target
        min_confidence: float = 0.55,
        cooldown_seconds: int = 30,
        update_interval_ms: int = 500,  # Min time between analyses
    ):
        self.strategy_type = StrategyType.ORDERBOOK_IMBALANCE
        self.levels = levels
        self.imbalance_long = imbalance_long_threshold
        self.imbalance_short = imbalance_short_threshold
        self.strong_imbalance = strong_imbalance_threshold
        self.min_order_density = min_order_density
        self.spoof_detection = spoof_detection
        self.spoof_size_ratio = spoof_size_ratio
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.min_confidence = min_confidence
        self.cooldown_seconds = cooldown_seconds
        self.update_interval_ms = update_interval_ms

        # State tracking
        self.last_analysis: Dict[str, datetime] = {}
        self.last_trade_time: Dict[str, datetime] = {}
        self.imbalance_history: Dict[str, List[float]] = {}  # Rolling history

        # Simulated time injected by backtest engine (None = use wall-clock)
        self._sim_time: Optional[datetime] = None

        # Orderbook level key mappings (handles different exchange formats)
        # Pacifica uses: "a" for amount, "p" for price
        # Some exchanges use: "amount"/"size" for amount, "price" for price, "n"/"count" for order count
        self._amount_keys = ["a", "amount", "size", "qty"]
        self._price_keys = ["p", "price"]
        self._count_keys = ["n", "count", "orders"]

        logger.info(
            f"OrderBookImbalanceStrategy initialized: levels={levels}, "
            f"long>{imbalance_long_threshold:.0%}, short<{imbalance_short_threshold:.0%}, "
            f"ATR stop={atr_stop_mult}x, target={atr_target_mult}x"
        )

    def _get_level_value(
        self, level: Dict[str, Any], keys: List[str], default: float = 0
    ) -> float:
        """Extract value from orderbook level using fallback keys."""
        for key in keys:
            if key in level:
                try:
                    return float(level[key])
                except (ValueError, TypeError):
                    continue
        return default

    def _now(self) -> datetime:
        """Return current time — simulated candle time in backtesting, wall-clock in live."""
        return (
            self._sim_time if self._sim_time is not None else datetime.now(timezone.utc)
        )

    def _check_cooldown(self, symbol: str) -> bool:
        """Check if cooldown period has passed."""
        if symbol not in self.last_trade_time:
            return True
        elapsed = self._now() - self.last_trade_time[symbol]
        if elapsed <= timedelta(seconds=self.cooldown_seconds):
            remaining = self.cooldown_seconds - elapsed.total_seconds()
            logger.debug(f"{symbol}: OB imbalance cooldown {remaining:.0f}s remaining")
            return False
        return True

    def _check_update_interval(self, symbol: str) -> bool:
        """Check if enough time passed since last analysis."""
        if symbol not in self.last_analysis:
            return True
        elapsed = self._now() - self.last_analysis[symbol]
        return elapsed > timedelta(milliseconds=self.update_interval_ms)

    def _detect_spoof(self, levels: List[Dict[str, Any]], side: str) -> bool:
        """
        Detect potential spoofing on order book.

        Spoofs often have: large size, few orders, near top of book
        """
        if not self.spoof_detection or len(levels) < 3:
            return False

        for i, level in enumerate(levels[:3]):  # Check top 3 levels
            size = self._get_level_value(level, self._amount_keys, 0)
            order_count = int(self._get_level_value(level, self._count_keys, 1))

            if order_count == 0:
                continue

            size_per_order = size / order_count

            # Large size concentrated in few orders at top = potential spoof
            if size_per_order > self.spoof_size_ratio and order_count < 3 and i == 0:
                logger.debug(
                    f"Potential spoof detected on {side} side: "
                    f"size={size:.2f}, orders={order_count}"
                )
                return True

        return False

    def _calculate_weighted_imbalance(
        self, bids: List[Dict[str, Any]], asks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Calculate imbalance with weighting and additional metrics.

        Returns dict with imbalance and supporting metrics.
        """
        result = {
            "imbalance": 0.5,
            "bid_volume": 0,
            "ask_volume": 0,
            "bid_orders": 0,
            "ask_orders": 0,
            "bid_density": 0,
            "ask_density": 0,
            "spread": 0,
            "best_bid": 0,
            "best_ask": 0,
        }

        if not bids or not asks:
            return result

        # Calculate volumes using fallback keys
        bid_volume = sum(
            self._get_level_value(level, self._amount_keys, 0)
            for level in bids[: self.levels]
        )
        ask_volume = sum(
            self._get_level_value(level, self._amount_keys, 0)
            for level in asks[: self.levels]
        )

        # Calculate order counts using fallback keys
        bid_orders = sum(
            int(self._get_level_value(level, self._count_keys, 1))
            for level in bids[: self.levels]
        )
        ask_orders = sum(
            int(self._get_level_value(level, self._count_keys, 1))
            for level in asks[: self.levels]
        )

        # Calculate spread using fallback keys
        best_bid = self._get_level_value(bids[0], self._price_keys, 0) if bids else 0
        best_ask = self._get_level_value(asks[0], self._price_keys, 0) if asks else 0
        spread = (best_ask - best_bid) / best_bid if best_bid > 0 else 0

        # Imbalance calculation
        total_volume = bid_volume + ask_volume
        imbalance = bid_volume / total_volume if total_volume > 0 else 0.5

        result.update(
            {
                "imbalance": imbalance,
                "bid_volume": bid_volume,
                "ask_volume": ask_volume,
                "bid_orders": bid_orders,
                "ask_orders": ask_orders,
                "bid_density": bid_orders / self.levels if self.levels > 0 else 0,
                "ask_density": ask_orders / self.levels if self.levels > 0 else 0,
                "spread": spread,
                "best_bid": best_bid,
                "best_ask": best_ask,
            }
        )

        return result

    def _update_imbalance_history(
        self, symbol: str, imbalance: float, max_history: int = 20
    ) -> List[float]:
        """Track imbalance history for trend detection."""
        if symbol not in self.imbalance_history:
            self.imbalance_history[symbol] = []

        self.imbalance_history[symbol].append(imbalance)

        # Trim to max length
        if len(self.imbalance_history[symbol]) > max_history:
            self.imbalance_history[symbol] = self.imbalance_history[symbol][
                -max_history:
            ]

        return self.imbalance_history[symbol]

    def _check_imbalance_trend(
        self, symbol: str, direction: str, lookback: int = 5
    ) -> bool:
        """
        Check if imbalance is trending in expected direction.

        Helps filter noise and confirm persistent pressure.
        """
        history = self.imbalance_history.get(symbol, [])
        if len(history) < lookback:
            return True  # Not enough data, allow signal

        recent = history[-lookback:]

        if direction == "long":
            # Imbalance should be increasing or staying high
            return sum(1 for x in recent if x > 0.55) >= lookback * 0.6
        else:
            # Imbalance should be decreasing or staying low
            return sum(1 for x in recent if x < 0.45) >= lookback * 0.6

    def generate_signals(
        self,
        symbol: str,
        multi_tf_data: Dict[str, Any],
        current_price: float,
        orderbook: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> List[Signal]:
        """
        Generate order book imbalance signals.

        Args:
            symbol: Trading symbol
            multi_tf_data: OHLCV data for ATR calculation
            current_price: Current market price
            orderbook: Dict with 'bids' and 'asks' lists (from kwargs or direct)
        """
        signals: List[Signal] = []

        # Get orderbook from kwargs if not passed directly
        if orderbook is None:
            orderbook = kwargs.get("orderbook")

        if not orderbook:
            logger.debug(f"{symbol}: No orderbook data available")
            return signals

        # Check timing constraints
        if not self._check_cooldown(symbol):
            return signals

        if not self._check_update_interval(symbol):
            return signals

        self.last_analysis[symbol] = self._now()

        # Extract orderbook data
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        if len(bids) < 3 or len(asks) < 3:
            logger.debug(f"{symbol}: Insufficient orderbook depth")
            return signals

        # Calculate imbalance metrics
        metrics = self._calculate_weighted_imbalance(bids, asks)
        imbalance = metrics["imbalance"]

        # Update history
        self._update_imbalance_history(symbol, imbalance)

        # Check for spoof on winning side
        if imbalance > self.imbalance_long and self._detect_spoof(bids, "bid"):
            logger.debug(f"{symbol}: Bid spoof detected, skipping long signal")
            return signals

        if imbalance < self.imbalance_short and self._detect_spoof(asks, "ask"):
            logger.debug(f"{symbol}: Ask spoof detected, skipping short signal")
            return signals

        # Determine signal direction
        side = None
        direction = None

        if imbalance > self.imbalance_long:
            side = OrderSide.BUY
            direction = "long"
            if metrics["bid_orders"] < self.min_order_density:
                logger.debug(f"{symbol}: Insufficient bid order density")
                return signals

        elif imbalance < self.imbalance_short:
            side = OrderSide.SELL
            direction = "short"
            if metrics["ask_orders"] < self.min_order_density:
                logger.debug(f"{symbol}: Insufficient ask order density")
                return signals

        if not side:
            return signals

        # Null safety check: ensure direction is set before using it
        if direction is None:
            return signals

        # Check imbalance trend
        if not self._check_imbalance_trend(symbol, direction):
            logger.debug(f"{symbol}: Imbalance trend not confirming {direction}")
            return signals

        # Calculate ATR for stops
        atr_value: float = 0
        execution_tf = kwargs.get("execution_tf_data", {})

        # Try execution timeframes first (1m, 5m), then strategy timeframes
        for tf in ["1m", "5m", "15m"]:
            df = execution_tf.get(tf) or multi_tf_data.get(tf)
            if df:
                highs = list(df.get("high", []))
                lows = list(df.get("low", []))
                closes = list(df.get("close", []))

                if len(closes) >= self.atr_period:
                    atr_value = calculate_atr_simple(
                        highs, lows, closes, self.atr_period
                    )
                    if atr_value > 0:
                        break

        # Fallback ATR if not available
        if atr_value == 0:
            atr_value = current_price * 0.003  # 0.3% as fallback

        # Calculate levels
        if side == OrderSide.BUY:
            stop_loss = current_price - (atr_value * self.atr_stop_mult)
            take_profit = current_price + (atr_value * self.atr_target_mult)
        else:
            stop_loss = current_price + (atr_value * self.atr_stop_mult)
            take_profit = current_price - (atr_value * self.atr_target_mult)

        # Calculate confidence
        confidence = self.min_confidence

        # Boost for strong imbalance
        if imbalance > self.strong_imbalance or imbalance < (1 - self.strong_imbalance):
            confidence += 0.15

        # Boost for high order density
        density = (
            metrics["bid_density"] if direction == "long" else metrics["ask_density"]
        )
        if density > 10:
            confidence += 0.05
        if density > 20:
            confidence += 0.05

        # Boost for tight spread (conviction)
        if metrics["spread"] < 0.0005:  # 0.05%
            confidence += 0.05

        # Reduce for wide spread (uncertainty)
        if metrics["spread"] > 0.002:  # 0.2%
            confidence -= 0.10

        confidence = max(self.min_confidence, min(0.92, confidence))

        # Calculate RRR
        risk = abs(current_price - stop_loss)
        reward = abs(take_profit - current_price)
        rrr = reward / risk if risk > 0 else 0

        # Create signal
        signal = Signal(
            strategy=self.strategy_type,
            asset=symbol,
            asset_class=AssetClass.PERPETUAL,
            side=side,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            quality=(
                TradeQuality.HIGH_CONVICTION
                if confidence > 0.75
                else TradeQuality.STANDARD
            ),
            timeframe="orderbook",
            market_state=MarketState.TREND,
            notes=(
                f"OB Imbalance: {imbalance:.1%} ({direction}), "
                f"bid_vol={metrics['bid_volume']:.2f}, ask_vol={metrics['ask_volume']:.2f}, "
                f"spread={metrics['spread']:.4%}"
            ),
            indicators={
                "imbalance": imbalance,
                "bid_volume": metrics["bid_volume"],
                "ask_volume": metrics["ask_volume"],
                "bid_orders": metrics["bid_orders"],
                "ask_orders": metrics["ask_orders"],
                "spread": metrics["spread"],
                "atr": atr_value,
            },
            # Validation flags
            volume_confirmation=True,  # Orderbook has volume info
            multi_timeframe_alignment=True,
            support_resistance_valid=True,
            rrr_meets_minimum=rrr >= 1.5,
            liquidation_buffer_safe=True,
            account_risk_ok=True,
            margin_drawdown_ok=True,
            forbidden_conditions_clear=True,
        )

        signals.append(signal)
        self.last_trade_time[symbol] = self._now()

        logger.info(
            f"{symbol}: OB imbalance signal - {direction.upper() if direction else 'UNKNOWN'} @ ${current_price:.2f}, "
            f"imbalance={imbalance:.1%}, conf={confidence:.0%}, RRR={rrr:.2f}"
        )

        return signals

    def get_orderbook_stats(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get current orderbook stats for a symbol (for monitoring)."""
        history = self.imbalance_history.get(symbol, [])
        if not history:
            return None

        return {
            "symbol": symbol,
            "current_imbalance": history[-1] if history else 0.5,
            "avg_imbalance": sum(history) / len(history) if history else 0.5,
            "history_length": len(history),
            "last_analysis": self.last_analysis.get(symbol),
            "last_trade": self.last_trade_time.get(symbol),
        }
