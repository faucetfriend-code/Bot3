# Implementation Prompt: Momentum Scalping Strategy

## Priority: 3 | Difficulty: Medium | Effort: 5-8 days

---

## Task Overview

Implement a **Momentum Scalping** strategy that captures short-term trend moves on 5m/15m timeframes. This is a higher-frequency version of the existing MA Crossover strategy, designed for quick entries and exits during trending conditions.

## Why This Strategy

- Captures momentum before larger trend strategies trigger
- Higher trade frequency than MA Crossover (10-30 trades/day vs 3-8)
- Works in TRENDING_STRONG and TRENDING_MODERATE regimes
- Complements existing strategies by catching faster moves

---

## Technical Requirements

### 1. New Files to Create

```
trading_bot_v2/strategies/momentum_scalping.py
```

### 2. Config Updates

Add to `.env`:
```
ENABLE_MOMENTUM_SCALPING=true
MOMENTUM_EMA_FAST=9
MOMENTUM_EMA_SLOW=21
MOMENTUM_RSI_PERIOD=14
MOMENTUM_RSI_UPPER=65
MOMENTUM_RSI_LOWER=35
MOMENTUM_ATR_STOP_MULT=1.5
MOMENTUM_ATR_TARGET_MULT=2.5
MOMENTUM_MIN_CONFIDENCE=0.55
MOMENTUM_COOLDOWN_MINUTES=10
```

Add to `config.py`:
```python
self.enable_momentum_scalping: bool = os.getenv("ENABLE_MOMENTUM_SCALPING", "false").lower() == "true"
self.momentum_ema_fast: int = int(os.getenv("MOMENTUM_EMA_FAST", "9"))
self.momentum_ema_slow: int = int(os.getenv("MOMENTUM_EMA_SLOW", "21"))
self.momentum_rsi_period: int = int(os.getenv("MOMENTUM_RSI_PERIOD", "14"))
self.momentum_min_confidence: float = float(os.getenv("MOMENTUM_MIN_CONFIDENCE", "0.55"))
```

Add to `Example files/core_logic/config.py` (StrategyType enum):
```python
MOMENTUM_SCALPING = "momentum_scalping"
```

---

## Strategy Logic

### Core Concept: Fast EMA Crossover with Momentum Confirmation

```
LONG Entry:
  - 9 EMA crosses above 21 EMA (on 5m)
  - Price above both EMAs
  - RSI > 50 and < 70 (momentum but not overbought)
  - MACD histogram positive and increasing
  - Volume > 1.2x average

SHORT Entry:
  - 9 EMA crosses below 21 EMA (on 5m)
  - Price below both EMAs
  - RSI < 50 and > 30 (momentum but not oversold)
  - MACD histogram negative and decreasing
  - Volume > 1.2x average
```

### Multi-Timeframe Confirmation

```
Primary: 5m (entry/exit signals)
Confirmation: 15m (trend direction must align)
Filter: 1h (avoid counter-trend trades)
```

### Exit Rules

1. **Stop Loss**: 1.5x ATR from entry
2. **Take Profit**: 2.5x ATR (RRR = 1:1.67)
3. **Trailing Stop**: After 1.5x ATR profit, trail at 1x ATR
4. **Time Stop**: Exit if no movement after 30 minutes
5. **EMA Cross**: Exit on opposite EMA crossover

### Regime Filter

- **Active in**: TRENDING_STRONG, TRENDING_MODERATE
- **Reduced weight in**: RANGING_VOLATILE (momentum can work in volatility)
- **Disabled in**: RANGING_CALM, INDECISIVE

---

## Implementation Skeleton

```python
# strategies/momentum_scalping.py

"""
Momentum Scalping Strategy

Fast EMA crossover scalping for 5m/15m timeframes.
Captures short-term momentum moves in trending conditions.

DESIGN NOTES:
- Faster than MA Crossover (9/21 vs 50/200)
- Higher frequency (10-30 trades/day target)
- Strict regime filter (trending only)
- All sizing delegated to RiskManager
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "Example files", "core_logic"))

from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from loguru import logger

from models import Signal, OrderSide
from config import StrategyType, TradeQuality
from indicators import calculate_ema, calculate_rsi, calculate_atr, calculate_macd


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
        cooldown_minutes: int = 10,
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

        # Track last crossover per symbol for entry timing
        self.last_crossover: Dict[str, Dict] = {}
        # {symbol: {"direction": "bullish/bearish", "time": datetime, "price": float}}

        # Cooldown tracking
        self.last_trade_time: Dict[str, datetime] = {}

        logger.info(
            f"MomentumScalpingStrategy initialized: EMA {ema_fast}/{ema_slow}, "
            f"RSI {rsi_lower}-{rsi_upper}, ATR stop={atr_stop_mult}x target={atr_target_mult}x"
        )

    def _check_cooldown(self, symbol: str) -> bool:
        """Check if cooldown period has passed since last trade."""
        if symbol not in self.last_trade_time:
            return True
        elapsed = datetime.utcnow() - self.last_trade_time[symbol]
        return elapsed > timedelta(minutes=self.cooldown_minutes)

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
        ema_fast = calculate_ema(closes, self.ema_fast)[-1]
        ema_slow = calculate_ema(closes, self.ema_slow)[-1]

        if direction == "bullish":
            # Price should be above both EMAs
            return current_price > ema_fast and current_price > ema_slow
        else:
            # Price should be below both EMAs
            return current_price < ema_fast and current_price < ema_slow

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

        ema_fast_15m = calculate_ema(tf_15m_closes, self.ema_fast)[-1]
        ema_slow_15m = calculate_ema(tf_15m_closes, self.ema_slow)[-1]

        if direction == "bullish":
            return ema_fast_15m > ema_slow_15m
        else:
            return ema_fast_15m < ema_slow_15m

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

        # Need 5m and 15m data
        if "5m" not in multi_tf_data:
            return signals

        df_5m = multi_tf_data["5m"]
        closes_5m = df_5m["close"] if hasattr(df_5m, "close") else df_5m.get("close", [])
        volumes_5m = df_5m["volume"] if hasattr(df_5m, "volume") else df_5m.get("volume", [])
        highs_5m = df_5m["high"] if hasattr(df_5m, "high") else df_5m.get("high", [])
        lows_5m = df_5m["low"] if hasattr(df_5m, "low") else df_5m.get("low", [])

        if len(closes_5m) < self.ema_slow + 10:
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
                "time": datetime.utcnow(),
                "price": current_price,
            }
            logger.debug(f"{symbol}: EMA crossover detected - {crossover}")

        # Check if we have a recent crossover to act on
        if symbol not in self.last_crossover:
            return signals

        crossover_info = self.last_crossover[symbol]
        crossover_age = datetime.utcnow() - crossover_info["time"]

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
        if "15m" in multi_tf_data:
            df_15m = multi_tf_data["15m"]
            closes_15m = df_15m["close"] if hasattr(df_15m, "close") else df_15m.get("close", [])
            closes_15m = list(closes_15m) if hasattr(closes_15m, '__iter__') else closes_15m

            if not self._check_higher_tf_alignment(closes_15m, direction):
                logger.debug(f"{symbol}: 15m trend not aligned with {direction} signal")
                return signals

        # Calculate ATR for stops/targets
        atr = calculate_atr(highs_5m, lows_5m, closes_5m, self.atr_period)
        if len(atr) == 0:
            return signals
        atr_value = atr[-1]

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

        # Create signal
        signal = Signal(
            strategy=self.strategy_type,
            asset=symbol,
            side=side,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            quality=TradeQuality.HIGH if confidence > 0.75 else TradeQuality.STANDARD,
            timeframe="5m",
            market_state="momentum_scalping",
            notes=(
                f"Momentum {direction}: EMA {self.ema_fast}/{self.ema_slow} cross, "
                f"RSI={momentum['rsi_value']:.1f}, Vol={momentum['volume_ratio']:.2f}x, "
                f"MACD hist={momentum['macd_hist']:.4f}"
            ),
        )

        signals.append(signal)

        # Update last trade time
        self.last_trade_time[symbol] = datetime.utcnow()

        logger.info(
            f"{symbol}: Momentum scalp signal - {direction.upper()} @ ${current_price:.2f}, "
            f"SL=${stop_loss:.2f}, TP=${take_profit:.2f}, conf={confidence:.0%}"
        )

        return signals
```

---

## Integration Points

### 1. StrategyManager Registration

In `strategy_manager.py` `__init__`:
```python
if config.enable_momentum_scalping:
    from .strategies.momentum_scalping import MomentumScalpingStrategy
    self.strategies["MomentumScalping"] = MomentumScalpingStrategy(
        ema_fast=config.momentum_ema_fast,
        ema_slow=config.momentum_ema_slow,
        min_confidence=config.momentum_min_confidence,
    )
    logger.info("Momentum Scalping strategy enabled")
```

### 2. Regime Mapping

In `market_regime.py`:
```python
REGIME_STRATEGY_MAP = {
    MarketRegime.TRENDING_STRONG: ["MACrossover", "MomentumScalping"],  # Both trend strategies
    MarketRegime.TRENDING_MODERATE: ["MomentumScalping"],  # Faster strategy for moderate trends
    MarketRegime.RANGING_VOLATILE: ["GridTrading"],  # No momentum in ranging
    MarketRegime.RANGING_CALM: ["MeanReversion"],
    MarketRegime.INDECISIVE: [],
}
```

### 3. Conflict Resolution

When both MACrossover and MomentumScalping generate signals:
- MomentumScalping has higher weight in TRENDING_MODERATE
- MACrossover has higher weight in TRENDING_STRONG
- If both agree on direction → boost confidence
- If they disagree → defer to higher timeframe (MACrossover)

---

## Testing Checklist

- [ ] Verify EMA crossover detection on 5m data
- [ ] Test momentum confirmation (RSI, MACD, volume)
- [ ] Verify 15m alignment check
- [ ] Test cooldown enforcement
- [ ] Verify ATR-based stop/target calculation
- [ ] Test signal generation in trending conditions
- [ ] Verify no signals in ranging conditions
- [ ] Dry-run on testnet for 48+ hours
- [ ] Compare performance vs MA Crossover

---

## Expected Performance

- **Win Rate**: 45-55% (lower than mean reversion)
- **Risk:Reward**: 1:1.67 (stop 1.5x ATR, target 2.5x ATR)
- **Trades/Day**: 10-30 per symbol in trending conditions
- **Monthly Return**: 5-10% (aggressive)
- **Max Drawdown**: 12-18%

---

## Comparison with MA Crossover

| Aspect | Momentum Scalping | MA Crossover |
|--------|-------------------|--------------|
| EMA Periods | 9/21 | 50/200 |
| Timeframe | 5m primary | 4h primary |
| Trades/Day | 10-30 | 3-8 |
| Hold Time | 15min - 2hr | 4hr - 2 days |
| Win Rate | 45-55% | 40-50% |
| RRR | 1:1.67 | 1:2.5+ |
| Regime | Trending moderate+ | Trending strong |
