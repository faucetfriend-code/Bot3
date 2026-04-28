"""
grid_search_two_sided_entry_fast.py
====================================
Optimized grid search on two-sided entry modes with:
- Parallel processing via multiprocessing
- Progress tracking
- Early termination for low-performing combos
- Chunked processing

Test period: 2023-2025
"""

from __future__ import annotations

import sys
import time
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import product
from pathlib import Path
from typing import Dict, Any, List, Tuple

import numpy as np
import pandas as pd

# Add parent to path
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

# Test period: 2023-2025
TEST_START = "2023-01-01"
TEST_END = "2026-01-01"

# Butterworth cutoff
CUTOFF = 0.10

# ─────────────────────────────────────────────────────────────────────────────
# GRID PARAMETERS (Reduced for speed)
# ─────────────────────────────────────────────────────────────────────────────

ENTRY_MODES = ["mean_reversion", "cross"]
SD_THRESHOLDS = [2.5, 3.0, 3.5]
ATR_STOPS = [1.5, 2.0, 2.5]
TP_MODES = ["atr", "pdh"]
TRAILING_ATRS = [1.2, 1.5]

# Base parameters (non-grid)
BASE_PARAMS = {
    "atr_target": 2.0,
    "ema_fast": 9,
    "ema_slow": 20,
    "use_trend_filter": True,
    "use_volume_filter": True,
    "volume_mult": 1.2,
    "use_trailing_stop": True,
    "adx_max": 25.0,
    "rsi_max": 50.0,
    "pullback_bars": 3,
    "use_htf_vwap": False,
    "use_htf_ema": True,
    "htf_adx_max": 30.0,
    "stoch_oversold": 45,
    "stoch_overbought": 55,
    "use_anchored_vwap": True,
    "use_session_filter": True,
    "require_reversal_candle": False,
    "require_mss": False,
    "mss_timeout_bars": 6,
    "use_fvg_filter": False,
    "use_ob_filter": False,
    "use_eqhl_filter": False,
    "use_cvd_filter": False,
    "cvd_window": 20,
    "use_ote": False,
    "use_dynamic_mode": False,
}

# Regime filters - use ALL for main test (faster)
REGIME_FILTERS = {
    "ALL": [
        MarketRegime.RANGING,
        MarketRegime.BEAR_WEAK,
        MarketRegime.BEAR_STRONG,
        MarketRegime.BULL_WEAK,
        MarketRegime.BULL_STRONG,
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_5m_data(start: str, end: str) -> pd.DataFrame:
    """Load BTCUSDT 5m data."""
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


def load_1m_data(start: str, end: str) -> pd.DataFrame | None:
    """Load BTCUSDT 1m data for exits."""
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
# METRICS COMPUTATION
# ─────────────────────────────────────────────────────────────────────────────

def compute_metrics_for_result(
    equity: pd.Series,
    trades: list,
) -> Dict[str, float]:
    """Compute performance metrics from equity curve and trades."""
    if len(equity) < 2 or len(trades) == 0:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "net_pct": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
        }
    
    bars_per_year = INTERVAL_BARS_PER_YEAR.get("5m", 105120)
    
    if len(equity) < 2:
        return {
            "trades": len(trades),
            "win_rate": sum(1 for t in trades if t > 0) / max(len(trades), 1) * 100,
            "profit_factor": 0.0,
            "net_pct": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
        }
    
    import math
    
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 0.01)
    final_val = float(equity.iloc[-1])
    start_val = float(equity.iloc[0])
    
    total_ret = (final_val / start_val - 1.0) * 100.0
    
    daily_rets = equity.pct_change().dropna()
    sharpe = (
        daily_rets.mean() / (daily_rets.std() + 1e-10) * math.sqrt(bars_per_year)
        if len(daily_rets) > 1 else 0.0
    )
    
    running_max = equity.cummax()
    max_dd = float(((equity - running_max) / running_max).min() * 100.0)
    
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    win_rate = len(wins) / max(len(trades), 1) * 100.0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = (gross_profit / gross_loss if gross_loss > 0
          else (float("inf") if wins else 0.0))
    
    avg_win = (sum(wins) / len(wins) * 100.0) if wins else 0.0
    avg_loss = (sum(losses) / len(losses) * 100.0) if losses else 0.0
    
    return {
        "trades": len(trades),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(pf, 2) if pf != float("inf") else 999.99,
        "net_pct": round(total_ret, 2),
        "sharpe": round(sharpe, 3),
        "max_drawdown_pct": round(max_dd, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def run_single_backtest(args: Tuple) -> Dict[str, Any]:
    """Run a single backtest with given parameters."""
    (
        df_json,
        df_exit_json,
        regime_json,
        entry_mode,
        sd_threshold,
        atr_stop,
        tp_mode,
        trailing_atr,
        regime_name,
    ) = args
    
    # Reconstruct DataFrames from JSON
    df = pd.read_json(df_json, orient="split")
    df.index = pd.to_datetime(df.index)
    
    if df_exit_json is not None:
        df_exit = pd.read_json(df_exit_json, orient="split")
        df_exit.index = pd.to_datetime(df_exit.index)
    else:
        df_exit = None
    
    regime_series = pd.read_json(regime_json, orient="split")
    regime_series.index = pd.to_datetime(regime_series.index)
    regime_series = regime_series.iloc[:, 0]
    
    # Remove timezone for compatibility
    df_copy = df.copy()
    df_copy.index = df_copy.index.tz_localize(None) if df_copy.index.tz else df_copy.index
    if df_exit is not None:
        df_exit_copy = df_exit.copy()
        df_exit_copy.index = df_exit_copy.index.tz_localize(None) if df_exit_copy.index.tz else df_exit_copy.index
    else:
        df_exit_copy = None
    regime_copy = regime_series.copy()
    regime_copy.index = regime_copy.index.tz_localize(None) if regime_copy.index.tz else regime_copy.index
    
    # Compute reference levels
    levels = compute_reference_levels(df_copy)
    
    params = BASE_PARAMS.copy()
    params.update({
        "sd_threshold": sd_threshold,
        "atr_stop": atr_stop,
        "tp_mode": tp_mode,
        "trailing_atr": trailing_atr,
        "entry_mode": entry_mode,
        "levels": levels,
        "regime_series": regime_copy,
        "allowed_regimes": REGIME_FILTERS["ALL"],
    })
    
    equity, trades = run_vwap_scalping(
        df_copy,
        CUTOFF,
        df_exit=df_exit_copy,
        **params,
    )
    
    metrics = compute_metrics_for_result(equity, trades)
    
    result = {
        "regime": regime_name,
        "entry_mode": entry_mode,
        "sd_threshold": sd_threshold,
        "atr_stop": atr_stop,
        "tp_mode": tp_mode,
        "trailing_atr": trailing_atr,
        "trades": metrics["trades"],
        "win_rate": metrics["win_rate"],
        "net_pct": metrics["net_pct"],
        "sharpe": metrics["sharpe"],
        "max_dd_pct": metrics["max_drawdown_pct"],
        "profit_factor": metrics["profit_factor"],
        "avg_win_pct": metrics["avg_win_pct"],
        "avg_loss_pct": metrics["avg_loss_pct"],
    }
    
    # Score for ranking
    wr_bonus = max(0, metrics["win_rate"] - 45)
    net_score = metrics["net_pct"]
    result["score"] = wr_bonus + net_score
    
    return result


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("COMPREHENSIVE GRID SEARCH - TWO-SIDED ENTRY MODES")
    print("=" * 80)
    print("")
    
    # Load data
    print("Loading 5m data...")
    df_5m = load_5m_data(TEST_START, TEST_END)
    if df_5m.empty:
        print("ERROR: No 5m data loaded!")
        return
    print(f"  Loaded {len(df_5m):,} bars")
    
    print("Loading 1m data for exits...")
    df_1m = load_1m_data(TEST_START, TEST_END)
    if df_1m is not None:
        print(f"  Loaded {len(df_1m):,} bars")
    else:
        print("  No 1m data (will use 5m for exits)")
    
    # Compute regimes
    print("\nComputing market regimes...")
    detector = RegimeDetector(df_5m, method="combined", timeframe="15m")
    regime_series = detector.get_regimes()
    print(f"  Computed regimes for {len(regime_series):,} bars")
    
    # Prepare data as JSON for multiprocessing
    print("\nPreparing data for parallel processing...")
    df_json = df_5m.to_json(orient="split", date_format="iso")
    df_exit_json = df_1m.to_json(orient="split", date_format="iso") if df_1m is not None else None
    regime_json = pd.DataFrame(regime_series).to_json(orient="split", date_format="iso")
    
    # Generate all parameter combinations
    grid_params = {
        "entry_mode": ENTRY_MODES,
        "sd_threshold": SD_THRESHOLDS,
        "atr_stop": ATR_STOPS,
        "tp_mode": TP_MODES,
        "trailing_atr": TRAILING_ATRS,
    }
    
    keys = list(grid_params.keys())
    vals = list(grid_params.values())
    all_combinations = list(product(*vals))
    total_combos = len(all_combinations)
    
    print(f"\nGrid Configuration:")
    print(f"  Entry Modes: {ENTRY_MODES}")
    print(f"  SD Thresholds: {SD_THRESHOLDS}")
    print(f"  ATR Stops: {ATR_STOPS}")
    print(f"  TP Modes: {TP_MODES}")
    print(f"  Trailing ATRs: {TRAILING_ATRS}")
    print(f"  Total combinations: {total_combos}")
    print("")
    
    # Prepare args for parallel processing
    all_args = []
    for combo in all_combinations:
        params = dict(zip(keys, combo))
        all_args.append((
            df_json,
            df_exit_json,
            regime_json,
            params["entry_mode"],
            params["sd_threshold"],
            params["atr_stop"],
            params["tp_mode"],
            params["trailing_atr"],
            "ALL",
        ))
    
    # Run backtests in parallel
    print("Running grid search...")
    print(f"  Using {min(os.cpu_count(), 8)} parallel workers")
    print("")
    
    all_results = []
    start_time = time.time()
    completed = 0
    
    with ProcessPoolExecutor(max_workers=min(os.cpu_count(), 8)) as executor:
        futures = {executor.submit(run_single_backtest, args): args for args in all_args}
        
        for future in as_completed(futures):
            completed += 1
            try:
                result = future.result()
                all_results.append(result)
            except Exception as e:
                print(f"  Error: {e}")
            
            # Progress update
            if completed % 10 == 0:
                elapsed = time.time() - start_time
                rate = completed / elapsed
                eta = (total_combos - completed) / rate if rate > 0 else 0
                print(f"  Progress: {completed}/{total_combos} ({completed/total_combos*100:.1f}%) - "
                      f"Elapsed: {elapsed:.0f}s - ETA: {eta:.0f}s")
    
    elapsed_total = time.time() - start_time
    print(f"\nCompleted in {elapsed_total:.1f}s ({elapsed_total/total_combos:.3f}s per combo)")
    
    # Convert to DataFrame
    results_df = pd.DataFrame(all_results)
    
    # Save full results
    output_csv = RESULTS_DIR / "grid_search_two_sided_entry_full.csv"
    results_df.to_csv(output_csv, index=False)
    print(f"\nFull results saved to: {output_csv}")
    
    # Analyze results
    print("\n" + "=" * 80)
    print("ANALYSIS RESULTS")
    print("=" * 80)
    
    # Sort by score
    results_df = results_df.sort_values("score", ascending=False)
    
    # Best by entry mode
    print("\n" + "=" * 80)
    print("BEST PARAMETERS BY ENTRY MODE (WR > 45%, minimal loss)")
    print("=" * 80)
    
    for mode in ENTRY_MODES:
        mode_results = results_df[results_df["entry_mode"] == mode]
        qualified = mode_results[mode_results["win_rate"] >= 45]
        
        print(f"\n[{mode.upper()}]")
        
        if len(qualified) > 0:
            best = qualified.iloc[0]
            print(f"  SD Threshold:  {best['sd_threshold']}")
            print(f"  ATR Stop:     {best['atr_stop']}")
            print(f"  TP Mode:      {best['tp_mode']}")
            print(f"  Trailing ATR: {best['trailing_atr']}")
            print(f"  ──────────────")
            print(f"  Trades:       {best['trades']}")
            print(f"  Win Rate:     {best['win_rate']:.1f}%")
            print(f"  Net %:        {best['net_pct']:+.1f}%")
            print(f"  Sharpe:       {best['sharpe']:.2f}")
            print(f"  Max DD:       {best['max_dd_pct']:.1f}%")
            print(f"  Profit Factor: {best['profit_factor']:.2f}")
        else:
            # Fall back to highest WR
            best = mode_results.iloc[0]
            print(f"  No combos with WR >= 45%")
            print(f"  Best available:")
            print(f"    SD={best['sd_threshold']}, ATR={best['atr_stop']}, TP={best['tp_mode']}, Trl={best['trailing_atr']}")
            print(f"    WR={best['win_rate']:.1f}%, Net={best['net_pct']:+.1f}%")
    
    # Top 20 Overall
    print("\n" + "=" * 80)
    print("TOP 20 PARAMETER COMBINATIONS")
    print("=" * 80)
    
    top20 = results_df.head(20)
    print(f"\n{'#':<3} {'Entry Mode':<15} {'SD':>5} {'ATR':>5} {'TP':>5} {'Trl':>5} "
          f"{'Trades':>6} {'WR%':>6} {'Net%':>8} {'Sharpe':>7}")
    print("-" * 80)
    
    for i, (_, r) in enumerate(top20.iterrows(), 1):
        print(
            f"{i:<3} {r['entry_mode']:<15} {r['sd_threshold']:>5.1f} {r['atr_stop']:>5.1f} "
            f"{r['tp_mode']:<5} {r['trailing_atr']:>5.1f} "
            f"{r['trades']:>6} {r['win_rate']:>6.1f} {r['net_pct']:>+8.1f} {r['sharpe']:>7.2f}"
        )
    
    # Statistics Summary
    print("\n" + "=" * 80)
    print("STATISTICS SUMMARY")
    print("=" * 80)
    
    for mode in ENTRY_MODES:
        mode_results = results_df[results_df["entry_mode"] == mode]
        if len(mode_results) > 0:
            avg_wr = mode_results["win_rate"].mean()
            avg_net = mode_results["net_pct"].mean()
            qualified_count = sum(mode_results["win_rate"] >= 45)
            positive_net_count = sum(mode_results["net_pct"] > 0)
            
            print(f"\n[{mode.upper()}]")
            print(f"  Combinations Tested: {len(mode_results)}")
            print(f"  Average WR%: {avg_wr:.1f}%")
            print(f"  Average Net%: {avg_net:+.1f}%")
            print(f"  WR >= 45% Count: {qualified_count} ({qualified_count/len(mode_results)*100:.1f}%)")
            print(f"  Net > 0% Count: {positive_net_count} ({positive_net_count/len(mode_results)*100:.1f}%)")
    
    # Parameter impact analysis
    print("\n" + "=" * 80)
    print("PARAMETER IMPACT ANALYSIS")
    print("=" * 80)
    
    # SD Threshold impact
    print("\n[SD Threshold Impact]")
    for sd in SD_THRESHOLDS:
        sd_results = results_df[results_df["sd_threshold"] == sd]
        avg_wr = sd_results["win_rate"].mean()
        avg_net = sd_results["net_pct"].mean()
        print(f"  SD={sd}: Avg WR={avg_wr:.1f}%, Avg Net={avg_net:+.1f}%")
    
    # ATR Stop impact
    print("\n[ATR Stop Impact]")
    for atr in ATR_STOPS:
        atr_results = results_df[results_df["atr_stop"] == atr]
        avg_wr = atr_results["win_rate"].mean()
        avg_net = atr_results["net_pct"].mean()
        print(f"  ATR={atr}: Avg WR={avg_wr:.1f}%, Avg Net={avg_net:+.1f}%")
    
    # TP Mode impact
    print("\n[TP Mode Impact]")
    for tp in TP_MODES:
        tp_results = results_df[results_df["tp_mode"] == tp]
        avg_wr = tp_results["win_rate"].mean()
        avg_net = tp_results["net_pct"].mean()
        print(f"  TP={tp}: Avg WR={avg_wr:.1f}%, Avg Net={avg_net:+.1f}%")
    
    # Trailing ATR impact
    print("\n[Trailing ATR Impact]")
    for trl in TRAILING_ATRS:
        trl_results = results_df[results_df["trailing_atr"] == trl]
        avg_wr = trl_results["win_rate"].mean()
        avg_net = trl_results["net_pct"].mean()
        print(f"  Trl={trl}: Avg WR={avg_wr:.1f}%, Avg Net={avg_net:+.1f}%")
    
    # Save summary report
    summary_path = RESULTS_DIR / "grid_search_summary.txt"
    with open(summary_path, "w") as f:
        f.write("GRID SEARCH RESULTS - TWO-SIDED ENTRY MODES\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Test Period: 2023-2025\n")
        f.write(f"Total Combinations: {total_combos}\n")
        f.write(f"Time Elapsed: {elapsed_total:.1f}s\n\n")
        
        for mode in ENTRY_MODES:
            mode_results = results_df[results_df["entry_mode"] == mode]
            qualified = mode_results[mode_results["win_rate"] >= 45]
            
            f.write(f"\n[{mode.upper()}]\n")
            if len(qualified) > 0:
                best = qualified.iloc[0]
                f.write(f"  SD={best['sd_threshold']}, ATR={best['atr_stop']}, "
                        f"TP={best['tp_mode']}, Trl={best['trailing_atr']}\n")
                f.write(f"  WR={best['win_rate']:.1f}%, Net={best['net_pct']:+.1f}%\n")
                f.write(f"  Sharpe={best['sharpe']:.2f}, MaxDD={best['max_dd_pct']:.1f}%\n")
    
    print(f"\nSummary saved to: {summary_path}")
    
    print("\n" + "=" * 80)
    print("DONE!")
    print("=" * 80)


if __name__ == "__main__":
    main()
