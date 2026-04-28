"""
validate_liquidation_capture_looser.py
=====================================
Test THREE liquidation capture configurations to find what works best:

Config A: 4h timeframe (more signals than daily)
- Use 4h bars instead of daily
- Keep threshold at 0.030
- This should capture more liquidation events

Config B: Lower threshold (0.020)
- Keep daily bars
- Lower price_threshold from 0.030 to 0.020 (2% instead of 3%)
- Lower volume_mult from 3.0 to 1.5

Config C: Combined (4h + lower threshold)
- 4h bars + 0.020 threshold + 1.5 volume

Test Period: 2018-01-01 to 2025-12-31 (7 years)

Output: Per-year metrics and totals for each config
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

# Three configurations to test
CONFIGS = {
    "A_4h_standard": {
        "price_threshold": 0.030,
        "volume_mult": 3.0,
        "rsi_threshold": 18.0,
        "timeframe": "4h",
    },
    "B_daily_loose": {
        "price_threshold": 0.020,
        "volume_mult": 1.5,
        "rsi_threshold": 15.0,
        "timeframe": "1d",
    },
    "C_4h_loose": {
        "price_threshold": 0.020,
        "volume_mult": 1.5,
        "rsi_threshold": 15.0,
        "timeframe": "4h",
    },
}

# Test period: 2018-2025 (7 years, extended to include 2025)
YEAR_RANGES = [
    ("2018-01-01", "2019-01-01"),
    ("2019-01-01", "2020-01-01"),
    ("2020-01-01", "2021-01-01"),
    ("2021-01-01", "2022-01-01"),
    ("2022-01-01", "2023-01-01"),
    ("2023-01-01", "2024-01-01"),
    ("2024-01-01", "2025-01-01"),
    # Include partial 2025 (through Dec 2025)
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


def load_data(timeframe: str, start: str, end: str) -> pd.DataFrame:
    """Load data for the specified timeframe."""
    if timeframe == "1d":
        return load_1d_data(start, end)
    elif timeframe == "4h":
        return load_4h_data(start, end)
    else:
        raise ValueError(f"Unknown timeframe: {timeframe}")


def load_1d_data(start: str, end: str) -> pd.DataFrame:
    """Load daily data."""
    path = DATA_DIR / "BTCUSDT_1d.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index, utc=True)
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        mask = (df.index >= start_ts) & (df.index < end_ts)
        return df.loc[mask].copy()
    return _load_and_resample("5m", "1D", start, end)


def load_4h_data(start: str, end: str) -> pd.DataFrame:
    """Load 4h data - try native first, then resample from 5m."""
    path = DATA_DIR / "BTCUSDT_4h.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index, utc=True)
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        mask = (df.index >= start_ts) & (df.index < end_ts)
        sliced = df.loc[mask].copy()
        if not sliced.empty:
            # Check if we got meaningful data or just a few rows
            if len(sliced) > 100:
                return sliced
    
    # Fallback: resample from 5m
    return _load_and_resample("5m", "240min", start, end)


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


def get_bars_per_year(timeframe: str) -> int:
    """Get approximate bars per year for the timeframe."""
    if timeframe == "1d":
        return INTERVAL_BARS_PER_YEAR["1d"]
    elif timeframe == "4h":
        return INTERVAL_BARS_PER_YEAR["4h"]
    return INTERVAL_BARS_PER_YEAR["1d"]


def run_backtest(start: str, end: str, config_name: str, params: dict) -> pd.DataFrame:
    """Run yearly backtest for a specific config."""
    timeframe = params["timeframe"]
    df = load_data(timeframe, start, end)
    
    if df.empty:
        return pd.DataFrame()
    
    bars_per_year = get_bars_per_year(timeframe)
    
    equity, trades = run_liquidation_capture(
        df, CUTOFF,
        price_threshold=params["price_threshold"],
        volume_mult=params["volume_mult"],
        rsi_threshold=params["rsi_threshold"],
    )
    
    metrics = compute_metrics(equity, trades, bars_per_year=bars_per_year)
    year = int(start[:4])
    
    return pd.DataFrame([{
        "Year": year,
        "Config": config_name,
        "Timeframe": timeframe,
        "Trades": metrics["n_trades"],
        "WR%": round(metrics["win_rate_pct"], 1),
        "PF": round(metrics["profit_factor"], 3),
        "Net%": round(metrics["total_return_pct"], 2),
        "Sharpe": round(metrics["sharpe"], 3),
        "MaxDD%": round(metrics["max_dd_pct"], 2),
    }])


def main():
    print("=" * 80)
    print("LIQUIDATION CAPTURE - LOOSER SETTINGS VALIDATION")
    print("=" * 80)
    print(f"\nTest Period: 2018-2025 (7 years)")
    print(f"Configs: A (4h standard), B (daily loose), C (4h loose)")
    
    # Show configs
    for name, params in CONFIGS.items():
        print(f"\n### {name}:")
        print(f"  timeframe: {params['timeframe']}")
        print(f"  price_threshold: {params['price_threshold']}")
        print(f"  volume_mult: {params['volume_mult']}")
        print(f"  rsi_threshold: {params['rsi_threshold']}")
    
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
                  f"Net={row['Net%']:.2f}%, Sharpe={row['Sharpe']:.3f}")
            
            if row['Trades'] > 0:
                print(f"       Event: {MAJOR_MARKET_EVENTS.get(year, 'N/A')}")
            
            all_results.append(row)
    
    # Save results
    df_results = pd.DataFrame(all_results)
    output_path = RESULTS_DIR / "liquidation_capture_looser_validation.csv"
    df_results.to_csv(output_path, index=False)
    print(f"\nResults saved to {output_path}")
    
    # Summary
    print("\n" + "=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)
    
    # Totals per config
    summary = []
    
    for config_name in CONFIGS.keys():
        df_config = df_results[df_results["Config"] == config_name]
        total_trades = int(df_config["Trades"].sum())
        total_net = df_config["Net%"].sum()
        
        # Calculate weighted average WR%
        if total_trades > 0:
            weighted_wr = (df_config["WR%"] * df_config["Trades"]).sum() / total_trades
        else:
            weighted_wr = 0.0
        
        # Calculate average Sharpe (across years with trades)
        df_with_trades = df_config[df_config["Trades"] > 0]
        if len(df_with_trades) > 0:
            avg_sharpe = df_with_trades["Sharpe"].mean()
            avg_maxdd = df_with_trades["MaxDD%"].mean()
        else:
            avg_sharpe = 0.0
            avg_maxdd = 0.0
        
        # Calculate summary PF (sum of wins / sum of losses would need raw data)
        # Approximate with simple sum
        total_pf = df_config["PF"].sum()
        
        timeframe = CONFIGS[config_name]["timeframe"]
        
        print(f"\n{config_name} ({timeframe}):")
        print(f"  Total Trades (2018-2025): {total_trades}")
        print(f"  Weighted Win Rate: {weighted_wr:.1f}%")
        print(f"  Total Net%: {total_net:.2f}%")
        print(f"  Avg Sharpe: {avg_sharpe:.3f}")
        print(f"  Avg MaxDD%: {avg_maxdd:.2f}%")
        
        summary.append({
            "Config": config_name,
            "Timeframe": timeframe,
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
        print(f"  {i}. {s['Config']}: {s['Net%']:+.2f}% ({s['Total Trades']} trades, {s['Win Rate%']:.1f}% WR)")
    
    # Compare to previous
    print("\n### Comparison to Previous Results:")
    print(f"  Previous PROD (A): 1 trade, -19.1%")
    print(f"  Previous LOOSE (B): 6 trades, +11.84%")
    
    best = summary_sorted[0]
    print(f"\n  BEST: {best['Config']} with {best['Total Trades']} trades and {best['Net%']:+.2f}%")
    
    # Target analysis
    print("\n### Target Analysis (3-10+ trades/year = 21-70 total):")
    for s in summary:
        if s["Total Trades"] >= 21:
            print(f"  {s['Config']}: {s['Total Trades']} trades - MEETS TARGET [OK]")
        elif s["Total Trades"] >= 10:
            print(f"  {s['Config']}: {s['Total Trades']} trades - CLOSE TO TARGET")
        else:
            print(f"  {s['Config']}: {s['Total Trades']} trades - NEEDS MORE TRADES")
    
    # Save summary
    df_summary = pd.DataFrame(summary)
    summary_path = RESULTS_DIR / "liquidation_capture_looser_summary.csv"
    df_summary.to_csv(summary_path, index=False)
    print(f"\nSummary saved to {summary_path}")
    
    print(f"\nValidation completed: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)


if __name__ == "__main__":
    main()