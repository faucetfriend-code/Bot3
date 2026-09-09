"""
VWAP Scalping Strategy (Volume-Weighted Average Price Mean Reversion)

TIMEFRAME HIERARCHY (Multi-TF Execution Model):
- PERMISSION (1h/4h): Regime detection (handled by StrategyManager)
- STRUCTURE (15m): VWAP + SD bands establish reversion zones
- TRIGGER (5m): MACD histogram confirmation for entry timing
- TIMING (1m): ExecutionLayer handles precise entry

Strategy Logic:
- VWAP calculated as ROLLING CUMULATIVE (price * volume) / cumulative volume
  over the supplied 15m window (no daily session anchor - see CONFIG NOTES)
- Standard deviation bands at 1SD, 2SD, 3SD levels
- BUY Signal: Price deviates below VWAP by at least sd_entry_threshold SD
  + an entry confirmation (see entry_confirmation)
- SELL Signal: Price deviates above VWAP by at least sd_entry_threshold SD
  + an entry confirmation (see entry_confirmation)
- Stop Loss: see stop_source - ATR-based or beyond the next SD band
- Take Profit: Return to VWAP (mean reversion target)

TWO MEASURED DEFECTS, NOW SWITCHABLE (2026-07-29)
-------------------------------------------------
Both defaults below reproduce the behaviour measured in docs/VWAP-LEVERS.md
(18.1% win rate, 82% of exits via the stop, PF 0.74 over 4851 BTC trades).
They are kept as defaults only so the A/B that replaces them is honest.

1. ``stop_source`` - the shipped stop is 1.5x the **1m** ATR even though the
   setup is 15m structure. Measured on BTC 2022-07..2024-07 at the 6538 bars
   that clear a 2.0 SD gate: median stop 0.138% of price against a median
   take-profit distance of 1.133%, and the stop is smaller than the *current
   15m bar's own high-low range* on 99.3% of signals. ATR(15m) is 5.08x
   ATR(1m). The docstring above always claimed a band-based stop; that branch
   was never written. ``atr_structure`` and ``sd_band`` supply it.

2. ``entry_confirmation`` - the shipped MACD gate requires the histogram to
   still be moving AGAINST the trade (negative for a BUY) and explicitly
   rejects a histogram that has already turned as a "late entry". Combined
   with (1) that is maximal adverse selection into a stop inside the noise.
   ``turning`` requires the histogram to be inflecting toward the trade
   instead; ``none`` removes the gate entirely as a control.

Best For: ALL regimes (overlay strategy, strongest in RANGING_*)
Expected Performance: 55-68% win rate, 1.4-2.1 profit factor

CONFIG NOTES:
- sd_entry_threshold MUST stay inside [SD_ENTRY_THRESHOLD_MIN,
  SD_ENTRY_THRESHOLD_MAX]. Because the VWAP here is rolling cumulative rather
  than daily-session-anchored, E[|price - vwap| / sigma] is approximately 1.0
  and the empirical maximum over six months of BTC/ETH/SUI data is ~4.0.
  A threshold at or above ~4 is therefore unreachable and silently produces
  zero signals forever. Out-of-range values are rejected at construction time
  by validate_sd_entry_threshold().
- Several VWAP_* environment variables are NOT read by this strategy (see
  UNSUPPORTED_ENV_VARS). They are leftovers from the standalone BTV2 tuning
  harness, which used a different entry model; setting them has no effect.
- rsi_oversold / rsi_overbought are accepted and stored (the optimizer search
  space tunes them) but do NOT gate entries today - RSI only annotates the
  signal notes. Treat them as reserved, not active filters.

VALIDATION HISTORY:
- Regime-aware walkforward validation failed on all 7 OOS years; the strategy
  was negative in 6/7 years at 15m with no ROBUST MC verdict. Those runs are
  NOT authoritative for the current code, because the threshold in use at the
  time (4.037) made the entry gate unreachable, so the runs measured a
  strategy that never traded. Re-validate before trusting live.

RISK NOTES:
- Delta-neutral reduces directional risk but not basis risk
- High fee sensitivity on 1m/5m - use limit orders when possible
- Cooldown enforced to prevent over-trading on noise
"""

import os
from typing import List, Dict, Optional
from datetime import datetime, timedelta, timezone
from loguru import logger

# Use relative imports from trading_bot_v2 package
from ..models import Signal, OrderSide
from ..diagnostics.gate_metrics import GATE_AT_LEAST, GateMetric
from ..indicators import (
    calculate_atr,
    calculate_macd,
    calculate_rsi,
)
from ..config import StrategyType, AssetClass, TradeQuality, MarketState


# ---------------------------------------------------------------------------
# Configuration bounds for sd_entry_threshold
# ---------------------------------------------------------------------------
# These mirror the optimizer search space in
# trading_bot_v2/optimization/search_spaces.py::_vwap_scalping_space().
# Keep the two in sync - test_vwap_config_guard asserts they match.
SD_ENTRY_THRESHOLD_MIN = 1.0
SD_ENTRY_THRESHOLD_MAX = 3.0

# Interim default, pending re-optimization inside the bounds above.
# The previously shipped value of 4.037 was tuned in the standalone BTV2
# harness, which uses a DAILY SESSION-ANCHORED VWAP whose sigma resets at
# 00:00 UTC, so 4-sigma excursions are routine early in a session. This
# strategy uses a rolling cumulative VWAP where the deviation distribution is
# far tighter (mean ~1.0 SD, max ~4.0 SD over six months of 5m data), so
# 4.037 was unreachable and the entry gate never fired.
DEFAULT_SD_ENTRY_THRESHOLD = 2.0

# ---------------------------------------------------------------------------
# Stop-loss placement (VWAP_STOP_SOURCE)
# ---------------------------------------------------------------------------
# "atr_execution" is the shipped behaviour and is kept as the default so that
# enabling the alternatives is an explicit, measurable act. See the module
# docstring for why it is almost certainly wrong.
STOP_SOURCE_ATR_EXECUTION = "atr_execution"  # 1.5x ATR(1m) when 1m is supplied
STOP_SOURCE_ATR_STRUCTURE = "atr_structure"  # 1.5x ATR of the 15m setup TF
STOP_SOURCE_SD_BAND = "sd_band"  # beyond the next whole SD band out
VALID_STOP_SOURCES = (
    STOP_SOURCE_ATR_EXECUTION,
    STOP_SOURCE_ATR_STRUCTURE,
    STOP_SOURCE_SD_BAND,
)
DEFAULT_STOP_SOURCE = STOP_SOURCE_ATR_EXECUTION

# ---------------------------------------------------------------------------
# Entry confirmation (VWAP_ENTRY_CONFIRMATION)
# ---------------------------------------------------------------------------
# "adverse" is the shipped behaviour: enter while MACD momentum still runs
# against the trade. "turning" waits for the histogram to inflect toward it.
# "none" drops the gate so the deviation signal can be measured on its own.
ENTRY_CONFIRMATION_ADVERSE = "adverse"
ENTRY_CONFIRMATION_TURNING = "turning"
ENTRY_CONFIRMATION_NONE = "none"
VALID_ENTRY_CONFIRMATIONS = (
    ENTRY_CONFIRMATION_ADVERSE,
    ENTRY_CONFIRMATION_TURNING,
    ENTRY_CONFIRMATION_NONE,
)
DEFAULT_ENTRY_CONFIRMATION = ENTRY_CONFIRMATION_ADVERSE

# VWAP_* environment variables this strategy does not read. They are leftovers
# from the BTV2 tuning harness (a different entry model) and have no effect
# here. Warned about at construction so a stale .env cannot silently mislead.
UNSUPPORTED_ENV_VARS = (
    "VWAP_ATR_TARGET_MULTIPLIER",
    "VWAP_ATR_TRAILING_MULTIPLIER",
    "VWAP_RSI_MAX",
    "VWAP_ENTRY_MODE",
    "VWAP_TP_MODE",
    "VWAP_USE_HTF_EMA",
    "VWAP_HTF_ADX_MAX",
    "VWAP_USE_HTF_VWAP",
    "VWAP_USE_SESSION_FILTER",
    "VWAP_REQUIRE_REVERSAL_CANDLE",
    "VWAP_USE_STOCH_FILTER",
)


def validate_sd_entry_threshold(value: float) -> float:
    """
    Validate an sd_entry_threshold against the supported range.

    An out-of-range threshold does not degrade the strategy, it disables it:
    generate_signals() returns early whenever the observed deviation is below
    the threshold, and a threshold above SD_ENTRY_THRESHOLD_MAX is unreachable
    for a rolling cumulative VWAP. That failure mode is silent, so warn loudly
    and fall back to the validated default rather than never trade.

    Falling back to the default (instead of raising) matches how the rest of
    the codebase handles bad config - see the _get_env_float helpers in
    strategy_manager.py, market_regime.py and risk_manager.py - and avoids
    taking a running bot down over a single tuning parameter.

    Args:
        value: Configured threshold, in standard deviations.

    Returns:
        The value if it is inside the supported range, else
        DEFAULT_SD_ENTRY_THRESHOLD.
    """
    if SD_ENTRY_THRESHOLD_MIN <= value <= SD_ENTRY_THRESHOLD_MAX:
        return value

    logger.warning(
        f"VWAP_SD_ENTRY_THRESHOLD={value} is outside the supported range "
        f"[{SD_ENTRY_THRESHOLD_MIN}, {SD_ENTRY_THRESHOLD_MAX}] used by the "
        f"optimizer search space. Values above the upper bound are unreachable "
        f"for this strategy's rolling cumulative VWAP (observed max deviation "
        f"is ~4.0 SD) and produce ZERO signals. Falling back to "
        f"{DEFAULT_SD_ENTRY_THRESHOLD}. Fix VWAP_SD_ENTRY_THRESHOLD in .env."
    )
    return DEFAULT_SD_ENTRY_THRESHOLD


def validate_stop_source(value: str) -> str:
    """
    Validate a stop-loss placement mode.

    Args:
        value: Requested mode, case-insensitive.

    Returns:
        The mode if recognised, else DEFAULT_STOP_SOURCE.
    """
    normalised = (value or "").strip().lower()
    if normalised in VALID_STOP_SOURCES:
        return normalised

    logger.warning(
        f"VWAP_STOP_SOURCE={value!r} is not one of "
        f"{', '.join(VALID_STOP_SOURCES)}. Falling back to "
        f"{DEFAULT_STOP_SOURCE}."
    )
    return DEFAULT_STOP_SOURCE


def validate_entry_confirmation(value: str) -> str:
    """
    Validate an entry-confirmation mode.

    Args:
        value: Requested mode, case-insensitive.

    Returns:
        The mode if recognised, else DEFAULT_ENTRY_CONFIRMATION.
    """
    normalised = (value or "").strip().lower()
    if normalised in VALID_ENTRY_CONFIRMATIONS:
        return normalised

    logger.warning(
        f"VWAP_ENTRY_CONFIRMATION={value!r} is not one of "
        f"{', '.join(VALID_ENTRY_CONFIRMATIONS)}. Falling back to "
        f"{DEFAULT_ENTRY_CONFIRMATION}."
    )
    return DEFAULT_ENTRY_CONFIRMATION


def warn_unsupported_env_vars() -> List[str]:
    """
    Warn about VWAP_* environment variables that are set but never read.

    Returns:
        The names of the unread variables that are currently set.
    """
    ignored = [name for name in UNSUPPORTED_ENV_VARS if os.getenv(name)]
    if ignored:
        logger.warning(
            "VWAP scalping: these environment variables are set but NOT read "
            f"by this strategy and have no effect: {', '.join(ignored)}. "
            "They are leftovers from the BTV2 tuning harness; remove them "
            "from .env to avoid confusion."
        )
    return ignored


class VWAPScalpingStrategy:
    """
    VWAP Scalping strategy using Volume-Weighted Average Price with SD bands.

    Captures mean reversion when price deviates significantly from VWAP.
    Uses MACD confirmation on 5m to filter for high-probability reversals.

    Parameters can be configured via environment variables:
    - VWAP_ATR_PERIOD (default: 14)
    - VWAP_SD_ENTRY_THRESHOLD (default: 2.0, must be within
      [SD_ENTRY_THRESHOLD_MIN, SD_ENTRY_THRESHOLD_MAX])
    - VWAP_ATR_STOP_MULTIPLIER (default: 1.5)
    - VWAP_MACD_FAST (default: 12)
    - VWAP_MACD_SLOW (default: 26)
    - VWAP_MACD_SIGNAL (default: 9)
    - VWAP_RSI_PERIOD (default: 14)
    - VWAP_RSI_OVERSOLD (default: 35.0)
    - VWAP_RSI_OVERBOUGHT (default: 65.0)
    - VWAP_MIN_CONFIDENCE (default: 0.62)
    - VWAP_COOLDOWN_MINUTES (default: 8)
    """

    def __init__(
        self,
        atr_period: Optional[int] = None,
        sd_entry_threshold: Optional[float] = None,
        atr_stop_multiplier: Optional[float] = None,
        macd_fast: Optional[int] = None,
        macd_slow: Optional[int] = None,
        macd_signal: Optional[int] = None,
        rsi_period: Optional[int] = None,
        rsi_oversold: Optional[float] = None,
        rsi_overbought: Optional[float] = None,
        min_confidence: Optional[float] = None,
        cooldown_minutes: Optional[int] = None,
        sd_multipliers: Optional[List[float]] = None,
        stop_source: Optional[str] = None,
        entry_confirmation: Optional[str] = None,
    ):
        """
        Initialize VWAP Scalping Strategy.

        Parameters are read from environment variables if not explicitly passed.

        Args:
            atr_period: ATR period for stop loss calculation (default: 14)
            sd_entry_threshold: Minimum SD deviation to trigger entry
                (default: 2.0). Rejected and replaced by the default when
                outside [SD_ENTRY_THRESHOLD_MIN, SD_ENTRY_THRESHOLD_MAX].
            atr_stop_multiplier: ATR multiplier for stop loss (default: 1.5)
            macd_fast: MACD fast period (default: 12)
            macd_slow: MACD slow period (default: 26)
            macd_signal: MACD signal period (default: 9)
            rsi_period: RSI calculation period (default: 14)
            rsi_oversold: RESERVED (default: 35.0). Stored and exposed to the
                optimizer search space, but does NOT gate entries today.
            rsi_overbought: RESERVED (default: 65.0). Same as rsi_oversold.
            min_confidence: Minimum confidence threshold (default: 0.62)
            cooldown_minutes: Cooldown between trades per symbol (default: 8)
            sd_multipliers: SD band multipliers to compute (default: [1.0, 2.0, 3.0])
            stop_source: Where the stop comes from - "atr_execution" (default,
                shipped: 1.5x ATR of the 1m series when supplied),
                "atr_structure" (1.5x ATR of the 15m setup timeframe) or
                "sd_band" (beyond the next whole SD band out from VWAP).
            entry_confirmation: MACD gate - "adverse" (default, shipped: the
                histogram must still run against the trade), "turning" (the
                histogram must be inflecting toward it) or "none".
        """
        self.strategy_type = StrategyType.VWAP_SCALPING

        # Read from environment with defaults
        self.atr_period = (
            atr_period
            if atr_period is not None
            else int(os.getenv("VWAP_ATR_PERIOD", "14"))
        )
        # Validated at construction: an unreachable threshold silently
        # disables the strategy, so it must never be accepted quietly.
        self.sd_entry_threshold = validate_sd_entry_threshold(
            sd_entry_threshold
            if sd_entry_threshold is not None
            else float(
                os.getenv("VWAP_SD_ENTRY_THRESHOLD", str(DEFAULT_SD_ENTRY_THRESHOLD))
            )
        )
        self.atr_stop_multiplier = (
            atr_stop_multiplier
            if atr_stop_multiplier is not None
            else float(os.getenv("VWAP_ATR_STOP_MULTIPLIER", "1.5"))
        )
        self.macd_fast = (
            macd_fast
            if macd_fast is not None
            else int(os.getenv("VWAP_MACD_FAST", "12"))
        )
        self.macd_slow = (
            macd_slow
            if macd_slow is not None
            else int(os.getenv("VWAP_MACD_SLOW", "26"))
        )
        self.macd_signal = (
            macd_signal
            if macd_signal is not None
            else int(os.getenv("VWAP_MACD_SIGNAL", "9"))
        )
        self.rsi_period = (
            rsi_period
            if rsi_period is not None
            else int(os.getenv("VWAP_RSI_PERIOD", "14"))
        )
        # RESERVED: stored so the optimizer search space stays intact, but
        # neither value gates entries in generate_signals() - RSI is only
        # reported in the signal notes. See CONFIG NOTES in the module
        # docstring before assuming these filter anything.
        self.rsi_oversold = (
            rsi_oversold
            if rsi_oversold is not None
            else float(os.getenv("VWAP_RSI_OVERSOLD", "35.0"))
        )
        self.rsi_overbought = (
            rsi_overbought
            if rsi_overbought is not None
            else float(os.getenv("VWAP_RSI_OVERBOUGHT", "65.0"))
        )
        self.min_confidence = (
            min_confidence
            if min_confidence is not None
            else float(os.getenv("VWAP_MIN_CONFIDENCE", "0.62"))
        )
        self.cooldown_minutes = (
            cooldown_minutes
            if cooldown_minutes is not None
            else int(os.getenv("VWAP_COOLDOWN_MINUTES", "8"))
        )

        # SD band multipliers: [1.0, 2.0, 3.0] for 1SD, 2SD, 3SD
        self.sd_multipliers = sd_multipliers or [1.0, 2.0, 3.0]

        # Both default to the shipped behaviour; see the module docstring for
        # the measurements that say both defaults are probably wrong.
        self.stop_source = validate_stop_source(
            stop_source
            if stop_source is not None
            else os.getenv("VWAP_STOP_SOURCE", DEFAULT_STOP_SOURCE)
        )
        self.entry_confirmation = validate_entry_confirmation(
            entry_confirmation
            if entry_confirmation is not None
            else os.getenv("VWAP_ENTRY_CONFIRMATION", DEFAULT_ENTRY_CONFIRMATION)
        )

        # Track cooldowns per symbol
        self._last_trade_time: Dict[str, datetime] = {}

        # Simulated time injected by backtest engine (None = use wall-clock)
        self._sim_time: Optional[datetime] = None

        logger.info(
            f"VWAPScalpingStrategy initialized: "
            f"SD threshold={self.sd_entry_threshold}, "
            f"ATR stop={self.atr_stop_multiplier}x, "
            f"MACD({self.macd_fast},{self.macd_slow},{self.macd_signal}), "
            f"min_confidence={self.min_confidence}, "
            f"cooldown={self.cooldown_minutes}min, "
            f"stop_source={self.stop_source}, "
            f"entry_confirmation={self.entry_confirmation}"
        )

        # Surface stale .env knobs that this strategy never reads.
        warn_unsupported_env_vars()

    def _calculate_vwap_and_bands(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        volumes: List[float],
    ) -> Optional[Dict[str, float]]:
        """
        Calculate VWAP and standard deviation bands.

        VWAP = cumulative(price * volume) / cumulative(volume)
        SD bands use volume-weighted variance for accurate statistical levels.

        Args:
            highs: List of high prices
            lows: List of low prices
            closes: List of close prices
            volumes: List of volume values

        Returns:
            Dict with vwap, stddev, and band levels, or None if insufficient data
        """
        min_length = 20  # Minimum candles for reliable VWAP

        if len(closes) < min_length:
            return None

        if not volumes or len(volumes) != len(closes):
            return None

        # Calculate typical price: (High + Low + Close) / 3
        typical_prices = []
        for i in range(len(closes)):
            typical = (highs[i] + lows[i] + closes[i]) / 3
            typical_prices.append(typical)

        # Calculate cumulative sums for VWAP
        cum_pv = 0.0  # cumulative (price * volume)
        cum_vol = 0.0  # cumulative volume
        cum_price_sq_vol = 0.0  # cumulative (price^2 * volume) for variance

        vwap_values = []

        for i in range(len(typical_prices)):
            cum_pv += typical_prices[i] * volumes[i]
            cum_vol += volumes[i]
            cum_price_sq_vol += (typical_prices[i] ** 2) * volumes[i]

            if cum_vol > 0:
                vwap = cum_pv / cum_vol
                vwap_values.append(vwap)
            else:
                vwap_values.append(typical_prices[i])

        if not vwap_values:
            return None

        # Current VWAP (most recent)
        current_vwap = vwap_values[-1]

        # Calculate volume-weighted variance
        # variance = [cumulative(vol * price^2) / cumulative(vol)] - vwap^2
        if cum_vol > 0:
            variance = (cum_price_sq_vol / cum_vol) - (current_vwap**2)
            # Clip negative variance (floating-point errors)
            variance = max(0.0, variance)
            stddev = variance**0.5
        else:
            stddev = 0.0

        if stddev == 0:
            # Zero stddev means no price variation - use ATR-based fallback
            return None

        # Build result with bands
        result = {
            "vwap": current_vwap,
            "stddev": stddev,
        }

        # Add SD bands
        for mult in self.sd_multipliers:
            result[f"sd{mult}_upper"] = current_vwap + mult * stddev
            result[f"sd{mult}_lower"] = current_vwap - mult * stddev

        return result

    def _now(self) -> datetime:
        """Return current time — simulated candle time in backtesting, wall-clock in live."""
        return (
            self._sim_time if self._sim_time is not None else datetime.now(timezone.utc)
        )

    def _check_cooldown(self, symbol: str) -> bool:
        """
        Check if symbol is in cooldown period.

        Args:
            symbol: Trading symbol

        Returns:
            True if in cooldown (should skip), False if ready to trade
        """
        if symbol not in self._last_trade_time:
            return False

        elapsed = self._now() - self._last_trade_time[symbol]
        cooldown_delta = timedelta(minutes=self.cooldown_minutes)

        if elapsed < cooldown_delta:
            remaining = (cooldown_delta - elapsed).total_seconds() / 60
            logger.debug(
                f"{symbol}: VWAP scalping cooldown {remaining:.1f}min remaining"
            )
            return True

        return False

    def _set_cooldown(self, symbol: str):
        """Set cooldown for symbol after trade signal."""
        self._last_trade_time[symbol] = self._now()

    def _entry_confirmed(
        self,
        side: OrderSide,
        histogram: float,
        prev_histogram: Optional[float],
    ) -> bool:
        """
        Apply the configured MACD entry confirmation.

        The three modes encode genuinely different theories of the setup:

        - ``adverse`` (shipped): momentum must still be running against the
          trade. The original comment called this "sellers exhausted", but a
          negative histogram is the opposite of exhaustion - it is the down
          move still accelerating. Kept because it is what every measurement
          in docs/VWAP-LEVERS.md was taken against.
        - ``turning``: the histogram must be inflecting toward the trade,
          which is the confirmation a mean-reversion entry normally waits
          for. This is the case ``adverse`` explicitly rejects.
        - ``none``: no momentum gate; isolates the deviation signal.

        Args:
            side: Proposed trade direction.
            histogram: Current MACD histogram.
            prev_histogram: Previous bar's histogram; required by ``turning``
                and ignored otherwise.

        Returns:
            True when the entry may proceed.
        """
        if self.entry_confirmation == ENTRY_CONFIRMATION_NONE:
            return True

        if self.entry_confirmation == ENTRY_CONFIRMATION_TURNING:
            if prev_histogram is None:
                return False
            if side == OrderSide.BUY:
                return histogram > prev_histogram
            return histogram < prev_histogram

        # ENTRY_CONFIRMATION_ADVERSE
        if side == OrderSide.BUY:
            return histogram < 0
        return histogram > 0

    def _resolve_stop_loss(
        self,
        side: OrderSide,
        current_price: float,
        atr: float,
        vwap: float,
        stddev: float,
        deviation_sd: float,
    ) -> float:
        """
        Place the stop according to ``stop_source``.

        ``sd_band`` implements the placement this module's docstring always
        described but never had: the stop sits beyond the next whole SD band
        outward from VWAP, so it scales with the same dispersion measure the
        entry is gated on rather than with an unrelated ATR.

        Args:
            side: Trade direction.
            current_price: Entry price.
            atr: ATR already computed from the series ``stop_source`` selects.
            vwap: Current VWAP.
            stddev: Volume-weighted standard deviation.
            deviation_sd: Current deviation in SD units.

        Returns:
            The stop price.
        """
        if self.stop_source == STOP_SOURCE_SD_BAND:
            # Next whole band outward from the one price is sitting past.
            next_band = float(int(deviation_sd) + 1)
            if side == OrderSide.BUY:
                return vwap - next_band * stddev
            return vwap + next_band * stddev

        # Both ATR modes share this formula; they differ only in which series
        # the caller computed `atr` from.
        offset = atr * self.atr_stop_multiplier
        if side == OrderSide.BUY:
            return current_price - offset
        return current_price + offset

    def generate_signals(
        self,
        symbol: str,
        multi_tf_data: Dict[str, Dict[str, List[float]]],
        current_price: float,
        execution_tf_data: Optional[Dict[str, Dict[str, List[float]]]] = None,
    ) -> List[Signal]:
        """
        Generate VWAP scalping signals using mean reversion logic.

        Uses 15m for VWAP/bands calculation, 5m for MACD confirmation.

        Args:
            symbol: Trading symbol (e.g., "BTC-PERP")
            multi_tf_data: Regime/structure TFs {"15m": {...}, "1h": {...}, "4h": {...}}
            current_price: Current market price
            execution_tf_data: Optional execution TFs {"5m": {...}, "1m": {...}}

        Returns:
            List of Signal objects (0-1 signals)
        """
        try:
            # Check cooldown
            if self._check_cooldown(symbol):
                return []

            # Require 15m for VWAP calculation (primary timeframe)
            if "15m" not in multi_tf_data:
                logger.debug(f"{symbol}: Missing 15m data for VWAP calculation")
                return []

            data_15m = multi_tf_data["15m"]

            # Validate 15m data
            required_fields = ["high", "low", "close", "volume"]
            for field in required_fields:
                if field not in data_15m or len(data_15m[field]) < 30:
                    logger.debug(f"{symbol}: Insufficient 15m {field} data for VWAP")
                    return []

            # Calculate VWAP and bands on 15m
            bands = self._calculate_vwap_and_bands(
                data_15m["high"],
                data_15m["low"],
                data_15m["close"],
                data_15m["volume"],
            )

            if not bands:
                logger.debug(f"{symbol}: Could not calculate VWAP bands")
                return []

            vwap = bands["vwap"]
            stddev = bands["stddev"]

            # Calculate deviation in SD units
            deviation = current_price - vwap
            deviation_sd = abs(deviation) / stddev if stddev > 0 else 0

            # Check minimum deviation threshold
            if deviation_sd < self.sd_entry_threshold:
                logger.debug(
                    f"{symbol}: VWAP deviation {deviation_sd:.2f} SD < threshold {self.sd_entry_threshold}"
                )
                return []

            # Get MACD confirmation (prefer 5m, fallback to 15m)
            macd_data = data_15m
            macd_tf = "15m"

            if execution_tf_data and "5m" in execution_tf_data:
                data_5m = execution_tf_data["5m"]
                if "close" in data_5m and len(data_5m["close"]) >= 35:
                    macd_data = data_5m
                    macd_tf = "5m"

            # Calculate MACD. Under entry_confirmation="none" the histogram is
            # reported but gates nothing, so a MACD failure must not veto the
            # signal - that would silently reintroduce a confirmation gate.
            macd_required = self.entry_confirmation != ENTRY_CONFIRMATION_NONE
            try:
                macd_line, signal_line, histogram = calculate_macd(
                    macd_data["close"],
                    self.macd_fast,
                    self.macd_slow,
                    self.macd_signal,
                )
            except ValueError as e:
                if macd_required:
                    logger.debug(f"{symbol}: MACD calculation failed: {e}")
                    return []
                macd_line = signal_line = histogram = 0.0

            # The "turning" gate needs the previous bar's histogram to know
            # which way momentum is inflecting.
            prev_histogram = None
            if self.entry_confirmation == ENTRY_CONFIRMATION_TURNING:
                try:
                    _, _, prev_histogram = calculate_macd(
                        macd_data["close"][:-1],
                        self.macd_fast,
                        self.macd_slow,
                        self.macd_signal,
                    )
                except ValueError as e:
                    logger.debug(
                        f"{symbol}: previous-bar MACD unavailable, cannot "
                        f"evaluate a turning histogram: {e}"
                    )
                    return []

            # Calculate RSI for second confirmation gate
            try:
                rsi = calculate_rsi(macd_data["close"], period=self.rsi_period)
            except (ValueError, Exception):
                rsi = 50.0  # Neutral fallback — does not block entries

            # Determine signal direction based on deviation
            side = None
            notes = []

            # Below VWAP -> potential LONG (price expected to revert up)
            if deviation < 0 and deviation_sd >= self.sd_entry_threshold:
                if self._entry_confirmed(OrderSide.BUY, histogram, prev_histogram):
                    side = OrderSide.BUY
                    notes.append(f"Below VWAP by {deviation_sd:.2f} SD")
                    notes.append(
                        f"MACD hist={histogram:.4f} "
                        f"[{self.entry_confirmation}], RSI={rsi:.1f}"
                    )
                else:
                    logger.debug(
                        f"{symbol}: Below VWAP SD{deviation_sd:.2f} - "
                        f"MACD hist={histogram:.4f} fails the "
                        f"'{self.entry_confirmation}' confirmation, skip"
                    )

            # Above VWAP -> potential SHORT (price expected to revert down)
            elif deviation > 0 and deviation_sd >= self.sd_entry_threshold:
                if self._entry_confirmed(OrderSide.SELL, histogram, prev_histogram):
                    side = OrderSide.SELL
                    notes.append(f"Above VWAP by {deviation_sd:.2f} SD")
                    notes.append(
                        f"MACD hist={histogram:.4f} "
                        f"[{self.entry_confirmation}], RSI={rsi:.1f}"
                    )
                else:
                    logger.debug(
                        f"{symbol}: Above VWAP SD{deviation_sd:.2f} - "
                        f"MACD hist={histogram:.4f} fails the "
                        f"'{self.entry_confirmation}' confirmation, skip"
                    )

            if not side:
                return []

            # Calculate ATR for stop loss.
            # atr_execution prefers the 1m series "for precision"; measured, it
            # yields a stop ~5x tighter than the 15m setup implies and smaller
            # than the entry bar's own range on 99.3% of signals.
            # atr_structure pins the ATR to the timeframe the setup lives on.
            atr_src = data_15m
            atr_tf = "15m"
            if (
                self.stop_source == STOP_SOURCE_ATR_EXECUTION
                and execution_tf_data
                and "1m" in execution_tf_data
            ):
                d1m = execution_tf_data["1m"]
                if (
                    all(k in d1m for k in ["high", "low", "close"])
                    and len(d1m["close"]) >= self.atr_period + 1
                ):
                    atr_src = d1m
                    atr_tf = "1m"
                    logger.debug(f"{symbol}: VWAP using 1m ATR for SL/TP placement")
            try:
                atr = calculate_atr(
                    atr_src["high"],
                    atr_src["low"],
                    atr_src["close"],
                    self.atr_period,
                )
            except ValueError:
                atr = abs(deviation)  # Fallback to deviation as proxy

            stop_loss = self._resolve_stop_loss(
                side=side,
                current_price=current_price,
                atr=atr,
                vwap=vwap,
                stddev=stddev,
                deviation_sd=deviation_sd,
            )

            # Take profit: VWAP (mean reversion target)
            take_profit = vwap

            # Calculate confidence based on:
            # - SD deviation magnitude (more deviation = higher conviction)
            # - Band position (SD2+ = bonus confidence)
            confidence = self.min_confidence

            # Deviation bonus (0-0.15)
            deviation_bonus = min(0.15, (deviation_sd - self.sd_entry_threshold) * 0.08)
            confidence += deviation_bonus

            # SD2+ bonus
            if deviation_sd >= 2.0:
                confidence += 0.10
                notes.append("Extreme deviation (SD2+)")

            # SD3 (very rare) bonus
            if deviation_sd >= 3.0:
                confidence += 0.08
                notes.append("Very extreme deviation (SD3+)")

            # Cap confidence
            confidence = min(0.94, confidence)

            # Determine quality
            if confidence >= 0.80:
                quality = TradeQuality.HIGH_CONVICTION
            else:
                quality = TradeQuality.STANDARD

            # Calculate RRR
            risk = abs(current_price - stop_loss)
            reward = abs(take_profit - current_price)
            rrr = reward / risk if risk > 0 else 0

            # Create signal
            signal = Signal(
                strategy=self.strategy_type,
                asset=symbol,
                asset_class=AssetClass.PERPETUAL,
                side=side,
                entry_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                confidence=confidence,
                quality=quality,
                market_state=MarketState.RANGE,  # VWAP is mean reversion
                timeframe=macd_tf,
                pattern="vwap_mean_reversion",
                volume_confirmation=True,  # VWAP inherently uses volume
                multi_timeframe_alignment=True,
                support_resistance_valid=True,  # VWAP acts as dynamic S/R
                rrr_meets_minimum=rrr
                >= 0.8,  # Scalping: lower RRR OK with high win rate
                liquidation_buffer_safe=True,
                account_risk_ok=True,
                margin_drawdown_ok=True,
                forbidden_conditions_clear=True,
                indicators={
                    "vwap": vwap,
                    "stddev": stddev,
                    "deviation_sd": deviation_sd,
                    "macd_histogram": histogram,
                    "macd_line": macd_line,
                    "macd_signal": signal_line,
                    "atr": atr,
                    "atr_timeframe": atr_tf,
                    "stop_source": self.stop_source,
                    "entry_confirmation": self.entry_confirmation,
                    "sd1_upper": bands.get("sd1.0_upper"),
                    "sd1_lower": bands.get("sd1.0_lower"),
                    "sd2_upper": bands.get("sd2.0_upper"),
                    "sd2_lower": bands.get("sd2.0_lower"),
                },
                notes=" | ".join(
                    [f"VWAP=${vwap:.4f}", f"dev={deviation_sd:.2f}SD", f"RRR={rrr:.2f}"]
                    + notes
                ),
            )

            # Set cooldown
            self._set_cooldown(symbol)

            logger.info(
                f"{symbol}: VWAP scalping {side.value} signal - "
                f"price=${current_price:.4f}, VWAP=${vwap:.4f}, "
                f"deviation={deviation_sd:.2f}SD, confidence={confidence:.2%}, RRR={rrr:.2f}"
            )

            return [signal]

        except Exception as e:
            logger.error(f"Error generating VWAP scalping signal for {symbol}: {e}")
            return []

    def get_vwap_status(
        self, symbol: str, data: Dict[str, List[float]]
    ) -> Optional[Dict]:
        """
        Get current VWAP status for monitoring/display purposes.

        Args:
            symbol: Trading symbol
            data: OHLCV data dict with high, low, close, volume

        Returns:
            Dict with VWAP metrics or None if calculation fails
        """
        if not all(k in data for k in ["high", "low", "close", "volume"]):
            return None

        bands = self._calculate_vwap_and_bands(
            data["high"], data["low"], data["close"], data["volume"]
        )

        if not bands:
            return None

        current_price = data["close"][-1] if data["close"] else 0

        deviation = current_price - bands["vwap"]
        deviation_sd = abs(deviation) / bands["stddev"] if bands["stddev"] > 0 else 0

        return {
            "symbol": symbol,
            "vwap": bands["vwap"],
            "stddev": bands["stddev"],
            "current_price": current_price,
            "deviation": deviation,
            "deviation_sd": deviation_sd,
            "sd1_upper": bands.get("sd1.0_upper"),
            "sd1_lower": bands.get("sd1.0_lower"),
            "sd2_upper": bands.get("sd2.0_upper"),
            "sd2_lower": bands.get("sd2.0_lower"),
            "in_cooldown": self._check_cooldown(symbol),
        }

    def describe_gate_metrics(
        self,
        symbol: str,
        multi_tf_data: Dict[str, Dict[str, List[float]]],
        current_price: float,
        execution_tf_data: Optional[Dict[str, Dict[str, List[float]]]] = None,
    ) -> List[GateMetric]:
        """
        Report the entry gate's metric for one bar (calibration hook).

        This is the hook that would have caught the 4.037 bug on day one:
        ``deviation_sd`` is the metric ``sd_entry_threshold`` gates on, and
        its distribution over real data has a hard ceiling near 4.0 because
        the VWAP here is rolling cumulative rather than session-anchored.

        The value is threshold-independent - it depends only on the 15m
        window and the current price - so an artifact built from it stays
        valid for any candidate ``sd_entry_threshold``. It does depend on
        how many 15m candles the caller supplies (the VWAP is cumulative
        over the whole window), which is why the calibration pass records
        the history lookback in its provenance.

        Side-effect free: no cooldown is set and no state is touched.

        Args:
            symbol: Trading symbol (unused; kept for hook uniformity).
            multi_tf_data: Regime/structure timeframes, must contain "15m".
            current_price: Current market price.
            execution_tf_data: Execution timeframes (unused - neither 5m
                MACD nor 1m ATR gates entry).

        Returns:
            One GateMetric for ``deviation_sd``, or an empty list when the
            15m bundle cannot produce VWAP bands.
        """
        data_15m = (multi_tf_data or {}).get("15m")
        if not data_15m:
            return []
        for field in ("high", "low", "close", "volume"):
            series = data_15m.get(field)
            if not series or len(series) < 30:
                return []

        bands = self._calculate_vwap_and_bands(
            data_15m["high"],
            data_15m["low"],
            data_15m["close"],
            data_15m["volume"],
        )
        if not bands:
            return []

        stddev = bands["stddev"]
        if stddev <= 0:
            return []

        deviation_sd = abs(current_price - bands["vwap"]) / stddev
        return [
            GateMetric(
                name="deviation_sd",
                value=deviation_sd,
                threshold=self.sd_entry_threshold,
                direction=GATE_AT_LEAST,
                param_key="sd_entry_threshold",
                description=(
                    "absolute price deviation from the rolling cumulative "
                    "VWAP, in volume-weighted standard deviations; the "
                    "entry gate every VWAP signal must clear first"
                ),
            )
        ]
