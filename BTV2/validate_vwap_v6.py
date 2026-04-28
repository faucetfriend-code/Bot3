"""
validate_vwap_v6.py
===================
Validate v6 VWAP configuration with all bug fixes applied.

v6 Settings (FIXED):
- sd_threshold: 3.0 (was 2.0 - too tight, causing overtrading)
- atr_stop: 1.0
- atr_target: 2.0
- trailing_atr: 1.2
- entry_mode: mean_reversion
- tp_mode: atr (was vwap - broken, causing poor exits)
- use_htf_ema: true
- use_stoch_filter: false
- use_htf_vwap: false
- require_reversal_candle: false
- htf_adx_max: 30.0

Compare to BROKEN config:
- sd_threshold: 2.0 (too tight, overtrading)
- tp_mode: vwap (broken exit mode)

Test Period: 2020-01-01 to 2025-12-31 (5m bars)
Output: BTV2/results/vwap_v6_validation.csv

Metrics: Win rate (target: 50%+), Total P&L (target: positive), Sharpe, Max DD
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

# v6 FIXED settings - all bug fixes applied
V6_SETTINGS = {
    "sd_threshold": 3.0,           # FIXED: was 2.0 (too tight, overtrading)
    "atr_stop": 1.0,                # ATR stop multiplier
    "atr_target": 2.0,              # ATR target multiplier
    "ema_fast": 9,
    "ema_slow": 20,
    "use_trend_filter": True,
    "use_volume_filter": True,
    "volume_mult": 1.5,
    "use_trailing_stop": True,
    "trailing_atr": 1.2,
    "adx_max": 25.0,
    "rsi_max": 45.0,
    "entry_mode": "mean_reversion",  # mean reversion (fade extreme deviations)
    "pullback_bars": 3,
    "use_htf_vwap": False,          # disabled (not needed with mean reversion)
    "use_htf_ema": True,             # 1h EMA filter enabled
    "htf_adx_max": 30.0,            # 1h ADX gate
    "stoch_oversold": 40,
    "stoch_overbought": 60,
    # UTM settings
    "use_anchored_vwap": True,
    "use_session_filter": True,
    "require_reversal_candle": False,
    "tp_mode": "atr",               # FIXED: was vwap (broken, now uses ATR trailing)
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

# BROKEN settings for comparison (what was wrong before)
BROKEN_SETTINGS = {
    "sd_threshold": 2.0,            # BROKEN: too tight, causing overtrading
    "atr_stop": 1.0,
    "atr_target": 2.0,
    "ema_fast": 9,
    "ema_slow": 20,
    "use_trend_filter": True,
    "use_volume_filter": True,
    "volume_mult": 1.5,
    "use_trailing_stop": True,
    "trailing_atr": 1.2,
    "adx_max": 25.0,
    "rsi_max": 45.0,
    "entry_mode": "mean_reversion",
    "pullback_bars": 3,
    "use_htf_vwap": False,
    "use_htf_ema": True,
    "htf_adx_max": 30.0,
    "stoch_oversold": 40,
    "stoch_overbought": 60,
    "use_anchored_vwap": True,
    "use_session_filter": True,
    "require_reversal_candle": False,
    "tp_mode": "vwap",              # BROKEN: vwap exit mode
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

# Cutoff for Butterworth filter
CUTOFF = 0.10

# Test period
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
    config_name: str,
    params: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Run backtest for a specific year with given config.
    
    config_name: "v6_fixed" or "broken"
    """
    year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    year_end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
    df_year = df.loc[(df.index >= year_start) & (df.index < year_end)].copy()
    
    if df_year.empty:
        return {
            "Year": year,
            "Config": config_name,
            "Trades": 0,
            "WR%": 0.0,
            "PF": 0.0,
            "Net%": 0.0,
            "Sharpe": 0.0,
            "MaxDD%": 0.0,
        }
    
    # Pre-compute reference levels
    levels = compute_reference_levels(df_year)
    params_copy = params.copy()
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
            "Config": config_name,
            "Trades": metrics["trades"],
            "WR%": round(metrics["win_rate"], 1),
            "PF": round(metrics["profit_factor"], 2),
            "Net%": round(metrics["net_pct"], 1),
            "Sharpe": round(metrics["sharpe"], 2),
            "MaxDD%": round(metrics["max_drawdown_pct"], 1),
        }
    except Exception as e:
        print(f"  ERROR in year {year} config {config_name}: {e}")
        import traceback
        traceback.print_exc()
        return {
            "Year": year,
            "Config": config_name,
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
    config_name: str,
    start_year: int,
    end_year: int,
) -> List[Dict[str, Any]]:
    """Run backtest for each year."""
    results = []
    
    for year in range(start_year, end_year + 1):
        print(f"  Processing {year} ({config_name})...")
        result = backtest_year(df, year, config_name, params)
        results.append(result)
    
    return results


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────


def main():
    print("=" * 70)
    print("VWAP v6 Configuration Validation")
    print("=" * 70)
    print("\n--- v6 FIXED Settings ---")
    print(f"  SD Entry Threshold: {V6_SETTINGS['sd_threshold']} (was 2.0 - FIXED)")
    print(f"  TP Mode: {V6_SETTINGS['tp_mode']} (was vwap - FIXED)")
    print(f"  Entry Mode: {V6_SETTINGS['entry_mode']}")
    print(f"  ATR Stop: {V6_SETTINGS['atr_stop']}")
    print(f"  ATR Target: {V6_SETTINGS['atr_target']}")
    print(f"  Trailing ATR: {V6_SETTINGS['trailing_atr']}")
    print(f"  Use HTF EMA: {V6_SETTINGS['use_htf_ema']}")
    print(f"  HTF ADX Max: {V6_SETTINGS['htf_adx_max']}")
    print()
    print("--- BROKEN Settings (for comparison) ---")
    print(f"  SD Entry Threshold: {BROKEN_SETTINGS['sd_threshold']} (too tight)")
    print(f"  TP Mode: {BROKEN_SETTINGS['tp_mode']} (broken)")
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
    
    # Run v6 Fixed config
    print("\n" + "=" * 50)
    print("Running v6 FIXED config")
    print("=" * 50)
    v6_results = run_walkforward(df, V6_SETTINGS, "v6_fixed", TEST_START_YEAR, TEST_END_YEAR)
    
    # Run Broken config
    print("\n" + "=" * 50)
    print("Running BROKEN config (sd=2.0, tp_mode=vwap)")
    print("=" * 50)
    broken_results = run_walkforward(df, BROKEN_SETTINGS, "broken", TEST_START_YEAR, TEST_END_YEAR)
    
    # Combine results
    all_results = v6_results + broken_results
    results_df = pd.DataFrame(all_results)
    
    # Save to CSV
    output_path = RESULTS_DIR / "vwap_v6_validation.csv"
    results_df.to_csv(output_path, index=False)
    print(f"\nSaved results to: {output_path}")
    
    # Print summary
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)
    
    # Per-year comparison
    print("\n--- YEARLY RESULTS ---")
    print(f"{'Year':<6} {'Config':<10} {'Trades':>6} {'WR%':>6} {'PF':>6} {'Net%':>8} {'Sharpe':>7} {'MaxDD%':>7}")
    print("-" * 68)
    
    for _, row in results_df.iterrows():
        print(f"{row['Year']:<6} {row['Config']:<10} {row['Trades']:>6} {row['WR%']:>6.1f} {row['PF']:>6.2f} {row['Net%']:>8.1f} {row['Sharpe']:>7.2f} {row['MaxDD%']:>7.1f}")
    
    # Totals by config
    print("\n--- TOTALS BY CONFIG ---")
    for config in ["v6_fixed", "broken"]:
        config_data = results_df[results_df["Config"] == config]
        total_trades = config_data["Trades"].sum()
        total_net = config_data["Net%"].sum()
        profitable_years = config_data[config_data["Net%"] > 0]
        avg_wr = config_data[config_data["Trades"] > 0]["WR%"].mean() if len(config_data[config_data["Trades"] > 0]) > 0 else 0
        avg_pf = config_data[config_data["Trades"] > 0]["PF"].mean() if len(config_data[config_data["Trades"] > 0]) > 0 else 0
        avg_sharpe = config_data[config_data["Trades"] > 0]["Sharpe"].mean() if len(config_data[config_data["Trades"] > 0]) > 0 else 0
        avg_maxdd = config_data["MaxDD%"].mean()
        
        print(f"\n{config.upper()} Config:")
        print(f"  Total Trades: {total_trades}")
        print(f"  Total Net%: {total_net:+.1f}%")
        print(f"  Profitable Years: {len(profitable_years)}/{len(config_data)}")
        print(f"  Avg WR%: {avg_wr:.1f}%")
        print(f"  Avg PF: {avg_pf:.2f}")
        print(f"  Avg Sharpe: {avg_sharpe:.2f}")
        print(f"  Avg MaxDD%: {avg_maxdd:.1f}%")
    
    # Direct comparison
    print("\n--- COMPARISON: v6 FIXED vs BROKEN ---")
    v6_data = results_df[results_df["Config"] == "v6_fixed"]
    broken_data = results_df[results_df["Config"] == "broken"]
    
    v6_total_net = v6_data["Net%"].sum()
    broken_total_net = broken_data["Net%"].sum()
    improvement = v6_total_net - broken_total_net
    
    v6_avg_wr = v6_data["WR%"].mean()
    broken_avg_wr = broken_data["WR%"].mean()
    
    v6_avg_sharpe = v6_data["Sharpe"].mean()
    broken_avg_sharpe = broken_data["Sharpe"].mean()
    
    v6_avg_maxdd = v6_data["MaxDD%"].mean()
    broken_avg_maxdd = broken_data["MaxDD%"].mean()
    
    print(f"\nTotal Net%: v6_fixed={v6_total_net:+.1f}% vs broken={broken_total_net:+.1f}%")
    print(f"  → Improvement: {improvement:+.1f}%")
    print(f"\nAvg Win Rate%: v6_fixed={v6_avg_wr:.1f}% vs broken={broken_avg_wr:.1f}%")
    print(f"  → Change: {v6_avg_wr - broken_avg_wr:+.1f}%")
    print(f"\nAvg Sharpe: v6_fixed={v6_avg_sharpe:.2f} vs broken={broken_avg_sharpe:.2f}")
    print(f"  → Change: {v6_avg_sharpe - broken_avg_sharpe:+.2f}")
    print(f"\nAvg MaxDD%: v6_fixed={v6_avg_maxdd:.1f}% vs broken={broken_avg_maxdd:.1f}%")
    print(f"  → Change: {v6_avg_maxdd - broken_avg_maxdd:+.1f}%")
    
    # Target checks
    print("\n--- TARGET CHECKS ---")
    print(f"Win Rate (target: 50%+): v6={v6_avg_wr:.1f}% → {'PASS' if v6_avg_wr >= 50 else 'BELOW TARGET'}")
    print(f"Total P&L (target: positive): v6={v6_total_net:+.1f}% → {'PASS' if v6_total_net > 0 else 'BELOW TARGET'}")
    print(f"Sharpe (target: >0): v6={v6_avg_sharpe:.2f} → {'PASS' if v6_avg_sharpe > 0 else 'BELOW TARGET'}")
    
    print("\n" + "=" * 70)
    print("VALIDATION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()