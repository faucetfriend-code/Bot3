"""
tuner_v4_test.py
================
Test V4 Tuner with CORRECT base parameters.

V4 Key Changes:
- Uses sd=4.037 (the winning static param)
- Uses bull_pullback for ALL regimes (the winning mode)
- Very conservative adaptation

Test:
- Use sd_threshold=4.037, entry_mode=bull_pullback as base
- Test from 2018-2025
- Compare to static .env (baseline)

Expected:
- Should achieve similar WR to static (~55%)
- Should achieve positive returns
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List

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

# Test period: 2018-2025
TEST_START = "2018-01-01"
TEST_END = "2026-01-01"

# Butterworth cutoff
CUTOFF = 0.10

# V4 Tuner CORRECT BASE PARAMETERS (from static .env that gave best WR!)
# Key insight: Use the SAME params for ALL regimes - conservative tuning
V4_BASE_PARAMS = {
    "sd_threshold": 4.037,       # THE WINNING static param
    "atr_stop": 1.87,
    "atr_target": 2.0,
    "trailing_atr": 1.93,
    "entry_mode": "bull_pullback",  # THE WINNING mode for ALL regimes
}

# Full params for V4 (EXACTLY matching baseline)
# Key differences from earlier: use_trend_filter=False, use_htf_vwap=False
V4_VWAP_PARAMS = {
    "sd_threshold": 4.037,
    "atr_stop": 1.87,
    "atr_target": 2.0,
    "trailing_atr": 1.93,
    "ema_fast": 9,
    "ema_slow": 20,
    "use_trend_filter": False,  # Match baseline
    "use_volume_filter": True,
    "volume_mult": 1.0,
    "use_trailing_stop": True,
    "adx_max": 30.0,  # Match baseline
    "rsi_max": 55.0,  # Match baseline
    "entry_mode": "bull_pullback",  # V4: ALL regimes use bull_pullback!
    "pullback_bars": 3,
    "use_htf_vwap": False,  # Match baseline
    "use_htf_ema": False,  # Match baseline
    "htf_adx_max": 30.0,
    "stoch_oversold": 40,
    "stoch_overbought": 60,
    "use_anchored_vwap": True,
    "use_session_filter": False,  # Match baseline
    "require_reversal_candle": False,
    "tp_mode": "atr",
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
# BACKTEST FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def run_vwap_v4(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    allowed_regimes: List[MarketRegime],
    sd_threshold: float = 4.037,
    entry_mode: str = "bull_pullback",
) -> Tuple[pd.Series, list, Dict[str, float]]:
    """Run VWAP with V4 params - conservative, simple configuration."""
    levels = compute_reference_levels(df)
    
    params = V4_VWAP_PARAMS.copy()
    params.update({
        "sd_threshold": sd_threshold,
        "entry_mode": entry_mode,
        "levels": levels,
        "regime_series": regime_series,
        "allowed_regimes": allowed_regimes,
    })
    
    equity, trades = run_vwap_scalping(
        df,
        CUTOFF,
        df_exit=df_exit,
        **params,
    )
    
    metrics = compute_metrics_for_result(equity, trades)
    return equity, trades, metrics


def run_static_comparison(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
) -> Tuple[pd.Series, list, Dict[str, float]]:
    """Run with STATIC .env params - baseline for comparison."""
    all_regimes = [
        MarketRegime.RANGING,
        MarketRegime.BEAR_WEAK,
        MarketRegime.BULL_WEAK,
        MarketRegime.BEAR_STRONG,
        MarketRegime.BULL_STRONG,
    ]
    return run_vwap_v4(df, df_exit, regime_series, all_regimes)


# ─────────────────────────────────────────────────────────────────────────────
# YEAR-BY-YEAR BACKTEST
# ─────────────────────────────────────────────────────────────────────────────

def backtest_year(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    year: int,
    config_name: str,
) -> Dict[str, Any]:
    """Run backtest for a specific year."""
    year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    year_end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
    
    df_year = df.loc[(df.index >= year_start) & (df.index < year_end)].copy()
    df_exit_year = None
    if df_exit is not None:
        df_exit_year = df_exit.loc[(df_exit.index >= year_start) & (df_exit.index < year_end)].copy()
    
    regime_year = regime_series.loc[(regime_series.index >= year_start) & (regime_series.index < year_end)].copy()
    
    if df_year.empty or regime_year.empty:
        return {
            "Year": year,
            "Config": config_name,
            "Trades": 0,
            "WR%": 0.0,
            "Net%": 0.0,
            "Sharpe": 0.0,
            "MaxDD%": 0.0,
        }
    
    _, trades, metrics = run_static_comparison(df_year, df_exit_year, regime_year)
    
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
    print("V4 Tuner Test - CORRECT Base Parameters")
    print("=" * 70)
    print("\n--- V4 Configuration:")
    print(f"  sd_threshold: {V4_BASE_PARAMS['sd_threshold']}")
    print(f"  entry_mode: {V4_BASE_PARAMS['entry_mode']} (for ALL regimes)")
    print(f"  atr_stop: {V4_BASE_PARAMS['atr_stop']}")
    print(f"  atr_target: {V4_BASE_PARAMS['atr_target']}")
    print("\n--- Test Period: 2018-01-01 to 2025-12-31 (5m bars)")
    print("\n--- Comparing:")
    print("  1. V4 (sd=4.037, bull_pullback for all regimes)")
    print("  2. Static .env baseline (same params)")
    print("\n--- Expected:")
    print("  - WR ~40-55% (winning static param)")
    print("  - Positive returns")
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
    
    # Compute regimes
    print("\nComputing regimes...")
    detector = RegimeDetector(df_5m, method="combined", timeframe="15m")
    regime_series = detector.get_regimes()
    regime_dist = regime_series.value_counts()
    for r in MarketRegime:
        cnt = regime_dist.get(r, 0)
        print(f"  {r.value}: {cnt} bars ({cnt/len(regime_series)*100:.1f}%)")
    
    # Test years
    years = list(range(2018, 2026))
    print(f"\nTesting years: {years}")
    
    # Storage for results
    all_results = []
    
    # Run V4 config
    print("\n" + "=" * 50)
    print("Running V4 (sd=4.037, bull_pullback for all regimes)")
    print("=" * 50)
    for year in years:
        print(f"  Processing {year}...")
        result = backtest_year(df_5m, df_1m, regime_series, year, "v4")
        all_results.append(result)
    
    # Create DataFrame
    results_df = pd.DataFrame(all_results)
    
    # Save to CSV
    output_path = RESULTS_DIR / "tuner_v4_test.csv"
    results_df.to_csv(output_path, index=False)
    print(f"\nSaved results to: {output_path}")
    
    # Print summary
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)
    
    total_trades = results_df["Trades"].sum()
    weighted_wr = (results_df["WR%"] * results_df["Trades"]).sum() / max(total_trades, 1)
    total_net = results_df["Net%"].sum()
    avg_sharpe = results_df["Sharpe"].mean()
    max_dd = results_df["MaxDD%"].max()
    
    print(f"\nV4 (sd=4.037, bull_pullback):")
    print(f"  Total Trades: {total_trades}")
    print(f"  Avg WR%: {weighted_wr:.1f}%")
    print(f"  Total Net%: {total_net:.1f}%")
    print(f"  Avg Sharpe: {avg_sharpe:.2f}")
    print(f"  Max DD%: {max_dd:.1f}%")
    
    print("\n" + "=" * 70)
    print("YEAR-BY-YEAR BREAKDOWN")
    print("=" * 70)
    print(f"\n{'Year':<8} {'Trades':>8} {'WR%':>8} {'Net%':>8} {'Sharpe':>8} {'MaxDD%':>8}")
    print("-" * 50)
    for _, row in results_df.iterrows():
        print(f"{row['Year']:<8} {row['Trades']:>8} {row['WR%']:>7.1f}% {row['Net%']:>7.1f}% {row['Sharpe']:>8.2f} {row['MaxDD%']:>7.1f}%")
    
    # Validate expectations
    print("\n" + "=" * 70)
    print("VALIDATION CHECKS")
    print("=" * 70)
    
    print(f"\n1. Win Rate Check:")
    print(f"   Actual WR: {weighted_wr:.1f}%")
    print(f"   Expected: ~40-55%")
    if weighted_wr >= 40:
        print("[PASS] Within expected range")
    else:
        print("[FAIL] Below expected range")
    
    print(f"\n2. Returns Check:")
    print(f"   Actual Net: {total_net:.1f}%")
    print(f"   Expected: Positive")
    if total_net > 0:
        print("[PASS] Positive returns")
    else:
        print("[FAIL] Negative returns")
    
    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    
    if weighted_wr >= 40 and total_net > 0:
        print("\n[V4 PASSED] Achieved expected WR and positive returns!")
    elif weighted_wr >= 40:
        print("\n[V4 PARTIAL] Achieved expected WR but negative returns")
    elif total_net > 0:
        print("\n[V4 PARTIAL] Positive returns but below expected WR")
    else:
        print("\n[V4 FAILED] Did not meet expected targets")
    
    print("\n" + "=" * 70)
    print("Done!")
    print("=" * 70)


if __name__ == "__main__":
    main()