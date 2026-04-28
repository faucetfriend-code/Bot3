"""
Test regime detection with hysteresis.

Tests different hysteresis configurations to measure:
1. Flips per month reduction
2. Regime stability
"""

import pandas as pd
import numpy as np
from pathlib import Path

# Add project root to path
import sys
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from BTV2.regime_detector import RegimeDetector, MarketRegime


def load_data() -> pd.DataFrame:
    """Load the main 5m dataset."""
    data_path = project_root / "trading_bot_v2" / "backtesting" / "data" / "BTC-USDC_5m_all.csv"
    df = pd.read_csv(data_path, parse_dates=["timestamp"])
    df.set_index("timestamp", inplace=True)
    
    # Fix column names to match expected case
    df.columns = [col.capitalize() for col in df.columns]
    
    # Use last 2 years for speed (2022-2023)
    df = df[df.index >= "2022-01-01"]
    
    print(f"Loaded {len(df)} rows from {df.index[0]} to {df.index[-1]}")
    return df


def count_regime_flips(regimes: pd.Series) -> int:
    """Count the number of regime changes (flips)."""
    flips = 0
    prev_regime = regimes.iloc[0]
    for regime in regimes.iloc[1:]:
        if regime != prev_regime:
            flips += 1
            prev_regime = regime
    return flips


def get_year_month(index: pd.DatetimeIndex) -> str:
    """Get YYYY-MM string for grouping."""
    return index.strftime("%Y-%m")


def analyze_flips_per_month(regimes: pd.Series) -> pd.DataFrame:
    """Analyze flips per month."""
    months = regimes.index.to_series().dt.to_period("M")
    flips_per_month = []
    
    for month in months.unique():
        month_regimes = regimes[months == month]
        flips = count_regime_flips(month_regimes)
        flips_per_month.append({
            "month": str(month),
            "flips": flips,
            "bars": len(month_regimes),
        })
    
    return pd.DataFrame(flips_per_month)


def run_hysteresis_test(df: pd.DataFrame, hysteresis: int, label: str) -> dict:
    """Run regime detection with specific hysteresis setting."""
    detector = RegimeDetector(df, method="combined", timeframe="5m")
    
    # Get regimes with hysteresis
    regimes = detector.get_regimes(hysteresis=hysteresis)
    
    # Basic stats
    flips = count_regime_flips(regimes)
    total_bars = len(regimes)
    
    # Time range
    start_date = df.index[0]
    end_date = df.index[-1]
    num_months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month) + 1
    
    flips_per_month = flips / num_months if num_months > 0 else flips
    
    # Regime distribution
    dist = regimes.value_counts(normalize=True) * 100
    
    result = {
        "config": label,
        "hysteresis": hysteresis,
        "total_flips": flips,
        "flips_per_month": round(flips_per_month, 2),
        "total_bars": total_bars,
        "months": num_months,
        "bull_strong_pct": round(dist.get(MarketRegime.BULL_STRONG, 0), 1),
        "bull_weak_pct": round(dist.get(MarketRegime.BULL_WEAK, 0), 1),
        "ranging_pct": round(dist.get(MarketRegime.RANGING, 0), 1),
        "bear_weak_pct": round(dist.get(MarketRegime.BEAR_WEAK, 0), 1),
        "bear_strong_pct": round(dist.get(MarketRegime.BEAR_STRONG, 0), 1),
    }
    
    print(f"  {label}: {flips} flips, {flips_per_month:.2f} flips/month")
    
    return result


def run_htf_hysteresis_test(df: pd.DataFrame, htf: str, hysteresis: int, label: str) -> dict:
    """Run HTF regime detection with hysteresis."""
    detector = RegimeDetector(df, method="combined", timeframe="5m")
    
    # Get HTF regime series
    regimes = detector.get_htf_regime_series(htf=htf, hysteresis=hysteresis)
    
    # Basic stats
    flips = count_regime_flips(regimes)
    total_bars = len(regimes)
    
    # Time range
    start_date = df.index[0]
    end_date = df.index[-1]
    num_months = (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month) + 1
    
    flips_per_month = flips / num_months if num_months > 0 else flips
    
    # Regime distribution
    dist = regimes.value_counts(normalize=True) * 100
    
    result = {
        "config": label,
        "htf": htf,
        "hysteresis": hysteresis,
        "total_flips": flips,
        "flips_per_month": round(flips_per_month, 2),
        "total_bars": total_bars,
        "months": num_months,
        "bull_strong_pct": round(dist.get(MarketRegime.BULL_STRONG, 0), 1),
        "bull_weak_pct": round(dist.get(MarketRegime.BULL_WEAK, 0), 1),
        "ranging_pct": round(dist.get(MarketRegime.RANGING, 0), 1),
        "bear_weak_pct": round(dist.get(MarketRegime.BEAR_WEAK, 0), 1),
        "bear_strong_pct": round(dist.get(MarketRegime.BEAR_STRONG, 0), 1),
    }
    
    print(f"  {label}: {flips} flips, {flips_per_month:.2f} flips/month")
    
    return result


def main():
    print("=" * 60)
    print("REGIME HYSTERESIS TEST")
    print("=" * 60)
    
    # Load data
    print("\nLoading data...")
    df = load_data()
    
    results = []
    
    # ─────────────────────────────────────────────────────────────
    # TEST 1: 5m with different hysteresis values
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("TEST 1: 5m with hysteresis")
    print("=" * 60)
    
    # Baseline (no hysteresis)
    results.append(run_hysteresis_test(df, hysteresis=0, label="5m_no_hysteresis"))
    
    # 30 min (6 bars on 5m)
    results.append(run_hysteresis_test(df, hysteresis=6, label="5m_hysteresis_30min"))
    
    # 60 min (12 bars on 5m)
    results.append(run_hysteresis_test(df, hysteresis=12, label="5m_hysteresis_60min"))
    
    # ─────────────────────────────────────────────────────────────
    # TEST 2: HTF with hysteresis
    # ─────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("TEST 2: HTF with hysteresis")
    print("=" * 60)
    
    # 1h with hysteresis=3 (3 hours)
    results.append(run_htf_hysteresis_test(df, htf="1h", hysteresis=3, label="1h_hysteresis_3h"))
    
    # 4h with hysteresis=3 (12 hours)
    results.append(run_htf_hysteresis_test(df, htf="4h", hysteresis=3, label="4h_hysteresis_12h"))
    
    # ─────────────────────────────────────────────────────────────
    # Save results
    # ─────────────────────────────────────────────────────────────
    results_df = pd.DataFrame(results)
    results_df = results_df[[
        "config", "hysteresis", "htf", "total_flips", "flips_per_month",
        "months", "bull_strong_pct", "bull_weak_pct", "ranging_pct", "bear_weak_pct", "bear_strong_pct"
    ]]
    
    output_path = project_root / "BTV2" / "results" / "regime_hysteresis_test.csv"
    results_df.to_csv(output_path, index=False)
    print(f"\nSaved results to {output_path}")
    
    # Print summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(results_df.to_string(index=False))
    
    # Analysis
    print("\n" + "=" * 60)
    print("ANALYSIS")
    print("=" * 60)
    baseline = results[0]["flips_per_month"]
    print(f"Baseline (no hysteresis): {baseline} flips/month")
    print("\nWith 5m hysteresis:")
    print(f"  30min (hysteresis=6): {results[1]['flips_per_month']} flips/month")
    print(f"  60min (hysteresis=12): {results[2]['flips_per_month']} flips/month")
    print("\nWith HTF + hysteresis:")
    print(f"  1h (hysteresis=3): {results[3]['flips_per_month']} flips/month")
    print(f"  4h (hysteresis=3): {results[4]['flips_per_month']} flips/month")


if __name__ == "__main__":
    main()