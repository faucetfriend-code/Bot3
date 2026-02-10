"""
Detailed risk management parameters and validation rules.
"""

from dataclasses import dataclass
from typing import Dict, Tuple
from enum import Enum


class AssetClass(Enum):
    BTC_ETH = "btc_eth"
    LARGE_CAP = "large_cap_alts"
    SMALL_CAP = "small_cap_meme"


class TradeQuality(Enum):
    STANDARD = "standard"
    HIGH_CONVICTION = "high_conviction"
    EXCEPTIONAL = "exceptional"


@dataclass
class PositionSizingParams:
    """Position sizing parameters based on trade quality."""

    margin_allocation: float
    typical_leverage: int
    max_account_risk: float

    def validate(self, account_balance: float) -> bool:
        """Validate position sizing parameters."""
        if self.margin_allocation > account_balance * 0.20:
            return False
        if self.typical_leverage < 10 or self.typical_leverage > 20:
            return False
        if self.max_account_risk > 0.15:
            return False
        return True


# Position Sizing by Trade Quality
POSITION_SIZING_MAP: Dict[TradeQuality, PositionSizingParams] = {
    TradeQuality.STANDARD: PositionSizingParams(
        margin_allocation=100.0, typical_leverage=15, max_account_risk=0.07
    ),
    TradeQuality.HIGH_CONVICTION: PositionSizingParams(
        margin_allocation=150.0, typical_leverage=15, max_account_risk=0.11
    ),
    TradeQuality.EXCEPTIONAL: PositionSizingParams(
        margin_allocation=200.0, typical_leverage=15, max_account_risk=0.15
    ),
}


@dataclass
class StopLossParams:
    """Stop-loss parameters by asset class."""

    min_stop: float
    optimal_min: float
    optimal_max: float
    max_stop: float

    def is_valid_stop(self, stop_distance: float) -> bool:
        """Check if stop distance is within acceptable range."""
        return self.min_stop <= stop_distance <= self.max_stop

    def is_optimal_stop(self, stop_distance: float) -> bool:
        """Check if stop distance is in optimal range."""
        return self.optimal_min <= stop_distance <= self.optimal_max


# Stop-Loss Parameters by Asset Class
STOP_LOSS_MAP: Dict[AssetClass, StopLossParams] = {
    AssetClass.BTC_ETH: StopLossParams(
        min_stop=0.025, optimal_min=0.03, optimal_max=0.04, max_stop=0.05
    ),
    AssetClass.LARGE_CAP: StopLossParams(
        min_stop=0.03, optimal_min=0.04, optimal_max=0.05, max_stop=0.06
    ),
    AssetClass.SMALL_CAP: StopLossParams(
        min_stop=0.04, optimal_min=0.05, optimal_max=0.06, max_stop=0.07
    ),
}


@dataclass
class LiquidationBuffer:
    """Liquidation buffer calculations."""

    leverage: int
    stop_distance: float

    @property
    def liquidation_distance(self) -> float:
        """Calculate liquidation distance based on leverage."""
        return 1.0 / self.leverage

    @property
    def required_buffer(self) -> float:
        """Calculate required liquidation buffer (1.5x stop)."""
        return self.stop_distance * 1.5

    @property
    def actual_buffer(self) -> float:
        """Calculate actual buffer to liquidation."""
        return self.liquidation_distance - self.stop_distance

    def is_safe(self) -> bool:
        """Check if liquidation buffer is adequate."""
        return self.actual_buffer >= self.required_buffer

    @property
    def buffer_multiplier(self) -> float:
        """Calculate buffer as multiple of stop distance."""
        if self.stop_distance == 0:
            return 0
        return self.actual_buffer / self.stop_distance


def calculate_max_safe_leverage(
    stop_distance: float, buffer_multiplier: float = 1.5
) -> int:
    """
    Calculate maximum safe leverage for given stop distance.

    Args:
        stop_distance: Stop-loss distance as decimal (e.g., 0.04 for 4%)
        buffer_multiplier: Required buffer multiplier (default 1.5)

    Returns:
        Maximum safe leverage as integer
    """
    min_liquidation_distance = stop_distance * buffer_multiplier
    max_leverage = int(1.0 / min_liquidation_distance)
    return min(max_leverage, 20)  # Cap at 20x


def calculate_position_value(margin: float, leverage: int) -> float:
    """Calculate position value from margin and leverage."""
    return margin * leverage


def calculate_account_risk(
    position_value: float, stop_distance: float, account_balance: float
) -> float:
    """
    Calculate actual account risk percentage.

    Returns:
        Risk as decimal (e.g., 0.05 for 5%)
    """
    dollar_risk = position_value * stop_distance
    return dollar_risk / account_balance


def calculate_margin_drawdown(
    position_value: float, stop_distance: float, margin: float
) -> float:
    """
    Calculate margin drawdown if stop is hit.

    Returns:
        Drawdown as decimal (e.g., 0.50 for 50%)
    """
    dollar_loss = position_value * stop_distance
    return dollar_loss / margin


@dataclass
class RiskValidation:
    """Complete risk validation result."""

    is_valid: bool
    stop_in_range: bool
    stop_optimal: bool
    liquidation_safe: bool
    account_risk_ok: bool
    margin_drawdown_ok: bool
    errors: list
    warnings: list


def validate_trade_risk(
    asset_class: AssetClass,
    trade_quality: TradeQuality,
    entry_price: float,
    stop_price: float,
    leverage: int,
    account_balance: float,
) -> RiskValidation:
    """
    Comprehensive risk validation for a trade.

    Returns:
        RiskValidation object with full assessment
    """
    errors = []
    warnings = []

    # Get parameters
    stop_params = STOP_LOSS_MAP[asset_class]
    sizing_params = POSITION_SIZING_MAP[trade_quality]

    # Calculate distances
    stop_distance = abs(entry_price - stop_price) / entry_price

    # Validate stop distance
    stop_in_range = stop_params.is_valid_stop(stop_distance)
    stop_optimal = stop_params.is_optimal_stop(stop_distance)

    if not stop_in_range:
        errors.append(
            f"Stop distance {stop_distance:.2%} outside valid range "
            f"[{stop_params.min_stop:.2%}, {stop_params.max_stop:.2%}]"
        )
    elif not stop_optimal:
        warnings.append(
            f"Stop distance {stop_distance:.2%} outside optimal range "
            f"[{stop_params.optimal_min:.2%}, {stop_params.optimal_max:.2%}]"
        )

    # Calculate position values
    margin = sizing_params.margin_allocation
    position_value = calculate_position_value(margin, leverage)

    # Validate liquidation buffer
    liq_buffer = LiquidationBuffer(leverage, stop_distance)
    liquidation_safe = liq_buffer.is_safe()

    if not liquidation_safe:
        errors.append(
            f"Liquidation buffer {liq_buffer.buffer_multiplier:.2f}x "
            f"below required 1.5x minimum"
        )

    # Validate account risk
    account_risk = calculate_account_risk(
        position_value, stop_distance, account_balance
    )
    account_risk_ok = account_risk <= 0.08  # 8% max

    if not account_risk_ok:
        errors.append(f"Account risk {account_risk:.2%} exceeds 8% maximum")
    elif account_risk > sizing_params.max_account_risk:
        warnings.append(
            f"Account risk {account_risk:.2%} above "
            f"{sizing_params.max_account_risk:.2%} for trade quality"
        )

    # Validate margin drawdown
    margin_drawdown = calculate_margin_drawdown(position_value, stop_distance, margin)
    margin_drawdown_ok = margin_drawdown <= 0.80  # 80% max

    if not margin_drawdown_ok:
        errors.append(f"Margin drawdown {margin_drawdown:.2%} exceeds 80% maximum")

    # Overall validation
    is_valid = (
        stop_in_range and liquidation_safe and account_risk_ok and margin_drawdown_ok
    )

    return RiskValidation(
        is_valid=is_valid,
        stop_in_range=stop_in_range,
        stop_optimal=stop_optimal,
        liquidation_safe=liquidation_safe,
        account_risk_ok=account_risk_ok,
        margin_drawdown_ok=margin_drawdown_ok,
        errors=errors,
        warnings=warnings,
    )


# Example usage and test cases
if __name__ == "__main__":
    # Test case 1: Conservative BTC trade
    validation = validate_trade_risk(
        asset_class=AssetClass.BTC_ETH,
        trade_quality=TradeQuality.STANDARD,
        entry_price=45000,
        stop_price=43600,
        leverage=15,
        account_balance=1000,
    )

    print("Test Case 1: Conservative BTC Trade")
    print(f"Valid: {validation.is_valid}")
    print(f"Errors: {validation.errors}")
    print(f"Warnings: {validation.warnings}")
    print()

    # Test case 2: Aggressive ETH trade (should have warnings)
    validation2 = validate_trade_risk(
        asset_class=AssetClass.BTC_ETH,
        trade_quality=TradeQuality.EXCEPTIONAL,
        entry_price=2500,
        stop_price=2390,
        leverage=15,
        account_balance=1000,
    )

    print("Test Case 2: Aggressive ETH Trade")
    print(f"Valid: {validation2.is_valid}")
    print(f"Errors: {validation2.errors}")
    print(f"Warnings: {validation2.warnings}")
