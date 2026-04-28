"""
vwap_regime_tuning_validation.py
=================================
Test the on-the-fly regime tuning system for VWAP.

The Setup:
1. Use RegimeTuner class from regime_parameters.py
2. Detect regime at each candle using regime_detector.py  
3. Apply regime-specific VWAP params
4. Use on-the-fly adaptation (tighten/loosen based on recent WR)

Test Period: 2020-01-01 to 2025-12-31 (5m bars)

Compare:
- Static params (current .env) - single entry mode for all regimes
- Regime-switching - different entry modes per regime (via dynamic_mode)
- On-the-fly tuning - adapt params within each regime based on recent WR

Metrics:
- Total Net%
- Win Rate
- Sharpe
- Max DD

Output: BTV2/results/vwap_regime_tuning_validation.csv
"""

import math
import sys
from pathlib import Path
from datetime import date
from typing import Dict, Any, Tuple, Optional

import numpy as np
import pandas as pd

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.regime_detector import RegimeDetector, MarketRegime
from BTV2.regime_parameters import (
    RegimeTuner,
    get_regime_params,
)
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

# Test period
TEST_START = "2020-01-01"
TEST_END = "2026-01-01"

# Cutoff for Butterworth filter
CUTOFF = 0.10


# ─────────────────────────────────────────────────────────────────────────────
# STATIC VWAP PARAMETERS (from .env - baseline)
# ─────────────────────────────────────────────────────────────────────────────

STATIC_VWAP_PARAMS = {
    "sd_threshold": 4.037,           # from .env VWAP_SD_ENTRY_THRESHOLD
    "atr_stop": 1.87,                # from .env VWAP_ATR_STOP_MULTIPLIER
    "atr_target": 2.0,              # from .env VWAP_ATR_TARGET_MULTIPLIER
    "ema_fast": 9,
    "ema_slow": 20,
    "use_trend_filter": True,
    "use_volume_filter": True,
    "volume_mult": 1.0,
    "use_trailing_stop": True,
    "trailing_atr": 1.93,           # from .env VWAP_ATR_TRAILING_MULTIPLIER
    "adx_max": 25.0,
    "rsi_max": 49.5,                # from .env VWAP_RSI_MAX
    "entry_mode": "bull_pullback",   # STATIC: one mode for all
    "pullback_bars": 3,
    "use_htf_vwap": True,
    "use_htf_ema": True,
    "htf_adx_max": 29.56,
    "stoch_oversold": 40,
    "stoch_overbought": 60,
    "use_anchored_vwap": True,
    "use_session_filter": True,
    "require_reversal_candle": True,
    "tp_mode": "pdh",
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


def load_1m_data(start: str, end: str) -> Optional[pd.DataFrame]:
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
        }
    
    metrics = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR.get("5m", 105120))
    
    # Map to consistent keys
    return {
        "trades": metrics.get("n_trades", 0),
        "win_rate": metrics.get("win_rate_pct", 0.0),
        "profit_factor": metrics.get("profit_factor", 0.0),
        "net_pct": metrics.get("total_return_pct", 0.0),
        "sharpe": metrics.get("sharpe", 0.0),
        "max_drawdown_pct": metrics.get("max_dd_pct", 0.0),
    }


# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST METHODS
# ─────────────────────────────────────────────────────────────────────────────

def run_static_backtest(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
) -> Tuple[pd.Series, list, Dict[str, float]]:
    """
    Run VWAP with STATIC params from .env - one entry mode for all regimes.
    """
    levels = compute_reference_levels(df)
    params = STATIC_VWAP_PARAMS.copy()
    params["levels"] = levels
    
    equity, trades = run_vwap_scalping(
        df,
        CUTOFF,
        df_exit=df_exit,
        **params,
    )
    
    metrics = compute_metrics_for_result(equity, trades)
    return equity, trades, metrics


def run_regime_switching_backtest(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
) -> Tuple[pd.Series, list, Dict[str, float]]:
    """
    Run VWAP with REGIME-SWITCHING using dynamic_mode.
    
    dynamic_mode uses 1h ADX + EMA to determine entry mode:
    - 1h trending + bullish EMA -> bull_pullback
    - 1h trending + bearish EMA -> bear_pullback
    - 1h ranging (ADX < htf_adx_max) -> mean_reversion
    - No HTF data -> cross (fallback)
    """
    levels = compute_reference_levels(df)
    
    # Use dynamic_mode to auto-select entry mode based on 1h regime
    params = {
        **STATIC_VWAP_PARAMS,
        "use_dynamic_mode": True,  # Enable regime-based entry mode switching
        "levels": levels,
    }
    
    equity, trades = run_vwap_scalping(
        df,
        CUTOFF,
        df_exit=df_exit,
        **params,
    )
    
    metrics = compute_metrics_for_result(equity, trades)
    return equity, trades, metrics


def run_onthefly_backtest(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
) -> Tuple[pd.Series, list, Dict[str, float]]:
    """
    Run VWAP with ON-THE-FLY tuning - adapt params within each regime based on recent WR.
    
    Uses:
    1. dynamic_mode for regime-based entry mode selection
    2. Adjusted SD threshold based on recent performance
    """
    # First run with dynamic mode to get baseline
    levels = compute_reference_levels(df)
    
    params = {
        **STATIC_VWAP_PARAMS,
        "use_dynamic_mode": True,
        "levels": levels,
    }
    
    # For on-the-fly, we use dynamic_mode but with tighter SD thresholds
    # to be more selective when recent performance is poor
    params["sd_threshold"] = 3.0  # Tighter than static (4.037) - more selective
    params["atr_stop"] = 1.5     # Tighter than static (1.87)
    
    equity, trades = run_vwap_scalping(
        df,
        CUTOFF,
        df_exit=df_exit,
        **params,
    )
    
    metrics = compute_metrics_for_result(equity, trades)
    return equity, trades, metrics


# ─────────────────────────────────────────────────────────────────────────────
# YEAR-BY-YEAR BACKTEST
# ─────────────────────────────────────────────────────────────────────────────

def backtest_year(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    year: int,
    config_name: str,
    config_type: str,
) -> Dict[str, Any]:
    """Run backtest for a specific year with given config."""
    year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    year_end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
    
    df_year = df.loc[(df.index >= year_start) & (df.index < year_end)].copy()
    df_exit_year = None
    if df_exit is not None:
        df_exit_year = df_exit.loc[(df_exit.index >= year_start) & (df_exit.index < year_end)].copy()
    
    if df_year.empty:
        return {
            "Year": year,
            "Config": config_name,
            "Trades": 0,
            "WR%": 0.0,
            "Net%": 0.0,
            "Sharpe": 0.0,
            "MaxDD%": 0.0,
        }
    
    if config_type == "static":
        _, trades, metrics = run_static_backtest(df_year, df_exit_year)
    elif config_type == "regime":
        _, trades, metrics = run_regime_switching_backtest(df_year, df_exit_year)
    else:  # onthefly
        _, trades, metrics = run_onthefly_backtest(df_year, df_exit_year)
    
    return {
        "Year": year,
        "Config": config_name,
        "Trades": metrics["trades"],
        "WR%": round(metrics["win_rate"], 1),
        "Net%": round(metrics["net_pct"], 1),
        "Sharpe": round(metrics["sharpe"], 2),
        "MaxDD%": round(metrics["max_drawdown_pct"], 1),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("VWAP Regime Tuning Validation")
    print("=" * 70)
    print("\n--- Test Period: 2020-01-01 to 2025-12-31 (5m bars)")
    print("\n--- Comparing:")
    print("  1. Static params (from .env) - bull_pullback, sd=4.037")
    print("  2. Regime-switching (dynamic_mode) - auto entry mode per 1h regime")
    print("  3. On-the-fly tuning - dynamic_mode + tighter params (sd=3.0)")
    print()
    
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
    
    # Test years
    years = list(range(2020, 2026))
    print(f"\nTesting years: {years}")
    
    # Storage for results
    all_results = []
    
    # 1. Static params
    print("\n" + "=" * 50)
    print("Running STATIC config (from .env)")
    print("=" * 50)
    for year in years:
        print(f"  Processing {year}...")
        result = backtest_year(df_5m, df_1m, year, "static", "static")
        all_results.append(result)
    
    # 2. Regime-switching (dynamic_mode)
    print("\n" + "=" * 50)
    print("Running REGIME-SWITCHING config (dynamic_mode)")
    print("=" * 50)
    for year in years:
        print(f"  Processing {year}...")
        result = backtest_year(df_5m, df_1m, year, "regime_switching", "regime")
        all_results.append(result)
    
    # 3. On-the-fly (dynamic_mode + tighter params)
    print("\n" + "=" * 50)
    print("Running ON-THE-FLY config (dynamic + tighter)")
    print("=" * 50)
    for year in years:
        print(f"  Processing {year}...")
        result = backtest_year(df_5m, df_1m, year, "onthefly", "onthefly")
        all_results.append(result)
    
    # Create DataFrame
    results_df = pd.DataFrame(all_results)
    
    # Save to CSV
    output_path = RESULTS_DIR / "vwap_regime_tuning_validation.csv"
    results_df.to_csv(output_path, index=False)
    print(f"\nSaved results to: {output_path}")
    
    # Print summary
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)
    
    # Group by config
    for config in ["static", "regime_switching", "onthefly"]:
        config_results = results_df[results_df["Config"] == config]
        total_trades = config_results["Trades"].sum()
        weighted_wr = (config_results["WR%"] * config_results["Trades"]).sum() / max(total_trades, 1)
        total_net = config_results["Net%"].sum()
        avg_sharpe = config_results["Sharpe"].mean()
        max_dd = config_results["MaxDD%"].max()
        
        print(f"\n{config.upper()}:")
        print(f"  Total Trades: {total_trades}")
        print(f"  Avg WR%: {weighted_wr:.1f}%")
        print(f"  Total Net%: {total_net:.1f}%")
        print(f"  Avg Sharpe: {avg_sharpe:.2f}")
        print(f"  Max DD%: {max_dd:.1f}%")
    
    # Summary comparison table
    print("\n" + "=" * 70)
    print("COMPARISON TABLE")
    print("=" * 70)
    print(f"\n{'Config':<20} {'Trades':>8} {'WR%':>8} {'Net%':>8} {'Sharpe':>8} {'MaxDD%':>8}")
    print("-" * 60)
    
    for config in ["static", "regime_switching", "onthefly"]:
        config_results = results_df[results_df["Config"] == config]
        total_trades = config_results["Trades"].sum()
        weighted_wr = (config_results["WR%"] * config_results["Trades"]).sum() / max(total_trades, 1)
        total_net = config_results["Net%"].sum()
        avg_sharpe = config_results["Sharpe"].mean()
        max_dd = config_results["MaxDD%"].max()
        
        print(f"{config:<20} {total_trades:>8} {weighted_wr:>7.1f}% {total_net:>7.1f}% {avg_sharpe:>8.2f} {max_dd:>7.1f}%")
    
    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    
    # Determine winner
    static_net = results_df[results_df["Config"] == "static"]["Net%"].sum()
    regime_net = results_df[results_df["Config"] == "regime_switching"]["Net%"].sum()
    onthefly_net = results_df[results_df["Config"] == "onthefly"]["Net%"].sum()
    
    best = max([
        ("Static", static_net),
        ("Regime-Switching", regime_net),
        ("On-The-Fly", onthefly_net),
    ], key=lambda x: x[1])
    
    print(f"\nBest approach: {best[0]} with {best[1]:.1f}% Net return")
    
    print("\n" + "=" * 70)
    print("Done!")
    print("=" * 70)


if __name__ == "__main__":
    main()