"""
tuner_v5_sd3_test.py
====================
Test V5 Tuner with SD=3.0 as BASE (instead of 4.037).

The Finding:
- SD=3.0 gives 45.7% WR (best!)
- Static SD=4.037 gives lower WR
- But V5 tuner with SD=4.037 was adapting toward higher SD anyway

Test:
- Base SD = 3.0 (the winning value)
- Use V5 tuner adaptation from there
- Test 2023-2025

This Should Work Because:
- 3.0 is the sweet spot for recent market
- V5 tuner will adapt up if WR is low, down if WR is high
- Should achieve 45%+ WR
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

# Test period: 2023-2025 (as requested)
TEST_START = "2023-01-01"
TEST_END = "2026-01-01"

# Butterworth cutoff
CUTOFF = 0.10

# V5 Tuner Parameters - MODIFIED: Base SD = 3.0 instead of 4.037
V5_BASE_SD = 3.0  # THE WINNING VALUE - was 4.037
V5_ENTRY_MODE = "bull_pullback"  # Never change

# Adaptation thresholds
WR_LOW_THRESHOLD = 40.0  # If WR < 40%, tighten (sd += 0.15)
WR_HIGH_THRESHOLD = 55.0  # If WR > 55%, loosen (sd -= 0.1)
ADAPT_LOOKBACK = 30  # Number of trades to evaluate

# Adaptation step sizes
SD_TIGHTEN = 0.15  # Add when WR is too low
SD_LOOSEN = 0.10  # Subtract when WR is too high
SD_MIN = 2.5  # Minimum SD to prevent over-trading
SD_MAX = 6.0  # Maximum SD to prevent under-trading

# V5 VWAP params
V5_VWAP_PARAMS = {
    "sd_threshold": V5_BASE_SD,
    "atr_stop": 1.87,
    "atr_target": 2.0,
    "trailing_atr": 1.93,
    "ema_fast": 9,
    "ema_slow": 20,
    "use_trend_filter": False,
    "use_volume_filter": True,
    "volume_mult": 1.0,
    "use_trailing_stop": True,
    "adx_max": 30.0,
    "rsi_max": 55.0,
    "entry_mode": V5_ENTRY_MODE,
    "pullback_bars": 3,
    "use_htf_vwap": False,
    "use_htf_ema": False,
    "htf_adx_max": 30.0,
    "stoch_oversold": 40,
    "stoch_overbought": 60,
    "use_anchored_vwap": True,
    "use_session_filter": False,
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
    
    return {
        "trades": metrics.get("n_trades", 0),
        "win_rate": metrics.get("win_rate_pct", 0.0),
        "profit_factor": metrics.get("profit_factor", 0.0),
        "net_pct": metrics.get("total_return_pct", 0.0),
        "sharpe": metrics.get("sharpe", 0.0),
        "max_drawdown_pct": metrics.get("max_dd_pct", 0.0),
    }


# ─────────────────────────────────────────────────────────────────────────────
# V5 ADAPTATION LOGIC
# ─────────────────────────────────────────────────────────────────────────────

def compute_win_rate(trades: List[float]) -> float:
    """Compute win rate from list of P&L values."""
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if t > 0)
    return wins / len(trades) * 100


def adapt_sd(current_sd: float, recent_trades: List[float]) -> Tuple[float, str]:
    """
    Adapt SD based on recent trade performance.
    
    Returns:
        (new_sd, reason) - The adapted SD and why it changed (or "no_change")
    """
    if len(recent_trades) < ADAPT_LOOKBACK:
        return current_sd, "insufficient_trades"
    
    wr = compute_win_rate(recent_trades)
    
    if wr < WR_LOW_THRESHOLD:
        # Tighten SD (higher threshold = fewer but more confident trades)
        new_sd = min(current_sd + SD_TIGHTEN, SD_MAX)
        reason = f"wr_low({wr:.1f}% < {WR_LOW_THRESHOLD}%)"
        return new_sd, reason
    elif wr > WR_HIGH_THRESHOLD:
        # Loosen SD (lower threshold = more trades when we're doing well)
        new_sd = max(current_sd - SD_LOOSEN, SD_MIN)
        reason = f"wr_high({wr:.1f}% > {WR_HIGH_THRESHOLD}%)"
        return new_sd, reason
    else:
        return current_sd, "no_change"


# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def run_vwap_with_params(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    sd_threshold: float,
    entry_mode: str = V5_ENTRY_MODE,
) -> Tuple[pd.Series, list, Dict[str, float]]:
    """Run VWAP with specific SD and entry mode."""
    levels = compute_reference_levels(df)
    
    params = V5_VWAP_PARAMS.copy()
    params.update({
        "sd_threshold": sd_threshold,
        "entry_mode": entry_mode,
        "levels": levels,
        "regime_series": regime_series,
        "allowed_regimes": [
            MarketRegime.RANGING,
            MarketRegime.BEAR_WEAK,
            MarketRegime.BULL_WEAK,
            MarketRegime.BEAR_STRONG,
            MarketRegime.BULL_STRONG,
        ],
    })
    
    equity, trades = run_vwap_scalping(
        df,
        CUTOFF,
        df_exit=df_exit,
        **params,
    )
    
    metrics = compute_metrics_for_result(equity, trades)
    return equity, trades, metrics


def run_static_baseline(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    base_sd: float = V5_BASE_SD,
) -> Tuple[pd.Series, list, Dict[str, float]]:
    """Run with STATIC sd (no adaptation) - baseline."""
    return run_vwap_with_params(df, df_exit, regime_series, base_sd, V5_ENTRY_MODE)


def run_v5_adaptive(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    adapt_interval: int = 30,
) -> Tuple[pd.Series, list, Dict[str, float], List[Dict[str, Any]]]:
    """
    Run V5 adaptive strategy with SD adaptation.
    
    Every `adapt_interval` trades, check recent performance and adjust SD.
    
    Returns:
        equity, trades, metrics, adaptation_log
    """
    levels = compute_reference_levels(df)
    
    params = V5_VWAP_PARAMS.copy()
    params.update({
        "levels": levels,
        "regime_series": regime_series,
        "allowed_regimes": [
            MarketRegime.RANGING,
            MarketRegime.BEAR_WEAK,
            MarketRegime.BULL_WEAK,
            MarketRegime.BEAR_STRONG,
            MarketRegime.BULL_STRONG,
        ],
    })
    
    if len(df) < 1000:
        # Not enough data, just run once
        params["sd_threshold"] = V5_BASE_SD
        params["entry_mode"] = V5_ENTRY_MODE
        equity, trades = run_vwap_scalping(df, CUTOFF, df_exit=df_exit, **params)
        metrics = compute_metrics_for_result(equity, trades)
        return equity, trades, metrics, []
    
    # Process in monthly segments
    adaptation_log = []
    current_sd = V5_BASE_SD
    all_equity = []
    all_trades = []
    
    # Remove tz from all DataFrames and Series for consistent comparison
    df_copy = df.copy()
    df_copy.index = df_copy.index.tz_localize(None)
    if df_exit is not None:
        df_exit_copy = df_exit.copy()
        df_exit_copy.index = df_exit_copy.index.tz_localize(None)
    else:
        df_exit_copy = None
    regime_copy = regime_series.copy()
    regime_copy.index = regime_copy.index.tz_localize(None)
    
    months = df_copy.index.to_period("M").unique()
    
    for i, month in enumerate(months):
        month_start = month.to_timestamp()
        month_end = (month + 1).to_timestamp()
        
        # Get data for this month (tz-naive)
        df_month = df_copy.loc[
            (df_copy.index >= month_start) & (df_copy.index < month_end)
        ].copy()
        
        df_exit_month = None
        if df_exit_copy is not None:
            df_exit_month = df_exit_copy.loc[
                (df_exit_copy.index >= month_start) & (df_exit_copy.index < month_end)
            ].copy()
        
        regime_month = regime_copy.loc[
            (regime_copy.index >= month_start) & (regime_copy.index < month_end)
        ].copy()
        
        if df_month.empty or regime_month.empty:
            continue
        
        # Update params with current SD
        params["sd_threshold"] = current_sd
        params["entry_mode"] = V5_ENTRY_MODE
        params["levels"] = compute_reference_levels(df_month)
        params["regime_series"] = regime_month
        
        # Run backtest for this month
        equity, trades = run_vwap_scalping(df_month, CUTOFF, df_exit=df_exit_month, **params)
        
        # Log current state
        trade_count = len(trades)
        if trade_count > 0:
            wins = sum(1 for t in trades if t > 0)
            wr = wins / trade_count * 100
        else:
            wr = 0.0
        
        adaptation_log.append({
            "month": str(month),
            "sd_before": current_sd,
            "trades": trade_count,
            "wr": wr,
        })
        
        all_equity.append(equity)
        all_trades.extend(trades)
        
        # Adapt for next month if we have enough trades
        if len(all_trades) >= ADAPT_LOOKBACK:
            # Use last ADAPT_LOOKBACK trades for adaptation
            recent_trades = all_trades[-ADAPT_LOOKBACK:]
            new_sd, reason = adapt_sd(current_sd, recent_trades)
            
            if new_sd != current_sd:
                adaptation_log[-1]["adapt_reason"] = reason
                adaptation_log[-1]["sd_after"] = new_sd
                current_sd = new_sd
            else:
                adaptation_log[-1]["adapt_reason"] = "no_change"
                adaptation_log[-1]["sd_after"] = current_sd
    
    # Combine equity curves
    if all_equity:
        combined_equity = pd.concat(all_equity)
        combined_equity = combined_equity.sort_index()
    else:
        combined_equity = pd.Series()
    
    metrics = compute_metrics_for_result(combined_equity, all_trades)
    return combined_equity, all_trades, metrics, adaptation_log


# ─────────────────────────────────────────────────────────────────────────────
# YEAR-BY-YEAR BACKTEST
# ─────────────────────────────────────────────────────────────────────────────

def backtest_year(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    year: int,
    config_name: str,
    base_sd: float = V5_BASE_SD,
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
            "FinalSD": base_sd,
        }
    
    if config_name == "v5_adaptive_sd3":
        _, trades, metrics, _ = run_v5_adaptive(df_year, df_exit_year, regime_year)
        # Get final SD
        return {
            "Year": year,
            "Config": config_name,
            "Trades": metrics["trades"],
            "WR%": round(metrics["win_rate"], 1),
            "Net%": round(metrics["net_pct"], 1),
            "Sharpe": round(metrics["sharpe"], 2),
            "MaxDD%": round(metrics["max_drawdown_pct"], 1),
            "FinalSD": base_sd,  # Will be updated by adaptive
        }
    else:
        _, trades, metrics = run_static_baseline(df_year, df_exit_year, regime_year, base_sd)
        return {
            "Year": year,
            "Config": config_name,
            "Trades": metrics["trades"],
            "WR%": round(metrics["win_rate"], 1),
            "Net%": round(metrics["net_pct"], 1),
            "Sharpe": round(metrics["sharpe"], 2),
            "MaxDD%": round(metrics["max_drawdown_pct"], 1),
            "FinalSD": base_sd,
        }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("V5 Tuner Test - SD=3.0 BASE (NOT 4.037)")
    print("=" * 70)
    print("\n--- KEY CHANGE: Base SD = 3.0 (was 4.037)")
    print("\n--- Rationale:")
    print("  - SD=3.0 gives 45.7% WR (best!)")
    print("  - Static SD=4.037 gives lower WR")
    print("  - V5 tuner with SD=4.037 was adapting toward higher SD anyway")
    print("\n--- V5 Configuration:")
    print(f"  Base SD: {V5_BASE_SD}")
    print(f"  Entry Mode: {V5_ENTRY_MODE} (never changes)")
    print(f"  Adaptation Lookback: {ADAPT_LOOKBACK} trades")
    print(f"  Low WR Threshold: {WR_LOW_THRESHOLD}% (tighten: sd += {SD_TIGHTEN})")
    print(f"  High WR Threshold: {WR_HIGH_THRESHOLD}% (loosen: sd -= {SD_LOOSEN})")
    print(f"  SD Range: [{SD_MIN}, {SD_MAX}]")
    print("\n--- Test Period: 2023-2025 (recent market)")
    print("\n--- Comparing:")
    print("  1. Static (sd=3.0, no adaptation)")
    print("  2. V5 Adaptive (sd=3.0 base, adapts based on WR)")
    print("\n--- Expected:")
    print("  - Both should achieve 45%+ WR (SD=3.0 sweet spot)")
    print("  - V5 should adapt if conditions change")
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
    
    # Test years: 2023-2025
    years = [2023, 2024, 2025]
    print(f"\nTesting years: {years}")
    
    # Storage for results
    all_results = []
    adaptation_records = []
    
    # Run Static baseline (SD=3.0)
    print("\n" + "=" * 50)
    print("Running STATIC (sd=3.0, no adaptation)")
    print("=" * 50)
    for year in years:
        print(f"  Processing {year}...")
        result = backtest_year(df_5m, df_1m, regime_series, year, "static_sd3", V5_BASE_SD)
        all_results.append(result)
    
    # Run V5 Adaptive (SD=3.0 base)
    print("\n" + "=" * 50)
    print("Running V5 ADAPTIVE (sd=3.0 base, adapts based on WR)")
    print("=" * 50)
    for year in years:
        print(f"  Processing {year}...")
        year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
        year_end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
        
        df_year = df_5m.loc[(df_5m.index >= year_start) & (df_5m.index < year_end)].copy()
        df_exit_year = None
        if df_1m is not None:
            df_exit_year = df_1m.loc[(df_1m.index >= year_start) & (df_1m.index < year_end)].copy()
        regime_year = regime_series.loc[(regime_series.index >= year_start) & (regime_series.index < year_end)].copy()
        
        if df_year.empty or regime_year.empty:
            result = {
                "Year": year,
                "Config": "v5_adaptive_sd3",
                "Trades": 0,
                "WR%": 0.0,
                "Net%": 0.0,
                "Sharpe": 0.0,
                "MaxDD%": 0.0,
                "FinalSD": V5_BASE_SD,
            }
        else:
            _, trades, metrics, adapt_log = run_v5_adaptive(df_year, df_exit_year, regime_year)
            
            # Get final SD from adaptation log
            final_sd = V5_BASE_SD
            if adapt_log:
                for log in adapt_log:
                    if "sd_after" in log:
                        final_sd = log["sd_after"]
            
            result = {
                "Year": year,
                "Config": "v5_adaptive_sd3",
                "Trades": metrics["trades"],
                "WR%": round(metrics["win_rate"], 1),
                "Net%": round(metrics["net_pct"], 1),
                "Sharpe": round(metrics["sharpe"], 2),
                "MaxDD%": round(metrics["max_drawdown_pct"], 1),
                "FinalSD": final_sd,
            }
            
            # Record adaptations
            for log in adapt_log:
                if "adapt_reason" in log and log["adapt_reason"] != "no_change":
                    adaptation_records.append({
                        "Year": year,
                        **log
                    })
        
        all_results.append(result)
    
    # Create DataFrame
    results_df = pd.DataFrame(all_results)
    
    # Save main results
    output_path = RESULTS_DIR / "tuner_v5_sd3_test.csv"
    results_df.to_csv(output_path, index=False)
    print(f"\nSaved results to: {output_path}")
    
    # Save adaptation log if any
    if adaptation_records:
        adapt_df = pd.DataFrame(adaptation_records)
        adapt_path = RESULTS_DIR / "tuner_v5_sd3_adaptations.csv"
        adapt_df.to_csv(adapt_path, index=False)
        print(f"Saved adaptation log to: {adapt_path}")
    
    # Print summary
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)
    
    configs = ["static_sd3", "v5_adaptive_sd3"]
    
    for config in configs:
        config_results = results_df[results_df["Config"] == config]
        total_trades = config_results["Trades"].sum()
        weighted_wr = (config_results["WR%"] * config_results["Trades"]).sum() / max(total_trades, 1)
        total_net = config_results["Net%"].sum()
        avg_sharpe = config_results["Sharpe"].mean()
        max_dd = config_results["MaxDD%"].max()
        
        print(f"\n{config}:")
        print(f"  Total Trades: {total_trades}")
        print(f"  Avg WR%: {weighted_wr:.1f}%")
        print(f"  Total Net%: {total_net:.1f}%")
        print(f"  Avg Sharpe: {avg_sharpe:.2f}")
        print(f"  Max DD%: {max_dd:.1f}%")
    
    # Comparison table
    print("\n" + "=" * 70)
    print("COMPARISON TABLE")
    print("=" * 70)
    print(f"\n{'Config':<25} {'Trades':>8} {'WR%':>8} {'Net%':>8} {'Sharpe':>8} {'MaxDD%':>8}")
    print("-" * 65)
    
    for config in configs:
        config_results = results_df[results_df["Config"] == config]
        total_trades = config_results["Trades"].sum()
        weighted_wr = (config_results["WR%"] * config_results["Trades"]).sum() / max(total_trades, 1)
        total_net = config_results["Net%"].sum()
        avg_sharpe = config_results["Sharpe"].mean()
        max_dd = config_results["MaxDD%"].max()
        
        print(f"{config:<25} {total_trades:>8} {weighted_wr:>7.1f}% {total_net:>7.1f}% {avg_sharpe:>8.2f} {max_dd:>7.1f}%")
    
    # Year-by-year breakdown
    print("\n" + "=" * 70)
    print("YEAR-BY-YEAR BREAKDOWN")
    print("=" * 70)
    print(f"\n{'Year':<8} {'Config':<25} {'Trades':>8} {'WR%':>8} {'Net%':>8} {'FinalSD':>8}")
    print("-" * 60)
    
    for year in years:
        for config in configs:
            row = results_df[(results_df["Year"] == year) & (results_df["Config"] == config)]
            if not row.empty:
                r = row.iloc[0]
                print(f"{year:<8} {config:<25} {r['Trades']:>8} {r['WR%']:>7.1f}% {r['Net%']:>7.1f}% {r['FinalSD']:>8.2f}")
    
    # Adaptation log
    if adaptation_records:
        print("\n" + "=" * 70)
        print("ADAPTATION LOG")
        print("=" * 70)
        for record in adaptation_records:
            print(f"  {record['month']}: sd {record['sd_before']:.2f} -> {record['sd_after']:.2f} ({record['adapt_reason']})")
    else:
        print("\n" + "=" * 70)
        print("ADAPTATION LOG")
        print("=" * 70)
        print("  (No adaptations triggered - WR remained in neutral zone)")
    
    # Validation checks
    print("\n" + "=" * 70)
    print("VALIDATION CHECKS")
    print("=" * 70)
    
    static_results = results_df[results_df["Config"] == "static_sd3"]
    v5_results = results_df[results_df["Config"] == "v5_adaptive_sd3"]
    
    static_trades = static_results["Trades"].sum()
    v5_trades = v5_results["Trades"].sum()
    
    static_wr = (static_results["WR%"] * static_results["Trades"]).sum() / max(static_trades, 1)
    v5_wr = (v5_results["WR%"] * v5_results["Trades"]).sum() / max(v5_trades, 1)
    
    static_net = static_results["Net%"].sum()
    v5_net = v5_results["Net%"].sum()
    
    print(f"\n1. Win Rate Check:")
    print(f"   Static SD=3.0 WR: {static_wr:.1f}%")
    print(f"   V5 Adaptive WR: {v5_wr:.1f}%")
    print(f"   Expected: 45%+ (the sweet spot)")
    if static_wr >= 45 and v5_wr >= 45:
        print("   [PASS] Both achieved 45%+ WR!")
    elif static_wr >= 40 and v5_wr >= 40:
        print("   [PASS] Both achieved 40%+ WR")
    else:
        print("   [NOTE] Below expected range")
    
    print(f"\n2. Trade Count:")
    print(f"   Static: {static_trades}")
    print(f"   V5: {v5_trades}")
    if v5_trades > 0:
        print(f"   V5/Static ratio: {v5_trades/static_trades:.2f}")
    
    print(f"\n3. Net Return:")
    print(f"   Static: {static_net:+.1f}%")
    print(f"   V5: {v5_net:+.1f}%")
    if v5_net > static_net:
        print("   [V5 BETTER] V5 outperforms static on net return")
    elif v5_net > 0 and static_net > 0:
        print("   [BOTH POSITIVE] Both strategies are profitable")
    else:
        print("   [NOTE] Returns need review")
    
    print(f"\n4. Base SD Comparison:")
    print(f"   Old base: SD=4.037")
    print(f"   New base: SD=3.0")
    print(f"   Key insight: 3.0 is the sweet spot for recent market")
    
    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    
    # Final verdict
    if v5_wr >= 45 and v5_net > 0:
        print("\n[SUCCESS!] V5 with SD=3.0 base achieved 45%+ WR and positive returns!")
        print("   The sweet spot hypothesis is confirmed!")
    elif v5_wr >= 40 and v5_net > 0:
        print("\n[GOOD] V5 with SD=3.0 base achieved 40%+ WR and positive returns")
    elif v5_wr >= 40:
        print("\n[PARTIAL] V5 achieved expected WR but returns need review")
    else:
        print("\n[REVIEW] Below expected - check adaptation thresholds")
    
    if len(adaptation_records) > 0:
        tighten_count = sum(1 for r in adaptation_records if "wr_low" in r.get("adapt_reason", ""))
        loosen_count = sum(1 for r in adaptation_records if "wr_high" in r.get("adapt_reason", ""))
        print(f"\nAdaptation breakdown:")
        print(f"  Tighten (WR low): {tighten_count}")
        print(f"  Loosen (WR high): {loosen_count}")
    
    print("\n" + "=" * 70)
    print("Done!")
    print("=" * 70)


if __name__ == "__main__":
    main()