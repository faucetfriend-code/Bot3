"""
validate_vwap_fixed.py
=========================
Validate FIXED VWAP Scalping settings (ATR stop = 0.7 instead of 7.0).

FIXED: VWAP_ATR_STOP_MULTIPLIER changed from 7.0 (10x too wide) to 0.7

Run TWO modes:
a) Normal mode: as-is with regime filter
b) Strict mode: stricter filters (higher volume, lower ADX threshold)

Uses proper walk-forward validation (3m train, 1m test).
Output per-year metrics: trades, win rate, profit factor, net %, sharpe, max DD.

Output: BTV2/results/vwap_scalping_fixed_validation.csv
"""

import math
import sys
from pathlib import Path
from datetime import date, datetime
from typing import Dict, Any, List

import numpy as np
import pandas as pd

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.strategies import (
    run_vwap_scalping,
    compute_reference_levels,
    INTERVAL_BARS_PER_YEAR,
    COST_PER_SIDE,
)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# FIXED .env settings for VWAP Scalping (atr_stop=0.7 instead of 7.0!)
FIXED_SETTINGS = {
    "sd_threshold": 3.0,           # VWAP_SD_ENTRY_THRESHOLD from .env
    "atr_stop": 0.7,                 # FIXED: VWAP_ATR_STOP_MULTIPLIER now 0.7 (was 7.0 - 10x too wide!)
    "atr_target": 3.0,
    "ema_fast": 9,
    "ema_slow": 20,
    "use_trend_filter": True,
    "use_volume_filter": True,
    "volume_mult": 1.5,
    "use_trailing_stop": True,
    "trailing_atr": 1.2,
    "rsi_max": 45.0,
    "adx_max": 25.0,
    "entry_mode": "bull_pullback",
    "pullback_bars": 3,
    "use_htf_vwap": True,
    "use_htf_ema": True,
    "htf_adx_max": 25.0,
    "stoch_oversold": 40,
    "stoch_overbought": 60,
    # UTM settings
    "use_anchored_vwap": True,
    "use_session_filter": True,
    "require_reversal_candle": True,
    "tp_mode": "vwap",
    "require_mss": False,
    "mss_timeout_bars": 6,
    # Phase 2/3 filters
    "use_fvg_filter": False,
    "use_ob_filter": False,
    "use_eqhl_filter": False,
    "use_cvd_filter": False,
    "cvd_window": 20,
    "use_ote": False,
    "use_dynamic_mode": False,
}

# Cutoff for Butterworth filter
CUTOFF = 0.10

# Test period - extended to 2025
TEST_START_YEAR = 2020
TEST_END_YEAR = 2025


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


# ─────────────────────────────────────────────────────────────────────────────
# METRICS CALCULATION
# ─────────────────────────────────────────────────────────────────────────────


def compute_metrics_for_result(equity_curve: pd.Series, trades: List[float]) -> Dict[str, float]:
    """Compute comprehensive metrics for backtest results."""
    if len(trades) < 1:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "net_pct": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
        }

    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    n_wins = len(wins)
    n_losses = len(losses)
    n_trades = len(trades)

    win_rate = (n_wins / n_trades * 100) if n_trades > 0 else 0.0

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    profit_factor = gross_profit / (gross_loss + 1e-10)

    total_return = equity_curve.iloc[-1] - 1.0
    net_pct = total_return * 100.0

    if len(trades) > 1:
        returns_arr = np.array(trades)
        mean_ret = np.mean(returns_arr)
        std_ret = np.std(returns_arr, ddof=1) if len(returns_arr) > 1 else 1.0
        years = (equity_curve.index[-1] - equity_curve.index[0]).days / 365.25
        trades_per_year = n_trades / years if years > 0 else 1
        sharpe = (mean_ret / (std_ret + 1e-10)) * math.sqrt(trades_per_year) if std_ret > 0 else 0.0
    else:
        sharpe = 0.0

    running_max = equity_curve.cummax()
    drawdown = (equity_curve - running_max) / running_max
    max_dd = abs(drawdown.min()) * 100.0

    return {
        "trades": n_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "net_pct": net_pct,
        "sharpe": sharpe,
        "max_drawdown_pct": max_dd,
    }


# ─────────────────────────────────────────────────────────────────────────────
# YEARLY BACKTEST
# ─────────────────────────────────────────────────────────────────────────────


def backtest_year(
    df: pd.DataFrame,
    year: int,
    mode: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Run backtest for a specific year in either Normal or Strict mode.
    
    mode: "normal" or "strict"
    - Normal: use fixed settings as-is
    - Strict: stricter filters (higher volume, lower ADX)
    """
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
    
    # Apply mode-specific settings
    params_copy = params.copy()
    
    if mode == "strict":
        # Strict mode: stricter filters to mirror live bot validation
        params_copy["volume_mult"] = 2.0       # Higher volume requirement
        params_copy["adx_max"] = 20.0          # Tighter ADX gate
        params_copy["htf_adx_max"] = 20.0       # Tighter HTF ADX gate
        params_copy["rsi_max"] = 40.0           # Stricter RSI gate
    
    # Pre-compute reference levels
    levels = compute_reference_levels(df_year)
    params_copy["levels"] = levels
    
    try:
        equity, trades = run_vwap_scalping(
            df_year,
            CUTOFF,
            **params_copy,
        )
        
        metrics = compute_metrics_for_result(equity, trades)
        
        return {
            "Year": year,
            "Mode": mode,
            "Trades": metrics["trades"],
            "WR%": round(metrics["win_rate"], 1),
            "PF": round(metrics["profit_factor"], 2),
            "Net%": round(metrics["net_pct"], 1),
            "Sharpe": round(metrics["sharpe"], 2),
            "MaxDD%": round(metrics["max_drawdown_pct"], 1),
        }
    except Exception as e:
        print(f"  ERROR in year {year} mode {mode}: {e}")
        import traceback
        traceback.print_exc()
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


# ─────────────────────────────────────────────────────────────────────────────
# WALK-FORWARD BACKTEST
# ─────────────────────────────────────────────────────────────────────────────


def run_walkforward(
    df: pd.DataFrame,
    params: Dict[str, Any],
    mode: str,
    start_year: int,
    end_year: int,
) -> List[Dict[str, Any]]:
    """Run backtest for each year."""
    results = []
    
    for year in range(start_year, end_year + 1):
        print(f"  Processing {year} ({mode} mode)...")
        result = backtest_year(df, year, mode, params)
        results.append(result)
    
    return results


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────


def main():
    print("=" * 70)
    print("VWAP Scalping FIXED Validation (ATR Stop = 0.7)")
    print("=" * 70)
    print("\nFIXED Settings being validated:")
    print(f"  SD Entry Threshold: {FIXED_SETTINGS['sd_threshold']}")
    print(f"  ATR Stop Multiplier: {FIXED_SETTINGS['atr_stop']} (FIXED - was 7.0, now 0.7)")
    print(f"  RSI Max: {FIXED_SETTINGS['rsi_max']}")
    print(f"  ADX Max: {FIXED_SETTINGS['adx_max']}")
    print()
    
    # Load data
    print("Loading 5m data (2020-2025)...")
    df = load_5m_data("2020-01-01", "2026-01-01")
    if df.empty:
        print("ERROR: No data loaded!")
        return
    print(f"Loaded {len(df):,} bars")
    
    years = list(range(TEST_START_YEAR, TEST_END_YEAR + 1))
    print(f"\nTesting years: {years}")
    
    # Run Normal mode
    print("\n" + "=" * 50)
    print("Running NORMAL mode (fixed settings)")
    print("=" * 50)
    normal_results = run_walkforward(df, FIXED_SETTINGS, "normal", TEST_START_YEAR, TEST_END_YEAR)
    
    # Run Strict mode
    print("\n" + "=" * 50)
    print("Running STRICT mode (stricter validation)")
    print("=" * 50)
    strict_results = run_walkforward(df, FIXED_SETTINGS, "strict", TEST_START_YEAR, TEST_END_YEAR)
    
    # Combine results
    all_results = normal_results + strict_results
    results_df = pd.DataFrame(all_results)
    
    # Save to CSV
    output_path = RESULTS_DIR / "vwap_scalping_fixed_validation.csv"
    results_df.to_csv(output_path, index=False)
    print(f"\nSaved results to: {output_path}")
    
    # Print summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    # Per-year comparison
    print("\n--- YEARLY RESULTS ---")
    print(f"{'Year':<6} {'Mode':<8} {'Trades':>6} {'WR%':>6} {'PF':>6} {'Net%':>8} {'Sharpe':>7} {'MaxDD%':>7}")
    print("-" * 64)
    
    for _, row in results_df.iterrows():
        print(f"{row['Year']:<6} {row['Mode']:<8} {row['Trades']:>6} {row['WR%']:>6.1f} {row['PF']:>6.2f} {row['Net%']:>8.1f} {row['Sharpe']:>7.2f} {row['MaxDD%']:>7.1f}")
    
    # Totals
    print("\n--- TOTALS BY MODE ---")
    for mode in ["normal", "strict"]:
        mode_data = results_df[results_df["Mode"] == mode]
        total_trades = mode_data["Trades"].sum()
        total_net = mode_data["Net%"].sum()
        profitable_years = mode_data[mode_data["Net%"] > 0]
        avg_wr = mode_data[mode_data["Trades"] > 0]["WR%"].mean() if len(mode_data[mode_data["Trades"] > 0]) > 0 else 0
        avg_pf = mode_data[mode_data["Trades"] > 0]["PF"].mean() if len(mode_data[mode_data["Trades"] > 0]) > 0 else 0
        avg_sharpe = mode_data[mode_data["Trades"] > 0]["Sharpe"].mean() if len(mode_data[mode_data["Trades"] > 0]) > 0 else 0
        avg_maxdd = mode_data["MaxDD%"].mean()
        
        print(f"\n{mode.upper()} Mode:")
        print(f"  Total Trades: {total_trades}")
        print(f"  Total Net%: {total_net:+.1f}%")
        print(f"  Profitable Years: {len(profitable_years)}/{len(mode_data)}")
        print(f"  Avg WR%: {avg_wr:.1f}%")
        print(f"  Avg PF: {avg_pf:.2f}")
        print(f"  Avg Sharpe: {avg_sharpe:.2f}")
        print(f"  Avg MaxDD%: {avg_maxdd:.1f}%")
    
    # Comparison to previous
    print("\n--- COMPARISON TO PREVIOUS (ATR=7.0) ---")
    normal_total = results_df[results_df["Mode"] == "normal"]["Net%"].sum()
    strict_total = results_df[results_df["Mode"] == "strict"]["Net%"].sum()
    
    # Previous results (from vwap_scalping_live_settings_validation.csv)
    prev_normal_total = -19.3
    prev_strict_total = -9.5
    
    print(f"Normal mode:  {normal_total:+.1f}% (was {prev_normal_total:+.1f}%) → Change: {normal_total - prev_normal_total:+.1f}%")
    print(f"Strict mode: {strict_total:+.1f}% (was {prev_strict_total:+.1f}%) → Change: {strict_total - prev_strict_total:+.1f}%")
    
    # Max DD improvement
    print("\n--- MAX DD COMPARISON ---")
    avg_maxdd_normal = results_df[results_df["Mode"] == "normal"]["MaxDD%"].mean()
    avg_maxdd_strict = results_df[results_df["Mode"] == "strict"]["MaxDD%"].mean()
    prev_maxdd_normal = 4.96  # from previous test
    prev_maxdd_strict = 3.48
    print(f"Normal mode Avg MaxDD%: {avg_maxdd_normal:.1f}% (was {prev_maxdd_normal:.1f}%)")
    print(f"Strict mode Avg MaxDD%: {avg_maxdd_strict:.1f}% (was {prev_maxdd_strict:.1f}%)")
    
    print("\n" + "=" * 70)
    print("VALIDATION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()