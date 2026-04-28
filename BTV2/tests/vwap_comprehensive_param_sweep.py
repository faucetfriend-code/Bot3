#!/usr/bin/env python3
"""
VWAP Scalping - Optimized Comprehensive Parameter Sweep
=======================================================
Focused parameter testing that balances thoroughness with execution time.
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
import json
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR
)

STORAGE_ROOT = Path("G:/Candle Data")


def load_parquet(symbol, interval):
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        return df
    return None


def run_test(params, df_5m, df_1m):
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


def format_result(r):
    p = r['params']
    return {
        'entry_mode': p.get('entry_mode'),
        'stoch_oversold': p.get('stoch_oversold'),
        'stoch_overbought': p.get('stoch_overbought'),
        'adx_max': p.get('adx_max'),
        'rsi_max': p.get('rsi_max'),
        'volume_mult': p.get('volume_mult'),
        'atr_stop': p.get('atr_stop'),
        'atr_target': p.get('atr_target'),
        'pullback_bars': p.get('pullback_bars'),
        'use_htf_vwap': p.get('use_htf_vwap'),
        'use_htf_ema': p.get('use_htf_ema'),
        'htf_adx_max': p.get('htf_adx_max'),
        'trades': r['trades'],
        'win_rate': round(r['win_rate'], 1),
        'profit_factor': round(r['profit_factor'], 2),
        'net_return': round(r['net_return'], 1)
    }


# Load data
print("Loading data...")
df_5m = load_parquet("BTCUSDT", "5m")
df_5m = df_5m[(df_5m.index >= '2023-01-01') & (df_5m.index < '2025-01-01')]
print(f"5m: {len(df_5m)} bars")

df_1m = load_parquet("BTCUSDT", "1m")
df_1m = df_1m[(df_1m.index >= '2023-01-01') & (df_1m.index < '2025-01-01')]
print(f"1m: {len(df_1m)} bars for exit")
print()

SD_THRESHOLD = 2.5

# Reduced parameter sets for faster execution
ENTRY_MODES = ['bull_pullback', 'bear_pullback', 'mean_reversion', 'cross']
# Focus on most impactful stochastic values
STOCH_OVERSOLD = [10, 20, 30]
STOCH_OVERBOUGHT = [70, 80, 90]
# Focus on key ADX/RSI thresholds
ADX_MAX = [16, 20, 30, 40]
RSI_MAX = [35, 45, 55]
VOLUME_MULT = [1.0, 2.0, 2.5]
ATR_STOP = [0.5, 1.0, 1.5]
ATR_TARGET = [2.0, 4.0, 5.0]
PULLBACK_BARS = [2, 4]
USE_HTF_VWAP = [True, False]
USE_HTF_EMA = [True, False]
HTF_ADX_MAX = [25, 35, 40]

# Calculate combos: 4 × 3 × 3 × 4 × 3 × 3 × 3 × 3 × 2 × 2 × 3 = 62,208 (still too many)
# Let's do staged approach with fewer combos per stage

print(f"Running optimized multi-stage sweep...")
print()

# STAGE 1: Entry Mode + Stochastic + ADX + RSI (focused)
print("=" * 80)
print("STAGE 1: Entry Mode + Stochastic + ADX/RSI")
print("=" * 80)

stage1_results = []
base_params = {
    "sd_threshold": SD_THRESHOLD,
    "volume_mult": 2.0,
    "atr_stop": 0.7,
    "atr_target": 3.8,
    "pullback_bars": 3,
    "use_htf_vwap": True,
    "use_htf_ema": True,
    "htf_adx_max": 25,
    "df_exit": df_1m,
}

count = 0
for entry_mode in ENTRY_MODES:
    print(f"  Entry mode: {entry_mode}...")
    for stoch_oversold in STOCH_OVERSOLD:
        for stoch_overbought in STOCH_OVERBOUGHT:
            for adx_max in ADX_MAX:
                for rsi_max in RSI_MAX:
                    params = {
                        **base_params,
                        "entry_mode": entry_mode,
                        "stoch_oversold": stoch_oversold,
                        "stoch_overbought": stoch_overbought,
                        "adx_max": adx_max,
                        "rsi_max": rsi_max,
                    }
                    result = run_test(params, df_5m, df_1m)
                    stage1_results.append(result)
                    count += 1

print(f"  Completed {count} tests")
print()

stage1_sorted = sorted(stage1_results, key=lambda x: x['net_return'], reverse=True)

print("TOP 15 Stage 1:")
for i, r in enumerate(stage1_sorted[:15]):
    p = r['params']
    print(f"  {i+1:2}. mode={p['entry_mode']:16} stoch=({p['stoch_oversold']},{p['stoch_overbought']}) "
          f"adx={p['adx_max']:2} rsi={p['rsi_max']:2} "
          f"net={r['net_return']:+7.1f}% wr={r['win_rate']:5.1f}% pf={r['profit_factor']:4.2f} trades={r['trades']:3}")

top_stage1 = stage1_sorted[:8]
print()

# STAGE 2: Volume + ATR (using top 8 from stage 1)
print("=" * 80)
print("STAGE 2: Volume + ATR + Pullback")
print("=" * 80)

stage2_results = []

for stage1_result in top_stage1:
    p1 = stage1_result['params']
    print(f"  Testing with {p1['entry_mode']}, stoch=({p1['stoch_oversold']},{p1['stoch_overbought']}), adx={p1['adx_max']}, rsi={p1['rsi_max']}...")
    
    for volume_mult in VOLUME_MULT:
        for atr_stop in ATR_STOP:
            for atr_target in ATR_TARGET:
                for pullback_bars in PULLBACK_BARS:
                    params = {
                        "sd_threshold": SD_THRESHOLD,
                        "entry_mode": p1['entry_mode'],
                        "stoch_oversold": p1['stoch_oversold'],
                        "stoch_overbought": p1['stoch_overbought'],
                        "adx_max": p1['adx_max'],
                        "rsi_max": p1['rsi_max'],
                        "volume_mult": volume_mult,
                        "atr_stop": atr_stop,
                        "atr_target": atr_target,
                        "pullback_bars": pullback_bars,
                        "use_htf_vwap": True,
                        "use_htf_ema": True,
                        "htf_adx_max": 25,
                        "df_exit": df_1m,
                    }
                    result = run_test(params, df_5m, df_1m)
                    stage2_results.append(result)

print(f"  Completed {len(stage2_results)} tests")
print()

stage2_sorted = sorted(stage2_results, key=lambda x: x['net_return'], reverse=True)

print("TOP 15 Stage 2:")
for i, r in enumerate(stage2_sorted[:15]):
    p = r['params']
    print(f"  {i+1:2}. mode={p['entry_mode']:16} vol={p['volume_mult']:.1f} "
          f"atr_s={p['atr_stop']:.1f} t={p['atr_target']:.1f} pb={p['pullback_bars']} "
          f"net={r['net_return']:+7.1f}% wr={r['win_rate']:5.1f}% pf={r['profit_factor']:4.2f}")

top_stage2 = stage2_sorted[:8]
print()

# STAGE 3: HTF Filters (using top 8 from stage 2)
print("=" * 80)
print("STAGE 3: HTF Filters")
print("=" * 80)

stage3_results = []

for stage2_result in top_stage2:
    p2 = stage2_result['params']
    print(f"  Testing HTF with {p2['entry_mode']}, vol={p2['volume_mult']}, atr_s={p2['atr_stop']}, pb={p2['pullback_bars']}...")
    
    for use_htf_vwap in USE_HTF_VWAP:
        for use_htf_ema in USE_HTF_EMA:
            for htf_adx_max in HTF_ADX_MAX:
                params = {
                    "sd_threshold": SD_THRESHOLD,
                    "entry_mode": p2['entry_mode'],
                    "stoch_oversold": p2['stoch_oversold'],
                    "stoch_overbought": p2['stoch_overbought'],
                    "adx_max": p2['adx_max'],
                    "rsi_max": p2['rsi_max'],
                    "volume_mult": p2['volume_mult'],
                    "atr_stop": p2['atr_stop'],
                    "atr_target": p2['atr_target'],
                    "pullback_bars": p2['pullback_bars'],
                    "use_htf_vwap": use_htf_vwap,
                    "use_htf_ema": use_htf_ema,
                    "htf_adx_max": htf_adx_max,
                    "df_exit": df_1m,
                }
                result = run_test(params, df_5m, df_1m)
                stage3_results.append(result)

print(f"  Completed {len(stage3_results)} tests")
print()

# FINAL RESULTS
print("=" * 80)
print("FINAL RESULTS - TOP 10 BEST PERFORMING CONFIGURATIONS")
print("=" * 80)
print("Criteria: Win Rate >45%, Net Return positive, Trades >=20")
print()

stage3_sorted = sorted(stage3_results, key=lambda x: x['net_return'], reverse=True)

qualifying = [
    r for r in stage3_sorted 
    if r['win_rate'] > 45 and r['net_return'] > 0 and r['trades'] >= 20
]

if qualifying:
    final_top = sorted(qualifying, key=lambda x: x['net_return'], reverse=True)[:10]
    
    print(f"{'#':>2} {'Entry Mode':<16} {'Stoch':<10} {'ADX':>4} {'RSI':>5} {'Vol':>5} "
          f"{'ATR-S':>6} {'ATR-T':>6} {'PB':>3} {'HTF-V':>6} {'HTF-E':>6} {'HTF-ADX':>7} "
          f"{'Trds':>5} {'WR%':>5} {'PF':>5} {'Net%':>7}")
    print("-" * 120)
    
    for i, r in enumerate(final_top, 1):
        p = r['params']
        print(f"{i:>2} {p['entry_mode']:<16} ({p['stoch_oversold']},{p['stoch_overbought']:<5}) "
              f"{p['adx_max']:>4} {p['rsi_max']:>5} {p['volume_mult']:>5.1f} "
              f"{p['atr_stop']:>6.1f} {p['atr_target']:>6.1f} {p['pullback_bars']:>3} "
              f"{str(p['use_htf_vwap']):>6} {str(p['use_htf_ema']):>6} {p['htf_adx_max']:>7} "
              f"{r['trades']:>5} {r['win_rate']:>5.1f} {r['profit_factor']:>5.2f} {r['net_return']:>+7.1f}")
else:
    print("No configurations met all criteria. Showing best available results...")
    final_top = stage3_sorted[:10]
    
    print(f"{'#':>2} {'Entry Mode':<16} {'Stoch':<10} {'ADX':>4} {'RSI':>5} {'Vol':>5} "
          f"{'ATR-S':>6} {'ATR-T':>6} {'PB':>3} {'HTF-V':>6} {'HTF-E':>6} {'HTF-ADX':>7} "
          f"{'Trds':>5} {'WR%':>5} {'PF':>5} {'Net%':>7}")
    print("-" * 120)
    
    for i, r in enumerate(final_top, 1):
        p = r['params']
        print(f"{i:>2} {p['entry_mode']:<16} ({p['stoch_oversold']},{p['stoch_overbought']:<5}) "
              f"{p['adx_max']:>4} {p['rsi_max']:>5} {p['volume_mult']:>5.1f} "
              f"{p['atr_stop']:>6.1f} {p['atr_target']:>6.1f} {p['pullback_bars']:>3} "
              f"{str(p['use_htf_vwap']):>6} {str(p['use_htf_ema']):>6} {p['htf_adx_max']:>7} "
              f"{r['trades']:>5} {r['win_rate']:>5.1f} {r['profit_factor']:>5.2f} {r['net_return']:>+7.1f}")

print()
print("=" * 80)
print("DETAILED TOP 10 RESULTS")
print("=" * 80)

for i, r in enumerate(final_top, 1):
    p = r['params']
    print(f"\n### Rank #{i}")
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
    print(f"trades: {r['trades']}")
    print(f"win_rate: {r['win_rate']:.1f}%")
    print(f"profit_factor: {r['profit_factor']:.2f}")
    print(f"net_return: {r['net_return']:.1f}%")

# Save results
results_output = {
    "test_date": datetime.now().isoformat(),
    "test_period": "2023-01-01 to 2025-01-01",
    "symbol": "BTCUSDT",
    "timeframe": "5m with 1m exit",
    "sd_threshold_fixed": SD_THRESHOLD,
    "top_10": [format_result(r) for r in final_top],
    "all_qualifying": [format_result(r) for r in qualifying] if qualifying else [],
    "total_tests": count + len(stage2_results) + len(stage3_results)
}

output_path = Path("BTV2/results/vwap_param_sweep_comprehensive.json")
output_path.parent.mkdir(parents=True, exist_ok=True)
with open(output_path, 'w') as f:
    json.dump(results_output, f, indent=2)

print(f"\nResults saved to: {output_path}")
print()
print("=" * 80)
print("SWEEP COMPLETE")
print("=" * 80)
