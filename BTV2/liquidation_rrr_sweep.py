"""
liquidation_rrr_sweep.py
=====================
Parameter sweep for Liquidation Capture focusing on RRR target optimization.

Since we already have 85.7% win rate, we can test tighter RRR targets
to get more trades and higher total net%.

Sweep Variables:
- RRR_TARGET: [2.0, 2.5, 3.0, 3.5, 4.0]
- PRICE_THRESHOLD: [0.015, 0.020, 0.025, 0.030]
- VOLUME_MULT: [1.0, 1.5, 2.0]
- MIN_CONSECUTIVE_MOVES: [2, 3, 4]

Test Period: 2018-01-01 to 2025-12-31 (daily bars)

Output:
- BTV2/results/liquidation_rrr_sweep.csv
"""

import itertools
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.strategies import (
    INTERVAL_BARS_PER_YEAR,
    compute_metrics,
    run_liquidation_capture,
    _resample_ohlcv,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CUTOFF = 0.10  # Butterworth cutoff

# Parameter sweep grid
RRR_TARGETS = [2.0, 2.5, 3.0, 3.5, 4.0]
PRICE_THRESHOLDS = [0.015, 0.020, 0.025, 0.030]
VOLUME_MULTS = [1.0, 1.5, 2.0]
MIN_CONSECUTIVE_MOVES = [2, 3, 4]

# Fixed parameters from previous successful config (known to generate trades)
RSI_THRESHOLD = 20.0  # Known to work (from previous sweep)
MIN_WICK_RATIO = 1.5  # Known to work


def load_daily_data(start: str, end: str) -> pd.DataFrame:
    """Load daily data, resample from 5m if needed."""
    path = DATA_DIR / "BTCUSDT_1d.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index, utc=True)
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        mask = (df.index >= start_ts) & (df.index < end_ts)
        sliced = df.loc[mask].copy()
        if not sliced.empty:
            return sliced

    # Fallback: resample from 5m
    return _load_and_resample("5m", "1D", start, end)


def _load_and_resample(source_interval: str, rule: str, start: str, end: str) -> pd.DataFrame:
    """Load and resample source interval to target."""
    path = DATA_DIR / f"BTCUSDT_{source_interval}.parquet"
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df.index >= start_ts) & (df.index < end_ts)
    sliced = df.loc[mask].copy()
    if sliced.empty:
        return sliced
    resampled = _resample_ohlcv(sliced, rule)
    return resampled


def run_sweep():
    """Run parameter sweep across all combinations."""
    print("=" * 80)
    print("LIQUIDATION CAPTURE RRR PARAMETER SWEEP (2018-2025)")
    print("=" * 80)

    # Load data once
    start, end = "2018-01-01", "2025-12-31"
    df = load_daily_data(start, end)
    print(f"\nLoaded {len(df)} daily bars from {df.index[0]} to {df.index[-1]}")

    # Generate all combinations
    grid = {
        "rrr_target": RRR_TARGETS,
        "price_threshold": PRICE_THRESHOLDS,
        "volume_mult": VOLUME_MULTS,
        "min_consecutive_moves": MIN_CONSECUTIVE_MOVES,
    }

    keys = list(grid.keys())
    values = list(grid.values())
    combos = list(itertools.product(*values))
    total = len(combos)
    print(f"Testing {total} parameter combinations...")
    print(f"  RRR_TARGET: {RRR_TARGETS}")
    print(f"  PRICE_THRESHOLD: {PRICE_THRESHOLDS}")
    print(f"  VOLUME_MULT: {VOLUME_MULTS}")
    print(f"  MIN_CONSECUTIVE_MOVES: {MIN_CONSECUTIVE_MOVES}")

    results = []
    for idx, combo in enumerate(combos):
        params = dict(zip(keys, combo))
        rrr = params["rrr_target"]
        pt = params["price_threshold"]
        vm = params["volume_mult"]
        mcm = params["min_consecutive_moves"]

        if (idx + 1) % 50 == 0 or idx == 0:
            print(f"\n  [{idx+1}/{total}] rrr={rrr}, pt={pt:.3f}, vm={vm:.1f}, mcm={mcm} ...")

        try:
            equity, trades = run_liquidation_capture(
                df,
                CUTOFF,
                price_threshold=pt,
                volume_mult=vm,
                rsi_threshold=RSI_THRESHOLD,
                min_consecutive_moves=mcm,
                min_wick_ratio=MIN_WICK_RATIO,
                rrr_target=rrr,
            )
            metrics = compute_metrics(
                equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"]
            )

            row = {
                "RRR_TARGET": rrr,
                "PRICE_THRESHOLD": pt,
                "VOLUME_MULT": vm,
                "MIN_CONSECUTIVE_MOVES": mcm,
                "Trades": metrics["n_trades"],
                "WR%": round(metrics["win_rate_pct"], 1),
                "PF": round(metrics["profit_factor"], 3),
                "Net%": round(metrics["total_return_pct"], 2),
                "Sharpe": round(metrics["sharpe"], 3),
                "MaxDD%": round(metrics["max_dd_pct"], 2),
            }
            results.append(row)

            if (idx + 1) % 50 == 0:
                print(f"Trades={row['Trades']}, WR={row['WR%']}%, Net={row['Net%']}%")

        except Exception as e:
            print(f"  ERROR at {idx+1}: {e}")
            results.append({
                "RRR_TARGET": rrr,
                "PRICE_THRESHOLD": pt,
                "VOLUME_MULT": vm,
                "MIN_CONSECUTIVE_MOVES": mcm,
                "Trades": 0, "WR%": 0.0, "PF": 0.0,
                "Net%": 0.0, "Sharpe": 0.0, "MaxDD%": 0.0,
            })

    # Save results
    df_results = pd.DataFrame(results)
    output_path = RESULTS_DIR / "liquidation_rrr_sweep.csv"
    df_results.to_csv(output_path, index=False)
    print(f"\n\nResults saved to {output_path}")

    # Analysis
    print("\n" + "=" * 80)
    print("ANALYSIS")
    print("=" * 80)

    # Filter to configs with trades
    df_with_trades = df_results[df_results["Trades"] >= 1].copy()

    if df_with_trades.empty:
        print("\nNO CONFIGS GENERATED ANY TRADES!")
        return df_results

    # 1. Best by Net% (all configs with trades)
    df_by_net = df_with_trades.sort_values("Net%", ascending=False)
    print("\n### TOP 10 BY NET% (with >= 1 trade):")
    print(df_by_net.head(10).to_string(index=False))

    # 2. Best by RRR target
    print("\n### RRR TARGET COMPARISON (avg across all other params):")
    rrr_cols = ["RRR_TARGET", "Trades", "WR%", "Net%", "Avg%"]
    rrr_summary = df_with_trades.groupby("RRR_TARGET").agg({
        "Trades": "sum",
        "WR%": "mean",
        "Net%": "mean",
        "Avg%": "mean",
        "MaxDD%": "mean",
    }).round(2)
    print(rrr_summary.to_string())

    # 3. Trade count vs Net% tradeoff
    print("\n### TRADE COUNT VS NET% (grouped by RRR_TARGET):")
    # Count configs with positive returns
    df_positive = df_with_trades[df_with_trades["Net%"] > 0]
    if not df_positive.empty:
        pos_by_rrr = df_positive.groupby("RRR_TARGET").agg({
            "Trades": ["count", "sum", "mean"],
            "Net%": ["sum", "mean"],
        }).round(2)
        print("Configs with positive Net%:")
        print(pos_by_rrr.to_string())

    # 4. Optimal combination
    print("\n### OPTIMAL COMBINATION:")
    # Find best config by Net% with at least 5 trades
    df_5trades = df_with_trades[df_with_trades["Trades"] >= 5]
    if not df_5trades.empty:
        best = df_5trades.sort_values("Net%", ascending=False).iloc[0]
        print(f"Best config (>= 5 trades):")
        print(f"  RRR_TARGET: {best['RRR_TARGET']}")
        print(f"  PRICE_THRESHOLD: {best['PRICE_THRESHOLD']}")
        print(f"  VOLUME_MULT: {best['VOLUME_MULT']}")
        print(f"  MIN_CONSECUTIVE_MOVES: {best['MIN_CONSECUTIVE_MOVES']}")
        print(f"  Trades: {best['Trades']}, WR%: {best['WR%']}%, Net%: {best['Net%']}%")
    else:
        # Fall back to any trades
        best = df_by_net.iloc[0]
        print(f"Best config (any trades):")
        print(f"  RRR_TARGET: {best['RRR_TARGET']}")
        print(f"  PRICE_THRESHOLD: {best['PRICE_THRESHOLD']}")
        print(f"  VOLUME_MULT: {best['VOLUME_MULT']}")
        print(f"  MIN_CONSECUTIVE_MOVES: {best['MIN_CONSECUTIVE_MOVES']}")
        print(f"  Trades: {best['Trades']}, WR%: {best['WR%']}%, Net%: {best['Net%']}%")

    return df_results


def main():
    df_results = run_sweep()
    print("\n" + "=" * 80)
    print("DONE")
    print("=" * 80)


if __name__ == "__main__":
    main()