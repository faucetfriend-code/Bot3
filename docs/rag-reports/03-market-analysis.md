# Market Analysis

<!--
RAG Metadata:
- Category: Analysis
- Tags: market-regime, indicators, adx, rsi, atr, macd, bollinger-bands, multi-timeframe
- Related: 02-trading-strategies, 01-core-trading-logic
-->

## Overview

Market analysis consists of:
1. **Market Regime Detection** - Classifies market conditions
2. **Technical Indicators** - Calculation functions
3. **Multi-Timeframe Fetcher** - Data retrieval across timeframes

---

## Market Regime Detection

**Location**: `trading_bot_v2/market_regime.py`

### Purpose
Classifies market into 5 regimes using ADX and volatility metrics.

### Regime Types

```python
class MarketRegime(Enum):
    TRENDING_STRONG = "trending_strong"    # ADX > 30
    TRENDING_MODERATE = "trending_moderate" # 25 < ADX ≤ 30
    RANGING_VOLATILE = "ranging_volatile"   # ADX ≤ 25, high volatility
    RANGING_CALM = "ranging_calm"           # ADX ≤ 25, low volatility
    INDECISIVE = "indecisive"               # Transitional/choppy
```

### Thresholds (Jan 2026 - Updated)

| Parameter | Value | Previous |
|-----------|-------|----------|
| ADX Trending Threshold | 30.0 | 28.0 |
| ADX Moderate Threshold | 25.0 | 22.0 |
| ADX Ranging Threshold | 25.0 | 22.0 |
| Volatility High Percentile | 65% | 75% |

### Detection Algorithm

```python
def detect_regime(self, market_data: Dict[str, List[float]]) -> MarketRegime:
    """
    Detect current market regime from market data.
    
    Classification Logic:
    1. Calculate ADX (Average Directional Index)
    2. If ADX > 30: TRENDING_STRONG
    3. If 25 < ADX ≤ 30: TRENDING_MODERATE
    4. If ADX ≤ 25:
       - Calculate volatility percentile
       - If volatility > 65%: RANGING_VOLATILE
       - If volatility ≤ 65%: RANGING_CALM
    5. If unclear: INDECISIVE
    
    Args:
        market_data: Dict with keys 'high', 'low', 'close', 'volume'
    
    Returns:
        MarketRegime enum value
    """
```

### Multi-Factor Analysis

```
┌─────────────────────────────────────────────────────┐
│              Market Regime Detection                 │
├─────────────────────────────────────────────────────┤
│                                                      │
│   ┌──────────────┐    ┌──────────────┐             │
│   │  ADX > 30    │    │ 25 < ADX≤30  │             │
│   │              │    │              │             │
│   │   TRENDING   │    │  TRENDING    │             │
│   │   _STRONG    │    │  _MODERATE   │             │
│   └──────────────┘    └──────────────┘             │
│                                                      │
│   ┌──────────────────────────────────────┐         │
│   │            ADX ≤ 25                   │         │
│   │  ┌─────────────┐  ┌─────────────┐   │         │
│   │  │ Volatility  │  │ Volatility  │   │         │
│   │  │   > 65%     │  │   ≤ 65%     │   │         │
│   │  │             │  │             │   │         │
│   │  │  RANGING_   │  │  RANGING_   │   │         │
│   │  │  VOLATILE   │  │   CALM      │   │         │
│   │  └─────────────┘  └─────────────┘   │         │
│   └──────────────────────────────────────┘         │
│                                                      │
└─────────────────────────────────────────────────────┘
```

### Caching

```python
# Regime caching to prevent unnecessary recalculation
_cache_ttl_hours = 4  # Recalculate every 4 hours
_trend_cache_ttl_minutes = 5  # Trend direction cache TTL
```

---

## Technical Indicators

**Location**: `trading_bot_v2/indicators.py`

### Available Indicators

| Indicator | Function | Purpose |
|-----------|----------|---------|
| SMA | `calculate_sma()` | Simple Moving Average |
| EMA | `calculate_ema()` | Exponential Moving Average |
| RSI | `calculate_rsi()` | Relative Strength Index |
| ATR | `calculate_atr()` | Average True Range |
| ADX | `calculate_adx()` | Average Directional Index |
| MACD | `calculate_macd()` | Moving Average Convergence Divergence |
| Bollinger Bands | `calculate_bollinger_bands()` | Volatility bands |
| VWAP | `calculate_vwap()` | Volume Weighted Average Price |

### SMA (Simple Moving Average)

```python
def calculate_sma(prices: List[float], period: int) -> float:
    """
    Calculate Simple Moving Average.
    
    Formula: SMA = Sum(Prices[-period:]) / period
    
    Args:
        prices: List of price values
        period: Period for SMA calculation
        
    Returns:
        Simple moving average value
    """
```

### EMA (Exponential Moving Average)

```python
def calculate_ema(prices: List[float], period: int) -> float:
    """
    Calculate Exponential Moving Average.
    
    Formula:
        multiplier = 2 / (period + 1)
        EMA = (price * multiplier) + (prev_EMA * (1 - multiplier))
    
    Args:
        prices: List of price values
        period: Period for EMA calculation
        
    Returns:
        Exponential moving average value
    """
```

### RSI (Relative Strength Index)

```python
def calculate_rsi(prices: List[float], period: int = 14) -> float:
    """
    Calculate Relative Strength Index.
    
    Formula:
        RS = Average Gain / Average Loss
        RSI = 100 - (100 / (1 + RS))
    
    Args:
        prices: List of price values
        period: Period for RSI calculation (default: 14)
        
    Returns:
        RSI value between 0 and 100
        
    Interpretation:
        - RSI < 30: Oversold
        - RSI > 70: Overbought
        - RSI 30-70: Neutral
    """
```

### ATR (Average True Range)

```python
def calculate_atr(
    highs: List[float], 
    lows: List[float], 
    closes: List[float], 
    period: int = 14
) -> float:
    """
    Calculate Average True Range.
    
    True Range = max(
        high - low,
        |high - prev_close|,
        |low - prev_close|
    )
    
    ATR = SMA(True Range, period)
    
    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of close prices
        period: Period for ATR calculation (default: 14)
        
    Returns:
        Average True Range value
    """
```

### ADX (Average Directional Index)

```python
def calculate_adx(
    highs: List[float], 
    lows: List[float], 
    closes: List[float], 
    period: int = 14
) -> float:
    """
    Calculate Average Directional Index.
    
    Measures trend strength (not direction):
        - ADX < 20: Weak/no trend
        - ADX 20-25: Building trend
        - ADX 25-50: Strong trend
        - ADX > 50: Very strong trend
    
    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of close prices
        period: Period for ADX calculation (default: 14)
        
    Returns:
        ADX value (0-100)
    """
```

### Bollinger Bands

```python
def calculate_bollinger_bands(
    closes: List[float], 
    period: int = 20, 
    std_dev: float = 2.0
) -> Tuple[float, float, float]:
    """
    Calculate Bollinger Bands.
    
    Middle Band = SMA(period)
    Upper Band = Middle + (std_dev * StdDev)
    Lower Band = Middle - (std_dev * StdDev)
    
    Args:
        closes: List of close prices
        period: Period for SMA (default: 20)
        std_dev: Standard deviation multiplier (default: 2.0)
        
    Returns:
        Tuple of (upper_band, middle_band, lower_band)
    """
```

### MACD

```python
def calculate_macd(
    closes: List[float],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9
) -> Tuple[float, float, float]:
    """
    Calculate MACD (Moving Average Convergence Divergence).
    
    MACD Line = EMA(fast) - EMA(slow)
    Signal Line = EMA(MACD Line, signal_period)
    Histogram = MACD Line - Signal Line
    
    Args:
        closes: List of close prices
        fast_period: Fast EMA period (default: 12)
        slow_period: Slow EMA period (default: 26)
        signal_period: Signal line period (default: 9)
        
    Returns:
        Tuple of (macd_line, signal_line, histogram)
    """
```

---

## Multi-Timeframe Fetcher

**Location**: `trading_bot_v2/multi_timeframe_fetcher.py`

### Purpose
Fetches and caches market data across multiple timeframes.

### Supported Timeframes

| Timeframe | Use Case |
|-----------|----------|
| 1m | Entry timing (ExecutionLayer) |
| 5m | Entry timing (ExecutionLayer) |
| 15m | Short-term analysis |
| 1h | Primary signal generation |
| 4h | Trend direction |
| 1d | Long-term context |

### Cache Configuration

```python
# Tiered cache TTL based on timeframe
CACHE_TTL = {
    '1m': 30,    # 30 seconds
    '5m': 60,    # 1 minute
    '15m': 120,  # 2 minutes
    '1h': 300,   # 5 minutes
    '4h': 600,   # 10 minutes
    '1d': 1800,  # 30 minutes
}
```

### Key Methods

```python
async def get_candles_multi_tf(
    self,
    symbol: str,
    timeframes: List[str] = ['1h', '15m', '5m', '1m'],
    limit: int = 200
) -> Dict[str, Dict[str, List[float]]]:
    """
    Fetch candle data for multiple timeframes.
    
    Args:
        symbol: Trading symbol
        timeframes: List of timeframe strings
        limit: Number of candles per timeframe
        
    Returns:
        Dict mapping timeframe to market_data dict:
        {
            '1h': {'open': [...], 'high': [...], 'low': [...], 'close': [...], 'volume': [...]},
            '5m': {...},
            ...
        }
    """
```

---

## Indicator Usage by Strategy

| Strategy | RSI | ATR | ADX | MACD | BB | EMA | VWAP |
|----------|:---:|:---:|:---:|:----:|:--:|:---:|:----:|
| Mean Reversion | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ |
| MA Crossover | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| Grid Trading | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| Liquidation Capture | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| VWAP Scalping | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ | ✅ |
| Momentum Scalping | ✅ | ❌ | ❌ | ✅ | ❌ | ✅ | ❌ |
| Order Book Imbalance | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## Related Reports

- [02-trading-strategies.md](./02-trading-strategies.md) - Strategy implementations
- [01-core-trading-logic.md](./01-core-trading-logic.md) - Signal generation
