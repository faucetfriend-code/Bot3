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
from .diagnostics.funnel import (
    NULL_FUNNEL,
    REASON_CONFIDENCE_GATE,
    REASON_CONFLICT_RESOLUTION,
    REASON_DATA_QUALITY,
    REASON_NO_ACTIVE_STRATEGIES,
    REASON_NO_ORDERBOOK,
    REASON_NO_REGIME_DATA,
    REASON_STRATEGY_EXCEPTION,
    REASON_STRATEGY_MISSING,
    REASON_VALIDITY,
    STAGE_BARS_EVALUATED,
    STAGE_CONFIDENCE_DROPPED,
    STAGE_CONFLICT_DROPPED,
    STAGE_DATA_REJECTED,
    STAGE_RAW_SIGNALS,
    STAGE_REGIME_BLOCKED,
    STAGE_STRATEGY_INVOKED,
    STAGE_VALIDITY_DROPPED,
)
from .market_regime import MarketRegimeDetector, MarketRegime
from .volatility_regime import make_regime_detector
from .strategies.mean_reversion import MeanReversionStrategy
from .strategies.ma_crossover import MACrossoverStrategy
from .strategies.grid_trading import GridTradingStrategy
from .strategies.liquidation_capture import LiquidationCaptureStrategy
from .strategies.vwap_scalping import (
    VWAPScalpingStrategy,
    DEFAULT_SD_ENTRY_THRESHOLD as VWAP_DEFAULT_SD_ENTRY_THRESHOLD,
)
from .exchanges import get_exchange_capabilities
from .regime_param_overlay import DISPLAY_TO_STRATEGY_KEY
from .strategies.funding_arb import FundingArbStrategy
from .strategies.momentum_scalping import MomentumScalpingStrategy
from .strategies.orderbook_imbalance import OrderBookImbalanceStrategy
from .strategies.session_range_breakout import SessionRangeBreakoutStrategy
from .strategies.vwap_pullback import VWAPPullbackStrategy
from .directional_bias import (
    GATE_ENFORCE,
    GATE_OFF,
    DirectionalBiasEngine,
    resolve_gate_exempt,
    validate_gate_mode,
)
from .strategies.calendar_flow import CalendarFlowStrategy


# VWAPScalping is a mean-reversion strategy, so it is gated away from the
# trending regimes where its SELL signals fight the trend. Which of the
# remaining regimes it is allowed into is a MAPPING decision, not a strategy
# parameter, and `docs/REGIME-CENSUS.md` measured the three cells separately:
# ranging_calm PF 0.65 (n=1246), ranging_volatile PF 0.77 (n=226),
# indecisive PF 1.05 (n=337). Making the list configurable is what allows a
# losing mapping to be removed and the removal to be falsified, at zero
# search-budget cost.
DEFAULT_VWAP_ACTIVE_REGIMES = (
    MarketRegime.RANGING_VOLATILE,
    MarketRegime.RANGING_CALM,
    MarketRegime.INDECISIVE,
)

# Regimes VWAP is never admitted to, whatever the env asks for. Its entries
# are counter-trend by construction.
_VWAP_FORBIDDEN_REGIMES = (
    MarketRegime.TRENDING_STRONG,
    MarketRegime.TRENDING_MODERATE,
)


def resolve_vwap_active_regimes(
    value: Optional[str] = None,
) -> List[MarketRegime]:
    """Resolve which regimes VWAPScalping is admitted to.

    Reads ``VWAP_ACTIVE_REGIMES`` (comma-separated regime names, case
    insensitive) when ``value`` is None. Unknown names and the trending
    regimes are dropped with a warning rather than taking the run down,
    matching the warn-and-fall-back convention used by
    :func:`~trading_bot_v2.strategies.vwap_scalping.validate_sd_entry_threshold`.

    Args:
        value: Explicit comma-separated regime list. ``None`` reads the
            environment.

    Returns:
        The regimes VWAPScalping may trade in, in a stable order. Falls
        back to :data:`DEFAULT_VWAP_ACTIVE_REGIMES` when nothing usable
        is configured.
    """
    raw = value if value is not None else os.getenv("VWAP_ACTIVE_REGIMES")
    if raw is None or not str(raw).strip():
        return list(DEFAULT_VWAP_ACTIVE_REGIMES)

    by_name = {regime.value.upper(): regime for regime in MarketRegime}
    resolved: List[MarketRegime] = []
    for token in str(raw).split(","):
        name = token.strip().upper()
        if not name:
            continue
        regime = by_name.get(name)
        if regime is None:
            logger.warning(
                f"VWAP_ACTIVE_REGIMES contains unknown regime {token.strip()!r} "
                f"- ignoring it. Valid names: {sorted(by_name)}"
            )
            continue
        if regime in _VWAP_FORBIDDEN_REGIMES:
            logger.warning(
                f"VWAP_ACTIVE_REGIMES asks for {regime.value}, but VWAPScalping "
                f"is counter-trend and is never admitted to a trending regime "
                f"- ignoring it."
            )
            continue
        if regime not in resolved:
            resolved.append(regime)

    if not resolved:
        logger.warning(
            f"VWAP_ACTIVE_REGIMES={raw!r} resolved to no usable regime - "
            f"falling back to the shipped mapping "
            f"{[r.value for r in DEFAULT_VWAP_ACTIVE_REGIMES]}."
        )
        return list(DEFAULT_VWAP_ACTIVE_REGIMES)
    return resolved


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
        enable_session_range_breakout: Optional[bool] = None,
        enable_calendar_flow: Optional[bool] = None,
        enable_vwap_pullback: Optional[bool] = None,
        risk_manager=None,
        client=None,  # Pacifica client for funding arb API calls
        ws_client=None,  # WebSocket client for orderbook data
        db=None,  # DatabaseManager for adaptive per-regime weights
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
            db: Optional DatabaseManager; when provided, adaptive per-regime
                strategy weights are computed from closed trades
                (ENABLE_ADAPTIVE_WEIGHTS). When None, adaptive weights stay
                neutral (1.0) and static regime weights apply unchanged.
        """
        # Initialize regime detector
        # REGIME_MODE selects the taxonomy (ADX by default).
        self.regime_detector = regime_detector or make_regime_detector()

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
        # Which regimes VWAPScalping is admitted to (VWAP_ACTIVE_REGIMES).
        self.vwap_active_regimes = resolve_vwap_active_regimes()
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
        self.enable_session_range_breakout = (
            enable_session_range_breakout
            if enable_session_range_breakout is not None
            else _get_env_bool(
                "ENABLE_SESSION_RANGE_BREAKOUT", False
            )  # Default to False - ships disabled until validated
        )
        self.enable_calendar_flow = (
            enable_calendar_flow
            if enable_calendar_flow is not None
            else _get_env_bool(
                "ENABLE_CALENDAR_FLOW", False
            )  # Default to False - ships disabled until validated
        )
        self.enable_vwap_pullback = (
            enable_vwap_pullback
            if enable_vwap_pullback is not None
            else _get_env_bool(
                "ENABLE_VWAP_PULLBACK", False
            )  # Default to False - ships disabled until validated
        )
        self.risk_manager = risk_manager
        self.client = client  # Store client for funding arb
        self.ws_client = ws_client  # Store websocket client for orderbook

        # Directional bias gate (HTF trend stack + funding extremes).
        # Default "off" = shipped behaviour; "log" annotates signals only;
        # "enforce" fails multi_timeframe_alignment on signals fighting the
        # bias. See directional_bias.py for the full rationale.
        self.directional_gate_mode = validate_gate_mode(os.getenv("DIRECTIONAL_GATE"))
        self.directional_gate_exempt = resolve_gate_exempt()
        # Strategies not admitted when the combined bias is NEUTRAL.
        # Measured (composite tuning, 2026-07-30): mean_reversion loses
        # in every vol tercile's neutral state under both gate arms,
        # while all three trend states are positive under enforce.
        # Default empty - each strategy earns its exclusion by
        # measurement. Only consulted in enforce mode.
        self.directional_neutral_exclude = {
            name.strip()
            for name in os.getenv("DIRECTIONAL_NEUTRAL_EXCLUDE", "").split(",")
            if name.strip()
        }
        self.directional_bias_engine = DirectionalBiasEngine(client=client)
        if self.directional_gate_mode != GATE_OFF:
            logger.info(
                f"Directional gate: mode={self.directional_gate_mode}, "
                f"exempt={sorted(self.directional_gate_exempt)}, "
                f"trend={self.directional_bias_engine.trend_timeframe} "
                f"EMA {self.directional_bias_engine.ema_fast}/"
                f"{self.directional_bias_engine.ema_slow}, "
                f"funding lookback={self.directional_bias_engine.funding_lookback} "
                f"pct [{self.directional_bias_engine.funding_low_pct}, "
                f"{self.directional_bias_engine.funding_high_pct}]"
            )

        # Regime-conditional minimum-confidence gate. Signals below
        # (MIN_SIGNAL_CONFIDENCE_FLOOR + per-regime adjustment) are dropped
        # after generation, before conflict resolution. Defaults keep the
        # gate effectively transparent (floor 0.0) except for a small
        # penalty in choppy regimes.
        def _get_env_float(var_name: str, default: float) -> float:
            """Read float from environment variable with fallback."""
            raw = os.getenv(var_name)
            if raw is None or raw == "":
                return default
            try:
                return float(raw)
            except ValueError:
                logger.warning(
                    f"Invalid float for {var_name}={raw!r}, using default {default}"
                )
                return default

        self.min_signal_confidence_floor = _get_env_float(
            "MIN_SIGNAL_CONFIDENCE_FLOOR", 0.0
        )
        self.regime_confidence_adjustments: Dict[str, float] = {
            MarketRegime.INDECISIVE.value: _get_env_float(
                "REGIME_CONF_ADJ_INDECISIVE", 0.05
            ),
            MarketRegime.RANGING_VOLATILE.value: _get_env_float(
                "REGIME_CONF_ADJ_RANGING_VOLATILE", 0.05
            ),
        }

        # Adaptive per-regime strategy weights (multiplies static weights in
        # _combine_signals). Neutral (all 1.0) when no db is wired.
        try:
            from .adaptive_weights import AdaptiveWeightManager
            from .history import TradeStore

            self.adaptive_weights = AdaptiveWeightManager(
                db=db,
                trade_store=TradeStore(db=db) if db is not None else None,
            )
        except Exception as e:
            logger.warning(f"AdaptiveWeightManager unavailable: {e}")
            self.adaptive_weights = None
        # Last effective weight map per regime (for change-detection logging)
        self._last_effective_weights: Dict[str, Dict[str, float]] = {}

        # ------------------------------------------------------------------
        # Signal discard accounting
        # ------------------------------------------------------------------
        # Signals that fail Signal.is_valid() used to be dropped with a bare
        # warning, which is indistinguishable from "the strategy generated
        # nothing". MomentumScalping sat at a 100% discard rate for months
        # because of it. These plain in-memory counters (no I/O in the hot
        # path) make the rate and the responsible flag visible via
        # get_signal_discard_stats() / log_signal_discard_summary().
        self._signal_generated_counts: Dict[str, int] = {}
        self._signal_discarded_counts: Dict[str, int] = {}
        self._signal_discard_flags: Dict[str, Dict[str, int]] = {}
        # Auto-warn every N discards per strategy so a persistent 100%
        # discard rate surfaces without anyone polling the API.
        self._discard_alert_interval = max(
            1, int(_get_env_float("SIGNAL_DISCARD_ALERT_INTERVAL", 10))
        )
        # Signal funnel (diagnostics). NullFunnel by default so the live
        # bot pays one attribute lookup and a no-op call per stage; the
        # backtest engine swaps in a real SignalFunnel via set_funnel().
        self._funnel = NULL_FUNNEL

        logger.info(
            f"Strategy enable flags: MeanReversion={self.enable_mean_reversion}, "
            f"MACrossover={self.enable_ma_crossover}, GridTrading={self.enable_grid_trading}, "
            f"LiquidationCapture={self.enable_liquidation_capture}, TrendFollowing={self.enable_trend_following}, "
            f"VWAPScalping={self.enable_vwap_scalping}, FundingArb={self.enable_funding_arb}, "
            f"MomentumScalping={self.enable_momentum_scalping}, OrderBookImbalance={self.enable_orderbook_imbalance}, "
            f"SessionRangeBreakout={self.enable_session_range_breakout}, "
            f"CalendarFlow={self.enable_calendar_flow}, "
            f"VWAPPullback={self.enable_vwap_pullback}"
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
            grid_atr_period = int(os.getenv("GRID_ATR_PERIOD", "14"))
            grid_adx_period = int(os.getenv("GRID_ADX_PERIOD", "14"))

            self.strategies["GridTrading"] = GridTradingStrategy(
                grid_levels=grid_levels,
                grid_spacing_atr_multiplier=grid_spacing,
                max_positions_per_symbol=grid_max_positions,
                emergency_stop_loss_pct=grid_emergency_stop,
                adx_regime_threshold=grid_adx_threshold,
                atr_period=grid_atr_period,
                adx_period=grid_adx_period,
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
            liq_rsi_period = int(os.getenv("LIQUIDATION_RSI_PERIOD", "14"))

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
                rsi_period=liq_rsi_period,
            )
            logger.info(
                f"Liquidation Capture strategy enabled: price threshold={liq_price_threshold:.1%}, "
                f"volume spike={liq_volume_multiplier}x, RRR target={liq_rrr_target}x"
            )

        if self.enable_vwap_scalping:
            # Load VWAP Scalping parameters from environment
            vwap_atr_period = int(os.getenv("VWAP_ATR_PERIOD", "14"))
            # The strategy re-validates this against its supported range and
            # falls back to the default if it is unreachable, so the effective
            # value is read back off the instance below rather than logged raw.
            vwap_sd_threshold = float(
                os.getenv(
                    "VWAP_SD_ENTRY_THRESHOLD", str(VWAP_DEFAULT_SD_ENTRY_THRESHOLD)
                )
            )
            vwap_atr_stop_mult = float(os.getenv("VWAP_ATR_STOP_MULTIPLIER", "1.5"))
            vwap_macd_fast = int(os.getenv("VWAP_MACD_FAST", "12"))
            vwap_macd_slow = int(os.getenv("VWAP_MACD_SLOW", "26"))
            vwap_macd_signal = int(os.getenv("VWAP_MACD_SIGNAL", "9"))
            vwap_min_confidence = float(os.getenv("VWAP_MIN_CONFIDENCE", "0.62"))
            vwap_cooldown = int(os.getenv("VWAP_COOLDOWN_MINUTES", "8"))
            vwap_sd_multipliers = [
                float(x)
                for x in os.getenv("VWAP_SD_MULTIPLIERS", "1.0,2.0,3.0").split(",")
            ]

            self.strategies["VWAPScalping"] = VWAPScalpingStrategy(
                atr_period=vwap_atr_period,
                sd_entry_threshold=vwap_sd_threshold,
                atr_stop_multiplier=vwap_atr_stop_mult,
                macd_fast=vwap_macd_fast,
                macd_slow=vwap_macd_slow,
                macd_signal=vwap_macd_signal,
                min_confidence=vwap_min_confidence,
                cooldown_minutes=vwap_cooldown,
                sd_multipliers=vwap_sd_multipliers,
            )
            effective_sd_threshold = self.strategies["VWAPScalping"].sd_entry_threshold
            logger.info(
                f"VWAP Scalping strategy enabled: SD threshold="
                f"{effective_sd_threshold} (configured {vwap_sd_threshold}), "
                f"ATR stop={vwap_atr_stop_mult}x, min_confidence={vwap_min_confidence:.0%}, "
                f"regimes={[r.value for r in self.vwap_active_regimes]}"
            )

        if self.enable_funding_arb:
            # Load Funding Arb parameters from environment
            funding_min_rate = float(
                os.getenv("FUNDING_ARB_MIN_RATE", "0.0001")
            )  # 0.01% min
            funding_max_alloc = float(
                os.getenv("FUNDING_ARB_MAX_ALLOCATION", "0.20")
            )  # 20% max
            funding_rebalance = float(
                os.getenv("FUNDING_ARB_REBALANCE_THRESHOLD", "0.02")
            )  # 2%
            funding_lookback = int(os.getenv("FUNDING_ARB_LOOKBACK_HOURS", "8"))
            funding_min_confidence = float(
                os.getenv("FUNDING_ARB_MIN_CONFIDENCE", "0.70")
            )

            # Funding interval comes from the selected exchange's
            # capabilities (Pacifica: 1h, Blofin: 8h) so the strategy's
            # rate math scales correctly per exchange.
            funding_interval_hours = get_exchange_capabilities().funding_interval_hours

            self.strategies["FundingArb"] = FundingArbStrategy(
                min_funding_rate=funding_min_rate,
                max_allocation_pct=funding_max_alloc,
                rebalance_threshold=funding_rebalance,
                lookback_hours=funding_lookback,
                min_confidence=funding_min_confidence,
                client=self.client,  # Pass exchange client for API calls
                funding_interval_hours=funding_interval_hours,
            )
            logger.info(
                f"Funding Arb strategy enabled: min_rate={funding_min_rate:.4%}, "
                f"max_alloc={funding_max_alloc:.0%}, rebalance={funding_rebalance:.1%}, "
                f"funding_interval={funding_interval_hours}h"
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
            momentum_atr_target = float(
                os.getenv("MOMENTUM_ATR_TARGET_MULTIPLIER", "2.5")
            )
            momentum_min_confidence = float(
                os.getenv("MOMENTUM_MIN_CONFIDENCE", "0.60")
            )
            momentum_cooldown = int(os.getenv("MOMENTUM_COOLDOWN_MINUTES", "5"))
            momentum_volume_mult = float(os.getenv("MOMENTUM_VOLUME_MULTIPLIER", "1.2"))
            momentum_macd_fast = int(os.getenv("MOMENTUM_MACD_FAST", "12"))
            momentum_macd_slow = int(os.getenv("MOMENTUM_MACD_SLOW", "26"))
            momentum_macd_signal = int(os.getenv("MOMENTUM_MACD_SIGNAL", "9"))
            momentum_min_atr_pct = float(os.getenv("MOMENTUM_MIN_ATR_PCT", "0.0"))
            # Minimum reward/risk for the signal's rrr_meets_minimum flag.
            # Momentum's RRR is the constant atr_target/atr_stop, so this
            # directly decides whether the strategy can trade at all.
            momentum_min_rrr = float(os.getenv("MOMENTUM_MIN_RRR", "1.5"))

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
                min_atr_pct=momentum_min_atr_pct,
                min_rrr=momentum_min_rrr,
            )
            _momentum = self.strategies["MomentumScalping"]
            _momentum_rrr = (
                _momentum.atr_target_mult / _momentum.atr_stop_mult
                if _momentum.atr_stop_mult > 0
                else 0.0
            )
            logger.info(
                f"Momentum Scalping strategy enabled: EMA {momentum_ema_fast}/{momentum_ema_slow}, "
                f"ATR stop={_momentum.atr_stop_mult}x, target={_momentum.atr_target_mult}x "
                f"(RRR {_momentum_rrr:.2f}, min_rrr={momentum_min_rrr}), "
                f"min_confidence={momentum_min_confidence:.0%}"
                + (
                    f", min_atr={momentum_min_atr_pct:.3%}"
                    if momentum_min_atr_pct > 0
                    else ""
                )
            )

        if self.enable_orderbook_imbalance:
            # Load Order Book Imbalance parameters from environment
            ob_levels = int(os.getenv("ORDERBOOK_LEVELS", "10"))
            ob_imb_long = float(os.getenv("ORDERBOOK_IMBALANCE_THRESHOLD_LONG", "0.62"))
            ob_imb_short = float(
                os.getenv("ORDERBOOK_IMBALANCE_THRESHOLD_SHORT", "0.38")
            )
            ob_strong_imb = float(os.getenv("ORDERBOOK_STRONG_IMBALANCE", "0.72"))
            ob_min_density = int(os.getenv("ORDERBOOK_MIN_ORDER_DENSITY", "5"))
            ob_spoof_detect = (
                os.getenv("ORDERBOOK_SPOOF_DETECTION", "true").lower() == "true"
            )
            ob_spoof_size_ratio = float(os.getenv("ORDERBOOK_SPOOF_SIZE_RATIO", "5.0"))
            ob_atr_period = int(os.getenv("ORDERBOOK_ATR_PERIOD", "14"))
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
                spoof_size_ratio=ob_spoof_size_ratio,
                atr_period=ob_atr_period,
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

        if self.enable_session_range_breakout:
            # Load Session Range Breakout (ORB) parameters from environment
            def _get_env_orb_bool(var_name: str, default: bool) -> bool:
                value = os.getenv(var_name, "").lower()
                if value in ("true", "1", "yes", "on"):
                    return True
                elif value in ("false", "0", "no", "off"):
                    return False
                return default

            orb_utc_open = _get_env_orb_bool("ORB_ENABLE_UTC_OPEN", True)
            orb_us_open = _get_env_orb_bool("ORB_ENABLE_US_OPEN", True)
            orb_range_minutes = int(os.getenv("ORB_RANGE_MINUTES", "30"))
            orb_entry_window = float(os.getenv("ORB_ENTRY_WINDOW_HOURS", "4"))
            orb_volume_mult = float(os.getenv("ORB_VOLUME_MULT", "1.5"))
            orb_min_range_pct = float(os.getenv("ORB_MIN_RANGE_PCT", "0.15"))
            orb_max_range_pct = float(os.getenv("ORB_MAX_RANGE_PCT", "3.0"))
            orb_tp_range_mult = float(os.getenv("ORB_TP_RANGE_MULT", "1.5"))
            orb_time_exit_hours = float(os.getenv("ORB_TIME_EXIT_HOURS", "4"))
            orb_max_trades = int(os.getenv("ORB_MAX_TRADES_PER_SESSION", "1"))
            orb_min_rrr = float(os.getenv("ORB_MIN_RRR", "1.2"))

            self.strategies["SessionRangeBreakout"] = SessionRangeBreakoutStrategy(
                enable_utc_open=orb_utc_open,
                enable_us_open=orb_us_open,
                range_minutes=orb_range_minutes,
                entry_window_hours=orb_entry_window,
                volume_mult=orb_volume_mult,
                min_range_pct=orb_min_range_pct,
                max_range_pct=orb_max_range_pct,
                tp_range_mult=orb_tp_range_mult,
                time_exit_hours=orb_time_exit_hours,
                max_trades_per_session=orb_max_trades,
                min_rrr=orb_min_rrr,
            )
            logger.info(
                f"Session Range Breakout strategy enabled: range={orb_range_minutes}min, "
                f"window={orb_entry_window}h, vol_mult={orb_volume_mult}x, "
                f"tp={orb_tp_range_mult}x range, time_exit={orb_time_exit_hours}h"
            )

        if self.enable_calendar_flow:
            # Load Calendar Flow (turn-of-the-month) parameters from environment
            def _get_env_calflow_bool(var_name: str, default: bool) -> bool:
                value = os.getenv(var_name, "").lower()
                if value in ("true", "1", "yes", "on"):
                    return True
                elif value in ("false", "0", "no", "off"):
                    return False
                return default

            calflow_long_entry = int(os.getenv("CALFLOW_LONG_ENTRY_DAY", "-2"))
            calflow_long_exit = int(os.getenv("CALFLOW_LONG_EXIT_DAY", "3"))
            calflow_enable_short = _get_env_calflow_bool("CALFLOW_ENABLE_SHORT", False)
            calflow_short_entry = int(os.getenv("CALFLOW_SHORT_ENTRY_DOM", "10"))
            calflow_short_exit = int(os.getenv("CALFLOW_SHORT_EXIT_DOM", "15"))
            calflow_atr_stop = float(os.getenv("CALFLOW_ATR_STOP_MULT", "3.0"))
            calflow_confidence = float(os.getenv("CALFLOW_CONFIDENCE", "0.6"))

            self.strategies["CalendarFlow"] = CalendarFlowStrategy(
                long_entry_day=calflow_long_entry,
                long_exit_day=calflow_long_exit,
                enable_short=calflow_enable_short,
                short_entry_dom=calflow_short_entry,
                short_exit_dom=calflow_short_exit,
                atr_stop_mult=calflow_atr_stop,
                confidence=calflow_confidence,
            )
            logger.info(
                f"Calendar Flow strategy enabled: long window "
                f"[{calflow_long_entry:+d}, {calflow_long_exit:+d}] days around "
                f"month boundary, short={calflow_enable_short}, "
                f"stop={calflow_atr_stop}x ATR(14) 4h"
            )

        if self.enable_vwap_pullback:
            # Load VWAP Pullback (trend-side continuation) parameters
            def _get_env_vpb_bool(var_name: str, default: bool) -> bool:
                value = os.getenv(var_name, "").lower()
                if value in ("true", "1", "yes", "on"):
                    return True
                elif value in ("false", "0", "no", "off"):
                    return False
                return default

            vpb_ema_fast = int(os.getenv("VWAP_PB_EMA_FAST", "20"))
            vpb_ema_slow = int(os.getenv("VWAP_PB_EMA_SLOW", "50"))
            vpb_band_sd = float(os.getenv("VWAP_PB_BAND_SD", "0.25"))
            vpb_extension = float(os.getenv("VWAP_PB_EXTENSION_MIN_SD", "1.0"))
            vpb_min_session_bars = int(os.getenv("VWAP_PB_MIN_SESSION_BARS", "8"))
            vpb_rvol_min = float(os.getenv("VWAP_PB_RVOL_MIN", "0.0"))
            vpb_rvol_window = int(os.getenv("VWAP_PB_RVOL_WINDOW", "96"))
            vpb_atr_buffer = float(os.getenv("VWAP_PB_ATR_STOP_BUFFER", "0.5"))
            vpb_tp_rr = float(os.getenv("VWAP_PB_TP_RR", "2.0"))
            vpb_time_exit = float(os.getenv("VWAP_PB_TIME_EXIT_HOURS", "24"))
            vpb_cooldown = float(os.getenv("VWAP_PB_COOLDOWN_HOURS", "4"))
            vpb_min_rrr = float(os.getenv("VWAP_PB_MIN_RRR", "1.5"))
            vpb_enable_short = _get_env_vpb_bool("VWAP_PB_ENABLE_SHORT", True)
            vpb_entry_mode = os.getenv("VWAP_PB_ENTRY_MODE", "market").strip().lower()
            vpb_maker_offset = float(os.getenv("VWAP_PB_MAKER_OFFSET_BP", "15.0"))
            vpb_entry_ttl = int(os.getenv("VWAP_PB_ENTRY_TTL_CANDLES", "12"))
            vpb_exit_mode = os.getenv("VWAP_PB_EXIT_MODE", "fixed").strip().lower()
            vpb_trail_activation = float(os.getenv("VWAP_PB_TRAIL_ACTIVATION_R", "1.0"))
            vpb_trail_r = float(os.getenv("VWAP_PB_TRAIL_R", "1.0"))

            self.strategies["VWAPPullback"] = VWAPPullbackStrategy(
                ema_fast=vpb_ema_fast,
                ema_slow=vpb_ema_slow,
                band_sd=vpb_band_sd,
                extension_min_sd=vpb_extension,
                min_session_bars=vpb_min_session_bars,
                rvol_min=vpb_rvol_min,
                rvol_window=vpb_rvol_window,
                atr_stop_buffer=vpb_atr_buffer,
                tp_rr=vpb_tp_rr,
                time_exit_hours=vpb_time_exit,
                cooldown_hours=vpb_cooldown,
                min_rrr=vpb_min_rrr,
                enable_short=vpb_enable_short,
                entry_mode=vpb_entry_mode,
                maker_offset_bp=vpb_maker_offset,
                entry_ttl_candles=vpb_entry_ttl,
                exit_mode=vpb_exit_mode,
                trail_activation_r=vpb_trail_activation,
                trail_r=vpb_trail_r,
            )
            logger.info(
                f"VWAP Pullback strategy enabled: 4h EMA {vpb_ema_fast}/"
                f"{vpb_ema_slow}, band={vpb_band_sd} SD, "
                f"extension>={vpb_extension} SD, tp={vpb_tp_rr}R, "
                f"time_exit={vpb_time_exit}h"
            )

        # Simulated time (set by backtest engine for accurate cooldown tracking)
        self._sim_time: Optional[datetime] = None

        logger.info(
            f"StrategyManager initialized with {len(self.strategies)} strategies: "
            f"{list(self.strategies.keys())}"
        )

    def set_sim_time(self, dt: datetime) -> None:
        """
        Set simulated current time for backtesting.

        Propagates to all strategy instances so internal cooldowns use candle
        timestamps instead of wall-clock time. No-op in live trading (dt=None).
        """
        self._sim_time = dt
        for strategy in self.strategies.values():
            strategy._sim_time = dt

    def should_skip_signal(self, signal: Signal) -> bool:
        """
        Check if signal should be skipped due to cooldown or other rules.

        Args:
            signal: Signal to check

        Returns:
            True if signal should be skipped, False otherwise
        """
        key = (signal.asset, signal.strategy.value)

        now = self._sim_time or datetime.now()

        # Check cooldown
        if key in self._trade_cooldowns:
            cooldown_until = self._trade_cooldowns[key]
            if now < cooldown_until:
                remaining = (cooldown_until - now).total_seconds() / 60
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
            and (now - t["timestamp"]).total_seconds() < 300
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

        now = self._sim_time or datetime.now()

        # Set cooldown (strategy-specific)
        cooldown_minutes = self._get_strategy_cooldown(signal.strategy.value)
        self._trade_cooldowns[key] = now + timedelta(minutes=cooldown_minutes)

        # Record trade
        trade_record = {
            "symbol": signal.asset,
            "strategy": signal.strategy.value,
            "side": signal.side.value,
            "quantity": order_result.get("quantity", 0),
            "price": order_result.get("price", 0),
            "timestamp": now,
            "order_id": order_result.get("id"),
            "signal_confidence": signal.confidence,
        }

        self._executed_trades.append(trade_record)

        # Keep only recent trades (last 24 hours)
        cutoff = now - timedelta(hours=24)
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
            "session_range_breakout": 60,  # 60 min - per-session dedup is the real limit
            "calendar_flow": 1440,  # 24 h - per-window dedup is the real limit
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

            # 5m is execution-timing only — strategies compute signals on 1h/4h.
            # Allow a lower floor so sparse-testnet 5m data doesn't block the symbol.
            min_length = 5 if tf == "5m" else 10
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
        funnel = self._funnel
        funnel.count(STAGE_BARS_EVALUATED)
        try:
            # Step 0: Validate data quality before proceeding
            if not self._validate_market_data(symbol, multi_tf_data):
                funnel.count(STAGE_DATA_REJECTED)
                funnel.reject(REASON_DATA_QUALITY)
                logger.warning(
                    f"{symbol}: Skipping signal generation due to data quality issues"
                )
                return []

            # Step 1: Detect market regime (prefer 4h data, fallback to 1h)
            regime_data = multi_tf_data.get("4h", multi_tf_data.get("1h", {}))

            if not regime_data or "close" not in regime_data:
                funnel.count(STAGE_DATA_REJECTED)
                funnel.reject(REASON_NO_REGIME_DATA)
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

            # Retrieve the ADX the detector just calculated so grid_trading can
            # use it directly instead of recalculating from the same raw data.
            # Will be None if the regime was served from cache (no recalculation).
            regime_adx = self.regime_detector.get_last_adx(symbol)
            funnel.record_regime(getattr(regime, "value", str(regime)))

            # Step 2: Get active strategies for current regime
            active_strategy_names = self.regime_detector.get_active_strategies(regime)

            # Step 2.25: ENFORCE GRID REGIME CONSTRAINTS (MANDATORY per Grid Trading Brief)
            # Delegated to the detector so the rule follows the active
            # taxonomy. For the ADX detector is_grid_allowed() is True for
            # exactly RANGING_CALM / RANGING_VOLATILE / INDECISIVE, i.e. the
            # hard disable still fires on TRENDING_STRONG / TRENDING_MODERATE
            # and nothing about ADX-mode behaviour changes.
            if not self.regime_detector.is_grid_allowed(regime):
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

            # Step 2.6: Add VWAPScalping only in the regimes it is mapped to.
            # VWAP is a mean-reversion strategy - SELL signals fight the trend in
            # TRENDING_STRONG / TRENDING_MODERATE and consistently hit SL, dragging PF below 1.
            # The remaining set is configurable (VWAP_ACTIVE_REGIMES); see
            # resolve_vwap_active_regimes and docs/REGIME-CENSUS.md.
            _vwap_regimes = self.vwap_active_regimes
            if (
                "VWAPScalping" in self.strategies
                and "VWAPScalping" not in active_strategy_names
                and regime in _vwap_regimes
            ):
                active_strategy_names = list(active_strategy_names) + ["VWAPScalping"]
                logger.debug(
                    f"{symbol}: Added VWAPScalping (ranging/indecisive regime)"
                )

            # Step 2.7: Always add FundingArb if enabled (passive strategy, runs in ALL regimes)
            if (
                "FundingArb" in self.strategies
                and "FundingArb" not in active_strategy_names
            ):
                active_strategy_names = list(active_strategy_names) + ["FundingArb"]
                logger.debug(
                    f"{symbol}: Added FundingArb (passive funding rate strategy, runs in all regimes)"
                )

            # Step 2.8: Add MomentumScalping if enabled and in TRENDING regimes
            if (
                "MomentumScalping" in self.strategies
                and "MomentumScalping" not in active_strategy_names
                and regime
                in [MarketRegime.TRENDING_STRONG, MarketRegime.TRENDING_MODERATE]
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

            # Step 2.10: Always add SessionRangeBreakout if enabled (time-gated, runs in ALL regimes)
            if (
                "SessionRangeBreakout" in self.strategies
                and "SessionRangeBreakout" not in active_strategy_names
            ):
                active_strategy_names = list(active_strategy_names) + [
                    "SessionRangeBreakout"
                ]
                logger.debug(
                    f"{symbol}: Added SessionRangeBreakout (time-gated ORB overlay, runs in all regimes)"
                )

            # Step 2.11: Always add CalendarFlow if enabled (calendar-gated, runs in ALL regimes)
            if (
                "CalendarFlow" in self.strategies
                and "CalendarFlow" not in active_strategy_names
            ):
                active_strategy_names = list(active_strategy_names) + ["CalendarFlow"]
                logger.debug(
                    f"{symbol}: Added CalendarFlow (calendar-gated overlay, runs in all regimes)"
                )

            # Step 2.12: Always add VWAPPullback if enabled (self-gated by
            # its own 4h EMA stack, so regime admission is redundant)
            if (
                "VWAPPullback" in self.strategies
                and "VWAPPullback" not in active_strategy_names
            ):
                active_strategy_names = list(active_strategy_names) + ["VWAPPullback"]
                logger.debug(
                    f"{symbol}: Added VWAPPullback (trend-gated overlay, runs in all regimes)"
                )

            if not active_strategy_names:
                funnel.count(STAGE_REGIME_BLOCKED)
                funnel.reject(REASON_NO_ACTIVE_STRATEGIES)
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
                funnel_key = DISPLAY_TO_STRATEGY_KEY.get(strategy_name, strategy_name)

                if not strategy:
                    funnel.reject(REASON_STRATEGY_MISSING, strategy=funnel_key)
                    logger.warning(
                        f"Strategy {strategy_name} is active but not initialized"
                    )
                    continue

                funnel.count_strategy(funnel_key, STAGE_STRATEGY_INVOKED)
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
                            funnel.reject(REASON_NO_ORDERBOOK, strategy=funnel_key)
                            logger.debug(
                                f"{symbol}: No orderbook data for OrderBookImbalance"
                            )
                            signals = []

                    # Special handling for GridTrading - pass through the regime detector's
                    # already-calculated ADX so the strategy doesn't recalculate it
                    elif strategy_name == "GridTrading":
                        try:
                            signals = strategy.generate_signals(
                                symbol,
                                multi_tf_data,
                                current_price,
                                execution_tf_data=execution_tf_data,
                                regime_adx=regime_adx,
                            )
                        except TypeError:
                            signals = strategy.generate_signals(
                                symbol, multi_tf_data, current_price
                            )

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
                        funnel.count_strategy(
                            funnel_key, STAGE_RAW_SIGNALS, len(signals)
                        )
                        logger.info(
                            f"{symbol}: {strategy_name} generated {len(signals)} signal(s) "
                            f"in {regime.value} regime"
                        )
                        self._record_generated_signals(signals)
                        all_signals.extend(signals)
                    else:
                        logger.debug(f"{symbol}: {strategy_name} returned 0 signals")

                except Exception as e:
                    funnel.reject(REASON_STRATEGY_EXCEPTION, strategy=funnel_key)
                    logger.error(f"Error in {strategy_name} for {symbol}: {e}")
                    continue

            # Step 3.4: Directional bias gate (HTF trend + funding extremes).
            # In "log" mode this only stamps the bias into each signal's
            # indicators; in "enforce" mode signals fighting the combined
            # bias additionally fail multi_timeframe_alignment and are
            # dropped (and attributed) by the existing 8-flag validation.
            all_signals = self._apply_directional_gate(
                all_signals, symbol, multi_tf_data
            )

            # Step 3.5: Regime-conditional minimum-confidence gate
            all_signals = self._apply_regime_confidence_gate(all_signals, regime)

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
                    failed = self._record_discarded_signal(signal, symbol)
                    logger.warning(
                        f"{symbol}: Single signal from "
                        f"{self._signal_strategy_key(signal)} failed validation - "
                        f"failing flags: {', '.join(failed)}"
                    )
                    return []

            # Multiple signals - resolve conflicts
            final_signals = self._resolve_signal_conflicts(all_signals, regime)
            self._count_conflict_drops(all_signals, final_signals)

            # Validate final signals (attributing every discard to its
            # strategy and the flag(s) that failed)
            valid_signals = []
            for candidate in final_signals:
                if candidate.is_valid():
                    valid_signals.append(candidate)
                    continue
                failed = self._record_discarded_signal(candidate, symbol)
                logger.warning(
                    f"{symbol}: Signal from "
                    f"{self._signal_strategy_key(candidate)} failed validation - "
                    f"failing flags: {', '.join(failed)}"
                )

            if valid_signals:
                logger.info(
                    f"{symbol}: Resolved to {len(valid_signals)} valid signal(s) "
                    f"from {len(all_signals)} candidates"
                )

            return valid_signals

        except Exception as e:
            logger.error(f"Error generating signals for {symbol}: {e}")
            return []

    # ------------------------------------------------------------------
    # Signal funnel (diagnostics)
    # ------------------------------------------------------------------

    def set_funnel(self, funnel: Any) -> None:
        """Attach a signal funnel to this manager.

        Args:
            funnel: A SignalFunnel, or None / NullFunnel to disable
                diagnostics (the default - the live bot never sets one).
        """
        self._funnel = funnel if funnel is not None else NULL_FUNNEL

    def get_funnel(self) -> Any:
        """Return the attached funnel (NULL_FUNNEL when disabled).

        Returns:
            The current funnel object.
        """
        return self._funnel

    def _count_conflict_drops(
        self, candidates: List[Signal], survivors: List[Signal]
    ) -> None:
        """Count signals removed by conflict resolution.

        Conflict resolution can also synthesise combined signals, so the
        drop set is computed by object identity rather than by length.
        Only runs on the multi-signal path, which is rare.

        Args:
            candidates: Signals entering conflict resolution.
            survivors: Signals it returned.
        """
        funnel = self._funnel
        if not funnel.enabled:
            return
        kept = {id(s) for s in survivors}
        for signal in candidates:
            if id(signal) in kept:
                continue
            name = self._signal_strategy_key(signal)
            funnel.count_strategy(name, STAGE_CONFLICT_DROPPED)
            funnel.reject(REASON_CONFLICT_RESOLUTION, strategy=name)

    # ------------------------------------------------------------------
    # Signal discard accounting
    # ------------------------------------------------------------------

    @staticmethod
    def _signal_strategy_key(signal: Signal) -> str:
        """Best-effort strategy name for a signal (used as counter key)."""
        strategy = getattr(signal, "strategy", None)
        return str(getattr(strategy, "value", strategy) or "unknown")

    def _record_generated_signals(self, signals: List[Signal]) -> None:
        """Count signals a strategy emitted, before any validation."""
        for signal in signals:
            name = self._signal_strategy_key(signal)
            self._signal_generated_counts[name] = (
                self._signal_generated_counts.get(name, 0) + 1
            )

    def _record_discarded_signal(self, signal: Signal, symbol: str) -> List[str]:
        """
        Record a signal dropped by Signal.is_valid() and return the failing flags.

        Counts the discard per strategy and per failing validity flag so a
        strategy that never trades looks different from one that never fires.
        """
        failed = signal.failed_validity_flags()
        name = self._signal_strategy_key(signal)

        total = self._signal_discarded_counts.get(name, 0) + 1
        self._signal_discarded_counts[name] = total

        flags = self._signal_discard_flags.setdefault(name, {})
        funnel = self._funnel
        funnel.count_strategy(name, STAGE_VALIDITY_DROPPED)
        for flag in failed:
            flags[flag] = flags.get(flag, 0) + 1
            # Interned constant, never an f-string (hot path).
            funnel.reject(REASON_VALIDITY[flag], strategy=name)

        if total % self._discard_alert_interval == 0:
            generated = self._signal_generated_counts.get(name, total)
            rate = total / generated if generated else 1.0
            top = sorted(flags.items(), key=lambda kv: kv[1], reverse=True)
            top_desc = ", ".join(f"{k}={v}" for k, v in top[:3])
            logger.warning(
                f"Signal discard alert: {name} has discarded {total}/{generated} "
                f"signals ({rate:.0%}) as invalid; top failing flags: {top_desc}"
            )

        logger.debug(f"{symbol}: {name} signal discarded on {failed}")
        return failed

    def get_signal_discard_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        Per-strategy accounting of signals dropped by Signal.is_valid().

        Returns a dict keyed by strategy name:

            {
                "momentum_scalping": {
                    "generated": 46,       # signals the strategy emitted
                    "discarded": 46,       # of those, failed is_valid()
                    "discard_rate": 1.0,   # discarded / generated
                    "failed_flags": {"rrr_meets_minimum": 46},
                },
                ...
            }

        Note: signals removed by conflict resolution are NOT counted as
        discards - they never reach the validity check. A discard_rate of
        1.0 means the strategy is structurally unable to trade.
        """
        names = set(self._signal_generated_counts) | set(self._signal_discarded_counts)
        stats: Dict[str, Dict[str, Any]] = {}
        for name in sorted(names):
            generated = self._signal_generated_counts.get(name, 0)
            discarded = self._signal_discarded_counts.get(name, 0)
            stats[name] = {
                "generated": generated,
                "discarded": discarded,
                "discard_rate": (discarded / generated) if generated else 0.0,
                "failed_flags": dict(self._signal_discard_flags.get(name, {})),
            }
        return stats

    def log_signal_discard_summary(self) -> Dict[str, Dict[str, Any]]:
        """
        Log one line per strategy summarising validation discards.

        Strategies with a 100% discard rate are logged at WARNING; the rest
        at INFO. Returns the same dict as get_signal_discard_stats().
        """
        stats = self.get_signal_discard_stats()
        if not stats:
            logger.info("Signal discard summary: no signals generated yet")
            return stats

        for name, entry in stats.items():
            flags = entry["failed_flags"]
            flag_desc = (
                ", ".join(
                    f"{k}={v}"
                    for k, v in sorted(
                        flags.items(), key=lambda kv: kv[1], reverse=True
                    )
                )
                or "none"
            )
            message = (
                f"Signal discard summary: {name} generated={entry['generated']} "
                f"discarded={entry['discarded']} "
                f"({entry['discard_rate']:.0%}) failing_flags=[{flag_desc}]"
            )
            if entry["generated"] > 0 and entry["discard_rate"] >= 1.0:
                logger.warning(message + " - strategy cannot trade with this config")
            else:
                logger.info(message)
        return stats

    def reset_signal_discard_stats(self) -> None:
        """Clear all discard counters (used by tests and long-running sessions)."""
        self._signal_generated_counts.clear()
        self._signal_discarded_counts.clear()
        self._signal_discard_flags.clear()

    def _apply_directional_gate(
        self,
        signals: List[Signal],
        symbol: str,
        multi_tf_data: Dict[str, Dict[str, List[float]]],
    ) -> List[Signal]:
        """
        Apply the directional bias gate (DIRECTIONAL_GATE env).

        "off": returns the signals untouched without computing anything.
        "log": computes the bias once per call and stamps it into every
        signal's indicators["directional_bias"] so the census can measure
        how the gate WOULD have voted, without changing a single trade.
        "enforce": as "log", plus non-exempt signals whose side fights the
        combined bias get multi_timeframe_alignment=False - the existing
        validation then drops them with per-flag discard attribution.

        Args:
            signals: Generated signals for a symbol.
            symbol: Trading symbol.
            multi_tf_data: Per-timeframe OHLCV bundles (trend leg input).

        Returns:
            The same signal list (enforce mode marks, never removes -
            removal happens downstream in the 8-flag validation).
        """
        mode = self.directional_gate_mode
        if mode == GATE_OFF or not signals:
            return signals

        try:
            bias = self.directional_bias_engine.compute(
                symbol, multi_tf_data, now=self._sim_time
            )
        except Exception as e:
            logger.warning(f"{symbol}: directional bias computation failed: {e}")
            return signals

        stamp = bias.to_indicator()
        for signal in signals:
            if signal.indicators is None:
                signal.indicators = {}
            signal.indicators["directional_bias"] = dict(stamp)

            if mode != GATE_ENFORCE:
                continue
            display_name = self._get_strategy_display_name(signal.strategy)
            if display_name in self.directional_gate_exempt:
                continue
            if not bias.allows(signal.side):
                signal.multi_timeframe_alignment = False
                logger.info(
                    f"Directional gate: {display_name} {signal.side.value} "
                    f"{symbol} fights bias "
                    f"(trend={bias.trend}, funding={bias.funding}, "
                    f"combined={bias.combined}) - failing "
                    f"multi_timeframe_alignment"
                )
            elif (
                bias.combined == "neutral"
                and display_name in self.directional_neutral_exclude
            ):
                # Neutral-state exclusion (DIRECTIONAL_NEUTRAL_EXCLUDE):
                # this strategy measured as a net loser whenever no
                # directional bias exists, so it stands aside in chop.
                signal.multi_timeframe_alignment = False
                logger.info(
                    f"Directional gate: {display_name} {symbol} excluded "
                    f"in neutral bias state (DIRECTIONAL_NEUTRAL_EXCLUDE)"
                )

        return signals

    def _apply_regime_confidence_gate(
        self, signals: List[Signal], regime: MarketRegime
    ) -> List[Signal]:
        """
        Drop signals below the regime-conditional minimum confidence.

        The threshold is MIN_SIGNAL_CONFIDENCE_FLOOR (env, default 0.0)
        plus a per-regime adjustment (REGIME_CONF_ADJ_INDECISIVE and
        REGIME_CONF_ADJ_RANGING_VOLATILE, both default 0.05; other regimes
        0.0). Applied after signal generation and before conflict
        resolution; each strategy's own minimum-confidence checks still run
        inside the strategies themselves.

        Args:
            signals: Generated signals for a symbol.
            regime: Current market regime.

        Returns:
            Signals whose confidence meets the threshold (each drop is
            logged at INFO with strategy, symbol, confidence, threshold).
        """
        if not signals:
            return signals

        regime_key = str(getattr(regime, "value", regime)).lower()
        adjustment = self.regime_confidence_adjustments.get(regime_key, 0.0)
        threshold = self.min_signal_confidence_floor + adjustment
        if threshold <= 0:
            return signals

        funnel = self._funnel
        kept: List[Signal] = []
        for signal in signals:
            if signal.confidence < threshold:
                strategy_name = getattr(signal.strategy, "value", str(signal.strategy))
                funnel.count_strategy(strategy_name, STAGE_CONFIDENCE_DROPPED)
                funnel.reject(REASON_CONFIDENCE_GATE, strategy=strategy_name)
                logger.info(
                    f"Confidence gate: dropping {strategy_name} {signal.asset} "
                    f"signal - confidence {signal.confidence:.2%} < threshold "
                    f"{threshold:.2%} (floor {self.min_signal_confidence_floor:.2%} "
                    f"+ {regime_key} adj {adjustment:.2%})"
                )
            else:
                kept.append(signal)
        return kept

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
        # Extract grid signals from all signals (may include non-grid signals)
        all_signals = buy_signals + sell_signals
        grid_buy = [
            s
            for s in all_signals
            if s.strategy == StrategyType.GRID_TRADING and s.side == OrderSide.BUY
        ]
        grid_sell = [
            s
            for s in all_signals
            if s.strategy == StrategyType.GRID_TRADING and s.side == OrderSide.SELL
        ]

        # If no grid signals at all, fall through to normal conflict resolution
        if not grid_buy and not grid_sell:
            return None

        if len(grid_buy) != 1 or len(grid_sell) != 1:
            logger.warning(
                f"Grid handler: Invalid grid setup - expected 1 BUY + 1 SELL, "
                f"got {len(grid_buy)} BUY + {len(grid_sell)} SELL"
            )
            return None

        buy_signal = grid_buy[0]
        sell_signal = grid_sell[0]

        # Non-grid signals are present but grid takes priority in ranging regime.
        # Log the override and proceed with the grid pair.
        non_grid = [s for s in all_signals if s.strategy != StrategyType.GRID_TRADING]
        if non_grid:
            logger.debug(
                f"Grid handler: Grid pair takes priority over "
                f"{len(non_grid)} non-grid signal(s) ({[s.strategy.value for s in non_grid]})"
            )

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

        # Additional regime-specific validation. Same delegation as the
        # Step 2.25 hard disable - identical behaviour under the ADX
        # taxonomy, correct under any other.
        if not self.regime_detector.is_grid_allowed(regime):
            logger.warning(
                f"Grid handler: Grid signals not allowed in {regime.value} "
                f"regime - rejecting"
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

        # Get regime-based strategy weights (static base weights scaled by
        # adaptive per-regime performance multipliers when enabled)
        strategy_weights = self.regime_detector.get_strategy_weights(regime)
        strategy_weights = self._apply_adaptive_weights(strategy_weights, regime)

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

    def _apply_adaptive_weights(
        self, static_weights: Dict[str, float], regime: MarketRegime
    ) -> Dict[str, float]:
        """
        Scale static regime weights by adaptive performance multipliers.

        final_weight = static_weight * adaptive_multiplier(regime, strategy).
        Behind ENABLE_ADAPTIVE_WEIGHTS (default true); neutral when the
        adaptive manager is missing, disabled, or has no db wired. The
        effective weight map is logged at DEBUG whenever it changes.

        Args:
            static_weights: Static strategy -> weight map for the regime.
            regime: Current market regime.

        Returns:
            Effective strategy -> weight map (static values on any failure).
        """
        manager = self.adaptive_weights
        if manager is None or not manager.enabled or not static_weights:
            return static_weights

        try:
            regime_key = str(getattr(regime, "value", regime)).lower()
            effective = {
                name: weight * manager.get_multiplier(regime_key, name)
                for name, weight in static_weights.items()
            }
        except Exception as e:
            logger.warning(f"Adaptive weight application failed: {e}")
            return static_weights

        rounded = {name: round(w, 6) for name, w in effective.items()}
        if self._last_effective_weights.get(regime_key) != rounded:
            self._last_effective_weights[regime_key] = rounded
            logger.debug(
                f"Effective strategy weights changed for {regime_key}: "
                f"static={static_weights} -> effective={rounded}"
            )
        return effective

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
            StrategyType.SESSION_RANGE_BREAKOUT: "SessionRangeBreakout",
            StrategyType.CALENDAR_FLOW: "CalendarFlow",
            StrategyType.VWAP_PULLBACK: "VWAPPullback",
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
            "SessionRangeBreakout": StrategyType.SESSION_RANGE_BREAKOUT,
            "CalendarFlow": StrategyType.CALENDAR_FLOW,
            "VWAPPullback": StrategyType.VWAP_PULLBACK,
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
