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
    For each strategy the runner walks backward from the most recent
    candle available in the offline data, cutting N windows of M months
    each (default 3 x 2-month). Every (symbol, window) pair is
    backtested in isolation; per-chunk results are stored, and the
    standing gate checks (min trades, profit factor, PSR/DSR with the
    trial-registry N, cross-symbol consistency) are computed over the
    POOLED per-symbol chunk returns.

CLI:
    python -m trading_bot_v2.validation.runner \\
        [--strategies all|mean_reversion,momentum_scalping] \\
        [--symbols SUI-USDC,BTC-USDC] [--window-months 2] [--windows 3] \\
        [--objective sharpe] [--capital 10000] \\
        [--loop-hours H | --once]

Env defaults (flags override): VALIDATION_WINDOW_MONTHS,
VALIDATION_WINDOWS, VALIDATION_SYMBOLS.
"""

import argparse
import json
import os
import signal
import sys
import time
from datetime import date, datetime
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
}

DEFAULT_SYMBOLS = "SUI-USDC,BTC-USDC"
DEFAULT_WINDOW_MONTHS = 2
DEFAULT_WINDOWS = 3
DEFAULT_CAPITAL = 10000.0

_shutdown_requested = False


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean env var (true/1/yes/on, false/0/no/off)."""
    value = os.getenv(name, "").lower()
    if value in ("true", "1", "yes", "on"):
        return True
    if value in ("false", "0", "no", "off"):
        return False
    return default


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


def _run_chunk_backtest(
    strategy_key: str,
    symbol: str,
    start: str,
    end: str,
    capital: float,
) -> List[float]:
    """Backtest one (strategy, symbol, window) chunk offline.

    A fresh BacktestEngine is built per chunk so no state leaks between
    windows.

    Args:
        strategy_key: Snake_case strategy key.
        symbol: Trading pair.
        start: Window start (ISO date).
        end: Window end (ISO date).
        capital: Initial capital for the chunk.

    Returns:
        Per-trade fractional returns of the chunk's closed trades.
    """
    from ..backtesting.engine import BacktestEngine

    engine = BacktestEngine()
    result = engine.run(
        start=start,
        end=end,
        symbol=symbol,
        initial_capital=capital,
        strategy_filter=strategy_key,
    )
    return closed_trade_returns(result.trade_log, capital)


def validate_strategy(
    strategy: str,
    symbols: List[str],
    window_months: int,
    n_windows: int,
    capital: float = DEFAULT_CAPITAL,
    data_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Chunk-validate one strategy across symbols and gate the result.

    Args:
        strategy: Strategy identifier (snake_case or display form).
        symbols: Trading pairs to evaluate.
        window_months: Months per chunk window.
        n_windows: Number of chunk windows.
        capital: Initial capital per chunk backtest.
        data_dir: Candle data dir override (default: config).

    Returns:
        Dict with strategy, symbols, window_spec, windows, chunks,
        verdict (GateVerdict or None), overall (PASS/FAIL/UNKNOWN),
        data_start, data_end, and optional reason.

    Raises:
        Exception: Propagates backtest failures; the caller (run_once)
            catches them so one broken strategy never kills the run.
    """
    from ..config import config as cfg
    from ..regime_param_overlay import resolve_strategy_key

    strategy_key = resolve_strategy_key(strategy) or strategy
    resolved_dir = data_dir or cfg.backtest_data_dir
    window_spec = f"{n_windows}x{window_months}mo"

    result: Dict[str, Any] = {
        "strategy": strategy_key,
        "symbols": list(symbols),
        "window_spec": window_spec,
        "windows": [],
        "chunks": [],
        "verdict": None,
        "overall": "UNKNOWN",
        "data_start": None,
        "data_end": None,
    }

    # Intersect coverage across symbols so every symbol sees the same
    # window series.
    coverage = {}
    for symbol in symbols:
        cov = _data_coverage(symbol, resolved_dir)
        if cov is None:
            logger.warning(
                f"[{strategy_key}] skipping symbol {symbol}: no data"
            )
            continue
        coverage[symbol] = cov
    if not coverage:
        result["reason"] = "no candle data for any symbol"
        return result

    data_start = max(c[0] for c in coverage.values())
    data_end = min(c[1] for c in coverage.values())
    result["data_start"] = data_start.isoformat()
    result["data_end"] = data_end.isoformat()

    windows = compute_chunk_windows(
        data_end, window_months, n_windows, data_start=data_start
    )
    if not windows:
        result["reason"] = "no usable windows in data coverage"
        return result
    result["windows"] = windows

    symbol_returns: Dict[str, List[float]] = {}
    for symbol in coverage:
        pooled: List[float] = []
        for start, end in windows:
            logger.info(
                f"[{strategy_key}] chunk backtest {symbol} {start} -> {end}"
            )
            returns = _run_chunk_backtest(
                strategy_key, symbol, start, end, capital
            )
            pooled.extend(returns)
            result["chunks"].append(
                {
                    "symbol": symbol,
                    "start": start,
                    "end": end,
                    "n_trades": len(returns),
                    "sum_return": round(sum(returns), 6),
                }
            )
        symbol_returns[symbol] = pooled

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
        if r.get("reason"):
            print(f"  {'':<24} reason: {r['reason']}")
    print(f"  {'-' * 92}")
    print(
        f"  windows: {results[0]['window_spec'] if results else '-'} | "
        f"symbols: {','.join(results[0]['symbols']) if results else '-'}"
    )
    print(f"{'=' * 96}\n")


def run_once(
    strategies: List[str],
    symbols: List[str],
    window_months: int,
    n_windows: int,
    capital: float = DEFAULT_CAPITAL,
    data_dir: Optional[str] = None,
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

    Returns:
        List of per-strategy result dicts (with "row_id" added).
    """
    from ..database import DatabaseManager

    db = DatabaseManager()
    results: List[Dict[str, Any]] = []
    for strategy in strategies:
        try:
            result = validate_strategy(
                strategy,
                symbols,
                window_months,
                n_windows,
                capital=capital,
                data_dir=data_dir,
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
        default=int(
            os.getenv("VALIDATION_WINDOW_MONTHS", str(DEFAULT_WINDOW_MONTHS))
        ),
        help="Months per chunk window (env: VALIDATION_WINDOW_MONTHS)",
    )
    parser.add_argument(
        "--windows",
        type=int,
        default=int(os.getenv("VALIDATION_WINDOWS", str(DEFAULT_WINDOWS))),
        help="Number of chunk windows (env: VALIDATION_WINDOWS)",
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

    signal.signal(signal.SIGINT, _handle_sigint)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sigint)

    loop_hours = None if args.once else args.loop_hours
    cycle = 0
    while True:
        cycle += 1
        logger.info(
            f"Validation cycle {cycle}: strategies={','.join(strategies)} "
            f"symbols={','.join(symbols)} "
            f"windows={args.windows}x{args.window_months}mo"
        )
        run_once(
            strategies,
            symbols,
            args.window_months,
            args.windows,
            capital=args.capital,
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
