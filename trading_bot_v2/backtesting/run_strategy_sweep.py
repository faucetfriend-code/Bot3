"""
Strategy Sweep Runner
=====================

Runs every strategy in isolation, one at a time, against a single symbol.
Each strategy runs in its own subprocess so log output stays clean.
Produces a ranked summary table comparing all strategies.

Usage
-----
# SUI (default)
python -m trading_bot_v2.backtesting.run_strategy_sweep

# Choose your token
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol BTC-USDC
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol ETH-USDC

# Custom date range / capital
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol BTC-USDC \\
    --start 2024-06-01 --end 2024-12-31 --capital 5000

# Run only a specific subset of strategies
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol SUI-USDC \\
    --strategies VWAPScalping MomentumScalping LiquidationCapture

# Save per-strategy HTML reports into reports/<symbol>/
python -m trading_bot_v2.backtesting.run_strategy_sweep --symbol BTC-USDC --save-reports
"""

import argparse
import sys
import os
import time
import json
import subprocess
from pathlib import Path
from typing import List, Optional

# Force UTF-8 on Windows terminals
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------
ALL_STRATEGIES: List[str] = [
    "MeanReversion",
    "MACrossover",
    "GridTrading",
    "LiquidationCapture",
    "VWAPScalping",
    "MomentumScalping",
    "FundingArb",
    "OrderBookImbalance",
    "SessionRangeBreakout",
]

# ---------------------------------------------------------------------------
# ANSI colour helpers
# ---------------------------------------------------------------------------
_USE_COLOUR = sys.stdout.isatty() or os.getenv("FORCE_COLOR")

def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _USE_COLOUR else text

def green(t):  return _c("32", t)
def red(t):    return _c("31", t)
def yellow(t): return _c("33", t)
def dim(t):    return _c("2",  t)
def bold(t):   return _c("1",  t)

# ---------------------------------------------------------------------------
# Worker script that runs inside the subprocess
# Prints a single JSON line to stdout with the results.
# ---------------------------------------------------------------------------
_WORKER_SCRIPT = r"""
import sys, json, os

# Silence loguru completely before importing anything
from loguru import logger
logger.remove()

symbol   = sys.argv[1]
start    = sys.argv[2]
end      = sys.argv[3]
capital  = float(sys.argv[4])
strategy = sys.argv[5]
report   = sys.argv[6] if len(sys.argv) > 6 else ""

try:
    from trading_bot_v2.backtesting.engine import BacktestEngine
    engine = BacktestEngine()
    br = engine.run(
        start=start, end=end,
        symbol=symbol, initial_capital=capital,
        strategy_filter=strategy,
    )
    if report:
        br.save_html(report)

    out = {
        "ok": True,
        "total_return_pct": br.total_return_pct,
        "sharpe":           br.sharpe_ratio,
        "sortino":          br.sortino_ratio,
        "max_dd_pct":       br.max_drawdown_pct,
        "win_rate_pct":     br.win_rate_pct,
        "profit_factor":    br.profit_factor if br.profit_factor != float("inf") else 999999,
        "closed_trades":    br.closed_trades,
        "total_fills":      br.total_trades,
        "total_fees":       br.total_fees,
        "final_equity":     br.final_equity,
        "calmar":           br.calmar_ratio,
    }
except Exception as exc:
    out = {"ok": False, "error": str(exc)}

print(json.dumps(out))
"""

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
class StrategyResult:
    def __init__(self, name: str):
        self.name = name
        self.ok = False
        self.error: Optional[str] = None
        self.elapsed_sec: float = 0.0
        self.total_return_pct  = 0.0
        self.sharpe            = 0.0
        self.sortino           = 0.0
        self.max_dd_pct        = 0.0
        self.win_rate_pct      = 0.0
        self.profit_factor     = 0.0
        self.closed_trades     = 0
        self.total_fills       = 0
        self.total_fees        = 0.0
        self.final_equity      = 0.0
        self.calmar            = 0.0


def run_single_strategy(
    name: str,
    symbol: str,
    start: str,
    end: str,
    capital: float,
    report_path: str = "",
) -> StrategyResult:
    r = StrategyResult(name)
    t0 = time.time()

    cmd = [sys.executable, "-c", _WORKER_SCRIPT,
           symbol, start, end, str(capital), name, report_path]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent.parent),  # repo root
            timeout=300,
        )
        # Find the JSON line in stdout (worker prints exactly one)
        json_line = None
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                json_line = line
        if json_line is None:
            r.error = "No JSON output" + (f": {proc.stderr[-200:]}" if proc.stderr else "")
            return r

        data = json.loads(json_line)
        if not data.get("ok"):
            r.error = data.get("error", "unknown error")
            return r

        r.ok               = True
        r.total_return_pct = data["total_return_pct"]
        r.sharpe           = data["sharpe"]
        r.sortino          = data["sortino"]
        r.max_dd_pct       = data["max_dd_pct"]
        r.win_rate_pct     = data["win_rate_pct"]
        r.profit_factor    = data["profit_factor"]
        r.closed_trades    = data["closed_trades"]
        r.total_fills      = data["total_fills"]
        r.total_fees       = data["total_fees"]
        r.final_equity     = data["final_equity"]
        r.calmar           = data["calmar"]

    except subprocess.TimeoutExpired:
        r.error = "timed out (>300 s)"
    except Exception as exc:
        r.error = str(exc)

    r.elapsed_sec = time.time() - t0
    return r


# ---------------------------------------------------------------------------
# Table formatting
# ---------------------------------------------------------------------------
_COLS = {
    "Strategy":   20,
    "Return":      9,
    "Sharpe":      7,
    "MaxDD":       7,
    "WinRate":     8,
    "ProfFactor": 11,
    "Calmar":      7,
    "Closed":      7,
    "Fees":        8,
}

def _header() -> str:
    return "  ".join(bold(k.ljust(v)) for k, v in _COLS.items())

def _sep() -> str:
    total = sum(_COLS.values()) + 2 * (len(_COLS) - 1)
    return dim("-" * total)

def _row(r: StrategyResult) -> str:
    name = r.name.ljust(_COLS["Strategy"])

    if not r.ok:
        msg = red(f"ERROR: {r.error}") if r.error else dim("0 trades")
        return f"  {name}  {msg}"

    if r.closed_trades == 0:
        return f"  {name}  " + dim("0 trades fired")

    def ret(v):
        s = f"{v:+.2f}%".ljust(_COLS["Return"])
        return green(s) if v >= 0 else red(s)

    def sharpe(v):
        s = f"{v:.2f}".ljust(_COLS["Sharpe"])
        return green(s) if v >= 0.5 else yellow(s) if v >= 0 else red(s)

    def dd(v):
        s = f"{v:.1f}%".ljust(_COLS["MaxDD"])
        return green(s) if v <= 5 else yellow(s) if v <= 15 else red(s)

    def wr(v):
        s = f"{v:.1f}%".ljust(_COLS["WinRate"])
        return green(s) if v >= 50 else yellow(s) if v >= 35 else red(s)

    def pf(v):
        s = ("inf" if v >= 999999 else f"{v:.2f}").ljust(_COLS["ProfFactor"])
        return green(s) if v >= 1.0 else red(s)

    calmar = ("--" if r.calmar == 0 else f"{r.calmar:.2f}").ljust(_COLS["Calmar"])
    fees   = f"${r.total_fees:.2f}".ljust(_COLS["Fees"])
    closed = str(r.closed_trades).ljust(_COLS["Closed"])

    return "  ".join([
        name,
        ret(r.total_return_pct),
        sharpe(r.sharpe),
        dd(r.max_dd_pct),
        wr(r.win_rate_pct),
        pf(r.profit_factor),
        calmar,
        closed,
        fees,
    ])

def _verdict(r: StrategyResult) -> str:
    if not r.ok:
        return ""
    if r.closed_trades == 0:
        return dim("  -> 0 trades (regime not matched or insufficient data)")
    pf = r.profit_factor
    wr = r.win_rate_pct
    ret = r.total_return_pct
    if pf >= 1.5 and ret >= 0:
        tag = green("GOOD")
    elif pf >= 1.0:
        tag = green("OK")
    elif pf >= 0.7:
        tag = yellow("MARGINAL")
    else:
        tag = red("LOSING")
    pf_str = "inf" if pf >= 999999 else f"{pf:.2f}"
    return f"  -> {tag}  PF {pf_str}  WR {wr:.1f}%  return {ret:+.2f}%  ({r.closed_trades} closed)"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    from trading_bot_v2.config import config as cfg

    parser = argparse.ArgumentParser(
        description="Run every strategy in isolation against one symbol.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--symbol", "-s",
        default=getattr(cfg, "backtest_symbol", "SUI-USDC"),
        help="Symbol to backtest  (default: %(default)s)",
    )
    parser.add_argument(
        "--start",
        default=getattr(cfg, "backtest_start_date", "2024-01-01"),
        help="Start date YYYY-MM-DD  (default: %(default)s)",
    )
    parser.add_argument(
        "--end",
        default=getattr(cfg, "backtest_end_date", "2024-12-31"),
        help="End date YYYY-MM-DD  (default: %(default)s)",
    )
    parser.add_argument(
        "--capital", "-c",
        type=float,
        default=getattr(cfg, "backtest_initial_capital", 10000.0),
        help="Initial capital in USDC  (default: %(default)s)",
    )
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=ALL_STRATEGIES,
        metavar="STRATEGY",
        choices=ALL_STRATEGIES,
        help=f"Subset to run. Default: all. Choices: {', '.join(ALL_STRATEGIES)}",
    )
    parser.add_argument(
        "--save-reports",
        action="store_true",
        help="Write an HTML report per strategy into reports/<symbol>/",
    )
    args = parser.parse_args()

    symbol     = args.symbol
    start      = args.start
    end        = args.end
    capital    = args.capital
    strategies = args.strategies

    report_dir: Optional[Path] = None
    if args.save_reports:
        report_dir = Path("reports") / symbol.replace("-", "_")
        report_dir.mkdir(parents=True, exist_ok=True)

    # ---- banner ------------------------------------------------------------
    print()
    print(bold(f"  Strategy Sweep  --  {symbol}"))
    print(f"  Period   : {start} -> {end}")
    print(f"  Capital  : ${capital:,.0f}")
    print(f"  Runs     : {len(strategies)} strategies (sequential, isolated subprocesses)")
    if report_dir:
        print(f"  Reports  : {report_dir}/")
    print()

    # ---- run each strategy -------------------------------------------------
    results: List[StrategyResult] = []

    for i, name in enumerate(strategies, 1):
        label = name.ljust(24)
        print(f"  [{i:02d}/{len(strategies):02d}]  {label}", end="", flush=True)

        rp = str(report_dir / f"{name}.html") if report_dir else ""
        r  = run_single_strategy(name, symbol, start, end, capital, report_path=rp)
        results.append(r)

        elapsed = f"{r.elapsed_sec:.1f}s"
        if not r.ok:
            status = red(f"FAILED ({r.error or 'no output'})") if r.error else dim("no trades")
        elif r.closed_trades == 0:
            status = dim(f"0 trades  {elapsed}")
        elif r.total_return_pct >= 0:
            status = green(f"{r.total_return_pct:+.2f}%  {r.closed_trades} closed  {elapsed}")
        else:
            status = red(f"{r.total_return_pct:+.2f}%  {r.closed_trades} closed  {elapsed}")

        print(f"  {status}", flush=True)

    # ---- summary table -----------------------------------------------------
    print()
    print(bold(f"  === Results: {symbol}  {start} -> {end} ==="))
    print()
    print("  " + _header())
    print("  " + _sep())

    with_trades    = sorted(
        [r for r in results if r.ok and r.closed_trades > 0],
        key=lambda r: r.total_return_pct, reverse=True,
    )
    without_trades = [r for r in results if not r.ok or r.closed_trades == 0]

    for r in with_trades + without_trades:
        print("  " + _row(r))
    print("  " + _sep())

    # ---- verdicts ----------------------------------------------------------
    print()
    print(bold("  Verdicts"))
    print()
    for r in with_trades + without_trades:
        print(f"  {r.name.ljust(24)}{_verdict(r)}")

    # ---- aggregate summary -------------------------------------------------
    print()
    positive = [r for r in with_trades if r.total_return_pct >= 0]
    losing   = [r for r in with_trades if r.total_return_pct < 0]
    no_trade = [r for r in without_trades]

    if positive:
        avg = sum(r.total_return_pct for r in positive) / len(positive)
        print(f"  {green('Profitable')} ({len(positive)} strategies):  avg {avg:+.2f}%")
    if losing:
        avg = sum(r.total_return_pct for r in losing) / len(losing)
        print(f"  {red('Losing')}     ({len(losing)} strategies):  avg {avg:+.2f}%")
    if no_trade:
        names = ", ".join(r.name for r in no_trade)
        print(f"  {dim('No trades')}  ({len(no_trade)} strategies):  {names}")

    total_fees = sum(r.total_fees for r in results if r.ok)
    total_time = sum(r.elapsed_sec for r in results)
    print()
    print(f"  Total fees across all strategies : ${total_fees:.2f}")
    print(f"  Total sweep time                 : {total_time:.0f}s")
    print()


if __name__ == "__main__":
    main()
