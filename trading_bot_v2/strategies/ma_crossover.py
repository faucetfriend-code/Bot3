"""
MA Crossover Strategy (Trend Following)

Strategy Logic:
- BUY Signal: fast MA crosses above slow MA (Golden Cross)
- SELL Signal: fast MA crosses below slow MA (Death Cross)
- Confirmation: Rising volume on crossover, MACD alignment
- Entry: Wait for pullback after crossover (not immediate)
- Stop Loss: Below recent swing low (long) or swing high (short)

MA periods are configurable (MA_CROSSOVER_FAST_PERIOD /
MA_CROSSOVER_SLOW_PERIOD). Code defaults are 20/50; the shipped .env
runs the validated 10/30 pair. The classic 50/200 pair needs at least
201 candles of 4h history, which is more than the 60-candle window the
backtest engine supplies by default - raise BACKTEST_HISTORY_LOOKBACK
before configuring long MAs (see _validate_data).

The entry window (MA_CROSSOVER_MIN_ENTRY_BARS /
MA_CROSSOVER_MAX_ENTRY_BARS) is configurable too - see
validate_entry_window() for the guard applied to bad values.

Best For: TRENDING_STRONG regime (ADX > 25)

VALIDATION NOTE (2026-07-28):
The "Sharpe +0.696 / 45 trades / 64.4% WR" figures quoted in .env come
from BTV2/results/regime_aware_ma_crossover_4h_2018-01-01_2025-01-01.json,
produced by the SEPARATE BTV2 harness (BTV2/strategies.py::run_ma_crossover),
which walks one continuous index over a whole fold and is therefore immune
to the rolling-window regression fixed in 4be716b. The numbers are real but
they measure a DIFFERENT implementation - they are not evidence about this
class. Do not treat them as validation of this file.

Based on Strategy Review Document: Trend Following MA Crossover
"""

import os
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from loguru import logger

# Use relative imports from trading_bot_v2 package
from ..models import Signal, OrderSide
from ..diagnostics.gate_metrics import GATE_IN_BAND, GateMetric
from ..indicators import calculate_sma, calculate_macd, calculate_atr
from ..config import StrategyType, AssetClass, TradeQuality, MarketState


# ---------------------------------------------------------------------------
# Entry-window defaults
# ---------------------------------------------------------------------------
# Bars after the crossover bar during which a pullback (long) or rally
# (short) is accepted. At 4h resolution the default 1-5 band is a 20-hour
# window. Mirrored by the optimizer search space in
# optimization/search_spaces.py::_ma_crossover_space().
DEFAULT_MIN_ENTRY_BARS = 1
DEFAULT_MAX_ENTRY_BARS = 5


def validate_entry_window(
    min_bars: int, max_bars: int
) -> Tuple[int, int]:
    """
    Validate the (min, max) entry-window pair, falling back on nonsense.

    An inverted or negative window does not degrade the strategy, it
    disables it: generate_signals() only builds a signal while
    ``min_entry_bars <= bars_since_crossover <= max_entry_bars``, so an
    empty band means every detected crossover expires unused and the
    strategy silently emits nothing forever.

    Falling back to the validated defaults (instead of raising) matches
    how the rest of the codebase handles bad config - see
    validate_sd_entry_threshold() in strategies/vwap_scalping.py and the
    min_rrr repair in strategies/momentum_scalping.py - and avoids taking
    a running bot down over a single tuning parameter.

    Args:
        min_bars: Configured lower edge of the entry window, in 4h bars.
        max_bars: Configured upper edge of the entry window, in 4h bars.

    Returns:
        The pair unchanged when it is usable, else
        (DEFAULT_MIN_ENTRY_BARS, DEFAULT_MAX_ENTRY_BARS).
    """
    if min_bars >= 0 and max_bars >= min_bars:
        return min_bars, max_bars

    logger.warning(
        f"MA_CROSSOVER entry window ({min_bars}, {max_bars}) is invalid "
        f"(need min >= 0 and max >= min). An empty or negative window "
        f"means every crossover expires before it can be entered and the "
        f"strategy produces ZERO signals. Falling back to "
        f"({DEFAULT_MIN_ENTRY_BARS}, {DEFAULT_MAX_ENTRY_BARS}). Fix "
        f"MA_CROSSOVER_MIN_ENTRY_BARS / MA_CROSSOVER_MAX_ENTRY_BARS "
        f"in .env."
    )
    return DEFAULT_MIN_ENTRY_BARS, DEFAULT_MAX_ENTRY_BARS


def _env_int(var_name: str, default: int) -> int:
    """Read an int from the environment, warning and falling back on junk.

    Args:
        var_name: Environment variable name.
        default: Value used when unset or unparseable.

    Returns:
        The parsed int, or the default.
    """
    raw = os.getenv(var_name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            f"Invalid int for {var_name}={raw!r}, using default {default}"
        )
        return default


class MACrossoverStrategy:
    """
    Moving Average Crossover strategy for trend following.

    Uses fast/slow MA crossovers with pullback entry logic.

    Parameters can be configured via environment variables:
    - MA_CROSSOVER_FAST_PERIOD (default: 20)
    - MA_CROSSOVER_SLOW_PERIOD (default: 50)
    - MA_CROSSOVER_PULLBACK_MIN (default: 0.02)
    - MA_CROSSOVER_PULLBACK_MAX (default: 0.04)
    - MA_CROSSOVER_VOLUME_THRESHOLD (default: 1.2)
    - MA_CROSSOVER_MACD_FAST (default: 12)
    - MA_CROSSOVER_MACD_SLOW (default: 26)
    - MA_CROSSOVER_MACD_SIGNAL (default: 9)
    - MA_CROSSOVER_ATR_PERIOD (default: 14)
    - MA_CROSSOVER_ATR_STOP_MULTIPLIER (default: 2.5)
    - MA_CROSSOVER_MIN_CONFIDENCE (default: 0.50)
    - MA_CROSSOVER_MIN_ENTRY_BARS (default: 1)
    - MA_CROSSOVER_MAX_ENTRY_BARS (default: 5)
    """

    # Entry window, in 4h bars after the crossover bar, during which a
    # pullback (long) or rally (short) is accepted. These remain the
    # defaults; the live values are the per-instance min_entry_bars /
    # max_entry_bars, which the optimizer search space can tune.
    MIN_ENTRY_BARS = DEFAULT_MIN_ENTRY_BARS
    MAX_ENTRY_BARS = DEFAULT_MAX_ENTRY_BARS

    def __init__(
        self,
        fast_ma_period: Optional[int] = None,
        slow_ma_period: Optional[int] = None,
        pullback_range: Optional[Tuple[float, float]] = None,
        volume_confirmation_threshold: Optional[float] = None,
        macd_fast: Optional[int] = None,
        macd_slow: Optional[int] = None,
        macd_signal: Optional[int] = None,
        atr_period: Optional[int] = None,
        atr_stop_multiplier: Optional[float] = None,
        min_confidence: Optional[float] = None,
        min_entry_bars: Optional[int] = None,
        max_entry_bars: Optional[int] = None,
    ):
        """
        Initialize MA Crossover Strategy.

        Parameters are read from environment variables if not explicitly passed.

        Args:
            fast_ma_period: Fast MA period (env: MA_CROSSOVER_FAST_PERIOD, default: 20)
            slow_ma_period: Slow MA period (env: MA_CROSSOVER_SLOW_PERIOD, default: 50)
            pullback_range: Acceptable pullback range for entry (env: MA_CROSSOVER_PULLBACK_MIN/MAX, default: 2-4%)
            volume_confirmation_threshold: Volume multiplier (env: MA_CROSSOVER_VOLUME_THRESHOLD, default: 1.2x)
            macd_fast: MACD fast period (default: 12)
            macd_slow: MACD slow period (default: 26)
            macd_signal: MACD signal period (default: 9)
            atr_period: ATR period for stop loss (default: 14)
            atr_stop_multiplier: ATR multiplier for stop (env: MA_CROSSOVER_ATR_STOP_MULTIPLIER, default: 2.5)
            min_confidence: Minimum confidence (env: MA_CROSSOVER_MIN_CONFIDENCE, default: 0.50)
            min_entry_bars: First 4h bar after the crossover on which an
                entry is accepted (env: MA_CROSSOVER_MIN_ENTRY_BARS,
                default: 1)
            max_entry_bars: Last 4h bar after the crossover on which an
                entry is accepted (env: MA_CROSSOVER_MAX_ENTRY_BARS,
                default: 5). The pair is validated together - see
                validate_entry_window().
        """
        # Read from environment with reasonable defaults
        self.fast_ma_period = (
            fast_ma_period
            if fast_ma_period is not None
            else int(os.getenv("MA_CROSSOVER_FAST_PERIOD", "20"))
        )
        self.slow_ma_period = (
            slow_ma_period
            if slow_ma_period is not None
            else int(os.getenv("MA_CROSSOVER_SLOW_PERIOD", "50"))
        )

        if pullback_range is not None:
            self.pullback_range = pullback_range
        else:
            pullback_min = float(os.getenv("MA_CROSSOVER_PULLBACK_MIN", "0.02"))
            pullback_max = float(os.getenv("MA_CROSSOVER_PULLBACK_MAX", "0.04"))
            self.pullback_range = (pullback_min, pullback_max)

        self.volume_threshold = (
            volume_confirmation_threshold
            if volume_confirmation_threshold is not None
            else float(os.getenv("MA_CROSSOVER_VOLUME_THRESHOLD", "1.2"))
        )
        self.macd_fast = macd_fast if macd_fast is not None else int(os.getenv("MA_CROSSOVER_MACD_FAST", "12"))
        self.macd_slow = macd_slow if macd_slow is not None else int(os.getenv("MA_CROSSOVER_MACD_SLOW", "26"))
        self.macd_signal = macd_signal if macd_signal is not None else int(os.getenv("MA_CROSSOVER_MACD_SIGNAL", "9"))
        self.atr_period = atr_period if atr_period is not None else int(os.getenv("MA_CROSSOVER_ATR_PERIOD", "14"))
        self.atr_stop_multiplier = (
            atr_stop_multiplier
            if atr_stop_multiplier is not None
            else float(os.getenv("MA_CROSSOVER_ATR_STOP_MULTIPLIER", "2.5"))
        )
        self.min_confidence = (
            min_confidence
            if min_confidence is not None
            else float(os.getenv("MA_CROSSOVER_MIN_CONFIDENCE", "0.50"))
        )  # Loosened from 0.65

        # Entry window. Read as a pair and validated as a pair, because
        # an inverted band silently disables every entry.
        raw_min_entry = (
            min_entry_bars
            if min_entry_bars is not None
            else _env_int("MA_CROSSOVER_MIN_ENTRY_BARS", self.MIN_ENTRY_BARS)
        )
        raw_max_entry = (
            max_entry_bars
            if max_entry_bars is not None
            else _env_int("MA_CROSSOVER_MAX_ENTRY_BARS", self.MAX_ENTRY_BARS)
        )
        self.min_entry_bars, self.max_entry_bars = validate_entry_window(
            int(raw_min_entry), int(raw_max_entry)
        )

        # Track crossover state.
        #
        # The crossover bar MUST be identified by something invariant under a
        # rolling window: every caller hands this strategy a fixed-length
        # window (backtest engine: 60 candles, live fetcher: up to 250), so an
        # absolute list index into that window is a moving target. Storing
        # ``len(closes) - 1`` made "bars since crossover" collapse to a
        # constant 0 once the window saturated, which silently disabled every
        # entry forever. Store the crossover bar's timestamp instead, plus a
        # monotonic per-symbol bar counter for callers that supply no
        # timestamps.
        # {symbol: {"type": "golden"/"death", "bar_ts": Any, "bar_seq": int, ...}}
        self.last_crossover = {}

        # {symbol: (last_bar_signature, bar_seq)} - see _observe_bar()
        self._bar_state: Dict[str, Tuple[Any, int]] = {}

        logger.info(
            f"MACrossoverStrategy initialized: "
            f"MA {self.fast_ma_period}/{self.slow_ma_period}, "
            f"pullback {self.pullback_range[0]:.1%}-{self.pullback_range[1]:.1%}, "
            f"entry window {self.min_entry_bars}-{self.max_entry_bars} bars, "
            f"volume>={self.volume_threshold}x, "
            f"ATR stop={self.atr_stop_multiplier}x, "
            f"min_confidence={self.min_confidence}"
        )

    # ------------------------------------------------------------------
    # Pullback band accessors
    # ------------------------------------------------------------------
    #
    # The band is stored as a tuple, but the optimizer addresses its two
    # edges as separate scalar parameters (pullback_range_min /
    # pullback_range_max in the search space). regime_param_overlay's
    # apply_params_to_strategy() only sets attributes that already exist
    # on the instance, so without these properties both edges were
    # silently skipped and every sampled pullback band was a no-op.

    @property
    def pullback_range_min(self) -> float:
        """Lower edge of the accepted pullback/rally band."""
        return self.pullback_range[0]

    @pullback_range_min.setter
    def pullback_range_min(self, value: float) -> None:
        self.pullback_range = (float(value), self.pullback_range[1])

    @property
    def pullback_range_max(self) -> float:
        """Upper edge of the accepted pullback/rally band."""
        return self.pullback_range[1]

    @pullback_range_max.setter
    def pullback_range_max(self, value: float) -> None:
        self.pullback_range = (self.pullback_range[0], float(value))

    def generate_signals(
        self,
        symbol: str,
        multi_tf_data: Dict[str, Dict[str, List[float]]],
        current_price: float,
    ) -> List[Signal]:
        """
        Generate MA crossover signals from multi-timeframe data.

        Args:
            symbol: Trading symbol (e.g., "SUI-PERP")
            multi_tf_data: Dictionary mapping timeframe to OHLCV data
                          {"15m": {"high": [...], "low": [...], "close": [...], "volume": [...]}, ...}
            current_price: Current market price

        Returns:
            List of Signal objects (0-1 signals)
        """
        try:
            # Require 4h data for MA crossover (longer timeframe for trend detection)
            if "4h" not in multi_tf_data:
                logger.warning(f"Missing 4h timeframe for {symbol}")
                return []

            data_4h = multi_tf_data["4h"]

            # Validate data (logs the specific shortfall itself)
            if not self._validate_data(data_4h, symbol=symbol):
                return []

            # Note which 4h bar this window ends on, so crossover age can be
            # measured even when the caller supplies no timestamps.
            current_bar_seq = self._observe_bar(symbol, data_4h)

            # Calculate MAs on 4h timeframe
            closes = data_4h["close"]
            fast_ma = calculate_sma(closes, period=self.fast_ma_period)
            slow_ma = calculate_sma(closes, period=self.slow_ma_period)

            # Calculate previous MAs to detect crossover
            fast_ma_prev = calculate_sma(closes[:-1], period=self.fast_ma_period)
            slow_ma_prev = calculate_sma(closes[:-1], period=self.slow_ma_period)

            # Calculate MACD for confirmation
            macd_line, signal_line, histogram = calculate_macd(
                closes,
                fast_period=self.macd_fast,
                slow_period=self.macd_slow,
                signal_period=self.macd_signal,
            )

            # Calculate ATR for stop loss
            atr = calculate_atr(
                data_4h["high"], data_4h["low"], closes, period=self.atr_period
            )

            # Calculate volume confirmation
            volumes = data_4h.get("volume", [1.0] * len(closes))
            avg_volume = sum(volumes[-20:]) / min(20, len(volumes))
            current_volume = volumes[-1]
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

            logger.debug(
                f"{symbol} 4h indicators: "
                f"Fast MA={fast_ma:.2f}, Slow MA={slow_ma:.2f}, "
                f"MACD={macd_line:.2f}, Signal={signal_line:.2f}, "
                f"Volume ratio={volume_ratio:.2f}x"
            )

            # Detect crossovers
            crossover_type = self._detect_crossover(
                fast_ma, slow_ma, fast_ma_prev, slow_ma_prev
            )

            if crossover_type:
                # Store crossover for pullback tracking. Keyed on the bar's
                # timestamp (window-position independent) with the bar counter
                # as a fallback.
                self.last_crossover[symbol] = {
                    "type": crossover_type,
                    "bar_ts": self._latest_timestamp(data_4h),
                    "bar_seq": current_bar_seq,
                    "fast_ma": fast_ma,
                    "slow_ma": slow_ma,
                }
                logger.info(f"{crossover_type.upper()} crossover detected for {symbol}")

            # Check if we should enter on pullback
            if symbol in self.last_crossover:
                crossover_info = self.last_crossover[symbol]
                candles_since_crossover = self._bars_since_crossover(
                    data_4h, crossover_info, current_bar_seq
                )

                if (
                    candles_since_crossover is None
                    or candles_since_crossover > self.max_entry_bars
                ):
                    # Entry window closed (or the crossover bar has scrolled
                    # out of the supplied history) - stop tracking it.
                    self.last_crossover.pop(symbol, None)
                    return []

                logger.debug(
                    f"{symbol} {crossover_info['type']} crossover is "
                    f"{candles_since_crossover} bar(s) old "
                    f"(entry window {self.min_entry_bars}-{self.max_entry_bars})"
                )

                # Enter only inside the configured post-crossover window
                if (
                    self.min_entry_bars
                    <= candles_since_crossover
                    <= self.max_entry_bars
                ):
                    if crossover_info["type"] == "golden":
                        # Golden cross - check for LONG entry on pullback
                        pullback_pct = (fast_ma - current_price) / fast_ma
                        if (
                            self.pullback_range[0]
                            <= pullback_pct
                            <= self.pullback_range[1]
                        ):
                            # Check MACD alignment
                            if macd_line > signal_line:
                                signal = self._create_long_signal(
                                    symbol=symbol,
                                    current_price=current_price,
                                    fast_ma=fast_ma,
                                    slow_ma=slow_ma,
                                    atr=atr,
                                    macd_line=macd_line,
                                    signal_line=signal_line,
                                    volume_ratio=volume_ratio,
                                    pullback_pct=pullback_pct,
                                    highs=data_4h["high"],
                                    lows=data_4h["low"],
                                )
                                if signal:
                                    logger.info(f"LONG signal on pullback for {symbol}")
                                    return [signal]

                    elif crossover_info["type"] == "death":
                        # Death cross - check for SHORT entry on rally
                        rally_pct = (current_price - fast_ma) / fast_ma
                        if (
                            self.pullback_range[0]
                            <= rally_pct
                            <= self.pullback_range[1]
                        ):
                            # Check MACD alignment
                            if macd_line < signal_line:
                                signal = self._create_short_signal(
                                    symbol=symbol,
                                    current_price=current_price,
                                    fast_ma=fast_ma,
                                    slow_ma=slow_ma,
                                    atr=atr,
                                    macd_line=macd_line,
                                    signal_line=signal_line,
                                    volume_ratio=volume_ratio,
                                    rally_pct=rally_pct,
                                    highs=data_4h["high"],
                                    lows=data_4h["low"],
                                )
                                if signal:
                                    logger.info(f"SHORT signal on rally for {symbol}")
                                    return [signal]

            # No signal
            return []

        except Exception as e:
            logger.error(f"Error generating MA crossover signal for {symbol}: {e}")
            return []

    def required_history(self) -> int:
        """Minimum number of 4h candles this configuration needs."""
        return max(self.slow_ma_period + 1, self.macd_slow + self.macd_signal)

    def _validate_data(self, data: Dict[str, List[float]], symbol: str = "") -> bool:
        """Validate that data has required fields and sufficient length.

        Logs loudly on failure: a silent ``return []`` here is
        indistinguishable from "no setup found", and a slow MA longer than
        the caller's history window disables the strategy permanently.

        Args:
            data: 4h OHLCV bundle.
            symbol: Symbol name, for log context.

        Returns:
            True when the bundle can drive signal generation.
        """
        required_keys = ["high", "low", "close"]
        min_length = self.required_history()
        label = symbol or "<unknown symbol>"

        for key in required_keys:
            if key not in data:
                logger.warning(
                    f"MACrossover {label}: 4h bundle is missing the '{key}' "
                    f"series - no signals can be generated"
                )
                return False
            if len(data[key]) < min_length:
                logger.warning(
                    f"MACrossover {label}: insufficient 4h history - '{key}' has "
                    f"{len(data[key])} candles but {min_length} are required "
                    f"(slow_ma_period={self.slow_ma_period}, "
                    f"macd={self.macd_slow}+{self.macd_signal}). "
                    f"This disables the strategy entirely: raise the caller's "
                    f"history lookback (BACKTEST_HISTORY_LOOKBACK in backtests, "
                    f"lookback_candles live) or lower MA_CROSSOVER_SLOW_PERIOD."
                )
                return False

        return True

    # ------------------------------------------------------------------
    # Rolling-window-safe bar tracking
    # ------------------------------------------------------------------

    @staticmethod
    def _latest_timestamp(data: Dict[str, List[Any]]) -> Optional[Any]:
        """Timestamp of the newest candle in a bundle, if one is present.

        Backtest bundles carry ISO-8601 strings, live bundles carry epoch
        milliseconds; both are used only for equality comparison, so the raw
        value is kept as-is.
        """
        timestamps = data.get("timestamp") or []
        if not timestamps:
            return None
        return timestamps[-1]

    def _observe_bar(self, symbol: str, data: Dict[str, List[Any]]) -> int:
        """Return a monotonic counter of distinct 4h bars seen for a symbol.

        Callers invoke generate_signals far more often than the 4h bar
        advances (the backtest engine replays 5m candles, so the same 4h
        bundle arrives 48 times). The counter only advances when the newest
        candle actually changes, giving a window-length-independent measure
        of elapsed bars for callers that supply no timestamps.

        Args:
            symbol: Trading symbol.
            data: 4h OHLCV bundle.

        Returns:
            Current bar sequence number for the symbol.
        """
        signature = self._bar_signature(data)
        previous_signature, seq = self._bar_state.get(symbol, (None, -1))
        if signature != previous_signature:
            seq += 1
            self._bar_state[symbol] = (signature, seq)
        return seq

    @staticmethod
    def _bar_signature(data: Dict[str, List[Any]]) -> Any:
        """Identity of the newest candle in a bundle, independent of index."""
        latest_ts = MACrossoverStrategy._latest_timestamp(data)
        if latest_ts is not None:
            return ("ts", latest_ts)
        closes = data.get("close") or []
        highs = data.get("high") or []
        lows = data.get("low") or []
        return (
            "ohlc",
            closes[-1] if closes else None,
            highs[-1] if highs else None,
            lows[-1] if lows else None,
        )

    def _bars_since_crossover(
        self,
        data: Dict[str, List[Any]],
        crossover_info: Dict[str, Any],
        current_bar_seq: int,
    ) -> Optional[int]:
        """Bars elapsed since the stored crossover.

        Locates the crossover bar by timestamp inside the supplied window, so
        the answer stays correct once the caller's fixed-length rolling window
        saturates. Falls back to the per-symbol bar counter when the bundle
        carries no timestamps.

        Args:
            data: 4h OHLCV bundle.
            crossover_info: Entry from ``self.last_crossover``.
            current_bar_seq: Value returned by ``_observe_bar`` this call.

        Returns:
            Number of 4h bars since the crossover bar, or None when the
            crossover bar is no longer present in the supplied history (it
            has scrolled out, so it is far older than the entry window).
        """
        timestamps = data.get("timestamp") or []
        crossover_ts = crossover_info.get("bar_ts")

        if timestamps and crossover_ts is not None:
            # Search from the newest end: the offset from the end IS the
            # number of bars elapsed, and duplicated timestamps in a
            # malformed feed resolve to the most recent occurrence.
            for offset, candidate in enumerate(reversed(timestamps)):
                if candidate == crossover_ts:
                    return offset
            return None  # crossover bar has scrolled out of the window

        crossover_seq = crossover_info.get("bar_seq")
        if crossover_seq is None:
            return None
        return current_bar_seq - crossover_seq

    def _detect_crossover(
        self, fast_ma: float, slow_ma: float, fast_ma_prev: float, slow_ma_prev: float
    ) -> Optional[str]:
        """
        Detect MA crossover.

        Returns:
            "golden" for golden cross (fast crosses above slow)
            "death" for death cross (fast crosses below slow)
            None if no crossover
        """
        # Golden cross: fast was below, now above
        if fast_ma_prev <= slow_ma_prev and fast_ma > slow_ma:
            return "golden"

        # Death cross: fast was above, now below
        if fast_ma_prev >= slow_ma_prev and fast_ma < slow_ma:
            return "death"

        return None

    def _find_recent_swing_low(self, lows: List[float], lookback: int = 20) -> float:
        """Find recent swing low for stop loss placement."""
        recent_lows = lows[-lookback:]
        return min(recent_lows) if recent_lows else lows[-1]

    def _find_recent_swing_high(self, highs: List[float], lookback: int = 20) -> float:
        """Find recent swing high for stop loss placement."""
        recent_highs = highs[-lookback:]
        return max(recent_highs) if recent_highs else highs[-1]

    def _create_long_signal(
        self,
        symbol: str,
        current_price: float,
        fast_ma: float,
        slow_ma: float,
        atr: float,
        macd_line: float,
        signal_line: float,
        volume_ratio: float,
        pullback_pct: float,
        highs: List[float],
        lows: List[float],
    ) -> Optional[Signal]:
        """Create LONG signal after golden cross pullback."""
        # Stop Loss: Below recent swing low or 2.5x ATR, whichever is closer
        swing_low = self._find_recent_swing_low(lows)
        atr_stop = current_price - (atr * self.atr_stop_multiplier)
        stop_loss = max(swing_low, atr_stop)  # Use the closer stop

        # Take Profit: 2x risk (RRR = 2.0 minimum)
        risk = current_price - stop_loss
        take_profit = current_price + (risk * 2.0)

        # Calculate confidence (0-1 scale)
        # Higher confidence when:
        # - Volume is strong (> 1.2x average)
        # - MACD shows strong bullish momentum
        # - Pullback is in sweet spot (2-3%)
        volume_score = min(volume_ratio / self.volume_threshold, 1.0)  # 0-1
        macd_strength = (
            (macd_line - signal_line) / abs(macd_line) if macd_line != 0 else 0
        )
        macd_score = min(abs(macd_strength), 1.0)  # 0-1
        pullback_score = 1.0 - abs(pullback_pct - 0.03) / 0.02  # Best at 3%, 0-1
        pullback_score = max(0.0, min(1.0, pullback_score))

        confidence = (volume_score * 0.3) + (macd_score * 0.4) + (pullback_score * 0.3)
        confidence = max(0.0, min(1.0, confidence))

        if confidence < self.min_confidence:
            logger.debug(
                f"LONG signal rejected: Low confidence {confidence:.2f} < {self.min_confidence}"
            )
            return None

        # Create signal
        signal = Signal(
            strategy=StrategyType.MA_CROSSOVER,
            asset=symbol,
            asset_class=AssetClass.PERPETUAL,
            side=OrderSide.BUY,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            quality=TradeQuality.HIGH_CONVICTION
            if confidence > 0.8
            else TradeQuality.STANDARD,
            market_state=MarketState.TREND,
            timeframe="4h",
            pattern="golden_cross_pullback",
            volume_confirmation=volume_ratio >= self.volume_threshold,
            multi_timeframe_alignment=True,  # Golden cross confirmed
            support_resistance_valid=True,  # Fast MA acts as support
            rrr_meets_minimum=True,  # RRR = 2.0
            liquidation_buffer_safe=True,  # Will be validated externally
            account_risk_ok=True,  # Will be validated externally
            margin_drawdown_ok=True,  # Will be validated externally
            forbidden_conditions_clear=True,  # No forbidden conditions
            indicators={
                "fast_ma": fast_ma,
                "slow_ma": slow_ma,
                "macd_line": macd_line,
                "signal_line": signal_line,
                "volume_ratio": volume_ratio,
                "pullback_pct": pullback_pct,
            },
            notes=f"Golden cross pullback {pullback_pct:.1%}, volume {volume_ratio:.1f}x",
        )

        return signal

    def _create_short_signal(
        self,
        symbol: str,
        current_price: float,
        fast_ma: float,
        slow_ma: float,
        atr: float,
        macd_line: float,
        signal_line: float,
        volume_ratio: float,
        rally_pct: float,
        highs: List[float],
        lows: List[float],
    ) -> Optional[Signal]:
        """Create SHORT signal after death cross rally."""
        # Stop Loss: Above recent swing high or 2.5x ATR, whichever is closer
        swing_high = self._find_recent_swing_high(highs)
        atr_stop = current_price + (atr * self.atr_stop_multiplier)
        stop_loss = min(swing_high, atr_stop)  # Use the closer stop

        # Take Profit: 2x risk (RRR = 2.0 minimum)
        risk = stop_loss - current_price
        take_profit = current_price - (risk * 2.0)

        # Calculate confidence (0-1 scale)
        volume_score = min(volume_ratio / self.volume_threshold, 1.0)  # 0-1
        macd_strength = (
            (signal_line - macd_line) / abs(macd_line) if macd_line != 0 else 0
        )
        macd_score = min(abs(macd_strength), 1.0)  # 0-1
        rally_score = 1.0 - abs(rally_pct - 0.03) / 0.02  # Best at 3%, 0-1
        rally_score = max(0.0, min(1.0, rally_score))

        confidence = (volume_score * 0.3) + (macd_score * 0.4) + (rally_score * 0.3)
        confidence = max(0.0, min(1.0, confidence))

        if confidence < self.min_confidence:
            logger.debug(
                f"SHORT signal rejected: Low confidence {confidence:.2f} < {self.min_confidence}"
            )
            return None

        # Create signal
        signal = Signal(
            strategy=StrategyType.MA_CROSSOVER,
            asset=symbol,
            asset_class=AssetClass.PERPETUAL,
            side=OrderSide.SELL,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            quality=TradeQuality.HIGH_CONVICTION
            if confidence > 0.8
            else TradeQuality.STANDARD,
            market_state=MarketState.TREND,
            timeframe="4h",
            pattern="death_cross_rally",
            volume_confirmation=volume_ratio >= self.volume_threshold,
            multi_timeframe_alignment=True,  # Death cross confirmed
            support_resistance_valid=True,  # Fast MA acts as resistance
            rrr_meets_minimum=True,  # RRR = 2.0
            liquidation_buffer_safe=True,  # Will be validated externally
            account_risk_ok=True,  # Will be validated externally
            margin_drawdown_ok=True,  # Will be validated externally
            forbidden_conditions_clear=True,  # No forbidden conditions
            indicators={
                "fast_ma": fast_ma,
                "slow_ma": slow_ma,
                "macd_line": macd_line,
                "signal_line": signal_line,
                "volume_ratio": volume_ratio,
                "rally_pct": rally_pct,
            },
            notes=f"Death cross rally {rally_pct:.1%}, volume {volume_ratio:.1f}x",
        )

        return signal

    def _newest_crossover_in_window(
        self, closes: List[float]
    ) -> Optional[Tuple[int, str]]:
        """Locate the most recent crossover inside a supplied window.

        Deliberately independent of ``self.last_crossover``: the tracked
        state is popped once the entry window closes, so reading it would
        make the observed ``bars_since_cross`` distribution a function of
        ``max_entry_bars`` - the very threshold the calibration is meant
        to judge. Rescanning the window keeps the metric
        threshold-independent, which is what an artifact needs to stay
        valid for any candidate parameter set.

        Args:
            closes: 4h closes, oldest -> newest.

        Returns:
            ``(bars_since_crossover, "golden"|"death")`` for the newest
            crossover visible in the window, or None when there is none.
        """
        n = len(closes)
        # A crossover needs one prior bar, so the earliest evaluable
        # window end is slow_ma_period + 1 closes.
        floor = self.slow_ma_period + 1
        for end in range(n, floor - 1, -1):
            window = closes[:end]
            crossover = self._detect_crossover(
                calculate_sma(window, period=self.fast_ma_period),
                calculate_sma(window, period=self.slow_ma_period),
                calculate_sma(window[:-1], period=self.fast_ma_period),
                calculate_sma(window[:-1], period=self.slow_ma_period),
            )
            if crossover:
                return n - end, crossover
        return None

    def describe_gate_metrics(
        self,
        symbol: str,
        multi_tf_data: Dict[str, Dict[str, List[float]]],
        current_price: float,
        execution_tf_data: Optional[Dict[str, Dict[str, List[float]]]] = None,
    ) -> List[GateMetric]:
        """
        Report this strategy's two band gates for one 4h bar.

        Both are BANDS, not one-sided thresholds, and both have already
        silently disabled this strategy once:

        * ``bars_since_cross`` must land inside
          ``[min_entry_bars, max_entry_bars]``. The 2026-07-28
          investigation found this metric pinned at a constant 0 (an
          absolute list index into a rolling window), so a band starting
          at 1 was unreachable: 552 crossovers, 0 signals. A calibrated
          artifact whose ``max`` is 0 says that out loud.
        * ``pullback_pct`` must land inside
          ``[pullback_range_min, pullback_range_max]``. Measured against
          the fast MA, signed so that a golden cross wants price BELOW
          the MA and a death cross wants it above - the same quantity
          generate_signals compares.

        Both are only defined while a crossover is in view, so nothing is
        reported on bars without one. ``pullback_pct`` does depend on
        ``fast_ma_period``, which IS searched, so an artifact is only
        strictly valid at the calibrated period - the pass records it in
        provenance.

        Side-effect free: neither ``last_crossover`` nor the bar counter
        is touched.

        Args:
            symbol: Trading symbol (unused; kept for hook uniformity).
            multi_tf_data: Timeframe bundles, must contain "4h".
            current_price: Current market price.
            execution_tf_data: Unused - no execution timeframe gates entry.

        Returns:
            Two GateMetric observations, or an empty list.
        """
        data_4h = (multi_tf_data or {}).get("4h")
        if not data_4h:
            return []
        closes = data_4h.get("close") or []
        # Quiet equivalent of _validate_data: that helper logs a warning
        # per call, which a per-bar calibration pass would drown in.
        if len(closes) < self.required_history():
            return []

        found = self._newest_crossover_in_window(list(closes))
        if found is None:
            return []
        bars_since, crossover_type = found

        fast_ma = calculate_sma(closes, period=self.fast_ma_period)
        if fast_ma <= 0:
            return []
        if crossover_type == "golden":
            pullback_pct = (fast_ma - current_price) / fast_ma
        else:
            pullback_pct = (current_price - fast_ma) / fast_ma

        return [
            GateMetric(
                name="bars_since_cross",
                value=float(bars_since),
                threshold=float(self.max_entry_bars),
                direction=GATE_IN_BAND,
                param_key="max_entry_bars",
                threshold_low=float(self.min_entry_bars),
                param_key_low="min_entry_bars",
                description=(
                    "4h bars since the newest crossover visible in the "
                    "supplied window; the post-crossover entry window "
                    "every signal must land inside"
                ),
            ),
            GateMetric(
                name="pullback_pct",
                value=pullback_pct,
                threshold=float(self.pullback_range[1]),
                direction=GATE_IN_BAND,
                param_key="pullback_range_max",
                threshold_low=float(self.pullback_range[0]),
                param_key_low="pullback_range_min",
                description=(
                    "signed distance from the fast MA as a fraction of "
                    "it - pullback for a golden cross, rally for a death "
                    "cross; negative means price has run the wrong way"
                ),
            ),
        ]
