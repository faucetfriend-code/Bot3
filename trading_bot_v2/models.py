"""
Data structures and models for the trading bot.
All classes based on Trading Bot Custom Instructions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any, Union, Tuple
from enum import Enum
from .config import AssetClass, TradeQuality, StrategyType, MarketState


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"
    STOP_LIMIT = "stop_limit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class PositionStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    LIQUIDATED = "liquidated"


@dataclass
class Trade:
    """
    Represents a completed trade.

    Based on Trading Instructions Section VI.D - Trade Journal Entries.
    """

    id: str
    asset: str
    asset_class: AssetClass
    side: OrderSide
    entry_price: float
    exit_price: float
    quantity: float
    entry_time: datetime
    exit_time: datetime
    stop_loss: float
    take_profit: Optional[float] = None
    actual_rrr: float = 0.0
    pnl_dollar: float = 0.0
    pnl_percent: float = 0.0
    commission: float = 0.0
    status: str = "closed"
    margin_used: float = 0.0
    leverage: int = 15
    strategy: StrategyType = StrategyType.TREND_FOLLOWING
    quality: TradeQuality = TradeQuality.STANDARD
    market_state: MarketState = MarketState.UNKNOWN
    volume_at_entry: float = 0.0
    rsi_at_entry: Optional[float] = None
    atr_at_entry: Optional[float] = None
    reason_entered: str = ""
    reason_exited: str = ""
    emotional_state: str = "calm"
    rule_adherence_score: int = 10  # 0-10 scale
    notes: str = ""

    def __post_init__(self):
        """Calculate derived fields after initialization."""
        self._calculate_pnl()
        self._calculate_rrr()

    def _calculate_pnl(self):
        """Calculate profit/loss."""
        if self.side == OrderSide.BUY:
            self.pnl_dollar = (self.exit_price - self.entry_price) * self.quantity
        else:
            self.pnl_dollar = (self.entry_price - self.exit_price) * self.quantity

        # Account for leverage
        position_value = self.entry_price * self.quantity
        self.pnl_percent = (self.pnl_dollar / position_value) * 100

    def _calculate_rrr(self):
        """Calculate actual reward-to-risk ratio."""
        risk = abs(self.entry_price - self.stop_loss)
        if risk == 0:
            self.actual_rrr = 0
        else:
            reward = abs(self.exit_price - self.entry_price)
            self.actual_rrr = reward / risk

    @property
    def duration_hours(self) -> float:
        """Trade duration in hours."""
        return (self.exit_time - self.entry_time).total_seconds() / 3600

    @property
    def is_winner(self) -> bool:
        """Check if trade was profitable."""
        return self.pnl_dollar > 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = self.__dict__.copy()
        data["entry_time"] = self.entry_time.isoformat()
        data["exit_time"] = self.exit_time.isoformat()
        data["side"] = self.side.value
        data["strategy"] = self.strategy.value
        data["quality"] = self.quality.value
        data["market_state"] = self.market_state.value
        return data


@dataclass
class Position:
    """
    Represents an open position.

    Based on Trading Instructions Section XI - Position Sizing Examples.
    Enhanced with Pacifica-specific fields for hourly funding and margin modes.
    """

    id: str
    asset: str
    asset_class: AssetClass
    side: OrderSide
    entry_price: float
    quantity: float
    margin_used: float
    leverage: int
    stop_loss: float
    take_profit: Optional[float] = None
    trailing_stop: Optional[float] = None
    entry_time: datetime = field(default_factory=datetime.now)
    status: PositionStatus = PositionStatus.OPEN
    strategy: StrategyType = StrategyType.TREND_FOLLOWING
    quality: TradeQuality = TradeQuality.STANDARD
    market_state: MarketState = MarketState.UNKNOWN

    # Pacifica-specific: Hourly funding (24x per day)
    hourly_funding_rate: float = 0.0  # Current hourly funding rate
    cumulative_funding_paid: float = 0.0  # Total funding paid since entry
    last_funding_timestamp: Optional[datetime] = None  # Last funding payment time

    # Pacifica-specific: Margin mode
    margin_mode: str = "cross"  # "cross" or "isolated"

    # Standard fields
    funding_rate: float = 0.0  # Deprecated, use hourly_funding_rate
    unrealized_pnl: float = 0.0
    liquidation_price: float = 0.0

    # Pacifica order validation fields
    tick_size: Optional[float] = None  # Minimum price increment for this market
    lot_size: Optional[float] = None  # Minimum size increment for this market

    def __post_init__(self):
        """Calculate derived fields."""
        self._calculate_liquidation_price()

    def _calculate_liquidation_price(self):
        """Calculate liquidation price based on leverage."""
        if self.leverage <= 1:
            return 0

        position_value = self.margin_used * self.leverage
        liquidation_distance = 1.0 / self.leverage

        if self.side == OrderSide.BUY:
            self.liquidation_price = self.entry_price * (1 - liquidation_distance)
        else:
            self.liquidation_price = self.entry_price * (1 + liquidation_distance)

    @property
    def position_value(self) -> float:
        """Current position value."""
        return self.margin_used * self.leverage

    @property
    def current_price(self) -> float:
        """Current market price (placeholder - would be updated from exchange)."""
        return self.entry_price  # In real implementation, fetch from exchange

    @property
    def stop_distance_pct(self) -> float:
        """Stop distance as percentage."""
        return abs(self.entry_price - self.stop_loss) / self.entry_price

    @property
    def liquidation_buffer_pct(self) -> float:
        """Buffer to liquidation as percentage."""
        if self.leverage <= 1:
            return float("inf")
        liquidation_distance = 1.0 / self.leverage
        return liquidation_distance - self.stop_distance_pct

    @property
    def margin_drawdown_pct(self) -> float:
        """Margin drawdown if stopped out."""
        loss_if_stopped = self.position_value * self.stop_distance_pct
        return loss_if_stopped / self.margin_used

    def update_unrealized_pnl(self, current_price: float):
        """Update unrealized P&L."""
        if self.side == OrderSide.BUY:
            self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
        else:
            self.unrealized_pnl = (self.entry_price - current_price) * self.quantity

    def is_liquidation_risk(self) -> bool:
        """Check if position is at risk of liquidation."""
        return self.liquidation_buffer_pct < 0.015  # Less than 1.5% buffer

    def apply_hourly_funding_payment(self, funding_rate: float):
        """
        Apply hourly funding payment and update position state.

        CRITICAL for Pacifica: This is called EVERY HOUR (24x per day).
        For isolated margin, this reduces margin and moves liquidation closer!

        Args:
            funding_rate: Current hourly funding rate (as decimal)
        """
        self.hourly_funding_rate = funding_rate
        self.last_funding_timestamp = datetime.now()

        # Calculate payment
        position_value = self.position_value
        funding_payment = position_value * funding_rate

        # Determine if paying or receiving
        is_paying = (self.side == OrderSide.BUY and funding_rate > 0) or (
            self.side == OrderSide.SELL and funding_rate < 0
        )

        if is_paying:
            # Paying funding
            self.cumulative_funding_paid += abs(funding_payment)

            # For isolated margin, reduce margin balance
            if self.margin_mode == "isolated":
                self.margin_used -= abs(funding_payment)

                # Recalculate liquidation price with reduced margin
                self._calculate_liquidation_price()
        else:
            # Receiving funding
            self.cumulative_funding_paid -= abs(funding_payment)

            # For isolated margin, increase margin balance
            if self.margin_mode == "isolated":
                self.margin_used += abs(funding_payment)

                # Recalculate liquidation price with increased margin
                self._calculate_liquidation_price()

    @property
    def hours_since_entry(self) -> float:
        """Hours since position was opened."""
        return (datetime.now() - self.entry_time).total_seconds() / 3600

    @property
    def funding_payments_count(self) -> int:
        """Number of funding payments that have occurred."""
        return int(self.hours_since_entry)  # One payment per hour

    @property
    def expected_total_funding_cost(self, hours_to_hold: int = 24) -> float:
        """
        Calculate expected total funding cost over hold period.

        Args:
            hours_to_hold: Expected additional hours to hold

        Returns:
            Expected total funding cost (positive = cost, negative = profit)
        """
        if self.hourly_funding_rate == 0:
            return 0.0

        return self.position_value * self.hourly_funding_rate * hours_to_hold

    @property
    def funding_cost_pct_of_position(self) -> float:
        """Cumulative funding cost as percentage of position value."""
        if self.position_value == 0:
            return 0.0
        return (self.cumulative_funding_paid / self.position_value) * 100

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = self.__dict__.copy()
        data["entry_time"] = self.entry_time.isoformat()
        data["side"] = self.side.value
        data["status"] = self.status.value
        data["strategy"] = self.strategy.value
        data["quality"] = self.quality.value
        data["market_state"] = self.market_state.value
        if self.last_funding_timestamp:
            data["last_funding_timestamp"] = self.last_funding_timestamp.isoformat()
        return data


@dataclass
class Order:
    """
    Represents a trading order for Pacifica.

    Includes Pacifica-specific validation fields and order types.
    """

    id: str
    asset: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None  # Required for limit orders
    stop_price: Optional[float] = None  # Required for stop orders
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None
    filled_quantity: float = 0.0
    filled_price: Optional[float] = None

    # Pacifica-specific fields
    leverage: int = 10
    margin_mode: str = "cross"  # "cross" or "isolated"
    reduce_only: bool = False  # Only reduce existing position
    post_only: bool = False  # Only maker (provide liquidity)
    time_in_force: str = "GTC"  # GTC, IOC, FOK

    # Pacifica order validation fields
    tick_size: Optional[float] = None
    lot_size: Optional[float] = None
    min_size: Optional[float] = None
    max_size: Optional[float] = None
    min_leverage: int = 5
    max_leverage: int = 50

    def is_valid_for_pacifica(self) -> Tuple[bool, List[str]]:
        """
        Validate order meets Pacifica requirements.

        Returns:
            Tuple of (is_valid, list_of_errors)
        """
        errors = []

        # Validate price (tick size)
        if self.price and self.tick_size:
            if (self.price % self.tick_size) != 0:
                errors.append(
                    f"Price {self.price} not a multiple of tick size {self.tick_size}"
                )

        # Validate quantity (lot size)
        if self.lot_size:
            if (self.quantity % self.lot_size) != 0:
                errors.append(
                    f"Quantity {self.quantity} not a multiple of lot size {self.lot_size}"
                )

        # Validate size limits
        if self.min_size and self.quantity < self.min_size:
            errors.append(f"Quantity {self.quantity} below minimum {self.min_size}")

        if self.max_size and self.quantity > self.max_size:
            errors.append(f"Quantity {self.quantity} exceeds maximum {self.max_size}")

        # Validate leverage
        if self.leverage < self.min_leverage:
            errors.append(
                f"Leverage {self.leverage}x below minimum {self.min_leverage}x"
            )

        if self.leverage > self.max_leverage:
            errors.append(
                f"Leverage {self.leverage}x exceeds maximum {self.max_leverage}x"
            )

        # Validate order type requirements
        if self.order_type == OrderType.LIMIT and self.price is None:
            errors.append("Limit order requires price")

        if (
            self.order_type in [OrderType.STOP_MARKET, OrderType.STOP_LIMIT]
            and self.stop_price is None
        ):
            errors.append("Stop order requires stop_price")

        return len(errors) == 0, errors

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = self.__dict__.copy()
        data["side"] = self.side.value
        data["order_type"] = self.order_type.value
        data["status"] = self.status.value
        data["created_at"] = self.created_at.isoformat()
        if self.filled_at:
            data["filled_at"] = self.filled_at.isoformat()
        return data


@dataclass
class MarketData:
    """
    Market data for analysis.

    Contains OHLCV data and calculated indicators.
    """

    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    indicators: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_bullish_candle(self) -> bool:
        """Check if candle is bullish."""
        return self.close > self.open

    @property
    def body_size(self) -> float:
        """Candle body size."""
        return abs(self.close - self.open)

    @property
    def wick_size(self) -> float:
        """Total wick size."""
        return self.high - self.low

    @property
    def body_pct(self) -> float:
        """Body size as percentage of total range."""
        total_range = self.high - self.low
        return self.body_size / total_range if total_range > 0 else 0


@dataclass
class Signal:
    """
    Trading signal with all required validation.

    Based on Trading Instructions Section III.B - Entry Signal Requirements.
    """

    strategy: StrategyType
    asset: str
    asset_class: AssetClass
    side: OrderSide
    entry_price: float
    stop_loss: float
    take_profit: Optional[float] = None
    confidence: float = 0.0  # 0-1 scale
    quantity: float = 0.0  # Trade quantity/size
    quality: TradeQuality = TradeQuality.STANDARD
    market_state: MarketState = MarketState.UNKNOWN
    timeframe: str = "15min"
    pattern: str = ""
    volume_confirmation: bool = False
    multi_timeframe_alignment: bool = False
    support_resistance_valid: bool = False
    rrr_meets_minimum: bool = False
    liquidation_buffer_safe: bool = False
    account_risk_ok: bool = False
    margin_drawdown_ok: bool = False
    forbidden_conditions_clear: bool = False
    timestamp: datetime = field(default_factory=datetime.now)
    indicators: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    # Grid trading specific attributes (added Jan 2026)
    grid_levels: Optional[int] = None  # Number of grid levels
    grid_capital: Optional[float] = None  # Capital allocated to grid
    spacing: Optional[float] = None  # ATR-based spacing between grid levels

    # Entry timing and risk profile (added Jan 2026)
    entry_time: Optional[datetime] = None  # Planned entry time
    risk_profile: Optional[str] = (
        None  # Risk profile: 'conservative', 'moderate', 'aggressive'
    )

    @property
    def rrr(self) -> float:
        """Calculate reward-to-risk ratio."""
        risk = abs(self.entry_price - self.stop_loss)
        if risk == 0 or self.take_profit is None:
            return 0
        reward = abs(self.take_profit - self.entry_price)
        return reward / risk

    @property
    def stop_distance_pct(self) -> float:
        """Stop distance as percentage."""
        return abs(self.entry_price - self.stop_loss) / self.entry_price

    def is_valid(self) -> bool:
        """
        Check if signal meets all qualification criteria.

        Based on Section III.B - All conditions must be met.
        """
        return (
            self.volume_confirmation
            and self.multi_timeframe_alignment
            and self.support_resistance_valid
            and self.rrr_meets_minimum
            and self.liquidation_buffer_safe
            and self.account_risk_ok
            and self.margin_drawdown_ok
            and self.forbidden_conditions_clear
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = self.__dict__.copy()
        data["strategy"] = self.strategy.value
        data["side"] = self.side.value
        data["quality"] = self.quality.value
        data["market_state"] = self.market_state.value
        data["timestamp"] = self.timestamp.isoformat()
        return data


@dataclass
class Account:
    """
    Account state and metrics.

    Based on Trading Instructions Section IX - Performance Metrics.
    """

    balance: float
    available_margin: float
    total_margin_used: float = 0.0
    positions: List[Position] = field(default_factory=list)
    trades: List[Trade] = field(default_factory=list)
    daily_pnl: float = 0.0
    peak_balance: float = 0.0
    drawdown_pct: float = 0.0
    win_rate: float = 0.0
    avg_rrr: float = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    consecutive_losses: int = 0
    last_update: datetime = field(default_factory=datetime.now)

    def __post_init__(self):
        """Initialize peak balance."""
        if self.peak_balance == 0:
            self.peak_balance = self.balance

    @property
    def total_value(self) -> float:
        """Total account value including unrealized P&L."""
        unrealized = sum(pos.unrealized_pnl for pos in self.positions)
        return self.balance + unrealized

    @property
    def margin_utilization_pct(self) -> float:
        """Margin utilization percentage."""
        if self.balance == 0:
            return 0
        return self.total_margin_used / self.balance

    @property
    def current_drawdown_pct(self) -> float:
        """Current drawdown from peak."""
        if self.peak_balance == 0:
            return 0
        return (self.peak_balance - self.total_value) / self.peak_balance

    def update_metrics(self):
        """Update performance metrics."""
        if not self.trades:
            return

        # Win rate
        winners = [t for t in self.trades if t.is_winner]
        self.win_rate = len(winners) / len(self.trades)

        # Average RRR
        rr_values = [t.actual_rrr for t in self.trades if t.actual_rrr > 0]
        self.avg_rrr = sum(rr_values) / len(rr_values) if rr_values else 0

        # Expectancy
        if self.trades:
            total_return = sum(t.pnl_percent for t in self.trades)
            self.expectancy = total_return / len(self.trades)

        # Profit factor
        gross_profit = sum(t.pnl_dollar for t in self.trades if t.pnl_dollar > 0)
        gross_loss = abs(sum(t.pnl_dollar for t in self.trades if t.pnl_dollar < 0))
        self.profit_factor = (
            gross_profit / gross_loss if gross_loss > 0 else float("inf")
        )

        # Consecutive losses
        losses = 0
        for trade in reversed(self.trades):
            if trade.is_winner:
                break
            losses += 1
        self.consecutive_losses = losses

        # Update drawdown
        self.drawdown_pct = self.current_drawdown_pct
        self.peak_balance = max(self.peak_balance, self.total_value)

    def add_trade(self, trade: Trade):
        """Add completed trade and update metrics."""
        self.trades.append(trade)
        self.balance += trade.pnl_dollar
        self.daily_pnl += trade.pnl_dollar
        self.update_metrics()

    def add_position(self, position: Position):
        """Add open position."""
        self.positions.append(position)
        self.total_margin_used += position.margin_used
        self.available_margin = self.balance - self.total_margin_used

    def remove_position(self, position_id: str):
        """Remove closed position."""
        for pos in self.positions:
            if pos.id == position_id:
                self.positions.remove(pos)
                self.total_margin_used -= pos.margin_used
                self.available_margin = self.balance - self.total_margin_used
                break

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = self.__dict__.copy()
        data["positions"] = [p.to_dict() for p in self.positions]
        data["trades"] = [t.to_dict() for t in self.trades]
        data["last_update"] = self.last_update.isoformat()
        return data


@dataclass
class RiskValidation:
    """
    Result of comprehensive risk validation.

    Based on Trading Instructions Section I - MANDATORY RISK MANAGEMENT RULES.
    """

    is_valid: bool
    stop_in_range: bool
    stop_optimal: bool
    liquidation_safe: bool
    account_risk_ok: bool
    margin_drawdown_ok: bool
    rrr_meets_minimum: bool
    volume_confirmed: bool
    multi_timeframe_aligned: bool
    forbidden_conditions_clear: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def add_error(self, error: str):
        """Add validation error."""
        self.errors.append(error)
        self.is_valid = False

    def add_warning(self, warning: str):
        """Add validation warning."""
        self.warnings.append(warning)

    @property
    def all_checks_pass(self) -> bool:
        """Check if all individual validations pass."""
        return (
            self.stop_in_range
            and self.liquidation_safe
            and self.account_risk_ok
            and self.margin_drawdown_ok
            and self.rrr_meets_minimum
            and self.volume_confirmed
            and self.multi_timeframe_aligned
            and self.forbidden_conditions_clear
        )


# NOTE: Duplicate Order class removed - use the Order class defined at line 317
# The original Order class above has `asset` field which is the correct one for Pacifica integration
