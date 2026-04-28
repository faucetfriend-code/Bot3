#!/usr/bin/env python3
"""
VWAP Scalping - MINIMAL Parameter Sweep
=======================================
Very quick test with reduced parameters
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
import json
from datetime import datetime
from itertools import product
import time

sys.path.insert(0, str(Path(__file__).parent.parent))
from strategies import run_vwap_scalping, compute_metrics


def load_parquet(symbol, interval):
    path = Path("G:/Candle Data") / f"{symbol}_{interval}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return None


def run_test(params, df_5m):
    equity, trades = run_vwap_scalping(df_5m, cutoff=0.08, **params)
    
    if not trades:
        return {'params': params, 'trades': 0, 'win_rate': 0, 'net_return': 0, 'profit_factor': 0, 'sharpe': 0, 'max_dd': 0}
    
    bars_py = 105120
    metrics = compute_metrics(equity, trades, bars_per_year=bars_py)
    costs = len(trades) * 0.30
    net_return = metrics['total_return_pct'] - costs
    
    return {
        'params': params,
        'trades': metrics['n_trades'],
        'win_rate': metrics['win_rate_pct'],
        'net_return': net_return,
        'profit_factor': metrics['profit_factor'],
        'sharpe': metrics['sharpe'],
        'max_dd': metrics['max_dd_pct']
    }


# ============================================================================
# LOAD DATA - 2 years for faster testing
# ============================================================================
print("=" * 80)
print("VWAP SCALPING - MINIMAL PARAMETER SWEEP")
print("=" * 80)
print("Loading data (2022-2024)...")

df_5m = load_parquet("BTCUSDT", "5m")
df_5m = df_5m[(df_5m.index >= '2022-01-01') & (df_5m.index < '2024-01-01')]
print(f"5m data: {len(df_5m)} bars")

global_start = time.time()

# ============================================================================
# STAGE 1: SD + Entry Mode (minimal)
# ============================================================================
SD_THRESHOLD = [1.0, 2.0, 3.0, 4.0, 5.0]
ENTRY_MODES = ['bull_pullback', 'bear_pullback', 'mean_reversion', 'cross', 'momentum', 'deviation']

print("\n" + "=" * 80)
print("STAGE 1: SD + Entry Mode")
print("=" * 80)

stage1_results = []

for sd in SD_THRESHOLD:
    print(f"Testing SD = {sd}...")
    
    for entry_mode in ENTRY_MODES:
        params = {
            'sd_threshold': sd,
            'entry_mode': entry_mode,
            'stoch_oversold': 20,
            'stoch_overbought': 80,
            'adx_max': 25,
            'rsi_max': 50,
            'volume_mult': 2.0,
            'atr_stop': 1.0,
            'atr_target': 4.0,
            'pullback_bars': 3,
            'use_htf_vwap': True,
            'use_htf_ema': True,
            'htf_adx_max': 25,
            'df_exit': df_5m
        }
        
        result = run_test(params, df_5m)
        stage1_results.append(result)
        print(f"  {entry_mode}: trades={result['trades']}, WR={result['win_rate']:.1f}%, Net={result['net_return']:.1f}%")

stage1_sorted = sorted(stage1_results, key=lambda x: x['net_return'], reverse=True)
print(f"\n\nSTAGE 1 COMPLETE: {len(stage1_results)} tests")

# ============================================================================
# STAGE 2: Add Stoch + ADX + RSI with top 10
# ============================================================================
STOCH_OVERSOLD = [15, 20, 25]
STOCH_OVERBOUGHT = [75, 80, 85]
ADX_MAX = [20, 25, 30]
RSI_MAX = [40, 50, 60]

top_for_stage2 = stage1_sorted[:10]
print(f"\nSelected {len(top_for_stage2)} configs for Stage 2")

print("\n" + "=" * 80)
print("STAGE 2: Stochastic + ADX + RSI")
print("=" * 80)

stage2_results = []

for i, stage1_result in enumerate(top_for_stage2):
    p1 = stage1_result['params']
    print(f"\n[{i+1}/{len(top_for_stage2)}] SD={p1['sd_threshold']}, {p1['entry_mode']}")
    
    for stoch_oversold in STOCH_OVERSOLD:
        for stoch_overbought in STOCH_OVERBOUGHT:
            for adx_max in ADX_MAX:
                for rsi_max in RSI_MAX:
                    params = {
                        'sd_threshold': p1['sd_threshold'],
                        'entry_mode': p1['entry_mode'],
                        'stoch_oversold': stoch_oversold,
                        'stoch_overbought': stoch_overbought,
                        'adx_max': adx_max,
                        'rsi_max': rsi_max,
                        'volume_mult': 2.0,
                        'atr_stop': 1.0,
                        'atr_target': 4.0,
                        'pullback_bars': 3,
                        'use_htf_vwap': True,
                        'use_htf_ema': True,
                        'htf_adx_max': 25,
                        'df_exit': df_5m
                    }
                    result = run_test(params, df_5m)
                    stage2_results.append(result)

print(f"\nStage 2 complete: {len(stage2_results)} tests")

stage2_sorted = sorted(stage2_results, key=lambda x: x['net_return'], reverse=True)
print("\nSTAGE 2 TOP 10:")
for i, r in enumerate(stage2_sorted[:10], 1):
    p = r['params']
    print(f"{i}. SD={p['sd_threshold']}, {p['entry_mode']}, stoch=({p['stoch_oversold']},{p['stoch_overbought']}), adx={p['adx_max']}, rsi={p['rsi_max']}, Net={r['net_return']:.1f}%")

# ============================================================================
# STAGE 3: Volume + ATR
# ============================================================================
VOLUME_MULT = [1.5, 2.0, 2.5]
ATR_STOP = [0.7, 1.0, 1.5]
ATR_TARGET = [3.0, 4.0, 5.0]

top_configs_stage3 = stage2_sorted[:10]
print(f"\nSelected {len(top_configs_stage3)} for Stage 3")

print("\n" + "=" * 80)
print("STAGE 3: Volume + ATR")
print("=" * 80)

stage3_results = []

for i, stage2_result in enumerate(top_configs_stage3):
    p2 = stage2_result['params']
    print(f"[{i+1}/{len(top_configs_stage3)}] SD={p2['sd_threshold']}, {p2['entry_mode']}")
    
    for volume_mult in VOLUME_MULT:
        for atr_stop in ATR_STOP:
            for atr_target in ATR_TARGET:
                params = {
                    'sd_threshold': p2['sd_threshold'],
                    'entry_mode': p2['entry_mode'],
                    'stoch_oversold': p2['stoch_oversold'],
                    'stoch_overbought': p2['stoch_overbought'],
                    'adx_max': p2['adx_max'],
                    'rsi_max': p2['rsi_max'],
                    'volume_mult': volume_mult,
                    'atr_stop': atr_stop,
                    'atr_target': atr_target,
                    'pullback_bars': 3,
                    'use_htf_vwap': True,
                    'use_htf_ema': True,
                    'htf_adx_max': 25,
                    'df_exit': df_5m
                }
                result = run_test(params, df_5m)
                stage3_results.append(result)

print(f"\nStage 3 complete: {len(stage3_results)} tests")

# ============================================================================
# FINAL RESULTS
# ============================================================================
stage3_sorted = sorted(stage3_results, key=lambda x: x['net_return'], reverse=True)

qualifying = [
    r for r in stage3_sorted 
    if r['win_rate'] > 48 and r['net_return'] > 0 and r['trades'] >= 10
]

print(f"\n\n{'='*80}")
print("FINAL RESULTS")
print(f"{'='*80}")
print(f"Total tests: {len(stage3_results)}")
print(f"Qualifying (WR>48%, Net>0%, Trades>=10): {len(qualifying)}")

final_top = sorted(qualifying, key=lambda x: x['net_return'], reverse=True)[:20] if qualifying else stage3_sorted[:20]

print("\n" + "=" * 100)
print("TOP 20 BEST CONFIGURATIONS")
print("=" * 100)
print(f"{'#':>3} {'SD':>4} {'Entry Mode':<16} {'Stoch':<10} {'ADX':>4} {'RSI':>5} {'Vol':>5} {'ATR-S':>6} {'ATR-T':>6} {'Trds':>5} {'WR%':>5} {'Net%':>7}")
print("-" * 100)

for i, r in enumerate(final_top, 1):
    p = r['params']
    print(f"{i:>3} {p['sd_threshold']:>4.1f} {p['entry_mode']:<16} ({p['stoch_oversold']},{p['stoch_overbought']:<5}) {p['adx_max']:>4} {p['rsi_max']:>5} {p['volume_mult']:>5.1f} {p['atr_stop']:>6.1f} {p['atr_target']:>6.1f} {r['trades']:>5} {r['win_rate']:>5.1f} {r['net_return']:>+7.1f}")

# SD ANALYSIS
print("\n" + "=" * 80)
print("SD THRESHOLD ANALYSIS")
print("=" * 80)

sd_stats = {}
for r in stage3_results:
    sd = r['params']['sd_threshold']
    if sd not in sd_stats:
        sd_stats[sd] = {'results': [], 'qualifying': 0}
    sd_stats[sd]['results'].append(r)
    if r['win_rate'] > 48 and r['net_return'] > 0 and r['trades'] >= 10:
        sd_stats[sd]['qualifying'] += 1

print(f"{'SD':>6} {'Tests':>8} {'Qualifying':>12} {'Best Net%':>12}")
for sd in sorted(sd_stats.keys()):
    best = max(sd_stats[sd]['results'], key=lambda x: x['net_return'])
    print(f"{sd:>6.1f} {len(sd_stats[sd]['results']):>8} {sd_stats[sd]['qualifying']:>12} {best['net_return']:>+12.1f}")

# DETAILED TOP 10
print("\n" + "=" * 80)
print("DETAILED TOP 10")
print("=" * 80)

for i, r in enumerate(final_top[:10], 1):
    p = r['params']
    print(f"\n### Rank #{i}")
    print(f"sd_threshold: {p['sd_threshold']}")
    print(f"entry_mode: {p['entry_mode']}")
    print(f"stoch_oversold: {p['stoch_oversold']}")
    print(f"stoch_overbought: {p['stoch_overbought']}")
    print(f"adx_max: {p['adx_max']}")
    print(f"rsi_max: {p['rsi_max']}")
    print(f"volume_mult: {p['volume_mult']}")
    print(f"atr_stop: {p['atr_stop']}")
    print(f"atr_target: {p['atr_target']}")
    print(f"trades: {r['trades']}, win_rate: {r['win_rate']:.1f}%, profit_factor: {r['profit_factor']:.2f}, net_return: {r['net_return']:.1f}%, sharpe: {r['sharpe']:.2f}, max_dd: {r['max_dd']:.1f}%")

# SAVE RESULTS
total_time = time.time() - global_start

results_output = {
    "test_date": datetime.now().isoformat(),
    "test_period": "2022-01-01 to 2024-01-01",
    "symbol": "BTCUSDT",
    "timeframe": "5m",
    "total_tests": len(stage3_results),
    "qualifying_count": len(qualifying),
    "runtime_minutes": total_time / 60,
    "criteria": {"win_rate_min": 48, "net_return_min": 0, "trades_min": 10},
    "top_20": [{'params': r['params'], 'trades': r['trades'], 'win_rate': r['win_rate'], 'profit_factor': r['profit_factor'], 'net_return': r['net_return'], 'sharpe': r['sharpe'], 'max_dd': r['max_dd']} for r in final_top[:20]],
    "all_qualifying": [{'params': r['params'], 'trades': r['trades'], 'win_rate': r['win_rate'], 'profit_factor': r['profit_factor'], 'net_return': r['net_return'], 'sharpe': r['sharpe'], 'max_dd': r['max_dd']} for r in qualifying],
}

output_path = Path("BTV2/results/vwap_minimal_sweep.json")
output_path.parent.mkdir(parents=True, exist_ok=True)
with open(output_path, 'w') as f:
    json.dump(results_output, f, indent=2)

print(f"\n{'='*80}")
print("SWEEP COMPLETE")
print(f"{'='*80}")
print(f"Runtime: {total_time/60:.1f} minutes")
print(f"Results saved to: {output_path}")
