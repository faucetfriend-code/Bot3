"""Blofin adapter STUB - documented slot for a future implementation.

Every trading method raises NotImplementedError.  Selecting
``EXCHANGE=blofin`` will construct this stub via the factory but any use
fails fast with a clear message.

Known integration facts for the implementer
-------------------------------------------
* Official SDK: ``pip install blofin`` - supports demo trading via
  ``isDemo=True`` when constructing its clients.
* API docs: https://docs.blofin.com (REST + WebSocket; separate demo
  base URL for paper trading).
* Auth: API key + secret + passphrase, HMAC request signing (unlike
  Pacifica's Ed25519 agent-wallet signatures).
* Order sides: standard "buy"/"sell".  Positions use a positionSide
  semantic (net vs long/short hedge mode) - map to the normalized
  PositionSide enum in this adapter, nowhere else.
* Quantities are denominated in CONTRACTS with per-instrument contract
  and lot sizes (unlike Pacifica's base-currency string amounts) - the
  adapter must convert normalized float base quantities to contract
  counts using instrument info.
* Funding: standard 8-hour cycle (3x/day) -> capabilities report
  ``funding_interval_hours=8``.  FundingArb scales its rate math off
  this value automatically.
* CCXT also supports Blofin ("blofin") if the official SDK proves
  awkward.
"""

from typing import Any, Dict, List, Optional

from ..models import OrderType
from .base import (
    ExchangeBalance,
    ExchangeCapabilities,
    ExchangeClient,
    ExchangePosition,
    FundingInfo,
    OrderSideInput,
    OrderTypeInput,
)

_MSG = (
    "BlofinExchange is a stub - the Blofin integration is not implemented "
    "yet. See trading_bot_v2/exchanges/blofin.py module docstring for the "
    "known integration facts (official 'blofin' SDK, isDemo=True demo "
    "mode, 8h funding, HMAC key/secret/passphrase auth)."
)


class BlofinExchange(ExchangeClient):
    """Stub adapter for Blofin perpetual futures (not implemented)."""

    _CAPABILITIES = ExchangeCapabilities(
        name="blofin",
        funding_interval_hours=8,
        has_testnet=True,  # Demo trading via isDemo=True
        native_order_sides=("buy", "sell"),
        native_position_sides=("long", "short", "net"),
        amounts_as_strings=True,  # Contract counts sent as strings
        min_order_size_source="instrument_info",
    )

    def __init__(self, rest_client: Any = None, ws_client: Any = None):
        """Store injected clients (unused until the adapter is implemented)."""
        self._rest = rest_client
        self._ws = ws_client

    @classmethod
    def capabilities(cls) -> ExchangeCapabilities:
        """Return Blofin's static capability description (real values)."""
        return cls._CAPABILITIES

    @property
    def rest_client(self) -> Any:
        """Injected REST client, if any (no default construction yet)."""
        return self._rest

    @property
    def ws_client(self) -> Any:
        """Injected WS client, if any (no default construction yet)."""
        return self._ws

    def place_order(
        self,
        symbol: str,
        side: OrderSideInput,
        quantity: float,
        order_type: OrderTypeInput = OrderType.MARKET,
        price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Not implemented."""
        raise NotImplementedError(_MSG)

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Not implemented."""
        raise NotImplementedError(_MSG)

    def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Not implemented."""
        raise NotImplementedError(_MSG)

    def get_positions(self) -> List[ExchangePosition]:
        """Not implemented."""
        raise NotImplementedError(_MSG)

    def get_balance(self) -> ExchangeBalance:
        """Not implemented."""
        raise NotImplementedError(_MSG)

    def get_funding_info(self, symbol: str) -> Optional[FundingInfo]:
        """Not implemented."""
        raise NotImplementedError(_MSG)

    def get_positions_raw(self) -> List[Dict[str, Any]]:
        """Not implemented."""
        raise NotImplementedError(_MSG)

    def get_orders(self) -> List[Dict[str, Any]]:
        """Not implemented."""
        raise NotImplementedError(_MSG)

    def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Not implemented."""
        raise NotImplementedError(_MSG)

    def get_candles(
        self,
        market: str,
        interval: str = "15m",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Not implemented."""
        raise NotImplementedError(_MSG)

    def get_markets(self) -> List[Dict[str, Any]]:
        """Not implemented."""
        raise NotImplementedError(_MSG)

    def get_market_data(self, symbol: str) -> Dict[str, Any]:
        """Not implemented."""
        raise NotImplementedError(_MSG)

    def get_instrument_info(self, symbol: str) -> Dict[str, Any]:
        """Not implemented."""
        raise NotImplementedError(_MSG)

    def get_funding_history(
        self, symbol: str, limit: int = 8
    ) -> List[Dict[str, Any]]:
        """Not implemented."""
        raise NotImplementedError(_MSG)
