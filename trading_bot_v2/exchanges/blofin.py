"""Blofin adapter: normalized surface over the native Blofin clients.

Composition wrapper around ``trading_bot_v2.blofin_client.BlofinClient``
(REST) and ``trading_bot_v2.blofin_ws_client`` (public market-data WS),
mirroring how ``pacifica.py`` wraps the Pacifica-native clients.

SDK-vs-raw decision
-------------------
The official ``blofin`` PyPI SDK (v0.5.0) was inspected and NOT used:
its REST base URL is a hard-coded constant with no demo-trading
support (demo is a different host, ``demo-trading-openapi.blofin.com``,
not a flag), and it ships no WebSocket client and no retry handling.
Since this bot defaults to demo mode (``BLOFIN_DEMO=true``), the
adapter uses the raw REST/WS clients in ``blofin_client.py`` /
``blofin_ws_client.py`` instead; the SDK source served only to confirm
the HMAC signing scheme.  See the ``blofin_client`` module docstring.

Vocabulary boundary
-------------------
* Order sides:    wire uses standard "buy"/"sell".
* Position sides: wire uses "long"/"short"/"net"; in net (one-way)
  mode the sign of the contract count carries direction.  The native
  client already resolves this to lowercase "long"/"short" in its
  bot-native dicts; this adapter maps those to ``PositionSide``.
* Amounts:        the bot passes base-currency floats; the native
  client converts to Blofin CONTRACT counts using per-instrument
  ``contractValue``/``lotSize`` from /market/instruments (cached), and
  converts contract counts back to base units on reads.
* Funding:        8-hour cycle (3x/day) -> funding_interval_hours=8.
* Auth:           API key + secret + passphrase (HMAC).  Public data
  works without keys; private calls raise BlofinAuthError telling the
  user to add BLOFIN_* keys to .env.
"""

import logging
from typing import Any, Dict, List, Optional

from ..models import OrderSide, OrderType
from .base import (
    ExchangeBalance,
    ExchangeCapabilities,
    ExchangeClient,
    ExchangePosition,
    FundingInfo,
    OrderSideInput,
    OrderTypeInput,
    PositionSide,
)

logger = logging.getLogger(__name__)

# Keys checked (in order) when extracting fields from bot-native
# position dicts (same tolerant sets as the Pacifica adapter).
_QTY_KEYS = ("size", "amount", "quantity", "position_size", "pos_size")
_ENTRY_KEYS = ("avg_entry_price", "entry_price", "average_entry", "avg_price", "entry")


def _first_float(data: Dict[str, Any], keys, default: float = 0.0) -> float:
    """Return the first parseable float among ``keys`` in ``data``."""
    for key in keys:
        if key in data and data[key] is not None:
            try:
                return float(data[key])
            except (TypeError, ValueError):
                continue
    return default


class BlofinExchange(ExchangeClient):
    """ExchangeClient adapter for Blofin USDT-margined perpetuals."""

    _CAPABILITIES = ExchangeCapabilities(
        name="blofin",
        funding_interval_hours=8,
        has_testnet=True,  # Demo trading host (BLOFIN_DEMO=true)
        native_order_sides=("buy", "sell"),
        native_position_sides=("long", "short", "net"),
        amounts_as_strings=True,  # Contract counts sent as strings
        min_order_size_source="instruments_endpoint",
    )

    def __init__(self, rest_client: Any = None, ws_client: Any = None):
        """Initialize the adapter.

        Args:
            rest_client: Existing BlofinClient to wrap.  If None, one
                is built lazily from BLOFIN_* env vars on first use (so
                factory/capability lookups never touch credentials).
            ws_client: Existing data client.  If None, the process
                singleton from ``blofin_ws_client.get_blofin_ws_client``
                is used lazily on first access.
        """
        self._rest = rest_client
        self._ws = ws_client

    # ------------------------------------------------------------------
    # Capabilities and underlying clients
    # ------------------------------------------------------------------

    @classmethod
    def capabilities(cls) -> ExchangeCapabilities:
        """Return Blofin's static capability description."""
        return cls._CAPABILITIES

    @property
    def rest_client(self) -> Any:
        """Underlying BlofinClient (built lazily from env if needed)."""
        if self._rest is None:
            from ..blofin_client import BlofinClient

            self._rest = BlofinClient()
        return self._rest

    @property
    def ws_client(self) -> Any:
        """Underlying market-data WS client (singleton if not injected)."""
        if self._ws is None:
            from ..blofin_ws_client import get_blofin_ws_client

            self._ws = get_blofin_ws_client()
        return self._ws

    # ------------------------------------------------------------------
    # Vocabulary conversion (the ONLY place these mappings may live)
    # ------------------------------------------------------------------

    @staticmethod
    def to_native_order_side(side: OrderSideInput) -> str:
        """Normalize an order side to the Blofin wire value "buy"/"sell"."""
        return (
            "buy"
            if BlofinExchange._to_order_side(side) == OrderSide.BUY
            else "sell"
        )

    @staticmethod
    def from_native_order_side(value: str) -> OrderSide:
        """Convert a Blofin wire order side back to OrderSide."""
        lowered = str(value).strip().lower()
        if lowered in ("buy", "bid"):
            return OrderSide.BUY
        if lowered in ("sell", "ask"):
            return OrderSide.SELL
        raise ValueError(f"Unknown Blofin order side: {value!r}")

    @staticmethod
    def from_native_position_side(value: Optional[str]) -> PositionSide:
        """Normalize any Blofin position-side spelling to PositionSide.

        Tolerates "long"/"short" (bot-native dicts), order-side leakage
        ("buy"/"sell") and any casing.  Defaults to LONG (mirrors the
        Pacifica adapter's battle-tested behavior).
        """
        if not value:
            return PositionSide.LONG
        lowered = str(value).strip().lower()
        if lowered in ("short", "sell", "ask"):
            return PositionSide.SHORT
        return PositionSide.LONG

    @staticmethod
    def _to_order_side(side: OrderSideInput) -> OrderSide:
        """Normalize enum-or-string input to OrderSide (tolerant)."""
        if isinstance(side, OrderSide):
            return side
        lowered = str(side).strip().lower()
        if lowered in ("buy", "bid", "long"):
            return OrderSide.BUY
        if lowered in ("sell", "ask", "short"):
            return OrderSide.SELL
        raise ValueError(f"Cannot normalize order side: {side!r}")

    @staticmethod
    def _to_order_type(order_type: OrderTypeInput) -> OrderType:
        """Normalize enum-or-string input to OrderType (tolerant)."""
        if isinstance(order_type, OrderType):
            return order_type
        lowered = str(order_type).strip().lower()
        try:
            return OrderType(lowered)
        except ValueError:
            raise ValueError(f"Cannot normalize order type: {order_type!r}")

    # ------------------------------------------------------------------
    # Normalized trading surface
    # ------------------------------------------------------------------

    def place_order(
        self,
        symbol: str,
        side: OrderSideInput,
        quantity: float,
        order_type: OrderTypeInput = OrderType.MARKET,
        price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Place an order with normalized vocabulary.

        Delegates to ``BlofinClient.place_order`` with exact lowercase
        "buy"/"sell" and "market"/"limit"; the native client converts
        the base quantity to contracts, rounds to lot size, and rejects
        below-minimum sizes with a clear error.

        Returns:
            Pacifica-style ack dict {"success": bool, "data":
            {"order_id": ...}} (shape parsed by existing call sites).
        """
        side_str = self._to_order_side(side).value  # "buy" / "sell"
        type_str = self._to_order_type(order_type).value  # "market" / "limit"
        if type_str == OrderType.LIMIT.value and price is None:
            raise ValueError("Price is required for limit orders")
        return self.rest_client.place_order(
            symbol=symbol,
            side=side_str,
            quantity=quantity,
            order_type=type_str,
            price=price,
        )

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Cancel a single order (passthrough)."""
        return self.rest_client.cancel_order(symbol=symbol, order_id=order_id)

    def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Cancel all open orders, optionally for one symbol."""
        return self.rest_client.cancel_all_orders(symbol=symbol)

    def get_positions(self) -> List[ExchangePosition]:
        """Return open positions normalized (base units, qty==0 filtered).

        The native client already converts contracts to base units and
        resolves net-mode signs to "long"/"short"; this method maps to
        the normalized dataclass and re-applies the ghost filter.
        """
        raw_positions = self.rest_client.get_positions() or []
        normalized: List[ExchangePosition] = []
        for raw in raw_positions:
            if not isinstance(raw, dict):
                continue
            quantity = _first_float(raw, _QTY_KEYS, 0.0)
            if quantity <= 0:
                continue  # Ghost position filter (qty == 0)
            normalized.append(
                ExchangePosition(
                    symbol=raw.get("symbol", ""),
                    side=self.from_native_position_side(raw.get("side")),
                    quantity=quantity,
                    entry_price=_first_float(raw, _ENTRY_KEYS, 0.0),
                    unrealized_pnl=_first_float(raw, ("unrealized_pnl",), 0.0),
                    raw=raw,
                )
            )
        return normalized

    def get_balance(self) -> ExchangeBalance:
        """Return the normalized futures account balance."""
        raw = self.rest_client.get_balance() or {}
        equity_str = raw.get("balance", raw.get("account_equity", "0"))
        try:
            equity = float(equity_str) if equity_str else 0.0
        except (TypeError, ValueError):
            equity = 0.0
        available_str = raw.get("available_to_spend", raw.get("available"))
        try:
            available = float(available_str) if available_str else equity
        except (TypeError, ValueError):
            available = equity
        return ExchangeBalance(equity=equity, available=available, raw=raw)

    def get_funding_info(self, symbol: str) -> Optional[FundingInfo]:
        """Return the current funding snapshot (rate per 8h interval)."""
        raw = self.rest_client.get_funding_rate(symbol)
        if not raw:
            return None
        try:
            rate = float(raw.get("funding_rate", 0) or 0)
        except (TypeError, ValueError):
            rate = 0.0
        return FundingInfo(
            symbol=raw.get("symbol", symbol),
            funding_rate=rate,
            funding_interval_hours=self._CAPABILITIES.funding_interval_hours,
            next_funding_time=raw.get("next_funding_time"),
            raw=raw,
        )

    # ------------------------------------------------------------------
    # Raw passthrough surface (legacy call sites; bot-native shapes)
    # ------------------------------------------------------------------

    def get_positions_raw(self) -> List[Dict[str, Any]]:
        """Positions as bot-native dicts (legacy surface).

        The native client emits Pacifica-compatible keys (symbol, side
        "long"/"short", amount in base units) so legacy parsers work.
        """
        return self.rest_client.get_positions()

    def get_orders(self) -> List[Dict[str, Any]]:
        """Open orders as bot-native dicts (legacy surface)."""
        return self.rest_client.get_orders()

    def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Fill history as bot-native dicts (legacy surface)."""
        return self.rest_client.get_trades(limit=limit)

    def get_candles(
        self,
        market: str,
        interval: str = "15m",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """OHLCV candles (native client standardizes shape/ordering)."""
        return self.rest_client.get_candles(
            market=market,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

    def get_markets(self) -> List[Dict[str, Any]]:
        """Available USDT perps with bot-native keys (passthrough)."""
        return self.rest_client.get_markets()

    def get_market_data(self, symbol: str) -> Dict[str, Any]:
        """Ticker + funding snapshot for a symbol (passthrough)."""
        return self.rest_client.get_market_data(symbol)

    def get_instrument_info(self, symbol: str) -> Dict[str, Any]:
        """Instrument constraints in base units (tick/lot/min sizes)."""
        return self.rest_client.get_instrument_info(symbol)

    def get_funding_history(
        self, symbol: str, limit: int = 8
    ) -> List[Dict[str, Any]]:
        """Funding-rate history records (passthrough)."""
        return self.rest_client.get_funding_history(symbol, limit=limit)
