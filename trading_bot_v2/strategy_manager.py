"""
Strategy Manager - Multi-Strategy Orchestration & Conflict Resolution

Orchestrates multiple trading strategies with intelligent regime-based filtering:
- Mean Reversion (RANGING_CALM)
- MA Crossover (TRENDING_STRONG)
- Trend Following (TRENDING_STRONG) - placeholder
- Grid Trading (RANGING_VOLATILE) - placeholder
- Liquidation Capture (ALL regimes) - placeholder

Conflict Resolution Rules:
1. All signals same direction → Combine with weighted average
2. Opposing signals (BUY + SELL):
   - TRENDING regime: Trust higher confidence signal
   - RANGING regime: Trust mean reversion over breakout
3. Quality override: HIGH_CONVICTION always takes priority
4. Maximum 1 signal per symbol per direction
"""

import os
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from loguru import logger

from .models import Signal, OrderSide, TradeQuality
from .config import StrategyType
from .market_regime import MarketRegimeDetector, MarketRegime
from .strategies.mean_reversion import MeanReversionStrategy
from .strategies.ma_crossover import MACrossoverStrategy
from .strategies.grid_trading import GridTradingStrategy
from .strategies.liquidation_capture import LiquidationCaptureStrategy
from .strategies.vwap_scalping import VWAPScalpingStrategy
from .strategies.funding_arb import FundingArbStrategy
from .strategies.momentum_scalping import MomentumScalpingStrategy
from .strategies.orderbook_imbalance import OrderBookImbalanceStrategy


class StrategyManager:
    """
    Orchestrates multiple trading strategies with regime-based filtering.

    Manages strategy lifecycle, signal generation, and conflict resolution.
    """

    def __init__(
        self,
        regime_detector: Optional[MarketRegimeDetector] = None,
        enable_mean_reversion: Optional[bool] = None,
        enable_ma_crossover: Optional[bool] = None,
        enable_trend_following: Optional[bool] = None,
        enable_grid_trading: Optional[bool] = None,
        enable_liquidation_capture: Optional[bool] = None,
        enable_vwap_scalping: Optional[bool] = None,
        enable_funding_arb: Optional[bool] = None,
        enable_momentum_scalping: Optional[bool] = None,
        enable_orderbook_imbalance: Optional[bool] = None,
        risk_manager=None,
        client=None,  # Pacifica client for funding arb API calls
        ws_client=None,  # WebSocket client for orderbook data
    ):
        """
        Initialize StrategyManager with enabled strategies.

        Strategy enable flags are read from environment variables if not explicitly passed.
        Environment variables: ENABLE_MEAN_REVERSION, ENABLE_MA_CROSSOVER, ENABLE_GRID_TRADING,
                               ENABLE_LIQUIDATION_CAPTURE, ENABLE_TREND_FOLLOWING

        Args:
            regime_detector: MarketRegimeDetector instance (creates default if None)
            enable_mean_reversion: Enable Mean Reversion strategy (env: ENABLE_MEAN_REVERSION)
            enable_ma_crossover: Enable MA Crossover strategy (env: ENABLE_MA_CROSSOVER)
            enable_trend_following: Enable Trend Following strategy (env: ENABLE_TREND_FOLLOWING)
            enable_grid_trading: Enable Grid Trading strategy (env: ENABLE_GRID_TRADING)
            enable_liquidation_capture: Enable Liquidation Capture (env: ENABLE_LIQUIDATION_CAPTURE)
        """
        # Initialize regime detector
        self.regime_detector = regime_detector or MarketRegimeDetector()

        # Read strategy enable flags from environment if not explicitly passed
        # This ensures .env configuration is respected (fixes parameter audit issue)
        def _get_env_bool(var_name: str, default: bool) -> bool:
            """Read boolean from environment variable."""
            value = os.getenv(var_name, "").lower()
            if value in ("true", "1", "yes", "on"):
                return True
            elif value in ("false", "0", "no", "off"):
                return False
            return default

        # Use explicit parameter if passed, otherwise read from environment
        self.enable_mean_reversion = (
            enable_mean_reversion
            if enable_mean_reversion is not None
            else _get_env_bool("ENABLE_MEAN_REVERSION", True)
        )
        self.enable_ma_crossover = (
            enable_ma_crossover
            if enable_ma_crossover is not None
            else _get_env_bool("ENABLE_MA_CROSSOVER", True)
        )
        self.enable_trend_following = (
            enable_trend_following
            if enable_trend_following is not None
            else _get_env_bool("ENABLE_TREND_FOLLOWING", False)
        )
        self.enable_grid_trading = (
            enable_grid_trading
            if enable_grid_trading is not None
            else _get_env_bool(
                "ENABLE_GRID_TRADING", True
            )  # Default to True for more trades
        )
        self.enable_liquidation_capture = (
            enable_liquidation_capture
            if enable_liquidation_capture is not None
            else _get_env_bool(
                "ENABLE_LIQUIDATION_CAPTURE", True
            )  # Default to True for more trades
        )
        self.enable_vwap_scalping = (
            enable_vwap_scalping
            if enable_vwap_scalping is not None
            else _get_env_bool(
                "ENABLE_VWAP_SCALPING", True
            )  # Default to True - overlay strategy
        )
        self.enable_funding_arb = (
            enable_funding_arb
            if enable_funding_arb is not None
            else _get_env_bool(
                "ENABLE_FUNDING_ARB", False
            )  # Default to False - passive strategy
        )
        self.enable_momentum_scalping = (
            enable_momentum_scalping
            if enable_momentum_scalping is not None
            else _get_env_bool(
                "ENABLE_MOMENTUM_SCALPING", True
            )  # Default to True - fast scalping strategy
        )
        self.enable_orderbook_imbalance = (
            enable_orderbook_imbalance
            if enable_orderbook_imbalance is not None
            else _get_env_bool(
                "ENABLE_ORDERBOOK_IMBALANCE", True
            )  # Default to True - overlay strategy
        )
        self.risk_manager = risk_manager
        self.client = client  # Store client for funding arb
        self.ws_client = ws_client  # Store websocket client for orderbook

        logger.info(
            f"Strategy enable flags: MeanReversion={self.enable_mean_reversion}, "
            f"MACrossover={self.enable_ma_crossover}, GridTrading={self.enable_grid_trading}, "
            f"LiquidationCapture={self.enable_liquidation_capture}, TrendFollowing={self.enable_trend_following}, "
            f"VWAPScalping={self.enable_vwap_scalping}, FundingArb={self.enable_funding_arb}, "
            f"MomentumScalping={self.enable_momentum_scalping}, OrderBookImbalance={self.enable_orderbook_imbalance}"
        )

        # Initialize trade cooldown and feedback tracking
        self._trade_cooldowns: Dict[
            Tuple[str, str], datetime
        ] = {}  # (symbol, strategy) -> cooldown_until
        self._executed_trades: List[Dict] = []  # Track recent trades for feedback

        # Initialize strategies based on flags
        self.strategies = {}

        if self.enable_mean_reversion:
            self.strategies["MeanReversion"] = MeanReversionStrategy()
            logger.info("Mean Reversion strategy enabled")

        if self.enable_ma_crossover:
            self.strategies["MACrossover"] = MACrossoverStrategy()
            logger.info("MA Crossover strategy enabled")

        if self.enable_trend_following:
            # Placeholder for TrendFollowingStrategy
            logger.warning("Trend Following strategy not yet implemented")

        if self.enable_grid_trading:
            # Load Grid Trading parameters from environment (loosened for more trades - Jan 2026)
            grid_levels = int(
                os.getenv("GRID_TRADING_LEVELS", "8")
            )  # Was 10 - fewer levels for faster setup
            grid_spacing = float(
                os.getenv("GRID_SPACING_ATR_MULTIPLIER", "0.4")
            )  # Was 0.5 - tighter grids
            grid_max_positions = int(os.getenv("GRID_MAX_POSITIONS_PER_SYMBOL", "10"))
            grid_emergency_stop = float(os.getenv("GRID_EMERGENCY_STOP_PCT", "0.05"))
            grid_adx_threshold = float(
                os.getenv("GRID_ADX_THRESHOLD", "20.0")
            )  # Lowered from 25.0 to 20.0 for more grid signals
            grid_min_confidence = float(
                os.getenv("GRID_MIN_CONFIDENCE", "0.45")
            )  # Was 0.6 - more signals

            self.strategies["GridTrading"] = GridTradingStrategy(
                grid_levels=grid_levels,
                grid_spacing_atr_multiplier=grid_spacing,
                max_positions_per_symbol=grid_max_positions,
                emergency_stop_loss_pct=grid_emergency_stop,
                adx_regime_threshold=grid_adx_threshold,
                min_confidence=grid_min_confidence,
                risk_manager=self.risk_manager,
            )
            logger.info(
                f"Grid Trading strategy enabled: {grid_levels} levels, "
                f"spacing={grid_spacing}x ATR, max positions={grid_max_positions}"
            )

        if self.enable_liquidation_capture:
            # Load Liquidation Capture parameters from environment
            liq_price_threshold = float(
                os.getenv("LIQUIDATION_PRICE_THRESHOLD", "0.03")
            )
            liq_volume_multiplier = float(
                os.getenv("LIQUIDATION_VOLUME_MULTIPLIER", "3.0")
            )
            liq_rsi_oversold = float(os.getenv("LIQUIDATION_RSI_OVERSOLD", "15.0"))
            liq_rsi_overbought = float(os.getenv("LIQUIDATION_RSI_OVERBOUGHT", "85.0"))
            liq_min_consecutive = int(
                os.getenv("LIQUIDATION_MIN_CONSECUTIVE_MOVES", "5")
            )
            liq_min_wick_ratio = float(os.getenv("LIQUIDATION_MIN_WICK_RATIO", "2.0"))
            liq_rrr_target = float(os.getenv("LIQUIDATION_RRR_TARGET", "3.0"))
            liq_max_per_session = int(os.getenv("LIQUIDATION_MAX_PER_SESSION", "1"))
            liq_min_hours_between = int(os.getenv("LIQUIDATION_MIN_HOURS_BETWEEN", "4"))

            self.strategies["LiquidationCapture"] = LiquidationCaptureStrategy(
                price_move_threshold=liq_price_threshold,
                volume_spike_multiplier=liq_volume_multiplier,
                rsi_oversold_threshold=liq_rsi_oversold,
                rsi_overbought_threshold=liq_rsi_overbought,
                min_consecutive_moves=liq_min_consecutive,
                min_wick_ratio=liq_min_wick_ratio,
                rrr_target=liq_rrr_target,
                max_per_session=liq_max_per_session,
                min_hours_between_trades=liq_min_hours_between,
            )
            logger.info(
                f"Liquidation Capture strategy enabled: price threshold={liq_price_threshold:.1%}, "
                f"volume spike={liq_volume_multiplier}x, RRR target={liq_rrr_target}x"
            )

        if self.enable_vwap_scalping:
            # Load VWAP Scalping parameters from environment
            vwap_atr_period = int(os.getenv("VWAP_ATR_PERIOD", "14"))
            vwap_sd_threshold = float(os.getenv("VWAP_SD_ENTRY_THRESHOLD", "1.8"))
            vwap_atr_stop_mult = float(os.getenv("VWAP_ATR_STOP_MULTIPLIER", "1.5"))
            vwap_min_confidence = float(os.getenv("VWAP_MIN_CONFIDENCE", "0.62"))
            vwap_cooldown = int(os.getenv("VWAP_COOLDOWN_MINUTES", "8"))

            self.strategies["VWAPScalping"] = VWAPScalpingStrategy(
                atr_period=vwap_atr_period,
                sd_entry_threshold=vwap_sd_threshold,
                atr_stop_multiplier=vwap_atr_stop_mult,
                min_confidence=vwap_min_confidence,
                cooldown_minutes=vwap_cooldown,
            )
            logger.info(
                f"VWAP Scalping strategy enabled: SD threshold={vwap_sd_threshold}, "
                f"ATR stop={vwap_atr_stop_mult}x, min_confidence={vwap_min_confidence:.0%}"
            )

        if self.enable_funding_arb:
            # Load Funding Arb parameters from environment
            funding_min_rate = float(os.getenv("FUNDING_ARB_MIN_RATE", "0.0001"))  # 0.01% min
            funding_max_alloc = float(os.getenv("FUNDING_ARB_MAX_ALLOCATION", "0.20"))  # 20% max
            funding_rebalance = float(os.getenv("FUNDING_ARB_REBALANCE_THRESHOLD", "0.02"))  # 2%
            funding_lookback = int(os.getenv("FUNDING_ARB_LOOKBACK_HOURS", "8"))
            funding_min_confidence = float(os.getenv("FUNDING_ARB_MIN_CONFIDENCE", "0.70"))

            self.strategies["FundingArb"] = FundingArbStrategy(
                min_funding_rate=funding_min_rate,
                max_allocation_pct=funding_max_alloc,
                rebalance_threshold=funding_rebalance,
                lookback_hours=funding_lookback,
                min_confidence=funding_min_confidence,
                client=self.client,  # Pass Pacifica client for API calls
            )
            logger.info(
                f"Funding Arb strategy enabled: min_rate={funding_min_rate:.4%}, "
                f"max_alloc={funding_max_alloc:.0%}, rebalance={funding_rebalance:.1%}"
            )

        if self.enable_momentum_scalping:
            # Load Momentum Scalping parameters from environment
            momentum_ema_fast = int(os.getenv("MOMENTUM_EMA_FAST", "9"))
            momentum_ema_slow = int(os.getenv("MOMENTUM_EMA_SLOW", "21"))
            momentum_rsi_period = int(os.getenv("MOMENTUM_RSI_PERIOD", "14"))
            momentum_rsi_lower = float(os.getenv("MOMENTUM_RSI_OVERSOLD", "35"))
            momentum_rsi_upper = float(os.getenv("MOMENTUM_RSI_OVERBOUGHT", "65"))
            momentum_atr_period = int(os.getenv("MOMENTUM_ATR_PERIOD", "14"))
            momentum_atr_stop = float(os.getenv("MOMENTUM_ATR_STOP_MULTIPLIER", "1.5"))
            momentum_atr_target = float(os.getenv("MOMENTUM_ATR_TARGET_MULTIPLIER", "2.5"))
            momentum_min_confidence = float(os.getenv("MOMENTUM_MIN_CONFIDENCE", "0.60"))
            momentum_cooldown = int(os.getenv("MOMENTUM_COOLDOWN_MINUTES", "5"))
            momentum_volume_mult = float(os.getenv("MOMENTUM_VOLUME_MULTIPLIER", "1.2"))
            momentum_macd_fast = int(os.getenv("MOMENTUM_MACD_FAST", "12"))
            momentum_macd_slow = int(os.getenv("MOMENTUM_MACD_SLOW", "26"))
            momentum_macd_signal = int(os.getenv("MOMENTUM_MACD_SIGNAL", "9"))

            self.strategies["MomentumScalping"] = MomentumScalpingStrategy(
                ema_fast=momentum_ema_fast,
                ema_slow=momentum_ema_slow,
                rsi_period=momentum_rsi_period,
                rsi_lower=momentum_rsi_lower,
                rsi_upper=momentum_rsi_upper,
                atr_period=momentum_atr_period,
                atr_stop_mult=momentum_atr_stop,
                atr_target_mult=momentum_atr_target,
                min_confidence=momentum_min_confidence,
                cooldown_minutes=momentum_cooldown,
                volume_threshold=momentum_volume_mult,
                macd_fast=momentum_macd_fast,
                macd_slow=momentum_macd_slow,
                macd_signal=momentum_macd_signal,
            )
            logger.info(
                f"Momentum Scalping strategy enabled: EMA {momentum_ema_fast}/{momentum_ema_slow}, "
                f"ATR stop={momentum_atr_stop}x, target={momentum_atr_target}x, "
                f"min_confidence={momentum_min_confidence:.0%}"
            )

        if self.enable_orderbook_imbalance:
            # Load Order Book Imbalance parameters from environment
            ob_levels = int(os.getenv("ORDERBOOK_LEVELS", "10"))
            ob_imb_long = float(os.getenv("ORDERBOOK_IMBALANCE_THRESHOLD_LONG", "0.62"))
            ob_imb_short = float(os.getenv("ORDERBOOK_IMBALANCE_THRESHOLD_SHORT", "0.38"))
            ob_strong_imb = float(os.getenv("ORDERBOOK_STRONG_IMBALANCE", "0.72"))
            ob_min_density = int(os.getenv("ORDERBOOK_MIN_ORDER_DENSITY", "5"))
            ob_spoof_detect = os.getenv("ORDERBOOK_SPOOF_DETECTION", "true").lower() == "true"
            ob_atr_stop = float(os.getenv("ORDERBOOK_ATR_STOP_MULTIPLIER", "0.75"))
            ob_atr_target = float(os.getenv("ORDERBOOK_ATR_TARGET_MULTIPLIER", "1.5"))
            ob_min_confidence = float(os.getenv("ORDERBOOK_MIN_CONFIDENCE", "0.55"))
            ob_cooldown = int(os.getenv("ORDERBOOK_COOLDOWN_SECONDS", "30"))
            ob_update_interval = int(os.getenv("ORDERBOOK_UPDATE_INTERVAL_MS", "500"))

            self.strategies["OrderBookImbalance"] = OrderBookImbalanceStrategy(
                levels=ob_levels,
                imbalance_long_threshold=ob_imb_long,
                imbalance_short_threshold=ob_imb_short,
                strong_imbalance_threshold=ob_strong_imb,
                min_order_density=ob_min_density,
                spoof_detection=ob_spoof_detect,
                atr_stop_mult=ob_atr_stop,
                atr_target_mult=ob_atr_target,
                min_confidence=ob_min_confidence,
                cooldown_seconds=ob_cooldown,
                update_interval_ms=ob_update_interval,
            )
            logger.info(
                f"Order Book Imbalance strategy enabled: levels={ob_levels}, "
                f"long>{ob_imb_long:.0%}, short<{ob_imb_short:.0%}, "
                f"ATR stop={ob_atr_stop}x, target={ob_atr_target}x"
            )

        logger.info(
            f"StrategyManager initialized with {len(self.strategies)} strategies: "
            f"{list(self.strategies.keys())}"
        )

    def should_skip_signal(self, signal: Signal) -> bool:
        """
        Check if signal should be skipped due to cooldown or other rules.

        Args:
            signal: Signal to check

        Returns:
            True if signal should be skipped, False otherwise
        """
        key = (signal.asset, signal.strategy.value)

        # Check cooldown
        if key in self._trade_cooldowns:
            cooldown_until = self._trade_cooldowns[key]
            if datetime.now() < cooldown_until:
                remaining = (cooldown_until - datetime.now()).total_seconds() / 60
                logger.debug(
                    f"Signal skipped for {key}: cooldown {remaining:.1f}min remaining"
                )
                return True

        # Check recent trade frequency (anti-spam) - increased limit Jan 2026
        recent_trades = [
            t
            for t in self._executed_trades
            if t["symbol"] == signal.asset
            and t["strategy"] == signal.strategy.value
            and (datetime.now() - t["timestamp"]).total_seconds() < 300
        ]  # Last 5 min

        if (
            len(recent_trades) >= 3
        ):  # Was 2 - Max 3 trades per strategy/symbol per 5 min
            logger.warning(
                f"Signal skipped for {key}: too many recent trades ({len(recent_trades)})"
            )
            return True

        return False

    def register_trade_execution(self, signal: Signal, order_result: Dict):
        """
        Register successful trade execution for feedback loop.

        Args:
            signal: Signal that was executed
            order_result: Order execution result
        """
        key = (signal.asset, signal.strategy.value)

        # Set cooldown (strategy-specific)
        cooldown_minutes = self._get_strategy_cooldown(signal.strategy.value)
        self._trade_cooldowns[key] = datetime.now() + timedelta(
            minutes=cooldown_minutes
        )

        # Record trade
        trade_record = {
            "symbol": signal.asset,
            "strategy": signal.strategy.value,
            "side": signal.side.value,
            "quantity": order_result.get("quantity", 0),
            "price": order_result.get("price", 0),
            "timestamp": datetime.now(),
            "order_id": order_result.get("id"),
            "signal_confidence": signal.confidence,
        }

        self._executed_trades.append(trade_record)

        # Keep only recent trades (last 24 hours)
        cutoff = datetime.now() - timedelta(hours=24)
        self._executed_trades = [
            t for t in self._executed_trades if t["timestamp"] > cutoff
        ]

        logger.info(f"Trade registered: {key} cooldown {cooldown_minutes}min")

    def _get_strategy_cooldown(self, strategy_name: str) -> int:
        """
        Get cooldown period in minutes for strategy.

        Args:
            strategy_name: Name of the strategy

        Returns:
            Cooldown period in minutes
        """
        # Cooldowns reduced by ~50% for more trading activity (Jan 2026)
        cooldowns = {
            "mean_reversion": 8,  # Was 15 - 8 min between MR trades
            "ma_crossover": 15,  # Was 30 - 15 min for trend signals
            "grid_trading": 3,  # Was 5 - 3 min for grid adjustments
            "liquidation_capture": 5,  # Was 10 - 5 min for liquidation plays
            "trend_following": 30,  # Was 60 - 30 min for major trend changes
            "vwap_scalping": 8,  # 8 min cooldown for VWAP scalping
            "funding_arb": 60,  # 60 min - funding positions held for hours
            "momentum_scalping": 5,  # 5 min cooldown for fast momentum scalping
            "orderbook_imbalance": 0.5,  # 30 sec cooldown - handled internally in seconds
        }

        return cooldowns.get(strategy_name, 5)  # Default 5 min (was 10)

    def _validate_market_data(
        self, symbol: str, multi_tf_data: Dict[str, Dict[str, List[float]]]
    ) -> bool:
        """
        Validate that market data is sufficient for analysis.

        Args:
            symbol: Trading symbol
            multi_tf_data: Multi-timeframe OHLCV data

        Returns:
            True if data is valid for analysis, False otherwise
        """
        # Check if we have any timeframe data at all
        if not multi_tf_data:
            logger.warning(f"{symbol}: No timeframe data available")
            return False

        # Check for minimum required timeframes
        required_timeframes = ["15m", "1h", "4h"]
        available_timeframes = list(multi_tf_data.keys())

        # Must have at least 15m and 1h data
        if not any(tf in available_timeframes for tf in ["15m", "1h"]):
            logger.warning(
                f"{symbol}: Missing required timeframes (15m, 1h). Available: {available_timeframes}"
            )
            return False

        # Check data quality for each timeframe
        for tf, data in multi_tf_data.items():
            if not isinstance(data, dict):
                logger.warning(f"{symbol} {tf}: Invalid data format")
                continue

            # Check required OHLCV fields
            required_fields = ["open", "high", "low", "close", "volume"]
            missing_fields = [field for field in required_fields if field not in data]
            if missing_fields:
                logger.warning(f"{symbol} {tf}: Missing OHLCV fields: {missing_fields}")
                continue

            # Check minimum data length (need at least 10 candles for basic analysis)
            min_length = 10
            for field in required_fields:
                if len(data[field]) < min_length:
                    logger.warning(
                        f"{symbol} {tf}: Insufficient data length for {field}: {len(data[field])} < {min_length}"
                    )
                    return False

            # Check for valid numeric data (not all zeros or NaN)
            try:
                close_prices = [float(x) for x in data["close"] if x > 0]
                if len(close_prices) < min_length:
                    logger.warning(
                        f"{symbol} {tf}: Too many zero/invalid close prices: {len(close_prices)} valid out of {len(data['close'])}"
                    )
                    return False
            except (ValueError, TypeError) as e:
                logger.warning(f"{symbol} {tf}: Invalid price data: {e}")
                return False

        return True

    def generate_signals_for_market(
        self,
        symbol: str,
        multi_tf_data: Dict[str, Dict[str, List[float]]],
        current_price: float,
        execution_tf_data: Optional[Dict[str, Dict[str, List[float]]]] = None,
    ) -> List[Signal]:
        """
        Generate trading signals for a symbol using regime-based strategy selection.

        TIMEFRAME HIERARCHY (Multi-TF Execution Model):
        - multi_tf_data: Regime/structure TFs (15m, 1h, 4h) - used for regime detection
        - execution_tf_data: Execution TFs (1m, 5m) - used for precise entry timing

        Main entry point for signal generation. Workflow:
        1. Validate market data quality
        2. Detect current market regime (using 1h/4h)
        3. Get active strategies for regime
        4. Generate signals from each active strategy (passing execution_tf_data)
        5. Resolve conflicts if multiple signals
        6. Return final signal list (max 1 per symbol)

        Args:
            symbol: Trading symbol (e.g., "SUI-PERP")
            multi_tf_data: Regime/structure OHLCV data {"15m": {...}, "1h": {...}, "4h": {...}}
            current_price: Current market price
            execution_tf_data: Optional execution OHLCV data {"1m": {...}, "5m": {...}}
                               If provided, strategies can use 5m for triggers and 1m for timing.

        Returns:
            List of validated Signal objects (0-1 signals)
        """
        try:
            # Step 0: Validate data quality before proceeding
            if not self._validate_market_data(symbol, multi_tf_data):
                logger.warning(
                    f"{symbol}: Skipping signal generation due to data quality issues"
                )
                return []

            # Step 1: Detect market regime (prefer 4h data, fallback to 1h)
            regime_data = multi_tf_data.get("4h", multi_tf_data.get("1h", {}))

            if not regime_data or "close" not in regime_data:
                logger.warning(
                    f"No suitable timeframe data for regime detection for {symbol}"
                )
                return []

            try:
                # Use cached regime detection to prevent unnecessary recalculation every loop
                regime = self.regime_detector.detect_regime_cached(symbol, regime_data)
            except ValueError as e:
                # Handle insufficient data case specifically
                if "Insufficient data" in str(e):
                    logger.warning(f"Insufficient historical data for {symbol}: {e}")
                    # Default to INDECISIVE for markets with insufficient data
                    regime = MarketRegime.INDECISIVE
                else:
                    logger.error(f"Regime detection failed for {symbol}: {e}")
                    regime = MarketRegime.INDECISIVE
            except Exception as e:
                logger.error(f"Regime detection failed for {symbol}: {e}")
                # Default to INDECISIVE if regime detection fails
                regime = MarketRegime.INDECISIVE

            # Step 2: Get active strategies for current regime
            active_strategy_names = self.regime_detector.get_active_strategies(regime)

            # Step 2.25: ENFORCE GRID REGIME CONSTRAINTS (MANDATORY per Grid Trading Brief)
            # Grid trading ONLY allowed in: RANGING_CALM, RANGING_VOLATILE, INDECISIVE
            # HARD DISABLE in: TRENDING_MODERATE, TRENDING_STRONG
            if regime in [MarketRegime.TRENDING_STRONG, MarketRegime.TRENDING_MODERATE]:
                if "GridTrading" in active_strategy_names:
                    active_strategy_names = [
                        name for name in active_strategy_names if name != "GridTrading"
                    ]
                    logger.info(
                        f"Grid trading HARD DISABLED in {regime.value} regime for {symbol}"
                    )

            # Step 2.5: Always add LiquidationCapture if enabled (runs in ALL regimes)
            if (
                "LiquidationCapture" in self.strategies
                and "LiquidationCapture" not in active_strategy_names
            ):
                active_strategy_names = list(active_strategy_names) + [
                    "LiquidationCapture"
                ]
                logger.debug(
                    f"{symbol}: Added LiquidationCapture (runs in all regimes)"
                )

            # Step 2.6: Always add VWAPScalping if enabled (overlay strategy, runs in ALL regimes)
            if (
                "VWAPScalping" in self.strategies
                and "VWAPScalping" not in active_strategy_names
            ):
                active_strategy_names = list(active_strategy_names) + [
                    "VWAPScalping"
                ]
                logger.debug(
                    f"{symbol}: Added VWAPScalping (overlay strategy, runs in all regimes)"
                )

            # Step 2.7: Always add FundingArb if enabled (passive strategy, runs in ALL regimes)
            if (
                "FundingArb" in self.strategies
                and "FundingArb" not in active_strategy_names
            ):
                active_strategy_names = list(active_strategy_names) + [
                    "FundingArb"
                ]
                logger.debug(
                    f"{symbol}: Added FundingArb (passive funding rate strategy, runs in all regimes)"
                )

            # Step 2.8: Add MomentumScalping if enabled and in TRENDING regimes
            if (
                "MomentumScalping" in self.strategies
                and "MomentumScalping" not in active_strategy_names
                and regime in [MarketRegime.TRENDING_STRONG, MarketRegime.TRENDING_MODERATE]
            ):
                active_strategy_names = list(active_strategy_names) + [
                    "MomentumScalping"
                ]
                logger.debug(
                    f"{symbol}: Added MomentumScalping (fast EMA crossover strategy for trending markets)"
                )

            # Step 2.9: Always add OrderBookImbalance if enabled (overlay strategy, runs in ALL regimes)
            if (
                "OrderBookImbalance" in self.strategies
                and "OrderBookImbalance" not in active_strategy_names
            ):
                active_strategy_names = list(active_strategy_names) + [
                    "OrderBookImbalance"
                ]
                logger.debug(
                    f"{symbol}: Added OrderBookImbalance (flow-based overlay strategy, runs in all regimes)"
                )

            if not active_strategy_names:
                logger.info(f"{symbol}: Regime {regime.value} - no active strategies")
                return []

            # Log regime detection result with active strategies
            logger.info(
                f"{symbol}: Regime={regime.value}, strategies={active_strategy_names}"
            )

            # Step 3: Generate signals from each active strategy
            all_signals = []

            for strategy_name in active_strategy_names:
                strategy = self.strategies.get(strategy_name)

                if not strategy:
                    logger.warning(
                        f"Strategy {strategy_name} is active but not initialized"
                    )
                    continue

                try:
                    logger.debug(
                        f"{symbol}: Calling {strategy_name}.generate_signals()"
                    )

                    # Special handling for OrderBookImbalance - needs orderbook data
                    if strategy_name == "OrderBookImbalance" and self.ws_client:
                        orderbook = self.ws_client.get_orderbook(symbol)
                        if orderbook:
                            signals = strategy.generate_signals(
                                symbol,
                                multi_tf_data,
                                current_price,
                                orderbook=orderbook,
                                execution_tf_data=execution_tf_data,
                            )
                        else:
                            logger.debug(f"{symbol}: No orderbook data for OrderBookImbalance")
                            signals = []
                    else:
                        # Pass execution_tf_data if the strategy accepts it (Mean Reversion, Liquidation Capture)
                        # Other strategies will ignore it (backward compatibility)
                        try:
                            signals = strategy.generate_signals(
                                symbol,
                                multi_tf_data,
                                current_price,
                                execution_tf_data=execution_tf_data,
                            )
                        except TypeError:
                            # Strategy doesn't accept execution_tf_data parameter - use old signature
                            signals = strategy.generate_signals(
                                symbol, multi_tf_data, current_price
                            )

                    if signals:
                        logger.info(
                            f"{symbol}: {strategy_name} generated {len(signals)} signal(s) "
                            f"in {regime.value} regime"
                        )
                        all_signals.extend(signals)
                    else:
                        logger.debug(f"{symbol}: {strategy_name} returned 0 signals")

                except Exception as e:
                    logger.error(f"Error in {strategy_name} for {symbol}: {e}")
                    continue

            if not all_signals:
                logger.debug(
                    f"{symbol}: No signals from any strategy in {regime.value} regime"
                )
                return []

            # Step 4: Resolve conflicts if multiple signals
            if len(all_signals) == 1:
                signal = all_signals[0]
                if signal.is_valid():
                    logger.info(
                        f"{symbol}: Single valid signal - {signal.side.value} @ ${signal.entry_price:.2f} "
                        f"(confidence: {signal.confidence:.2%}, RRR: {signal.rrr:.2f})"
                    )
                    return [signal]
                else:
                    logger.warning(f"{symbol}: Single signal failed validation")
                    return []

            # Multiple signals - resolve conflicts
            final_signals = self._resolve_signal_conflicts(all_signals, regime)

            # Validate final signals
            valid_signals = [s for s in final_signals if s.is_valid()]

            if valid_signals:
                logger.info(
                    f"{symbol}: Resolved to {len(valid_signals)} valid signal(s) "
                    f"from {len(all_signals)} candidates"
                )

            return valid_signals

        except Exception as e:
            logger.error(f"Error generating signals for {symbol}: {e}")
            return []

    def _resolve_signal_conflicts(
        self, signals: List[Signal], regime: MarketRegime
    ) -> List[Signal]:
        """
        Resolve conflicts when multiple signals are generated.

        Rules:
        1. Grid Trading special case: BUY + SELL signals are BOTH returned (not conflicting)
        2. All signals same direction → Combine with weighted average
        3. Opposing signals (BUY + SELL):
           - TRENDING regime: Trust higher confidence signal
           - RANGING regime: Trust mean reversion over breakout
        4. Quality override: HIGH_CONVICTION always takes priority

        Args:
            signals: List of Signal objects to resolve
            regime: Current market regime

        Returns:
            List with 0-2 resolved signals (2 for grid BUY+SELL pairs)
        """
        if not signals:
            return []

        # Separate signals by direction
        buy_signals = [s for s in signals if s.side == OrderSide.BUY]
        sell_signals = [s for s in signals if s.side == OrderSide.SELL]

        # SPECIAL CASE: Grid Trading BUY + SELL signals (not conflicting)
        # Grid trading generates both sides simultaneously to set up the grid
        # Handle this FIRST to ensure grid signals are not blocked by high-conviction filter
        if buy_signals and sell_signals:
            grid_signals = self._handle_grid_signals(buy_signals, sell_signals, regime)
            if grid_signals:
                return grid_signals
            # If not a valid grid setup, continue to normal conflict resolution

        # Check for HIGH_CONVICTION signals (automatic priority)
        # Note: Grid signals already handled above, so this won't block grid signals
        high_conviction_signals = [
            s for s in signals if s.quality == TradeQuality.HIGH_CONVICTION
        ]

        if high_conviction_signals:
            # Take highest confidence HIGH_CONVICTION signal
            best_signal = max(high_conviction_signals, key=lambda s: s.confidence)
            logger.info(
                f"Conflict resolution: HIGH_CONVICTION override - "
                f"{best_signal.strategy.value} {best_signal.side.value} @ {best_signal.confidence:.2%}"
            )
            return [best_signal]

        # Case 1: All signals same direction → Combine
        if buy_signals and not sell_signals:
            combined = self._combine_signals(buy_signals, regime)
            logger.info(
                f"Conflict resolution: Combined {len(buy_signals)} BUY signals → "
                f"confidence {combined.confidence:.2%}"
            )
            return [combined]

        if sell_signals and not buy_signals:
            combined = self._combine_signals(sell_signals, regime)
            logger.info(
                f"Conflict resolution: Combined {len(sell_signals)} SELL signals → "
                f"confidence {combined.confidence:.2%}"
            )
            return [combined]

        # Case 2: Opposing signals (BUY + SELL) → Use tiebreaker
        logger.warning(
            f"Conflict resolution: Opposing signals - "
            f"{len(buy_signals)} BUY vs {len(sell_signals)} SELL"
        )

        winner = self._tiebreaker(buy_signals, sell_signals, regime)

        if winner:
            logger.info(
                f"Conflict resolution: Tiebreaker winner - "
                f"{winner.strategy.value} {winner.side.value} @ {winner.confidence:.2%}"
            )
            return [winner]

        # No clear winner - stay flat
        logger.warning("Conflict resolution: No clear winner - staying flat")
        return []

    def _handle_grid_signals(
        self,
        buy_signals: List[Signal],
        sell_signals: List[Signal],
        regime: MarketRegime,
    ) -> Optional[List[Signal]]:
        """
        Handle Grid Trading BUY + SELL signal pairs.

        Grid Trading generates both BUY and SELL signals simultaneously to set up
        orders on both sides of current price. This is NOT a conflict - both signals
        should be executed together as a grid setup.

        Validation checks:
        1. All signals must be from GRID_TRADING strategy
        2. Same symbol across all signals
        3. Minimum confidence threshold met for both sides
        4. Valid prices and stop losses
        5. Grid setup makes sense (BUY below current, SELL above current)

        Args:
            buy_signals: List of BUY signals
            sell_signals: List of SELL signals
            regime: Current market regime

        Returns:
            List of [BUY, SELL] signals if valid grid, None otherwise
        """
        # Check if ALL signals are from Grid Trading
        all_signals = buy_signals + sell_signals
        grid_signals = [
            s for s in all_signals if s.strategy == StrategyType.GRID_TRADING
        ]

        if len(grid_signals) != len(all_signals):
            # Mixed strategies - not a pure grid setup, use normal conflict resolution
            logger.debug(
                f"Grid handler: Mixed strategies detected "
                f"({len(grid_signals)} grid, {len(all_signals) - len(grid_signals)} other) - "
                f"using normal conflict resolution"
            )
            return None

        # Must have exactly 1 BUY and 1 SELL for a basic grid level
        grid_buy = [s for s in grid_signals if s.side == OrderSide.BUY]
        grid_sell = [s for s in grid_signals if s.side == OrderSide.SELL]

        if len(grid_buy) != 1 or len(grid_sell) != 1:
            logger.warning(
                f"Grid handler: Invalid grid setup - expected 1 BUY + 1 SELL, "
                f"got {len(grid_buy)} BUY + {len(grid_sell)} SELL"
            )
            return None

        buy_signal = grid_buy[0]
        sell_signal = grid_sell[0]

        # Validate same symbol
        if buy_signal.asset != sell_signal.asset:
            logger.error(
                f"Grid handler: Symbol mismatch - "
                f"BUY={buy_signal.asset}, SELL={sell_signal.asset}"
            )
            return None

        # REMOVED: Hard confidence threshold (Prompt 054 - confidence affects SIZE, not permission)
        # Confidence will be handled by ConfidenceSizer - low confidence = smaller position
        # Log warning for very low confidence but don't block
        if buy_signal.confidence < 0.3 or sell_signal.confidence < 0.3:
            logger.warning(
                f"Grid handler: Low confidence signals - "
                f"BUY={buy_signal.confidence:.2%}, SELL={sell_signal.confidence:.2%} "
                f"(will use reduced position size)"
            )

        # Validate price ordering (BUY should be below SELL for grid setup)
        if buy_signal.entry_price >= sell_signal.entry_price:
            logger.error(
                f"Grid handler: Invalid price ordering - "
                f"BUY entry ${buy_signal.entry_price:.4f} >= SELL entry ${sell_signal.entry_price:.4f}"
            )
            return None

        # Validate stop losses exist
        if not buy_signal.stop_loss or not sell_signal.stop_loss:
            logger.error("Grid handler: Missing stop loss on one or both signals")
            return None

        # Additional regime-specific validation
        if regime not in [
            MarketRegime.RANGING_CALM,
            MarketRegime.RANGING_VOLATILE,
            MarketRegime.INDECISIVE,
        ]:
            logger.warning(
                f"Grid handler: Grid signals in non-ranging regime ({regime.value}) - rejecting"
            )
            return None

        # All validations passed - return both signals
        logger.info(
            f"Grid handler: Valid grid setup - "
            f"{buy_signal.asset} BUY @ ${buy_signal.entry_price:.4f} + "
            f"SELL @ ${sell_signal.entry_price:.4f} "
            f"(confidence: {buy_signal.confidence:.2%}/{sell_signal.confidence:.2%})"
        )

        return [buy_signal, sell_signal]

    def _combine_signals(self, signals: List[Signal], regime: MarketRegime) -> Signal:
        """
        Combine multiple signals of the same direction using weighted average.

        Args:
            signals: List of signals (all same direction)
            regime: Current market regime

        Returns:
            Combined Signal object
        """
        if len(signals) == 1:
            return signals[0]

        # Get regime-based strategy weights
        strategy_weights = self.regime_detector.get_strategy_weights(regime)

        # Calculate weighted averages
        total_weight = 0.0
        weighted_confidence = 0.0
        weighted_entry = 0.0
        weighted_stop = 0.0
        weighted_target = 0.0

        # Use first signal as template
        base_signal = signals[0]

        for signal in signals:
            # Get weight for this strategy (default to equal weight if not in map)
            strategy_name = self._get_strategy_display_name(signal.strategy)
            weight = strategy_weights.get(strategy_name, 1.0 / len(signals))

            weighted_confidence += signal.confidence * weight
            weighted_entry += signal.entry_price * weight
            weighted_stop += signal.stop_loss * weight

            if signal.take_profit:
                weighted_target += signal.take_profit * weight

            total_weight += weight

        # Normalize by total weight
        if total_weight > 0:
            combined_confidence = weighted_confidence / total_weight
            combined_entry = weighted_entry / total_weight
            combined_stop = weighted_stop / total_weight
            combined_target = (
                weighted_target / total_weight if weighted_target > 0 else None
            )
        else:
            # Fallback to simple average
            combined_confidence = sum(s.confidence for s in signals) / len(signals)
            combined_entry = sum(s.entry_price for s in signals) / len(signals)
            combined_stop = sum(s.stop_loss for s in signals) / len(signals)
            combined_target = (
                sum(s.take_profit for s in signals if s.take_profit) / len(signals)
                if any(s.take_profit for s in signals)
                else None
            )

        # Create combined signal using base signal as template
        combined = Signal(
            strategy=base_signal.strategy,
            asset=base_signal.asset,
            asset_class=base_signal.asset_class,
            side=base_signal.side,
            entry_price=combined_entry,
            stop_loss=combined_stop,
            take_profit=combined_target,
            confidence=combined_confidence,
            quality=TradeQuality.STANDARD,  # Combined signals are STANDARD quality
            market_state=base_signal.market_state,
            timeframe=base_signal.timeframe,
            pattern=f"combined_{len(signals)}_signals",
            volume_confirmation=all(s.volume_confirmation for s in signals),
            multi_timeframe_alignment=all(s.multi_timeframe_alignment for s in signals),
            support_resistance_valid=all(s.support_resistance_valid for s in signals),
            rrr_meets_minimum=True,  # Will be recalculated
            liquidation_buffer_safe=all(s.liquidation_buffer_safe for s in signals),
            account_risk_ok=all(s.account_risk_ok for s in signals),
            margin_drawdown_ok=all(s.margin_drawdown_ok for s in signals),
            forbidden_conditions_clear=all(
                s.forbidden_conditions_clear for s in signals
            ),
            indicators={
                "combined_from": [s.strategy.value for s in signals],
                "individual_confidences": [s.confidence for s in signals],
            },
            notes=f"Combined signal from {len(signals)} strategies",
        )

        return combined

    def _tiebreaker(
        self,
        buy_signals: List[Signal],
        sell_signals: List[Signal],
        regime: MarketRegime,
    ) -> Optional[Signal]:
        """
        Break ties when opposing signals (BUY + SELL) are generated.

        Tiebreaker rules:
        1. TRENDING regime: Trust higher confidence signal
        2. RANGING regime: Trust mean reversion over breakout
        3. Equal confidence: Highest quality wins
        4. Still tied: Return None (stay flat)

        Args:
            buy_signals: List of BUY signals
            sell_signals: List of SELL signals
            regime: Current market regime

        Returns:
            Winning Signal or None
        """
        # Regime-specific tiebreaker logic
        if regime == MarketRegime.TRENDING_STRONG:
            # TRENDING: Trust higher confidence signal
            all_signals = buy_signals + sell_signals
            winner = max(all_signals, key=lambda s: s.confidence)

            logger.info(
                f"Tiebreaker (TRENDING): Highest confidence wins - "
                f"{winner.strategy.value} {winner.side.value} @ {winner.confidence:.2%}"
            )
            return winner

        elif regime in [MarketRegime.RANGING_CALM, MarketRegime.RANGING_VOLATILE]:
            # RANGING: Trust mean reversion over breakout
            mean_reversion_signals = [
                s
                for s in (buy_signals + sell_signals)
                if s.strategy == StrategyType.MEAN_REVERSION
            ]

            if mean_reversion_signals:
                winner = max(mean_reversion_signals, key=lambda s: s.confidence)
                logger.info(
                    f"Tiebreaker (RANGING): Mean reversion priority - "
                    f"{winner.side.value} @ {winner.confidence:.2%}"
                )
                return winner

        # Fallback: Highest confidence across all signals
        all_signals = buy_signals + sell_signals

        if not all_signals:
            return None

        # Sort by confidence, then quality
        sorted_signals = sorted(
            all_signals, key=lambda s: (s.confidence, s.quality.value), reverse=True
        )

        best = sorted_signals[0]

        # Check if there's a clear winner (confidence difference > 10%)
        if len(sorted_signals) > 1:
            second_best = sorted_signals[1]
            confidence_diff = best.confidence - second_best.confidence

            if confidence_diff < 0.10:
                # Too close to call - stay flat
                logger.warning(
                    f"Tiebreaker: Too close to call - "
                    f"best={best.confidence:.2%} vs second={second_best.confidence:.2%}"
                )
                return None

        logger.info(
            f"Tiebreaker (fallback): Highest confidence - "
            f"{best.strategy.value} {best.side.value} @ {best.confidence:.2%}"
        )
        return best

    def _get_strategy_display_name(self, strategy_type: StrategyType) -> str:
        """
        Map StrategyType enum to display name used by MarketRegimeDetector.

        Args:
            strategy_type: StrategyType enum value

        Returns:
            Display name string
        """
        mapping = {
            StrategyType.MEAN_REVERSION: "MeanReversion",
            StrategyType.MA_CROSSOVER: "MACrossover",
            StrategyType.TREND_FOLLOWING: "TrendFollowing",
            StrategyType.GRID_TRADING: "GridTrading",
            StrategyType.LIQUIDATION_CAPTURE: "LiquidationCapture",
            StrategyType.VWAP_SCALPING: "VWAPScalping",
            StrategyType.FUNDING_ARB: "FundingArb",
            StrategyType.MOMENTUM_SCALPING: "MomentumScalping",
            StrategyType.ORDERBOOK_IMBALANCE: "OrderBookImbalance",
        }
        fallback = strategy_type.value if strategy_type.value else str(strategy_type)
        return mapping.get(strategy_type, fallback)

    def get_enabled_strategies(self) -> List[str]:
        """Get list of currently enabled strategy names."""
        return list(self.strategies.keys())

    def get_strategy_count(self) -> int:
        """Get number of enabled strategies."""
        return len(self.strategies)

    def get_strategies_by_type(self) -> Dict[StrategyType, Any]:
        """
        Get strategies dict keyed by StrategyType for use with SignalPipeline.

        Returns:
            Dict mapping StrategyType to strategy instance
        """
        type_map = {
            "MeanReversion": StrategyType.MEAN_REVERSION,
            "MACrossover": StrategyType.MA_CROSSOVER,
            "TrendFollowing": StrategyType.TREND_FOLLOWING,
            "GridTrading": StrategyType.GRID_TRADING,
            "LiquidationCapture": StrategyType.LIQUIDATION_CAPTURE,
            "VWAPScalping": StrategyType.VWAP_SCALPING,
            "FundingArb": StrategyType.FUNDING_ARB,
            "MomentumScalping": StrategyType.MOMENTUM_SCALPING,
            "OrderBookImbalance": StrategyType.ORDERBOOK_IMBALANCE,
        }

        result = {}
        for name, strategy in self.strategies.items():
            strategy_type = type_map.get(name)
            if strategy_type:
                result[strategy_type] = strategy

        return result

    def set_cooldown_manager(self, cooldown_manager):
        """
        Set the cooldown manager for per-strategy cooldowns.

        Args:
            cooldown_manager: CooldownManager instance
        """
        self._cooldown_manager = cooldown_manager
        logger.info("CooldownManager integrated with StrategyManager")
