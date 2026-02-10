# Prompt 055: Confidence-to-Sizing Transformation

## Context
With over-confirmation removed (Prompt 054), we now implement the key insight: confidence should affect SIZING, not PERMISSION.

## Problem
Previously, confidence was used as a kill-switch:
- `if signal.confidence < 0.6: continue` - Signal blocked entirely
- Good setups with 0.55 confidence → zero trades
- No way to take smaller positions on uncertain setups

## Solution
Transform confidence from gate to multiplier:
- Confidence 1.0 = 100% of calculated position size
- Confidence 0.5 = 50% of calculated position size
- Confidence 0.3 = 30% of calculated position size (minimum floor)
- NO signal is blocked for low confidence alone

## Implementation

### File: `trading_bot_v2/confidence_sizer.py` (NEW)

```python
"""
Confidence-to-Sizing Transformer
================================

Converts signal confidence to position size multiplier.

Key principle: Confidence affects SIZE, not PERMISSION.

A signal with 0.4 confidence is still valid - we just trade smaller.
This allows the bot to participate in uncertain markets with reduced risk,
rather than sitting out entirely.

Formula:
    effective_size = base_size * confidence_multiplier(signal.confidence)

Where confidence_multiplier is:
    - Floor of 0.3 (always take at least 30% of base size)
    - Linear scale from floor to 1.0
    - Optional ceiling for very high confidence (prevent over-betting)
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass
from loguru import logger

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "Example files", "core_logic"))
from models import Signal
from config import StrategyType


@dataclass
class SizingResult:
    """Result of confidence-based sizing calculation."""
    base_size: float
    confidence: float
    multiplier: float
    effective_size: float
    reason: str


class ConfidenceSizer:
    """
    Transforms confidence into position size multiplier.

    Configuration:
        confidence_floor: Minimum multiplier (default 0.3)
        confidence_ceiling: Maximum multiplier (default 1.0)
        scale_type: 'linear' or 'sqrt' (sqrt = more size at lower confidence)
    """

    # Strategy-specific confidence floors
    # Some strategies should be more aggressive at low confidence
    STRATEGY_FLOORS = {
        StrategyType.GRID_TRADING: 0.4,      # Grids need reasonable size to work
        StrategyType.MEAN_REVERSION: 0.3,    # Can scale down more
        StrategyType.MA_CROSSOVER: 0.3,      # Can scale down more
        StrategyType.TREND_FOLLOWING: 0.35,  # Needs some size for trend capture
        StrategyType.LIQUIDATION_CAPTURE: 0.5,  # High-conviction strategy
    }

    # Default floor if strategy not specified
    DEFAULT_FLOOR = 0.3

    # Maximum multiplier (prevent over-sizing on high confidence)
    DEFAULT_CEILING = 1.0

    def __init__(self, config=None):
        """
        Initialize ConfidenceSizer.

        Args:
            config: Optional config object with sizing parameters
        """
        self.config = config

        # Load from config or use defaults
        self.global_floor = getattr(config, 'confidence_floor', self.DEFAULT_FLOOR)
        self.global_ceiling = getattr(config, 'confidence_ceiling', self.DEFAULT_CEILING)
        self.scale_type = getattr(config, 'confidence_scale_type', 'linear')

        logger.info(
            f"ConfidenceSizer initialized: floor={self.global_floor}, "
            f"ceiling={self.global_ceiling}, scale={self.scale_type}"
        )

    def calculate_multiplier(self, signal: Signal) -> SizingResult:
        """
        Calculate size multiplier from signal confidence.

        Args:
            signal: Signal with confidence value (0-1)

        Returns:
            SizingResult with multiplier and explanation
        """
        confidence = signal.confidence
        strategy_type = signal.strategy

        # Get floor for this strategy
        floor = self.STRATEGY_FLOORS.get(strategy_type, self.global_floor)
        ceiling = self.global_ceiling

        # Clamp confidence to valid range
        confidence = max(0.0, min(1.0, confidence))

        # Calculate multiplier based on scale type
        if self.scale_type == 'sqrt':
            # Square root scaling: more aggressive at lower confidence
            # sqrt(0.5) = 0.71, so 50% confidence gives 71% size
            raw_multiplier = confidence ** 0.5
        else:
            # Linear scaling: 50% confidence = 50% size
            raw_multiplier = confidence

        # Apply floor and ceiling
        multiplier = max(floor, min(ceiling, raw_multiplier))

        return SizingResult(
            base_size=0,  # Filled by caller
            confidence=confidence,
            multiplier=multiplier,
            effective_size=0,  # Filled by caller
            reason=f"confidence={confidence:.2f} -> multiplier={multiplier:.2f} "
                   f"(floor={floor}, scale={self.scale_type})"
        )

    def apply_to_size(self, base_size: float, signal: Signal) -> SizingResult:
        """
        Apply confidence multiplier to base position size.

        Args:
            base_size: Position size from Kelly/fixed calculation
            signal: Signal with confidence

        Returns:
            SizingResult with effective_size
        """
        result = self.calculate_multiplier(signal)
        result.base_size = base_size
        result.effective_size = base_size * result.multiplier

        logger.debug(
            f"Confidence sizing: {signal.symbol} {signal.side.value} "
            f"base={base_size:.6f} * {result.multiplier:.2f} = {result.effective_size:.6f}"
        )

        return result


class IntegratedPositionSizer:
    """
    Combines Kelly Position Sizer with Confidence Sizer.

    Flow:
    1. Kelly calculates base size from strategy performance
    2. Confidence multiplier adjusts for signal quality
    3. Final size = Kelly size * confidence multiplier
    """

    def __init__(self, kelly_sizer, confidence_sizer, config=None):
        """
        Initialize integrated sizer.

        Args:
            kelly_sizer: KellyPositionSizer instance
            confidence_sizer: ConfidenceSizer instance
            config: Optional config
        """
        self.kelly = kelly_sizer
        self.confidence = confidence_sizer
        self.config = config

        # Hard limits (safety)
        self.max_position_pct = getattr(config, 'max_position_pct', 0.10)  # 10% max
        self.min_position_size = getattr(config, 'min_position_size', 0.001)

    def calculate_position_size(self, signal: Signal, account_balance: float) -> SizingResult:
        """
        Calculate final position size integrating Kelly and confidence.

        Args:
            signal: Signal to size
            account_balance: Current account balance

        Returns:
            SizingResult with final effective_size
        """
        # Step 1: Kelly base size
        kelly_result = self.kelly.calculate_position_size(
            strategy_type=signal.strategy,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            account_balance=account_balance
        )
        base_size = kelly_result.get('position_size', 0)

        # Step 2: Apply confidence multiplier
        sizing_result = self.confidence.apply_to_size(base_size, signal)

        # Step 3: Apply hard limits
        max_size = account_balance * self.max_position_pct / signal.entry_price
        sizing_result.effective_size = min(sizing_result.effective_size, max_size)
        sizing_result.effective_size = max(sizing_result.effective_size, self.min_position_size)

        logger.info(
            f"Position sized: {signal.symbol} {signal.side.value} "
            f"kelly={base_size:.6f} * conf={sizing_result.multiplier:.2f} "
            f"= {sizing_result.effective_size:.6f} "
            f"(max={max_size:.6f})"
        )

        return sizing_result
```

### Update `signal_phases.py` to Use ConfidenceSizer

In `SignalPipeline.process()`:

```python
# OLD (hardcoded multiplier)
confidence_multiplier = max(0.3, signal.confidence)
adjusted_size = base_size * confidence_multiplier

# NEW (use ConfidenceSizer)
from confidence_sizer import ConfidenceSizer

sizing_result = self.confidence_sizer.apply_to_size(base_size, signal)
adjusted_size = sizing_result.effective_size
```

### Configuration Options

Add to `.env`:

```bash
# Confidence-to-Sizing Configuration
CONFIDENCE_FLOOR=0.3          # Minimum multiplier (30%)
CONFIDENCE_CEILING=1.0        # Maximum multiplier (100%)
CONFIDENCE_SCALE_TYPE=linear  # 'linear' or 'sqrt'

# Strategy-specific floors (optional overrides)
GRID_CONFIDENCE_FLOOR=0.4
MEAN_REVERSION_CONFIDENCE_FLOOR=0.3
LIQUIDATION_CONFIDENCE_FLOOR=0.5
```

Add to `config.py`:

```python
# Confidence Sizing
self.confidence_floor = float(os.getenv('CONFIDENCE_FLOOR', '0.3'))
self.confidence_ceiling = float(os.getenv('CONFIDENCE_CEILING', '1.0'))
self.confidence_scale_type = os.getenv('CONFIDENCE_SCALE_TYPE', 'linear')
```

## Behavior Examples

### Before (Kill-Switch)
```
Signal: BUY BTC @ $40,000, confidence=0.55
Result: BLOCKED (confidence < 0.6 threshold)
Position: $0
```

### After (Size Multiplier)
```
Signal: BUY BTC @ $40,000, confidence=0.55
Kelly base size: $2,000
Confidence multiplier: 0.55
Effective size: $2,000 * 0.55 = $1,100
Result: TRADE EXECUTED with $1,100 position

Signal: BUY ETH @ $2,500, confidence=0.35
Kelly base size: $1,500
Confidence multiplier: 0.35 -> 0.3 (floor)
Effective size: $1,500 * 0.3 = $450
Result: TRADE EXECUTED with $450 position (minimum viable)
```

## Testing

```python
def test_confidence_floor():
    """Test that confidence floor prevents zero-size trades."""
    from confidence_sizer import ConfidenceSizer
    from models import Signal, OrderSide
    from config import StrategyType

    sizer = ConfidenceSizer()

    # Create low-confidence signal
    signal = Signal(
        symbol='TEST',
        side=OrderSide.BUY,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        confidence=0.2,  # Very low
        strategy=StrategyType.MEAN_REVERSION
    )

    result = sizer.calculate_multiplier(signal)

    # Should hit floor of 0.3
    assert result.multiplier >= 0.3, f"Multiplier {result.multiplier} below floor"
    print(f"Low confidence (0.2) -> multiplier {result.multiplier} (floor applied)")


def test_confidence_linear_scale():
    """Test linear scaling of confidence to size."""
    from confidence_sizer import ConfidenceSizer
    from models import Signal, OrderSide
    from config import StrategyType

    sizer = ConfidenceSizer()

    confidences = [0.4, 0.6, 0.8, 1.0]

    for conf in confidences:
        signal = Signal(
            symbol='TEST',
            side=OrderSide.BUY,
            entry_price=100.0,
            confidence=conf,
            strategy=StrategyType.MEAN_REVERSION
        )
        result = sizer.calculate_multiplier(signal)
        print(f"Confidence {conf:.1f} -> multiplier {result.multiplier:.2f}")


def test_apply_to_base_size():
    """Test applying multiplier to base position size."""
    from confidence_sizer import ConfidenceSizer
    from models import Signal, OrderSide
    from config import StrategyType

    sizer = ConfidenceSizer()

    signal = Signal(
        symbol='TEST',
        side=OrderSide.BUY,
        entry_price=100.0,
        confidence=0.7,
        strategy=StrategyType.MEAN_REVERSION
    )

    base_size = 10.0  # 10 units
    result = sizer.apply_to_size(base_size, signal)

    expected = 10.0 * 0.7  # 7 units
    assert abs(result.effective_size - expected) < 0.01, \
        f"Expected {expected}, got {result.effective_size}"

    print(f"Base {base_size} * confidence 0.7 = {result.effective_size}")


if __name__ == "__main__":
    test_confidence_floor()
    test_confidence_linear_scale()
    test_apply_to_base_size()
    print("\nAll confidence sizing tests passed!")
```

## Verification Checklist

- [ ] `confidence_sizer.py` created
- [ ] ConfidenceSizer implements floor/ceiling/scaling
- [ ] Strategy-specific floors defined
- [ ] IntegratedPositionSizer combines Kelly + Confidence
- [ ] Configuration options in .env
- [ ] No signal blocked solely for low confidence
- [ ] Tests pass

## Next Prompt
Prompt 056 will implement per-strategy, per-timeframe cooldowns instead of global cooldowns.
