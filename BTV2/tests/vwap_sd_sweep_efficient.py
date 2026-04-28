#!/usr/bin/env python3
"""
VWAP Scalping - Efficient SD-Focused Parameter Sweep
====================================================
Focus on finding optimal SD threshold (0.5-6.0) with reduced parameter space.
Uses smart multi-stage approach for faster execution.

Test Data: BTCUSDT 5m for 2018-2024
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
import json
from datetime import datetime
from itertools import product

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR
)

STORAGE_ROOT = Path("G:/Candle Data")


def load_parquet(symbol, interval):
    """Load parquet data file."""
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        return df
    return None


def run_test(params, df_5m, df_1m):
    """Run a single parameter configuration and return metrics."""
    equity, trades = run_vwap_scalping(df_5m, cutoff=0.10, **params)
    bars_per_year = INTERVAL_BARS_PER_YEAR["5m"]
    metrics = compute_metrics(equity, trades, bars_per_year=bars_per_year)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        costs = len(trades) * 0.30
        net_return = metrics['total_return_pct'] - costs
    else:
        win_rate, profit_factor, costs, net_return = 0, 0, 0, 0
    
    return {
        'params': params.copy(),
        'total_return': metrics['total_return_pct'],
        'net_return': net_return,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'trades': len(trades),
        'sharpe': metrics['sharpe'],
        'max_dd': metrics['max_dd_pct']
    }


# ============================================================================
# PARAMETER RANGES - Reduced for speed but covers SD thoroughly
# ============================================================================

# CRITICAL: SD Range from 0.5 to 6.0
SD_THRESHOLD = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]

# Entry Modes (reduced)
ENTRY_MODES = ['bull_pullback', 'mean_reversion', 'cross', 'momentum']

# Stochastic (reduced)
STOCH_OVERSOLD = [15, 20, 25]
STOCH_OVERBOUGHT = [75, 80, 85]

# Filters (reduced)
ADX_MAX = [20, 25, 30, 35]
RSI_MAX = [40, 50, 55]
VOLUME_MULT = [1.5, 2.0, 2.5]

# ATR (reduced)
ATR_STOP = [0.7, 1.0, 1.5]
ATR_TARGET = [3.0, 4.0, 5.0]

# Pullback
PULLBACK_BARS = [3, 4]

# HTF (reduced)
USE_HTF_VWAP = [True, False]
USE_HTF_EMA = [True, False]
HTF_ADX_MAX = [25, 30]


# ============================================================================
# LOAD DATA
# ============================================================================

print("=" * 80)
print("VWAP SCALPING - EFFICIENT SD-FOCUSED PARAMETER SWEEP")
print("=" * 80)
print()

# Load 5m data (2018-2024)
df_5m = load_parquet("BTCUSDT", "5m")
df_5m = df_5m[(df_5m.index >= '2018-01-01') & (df_5m.index < '2025-01-01')]
print(f"5m data: {len(df_5m)} bars ({df_5m.index.min()} to {df_5m.index.max()})")

# Load 1m data for exits
df_1m = load_parquet("BTCUSDT", "1m")
if df_1m is not None:
    df_1m = df_1m[(df_1m.index >= '2018-01-01') & (df_1m.index < '2025-01-01')]
    print(f"1m data: {len(df_1m)} bars for exit resolution")
else:
    df_1m = df_5m
    print("Warning: Using 5m for exits")

base_params = {
    "volume_mult": 2.0,
    "atr_stop": 1.0,
    "atr_target": 4.0,
    "pullback_bars": 3,
    "use_htf_vwap": True,
    "use_htf_ema": True,
    "htf_adx_max": 25,
    "df_exit": df_1m,
}

# ============================================================================
# STAGE 1: Full SD sweep with core parameters
# ============================================================================

print("\n" + "=" * 80)
print("STAGE 1: Full SD sweep with core parameters")
print("=" * 80)

stage1_results = []
total_tests = 0
start_time = datetime.now()

for sd in SD_THRESHOLD:
    print(f"\nTesting SD = {sd}...")
    for entry_mode in ENTRY_MODES:
        for stoch_os in STOCH_OVERSOLD:
            for stoch_ob in STOCH_OVERBOUGHT:
                for adx in ADX_MAX:
                    for rsi in RSI_MAX:
                        params = {
                            **base_params,
                            "sd_threshold": sd,
                            "entry_mode": entry_mode,
                            "stoch_oversold": stoch_os,
                            "stoch_overbought": stoch_ob,
                            "adx_max": adx,
                            "rsi_max": rsi,
                        }
                        result = run_test(params, df_5m, df_1m)
                        stage1_results.append(result)
                        total_tests += 1
    
    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"  SD {sd} complete ({total_tests} tests, {elapsed:.0f}s elapsed)")

print(f"\nStage 1 complete: {len(stage1_results)} tests")

# Stage 1 Results - best per SD
sd_best = {}
for r in stage1_results:
    sd = r['params']['sd_threshold']
    if sd not in sd_best or r['net_return'] > sd_best[sd]['net_return']:
        sd_best[sd] = r

print("\n" + "=" * 80)
print("STAGE 1 RESULTS - BEST PER SD")
print("=" * 80)
print(f"{'SD':>6} {'Entry Mode':<16} {'Stoch':<10} {'ADX':>4} {'RSI':>5} "
      f"{'Trds':>5} {'WR%':>5} {'PF':>5} {'Net%':>7}")
print("-" * 75)

for sd in sorted(sd_best.keys()):
    r = sd_best[sd]
    p = r['params']
    print(f"{sd:>6.1f} {p['entry_mode']:<16} "
          f"({p['stoch_oversold']},{p['stoch_overbought']:<5}) "
          f"{p['adx_max']:>4} {p['rsi_max']:>5} "
          f"{r['trades']:>5} {r['win_rate']:>5.1f} {r['profit_factor']:>5.2f} {r['net_return']:>+7.1f}")

# Select top configs for Stage 2
stage1_sorted = sorted(stage1_results, key=lambda x: x['net_return'], reverse=True)
top_for_stage2 = [r for r in stage1_sorted[:24] if r['trades'] >= 20]
print(f"\nSelected {len(top_for_stage2)} configs for Stage 2")


# ============================================================================
# STAGE 2: Volume + ATR + Pullback
# ============================================================================

print("\n" + "=" * 80)
print("STAGE 2: Volume + ATR + Pullback")
print("=" * 80)

stage2_results = []
stage2_start = datetime.now()

for i, s1 in enumerate(top_for_stage2):
    p1 = s1['params']
    cfg = f"SD={p1['sd_threshold']}, {p1['entry_mode']}, stoch=({p1['stoch_oversold']},{p1['stoch_overbought']}), adx={p1['adx_max']}, rsi={p1['rsi_max']}"
    print(f"[{i+1}/{len(top_for_stage2)}] {cfg}")
    
    for vol in VOLUME_MULT:
        for atr_s in ATR_STOP:
            for atr_t in ATR_TARGET:
                for pb in PULLBACK_BARS:
                    params = {
                        "sd_threshold": p1['sd_threshold'],
                        "entry_mode": p1['entry_mode'],
                        "stoch_oversold": p1['stoch_oversold'],
                        "stoch_overbought": p1['stoch_overbought'],
                        "adx_max": p1['adx_max'],
                        "rsi_max": p1['rsi_max'],
                        "volume_mult": vol,
                        "atr_stop": atr_s,
                        "atr_target": atr_t,
                        "pullback_bars": pb,
                        "use_htf_vwap": True,
                        "use_htf_ema": True,
                        "htf_adx_max": 25,
                        "df_exit": df_1m,
                    }
                    result = run_test(params, df_5m, df_1m)
                    stage2_results.append(result)

print(f"\nStage 2 complete: {len(stage2_results)} tests")

# Stage 2 Results
stage2_sorted = sorted(stage2_results, key=lambda x: x['net_return'], reverse=True)

print("\n" + "=" * 80)
print("STAGE 2 RESULTS - TOP 15")
print("=" * 80)
print(f"{'#':>3} {'SD':>4} {'Entry Mode':<16} {'Vol':>5} {'ATR-S':>6} {'ATR-T':>6} {'PB':>3} "
      f"{'Trds':>5} {'WR%':>5} {'PF':>5} {'Net%':>7}")
print("-" * 80)

for i, r in enumerate(stage2_sorted[:15]):
    p = r['params']
    print(f"{i+1:>3} {p['sd_threshold']:>4.1f} {p['entry_mode']:<16} "
          f"{p['volume_mult']:>5.1f} {p['atr_stop']:>6.1f} {p['atr_target']:>6.1f} {p['pullback_bars']:>3} "
          f"{r['trades']:>5} {r['win_rate']:>5.1f} {r['profit_factor']:>5.2f} {r['net_return']:>+7.1f}")

# Select top for Stage 3
top_stage3 = stage2_sorted[:20]
print(f"\nSelected {len(top_stage3)} configs for Stage 3")


# ============================================================================
# STAGE 3: HTF Filters
# ============================================================================

print("\n" + "=" * 80)
print("STAGE 3: HTF Filters")
print("=" * 80)

stage3_results = []

for i, s2 in enumerate(top_stage3):
    p2 = s2['params']
    cfg = f"SD={p2['sd_threshold']}, {p2['entry_mode']}, vol={p2['volume_mult']}, atr={p2['atr_stop']}/{p2['atr_target']}"
    print(f"[{i+1}/{len(top_stage3)}] {cfg}")
    
    for htf_vwap in USE_HTF_VWAP:
        for htf_ema in USE_HTF_EMA:
            for htf_adx in HTF_ADX_MAX:
                params = {
                    "sd_threshold": p2['sd_threshold'],
                    "entry_mode": p2['entry_mode'],
                    "stoch_oversold": p2['stoch_oversold'],
                    "stoch_overbought": p2['stoch_overbought'],
                    "adx_max": p2['adx_max'],
                    "rsi_max": p2['rsi_max'],
                    "volume_mult": p2['volume_mult'],
                    "atr_stop": p2['atr_stop'],
                    "atr_target": p2['atr_target'],
                    "pullback_bars": p2['pullback_bars'],
                    "use_htf_vwap": htf_vwap,
                    "use_htf_ema": htf_ema,
                    "htf_adx_max": htf_adx,
                    "df_exit": df_1m,
                }
                result = run_test(params, df_5m, df_1m)
                stage3_results.append(result)

print(f"\nStage 3 complete: {len(stage3_results)} tests")


# ============================================================================
# FINAL RESULTS
# ============================================================================

print("\n" + "=" * 80)
print("FINAL RESULTS")
print("=" * 80)

stage3_sorted = sorted(stage3_results, key=lambda x: x['net_return'], reverse=True)

# Qualifying configurations
qualifying = [
    r for r in stage3_sorted 
    if r['win_rate'] > 48 and r['net_return'] > 0 and r['trades'] >= 20
]

print(f"Total tests: {len(stage3_results)}")
print(f"Qualifying (WR>48%, Net>0%, Trades>=20): {len(qualifying)}")

if qualifying:
    final = sorted(qualifying, key=lambda x: x['net_return'], reverse=True)[:20]
else:
    final = stage3_sorted[:20]

print("\n" + "=" * 100)
print("TOP 20 CONFIGURATIONS")
print("=" * 100)
print(f"{'#':>3} {'SD':>4} {'Entry Mode':<14} {'Stoch':<10} {'ADX':>4} {'RSI':>5} "
      f"{'Vol':>4} {'ATR-S':>5} {'ATR-T':>5} {'PB':>3} {'HTF-V':>5} {'HTF-E':>5} {'Trds':>5} {'WR%':>5} {'PF':>5} {'Net%':>7}")
print("-" * 110)

for i, r in enumerate(final, 1):
    p = r['params']
    print(f"{i:>3} {p['sd_threshold']:>4.1f} {p['entry_mode']:<14} "
          f"({p['stoch_oversold']},{p['stoch_overbought']:<5}) "
          f"{p['adx_max']:>4} {p['rsi_max']:>5} "
          f"{p['volume_mult']:>4.1f} {p['atr_stop']:>5.1f} {p['atr_target']:>5.1f} {p['pullback_bars']:>3} "
          f"{str(p['use_htf_vwap']):>5} {str(p['use_htf_ema']):>5} "
          f"{r['trades']:>5} {r['win_rate']:>5.1f} {r['profit_factor']:>5.2f} {r['net_return']:>+7.1f}")

# SD Analysis
print("\n" + "=" * 80)
print("SD THRESHOLD ANALYSIS")
print("=" * 80)

sd_stats = {}
for sd in SD_THRESHOLD:
    sd_results = [r for r in stage3_results if r['params']['sd_threshold'] == sd]
    if sd_results:
        best = max(sd_results, key=lambda x: x['net_return'])
        qual = len([r for r in sd_results if r['win_rate'] > 48 and r['net_return'] > 0 and r['trades'] >= 20])
        sd_stats[sd] = {'best_net': best['net_return'], 'best_wr': best['win_rate'], 'qualifying': qual}

print(f"{'SD':>6} {'Qualifying':>10} {'Best Net%':>12} {'Best WR%':>10}")
print("-" * 45)
for sd in sorted(sd_stats.keys()):
    s = sd_stats[sd]
    print(f"{sd:>6.1f} {s['qualifying']:>10} {s['best_net']:>+12.1f} {s['best_wr']:>10.1f}")

# Detailed output
print("\n" + "=" * 80)
print("DETAILED TOP 10 CONFIGURATIONS")
print("=" * 80)

for i, r in enumerate(final[:10], 1):
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
    print(f"pullback_bars: {p['pullback_bars']}")
    print(f"use_htf_vwap: {p['use_htf_vwap']}")
    print(f"use_htf_ema: {p['use_htf_ema']}")
    print(f"htf_adx_max: {p['htf_adx_max']}")
    print(f"trades: {r['trades']}, win_rate: {r['win_rate']:.1f}%, profit_factor: {r['profit_factor']:.2f}, net_return: {r['net_return']:+.1f}%")

# Save results
results = {
    "test_date": datetime.now().isoformat(),
    "test_period": "2018-01-01 to 2025-01-01",
    "symbol": "BTCUSDT",
    "timeframe": "5m",
    "total_tests": len(stage3_results),
    "qualifying_count": len(qualifying),
    "top_20": [{
        'sd_threshold': r['params']['sd_threshold'],
        'entry_mode': r['params']['entry_mode'],
        'stoch_oversold': r['params']['stoch_oversold'],
        'stoch_overbought': r['params']['stoch_overbought'],
        'adx_max': r['params']['adx_max'],
        'rsi_max': r['params']['rsi_max'],
        'volume_mult': r['params']['volume_mult'],
        'atr_stop': r['params']['atr_stop'],
        'atr_target': r['params']['atr_target'],
        'pullback_bars': r['params']['pullback_bars'],
        'use_htf_vwap': r['params']['use_htf_vwap'],
        'use_htf_ema': r['params']['use_htf_ema'],
        'htf_adx_max': r['params']['htf_adx_max'],
        'trades': r['trades'],
        'win_rate': round(r['win_rate'], 1),
        'profit_factor': round(r['profit_factor'], 2),
        'net_return': round(r['net_return'], 1),
    } for r in final]
}

output_path = Path("BTV2/results/vwap_sd_sweep_results.json")
output_path.parent.mkdir(parents=True, exist_ok=True)
with open(output_path, 'w') as f:
    json.dump(results, f, indent=2)

total_time = (datetime.now() - start_time).total_seconds()
print(f"\n" + "=" * 80)
print(f"SWEEP COMPLETE in {total_time:.0f}s")
print(f"Total tests: {len(stage3_results)}")
print(f"Qualifying: {len(qualifying)}")
print(f"Results saved to: {output_path}")
