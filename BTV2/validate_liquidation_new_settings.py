"""
validate_liquidation_new_settings.py
==================================
Validate Liquidation Capture with new looser settings (2018-2025).

New Settings (.env):
- LIQUIDATION_PRICE_THRESHOLD=0.020    # Looser (was 0.030)
- LIQUIDATION_VOLUME_MULTIPLIER=1.5    # Looser (was 3.0)
- LIQUIDATION_MIN_CONSECUTIVE_MOVES=3 # Looser (was 4)
- LIQUIDATION_MIN_WICK_RATIO=1.5       # Same
- LIQUIDATION_RRR_TARGET=3.0          # Higher 3:1 TP

Two Modes:
- Normal: New looser settings as specified
- Strict: Slightly tighter (price=0.025, vol=2.0, consec=4)
"""

import sys
from pathlib import Path
from datetime import datetime

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
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CUTOFF = 0.10  # Butterworth cutoff

# Two configurations to test
CONFIGS = {
    "Normal": {
        # New looser settings from .env
        "price_threshold": 0.020,
        "volume_mult": 1.5,
        "rsi_threshold": 18.0,
        "min_consecutive_moves": 3,
        "min_wick_ratio": 1.5,
        "rrr_target": 3.0,
    },
    "Strict": {
        # Slightly tighter than normal (but still looser than original)
        "price_threshold": 0.025,
        "volume_mult": 2.0,
        "rsi_threshold": 15.0,
        "min_consecutive_moves": 4,
        "min_wick_ratio": 1.5,
        "rrr_target": 3.0,
    },
}

# Test period: 2018-2025 (8 years)
YEAR_RANGES = [
    ("2018-01-01", "2019-01-01"),
    ("2019-01-01", "2020-01-01"),
    ("2020-01-01", "2021-01-01"),
    ("2021-01-01", "2022-01-01"),
    ("2022-01-01", "2023-01-01"),
    ("2023-01-01", "2024-01-01"),
    ("2024-01-01", "2025-01-01"),
    ("2025-01-01", "2026-01-01"),
]

MAJOR_MARKET_EVENTS = {
    2018: "Crypto crash (-73%)",
    2019: "Recovery (+95%)",
    2020: "COVID crash + Bull (+300%)",
    2021: "Peak $64K + Crash",
    2022: "Bear market (-65%)",
    2023: "Recovery (+155%)",
    2024: "New ATH (+120%)",
    2025: "Consolidation",
}


def load_daily_data(start: str, end: str) -> pd.DataFrame:
    """Load daily data."""
    path = DATA_DIR / "BTCUSDT_1d.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index, utc=True)
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        mask = (df.index >= start_ts) & (df.index < end_ts)
        return df.loc[mask].copy()
    # Fallback: resample from 5m
    return _load_and_resample("5m", "1D", start, end)


def _load_and_resample(source_interval: str, rule: str, start: str, end: str) -> pd.DataFrame:
    """Load source interval parquet and resample to target."""
    path = DATA_DIR / f"BTCUSDT_{source_interval}.parquet"
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df.index >= start_ts) & (df.index < end_ts)
    sliced = df.loc[mask].copy()
    return _resample_ohlcv(sliced, rule)


def run_backtest(start: str, end: str, config_name: str, params: dict) -> pd.DataFrame:
    """Run yearly backtest for a specific config."""
    df = load_daily_data(start, end)

    if df.empty:
        return pd.DataFrame()

    bars_per_year = INTERVAL_BARS_PER_YEAR["1d"]

    equity, trades = run_liquidation_capture(
        df, CUTOFF,
        price_threshold=params["price_threshold"],
        volume_mult=params["volume_mult"],
        rsi_threshold=params["rsi_threshold"],
        min_consecutive_moves=params["min_consecutive_moves"],
        min_wick_ratio=params["min_wick_ratio"],
        rrr_target=params["rrr_target"],
    )

    metrics = compute_metrics(equity, trades, bars_per_year=bars_per_year)
    year = int(start[:4])

    return pd.DataFrame([{
        "Year": year,
        "Mode": config_name,
        "Trades": metrics["n_trades"],
        "WR%": round(metrics["win_rate_pct"], 1),
        "PF": round(metrics["profit_factor"], 3),
        "Net%": round(metrics["total_return_pct"], 2),
        "Sharpe": round(metrics["sharpe"], 3),
        "MaxDD%": round(metrics["max_dd_pct"], 2),
    }])


def main():
    print("=" * 80)
    print("LIQUIDATION CAPTURE - NEW SETTINGS VALIDATION (2018-2025)")
    print("=" * 80)

    # Show new settings
    print("\n### New Settings (from .env):")
    print(f"  price_threshold: {CONFIGS['Normal']['price_threshold']}")
    print(f"  volume_mult: {CONFIGS['Normal']['volume_mult']}")
    print(f"  rsi_threshold: {CONFIGS['Normal']['rsi_threshold']}")
    print(f"  min_consecutive_moves: {CONFIGS['Normal']['min_consecutive_moves']}")
    print(f"  min_wick_ratio: {CONFIGS['Normal']['min_wick_ratio']}")
    print(f"  rrr_target: {CONFIGS['Normal']['rrr_target']}")

    print("\n### Strict Mode:")
    for k, v in CONFIGS["Strict"].items():
        print(f"  {k}: {v}")

    # Run tests
    all_results = []

    for config_name, params in CONFIGS.items():
        print(f"\n{'='*60}")
        print(f"Testing: {config_name}")
        print(f"{'='*60}")

        for start, end in YEAR_RANGES:
            year = int(start[:4])
            df_result = run_backtest(start, end, config_name, params)

            if df_result.empty:
                print(f"  {year}: No data")
                continue

            row = df_result.iloc[0]
            print(f"  {year}: Trades={row['Trades']}, WR={row['WR%']}%, PF={row['PF']:.3f}, "
                  f"Net={row['Net%']:+.2f}%, Sharpe={row['Sharpe']:.3f}")

            if row['Trades'] > 0:
                print(f"       Event: {MAJOR_MARKET_EVENTS.get(year, 'N/A')}")

            all_results.append(row)

    # Save results
    df_results = pd.DataFrame(all_results)
    output_path = RESULTS_DIR / "liquidation_updated_validation.csv"
    df_results.to_csv(output_path, index=False)
    print(f"\nResults saved to {output_path}")

    # Summary
    print("\n" + "=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)

    summary = []

    for mode in CONFIGS.keys():
        df_mode = df_results[df_results["Mode"] == mode]
        total_trades = int(df_mode["Trades"].sum())
        total_net = df_mode["Net%"].sum()

        # Calculate weighted average WR%
        if total_trades > 0:
            weighted_wr = (df_mode["WR%"] * df_mode["Trades"]).sum() / total_trades
        else:
            weighted_wr = 0.0

        # Calculate average Sharpe and MaxDD
        df_with_trades = df_mode[df_mode["Trades"] > 0]
        if len(df_with_trades) > 0:
            avg_sharpe = df_with_trades["Sharpe"].mean()
            avg_maxdd = df_with_trades["MaxDD%"].mean()
        else:
            avg_sharpe = 0.0
            avg_maxdd = 0.0

        print(f"\n{mode} (Daily):")
        print(f"  Total Trades (2018-2025): {total_trades}")
        print(f"  Weighted Win Rate: {weighted_wr:.1f}%")
        print(f"  Total Net%: {total_net:+.2f}%")
        print(f"  Avg Sharpe: {avg_sharpe:.3f}")
        print(f"  Avg MaxDD%: {avg_maxdd:.2f}%")

        summary.append({
            "Mode": mode,
            "Total Trades": total_trades,
            "Win Rate%": round(weighted_wr, 1),
            "Net%": round(total_net, 2),
            "Avg Sharpe": round(avg_sharpe, 3),
            "Avg MaxDD%": round(avg_maxdd, 2),
        })

    # Comparison
    print("\n" + "=" * 80)
    print("COMPARISON")
    print("=" * 80)

    # Sort by net return
    summary_sorted = sorted(summary, key=lambda x: x["Net%"], reverse=True)

    print("\n### Rankings by Net Return:")
    for i, s in enumerate(summary_sorted, 1):
        print(f"  {i}. {s['Mode']}: {s['Net%']:+.2f}% ({s['Total Trades']} trades, "
              f"{s['Win Rate%']:.1f}% WR)")

    # Compare to previous (original settings)
    print("\n### Comparison to Previous Results:")
    print(f"  Previous (original settings): 1 trade in 7 years")

    best = summary_sorted[0]
    print(f"\n  BEST: {best['Mode']} with {best['Total Trades']} trades and {best['Net%']:+.2f}%")

    # Target analysis
    print("\n### Target Analysis:")
    if best["Total Trades"] >= 10:
        print(f"  {best['Mode']}: {best['Total Trades']} trades - MEETS TARGET [OK]")
    else:
        print(f"  {best['Mode']}: {best['Total Trades']} trades - NEEDS MORE TRADES")

    # Save summary
    df_summary = pd.DataFrame(summary)
    summary_path = RESULTS_DIR / "liquidation_updated_summary.csv"
    df_summary.to_csv(summary_path, index=False)
    print(f"\nSummary saved to {summary_path}")

    print(f"\nValidation completed: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)


if __name__ == "__main__":
    main()