"""
MomentumScalping Minimum ATR Threshold Sweep - ETH-USDC
=========================================================

Tests different minimum ATR% thresholds to find where the strategy becomes
profitable by only trading in high-volatility conditions where slippage
is a smaller fraction of the stop distance.

Background:
  ETH 5m avg ATR  = 0.24%
  Slippage/side   = 0.20%  (= 0.82x avg ATR)
  Cost model drag = 0.32%  (= 1.31x avg ATR)

  At ATR = 0.5% (2x average): slippage = 0.40x ATR per side → much more manageable

Usage:
    python run_momentum_min_atr_sweep.py
"""

import sys, io, os
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from loguru import logger
logger.remove()

from trading_bot_v2.backtesting.engine import BacktestEngine

SYMBOL  = "ETH-USDC"
START   = "2024-01-01"
END     = "2024-12-31"
CAPITAL = 10000.0

# Keep ATR multipliers at current .env values
STOP_MULT   = 2.0
TARGET_MULT = 3.0

# Minimum ATR% thresholds to test (as fraction, e.g. 0.003 = 0.3%)
MIN_ATR_PCTS = [0.0, 0.002, 0.003, 0.004, 0.005, 0.006, 0.007, 0.008, 0.010]


def _c(code, t): return f"\033[{code}m{t}\033[0m"
def green(t):   return _c("32", t)
def red(t):     return _c("31", t)
def yellow(t):  return _c("33", t)
def bold(t):    return _c("1",  t)
def dim(t):     return _c("2",  t)


def run_one(min_atr_pct):
    os.environ["MOMENTUM_ATR_STOP_MULTIPLIER"]   = str(STOP_MULT)
    os.environ["MOMENTUM_ATR_TARGET_MULTIPLIER"] = str(TARGET_MULT)
    os.environ["MOMENTUM_MIN_ATR_PCT"]           = str(min_atr_pct)
    engine = BacktestEngine()
    br = engine.run(
        start=START, end=END,
        symbol=SYMBOL, initial_capital=CAPITAL,
        strategy_filter="MomentumScalping",
    )
    return {
        "total_return_pct": br.total_return_pct,
        "profit_factor":    br.profit_factor,
        "win_rate_pct":     br.win_rate_pct,
        "max_dd_pct":       br.max_drawdown_pct,
        "closed_trades":    br.closed_trades,
        "total_fees":       br.total_fees,
        "sharpe":           br.sharpe_ratio,
    }


def main():
    import time
    print()
    print(bold(f"  MomentumScalping min_atr_pct sweep  --  {SYMBOL}"))
    print(f"  ATR stop={STOP_MULT}x  target={TARGET_MULT}x  (constant)")
    print(f"  ETH avg 5m ATR = 0.2435% (avg slippage = 0.82x ATR)")
    print()

    results = []
    for i, pct in enumerate(MIN_ATR_PCTS, 1):
        label = f"min_atr={pct:.3%}"
        print(f"  [{i:02d}/{len(MIN_ATR_PCTS):02d}]  {label:<18}", end="", flush=True)
        t0 = time.time()
        try:
            d = run_one(pct)
        except Exception as e:
            print(f"  {red('FAILED')}: {e}")
            results.append((pct, None))
            continue
        elapsed = time.time() - t0

        trades = d["closed_trades"]
        if trades == 0:
            print(f"  {dim('0 trades')}  ({elapsed:.0f}s)")
            results.append((pct, d))
            continue

        pf  = d["profit_factor"]
        ret = d["total_return_pct"]
        wr  = d["win_rate_pct"]
        dd  = d["max_dd_pct"]

        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        if pf >= 1.5 and ret >= 0:
            tag = green("GOOD    ")
        elif pf >= 1.0:
            tag = green("OK      ")
        elif pf >= 0.7:
            tag = yellow("MARGINAL")
        else:
            tag = red("LOSING  ")

        print(f"  {tag}  PF={pf_str:<6}  WR={wr:.1f}%  ret={ret:+.2f}%  dd={dd:.1f}%  trades={trades}  ({elapsed:.0f}s)")
        results.append((pct, d))

    # Table
    print()
    print("  " + "="*78)
    print(bold("  SUMMARY"))
    print("  " + "="*78)
    print(f"  {'min_atr_pct':>12}  {'PF':>7}  {'WinRate':>8}  {'Return':>9}  {'MaxDD':>7}  {'Trades':>7}  {'Fees':>8}  {'% vs baseline':>14}")
    print("  " + "-"*78)

    baseline_trades = None
    for pct, d in results:
        if d is None:
            print(f"  {pct:12.4%}  FAILED")
            continue
        trades = d["closed_trades"]
        if baseline_trades is None and trades > 0:
            baseline_trades = trades

        if trades == 0:
            print(f"  {pct:12.4%}  0 trades")
            continue

        pf   = d["profit_factor"]
        ret  = d["total_return_pct"]
        wr   = d["win_rate_pct"]
        dd   = d["max_dd_pct"]
        fees = d["total_fees"]
        pf_str = "    inf" if pf == float("inf") else f"{pf:7.2f}"
        trade_pct = f"{trades/baseline_trades*100:.0f}%" if baseline_trades else "?"

        line = (
            f"  {pct:12.4%}  {pf_str}  {wr:8.1f}%  {ret:+9.2f}%  {dd:6.1f}%  {trades:7d}  ${fees:7.0f}  {trade_pct:>14}"
        )
        if pf >= 1.0:
            print(green(line))
        elif pf >= 0.7:
            print(yellow(line))
        else:
            print(line)

    print("  " + "-"*78)
    print()
    print(f"  Note: higher min_atr_pct = trades only in above-average volatility")
    print(f"  Target: PF > 1.0 with reasonable trade count (>50 trades/year)")
    print()


if __name__ == "__main__":
    main()
