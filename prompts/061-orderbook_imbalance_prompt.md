# Implementation Prompt: Order Book Imbalance Strategy

## Priority: 4 | Difficulty: Medium-High | Effort: 8-14 days

---

## Task Overview

Implement an **Order Book Imbalance** strategy that analyzes Level 2 market depth to detect buying/selling pressure imbalances. This is a high-frequency overlay strategy that can run in all regimes.

## Why This Strategy

- Detects institutional flow before price moves
- Works in ALL market conditions
- Highest edge potential of the 4 new strategies
- Complements price-based strategies with flow-based signals

---

## Technical Requirements

### 1. New Files to Create

```
trading_bot_v2/strategies/orderbook_imbalance.py
```

### 2. WebSocket Extension

Extend `pacifica_ws_client.py` to subscribe to orderbook depth channel.

### 3. Config Updates

Add to `.env`:
```
ENABLE_ORDERBOOK_IMBALANCE=true
ORDERBOOK_LEVELS=10
ORDERBOOK_AGG_LEVEL=10
ORDERBOOK_IMBALANCE_THRESHOLD_LONG=0.62
ORDERBOOK_IMBALANCE_THRESHOLD_SHORT=0.38
ORDERBOOK_UPDATE_INTERVAL_MS=500
ORDERBOOK_MIN_CONFIDENCE=0.55
ORDERBOOK_COOLDOWN_SECONDS=30
```

Add to `config.py`:
```python
self.enable_orderbook_imbalance: bool = os.getenv("ENABLE_ORDERBOOK_IMBALANCE", "false").lower() == "true"
self.orderbook_levels: int = int(os.getenv("ORDERBOOK_LEVELS", "10"))
self.orderbook_agg_level: int = int(os.getenv("ORDERBOOK_AGG_LEVEL", "10"))
self.orderbook_imbalance_long: float = float(os.getenv("ORDERBOOK_IMBALANCE_THRESHOLD_LONG", "0.62"))
self.orderbook_imbalance_short: float = float(os.getenv("ORDERBOOK_IMBALANCE_THRESHOLD_SHORT", "0.38"))
```

Add to `Example files/core_logic/config.py` (StrategyType enum):
```python
ORDERBOOK_IMBALANCE = "orderbook_imbalance"
```

---

## Pacifica WebSocket Orderbook Subscription

Based on Pacifica docs at `/api-documentation/api/websocket/subscriptions/orderbook`:

### Subscription Message
```json
{
    "method": "subscribe",
    "params": {
        "source": "book",
        "symbol": "BTC",
        "agg_level": 10
    }
}
```

### Response Format (every 100ms)
```json
{
    "channel": "book",
    "symbol": "BTC",
    "t": 1707400000000,
    "li": 12345,
    "bids": [
        {"p": "69500.00", "a": "2.5", "n": 15},
        {"p": "69490.00", "a": "1.8", "n": 8},
        ...
    ],
    "asks": [
        {"p": "69510.00", "a": "1.2", "n": 5},
        {"p": "69520.00", "a": "3.1", "n": 12},
        ...
    ]
}
```

Fields:
- `p`: Price level (highest for bids, lowest for asks in bucket)
- `a`: Total quantity/amount at that level
- `n`: Number of orders in the bucket

---

## Strategy Logic

### Core Concept: Order Book Imbalance

```
imbalance = bid_volume / (bid_volume + ask_volume)

> 0.60 → Strong buy pressure → LONG bias
< 0.40 → Strong sell pressure → SHORT bias
0.40-0.60 → Neutral, no signal
```

### Entry Conditions

**LONG Signal:**
1. Imbalance > 0.62 (top N levels)
2. Bid wall detected (high `n` count on bid side)
3. Price momentum aligning (price rising or stable)
4. Optional: Volume spike on recent candle

**SHORT Signal:**
1. Imbalance < 0.38 (top N levels)
2. Ask wall detected (high `n` count on ask side)
3. Price momentum aligning (price falling or stable)
4. Optional: Volume spike on recent candle

### Advanced Filters

1. **Spoofing Detection**: Large size (`a`) with low order count (`n`) may be spoof
2. **Absorption**: Watch for walls being "eaten" = strong directional signal
3. **Delta**: Track aggressive orders (market orders) vs passive (limit orders)
4. **Spread Analysis**: Widening spread = uncertainty, tightening = conviction

### Exit Rules

1. **Imbalance Flip**: Exit when imbalance crosses neutral (0.5)
2. **Time Stop**: Exit after 30-60 seconds if no movement
3. **ATR Stop**: Small stop (0.5-1x ATR) due to short hold times
4. **Target**: 1-2x ATR or imbalance neutralization

---

## Implementation Skeleton

### Part 1: WebSocket Extension (`pacifica_ws_client.py`)

```python
# Add to PacificaWebSocketClient class

def __init__(self):
    # ... existing init ...

    # Orderbook cache
    self._orderbook_cache: Dict[str, Dict] = {}
    # {symbol: {"bids": [...], "asks": [...], "timestamp": int, "nonce": int}}

def subscribe_orderbook(self, symbol: str, agg_level: int = 10) -> None:
    """
    Subscribe to orderbook depth for a symbol.

    Args:
        symbol: Trading symbol (e.g., "BTC", "SOL")
        agg_level: Aggregation level (1, 10, 100, 1000, 10000)
                   Lower = finer price resolution
    """
    clean_symbol = symbol.upper().replace("-PERP", "")

    payload = {
        "method": "subscribe",
        "params": {
            "source": "book",
            "symbol": clean_symbol,
            "agg_level": agg_level
        }
    }

    asyncio.run_coroutine_threadsafe(
        self._send_message(payload), self._loop
    )

    logger.info(f"Subscribed to orderbook: {clean_symbol} (agg_level={agg_level})")

def _handle_orderbook_message(self, data: Dict) -> None:
    """Process incoming orderbook update."""
    symbol = data.get("symbol", "")

    self._orderbook_cache[symbol] = {
        "bids": data.get("bids", []),
        "asks": data.get("asks", []),
        "timestamp": data.get("t", 0),
        "nonce": data.get("li", 0),
    }

    # Notify callbacks if registered
    if symbol in self._channel_callbacks.get("book", {}):
        self._channel_callbacks["book"][symbol](data)

def get_orderbook(self, symbol: str) -> Optional[Dict]:
    """
    Get cached orderbook for a symbol.

    Returns:
        Dict with 'bids', 'asks', 'timestamp', 'nonce' or None
    """
    clean_symbol = symbol.upper().replace("-PERP", "")
    return self._orderbook_cache.get(clean_symbol)

def get_orderbook_imbalance(
    self, symbol: str, levels: int = 10
) -> Optional[float]:
    """
    Calculate order book imbalance for a symbol.

    Args:
        symbol: Trading symbol
        levels: Number of price levels to consider

    Returns:
        Imbalance ratio (0.0 to 1.0) or None if no data
        > 0.5 = more bids (buy pressure)
        < 0.5 = more asks (sell pressure)
    """
    book = self.get_orderbook(symbol)
    if not book:
        return None

    bids = book.get("bids", [])[:levels]
    asks = book.get("asks", [])[:levels]

    if not bids and not asks:
        return None

    bid_volume = sum(float(level.get("a", 0)) for level in bids)
    ask_volume = sum(float(level.get("a", 0)) for level in asks)

    total_volume = bid_volume + ask_volume
    if total_volume == 0:
        return 0.5

    return bid_volume / total_volume
```

### Part 2: Strategy Implementation

```python
# strategies/orderbook_imbalance.py

"""
Order Book Imbalance Strategy

Analyzes Level 2 market depth to detect buying/selling pressure.
Uses bid/ask volume imbalance to generate directional signals.

DESIGN NOTES:
- Real-time overlay strategy (tick/sub-second)
- Runs in ALL regimes
- Complements price-based strategies with flow analysis
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
from indicators import calculate_atr


class OrderBookImbalanceStrategy:
    """
    Order book imbalance analysis for directional signals.

    Monitors bid/ask depth to detect institutional flow
    and generate short-term directional signals.
    """

    def __init__(
        self,
        levels: int = 10,                     # Price levels to analyze
        imbalance_long_threshold: float = 0.62,    # Imbalance > this = long
        imbalance_short_threshold: float = 0.38,   # Imbalance < this = short
        strong_imbalance_threshold: float = 0.72,  # High conviction threshold
        min_order_density: int = 5,            # Min orders on winning side
        spoof_detection: bool = True,          # Filter potential spoofs
        spoof_size_ratio: float = 5.0,         # Size/orders ratio for spoof
        atr_period: int = 14,
        atr_stop_mult: float = 0.75,           # Tight stop for fast trades
        atr_target_mult: float = 1.5,          # Quick target
        min_confidence: float = 0.55,
        cooldown_seconds: int = 30,
        update_interval_ms: int = 500,         # Min time between analyses
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

        logger.info(
            f"OrderBookImbalanceStrategy initialized: levels={levels}, "
            f"long>{imbalance_long_threshold:.0%}, short<{imbalance_short_threshold:.0%}"
        )

    def _check_cooldown(self, symbol: str) -> bool:
        """Check if cooldown period has passed."""
        if symbol not in self.last_trade_time:
            return True
        elapsed = datetime.utcnow() - self.last_trade_time[symbol]
        return elapsed > timedelta(seconds=self.cooldown_seconds)

    def _check_update_interval(self, symbol: str) -> bool:
        """Check if enough time passed since last analysis."""
        if symbol not in self.last_analysis:
            return True
        elapsed = datetime.utcnow() - self.last_analysis[symbol]
        return elapsed > timedelta(milliseconds=self.update_interval_ms)

    def _detect_spoof(
        self, levels: List[Dict], side: str
    ) -> bool:
        """
        Detect potential spoofing on order book.

        Spoofs often have: large size, few orders, near top of book
        """
        if not self.spoof_detection or len(levels) < 3:
            return False

        for i, level in enumerate(levels[:3]):  # Check top 3 levels
            size = float(level.get("a", 0))
            order_count = int(level.get("n", 1))

            if order_count == 0:
                continue

            size_per_order = size / order_count

            # Large size concentrated in few orders at top = potential spoof
            if (size_per_order > self.spoof_size_ratio and
                order_count < 3 and i == 0):
                logger.debug(
                    f"Potential spoof detected on {side} side: "
                    f"size={size:.2f}, orders={order_count}"
                )
                return True

        return False

    def _calculate_weighted_imbalance(
        self, bids: List[Dict], asks: List[Dict]
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
        }

        if not bids or not asks:
            return result

        # Calculate volumes
        bid_volume = sum(float(level.get("a", 0)) for level in bids[:self.levels])
        ask_volume = sum(float(level.get("a", 0)) for level in asks[:self.levels])

        # Calculate order counts
        bid_orders = sum(int(level.get("n", 0)) for level in bids[:self.levels])
        ask_orders = sum(int(level.get("n", 0)) for level in asks[:self.levels])

        # Calculate spread
        best_bid = float(bids[0].get("p", 0)) if bids else 0
        best_ask = float(asks[0].get("p", 0)) if asks else 0
        spread = (best_ask - best_bid) / best_bid if best_bid > 0 else 0

        # Imbalance calculation
        total_volume = bid_volume + ask_volume
        imbalance = bid_volume / total_volume if total_volume > 0 else 0.5

        result.update({
            "imbalance": imbalance,
            "bid_volume": bid_volume,
            "ask_volume": ask_volume,
            "bid_orders": bid_orders,
            "ask_orders": ask_orders,
            "bid_density": bid_orders / self.levels if self.levels > 0 else 0,
            "ask_density": ask_orders / self.levels if self.levels > 0 else 0,
            "spread": spread,
        })

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
            self.imbalance_history[symbol] = self.imbalance_history[symbol][-max_history:]

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
        orderbook: Dict[str, Any],
        multi_tf_data: Dict[str, Any],
        current_price: float,
        **kwargs
    ) -> List[Signal]:
        """
        Generate order book imbalance signals.

        Args:
            symbol: Trading symbol
            orderbook: Dict with 'bids' and 'asks' lists
            multi_tf_data: OHLCV data for ATR calculation
            current_price: Current market price
        """
        signals = []

        # Check timing constraints
        if not self._check_cooldown(symbol):
            return signals

        if not self._check_update_interval(symbol):
            return signals

        self.last_analysis[symbol] = datetime.utcnow()

        # Extract orderbook data
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        if len(bids) < 3 or len(asks) < 3:
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

        # Check imbalance trend
        if not self._check_imbalance_trend(symbol, direction):
            logger.debug(f"{symbol}: Imbalance trend not confirming {direction}")
            return signals

        # Calculate ATR for stops
        atr_value = 0
        if "5m" in multi_tf_data or "1m" in multi_tf_data:
            tf = "1m" if "1m" in multi_tf_data else "5m"
            df = multi_tf_data[tf]
            highs = list(df.get("high", []))
            lows = list(df.get("low", []))
            closes = list(df.get("close", []))

            if len(closes) >= self.atr_period:
                atr = calculate_atr(highs, lows, closes, self.atr_period)
                atr_value = atr[-1] if len(atr) > 0 else 0

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
        density = metrics["bid_density"] if direction == "long" else metrics["ask_density"]
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
            timeframe="orderbook",
            market_state="orderbook_imbalance",
            notes=(
                f"OB Imbalance: {imbalance:.1%} ({direction}), "
                f"bid_vol={metrics['bid_volume']:.2f}, ask_vol={metrics['ask_volume']:.2f}, "
                f"spread={metrics['spread']:.4%}"
            ),
        )

        signals.append(signal)
        self.last_trade_time[symbol] = datetime.utcnow()

        logger.info(
            f"{symbol}: OB imbalance signal - {direction.upper()} @ ${current_price:.2f}, "
            f"imbalance={imbalance:.1%}, conf={confidence:.0%}"
        )

        return signals
```

---

## Integration Points

### 1. WebSocket Subscription on Startup

In `api_server.py` startup:
```python
@app.on_event("startup")
async def startup_event():
    # ... existing code ...

    # Subscribe to orderbook for trading symbols
    if config.enable_orderbook_imbalance:
        ws_client = get_ws_client()
        for symbol in TRADING_SYMBOLS:
            ws_client.subscribe_orderbook(symbol, agg_level=config.orderbook_agg_level)
```

### 2. StrategyManager Integration

The OrderBook strategy needs special handling because it requires orderbook data:

```python
# In StrategyManager.generate_signals_for_market():

if "OrderBookImbalance" in self.strategies:
    orderbook = ws_client.get_orderbook(symbol)
    if orderbook:
        ob_signals = self.strategies["OrderBookImbalance"].generate_signals(
            symbol=symbol,
            orderbook=orderbook,
            multi_tf_data=multi_tf_data,
            current_price=current_price,
        )
        all_signals.extend(ob_signals)
```

### 3. Regime Mapping

In `market_regime.py`, add to ALL regimes:
```python
# OrderBookImbalance runs as overlay in ALL regimes
REGIME_STRATEGY_MAP = {
    MarketRegime.TRENDING_STRONG: ["MACrossover", "OrderBookImbalance"],
    MarketRegime.TRENDING_MODERATE: ["MomentumScalping", "OrderBookImbalance"],
    MarketRegime.RANGING_VOLATILE: ["GridTrading", "OrderBookImbalance"],
    MarketRegime.RANGING_CALM: ["MeanReversion", "OrderBookImbalance"],
    MarketRegime.INDECISIVE: ["OrderBookImbalance"],  # Only flow-based
}
```

---

## Testing Checklist

- [ ] Verify WebSocket orderbook subscription works
- [ ] Test orderbook message parsing
- [ ] Verify imbalance calculation accuracy
- [ ] Test spoof detection logic
- [ ] Test cooldown enforcement
- [ ] Verify signal generation on imbalance threshold
- [ ] Test with different agg_levels (1, 10, 100)
- [ ] Monitor update frequency (100ms from Pacifica)
- [ ] Dry-run on testnet for 24+ hours
- [ ] Compare signals with actual price moves

---

## Expected Performance

- **Win Rate**: 55-65%
- **Risk:Reward**: 1:2 (0.75x ATR stop, 1.5x ATR target)
- **Trades/Day**: 20-50 (high frequency)
- **Hold Time**: 30 seconds - 5 minutes
- **Monthly Return**: 8-15% (aggressive)
- **Max Drawdown**: 10-15%

---

## Risk Considerations

1. **Spoofing**: Large orders can be fake, removed before fill
2. **Speed**: Requires low-latency execution (sub-second)
3. **Fees**: High frequency = high fee drag
4. **Slippage**: Fast moves = potential slippage
5. **Data Quality**: Relies on accurate L2 feed

## Monitoring Recommendations

Log every 5 minutes:
- Average imbalance per symbol
- Signal count and direction distribution
- Win/loss ratio
- Average hold time
- Slippage impact
