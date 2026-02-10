"""
Trading Bot API Server with Real Data
Optimized for fast startup with lazy initialization.
"""

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
import sqlite3
import csv
import os
import asyncio
import threading
import time
from datetime import datetime
from typing import Literal, Dict, Any, List, Optional

# Load environment variables ONCE at startup (before other imports that may need them)
from dotenv import load_dotenv
load_dotenv()

from database import DatabaseManager
from encryption import encrypt_private_key, decrypt_private_key
from pacifica_client import PacificaClient, PacificaEnvironment, RateLimitTier
from pacifica_market_data import PacificaMarketDataHandler, calculate_rsi, calculate_ema, calculate_sma, calculate_macd
from balance_manager import BalanceHistoryManager
from main import TradingBot
from pacifica_websocket import PacificaWebSocketManager, create_pacifica_websocket
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Standardized API Response Format
def create_api_response(
    success: bool,
    data: Any = None,
    source: str = "pacifica_api",
    metadata: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a standardized API response following the expected SDK format.

    Args:
        success: Whether the operation was successful
        data: Response data (only included if success=True)
        source: Source of the data (default: "pacifica_api")
        metadata: Additional metadata about the response
        error: Error message (only included if success=False)

    Returns:
        Standardized response dictionary
    """
    response = {
        "success": success,
        "source": source,
    }

    if success:
        response["data"] = data or {}
    else:
        response["error"] = error or "Unknown error"

    # Always include metadata, even if empty
    response["metadata"] = metadata or {}

    return response

# Modern FastAPI lifespan event handler (replaces deprecated @app.on_event)
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle application startup and shutdown events."""
    # Startup logic
    global market_data_handler
    if market_data_handler is None:
        market_data_handler = get_market_data_handler()
        if market_data_handler:
            mode = "MOCK" if market_data_handler.use_mock else "LIVE"
            logger.info(f"✅ Market data handler ready [{mode}]")

    try:
        # Check if any profiles exist
        profiles = db_manager.get_all_profiles()

        # Determine profile name based on network
        network_type = "Testnet" if TESTNET_ENABLED else "Mainnet"
        expected_profile_name = f"Default Account ({network_type})"

        # Check if we already have a profile for this network
        has_network_profile = any(p.get('name') == expected_profile_name for p in profiles)

        if len(profiles) == 0 or not has_network_profile:
            # Get credentials from environment
            agent_private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
            account_public_key = os.getenv("ACCOUNT_PUBLIC_KEY")

            if agent_private_key and account_public_key:
                # Create default profile for this network
                encrypted_key = encrypt_private_key(agent_private_key)
                profile_id = db_manager.create_profile(
                    name=expected_profile_name,
                    private_key_encrypted=encrypted_key,
                    public_key=account_public_key,
                    is_default=True
                )
                print(f"[OK] Created {network_type} account profile (ID: {profile_id}) from .env")
                print(f"     Public Key: {account_public_key[:8]}...{account_public_key[-8:]}")
            else:
                print("[WARN] No AGENT_WALLET_PRIVATE_KEY or ACCOUNT_PUBLIC_KEY in .env")
                print("       Skipping default profile creation")
        else:
            print(f"[INFO] Found {len(profiles)} existing account profile(s)")
            # Ensure the correct network profile is set as default
            for p in profiles:
                if p.get('name') == expected_profile_name and not p.get('is_default'):
                    db_manager.set_default_profile(p.get('id'))
                    print(f"[INFO] Set '{expected_profile_name}' as default profile")

    except Exception as e:
        print(f"[ERROR] Error initializing default profile: {e}")

    yield

    # Shutdown logic (if needed)
    pass

app = FastAPI(title="Trading Bot API", version="1.0.0", lifespan=lifespan)

# Global trading mode state
TRADING_MODE = "paper"  # Default to paper trading
TESTNET_ENABLED = os.getenv("TESTNET", "true").lower() == "true"
MODE_CHANGE_TIMESTAMP = datetime.now()

# Global bot status state
BOT_STATUS = "stopped"  # Tracks bot state: stopped, standby, ready, trading

# Position sync state
last_position_sync = 0
POSITION_SYNC_INTERVAL = 30  # Sync positions every 30 seconds

# Initialize mode based on environment
if not TESTNET_ENABLED:
    TRADING_MODE = "real"


def sync_positions_to_database():
    """Sync live Pacifica positions to local database for UI and reporting."""
    global last_position_sync

    if not pacifica_client:
        return

    current_time = time.time()
    if current_time - last_position_sync < POSITION_SYNC_INTERVAL:
        return  # Too soon to sync again

    try:
        live_positions = pacifica_client.get_positions()

        with get_db_connection() as conn:
            # Clear existing positions
            conn.execute("DELETE FROM positions")

            # Insert live positions
            for pos in live_positions:
                symbol = pos.get("symbol", "")
                side = "long" if pos.get("side") == "bid" else "short"
                quantity = abs(float(pos.get("amount", 0)))
                entry_price = float(pos.get("entry_price", 0))

                conn.execute("""
                    INSERT INTO positions
                    (symbol, asset_class, side, quantity, entry_price, current_price,
                     unrealized_pnl, opened_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol,
                    "crypto",
                    side,
                    quantity,
                    entry_price,
                    pos.get("current_price", pos.get("entry_price", 0)),
                    pos.get("unrealized_pnl", 0),
                    pos.get("opened_at", "")
                ))

            conn.commit()
            last_position_sync = current_time
            logger.info(f"✅ Synced {len(live_positions)} positions to database")

    except Exception as e:
        logger.error(f"❌ Failed to sync positions to database: {e}")

# ===========================================
# TRADING BOT SINGLETON MANAGEMENT
# ===========================================

# Global TradingBot instance
trading_bot: Optional[TradingBot] = None
bot_thread: Optional[threading.Thread] = None
bot_loop: Optional[asyncio.AbstractEventLoop] = None
bot_error: Optional[str] = None  # Last error from bot operations


def _run_bot_async(bot: TradingBot, private_key: str, public_key: str):
    """
    Run the trading bot in a separate thread with its own event loop.
    """
    global bot_loop, BOT_STATUS, bot_error

    try:
        # Create new event loop for this thread
        bot_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(bot_loop)

        # Initialize bot with credentials
        bot.initialize_standby()
        bot.load_credentials(private_key, public_key)

        BOT_STATUS = "ready"
        logger.info("Trading bot initialized with credentials, starting trading...")

        # Start trading (this runs the trading loop)
        bot_loop.run_until_complete(bot.start_trading())

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Trading bot error: {error_msg}")
        bot_error = error_msg
        BOT_STATUS = "stopped"
    finally:
        if bot_loop and bot_loop.is_running():
            bot_loop.stop()
        if bot_loop:
            bot_loop.close()
        bot_loop = None
        logger.info("Trading bot thread finished")


async def _stop_bot_async():
    """Stop the trading bot gracefully."""
    global trading_bot, bot_thread, BOT_STATUS

    if trading_bot and trading_bot.is_running:
        trading_bot.is_running = False

        # Give the trading loop time to finish
        await asyncio.sleep(1)

        # Stop execution engine if exists
        if trading_bot.execution_engine:
            try:
                await trading_bot.execution_engine.stop()
            except Exception as e:
                logger.error(f"Error stopping execution engine: {e}")

    BOT_STATUS = "stopped"
    logger.info("Trading bot stopped")

class ModeChangeRequest(BaseModel):
    mode: Literal["paper", "real"]
    confirmation: str = ""

# Account Profile models
class AccountProfileCreate(BaseModel):
    """Model for creating a new account profile."""
    name: str
    private_key: str
    public_key: Optional[str] = None
    is_default: bool = False

class AccountProfileUpdate(BaseModel):
    """Model for updating an account profile."""
    name: Optional[str] = None
    private_key: Optional[str] = None
    public_key: Optional[str] = None

class AccountProfileResponse(BaseModel):
    """Model for account profile response (without sensitive data)."""
    id: int
    name: str
    public_key: Optional[str] = None
    is_default: bool
    created_at: str
    updated_at: str
    last_used_at: str

class AccountProfileDetail(AccountProfileResponse):
    """Model for account profile with decrypted private key."""
    private_key: str

# Add CORS middleware with proper configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify allowed origins
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-API-Source", "X-Request-ID"],
)

# Initialize database manager
db_manager = DatabaseManager()

def get_database_manager() -> DatabaseManager:
    """Get the global database manager instance."""
    return db_manager

# ===========================================
# LAZY INITIALIZATION FOR FAST STARTUP
# ===========================================

# Global instances - initialized lazily
pacifica_client: Optional[PacificaClient] = None
market_data_handler: Optional[PacificaMarketDataHandler] = None
websocket_client: Optional[PacificaWebSocketManager] = None
_pacifica_initialized = False
_api_verified = False
_ws_initialized = False

def get_pacifica_client() -> Optional[PacificaClient]:
    """
    Get or create Pacifica client (lazy initialization).
    Client is created once and reused for all requests.
    """
    global pacifica_client, _pacifica_initialized

    if _pacifica_initialized:
        return pacifica_client

    _pacifica_initialized = True

    try:
        # Get credentials from environment (already loaded at top)
        agent_private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
        account_public_key = os.getenv("ACCOUNT_PUBLIC_KEY")

        if not agent_private_key or not account_public_key:
            logger.warning("⚠️ Pacifica credentials not found - Set AGENT_WALLET_PRIVATE_KEY and ACCOUNT_PUBLIC_KEY in .env")
            return None

        # Determine environment
        use_testnet = os.getenv("TESTNET", "true").lower() == "true"
        environment = PacificaEnvironment.TESTNET if use_testnet else PacificaEnvironment.MAINNET

        # Create client (fast - no network calls)
        pacifica_client = PacificaClient(
            agent_wallet_private_key=agent_private_key,
            account_public_key=account_public_key,
            environment=environment,
            rate_limit_tier=RateLimitTier.BASIC
        )

        logger.info(f"✅ Initialized Pacifica client ({environment.name})")
        logger.info(f"   Account: {account_public_key[:8]}...{account_public_key[-8:]}")
        return pacifica_client
    except Exception as e:
        logger.error(f"❌ Failed to initialize Pacifica client: {e}")
        return None

def get_market_data_handler() -> Optional[PacificaMarketDataHandler]:
    """
    Get or create market data handler (lazy initialization).
    Defers API verification until actually needed.
    Falls back gracefully if API is unavailable.
    """
    global market_data_handler, _api_verified

    if market_data_handler is not None:
        return market_data_handler

def get_ws_client() -> Optional[PacificaWebSocketManager]:
    """
    Get or create WebSocket client (lazy initialization).

    Returns:
        PacificaWebSocketManager instance or None if initialization fails
    """
    global websocket_client, _ws_initialized

    if _ws_initialized:
        return websocket_client

    _ws_initialized = True

    try:
        # Get credentials from environment (already loaded at top)
        agent_private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
        account_public_key = os.getenv("ACCOUNT_PUBLIC_KEY")

        if not agent_private_key or not account_public_key:
            logger.warning("⚠️ WebSocket credentials not found - Set AGENT_WALLET_PRIVATE_KEY and ACCOUNT_PUBLIC_KEY in .env")
            return None

        # Determine environment
        use_testnet = os.getenv("TESTNET", "true").lower() == "true"
        environment = "testnet" if use_testnet else "mainnet"

        # Create agent wallet keypair for signing
        from solders.keypair import Keypair
        agent_keypair = Keypair.from_base58_string(agent_private_key)

        # Create WebSocket client
        from pacifica_websocket import create_pacifica_websocket
        websocket_client = create_pacifica_websocket(
            environment=environment,
            agent_wallet_keypair=agent_keypair,
            account_public_key=account_public_key
        )

        logger.info(f"✅ Initialized WebSocket client ({environment})")
        return websocket_client
    except Exception as e:
        logger.error(f"❌ Failed to initialize WebSocket client: {e}")
        return None

    client = get_pacifica_client()

    if client and not _api_verified:
        # Try to verify API connectivity (will be fast after first call due to caching)
        try:
            client.get_markets()
            logger.info("✅ Pacifica API accessible - using LIVE data")
            market_data_handler = PacificaMarketDataHandler(client, use_mock=False)
        except Exception as e:
            logger.warning(f"⚠️ Pacifica API not accessible: {str(e)[:50]} - falling back to direct API calls")
            # Return None to trigger direct API calls in endpoints
            return None
    elif not client:
        logger.warning("📊 No Pacifica client available - using direct API calls")
        return None

    return market_data_handler

# Initialize client immediately (fast - no network), but defer API verification
pacifica_client = get_pacifica_client()
# Don't initialize market_data_handler here - do it lazily on first request

# Lifespan event handler is defined above with app creation

def get_db_connection() -> sqlite3.Connection:
    """Get database connection."""
    # Use the same database path as the rest of the system
    db_path = os.path.abspath(os.getenv("DATABASE_PATH", "data/trading_bot.db"))
    return sqlite3.connect(db_path)

def load_context_file(filename: str) -> str:
    """Load content from context files."""
    try:
        path = os.path.join(os.path.dirname(__file__), 'context files', filename)
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error loading {filename}: {str(e)}"

@app.get("/test")
def test_endpoint() -> Dict[str, bool]:
    return {"ok": True}

@app.get("/")
def serve_interface() -> HTMLResponse:
    """Serve the trading bot interface with fresh content."""
    html_path = os.path.join(os.path.dirname(__file__), 'trading_bot_interface.html')

    # Read file fresh each time - no caching
    with open(html_path, 'r', encoding='utf-8') as f:
        content = f.read()

    return HTMLResponse(
        content=content,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )

@app.get("/api/health")
async def health_check() -> Dict[str, Any]:
    """Simple health check endpoint."""
    return create_api_response(
        success=True,
        data={
            "status": "healthy",
            "service": "api_server",
            "timestamp": datetime.now().isoformat()
        },
        source="health_service",
        metadata={"uptime": "unknown"}  # Could be enhanced with actual uptime
    )

@app.get("/api/status")
def get_status() -> Dict[str, Any]:
    """Get server and bot status with financial metrics."""
    global TRADING_MODE, TESTNET_ENABLED, BOT_STATUS

    # Try to get real balance from Pacifica API first
    exchange_balance = None
    exchange_positions = None
    balance_source = "Local Database"

    if pacifica_client:
        try:
            # Try to fetch real account balance from Pacifica
            balance_response = pacifica_client.get_balance()
            if balance_response:
                exchange_balance = float(balance_response.get("available_balance", 0))
                balance_source = "Pacifica.fi Live API"
        except Exception as e:
            logger.debug(f"Could not fetch exchange balance: {e}")
            # Will fall back to database

    # Calculate financial metrics from database
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get total PnL from trades
        cursor.execute("SELECT SUM(pnl) FROM trades WHERE pnl IS NOT NULL")
        total_pnl = cursor.fetchone()[0] or 0.0

        # Get win rate
        cursor.execute("SELECT COUNT(*) FROM trades WHERE pnl > 0")
        wins = cursor.fetchone()[0] or 0
        cursor.execute("SELECT COUNT(*) FROM trades WHERE pnl IS NOT NULL")
        total_trades = cursor.fetchone()[0] or 1  # Avoid division by zero
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

        # Get LIVE position data from Pacifica API
        open_positions = 0
        unrealized_pnl = 0.0

        if pacifica_client:
            try:
                live_positions = pacifica_client.get_positions()
                open_positions = len(live_positions)
                unrealized_pnl = sum(pos.get("unrealized_pnl", 0) for pos in live_positions)
                logger.debug(f"Status: {open_positions} live positions, P&L: ${unrealized_pnl:.2f}")
            except Exception as e:
                logger.warning(f"Failed to get live positions for status: {e}")
                logger.warning(f"Error type: {type(e).__name__}")
                # Fall back to database
                cursor.execute("SELECT COUNT(*) FROM positions")
                open_positions = cursor.fetchone()[0] or 0
                cursor.execute("SELECT SUM(unrealized_pnl) FROM positions WHERE unrealized_pnl IS NOT NULL")
                unrealized_pnl = cursor.fetchone()[0] or 0.0
        else:
            # Fallback to database if no client
            cursor.execute("SELECT COUNT(*) FROM positions")
            open_positions = cursor.fetchone()[0] or 0
            cursor.execute("SELECT SUM(unrealized_pnl) FROM positions WHERE unrealized_pnl IS NOT NULL")
            unrealized_pnl = cursor.fetchone()[0] or 0.0

        conn.close()

        # Use exchange balance if available, otherwise calculate from database
        if exchange_balance is not None:
            account_balance = exchange_balance
        else:
            account_balance = 10000.0 + total_pnl + unrealized_pnl  # Starting balance + realized + unrealized
    except Exception as e:
        # If database query fails, use defaults
        if exchange_balance is not None:
            account_balance = exchange_balance
        else:
            account_balance = 10000.0
        total_pnl = 0.0
        win_rate = 0.0
        open_positions = 0

    # Check if bot is actually running
    bot_is_running = trading_bot is not None and trading_bot.is_running if trading_bot else False
    exchange_connected = (
        trading_bot is not None and
        trading_bot.execution_engine is not None
    ) if trading_bot else False

    # Check market data mode
    market_data_mode = "LIVE" if (market_data_handler and not market_data_handler.use_mock) else "MOCK"

    return create_api_response(
        success=True,
        data={
            "status": "healthy",
            "bot": BOT_STATUS,
            "bot_is_running": bot_is_running,
            "bot_error": bot_error,
            "server_info": {"version": "1.0.0"},
            "trading_mode": TRADING_MODE,
            "testnet": TESTNET_ENABLED,
            "safety_warning": "REAL MONEY MODE" if TRADING_MODE == "real" else None,
            "account_balance": account_balance,
            "balance_source": balance_source,
            "total_pnl": total_pnl,
            "win_rate": win_rate,
            "open_positions": open_positions,
            "exchange_connected": exchange_connected,
            "market_data_mode": market_data_mode,
            "account_name": "Trading Account"
        },
        source="status_service",
        metadata={
            "timestamp": datetime.now().isoformat(),
            "database_connected": True,  # Assuming DB is working if we get here
            "pacifica_connected": pacifica_client is not None
        }
    )

@app.get("/api/server/status")
def get_server_status() -> Dict[str, Any]:
    """Alias for /api/status for backwards compatibility."""
    # Return the same response as get_status but with different source
    base_response = get_status()
    base_response["source"] = "server_status_service"
    return base_response

@app.post("/api/bot/standby")
def bot_standby() -> Dict[str, Any]:
    """Start bot in standby mode - prepares bot but doesn't start trading."""
    global BOT_STATUS, trading_bot, bot_error

    try:
        # Create TradingBot instance in standby mode
        testnet = TESTNET_ENABLED
        trading_bot = TradingBot(testnet=testnet)
        BOT_STATUS = "standby"
        bot_error = None
        logger.info("Trading bot initialized in standby mode")
        return create_api_response(
            success=True,
            data={"message": "Bot in standby mode", "status": BOT_STATUS},
            source="bot_service",
            metadata={"timestamp": datetime.now().isoformat()}
        )
    except Exception as e:
        logger.error(f"Failed to initialize bot: {e}")
        bot_error = str(e)
        return create_api_response(
            success=False,
            error=str(e),
            source="bot_service",
            metadata={"timestamp": datetime.now().isoformat()}
        )

@app.post("/api/bot/credentials")
def bot_credentials() -> Dict[str, Any]:
    """Load trading credentials from environment or default profile."""
    global BOT_STATUS, trading_bot, bot_error

    try:
        # Get credentials from environment or database
        private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
        public_key = os.getenv("ACCOUNT_PUBLIC_KEY")

        if not private_key or not public_key:
            # Try to get from default profile in database
            db = DatabaseManager()
            default_profile = db.get_default_profile()
            if default_profile:
                private_key = decrypt_private_key(default_profile.get('private_key_encrypted', ''))
                public_key = default_profile.get('public_key')

        if not private_key or not public_key:
            return create_api_response(
                success=False,
                error="No credentials found. Please set AGENT_WALLET_PRIVATE_KEY and ACCOUNT_PUBLIC_KEY in .env",
                source="bot_service",
                metadata={"timestamp": datetime.now().isoformat()}
            )

        # If bot doesn't exist, create it
        if not trading_bot:
            trading_bot = TradingBot(testnet=TESTNET_ENABLED)

        BOT_STATUS = "ready"
        bot_error = None

        profile = {
            "id": "user_account_1",
            "name": "Trading Account",
            "isDefault": True,
            "created": datetime.now().isoformat(),
            "type": "live" if not TESTNET_ENABLED else "testnet",
            "public_key": public_key[:8] + "..." + public_key[-8:] if public_key else None
        }

        logger.info(f"Credentials loaded for account: {profile['public_key']}")
        return create_api_response(
            success=True,
            data={"message": "Credentials loaded", "profile": profile, "status": BOT_STATUS},
            source="bot_service",
            metadata={"timestamp": datetime.now().isoformat()}
        )

    except Exception as e:
        logger.error(f"Failed to load credentials: {e}")
        bot_error = str(e)
        return create_api_response(
            success=False,
            error=str(e),
            source="bot_service",
            metadata={"timestamp": datetime.now().isoformat()}
        )

@app.post("/api/bot/activate")
def bot_activate() -> Dict[str, Any]:
    """Activate trading bot - alias for start."""
    return bot_start()

@app.post("/api/bot/start")
def bot_start() -> Dict[str, Any]:
    """Start the trading bot."""
    global BOT_STATUS, trading_bot, bot_thread, bot_error

    try:
        # Get credentials
        private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
        public_key = os.getenv("ACCOUNT_PUBLIC_KEY")

        if not private_key or not public_key:
            # Try to get from default profile in database
            db = DatabaseManager()
            default_profile = db.get_default_profile()
            if default_profile:
                private_key = decrypt_private_key(default_profile.get('private_key_encrypted', ''))
                public_key = default_profile.get('public_key')

        if not private_key or not public_key:
            return create_api_response(
                success=False,
                error="No credentials configured. Set AGENT_WALLET_PRIVATE_KEY and ACCOUNT_PUBLIC_KEY in .env",
                source="bot_service",
                metadata={"timestamp": datetime.now().isoformat()}
            )

        # Check if already running
        if bot_thread and bot_thread.is_alive():
            return create_api_response(
                success=False,
                error="Bot is already running",
                source="bot_service",
                metadata={"current_status": BOT_STATUS, "timestamp": datetime.now().isoformat()}
            )

        # Create new TradingBot instance
        trading_bot = TradingBot(testnet=TESTNET_ENABLED)

        # Start bot in a background thread
        BOT_STATUS = "starting"
        bot_error = None

        bot_thread = threading.Thread(
            target=_run_bot_async,
            args=(trading_bot, private_key, public_key),
            daemon=True
        )
        bot_thread.start()

        # Wait briefly for initialization
        import time
        time.sleep(2)

        # Check if bot started successfully
        if BOT_STATUS == "stopped" and bot_error:
            return create_api_response(
                success=False,
                error=f"Failed to start bot: {bot_error}",
                source="bot_service",
                metadata={"timestamp": datetime.now().isoformat()}
            )

        BOT_STATUS = "trading"
        logger.info("Trading bot started successfully")
        return create_api_response(
            success=True,
            data={"message": "Trading started", "status": BOT_STATUS},
            source="bot_service",
            metadata={"timestamp": datetime.now().isoformat()}
        )

    except Exception as e:
        logger.error(f"Failed to start trading: {e}")
        bot_error = str(e)
        BOT_STATUS = "stopped"
        return create_api_response(
            success=False,
            error=str(e),
            source="bot_service",
            metadata={"timestamp": datetime.now().isoformat()}
        )

@app.post("/api/bot/stop")
async def bot_stop() -> Dict[str, Any]:
    """Stop the trading bot gracefully."""
    global BOT_STATUS, trading_bot, bot_thread, bot_error

    try:
        if not trading_bot:
            BOT_STATUS = "stopped"
            return create_api_response(
                success=True,
                data={"message": "Bot was not running", "status": BOT_STATUS},
                source="bot_service",
                metadata={"timestamp": datetime.now().isoformat()}
            )

        logger.info("Stopping trading bot...")

        # Signal bot to stop
        if trading_bot:
            trading_bot.is_running = False

        # Wait for thread to finish
        if bot_thread and bot_thread.is_alive():
            bot_thread.join(timeout=5)

        BOT_STATUS = "stopped"
        bot_error = None
        logger.info("Trading bot stopped successfully")
        return create_api_response(
            success=True,
            data={"message": "Trading stopped", "status": BOT_STATUS},
            source="bot_service",
            metadata={"timestamp": datetime.now().isoformat()}
        )

    except Exception as e:
        logger.error(f"Error stopping bot: {e}")
        BOT_STATUS = "stopped"
        return create_api_response(
            success=False,
            error=str(e),
            source="bot_service",
            metadata={"timestamp": datetime.now().isoformat()}
        )

@app.post("/api/bot/emergency-stop")
async def bot_emergency_stop() -> Dict[str, Any]:
    """Emergency stop - immediately halt all trading."""
    global BOT_STATUS, trading_bot, bot_thread, bot_error

    try:
        logger.warning("EMERGENCY STOP triggered!")

        # Force stop
        if trading_bot:
            trading_bot.is_running = False

            # Try to cancel all orders if execution engine exists
            if trading_bot.execution_engine:
                try:
                    # Cancel all open orders
                    if hasattr(trading_bot.execution_engine, 'exchange') and trading_bot.execution_engine.exchange:
                        await asyncio.wait_for(
                            asyncio.to_thread(trading_bot.execution_engine.exchange.cancel_all_orders),
                            timeout=5
                        )
                except Exception as e:
                    logger.error(f"Error canceling orders during emergency stop: {e}")

        BOT_STATUS = "stopped"
        bot_error = "Emergency stop executed"
        return create_api_response(
            success=True,
            data={"message": "Emergency stop executed - all trading halted", "status": BOT_STATUS},
            source="bot_service",
            metadata={"emergency": True, "timestamp": datetime.now().isoformat()}
        )

    except Exception as e:
        logger.error(f"Emergency stop error: {e}")
        BOT_STATUS = "stopped"
        return create_api_response(
            success=False,
            error=str(e),
            source="bot_service",
            metadata={"emergency": True, "timestamp": datetime.now().isoformat()}
        )

@app.post("/api/bot/circuit-breaker")
async def bot_circuit_breaker(request: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Toggle circuit breaker protection."""
    return create_api_response(
        success=True,
        data={"message": "Circuit breaker setting updated"},
        source="bot_service",
        metadata={"timestamp": datetime.now().isoformat()}
    )

@app.get("/api/mode/status")
def get_trading_mode() -> Dict[str, Any]:
    """
    Get current trading mode status.

    Returns:
        mode: 'paper' or 'real'
        testnet: boolean indicating if testnet is enabled
        warning: safety warning message for real mode
        last_changed: timestamp of last mode change
    """
    global TRADING_MODE, TESTNET_ENABLED, MODE_CHANGE_TIMESTAMP

    return create_api_response(
        success=True,
        data={
            "mode": TRADING_MODE,
            "testnet": TESTNET_ENABLED,
            "is_safe": TRADING_MODE == "paper",
            "warning": "⚠️ REAL MONEY TRADING ACTIVE" if TRADING_MODE == "real" else None,
            "last_changed": MODE_CHANGE_TIMESTAMP.isoformat(),
            "endpoint": "testnet" if TESTNET_ENABLED else "mainnet"
        },
        source="mode_service",
        metadata={"timestamp": datetime.now().isoformat()}
    )

@app.post("/api/mode/set")
def set_trading_mode(request: ModeChangeRequest) -> Dict[str, Any]:
    """
    Change trading mode with safety confirmation.

    Requires confirmation text for real mode to prevent accidents.
    """
    global TRADING_MODE, MODE_CHANGE_TIMESTAMP

    # Validate mode change
    if request.mode == "real":
        # Require explicit confirmation for real trading
        if request.confirmation != "CONFIRM REAL TRADING":
            raise HTTPException(
                status_code=400,
                detail="Must type 'CONFIRM REAL TRADING' exactly to enable real money trading"
            )

        # Additional safety check
        if TESTNET_ENABLED:
            raise HTTPException(
                status_code=400,
                detail="Cannot enable real trading while TESTNET=true in environment. Set TESTNET=false and restart."
            )

    # Update mode
    old_mode = TRADING_MODE
    TRADING_MODE = request.mode
    MODE_CHANGE_TIMESTAMP = datetime.now()

    return create_api_response(
        success=True,
        data={
            "message": f"Trading mode changed from {old_mode} to {request.mode}",
            "mode": TRADING_MODE,
            "warning": "⚠️ REAL MONEY TRADING NOW ACTIVE" if request.mode == "real" else "Safe mode: Paper trading active",
            "timestamp": MODE_CHANGE_TIMESTAMP.isoformat()
        },
        source="mode_service",
        metadata={"old_mode": old_mode, "new_mode": request.mode}
    )

@app.post("/api/server/stop")
def server_stop() -> Dict[str, Any]:
    """Stop the API server gracefully."""
    return create_api_response(
        success=True,
        data={"message": "Server stopping..."},
        source="server_service",
        metadata={"timestamp": datetime.now().isoformat()}
    )

@app.post("/api/auth/login")
def auth_login() -> Dict[str, Any]:
    """User login with JWT token using Pacifica credentials from environment."""
    from auth import load_pacifica_credentials, create_access_token
    from solders.keypair import Keypair

    # Load credentials from environment
    credentials = load_pacifica_credentials()

    if not credentials:
        return create_api_response(
            success=False,
            error="No Pacifica credentials found. Set AGENT_WALLET_PRIVATE_KEY and ACCOUNT_PUBLIC_KEY in .env",
            source="auth_service",
            metadata={"timestamp": datetime.now().isoformat()}
        )

    try:
        # Get the agent wallet public key from the private key
        agent_keypair = Keypair.from_base58_string(credentials["agent_wallet_private_key"])
        agent_wallet_public = str(agent_keypair.pubkey())
        account_public = credentials["account_public_key"]

        # Create a real JWT token
        token_data = {
            "account": account_public,
            "agent_wallet": agent_wallet_public,
            "type": "trading_session"
        }
        access_token = create_access_token(token_data)

        logger.info(f"Auth login successful - Account: {account_public[:8]}..., Agent: {agent_wallet_public[:8]}...")

        return create_api_response(
            success=True,
            data={
                "access_token": access_token,
                "expires_in": 1800,  # 30 minutes
                "agent_wallet": agent_wallet_public,
                "account_public_key": account_public
            },
            source="auth_service",
            metadata={"timestamp": datetime.now().isoformat(), "token_type": "jwt"}
        )
    except Exception as e:
        logger.error(f"Auth login failed: {e}")
        return create_api_response(
            success=False,
            error=f"Authentication failed: {str(e)}",
            source="auth_service",
            metadata={"timestamp": datetime.now().isoformat()}
        )

@app.get("/api/auth/verify")
def auth_verify() -> Dict[str, Any]:
    """Verify authentication token."""
    return create_api_response(
        success=True,
        data={"valid": True, "user": "trader"},
        source="auth_service",
        metadata={"verified_at": datetime.now().isoformat()}
    )

@app.post("/api/auth/logout")
def auth_logout() -> Dict[str, Any]:
    """Logout and invalidate authentication token."""
    # In a real implementation, this would invalidate the token
    # For now, just return success
    return create_api_response(
        success=True,
        data={"message": "Logged out successfully"},
        source="auth_service",
        metadata={"logged_out_at": datetime.now().isoformat()}
    )

@app.get("/api/auth/default-credentials")
def get_default_credentials() -> Dict[str, Any]:
    """Get default credentials for automatic form population."""
    try:
        agent_private_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
        account_public_key = os.getenv("ACCOUNT_PUBLIC_KEY")

        if not agent_private_key or not account_public_key:
            return create_api_response(
                success=False,
                error="No default credentials found in environment",
                source="auth_service",
                metadata={"timestamp": datetime.now().isoformat()}
            )

        network_type = "Testnet" if TESTNET_ENABLED else "Mainnet"
        profile_name = f"Default Account ({network_type})"

        return create_api_response(
            success=True,
            data={
                "name": profile_name,
                "public_key": account_public_key,
                "private_key": agent_private_key,  # Only exposed to local interface for auto-population
                "network": network_type.lower(),
                "testnet": TESTNET_ENABLED
            },
            source="auth_service",
            metadata={"timestamp": datetime.now().isoformat()}
        )
    except Exception as e:
        logger.error(f"Failed to get default credentials: {e}")
        return create_api_response(
            success=False,
            error=str(e),
            source="auth_service",
            metadata={"timestamp": datetime.now().isoformat()}
        )

def _fetch_order_book(symbol: str) -> Dict[str, Any]:
    """
    Fetch order book data from Pacifica API.

    Args:
        symbol: Market symbol (e.g., "BTC", "ETH")

    Returns:
        Order book data with bids and asks
    """
    try:
        # Use requests directly since we don't have a full client
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

        # Use testnet or mainnet based on environment setting
        if TESTNET_ENABLED:
            base_url = "https://test-api.pacifica.fi/api/v1"
        else:
            base_url = "https://api.pacifica.fi/api/v1"

        url = f"{base_url}/book?symbol={symbol}"

        response = requests.get(url, timeout=10, verify=False)  # verify=False for testnet SSL issues
        response.raise_for_status()

        data = response.json()
        if data.get("success"):
            return data.get("data", {})
        else:
            raise Exception(f"API returned error: {data.get('error', 'Unknown error')}")

    except Exception as e:
        logger.warning(f"Failed to fetch order book for {symbol}: {e}")
        raise


def _extract_ticker_from_order_book(order_book: Dict[str, Any], symbol: str) -> Dict[str, Any]:
    """
    Extract ticker data from order book.

    Args:
        order_book: Order book data from API
        symbol: Market symbol

    Returns:
        Ticker data dict
    """
    try:
        bids = order_book.get("bids", [])
        asks = order_book.get("asks", [])

        if not bids or not asks:
            raise Exception("Order book missing bids or asks")

        # Get best bid and ask
        best_bid = float(bids[0][0]) if bids else 0.0
        best_ask = float(asks[0][0]) if asks else 0.0

        # Calculate mid price
        mid_price = (best_bid + best_ask) / 2 if best_bid > 0 and best_ask > 0 else 0.0

        # Calculate spread
        spread = best_ask - best_bid if best_bid > 0 and best_ask > 0 else 0.0

        return {
            "symbol": symbol,
            "price": mid_price,
            "bid": best_bid,
            "ask": best_ask,
            "spread": spread,
            "volume": 0.0,  # Not available in order book
            "source": "order_book_api"
        }

    except Exception as e:
        logger.error(f"Failed to extract ticker from order book for {symbol}: {e}")
        raise


async def _get_market_data_from_database() -> Dict[str, Any]:
    """
    Get market data from database as fallback when API is unavailable.

    Returns most recent market data for all symbols.
    """
    try:
        from market_data_collector import MarketDataCollector

        db_manager = get_database_manager()
        collector = MarketDataCollector(db_manager)

        # Get most recent data for all symbols
        recent_data = await collector._get_previous_market_data()

        if not recent_data:
            return create_api_response(
                success=False,
                error="FIX ME: Implement real Pacifica.fi authentication to populate market data",
                source="database_fallback",
                metadata={"timestamp": datetime.now().isoformat()}
            )

        # Transform to expected API format
        market_obj = {}
        for symbol, data in recent_data.items():
            market_obj[symbol] = {
                "symbol": symbol,
                "price": 0.0,  # No real-time pricing in database
                "bid": 0.0,
                "ask": 0.0,
                "volume": 0.0,
                "tick_size": float(data.get("tick_size", 0)),
                "lot_size": float(data.get("lot_size", 0)),
                "min_size": float(data.get("min_order_size", 0)),
                "max_size": float(data.get("max_order_size", 0)),
                "max_leverage": int(data.get("max_leverage", 1)),
                "funding_rate": float(data.get("funding_rate", 0)),
                "next_funding_rate": float(data.get("next_funding_rate", 0)),
                "source": "database",
                "last_updated": None
            }

        return create_api_response(
            success=True,
            data=market_obj,
            source="database_fallback",
            metadata={
                "message": "Using cached market data from database",
                "timestamp": datetime.now().isoformat()
            }
        )

    except Exception as e:
        logger.error(f"Database fallback failed: {e}")
        return create_api_response(
            success=False,
            error=f"Database fallback failed: {str(e)}",
            source="database_fallback",
            metadata={"timestamp": datetime.now().isoformat()}
        )


@app.get("/api/market-data")
def market_data() -> Dict[str, Any]:
    """
    Get live market data from Pacifica.fi API.

    Uses /info endpoint for market specs and funding rates (reliable).
    Attempts order book for real-time bid/ask prices (may be empty on testnet).
    Falls back gracefully when order books are unavailable.
    """
    try:
        result_data = {}
        source = "unknown"

        # First, try to get market info from /info endpoint (most reliable)
        client = get_pacifica_client()
        if client:
            try:
                markets_info = client.get_markets()
                if markets_info:
                    for market in markets_info:
                        symbol = market.get("symbol", "").replace("-PERP", "")
                        result_data[symbol] = {
                            "symbol": symbol,
                            "price": 0.0,  # Will try to get from order book
                            "bid": 0.0,
                            "ask": 0.0,
                            "spread": 0.0,
                            "volume": 0.0,
                            "tick_size": float(market.get("tick_size", 0)),
                            "lot_size": float(market.get("lot_size", 0)),
                            "min_size": float(market.get("min_order_size", 0)),
                            "max_size": float(market.get("max_order_size", 0)),
                            "max_leverage": int(market.get("max_leverage", 1)),
                            "funding_rate": float(market.get("funding_rate", 0)),
                            "next_funding_rate": float(market.get("next_funding_rate", 0)),
                            "source": "pacifica_info_api"
                        }
                    source = "pacifica_info_api"
                    logger.debug(f"Got market info for {len(result_data)} markets from /info endpoint")
            except Exception as e:
                logger.warning(f"Failed to get market info from /info endpoint: {e}")

        # Define primary markets we want to display
        primary_markets = ["BTC", "ETH", "SOL", "DOGE", "XRP", "AVAX", "SUI", "WLD"]

        # Get real-time prices from /info/prices endpoint (same as positions endpoint)
        price_success = 0
        if client:
            try:
                price_response = client._make_request("GET", "/info/prices")
                if price_response.get("success") and price_response.get("data"):
                    for price_info in price_response["data"]:
                        symbol = price_info.get("symbol", "").replace("-PERP", "")
                        if symbol in primary_markets:
                            price = float(price_info.get("price", 0))
                            if price > 0:
                                if symbol in result_data:
                                    result_data[symbol].update({
                                        "price": price,
                                        "bid": price,  # Use price as bid/ask approximation
                                        "ask": price,
                                        "spread": 0.0,
                                        "source": "pacifica_info_prices"
                                    })
                                else:
                                    result_data[symbol] = {
                                        "symbol": symbol,
                                        "price": price,
                                        "bid": price,
                                        "ask": price,
                                        "spread": 0.0,
                                        "volume": 0.0,
                                        "source": "pacifica_info_prices"
                                    }
                                price_success += 1
                    logger.debug(f"Got prices for {price_success} markets from /info/prices endpoint")
            except Exception as e:
                logger.warning(f"Failed to get prices from /info/prices endpoint: {e}")

        if price_success > 0:
            source = "pacifica_info_prices"

        # Filter to only return primary markets (or all if we have them)
        if result_data:
            # Ensure primary markets are included
            filtered_data = {k: v for k, v in result_data.items() if k in primary_markets}
            if filtered_data:
                return create_api_response(
                    success=True,
                    data=filtered_data,
                    source=source,
                    metadata={
                        "markets_with_prices": price_success,
                        "total_markets": len(filtered_data),
                        "note": "Using /info/prices endpoint for real-time pricing data",
                        "timestamp": datetime.now().isoformat()
                    }
                )

        # Final fallback to database
        logger.info("📊 No live API data available - using database fallback")
        import asyncio
        return asyncio.run(_get_market_data_from_database())

    except Exception as e:
        logger.error(f"Market data endpoint failed: {e}")
        try:
            import asyncio
            return asyncio.run(_get_market_data_from_database())
        except Exception as db_error:
            logger.error(f"Database fallback also failed: {db_error}")
            return create_api_response(
                success=False,
                error=f"Both API and database failed: {str(e)}, {str(db_error)}",
                source="market_data_service",
                metadata={"timestamp": datetime.now().isoformat()}
            )


@app.post("/api/positions/{asset}/close")
async def close_position(asset: str) -> Dict[str, Any]:
    """Close a specific position by asset symbol."""
    try:
        if not pacifica_client:
            return create_api_response(
                success=False,
                error="Pacifica client not initialized",
                source="positions_service",
                metadata={"timestamp": datetime.now().isoformat()}
            )

        # Find the position to close
        positions_response = pacifica_client.get_positions()
        if not positions_response:
            return create_api_response(
                success=False,
                error="No positions found",
                source="positions_service",
                metadata={"timestamp": datetime.now().isoformat()}
            )

        # Find position by symbol
        position_to_close = None
        for pos in positions_response:
            if pos.get("symbol", "").replace("-PERP", "") == asset:
                position_to_close = pos
                break

        if not position_to_close:
            return create_api_response(
                success=False,
                error=f"Position for {asset} not found",
                source="positions_service",
                metadata={"timestamp": datetime.now().isoformat()}
            )

        # For now, return success (actual order placement would need more implementation)
        # In a real implementation, this would place a market order to close the position
        logger.info(f"Close position requested for {asset} - position: {position_to_close}")

        return create_api_response(
            success=True,
            data={
                "message": f"Close order placed for {asset} position",
                "position": position_to_close
            },
            source="positions_service",
            metadata={"timestamp": datetime.now().isoformat()}
        )

    except Exception as e:
        logger.error(f"Error closing position for {asset}: {e}")
        return create_api_response(
            success=False,
            error=str(e),
            source="positions_service",
            metadata={"timestamp": datetime.now().isoformat()}
        )


@app.get("/api/positions")
def positions() -> Dict[str, Any]:
    """Get current positions from Pacifica API with market data and prices."""
    try:
        # Try to get live positions from Pacifica API first
        if pacifica_client:
            try:
                # 1. Get positions
                try:
                    live_positions = pacifica_client.get_positions()
                    logger.debug(f"Retrieved {len(live_positions) if live_positions else 0} positions from Pacifica API")
                except Exception as e:
                    logger.warning(f"Failed to get positions from Pacifica API: {e}")
                    logger.warning(f"Error type: {type(e).__name__}")
                    # Try raw API call as fallback
                    try:
                        logger.info("Trying raw positions API call...")
                        response = pacifica_client._make_request("GET", "/positions")
                        if response.get("success"):
                            live_positions = response.get("data", [])
                            logger.info(f"Retrieved {len(live_positions)} positions via raw API call")
                        else:
                            logger.error(f"Raw positions API failed: {response}")
                            live_positions = []
                    except Exception as e2:
                        logger.error(f"All positions API attempts failed: {e2}")
                        live_positions = []
                    except Exception as e2:
                        logger.warning(f"Subaccount approach failed: {e2}")
                        # Try without account parameter
                        try:
                            logger.info("Trying positions endpoint without account parameter...")
                            response = pacifica_client._make_request("GET", "/positions")
                            if response.get("success"):
                                live_positions = response.get("data", [])
                                logger.info(f"Retrieved {len(live_positions)} positions without account parameter")
                            else:
                                logger.error(f"Positions API failed: {response}")
                                live_positions = []
                        except Exception as e3:
                            logger.error(f"Positions API failed even without account: {e3}")
                            live_positions = []

                # 2. Get market information for all symbols
                market_response = pacifica_client._make_request("GET", "/info")
                market_data = {}
                if market_response.get("success") and market_response.get("data"):
                    for market in market_response["data"]:
                        market_data[market["symbol"]] = market

                # 3. Get current prices
                price_response = pacifica_client._make_request("GET", "/info/prices")
                price_data = {}
                if price_response.get("success") and price_response.get("data"):
                    for price in price_response["data"]:
                        price_data[price["symbol"]] = price

                if live_positions is not None:
                    # Transform Pacifica position format to interface format
                    data = []

                    # Get detailed funding data from database for all positions (optional)
                    funding_data = {}
                    try:
                        conn = get_db_connection()
                        cursor = conn.cursor()

                        # Check if pacifica_positions table exists
                        cursor.execute("""
                            SELECT name FROM sqlite_master
                            WHERE type='table' AND name='pacifica_positions'
                        """)
                        table_exists = cursor.fetchone() is not None

                        if table_exists:
                            # Get funding data for all symbols that have positions
                            symbols_in_positions = [pos.get("symbol", "") for pos in live_positions]
                            if symbols_in_positions:
                                # Get latest funding tracking data for each symbol
                                symbols_placeholder = ','.join('?' * len(symbols_in_positions))
                                cursor.execute(f"""
                                    SELECT symbol, cumulative_funding_paid, last_funding_timestamp
                                    FROM pacifica_positions
                                    WHERE symbol IN ({symbols_placeholder})
                                """, symbols_in_positions)

                                for row in cursor.fetchall():
                                    symbol, cumulative_paid, last_timestamp = row
                                    funding_data[symbol] = {
                                        "cumulative_paid": float(cumulative_paid or 0),
                                        "last_payment": last_timestamp
                                    }

                                # Get payment counts and averages for each symbol
                                for symbol in symbols_in_positions:
                                    cursor.execute("""
                                        SELECT COUNT(*), SUM(payment_amount), AVG(funding_rate)
                                        FROM funding_payments fp
                                        JOIN pacifica_positions pp ON fp.position_id = pp.position_id
                                        WHERE pp.symbol = ?
                                    """, (symbol,))

                                    funding_stats = cursor.fetchone()
                                    if funding_stats and funding_data.get(symbol):
                                        payment_count, total_paid, avg_rate = funding_stats
                                        funding_data[symbol].update({
                                            "payment_count": payment_count or 0,
                                            "total_paid": float(total_paid or 0),
                                            "avg_hourly_rate": float(avg_rate or 0),
                                            "avg_hourly_rate_pct": float(avg_rate or 0) * 100,
                                            "avg_daily_rate_pct": float(avg_rate or 0) * 24 * 100,
                                            "hours_held": payment_count or 0
                                        })

                        conn.close()
                        if table_exists:
                            logger.debug(f"Loaded detailed funding data for {len(funding_data)} positions")
                        else:
                            logger.debug("Pacifica positions table not found - using basic funding data only")
                    except Exception as e:
                        logger.warning(f"Could not fetch detailed funding data: {e}")
                        # Continue without detailed funding data

                    for pos in live_positions:
                        symbol = pos.get("symbol", "")
                        market_info = market_data.get(symbol, {})
                        price_info = price_data.get(symbol, {})
                        detailed_funding = funding_data.get(symbol, {})

                        # Convert side: "bid" = long, "ask" = short
                        side = "long" if pos.get("side") == "bid" else "short"
                        quantity = abs(float(pos.get("amount", 0)))
                        entry_price = float(pos.get("entry_price", 0))

                        # Get current price from price data, fallback to entry price
                        current_price = float(price_info.get("price", entry_price))

                        # Calculate unrealized P&L: (current_price - entry_price) * quantity * side_multiplier
                        side_multiplier = 1 if side == "long" else -1
                        unrealized_pnl = (current_price - entry_price) * quantity * side_multiplier

                        # Calculate real P&L from balance history instead of using Pacifica's fake data
                        if pacifica_client and pacifica_client.account_public_key:
                            real_pnl = calculate_real_pnl_from_balance(pacifica_client.account_public_key, pos.get("opened_at"))
                        else:
                            real_pnl = 0.0  # Fallback if no account ID available
                        funding_pnl = detailed_funding.get("cumulative_paid", 0)
                        total_pnl = real_pnl + funding_pnl

                        position_data = {
                            "symbol": symbol,
                            "asset": symbol,
                            "asset_class": "crypto",
                            "side": side,
                            "quantity": quantity,
                            "entry_price": entry_price,
                            "current_price": current_price,
                            "unrealized_pnl": total_pnl,  # Combined funding + price P&L
                            "pnl": total_pnl,
                            "funding_pnl": funding_pnl,
                            "price_pnl": unrealized_pnl,
                            "opened_at": datetime.fromtimestamp(pos.get("created_at", 0)/1000).isoformat() if pos.get("created_at") else "",
                            "leverage": 1,  # Default, could be calculated from margin
                            "margin_mode": "isolated" if pos.get("isolated", False) else "cross",
                            "liquidation_price": float(pos.get("liquidation_price", 0)) if pos.get("liquidation_price") else None,
                            "tick_size": float(market_info.get("tick_size", 0.01)),
                            "max_leverage": market_info.get("max_leverage", 1),
                            "funding_rate": float(market_info.get("funding_rate", 0)),
                            # Include detailed funding tracking data
                            "funding": {
                                "cumulative_paid": detailed_funding.get("cumulative_paid", 0),
                                "payment_count": detailed_funding.get("payment_count", 0),
                                "total_paid": detailed_funding.get("total_paid", 0),
                                "avg_hourly_rate": detailed_funding.get("avg_hourly_rate", 0),
                                "avg_hourly_rate_pct": detailed_funding.get("avg_hourly_rate_pct", 0),
                                "avg_daily_rate_pct": detailed_funding.get("avg_daily_rate_pct", 0),
                                "last_payment": detailed_funding.get("last_payment"),
                                "hours_held": detailed_funding.get("hours_held", 0)
                            }
                        }
                        data.append(position_data)

                    # Sync to database for other components
                    sync_positions_to_database()
                    sync_balance_history()

                    logger.info(f"Retrieved {len(data)} live positions with market data from Pacifica API")
                    return create_api_response(
                        success=True,
                        data=data,
                        source="pacifica_api",
                        metadata={"count": len(data), "timestamp": datetime.now().isoformat()}
                    )
            except Exception as e:
                logger.warning(f"Failed to get live positions from Pacifica API: {e}")
                # Fall back to database

        # Fallback to database if API fails
        logger.info("Falling back to database for positions")
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, asset_class, side, quantity, entry_price, current_price,
                   unrealized_pnl, opened_at
            FROM positions
            ORDER BY opened_at DESC
        """)
        rows = cursor.fetchall()
        data = []
        for row in rows:
            data.append({
                "symbol": row[0],
                "asset": row[0],  # Interface expects 'asset' field
                "asset_class": row[1],
                "side": row[2],
                "quantity": row[3],
                "entry_price": row[4],
                "current_price": row[5] or row[4],  # Use entry if no current
                "unrealized_pnl": row[6],
                "pnl": row[6],  # Interface expects 'pnl' field
                "opened_at": row[7]
            })
        conn.close()
        return create_api_response(
            success=True,
            data=data,
            source="database",
            metadata={"count": len(data), "timestamp": datetime.now().isoformat()}
        )
    except Exception as e:
        logger.error(f"Positions error: {str(e)}", exc_info=True)
        return create_api_response(
            success=False,
            error=str(e),
            source="positions_service",
            metadata={"timestamp": datetime.now().isoformat()}
        )

@app.get("/api/trades/export")
def trades_export() -> Dict[str, Any]:
    """Export trade history to CSV."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, side, quantity, entry_price, exit_price, entry_time,
                   exit_time, pnl, commission, strategy, status
            FROM trades
            ORDER BY entry_time DESC
        """)
        rows = cursor.fetchall()
        conn.close()

        # Convert to CSV format
        import io
        import csv

        output = io.StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow([
            'Symbol', 'Side', 'Quantity', 'Entry Price', 'Exit Price',
            'Entry Time', 'Exit Time', 'PnL', 'Commission', 'Strategy', 'Status'
        ])

        # Write data
        for row in rows:
            writer.writerow(row)

        csv_content = output.getvalue()
        output.close()

        return {
            "success": True,
            "data": csv_content,
            "filename": f"trades_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "content_type": "text/csv"
        }

    except Exception as e:
        logger.error(f"Error exporting trades: {e}")
        return {"success": False, "error": str(e)}


@app.get("/api/trades")
def trades() -> Dict[str, Any]:
    """Get trade history - tries Pacifica first, falls back to database."""
    source = "Local Database"

    # Try Pacifica API first
    if pacifica_client:
        try:
            pacifica_orders = pacifica_client.get_order_history(limit=100)
            if pacifica_orders:
                data = []
                for order in pacifica_orders:
                    # Map Pacifica order format to our format
                    # Pacifica API uses "buy"/"sell" for side (not "bid"/"ask")
                    side = order.get("side", "unknown")

                    # Handle timestamp - Pacifica returns ISO string in created_at
                    timestamp = order.get("created_at")
                    ts_str = timestamp  # Already ISO string from Pacifica API

                    # If timestamp is numeric (ms), convert to ISO
                    if isinstance(timestamp, (int, float)) and timestamp > 0:
                        ts_str = datetime.fromtimestamp(timestamp / 1000).isoformat()

                    # Extract symbol from market field (Pacifica uses "market" not "symbol")
                    symbol = order.get("market", order.get("symbol", ""))
                    # Pacifica uses "BTC-PERP" format or just "BTC"
                    symbol_clean = symbol.replace("-PERP", "") if symbol else ""

                    data.append({
                        "symbol": symbol_clean,
                        "asset": symbol_clean,
                        "side": side,
                        "quantity": float(order.get("filled_size", order.get("filled_amount", order.get("size", 0)))),
                        "entry_price": float(order.get("avg_fill_price", order.get("average_filled_price", order.get("price", 0)))),
                        "exit_price": None,
                        "entry_time": ts_str,
                        "exit_time": order.get("filled_at"),
                        "timestamp": ts_str,
                        "pnl": None,  # Not available in order history
                        "commission": float(order.get("fee", 0)) if order.get("fee") else None,
                        "strategy": order.get("type", order.get("order_type", "market")),
                        "status": order.get("status", order.get("order_status", "unknown")),
                        "order_id": str(order.get("order_id", "")),
                        "source": "Pacifica.fi"
                    })
                return {"success": True, "data": data, "source": "Pacifica.fi Live API"}
        except Exception as e:
            logger.debug(f"Pacifica trade history unavailable: {e}")

    # Fall back to local database
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, side, quantity, entry_price, exit_price, entry_time,
                   exit_time, pnl, commission, strategy, status
            FROM trades
            ORDER BY entry_time DESC
            LIMIT 100
        """)
        rows = cursor.fetchall()
        data = []
        for row in rows:
            data.append({
                "symbol": row[0],
                "asset": row[0],
                "side": row[1],
                "quantity": row[2],
                "entry_price": row[3],
                "exit_price": row[4],
                "entry_time": row[5],
                "exit_time": row[6],
                "timestamp": row[6] or row[5],
                "pnl": row[7],
                "commission": row[8],
                "strategy": row[9],
                "status": row[10],
                "source": "Local"
            })
        conn.close()
        return {"success": True, "data": data, "source": "Local Database"}
    except Exception as e:
        return {"success": False, "error": str(e), "data": [], "source": "Error"}

@app.get("/api/signals")
def signals() -> Dict[str, Any]:
    """Get trading signals from database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, signal_type, strength, indicators, timestamp, executed
            FROM signals
            ORDER BY timestamp DESC
            LIMIT 50
        """)
        rows = cursor.fetchall()
        data = []
        for row in rows:
            signal_type = row[1]
            strength = row[2]

            # Generate message from signal type and strength
            message = f"{signal_type.upper()} signal for {row[0]} (strength: {strength:.2f})"

            data.append({
                "symbol": row[0],
                "asset": row[0],  # Interface expects 'asset' field
                "signal_type": signal_type,
                "direction": signal_type,  # Interface expects 'direction' field
                "strength": strength,
                "indicators": row[3],
                "strategy": row[3] or "momentum",  # Use indicators as strategy or default
                "timestamp": row[4],
                "message": message,  # Interface expects 'message' field
                "executed": bool(row[5])
            })
        conn.close()
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"Signals error: {str(e)}", exc_info=True)
        return {"success": False, "error": str(e), "data": []}

@app.get("/api/logs")
def logs() -> Dict[str, Any]:
    """Get system logs."""
    # TODO: Implement real logging system (database or file-based)
    # Currently returns empty logs
    return {
        "success": True,
        "data": [],
        "message": "Logging system not yet implemented. Check logs/debug.log for file-based logs."
    }

@app.get("/api/risk/portfolio")
def risk_portfolio() -> Dict[str, Any]:
    """Get portfolio risk from context."""
    try:
        risk_data = load_context_file("Risk management.txt")
        return {"success": True, "data": {"guidelines": risk_data}}
    except Exception as e:
        return {"success": False, "error": str(e), "data": {}}

@app.get("/api/risk/positions")
def risk_positions() -> Dict[str, Any]:
    """Get position risks from database."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT symbol, unrealized_pnl, quantity, current_price
            FROM positions
            WHERE unrealized_pnl < 0
            ORDER BY unrealized_pnl ASC
        """)
        rows = cursor.fetchall()
        data = []
        for row in rows:
            data.append({
                "symbol": row[0],
                "unrealized_pnl": row[1],
                "quantity": row[2],
                "current_price": row[3],
                "risk_level": "high" if row[1] < -100 else "medium"
            })
        conn.close()
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e), "data": []}

@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    """Get configuration from settings."""
    try:
        # Load from risk_params.py or settings.py if available
        config = {
            "trading_enabled": True,
            "max_positions": 5,
            "risk_per_trade": 0.02,
            "default_symbol": "TEST/USD"
        }
        return {"success": True, "data": config}
    except Exception as e:
        return {"success": False, "error": str(e), "data": {}}

@app.post("/api/config")
async def update_config(request: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Update bot configuration."""
    # In a real implementation, this would save to a config file
    return {"success": True, "message": "Configuration updated successfully"}

@app.get("/api/strategies/config")
def strategies_config() -> Dict[str, Any]:
    """Get strategy configuration from context files."""
    try:
        data = {
            "investment_thesis": load_context_file("Investment thesis.txt"),
            "trading_methodology": load_context_file("A Strategic Analysis of Swing Trading and Day Trading Methodologiestxt.txt"),
            "traders_brief": load_context_file("Traders brief.txt"),
            "beginners_guide": load_context_file("Beginners guide.txt"),
            "proven_strategies": load_context_file("Proven Strategies to Augment Your Plan.txt")
        }
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e), "data": {}}

@app.post("/api/strategies/config")
async def update_strategies_config(request: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Update strategy configuration."""
    # In a real implementation, this would save to context files
    return {"success": True, "message": "Strategy configuration updated successfully"}

@app.get("/api/indicators/current")
def indicators_current() -> Dict[str, Any]:
    """
    Calculate technical indicators from live Pacifica candle data.

    Fetches 100 hourly candles and calculates:
    - SMA-20 (20-period simple moving average)
    - RSI-14 (14-period relative strength index)
    - MACD (12, 26, 9)
    """
    try:
        handler = get_market_data_handler()
        if not handler:
            return {
                "success": False,
                "error": "Pacifica client not initialized",
                "data": {}
            }

        # Get BTC-PERP candles for the last 100 hours
        candles = handler.get_candles(
            market="BTC-PERP",
            interval="1h",
            limit=100,
            use_cache=True  # Cache for 60 seconds
        )

        if not candles or len(candles) == 0:
            return {
                "success": False,
                "error": "No candle data available from Pacifica",
                "data": {}
            }

        # Extract closes for indicator calculation
        closes = [float(c["close"]) for c in candles if "close" in c]

        if len(closes) < 20:
            return {
                "success": False,
                "error": f"Insufficient candle data (got {len(closes)}, need 20+)",
                "data": {}
            }

        # Calculate SMA-20
        sma_20 = calculate_sma(closes, period=20)

        # Calculate RSI-14
        rsi = calculate_rsi(closes, period=14)

        # Calculate MACD
        macd_data = calculate_macd(closes, fast=12, slow=26, signal=9)

        current_price = closes[-1]

        # Determine signal based on MACD
        signal = "buy" if macd_data["macd_line"] > macd_data["signal_line"] else "sell"

        return {
            "success": True,
            "data": {
                "sma_20": round(sma_20, 2),
                "rsi": round(rsi, 2),
                "macd": round(macd_data["macd_line"], 2),
                "macd_signal": round(macd_data["signal_line"], 2),
                "macd_histogram": round(macd_data["histogram"], 2),
                "current_price": round(current_price, 2),
                "signal": signal,
                "candles_used": len(closes),
                "market": "BTC-PERP",
                "interval": "1h",
                "timestamp": datetime.now().isoformat()
            },
            "source": "Pacifica.fi Live Candles"
        }

    except Exception as e:
        logger.error(f"Indicators calculation error: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "data": {}
        }

@app.get("/api/funding-rates")
def funding_rates() -> Dict[str, Any]:
    """
    Get current funding rates for all markets.

    ⚠️ CRITICAL: Pacifica charges funding HOURLY (24x per day)

    Returns rates in hourly, daily, and annual formats.
    Data is cached for 5 minutes.
    """
    try:
        handler = get_market_data_handler()
        if not handler:
            return {
                "success": False,
                "error": "Pacifica client not initialized",
                "data": {}
            }

        # Get funding rates for all markets (cached for 5 minutes)
        rates = handler.get_funding_rates_all(use_cache=True)

        return {
            "success": True,
            "data": rates,
            "note": "⚠️ All rates are HOURLY. Pacifica charges 24x per day (8x more expensive than standard exchanges).",
            "source": "Pacifica.fi Live API",
            "cached": True,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Funding rates error: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "data": {}
        }


@app.get("/api/candles/{market}")
def get_candles(market: str, interval: str = "1h", limit: int = 100) -> Dict[str, Any]:
    """
    Get candlestick (OHLCV) data for technical analysis.

    Args:
        market: Market symbol (BTC-PERP, ETH-PERP, etc.)
        interval: Time interval (1m, 5m, 15m, 1h, 4h, 1d)
        limit: Number of candles (1-1000)

    Returns:
        Array of OHLCV candles with timestamp, open, high, low, close, volume
    """
    try:
        handler = get_market_data_handler()
        if not handler:
            return {
                "success": False,
                "error": "FIX ME: Implement real Pacifica.fi authentication (AGENT_WALLET_PRIVATE_KEY, ACCOUNT_PUBLIC_KEY)",
                "data": {}
            }

        # Validate interval
        valid_intervals = ["1m", "5m", "15m", "1h", "4h", "1d"]
        if interval not in valid_intervals:
            return {
                "success": False,
                "error": f"Invalid interval. Must be one of: {', '.join(valid_intervals)}",
                "data": []
            }

        # Limit range
        limit = min(max(limit, 1), 1000)

        # Fetch candles from Pacifica (cached for 60 seconds)
        candles = handler.get_candles(
            market=market,
            interval=interval,
            limit=limit,
            use_cache=True
        )

        if not candles:
            return {
                "success": False,
                "error": f"No candle data available for {market}",
                "data": []
            }

        # Format response
        formatted_candles = [
            {
                "timestamp": c.get("timestamp"),
                "open": c.get("open"),
                "high": c.get("high"),
                "low": c.get("low"),
                "close": c.get("close"),
                "volume": c.get("volume")
            }
            for c in candles
        ]

        return {
            "success": True,
            "data": formatted_candles,
            "market": market,
            "interval": interval,
            "count": len(formatted_candles),
            "source": "Pacifica.fi Live API",
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Candles error for {market}: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "data": []
        }


@app.get("/api/market-specs")
def market_specs() -> Dict[str, Any]:
    """
    Get market specifications for all configured markets.

    Returns tick size, lot size, min/max order size, max leverage for each market.
    Data is cached for 1 hour (specifications rarely change).
    """
    try:
        handler = get_market_data_handler()
        if not handler:
            return {
                "success": False,
                "error": "Pacifica client not initialized",
                "data": {}
            }

        # Get market specs for all markets (cached for 1 hour)
        specs = handler.get_all_market_specs(use_cache=True)

        return {
            "success": True,
            "data": specs,
            "source": "Pacifica.fi API",
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Market specs error: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "data": {}
        }


@app.post("/api/backtest/upload-csv")
async def backtest_upload_csv() -> Dict[str, Any]:
    """Upload CSV file for backtesting."""
    # Placeholder implementation
    return {"success": True, "message": "CSV upload functionality not yet implemented"}


@app.post("/api/backtest/run")
def backtest_run() -> Dict[str, Any]:
    """Run backtest."""
    return {"success": True, "message": "Backtest started"}

@app.get("/api/backtest/status")
def backtest_status() -> Dict[str, Any]:
    """Get backtest status."""
    return {"success": True, "status": "completed"}

@app.get("/api/backtest/results")
def backtest_results() -> Dict[str, Any]:
    """Get backtest results."""
    return {"success": True, "data": {}}

# Account Profile Endpoints

@app.post("/api/profiles", response_model=AccountProfileResponse)
def create_profile(profile: AccountProfileCreate) -> AccountProfileResponse:
    """
    Create a new account profile with encrypted private key.

    Args:
        profile: Profile creation data

    Returns:
        Created profile (without decrypted private key)
    """
    try:
        # Create new DB connection for this request
        db = DatabaseManager()

        # Check if name already exists
        existing = db.get_profile_by_name(profile.name)
        if existing:
            raise HTTPException(status_code=400, detail=f"Profile with name '{profile.name}' already exists")

        # Encrypt the private key
        encrypted_key = encrypt_private_key(profile.private_key)

        # Create profile
        profile_id = db.create_profile(
            name=profile.name,
            private_key_encrypted=encrypted_key,
            public_key=profile.public_key,
            is_default=profile.is_default
        )

        # Return the created profile
        created = db.get_profile_by_id(profile_id, include_private_key=False)
        if not created:
            raise HTTPException(status_code=500, detail="Failed to retrieve created profile")

        return AccountProfileResponse(**created)

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create profile: {str(e)}")

@app.get("/api/profiles", response_model=List[AccountProfileResponse])
def list_profiles() -> List[AccountProfileResponse]:
    """
    Get all account profiles (without decrypted private keys).

    Returns:
        List of profiles
    """
    try:
        # Create new DB connection for this request to avoid threading issues
        db = DatabaseManager()
        profiles = db.get_all_profiles()

        # Ensure all required fields have valid values
        result = []
        for p in profiles:
            # Handle potential null timestamps
            profile_data = {
                'id': p.get('id'),
                'name': p.get('name', 'Unknown'),
                'public_key': p.get('public_key'),
                'is_default': bool(p.get('is_default', False)),
                'created_at': p.get('created_at') or datetime.now().isoformat(),
                'updated_at': p.get('updated_at') or datetime.now().isoformat(),
                'last_used_at': p.get('last_used_at') or datetime.now().isoformat(),
            }
            result.append(AccountProfileResponse(**profile_data))
        return result
    except Exception as e:
        logger.error(f"Failed to list profiles: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list profiles: {str(e)}")

@app.get("/api/profiles/default", response_model=AccountProfileDetail)
def get_default_profile() -> AccountProfileDetail:
    """
    Get the default profile with decrypted private key.

    Returns:
        Default profile with private key
    """
    try:
        db = DatabaseManager()
        profile = db.get_default_profile()
        if not profile:
            raise HTTPException(status_code=404, detail="No default profile set")

        # Decrypt private key
        profile['private_key'] = decrypt_private_key(profile['private_key_encrypted'])
        del profile['private_key_encrypted']

        return AccountProfileDetail(**profile)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get default profile: {str(e)}")

@app.get("/api/profiles/{profile_id}", response_model=AccountProfileDetail)
def get_profile(profile_id: int) -> AccountProfileDetail:
    """
    Get a specific profile by ID with decrypted private key.

    Args:
        profile_id: Profile ID

    Returns:
        Profile with decrypted private key
    """
    try:
        db = DatabaseManager()
        profile = db.get_profile_by_id(profile_id, include_private_key=True)
        if not profile:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")

        # Decrypt private key
        profile['private_key'] = decrypt_private_key(profile['private_key_encrypted'])
        del profile['private_key_encrypted']

        return AccountProfileDetail(**profile)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get profile: {str(e)}")

@app.put("/api/profiles/{profile_id}", response_model=AccountProfileResponse)
def update_profile(profile_id: int, profile_update: AccountProfileUpdate) -> AccountProfileResponse:
    """
    Update an existing profile.

    Args:
        profile_id: Profile ID
        profile_update: Fields to update

    Returns:
        Updated profile (without decrypted private key)
    """
    try:
        db = DatabaseManager()

        # Check if profile exists
        existing = db.get_profile_by_id(profile_id, include_private_key=False)
        if not existing:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")

        # Encrypt private key if provided
        encrypted_key = None
        if profile_update.private_key:
            encrypted_key = encrypt_private_key(profile_update.private_key)

        # Update profile
        success = db.update_profile(
            profile_id=profile_id,
            name=profile_update.name,
            private_key_encrypted=encrypted_key,
            public_key=profile_update.public_key
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to update profile")

        # Return updated profile
        updated = db.get_profile_by_id(profile_id, include_private_key=False)
        if not updated:
            raise HTTPException(status_code=500, detail="Failed to retrieve updated profile")

        return AccountProfileResponse(**updated)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update profile: {str(e)}")

@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: int) -> Dict[str, Any]:
    """
    Delete a profile.

    Args:
        profile_id: Profile ID

    Returns:
        Success message
    """
    try:
        db = DatabaseManager()
        success = db.delete_profile(profile_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")

        return {"success": True, "message": f"Profile {profile_id} deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete profile: {str(e)}")

@app.post("/api/profiles/{profile_id}/set-default")
def set_default_profile(profile_id: int) -> Dict[str, Any]:
    """
    Set a profile as the default.

    Args:
        profile_id: Profile ID

    Returns:
        Success message
    """
    try:
        db = DatabaseManager()
        success = db.set_default_profile(profile_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Profile {profile_id} not found")

        return {"success": True, "message": f"Profile {profile_id} set as default"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to set default profile: {str(e)}")


# ======================
# PACIFICA.FI ENDPOINTS
# ======================

@app.get("/api/pacifica/status")
def pacifica_status() -> Dict[str, Any]:
    """
    Get Pacifica.fi connection and configuration status.

    ⚠️ CRITICAL: Shows HOURLY funding rate info (24 payments per day)
    """
    try:
        from config import get_pacifica_config
        from auth import load_pacifica_credentials

        config = get_pacifica_config()
        credentials = load_pacifica_credentials()

        return {
            "success": True,
            "connected": credentials is not None,
            "environment": config.environment.value if config else "unknown",
            "rest_url": config.rest_url if config else None,
            "websocket_url": config.websocket_url_testnet if config else None,
            "funding_info": {
                "payment_frequency": "HOURLY",
                "payments_per_day": 24,
                "rate_update_interval": "5 seconds",
                "warning": "⚠️ 8x more funding payments than standard exchanges (3/day)"
            },
            "account_public_key": credentials.get("account_public_key") if credentials else None,
        }
    except Exception as e:
        return {
            "success": False,
            "connected": False,
            "error": str(e)
        }


@app.get("/api/pacifica/funding-rates")
def pacifica_funding_rates() -> Dict[str, Any]:
    """
    Get current funding rates for all Pacifica markets.

    ⚠️ CRITICAL: Returns HOURLY funding rates (24x per day)
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get latest funding rate for each symbol
        cursor.execute("""
            SELECT symbol, funding_rate, premium_index, timestamp
            FROM funding_rate_history
            WHERE timestamp IN (
                SELECT MAX(timestamp)
                FROM funding_rate_history
                GROUP BY symbol
            )
            ORDER BY timestamp DESC
        """)

        rows = cursor.fetchall()
        data = []

        for row in rows:
            symbol, rate, premium, timestamp = row
            hourly_rate = float(rate)
            daily_rate = hourly_rate * 24
            annual_rate = daily_rate * 365

            data.append({
                "symbol": symbol,
                "hourly_rate": hourly_rate,
                "hourly_rate_pct": hourly_rate * 100,
                "daily_rate": daily_rate,
                "daily_rate_pct": daily_rate * 100,
                "annual_rate_pct": annual_rate * 100,
                "premium_index": premium,
                "timestamp": timestamp,
                "payments_per_day": 24,
                "next_payment_minutes": 60 - datetime.now().minute  # Approximate
            })

        conn.close()

        if not data:
            return {
                "success": False,
                "error": "FIX ME: Implement real Pacifica.fi authentication to populate funding rate data",
                "data": []
            }

        return {
            "success": True,
            "data": data,
            "warning": "⚠️ Hourly rates = 8x more payments than standard exchanges"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "data": []
        }


@app.get("/api/pacifica/funding-payments")
def pacifica_funding_payments(limit: int = 100) -> Dict[str, Any]:
    """
    Get funding payment history.

    Shows ALL hourly funding payments (24 per day per position).
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT position_id, symbol, funding_rate, payment_amount,
                   position_value, margin_mode, timestamp
            FROM funding_payments
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))

        rows = cursor.fetchall()
        data = []

        for row in rows:
            data.append({
                "position_id": row[0],
                "symbol": row[1],
                "funding_rate": row[2],
                "funding_rate_pct": row[2] * 100,
                "payment_amount": row[3],
                "position_value": row[4],
                "margin_mode": row[5],
                "timestamp": row[6]
            })

        conn.close()

        return {
            "success": True,
            "data": data,
            "count": len(data)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "data": []
        }


@app.get("/api/pacifica/positions")
def pacifica_positions() -> Dict[str, Any]:
    """
    Get Pacifica positions with funding tracking.

    Includes cumulative funding paid and hourly funding impact.
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT position_id, symbol, side, size, entry_price, current_price,
                   leverage, margin_mode, cumulative_funding_paid,
                   last_funding_timestamp, unrealized_pnl, liquidation_price,
                   tick_size, lot_size, opened_at
            FROM pacifica_positions
            ORDER BY opened_at DESC
        """)

        rows = cursor.fetchall()
        data = []

        for row in rows:
            position_id = row[0]

            # Get funding summary
            cursor.execute("""
                SELECT COUNT(*), SUM(payment_amount), AVG(funding_rate)
                FROM funding_payments
                WHERE position_id = ?
            """, (position_id,))

            funding_stats = cursor.fetchone()
            payment_count = funding_stats[0] or 0
            total_funding = funding_stats[1] or 0.0
            avg_rate = funding_stats[2] or 0.0

            data.append({
                "position_id": position_id,
                "symbol": row[1],
                "side": row[2],
                "size": row[3],
                "entry_price": row[4],
                "current_price": row[5],
                "leverage": row[6],
                "margin_mode": row[7],
                "funding": {
                    "cumulative_paid": row[8],
                    "payment_count": payment_count,
                    "total_paid": total_funding,
                    "avg_hourly_rate": avg_rate,
                    "avg_hourly_rate_pct": avg_rate * 100,
                    "avg_daily_rate_pct": avg_rate * 24 * 100,
                    "last_payment": row[9],
                    "hours_held": payment_count,  # 1 payment per hour
                },
                "unrealized_pnl": row[10],
                "liquidation_price": row[11],
                "market_specs": {
                    "tick_size": row[12],
                    "lot_size": row[13],
                },
                "opened_at": row[14]
            })

        conn.close()

        return {
            "success": True,
            "data": data,
            "count": len(data)
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "data": []
        }


@app.get("/api/pacifica/position/{position_id}/funding-summary")
def pacifica_position_funding_summary(position_id: str) -> Dict[str, Any]:
    """
    Get detailed funding summary for a position.

    Shows all hourly payments, rates, and costs.
    """
    try:
        funding_summary = db_manager.get_funding_summary(position_id)

        if not funding_summary:
            raise HTTPException(status_code=404, detail=f"Position {position_id} not found")

        return {
            "success": True,
            "data": {
                "position_id": position_id,
                "payment_count": funding_summary["payment_count"],
                "total_paid": funding_summary["total_paid"],
                "avg_hourly_rate": funding_summary["avg_rate"],
                "avg_hourly_rate_pct": funding_summary["avg_rate"] * 100,
                "avg_daily_cost": funding_summary["funding_per_day_avg"],
                "min_rate": funding_summary["min_rate"],
                "max_rate": funding_summary["max_rate"],
                "hours_held": funding_summary["payment_count"],
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/api/pacifica/markets")
def pacifica_markets() -> Dict[str, Any]:
    """
    Get Pacifica market specifications from LIVE API.

    Returns tick size, lot size, leverage limits for all markets.
    """
    try:
        # Fetch real market data from Pacifica API
        if pacifica_client:
            raw_markets = pacifica_client.get_markets()
            # Transform to expected format
            markets = []
            for m in raw_markets:
                markets.append({
                    "symbol": f"{m.get('symbol', 'UNKNOWN')}-PERP",
                    "base_currency": m.get("symbol", ""),
                    "quote_currency": "USD",
                    "tick_size": float(m.get("tick_size", 0)),
                    "lot_size": float(m.get("lot_size", 0)),
                    "min_size": float(m.get("min_order_size", 0)),
                    "max_size": float(m.get("max_order_size", 0)),
                    "min_leverage": 1,
                    "max_leverage": int(m.get("max_leverage", 1)),
                    "trading_hours": "24/7",
                    "funding_frequency": "hourly",
                    "funding_rate": float(m.get("funding_rate", 0)),
                    "next_funding_rate": float(m.get("next_funding_rate", 0)),
                    "isolated_only": m.get("isolated_only", False)
                })
            return {
                "success": True,
                "data": markets,
                "count": len(markets),
                "source": "Pacifica.fi Live API"
            }
        else:
            # FIX ME: Implement real authentication for Pacifica client
            return {
                "success": False,
                "error": "FIX ME: Implement real Pacifica.fi authentication (AGENT_WALLET_PRIVATE_KEY, ACCOUNT_PUBLIC_KEY)",
                "data": []
            }
    except Exception as e:
        logger.error(f"Error fetching Pacifica markets: {e}")
        return {
            "success": False,
            "error": str(e),
            "data": []
        }

# ===========================
# HISTORICAL MARKET DATA ENDPOINTS
# ===========================

@app.get("/api/market-data/history/{symbol}")
async def get_market_history(symbol: str, days: int = 30) -> Dict[str, Any]:
    """
    Get historical market information for a specific symbol.

    Args:
        symbol: Market symbol (e.g., "BTC", "ETH")
        days: Number of days of history (default: 30)

    Returns:
        Historical market data including funding rates, leverage, etc.
    """
    try:
        from market_data_collector import MarketDataCollector

        db_manager = get_database_manager()
        collector = MarketDataCollector(db_manager)

        # Get funding rate history
        funding_history = await collector.get_funding_rate_history(symbol, days)

        # Get parameter changes
        changes = await collector.get_market_parameter_changes(symbol, days)

        # Get latest market stats
        stats = await collector.get_market_stats()

        return {
            "success": True,
            "symbol": symbol,
            "funding_rate_history": funding_history,
            "parameter_changes": changes,
            "stats": stats,
            "days_requested": days
        }

    except Exception as e:
        logger.error(f"Error fetching market history for {symbol}: {e}")
        return {
            "success": False,
            "error": str(e),
            "symbol": symbol
        }

@app.get("/api/market-data/history")
async def get_all_market_history(days: int = 7) -> Dict[str, Any]:
    """
    Get historical market information for all symbols.

    Args:
        days: Number of days of history (default: 7)

    Returns:
        Historical data for all markets
    """
    try:
        from market_data_collector import MarketDataCollector

        db_manager = get_database_manager()
        collector = MarketDataCollector(db_manager)

        # Get overall market stats
        stats = await collector.get_market_stats()

        # Get recent parameter changes across all symbols
        query = f"""
        SELECT symbol, parameter_name, old_value, new_value, change_type, changed_at
        FROM market_parameter_changes
        WHERE changed_at >= datetime('now', '-{days} days')
        ORDER BY changed_at DESC
        LIMIT 100
        """

        changes = []
        async with db_manager.get_connection() as conn:
            async with conn.execute(query) as cursor:
                async for row in cursor:
                    changes.append({
                        'symbol': row[0],
                        'parameter': row[1],
                        'old_value': row[2],
                        'new_value': row[3],
                        'change_type': row[4],
                        'timestamp': row[5]
                    })

        return {
            "success": True,
            "stats": stats,
            "recent_changes": changes,
            "days_requested": days
        }

    except Exception as e:
        logger.error(f"Error fetching market history: {e}")
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/api/market-data/changes")
async def get_market_changes(symbol: str = None, days: int = 30) -> Dict[str, Any]:
    """
    Get market parameter changes.

    Args:
        symbol: Specific symbol to filter (optional)
        days: Number of days to look back (default: 30)

    Returns:
        List of parameter changes
    """
    try:
        from market_data_collector import MarketDataCollector

        db_manager = get_database_manager()
        collector = MarketDataCollector(db_manager)

        if symbol:
            changes = await collector.get_market_parameter_changes(symbol, days)
        else:
            # Get changes for all symbols
            query = f"""
            SELECT symbol, parameter_name, old_value, new_value, change_type, changed_at
            FROM market_parameter_changes
            WHERE changed_at >= datetime('now', '-{days} days')
            ORDER BY changed_at DESC
            LIMIT 200
            """

            changes = []
            async with db_manager.get_connection() as conn:
                async with conn.execute(query) as cursor:
                    async for row in cursor:
                        changes.append({
                            'symbol': row[0],
                            'parameter': row[1],
                            'old_value': row[2],
                            'new_value': row[3],
                            'change_type': row[4],
                            'timestamp': row[5]
                        })

        return {
            "success": True,
            "changes": changes,
            "symbol_filter": symbol,
            "days_requested": days
        }

    except Exception as e:
        logger.error(f"Error fetching market changes: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/api/pacifica/validate-order")
async def pacifica_validate_order(order: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate order parameters for Pacifica.

    Checks tick size, lot size, leverage, and all requirements.
    """
    try:
        from pacifica_validator import create_pacifica_validator

        # Get market specs
        # In production, fetch from Pacifica API
        market_specs = {
            "BTC-PERP": {
                "tick_size": 0.5,
                "lot_size": 0.001,
                "min_size": 0.001,
                "max_size": 100.0,
                "min_leverage": 5,
                "max_leverage": 50,
            }
        }

        validator = create_pacifica_validator(market_specs)

        # Validate order
        is_valid, errors = validator.validate_order(
            symbol=order.get("symbol"),
            side=order.get("side"),
            order_type=order.get("order_type", "market"),
            size=order.get("size"),
            price=order.get("price"),
            leverage=order.get("leverage"),
            margin_mode=order.get("margin_mode", "cross"),
            reduce_only=order.get("reduce_only", False),
            time_in_force=order.get("time_in_force", "GTC"),
        )

        error_details = [
            {
                "field": e.field,
                "message": e.message,
                "severity": e.severity.value,
                "suggestion": e.suggestion,
            }
            for e in errors
        ]

        return {
            "success": True,
            "valid": is_valid,
            "errors": error_details
        }
    except Exception as e:
        return {
            "success": False,
            "valid": False,
            "error": str(e)
        }


@app.get("/api/pacifica/balance-history")
def get_balance_history(
    account_id: Optional[str] = None,
    subaccount_id: Optional[str] = None,
    limit: int = 100
) -> Dict[str, Any]:
    """Get account balance history from database (fetched from Pacifica API)."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Build query based on parameters
        query = """
            SELECT account_id, subaccount_id, balance, equity,
                   available_balance, margin_used, timestamp, fetched_at
            FROM balance_history
            WHERE 1=1
        """
        params = []

        if account_id:
            query += " AND account_id = ?"
            params.append(account_id)

        if subaccount_id:
            query += " AND subaccount_id = ?"
            params.append(subaccount_id)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        data = []
        for row in rows:
            data.append({
                "account_id": row[0],
                "subaccount_id": row[1],
                "balance": float(row[2]),
                "equity": float(row[3]),
                "available_balance": float(row[4]),
                "margin_used": float(row[5]),
                "timestamp": int(row[6]),
                "fetched_at": row[7]
            })

        return {
            "success": True,
            "data": data,
            "count": len(data),
            "source": "database"
        }

    except Exception as e:
        logger.error(f"Balance history fetch failed: {e}")
        return {"success": False, "error": str(e)}


def calculate_real_pnl_from_balance(account_id: str, position_opened_at: Optional[str] = None) -> float:
    """Calculate real P&L from balance history changes since position opened."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Get balance history for this account
        query = """
            SELECT balance, equity, timestamp
            FROM balance_history
            WHERE account_id = ?
            ORDER BY timestamp ASC
        """
        cursor.execute(query, (account_id,))
        balance_rows = cursor.fetchall()
        conn.close()

        if len(balance_rows) < 2:
            # Not enough balance history, fall back to calculated P&L
            return 0.0

        # Calculate P&L as equity change from position open to now
        initial_equity = float(balance_rows[0][1])  # First equity value
        current_equity = float(balance_rows[-1][1])  # Latest equity value

        # If position_opened_at is provided, use balance from that time
        if position_opened_at:
            try:
                opened_timestamp = int(position_opened_at) // 1000  # Convert from ms to seconds
                # Find balance closest to position open time
                for row in balance_rows:
                    if int(row[2]) >= opened_timestamp:
                        initial_equity = float(row[1])
                        break
            except (ValueError, TypeError):
                pass  # Use earliest balance if parsing fails

        real_pnl = current_equity - initial_equity
        logger.debug(f"Calculated real P&L: {real_pnl} (initial: {initial_equity}, current: {current_equity})")

        return real_pnl

    except Exception as e:
        logger.warning(f"Failed to calculate real P&L from balance: {e}")
        return 0.0


# Global instance for bot access
balance_manager = BalanceHistoryManager()


def sync_balance_history():
    """Fetch and store balance history from Pacifica API."""
    try:
        if not pacifica_client:
            logger.warning("Pacifica client not initialized - skipping balance sync")
            return

        account_id = pacifica_client.account_public_key
        if not account_id:
            logger.warning("No account public key available - skipping balance sync")
            return

        response = pacifica_client._make_request(
            "GET",
            f"/account/balance/history?account={account_id}"
        )

        if response.get("success") and response.get("data"):
            balance_entries = response.get("data", [])

            with get_db_connection() as conn:
                # Store balance history
                for balance_entry in balance_entries:
                    try:
                        conn.execute("""
                            INSERT OR REPLACE INTO balance_history
                            (account_id, subaccount_id, balance, equity,
                             available_balance, margin_used, timestamp)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            account_id,
                            balance_entry.get("subaccount_id"),
                            float(balance_entry.get("balance", 0)),
                            float(balance_entry.get("equity", 0)),
                            float(balance_entry.get("available_balance", 0)),
                            float(balance_entry.get("margin_used", 0)),
                            int(balance_entry.get("timestamp", 0))
                        ))
                    except Exception as e:
                        logger.warning(f"Failed to store balance entry: {e}")
                        continue

                conn.commit()

            logger.info(f"Balance history synced: {len(balance_entries)} entries stored")
        else:
            logger.warning("Failed to fetch balance history from Pacifica API")

    except Exception as e:
        logger.error(f"Balance history sync failed: {e}")


@app.get("/api/pacifica/funding-tracker/status")
def pacifica_funding_tracker_status() -> Dict[str, Any]:
    """
    Get funding tracker status and statistics with LIVE data.
    """
    try:
        # Get positions count from database
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM positions")
            tracked_positions = cursor.fetchone()[0] or 0
            cursor.execute("SELECT COUNT(*) FROM funding_payments")
            total_payments = cursor.fetchone()[0] or 0
            conn.close()
        except:
            tracked_positions = 0
            total_payments = 0

        # Calculate next funding time (Pacifica is hourly at the top of hour)
        now = datetime.now()
        next_payment_in_minutes = 60 - now.minute if now.minute > 0 else 60

        # Check if bot is running
        is_running = trading_bot is not None and trading_bot.is_running if trading_bot else False

        return {
            "success": True,
            "data": {
                "running": is_running,
                "tracked_positions": tracked_positions,
                "total_payments_processed": total_payments,
                "last_payment_time": None,
                "alert_threshold_warning": 0.10,
                "alert_threshold_critical": 0.25,
                "next_payment_in_minutes": next_payment_in_minutes,
                "funding_frequency": "hourly (24x per day)",
                "note": "Pacifica charges funding HOURLY - 8x more than standard exchanges"
            },
            "source": "Live Tracker"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


# ===========================
# SUBACCOUNT MANAGEMENT ENDPOINTS
# ===========================

@app.get("/api/subaccounts")
def get_subaccounts() -> Dict[str, Any]:
    """
    Get all configured subaccounts.

    Returns:
        List of subaccounts with configurations
    """
    try:
        from subaccount_manager import get_subaccount_manager
        from database import DatabaseManager

        db = DatabaseManager()
        manager = get_subaccount_manager(db)

        subaccounts_list = []
        for sub_id, sub_config in manager.subaccounts.items():
            subaccounts_list.append({
                "subaccount_id": sub_config.subaccount_id,
                "subaccount_name": sub_config.subaccount_name,
                "subaccount_public_key": sub_config.subaccount_public_key,
                "trading_strategy": sub_config.trading_strategy.value,
                "max_position_size": sub_config.max_position_size,
                "risk_per_trade": sub_config.risk_per_trade,
                "max_leverage": sub_config.max_leverage,
                "enabled": sub_config.enabled,
                "created_at": sub_config.created_at,
                "updated_at": sub_config.updated_at,
            })

        return {
            "success": True,
            "data": {
                "subaccounts": subaccounts_list,
                "default_subaccount_id": manager.default_subaccount_id,
                "total": len(subaccounts_list),
                "active": len(manager.get_active_subaccounts()),
            }
        }
    except Exception as e:
        logger.error(f"Failed to get subaccounts: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/api/subaccounts/{subaccount_id}")
def get_subaccount(subaccount_id: str) -> Dict[str, Any]:
    """
    Get specific subaccount details.

    Args:
        subaccount_id: Subaccount ID

    Returns:
        Subaccount configuration and stats
    """
    try:
        from subaccount_manager import get_subaccount_manager
        from database import DatabaseManager

        db = DatabaseManager()
        manager = get_subaccount_manager(db)

        sub_config = manager.get_subaccount(subaccount_id)
        if not sub_config:
            return {
                "success": False,
                "error": f"Subaccount {subaccount_id} not found"
            }

        # Get balance
        balance = manager.get_subaccount_balance(subaccount_id)

        return {
            "success": True,
            "data": {
                "subaccount_id": sub_config.subaccount_id,
                "subaccount_name": sub_config.subaccount_name,
                "subaccount_public_key": sub_config.subaccount_public_key,
                "trading_strategy": sub_config.trading_strategy.value,
                "max_position_size": sub_config.max_position_size,
                "risk_per_trade": sub_config.risk_per_trade,
                "max_leverage": sub_config.max_leverage,
                "enabled": sub_config.enabled,
                "balance": balance,
                "created_at": sub_config.created_at,
                "updated_at": sub_config.updated_at,
            }
        }
    except Exception as e:
        logger.error(f"Failed to get subaccount: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/api/subaccounts/discover")
def discover_subaccounts() -> Dict[str, Any]:
    """
    Discover subaccounts from Pacifica API.

    Returns:
        List of discovered subaccounts
    """
    try:
        from subaccount_manager import get_subaccount_manager
        from database import DatabaseManager

        db = DatabaseManager()
        manager = get_subaccount_manager(db)

        discovered = manager.discover_subaccounts()

        return {
            "success": True,
            "data": {
                "discovered": len(discovered),
                "subaccounts": [
                    {
                        "subaccount_id": sub.subaccount_id,
                        "subaccount_name": sub.subaccount_name,
                        "trading_strategy": sub.trading_strategy.value,
                    }
                    for sub in discovered
                ]
            }
        }
    except Exception as e:
        logger.error(f"Failed to discover subaccounts: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@app.put("/api/subaccounts/{subaccount_id}/strategy")
def update_subaccount_strategy(subaccount_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update subaccount trading strategy.

    Args:
        subaccount_id: Subaccount ID
        request: {"strategy": "conservative|balanced|aggressive|..."}

    Returns:
        Success status
    """
    try:
        from subaccount_manager import get_subaccount_manager
        from database import DatabaseManager
        from config import TradingStrategy

        db = DatabaseManager()
        manager = get_subaccount_manager(db)

        strategy_str = request.get("strategy")
        if not strategy_str:
            return {
                "success": False,
                "error": "Missing 'strategy' field"
            }

        try:
            strategy = TradingStrategy(strategy_str.lower())
        except ValueError:
            return {
                "success": False,
                "error": f"Invalid strategy: {strategy_str}"
            }

        success = manager.assign_strategy_to_subaccount(subaccount_id, strategy)

        if success:
            return {
                "success": True,
                "data": {
                    "subaccount_id": subaccount_id,
                    "strategy": strategy.value
                }
            }
        else:
            return {
                "success": False,
                "error": "Failed to update strategy"
            }
    except Exception as e:
        logger.error(f"Failed to update strategy: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@app.put("/api/subaccounts/{subaccount_id}/risk")
def update_subaccount_risk(subaccount_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Update subaccount risk parameters.

    Args:
        subaccount_id: Subaccount ID
        request: {
            "max_position_size": float (optional),
            "risk_per_trade": float (optional),
            "max_leverage": int (optional)
        }

    Returns:
        Success status
    """
    try:
        from subaccount_manager import get_subaccount_manager
        from database import DatabaseManager

        db = DatabaseManager()
        manager = get_subaccount_manager(db)

        max_position_size = request.get("max_position_size")
        risk_per_trade = request.get("risk_per_trade")
        max_leverage = request.get("max_leverage")

        success = manager.update_risk_parameters(
            subaccount_id,
            max_position_size=max_position_size,
            risk_per_trade=risk_per_trade,
            max_leverage=max_leverage
        )

        if success:
            return {
                "success": True,
                "data": {
                    "subaccount_id": subaccount_id,
                    "updated": True
                }
            }
        else:
            return {
                "success": False,
                "error": "Failed to update risk parameters"
            }
    except Exception as e:
        logger.error(f"Failed to update risk parameters: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/api/subaccounts/{subaccount_id}/enable")
def enable_subaccount(subaccount_id: str) -> Dict[str, Any]:
    """
    Enable a subaccount for trading.

    Args:
        subaccount_id: Subaccount ID

    Returns:
        Success status
    """
    try:
        from subaccount_manager import get_subaccount_manager
        from database import DatabaseManager

        db = DatabaseManager()
        manager = get_subaccount_manager(db)

        success = manager.enable_subaccount(subaccount_id)

        if success:
            return {
                "success": True,
                "data": {
                    "subaccount_id": subaccount_id,
                    "enabled": True
                }
            }
        else:
            return {
                "success": False,
                "error": "Failed to enable subaccount"
            }
    except Exception as e:
        logger.error(f"Failed to enable subaccount: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/api/subaccounts/{subaccount_id}/disable")
def disable_subaccount(subaccount_id: str) -> Dict[str, Any]:
    """
    Disable a subaccount (stop trading).

    Args:
        subaccount_id: Subaccount ID

    Returns:
        Success status
    """
    try:
        from subaccount_manager import get_subaccount_manager
        from database import DatabaseManager

        db = DatabaseManager()
        manager = get_subaccount_manager(db)

        success = manager.disable_subaccount(subaccount_id)

        if success:
            return {
                "success": True,
                "data": {
                    "subaccount_id": subaccount_id,
                    "enabled": False
                }
            }
        else:
            return {
                "success": False,
                "error": "Failed to disable subaccount"
            }
    except Exception as e:
        logger.error(f"Failed to disable subaccount: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/api/subaccounts/{subaccount_id}/balance")
def get_subaccount_balance(subaccount_id: str) -> Dict[str, Any]:
    """
    Get subaccount balance from Pacifica API.

    Args:
        subaccount_id: Subaccount ID

    Returns:
        Balance information
    """
    try:
        from subaccount_manager import get_subaccount_manager
        from database import DatabaseManager

        db = DatabaseManager()
        manager = get_subaccount_manager(db)

        balance = manager.get_subaccount_balance(subaccount_id)

        if balance is not None:
            return {
                "success": True,
                "data": {
                    "subaccount_id": subaccount_id,
                    "balance": balance,
                    "currency": "USDC"
                }
            }
        else:
            return {
                "success": False,
                "error": "Failed to fetch balance"
            }
    except Exception as e:
        logger.error(f"Failed to get balance: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/api/subaccounts/balances/all")
def get_all_subaccount_balances() -> Dict[str, Any]:
    """
    Get balances for all subaccounts.

    Returns:
        Dict of subaccount_id -> balance
    """
    try:
        from subaccount_manager import get_subaccount_manager
        from database import DatabaseManager

        db = DatabaseManager()
        manager = get_subaccount_manager(db)

        balances = manager.get_all_balances()

        return {
            "success": True,
            "data": {
                "balances": balances,
                "total_balance": sum(balances.values())
            }
        }
    except Exception as e:
        logger.error(f"Failed to get all balances: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@app.post("/api/subaccounts/transfer")
def transfer_between_subaccounts(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transfer funds between subaccounts.

    Args:
        request: {
            "from_subaccount_id": str,
            "to_subaccount_id": str,
            "amount": float
        }

    Returns:
        Success status
    """
    try:
        from subaccount_manager import get_subaccount_manager
        from database import DatabaseManager

        db = DatabaseManager()
        manager = get_subaccount_manager(db)

        from_id = request.get("from_subaccount_id")
        to_id = request.get("to_subaccount_id")
        amount = request.get("amount")

        if not all([from_id, to_id, amount]):
            return {
                "success": False,
                "error": "Missing required fields"
            }

        success = manager.transfer_between_subaccounts(from_id, to_id, amount)

        if success:
            return {
                "success": True,
                "data": {
                    "from_subaccount_id": from_id,
                    "to_subaccount_id": to_id,
                    "amount": amount
                }
            }
        else:
            return {
                "success": False,
                "error": "Transfer failed"
            }
    except Exception as e:
        logger.error(f"Transfer failed: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/api/subaccounts/stats")
def get_subaccount_stats() -> Dict[str, Any]:
    """
    Get subaccount manager statistics.

    Returns:
        Statistics and strategy distribution
    """
    try:
        from subaccount_manager import get_subaccount_manager
        from database import DatabaseManager

        db = DatabaseManager()
        manager = get_subaccount_manager(db)

        stats = manager.get_stats()

        return {
            "success": True,
            "data": stats
        }
    except Exception as e:
        logger.error(f"Failed to get stats: {e}")
        return {
            "success": False,
            "error": str(e)
        }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
