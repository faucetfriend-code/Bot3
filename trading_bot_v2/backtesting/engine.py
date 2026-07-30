"""
Backtest Engine
===============

Replays historical candles through the live strategy pipeline.

Architecture:
  BacktestEngine
    +-- BacktestDataLoader       (historical candles)
    +-- SimulatedExchange        (fake PacificaClient)
    +-- StrategyManager          (unchanged live code, handles regime internally)
    +-- RiskManager              (unchanged live code)
    +-- PerformanceTracker       (metrics accumulator)

Usage:
    engine = BacktestEngine()
    result = engine.run(
        start="2024-01-01",
        end="2024-12-31",
        symbol="SUI-USDC",
        initial_capital=10000.0,
    )
    result.print_summary()
    result.save_html("backtest_result.html")
"""

import os
from bisect import bisect_left, bisect_right
from datetime import datetime
from typing import Any, Dict, List, Optional
from loguru import logger

from ..diagnostics.funnel import (
    NULL_FUNNEL,
    REASON_EXEC_EXCEPTION,
    REASON_EXEC_HEDGE_MODE,
    REASON_EXEC_MIN_HOLD,
    REASON_EXEC_NO_PRICE,
    REASON_EXEC_PYRAMID_SPACING,
    REASON_EXEC_QTY_NON_POSITIVE,
    REASON_EXEC_SAME_DIRECTION,
    STAGE_BARS_SKIPPED_WARMUP,
    STAGE_CLOSED_TRADES,
    STAGE_EXECUTION_BLOCKED,
    STAGE_FILLS,
    STAGE_ORDERS_PLACED,
    SignalFunnel,
)
from ..models import OrderSide, StrategyType
from ..regime_param_overlay import (
    DISPLAY_TO_STRATEGY_KEY,
    apply_params_to_strategy,
    resolve_strategy_display_name,
)
from ..strategy_manager import StrategyManager
from ..risk_manager import RiskManager
from .data_loader import BacktestDataLoader, autodownload_lever
from .funding import (
    FUNDING_MODEL_HISTORICAL,
    load_funding_schedule,
    validate_funding_model,
)
from .simulated_exchange import SimulatedExchange
from .performance import PerformanceTracker, BacktestResult
from .cost_model import CostModel

# Overlays that depend on live-only surfaces SimulatedExchange cannot
# provide, so they can never produce meaningful signals in a backtest.
# Keys are the snake_case optimization identifiers (see
# regime_param_overlay.STRATEGY_KEY_TO_DISPLAY).
#
# ``funding_arb`` was here until real funding history was ingested
# (data_manager.BinanceFundingSource) and SimulatedExchange grew
# get_market_data / get_funding_history / get_balance. It is now
# backtestable - under an explicit cross-venue modelling assumption
# documented in backtesting/funding.py. ``orderbook_imbalance`` stays:
# L2 depth is genuinely absent from the store.
NON_BACKTESTABLE_STRATEGIES = frozenset({"orderbook_imbalance"})

# StrategyManager display name -> constructor enable-flag kwarg.
STRATEGY_ENABLE_FLAGS: Dict[str, str] = {
    "MeanReversion": "enable_mean_reversion",
    "MACrossover": "enable_ma_crossover",
    "GridTrading": "enable_grid_trading",
    "LiquidationCapture": "enable_liquidation_capture",
    "VWAPScalping": "enable_vwap_scalping",
    "MomentumScalping": "enable_momentum_scalping",
    "FundingArb": "enable_funding_arb",
    "OrderBookImbalance": "enable_orderbook_imbalance",
    "SessionRangeBreakout": "enable_session_range_breakout",
    "CalendarFlow": "enable_calendar_flow",
    "VWAPPullback": "enable_vwap_pullback",
}

# ---------------------------------------------------------------------------
# Execution policy
# ---------------------------------------------------------------------------
# _execute_signal drops signals that already passed all eight validity flags.
# Measured on the canonical candle store (SUI-USDC 2024-06..09, BTC-USDC
# 2024-03..06), those drops are the single largest attrition stage for several
# strategies - e.g. MeanReversion/SUI dropped 546 of 601 raw signals here.
# Every knob below therefore defaults to the behaviour that shipped, so this
# module stays bit-for-bit reproducible, and is sweepable from .env.
#
# Empirical note on pyramiding: replaying each skipped same-direction signal as
# an independent trade gives mean +0.33R (MACrossover/SUI) but -0.28R
# (MeanReversion/SUI). Allowing adds is NOT free - it is a parameter to sweep,
# which is exactly why the default stays 1.

#: Only one entry per position - a same-direction signal is skipped. Matches
#: the shipped behaviour and is the honest anti-pyramiding default.
DEFAULT_MAX_PYRAMID_ENTRIES = 1
#: Sanity ceiling. Above this, exit-order bookkeeping (one SL/TP set replaced
#: per add) stops being a meaningful model of a real position.
MAX_PYRAMID_ENTRIES_CEILING = 10
#: No enforced gap between pyramid adds (shipped behaviour is "no adds at
#: all", so any spacing default other than 0 would be inventing policy).
DEFAULT_PYRAMID_MIN_SPACING_CANDLES = 0
#: 5m replay candles a position must be held before a signal may close it.
#: Only consulted when signal-driven closes are enabled - see
#: BACKTEST_OPPOSING_CLOSES_POSITION below.
DEFAULT_MIN_HOLD_CANDLES = 6

# ---------------------------------------------------------------------------
# Data-coverage policy
# ---------------------------------------------------------------------------
# Every timeframe the replay loop loads. Order is the order they are
# reported in, cheapest-to-explain first.
COVERAGE_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h")

#: Timeframes that drive the replay itself. 5m is the bar clock - a short
#: 5m store does not shorten the *data*, it shortens the *run*, silently.
#: 1m feeds execution refinement, where _nearest_idx would otherwise serve
#: a stale candle as if it were the current minute.
EXECUTION_TIMEFRAMES = ("1m", "5m")

#: Timeframes consumed as context (regime detection, higher-TF indicators).
#: A shortfall here does not truncate the run; it freezes the context on
#: the last stored bar while the replay walks on.
CONTEXT_TIMEFRAMES = ("15m", "1h", "4h")

#: "all": any timeframe missing coverage of the requested window is fatal.
#: "execution": only 1m/5m are fatal; 15m/1h/4h log an ERROR and continue.
#: "warn": nothing is fatal, every shortfall logs an ERROR.
#: No mode is silent - "warn" is for deliberate exploration, not for
#: making a bad window look like a good one.
COVERAGE_STRICTNESS_MODES = ("all", "execution", "warn")

#: Default is "all". Justification: on the canonical store every context
#: timeframe reaches back at least as far as 1m for every symbol, so the
#: strict mode rejects nothing that the pre-existing 1m guard would have
#: allowed - while a frozen 4h slice gates every trade in the replay and
#: is no less corrupting than a truncated window. Stores where that is
#: not true can drop to "execution".
DEFAULT_COVERAGE_STRICTNESS = "all"


def validate_coverage_strictness(value: Any) -> str:
    """Validate the BACKTEST_COVERAGE_STRICTNESS mode.

    Unknown values warn and fall back rather than raising - the same
    warn-and-fall-back contract as ``validate_max_pyramid_entries``. The
    fallback is deliberately the *strict* mode: a typo must not be able
    to quietly disarm the guard.

    Args:
        value: Configured value (string or None).

    Returns:
        One of COVERAGE_STRICTNESS_MODES.
    """
    mode = str(value or "").strip().lower()
    if mode in COVERAGE_STRICTNESS_MODES:
        return mode
    if mode:
        logger.warning(
            f"BACKTEST_COVERAGE_STRICTNESS={value!r} is not one of "
            f"{'/'.join(COVERAGE_STRICTNESS_MODES)}. Falling back to "
            f"'{DEFAULT_COVERAGE_STRICTNESS}' (strictest). Fix "
            f"BACKTEST_COVERAGE_STRICTNESS in .env."
        )
    return DEFAULT_COVERAGE_STRICTNESS


def _env_or_cfg(cfg: Any, cfg_attr: str, env_name: str) -> Optional[str]:
    """Return the raw configured value for a policy knob, or None.

    ``cfg`` wins when it carries the attribute (the optimization adapter and
    the tests both drive the engine through a config proxy); otherwise the
    environment is consulted directly, so a knob can be added to ``.env``
    without also having to be threaded through ``config.py``.

    Args:
        cfg: Config object (live config or an override proxy).
        cfg_attr: Attribute name to look for on ``cfg``.
        env_name: Environment variable to fall back to.

    Returns:
        The raw string value, or None when neither source sets it.
    """
    value = getattr(cfg, cfg_attr, None)
    if value is not None:
        return str(value)
    raw = os.getenv(env_name)
    return raw if raw not in (None, "") else None


def _as_bool(raw: str) -> bool:
    """Parse a truthy config string the way ``config.py`` does."""
    return raw.strip().lower() in ("true", "1", "yes")


def validate_max_pyramid_entries(value: Any) -> int:
    """Validate the maximum number of entries allowed per open position.

    1 means "no pyramiding" (the shipped behaviour): once a position is open,
    further same-direction signals are skipped. Values above
    MAX_PYRAMID_ENTRIES_CEILING, below 1, or non-numeric are rejected with a
    warning and replaced by the default rather than raising - the same
    warn-and-fall-back contract as
    ``strategies/vwap_scalping.py::validate_sd_entry_threshold``.

    Args:
        value: Configured value (string, int or None).

    Returns:
        A usable entry cap in [1, MAX_PYRAMID_ENTRIES_CEILING].
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        logger.warning(
            f"BACKTEST_MAX_PYRAMID_ENTRIES={value!r} is not an integer. "
            f"Falling back to {DEFAULT_MAX_PYRAMID_ENTRIES} (no pyramiding)."
        )
        return DEFAULT_MAX_PYRAMID_ENTRIES
    if 1 <= parsed <= MAX_PYRAMID_ENTRIES_CEILING:
        return parsed
    logger.warning(
        f"BACKTEST_MAX_PYRAMID_ENTRIES={parsed} is outside the supported "
        f"range [1, {MAX_PYRAMID_ENTRIES_CEILING}]. Below 1 would block every "
        f"entry outright; above the ceiling the per-add exit-order model stops "
        f"being realistic. Falling back to {DEFAULT_MAX_PYRAMID_ENTRIES}. "
        f"Fix BACKTEST_MAX_PYRAMID_ENTRIES in .env."
    )
    return DEFAULT_MAX_PYRAMID_ENTRIES


def validate_non_negative_candles(value: Any, env_name: str, default: int) -> int:
    """Validate a candle-count knob that must be >= 0.

    Args:
        value: Configured value (string, int or None).
        env_name: Variable name, used in the warning so the message names the
            exact thing to fix.
        default: Value substituted when ``value`` is unusable.

    Returns:
        A non-negative candle count.
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        logger.warning(
            f"{env_name}={value!r} is not an integer. Falling back to {default}."
        )
        return default
    if parsed >= 0:
        return parsed
    logger.warning(
        f"{env_name}={parsed} is negative, which would disable the guard "
        f"silently. Falling back to {default}. Fix {env_name} in .env."
    )
    return default


class BacktestEngine:
    """
    Runs a full backtest of all enabled strategies.

    The engine advances the simulation one 5m candle at a time (matching the
    live bot's primary signal-generation cadence). Regime detection is handled
    internally by StrategyManager using the 4h/1h slice of each bundle.

    Every 60 candles (5h) an equity snapshot is recorded.
    """

    def __init__(self, override_config=None):
        # Import here to avoid circular imports and to allow override_config
        from ..config import config as live_config
        self.cfg = override_config or live_config
        # Signal funnel for this run (replaced in run(); NullFunnel until
        # then so _execute_signal is safe to call standalone in tests).
        self._funnel = NULL_FUNNEL
        # Per-run state (reset in run())
        self._hedge_mode: bool = False
        self._opposing_closes_position: bool = False
        self._min_hold_candles: int = DEFAULT_MIN_HOLD_CANDLES
        self._max_pyramid_entries: int = DEFAULT_MAX_PYRAMID_ENTRIES
        self._pyramid_min_spacing: int = DEFAULT_PYRAMID_MIN_SPACING_CANDLES
        # symbol -> replay index of the bar the CURRENT position was first
        # observed on. Maintained by _sync_position_tracking(), not by
        # _execute_signal, so a resting limit entry that fills several bars
        # later is aged from its fill and not from its order placement.
        self._position_open_candle: Dict[str, int] = {}
        # symbol -> entries placed into the current position (pyramid depth)
        self._position_entry_count: Dict[str, int] = {}
        # symbol -> replay index of the most recent entry into the position
        self._position_last_entry_candle: Dict[str, int] = {}
        # Time-exit tracking for signals carrying indicators["time_exit_hours"]:
        # {symbol: {"open_ts": datetime, "hours": float, "strategy": str}}
        self._position_time_exit: Dict[str, Dict] = {}
        self._sim_dt: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Execution policy
    # ------------------------------------------------------------------

    def _resolve_execution_policy(self) -> None:
        """Resolve and validate the four execution-policy knobs for this run.

        Every knob defaults to the behaviour that shipped before the policy was
        made configurable, so resolving it is never a behaviour change on its
        own. Nonsense values warn and fall back instead of raising.

        ``BACKTEST_HEDGE_MODE`` is retained as a deprecated alias for
        ``BACKTEST_OPPOSING_CLOSES_POSITION``. It never meant "hold both sides"
        - the code it gates sizes the order to the existing position and exits
        it - so the name has always described something the engine does not do.
        """
        hedge_raw = _env_or_cfg(
            self.cfg, "backtest_hedge_mode", "BACKTEST_HEDGE_MODE"
        )
        self._hedge_mode = _as_bool(hedge_raw) if hedge_raw is not None else False

        opposing_raw = _env_or_cfg(
            self.cfg,
            "backtest_opposing_closes_position",
            "BACKTEST_OPPOSING_CLOSES_POSITION",
        )
        # The alias is an OR, not an override: setting either one enables
        # signal-driven closes, so existing BACKTEST_HEDGE_MODE=true configs
        # keep working untouched.
        self._opposing_closes_position = self._hedge_mode or (
            _as_bool(opposing_raw) if opposing_raw is not None else False
        )

        self._min_hold_candles = validate_non_negative_candles(
            _env_or_cfg(
                self.cfg, "backtest_min_hold_candles", "BACKTEST_MIN_HOLD_CANDLES"
            )
            or DEFAULT_MIN_HOLD_CANDLES,
            "BACKTEST_MIN_HOLD_CANDLES",
            DEFAULT_MIN_HOLD_CANDLES,
        )
        self._max_pyramid_entries = validate_max_pyramid_entries(
            _env_or_cfg(
                self.cfg,
                "backtest_max_pyramid_entries",
                "BACKTEST_MAX_PYRAMID_ENTRIES",
            )
            or DEFAULT_MAX_PYRAMID_ENTRIES
        )
        self._pyramid_min_spacing = validate_non_negative_candles(
            _env_or_cfg(
                self.cfg,
                "backtest_pyramid_min_spacing_candles",
                "BACKTEST_PYRAMID_MIN_SPACING_CANDLES",
            )
            or DEFAULT_PYRAMID_MIN_SPACING_CANDLES,
            "BACKTEST_PYRAMID_MIN_SPACING_CANDLES",
            DEFAULT_PYRAMID_MIN_SPACING_CANDLES,
        )

        if self._min_hold_candles and not self._opposing_closes_position:
            logger.info(
                f"BACKTEST_MIN_HOLD_CANDLES={self._min_hold_candles} has no "
                f"effect in this run: it only gates signal-driven closes, and "
                f"BACKTEST_OPPOSING_CLOSES_POSITION is off, so opposing "
                f"signals are dropped outright (exec:hedge_mode_block) and "
                f"positions exit only via SL/TP."
            )

    def execution_policy(self) -> Dict[str, Any]:
        """Return the resolved execution policy as a JSON-safe dict.

        Attached to every funnel as the ``execution_policy`` note so a funnel
        block is self-describing: the counts under ``exec:`` are only
        interpretable against the policy that produced them.

        Returns:
            Mapping of knob name to resolved value.
        """
        return {
            "opposing_closes_position": self._opposing_closes_position,
            "min_hold_candles": self._min_hold_candles,
            "max_pyramid_entries": self._max_pyramid_entries,
            "pyramid_min_spacing_candles": self._pyramid_min_spacing,
        }

    # ------------------------------------------------------------------
    # Data-coverage guard
    # ------------------------------------------------------------------

    def coverage_strictness(self) -> str:
        """Resolve BACKTEST_COVERAGE_STRICTNESS for this run."""
        return validate_coverage_strictness(
            _env_or_cfg(
                self.cfg,
                "backtest_coverage_strictness",
                "BACKTEST_COVERAGE_STRICTNESS",
            )
        )

    def _check_data_coverage(
        self,
        loader: BacktestDataLoader,
        symbol: str,
        start: str,
        end: str,
        warmup: int,
        funnel: Any = NULL_FUNNEL,
    ) -> None:
        """Refuse (or loudly flag) a window the candle store cannot cover.

        Historically only 1m was guarded, because _nearest_idx serves the
        most recent candle at-or-before a timestamp and therefore hands
        out a stale bar - as if it were current - once a series runs out.
        Every other timeframe had the same hole: with auto-download off, a
        5m store that stops early silently truncates the replay, which is
        how two runs with different ``end`` dates came back identical.

        Two spans are measured per timeframe, and they mean different
        things:

        * The **loaded span** ``[start - warmup, end]`` is what the run
          actually reads, so it is what gets probed first (auto-download
          therefore also gets its chance at the warmup prefix).
        * The **requested window** ``[start, end]`` is what the result
          claims to describe. Only a shortfall *here* can mis-date a
          replayed bar, so only this one is ever fatal.

        A short warmup prefix is never fatal: it degrades indicator
        saturation on the first bars - the behaviour that shipped before
        warmup existed - without mis-dating anything. Making it fatal
        would reject the earliest window of the 8-year campaign, whose
        BTC-USDC 1m store begins exactly at the window start.

        Args:
            loader: Data loader for ``symbol``.
            symbol: Trading pair being backtested.
            start: Requested window start.
            end: Requested window end.
            warmup: Pre-window candles loaded per timeframe.
            funnel: Signal funnel to annotate with any degradation.

        Raises:
            ValueError: When a timeframe the active strictness mode
                treats as fatal does not cover [start, end].
        """
        strictness = self.coverage_strictness()
        failures: List[str] = []
        degraded: Dict[str, Dict[str, Any]] = {}

        for timeframe in COVERAGE_TIMEFRAMES:
            load_start = loader.shift_start(start, timeframe, warmup)
            span = loader.coverage_shortfall(timeframe, load_start, end)
            window = (
                span
                if load_start == start
                else loader.coverage_shortfall(
                    timeframe, start, end, allow_download=False
                )
            )
            available = (
                f"{span['first']} .. {span['last']}"
                if span["first"] is not None
                else f"no {timeframe} data on disk"
            )

            if not window["covered"]:
                fatal = strictness == "all" or (
                    strictness == "execution" and timeframe in EXECUTION_TIMEFRAMES
                )
                message = (
                    f"{timeframe} candle data for {symbol} does not cover the "
                    f"requested backtest window {start} .. {end} "
                    f"(available {timeframe} coverage: {available}; missing "
                    f"{window['missing_leading']} leading and "
                    f"{window['missing_trailing']} trailing {timeframe} "
                    f"candles of {window['requested']} requested). "
                    f"{autodownload_lever(timeframe)} "
                    f"Backfill it with: python -m trading_bot_v2.data_manager "
                    f"--symbols {symbol} --timeframes {timeframe}"
                )
                degraded[timeframe] = {
                    "window_covered": False,
                    "missing_leading": window["missing_leading"],
                    "missing_trailing": window["missing_trailing"],
                    "requested": window["requested"],
                    "available": available,
                    "fatal": fatal,
                }
                if fatal:
                    failures.append(message)
                else:
                    logger.error(
                        f"DEGRADED BACKTEST (BACKTEST_COVERAGE_STRICTNESS="
                        f"{strictness}): {message} Results past the last "
                        f"stored {timeframe} candle are computed against a "
                        f"FROZEN {timeframe} slice and are not trustworthy."
                    )
                continue

            if span["missing_leading"]:
                degraded[timeframe] = {
                    "window_covered": True,
                    "warmup_short_candles": span["missing_leading"],
                    "available": available,
                    "fatal": False,
                }
                logger.warning(
                    f"{timeframe} warmup prefix for {symbol} is short by "
                    f"{span['missing_leading']} candles: the {warmup}-candle "
                    f"prefix reaches back to {load_start} but {timeframe} "
                    f"data starts at {span['first']}. The requested window "
                    f"{start} .. {end} IS fully covered, so no bar is "
                    f"mis-dated - but the first replayed bars see a shorter "
                    f"history slice and their indicators are not saturated. "
                    f"{autodownload_lever(timeframe)} "
                    f"Backfill it with: python -m trading_bot_v2.data_manager "
                    f"--symbols {symbol} --timeframes {timeframe}"
                )

        funnel.note(
            "data_coverage",
            {"strictness": strictness, "warmup_candles": warmup, "degraded": degraded},
        )

        if failures:
            raise ValueError(
                "Backtest refused: candle data does not cover the requested "
                f"window (BACKTEST_COVERAGE_STRICTNESS={strictness}).\n"
                + "\n".join(failures)
            )

    # ------------------------------------------------------------------
    # Funding policy
    # ------------------------------------------------------------------

    def _venue_funding_interval_hours(self) -> int:
        """Settlement cadence of the venue being simulated, in hours.

        Read from the selected exchange adapter's capabilities
        (Pacifica 1, Blofin 8) so the interval is a property of the
        venue and not a constant baked into the simulator. Falls back to
        1 (Pacifica) if the adapter registry cannot be consulted, which
        preserves the behaviour that shipped.
        """
        raw = _env_or_cfg(
            self.cfg,
            "backtest_funding_interval_hours",
            "BACKTEST_FUNDING_INTERVAL_HOURS",
        )
        if raw is not None:
            try:
                parsed = int(float(raw))
                if parsed > 0:
                    return parsed
                logger.warning(
                    f"BACKTEST_FUNDING_INTERVAL_HOURS={raw!r} must be "
                    f"positive; falling back to the venue capability."
                )
            except (TypeError, ValueError):
                logger.warning(
                    f"BACKTEST_FUNDING_INTERVAL_HOURS={raw!r} is not a "
                    f"number; falling back to the venue capability."
                )
        try:
            from ..exchanges import get_exchange_capabilities

            hours = int(get_exchange_capabilities().funding_interval_hours)
            return hours if hours > 0 else 1
        except Exception as e:  # adapter registry unavailable
            logger.debug(f"Falling back to 1h funding interval: {e}")
            return 1

    @staticmethod
    def _funding_shortfall(schedule, start: str, end: str) -> str:
        """Describe the part of [start, end] the funding series misses.

        Args:
            schedule: Loaded FundingSchedule.
            start: Window start (ISO date).
            end: Window end (ISO date).

        Returns:
            A human-readable range string, or "" when fully covered.
        """
        if not start or not schedule.times:
            return ""
        try:
            win_start = datetime.fromisoformat(str(start)[:19])
            win_end = datetime.fromisoformat(str(end)[:19]) if end else None
        except (TypeError, ValueError):
            return ""
        first = schedule.times[0]
        if win_start >= first:
            return ""
        cut = min(first, win_end) if win_end else first
        return f"{win_start.date()}..{cut.date()}"

    def _resolve_funding_schedule(
        self, symbol: str, funnel: Any, start: str = "", end: str = ""
    ):
        """Resolve the funding model for this run and log it loudly.

        Two models exist and the difference is large enough that no run
        should be readable without knowing which one produced it:

        * ``flat`` - a constant ``BACKTEST_FUNDING_HOURLY_PCT`` charged
          every venue settlement, longs always paying and shorts always
          receiving. Every backtest published by this repo before real
          funding was ingested used it.
        * ``historical`` - the ingested Binance series mapped onto the
          venue's settlement clock (see backtesting/funding.py). Sign
          and level are the market's.

        Args:
            symbol: Symbol being replayed.
            funnel: Run funnel, annotated with the resolved model.
            start: Window start (ISO date), for the coverage check.
            end: Window end (ISO date), for the coverage check.

        Returns:
            A FundingSchedule, or None to use the flat model.
        """
        model = validate_funding_model(
            _env_or_cfg(self.cfg, "backtest_funding_model", "BACKTEST_FUNDING_MODEL")
        )
        interval = self._venue_funding_interval_hours()
        note: Dict[str, Any] = {
            "model": model,
            "venue_interval_hours": interval,
        }
        if model != FUNDING_MODEL_HISTORICAL:
            logger.warning(
                f"Funding model: FLAT {self.cfg.backtest_funding_hourly_pct:.6g} "
                f"per {interval}h settlement, sign-locked (longs always pay). "
                f"Real BTC funding averaged ~1/8 of this per hour and was "
                f"NEGATIVE on ~14% of settlements. Set "
                f"BACKTEST_FUNDING_MODEL=historical to charge the ingested "
                f"series instead."
            )
            note["flat_rate"] = float(self.cfg.backtest_funding_hourly_pct)
            funnel.note("funding_model", note)
            return None
        schedule = load_funding_schedule(
            symbol,
            self.cfg.backtest_data_dir,
            venue_interval_hours=interval,
        )
        if schedule is None:
            logger.error(
                f"BACKTEST_FUNDING_MODEL=historical but no funding store "
                f"for {symbol}; falling back to the flat model. Results "
                f"are NOT comparable with a historical-funding run."
            )
            note["model"] = "flat_fallback"
            funnel.note("funding_model", note)
            return None
        note.update(
            {
                "settlements": len(schedule),
                "conversion": schedule.conversion,
                "scale": schedule.scale,
                "factor": schedule.factor,
                "source_interval_hours": schedule.source_interval_hours,
                "funding_start": schedule.times[0].isoformat(),
                "funding_end": schedule.times[-1].isoformat(),
            }
        )
        # A window that predates the funding series is not "zero funding",
        # it is NO DATA - the schedule serves None and nothing is charged.
        # That silently looks like a costless run, so say it out loud.
        uncovered = self._funding_shortfall(schedule, start, end)
        if uncovered:
            note["window_uncovered"] = uncovered
            logger.error(
                f"Funding history does NOT cover {uncovered} of the "
                f"requested window ({start} .. {end}); the series starts "
                f"{schedule.times[0].date()}. Nothing is charged there and "
                f"FundingArb sees no rate - those bars are silently "
                f"funding-free. Shorten the span or accept the hole."
            )
        funnel.note("funding_model", note)
        logger.warning(
            "Funding model: HISTORICAL. Binance 8h rates are a PROXY for "
            "Pacifica hourly funding; the cross-venue basis is unmeasured "
            f"(BACKTEST_FUNDING_SCALE={schedule.scale:g})."
        )
        return schedule

    def run(
        self,
        start: str,
        end: str,
        symbol: Optional[str] = None,
        initial_capital: Optional[float] = None,
        strategy_filter: Optional[str] = None,
    ) -> BacktestResult:
        symbol = symbol or self.cfg.backtest_symbol
        initial_capital = initial_capital or self.cfg.backtest_initial_capital
        strategy_filter = strategy_filter or getattr(self.cfg, "backtest_strategy", "") or None

        # Initialise per-run state
        self._resolve_execution_policy()
        self._position_open_candle = {}
        self._position_entry_count = {}
        self._position_last_entry_candle = {}
        self._position_time_exit = {}
        self._sim_dt = None
        # Signal funnel: a backtest always wants diagnostics (the cost is
        # a handful of dict increments per bar - see the <3% benchmark in
        # tests/test_diagnostics.py). The live bot keeps NullFunnel.
        funnel = SignalFunnel(
            label=f"{strategy_filter or 'all'}/{symbol} {start}..{end}"
        )
        self._funnel = funnel
        funnel.note("execution_policy", self.execution_policy())

        policy = self.execution_policy()
        logger.info(
            f"Starting backtest: {symbol} | {start} -> {end} | "
            f"capital={initial_capital} | "
            f"opposing_closes_position={policy['opposing_closes_position']} | "
            f"min_hold_candles={policy['min_hold_candles']} | "
            f"max_pyramid_entries={policy['max_pyramid_entries']} | "
            f"pyramid_min_spacing_candles="
            f"{policy['pyramid_min_spacing_candles']}"
            + (f" | strategy_filter={strategy_filter}" if strategy_filter else "")
        )

        # --- Build components ---
        funding_schedule = self._resolve_funding_schedule(
            symbol, funnel, start, end
        )
        exchange = SimulatedExchange(
            initial_capital=initial_capital,
            slippage_pct=self.cfg.backtest_slippage_pct,
            taker_fee_pct=self.cfg.backtest_taker_fee_pct,
            maker_fee_pct=self.cfg.backtest_maker_fee_pct,
            funding_hourly_pct=self.cfg.backtest_funding_hourly_pct,
            funding_schedule=funding_schedule,
            funding_interval_hours=self._venue_funding_interval_hours(),
        )
        loader = BacktestDataLoader(symbol=symbol, data_dir=self.cfg.backtest_data_dir)
        risk_manager = RiskManager(client=exchange)

        # Build strategy enable kwargs for single-strategy mode
        strategy_kwargs: Dict = {}
        if strategy_filter:
            # Accept both display ("MeanReversion") and optimization
            # snake_case ("mean_reversion") strategy identifiers.
            resolved_filter = resolve_strategy_display_name(strategy_filter)
            if resolved_filter is not None:
                strategy_filter = resolved_filter
            else:
                logger.warning(
                    f"Unknown strategy_filter '{strategy_filter}' - "
                    f"no strategy will match"
                )
            for name, flag in STRATEGY_ENABLE_FLAGS.items():
                strategy_kwargs[flag] = (name == strategy_filter)
            logger.info(f"Single-strategy mode: only {strategy_filter} enabled")

        # Force-disable strategies that can never work against the
        # simulated exchange (see NON_BACKTESTABLE_STRATEGIES).
        for display_name, flag in STRATEGY_ENABLE_FLAGS.items():
            strategy_key = DISPLAY_TO_STRATEGY_KEY.get(display_name)
            if strategy_key not in NON_BACKTESTABLE_STRATEGIES:
                continue
            if strategy_kwargs.get(flag, True):
                logger.warning(
                    f"SKIPPING {display_name} in backtest mode: not "
                    f"backtestable (depends on live-only data surfaces - "
                    f"real L2 orderbook depth / funding-history API - that "
                    f"SimulatedExchange cannot provide)"
                )
            strategy_kwargs[flag] = False

        strategy_manager = StrategyManager(
            risk_manager=risk_manager,
            client=exchange,
            **strategy_kwargs,
        )
        strategy_manager.set_funnel(funnel)

        # Apply per-strategy optimization parameter overrides carried on
        # the config proxy (set by OptimizationAdapter as
        # ``_optimization_params_<strategy>``). Only whitelisted params
        # (search-space keys) are ever set on the strategy instances.
        for _display_name, _strategy_obj in strategy_manager.strategies.items():
            _strategy_key = DISPLAY_TO_STRATEGY_KEY.get(_display_name)
            if not _strategy_key:
                continue
            _params = getattr(
                self.cfg, f"_optimization_params_{_strategy_key}", None
            )
            if _params:
                _applied = apply_params_to_strategy(
                    _strategy_obj, _strategy_key, _params
                )
                if _applied:
                    logger.info(
                        f"Backtest param overrides applied to "
                        f"{_display_name}: {_applied}"
                    )

        performance = PerformanceTracker(initial_capital=initial_capital)
        cost_model = CostModel(
            slippage_pct=self.cfg.backtest_slippage_pct,
            taker_fee_pct=self.cfg.backtest_taker_fee_pct,
        )

        # --- Load candles (with a pre-window warmup prefix) ---
        # Every timeframe is loaded from `start - warmup` so the first
        # replayed bar already sees a full `lookback`-candle history slice,
        # instead of ramping up from 1 candle inside the requested window.
        lookback = max(1, int(getattr(self.cfg, "backtest_history_lookback", 60) or 60))
        warmup = int(getattr(self.cfg, "backtest_warmup_candles", 0) or 0) or lookback
        logger.info(
            f"History lookback: {lookback} candles/timeframe | "
            f"warmup prefix: {warmup} candles/timeframe"
        )

        # --- Data-coverage guard (every timeframe) ---
        self._check_data_coverage(loader, symbol, start, end, warmup, funnel)

        candles = {
            tf: loader.get_candles(tf, start, end, warmup_candles=warmup)
            for tf in ("1m", "5m", "15m", "1h", "4h")
        }

        timestamps_5m = candles["5m"]["timestamp"]
        # Sorted string views of each timeframe's timestamps, for the
        # as-of index lookup in _nearest_idx.
        sorted_ts = {
            tf: [str(t) for t in candles[tf]["timestamp"]]
            for tf in ("1m", "15m", "1h", "4h")
        }

        # First 5m index inside the requested window - everything before it
        # is warmup and is only ever read through _history().
        replay_start = self._first_index_at_or_after(timestamps_5m, start)
        total = len(timestamps_5m) - replay_start
        logger.info(
            f"Loaded {len(timestamps_5m)} 5m candles "
            f"({replay_start} warmup + {total} replayed)"
        )

        # Build reverse-lookup: 5m timestamp -> index in each higher timeframe
        idx_map = {
            tf: {ts: i for i, ts in enumerate(candles[tf]["timestamp"])}
            for tf in ("1m", "15m", "1h", "4h")
        }

        # --- Replay loop (one 5m candle at a time) ---
        for i in range(replay_start, len(timestamps_5m)):
            ts = timestamps_5m[i]
            # Advance the simulated exchange price to this candle's close
            candle_5m = self._candle_at(candles["5m"], i)
            exchange.advance(candle_5m, ts)
            # advance() fills resting orders, so position bookkeeping has to be
            # reconciled against the exchange before any signal is executed.
            self._sync_position_tracking(exchange, i)

            # --- Build multi-timeframe bundles ---
            i_15m = self._nearest_idx(idx_map["15m"], sorted_ts["15m"], ts)
            i_1h  = self._nearest_idx(idx_map["1h"],  sorted_ts["1h"],  ts)
            i_4h  = self._nearest_idx(idx_map["4h"],  sorted_ts["4h"],  ts)
            i_1m  = self._nearest_idx(idx_map["1m"],  sorted_ts["1m"],  ts)

            # Regime / structure timeframes (required by StrategyManager)
            multi_tf_data = {
                "15m": self._history(candles["15m"], i_15m, lookback),
                "1h":  self._history(candles["1h"],  i_1h,  lookback),
                "4h":  self._history(candles["4h"],  i_4h,  lookback),
            }

            # Execution timeframes (optional, for precise entry)
            execution_tf_data = {
                "5m": self._history(candles["5m"], i, lookback),
                "1m": self._history(candles["1m"], i_1m, lookback),
            }

            # Skip until we have enough 4h history for regime detection (29 candles)
            if i_4h < 28:
                funnel.count(STAGE_BARS_SKIPPED_WARMUP)
                continue

            # Advance simulated time so strategy cooldowns use candle timestamps
            try:
                sim_dt = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                sim_dt = None
            self._sim_dt = sim_dt
            strategy_manager.set_sim_time(sim_dt)

            # Drive the regime detector's injectable clock with simulated
            # time so its cache TTL / dwell / confirmation logic follows
            # candle time instead of wall-clock (otherwise the regime
            # would be computed once and served from cache for the whole
            # replay). Uses the same injection point as
            # analysis/regime_stability.py.
            if sim_dt is not None:
                strategy_manager.regime_detector._clock = lambda dt=sim_dt: dt

            # --- Time-based exits (signals carrying time_exit_hours) ---
            if sim_dt is not None and self._position_time_exit:
                self._apply_time_exits(exchange, sim_dt)

            # --- Generate signals ---
            try:
                signals = strategy_manager.generate_signals_for_market(
                    symbol=symbol,
                    multi_tf_data=multi_tf_data,
                    current_price=exchange._current_price,
                    execution_tf_data=execution_tf_data,
                )
            except Exception as e:
                logger.debug(f"Signal generation skipped at {ts}: {e}")
                signals = []

            # Expose the confirmed regime to the exchange so every fill
            # is regime-tagged (reuses the cache populated during signal
            # generation - no recomputation).
            regime_obj = strategy_manager.regime_detector.get_current_regime(
                symbol
            )
            exchange._current_regime = getattr(regime_obj, "value", "") or ""

            # --- Execute signals ---
            for signal in signals:
                try:
                    cost_model.apply(signal, exchange._current_price)
                    exchange._current_strategy = signal.strategy.value
                    executed = self._execute_signal(signal, exchange, i)
                    if executed:
                        funnel.count(STAGE_ORDERS_PLACED)
                        strategy_manager.register_trade_execution(
                            signal, {"quantity": 0, "price": exchange._current_price}
                        )
                        # Wire LiquidationCapture session tracking
                        if signal.strategy == StrategyType.LIQUIDATION_CAPTURE:
                            lc = strategy_manager.strategies.get("LiquidationCapture")
                            if lc:
                                lc.record_trade()
                except Exception as e:
                    funnel.count(STAGE_EXECUTION_BLOCKED)
                    funnel.reject(REASON_EXEC_EXCEPTION)
                    logger.debug(f"Signal execution skipped: {e}")

            # --- Equity snapshot every 60 candles (~5h) ---
            if i % 60 == 0:
                equity = float(exchange.get_account_balance()["balance"])
                performance.record_snapshot(ts, equity, exchange._positions.copy())

        # --- Finalise ---
        final_equity = float(exchange.get_account_balance()["balance"])
        result = performance.finalise(
            final_equity=final_equity,
            trade_log=exchange.trade_log,
            symbol=symbol,
            start=start,
            end=end,
            total_funding=exchange.total_funding,
        )
        # Terminal funnel stages are only known once the exchange has
        # been finalised.
        funnel.set_stage(STAGE_FILLS, result.total_trades)
        funnel.set_stage(STAGE_CLOSED_TRADES, result.closed_trades)
        result.diagnostics = funnel.to_dict()
        logger.info(
            f"Backtest complete | Final: ${final_equity:,.2f} | "
            f"Return: {result.total_return_pct:+.1f}% | "
            f"Sharpe: {result.sharpe_ratio:.2f} | "
            f"Max DD: {result.max_drawdown_pct:.1f}% | "
            f"Trades: {result.total_trades}"
        )
        return result

    # ------------------------------------------------------------------
    # Position bookkeeping
    # ------------------------------------------------------------------

    def _sync_position_tracking(
        self, exchange: SimulatedExchange, candle_idx: int
    ) -> None:
        """Reconcile per-position bookkeeping against the exchange.

        Called once per replayed bar, immediately after ``exchange.advance()``
        fills resting orders. Two things it fixes that per-signal bookkeeping
        could not:

        * A position opened by a resting limit order (GridTrading places every
          entry as a limit away from the market) appears several bars after the
          signal that ordered it. Stamping the open candle here ages the
          position from its FILL, which is what ``min_hold_candles`` is
          supposed to measure, instead of from order placement.
        * A position closed by SL/TP left ``_position_open_candle`` populated
          forever, because only the signal-driven close path popped it. Stale
          entries are cleared here, along with the pyramid counters.

        Args:
            exchange: The simulated exchange for this run.
            candle_idx: Current replay index.
        """
        positions = exchange._positions
        for asset in list(self._position_open_candle):
            if asset not in positions:
                self._position_open_candle.pop(asset, None)
                self._position_entry_count.pop(asset, None)
                self._position_last_entry_candle.pop(asset, None)
        for asset in positions:
            if asset not in self._position_open_candle:
                self._position_open_candle[asset] = candle_idx
                self._position_entry_count.setdefault(asset, 1)
                self._position_last_entry_candle.setdefault(asset, candle_idx)

    # ------------------------------------------------------------------
    # Signal execution
    # ------------------------------------------------------------------

    def _execute_signal(self, signal, exchange: SimulatedExchange, candle_idx: int) -> bool:
        """
        Translate a Signal object into a SimulatedExchange order.

        Returns True if an order was placed, False if the signal was skipped.

        This is the last gate in the pipeline, and it discards signals that
        already passed all eight validity flags. Each early return below is
        counted on the funnel under an ``exec:`` reason. Every threshold is a
        policy knob resolved by :meth:`_resolve_execution_policy`, and every
        default reproduces the behaviour that shipped.

        Same-direction signals (``exec:same_direction_skip``):
            A position already open in the signal's direction. Verified against
            live position state - across seven strategy/symbol replays, 1042 of
            1042 skips had a genuinely open same-side position (the exchange
            deletes closed positions and OCO-cancels their exits, so there is
            no stale-record case). This is anti-pyramiding, not lost re-entry.
            ``max_pyramid_entries`` > 1 permits adds; ``pyramid_min_spacing``
            candles must separate them (``exec:pyramid_spacing_block``).

        Opposing signals (``exec:hedge_mode_block``):
            Dropped unless ``opposing_closes_position`` is set. The name
            "hedge mode" is historical and misleading: enabling it never opens
            a hedge, it sizes the order to the existing position and exits it.
            Off means positions leave only via SL/TP, a time exit, or an
            explicit ``indicators["close_position"]`` exit signal.

        Min hold (``exec:min_hold_block``):
            Counted in 5m replay candles from the bar the position was first
            observed on. Only consulted when signal-driven closes are enabled,
            so it is silent (by design, not by accident) under the default.
        """
        funnel = self._funnel
        price = exchange._current_price
        if price <= 0:
            funnel.count(STAGE_EXECUTION_BLOCKED)
            funnel.reject(REASON_EXEC_NO_PRICE)
            return False

        side = "bid" if signal.side == OrderSide.BUY else "ask"
        exit_side = "ask" if signal.side == OrderSide.BUY else "bid"

        # --- Existing position check ---
        existing_pos = exchange._positions.get(signal.asset)
        is_pyramid_add = False
        if existing_pos:
            signal_side_str = "long" if signal.side == OrderSide.BUY else "short"
            if existing_pos.side == signal_side_str:
                entries = self._position_entry_count.get(signal.asset, 1)
                if entries >= self._max_pyramid_entries:
                    funnel.count(STAGE_EXECUTION_BLOCKED)
                    funnel.reject(REASON_EXEC_SAME_DIRECTION)
                    return False  # Same direction - skip duplicate entry
                last_entry = self._position_last_entry_candle.get(
                    signal.asset, candle_idx
                )
                if candle_idx - last_entry < self._pyramid_min_spacing:
                    funnel.count(STAGE_EXECUTION_BLOCKED)
                    funnel.reject(REASON_EXEC_PYRAMID_SPACING)
                    return False
                is_pyramid_add = True
            else:
                # An EXPLICIT exit is not the same thing as an opposing
                # entry. `opposing_closes_position` decides whether a
                # fresh entry signal in the other direction should be
                # REINTERPRETED as an exit; a signal that declares
                # indicators["close_position"] is not asking to be
                # reinterpreted, it is the owning strategy retiring its
                # own position. Time exits already bypass this gate for
                # the same reason. No strategy that shipped before
                # FundingArb sets the flag, so the default path is
                # untouched.
                explicit_close = bool(
                    (signal.indicators or {}).get("close_position")
                )
                # Opposing direction - signal-driven close, if permitted
                if not self._opposing_closes_position and not explicit_close:
                    funnel.count(STAGE_EXECUTION_BLOCKED)
                    funnel.reject(REASON_EXEC_HEDGE_MODE)
                    logger.debug(
                        f"Signal-driven closes disabled: blocking opposing "
                        f"{signal.side.value} signal for {signal.asset}"
                    )
                    return False

                open_candle = self._position_open_candle.get(
                    signal.asset, candle_idx
                )
                candles_held = candle_idx - open_candle
                if candles_held < self._min_hold_candles and not explicit_close:
                    funnel.count(STAGE_EXECUTION_BLOCKED)
                    funnel.reject(REASON_EXEC_MIN_HOLD)
                    logger.debug(
                        f"Min hold not met for {signal.asset}: "
                        f"{candles_held}/{self._min_hold_candles} candles - "
                        f"skipping close"
                    )
                    return False

                # Allow close: size to exactly the existing position quantity
                close_qty = existing_pos.quantity
                price_diff_pct = abs(signal.entry_price - price) / price
                if price_diff_pct > 0.001:
                    exchange.place_order(
                        symbol=signal.asset,
                        side=side,
                        quantity=str(close_qty),
                        order_type="limit",
                        price=signal.entry_price,
                    )
                else:
                    exchange.place_order(
                        symbol=signal.asset,
                        side=side,
                        quantity=str(close_qty),
                        order_type="market",
                    )
                # Closing trades need no SL/TP - the position is being exited
                self._position_open_candle.pop(signal.asset, None)
                self._position_entry_count.pop(signal.asset, None)
                self._position_last_entry_candle.pop(signal.asset, None)
                self._position_time_exit.pop(signal.asset, None)
                return True

        # --- Opening a new position (or adding to one) ---
        qty = signal.quantity
        if qty <= 0:
            # Fixed fractional sizing: 2% of available balance per trade
            available = exchange.balance
            risk_pct = getattr(self.cfg, "max_risk_per_trade", 0.02)
            qty = round((available * risk_pct) / price, 6)

        if qty <= 0:
            funnel.count(STAGE_EXECUTION_BLOCKED)
            funnel.reject(REASON_EXEC_QTY_NON_POSITIVE)
            return False

        if is_pyramid_add:
            # Replace the position's exit orders rather than stacking a second
            # SL/TP set on top. Stacked exits total more than the position, so
            # the second one to trigger over-closes and flips direction.
            exchange.cancel_all_orders(signal.asset)

        # Use limit order at entry_price if it differs from current price
        # by more than 0.1%, otherwise use market order for immediate fill
        price_diff_pct = abs(signal.entry_price - price) / price
        if price_diff_pct > 0.001:
            exchange.place_order(
                symbol=signal.asset,
                side=side,
                quantity=str(qty),
                order_type="limit",
                price=signal.entry_price,
            )
        else:
            exchange.place_order(
                symbol=signal.asset,
                side=side,
                quantity=str(qty),
                order_type="market",
            )

        # Size the exits off the position that actually exists now, never off
        # the requested quantity. A market entry has already filled, so this is
        # the full (possibly pyramided) position; a resting limit entry has not,
        # so exits cover only the requested size exactly as before. Either way
        # the exits can never total more than the position and over-close it.
        position_after = exchange._positions.get(signal.asset)
        exit_qty = position_after.quantity if position_after is not None else qty

        # A market order fills inside place_order(), so stamp the open candle
        # now to keep min-hold ageing identical to the pre-policy engine. A
        # resting limit entry has no position yet; _sync_position_tracking()
        # stamps that one from the bar it actually fills on.
        if position_after is not None and signal.asset not in self._position_open_candle:
            self._position_open_candle[signal.asset] = candle_idx

        if is_pyramid_add:
            self._position_entry_count[signal.asset] = (
                self._position_entry_count.get(signal.asset, 1) + 1
            )
        else:
            self._position_entry_count[signal.asset] = 1
        self._position_last_entry_candle[signal.asset] = candle_idx

        # Track time-based exit if the signal requests one (e.g. SessionRangeBreakout)
        time_exit_hours = (signal.indicators or {}).get("time_exit_hours")
        if time_exit_hours and self._sim_dt is not None:
            self._position_time_exit[signal.asset] = {
                "open_ts": self._sim_dt,
                "hours": float(time_exit_hours),
                "strategy": signal.strategy.value,
            }

        # Place exit orders. Grid signals use stop-only (the opposing grid limit
        # order acts as TP when price reaches it). All other strategies get both.
        is_grid = signal.strategy == StrategyType.GRID_TRADING
        if is_grid:
            if signal.stop_loss and signal.stop_loss > 0:
                exchange.place_order(
                    symbol=signal.asset,
                    side=exit_side,
                    quantity=str(exit_qty),
                    order_type="stop",
                    price=signal.stop_loss,
                )
        else:
            if signal.stop_loss and signal.stop_loss > 0:
                exchange.place_order(
                    symbol=signal.asset,
                    side=exit_side,
                    quantity=str(exit_qty),
                    order_type="stop",
                    price=signal.stop_loss,
                )
            if signal.take_profit and signal.take_profit > 0:
                exchange.place_order(
                    symbol=signal.asset,
                    side=exit_side,
                    quantity=str(exit_qty),
                    order_type="limit",
                    price=signal.take_profit,
                )

        return True

    # ------------------------------------------------------------------
    # Time-based exits
    # ------------------------------------------------------------------

    def _apply_time_exits(self, exchange: SimulatedExchange, sim_dt: datetime) -> None:
        """
        Close open positions whose originating signal set a max hold time.

        Signals that carry indicators["time_exit_hours"] (e.g.
        SessionRangeBreakout) are tracked in _position_time_exit at open.
        Once the position's age exceeds its limit it is closed at the
        current bar close via a market order (reason: time_exit). SL/TP
        orders are cancelled automatically by the exchange's OCO cleanup
        when the position fully closes.
        """
        for symbol in list(self._position_time_exit.keys()):
            info = self._position_time_exit[symbol]
            pos = exchange._positions.get(symbol)
            if pos is None:
                # Already closed by SL/TP - drop stale tracking
                del self._position_time_exit[symbol]
                continue

            age_hours = (sim_dt - info["open_ts"]).total_seconds() / 3600.0
            if age_hours < info["hours"]:
                continue

            close_side = "ask" if pos.side == "long" else "bid"
            exchange._current_strategy = info.get("strategy", "")
            exchange.place_order(
                symbol=symbol,
                side=close_side,
                quantity=str(pos.quantity),
                order_type="market",
            )
            del self._position_time_exit[symbol]
            self._position_open_candle.pop(symbol, None)
            self._position_entry_count.pop(symbol, None)
            self._position_last_entry_candle.pop(symbol, None)
            logger.debug(
                f"time_exit: closed {symbol} {pos.side} after {age_hours:.1f}h "
                f"(limit {info['hours']}h)"
            )

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _candle_at(candles: Dict, idx: int) -> Dict:
        return {k: candles[k][idx] for k in ("open", "high", "low", "close", "volume")}

    @staticmethod
    def _history(candles: Dict, up_to: int, lookback: int) -> Dict:
        """Return a slice of candles up to and including up_to index.

        Includes the "timestamp" list (ISO-8601 strings from the data loader)
        so time-aware strategies (e.g. SessionRangeBreakout) can locate
        session windows within the slice.
        """
        start = max(0, up_to - lookback + 1)
        return {k: candles[k][start: up_to + 1] for k in candles}

    @staticmethod
    def _nearest_idx(idx_map: Dict, sorted_ts: List[str], ts) -> int:
        """Return the most recent higher-TF index at or before ts.

        Exact hit first (the common case, since higher-TF bars land on 5m
        boundaries), otherwise an as-of lookup over the timeframe's sorted
        timestamps. Timestamps are canonical "%Y-%m-%dT%H:%M:%S" strings, so
        lexicographic order matches chronological order.

        A positional estimate (5m_index // ratio) is deliberately not used:
        it breaks as soon as the series carry a warmup prefix or contain
        gaps, and silently serves candles from the wrong date.
        """
        if ts in idx_map:
            return idx_map[ts]
        return max(0, bisect_right(sorted_ts, str(ts)) - 1)

    @staticmethod
    def _first_index_at_or_after(timestamps: List, boundary: str) -> int:
        """Index of the first timestamp at or after boundary (len if none)."""
        return bisect_left([str(t) for t in timestamps], str(boundary))
