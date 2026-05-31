"""
MomentumScalping ATR Multiplier Sweep
======================================

Tests combinations of ATR stop/target multipliers across ETH, BTC, SUI.

Key insight from ATR analysis:
  SUI 5m ATR = 0.53%  (slippage = 0.38x ATR per side = 19% of 2x-stop)
  ETH 5m ATR = 0.24%  (slippage = 0.82x ATR per side = 41% of 2x-stop)
  BTC 5m ATR = 0.20%  (slippage = 1.01x ATR per side = 51% of 2x-stop)

SUI has 2.2x more relative ATR → costs are proportionally smaller → strategy is viable there.

Usage:
    python run_momentum_atr_sweep.py                     # SUI only (recommended)
    python run_momentum_atr_sweep.py --symbol ETH-USDC   # ETH
    python run_momentum_atr_sweep.py --symbol BTC-USDC   # BTC
    python run_momentum_atr_sweep.py --all               # all three symbols
"""

import sys
import io
import os
import argparse

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

START   = "2024-01-01"
END     = "2024-12-31"
CAPITAL = 10000.0

# Parameter grid
STOP_MULTIPLIERS   = [1.5, 2.0, 2.5, 3.0]
TARGET_MULTIPLIERS = [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]

# ---------------------------------------------------------------------------
# Silence loguru before any bot import
# ---------------------------------------------------------------------------
from loguru import logger
logger.remove()

# ---------------------------------------------------------------------------
# Import bot code (dotenv loads .env into os.environ here, once only)
# ---------------------------------------------------------------------------
from trading_bot_v2.backtesting.engine import BacktestEngine


def _color(code, text):
    return f"\033[{code}m{text}\033[0m"

def green(t):   return _color("32", t)
def red(t):     return _color("31", t)
def yellow(t):  return _color("33", t)
def dim(t):     return _color("2",  t)
def bold(t):    return _color("1",  t)


def run_one(symbol: str, stop_mult: float, target_mult: float) -> dict:
    """Set env vars after dotenv loaded, build fresh engine, run backtest."""
    os.environ["MOMENTUM_ATR_STOP_MULTIPLIER"]   = str(stop_mult)
    os.environ["MOMENTUM_ATR_TARGET_MULTIPLIER"] = str(target_mult)

    engine = BacktestEngine()
    br = engine.run(
        start=START, end=END,
        symbol=symbol, initial_capital=CAPITAL,
        strategy_filter="MomentumScalping",
    )
    return {
        "ok":               True,
        "total_return_pct": br.total_return_pct,
        "sharpe":           br.sharpe_ratio,
        "max_dd_pct":       br.max_drawdown_pct,
        "win_rate_pct":     br.win_rate_pct,
        "profit_factor":    br.profit_factor,
        "closed_trades":    br.closed_trades,
        "total_fees":       br.total_fees,
    }


def run_sweep(symbol: str):
    from itertools import product
    import time

    combos = list(product(STOP_MULTIPLIERS, TARGET_MULTIPLIERS))
    total  = len(combos)

    print()
    print(bold(f"  MomentumScalping ATR Sweep  --  {symbol}  {START} -> {END}"))
    print(f"  Stop multipliers  : {STOP_MULTIPLIERS}")
    print(f"  Target multipliers: {TARGET_MULTIPLIERS}")
    print(f"  Total runs        : {total}")
    print()

    results = []

    for i, (stop_m, target_m) in enumerate(combos, 1):
        rrr   = target_m / stop_m
        label = f"stop={stop_m:.1f}x  target={target_m:.1f}x  (RRR={rrr:.2f})"
        print(f"  [{i:02d}/{total:02d}]  {label:<42}", end="", flush=True)

        t0 = time.time()
        try:
            data = run_one(symbol, stop_m, target_m)
        except Exception as exc:
            print(f"  {red('FAILED')}: {exc}")
            results.append((stop_m, target_m, None))
            continue
        elapsed = time.time() - t0

        trades = data["closed_trades"]
        if trades == 0:
            print(f"  {dim('0 trades')}  ({elapsed:.0f}s)")
            results.append((stop_m, target_m, data))
            continue

        pf  = data["profit_factor"]
        ret = data["total_return_pct"]
        wr  = data["win_rate_pct"]
        dd  = data["max_dd_pct"]

        pf_real = pf if pf != float("inf") else 999999
        pf_str  = "inf" if pf == float("inf") else f"{pf:.2f}"

        if pf_real >= 1.5 and ret >= 0:
            tag = green("GOOD    ")
        elif pf_real >= 1.0:
            tag = green("OK      ")
        elif pf_real >= 0.7:
            tag = yellow("MARGINAL")
        else:
            tag = red("LOSING  ")

        print(f"  {tag}  PF={pf_str:<6}  WR={wr:.1f}%  ret={ret:+.2f}%  dd={dd:.1f}%  t={trades}  ({elapsed:.0f}s)")
        results.append((stop_m, target_m, data))

    # ---- Summary table -------------------------------------------------------
    print()
    print("  " + "=" * 84)
    print(bold(f"  SUMMARY: {symbol}  (green = PF >= 1.0)"))
    print("  " + "=" * 84)
    hdr = f"  {'stop':>5}  {'target':>7}  {'RRR':>5}  {'PF':>7}  {'WinRate':>8}  {'Return':>9}  {'MaxDD':>7}  {'Trades':>7}  {'Fees':>8}"
    print(hdr)
    print("  " + "-" * 84)

    viable = []
    for stop_m, target_m, data in results:
        if data is None:
            print(f"  {stop_m:5.1f}  {target_m:7.1f}  {target_m/stop_m:5.2f}  FAILED")
            continue
        trades = data.get("closed_trades", 0)
        if trades == 0:
            print(f"  {stop_m:5.1f}  {target_m:7.1f}  {target_m/stop_m:5.2f}  0 trades")
            continue

        pf   = data["profit_factor"]
        ret  = data["total_return_pct"]
        wr   = data["win_rate_pct"]
        dd   = data["max_dd_pct"]
        fees = data["total_fees"]
        rrr  = target_m / stop_m

        pf_real = pf if pf != float("inf") else 999999
        pf_str  = "    inf" if pf == float("inf") else f"{pf:7.2f}"

        line = (
            f"  {stop_m:5.1f}  {target_m:7.1f}  {rrr:5.2f}  {pf_str}"
            f"  {wr:8.1f}%  {ret:+9.2f}%  {dd:6.1f}%  {trades:7d}  ${fees:7.0f}"
        )
        if pf_real >= 1.0:
            print(green(line))
            viable.append((stop_m, target_m, pf_real, ret, wr, dd, trades, fees))
        elif pf_real >= 0.7:
            print(yellow(line))
        else:
            print(line)

    print("  " + "-" * 84)
    print()

    if viable:
        best_pf  = max(viable, key=lambda x: x[2])
        best_ret = max(viable, key=lambda x: x[3])
        print(green(f"  Best by PF    : stop={best_pf[0]:.1f}x  target={best_pf[1]:.1f}x  PF={best_pf[2]:.2f}  WR={best_pf[4]:.1f}%  return={best_pf[3]:+.2f}%"))
        if best_ret[0] != best_pf[0] or best_ret[1] != best_pf[1]:
            print(green(f"  Best by return: stop={best_ret[0]:.1f}x  target={best_ret[1]:.1f}x  PF={best_ret[2]:.2f}  WR={best_ret[4]:.1f}%  return={best_ret[3]:+.2f}%"))
    else:
        with_trades = [(s, t, d) for s, t, d in results if d and d.get("closed_trades", 0) > 0]
        if with_trades:
            closest = max(with_trades, key=lambda x: x[2]["profit_factor"])
            s, t, d = closest
            print(yellow(f"  Closest to break-even: stop={s:.1f}x  target={t:.1f}x  PF={d['profit_factor']:.2f}  WR={d['win_rate_pct']:.1f}%  return={d['total_return_pct']:+.2f}%"))
        print(red("  No viable configurations (PF >= 1.0) found for " + symbol))
    print()

    return viable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", "-s", default="SUI-USDC")
    parser.add_argument("--all", "-a", action="store_true", help="Run all three symbols")
    args = parser.parse_args()

    symbols = ["ETH-USDC", "BTC-USDC", "SUI-USDC"] if args.all else [args.symbol]

    all_viable = {}
    for sym in symbols:
        viable = run_sweep(sym)
        all_viable[sym] = viable

    if len(symbols) > 1:
        print(bold("  === CROSS-SYMBOL SUMMARY ==="))
        for sym, viable in all_viable.items():
            if viable:
                best = max(viable, key=lambda x: x[2])
                print(green(f"  {sym:<12}  best: stop={best[0]:.1f}x  target={best[1]:.1f}x  PF={best[2]:.2f}  ret={best[3]:+.2f}%"))
            else:
                print(red(f"  {sym:<12}  NO viable config found"))
        print()


if __name__ == "__main__":
    main()
