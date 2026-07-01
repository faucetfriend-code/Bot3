"""
Trading Bot API Server
FastAPI server providing web interface and REST API for the trading bot system.
"""

import sys
import os
import asyncio
import logging
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from pathlib import Path

# Add path for shared modules (needed for core_logic imports like models, indicators base)
sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "Example files", "core_logic")
)

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from trading_bot_v2.metrics import metrics
from contextlib import asynccontextmanager
import uvicorn

# Import real bot components - support both module and script execution
try:
    # When running as module: python -m trading_bot_v2.api_server
    from .trading_bot import TradingBot
    from .database import DatabaseManager
    from .pacifica_client import PacificaClient
    from .strategy_manager import StrategyManager
    from .market_regime import MarketRegimeDetector, MarketRegime
    from .multi_timeframe_fetcher import MultiTimeframeFetcher
    from .grid_lifecycle_manager import GridLifecycleManager
    from .risk_manager import RiskManager
    from .indicators import calculate_rsi, calculate_adx
    from .config import config
    from .pacifica_ws_client import get_ws_client
except ImportError:
    # When running as script: python api_server.py
    from trading_bot import TradingBot
    from database import DatabaseManager
    from pacifica_client import PacificaClient
    from strategy_manager import StrategyManager
    from market_regime import MarketRegimeDetector, MarketRegime
    from multi_timeframe_fetcher import MultiTimeframeFetcher
    from grid_lifecycle_manager import GridLifecycleManager
    from risk_manager import RiskManager
    from indicators import calculate_rsi, calculate_adx
    from config import config
    from pacifica_ws_client import get_ws_client

# Configure logging
# - Console: stdout (existing behavior)
# - File:    "<repo>/server logs reports/current.log" — supervisor reads this
#            Rotated to current_YYYYMMDD_HHMMSS.log on each bot start so
#            "current.log" always reflects the active session.
_LOG_FMT = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
logging.basicConfig(level=logging.INFO, format=_LOG_FMT)

try:
    _LOG_DIR = Path(__file__).resolve().parent.parent / "server logs reports"
    _LOG_DIR.mkdir(exist_ok=True)
    _CURRENT_LOG = _LOG_DIR / "current.log"

    # Rotate previous current.log if non-empty
    if _CURRENT_LOG.exists() and _CURRENT_LOG.stat().st_size > 0:
        _ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        _rotated = _LOG_DIR / f"current_{_ts}.log"
        try:
            _CURRENT_LOG.rename(_rotated)
        except OSError:
            # If rename fails (rare; e.g. file in use on Windows), append instead
            pass

    _file_handler = logging.FileHandler(_CURRENT_LOG, mode="a", encoding="utf-8")
    _file_handler.setLevel(logging.INFO)
    _file_handler.setFormatter(logging.Formatter(_LOG_FMT))
    logging.getLogger().addHandler(_file_handler)

    # Loguru sink for modules that use loguru (signal_logger, kelly_position_sizer)
    try:
        from loguru import logger as _loguru
        _loguru.add(
            str(_CURRENT_LOG),
            format="{time:YYYY-MM-DD HH:mm:ss} - {name} - {level} - {message}",
            level="INFO",
            rotation=None,           # we rotate manually on bot start
            enqueue=True,            # thread-safe writes
        )
    except ImportError:
        pass  # loguru optional
except Exception as _log_setup_err:
    # Never let logging setup take down the bot
    print(f"WARNING: file logging setup failed: {_log_setup_err}")

logger = logging.getLogger(__name__)

# Create FastAPI app with lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup
    try:
        bot_integration.initialize()
        logger.info("API server started with bot integration")
    except Exception as e:
        logger.error(f"Failed to initialize bot on startup: {e}")

    yield  # Server is running

    # Shutdown
    try:
        if bot_integration._is_running:
            await bot_integration.stop()
        bot_integration.shutdown()
        logger.info("API server shutdown complete")
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")


app = FastAPI(
    title="Trading Bot Control Interface",
    description="Professional trading bot control interface with real-time monitoring",
    version="2.0.0",
    lifespan=lifespan,
)


class BotIntegration:
    """
    Manages real TradingBot, DatabaseManager, PacificaClient with thread-safe state.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._is_running = False
        self._bot_task: Optional[asyncio.Task] = None

        # Initialize real components
        self.database: Optional[DatabaseManager] = None
        self.pacifica_client: Optional[PacificaClient] = None
        self.trading_bot: Optional[TradingBot] = None
        self.strategy_manager: Optional[StrategyManager] = None
        self.regime_detector: Optional[MarketRegimeDetector] = None
        self.mtf_fetcher: Optional[MultiTimeframeFetcher] = None
        self.grid_manager: Optional[GridLifecycleManager] = None
        self.risk_manager: Optional[RiskManager] = None
        self.ws_client = (
            None  # WebSocket client for market data (starts at server startup)
        )

        self._initialized = False

    def initialize(self):
        """Initialize all bot components."""
        if self._initialized:
            return

        try:
            logger.info("Initializing bot components...")

            # Initialize database
            self.database = DatabaseManager()
            logger.info("DatabaseManager initialized")

            # Initialize Pacifica client with API keys from config
            if not config.pacifica_private_key or not config.pacifica_public_key:
                logger.warning(
                    "Pacifica API keys not configured - some features will be unavailable"
                )
                self.pacifica_client = None
            else:
                self.pacifica_client = PacificaClient(
                    agent_wallet_private_key=config.pacifica_private_key,
                    account_public_key=config.pacifica_public_key,
                    testnet=config.testnet,
                )
                logger.info("PacificaClient initialized")

            # Initialize WebSocket client for real-time market data (starts immediately)
            # This runs independently of the trading bot so the interface can show data before bot starts
            try:
                self.ws_client = get_ws_client()
                self.ws_client.start()
                logger.info("WebSocket client started for real-time market data")

                # Bootstrap kline cache for key trading symbols
                # Include ALL timeframes needed - both strategy (15m, 1h, 4h) and execution (1m, 5m)
                # This single bootstrap replaces the duplicate in trading_bot.start()
                key_symbols = ["BTC", "ETH", "SOL", "SUI", "AVAX", "XRP", "DOGE", "LTC"]
                all_intervals = ["1m", "5m", "15m", "1h", "4h"]

                if self.pacifica_client:
                    logger.info(
                        f"⚡ Fast bootstrap: {len(key_symbols)} symbols x {len(all_intervals)} timeframes "
                        "(all timeframes in one call)"
                    )
                    self.ws_client.bootstrap_kline_cache(
                        rest_client=self.pacifica_client,
                        symbols=key_symbols,
                        intervals=all_intervals,  # All timeframes at once
                        lookback=250,
                        use_disk_cache=True,  # Cache to disk for faster restarts
                        max_concurrent=2,  # Conservative rate limiting (was 3)
                    )
                    logger.info(
                        f"✅ Kline cache ready for {len(key_symbols)} symbols (all timeframes)"
                    )

                    # Subscribe to orderbook for OrderBookImbalance strategy
                    # Uses agg_level=10 for reasonable depth resolution
                    import os
                    if os.getenv("ENABLE_ORDERBOOK_IMBALANCE", "true").lower() == "true":
                        agg_level = int(os.getenv("ORDERBOOK_AGG_LEVEL", "10"))
                        for symbol in key_symbols:
                            self.ws_client.subscribe_orderbook(symbol, agg_level=agg_level)
                        logger.info(
                            f"📚 Subscribed to orderbook for {len(key_symbols)} symbols (agg_level={agg_level})"
                        )
            except Exception as e:
                logger.warning(f"Could not start WebSocket client: {e}")
                self.ws_client = None

            # Initialize regime detector
            self.regime_detector = MarketRegimeDetector()
            logger.info("MarketRegimeDetector initialized")

            # Initialize multi-timeframe fetcher (requires pacifica_client)
            if self.pacifica_client:
                self.mtf_fetcher = MultiTimeframeFetcher(self.pacifica_client)
                logger.info("MultiTimeframeFetcher initialized")
            else:
                self.mtf_fetcher = None
                logger.warning("MultiTimeframeFetcher skipped - no Pacifica client")

            # Initialize risk manager
            self.risk_manager = RiskManager()
            logger.info("RiskManager initialized")

            # Initialize grid lifecycle manager (requires client, risk_manager, db)
            if self.pacifica_client:
                self.grid_manager = GridLifecycleManager(
                    client=self.pacifica_client,
                    risk_manager=self.risk_manager,
                    db=self.database,
                )
                logger.info("GridLifecycleManager initialized")
            else:
                self.grid_manager = None
                logger.warning("GridLifecycleManager skipped - no Pacifica client")

            # Initialize strategy manager (uses regime_detector and enable flags)
            self.strategy_manager = StrategyManager(
                regime_detector=self.regime_detector,
                enable_mean_reversion=True,
                enable_ma_crossover=True,
                enable_trend_following=False,
                enable_grid_trading=True,
                enable_liquidation_capture=True,
                client=self.pacifica_client,  # For FundingArb API calls
                ws_client=self.ws_client,  # For OrderBookImbalance
            )
            logger.info("StrategyManager initialized")

            # Initialize trading bot (uses db, client, risk_manager, hub_publish_func)
            if self.pacifica_client:
                self.trading_bot = TradingBot(
                    db=self.database,
                    client=self.pacifica_client,
                    risk_manager=self.risk_manager,
                )
                logger.info("TradingBot initialized")
            else:
                self.trading_bot = None
                logger.warning(
                    "TradingBot skipped - no Pacifica client (API keys required)"
                )

            self._initialized = True
            logger.info("All bot components initialized successfully")

        except Exception as e:
            logger.error(f"Error initializing bot components: {e}")
            raise

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive bot status."""
        with self._lock:
            try:
                # Base status
                status = {
                    "is_running": self._is_running,
                    "positions_count": 0,
                    "trades_count": 0,
                    "total_pnl": 0.0,
                    "account_balance": 0.0,
                    "active_grids": 0,
                    "current_regime": "unknown",
                    "circuit_breaker_triggered": False,
                }

                if not self._initialized:
                    return status

                # Get positions count (filtered: same logic as get_positions)
                if self.database:
                    positions = self.database.get_positions()
                    filtered = [p for p in (positions or []) if float(p.get('quantity', 0)) > 0]
                    status["positions_count"] = len(filtered)

                    # Get trades count and PnL
                    trades = self.database.get_trades(limit=1000)
                    if trades:
                        status["trades_count"] = len(trades)
                        status["total_pnl"] = sum(
                            t.get("pnl", 0) or 0
                            for t in trades
                            if t.get("status") == "closed"
                        )

                # Get account balance
                if self.pacifica_client:
                    try:
                        balance_info = self.pacifica_client.get_balance()
                        # Pacifica returns strings, convert to float
                        # Keys: "balance" or "account_equity"
                        balance_str = balance_info.get("balance", balance_info.get("account_equity", "0"))
                        status["account_balance"] = float(balance_str) if balance_str else 0.0
                    except Exception as e:
                        logger.warning(f"Could not fetch balance: {e}")

                # Get active grids count
                if self.grid_manager:
                    try:
                        active_grids = self.grid_manager.get_all_active_grids()
                        status["active_grids"] = (
                            len(active_grids) if active_grids else 0
                        )
                    except Exception as e:
                        logger.warning(f"Could not fetch grids: {e}")

                # Get circuit breaker status from trading bot
                if self.trading_bot:
                    status["circuit_breaker_triggered"] = getattr(
                        self.trading_bot, "_circuit_breaker_triggered", False
                    )

                # Get current regime (use BTC as reference)
                if self.regime_detector and self.ws_client:
                    try:
                        kline_list = self.ws_client.get_kline_data("BTC", "4h")
                        if kline_list and len(kline_list) >= 50:
                            # Convert list of candles to dict format expected by detect_regime_cached
                            # WebSocket kline format: {"c": close, "h": high, "l": low, "v": volume}
                            market_data = {
                                "close": [float(c.get("c", 0)) for c in kline_list],
                                "high": [float(c.get("h", 0)) for c in kline_list],
                                "low": [float(c.get("l", 0)) for c in kline_list],
                                "volume": [float(c.get("v", 0)) for c in kline_list],
                            }
                            regime = self.regime_detector.detect_regime_cached("BTC", market_data)
                            status["current_regime"] = regime.value if regime else "unknown"
                    except Exception as e:
                        logger.warning(f"Could not detect regime: {e}")

                return status

            except Exception as e:
                logger.error(f"Error getting status: {e}")
                return {
                    "is_running": self._is_running,
                    "positions_count": 0,
                    "trades_count": 0,
                    "total_pnl": 0.0,
                    "error": str(e),
                }

    async def start(self):
        """Start the trading bot."""
        print("=== DIAGNOSTIC: BotIntegration.start() CALLED ===", flush=True)
        logger.info("BotIntegration.start() called")
        with self._lock:
            if self._is_running:
                logger.warning("Bot is already running")
                return

            if not self._initialized:
                self.initialize()

            self._is_running = True
            logger.info(f"Set _is_running=True, trading_bot={self.trading_bot is not None}")

        try:
            if self.trading_bot:
                logger.info("Calling trading_bot.start() via executor...")
                # TradingBot.start() is synchronous - run in thread to not block
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self.trading_bot.start)
                logger.info("Trading bot started successfully")
            else:
                logger.warning("trading_bot is None - cannot start")
        except Exception as e:
            with self._lock:
                self._is_running = False
            logger.error(f"Error starting bot: {e}", exc_info=True)
            raise

    async def stop(self):
        """Stop the trading bot."""
        with self._lock:
            if not self._is_running:
                logger.warning("Bot is not running")
                return

            self._is_running = False

        try:
            if self.trading_bot:
                # TradingBot.stop() is synchronous
                self.trading_bot.stop()
                logger.info("Trading bot stopped")
        except Exception as e:
            logger.error(f"Error stopping bot: {e}")
            raise

    def get_positions(self) -> List[Dict]:
        """Get current positions from database and Pacifica."""
        positions = []

        try:
            # Get positions from database
            if self.database:
                db_positions = self.database.get_positions()
                if db_positions:
                    # Filter out zero-quantity positions
                    positions = [
                        p for p in db_positions
                        if float(p.get('quantity', 0)) > 0
                    ]

            # Enrich with live data from Pacifica
            if self.pacifica_client and positions:
                try:
                    live_positions = self.pacifica_client.get_positions()
                    live_map = (
                        {p.get("symbol"): p for p in live_positions}
                        if live_positions
                        else {}
                    )

                    for pos in positions:
                        symbol = pos.get("symbol")
                        if symbol in live_map:
                            live = live_map[symbol]
                            pos["unrealized_pnl"] = live.get("unrealized_pnl", 0)
                            pos["mark_price"] = live.get("mark_price", 0)
                            pos["liquidation_price"] = live.get("liquidation_price", 0)
                except Exception as e:
                    logger.warning(f"Could not enrich positions with live data: {e}")

            # Calculate unrealized P&L locally if not provided or zero
            # Use server's WebSocket client for real-time prices (independent of bot)
            ws_client = self.ws_client

            for pos in positions:
                entry_price = float(pos.get("entry_price", 0))
                current_price = float(pos.get("current_price", 0))
                quantity = float(pos.get("quantity", 0))
                side = pos.get("side", "long")

                # Try to get real-time price from WebSocket
                if ws_client and current_price == 0:
                    symbol = pos.get("symbol", "")
                    ws_price = ws_client.get_price(symbol)
                    if ws_price:
                        current_price = ws_price
                        pos["current_price"] = current_price

                # Calculate unrealized P&L if we have the data
                if entry_price > 0 and current_price > 0 and quantity > 0:
                    if side == "long":
                        unrealized = (current_price - entry_price) * quantity
                    else:  # short
                        unrealized = (entry_price - current_price) * quantity
                    pos["unrealized_pnl"] = round(unrealized, 2)

        except Exception as e:
            logger.error(f"Error getting positions: {e}")

        return positions

    def get_trades(self, limit: int = 100) -> List[Dict]:
        """Get recent trades."""
        trades = []

        try:
            if self.database:
                trades = self.database.get_trades(limit=limit)

            # Also try to get recent trades from Pacifica API
            if self.pacifica_client:
                try:
                    api_trades = self.pacifica_client.get_trades(limit=limit)
                    if api_trades:
                        # Merge with database trades
                        existing_ids = {
                            t.get("order_id") for t in trades if t.get("order_id")
                        }
                        for trade in api_trades:
                            if trade.get("order_id") not in existing_ids:
                                trades.append(
                                    {
                                        "order_id": trade.get("order_id"),
                                        "symbol": trade.get("symbol"),
                                        "side": trade.get("side"),
                                        "quantity": trade.get(
                                            "size", trade.get("quantity")
                                        ),
                                        "price": trade.get("price"),
                                        "timestamp": trade.get("timestamp"),
                                        "source": "pacifica_api",
                                    }
                                )
                except Exception as e:
                    logger.warning(f"Could not fetch trades from Pacifica API: {e}")

        except Exception as e:
            logger.error(f"Error getting trades: {e}")

        return trades

    def get_orders(self) -> List[Dict]:
        """Get open orders from Pacifica."""
        orders = []

        try:
            if self.pacifica_client:
                all_orders = self.pacifica_client.get_orders()
                if all_orders:
                    # Filter out grid orders (show only manual/strategy orders)
                    for order in all_orders:
                        # Grid orders typically have specific client_order_id patterns
                        client_id = order.get("client_order_id", "")
                        if not client_id.startswith("grid_"):
                            orders.append(order)
        except Exception as e:
            logger.error(f"Error getting orders: {e}")

        return orders

    def get_grids(self) -> List[Dict]:
        """Get active grid trading configurations."""
        grids = []

        try:
            if self.grid_manager:
                active_grids = self.grid_manager.get_all_active_grids()
                if active_grids:
                    for symbol, grid_data in active_grids.items():
                        grids.append(
                            {
                                "symbol": symbol,
                                "state": grid_data.get("state", "unknown"),
                                "grid_levels": grid_data.get("levels", []),
                                "upper_price": grid_data.get("upper_price"),
                                "lower_price": grid_data.get("lower_price"),
                                "filled_orders": grid_data.get("filled_count", 0),
                                "total_orders": grid_data.get("total_orders", 0),
                                "realized_pnl": grid_data.get("realized_pnl", 0),
                                "created_at": grid_data.get("created_at"),
                            }
                        )
        except Exception as e:
            logger.error(f"Error getting grids: {e}")

        return grids

    def _normalize_position_side(self, side: Optional[str]) -> str:
        """Normalize position side from Pacifica format to database format.

        Pacifica uses non-standard side names: 'bid'/'ask'/'long'/'short'
        """
        if not side:
            return "LONG"
        side_lower = str(side).lower()
        if side_lower in ("long", "bid", "buy"):
            return "LONG"
        elif side_lower in ("short", "ask", "sell"):
            return "SHORT"
        side_upper = str(side).upper()
        return side_upper if side_upper in ("LONG", "SHORT") else "LONG"

    def _map_pacifica_position(self, pos: Dict[str, Any]) -> Dict[str, Any]:
        """Map Pacifica position fields to database format."""
        symbol = pos.get("symbol", "")

        quantity = 0.0
        for key in ("size", "amount", "quantity", "position_size", "pos_size"):
            if key in pos and pos[key] is not None:
                try:
                    quantity = float(pos[key])
                    break
                except (TypeError, ValueError):
                    continue

        entry_price = 0.0
        for key in ("avg_entry_price", "entry_price", "average_entry", "avg_price", "entry"):
            if key in pos and pos[key] is not None:
                try:
                    entry_price = float(pos[key])
                    break
                except (TypeError, ValueError):
                    continue

        current_price = 0.0
        for key in ("mark_price", "current_price", "last_price", "price"):
            if key in pos and pos[key] is not None:
                try:
                    current_price = float(pos[key])
                    break
                except (TypeError, ValueError):
                    continue

        if current_price == 0 and symbol and self.ws_client:
            try:
                ws_price = self.ws_client.get_price(symbol)
                if ws_price:
                    current_price = ws_price
            except Exception:
                pass

        if current_price == 0:
            current_price = entry_price

        side = self._normalize_position_side(pos.get("side"))

        opened_at = None
        for key in ("created_at", "opened_at", "timestamp", "open_time"):
            if key in pos and pos[key] is not None:
                opened_at = pos[key]
                break
        if not opened_at:
            opened_at = datetime.now(timezone.utc).isoformat()

        unrealized_pnl = 0.0
        for key in ("unrealized_pnl", "upnl", "floating_pnl"):
            if key in pos and pos[key] is not None:
                try:
                    unrealized_pnl = float(pos[key])
                    break
                except (TypeError, ValueError):
                    continue

        if unrealized_pnl == 0 and entry_price > 0 and current_price > 0 and quantity > 0:
            if side == "LONG":
                unrealized_pnl = (current_price - entry_price) * quantity
            else:
                unrealized_pnl = (entry_price - current_price) * quantity

        return {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "entry_price": entry_price,
            "current_price": current_price,
            "opened_at": opened_at,
            "asset_class": pos.get("asset_class", "perpetual"),
            "unrealized_pnl": round(unrealized_pnl, 2),
        }

    async def sync_positions(self) -> Dict[str, Any]:
        """Sync positions from Pacifica exchange to database.

        Returns:
            Dict with synced/inserted/updated/closed counts and errors.
        """
        result = {
            "success": False,
            "synced_count": 0,
            "inserted_count": 0,
            "updated_count": 0,
            "closed_count": 0,
            "errors": [],
        }

        try:
            if not self.pacifica_client or not self.database:
                result["errors"].append("Client or database not available")
                return result

            live_positions = self.pacifica_client.get_positions()

            # Get existing DB positions for insert vs update detection
            existing_positions = {}
            try:
                db_positions = self.database.get_positions()
                if db_positions:
                    existing_positions = {
                        p.get("symbol"): p for p in db_positions if p.get("symbol")
                    }
            except Exception as e:
                logger.warning(f"Could not fetch existing DB positions: {e}")

            # Filter and map live positions
            active_symbols = set()
            if live_positions:
                for pos in live_positions:
                    try:
                        mapped = self._map_pacifica_position(pos)
                        if mapped["quantity"] <= 0:
                            continue

                        symbol = mapped["symbol"]
                        active_symbols.add(symbol)

                        self.database.upsert_position(mapped)

                        if symbol in existing_positions:
                            result["updated_count"] += 1
                        else:
                            result["inserted_count"] += 1

                        result["synced_count"] += 1
                    except Exception as e:
                        result["errors"].append(f"Error syncing {pos.get('symbol', '?')}: {e}")

            # Remove positions closed on exchange
            for symbol in existing_positions:
                if symbol not in active_symbols:
                    try:
                        self.database.close_position(symbol)
                        result["closed_count"] += 1
                    except Exception as e:
                        result["errors"].append(f"Error closing {symbol}: {e}")

            result["success"] = True

        except Exception as e:
            logger.error(f"Error syncing positions: {e}")
            result["errors"].append(str(e))

        return result

    def get_activity(self, limit: int = 50) -> List[Dict]:
        """Get market activity with regime detection and RSI for each market."""
        activity = []

        try:
            if not self.pacifica_client:
                return activity

            # Get available markets
            markets = self.pacifica_client.get_markets()
            if not markets:
                return activity

            # Use server's WebSocket client for real-time prices (independent of bot)
            ws_client = self.ws_client

            for market in markets[:limit]:
                # Extract symbol - markets can have different field names
                symbol = market.get(
                    "symbol", market.get("market", market.get("name", ""))
                )
                if not symbol:
                    continue

                # Clean symbol for display (remove -PERP suffix if present)
                display_symbol = symbol.replace("-PERP", "").upper()

                # Get real-time price from WebSocket cache
                price = 0
                if ws_client:
                    ws_price = ws_client.get_price(display_symbol)
                    if ws_price:
                        price = ws_price

                # Fall back to market data price if WS price not available
                if price == 0:
                    price = float(
                        market.get("mark_price")
                        or market.get("last_price")
                        or market.get("price")
                        or 0
                    )

                market_activity = {
                    "symbol": display_symbol,
                    "regime": "unknown",
                    "rsi_15m": None,
                    "rsi_1h": None,
                    "adx": None,
                    "price": round(price, 2) if price > 1 else round(price, 6),
                    "change_24h": market.get(
                        "change_24h", market.get("price_change_percent_24h", 0)
                    ),
                    "volume_24h": market.get(
                        "volume_24h", market.get("volume_24h_usd", 0)
                    ),
                    "active_strategies": [],
                    "status": "monitoring",
                }

                # Regime to active strategies mapping
                regime_strategies = {
                    "ranging_calm": ["MeanReversion", "GridTrading", "VWAPScalping", "LiquidationCapture", "FundingArb", "OrderBookImbalance"],
                    "ranging_volatile": ["GridTrading", "VWAPScalping", "LiquidationCapture", "FundingArb", "OrderBookImbalance"],
                    "trending_strong": ["MACrossover", "MomentumScalping", "LiquidationCapture", "FundingArb", "OrderBookImbalance"],
                    "trending_moderate": ["MACrossover", "MomentumScalping", "LiquidationCapture", "FundingArb", "OrderBookImbalance"],
                    "indecisive": ["LiquidationCapture", "FundingArb", "OrderBookImbalance"],
                }

                # Use server's regime detector (or bot's if available for cached data)
                regime_detector = None
                # Prefer trading bot's regime detector if bot is running (has fresher cache)
                if self.trading_bot and hasattr(self.trading_bot, "strategy_manager"):
                    sm = self.trading_bot.strategy_manager
                    if sm and hasattr(sm, "regime_detector"):
                        regime_detector = sm.regime_detector
                # Fall back to server's regime detector
                if not regime_detector:
                    regime_detector = self.regime_detector

                if regime_detector:
                    # First check cache for regime
                    cached_regime = None
                    if hasattr(regime_detector, "_regime_cache"):
                        cached = regime_detector._regime_cache.get(display_symbol)
                        if cached and "regime" in cached:
                            cached_regime = cached["regime"].value

                    # Always try to calculate ADX from kline data (even if regime is cached)
                    if ws_client:
                        try:
                            kline_4h = ws_client.get_kline_data(display_symbol, "4h")
                            if kline_4h and len(kline_4h) >= 30:
                                closes = [
                                    float(c.get("c") or c.get("close") or 0)
                                    for c in kline_4h[-50:]
                                ]
                                highs = [
                                    float(c.get("h") or c.get("high") or 0)
                                    for c in kline_4h[-50:]
                                ]
                                lows = [
                                    float(c.get("l") or c.get("low") or 0)
                                    for c in kline_4h[-50:]
                                ]
                                if all(c > 0 for c in closes) and len(closes) >= 30:
                                    # Calculate ADX for display
                                    try:
                                        adx_value = calculate_adx(highs, lows, closes, period=14)
                                        market_activity["adx"] = round(adx_value, 1)
                                    except Exception:
                                        pass  # ADX calculation failed, keep None

                                    # If no cached regime, detect it now
                                    if not cached_regime:
                                        market_data = {
                                            "high": highs,
                                            "low": lows,
                                            "close": closes,
                                        }
                                        detected = regime_detector.detect_regime(
                                            market_data
                                        )
                                        cached_regime = detected.value
                                        logger.debug(
                                            f"Detected regime for {display_symbol}: {cached_regime}, ADX: {market_activity.get('adx')}"
                                        )
                                else:
                                    logger.debug(
                                        f"Invalid kline data for {display_symbol}: closes={len(closes)}, valid={sum(1 for c in closes if c > 0)}"
                                    )
                            else:
                                logger.debug(
                                    f"Insufficient kline data for {display_symbol}: {len(kline_4h) if kline_4h else 0} candles"
                                )
                        except Exception as e:
                            logger.warning(
                                f"Could not calculate ADX for {display_symbol}: {e}"
                            )

                    if cached_regime:
                        market_activity["regime"] = cached_regime
                        market_activity["active_strategies"] = regime_strategies.get(
                            cached_regime, ["LiquidationCapture"]
                        )
                        market_activity["status"] = (
                            "active" if cached_regime != "indecisive" else "waiting"
                        )

                # Try to get RSI from WebSocket kline cache (fast, no API call)
                if ws_client:
                    try:
                        # Get 15m kline data
                        # RSI-14 needs 15 data points (14 for period + 1 for initial calculation)
                        kline_15m = ws_client.get_kline_data(display_symbol, "15m")
                        if kline_15m and len(kline_15m) >= 15:
                            # Debug first symbol
                            if display_symbol == "BTC":
                                logger.debug(
                                    f"BTC 15m kline: {len(kline_15m)} candles available"
                                )
                            closes = [
                                float(c.get("c") or c.get("close") or 0)
                                for c in kline_15m[-15:]
                            ]
                            if all(c > 0 for c in closes):
                                market_activity["rsi_15m"] = round(
                                    calculate_rsi(closes, period=14), 1
                                )

                        # Get 1h kline data
                        kline_1h = ws_client.get_kline_data(display_symbol, "1h")
                        if kline_1h and len(kline_1h) >= 15:
                            closes = [
                                float(c.get("c") or c.get("close") or 0)
                                for c in kline_1h[-15:]
                            ]
                            if all(c > 0 for c in closes):
                                market_activity["rsi_1h"] = round(
                                    calculate_rsi(closes, period=14), 1
                                )
                    except Exception as e:
                        logger.warning(f"Could not calculate RSI for {symbol}: {e}")

                # Only include tokens that have RSI data (bootstrapped tokens)
                if (
                    market_activity["rsi_15m"] is not None
                    or market_activity["rsi_1h"] is not None
                ):
                    activity.append(market_activity)

        except Exception as e:
            logger.error(f"Error getting activity: {e}")

        return activity

    def shutdown(self):
        """Clean shutdown of all components."""
        logger.info("Shutting down bot components...")

        try:
            if self.grid_manager:
                # Cancel any active grids
                pass

            if self.database:
                # Close database connections if needed
                pass

            self._initialized = False
            logger.info("Bot components shut down")

        except Exception as e:
            logger.error(f"Error during shutdown: {e}")


# Global bot integration instance
bot_integration = BotIntegration()

# WebSocket connections for real-time updates
active_connections: List[WebSocket] = []


@app.on_event("startup")
async def startup_event():
    """Initialize bot components on server startup."""
    try:
        bot_integration.initialize()
        logger.info("API server started with bot integration")
    except Exception as e:
        logger.error(f"Failed to initialize bot on startup: {e}")
        # Continue running server even if bot init fails
        # User can retry via API


@app.on_event("shutdown")
async def shutdown_event():
    """Gracefully stop bot on server shutdown."""
    try:
        if bot_integration._is_running:
            await bot_integration.stop()
        bot_integration.shutdown()
        logger.info("API server shutdown complete")
    except Exception as e:
        logger.error(f"Error during server shutdown: {e}")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates."""
    await websocket.accept()
    active_connections.append(websocket)
    logger.info(
        f"WebSocket client connected. Total connections: {len(active_connections)}"
    )

    try:
        # Send initial status
        status = bot_integration.get_status()
        await websocket.send_json(
            {
                "type": "bot_status",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "data": status,
            }
        )

        while True:
            # Keep connection alive and listen for messages
            data = await websocket.receive_text()

            # Handle ping/pong for keepalive
            if data == "ping":
                await websocket.send_text("pong")

    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)
        logger.info(
            f"WebSocket client disconnected. Total connections: {len(active_connections)}"
        )
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        if websocket in active_connections:
            active_connections.remove(websocket)


async def broadcast_update(update_type: str, data: Dict[str, Any]):
    """Broadcast real-time updates to all connected WebSocket clients."""
    message = {
        "type": update_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }

    disconnected = []
    for connection in active_connections:
        try:
            await connection.send_json(message)
        except Exception:
            disconnected.append(connection)

    # Clean up disconnected clients
    for conn in disconnected:
        if conn in active_connections:
            active_connections.remove(conn)


@app.get("/", response_class=HTMLResponse)
async def get_root():
    """Serve the main web interface from interface.html."""
    try:
        # Get the path to interface.html (one level up from trading_bot_v2)
        interface_path = Path(__file__).parent.parent / "interface.html"

        if interface_path.exists():
            with open(interface_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read())
        else:
            logger.error(f"interface.html not found at {interface_path}")
            return HTMLResponse(
                content="<h1>Error: interface.html not found</h1>", status_code=500
            )
    except Exception as e:
        logger.error(f"Error serving interface: {e}")
        return HTMLResponse(
            content=f"<h1>Error loading interface: {e}</h1>", status_code=500
        )


@app.get("/api/status")
async def get_status():
    """Get bot status and statistics."""
    try:
        status = bot_integration.get_status()
        return {"success": True, "data": status}
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/metrics")
async def metrics_endpoint():
    """Prometheus metrics endpoint."""
    return Response(
        content=metrics.get_metrics(),
        media_type=metrics.get_content_type()
    )


@app.get("/api/trades")
async def get_trades(limit: int = 100):
    """Get recent trades."""
    try:
        trades = bot_integration.get_trades(limit=limit)
        return {"success": True, "data": trades}
    except Exception as e:
        logger.error(f"Error getting trades: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/positions")
async def get_positions():
    """Get current positions."""
    try:
        positions = bot_integration.get_positions()
        return {"success": True, "data": positions}
    except Exception as e:
        logger.error(f"Error getting positions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/orders")
async def get_orders():
    """Get open orders (excluding grid orders)."""
    try:
        orders = bot_integration.get_orders()
        return {"success": True, "data": orders}
    except Exception as e:
        logger.error(f"Error getting orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/orders/cancel-all")
async def cancel_all_orders_endpoint(symbol: str = None):
    """Cancel all open orders and clear orphaned grid state."""
    try:
        if not bot_integration.pacifica_client:
            raise HTTPException(status_code=503, detail="Exchange client not available")

        # 1. Cancel all orders on exchange
        result = bot_integration.pacifica_client.cancel_all_orders(symbol)
        logger.info(f"Cancel all orders result: {result}")

        # 2. Clear grid state in GridLifecycleManager so grids don't remain orphaned
        grids_cleared = 0
        bot = bot_integration.trading_bot
        if bot and hasattr(bot, "grid_lifecycle") and bot.grid_lifecycle:
            glm = bot.grid_lifecycle
            if symbol:
                # Clear specific symbol
                if symbol in glm._grids:
                    del glm._grids[symbol]
                    grids_cleared = 1
            else:
                # Clear all grids
                grids_cleared = len(glm._grids)
                glm._grids.clear()
            if grids_cleared:
                logger.info(f"Cleared {grids_cleared} grid(s) from GridLifecycleManager")

        return {
            "success": True,
            "result": result,
            "message": f"Cancelled orders for {'all symbols' if not symbol else symbol}",
            "grids_cleared": grids_cleared,
        }
    except Exception as e:
        logger.error(f"Error cancelling orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/grids")
async def get_grids():
    """Get active grid trading configurations."""
    try:
        grids = bot_integration.get_grids()
        return {"success": True, "data": grids}
    except Exception as e:
        logger.error(f"Error getting grids: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/activity")
async def get_activity(limit: int = 50):
    """Get market activity with regime detection and indicators."""
    try:
        activity = bot_integration.get_activity(limit=limit)
        return {"success": True, "data": activity}
    except Exception as e:
        logger.error(f"Error getting activity: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/strategy-health")
async def strategy_health():
    """
    Get strategy health report.

    Returns correlation matrix, decay alerts, per-strategy Sharpe ratios,
    win rates, and trade counts from the StrategyMonitor.
    """
    try:
        try:
            from .strategy_monitor import get_strategy_monitor
        except ImportError:
            from strategy_monitor import get_strategy_monitor

        monitor = get_strategy_monitor()
        report = monitor.get_health_report()
        return {"success": True, "data": report}
    except Exception as e:
        logger.error(f"Error getting strategy health: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/positions/sync")
async def sync_positions():
    """Sync positions from exchange to database."""
    try:
        sync_result = await bot_integration.sync_positions()
        synced_count = sync_result.get("synced_count", 0) if isinstance(sync_result, dict) else sync_result
        await broadcast_update("positions_synced", {"count": synced_count})
        return {
            "success": sync_result.get("success", True) if isinstance(sync_result, dict) else True,
            "message": f"Synced {synced_count} positions",
            "data": sync_result if isinstance(sync_result, dict) else {"synced_count": synced_count},
        }
    except Exception as e:
        logger.error(f"Error syncing positions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/bot/start")
async def start_bot():
    """Start the trading bot."""
    try:
        await bot_integration.start()
        # Broadcast status update
        status = bot_integration.get_status()
        await broadcast_update("bot_status", status)
        return {"success": True, "message": "Bot started successfully"}
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/bot/stop")
async def stop_bot():
    """Stop the trading bot."""
    try:
        await bot_integration.stop()
        # Broadcast status update
        status = bot_integration.get_status()
        await broadcast_update("bot_status", status)
        return {"success": True, "message": "Bot stopped successfully"}
    except Exception as e:
        logger.error(f"Error stopping bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Supervisor endpoints (Claude routine + manual ops)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/supervisor/status")
async def supervisor_status():
    """
    Return current supervisor pause state.

    Response:
      { "is_paused": bool, "raw_state": {...}, "pause_file": "<path>" }
    """
    try:
        from .supervisor_control import get_supervisor_control
    except ImportError:
        from supervisor_control import get_supervisor_control
    try:
        return get_supervisor_control().status()
    except Exception as e:
        logger.error(f"supervisor_status error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/supervisor/pause")
async def supervisor_pause(payload: Optional[Dict[str, Any]] = None):
    """
    Pause new trade entries. Existing positions and management continue.

    Body (all optional):
      { "reason": "FOMC at 14:00", "until_ts": "2026-05-02T16:00:00Z" }

    until_ts: ISO-8601 UTC. If omitted, pause is indefinite until /resume.
    """
    try:
        from .supervisor_control import get_supervisor_control
    except ImportError:
        from supervisor_control import get_supervisor_control

    payload  = payload or {}
    reason   = payload.get("reason",   "no reason given")
    until_ts = payload.get("until_ts", None)

    try:
        new_state = get_supervisor_control().pause(reason=reason, until_ts=until_ts)
        logger.warning(f"⏸  Supervisor PAUSE applied: {reason}, until={until_ts}")
        return {"success": True, "state": new_state}
    except Exception as e:
        logger.error(f"supervisor_pause error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/supervisor/resume")
async def supervisor_resume():
    """
    Clear the supervisor pause. New entries are immediately allowed again.
    """
    try:
        from .supervisor_control import get_supervisor_control
    except ImportError:
        from supervisor_control import get_supervisor_control
    try:
        new_state = get_supervisor_control().resume()
        logger.info("▶  Supervisor RESUME applied — new entries allowed")
        return {"success": True, "state": new_state}
    except Exception as e:
        logger.error(f"supervisor_resume error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/grids/{symbol}/clear")
async def clear_grid(symbol: str):
    """Clear a grid registration for a symbol (allows new grid creation)."""
    try:
        # Use trading_bot's grid_lifecycle (authoritative source)
        grid_mgr = None
        if bot_integration.trading_bot and hasattr(
            bot_integration.trading_bot, "grid_lifecycle"
        ):
            grid_mgr = bot_integration.trading_bot.grid_lifecycle
        elif bot_integration.grid_manager:
            grid_mgr = bot_integration.grid_manager

        if grid_mgr:
            # Clear from memory
            cleared = grid_mgr.clear_grid(symbol.upper())
            if cleared:
                return {
                    "success": True,
                    "message": f"Grid cleared for {symbol.upper()}",
                }
            else:
                return {
                    "success": False,
                    "message": f"No grid found for {symbol.upper()}",
                }
        else:
            raise HTTPException(status_code=500, detail="Grid manager not available")
    except Exception as e:
        logger.error(f"Error clearing grid: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/grids")
async def get_grids():
    """Get all active grids."""
    try:
        # Use trading_bot's grid_lifecycle (authoritative source)
        grid_mgr = None
        if bot_integration.trading_bot and hasattr(
            bot_integration.trading_bot, "grid_lifecycle"
        ):
            grid_mgr = bot_integration.trading_bot.grid_lifecycle
        elif bot_integration.grid_manager:
            grid_mgr = bot_integration.grid_manager

        if grid_mgr:
            grids = grid_mgr.get_all_active_grids()
            return {"success": True, "data": grids}
        else:
            return {"success": True, "data": []}
    except Exception as e:
        logger.error(f"Error getting grids: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/signals")
async def get_signals():
    """Get recent signals from the event bus."""
    try:
        bot = bot_integration.trading_bot
        if not bot or not hasattr(bot, "event_bus"):
            return {"success": True, "data": []}

        # Import EventType
        try:
            from .event_system import EventType
        except ImportError:
            from event_system import EventType

        # Get signal events from event bus history
        event_bus = bot.event_bus
        signal_events = event_bus.get_events_by_type(EventType.SIGNAL_GENERATED, limit=50)

        signals = []
        for event in signal_events:
            signal_data = event.data
            signal = signal_data.get("signal")
            if signal:
                signals.append({
                    "id": event.id,
                    "timestamp": event.timestamp.isoformat(),
                    "symbol": signal_data.get("symbol", signal.asset if hasattr(signal, "asset") else "UNKNOWN"),
                    "strategy": signal.strategy.value if hasattr(signal, "strategy") and signal.strategy else "unknown",
                    "side": signal.side.value if hasattr(signal, "side") and signal.side else "unknown",
                    "entry_price": signal.entry_price if hasattr(signal, "entry_price") else 0,
                    "stop_loss": signal.stop_loss if hasattr(signal, "stop_loss") else 0,
                    "take_profit": signal.take_profit if hasattr(signal, "take_profit") else None,
                    "confidence": round(signal.confidence * 100, 1) if hasattr(signal, "confidence") else 0,
                    "is_valid": signal.is_valid() if hasattr(signal, "is_valid") else False,
                    "current_price": signal_data.get("current_price", 0),
                })

        return {"success": True, "data": signals, "count": len(signals)}
    except Exception as e:
        import traceback
        logger.error(f"Error getting signals: {e}")
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/debug/kline-cache")
async def get_kline_cache_debug():
    """Debug endpoint to check kline cache state."""
    try:
        ws_client = bot_integration.ws_client
        if not ws_client:
            return {"success": False, "error": "WebSocket client not initialized"}

        cache_info = {}
        for key, candles in ws_client._kline_cache.items():
            cache_info[key] = len(candles)

        # Check bot's ws_client and mtf_fetcher ws_client as well
        bot = bot_integration.trading_bot
        bot_ws_cache_keys = []
        mtf_ws_cache_keys = []
        if bot:
            if hasattr(bot, 'ws_client') and bot.ws_client:
                bot_ws_cache_keys = list(bot.ws_client._kline_cache.keys())
            if hasattr(bot, 'multi_tf_fetcher') and bot.multi_tf_fetcher and bot.multi_tf_fetcher.ws_client:
                mtf_ws_cache_keys = list(bot.multi_tf_fetcher.ws_client._kline_cache.keys())

        return {
            "success": True,
            "server_ws_connected": ws_client.is_connected() if hasattr(ws_client, 'is_connected') else "unknown",
            "server_ws_cache_pairs": len(ws_client._kline_cache),
            "server_ws_cache_keys": list(ws_client._kline_cache.keys()),
            "bot_ws_cache_keys": bot_ws_cache_keys,
            "mtf_ws_cache_keys": mtf_ws_cache_keys,
            "are_same_client": (ws_client is bot.ws_client) if bot and bot.ws_client else False,
            "candle_counts": cache_info,
        }
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/debug/test-signals/{symbol}")
async def test_signal_generation(symbol: str):
    """Debug endpoint to test signal generation for a symbol."""
    try:
        bot = bot_integration.trading_bot
        if not bot:
            return {"success": False, "error": "Trading bot not initialized"}

        # Get current price
        ws_client = bot_integration.ws_client
        price = 0.0
        if ws_client:
            price = ws_client.get_price(symbol.upper()) or 0.0

        # Check trading bot's ws_client status
        bot_ws_client = bot.ws_client
        bot_mtf_ws_client = bot.multi_tf_fetcher.ws_client if bot.multi_tf_fetcher else None

        ws_debug = {
            "bot_has_ws_client": bot_ws_client is not None,
            "bot_ws_id": id(bot_ws_client) if bot_ws_client else None,
            "mtf_has_ws_client": bot_mtf_ws_client is not None,
            "mtf_ws_id": id(bot_mtf_ws_client) if bot_mtf_ws_client else None,
            "server_ws_id": id(bot_integration.ws_client) if bot_integration.ws_client else None,
            "are_same": bot_ws_client is bot_integration.ws_client if (bot_ws_client and bot_integration.ws_client) else False,
        }

        # Check if bot's ws_client has data
        if bot_mtf_ws_client:
            test_data = bot_mtf_ws_client.get_kline_data(symbol.upper(), "15m")
            ws_debug["mtf_ws_has_15m_data"] = len(test_data) if test_data else 0

        # Try to get multi-TF data
        multi_tf_data = None
        execution_tf_data = None
        try:
            multi_tf_data = bot.multi_tf_fetcher.get_candles_multi_tf(
                symbol=symbol,
                timeframes=["15m", "1h", "4h"],
                lookback_candles=250,
            )
        except Exception as e:
            import traceback
            return {"success": False, "error": f"Failed to get multi_tf_data: {e}", "ws_debug": ws_debug, "traceback": traceback.format_exc()}

        try:
            execution_tf_data = bot.multi_tf_fetcher.get_candles_multi_tf(
                symbol=symbol,
                timeframes=["1m", "5m"],
                lookback_candles=50,
            )
        except Exception as e:
            execution_tf_data = None

        # Check data quality
        data_info = {}
        for tf, data in (multi_tf_data or {}).items():
            if isinstance(data, dict):
                data_info[tf] = {
                    "close_count": len(data.get("close", [])),
                    "has_volume": "volume" in data and len(data.get("volume", [])) > 0,
                    "last_close": data.get("close", [0])[-1] if data.get("close") else 0,
                }

        # Try to generate signals
        signals = []
        try:
            signals = bot.strategy_manager.generate_signals_for_market(
                symbol,
                multi_tf_data,
                price if price > 0 else data_info.get("15m", {}).get("last_close", 0),
                execution_tf_data=execution_tf_data,
            )
        except Exception as e:
            return {
                "success": False,
                "error": f"Signal generation failed: {e}",
                "price": price,
                "data_info": data_info,
            }

        # Format signals for response
        signal_info = []
        for sig in signals:
            signal_info.append({
                "side": sig.side.value if sig.side else "unknown",
                "entry_price": sig.entry_price,
                "stop_loss": sig.stop_loss,
                "take_profit": sig.take_profit,
                "confidence": sig.confidence,
                "rrr": sig.rrr,
                "strategy": getattr(sig, "strategy", "unknown"),
                "is_valid": sig.is_valid(),
            })

        # Add LiquidationCapture debug info for multiple timeframes
        liq_debug = {}
        try:
            # Check multiple timeframes
            for tf_name, tf_data_source in [("5m", execution_tf_data), ("15m", multi_tf_data), ("1h", multi_tf_data)]:
                trigger_data = tf_data_source.get(tf_name) if tf_data_source else None
                if not trigger_data or not trigger_data.get("close") or len(trigger_data.get("close", [])) < 10:
                    continue

                tf_debug = {}
                closes = trigger_data["close"]
                # Check cascade conditions
                start_price = closes[-10]
                end_price = closes[-1]
                price_move = (end_price - start_price) / start_price
                tf_debug["price_move_pct"] = round(price_move * 100, 2)
                tf_debug["price_move_ok"] = abs(price_move) >= 0.025

                # Volume spike
                if "volume" in trigger_data and len(trigger_data["volume"]) >= 10:
                    recent_volumes = trigger_data["volume"][-10:]
                    avg_volume = sum(recent_volumes[:-3]) / len(recent_volumes[:-3]) if len(recent_volumes[:-3]) > 0 else 0
                    current_volume = recent_volumes[-1]
                    volume_spike = current_volume / avg_volume if avg_volume > 0 else 0
                    tf_debug["volume_spike"] = round(volume_spike, 2)
                    tf_debug["volume_spike_ok"] = volume_spike >= 2.5

                # RSI
                from trading_bot_v2.indicators import calculate_rsi
                rsi = calculate_rsi(closes, period=14)
                tf_debug["rsi"] = round(rsi, 1)
                tf_debug["rsi_extreme"] = rsi <= 20 or rsi >= 80

                liq_debug[tf_name] = tf_debug
        except Exception as e:
            liq_debug["error"] = str(e)

        # Get bot's internal account balance
        bot_balance = 0.0
        try:
            bot_balance = bot._get_account_balance()
        except Exception as e:
            bot_balance = f"Error: {e}"

        return {
            "success": True,
            "symbol": symbol,
            "price": price,
            "data_info": data_info,
            "signals_count": len(signals),
            "signals": signal_info,
            "liquidation_debug": liq_debug,
            "bot_account_balance": bot_balance,
        }
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/debug/signal-processing")
async def get_signal_processing_log():
    """Debug endpoint to check signal processing stages - now uses SignalLogger."""
    try:
        bot = bot_integration.trading_bot
        if not bot:
            return {"success": False, "error": "Trading bot not available"}

        # Use the new SignalLogger
        if hasattr(bot, "signal_logger"):
            stats = bot.signal_logger.get_statistics()
            recent = bot.signal_logger.get_recent_signals(count=20)
            return {
                "success": True,
                "statistics": stats,
                "recent_signals": recent,
                "csv_path": str(bot.signal_logger.csv_path),
            }
        else:
            # Fallback to old method
            log = getattr(bot, "_signal_processing_log", [])
            return {
                "success": True,
                "total_processed": len(log),
                "recent_signals": log[-10:] if log else [],
            }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/signals/stats")
async def get_signal_statistics():
    """Get signal generation and execution statistics."""
    try:
        bot = bot_integration.trading_bot
        if not bot or not hasattr(bot, "signal_logger"):
            return {"success": False, "error": "Signal logger not available"}

        stats = bot.signal_logger.get_statistics()
        return {"success": True, "data": stats}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/signals/recent")
async def get_recent_signals(count: int = 50):
    """Get recent signals from the signal logger."""
    try:
        bot = bot_integration.trading_bot
        if not bot or not hasattr(bot, "signal_logger"):
            return {"success": False, "error": "Signal logger not available"}

        signals = bot.signal_logger.get_recent_signals(count=count)
        return {"success": True, "data": signals, "count": len(signals)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/signals/by-status/{status}")
async def get_signals_by_status(status: str):
    """Get signals filtered by status (generated, executed, rejected, failed)."""
    try:
        bot = bot_integration.trading_bot
        if not bot or not hasattr(bot, "signal_logger"):
            return {"success": False, "error": "Signal logger not available"}

        if status not in ["generated", "executed", "rejected", "failed"]:
            return {"success": False, "error": f"Invalid status: {status}"}

        signals = bot.signal_logger.get_signals_by_status(status)
        return {"success": True, "data": signals, "count": len(signals)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/signals/csv-path")
async def get_signals_csv_path():
    """Get the path to the signals CSV file for easy access."""
    try:
        bot = bot_integration.trading_bot
        if not bot or not hasattr(bot, "signal_logger"):
            return {"success": False, "error": "Signal logger not available"}

        csv_path = str(bot.signal_logger.csv_path)
        exists = bot.signal_logger.csv_path.exists()

        return {
            "success": True,
            "csv_path": csv_path,
            "exists": exists,
            "tip": "Open this file in Excel or Google Sheets for easy viewing",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/debug/key-config")
async def check_key_configuration():
    """Debug endpoint to check Pacifica API key configuration."""
    try:
        client = bot_integration.pacifica_client
        if not client:
            return {"success": False, "error": "Pacifica client not available"}

        agent_wallet_pubkey = client.agent_wallet_public_key
        account_pubkey = client.account_public_key

        return {
            "success": True,
            "agent_wallet_public_key": agent_wallet_pubkey,
            "account_public_key": account_pubkey,
            "keys_match": agent_wallet_pubkey == account_pubkey,
            "note": "If keys_match is True, that's the problem - they should be different"
        }
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/debug/test-order")
async def test_order_placement():
    """Debug endpoint to test order placement and see full API response."""
    try:
        client = bot_integration.pacifica_client
        if not client:
            return {"success": False, "error": "Pacifica client not available"}

        # Test with a tiny order that should fail (below minimum)
        # This will show us the exact API error format
        import traceback
        try:
            result = client.place_order(
                symbol="BTC",
                side="buy",
                quantity=0.00001,  # Tiny amount
                order_type="limit",
                price=60000.0  # Far below market
            )
            return {"success": True, "order_result": result}
        except ValueError as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": "ValueError",
                "full_error": repr(e)
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
                "traceback": traceback.format_exc()
            }
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/debug/event-history")
async def get_event_history():
    """Debug endpoint to check recent events from the event bus."""
    try:
        bot = bot_integration.trading_bot
        if not bot or not hasattr(bot, "event_bus"):
            return {"success": False, "error": "Trading bot or event bus not available"}

        event_bus = bot.event_bus
        recent_events = []
        for event in event_bus._event_history[-20:]:  # Last 20 events
            recent_events.append({
                "id": event.id,
                "type": event.event_type.value,
                "source": event.source,
                "timestamp": event.timestamp.isoformat(),
                "data_keys": list(event.data.keys()) if isinstance(event.data, dict) else str(type(event.data)),
            })

        # Get callback errors if any
        callback_errors = getattr(event_bus, "_callback_errors", [])

        return {
            "success": True,
            "total_published": getattr(event_bus, "_published_count", "n/a"),
            "history_window": len(event_bus._event_history),
            "max_history": event_bus._max_history,
            "subscribers": {k.value: len(v) for k, v in event_bus._subscribers.items()},
            "recent_events": recent_events,
            "callback_errors": callback_errors[-5:] if callback_errors else [],
        }
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/debug/trigger-signals")
async def trigger_signal_generation():
    """
    Debug endpoint that manually triggers the trading loop's signal generation.
    Traces every step to identify why signals aren't being generated or published.
    """
    try:
        bot = bot_integration.trading_bot
        if not bot:
            return {"success": False, "error": "Trading bot not initialized"}

        trace = {
            "bot_running": bot._running_event.is_set() if hasattr(bot, "_running_event") else "unknown",
            "markets": [],
            "steps": [],
        }

        # Step 1: Get markets
        try:
            markets = bot.client.get_markets()
            trace["steps"].append({"step": "get_markets", "success": True, "count": len(markets)})
        except Exception as e:
            trace["steps"].append({"step": "get_markets", "success": False, "error": str(e)})
            return {"success": False, "trace": trace}

        # Core symbols
        core_symbols = ["BTC", "ETH", "LTC", "SOL", "SUI", "AVAX", "XRP", "DOGE"]
        markets_to_process = [m for m in markets if m.get("symbol") in core_symbols]
        trace["steps"].append({"step": "filter_markets", "count": len(markets_to_process), "symbols": [m.get("symbol") for m in markets_to_process]})

        # Process each symbol
        for market in markets_to_process[:3]:  # Only process first 3 for speed
            symbol = market.get("symbol")
            if not symbol:
                continue

            market_trace = {"symbol": symbol, "steps": []}

            # Step: Get price
            try:
                ticker = bot._get_ticker_ws(symbol)
                current_price = float(ticker.get("last", 0))
                market_trace["current_price"] = current_price
                market_trace["steps"].append({"step": "get_price", "success": True, "price": current_price})
            except Exception as e:
                market_trace["steps"].append({"step": "get_price", "success": False, "error": str(e)})
                trace["markets"].append(market_trace)
                continue

            if current_price <= 0:
                market_trace["steps"].append({"step": "price_check", "success": False, "error": "Invalid price"})
                trace["markets"].append(market_trace)
                continue

            # Step: Get multi-TF data
            try:
                multi_tf_data = bot.multi_tf_fetcher.get_candles_multi_tf(
                    symbol=symbol,
                    timeframes=["15m", "1h", "4h"],
                    lookback_candles=250,
                )
                market_trace["steps"].append({
                    "step": "get_multi_tf_data",
                    "success": True,
                    "timeframes": list(multi_tf_data.keys()),
                    "candle_counts": {tf: len(data.get("close", [])) for tf, data in multi_tf_data.items()}
                })
            except Exception as e:
                import traceback
                market_trace["steps"].append({"step": "get_multi_tf_data", "success": False, "error": str(e), "traceback": traceback.format_exc()})
                trace["markets"].append(market_trace)
                continue

            # Step: Generate signals
            try:
                signals = bot.strategy_manager.generate_signals_for_market(
                    symbol, multi_tf_data, current_price
                )
                market_trace["steps"].append({
                    "step": "generate_signals",
                    "success": True,
                    "signal_count": len(signals),
                    "signals": [
                        {
                            "strategy": str(getattr(s, "strategy", "unknown")),
                            "side": s.side.value if s.side else "unknown",
                            "confidence": s.confidence,
                            "is_valid": s.is_valid(),
                            "validation_flags": {
                                "volume_confirmation": s.volume_confirmation,
                                "multi_timeframe_alignment": s.multi_timeframe_alignment,
                                "support_resistance_valid": s.support_resistance_valid,
                                "rrr_meets_minimum": s.rrr_meets_minimum,
                                "liquidation_buffer_safe": s.liquidation_buffer_safe,
                                "account_risk_ok": s.account_risk_ok,
                                "margin_drawdown_ok": s.margin_drawdown_ok,
                                "forbidden_conditions_clear": s.forbidden_conditions_clear,
                            }
                        }
                        for s in signals
                    ]
                })

                # Step: Check which signals would be published
                for signal in signals:
                    if signal.is_valid():
                        should_skip = bot.strategy_manager.should_skip_signal(signal)
                        market_trace["steps"].append({
                            "step": "check_should_skip",
                            "signal_strategy": str(getattr(signal, "strategy", "unknown")),
                            "should_skip": should_skip,
                        })

                        if not should_skip:
                            # Actually publish the signal event
                            try:
                                from .event_system import EventType
                            except ImportError:
                                from event_system import EventType

                            bot.event_bus.publish_event(
                                EventType.SIGNAL_GENERATED,
                                {
                                    "signal": signal,
                                    "market_data": multi_tf_data,
                                    "current_price": current_price,
                                    "symbol": symbol,
                                },
                                "debug_trigger",
                            )
                            market_trace["steps"].append({
                                "step": "publish_signal",
                                "success": True,
                                "signal_strategy": str(getattr(signal, "strategy", "unknown")),
                            })
                    else:
                        market_trace["steps"].append({
                            "step": "signal_invalid",
                            "signal_strategy": str(getattr(signal, "strategy", "unknown")),
                        })

            except Exception as e:
                import traceback
                market_trace["steps"].append({"step": "generate_signals", "success": False, "error": str(e), "traceback": traceback.format_exc()})

            trace["markets"].append(market_trace)

        # Get current event count (total_published is monotonic; history_window saturates at max_history)
        trace["total_published"] = getattr(bot.event_bus, "_published_count", 0)
        trace["history_window"] = len(bot.event_bus._event_history)

        return {"success": True, "trace": trace}

    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/debug/test-signal-handler")
async def test_signal_handler():
    """Debug: manually invoke _handle_signal_generated and trace every step."""
    import traceback
    try:
        bot = bot_integration.trading_bot
        if not bot:
            return {"success": False, "error": "Bot not available"}

        result = {"steps": []}

        # Get a real signal
        markets = bot.client.get_markets()
        core = [m for m in markets if m.get("symbol") in ["AVAX", "SUI", "XRP"]]
        if not core:
            return {"success": False, "error": "No markets"}

        symbol = core[0].get("symbol")
        ticker = bot._get_ticker_ws(symbol)
        price = float(ticker.get("last", 0))
        result["steps"].append({"step": "price", "symbol": symbol, "price": price})

        multi_tf = bot.multi_tf_fetcher.get_candles_multi_tf(symbol=symbol, timeframes=["15m", "1h", "4h"], lookback_candles=250)
        signals = bot.strategy_manager.generate_signals_for_market(symbol, multi_tf, price)
        result["steps"].append({"step": "signals", "count": len(signals), "valid": [s.is_valid() for s in signals]})

        if not signals:
            return {"success": True, "result": result, "note": "No signals generated"}

        signal = signals[0]
        result["steps"].append({
            "step": "signal_detail",
            "strategy": signal.strategy.name,
            "side": signal.side.name,
            "entry": signal.entry_price,
            "is_valid": signal.is_valid(),
        })

        # Step 1: should_execute_signal
        stats_before = bot.signal_logger.get_statistics()
        try:
            should_exec = bot._should_execute_signal(signal)
            result["steps"].append({"step": "should_execute", "result": should_exec})
        except Exception as e:
            result["steps"].append({"step": "should_execute", "error": str(e), "tb": traceback.format_exc()})
            stats_after = bot.signal_logger.get_statistics()
            result["stats_before"] = stats_before
            result["stats_after"] = stats_after
            return {"success": True, "result": result}

        stats_after_validate = bot.signal_logger.get_statistics()
        result["steps"].append({"step": "stats_after_validate", "stats": stats_after_validate})

        # Step 2: coordinate execution
        if should_exec:
            try:
                log_entry = {"timestamp": "test", "symbol": signal.asset}
                bot._coordinate_signal_execution(signal, log_entry)
                result["steps"].append({"step": "coordinate", "result": "completed"})
            except Exception as e:
                result["steps"].append({"step": "coordinate", "error": str(e), "tb": traceback.format_exc()})

        stats_final = bot.signal_logger.get_statistics()
        result["stats_before"] = stats_before
        result["stats_final"] = stats_final
        return {"success": True, "result": result}

    except Exception as e:
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/debug/bot-internals")
async def get_bot_internals():
    """Debug endpoint to check trading bot internal state."""
    try:
        bot = bot_integration.trading_bot
        if not bot:
            return {"success": False, "error": "Trading bot not initialized"}

        # Check for both thread naming conventions
        thread_alive = False
        thread_name = None
        if hasattr(bot, "thread") and bot.thread:
            thread_alive = bot.thread.is_alive()
            thread_name = f"thread: {bot.thread.name}"
        elif hasattr(bot, "_trading_thread") and bot._trading_thread:
            thread_alive = bot._trading_thread.is_alive()
            thread_name = f"_trading_thread: {bot._trading_thread.name}"

        # Check WS client - use connected property or method
        ws_connected = False
        if hasattr(bot, "ws_client") and bot.ws_client:
            if hasattr(bot.ws_client, "_connected"):
                ws_connected = bot.ws_client._connected
            elif hasattr(bot.ws_client, "connected"):
                ws_connected = bot.ws_client.connected

        return {
            "success": True,
            "running_event_set": bot._running_event.is_set() if hasattr(bot, "_running_event") else "unknown",
            "trading_thread_alive": thread_alive,
            "trading_thread_name": thread_name,
            "ws_client_connected": ws_connected,
            "strategy_manager_strategies": list(bot.strategy_manager.strategies.keys()) if hasattr(bot, "strategy_manager") else [],
            "event_bus_subscribers": {k.value: len(v) for k, v in bot.event_bus._subscribers.items()} if hasattr(bot, "event_bus") else {},
            "event_total_published": getattr(bot.event_bus, "_published_count", 0) if hasattr(bot, "event_bus") else 0,
            "event_history_window": len(bot.event_bus._event_history) if hasattr(bot, "event_bus") else 0,
        }
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/debug/grid-state")
async def get_grid_state():
    """Debug: Check grid lifecycle manager state."""
    try:
        bot = bot_integration.trading_bot
        if not bot:
            return {"success": False, "error": "Trading bot not initialized"}

        grid_lifecycle = getattr(bot, "grid_lifecycle", None)
        if not grid_lifecycle:
            return {"success": False, "error": "Grid lifecycle manager not found"}

        return {
            "success": True,
            "active_grids": list(grid_lifecycle._grids.keys()),
            "grid_count": len(grid_lifecycle._grids),
            "grid_details": {
                symbol: {
                    "capital": grid.get("grid_capital"),
                    "regime": grid.get("regime_on_creation"),
                    "created_at": str(grid.get("created_at")),
                }
                for symbol, grid in grid_lifecycle._grids.items()
            },
        }
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/debug/balance-raw")
async def get_balance_raw():
    """Get raw balance data from Pacifica API for debugging."""
    try:
        if not bot_integration.pacifica_client:
            return {"success": False, "error": "Pacifica client not initialized"}

        balance_data = bot_integration.pacifica_client.get_balance()
        return {
            "success": True,
            "raw_data": balance_data,
            "keys": list(balance_data.keys()) if isinstance(balance_data, dict) else "not_dict",
        }
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/debug/loop-status")
async def get_loop_status():
    """Check trading loop iteration status."""
    try:
        bot = bot_integration.trading_bot
        if not bot:
            return {"success": False, "error": "Trading bot not initialized"}

        loop_iter = getattr(bot, "_loop_iteration", None)
        last_loop = getattr(bot, "_last_loop_time", None)
        loop_step = getattr(bot, "_loop_step", None)
        events_generated = getattr(bot, "_loop_events_generated", None)

        return {
            "success": True,
            "loop_iteration": loop_iter,
            "last_loop_time": last_loop.isoformat() if last_loop else None,
            "current_step": loop_step,
            "events_generated_last_iteration": events_generated,
            "running_event_set": bot._running_event.is_set() if hasattr(bot, "_running_event") else None,
            "thread_alive": bot.thread.is_alive() if hasattr(bot, "thread") and bot.thread else False,
            "total_published": getattr(bot.event_bus, "_published_count", 0) if hasattr(bot, "event_bus") else 0,
            "history_window": len(bot.event_bus._event_history) if hasattr(bot, "event_bus") else 0,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/debug/call-actual-generate-signals")
async def call_actual_generate_signals():
    """Test signal generation with inline implementation to avoid module reload issues."""
    try:
        bot = bot_integration.trading_bot
        if not bot:
            return {"success": False, "error": "Trading bot not initialized"}

        trace = {"steps": []}
        count_before = getattr(bot.event_bus, "_published_count", 0)

        try:
            from .event_system import EventType
        except ImportError:
            from event_system import EventType

        # Inline implementation matching _generate_and_publish_signals
        markets = bot.client.get_markets()
        trace["steps"].append(f"got {len(markets)} markets")

        core_symbols = ["BTC", "ETH", "LTC", "SOL", "SUI", "AVAX", "XRP", "DOGE"]
        markets_to_process = [m for m in markets if m.get("symbol") in core_symbols]

        for market in markets_to_process:
            symbol = market.get("symbol")
            if not symbol:
                continue

            try:
                # Get price
                ticker = bot._get_ticker_ws(symbol)
                current_price = float(ticker.get("last", 0))
                if current_price <= 0:
                    continue

                # Get multi-tf data
                multi_tf_data = bot.multi_tf_fetcher.get_candles_multi_tf(
                    symbol=symbol, timeframes=["15m", "1h", "4h"], lookback_candles=250
                )

                # Generate signals
                trace["steps"].append(f"{symbol}: calling generate_signals_for_market")
                signals = bot.strategy_manager.generate_signals_for_market(
                    symbol, multi_tf_data, current_price
                )
                trace["steps"].append(f"{symbol}: got {len(signals)} signals")

                # This is the part that might be failing - let's see what happens
                try:
                    regime_data = multi_tf_data.get("4h", multi_tf_data.get("1h", {}))
                    cached_regime = bot.strategy_manager.regime_detector._regime_cache.get(symbol) if hasattr(bot.strategy_manager.regime_detector, '_regime_cache') else None
                    trace["steps"].append(f"{symbol}: cached_regime type={type(cached_regime).__name__}, value={cached_regime}")
                except Exception as e:
                    trace["steps"].append(f"{symbol}: regime_cache error: {e}")

                # Iterate signals
                trace["steps"].append(f"{symbol}: iterating over {len(signals)} signals")
                for signal in signals:
                    trace["steps"].append(f"{symbol}: checking signal {getattr(signal, 'strategy', '?')}")
                    is_valid = signal.is_valid()
                    trace["steps"].append(f"{symbol}: is_valid={is_valid}")

                    if is_valid:
                        should_skip = bot.strategy_manager.should_skip_signal(signal)
                        trace["steps"].append(f"{symbol}: should_skip={should_skip}")

                        if not should_skip:
                            bot.event_bus.publish_event(
                                EventType.SIGNAL_GENERATED,
                                {
                                    "signal": signal,
                                    "market_data": multi_tf_data,
                                    "current_price": current_price,
                                    "symbol": symbol,
                                },
                                "inline_test",
                            )
                            trace["steps"].append(f"{symbol}: signal published!")

            except Exception as e:
                import traceback as tb
                trace["steps"].append(f"{symbol}: EXCEPTION: {e}")
                trace["steps"].append(f"{symbol}: tb: {tb.format_exc()[:300]}")

        count_after = getattr(bot.event_bus, "_published_count", 0)

        return {
            "success": True,
            "published_before": count_before,
            "published_after": count_after,
            "new_events": count_after - count_before,
            "trace": trace,
        }
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@app.get("/api/debug/call-generate-signals")
async def call_generate_signals():
    """Directly call the bot's _generate_and_publish_signals method with tracing."""
    try:
        bot = bot_integration.trading_bot
        if not bot:
            return {"success": False, "error": "Trading bot not initialized"}

        trace = {"steps": []}

        # Get event count before (use monotonic counter, not len() which saturates at max_history)
        count_before = getattr(bot.event_bus, "_published_count", 0)
        trace["published_before"] = count_before

        # Step 1: Get markets
        try:
            markets = bot.client.get_markets()
            trace["steps"].append({"step": "get_markets", "count": len(markets)})
        except Exception as e:
            trace["steps"].append({"step": "get_markets", "error": str(e)})
            return {"success": False, "trace": trace}

        # Step 2: Filter to core symbols
        core_symbols = ["BTC", "ETH", "LTC", "SOL", "SUI", "AVAX", "XRP", "DOGE"]
        markets_to_process = [m for m in markets if m.get("symbol") in core_symbols]
        trace["steps"].append({"step": "filter_markets", "count": len(markets_to_process)})

        # Step 3: Process each market
        for market in markets_to_process[:3]:  # First 3 for speed
            symbol = market.get("symbol")
            if not symbol:
                continue

            market_step = {"symbol": symbol, "substeps": []}

            # Get price
            try:
                ticker = bot._get_ticker_ws(symbol)
                current_price = float(ticker.get("last", 0))
                market_step["price"] = current_price
                market_step["substeps"].append({"substep": "price", "value": current_price})
            except Exception as e:
                market_step["substeps"].append({"substep": "price", "error": str(e)})
                trace["steps"].append(market_step)
                continue

            if current_price <= 0:
                market_step["substeps"].append({"substep": "price_check", "error": "Invalid"})
                trace["steps"].append(market_step)
                continue

            # Get multi-tf data
            try:
                multi_tf_data = bot.multi_tf_fetcher.get_candles_multi_tf(
                    symbol=symbol,
                    timeframes=["15m", "1h", "4h"],
                    lookback_candles=250,
                )
                candle_counts = {tf: len(data.get("close", [])) for tf, data in multi_tf_data.items()}
                market_step["substeps"].append({"substep": "multi_tf", "candles": candle_counts})
            except Exception as e:
                market_step["substeps"].append({"substep": "multi_tf", "error": str(e)})
                trace["steps"].append(market_step)
                continue

            # Generate signals
            try:
                signals = bot.strategy_manager.generate_signals_for_market(
                    symbol, multi_tf_data, current_price
                )
                market_step["signal_count"] = len(signals)
                market_step["substeps"].append({"substep": "generate", "count": len(signals)})

                # Check each signal
                for sig in signals:
                    is_valid = sig.is_valid()
                    should_skip = bot.strategy_manager.should_skip_signal(sig) if is_valid else None
                    market_step["substeps"].append({
                        "substep": "signal_check",
                        "strategy": str(getattr(sig, "strategy", "?")),
                        "is_valid": is_valid,
                        "should_skip": should_skip,
                    })

                    if is_valid and not should_skip:
                        # Publish signal
                        try:
                            from .event_system import EventType
                        except ImportError:
                            from event_system import EventType

                        bot.event_bus.publish_event(
                            EventType.SIGNAL_GENERATED,
                            {
                                "signal": sig,
                                "market_data": multi_tf_data,
                                "current_price": current_price,
                                "symbol": symbol,
                            },
                            "call_generate_signals",
                        )
                        market_step["substeps"].append({"substep": "publish", "success": True})

            except Exception as e:
                import traceback as tb
                market_step["substeps"].append({"substep": "generate", "error": str(e), "tb": tb.format_exc()})

            trace["steps"].append(market_step)

        # Get event count after
        count_after = getattr(bot.event_bus, "_published_count", 0)
        trace["published_after"] = count_after
        trace["new_events"] = count_after - count_before

        return {"success": True, "trace": trace}
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


@app.post("/api/debug/clear-regime-cache")
async def clear_regime_cache(symbol: str = None):
    """Clear the regime detector's in-memory cache. Forces re-detection on the next loop.
    Pass ?symbol=BTC to clear a single symbol, or omit to clear all symbols.
    """
    try:
        bot = bot_integration.trading_bot
        if not bot or not hasattr(bot, "strategy_manager"):
            return {"success": False, "error": "Trading bot not initialized"}
        detector = bot.strategy_manager.regime_detector
        before = list(detector._regime_cache.keys())
        detector.clear_regime_cache(symbol if symbol else None)
        after = list(detector._regime_cache.keys())
        return {
            "success": True,
            "cleared": symbol if symbol else "all",
            "cache_before": before,
            "cache_after": after,
        }
    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "traceback": traceback.format_exc()}


if __name__ == "__main__":
    # Determine module path based on how script is run
    # When run as module (-m), __package__ is set; when run directly, it's None
    if __package__:
        # Running as module: python -m trading_bot_v2.api_server
        app_path = f"{__package__}.api_server:app"
    else:
        # Running as script: python api_server.py
        app_path = "api_server:app"

    uvicorn.run(app_path, host="0.0.0.0", port=8000, reload=False, log_level="info")
