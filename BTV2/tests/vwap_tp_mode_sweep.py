#!/usr/bin/env python3
"""
VWAP Scalping TP Mode Comparison - Using Correct BASE PARAMS from vwap_yearly_backtest.py

atr_stop=0.7 (not 2.0!)
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
import csv

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    run_vwap_scalping,
    compute_reference_levels,
    compute_metrics,
    INTERVAL_BARS_PER_YEAR,
)

STORAGE_ROOT = Path("G:/Candle Data")
COST_PER_TRADE = 0.30


def load_data(start: str, end: str):
    df_5m = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
    df_5m.columns = [c.capitalize() for c in df_5m.columns]
    df_5m = df_5m[(df_5m.index >= start) & (df_5m.index < end)]

    df_1m = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_1m.parquet")
    df_1m.columns = [c.capitalize() for c in df_1m.columns]
    df_1m = df_1m[(df_1m.index >= start) & (df_1m.index < end)]

    return df_5m, df_1m


def run_test(params: dict, df_5m, df_1m, levels):
    equity, trades = run_vwap_scalping(
        df_5m, 
        cutoff=0.10, 
        df_exit=df_1m,
        levels=levels,
        **params
    )

    bars_per_year = INTERVAL_BARS_PER_YEAR["5m"]
    metrics = compute_metrics(equity, trades, bars_per_year=bars_per_year)

    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.99
        
        wins_list = [t * 100 for t in trades if t > 0]
        losses_list = [abs(t) * 100 for t in trades if t < 0]
        avg_win = sum(wins_list) / len(wins_list) if wins_list else 0
        avg_loss = sum(losses_list) / len(losses_list) if losses_list else 0
        rr = avg_win / avg_loss if avg_loss > 0 else 0
        
        costs = len(trades) * COST_PER_TRADE
        net_return = metrics['total_return_pct'] - costs
    else:
        win_rate, profit_factor, net_return, rr, avg_win, avg_loss = 0, 0, 0, 0, 0, 0
    
    return {
        'trades': len(trades),
        'win_rate': round(win_rate, 1),
        'profit_factor': round(profit_factor, 2) if profit_factor != 999.99 else 999.99,
        'total_return': round(metrics['total_return_pct'], 2),
        'net_return': round(net_return, 2),
        'sharpe': round(metrics['sharpe'], 2),
        'max_dd': round(metrics['max_dd_pct'], 2),
        'cagr': round(metrics['cagr_pct'], 2),
        'avg_win_pct': round(avg_win, 2),
        'avg_loss_pct': round(avg_loss, 2),
        'rr_ratio': round(rr, 2),
    }


def main():
    print("=" * 80)
    print("VWAP TP MODE COMPARISON - CORRECT PARAMS (atr_stop=0.7)")
    print("Period: 2024-01-01 to 2025-12-31")
    print("=" * 80)
    
    df_5m, df_1m = load_data("2024-01-01", "2025-12-31")
    print(f"\nData: {len(df_5m)} 5m bars, {len(df_1m)} 1m bars")
    
    levels = compute_reference_levels(df_5m)
    
    # CORRECT base params (from vwap_yearly_backtest.py)
    base_params = {
        "sd_threshold": 2.0,
        "entry_mode": "bull_pullback",
        "adx_max": 30,
        "rsi_max": 50,
        "volume_mult": 2.0,
        "atr_stop": 0.7,          # CORRECT: 0.7, not 2.0!
        "atr_target": 3.5,
        "pullback_bars": 3,
        "use_session_filter": True,
        "use_htf_vwap": True,
        "use_htf_ema": False,
        "htf_adx_max": 25,
        "use_trailing_stop": True,
        "trailing_atr": 1.2,
        "use_anchored_vwap": True,
        "require_reversal_candle": True,
    }
    
    results = []
    
    # ─────────────────────────────────────────────────────────────────────────────
    # Test 1: tp_mode="atr" with different atr_target values
    # atr_stop = 0.7 (the base)
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("TEST 1: tp_mode='atr' with various atr_target (atr_stop=0.7)")
    print("=" * 80)
    
    for atr_target in [1.5, 2.0, 2.5, 3.0, 3.5, 4.0]:
        params = base_params.copy()
        params["tp_mode"] = "atr"
        params["atr_target"] = atr_target
        
        print(f"\n  atr_target={atr_target}")
        r = run_test(params, df_5m, df_1m, levels)
        print(f"    Trades: {r['trades']}, WR: {r['win_rate']:.1f}%, PF: {r['profit_factor']:.2f}")
        print(f"    Net%: {r['net_return']:+.2f}%, R:R: {r['rr_ratio']:.2f}")
        
        results.append({
            "tp_mode": "atr",
            "atr_target": atr_target,
            "atr_stop": 0.7,
            **r
        })
    
    # ─────────────────────────────────────────────────────────────────────────────
    # Test 2: tp_mode="vwap" with atr_stop=0.7 (baseline from yearly backtest)
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("TEST 2: tp_mode='vwap' (atr_stop=0.7) - BASELINE")
    print("=" * 80)
    
    params = base_params.copy()
    params["tp_mode"] = "vwap"
    
    print(f"\n  tp_mode='vwap', atr_stop=0.7 (BASELINE)")
    r = run_test(params, df_5m, df_1m, levels)
    print(f"    Trades: {r['trades']}, WR: {r['win_rate']:.1f}%, PF: {r['profit_factor']:.2f}")
    print(f"    Net%: {r['net_return']:+.2f}%, R:R: {r['rr_ratio']:.2f}")
    
    results.append({
        "tp_mode": "vwap",
        "atr_target": "N/A",
        "atr_stop": 0.7,
        **r
    })
    
    # ─────────────────────────────────────────────────────────────────────────────
    # Test 3: tp_mode="vwap" with atr_stop=7.0 (the problematic config you mentioned)
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("TEST 3: tp_mode='vwap' (atr_stop=7.0) - THE PROBLEMATIC ONE")
    print("=" * 80)
    
    params = base_params.copy()
    params["tp_mode"] = "vwap"
    params["atr_stop"] = 7.0
    
    print(f"\n  tp_mode='vwap', atr_stop=7.0 (PROBLEMATIC)")
    r = run_test(params, df_5m, df_1m, levels)
    print(f"    Trades: {r['trades']}, WR: {r['win_rate']:.1f}%, PF: {r['profit_factor']:.2f}")
    print(f"    Net%: {r['net_return']:+.2f}%, R:R: {r['rr_ratio']:.2f}")
    
    results.append({
        "tp_mode": "vwap",
        "atr_target": "N/A",
        "atr_stop": 7.0,
        **r
    })
    
    # ─────────────────────────────────────────────────────────────────────────────
    # Test 4: tp_mode="nearest"
    # ─────────────────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("TEST 4: tp_mode='nearest' (atr_stop=0.7)")
    print("=" * 80)
    
    params = base_params.copy()
    params["tp_mode"] = "nearest"
    
    print(f"\n  tp_mode='nearest', atr_stop=0.7")
    r = run_test(params, df_5m, df_1m, levels)
    print(f"    Trades: {r['trades']}, WR: {r['win_rate']:.1f}%, PF: {r['profit_factor']:.2f}")
    print(f"    Net%: {r['net_return']:+.2f}%, R:R: {r['rr_ratio']:.2f}")
    
    results.append({
        "tp_mode": "nearest",
        "atr_target": 3.5,
        "atr_stop": 0.7,
        **r
    })
    
    # Summary
    print("\n" + "=" * 80)
    print("RESULTS SUMMARY")
    print("=" * 80)
    print(f"{'tp_mode':<10} {'atr_tgt':<8} {'atr_stp':<8} {'Trades':>6} {'WR%':>6} {'PF':>6} {'Net%':>8} {'R:R':>6} {'Sharpe':>7}")
    print("-" * 80)
    
    for r in results:
        pf_str = f"{r['profit_factor']:.2f}" if r['profit_factor'] < 999 else "inf"
        atr_tgt_str = str(r['atr_target']) if isinstance(r['atr_target'], (int, float)) else r['atr_target']
        flag = " **" if r['net_return'] > 0 and r['trades'] >= 10 and r['rr_ratio'] >= 1.0 else ""
        print(f"{r['tp_mode']:<10} {atr_tgt_str:<8} {r['atr_stop']:<8} {r['trades']:>6} {r['win_rate']:>5.1f}% {pf_str:>6} {r['net_return']:>+7.2f}% {r['rr_ratio']:>5.2f}{flag}")
    
    # Save CSV
    csv_path = Path(__file__).parent.parent / "results" / "vwap_tp_mode_comparison.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    
    fieldnames = ["tp_mode", "atr_target", "atr_stop", "trades", "win_rate", 
                  "profit_factor", "total_return", "net_return", "sharpe", "max_dd", "cagr",
                  "avg_win_pct", "avg_loss_pct", "rr_ratio"]
    
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    
    print(f"\nCSV saved: {csv_path}")
    
    # Best analysis
    print("\n" + "=" * 80)
    print("ANALYSIS")
    print("=" * 80)
    
    best_net = max(results, key=lambda x: x['net_return'])
    print(f"\nBest Net%: {best_net['tp_mode']} (atr_target={best_net['atr_target']}, atr_stop={best_net['atr_stop']}) -> {best_net['net_return']:+.2f}%")
    
    profitable = [r for r in results if r['net_return'] > 0 and r['trades'] >= 10]
    if profitable:
        best_rr = max(profitable, key=lambda x: x['rr_ratio'])
        print(f"Best R:R: {best_rr['tp_mode']} (atr_target={best_rr['atr_target']}, atr_stop={best_rr['atr_stop']}) -> R:R={best_rr['rr_ratio']:.2f}, Net%={best_rr['net_return']:+.2f}%")
    
    favorable = [r for r in results if r['rr_ratio'] >= 1.5]
    if favorable:
        print(f"\nFavorable R:R (>=1.5):")
        for r in sorted(favorable, key=lambda x: -x['rr_ratio']):
            print(f"  {r['tp_mode']} atr_target={r['atr_target']} atr_stop={r['atr_stop']}: R:R={r['rr_ratio']:.2f}, Net%={r['net_return']:+.2f}%")
    else:
        print("\nNo configurations with R:R >= 1.5 found")


if __name__ == "__main__":
    main()