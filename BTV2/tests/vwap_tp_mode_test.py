#!/usr/bin/env python3
"""
Test: Use VWAP-based take profit instead of ATR-based
The original logic: mean reversion targets are the VWAP itself (the mean)
So TP should be the session VWAP, not an ATR multiple.
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    run_vwap_scalping,
    compute_reference_levels,
    compute_metrics,
    INTERVAL_BARS_PER_YEAR,
)

STORAGE_ROOT = Path("G:/Candle Data")
COST_PER_SIDE = 0.0015


def load_data():
    df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
    return df


def load_exit_data():
    df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_1m.parquet")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
    return df


def run_test(params: dict, df_5m, levels, df_exit):
    equity, trades = run_vwap_scalping(df_5m, cutoff=0.10, **params)
    
    bars_per_year = INTERVAL_BARS_PER_YEAR["5m"]
    metrics = compute_metrics(equity, trades, bars_per_year=bars_per_year)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        costs = len(trades) * COST_PER_SIDE * 2
        net_return = metrics['total_return_pct'] - costs
    else:
        win_rate, profit_factor, costs, net_return = 0, 0, 0, 0
    
    return {
        'total_return': round(metrics['total_return_pct'], 2),
        'net_return': round(net_return, 2),
        'win_rate': round(win_rate, 1),
        'profit_factor': round(profit_factor, 2) if profit_factor != float('inf') else 999.99,
        'trades': len(trades),
    }


def main():
    df_5m = load_data()
    df_exit = load_exit_data()
    levels = compute_reference_levels(df_5m)
    
    # Base params
    base_params = {
        "sd_threshold": 2.5,
        "atr_stop": 0.5,
        "atr_target": 1.0,  # Not used for vwap mode
        "rsi_max": 60,
        "require_reversal_candle": False,
        "entry_mode": "mean_reversion",
        "use_anchored_vwap": True,
        "use_session_filter": True,
        "adx_max": 25.0,
        "volume_mult": 1.0,
        "use_htf_vwap": True,
        "levels": levels,
        "df_exit": df_exit,
    }
    
    print("="*80)
    print("VWAP-Based Take Profit Test")
    print("="*80)
    
    # Test 1: ATR mode (baseline)
    print("\n[Test 1] tp_mode='atr' (baseline)")
    p1 = base_params.copy()
    p1["tp_mode"] = "atr"
    p1["atr_target"] = 1.0
    result1 = run_test(p1, df_5m, levels, df_exit)
    print(f"  Trades: {result1['trades']}, WR: {result1['win_rate']:.1f}%, PF: {result1['profit_factor']:.2f}, Net: {result1['net_return']:+.1f}%")
    
    # Test 2: VWAP mode (the real mean reversion target)
    print("\n[Test 2] tp_mode='vwap' (session VWAP target)")
    p2 = base_params.copy()
    p2["tp_mode"] = "vwap"
    result2 = run_test(p2, df_5m, levels, df_exit)
    print(f"  Trades: {result2['trades']}, WR: {result2['win_rate']:.1f}%, PF: {result2['profit_factor']:.2f}, Net: {result2['net_return']:+.1f}%")
    
    # Test 3: Higher stop, vwap target
    print("\n[Test 3] tp_mode='vwap', atr_stop=1.0")
    p3 = base_params.copy()
    p3["tp_mode"] = "vwap"
    p3["atr_stop"] = 1.0
    result3 = run_test(p3, df_5m, levels, df_exit)
    print(f"  Trades: {result3['trades']}, WR: {result3['win_rate']:.1f}%, PF: {result3['profit_factor']:.2f}, Net: {result3['net_return']:+.1f}%")
    
    # Test 4: Bull pullback mode with vwap target (better follow-through)
    print("\n[Test 4] entry_mode='bull_pullback', tp_mode='vwap'")
    p4 = base_params.copy()
    p4["tp_mode"] = "vwap"
    p4["entry_mode"] = "bull_pullback"
    p4["use_htf_ema"] = True  # Required for pullback
    result4 = run_test(p4, df_5m, levels, df_exit)
    print(f"  Trades: {result4['trades']}, WR: {result4['win_rate']:.1f}%, PF: {result4['profit_factor']:.2f}, Net: {result4['net_return']:+.1f}%")
    
    # Test 5: Cross mode with vwap target
    print("\n[Test 5] entry_mode='cross', tp_mode='vwap'")
    p5 = base_params.copy()
    p5["tp_mode"] = "vwap"
    p5["entry_mode"] = "cross"
    result5 = run_test(p5, df_5m, levels, df_exit)
    print(f"  Trades: {result5['trades']}, WR: {result5['win_rate']:.1f}%, PF: {result5['profit_factor']:.2f}, Net: {result5['net_return']:+.1f}%")
    
    # Test 6: Momentum mode with vwap target
    print("\n[Test 6] entry_mode='momentum', tp_mode='vwap'")
    p6 = base_params.copy()
    p6["tp_mode"] = "vwap"
    p6["entry_mode"] = "momentum"
    result6 = run_test(p6, df_5m, levels, df_exit)
    print(f"  Trades: {result6['trades']}, WR: {result6['win_rate']:.1f}%, PF: {result6['profit_factor']:.2f}, Net: {result6['net_return']:+.1f}%")
    
    # Test 7: SD 2.0 with vwap target
    print("\n[Test 7] sd_threshold=2.0, tp_mode='vwap'")
    p7 = base_params.copy()
    p7["tp_mode"] = "vwap"
    p7["sd_threshold"] = 2.0
    result7 = run_test(p7, df_5m, levels, df_exit)
    print(f"  Trades: {result7['trades']}, WR: {result7['win_rate']:.1f}%, PF: {result7['profit_factor']:.2f}, Net: {result7['net_return']:+.1f}%")
    
    # Test 8: SD 1.5 with vwap target
    print("\n[Test 8] sd_threshold=1.5, tp_mode='vwap'")
    p8 = base_params.copy()
    p8["tp_mode"] = "vwap"
    p8["sd_threshold"] = 1.5
    p8["volume_mult"] = 1.5
    result8 = run_test(p8, df_5m, levels, df_exit)
    print(f"  Trades: {result8['trades']}, WR: {result8['win_rate']:.1f}%, PF: {result8['profit_factor']:.2f}, Net: {result8['net_return']:+.1f}%")
    
    print("\n" + "="*80)
    print("CONCLUSION")
    print("="*80)


if __name__ == "__main__":
    main()