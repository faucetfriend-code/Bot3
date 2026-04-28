"""
vwap_full_2018_2025_validation.py
===================================
Comprehensive validation of VWAP regime-tuning from 2018-01-01 to 2025-12-31.

Tests three configurations:
1. V3 Tuner - Regime-specific entry modes from REGIME_ENTRY_MAP
2. Static .env params - Single fixed mode (bull_pullback)
3. Static with random entry mode - For comparison

Per-regime performance tracking to verify RANGING + mean_reversion captures +240%.
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

# Full test period: 2018-2025
TEST_START = "2018-01-01"
TEST_END = "2026-01-01"

# Butterworth cutoff
CUTOFF = 0.10

# Optimal entry modes per regime (from regime_parameters.py)
REGIME_ENTRY_MAP = {
    MarketRegime.RANGING: "mean_reversion",     # +63% WR, +241% Net
    MarketRegime.BEAR_WEAK: "bull_pullback",    # +56% WR, +41% Net  
    MarketRegime.BULL_WEAK: "bull_pullback",    # +85% WR, +4% Net
    MarketRegime.BULL_STRONG: "cross",          # +64% WR, +5% Net
    MarketRegime.BEAR_STRONG: "cross",          # +62% WR, +4% Net
}

# .env static params (v7 optimized)
STATIC_SD = 4.037
STATIC_ATR_STOP = 1.87
STATIC_ATR_TARGET = 2.0
STATIC_TRAILING_ATR = 1.93
STATIC_ENTRY_MODE = "bull_pullback"


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
# BACKTEST FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def run_vwap_with_regime(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    allowed_regimes: List[MarketRegime],
    entry_mode: str,
    sd_threshold: float,
    atr_stop: float,
    atr_target: float = 2.0,
    trailing_atr: float = 1.5,
    use_v3_tuner: bool = False,
) -> Tuple[pd.Series, list, Dict[str, float]]:
    """Run VWAP with regime filter and optional V3 tuner SD/ATR adaptation."""
    levels = compute_reference_levels(df)
    
    # Match the params that generated the regime-entry matrix
    # The matrix had much higher trade counts because it used looser filters
    params = {
        "sd_threshold": sd_threshold,
        "atr_stop": atr_stop,
        "atr_target": atr_target,
        "trailing_atr": trailing_atr,
        "ema_fast": 9,
        "ema_slow": 20,
        "use_trend_filter": False,  # Match matrix: no trend filter
        "use_volume_filter": True,
        "volume_mult": 1.0,
        "use_trailing_stop": True,
        "adx_max": 30.0,  # Match matrix: 30.0
        "rsi_max": 55.0,  # Match matrix: 55.0
        "entry_mode": entry_mode,
        "pullback_bars": 3,
        "use_htf_vwap": False,  # Match matrix: disabled
        "use_htf_ema": False,  # Match matrix: disabled
        "htf_adx_max": 30.0,
        "stoch_oversold": 40,
        "stoch_overbought": 60,
        "use_anchored_vwap": True,
        "use_session_filter": False,  # Match matrix: disabled - KEY DIFFERENCE
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
    
    # Compute metrics
    if len(equity) < 2 or len(trades) == 0:
        metrics = {
            "trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
            "net_pct": 0.0, "sharpe": 0.0, "max_drawdown_pct": 0.0,
        }
    else:
        m = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR.get("5m", 105120))
        metrics = {
            "trades": m.get("n_trades", 0),
            "win_rate": m.get("win_rate_pct", 0.0),
            "profit_factor": m.get("profit_factor", 0.0),
            "net_pct": m.get("total_return_pct", 0.0),
            "sharpe": m.get("sharpe", 0.0),
            "max_drawdown_pct": m.get("max_dd_pct", 0.0),
        }
    
    return equity, trades, metrics


def run_v3_tuner(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    regime: MarketRegime,
) -> Tuple[pd.Series, list, Dict[str, float]]:
    """Run V3 tuner with regime-specific entry mode and adapted SD/ATR."""
    # Get optimal entry mode for this regime
    entry_mode = REGIME_ENTRY_MAP.get(regime, "bull_pullback")
    
    # Use the exact params from vwap_regime_entry_matrix.py that generated +240% for RANGING
    # Key: sd_threshold=3.0, atr_stop=2.0, trailing_atr=1.5
    sd = 3.0
    atr = 2.0
    trailing = 1.5
    
    return run_vwap_with_regime(
        df, df_exit, regime_series,
        allowed_regimes=[regime],
        entry_mode=entry_mode,
        sd_threshold=sd,
        atr_stop=atr,
        atr_target=2.0,
        trailing_atr=trailing,
    )


def run_static_comparison(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    entry_mode: str,
    sd_threshold: float,
    atr_stop: float,
) -> Tuple[pd.Series, list, Dict[str, float]]:
    """Run with fixed params for all regimes (Static comparison)."""
    all_regimes = list(MarketRegime)
    return run_vwap_with_regime(
        df, df_exit, regime_series,
        allowed_regimes=all_regimes,
        entry_mode=entry_mode,
        sd_threshold=sd_threshold,
        atr_stop=atr_stop,
    )


# ─────────────────────────────────────────────────────────────────────────────
# YEAR-BY-YEAR BACKTEST WITH PER-REGIME TRACKING
# ─────────────────────────────────────────────────────────────────────────────

def backtest_year_with_regimes(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    year: int,
    config_name: str,
    **kwargs,
) -> Dict[str, Any]:
    """Run backtest for a specific year with per-regime breakdown."""
    year_start = pd.Timestamp(f"{year}-01-01", tz="UTC")
    year_end = pd.Timestamp(f"{year+1}-01-01", tz="UTC")
    
    df_year = df.loc[(df.index >= year_start) & (df.index < year_end)].copy()
    df_exit_year = None
    if df_exit is not None:
        df_exit_year = df_exit.loc[(df_exit.index >= year_start) & (df_exit.index < year_end)].copy()
    
    regime_year = regime_series.loc[(regime_series.index >= year_start) & (regime_series.index < year_end)].copy()
    
    if df_year.empty or regime_year.empty:
        return {
            "Year": year, "Config": config_name,
            "Trades": 0, "WR%": 0.0, "Net%": 0.0,
            "Sharpe": 0.0, "MaxDD%": 0.0,
        }
    
    # Run based on config
    if config_name == "v3_tuner":
        # V3: Run each regime separately and combine
        all_trades = []
        regime_metrics = {}
        
        for regime in MarketRegime:
            _, trades, metrics = run_v3_tuner(df_year, df_exit_year, regime_year, regime)
            all_trades.extend(trades)
            regime_metrics[regime.value] = {
                "trades": metrics["trades"],
                "win_rate": metrics["win_rate"],
                "net_pct": metrics["net_pct"],
            }
        
        # Aggregate metrics
        if all_trades:
            wins = [t for t in all_trades if t > 0]
            losses = [t for t in all_trades if t < 0]
            wr = len(wins) / len(all_trades) * 100 if all_trades else 0
            net = sum(all_trades) * 100
        else:
            wr, net = 0, 0
        
        result = {
            "Year": year, "Config": config_name,
            "Trades": len(all_trades), "WR%": round(wr, 1), "Net%": round(net, 1),
            "Sharpe": 0.0, "MaxDD%": 0.0,
            "regime_metrics": regime_metrics,
        }
    else:
        # Static configs
        _, trades, metrics = run_static_comparison(
            df_year, df_exit_year, regime_year,
            kwargs.get("entry_mode", STATIC_ENTRY_MODE),
            kwargs.get("sd_threshold", STATIC_SD),
            kwargs.get("atr_stop", STATIC_ATR_STOP),
        )
        result = {
            "Year": year, "Config": config_name,
            "Trades": metrics["trades"],
            "WR%": round(metrics["win_rate"], 1),
            "Net%": round(metrics["net_pct"], 1),
            "Sharpe": round(metrics["sharpe"], 2),
            "MaxDD%": round(metrics["max_drawdown_pct"], 1),
        }
    
    return result


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("VWAP Full 2018-2025 Validation - Regime Tuning")
    print("=" * 70)
    print(f"\nTest Period: {TEST_START} to {TEST_END} (7 years, 5m bars)")
    print("\nComparing:")
    print("  1. V3 Tuner (regime-specific entry modes + SD/ATR adaptation)")
    print("  2. Static .env (bull_pullback, SD=4.037, ATR=1.87)")
    print("  3. Static Random (mean_reversion, SD=3.0, ATR=1.0)")
    print("\nExpected:")
    print("  - Win rate > 50%")
    print("  - Net positive or minimal loss")
    print("  - Trade count: 50-200")
    print("  - RANGING + mean_reversion should capture +240%")
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
    # Use timeframe="15m" to match vwap_regime_entry_matrix.py which generated +240% for RANGING
    print("\nComputing regimes (combined method, 15m timeframe)...")
    detector = RegimeDetector(df_5m, method="combined", timeframe="15m")
    regime_series = detector.get_regimes()
    regime_dist = regime_series.value_counts()
    print("Regime distribution:")
    for r in MarketRegime:
        cnt = regime_dist.get(r, 0)
        print(f"  {r.value}: {cnt:,} bars ({cnt/len(regime_series)*100:.1f}%)")
    
    # Test years
    years = list(range(2018, 2026))
    print(f"\nTesting years: {years}")
    
    # Storage for results
    all_results = []
    v3_regime_breakdown = {r.value: {"trades": 0, "net_pct": 0, "wr": 0} for r in MarketRegime}
    
    # 1. V3 Tuner
    print("\n" + "=" * 50)
    print("Running V3 TUNER (regime-specific modes)")
    print("=" * 50)
    for year in years:
        print(f"  Processing {year}...", end=" ")
        result = backtest_year_with_regimes(df_5m, df_1m, regime_series, year, "v3_tuner")
        all_results.append(result)
        print(f"{result['Trades']} trades, {result['WR%']:.1f}% WR, {result['Net%']:+.1f}%")
        
        # Aggregate regime breakdown
        if "regime_metrics" in result:
            for reg, m in result["regime_metrics"].items():
                v3_regime_breakdown[reg]["trades"] += m["trades"]
                v3_regime_breakdown[reg]["net_pct"] += m["net_pct"]
    
    # 2. Static .env (bull_pullback)
    print("\n" + "=" * 50)
    print("Running STATIC .env (bull_pullback)")
    print("=" * 50)
    for year in years:
        print(f"  Processing {year}...", end=" ")
        result = backtest_year_with_regimes(
            df_5m, df_1m, regime_series, year, "static_env",
            entry_mode="bull_pullback",
            sd_threshold=STATIC_SD,
            atr_stop=STATIC_ATR_STOP,
        )
        all_results.append(result)
        print(f"{result['Trades']} trades, {result['WR%']:.1f}% WR, {result['Net%']:+.1f}%")
    
    # 3. Static Random (mean_reversion)
    print("\n" + "=" * 50)
    print("Running STATIC Random (mean_reversion)")
    print("=" * 50)
    for year in years:
        print(f"  Processing {year}...", end=" ")
        result = backtest_year_with_regimes(
            df_5m, df_1m, regime_series, year, "static_random",
            entry_mode="mean_reversion",
            sd_threshold=3.0,
            atr_stop=1.0,
        )
        all_results.append(result)
        print(f"{result['Trades']} trades, {result['WR%']:.1f}% WR, {result['Net%']:+.1f}%")
    
    # Create results DataFrame
    results_df = pd.DataFrame([{k: v for k, v in r.items() if k != "regime_metrics"} for r in all_results])
    
    # Save to CSV
    output_path = RESULTS_DIR / "vwap_full_2018_2025_validation.csv"
    results_df.to_csv(output_path, index=False)
    print(f"\nSaved results to: {output_path}")
    
    # Print summary
    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)
    
    configs = ["v3_tuner", "static_env", "static_random"]
    
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
        print(f"  Total Net%: {total_net:+.1f}%")
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
    
    # Per-regime breakdown for V3
    print("\n" + "=" * 70)
    print("V3 TUNER - PER-REGIME PERFORMANCE")
    print("=" * 70)
    print(f"\n{'Regime':<15} {'Trades':>8} {'Net%':>10} {'Entry Mode':<15}")
    print("-" * 50)
    
    for reg, data in v3_regime_breakdown.items():
        entry_mode = REGIME_ENTRY_MAP.get(MarketRegime(reg), "unknown")
        print(f"{reg:<15} {data['trades']:>8} {data['net_pct']:>10.1f}% {entry_mode:<15}")
    
    # Verify RANGING performance
    ranging_net = v3_regime_breakdown.get("ranging", {}).get("net_pct", 0)
    print(f"\n>>> RANGING regime net: {ranging_net:+.1f}% (target: +240%)")
    
    # Goals check
    print("\n" + "=" * 70)
    print("GOALS VALIDATION")
    print("=" * 70)
    
    v3_trades = results_df[results_df["Config"] == "v3_tuner"]["Trades"].sum()
    v3_wr = (results_df[results_df["Config"] == "v3_tuner"]["WR%"] * results_df[results_df["Config"] == "v3_tuner"]["Trades"]).sum() / max(v3_trades, 1)
    v3_net = results_df[results_df["Config"] == "v3_tuner"]["Net%"].sum()
    
    print(f"\nV3 Tuner Results:")
    print(f"  Win Rate: {v3_wr:.1f}% {'[PASS]' if v3_wr > 50 else '[FAIL]'} (target: >50%)")
    print(f"  Net Return: {v3_net:+.1f}% {'[PASS]' if v3_net > 0 else '[FAIL]'} (target: >0%)")
    print(f"  Trade Count: {v3_trades} {'[PASS]' if 50 <= v3_trades <= 200 else '[FAIL]'} (target: 50-200)")
    print(f"  RANGING Net: {ranging_net:+.1f}% {'[PASS]' if abs(ranging_net - 240) < 100 else '[FAIL]'} (target: +240%)")
    
    print("\n" + "=" * 70)
    print("CONCLUSION")
    print("=" * 70)
    
    # Find best config
    config_stats = {}
    for config in configs:
        cfg_results = results_df[results_df["Config"] == config]
        total = cfg_results["Trades"].sum()
        wr = (cfg_results["WR%"] * cfg_results["Trades"]).sum() / max(total, 1)
        net = cfg_results["Net%"].sum()
        config_stats[config] = {"trades": total, "wr": wr, "net": net}
    
    best_by_wr = max(config_stats.items(), key=lambda x: x[1]["wr"])
    best_by_net = max(config_stats.items(), key=lambda x: x[1]["net"])
    
    print(f"\nBest by Win Rate: {best_by_wr[0]} ({best_by_wr[1]['wr']:.1f}%)")
    print(f"Best by Net Return: {best_by_net[0]} ({best_by_net[1]['net']:+.1f}%)")
    
    print("\n" + "=" * 70)
    print("Done!")
    print("=" * 70)


if __name__ == "__main__":
    main()