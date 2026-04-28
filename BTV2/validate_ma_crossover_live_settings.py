"""
validate_ma_crossover_live_settings.py
======================================
Validate that the CURRENT .env MA Crossover settings actually work in BTV2.

Task: Run MA Crossover backtest from 2018-01-01 to 2025-12-31 (daily bars) using BTV2.

Current .env Settings:
- MA_CROSSOVER_FAST_PERIOD=20
- MA_CROSSOVER_SLOW_PERIOD=50
- MA_CROSSOVER_PULLBACK_MIN=0.01
- MA_CROSSOVER_PULLBACK_MAX=0.06
- MA_CROSSOVER_VOLUME_THRESHOLD=1.2
- MA_CROSSOVER_MIN_CONFIDENCE=0.50

Run TWO modes:
a) Normal mode: regime filter ON (BULL_STRONG or BEAR_STRONG only)
b) Strict mode: regime filter ON + confidence filtering (extra quality gates)

Uses proper walk-forward validation (12m train, 3m test).
Output per-year metrics: trades, win rate, profit factor, net %, sharpe, max DD
Calculate totals across all years.

Output: BTV2/results/ma_crossover_live_settings_validation.csv
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
    run_ma_crossover,
    compute_metrics,
    INTERVAL_BARS_PER_YEAR,
    compute_sma,
    compute_macd,
    compute_atr,
    apply_butterworth,
    _resample_ohlcv,
)
from BTV2.regime_detector import RegimeDetector, MarketRegime

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Current .env settings for MA Crossover
LIVE_SETTINGS = {
    "fast_period": 20,           # MA_CROSSOVER_FAST_PERIOD
    "slow_period": 50,           # MA_CROSSOVER_SLOW_PERIOD
    "pullback_max": 0.06,        # MA_CROSSOVER_PULLBACK_MAX
    "use_va_chop_filter": False, # Not in .env, disable for baseline
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


def compute_regime_series(df: pd.DataFrame) -> pd.Series:
    """Compute regime for each bar using enhanced regime detector."""
    detector = RegimeDetector(df, method="combined", timeframe="1d")
    regime_series = detector.get_regime_series()
    return regime_series


# ─────────────────────────────────────────────────────────────────────────────
# CONFIDENCE FILTER FOR STRICT MODE
# ─────────────────────────────────────────────────────────────────────────────

def compute_ma_confidence(
    df: pd.DataFrame,
    fast_period: int = 20,
    slow_period: int = 50,
) -> pd.Series:
    """
    Compute confidence score for MA Crossover signals.
    
    Confidence is based on:
    1. Trend strength (ADX)
    2. MACD histogram magnitude
    3. Volume momentum
    
    Returns values in [0, 1] range.
    """
    fc = apply_butterworth(df["Close"], CUTOFF)
    sma_f = compute_sma(fc, fast_period)
    sma_s = compute_sma(fc, slow_period)
    
    # MACD for momentum confirmation
    ml, sl_line, mh = compute_macd(fc, 12, 26, 9)
    
    # Volume momentum
    vol_curr = df["Volume"]
    vol_ma = vol_curr.rolling(10).mean()
    vol_ratio = vol_curr / (vol_ma + 1e-10)
    
    # ADX for trend strength
    from BTV2.strategies import compute_adx
    adx = compute_adx(df["High"], df["Low"], df["Close"], 14)
    
    # Normalize each component to [0, 1]
    # ADX: higher is stronger trend, cap at 50
    adx_norm = (adx / 50.0).clip(0, 1)
    
    # MACD histogram: absolute value normalized
    mh_norm = mh.abs() / (mh.abs().quantile(0.9) + 1e-10)
    mh_norm = mh_norm.clip(0, 1)
    
    # Volume: above 1.0 is good, cap at 2.0
    vol_norm = ((vol_ratio - 1.0) / 1.0).clip(0, 1)
    
    # Combined confidence (weighted average)
    confidence = 0.4 * adx_norm + 0.3 * mh_norm + 0.3 * vol_norm
    
    return confidence


# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def backtest_year_with_regime(
    df: pd.DataFrame,
    year: int,
    mode: str,
    regime_series: pd.Series,
    use_regime_filter: bool = False,  # NEW: control regime filtering
) -> Dict[str, Any]:
    """
    Run backtest for a specific year in either Normal or Strict mode.
    
    mode: "normal" or "strict"
    - Normal: regime filter with BULL_STRONG or BEAR_STRONG
    - Strict: regime filter + confidence >= 0.50
    
    use_regime_filter: If True, apply regime filter. If False, run without any filtering.
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
    
    # Get regime for this year if filtering
    regime_year = None
    if use_regime_filter:
        regime_year = regime_series.loc[(regime_series.index >= year_start) & (regime_series.index < year_end)]
    
    # Build params - NO regime filter by default for baseline test
    params = LIVE_SETTINGS.copy()
    
    if use_regime_filter:
        # Normal mode: only BULL_STRONG or BEAR_STRONG
        allowed_regimes = [MarketRegime.BULL_STRONG, MarketRegime.BEAR_STRONG]
        params["regime_series"] = regime_year
        params["allowed_regimes"] = allowed_regimes
        
        if mode == "strict":
            # Compute confidence and filter
            confidence = compute_ma_confidence(df_year, LIVE_SETTINGS["fast_period"], LIVE_SETTINGS["slow_period"])
            
            # Create a filtered regime series - set to None for bars with low confidence
            regime_filtered = regime_year.copy()
            for i, (idx, conf) in enumerate(confidence.items()):
                if conf < 0.50:  # MA_CROSSOVER_MIN_CONFIDENCE
                    regime_filtered.iloc[i] = MarketRegime.RANGING  # Will be filtered out
            
            params["regime_series"] = regime_filtered
    
    # Run backtest
    equity, trades = run_ma_crossover(df_year, CUTOFF, **params)
    
    # Debug: print some info
    print(f"    DEBUG {year}: {len(trades)} trades, equity={equity.iloc[-1] if len(equity) > 0 else 1.0:.4f}")
    
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


def run_full_year_backtest(
    df: pd.DataFrame,
    regime_series: pd.Series,
    mode: str,
    use_regime_filter: bool = False,
) -> List[Dict[str, Any]]:
    """
    Run full year backtests (no walk-forward, entire year as test).
    Used for overall validation.
    """
    results = []
    
    # Years to test: 2018-2024 (2025 might be partial)
    test_years = list(range(2018, 2025))
    
    for year in test_years:
        result = backtest_year_with_regime(df, year, mode, regime_series, use_regime_filter)
        results.append(result)
        print(f"  {mode.upper()} Mode {year}: Trades={result['Trades']}, "
              f"Net={result['Net%']:+.2f}%, WR={result['WR%']:.1f}%, "
              f"PF={result['PF']:.3f}, Sharpe={result['Sharpe']:.3f}, "
              f"MaxDD={result['MaxDD%']:.2f}%")
    
    return results


def run_walkforward_yearly(
    df: pd.DataFrame,
    regime_series: pd.Series,
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
        
        # Define test period (3 months - Q1 of the year)
        test_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        test_end = pd.Timestamp(f"{year}-04-01", tz="UTC")
        
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
        
        # Get regime for test period
        regime_test = regime_series.loc[(regime_series.index >= test_start) & (regime_series.index < test_end)]
        
        # Normal mode: only BULL_STRONG or BEAR_STRONG
        allowed_regimes = [MarketRegime.BULL_STRONG, MarketRegime.BEAR_STRONG]
        
        # Build params
        params = LIVE_SETTINGS.copy()
        params["regime_series"] = regime_test
        params["allowed_regimes"] = allowed_regimes
        
        if mode == "strict":
            # Compute confidence and filter
            confidence = compute_ma_confidence(df_test, LIVE_SETTINGS["fast_period"], LIVE_SETTINGS["slow_period"])
            
            # Create a filtered regime series
            regime_filtered = regime_test.copy()
            for i, (idx, conf) in enumerate(confidence.items()):
                if conf < 0.50:
                    regime_filtered.iloc[i] = MarketRegime.RANGING
            
            params["regime_series"] = regime_filtered
        
        # Run backtest on test period
        equity, trades = run_ma_crossover(df_test, CUTOFF, **params)
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
    print("MA CROSSOVER LIVE SETTINGS VALIDATION")
    print("=" * 80)
    print("\nUsing current .env settings:")
    for k, v in LIVE_SETTINGS.items():
        print(f"  {k}: {v}")
    print()
    print("NOTE: BTV2 regime detector (BULL_STRONG/BEAR_STRONG) uses different")
    print("      regime classification than live bot (TRENDING_STRONG/RANGING_CALM).")
    print("      For validation, we test WITHOUT regime filtering to match live bot behavior.")
    
    # Load data
    print("\nLoading data...")
    df = load_daily_data("2017-01-01", "2026-01-01")
    if df.empty:
        print("ERROR: No data available!")
        return
    
    print(f"  Data: {len(df)} daily bars from {df.index[0]} to {df.index[-1]}")
    
    # Compute regime series for reference (but NOT used for filtering in live mode)
    print("\nComputing regime detection (for reference only)...")
    regime_series = compute_regime_series(df)
    regime_counts = regime_series.value_counts()
    print(f"  Regime distribution:")
    for reg, count in regime_counts.items():
        print(f"    {reg.value}: {count} bars ({count/len(regime_series)*100:.1f}%)")
    
    # ============================================================
    # TEST 1: BASELINE (no regime filter - matches live bot behavior)
    # ============================================================
    print("\n" + "=" * 80)
    print("TEST 1: BASELINE MODE (no regime filter - matches live bot)")
    print("=" * 80)
    baseline_results = run_full_year_backtest(df, regime_series, "baseline", use_regime_filter=False)
    
    # ============================================================
    # TEST 2: WITH BTV2 REGIME FILTER (BULL_STRONG or BEAR_STRONG)
    # This is NOT how the live bot works - for comparison only
    # ============================================================
    print("\n" + "=" * 80)
    print("TEST 2: BTV2 REGIME FILTER (BULL_STRONG or BEAR_STRONG) - NOT LIVE BOT")
    print("=" * 80)
    btv2_regime_results = run_full_year_backtest(df, regime_series, "btv2_regime", use_regime_filter=True)
    
    # ============================================================
    # WALK-FORWARD TESTS (12m train, 3m test) - using baseline mode
    # ============================================================
    print("\n" + "=" * 80)
    print("WALK-FORWARD VALIDATION (12m train, 3m test)")
    print("=" * 80)
    wf_results = run_walkforward_yearly(df, regime_series, "baseline")
    
    # Combine results
    all_results = baseline_results + btv2_regime_results + wf_results
    
    # Compute totals for each mode
    if baseline_results:
        baseline_total = compute_totals(baseline_results)
        baseline_total["Mode"] = "BASELINE_TOTAL"
        all_results.append(baseline_total)
    
    if btv2_regime_results:
        btv2_total = compute_totals(btv2_regime_results)
        btv2_total["Mode"] = "BTV2_REGIME_TOTAL"
        all_results.append(btv2_total)
    
    if wf_results:
        wf_total = compute_totals(wf_results)
        wf_total["Mode"] = "WALKFORWARD_TOTAL"
        all_results.append(wf_total)
    
    # Save to CSV
    df_results = pd.DataFrame(all_results)
    output_path = RESULTS_DIR / "ma_crossover_live_settings_validation.csv"
    df_results.to_csv(output_path, index=False)
    print(f"\nResults saved to {output_path}")
    
    # Print summary
    print("\n" + "=" * 80)
    print("VALIDATION SUMMARY")
    print("=" * 80)
    
    # Baseline mode summary
    print("\n--- BASELINE MODE (no regime filter - matches live bot) ---")
    if baseline_results:
        profitable = sum(1 for r in baseline_results if r["Net%"] > 0)
        total_net = sum(r["Net%"] for r in baseline_results)
        total_trades = sum(r["Trades"] for r in baseline_results)
        avg_wr = sum(r["WR%"] * r["Trades"] for r in baseline_results) / total_trades if total_trades > 0 else 0
        print(f"  Years tested: {len(baseline_results)}")
        print(f"  Profitable years: {profitable}/{len(baseline_results)}")
        print(f"  Total trades: {total_trades}")
        print(f"  Total Net%: {total_net:+.2f}%")
        print(f"  Avg WR%: {avg_wr:.1f}%")
        
        # Show each year
        print("\n  Per-Year Breakdown:")
        for r in baseline_results:
            status = "PROFIT" if r["Net%"] > 0 else "LOSS"
            trend_friendly = "TREND" if r["Year"] in [2019, 2020, 2021, 2024] else "CHOP"
            print(f"    {r['Year']}: {r['Net%']:+6.2f}% ({r['Trades']:3d} trades, WR={r['WR%']:5.1f}%, PF={r['PF']:.3f}) [{status}] [{trend_friendly}]")
    
    # BTV2 regime summary
    print("\n--- BTV2 REGIME FILTER (BULL_STRONG/BEAR_STRONG) - NOT LIVE BOT ---")
    if btv2_regime_results:
        profitable = sum(1 for r in btv2_regime_results if r["Net%"] > 0)
        total_net = sum(r["Net%"] for r in btv2_regime_results)
        total_trades = sum(r["Trades"] for r in btv2_regime_results)
        print(f"  Years tested: {len(btv2_regime_results)}")
        print(f"  Profitable years: {profitable}/{len(btv2_regime_results)}")
        print(f"  Total trades: {total_trades}")
        print(f"  Total Net%: {total_net:+.2f}%")
        
        print("\n  Per-Year Breakdown:")
        for r in btv2_regime_results:
            status = "PROFIT" if r["Net%"] > 0 else "LOSS"
            print(f"    {r['Year']}: {r['Net%']:+6.2f}% ({r['Trades']:3d} trades, WR={r['WR%']:5.1f}%) [{status}]")
    
    # Walk-forward summary
    print("\n--- WALK-FORWARD VALIDATION (12m train, 3m test) ---")
    if wf_results:
        profitable = sum(1 for r in wf_results if r["Net%"] > 0)
        total_net = sum(r["Net%"] for r in wf_results)
        total_trades = sum(r["Trades"] for r in wf_results)
        print(f"  Periods tested: {len(wf_results)}")
        print(f"  Profitable periods: {profitable}/{len(wf_results)}")
        print(f"  Total trades: {total_trades}")
        print(f"  Total Net%: {total_net:+.2f}%")
        
        print("\n  Per-Period Breakdown:")
        for r in wf_results:
            status = "PROFIT" if r["Net%"] > 0 else "LOSS"
            print(f"    {r['Year']}: {r['Net%']:+6.2f}% ({r['Trades']:3d} trades, WR={r['WR%']:5.1f}%) [{status}]")
    
    # Comparison
    print("\n--- COMPARISON ---")
    if baseline_results and btv2_regime_results:
        baseline_total_net = sum(r["Net%"] for r in baseline_results)
        btv2_total_net = sum(r["Net%"] for r in btv2_regime_results)
        baseline_trades = sum(r["Trades"] for r in baseline_results)
        btv2_trades = sum(r["Trades"] for r in btv2_regime_results)
        
        print(f"  Baseline (no filter):   {baseline_total_net:+.2f}% ({baseline_trades} trades)")
        print(f"  BTV2 Regime (blocked):  {btv2_total_net:+.2f}% ({btv2_trades} trades)")
        print(f"  Difference: {baseline_total_net - btv2_total_net:+.2f}%")
        
        # Trend-following friendly years analysis
        print("\n--- TREND-FOLLOWING ANALYSIS (Baseline) ---")
        trend_years = [2019, 2020, 2021, 2024]
        chop_years = [2018, 2022, 2023]
        
        baseline_trend = sum(r["Net%"] for r in baseline_results if r["Year"] in trend_years)
        baseline_chop = sum(r["Net%"] for r in baseline_results if r["Year"] in chop_years)
        
        print(f"  Trend years (2019,2020,2021,2024): {baseline_trend:+.2f}%")
        print(f"  Chop years (2018,2022,2023): {baseline_chop:+.2f}%")
        
        # Check for failed years
        print("\n--- FAILED YEARS (Net% < 0) ---")
        baseline_failed = [r for r in baseline_results if r["Net%"] < 0]
        
        if baseline_failed:
            print(f"  Baseline failed years: {[r['Year'] for r in baseline_failed]}")
        else:
            print("  Baseline: No failed years!")
    
    # Final verdict
    print("\n" + "=" * 80)
    print("FINAL VERDICT")
    print("=" * 80)
    
    if baseline_results:
        baseline_total_net = sum(r["Net%"] for r in baseline_results)
        baseline_profitable_years = sum(1 for r in baseline_results if r["Net%"] > 0)
        
        if baseline_total_net > 0:
            print("[PASS] Baseline mode (no regime filter - matches live bot) is PROFITABLE")
            print(f"   Total Net%: {baseline_total_net:+.2f}%")
            print(f"   Profitable years: {baseline_profitable_years}/7")
        else:
            print("[FAIL] Baseline mode is NOT profitable")
            print(f"   Total Net%: {baseline_total_net:+.2f}%")
        
        # Expected behavior check
        print("\n--- EXPECTED BEHAVIOR CHECK ---")
        print("MA Crossover is a trend-following strategy - expected to:")
        print("  [OK] Perform well in strong trending years (2019, 2020, 2021, 2024)")
        print("  [OK] Struggle in choppy/bear years (2018, 2022, 2023)")
        
        trend_year_performance = [r for r in baseline_results if r["Year"] in [2019, 2020, 2021, 2024]]
        if trend_year_performance:
            avg_trend = sum(r["Net%"] for r in trend_year_performance) / len(trend_year_performance)
            print(f"\n  Baseline avg in trend years: {avg_trend:+.2f}%")
    
    print("\nDone!")


if __name__ == "__main__":
    main()