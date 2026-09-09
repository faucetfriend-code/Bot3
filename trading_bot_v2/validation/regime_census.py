"""Regime x strategy trade census.

Answers the question that has to be settled BEFORE any per-regime
parameter tuning: for each (strategy, regime) pair, how many closed
trades does the strategy actually produce across the multi-year
validation window series, and is that enough to support an honest
verdict?

Per-regime variants multiply the search space while fragmenting the
sample. The promotion gate derives its pooled minimum from
``statistics.min_observations_for_sharpe`` (33 at the current settings),
so a regime cell below that cannot produce a PASS or a FAIL - only
INSUFFICIENT_DATA. Splitting a strategy five ways can therefore turn one
gradeable result into five ungradeable ones while charging five times the
search cost to the same deflated Sharpe (``validation.runner`` reads the
strategy's TOTAL trial count, regime-tagged rows included).

Trades are attributed to the regime CONFIRMED AT ENTRY: SimulatedExchange
tags every opening fill with the regime the detector had confirmed on
that bar, and PerformanceTracker rolls it up into ``result.by_regime``.
For strategies with resting orders (grid) the entry regime is the regime
when the order FILLED, which can differ from the regime that created the
grid.

Usage:
    python -m trading_bot_v2.validation.regime_census
    python -m trading_bot_v2.validation.regime_census \\
        --strategies momentum_scalping,vwap_scalping \\
        --symbols BTC-USDC,ETH-USDC,SUI-USDC --json census.json
"""

import argparse
import json
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from ..market_regime import MarketRegime
from .gate import load_gate_policy
from .runner import (
    DEFAULT_CAPITAL,
    DEFAULT_SPAN_YEARS,
    DEFAULT_WINDOW_MODE,
    format_duration,
    resolve_chunk_windows,
)

#: Strategies worth censusing: every optimizable strategy the backtest
#: engine can actually run. FundingArb and OrderBookImbalance are excluded
#: because SimulatedExchange cannot serve them (see engine
#: NON_BACKTESTABLE_STRATEGIES) - they would report zeros everywhere.
DEFAULT_STRATEGIES: Tuple[str, ...] = (
    "momentum_scalping",
    "ma_crossover",
    "mean_reversion",
    "grid_trading",
    "liquidation_capture",
    "vwap_scalping",
)

#: Column order for the census table (regime prevalence, most common
#: first over the 8-year campaign).
REGIME_ORDER: Tuple[str, ...] = (
    "trending_strong",
    "ranging_calm",
    "trending_moderate",
    "indecisive",
    "ranging_volatile",
)


def regime_columns() -> List[str]:
    """Return every regime value, in the census table's column order."""
    known = {r.value for r in MarketRegime}
    columns = [r for r in REGIME_ORDER if r in known]
    columns.extend(sorted(known - set(columns)))
    return columns


def derived_requirements() -> Dict[str, int]:
    """Sample requirements the promotion gate will apply to a cell.

    Returns:
        Dict with "pooled" (derived pooled closed-trade minimum) and
        "per_symbol" (floor deciding which symbols may vote in the
        cross-symbol check).
    """
    policy = load_gate_policy()
    return {
        "pooled": int(policy["min_closed_trades"]),
        "per_symbol": int(policy["min_trades_per_symbol"]),
        "min_symbols": int(policy["min_consistent_symbols"]),
    }


def classify_cell(cell: Dict[str, Any], requirements: Dict[str, int]) -> str:
    """Grade one (strategy, regime) cell against the derived requirement.

    Args:
        cell: Cell dict with "trades" and "by_symbol".
        requirements: Output of derived_requirements().

    Returns:
        "tunable" when the cell could support a graded verdict,
        "thin" when it traded but cannot, "none" when it never traded.
    """
    trades = int(cell.get("trades", 0))
    if trades <= 0:
        return "none"
    eligible = sum(
        1
        for n in (cell.get("by_symbol") or {}).values()
        if int(n) >= requirements["per_symbol"]
    )
    if trades >= requirements["pooled"] and eligible >= requirements["min_symbols"]:
        return "tunable"
    return "thin"


def _empty_cell() -> Dict[str, Any]:
    return {
        "trades": 0,
        "wins": 0,
        "pnl": 0.0,
        "gross_win": 0.0,
        "gross_loss": 0.0,
        "by_symbol": {},
    }


def _accumulate(
    cells: Dict[str, Dict[str, Any]],
    symbol: str,
    trade_log: List[Dict[str, Any]],
) -> None:
    """Fold one chunk's closed trades into the per-regime cells."""
    for trade in trade_log:
        pnl = float(trade.get("pnl", 0) or 0)
        if pnl == 0:
            continue
        regime = str(trade.get("regime") or "untagged").strip().lower()
        cell = cells.setdefault(regime, _empty_cell())
        cell["trades"] += 1
        cell["pnl"] += pnl
        if pnl > 0:
            cell["wins"] += 1
            cell["gross_win"] += pnl
        else:
            cell["gross_loss"] += abs(pnl)
        cell["by_symbol"][symbol] = cell["by_symbol"].get(symbol, 0) + 1


def profit_factor(cell: Dict[str, Any]) -> Optional[float]:
    """Profit factor of a cell, or None when it has no losses to divide by."""
    gross_loss = float(cell.get("gross_loss", 0.0))
    if gross_loss <= 0:
        return None
    return float(cell.get("gross_win", 0.0)) / gross_loss


def run_census(
    strategies: List[str],
    symbols: List[str],
    window_months: int = 2,
    n_windows: int = 6,
    span_years: int = DEFAULT_SPAN_YEARS,
    mode: str = DEFAULT_WINDOW_MODE,
    capital: float = DEFAULT_CAPITAL,
    data_dir: Optional[str] = None,
    per_symbol: bool = True,
) -> Dict[str, Any]:
    """Census closed trades per (strategy, regime) over the window series.

    Uses the SAME window resolution the validation runner and the chunked
    optimizer use, so the counts describe the periods a verdict would be
    computed over.

    Args:
        strategies: Snake_case strategy keys to census.
        symbols: Trading pairs.
        window_months: Months per chunk window.
        n_windows: Number of chunk windows.
        span_years: Years the spread series reaches back over.
        mode: "spread" or "recent".
        capital: Initial capital per chunk backtest.
        data_dir: Candle store override (pass an ABSOLUTE path from a git
            worktree - BACKTEST_DATA_DIR is relative and .env wins over
            the shell).
        per_symbol: Cut each symbol's series over its own coverage.

    Returns:
        Dict with "requirements", "windows_by_symbol", "cells"
        (strategy -> regime -> cell), "regime_bars" (strategy -> regime ->
        bars) and "elapsed_seconds" (strategy -> seconds).

    Raises:
        ValueError: When no usable window series exists.
    """
    resolved = resolve_chunk_windows(
        symbols,
        window_months,
        n_windows,
        data_dir=data_dir,
        label="regime-census",
        mode=mode,
        span_years=span_years,
        per_symbol=per_symbol,
    )
    if resolved.get("reason"):
        raise ValueError(f"Cannot census: {resolved['reason']}")

    windows = resolved["windows"]
    by_symbol_windows = resolved.get("windows_by_symbol") or {}

    census: Dict[str, Any] = {
        "requirements": derived_requirements(),
        "symbols": list(resolved["symbols"]),
        "window_spec": f"{n_windows}x{window_months}mo"
        + (f"@{span_years}y" if mode == "spread" else ""),
        "windows_by_symbol": {
            s: [list(w) for w in (by_symbol_windows.get(s) or windows)]
            for s in resolved["symbols"]
        },
        "cells": {},
        "regime_bars": {},
        "elapsed_seconds": {},
    }

    for strategy in strategies:
        cells: Dict[str, Dict[str, Any]] = {}
        bars: Dict[str, int] = {}
        started = time.time()
        for symbol in resolved["symbols"]:
            for start, end in by_symbol_windows.get(symbol) or windows:
                logger.info(f"[census/{strategy}] {symbol} {start} -> {end}")
                try:
                    chunk = _census_chunk(
                        strategy, symbol, start, end, capital, data_dir
                    )
                except Exception as e:  # noqa: BLE001 - one bad chunk
                    logger.error(
                        f"[census/{strategy}] {symbol} {start}..{end} failed: {e}"
                    )
                    continue
                _accumulate(cells, symbol, chunk["trade_log"])
                for regime, n in chunk["regimes"].items():
                    bars[str(regime)] = bars.get(str(regime), 0) + int(n)
        census["cells"][strategy] = cells
        census["regime_bars"][strategy] = bars
        census["elapsed_seconds"][strategy] = round(time.time() - started, 1)

    return census


def _census_chunk(
    strategy: str,
    symbol: str,
    start: str,
    end: str,
    capital: float,
    data_dir: Optional[str],
) -> Dict[str, Any]:
    """Backtest one chunk and return its closed trades plus regime bars.

    The validation runner's chunk helper returns pooled returns rather
    than the trade log, so this runs the engine directly. The regime bar
    histogram comes from the funnel diagnostics either way.

    Args:
        strategy: Snake_case strategy key.
        symbol: Trading pair.
        start: Window start (ISO date).
        end: Window end (ISO date).
        capital: Initial capital.
        data_dir: Candle store override.

    Returns:
        Dict with "trade_log" (all fills; closers carry pnl != 0) and
        "regimes" (regime value -> bars observed).
    """
    from ..backtesting.engine import BacktestEngine
    from .runner import _DataDirConfig

    override = None
    if data_dir:
        from ..config import config as base_config

        if str(data_dir) != str(getattr(base_config, "backtest_data_dir", "")):
            override = _DataDirConfig(base_config, str(data_dir))

    result = BacktestEngine(override_config=override).run(
        start=start,
        end=end,
        symbol=symbol,
        initial_capital=capital,
        strategy_filter=strategy,
    )
    diagnostics = getattr(result, "diagnostics", None) or {}
    return {
        "trade_log": list(getattr(result, "trade_log", None) or []),
        "regimes": dict(diagnostics.get("regimes") or {}),
    }


def format_census_table(census: Dict[str, Any]) -> str:
    """Render the strategy x regime table with tunability marks.

    Args:
        census: Output of run_census.

    Returns:
        The printable report.
    """
    req = census["requirements"]
    columns = regime_columns()
    lines: List[str] = []
    width = 22 + len(columns) * 13 + 9
    rule = "=" * width

    lines.append(rule)
    lines.append("REGIME x STRATEGY TRADE CENSUS")
    lines.append(rule)
    lines.append(
        f"  windows: {census['window_spec']} | symbols: {','.join(census['symbols'])}"
    )
    lines.append(
        f"  a cell is TUNABLE at >= {req['pooled']} pooled closed trades "
        f"(derived from min_observations_for_sharpe) AND >= "
        f"{req['min_symbols']} symbols with >= {req['per_symbol']} trades"
    )
    lines.append("-" * width)
    header = f"  {'strategy':<20}" + "".join(f"{c[:12]:>13}" for c in columns)
    lines.append(header + f"{'total':>9}")
    lines.append("-" * width)

    for strategy, cells in census["cells"].items():
        row = f"  {strategy:<20}"
        total = 0
        for column in columns:
            cell = cells.get(column)
            if not cell or not cell.get("trades"):
                row += f"{'-':>13}"
                continue
            verdict = classify_cell(cell, req)
            mark = "*" if verdict == "tunable" else "!"
            row += f"{str(cell['trades']) + mark:>13}"
            total += int(cell["trades"])
        lines.append(row + f"{total:>9}")

    lines.append("-" * width)
    lines.append("  * = tunable (sample supports a graded verdict)")
    lines.append(
        "  ! = traded but BELOW the derived requirement -> a "
        "per-regime study here can only ever return "
        "INSUFFICIENT_DATA"
    )
    lines.append(rule)

    lines.append("")
    lines.append("PER-CELL DETAIL (tunable cells first)")
    lines.append("-" * width)
    lines.append(
        f"  {'strategy':<20}{'regime':<20}{'n':>7}{'WR':>8}{'PF':>8}"
        f"{'pnl':>11}  symbols"
    )
    detail: List[Tuple[int, str, str, Dict[str, Any]]] = []
    for strategy, cells in census["cells"].items():
        for regime, cell in cells.items():
            detail.append((int(cell["trades"]), strategy, regime, cell))
    for trades, strategy, regime, cell in sorted(detail, reverse=True):
        pf = profit_factor(cell)
        pf_s = f"{pf:.2f}" if pf is not None else "inf"
        wr = cell["wins"] / trades * 100 if trades else 0.0
        mark = {"tunable": "*", "thin": "!", "none": " "}[classify_cell(cell, req)]
        symbols = ",".join(f"{s}:{n}" for s, n in sorted(cell["by_symbol"].items()))
        lines.append(
            f"  {strategy:<20}{regime:<20}{str(trades) + mark:>7}"
            f"{wr:>7.1f}%{pf_s:>8}{cell['pnl']:>11.1f}  {symbols}"
        )
    lines.append("-" * width)

    lines.append("")
    lines.append("CONCENTRATION (share of a strategy's trades per regime)")
    lines.append("-" * width)
    for strategy, cells in census["cells"].items():
        total = sum(int(c["trades"]) for c in cells.values())
        if not total:
            lines.append(f"  {strategy:<20} no closed trades")
            continue
        top = sorted(cells.items(), key=lambda kv: -int(kv[1]["trades"]))[0]
        lines.append(
            f"  {strategy:<20} {total:>5} trades | dominant regime "
            f"{top[0]} at {int(top[1]['trades']) / total * 100:.0f}% | "
            f"{sum(1 for c in cells.values() if classify_cell(c, req) == 'tunable')}"
            f" tunable cell(s)"
        )
    lines.append(rule)

    total_seconds = sum(census.get("elapsed_seconds", {}).values())
    if total_seconds:
        lines.append(f"  census runtime: {format_duration(total_seconds)}")
    return "\n".join(lines)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse the census CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Census closed trades per (strategy, regime) over the "
            "validation window series, and mark which cells have the "
            "sample to support a per-regime variant."
        )
    )
    parser.add_argument(
        "--strategies",
        default=",".join(DEFAULT_STRATEGIES),
        help="Comma-separated snake_case strategy keys "
        f"(default: {','.join(DEFAULT_STRATEGIES)})",
    )
    parser.add_argument(
        "--symbols",
        default="BTC-USDC,ETH-USDC,SUI-USDC",
        help="Comma-separated trading pairs",
    )
    parser.add_argument("--windows", type=int, default=6)
    parser.add_argument("--window-months", type=int, default=2)
    parser.add_argument("--span-years", type=int, default=DEFAULT_SPAN_YEARS)
    parser.add_argument("--window-mode", choices=["spread", "recent"], default="spread")
    parser.add_argument("--capital", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument(
        "--data-dir",
        help="Candle store. Pass an ABSOLUTE path from a git worktree: "
        "BACKTEST_DATA_DIR is relative and .env overrides the shell.",
    )
    parser.add_argument(
        "--shared-windows",
        action="store_true",
        help="One intersected window series instead of per-symbol series",
    )
    parser.add_argument("--json", help="Also write the raw census to this path")
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress per-chunk logging"
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """Run the census CLI.

    Args:
        argv: Optional argument list.

    Returns:
        0 on success, 1 on a configuration error.
    """
    args = parse_args(argv)
    if args.quiet:
        logger.remove()
        logger.add(sys.stderr, level="ERROR")

    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not strategies or not symbols:
        print("Need at least one strategy and one symbol")
        return 1

    try:
        census = run_census(
            strategies=strategies,
            symbols=symbols,
            window_months=args.window_months,
            n_windows=args.windows,
            span_years=args.span_years,
            mode=args.window_mode,
            capital=args.capital,
            data_dir=args.data_dir,
            per_symbol=not args.shared_windows,
        )
    except ValueError as e:
        print(f"Census failed: {e}")
        return 1

    print(format_census_table(census))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(census, handle, indent=1)
        print(f"\n  raw census written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
