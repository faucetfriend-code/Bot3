#!/usr/bin/env python3
"""
VWAP Scalping Strategy — Year-by-Year Backtest (2018-2025)

Tests the best-known configuration across individual years to find
which market regimes the strategy works in.

Two entry modes tested:
  1. bull_pullback — catch dips in uptrends
  2. mean_reversion — fade extreme VWAP deviations
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
import csv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from BTV2.strategies import run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR

STORAGE_ROOT = Path("G:/Candle Data")

def load_parquet(symbol, interval):
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    return None

# ── Best configuration from previous sweeps ──────────────────────────────────
BASE_PARAMS = {
    "sd_threshold": 2.0,
    "entry_mode": "bull_pullback",
    "adx_max": 30,
    "rsi_max": 50,
    "volume_mult": 2.0,
    "atr_stop": 0.7,
    "atr_target": 3.5,
    "pullback_bars": 3,
    "use_session_filter": True,
    "use_htf_vwap": True,
    "use_htf_ema": False,
    "htf_adx_max": 25,
    # Additional defaults from the function signature
    "use_trailing_stop": True,
    "trailing_atr": 1.2,
    "use_anchored_vwap": True,
    "require_reversal_candle": True,
    "tp_mode": "vwap",
}

# Mean reversion variant
MR_PARAMS = BASE_PARAMS.copy()
MR_PARAMS["entry_mode"] = "mean_reversion"
MR_PARAMS["sd_threshold"] = 2.5

# Years to test: 2018-01-01 through 2025-01-01
YEARS = [
    ("2018", "2018-01-01", "2019-01-01"),
    ("2019", "2019-01-01", "2020-01-01"),
    ("2020", "2020-01-01", "2021-01-01"),
    ("2021", "2021-01-01", "2022-01-01"),
    ("2022", "2022-01-01", "2023-01-01"),
    ("2023", "2023-01-01", "2024-01-01"),
    ("2024", "2024-01-01", "2025-01-01"),
]

def run_year_test(df_5m, df_1m, params, label):
    """Run a single year test and return metrics dict."""
    print(f"  {label}...", end=" ", flush=True)

    equity, trades = run_vwap_scalping(df_5m, cutoff=0.10, df_exit=df_1m, **params)

    bars_per_year = INTERVAL_BARS_PER_YEAR["5m"]
    metrics = compute_metrics(equity, trades, bars_per_year=bars_per_year)

    # Calculate cost-adjusted net return
    costs = len(trades) * 0.30  # 0.30% round-trip per trade
    net_return = metrics["total_return_pct"] - costs

    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    else:
        win_rate = 0
        profit_factor = 0

    print(f"Trades={len(trades)}, WR={win_rate:.1f}%, Net={net_return:+.2f}%, PF={profit_factor:.2f}, Sharpe={metrics['sharpe']:.2f}")

    return {
        "year": label.split()[0] if " " in label else label,
        "mode": params["entry_mode"],
        "trades": len(trades),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999.99,
        "net_return": round(net_return, 2),
        "total_return": round(metrics["total_return_pct"], 2),
        "sharpe": round(metrics["sharpe"], 2),
        "max_dd": round(metrics["max_dd_pct"], 2),
        "cagr": round(metrics["cagr_pct"], 2),
    }


def main():
    print("=" * 70)
    print("VWAP SCALPING — YEAR-BY-YEAR BACKTEST (2018-2025)")
    print("=" * 70)

    # Load data
    print("\nLoading data...")
    df_5m_full = load_parquet("BTCUSDT", "5m")
    df_1m_full = load_parquet("BTCUSDT", "1m")

    if df_5m_full is None:
        print("ERROR: Could not load 5m data from G:/Candle Data/BTCUSDT_5m.parquet")
        sys.exit(1)
    if df_1m_full is None:
        print("ERROR: Could not load 1m data from G:/Candle Data/BTCUSDT_1m.parquet")
        sys.exit(1)

    print(f"5m data: {len(df_5m_full)} bars ({df_5m_full.index[0]} to {df_5m_full.index[-1]})")
    print(f"1m data: {len(df_1m_full)} bars ({df_1m_full.index[0]} to {df_1m_full.index[-1]})")

    results = []

    # ── Test bull_pullback mode for each year ────────────────────────────────
    print("\n--- bull_pullback mode ---")
    for year, start, end in YEARS:
        df_5m = df_5m_full[(df_5m_full.index >= start) & (df_5m_full.index < end)]
        df_1m = df_1m_full[(df_1m_full.index >= start) & (df_1m_full.index < end)]

        if df_5m.empty:
            print(f"  {year}: No data available, skipping")
            results.append({
                "year": year, "mode": "bull_pullback",
                "trades": 0, "win_rate": 0, "profit_factor": 0,
                "net_return": 0, "total_return": 0, "sharpe": 0,
                "max_dd": 0, "cagr": 0,
            })
            continue

        params = BASE_PARAMS.copy()
        label = f"{year} ({len(df_5m)} bars)"
        r = run_year_test(df_5m, df_1m, params, label)
        r["year"] = year
        results.append(r)

    # ── Test mean_reversion mode for each year ───────────────────────────────
    print("\n--- mean_reversion mode ---")
    for year, start, end in YEARS:
        df_5m = df_5m_full[(df_5m_full.index >= start) & (df_5m_full.index < end)]
        df_1m = df_1m_full[(df_1m_full.index >= start) & (df_1m_full.index < end)]

        if df_5m.empty:
            print(f"  {year}: No data available, skipping")
            results.append({
                "year": year, "mode": "mean_reversion",
                "trades": 0, "win_rate": 0, "profit_factor": 0,
                "net_return": 0, "total_return": 0, "sharpe": 0,
                "max_dd": 0, "cagr": 0,
            })
            continue

        params = MR_PARAMS.copy()
        label = f"{year} ({len(df_5m)} bars)"
        r = run_year_test(df_5m, df_1m, params, label)
        r["year"] = year
        results.append(r)

    # ── Print summary table ──────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SUMMARY TABLE")
    print("=" * 70)
    print(f"{'Year':<6} {'Mode':<18} {'Trades':>6} {'WR%':>6} {'PF':>6} {'Net%':>8} {'Sharpe':>7}")
    print("-" * 70)

    for r in results:
        pf_str = f"{r['profit_factor']:.2f}" if r['profit_factor'] < 999 else "inf"
        flag = " **" if r["net_return"] > 0 and r["trades"] >= 10 else ""
        print(f"{r['year']:<6} {r['mode']:<18} {r['trades']:>6} {r['win_rate']:>5.1f}% {pf_str:>6} {r['net_return']:>+7.2f}% {r['sharpe']:>6.2f}{flag}")

    # ── Save CSV ─────────────────────────────────────────────────────────────
    results_dir = Path(__file__).parent.parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    csv_path = results_dir / "vwap_yearly_test_results.csv"
    fieldnames = ["year", "mode", "trades", "win_rate", "profit_factor",
                  "net_return", "total_return", "sharpe", "max_dd", "cagr"]

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"\nCSV saved: {csv_path}")

    # ── Save Markdown Report ─────────────────────────────────────────────────
    md_path = results_dir / "vwap_yearly_test_report.md"

    profitable = [r for r in results if r["net_return"] > 0 and r["trades"] >= 10]
    unprofitable = [r for r in results if r["net_return"] <= 0 or r["trades"] < 10]

    with open(md_path, "w") as f:
        f.write("# VWAP Scalping Strategy — Year-by-Year Backtest (2018-2025)\n\n")
        f.write("## Configuration\n\n")
        f.write("### bull_pullback mode\n")
        f.write("```python\n")
        for k, v in BASE_PARAMS.items():
            f.write(f"    {k} = {v}\n")
        f.write("```\n\n")
        f.write("### mean_reversion mode\n")
        f.write("```python\n")
        for k, v in MR_PARAMS.items():
            f.write(f"    {k} = {v}\n")
        f.write("```\n\n")

        f.write("## Results\n\n")
        f.write("| Year | Mode | Trades | WR% | PF | Net% | Sharpe | MaxDD% | CAGR% |\n")
        f.write("|------|------|--------|-----|-----|------|--------|--------|-------|\n")

        for r in results:
            pf_str = f"{r['profit_factor']:.2f}" if r['profit_factor'] < 999 else "inf"
            flag = " **" if r["net_return"] > 0 and r["trades"] >= 10 else ""
            f.write(
                f"| {r['year']} | {r['mode']} | {r['trades']} "
                f"| {r['win_rate']:.1f}% | {pf_str} | {r['net_return']:+.2f}% "
                f"| {r['sharpe']:.2f} | {r['max_dd']:.2f}% | {r['cagr']:+.2f}% |{flag}\n"
            )

        f.write("\n** = Profitable with >=10 trades\n\n")

        f.write("## Analysis\n\n")

        if profitable:
            f.write(f"### Profitable Configurations ({len(profitable)})\n\n")
            for r in sorted(profitable, key=lambda x: -x["net_return"]):
                f.write(f"- **{r['year']} {r['mode']}**: Net={r['net_return']:+.2f}%, "
                        f"Trades={r['trades']}, WR={r['win_rate']:.1f}%, "
                        f"PF={r['profit_factor']:.2f}, Sharpe={r['sharpe']:.2f}\n")
        else:
            f.write("### No Profitable Configurations Found\n\n")
            f.write("No year/mode combination produced a net positive return with >=10 trades.\n\n")

        if unprofitable:
            f.write(f"\n### Unprofitable Configurations ({len(unprofitable)})\n\n")
            for r in sorted(unprofitable, key=lambda x: x["net_return"]):
                f.write(f"- **{r['year']} {r['mode']}**: Net={r['net_return']:+.2f}%, "
                        f"Trades={r['trades']}, WR={r['win_rate']:.1f}%\n")

        # Best overall
        best = max(results, key=lambda x: x["net_return"])
        f.write(f"\n## Best Overall\n\n")
        f.write(f"**{best['year']} {best['mode']}** with Net={best['net_return']:+.2f}%, "
                f"Trades={best['trades']}, WR={best['win_rate']:.1f}%, "
                f"PF={best['profit_factor']:.2f}, Sharpe={best['sharpe']:.2f}\n")

    print(f"Report saved: {md_path}")

    # ── Final verdict ────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    if profitable:
        print(f"PROFITABLE: {len(profitable)} year/mode combinations")
        for r in sorted(profitable, key=lambda x: -x["net_return"]):
            print(f"  {r['year']} {r['mode']}: Net={r['net_return']:+.2f}%, Trades={r['trades']}")
    else:
        best = max(results, key=lambda x: x["net_return"])
        print(f"NO PROFITABLE YEAR FOUND")
        print(f"  Best: {best['year']} {best['mode']} (Net={best['net_return']:+.2f}%, Trades={best['trades']})")
    print("=" * 70)


if __name__ == "__main__":
    main()
