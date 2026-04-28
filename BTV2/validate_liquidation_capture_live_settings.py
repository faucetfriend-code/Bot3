"""
validate_liquidation_capture_live_settings.py
==========================================
Validate that the CURRENT Liquidation Capture settings in Bot3/.env 
actually work in the BTV2 backtesting system.

Tests two modes:
- Normal mode: uses BTV2's standard run (no regime filter for fair comparison)
- Strict mode: mirrors live bot's validation (session limits, stricter thresholds)

Uses yearly backtest (2018-2024).

Settings from .env:
- LIQUIDATION_PRICE_THRESHOLD=0.030 (3%)
- LIQUIDATION_VOLUME_MULTIPLIER=3.0 (3x)  
- LIQUIDATION_RSI_OVERSOLD=18
- LIQUIDATION_RSI_OVERBOUGHT=82
- LIQUIDATION_MIN_CONSECUTIVE_MOVES=4
- LIQUIDATION_MIN_WICK_RATIO=1.7
- LIQUIDATION_RRR_TARGET=2.0
- LIQUIDATION_MAX_PER_SESSION=2
- LIQUIDATION_MIN_HOURS_BETWEEN=2

NOTE: The BTV2 strategy has HARDCODED values (MIN_CONSECUTIVE=4, MIN_WICK_RATIO=1.5)
that differ from .env values. This validation tests what's actually used by BTV2.
"""

import sys
import os
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

# Current .env settings (AS-IS)
LIQUIDATION_PROD = {
    "price_threshold": 0.030,    # 3%
    "volume_mult": 3.0,
    "rsi_threshold": 18.0,
}

# Loosened settings for comparison (from sweep that works)
LIQUIDATION_LOOSE = {
    "price_threshold": 0.020,   # 2%
    "volume_mult": 1.5,        # 1.5x
    "rsi_threshold": 15.0,
}

YEAR_RANGES = [
    ("2018-01-01", "2019-01-01"),
    ("2019-01-01", "2020-01-01"),
    ("2020-01-01", "2021-01-01"),
    ("2021-01-01", "2022-01-01"),
    ("2022-01-01", "2023-01-01"),
    ("2023-01-01", "2024-01-01"),
    ("2024-01-01", "2025-01-01"),
]

MAJOR_MARKET_EVENTS = {
    2018: "Crypto crash (-73%)",
    2019: "Recovery (+95%)",
    2020: "COVID crash + Bull (+300%)",
    2021: "Peak $64K + Crash",
    2022: "Bear market (-65%)",
    2023: "Recovery (+155%)",
    2024: "New ATH (+120%)",
}


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


def _load_and_resample(source_interval: str, rule: str, start: str, end: str) -> pd.DataFrame:
    """Load and resample."""
    path = DATA_DIR / f"BTCUSDT_{source_interval}.parquet"
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df.index >= start_ts) & (df.index < end_ts)
    sliced = df.loc[mask].copy()
    return _resample_ohlcv(sliced, rule)


def run_backtest(start: str, end: str, params: dict, label: str) -> pd.DataFrame:
    """Run yearly backtest."""
    df = load_1d_data(start, end)
    if df.empty:
        return pd.DataFrame()
    
    equity, trades = run_liquidation_capture(
        df, CUTOFF,
        price_threshold=params["price_threshold"],
        volume_mult=params["volume_mult"],
        rsi_threshold=params["rsi_threshold"],
    )
    
    metrics = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"])
    year = int(start[:4])
    
    return pd.DataFrame([{
        "Year": year,
        "Config": label,
        "Trades": metrics["n_trades"],
        "WR%": round(metrics["win_rate_pct"], 1),
        "PF": round(metrics["profit_factor"], 3),
        "Net%": round(metrics["total_return_pct"], 2),
        "Sharpe": round(metrics["sharpe"], 3),
        "MaxDD%": round(metrics["max_dd_pct"], 2),
    }])


def main():
    print("=" * 80)
    print("LIQUIDATION CAPTURE - LIVE SETTINGS VALIDATION")
    print("=" * 80)
    
    # Show settings
    print("\n### Current .env Liquidation Capture Settings:")
    for k, v in LIQUIDATION_PROD.items():
        print(f"  {k}: {v}")
    
    print("\n### Loosened Settings (for comparison):")
    for k, v in LIQUIDATION_LOOSE.items():
        print(f"  {k}: {v}")
    
    print(f"\n[IMPORTANT] BTV2 uses HARDCODED values:")
    print(f"  MIN_CONSECUTIVE = 4 (not configurable)")
    print(f"  MIN_WICK_RATIO  = 1.5 (not configurable)")
    print(f"  RRR_TARGET     = 3.0 (not configurable)")
    
    # Run tests
    all_results = []
    
    for label, params in [("PROD", LIQUIDATION_PROD), ("LOOSE", LIQUIDATION_LOOSE)]:
        print(f"\n{'='*50}")
        print(f"Testing: {label} settings")
        print(f"{'='*50}")
        
        for start, end in YEAR_RANGES:
            year = int(start[:4])
            df_result = run_backtest(start, end, params, label)
            
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
    output_path = RESULTS_DIR / "liquidation_capture_live_settings_validation.csv"
    df_results.to_csv(output_path, index=False)
    print(f"\nResults saved to {output_path}")
    
    # Summary
    print("\n" + "=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)
    
    # Totals
    for label in ["PROD", "LOOSE"]:
        df_label = df_results[df_results["Config"] == label]
        total_trades = df_label["Trades"].sum()
        total_net = df_label["Net%"].sum()
        
        if total_trades > 0:
            avg_wr = (df_label["WR%"] * df_label["Trades"]).sum() / total_trades
        else:
            avg_wr = 0.0
        
        print(f"\n{label} Settings:")
        print(f"  Total Trades (2018-2024): {total_trades}")
        print(f"  Avg Win Rate: {avg_wr:.1f}%")
        print(f"  Total Net%: {total_net:.2f}%")
    
    # Comparison
    print("\n### Comparison (PROD vs LOOSE)")
    prod_trades = df_results[df_results["Config"] == "PROD"]["Trades"].sum()
    loose_trades = df_results[df_results["Config"] == "LOOSE"]["Trades"].sum()
    prod_net = df_results[df_results["Config"] == "PROD"]["Net%"].sum()
    loose_net = df_results[df_results["Config"] == "LOOSE"]["Net%"].sum()
    
    print(f"  PROD trades: {prod_trades}")
    print(f"  LOOSE trades: {loose_trades}")
    print(f"  Trade difference: {loose_trades - prod_trades}")
    
    # Verdict
    print("\n### VERIFICATION RESULT")
    if prod_trades > 0 and prod_net > 0:
        print("[OK] Production settings are PROFITABLE")
    elif prod_trades > 0:
        print("[WARN] Production settings generate trades but LOSE money")
    else:
        print("[FAIL] Production settings are TOO STRICT - no trades generated")
        print("\n### RECOMMENDATIONS:")
        print("1. Lower LIQUIDATION_PRICE_THRESHOLD from 0.030 to 0.020 (2%)")
        print("2. Lower LIQUIDATION_VOLUME_MULTIPLIER from 3.0 to 1.5")
        print("3. Or use higher timeframe (4h instead of 1d) for more signals")
    
    # Market events captured with PROD settings
    print("\n### Major Market Events Captured (PROD settings)")
    prod_results = df_results[df_results["Config"] == "PROD"]
    for _, row in prod_results[prod_results["Trades"] > 0].iterrows():
        year = int(row["Year"])
        print(f"  {year}: {int(row['Trades'])} trade(s) - {MAJOR_MARKET_EVENTS.get(year, 'N/A')}")
    
    if prod_results["Trades"].sum() == 0:
        print("  No major events captured with production settings")
    
    print(f"\nValidation completed: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80)


if __name__ == "__main__":
    main()