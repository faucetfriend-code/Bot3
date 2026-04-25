"""
Momentum Scalping Strategy

Fast EMA crossover scalping for 5m/15m timeframes.
Captures short-term momentum moves in trending conditions.

DESIGN NOTES:
- Faster than MA Crossover (9/21 vs 50/200)
- Higher frequency (10-30 trades/day target)
- Strict regime filter (trending only)
- All sizing delegated to RiskManager

RISK NOTES:
- Higher frequency = more exposure to fees
- Requires tight stops (1.5x ATR)
- Works best with good execution speed
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from loguru import logger

from ..models import Signal, OrderSide
from ..config import StrategyType, TradeQuality, MarketState, AssetClass


def calculate_ema(prices: List[float], period: int) -> List[float]:
    """Calculate Exponential Moving Average."""
    if len(prices) < period:
        return []

    ema = []
    multiplier = 2 / (period + 1)

    # Start with SMA for first value
    sma = sum(prices[:period]) / period
    ema.append(sma)

    # Calculate EMA for remaining values
    for i in range(period, len(prices)):
        value = (prices[i] * multiplier) + (ema[-1] * (1 - multiplier))
        ema.append(value)

    return ema


def calculate_rsi(prices: List[float], period: int = 14) -> List[float]:
    """Calculate Relative Strength Index."""
    if len(prices) < period + 1:
        return []

    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]

    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    rsi_values = []

    # First RSI calculation using SMA
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        rsi_values.append(100)
    else:
        rs = avg_gain / avg_loss
        rsi_values.append(100 - (100 / (1 + rs)))

    # Subsequent RSI using Wilder's smoothing
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            rsi_values.append(100)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - (100 / (1 + rs)))

    return rsi_values


def calculate_macd(
    prices: List[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9
) -> tuple:
    """Calculate MACD line, signal line, and histogram."""
    if len(prices) < slow_period + signal_period:
        return [], [], []

    ema_fast = calculate_ema(prices, fast_period)
    ema_slow = calculate_ema(prices, slow_period)

    # Align lengths
    offset = len(ema_fast) - len(ema_slow)
    ema_fast = ema_fast[offset:]

    # MACD line
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]

    # Signal line
    signal_line = calculate_ema(macd_line, signal_period)

    # Align MACD to signal
    offset = len(macd_line) - len(signal_line)
    macd_line = macd_line[offset:]

    # Histogram
    histogram = [m - s for m, s in zip(macd_line, signal_line)]

    return macd_line, signal_line, histogram


def calculate_atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14
) -> List[float]:
    """Calculate Average True Range."""
    if len(highs) < period + 1:
        return []

    true_ranges = []
    for i in range(1, len(highs)):
        high_low = highs[i] - lows[i]
        high_close = abs(highs[i] - closes[i-1])
        low_close = abs(lows[i] - closes[i-1])
        true_ranges.append(max(high_low, high_close, low_close))

    atr = []
    # First ATR is simple average
    atr.append(sum(true_ranges[:period]) / period)

    # Subsequent ATR using Wilder's smoothing
    for i in range(period, len(true_ranges)):
        atr.append((atr[-1] * (period - 1) + true_ranges[i]) / period)

    return atr


class MomentumScalpingStrategy:
    """
    Fast momentum scalping using EMA crossovers.

    Targets quick 1-3% moves on 5m timeframe with 15m confirmation.
    Works best in trending markets with clear directional momentum.
    """

    def __init__(
        self,
        ema_fast: int = 9,
        ema_slow: int = 21,
        rsi_period: int = 14,
        rsi_upper: float = 65.0,          # RSI ceiling (avoid overbought entries)
        rsi_lower: float = 35.0,          # RSI floor (avoid oversold entries)
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        atr_period: int = 14,
        atr_stop_mult: float = 1.5,       # SL = 1.5x ATR
        atr_target_mult: float = 2.5,     # TP = 2.5x ATR
        volume_threshold: float = 1.2,    # Min 1.2x average volume
        min_confidence: float = 0.55,
        cooldown_minutes: int = 5,  # Match strategy_manager default
        min_atr_pct: float = 0.0,   # Min ATR as % of price (0 = disabled); filters low-vol candles
    ):
        self.strategy_type = StrategyType.MOMENTUM_SCALPING
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.rsi_upper = rsi_upper
        self.rsi_lower = rsi_lower
        self.macd_params = (macd_fast, macd_slow, macd_signal)
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.volume_threshold = volume_threshold
        self.min_confidence = min_confidence
        self.cooldown_minutes = cooldown_minutes
        self.min_atr_pct = min_atr_pct

        # Track last crossover per symbol for entry timing
        self.last_crossover: Dict[str, Dict] = {}
        # {symbol: {"direction": "bullish/bearish", "time": datetime, "price": float}}

        # Cooldown tracking
        self.last_trade_time: Dict[str, datetime] = {}

        # Simulated time injected by backtest engine (None = use wall-clock)
        self._sim_time: Optional[datetime] = None

        logger.info(
            f"MomentumScalpingStrategy initialized: EMA {ema_fast}/{ema_slow}, "
            f"RSI {rsi_lower}-{rsi_upper}, ATR stop={atr_stop_mult}x target={atr_target_mult}x"
            + (f", min_atr={min_atr_pct:.3%}" if min_atr_pct > 0 else "")
        )

    def _now(self) -> datetime:
        """Return current time — simulated candle time in backtesting, wall-clock in live."""
        return self._sim_time if self._sim_time is not None else datetime.utcnow()

    def _check_cooldown(self, symbol: str) -> bool:
        """Check if cooldown period has passed since last trade."""
        if symbol not in self.last_trade_time:
            return True
        elapsed = self._now() - self.last_trade_time[symbol]
        if elapsed <= timedelta(minutes=self.cooldown_minutes):
            remaining = self.cooldown_minutes - (elapsed.total_seconds() / 60)
            logger.debug(f"{symbol}: Momentum scalping cooldown {remaining:.1f}min remaining")
            return False
        return True

    def _detect_ema_crossover(
        self, closes: List[float], symbol: str
    ) -> Optional[str]:
        """
        Detect EMA crossover and return direction.

        Returns: "bullish", "bearish", or None
        """
        if len(closes) < self.ema_slow + 5:
            return None

        ema_fast = calculate_ema(closes, self.ema_fast)
        ema_slow = calculate_ema(closes, self.ema_slow)

        if len(ema_fast) < 2 or len(ema_slow) < 2:
            return None

        # Current and previous values
        fast_now, fast_prev = ema_fast[-1], ema_fast[-2]
        slow_now, slow_prev = ema_slow[-1], ema_slow[-2]

        # Bullish crossover: fast crosses above slow
        if fast_prev <= slow_prev and fast_now > slow_now:
            return "bullish"

        # Bearish crossover: fast crosses below slow
        if fast_prev >= slow_prev and fast_now < slow_now:
            return "bearish"

        return None

    def _check_ema_position(
        self, current_price: float, closes: List[float], direction: str
    ) -> bool:
        """Check if price is positioned correctly relative to EMAs."""
        ema_fast = calculate_ema(closes, self.ema_fast)
        ema_slow = calculate_ema(closes, self.ema_slow)

        if not ema_fast or not ema_slow:
            return False

        ema_fast_val = ema_fast[-1]
        ema_slow_val = ema_slow[-1]

        if direction == "bullish":
            # Price should be above both EMAs
            return current_price > ema_fast_val and current_price > ema_slow_val
        else:
            # Price should be below both EMAs
            return current_price < ema_fast_val and current_price < ema_slow_val

    def _check_momentum_confirmation(
        self, closes: List[float], volumes: List[float], direction: str
    ) -> Dict[str, Any]:
        """
        Check momentum indicators for confirmation.

        Returns dict with confirmation status and details.
        """
        result = {
            "confirmed": False,
            "rsi_ok": False,
            "macd_ok": False,
            "volume_ok": False,
            "rsi_value": 0,
            "macd_hist": 0,
            "volume_ratio": 0,
        }

        # RSI check
        rsi = calculate_rsi(closes, self.rsi_period)
        if len(rsi) < 2:
            return result

        rsi_now = rsi[-1]
        result["rsi_value"] = rsi_now

        if direction == "bullish":
            result["rsi_ok"] = 50 < rsi_now < self.rsi_upper
        else:
            result["rsi_ok"] = self.rsi_lower < rsi_now < 50

        # MACD check
        macd_line, signal_line, histogram = calculate_macd(
            closes, *self.macd_params
        )
        if len(histogram) < 3:
            return result

        macd_hist_now = histogram[-1]
        macd_hist_prev = histogram[-2]
        result["macd_hist"] = macd_hist_now

        if direction == "bullish":
            # Histogram positive and increasing
            result["macd_ok"] = macd_hist_now > 0 and macd_hist_now > macd_hist_prev
        else:
            # Histogram negative and decreasing
            result["macd_ok"] = macd_hist_now < 0 and macd_hist_now < macd_hist_prev

        # Volume check
        if len(volumes) >= 20:
            avg_volume = sum(volumes[-20:]) / 20
            current_volume = volumes[-1]
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0
            result["volume_ratio"] = volume_ratio
            result["volume_ok"] = volume_ratio >= self.volume_threshold

        # Overall confirmation
        result["confirmed"] = (
            result["rsi_ok"] and result["macd_ok"] and result["volume_ok"]
        )

        return result

    def _check_higher_tf_alignment(
        self, tf_15m_closes: List[float], direction: str
    ) -> bool:
        """
        Check if 15m timeframe trend aligns with signal direction.
        """
        if len(tf_15m_closes) < self.ema_slow + 5:
            return True  # Allow if insufficient data

        ema_fast_15m = calculate_ema(tf_15m_closes, self.ema_fast)
        ema_slow_15m = calculate_ema(tf_15m_closes, self.ema_slow)

        if not ema_fast_15m or not ema_slow_15m:
            return True

        if direction == "bullish":
            return ema_fast_15m[-1] > ema_slow_15m[-1]
        else:
            return ema_fast_15m[-1] < ema_slow_15m[-1]

    def _check_4h_trend_alignment(
        self, closes_4h: List[float], direction: str
    ) -> bool:
        """
        4h HTF trend filter — highest-priority trend gate.

        Only take BUY signals when 4h EMA-fast > EMA-slow (uptrend).
        Only take SELL signals when 4h EMA-fast < EMA-slow (downtrend).
        Returns True (allow) when insufficient 4h data exists.
        """
        min_len = self.ema_slow + 5
        if len(closes_4h) < min_len:
            return True

        ema_fast_4h = calculate_ema(closes_4h, self.ema_fast)
        ema_slow_4h = calculate_ema(closes_4h, self.ema_slow)

        if not ema_fast_4h or not ema_slow_4h:
            return True

        bullish_4h = ema_fast_4h[-1] > ema_slow_4h[-1]
        return bullish_4h if direction == "bullish" else not bullish_4h

    def generate_signals(
        self,
        symbol: str,
        multi_tf_data: Dict[str, Any],
        current_price: float,
        **kwargs
    ) -> List[Signal]:
        """
        Generate momentum scalping signals.

        Uses 5m for entry timing, 15m for trend confirmation.
        """
        signals = []

        # Check cooldown
        if not self._check_cooldown(symbol):
            return signals

        # Need 5m data - check execution_tf_data first, then multi_tf_data
        execution_tf_data = kwargs.get("execution_tf_data", {})

        df_5m = None
        if "5m" in execution_tf_data:
            df_5m = execution_tf_data["5m"]
        elif "5m" in multi_tf_data:
            df_5m = multi_tf_data["5m"]

        if df_5m is None:
            logger.debug(f"{symbol}: No 5m data available for momentum scalping")
            return signals

        # Extract OHLCV data
        closes_5m = df_5m.get("close", [])
        volumes_5m = df_5m.get("volume", [])
        highs_5m = df_5m.get("high", [])
        lows_5m = df_5m.get("low", [])

        if len(closes_5m) < self.ema_slow + 10:
            logger.debug(f"{symbol}: Insufficient 5m data for momentum scalping ({len(closes_5m)} candles)")
            return signals

        # Convert to lists if needed
        closes_5m = list(closes_5m) if hasattr(closes_5m, '__iter__') else closes_5m
        volumes_5m = list(volumes_5m) if hasattr(volumes_5m, '__iter__') else volumes_5m
        highs_5m = list(highs_5m) if hasattr(highs_5m, '__iter__') else highs_5m
        lows_5m = list(lows_5m) if hasattr(lows_5m, '__iter__') else lows_5m

        # Detect crossover
        crossover = self._detect_ema_crossover(closes_5m, symbol)

        if crossover:
            # Update crossover tracking
            self.last_crossover[symbol] = {
                "direction": crossover,
                "time": self._now(),
                "price": current_price,
            }
            logger.debug(f"{symbol}: EMA {self.ema_fast}/{self.ema_slow} crossover detected - {crossover}")

        # Check if we have a recent crossover to act on
        if symbol not in self.last_crossover:
            return signals

        crossover_info = self.last_crossover[symbol]
        crossover_age = self._now() - crossover_info["time"]

        # Only act on crossovers within last 5 candles (25 minutes on 5m)
        if crossover_age > timedelta(minutes=25):
            return signals

        direction = crossover_info["direction"]

        # Check price position relative to EMAs
        if not self._check_ema_position(current_price, closes_5m, direction):
            return signals

        # Check momentum confirmation
        momentum = self._check_momentum_confirmation(closes_5m, volumes_5m, direction)
        if not momentum["confirmed"]:
            logger.debug(
                f"{symbol}: Momentum not confirmed - RSI:{momentum['rsi_ok']}, "
                f"MACD:{momentum['macd_ok']}, Vol:{momentum['volume_ok']}"
            )
            return signals

        # Check 15m alignment
        df_15m = multi_tf_data.get("15m", {})
        if df_15m:
            closes_15m = df_15m.get("close", [])
            closes_15m = list(closes_15m) if hasattr(closes_15m, '__iter__') else closes_15m

            if closes_15m and not self._check_higher_tf_alignment(closes_15m, direction):
                logger.debug(f"{symbol}: 15m trend not aligned with {direction} signal")
                return signals

        # 4h HTF trend gate (highest-priority filter — must be aligned with trade direction)
        df_4h = multi_tf_data.get("4h", {})
        if df_4h:
            closes_4h = df_4h.get("close", [])
            closes_4h = list(closes_4h) if hasattr(closes_4h, '__iter__') else closes_4h
            if closes_4h and not self._check_4h_trend_alignment(closes_4h, direction):
                logger.debug(f"{symbol}: 4h trend not aligned with {direction} signal — blocked")
                return signals

        # Calculate ATR for stops/targets
        atr = calculate_atr(highs_5m, lows_5m, closes_5m, self.atr_period)
        if len(atr) == 0:
            return signals
        atr_value = atr[-1]

        # Minimum ATR gate: skip if current volatility is too low for costs to be worthwhile.
        # Slippage + cost-model drag ≈ 0.52% × price per round-trip; require ATR to be at least
        # min_atr_pct of price so the stop/target have room to breathe.
        if self.min_atr_pct > 0 and current_price > 0:
            atr_pct = atr_value / current_price
            if atr_pct < self.min_atr_pct:
                logger.debug(
                    f"{symbol}: ATR={atr_pct:.4%} below min_atr_pct={self.min_atr_pct:.4%} — skipping"
                )
                return signals

        # Determine side
        side = OrderSide.BUY if direction == "bullish" else OrderSide.SELL

        # Calculate levels
        if side == OrderSide.BUY:
            stop_loss = current_price - (atr_value * self.atr_stop_mult)
            take_profit = current_price + (atr_value * self.atr_target_mult)
        else:
            stop_loss = current_price + (atr_value * self.atr_stop_mult)
            take_profit = current_price - (atr_value * self.atr_target_mult)

        # Calculate confidence
        confidence = self.min_confidence

        # Boost for strong RSI
        if direction == "bullish" and momentum["rsi_value"] > 55:
            confidence += 0.05
        elif direction == "bearish" and momentum["rsi_value"] < 45:
            confidence += 0.05

        # Boost for strong volume
        if momentum["volume_ratio"] > 1.5:
            confidence += 0.05
        if momentum["volume_ratio"] > 2.0:
            confidence += 0.05

        # Boost for strong MACD
        if abs(momentum["macd_hist"]) > atr_value * 0.1:
            confidence += 0.05

        confidence = min(0.90, confidence)

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
            quality=TradeQuality.HIGH_CONVICTION if confidence > 0.75 else TradeQuality.STANDARD,
            timeframe="5m",
            market_state=MarketState.TREND,
            notes=(
                f"Momentum {direction}: EMA {self.ema_fast}/{self.ema_slow} cross, "
                f"RSI={momentum['rsi_value']:.1f}, Vol={momentum['volume_ratio']:.2f}x, "
                f"MACD hist={momentum['macd_hist']:.4f}"
            ),
            indicators={
                "ema_fast": self.ema_fast,
                "ema_slow": self.ema_slow,
                "rsi": momentum["rsi_value"],
                "macd_hist": momentum["macd_hist"],
                "volume_ratio": momentum["volume_ratio"],
                "atr": atr_value,
                "direction": direction,
            },
            # Validation flags
            volume_confirmation=momentum["volume_ok"],
            multi_timeframe_alignment=True,
            support_resistance_valid=True,
            rrr_meets_minimum=rrr >= 1.5,
            liquidation_buffer_safe=True,
            account_risk_ok=True,
            margin_drawdown_ok=True,
            forbidden_conditions_clear=True,
        )

        signals.append(signal)

        # Update last trade time
        self.last_trade_time[symbol] = self._now()

        # Clear crossover after generating signal
        del self.last_crossover[symbol]

        logger.info(
            f"{symbol}: Momentum scalp signal - {direction.upper()} @ ${current_price:.2f}, "
            f"SL=${stop_loss:.2f}, TP=${take_profit:.2f}, conf={confidence:.0%}, RRR={rrr:.2f}"
        )

        return signals
