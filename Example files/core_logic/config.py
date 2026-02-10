"""
Configuration system for the trading bot.
All parameters based on Trading Bot Custom Instructions and related documents.
Uses Pydantic for validation and type safety.

⚠️ CRITICAL: Includes Pacifica.fi-specific configuration for hourly funding (24x per day)
"""

from enum import Enum
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
import os
from pathlib import Path


class TradingMode(str, Enum):
    LIVE = "live"
    PAPER = "paper"
    BACKTEST = "backtest"


class MarketState(str, Enum):
    TREND = "trend"
    RANGE = "range"
    TRANSITION = "transition"
    UNKNOWN = "unknown"


class AssetClass(str, Enum):
    BTC_ETH = "btc_eth"
    LARGE_CAP = "large_cap"
    SMALL_CAP = "small_cap"
    CRYPTO = "crypto"
    PERPETUAL = "perpetual"
    STOCK = "stock"
    FOREX = "forex"
    COMMODITY = "commodity"


class AssetType(str, Enum):
    BTC = "BTC"
    ETH = "ETH"
    SOL = "SOL"
    ADA = "ADA"
    DOT = "DOT"
    LINK = "LINK"
    UNI = "UNI"
    AAVE = "AAVE"
    # Add more crypto assets as needed

    # Stock symbols
    AAPL = "AAPL"
    MSFT = "MSFT"
    GOOGL = "GOOGL"
    AMZN = "AMZN"
    TSLA = "TSLA"
    NVDA = "NVDA"

    # Forex pairs
    EURUSD = "EURUSD"
    GBPUSD = "GBPUSD"
    USDJPY = "USDJPY"
    AUDUSD = "AUDUSD"
    USDCAD = "USDCAD"

    # Commodities
    GOLD = "XAUUSD"
    SILVER = "XAGUSD"
    OIL = "WTI"


class TradeQuality(str, Enum):
    STANDARD = "standard"
    HIGH_CONVICTION = "high_conviction"
    EXCEPTIONAL = "exceptional"


class StrategyType(str, Enum):
    TREND_FOLLOWING = "trend_following"
    BREAKOUT = "breakout"
    LIQUIDATION_CAPTURE = "liquidation_capture"
    MEAN_REVERSION = "mean_reversion"
    MA_CROSSOVER = "ma_crossover"
    GRID_TRADING = "grid_trading"
    VWAP_SCALPING = "vwap_scalping"


class PacificaEnvironment(str, Enum):
    """Pacifica.fi environment selection with full API URLs."""
    TESTNET = "https://test-api.pacifica.fi/api/v1"
    MAINNET = "https://api.pacifica.fi/api/v1"


class PacificaMarginMode(str, Enum):
    """Pacifica margin mode (cannot switch with open positions)."""
    CROSS = "cross"
    ISOLATED = "isolated"


class PacificaRateLimitTier(str, Enum):
    """Pacifica rate limit tiers based on trading volume."""
    BASIC = "basic"
    ADVANCED = "advanced"
    VIP = "vip"


class TradingStrategy(str, Enum):
    """Trading strategy types for subaccount assignment."""
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"
    SCALPING = "scalping"
    SWING = "swing"
    DAY_TRADE = "day_trade"
    GRID = "grid"
    DCA = "dca"


class AccountConfig(BaseModel):
    """Account configuration parameters."""

    account_id: str = Field("default", description="Unique account identifier")
    account_name: str = Field("Default Account", description="Human-readable account name")
    initial_balance: float = Field(
        1000.0, description="Initial account balance (will be updated from connected account)"
    )
    currency: str = Field("USDC", description="Account currency")
    mode: TradingMode = Field(TradingMode.PAPER, description="Trading mode")
    max_concurrent_positions: int = Field(4, description="Maximum concurrent positions")
    max_total_margin_allocation: float = Field(
        0.20, description="Max total margin as % of account (20% risk limit)"
    )

    @field_validator("initial_balance")
    @classmethod
    def validate_balance(cls, v):
        if v <= 0:
            raise ValueError("Initial balance must be positive")
        return v

    @field_validator("max_total_margin_allocation")
    @classmethod
    def validate_margin_pct(cls, v):
        if not 0 < v <= 1:
            raise ValueError("Max margin allocation must be between 0 and 1")
        return v


class PositionSizingConfig(BaseModel):
    """Position sizing configuration."""

    min_margin_allocation: float = Field(100.0, description="$100 minimum margin")
    standard_margin_allocation: float = Field(100.0, description="$100 standard (10%)")
    high_conviction_margin: float = Field(
        150.0, description="$150 high conviction (15%)"
    )
    exceptional_margin: float = Field(200.0, description="$200 exceptional (20%)")
    max_margin_per_trade: float = Field(200.0, description="Never exceed 20%")
    default_leverage: int = Field(15, description="15x default leverage")
    min_leverage: int = Field(10, description="10x minimum")
    max_leverage: int = Field(20, description="20x maximum")

    @field_validator("default_leverage", "min_leverage", "max_leverage")
    @classmethod
    def validate_leverage(cls, v):
        if not 1 <= v <= 50:
            raise ValueError("Leverage must be between 1 and 50")
        return v

    @model_validator(mode="after")
    def validate_ranges(self):
        if not self.min_leverage <= self.default_leverage <= self.max_leverage:
            raise ValueError("Default leverage must be between min and max")
        return self


class StopLossConfig(BaseModel):
    """Stop-loss configuration by asset class."""

    btc_eth: Dict[str, float] = Field(
        {
            "min_stop_pct": 0.025,  # 2.5%
            "optimal_min": 0.03,  # 3%
            "optimal_max": 0.04,  # 4%
            "max_stop_pct": 0.05,  # 5%
        }
    )
    large_cap_alts: Dict[str, float] = Field(
        {
            "min_stop_pct": 0.03,  # 3%
            "optimal_min": 0.04,  # 4%
            "optimal_max": 0.05,  # 5%
            "max_stop_pct": 0.06,  # 6%
        }
    )
    small_cap_meme: Dict[str, float] = Field(
        {
            "min_stop_pct": 0.04,  # 4%
            "optimal_min": 0.05,  # 5%
            "optimal_max": 0.06,  # 6%
            "max_stop_pct": 0.07,  # 7%
        }
    )
    buffer_beyond_level: float = Field(
        0.005, description="0.5-1% beyond technical level"
    )
    liquidation_buffer_multiplier: float = Field(
        1.5, description="Min 1.5x stop distance"
    )
    atr_conservative_multiplier: float = Field(2.0, description="2x ATR")
    atr_aggressive_multiplier: float = Field(1.5, description="1.5x ATR")


class RRRConfig(BaseModel):
    """Reward-to-Risk Ratio configuration."""

    minimum_rrr: float = Field(2.0, description="Minimum 2:1")
    target_rrr: float = Field(3.0, description="Target 3:1")
    liquidation_event_min: float = Field(3.0, description="3:1 for liquidation events")

    @field_validator("minimum_rrr", "target_rrr", "liquidation_event_min")
    @classmethod
    def validate_rrr(cls, v):
        if v < 1:
            raise ValueError("RRR must be at least 1:1")
        return v


class TimeStopConfig(BaseModel):
    """Time-stop configuration."""

    min_chart: Dict[str, Any] = Field({"hours": 2, "min_movement_pct": 0.005})  # 0.5%
    swing_trades: Dict[str, Any] = Field({"hours": 4, "min_movement_pct": 0.01})  # 1%


class AssetConfig(BaseModel):
    """Configuration for a specific asset."""

    symbol: str
    asset_class: AssetClass
    min_position_size: float
    max_position_size: float
    leverage_limit: float
    trading_hours: Optional[Dict[str, str]] = None  # For stocks/forex with market hours


class MarketConfig(BaseModel):
    """Market and timeframe configuration."""

    primary_market: str = Field("crypto_perpetuals", description="Primary market")
    exchange: str = Field("pacifica_fi", description="Solana-based DEX")
    primary_timeframe: str = Field("15min", description="Primary trading timeframe")

    # Multi-asset support
    supported_assets: Dict[AssetClass, List[AssetConfig]] = Field(
        {
            AssetClass.CRYPTO: [
                AssetConfig(
                    symbol="BTC",
                    asset_class=AssetClass.CRYPTO,
                    min_position_size=0.001,
                    max_position_size=1.0,
                    leverage_limit=10.0,
                ),
                AssetConfig(
                    symbol="ETH",
                    asset_class=AssetClass.CRYPTO,
                    min_position_size=0.01,
                    max_position_size=10.0,
                    leverage_limit=10.0,
                ),
                AssetConfig(
                    symbol="SOL",
                    asset_class=AssetClass.CRYPTO,
                    min_position_size=0.1,
                    max_position_size=100.0,
                    leverage_limit=5.0,
                ),
                AssetConfig(
                    symbol="ADA",
                    asset_class=AssetClass.CRYPTO,
                    min_position_size=10.0,
                    max_position_size=10000.0,
                    leverage_limit=5.0,
                ),
            ],
            AssetClass.STOCK: [
                AssetConfig(
                    symbol="AAPL",
                    asset_class=AssetClass.STOCK,
                    min_position_size=1.0,
                    max_position_size=1000.0,
                    leverage_limit=4.0,
                    trading_hours={
                        "start": "09:30",
                        "end": "16:00",
                        "timezone": "America/New_York",
                    },
                ),
                AssetConfig(
                    symbol="MSFT",
                    asset_class=AssetClass.STOCK,
                    min_position_size=1.0,
                    max_position_size=1000.0,
                    leverage_limit=4.0,
                    trading_hours={
                        "start": "09:30",
                        "end": "16:00",
                        "timezone": "America/New_York",
                    },
                ),
            ],
            AssetClass.FOREX: [
                AssetConfig(
                    symbol="EURUSD",
                    asset_class=AssetClass.FOREX,
                    min_position_size=1000.0,
                    max_position_size=100000.0,
                    leverage_limit=50.0,
                ),
                AssetConfig(
                    symbol="GBPUSD",
                    asset_class=AssetClass.FOREX,
                    min_position_size=1000.0,
                    max_position_size=100000.0,
                    leverage_limit=50.0,
                ),
            ],
        },
        description="Supported assets by class",
    )

    timeframe_3d: Dict[str, List[str]] = Field(
        {
            "long_term": ["1d", "4h"],  # Context timeframes
            "trade_setup": ["15min", "1h"],  # Primary trading timeframe
            "entry_precision": ["5min"],  # Entry timing
        }
    )

    # Asset class specific timeframes
    asset_class_timeframes: Dict[AssetClass, Dict[str, List[str]]] = Field(
        {
            AssetClass.CRYPTO: {
                "long_term": ["1d", "4h"],
                "trade_setup": ["15min", "1h"],
                "entry_precision": ["5min"],
            },
            AssetClass.STOCK: {
                "long_term": ["1d", "1w"],
                "trade_setup": ["1h", "4h"],
                "entry_precision": ["15min", "30min"],
            },
            AssetClass.FOREX: {
                "long_term": ["4h", "1d"],
                "trade_setup": ["1h", "30min"],
                "entry_precision": ["15min", "5min"],
            },
        }
    )


class StrategyConfig(BaseModel):
    """Strategy allocation and parameters."""

    trend_following: Dict[str, Any] = Field(
        {
            "allocation": 0.75,  # 75% of trades
            "min_trend_swings": 3,
            "pullback_range": (0.02, 0.04),  # 2-4%
            "rsi_oversold": 30,
            "rsi_overbought": 70,
        }
    )
    breakout: Dict[str, Any] = Field(
        {
            "allocation": 0.20,  # 20% of trades
            "min_pattern_hours": 4,
            "volume_multiplier": 1.5,  # 1.5-2x average
            "min_touches": 5,
        }
    )
    liquidation_capture: Dict[str, Any] = Field(
        {
            "allocation": 0.05,  # 5% of trades
            "price_move_threshold": 0.03,  # 3% in 15min
            "time_threshold_minutes": 15,
            "volume_spike_multiplier": 3.0,  # 3-5x average
            "rsi_extreme_long": 15,
            "rsi_extreme_short": 85,
            "std_dev_threshold": 3.0,
            "max_per_session": 1,
            "min_hours_between": 4,
        }
    )

    @model_validator(mode="after")
    def validate_allocations(self):
        total = sum(
            v.get("allocation", 0)
            for v in [self.trend_following, self.breakout, self.liquidation_capture]
        )
        if not abs(total - 1.0) < 0.01:
            raise ValueError("Strategy allocations must sum to 1.0")
        return self


class RiskLimitsConfig(BaseModel):
    """Risk management limits."""

    max_account_risk_per_trade: float = Field(0.08, description="8% max per trade")
    max_margin_drawdown: float = Field(0.80, description="80% max margin drawdown")
    daily_loss_limit: float = Field(200.0, description="$200 (20%) daily loss limit")
    max_drawdown_from_peak: float = Field(0.25, description="25% circuit breaker")
    consecutive_loss_limit: int = Field(3, description="Max consecutive losses")
    min_win_rate_threshold: float = Field(0.30, description="30% over 20 trades")


class CircuitBreakersConfig(BaseModel):
    """Circuit breaker thresholds for emergency stops."""

    consecutive_losses: Dict[str, Any] = Field(
        {"threshold": 3, "action": "break_4h_reduce_50pct"},
        description="Consecutive loss circuit breaker",
    )
    drawdown: Dict[str, Any] = Field(
        {"threshold": 0.25, "action": "cease_immediate"},
        description="Drawdown circuit breaker (25%)",
    )
    win_rate_collapse: Dict[str, Any] = Field(
        {"threshold": 0.30, "action": "stop_intensive_review"},
        description="Win rate collapse circuit breaker (30%)",
    )


class VolumeConfig(BaseModel):
    """Volume and liquidity requirements."""

    min_volume_multiplier: float = Field(
        0.7, description="Don't trade if <0.7x average"
    )
    breakout_volume_min: float = Field(1.5, description="1.5-2x for breakouts")
    confirmation_volume_min: float = Field(1.5, description="1.5x for confirmations")
    liquidation_volume_min: float = Field(3.0, description="3x+ for liquidation events")
    volume_period: int = Field(20, description="20-period average")


class IndicatorsConfig(BaseModel):
    """Technical indicators configuration."""

    moving_averages: List[int] = Field([20, 50, 200], description="MA periods")
    rsi_period: int = Field(14, description="RSI period")
    rsi_overbought: int = Field(70, description="RSI overbought level")
    rsi_oversold: int = Field(30, description="RSI oversold level")
    atr_period: int = Field(14, description="ATR period")
    bollinger_period: int = Field(20, description="Bollinger period")
    bollinger_std_dev: int = Field(2, description="Bollinger standard deviations")
    volume_ma_period: int = Field(20, description="Volume MA period")
    macd_fast: int = Field(12, description="MACD fast period")
    macd_slow: int = Field(26, description="MACD slow period")
    macd_signal: int = Field(9, description="MACD signal period")


class SubaccountConfig(BaseModel):
    """
    Configuration for a single Pacifica subaccount.

    Subaccounts allow isolating funds and risk for different trading strategies.
    """

    subaccount_id: str = Field(
        ...,
        description="Internal Pacifica subaccount ID (e.g., 'sub_12345')"
    )
    subaccount_name: str = Field(
        "Unnamed Subaccount",
        description="Human-readable name for the subaccount"
    )
    subaccount_public_key: Optional[str] = Field(
        None,
        description="Solana public key associated with subaccount (for reference)"
    )

    # Strategy Assignment
    trading_strategy: TradingStrategy = Field(
        TradingStrategy.BALANCED,
        description="Trading strategy assigned to this subaccount"
    )

    # Risk Parameters (per subaccount)
    max_position_size: float = Field(
        10000.0,
        ge=0,
        description="Maximum position size in USD for this subaccount"
    )
    risk_per_trade: float = Field(
        0.02,
        ge=0,
        le=1,
        description="Risk per trade as fraction of subaccount balance (e.g., 0.02 = 2%)"
    )
    max_leverage: int = Field(
        20,
        ge=5,
        le=50,
        description="Maximum leverage for this subaccount (5-50x)"
    )

    # Status
    enabled: bool = Field(
        True,
        description="Whether trading is enabled for this subaccount"
    )

    # Metadata
    created_at: Optional[str] = Field(
        None,
        description="Creation timestamp"
    )
    updated_at: Optional[str] = Field(
        None,
        description="Last update timestamp"
    )

    model_config = ConfigDict(extra="allow")


class PacificaConfig(BaseModel):
    """
    Pacifica.fi exchange-specific configuration.

    ⚠️ CRITICAL: Pacifica uses HOURLY funding rates (24x per day)
    """

    # Environment
    environment: PacificaEnvironment = Field(
        PacificaEnvironment.TESTNET,
        description="Pacifica environment (testnet/mainnet)"
    )

    # Authentication (Main Account - used for ALL subaccounts)
    agent_wallet_private_key: Optional[str] = Field(
        None,
        description="Agent wallet private key (load from env) - authenticates ALL subaccounts"
    )
    account_public_key: Optional[str] = Field(
        None,
        description="Main account public key (load from env)"
    )

    # Subaccount Management
    subaccounts: List[SubaccountConfig] = Field(
        default_factory=list,
        description="List of configured subaccounts"
    )
    default_subaccount_id: Optional[str] = Field(
        None,
        description="Default subaccount ID for trading operations"
    )
    enable_multi_subaccount: bool = Field(
        False,
        description="Enable multi-subaccount support"
    )

    # API Endpoints
    rest_url_mainnet: str = Field(
        "https://api.pacifica.fi/api/v1",
        description="Mainnet REST API endpoint"
    )
    rest_url_testnet: str = Field(
        "https://test-api.pacifica.fi/api/v1",
        description="Testnet REST API endpoint"
    )
    websocket_url_mainnet: str = Field(
        "wss://ws.pacifica.fi/ws",
        description="Mainnet WebSocket endpoint"
    )
    websocket_url_testnet: str = Field(
        "wss://test-ws.pacifica.fi/ws",
        description="Testnet WebSocket endpoint"
    )

    # Rate Limiting
    rate_limit_tier: PacificaRateLimitTier = Field(
        PacificaRateLimitTier.BASIC,
        description="Rate limit tier (Basic: 10/s, Advanced: 20/s, VIP: 50/s)"
    )
    rate_limit_buffer: float = Field(
        0.8,
        description="Use 80% of rate limit for safety"
    )

    # Margin & Leverage
    margin_mode: PacificaMarginMode = Field(
        PacificaMarginMode.CROSS,
        description="Margin mode (cross or isolated) - cannot switch with open positions"
    )
    min_leverage: int = Field(5, description="Minimum leverage on Pacifica")
    max_leverage: int = Field(50, description="Maximum leverage on Pacifica")
    default_leverage: int = Field(10, description="Default leverage for Pacifica")

    # Funding Rates (HOURLY - 24x per day)
    funding_rate_update_interval: int = Field(
        5,
        description="Funding rates update every 5 seconds"
    )
    funding_payment_interval: int = Field(
        3600,
        description="Funding payments occur EVERY HOUR (3600 seconds)"
    )
    funding_payments_per_day: int = Field(
        24,
        description="24 funding payments per day (CRITICAL)"
    )
    max_funding_rate_per_hour: float = Field(
        0.04,
        description="Maximum funding rate: ±4% per hour"
    )
    funding_rate_clamp: float = Field(
        0.0005,
        description="Funding rate clamp: ±0.05% in formula"
    )

    # Market Specifications (BTC-PERP defaults)
    default_tick_size: float = Field(0.5, description="Default tick size ($0.50)")
    default_lot_size: float = Field(0.001, description="Default lot size (0.001 BTC)")
    default_min_order_size: float = Field(0.001, description="Minimum order size")
    default_max_order_size: float = Field(100.0, description="Maximum order size")

    # Order Configuration
    default_time_in_force: str = Field("GTC", description="Good-Til-Cancel")
    signature_expiry_window: int = Field(
        5000,
        description="Signature expiry window (5 seconds in ms)"
    )

    # WebSocket
    websocket_timeout: int = Field(60, description="WebSocket timeout in seconds")
    websocket_max_duration: int = Field(
        86400,
        description="Max WebSocket duration (24 hours)"
    )
    websocket_heartbeat_interval: int = Field(
        30,
        description="Heartbeat interval in seconds"
    )
    websocket_reconnect_delay: int = Field(
        5,
        description="Reconnect delay in seconds"
    )
    websocket_max_reconnect_attempts: int = Field(
        10,
        description="Maximum reconnect attempts"
    )

    # Funding Tracking
    enable_funding_tracker: bool = Field(
        True,
        description="Enable automatic hourly funding tracker"
    )
    funding_tracker_start_delay: int = Field(
        60,
        description="Delay before starting funding tracker (seconds)"
    )
    save_funding_history: bool = Field(
        True,
        description="Save funding rate history to database"
    )
    funding_warning_threshold: float = Field(
        0.10,
        description="Warn if funding consumes >10% of margin"
    )

    @field_validator("default_leverage")
    @classmethod
    def validate_pacifica_leverage(cls, v, info):
        """Validate leverage is within Pacifica's 5-50x range."""
        min_lev = info.data.get("min_leverage", 5)
        max_lev = info.data.get("max_leverage", 50)
        if not min_lev <= v <= max_lev:
            raise ValueError(f"Default leverage must be between {min_lev}x and {max_lev}x")
        return v

    @property
    def rest_url(self) -> str:
        """Get REST URL based on environment."""
        return (
            self.rest_url_mainnet
            if self.environment == PacificaEnvironment.MAINNET
            else self.rest_url_testnet
        )

    @property
    def websocket_url(self) -> str:
        """Get WebSocket URL based on environment."""
        return (
            self.websocket_url_mainnet
            if self.environment == PacificaEnvironment.MAINNET
            else self.websocket_url_testnet
        )

    @property
    def rate_limit_per_second(self) -> int:
        """Get rate limit based on tier."""
        limits = {
            PacificaRateLimitTier.BASIC: 10,
            PacificaRateLimitTier.ADVANCED: 20,
            PacificaRateLimitTier.VIP: 50,
        }
        return limits.get(self.rate_limit_tier, 10)

    @property
    def effective_rate_limit(self) -> float:
        """Get effective rate limit with buffer applied."""
        return self.rate_limit_per_second * self.rate_limit_buffer

    # Subaccount Helper Methods

    def get_subaccount(self, subaccount_id: str) -> Optional[SubaccountConfig]:
        """
        Get subaccount configuration by ID.

        Args:
            subaccount_id: Subaccount ID to lookup

        Returns:
            SubaccountConfig or None if not found
        """
        for subaccount in self.subaccounts:
            if subaccount.subaccount_id == subaccount_id:
                return subaccount
        return None

    def get_default_subaccount(self) -> Optional[SubaccountConfig]:
        """
        Get the default subaccount configuration.

        Returns:
            SubaccountConfig or None if no default set
        """
        if not self.default_subaccount_id:
            return None
        return self.get_subaccount(self.default_subaccount_id)

    def get_active_subaccounts(self) -> List[SubaccountConfig]:
        """
        Get all enabled subaccounts.

        Returns:
            List of enabled SubaccountConfig objects
        """
        return [sub for sub in self.subaccounts if sub.enabled]

    def get_subaccounts_by_strategy(self, strategy: TradingStrategy) -> List[SubaccountConfig]:
        """
        Get all subaccounts assigned to a specific strategy.

        Args:
            strategy: Trading strategy to filter by

        Returns:
            List of SubaccountConfig objects with matching strategy
        """
        return [
            sub for sub in self.subaccounts
            if sub.trading_strategy == strategy and sub.enabled
        ]

    def add_subaccount(self, subaccount: SubaccountConfig) -> bool:
        """
        Add a new subaccount to configuration.

        Args:
            subaccount: SubaccountConfig to add

        Returns:
            True if added, False if already exists
        """
        if self.get_subaccount(subaccount.subaccount_id):
            return False
        self.subaccounts.append(subaccount)
        return True

    def remove_subaccount(self, subaccount_id: str) -> bool:
        """
        Remove a subaccount from configuration.

        Args:
            subaccount_id: Subaccount ID to remove

        Returns:
            True if removed, False if not found
        """
        for i, sub in enumerate(self.subaccounts):
            if sub.subaccount_id == subaccount_id:
                del self.subaccounts[i]
                if self.default_subaccount_id == subaccount_id:
                    self.default_subaccount_id = None
                return True
        return False


class APIConfig(BaseModel):
    """Legacy API configuration (kept for backwards compatibility)."""

    rest_endpoint: str = Field(
        "https://api.pacifica.fi/api/v1", description="REST API endpoint"
    )
    websocket_endpoint: str = Field(
        "wss://ws.pacifica.fi/ws", description="WebSocket endpoint"
    )
    testnet_endpoint: str = Field(
        "https://test-api.pacifica.fi/api/v1", description="Testnet endpoint"
    )
    rate_limit_buffer: float = Field(0.8, description="Use 80% of rate limit")
    websocket_timeout: int = Field(60, description="WebSocket timeout in seconds")
    websocket_max_duration: int = Field(
        86400, description="Max WebSocket duration (24h)"
    )
    heartbeat_interval: int = Field(30, description="Heartbeat interval in seconds")


class LoggingConfig(BaseModel):
    """Logging configuration."""

    log_level: str = Field("INFO", description="Logging level")
    trade_journal_path: str = Field(
        "logs/trades.csv", description="Trade journal file path"
    )
    error_log_path: str = Field("logs/errors.log", description="Error log file path")
    debug_log_path: str = Field("logs/debug.log", description="Debug log file path")
    performance_log_path: str = Field(
        "logs/performance.log", description="Performance log file path"
    )
    max_log_size: int = Field(
        10 * 1024 * 1024, description="Max log file size in bytes"
    )
    backup_count: int = Field(5, description="Number of backup log files")


class DefaultAccountsConfig(BaseModel):
    """Default account configurations."""

    sub_1: AccountConfig = Field(
        default_factory=lambda: AccountConfig(
            account_id="sub_1",
            account_name="sub 1",
            initial_balance=10000.0,
            currency="USDC",
            mode=TradingMode.PAPER,
            max_concurrent_positions=5,
            max_total_margin_allocation=0.02,  # Conservative 2% risk per trade
        ),
        description="Default sub account configuration"
    )


class BotConfig(BaseModel):
    """Main bot configuration combining all sections."""

    account: AccountConfig
    default_accounts: DefaultAccountsConfig
    position_sizing: PositionSizingConfig
    stop_loss: StopLossConfig
    rrr: RRRConfig
    time_stop: TimeStopConfig
    market: MarketConfig
    strategy: StrategyConfig
    risk_limits: RiskLimitsConfig
    circuit_breakers: CircuitBreakersConfig
    volume: VolumeConfig
    indicators: IndicatorsConfig
    api: APIConfig
    pacifica: PacificaConfig  # ⚠️ NEW: Pacifica-specific configuration
    logging: LoggingConfig

    model_config = ConfigDict(validate_assignment=True)


def load_config_from_env() -> Dict[str, Any]:
    """Load configuration overrides from environment variables."""
    env_config = {}

    # Pacifica authentication from environment
    agent_wallet_key = os.getenv("AGENT_WALLET_PRIVATE_KEY")
    account_pubkey = os.getenv("ACCOUNT_PUBLIC_KEY")

    if agent_wallet_key or account_pubkey:
        env_config["pacifica"] = {}
        if agent_wallet_key:
            env_config["pacifica"]["agent_wallet_private_key"] = agent_wallet_key
        if account_pubkey:
            env_config["pacifica"]["account_public_key"] = account_pubkey

    # Pacifica environment (testnet/mainnet)
    pacifica_env = os.getenv("PACIFICA_ENVIRONMENT", "").lower()
    if pacifica_env in ["mainnet", "testnet"]:
        if "pacifica" not in env_config:
            env_config["pacifica"] = {}
        env_config["pacifica"]["environment"] = pacifica_env

    # Rate limit tier
    rate_limit_tier = os.getenv("PACIFICA_RATE_LIMIT_TIER", "").lower()
    if rate_limit_tier in ["basic", "advanced", "vip"]:
        if "pacifica" not in env_config:
            env_config["pacifica"] = {}
        env_config["pacifica"]["rate_limit_tier"] = rate_limit_tier

    # Margin mode
    margin_mode = os.getenv("PACIFICA_MARGIN_MODE", "").lower()
    if margin_mode in ["cross", "isolated"]:
        if "pacifica" not in env_config:
            env_config["pacifica"] = {}
        env_config["pacifica"]["margin_mode"] = margin_mode

    return env_config


def create_default_config() -> BotConfig:
    """Create default configuration from documents."""
    return BotConfig(
        account=AccountConfig(),
        default_accounts=DefaultAccountsConfig(),
        position_sizing=PositionSizingConfig(),
        stop_loss=StopLossConfig(),
        rrr=RRRConfig(),
        time_stop=TimeStopConfig(),
        market=MarketConfig(),
        strategy=StrategyConfig(),
        risk_limits=RiskLimitsConfig(),
        circuit_breakers=CircuitBreakersConfig(),
        volume=VolumeConfig(),
        indicators=IndicatorsConfig(),
        api=APIConfig(),
        pacifica=PacificaConfig(),  # ⚠️ NEW: Pacifica configuration
        logging=LoggingConfig(),
    )


def load_config(config_path: Optional[Path] = None) -> BotConfig:
    """
    Load configuration from file and environment.

    Args:
        config_path: Path to YAML/JSON config file

    Returns:
        Validated BotConfig instance

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValidationError: If config is invalid
    """
    config = create_default_config()

    if config_path and config_path.exists():
        import yaml

        with open(config_path, "r") as f:
            file_config = yaml.safe_load(f)
        config = config.parse_obj(file_config)

    # Apply environment overrides
    env_overrides = load_config_from_env()
    for key, value in env_overrides.items():
        if hasattr(config, key):
            current_attr = getattr(config, key)
            # If value is a dict and current attribute is a BaseModel, update fields
            if isinstance(value, dict) and isinstance(current_attr, BaseModel):
                for field_key, field_value in value.items():
                    if hasattr(current_attr, field_key):
                        setattr(current_attr, field_key, field_value)
            else:
                setattr(config, key, value)

    return config


# Global config instance
_config: Optional[BotConfig] = None


def get_config() -> BotConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        config_path = Path(os.getenv("BOT_CONFIG_PATH", "config.yaml"))
        _config = load_config(config_path)
    return _config


def reload_config() -> BotConfig:
    """Reload configuration from file."""
    global _config
    config_path = Path(os.getenv("BOT_CONFIG_PATH", "config.yaml"))
    _config = load_config(config_path)
    return _config


def get_account_config(account_id: str) -> Optional[AccountConfig]:
    """Get account configuration by account ID."""
    config = get_config()
    if account_id == "sub_1":
        return config.default_accounts.sub_1
    # Add more account mappings as needed
    return None


def get_default_account_config() -> AccountConfig:
    """Get the default account configuration."""
    config = get_config()
    return config.default_accounts.sub_1


def get_pacifica_config() -> PacificaConfig:
    """
    Get Pacifica.fi configuration.

    Returns:
        PacificaConfig with environment overrides applied

    Raises:
        RuntimeError: If Pacifica credentials are missing
    """
    config = get_config()
    pacifica_config = config.pacifica

    # Verify credentials are configured
    if not pacifica_config.agent_wallet_private_key:
        raise RuntimeError(
            "AGENT_WALLET_PRIVATE_KEY not configured. "
            "Set in environment or config file."
        )
    if not pacifica_config.account_public_key:
        raise RuntimeError(
            "ACCOUNT_PUBLIC_KEY not configured. "
            "Set in environment or config file."
        )

    return pacifica_config
