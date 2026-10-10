"""Single writer for closed-trade records in the ``trades`` table.

``record_closed_trade`` is the ONLY function that writes a closed,
strategy-tagged round trip.  Every close path calls it once per closed
quantity (a partial close records the closed portion).  It is pure
bookkeeping: it never sends, cancels or modifies an order, and it never
raises - a failure is logged at ERROR and the caller's close proceeds.

Record shape (one ``trades`` row with ``status='closed'``):
    symbol, side ('long'/'short'), strategy (canonical tag), quantity,
    entry_price, exit_price, entry_time, exit_time, pnl (gross minus
    known fees; funding is NOT included), commission (fees, NULL when
    unknown), funding_pnl (NULL when unknown), close_reason, exchange,
    network ('testnet'/'mainnet'), exit_price_source ('fill' or
    'estimate'), regime (regime at entry, when known) and close_key.

Strategy tags:
    The canonical format is the ``StrategyType`` value: lower snake case
    (``mean_reversion``).  ``canonical_strategy_tag`` normalises enum
    members, enum names (``MEAN_REVERSION``), display names and the
    underscore-free keys used by the regime review.  A position whose
    owner is not known is tagged ``unknown`` (or ``migrated`` when the
    migrated position manager closed it); neither is a ``StrategyType``
    value, so no strategy's Kelly history can ever match them.

Idempotency:
    ``close_key`` identifies one close of one position episode.  It is
    built from exchange, network, account, symbol, side, the positions
    row identity (row id, opened_at, entry_order_id, entry price) and a
    close reference ('final', or 'partial:<closing order id>').  The row
    is inserted only when no row with that key exists, so a retried
    close, a restart or a sync pass that rediscovers an already recorded
    close cannot add a second row.
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from loguru import logger

from .trade_store import get_active_exchange_name

UNKNOWN_STRATEGY_TAG = "unknown"
MIGRATED_STRATEGY_TAG = "migrated"

EXIT_PRICE_FILL = "fill"
EXIT_PRICE_ESTIMATE = "estimate"

NETWORK_TESTNET = "testnet"
NETWORK_MAINNET = "mainnet"

# Close reasons used by the call sites (free text is accepted too).
REASON_LOCAL_STOP = "local_stop"
REASON_POSITION_GONE = "position_gone_at_sync"
REASON_TAKE_PROFIT_PARTIAL = "take_profit_partial"

CLOSE_REF_FINAL = "final"

_TRUE_VALUES = ("true", "1", "yes", "on")


def _strategy_lookup() -> Dict[str, str]:
    """Map underscore-free strategy keys to their canonical tag."""
    from ..config import StrategyType

    return {member.value.replace("_", ""): member.value for member in StrategyType}


def canonical_strategy_tag(value: Any, default: str = UNKNOWN_STRATEGY_TAG) -> str:
    """Normalise a strategy identifier to the canonical tag.

    Args:
        value: StrategyType member, its value or name, a display name
            ("Mean Reversion") or an underscore-free key
            ("meanreversion").  None or blank gives ``default``.
        default: Tag returned when ``value`` carries no name.

    Returns:
        Lower snake case tag; the ``StrategyType`` value when the input
        names a known strategy.
    """
    raw = getattr(value, "value", value)
    if raw is None:
        return default
    text = "_".join(str(raw).strip().lower().replace("-", " ").split())
    text = "_".join(part for part in text.split("_") if part)
    if not text:
        return default
    return _strategy_lookup().get(text.replace("_", ""), text)


def is_strategy_tag(tag: Any) -> bool:
    """True when ``tag`` is the canonical tag of a real strategy."""
    return str(tag) in set(_strategy_lookup().values())


def current_network(exchange: Optional[str] = None) -> str:
    """Return 'testnet' or 'mainnet' for the active exchange.

    Reads the same switches the clients use (TESTNET for Pacifica,
    BLOFIN_DEMO for Blofin; both default to the test environment).  It
    only reads them - mode handling itself is unchanged.

    Args:
        exchange: Exchange name; the active one when omitted.

    Returns:
        ``NETWORK_TESTNET`` or ``NETWORK_MAINNET``.
    """
    name = (exchange or get_active_exchange_name()).strip().lower()
    if name == "blofin":
        demo = os.getenv("BLOFIN_DEMO", "true").strip().lower()
        return NETWORK_TESTNET if demo in _TRUE_VALUES else NETWORK_MAINNET
    try:
        from ..config import config

        testnet = bool(config.testnet)
    except Exception:  # noqa: BLE001 - fall back to the env default
        testnet = os.getenv("TESTNET", "true").strip().lower() in _TRUE_VALUES
    return NETWORK_TESTNET if testnet else NETWORK_MAINNET


def _to_float(value: Any) -> Optional[float]:
    """Convert to float; None when missing or not numeric."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _side_lower(side: Any) -> Optional[str]:
    """Normalise a position side to 'long' / 'short' (None if unknown)."""
    text = str(getattr(side, "name", side) or "").strip().lower()
    if text in ("long", "buy", "bid"):
        return "long"
    if text in ("short", "sell", "ask"):
        return "short"
    return None


def _iso_utc(value: Any) -> Optional[str]:
    """Render a timestamp as an ISO-8601 UTC string (None if unusable)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        seconds = value / 1000.0 if value > 1e12 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    if isinstance(value, datetime):
        stamp = value
    else:
        try:
            stamp = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat()


def _load_position(db: Any, symbol: str, side: str) -> Optional[Dict[str, Any]]:
    """Read the open positions row for ``symbol``/``side`` (None if absent)."""
    reader = getattr(db, "get_position", None)
    if not callable(reader):
        return None
    for spelling in (side.upper(), side.lower()):
        row = reader(symbol, spelling)
        if isinstance(row, dict):
            return row
    return None


def build_close_key(
    exchange: str,
    network: str,
    account_id: str,
    symbol: str,
    side: str,
    position: Optional[Dict[str, Any]],
    entry_time: Optional[str],
    entry_price: float,
    close_ref: str,
) -> str:
    """Build the idempotency key for one close of one position episode.

    Args:
        exchange: Exchange tag.
        network: 'testnet' or 'mainnet'.
        account_id: Owning account id.
        symbol: Trading symbol.
        side: 'long' or 'short'.
        position: The positions row, when one exists.
        entry_time: Entry time used when there is no positions row.
        entry_price: Entry price used when there is no positions row.
        close_ref: 'final' or 'partial:<closing order id>'.

    Returns:
        A stable, human-readable key.
    """
    if position and position.get("id") is not None:
        identity = "pos{}|{}|{}|{!r}".format(
            position.get("id"),
            position.get("opened_at") or "",
            position.get("entry_order_id") or "",
            _to_float(position.get("entry_price")),
        )
    else:
        identity = "nopos|{}|{!r}".format(entry_time or "", entry_price)
    parts = (exchange, network, account_id, symbol, side, identity, close_ref)
    return "|".join(str(part) for part in parts)


def _resolve_exit(
    exit_price: Optional[float],
    exit_price_is_fill: bool,
    position: Optional[Dict[str, Any]],
    entry_price: float,
) -> Tuple[float, str]:
    """Pick the exit price and say whether it is a fill or an estimate."""
    given = _to_float(exit_price)
    if given is not None and given > 0:
        source = EXIT_PRICE_FILL if exit_price_is_fill else EXIT_PRICE_ESTIMATE
        return given, source
    last = _to_float((position or {}).get("current_price"))
    if last is not None and last > 0:
        return last, EXIT_PRICE_ESTIMATE
    return entry_price, EXIT_PRICE_ESTIMATE


def _build_row(
    fields: Dict[str, Any], position: Optional[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Assemble the trades row; None when the close cannot be priced."""
    pos = position or {}
    side = _side_lower(fields["side"])
    quantity = _to_float(fields.get("quantity"))
    if quantity is None:
        quantity = _to_float(pos.get("quantity"))
    entry_price = _to_float(fields.get("entry_price")) or _to_float(
        pos.get("entry_price")
    )
    if side is None or not quantity or quantity <= 0 or not entry_price:
        logger.error(
            f"Closed trade NOT recorded for {fields['symbol']} {fields['side']} "
            f"({fields['close_reason']}): side/quantity/entry price unknown"
        )
        return None
    exit_price, source = _resolve_exit(
        fields.get("exit_price"),
        bool(fields.get("exit_price_is_fill")),
        pos,
        entry_price,
    )
    direction = 1.0 if side == "long" else -1.0
    fees = _to_float(fields.get("fees"))
    funding = _to_float(fields.get("funding"))
    if funding is None and not fields.get("partial"):
        funding = _to_float(pos.get("funding_pnl"))
    exit_time = (
        _iso_utc(fields.get("exit_time")) or datetime.now(timezone.utc).isoformat()
    )
    entry_time = (
        _iso_utc(fields.get("entry_time"))
        or _iso_utc(pos.get("opened_at"))
        or exit_time
    )
    owner = fields.get("strategy") or pos.get("strategy")
    default_tag = str(fields.get("default_tag") or UNKNOWN_STRATEGY_TAG)
    return {
        "symbol": fields["symbol"],
        "side": side,
        "quantity": quantity,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "pnl": (exit_price - entry_price) * quantity * direction - (fees or 0.0),
        "commission": fees,
        "funding_pnl": funding,
        "strategy": canonical_strategy_tag(owner, default=default_tag),
        "regime": fields.get("regime") or pos.get("entry_regime"),
        "close_reason": str(fields["close_reason"]),
        "exit_price_source": source,
    }


def record_closed_trade(
    db: Any,
    *,
    symbol: str,
    side: Any,
    close_reason: str,
    quantity: Optional[float] = None,
    exit_price: Optional[float] = None,
    exit_price_is_fill: bool = False,
    position: Optional[Dict[str, Any]] = None,
    entry_price: Optional[float] = None,
    entry_time: Any = None,
    exit_time: Any = None,
    strategy: Any = None,
    default_tag: str = UNKNOWN_STRATEGY_TAG,
    regime: Optional[str] = None,
    fees: Optional[float] = None,
    funding: Optional[float] = None,
    partial: bool = False,
    close_ref: Optional[str] = None,
    exchange: Optional[str] = None,
    network: Optional[str] = None,
    account_id: str = "sub_1",
) -> Optional[int]:
    """Record one closed trade; the single writer for every close path.

    Missing facts are read from the open ``positions`` row (quantity,
    entry price, entry time, owning strategy, funding).  Never raises.

    Args:
        db: DatabaseManager (needs ``save_closed_trade``; ``get_position``
            is used when ``position`` is not supplied).
        symbol: Trading symbol.
        side: Position side (any spelling of long/short).
        close_reason: Why the position closed (e.g. 'local_stop').
        quantity: Quantity closed by THIS close; the row quantity when
            omitted.
        exit_price: Exit price; the row's last price when omitted.
        exit_price_is_fill: True only when ``exit_price`` is a confirmed
            fill price.  Anything else is stored as an estimate.
        position: The positions row, when the caller already holds it.
        entry_price: Entry price override.
        entry_time: Entry time override.
        exit_time: Exit time; now (UTC) when omitted.
        strategy: Owning strategy; the row's tag when omitted.
        default_tag: Tag used when no owner is known.
        regime: Regime at entry; the row's ``entry_regime`` when omitted.
        fees: Fees paid, when known (stored in ``commission``).
        funding: Funding P&L, when known (stored separately).
        partial: True for a partial close.
        close_ref: Distinguishes closes of one episode; for a partial
            close pass the closing order id.
        exchange: Exchange tag; the active exchange when omitted.
        network: 'testnet'/'mainnet'; detected when omitted.
        account_id: Owning account id.

    Returns:
        Row id of the new record, or None when nothing was inserted
        (already recorded, not recordable, or the write failed).
    """
    try:
        if position is None:
            position = _load_position(db, symbol, str(_side_lower(side) or side))
        fields: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "close_reason": close_reason,
            "quantity": quantity,
            "exit_price": exit_price,
            "exit_price_is_fill": exit_price_is_fill,
            "entry_price": entry_price,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "strategy": strategy,
            "default_tag": default_tag,
            "regime": regime,
            "fees": fees,
            "funding": funding,
            "partial": partial,
        }
        row = _build_row(fields, position)
        if row is None:
            return None
        row["exchange"] = (exchange or get_active_exchange_name()).strip().lower()
        row["network"] = network or current_network(row["exchange"])
        ref = close_ref or CLOSE_REF_FINAL
        if partial:
            ref = f"partial:{close_ref or row['exit_time']}"
        row["close_key"] = build_close_key(
            row["exchange"],
            row["network"],
            account_id,
            symbol,
            row["side"],
            position,
            row["entry_time"],
            row["entry_price"],
            ref,
        )
        row_id: Optional[int] = db.save_closed_trade(row, account_id=account_id)
        if row_id is None:
            logger.info(f"Closed trade already recorded: {row['close_key']}")
            return None
        logger.info(
            f"Closed trade recorded: {symbol} {row['side']} {row['quantity']:.6f} "
            f"[{row['strategy']}] {close_reason} pnl={row['pnl']:.4f} "
            f"exit={row['exit_price']} ({row['exit_price_source']})"
        )
        return row_id
    except Exception as exc:  # noqa: BLE001 - recording must never break a close
        logger.error(
            f"Closed trade NOT recorded for {symbol} {side} ({close_reason}): "
            f"{type(exc).__name__}: {exc}"
        )
        return None


def record_position_gone(
    db: Any,
    symbol: str,
    side: Any,
    *,
    position: Optional[Dict[str, Any]] = None,
    quantity: Optional[float] = None,
    entry_price: Optional[float] = None,
    estimated_exit_price: Optional[float] = None,
) -> Optional[int]:
    """Record a position the exchange no longer reports (sync paths).

    The bot did not send the closing order (venue stop or take profit
    fill, liquidation, manual close on the venue), so the exit price is
    always stored as an ESTIMATE: ``estimated_exit_price`` when given,
    else the last price on the positions row.  Call it BEFORE the local
    row is deleted.  Delegates to ``record_closed_trade``; never raises.

    Args:
        db: DatabaseManager.
        symbol: Trading symbol.
        side: Position side.
        position: Full positions row, when the caller holds it.
        quantity: Fallback quantity when no row can be read.
        entry_price: Fallback entry price when no row can be read.
        estimated_exit_price: A current price, if the caller has one.

    Returns:
        Row id of the new record, or None.
    """
    return record_closed_trade(
        db,
        symbol=symbol,
        side=side,
        close_reason=REASON_POSITION_GONE,
        position=position,
        quantity=quantity,
        entry_price=entry_price,
        exit_price=estimated_exit_price,
        exit_price_is_fill=False,
    )
