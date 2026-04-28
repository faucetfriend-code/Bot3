"""
grid_search_two_sided_entry.py
================================
Comprehensive grid search on two-sided entry modes:
- mean_reversion: fade extremes (long at lows, short at highs)
- cross: breakout trades (long breakouts, short breakdowns)

Test all combinations of:
- entry_mode: [mean_reversion, cross]
- sd_threshold: [2.5, 3.0, 3.5]
- atr_stop: [1.5, 2.0, 2.5]
- tp_mode: [atr, pdh]
- trailing_atr: [1.2, 1.5]

For each combination:
- Test in each regime type (RANGING, BEAR, BULL)
- Track WR%, Net%, trades per regime

Goal: Find best params for each entry mode that gives:
- WR > 45%
- Positive or minimal loss

Period: 2023-2025
"""

from __future__ import annotations

import sys
import time
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
# GRID PARAMETERS
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

# Regime filters
REGIME_FILTERS = {
    "RANGING": [MarketRegime.RANGING],
    "BEAR": [MarketRegime.BEAR_WEAK, MarketRegime.BEAR_STRONG],
    "BULL": [MarketRegime.BULL_WEAK, MarketRegime.BULL_STRONG],
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

def compute_win_rate(trades: List[float]) -> float:
    """Compute win rate from list of P&L values."""
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t > 0)
    return wins / len(trades) * 100


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
    
    metrics = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR.get("5m", 105120))
    
    return {
        "trades": metrics.get("n_trades", 0),
        "win_rate": metrics.get("win_rate_pct", 0.0),
        "profit_factor": metrics.get("profit_factor", 0.0),
        "net_pct": metrics.get("total_return_pct", 0.0),
        "sharpe": metrics.get("sharpe", 0.0),
        "max_drawdown_pct": metrics.get("max_dd_pct", 0.0),
        "avg_win_pct": metrics.get("avg_win_pct", 0.0),
        "avg_loss_pct": metrics.get("avg_loss_pct", 0.0),
    }


# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(
    df: pd.DataFrame,
    df_exit: pd.DataFrame | None,
    regime_series: pd.Series,
    entry_mode: str,
    sd_threshold: float,
    atr_stop: float,
    tp_mode: str,
    trailing_atr: float,
    allowed_regimes: List[MarketRegime],
) -> Tuple[pd.Series, list, Dict[str, float]]:
    """Run VWAP scalping backtest with specific parameters."""
    
    # Remove timezone for compatibility
    df_copy = df.copy()
    df_copy.index = df_copy.index.tz_localize(None)
    if df_exit is not None:
        df_exit_copy = df_exit.copy()
        df_exit_copy.index = df_exit_copy.index.tz_localize(None)
    else:
        df_exit_copy = None
    regime_copy = regime_series.copy()
    regime_copy.index = regime_copy.index.tz_localize(None)
    
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
        "allowed_regimes": allowed_regimes,
    })
    
    equity, trades = run_vwap_scalping(
        df_copy,
        CUTOFF,
        df_exit=df_exit_copy,
        **params,
    )
    
    metrics = compute_metrics_for_result(equity, trades)
    return equity, trades, metrics


def run_grid_for_regime(
    df: pd.DataFrame,
    df_exit: pd.DataFrame | None,
    regime_series: pd.Series,
    regime_name: str,
    allowed_regimes: List[MarketRegime],
    grid_params: Dict[str, list],
) -> List[Dict[str, Any]]:
    """Run grid search for a specific regime filter."""
    
    results = []
    keys = list(grid_params.keys())
    vals = list(grid_params.values())
    total_combos = len(list(product(*vals)))
    
    print(f"\n  Running {total_combos} combinations for regime: {regime_name}")
    start_time = time.time()
    combo_count = 0
    
    for combo in product(*vals):
        params = dict(zip(keys, combo))
        combo_count += 1
        
        if combo_count % 20 == 0:
            elapsed = time.time() - start_time
            eta = (elapsed / combo_count) * (total_combos - combo_count)
            print(f"    Progress: {combo_count}/{total_combos} ({combo_count/total_combos*100:.1f}%) - ETA: {eta:.0f}s")
        
        equity, trades, metrics = run_backtest(
            df=df,
            df_exit=df_exit,
            regime_series=regime_series,
            entry_mode=params["entry_mode"],
            sd_threshold=params["sd_threshold"],
            atr_stop=params["atr_stop"],
            tp_mode=params["tp_mode"],
            trailing_atr=params["trailing_atr"],
            allowed_regimes=allowed_regimes,
        )
        
        result = {
            "regime": regime_name,
            "entry_mode": params["entry_mode"],
            "sd_threshold": params["sd_threshold"],
            "atr_stop": params["atr_stop"],
            "tp_mode": params["tp_mode"],
            "trailing_atr": params["trailing_atr"],
            "trades": metrics["trades"],
            "win_rate": metrics["win_rate"],
            "net_pct": metrics["net_pct"],
            "sharpe": metrics["sharpe"],
            "max_dd_pct": metrics["max_drawdown_pct"],
            "profit_factor": metrics["profit_factor"],
            "avg_win_pct": metrics["avg_win_pct"],
            "avg_loss_pct": metrics["avg_loss_pct"],
        }
        
        # Calculate a composite score (WR% - 45%, prioritize positive returns)
        wr_bonus = max(0, metrics["win_rate"] - 45)  # Bonus for WR > 45%
        net_score = metrics["net_pct"]  # Net can be positive or negative
        result["score"] = wr_bonus + net_score
        
        results.append(result)
    
    elapsed = time.time() - start_time
    print(f"  Completed in {elapsed:.1f}s")
    
    return results


# ─────────────────────────────────────────────────────────────────────────────
# ANALYSIS FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def find_best_per_entry_mode(
    results: List[Dict[str, Any]],
    min_wr: float = 45.0,
    max_loss: float = 0.0,
) -> Dict[str, Dict[str, Any]]:
    """Find best parameter combination for each entry mode."""
    
    best_by_mode = {}
    
    for mode in ENTRY_MODES:
        mode_results = [r for r in results if r["entry_mode"] == mode]
        
        if not mode_results:
            continue
        
        # Filter for WR > min_wr and Net >= max_loss
        qualified = [
            r for r in mode_results
            if r["win_rate"] >= min_wr and r["net_pct"] >= max_loss
        ]
        
        if qualified:
            # Sort by score (WR bonus + net)
            qualified.sort(key=lambda x: x["score"], reverse=True)
            best = qualified[0]
        else:
            # Fall back to highest WR if no qualified results
            mode_results.sort(key=lambda x: x["win_rate"], reverse=True)
            best = mode_results[0]
        
        best_by_mode[mode] = best
    
    return best_by_mode


def find_best_per_regime(
    results: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Find best parameter combination for each regime type."""
    
    best_by_regime = {}
    
    for regime in REGIME_FILTERS.keys():
        regime_results = [r for r in results if r["regime"] == regime]
        
        if not regime_results:
            continue
        
        # Filter for WR > 45%
        qualified = [r for r in regime_results if r["win_rate"] >= 45.0]
        
        if qualified:
            # Sort by score
            qualified.sort(key=lambda x: x["score"], reverse=True)
            best = qualified[0]
        else:
            # Fall back to highest WR
            regime_results.sort(key=lambda x: x["win_rate"], reverse=True)
            best = regime_results[0]
        
        best_by_regime[regime] = best
    
    return best_by_regime


def generate_summary_report(
    all_results: List[Dict[str, Any]],
    best_by_mode: Dict[str, Dict[str, Any]],
    best_by_regime: Dict[str, Dict[str, Any]],
) -> str:
    """Generate a comprehensive summary report."""
    
    report = []
    report.append("=" * 80)
    report.append("GRID SEARCH RESULTS - TWO-SIDED ENTRY MODES")
    report.append("=" * 80)
    report.append("")
    report.append(f"Test Period: 2023-2025")
    report.append(f"Total Combinations Tested: {len(all_results)}")
    report.append(f"Entry Modes: {ENTRY_MODES}")
    report.append(f"SD Thresholds: {SD_THRESHOLDS}")
    report.append(f"ATR Stops: {ATR_STOPS}")
    report.append(f"TP Modes: {TP_MODES}")
    report.append(f"Trailing ATRs: {TRAILING_ATRS}")
    report.append("")
    
    # Best by Entry Mode
    report.append("=" * 80)
    report.append("BEST PARAMETERS BY ENTRY MODE (WR > 45%, minimal loss)")
    report.append("=" * 80)
    
    for mode, best in best_by_mode.items():
        report.append("")
        report.append(f"[{mode.upper()}]")
        report.append(f"  SD Threshold:  {best['sd_threshold']}")
        report.append(f"  ATR Stop:     {best['atr_stop']}")
        report.append(f"  TP Mode:      {best['tp_mode']}")
        report.append(f"  Trailing ATR: {best['trailing_atr']}")
        report.append(f"  Regime:       {best['regime']}")
        report.append(f"  ──────────────")
        report.append(f"  Trades:       {best['trades']}")
        report.append(f"  Win Rate:     {best['win_rate']:.1f}%")
        report.append(f"  Net %:        {best['net_pct']:+.1f}%")
        report.append(f"  Sharpe:       {best['sharpe']:.2f}")
        report.append(f"  Max DD:       {best['max_dd_pct']:.1f}%")
        report.append(f"  Profit Factor: {best['profit_factor']:.2f}")
    
    # Best by Regime
    report.append("")
    report.append("=" * 80)
    report.append("BEST PARAMETERS BY REGIME (WR > 45%)")
    report.append("=" * 80)
    
    for regime, best in best_by_regime.items():
        report.append("")
        report.append(f"[{regime}]")
        report.append(f"  Entry Mode:   {best['entry_mode']}")
        report.append(f"  SD Threshold:  {best['sd_threshold']}")
        report.append(f"  ATR Stop:     {best['atr_stop']}")
        report.append(f"  TP Mode:      {best['tp_mode']}")
        report.append(f"  Trailing ATR: {best['trailing_atr']}")
        report.append(f"  ──────────────")
        report.append(f"  Trades:       {best['trades']}")
        report.append(f"  Win Rate:     {best['win_rate']:.1f}%")
        report.append(f"  Net %:        {best['net_pct']:+.1f}%")
        report.append(f"  Sharpe:       {best['sharpe']:.2f}")
        report.append(f"  Max DD:       {best['max_dd_pct']:.1f}%")
    
    # Top 10 Overall
    report.append("")
    report.append("=" * 80)
    report.append("TOP 10 PARAMETER COMBINATIONS (All Regimes)")
    report.append("=" * 80)
    
    sorted_results = sorted(all_results, key=lambda x: x["score"], reverse=True)[:10]
    
    report.append("")
    report.append(f"{'#':<3} {'Entry Mode':<15} {'SD':>5} {'ATR':>5} {'TP':>5} {'Trl':>5} {'Regime':<8} {'Trades':>6} {'WR%':>6} {'Net%':>8} {'Sharpe':>7}")
    report.append("-" * 90)
    
    for i, r in enumerate(sorted_results, 1):
        report.append(
            f"{i:<3} {r['entry_mode']:<15} {r['sd_threshold']:>5.1f} {r['atr_stop']:>5.1f} "
            f"{r['tp_mode']:<5} {r['trailing_atr']:>5.1f} {r['regime']:<8} "
            f"{r['trades']:>6} {r['win_rate']:>6.1f} {r['net_pct']:>+8.1f} {r['sharpe']:>7.2f}"
        )
    
    # Statistics Summary
    report.append("")
    report.append("=" * 80)
    report.append("STATISTICS SUMMARY")
    report.append("=" * 80)
    
    for mode in ENTRY_MODES:
        mode_results = [r for r in all_results if r["entry_mode"] == mode]
        if mode_results:
            avg_wr = np.mean([r["win_rate"] for r in mode_results])
            avg_net = np.mean([r["net_pct"] for r in mode_results])
            qualified_count = sum(1 for r in mode_results if r["win_rate"] >= 45)
            
            report.append("")
            report.append(f"[{mode.upper()}]")
            report.append(f"  Combinations Tested: {len(mode_results)}")
            report.append(f"  Average WR%: {avg_wr:.1f}%")
            report.append(f"  Average Net%: {avg_net:+.1f}%")
            report.append(f"  WR > 45% Count: {qualified_count} ({qualified_count/len(mode_results)*100:.1f}%)")
    
    return "\n".join(report)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("COMPREHENSIVE GRID SEARCH - TWO-SIDED ENTRY MODES")
    print("=" * 80)
    print("")
    print("Entry Modes: mean_reversion, cross")
    print(f"SD Thresholds: {SD_THRESHOLDS}")
    print(f"ATR Stops: {ATR_STOPS}")
    print(f"TP Modes: {TP_MODES}")
    print(f"Trailing ATRs: {TRAILING_ATRS}")
    print(f"Test Period: 2023-2025")
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
    regime_dist = regime_series.value_counts()
    print("\nRegime Distribution:")
    for r in MarketRegime:
        cnt = regime_dist.get(r, 0)
        print(f"  {r.value}: {cnt} bars ({cnt/len(regime_series)*100:.1f}%)")
    
    # Grid parameters
    grid_params = {
        "entry_mode": ENTRY_MODES,
        "sd_threshold": SD_THRESHOLDS,
        "atr_stop": ATR_STOPS,
        "tp_mode": TP_MODES,
        "trailing_atr": TRAILING_ATRS,
    }
    
    total_combos = len(list(product(*list(grid_params.values()))))
    total_runs = total_combos * len(REGIME_FILTERS)
    print(f"\nGrid Search Configuration:")
    print(f"  Total combinations per regime: {total_combos}")
    print(f"  Regimes to test: {list(REGIME_FILTERS.keys())}")
    print(f"  Total backtests to run: {total_runs}")
    print("")
    
    # Run grid search for each regime
    all_results = []
    
    for regime_name, allowed_regimes in REGIME_FILTERS.items():
        print(f"\n{'='*60}")
        print(f"TESTING REGIME: {regime_name}")
        print(f"{'='*60}")
        
        regime_results = run_grid_for_regime(
            df=df_5m,
            df_exit=df_1m,
            regime_series=regime_series,
            regime_name=regime_name,
            allowed_regimes=allowed_regimes,
            grid_params=grid_params,
        )
        
        all_results.extend(regime_results)
        
        # Show interim results for this regime
        regime_results_sorted = sorted(regime_results, key=lambda x: x["score"], reverse=True)[:5]
        print(f"\n  Top 5 for {regime_name}:")
        for i, r in enumerate(regime_results_sorted, 1):
            print(f"    {i}. {r['entry_mode']} SD={r['sd_threshold']:.1f} ATR={r['atr_stop']:.1f} "
                  f"TP={r['tp_mode']} Trl={r['trailing_atr']:.1f} - "
                  f"WR={r['win_rate']:.1f}% Net={r['net_pct']:+.1f}% Trades={r['trades']}")
    
    # Convert to DataFrame
    results_df = pd.DataFrame(all_results)
    
    # Save full results
    output_csv = RESULTS_DIR / "grid_search_two_sided_entry_full.csv"
    results_df.to_csv(output_csv, index=False)
    print(f"\n\nFull results saved to: {output_csv}")
    
    # Find best parameters
    print("\n" + "=" * 80)
    print("ANALYZING RESULTS")
    print("=" * 80)
    
    best_by_mode = find_best_per_entry_mode(all_results, min_wr=45.0, max_loss=0.0)
    best_by_regime = find_best_per_regime(all_results)
    
    # Generate summary report
    summary_report = generate_summary_report(all_results, best_by_mode, best_by_regime)
    
    # Save summary report
    output_report = RESULTS_DIR / "grid_search_two_sided_entry_summary.txt"
    with open(output_report, "w") as f:
        f.write(summary_report)
    print(f"\nSummary report saved to: {output_report}")
    
    # Print summary
    print("\n" + summary_report)
    
    # Print final recommendations
    print("\n" + "=" * 80)
    print("FINAL RECOMMENDATIONS")
    print("=" * 80)
    
    for mode, best in best_by_mode.items():
        print(f"\n{mode.upper()}:")
        print(f"  Recommended params: SD={best['sd_threshold']}, ATR_stop={best['atr_stop']}, "
              f"TP={best['tp_mode']}, Trailing={best['trailing_atr']}")
        print(f"  Expected WR: {best['win_rate']:.1f}%")
        print(f"  Expected Net: {best['net_pct']:+.1f}%")
        print(f"  Best in regime: {best['regime']}")
    
    print("\n" + "=" * 80)
    print("DONE!")
    print("=" * 80)


if __name__ == "__main__":
    main()
