"""
test_all_other_strategies.py
=============================
Tests ALL non-VWAP strategies in the BTV2 system on BTCUSDT 2024 data.
Runs default parameters + parameter variations for each strategy.
Saves results to CSV and markdown report.
"""

from __future__ import annotations

import csv
import math
import os
import sys
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

# Ensure BTV2 is on the path
btv2_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(btv2_root))

from strategies import (
    STRATEGY_REGISTRY,
    STRATEGY_TIMEFRAME_CONFIG,
    INTERVAL_BARS_PER_YEAR,
    compute_metrics,
)
from data_manager import get_candles

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────
SYMBOL = "BTCUSDT"
YEAR = 2024
CUTOFF = 0.10  # default Butterworth cutoff

RESULTS_DIR = btv2_root / "results"
RESULTS_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# PARAMETER VARIATIONS (as specified in the task)
# ─────────────────────────────────────────────────────────────────────────────

# Mean Reversion variations — sample a representative subset
MR_VARIATIONS = [
    {"rsi_oversold": 25, "rsi_overbought": 75, "bb_proximity": 0.05, "atr_stop": 3.0, "use_sfp_entry": False, "use_poc_tp": False},
    {"rsi_oversold": 25, "rsi_overbought": 65, "bb_proximity": 0.10, "atr_stop": 3.0, "use_sfp_entry": False, "use_poc_tp": False},
    {"rsi_oversold": 30, "rsi_overbought": 70, "bb_proximity": 0.05, "atr_stop": 3.0, "use_sfp_entry": False, "use_poc_tp": False},
    {"rsi_oversold": 30, "rsi_overbought": 70, "bb_proximity": 0.20, "atr_stop": 3.0, "use_sfp_entry": False, "use_poc_tp": False},
    {"rsi_oversold": 35, "rsi_overbought": 65, "bb_proximity": 0.10, "atr_stop": 3.0, "use_sfp_entry": False, "use_poc_tp": False},
    {"rsi_oversold": 35, "rsi_overbought": 75, "bb_proximity": 0.20, "atr_stop": 3.0, "use_sfp_entry": False, "use_poc_tp": False},
    {"rsi_oversold": 25, "rsi_overbought": 70, "bb_proximity": 0.15, "atr_stop": 3.0, "use_sfp_entry": False, "use_poc_tp": False},
    {"rsi_oversold": 30, "rsi_overbought": 75, "bb_proximity": 0.05, "atr_stop": 3.0, "use_sfp_entry": True, "use_poc_tp": False},
    {"rsi_oversold": 30, "rsi_overbought": 70, "bb_proximity": 0.10, "atr_stop": 3.0, "use_sfp_entry": True, "use_poc_tp": True},
]

# Momentum Scalping variations — focused subset
MS_VARIATIONS = [
    {"ema_fast": 8, "ema_slow": 20, "atr_stop": 2.0, "atr_target": 3.0, "use_cvd_confirm": False, "cvd_window": 20},
    {"ema_fast": 8, "ema_slow": 30, "atr_stop": 1.5, "atr_target": 2.5, "use_cvd_confirm": False, "cvd_window": 20},
    {"ema_fast": 12, "ema_slow": 30, "atr_stop": 2.0, "atr_target": 3.0, "use_cvd_confirm": False, "cvd_window": 20},
    {"ema_fast": 12, "ema_slow": 50, "atr_stop": 2.5, "atr_target": 3.5, "use_cvd_confirm": False, "cvd_window": 20},
    {"ema_fast": 20, "ema_slow": 50, "atr_stop": 2.0, "atr_target": 3.0, "use_cvd_confirm": False, "cvd_window": 20},
    {"ema_fast": 9, "ema_slow": 21, "atr_stop": 1.5, "atr_target": 2.5, "use_cvd_confirm": False, "cvd_window": 20},
    {"ema_fast": 9, "ema_slow": 26, "atr_stop": 2.0, "atr_target": 3.0, "use_cvd_confirm": False, "cvd_window": 20},
    {"ema_fast": 12, "ema_slow": 26, "atr_stop": 1.5, "atr_target": 2.5, "use_cvd_confirm": False, "cvd_window": 20},
    {"ema_fast": 8, "ema_slow": 21, "atr_stop": 2.0, "atr_target": 3.0, "use_cvd_confirm": True, "cvd_window": 20},
]

# Grid Trading variations
GT_VARIATIONS = [
    {"adx_threshold": 15.0, "spacing_mult": 0.30, "use_poc_center": False, "use_va_bounds": False},
    {"adx_threshold": 15.0, "spacing_mult": 0.50, "use_poc_center": False, "use_va_bounds": False},
    {"adx_threshold": 20.0, "spacing_mult": 0.30, "use_poc_center": False, "use_va_bounds": False},
    {"adx_threshold": 20.0, "spacing_mult": 0.50, "use_poc_center": False, "use_va_bounds": False},
    {"adx_threshold": 20.0, "spacing_mult": 0.65, "use_poc_center": False, "use_va_bounds": False},
    {"adx_threshold": 25.0, "spacing_mult": 0.50, "use_poc_center": False, "use_va_bounds": False},
    {"adx_threshold": 25.0, "spacing_mult": 0.80, "use_poc_center": False, "use_va_bounds": False},
    {"adx_threshold": 20.0, "spacing_mult": 0.50, "use_poc_center": True, "use_va_bounds": False},
    {"adx_threshold": 20.0, "spacing_mult": 0.50, "use_poc_center": False, "use_va_bounds": True},
]

# MA Crossover variations
MA_VARIATIONS = [
    {"fast_period": 10, "slow_period": 50, "pullback_max": 0.06, "use_va_chop_filter": False},
    {"fast_period": 10, "slow_period": 100, "pullback_max": 0.06, "use_va_chop_filter": False},
    {"fast_period": 10, "slow_period": 200, "pullback_max": 0.06, "use_va_chop_filter": False},
    {"fast_period": 20, "slow_period": 50, "pullback_max": 0.06, "use_va_chop_filter": False},
    {"fast_period": 20, "slow_period": 100, "pullback_max": 0.06, "use_va_chop_filter": False},
    {"fast_period": 20, "slow_period": 200, "pullback_max": 0.06, "use_va_chop_filter": False},
    {"fast_period": 50, "slow_period": 100, "pullback_max": 0.06, "use_va_chop_filter": False},
    {"fast_period": 50, "slow_period": 200, "pullback_max": 0.06, "use_va_chop_filter": False},
    {"fast_period": 20, "slow_period": 50, "pullback_max": 0.04, "use_va_chop_filter": False},
    {"fast_period": 20, "slow_period": 50, "pullback_max": 0.08, "use_va_chop_filter": False},
    {"fast_period": 20, "slow_period": 50, "pullback_max": 0.06, "use_va_chop_filter": True},
]

# Liquidation Capture variations — focused subset
LC_VARIATIONS = [
    {"price_threshold": 0.020, "volume_mult": 2.0, "rsi_threshold": 15.0, "use_nearest_tp": False},
    {"price_threshold": 0.020, "volume_mult": 2.0, "rsi_threshold": 18.0, "use_nearest_tp": False},
    {"price_threshold": 0.020, "volume_mult": 2.0, "rsi_threshold": 22.0, "use_nearest_tp": False},
    {"price_threshold": 0.020, "volume_mult": 2.5, "rsi_threshold": 18.0, "use_nearest_tp": False},
    {"price_threshold": 0.020, "volume_mult": 3.0, "rsi_threshold": 18.0, "use_nearest_tp": False},
    {"price_threshold": 0.025, "volume_mult": 2.0, "rsi_threshold": 18.0, "use_nearest_tp": False},
    {"price_threshold": 0.025, "volume_mult": 2.5, "rsi_threshold": 18.0, "use_nearest_tp": False},
    {"price_threshold": 0.025, "volume_mult": 3.0, "rsi_threshold": 18.0, "use_nearest_tp": False},
    {"price_threshold": 0.030, "volume_mult": 2.0, "rsi_threshold": 18.0, "use_nearest_tp": False},
    {"price_threshold": 0.030, "volume_mult": 2.5, "rsi_threshold": 18.0, "use_nearest_tp": False},
    {"price_threshold": 0.020, "volume_mult": 2.0, "rsi_threshold": 22.0, "use_nearest_tp": True},
    {"price_threshold": 0.025, "volume_mult": 2.5, "rsi_threshold": 22.0, "use_nearest_tp": True},
]


def load_data(interval: str) -> pd.DataFrame:
    """Load BTCUSDT data for 2024 at the given interval."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    print(f"  Loading {SYMBOL} {interval} data for 2024...")
    df = get_candles(SYMBOL, interval, start, end)
    if df.empty:
        print(f"  WARNING: No data loaded for {interval}")
        return df
    print(f"  Loaded {len(df)} bars ({df.index[0].date()} to {df.index[-1].date()})")
    return df


def run_strategy_test(name: str, df: pd.DataFrame, params: dict, label: str) -> dict:
    """Run a single strategy test and return metrics."""
    func, grid, defaults = STRATEGY_REGISTRY[name]
    interval = STRATEGY_TIMEFRAME_CONFIG[name]["interval"]
    bars_py = INTERVAL_BARS_PER_YEAR[interval]

    full_params = {**defaults, **params}

    try:
        equity, trades = func(df, cutoff=CUTOFF, **full_params)
        metrics = compute_metrics(equity, trades, bars_per_year=bars_py)

        return {
            "strategy": name,
            "label": label,
            "interval": interval,
            "n_trades": metrics["n_trades"],
            "win_rate_pct": round(metrics["win_rate_pct"], 1),
            "profit_factor": round(metrics["profit_factor"], 3) if metrics["profit_factor"] != float("inf") else "inf",
            "net_return_pct": round(metrics["total_return_pct"], 2),
            "sharpe": round(metrics["sharpe"], 3),
            "max_dd_pct": round(metrics["max_dd_pct"], 2),
            "cagr_pct": round(metrics["cagr_pct"], 2),
            "params": str({k: v for k, v in params.items() if k in defaults and defaults[k] != v}),
        }
    except Exception as e:
        print(f"  ERROR running {name} ({label}): {e}")
        return {
            "strategy": name,
            "label": label,
            "interval": interval,
            "n_trades": 0,
            "win_rate_pct": 0,
            "profit_factor": 0,
            "net_return_pct": 0,
            "sharpe": 0,
            "max_dd_pct": 0,
            "cagr_pct": 0,
            "params": str(params),
            "error": str(e),
        }


def find_best_variations(name: str, df: pd.DataFrame, variations: list, max_results: int = 5) -> list:
    """Test all variations and return the top N by net return."""
    print(f"  Testing {len(variations)} variations for {name}...")
    results = []
    for i, params in enumerate(variations):
        label = f"var {i+1}"
        r = run_strategy_test(name, df, params, label)
        results.append(r)
        if (i + 1) % 5 == 0 or (i + 1) == len(variations):
            print(f"    ... {i+1}/{len(variations)} tested")

    # Sort by net return descending
    results.sort(key=lambda x: x["net_return_pct"], reverse=True)
    return results[:max_results]


def main():
    print("=" * 80)
    print("BTV2 — Testing ALL Non-VWAP Strategies on BTCUSDT 2024")
    print("=" * 80)

    all_results = []

    # Strategies to test (excluding VWAP Scalping)
    strategies_to_test = [
        "Mean Reversion",
        "Momentum Scalping",
        "Liquidation Capture",
        "Grid Trading",
        "MA Crossover",
    ]

    variation_map = {
        "Mean Reversion": MR_VARIATIONS,
        "Momentum Scalping": MS_VARIATIONS,
        "Liquidation Capture": LC_VARIATIONS,
        "Grid Trading": GT_VARIATIONS,
        "MA Crossover": MA_VARIATIONS,
    }

    for strategy_name in strategies_to_test:
        print(f"\n{'='*60}")
        print(f"Testing: {strategy_name}")
        print(f"{'='*60}")

        interval = STRATEGY_TIMEFRAME_CONFIG[strategy_name]["interval"]
        df = load_data(interval)

        if df.empty:
            print(f"  SKIPPING {strategy_name} — no data available")
            continue

        # 1. Test with defaults
        print(f"\n  Running with default parameters...")
        default_result = run_strategy_test(strategy_name, df, {}, "defaults")
        all_results.append(default_result)
        print(f"    Trades: {default_result['n_trades']}, "
              f"WR: {default_result['win_rate_pct']}%, "
              f"PF: {default_result['profit_factor']}, "
              f"Net: {default_result['net_return_pct']}%, "
              f"Sharpe: {default_result['sharpe']}")

        # 2. Test variations
        variations = variation_map[strategy_name]
        best_vars = find_best_variations(strategy_name, df, variations, max_results=5)
        all_results.extend(best_vars)

        for r in best_vars:
            print(f"    {r['label']}: Trades={r['n_trades']}, "
                  f"WR={r['win_rate_pct']}%, PF={r['profit_factor']}, "
                  f"Net={r['net_return_pct']}%, Sharpe={r['sharpe']}")

    # ─────────────────────────────────────────────────────────────────────
    # SAVE RESULTS
    # ─────────────────────────────────────────────────────────────────────
    csv_path = RESULTS_DIR / "other_strategies_test_results.csv"
    md_path = RESULTS_DIR / "other_strategies_test_report.md"

    # CSV
    fieldnames = ["strategy", "label", "interval", "n_trades", "win_rate_pct",
                  "profit_factor", "net_return_pct", "sharpe", "max_dd_pct",
                  "cagr_pct", "params"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in all_results:
            row = {k: r.get(k, "") for k in fieldnames}
            writer.writerow(row)
    print(f"\nCSV saved to: {csv_path}")

    # Markdown report
    # Sort by net return for the main table
    sorted_results = sorted(all_results, key=lambda x: x["net_return_pct"], reverse=True)

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("# BTV2 — Other Strategies Test Report\n\n")
        f.write(f"**Asset:** BTCUSDT | **Year:** 2024 | **Cutoff:** {CUTOFF}\n\n")

        # Summary table
        f.write("## All Results (sorted by Net Return)\n\n")
        f.write("| Strategy | Config | Interval | Trades | WR% | PF | Net% | Sharpe | MaxDD% |\n")
        f.write("|----------|--------|----------|--------|-----|-----|------|--------|--------|\n")
        for r in sorted_results:
            f.write(f"| {r['strategy']} | {r['label']} | {r['interval']} | "
                    f"{r['n_trades']} | {r['win_rate_pct']} | {r['profit_factor']} | "
                    f"{r['net_return_pct']} | {r['sharpe']} | {r['max_dd_pct']} |\n")

        f.write("\n## Success Criteria Check\n\n")
        f.write("Goal: Net Return > 0%, Win Rate > 40%, Trades >= 10\n\n")
        f.write("| Strategy | Config | Net% | WR% | Trades | PASS? |\n")
        f.write("|----------|--------|------|-----|--------|-------|\n")
        for r in sorted_results:
            passes = (r["net_return_pct"] > 0 and
                      r["win_rate_pct"] > 40 and
                      r["n_trades"] >= 10)
            status = "PASS" if passes else "FAIL"
            f.write(f"| {r['strategy']} | {r['label']} | "
                    f"{r['net_return_pct']} | {r['win_rate_pct']} | "
                    f"{r['n_trades']} | {status} |\n")

        # Best per strategy
        f.write("\n## Best Config Per Strategy\n\n")
        for strat in strategies_to_test:
            strat_results = [r for r in sorted_results if r["strategy"] == strat]
            if strat_results:
                best = strat_results[0]
                f.write(f"### {best['strategy']}\n")
                f.write(f"- **Best config:** {best['label']}\n")
                f.write(f"- **Net Return:** {best['net_return_pct']}%\n")
                f.write(f"- **Win Rate:** {best['win_rate_pct']}%\n")
                f.write(f"- **Profit Factor:** {best['profit_factor']}\n")
                f.write(f"- **Sharpe:** {best['sharpe']}\n")
                f.write(f"- **Max Drawdown:** {best['max_dd_pct']}%\n")
                f.write(f"- **Trades:** {best['n_trades']}\n")
                f.write(f"- **Params:** {best['params']}\n\n")

        # Parameter details for top performers
        f.write("\n## Parameter Details for Top 5 Performers\n\n")
        for r in sorted_results[:5]:
            f.write(f"### {r['strategy']} ({r['label']})\n")
            f.write(f"```\n{r['params']}\n```\n\n")

    print(f"Markdown report saved to: {md_path}")

    # Print final summary
    print("\n" + "=" * 80)
    print("FINAL SUMMARY")
    print("=" * 80)
    print(f"{'Strategy':<25} {'Config':<15} {'Trades':>7} {'WR%':>6} {'PF':>6} {'Net%':>8} {'Sharpe':>8}")
    print("-" * 80)
    for r in sorted_results:
        print(f"{r['strategy']:<25} {r['label']:<15} {r['n_trades']:>7} "
              f"{r['win_rate_pct']:>6.1f} {r['profit_factor']:>6} "
              f"{r['net_return_pct']:>8.2f} {r['sharpe']:>8.3f}")

    # Count passing strategies
    passing = [r for r in sorted_results
               if r["net_return_pct"] > 0 and r["win_rate_pct"] > 40 and r["n_trades"] >= 10]
    print(f"\n{'='*80}")
    print(f"Strategies meeting ALL criteria (Net>0%, WR>40%, Trades>=10): {len(passing)}")
    for r in passing:
        print(f"  PASS {r['strategy']} ({r['label']}): Net={r['net_return_pct']}%, "
              f"WR={r['win_rate_pct']}%, Trades={r['n_trades']}")
    if not passing:
        print("  NONE found - all strategies failed to meet criteria")


if __name__ == "__main__":
    main()
