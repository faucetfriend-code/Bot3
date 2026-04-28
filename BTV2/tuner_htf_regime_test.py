"""
tuner_htf_regime_test.py
=========================
Test the on-the-fly tuner with the improved HTF regime detection.

Test Setup:
1. Use 4h timeframe for regime detection with hysteresis=3
2. Use V3 tuner with regime-specific entry modes
3. Period: 2018-2025 (full data)

Compare:
- V3 tuner with old 5m regime
- V3 tuner with new 4h regime

Metrics:
- Win Rate
- Net%
- Trade count

Output: BTV2/results/tuner_htf_regime_test.csv
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from datetime import date
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

# Test period - full 2018-2025
TEST_START = "2018-01-01"
TEST_END = "2026-01-01"

# Quick test with 2023-2025 only (faster)
QUICK_TEST = False  # Set to True for quick test
if QUICK_TEST:
    TEST_START = "2023-01-01"
    TEST_END = "2026-01-01"
else:
    # Full test 2018-2025 - this will be slow!
    TEST_START = "2018-01-01"
    TEST_END = "2026-01-01"

# Cutoff for Butterworth filter
CUTOFF = 0.10


# ─────────────────────────────────────────────────────────────────────────────
# OPTIMAL ENTRY MODES PER REGIME (from regime entry matrix)
# ─────────────────────────────────────────────────────────────────────────────

REGIME_ENTRY_MAP = {
    "RANGING": "mean_reversion",     # +63% WR, +241% Net
    "BEAR_WEAK": "bull_pullback",   # +56% WR, +41% Net  
    "BULL_WEAK": "bull_pullback",   # +85% WR, +4% Net
    "BULL_STRONG": "cross",         # +64% WR, +5% Net
    "BEAR_STRONG": "cross",        # +62% WR, +4% Net
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

def run_vwap_filtered(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    allowed_regimes: List[MarketRegime],
    entry_mode: str = "bull_pullback",
    sd_threshold: float = 3.0,
    atr_stop: float = 2.0,
    trailing_atr: float = 1.5,
) -> Tuple[pd.Series, list, Dict[str, float]]:
    """Run VWAP with regime filter - using looser params like the working validation."""
    levels = compute_reference_levels(df)
    
    params = {
        "sd_threshold": sd_threshold,
        "atr_stop": atr_stop,
        "atr_target": 2.0,
        "trailing_atr": trailing_atr,
        "ema_fast": 9,
        "ema_slow": 20,
        "use_trend_filter": False,  # Looser: no trend filter
        "use_volume_filter": True,
        "volume_mult": 1.0,
        "use_trailing_stop": True,
        "adx_max": 30.0,  # Looser: 30.0
        "rsi_max": 55.0,  # Looser: 55.0
        "entry_mode": entry_mode,
        "pullback_bars": 3,
        "use_htf_vwap": False,  # Looser: disabled
        "use_htf_ema": False,  # Looser: disabled
        "htf_adx_max": 30.0,
        "stoch_oversold": 40,
        "stoch_overbought": 60,
        "use_anchored_vwap": True,
        "use_session_filter": False,  # Looser: disabled - KEY
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
        "levels": levels,
        "regime_series": regime_series,
        "allowed_regimes": allowed_regimes,
    }
    
    equity, trades = run_vwap_scalping(
        df,
        CUTOFF,
        df_exit=df_exit,
        **params,
    )
    
    metrics = compute_metrics_for_result(equity, trades)
    return equity, trades, metrics


def run_v3_composite(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
) -> Tuple[pd.Series, list, Dict[str, float]]:
    """
    Run V3 as COMPOSITE strategy:
    For each regime, run with its optimal entry mode, then combine results.
    Uses the exact params from vwap_regime_entry_matrix.py that generated +240% for RANGING:
    Key: sd_threshold=3.0, atr_stop=2.0, trailing_atr=1.5
    """
    all_trades = []
    
    # Run each regime with its optimal entry mode
    regime_combos = [
        (MarketRegime.RANGING, "mean_reversion"),
        (MarketRegime.BEAR_WEAK, "bull_pullback"),
        (MarketRegime.BULL_WEAK, "bull_pullback"),
        (MarketRegime.BEAR_STRONG, "cross"),
        (MarketRegime.BULL_STRONG, "cross"),
    ]
    
    for regime, entry_mode in regime_combos:
        try:
            # Use the exact params that worked
            _, trades, _ = run_vwap_filtered(
                df, df_exit, regime_series, [regime], entry_mode,
                sd_threshold=3.0,  # Key param from working validation
                atr_stop=2.0,     # Key param from working validation
                trailing_atr=1.5,  # Key param from working validation
            )
            all_trades.extend(trades)
        except Exception as e:
            pass  # Skip failed combos
    
    # Calculate composite metrics from combined trades
    if not all_trades:
        return pd.Series(), [], {"trades": 0, "win_rate": 0, "net_pct": 0, "sharpe": 0, "max_drawdown_pct": 0}
    
    wins = [t for t in all_trades if t > 0]
    losses = [t for t in all_trades if t < 0]
    wr = len(wins) / len(all_trades) * 100
    net = sum(all_trades) * 100
    
    return pd.Series(), all_trades, {
        "trades": len(all_trades),
        "win_rate": round(wr, 1),
        "net_pct": round(net, 1),
        "sharpe": 0.0,
        "max_drawdown_pct": 0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# YEAR-BY-YEAR BACKTEST
# ─────────────────────────────────────────────────────────────────────────────

def backtest_year(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    year: int,
    config_name: str,
    entry_mode: str = None,
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
    
    if config_name.startswith("v3"):
        _, trades, metrics = run_v3_composite(df_year, df_exit_year, regime_year)
    else:
        all_regimes = [MarketRegime.RANGING, MarketRegime.BEAR_WEAK, MarketRegime.BULL_WEAK,
                      MarketRegime.BEAR_STRONG, MarketRegime.BULL_STRONG]
        _, trades, metrics = run_vwap_filtered(df_year, df_exit_year, regime_year, all_regimes, entry_mode)
    
    return {
        "Year": year,
        "Config": config_name,
        "Trades": metrics["trades"],
        "WR%": round(metrics["win_rate"], 1),
        "Net%": round(metrics["net_pct"], 1),
        "Sharpe": round(metrics["sharpe"], 2),
        "MaxDD%": round(metrics["max_drawdown_pct"], 1),
    }


def get_regime_flips(regime_series: pd.Series) -> int:
    """Count regime flips."""
    if len(regime_series) < 2:
        return 0
    flips = (regime_series != regime_series.shift(1)).sum()
    return int(flips)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("VWAP Tuner V3 - HTF Regime Detection Test")
    print("=" * 70)
    print("\n--- Test Period: 2018-01-01 to 2025-12-31 (full data)")
    print("\n--- Comparing:")
    print("  1. V3 + 5m regime (original, noisy)")
    print("  2. V3 + 4h regime (clean, with hysteresis=3)")
    print("\n--- V3 Entry Modes Per Regime:")
    for regime, mode in REGIME_ENTRY_MAP.items():
        print(f"    {regime}: {mode}")
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
    print("\n--- Computing regimes...")
    
    # 1. 5m regime (original, on the 5m data itself)
    print("\n[1] Computing 5m regime (original)...")
    detector_5m = RegimeDetector(df_5m, method="combined", timeframe="15m")  # Match working validation
    regime_5m = detector_5m.get_regimes()
    flips_5m = get_regime_flips(regime_5m)
    print(f"  5m regime flips: {flips_5m:,}")
    
    # 2. 4h regime (HTF, with hysteresis=3)
    print("\n[2] Computing 4h regime (HTF, hysteresis=3)...")
    detector_4h = RegimeDetector(df_5m, method="combined", timeframe="4h")
    regime_4h = detector_4h.get_htf_regime_series("4h", hysteresis=3)
    flips_4h = get_regime_flips(regime_4h)
    print(f"  4h regime flips: {flips_4h:,}")
    
    # Report flip reduction
    reduction = (1 - flips_4h/flips_5m) * 100 if flips_5m > 0 else 0
    print(f"\n  Flip reduction: {reduction:.1f}%")
    
    # Test years
    years = list(range(2018, 2026))
    print(f"\nTesting years: {years}")
    
    # Storage for results
    all_results = []
    
    # 1. V3 + 5m regime
    print("\n" + "=" * 50)
    print("Running V3 + 5m regime")
    print("=" * 50)
    for year in years:
        print(f"  Processing {year}...")
        result = backtest_year(df_5m, df_1m, regime_5m, year, "v3_5m")
        all_results.append(result)
    
    # 2. V3 + 4h regime
    print("\n" + "=" * 50)
    print("Running V3 + 4h regime (hysteresis=3)")
    print("=" * 50)
    for year in years:
        print(f"  Processing {year}...")
        result = backtest_year(df_5m, df_1m, regime_4h, year, "v3_4h")
        all_results.append(result)
    
    # Create DataFrame
    results_df = pd.DataFrame(all_results)
    
    # Save to CSV
    output_path = RESULTS_DIR / "tuner_htf_regime_test.csv"
    results_df.to_csv(output_path, index=False)
    print(f"\nSaved results to: {output_path}")
    
    # Print summary
    print("\n" + "=" * 70)
    print("COMPARISON SUMMARY")
    print("=" * 70)
    
    configs = ["v3_5m", "v3_4h"]
    
    # Group by config
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
    
    # Summary comparison table
    print("\n" + "=" * 70)
    print("COMPARISON TABLE")
    print("=" * 70)
    print(f"\n{'Config':<20} {'Trades':>8} {'WR%':>8} {'Net%':>8} {'Sharpe':>8} {'MaxDD%':>8}")
    print("-" * 60)
    
    for config in configs:
        config_results = results_df[results_df["Config"] == config]
        total_trades = config_results["Trades"].sum()
        weighted_wr = (config_results["WR%"] * config_results["Trades"]).sum() / max(total_trades, 1)
        total_net = config_results["Net%"].sum()
        avg_sharpe = config_results["Sharpe"].mean()
        max_dd = config_results["MaxDD%"].max()
        
        print(f"{config:<20} {total_trades:>8} {weighted_wr:>7.1f}% {total_net:>7.1f}% {avg_sharpe:>8.2f} {max_dd:>7.1f}%")
    
    # Final Analysis
    print("\n" + "=" * 70)
    print("ANALYSIS")
    print("=" * 70)
    
    v3_5m_trades = results_df[results_df["Config"] == "v3_5m"]["Trades"].sum()
    v3_4h_trades = results_df[results_df["Config"] == "v3_4h"]["Trades"].sum()
    
    v3_5m_net = results_df[results_df["Config"] == "v3_5m"]["Net%"].sum()
    v3_4h_net = results_df[results_df["Config"] == "v3_4h"]["Net%"].sum()
    
    v3_5m_weighted_wr = (results_df[results_df["Config"] == "v3_5m"]["WR%"] * results_df[results_df["Config"] == "v3_5m"]["Trades"]).sum() / max(v3_5m_trades, 1)
    v3_4h_weighted_wr = (results_df[results_df["Config"] == "v3_4h"]["WR%"] * results_df[results_df["Config"] == "v3_4h"]["Trades"]).sum() / max(v3_4h_trades, 1)
    
    print(f"\n1. Trade Count:")
    print(f"   V3 + 5m: {v3_5m_trades}")
    print(f"   V3 + 4h: {v3_4h_trades}")
    
    print(f"\n2. Win Rate (weighted):")
    print(f"   V3 + 5m: {v3_5m_weighted_wr:.1f}%")
    print(f"   V3 + 4h: {v3_4h_weighted_wr:.1f}%")
    
    print(f"\n3. Net Return:")
    print(f"   V3 + 5m: {v3_5m_net:+.1f}%")
    print(f"   V3 + 4h: {v3_4h_net:+.1f}%")
    
    print(f"\n4. Regime Flip Comparison:")
    print(f"   5m flips: {flips_5m:,}")
    print(f"   4h flips: {flips_4h:,} (hysteresis=3)")
    print(f"   Reduction: {reduction:.1f}%")
    
    # Determine winner
    print("\n" + "=" * 70)
    print("VERDICT")
    print("=" * 70)
    
    if v3_4h_weighted_wr > v3_5m_weighted_wr:
        print(f"\n  4h regime has HIGHER WR: {v3_4h_weighted_wr:.1f}% vs {v3_5m_weighted_wr:.1f}%")
    else:
        print(f"\n  5m regime has HIGHER WR: {v3_5m_weighted_wr:.1f}% vs {v3_4h_weighted_wr:.1f}%")
    
    if v3_4h_net > v3_5m_net:
        print(f"  4h regime has HIGHER Net%: {v3_4h_net:.1f}% vs {v3_5m_net:.1f}%")
    else:
        print(f"  5m regime has HIGHER Net%: {v3_5m_net:.1f}% vs {v3_4h_net:.1f}%")
    
    if v3_4h_trades < v3_5m_trades:
        print(f"  4h regime has FEWER trades: {v3_4h_trades} vs {v3_5m_trades}")
    
    print("\n" + "=" * 70)
    print("Done!")
    print("=" * 70)


if __name__ == "__main__":
    main()
