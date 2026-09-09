"""Size exit orders from the exchange's CURRENT position, not local memory.

Every close path in the bot used to send the locally remembered quantity
as an ordinary opposite-side order.  If the exchange stop, a manual
action, or a previous partial close had already reduced the position,
that order flipped the book into reverse exposure.  This module gives
the close paths one shared answer to "how much is actually still open?"
so they can clamp to it (and skip the order entirely when flat).

``None`` means "could not determine" (transport failure, unrecognized
payload).  Callers must treat that as unknown - never as flat - and fall
back to the local quantity WITH ``reduce_only=True`` so the exchange
enforces the clamp instead.
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)

_QTY_KEYS = ("quantity", "amount", "size", "qty", "position_size")
_LONG_WORDS = ("long", "buy", "bid")
_SHORT_WORDS = ("short", "sell", "ask")


def normalize_symbol(symbol: Any) -> str:
    """Uppercase a symbol and strip a trailing -PERP / -USDT suffix."""
    text = str(symbol or "").upper()
    for suffix in ("-PERP", "-USDT", "-USD"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def normalize_position_side(side: Any) -> Optional[str]:
    """Map any side spelling to "long" / "short" (None if unrecognized)."""
    if side is None:
        return None
    value = getattr(side, "value", side)
    lowered = str(value).strip().lower()
    if any(word in lowered for word in _SHORT_WORDS):
        return "short"
    if any(word in lowered for word in _LONG_WORDS):
        return "long"
    return None


def _extract_position(pos: Any) -> Tuple[str, Optional[str], float]:
    """Return (symbol, side, quantity) from a dict or dataclass position."""
    if isinstance(pos, dict):
        symbol = pos.get("symbol", "")
        side = pos.get("side")
        quantity = 0.0
        for key in _QTY_KEYS:
            if pos.get(key) not in (None, ""):
                try:
                    quantity = abs(float(pos[key]))
                    break
                except (TypeError, ValueError):
                    continue
        return normalize_symbol(symbol), normalize_position_side(side), quantity
    symbol = getattr(pos, "symbol", "")
    side = getattr(pos, "side", None)
    try:
        quantity = abs(float(getattr(pos, "quantity", 0.0) or 0.0))
    except (TypeError, ValueError):
        quantity = 0.0
    return normalize_symbol(symbol), normalize_position_side(side), quantity


def remaining_exchange_quantity(
    client: Any, symbol: str, side: str
) -> Optional[float]:
    """Return the open quantity the exchange reports for ``symbol``/``side``.

    Args:
        client: Anything with ``get_positions()`` - a native client (dict
            positions) or an ``ExchangeClient`` adapter (dataclasses).
        symbol: Bot symbol ("BTC").
        side: Position side in any spelling ("long", "LONG", "buy", ...).

    Returns:
        The summed open quantity (0.0 when confirmed flat), or None when
        the position list could not be fetched or parsed.
    """
    wanted_side = normalize_position_side(side)
    if wanted_side is None:
        return None
    try:
        positions = client.get_positions()
    except Exception as exc:  # noqa: BLE001 - any transport failure is "unknown"
        logger.warning("Could not fetch positions for %s close sizing: %s", symbol, exc)
        return None
    if not isinstance(positions, (list, tuple)):
        return None
    wanted_symbol = normalize_symbol(symbol)
    total = 0.0
    for pos in positions:
        p_symbol, p_side, p_qty = _extract_position(pos)
        if p_symbol == wanted_symbol and p_side == wanted_side:
            total += p_qty
    return total


@dataclass
class ClosePlan:
    """What a close path should send to the exchange.

    Attributes:
        quantity: Quantity to submit (0.0 when nothing should be sent).
        exchange_quantity: What the exchange reported, or None if unknown.
        already_flat: The exchange confirmed there is nothing to close.
        clamped: The local quantity exceeded the exchange quantity.
    """

    quantity: float
    exchange_quantity: Optional[float]
    already_flat: bool
    clamped: bool


def plan_close_quantity(
    client: Any, symbol: str, side: str, local_quantity: float
) -> ClosePlan:
    """Clamp a local close quantity to what the exchange still holds.

    Args:
        client: Position source (see ``remaining_exchange_quantity``).
        symbol: Bot symbol.
        side: Position side being closed.
        local_quantity: Quantity the bot believes is open.

    Returns:
        ClosePlan.  When the exchange cannot be read the plan carries the
        local quantity unchanged and ``exchange_quantity=None``; callers
        must still send ``reduce_only=True``.
    """
    local = max(0.0, float(local_quantity or 0.0))
    remaining = remaining_exchange_quantity(client, symbol, side)
    if remaining is None:
        return ClosePlan(local, None, False, False)
    if remaining <= 0.0:
        return ClosePlan(0.0, 0.0, True, local > 0.0)
    if local > remaining:
        return ClosePlan(remaining, remaining, False, True)
    return ClosePlan(local, remaining, False, False)
