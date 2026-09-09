"""
GridLifecycleManager
====================

Authoritative state machine for Grid Trading lifecycle.

Responsibilities (HARD GUARANTEES):
- Enforces exactly ONE grid per symbol
- Owns grid state (ACTIVE / DISABLED / EMERGENCY_EXIT)
- Cancels all grid orders on regime change
- Flattens positions on emergency stop
- Single integration point for TradingBot
- ACTIVE MONITORING: Track fills, calculate P&L, detect round-trips

This module intentionally contains NO strategy logic and NO sizing logic.
Sizing MUST come from RiskManager.
"""

from enum import Enum
from typing import Dict, Any, List, Optional, Set
import json
import os
import time
from datetime import datetime, timedelta, timezone
from loguru import logger
from dataclasses import dataclass, field

from .config import config
from .exit_sizing import (
    ClosePlan,
    normalize_position_side,
    plan_close_quantity,
    remaining_exchange_quantity,
)
from .order_result import OrderResult


def _send_telegram_error_alert(error_type: str, message: str, context: str) -> bool:
    """Best-effort Telegram alert; shares the migrated manager's hook.

    Imported lazily so this module stays importable without httpx and so
    tests can monkeypatch this name.  Returns False when Telegram is
    disabled; transport failures are logged by the hook, never raised.
    """
    from .migrated_position_manager import _send_telegram_error_alert as _send

    return _send(error_type, message, context)


def _max_flatten_attempts() -> int:
    """GRID_FLATTEN_MAX_ATTEMPTS env var (default 5, minimum 1)."""
    raw = os.getenv("GRID_FLATTEN_MAX_ATTEMPTS", "").strip()
    try:
        return max(1, int(raw)) if raw else 5
    except ValueError:
        return 5


@dataclass
class CloseOutcome:
    """Result of one reduce-only close attempt against a grid position.

    Attributes:
        status: "flat" (exchange already flat, nothing sent), "closed"
            (confirmed fully executed), "partial", "rejected",
            "unconfirmed" (accepted but no fill evidence) or "error"
            (the order call raised).
        requested: Quantity submitted (0.0 when nothing was sent).
        executed: Confirmed executed quantity.
        detail: Human-readable explanation for logs / result dicts.
    """

    status: str
    requested: float = 0.0
    executed: float = 0.0
    detail: str = ""

    @property
    def confirmed_flat(self) -> bool:
        """True only when the exchange confirms nothing remains to close."""
        return self.status in ("flat", "closed")


_PROTECTIVE_ORDER_TYPES = (
    "tpsl",
    "stop",
    "stop_loss",
    "stop_market",
    "conditional",
    "trigger",
)


def is_protective_order(order: Any) -> bool:
    """True for TP/SL (venue stop) rows, which must never count as grid orders.

    Recognizes a ``tpsl_id``/``tpslId`` marker (top level or under
    ``raw``), a stop-type ``order_type``, an ``sl_trigger_price`` and a
    reduce-only flag - grid levels are plain limit orders and never
    reduce-only.

    Args:
        order: Bot-native order dict from ``client.get_orders()``.

    Returns:
        True when the row is a protective order.
    """
    if not isinstance(order, dict):
        return False
    raw = order.get("raw") if isinstance(order.get("raw"), dict) else {}
    if order.get("tpsl_id") or order.get("tpslId") or raw.get("tpslId"):
        return True
    order_type = str(
        order.get("order_type") or order.get("orderType") or raw.get("orderType") or ""
    ).lower()
    if order_type in _PROTECTIVE_ORDER_TYPES:
        return True
    if order.get("sl_trigger_price") or order.get("slTriggerPrice") or raw.get("slTriggerPrice"):
        return True
    reduce_only = order.get("reduce_only", raw.get("reduceOnly"))
    return reduce_only in (True, "true", "True", 1)


class GridState(Enum):
    IDLE = "idle"
    ACTIVE = "active"
    EMERGENCY_EXIT = "emergency_exit"
    DISABLED_BY_REGIME = "disabled_by_regime"
    CLOSED = "closed"


# Single source of truth for the regimes in which a grid may KEEP RUNNING.
# Grid trading is a RANGING strategy (see CLAUDE.md regime table). INDECISIVE
# (ADX 20-25 transition band) is tolerated as a buffer so grids are not
# churned on every border flicker; new grids are still only CREATED in
# ranging regimes (StrategyManager Step 2.25 + _handle_grid_signals).
# Grids are unwound when a TRENDING_* regime is confirmed.
GRID_ALLOWED_REGIMES = frozenset(
    {"ranging_calm", "ranging_volatile", "indecisive"}
)


@dataclass
class GridFill:
    """Represents a single fill on a grid order."""

    trade_id: str
    order_id: str
    side: str  # "BUY" or "SELL"
    price: float
    quantity: float
    fee: float
    timestamp: datetime
    matched: bool = False  # True when FULLY matched with opposite side for P&L
    matched_quantity: float = 0.0  # Track how much of this fill has been matched

    @property
    def remaining_quantity(self) -> float:
        """Return unmatched quantity."""
        return self.quantity - self.matched_quantity


@dataclass
class GridMetrics:
    """Tracks grid performance metrics."""

    total_buy_fills: int = 0
    total_sell_fills: int = 0
    total_buy_quantity: float = 0.0
    total_sell_quantity: float = 0.0
    total_buy_value: float = 0.0  # quantity * price
    total_sell_value: float = 0.0
    total_fees: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    completed_round_trips: int = 0
    avg_buy_price: float = 0.0
    avg_sell_price: float = 0.0
    net_position: float = 0.0  # positive = long, negative = short


class GridLifecycleManager:
    def __init__(self, client, risk_manager, db=None, regime_detector=None):
        """
        Args:
            client: Exchange client (PacificaClient)
            risk_manager: Central RiskManager instance
            db: Optional DatabaseManager for grid state persistence
            regime_detector: Optional MarketRegimeDetector for trend direction on unwind
        """
        self.client = client
        self.risk_manager = risk_manager
        self.db = db
        self.regime_detector = regime_detector

        # symbol -> grid metadata
        self._grids: Dict[str, Dict[str, Any]] = {}

        # symbol -> list of fills
        self._fills: Dict[str, List[GridFill]] = {}

        # symbol -> set of processed trade IDs (prevent duplicate processing)
        self._processed_trades: Dict[str, Set[str]] = {}

        # symbol -> GridMetrics
        self._metrics: Dict[str, GridMetrics] = {}

        # Last time we checked for fills
        self._last_fill_check: datetime = datetime.now(timezone.utc) - timedelta(hours=1)

        # symbol -> pending-flatten marker.  A force exit that could not
        # confirm every position flat keeps its grid state and lands here
        # with an attempt counter; monitor_grids retries it each loop up
        # to GRID_FLATTEN_MAX_ATTEMPTS.  In-memory only (lost on restart).
        self._pending_flattens: Dict[str, Dict[str, Any]] = {}
        self._max_flatten_attempts = _max_flatten_attempts()

    # =========================
    # ORDER-ID TRACKING HELPERS
    # =========================

    @staticmethod
    def _extract_order_id_from_order(order: Dict[str, Any]) -> Optional[str]:
        """Extract an order ID from an exchange open-orders entry.

        Pacifica open-orders entries expose the ID as "order_id" (some
        legacy shapes use "id").

        Args:
            order: Order dict from the exchange open-orders response.

        Returns:
            Order ID as a string, or None if absent.
        """
        order_id = order.get("order_id") or order.get("id")
        return str(order_id) if order_id else None

    @staticmethod
    def _extract_order_id_from_response(
        response: Dict[str, Any],
    ) -> Optional[str]:
        """Extract an order ID from a place_order response.

        Pacifica wraps order creation as {"success": bool, "data":
        {"order_id": ...}}; fall back to top-level keys defensively.

        Args:
            response: Response dict returned by client.place_order.

        Returns:
            Order ID as a string, or None if it cannot be determined.
        """
        if not isinstance(response, dict):
            return None
        data = response.get("data")
        if isinstance(data, dict):
            order_id = data.get("order_id") or data.get("id")
            if order_id:
                return str(order_id)
        order_id = response.get("order_id") or response.get("id")
        return str(order_id) if order_id else None

    def get_tracked_order_ids(self, symbol: str) -> Optional[Set[str]]:
        """Return the live grid order-ID set for a symbol.

        Args:
            symbol: Trading symbol.

        Returns:
            The set of tracked order IDs, or None when the grid does not
            track IDs (legacy grid) or does not exist.
        """
        grid = self._grids.get(symbol)
        if grid is None:
            return None
        return grid.get("order_ids")

    # =========================
    # ORPHAN DETECTION & REPAIR
    # =========================

    def detect_and_repair_orphaned_grids(self) -> int:
        """
        Detect and repair orphaned grids on startup.

        Orphaned grids are those that:
        1. Have missing center_price or initial_center
        2. Have incomplete metadata (grid_capital, grid_spacing, num_levels)
        3. Have invalid state or corrupted data

        Returns:
            Number of grids repaired
        """
        repaired_count = 0

        try:
            orphaned_symbols = []

            for symbol, grid_data in self._grids.items():
                issues = []

                # Check for missing center price
                if not grid_data.get("center_price") and not grid_data.get("initial_center"):
                    issues.append("missing_center_price")

                # Check for incomplete metadata
                for req_field in ["grid_capital", "grid_spacing", "num_levels"]:
                    if req_field not in grid_data or grid_data[req_field] is None:
                        issues.append(f"missing_{req_field}")

                # Check for invalid state
                state_value = grid_data.get("state")
                if state_value is not None:
                    state_str = state_value.value if hasattr(state_value, "value") else str(state_value)
                    if state_str not in [s.value for s in GridState]:
                        issues.append("invalid_state")

                if issues:
                    orphaned_symbols.append((symbol, issues))
                    logger.warning(
                        f"⚠️ Orphaned grid detected for {symbol}: {', '.join(issues)}"
                    )

            # Attempt to repair orphaned grids
            for symbol, issues in orphaned_symbols:
                if self._repair_orphaned_grid(symbol, issues):
                    repaired_count += 1
                    logger.info(f"✅ Successfully repaired orphaned grid for {symbol}")
                else:
                    logger.error(f"❌ Failed to repair orphaned grid for {symbol}")
                    self._close_unrecoverable_grid(symbol, "startup_repair_failed")

            # If no memory grids needed repair, also sync from exchange
            if repaired_count == 0:
                exchange_repaired = self._sync_grids_from_exchange()
                repaired_count += exchange_repaired

        except Exception as e:
            logger.error(f"Error in detect_and_repair_orphaned_grids: {e}", exc_info=True)

        return repaired_count

    def _repair_orphaned_grid(self, symbol: str, issues: List[str]) -> bool:
        """
        Attempt to repair an orphaned grid by reconstructing data from exchange orders.

        Returns:
            True if repair was successful
        """
        try:
            grid_data = self._grids.get(symbol)
            if not grid_data:
                return False

            # Try to reconstruct missing center price from exchange orders
            if "missing_center_price" in issues:
                center_price = self._calculate_center_from_exchange_orders(symbol)
                if center_price:
                    grid_data["center_price"] = center_price
                    grid_data["initial_center"] = center_price
                    logger.info(f"🔧 Reconstructed center price for {symbol}: ${center_price:.4f}")
                else:
                    return False

            # Repair missing metadata with exchange data or defaults.
            # Spacing from exchange orders is RELATIVE (fraction of price);
            # grid_spacing must be stored as a DOLLAR offset.
            if "missing_grid_spacing" in issues:
                spacing_rel = self._calculate_spacing_from_exchange_orders(symbol)
                center_ref = (
                    grid_data.get("center_price")
                    or grid_data.get("initial_center")
                    or 0
                )
                if spacing_rel and center_ref:
                    grid_data["grid_spacing"] = spacing_rel * center_ref
                elif center_ref:
                    grid_data["grid_spacing"] = 0.004 * center_ref
                else:
                    grid_data["grid_spacing"] = 0.004

            if "missing_num_levels" in issues:
                levels = self._count_levels_from_exchange_orders(symbol)
                grid_data["num_levels"] = levels or 8

            if "missing_grid_capital" in issues:
                capital = self._estimate_capital_from_exchange_orders(symbol)
                grid_data["grid_capital"] = capital or 1000.0

            if "invalid_state" in issues:
                grid_data["state"] = GridState.ACTIVE

            # Add repair metadata
            grid_data["repaired_at"] = datetime.now(timezone.utc)
            grid_data["repair_issues"] = issues

            # Persist the repaired grid state
            if self.db:
                self.save_grid_state(symbol)

            return True

        except Exception as e:
            logger.error(f"Error repairing orphaned grid for {symbol}: {e}", exc_info=True)
            return False

    def _close_unrecoverable_grid(self, symbol: str, reason: str) -> None:
        """Close a grid that cannot be repaired."""
        try:
            logger.warning(f"🗑️ Closing unrecoverable grid for {symbol}: {reason}")

            try:
                self.client.cancel_all_orders(symbol)
                logger.info(f"  ✅ Cancelled all orders for {symbol}")
            except Exception as e:
                logger.error(f"  ❌ Failed to cancel orders: {e}")

            if symbol in self._grids:
                del self._grids[symbol]
            if self.db:
                self.delete_grid_state(symbol)

            for data_dict in [self._fills, self._metrics, self._processed_trades]:
                if symbol in data_dict:
                    del data_dict[symbol]

        except Exception as e:
            logger.error(f"Error closing unrecoverable grid for {symbol}: {e}", exc_info=True)

    def _calculate_center_from_exchange_orders(self, symbol: str) -> Optional[float]:
        """Calculate grid center price from current exchange orders."""
        try:
            orders = self.client.get_orders()
            symbol_orders = [o for o in orders if o.get("symbol") == symbol]

            if not symbol_orders:
                return None

            buy_orders = [o for o in symbol_orders if o.get("side", "").lower() in ["bid", "buy"]]
            sell_orders = [o for o in symbol_orders if o.get("side", "").lower() in ["ask", "sell"]]

            if not buy_orders or not sell_orders:
                return None

            buy_prices = [float(o.get("price", 0)) for o in buy_orders if o.get("price")]
            sell_prices = [float(o.get("price", 0)) for o in sell_orders if o.get("price")]

            if not buy_prices or not sell_prices:
                return None

            return (max(buy_prices) + min(sell_prices)) / 2

        except Exception as e:
            logger.error(f"Error calculating center from exchange orders for {symbol}: {e}")
            return None

    def _calculate_spacing_from_exchange_orders(self, symbol: str) -> Optional[float]:
        """Calculate grid spacing from current exchange orders."""
        try:
            orders = self.client.get_orders()
            symbol_orders = [o for o in orders if o.get("symbol") == symbol]

            buy_orders = [o for o in symbol_orders if o.get("side", "").lower() in ["bid", "buy"]]
            sell_orders = [o for o in symbol_orders if o.get("side", "").lower() in ["ask", "sell"]]

            if not buy_orders or not sell_orders:
                return None

            buy_prices = sorted(
                [float(o.get("price", 0)) for o in buy_orders if o.get("price")],
                reverse=True,
            )
            sell_prices = sorted(
                [float(o.get("price", 0)) for o in sell_orders if o.get("price")]
            )

            if len(buy_prices) < 2 and len(sell_prices) < 2:
                return None

            spacings = []
            for i in range(len(buy_prices) - 1):
                if buy_prices[i + 1] > 0:
                    spacings.append((buy_prices[i] - buy_prices[i + 1]) / buy_prices[i + 1])
            for i in range(len(sell_prices) - 1):
                if sell_prices[i] > 0:
                    spacings.append((sell_prices[i + 1] - sell_prices[i]) / sell_prices[i])

            return sum(spacings) / len(spacings) if spacings else None

        except Exception as e:
            logger.error(f"Error calculating spacing from exchange orders for {symbol}: {e}")
            return None

    def _count_levels_from_exchange_orders(self, symbol: str) -> Optional[int]:
        """Count grid levels from current exchange orders."""
        try:
            orders = self.client.get_orders()
            return len([o for o in orders if o.get("symbol") == symbol])
        except Exception as e:
            logger.error(f"Error counting levels from exchange orders for {symbol}: {e}")
            return None

    def _estimate_capital_from_exchange_orders(self, symbol: str) -> Optional[float]:
        """Estimate grid capital from current exchange orders."""
        try:
            orders = self.client.get_orders()
            symbol_orders = [o for o in orders if o.get("symbol") == symbol]

            total_value = 0
            for order in symbol_orders:
                price = float(order.get("price", 0))
                quantity = float(
                    order.get("quantity") or order.get("size") or order.get("amount") or order.get("initial_amount", 0)
                )
                total_value += price * quantity

            return total_value if total_value > 0 else None
        except Exception as e:
            logger.error(f"Error estimating capital from exchange orders for {symbol}: {e}")
            return None

    def _sync_grids_from_exchange(self) -> int:
        """
        Sync grids from exchange - detect orphaned grids not in memory.

        Returns:
            Number of grids re-adopted from exchange
        """
        repaired_count = 0

        try:
            orders = self.client.get_orders()
            orders_by_symbol: Dict[str, list] = {}

            for order in orders:
                # Protective TP/SL rows (venue stops) are never grid
                # levels: a protected position must not look like a grid.
                if is_protective_order(order):
                    continue
                symbol = order.get("symbol")
                if symbol:
                    if symbol not in orders_by_symbol:
                        orders_by_symbol[symbol] = []
                    orders_by_symbol[symbol].append(order)

            for symbol, symbol_orders in orders_by_symbol.items():
                if symbol in self._grids:
                    continue

                if len(symbol_orders) >= 5:
                    buy_orders = [o for o in symbol_orders if o.get("side", "").lower() in ["bid", "buy"]]
                    sell_orders = [o for o in symbol_orders if o.get("side", "").lower() in ["ask", "sell"]]

                    if buy_orders and sell_orders:
                        logger.info(f"🔍 Found potential orphaned grid on exchange: {symbol}")

                        if self._readopt_orphaned_grid_from_exchange(symbol, buy_orders, sell_orders):
                            repaired_count += 1
                            logger.info(f"✅ Re-adopted orphaned grid from exchange: {symbol}")

        except Exception as e:
            logger.error(f"Error syncing grids from exchange: {e}", exc_info=True)

        return repaired_count

    def _readopt_orphaned_grid_from_exchange(
        self, symbol: str, buy_orders: List[Dict], sell_orders: List[Dict]
    ) -> bool:
        """Re-adopt an orphaned grid detected from exchange orders."""
        try:
            all_orders = buy_orders + sell_orders

            center_price = self._calculate_center_from_exchange_orders(symbol)
            if not center_price:
                return False

            # _calculate_spacing_from_exchange_orders returns a RELATIVE step
            # (fraction of price); grid_spacing is consumed as a DOLLAR offset
            # by _replenish_order, so convert here.
            spacing_rel = self._calculate_spacing_from_exchange_orders(symbol)
            if spacing_rel and spacing_rel > 0:
                grid_spacing = spacing_rel * center_price
            else:
                grid_spacing = 0.004 * center_price  # 0.4% default, in dollars
            num_levels = len(all_orders)
            total_capital = self._estimate_capital_from_exchange_orders(symbol) or 0

            # SAFETY: compute a real emergency stop on re-adoption.
            # _check_emergency_stop short-circuits on ≤ 0; without this the
            # grid runs unprotected at the per-symbol level.
            emergency_stop_pct = float(os.getenv("GRID_EMERGENCY_STOP_PCT", "0.05"))
            buy_prices = [float(o.get("price", 0)) for o in buy_orders if o.get("price")]
            if buy_prices:
                emergency_stop = min(buy_prices) * (1.0 - emergency_stop_pct)
            else:
                emergency_stop = center_price * (1.0 - emergency_stop_pct)

            self._grids[symbol] = {
                "state": GridState.ACTIVE,
                "grid_capital": total_capital,
                "emergency_stop": emergency_stop,  # FIX: was 0 — unprotected on re-adoption
                "regime_on_creation": "unknown_readopted",
                "atr_at_creation": 0,
                "grid_spacing": grid_spacing,
                "num_levels": num_levels,
                "orders_placed": num_levels,
                "center_price": center_price,
                "initial_center": center_price,
                "created_at": datetime.now(timezone.utc),
                "refresh_count": 0,
                "readopted": True,
                "readopted_at": datetime.now(timezone.utc),
            }

            # Track order IDs from the exchange's open-orders response so
            # only fills of these orders are attributed to the grid.
            readopted_ids = {
                oid
                for oid in (
                    self._extract_order_id_from_order(o) for o in all_orders
                )
                if oid
            }
            if readopted_ids:
                self._grids[symbol]["order_ids"] = readopted_ids

            self._fills[symbol] = []
            self._processed_trades[symbol] = set()
            self._metrics[symbol] = GridMetrics()

            if self.db:
                self.save_grid_state(symbol)

            return True

        except Exception as e:
            logger.error(f"Error readopting orphaned grid for {symbol}: {e}", exc_info=True)
            return False

    def _validate_grid_integrity(self) -> None:
        """Validate the integrity of all loaded grids."""
        try:
            valid_grids = 0
            issues_found = 0

            for symbol, grid_data in list(self._grids.items()):
                issues = []

                for req_field in ["state", "grid_capital"]:
                    if req_field not in grid_data or grid_data[req_field] is None:
                        issues.append(f"missing_{req_field}")

                if "grid_spacing" in grid_data:
                    spacing = grid_data["grid_spacing"]
                    if not isinstance(spacing, (int, float)) or spacing <= 0:
                        issues.append("invalid_spacing")

                if issues:
                    issues_found += 1
                    logger.warning(f"⚠️ Grid integrity issues for {symbol}: {', '.join(issues)}")

                    if "invalid_spacing" in issues:
                        center_ref = (
                            grid_data.get("center_price")
                            or grid_data.get("initial_center")
                            or 0
                        )
                        grid_data["grid_spacing"] = (
                            0.004 * center_ref if center_ref else 0.004
                        )
                        if self.db:
                            self.save_grid_state(symbol)
                else:
                    valid_grids += 1

            if issues_found > 0:
                logger.info(f"Grid integrity check: {valid_grids} valid, {issues_found} with issues")
            else:
                logger.debug(f"Grid integrity check: all {valid_grids} grids valid")

        except Exception as e:
            logger.error(f"Error validating grid integrity: {e}", exc_info=True)

    def initialize_grid_system(self) -> None:
        """
        Initialize the grid system with startup repair and validation.

        Called after construction to:
        1. Load existing grid states from database
        2. Detect and repair orphaned grids
        3. Validate grid integrity
        """
        try:
            logger.info("🔧 Initializing GridLifecycleManager repair system...")

            if self.db:
                loaded_grids = self.load_grid_states(regime_detector=self.regime_detector)
                logger.info(f"📊 Loaded {len(loaded_grids)} grids from database")

            orphaned_repaired = self.detect_and_repair_orphaned_grids()
            if orphaned_repaired > 0:
                logger.info(f"🔧 Fixed {orphaned_repaired} orphaned grids on startup")

            self._validate_grid_integrity()

            logger.info("✅ GridLifecycleManager repair system initialization complete")

        except Exception as e:
            logger.error(f"❌ Grid system initialization failed: {e}", exc_info=True)

    # =========================
    # STATE CHECKS
    # =========================

    def has_active_grid(self, symbol: str) -> bool:
        return (
            symbol in self._grids and self._grids[symbol]["state"] == GridState.ACTIVE
        )

    def needs_order_placement(self, symbol: str) -> bool:
        """Check if an active grid needs orders placed (registered but no orders on exchange)."""
        if symbol not in self._grids:
            return False
        grid = self._grids[symbol]
        if grid.get("state") != GridState.ACTIVE:
            return False
        return grid.get("orders_placed", 0) == 0

    def get_orders_placed(self, symbol: str) -> int:
        """Get the number of orders placed for a grid."""
        if symbol not in self._grids:
            return 0
        return self._grids[symbol].get("orders_placed", 0)

    def set_orders_placed(self, symbol: str, count: int):
        """Update the orders placed count for a grid."""
        if symbol not in self._grids:
            logger.warning(f"Cannot set orders_placed - no grid for {symbol}")
            return
        self._grids[symbol]["orders_placed"] = count
        logger.info(f"📊 Grid {symbol} orders_placed set to {count}")

    def get_grid_state(self, symbol: str) -> GridState:
        return self._grids.get(symbol, {}).get("state", GridState.IDLE)

    def get_grid_center(self, symbol: str) -> Optional[float]:
        """Return the current reference center price of the active grid."""
        if not self.has_active_grid(symbol):
            return None
        grid = self._grids[symbol]
        # Use stored center price if available
        if "center_price" in grid:
            return grid["center_price"]
        # Fallback to initial entry price from grid creation
        return grid.get("initial_center", None)

    def get_last_refresh_time(self, symbol: str) -> Optional[datetime]:
        """Get when grid was last recentered/refreshed."""
        if symbol not in self._grids:
            return None
        return self._grids[symbol].get("last_refresh", None)

    def get_grid_spacing(self, symbol: str) -> float:
        """Get the current grid spacing for a symbol."""
        if symbol not in self._grids:
            return 0.0
        return self._grids[symbol].get("grid_spacing", 0.0)

    def update_grid_spacing(self, symbol: str, new_spacing: float, reason: str = "dynamic"):
        """Update the grid spacing (for dynamic spacing adjustments)."""
        if symbol not in self._grids:
            logger.warning(f"Cannot update spacing - no grid for {symbol}")
            return
        old_spacing = self._grids[symbol].get("grid_spacing", 0)
        self._grids[symbol]["grid_spacing"] = new_spacing
        self._grids[symbol]["spacing_updated_at"] = datetime.now(timezone.utc)
        logger.info(
            f"📊 Grid {symbol} spacing updated: ${old_spacing:.4f} → ${new_spacing:.4f} ({reason})"
        )

    def recenter_grid(self, symbol: str, new_center: float, reason: str = "signal_refresh") -> bool:
        """
        Soft recenter: shift UNFILLED limit orders toward new center price.
        Does NOT touch filled positions.

        Args:
            symbol: Trading symbol
            new_center: New center price to shift orders toward
            reason: Reason for recentering (for logging)

        Returns:
            True if recenter successful, False otherwise
        """
        if not self.has_active_grid(symbol):
            logger.warning(f"Cannot recenter - no active grid for {symbol}")
            return False

        grid = self._grids[symbol]
        current_center = self.get_grid_center(symbol)
        if current_center is None:
            logger.error(f"Cannot recenter {symbol} - no current center price")
            return False

        # Safety: enforce minimum time between refreshes (45 minutes)
        last_refresh = self.get_last_refresh_time(symbol)
        if last_refresh and (datetime.now(timezone.utc) - last_refresh).total_seconds() < 2700:
            remaining = 2700 - (datetime.now(timezone.utc) - last_refresh).total_seconds()
            logger.info(
                f"Refresh skipped for {symbol} - cooldown {remaining/60:.1f}min remaining"
            )
            return False

        drift_abs = abs(new_center - current_center)
        drift_pct = drift_abs / current_center if current_center > 0 else 0
        logger.info(
            f"📊 Recentering {symbol} | old center ${current_center:.4f} → new ${new_center:.4f} "
            f"(drift ${drift_abs:.4f} / {drift_pct:.2%}) - reason: {reason}"
        )

        try:
            # Calculate shift delta
            delta = new_center - current_center
            spacing = grid.get("grid_spacing", 0)

            # Resolve correct tick size for this symbol from the exchange.
            # Falls back to a sane default ONLY if instrument info is unavailable.
            try:
                instr = self.client.get_instrument_info(symbol) or {}
                tick_size = float(instr.get("tick_size", 0)) or 0.0
            except Exception as e:
                logger.warning(f"get_instrument_info failed for {symbol}: {e}")
                tick_size = 0.0
            if tick_size <= 0:
                # Last-resort fallback (BTC ticks ≈ $1, most alts ≈ $0.01).
                # Logged because this can produce wrong prices on novel assets.
                tick_size = 1.0 if "BTC" in symbol else 0.01
                logger.warning(
                    f"Using fallback tick_size={tick_size} for {symbol} "
                    f"— get_instrument_info returned 0/missing"
                )

            # Get current open orders from exchange
            orders = self.client.get_orders()
            symbol_orders = [o for o in orders if o.get("symbol") == symbol]

            if not symbol_orders:
                logger.info(f"No open orders to recenter for {symbol}")
                # Still update metadata (no orders to fail)
                grid["center_price"] = new_center
                grid["last_refresh"] = datetime.now(timezone.utc)
                grid["last_refresh_reason"] = reason
                return True

            # ATOMIC RECENTER: track skipped/failed/succeeded separately so we
            # can decide whether the recenter as a whole was successful.
            # Previously, partial failures left center_price updated as if all
            # replacements had worked — corrupting the in-memory model.
            orders_to_replace = []   # legs that pass safety checks
            skipped_legs     = []    # legs we deliberately skipped (price shift cap)
            for order in symbol_orders:
                order_id = order.get("id") or order.get("order_id")
                old_price = float(order.get("price", 0))
                side = order.get("side", "").lower()
                quantity = float(order.get("quantity") or order.get("size") or order.get("amount", 0))

                if not order_id or not old_price or not quantity:
                    continue

                new_price = old_price + delta

                # Safety: cap single-order price shift at 8%
                if abs(new_price - old_price) / old_price > 0.08:
                    logger.warning(
                        f"Price shift too large for {symbol} order {order_id} "
                        f"(${old_price:.2f} → ${new_price:.2f}) - skipping this leg"
                    )
                    skipped_legs.append(order_id)
                    continue

                # Round to tick size resolved above
                new_price = round(new_price / tick_size) * tick_size
                orders_to_replace.append((order_id, side, quantity, old_price, new_price))

            # Phase 1: cancel-then-replace each leg, tracking failures
            failed_replacements = []
            orders_adjusted = 0
            for order_id, side, quantity, old_price, new_price in orders_to_replace:
                cancelled = False
                try:
                    self.client.cancel_order(symbol, order_id)
                    cancelled = True
                    logger.debug(f"Cancelled old order {order_id} @ ${old_price:.4f}")

                    new_order = self.client.place_order(
                        symbol, side, quantity, "limit", new_price
                    )
                    new_order_id = new_order.get("id") or new_order.get("order_id")
                    logger.info(
                        f"📊 Replaced order {order_id} → {new_order_id} @ ${new_price:.4f} ({side})"
                    )
                    orders_adjusted += 1

                    # Rotate tracked order IDs (old leg is gone, new leg is
                    # live) so fill attribution survives recentering.
                    tracked_ids = grid.get("order_ids")
                    if tracked_ids is not None:
                        tracked_ids.discard(str(order_id))
                        replacement_id = self._extract_order_id_from_response(
                            new_order
                        )
                        if replacement_id:
                            tracked_ids.add(replacement_id)

                except Exception as e:
                    # Cancel succeeded but replace failed = orphan leg.
                    # Log critically so the supervisor sees it; track for caller.
                    state = "post-cancel" if cancelled else "pre-cancel"
                    logger.critical(
                        f"❌ Recenter leg failed for {symbol} order {order_id} "
                        f"(state={state}, side={side}, qty={quantity}, "
                        f"price ${old_price:.4f}→${new_price:.4f}): {e}"
                    )
                    failed_replacements.append({
                        "order_id":  order_id,
                        "side":      side,
                        "quantity":  quantity,
                        "old_price": old_price,
                        "new_price": new_price,
                        "state":     state,
                        "error":     str(e),
                    })

                    # Cancelled-but-not-replaced legs are no longer live:
                    # drop them from the tracked ID set.
                    if cancelled and grid.get("order_ids") is not None:
                        grid["order_ids"].discard(str(order_id))

            # Phase 2: decide how to update grid metadata.
            # If ANY orphan legs (cancel succeeded, replace failed), the grid
            # has fewer live orders than tracked. We DO NOT update center_price
            # in that case — the new center would be a lie. Caller can retry.
            had_orphans = any(f["state"] == "post-cancel" for f in failed_replacements)
            if had_orphans:
                logger.critical(
                    f"⚠️  Grid recenter for {symbol} left {len(failed_replacements)} "
                    f"orphan leg(s). center_price NOT updated. "
                    f"Adjusted {orders_adjusted}, skipped {len(skipped_legs)}, "
                    f"failed {len(failed_replacements)}."
                )
                # Track in grid metadata so monitor / supervisor can see it
                grid.setdefault("recenter_failures", []).append({
                    "ts":      datetime.now(timezone.utc).isoformat(),
                    "reason":  reason,
                    "orphans": failed_replacements,
                })
                return False

            # All legs that we attempted succeeded → safe to update metadata
            grid["center_price"] = new_center
            grid["last_refresh"] = datetime.now(timezone.utc)
            grid["last_refresh_reason"] = reason
            grid["refresh_count"] = grid.get("refresh_count", 0) + 1

            logger.info(
                f"✅ Grid recenter completed for {symbol} - "
                f"{orders_adjusted} adjusted, {len(skipped_legs)} skipped"
            )
            return True

        except Exception as e:
            logger.critical(f"Recentering FAILED for {symbol}: {e}")
            return False

    def close_grid(self, symbol: str, reason: str = "manual") -> bool:
        """
        Close/deactivate a grid for a symbol.

        Args:
            symbol: Trading symbol
            reason: Reason for closing (for logging)

        Returns:
            True if grid was closed, False if no active grid
        """
        if symbol not in self._grids:
            logger.warning(f"No grid found for {symbol}")
            return False

        grid_data = self._grids[symbol]
        old_state = grid_data.get("state", GridState.IDLE)

        # Set state to CLOSED
        grid_data["state"] = GridState.CLOSED
        grid_data["closed_at"] = datetime.now(timezone.utc)
        grid_data["close_reason"] = reason

        logger.info(
            f"📊 Grid closed for {symbol}: {old_state.value} -> CLOSED (reason: {reason})"
        )

        # Optionally persist to database
        if self.db:
            try:
                self.db.update_grid_state(symbol, "closed", reason)
            except Exception as e:
                logger.warning(f"Could not persist grid close to DB: {e}")

        return True

    def clear_grid(self, symbol: str) -> bool:
        """
        Completely remove a grid from memory AND database (allows new grid creation).

        Args:
            symbol: Trading symbol

        Returns:
            True if grid was removed, False if no grid existed
        """
        # An explicit clear abandons any in-flight flatten retry.
        if self._pending_flattens.pop(symbol, None) is not None:
            logger.warning(f"Pending flatten marker dropped for {symbol} (clear_grid)")

        if symbol in self._grids:
            del self._grids[symbol]
            logger.info(f"📊 Grid cleared from memory for {symbol}")

            # Also clear related data
            if symbol in self._fills:
                del self._fills[symbol]
            if symbol in self._processed_trades:
                del self._processed_trades[symbol]
            if symbol in self._metrics:
                del self._metrics[symbol]

            # Delete from database to prevent reload
            self.delete_grid_state(symbol)
            logger.info(f"📊 Grid cleared from database for {symbol}")

            return True

        # Even if not in memory, try to delete from DB (in case of stale state)
        self.delete_grid_state(symbol)
        return False

    # =========================
    # GRID CREATION
    # =========================

    def register_new_grid(
        self,
        symbol: str,
        grid_capital: float,
        emergency_stop_price: float,
        regime: str = None,
        atr: float = 0,
        spacing: float = 0,
        num_levels: int = 10,
        center_price: float = 0,
        order_ids: Optional[List[str]] = None,
    ):
        """
        Register a grid AFTER emergency stop has been successfully placed.
        HARD FAIL if grid already exists.

        Args:
            symbol: Trading symbol
            grid_capital: Capital allocated to the grid
            emergency_stop_price: Price at which to emergency exit
            regime: Current market regime (for persistence)
            atr: ATR at creation time
            spacing: Grid spacing used
            num_levels: Number of grid levels
            center_price: Initial center price for the grid
            order_ids: Exchange order IDs of the placed grid orders. When
                provided, fills are attributed to the grid ONLY if their
                order ID is in this set (protects against overlay-strategy
                fills on the same symbol). When None, the grid falls back
                to legacy attribute-everything behavior.
        """
        if symbol in self._grids:
            raise RuntimeError(f"Grid already active for {symbol}")

        self._grids[symbol] = {
            "state": GridState.ACTIVE,
            "grid_capital": grid_capital,
            "emergency_stop": emergency_stop_price,
            "regime_on_creation": regime,
            "atr_at_creation": atr,
            "grid_spacing": spacing,
            "num_levels": num_levels,
            "orders_placed": 0,  # Track actual orders placed on exchange
            "center_price": center_price,  # For recentering logic
            "initial_center": center_price,  # Preserve original center
            "created_at": datetime.now(timezone.utc),
            "refresh_count": 0,
        }

        if order_ids is not None:
            self._grids[symbol]["order_ids"] = {
                str(oid) for oid in order_ids if oid
            }

        # Initialize exposure tracking at zero
        self.risk_manager.grid_exposure[symbol] = 0.0

        # Persist to database for restart resilience
        self.save_grid_state(symbol, regime=regime, atr=atr, spacing=spacing)

        logger.info(
            f"Grid registered for {symbol}: capital=${grid_capital:.2f}, center=${center_price:.4f}, regime={regime}"
        )

    # =========================
    # REGIME HANDLING
    # =========================

    def handle_regime_change(
        self,
        symbol: str,
        new_regime,
        market_data: Dict[str, List] = None,
    ) -> bool:
        """
        Entry point for REGIME_CHANGED events (wired in trading_bot.py).

        If the confirmed new regime is outside GRID_ALLOWED_REGIMES and an
        ACTIVE grid exists for the symbol, the grid is unwound via
        on_regime_disallowed (partial unwind keeps trend-aligned positions
        when enabled, otherwise full exit).

        Args:
            symbol: Trading symbol from the event.
            new_regime: New regime (MarketRegime enum or its value string).
            market_data: Optional 4h OHLCV data for trend-direction detection
                during partial unwind.

        Returns:
            True if a grid unwind was triggered, False otherwise.
        """
        regime_str = str(getattr(new_regime, "value", new_regime)).lower()
        if regime_str in GRID_ALLOWED_REGIMES:
            return False

        grid = self._grids.get(symbol)
        if grid is None or grid.get("state") != GridState.ACTIVE:
            return False

        logger.warning(
            f"Regime change to {regime_str} disallows grid for {symbol} - unwinding"
        )
        self.on_regime_disallowed(symbol, market_data)
        return True

    def on_regime_disallowed(self, symbol: str, market_data: Dict[str, List] = None):
        """
        Called when market regime transitions OUT of allowed grid regimes.

        If partial unwind is enabled and trend direction is clear:
        - Close positions AGAINST the trend
        - KEEP positions aligned WITH the trend (migrate to trend-following)

        Falls back to full exit if:
        - Partial unwind disabled in config
        - Trend direction is 'none' (unclear)
        - Any error during partial exit
        - No regime detector available

        Args:
            symbol: Trading symbol
            market_data: Optional OHLCV data for trend detection (4h timeframe preferred)
        """
        if symbol not in self._grids:
            return

        logger.warning(f"Grid disabled by regime change for {symbol}")
        self._grids[symbol]["state"] = GridState.DISABLED_BY_REGIME

        # Check if partial unwind is enabled
        if not config.grid_partial_unwind_enabled:
            logger.info(
                f"Partial unwind disabled in config - using full exit for {symbol}"
            )
            self._force_exit(symbol, reason="REGIME_CHANGE")
            return

        # Check if regime detector available
        if not self.regime_detector or not market_data:
            logger.info(
                f"No regime detector or market data - using full exit for {symbol}"
            )
            self._force_exit(symbol, reason="REGIME_CHANGE")
            return

        # Get trend direction
        trend_direction = self.regime_detector.get_trend_direction(market_data)
        logger.info(f"Detected trend direction for {symbol}: {trend_direction}")

        # Fall back to full exit if trend unclear
        if trend_direction == "none":
            logger.warning(f"Trend direction unclear - using full exit for {symbol}")
            self._force_exit(symbol, reason="REGIME_CHANGE_UNCLEAR_TREND")
            return

        # Attempt partial exit
        try:
            result = self._partial_exit(
                symbol, reason="REGIME_CHANGE", trend_direction=trend_direction
            )

            if not result.get("success"):
                logger.error(
                    f"Partial exit failed for {symbol} - falling back to full exit"
                )
                self._force_exit(symbol, reason="PARTIAL_EXIT_FAILED")
        except Exception as e:
            logger.error(
                f"Error during partial exit for {symbol}: {e} - falling back to full exit"
            )
            self._force_exit(symbol, reason=f"PARTIAL_EXIT_ERROR:{e}")

    # =========================
    # EMERGENCY EXIT
    # =========================

    def on_emergency_stop_triggered(self, symbol: str):
        """
        Called when emergency stop price is breached or stop order fills.
        """
        if symbol not in self._grids:
            return

        logger.critical(f"EMERGENCY GRID EXIT triggered for {symbol}")
        self._grids[symbol]["state"] = GridState.EMERGENCY_EXIT

        self._force_exit(symbol, reason="EMERGENCY_STOP")

    # =========================
    # CORE EXIT LOGIC
    # =========================

    def _force_exit(self, symbol: str, reason: str) -> Dict[str, Any]:
        """
        HARD EXIT:
        - Cancel all orders
        - Close all positions (reduce-only, sized from the exchange)
        - Clear RiskManager exposure and remove grid state ONLY when every
          position is confirmed flat; otherwise keep the grid state and
          record a pending-flatten marker that monitor_grids retries.

        Returns:
            Dict with ``positions_closed``, ``positions_pending``,
            ``errors`` and ``flat`` (True when the grid was fully cleared).
        """
        result: Dict[str, Any] = {
            "symbol": symbol,
            "reason": reason,
            "flat": False,
            "positions_closed": [],
            "positions_pending": [],
            "errors": [],
        }
        try:
            self.client.cancel_all_orders(symbol)
            logger.info(f"All orders cancelled for {symbol} ({reason})")
        except Exception as e:
            logger.error(f"Order cancel failed for {symbol}: {e}")
            result["errors"].append(f"Order cancel failed: {e}")

        try:
            positions = self.client.get_positions()
        except Exception as e:
            logger.critical(f"POSITION FLATTEN FAILED for {symbol}: {e}")
            result["errors"].append(f"Get positions failed: {e}")
            self._mark_pending_flatten(symbol, reason, result)
            return result

        for pos in positions or []:
            if not isinstance(pos, dict) or pos.get("symbol") != symbol:
                continue
            self._flatten_one_position(symbol, pos, reason, result)

        if result["positions_pending"] or result["errors"]:
            self._mark_pending_flatten(symbol, reason, result)
            return result

        self._complete_force_exit(symbol, reason)
        result["flat"] = True
        return result

    def _flatten_one_position(
        self, symbol: str, pos: Dict[str, Any], reason: str, result: Dict[str, Any]
    ) -> None:
        """Close one exchange position; failures never abort the caller's loop."""
        side: Optional[str] = None
        qty = 0.0
        try:
            qty = abs(float(pos.get("amount", 0) or 0))
            if qty <= 0:
                return
            # Pacifica reports "long"/"short"; older paths used "bid"/"ask".
            side = normalize_position_side(pos.get("side"))
            if side is None:
                raise ValueError(f"unrecognized position side {pos.get('side')!r}")
            outcome = self._close_position_reduce_only(symbol, side, qty, reason)
        except Exception as e:  # noqa: BLE001 - isolate per position
            logger.error(f"POSITION FLATTEN FAILED for {symbol} {side} {qty}: {e}")
            result["errors"].append(f"{side} {qty}: {e}")
            result["positions_pending"].append(
                {"side": side, "qty": qty, "status": "error", "detail": str(e)}
            )
            return

        entry = {
            "side": side,
            "qty": qty,
            "requested": outcome.requested,
            "executed": outcome.executed,
            "status": outcome.status,
            "detail": outcome.detail,
        }
        if outcome.confirmed_flat:
            result["positions_closed"].append(entry)
            logger.critical(
                f"Position flattened: {symbol} {side} {qty} ({outcome.status})"
            )
            return
        result["positions_pending"].append(entry)
        result["errors"].append(f"{side} {qty}: {outcome.status} - {outcome.detail}")

    def _close_position_reduce_only(
        self, symbol: str, position_side: str, qty: float, reason: str
    ) -> CloseOutcome:
        """Send a reduce-only market close sized from the exchange position.

        Args:
            symbol: Trading symbol.
            position_side: "long" / "short" (any spelling accepted by
                ``exit_sizing``; unknown sides are sent as sells and can
                only be confirmed by ack fills).
            qty: Locally believed open quantity.
            reason: Exit reason for logs.

        Returns:
            CloseOutcome - only ``confirmed_flat`` outcomes may be
            accounted as closed by the caller.
        """
        plan = plan_close_quantity(self.client, symbol, position_side, qty)
        if plan.already_flat:
            logger.warning(
                f"Close {symbol} {position_side} ({reason}): exchange already "
                f"flat - no order sent"
            )
            return CloseOutcome("flat", 0.0, 0.0, "exchange already flat")
        if plan.clamped:
            logger.warning(
                f"Close {symbol} {position_side}: clamping {qty:.6f} to exchange "
                f"quantity {plan.quantity:.6f}"
            )
        close_side = "buy" if position_side == "short" else "sell"
        try:
            ack = self.client.place_order(
                symbol, close_side, plan.quantity, "market", reduce_only=True
            )
        except Exception as exc:  # noqa: BLE001 - outcome is ambiguous
            detail = f"{type(exc).__name__}: {exc}"
            logger.error(f"CLOSE RAISED {symbol} {position_side} ({reason}): {detail}")
            return CloseOutcome("error", plan.quantity, 0.0, detail)
        order = OrderResult.from_ack(ack)
        if not order.accepted:
            detail = order.error or "order rejected"
            logger.error(
                f"CLOSE REJECTED {symbol} {position_side} {plan.quantity:.6f} "
                f"({reason}): {detail} - position kept, no accounting applied"
            )
            return CloseOutcome("rejected", plan.quantity, 0.0, detail)
        executed = self._confirmed_executed_quantity(
            symbol, position_side, plan, order, qty
        )
        if executed is None:
            return CloseOutcome(
                "unconfirmed", plan.quantity, 0.0, "accepted but fill unconfirmed"
            )
        if executed + 1e-9 < plan.quantity:
            return CloseOutcome(
                "partial",
                plan.quantity,
                executed,
                f"partial execution {executed:.6f}/{plan.quantity:.6f}",
            )
        return CloseOutcome("closed", plan.quantity, executed, "confirmed")

    def _confirmed_executed_quantity(
        self,
        symbol: str,
        side: str,
        plan: ClosePlan,
        order: OrderResult,
        local_before: float,
    ) -> Optional[float]:
        """Executed quantity confirmed by ack fills or the exchange position.

        Returns:
            Executed quantity, or None when it cannot be confirmed.
        """
        if order.has_fills:
            return min(plan.quantity, order.filled_quantity)
        remaining_after = remaining_exchange_quantity(self.client, symbol, side)
        if remaining_after is None:
            return None
        before = (
            plan.exchange_quantity
            if plan.exchange_quantity is not None
            else local_before
        )
        return max(0.0, min(plan.quantity, before - remaining_after))

    def _mark_pending_flatten(
        self, symbol: str, reason: str, result: Dict[str, Any]
    ) -> None:
        """Record / bump the pending-flatten marker; grid state is KEPT."""
        marker = self._pending_flattens.get(symbol)
        if marker is None:
            marker = {
                "attempts": 0,
                "first_failed_at": time.time(),
                "escalated": False,
            }
            self._pending_flattens[symbol] = marker
        marker["attempts"] += 1
        marker["reason"] = reason
        marker["last_attempt_at"] = time.time()
        marker["positions_pending"] = list(result["positions_pending"])
        marker["errors"] = list(result["errors"])
        if symbol in self._grids:
            self._grids[symbol]["state"] = GridState.EMERGENCY_EXIT
        logger.error(
            f"GRID FLATTEN INCOMPLETE for {symbol} ({reason}): "
            f"closed={len(result['positions_closed'])}, "
            f"pending={len(result['positions_pending'])}, "
            f"errors={result['errors']} - attempt "
            f"{marker['attempts']}/{self._max_flatten_attempts}; grid state "
            f"kept for retry, no exposure reset"
        )

    def _complete_force_exit(self, symbol: str, reason: str) -> None:
        """Accounting for a CONFIRMED flat grid: exposure, memory, DB."""
        self._pending_flattens.pop(symbol, None)
        self.risk_manager.on_grid_emergency_exit(symbol)
        if symbol in self._grids:
            del self._grids[symbol]
        self.delete_grid_state(symbol)
        logger.critical(f"Grid fully exited and cleared for {symbol} ({reason})")

    def retry_pending_flattens(self) -> int:
        """Retry incomplete force exits, capped by GRID_FLATTEN_MAX_ATTEMPTS.

        Called from ``monitor_grids`` each loop.  Once a marker reaches
        the cap it is escalated exactly once (ERROR log + EventBus event)
        and left for manual action.

        Returns:
            Number of force exits retried this call.
        """
        retried = 0
        for symbol in list(self._pending_flattens):
            marker = self._pending_flattens[symbol]
            if marker["attempts"] >= self._max_flatten_attempts:
                if not marker.get("escalated"):
                    marker["escalated"] = True
                    self._escalate_pending_flatten(symbol, marker)
                continue
            retried += 1
            self._force_exit(symbol, str(marker.get("reason", "RETRY")))
        return retried

    def _escalate_pending_flatten(self, symbol: str, marker: Dict[str, Any]) -> None:
        """Alert on a flatten that hit the retry cap: log, EventBus, Telegram.

        Every channel is best effort - a failure is logged and never raised.
        """
        message = (
            f"Pending grid flatten {symbol} reached the retry cap "
            f"({self._max_flatten_attempts}); positions kept open - manual "
            f"action required. Last errors: {marker.get('errors')}"
        )
        logger.error(message)
        try:
            from .event_system import EventType, get_event_bus

            get_event_bus().publish_event(
                EventType.CLOSE_ESCALATED,
                {
                    "symbol": symbol,
                    "source_kind": "grid_flatten",
                    "reason": marker.get("reason"),
                    "attempts": marker.get("attempts"),
                    "errors": list(marker.get("errors") or []),
                },
                "GridLifecycleManager",
            )
        except Exception as exc:  # noqa: BLE001 - alerting is best effort
            logger.warning(f"Could not publish CLOSE_ESCALATED for {symbol}: {exc}")
        try:
            _send_telegram_error_alert(
                "close_escalated",
                message,
                f"{symbol} grid_flatten attempts={marker.get('attempts')}",
            )
        except Exception as exc:  # noqa: BLE001 - alerting is best effort
            logger.warning(f"Telegram escalation for {symbol} failed: {exc}")

    def get_pending_flattens(self) -> Dict[str, Dict[str, Any]]:
        """Return a copy of the pending-flatten markers (for status/tests)."""
        return {symbol: dict(m) for symbol, m in self._pending_flattens.items()}

    def _partial_exit(
        self, symbol: str, reason: str, trend_direction: str
    ) -> Dict[str, Any]:
        """
        Selective unwind: Close against-trend positions, keep with-trend.

        This is called when regime changes from ranging to trending.
        Instead of closing ALL positions, we:
        - Close positions that are AGAINST the trend (would lose in trending market)
        - KEEP positions that are WITH the trend (could profit from trend continuation)
        - Migrate kept positions to trend-following management

        Args:
            symbol: Trading symbol
            reason: Why the unwind is happening
            trend_direction: 'up' or 'down' (from MarketRegimeDetector)

        Returns:
            Dict with 'closed_positions', 'kept_positions', 'success' keys
        """
        result = {
            "success": False,
            "closed_positions": [],
            "kept_positions": [],
            "errors": [],
        }

        # ALWAYS cancel all open orders first (pending orders are neutral, don't keep them)
        try:
            self.client.cancel_all_orders(symbol)
            logger.info(f"All grid orders cancelled for {symbol} ({reason})")
        except Exception as e:
            logger.error(f"Order cancel failed for {symbol}: {e}")
            result["errors"].append(f"Order cancel failed: {e}")
            return result  # Fail early - orders must be cancelled

        # No live grid orders remain - clear tracked order IDs.
        grid_rec = self._grids.get(symbol)
        if grid_rec is not None and grid_rec.get("order_ids") is not None:
            grid_rec["order_ids"] = set()

        # Get all positions for this symbol
        try:
            positions = self.client.get_positions()
        except Exception as e:
            logger.error(f"Failed to get positions for {symbol}: {e}")
            result["errors"].append(f"Get positions failed: {e}")
            return result

        # Process each position
        for pos in positions:
            if pos.get("symbol") != symbol:
                continue

            qty = abs(float(pos.get("amount", 0)))
            if qty <= 0:
                continue

            # Determine position side
            raw_side = str(pos.get("side", "")).lower()
            if "long" in raw_side or "bid" in raw_side or "buy" in raw_side:
                position_side = "long"
            elif "short" in raw_side or "ask" in raw_side or "sell" in raw_side:
                position_side = "short"
            else:
                logger.warning(
                    f"Unknown position side '{raw_side}' for {symbol} - closing for safety"
                )
                position_side = "unknown"

            # Determine if position is against trend
            # trend='up' and LONG → WITH trend (KEEP)
            # trend='up' and SHORT → AGAINST trend (CLOSE)
            # trend='down' and LONG → AGAINST trend (CLOSE)
            # trend='down' and SHORT → WITH trend (KEEP)
            against_trend = False
            if position_side == "unknown":
                against_trend = True  # Close unknown positions for safety
            elif trend_direction == "up" and position_side == "short":
                against_trend = True
            elif trend_direction == "down" and position_side == "long":
                against_trend = True

            if against_trend:
                # CLOSE against-trend position.  Sized from the exchange,
                # reduce-only, and accounted as closed ONLY on confirmed
                # execution; a rejection / exception leaves the DB row and
                # closed_positions untouched and fails the partial exit so
                # the caller falls back to _force_exit (which retries).
                try:
                    outcome = self._close_position_reduce_only(
                        symbol, position_side, qty, "against_trend"
                    )
                except Exception as e:  # noqa: BLE001 - status stays unchanged
                    logger.error(f"Failed to close position for {symbol}: {e}")
                    result["errors"].append(f"Close position failed: {e}")
                    return result
                if not outcome.confirmed_flat:
                    logger.error(
                        f"Against-trend close NOT confirmed for {symbol} "
                        f"{position_side} {qty}: {outcome.status} - "
                        f"{outcome.detail}; status left unchanged"
                    )
                    result["errors"].append(
                        f"Close position failed ({position_side} {qty}): "
                        f"{outcome.status} - {outcome.detail}"
                    )
                    return result  # Fail if any close fails

                result["closed_positions"].append(
                    {
                        "side": position_side,
                        "qty": qty,
                        "executed": outcome.executed,
                        "reason": "against_trend",
                    }
                )
                logger.warning(
                    f"📊 Against-trend position CLOSED: {symbol} {position_side} {qty} "
                    f"(trend={trend_direction}, {outcome.status})"
                )
                self._update_position_status(
                    symbol, position_side, qty, "closed", "against_trend"
                )
            else:
                # KEEP with-trend position (migrate to trend-following)
                # Get entry price from position data (or use current price as fallback)
                entry_price = float(
                    pos.get("entry_price")
                    or pos.get("avg_price")
                    or pos.get("price", 0)
                )

                result["kept_positions"].append(
                    {
                        "side": position_side,
                        "qty": qty,
                        "entry_price": entry_price,
                        "reason": "trend_aligned",
                    }
                )

                logger.info(
                    f"📊 With-trend position KEPT: {symbol} {position_side} {qty} "
                    f"(trend={trend_direction}) - migrating to trend-following"
                )

                # Register migrated position with RiskManager
                self.risk_manager.register_migrated_position(
                    symbol,
                    {
                        "side": position_side,
                        "qty": qty,
                        "entry_price": entry_price,
                        "trend_direction": trend_direction,
                        "has_stop": False,  # Stop will be added by MigratedPositionManager
                    },
                )

                # Update database record for migration
                self._update_position_status(
                    symbol, position_side, qty, "migrated", "trend_aligned"
                )

        # Update grid state
        if symbol in self._grids:
            if result["kept_positions"]:
                # Mark grid as disabled but with migrated positions
                self._grids[symbol]["state"] = GridState.DISABLED_BY_REGIME
                self._grids[symbol]["has_migrated_positions"] = True
                self._grids[symbol]["migrated_positions"] = result["kept_positions"]
                self._grids[symbol]["trend_direction"] = trend_direction

                logger.info(
                    f"📊 Partial grid exit complete for {symbol}: "
                    f"closed={len(result['closed_positions'])}, kept={len(result['kept_positions'])}"
                )
            else:
                # No positions kept - clear grid entirely
                del self._grids[symbol]
                self.delete_grid_state(symbol)

        # Reset grid exposure in RiskManager (grid orders are gone)
        # Migrated positions will be tracked separately
        self.risk_manager.on_grid_emergency_exit(symbol)

        result["success"] = True
        logger.critical(
            f"📊 Partial grid unwind complete for {symbol} ({reason}): "
            f"trend={trend_direction}, closed={len(result['closed_positions'])}, "
            f"migrated={len(result['kept_positions'])}"
        )

        return result

    def _update_position_status(
        self, symbol: str, side: str, qty: float, status: str, exit_reason: str
    ):
        """
        Update position status in database.

        Args:
            symbol: Trading symbol
            side: Position side ('long' or 'short')
            qty: Position quantity
            status: New status ('closed' or 'migrated')
            exit_reason: Reason for the status change
        """
        if not self.db:
            return

        try:
            from .database import get_db_connection

            with get_db_connection() as conn:
                conn.execute(
                    """
                    UPDATE grid_positions
                    SET status = ?, exit_reason = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE symbol = ? AND side = ? AND status = 'open'
                    ORDER BY created_at DESC
                    LIMIT 1
                """,
                    (status, exit_reason, symbol, side),
                )
                conn.commit()
                logger.debug(
                    f"Updated position status: {symbol} {side} -> {status} ({exit_reason})"
                )
        except Exception as e:
            logger.warning(f"Could not update position status in DB: {e}")

    # =========================
    # HOUSEKEEPING
    # =========================

    def clear_all(self):
        """Force-clear ALL grids (shutdown safety)."""
        for symbol in list(self._grids.keys()):
            self._force_exit(symbol, reason="SYSTEM_SHUTDOWN")

    # =========================
    # ACTIVE MONITORING
    # =========================

    def monitor_grids(self, current_prices: Dict[str, float] = None) -> Dict[str, Any]:
        """
        Main monitoring method - call this periodically from the trading loop.

        1. Fetch recent trades from exchange
        2. Match trades to active grids
        3. Update fill tracking and metrics
        4. Check emergency stop conditions
        5. Calculate P&L

        Args:
            current_prices: Optional dict of symbol -> current price for unrealized P&L

        Returns:
            Dict with monitoring results
        """
        results = {
            "grids_monitored": 0,
            "new_fills": 0,
            "completed_round_trips": 0,
            "flatten_retries": 0,
            "alerts": [],
        }

        # Incomplete force exits are retried BEFORE the active-grid pass so
        # a grid parked in EMERGENCY_EXIT keeps being flattened each loop.
        if self._pending_flattens:
            try:
                results["flatten_retries"] = self.retry_pending_flattens()
            except Exception as e:  # noqa: BLE001 - never block monitoring
                logger.error(f"Pending flatten retry error: {e}")
                results["alerts"].append(f"Flatten retry error: {e}")

        if not self._grids:
            return results

        try:
            # Fetch recent trades from exchange
            trades = self.client.get_trades(limit=100)

            for symbol in list(self._grids.keys()):
                if self._grids[symbol]["state"] != GridState.ACTIVE:
                    continue

                results["grids_monitored"] += 1

                # Process fills for this symbol
                symbol_trades = [
                    t
                    for t in trades
                    if (t.get("symbol") or t.get("market", "").replace("-PERP", ""))
                    == symbol
                ]

                new_fills = self._process_trades(symbol, symbol_trades)
                results["new_fills"] += new_fills

                # Calculate round-trips and P&L
                round_trips = self._calculate_round_trips(symbol)
                results["completed_round_trips"] += round_trips

                # Check emergency stop
                current_price = (current_prices or {}).get(symbol)
                if current_price:
                    self._check_emergency_stop(symbol, current_price)
                    self._update_unrealized_pnl(symbol, current_price)

                # Periodically update DB with latest metrics (on new fills)
                if new_fills > 0:
                    self.update_grid_metrics_in_db(symbol)

            self._last_fill_check = datetime.now(timezone.utc)

        except Exception as e:
            logger.error(f"Grid monitoring error: {e}")
            results["alerts"].append(f"Monitoring error: {e}")

        return results

    def _process_trades(self, symbol: str, trades: List[Dict]) -> int:
        """
        Process trades from exchange and record new fills.

        Fill attribution: when the grid tracks order IDs (grid["order_ids"]
        is a set), only trades whose order_id is in that set are treated as
        grid fills; everything else on the symbol (overlay strategies such
        as LiquidationCapture / FundingArb / OrderBookImbalance) is ignored.
        Legacy grids without tracked IDs keep the old attribute-everything
        behavior, with a one-time WARNING per grid.

        Returns:
            Number of new fills processed
        """
        if symbol not in self._processed_trades:
            self._processed_trades[symbol] = set()

        if symbol not in self._fills:
            self._fills[symbol] = []

        if symbol not in self._metrics:
            self._metrics[symbol] = GridMetrics()

        grid = self._grids.get(symbol)
        known_ids = grid.get("order_ids") if grid is not None else None

        if (
            trades
            and grid is not None
            and known_ids is None
            and not grid.get("order_id_warning_emitted")
        ):
            grid["order_id_warning_emitted"] = True
            logger.warning(
                f"Grid for {symbol} has no tracked order IDs (legacy grid) - "
                f"attributing ALL account trades on this symbol to the grid. "
                f"Overlay-strategy fills may corrupt grid PnL/replenishment."
            )

        new_fills = 0

        for trade in trades:
            trade_id = str(
                trade.get("trade_id") or trade.get("history_id") or trade.get("id", "")
            )

            # Skip if already processed
            if trade_id in self._processed_trades[symbol]:
                continue

            # STRICT ATTRIBUTION: when order IDs are tracked, only fills of
            # known grid orders belong to the grid. Non-matching fills are
            # marked processed so they are not re-examined every cycle.
            fill_order_id = str(trade.get("order_id", ""))
            if known_ids is not None and fill_order_id not in known_ids:
                if trade_id:
                    self._processed_trades[symbol].add(trade_id)
                logger.debug(
                    f"Ignoring non-grid fill for {symbol} "
                    f"(order_id={fill_order_id or 'unknown'}, trade={trade_id})"
                )
                continue

            # Determine side
            raw_side = str(trade.get("side", "")).lower()
            if "long" in raw_side or "bid" in raw_side or "buy" in raw_side:
                side = "BUY"
            elif "short" in raw_side or "ask" in raw_side or "sell" in raw_side:
                side = "SELL"
            else:
                continue  # Unknown side, skip

            # Extract fill data
            try:
                price = float(trade.get("price", 0))
                quantity = float(
                    trade.get("amount") or trade.get("size") or trade.get("quantity", 0)
                )
                fee = float(trade.get("fee", 0))
                order_id = fill_order_id

                # Parse timestamp - ALWAYS timezone-aware UTC. Mixing naive
                # and aware datetimes makes the FIFO sort in
                # _calculate_round_trips raise TypeError, silently breaking
                # round-trip P&L matching.
                ts = trade.get("timestamp") or trade.get("created_at")
                if isinstance(ts, (int, float)):
                    timestamp = datetime.fromtimestamp(
                        ts / 1000 if ts > 1e12 else ts, tz=timezone.utc
                    )
                elif isinstance(ts, str):
                    timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                else:
                    timestamp = datetime.now(timezone.utc)

            except (ValueError, TypeError) as e:
                logger.warning(f"Invalid trade data for {symbol}: {e}")
                continue

            # Create fill record
            fill = GridFill(
                trade_id=trade_id,
                order_id=order_id,
                side=side,
                price=price,
                quantity=quantity,
                fee=fee,
                timestamp=timestamp,
            )

            self._fills[symbol].append(fill)
            self._processed_trades[symbol].add(trade_id)
            new_fills += 1

            # Filled order is no longer live - rotate it out of the ID set.
            # Its replacement is registered by _replenish_order below.
            if known_ids is not None and fill_order_id:
                known_ids.discard(fill_order_id)

            # Update metrics
            metrics = self._metrics[symbol]
            metrics.total_fees += fee

            if side == "BUY":
                metrics.total_buy_fills += 1
                metrics.total_buy_quantity += quantity
                metrics.total_buy_value += quantity * price
                metrics.net_position += quantity
                if metrics.total_buy_quantity > 0:
                    metrics.avg_buy_price = (
                        metrics.total_buy_value / metrics.total_buy_quantity
                    )
            else:  # SELL
                metrics.total_sell_fills += 1
                metrics.total_sell_quantity += quantity
                metrics.total_sell_value += quantity * price
                metrics.net_position -= quantity
                if metrics.total_sell_quantity > 0:
                    metrics.avg_sell_price = (
                        metrics.total_sell_value / metrics.total_sell_quantity
                    )

            logger.info(
                f"📊 Grid fill: {symbol} {side} {quantity:.6f} @ ${price:.2f} "
                f"(fee: ${fee:.6f}, net pos: {metrics.net_position:.6f})"
            )

            # REPLENISH: Place counter order on opposite side
            self._replenish_order(symbol, side, price, quantity)

        return new_fills

    @staticmethod
    def _round_decimals_for_price(price: float) -> tuple:
        """
        Return (tick_decimals, lot_decimals) for a given price magnitude.

        Mirrors TradingBot._calculate_grid_levels so replenished counter
        orders land on the same price/quantity granularity as the original
        grid levels.

        Args:
            price: Reference price for the symbol.

        Returns:
            Tuple of (tick_decimals, lot_decimals).
        """
        if price >= 100:
            return 2, 4
        if price >= 1:
            return 3, 2
        return 5, 1

    def _replenish_order(self, symbol: str, filled_side: str, fill_price: float, fill_quantity: float):
        """
        Place a counter order when a grid order is filled.

        Grid trading logic:
        - BUY filled -> Place SELL order at fill_price + spacing (take profit)
        - SELL filled -> Place BUY order at fill_price - spacing (take profit)

        This maintains the grid and captures oscillations.
        """
        if symbol not in self._grids:
            return

        grid = self._grids[symbol]
        if grid["state"] != GridState.ACTIVE:
            logger.debug(f"Grid not active for {symbol}, skipping replenishment")
            return

        spacing = grid.get("grid_spacing", 0)
        if not spacing or spacing <= 0:
            logger.warning(f"No valid grid spacing for {symbol}, cannot replenish")
            return

        # SANITY: spacing is a DOLLAR offset. A spacing above 20% of the fill
        # price indicates corrupted state (e.g. legacy rows that stored a
        # relative fraction, or unit mix-ups) - skip rather than place a wild
        # order far from market.
        if fill_price > 0 and spacing / fill_price > 0.20:
            logger.error(
                f"Grid spacing {spacing} looks invalid for {symbol} "
                f"(> 20% of fill price {fill_price}) - skipping replenish"
            )
            return

        try:
            # Determine counter order parameters
            if filled_side == "BUY":
                # Buy was filled -> place sell at higher price
                counter_side = "sell"
                counter_price = fill_price + spacing
            else:  # SELL was filled
                # Sell was filled -> place buy at lower price
                counter_side = "buy"
                counter_price = fill_price - spacing

            # Round price/quantity with the SAME price-magnitude heuristic
            # used when the grid levels were originally placed
            # (TradingBot._calculate_grid_levels). The previous hardcoded
            # 0.01 tick collapsed sub-cent ladders on low-priced symbols.
            tick_decimals, lot_decimals = self._round_decimals_for_price(
                fill_price
            )
            counter_price = round(counter_price, tick_decimals)

            if counter_price <= 0:
                logger.warning(
                    f"Counter price non-positive for {symbol}: {counter_price}"
                )
                return

            # Use same quantity as filled order (rounded to lot size)
            counter_quantity = round(fill_quantity, lot_decimals)
            min_lot = 10 ** (-lot_decimals)

            if counter_quantity < min_lot:
                logger.warning(f"Counter quantity too small for {symbol}: {counter_quantity}")
                return

            # Place the counter order
            order_result = self.client.place_order(
                symbol, counter_side, counter_quantity, "limit", counter_price
            )

            # Register the replacement order ID so its future fill is
            # attributed to the grid (only for ID-tracking grids).
            if grid.get("order_ids") is not None:
                new_order_id = self._extract_order_id_from_response(
                    order_result
                )
                if new_order_id:
                    grid["order_ids"].add(new_order_id)
                else:
                    logger.error(
                        f"Grid replenish for {symbol}: could not extract "
                        f"order ID from response - the replacement order's "
                        f"fill will NOT be attributed to the grid"
                    )

            logger.info(
                f"📊 Grid REPLENISH: {symbol} {counter_side.upper()} {counter_quantity:.6f} @ ${counter_price:.2f} "
                f"(triggered by {filled_side} fill @ ${fill_price:.2f})"
            )

        except Exception as e:
            logger.error(f"Failed to replenish grid order for {symbol}: {e}")

    def _calculate_round_trips(self, symbol: str) -> int:
        """
        Calculate completed round-trips (buy + sell pairs) and realized P&L.

        A round-trip is when we have both buy and sell fills that can be matched.
        P&L = (sell_price - buy_price) * quantity - fees

        Uses FIFO matching with proper partial fill tracking.

        Returns:
            Number of new round-trips completed
        """
        if symbol not in self._fills or symbol not in self._metrics:
            return 0

        fills = self._fills[symbol]
        metrics = self._metrics[symbol]

        # Get fills with remaining unmatched quantity
        unmatched_buys = [
            f for f in fills if f.side == "BUY" and f.remaining_quantity > 0.0000001
        ]
        unmatched_sells = [
            f for f in fills if f.side == "SELL" and f.remaining_quantity > 0.0000001
        ]

        # Sort by timestamp (oldest first for FIFO matching)
        unmatched_buys.sort(key=lambda f: f.timestamp)
        unmatched_sells.sort(key=lambda f: f.timestamp)

        round_trips = 0
        buy_idx = 0
        sell_idx = 0

        while buy_idx < len(unmatched_buys) and sell_idx < len(unmatched_sells):
            buy_fill = unmatched_buys[buy_idx]
            sell_fill = unmatched_sells[sell_idx]

            # Match using REMAINING quantities (not original)
            match_qty = min(buy_fill.remaining_quantity, sell_fill.remaining_quantity)

            if match_qty < 0.0000001:  # Skip negligible matches
                buy_idx += 1
                continue

            # Calculate P&L for this match
            gross_pnl = (sell_fill.price - buy_fill.price) * match_qty

            # Allocate fees proportionally based on matched portion
            buy_fee_portion = (
                buy_fill.fee * (match_qty / buy_fill.quantity)
                if buy_fill.quantity > 0
                else 0
            )
            sell_fee_portion = (
                sell_fill.fee * (match_qty / sell_fill.quantity)
                if sell_fill.quantity > 0
                else 0
            )
            net_pnl = gross_pnl - buy_fee_portion - sell_fee_portion

            metrics.realized_pnl += net_pnl
            metrics.completed_round_trips += 1
            round_trips += 1

            logger.info(
                f"📊 Grid round-trip: {symbol} "
                f"BUY @ ${buy_fill.price:.2f} → SELL @ ${sell_fill.price:.2f} "
                f"qty={match_qty:.6f}, P&L=${net_pnl:.4f}"
            )

            # Track matched quantities
            buy_fill.matched_quantity += match_qty
            sell_fill.matched_quantity += match_qty

            # Mark as fully matched if no remaining quantity
            if buy_fill.remaining_quantity < 0.0000001:
                buy_fill.matched = True
                buy_idx += 1
            if sell_fill.remaining_quantity < 0.0000001:
                sell_fill.matched = True
                sell_idx += 1

        return round_trips

    def _check_emergency_stop(self, symbol: str, current_price: float):
        """Check if emergency stop price has been breached."""
        if symbol not in self._grids:
            return

        grid = self._grids[symbol]
        emergency_stop = grid.get("emergency_stop", 0)

        if emergency_stop <= 0:
            return

        # Check if price has breached emergency stop
        # For grids, emergency stop is typically below entry (protecting against crash)
        if current_price <= emergency_stop:
            logger.critical(
                f"🚨 EMERGENCY STOP BREACHED for {symbol}: "
                f"price ${current_price:.2f} <= stop ${emergency_stop:.2f}"
            )
            self.on_emergency_stop_triggered(symbol)

    def _update_unrealized_pnl(self, symbol: str, current_price: float):
        """Update unrealized P&L based on current price."""
        if symbol not in self._metrics:
            return

        metrics = self._metrics[symbol]

        # Unrealized P&L = net_position * (current_price - avg_entry_price)
        if metrics.net_position > 0:  # Long position
            avg_entry = (
                metrics.avg_buy_price if metrics.avg_buy_price > 0 else current_price
            )
            metrics.unrealized_pnl = metrics.net_position * (current_price - avg_entry)
        elif metrics.net_position < 0:  # Short position
            avg_entry = (
                metrics.avg_sell_price if metrics.avg_sell_price > 0 else current_price
            )
            metrics.unrealized_pnl = abs(metrics.net_position) * (
                avg_entry - current_price
            )
        else:
            metrics.unrealized_pnl = 0.0

    # =========================
    # SYNC FROM EXCHANGE
    # =========================

    def sync_from_exchange(self, symbol: str) -> bool:
        """
        Synchronize grid state from exchange orders.
        Call this on startup to detect existing grids.

        Returns:
            True if grid was detected and registered
        """
        try:
            # Get open orders for symbol
            orders = self.client.get_open_orders(market=symbol)
            limit_orders = [
                o for o in orders if o.get("order_type", o.get("type", "")) == "limit"
            ]

            if len(limit_orders) < 5:
                return False  # Not enough orders to be a grid

            # Detect grid parameters from orders
            buy_orders = [
                o
                for o in limit_orders
                if "bid" in str(o.get("side", "")).lower()
                or "buy" in str(o.get("side", "")).lower()
            ]
            sell_orders = [
                o
                for o in limit_orders
                if "ask" in str(o.get("side", "")).lower()
                or "sell" in str(o.get("side", "")).lower()
            ]

            if not buy_orders and not sell_orders:
                return False

            # Calculate grid capital from order sizes
            total_value = 0
            for order in limit_orders:
                price = float(order.get("price", 0))
                qty = float(
                    order.get("amount") or order.get("size") or order.get("quantity", 0)
                )
                total_value += price * qty

            # Estimate emergency stop (5% below lowest buy order)
            if buy_orders:
                lowest_buy = min(float(o.get("price", 0)) for o in buy_orders)
                emergency_stop = lowest_buy * 0.95
            else:
                emergency_stop = 0

            # Register the grid
            if symbol not in self._grids:
                self._grids[symbol] = {
                    "state": GridState.ACTIVE,
                    "grid_capital": total_value,
                    "emergency_stop": emergency_stop,
                    "synced_from_exchange": True,
                    "order_count": len(limit_orders),
                    "buy_orders": len(buy_orders),
                    "sell_orders": len(sell_orders),
                }

                # Track order IDs from the open-orders response for strict
                # fill attribution after sync.
                synced_ids = {
                    oid
                    for oid in (
                        self._extract_order_id_from_order(o)
                        for o in limit_orders
                    )
                    if oid
                }
                if synced_ids:
                    self._grids[symbol]["order_ids"] = synced_ids

                # Initialize metrics
                self._metrics[symbol] = GridMetrics()
                self._fills[symbol] = []
                self._processed_trades[symbol] = set()

                logger.info(
                    f"📊 Grid synced from exchange: {symbol} - "
                    f"{len(limit_orders)} orders, capital≈${total_value:.2f}"
                )

                # Sync recent fills
                self._sync_recent_fills(symbol)

                return True

        except Exception as e:
            logger.error(f"Failed to sync grid from exchange for {symbol}: {e}")

        return False

    def _sync_recent_fills(self, symbol: str):
        """Sync recent trade history for a newly detected grid."""
        try:
            trades = self.client.get_trades(limit=100)
            symbol_trades = [
                t
                for t in trades
                if (t.get("symbol") or t.get("market", "").replace("-PERP", ""))
                == symbol
            ]

            if symbol_trades:
                new_fills = self._process_trades(symbol, symbol_trades)
                self._calculate_round_trips(symbol)
                logger.info(f"📊 Synced {new_fills} historical fills for {symbol}")

        except Exception as e:
            logger.warning(f"Could not sync fills for {symbol}: {e}")

    # =========================
    # STATUS & REPORTING
    # =========================

    def get_grid_status(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get comprehensive grid status including metrics."""
        if symbol not in self._grids:
            return None

        grid = self._grids[symbol]
        metrics = self._metrics.get(symbol, GridMetrics())

        return {
            "symbol": symbol,
            "state": grid["state"].value,
            "grid_capital": grid.get("grid_capital", 0),
            "emergency_stop": grid.get("emergency_stop", 0),
            "metrics": {
                "total_buy_fills": metrics.total_buy_fills,
                "total_sell_fills": metrics.total_sell_fills,
                "total_buy_quantity": metrics.total_buy_quantity,
                "total_sell_quantity": metrics.total_sell_quantity,
                "avg_buy_price": metrics.avg_buy_price,
                "avg_sell_price": metrics.avg_sell_price,
                "net_position": metrics.net_position,
                "total_fees": metrics.total_fees,
                "realized_pnl": metrics.realized_pnl,
                "unrealized_pnl": metrics.unrealized_pnl,
                "total_pnl": metrics.realized_pnl + metrics.unrealized_pnl,
                "completed_round_trips": metrics.completed_round_trips,
            },
            "synced_from_exchange": grid.get("synced_from_exchange", False),
        }

    def get_all_active_grids(self) -> List[Dict[str, Any]]:
        """Get status for all active grids."""
        active_grids = []
        for symbol in self._grids:
            if self._grids[symbol]["state"] == GridState.ACTIVE:
                status = self.get_grid_status(symbol)
                if status:
                    active_grids.append(status)
        return active_grids

    def cleanup_completed_grids(self) -> int:
        """
        Remove CLOSED grids from memory to free resources.

        Grids enter the CLOSED state after emergency exit or regime-based
        shutdown. They are retained in ``_grids`` until this method is called
        so that status queries can still inspect them after the fact.

        Returns:
            Number of grids removed from memory.
        """
        removed = 0
        for symbol in list(self._grids.keys()):
            if self._grids[symbol].get("state") == GridState.CLOSED:
                del self._grids[symbol]
                self._fills.pop(symbol, None)
                self._processed_trades.pop(symbol, None)
                self._metrics.pop(symbol, None)
                removed += 1
                logger.info(f"🗑️ Cleaned up closed grid for {symbol}")
        return removed

    def get_grid_statistics(self) -> Dict[str, Any]:
        """Get aggregate statistics across all grids."""
        total_realized_pnl = 0.0
        total_unrealized_pnl = 0.0
        total_fees = 0.0
        total_round_trips = 0

        for symbol, metrics in self._metrics.items():
            total_realized_pnl += metrics.realized_pnl
            total_unrealized_pnl += metrics.unrealized_pnl
            total_fees += metrics.total_fees
            total_round_trips += metrics.completed_round_trips

        return {
            "total_active_grids": sum(
                1 for g in self._grids.values() if g["state"] == GridState.ACTIVE
            ),
            "total_realized_pnl": total_realized_pnl,
            "total_unrealized_pnl": total_unrealized_pnl,
            "total_pnl": total_realized_pnl + total_unrealized_pnl,
            "total_fees": total_fees,
            "total_round_trips": total_round_trips,
            "last_check": self._last_fill_check.isoformat()
            if self._last_fill_check
            else None,
        }

    def validate_grid_creation(self, symbol: str, capital: float) -> bool:
        """Validate if a new grid can be created for this symbol."""
        if symbol in self._grids:
            return False
        return True

    def is_healthy(self) -> bool:
        """Check if the grid manager is healthy."""
        return True

    def update_grid_levels(self, symbol: str, buy_levels: int, sell_levels: int):
        """Update grid level counts (for tracking)."""
        if symbol in self._grids:
            self._grids[symbol]["buy_levels"] = buy_levels
            self._grids[symbol]["sell_levels"] = sell_levels

    # =========================
    # DATABASE PERSISTENCE
    # =========================

    def save_grid_state(
        self, symbol: str, regime: str = None, atr: float = 0, spacing: float = 0
    ):
        """
        Save grid state to database for persistence across restarts.

        Args:
            symbol: Trading symbol
            regime: Market regime when grid was created (e.g., 'ranging_volatile')
            atr: ATR value at creation time
            spacing: Grid spacing used
        """
        if not self.db or symbol not in self._grids:
            return

        grid = self._grids[symbol]
        metrics = self._metrics.get(symbol, GridMetrics())

        # Use grid data for regime/atr/spacing if not explicitly provided
        if not regime:
            regime = grid.get("regime_on_creation", "")
        if atr == 0:
            atr = grid.get("atr_at_creation", 0)
        if spacing == 0:
            spacing = grid.get("grid_spacing", 0)

        # Build repair history JSON if present
        import json
        repair_history = None
        if grid.get("repair_issues") or grid.get("readopted"):
            repair_entries = grid.get("repair_history_entries", [])
            if grid.get("repair_issues"):
                repair_entries.append({
                    "at": grid.get("repaired_at", datetime.now(timezone.utc)).isoformat() if isinstance(grid.get("repaired_at"), datetime) else str(grid.get("repaired_at", "")),
                    "issues": grid.get("repair_issues", []),
                })
            if grid.get("readopted"):
                repair_entries.append({
                    "at": grid.get("readopted_at", datetime.now(timezone.utc)).isoformat() if isinstance(grid.get("readopted_at"), datetime) else str(grid.get("readopted_at", "")),
                    "type": "readopted_from_exchange",
                })
            repair_history = json.dumps(repair_entries[-10:])  # Keep last 10

        # Serialize tracked order IDs (None => legacy grid, stored as NULL)
        order_ids_json = None
        if grid.get("order_ids") is not None:
            order_ids_json = json.dumps(sorted(grid["order_ids"]))

        try:
            from .database import get_db_connection

            with get_db_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO grid_states
                    (symbol, state, regime_on_creation, grid_capital, emergency_stop,
                     atr_at_creation, grid_spacing, num_levels, total_buy_fills,
                     total_sell_fills, realized_pnl, total_fees,
                     center_price, initial_center, orders_placed, refresh_count,
                     last_refresh, consistency_checked_at, repair_history,
                     order_ids, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?,
                            CURRENT_TIMESTAMP)
                """,
                    (
                        symbol,
                        grid["state"].value
                        if isinstance(grid["state"], GridState)
                        else grid["state"],
                        regime,
                        grid.get("grid_capital", 0),
                        grid.get("emergency_stop", 0),
                        atr,
                        spacing,
                        grid.get("num_levels", 10),
                        metrics.total_buy_fills,
                        metrics.total_sell_fills,
                        metrics.realized_pnl,
                        metrics.total_fees,
                        grid.get("center_price"),
                        grid.get("initial_center"),
                        grid.get("orders_placed", 0),
                        grid.get("refresh_count", 0),
                        grid.get("last_refresh"),
                        datetime.now(timezone.utc).isoformat(),
                        repair_history,
                        order_ids_json,
                    ),
                )
                conn.commit()
            logger.debug(f"Grid state saved to DB: {symbol}")
        except Exception as e:
            logger.error(f"Failed to save grid state for {symbol}: {e}")

    def load_grid_states(self, regime_detector=None) -> Dict[str, Dict]:
        """
        Load grid states from database on startup.
        Validates regime if detector provided - closes grids in wrong regime.

        Args:
            regime_detector: Optional MarketRegimeDetector for regime validation

        Returns:
            Dict of loaded grids that passed validation
        """
        if not self.db:
            return {}

        loaded_grids = {}

        try:
            from .database import get_db_connection

            with get_db_connection() as conn:
                cursor = conn.execute("""
                    SELECT symbol, state, regime_on_creation, grid_capital, emergency_stop,
                           atr_at_creation, grid_spacing, num_levels, total_buy_fills,
                           total_sell_fills, realized_pnl, total_fees,
                           center_price, initial_center, orders_placed, refresh_count,
                           last_refresh, repair_history, order_ids
                    FROM grid_states
                    WHERE state = 'active'
                """)

                rows = cursor.fetchall()
                for row in rows:
                    symbol = row[0]
                    regime_on_creation = row[2]

                    # Validate regime if detector provided
                    if regime_detector:
                        current_regime = regime_detector.get_cached_regime(symbol)
                        if current_regime:
                            current_regime_str = current_regime.value

                            # Grid may only keep running in allowed regimes
                            # (single source of truth: GRID_ALLOWED_REGIMES)
                            if current_regime_str not in GRID_ALLOWED_REGIMES:
                                logger.warning(
                                    f"⚠️ Grid {symbol} created in {regime_on_creation}, "
                                    f"but current regime is {current_regime_str} - CLOSING"
                                )
                                # Flatten on the exchange.  The DB row is
                                # deleted only once every position is
                                # confirmed flat (_complete_force_exit);
                                # otherwise it survives for the next restart.
                                self._force_exit(
                                    symbol,
                                    reason=f"REGIME_MISMATCH:{current_regime_str}",
                                )
                                continue

                    # Load into memory with full column set
                    self._grids[symbol] = {
                        "state": GridState.ACTIVE,
                        "grid_capital": row[3],
                        "emergency_stop": row[4],
                        "atr_at_creation": row[5],
                        "grid_spacing": row[6],
                        "num_levels": row[7],
                        "loaded_from_db": True,
                        "regime_on_creation": regime_on_creation,
                        "center_price": row[12],
                        "initial_center": row[13],
                        "orders_placed": row[14] or 0,
                        "refresh_count": row[15] or 0,
                        "last_refresh": row[16],
                    }

                    # Restore tracked order IDs (NULL => legacy grid that
                    # keeps attribute-everything fill behavior)
                    if row[18]:
                        try:
                            self._grids[symbol]["order_ids"] = {
                                str(oid) for oid in json.loads(row[18])
                            }
                        except (ValueError, TypeError) as e:
                            logger.warning(
                                f"Could not parse stored order_ids for "
                                f"{symbol}: {e}"
                            )

                    # Initialize metrics from DB
                    self._metrics[symbol] = GridMetrics(
                        total_buy_fills=row[8],
                        total_sell_fills=row[9],
                        realized_pnl=row[10],
                        total_fees=row[11],
                    )
                    self._fills[symbol] = []
                    self._processed_trades[symbol] = set()

                    loaded_grids[symbol] = self._grids[symbol]

                    center_str = f"center=${row[12]:.4f}" if row[12] else "no_center"
                    logger.info(
                        f"📊 Loaded grid from DB: {symbol} (regime: {regime_on_creation}, "
                        f"{center_str}, orders={row[14] or 0})"
                    )

        except Exception as e:
            logger.error(f"Failed to load grid states from DB: {e}")

        return loaded_grids

    def delete_grid_state(self, symbol: str):
        """Remove grid state from database when grid is closed."""
        if not self.db:
            return

        try:
            from .database import get_db_connection

            with get_db_connection() as conn:
                conn.execute("DELETE FROM grid_states WHERE symbol = ?", (symbol,))
                conn.commit()
            logger.debug(f"Grid state deleted from DB: {symbol}")
        except Exception as e:
            logger.error(f"Failed to delete grid state for {symbol}: {e}")

    def update_grid_metrics_in_db(self, symbol: str):
        """Update grid metrics in database (call periodically)."""
        if not self.db or symbol not in self._grids:
            return

        metrics = self._metrics.get(symbol, GridMetrics())

        # Keep persisted order IDs in sync with fill/replenish rotation so
        # a restart re-adopts the CURRENT live grid orders.
        order_ids_json = None
        if self._grids[symbol].get("order_ids") is not None:
            order_ids_json = json.dumps(sorted(self._grids[symbol]["order_ids"]))

        try:
            from .database import get_db_connection

            with get_db_connection() as conn:
                conn.execute(
                    """
                    UPDATE grid_states
                    SET total_buy_fills = ?, total_sell_fills = ?,
                        realized_pnl = ?, total_fees = ?, order_ids = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE symbol = ?
                """,
                    (
                        metrics.total_buy_fills,
                        metrics.total_sell_fills,
                        metrics.realized_pnl,
                        metrics.total_fees,
                        order_ids_json,
                        symbol,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to update grid metrics for {symbol}: {e}")
