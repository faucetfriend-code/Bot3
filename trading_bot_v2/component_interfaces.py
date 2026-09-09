"""
Component Interfaces - Clean contracts for component communication.

Defines interfaces that components must implement for proper decoupling.
All components should depend on these interfaces rather than concrete implementations.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from datetime import datetime


class ComponentInterface(ABC):
    """Base interface for all trading bot components."""

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """Get component status and health information."""
        pass

    @abstractmethod
    def is_healthy(self) -> bool:
        """Check if component is functioning properly."""
        pass


class PriceProviderInterface(ComponentInterface):
    """Interface for components that provide price data."""

    @abstractmethod
    def get_price(self, symbol: str) -> Optional[float]:
        """Get current price for symbol."""
        pass

    @abstractmethod
    def get_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get full ticker data for symbol."""
        pass


class ExecutionInterface(ComponentInterface):
    """Interface for components that execute orders."""

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str,
        price: Optional[float] = None,
        reduce_only: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Place an order and return order details (reduce_only for exits)."""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order by ID."""
        pass

    @abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        """Get current positions."""
        pass


class RiskInterface(ComponentInterface):
    """Interface for risk management components."""

    @abstractmethod
    def request_capital_allocation(
        self,
        symbol: str,
        requested_amount: float,
        strategy: str,
        account_balance: float,
        current_exposure: float,
    ) -> Dict[str, Any]:
        """Request capital allocation approval."""
        pass

    @abstractmethod
    def validate_position_size(
        self,
        quantity: float,
        account_balance: float,
        current_exposure: float,
        entry_price: float = 0,
    ) -> bool:
        """Validate position size against risk limits."""
        pass


class GridInterface(ComponentInterface):
    """Interface for grid trading components."""

    @abstractmethod
    def has_active_grid(self, symbol: str) -> bool:
        """Check if symbol has an active grid."""
        pass

    @abstractmethod
    def register_new_grid(
        self,
        symbol: str,
        grid_capital: float,
        emergency_stop_price: float,
        regime: str = None,
        atr: float = 0,
        spacing: float = 0,
        num_levels: int = 10,
    ) -> bool:
        """Register a new grid for a symbol with persistence metadata."""
        pass

    @abstractmethod
    def on_emergency_stop_triggered(self, symbol: str) -> None:
        """Handle emergency stop for symbol."""
        pass

    @abstractmethod
    def on_regime_disallowed(self, symbol: str) -> None:
        """Handle regime change that disallows grids."""
        pass


class RegimeInterface(ComponentInterface):
    """Interface for market regime detection components."""

    @abstractmethod
    def detect_regime(self, market_data: Dict[str, List[float]]) -> str:
        """Detect current market regime from data."""
        pass

    @abstractmethod
    def is_grid_allowed(self, regime: str) -> bool:
        """Check if grid trading is allowed in regime."""
        pass

    @abstractmethod
    def get_active_strategies(self, regime: str) -> List[str]:
        """Get strategies allowed in current regime."""
        pass


class StrategyInterface(ComponentInterface):
    """Interface for strategy components."""

    @abstractmethod
    def generate_signals(
        self, symbol: str, market_data: Dict[str, Any], current_price: float
    ) -> List[Dict[str, Any]]:
        """Generate trading signals for a symbol."""
        pass

    @abstractmethod
    def should_skip_signal(self, signal: Dict[str, Any]) -> bool:
        """Check if signal should be skipped (cooldowns, etc.)."""
        pass

    @abstractmethod
    def register_trade_execution(
        self, signal: Dict[str, Any], order_result: Dict[str, Any]
    ) -> None:
        """Register successful trade execution."""
        pass


class DatabaseInterface(ComponentInterface):
    """Interface for database components."""

    @abstractmethod
    def save_trade(self, trade_data: Dict[str, Any]) -> Optional[int]:
        """Save trade to database."""
        pass

    @abstractmethod
    def save_position(self, position_data: Dict[str, Any]) -> Optional[int]:
        """Save position to database."""
        pass

    @abstractmethod
    def get_positions(self) -> List[Dict[str, Any]]:
        """Get current positions."""
        pass

    @abstractmethod
    def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent trades."""
        pass
