"""Exchange abstraction base: normalized vocabulary, dataclasses, interface.

This module defines the exchange-agnostic surface that the trading bot
consumes.  The surface was mapped from actual call sites (trading_bot.py,
grid_lifecycle_manager.py, api_server.py, multi_timeframe_fetcher.py,
strategies/funding_arb.py) - it intentionally contains no methods that
nobody calls.

Normalized vocabulary
---------------------
* Order sides:    ``models.OrderSide`` (BUY / SELL).  Never "bid"/"ask".
* Position sides: ``PositionSide`` (LONG / SHORT).  Never lowercase
  "long"/"short".
* Amounts:        ``float``.  The whole codebase computes quantities as
  floats; adapters convert to whatever wire type the exchange wants
  (Pacifica: string) at the last possible moment.
* Funding:        ``ExchangeCapabilities.funding_interval_hours`` describes
  the funding cycle (Pacifica: 1 hour / 24x per day, Blofin: 8 hours).

Adapters (``pacifica.py``, ``blofin.py``) are composition wrappers around
the existing exchange-native clients.  All vocabulary conversion happens
inside the adapter boundary - call sites pass and receive normalized
values only.

Response note: mutating calls (``place_order``, ``cancel_*``) return the
raw exchange acknowledgement dict unchanged, because existing call sites
parse the Pacifica ``{"success": bool, "data": {...}}`` wrapper and the
intermittent ``"success"``-string ack.  Normalizing acks is a follow-up.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from ..models import OrderSide, OrderType


class PositionSide(str, Enum):
    """Normalized position side (exchange-native casing never leaks out)."""

    LONG = "LONG"
    SHORT = "SHORT"


# Union types accepted by adapter methods.  Adapters normalize strings
# tolerantly ("buy"/"BUY"/"bid" -> OrderSide.BUY) so legacy call sites can
# migrate incrementally.
OrderSideInput = Union[OrderSide, str]
OrderTypeInput = Union[OrderType, str]


@dataclass(frozen=True)
class ExchangeCapabilities:
    """Static description of an exchange's quirks.

    Attributes:
        name: Registry name of the exchange (e.g. "pacifica").
        funding_interval_hours: Hours between funding payments
            (Pacifica: 1, Blofin: 8).
        has_testnet: Whether a testnet/demo environment exists.
        native_order_sides: Wire vocabulary for order sides.
        native_position_sides: Wire vocabulary for position sides.
        amounts_as_strings: True if order amounts must be sent as strings.
        min_order_size_source: Where minimum order size comes from
            (e.g. "info_endpoint" for Pacifica's /info market specs).
    """

    name: str
    funding_interval_hours: int
    has_testnet: bool
    native_order_sides: Tuple[str, ...]
    native_position_sides: Tuple[str, ...]
    amounts_as_strings: bool
    min_order_size_source: str


@dataclass
class ExchangeBalance:
    """Normalized account balance snapshot."""

    equity: float
    available: float
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExchangePosition:
    """Normalized open position (ghost qty==0 entries are filtered out)."""

    symbol: str
    side: PositionSide
    quantity: float
    entry_price: float
    unrealized_pnl: float = 0.0
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExchangeOrder:
    """Normalized open order."""

    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExchangeFill:
    """Normalized trade fill / execution record."""

    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    timestamp: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FundingInfo:
    """Normalized funding-rate snapshot for one symbol.

    ``funding_rate`` is the rate PER FUNDING INTERVAL.  Use
    ``funding_interval_hours`` to scale to daily/annualized figures
    (Pacifica pays hourly, so daily = rate * 24; Blofin pays every 8h,
    so daily = rate * 3).
    """

    symbol: str
    funding_rate: float
    funding_interval_hours: int
    next_funding_time: Optional[Any] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class ExchangeClient(ABC):
    """Abstract exchange adapter consumed by the trading/execution paths.

    Concrete adapters wrap the exchange-native REST/WS clients via
    composition and own ALL vocabulary conversion (side names, casing,
    amount types).  Two tiers of methods exist:

    * Normalized tier: ``place_order``, ``cancel_order``,
      ``cancel_all_orders``, ``get_positions``, ``get_balance``,
      ``get_funding_info`` - use normalized enums/dataclasses.
    * Raw passthrough tier: ``get_positions_raw``, ``get_orders``,
      ``get_trades``, ``get_candles``, ``get_markets``,
      ``get_market_data``, ``get_instrument_info``,
      ``get_funding_history`` - return exchange-native dicts for call
      sites not yet migrated (documented legacy surface).
    """

    @classmethod
    @abstractmethod
    def capabilities(cls) -> ExchangeCapabilities:
        """Return the static capability description for this exchange."""

    # ------------------------------------------------------------------
    # Underlying clients (data plumbing - construction is routed through
    # the adapter so exchange selection controls which clients exist).
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def rest_client(self) -> Any:
        """Underlying exchange-native REST client (legacy data surface)."""

    @property
    @abstractmethod
    def ws_client(self) -> Any:
        """Underlying exchange-native WebSocket client (may be None)."""

    # ------------------------------------------------------------------
    # Normalized trading surface
    # ------------------------------------------------------------------

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: OrderSideInput,
        quantity: float,
        order_type: OrderTypeInput = OrderType.MARKET,
        price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Place an order using normalized side/type vocabulary.

        Args:
            symbol: Trading symbol (e.g. "BTC").
            side: OrderSide.BUY/SELL (strings tolerated: "buy", "BUY",
                "bid", ...).
            quantity: Order quantity as float (adapter converts to the
                exchange wire type).
            order_type: OrderType.MARKET/LIMIT (strings tolerated).
            price: Limit price (required for limit orders).

        Returns:
            Raw exchange acknowledgement dict (see module docstring).
        """

    @abstractmethod
    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Cancel a single order."""

    @abstractmethod
    def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Cancel all open orders, optionally scoped to one symbol."""

    @abstractmethod
    def get_positions(self) -> List[ExchangePosition]:
        """Return open positions, normalized.

        Adapters must uppercase/normalize the side vocabulary and filter
        out quantity==0 ghost positions (documented Pacifica behavior).
        """

    @abstractmethod
    def get_balance(self) -> ExchangeBalance:
        """Return the normalized account balance."""

    @abstractmethod
    def get_funding_info(self, symbol: str) -> Optional[FundingInfo]:
        """Return the current funding snapshot for a symbol, or None."""

    # ------------------------------------------------------------------
    # Raw passthrough surface (legacy call sites; exchange-native shapes)
    # ------------------------------------------------------------------

    @abstractmethod
    def get_positions_raw(self) -> List[Dict[str, Any]]:
        """Return positions as exchange-native dicts (legacy surface)."""

    @abstractmethod
    def get_orders(self) -> List[Dict[str, Any]]:
        """Return open orders as exchange-native dicts (legacy surface)."""

    @abstractmethod
    def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return trade history as exchange-native dicts (legacy surface)."""

    @abstractmethod
    def get_candles(
        self,
        market: str,
        interval: str = "15m",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Return OHLCV candles (already-standardized dict shape)."""

    @abstractmethod
    def get_markets(self) -> List[Dict[str, Any]]:
        """Return available markets (exchange-native dicts)."""

    @abstractmethod
    def get_market_data(self, symbol: str) -> Dict[str, Any]:
        """Return current market data/prices for a symbol."""

    @abstractmethod
    def get_instrument_info(self, symbol: str) -> Dict[str, Any]:
        """Return instrument constraints (tick_size, lot_size, ...)."""

    @abstractmethod
    def get_funding_history(
        self, symbol: str, limit: int = 8
    ) -> List[Dict[str, Any]]:
        """Return funding-rate history records (exchange-native dicts)."""
