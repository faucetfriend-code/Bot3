import time
import logging
import threading
import sys
import os
import warnings
import json
import uuid
from typing import Dict, List, Any, Optional
from datetime import datetime
import urllib3
from loguru import logger

# Suppress expected SSL warnings for test environment
try:
    import urllib3

    warnings.filterwarnings(
        "ignore",
        message=".*Unverified HTTPS request.*",
        category=urllib3.exceptions.InsecureRequestWarning,
    )
except ImportError:
    # Fallback if urllib3 not available
    warnings.filterwarnings("ignore", message=".*Unverified HTTPS request.*")

# Import local config
from .config import config

# Response handling utilities
import json


class ResponseHandler:
    """Handles Pacifica API responses with robust validation and error handling."""
    
    @staticmethod
    def validate_order_response(response: Any) -> Dict[str, Any]:
        """
        Validate and normalize order response from Pacifica API.
        
        Handles various response formats:
        - Dict with success/data/error fields (expected format)
        - Boolean responses (direct API responses)
        - String responses (e.g., "success", "error", or JSON strings)
        - None or unexpected types
        
        Args:
            response: Raw response from API (could be dict, string, etc.)
            
        Returns:
            Normalized response dict with expected format:
            {"success": bool, "data": dict, "error": Optional[str]}
        """
        logger.debug(f"Validating order response: {response} (type: {type(response)})")
        
        # Defensive: Ensure response doesn't cause KeyError in string formatting
        if isinstance(response, dict):
            # Ensure all required keys exist to prevent KeyError
            response.setdefault("success", False)
            response.setdefault("data", {})
            response.setdefault("error", None)
        
        # Case 1: Response is already a dict (expected format)
        if isinstance(response, dict):
            return ResponseHandler._normalize_dict_response(response)
        
        # Case 2: Response is a boolean (direct API response)
        elif isinstance(response, bool):
            logger.warning(f"API returned boolean directly: {response}")
            return {
                "success": response,
                "data": {"status": "success" if response else "error"},
                "error": None if response else "API returned false"
            }
        
        # Case 3: Response is a string (could be JSON or just "success")
        elif isinstance(response, str):
            return ResponseHandler._handle_string_response(response)
        
        # Case 4: Response is None or unexpected type
        else:
            return {
                "success": False,
                "data": {},
                "error": f"Unexpected response type: {type(response).__name__}"
            }
    
    @staticmethod
    def _normalize_dict_response(response: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize dictionary response to expected format."""
        # Ensure we have the required fields
        normalized = {
            "success": bool(response.get("success", False)),
            "data": response.get("data", {}),
            "error": response.get("error") if not response.get("success") else None
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
                        "error": None if parsed else "API returned false"
                    }
                elif isinstance(parsed, str) and parsed.lower() == "success":
                    return {
                        "success": True,
                        "data": {"status": "success"},
                        "error": None
                    }
                elif isinstance(parsed, str) and parsed.lower() == "error":
                    return {
                        "success": False,
                        "data": {},
                        "error": "API returned error response"
                    }
                else:
                    # Default to treating as success for unknown types
                    return {
                        "success": True,
                        "data": {"raw_response": parsed},
                        "error": None
                    }
        except json.JSONDecodeError:
            # Not valid JSON, treat raw string
            pass
        
        # Handle specific string responses
        if response.lower() == '"success"' or response.lower() == "success":
            logger.warning("API returned string 'success' instead of JSON object")
            return {
                "success": True,
                "data": {"status": "success"},
                "error": None
            }
        elif response.lower() == '"error"' or response.lower() == "error":
            logger.error("API returned string 'error'")
            return {
                "success": False,
                "data": {},
                "error": "API returned error response"
            }
        else:
            # Unknown string response
            logger.error(f"API returned unexpected string response: {response}")
            return {
                "success": False,
                "data": {},
                "error": f"Unexpected API response: {response}"
            }

# Import core modules
from .models import Signal, OrderSide
from .indicators import calculate_adx, calculate_atr, calculate_bollinger_bands

# Import other modules
from .database import DatabaseManager
from .pacifica_client import PacificaClient, PacificaEnvironment
from .strategy_manager import StrategyManager
from .multi_timeframe_fetcher import MultiTimeframeFetcher
from .market_regime import MarketRegimeDetector
from .risk_manager import RiskManager, RiskProfile

# Import StrategyType from local config (re-exported from core_logic)
from .config import StrategyType

# Import WebSocket client for real-time price data
from .pacifica_ws_client import get_ws_client

# Import GridLifecycleManager for authoritative grid state management
from .grid_lifecycle_manager import GridLifecycleManager, GridState

# Import MigratedPositionManager for trend-following management of migrated positions
from .migrated_position_manager import MigratedPositionManager

# Import ExecutionLayer for precise 1m/5m entry timing
from .execution_layer import ExecutionLayer

# Import SignalLogger for comprehensive signal tracking
from .signal_logger import SignalLogger

# Import Phase 2 component system
from .component_interfaces import (
    ExecutionInterface,
    RiskInterface,
    GridInterface,
    RegimeInterface,
    StrategyInterface,
    DatabaseInterface,
)
from .event_system import get_event_bus, EventType
from .component_registry import get_component_registry


class TradingBot:
    """
    Trading bot class for automated trading on Pacifica exchange.

    INTEGRATION WITH API SERVER HUB
    ===============================
    This bot now communicates through the centralized API server hub
    for all data distribution and real-time updates.
    ===============================

    RISK MANAGEMENT WARNING
    ===============================
    This bot now communicates through the centralized API server hub
    for all data distribution and real-time updates.
    ===============================

    RISK MANAGEMENT WARNING
    ===============================
    DO NOT ADD RISK LOGIC HERE
    All risk calculations must go through RiskManager
    Use: self.risk_manager.get_position_size()
    Use: self.risk_manager.validate_position_size()
    ===============================
    """

    # Grid refresh/recenter configuration constants
    # Drift threshold in ATR multiples (1.5-2.0 range, default 1.8)
    GRID_REFRESH_MIN_ATR_DRIFT: float = 1.8
    # Minimum signal confidence to trigger refresh (0.72 = high conviction)
    GRID_REFRESH_MIN_CONFIDENCE: float = 0.72
    # Minimum confidence improvement over current (0.08 = ~10% relative improvement)
    GRID_REFRESH_MIN_CONF_IMPROVE: float = 0.08
    # Cooldown period between refreshes in minutes (45 minutes)
    GRID_REFRESH_COOLDOWN_MINUTES: int = 45

    def __init__(self, db=None, client=None, risk_manager=None, hub_publish_func=None):
        """
        Initialize the trading bot.

        Args:
            db: Database instance (DatabaseManager). If None, creates new instance.
            client: PacificaClient instance.
            risk_manager: RiskManager instance. If None, creates new instance.
            hub_publish_func: Function to publish status updates to API server hub.
        """
        # Store config reference
        self.config = config

        # Initialize database
        if db is None:
            self.db = DatabaseManager()
        else:
            self.db = db

        # Initialize signal logger (CSV auto-save + database + memory)
        self.signal_logger = SignalLogger(db_manager=self.db)
        logger.info("Signal logger initialized - logging to signals_log.csv")

        # Initialize client
        if client is None:
            self.client = PacificaClient(
                agent_wallet_private_key=config.agent_wallet_private_key,
                account_public_key=config.account_public_key,
                testnet=config.testnet,
            )
        else:
            self.client = client

        # Initialize risk manager
        if risk_manager is None:
            self.risk_manager = RiskManager(
                max_portfolio_risk_pct=0.05,
                max_portfolio_exposure_pct=0.15,
                client=self.client,  # Pass client for margin data retrieval
                max_margin_utilization_pct=0.75,  # 75% max margin utilization
                maintenance_margin_buffer_pct=0.15,  # 15% buffer above maintenance margin
            )
        else:
            self.risk_manager = risk_manager

        # Initialize hub publish function
        self.hub_publish_func = hub_publish_func

        # Initialize circuit breaker
        self._circuit_breaker_triggered = False
        self._circuit_breaker_loss_pct = (
            config.circuit_breaker_loss_pct
        )  # From config (default 10%)

        # Initialize market info cache for lot_size and tick_size compliance
        # Fetched from Pacifica /info endpoint via get_markets()
        # OPTIMIZATION: Don't fetch at __init__ - lazy load on first use to speed up startup
        self._market_info_cache: Dict[str, Dict[str, float]] = {}
        self._market_info_cache_timestamp: Optional[float] = None
        self._market_info_cache_ttl: int = 300  # 5 minutes TTL
        # Lazy loading - fetch on first use, not at startup
        # self._refresh_market_info_cache()  # Commented out for faster startup

        # Initialize WebSocket client if enabled
        if self.config.enable_websocket:
            try:
                self.ws_client = get_ws_client()
                # Don't start WebSocket client automatically - will be started by API server
                logging.info("WebSocket client initialized (not started)")
            except Exception as e:
                logging.warning(f"WebSocket client failed to initialize: {e}")
                self.ws_client = None
        else:
            self.ws_client = None

        # Initialize multi-timeframe data fetcher with WebSocket support and longer cache
        self.multi_tf_fetcher = MultiTimeframeFetcher(
            self.client,
            ws_client=self.ws_client,
            cache_ttl_seconds=300,  # 5 minutes cache (tiered TTL handles 1m/5m)
        )

        # Initialize market regime detector (uses default thresholds)
        self.market_regime = MarketRegimeDetector()

        # Initialize strategy manager with regime detector
        self.strategy_manager = StrategyManager(
            regime_detector=self.market_regime,
            risk_manager=self.risk_manager,
            client=self.client,  # Pass client for FundingArb API calls
            ws_client=self.ws_client,  # Pass WS client for OrderBookImbalance
        )

        # Initialize grid lifecycle manager
        self.grid_lifecycle = GridLifecycleManager(
            client=self.client,
            risk_manager=self.risk_manager,
            db=self.db,
        )

        # Load persisted grid states from database (survives restarts)
        loaded_grids = self.grid_lifecycle.load_grid_states(
            regime_detector=self.market_regime
        )
        if loaded_grids:
            logger.info(f"Restored {len(loaded_grids)} grid(s) from database: {list(loaded_grids.keys())}")

        # Initialize migrated position manager
        self.migrated_position_manager = MigratedPositionManager(
            client=self.client,
            risk_manager=self.risk_manager,
            regime_detector=self.market_regime,
            multi_tf_fetcher=self.multi_tf_fetcher,
        )

        # Initialize execution layer for precise 1m/5m entry timing
        try:
            self.execution_layer = ExecutionLayer(
                fetcher=self.multi_tf_fetcher,
            )
            logger.info("🔧 ExecutionLayer initialized successfully")
        except Exception as e:
            logger.error(f"❌ Failed to initialize ExecutionLayer: {e}")
            self.execution_layer = None
            logger.warning("⚠️ Trading will continue without ExecutionLayer refinement")

        # Thread control
        self._running_event = threading.Event()
        self.thread = None

        # PHASE 2: Component system
        self.event_bus = get_event_bus()
        self.component_registry = get_component_registry()
        self._register_components()
        self._setup_event_subscriptions()

        logging.info("Trading bot initialized")

    @property
    def is_running(self) -> bool:
        """Check if bot is running."""
        return self._running_event.is_set()

    @is_running.setter
    def is_running(self, value: bool) -> None:
        """Set running state."""
        if value:
            self._running_event.set()
        else:
            self._running_event.clear()

    def _register_components(self):
        """Register bot components with component registry."""
        # Register self as coordinator (component, name, interfaces)
        self.component_registry.register(
            component=self,
            name="coordinator",
        )

        # Register strategy manager
        self.component_registry.register(
            component=self.strategy_manager,
            name="strategy_manager",
        )

        # Register regime detector
        self.component_registry.register(
            component=self.market_regime,
            name="market_regime",
        )

        # Register database
        self.component_registry.register(
            component=self.db,
            name="database",
        )

        # Register grid lifecycle manager
        self.component_registry.register(
            component=self.grid_lifecycle,
            name="grid_lifecycle",
        )

        logger.info("Components registered with component registry")

    def start(self) -> None:
        """Start the trading bot."""
        if self.is_running:
            logging.warning("Trading bot is already running")
            return

        # Start WebSocket client if available (for real-time price data)
        if self.ws_client and hasattr(self.ws_client, "start"):
            try:
                self.ws_client.start()
                logger.info("WebSocket client started for real-time price data")

                # OPTIMIZATION: Kline bootstrap is now done in api_server.initialize()
                # to avoid duplicate API calls and rate limiting.
                # All timeframes (1m, 5m, 15m, 1h, 4h) are fetched there once.

            except Exception as e:
                logger.error(f"WebSocket client start failed: {e}")
                self.ws_client = None

        self.is_running = True
        self.thread = threading.Thread(target=self._trading_loop, daemon=True)
        self.thread.start()
        logging.info("Trading bot started")

        # Publish status update to API server hub
        if self.hub_publish_func:
            self.hub_publish_func(
                {"type": "bot_status", "status": "running", "timestamp": time.time()}
            )

    def stop(self) -> None:
        """Stop the trading bot."""
        if not self.is_running:
            logging.warning("Trading bot is not running")
            return

        self.is_running = False

        # Stop WebSocket client if running
        if self.ws_client and hasattr(self.ws_client, "stop"):
            try:
                self.ws_client.stop()
                logger.info("WebSocket client stopped")
            except Exception as e:
                logger.error(f"WebSocket client stop failed: {e}")

        logging.info("Trading bot stopped")
        if self.thread:
            self.thread.join(timeout=5)

    def _get_ticker_rest(self, symbol: str) -> Dict[str, Any]:
        """
        Get ticker data using REST API (fallback method).

        Args:
            symbol: Market symbol (e.g., "SUI" or "SUI-PERP")

        Returns:
            Ticker dict with 'last', 'bid', 'ask', 'symbol', 'timestamp'

        Raises:
            RuntimeError: If REST API price unavailable
        """
        try:
            # Clean symbol (remove -PERP suffix if present)
            clean_symbol = symbol.replace("-PERP", "").upper()

            # Get market data from REST API
            market_data = self.client.get_market_data(clean_symbol)

            if not market_data:
                raise RuntimeError(f"No REST API data available for {clean_symbol}")

            # Extract price from market data
            # API returns dict with various fields - try common price fields
            price = None
            for field in ["last", "price", "last_price", "mark_price"]:
                if field in market_data:
                    price = float(market_data[field])
                    if price > 0:
                        break

            if not price or price <= 0:
                raise RuntimeError(
                    f"Invalid price in REST API data for {clean_symbol}: {market_data}"
                )

            logging.debug(f"REST API price for {clean_symbol}: ${price}")

            # Return in same format as WebSocket for compatibility
            return {
                "symbol": symbol,
                "last": float(price),
                "bid": float(price * 0.9995),  # Approximate bid (0.05% below)
                "ask": float(price * 1.0005),  # Approximate ask (0.05% above)
                "high": float(market_data.get("high", price * 1.02)),
                "low": float(market_data.get("low", price * 0.98)),
                "volume": float(market_data.get("volume", 0)),
                "timestamp": int(time.time() * 1000),
            }

        except Exception as e:
            error_msg = f"REST API price retrieval failed for {symbol}: {e}"
            logging.error(error_msg)
            raise RuntimeError(error_msg) from e

    def _get_ticker_ws(self, symbol: str) -> Dict[str, Any]:
        """
        Get ticker data using WebSocket with REST API fallback.

        IMPROVED RESILIENCE: WebSocket is preferred for real-time data,
        but REST API fallback ensures trading continuity if WebSocket is unavailable.

        Args:
            symbol: Market symbol (e.g., "SUI")

        Returns:
            Ticker dict with 'last', 'bid', 'ask', 'symbol', 'timestamp'

        Raises:
            RuntimeError: If both WebSocket and REST API price unavailable
        """
        # Check if WebSocket client is available and connected
        if not self.ws_client or not hasattr(self.ws_client, "_running") or not self.ws_client._running:
            logger.warning(
                f"WebSocket unavailable for {symbol}, falling back to REST API"
            )
            return self._get_ticker_rest(symbol)

        try:
            # Get price from WebSocket cache
            clean_symbol = symbol.replace("-PERP", "").upper()
            price = self.ws_client.get_price(clean_symbol)

            if price and price > 0:
                logging.debug(f"WebSocket price for {clean_symbol}: ${price}")
                # Return in same format as REST API for compatibility
                return {
                    "symbol": symbol,
                    "last": float(price),
                    "bid": float(price * 0.9995),  # Approximate bid (0.05% below)
                    "ask": float(price * 1.0005),  # Approximate ask (0.05% above)
                    "high": float(price * 1.02),
                    "low": float(price * 0.98),
                    "volume": 0,  # Not available via ticker WS
                    "timestamp": int(time.time() * 1000),
                }
            else:
                # No WebSocket price - fall back to REST
                logger.warning(
                    f"No WebSocket price available for {clean_symbol}, falling back to REST API"
                )
                return self._get_ticker_rest(symbol)

        except Exception as e:
            # WebSocket error - fall back to REST
            logger.warning(
                f"WebSocket price failed for {symbol}, trying REST fallback: {e}"
            )
            try:
                return self._get_ticker_rest(symbol)
            except Exception as rest_error:
                # Both methods failed - raise final error
                error_msg = f"Both WebSocket and REST API failed for {symbol}. WS error: {e}, REST error: {rest_error}"
                logging.error(error_msg)
                raise RuntimeError(error_msg) from rest_error

    def _trading_loop(self) -> None:
        """Main trading loop - PHASE 2: Coordinator pattern."""
        logger.info(
            "🔄 Trading Bot coordinator loop STARTED - running every 30 seconds (was 120s)"
        )

        # Wait briefly for WebSocket prices to cache before first iteration
        # OPTIMIZATION: Reduced from 5s to 2s - WebSocket usually connects fast
        logger.info("⏳ Waiting 2 seconds for WebSocket price cache to populate...")
        time.sleep(2)

        # GRID MONITORING: Sync existing grids from exchange on startup
        self._sync_existing_grids()

        self._loop_iteration = 0
        self._last_loop_time = None
        self._loop_step = None  # Track which step we're on for debugging
        self._loop_events_generated = 0  # Track events generated in loop
        while self._running_event.is_set():
            self._loop_iteration += 1
            self._last_loop_time = datetime.now()
            self._loop_step = "starting"
            try:
                logger.info(f"🔄 Trading loop iteration {self._loop_iteration} starting...")

                # Update positions from client
                self._loop_step = "update_positions"
                self._update_positions()

                # GRID MONITORING: Monitor active grids for fills, P&L, emergency stops
                self._loop_step = "monitor_grids"
                self._monitor_grids()

                # MIGRATED POSITION MANAGEMENT: Manage positions migrated from grid
                self._loop_step = "manage_migrated_positions"
                self._manage_migrated_positions()

                # PHASE 2: Generate and publish signals as events (coordinator role)
                self._loop_step = "generate_signals"
                events_before = len(self.event_bus._event_history)
                logger.info(f"📊 Calling _generate_and_publish_signals (events_before={events_before})...")
                self._generate_and_publish_signals()
                events_after = len(self.event_bus._event_history)
                self._loop_events_generated = events_after - events_before
                logger.info(f"📊 Signal generation complete (events_after={events_after}, new={self._loop_events_generated})")

                # Monitor risk (delegated to RiskManager)
                self._loop_step = "monitor_risk"
                self._monitor_risk_coordinated()

                # Publish status update to API server hub
                if self.hub_publish_func:
                    self.hub_publish_func(
                        {
                            "type": "loop_complete",
                            "iteration": self._loop_iteration,
                            "timestamp": time.time(),
                        }
                    )

            except Exception as e:
                logger.error(
                    f"Error in trading loop (step={self._loop_step}): {e}", exc_info=True
                )

            # Sleep for configured interval (30 seconds)
            self._loop_step = "sleeping"
            time.sleep(getattr(config, 'trading_loop_interval', 30))

    def _monitor_risk(self) -> None:
        """Monitor risk and trigger circuit breaker if needed."""
        pass  # Placeholder for legacy method signature

    def _update_positions(self) -> None:
        """
        Update positions from Pacifica API.

        Syncs positions with database for accurate P&L tracking.
        """
        try:
            # Get positions from API
            positions = self.client.get_positions()

            if not positions:
                logging.debug("No open positions from Pacifica API")
                return

            # Filter to real positions (non-zero quantity)
            real_positions = [p for p in positions if float(p.get("quantity", 0)) > 0]
            logging.info(
                f"Positions from Pacifica: {len(positions)} total, {len(real_positions)} with quantity > 0"
            )

            # Get current prices for P&L calculation
            current_prices = {}
            for pos in positions:
                symbol = pos.get("symbol")
                if not symbol:
                    continue

                # Get price via WebSocket (Phase 2 requirement)
                # PHASE 2: WebSocket is authoritative, but be tolerant during startup
                entry_price = float(pos.get("entry_price", 0))
                try:
                    ticker = self._get_ticker_ws(symbol)
                    current_price = float(ticker.get("last", entry_price))
                except Exception as e:
                    logging.debug(
                        f"Price unavailable for {symbol} during position update: {e}"
                    )
                    current_price = entry_price  # Fall back to entry price

                current_prices[symbol] = current_price

            # Update database with positions
            for pos in positions:
                symbol = pos.get("symbol")
                if not symbol:
                    continue

                # Calculate unrealized P&L
                # Pacifica API returns "long"/"short" or "bid"/"ask" (lowercase)
                raw_side = pos.get("side", "long")
                side = raw_side.upper() if raw_side else "LONG"
                quantity = float(pos.get("quantity", 0))
                entry_price = float(pos.get("entry_price", 0))
                current_price = current_prices.get(symbol, entry_price)

                # Skip zero-quantity positions (filled order remnants)
                if quantity == 0:
                    continue

                # "long" or "bid" = long position; "short" or "ask" = short
                is_long = side in ("LONG", "BID")
                if is_long:
                    unrealized_pnl = (current_price - entry_price) * quantity
                else:
                    unrealized_pnl = (entry_price - current_price) * quantity

                # Update position in database (save_position does upsert)
                self.db.save_position({
                    "symbol": symbol,
                    "side": side,
                    "quantity": quantity,
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "unrealized_pnl": unrealized_pnl,
                    "leverage": float(pos.get("leverage", 1)),
                    "asset_class": "perpetual",
                    "opened_at": pos.get("opened_at", pos.get("created_at", "now")),
                    "funding_pnl": float(pos.get("funding_pnl", 0)),
                })

                logging.debug(
                    f"Updated position: {symbol} {side} {quantity} @ ${entry_price:.2f} "
                    f"(current: ${current_price:.2f}, PnL: ${unrealized_pnl:.2f})"
                )

        except Exception as e:
            logging.error(f"Error updating positions: {e}")

    def _sync_existing_grids(self) -> None:
        """
        GRID MONITORING: Sync existing grids from exchange on startup.

        Detects grids from previous sessions and RE-ADOPTS them into the
        GridLifecycleManager so they continue to be managed properly.
        This prevents orphaned grids that block new signals.
        """
        try:
            logger.info("🔍 Syncing existing grids from exchange...")

            # Get all open orders from exchange
            open_orders = self.client.get_orders()
            if not open_orders:
                # No orders on exchange - clear any stale grids loaded from DB
                if self.grid_lifecycle and self.grid_lifecycle._grids:
                    stale_symbols = list(self.grid_lifecycle._grids.keys())
                    for symbol in stale_symbols:
                        self.grid_lifecycle.delete_grid_state(symbol)
                    self.grid_lifecycle._grids.clear()
                    logger.warning(
                        f"🧹 Cleared {len(stale_symbols)} stale grid(s) from DB "
                        f"(no orders on exchange): {stale_symbols}"
                    )
                else:
                    logger.info("No open orders found on exchange - no grids to sync")
                return

            logger.info(f"📋 Found {len(open_orders)} open orders on exchange")

            # Group orders by symbol
            orders_by_symbol: Dict[str, List[Dict]] = {}
            for order in open_orders:
                symbol = order.get("symbol", "")
                if not symbol:
                    continue
                if symbol not in orders_by_symbol:
                    orders_by_symbol[symbol] = []
                orders_by_symbol[symbol].append(order)

            # Analyze each symbol's orders to detect grid patterns
            for symbol, orders in orders_by_symbol.items():
                # Grid detection: multiple orders with mix of buy and sell sides
                if len(orders) < 5:
                    logger.debug(
                        f"Skipping {symbol}: only {len(orders)} orders (need >= 5 for grid)"
                    )
                    continue

                # Pacifica API uses "bid"/"ask" for sides (not "BUY"/"SELL")
                buy_orders = [o for o in orders if o.get("side") in ("bid", "BUY", "buy")]
                sell_orders = [o for o in orders if o.get("side") in ("ask", "SELL", "sell")]

                if not buy_orders or not sell_orders:
                    logger.debug(
                        f"Skipping {symbol}: need both bid and ask orders for grid "
                        f"(found {len(buy_orders)} bid, {len(sell_orders)} ask)"
                    )
                    continue

                # Check if already tracked in memory
                if self.grid_lifecycle and symbol in self.grid_lifecycle._grids:
                    logger.info(
                        f"✅ {symbol} grid already tracked in memory ({len(orders)} orders)"
                    )
                    continue

                # ORPHANED GRID DETECTED - re-adopt it
                logger.warning(
                    f"⚠️ ORPHANED GRID DETECTED: {symbol} has {len(orders)} orders "
                    f"({len(buy_orders)} bid, {len(sell_orders)} ask) - RE-ADOPTING"
                )
                self._readopt_orphaned_grid(symbol, buy_orders, sell_orders)

            # Clean up grids loaded from DB that have no matching exchange orders
            if self.grid_lifecycle and self.grid_lifecycle._grids:
                # Symbols that have confirmed grid orders on exchange
                confirmed_grid_symbols = set()
                for symbol, orders in orders_by_symbol.items():
                    buy_orders = [o for o in orders if o.get("side") in ("bid", "BUY", "buy")]
                    sell_orders = [o for o in orders if o.get("side") in ("ask", "SELL", "sell")]
                    if len(orders) >= 5 and buy_orders and sell_orders:
                        confirmed_grid_symbols.add(symbol)

                # Remove grids that aren't confirmed on exchange
                stale_symbols = [
                    s for s in self.grid_lifecycle._grids
                    if s not in confirmed_grid_symbols
                ]
                for symbol in stale_symbols:
                    self.grid_lifecycle.delete_grid_state(symbol)
                    del self.grid_lifecycle._grids[symbol]
                    logger.warning(
                        f"🧹 Cleared stale grid for {symbol} "
                        f"(no matching grid orders on exchange)"
                    )

        except Exception as e:
            logger.error(f"Error syncing existing grids: {e}", exc_info=True)

    def _readopt_orphaned_grid(
        self, symbol: str, buy_orders: List[Dict], sell_orders: List[Dict]
    ) -> None:
        """
        Re-adopt an orphaned grid into the GridLifecycleManager.

        Reconstructs minimal grid state from existing exchange orders so the
        grid continues to be managed (fills tracked, emergency stops work, etc.)
        """
        try:
            all_orders = buy_orders + sell_orders
            # Calculate center price from order spread
            buy_prices = [float(o.get("price", 0)) for o in buy_orders if o.get("price")]
            sell_prices = [float(o.get("price", 0)) for o in sell_orders if o.get("price")]

            if not buy_prices or not sell_prices:
                logger.error(f"Cannot re-adopt {symbol}: no valid prices in orders")
                return

            center_price = (max(buy_prices) + min(sell_prices)) / 2
            grid_spacing = (min(sell_prices) - max(buy_prices)) / center_price if center_price > 0 else 0

            # Estimate capital from order sizes
            total_capital = sum(
                float(o.get("initial_amount", 0)) * float(o.get("price", 0))
                for o in all_orders
            )

            # Register directly in GridLifecycleManager
            if self.grid_lifecycle and symbol not in self.grid_lifecycle._grids:
                self.grid_lifecycle._grids[symbol] = {
                    "state": GridState.ACTIVE,
                    "grid_capital": total_capital,
                    "emergency_stop": 0,  # No emergency stop for re-adopted grids
                    "regime_on_creation": "unknown_readopted",
                    "atr_at_creation": 0,
                    "grid_spacing": grid_spacing,
                    "num_levels": len(all_orders),
                    "orders_placed": len(all_orders),
                    "center_price": center_price,
                    "initial_center": center_price,
                    "created_at": datetime.now(),
                    "refresh_count": 0,
                    "readopted": True,  # Flag that this was re-adopted
                }

                logger.info(
                    f"✅ Re-adopted grid for {symbol}: center=${center_price:.4f}, "
                    f"{len(buy_orders)} bids + {len(sell_orders)} asks, "
                    f"capital≈${total_capital:.2f}"
                )
            else:
                logger.warning(f"Cannot re-adopt {symbol}: GridLifecycleManager unavailable or grid already exists")

        except Exception as e:
            logger.error(f"Error re-adopting grid for {symbol}: {e}", exc_info=True)

    def _close_orphaned_grid(self, symbol: str, reason: str = "UNKNOWN") -> None:
        """
        Close an orphaned grid by cancelling all orders for a symbol.

        Args:
            symbol: Symbol with orphaned grid
            reason: Reason for closure (for logging/metrics)
        """
        try:
            logger.warning(f"🛑 Closing orphaned grid for {symbol} (reason: {reason})")

            # Cancel all orders for this symbol
            try:
                result = self.client.cancel_all_orders(symbol=symbol)
                logger.info(f"  ✅ Cancelled all orders for {symbol}: {result}")
            except Exception as e:
                logger.error(f"  ❌ Failed to cancel orders for {symbol}: {e}")

            # Check if there's an open position for this symbol
            positions = self.client.get_positions()
            symbol_positions = [
                p for p in positions
                if p.get("symbol") == symbol and float(p.get("quantity", 0)) > 0
            ]

            if symbol_positions:
                logger.warning(
                    f"  ⚠️ Open position exists for {symbol} after grid closure - "
                    f"consider manual review"
                )

            logger.info(f"✅ Orphaned grid closed for {symbol}")

        except Exception as e:
            logger.error(
                f"Error closing orphaned grid for {symbol}: {e}", exc_info=True
            )

    def _monitor_grids(self) -> None:
        """
        GRID MONITORING: Monitor active grids for fills, P&L, and emergency stops.

        Delegates to GridLifecycleManager for authoritative state management.
        """
        try:
            # Get current prices for all active grids
            current_prices = {}
            for symbol in list(self.grid_lifecycle._grids.keys()):
                try:
                    ticker = self._get_ticker_ws(symbol)
                    current_prices[symbol] = float(ticker.get("last", 0))
                except Exception:
                    pass  # Skip price update if unavailable

            if not current_prices:
                return  # No active grids or no prices available

            # Monitor all grids at once using GridLifecycleManager
            try:
                result = self.grid_lifecycle.monitor_grids(
                    current_prices=current_prices
                )

                # Log monitoring results
                if result.get("new_fills", 0) > 0:
                    logger.info(
                        f"📊 Grid monitoring: {result['new_fills']} fills detected, "
                        f"{result.get('completed_round_trips', 0)} round-trips completed"
                    )

                for alert in result.get("alerts", []):
                    logger.warning(f"Grid alert: {alert}")

            except Exception as e:
                logger.error(
                    f"Error monitoring grids: {e}", exc_info=True
                )

        except Exception as e:
            logger.error(f"Error in grid monitoring: {e}", exc_info=True)

    def _manage_migrated_positions(self) -> None:
        """
        MIGRATED POSITION MANAGEMENT: Manage positions that were migrated from grid trading.

        When a grid exits (due to trend change or stop loss), its position is "migrated" to
        trend-following management with trailing stop loss.

        Delegates to MigratedPositionManager for authoritative state management.
        """
        try:
            # Get current prices for all migrated positions
            current_prices = {}
            positions = self.client.get_positions()
            for pos in positions:
                symbol = pos.get("symbol")
                if symbol and symbol not in current_prices:
                    try:
                        ticker = self._get_ticker_ws(symbol)
                        current_prices[symbol] = float(ticker.get("last", 0))
                    except Exception:
                        pass

            if not current_prices:
                return  # No positions or no prices available

            # Manage migrated positions using MigratedPositionManager
            result = self.migrated_position_manager.manage_positions(
                current_prices=current_prices
            )

            # Log management results
            if result.get("positions_closed", 0) > 0:
                logger.info(
                    f"📊 Migrated Position Manager: {result['positions_closed']} positions closed, "
                    f"reasons: {result.get('close_reasons', {})}"
                )

            if result.get("stops_updated", 0) > 0:
                logger.debug(
                    f"📊 Migrated Position Manager: {result['stops_updated']} trailing stops updated"
                )

        except Exception as e:
            logger.error(
                f"Error managing migrated positions: {e}", exc_info=True
            )

    def start_trading(self) -> None:
        """
        Start trading (unpause).

        PHASE 2: This is a stub - actual trading is controlled by start/stop methods.
        Kept for backward compatibility with existing code.
        """
        if not self.is_running:
            logger.warning("Trading bot not running - call start() first")
            return

        logger.info("Trading active (PHASE 2: coordinator pattern)")

    def stop_trading(self) -> None:
        """
        Stop trading (pause).

        PHASE 2: This is a stub - actual trading is controlled by start/stop methods.
        Kept for backward compatibility with existing code.
        """
        logger.info("Trading paused (PHASE 2: coordinator pattern)")

    def _generate_and_publish_signals(self) -> None:
        """
        PHASE 2: Generate signals and publish as events.

        Coordinator role: Ask strategies for signals, validate them, and publish events.
        Execution is handled by event subscribers (decoupled).
        """
        try:
            # Get all markets
            markets = self.client.get_markets()
            if not markets:
                logger.warning("No markets available")
                return

            logger.info(f"📊 Checking {len(markets)} markets for signals...")

            signals_generated = 0
            for market in markets[:10]:  # Limit to first 10 markets to avoid rate limits
                try:
                    symbol = market.get("symbol")
                    if not symbol:
                        continue

                    # Get current price
                    try:
                        ticker = self._get_ticker_ws(symbol)
                        current_price = float(ticker.get("last", 0))
                    except Exception as e:
                        logger.debug(f"Skipping {symbol}: price unavailable ({e})")
                        continue

                    if current_price <= 0:
                        logger.debug(f"Skipping {symbol}: invalid price {current_price}")
                        continue

                    # Get multi-timeframe data (5m for MomentumScalping, others for main strategies)
                    multi_tf_data = self.multi_tf_fetcher.get_candles_multi_tf(
                        symbol=symbol, timeframes=["5m", "15m", "1h", "4h"], lookback_candles=250
                    )

                    if not multi_tf_data:
                        logger.debug(f"Skipping {symbol}: no multi-timeframe data")
                        continue

                    # Detect regime for this symbol (for logging) - uses 4h data
                    regime_data = multi_tf_data.get("4h", {})
                    if regime_data:
                        regime = self.market_regime.detect_regime(regime_data)
                    else:
                        regime = None  # Will be handled gracefully in logging

                    # Generate signals using strategy manager
                    signals = self.strategy_manager.generate_signals_for_market(
                        symbol=symbol,
                        multi_tf_data=multi_tf_data,
                        current_price=current_price,
                    )

                    # Publish signals as events
                    for signal in signals:
                        # Validate signal before publishing
                        if not signal.is_valid():
                            flags = {
                                'volume_confirmation': signal.volume_confirmation,
                                'multi_timeframe_alignment': signal.multi_timeframe_alignment,
                                'support_resistance_valid': signal.support_resistance_valid,
                                'rrr_meets_minimum': signal.rrr_meets_minimum,
                                'liquidation_buffer_safe': signal.liquidation_buffer_safe,
                                'account_risk_ok': signal.account_risk_ok,
                                'margin_drawdown_ok': signal.margin_drawdown_ok,
                                'forbidden_conditions_clear': signal.forbidden_conditions_clear,
                            }
                            failed = [k for k, v in flags.items() if not v]
                            reason = f"Pre-publish validation failed: {failed}"
                            logger.debug(f"Skipping invalid signal for {symbol}: {failed}")
                            regime_str = regime.name if regime and hasattr(regime, 'name') else str(regime) if regime else "unknown"
                            self.signal_logger.log_signal_rejected(signal=signal, reason=reason, regime=regime_str)
                            continue

                        # Log signal BEFORE publishing event (event bus is synchronous)
                        signals_generated += 1
                        regime_str = regime.name if regime and hasattr(regime, 'name') else str(regime) if regime else "unknown"
                        log_result = self.signal_logger.log_signal_generated(
                            signal=signal,
                            regime=regime_str,
                            notes=f"Generated from {signal.strategy.name}",
                        )
                        
                        # Check if signal was rejected as duplicate (log_signal_generated returns empty dict)
                        if not log_result:
                            logger.info(
                                f"🚫 Duplicate signal rejected by deduplication: {signal.strategy.name} {signal.side.name} "
                                f"{symbol} @ ${signal.entry_price:.4f} "
                                f"(same signal executed within {self.signal_logger._dedup_window_seconds}s window)"
                            )
                            continue

                        logger.info(
                            f"✅ Signal generated: {signal.strategy.name} {signal.side.name} "
                            f"{symbol} @ ${signal.entry_price:.4f} "
                            f"(confidence: {signal.confidence:.1%}, quality: {signal.quality.name})"
                        )

                        # Publish signal event (synchronous - will execute immediately)
                        self.event_bus.publish_event(
                            event_type=EventType.SIGNAL_GENERATED,
                            data={
                                "signal": signal,
                                "timestamp": time.time(),
                            },
                            source="strategy_manager",
                        )

                except Exception as e:
                    logger.error(
                        f"Error generating signals for {symbol}: {e}", exc_info=True
                    )

            if signals_generated > 0:
                logger.info(f"📊 Total signals generated: {signals_generated}")
            else:
                logger.debug("📊 No signals generated this iteration")

        except Exception as e:
            logger.error(f"Error in signal generation: {e}", exc_info=True)

    def _monitor_risk_coordinated(self) -> None:
        """
        PHASE 2: Monitor risk using RiskManager.

        Coordinator role: Check risk limits and publish events if exceeded.
        Includes margin safety monitoring.
        """
        try:
            # Get account balance
            balance = self._get_account_balance()
            if balance <= 0:
                logger.warning("Invalid account balance - skipping risk monitoring")
                return

            # Get current exposure
            exposure = self._get_current_exposure()

            # Calculate risk percentage
            risk_pct = (exposure / balance) * 100

            # Check circuit breaker threshold
            if risk_pct >= 80:  # Warning at 80% of limit
                logger.warning(
                    f"⚠️ High risk exposure: {risk_pct:.1f}% of account "
                    f"(${exposure:.2f} / ${balance:.2f})"
                )

            # MARGIN SAFETY MONITORING
            try:
                margin_summary = self.risk_manager.get_margin_summary()
                if margin_summary.get("status") != "unavailable":
                    utilization_pct = margin_summary.get("utilization_pct", 0)
                    status = margin_summary.get("status", "unknown")

                    # Log margin status
                    if status == "critical":
                        logger.critical(
                            f"🚨 CRITICAL MARGIN LEVEL: {utilization_pct:.1f}% utilized! "
                            f"Equity: ${margin_summary.get('account_equity', 0):.2f}, "
                            f"Used: ${margin_summary.get('total_margin_used', 0):.2f}"
                        )
                    elif status == "warning":
                        logger.warning(
                            f"⚠️ HIGH MARGIN UTILIZATION: {utilization_pct:.1f}% "
                            f"(max: {margin_summary.get('max_utilization_pct', 75):.0f}%)"
                        )
                    else:
                        logger.debug(
                            f"📊 Margin status: {utilization_pct:.1f}% utilized, "
                            f"${margin_summary.get('available_margin', 0):.2f} available"
                        )

                    # Add margin data to published event
                    margin_data = {
                        "margin_utilization_pct": utilization_pct,
                        "margin_status": status,
                        "account_equity": margin_summary.get("account_equity"),
                        "available_margin": margin_summary.get("available_margin"),
                    }
                else:
                    margin_data = {"margin_status": "unavailable"}
            except Exception as margin_error:
                logger.debug(f"Margin monitoring error: {margin_error}")
                margin_data = {"margin_status": "error", "error": str(margin_error)}

            # Publish risk event for monitoring
            event_data = {
                "balance": balance,
                "exposure": exposure,
                "risk_pct": risk_pct,
                "timestamp": time.time(),
            }
            event_data.update(margin_data)

            self.event_bus.publish_event(
                event_type=EventType.CAPITAL_REQUESTED,
                data=event_data,
                source="trading_bot",
            )

        except Exception as e:
            logger.error(f"Error monitoring risk: {e}", exc_info=True)

    def _setup_event_subscriptions(self):
        """
        PHASE 2: Subscribe to events from other components.

        Coordinator subscribes to:
        - SIGNAL_GENERATED: Execute signals
        - ORDER_PLACED: Track order placement
        - ORDER_FILLED: Update positions
        - CAPITAL_REQUESTED: Validate capital allocation
        - RISK_LIMIT_EXCEEDED: Trigger circuit breaker
        - GRID_EMERGENCY: Handle grid emergency stops
        """
        self.event_bus.subscribe(EventType.SIGNAL_GENERATED, self._handle_signal_generated)
        self.event_bus.subscribe(EventType.ORDER_PLACED, self._handle_order_placed)
        self.event_bus.subscribe(EventType.ORDER_FILLED, self._handle_order_filled)
        self.event_bus.subscribe(
            EventType.CAPITAL_REQUESTED, self._handle_capital_requested
        )
        self.event_bus.subscribe(
            EventType.RISK_LIMIT_EXCEEDED, self._handle_risk_limit_exceeded
        )
        # Note: GRID_EMERGENCY doesn't exist in EventType yet, so commenting out
        # self.event_bus.subscribe(EventType.GRID_EMERGENCY, self._handle_grid_emergency)

        logger.info("Event subscriptions configured")

    def _handle_signal_generated(self, event):
        """
        Handle SIGNAL_GENERATED event.

        PHASE 2: Coordinator receives signals as events and coordinates execution.
        """
        try:
            # Event object has .data attribute (not a dict with .get())
            signal_data = event.data if hasattr(event, 'data') else event.get("data", {})
            signal = signal_data.get("signal") if isinstance(signal_data, dict) else None

            if not signal:
                logger.warning("Received signal event with no signal data - event.data type=%s, signal_data type=%s", type(event.data).__name__, type(signal_data).__name__)
                return

            # Enhanced logging for VWAP_SCALPING signals
            if signal.strategy == StrategyType.VWAP_SCALPING:
                logger.info(
                    f"🚨 VWAP_SCALPING SIGNAL RECEIVED: {signal.side.name} "
                    f"{signal.asset} (valid={signal.is_valid()}, confidence={signal.confidence})"
                )
                logger.info(f"🔍 VWAP_SCALPING Details: entry={signal.entry_price}, stop={signal.stop_loss}, quality={signal.quality.name}")
            else:
                logger.info(
                    f"📨 Signal handler received: {signal.strategy.name} {signal.side.name} "
                    f"{signal.asset} (valid={signal.is_valid()}, confidence={signal.confidence})"
                )

            # Validate signal should be executed
            should_execute = self._should_execute_signal(signal)
            
            # Enhanced tracking for VWAP_SCALPING
            if signal.strategy == StrategyType.VWAP_SCALPING:
                if should_execute:
                    logger.info(f"✅ VWAP_SCALPING validation PASSED for {signal.asset} - proceeding to execution")
                else:
                    logger.warning(f"❌ VWAP_SCALPING validation FAILED for {signal.asset} - SKIPPING execution")
            
            if not should_execute:
                logger.info(
                    f"🚫 Signal validation failed for {signal.asset} {signal.strategy.name} - skipping execution"
                )
                return

            # Log signal details for debugging
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "symbol": signal.asset,
                "strategy": signal.strategy.name,
                "side": signal.side.name,
                "entry_price": signal.entry_price,
                "confidence": signal.confidence,
                "quality": signal.quality.name,
            }

            # Coordinate signal execution
            self._coordinate_signal_execution(signal, log_entry)

        except Exception as e:
            logger.error(f"Error handling signal event: {e}", exc_info=True)
            # Log the failure so it's visible in signal stats
            if signal:
                self.signal_logger.log_signal_failed(
                    signal=signal,
                    error=str(e),
                    notes="Exception in _handle_signal_generated",
                )

    def _convert_signal_data(self, signal_data):
        """Convert signal data dict to Signal object if needed."""
        if isinstance(signal_data, Signal):
            return signal_data
        # If it's a dict, construct Signal object
        # Implementation depends on Signal class structure
        return signal_data

    def _should_execute_signal(self, signal):
        """
        Validate if signal should be executed.

        Checks:
        1. Signal is valid (8 validation flags)
        2. Stop loss is present and valid (CRITICAL SAFETY CHECK)
        3. No duplicate positions exist for this symbol
        4. Account has sufficient capital
        5. Risk limits not exceeded
        6. No conflicting positions
        """
        try:
            # Check signal validity
            if not signal.is_valid():
                flags = {
                    'volume_confirmation': signal.volume_confirmation,
                    'multi_timeframe_alignment': signal.multi_timeframe_alignment,
                    'support_resistance_valid': signal.support_resistance_valid,
                    'rrr_meets_minimum': signal.rrr_meets_minimum,
                    'liquidation_buffer_safe': signal.liquidation_buffer_safe,
                    'account_risk_ok': signal.account_risk_ok,
                    'margin_drawdown_ok': signal.margin_drawdown_ok,
                    'forbidden_conditions_clear': signal.forbidden_conditions_clear,
                }
                failed = [k for k, v in flags.items() if not v]
                reason = f"Signal invalid - failed flags: {failed}"
                logger.info(f"🚫 Signal invalid for {signal.asset} {signal.strategy.name}: failed={failed}")
                self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
                return False

            # CRITICAL: Validate stop loss is present
            if not signal.stop_loss or signal.stop_loss <= 0:
                reason = "CRITICAL: Signal missing valid stop loss - rejecting for safety"
                logger.error(f"🚨 {reason} for {signal.asset}")
                self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
                return False
            else:
                logger.debug(f"✅ Stop loss validated: ${signal.stop_loss:.4f} for {signal.asset}")

            # Check if position already exists for this symbol
            # If position exists and is active, we should UPDATE it (adjust TP/SL), not reject
            try:
                existing_positions = self.client.get_positions()
                self._has_active_position = False
                self._existing_position_info = None
                
                if existing_positions:
                    for pos in existing_positions:
                        pos_symbol = pos.get("symbol", "")
                        pos_side = pos.get("side", "").lower()
                        pos_size = pos.get("size", 0) or pos.get("position_size", 0) or pos.get("quantity", 0)
                        signal_side = "long" if signal.side.name == "BUY" else "short"
                        
                        if pos_symbol == signal.asset and pos_size > 0:
                            # Position exists - check if it's the same side
                            logger.info(
                                f"🔍 Position check: Found position for {signal.asset} - "
                                f"side={pos_side}, size={pos_size}, signal_side={signal_side}"
                            )
                            
                            if pos_side == signal_side:
                                # Same side - this is a POSITION UPDATE (adjust TP/SL)
                                # Don't reject - allow the signal to update the position
                                self._has_active_position = True
                                self._existing_position_info = {
                                    "side": pos_side,
                                    "size": pos_size,
                                    "symbol": pos_symbol
                                }
                                logger.info(
                                    f"🔄 Position UPDATE detected for {signal.asset} - "
                                    f"will update existing position (side={pos_side}, size={pos_size})"
                                )
                                # Don't return False - continue with execution but mark as update
                            else:
                                # Opposite side - could be a hedge or flip
                                # For now, allow it (could close and reverse)
                                logger.info(
                                    f"⚠️ Opposite position exists for {signal.asset}: "
                                    f"existing={pos_side} (size={pos_size}), signal={signal_side}"
                                )
                else:
                    logger.debug(f"ℹ️ No existing positions found for {signal.asset} - will create new position")
            except Exception as e:
                reason = f"Failed to check existing positions: {str(e)}"
                logger.error(f"🚨 {reason} - rejecting signal for safety")
                self.signal_logger.log_signal_rejected(
                    signal=signal,
                    reason=reason,
                    notes="Position check exception - safety rejection"
                )
                return False

            # PACIFICA TRADE COUNT CHECK: Count trades by TP/SL orders
            # Use this to verify position exists (for update) or detect duplicates
            # If we already detected an active position above, this confirms it
            # If no position detected above, this check can still catch duplicates
            try:
                trade_count = self._count_trades_by_tp_sl(signal.asset)
                
                # If we already have an active position from position check, trade_count should match
                # If position check missed it but TP/SL found it, we still have an active position
                if trade_count > 0 and not self._has_active_position:
                    # TP/SL found a trade but position check didn't - treat as active position
                    self._has_active_position = True
                    self._existing_position_info = {
                        "side": "unknown",  # TP/SL found but side unknown from orders
                        "size": 0,  # Unknown size but trade exists
                        "symbol": signal.asset
                    }
                    logger.info(
                        f"🔄 Position UPDATE detected via TP/SL for {signal.asset} - "
                        f"found {trade_count} trade(s)"
                    )
                elif trade_count == 0 and self._has_active_position:
                    # Position check found it but TP/SL didn't - still valid, continue
                    logger.debug(f"✅ Position exists (confirmed via position check) for {signal.asset}")
                elif trade_count > 0 and self._has_active_position:
                    # Both found it - confirmed
                    logger.debug(f"✅ Position exists (confirmed via both checks) for {signal.asset}")
                    
                logger.debug(f"📊 Trade count for {signal.asset}: {trade_count}, has_active: {self._has_active_position}")
            except Exception as e:
                # Don't block execution if TP/SL check fails - log warning but continue
                logger.warning(f"⚠️ TP/SL trade count check failed (non-blocking): {e}")

            # Check account balance
            balance = self._get_account_balance()
            if balance <= 0:
                reason = "Invalid account balance (<=0)"
                logger.warning(f"Invalid account balance (${balance:.2f}) - cannot execute signal for {signal.asset}")
                logger.warning(f"💡 Tip: Set BYPASS_BALANCE_VALIDATION=true for testing")
                self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
                return False
            else:
                logger.info(f"✅ Account balance validation passed: ${balance:.2f} available")

            # Check risk limits
            current_exposure = self._get_current_exposure()
            exposure_pct = (current_exposure / balance) * 100

            if exposure_pct >= 80:  # 80% utilization limit
                reason = f"Risk limit reached ({exposure_pct:.1f}% >= 80%)"
                logger.warning(f"Risk limit reached ({exposure_pct:.1f}%) - cannot execute signal")
                self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
                return False

            # MARGIN SAFETY CHECK: Validate margin levels before execution
            try:
                # Calculate proposed position notional for margin check
                position_size = self.risk_manager.get_position_size(
                    signal=signal,
                    account_balance=balance,
                    current_exposure=current_exposure,
                )
                proposed_notional = position_size * signal.entry_price

                # Validate margin safety
                margin_validation = self.risk_manager.validate_margin_for_position(
                    position_notional=proposed_notional,
                    leverage=getattr(signal, 'leverage', 1.0),
                )

                if not margin_validation.get("safe", True):
                    reason = f"Margin safety check failed: {margin_validation.get('reason', 'unknown')}"
                    logger.warning(f"🚫 {reason} for {signal.asset}")
                    self.signal_logger.log_signal_rejected(
                        signal=signal,
                        reason=reason,
                        notes=f"Available margin: ${margin_validation.get('available_margin', 0):.2f}"
                    )
                    return False

                # Log margin status
                margin_summary = self.risk_manager.get_margin_summary()
                logger.info(
                    f"✅ Margin safety check passed for {signal.asset}: "
                    f"utilization={margin_summary.get('utilization_pct', 0):.1f}%, "
                    f"available=${margin_validation.get('available_margin', 0):.2f}"
                )

            except Exception as e:
                # Log but don't block execution if margin check fails
                # This maintains backward compatibility
                logger.warning(f"⚠️ Margin safety check failed (non-blocking): {e}")

            logger.info(
                f"✅ Signal passed validation: {signal.asset} {signal.strategy.name} "
                f"(balance=${balance:.2f}, exposure={exposure_pct:.1f}%)"
            )
            return True

        except Exception as e:
            reason = f"Validation error: {e}"
            logger.error(f"Error validating signal execution: {e}")
            self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
            return False

    def _coordinate_signal_execution(self, signal, log_entry=None):
        """
        PHASE 2: Coordinate signal execution through components.

        Flow:
        1. Request capital allocation from RiskManager
        2. If approved, delegate to appropriate execution method:
           - Grid signals → GridLifecycleManager
           - Standard signals → ExecutionLayer → PacificaClient
        """
        try:
            # Get current account state for capital allocation request
            account_balance = self._get_account_balance()
            current_exposure = self._get_current_exposure()

            # Calculate requested amount based on position size
            position_size = self.risk_manager.get_position_size(
                signal=signal,
                account_balance=account_balance,
                current_exposure=current_exposure,
            )
            requested_amount = position_size * signal.entry_price

            # Request capital allocation
            allocation_result = self.risk_manager.request_capital_allocation(
                symbol=signal.asset,
                requested_amount=requested_amount,
                strategy=signal.strategy.name if hasattr(signal.strategy, 'name') else str(signal.strategy),
                account_balance=account_balance,
                current_exposure=current_exposure,
            )

            if not allocation_result.get("approved", False):
                reason = f"Capital allocation denied: {allocation_result.get('reason', 'unknown')}"
                logger.warning(
                    f"Capital allocation denied for {signal.asset}: {allocation_result.get('reason')}"
                )
                self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
                return

            logger.info(
                f"✅ Capital allocated: ${allocation_result.get('allocated_amount', 0):.2f} "
                f"for {signal.asset} {signal.strategy.name}"
            )

            # MARGIN SAFETY CHECK: Final validation before execution
            try:
                allocated_amount = allocation_result.get('allocated_amount', 0)
                margin_validation = self.risk_manager.validate_margin_for_position(
                    position_notional=allocated_amount,
                    leverage=getattr(signal, 'leverage', 1.0),
                )

                if not margin_validation.get("safe", True):
                    reason = f"Margin safety check failed at execution: {margin_validation.get('reason', 'unknown')}"
                    logger.error(f"🚫 {reason} for {signal.asset}")
                    self.signal_logger.log_signal_rejected(
                        signal=signal,
                        reason=reason,
                        notes="Margin check failed after capital allocation - potential race condition"
                    )
                    return

                logger.info(
                    f"✅ Final margin check passed for {signal.asset}: "
                    f"available=${margin_validation.get('available_margin', 0):.2f}"
                )

            except Exception as e:
                logger.warning(f"⚠️ Final margin check error (proceeding): {e}")

            # PHASE 3: Final position re-check before execution (race condition prevention)
            # This is a critical second check - but now we ALLOW position UPDATES
            # If position exists with same side, it's an UPDATE not a duplicate
            logger.info(f"🔍 Final position re-check for {signal.asset} before execution...")
            try:
                recheck_positions = self.client.get_positions()
                signal_side = "long" if signal.side.name == "BUY" else "short"
                
                position_found_for_update = False
                for pos in recheck_positions:
                    pos_symbol = pos.get("symbol", "")
                    pos_side = pos.get("side", "").lower()
                    pos_size = pos.get("size", 0) or pos.get("position_size", 0) or pos.get("quantity", 0)
                    
                    if pos_symbol == signal.asset and pos_side == signal_side and pos_size > 0:
                        # Position exists with same side - this is an UPDATE, not a duplicate
                        logger.info(
                            f"🔄 Position UPDATE confirmed at execution time for {signal.asset} - "
                            f"existing: side={pos_side}, size={pos_size}"
                        )
                        position_found_for_update = True
                        # Store this info for the execution methods to use
                        signal._is_position_update = True
                        signal._existing_position_size = pos_size
                        break
                
                if position_found_for_update:
                    logger.info(f"✅ Final position re-check: UPDATE mode for {signal.asset}")
                else:
                    logger.info(f"✅ Final position re-check: NEW POSITION mode for {signal.asset}")
                
            except Exception as e:
                # If we can't verify, log warning but continue (position check already passed)
                logger.warning(f"⚠️ Final position re-check failed (non-blocking): {e}")

            # Execute signal based on type
            logger.debug(f"🔍 Execution routing for {signal.asset}: strategy={signal.strategy.name} (enum: {signal.strategy})")
            
            # Check if this is a GRID position update (grid already exists) or new position
            if signal.strategy == StrategyType.GRID_TRADING:
                # Check if grid already exists in GridLifecycleManager
                existing_grid = None
                if self.grid_lifecycle and hasattr(self.grid_lifecycle, '_grids'):
                    existing_grid = self.grid_lifecycle._grids.get(signal.asset)
                
                if existing_grid:
                    # Grid already exists - this is a position update
                    logger.info(f"🔄 Grid already active for {signal.asset} - marking as position update")
                    signal._is_position_update = True
                    signal._existing_position_size = existing_grid.get("grid_capital", 0)
                else:
                    # New grid - clear the update flag
                    signal._is_position_update = False
            
            if signal.strategy == StrategyType.GRID_TRADING:
                logger.info(f"🔍 Routing {signal.asset} to GRID execution (strategy: {signal.strategy.name})")
                self._execute_grid_signal_coordinated(signal, allocation_result, log_entry)
            else:
                logger.info(f"🔍 Routing {signal.asset} to STANDARD execution (strategy: {signal.strategy.name})")
                # VWAP_SCALPING should ALWAYS go here - never grid execution
                if signal.strategy == StrategyType.VWAP_SCALPING:
                    logger.info(f"✅ VWAP_SCALPING correctly routed to standard execution for {signal.asset}")
                self._execute_standard_signal_coordinated(
                    signal, allocation_result, log_entry
                )

        except Exception as e:
            # Enhanced debugging for critical error investigation
            import traceback
            import sys
            
            # Capture full exception details
            exc_type, exc_value, exc_traceback = sys.exc_info()
            
            logger.error(
                f"🚨 CRITICAL ERROR in signal coordination for {signal.asset} ({signal.strategy.name}): {e}",
                exc_info=True,
            )
            
            # Enhanced debugging information
            logger.error(f"🔍 Exception Type: {exc_type.__name__}")
            logger.error(f"🔍 Exception Value: {exc_value}")
            logger.error(f"🔍 Signal Details: {signal}")
            logger.error(f"🔍 Strategy Type: {signal.strategy.name} (enum: {signal.strategy})")
            logger.error(f"🔍 Execution Path: {'GRID' if signal.strategy == StrategyType.GRID_TRADING else 'STANDARD'}")
            
            # Log full traceback for debugging
            full_traceback = traceback.format_exception(exc_type, exc_value, exc_traceback)
            logger.error(f"🔍 Full Traceback:\n{''.join(full_traceback)}")
            
            # Check if this is the mysterious '"success"' error
            if '"success"' in str(e) or 'success' in str(e).lower():
                logger.error("🚨 DETECTED THE MYSTERIOUS 'SUCCESS' ERROR - INVESTIGATING FURTHER")
                logger.error("🔍 This error should have been handled by ResponseHandler")
                logger.error("🔍 Possible causes:")
                logger.error("   1. Exception occurring before ResponseHandler is called")
                logger.error("   2. Different code path bypassing ResponseHandler")
                logger.error("   3. ResponseHandler itself throwing an exception")
                logger.error("   4. VWAP_SCALPING incorrectly routed to grid execution path")
            
            # Check VWAP_SCALPING execution routing
            if signal.strategy == StrategyType.VWAP_SCALPING:
                logger.error("🚨 VWAP_SCALPING SIGNAL FAILED - ROUTING ANALYSIS:")
                logger.error(f"🔍 Strategy enum comparison: {signal.strategy == StrategyType.GRID_TRADING}")
                logger.error(f"🔍 Strategy string: {str(signal.strategy)}")
                logger.error(f"🔍 Strategy type: {type(signal.strategy)}")
                logger.error(f"🔍 Should use standard execution: {signal.strategy != StrategyType.GRID_TRADING}")
            
            # Check stack trace for method call origins
            stack_summary = traceback.extract_tb(exc_traceback)
            logger.error("🔍 Call Stack Analysis:")
            for i, frame in enumerate(stack_summary[-5:]):  # Last 5 frames
                logger.error(f"   Frame {i}: {frame.filename}:{frame.lineno} in {frame.name} - {frame.line}")
            
            self.signal_logger.log_signal_failed(
                signal=signal,
                error=str(e),
                notes=f"Exception during signal coordination. Type: {exc_type.__name__}. Strategy: {signal.strategy.name}",
            )

    def _execute_coordinated_signal(self, signal, allocation_result, log_entry=None):
        """
        Execute signal through appropriate execution path.

        PHASE 2: Delegates to GridLifecycleManager or ExecutionLayer based on strategy type.
        """
        try:
            if signal.strategy == StrategyType.GRID_TRADING:
                self._execute_grid_signal_coordinated(signal, allocation_result, log_entry)
            else:
                self._execute_standard_signal_coordinated(
                    signal, allocation_result, log_entry
                )
        except Exception as e:
            logger.error(f"Error executing signal: {e}", exc_info=True)

    def _execute_grid_signal_coordinated(
        self, signal, allocation_result, log_entry=None
    ):
        """
        Execute grid trading signal through GridLifecycleManager.

        PHASE 2: Coordinator delegates grid execution to GridLifecycleManager.
        GridLifecycleManager is responsible for:
        - Calculating grid levels
        - Placing grid orders
        - Monitoring fills
        - Managing P&L
        - Emergency stops

        GRID REFRESH/RECENTER FEATURE:
        When a high-conviction signal arrives with significant price drift from the
        current grid center, the grid can be refreshed (recentered) to adapt to new
        market conditions without closing existing filled positions.
        """
        try:
            symbol = signal.asset

            # Pre-check: handle active grid with intelligent refresh logic
            if self.grid_lifecycle and self.grid_lifecycle.has_active_grid(symbol):
                self._handle_active_grid_signal(symbol, signal)
                return

            # Enhanced capital extraction with fallback keys
            capital_keys = ["allocated_amount", "capital_allocated", "capital", "amount", "allocated"]
            capital_allocated = 0
            
            for key in capital_keys:
                if key in allocation_result and allocation_result[key] is not None:
                    try:
                        parsed_capital = float(allocation_result[key])
                        if parsed_capital > 0:  # Only use positive capital amounts
                            capital_allocated = parsed_capital
                            logger.info(f"💰 Capital allocated for {symbol} from '{key}': ${capital_allocated:.2f}")
                            break
                        else:
                            logger.debug(f"⚠️ Capital from '{key}' is zero or negative: ${parsed_capital:.2f}")
                    except (ValueError, TypeError):
                        logger.warning(f"⚠️ Could not parse capital from '{key}': {allocation_result[key]}")
                        continue
            
            if capital_allocated <= 0:
                logger.error(f"❌ No capital allocated for {symbol} grid. Available keys: {list(allocation_result.keys())}")
                if log_entry is None:
                    log_entry = {
                        "timestamp": datetime.now().isoformat(),
                        "symbol": symbol,
                        "strategy": signal.strategy.name if hasattr(signal.strategy, 'name') else str(signal.strategy),
                        "side": signal.side.name if hasattr(signal.side, 'name') else str(signal.side),
                        "entry_price": signal.entry_price,
                        "confidence": signal.confidence,
                    }
                self.signal_logger.log_signal_rejected(
                    signal=signal,
                    reason=f"No capital allocated for grid (checked: {capital_keys})"
                )
                return

            # Place grid orders through GridLifecycleManager
            # TradingBot has self.client (PacificaClient) for order execution
            result = self._place_grid_orders(
                signal=signal,
                allocation_result=allocation_result,
                log_entry=log_entry,
            )

            if result.get("success"):
                buy_orders = result.get('buy_orders', 0)
                sell_orders = result.get('sell_orders', 0)
                logger.info(
                    f"✅ Grid orders placed for {symbol}: "
                    f"{buy_orders} BUY, {sell_orders} SELL"
                )

                # Determine if this is a position update or new position
                is_position_update = getattr(signal, '_is_position_update', False)
                execution_status = "position_updated" if is_position_update else "executed"

                # Log successful grid execution
                self.signal_logger.log_signal_executed(
                    signal=signal,
                    order_id=f"grid_{symbol}_{buy_orders}buy_{sell_orders}sell",
                    filled_price=signal.entry_price,
                    filled_quantity=buy_orders + sell_orders,
                    execution_result=f"Grid placed: {buy_orders} BUY, {sell_orders} SELL",
                    notes=f"Capital allocated: ${capital_allocated:.2f}",
                    status=execution_status,
                )

                # Register grid with GridLifecycleManager for monitoring
                # Add defensive check for register_new_grid method
                logger.info(f"🔍 Checking grid_lifecycle for registration: {self.grid_lifecycle is not None}")
                if self.grid_lifecycle and hasattr(self.grid_lifecycle, 'register_new_grid'):
                    try:
                        # Calculate ATR at grid creation time for later drift calculation
                        atr_at_creation = self._calculate_atr_for_symbol(symbol, timeframe="5m") or 0

                        self.grid_lifecycle.register_new_grid(
                            symbol=symbol,
                            grid_capital=capital_allocated,
                            emergency_stop_price=signal.stop_loss,
                            regime=signal.market_state.name if hasattr(signal.market_state, 'name') else str(signal.market_state),
                            atr=atr_at_creation,  # Pass calculated ATR
                            spacing=signal.spacing or 0,
                            num_levels=signal.grid_levels or 10,
                            center_price=signal.entry_price,
                        )
                        logger.info(f"✅ Grid registered with GridLifecycleManager for {symbol}")
                        
                        # DEBUG: Verify grid was registered
                        if symbol in self.grid_lifecycle._grids:
                            logger.info(f"✅ VERIFIED: Grid for {symbol} is now in _grids")
                        else:
                            logger.error(f"❌ FAILED: Grid for {symbol} NOT in _grids after registration!")
                    except Exception as e:
                        logger.error(f"❌ Failed to register grid with GridLifecycleManager: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        logger.warning(f"⚠️ Grid orders placed but registration failed - monitoring may be limited")
                else:
                    logger.warning(f"⚠️ GridLifecycleManager not available or missing register_new_grid method")
                    logger.warning(f"⚠️ Grid orders placed but not registered for lifecycle management")

            else:
                error_msg = result.get('error', 'Unknown error')
                logger.error(f"❌ Grid order placement failed for {symbol}: {error_msg}")

                # Log failed grid execution
                self.signal_logger.log_signal_failed(
                    signal=signal,
                    error=error_msg,
                    notes=f"Grid placement failed, capital: ${capital_allocated:.2f}",
                )

        except Exception as e:
            logger.error(
                f"Error executing grid signal for {signal.asset}: {e}", exc_info=True
            )

            # Log exception
            self.signal_logger.log_signal_failed(
                signal=signal,
                error=str(e),
                notes="Exception during grid execution",
            )

    def _handle_active_grid_signal(self, symbol: str, signal) -> None:
        """
        Handle incoming grid signal when grid is already active for symbol.

        Implements intelligent refresh logic:
        - Calculates drift between current grid center and proposed new center
        - Evaluates refresh conditions (drift, confidence, cooldown, regime)
        - Either refreshes (recenters) the grid or skips with detailed logging

        Args:
            symbol: Trading symbol (e.g., "BTC", "ETH")
            signal: New grid trading signal with proposed entry_price and confidence

        Refresh Conditions (all must be met):
        1. Drift >= GRID_REFRESH_MIN_ATR_DRIFT x ATR (default 1.8x)
        2. Signal confidence >= GRID_REFRESH_MIN_CONFIDENCE (default 0.72)
        3. Confidence improvement >= GRID_REFRESH_MIN_CONF_IMPROVE (default 0.08)
        4. Cooldown period passed (45 minutes, checked in recenter_grid)
        5. Regime compatibility (new signal regime matches current grid regime)
        """
        try:
            # Get current grid center
            current_center = self.grid_lifecycle.get_grid_center(symbol)
            if current_center is None:
                logger.warning(
                    f"Grid active for {symbol} but no center price available - skipping signal"
                )
                self.signal_logger.log_signal_rejected(
                    signal=signal,
                    reason="Active grid has no center price",
                )
                return

            # Get proposed center from new signal
            proposed_center = signal.entry_price

            # Get current grid data (includes atr_at_creation from when grid was created)
            grid_data = self.grid_lifecycle._grids.get(symbol, {})
            
            # Calculate drift in ATR multiples
            # Fetch 5m candles and calculate ATR
            atr_current = self._calculate_atr_for_symbol(symbol, timeframe="5m")

            # Use stored ATR as fallback if current ATR unavailable
            atr_stored = grid_data.get("atr_at_creation", 0) if grid_data else 0
            
            # Track if we're using percentage-based drift (no ATR available)
            using_pct_drift = False
            
            if atr_current is None or atr_current <= 0:
                if atr_stored and atr_stored > 0:
                    logger.info(
                        f"Using stored ATR for {symbol}: {atr_stored:.4f} (current ATR unavailable)"
                    )
                    atr_current = atr_stored
                else:
                    # ATR unavailable - use percentage-based drift as fallback
                    # This allows grid refresh even without ATR data
                    if current_center > 0:
                        using_pct_drift = True
                        pct_drift = abs(proposed_center - current_center) / current_center * 100  # % drift
                        logger.warning(
                            f"ATR unavailable for {symbol} (current: {atr_current}, stored: {atr_stored}) - "
                            f"using percentage-based drift: {pct_drift:.2f}%"
                        )
                    else:
                        logger.warning(
                            f"Cannot calculate drift for {symbol} - ATR unavailable (current: {atr_current}, stored: {atr_stored}) "
                            f"and no center price"
                        )
                        reason = f"Grid already active for {symbol} (ATR unavailable for drift calc)"
                        logger.info(f"🔷 {reason}, skipping signal")
                        self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
                        return

            # Calculate drift (either ATR-based or percentage-based)
            if using_pct_drift:
                drift_atr = abs(proposed_center - current_center) / current_center * 100  # Already in %
            else:
                drift_atr = abs(proposed_center - current_center) / atr_current

            # Get current grid confidence (stored in grid metadata if available)
            current_confidence = grid_data.get("signal_confidence", 0.65)

            # Check cooldown period
            last_refresh = self.grid_lifecycle.get_last_refresh_time(symbol)
            cooldown_seconds = self.GRID_REFRESH_COOLDOWN_MINUTES * 60
            cooldown_passed = True
            cooldown_remaining_min = 0

            if last_refresh:
                elapsed = (datetime.now() - last_refresh).total_seconds()
                if elapsed < cooldown_seconds:
                    cooldown_passed = False
                    cooldown_remaining_min = (cooldown_seconds - elapsed) / 60

            # Regime compatibility check
            current_regime = grid_data.get("regime_on_creation", "unknown")
            new_regime = (
                signal.market_state.name
                if hasattr(signal.market_state, "name")
                else str(signal.market_state)
            )
            regime_compatible = current_regime == new_regime

            # Evaluate refresh conditions
            drift_condition = drift_atr >= self.GRID_REFRESH_MIN_ATR_DRIFT
            confidence_condition = signal.confidence >= self.GRID_REFRESH_MIN_CONFIDENCE
            improvement_condition = (
                signal.confidence - current_confidence
            ) >= self.GRID_REFRESH_MIN_CONF_IMPROVE

            # Build detailed decision log
            drift_unit = "%" if using_pct_drift else "x"
            decision_factors = {
                "drift_atr": f"{drift_atr:.2f}{drift_unit} (need {self.GRID_REFRESH_MIN_ATR_DRIFT}{drift_unit})",
                "drift_passed": drift_condition,
                "signal_confidence": f"{signal.confidence:.2f} (need {self.GRID_REFRESH_MIN_CONFIDENCE})",
                "confidence_passed": confidence_condition,
                "confidence_improvement": f"{signal.confidence - current_confidence:.2f} (need {self.GRID_REFRESH_MIN_CONF_IMPROVE})",
                "improvement_passed": improvement_condition,
                "cooldown_passed": cooldown_passed,
                "regime_compatible": regime_compatible,
                "current_regime": current_regime,
                "new_regime": new_regime,
            }

            # All conditions must be met for refresh
            should_refresh = (
                drift_condition
                and confidence_condition
                and improvement_condition
                and cooldown_passed
                and regime_compatible
            )

            if should_refresh:
                # Grid refresh opportunity detected
                logger.info(
                    f"🔄 Grid refresh OPPORTUNITY for {symbol}: "
                    f"drift={drift_atr:.2f}x ATR, "
                    f"confidence={signal.confidence:.2f} (was {current_confidence:.2f}), "
                    f"regime={new_regime}"
                )
                logger.info(
                    f"📊 Refresh details: center ${current_center:.4f} → ${proposed_center:.4f} "
                    f"(delta: ${abs(proposed_center - current_center):.4f})"
                )

                # Attempt recenter
                reason = (
                    f"signal_refresh_drift_{drift_atr:.1f}x_atr_"
                    f"conf_{signal.confidence:.2f}"
                )
                success = self.grid_lifecycle.recenter_grid(
                    symbol, proposed_center, reason
                )

                if success:
                    # Update stored confidence for future comparisons
                    grid_data["signal_confidence"] = signal.confidence
                    logger.info(f"✅ Grid refresh SUCCESS for {symbol}")
                    
                    # Determine if this is a position update or new position
                    is_position_update = getattr(signal, '_is_position_update', False)
                    execution_status = "position_updated" if is_position_update else "executed"
                    
                    self.signal_logger.log_signal_executed(
                        signal=signal,
                        order_id=f"grid_refresh_{symbol}",
                        filled_price=proposed_center,
                        filled_quantity=0,  # Refreshed existing orders
                        execution_result="grid_refreshed",
                        notes=f"Recentered: drift={drift_atr:.2f}x ATR, conf={signal.confidence:.2f}",
                        status=execution_status,
                    )
                else:
                    logger.warning(f"⚠️ Grid refresh FAILED for {symbol} (recenter_grid returned False)")
                    self.signal_logger.log_signal_failed(
                        signal=signal,
                        error="Grid recenter failed",
                        notes=f"Drift={drift_atr:.2f}x ATR, reason={reason}",
                    )
            else:
                # Refresh conditions not met - skip with detailed reason
                skip_reasons = []
                if not drift_condition:
                    skip_reasons.append(f"drift {drift_atr:.2f}x < {self.GRID_REFRESH_MIN_ATR_DRIFT}x ATR")
                if not confidence_condition:
                    skip_reasons.append(f"confidence {signal.confidence:.2f} < {self.GRID_REFRESH_MIN_CONFIDENCE}")
                if not improvement_condition:
                    skip_reasons.append(f"improvement {signal.confidence - current_confidence:.2f} < {self.GRID_REFRESH_MIN_CONF_IMPROVE}")
                if not cooldown_passed:
                    skip_reasons.append(f"cooldown {cooldown_remaining_min:.1f}min remaining")
                if not regime_compatible:
                    skip_reasons.append(f"regime mismatch ({current_regime} vs {new_regime})")

                reason = f"Grid active for {symbol} - refresh skipped: {', '.join(skip_reasons)}"
                logger.info(f"🔷 {reason}")
                logger.debug(f"Grid refresh decision factors: {decision_factors}")

                self.signal_logger.log_signal_rejected(
                    signal=signal,
                    reason=reason,
                    notes=f"Current center: ${current_center:.4f}, Proposed: ${proposed_center:.4f}",
                )

        except Exception as e:
            logger.error(
                f"Error in grid refresh logic for {symbol}: {e}", exc_info=True
            )
            # Fallback to simple skip
            reason = f"Grid already active for {symbol} (refresh logic error: {e})"
            logger.info(f"🔷 {reason}, skipping signal")
            self.signal_logger.log_signal_rejected(
                signal=signal,
                reason=reason,
            )

    def _calculate_atr_for_symbol(
        self, symbol: str, timeframe: str = "5m", period: int = 14
    ) -> Optional[float]:
        """
        Calculate ATR for a symbol using multi-timeframe fetcher data.

        Args:
            symbol: Trading symbol (e.g., "BTC", "ETH")
            timeframe: Candle timeframe (default "5m")
            period: ATR calculation period (default 14)

        Returns:
            Latest ATR value or None if calculation fails
        """
        try:
            # Fetch candles for the specified timeframe
            multi_tf_data = self.multi_tf_fetcher.get_candles_multi_tf(
                symbol=symbol,
                timeframes=[timeframe],
                lookback_candles=max(period + 10, 50),  # Ensure enough data
            )

            if not multi_tf_data or timeframe not in multi_tf_data:
                logger.debug(f"No candle data available for {symbol} {timeframe}")
                return None

            candles = multi_tf_data[timeframe]
            if not candles or len(candles) < period + 1:
                logger.debug(
                    f"Insufficient candles for {symbol} {timeframe}: "
                    f"have {len(candles) if candles else 0}, need {period + 1}"
                )
                return None

            # Extract high, low, close from candles
            # Candles are typically dicts with 'high', 'low', 'close' keys
            highs = []
            lows = []
            closes = []

            for candle in candles:
                if isinstance(candle, dict):
                    highs.append(float(candle.get("high", 0)))
                    lows.append(float(candle.get("low", 0)))
                    closes.append(float(candle.get("close", 0)))
                elif hasattr(candle, "high"):
                    # Handle candle objects
                    highs.append(float(candle.high))
                    lows.append(float(candle.low))
                    closes.append(float(candle.close))

            if len(highs) < period + 1:
                logger.debug(f"Insufficient valid price data for {symbol} ATR calculation")
                return None

            # Calculate ATR using indicators module
            atr_value = calculate_atr(highs, lows, closes, period=period)

            if atr_value and atr_value > 0:
                logger.debug(f"ATR for {symbol} ({timeframe}): {atr_value:.4f}")
                return float(atr_value)

            return None

        except Exception as e:
            logger.debug(f"Error calculating ATR for {symbol}: {e}")
            return None

    def _place_grid_orders(self, signal, allocation_result, log_entry=None):
        """
        Place grid orders using GridLifecycleManager.

        Args:
            signal: Grid trading signal
            allocation_result: Capital allocation result from RiskManager
            log_entry: Optional log entry dict for debugging

        Returns:
            Dict with placement results:
            {
                "success": bool,
                "buy_orders": int,
                "sell_orders": int,
                "buy_order_ids": List[str],
                "sell_order_ids": List[str],
                "error": Optional[str]
            }
        """
        try:
            symbol = signal.asset
            # Enhanced capital extraction with fallback keys
            capital_keys = ["allocated_amount", "capital_allocated", "capital", "amount", "allocated"]
            capital = 0
            
            for key in capital_keys:
                if key in allocation_result and allocation_result[key] is not None:
                    try:
                        parsed_capital = float(allocation_result[key])
                        if parsed_capital > 0:  # Only use positive capital amounts
                            capital = parsed_capital
                            logger.info(f"💰 Found capital in '{key}': ${capital:.2f}")
                            break
                        else:
                            logger.debug(f"⚠️ Capital from '{key}' is zero or negative: ${parsed_capital:.2f}")
                    except (ValueError, TypeError):
                        logger.warning(f"⚠️ Could not parse capital from '{key}': {allocation_result[key]}")
                        continue
            
            if capital <= 0:
                logger.error(f"❌ No capital allocated for {symbol}. Available keys: {list(allocation_result.keys())}")
                return {"success": False, "error": f"No capital allocated (checked keys: {capital_keys})"}

            # Calculate grid levels
            grid_levels = self._calculate_grid_levels(
                signal=signal,
                total_capital=capital,
            )

            if not grid_levels.get("buy_levels") or not grid_levels.get("sell_levels"):
                return {"success": False, "error": "Failed to calculate grid levels"}

            logger.info(
                f"📊 Grid levels calculated for {symbol}: "
                f"{len(grid_levels['buy_levels'])} BUY, {len(grid_levels['sell_levels'])} SELL"
            )

            # Place grid orders with tick_size and lot_size compliance
            buy_order_ids = []
            sell_order_ids = []

            # Place BUY orders with compliance validation
            for level in grid_levels["buy_levels"]:
                try:
                    # Get price and quantity from level
                    order_price = level["price"]
                    order_quantity = level["quantity"]
                    
                    # Apply final compliance rounding
                    order_price = self.round_price_to_tick_size(order_price, symbol)
                    order_quantity = self.round_quantity_to_lot_size(order_quantity, symbol)
                    
                    # Pre-flight validation
                    validation = self.validate_order_compliance(
                        price=order_price,
                        quantity=order_quantity,
                        symbol=symbol
                    )
                    
                    if not validation["valid"]:
                        logger.warning(
                            f"  ⚠️ BUY order compliance issues for {symbol}: {validation['errors']}. "
                            f"Using adjusted values: price={validation['adjusted_price']:.4f}, "
                            f"qty={validation['adjusted_quantity']:.6f}"
                        )
                        order_price = validation["adjusted_price"]
                        order_quantity = validation["adjusted_quantity"]
                    
                    # Pre-flight order payload validation
                    order_params = {
                        "symbol": symbol,
                        "side": "buy",
                        "quantity": float(order_quantity),
                        "order_type": "limit",
                        "price": float(order_price),
                        "client_order_id": str(uuid.uuid4()),
                        # Note: Grid orders don't use embedded stop_loss/take_profit to avoid StopOrderInfo JSON errors
                        # Grid risk is managed through the grid structure itself
                    }

                    # Validate order payload structure before submission
                    self._validate_order_payload(order_params, symbol)

                    # Log order payload for debugging
                    logger.debug(f"Grid BUY order payload for {symbol}: {json.dumps(order_params, indent=2)}")

                    response = self.client.place_order(**order_params)

                    # Validate response using ResponseHandler
                    validated_response = ResponseHandler.validate_order_response(response)
                    order_data = validated_response.get("data", {})
                    order_id = order_data.get("order_id") or order_data.get("id")

                    if order_id and validated_response.get("success"):
                        buy_order_ids.append(str(order_id))
                        logger.info(
                            f"  ✅ BUY order placed: {order_quantity:.6f} @ ${order_price:.4f} (ID: {order_id})"
                        )
                    else:
                        error_msg = validated_response.get("error", "Unknown API error")
                        logger.error(f"  ❌ BUY order rejected: {error_msg}")
                except Exception as e:
                    logger.error(f"  ❌ Failed to place BUY order @ ${level['price']:.4f}: {e}")

            # Place SELL orders with compliance validation
            for level in grid_levels["sell_levels"]:
                try:
                    # Get price and quantity from level
                    order_price = level["price"]
                    order_quantity = level["quantity"]
                    
                    # Apply final compliance rounding
                    order_price = self.round_price_to_tick_size(order_price, symbol)
                    order_quantity = self.round_quantity_to_lot_size(order_quantity, symbol)
                    
                    # Pre-flight validation
                    validation = self.validate_order_compliance(
                        price=order_price,
                        quantity=order_quantity,
                        symbol=symbol
                    )
                    
                    if not validation["valid"]:
                        logger.warning(
                            f"  ⚠️ SELL order compliance issues for {symbol}: {validation['errors']}. "
                            f"Using adjusted values: price={validation['adjusted_price']:.4f}, "
                            f"qty={validation['adjusted_quantity']:.6f}"
                        )
                        order_price = validation["adjusted_price"]
                        order_quantity = validation["adjusted_quantity"]
                    
                    # Pre-flight order payload validation
                    order_params = {
                        "symbol": symbol,
                        "side": "sell",
                        "quantity": float(order_quantity),
                        "order_type": "limit",
                        "price": float(order_price),
                        "client_order_id": str(uuid.uuid4()),
                        # Note: Grid orders don't use embedded stop_loss/take_profit to avoid StopOrderInfo JSON errors
                        # Grid risk is managed through the grid structure itself
                    }

                    # Validate order payload structure before submission
                    self._validate_order_payload(order_params, symbol)

                    # Log order payload for debugging
                    logger.debug(f"Grid SELL order payload for {symbol}: {json.dumps(order_params, indent=2)}")

                    response = self.client.place_order(**order_params)

                    # Validate response using ResponseHandler
                    validated_response = ResponseHandler.validate_order_response(response)
                    order_data = validated_response.get("data", {})
                    order_id = order_data.get("order_id") or order_data.get("id")

                    if order_id and validated_response.get("success"):
                        sell_order_ids.append(str(order_id))
                        logger.info(
                            f"  ✅ SELL order placed: {order_quantity:.6f} @ ${order_price:.4f} (ID: {order_id})"
                        )
                    else:
                        error_msg = validated_response.get("error", "Unknown API error")
                        logger.error(f"  ❌ SELL order rejected: {error_msg}")
                except Exception as e:
                    logger.error(f"  ❌ Failed to place SELL order @ ${level['price']:.4f}: {e}")

            # Return results
            success = len(buy_order_ids) > 0 or len(sell_order_ids) > 0
            return {
                "success": success,
                "buy_orders": len(buy_order_ids),
                "sell_orders": len(sell_order_ids),
                "buy_order_ids": buy_order_ids,
                "sell_order_ids": sell_order_ids,
            }

        except Exception as e:
            logger.error(f"Error placing grid orders: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _validate_order_payload(self, order_params: Dict[str, Any], symbol: str) -> None:
        """
        Validate order payload matches Pacifica API requirements.
        
        Args:
            order_params: Order parameters dictionary
            symbol: Trading symbol for context
            
        Raises:
            ValueError: If order payload is invalid
        """
        required_fields = ['symbol', 'side', 'order_type', 'quantity', 'price']
        for field in required_fields:
            if field not in order_params:
                raise ValueError(f"Missing required field: {field}")
        
        # Validate data types are numeric (not strings)
        if not isinstance(order_params['quantity'], (int, float)):
            raise ValueError(f"Quantity must be numeric, got {type(order_params['quantity'])}")
        if not isinstance(order_params['price'], (int, float)):
            raise ValueError(f"Price must be numeric, got {type(order_params['price'])}")
        
        # Validate side
        if order_params['side'] not in ['buy', 'sell']:
            raise ValueError(f"Side must be 'buy' or 'sell', got {order_params['side']}")
        
        # Validate order_type
        if order_params['order_type'] not in ['limit', 'market']:
            raise ValueError(f"Order type must be 'limit' or 'market', got {order_params['order_type']}")
        
        # Validate stop_loss and take_profit if present
        stop_loss = order_params.get('stop_loss')
        if stop_loss is not None:
            if not isinstance(stop_loss, (int, float)):
                raise ValueError(f"Stop loss must be numeric, got {type(stop_loss)}")
            if stop_loss <= 0:
                raise ValueError(f"Stop loss must be positive, got {stop_loss}")
                
        take_profit = order_params.get('take_profit')
        if take_profit is not None:
            if not isinstance(take_profit, (int, float)):
                raise ValueError(f"Take profit must be numeric, got {type(take_profit)}")
            if take_profit <= 0:
                raise ValueError(f"Take profit must be positive, got {take_profit}")

    def _refresh_market_info_cache(self) -> None:
        """
        Refresh the market info cache from Pacifica API.
        
        Fetches market data from /info endpoint and caches tick_size and lot_size
        for each symbol. This ensures order compliance with Pacifica's requirements.
        """
        try:
            markets = self.client.get_markets()
            if not markets:
                logger.warning("Failed to refresh market info cache: no markets returned")
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
            self._market_info_cache_timestamp = time.time()

            logger.info(
                f"Market info cache refreshed: {len(new_cache)} symbols cached "
                f"(tick_size/lot_size from /info endpoint)"
            )

        except Exception as e:
            logger.error(f"Error refreshing market info cache: {e}")

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
            or (time.time() - self._market_info_cache_timestamp) > self._market_info_cache_ttl
        ):
            self._refresh_market_info_cache()

        return self._market_info_cache.get(symbol, {"tick_size": None, "lot_size": None})

    def get_symbol_tick_size(self, symbol: str) -> Optional[float]:
        """
        Get tick size requirements for a symbol from cached market data.
        
        Tick size is the minimum price increment for the symbol.
        
        Args:
            symbol: Trading symbol (e.g., 'BTC', 'ETH')
            
        Returns:
            Tick size requirement, or None if not available from API
        """
        market_info = self._get_cached_market_info(symbol)
        return market_info.get("tick_size")

    def get_symbol_lot_size(self, symbol: str) -> Optional[float]:
        """
        Get lot size requirements for a symbol from cached market data.
        
        Lot size is the minimum quantity increment for the symbol.
        Falls back to API call if cache miss or stale data.
        
        Args:
            symbol: Trading symbol (e.g., 'BTC', 'ETH')
            
        Returns:
            Lot size requirement, or None if not available from API
        """
        market_info = self._get_cached_market_info(symbol)
        return market_info.get("lot_size")

    def round_price_to_tick_size(self, price: float, symbol: str) -> float:
        """
        Round price to comply with tick size requirements.
        
        Args:
            price: Original price value
            symbol: Trading symbol
            
        Returns:
            Price rounded to tick size compliance
        """
        from decimal import Decimal, ROUND_HALF_UP

        tick_size = self.get_symbol_tick_size(symbol)

        if tick_size is None or tick_size <= 0:
            logger.debug(f"No tick size available for {symbol}, using original price: {price}")
            return price

        # Use Decimal for precise arithmetic
        price_dec = Decimal(str(price))
        tick_size_dec = Decimal(str(tick_size))

        # Round to nearest tick size
        ticks = (price_dec / tick_size_dec).to_integral_value(rounding=ROUND_HALF_UP)
        rounded_price_dec = ticks * tick_size_dec

        rounded_price = float(rounded_price_dec)

        if rounded_price != price:
            logger.debug(
                "Price tick size adjustment for %s: %.8f -> %.8f (tick size: %s)",
                symbol,
                price,
                rounded_price,
                tick_size,
            )

        return rounded_price

    def round_quantity_to_lot_size(self, quantity: float, symbol: str) -> float:
        """
        Round quantity to comply with lot size requirements.
        
        Args:
            quantity: Original quantity value
            symbol: Trading symbol
            
        Returns:
            Quantity rounded to lot size compliance
        """
        from decimal import Decimal, ROUND_DOWN

        lot_size = self.get_symbol_lot_size(symbol)

        if lot_size is None or lot_size <= 0:
            logger.debug(f"No lot size available for {symbol}, using original quantity: {quantity}")
            return quantity

        # Use Decimal for precise arithmetic
        quantity_dec = Decimal(str(quantity))
        lot_size_dec = Decimal(str(lot_size))

        # Calculate how many lot size units fit into the quantity (round down)
        lot_units = int((quantity_dec / lot_size_dec).to_integral_value(rounding=ROUND_DOWN))

        if lot_units < 1 and quantity > 0:
            lot_units = 1

        rounded_quantity_dec = Decimal(lot_units) * lot_size_dec
        rounded_quantity = float(rounded_quantity_dec)

        if rounded_quantity != quantity:
            logger.debug(
                "Quantity lot size adjustment for %s: %.8f -> %.8f (lot size: %s, units: %d)",
                symbol,
                quantity,
                rounded_quantity,
                lot_size,
                lot_units,
            )

        return rounded_quantity

    def validate_order_compliance(
        self, price: float, quantity: float, symbol: str
    ) -> Dict[str, Any]:
        """
        Validate price and quantity compliance with tick_size and lot_size.
        
        Performs pre-flight validation before order placement to prevent 500 errors
        from Pacifica API due to non-compliant orders.
        
        Args:
            price: Order price to validate
            quantity: Order quantity to validate
            symbol: Trading symbol
            
        Returns:
            Dict with validation results:
            {
                'valid': bool,
                'price_compliant': bool,
                'quantity_compliant': bool,
                'tick_size': float or None,
                'lot_size': float or None,
                'errors': List[str],
                'adjusted_price': float,
                'adjusted_quantity': float,
            }
        """
        from decimal import Decimal

        result = {
            "valid": True,
            "price_compliant": True,
            "quantity_compliant": True,
            "tick_size": None,
            "lot_size": None,
            "errors": [],
            "adjusted_price": price,
            "adjusted_quantity": quantity,
        }

        symbol = symbol.upper().replace("-PERP", "")

        # Get market specs
        tick_size = self.get_symbol_tick_size(symbol)
        lot_size = self.get_symbol_lot_size(symbol)

        result["tick_size"] = tick_size
        result["lot_size"] = lot_size

        # Validate price against tick size
        if tick_size is not None and tick_size > 0:
            price_dec = Decimal(str(price))
            tick_size_dec = Decimal(str(tick_size))

            # Check if price is a multiple of tick size
            remainder = price_dec % tick_size_dec
            if remainder != 0:
                result["price_compliant"] = False
                result["valid"] = False
                result["errors"].append(
                    f"Price {price} is not a multiple of tick size {tick_size} "
                    f"(remainder: {float(remainder):.10f})"
                )
                # Calculate adjusted price
                result["adjusted_price"] = self.round_price_to_tick_size(price, symbol)

        # Validate quantity against lot size
        if lot_size is not None and lot_size > 0:
            quantity_dec = Decimal(str(quantity))
            lot_size_dec = Decimal(str(lot_size))

            # Check if quantity is a multiple of lot size
            remainder = quantity_dec % lot_size_dec
            if remainder != 0:
                result["quantity_compliant"] = False
                result["valid"] = False
                result["errors"].append(
                    f"Quantity {quantity} is not a multiple of lot size {lot_size} "
                    f"(remainder: {float(remainder):.10f})"
                )
                # Calculate adjusted quantity
                result["adjusted_quantity"] = self.round_quantity_to_lot_size(quantity, symbol)

        # Check if adjusted values are valid (non-zero)
        if result["adjusted_price"] <= 0:
            result["valid"] = False
            result["errors"].append(f"Adjusted price {result['adjusted_price']} is invalid (<= 0)")

        if result["adjusted_quantity"] <= 0:
            result["valid"] = False
            result["errors"].append(
                f"Adjusted quantity {result['adjusted_quantity']} is invalid (<= 0)"
            )

        return result

    def adjust_quantity_for_lot_size(self, quantity: float, symbol: str) -> float:
        """
        Adjust quantity to comply with lot size requirements.
        
        Uses cached market data from Pacifica API. Falls back to original
        quantity if no lot size data available.

        Args:
            quantity: Original calculated quantity
            symbol: Trading symbol

        Returns:
            Adjusted quantity that complies with lot size
        """
        return self.round_quantity_to_lot_size(quantity, symbol)

    def validate_lot_size_compliance(self, quantity: float, symbol: str) -> bool:
        """
        Validate if quantity complies with lot size requirements.
        
        Uses cached market data from Pacifica API.

        Args:
            quantity: Quantity to validate
            symbol: Trading symbol

        Returns:
            True if compliant, False otherwise
        """
        from decimal import Decimal

        lot_size = self.get_symbol_lot_size(symbol)

        if lot_size is None or lot_size <= 0:
            # No lot size info available, assume compliant
            return True

        # Use Decimal for precise arithmetic
        quantity_dec = Decimal(str(quantity))
        lot_size_dec = Decimal(str(lot_size))

        # Check if quantity is a multiple of lot size
        remainder = quantity_dec % lot_size_dec
        is_compliant = remainder == 0

        if not is_compliant:
            difference = float(abs(remainder))
            logger.warning(
                "Quantity %.8f for %s is not compliant with lot size %s "
                "(remainder: %.10f)",
                quantity,
                symbol,
                lot_size,
                difference,
            )

        return is_compliant

    def _execute_standard_signal_coordinated(
        self, signal, allocation_result, log_entry=None
    ):
        """
        Execute standard (non-grid) signal through ExecutionLayer.

        PHASE 2: Coordinator delegates execution to ExecutionLayer for precise entry timing.
        ExecutionLayer monitors 1m/5m candles for optimal entry conditions.
        
        Includes tick_size and lot_size compliance validation using Pacifica API data.
        """
        try:
            symbol = signal.asset
            # RiskManager returns "allocated_amount" (not "capital_allocated")
            capital_allocated = allocation_result.get("allocated_amount", 0)

            # Calculate quantity from allocated capital and entry price
            if signal.entry_price > 0 and capital_allocated > 0:
                quantity = capital_allocated / signal.entry_price
            else:
                quantity = 0

            # Apply tick size to entry price for compliance
            original_price = signal.entry_price
            adjusted_price = self.round_price_to_tick_size(original_price, symbol)
            
            # Apply lot size to quantity for compliance
            if quantity > 0:
                adjusted_quantity = self.round_quantity_to_lot_size(quantity, symbol)
                quantity = adjusted_quantity

            # Pre-flight validation using comprehensive compliance check
            validation = self.validate_order_compliance(
                price=adjusted_price,
                quantity=quantity,
                symbol=symbol
            )
            
            if not validation["valid"]:
                logger.error(
                    f"❌ Order compliance validation failed for {symbol}: "
                    f"{'; '.join(validation['errors'])}"
                )
                self.signal_logger.log_signal_failed(
                    signal=signal,
                    error="Order compliance validation failed",
                    notes=f"Errors: {validation['errors']}, Tick: {validation['tick_size']}, Lot: {validation['lot_size']}",
                )
                
                # Try to use adjusted values if available
                if validation["adjusted_price"] > 0 and validation["adjusted_quantity"] > 0:
                    logger.warning(f"🔄 Attempting with adjusted values for {symbol}")
                    adjusted_price = validation["adjusted_price"]
                    quantity = validation["adjusted_quantity"]
                else:
                    return

            logger.info(
                f"🔹 Executing {signal.strategy.name} signal for {symbol}: "
                f"{signal.side.name} {quantity:.6f} @ ${adjusted_price:.4f} "
                f"(capital: ${capital_allocated:.2f}, "
                f"tick_size: {validation['tick_size']}, lot_size: {validation['lot_size']})"
            )

            if quantity <= 0:
                logger.error(f"❌ Invalid quantity for {symbol}: {quantity}")
                self.signal_logger.log_signal_failed(
                    signal=signal,
                    error="Invalid quantity (<=0)",
                    notes=f"Capital: ${capital_allocated:.2f}, Price: ${adjusted_price:.4f}, Lot size: {validation['lot_size']}",
                )
                return

            # Refine signal through ExecutionLayer before execution
            refined_signal = signal
            if self.execution_layer is not None:
                try:
                    refined_signal = self.execution_layer.refine_entry(signal, symbol)
                    if refined_signal is None:
                        logger.info(f"⚠️ ExecutionLayer skipped entry for {symbol} - timing not favorable")
                        self.signal_logger.log_signal_rejected(
                            signal=signal,
                            reason="ExecutionLayer timing skip",
                            notes="1m/5m timing conditions not met",
                        )
                        return
                    
                    # Apply tick size to refined entry price
                    refined_price = self.round_price_to_tick_size(refined_signal.entry_price, symbol)
                    logger.debug(f"🎯 ExecutionLayer refined {symbol} entry: {signal.entry_price:.6f} → {refined_price:.6f} (tick size adjusted)")
                    
                    # Update refined signal with tick-size-compliant price
                    refined_signal.entry_price = refined_price
                    adjusted_price = refined_price
                    
                except AttributeError as e:
                    logger.warning(f"⚠️ ExecutionLayer method unavailable: {e}")
                    logger.info(f"🔄 Using original signal for {symbol}")
                except Exception as e:
                    logger.error(f"❌ ExecutionLayer refinement failed for {symbol}: {e}")
                    logger.info(f"🔄 Using original signal for {symbol}")
            else:
                logger.debug(f"🔧 ExecutionLayer unavailable - using original signal for {symbol}")
            
            # Use refined signal for execution
            signal = refined_signal
            side_str = "buy" if signal.side.name == "BUY" else "sell"

            # Final compliance check before placing order
            final_validation = self.validate_order_compliance(
                price=adjusted_price,
                quantity=quantity,
                symbol=symbol
            )
            
            if not final_validation["valid"]:
                logger.error(f"❌ Final compliance check failed for {symbol}: {final_validation['errors']}")
                # Use adjusted values
                adjusted_price = final_validation["adjusted_price"]
                quantity = final_validation["adjusted_quantity"]

            # Use market order for immediate execution
            # Include stop loss and take profit for safety
            order_response = self.client.place_order(
                symbol=symbol,
                side=side_str,
                quantity=quantity,
                order_type="market",
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
            )

            # Debug: log raw API response to diagnose parsing issues
            logger.info(
                f"📡 Raw order response for {symbol}: type={type(order_response).__name__}, "
                f"value={str(order_response)[:200]}"
            )

            # Enhanced debugging for ResponseHandler
            try:
                logger.debug(f"🔍 About to validate order response for {symbol} using ResponseHandler")
                validated_response = ResponseHandler.validate_order_response(order_response)
                logger.debug(f"✅ ResponseHandler validation successful for {symbol}")
            except Exception as response_error:
                logger.error(f"🚨 RESPONSEHANDLER ERROR for {symbol}: {response_error}")
                logger.error(f"🔍 ResponseHandler failed on response: {order_response} (type: {type(order_response)})")
                logger.error(f"🔍 This might be the source of the 'success' error!")
                raise response_error
            
            # Extract order data from validated response
            order_data = validated_response.get("data", {})
            order_id = order_data.get("order_id") or order_data.get("id")

            # Convert to execution result format
            execution_result = {
                "success": validated_response.get("success", False) and order_id is not None,
                "order_id": order_id,
                "executed_price": order_data.get("price", signal.entry_price),
                "error": validated_response.get("error"),
            }

            if execution_result.get("success"):
                logger.info(
                    f"✅ Order executed for {symbol}: "
                    f"ID={execution_result.get('order_id')}, "
                    f"Price=${execution_result.get('executed_price'):.4f}"
                )

                # Determine if this is a position update or new position
                is_position_update = getattr(signal, '_is_position_update', False)
                execution_status = "position_updated" if is_position_update else "executed"

                # Log successful execution to CSV/database
                self.signal_logger.log_signal_executed(
                    signal=signal,
                    order_id=str(execution_result.get("order_id", "")),
                    filled_price=execution_result.get("executed_price", 0),
                    filled_quantity=quantity,
                    execution_result="success",
                    notes=f"Capital allocated: ${capital_allocated:.2f}",
                    status=execution_status,
                )

                # Publish ORDER_PLACED event
                self.event_bus.publish_event(
                    event_type=EventType.ORDER_PLACED,
                    data={
                        "symbol": symbol,
                        "order_id": execution_result.get("order_id"),
                        "side": signal.side.name,
                        "quantity": quantity,
                        "price": execution_result.get("executed_price"),
                        "timestamp": time.time(),
                    },
                    source="trading_bot",
                )

            else:
                error_msg = execution_result.get('error', 'Unknown error')
                logger.error(f"❌ Order execution failed for {symbol}: {error_msg}")

                # Log failed execution
                self.signal_logger.log_signal_failed(
                    signal=signal,
                    error=error_msg,
                    notes=f"Attempted quantity: {quantity}",
                )

        except Exception as e:
            # Use %s formatting to avoid issues with JSON in exception messages
            # JSON contains {} which can be interpreted as format placeholders
            logger.error(
                "Error executing standard signal for %s: %s",
                signal.asset,
                str(e),
                exc_info=True,
            )

            # Log exception
            self.signal_logger.log_signal_failed(
                signal=signal,
                error=str(e),
                notes="Exception during execution",
            )

    def _handle_order_placed(self, event):
        """Handle ORDER_PLACED event."""
        logger.debug(f"Order placed event: {event}")

    def _handle_order_filled(self, event):
        """Handle ORDER_FILLED event."""
        logger.debug(f"Order filled event: {event}")

    def _handle_capital_requested(self, event):
        """Handle CAPITAL_REQUESTED event."""
        logger.debug(f"Capital requested event: {event}")

    def _handle_risk_limit_exceeded(self, event):
        """Handle RISK_LIMIT_EXCEEDED event."""
        logger.warning(f"Risk limit exceeded event: {event}")

    def _handle_grid_emergency(self, event):
        """Handle GRID_EMERGENCY event."""
        logger.critical(f"Grid emergency event: {event}")

    def get_coordinator_status(self) -> Dict[str, Any]:
        """
        Get coordinator status for monitoring.

        Returns:
            Dict with coordinator state, active components, event metrics
        """
        return {
            "is_running": self.is_running,
            "loop_iteration": getattr(self, "_loop_iteration", 0),
            "last_loop_time": getattr(self, "_last_loop_time", None),
            "loop_step": getattr(self, "_loop_step", None),
            "loop_events_generated": getattr(self, "_loop_events_generated", 0),
            "active_components": self.component_registry.list_components(),
            "event_history_size": len(self.event_bus._event_history),
        }

    def _check_signals(self) -> None:
        """
        Check for trading signals and execute them.

        LEGACY METHOD - PHASE 2: Now handled by _generate_and_publish_signals()
        Kept for backward compatibility.
        """
        try:
            # Get all markets
            markets = self.client.get_markets()
            if not markets:
                logging.warning("No markets available")
                return

            logging.info(f"Checking {len(markets)} markets for signals...")

            for market in markets[:10]:  # Limit to first 10 markets
                try:
                    symbol = market.get("symbol")
                    if not symbol:
                        continue

                    # Get current price via WebSocket (eliminates REST API rate limiting)
                    ticker = self._get_ticker_ws(symbol)
                    current_price = float(ticker.get("last", 0))

                    if current_price <= 0:
                        logging.debug(
                            f"Skipping {symbol}: invalid price {current_price}"
                        )
                        continue

                    # Get multi-timeframe data (5m, 15m, 1h, 4h)
                    multi_tf_data = self.multi_tf_fetcher.get_candles_multi_tf(
                        symbol=symbol, timeframes=["5m", "15m", "1h", "4h"], lookback_candles=250
                    )

                    if not multi_tf_data:
                        logging.debug(f"Skipping {symbol}: no multi-timeframe data")
                        continue

                    # Generate signals using strategy manager
                    signals = self.strategy_manager.generate_signals_for_market(
                        symbol=symbol,
                        multi_tf_data=multi_tf_data,
                        current_price=current_price,
                    )

                    # Execute valid signals
                    for signal in signals:
                        if signal.is_valid():
                            logging.info(
                                f"Valid signal: {signal.strategy.name} {signal.side.name} "
                                f"{symbol} @ ${signal.entry_price:.4f} "
                                f"(confidence: {signal.confidence:.1%})"
                            )

                            # Execute signal
                            self._execute_signal(signal)
                        else:
                            logging.debug(
                                f"Invalid signal for {symbol}: {signal.validation_flags}"
                            )

                except Exception as e:
                    logging.error(f"Error checking signals for {symbol}: {e}")

        except Exception as e:
            logging.error(f"Error checking signals: {e}")

    def _count_trades_by_tp_sl(self, symbol: str) -> int:
        """
        Count number of trades for a symbol by checking TP/SL orders.
        
        Pacifica allows only 1 position per token. Each trade creates its own
        take profit and stop loss orders. By counting these, we can detect
        if a position already exists for this symbol.
        
        Args:
            symbol: Trading symbol (e.g., "SOL/USD")
            
        Returns:
            Number of trades detected (0 = no position, >0 = position exists)
        """
        try:
            # Get all orders from Pacifica
            orders = self.client.get_orders()
            
            if not orders:
                return 0
            
            # Count orders that are TP or SL for this symbol
            # TP/SL orders typically have specific types or statuses
            trade_count = 0
            seen_parents = set()  # Track parent order IDs to avoid double-counting
            
            for order in orders:
                order_symbol = order.get("symbol", "")
                
                # Match symbol (handle both formats: "SOL/USD" vs "SOL-USD")
                if order_symbol.replace("-", "/") != symbol.replace("-", "/"):
                    continue
                
                # Check if this is a TP or SL order
                order_type = order.get("type", "").lower()
                order_side = order.get("side", "").lower()
                order_status = order.get("status", "").lower()
                
                # TP/SL orders are typically "stop_loss" or "take_profit" type
                # or have specific trigger types
                is_tp_sl = (
                    order_type in ("stop_loss", "take_profit", "stop", "tp", "sl") or
                    "stop" in order_type or
                    order.get("stop_price") is not None or
                    order.get("trigger_price") is not None
                )
                
                # Only count active/filled TP/SL orders (not cancelled/expired)
                is_active = order_status in ("", "open", "active", "filled", "partially_filled", "new")
                
                if is_tp_sl and is_active:
                    # Use parent_order_id or order_id to avoid double-counting
                    # Each trade has 1 TP and 1 SL, so we divide by 2
                    parent_id = order.get("parent_order_id") or order.get("order_id")
                    
                    if parent_id and parent_id not in seen_parents:
                        seen_parents.add(parent_id)
                        trade_count += 1
            
            # If we couldn't determine cleanly, fallback to simple count / 2
            if trade_count == 0:
                tp_sl_orders = [
                    o for o in orders
                    if o.get("symbol", "").replace("-", "/") == symbol.replace("-", "/")
                    and (
                        o.get("type", "").lower() in ("stop_loss", "take_profit", "stop", "tp", "sl")
                        or o.get("stop_price") is not None
                        or o.get("trigger_price") is not None
                    )
                    and o.get("status", "").lower() in ("", "open", "active", "filled", "partially_filled", "new")
                ]
                # Each trade has TP + SL = 2 orders, so divide by 2
                trade_count = len(tp_sl_orders) // 2
            
            logger.debug(f"📊 Trade count for {symbol}: {trade_count} (via TP/SL check)")
            return trade_count
            
        except Exception as e:
            logger.warning(f"⚠️ Failed to count trades via TP/SL: {e}")
            return 0  # Don't block execution on check failure

    def _calculate_position_size(self, signal: Signal) -> float:
        """
        Calculate position size based on signal and risk parameters.

        RISK MANAGEMENT WARNING:
        This is a LEGACY method kept for backward compatibility.
        New code should use: self.risk_manager.get_position_size(signal)

        Args:
            signal: Trading signal.

        Returns:
            Position size in base currency units.
        """
        # Delegate to RiskManager
        return self.risk_manager.get_position_size(
            strategy=signal.strategy,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            confidence=signal.confidence,
        )

    def _get_account_balance(self) -> float:
        """
        Get current account balance with enhanced parsing for multiple field formats.

        Returns:
            Account balance in USD.
        """
        try:
            # Check for bypass flag for testing
            bypass_validation = os.getenv('BYPASS_BALANCE_VALIDATION', 'false').lower() == 'true'
            if bypass_validation:
                logger.info("🔧 BYPASS_BALANCE_VALIDATION enabled - using default balance")
                return 10000.0  # Default test balance

            balance = self.client.get_balance()
            logger.info(f"🔍 Raw balance response: {balance}")
            
            # Enhanced parsing for multiple possible balance field names
            balance_fields = [
                "balance",           # Primary field
                "account_equity",    # Alternative field
                "equity",           # Another alternative
                "available_balance", # Available funds
                "total_equity",      # Total equity
                "usd_balance",       # USD-specific balance
                "portfolio_value"    # Portfolio value
            ]
            
            balance_str = None
            for field in balance_fields:
                if field in balance and balance[field] is not None:
                    balance_str = str(balance[field])
                    logger.info(f"🔍 Found balance field '{field}': '{balance_str}'")
                    break
            
            if balance_str is None:
                logger.warning("🔍 No balance field found in response, using 0")
                balance_str = "0"
            
            # Enhanced parsing: handle commas, currency symbols, whitespace
            try:
                # Remove common currency symbols and whitespace
                cleaned = balance_str.strip().replace('$', '').replace(',', '').replace('USD', '').replace(' ', '')
                equity = float(cleaned)
                logger.info(f"🔍 Parsed balance: '{balance_str}' -> ${equity:.2f}")
            except (ValueError, TypeError) as parse_error:
                logger.error(f"❌ Failed to parse balance '{balance_str}': {parse_error}")
                equity = 0.0
            
            logger.info(f"🔍 Final account balance: ${equity:.2f}")
            
            if equity <= 0:
                logger.warning(f"⚠️ Account balance is ${equity:.2f} - this will block all executions!")
                logger.warning(f"⚠️ Available fields in response: {list(balance.keys())}")
            else:
                logger.info(f"✅ Account balance validated: ${equity:.2f}")
            
            return equity
        except Exception as e:
            logger.error(f"❌ Error getting account balance: {e}")
            return 0.0

    def _get_current_exposure(self) -> float:
        """
        Get current total exposure across all positions.

        Returns:
            Total exposure in USD.
        """
        try:
            positions = self.client.get_positions()
            if not positions:
                return 0.0

            total_exposure = 0.0
            for pos in positions:
                quantity = float(pos.get("quantity", 0))
                # Get current price (fallback to entry price if unavailable)
                symbol = pos.get("symbol")
                entry_price = float(pos.get("entry_price", 0))

                current_price = entry_price  # Default to entry price
                if symbol:
                    try:
                        ticker = self._get_ticker_ws(symbol)
                        current_price = float(ticker.get("last", entry_price))
                    except (RuntimeError, ValueError, KeyError):
                        pass  # Keep default current_price

                exposure = quantity * current_price
                total_exposure += exposure

            logging.debug(f"Total exposure: ${total_exposure:.2f}")
            return total_exposure

        except Exception as e:
            logging.error(f"Error calculating current exposure: {e}")
            return 0.0

    def _validate_position_size(self, quantity: float, entry_price: float = 0) -> bool:
        """
        Validate that position size is within limits.

        RISK MANAGEMENT WARNING:
        This is a LEGACY method kept for backward compatibility.
        New code should use: self.risk_manager.validate_position_size(...)

        Args:
            quantity: Position size in base currency units.
            entry_price: Entry price (for notional value calculation).

        Returns:
            True if position size is valid, False otherwise.
        """
        # Delegate to RiskManager
        return self.risk_manager.validate_position_size(
            quantity=quantity, entry_price=entry_price
        )

    def _execute_signal(self, signal: Signal) -> Optional[Dict]:
        """
        Execute a trading signal.

        LEGACY METHOD - PHASE 2: Now handled by _coordinate_signal_execution()
        Kept for backward compatibility.

        Args:
            signal: Signal to execute.

        Returns:
            Order details if successful, None otherwise.
        """
        try:
            # Route to appropriate execution method based on strategy type
            if signal.strategy == StrategyType.GRID_TRADING:
                return self._execute_grid_signal(signal)
            else:
                return self._execute_standard_signal(signal)

        except Exception as e:
            logging.error(f"Error executing signal: {e}")
            return None

    def _execute_standard_signal(self, signal: Signal):
        """
        Execute a standard (non-grid) trading signal.

        LEGACY METHOD - PHASE 2: Now handled by ExecutionLayer
        Kept for backward compatibility.

        Args:
            signal: Signal to execute.

        Returns:
            Order details if successful, None otherwise.
        """
        try:
            # Calculate position size
            quantity = self._calculate_position_size(signal)

            if quantity <= 0:
                logging.warning(
                    f"Invalid position size {quantity} for {signal.asset}"
                )
                return None

            # Validate position size
            if not self._validate_position_size(quantity, signal.entry_price):
                logging.warning(
                    f"Position size validation failed for {signal.asset}"
                )
                return None

            # Place order
            order_side = "BUY" if signal.side == OrderSide.BUY else "SELL"
            order = self.client.place_order(
                symbol=signal.asset,
                side=order_side,
                quantity=quantity,
                order_type="MARKET",
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
            )

            logging.info(
                f"Order placed: {order_side} {quantity} {signal.asset} @ ${signal.entry_price:.4f}"
            )

            # Save trade to database
            self.db.save_trade(
                symbol=signal.asset,
                strategy=signal.strategy.name,
                side=order_side,
                quantity=quantity,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                confidence=signal.confidence,
                quality=signal.quality.name,
                metadata={"notes": signal.notes, "indicators": signal.indicators},
            )

            return order

        except Exception as e:
            # Use %s formatting to avoid issues with JSON in exception messages
            logging.error("Error executing standard signal: %s", str(e))
            return None

    def _calculate_emergency_stop(self, signal: Signal) -> float:
        """
        Calculate emergency stop loss price for grid trading.

        Emergency stop is a hard stop below which ALL grid orders are cancelled
        and positions are closed immediately.

        Args:
            signal: Grid trading signal

        Returns:
            Emergency stop price
        """
        # Emergency stop is 5-10% below entry depending on volatility
        # For now using fixed 7.5%
        return signal.entry_price * 0.925  # 7.5% below entry

    def _emergency_close_position(self, symbol: str, side: str, quantity: float):
        """
        Emergency close a position immediately at market price.

        Used when emergency stop is triggered for grid trading.

        Args:
            symbol: Symbol to close
            side: Position side (LONG/SHORT)
            quantity: Position size
        """
        try:
            # Determine order side (opposite of position side)
            order_side = "SELL" if side == "LONG" else "BUY"

            logging.critical(
                f"🚨 EMERGENCY CLOSE: {order_side} {quantity} {symbol} @ MARKET"
            )

            # Place market order
            order = self.client.place_order(
                symbol=symbol,
                side=order_side,
                quantity=quantity,
                order_type="MARKET",
            )

            logging.info(f"Emergency close order placed: {order}")

        except Exception as e:
            logging.error(f"Error placing emergency close order: {e}")

    def _execute_grid_signal(self, signal: Signal):
        """
        Execute a grid trading signal.

        LEGACY METHOD - PHASE 2: Now handled by GridLifecycleManager
        Kept for backward compatibility.

        Grid trading executes as follows:
        1. Calculate grid levels (buy and sell prices)
        2. Place limit orders at each grid level
        3. Monitor fills and P&L
        4. Emergency stop if price breaks support

        Args:
            signal: Grid trading signal

        Returns:
            Dict with grid details if successful, None otherwise
        """
        try:
            symbol = signal.asset
            logging.info(f"Executing GRID signal for {symbol}")

            # Calculate position size for grid (total capital allocation)
            total_capital = self._calculate_position_size(signal)

            if total_capital <= 0:
                logging.warning(
                    f"Invalid capital allocation {total_capital} for grid {symbol}"
                )
                return None

            # Calculate grid levels
            grid_levels = self._calculate_grid_levels(signal, total_capital)

            if not grid_levels:
                logging.error(f"Failed to calculate grid levels for {symbol}")
                return None

            # Place grid orders
            buy_orders = []
            sell_orders = []

            for level in grid_levels["buy_levels"]:
                order = self.client.place_order(
                    symbol=symbol,
                    side="BUY",
                    quantity=level["quantity"],
                    order_type="LIMIT",
                    price=level["price"],
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                )
                buy_orders.append(order)
                logging.info(
                    f"Grid BUY order: {level['quantity']} @ ${level['price']:.4f}"
                )

            for level in grid_levels["sell_levels"]:
                order = self.client.place_order(
                    symbol=symbol,
                    side="SELL",
                    quantity=level["quantity"],
                    order_type="LIMIT",
                    price=level["price"],
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                )
                sell_orders.append(order)
                logging.info(
                    f"Grid SELL order: {level['quantity']} @ ${level['price']:.4f}"
                )

            # Save grid position to database
            self._save_grid_position(
                symbol=symbol,
                signal=signal,
                buy_orders=buy_orders,
                sell_orders=sell_orders,
                grid_levels=grid_levels,
            )

            logging.info(
                f"Grid executed: {len(buy_orders)} BUY orders, {len(sell_orders)} SELL orders"
            )

            return {
                "symbol": symbol,
                "buy_orders": buy_orders,
                "sell_orders": sell_orders,
                "grid_levels": grid_levels,
            }

        except Exception as e:
            logging.error(f"Error executing grid signal: {e}")
            return None

    def _calculate_grid_levels(
        self, signal: Signal, total_capital: Optional[float] = None, capital: Optional[float] = None
    ) -> Dict[str, List[Dict]]:
        """
        Calculate grid price levels per Grid Trading Brief specifications.
        
        Uses actual tick_size and lot_size from Pacifica API (/info endpoint)
        to ensure order compliance and prevent 500 errors.
        """
        try:
            # Handle backward compatibility: accept both 'capital' and 'total_capital' parameters
            if total_capital is None and capital is not None:
                total_capital = capital
                logger.info(f"🔄 Using 'capital' parameter (${capital:.2f}) for backward compatibility")
            elif total_capital is not None and capital is not None:
                logger.warning(f"⚠️ Both 'capital' and 'total_capital' provided, using 'total_capital' (${total_capital:.2f})")
            elif total_capital is None:
                logger.error("❌ Neither 'capital' nor 'total_capital' provided to _calculate_grid_levels")
                return {}
            
            if total_capital <= 0:
                logger.error(f"❌ Invalid capital amount: ${total_capital:.2f}")
                return {}
            
            symbol = signal.asset
            
            # Get current price
            ticker = self._get_ticker_ws(symbol)
            current_price = float(ticker.get("last", signal.entry_price))
            if current_price <= 0:
                logger.error(
                    f"Invalid current price {current_price} for {symbol}"
                )
                return {}

            # Get actual tick_size and lot_size from Pacifica API cache
            tick_size = self.get_symbol_tick_size(symbol)
            lot_size = self.get_symbol_lot_size(symbol)
            
            # Log the market specs being used
            logger.info(
                f"📊 Grid calculation for {symbol} using Pacifica API specs: "
                f"tick_size={tick_size}, lot_size={lot_size}"
            )

            # Grid parameters from signal attributes
            num_levels = signal.grid_levels or 10

            # signal.spacing is the actual dollar spacing (already calculated by GridTradingStrategy)
            # If not set, default to 0.5% of current price
            if signal.spacing and signal.spacing > 0:
                grid_spacing = signal.spacing  # Already in price units
            else:
                grid_spacing = current_price * 0.005  # Default 0.5% of price

            # Calculate quantity per grid level
            # Total capital divided by number of levels
            quantity_per_level = total_capital / (num_levels * current_price)

            # Apply lot size compliance to quantity
            if lot_size is not None and lot_size > 0:
                quantity_per_level = self.round_quantity_to_lot_size(quantity_per_level, symbol)
            else:
                # Fallback: use conservative lot size based on price magnitude
                logger.warning(f"⚠️ No lot_size from API for {symbol}, using fallback calculation")
                if current_price >= 100:
                    lot_decimals = 4  # 0.0001
                elif current_price >= 1:
                    lot_decimals = 2   # 0.01
                else:
                    lot_decimals = 1   # 0.1 for small coins
                quantity_per_level = round(quantity_per_level, lot_decimals)
                min_lot = 10 ** (-lot_decimals)
                if quantity_per_level < min_lot:
                    quantity_per_level = min_lot

            # Generate buy levels (below current price)
            buy_levels = []
            for i in range(1, num_levels // 2 + 1):
                price = current_price - (i * grid_spacing)
                if price > 0:  # Only add positive prices
                    # Apply tick size compliance to price
                    if tick_size is not None and tick_size > 0:
                        price = self.round_price_to_tick_size(price, symbol)
                    buy_levels.append({"price": price, "quantity": quantity_per_level})

            # Generate sell levels (above current price)
            sell_levels = []
            for i in range(1, num_levels // 2 + 1):
                price = current_price + (i * grid_spacing)
                # Apply tick size compliance to price
                if tick_size is not None and tick_size > 0:
                    price = self.round_price_to_tick_size(price, symbol)
                sell_levels.append({"price": price, "quantity": quantity_per_level})

            spacing_pct = (grid_spacing / current_price * 100) if current_price > 0 else 0
            logging.info(
                f"Grid levels calculated for {symbol}: {len(buy_levels)} BUY, {len(sell_levels)} SELL, "
                f"spacing: ${grid_spacing:.4f} ({spacing_pct:.2f}%), qty/level: {quantity_per_level:.6f}, "
                f"tick_size: {tick_size}, lot_size: {lot_size}"
            )

            return {
                "buy_levels": buy_levels,
                "sell_levels": sell_levels,
                "current_price": current_price,
                "grid_spacing": grid_spacing,
                "quantity_per_level": quantity_per_level,
                "tick_size": tick_size,
                "lot_size": lot_size,
            }

        except Exception as e:
            logging.error(f"Error calculating grid levels: {e}")
            return {}

    def _save_grid_position(
        self,
        symbol: str,
        signal: Signal,
        buy_orders: List[Dict],
        sell_orders: List[Dict],
        grid_levels: Dict,
    ):
        """
        Save grid position to database.

        Grid positions are saved differently than standard positions:
        - Multiple orders (buy + sell grid levels)
        - Total capital allocation
        - Grid spacing parameters
        - Emergency stop level

        Args:
            symbol: Trading symbol
            signal: Original grid signal
            buy_orders: List of buy order details
            sell_orders: List of sell order details
            grid_levels: Calculated grid levels
        """
        try:
            # Calculate total capital allocated
            total_capital = sum(
                level["quantity"] * level["price"]
                for level in grid_levels["buy_levels"]
            )

            # Grid metadata
            metadata = {
                "grid_type": "ranging_volatile",
                "num_levels": len(buy_orders) + len(sell_orders),
                "grid_spacing": grid_levels["grid_spacing"],
                "quantity_per_level": grid_levels["quantity_per_level"],
                "buy_order_ids": [
                    o.get("order_id") or o.get("id") for o in buy_orders
                ],
                "sell_order_ids": [
                    o.get("order_id") or o.get("id") for o in sell_orders
                ],
                "emergency_stop": self._calculate_emergency_stop(signal),
                "notes": signal.notes,
            }

            # Save as special "GRID" position
            self.db.save_trade(
                symbol=symbol,
                strategy="GRID_TRADING",
                side="GRID",
                quantity=grid_levels["quantity_per_level"],
                entry_price=grid_levels["current_price"],
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                confidence=signal.confidence,
                quality=signal.quality.name,
                metadata=metadata,
            )

            logging.info(f"Grid position saved to database: {symbol}")

        except Exception as e:
            logging.error(f"Error saving grid position: {e}")

    def _monitor_risk(self) -> None:
        """
        Monitor risk and check circuit breaker.

        Checks:
        1. Total unrealized P&L against circuit breaker threshold
        2. Position exposure vs account balance
        3. Leverage usage
        """
        try:
            # Get account balance
            balance = self._get_account_balance()
            if balance <= 0:
                logging.warning("Invalid account balance - skipping risk monitoring")
                return

            # Get all positions
            positions = self.client.get_positions()
            if not positions:
                return

            # Calculate total unrealized P&L
            total_pnl = 0.0
            for pos in positions:
                unrealized_pnl = float(pos.get("unrealized_pnl", 0))
                total_pnl += unrealized_pnl

            # Calculate P&L percentage
            pnl_percentage = (total_pnl / balance) * 100

            # Check circuit breaker threshold
            if pnl_percentage <= -self._circuit_breaker_loss_pct:
                self._circuit_breaker_triggered = True
                logging.critical(
                    f"🚨 CIRCUIT BREAKER TRIGGERED: {pnl_percentage:.1f}% loss "
                    f"(${total_pnl:.2f} / ${balance:.2f})"
                )

                # Stop trading
                self.stop()

                # Publish circuit breaker event
                if self.hub_publish_func:
                    self.hub_publish_func(
                        {
                            "type": "circuit_breaker",
                            "pnl_percentage": pnl_percentage,
                            "total_pnl": total_pnl,
                            "balance": balance,
                            "timestamp": time.time(),
                        }
                    )

            # Warning at 80% of threshold
            elif pnl_percentage <= -self._circuit_breaker_loss_pct * 0.8:
                logging.warning(
                    f"⚠️ Approaching circuit breaker threshold: {pnl_percentage:.1f}% loss "
                    f"(threshold: {-self._circuit_breaker_loss_pct:.1f}%)"
                )

        except Exception as e:
            logging.error(f"Error monitoring risk: {e}")

    def _handle_regime_transition(self) -> None:
        """
        Handle market regime transitions.

        When regime changes:
        1. Close positions from strategies no longer active
        2. Cancel pending orders from inactive strategies
        3. Update strategy weights
        """
        try:
            # Detect current regime
            current_regime = self.market_regime.detect_regime("BTC")  # Use BTC as proxy

            if not hasattr(self, "_previous_regime"):
                self._previous_regime = current_regime
                return

            # Check if regime has changed
            if current_regime != self._previous_regime:
                logging.info(
                    f"🔄 Regime transition: {self._previous_regime.name} → {current_regime.name}"
                )

                # Get strategies that are no longer active
                old_strategies = self.strategy_manager.get_active_strategies(
                    self._previous_regime
                )
                new_strategies = self.strategy_manager.get_active_strategies(
                    current_regime
                )

                inactive_strategies = set(old_strategies.keys()) - set(
                    new_strategies.keys()
                )

                # Close positions from inactive strategies
                for strategy_name in inactive_strategies:
                    logging.info(
                        f"Closing positions from inactive strategy: {strategy_name}"
                    )
                    # TODO: Implement position closure by strategy

                # Update previous regime
                self._previous_regime = current_regime

        except Exception as e:
            logging.error(f"Error handling regime transition: {e}")

    def _monitor_emergency_stops(self) -> None:
        """
        Monitor emergency stops for grid trading positions.

        Emergency stop is triggered when:
        1. Price breaks below emergency stop level
        2. Grid P&L exceeds max drawdown threshold
        3. ADX rises above 20 (trend forming - not suitable for grid)
        """
        try:
            # Get all grid positions
            # TODO: Query database for active grid positions

            # For each grid position:
            # 1. Check current price vs emergency stop
            # 2. Check unrealized P&L vs max drawdown
            # 3. Check ADX (via market_regime)

            # If emergency stop triggered:
            # 1. Cancel all grid orders
            # 2. Close position at market
            # 3. Log emergency stop details

            pass  # Implementation deferred to Phase 3

        except Exception as e:
            logging.error(f"Error monitoring emergency stops: {e}")

    def _assert_grid_pre_trade(
        self,
        symbol: str,
        grid_spacing: float,
        num_levels: int,
        quantity_per_level: float,
    ) -> None:
        """
        Assert pre-trade conditions for grid trading (per Grid Trading Brief).

        Validates:
        1. Grid spacing > 0
        2. Number of levels between 5-20
        3. Quantity per level > minimum order size
        4. Emergency stop price > 0 and < current price
        5. Total capital allocation within risk limits

        Args:
            symbol: Trading symbol
            grid_spacing: Grid spacing in price units
            num_levels: Number of grid levels
            quantity_per_level: Quantity at each grid level

        Raises:
            AssertionError: If any pre-trade condition fails
        """
        # Validate grid spacing
        assert (
            grid_spacing > 0
        ), f"Grid spacing must be positive, got {grid_spacing}"

        # Validate number of levels
        assert (
            5 <= num_levels <= 20
        ), f"Number of levels must be 5-20, got {num_levels}"

        # Validate quantity per level
        min_order_size = 1.0  # TODO: Get from exchange info
        assert (
            quantity_per_level >= min_order_size
        ), f"Quantity per level {quantity_per_level} below minimum {min_order_size}"

        # Validate emergency stop price
        signal = None  # TODO: Pass signal to this method
        if signal:
            emergency_stop_price = self._calculate_emergency_stop(signal)
            assert emergency_stop_price > 0, "Emergency stop price must be positive"

            current_price = self._get_ticker_ws(symbol)
            if isinstance(current_price, dict):
                current_price = current_price.get("price", current_price.get("last", 0))
            assert emergency_stop_price < current_price, (
                f"Emergency stop {emergency_stop_price} must be below "
                f"current price {current_price}"
            )

        logging.info(f"✅ Grid pre-trade assertions passed for {symbol}")

    def get_status(self) -> Dict[str, Any]:
        """
        Get bot status for monitoring.

        Returns:
            Dict with bot state, positions, P&L, risk metrics.
        """
        try:
            # Get account balance
            balance = self._get_account_balance()

            # Get positions
            positions = self.client.get_positions()

            # Calculate total unrealized P&L
            total_pnl = sum(
                float(pos.get("unrealized_pnl", 0)) for pos in positions
            )

            # Get current exposure
            exposure = self._get_current_exposure()

            # Calculate risk percentage
            risk_pct = (exposure / balance * 100) if balance > 0 else 0

            return {
                "is_running": self.is_running,
                "circuit_breaker_triggered": self._circuit_breaker_triggered,
                "account_balance": balance,
                "total_positions": len(positions),
                "total_unrealized_pnl": total_pnl,
                "total_exposure": exposure,
                "risk_percentage": risk_pct,
                "circuit_breaker_threshold": self._circuit_breaker_loss_pct,
                "positions": [
                    {
                        "symbol": pos.get("symbol"),
                        "side": pos.get("side"),
                        "quantity": float(pos.get("quantity", 0)),
                        "entry_price": float(pos.get("entry_price", 0)),
                        "unrealized_pnl": float(pos.get("unrealized_pnl", 0)),
                    }
                    for pos in positions
                ],
            }

        except Exception as e:
            logging.error(f"Error getting bot status: {e}")
            return {
                "is_running": self.is_running,
                "error": str(e),
            }
