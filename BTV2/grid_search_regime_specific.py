"""
grid_search_regime_specific.py
==============================
Regime-specific grid search to find optimal parameters per regime type.
Run after the main grid search to find regime-specific best combinations.
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
    COST_PER_SIDE,
    INTERVAL_BARS_PER_YEAR,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"

TEST_START = "2023-01-01"
TEST_END = "2026-01-01"
CUTOFF = 0.10

# GRID PARAMETERS
ENTRY_MODES = ["mean_reversion", "cross"]
SD_THRESHOLDS = [2.5, 3.0, 3.5]
ATR_STOPS = [1.5, 2.0, 2.5]
TP_MODES = ["atr", "pdh"]
TRAILING_ATRS = [1.2, 1.5]

# Base parameters
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

# Regime filters for separate testing
REGIME_FILTERS = {
    "RANGING": [MarketRegime.RANGING],
    "BEAR_WEAK": [MarketRegime.BEAR_WEAK],
    "BEAR_STRONG": [MarketRegime.BEAR_STRONG],
    "BULL_WEAK": [MarketRegime.BULL_WEAK],
    "BULL_STRONG": [MarketRegime.BULL_STRONG],
}


def load_5m_data(start: str, end: str) -> pd.DataFrame:
    path = DATA_DIR / "BTCUSDT_5m.parquet"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df.index >= start_ts) & (df.index < end_ts)
    return df.loc[mask].copy()


def load_1m_data(start: str, end: str) -> pd.DataFrame | None:
    path = DATA_DIR / "BTCUSDT_1m.parquet"
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df.index >= start_ts) & (df.index < end_ts)
    return df.loc[mask].copy()


def compute_metrics_for_result(equity: pd.Series, trades: list) -> Dict[str, float]:
    if len(equity) < 2 or len(trades) == 0:
        wins = [t for t in trades if t > 0]
        return {
            "trades": len(trades),
            "win_rate": len(wins) / max(len(trades), 1) * 100,
            "profit_factor": 0.0,
            "net_pct": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
            "avg_win_pct": 0.0,
            "avg_loss_pct": 0.0,
        }
    
    import math
    bars_per_year = INTERVAL_BARS_PER_YEAR.get("5m", 105120)
    
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
) -> Dict[str, Any]:
    """Run a single backtest."""
    
    df_copy = df.copy()
    df_copy.index = df_copy.index.tz_localize(None)
    if df_exit is not None:
        df_exit_copy = df_exit.copy()
        df_exit_copy.index = df_exit_copy.index.tz_localize(None)
    else:
        df_exit_copy = None
    regime_copy = regime_series.copy()
    regime_copy.index = regime_copy.index.tz_localize(None)
    
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
    
    equity, trades = run_vwap_scalping(df_copy, CUTOFF, df_exit=df_exit_copy, **params)
    metrics = compute_metrics_for_result(equity, trades)
    
    return metrics


def main():
    print("=" * 80)
    print("REGIME-SPECIFIC GRID SEARCH")
    print("=" * 80)
    print("")
    
    # Load data
    print("Loading data...")
    df_5m = load_5m_data(TEST_START, TEST_END)
    if df_5m.empty:
        print("ERROR: No data!")
        return
    print(f"  5m: {len(df_5m):,} bars")
    
    df_1m = load_1m_data(TEST_START, TEST_END)
    if df_1m is not None:
        print(f"  1m: {len(df_1m):,} bars")
    
    # Compute regimes
    print("\nComputing regimes...")
    detector = RegimeDetector(df_5m, method="combined", timeframe="15m")
    regime_series = detector.get_regimes()
    
    # Grid
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
    
    print(f"\nGrid: {total_combos} combos per regime")
    print(f"Regimes: {list(REGIME_FILTERS.keys())}")
    print(f"Total runs: {total_combos * len(REGIME_FILTERS)}")
    print("")
    
    # Run for each regime
    all_results = []
    start_time = time.time()
    
    for regime_name, allowed_regimes in REGIME_FILTERS.items():
        print(f"\n{'='*50}")
        print(f"REGIME: {regime_name}")
        print(f"{'='*50}")
        
        regime_results = []
        
        for i, combo in enumerate(all_combinations):
            if (i + 1) % 20 == 0:
                print(f"  Progress: {i+1}/{total_combos}")
            
            params = dict(zip(keys, combo))
            
            metrics = run_backtest(
                df=df_5m,
                df_exit=df_1m,
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
                **metrics,
            }
            
            # Score
            wr_bonus = max(0, metrics["win_rate"] - 45)
            net_score = metrics["net_pct"]
            result["score"] = wr_bonus + net_score
            
            regime_results.append(result)
        
        all_results.extend(regime_results)
        
        # Show best for this regime
        regime_df = pd.DataFrame(regime_results)
        regime_df_sorted = regime_df.sort_values("score", ascending=False)
        
        best = regime_df_sorted.iloc[0]
        qualified = regime_df_sorted[regime_df_sorted["win_rate"] >= 45]
        
        print(f"\n  Best Result:")
        print(f"    Entry: {best['entry_mode']}")
        print(f"    SD={best['sd_threshold']}, ATR={best['atr_stop']}, TP={best['tp_mode']}, Trl={best['trailing_atr']}")
        print(f"    WR={best['win_rate']:.1f}%, Net={best['net_pct']:+.1f}%, Trades={best['trades']}")
        
        if len(qualified) > 0:
            print(f"\n  Qualified (WR >= 45%): {len(qualified)} combos")
            for j, (_, r) in enumerate(qualified.head(5).iterrows()):
                print(f"    {j+1}. {r['entry_mode']} SD={r['sd_threshold']} ATR={r['atr_stop']} TP={r['tp_mode']} Trl={r['trailing_atr']}")
                print(f"       WR={r['win_rate']:.1f}%, Net={r['net_pct']:+.1f}%, PF={r['profit_factor']:.2f}")
        else:
            print(f"\n  No combos with WR >= 45%")
            print(f"  Best WR available: {regime_df['win_rate'].max():.1f}%")
    
    elapsed = time.time() - start_time
    print(f"\n\nTotal time: {elapsed:.1f}s")
    
    # Save all results
    results_df = pd.DataFrame(all_results)
    output_csv = RESULTS_DIR / "grid_search_regime_specific.csv"
    results_df.to_csv(output_csv, index=False)
    print(f"\nSaved to: {output_csv}")
    
    # Final summary
    print("\n" + "=" * 80)
    print("FINAL SUMMARY - BEST PER REGIME")
    print("=" * 80)
    
    summary = {}
    for regime_name in REGIME_FILTERS.keys():
        regime_df = results_df[results_df["regime"] == regime_name]
        qualified = regime_df[regime_df["win_rate"] >= 45]
        
        if len(qualified) > 0:
            best = qualified.sort_values("score", ascending=False).iloc[0]
        else:
            best = regime_df.sort_values("win_rate", ascending=False).iloc[0]
        
        summary[regime_name] = best
        
        print(f"\n[{regime_name}]")
        print(f"  Entry Mode: {best['entry_mode']}")
        print(f"  SD={best['sd_threshold']}, ATR={best['atr_stop']}, TP={best['tp_mode']}, Trl={best['trailing_atr']}")
        print(f"  WR={best['win_rate']:.1f}%, Net={best['net_pct']:+.1f}%")
        print(f"  Trades={best['trades']}, Sharpe={best['sharpe']:.2f}, MaxDD={best['max_dd_pct']:.1f}%")
        print(f"  Qualified (WR>=45%): {len(qualified)}")
    
    # Best by entry mode overall
    print("\n" + "=" * 80)
    print("BEST BY ENTRY MODE (Across All Regimes)")
    print("=" * 80)
    
    for mode in ENTRY_MODES:
        mode_results = results_df[results_df["entry_mode"] == mode]
        qualified = mode_results[mode_results["win_rate"] >= 45]
        
        print(f"\n[{mode.upper()}]")
        print(f"  Total combos: {len(mode_results)}")
        print(f"  WR >= 45%: {len(qualified)} ({len(qualified)/len(mode_results)*100:.1f}%)")
        
        if len(qualified) > 0:
            best = qualified.sort_values("score", ascending=False).iloc[0]
            print(f"  Best Qualified:")
            print(f"    Regime: {best['regime']}")
            print(f"    SD={best['sd_threshold']}, ATR={best['atr_stop']}, TP={best['tp_mode']}, Trl={best['trailing_atr']}")
            print(f"    WR={best['win_rate']:.1f}%, Net={best['net_pct']:+.1f}%")
        else:
            best = mode_results.sort_values("win_rate", ascending=False).iloc[0]
            print(f"  Best Available (no WR >= 45%):")
            print(f"    Regime: {best['regime']}")
            print(f"    WR={best['win_rate']:.1f}%, Net={best['net_pct']:+.1f}%")
    
    # Parameter impact
    print("\n" + "=" * 80)
    print("PARAMETER IMPACT BY REGIME")
    print("=" * 80)
    
    for regime_name in REGIME_FILTERS.keys():
        regime_df = results_df[results_df["regime"] == regime_name]
        
        print(f"\n[{regime_name}]")
        
        # SD Impact
        print("\n  SD Threshold Impact:")
        for sd in SD_THRESHOLDS:
            sd_df = regime_df[regime_df["sd_threshold"] == sd]
            if len(sd_df) > 0:
                print(f"    SD={sd}: Avg WR={sd_df['win_rate'].mean():.1f}%, Best WR={sd_df['win_rate'].max():.1f}%")
        
        # ATR Stop Impact
        print("\n  ATR Stop Impact:")
        for atr in ATR_STOPS:
            atr_df = regime_df[regime_df["atr_stop"] == atr]
            if len(atr_df) > 0:
                print(f"    ATR={atr}: Avg WR={atr_df['win_rate'].mean():.1f}%, Best WR={atr_df['win_rate'].max():.1f}%")
        
        # TP Mode Impact
        print("\n  TP Mode Impact:")
        for tp in TP_MODES:
            tp_df = regime_df[regime_df["tp_mode"] == tp]
            if len(tp_df) > 0:
                print(f"    TP={tp}: Avg WR={tp_df['win_rate'].mean():.1f}%, Best WR={tp_df['win_rate'].max():.1f}%")
    
    # Save summary
    summary_path = RESULTS_DIR / "grid_search_regime_summary.txt"
    with open(summary_path, "w") as f:
        f.write("REGIME-SPECIFIC GRID SEARCH SUMMARY\n")
        f.write("=" * 60 + "\n\n")
        
        f.write("BEST PARAMETERS PER REGIME:\n")
        for regime_name, best in summary.items():
            f.write(f"\n[{regime_name}]\n")
            f.write(f"  Entry Mode: {best['entry_mode']}\n")
            f.write(f"  SD={best['sd_threshold']}, ATR={best['atr_stop']}, "
                    f"TP={best['tp_mode']}, Trl={best['trailing_atr']}\n")
            f.write(f"  WR={best['win_rate']:.1f}%, Net={best['net_pct']:+.1f}%\n")
        
        f.write("\n\nRECOMMENDED COMBINATIONS:\n")
        for mode in ENTRY_MODES:
            f.write(f"\n[{mode.upper()}]\n")
            mode_best = results_df[results_df["entry_mode"] == mode].sort_values("score", ascending=False)
            if len(mode_best) > 0:
                best = mode_best.iloc[0]
                f.write(f"  Regime: {best['regime']}\n")
                f.write(f"  SD={best['sd_threshold']}, ATR={best['atr_stop']}, "
                        f"TP={best['tp_mode']}, Trl={best['trailing_atr']}\n")
                f.write(f"  WR={best['win_rate']:.1f}%, Net={best['net_pct']:+.1f}%\n")
    
    print(f"\nSummary saved to: {summary_path}")
    print("\n" + "=" * 80)
    print("DONE!")
    print("=" * 80)


if __name__ == "__main__":
    main()
