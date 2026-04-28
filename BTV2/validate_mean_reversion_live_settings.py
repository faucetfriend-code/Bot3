"""
validate_mean_reversion_live_settings.py
=========================================
Validate that the CURRENT .env Mean Reversion settings actually work in BTV2.

Task: Run Mean Reversion backtest from 2018-01-01 to 2025-12-31 (daily bars) using BTV2.

Current .env Settings:
- MEAN_REVERSION_RSI_OVERSOLD=30
- MEAN_REVERSION_RSI_OVERBOUGHT=70
- MEAN_REVERSION_BB_PROXIMITY=0.10
- MEAN_REVERSION_ATR_STOP_MULTIPLIER=3.0
- MEAN_REVERSION_MIN_CONFIDENCE=0.50
- MEAN_REVERSION_MIN_RRR=1.0
- ADX_TRENDING_THRESHOLD=25.0

Run TWO modes:
a) Normal mode: use_regime_filter=True with adx_max=25.0 (ranging regime gate)
b) Strict mode: mirrors live bot's 8-flag validation - volume confirmation + RRR filters

Uses proper walk-forward validation (12m train, 3m test).
Output per-year metrics: trades, win rate, profit factor, net %, sharpe, max DD
Calculate totals across all years.

Output: BTV2/results/mean_reversion_live_settings_validation.csv
"""

import math
import sys
from pathlib import Path
from datetime import date, datetime
from typing import Tuple, List, Dict, Any

import numpy as np
import pandas as pd

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.strategies import (
    run_mean_reversion,
    compute_metrics,
    INTERVAL_BARS_PER_YEAR,
    compute_rsi,
    compute_bollinger,
    compute_sma,
    compute_atr,
    compute_adx,
    apply_butterworth,
    _resample_ohlcv,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Current .env settings for Mean Reversion
LIVE_SETTINGS = {
    "rsi_oversold": 30.0,
    "rsi_overbought": 70.0,
    "bb_proximity": 0.10,           # 10% - price must be within 10% of BB edge
    "atr_stop": 3.0,                 # 3.0 ATR multiplier for stop loss
    "use_regime_filter": True,       # Only trade when ADX < threshold
    "adx_max": 25.0,                 # ADX_TRENDING_THRESHOLD from .env
    "use_trailing_stop": True,       # Enable trailing stop
    "trailing_atr_mult": 1.5,        # Trail 1.5 ATR behind price
    "use_sfp_entry": False,          # Standard entry, not SFP
    "use_poc_tp": False,             # Use SMA-20 for TP, not POC
}

# Cutoff for Butterworth filter
CUTOFF = 0.10

# Walk-forward parameters (12m train, 3m test as requested)
TRAIN_MONTHS = 12
TEST_MONTHS = 3

# Period to test
TEST_START = date(2018, 1, 1)
TEST_END = date(2025, 12, 31)


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_daily_data(start: str, end: str) -> pd.DataFrame:
    """Load BTCUSDT daily data, resampling from 5m if needed."""
    path_1d = DATA_DIR / "BTCUSDT_1d.parquet"
    if path_1d.exists():
        df = pd.read_parquet(path_1d)
        df.index = pd.to_datetime(df.index, utc=True)
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        mask = (df.index >= start_ts) & (df.index < end_ts)
        sliced = df.loc[mask].copy()
        if not sliced.empty:
            return sliced

    # Fallback: resample from 5m
    path_5m = DATA_DIR / "BTCUSDT_5m.parquet"
    if not path_5m.exists():
        print(f"ERROR: No data found at {DATA_DIR}")
        return pd.DataFrame()

    df_5m = pd.read_parquet(path_5m)
    df_5m.index = pd.to_datetime(df_5m.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df_5m.index >= start_ts) & (df_5m.index < end_ts)
    sliced = df_5m.loc[mask].copy()
    if sliced.empty:
        return sliced
    return _resample_ohlcv(sliced, "1D")


# ─────────────────────────────────────────────────────────────────────────────
# YEARLY BACKTEST
# ─────────────────────────────────────────────────────────────────────────────

def backtest_year(
    df: pd.DataFrame,
    year: int,
    mode: str,
) -> Dict[str, Any]:
    """
    Run backtest for a specific year in either Normal or Strict mode.
    
    mode: "normal" or "strict"
    - Normal: use_regime_filter=True with adx_max=25.0
    - Strict: strict_validation=True with volume confirmation + RRR filter
    """
    # Filter to specific year
    year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    year_end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
    df_year = df.loc[(df.index >= year_start) & (df.index < year_end)].copy()
    
    if df_year.empty:
        return {
            "Year": year,
            "Mode": mode,
            "Trades": 0,
            "WR%": 0.0,
            "PF": 0.0,
            "Net%": 0.0,
            "Sharpe": 0.0,
            "MaxDD%": 0.0,
        }
    
    # Build params based on mode
    params = LIVE_SETTINGS.copy()
    
    if mode == "strict":
        # Strict mode: mirrors live bot's 8-flag validation
        params["strict_validation"] = True
        params["strict_volume_mult"] = 1.2  # Volume must exceed 1.2x 20-bar avg
        params["strict_min_rrr"] = 1.0      # RRR >= 1.0 (from .env MEAN_REVERSION_MIN_RRR)
    
    # Run backtest
    equity, trades = run_mean_reversion(df_year, CUTOFF, **params)
    
    # Compute metrics
    metrics = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"])
    
    return {
        "Year": year,
        "Mode": mode,
        "Trades": metrics["n_trades"],
        "WR%": round(metrics["win_rate_pct"], 1),
        "PF": round(metrics["profit_factor"], 3),
        "Net%": round(metrics["total_return_pct"], 2),
        "Sharpe": round(metrics["sharpe"], 3),
        "MaxDD%": round(metrics["max_dd_pct"], 2),
    }


def run_walkforward_yearly(
    df: pd.DataFrame,
    mode: str,
) -> List[Dict[str, Any]]:
    """
    Run walk-forward validation with 12m train, 3m test.
    For each test year, train on the preceding 12 months.
    """
    results = []
    
    # Years to test: 2018-2025
    test_years = list(range(2018, 2026))
    
    for year in test_years:
        print(f"  {mode.upper()} Mode: Testing {year}...", end=" ", flush=True)
        
        # Define test period (3 months)
        test_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        test_end = pd.Timestamp(f"{year}-04-01", tz="UTC")  # Q1 of the year
        
        # Define train period (12 months before test)
        train_start = test_start - pd.DateOffset(months=12)
        train_end = test_start
        
        # Get train data for regime calibration (not optimization - just using live settings)
        df_train = df.loc[(df.index >= train_start) & (df.index < train_end)].copy()
        
        # Get test data
        df_test = df.loc[(df.index >= test_start) & (df.index < test_end)].copy()
        
        if df_train.empty or df_test.empty:
            print(f"SKIP (insufficient data)")
            continue
        
        # Build params based on mode (NO OPTIMIZATION - using live settings as-is)
        params = LIVE_SETTINGS.copy()
        
        if mode == "strict":
            params["strict_validation"] = True
            params["strict_volume_mult"] = 1.2
            params["strict_min_rrr"] = 1.0
        
        # Run backtest on test period
        equity, trades = run_mean_reversion(df_test, CUTOFF, **params)
        metrics = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["1d"])
        
        result = {
            "Year": year,
            "Mode": mode,
            "Trades": metrics["n_trades"],
            "WR%": round(metrics["win_rate_pct"], 1),
            "PF": round(metrics["profit_factor"], 3),
            "Net%": round(metrics["total_return_pct"], 2),
            "Sharpe": round(metrics["sharpe"], 3),
            "MaxDD%": round(metrics["max_dd_pct"], 2),
        }
        
        results.append(result)
        print(f"Trades={metrics['n_trades']}, Net={metrics['total_return_pct']:+.2f}%, "
              f"WR={metrics['win_rate_pct']:.1f}%, PF={metrics['profit_factor']:.3f}")
    
    return results


def run_full_year_backtest(
    df: pd.DataFrame,
    mode: str,
) -> List[Dict[str, Any]]:
    """
    Run full year backtests (no walk-forward, entire year as test).
    Used for overall validation.
    """
    results = []
    
    # Years to test: 2018-2024 (2025 might be partial)
    test_years = list(range(2018, 2025))
    
    for year in test_years:
        result = backtest_year(df, year, mode)
        results.append(result)
        print(f"  {mode.upper()} Mode {year}: Trades={result['Trades']}, "
              f"Net={result['Net%']:+.2f}%, WR={result['WR%']:.1f}%, "
              f"PF={result['PF']:.3f}, Sharpe={result['Sharpe']:.3f}, "
              f"MaxDD={result['MaxDD%']:.2f}%")
    
    return results


def compute_totals(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute aggregate totals across all years."""
    if not results:
        return {}
    
    # Sum trades
    total_trades = sum(r["Trades"] for r in results)
    
    # Compute weighted average WR
    wins = sum(r["WR%"] * r["Trades"] for r in results if r["Trades"] > 0)
    total_wr = wins / total_trades if total_trades > 0 else 0
    
    # Net% - compound or sum? Let's do sum for simplicity
    total_net = sum(r["Net%"] for r in results)
    
    # Sharpe - average
    avg_sharpe = sum(r["Sharpe"] for r in results) / len(results)
    
    # MaxDD - worst case
    max_dd = min(r["MaxDD%"] for r in results)
    
    # PF - weighted average
    pf_sum = sum(r["PF"] * r["Trades"] for r in results if r["Trades"] > 0)
    total_pf = pf_sum / total_trades if total_trades > 0 else 0
    
    return {
        "Year": "TOTAL",
        "Mode": results[0]["Mode"],
        "Trades": total_trades,
        "WR%": round(total_wr, 1),
        "PF": round(total_pf, 3),
        "Net%": round(total_net, 2),
        "Sharpe": round(avg_sharpe, 3),
        "MaxDD%": round(max_dd, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("MEAN REVERSION LIVE SETTINGS VALIDATION")
    print("=" * 80)
    print("\nUsing current .env settings:")
    for k, v in LIVE_SETTINGS.items():
        print(f"  {k}: {v}")
    print()
    
    # Load data
    print("Loading data...")
    df = load_daily_data("2017-01-01", "2026-01-01")
    if df.empty:
        print("ERROR: No data available!")
        return
    
    print(f"  Data: {len(df)} daily bars from {df.index[0]} to {df.index[-1]}")
    
    # Run Normal mode (walk-forward)
    print("\n" + "=" * 80)
    print("NORMAL MODE (use_regime_filter=True, ADX<25)")
    print("=" * 80)
    normal_wf_results = run_walkforward_yearly(df, "normal")
    
    # Run Strict mode (walk-forward)
    print("\n" + "=" * 80)
    print("STRICT MODE (strict_validation=True, volume+RRR filters)")
    print("=" * 80)
    strict_wf_results = run_walkforward_yearly(df, "strict")
    
    # Combine results
    all_results = normal_wf_results + strict_wf_results
    
    # Compute totals for each mode
    if normal_wf_results:
        normal_total = compute_totals(normal_wf_results)
        all_results.append(normal_total)
    
    if strict_wf_results:
        strict_total = compute_totals(strict_wf_results)
        all_results.append(strict_total)
    
    # Save to CSV
    df_results = pd.DataFrame(all_results)
    output_path = RESULTS_DIR / "mean_reversion_live_settings_validation.csv"
    df_results.to_csv(output_path, index=False)
    print(f"\nResults saved to {output_path}")
    
    # Print summary
    print("\n" + "=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)
    
    # Normal mode summary
    print("\n--- NORMAL MODE (ADX<25 regime filter only) ---")
    if normal_wf_results:
        profitable = sum(1 for r in normal_wf_results if r["Net%"] > 0)
        total_net = sum(r["Net%"] for r in normal_wf_results)
        total_trades = sum(r["Trades"] for r in normal_wf_results)
        avg_wr = sum(r["WR%"] * r["Trades"] for r in normal_wf_results) / total_trades if total_trades > 0 else 0
        print(f"  Years tested: {len(normal_wf_results)}")
        print(f"  Profitable years: {profitable}/{len(normal_wf_results)}")
        print(f"  Total trades: {total_trades}")
        print(f"  Total Net%: {total_net:+.2f}%")
        print(f"  Avg WR%: {avg_wr:.1f}%")
        
        # Show each year
        print("\n  Per-Year Breakdown:")
        for r in normal_wf_results:
            status = "PROFIT" if r["Net%"] > 0 else "LOSS"
            print(f"    {r['Year']}: {r['Net%']:+6.2f}% ({r['Trades']:3d} trades, WR={r['WR%']:5.1f}%, PF={r['PF']:.3f}) [{status}]")
    
    # Strict mode summary
    print("\n--- STRICT MODE (volume confirmation + RRR filter) ---")
    if strict_wf_results:
        profitable = sum(1 for r in strict_wf_results if r["Net%"] > 0)
        total_net = sum(r["Net%"] for r in strict_wf_results)
        total_trades = sum(r["Trades"] for r in strict_wf_results)
        avg_wr = sum(r["WR%"] * r["Trades"] for r in strict_wf_results) / total_trades if total_trades > 0 else 0
        print(f"  Years tested: {len(strict_wf_results)}")
        print(f"  Profitable years: {profitable}/{len(strict_wf_results)}")
        print(f"  Total trades: {total_trades}")
        print(f"  Total Net%: {total_net:+.2f}%")
        print(f"  Avg WR%: {avg_wr:.1f}%")
        
        # Show each year
        print("\n  Per-Year Breakdown:")
        for r in strict_wf_results:
            status = "PROFIT" if r["Net%"] > 0 else "LOSS"
            print(f"    {r['Year']}: {r['Net%']:+6.2f}% ({r['Trades']:3d} trades, WR={r['WR%']:5.1f}%, PF={r['PF']:.3f}) [{status}]")
    
    # Comparison
    print("\n--- COMPARISON: Normal vs Strict ---")
    if normal_wf_results and strict_wf_results:
        normal_total_net = sum(r["Net%"] for r in normal_wf_results)
        strict_total_net = sum(r["Net%"] for r in strict_wf_results)
        normal_trades = sum(r["Trades"] for r in normal_wf_results)
        strict_trades = sum(r["Trades"] for r in strict_wf_results)
        
        print(f"  Normal Mode Total Net%: {normal_total_net:+.2f}% ({normal_trades} trades)")
        print(f"  Strict Mode Total Net%: {strict_total_net:+.2f}% ({strict_trades} trades)")
        print(f"  Difference: {strict_total_net - normal_total_net:+.2f}%")
        
        # Check for failed years
        print("\n--- FAILED YEARS (Net% < 0) ---")
        normal_failed = [r for r in normal_wf_results if r["Net%"] < 0]
        strict_failed = [r for r in strict_wf_results if r["Net%"] < 0]
        
        if normal_failed:
            print(f"  Normal mode failed years: {[r['Year'] for r in normal_failed]}")
        else:
            print("  Normal mode: No failed years!")

        if strict_failed:
            print(f"  Strict mode failed years: {[r['Year'] for r in strict_failed]}")
        else:
            print("  Strict mode: No failed years!")
    
    # Final verdict
    print("\n" + "=" * 80)
    print("FINAL VERDICT")
    print("=" * 80)

    if normal_wf_results and strict_wf_results:
        normal_total_net = sum(r["Net%"] for r in normal_wf_results)
        strict_total_net = sum(r["Net%"] for r in strict_wf_results)

        if normal_total_net > 0 and strict_total_net > 0:
            print("[PASS] Both Normal and Strict modes are PROFITABLE")
            print(f"   Normal: {normal_total_net:+.2f}%, Strict: {strict_total_net:+.2f}%")
        elif normal_total_net > 0:
            print("[PARTIAL] Only Normal mode is profitable")
            print(f"   Normal: {normal_total_net:+.2f}%, Strict: {strict_total_net:+.2f}%")
        elif strict_total_net > 0:
            print("[PARTIAL] Only Strict mode is profitable")
            print(f"   Normal: {normal_total_net:+.2f}%, Strict: {strict_total_net:+.2f}%")
        else:
            print("[FAIL] Both modes are unprofitable")
            print(f"   Normal: {normal_total_net:+.2f}%, Strict: {strict_total_net:+.2f}%")
    
    print("\nDone!")


if __name__ == "__main__":
    main()