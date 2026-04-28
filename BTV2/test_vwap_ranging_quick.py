#!/usr/bin/env python3
"""
VWAP Quick RANGING Test - Subset of 2018-2025
==============================================
Focus: RANGING regime with exact parameters
"""
import sys
from pathlib import Path
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

# Use smaller subset for faster testing (2020-2023)
TEST_START = "2020-01-01"
TEST_END = "2024-01-01"
CUTOFF = 0.10


def main():
    print("=" * 70)
    print("VWAP RANGING QUICK TEST - EXACT MATRIX PARAMS")
    print("=" * 70)
    
    # Load 5m data
    print("\nLoading data...")
    path_5m = DATA_DIR / "BTCUSDT_5m.parquet"
    path_1m = DATA_DIR / "BTCUSDT_1m.parquet"
    
    df_5m = pd.read_parquet(path_5m)
    df_5m.index = pd.to_datetime(df_5m.index, utc=True)
    
    df_1m = pd.read_parquet(path_1m)
    df_1m.index = pd.to_datetime(df_1m.index, utc=True)
    
    # Filter period
    start_ts = pd.Timestamp(TEST_START, tz="UTC")
    end_ts = pd.Timestamp(TEST_END, tz="UTC")
    
    mask = (df_5m.index >= start_ts) & (df_5m.index < end_ts)
    df_5m = df_5m.loc[mask].copy()
    
    mask = (df_1m.index >= start_ts) & (df_1m.index < end_ts)
    df_1m = df_1m.loc[mask].copy()
    
    print(f"5m: {len(df_5m):,} bars, 1m: {len(df_1m):,} bars")
    
    # Detect regimes (faster with smaller data)
    print("Detecting regimes...")
    detector = RegimeDetector(df_5m, method="combined", timeframe="15m")
    regimes = detector.get_regimes()
    
    regime_counts = regimes.value_counts()
    print("\nRegime distribution:")
    for r in regime_counts.index:
        pct = regime_counts[r] / len(regimes) * 100
        print(f"  {r.value:12}: {regime_counts[r]:,} ({pct:.1f}%)")
    
    levels = compute_reference_levels(df_5m)
    results = []
    
    # ===================================================================
    # MATRIX CONFIG: RANGING + sd=3.0 + mean_reversion (THE WINNER)
    # From vwap_regime_entry_matrix.py: 4578 trades, 63.4% WR, +240.66% Net
    # ===================================================================
    print("\n" + "=" * 70)
    print("TEST 1: MATRIX EXACT - RANGING + sd=3.0 + mean_reversion")
    print("=" * 70)
    
    # EXACT params from matrix PARAMS dict
    params1 = {
        "sd_threshold": 3.0,        # Matrix: 3.0 (not 0.5!)
        "atr_stop": 2.0,            # Matrix: 2.0 (not 3.0!)
        "atr_target": 2.0,
        "trailing_atr": 1.5,        # Matrix: 1.5
        "ema_fast": 9,
        "ema_slow": 20,
        "use_trend_filter": False,
        "use_volume_filter": True,
        "volume_mult": 1.0,         # Matrix: 1.0 (not 2.0!)
        "use_trailing_stop": True,
        "adx_max": 30.0,
        "rsi_max": 55.0,
        "entry_mode": "mean_reversion",  # Matrix: mean_reversion (NOT cross!)
        "pullback_bars": 3,
        "use_htf_vwap": False,
        "use_htf_ema": False,
        "use_anchored_vwap": True,
        "use_session_filter": False,
        "require_reversal_candle": False,
        "tp_mode": "atr",
        "use_fvg_filter": False,
        "use_ob_filter": False,
        "use_cvd_filter": False,
        "levels": levels,
        "regime_series": regimes,
        "allowed_regimes": [MarketRegime.RANGING],
    }
    
    eq1, trades1 = run_vwap_scalping(df_5m, CUTOFF, df_exit=df_1m, **params1)
    
    if trades1:
        wins1 = sum(1 for t in trades1 if t > 0)
        wr1 = wins1 / len(trades1) * 100
        gp1 = sum(t for t in trades1 if t > 0)
        gl1 = abs(sum(t for t in trades1 if t < 0))
        pf1 = gp1 / gl1 if gl1 > 0 else 999.99
        costs1 = len(trades1) * COST_PER_SIDE
        m1 = compute_metrics(eq1, trades1, bars_per_year=105120)
        net1 = m1["total_return_pct"] - costs1
    else:
        wr1, gp1, gl1, pf1, net1, m1 = 0, 0, 0, 0, 0, {"sharpe": 0, "max_dd_pct": 0}
    
    print(f"  Trades: {len(trades1)}, WR: {wr1:.1f}%, Net: {net1:+.2f}%, PF: {pf1:.2f}")
    print(f"  [Matrix had: 4578 trades, 63.4% WR, +240.66% Net]")
    results.append(("RANGING+sd3.0+mean_rev", len(trades1), wr1, net1, pf1, m1.get("sharpe", 0), m1.get("max_dd_pct", 0)))
    
    # ===================================================================
    # USER'S CLAIMED WINNING CONFIG: sd=0.5 + cross
    # ===================================================================
    print("\n" + "=" * 70)
    print("TEST 2: USER CLAIM - RANGING + sd=0.5 + cross")
    print("=" * 70)
    
    params2 = {
        "sd_threshold": 0.5,
        "atr_stop": 3.0,
        "atr_target": 6.0,
        "trailing_atr": 1.2,
        "ema_fast": 9,
        "ema_slow": 20,
        "use_trend_filter": False,
        "use_volume_filter": True,
        "volume_mult": 2.0,
        "use_trailing_stop": True,
        "adx_max": 30.0,
        "rsi_max": 50.0,
        "entry_mode": "cross",
        "pullback_bars": 3,
        "use_htf_vwap": False,
        "use_htf_ema": False,
        "use_anchored_vwap": True,
        "use_session_filter": True,
        "require_reversal_candle": True,
        "tp_mode": "atr",
        "use_fvg_filter": False,
        "use_ob_filter": False,
        "use_cvd_filter": False,
        "levels": levels,
        "regime_series": regimes,
        "allowed_regimes": [MarketRegime.RANGING],
    }
    
    eq2, trades2 = run_vwap_scalping(df_5m, CUTOFF, df_exit=df_1m, **params2)
    
    if trades2:
        wins2 = sum(1 for t in trades2 if t > 0)
        wr2 = wins2 / len(trades2) * 100
        gp2 = sum(t for t in trades2 if t > 0)
        gl2 = abs(sum(t for t in trades2 if t < 0))
        pf2 = gp2 / gl2 if gl2 > 0 else 999.99
        costs2 = len(trades2) * COST_PER_SIDE
        m2 = compute_metrics(eq2, trades2, bars_per_year=105120)
        net2 = m2["total_return_pct"] - costs2
    else:
        wr2, gp2, gl2, pf2, net2, m2 = 0, 0, 0, 0, 0, {"sharpe": 0, "max_dd_pct": 0}
    
    print(f"  Trades: {len(trades2)}, WR: {wr2:.1f}%, Net: {net2:+.2f}%, PF: {pf2:.2f}")
    results.append(("RANGING+sd0.5+cross", len(trades2), wr2, net2, pf2, m2.get("sharpe", 0), m2.get("max_dd_pct", 0)))
    
    # ===================================================================
    # TEST 3: BEAR_WEAK + sd=3.0 + bull_pullback (from matrix)
    # Matrix: 2706 trades, 56.4% WR, +40.87% Net
    # ===================================================================
    print("\n" + "=" * 70)
    print("TEST 3: BEAR_WEAK + sd=3.0 + bull_pullback")
    print("=" * 70)
    
    params3 = {
        "sd_threshold": 3.0,
        "atr_stop": 2.0,
        "atr_target": 2.0,
        "trailing_atr": 1.5,
        "ema_fast": 9,
        "ema_slow": 20,
        "use_trend_filter": False,
        "use_volume_filter": True,
        "volume_mult": 1.0,
        "use_trailing_stop": True,
        "adx_max": 30.0,
        "rsi_max": 55.0,
        "entry_mode": "bull_pullback",
        "pullback_bars": 3,
        "use_htf_vwap": False,
        "use_htf_ema": False,
        "use_anchored_vwap": True,
        "use_session_filter": False,
        "require_reversal_candle": False,
        "tp_mode": "atr",
        "use_fvg_filter": False,
        "use_ob_filter": False,
        "use_cvd_filter": False,
        "levels": levels,
        "regime_series": regimes,
        "allowed_regimes": [MarketRegime.BEAR_WEAK],
    }
    
    eq3, trades3 = run_vwap_scalping(df_5m, CUTOFF, df_exit=df_1m, **params3)
    
    if trades3:
        wins3 = sum(1 for t in trades3 if t > 0)
        wr3 = wins3 / len(trades3) * 100
        gp3 = sum(t for t in trades3 if t > 0)
        gl3 = abs(sum(t for t in trades3 if t < 0))
        pf3 = gp3 / gl3 if gl3 > 0 else 999.99
        costs3 = len(trades3) * COST_PER_SIDE
        m3 = compute_metrics(eq3, trades3, bars_per_year=105120)
        net3 = m3["total_return_pct"] - costs3
    else:
        wr3, gp3, gl3, pf3, net3, m3 = 0, 0, 0, 0, 0, {"sharpe": 0, "max_dd_pct": 0}
    
    print(f"  Trades: {len(trades3)}, WR: {wr3:.1f}%, Net: {net3:+.2f}%, PF: {pf3:.2f}")
    print(f"  [Matrix had: 2706 trades, 56.4% WR, +40.87% Net]")
    results.append(("BEAR_WEAK+bull_pullback", len(trades3), wr3, net3, pf3, m3.get("sharpe", 0), m3.get("max_dd_pct", 0)))
    
    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"{'Config':<28} {'Trades':>6} {'WR%':>6} {'Net%':>10} {'PF':>6}")
    print("-" * 60)
    for name, trades, wr, net, pf, sharpe, dd in results:
        print(f"{name:<28} {trades:>6} {wr:>5.1f}% {net:>+9.2f}% {pf:>5.2f}")
    
    # CSV
    import csv
    csv_path = RESULTS_DIR / "vwap_ranging_matrix.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Config", "Trades", "WR%", "Net%", "PF", "Sharpe", "MaxDD%"])
        for name, trades, wr, net, pf, sharpe, dd in results:
            writer.writerow([name, trades, f"{wr:.1f}", f"{net:.2f}", f"{pf:.2f}", f"{sharpe:.2f}", f"{dd:.2f}"])
    
    print(f"\nCSV: {csv_path}")
    
    # Key findings
    print("\n" + "=" * 70)
    print("KEY FINDINGS")
    print("=" * 70)
    
    # Compare with matrix
    print("\nMatrix Comparison:")
    print("  RANGING+mean_rev: Our test got {:.1f}%, Matrix had +240.66%".format(results[0][3]))
    print("  BEAR_WEAK+bull_pullback: Our test got {:.1f}%, Matrix had +40.87%".format(results[2][3]))
    
    if abs(results[0][3] - 240) < 50:
        print("\n[OK] RANGING+mean_rev matches matrix!")
    elif results[0][3] < 0:
        print("\n[ISSUE] RANGING+mean_rev shows negative return instead of +240%")
        print("   Possible causes:")
        print("   - Data period difference (2020-2024 vs 2020-2025)")
        print("   - Regime detection differences")
        print("   - Implementation differences")


if __name__ == "__main__":
    main()