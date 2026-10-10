"""
Position Reconciler - Compares exchange positions against local DB state.

Addresses gap H4 (position reconciliation): the exchange is treated as the
authoritative source of truth. This module detects and resolves drift
between the local database and the exchange:

  - Positions present in the DB but no longer on the exchange -> closed locally
  - Positions on the exchange but missing from the DB -> adopted/saved locally
  - Positions present in both but with mismatched side/quantity/entry price
    -> local values are corrected to match the exchange, a WARNING is logged,
       a POSITION_DISCREPANCY event is published, and a metric is incremented

Pacifica API quirks handled here (see CLAUDE.md):
  - Position sides come back lowercase: "long"/"short", sometimes "bid"/"ask"
  - Quantities are sometimes strings
  - Zero-quantity "ghost" positions must be filtered out

Usage:
    from trading_bot_v2.position_reconciler import PositionReconciler

    reconciler = PositionReconciler(db=db, event_bus=event_bus)
    report = reconciler.reconcile(exchange_positions, db_positions)
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from .event_system import EventBus, EventType, get_event_bus
from .metrics import metrics

# Relative tolerance for treating quantity/entry-price as "matching".
# Avoids false-positive discrepancies from floating point noise.
_QUANTITY_REL_TOL = 1e-6
_PRICE_REL_TOL = 1e-4


@dataclass
class ReconciliationReport:
    """Summary of a single reconciliation pass."""

    closed_locally: int = 0
    adopted_from_exchange: int = 0
    discrepancies_found: int = 0
    matched: int = 0
    errors: int = 0
    discrepancy_details: List[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the report to a plain dictionary."""
        return {
            "closed_locally": self.closed_locally,
            "adopted_from_exchange": self.adopted_from_exchange,
            "discrepancies_found": self.discrepancies_found,
            "matched": self.matched,
            "errors": self.errors,
            "discrepancy_details": list(self.discrepancy_details),
            "timestamp": self.timestamp,
        }


def _normalize_side(raw_side: Optional[str]) -> str:
    """
    Normalize a Pacifica position/order side to "LONG" or "SHORT".

    Pacifica returns "long"/"short" for positions, and occasionally
    "bid"/"ask" (order-style naming). Anything unrecognized defaults to
    "LONG" to match existing normalization behavior in trading_bot.py.
    """
    if not raw_side:
        return "LONG"
    side = raw_side.upper()
    if side in ("LONG", "BID"):
        return "LONG"
    if side in ("SHORT", "ASK"):
        return "SHORT"
    return side


def _safe_float(value: Any, default: float = 0.0) -> float:
    """Coerce a value (possibly a string, per Pacifica API) to float safely."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# Keys a raw position dict may carry its size under.  Local DB rows use
# ``quantity``; raw Pacifica positions use ``amount`` (a string); Blofin's
# bot-native dicts carry both.  Checked in order; the first parseable wins.
_QUANTITY_KEYS = ("quantity", "amount", "size")


def position_quantity(pos: Dict[str, Any]) -> float:
    """Return a position dict's size, whichever key the source used.

    Args:
        pos: Raw exchange or local DB position dict.

    Returns:
        The size as a float, or 0.0 when no key carries a parseable value.
    """
    for key in _QUANTITY_KEYS:
        value = pos.get(key)
        if value in (None, ""):
            continue
        parsed = _safe_float(value, default=0.0)
        if parsed != 0.0:
            return parsed
    return 0.0


def _filter_real_positions(
    positions: Optional[List[Dict[str, Any]]],
) -> Dict[str, Dict[str, Any]]:
    """
    Normalize a list of raw position dicts into a symbol-keyed map.

    Filters out zero-quantity ghost positions and normalizes symbol (upper)
    and side (LONG/SHORT). If multiple entries exist for the same symbol
    (e.g. hedge mode), the last non-zero one wins - single-sided position
    tracking is assumed elsewhere in the codebase (save_position keys on
    symbol + side).
    """
    result: Dict[str, Dict[str, Any]] = {}
    for pos in positions or []:
        try:
            symbol = pos.get("symbol")
            if not symbol:
                continue
            symbol = str(symbol).upper()

            quantity = position_quantity(pos)
            if quantity == 0:
                continue

            side = _normalize_side(pos.get("side"))
            key = f"{symbol}:{side}"
            result[key] = {
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "entry_price": _safe_float(pos.get("entry_price", 0)),
            }
        except Exception as e:
            logger.warning(
                f"PositionReconciler: skipping malformed position {pos}: {e}"
            )
    return result


class PositionReconciler:
    """
    Compares exchange (authoritative) and local DB position state.

    The reconciler only performs comparison and local database
    correction. It does not place, cancel, or modify any exchange orders -
    RiskManager and the exchange client remain the only components that
    touch live orders.
    """

    def __init__(self, db, event_bus: Optional[EventBus] = None):
        """
        Initialize the reconciler.

        Args:
            db: DatabaseManager instance (must expose get_positions,
                save_position, close_position).
            event_bus: EventBus instance. Defaults to the global singleton.
        """
        self.db = db
        self.event_bus = event_bus or get_event_bus()

    def reconcile(
        self,
        exchange_positions: Optional[List[Dict[str, Any]]],
        db_positions: Optional[List[Dict[str, Any]]],
    ) -> ReconciliationReport:
        """
        Reconcile local DB positions against the exchange's authoritative state.

        Args:
            exchange_positions: Raw position dicts from the exchange client.
                An empty list means the exchange confirmed zero open
                positions (the caller is responsible for distinguishing
                this from an API failure - this method assumes the input
                is trustworthy).
            db_positions: Raw position dicts from Database.get_positions().

        Returns:
            ReconciliationReport summarizing actions taken. This method
            never raises; per-position errors are caught and logged.
        """
        report = ReconciliationReport()

        try:
            exchange_map = _filter_real_positions(exchange_positions)
            db_map = _filter_real_positions(db_positions)
        except Exception as e:
            logger.error(f"PositionReconciler: failed to normalize positions: {e}")
            report.errors += 1
            return report

        all_keys = set(exchange_map.keys()) | set(db_map.keys())

        for key in all_keys:
            try:
                exchange_pos = exchange_map.get(key)
                db_pos = db_map.get(key)

                if exchange_pos is None and db_pos is not None:
                    # In DB but not on exchange -> close locally (exchange wins)
                    self.db.close_position(db_pos["symbol"], db_pos["side"])
                    report.closed_locally += 1
                    logger.info(
                        f"PositionReconciler: closed stale local position "
                        f"{db_pos['symbol']} {db_pos['side']} (absent from exchange)"
                    )
                    continue

                if exchange_pos is not None and db_pos is None:
                    # On exchange but missing locally -> adopt it
                    self.db.save_position(
                        {
                            "symbol": exchange_pos["symbol"],
                            "side": exchange_pos["side"],
                            "quantity": exchange_pos["quantity"],
                            "entry_price": exchange_pos["entry_price"],
                            "current_price": exchange_pos["entry_price"],
                            "unrealized_pnl": 0.0,
                            "asset_class": "perpetual",
                            "opened_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    report.adopted_from_exchange += 1
                    logger.info(
                        f"PositionReconciler: adopted exchange position "
                        f"{exchange_pos['symbol']} {exchange_pos['side']} "
                        f"(missing from local DB)"
                    )
                    continue

                # Present in both - check for discrepancies
                if exchange_pos is not None and db_pos is not None:
                    mismatches = self._find_mismatches(exchange_pos, db_pos)
                    if not mismatches:
                        report.matched += 1
                        continue

                    report.discrepancies_found += 1
                    detail = (
                        f"{exchange_pos['symbol']} {exchange_pos['side']}: "
                        + "; ".join(mismatches)
                    )
                    report.discrepancy_details.append(detail)
                    logger.warning(f"PositionReconciler: discrepancy - {detail}")

                    for mismatch_type in self._mismatch_types(exchange_pos, db_pos):
                        metrics.record_reconciliation_discrepancy(mismatch_type)

                    # Correct local DB to match exchange (exchange is authoritative)
                    self.db.save_position(
                        {
                            "symbol": exchange_pos["symbol"],
                            "side": exchange_pos["side"],
                            "quantity": exchange_pos["quantity"],
                            "entry_price": exchange_pos["entry_price"],
                            "current_price": exchange_pos["entry_price"],
                            "unrealized_pnl": 0.0,
                            "asset_class": "perpetual",
                            "opened_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )

                    self.event_bus.publish_event(
                        event_type=EventType.POSITION_DISCREPANCY,
                        data={
                            "symbol": exchange_pos["symbol"],
                            "side": exchange_pos["side"],
                            "message": detail,
                            "discrepancy_count": len(mismatches),
                        },
                        source="position_reconciler",
                    )

            except Exception as e:
                report.errors += 1
                logger.error(
                    f"PositionReconciler: error reconciling position key={key}: {e}"
                )

        metrics.record_reconciliation_run()
        logger.info(
            "PositionReconciler: run complete - "
            f"closed_locally={report.closed_locally}, "
            f"adopted={report.adopted_from_exchange}, "
            f"discrepancies={report.discrepancies_found}, "
            f"matched={report.matched}, errors={report.errors}"
        )
        return report

    @staticmethod
    def _find_mismatches(
        exchange_pos: Dict[str, Any], db_pos: Dict[str, Any]
    ) -> List[str]:
        """Return human-readable descriptions of field mismatches."""
        mismatches: List[str] = []

        if exchange_pos["side"] != db_pos["side"]:
            mismatches.append(
                f"side mismatch (exchange={exchange_pos['side']}, db={db_pos['side']})"
            )

        ex_qty = exchange_pos["quantity"]
        db_qty = db_pos["quantity"]
        if (
            ex_qty == 0
            or abs(ex_qty - db_qty) / max(abs(ex_qty), 1e-9) > _QUANTITY_REL_TOL
        ):
            mismatches.append(f"quantity mismatch (exchange={ex_qty}, db={db_qty})")

        ex_price = exchange_pos["entry_price"]
        db_price = db_pos["entry_price"]
        if ex_price > 0 and abs(ex_price - db_price) / ex_price > _PRICE_REL_TOL:
            mismatches.append(
                f"entry_price mismatch (exchange={ex_price}, db={db_price})"
            )

        return mismatches

    @staticmethod
    def _mismatch_types(
        exchange_pos: Dict[str, Any], db_pos: Dict[str, Any]
    ) -> List[str]:
        """Return machine-readable discrepancy type labels for metrics."""
        types: List[str] = []

        if exchange_pos["side"] != db_pos["side"]:
            types.append("side_mismatch")

        ex_qty = exchange_pos["quantity"]
        db_qty = db_pos["quantity"]
        if (
            ex_qty == 0
            or abs(ex_qty - db_qty) / max(abs(ex_qty), 1e-9) > _QUANTITY_REL_TOL
        ):
            types.append("quantity_mismatch")

        ex_price = exchange_pos["entry_price"]
        db_price = db_pos["entry_price"]
        if ex_price > 0 and abs(ex_price - db_price) / ex_price > _PRICE_REL_TOL:
            types.append("entry_price_mismatch")

        return types
