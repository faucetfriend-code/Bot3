"""
Standalone Validation Runner (P5)
=================================

Independent service that periodically re-validates enabled strategies
over a SERIES OF SMALLER BACKTEST WINDOWS (chunked evaluation - house
style) and persists structured verdicts to the validation_runs table.

Process independence:
    - Runs in its own process with its own DatabaseManager connection.
    - Never imports (or touches) the live bot runtime: no
      trading_bot.py, no api_server.py, no websocket clients.
    - Backtests replay offline candle CSVs; the live bot keeps trading
      undisturbed while this service runs.
    - The bot's API serves the stored verdicts READ-ONLY
      (GET /api/validation/runs, GET /api/validation/latest).

Chunked evaluation:
    For each strategy the runner cuts N windows of M months each and
    backtests every (symbol, window) pair in isolation. Per-chunk
    results are stored, and the standing gate checks (min trades, profit
    factor, PSR/DSR with the trial-registry N, cross-symbol consistency)
    are computed over the POOLED per-symbol chunk returns.

Window modes:
    spread (default)
        The N windows are distributed evenly across a multi-YEAR span
        (VALIDATION_SPAN_YEARS, default 8) ending at the data anchor, so
        the series samples several market epochs at a runtime that
        depends only on N x M - not on the span. Out-of-sample seams sit
        between every pair of windows.
    recent
        The legacy behaviour: N abutting windows walking backward from
        the anchor, i.e. one contiguous block of N x M months.

Per-symbol spans:
    Symbols list at different times (SUI-USDC only exists from
    2023-05-03, BTC-USDC 1m from 2018-01-01). By default each symbol
    gets its own window series over its OWN coverage, and the run
    summary prints the per-symbol span so an 8-year BTC record is never
    silently compared against a 3-year SUI one. Pass --shared-windows to
    force one intersected series instead.

CLI:
    python -m trading_bot_v2.validation.runner \\
        [--strategies all|mean_reversion,momentum_scalping] \\
        [--symbols SUI-USDC,BTC-USDC] [--window-months 2] [--windows 6] \\
        [--window-mode spread|recent] [--span-years 8] \\
        [--shared-windows] [--dry-run] \\
        [--objective sharpe] [--capital 10000] [--refresh-data] \\
        [--loop-hours H | --once]

Env defaults (flags override): VALIDATION_WINDOW_MONTHS,
VALIDATION_WINDOWS, VALIDATION_WINDOW_MODE, VALIDATION_SPAN_YEARS,
VALIDATION_PER_SYMBOL_SPAN, VALIDATION_SYMBOLS,
VALIDATION_REFRESH_DATA, VALIDATION_SECONDS_PER_MONTH.
"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from .gate import GateVerdict, evaluate_strategy_gate
from .statistics import closed_trade_returns

# Static snake_case-strategy -> (ENABLE_* env var, default) map.
# Mirrors StrategyManager's constructor flags WITHOUT constructing the
# live bot (defaults match trading_bot_v2/strategy_manager.py).
STRATEGY_ENV_FLAGS: Dict[str, Tuple[str, bool]] = {
    "mean_reversion": ("ENABLE_MEAN_REVERSION", True),
    "ma_crossover": ("ENABLE_MA_CROSSOVER", True),
    "grid_trading": ("ENABLE_GRID_TRADING", True),
    "liquidation_capture": ("ENABLE_LIQUIDATION_CAPTURE", True),
    "vwap_scalping": ("ENABLE_VWAP_SCALPING", True),
    "funding_arb": ("ENABLE_FUNDING_ARB", False),
    "momentum_scalping": ("ENABLE_MOMENTUM_SCALPING", True),
    "orderbook_imbalance": ("ENABLE_ORDERBOOK_IMBALANCE", True),
    "session_range_breakout": ("ENABLE_SESSION_RANGE_BREAKOUT", False),
    "calendar_flow": ("ENABLE_CALENDAR_FLOW", False),
    "vwap_pullback": ("ENABLE_VWAP_PULLBACK", False),
}

DEFAULT_SYMBOLS = "SUI-USDC,BTC-USDC"
DEFAULT_WINDOW_MONTHS = 2
DEFAULT_WINDOWS = 6
DEFAULT_CAPITAL = 10000.0

#: Calendar years the window series reaches back over in "spread" mode.
#: 8 years clears BTC's 1m floor (2018-01-01) and covers the 2018 bear,
#: the 2020 crash, the 2021 bull, the 2022 bear and everything since.
DEFAULT_SPAN_YEARS = 8

#: "spread" distributes the windows across DEFAULT_SPAN_YEARS; "recent"
#: is the legacy contiguous block walking back from the anchor.
DEFAULT_WINDOW_MODE = "spread"
WINDOW_MODES = ("spread", "recent")

#: Wall-clock seconds per (symbol, strategy, window-month) of 5m replay.
#: Measured at ~5s/month for a regime-gated strategy (mean_reversion,
#: 3x2mo x 2 symbols in 60s); 8 leaves headroom for overlay strategies
#: that are invoked on every bar. Override: VALIDATION_SECONDS_PER_MONTH.
DEFAULT_SECONDS_PER_MONTH = 8.0

#: Regime share above which a run is flagged as single-epoch: a verdict
#: earned almost entirely inside one regime is a fit, not a validation.
REGIME_CONCENTRATION_WARN = 0.70

_shutdown_requested = False


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean env var (true/1/yes/on, false/0/no/off)."""
    value = os.getenv(name, "").lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    return default


def _env_int(name: str, default: int) -> int:
    """Read an int env var, falling back to ``default`` when unusable."""
    try:
        return int(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float env var, falling back to ``default`` when unusable."""
    try:
        return float(str(os.getenv(name, "")).strip())
    except (TypeError, ValueError):
        return default


def _env_mode(name: str, default: str) -> str:
    """Read a window-mode env var, falling back on an unknown value."""
    value = str(os.getenv(name, "")).strip().lower()
    return value if value in WINDOW_MODES else default


def discover_enabled_strategies() -> List[str]:
    """Strategies whose ENABLE_* env flag resolves true.

    Reads the static STRATEGY_ENV_FLAGS map so no live-bot component is
    ever constructed.

    Returns:
        Snake_case strategy keys, in map order.
    """
    return [
        name
        for name, (env_var, default) in STRATEGY_ENV_FLAGS.items()
        if _env_bool(env_var, default)
    ]


def _shift_months(d: date, months: int) -> date:
    """Shift a date by a signed number of calendar months.

    The day-of-month is clamped to the target month's length
    (e.g. Jan 31 - 2 months -> Nov 30).

    Args:
        d: Anchor date.
        months: Signed month delta.

    Returns:
        Shifted date.
    """
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = d.day
    while day > 28:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, day)


def compute_chunk_windows(
    data_end: date,
    window_months: int,
    n_windows: int,
    data_start: Optional[date] = None,
) -> List[Tuple[str, str]]:
    """Cut N chunked windows walking backward from the data end.

    The newest window ends at data_end; each earlier window abuts the
    next. When data_start is given, a window reaching past it is
    clamped to data_start (partial coverage) and iteration stops.

    Args:
        data_end: Last date with candle data.
        window_months: Length of each window in calendar months.
        n_windows: Number of windows requested.
        data_start: Optional first date with candle data.

    Returns:
        List of (start_iso, end_iso) tuples ordered oldest -> newest.
    """
    if window_months <= 0 or n_windows <= 0:
        return []
    windows: List[Tuple[date, date]] = []
    end = data_end
    for _ in range(n_windows):
        if data_start is not None and end <= data_start:
            break
        start = _shift_months(end, -window_months)
        if data_start is not None and start < data_start:
            windows.append((data_start, end))
            break
        windows.append((start, end))
        end = start
    windows.reverse()
    return [(s.isoformat(), e.isoformat()) for s, e in windows]


def _month_span(start: date, end: date) -> int:
    """Whole calendar months between two dates (0 when end <= start).

    Args:
        start: Earlier date.
        end: Later date.

    Returns:
        Number of complete months from start to end.
    """
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def compute_spread_windows(
    data_end: date,
    window_months: int,
    n_windows: int,
    data_start: Optional[date] = None,
) -> List[Tuple[str, str]]:
    """Spread N windows evenly across the whole available span.

    The oldest window starts at data_start, the newest ends at data_end,
    and the rest are placed at equal intervals between them. Runtime
    therefore depends on ``n_windows * window_months`` alone, while the
    calendar reach is the full span - which is the point: many small
    out-of-sample windows sampling several market epochs instead of one
    contiguous recent block.

    When the span cannot fit N non-overlapping windows the series
    degrades to the contiguous layout of compute_chunk_windows, so a
    short-history symbol still gets a usable (if packed) series.

    Args:
        data_end: Last date with candle data (anchor).
        window_months: Length of each window in calendar months.
        n_windows: Number of windows requested.
        data_start: First date with candle data. None or a start at/after
            data_end falls back to the contiguous layout.

    Returns:
        List of (start_iso, end_iso) tuples ordered oldest -> newest.
    """
    if window_months <= 0 or n_windows <= 0:
        return []
    if data_start is None or data_start >= data_end:
        return compute_chunk_windows(
            data_end, window_months, n_windows, data_start=data_start
        )
    span = _month_span(data_start, data_end)
    if n_windows == 1 or span < window_months * n_windows:
        return compute_chunk_windows(
            data_end, window_months, n_windows, data_start=data_start
        )

    free = span - window_months * n_windows
    windows: List[Tuple[date, date]] = []
    for i in range(n_windows):
        offset = window_months * i + round(free * i / (n_windows - 1))
        start = _shift_months(data_start, offset)
        end = _shift_months(start, window_months)
        if i == n_windows - 1 or end >= data_end:
            # Pin the newest window to the anchor, keeping its length
            # exactly window_months so every chunk costs the same.
            end = data_end
            start = _shift_months(data_end, -window_months)
        if start < data_start:
            start = data_start
        windows.append((start, end))
    return [(s.isoformat(), e.isoformat()) for s, e in windows]


def cut_windows(
    data_end: date,
    window_months: int,
    n_windows: int,
    data_start: Optional[date] = None,
    mode: str = DEFAULT_WINDOW_MODE,
) -> List[Tuple[str, str]]:
    """Cut a window series in the requested mode.

    Args:
        data_end: Last date with candle data (anchor).
        window_months: Length of each window in calendar months.
        n_windows: Number of windows requested.
        data_start: Optional first date with candle data.
        mode: "spread" (evenly distributed across the span) or "recent"
            (contiguous, walking backward from the anchor).

    Returns:
        List of (start_iso, end_iso) tuples ordered oldest -> newest.
    """
    if mode == "recent":
        return compute_chunk_windows(
            data_end, window_months, n_windows, data_start=data_start
        )
    return compute_spread_windows(
        data_end, window_months, n_windows, data_start=data_start
    )


def _data_coverage(
    symbol: str, data_dir: str
) -> Optional[Tuple[date, date]]:
    """First and last candle dates available for a symbol.

    Uses the offline BacktestDataLoader (5m timeframe, the backtest
    cadence) - no network, no live bot.

    Args:
        symbol: Trading pair (e.g. "SUI-USDC").
        data_dir: Candle data directory.

    Returns:
        (first_date, last_date), or None when no data is on disk.
    """
    from ..backtesting.data_loader import BacktestDataLoader

    try:
        loader = BacktestDataLoader(symbol=symbol, data_dir=data_dir)
        candles = loader.get_candles("5m", "2000-01-01", "2100-01-01")
    except Exception as e:
        logger.warning(f"No candle data for {symbol}: {e}")
        return None
    timestamps = candles.get("timestamp") or []
    if not timestamps:
        return None
    first = date.fromisoformat(str(timestamps[0])[:10])
    last = date.fromisoformat(str(timestamps[-1])[:10])
    return first, last


def _coverage_1m_end(symbol: str, data_dir: str) -> Optional[date]:
    """Last date with locally stored 1m candle data for a symbol.

    Uses BacktestDataLoader.coverage_bounds, which only inspects the
    local store - no network, no live bot. 1m matters because the
    backtest engine refuses to run a window that 1m data does not
    cover, and 1m is excluded from auto-download (manual top-up).

    Args:
        symbol: Trading pair (e.g. "SUI-USDC").
        data_dir: Candle data directory.

    Returns:
        Date of the last 1m candle, or None when no 1m data is on disk.
    """
    from ..backtesting.data_loader import BacktestDataLoader

    try:
        loader = BacktestDataLoader(symbol=symbol, data_dir=data_dir)
        bounds = loader.coverage_bounds("1m")
    except Exception as e:
        logger.warning(f"1m coverage check failed for {symbol}: {e}")
        return None
    if bounds is None:
        return None
    return date.fromisoformat(str(bounds[1])[:10])


def _day_ceiling(timestamp: str) -> date:
    """Round an ISO timestamp UP to a whole day.

    Window bounds are dates, i.e. midnight. A store whose first candle
    is 2023-05-03T12:00 does NOT cover a window starting 2023-05-03, and
    the engine's 1m guard rejects it - SUI-USDC listed mid-day, so this
    is the real listing-boundary case, not a hypothetical.

    Args:
        timestamp: ISO date or datetime string.

    Returns:
        The same day when the time is midnight, otherwise the next day.
    """
    day = date.fromisoformat(str(timestamp)[:10])
    time_part = str(timestamp)[11:].strip()
    if time_part and time_part.replace(":", "").replace(".", "").strip(
        "0"
    ):
        return day + timedelta(days=1)
    return day


def _coverage_1m_start(symbol: str, data_dir: str) -> Optional[date]:
    """First date with locally stored 1m candle data for a symbol.

    The mirror image of :func:`_coverage_1m_end`, and the reason a
    multi-year span cannot simply use the 5m start: BTC-USDC 5m reaches
    2017-08-17 but its 1m store begins 2018-01-01, and the engine's 1m
    guard would reject every window in between.

    Args:
        symbol: Trading pair (e.g. "BTC-USDC").
        data_dir: Candle data directory.

    Returns:
        Date of the first 1m candle, or None when no 1m data is on disk.
    """
    from ..backtesting.data_loader import BacktestDataLoader

    try:
        loader = BacktestDataLoader(symbol=symbol, data_dir=data_dir)
        bounds = loader.coverage_bounds("1m")
    except Exception as e:
        logger.warning(f"1m coverage check failed for {symbol}: {e}")
        return None
    if bounds is None:
        return None
    return _day_ceiling(str(bounds[0]))


def refresh_market_data(
    symbols: List[str], data_dir: Optional[str] = None
) -> None:
    """Top up the 1m candle store for each symbol before validating.

    Opt-in only (--refresh-data / VALIDATION_REFRESH_DATA): runs
    CandleDownloadManager in update mode for 1m - from the last stored
    candle up to now. 5m/15m/1h/4h stay on the loader's existing
    auto-download path. Symbols with no 1m store at all are skipped
    (a from-scratch multi-year 1m pull is huge - backfill manually).

    Args:
        symbols: Trading pairs to refresh.
        data_dir: Candle data dir override (default: config).
    """
    from ..config import config as cfg
    from ..data_manager import CandleDownloadManager

    resolved_dir = data_dir or cfg.backtest_data_dir
    manager = CandleDownloadManager(data_dir=resolved_dir)
    for symbol in symbols:
        try:
            cov = manager.coverage(symbol, "1m")
            if not cov.end:
                logger.warning(
                    f"refresh-data: no existing 1m store for {symbol}, "
                    f"skipping (backfill manually with: python -m "
                    f"trading_bot_v2.data_manager --symbols {symbol} "
                    f"--timeframes 1m)"
                )
                continue
            summary = manager.ensure(
                symbol, "1m", cov.end, include_internal_gaps=False
            )
            logger.info(
                f"refresh-data: {symbol} 1m +{summary.get('added', 0)} "
                f"candles (updated from {cov.end})"
            )
        except Exception as e:
            logger.warning(f"refresh-data failed for {symbol} 1m: {e}")


def resolve_chunk_windows(
    symbols: List[str],
    window_months: int,
    n_windows: int,
    data_dir: Optional[str] = None,
    label: str = "chunks",
    anchor_end: Optional[str] = None,
    mode: Optional[str] = None,
    span_years: Optional[int] = None,
    per_symbol: Optional[bool] = None,
) -> Dict[str, Any]:
    """Resolve the chunk window series for a set of symbols.

    Two series are produced:

    * ``windows`` - one SHARED series over the intersected coverage of
      every symbol. This is what makes per-symbol results directly
      comparable, and it is what the chunked optimizer sweep consumes.
    * ``windows_by_symbol`` - a per-symbol series cut over that symbol's
      OWN coverage. SUI-USDC only exists from 2023-05-03 while BTC-USDC
      1m reaches 2018-01-01, so the shared series would otherwise throw
      away five years of BTC history. Present only when ``per_symbol``.

    Coverage bounds are clamped to 1m execution data at BOTH ends (the
    backtest engine refuses a window its 1m data does not cover), then
    the windows are cut in the requested mode.

    Shared by the validation runner and the chunked optimizer sweep
    (optimization/optuna_runner.py::optimize_chunked) so both evaluate
    exactly the same windows.

    Args:
        symbols: Trading pairs to intersect.
        window_months: Months per chunk window.
        n_windows: Number of chunk windows.
        data_dir: Candle data dir override (default: config).
        label: Log prefix, for context.
        anchor_end: Optional ISO date to end the newest window on,
            instead of the data end. Only ever moves the anchor EARLIER
            (a later anchor would ask for candles that do not exist).
            Useful when the automatic anchor lands in a hole in a
            higher-timeframe store that 5m coverage cannot see.
        mode: "spread" or "recent" (default: VALIDATION_WINDOW_MODE,
            else "spread").
        span_years: Calendar years the spread series reaches back over
            (default: VALIDATION_SPAN_YEARS, else 8). Ignored in
            "recent" mode. A span longer than the data is clamped to the
            data.
        per_symbol: Also cut a per-symbol series over each symbol's own
            coverage (default: VALIDATION_PER_SYMBOL_SPAN, else True).

    Returns:
        Dict with keys: symbols (those that had data), windows (shared
        series of (start, end) ISO pairs), windows_by_symbol, coverage
        (per-symbol span metadata), data_start, data_end (ISO or None),
        mode, span_years, and reason (set only when no windows could be
        produced).
    """
    from ..config import config as cfg

    resolved_dir = data_dir or cfg.backtest_data_dir
    mode = mode or _env_mode("VALIDATION_WINDOW_MODE", DEFAULT_WINDOW_MODE)
    if mode not in WINDOW_MODES:
        mode = DEFAULT_WINDOW_MODE
    if span_years is None:
        span_years = _env_int("VALIDATION_SPAN_YEARS", DEFAULT_SPAN_YEARS)
    span_years = max(1, int(span_years))
    if per_symbol is None:
        per_symbol = _env_bool("VALIDATION_PER_SYMBOL_SPAN", True)

    out: Dict[str, Any] = {
        "symbols": [],
        "windows": [],
        "windows_by_symbol": {},
        "coverage": {},
        "data_start": None,
        "data_end": None,
        "mode": mode,
        "span_years": span_years,
    }

    coverage = {}
    for symbol in symbols:
        cov = _data_coverage(symbol, resolved_dir)
        if cov is None:
            logger.warning(f"[{label}] skipping symbol {symbol}: no data")
            continue
        coverage[symbol] = cov
    if not coverage:
        out["reason"] = "no candle data for any symbol"
        return out

    # Clamp each symbol's usable span to its 1m execution coverage. The
    # 5m store auto-downloads up to now and reaches back to the Binance
    # listing, but 1m is topped up manually and starts later, so an
    # unclamped span would ask for windows the engine's 1m guard
    # rejects. A symbol with no 1m data at all contributes no clamp -
    # the engine's guard stays the loud failure path.
    m1_ends: Dict[str, date] = {}
    per_symbol_bounds: Dict[str, Tuple[date, date]] = {}
    for symbol, (sym_start, sym_end) in coverage.items():
        m1_start = _coverage_1m_start(symbol, resolved_dir)
        m1_end = _coverage_1m_end(symbol, resolved_dir)
        if m1_start is not None and m1_start > sym_start:
            sym_start = m1_start
        if m1_end is not None:
            m1_ends[symbol] = m1_end
            if m1_end < sym_end:
                sym_end = m1_end
        per_symbol_bounds[symbol] = (sym_start, sym_end)

    data_start = max(b[0] for b in per_symbol_bounds.values())
    data_end = min(b[1] for b in per_symbol_bounds.values())
    unclamped_end = min(c[1] for c in coverage.values())
    if m1_ends and data_end < unclamped_end:
        min_symbol = min(m1_ends, key=lambda s: m1_ends[s])
        logger.info(
            f"[{label}] window anchor clamped to 1m coverage "
            f"end of {min_symbol} (min across "
            f"{','.join(sorted(m1_ends))}): "
            f"{unclamped_end.isoformat()} -> {data_end.isoformat()}"
        )

    if anchor_end:
        requested = date.fromisoformat(str(anchor_end)[:10])
        if requested < data_end:
            logger.info(
                f"[{label}] window anchor moved back on request: "
                f"{data_end.isoformat()} -> {requested.isoformat()}"
            )
            data_end = requested
        elif requested > data_end:
            logger.warning(
                f"[{label}] requested anchor {requested.isoformat()} is "
                f"beyond available coverage - keeping "
                f"{data_end.isoformat()}"
            )

    def _span_start(first: date, last: date) -> date:
        """Clamp the requested span to the data actually present."""
        if mode != "spread":
            return first
        wanted = _shift_months(last, -12 * span_years)
        return max(first, wanted)

    out["symbols"] = list(coverage)
    out["data_start"] = data_start.isoformat()
    out["data_end"] = data_end.isoformat()
    out["windows"] = cut_windows(
        data_end,
        window_months,
        n_windows,
        data_start=_span_start(data_start, data_end),
        mode=mode,
    )

    # "listing_limited" means this symbol's usable history starts more
    # than half a year after the longest-history symbol in the run - its
    # record is structurally shorter and must not be read as an equal
    # vote. The 6-month floor keeps store-boundary noise (BTC 1m starts
    # 2018-01, ETH 1m 2017-08) from being flagged as an asymmetry.
    earliest = min(b[0] for b in per_symbol_bounds.values())
    for symbol, (sym_start, sym_end) in per_symbol_bounds.items():
        sym_end = min(sym_end, data_end)
        entry: Dict[str, Any] = {
            "data_start": sym_start.isoformat(),
            "data_end": sym_end.isoformat(),
            "months": _month_span(sym_start, sym_end),
            "listing_limited": _month_span(earliest, sym_start) >= 6,
        }
        if per_symbol and sym_end > sym_start:
            sym_windows = cut_windows(
                sym_end,
                window_months,
                n_windows,
                data_start=_span_start(sym_start, sym_end),
                mode=mode,
            )
            out["windows_by_symbol"][symbol] = sym_windows
            if sym_windows:
                entry["window_start"] = sym_windows[0][0]
                entry["window_end"] = sym_windows[-1][1]
                entry["n_windows"] = len(sym_windows)
        out["coverage"][symbol] = entry

    if not out["windows"] and not out["windows_by_symbol"]:
        out["reason"] = "no usable windows in data coverage"
    return out


def estimate_runtime_seconds(
    n_strategies: int,
    n_symbols: int,
    n_windows: int,
    window_months: int,
    seconds_per_month: Optional[float] = None,
) -> float:
    """Estimate wall-clock seconds for a chunked validation campaign.

    Runtime scales with total replayed bars, which is
    ``strategies x symbols x windows x window_months`` months of 5m
    candles. The per-month constant is machine-specific; override it
    with VALIDATION_SECONDS_PER_MONTH once measured locally.

    Args:
        n_strategies: Strategies to validate.
        n_symbols: Symbols per strategy.
        n_windows: Windows per symbol.
        window_months: Months per window.
        seconds_per_month: Override for the per-window-month constant.

    Returns:
        Estimated seconds (0.0 when any factor is non-positive).
    """
    if seconds_per_month is None:
        seconds_per_month = _env_float(
            "VALIDATION_SECONDS_PER_MONTH", DEFAULT_SECONDS_PER_MONTH
        )
    total_months = (
        max(0, n_strategies)
        * max(0, n_symbols)
        * max(0, n_windows)
        * max(0, window_months)
    )
    return float(total_months) * float(seconds_per_month)


def format_duration(seconds: float) -> str:
    """Render a duration as a compact human string (e.g. "1h 12m")."""
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return f"{seconds:.0f}s"
    minutes = seconds / 60.0
    if minutes < 90:
        return f"{minutes:.0f}m"
    hours = int(minutes // 60)
    rest = int(minutes - hours * 60)
    return f"{hours}h {rest:02d}m"


class _DataDirConfig:
    """Config proxy pinning backtest_data_dir to an explicit path.

    ``.env`` sets BACKTEST_DATA_DIR to a RELATIVE path and config.py
    loads it with override=True, so exporting the variable does nothing
    and a git worktree resolves it to its own (parquet-less) directory.
    Redirecting in-process is the only way to point a chunk backtest at
    the canonical store.
    """

    def __init__(self, base: Any, data_dir: str):
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_data_dir", data_dir)

    def __getattr__(self, name: str) -> Any:
        if name == "backtest_data_dir":
            return object.__getattribute__(self, "_data_dir")
        return getattr(object.__getattribute__(self, "_base"), name)


def _run_chunk_backtest(
    strategy_key: str,
    symbol: str,
    start: str,
    end: str,
    capital: float,
    data_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Backtest one (strategy, symbol, window) chunk offline.

    A fresh BacktestEngine is built per chunk so no state leaks between
    windows.

    Args:
        strategy_key: Snake_case strategy key.
        symbol: Trading pair.
        start: Window start (ISO date).
        end: Window end (ISO date).
        capital: Initial capital for the chunk.
        data_dir: Candle store to read. Defaults to the configured one.
            Window resolution already honours this; passing it here keeps
            the backtest reading the SAME store the windows were cut from.

    Returns:
        Dict with "returns" (per-trade fractional returns of the chunk's
        closed trades), "regimes" (regime value -> bars observed in the
        window), "regime_trades" (regime value -> closed trades ENTERED
        in that regime) and "diagnosis" (the funnel's outcome).
    """
    from ..backtesting.engine import BacktestEngine

    override = None
    if data_dir:
        from ..config import config as base_config

        if str(data_dir) != str(
            getattr(base_config, "backtest_data_dir", "")
        ):
            override = _DataDirConfig(base_config, str(data_dir))

    engine = BacktestEngine(override_config=override)
    result = engine.run(
        start=start,
        end=end,
        symbol=symbol,
        initial_capital=capital,
        strategy_filter=strategy_key,
    )
    diagnostics = getattr(result, "diagnostics", None) or {}
    return {
        "returns": closed_trade_returns(result.trade_log, capital),
        "regimes": dict(diagnostics.get("regimes") or {}),
        "regime_trades": {
            str(regime): int(cell.get("closed_trades", 0))
            for regime, cell in (getattr(result, "by_regime", None) or {}).items()
        },
        "diagnosis": diagnostics.get("diagnosis"),
    }


def _normalize_chunk_result(value: Any) -> Dict[str, Any]:
    """Coerce a chunk backtest result into the dict form.

    Accepts the bare returns list that older callers (and test doubles)
    hand back, so a monkeypatched _run_chunk_backtest keeps working.

    Args:
        value: Dict from _run_chunk_backtest, or a plain returns list.

    Returns:
        Dict with "returns", "regimes" and "diagnosis" keys.
    """
    if isinstance(value, dict):
        return {
            "returns": list(value.get("returns") or []),
            "regimes": dict(value.get("regimes") or {}),
            "regime_trades": dict(value.get("regime_trades") or {}),
            "diagnosis": value.get("diagnosis"),
        }
    return {
        "returns": list(value or []),
        "regimes": {},
        "regime_trades": {},
        "diagnosis": None,
    }


def build_window_spec(
    n_windows: int, window_months: int, mode: str, span_years: int
) -> str:
    """Compact description of a window series, stored with the verdict.

    Args:
        n_windows: Number of chunk windows.
        window_months: Months per chunk window.
        mode: "spread" or "recent".
        span_years: Span the spread series reaches back over.

    Returns:
        e.g. "6x2mo@8y" (spread) or "3x2mo" (recent).
    """
    spec = f"{n_windows}x{window_months}mo"
    if mode == "spread":
        spec += f"@{span_years}y"
    return spec


def validate_strategy(
    strategy: str,
    symbols: List[str],
    window_months: int,
    n_windows: int,
    capital: float = DEFAULT_CAPITAL,
    data_dir: Optional[str] = None,
    mode: Optional[str] = None,
    span_years: Optional[int] = None,
    per_symbol: Optional[bool] = None,
    anchor_end: Optional[str] = None,
) -> Dict[str, Any]:
    """Chunk-validate one strategy across symbols and gate the result.

    Args:
        strategy: Strategy identifier (snake_case or display form).
        symbols: Trading pairs to evaluate.
        window_months: Months per chunk window.
        n_windows: Number of chunk windows.
        capital: Initial capital per chunk backtest.
        data_dir: Candle data dir override (default: config).
        mode: Window mode, "spread" or "recent" (default: env).
        span_years: Years the spread series reaches back (default: env).
        per_symbol: Cut each symbol's series over its own coverage
            instead of the intersected one (default: env, else True).
        anchor_end: Pin the newest window to this ISO date instead of
            the store's trailing edge. Only ever moves the anchor
            EARLIER. Without it a re-run months later evaluates
            different windows and is not reproducible.

    Returns:
        Dict with strategy, symbols, window_spec, windows, chunks,
        coverage, regimes, verdict (GateVerdict or None), overall
        (PASS/FAIL/UNKNOWN), data_start, data_end, and optional reason.

    Raises:
        Exception: Propagates backtest failures; the caller (run_once)
            catches them so one broken strategy never kills the run.
    """
    from ..config import config as cfg
    from ..regime_param_overlay import resolve_strategy_key

    strategy_key = resolve_strategy_key(strategy) or strategy
    resolved_dir = data_dir or cfg.backtest_data_dir

    # Intersect coverage across symbols for the shared series, and cut a
    # per-symbol series so a late-listing symbol does not truncate the
    # history of every other one (shared with the optimizer sweep).
    resolved = resolve_chunk_windows(
        symbols,
        window_months,
        n_windows,
        data_dir=resolved_dir,
        label=strategy_key,
        mode=mode,
        span_years=span_years,
        per_symbol=per_symbol,
        anchor_end=anchor_end,
    )
    window_spec = build_window_spec(
        n_windows,
        window_months,
        resolved.get("mode", DEFAULT_WINDOW_MODE),
        int(resolved.get("span_years") or DEFAULT_SPAN_YEARS),
    )

    result: Dict[str, Any] = {
        "strategy": strategy_key,
        "symbols": list(symbols),
        "window_spec": window_spec,
        "windows": [],
        "chunks": [],
        "coverage": resolved.get("coverage", {}),
        "regimes": {},
        "regime_trades": {},
        "verdict": None,
        "overall": "UNKNOWN",
        "data_start": resolved["data_start"],
        "data_end": resolved["data_end"],
    }
    if resolved.get("reason"):
        result["reason"] = resolved["reason"]
        return result

    windows = resolved["windows"]
    by_symbol = resolved.get("windows_by_symbol") or {}
    result["windows"] = windows

    symbol_returns: Dict[str, List[float]] = {}
    pooled_regimes: Dict[str, int] = {}
    pooled_regime_trades: Dict[str, int] = {}
    for symbol in resolved["symbols"]:
        pooled: List[float] = []
        for start, end in by_symbol.get(symbol) or windows:
            logger.info(
                f"[{strategy_key}] chunk backtest {symbol} {start} -> {end}"
            )
            chunk = _normalize_chunk_result(
                _run_chunk_backtest(
                    strategy_key,
                    symbol,
                    start,
                    end,
                    capital,
                    data_dir=resolved_dir,
                )
            )
            returns = chunk["returns"]
            pooled.extend(returns)
            for regime, bars in chunk["regimes"].items():
                pooled_regimes[regime] = (
                    pooled_regimes.get(regime, 0) + int(bars)
                )
            for regime, n in chunk["regime_trades"].items():
                pooled_regime_trades[regime] = (
                    pooled_regime_trades.get(regime, 0) + int(n)
                )
            result["chunks"].append(
                {
                    "symbol": symbol,
                    "start": start,
                    "end": end,
                    "n_trades": len(returns),
                    "sum_return": round(sum(returns), 6),
                    "regimes": chunk["regimes"],
                    "regime_trades": chunk["regime_trades"],
                    "diagnosis": chunk["diagnosis"],
                }
            )
        symbol_returns[symbol] = pooled
    result["regimes"] = pooled_regimes
    result["regime_trades"] = pooled_regime_trades

    n_trials: Optional[int] = None
    sr_variance: Optional[float] = None
    try:
        from ..database import DatabaseManager

        db = DatabaseManager()
        total = db.get_total_trials(strategy_key)
        if total > 0:
            n_trials = total
            sr_variance = db.get_trial_sr_variance(strategy_key)
    except Exception as e:
        logger.warning(
            f"[{strategy_key}] trial registry lookup failed: {e}"
        )

    verdict = evaluate_strategy_gate(
        strategy=strategy_key,
        symbol_returns=symbol_returns,
        n_trials=n_trials,
        sr_variance=sr_variance,
    )
    result["verdict"] = verdict
    result["overall"] = "PASS" if verdict.passed else "FAIL"
    return result


def _checks_payload(verdict: Optional[GateVerdict]) -> List[Dict[str, Any]]:
    """Serialize gate checks for the checks_json column."""
    if verdict is None:
        return []
    return [
        {
            "name": c.name,
            "passed": c.passed,
            "value": c.value,
            "threshold": c.threshold,
            "detail": c.detail,
        }
        for c in verdict.checks
    ]


def persist_result(db: Any, result: Dict[str, Any]) -> Optional[int]:
    """Store one strategy validation result in validation_runs.

    Args:
        db: DatabaseManager instance (runner-owned connection).
        result: Output of validate_strategy (or an UNKNOWN stub).

    Returns:
        Inserted row id (None on Postgres).
    """
    checks = _checks_payload(result.get("verdict"))
    if not checks and result.get("reason"):
        checks = [
            {
                "name": "runner_error",
                "passed": False,
                "value": str(result["reason"]),
                "threshold": "",
                "detail": "",
            }
        ]
    return db.save_validation_run(
        strategy=result["strategy"],
        symbols=",".join(result["symbols"]),
        window_spec=result["window_spec"],
        chunks_json=json.dumps(result.get("chunks", [])),
        checks_json=json.dumps(checks),
        overall=result["overall"],
        data_start=result.get("data_start"),
        data_end=result.get("data_end"),
    )


def _regime_shares(regimes: Dict[str, int]) -> List[Tuple[str, float]]:
    """Regime -> share of bars, ordered most-common first.

    Args:
        regimes: Regime value -> bars observed.

    Returns:
        List of (regime, fraction) tuples; empty when no bars.
    """
    total = sum(int(v) for v in regimes.values())
    if total <= 0:
        return []
    return sorted(
        ((str(k), int(v) / total) for k, v in regimes.items()),
        key=lambda kv: -kv[1],
    )


def format_regime_shares(regimes: Dict[str, int], top: int = 3) -> str:
    """Render the top regimes as "name 42%, name 31%" (or "-")."""
    shares = _regime_shares(regimes)
    if not shares:
        return "-"
    return ", ".join(f"{k} {v * 100:.0f}%" for k, v in shares[:top])


def format_regime_trades(regime_trades: Dict[str, int]) -> str:
    """Render closed trades per entry regime as "name 106 (97%), ...".

    Bar shares say where the TAPE was; this says where the STRATEGY
    actually traded. The two diverge sharply - a strategy gated to a
    4.6%-of-bars regime can still take most of its trades elsewhere,
    because resting orders fill after the regime has moved on.

    Args:
        regime_trades: Regime value -> closed trades entered there.

    Returns:
        Formatted string, or "-" when nothing traded.
    """
    total = sum(int(v) for v in regime_trades.values())
    if total <= 0:
        return "-"
    ordered = sorted(
        regime_trades.items(), key=lambda kv: (-int(kv[1]), str(kv[0]))
    )
    return ", ".join(
        f"{name} {int(n)} ({int(n) / total * 100:.0f}%)" for name, n in ordered
    )


def collect_regime_coverage(
    results: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Merge the per-window regime histograms recorded by every run.

    Regime classification depends on the data and the window, not on the
    strategy, so identical (symbol, window) chunks across strategies are
    de-duplicated by keeping the richest observation.

    Args:
        results: Per-strategy result dicts from validate_strategy.

    Returns:
        (rows, pooled) where rows is one dict per (symbol, window) with
        symbol/start/end/bars/regimes, ordered by symbol then start, and
        pooled is the regime histogram summed over those rows.
    """
    best: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for result in results:
        for chunk in result.get("chunks", []):
            regimes = chunk.get("regimes") or {}
            bars = sum(int(v) for v in regimes.values())
            if bars <= 0:
                continue
            key = (chunk["symbol"], chunk["start"], chunk["end"])
            current = best.get(key)
            if current is None or bars > current["bars"]:
                best[key] = {
                    "symbol": chunk["symbol"],
                    "start": chunk["start"],
                    "end": chunk["end"],
                    "bars": bars,
                    "regimes": dict(regimes),
                }
    rows = sorted(best.values(), key=lambda r: (r["symbol"], r["start"]))
    pooled: Dict[str, int] = {}
    for row in rows:
        for regime, bars in row["regimes"].items():
            pooled[regime] = pooled.get(regime, 0) + int(bars)
    return rows, pooled


def print_regime_coverage(results: List[Dict[str, Any]]) -> None:
    """Print the per-window regime breakdown for a validation run.

    A multi-year validation is only worth more than a six-month one if
    the windows actually span different market conditions. This block is
    the evidence: if a strategy only ever traded one regime across the
    whole span, that IS the finding.

    Args:
        results: Per-strategy result dicts from validate_strategy.
    """
    rows, pooled = collect_regime_coverage(results)
    if not rows:
        print("  regime coverage: unavailable (no funnel diagnostics)")
        return
    print(f"\n{'=' * 96}")
    print("REGIME COVERAGE BY WINDOW")
    print(f"{'=' * 96}")
    print(
        f"  {'Symbol':<11}{'Window':<26}{'Bars':>9}  Regimes (share of bars)"
    )
    print(f"  {'-' * 92}")
    for row in rows:
        window = f"{row['start']} -> {row['end']}"
        print(
            f"  {row['symbol']:<11}{window:<26}{row['bars']:>9}  "
            f"{format_regime_shares(row['regimes'])}"
        )
    print(f"  {'-' * 92}")
    total_bars = sum(r["bars"] for r in rows)
    shares = _regime_shares(pooled)
    print(f"  POOLED ({total_bars} bars over {len(rows)} windows)")
    for regime, share in shares:
        bars = pooled[regime]
        print(f"    {regime:<22}{bars:>9}  {share * 100:5.1f}%")
    print(f"  {'-' * 92}")
    if shares and shares[0][1] >= REGIME_CONCENTRATION_WARN:
        print(
            f"  WARNING: {shares[0][0]} accounts for "
            f"{shares[0][1] * 100:.0f}% of all bars - this span is a "
            f"single regime epoch, so a PASS here is not evidence of "
            f"robustness across conditions."
        )
    elif len(shares) < 3:
        print(
            f"  WARNING: only {len(shares)} regime(s) observed across the "
            f"whole span - widen VALIDATION_SPAN_YEARS or add symbols."
        )
    else:
        print(
            f"  {len(shares)} regimes observed; most common "
            f"{shares[0][0]} at {shares[0][1] * 100:.0f}% of bars."
        )
    print(f"{'=' * 96}\n")


def print_data_spans(results: List[Dict[str, Any]]) -> None:
    """Print the per-symbol data span actually evaluated.

    Symbols list at different times, so a multi-year run compares an
    8-year BTC record against a 3-year SUI one. Printing the spans makes
    that asymmetry impossible to mistake for a fair comparison.

    Args:
        results: Per-strategy result dicts from validate_strategy.
    """
    coverage: Dict[str, Dict[str, Any]] = {}
    for result in results:
        for symbol, entry in (result.get("coverage") or {}).items():
            coverage.setdefault(symbol, entry)
    if not coverage:
        return
    print(f"\n{'=' * 96}")
    print("PER-SYMBOL DATA SPANS")
    print(f"{'=' * 96}")
    print(
        f"  {'Symbol':<11}{'Data span (1m-clamped)':<26}"
        f"{'Months':>7}  {'Windows evaluated':<38}Note"
    )
    print(f"  {'-' * 92}")
    for symbol in sorted(coverage):
        entry = coverage[symbol]
        span = f"{entry.get('data_start')} .. {entry.get('data_end')}"
        months = entry.get("months", 0)
        if entry.get("window_start"):
            windows = (
                f"{entry['window_start']} -> {entry['window_end']} "
                f"({entry.get('n_windows', 0)} windows)"
            )
        else:
            windows = "shared series"
        note = "SHORTER HISTORY" if entry.get("listing_limited") else ""
        print(
            f"  {symbol:<11}{span:<26}{months:>7}  "
            f"{windows:<38}{note}".rstrip()
        )
    print(f"  {'-' * 92}")
    months = [e.get("months", 0) for e in coverage.values()]
    if months and max(months) - min(months) >= 12:
        print(
            "  Per-symbol history is ASYMMETRIC: the pooled verdict "
            "weighs a long record against a short one. Read the "
            "cross-symbol check per symbol, not as an average."
        )
    print(f"{'=' * 96}\n")


def print_run_summary(results: List[Dict[str, Any]]) -> None:
    """Print the per-run summary table (strategy x verdict x numbers)."""
    print(f"\n{'=' * 96}")
    print("VALIDATION RUN SUMMARY")
    print(f"{'=' * 96}")
    header = (
        f"  {'Strategy':<24} {'Verdict':<9} {'Trades':<8} "
        f"{'PF':<8} {'PSR/DSR':<30} Consistent"
    )
    print(header)
    print(f"  {'-' * 92}")
    for r in results:
        verdict: Optional[GateVerdict] = r.get("verdict")
        trades = sum(c["n_trades"] for c in r.get("chunks", []))
        pf = "-"
        psr = "-"
        consistent = "-"
        if verdict is not None:
            by_name = {c.name: c for c in verdict.checks}
            if "profit_factor" in by_name:
                pf = by_name["profit_factor"].value
            if "psr_or_dsr" in by_name:
                psr = by_name["psr_or_dsr"].value
            if "cross_symbol_consistency" in by_name:
                consistent = by_name["cross_symbol_consistency"].value
        print(
            f"  {r['strategy']:<24} {r['overall']:<9} {trades:<8} "
            f"{pf:<8} {psr:<30} {consistent}"
        )
        if r.get("regimes"):
            print(
                f"  {'':<24} regimes: "
                f"{format_regime_shares(r['regimes'], top=5)}"
            )
        if r.get("regime_trades"):
            print(
                f"  {'':<24} trades by entry regime: "
                f"{format_regime_trades(r['regime_trades'])}"
            )
        if r.get("reason"):
            print(f"  {'':<24} reason: {r['reason']}")
    print(f"  {'-' * 92}")
    first = results[0] if results else {}
    print(
        f"  windows: {first.get('window_spec', '-')} | "
        f"symbols: {','.join(first.get('symbols', [])) or '-'} | "
        f"shared span: {first.get('data_start') or '-'} .. "
        f"{first.get('data_end') or '-'}"
    )
    print(f"{'=' * 96}\n")
    print_data_spans(results)
    print_regime_coverage(results)


def run_once(
    strategies: List[str],
    symbols: List[str],
    window_months: int,
    n_windows: int,
    capital: float = DEFAULT_CAPITAL,
    data_dir: Optional[str] = None,
    refresh_data: bool = False,
    mode: Optional[str] = None,
    span_years: Optional[int] = None,
    per_symbol: Optional[bool] = None,
    anchor_end: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Run one full validation cycle and persist every verdict.

    A strategy whose backtest raises is logged and skipped (stored as
    UNKNOWN); the run always continues to the next strategy.

    Args:
        strategies: Snake_case strategy keys to validate.
        symbols: Trading pairs.
        window_months: Months per chunk window.
        n_windows: Number of chunk windows.
        capital: Initial capital per chunk backtest.
        data_dir: Candle data dir override.
        refresh_data: Top up the 1m candle store from public APIs
            before validating (default False: fully offline).
        mode: Window mode, "spread" or "recent" (default: env).
        span_years: Years the spread series reaches back (default: env).
        per_symbol: Cut each symbol's series over its own coverage
            (default: env, else True).
        anchor_end: Pin the newest window to this ISO date so the run is
            reproducible against a store whose trailing edge advances.

    Returns:
        List of per-strategy result dicts (with "row_id" added).
    """
    from ..backtesting.engine import NON_BACKTESTABLE_STRATEGIES
    from ..database import DatabaseManager
    from ..regime_param_overlay import resolve_strategy_key

    if refresh_data:
        refresh_market_data(symbols, data_dir=data_dir)

    db = DatabaseManager()
    backtestable = [
        s
        for s in strategies
        if (resolve_strategy_key(s) or s) not in NON_BACKTESTABLE_STRATEGIES
    ]
    estimate = estimate_runtime_seconds(
        len(backtestable), len(symbols), n_windows, window_months
    )
    logger.info(
        f"Estimated runtime: {format_duration(estimate)} "
        f"({len(backtestable)} strategies x {len(symbols)} symbols x "
        f"{n_windows} windows x {window_months}mo = "
        f"{len(backtestable) * len(symbols) * n_windows * window_months} "
        f"window-months of 5m replay)"
    )

    results: List[Dict[str, Any]] = []
    for strategy in strategies:
        strategy_key = resolve_strategy_key(strategy) or strategy
        if strategy_key in NON_BACKTESTABLE_STRATEGIES:
            logger.warning(
                f"[{strategy_key}] not backtestable (live-only data "
                f"surfaces), skipping"
            )
            continue
        try:
            result = validate_strategy(
                strategy,
                symbols,
                window_months,
                n_windows,
                capital=capital,
                data_dir=data_dir,
                mode=mode,
                span_years=span_years,
                per_symbol=per_symbol,
                anchor_end=anchor_end,
            )
        except Exception as e:
            logger.warning(
                f"SKIPPING strategy '{strategy}': backtest raised: {e}"
            )
            result = {
                "strategy": strategy,
                "symbols": list(symbols),
                "window_spec": f"{n_windows}x{window_months}mo",
                "chunks": [],
                "verdict": None,
                "overall": "UNKNOWN",
                "data_start": None,
                "data_end": None,
                "reason": f"backtest raised: {e}",
            }
        try:
            result["row_id"] = persist_result(db, result)
        except Exception as e:
            logger.error(
                f"Failed to persist verdict for '{result['strategy']}': {e}"
            )
            result["row_id"] = None
        results.append(result)
    print_run_summary(results)
    return results


def print_plan(
    strategies: List[str],
    symbols: List[str],
    window_months: int,
    n_windows: int,
    data_dir: Optional[str] = None,
    mode: Optional[str] = None,
    span_years: Optional[int] = None,
    per_symbol: Optional[bool] = None,
    anchor_end: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve and print the window plan without running any backtest.

    Backs --dry-run: the campaign a real run would execute, plus its
    runtime estimate, so a multi-year span can be sized before anyone
    commits hours to it.

    Args:
        strategies: Snake_case strategy keys.
        symbols: Trading pairs.
        window_months: Months per chunk window.
        n_windows: Number of chunk windows.
        data_dir: Candle data dir override.
        mode: Window mode, "spread" or "recent" (default: env).
        span_years: Years the spread series reaches back (default: env).
        per_symbol: Per-symbol window series (default: env, else True).
        anchor_end: Pin the newest window to this ISO date.

    Returns:
        The resolve_chunk_windows payload (also printed).
    """
    from ..backtesting.engine import NON_BACKTESTABLE_STRATEGIES
    from ..regime_param_overlay import resolve_strategy_key

    backtestable = [
        s
        for s in strategies
        if (resolve_strategy_key(s) or s) not in NON_BACKTESTABLE_STRATEGIES
    ]
    resolved = resolve_chunk_windows(
        symbols,
        window_months,
        n_windows,
        data_dir=data_dir,
        label="plan",
        mode=mode,
        span_years=span_years,
        per_symbol=per_symbol,
        anchor_end=anchor_end,
    )
    spec = build_window_spec(
        n_windows,
        window_months,
        resolved.get("mode", DEFAULT_WINDOW_MODE),
        int(resolved.get("span_years") or DEFAULT_SPAN_YEARS),
    )
    print(f"\n{'=' * 96}")
    print(f"VALIDATION PLAN (dry run) - {spec}")
    print(f"{'=' * 96}")
    print(f"  strategies : {', '.join(backtestable) or '-'}")
    print(f"  symbols    : {', '.join(resolved['symbols']) or '-'}")
    print(
        f"  shared span: {resolved.get('data_start')} .. "
        f"{resolved.get('data_end')}  (intersection of all symbols; "
        f"per-symbol series below reach further back)"
    )
    if resolved.get("reason"):
        print(f"  reason     : {resolved['reason']}")
        print(f"{'=' * 96}\n")
        return resolved
    by_symbol = resolved.get("windows_by_symbol") or {}
    for symbol in resolved["symbols"]:
        windows = by_symbol.get(symbol) or resolved["windows"]
        print(f"  {symbol}:")
        for start, end in windows:
            print(f"    {start} -> {end}")
    estimate = estimate_runtime_seconds(
        len(backtestable), len(resolved["symbols"]), n_windows, window_months
    )
    total_months = (
        len(backtestable)
        * len(resolved["symbols"])
        * n_windows
        * window_months
    )
    print(f"  {'-' * 92}")
    print(
        f"  estimated runtime: {format_duration(estimate)} "
        f"({total_months} window-months of 5m replay at "
        f"{_env_float('VALIDATION_SECONDS_PER_MONTH', DEFAULT_SECONDS_PER_MONTH):.0f}"
        f"s/month)"
    )
    print(f"{'=' * 96}\n")
    print_data_spans([{"coverage": resolved.get("coverage", {})}])
    return resolved


def _handle_sigint(signum: int, frame: Any) -> None:
    """SIGINT/SIGTERM handler: request a clean loop exit."""
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("Shutdown requested - finishing up")


def _sleep_interruptible(seconds: float) -> None:
    """Sleep in 1s slices so a shutdown request exits promptly."""
    deadline = time.monotonic() + seconds
    while not _shutdown_requested and time.monotonic() < deadline:
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for the standalone validation runner."""
    # Cap loguru at LOG_LEVEL for this service. loguru ships a default
    # stderr sink at DEBUG and no module in this repo ever configures
    # one, so LOG_LEVEL only ever governed stdlib logging - a full
    # campaign at DEBUG once wrote ~275 MB per validation cell and
    # filled the volume (2026-07-29). Scoped to the CLI entry so
    # importing this module never touches global logging state.
    try:
        from loguru import logger as _loguru_logger

        _loguru_logger.remove()
        _loguru_logger.add(
            sys.stderr, level=os.getenv("LOG_LEVEL", "INFO").upper() or "INFO"
        )
    except Exception:
        pass

    parser = argparse.ArgumentParser(
        description=(
            "Standalone strategy validation service (chunked windows, "
            "independent of the live bot)"
        ),
    )
    parser.add_argument(
        "--strategies",
        default="all",
        help=(
            "'all' (= every ENABLE_*-enabled strategy) or a "
            "comma-separated list (default: all)"
        ),
    )
    parser.add_argument(
        "--symbols",
        default=os.getenv("VALIDATION_SYMBOLS", DEFAULT_SYMBOLS),
        help="Comma-separated symbols (env: VALIDATION_SYMBOLS)",
    )
    parser.add_argument(
        "--window-months",
        type=int,
        default=_env_int("VALIDATION_WINDOW_MONTHS", DEFAULT_WINDOW_MONTHS),
        help="Months per chunk window (env: VALIDATION_WINDOW_MONTHS)",
    )
    parser.add_argument(
        "--windows",
        type=int,
        default=_env_int("VALIDATION_WINDOWS", DEFAULT_WINDOWS),
        help="Number of chunk windows (env: VALIDATION_WINDOWS)",
    )
    parser.add_argument(
        "--window-mode",
        default=_env_mode("VALIDATION_WINDOW_MODE", DEFAULT_WINDOW_MODE),
        choices=list(WINDOW_MODES),
        help=(
            "'spread' distributes the windows evenly across --span-years "
            "(many out-of-sample seams across market epochs); 'recent' is "
            "the legacy contiguous block walking back from the data end "
            "(env: VALIDATION_WINDOW_MODE)"
        ),
    )
    parser.add_argument(
        "--span-years",
        type=int,
        default=_env_int("VALIDATION_SPAN_YEARS", DEFAULT_SPAN_YEARS),
        help=(
            "Calendar years the spread series reaches back over; clamped "
            "to available 1m coverage per symbol "
            "(env: VALIDATION_SPAN_YEARS)"
        ),
    )
    parser.add_argument(
        "--shared-windows",
        action="store_true",
        default=not _env_bool("VALIDATION_PER_SYMBOL_SPAN", True),
        help=(
            "Evaluate one intersected window series for every symbol "
            "instead of per-symbol series. Comparable window-for-window, "
            "but a late-listing symbol (SUI-USDC, 2023-05) truncates the "
            "history of every other one "
            "(env: VALIDATION_PER_SYMBOL_SPAN=false)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Resolve and print the window plan, per-symbol spans and "
            "runtime estimate, then exit without backtesting"
        ),
    )
    parser.add_argument(
        "--objective",
        default="sharpe",
        choices=["sharpe"],
        help="Gate objective family (reserved; only 'sharpe' today)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=DEFAULT_CAPITAL,
        help="Initial capital per chunk backtest (default: 10000)",
    )
    parser.add_argument(
        "--refresh-data",
        action="store_true",
        default=_env_bool("VALIDATION_REFRESH_DATA", False),
        help=(
            "Top up the 1m candle store from public APIs before "
            "validating (env: VALIDATION_REFRESH_DATA; default: off, "
            "fully offline)"
        ),
    )
    parser.add_argument(
        "--loop-hours",
        type=float,
        default=None,
        help="Re-run every H hours until interrupted (default: off)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle and exit (default behavior)",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help=(
            "Candle/funding store to read. MUST be absolute when running "
            "from a git worktree: BACKTEST_DATA_DIR in .env is relative "
            "and config.py loads it with override=True, so exporting the "
            "variable does nothing"
        ),
    )
    parser.add_argument(
        "--anchor-end",
        default=os.getenv("VALIDATION_ANCHOR_END") or None,
        help=(
            "Pin the newest window to this ISO date instead of the "
            "store's trailing edge, so the same command evaluates the "
            "same windows next month (env: VALIDATION_ANCHOR_END). Only "
            "ever moves the anchor earlier"
        ),
    )
    args = parser.parse_args(argv)

    if args.strategies.strip().lower() == "all":
        strategies = discover_enabled_strategies()
    else:
        strategies = [
            s.strip() for s in args.strategies.split(",") if s.strip()
        ]
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not strategies:
        logger.error("No strategies to validate (all ENABLE_* flags off?)")
        return 1
    if not symbols:
        logger.error("No symbols supplied")
        return 1

    per_symbol = not args.shared_windows

    if args.dry_run:
        print_plan(
            strategies,
            symbols,
            args.window_months,
            args.windows,
            data_dir=args.data_dir,
            mode=args.window_mode,
            span_years=args.span_years,
            per_symbol=per_symbol,
            anchor_end=args.anchor_end,
        )
        return 0

    signal.signal(signal.SIGINT, _handle_sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sigint)

    window_spec = build_window_spec(
        args.windows, args.window_months, args.window_mode, args.span_years
    )
    loop_hours = None if args.once else args.loop_hours
    cycle = 0
    while True:
        cycle += 1
        logger.info(
            f"Validation cycle {cycle}: strategies={','.join(strategies)} "
            f"symbols={','.join(symbols)} windows={window_spec} "
            f"per_symbol_span={per_symbol}"
        )
        run_once(
            strategies,
            symbols,
            args.window_months,
            args.windows,
            capital=args.capital,
            data_dir=args.data_dir,
            refresh_data=args.refresh_data,
            mode=args.window_mode,
            span_years=args.span_years,
            per_symbol=per_symbol,
            anchor_end=args.anchor_end,
        )
        if loop_hours is None:
            break
        if _shutdown_requested:
            logger.info("Loop stopped cleanly")
            break
        logger.info(f"Cycle {cycle} done - sleeping {loop_hours}h")
        _sleep_interruptible(loop_hours * 3600.0)
        if _shutdown_requested:
            logger.info("Loop stopped cleanly")
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
