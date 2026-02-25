# Trading Strategies

<!--
RAG Metadata:
- Category: Strategies
- Tags: mean-reversion, ma-crossover, grid-trading, liquidation, vwap, funding-arb, momentum, orderbook
- Related: 01-core-trading-logic, 03-market-analysis, 07-risk-management
-->

## Overview

The trading bot implements 8 strategies organized into two categories:

**Core Strategies (Regime-Based)**:
1. Mean Reversion - RSI-based range trading
2. MA Crossover - Trend following
3. Grid Trading - Range-bound scalping
4. Liquidation Capture - Event-driven

**Advanced Strategies (Overlay)**:
5. VWAP Scalping - Volume analysis
6. Funding Arbitrage - Delta-neutral
7. Momentum Scalping - Fast EMA
8. Order Book Imbalance - Level 2 analysis

---

## Market Regime Mapping

| Strategy | RANGING_CALM | RANGING_VOLATILE | TRENDING_MODERATE | TRENDING_STRONG |
|----------|:------------:|:----------------:|:-----------------:|:---------------:|
| Mean Reversion | ✅ | ❌ | ❌ | ❌ |
| MA Crossover | ❌ | ❌ | ✅ | ✅ |
| Grid Trading | ❌ | ✅ | ❌ | ❌ |
| Liquidation Capture | ✅ | ✅ | ✅ | ✅ |
| VWAP Scalping | ✅ | ✅ | ✅ | ✅ |
| Funding Arbitrage | ✅ | ✅ | ✅ | ✅ |
| Momentum Scalping | ❌ | ❌ | ✅ | ✅ |
| Order Book Imbalance | ✅ | ✅ | ✅ | ✅ |

---

## 1. Mean Reversion Strategy

**Location**: `trading_bot_v2/strategies/mean_reversion.py`

### Purpose
Captures price reversals in calm, ranging markets using RSI extremes.

### Parameters (Jan 2026 - Loosened)

```python
RSI_OVERSOLD = 35      # Was 30 - more signals
RSI_OVERBOUGHT = 65    # Was 70 - more signals
RSI_PERIOD = 14
MIN_CONFIDENCE = 0.45  # Was 0.6 - more signals
```

### Signal Generation Logic

```python
def generate_signal(self, symbol: str, market_data: Dict, regime: MarketRegime) -> Optional[Signal]:
    """
    Generate mean reversion signal.
    
    BUY Signal:
    - RSI < 35 (oversold)
    - RANGING_CALM regime
    - Price near lower Bollinger Band
    
    SELL Signal:
    - RSI > 65 (overbought)
    - RANGING_CALM regime
    - Price near upper Bollinger Band
    """
```

### Regime Requirements
- **Required**: `RANGING_CALM`
- **Rejected**: All others

---

## 2. MA Crossover Strategy

**Location**: `trading_bot_v2/strategies/ma_crossover.py`

### Purpose
Captures trend continuation with 50/200 MA crossovers and pullback entries.

### Parameters

```python
FAST_MA_PERIOD = 50
SLOW_MA_PERIOD = 200
MIN_CONFIDENCE = 0.65
PULLBACK_THRESHOLD = 0.02  # 2% pullback from high
```

### Signal Generation Logic

```python
def generate_signal(self, symbol: str, market_data: Dict, regime: MarketRegime) -> Optional[Signal]:
    """
    Generate MA crossover signal.
    
    BUY Signal:
    - Fast MA > Slow MA (uptrend)
    - Price pulls back to Fast MA
    - TRENDING_STRONG or TRENDING_MODERATE regime
    
    SELL Signal:
    - Fast MA < Slow MA (downtrend)
    - Price pulls back to Fast MA
    - TRENDING_STRONG or TRENDING_MODERATE regime
    """
```

### Regime Requirements
- **Required**: `TRENDING_STRONG` or `TRENDING_MODERATE`
- **Rejected**: `RANGING_*`, `INDECISIVE`

---

## 3. Grid Trading Strategy

**Location**: `trading_bot_v2/strategies/grid_trading.py`

### Purpose
Creates a grid of buy/sell orders in volatile ranging markets.

### Parameters (Jan 2026 - Tighter Grids)

```python
GRID_LEVELS = 8              # Was 10 - fewer levels
GRID_SPACING_ATR = 0.4       # Was 0.5 - tighter spacing
MAX_POSITIONS_PER_SYMBOL = 10
ADX_THRESHOLD = 20.0         # Was 25.0 - more signals
EMERGENCY_STOP_PCT = 0.05    # 5% portfolio stop
MIN_CONFIDENCE = 0.45        # Was 0.6 - more signals
```

### Grid Structure

```
Price Level    Action
────────────────────────
+3.2% ATR     SELL (Level 4)
+2.4% ATR     SELL (Level 3)
+1.6% ATR     SELL (Level 2)
+0.8% ATR     SELL (Level 1)
────────────────────────
   Current Price
────────────────────────
-0.8% ATR     BUY (Level 1)
-1.6% ATR     BUY (Level 2)
-2.4% ATR     BUY (Level 3)
-3.2% ATR     BUY (Level 4)
```

### Regime Requirements
- **Required**: `RANGING_VOLATILE`
- **Emergency Stop**: ADX > 25.0 triggers partial unwind

---

## 4. Liquidation Capture Strategy

**Location**: `trading_bot_v2/strategies/liquidation_capture.py`

### Purpose
Captures price movements from liquidation cascades.

### Parameters (Jan 2026 - Loosened)

```python
PRICE_MOVE_THRESHOLD = 0.025   # 2.5% (was 3%)
VOLUME_SPIKE_MULTIPLIER = 2.5  # 2.5x average (was 3x)
COOLDOWN_MINUTES = 10
MIN_CONFIDENCE = 0.55
```

### Signal Generation Logic

```python
def generate_signal(self, symbol: str, market_data: Dict, regime: MarketRegime) -> Optional[Signal]:
    """
    Generate liquidation capture signal.
    
    Signal triggers when:
    - Price moves > 2.5% in short time
    - Volume spikes > 2.5x average
    - Indicates liquidation cascade
    
    Direction: Counter-trend (fade the liquidation)
    """
```

### Regime Requirements
- **Allowed**: All regimes (event-driven)

---

## 5. VWAP Scalping Strategy

**Location**: `trading_bot_v2/strategies/vwap_scalping.py`

### Purpose
Scalps mean reversion to Volume-Weighted Average Price.

### Parameters

```python
VWAP_DEVIATION_THRESHOLD = 0.018  # 1.8% deviation
COOLDOWN_MINUTES = 8
MIN_CONFIDENCE = 0.50
```

### Signal Generation Logic

```python
def generate_signal(self, symbol: str, market_data: Dict, regime: MarketRegime) -> Optional[Signal]:
    """
    Generate VWAP scalping signal.
    
    BUY Signal:
    - Price > 1.8% below VWAP
    - MACD confirms bullish momentum
    
    SELL Signal:
    - Price > 1.8% above VWAP
    - MACD confirms bearish momentum
    """
```

### Regime Requirements
- **Allowed**: All regimes (overlay strategy)

---

## 6. Funding Arbitrage Strategy

**Location**: `trading_bot_v2/strategies/funding_arb.py`

### Purpose
Captures funding rate differentials with delta-neutral positions.

### Parameters

```python
MIN_FUNDING_RATE = 0.0001  # 0.01% minimum
MAX_ALLOCATION = 0.20      # 20% max portfolio
COOLDOWN_HOURS = 1
MIN_CONFIDENCE = 0.60
```

### Signal Generation Logic

```python
def generate_signal(self, symbol: str, market_data: Dict, regime: MarketRegime) -> Optional[Signal]:
    """
    Generate funding arbitrage signal.
    
    Long Perp + Short Spot (when funding positive):
    - Funding rate > 0.01%
    - Delta-neutral exposure
    - Collect hourly funding payments
    
    Short Perp + Long Spot (when funding negative):
    - Funding rate < -0.01%
    - Delta-neutral exposure
    """
```

### Regime Requirements
- **Allowed**: All regimes (passive strategy)
- **Note**: Requires spot market access

---

## 7. Momentum Scalping Strategy

**Location**: `trading_bot_v2/strategies/momentum_scalping.py`

### Purpose
Fast scalping with EMA crossovers on 5-minute timeframe.

### Parameters

```python
FAST_EMA = 9
SLOW_EMA = 21
RSI_CONFIRM_MIN = 40
RSI_CONFIRM_MAX = 60
COOLDOWN_MINUTES = 5
MIN_CONFIDENCE = 0.55
```

### Signal Generation Logic

```python
def generate_signal(self, symbol: str, market_data: Dict, regime: MarketRegime) -> Optional[Signal]:
    """
    Generate momentum scalping signal.
    
    BUY Signal:
    - EMA 9 crosses above EMA 21
    - RSI between 40-60 (not extreme)
    - MACD confirms upward momentum
    
    SELL Signal:
    - EMA 9 crosses below EMA 21
    - RSI between 40-60 (not extreme)
    - MACD confirms downward momentum
    """
```

### Regime Requirements
- **Required**: `TRENDING_STRONG` or `TRENDING_MODERATE`

---

## 8. Order Book Imbalance Strategy

**Location**: `trading_bot_v2/strategies/orderbook_imbalance.py`

### Purpose
Detects order flow imbalances from Level 2 orderbook data.

### Parameters

```python
ORDERBOOK_DEPTH = 10        # 10 price levels
IMBALANCE_THRESHOLD = 0.3   # 30% bid/ask ratio
COOLDOWN_SECONDS = 30
MIN_CONFIDENCE = 0.55
```

### Signal Generation Logic

```python
def generate_signal(self, symbol: str, orderbook: Dict, regime: MarketRegime) -> Optional[Signal]:
    """
    Generate order book imbalance signal.
    
    BUY Signal:
    - Bid volume > 1.3x Ask volume
    - Strong buying pressure in top 10 levels
    
    SELL Signal:
    - Ask volume > 1.3x Bid volume
    - Strong selling pressure in top 10 levels
    """
```

### Regime Requirements
- **Allowed**: All regimes (overlay strategy)
- **Requires**: WebSocket connection for real-time orderbook

---

## Strategy Files Summary

| File | Strategy | Lines |
|------|----------|-------|
| `mean_reversion.py` | Mean Reversion | ~200 |
| `ma_crossover.py` | MA Crossover | ~250 |
| `grid_trading.py` | Grid Trading | ~400 |
| `liquidation_capture.py` | Liquidation Capture | ~200 |
| `vwap_scalping.py` | VWAP Scalping | ~250 |
| `funding_arb.py` | Funding Arbitrage | ~350 |
| `momentum_scalping.py` | Momentum Scalping | ~200 |
| `orderbook_imbalance.py` | Order Book Imbalance | ~250 |

---

## Related Reports

- [01-core-trading-logic.md](./01-core-trading-logic.md) - Strategy orchestration
- [03-market-analysis.md](./03-market-analysis.md) - Regime detection
- [04-exchange-integration.md](./04-exchange-integration.md) - Orderbook data
