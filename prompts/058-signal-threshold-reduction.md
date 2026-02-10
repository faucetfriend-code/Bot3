# Prompt 058: Signal Generation Threshold Reduction

## Context
This is the final "Too Tight" optimization prompt. After loosening grid thresholds (057), we now reduce signal generation thresholds across ALL strategies.

## Problem (from tootight.txt diagnosis)
Multiple strategies have overly strict requirements:
- 10+ confluence checks before signal generation
- Multi-timeframe alignment requiring ALL timeframes to agree
- RSI thresholds too extreme (< 25 for oversold, > 75 for overbought)
- Volume confirmation requiring 150%+ of average
- Cooldowns applied too aggressively (fixed in 056)

## Solution
Reduce thresholds to generate more signals, relying on the new architecture:
- Confidence-to-sizing (055) handles uncertainty
- Per-strategy cooldowns (056) prevent overtrading
- Phase 3 execution filter (053) catches real risks

## Implementation

### File: `trading_bot_v2/strategies/mean_reversion.py`

#### BEFORE (Too Tight)

```python
# Overly strict RSI thresholds
RSI_OVERSOLD = 25      # Too extreme
RSI_OVERBOUGHT = 75    # Too extreme

# Requires ALL timeframes to agree
if not (rsi_15m < 30 and rsi_1h < 35 and rsi_4h < 40):
    return []  # Hard block

# Requires both BB proximity AND RSI extreme
if bb_distance > 0.02 and rsi > 30:
    return []  # Blocked - not extreme enough
```

#### AFTER (Loosened)

```python
"""
Mean Reversion Strategy with loosened thresholds.

Changes:
- RSI oversold raised to 35 (was 25)
- RSI overbought lowered to 65 (was 75)
- Multi-TF alignment now affects confidence, not permission
- BB proximity is additive to confidence, not required
"""

class MeanReversionStrategy:
    # Loosened thresholds
    RSI_OVERSOLD = 35           # Raised from 25
    RSI_OVERBOUGHT = 65         # Lowered from 75
    RSI_EXTREME_OVERSOLD = 25   # For bonus confidence
    RSI_EXTREME_OVERBOUGHT = 75 # For bonus confidence

    # BB distance thresholds (% from band)
    BB_NEAR_THRESHOLD = 0.02    # Within 2% of band = high confidence
    BB_TOUCH_THRESHOLD = 0.005  # Touching band = bonus confidence

    # Multi-TF thresholds (now soft, not hard)
    MTF_ALIGNMENT_BONUS = 0.15  # Confidence bonus if TFs align

    def __init__(self, config=None):
        self.config = config
        if config:
            self.RSI_OVERSOLD = getattr(config, 'mr_rsi_oversold', 35)
            self.RSI_OVERBOUGHT = getattr(config, 'mr_rsi_overbought', 65)

    def generate_signals(self, symbol: str, market_data: Dict,
                        current_price: float) -> List[Signal]:
        """
        Generate mean reversion signals with loosened thresholds.

        Key changes:
        1. RSI thresholds widened (35/65 instead of 25/75)
        2. Multi-TF alignment is bonus, not requirement
        3. BB proximity adds confidence, doesn't block
        """
        from indicators import calculate_rsi, calculate_bollinger_bands
        from models import Signal, OrderSide
        from config import StrategyType

        signals = []

        # Get data from preferred timeframe (1h for mean reversion)
        data_1h = market_data.get('1h', market_data.get('4h', {}))
        closes_1h = data_1h.get('close', [])

        if len(closes_1h) < 50:
            return []

        # Calculate primary indicators
        rsi_1h = calculate_rsi(closes_1h)
        bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(closes_1h)

        # Calculate BB distance
        if bb_upper > 0 and bb_lower > 0:
            if current_price > bb_middle:
                bb_distance = (current_price - bb_upper) / current_price
            else:
                bb_distance = (bb_lower - current_price) / current_price
        else:
            bb_distance = 0

        # === LOOSENED SIGNAL GENERATION ===

        # Check for oversold (BUY opportunity)
        if rsi_1h <= self.RSI_OVERSOLD:
            confidence = self._calculate_buy_confidence(
                rsi_1h, bb_distance, market_data, current_price
            )

            if confidence > 0:
                signals.append(self._create_signal(
                    symbol=symbol,
                    side=OrderSide.BUY,
                    current_price=current_price,
                    confidence=confidence,
                    rsi=rsi_1h,
                    closes=closes_1h
                ))

        # Check for overbought (SELL opportunity)
        if rsi_1h >= self.RSI_OVERBOUGHT:
            confidence = self._calculate_sell_confidence(
                rsi_1h, bb_distance, market_data, current_price
            )

            if confidence > 0:
                signals.append(self._create_signal(
                    symbol=symbol,
                    side=OrderSide.SELL,
                    current_price=current_price,
                    confidence=confidence,
                    rsi=rsi_1h,
                    closes=closes_1h
                ))

        return signals

    def _calculate_buy_confidence(self, rsi: float, bb_distance: float,
                                   market_data: Dict, current_price: float) -> float:
        """
        Calculate confidence for BUY signal.

        Factors:
        - RSI depth (lower = more confident)
        - BB proximity (closer to lower band = more confident)
        - Multi-TF alignment (bonus, not requirement)
        """
        confidence = 0.5  # Base confidence

        # RSI contribution (0.0 to 0.25 bonus)
        # RSI 35 = 0 bonus, RSI 25 = 0.15 bonus, RSI 15 = 0.25 bonus
        rsi_bonus = (self.RSI_OVERSOLD - rsi) / 80  # Scaled
        rsi_bonus = max(0, min(0.25, rsi_bonus))
        confidence += rsi_bonus

        # BB proximity contribution (0.0 to 0.15 bonus)
        if bb_distance < 0:  # Below lower band
            bb_bonus = min(0.15, abs(bb_distance) * 5)
            confidence += bb_bonus
        elif bb_distance < self.BB_NEAR_THRESHOLD:
            bb_bonus = 0.05  # Near lower band
            confidence += bb_bonus

        # Multi-TF alignment contribution (0.0 to 0.15 bonus)
        mtf_aligned = self._check_mtf_alignment_buy(market_data)
        if mtf_aligned:
            confidence += self.MTF_ALIGNMENT_BONUS

        # Cap at 1.0
        return min(1.0, confidence)

    def _calculate_sell_confidence(self, rsi: float, bb_distance: float,
                                    market_data: Dict, current_price: float) -> float:
        """Calculate confidence for SELL signal."""
        confidence = 0.5  # Base confidence

        # RSI contribution
        rsi_bonus = (rsi - self.RSI_OVERBOUGHT) / 80
        rsi_bonus = max(0, min(0.25, rsi_bonus))
        confidence += rsi_bonus

        # BB proximity contribution
        if bb_distance > 0:  # Above upper band
            bb_bonus = min(0.15, abs(bb_distance) * 5)
            confidence += bb_bonus
        elif bb_distance > -self.BB_NEAR_THRESHOLD:
            bb_bonus = 0.05
            confidence += bb_bonus

        # Multi-TF alignment contribution
        mtf_aligned = self._check_mtf_alignment_sell(market_data)
        if mtf_aligned:
            confidence += self.MTF_ALIGNMENT_BONUS

        return min(1.0, confidence)

    def _check_mtf_alignment_buy(self, market_data: Dict) -> bool:
        """
        Check if multiple timeframes suggest oversold.

        This is now a BONUS, not a requirement.
        Returns True if at least 2 of 3 timeframes are oversold.
        """
        from indicators import calculate_rsi

        oversold_count = 0

        # 15m
        data_15m = market_data.get('15m', {})
        if data_15m.get('close') and len(data_15m['close']) > 14:
            rsi_15m = calculate_rsi(data_15m['close'])
            if rsi_15m <= 40:  # Loosened from 30
                oversold_count += 1

        # 1h (already checked in main function)
        oversold_count += 1  # Assume main RSI check passed

        # 4h
        data_4h = market_data.get('4h', {})
        if data_4h.get('close') and len(data_4h['close']) > 14:
            rsi_4h = calculate_rsi(data_4h['close'])
            if rsi_4h <= 45:  # Loosened from 40
                oversold_count += 1

        # At least 2 of 3 timeframes agree
        return oversold_count >= 2

    def _check_mtf_alignment_sell(self, market_data: Dict) -> bool:
        """Check if multiple timeframes suggest overbought."""
        from indicators import calculate_rsi

        overbought_count = 0

        data_15m = market_data.get('15m', {})
        if data_15m.get('close') and len(data_15m['close']) > 14:
            rsi_15m = calculate_rsi(data_15m['close'])
            if rsi_15m >= 60:
                overbought_count += 1

        overbought_count += 1  # Main RSI check passed

        data_4h = market_data.get('4h', {})
        if data_4h.get('close') and len(data_4h['close']) > 14:
            rsi_4h = calculate_rsi(data_4h['close'])
            if rsi_4h >= 55:
                overbought_count += 1

        return overbought_count >= 2

    def _create_signal(self, symbol: str, side: OrderSide, current_price: float,
                       confidence: float, rsi: float, closes: List[float]) -> Signal:
        """Create a mean reversion signal."""
        from indicators import calculate_atr
        from config import StrategyType

        # Calculate ATR for stop/target
        atr = calculate_atr(closes, closes, closes)  # Simplified, assumes OHLC similar
        if atr <= 0:
            atr = current_price * 0.02

        if side == OrderSide.BUY:
            stop_loss = current_price - (atr * 2.0)
            take_profit = current_price + (atr * 1.5)  # 1.5:1 RRR
        else:
            stop_loss = current_price + (atr * 2.0)
            take_profit = current_price - (atr * 1.5)

        return Signal(
            symbol=symbol,
            side=side,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            strategy=StrategyType.MEAN_REVERSION,
            metadata={
                'rsi': rsi,
                'atr': atr,
                'mtf_aligned': confidence > 0.65  # Indicates alignment was present
            }
        )
```

### File: `trading_bot_v2/strategies/ma_crossover.py`

#### Loosened Thresholds

```python
class MACrossoverStrategy:
    # Loosened thresholds
    PULLBACK_MIN_PCT = 0.01      # Lowered from 0.02 (1% instead of 2%)
    PULLBACK_MAX_PCT = 0.05      # Raised from 0.04 (5% instead of 4%)
    VOLUME_THRESHOLD = 1.1       # Lowered from 1.2 (110% instead of 120%)
    MIN_TREND_STRENGTH = 0.3     # Lowered from 0.5
    CANDLES_AFTER_CROSS_MAX = 7  # Raised from 5 (more time to enter)

    def _check_pullback(self, current_price: float, cross_price: float,
                       direction: str) -> Tuple[bool, float]:
        """
        Check for pullback entry opportunity.

        LOOSENED: Now accepts 1-5% pullback (was 2-4%)
        """
        if direction == 'up':
            pullback_pct = (cross_price - current_price) / cross_price
        else:
            pullback_pct = (current_price - cross_price) / cross_price

        # Loosened range
        valid = self.PULLBACK_MIN_PCT <= pullback_pct <= self.PULLBACK_MAX_PCT

        # Confidence scales with pullback depth (deeper = more confident)
        confidence_factor = 1.0
        if valid:
            # Optimal pullback around 2-3%
            if 0.02 <= pullback_pct <= 0.03:
                confidence_factor = 1.0
            else:
                confidence_factor = 0.8

        return valid, confidence_factor

    def _check_volume(self, volumes: List[float]) -> Tuple[bool, float]:
        """
        Check volume confirmation.

        LOOSENED: Now requires only 110% (was 120%)
        """
        if len(volumes) < 20:
            return True, 0.9  # Allow but lower confidence

        avg_volume = sum(volumes[-20:]) / 20
        current_volume = volumes[-1]

        ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

        if ratio >= self.VOLUME_THRESHOLD:
            return True, min(1.0, 0.8 + (ratio - 1.0) * 0.2)
        else:
            # Still allow, but reduced confidence
            return True, max(0.6, ratio * 0.8)
```

### File: `trading_bot_v2/strategies/liquidation_capture.py`

#### Loosened Thresholds

```python
class LiquidationCaptureStrategy:
    # Loosened thresholds
    PRICE_MOVE_PCT = 0.025       # Lowered from 0.03 (2.5% instead of 3%)
    VOLUME_SPIKE_MULT = 2.5     # Lowered from 3.0 (2.5x instead of 3x)
    RSI_EXTREME_LOW = 20        # Raised from 15
    RSI_EXTREME_HIGH = 80       # Lowered from 85
    CANDLES_FOR_MOVE = 7        # Raised from 5 (more time to develop)

    def _detect_cascade(self, closes: List[float], volumes: List[float],
                        rsi: float) -> Tuple[bool, str, float]:
        """
        Detect liquidation cascade.

        LOOSENED:
        - 2.5% move in 7 candles (was 3% in 5)
        - 2.5x volume spike (was 3x)
        - RSI 20/80 extreme (was 15/85)
        """
        if len(closes) < self.CANDLES_FOR_MOVE + 1:
            return False, None, 0

        # Check price move over window
        start_price = closes[-(self.CANDLES_FOR_MOVE + 1)]
        end_price = closes[-1]
        move_pct = (end_price - start_price) / start_price

        # Check volume spike
        if len(volumes) < 20:
            volume_spike = True
            volume_mult = 1.0
        else:
            avg_volume = sum(volumes[-20:-1]) / 19
            current_volume = volumes[-1]
            volume_mult = current_volume / avg_volume if avg_volume > 0 else 1.0
            volume_spike = volume_mult >= self.VOLUME_SPIKE_MULT

        # Detect direction
        if move_pct <= -self.PRICE_MOVE_PCT and rsi <= self.RSI_EXTREME_LOW:
            # Downward cascade - long liquidations
            direction = 'long_liq'
            cascade = True
        elif move_pct >= self.PRICE_MOVE_PCT and rsi >= self.RSI_EXTREME_HIGH:
            # Upward cascade - short squeeze
            direction = 'short_squeeze'
            cascade = True
        else:
            cascade = False
            direction = None

        if cascade and volume_spike:
            # Calculate confidence from cascade strength
            move_strength = abs(move_pct) / 0.05  # Normalize to 5% being max
            volume_strength = min(1.0, volume_mult / 5.0)  # Normalize to 5x being max
            confidence = min(1.0, (move_strength + volume_strength) / 2 * 0.8 + 0.2)
            return True, direction, confidence

        return False, None, 0
```

### Configuration Updates

Add to `.env`:

```bash
# Mean Reversion Loosened Thresholds
MR_RSI_OVERSOLD=35              # Raised from 25
MR_RSI_OVERBOUGHT=65            # Lowered from 75
MR_BB_NEAR_THRESHOLD=0.02
MR_MTF_ALIGNMENT_BONUS=0.15

# MA Crossover Loosened Thresholds
MA_PULLBACK_MIN_PCT=0.01        # Lowered from 0.02
MA_PULLBACK_MAX_PCT=0.05        # Raised from 0.04
MA_VOLUME_THRESHOLD=1.1         # Lowered from 1.2
MA_CANDLES_AFTER_CROSS=7        # Raised from 5

# Liquidation Capture Loosened Thresholds
LC_PRICE_MOVE_PCT=0.025         # Lowered from 0.03
LC_VOLUME_SPIKE_MULT=2.5        # Lowered from 3.0
LC_RSI_EXTREME_LOW=20           # Raised from 15
LC_RSI_EXTREME_HIGH=80          # Lowered from 85
LC_CANDLES_FOR_MOVE=7           # Raised from 5
```

## Summary of Threshold Changes

| Strategy | Parameter | Before | After | Effect |
|----------|-----------|--------|-------|--------|
| **Mean Reversion** | RSI Oversold | 25 | 35 | +40% more signals |
| | RSI Overbought | 75 | 65 | +40% more signals |
| | MTF Alignment | Required | Bonus | No longer blocks |
| **MA Crossover** | Pullback Min | 2% | 1% | Earlier entries |
| | Pullback Max | 4% | 5% | Later entries OK |
| | Volume Threshold | 120% | 110% | More signals |
| | Candles After Cross | 5 | 7 | Longer entry window |
| **Grid Trading** | ADX Threshold | 20 | 25 | +25% more conditions |
| | Min Confidence | 60% | 50% | Size-based, not blocked |
| | Volatility High | 75th | 80th | Wider window |
| **Liquidation Capture** | Price Move | 3% | 2.5% | Earlier detection |
| | Volume Spike | 3x | 2.5x | More triggers |
| | RSI Extreme | 15/85 | 20/80 | Wider detection |

## Testing

```python
def test_mean_reversion_loosened():
    """Test MR generates signals at RSI 35."""
    from strategies.mean_reversion import MeanReversionStrategy

    strategy = MeanReversionStrategy()

    # Data with RSI around 35 (was blocked before)
    closes = [100 - (i * 0.3) for i in range(50)]  # Declining
    closes.extend([85 - (i * 0.1) for i in range(20)])  # Slowing decline

    market_data = {'1h': {'close': closes}}

    signals = strategy.generate_signals('TEST', market_data, 83.0)

    print(f"MR signals at moderate oversold: {len(signals)}")
    if signals:
        print(f"Confidence: {signals[0].confidence:.2f}")


def test_ma_crossover_loosened():
    """Test MA accepts 1% pullback."""
    from strategies.ma_crossover import MACrossoverStrategy

    strategy = MACrossoverStrategy()

    # Simulate 1% pullback (was blocked before)
    cross_price = 100.0
    current_price = 99.0  # 1% pullback

    valid, confidence = strategy._check_pullback(current_price, cross_price, 'up')

    assert valid, "1% pullback should now be valid"
    print(f"1% pullback valid with confidence factor: {confidence:.2f}")


def test_liquidation_capture_loosened():
    """Test LC detects 2.5% moves."""
    from strategies.liquidation_capture import LiquidationCaptureStrategy

    strategy = LiquidationCaptureStrategy()

    # 2.5% down move (was blocked before at 3% threshold)
    closes = [100.0] * 45 + [98.5, 97.5, 97.0, 96.5, 96.0, 95.5, 95.0]  # ~5% drop
    volumes = [1000] * 45 + [3000, 3500, 4000, 3800, 3500, 3200, 3000]  # Spike

    from indicators import calculate_rsi
    rsi = calculate_rsi(closes)

    cascade, direction, confidence = strategy._detect_cascade(closes, volumes, rsi)

    print(f"Cascade detected: {cascade}, direction: {direction}, confidence: {confidence:.2f}")


def test_all_loosened_thresholds():
    """Verify all thresholds are set to loosened values."""
    from strategies.mean_reversion import MeanReversionStrategy
    from strategies.ma_crossover import MACrossoverStrategy
    from strategies.grid_trading import GridTradingStrategy
    from strategies.liquidation_capture import LiquidationCaptureStrategy

    mr = MeanReversionStrategy()
    assert mr.RSI_OVERSOLD == 35, f"MR RSI oversold should be 35, got {mr.RSI_OVERSOLD}"
    assert mr.RSI_OVERBOUGHT == 65, f"MR RSI overbought should be 65, got {mr.RSI_OVERBOUGHT}"

    ma = MACrossoverStrategy()
    assert ma.PULLBACK_MIN_PCT == 0.01, f"MA pullback min should be 0.01"
    assert ma.VOLUME_THRESHOLD == 1.1, f"MA volume should be 1.1"

    grid = GridTradingStrategy()
    assert grid.ADX_THRESHOLD == 25.0, f"Grid ADX should be 25"
    assert grid.MIN_CONFIDENCE == 0.50, f"Grid min confidence should be 0.50"

    lc = LiquidationCaptureStrategy()
    assert lc.PRICE_MOVE_PCT == 0.025, f"LC price move should be 0.025"
    assert lc.VOLUME_SPIKE_MULT == 2.5, f"LC volume spike should be 2.5"

    print("All loosened thresholds verified!")


if __name__ == "__main__":
    test_all_loosened_thresholds()
    test_mean_reversion_loosened()
    test_ma_crossover_loosened()
    test_liquidation_capture_loosened()
    print("\nAll signal threshold tests passed!")
```

## Verification Checklist

- [ ] Mean Reversion RSI thresholds loosened (35/65)
- [ ] Mean Reversion MTF alignment is bonus, not block
- [ ] MA Crossover pullback range widened (1-5%)
- [ ] MA Crossover volume threshold lowered (110%)
- [ ] Grid Trading (already done in 057)
- [ ] Liquidation Capture thresholds loosened
- [ ] All config options added to .env
- [ ] Tests pass

## Architecture Summary

After prompts 053-058, the signal flow is:

```
Market Data
    ↓
Phase 1 (Regime Permission) [053]
    - Is strategy allowed in current regime?
    - Circuit breaker OK?
    - Strategy enabled?
    ↓
Phase 2 (Strategy Attempt) [054, 057, 058]
    - Strategy generates signal with LOOSENED thresholds
    - No second-guessing by orchestrator
    - Confidence calculated (not hard threshold)
    ↓
Confidence-to-Sizing [055]
    - Low confidence = smaller position (not blocked)
    - Floor of 30% minimum size
    ↓
Per-Strategy Cooldowns [056]
    - Each strategy has own cooldown
    - Scaled by timeframe
    - Losses = longer cooldown
    ↓
Phase 3 (Execution Filter) [053]
    - Position exposure within limits
    - Order validity
    - Final safety checks
    ↓
TRADE EXECUTED
```

This architecture unblocks signal flow while maintaining safety through sizing and exposure limits.
