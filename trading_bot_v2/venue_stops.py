"""Shared vocabulary and helpers for exchange-side (venue) stop protection.

Live-readiness audit finding T4 (2026-09-08): every signal carries a
``stop_loss`` that the risk manager sizes from, but nothing ever sent it
to an exchange.  This module holds the small pieces that the entry path
(``trading_bot.py``), the trailing-stop coordinator
(``migrated_position_manager.py``), the repair sweep and the exchange
adapters all share, so the state vocabulary lives in exactly one place.

Venue stop state (``positions.venue_stop_state``):

* ``attached``    - the stop rode on the entry order (Blofin slTriggerPrice)
  and the venue now holds it as a TP/SL row.
* ``standalone``  - a reduce-only TP/SL order placed separately.
* ``missing``     - the venue has NO stop for this position; only the local
  loop check protects it.  Logged at ERROR every loop.
* ``unsupported`` - the exchange has no venue-side stop orders (Pacifica);
  the local loop check is the only protection by design.
"""

from typing import Any, Dict, Iterable, Optional

STOP_STATE_ATTACHED = "attached"
STOP_STATE_STANDALONE = "standalone"
STOP_STATE_MISSING = "missing"
STOP_STATE_UNSUPPORTED = "unsupported"

PROTECTED_STATES = (STOP_STATE_ATTACHED, STOP_STATE_STANDALONE)
LOCAL_ONLY_STATES = (STOP_STATE_MISSING, STOP_STATE_UNSUPPORTED)

STOP_FAILURE_POLICY_CLOSE = "close"
STOP_FAILURE_POLICY_LOCAL = "local"
STOP_FAILURE_POLICIES = (STOP_FAILURE_POLICY_CLOSE, STOP_FAILURE_POLICY_LOCAL)


def venue_stops_supported(exchange: Any) -> bool:
    """Return True when the adapter advertises venue-side stop orders.

    Tolerates duck-typed stand-ins without ``capabilities()`` (treated as
    unsupported) so test fixtures and legacy raw clients never trip it.

    Args:
        exchange: Exchange adapter (or anything else).

    Returns:
        True only when ``capabilities().supports_venue_stops`` is truthy.
    """
    caps_fn = getattr(exchange, "capabilities", None)
    if not callable(caps_fn):
        return False
    try:
        caps = caps_fn()
    except Exception:  # noqa: BLE001 - a broken stand-in is "unsupported"
        return False
    return bool(getattr(caps, "supports_venue_stops", False))


def position_side_lower(side: Any) -> str:
    """Normalize any side spelling to lowercase "long"/"short".

    Accepts OrderSide / PositionSide enums, "buy"/"sell"/"bid"/"ask" and
    "long"/"short" in any casing.  A BUY opens a long.

    Args:
        side: Side value in any of the accepted vocabularies.

    Returns:
        "long" or "short".
    """
    value = getattr(side, "value", side)
    lowered = str(value).strip().lower()
    if lowered in ("short", "sell", "ask"):
        return "short"
    return "long"


def close_order_side(side: Any) -> str:
    """Return the lowercase order side that CLOSES a position side."""
    return "sell" if position_side_lower(side) == "long" else "buy"


def stop_hit(
    side: Any, stop_price: Optional[float], current_price: Optional[float]
) -> bool:
    """Return True when ``current_price`` has crossed the protective stop.

    Args:
        side: Position side in any accepted vocabulary.
        stop_price: Stored stop level; None/0 means "no stop".
        current_price: Latest traded price; None/0 means "unknown".

    Returns:
        True when a long trades at or below, or a short at or above, the stop.
    """
    if not stop_price or not current_price:
        return False
    if position_side_lower(side) == "long":
        return float(current_price) <= float(stop_price)
    return float(current_price) >= float(stop_price)


def find_stop_row(
    rows: Iterable[Dict[str, Any]], side: Any
) -> Optional[Dict[str, Any]]:
    """Pick the pending TP/SL row that protects ``side`` from ``list_stops``.

    A row protects a long when it closes with a sell (or carries
    positionSide "long"); net-mode rows carry positionSide "net" and are
    matched on the close-order side alone.  Rows carrying a stop-loss
    trigger win over take-profit-only rows.

    Args:
        rows: Normalized rows from ``ExchangeClient.list_stops``.
        side: Position side in any accepted vocabulary.

    Returns:
        The matching row, or None.
    """
    wanted_close = close_order_side(side)
    wanted_pos = position_side_lower(side)
    candidates = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        pos_side = str(row.get("position_side") or "net").lower()
        if pos_side in ("long", "short") and pos_side != wanted_pos:
            continue
        row_side = str(row.get("side") or "").lower()
        if row_side and row_side != wanted_close:
            continue
        candidates.append(row)
    candidates.sort(key=lambda r: 0 if r.get("sl_trigger_price") else 1)
    return candidates[0] if candidates else None


def stop_move_pct(old_price: Optional[float], new_price: float) -> float:
    """Absolute percentage move between two stop levels (inf when no old)."""
    if not old_price:
        return float("inf")
    return abs(float(new_price) - float(old_price)) / abs(float(old_price)) * 100.0
