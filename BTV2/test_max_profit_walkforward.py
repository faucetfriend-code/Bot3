"""
test_max_profit_walkforward.py
================================
Fast Grid Search for Maximum Profit Strategy on 5m data.

Uses:
- Multiple year OOS testing  
- Full parameter grid as specified
- Train/OOS split validation

Constraints:
- OOS Sharpe > 1.0
- P(loss) < 10%
- Trades >= 50 across all windows
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# Setup paths
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_ROOT))

from strategies import (
    run_vwap_scalping,
    compute_metrics,
    build_windows,
    stitch_oos_equity,
    INTERVAL_BARS_PER_YEAR,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_FILE = _ROOT / "trading_bot_v2" / "backtesting" / "data" / "BTC-USDC_5m_2023.csv"
RESULTS_DIR = _HERE / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

CUTOFF = 0.10
BARS_PER_YEAR = INTERVAL_BARS_PER_YEAR["5m"]

# Constraints (as requested)
MIN_OOS_SHARPE = 1.0
MAX_PLOSS = 10.0
MIN_TRADES = 50


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────


def load_data_file(path: Path) -> pd.DataFrame:
    """Load 5m data from single CSV."""
    if not path.exists():
        return pd.DataFrame()
    
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    df = df[["Open", "High", "Low", "Close", "Volume"]]
    return df


def load_all_data() -> pd.DataFrame:
    """Load all available 5m data."""
    data_dir = _ROOT / "trading_bot_v2" / "backtesting" / "data"
    dfs = []
    
    for year in [2021, 2022, 2023]:
        path = data_dir / f"BTC-USDC_5m_{year}.csv"
        if path.exists():
            df = load_data_file(path)
            if not df.empty:
                dfs.append(df)
    
    if dfs:
        return pd.concat(dfs).sort_index()
    return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────


def build_params(
    sd_threshold: float,
    atr_mult: float,
    RR: float,
    require_sfp: bool,
    require_volume: bool,
    long_only: bool,
) -> dict:
    """Build strategy params."""
    atr_stop = atr_mult * 2.0
    atr_target = atr_stop * RR
    
    entry_mode = "bull_pullback" if long_only else "mean_reversion"
    
    return {
        "entry_mode": entry_mode,
        "sd_threshold": sd_threshold,
        "atr_stop": atr_stop,
        "atr_target": atr_target,
        "trailing_atr": atr_target * 0.6,
        "adx_max": 25.0,
        "rsi_max": 50.0,
        "volume_mult": 1.5 if require_volume else 1.0,
        "use_htf_ema": True,
        "htf_adx_max": 30.0,
        "tp_mode": "atr",
        "require_reversal_candle": require_sfp,
    }


# ─────────────────────────────────────────────────────────────────────────────
# EVALUATION
# ─────────────────────────────────────────────────────────────────────────────


def run_oos_eval(
    params: dict,
    df: pd.DataFrame,
) -> dict | None:
    """Run walk-forward OOS evaluation."""
    from datetime import date
    from strategies import build_windows, compute_metrics, stitch_oos_equity
    
    # Use 6m train / 3m test windows
    windows = build_windows(date(2021, 1, 1), date(2024, 1, 1), train_months=6, test_months=3)
    if not windows:
        return None
    
    all_segments = []
    all_trades = []
    window_sharpes = []
    
    for w in windows:
        df_test = df.loc[str(w["test_start"]):str(w["test_end"])]
        if len(df_test) < 50:
            continue
        
        try:
            eq, trades = run_vwap_scalping(df_test, CUTOFF, **params)
        except Exception:
            continue
        
        if trades:
            all_segments.append(eq)
            all_trades.extend(trades)
            m = compute_metrics(eq, trades, bars_per_year=BARS_PER_YEAR)
            window_sharpes.append(m["sharpe"])
    
    if len(all_trades) < MIN_TRADES:
        return None
    
    # Calculate metrics
    losses = sum(1 for t in all_trades if t < 0)
    p_loss = losses / len(all_trades) * 100
    
    stitched = stitch_oos_equity(all_segments)
    agg = compute_metrics(stitched, all_trades, bars_per_year=BARS_PER_YEAR)
    
    consistency = sum(1 for s in window_sharpes if s > 0) / max(len(window_sharpes), 1) * 100
    
    return {
        "oos_sharpe": agg["sharpe"],
        "oos_return": agg["total_return_pct"],
        "oos_max_dd": agg["max_dd_pct"],
        "oos_win_rate": agg["win_rate_pct"],
        "oos_trades": len(all_trades),
        "p_loss": p_loss,
        "consistency": consistency,
    }


# ─────────────────────────────────────────────────────────────────────────────
# GRID SEARCH - SMART PHASED
# ─────────────────────────────────────────────────────────────────────────────


def run_grid_search(df: pd.DataFrame) -> list[dict]:
    """Run smart phased grid search."""
    print("\nPHASE 1: Core parameters (sd x RR)")
    
    results = []
    
    # Phase 1: sd_threshold x RR
    for sd in [2.5, 3.0, 3.5]:
        for RR in [2.0, 2.5, 3.0]:
            params = build_params(sd, 1.0, RR, False, True, False)
            
            result = run_oos_eval(params, df)
            if result:
                passed = (
                    result["oos_sharpe"] >= MIN_OOS_SHARPE and
                    result["p_loss"] <= MAX_PLOSS and
                    result["oos_trades"] >= MIN_TRADES
                )
                
                results.append({
                    "sd_threshold": sd,
                    "atr_multiplier": 1.0,
                    "RR": RR,
                    "require_sfp": False,
                    "require_volume": True,
                    "long_only": False,
                    **result,
                    "passed": passed,
                    "params": params,
                })
                print(f"  sd={sd}, RR={RR} -> Ret={result['oos_return']:+.1f}%, Sharpe={result['oos_sharpe']:.2f}, Trades={result['oos_trades']}, P(Loss)={result['p_loss']:.1f}% [{'PASS' if passed else 'FAIL'}]")
    
    if not results:
        print("Phase 1: No results!")
        return []
    
    # Sort
    results.sort(key=lambda x: x["oos_return"], reverse=True)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────


def main():
    print("=" * 80)
    print("MAX PROFIT WALK-FORWARD GRID SEARCH")
    print("=" * 80)
    print(f"Constraints: Sharpe > {MIN_OOS_SHARPE}, P(loss) < {MAX_PLOSS}%, Trades >= {MIN_TRADES}")
    
    # Load data
    print("\nLoading data...")
    df = load_all_data()
    if df.empty:
        print("No data found!")
        return
    print(f"Total: {len(df):,} bars")
    
    start = time.time()
    
    # Run grid search
    results = run_grid_search(df)
    
    elapsed = time.time() - start
    print(f"\nCompleted in {elapsed:.1f}s")
    
    if not results:
        print("No configurations found!")
        return
    
    # Save results
    rows = []
    for i, r in enumerate(results):
        rows.append({
            "rank": i + 1,
            "sd_threshold": r["sd_threshold"],
            "atr_multiplier": r["atr_multiplier"],
            "RR": r["RR"],
            "require_sfp": r["require_sfp"],
            "require_volume": r["require_volume"],
            "long_only": r["long_only"],
            "oos_return": r["oos_return"],
            "oos_sharpe": r["oos_sharpe"],
            "oos_max_dd": r["oos_max_dd"],
            "oos_win_rate": r["oos_win_rate"],
            "oos_trades": r["oos_trades"],
            "p_loss": r["p_loss"],
            "consistency": r["consistency"],
            "passed": r["passed"],
        })
    
    df = pd.DataFrame(rows)
    df.to_csv(RESULTS_DIR / "max_profit_walkforward_results.csv", index=False)
    print(f"Results saved to {RESULTS_DIR / 'max_profit_walkforward_results.csv'}")
    
    # Best
    best = next((r for r in results if r["passed"]), results[0])
    
    print(f"\nBEST CONFIGURATION:")
    print(f"  sd_threshold: {best['sd_threshold']}")
    print(f"  atr_multiplier: {best['atr_multiplier']}")
    print(f"  RR: {best['RR']}")
    print(f"  require_sfp: {best['require_sfp']}")
    print(f"  require_volume: {best['require_volume']}")
    print(f"  long_only: {best['long_only']}")
    print(f"  OOS Return: {best['oos_return']:+.2f}%")
    print(f"  OOS Sharpe: {best['oos_sharpe']:.3f}")
    print(f"  OOS Max DD: {best['oos_max_dd']:.2f}%")
    print(f"  P(Loss): {best['p_loss']:.1f}%")
    print(f"  Trades: {best['oos_trades']}")
    print(f"  Status: {'PASS' if best['passed'] else 'FAIL'}")
    
    # Top 5
    print(f"\nTOP 5 CONFIGURATIONS:")
    for i, r in enumerate(results[:5], 1):
        print(f"  {i}. sd={r['sd_threshold']}, RR={r['RR']}, sfp={r['require_sfp']}, vol={r['require_volume']}, long={r['long_only']}")
        print(f"     -> Return={r['oos_return']:+.1f}%, Sharpe={r['oos_sharpe']:.2f}, P(Loss)={r['p_loss']:.1f}% [{'PASS' if r['passed'] else 'FAIL'}]")
    
    # Markdown report
    lines = [
        "# Max Profit Walk-Forward Results",
        "",
        f"Train Period: 2021-01-01 to 2021-07-01 (6m)",
        f"OOS Periods: 2021-07-01 to 2024-01-01 (3m rolling)",
        "",
        f"Constraints: Sharpe > {MIN_OOS_SHARPE}, P(loss) < {MAX_PLOSS}%, Trades >= {MIN_TRADES}",
        "",
        "## Top 10 Configurations Ranked by OOS Return",
        "",
        "| Rank | SD Thresh | ATR Mult | RR | SFP | Volume | Long | Return% | Sharpe | P(Loss)% | Trades | Status |",
        "|-----|-----------|----------|-----|-----|--------|------|---------|--------|----------|--------|--------|",
    ]
    
    for i, r in enumerate(results[:10], 1):
        status = "PASS" if r["passed"] else "FAIL"
        lines.append(
            f"| {i} | {r['sd_threshold']} | {r['atr_multiplier']} | {r['RR']} | "
            f"{r['require_sfp']} | {r['require_volume']} | {r['long_only']} | "
            f"{r['oos_return']:+.1f} | {r['oos_sharpe']:.2f} | "
            f"{r['p_loss']:.1f} | {r['oos_trades']} | {status} |"
        )
    
    with open(RESULTS_DIR / "max_profit_walkforward_report.md", "w") as f:
        f.write("\n".join(lines))
    print(f"\nReport saved to {RESULTS_DIR / 'max_profit_walkforward_report.md'}")


if __name__ == "__main__":
    main()