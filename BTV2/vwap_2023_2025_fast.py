"""
vwap_2023_2025_fast_optimization.py
===================================
Fast VWAP Parameter Optimization using ONLY recent data (2023-2025).

Uses pre-computed indicators to speed up the grid search significantly.
"""

from __future__ import annotations

import sys
from itertools import product
from pathlib import Path
from typing import Dict, Tuple, Any, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.regime_detector import RegimeDetector, MarketRegime
from BTV2.strategies import (
    run_vwap_scalping,
    compute_reference_levels,
    compute_metrics,
    COST_PER_SIDE,
    INTERVAL_BARS_PER_YEAR,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TEST_START = "2023-01-01"
TEST_END = "2026-01-01"
CUTOFF = 0.10

# Parameter grid (reduced for speed)
SD_THRESHOLDS = [3.5, 4.0, 4.5, 5.0, 5.5, 6.0]
ENTRY_MODES = ["bull_pullback", "bear_pullback", "cross"]
ATR_STOPS = [1.5, 2.0, 2.5, 3.0]
TP_MODES = ["atr", "pdh"]

MIN_WIN_RATE = 45.0
MIN_NET_PCT = -5.0


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_5m_data(start: str, end: str) -> pd.DataFrame:
    path = DATA_DIR / "BTCUSDT_5m.parquet"
    if not path.exists():
        print(f"ERROR: No data found at {path}")
        return pd.DataFrame()
    
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df.index >= start_ts) & (df.index < end_ts)
    return df.loc[mask].copy()


def load_1m_data(start: str, end: str) -> Optional[pd.DataFrame]:
    path = DATA_DIR / "BTCUSDT_1m.parquet"
    if not path.exists():
        return None
    
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df.index >= start_ts) & (df.index < end_ts)
    return df.loc[mask].copy()


# ─────────────────────────────────────────────────────────────────────────────
# FAST BACKTEST
# ─────────────────────────────────────────────────────────────────────────────

def run_fast_backtest(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    levels: pd.DataFrame,
    regime_series: pd.Series,
    sd_threshold: float,
    entry_mode: str,
    atr_stop: float,
    tp_mode: str,
    allowed_regimes: Optional[List[MarketRegime]] = None,
) -> Dict[str, Any]:
    """Run VWAP backtest with specified parameters."""
    
    params = {
        "sd_threshold": sd_threshold,
        "atr_stop": atr_stop,
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
        "entry_mode": entry_mode,
        "pullback_bars": 3,
        "use_htf_vwap": False,
        "use_htf_ema": True,
        "htf_adx_max": 30.0,
        "stoch_oversold": 40,
        "stoch_overbought": 60,
        "use_anchored_vwap": True,
        "use_session_filter": False,
        "require_reversal_candle": False,
        "tp_mode": tp_mode,
        "require_mss": False,
        "mss_timeout_bars": 6,
        "use_fvg_filter": False,
        "use_ob_filter": False,
        "use_eqhl_filter": False,
        "use_cvd_filter": False,
        "cvd_window": 20,
        "use_ote": False,
        "use_dynamic_mode": False,
        "levels": levels,
        "regime_series": regime_series,
        "allowed_regimes": allowed_regimes,
    }
    
    equity, trades = run_vwap_scalping(df, CUTOFF, df_exit=df_exit, **params)
    
    if len(equity) < 2 or len(trades) == 0:
        return {
            "trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "net_pct": 0.0, "sharpe": 0.0, "max_drawdown_pct": 0.0,
        }
    
    m = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR.get("5m", 105120))
    return {
        "trades": m.get("n_trades", 0),
        "win_rate": m.get("win_rate_pct", 0.0),
        "profit_factor": m.get("profit_factor", 0.0),
        "net_pct": m.get("total_return_pct", 0.0),
        "sharpe": m.get("sharpe", 0.0),
        "max_drawdown_pct": m.get("max_dd_pct", 0.0),
    }


def run_yearly_backtest(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    levels: pd.DataFrame,
    regime_series: pd.Series,
    params: Dict[str, Any],
    years: List[int],
) -> List[Dict[str, Any]]:
    """Run backtest for each year separately."""
    results = []
    
    for year in years:
        year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        year_end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
        
        df_year = df.loc[(df.index >= year_start) & (df.index < year_end)].copy()
        df_exit_year = None
        if df_exit is not None:
            df_exit_year = df_exit.loc[(df_exit.index >= year_start) & (df_exit.index < year_end)].copy()
        
        regime_year = regime_series.loc[(regime_series.index >= year_start) & (regime_series.index < year_end)].copy()
        
        if df_year.empty or regime_year.empty:
            results.append({"year": year, "trades": 0, "net_pct": 0.0, "win_rate": 0.0})
            continue
        
        # Pre-compute levels for this year
        levels_year = compute_reference_levels(df_year)
        
        metrics = run_fast_backtest(
            df_year, df_exit_year, levels_year, regime_year,
            params["sd_threshold"], params["entry_mode"],
            params["atr_stop"], params["tp_mode"],
            allowed_regimes=params.get("allowed_regimes"),
        )
        
        results.append({
            "year": year,
            "trades": metrics["trades"],
            "net_pct": metrics["net_pct"],
            "win_rate": metrics["win_rate"],
        })
    
    return results


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("VWAP PARAMETER OPTIMIZATION - 2023-2025 RECENT MARKET ONLY")
    print("=" * 80)
    print(f"\nTest Period: {TEST_START} to {TEST_END}")
    print(f"Parameter Grid: {len(SD_THRESHOLDS) * len(ENTRY_MODES) * len(ATR_STOPS) * len(TP_MODES)} combinations")
    print(f"Goals: WR > {MIN_WIN_RATE}%, Net >= {MIN_NET_PCT}%")
    print()
    
    # Load data
    print("Loading data...")
    df_5m = load_5m_data(TEST_START, TEST_END)
    if df_5m.empty:
        print("ERROR: No data loaded!")
        return
    print(f"  5m bars: {len(df_5m):,}")
    
    df_1m = load_1m_data(TEST_START, TEST_END)
    if df_1m is not None:
        print(f"  1m bars: {len(df_1m):,}")
    
    # Compute regimes
    print("\nComputing regimes...")
    detector = RegimeDetector(df_5m, method="combined", timeframe="15m")
    regime_series = detector.get_regimes()
    regime_dist = regime_series.value_counts()
    print("Regime distribution:")
    for r in MarketRegime:
        cnt = regime_dist.get(r, 0)
        print(f"  {r.value}: {cnt:,} bars ({cnt/len(regime_series)*100:.1f}%)")
    
    # Pre-compute reference levels once
    print("\nPre-computing reference levels...")
    levels = compute_reference_levels(df_5m)
    print("  Done!")
    
    # Generate combinations
    param_combinations = list(product(SD_THRESHOLDS, ENTRY_MODES, ATR_STOPS, TP_MODES))
    print(f"\nTesting {len(param_combinations)} combinations...")
    print("-" * 80)
    
    all_results = []
    passing_results = []
    years = [2023, 2024, 2025]
    
    for idx, (sd, entry, atr, tp) in enumerate(param_combinations):
        params = {
            "sd_threshold": sd,
            "entry_mode": entry,
            "atr_stop": atr,
            "tp_mode": tp,
            "allowed_regimes": None,
        }
        
        # Run full backtest
        metrics = run_fast_backtest(df_5m, df_1m, levels, regime_series, sd, entry, atr, tp)
        
        # Yearly breakdown
        yearly_results = run_yearly_backtest(df_5m, df_1m, levels, regime_series, params, years)
        
        result = {
            "sd_threshold": sd,
            "entry_mode": entry,
            "atr_stop": atr,
            "tp_mode": tp,
            "trades": metrics["trades"],
            "win_rate": metrics["win_rate"],
            "net_pct": metrics["net_pct"],
            "sharpe": metrics["sharpe"],
            "max_dd": metrics["max_drawdown_pct"],
            "profit_factor": metrics["profit_factor"],
            "year_2023_trades": yearly_results[0]["trades"],
            "year_2023_net": yearly_results[0]["net_pct"],
            "year_2024_trades": yearly_results[1]["trades"],
            "year_2024_net": yearly_results[1]["net_pct"],
            "year_2025_trades": yearly_results[2]["trades"],
            "year_2025_net": yearly_results[2]["net_pct"],
        }
        all_results.append(result)
        
        passes = (metrics["win_rate"] >= MIN_WIN_RATE) and (metrics["net_pct"] >= MIN_NET_PCT)
        result["passes_goals"] = passes
        
        if passes:
            passing_results.append(result)
        
        if (idx + 1) % 10 == 0 or passes:
            status = "✓ PASS" if passes else ""
            print(f"  [{idx+1:3d}/{len(param_combinations)}] SD={sd:.1f} {entry:15s} ATR={atr:.1f} TP={tp:3s} "
                  f"→ WR={metrics['win_rate']:5.1f}% Net={metrics['net_pct']:+7.1f}% {status}")
    
    # Save results
    results_df = pd.DataFrame(all_results)
    results_df = results_df.sort_values(["win_rate", "net_pct"], ascending=[False, False])
    
    output_path = RESULTS_DIR / "vwap_2023_2025_optimization.csv"
    results_df.to_csv(output_path, index=False)
    print(f"\nSaved results to: {output_path}")
    
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
  Trade Count:  {best['trades']} trades
  Profit Factor: {best['profit_factor']:.2f}x

YEARLY BREAKDOWN:
  2023: {best['year_2023_trades']} trades, {best['year_2023_net']:+.1f}% net
  2024: {best['year_2024_trades']} trades, {best['year_2024_net']:+.1f}% net
  2025: {best['year_2025_trades']} trades, {best['year_2025_net']:+.1f}% net
""")
    else:
        print("\n✗ NO PASSING COMBINATIONS FOUND")
        print("\nMost promising (closest to goals):")
        
        # Calculate score based on how close to goals
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
        print(f"  SD={sd:.1f}: Avg WR={row['win_rate']:.1f}% Avg Net={row['net_pct']:+.1f}% Total Trades={row['trades']}")
    
    print("\nENTRY_MODE Impact:")
    entry_analysis = results_df.groupby("entry_mode").agg({
        "win_rate": "mean", "net_pct": "mean", "trades": "sum",
    }).round(2)
    for mode, row in entry_analysis.iterrows():
        print(f"  {mode:15s}: Avg WR={row['win_rate']:.1f}% Avg Net={row['net_pct']:+.1f}% Total Trades={row['trades']}")
    
    print("\nATR_STOP Impact:")
    atr_analysis = results_df.groupby("atr_stop").agg({
        "win_rate": "mean", "net_pct": "mean", "trades": "sum",
    }).round(2)
    for atr, row in atr_analysis.iterrows():
        print(f"  ATR={atr:.1f}: Avg WR={row['win_rate']:.1f}% Avg Net={row['net_pct']:+.1f}% Total Trades={row['trades']}")
    
    print("\nTP_MODE Impact:")
    tp_analysis = results_df.groupby("tp_mode").agg({
        "win_rate": "mean", "net_pct": "mean", "trades": "sum",
    }).round(2)
    for tp, row in tp_analysis.iterrows():
        print(f"  TP={tp:3s}: Avg WR={row['win_rate']:.1f}% Avg Net={row['net_pct']:+.1f}% Total Trades={row['trades']}")
    
    print("\n" + "=" * 80)
    print("Done!")
    print("=" * 80)


if __name__ == "__main__":
    main()
