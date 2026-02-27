#!/usr/bin/env python3
"""
Pacifica.fi Validation Utilities

Comprehensive validation for Pacifica.fi orders, parameters, and market specifications.

⚠️ CRITICAL: Pacifica has strict requirements that differ from standard exchanges:
- Tick size: Price must be multiple of tick_size
- Lot size: Quantity must be multiple of lot_size
- Leverage: Must be within [min_leverage, max_leverage] for each market
- Margin modes: "cross" or "isolated"
- Hourly funding: 24 payments per day (not 8-hour intervals)
"""

from typing import Dict, List, Optional, Tuple, Any
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from dataclasses import dataclass
from enum import Enum
import re
from loguru import logger


class ValidationError(Exception):
    """Base exception for validation errors."""
    pass


class ValidationSeverity(Enum):
    """Validation error severity levels."""
    ERROR = "error"  # Blocks order submission
    WARNING = "warning"  # Allows submission but logs warning
    INFO = "info"  # Informational only


@dataclass
class ValidationResult:
    """Result of a validation check."""
    is_valid: bool
    severity: ValidationSeverity
    field: str
    message: str
    expected: Optional[Any] = None
    actual: Optional[Any] = None
    suggestion: Optional[str] = None

    def __str__(self) -> str:
        msg = f"[{self.severity.value.upper()}] {self.field}: {self.message}"
        if self.suggestion:
            msg += f" → {self.suggestion}"
        return msg


@dataclass
class MarketSpecs:
    """Market specifications for validation."""
    symbol: str
    tick_size: float  # Minimum price increment
    lot_size: float  # Minimum quantity increment
    min_size: float  # Minimum order size
    max_size: float  # Maximum order size
    min_leverage: int = 5
    max_leverage: int = 50
    base_currency: str = "BTC"
    quote_currency: str = "USD"


class PacificaValidator:
    """
    Comprehensive validation for Pacifica.fi orders and parameters.

    Validates:
    - Price tick size alignment
    - Quantity lot size alignment
    - Order size limits
    - Leverage limits
    - Margin modes
    - Symbol formats
    - Time-in-force values
    """

    # Valid margin modes
    VALID_MARGIN_MODES = {"cross", "isolated"}

    # Valid order sides
    VALID_ORDER_SIDES = {"buy", "sell", "long", "short"}

    # Valid order types
    VALID_ORDER_TYPES = {"market", "limit", "stop", "stop_limit"}

    # Valid time-in-force values
    VALID_TIME_IN_FORCE = {"GTC", "IOC", "FOK"}

    # Symbol format regex (e.g., BTC-PERP, ETH-PERP)
    SYMBOL_PATTERN = re.compile(r"^[A-Z]{2,10}-(PERP|USD|USDT)$")

    def __init__(self, market_specs: Optional[Dict[str, MarketSpecs]] = None):
        """
        Initialize validator.

        Args:
            market_specs: Dictionary mapping symbols to MarketSpecs
        """
        self.market_specs = market_specs or {}
        logger.info(f"Initialized Pacifica validator with {len(self.market_specs)} markets")

    def add_market_specs(self, symbol: str, specs: MarketSpecs):
        """
        Add market specifications for a symbol.

        Args:
            symbol: Market symbol
            specs: Market specifications
        """
        self.market_specs[symbol] = specs
        logger.debug(f"Added market specs for {symbol}")

    def validate_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        size: float,
        price: Optional[float] = None,
        leverage: Optional[int] = None,
        margin_mode: str = "cross",
        reduce_only: bool = False,
        time_in_force: str = "GTC",
    ) -> Tuple[bool, List[ValidationResult]]:
        """
        Validate complete order before submission.

        Args:
            symbol: Market symbol
            side: Order side (buy/sell/long/short)
            order_type: Order type (market/limit/stop/stop_limit)
            size: Order size
            price: Limit price (required for limit/stop_limit orders)
            leverage: Leverage multiplier
            margin_mode: Margin mode (cross/isolated)
            reduce_only: Reduce-only flag
            time_in_force: Time-in-force (GTC/IOC/FOK)

        Returns:
            (is_valid, errors) tuple
        """
        errors = []

        # Symbol validation
        errors.extend(self.validate_symbol(symbol))

        # Side validation
        errors.extend(self.validate_side(side))

        # Order type validation
        errors.extend(self.validate_order_type(order_type))

        # Size validation
        if symbol in self.market_specs:
            specs = self.market_specs[symbol]
            errors.extend(self.validate_size(size, specs))

        # Price validation (for limit orders)
        if order_type in ["limit", "stop_limit"]:
            if price is None:
                errors.append(
                    ValidationResult(
                        is_valid=False,
                        severity=ValidationSeverity.ERROR,
                        field="price",
                        message=f"Price required for {order_type} orders",
                        actual=None,
                        expected="float > 0",
                    )
                )
            elif symbol in self.market_specs:
                specs = self.market_specs[symbol]
                errors.extend(self.validate_price(price, specs))

        # Leverage validation
        if leverage is not None and symbol in self.market_specs:
            specs = self.market_specs[symbol]
            errors.extend(self.validate_leverage(leverage, specs))

        # Margin mode validation
        errors.extend(self.validate_margin_mode(margin_mode))

        # Time-in-force validation
        errors.extend(self.validate_time_in_force(time_in_force))

        # Check if any errors are severity ERROR
        has_errors = any(e.severity == ValidationSeverity.ERROR for e in errors)

        return not has_errors, errors

    def validate_symbol(self, symbol: str) -> List[ValidationResult]:
        """
        Validate symbol format.

        Args:
            symbol: Market symbol to validate

        Returns:
            List of validation results
        """
        errors = []

        if not symbol:
            errors.append(
                ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.ERROR,
                    field="symbol",
                    message="Symbol cannot be empty",
                    actual=symbol,
                )
            )
            return errors

        if not self.SYMBOL_PATTERN.match(symbol):
            errors.append(
                ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.ERROR,
                    field="symbol",
                    message="Invalid symbol format",
                    actual=symbol,
                    expected="Format: XXX-PERP (e.g., BTC-PERP)",
                    suggestion="Use format like BTC-PERP, ETH-PERP",
                )
            )

        return errors

    def validate_side(self, side: str) -> List[ValidationResult]:
        """
        Validate order side.

        Args:
            side: Order side

        Returns:
            List of validation results
        """
        errors = []

        side_lower = side.lower() if side else ""
        if side_lower not in self.VALID_ORDER_SIDES:
            errors.append(
                ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.ERROR,
                    field="side",
                    message="Invalid order side",
                    actual=side,
                    expected=", ".join(self.VALID_ORDER_SIDES),
                )
            )

        return errors

    def validate_order_type(self, order_type: str) -> List[ValidationResult]:
        """
        Validate order type.

        Args:
            order_type: Order type

        Returns:
            List of validation results
        """
        errors = []

        order_type_lower = order_type.lower() if order_type else ""
        if order_type_lower not in self.VALID_ORDER_TYPES:
            errors.append(
                ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.ERROR,
                    field="order_type",
                    message="Invalid order type",
                    actual=order_type,
                    expected=", ".join(self.VALID_ORDER_TYPES),
                )
            )

        return errors

    def validate_price(
        self, price: float, specs: MarketSpecs
    ) -> List[ValidationResult]:
        """
        Validate price meets tick size requirements.

        Args:
            price: Order price
            specs: Market specifications

        Returns:
            List of validation results
        """
        errors = []

        if price <= 0:
            errors.append(
                ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.ERROR,
                    field="price",
                    message="Price must be positive",
                    actual=price,
                    expected="> 0",
                )
            )
            return errors

        # Check tick size alignment
        tick_size = specs.tick_size
        remainder = Decimal(str(price)) % Decimal(str(tick_size))

        if remainder != 0:
            # Calculate nearest valid prices
            rounded_down = self.round_price_down(price, tick_size)
            rounded_up = self.round_price_up(price, tick_size)

            errors.append(
                ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.ERROR,
                    field="price",
                    message=f"Price must be multiple of tick size {tick_size}",
                    actual=price,
                    expected=f"Multiple of {tick_size}",
                    suggestion=f"Use {rounded_down} or {rounded_up}",
                )
            )

        return errors

    def validate_size(
        self, size: float, specs: MarketSpecs
    ) -> List[ValidationResult]:
        """
        Validate order size meets lot size and limit requirements.

        Args:
            size: Order size
            specs: Market specifications

        Returns:
            List of validation results
        """
        errors = []

        if size <= 0:
            errors.append(
                ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.ERROR,
                    field="size",
                    message="Size must be positive",
                    actual=size,
                    expected="> 0",
                )
            )
            return errors

        # Check lot size alignment
        lot_size = specs.lot_size
        remainder = Decimal(str(size)) % Decimal(str(lot_size))

        if remainder != 0:
            rounded_down = self.round_size_down(size, lot_size)
            rounded_up = self.round_size_up(size, lot_size)

            errors.append(
                ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.ERROR,
                    field="size",
                    message=f"Size must be multiple of lot size {lot_size}",
                    actual=size,
                    expected=f"Multiple of {lot_size}",
                    suggestion=f"Use {rounded_down} or {rounded_up}",
                )
            )

        # Check minimum size
        if size < specs.min_size:
            errors.append(
                ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.ERROR,
                    field="size",
                    message=f"Size below minimum {specs.min_size}",
                    actual=size,
                    expected=f">= {specs.min_size}",
                )
            )

        # Check maximum size
        if size > specs.max_size:
            errors.append(
                ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.ERROR,
                    field="size",
                    message=f"Size exceeds maximum {specs.max_size}",
                    actual=size,
                    expected=f"<= {specs.max_size}",
                )
            )

        return errors

    def validate_leverage(
        self, leverage: int, specs: MarketSpecs
    ) -> List[ValidationResult]:
        """
        Validate leverage is within allowed range.

        Args:
            leverage: Leverage multiplier
            specs: Market specifications

        Returns:
            List of validation results
        """
        errors = []

        if leverage < specs.min_leverage:
            errors.append(
                ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.ERROR,
                    field="leverage",
                    message=f"Leverage below minimum {specs.min_leverage}x",
                    actual=leverage,
                    expected=f"[{specs.min_leverage}, {specs.max_leverage}]",
                )
            )

        if leverage > specs.max_leverage:
            errors.append(
                ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.ERROR,
                    field="leverage",
                    message=f"Leverage exceeds maximum {specs.max_leverage}x",
                    actual=leverage,
                    expected=f"[{specs.min_leverage}, {specs.max_leverage}]",
                )
            )

        return errors

    def validate_margin_mode(self, margin_mode: str) -> List[ValidationResult]:
        """
        Validate margin mode.

        Args:
            margin_mode: Margin mode (cross/isolated)

        Returns:
            List of validation results
        """
        errors = []

        if margin_mode.lower() not in self.VALID_MARGIN_MODES:
            errors.append(
                ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.ERROR,
                    field="margin_mode",
                    message="Invalid margin mode",
                    actual=margin_mode,
                    expected=", ".join(self.VALID_MARGIN_MODES),
                )
            )

        return errors

    def validate_time_in_force(self, time_in_force: str) -> List[ValidationResult]:
        """
        Validate time-in-force value.

        Args:
            time_in_force: Time-in-force (GTC/IOC/FOK)

        Returns:
            List of validation results
        """
        errors = []

        if time_in_force.upper() not in self.VALID_TIME_IN_FORCE:
            errors.append(
                ValidationResult(
                    is_valid=False,
                    severity=ValidationSeverity.ERROR,
                    field="time_in_force",
                    message="Invalid time-in-force",
                    actual=time_in_force,
                    expected=", ".join(self.VALID_TIME_IN_FORCE),
                )
            )

        return errors

    @staticmethod
    def round_price_down(price: float, tick_size: float) -> float:
        """
        Round price down to nearest valid tick.

        Args:
            price: Price to round
            tick_size: Tick size

        Returns:
            Rounded price
        """
        price_decimal = Decimal(str(price))
        tick_decimal = Decimal(str(tick_size))
        rounded = (price_decimal // tick_decimal) * tick_decimal
        return float(rounded)

    @staticmethod
    def round_price_up(price: float, tick_size: float) -> float:
        """
        Round price up to nearest valid tick.

        Args:
            price: Price to round
            tick_size: Tick size

        Returns:
            Rounded price
        """
        price_decimal = Decimal(str(price))
        tick_decimal = Decimal(str(tick_size))
        rounded = ((price_decimal + tick_decimal - Decimal('0.000001')) // tick_decimal) * tick_decimal
        return float(rounded)

    @staticmethod
    def round_size_down(size: float, lot_size: float) -> float:
        """
        Round size down to nearest valid lot.

        Args:
            size: Size to round
            lot_size: Lot size

        Returns:
            Rounded size
        """
        size_decimal = Decimal(str(size))
        lot_decimal = Decimal(str(lot_size))
        rounded = (size_decimal // lot_decimal) * lot_decimal
        return float(rounded)

    @staticmethod
    def round_size_up(size: float, lot_size: float) -> float:
        """
        Round size up to nearest valid lot.

        Args:
            size: Size to round
            lot_size: Lot size

        Returns:
            Rounded size
        """
        size_decimal = Decimal(str(size))
        lot_decimal = Decimal(str(lot_size))
        rounded = ((size_decimal + lot_decimal - Decimal('0.000001')) // lot_decimal) * lot_decimal
        return float(rounded)

    @staticmethod
    def calculate_notional_value(size: float, price: float) -> float:
        """
        Calculate notional value of position.

        Args:
            size: Position size
            price: Entry price

        Returns:
            Notional value (size * price)
        """
        return size * price

    @staticmethod
    def calculate_required_margin(
        size: float, price: float, leverage: int
    ) -> float:
        """
        Calculate required margin for a position.

        Args:
            size: Position size
            price: Entry price
            leverage: Leverage multiplier

        Returns:
            Required margin
        """
        notional_value = size * price
        return notional_value / leverage

    def get_market_specs(self, symbol: str) -> Optional[MarketSpecs]:
        """
        Get market specifications for a symbol.

        Args:
            symbol: Market symbol

        Returns:
            MarketSpecs or None if not found
        """
        return self.market_specs.get(symbol)

    def print_validation_errors(self, errors: List[ValidationResult]):
        """
        Print validation errors in formatted way.

        Args:
            errors: List of validation results
        """
        if not errors:
            logger.info("✅ Validation passed")
            return

        logger.warning(f"Validation found {len(errors)} issues:")
        for error in errors:
            if error.severity == ValidationSeverity.ERROR:
                logger.error(f"  ❌ {error}")
            elif error.severity == ValidationSeverity.WARNING:
                logger.warning(f"  ⚠️  {error}")
            else:
                logger.info(f"  ℹ️  {error}")


# Factory function
def create_pacifica_validator(
    market_specs_dict: Optional[Dict[str, Dict[str, Any]]] = None
) -> PacificaValidator:
    """
    Create Pacifica validator with market specifications.

    Args:
        market_specs_dict: Dictionary of market specifications

    Returns:
        PacificaValidator instance
    """
    validator = PacificaValidator()

    if market_specs_dict:
        for symbol, specs_data in market_specs_dict.items():
            specs = MarketSpecs(
                symbol=symbol,
                tick_size=specs_data.get("tick_size", 0.5),
                lot_size=specs_data.get("lot_size", 0.001),
                min_size=specs_data.get("min_size", 0.001),
                max_size=specs_data.get("max_size", 100.0),
                min_leverage=specs_data.get("min_leverage", 5),
                max_leverage=specs_data.get("max_leverage", 50),
                base_currency=specs_data.get("base_currency", "BTC"),
                quote_currency=specs_data.get("quote_currency", "USD"),
            )
            validator.add_market_specs(symbol, specs)

    return validator


# Example usage
if __name__ == "__main__":
    # Create validator with BTC-PERP specs
    market_specs = {
        "BTC-PERP": {
            "tick_size": 0.5,
            "lot_size": 0.001,
            "min_size": 0.001,
            "max_size": 100.0,
            "min_leverage": 5,
            "max_leverage": 50,
        }
    }

    validator = create_pacifica_validator(market_specs)

    # Test valid order
    print("\n=== Testing VALID order ===")
    is_valid, errors = validator.validate_order(
        symbol="BTC-PERP",
        side="buy",
        order_type="limit",
        size=0.01,  # Valid: multiple of 0.001
        price=50000.0,  # Valid: multiple of 0.5
        leverage=10,
        margin_mode="cross",
    )
    print(f"Valid: {is_valid}")
    validator.print_validation_errors(errors)

    # Test invalid price (not aligned to tick size)
    print("\n=== Testing INVALID price ===")
    is_valid, errors = validator.validate_order(
        symbol="BTC-PERP",
        side="buy",
        order_type="limit",
        size=0.01,
        price=50000.25,  # Invalid: not multiple of 0.5
        leverage=10,
    )
    print(f"Valid: {is_valid}")
    validator.print_validation_errors(errors)

    # Test invalid size (not aligned to lot size)
    print("\n=== Testing INVALID size ===")
    is_valid, errors = validator.validate_order(
        symbol="BTC-PERP",
        side="sell",
        order_type="market",
        size=0.0123,  # Invalid: not multiple of 0.001
        leverage=20,
    )
    print(f"Valid: {is_valid}")
    validator.print_validation_errors(errors)

    # Test rounding functions
    print("\n=== Testing rounding utilities ===")
    price = 50123.75
    tick_size = 0.5
    print(f"Original price: ${price}")
    print(f"Rounded down: ${validator.round_price_down(price, tick_size)}")
    print(f"Rounded up: ${validator.round_price_up(price, tick_size)}")
