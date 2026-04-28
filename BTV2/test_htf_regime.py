"""
HTF Regime Detection Test Script
==================================

Test the new Higher Timeframe (HTF) regime detection method:
- Compute regime on 1h or 4h timeframe
- Map back to 5m bars (forward fill)
- Should be much less noisy

Metrics:
- Flips per month (should be 1-5, not 1000+)
- Accuracy vs known years:
  - 2018: BEAR
  - 2019: RANGING
  - 2020: BULL
  - 2021: BULL then BEAR (May)
  - 2022: BEAR
  - 2023: RANGING
  - 2024: BULL
"""

import pandas as pd
import numpy as np
from pathlib import Path
from collections import defaultdict
import warnings

# Add project root to path
import sys
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from regime_detector import RegimeDetector, MarketRegime


# Path configuration
DATA_DIR = project_root.parent / "trading_bot_v2" / "backtesting" / "data"
RESULTS_DIR = project_root / "results"


def load_btc_5m_data() -> pd.DataFrame:
    """Load BTC 5m data from 2018-2025."""
    print("Loading BTC 5m data...")
    
    # Load all yearly CSV files
    csv_files = [
        DATA_DIR / "BTC-USDC_5m_2018.csv",
        DATA_DIR / "BTC-USDC_5m_2019.csv",
        DATA_DIR / "BTC-USDC_5m_2020.csv",
        DATA_DIR / "BTC-USDC_5m_2021.csv",
        DATA_DIR / "BTC-USDC_5m_2022.csv",
        # 2023-2025 in main file
    ]
    
    dfs = []
    for csv_file in csv_files:
        if csv_file.exists():
            df = pd.read_csv(csv_file, parse_dates=["timestamp"], index_col="timestamp")
            # Normalize column names to PascalCase
            df.columns = [c.title() for c in df.columns]
            # Convert to UTC and make tz-naive for consistency
            if df.index.tz is not None:
                df.index = df.index.tz_convert('UTC').tz_localize(None)
            dfs.append(df)
            print(f"  Loaded {csv_file.name}: {len(df):,} rows")
    
    # Load 2023-2025 from main file
    main_file = DATA_DIR / "BTC-USDC_5m.csv"
    if main_file.exists():
        df_main = pd.read_csv(main_file, parse_dates=["timestamp"], index_col="timestamp")
        # Normalize column names to PascalCase
        df_main.columns = [c.title() for c in df_main.columns]
        # Convert to UTC and make tz-naive for consistency
        if df_main.index.tz is not None:
            df_main.index = df_main.index.tz_convert('UTC').tz_localize(None)
        dfs.append(df_main)
        print(f"  Loaded {main_file.name}: {len(df_main):,} rows")
    
    # Combine all dataframes
    combined = pd.concat(dfs)
    combined = combined[~combined.index.duplicated(keep="first")]
    
    # Ensure tz-naive and sort
    if combined.index.tz is not None:
        combined.index = combined.index.tz_localize(None)
    combined = combined.sort_index()
    
    print(f"Total data: {len(combined):,} rows ({combined.index.min()} to {combined.index.max()})")
    return combined


def count_regime_flips(series: pd.Series) -> int:
    """Count number of regime changes (flips)."""
    if len(series) < 2:
        return 0
    flips = (series != series.shift(1)).sum()
    return int(flips)


def get_monthly_flips(series: pd.Series) -> dict[str, int]:
    """Get flips per month."""
    df = series.to_frame("regime")
    df["flip"] = (df["regime"] != df["regime"].shift(1)).astype(int)
    df["yearmonth"] = df.index.to_period("M")
    
    monthly = df.groupby("yearmonth")["flip"].sum()
    return {str(ym): int(flips) for ym, flips in monthly.items()}


def get_dominant_regime(series: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> MarketRegime:
    """Get the most common regime in a date range."""
    mask = (series.index >= start) & (series.index <= end)
    subset = series[mask]
    if len(subset) == 0:
        return MarketRegime.RANGING
    counts = subset.value_counts()
    return counts.index[0]


def get_yearly_accuracy(regimes: pd.Series) -> dict[int, dict]:
    """Get accuracy metrics for each year."""
    known_regimes = {
        2018: MarketRegime.BEAR_STRONG,
        2019: MarketRegime.RANGING,
        2020: MarketRegime.BULL_STRONG,
        2021: MarketRegime.BULL_STRONG,  # First half bull, then bear in May
        2022: MarketRegime.BEAR_STRONG,
        2023: MarketRegime.RANGING,
        2024: MarketRegime.BULL_STRONG,
    }
    
    results = {}
    for year, expected in known_regimes.items():
        start = pd.Timestamp(f"{year}-01-01")
        end = pd.Timestamp(f"{year}-12-31 23:59:59")
        
        if year == 2021:
            # For 2021, check both halves
            mid = pd.Timestamp("2021-05-01")
            h1_expected = MarketRegime.BULL_STRONG
            h2_expected = MarketRegime.BEAR_STRONG
            
            h1_actual = get_dominant_regime(regimes, start, mid - pd.Timedelta(minutes=5))
            h2_actual = get_dominant_regime(regimes, mid, end)
            
            h1_correct = h1_actual == h1_expected
            h2_correct = h2_actual == h2_expected
            
            results[year] = {
                "expected": f"BULL(Jan-Apr), BEAR(May-Dec)",
                "h1_actual": h1_actual.value,
                "h2_actual": h2_actual.value,
                "h1_correct": h1_correct,
                "h2_correct": h2_correct,
                "accuracy": (h1_correct + h2_correct) / 2
            }
        else:
            actual = get_dominant_regime(regimes, start, end)
            correct = actual == expected
            
            results[year] = {
                "expected": expected.value,
                "actual": actual.value,
                "correct": correct,
                "accuracy": 1.0 if correct else 0.0
            }
    
    return results


def test_htf_regime_detection():
    """Run the HTF regime detection test."""
    print("\n" + "="*70)
    print("HTF REGIME DETECTION TEST")
    print("="*70 + "\n")
    
    # Load data
    df = load_btc_5m_data()
    
    # ========================================
    # Method 1: Original get_regimes() on 5m
    # ========================================
    print("\n[1/3] Computing regimes on 5m (original method)...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        detector_5m = RegimeDetector(df, method="combined", timeframe="5m")
        regimes_5m = detector_5m.get_regimes()
    flips_5m = count_regime_flips(regimes_5m)
    print(f"  Total flips: {flips_5m:,}")
    
    # ========================================
    # Method 2: HTF 1h regime
    # ========================================
    print("\n[2/3] Computing regimes from 1h HTF...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        htf_1h = detector_5m.get_htf_regime_series("1h")
    flips_1h = count_regime_flips(htf_1h)
    print(f"  Total flips: {flips_1h:,}")
    
    # ========================================
    # Method 3: HTF 4h regime
    # ========================================
    print("\n[3/3] Computing regimes from 4h HTF...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        htf_4h = detector_5m.get_htf_regime_series("4h")
    flips_4h = count_regime_flips(htf_4h)
    print(f"  Total flips: {flips_4h:,}")
    
    # ========================================
    # Flip Comparison
    # ========================================
    print("\n" + "-"*70)
    print("FLIP COMPARISON")
    print("-"*70)
    
    print(f"\nOriginal (5m):   {flips_5m:>10,} flips")
    print(f"HTF (1h):        {flips_1h:>10,} flips")
    print(f"HTF (4h):        {flips_4h:>10,} flips")
    
    reduction_1h = (1 - flips_1h/flips_5m) * 100 if flips_5m > 0 else 0
    reduction_4h = (1 - flips_4h/flips_5m) * 100 if flips_5m > 0 else 0
    
    print(f"\nReduction vs 5m:")
    print(f"  1h: {reduction_1h:.1f}%")
    print(f"  4h: {reduction_4h:.1f}%")
    
    # Monthly flips
    monthly_5m = get_monthly_flips(regimes_5m)
    monthly_1h = get_monthly_flips(htf_1h)
    monthly_4h = get_monthly_flips(htf_4h)
    
    # Average flips per month
    avg_flips_5m = np.mean(list(monthly_5m.values()))
    avg_flips_1h = np.mean(list(monthly_1h.values()))
    avg_flips_4h = np.mean(list(monthly_4h.values()))
    
    print(f"\nAverage flips per month:")
    print(f"  Original (5m): {avg_flips_5m:.1f}")
    print(f"  HTF (1h):      {avg_flips_1h:.1f}")
    print(f"  HTF (4h):      {avg_flips_4h:.1f}")
    
    # ========================================
    # Yearly Accuracy
    # ========================================
    print("\n" + "-"*70)
    print("YEARLY ACCURACY TEST")
    print("-"*70)
    
    # Test each method
    accuracy_results = {}
    for name, series in [("5m", regimes_5m), ("1h", htf_1h), ("4h", htf_4h)]:
        yearly = get_yearly_accuracy(series)
        correct = sum(y["correct"] if "correct" in y else (y["h1_correct"] and y["h2_correct"]) 
                    for y in yearly.values())
        total = len(yearly)
        accuracy_results[name] = {
            "yearly": yearly,
            "total_correct": correct,
            "total": total,
            "accuracy": correct / total
        }
    
    for method in ["5m", "1h", "4h"]:
        print(f"\n{method.upper()} Method:")
        res = accuracy_results[method]
        for year, data in res["yearly"].items():
            if "h1_actual" in data:
                print(f"  {year}: H1={data['h1_actual']} (exp={data['expected'].split(',')[0]}) "
                      f"H2={data['h2_actual']} (exp={data['expected'].split(',')[1]}) "
                      f"-> {'PASS' if data['h1_correct'] and data['h2_correct'] else 'FAIL'}")
            else:
                print(f"  {year}: {data['actual']} (expected={data['expected']}) "
                      f"-> {'PASS' if data['correct'] else 'FAIL'}")
        print(f"  Accuracy: {res['total_correct']}/{res['total']} = {res['accuracy']*100:.1f}%")
    
    # ========================================
    # Save Results
    # ========================================
    print("\n" + "-"*70)
    print("SAVING RESULTS")
    print("-"*70)
    
    # Create summary CSV
    summary_data = []
    for year in range(2018, 2025):
        data_5m = accuracy_results["5m"]["yearly"].get(year, {})
        data_1h = accuracy_results["1h"]["yearly"].get(year, {})
        data_4h = accuracy_results["4h"]["yearly"].get(year, {})
        
        summary_data.append({
            "year": year,
            "expected": data_5m.get("expected", "N/A"),
            "5m_actual": data_5m.get("actual", data_5m.get("h1_actual", "N/A")),
            "5m_correct": data_5m.get("correct", data_5m.get("h1_correct", False)),
            "1h_actual": data_1h.get("actual", data_1h.get("h1_actual", "N/A")),
            "1h_correct": data_1h.get("correct", data_1h.get("h1_correct", False)),
            "4h_actual": data_4h.get("actual", data_4h.get("h1_actual", "N/A")),
            "4h_correct": data_4h.get("correct", data_4h.get("h1_correct", False)),
        })
    
    summary_df = pd.DataFrame(summary_data)
    csv_path = RESULTS_DIR / "htf_regime_accuracy.csv"
    summary_df.to_csv(csv_path, index=False)
    print(f"\nSaved: {csv_path}")
    
    # Print final metrics
    print("\n" + "="*70)
    print("FINAL RESULTS")
    print("="*70)
    print(f"\nTotal Flips:")
    print(f"  Original 5m: {flips_5m:,}")
    print(f"  HTF 1h:      {flips_1h:,} ({reduction_1h:.1f}% reduction)")
    print(f"  HTF 4h:      {flips_4h:,} ({reduction_4h:.1f}% reduction)")
    print(f"\nAvg Flips/Month:")
    print(f"  Original 5m: {avg_flips_5m:.1f} (should be 1-5)")
    print(f"  HTF 1h:       {avg_flips_1h:.1f} (should be 1-5)")
    print(f"  HTF 4h:       {avg_flips_4h:.1f} (should be 1-5)")
    print(f"\nYearly Accuracy:")
    print(f"  Original 5m:  {accuracy_results['5m']['accuracy']*100:.1f}%")
    print(f"  HTF 1h:       {accuracy_results['1h']['accuracy']*100:.1f}%")
    print(f"  HTF 4h:       {accuracy_results['4h']['accuracy']*100:.1f}%")
    
    return accuracy_results


if __name__ == "__main__":
    results = test_htf_regime_detection()