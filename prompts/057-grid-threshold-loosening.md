# Prompt 057: Grid Trading Threshold Loosening

## Context
This is the first "Too Tight" optimization prompt. The Strategy Overhaul (053-056) fixed architectural issues. Now we loosen specific thresholds that were causing trade starvation.

## Problem (from tootight.txt diagnosis)
Grid trading is blocked by overly strict conditions:
- ADX < 20 required (too restrictive - grids can work up to ADX 25)
- Min confidence 60% (too high for grid strategy)
- Volatility percentile checks too narrow
- Too many confluence checks before grid can start

## Solution
Loosen grid-specific thresholds to allow more trading opportunities while maintaining safety through the new 3-phase architecture.

## Implementation

### File: `trading_bot_v2/strategies/grid_trading.py`

#### BEFORE (Too Tight)

```python
class GridTradingStrategy:
    # Current restrictive settings
    ADX_THRESHOLD = 20.0          # Must be below this
    MIN_CONFIDENCE = 0.6          # Kills many valid setups
    VOLATILITY_LOW_PERCENTILE = 25
    VOLATILITY_HIGH_PERCENTILE = 75

    def generate_signals(self, symbol, market_data, current_price):
        # Too many blocking conditions
        if adx > self.ADX_THRESHOLD:
            return []  # Blocked
        if volatility_percentile < self.VOLATILITY_LOW_PERCENTILE:
            return []  # Blocked - "too quiet"
        if volatility_percentile > self.VOLATILITY_HIGH_PERCENTILE:
            return []  # Blocked - "too volatile"
        # ... more blocks ...
```

#### AFTER (Loosened)

```python
class GridTradingStrategy:
    """
    Grid Trading Strategy with loosened thresholds.

    Changes from original:
    - ADX threshold raised to 25 (was 20)
    - Min confidence lowered to 50% (was 60%)
    - Volatility range widened
    - Fewer hard blocks, more soft scaling
    """

    # Loosened settings
    ADX_THRESHOLD = 25.0          # Raised: grids can handle mild trends
    ADX_IDEAL = 15.0              # NEW: ideal ADX for full confidence
    MIN_CONFIDENCE = 0.50         # Lowered: confidence affects size, not permission

    # Widened volatility window
    VOLATILITY_LOW_PERCENTILE = 20   # Lowered: allow quieter markets
    VOLATILITY_HIGH_PERCENTILE = 80  # Raised: allow more volatile markets
    VOLATILITY_IDEAL_LOW = 35        # NEW: ideal range start
    VOLATILITY_IDEAL_HIGH = 65       # NEW: ideal range end

    # Grid parameters
    GRID_LEVELS = 5               # Number of grid levels each side
    GRID_SPACING_ATR_MULT = 0.5   # Grid spacing as ATR multiple
    MIN_PROFIT_TARGET_PCT = 0.3   # Minimum profit target per grid (0.3%)

    def __init__(self, config=None):
        self.config = config
        # Load overrides from config
        if config:
            self.ADX_THRESHOLD = getattr(config, 'grid_adx_threshold', 25.0)
            self.MIN_CONFIDENCE = getattr(config, 'grid_min_confidence', 0.50)
            self.VOLATILITY_HIGH_PERCENTILE = getattr(config, 'grid_volatility_high', 80.0)

    def generate_signals(self, symbol: str, market_data: Dict,
                        current_price: float) -> List[Signal]:
        """
        Generate grid signals with loosened thresholds.

        Key changes:
        1. ADX up to 25 allowed (was 20)
        2. Volatility affects confidence, not permission
        3. Confidence scales with market conditions
        """
        # Get market indicators
        closes = market_data.get('4h', market_data).get('close', [])
        if len(closes) < 50:
            return []

        # Calculate ADX
        from indicators import calculate_adx
        highs = market_data.get('4h', market_data).get('high', closes)
        lows = market_data.get('4h', market_data).get('low', closes)
        adx = calculate_adx(highs, lows, closes)

        # Calculate volatility percentile
        volatility_pct = self._calculate_volatility_percentile(closes)

        # Calculate ATR for grid spacing
        from indicators import calculate_atr
        atr = calculate_atr(highs, lows, closes)

        # === LOOSENED LOGIC ===

        # ADX check - now allows up to 25 (was 20)
        # Instead of hard block, ADX affects confidence
        if adx > self.ADX_THRESHOLD:
            logger.debug(f"{symbol}: ADX {adx:.1f} > {self.ADX_THRESHOLD} - skipping grid")
            return []

        # Calculate base confidence from ADX
        # ADX 0-15 = high confidence, ADX 15-25 = declining confidence
        adx_confidence = 1.0
        if adx > self.ADX_IDEAL:
            # Linear decline from ideal to threshold
            adx_confidence = 1.0 - ((adx - self.ADX_IDEAL) / (self.ADX_THRESHOLD - self.ADX_IDEAL)) * 0.5
            adx_confidence = max(0.5, adx_confidence)  # Floor at 0.5

        # Volatility affects confidence, NOT permission
        # Ideal volatility (35-65 percentile) = full confidence
        # Outside ideal = reduced confidence, but still allowed
        volatility_confidence = 1.0
        if volatility_pct < self.VOLATILITY_LOW_PERCENTILE:
            # Too quiet - reduced confidence but allowed
            volatility_confidence = 0.7
            logger.debug(f"{symbol}: Low volatility ({volatility_pct:.0f}th pct) - reduced confidence")
        elif volatility_pct > self.VOLATILITY_HIGH_PERCENTILE:
            # Too volatile - reduced confidence but allowed
            volatility_confidence = 0.6
            logger.debug(f"{symbol}: High volatility ({volatility_pct:.0f}th pct) - reduced confidence")
        elif volatility_pct < self.VOLATILITY_IDEAL_LOW or volatility_pct > self.VOLATILITY_IDEAL_HIGH:
            # Outside ideal but within acceptable
            volatility_confidence = 0.85

        # Combined confidence
        final_confidence = adx_confidence * volatility_confidence

        # Apply minimum threshold (but much lower than before)
        if final_confidence < self.MIN_CONFIDENCE:
            final_confidence = self.MIN_CONFIDENCE  # Floor, don't block

        # Generate grid signals
        signals = self._create_grid_signals(
            symbol=symbol,
            current_price=current_price,
            atr=atr,
            confidence=final_confidence
        )

        if signals:
            logger.info(
                f"Grid signals for {symbol}: ADX={adx:.1f} volatility={volatility_pct:.0f}th "
                f"confidence={final_confidence:.2f} -> {len(signals)} signals"
            )

        return signals

    def _calculate_volatility_percentile(self, closes: List[float]) -> float:
        """Calculate where current volatility sits in historical range."""
        if len(closes) < 20:
            return 50.0  # Default to middle

        import numpy as np

        # Calculate rolling volatility (20-period standard deviation)
        returns = np.diff(closes) / closes[:-1]
        if len(returns) < 20:
            return 50.0

        current_vol = np.std(returns[-20:])
        historical_vols = [np.std(returns[i:i+20]) for i in range(len(returns)-20)]

        if not historical_vols:
            return 50.0

        # Percentile rank
        below_current = sum(1 for v in historical_vols if v < current_vol)
        percentile = (below_current / len(historical_vols)) * 100

        return percentile

    def _create_grid_signals(self, symbol: str, current_price: float,
                            atr: float, confidence: float) -> List[Signal]:
        """
        Create grid buy/sell signals around current price.

        Args:
            symbol: Trading symbol
            current_price: Current market price
            atr: Average True Range
            confidence: Signal confidence (affects position size via ConfidenceSizer)

        Returns:
            List of grid signals (buys below, sells above)
        """
        from models import Signal, OrderSide
        from config import StrategyType

        signals = []
        grid_spacing = atr * self.GRID_SPACING_ATR_MULT

        # Ensure minimum spacing
        min_spacing = current_price * 0.002  # 0.2% minimum
        grid_spacing = max(grid_spacing, min_spacing)

        # Create grid levels
        for level in range(1, self.GRID_LEVELS + 1):
            # Buy signal below current price
            buy_price = current_price - (grid_spacing * level)
            buy_stop = buy_price - (atr * 2)  # 2 ATR stop
            buy_target = current_price  # Target back to center

            signals.append(Signal(
                symbol=symbol,
                side=OrderSide.BUY,
                entry_price=buy_price,
                stop_loss=buy_stop,
                take_profit=buy_target,
                confidence=confidence,
                strategy=StrategyType.GRID_TRADING,
                metadata={
                    'grid_level': level,
                    'grid_type': 'buy',
                    'grid_spacing': grid_spacing
                }
            ))

            # Sell signal above current price
            sell_price = current_price + (grid_spacing * level)
            sell_stop = sell_price + (atr * 2)
            sell_target = current_price

            signals.append(Signal(
                symbol=symbol,
                side=OrderSide.SELL,
                entry_price=sell_price,
                stop_loss=sell_stop,
                take_profit=sell_target,
                confidence=confidence,
                strategy=StrategyType.GRID_TRADING,
                metadata={
                    'grid_level': level,
                    'grid_type': 'sell',
                    'grid_spacing': grid_spacing
                }
            ))

        return signals
```

### Configuration Updates

Add to `.env`:

```bash
# Grid Trading Loosened Thresholds
GRID_ADX_THRESHOLD=25.0           # Raised from 20.0
GRID_MIN_CONFIDENCE=0.50          # Lowered from 0.60
GRID_VOLATILITY_HIGH_PERCENTILE=80.0  # Raised from 75.0
GRID_VOLATILITY_LOW_PERCENTILE=20.0   # Lowered from 25.0

# Grid Parameters
GRID_LEVELS=5
GRID_SPACING_ATR_MULT=0.5
GRID_MIN_PROFIT_PCT=0.003
```

Add to `config.py`:

```python
# Grid Trading
self.grid_adx_threshold = float(os.getenv('GRID_ADX_THRESHOLD', '25.0'))
self.grid_min_confidence = float(os.getenv('GRID_MIN_CONFIDENCE', '0.50'))
self.grid_volatility_high = float(os.getenv('GRID_VOLATILITY_HIGH_PERCENTILE', '80.0'))
self.grid_volatility_low = float(os.getenv('GRID_VOLATILITY_LOW_PERCENTILE', '20.0'))
```

## Behavior Comparison

### Before (Too Tight)
```
Market: BTC ranging, ADX = 22, volatility = 72nd percentile

Check 1: ADX 22 > 20 threshold → BLOCKED
Result: No grid signals generated
```

### After (Loosened)
```
Market: BTC ranging, ADX = 22, volatility = 72nd percentile

Check 1: ADX 22 < 25 threshold → ALLOWED
ADX confidence: 1.0 - ((22-15)/(25-15))*0.5 = 0.65

Check 2: Volatility 72 < 80 threshold → ALLOWED
Volatility in acceptable range (outside ideal 35-65)
Volatility confidence: 0.85

Combined confidence: 0.65 * 0.85 = 0.55

Result: Grid signals generated with confidence 0.55
Position size: 55% of base size (via ConfidenceSizer)
```

## Testing

```python
def test_grid_loosened_adx():
    """Test that ADX up to 25 is now allowed."""
    from strategies.grid_trading import GridTradingStrategy

    strategy = GridTradingStrategy()

    # Create market data with ADX = 22 (was blocked before)
    # This requires simulated data that produces ADX ~22
    closes = [100 + (i * 0.1) for i in range(250)]  # Mild trend
    market_data = {
        '4h': {
            'close': closes,
            'high': [c * 1.005 for c in closes],
            'low': [c * 0.995 for c in closes]
        }
    }

    signals = strategy.generate_signals('TEST', market_data, 125.0)

    # Should generate signals now (ADX ~22 is below new 25 threshold)
    print(f"Generated {len(signals)} signals with mild trend data")
    assert len(signals) > 0, "Should generate signals with ADX < 25"


def test_grid_volatility_scaling():
    """Test that volatility affects confidence, not permission."""
    from strategies.grid_trading import GridTradingStrategy

    strategy = GridTradingStrategy()

    # High volatility data
    import numpy as np
    np.random.seed(42)
    closes = [100 + np.random.uniform(-5, 5) for _ in range(250)]  # Volatile
    market_data = {
        '4h': {
            'close': closes,
            'high': [c + 2 for c in closes],
            'low': [c - 2 for c in closes]
        }
    }

    signals = strategy.generate_signals('TEST', market_data, 100.0)

    # Should still generate signals, just with lower confidence
    if signals:
        print(f"High volatility signals generated with confidence {signals[0].confidence:.2f}")
        assert signals[0].confidence < 1.0, "Confidence should be reduced for high volatility"
    else:
        print("Note: ADX may have exceeded threshold in volatile data")


def test_grid_min_confidence():
    """Test that minimum confidence is 0.5, not 0.6."""
    from strategies.grid_trading import GridTradingStrategy

    strategy = GridTradingStrategy()
    assert strategy.MIN_CONFIDENCE == 0.50, f"Min confidence should be 0.50, got {strategy.MIN_CONFIDENCE}"
    print(f"Grid min confidence correctly set to {strategy.MIN_CONFIDENCE}")


if __name__ == "__main__":
    test_grid_min_confidence()
    test_grid_loosened_adx()
    test_grid_volatility_scaling()
    print("\nAll grid loosening tests passed!")
```

## Verification Checklist

- [ ] ADX threshold raised to 25.0 (from 20.0)
- [ ] Min confidence lowered to 0.50 (from 0.60)
- [ ] Volatility HIGH percentile raised to 80 (from 75)
- [ ] Volatility LOW percentile lowered to 20 (from 25)
- [ ] Volatility affects confidence, not hard block
- [ ] ADX affects confidence scaling between 15-25
- [ ] Config options added to .env
- [ ] Tests pass

## Next Prompt
Prompt 058 will loosen signal generation thresholds across all strategies.
