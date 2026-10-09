import time
import logging
import threading
import os
import uuid
import warnings
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
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

# Import core modules
from .models import Signal, OrderSide, OrderType
from .order_result import OrderResult, OrderResultStatus
from .exit_sizing import plan_close_quantity
from .venue_stops import (
    LOCAL_ONLY_STATES,
    PROTECTED_STATES,
    STOP_FAILURE_POLICY_CLOSE,
    STOP_STATE_ATTACHED,
    STOP_STATE_MISSING,
    STOP_STATE_STANDALONE,
    STOP_STATE_UNSUPPORTED,
    find_stop_row,
    position_side_lower,
    stop_hit,
    venue_stops_supported,
)

# Exchange abstraction: adapter factory selected via EXCHANGE env var
from .exchanges import get_exchange_client

# Import other modules
from .database import DatabaseManager
from .history import TradeStore
from .pacifica_client import PacificaClient
from .strategy_manager import StrategyManager
from .multi_timeframe_fetcher import MultiTimeframeFetcher
from .volatility_regime import make_regime_detector
from .risk_manager import RiskManager

# Import StrategyType from local config (re-exported from core_logic)
from .config import StrategyType

# Import WebSocket client for real-time price data
from .ws_factory import get_market_ws_client as get_ws_client

# Import GridLifecycleManager for authoritative grid state management
from .grid_lifecycle_manager import GridLifecycleManager, GridState

# Import MigratedPositionManager for trend-following management of migrated positions
from .migrated_position_manager import MigratedPositionManager

# Import RegimePositionReviewer for regime-flip open position review
from .regime_position_review import RegimePositionReviewer

# Import RegimeParamOverlayManager for per-regime optimized parameter
# overlays (P4, behind ENABLE_REGIME_PARAM_OVERLAYS - default off)
from .regime_param_overlay import RegimeParamOverlayManager

# Import ExecutionLayer for precise 1m/5m entry timing
from .execution_layer import ExecutionLayer

# Import SignalLogger for comprehensive signal tracking
from .signal_logger import SignalLogger

# Import Phase 2 component system
from .event_system import get_event_bus, EventType
from .component_registry import get_component_registry
from .telegram_alerts import telegram_alerts

# Import PositionReconciler for H4 position reconciliation
from .position_reconciler import PositionReconciler


def _signal_stop_price(signal: Any) -> Optional[float]:
    """The signal's protective stop, or None when it carries none."""
    try:
        stop = float(getattr(signal, "stop_loss", 0) or 0)
    except (TypeError, ValueError):
        return None
    return stop if stop > 0 else None


def _entry_fill_max_lookups() -> int:
    """ENTRY_FILL_MAX_LOOKUPS: env var first (legacy), else config (default 10)."""
    raw = os.getenv("ENTRY_FILL_MAX_LOOKUPS")
    if raw is not None:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return max(1, int(getattr(config, "entry_fill_max_lookups", 10)))


def _resolve_exchange(bot):
    """Return the exchange adapter for a bot-like object.

    Real TradingBot instances expose the ``exchange`` property; some test
    fixtures bind TradingBot methods onto duck-typed stand-ins (e.g.
    SimpleNamespace) that only carry ``client``.  For those, wrap the raw
    client in a fresh adapter via the factory.

    Args:
        bot: TradingBot instance or duck-typed stand-in with ``client``.

    Returns:
        ExchangeClient adapter for the bot's current client.
    """
    exchange = getattr(bot, "exchange", None)
    if exchange is None:
        exchange = get_exchange_client(rest_client=getattr(bot, "client", None))
    return exchange


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

        # Trade recording/querying facade; tags trades with the active
        # exchange name (EXCHANGE env, default pacifica) automatically.
        self.trade_store = TradeStore(db=self.db)

        # Initialize signal logger (CSV auto-save + database + memory)
        self.signal_logger = SignalLogger(db_manager=self.db)
        logger.info("Signal logger initialized - logging to signals_log.csv")

        # Initialize client.
        # NOTE: the Config attributes are named pacifica_*; the AGENT_WALLET_*
        # / ACCOUNT_* spellings are the .env variable names, not attributes.
        # Using the env spellings here raised AttributeError and made this
        # whole branch dead - api_server always injects a client, so it went
        # unnoticed.
        if client is None:
            self.client = PacificaClient(
                agent_wallet_private_key=config.pacifica_private_key,
                account_public_key=config.pacifica_public_key,
                testnet=config.testnet,
            )
        else:
            self.client = client

        # Initialize risk manager.
        # NOTE: in production, BotIntegration always passes a constructed
        # RiskManager via the `risk_manager=` argument, so this fallback
        # path is only used by tests and standalone instantiation.
        # The fallback signature must match RiskManager.__init__ exactly —
        # `db` and `risk_profile` (singular) are NOT valid kwargs.
        if risk_manager is None:
            self.risk_manager = RiskManager(client=self.client)
        else:
            self.risk_manager = risk_manager

        # Initialize hub publish function
        self.hub_publish_func = hub_publish_func

        # Initialize circuit breaker
        self._circuit_breaker_triggered = False
        self._circuit_breaker_loss_pct = (
            config.circuit_breaker_loss_pct
        )  # From config (default 10%)

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

        # Exchange adapter facade over the raw client (exchange modularity
        # refactor).  The factory reads EXCHANGE (default "pacifica"); the
        # adapter owns all side/amount vocabulary conversion.  self.client
        # remains the raw native client for legacy call sites.
        self._exchange = get_exchange_client(
            rest_client=self.client, ws_client=self.ws_client
        )

        # Initialize multi-timeframe data fetcher with WebSocket support and longer cache
        self.multi_tf_fetcher = MultiTimeframeFetcher(
            self.client,
            ws_client=self.ws_client,
            cache_ttl_seconds=300,  # 5 minutes cache (tiered TTL handles 1m/5m)
        )

        # Initialize market regime detector (uses default thresholds).
        # Wired with the global event bus + db so confirmed regime
        # transitions publish REGIME_CHANGED and persist to regime_history.
        # REGIME_MODE selects the taxonomy: 'adx' (default, shipped) or
        # 'volatility' (docs/REGIME-VOLATILITY.md).
        self.market_regime = make_regime_detector(event_bus=get_event_bus(), db=self.db)

        # Initialize strategy manager with regime detector
        self.strategy_manager = StrategyManager(
            regime_detector=self.market_regime,
            risk_manager=self.risk_manager,
            client=self.client,  # Pass client for FundingArb API calls
            ws_client=self.ws_client,  # Pass WS client for OrderBookImbalance
            db=self.db,  # Pass db for adaptive per-regime strategy weights
        )

        # Initialize grid lifecycle manager
        self.grid_lifecycle = GridLifecycleManager(
            client=self.client,
            risk_manager=self.risk_manager,
            db=self.db,
            regime_detector=self.market_regime,
        )

        # Initialize grid system: load from DB, repair orphans, validate integrity
        self.grid_lifecycle.initialize_grid_system()

        # Entries the exchange accepted but whose fills were not yet
        # confirmed at submit time: {client_order_id: {...}}.  Completed
        # (or given up on) by _complete_pending_entries each loop.
        self._pending_entries: Dict[str, Dict[str, Any]] = {}
        self._entry_fill_max_lookups = _entry_fill_max_lookups()

        # Venue-side stop protection (live-readiness audit T4).  What to
        # do when a filled entry cannot be protected, plus an in-memory
        # mirror of the persisted protection records keyed (symbol, SIDE)
        # so stand-ins without a DB still enforce local stops.
        self._venue_stop_policy: str = getattr(
            config, "venue_stop_failure_policy", STOP_FAILURE_POLICY_CLOSE
        )
        self._protection_records: Dict[tuple, Dict[str, Any]] = {}
        self._startup_stop_repair_done = False

        # Initialize migrated position manager
        self.migrated_position_manager = MigratedPositionManager(
            client=self.client,
            risk_manager=self.risk_manager,
            regime_detector=self.market_regime,
            multi_tf_fetcher=self.multi_tf_fetcher,
            reconcile_callback=self._reconcile_after_ambiguous_close,
            venue_exchange_resolver=lambda: self.exchange,
            db=self.db,
        )

        # Initialize regime param overlay manager (P4). Loads active
        # per-regime optimized parameters and swaps them onto the shared
        # strategy instances when the reference symbol's confirmed regime
        # changes. Inert unless ENABLE_REGIME_PARAM_OVERLAYS=true.
        self.regime_param_overlays = RegimeParamOverlayManager(
            strategy_manager=self.strategy_manager,
            db=self.db,
        )

        # Initialize regime-flip position reviewer (subscribes to
        # REGIME_CHANGED in _setup_event_subscriptions when enabled)
        self.regime_position_reviewer = RegimePositionReviewer(
            db=self.db,
            client=self.client,
            regime_detector=self.market_regime,
            migrated_position_manager=self.migrated_position_manager,
            multi_tf_fetcher=self.multi_tf_fetcher,
        )

        # Initialize execution layer for precise 1m/5m entry timing
        try:
            self.execution_layer = ExecutionLayer(
                fetcher=self.multi_tf_fetcher,
            )
            logger.info("ExecutionLayer initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize ExecutionLayer: {e}")
            self.execution_layer = None
            logger.warning("Trading will continue without ExecutionLayer refinement")

        # Thread control
        self._running_event = threading.Event()
        self.thread = None

        # PHASE 2: Component system
        self.event_bus = get_event_bus()
        self.component_registry = get_component_registry()
        self._register_components()
        self._setup_event_subscriptions()

        # H4: Position reconciliation (coordinator supplies client + db,
        # reconciler owns the comparison logic - see position_reconciler.py)
        self.position_reconciler = PositionReconciler(
            db=self.db, event_bus=self.event_bus
        )
        self._last_reconciliation_time = 0.0

        logging.info("Trading bot initialized")

    @property
    def exchange(self):
        """Exchange adapter facade over the CURRENT raw client.

        Resolved dynamically because tests (and some legacy code paths)
        reassign ``self.client`` after construction; the cached adapter
        is rebuilt whenever the underlying client identity changes.
        Instances built without __init__ (test fixtures using __new__)
        also get a working adapter lazily.

        Returns:
            ExchangeClient adapter wrapping ``self.client``.
        """
        raw = getattr(self, "client", None)
        cached = getattr(self, "_exchange", None)
        if cached is not None and cached.rest_client is raw:
            return cached
        exchange = get_exchange_client(
            rest_client=raw, ws_client=getattr(self, "ws_client", None)
        )
        self._exchange = exchange
        return exchange

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
        if (
            not self.ws_client
            or not hasattr(self.ws_client, "_running")
            or not self.ws_client._running
        ):
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
                # WebSocket exposes only the last price — DO NOT fabricate
                # bid/ask/high/low. Anyone needing those must use kline data.
                # Previous fabrication (price ± 0.05% / ± 2%) was passed back
                # as if it were real, which would break stop-distance logic if
                # any caller ever read ticker["high"] or ticker["low"].
                return {
                    "symbol": symbol,
                    "last": float(price),
                    "volume": 0,  # Not available via ticker WS
                    "timestamp": int(time.time() * 1000),
                    "_source": "ws_last_only",  # marker so callers can detect
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

        # Wait for WebSocket prices to cache before first iteration
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
            self._last_loop_time = datetime.now(timezone.utc)
            self._loop_step = "starting"
            try:
                logger.info(
                    f"🔄 Trading loop iteration {self._loop_iteration} starting..."
                )

                # Update positions from client
                self._loop_step = "update_positions"
                self._update_positions()

                # Once, after the first position sync: re-install any venue
                # stop lost across the restart (closes the restart hole).
                if not self._startup_stop_repair_done:
                    self._startup_stop_repair_done = True
                    self._loop_step = "repair_venue_stops_startup"
                    self._repair_venue_stops(reason="startup")

                # Complete entries whose fills were not confirmed at submit
                self._loop_step = "complete_pending_entries"
                self._complete_pending_entries()

                # Local stop enforcement for positions the venue is not
                # protecting (state missing / unsupported)
                self._loop_step = "enforce_local_stops"
                self._enforce_local_stops()

                # GRID MONITORING: Monitor active grids for fills, P&L, emergency stops
                self._loop_step = "monitor_grids"
                self._monitor_grids()

                # MIGRATED POSITION MANAGEMENT: Manage positions migrated from grid
                self._loop_step = "manage_migrated_positions"
                self._manage_migrated_positions()

                # PHASE 2: Generate and publish signals as events (coordinator role)
                self._loop_step = "generate_signals"
                # Use the monotonic _published_count instead of len(_event_history).
                # len() saturates at max_history (1000) and then always returns 1000,
                # making "after - before" permanently 0 — a misleading metric.
                count_before = self.event_bus._published_count
                logger.info(
                    f"📊 Calling _generate_and_publish_signals (total_published_so_far={count_before})..."
                )
                self._generate_and_publish_signals()
                count_after = self.event_bus._published_count
                self._loop_events_generated = count_after - count_before
                logger.info(
                    f"📊 Signal generation complete (published_this_loop={self._loop_events_generated}, total_published={count_after})"
                )

                # Monitor risk (delegated to RiskManager)
                self._loop_step = "monitor_risk"
                self._monitor_risk_coordinated()

                # House-keeping: prune expired capital-approval entries.
                # Without this, RiskManager._pending_approvals grows
                # unboundedly (one entry per request, ~1200/hr at scale).
                self._loop_step = "cleanup_approvals"
                try:
                    if self.risk_manager and hasattr(
                        self.risk_manager, "cleanup_expired_approvals"
                    ):
                        self.risk_manager.cleanup_expired_approvals()
                except Exception as e:
                    logger.warning(f"cleanup_expired_approvals failed: {e}")

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
                    f"Error in trading loop (step={self._loop_step}): {e}",
                    exc_info=True,
                )

            # Sleep for configured interval (30 seconds)
            self._loop_step = "sleeping"
            time.sleep(getattr(config, "trading_loop_interval", 30))

    def _update_positions(self) -> None:
        """
        Update positions from Pacifica API and reconcile against local DB.

        Syncs positions with database for accurate P&L tracking. Also fixes
        the H4 position-drift gap: if the exchange returns an empty list
        (a successful call reporting zero open positions), any positions
        still open in the local DB are closed. This must be distinguished
        from an API call failure (exception), in which case reconciliation
        is skipped entirely for this iteration to avoid wiping out valid
        local state on a transient network error.

        A full discrepancy-detecting reconciliation (side/quantity/entry
        price mismatches, adoption of exchange-only positions) runs on a
        configurable interval (RECONCILIATION_INTERVAL_SECONDS) via
        `_maybe_run_full_reconciliation`, since that path is more
        expensive and less urgent than the stale-position close.
        """
        try:
            # Get positions from API. If this raises, we skip reconciliation
            # entirely this iteration (API failure != "no positions").
            positions = self.client.get_positions()
        except Exception as e:
            logging.error(f"Error fetching positions from exchange: {e}")
            return

        try:
            if not positions:
                # API call succeeded and reported zero open positions.
                # Close any open local positions to avoid drift (H4 fix -
                # previously this branch returned early and NEVER closed
                # stale local positions).
                self._close_stale_local_positions(exchange_positions=[])
                logging.debug(
                    "No open positions from Pacifica API - stale local "
                    "positions (if any) have been closed"
                )
                return

            # Filter to real positions (non-zero quantity).  Raw Pacifica
            # positions carry the size under "amount", not "quantity".
            from .position_reconciler import position_quantity

            real_positions = [p for p in positions if position_quantity(p) > 0]
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
                quantity = position_quantity(pos)
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
                self.db.save_position(
                    {
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
                    }
                )

                logging.debug(
                    f"Updated position: {symbol} {side} {quantity} @ ${entry_price:.2f} "
                    f"(current: ${current_price:.2f}, PnL: ${unrealized_pnl:.2f})"
                )

            # Cheap drift fix every loop: close any local position that is
            # no longer present on the exchange (non-empty list case).
            self._close_stale_local_positions(exchange_positions=positions)

            # Expensive discrepancy-detecting reconciliation runs on interval.
            self._maybe_run_full_reconciliation(exchange_positions=positions)

        except Exception as e:
            logging.error(f"Error updating positions: {e}")

    def _close_stale_local_positions(
        self, exchange_positions: List[Dict[str, Any]]
    ) -> None:
        """
        Close local DB positions that are no longer present on the exchange.

        This is the cheap, every-loop half of H4 position reconciliation.
        The exchange is authoritative: if a position exists locally but not
        on the exchange, it has been closed (manually, by SL/TP, or by
        liquidation) and must be closed locally too, or it will drift
        forever (the original bug this fixes).

        Args:
            exchange_positions: Raw position list from the exchange client
                (may be empty - that is a valid "no open positions" state).
        """
        try:
            from .position_reconciler import _filter_real_positions

            exchange_map = _filter_real_positions(exchange_positions)
            db_positions = self.db.get_positions()
            db_map = _filter_real_positions(db_positions)

            for key, db_pos in db_map.items():
                if key not in exchange_map:
                    self.db.close_position(db_pos["symbol"], db_pos["side"])
                    logger.warning(
                        f"Closed stale local position {db_pos['symbol']} "
                        f"{db_pos['side']} (no longer on exchange)"
                    )
        except Exception as e:
            logger.error(f"Error closing stale local positions: {e}")

    def _maybe_run_full_reconciliation(
        self, exchange_positions: List[Dict[str, Any]]
    ) -> None:
        """
        Run the full discrepancy-detecting reconciliation on an interval.

        Unlike `_close_stale_local_positions` (which runs every loop and
        only handles the drift bug), this also adopts exchange-only
        positions and detects/corrects side/quantity/entry-price mismatches,
        publishing a POSITION_DISCREPANCY event and incrementing metrics
        for each discrepancy found. Controlled by
        RECONCILIATION_INTERVAL_SECONDS (default 3600s).

        Args:
            exchange_positions: Raw position list from the exchange client.
        """
        now = time.time()
        interval = getattr(config, "reconciliation_interval_seconds", 3600)
        last_run = getattr(self, "_last_reconciliation_time", 0.0)

        if now - last_run < interval:
            return

        self._last_reconciliation_time = now

        try:
            db_positions = self.db.get_positions()
            report = self.position_reconciler.reconcile(
                exchange_positions=exchange_positions, db_positions=db_positions
            )
            logger.info(f"Full position reconciliation report: {report.to_dict()}")
        except Exception as e:
            logger.error(f"Error running full position reconciliation: {e}")

        # Same cadence: make sure every open position still has its venue
        # stop (an operator cancel-all or a venue-side purge can remove it).
        self._repair_venue_stops(reason="reconciliation")

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
                        f"Cleared {len(stale_symbols)} stale grid(s) from DB "
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
                buy_orders = [
                    o for o in orders if o.get("side") in ("bid", "BUY", "buy")
                ]
                sell_orders = [
                    o for o in orders if o.get("side") in ("ask", "SELL", "sell")
                ]

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
                confirmed_grid_symbols = set()
                for symbol, orders in orders_by_symbol.items():
                    buy_orders = [
                        o for o in orders if o.get("side") in ("bid", "BUY", "buy")
                    ]
                    sell_orders = [
                        o for o in orders if o.get("side") in ("ask", "SELL", "sell")
                    ]
                    if len(orders) >= 5 and buy_orders and sell_orders:
                        confirmed_grid_symbols.add(symbol)

                stale_symbols = [
                    s
                    for s in self.grid_lifecycle._grids
                    if s not in confirmed_grid_symbols
                ]
                for symbol in stale_symbols:
                    self.grid_lifecycle.delete_grid_state(symbol)
                    del self.grid_lifecycle._grids[symbol]
                    logger.warning(
                        f"Cleared stale grid for {symbol} "
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
            buy_prices = [
                float(o.get("price", 0)) for o in buy_orders if o.get("price")
            ]
            sell_prices = [
                float(o.get("price", 0)) for o in sell_orders if o.get("price")
            ]

            if not buy_prices or not sell_prices:
                logger.error(f"Cannot re-adopt {symbol}: no valid prices in orders")
                return

            center_price = (max(buy_prices) + min(sell_prices)) / 2
            # DOLLAR spacing (grid_spacing is consumed as a dollar offset by
            # _replenish_order). The gap between the highest bid and lowest
            # ask straddles the center, i.e. about TWO grid steps.
            gap = min(sell_prices) - max(buy_prices)
            grid_spacing = gap / 2 if gap > 0 else 0

            # Estimate capital from order sizes
            total_capital = sum(
                float(o.get("initial_amount", 0)) * float(o.get("price", 0))
                for o in all_orders
            )

            # SAFETY: compute a real emergency stop on re-adoption.
            # Without this, _check_emergency_stop short-circuits on ≤ 0 and the
            # grid runs unprotected at the per-symbol level until next restart.
            # Convention (matches grid_lifecycle_manager._sync_grids_from_exchange):
            #   emergency_stop = lowest_buy_price × (1 - GRID_EMERGENCY_STOP_PCT)
            # Falls back to center × (1 - pct) if no buy orders are visible.
            emergency_stop_pct = float(os.getenv("GRID_EMERGENCY_STOP_PCT", "0.05"))
            if buy_prices:
                emergency_stop = min(buy_prices) * (1.0 - emergency_stop_pct)
            else:
                emergency_stop = center_price * (1.0 - emergency_stop_pct)

            # Register directly in GridLifecycleManager
            if self.grid_lifecycle and symbol not in self.grid_lifecycle._grids:
                self.grid_lifecycle._grids[symbol] = {
                    "state": GridState.ACTIVE,
                    "grid_capital": total_capital,
                    "emergency_stop": emergency_stop,  # FIX: was 0 — unprotected on re-adoption
                    "regime_on_creation": "unknown_readopted",
                    "atr_at_creation": 0,
                    "grid_spacing": grid_spacing,
                    "num_levels": len(all_orders),
                    "orders_placed": len(all_orders),
                    "center_price": center_price,
                    "initial_center": center_price,
                    "created_at": datetime.now(timezone.utc),
                    "refresh_count": 0,
                    "readopted": True,  # Flag that this was re-adopted
                }

                # Populate tracked order IDs from the exchange's open-orders
                # response so fills are attributed strictly by order ID.
                readopted_ids = set()
                for order in all_orders:
                    oid = order.get("order_id") or order.get("id")
                    if oid:
                        readopted_ids.add(str(oid))
                if readopted_ids:
                    self.grid_lifecycle._grids[symbol]["order_ids"] = readopted_ids

                logger.info(
                    f"✅ Re-adopted grid for {symbol}: center=${center_price:.4f}, "
                    f"emergency_stop=${emergency_stop:.4f} "
                    f"({emergency_stop_pct * 100:.0f}% below lowest bid), "
                    f"{len(buy_orders)} bids + {len(sell_orders)} asks, "
                    f"capital≈${total_capital:.2f}"
                )
            else:
                logger.warning(
                    f"Cannot re-adopt {symbol}: GridLifecycleManager unavailable or grid already exists"
                )

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

            # Cancel all orders for this symbol (via exchange adapter)
            exchange = _resolve_exchange(self)
            try:
                result = exchange.cancel_all_orders(symbol=symbol)
                logger.info(f"  ✅ Cancelled all orders for {symbol}: {result}")
            except Exception as e:
                logger.error(f"  ❌ Failed to cancel orders for {symbol}: {e}")

            # Check if there's an open position for this symbol.  The
            # adapter returns normalized positions with qty==0 ghosts
            # already filtered out.
            positions = exchange.get_positions()
            symbol_positions = [p for p in positions if p.symbol == symbol]

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

            # A force exit that could not confirm every position flat is
            # retried inside monitor_grids; keep calling it while such a
            # marker exists even if no ACTIVE grid (or no price) is left.
            pending = getattr(self.grid_lifecycle, "get_pending_flattens", None)
            flattens = pending() if callable(pending) else None
            has_pending = isinstance(flattens, dict) and bool(flattens)
            if not current_prices and not has_pending:
                return  # No active grids, no prices and nothing to retry

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
                logger.error(f"Error monitoring grids: {e}", exc_info=True)

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
            logger.error(f"Error managing migrated positions: {e}", exc_info=True)

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
            for market in markets[
                :10
            ]:  # Limit to first 10 markets to avoid rate limits
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
                        logger.debug(
                            f"Skipping {symbol}: invalid price {current_price}"
                        )
                        continue

                    # Get multi-timeframe data (5m for MomentumScalping, others for main strategies)
                    multi_tf_data = self.multi_tf_fetcher.get_candles_multi_tf(
                        symbol=symbol,
                        timeframes=["5m", "15m", "1h", "4h"],
                        lookback_candles=250,
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
                                "volume_confirmation": signal.volume_confirmation,
                                "multi_timeframe_alignment": signal.multi_timeframe_alignment,
                                "support_resistance_valid": signal.support_resistance_valid,
                                "rrr_meets_minimum": signal.rrr_meets_minimum,
                                "liquidation_buffer_safe": signal.liquidation_buffer_safe,
                                "account_risk_ok": signal.account_risk_ok,
                                "margin_drawdown_ok": signal.margin_drawdown_ok,
                                "forbidden_conditions_clear": signal.forbidden_conditions_clear,
                            }
                            failed = [k for k, v in flags.items() if not v]
                            reason = f"Pre-publish validation failed: {failed}"
                            logger.debug(
                                f"Skipping invalid signal for {symbol}: {failed}"
                            )
                            regime_str = (
                                regime.name
                                if regime and hasattr(regime, "name")
                                else str(regime)
                                if regime
                                else "unknown"
                            )
                            self.signal_logger.log_signal_rejected(
                                signal=signal, reason=reason, regime=regime_str
                            )
                            continue

                        # Log signal BEFORE publishing event (event bus is synchronous)
                        signals_generated += 1
                        regime_str = (
                            regime.name
                            if regime and hasattr(regime, "name")
                            else str(regime)
                            if regime
                            else "unknown"
                        )
                        log_result = self.signal_logger.log_signal_generated(
                            signal=signal,
                            regime=regime_str,
                            notes=f"Generated from {signal.strategy.name}",
                        )

                        # Check if signal was rejected as duplicate
                        if not log_result:
                            logger.info(
                                f"Duplicate signal rejected by deduplication: {signal.strategy.name} {signal.side.name} "
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
        """
        # Portfolio circuit breaker: the only caller of _monitor_risk.
        # Before this delegation nothing in the loop evaluated it, so the
        # documented 10% loss stop could never trigger.
        self._monitor_risk()
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

            # Publish risk event for monitoring
            self.event_bus.publish_event(
                event_type=EventType.CAPITAL_REQUESTED,
                data={
                    "balance": balance,
                    "exposure": exposure,
                    "risk_pct": risk_pct,
                    "timestamp": time.time(),
                },
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
        self.event_bus.subscribe(
            EventType.SIGNAL_GENERATED, self._handle_signal_generated
        )
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

        # Grid regime unwind: when a confirmed regime change leaves the
        # grid-allowed set (ranging/indecisive), unwind the symbol's active
        # grid (cancel orders, partial/full position exit). Without this
        # subscription nothing ever calls on_regime_disallowed and grid
        # orders are orphaned on the exchange after every regime flip.
        self.event_bus.subscribe(
            EventType.REGIME_CHANGED, self._handle_regime_changed_for_grids
        )
        logger.info("Grid lifecycle subscribed to REGIME_CHANGED")

        # Telegram alerts: subscribe to key events for notifications
        if telegram_alerts.enabled:
            self.event_bus.subscribe(
                EventType.SIGNAL_EXECUTED, telegram_alerts._handle_event_sync
            )
            self.event_bus.subscribe(
                EventType.RISK_LIMIT_EXCEEDED, telegram_alerts._handle_event_sync
            )
            self.event_bus.subscribe(
                EventType.REGIME_CHANGED, telegram_alerts._handle_event_sync
            )
            self.event_bus.subscribe(
                EventType.COMPONENT_FAILURE, telegram_alerts._handle_event_sync
            )
            self.event_bus.subscribe(
                EventType.POSITION_DISCREPANCY, telegram_alerts._handle_event_sync
            )
            logger.info("Telegram alert event subscriptions configured")
        else:
            logger.info("Telegram alerts disabled - skipping event subscriptions")

        # Regime-flip position review: revisit open non-grid positions when
        # a symbol's confirmed regime changes (ENABLE_REGIME_POSITION_REVIEW)
        if self.regime_position_reviewer.enabled:
            self.event_bus.subscribe(
                EventType.REGIME_CHANGED,
                self.regime_position_reviewer.handle_regime_changed,
            )
            logger.info("Regime position review subscribed to REGIME_CHANGED")
        else:
            logger.info("Regime position review disabled - skipping subscription")

        # Regime param overlays (P4): swap optimized per-regime params
        # onto the shared strategy instances on confirmed regime changes
        # of the reference symbol (ENABLE_REGIME_PARAM_OVERLAYS)
        if self.regime_param_overlays.enabled:
            self.event_bus.subscribe(
                EventType.REGIME_CHANGED,
                self.regime_param_overlays.handle_regime_changed,
            )
            logger.info("Regime param overlays subscribed to REGIME_CHANGED")
        else:
            logger.info("Regime param overlays disabled - skipping subscription")

        logger.info("Event subscriptions configured")

    def _handle_signal_generated(self, event):
        """
        Handle SIGNAL_GENERATED event.

        PHASE 2: Coordinator receives signals as events and coordinates execution.
        """
        try:
            # Event object has .data attribute (not a dict with .get())
            signal_data = (
                event.data if hasattr(event, "data") else event.get("data", {})
            )
            signal = (
                signal_data.get("signal") if isinstance(signal_data, dict) else None
            )

            if not signal:
                logger.warning(
                    "Received signal event with no signal data - event.data type=%s, signal_data type=%s",
                    type(event.data).__name__,
                    type(signal_data).__name__,
                )
                return

            logger.info(
                f"📨 Signal handler received: {signal.strategy.name} {signal.side.name} "
                f"{signal.asset} (valid={signal.is_valid()}, confidence={signal.confidence})"
            )

            # Validate signal should be executed
            if not self._should_execute_signal(signal):
                logger.info(
                    f"🚫 Signal validation failed for {signal.asset} {signal.strategy.name} - skipping execution"
                )
                return

            # Log signal details for debugging
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
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

        Checks (in priority order — earliest short-circuits first):
        0a. Circuit breaker — if portfolio loss tripped it, no new entries
        0b. Supervisor pause flag — external pause without killing bot
        1.  Signal is valid (8 validation flags)
        2.  Stop loss is present and valid (CRITICAL SAFETY CHECK)
        3.  Account has sufficient capital
        4.  Risk limits not exceeded
        5.  No conflicting positions
        """
        try:
            # Check 0a: Circuit breaker (highest priority — defensive layer)
            # Belt-and-suspenders: stop() clears _running_event, but we don't
            # want to depend on stop() succeeding. If the breaker tripped,
            # block new entries even if the loop is somehow still spinning.
            if self._circuit_breaker_triggered:
                reason = (
                    f"Circuit breaker triggered (portfolio loss "
                    f">= {self._circuit_breaker_loss_pct:.1%})"
                )
                logger.critical(
                    f"🛑 Signal blocked by circuit breaker for {signal.asset} "
                    f"{signal.strategy.name}"
                )
                self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
                return False

            # Check 0b: Supervisor pause
            try:
                from .supervisor_control import get_supervisor_control
            except ImportError:
                from supervisor_control import get_supervisor_control
            sup = get_supervisor_control()
            if sup.is_paused():
                pause_status = sup.status().get("raw_state", {})
                pause_reason = pause_status.get("reason", "no reason")
                reason = f"supervisor pause: {pause_reason}"
                logger.warning(
                    f"⏸  Signal blocked by supervisor pause for {signal.asset} "
                    f"{signal.strategy.name}: {pause_reason}"
                )
                self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
                return False

            # Check signal validity
            if not signal.is_valid():
                flags = {
                    "volume_confirmation": signal.volume_confirmation,
                    "multi_timeframe_alignment": signal.multi_timeframe_alignment,
                    "support_resistance_valid": signal.support_resistance_valid,
                    "rrr_meets_minimum": signal.rrr_meets_minimum,
                    "liquidation_buffer_safe": signal.liquidation_buffer_safe,
                    "account_risk_ok": signal.account_risk_ok,
                    "margin_drawdown_ok": signal.margin_drawdown_ok,
                    "forbidden_conditions_clear": signal.forbidden_conditions_clear,
                }
                failed = [k for k, v in flags.items() if not v]
                reason = f"Signal invalid - failed flags: {failed}"
                logger.info(
                    f"🚫 Signal invalid for {signal.asset} {signal.strategy.name}: failed={failed}"
                )
                self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
                return False

            # CRITICAL: Validate stop loss is present
            if not signal.stop_loss or signal.stop_loss <= 0:
                reason = (
                    "CRITICAL: Signal missing valid stop loss - rejecting for safety"
                )
                logger.error(f"{reason} for {signal.asset}")
                self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
                return False

            # Check account balance
            balance = self._get_account_balance()
            if balance <= 0:
                reason = "Invalid account balance (<=0)"
                logger.warning("Invalid account balance - cannot execute signal")
                self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
                return False

            # Check risk limits
            current_exposure = self._get_current_exposure()
            exposure_pct = (current_exposure / balance) * 100

            if exposure_pct >= 80:  # 80% utilization limit
                reason = f"Risk limit reached ({exposure_pct:.1f}% >= 80%)"
                logger.warning(
                    f"Risk limit reached ({exposure_pct:.1f}%) - cannot execute signal"
                )
                self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
                return False

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

            # Calculate requested amount based on position size.
            # The current confirmed regime scales the size via RiskManager's
            # per-regime multipliers (None regime -> multiplier 1.0).
            # getattr-guarded: partially constructed bots (tests) may lack
            # the regime detector, in which case sizing stays neutral.
            regime_detector = getattr(self, "market_regime", None)
            current_regime = (
                regime_detector.get_current_regime(signal.asset)
                if regime_detector is not None
                else None
            )
            position_size = self.risk_manager.get_position_size(
                signal=signal,
                account_balance=account_balance,
                current_exposure=current_exposure,
                regime=current_regime,
            )
            requested_amount = position_size * signal.entry_price

            # Request capital allocation
            allocation_result = self.risk_manager.request_capital_allocation(
                symbol=signal.asset,
                requested_amount=requested_amount,
                strategy=signal.strategy.name
                if hasattr(signal.strategy, "name")
                else str(signal.strategy),
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

            # Execute signal based on type
            if signal.strategy == StrategyType.GRID_TRADING:
                self._execute_grid_signal_coordinated(
                    signal, allocation_result, log_entry
                )
            else:
                self._execute_standard_signal_coordinated(
                    signal, allocation_result, log_entry
                )

        except Exception as e:
            # Use loguru's opt(exception=True) so the full traceback is captured
            logger.opt(exception=True).error(
                f"Error coordinating signal execution for {signal.asset}: {type(e).__name__}: {e}"
            )
            self.signal_logger.log_signal_failed(
                signal=signal,
                error=f"{type(e).__name__}: {e}",
                notes="Exception during signal coordination",
            )

    def _execute_coordinated_signal(self, signal, allocation_result, log_entry=None):
        """
        Execute signal through appropriate execution path.

        PHASE 2: Delegates to GridLifecycleManager or ExecutionLayer based on strategy type.
        """
        try:
            if signal.strategy == StrategyType.GRID_TRADING:
                self._execute_grid_signal_coordinated(
                    signal, allocation_result, log_entry
                )
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
        """
        try:
            symbol = signal.asset

            # Pre-check: skip if grid already active for this symbol
            if self.grid_lifecycle and self.grid_lifecycle.has_active_grid(symbol):
                reason = f"Grid already active for {symbol}"
                logger.info(f"🔷 {reason}, skipping duplicate grid signal")
                self.signal_logger.log_signal_rejected(signal=signal, reason=reason)
                return

            # RiskManager returns "allocated_amount" (not "capital_allocated")
            capital_allocated = allocation_result.get("allocated_amount", 0)

            logger.info(
                f"🔷 Executing GRID signal for {symbol} with ${capital_allocated:.2f} capital"
            )

            # Place grid orders through GridLifecycleManager
            # TradingBot has self.client (PacificaClient) for order execution
            result = self._place_grid_orders(
                signal=signal,
                allocation_result=allocation_result,
                log_entry=log_entry,
            )

            if result.get("success"):
                buy_orders = result.get("buy_orders", 0)
                sell_orders = result.get("sell_orders", 0)
                logger.info(
                    f"✅ Grid orders placed for {symbol}: "
                    f"{buy_orders} BUY, {sell_orders} SELL"
                )

                # Log successful grid execution
                self.signal_logger.log_signal_executed(
                    signal=signal,
                    order_id=f"grid_{symbol}_{buy_orders}buy_{sell_orders}sell",
                    filled_price=signal.entry_price,
                    filled_quantity=buy_orders + sell_orders,
                    execution_result=f"Grid placed: {buy_orders} BUY, {sell_orders} SELL",
                    notes=f"Capital allocated: ${capital_allocated:.2f}",
                )

                # Register grid with GridLifecycleManager for monitoring.
                # Emergency stop MUST sit BELOW the lowest buy level: the old
                # value (signal.stop_loss = entry - 2*ATR) landed INSIDE the
                # grid and force-exited healthy grids on normal oscillation.
                # Convention matches re-adoption: lowest_buy * (1 - stop_pct).
                emergency_stop_pct = float(os.getenv("GRID_EMERGENCY_STOP_PCT", "0.05"))
                center_price = result.get("center_price") or signal.entry_price
                lowest_buy_price = result.get("lowest_buy_price") or 0
                if lowest_buy_price > 0:
                    emergency_stop_price = lowest_buy_price * (1.0 - emergency_stop_pct)
                else:
                    emergency_stop_price = center_price * (1.0 - emergency_stop_pct)

                # Record the actual market regime (not signal.market_state,
                # which is always "RANGE" for grid signals).
                regime_str = None
                regime_detector = getattr(self, "market_regime", None)
                if regime_detector is not None:
                    try:
                        current_regime = regime_detector.get_current_regime(symbol)
                        if current_regime is not None:
                            regime_str = current_regime.value
                    except Exception as e:
                        logger.debug(
                            f"Could not resolve regime for grid registration: {e}"
                        )
                if not regime_str:
                    regime_str = (
                        signal.market_state.name
                        if hasattr(signal.market_state, "name")
                        else str(signal.market_state)
                    )

                # Order IDs collected at placement time drive strict fill
                # attribution (overlay strategies trade the same symbols).
                placed_order_ids = (result.get("buy_order_ids") or []) + (
                    result.get("sell_order_ids") or []
                )

                self.grid_lifecycle.register_new_grid(
                    symbol=symbol,
                    grid_capital=capital_allocated,
                    emergency_stop_price=emergency_stop_price,
                    regime=regime_str,
                    atr=(signal.indicators or {}).get("atr", 0),
                    spacing=result.get("grid_spacing") or signal.spacing or 0,
                    num_levels=result.get("num_levels") or signal.grid_levels or 10,
                    center_price=center_price,
                    order_ids=placed_order_ids,
                )
                logger.info(
                    f"✅ Grid registered with GridLifecycleManager for {symbol}"
                )

            else:
                error_msg = result.get("error", "Unknown error")
                logger.error(
                    f"❌ Grid order placement failed for {symbol}: {error_msg}"
                )

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
            # RiskManager returns "allocated_amount" (not "capital_allocated")
            capital = allocation_result.get("allocated_amount", 0)

            if capital <= 0:
                return {"success": False, "error": "No capital allocated"}

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

            # Place grid orders
            buy_order_ids = []
            sell_order_ids = []
            exchange = _resolve_exchange(self)

            # Place BUY orders (normalized vocabulary via exchange adapter)
            for level in grid_levels["buy_levels"]:
                try:
                    response = exchange.place_order(
                        symbol=symbol,
                        side=OrderSide.BUY,
                        quantity=level["quantity"],
                        order_type=OrderType.LIMIT,
                        price=level["price"],
                    )

                    # Extract order data from response wrapper {"success": bool, "data": {...}}
                    order_data = (
                        response.get("data", {}) if isinstance(response, dict) else {}
                    )
                    order_id = order_data.get("order_id") or order_data.get("id")

                    if order_id:
                        buy_order_ids.append(str(order_id))
                        logger.info(
                            f"  ✅ BUY order placed: {level['quantity']} @ ${level['price']:.4f} (ID: {order_id})"
                        )
                    elif response.get("success") is False:
                        error_msg = response.get("error", "Unknown API error")
                        logger.error(f"  ❌ BUY order rejected: {error_msg}")
                except Exception as e:
                    logger.error(
                        f"  ❌ Failed to place BUY order @ ${level['price']:.4f}: {e}"
                    )

            # Place SELL orders (normalized vocabulary via exchange adapter)
            for level in grid_levels["sell_levels"]:
                try:
                    response = exchange.place_order(
                        symbol=symbol,
                        side=OrderSide.SELL,
                        quantity=level["quantity"],
                        order_type=OrderType.LIMIT,
                        price=level["price"],
                    )

                    # Extract order data from response wrapper {"success": bool, "data": {...}}
                    order_data = (
                        response.get("data", {}) if isinstance(response, dict) else {}
                    )
                    order_id = order_data.get("order_id") or order_data.get("id")

                    if order_id:
                        sell_order_ids.append(str(order_id))
                        logger.info(
                            f"  ✅ SELL order placed: {level['quantity']} @ ${level['price']:.4f} (ID: {order_id})"
                        )
                    elif response.get("success") is False:
                        error_msg = response.get("error", "Unknown API error")
                        logger.error(f"  ❌ SELL order rejected: {error_msg}")
                except Exception as e:
                    logger.error(
                        f"  ❌ Failed to place SELL order @ ${level['price']:.4f}: {e}"
                    )

            # Return results, including the grid geometry actually used so the
            # caller can register the grid with real values (spacing drives
            # fill replenishment; lowest buy drives the emergency stop).
            success = len(buy_order_ids) > 0 or len(sell_order_ids) > 0
            lowest_buy_price = min(
                (level["price"] for level in grid_levels["buy_levels"]), default=0
            )
            return {
                "success": success,
                "buy_orders": len(buy_order_ids),
                "sell_orders": len(sell_order_ids),
                "buy_order_ids": buy_order_ids,
                "sell_order_ids": sell_order_ids,
                "grid_spacing": grid_levels.get("grid_spacing", 0),
                "center_price": grid_levels.get("current_price", 0),
                "lowest_buy_price": lowest_buy_price,
                "num_levels": (
                    len(grid_levels["buy_levels"]) + len(grid_levels["sell_levels"])
                ),
            }

        except Exception as e:
            logger.error(f"Error placing grid orders: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    def _execute_standard_signal_coordinated(
        self, signal, allocation_result, log_entry=None
    ):
        """
        Execute standard (non-grid) signal through ExecutionLayer.

        PHASE 2: Coordinator delegates execution to ExecutionLayer for precise entry timing.
        ExecutionLayer monitors 1m/5m candles for optimal entry conditions.
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

            logger.info(
                f"🔹 Executing {signal.strategy.name} signal for {symbol}: "
                f"{signal.side.name} {quantity:.6f} @ ${signal.entry_price:.4f} "
                f"(capital: ${capital_allocated:.2f})"
            )

            if quantity <= 0:
                logger.error(f"❌ Invalid quantity for {symbol}: {quantity}")
                self.signal_logger.log_signal_failed(
                    signal=signal,
                    error="Invalid quantity (<=0)",
                    notes=f"Capital: ${capital_allocated:.2f}, Price: ${signal.entry_price:.4f}",
                )
                return

            # Refine signal through ExecutionLayer before execution
            refined_signal = self.execution_layer.refine_entry(signal, symbol)
            if refined_signal is None:
                logger.info(
                    f"⚠️ ExecutionLayer skipped entry for {symbol} - timing not favorable"
                )
                self.signal_logger.log_signal_rejected(
                    signal=signal,
                    reason="ExecutionLayer timing skip",
                    notes="1m/5m timing conditions not met",
                )
                return

            # Use refined signal for execution
            signal = refined_signal

            # Generate + persist the client order id BEFORE transmission so
            # an ambiguous response (timeout, bare ack) can be looked up.
            client_order_id = uuid.uuid4().hex[:32]
            logger.info(
                f"Submitting {symbol} {signal.side.name} {quantity:.6f} "
                f"client_order_id={client_order_id}"
            )
            self.signal_logger.log_signal_pending(
                signal=signal,
                client_order_id=client_order_id,
                quantity=quantity,
                notes=f"Capital allocated: ${capital_allocated:.2f}",
            )

            # Use market order for immediate execution.  Routed through the
            # exchange adapter: normalized OrderSide/OrderType go in, the
            # adapter owns the exchange-native side/amount conversion.
            exchange = _resolve_exchange(self)
            # Attach the signal's stop to the entry itself on venues that
            # hold stops server-side (T4); the fill path verifies it landed.
            order_kwargs: Dict[str, Any] = {}
            entry_stop = _signal_stop_price(signal)
            if entry_stop is not None and venue_stops_supported(exchange):
                order_kwargs["stop_loss"] = entry_stop
            order_response = exchange.place_order(
                symbol=symbol,
                side=signal.side,
                quantity=quantity,
                order_type=OrderType.MARKET,
                client_order_id=client_order_id,
                **order_kwargs,
            )

            # Debug: log raw API response to diagnose parsing issues
            logger.info(
                f"📡 Raw order response for {symbol}: type={type(order_response).__name__}, "
                f"value={str(order_response)[:200]}"
            )

            result = OrderResult.from_ack(order_response)
            if result.client_order_id is None:
                result.client_order_id = client_order_id

            if not result.accepted:
                error_msg = result.error or "Unknown error"
                logger.error(f"❌ Order execution failed for {symbol}: {error_msg}")
                self.signal_logger.log_signal_failed(
                    signal=signal,
                    error=error_msg,
                    notes=(
                        f"Attempted quantity: {quantity}; "
                        f"client_order_id={client_order_id}"
                    ),
                )
                return

            # The ack only says ACCEPTED.  Record the entry from actual
            # fills (or park it as pending) - never from the request.
            self._record_entry_outcome(
                signal, symbol, quantity, capital_allocated, result, exchange
            )

        except Exception as e:
            logger.opt(exception=True).error(
                f"Error executing standard signal for {signal.asset}: {type(e).__name__}: {e}"
            )
            self.signal_logger.log_signal_failed(
                signal=signal,
                error=f"{type(e).__name__}: {e}",
                notes="Exception during execution",
            )

    def _lookup_entry_fill(
        self, exchange, symbol: str, result: OrderResult, quantity: float
    ) -> OrderResult:
        """Query the exchange for an accepted order's fills (UNKNOWN on error)."""
        if result.has_fills:
            return result
        try:
            fill = exchange.get_order_fill(
                symbol,
                order_id=result.order_id,
                client_order_id=result.client_order_id,
                requested_quantity=quantity,
            )
        except Exception as exc:  # noqa: BLE001 - lookup failure is "unknown"
            logger.warning(f"Fill lookup failed for {symbol} {result.order_id}: {exc}")
            fill = None
        if not isinstance(fill, OrderResult):
            return OrderResult(
                order_id=result.order_id,
                client_order_id=result.client_order_id,
                status=OrderResultStatus.UNKNOWN.value,
                error="fill lookup unavailable",
            )
        if fill.order_id is None:
            fill.order_id = result.order_id
        if fill.client_order_id is None:
            fill.client_order_id = result.client_order_id
        return fill

    def _record_entry_outcome(
        self,
        signal: Signal,
        symbol: str,
        quantity: float,
        capital_allocated: float,
        result: OrderResult,
        exchange,
    ) -> None:
        """Record an ACCEPTED entry from actual fills, or park it as pending."""
        fill = self._lookup_entry_fill(exchange, symbol, result, quantity)
        if fill.has_fills:
            self._record_entry_fill(
                signal, symbol, quantity, capital_allocated, fill, False
            )
            return
        if fill.rejected:
            self._record_entry_rejected(signal, symbol, quantity, fill)
            return
        key = result.client_order_id or result.order_id or uuid.uuid4().hex
        self._pending_entries[key] = {
            "signal": signal,
            "symbol": symbol,
            "quantity": quantity,
            "capital": capital_allocated,
            "order_id": result.order_id,
            "client_order_id": result.client_order_id,
            "submitted_at": time.time(),
            "lookups": 1,
        }
        logger.warning(
            f"{symbol} order {result.order_id} accepted but not yet filled "
            f"(status={fill.status}) - recorded as PENDING, not executed"
        )
        self.signal_logger.log_signal_pending(
            signal=signal,
            client_order_id=result.client_order_id or "",
            quantity=quantity,
            order_id=result.order_id or "",
            notes=f"accepted, awaiting fill (status={fill.status})",
        )

    def _record_entry_fill(
        self,
        signal: Signal,
        symbol: str,
        quantity: float,
        capital_allocated: float,
        fill: OrderResult,
        delayed: bool,
    ) -> None:
        """Record executed price/quantity from confirmed fills."""
        executed_qty = fill.filled_quantity
        executed_price = fill.avg_fill_price or signal.entry_price
        partial = executed_qty + 1e-9 < quantity
        execution_result = "partial_fill" if partial else "success"
        if delayed:
            execution_result = "delayed_partial_fill" if partial else "delayed_fill"
        logger.info(
            f"[FILLED] {symbol}: ID={fill.order_id}, "
            f"qty={executed_qty:.6f}/{quantity:.6f}, VWAP=${executed_price:.4f} "
            f"({execution_result})"
        )
        self.signal_logger.log_signal_executed(
            signal=signal,
            order_id=str(fill.order_id or ""),
            filled_price=executed_price,
            filled_quantity=executed_qty,
            execution_result=execution_result,
            notes=(
                f"Capital allocated: ${capital_allocated:.2f}; "
                f"requested {quantity:.6f}; client_order_id={fill.client_order_id}"
            ),
        )
        # The position now exists on the venue: make sure a stop does too.
        # (getattr: duck-typed stand-ins may bind only the fill methods.)
        protect = getattr(self, "_ensure_entry_protection", None)
        try:
            if callable(protect):
                protect(signal, symbol, fill, executed_price)
        except Exception as exc:  # noqa: BLE001 - never raise into the loop
            logger.error(
                f"UNPROTECTED {symbol}: stop verification raised "
                f"{type(exc).__name__}: {exc}"
            )
        self.event_bus.publish_event(
            event_type=EventType.ORDER_PLACED,
            data={
                "symbol": symbol,
                "order_id": fill.order_id,
                "client_order_id": fill.client_order_id,
                "side": signal.side.name,
                "quantity": executed_qty,
                "requested_quantity": quantity,
                "price": executed_price,
                "fill_status": fill.status,
                "timestamp": time.time(),
            },
            source="trading_bot",
        )

    def _record_entry_rejected(
        self, signal: Signal, symbol: str, quantity: float, fill: OrderResult
    ) -> None:
        """Record an order the exchange accepted and later rejected/cancelled."""
        logger.error(
            f"[REJECTED] {symbol} order {fill.order_id} rejected after accept: {fill.error}"
        )
        self.signal_logger.log_signal_failed(
            signal=signal,
            error=f"rejected_after_accept: {fill.error}",
            notes=(
                f"order_id={fill.order_id}; client_order_id={fill.client_order_id}; "
                f"requested {quantity:.6f}"
            ),
        )

    def _complete_pending_entries(self) -> None:
        """Resolve entries parked as pending on earlier cycles.

        Each pending entry is looked up again; a fill records it as a
        delayed (or delayed partial) fill, a cancel as rejected-after-
        accept, and after ENTRY_FILL_MAX_LOOKUPS unresolved lookups it
        is recorded as unconfirmed and left to the reconciler.
        """
        if not self._pending_entries:
            return
        exchange = _resolve_exchange(self)
        for key in list(self._pending_entries):
            entry = self._pending_entries[key]
            probe = OrderResult(
                success=True,
                accepted=True,
                order_id=entry["order_id"],
                client_order_id=entry["client_order_id"],
                status=OrderResultStatus.ACCEPTED.value,
            )
            fill = self._lookup_entry_fill(
                exchange, entry["symbol"], probe, entry["quantity"]
            )
            entry["lookups"] += 1
            if fill.has_fills:
                self._pending_entries.pop(key, None)
                self._record_entry_fill(
                    entry["signal"],
                    entry["symbol"],
                    entry["quantity"],
                    entry["capital"],
                    fill,
                    True,
                )
            elif fill.rejected:
                self._pending_entries.pop(key, None)
                self._record_entry_rejected(
                    entry["signal"], entry["symbol"], entry["quantity"], fill
                )
            elif entry["lookups"] >= self._entry_fill_max_lookups:
                self._pending_entries.pop(key, None)
                logger.error(
                    f"{entry['symbol']} order {entry['order_id']} still unconfirmed "
                    f"after {entry['lookups']} lookups - recording as unconfirmed; "
                    f"position reconciler owns it now"
                )
                self.signal_logger.log_signal_failed(
                    signal=entry["signal"],
                    error=f"fill_unconfirmed after {entry['lookups']} lookups",
                    notes=(
                        f"order_id={entry['order_id']}; "
                        f"client_order_id={entry['client_order_id']}"
                    ),
                )

    # ------------------------------------------------------------------
    # Venue-side stop protection (live-readiness audit T4)
    # ------------------------------------------------------------------

    def _ensure_entry_protection(
        self, signal: Signal, symbol: str, fill: OrderResult, entry_price: float
    ) -> Dict[str, Any]:
        """Verify a venue stop exists for a filled entry; install or act.

        Order of operations: on venues with server-side stops, look for
        the TP/SL row the attached stop should have produced; if absent,
        attempt ONE standalone install; if that is rejected too, apply
        ``VENUE_STOP_FAILURE_POLICY``.  The outcome is persisted on the
        position record as entry_order_id / venue_stop_id /
        venue_stop_price / venue_stop_state.

        Args:
            signal: The executed signal (carries stop_loss and side).
            symbol: Trading symbol.
            fill: Confirmed fill (order id + executed quantity).
            entry_price: Executed price (for the insert path).

        Returns:
            The persisted protection record.
        """
        exchange = _resolve_exchange(self)
        side = position_side_lower(signal.side).upper()
        stop_price = _signal_stop_price(signal)
        quantity = float(fill.filled_quantity or 0.0)
        record: Dict[str, Any] = {
            "entry_order_id": fill.order_id,
            "venue_stop_id": None,
            "venue_stop_price": stop_price,
            "venue_stop_state": STOP_STATE_UNSUPPORTED,
        }
        if venue_stops_supported(exchange):
            row = self._verify_venue_stop(exchange, symbol, side)
            if row is not None:
                record["venue_stop_id"] = row.get("tpsl_id") or None
                record["venue_stop_price"] = row.get("sl_trigger_price") or stop_price
                record["venue_stop_state"] = STOP_STATE_ATTACHED
                logger.info(
                    f"{symbol} {side} protected by attached venue stop "
                    f"{record['venue_stop_id']} @ {record['venue_stop_price']}"
                )
            elif stop_price is not None and quantity > 0:
                result = exchange.install_stop(symbol, side, quantity, stop_price)
                if result.accepted:
                    record["venue_stop_id"] = result.order_id
                    record["venue_stop_state"] = STOP_STATE_STANDALONE
                    logger.warning(
                        f"{symbol} {side}: attached stop absent, installed standalone "
                        f"venue stop {result.order_id} @ {stop_price}"
                    )
                else:
                    self._apply_stop_failure_policy(
                        symbol,
                        side,
                        quantity,
                        stop_price,
                        record,
                        f"install rejected: {result.error}",
                    )
            else:
                self._apply_stop_failure_policy(
                    symbol,
                    side,
                    quantity,
                    stop_price,
                    record,
                    "signal carries no stop price"
                    if stop_price is None
                    else "no fill quantity",
                )
        elif stop_price is None:
            logger.error(
                f"UNPROTECTED {symbol} {side}: signal carries no stop price and the "
                f"venue holds no stops - nothing to enforce locally"
            )
        else:
            logger.warning(
                f"{symbol} {side}: venue has no server-side stops; local "
                f"enforcement only (stop {stop_price})"
            )
        self._persist_protection(symbol, side, record, quantity, entry_price)
        return record

    def _list_venue_stops(
        self, exchange, symbol: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Pending venue stop rows for ``symbol``; None when the venue is unreachable."""
        try:
            return list(exchange.list_stops(symbol) or [])
        except Exception as exc:  # noqa: BLE001 - unknown, not "no stops"
            logger.warning(f"Could not list venue stops for {symbol}: {exc}")
            return None

    def _verify_venue_stop(
        self, exchange, symbol: str, side: str
    ) -> Optional[Dict[str, Any]]:
        """Return the pending venue stop row protecting ``side``, if any.

        A transport failure is treated as "not verified" (None) so the
        entry path installs a standalone stop rather than assuming
        protection; the repair sweep uses ``_list_venue_stops`` directly
        so it can tell "unreachable" from "absent".
        """
        rows = self._list_venue_stops(exchange, symbol)
        if rows is None:
            return None
        return find_stop_row(rows, side)

    def _apply_stop_failure_policy(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: Optional[float],
        record: Dict[str, Any],
        why: str,
    ) -> None:
        """Act on a position that could not be protected on the venue.

        ``close``: flatten it reduce-only right now (default; an
        unattended live bot must not carry an unprotected position).
        ``local``: keep it, mark ``missing``; the loop check enforces the
        stored stop and logs ERROR every cycle while unprotected.
        """
        policy = getattr(self, "_venue_stop_policy", STOP_FAILURE_POLICY_CLOSE)
        record["venue_stop_state"] = STOP_STATE_MISSING
        logger.error(
            f"UNPROTECTED {symbol} {side} qty={quantity:.6f}: venue stop could not "
            f"be installed ({why}); VENUE_STOP_FAILURE_POLICY={policy}"
        )
        if policy != STOP_FAILURE_POLICY_CLOSE:
            return
        closed = False
        if quantity > 0:
            closed = bool(self._emergency_close_position(symbol, side, quantity))
        record["closed_by_policy"] = closed
        if closed:
            logger.error(
                f"{symbol} {side}: closed reduce-only under failure policy 'close'"
            )
        else:
            logger.error(
                f"{symbol} {side}: policy close could NOT flatten the position - "
                f"it remains open and UNPROTECTED; local enforcement continues"
            )

    def _persist_protection(
        self,
        symbol: str,
        side: str,
        record: Dict[str, Any],
        quantity: Optional[float] = None,
        entry_price: Optional[float] = None,
    ) -> None:
        """Store the protection record in memory and (when available) the DB."""
        records = getattr(self, "_protection_records", None)
        if records is None:
            records = {}
            self._protection_records = records
        records[(symbol, side)] = dict(record)
        db = getattr(self, "db", None)
        writer = getattr(db, "record_position_protection", None)
        if not callable(writer):
            return
        try:
            writer(symbol, side, record, quantity=quantity, entry_price=entry_price)
        except Exception as exc:  # noqa: BLE001 - persistence must not break trading
            logger.error(f"Could not persist stop record for {symbol} {side}: {exc}")

    def _protection_rows(self) -> List[Dict[str, Any]]:
        """Open positions with their stop columns (DB rows + memory mirror)."""
        rows: List[Dict[str, Any]] = []
        seen = set()
        db = getattr(self, "db", None)
        if db is not None and hasattr(db, "get_positions"):
            try:
                for row in db.get_positions() or []:
                    if not isinstance(row, dict) or not row.get("symbol"):
                        continue
                    key = (str(row["symbol"]), str(row.get("side", "")).upper())
                    seen.add(key)
                    rows.append(dict(row))
            except Exception as exc:  # noqa: BLE001 - fall back to memory
                logger.warning(f"Could not read positions for stop enforcement: {exc}")
        for key, record in (getattr(self, "_protection_records", None) or {}).items():
            if key in seen:
                continue
            rows.append({"symbol": key[0], "side": key[1], **record})
        return rows

    def _is_migrated_symbol(self, symbol: str, side: str) -> bool:
        """True when MigratedPositionManager owns this position's stop."""
        risk = getattr(self, "risk_manager", None)
        checker = getattr(risk, "get_migrated_positions", None)
        if not callable(checker):
            return False
        try:
            for pos in checker(symbol) or []:
                if position_side_lower(pos.get("side")) == side.lower():
                    return True
        except Exception:  # noqa: BLE001 - stand-ins may not support it
            return False
        return False

    def _current_price_for(self, symbol: str) -> Optional[float]:
        """Latest price via the WS cache, or None when unavailable."""
        try:
            ticker = self._get_ticker_ws(symbol)
            price = float(ticker.get("last", ticker.get("price", 0)) or 0)
        except Exception:  # noqa: BLE001 - unknown price -> no action
            return None
        return price if price > 0 else None

    def _enforce_local_stops(self) -> int:
        """Close any position the venue is not protecting once its stop is hit.

        Covers ``venue_stop_state`` in (missing, unsupported) with a
        stored ``venue_stop_price``.  Positions owned by the migrated
        position manager are skipped (it runs its own trailing check).
        A ``missing`` position is logged at ERROR every loop.

        Returns:
            Number of positions closed this cycle.
        """
        closed = 0
        for row in self._protection_rows():
            state = row.get("venue_stop_state")
            if state not in LOCAL_ONLY_STATES:
                continue
            stop_price = row.get("venue_stop_price")
            try:
                quantity = float(row.get("quantity") or 0.0)
            except (TypeError, ValueError):
                quantity = 0.0
            symbol = str(row["symbol"])
            side = str(row.get("side", "")).upper()
            if not stop_price or self._is_migrated_symbol(symbol, side):
                continue
            if state == STOP_STATE_MISSING:
                logger.error(
                    f"UNPROTECTED {symbol} {side}: no venue stop; enforcing "
                    f"local stop {stop_price} only"
                )
            price = self._current_price_for(symbol)
            if price is None or not stop_hit(side, stop_price, price):
                continue
            logger.error(
                f"LOCAL STOP HIT {symbol} {side}: price {price} crossed stop "
                f"{stop_price} - closing reduce-only"
            )
            if quantity <= 0:
                quantity = self._exchange_quantity_for(symbol, side)
            if self._emergency_close_position(symbol, side, quantity):
                closed += 1
                self._mark_position_closed(symbol, side, price)
        return closed

    def _exchange_quantity_for(self, symbol: str, side: str) -> float:
        """Current exchange quantity for a position (0 when unknown)."""
        from .exit_sizing import remaining_exchange_quantity

        try:
            return float(
                remaining_exchange_quantity(self.client, symbol, side.lower()) or 0.0
            )
        except Exception:  # noqa: BLE001 - reduce-only close clamps anyway
            return 0.0

    def _mark_position_closed(self, symbol: str, side: str, price: float) -> None:
        """Drop the local record after a local-stop close."""
        records = getattr(self, "_protection_records", None) or {}
        records.pop((symbol, side), None)
        db = getattr(self, "db", None)
        closer = getattr(db, "close_position", None)
        if callable(closer):
            try:
                closer(symbol, side, exit_price=price)
            except Exception as exc:  # noqa: BLE001 - the loop sync catches up
                logger.warning(f"Could not close DB position {symbol} {side}: {exc}")

    def _stored_stop_price(
        self, symbol: str, side: str, db_row: Optional[Dict[str, Any]]
    ) -> Optional[float]:
        """Best known stop for a position: DB columns, memory, trailing state."""
        candidates: List[Any] = []
        if db_row:
            candidates.extend(
                db_row.get(key)
                for key in ("venue_stop_price", "stop_loss", "stop_price")
            )
        record = (getattr(self, "_protection_records", None) or {}).get((symbol, side))
        if record:
            candidates.append(record.get("venue_stop_price"))
        manager = getattr(self, "migrated_position_manager", None)
        trailing = getattr(manager, "_trailing_stops", None) or {}
        candidates.append(trailing.get(symbol, {}).get(side.lower()))
        for value in candidates:
            try:
                if value and float(value) > 0:
                    return float(value)
            except (TypeError, ValueError):
                continue
        return None

    def _repair_venue_stops(self, reason: str = "sweep") -> Dict[str, Any]:
        """Re-install a venue stop for every open position lacking one.

        Runs once at startup after the first position sync and on the
        hourly reconciliation cadence.  For each exchange position: a
        pending TP/SL row means protected (the DB record is refreshed);
        otherwise the stored stop (venue_stop_price, else the position's
        stored stop, else the trailing level) is installed as a
        standalone stop; with no stored stop the position is skipped
        with an ERROR.  Never raises.

        Args:
            reason: Free-text tag for the log lines.

        Returns:
            Summary counts {"protected", "installed", "failed", "skipped"}.
        """
        summary: Dict[str, Any] = {
            "protected": 0,
            "installed": 0,
            "failed": 0,
            "skipped": 0,
        }
        exchange = _resolve_exchange(self)
        if not venue_stops_supported(exchange):
            summary["unsupported"] = True
            return summary
        try:
            positions = self.client.get_positions() or []
        except Exception as exc:  # noqa: BLE001 - cannot see the exchange
            logger.error(f"Stop repair ({reason}): positions unavailable: {exc}")
            summary["error"] = str(exc)
            return summary
        db_rows = {
            (str(r["symbol"]), str(r.get("side", "")).upper()): r
            for r in self._protection_rows()
        }
        for pos in positions:
            try:
                self._repair_one_position(exchange, pos, db_rows, summary, reason)
            except Exception as exc:  # noqa: BLE001 - one position must not stop the sweep
                summary["failed"] += 1
                logger.error(
                    f"Stop repair ({reason}) failed for {pos.get('symbol')}: {exc}"
                )
        logger.info(f"Venue stop repair ({reason}): {summary}")
        return summary

    def _repair_one_position(
        self,
        exchange,
        pos: Dict[str, Any],
        db_rows: Dict[tuple, Dict[str, Any]],
        summary: Dict[str, Any],
        reason: str,
    ) -> None:
        """Repair-sweep body for a single exchange position."""
        quantity = float(pos.get("quantity") or pos.get("amount") or 0.0)
        symbol = str(pos.get("symbol") or "")
        if quantity <= 0 or not symbol:
            return
        side = position_side_lower(pos.get("side", "long")).upper()
        db_row = db_rows.get((symbol, side))
        rows = self._list_venue_stops(exchange, symbol)
        if rows is None:
            # Unreachable venue: do not stack a second stop on a guess.
            summary["failed"] += 1
            logger.error(
                f"Stop repair ({reason}): could not list venue stops for {symbol} "
                f"{side}; leaving it for the next sweep"
            )
            return
        row = find_stop_row(rows, side)
        record: Dict[str, Any] = {
            "entry_order_id": (db_row or {}).get("entry_order_id"),
        }
        if row is not None:
            summary["protected"] += 1
            current_state = (db_row or {}).get("venue_stop_state")
            record.update(
                venue_stop_id=row.get("tpsl_id") or (db_row or {}).get("venue_stop_id"),
                venue_stop_price=row.get("sl_trigger_price")
                or (db_row or {}).get("venue_stop_price"),
                venue_stop_state=current_state
                if current_state in PROTECTED_STATES
                else STOP_STATE_STANDALONE,
            )
            self._persist_protection(
                symbol, side, record, quantity, pos.get("entry_price")
            )
            return
        stop_price = self._stored_stop_price(symbol, side, db_row)
        if stop_price is None:
            summary["skipped"] += 1
            logger.error(
                f"Stop repair ({reason}): {symbol} {side} has NO venue stop and no "
                f"stored stop price - cannot protect it; operator action required"
            )
            return
        result = exchange.install_stop(symbol, side, quantity, stop_price)
        if result.accepted:
            summary["installed"] += 1
            record.update(
                venue_stop_id=result.order_id,
                venue_stop_price=stop_price,
                venue_stop_state=STOP_STATE_STANDALONE,
            )
            logger.warning(
                f"Stop repair ({reason}): re-installed venue stop for {symbol} {side} "
                f"@ {stop_price} (id {result.order_id})"
            )
        else:
            summary["failed"] += 1
            record.update(
                venue_stop_id=None,
                venue_stop_price=stop_price,
                venue_stop_state=STOP_STATE_MISSING,
            )
            logger.error(
                f"Stop repair ({reason}): could not install venue stop for {symbol} "
                f"{side} @ {stop_price}: {result.error}; local enforcement only"
            )
        self._persist_protection(symbol, side, record, quantity, pos.get("entry_price"))

    def _reconcile_after_ambiguous_close(self) -> None:
        """Force a full position reconciliation right now.

        Called by MigratedPositionManager after a close whose outcome is
        ambiguous (timeout / exception) so the reconciler runs before
        any accounting is applied.
        """
        try:
            positions = self.client.get_positions()
        except Exception as exc:  # noqa: BLE001 - cannot see the exchange
            logger.error(
                f"Reconcile after ambiguous close: positions unavailable: {exc}"
            )
            return
        self._last_reconciliation_time = 0.0
        self._maybe_run_full_reconciliation(exchange_positions=positions)

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

    def _handle_regime_changed_for_grids(self, event):
        """
        Handle REGIME_CHANGED: unwind active grids when the new regime
        disallows grid trading.

        Delegates the allowed-regime decision and the unwind itself to
        GridLifecycleManager.handle_regime_change. Fetches 4h market data so
        the partial unwind can keep trend-aligned positions; falls back to a
        full exit inside the lifecycle manager when data is unavailable.

        Args:
            event: Event whose data contains symbol and new_regime.
        """
        try:
            data = event.data if hasattr(event, "data") else {}
            symbol = data.get("symbol") if isinstance(data, dict) else None
            new_regime = data.get("new_regime") if isinstance(data, dict) else None

            if not symbol or not new_regime:
                return
            if not self.grid_lifecycle:
                return

            market_data = None
            try:
                fetched = self.multi_tf_fetcher.get_candles_multi_tf(
                    symbol, timeframes=["4h"]
                )
                market_data = (fetched or {}).get("4h")
            except Exception as e:
                logger.warning(
                    f"Could not fetch 4h data for grid unwind of {symbol}: {e}"
                )

            unwound = self.grid_lifecycle.handle_regime_change(
                symbol, new_regime, market_data
            )
            if unwound:
                logger.warning(
                    f"Grid unwind executed for {symbol} after regime change "
                    f"to {new_regime}"
                )
        except Exception as e:
            logger.error(f"Error handling REGIME_CHANGED for grids: {e}", exc_info=True)

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

    # Removed (2026-05-02): _check_signals() — legacy loop-driven signal pull.
    # Active path is event-driven via _generate_and_publish_signals() →
    # SIGNAL_GENERATED event → _handle_signal_generated() → _coordinate_signal_execution().
    # The legacy method was unreachable from the main loop and contained a stale
    # call to _execute_signal() that used "BUY"/"SELL"/"MARKET" (Pacifica wants lowercase).
    # See trading_bot.git history for the original implementation.

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
        Get current account balance.

        Returns:
            Account balance in USD.
        """
        try:
            # Balance read via exchange adapter: the "balance" vs
            # "account_equity" string extraction now lives in the adapter.
            balance = _resolve_exchange(self).get_balance()
            logging.info(f"🔍 Raw balance response: {balance.raw}")

            equity = balance.equity
            logging.info(f"🔍 Final account balance: ${equity:.2f}")

            if equity <= 0:
                logging.warning(
                    f"⚠️ Account balance is ${equity:.2f} - this will block all executions!"
                )

            return equity
        except Exception as e:
            logging.error(f"❌ Error getting account balance: {e}")
            return 0.0

    def _get_current_exposure(self) -> float:
        """
        Get current total exposure across all positions.

        Returns:
            Total exposure in USD.

        Raises:
            RuntimeError: When the exchange positions cannot be read.  An
                unknown exposure must never be reported as zero, or the
                exposure limits are bypassed while the venue is unreachable;
                every caller already handles the exception.
        """
        try:
            # Normalized positions from the exchange adapter (ghost
            # qty==0 entries already filtered, sides normalized).
            positions = _resolve_exchange(self).get_positions()
            if not positions:
                return 0.0

            total_exposure = 0.0
            for pos in positions:
                quantity = pos.quantity
                # Get current price (fallback to entry price if unavailable)
                symbol = pos.symbol
                entry_price = pos.entry_price

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
            raise RuntimeError(f"Exposure unavailable: {e}") from e

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

    # Removed (2026-05-02): _execute_signal() and _execute_standard_signal() —
    # legacy execution path. Replaced by _coordinate_signal_execution() →
    # _execute_standard_signal_coordinated() which routes through ExecutionLayer.
    # The deleted _execute_standard_signal contained a Pacifica side-name bug
    # ("BUY"/"SELL"/"MARKET" instead of lowercase "buy"/"sell"/"market") that
    # would have produced rejected orders if any caller had still reached it.

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

    def _emergency_close_position(
        self, symbol: str, side: str, quantity: float
    ) -> bool:
        """
        Emergency close a position immediately at market price.

        Used when emergency stop is triggered for grid trading, by the
        venue-stop failure policy and by local stop enforcement.

        Args:
            symbol: Symbol to close
            side: Position side (LONG/SHORT)
            quantity: Position size

        Returns:
            True when the close order was accepted (or the exchange was
            already flat), False when it was rejected or raised.
        """
        try:
            # Determine order side (opposite of position side).  Uses the
            # normalized OrderSide enum via the exchange adapter - the old
            # direct call passed "SELL"/"BUY" + "MARKET", which the native
            # Pacifica client rejected (order_type mismatch) and would have
            # side-mangled ("BUY" mapped to "ask").
            position_side = "long" if str(side).upper() == "LONG" else "short"
            close_side = OrderSide.SELL if position_side == "long" else OrderSide.BUY

            # Size from the exchange's CURRENT position: an exchange stop or
            # a manual action may already have reduced or closed it.
            plan = plan_close_quantity(self.client, symbol, position_side, quantity)
            if plan.already_flat:
                logging.warning(
                    f"EMERGENCY CLOSE {symbol} {position_side}: exchange already "
                    f"flat - no order sent"
                )
                return True
            if plan.clamped:
                logging.warning(
                    f"EMERGENCY CLOSE {symbol}: clamping {quantity} to exchange "
                    f"quantity {plan.quantity}"
                )

            logging.critical(
                f"EMERGENCY CLOSE: {close_side.name} {plan.quantity} {symbol} "
                f"@ MARKET (reduce-only)"
            )

            # Reduce-only through the exchange adapter: can never flip the book
            order = _resolve_exchange(self).place_order(
                symbol=symbol,
                side=close_side,
                quantity=plan.quantity,
                order_type=OrderType.MARKET,
                reduce_only=True,
            )
            result = OrderResult.from_ack(order)
            if not result.accepted:
                logging.error(
                    f"EMERGENCY CLOSE REJECTED for {symbol}: {result.error} - "
                    f"position remains open"
                )
                return False

            logging.info(f"Emergency close order accepted: {order}")
            return True

        except Exception as e:
            logging.error(f"Error placing emergency close order: {e}")
            return False

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

            # Run pre-trade assertions before committing any orders.
            # Uses live exchange instrument info for the minimum order size check.
            try:
                self._assert_grid_pre_trade(
                    symbol=symbol,
                    grid_spacing=grid_levels["grid_spacing"],
                    num_levels=(
                        len(grid_levels["buy_levels"]) + len(grid_levels["sell_levels"])
                    ),
                    quantity_per_level=grid_levels["quantity_per_level"],
                    signal=signal,
                )
            except AssertionError as ae:
                logging.error(f"Grid pre-trade check FAILED for {symbol}: {ae}")
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
        self, signal: Signal, total_capital: float
    ) -> Dict[str, List[Dict]]:
        """Calculate grid price levels per Grid Trading Brief specifications."""
        try:
            # Get current price
            ticker = self._get_ticker_ws(signal.asset)
            current_price = float(ticker.get("last", signal.entry_price))
            if current_price <= 0:
                logging.error(
                    f"Invalid current price {current_price} for {signal.asset}"
                )
                return {}

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

            # Determine lot size and tick size based on price magnitude
            # Using conservative tick sizes to match Pacifica API requirements
            if current_price >= 100:
                tick_decimals = 2  # BTC: 0.01
                lot_decimals = 4  # 0.0001
            elif current_price >= 1:
                tick_decimals = 3  # AVAX, XRP: 0.001
                lot_decimals = 2  # 0.01
            else:
                tick_decimals = 5  # SUI: 0.00001
                lot_decimals = 1  # 0.1 for small coins

            # Round quantity to lot size
            quantity_per_level = round(quantity_per_level, lot_decimals)
            min_lot = 10 ** (-lot_decimals)
            if quantity_per_level < min_lot:
                quantity_per_level = min_lot

            # Generate buy levels (below current price)
            buy_levels = []
            for i in range(1, num_levels // 2 + 1):
                price = current_price - (i * grid_spacing)
                if price > 0:  # Only add positive prices
                    price = round(price, tick_decimals)
                    buy_levels.append({"price": price, "quantity": quantity_per_level})

            # Generate sell levels (above current price)
            sell_levels = []
            for i in range(1, num_levels // 2 + 1):
                price = current_price + (i * grid_spacing)
                price = round(price, tick_decimals)
                sell_levels.append({"price": price, "quantity": quantity_per_level})

            spacing_pct = (
                (grid_spacing / current_price * 100) if current_price > 0 else 0
            )
            logging.info(
                f"Grid levels calculated: {len(buy_levels)} BUY, {len(sell_levels)} SELL, "
                f"spacing: ${grid_spacing:.4f} ({spacing_pct:.2f}%), qty/level: {quantity_per_level:.4f}"
            )

            return {
                "buy_levels": buy_levels,
                "sell_levels": sell_levels,
                "current_price": current_price,
                "grid_spacing": grid_spacing,
                "quantity_per_level": quantity_per_level,
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
            # Grid metadata
            metadata = {
                "grid_type": "ranging_volatile",
                "num_levels": len(buy_orders) + len(sell_orders),
                "grid_spacing": grid_levels["grid_spacing"],
                "quantity_per_level": grid_levels["quantity_per_level"],
                "buy_order_ids": [o.get("order_id") or o.get("id") for o in buy_orders],
                "sell_order_ids": [
                    o.get("order_id") or o.get("id") for o in sell_orders
                ],
                "emergency_stop": self._calculate_emergency_stop(signal),
                "notes": signal.notes,
            }

            # Save as special "GRID" position, tagged with the current
            # confirmed regime for per-regime attribution.  Recorded via
            # TradeStore so the active exchange name is tagged as well.
            current_regime = self.market_regime.get_current_regime(symbol)
            self.trade_store.record_trade(
                {
                    "symbol": symbol,
                    "strategy": "GRID_TRADING",
                    "side": "GRID",
                    "quantity": grid_levels["quantity_per_level"],
                    "entry_price": grid_levels["current_price"],
                    "entry_time": datetime.now().isoformat(),
                    "status": "open",
                    "regime": current_regime.value if current_regime else None,
                }
            )
            logging.debug(f"Grid trade metadata (not persisted): {metadata}")

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

            # Calculate P&L percentage.  The configured threshold is a
            # fraction (0.10 = 10%), so compare in the same unit.
            pnl_percentage = (total_pnl / balance) * 100
            threshold_pct = self._circuit_breaker_loss_pct * 100

            # Check circuit breaker threshold
            if pnl_percentage <= -threshold_pct:
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
            elif pnl_percentage <= -threshold_pct * 0.8:
                logging.warning(
                    f"⚠️ Approaching circuit breaker threshold: {pnl_percentage:.1f}% loss "
                    f"(threshold: {-threshold_pct:.1f}%)"
                )

        except Exception as e:
            logging.error(f"Error monitoring risk: {e}")

    # Mapping from the display names returned by get_active_strategies() to the
    # strategy string stored in the trades/positions DB tables.
    # Only strategies that leave open exchange orders (limit orders that outlive
    # the signal) need to be listed here; market-execution strategies clean up
    # automatically when they close.
    _STRATEGY_NAME_TO_DB: Dict[str, str] = {
        "GridTrading": "GRID_TRADING",
        "MACrossover": "MA_CROSSOVER",
        "MeanReversion": "MEAN_REVERSION",
        "MomentumScalping": "MOMENTUM_SCALPING",
        "VWAPScalping": "VWAP_SCALPING",
        "FundingArb": "FUNDING_ARB",
        "LiquidationCapture": "LIQUIDATION_CAPTURE",
        "OrderBookImbalance": "ORDERBOOK_IMBALANCE",
    }

    def _close_positions_for_strategy(self, strategy_name: str) -> None:
        """
        Cancel open exchange orders and mark DB trades closed for a strategy
        that has become inactive due to a regime change.

        For grid strategies this is critical: the bot places limit orders that
        remain open on the exchange until explicitly cancelled.  For other
        strategies it is a belt-and-suspenders safety measure.

        Args:
            strategy_name: Display name as returned by get_active_strategies()
                           (e.g. "GridTrading", "MACrossover").
        """
        db_strategy = self._STRATEGY_NAME_TO_DB.get(strategy_name)

        try:
            # 1. Find open DB trades for this strategy so we know which symbols
            #    need order cancellation.
            open_trades = self.db.get_trades(status="open")
            strategy_trades = [
                t for t in open_trades if t.get("strategy") == db_strategy
            ]

            if not strategy_trades:
                logging.info(
                    f"  No open DB trades for {strategy_name} "
                    f"(db_key='{db_strategy}') — nothing to close"
                )
                return

            symbols_affected = {
                t.get("symbol") for t in strategy_trades if t.get("symbol")
            }
            logging.info(
                f"  {strategy_name}: closing {len(strategy_trades)} open trade(s) "
                f"across symbols: {symbols_affected}"
            )

            # 2. For each affected symbol, cancel all open exchange orders.
            #    cancel_all_orders() is safe to call even if there are no orders.
            for symbol in symbols_affected:
                try:
                    result = _resolve_exchange(self).cancel_all_orders(symbol=symbol)
                    logging.info(
                        f"  ✅ Cancelled orders for {symbol} ({strategy_name}): {result}"
                    )
                except Exception as cancel_err:
                    logging.error(
                        f"  ❌ Failed to cancel orders for {symbol}: {cancel_err}"
                    )

            # 3. For grid strategy specifically, also clear the in-memory grid
            #    state so GridLifecycleManager doesn't try to manage stale grids.
            if strategy_name == "GridTrading":
                try:
                    self.component_registry.get(
                        type(None)  # use duck-typing below
                    )
                except Exception:
                    pass

                # Try direct attribute access (GridLifecycleManager stored on bot)
                grid_lifecycle = getattr(self, "grid_lifecycle_manager", None)
                if grid_lifecycle is not None:
                    for symbol in symbols_affected:
                        try:
                            grid_lifecycle.clear_grid(symbol, reason="REGIME_CHANGE")
                            logging.info(
                                f"  🗑️ Cleared in-memory grid state for {symbol}"
                            )
                        except Exception as gc_err:
                            logging.warning(
                                f"  Could not clear grid state for {symbol}: {gc_err}"
                            )

        except Exception as e:
            logging.error(
                f"Error closing positions for strategy {strategy_name}: {e}",
                exc_info=True,
            )

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

                # get_active_strategies() lives on MarketRegimeDetector and returns
                # List[str].  Previously this incorrectly called strategy_manager and
                # used .keys() on the result — both fixed here.
                old_strategies: List[str] = self.market_regime.get_active_strategies(
                    self._previous_regime
                )
                new_strategies: List[str] = self.market_regime.get_active_strategies(
                    current_regime
                )

                inactive_strategies = set(old_strategies) - set(new_strategies)

                if inactive_strategies:
                    logging.info(f"  Strategies going inactive: {inactive_strategies}")

                # Close positions from inactive strategies
                for strategy_name in inactive_strategies:
                    logging.info(
                        f"  Closing positions from inactive strategy: {strategy_name}"
                    )
                    self._close_positions_for_strategy(strategy_name)

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
        3. ADX rises above the grid ADX threshold (trend forming — not suitable for grid)

        For each open grid DB trade, we check these conditions and trigger
        _close_orphaned_grid() which cancels all orders for the symbol.
        """
        try:
            # --- Fix 4: Query database for active grid positions ---
            # Grid positions are stored in the trades table with
            # strategy='GRID_TRADING' and side='GRID' and status='open'.
            open_trades = self.db.get_trades(status="open")
            grid_trades = [
                t for t in open_trades if t.get("strategy") == "GRID_TRADING"
            ]

            if not grid_trades:
                return  # Nothing to monitor

            logging.debug(
                f"Emergency stop monitor: {len(grid_trades)} active grid trade(s)"
            )

            for trade in grid_trades:
                symbol = trade.get("symbol")
                if not symbol:
                    continue

                try:
                    # 1. Check current price vs emergency stop level
                    ticker = self._get_ticker_ws(symbol)
                    if isinstance(ticker, dict):
                        current_price = float(
                            ticker.get("last", ticker.get("price", 0))
                        )
                    else:
                        current_price = float(ticker) if ticker else 0

                    if current_price <= 0:
                        logging.debug(
                            f"  {symbol}: price unavailable, skipping emergency check"
                        )
                        continue

                    # Emergency stop is stored in trade metadata (stop_loss field)
                    emergency_stop = trade.get("stop_loss") or trade.get("stop_price")
                    if emergency_stop and current_price <= float(emergency_stop):
                        logging.warning(
                            f"🚨 GRID EMERGENCY STOP triggered for {symbol}: "
                            f"price {current_price:.4f} <= stop {emergency_stop:.4f}"
                        )
                        self._close_orphaned_grid(symbol, reason="EMERGENCY_STOP_PRICE")
                        continue

                    # 2. Check ADX (trend forming — grid becomes unsuitable)
                    adx_threshold = getattr(self.market_regime, "adx_threshold", 25.0)
                    last_adx = self.market_regime.get_last_adx(symbol)
                    if last_adx is not None and last_adx > adx_threshold:
                        logging.warning(
                            f"🚨 GRID ADX STOP for {symbol}: "
                            f"ADX {last_adx:.1f} > threshold {adx_threshold:.1f} — "
                            f"trend forming, grid is unsuitable"
                        )
                        self._close_orphaned_grid(
                            symbol, reason="ADX_THRESHOLD_EXCEEDED"
                        )
                        continue

                    # 3. Check unrealized P&L vs max drawdown (if available)
                    unrealized_pnl = trade.get("pnl", 0) or 0
                    entry_price = trade.get("entry_price", 0) or 0
                    quantity = trade.get("quantity", 0) or 0
                    if entry_price > 0 and quantity > 0:
                        position_value = entry_price * quantity
                        max_drawdown_pct = 0.15  # 15% drawdown triggers emergency stop
                        if position_value > 0 and unrealized_pnl < -(
                            position_value * max_drawdown_pct
                        ):
                            logging.warning(
                                f"🚨 GRID P&L STOP for {symbol}: "
                                f"unrealized P&L {unrealized_pnl:.2f} exceeds "
                                f"{max_drawdown_pct * 100:.0f}% drawdown on "
                                f"position value {position_value:.2f}"
                            )
                            self._close_orphaned_grid(
                                symbol, reason="MAX_DRAWDOWN_EXCEEDED"
                            )
                            continue

                except Exception as per_trade_err:
                    logging.error(
                        f"Error monitoring emergency stop for grid {symbol}: {per_trade_err}"
                    )

        except Exception as e:
            logging.error(f"Error monitoring emergency stops: {e}")

    def _assert_grid_pre_trade(
        self,
        symbol: str,
        grid_spacing: float,
        num_levels: int,
        quantity_per_level: float,
        signal: Optional[Signal] = None,
    ) -> None:
        """
        Assert pre-trade conditions for grid trading (per Grid Trading Brief).

        Validates:
        1. Grid spacing > 0
        2. Number of levels between 5-20
        3. Quantity per level > minimum order size (fetched live from exchange)
        4. Emergency stop price > 0 and < current price (when signal is provided)
        5. Total capital allocation within risk limits

        Args:
            symbol: Trading symbol
            grid_spacing: Grid spacing in price units
            num_levels: Number of grid levels
            quantity_per_level: Quantity at each grid level
            signal: Optional originating Signal for emergency-stop validation

        Raises:
            AssertionError: If any pre-trade condition fails
        """
        # Validate grid spacing
        assert grid_spacing > 0, f"Grid spacing must be positive, got {grid_spacing}"

        # Validate number of levels
        assert 5 <= num_levels <= 20, f"Number of levels must be 5-20, got {num_levels}"

        # Validate quantity per level against exchange minimum
        # Fetch live instrument info so we respect per-token minimums.
        # Falls back to 0.0 (no constraint) if the API call fails.
        instrument_info = self.client.get_instrument_info(symbol)
        min_order_size = instrument_info.get("min_order_size", 0.0)
        if min_order_size > 0:
            assert quantity_per_level >= min_order_size, (
                f"Quantity per level {quantity_per_level} below exchange minimum "
                f"{min_order_size} for {symbol}"
            )
        else:
            logging.debug(
                f"{symbol}: min_order_size not available from exchange info — "
                "skipping minimum quantity check"
            )

        # Validate emergency stop price (only when a signal is provided)
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
            total_pnl = sum(float(pos.get("unrealized_pnl", 0)) for pos in positions)

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
