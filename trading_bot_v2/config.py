import os
import logging
from typing import Optional
from enum import Enum
from .env_precedence import load_env

# Load environment variables from .env.
#
# Precedence is still .env-wins (override=True), unchanged - but it is no
# longer silent. load_env reports every key set in BOTH .env and the
# process environment with a different value, i.e. exactly the keys whose
# resolution this choice decided, and warns with their NAMES (never their
# values). That set is what has to be empty before the precedence can be
# flipped to the conventional flag > env > file > default order; see
# env_precedence.py, and run `python -m trading_bot_v2.env_precedence`.
ENV_LOAD_REPORT = load_env()

# Imported AFTER load_dotenv on purpose: database.py resolves DATABASE_PATH at
# import time, so importing it before the .env is loaded would make it resolve
# a different file than this module - the exact split this shared constant
# exists to close.
from .database import DEFAULT_DATABASE_PATH  # noqa: E402

# Define all enums locally to avoid import issues with "Example files/core_logic"
# These are duplicated from core_logic/config.py for proper module resolution


class MarketState(str, Enum):
    """Market state classification."""

    TREND = "trend"
    RANGE = "range"
    TRANSITION = "transition"
    UNKNOWN = "unknown"


class StrategyType(str, Enum):
    """Trading strategy types."""

    TREND_FOLLOWING = "trend_following"
    BREAKOUT = "breakout"
    LIQUIDATION_CAPTURE = "liquidation_capture"
    MEAN_REVERSION = "mean_reversion"
    MA_CROSSOVER = "ma_crossover"
    GRID_TRADING = "grid_trading"
    VWAP_SCALPING = "vwap_scalping"
    FUNDING_ARB = "funding_arb"
    MOMENTUM_SCALPING = "momentum_scalping"
    ORDERBOOK_IMBALANCE = "orderbook_imbalance"
    SESSION_RANGE_BREAKOUT = "session_range_breakout"
    CALENDAR_FLOW = "calendar_flow"
    VWAP_PULLBACK = "vwap_pullback"


class AssetClass(str, Enum):
    """Asset class types for position sizing and risk management."""

    BTC_ETH = "btc_eth"
    LARGE_CAP = "large_cap"
    SMALL_CAP = "small_cap"
    CRYPTO = "crypto"
    PERPETUAL = "perpetual"
    STOCK = "stock"
    FOREX = "forex"
    COMMODITY = "commodity"


class TradeQuality(str, Enum):
    """Trade quality classification for position sizing."""

    STANDARD = "standard"
    HIGH_CONVICTION = "high_conviction"
    EXCEPTIONAL = "exceptional"


def _env_int(name: str, default: int, minimum: Optional[int] = None) -> int:
    """Read an int env var; unparseable values fall back to ``default``."""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _env_float(name: str, default: float, minimum: Optional[float] = None) -> float:
    """Read a float env var; unparseable values fall back to ``default``."""
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


class Config:
    """Configuration class for the trading bot v2."""

    def __init__(self) -> None:
        """Initialize configuration from environment variables."""
        # ---- Database backend selection ----
        # "sqlite" (default) or "postgres"
        self.database_backend: str = os.getenv("DATABASE_BACKEND", "sqlite").lower()
        # Default imported from database.py so the two modules cannot
        # disagree about which file DATABASE_PATH means (see
        # database.DEFAULT_DATABASE_PATH).
        self.database_path: str = os.getenv("DATABASE_PATH", DEFAULT_DATABASE_PATH)

        # ---- PostgreSQL connection parameters ----
        self.pg_host: str = os.getenv("PG_HOST", "localhost")
        try:
            self.pg_port: int = int(os.getenv("PG_PORT", "5432"))
        except ValueError:
            self.pg_port = 5432
        self.pg_database: str = os.getenv("PG_DATABASE", "trading_bot")
        self.pg_user: str = os.getenv("PG_USER", "postgres")
        self.pg_password: str = os.getenv("PG_PASSWORD", "")
        self.pg_schema: str = os.getenv("PG_SCHEMA", "public")

        # ---- PostgreSQL connection pool settings ----
        try:
            self.pg_pool_min: int = int(os.getenv("PG_POOL_MIN", "2"))
        except ValueError:
            self.pg_pool_min = 2
        try:
            self.pg_pool_max: int = int(os.getenv("PG_POOL_MAX", "10"))
        except ValueError:
            self.pg_pool_max = 10
        try:
            self.pg_pool_timeout: int = int(os.getenv("PG_POOL_TIMEOUT", "30"))
        except ValueError:
            self.pg_pool_timeout = 30

        self.pacifica_private_key: Optional[str] = os.getenv("AGENT_WALLET_PRIVATE_KEY")
        self.pacifica_public_key: Optional[str] = os.getenv("ACCOUNT_PUBLIC_KEY")

        # Parse testnet with more flexible boolean conversion
        testnet_env = os.getenv("TESTNET", "true").lower()
        self.testnet: bool = testnet_env in ("true", "1", "yes")

        # Parse auto-trading flag
        auto_trading_env = os.getenv("ENABLE_AUTO_TRADING", "false").lower()
        self.enable_auto_trading: bool = auto_trading_env in ("true", "1", "yes")

        # Parse websocket flag
        websocket_env = os.getenv("ENABLE_WEBSOCKET", "true").lower()
        self.enable_websocket: bool = websocket_env in ("true", "1", "yes")

        # Parse integer values with error handling
        try:
            self.max_positions: int = int(os.getenv("MAX_POSITIONS", "15"))
        except ValueError:
            raise ValueError("MAX_POSITIONS must be a valid integer")

        try:
            self.max_positions_per_strategy: int = int(
                os.getenv("MAX_POSITIONS_PER_STRATEGY", "3")
            )
        except ValueError:
            raise ValueError("MAX_POSITIONS_PER_STRATEGY must be a valid integer")

        try:
            self.max_grid_positions: int = int(os.getenv("MAX_GRID_POSITIONS", "10"))
        except ValueError:
            raise ValueError("MAX_GRID_POSITIONS must be a valid integer")

        # Grid partial unwind on regime change (keep trend-aligned positions)
        self.grid_partial_unwind_enabled: bool = (
            os.getenv("GRID_PARTIAL_UNWIND_ENABLED", "true").lower() == "true"
        )

        # Grid refresh/recenter configuration
        try:
            self.grid_refresh_min_atr_drift: float = float(
                os.getenv("GRID_REFRESH_MIN_ATR_DRIFT", "1.8")
            )
        except ValueError:
            self.grid_refresh_min_atr_drift = 1.8

        try:
            self.grid_refresh_min_confidence: float = float(
                os.getenv("GRID_REFRESH_MIN_CONFIDENCE", "0.72")
            )
        except ValueError:
            self.grid_refresh_min_confidence = 0.72

        try:
            self.grid_refresh_min_conf_improve: float = float(
                os.getenv("GRID_REFRESH_MIN_CONF_IMPROVE", "0.08")
            )
        except ValueError:
            self.grid_refresh_min_conf_improve = 0.08

        try:
            self.grid_refresh_cooldown_minutes: int = int(
                os.getenv("GRID_REFRESH_COOLDOWN_MINUTES", "45")
            )
        except ValueError:
            self.grid_refresh_cooldown_minutes = 45

        try:
            self.grid_refresh_max_per_day: int = int(
                os.getenv("GRID_REFRESH_MAX_PER_DAY", "3")
            )
        except ValueError:
            self.grid_refresh_max_per_day = 3

        # Dynamic spacing configuration
        self.grid_dynamic_spacing_enabled: bool = (
            os.getenv("GRID_DYNAMIC_SPACING_ENABLED", "true").lower() == "true"
        )

        try:
            self.grid_dynamic_spacing_recalc_minutes: int = int(
                os.getenv("GRID_DYNAMIC_SPACING_RECALC_MINUTES", "30")
            )
        except ValueError:
            self.grid_dynamic_spacing_recalc_minutes = 30

        try:
            self.grid_spacing_volatility_multiplier: float = float(
                os.getenv("GRID_SPACING_VOLATILITY_MULTIPLIER", "1.2")
            )
        except ValueError:
            self.grid_spacing_volatility_multiplier = 1.2

        # Safety thresholds
        try:
            self.grid_emergency_drift_threshold: float = float(
                os.getenv("GRID_EMERGENCY_DRIFT_THRESHOLD", "3.0")
            )
        except ValueError:
            self.grid_emergency_drift_threshold = 3.0

        try:
            self.default_leverage: int = int(os.getenv("DEFAULT_LEVERAGE", "10"))
        except ValueError:
            raise ValueError("DEFAULT_LEVERAGE must be a valid integer")

        # Parse float values with error handling
        try:
            self.max_risk_per_trade: float = float(
                os.getenv("MAX_RISK_PER_TRADE", "0.02")
            )
        except ValueError:
            raise ValueError("MAX_RISK_PER_TRADE must be a valid float")

        try:
            self.circuit_breaker_loss_pct: float = float(
                os.getenv("CIRCUIT_BREAKER_LOSS_PCT", "0.10")
            )
        except ValueError:
            raise ValueError("CIRCUIT_BREAKER_LOSS_PCT must be a valid float")

        self.log_level: str = os.getenv("LOG_LEVEL", "INFO")

        # ---- API server binding and access control (live-readiness T1) ----
        # The control interface can start real orders, so it binds to
        # loopback unless API_HOST is set deliberately, and a non-loopback
        # bind refuses to start without API_TOKEN (see config_validation).
        self.api_host: str = os.getenv("API_HOST", "127.0.0.1").strip() or "127.0.0.1"
        try:
            self.api_port: int = int(os.getenv("API_PORT", "8000"))
        except ValueError:
            self.api_port = 8000
        self.api_token: Optional[str] = os.getenv("API_TOKEN", "").strip() or None
        self.enable_debug_routes: bool = os.getenv(
            "ENABLE_DEBUG_ROUTES", "false"
        ).lower() in ("true", "1", "yes")

        # Backtesting
        self.backtest_start_date: str = os.getenv("BACKTEST_START_DATE", "2024-01-01")
        self.backtest_end_date: str = os.getenv("BACKTEST_END_DATE", "2024-12-31")
        self.backtest_symbol: str = os.getenv("BACKTEST_SYMBOL", "SUI-USDC")
        self.backtest_initial_capital: float = float(os.getenv("BACKTEST_INITIAL_CAPITAL", "10000.0"))
        self.backtest_slippage_pct: float = float(os.getenv("BACKTEST_SLIPPAGE_PCT", "0.002"))
        self.backtest_taker_fee_pct: float = float(os.getenv("BACKTEST_TAKER_FEE_PCT", "0.0006"))
        self.backtest_maker_fee_pct: float = float(os.getenv("BACKTEST_MAKER_FEE_PCT", "0.0002"))
        self.backtest_funding_hourly_pct: float = float(os.getenv("BACKTEST_FUNDING_HOURLY_PCT", "0.0001"))
        self.backtest_data_dir: str = os.getenv("BACKTEST_DATA_DIR", "trading_bot_v2/backtesting/data")
        self.backtest_walk_forward_train_months: int = int(os.getenv("BACKTEST_WALK_FORWARD_TRAIN_MONTHS", "6"))
        self.backtest_walk_forward_test_months: int = int(os.getenv("BACKTEST_WALK_FORWARD_TEST_MONTHS", "1"))
        # Hedge mode: False = Pacifica (no opposing positions, only SL/TP closes)
        self.backtest_hedge_mode: bool = os.getenv("BACKTEST_HEDGE_MODE", "false").lower() in ("true", "1", "yes")
        # Min candles a position must be held before an opposing signal can close it (hedge_mode=True only)
        self.backtest_min_hold_candles: int = int(os.getenv("BACKTEST_MIN_HOLD_CANDLES", "6"))
        # Single-strategy filter: empty = all strategies, "MomentumScalping" = only that one
        self.backtest_strategy: str = os.getenv("BACKTEST_STRATEGY", "")
        # Candles of rolling history handed to strategies per timeframe.
        # Must exceed the longest indicator lookback in play (e.g. a 200-period
        # slow MA needs 201) or that strategy silently generates nothing.
        self.backtest_history_lookback: int = int(
            os.getenv("BACKTEST_HISTORY_LOOKBACK", "60")
        )
        # Candles loaded before the window start so the first replayed bar
        # already has a full history slice. 0 = mirror the history lookback.
        self.backtest_warmup_candles: int = int(
            os.getenv("BACKTEST_WARMUP_CANDLES", "0")
        )

        # ---- Position reconciliation (H4) ----
        try:
            self.reconciliation_interval_seconds: int = int(
                os.getenv("RECONCILIATION_INTERVAL_SECONDS", "3600")
            )
        except ValueError:
            self.reconciliation_interval_seconds = 3600

        # ---- Venue-side stop protection (live-readiness audit T4) ----
        # What to do when a just-filled entry cannot get a venue stop
        # installed after the one retry: "close" (default; flatten it
        # reduce-only - safe for unattended live) or "local" (keep it,
        # mark venue_stop_state=missing, rely on the loop check).
        policy = os.getenv("VENUE_STOP_FAILURE_POLICY", "close").strip().lower()
        if policy not in ("close", "local"):
            logging.getLogger(__name__).warning(
                "VENUE_STOP_FAILURE_POLICY=%r is not close|local; using close",
                policy,
            )
            policy = "close"
        self.venue_stop_failure_policy: str = policy
        self.venue_stop_min_move_pct: float = _env_float(
            "VENUE_STOP_MIN_MOVE_PCT", 0.1, minimum=0.0
        )
        self.venue_stop_max_amend_failures: int = _env_int(
            "VENUE_STOP_MAX_AMEND_FAILURES", 5, minimum=1
        )
        # Previously read in-module by migrated_position_manager.py and
        # trading_bot.py; those modules keep their os.getenv fallback.
        self.migrated_close_max_attempts: int = _env_int(
            "MIGRATED_CLOSE_MAX_ATTEMPTS", 5, minimum=1
        )
        self.entry_fill_max_lookups: int = _env_int(
            "ENTRY_FILL_MAX_LOOKUPS", 10, minimum=1
        )

        # ---- Automated database backup (H5) ----
        self.backup_enabled: bool = os.getenv("BACKUP_ENABLED", "true").lower() in (
            "true",
            "1",
            "yes",
        )
        try:
            self.backup_interval_hours: float = float(
                os.getenv("BACKUP_INTERVAL_HOURS", "24")
            )
        except ValueError:
            self.backup_interval_hours = 24.0
        self.backup_dir: str = os.getenv("BACKUP_DIR", "./backups")
        try:
            self.backup_retention_days: int = int(
                os.getenv("BACKUP_RETENTION_DAYS", "30")
            )
        except ValueError:
            self.backup_retention_days = 30

    @property
    def pacifica_base_url(self) -> str:
        """Return the base URL for Pacifica API based on testnet/mainnet setting."""
        if self.testnet:
            return "https://testnet.api.pacifica.network"
        else:
            return "https://api.pacifica.network"

    @property
    def pg_connection_string(self) -> str:
        """Return PostgreSQL connection string."""
        parts = [
            f"host={self.pg_host}",
            f"port={self.pg_port}",
            f"dbname={self.pg_database}",
            f"user={self.pg_user}",
        ]
        if self.pg_password:
            parts.append(f"password={self.pg_password}")
        return " ".join(parts)

    def validate(self) -> None:
        """Validate that required configuration values are present and valid."""
        # Validate database backend
        if self.database_backend not in ("sqlite", "postgres"):
            raise ValueError(
                f"DATABASE_BACKEND must be 'sqlite' or 'postgres', got '{self.database_backend}'"
            )

        # Validate PostgreSQL settings if backend is postgres
        if self.database_backend == "postgres":
            if not self.pg_host:
                raise ValueError("PG_HOST is required when DATABASE_BACKEND=postgres")
            if not self.pg_database:
                raise ValueError("PG_DATABASE is required when DATABASE_BACKEND=postgres")
            if not self.pg_user:
                raise ValueError("PG_USER is required when DATABASE_BACKEND=postgres")
            if self.pg_pool_min < 0:
                raise ValueError("PG_POOL_MIN must be non-negative")
            if self.pg_pool_max < 1:
                raise ValueError("PG_POOL_MAX must be at least 1")
            if self.pg_pool_min > self.pg_pool_max:
                raise ValueError("PG_POOL_MIN must be <= PG_POOL_MAX")

        if not self.pacifica_private_key:
            raise ValueError(
                "AGENT_WALLET_PRIVATE_KEY environment variable is required"
            )
        if not self.pacifica_public_key:
            raise ValueError("ACCOUNT_PUBLIC_KEY environment variable is required")

        # Validate log level
        if self.log_level not in logging.getLevelNamesMapping():
            raise ValueError(
                f"Invalid LOG_LEVEL: {self.log_level}. Must be one of: {list(logging.getLevelNamesMapping().keys())}"
            )

        # Validate bounds
        if self.max_positions <= 0:
            raise ValueError("MAX_POSITIONS must be positive")
        if self.max_positions_per_strategy <= 0:
            raise ValueError("MAX_POSITIONS_PER_STRATEGY must be positive")
        if self.max_grid_positions <= 0:
            raise ValueError("MAX_GRID_POSITIONS must be positive")
        if self.default_leverage <= 0:
            raise ValueError("DEFAULT_LEVERAGE must be positive")
        if not (0 < self.max_risk_per_trade <= 1):
            raise ValueError("MAX_RISK_PER_TRADE must be between 0 and 1")
        if not (0 < self.circuit_breaker_loss_pct <= 1):
            raise ValueError("CIRCUIT_BREAKER_LOSS_PCT must be between 0 and 1")


# Global config instance
config = Config()
