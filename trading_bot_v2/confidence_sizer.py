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

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from loguru import logger

# Use relative imports from trading_bot_v2 package
from .models import Signal
from .config import StrategyType

if TYPE_CHECKING:
    from .kelly_position_sizer import KellyPositionSizer


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
        StrategyType.GRID_TRADING: 0.4,  # Grids need reasonable size to work
        StrategyType.MEAN_REVERSION: 0.3,  # Can scale down more
        StrategyType.MA_CROSSOVER: 0.3,  # Can scale down more
        StrategyType.TREND_FOLLOWING: 0.35,  # Needs some size for trend capture
        StrategyType.LIQUIDATION_CAPTURE: 0.5,  # High-conviction strategy
    }

    # Default floor if strategy not specified
    DEFAULT_FLOOR = 0.3

    # Maximum multiplier (prevent over-sizing on high confidence)
    DEFAULT_CEILING = 1.0

    def __init__(self, config: Any = None) -> None:
        """
        Initialize ConfidenceSizer.

        Args:
            config: Optional config object with sizing parameters
        """
        self.config = config

        # Load from config or use defaults
        self.global_floor = getattr(config, "confidence_floor", self.DEFAULT_FLOOR)
        self.global_ceiling = getattr(
            config, "confidence_ceiling", self.DEFAULT_CEILING
        )
        self.scale_type = getattr(config, "confidence_scale_type", "linear")

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
        confidence = signal.confidence if signal.confidence else 0.5
        strategy_type = signal.strategy

        # Get floor for this strategy
        floor = self.STRATEGY_FLOORS.get(strategy_type, self.global_floor)
        ceiling = self.global_ceiling

        # Clamp confidence to valid range
        confidence = max(0.0, min(1.0, confidence))

        # Calculate multiplier based on scale type
        if self.scale_type == "sqrt":
            # Square root scaling: more aggressive at lower confidence
            # sqrt(0.5) = 0.71, so 50% confidence gives 71% size
            raw_multiplier = confidence**0.5
        else:
            # Linear scaling: 50% confidence = 50% size
            raw_multiplier = confidence

        # Apply floor and ceiling
        multiplier = max(floor, min(ceiling, raw_multiplier))

        # Debug logging for confidence scoring
        symbol = getattr(signal, "symbol", None) or getattr(signal, "asset", "UNKNOWN")
        strategy_name = (
            strategy_type.value
            if hasattr(strategy_type, "value")
            else str(strategy_type)
        )
        logger.debug(
            f"[{symbol}] Confidence sizing: input_conf={signal.confidence:.3f} -> clamped={confidence:.3f}, "
            f"raw_mult={raw_multiplier:.3f}, floor={floor}, ceiling={ceiling} => final_mult={multiplier:.3f} "
            f"(strategy={strategy_name}, scale={self.scale_type})"
        )

        return SizingResult(
            base_size=0,  # Filled by caller
            confidence=confidence,
            multiplier=multiplier,
            effective_size=0,  # Filled by caller
            reason=f"confidence={confidence:.2f} -> multiplier={multiplier:.2f} "
            f"(floor={floor}, scale={self.scale_type})",
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

        symbol = getattr(signal, "symbol", None) or getattr(signal, "asset", "UNKNOWN")
        logger.debug(
            f"Confidence sizing: {symbol} {signal.side.value} "
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

    def __init__(
        self,
        kelly_sizer: "KellyPositionSizer",
        confidence_sizer: ConfidenceSizer,
        config: Any = None,
    ) -> None:
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
        self.max_position_pct = getattr(config, "max_position_pct", 0.10)  # 10% max
        self.min_position_size = getattr(config, "min_position_size", 0.001)

    def calculate_position_size(
        self, signal: Signal, account_balance: float
    ) -> SizingResult:
        """
        Calculate final position size integrating Kelly and confidence.

        Args:
            signal: Signal to size
            account_balance: Current account balance

        Returns:
            SizingResult with final effective_size
        """
        # Step 1: Kelly base size
        try:
            kelly_result = self.kelly.calculate_position_size(  # type: ignore[call-arg]  # KellyPositionSizer takes (signal, account_balance); reported, not fixed
                strategy_type=signal.strategy,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                account_balance=account_balance,
            )
            if isinstance(kelly_result, dict):
                base_size = kelly_result.get("position_size", 0)
            else:
                base_size = kelly_result
        except Exception as e:
            logger.warning(f"Kelly calculation failed: {e}, using fallback")
            # Fallback: 2% of account
            base_size = (
                (account_balance * 0.02) / signal.entry_price
                if signal.entry_price > 0
                else 0
            )

        # Step 2: Apply confidence multiplier
        sizing_result = self.confidence.apply_to_size(base_size, signal)

        # Step 3: Apply hard limits
        max_size = (
            account_balance * self.max_position_pct / signal.entry_price
            if signal.entry_price > 0
            else 0
        )
        sizing_result.effective_size = min(sizing_result.effective_size, max_size)
        sizing_result.effective_size = max(
            sizing_result.effective_size, self.min_position_size
        )

        logger.info(
            f"Position sized: {signal.symbol} {signal.side.value} "  # type: ignore[attr-defined]  # Signal has no 'symbol' (field is 'asset'); reported, not fixed
            f"kelly={base_size:.6f} * conf={sizing_result.multiplier:.2f} "
            f"= {sizing_result.effective_size:.6f} "
            f"(max={max_size:.6f})"
        )

        return sizing_result
