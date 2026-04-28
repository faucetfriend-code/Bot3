"""
vwap_2023_2025_ultra_fast.py
==============================
Ultra-fast VWAP Parameter Optimization using multiprocessing.
Tests all parameter combinations in parallel.
"""

from __future__ import annotations

import sys
import os
import csv
import time
from pathlib import Path
from itertools import product
from typing import Dict, List, Any, Tuple
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp

import numpy as np
import pandas as pd

# Setup path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TEST_START = "2023-01-01"
TEST_END = "2026-01-01"
CUTOFF = 0.10

# Parameter grid
SD_THRESHOLDS = [3.5, 4.0, 4.5, 5.0, 5.5, 6.0]
ENTRY_MODES = ["bull_pullback", "bear_pullback", "cross"]
ATR_STOPS = [1.5, 2.0, 2.5, 3.0]
TP_MODES = ["atr", "pdh"]

MIN_WIN_RATE = 45.0
MIN_NET_PCT = -5.0

# ─────────────────────────────────────────────────────────────────────────────
# DATA (Global for multiprocessing)
# ─────────────────────────────────────────────────────────────────────────────

_data_5m: pd.DataFrame = None
_data_1m: pd.DataFrame = None
_levels: pd.DataFrame = None
_regime_series: pd.Series = None


def load_data() -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series]:
    """Load all data needed for backtesting."""
    from BTV2.regime_detector import RegimeDetector
    from BTV2.strategies import compute_reference_levels
    
    # Load 5m data
    path_5m = DATA_DIR / "BTCUSDT_5m.parquet"
    df_5m = pd.read_parquet(path_5m)
    df_5m.index = pd.to_datetime(df_5m.index, utc=True)
    start_ts = pd.Timestamp(TEST_START, tz="UTC")
    end_ts = pd.Timestamp(TEST_END, tz="UTC")
    mask_5m = (df_5m.index >= start_ts) & (df_5m.index < end_ts)
    df_5m = df_5m.loc[mask_5m].copy()
    
    # Load 1m data
    path_1m = DATA_DIR / "BTCUSDT_1m.parquet"
    if path_1m.exists():
        df_1m = pd.read_parquet(path_1m)
        df_1m.index = pd.to_datetime(df_1m.index, utc=True)
        mask_1m = (df_1m.index >= start_ts) & (df_1m.index < end_ts)
        df_1m = df_1m.loc[mask_1m].copy()
    else:
        df_1m = None
    
    # Compute regimes
    detector = RegimeDetector(df_5m, method="combined", timeframe="15m")
    regime_series = detector.get_regimes()
    
    # Pre-compute levels
    levels = compute_reference_levels(df_5m)
    
    return df_5m, df_1m, levels, regime_series


def init_worker(df_5m, df_1m, levels, regime_series):
    """Initialize worker process with shared data."""
    global _data_5m, _data_1m, _levels, _regime_series
    _data_5m = df_5m
    _data_1m = df_1m
    _levels = levels
    _regime_series = regime_series


def run_single_backtest(params: Tuple[float, str, float, str]) -> Dict[str, Any]:
    """Run a single backtest with given parameters."""
    from BTV2.strategies import run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR
    
    sd, entry, atr, tp = params
    
    param_dict = {
        "sd_threshold": sd,
        "atr_stop": atr,
        "atr_target": 2.0,
        "trailing_atr": 1.5,
        "ema_fast": 9,
        "ema_slow": 20,
        "use_trend_filter": False,
        "use_volume_filter": True,
        "volume_mult": 1.0,
        "use_trailing_stop": True,
        "adx_max": 30.0,
        "rsi_max": 55.0,
        "entry_mode": entry,
        "pullback_bars": 3,
        "use_htf_vwap": False,
        "use_htf_ema": True,
        "htf_adx_max": 30.0,
        "stoch_oversold": 40,
        "stoch_overbought": 60,
        "use_anchored_vwap": True,
        "use_session_filter": False,
        "require_reversal_candle": False,
        "tp_mode": tp,
        "require_mss": False,
        "mss_timeout_bars": 6,
        "use_fvg_filter": False,
        "use_ob_filter": False,
        "use_eqhl_filter": False,
        "use_cvd_filter": False,
        "cvd_window": 20,
        "use_ote": False,
        "use_dynamic_mode": False,
        "levels": _levels,
        "regime_series": _regime_series,
        "allowed_regimes": None,
    }
    
    equity, trades = run_vwap_scalping(
        _data_5m, CUTOFF, df_exit=_data_1m, **param_dict
    )
    
    if len(equity) < 2 or len(trades) == 0:
        return {
            "sd_threshold": sd, "entry_mode": entry, "atr_stop": atr, "tp_mode": tp,
            "trades": 0, "win_rate": 0.0, "net_pct": 0.0, "sharpe": 0.0,
            "max_dd": 0.0, "profit_factor": 0.0,
        }
    
    m = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR.get("5m", 105120))
    
    return {
        "sd_threshold": sd,
        "entry_mode": entry,
        "atr_stop": atr,
        "tp_mode": tp,
        "trades": m.get("n_trades", 0),
        "win_rate": m.get("win_rate_pct", 0.0),
        "net_pct": m.get("total_return_pct", 0.0),
        "sharpe": m.get("sharpe", 0.0),
        "max_dd": m.get("max_dd_pct", 0.0),
        "profit_factor": m.get("profit_factor", 0.0),
    }


def run_yearly_backtest(params: Tuple[float, str, float, str]) -> Dict[str, Any]:
    """Run yearly backtests for a parameter set."""
    from BTV2.strategies import run_vwap_scalping, compute_metrics, compute_reference_levels, INTERVAL_BARS_PER_YEAR
    
    sd, entry, atr, tp = params
    
    yearly_results = {}
    years = [2023, 2024, 2025]
    
    for year in years:
        year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        year_end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
        
        df_year = _data_5m.loc[(_data_5m.index >= year_start) & (_data_5m.index < year_end)].copy()
        df_exit_year = None
        if _data_1m is not None:
            df_exit_year = _data_1m.loc[(_data_1m.index >= year_start) & (_data_1m.index < year_end)].copy()
        regime_year = _regime_series.loc[(_regime_series.index >= year_start) & (_regime_series.index < year_end)].copy()
        
        if df_year.empty or regime_year.empty:
            yearly_results[year] = {"trades": 0, "net_pct": 0.0, "win_rate": 0.0}
            continue
        
        levels_year = compute_reference_levels(df_year)
        
        param_dict = {
            "sd_threshold": sd, "atr_stop": atr, "atr_target": 2.0, "trailing_atr": 1.5,
            "ema_fast": 9, "ema_slow": 20, "use_trend_filter": False, "use_volume_filter": True,
            "volume_mult": 1.0, "use_trailing_stop": True, "adx_max": 30.0, "rsi_max": 55.0,
            "entry_mode": entry, "pullback_bars": 3, "use_htf_vwap": False, "use_htf_ema": True,
            "htf_adx_max": 30.0, "stoch_oversold": 40, "stoch_overbought": 60,
            "use_anchored_vwap": True, "use_session_filter": False, "require_reversal_candle": False,
            "tp_mode": tp, "require_mss": False, "mss_timeout_bars": 6,
            "use_fvg_filter": False, "use_ob_filter": False, "use_eqhl_filter": False,
            "use_cvd_filter": False, "cvd_window": 20, "use_ote": False, "use_dynamic_mode": False,
            "levels": levels_year, "regime_series": regime_year, "allowed_regimes": None,
        }
        
        equity, trades = run_vwap_scalping(df_year, CUTOFF, df_exit=df_exit_year, **param_dict)
        
        if trades:
            wins = sum(1 for t in trades if t > 0)
            win_rate = wins / len(trades) * 100
            m = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR.get("5m", 105120))
            net = m.get("total_return_pct", 0.0)
        else:
            win_rate, net = 0.0, 0.0
        
        yearly_results[year] = {"trades": len(trades), "net_pct": net, "win_rate": win_rate}
    
    return yearly_results


def main():
    start_time = time.time()
    
    print("=" * 80)
    print("VWAP PARAMETER OPTIMIZATION - 2023-2025 RECENT MARKET ONLY")
    print("=" * 80)
    print(f"\nTest Period: {TEST_START} to {TEST_END}")
    print(f"Goals: Win Rate > {MIN_WIN_RATE}%, Net Return >= {MIN_NET_PCT}%")
    
    # Load data
    print("\nLoading data...")
    df_5m, df_1m, levels, regime_series = load_data()
    print(f"  5m bars: {len(df_5m):,}")
    print(f"  Regime distribution:")
    regime_dist = regime_series.value_counts()
    for r in regime_dist.index:
        print(f"    {r.value}: {regime_dist[r]:,} bars ({regime_dist[r]/len(regime_series)*100:.1f}%)")
    
    # Generate parameter combinations
    param_combinations = list(product(SD_THRESHOLDS, ENTRY_MODES, ATR_STOPS, TP_MODES))
    print(f"\nTotal combinations to test: {len(param_combinations)}")
    print("-" * 80)
    
    # Run backtests in parallel
    n_workers = min(mp.cpu_count(), 8)
    print(f"Using {n_workers} parallel workers...")
    
    all_results = []
    passing_results = []
    
    with ProcessPoolExecutor(max_workers=n_workers, initializer=init_worker,
                           initargs=(df_5m, df_1m, levels, regime_series)) as executor:
        futures = {executor.submit(run_single_backtest, p): p for p in param_combinations}
        
        for idx, future in enumerate(as_completed(futures)):
            result = future.result()
            all_results.append(result)
            
            passes = (result["win_rate"] >= MIN_WIN_RATE) and (result["net_pct"] >= MIN_NET_PCT)
            result["passes_goals"] = passes
            
            if passes:
                passing_results.append(result)
            
            if (idx + 1) % 20 == 0 or passes:
                status = "✓ PASS" if passes else ""
                print(f"  [{idx+1:3d}/{len(param_combinations)}] SD={result['sd_threshold']:.1f} "
                      f"{result['entry_mode']:15s} ATR={result['atr_stop']:.1f} TP={result['tp_mode']:3s} "
                      f"→ WR={result['win_rate']:5.1f}% Net={result['net_pct']:+7.1f}% {status}")
    
    # Run yearly breakdown for passing results
    print("\nRunning yearly breakdown for passing combinations...")
    for result in passing_results:
        params = (result["sd_threshold"], result["entry_mode"], result["atr_stop"], result["tp_mode"])
        yearly = run_yearly_backtest(params)
        result.update({
            "year_2023_trades": yearly[2023]["trades"],
            "year_2023_net": yearly[2023]["net_pct"],
            "year_2024_trades": yearly[2024]["trades"],
            "year_2024_net": yearly[2024]["net_pct"],
            "year_2025_trades": yearly[2025]["trades"],
            "year_2025_net": yearly[2025]["net_pct"],
        })
    
    # Create DataFrame and save
    results_df = pd.DataFrame(all_results)
    results_df = results_df.sort_values(["win_rate", "net_pct"], ascending=[False, False])
    
    output_path = RESULTS_DIR / "vwap_2023_2025_optimization.csv"
    results_df.to_csv(output_path, index=False)
    print(f"\nSaved full results to: {output_path}")
    
    # Print summary
    print("\n" + "=" * 80)
    print("OPTIMIZATION RESULTS")
    print("=" * 80)
    
    # Top 10 by win rate
    print("\nTOP 10 PARAMETER COMBINATIONS (by Win Rate):")
    print("-" * 85)
    top_10 = results_df.head(10)
    print(f"{'SD':>4} {'Entry Mode':15s} {'ATR':>5} {'TP':>4} {'Trades':>6} {'WR%':>6} {'Net%':>7} {'Sharpe':>7} {'PF':>5}")
    print("-" * 85)
    for _, row in top_10.iterrows():
        print(f"{row['sd_threshold']:>4.1f} {row['entry_mode']:15s} {row['atr_stop']:>5.1f} {row['tp_mode']:>4s} "
              f"{row['trades']:>6d} {row['win_rate']:>6.1f}% {row['net_pct']:>+7.1f}% {row['sharpe']:>7.2f} {row['profit_factor']:>5.2f}")
    
    # Top 10 by net return
    print("\nTOP 10 PARAMETER COMBINATIONS (by Net Return):")
    print("-" * 85)
    top_net = results_df.sort_values("net_pct", ascending=False).head(10)
    print(f"{'SD':>4} {'Entry Mode':15s} {'ATR':>5} {'TP':>4} {'Trades':>6} {'WR%':>6} {'Net%':>7} {'Sharpe':>7} {'PF':>5}")
    print("-" * 85)
    for _, row in top_net.iterrows():
        print(f"{row['sd_threshold']:>4.1f} {row['entry_mode']:15s} {row['atr_stop']:>5.1f} {row['tp_mode']:>4s} "
              f"{row['trades']:>6d} {row['win_rate']:>6.1f}% {row['net_pct']:>+7.1f}% {row['sharpe']:>7.2f} {row['profit_factor']:>5.2f}")
    
    # Goals validation
    print("\n" + "=" * 80)
    print("GOALS VALIDATION SUMMARY")
    print("=" * 80)
    print(f"\nGoals: Win Rate > {MIN_WIN_RATE}% AND Net Return >= {MIN_NET_PCT}%")
    print(f"Passing combinations: {len(passing_results)} / {len(all_results)}")
    
    if passing_results:
        passing_df = pd.DataFrame(passing_results)
        passing_df = passing_df.sort_values(["win_rate", "net_pct"], ascending=[False, False])
        
        print("\n✓ PASSING COMBINATIONS:")
        print(f"\n{'SD':>4} {'Entry Mode':15s} {'ATR':>5} {'TP':>4} {'Trades':>6} {'WR%':>6} {'Net%':>7} {'Sharpe':>7}")
        print("-" * 70)
        for _, row in passing_df.iterrows():
            print(f"{row['sd_threshold']:>4.1f} {row['entry_mode']:15s} {row['atr_stop']:>5.1f} {row['tp_mode']:>4s} "
                  f"{row['trades']:>6d} {row['win_rate']:>6.1f}% {row['net_pct']:>+7.1f}% {row['sharpe']:>7.2f}")
        
        # Best recommendation
        best = passing_df.iloc[0]
        print("\n" + "=" * 80)
        print("RECOMMENDED PARAMETERS FOR 2023-2025 MARKET")
        print("=" * 80)
        
        yearly_str = ""
        if "year_2023_trades" in best:
            yearly_str = f"""
YEARLY BREAKDOWN:
  2023: {int(best['year_2023_trades'])} trades, {best['year_2023_net']:+.1f}% net
  2024: {int(best['year_2024_trades'])} trades, {best['year_2024_net']:+.1f}% net
  2025: {int(best['year_2025_trades'])} trades, {best['year_2025_net']:+.1f}% net"""
        
        print(f"""
BEST PARAMETERS FOUND:
  sd_threshold: {best['sd_threshold']}
  entry_mode:  {best['entry_mode']}
  atr_stop:    {best['atr_stop']}
  tp_mode:     {best['tp_mode']}

EXPECTED PERFORMANCE:
  Win Rate:      {best['win_rate']:.1f}%
  Net Return:   {best['net_pct']:+.1f}%
  Sharpe:        {best['sharpe']:.2f}
  Max Drawdown: {best['max_dd']:.1f}%
  Trade Count:  {int(best['trades'])} trades
  Profit Factor: {best['profit_factor']:.2f}x{yearly_str}
""")
    else:
        print("\n✗ NO PASSING COMBINATIONS FOUND")
        print("\nMost promising (closest to goals):")
        
        results_df["score"] = (
            (results_df["win_rate"] / 100) * 0.5 +
            (results_df["net_pct"].clip(lower=0) / 100 + 0.5) * 0.5
        )
        closest = results_df.nlargest(5, "score")
        print(f"\n{'SD':>4} {'Entry Mode':15s} {'ATR':>5} {'TP':>4} {'Trades':>6} {'WR%':>6} {'Net%':>7}")
        print("-" * 70)
        for _, row in closest.iterrows():
            print(f"{row['sd_threshold']:>4.1f} {row['entry_mode']:15s} {row['atr_stop']:>5.1f} {row['tp_mode']:>4s} "
                  f"{row['trades']:>6d} {row['win_rate']:>6.1f}% {row['net_pct']:>+7.1f}%")
    
    # Sensitivity analysis
    print("\n" + "=" * 80)
    print("PARAMETER SENSITIVITY ANALYSIS")
    print("=" * 80)
    
    print("\nSD_THRESHOLD Impact:")
    sd_analysis = results_df.groupby("sd_threshold").agg({
        "win_rate": "mean", "net_pct": "mean", "trades": "sum",
    }).round(2)
    for sd, row in sd_analysis.iterrows():
        print(f"  SD={sd:.1f}: Avg WR={row['win_rate']:.1f}% Avg Net={row['net_pct']:+.1f}% Total Trades={int(row['trades'])}")
    
    print("\nENTRY_MODE Impact:")
    entry_analysis = results_df.groupby("entry_mode").agg({
        "win_rate": "mean", "net_pct": "mean", "trades": "sum",
    }).round(2)
    for mode, row in entry_analysis.iterrows():
        print(f"  {mode:15s}: Avg WR={row['win_rate']:.1f}% Avg Net={row['net_pct']:+.1f}% Total Trades={int(row['trades'])}")
    
    print("\nATR_STOP Impact:")
    atr_analysis = results_df.groupby("atr_stop").agg({
        "win_rate": "mean", "net_pct": "mean", "trades": "sum",
    }).round(2)
    for atr, row in atr_analysis.iterrows():
        print(f"  ATR={atr:.1f}: Avg WR={row['win_rate']:.1f}% Avg Net={row['net_pct']:+.1f}% Total Trades={int(row['trades'])}")
    
    print("\nTP_MODE Impact:")
    tp_analysis = results_df.groupby("tp_mode").agg({
        "win_rate": "mean", "net_pct": "mean", "trades": "sum",
    }).round(2)
    for tp, row in tp_analysis.iterrows():
        print(f"  TP={tp:3s}: Avg WR={row['win_rate']:.1f}% Avg Net={row['net_pct']:+.1f}% Total Trades={int(row['trades'])}")
    
    elapsed = time.time() - start_time
    print(f"\nTotal time: {elapsed:.1f} seconds")
    print("\n" + "=" * 80)
    print("Done!")
    print("=" * 80)


if __name__ == "__main__":
    main()
