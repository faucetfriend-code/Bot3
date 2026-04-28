#!/usr/bin/env python3
"""
VWAP RANGING Regime Test - Exact Parameters from +240% Analysis
================================================================

Test the winning configuration from analysis:
- RANGING with sd=0.5, entry_mode=cross → should give +240%
- Compare with current tuned (sd=3.5, mean_reversion)
- Also test BEAR_WEAK with bull_pullback

Period: 2018-2025 (RANGING only)
"""
import sys
from pathlib import Path
from typing import Tuple, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.regime_detector import RegimeDetector, MarketRegime
from BTV2.strategies import (
    run_vwap_scalping,
    compute_reference_levels,
    compute_metrics,
    COST_PER_SIDE,
    INTERVAL_BARS_PER_YEAR,
)

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TEST_START = "2018-01-01"
TEST_END = "2026-01-01"
CUTOFF = 0.10


def load_data(start: str, end: str) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """Load 5m and 1m data."""
    path_5m = DATA_DIR / "BTCUSDT_5m.parquet"
    path_1m = DATA_DIR / "BTCUSDT_1m.parquet"
    
    df_5m = None
    if path_5m.exists():
        df_5m = pd.read_parquet(path_5m)
        df_5m.index = pd.to_datetime(df_5m.index, utc=True)
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        mask = (df_5m.index >= start_ts) & (df_5m.index < end_ts)
        df_5m = df_5m.loc[mask].copy()
    
    df_1m = None
    if path_1m.exists():
        df_1m = pd.read_parquet(path_1m)
        df_1m.index = pd.to_datetime(df_1m.index, utc=True)
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        mask = (df_1m.index >= start_ts) & (df_1m.index < end_ts)
        df_1m = df_1m.loc[mask].copy()
    
    return df_5m, df_1m


def detect_regimes(df: pd.DataFrame) -> pd.Series:
    """Detect market regimes."""
    detector = RegimeDetector(df, method="combined", timeframe="15m")
    regimes = detector.get_regimes()
    return regimes


def run_vwap_regime_test(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    regime_series: pd.Series,
    regime: MarketRegime,
    entry_mode: str,
    sd_threshold: float,
    atr_stop: float = 2.0,
    atr_target: float = 2.0,
    trailing_atr: float = 1.5,
    name: str = "",
) -> dict:
    """Run VWAP backtest for a specific regime only."""
    levels = compute_reference_levels(df)
    
    params = {
        "sd_threshold": sd_threshold,
        "atr_stop": atr_stop,
        "atr_target": atr_target,
        "trailing_atr": trailing_atr,
        "ema_fast": 9,
        "ema_slow": 20,
        "use_trend_filter": False,
        "use_volume_filter": True,
        "volume_mult": 1.5,
        "use_trailing_stop": True,
        "adx_max": 30.0,
        "rsi_max": 55.0,
        "entry_mode": entry_mode,
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
        "levels": levels,
        "regime_series": regime_series,
        "allowed_regimes": [regime],  # KEY: Only this regime
    }
    
    equity, trades = run_vwap_scalping(
        df,
        CUTOFF,
        df_exit=df_exit,
        **params,
    )
    
    # Calculate metrics
    if len(trades) == 0:
        return {
            "name": name,
            "regime": regime.value,
            "trades": 0,
            "win_rate": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "net_return": 0.0,
            "profit_factor": 0.0,
            "sharpe": 0.0,
            "max_dd": 0.0,
        }
    
    wins = sum(1 for t in trades if t > 0)
    win_rate = wins / len(trades) * 100
    gross_profit = sum(t for t in trades if t > 0)
    gross_loss = abs(sum(t for t in trades if t < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.99
    
    costs = len(trades) * COST_PER_SIDE
    m = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR.get("5m", 105120))
    net_return = m["total_return_pct"] - costs
    
    return {
        "name": name,
        "regime": regime.value,
        "trades": len(trades),
        "win_rate": round(win_rate, 1),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_return": round(net_return, 2),
        "profit_factor": round(profit_factor, 2),
        "sharpe": round(m.get("sharpe", 0), 2),
        "max_dd": round(m.get("max_dd_pct", 0), 2),
    }


def main():
    print("=" * 70)
    print("VWAP RANGING REGIME TEST - EXACT PARAMETERS")
    print("=" * 70)
    
    # Load data
    print("\nLoading data...")
    df_5m, df_1m = load_data(TEST_START, TEST_END)
    
    if df_5m is None:
        print("ERROR: Could not load 5m data")
        sys.exit(1)
    
    print(f"5m data: {len(df_5m):,} bars ({df_5m.index[0]} to {df_5m.index[-1]})")
    if df_1m is not None:
        print(f"1m data: {len(df_1m):,} bars")
    
    # Detect regimes
    print("\nDetecting regimes...")
    regimes = detect_regimes(df_5m)
    regime_counts = regimes.value_counts()
    print("\nRegime distribution (full period):")
    for r, count in regime_counts.items():
        pct = count / len(regimes) * 100
        print(f"  {r.value:15} {count:6,} ({pct:.1f}%)")
    
    results = []
    
    # =======================================================================
    # TEST 1: RANGING with sd=0.5, entry_mode=cross (THE WINNING CONFIG)
    # =======================================================================
    print("\n" + "=" * 70)
    print("TEST 1: RANGING + sd=0.5 + cross (EXACT WINNING CONFIG)")
    print("=" * 70)
    
    result1 = run_vwap_regime_test(
        df_5m, df_1m, regimes,
        regime=MarketRegime.RANGING,
        entry_mode="cross",
        sd_threshold=0.5,
        atr_stop=3.0,
        atr_target=6.0,
        trailing_atr=1.2,
        name="RANGING_sd0.5_cross",
    )
    
    print(f"\nRANGING + sd=0.5 + cross:")
    print(f"  Trades: {result1['trades']}")
    print(f"  Win Rate: {result1['win_rate']}%")
    print(f"  Net Return: {result1['net_return']:+.2f}%")
    print(f"  Profit Factor: {result1['profit_factor']}")
    print(f"  Sharpe: {result1['sharpe']}")
    print(f"  Max DD: {result1['max_dd']}%")
    results.append(result1)
    
    # =======================================================================
    # TEST 2: RANGING with sd=3.5, entry_mode=mean_reversion (CURRENT TUNED)
    # =======================================================================
    print("\n" + "=" * 70)
    print("TEST 2: RANGING + sd=3.5 + mean_reversion (CURRENT TUNED)")
    print("=" * 70)
    
    result2 = run_vwap_regime_test(
        df_5m, df_1m, regimes,
        regime=MarketRegime.RANGING,
        entry_mode="mean_reversion",
        sd_threshold=3.5,
        atr_stop=1.5,
        atr_target=2.0,
        trailing_atr=1.5,
        name="RANGING_sd3.5_mean_reversion",
    )
    
    print(f"\nRANGING + sd=3.5 + mean_reversion:")
    print(f"  Trades: {result2['trades']}")
    print(f"  Win Rate: {result2['win_rate']}%")
    print(f"  Net Return: {result2['net_return']:+.2f}%")
    print(f"  Profit Factor: {result2['profit_factor']}")
    print(f"  Sharpe: {result2['sharpe']}")
    print(f"  Max DD: {result2['max_dd']}%")
    results.append(result2)
    
    # =======================================================================
    # TEST 3: BEAR_WEAK with bull_pullback (different params for this regime)
    # =======================================================================
    print("\n" + "=" * 70)
    print("TEST 3: BEAR_WEAK + sd=3.0 + bull_pullback")
    print("=" * 70)
    
    result3 = run_vwap_regime_test(
        df_5m, df_1m, regimes,
        regime=MarketRegime.BEAR_WEAK,
        entry_mode="bull_pullback",
        sd_threshold=3.0,
        atr_stop=1.5,
        atr_target=2.0,
        trailing_atr=1.5,
        name="BEAR_WEAK_bull_pullback",
    )
    
    print(f"\nBEAR_WEAK + sd=3.0 + bull_pullback:")
    print(f"  Trades: {result3['trades']}")
    print(f"  Win Rate: {result3['win_rate']}%")
    print(f"  Net Return: {result3['net_return']:+.2f}%")
    print(f"  Profit Factor: {result3['profit_factor']}")
    print(f"  Sharpe: {result3['sharpe']}")
    print(f"  Max DD: {result3['max_dd']}%")
    results.append(result3)
    
    # =======================================================================
    # Summary
    # =======================================================================
    print("\n" + "=" * 70)
    print("SUMMARY COMPARISON")
    print("=" * 70)
    print(f"{'Config':<30} {'Regime':<12} {'Trades':>6} {'WR%':>6} {'Net%':>10} {'PF':>6}")
    print("-" * 70)
    for r in results:
        print(f"{r['name']:<30} {r['regime']:<12} {r['trades']:>6} {r['win_rate']:>5.1f}% {r['net_return']:>+9.2f}% {r['profit_factor']:>5.2f}")
    
    # Save CSV
    import csv
    csv_path = RESULTS_DIR / "vwap_ranging_test.csv"
    fieldnames = ["name", "regime", "trades", "win_rate", "gross_profit", "gross_loss", 
                  "net_return", "profit_factor", "sharpe", "max_dd"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    
    print(f"\nCSV saved: {csv_path}")
    
    # Key insight
    print("\n" + "=" * 70)
    print("KEY INSIGHT")
    print("=" * 70)
    if result1['net_return'] > result2['net_return']:
        diff = result1['net_return'] - result2['net_return']
        print(f"✓ sd=0.5 + cross OUTPERFORMS sd=3.5 + mean_reversion by {diff:.1f}%")
    else:
        diff = result2['net_return'] - result1['net_return']
        print(f"✗ sd=3.5 + mean_reversion OUTPERFORMS sd=0.5 + cross by {diff:.1f}%")
        print(f"  (Expected: sd=0.5 + cross should give +240%)")


if __name__ == "__main__":
    main()