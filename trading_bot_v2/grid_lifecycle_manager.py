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
from datetime import datetime, timedelta
from loguru import logger
from dataclasses import dataclass
import json
import asyncio
import uuid

from .config import config
from .universal_grid_state_consistency import UniversalGridStateConsistencyManager


class ResponseHandler:
    """Handles Pacifica API responses with robust validation and error handling."""

    @staticmethod
    def validate_order_response(response: Any) -> Dict[str, Any]:
        """
        Validate and normalize order response from Pacifica API.

        Args:
            response: Raw response from API (could be dict, string, etc.)

        Returns:
            Normalized response dict with expected format:
            {"success": bool, "data": dict, "error": Optional[str]}
        """
        logger.debug(f"Validating order response: {response} (type: {type(response)})")

        # Case 1: Response is already a dict (expected format)
        if isinstance(response, dict):
            return ResponseHandler._normalize_dict_response(response)

        # Case 2: Response is a boolean (direct API response)
        elif isinstance(response, bool):
            logger.warning(f"API returned boolean directly: {response}")
            return {
                "success": response,
                "data": {"status": "success" if response else "error"},
                "error": None if response else "API returned false",
            }

        # Case 3: Response is a string (could be JSON or just "success")
        elif isinstance(response, str):
            return ResponseHandler._handle_string_response(response)

        # Case 4: Response is None or unexpected type
        else:
            return {
                "success": False,
                "data": {},
                "error": f"Unexpected response type: {type(response).__name__}",
            }

    @staticmethod
    def _normalize_dict_response(response: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize dictionary response to expected format."""
        # Ensure we have the required fields
        normalized = {
            "success": bool(response.get("success", False)),
            "data": response.get("data", {}),
            "error": response.get("error") if not response.get("success") else None,
        }

        # Validate data field
        if not isinstance(normalized["data"], dict):
            logger.warning(f"Response data is not a dict: {normalized['data']}")
            normalized["data"] = {}

        return normalized

    @staticmethod
    def _handle_string_response(response: str) -> Dict[str, Any]:
        """Handle string response from API."""
        # Try to parse as JSON first
        try:
            parsed = json.loads(response)
            if isinstance(parsed, dict):
                return ResponseHandler._normalize_dict_response(parsed)
            else:
                # If parsed result is not a dict, handle based on content
                logger.warning(f"API returned non-dict JSON: {parsed}")
                if isinstance(parsed, bool):
                    return {
                        "success": parsed,
                        "data": {"status": "success" if parsed else "error"},
                        "error": None if parsed else "API returned false",
                    }
                elif isinstance(parsed, str) and parsed.lower() == "success":
                    return {
                        "success": True,
                        "data": {"status": "success"},
                        "error": None,
                    }
                elif isinstance(parsed, str) and parsed.lower() == "error":
                    return {
                        "success": False,
                        "data": {},
                        "error": "API returned error response",
                    }
                else:
                    # Default to treating as success for unknown types
                    return {
                        "success": True,
                        "data": {"raw_response": parsed},
                        "error": None,
                    }
        except json.JSONDecodeError:
            # Not valid JSON, treat raw string
            pass

        # Handle specific string responses
        if response.lower() == '"success"' or response.lower() == "success":
            logger.warning("API returned string 'success' instead of JSON object")
            return {"success": True, "data": {"status": "success"}, "error": None}
        elif response.lower() == '"error"' or response.lower() == "error":
            logger.error("API returned string 'error'")
            return {
                "success": False,
                "data": {},
                "error": "API returned error response",
            }
        else:
            # Unknown string response
            logger.error(f"API returned unexpected string response: {response}")
            return {
                "success": False,
                "data": {},
                "error": f"Unexpected API response: {response}",
            }


class GridState(Enum):
    IDLE = "idle"
    ACTIVE = "active"
    EMERGENCY_EXIT = "emergency_exit"
    DISABLED_BY_REGIME = "disabled_by_regime"
    CLOSED = "closed"


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
        self._last_fill_check: datetime = datetime.now() - timedelta(hours=1)

        # Market info cache for tick_size and lot_size compliance
        # Fetched from Pacifica /info endpoint
        self._market_info_cache: Dict[str, Dict[str, float]] = {}
        self._market_info_cache_timestamp: Optional[datetime] = None
        self._market_info_cache_ttl: int = 300  # 5 minutes TTL

        # Grid refresh/recenter configuration (from config)
        self.GRID_REFRESH_MIN_ATR_DRIFT: float = config.grid_refresh_min_atr_drift
        self.GRID_REFRESH_MIN_CONFIDENCE: float = config.grid_refresh_min_confidence
        self.GRID_REFRESH_MIN_CONF_IMPROVE: float = config.grid_refresh_min_conf_improve
        self.GRID_REFRESH_COOLDOWN_MINUTES: int = config.grid_refresh_cooldown_minutes
        self.GRID_REFRESH_MAX_PER_DAY: int = config.grid_refresh_max_per_day

        # Dynamic spacing configuration (from config)
        self.GRID_DYNAMIC_SPACING_ENABLED: bool = config.grid_dynamic_spacing_enabled
        self.GRID_DYNAMIC_SPACING_RECALC_MINUTES: int = (
            config.grid_dynamic_spacing_recalc_minutes
        )
        self.GRID_SPACING_VOLATILITY_MULTIPLIER: float = (
            config.grid_spacing_volatility_multiplier
        )

        # Safety thresholds (from config)
        self.GRID_EMERGENCY_DRIFT_THRESHOLD: float = (
            config.grid_emergency_drift_threshold
        )

        # Track refresh counts per symbol (daily reset)
        self._daily_refresh_counts: Dict[str, int] = {}
        self._last_reset_date: Optional[datetime] = None

        # Initialize universal grid state consistency manager
        self.consistency_manager = UniversalGridStateConsistencyManager(
            grid_lifecycle_manager=self, database_manager=self.db, client=self.client
        )

        # Initialize startup repair and validation
        self._initialize_grid_system()

    def _initialize_grid_system(self) -> None:
        """
        Initialize the grid system with startup repair and validation.

        This method is called during __init__ to:
        1. Load existing grid states from database
        2. Detect and repair orphaned grids
        3. Validate grid integrity
        4. Initialize daily tracking

        Note: Async validation is handled by the continuous monitoring system
        that runs every 60 seconds.
        """
        try:
            logger.info("🔧 Initializing GridLifecycleManager system...")

            # Reset daily counters if needed
            self._reset_daily_counters_if_needed()

            # Load existing grids from database (if available)
            if self.db:
                loaded_grids = self.load_grid_states(
                    regime_detector=self.regime_detector
                )
                logger.info(f"📊 Loaded {len(loaded_grids)} grids from database")

            # Legacy orphaned grid detection (as fallback)
            orphaned_repaired = self.detect_and_repair_orphaned_grids()
            if orphaned_repaired > 0:
                logger.info(
                    f"🔧 Legacy repair: Fixed {orphaned_repaired} orphaned grids on startup"
                )

            # Legacy grid integrity validation (as fallback)
            self._validate_grid_integrity()

            logger.info("✅ GridLifecycleManager system initialization complete")
            logger.info(
                "   (Async validation will be performed by continuous monitoring)"
            )

        except Exception as e:
            logger.error(f"❌ Grid system initialization failed: {e}", exc_info=True)

    def _reset_daily_counters_if_needed(self) -> None:
        """Reset daily refresh counters if date has changed."""
        today = datetime.now().date()
        should_reset = False

        if self._last_reset_date is None:
            should_reset = True
        else:
            should_reset = today != self._last_reset_date.date()

        if should_reset:
            self._daily_refresh_counts.clear()
            self._last_reset_date = datetime.now()
            logger.debug(f"📅 Reset daily grid refresh counters for {today}")

    def detect_and_repair_orphaned_grids(self) -> int:
        """
        Detect and repair orphaned grids on startup.

        Orphaned grids are those that:
        1. Have missing center_price or initial_center
        2. Have incomplete metadata
        3. Exist in DB but not on exchange
        4. Have invalid state or corrupted data

        Returns:
            Number of grids repaired
        """
        repaired_count = 0

        try:
            # Check in-memory grids for orphaned conditions
            orphaned_symbols = []

            for symbol, grid_data in self._grids.items():
                issues = []

                # Check for missing center price
                if not grid_data.get("center_price") and not grid_data.get(
                    "initial_center"
                ):
                    issues.append("missing_center_price")

                # Check for incomplete metadata
                required_fields = ["grid_capital", "grid_spacing", "num_levels"]
                for field in required_fields:
                    if field not in grid_data or grid_data[field] is None:
                        issues.append(f"missing_{field}")

                # Check for invalid state - handle both enum and string values
                state_value = grid_data.get("state")
                if state_value is not None:
                    # Convert enum to string value if needed for comparison
                    state_str = state_value.value if hasattr(state_value, 'value') else str(state_value)
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
                    # Close unrecoverable grids
                    self._close_unrecoverable_grid(symbol, "startup_repair_failed")

            # Sync with exchange to detect additional orphaned grids
            if repaired_count == 0:  # Only if no memory grids needed repair
                exchange_repaired = self._sync_grids_from_exchange()
                repaired_count += exchange_repaired

        except Exception as e:
            logger.error(
                f"Error in detect_and_repair_orphaned_grids: {e}", exc_info=True
            )

        return repaired_count

    def _repair_orphaned_grid(self, symbol: str, issues: List[str]) -> bool:
        """
        Attempt to repair an orphaned grid.

        Args:
            symbol: Trading symbol
            issues: List of identified issues

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
                    logger.info(
                        f"🔧 Reconstructed center price for {symbol}: ${center_price:.4f}"
                    )
                else:
                    # Cannot repair without center price
                    return False

            # Repair missing metadata with reasonable defaults
            if "missing_grid_spacing" in issues:
                # Calculate spacing from order spread
                spacing = self._calculate_spacing_from_exchange_orders(symbol)
                if spacing:
                    grid_data["grid_spacing"] = spacing
                else:
                    grid_data["grid_spacing"] = 0.004  # Default 0.4%

            if "missing_num_levels" in issues:
                levels = self._count_levels_from_exchange_orders(symbol)
                grid_data["num_levels"] = levels or 8  # Default 8 levels

            if "missing_grid_capital" in issues:
                capital = self._estimate_capital_from_exchange_orders(symbol)
                grid_data["grid_capital"] = capital or 1000.0  # Default fallback

            # Fix invalid state
            if "invalid_state" in issues:
                grid_data["state"] = GridState.ACTIVE

            # Add repair metadata
            grid_data["repaired_at"] = datetime.now()
            grid_data["repair_issues"] = issues

            # Persist the repaired grid state
            if self.db:
                self.save_grid_state(symbol)

            return True

        except Exception as e:
            logger.error(
                f"Error repairing orphaned grid for {symbol}: {e}", exc_info=True
            )
            return False

    def _close_unrecoverable_grid(self, symbol: str, reason: str) -> None:
        """
        Close a grid that cannot be repaired.

        Args:
            symbol: Trading symbol
            reason: Reason for closure
        """
        try:
            logger.warning(f"🗑️ Closing unrecoverable grid for {symbol}: {reason}")

            # Cancel all orders on exchange
            try:
                self.client.cancel_all_orders(symbol)
                logger.info(f"  ✅ Cancelled all orders for {symbol}")
            except Exception as e:
                logger.error(f"  ❌ Failed to cancel orders: {e}")

            # Remove from memory
            if symbol in self._grids:
                del self._grids[symbol]

            # Remove from database
            if self.db:
                self.delete_grid_state(symbol)

            # Clean up related data
            for data_dict in [self._fills, self._metrics, self._processed_trades]:
                if symbol in data_dict:
                    del data_dict[symbol]

            logger.info(f"🗑️ Successfully closed unrecoverable grid for {symbol}")

        except Exception as e:
            logger.error(
                f"Error closing unrecoverable grid for {symbol}: {e}", exc_info=True
            )

    def _calculate_center_from_exchange_orders(self, symbol: str) -> Optional[float]:
        """Calculate grid center price from current exchange orders."""
        try:
            orders = self.client.get_orders()
            symbol_orders = [o for o in orders if o.get("symbol") == symbol]

            if not symbol_orders:
                return None

            buy_orders = [
                o for o in symbol_orders if o.get("side", "").lower() in ["bid", "buy"]
            ]
            sell_orders = [
                o for o in symbol_orders if o.get("side", "").lower() in ["ask", "sell"]
            ]

            if not buy_orders or not sell_orders:
                return None

            buy_prices = [
                float(o.get("price", 0)) for o in buy_orders if o.get("price")
            ]
            sell_prices = [
                float(o.get("price", 0)) for o in sell_orders if o.get("price")
            ]

            if not buy_prices or not sell_prices:
                return None

            # Center is midpoint between highest buy and lowest sell
            center_price = (max(buy_prices) + min(sell_prices)) / 2
            return center_price

        except Exception as e:
            logger.error(
                f"Error calculating center from exchange orders for {symbol}: {e}"
            )
            return None

    def _calculate_spacing_from_exchange_orders(self, symbol: str) -> Optional[float]:
        """Calculate grid spacing from current exchange orders."""
        try:
            orders = self.client.get_orders()
            symbol_orders = [o for o in orders if o.get("symbol") == symbol]

            buy_orders = [
                o for o in symbol_orders if o.get("side", "").lower() in ["bid", "buy"]
            ]
            sell_orders = [
                o for o in symbol_orders if o.get("side", "").lower() in ["ask", "sell"]
            ]

            if not buy_orders or not sell_orders:
                return None

            buy_prices = sorted(
                [float(o.get("price", 0)) for o in buy_orders if o.get("price")],
                reverse=True,
            )
            sell_prices = sorted(
                [float(o.get("price", 0)) for o in sell_orders if o.get("price")]
            )

            if len(buy_prices) < 2 or len(sell_prices) < 2:
                return None

            # Calculate average spacing between adjacent orders
            spacings = []

            # Buy order spacings
            for i in range(len(buy_prices) - 1):
                spacing_pct = (buy_prices[i] - buy_prices[i + 1]) / buy_prices[i + 1]
                spacings.append(spacing_pct)

            # Sell order spacings
            for i in range(len(sell_prices) - 1):
                spacing_pct = (sell_prices[i + 1] - sell_prices[i]) / sell_prices[i]
                spacings.append(spacing_pct)

            if spacings:
                return sum(spacings) / len(spacings)

            return None

        except Exception as e:
            logger.error(
                f"Error calculating spacing from exchange orders for {symbol}: {e}"
            )
            return None

    def _count_levels_from_exchange_orders(self, symbol: str) -> Optional[int]:
        """Count grid levels from current exchange orders."""
        try:
            orders = self.client.get_orders()
            symbol_orders = [o for o in orders if o.get("symbol") == symbol]
            return len(symbol_orders)
        except Exception as e:
            logger.error(
                f"Error counting levels from exchange orders for {symbol}: {e}"
            )
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
                    order.get("quantity") or order.get("size") or order.get("amount", 0)
                )
                total_value += price * quantity

            return total_value
        except Exception as e:
            logger.error(
                f"Error estimating capital from exchange orders for {symbol}: {e}"
            )
            return None

    def _sync_grids_from_exchange(self) -> int:
        """
        Sync grids from exchange and detect additional orphaned grids.

        Returns:
            Number of grids repaired from exchange sync
        """
        repaired_count = 0

        try:
            # Get all symbols with grid-like order patterns
            orders = self.client.get_orders()
            orders_by_symbol = {}

            for order in orders:
                symbol = order.get("symbol")
                if symbol:
                    if symbol not in orders_by_symbol:
                        orders_by_symbol[symbol] = []
                    orders_by_symbol[symbol].append(order)

            # Check each symbol for grid patterns
            for symbol, symbol_orders in orders_by_symbol.items():
                # Skip if already managed
                if symbol in self._grids:
                    continue

                # Check if this looks like a grid (5+ orders, both sides)
                if len(symbol_orders) >= 5:
                    buy_orders = [
                        o
                        for o in symbol_orders
                        if o.get("side", "").lower() in ["bid", "buy"]
                    ]
                    sell_orders = [
                        o
                        for o in symbol_orders
                        if o.get("side", "").lower() in ["ask", "sell"]
                    ]

                    if buy_orders and sell_orders:
                        # This looks like an orphaned grid
                        logger.info(
                            f"🔍 Found potential orphaned grid on exchange: {symbol}"
                        )

                        if self._readopt_orphaned_grid_from_exchange(
                            symbol, buy_orders, sell_orders
                        ):
                            repaired_count += 1
                            logger.info(
                                f"✅ Re-adopted orphaned grid from exchange: {symbol}"
                            )

        except Exception as e:
            logger.error(f"Error syncing grids from exchange: {e}", exc_info=True)

        return repaired_count

    def _readopt_orphaned_grid_from_exchange(
        self, symbol: str, buy_orders: List[Dict], sell_orders: List[Dict]
    ) -> bool:
        """
        Re-adopt an orphaned grid from exchange orders.

        Args:
            symbol: Trading symbol
            buy_orders: List of buy orders
            sell_orders: List of sell orders

        Returns:
            True if successfully readopted
        """
        try:
            all_orders = buy_orders + sell_orders

            # Calculate grid parameters from orders
            center_price = self._calculate_center_from_exchange_orders(symbol)
            if not center_price:
                return False

            grid_spacing = self._calculate_spacing_from_exchange_orders(symbol)
            num_levels = len(all_orders)
            total_capital = self._estimate_capital_from_exchange_orders(symbol) or 0

            # Register the grid
            self._grids[symbol] = {
                "state": GridState.ACTIVE,
                "grid_capital": total_capital,
                "emergency_stop": 0,  # No emergency stop for readopted grids
                "regime_on_creation": "unknown_readopted",
                "atr_at_creation": 0,
                "grid_spacing": grid_spacing or 0.004,
                "num_levels": num_levels,
                "orders_placed": num_levels,
                "center_price": center_price,
                "initial_center": center_price,
                "created_at": datetime.now(),
                "refresh_count": 0,
                "readopted": True,
                "readopted_at": datetime.now(),
            }

            # Initialize tracking data
            self._fills[symbol] = []
            self._processed_trades[symbol] = set()
            self._metrics[symbol] = GridMetrics()

            # Persist to database
            if self.db:
                self.save_grid_state(symbol)

            return True

        except Exception as e:
            logger.error(
                f"Error readopting orphaned grid from exchange for {symbol}: {e}",
                exc_info=True,
            )
            return False

    def _validate_grid_integrity(self) -> None:
        """
        Validate the integrity of all loaded grids.
        """
        try:
            valid_grids = 0
            issues_found = 0

            for symbol, grid_data in list(self._grids.items()):
                issues = []

                # Validate required fields
                required_fields = [
                    "state",
                    "grid_capital",
                    "center_price",
                    "initial_center",
                ]
                for field in required_fields:
                    if field not in grid_data or grid_data[field] is None:
                        issues.append(f"missing_{field}")

                # Validate data types and ranges
                if "grid_capital" in grid_data and not isinstance(
                    grid_data["grid_capital"], (int, float)
                ):
                    issues.append("invalid_capital_type")

                if "center_price" in grid_data and not isinstance(
                    grid_data["center_price"], (int, float)
                ):
                    issues.append("invalid_center_price_type")

                if "grid_spacing" in grid_data:
                    spacing = grid_data["grid_spacing"]
                    if not isinstance(spacing, (int, float)) or spacing <= 0:
                        issues.append("invalid_spacing")

                if issues:
                    issues_found += 1
                    logger.warning(
                        f"⚠️ Grid integrity issues for {symbol}: {', '.join(issues)}"
                    )

                    # Try to fix minor issues
                    if "invalid_spacing" in issues:
                        grid_data["grid_spacing"] = 0.004  # Default spacing
                        # Save the repaired spacing to database
                        if self.db:
                            self.save_grid_state(symbol)

                    # Mark as repaired if fixed
                    if len(issues) == 1 and "invalid_spacing" in issues:
                        grid_data["integrity_repaired"] = datetime.now()
                else:
                    valid_grids += 1

            logger.info(
                f"✅ Grid integrity validation complete: {valid_grids} valid, {issues_found} with issues"
            )

        except Exception as e:
            logger.error(f"Error in grid integrity validation: {e}", exc_info=True)

    def _refresh_market_info_cache(self) -> None:
        """
        Refresh the market info cache from Pacifica API.

        Fetches market data from /info endpoint and caches tick_size and lot_size
        for each symbol to ensure order compliance.
        """
        try:
            markets = self.client.get_markets()
            if not markets:
                logger.warning(
                    "Failed to refresh market info cache: no markets returned"
                )
                return

            new_cache = {}
            for market in markets:
                symbol = market.get("symbol", "").upper().replace("-PERP", "")
                if not symbol:
                    continue

                # Extract tick_size and lot_size from market data
                tick_size = market.get("tick_size")
                lot_size = market.get("lot_size")

                # Convert to float if present
                if tick_size is not None:
                    try:
                        tick_size = float(tick_size)
                    except (TypeError, ValueError):
                        tick_size = None

                if lot_size is not None:
                    try:
                        lot_size = float(lot_size)
                    except (TypeError, ValueError):
                        lot_size = None

                if tick_size or lot_size:
                    new_cache[symbol] = {
                        "tick_size": tick_size,
                        "lot_size": lot_size,
                    }

            self._market_info_cache = new_cache
            self._market_info_cache_timestamp = datetime.now()

            logger.debug(
                f"GridLifecycleManager market info cache refreshed: {len(new_cache)} symbols"
            )

        except Exception as e:
            logger.error(
                f"Error refreshing market info cache in GridLifecycleManager: {e}"
            )

    def _get_cached_market_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get cached market info for a symbol.

        Args:
            symbol: Trading symbol (e.g., 'BTC', 'ETH')

        Returns:
            Dict with 'tick_size' and 'lot_size' keys (values may be None)
        """
        symbol = symbol.upper().replace("-PERP", "")

        # Check if cache needs refresh
        if (
            self._market_info_cache_timestamp is None
            or (datetime.now() - self._market_info_cache_timestamp).seconds
            > self._market_info_cache_ttl
        ):
            self._refresh_market_info_cache()

        return self._market_info_cache.get(
            symbol, {"tick_size": None, "lot_size": None}
        )

    def get_symbol_tick_size(self, symbol: str) -> Optional[float]:
        """
        Get tick size for a symbol from cached market data.

        Args:
            symbol: Trading symbol

        Returns:
            Tick size or None if not available
        """
        market_info = self._get_cached_market_info(symbol)
        return market_info.get("tick_size")

    def get_symbol_lot_size(self, symbol: str) -> Optional[float]:
        """
        Get lot size for a symbol from cached market data.

        Args:
            symbol: Trading symbol

        Returns:
            Lot size or None if not available
        """
        market_info = self._get_cached_market_info(symbol)
        return market_info.get("lot_size")

    def round_price_to_tick_size(self, price: float, symbol: str) -> float:
        """
        Round price to comply with tick size requirements.

        Args:
            price: Original price
            symbol: Trading symbol

        Returns:
            Price rounded to tick size
        """
        from decimal import Decimal, ROUND_HALF_UP

        tick_size = self.get_symbol_tick_size(symbol)

        if tick_size is None or tick_size <= 0:
            return price

        price_dec = Decimal(str(price))
        tick_size_dec = Decimal(str(tick_size))

        ticks = (price_dec / tick_size_dec).to_integral_value(rounding=ROUND_HALF_UP)
        rounded_price = float(ticks * tick_size_dec)

        return rounded_price

    def round_quantity_to_lot_size(self, quantity: float, symbol: str) -> float:
        """
        Round quantity to comply with lot size requirements.

        Args:
            quantity: Original quantity
            symbol: Trading symbol

        Returns:
            Quantity rounded to lot size
        """
        from decimal import Decimal, ROUND_DOWN

        lot_size = self.get_symbol_lot_size(symbol)

        if lot_size is None or lot_size <= 0:
            return quantity

        quantity_dec = Decimal(str(quantity))
        lot_size_dec = Decimal(str(lot_size))

        lot_units = int(
            (quantity_dec / lot_size_dec).to_integral_value(rounding=ROUND_DOWN)
        )

        if lot_units < 1 and quantity > 0:
            lot_units = 1

        rounded_quantity = float(Decimal(lot_units) * lot_size_dec)

        return rounded_quantity

    # =========================
    # STATE CHECKS
    # =========================

    def has_active_grid(self, symbol: str) -> bool:
        """
        Enhanced check for active grid with consistency validation.

        Args:
            symbol: Trading symbol (any format - will be normalized)

        Returns:
            True if symbol has an active grid in consistent state
        """
        try:
            # Normalize symbol format
            normalized_symbol = self.consistency_manager.normalize_ticker_symbol(symbol)

            # Check direct memory state first (fast path)
            if (
                normalized_symbol in self._grids
                and self._grids[normalized_symbol]["state"] == GridState.ACTIVE
            ):
                # Quick consistency check
                memory_state = self._grids[normalized_symbol]

                # Ensure required fields are present
                if not memory_state.get("center_price") and not memory_state.get(
                    "initial_center"
                ):
                    logger.warning(
                        f"⚠️ Active grid {symbol} missing center price - triggering consistency check"
                    )
                    # Trigger consistency validation in background
                    try:
                        import threading

                        threading.Thread(
                            target=self.consistency_manager.validate_and_repair_all_grids,
                            kwargs={"automatic": True},
                            daemon=True,
                        ).start()
                    except Exception:
                        pass  # Don't let consistency check break main logic

                return True

            # Check other symbol formats as fallback
            for grid_symbol in self._grids:
                if (
                    self.consistency_manager.normalize_ticker_symbol(grid_symbol)
                    == normalized_symbol
                ):
                    if self._grids[grid_symbol]["state"] == GridState.ACTIVE:
                        return True

            return False

        except Exception as e:
            logger.error(f"Error in has_active_grid for {symbol}: {e}")
            # Fallback to original logic on error
            return (
                symbol in self._grids
                and self._grids[symbol]["state"] == GridState.ACTIVE
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

    def evaluate_refresh_opportunity(
        self,
        symbol: str,
        signal_center: float,
        signal_confidence: float,
        atr_current: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Evaluate if a grid refresh opportunity exists based on the research criteria.

        Args:
            symbol: Trading symbol
            signal_center: Proposed new center price from signal
            signal_confidence: Confidence of the new signal
            atr_current: Current ATR value (optional)

        Returns:
            Dict with evaluation results and recommendation
        """
        result = {
            "should_refresh": False,
            "drift_atr": 0.0,
            "drift_pct": 0.0,
            "confidence_improvement": 0.0,
            "reasons": [],
            "conditions_met": [],
            "conditions_failed": [],
        }

        try:
            if not self.has_active_grid(symbol):
                result["reasons"].append("No active grid found")
                return result

            current_center = self.get_grid_center(symbol)
            if current_center is None:
                result["reasons"].append("Current grid has no center price")
                return result

            grid_data = self._grids[symbol]
            current_confidence = grid_data.get("signal_confidence", 0.65)

            # Calculate drift
            drift_abs = abs(signal_center - current_center)
            result["drift_pct"] = (
                drift_abs / current_center if current_center > 0 else 0
            )

            # Calculate ATR-based drift if ATR provided
            if atr_current and atr_current > 0:
                result["drift_atr"] = drift_abs / atr_current

                # Check drift condition
                if result["drift_atr"] >= self.GRID_REFRESH_MIN_ATR_DRIFT:
                    result["conditions_met"].append(
                        f"drift_{result['drift_atr']:.2f}x_atr"
                    )
                else:
                    result["conditions_failed"].append(
                        f"drift_{result['drift_atr']:.2f}x_atr"
                    )
            else:
                # Fallback to percentage-based drift (if ATR unavailable)
                min_drift_pct = 0.02  # 2% minimum drift
                if result["drift_pct"] >= min_drift_pct:
                    result["conditions_met"].append(f"drift_{result['drift_pct']:.1%}")
                else:
                    result["conditions_failed"].append(
                        f"drift_{result['drift_pct']:.1%}"
                    )

            # Check confidence condition
            if signal_confidence >= self.GRID_REFRESH_MIN_CONFIDENCE:
                result["conditions_met"].append(f"confidence_{signal_confidence:.2f}")
            else:
                result["conditions_failed"].append(
                    f"confidence_{signal_confidence:.2f}"
                )

            # Check confidence improvement
            result["confidence_improvement"] = signal_confidence - current_confidence
            if result["confidence_improvement"] >= self.GRID_REFRESH_MIN_CONF_IMPROVE:
                result["conditions_met"].append(
                    f"improvement_{result['confidence_improvement']:.2f}"
                )
            else:
                result["conditions_failed"].append(
                    f"improvement_{result['confidence_improvement']:.2f}"
                )

            # Check cooldown
            last_refresh = self.get_last_refresh_time(symbol)
            if last_refresh:
                minutes_since_refresh = (
                    datetime.now() - last_refresh
                ).total_seconds() / 60
                if minutes_since_refresh >= self.GRID_REFRESH_COOLDOWN_MINUTES:
                    result["conditions_met"].append(
                        f"cooldown_{minutes_since_refresh:.0f}min"
                    )
                else:
                    remaining = (
                        self.GRID_REFRESH_COOLDOWN_MINUTES - minutes_since_refresh
                    )
                    result["conditions_failed"].append(
                        f"cooldown_{remaining:.0f}min_remaining"
                    )
            else:
                result["conditions_met"].append("no_previous_refresh")

            # Check daily limit
            today_refreshes = self._daily_refresh_counts.get(symbol, 0)
            if today_refreshes < self.GRID_REFRESH_MAX_PER_DAY:
                result["conditions_met"].append(
                    f"daily_limit_{today_refreshes}/{self.GRID_REFRESH_MAX_PER_DAY}"
                )
            else:
                result["conditions_failed"].append(
                    f"daily_limit_exceeded_{today_refreshes}"
                )

            # Emergency drift check
            if (
                atr_current
                and result["drift_atr"] > self.GRID_EMERGENCY_DRIFT_THRESHOLD
            ):
                result["conditions_failed"].append(
                    f"emergency_drift_{result['drift_atr']:.2f}x_atr"
                )
                result["reasons"].append(
                    "Emergency drift detected - manual review required"
                )
                return result

            # Determine if refresh should proceed
            # All basic conditions must be met (except maybe one can be flexible)
            critical_conditions = ["drift", "confidence"]
            flexible_conditions = ["improvement", "cooldown", "daily_limit"]

            critical_met = any(
                "drift" in condition for condition in result["conditions_met"]
            ) and any(
                "confidence" in condition for condition in result["conditions_met"]
            )

            flexible_met = (
                len(
                    [
                        c
                        for c in flexible_conditions
                        if any(c in condition for condition in result["conditions_met"])
                    ]
                )
                >= 2
            )

            result["should_refresh"] = (
                critical_met
                and flexible_met
                and "emergency_drift" not in result["conditions_failed"]
            )

            if result["should_refresh"]:
                result["reasons"].append("Refresh opportunity detected")
            else:
                result["reasons"].append("Refresh conditions not met")

            return result

        except Exception as e:
            logger.error(
                f"Error evaluating refresh opportunity for {symbol}: {e}", exc_info=True
            )
            result["reasons"].append(f"Evaluation error: {e}")
            return result

    def get_grid_spacing(self, symbol: str) -> float:
        """Get the current grid spacing for a symbol."""
        if symbol not in self._grids:
            return 0.0
        return self._grids[symbol].get("grid_spacing", 0.0)

    def update_grid_spacing(
        self, symbol: str, new_spacing: float, reason: str = "dynamic"
    ):
        """Update the grid spacing (for dynamic spacing adjustments)."""
        if symbol not in self._grids:
            logger.warning(f"Cannot update spacing - no grid for {symbol}")
            return
        old_spacing = self._grids[symbol].get("grid_spacing", 0)
        self._grids[symbol]["grid_spacing"] = new_spacing
        self._grids[symbol]["spacing_updated_at"] = datetime.now()
        logger.info(
            f"📊 Grid {symbol} spacing updated: ${old_spacing:.4f} → ${new_spacing:.4f} ({reason})"
        )

    def recenter_grid(
        self,
        symbol: str,
        new_center: float,
        reason: str = "signal_refresh",
        signal_confidence: float = 0.0,
    ) -> bool:
        """
        Soft recenter: shift UNFILLED limit orders toward new center price.
        Does NOT touch filled positions.
        Includes enhanced safety mechanisms and refresh opportunity logic.

        Args:
            symbol: Trading symbol
            new_center: New center price to shift orders toward
            reason: Reason for recentering (for logging)
            signal_confidence: Confidence of the triggering signal (for refresh logic)

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

        # Enhanced safety checks
        # 1. Emergency drift check
        drift_abs = abs(new_center - current_center)
        drift_pct = drift_abs / current_center if current_center > 0 else 0

        # Get ATR for emergency check (if possible)
        try:
            atr_current = self._calculate_atr_for_symbol(symbol)
            drift_atr = (
                drift_abs / atr_current if atr_current and atr_current > 0 else 0
            )
        except (ValueError, TypeError, AttributeError) as e:
            logger.debug(f"Could not calculate ATR for {symbol}: {e}")
            drift_atr = 0

        if drift_atr > self.GRID_EMERGENCY_DRIFT_THRESHOLD:
            logger.error(
                f"🚨 EMERGENCY DRIFT for {symbol}: {drift_atr:.2f}x ATR > {self.GRID_EMERGENCY_DRIFT_THRESHOLD}x - "
                f"aborting recenter, consider emergency stop"
            )
            # Trigger emergency evaluation
            self._evaluate_emergency_conditions(symbol, new_center, drift_atr)
            return False

        # 2. Daily refresh limit check
        today_refreshes = self._daily_refresh_counts.get(symbol, 0)
        if today_refreshes >= self.GRID_REFRESH_MAX_PER_DAY:
            logger.warning(
                f"🚫 Daily refresh limit reached for {symbol}: {today_refreshes}/{self.GRID_REFRESH_MAX_PER_DAY} - "
                f"skipping recenter"
            )
            return False

        # 3. Cooldown check (45 minutes)
        last_refresh = self.get_last_refresh_time(symbol)
        if last_refresh and (datetime.now() - last_refresh).total_seconds() < (
            self.GRID_REFRESH_COOLDOWN_MINUTES * 60
        ):
            remaining = (self.GRID_REFRESH_COOLDOWN_MINUTES * 60) - (
                datetime.now() - last_refresh
            ).total_seconds()
            logger.info(
                f"🔄 Refresh skipped for {symbol} - cooldown {remaining / 60:.1f}min remaining"
            )
            return False

            logger.info(
                f"🔍 Recentering {symbol} | old center ${current_center:.4f} → new ${new_center:.4f} "
                f"(drift ${drift_abs:.4f} / {drift_pct:.2%}, {drift_atr:.2f}x ATR) - reason: {reason}"
            )

            try:
                # Cancel ALL existing orders for this symbol first (more reliable than individual cancels)
                logger.info(f"🗑️ Cancelling all orders for {symbol} before recentering...")
                cancel_result = self.client.cancel_all_orders(symbol)
                logger.info(f"✅ Cancel result for {symbol}: {cancel_result}")

                # Small delay to ensure cancels are processed
                import time
                time.sleep(0.5)

                # Now place the new grid orders at the new center
                # Calculate grid levels around new center
                atr_value = atr_current if atr_current and atr_current > 0 else (new_center * 0.02)  # 2% fallback
                spacing = atr_value * 0.5  # 0.5x ATR spacing
                
                # Calculate number of levels
                num_levels = grid.get("num_levels", 10)
                
                # Get current price to determine buy/sell levels
                current_price = new_center
                
                # Calculate prices for both buy and sell sides
                buy_levels = []
                sell_levels = []
                
                # Generate grid levels: 5 buys below, 5 sells above center
                for i in range(1, num_levels + 1):
                    buy_price = new_center - (spacing * i)
                    sell_price = new_center + (spacing * i)
                    
                    # Apply tick size
                    tick_size = self.get_symbol_tick_size(symbol)
                    if tick_size:
                        buy_price = self.round_price_to_tick_size(buy_price, symbol)
                        sell_price = self.round_price_to_tick_size(sell_price, symbol)
                    
                    buy_levels.append(buy_price)
                    sell_levels.append(sell_price)
                
                # Calculate quantity per order based on capital
                grid_capital = grid.get("grid_capital", 1000)
                total_orders = len(buy_levels) + len(sell_levels)
                quantity_per_order = (grid_capital / new_center) / total_orders if new_center > 0 else 0
                quantity_per_order = round(quantity_per_order, 2)
                
                orders_placed = 0
                
                # Place buy orders
                for buy_price in buy_levels:
                    if buy_price > 0:
                        try:
                            new_order = self.client.place_order(
                                symbol, "buy", quantity_per_order, "limit", buy_price,
                                client_order_id=str(uuid.uuid4())
                            )
                            validated = ResponseHandler.validate_order_response(new_order)
                            if validated.get("success"):
                                orders_placed += 1
                                logger.debug(f"✅ Placed BUY order @ ${buy_price:.4f}")
                        except Exception as e:
                            logger.error(f"❌ Failed to place BUY order @ ${buy_price:.4f}: {e}")
                
                # Place sell orders
                for sell_price in sell_levels:
                    if sell_price > 0:
                        try:
                            new_order = self.client.place_order(
                                symbol, "sell", quantity_per_order, "limit", sell_price,
                                client_order_id=str(uuid.uuid4())
                            )
                            validated = ResponseHandler.validate_order_response(new_order)
                            if validated.get("success"):
                                orders_placed += 1
                                logger.debug(f"✅ Placed SELL order @ ${sell_price:.4f}")
                        except Exception as e:
                            logger.error(f"❌ Failed to place SELL order @ ${sell_price:.4f}: {e}")
                
                logger.info(f"✅ Grid recenter completed for {symbol}: {orders_placed} new orders placed")
                
                # Update grid metadata
                self._update_grid_after_refresh(
                    symbol, new_center, reason, signal_confidence, orders_placed
                )
                
                return True

            except Exception as e:
                logger.critical(f"❌ Grid recenter FAILED for {symbol}: {e}", exc_info=True)
                return False

    def _calculate_atr_for_symbol(
        self, symbol: str, timeframe: str = "5m", period: int = 14
    ) -> Optional[float]:
        """
        Calculate ATR for a symbol using cached market data.

        Args:
            symbol: Trading symbol
            timeframe: Candle timeframe
            period: ATR period

        Returns:
            ATR value or None if calculation fails
        """
        try:
            # This is a simplified ATR calculation
            # In a full implementation, this would fetch candle data
            # For now, we'll return None to disable ATR-based checks
            return None
        except Exception as e:
            logger.error(f"Error calculating ATR for {symbol}: {e}")
            return None

    def _evaluate_emergency_conditions(
        self, symbol: str, new_center: float, drift_atr: float
    ) -> None:
        """
        Evaluate emergency conditions when drift exceeds threshold.

        Args:
            symbol: Trading symbol
            new_center: Proposed new center price
            drift_atr: Drift in ATR multiples
        """
        try:
            logger.critical(
                f"🚨 EMERGENCY EVALUATION for {symbol}: drift={drift_atr:.2f}x ATR"
            )

            # Get current price
            try:
                ticker = self.client.get_ticker(symbol)
                current_price = float(ticker.get("last", 0)) if ticker else 0
            except (ValueError, TypeError) as e:
                logger.warning(f"Could not get ticker for {symbol}: {e}")
                current_price = 0

            if current_price == 0:
                logger.error(
                    f"Cannot get current price for {symbol} - emergency evaluation incomplete"
                )
                return

            # Check if we're in a rapid market movement
            grid = self._grids.get(symbol)
            if grid:
                emergency_stop = grid.get("emergency_stop", 0)
                if emergency_stop > 0 and current_price <= emergency_stop:
                    logger.critical(
                        f"🚨 Emergency stop breached for {symbol}: {current_price} <= {emergency_stop}"
                    )
                    self.on_emergency_stop_triggered(symbol)
                else:
                    # Consider temporary grid suspension
                    logger.warning(
                        f"⚠️ High drift detected for {symbol} - consider manual review"
                    )

        except Exception as e:
            logger.error(f"Error in emergency evaluation for {symbol}: {e}")

    def _update_grid_after_refresh(
        self,
        symbol: str,
        new_center: float,
        reason: str,
        signal_confidence: float,
        orders_adjusted: int,
    ) -> None:
        """
        Update grid metadata after a successful refresh.

        Args:
            symbol: Trading symbol
            new_center: New center price
            reason: Refresh reason
            signal_confidence: Signal confidence
            orders_adjusted: Number of orders adjusted
        """
        try:
            grid = self._grids[symbol]

            # Update core metadata
            grid["center_price"] = new_center
            grid["last_refresh"] = datetime.now()
            grid["last_refresh_reason"] = reason
            grid["refresh_count"] = grid.get("refresh_count", 0) + 1
            grid["last_refresh_orders_adjusted"] = orders_adjusted

            # Update signal confidence if provided
            if signal_confidence > 0:
                grid["signal_confidence"] = signal_confidence
                grid["confidence_updated_at"] = datetime.now()

            # Update daily refresh counter
            self._daily_refresh_counts[symbol] = (
                self._daily_refresh_counts.get(symbol, 0) + 1
            )

            # Add refresh metadata
            grid["refresh_history"] = grid.get("refresh_history", [])
            grid["refresh_history"].append(
                {
                    "timestamp": datetime.now(),
                    "reason": reason,
                    "old_center": grid.get("center_price"),
                    "new_center": new_center,
                    "orders_adjusted": orders_adjusted,
                    "signal_confidence": signal_confidence,
                }
            )

            # Keep only last 10 refreshes in history
            if len(grid["refresh_history"]) > 10:
                grid["refresh_history"] = grid["refresh_history"][-10:]

            # Persist to database
            if self.db:
                self.save_grid_state(symbol)

            logger.info(f"✅ Grid metadata updated for {symbol} after refresh")

        except Exception as e:
            logger.error(
                f"Error updating grid metadata after refresh for {symbol}: {e}"
            )

    def _attempt_order_restoration(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        original_order_id: str,
    ) -> None:
        """
        Attempt to restore an original order if replacement failed.

        Args:
            symbol: Trading symbol
            side: Order side
            quantity: Order quantity
            price: Original price
            original_order_id: Original order ID
        """
        try:
            logger.warning(
                f"🔄 Attempting to restore order {original_order_id} for {symbol}"
            )

            restored_order = self.client.place_order(
                symbol, side, quantity, "limit", price,
                client_order_id=str(uuid.uuid4())
            )
            validated_response = ResponseHandler.validate_order_response(restored_order)

            if validated_response.get("success"):
                logger.info(
                    f"✅ Successfully restored order {original_order_id} for {symbol}"
                )
            else:
                logger.error(
                    f"❌ Failed to restore order {original_order_id}: {validated_response.get('error')}"
                )

        except Exception as e:
            logger.error(f"Error restoring order {original_order_id}: {e}")

    def _evaluate_dynamic_spacing_adjustment(self, symbol: str) -> None:
        """
        Evaluate if dynamic spacing adjustment is needed.

        Args:
            symbol: Trading symbol
        """
        try:
            if not self.GRID_DYNAMIC_SPACING_ENABLED:
                return

            grid = self._grids.get(symbol)
            if not grid:
                return

            # Check if enough time has passed since last spacing adjustment
            last_spacing_update = grid.get("spacing_updated_at")
            if last_spacing_update:
                minutes_since_update = (
                    datetime.now() - last_spacing_update
                ).total_seconds() / 60
                if minutes_since_update < self.GRID_DYNAMIC_SPACING_RECALC_MINUTES:
                    return

            # Calculate new spacing based on current volatility
            new_spacing = self._calculate_dynamic_spacing(symbol)
            if new_spacing and new_spacing != grid.get("grid_spacing"):
                self.update_grid_spacing(
                    symbol, new_spacing, "dynamic_volatility_adjustment"
                )

        except Exception as e:
            logger.error(f"Error evaluating dynamic spacing for {symbol}: {e}")

    def _calculate_dynamic_spacing(self, symbol: str) -> Optional[float]:
        """
        Calculate dynamic grid spacing based on market conditions.

        Args:
            symbol: Trading symbol

        Returns:
            New spacing percentage or None if calculation fails
        """
        try:
            # Get current ATR
            atr = self._calculate_atr_for_symbol(symbol)
            if not atr:
                return None

            # Get current price
            try:
                ticker = self.client.get_ticker(symbol)
                current_price = float(ticker.get("last", 0)) if ticker else 0
            except (ValueError, TypeError) as e:
                logger.warning(f"Could not get ticker for {symbol}: {e}")
                current_price = 0

            if current_price == 0:
                return None

            # Calculate base spacing as percentage of ATR
            atr_pct = atr / current_price

            # Apply volatility multiplier
            # Higher volatility = wider spacing
            grid = self._grids.get(symbol)
            if grid:
                # Check recent fills to determine if market is volatile
                fills = self._fills.get(symbol, [])
                recent_fills = [
                    f
                    for f in fills
                    if (datetime.now() - f.timestamp).total_seconds() < 3600
                ]  # Last hour

                if len(recent_fills) > 4:  # High activity indicates high volatility
                    multiplier = self.GRID_SPACING_VOLATILITY_MULTIPLIER
                else:
                    multiplier = 1.0

                # Calculate base spacing (configurable)
                base_spacing_pct = 0.4  # 0.4% base spacing
                new_spacing_pct = base_spacing_pct * multiplier

                # Apply reasonable bounds
                new_spacing_pct = max(0.002, min(0.01, new_spacing_pct))  # 0.2% to 1.0%

                logger.debug(
                    f"📊 Dynamic spacing for {symbol}: ATR={atr:.4f} ({atr_pct:.3%}), "
                    f"multiplier={multiplier:.2f}, spacing={new_spacing_pct:.3%}"
                )

                return new_spacing_pct

            return None

        except Exception as e:
            logger.error(f"Error calculating dynamic spacing for {symbol}: {e}")
            return None

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
        grid_data["closed_at"] = datetime.now()
        grid_data["close_reason"] = reason

        logger.info(
            f"📊 Grid closed for {symbol}: {old_state.value} -> CLOSED (reason: {reason})"
        )

        # Optionally persist to database
        if self.db:
            try:
                self.db.update_grid_state(symbol, GridState.CLOSED.value, reason)
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
        regime: Optional[str] = None,
        atr: float = 0,
        spacing: float = 0,
        num_levels: int = 10,
        center_price: float = 0,
    ):
        """
        Register a grid AFTER emergency stop has been successfully placed.
        
        If grid already exists, updates the existing grid instead of failing.

        Args:
            symbol: Trading symbol
            grid_capital: Capital allocated to the grid
            emergency_stop_price: Price at which to emergency exit
            regime: Current market regime (for persistence)
            atr: ATR at creation time
            spacing: Grid spacing used
            num_levels: Number of grid levels
            center_price: Initial center price for the grid
        """
        # Check if grid already exists - update instead of fail
        if symbol in self._grids:
            logger.info(f"Grid already exists for {symbol} - updating grid data")
            # Update existing grid instead of failing
            self._grids[symbol].update({
                "grid_capital": grid_capital,
                "emergency_stop": emergency_stop_price,
                "regime_on_creation": regime,
                "atr_at_creation": atr,
                "grid_spacing": spacing,
                "num_levels": num_levels,
                "center_price": center_price,
                "initial_center": center_price,
                "updated_at": datetime.now(),
            })
            # Save to database
            self.save_grid_state(symbol, regime=regime, atr=atr, spacing=spacing)
            logger.info(f"✅ Grid updated for {symbol}")
            return

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
            "created_at": datetime.now(),
            "refresh_count": 0,
        }

        # Initialize exposure tracking at zero
        self.risk_manager.grid_exposure[symbol] = 0.0

        # Persist to database for restart resilience
        self.save_grid_state(
            symbol, regime=regime or "unknown", atr=atr, spacing=spacing
        )

        logger.info(
            f"Grid registered for {symbol}: capital=${grid_capital:.2f}, center=${center_price:.4f}, regime={regime}"
        )

    # =========================
    # REGIME HANDLING
    # =========================

    def on_regime_disallowed(
        self, symbol: str, market_data: Optional[Dict[str, List]] = None
    ):
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

    def _force_exit(self, symbol: str, reason: str):
        """
        HARD EXIT:
        - Cancel all orders
        - Close all positions
        - Clear RiskManager exposure
        - Remove grid state
        """
        try:
            # Cancel all open orders
            self.client.cancel_all_orders(symbol)
            logger.info(f"All orders cancelled for {symbol} ({reason})")
        except Exception as e:
            logger.error(f"Order cancel failed for {symbol}: {e}")

        try:
            # Close all open positions (market)
            positions = self.client.get_positions()
            for pos in positions:
                if pos.get("symbol") != symbol:
                    continue

                qty = abs(float(pos.get("amount", 0)))
                if qty <= 0:
                    continue

                side = pos.get("side")
                close_side = "sell" if side == "bid" else "buy"

                # Validate response using ResponseHandler
                flatten_response = self.client.place_order(
                    symbol, close_side, qty, "market"
                )
                validated_response = ResponseHandler.validate_order_response(
                    flatten_response
                )

                if not validated_response.get("success"):
                    error_msg = validated_response.get("error", "Unknown API error")
                    logger.error(
                        f"❌ Position flatten order failed for {symbol}: {error_msg}"
                    )
                else:
                    logger.critical(f"Position flattened: {symbol} {close_side} {qty}")

        except Exception as e:
            logger.critical(f"POSITION FLATTEN FAILED for {symbol}: {e}")

        # Reset risk manager exposure
        self.risk_manager.on_grid_emergency_exit(symbol)

        # Remove grid from memory
        if symbol in self._grids:
            del self._grids[symbol]

        # Remove from database persistence
        self.delete_grid_state(symbol)

        logger.critical(f"Grid fully exited and cleared for {symbol} ({reason})")

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
                # CLOSE against-trend position
                try:
                    close_side = "buy" if position_side == "short" else "sell"
                    close_response = self.client.place_order(
                        symbol, close_side, qty, "market"
                    )

                    # Validate response using ResponseHandler
                    validated_response = ResponseHandler.validate_order_response(
                        close_response
                    )

                    if validated_response.get("success"):
                        result["closed_positions"].append(
                            {
                                "side": position_side,
                                "qty": qty,
                                "reason": "against_trend",
                            }
                        )
                    else:
                        error_msg = validated_response.get("error", "Unknown API error")
                        logger.error(
                            f"❌ Against-trend position close failed for {symbol}: {error_msg}"
                        )

                    logger.warning(
                        f"📊 Against-trend position CLOSED: {symbol} {position_side} {qty} "
                        f"(trend={trend_direction})"
                    )

                    # Update database record
                    self._update_position_status(
                        symbol, position_side, qty, "closed", "against_trend"
                    )

                except Exception as e:
                    logger.error(f"Failed to close position for {symbol}: {e}")
                    result["errors"].append(f"Close position failed: {e}")
                    return result  # Fail if any close fails
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
                # Keep grid ACTIVE but mark it as having migrated positions
                # This allows the grid manager to continue monitoring it for updates
                self._grids[symbol]["state"] = GridState.ACTIVE
                self._grids[symbol]["has_migrated_positions"] = True
                self._grids[symbol]["migrated_positions"] = result["kept_positions"]
                self._grids[symbol]["trend_direction"] = trend_direction
                self._grids[symbol]["migration_time"] = datetime.now().isoformat()

                logger.info(
                    f"📊 Grid migrated to trend-following for {symbol}: "
                    f"closed={len(result['closed_positions'])}, kept={len(result['kept_positions'])}, "
                    f"grid remains ACTIVE for monitoring"
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

    def monitor_grids(
        self, current_prices: Optional[Dict[str, float]] = None
    ) -> Dict[str, Any]:
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
            "alerts": [],
        }

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

            self._last_fill_check = datetime.now()

        except Exception as e:
            logger.error(f"Grid monitoring error: {e}")
            results["alerts"].append(f"Monitoring error: {e}")

        return results

    def _process_trades(self, symbol: str, trades: List[Dict]) -> int:
        """
        Process trades from exchange and record new fills.

        Returns:
            Number of new fills processed
        """
        if symbol not in self._processed_trades:
            self._processed_trades[symbol] = set()

        if symbol not in self._fills:
            self._fills[symbol] = []

        if symbol not in self._metrics:
            self._metrics[symbol] = GridMetrics()

        new_fills = 0

        for trade in trades:
            trade_id = str(
                trade.get("trade_id") or trade.get("history_id") or trade.get("id", "")
            )

            # Skip if already processed
            if trade_id in self._processed_trades[symbol]:
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
                order_id = str(trade.get("order_id", ""))

                # Parse timestamp
                ts = trade.get("timestamp") or trade.get("created_at")
                if isinstance(ts, (int, float)):
                    timestamp = datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts)
                elif isinstance(ts, str):
                    timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                else:
                    timestamp = datetime.now()

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

    def _replenish_order(
        self, symbol: str, filled_side: str, fill_price: float, fill_quantity: float
    ):
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

            # Round price to tick size from Pacifica API
            tick_size = self.get_symbol_tick_size(symbol)
            if tick_size is not None and tick_size > 0:
                counter_price = self.round_price_to_tick_size(counter_price, symbol)
            else:
                # Fallback: use conservative defaults
                logger.warning(f"⚠️ No tick_size from API for {symbol}, using fallback")
                fallback_tick = 1.0 if "BTC" in symbol else 0.01
                counter_price = round(counter_price / fallback_tick) * fallback_tick

            # Round quantity to lot size from Pacifica API
            lot_size = self.get_symbol_lot_size(symbol)
            if lot_size is not None and lot_size > 0:
                counter_quantity = self.round_quantity_to_lot_size(
                    fill_quantity, symbol
                )
            else:
                # Fallback: use conservative defaults
                logger.warning(f"⚠️ No lot_size from API for {symbol}, using fallback")
                fallback_lot = 0.00001 if "BTC" in symbol else 0.0001
                counter_quantity = round(
                    int(fill_quantity / fallback_lot) * fallback_lot, 5
                )

            if lot_size is not None and counter_quantity < lot_size:
                logger.warning(
                    f"Counter quantity too small for {symbol}: {counter_quantity} < {lot_size}"
                )
                return

            # Place the counter order with grid prefix
            order_result = self.client.place_order(
                symbol, counter_side, counter_quantity, "limit", counter_price,
                client_order_id=str(uuid.uuid4())
            )

            # Validate response using ResponseHandler
            validated_response = ResponseHandler.validate_order_response(order_result)

            if validated_response.get("success"):
                logger.info(
                    f"📊 Grid REPLENISH: {symbol} {counter_side.upper()} {counter_quantity:.6f} @ ${counter_price:.2f} "
                    f"(triggered by {filled_side} fill @ ${fill_price:.2f})"
                )
            else:
                error_msg = validated_response.get("error", "Unknown API error")
                logger.error(
                    f"❌ Grid replenish order failed for {symbol}: {error_msg}"
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
        state = grid["state"]
        has_migrated = grid.get("has_migrated_positions", False)

        # Determine display state for user interface
        # Show "migrated" if grid has migrated positions (regardless of actual state)
        if has_migrated:
            display_state = "migrated"
        else:
            display_state = state.value

        return {
            "symbol": symbol,
            "state": state.value,
            "display_state": display_state,
            "has_migrated_positions": has_migrated,
            "migrated_positions": grid.get("migrated_positions", []),
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
        """Get status for all active grids, including migrated grids."""
        active_grids = []
        for symbol in self._grids:
            grid = self._grids[symbol]
            state = grid.get("state")
            has_migrated = grid.get("has_migrated_positions", False)

            # Include grids that are either:
            # 1. ACTIVE state (includes migrated grids that remain active), OR
            # 2. DISABLED_BY_REGIME state with has_migrated_positions=True (backward compatibility)
            if state == GridState.ACTIVE or (
                state == GridState.DISABLED_BY_REGIME and has_migrated
            ):
                status = self.get_grid_status(symbol)
                if status:
                    active_grids.append(status)
        return active_grids

    def get_grid_consistency_status(self) -> Dict[str, Any]:
        """
        Get comprehensive grid state consistency status.

        Returns:
            Dictionary with consistency statistics and recommendations
        """
        return self.consistency_manager.get_consistency_statistics()

    def force_grid_state_repair(
        self, symbols: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Force repair of grid state consistency for specific symbols or all.

        Args:
            symbols: List of symbols to repair, or None for all symbols

        Returns:
            Repair operation summary
        """
        try:
            return asyncio.run(self.consistency_manager.force_full_repair(symbols))
        except RuntimeError as e:
            # Handle case where event loop is already running
            if "asyncio.run() cannot be called from a running event loop" in str(e):
                logger.warning("Cannot run async repair from current context, skipping")
                return {
                    "error": "Async repair not available in current context",
                    "symbols": symbols,
                }
            raise

    def emergency_grid_cleanup(self) -> Dict[str, Any]:
        """
        Perform emergency cleanup of all grid states.

        ⚠️ WARNING: This is a drastic measure that will reset all grid states!
        Only use in emergency situations where grid states are completely corrupted.

        Returns:
            Cleanup operation summary
        """
        try:
            return asyncio.run(self.consistency_manager.emergency_grid_cleanup())
        except RuntimeError as e:
            # Handle case where event loop is already running
            if "asyncio.run() cannot be called from a running event loop" in str(e):
                logger.warning(
                    "Cannot run async cleanup from current context, skipping"
                )
                return {"error": "Async cleanup not available in current context"}
            raise

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

        # Get consistency statistics
        consistency_stats = self.consistency_manager.get_consistency_statistics()

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
            "consistency_statistics": consistency_stats,
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
        self,
        symbol: str,
        regime: Optional[str] = None,
        atr: float = 0,
        spacing: float = 0,
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

        try:
            from .database import get_db_connection

            # Get center_price from grid data
            center_price = grid.get("center_price", 0)
            initial_center = grid.get("initial_center", 0)

            with get_db_connection() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO grid_states
                    (symbol, state, regime_on_creation, grid_capital, emergency_stop,
                     atr_at_creation, grid_spacing, num_levels, total_buy_fills,
                     total_sell_fills, realized_pnl, total_fees, center_price,
                     initial_center, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                    (
                        symbol,
                        grid["state"].value
                        if isinstance(grid["state"], GridState)
                        else grid["state"],
                        regime or "unknown",
                        grid.get("grid_capital", 0),
                        grid.get("emergency_stop", 0),
                        atr,
                        spacing,
                        grid.get("num_levels", 10),
                        metrics.total_buy_fills,
                        metrics.total_sell_fills,
                        metrics.realized_pnl,
                        metrics.total_fees,
                        center_price,
                        initial_center,
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
                           total_sell_fills, realized_pnl, total_fees, center_price,
                           initial_center
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

                            # Grid trading only valid in RANGING regimes
                            allowed_regimes = ["ranging_volatile", "ranging_calm"]
                            if current_regime_str not in allowed_regimes:
                                logger.warning(
                                    f"⚠️ Grid {symbol} created in {regime_on_creation}, "
                                    f"but current regime is {current_regime_str} - CLOSING"
                                )
                                self.delete_grid_state(symbol)
                                # Trigger close on exchange
                                self._force_exit(
                                    symbol,
                                    reason=f"REGIME_MISMATCH:{current_regime_str}",
                                )
                                continue

                    # Load into memory
                    self._grids[symbol] = {
                        "state": GridState.ACTIVE,
                        "grid_capital": row[3],
                        "emergency_stop": row[4],
                        "atr_at_creation": row[5],
                        "grid_spacing": row[6],
                        "num_levels": row[7],
                        "loaded_from_db": True,
                        "regime_on_creation": regime_on_creation,
                        "center_price": row[12] if row[12] else 0,
                        "initial_center": row[13] if row[13] else 0,
                    }

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
                    logger.info(
                        f"📊 Loaded grid from DB: {symbol} (regime: {regime_on_creation})"
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

        try:
            from .database import get_db_connection

            with get_db_connection() as conn:
                conn.execute(
                    """
                    UPDATE grid_states
                    SET total_buy_fills = ?, total_sell_fills = ?,
                        realized_pnl = ?, total_fees = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE symbol = ?
                """,
                    (
                        metrics.total_buy_fills,
                        metrics.total_sell_fills,
                        metrics.realized_pnl,
                        metrics.total_fees,
                        symbol,
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to update grid metrics for {symbol}: {e}")
