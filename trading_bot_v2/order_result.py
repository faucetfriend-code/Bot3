"""Normalized order acknowledgement / fill state shared by every exchange.

Every exchange client in this package returns a Pacifica-style ack dict
(``{"success": bool, "data": {...}, "error": ...}``) from ``place_order``.
That shape is kept for the existing dict consumers; this module adds a
typed view on top of it so call sites that make accounting decisions
(closing a position, recording a fill) can reason about ACCEPTED vs
FILLED vs REJECTED instead of a bare boolean.

Vocabulary
----------
* ``accepted``  - the exchange took the order (an order id exists or the
  venue returned its bare "success" ack).  Says nothing about fills.
* ``status``    - one of ``OrderResultStatus``: accepted / filled /
  partial / rejected / unknown.  ``filled`` and ``partial`` are only
  ever set from a fill lookup or an ack that carries fill data.
* ``success``   - legacy boolean, equal to ``accepted`` for acks and to
  ``status in (filled, partial)`` for fill lookups.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class OrderResultStatus(str, Enum):
    """Lifecycle state of an order as far as the bot can tell."""

    ACCEPTED = "accepted"
    FILLED = "filled"
    PARTIAL = "partial"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


_FILL_QTY_KEYS = ("filled_quantity", "filled_size", "filled_amount", "filledSize")
_FILL_PX_KEYS = ("avg_fill_price", "average_price", "averagePrice", "avg_price")
_TERMINAL_STATES = ("canceled", "cancelled", "rejected", "expired", "failed")
_OPEN_STATES = ("live", "open", "pending", "accepted", "new", "partially_filled")
_DONE_STATES = ("filled", "closed", "done")


def _first_float(data: Dict[str, Any], keys) -> Optional[float]:
    """Return the first parseable float among ``keys`` in ``data``."""
    for key in keys:
        value = data.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


@dataclass
class OrderResult:
    """Normalized outcome of an order request or fill lookup.

    Attributes:
        success: Legacy boolean consumed by dict-era call sites.
        accepted: The exchange acknowledged the order.
        order_id: Exchange order id, when known.
        client_order_id: Client order id that was sent (or echoed back).
        status: ``OrderResultStatus`` value.
        filled_quantity: Confirmed executed quantity in base units.
        avg_fill_price: Volume-weighted average fill price, when known.
        error: Exchange/transport error text on rejection.
        raw: The original ack or lookup payload.
    """

    success: bool = False
    accepted: bool = False
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    status: str = OrderResultStatus.UNKNOWN.value
    filled_quantity: float = 0.0
    avg_fill_price: Optional[float] = None
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def rejected(self) -> bool:
        """True when the exchange refused (or later cancelled) the order."""
        return self.status == OrderResultStatus.REJECTED.value

    @property
    def is_filled(self) -> bool:
        """True when the whole requested quantity is confirmed executed."""
        return self.status == OrderResultStatus.FILLED.value

    @property
    def has_fills(self) -> bool:
        """True when at least some quantity is confirmed executed."""
        return self.filled_quantity > 0 and self.status in (
            OrderResultStatus.FILLED.value,
            OrderResultStatus.PARTIAL.value,
        )

    @classmethod
    def from_ack(cls, ack: Any) -> "OrderResult":
        """Build a result from a Pacifica-style ack dict.

        Handles the three shapes the clients produce: the normal
        ``{"success": bool, "data": {"order_id": ...}}`` wrapper, the
        Pacifica bare ``"success"`` string normalized to
        ``{"success": True, "data": {}, "status": "success"}``, and the
        Blofin per-order rejection ``{"success": False, "error": ...}``.
        Anything that is not a dict is an unknown outcome, never a
        success.

        Args:
            ack: Raw acknowledgement returned by a client ``place_order``.

        Returns:
            OrderResult describing the ack.
        """
        if not isinstance(ack, dict):
            return cls(
                status=OrderResultStatus.UNKNOWN.value,
                error=f"unexpected ack type: {type(ack).__name__}",
                raw={"value": repr(ack)[:200]},
            )
        data = ack.get("data")
        if not isinstance(data, dict):
            data = {}
        order_id = data.get("order_id") or data.get("id") or ack.get("order_id")
        client_order_id = data.get("client_order_id") or ack.get("client_order_id")
        bare_success_ack = ack.get("status") == "success"
        accepted = bool(ack.get("success")) and (
            order_id is not None or bare_success_ack
        )
        filled = _first_float(data, _FILL_QTY_KEYS)
        price = _first_float(data, _FILL_PX_KEYS)
        if accepted:
            status = OrderResultStatus.ACCEPTED.value
            if filled and filled > 0:
                status = OrderResultStatus.FILLED.value
        elif ack.get("success") is False or ack.get("error"):
            status = OrderResultStatus.REJECTED.value
        else:
            status = OrderResultStatus.UNKNOWN.value
        error = ack.get("error") if not accepted else None
        if error is None and not accepted:
            error = "order not accepted"
        return cls(
            success=accepted,
            accepted=accepted,
            order_id=str(order_id) if order_id is not None else None,
            client_order_id=str(client_order_id) if client_order_id else None,
            status=status,
            filled_quantity=float(filled) if filled and filled > 0 else 0.0,
            avg_fill_price=price,
            error=str(error) if error is not None else None,
            raw=ack,
        )

    @classmethod
    def from_fill_lookup(
        cls,
        order_id: Optional[str],
        state: str,
        filled_quantity: float,
        avg_fill_price: Optional[float],
        requested_quantity: Optional[float] = None,
        client_order_id: Optional[str] = None,
        raw: Optional[Dict[str, Any]] = None,
    ) -> "OrderResult":
        """Build a result from an order-state / fills query.

        Args:
            order_id: Exchange order id that was looked up.
            state: Exchange state string (filled, partially_filled, live,
                canceled, rejected, unknown, ...), any casing.
            filled_quantity: Executed quantity in base units.
            avg_fill_price: VWAP of the fills, or None.
            requested_quantity: Original order size, used to tell a full
                fill from a partial one when the venue state is vague.
            client_order_id: Client order id, if known.
            raw: Original payload for diagnostics.

        Returns:
            OrderResult with status filled / partial / accepted /
            rejected / unknown.
        """
        lowered = str(state or "").strip().lower()
        filled = max(0.0, float(filled_quantity or 0.0))
        if filled > 0:
            complete = lowered in _DONE_STATES or (
                requested_quantity is not None
                and filled >= requested_quantity * (1 - 1e-9)
            )
            status = (
                OrderResultStatus.FILLED.value
                if complete
                else OrderResultStatus.PARTIAL.value
            )
        elif lowered in _TERMINAL_STATES:
            status = OrderResultStatus.REJECTED.value
        elif lowered in _OPEN_STATES:
            status = OrderResultStatus.ACCEPTED.value
        else:
            status = OrderResultStatus.UNKNOWN.value
        rejected = status == OrderResultStatus.REJECTED.value
        unknown = status == OrderResultStatus.UNKNOWN.value
        return cls(
            success=filled > 0,
            accepted=not rejected and not unknown,
            order_id=str(order_id) if order_id is not None else None,
            client_order_id=client_order_id,
            status=status,
            filled_quantity=filled,
            avg_fill_price=avg_fill_price if filled > 0 else None,
            error=f"order {lowered}" if rejected else None,
            raw=raw or {},
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return a dict in the legacy ack shape plus the typed fields."""
        return {
            "success": self.success,
            "accepted": self.accepted,
            "status": self.status,
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "filled_quantity": self.filled_quantity,
            "avg_fill_price": self.avg_fill_price,
            "error": self.error,
            "data": {
                "order_id": self.order_id,
                "client_order_id": self.client_order_id,
            },
        }
