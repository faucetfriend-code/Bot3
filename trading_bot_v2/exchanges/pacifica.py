"""Pacifica.fi adapter: normalized surface over the existing native clients.

Composition wrapper around ``trading_bot_v2.pacifica_client.PacificaClient``
and ``trading_bot_v2.pacifica_ws_client`` (NOT a rewrite - the native
clients keep their behavior, retries, caching and signing).

Vocabulary boundary (the source of repeated bugs, see commit 86f9601):

* Order sides:    wire uses "bid" (buy) / "ask" (sell).  The native
  ``PacificaClient.place_order`` accepts lowercase "buy"/"sell" and maps
  to "bid"/"ask" itself - note its mapping is exact-match on "buy", so
  ANY other string (including "BUY") silently becomes "ask".  This
  adapter therefore only ever passes exact "buy"/"sell" down.
* Position sides: wire uses lowercase "long"/"short"; ghost positions
  with quantity==0 appear in responses and must be filtered.
* Amounts:        wire wants strings; ``PacificaClient`` performs
  ``str(quantity)`` when building payloads, so this adapter passes
  floats to it.
* Funding:        HOURLY (24x/day), unlike the 8h industry standard.
"""

import logging
from typing import Any, Dict, List, Optional

from ..models import OrderSide, OrderType
from ..order_result import OrderResult, OrderResultStatus
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

# Keys checked (in order) when extracting fields from raw position dicts.
# Mirrors the tolerant mapping in api_server._map_pacifica_position.
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


class PacificaExchange(ExchangeClient):
    """ExchangeClient adapter for Pacifica.fi perpetual futures."""

    _CAPABILITIES = ExchangeCapabilities(
        name="pacifica",
        funding_interval_hours=1,
        has_testnet=True,
        native_order_sides=("bid", "ask"),
        native_position_sides=("long", "short"),
        amounts_as_strings=True,
        min_order_size_source="info_endpoint",
    )

    def __init__(self, rest_client: Any = None, ws_client: Any = None):
        """Initialize the adapter.

        Args:
            rest_client: Existing PacificaClient instance to wrap.  If
                None, one is built lazily from config on first use (so
                that factory/capability lookups never touch API keys).
            ws_client: Existing WebSocket client.  If None, the process
                singleton from ``pacifica_ws_client.get_ws_client()`` is
                used lazily on first access.
        """
        self._rest = rest_client
        self._ws = ws_client

    # ------------------------------------------------------------------
    # Capabilities and underlying clients
    # ------------------------------------------------------------------

    @classmethod
    def capabilities(cls) -> ExchangeCapabilities:
        """Return Pacifica's static capability description."""
        return cls._CAPABILITIES

    @property
    def rest_client(self) -> Any:
        """Underlying PacificaClient (built lazily from config if needed)."""
        if self._rest is None:
            from ..config import config
            from ..pacifica_client import PacificaClient

            self._rest = PacificaClient(
                agent_wallet_private_key=config.pacifica_private_key,
                account_public_key=config.pacifica_public_key,
                testnet=config.testnet,
            )
        return self._rest

    @property
    def ws_client(self) -> Any:
        """Underlying WebSocket client (process singleton if not injected)."""
        if self._ws is None:
            from ..pacifica_ws_client import get_ws_client

            self._ws = get_ws_client()
        return self._ws

    # ------------------------------------------------------------------
    # Vocabulary conversion (the ONLY place these mappings may live)
    # ------------------------------------------------------------------

    @staticmethod
    def to_native_order_side(side: OrderSideInput) -> str:
        """Normalize an order side to the Pacifica wire value "bid"/"ask"."""
        return (
            "bid" if PacificaExchange._to_order_side(side) == OrderSide.BUY else "ask"
        )

    @staticmethod
    def from_native_order_side(value: str) -> OrderSide:
        """Convert a Pacifica wire order side back to OrderSide."""
        lowered = str(value).strip().lower()
        if lowered in ("bid", "buy"):
            return OrderSide.BUY
        if lowered in ("ask", "sell"):
            return OrderSide.SELL
        raise ValueError(f"Unknown Pacifica order side: {value!r}")

    @staticmethod
    def to_native_position_side(side: PositionSide) -> str:
        """Convert PositionSide to the Pacifica wire value "long"/"short"."""
        return "long" if PositionSide(side) == PositionSide.LONG else "short"

    @staticmethod
    def from_native_position_side(value: Optional[str]) -> PositionSide:
        """Normalize any Pacifica position-side spelling to PositionSide.

        Tolerates "long"/"short", order-side leakage ("bid"/"ask",
        "buy"/"sell") and any casing; mirrors the battle-tested logic in
        api_server._normalize_position_side.  Defaults to LONG.
        """
        if not value:
            return PositionSide.LONG
        lowered = str(value).strip().lower()
        if lowered in ("long", "bid", "buy"):
            return PositionSide.LONG
        if lowered in ("short", "ask", "sell"):
            return PositionSide.SHORT
        upper = str(value).strip().upper()
        return PositionSide.SHORT if upper == "SHORT" else PositionSide.LONG

    @staticmethod
    def amount_to_native(quantity: float) -> str:
        """Convert a numeric amount to Pacifica's string wire type."""
        return str(quantity)

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
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
        stop_loss: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Place an order with normalized vocabulary.

        Delegates to ``PacificaClient.place_order`` with the exact
        lowercase "buy"/"sell" and "market"/"limit" strings it requires;
        the native client then performs the final bid/ask and
        string-amount wire conversion.  ``reduce_only`` and
        ``client_order_id`` are forwarded only when set so legacy
        positional mocks keep matching.  ``stop_loss`` is NOT forwarded:
        Pacifica has no venue-side stop in this client, so the caller
        must keep local enforcement (``supports_venue_stops`` is False).

        Returns:
            Raw Pacifica acknowledgement dict
            (``{"success": bool, "data": {...}}`` shape).
        """
        side_str = self._to_order_side(side).value  # "buy" / "sell"
        type_str = self._to_order_type(order_type).value  # "market" / "limit"
        if stop_loss is not None:
            logger.warning(
                "Pacifica adapter cannot attach a venue stop for %s (stop %.6f); "
                "local stop enforcement only",
                symbol,
                stop_loss,
            )
        extra: Dict[str, Any] = {}
        if reduce_only:
            extra["reduce_only"] = True
        if client_order_id:
            extra["client_order_id"] = client_order_id

        if type_str == OrderType.LIMIT.value:
            if price is None:
                raise ValueError("Price is required for limit orders")
            return self.rest_client.place_order(
                symbol=symbol,
                side=side_str,
                quantity=quantity,
                order_type=type_str,
                price=price,
                **extra,
            )
        return self.rest_client.place_order(
            symbol=symbol,
            side=side_str,
            quantity=quantity,
            order_type=type_str,
            **extra,
        )

    def get_order_fill(
        self,
        symbol: str,
        order_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        requested_quantity: Optional[float] = None,
    ) -> OrderResult:
        """Best-effort fill lookup over Pacifica's order history.

        Pacifica has no per-order endpoint in this client, so the recent
        order history (``get_trades``) is scanned for the order id or the
        client order id.  Not found / transport error -> UNKNOWN.
        """
        if not order_id and not client_order_id:
            return OrderResult(
                status=OrderResultStatus.UNKNOWN.value, error="no order id"
            )
        try:
            rows = self.rest_client.get_trades(limit=100) or []
        except Exception as exc:  # noqa: BLE001 - lookup failure is "unknown"
            logger.warning(
                "Pacifica fill lookup failed for %s %s: %s", symbol, order_id, exc
            )
            return OrderResult(
                order_id=order_id,
                client_order_id=client_order_id,
                status=OrderResultStatus.UNKNOWN.value,
                error=str(exc),
            )
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = str(row.get("order_id") or row.get("id") or "")
            row_cid = str(row.get("client_order_id") or "")
            if not (
                (order_id and row_id == str(order_id))
                or (client_order_id and row_cid == str(client_order_id))
            ):
                continue
            filled = _first_float(
                row, ("filled_amount", "filled_quantity", "filled_size"), 0.0
            )
            price = _first_float(
                row,
                ("average_filled_price", "avg_fill_price", "average_price", "price"),
                0.0,
            )
            return OrderResult.from_fill_lookup(
                order_id=row_id or order_id,
                state=str(row.get("order_status") or row.get("status") or "unknown"),
                filled_quantity=filled,
                avg_fill_price=price if price > 0 else None,
                requested_quantity=requested_quantity,
                client_order_id=row_cid or client_order_id,
                raw=row,
            )
        return OrderResult(
            order_id=order_id,
            client_order_id=client_order_id,
            status=OrderResultStatus.UNKNOWN.value,
            error="order not found in history",
        )

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        """Cancel a single order (passthrough)."""
        return self.rest_client.cancel_order(symbol=symbol, order_id=order_id)

    def cancel_all_orders(
        self, symbol: Optional[str] = None, include_stops: bool = False
    ) -> Dict[str, Any]:
        """Cancel all open orders, optionally for one symbol (passthrough).

        ``include_stops`` is accepted for interface parity and ignored:
        Pacifica holds no venue-side stops to cancel.
        """
        return self.rest_client.cancel_all_orders(symbol=symbol)

    def get_positions(self) -> List[ExchangePosition]:
        """Return open positions normalized (side casing, ghost filter).

        Preserves the documented behavior scattered across call sites
        today: lowercase "long"/"short" sides are normalized and
        quantity==0 ghost positions are dropped.
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
        """Return the normalized account balance.

        Pacifica returns "balance" or "account_equity" as strings (there
        is no "equity" key) - same extraction the bot used inline before
        this adapter existed.
        """
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
        """Return the current funding snapshot for a symbol."""
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
    # Raw passthrough surface (legacy call sites)
    # ------------------------------------------------------------------

    def get_positions_raw(self) -> List[Dict[str, Any]]:
        """Positions as Pacifica-native dicts (deprecated legacy surface).

        Deprecated: prefer ``get_positions()``.  Kept for call sites that
        still parse native keys (grid lifecycle, position reconciler).
        """
        return self.rest_client.get_positions()

    def get_orders(self) -> List[Dict[str, Any]]:
        """Open orders as Pacifica-native dicts (legacy surface)."""
        return self.rest_client.get_orders()

    def get_trades(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Trade history as Pacifica-native dicts (legacy surface)."""
        return self.rest_client.get_trades(limit=limit)

    def get_candles(
        self,
        market: str,
        interval: str = "15m",
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """OHLCV candles (native client already standardizes the shape)."""
        return self.rest_client.get_candles(
            market=market,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

    def get_markets(self) -> List[Dict[str, Any]]:
        """Available markets (passthrough)."""
        return self.rest_client.get_markets()

    def get_market_data(self, symbol: str) -> Dict[str, Any]:
        """Current prices/market data for a symbol (passthrough)."""
        return self.rest_client.get_market_data(symbol)

    def get_instrument_info(self, symbol: str) -> Dict[str, Any]:
        """Instrument constraints: tick_size, lot_size, min_order_size."""
        return self.rest_client.get_instrument_info(symbol)

    def get_funding_history(self, symbol: str, limit: int = 8) -> List[Dict[str, Any]]:
        """Funding-rate history records (passthrough)."""
        return self.rest_client.get_funding_history(symbol, limit=limit)
