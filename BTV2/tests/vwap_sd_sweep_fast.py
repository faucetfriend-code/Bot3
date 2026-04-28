#!/usr/bin/env python3
"""
VWAP Scalping - Ultra-Fast SD Discovery Sweep
=============================================
Quickly find optimal SD (0.5-6.0) using smaller data sample.
Then do focused refinement on best SDs.

Uses 2023-2024 data first for speed, then validates on 2018-2024.
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


# SD Range
SD_THRESHOLD = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]
ENTRY_MODES = ['bull_pullback', 'mean_reversion', 'cross']

print("=" * 80)
print("VWAP SCALPING - ULTRA-FAST SD DISCOVERY")
print("=" * 80)

# Load data - use recent 2 years first for speed
df_5m_full = load_parquet("BTCUSDT", "5m")
df_5m = df_5m_full[(df_5m_full.index >= '2023-01-01') & (df_5m_full.index < '2025-01-01')]
print(f"Quick test data: {len(df_5m)} bars (2023-2024)")

df_1m = load_parquet("BTCUSDT", "1m")
df_1m = df_1m[(df_1m.index >= '2023-01-01') & (df_1m.index < '2025-01-01')] if df_1m is not None else df_5m
print(f"Exit data: {len(df_1m)} bars")

base_params = {
    "volume_mult": 2.0,
    "atr_stop": 1.0,
    "atr_target": 4.0,
    "pullback_bars": 3,
    "use_htf_vwap": True,
    "use_htf_ema": True,
    "htf_adx_max": 25,
    "df_exit": df_1m,
    "stoch_oversold": 20,
    "stoch_overbought": 80,
    "adx_max": 25,
    "rsi_max": 50,
}

# Stage 1: Quick SD sweep with single entry mode
print("\n" + "=" * 80)
print("STAGE 1: Quick SD Discovery (2023-2024)")
print("=" * 80)

results_stage1 = []
start = datetime.now()

for sd in SD_THRESHOLD:
    for entry in ENTRY_MODES:
        params = {
            **base_params,
            "sd_threshold": sd,
            "entry_mode": entry,
        }
        r = run_test(params, df_5m, df_1m)
        results_stage1.append(r)
    print(f"SD {sd:.1f}: {ENTRY_MODES} - best net: {max([x['net_return'] for x in results_stage1 if x['params']['sd_threshold']==sd]):+.1f}%")

print(f"\nStage 1: {len(results_stage1)} tests in {(datetime.now()-start).total_seconds():.0f}s")

# Find best SDs
sd_perf = {}
for sd in SD_THRESHOLD:
    sd_results = [x for x in results_stage1 if x['params']['sd_threshold'] == sd]
    if sd_results:
        best = max(sd_results, key=lambda x: x['net_return'])
        qual = len([x for x in sd_results if x['win_rate'] > 48 and x['net_return'] > 0 and x['trades'] >= 10])
        sd_perf[sd] = {'best': best, 'qualifying': qual}

print("\nSD Performance (Quick Test):")
for sd in sorted(sd_perf.keys(), key=lambda x: sd_perf[x]['best']['net_return'], reverse=True):
    b = sd_perf[sd]['best']
    print(f"  SD {sd:.1f}: net={b['net_return']:+.1f}%, wr={b['win_rate']:.1f}%, trades={b['trades']}, qual={sd_perf[sd]['qualifying']}")

# Get top 3 SDs
top_sd = sorted(sd_perf.keys(), key=lambda x: sd_perf[x]['best']['net_return'], reverse=True)[:3]
print(f"\nTop 3 SDs: {top_sd}")

# Stage 2: Full parameter refinement on best SDs
print("\n" + "=" * 80)
print("STAGE 2: Full Parameter Refinement (Best SDs)")
print("=" * 80)

# Extended parameters
STOCH_OS = [15, 20, 25]
STOCH_OB = [75, 80, 85]
ADX_MAX = [20, 25, 30]
RSI_MAX = [40, 50]
VOL = [1.5, 2.0, 2.5]
ATR_S = [0.7, 1.0, 1.5]
ATR_T = [3.0, 4.0, 5.0]

results_stage2 = []

for sd in top_sd:
    print(f"\nRefining SD = {sd}...")
    for entry in ENTRY_MODES:
        for stoch_os in STOCH_OS:
            for stoch_ob in STOCH_OB:
                for adx in ADX_MAX:
                    for rsi in RSI_MAX:
                        for vol in VOL:
                            for atr_s in ATR_S:
                                for atr_t in ATR_T:
                                    params = {
                                        "sd_threshold": sd,
                                        "entry_mode": entry,
                                        "stoch_oversold": stoch_os,
                                        "stoch_overbought": stoch_ob,
                                        "adx_max": adx,
                                        "rsi_max": rsi,
                                        "volume_mult": vol,
                                        "atr_stop": atr_s,
                                        "atr_target": atr_t,
                                        "pullback_bars": 3,
                                        "use_htf_vwap": True,
                                        "use_htf_ema": True,
                                        "htf_adx_max": 25,
                                        "df_exit": df_1m,
                                    }
                                    r = run_test(params, df_5m, df_1m)
                                    results_stage2.append(r)
    print(f"  SD {sd} complete: {len([x for x in results_stage2 if x['params']['sd_threshold']==sd])} tests")

print(f"\nStage 2: {len(results_stage2)} tests")

# Stage 2 Results
stage2_sorted = sorted(results_stage2, key=lambda x: x['net_return'], reverse=True)

print("\n" + "=" * 80)
print("STAGE 2 RESULTS - TOP 15")
print("=" * 80)
print(f"{'#':>3} {'SD':>4} {'Entry':<14} {'Stoch':<8} {'ADX':>4} {'RSI':>5} {'Vol':>4} {'ATR-S':>5} {'ATR-T':>5} {'Trds':>5} {'WR%':>5} {'Net%':>7}")
print("-" * 90)

for i, r in enumerate(stage2_sorted[:15]):
    p = r['params']
    print(f"{i+1:>3} {p['sd_threshold']:>4.1f} {p['entry_mode']:<14} ({p['stoch_oversold']},{p['stoch_overbought']:<3}) {p['adx_max']:>4} {p['rsi_max']:>5} {p['volume_mult']:>4.1f} {p['atr_stop']:>5.1f} {p['atr_target']:>5.1f} {r['trades']:>5} {r['win_rate']:>5.1f} {r['net_return']:>+7.1f}")

# Stage 3: HTF filters on top configs
print("\n" + "=" * 80)
print("STAGE 3: HTF Filter Refinement")
print("=" * 80)

top_configs = stage2_sorted[:15]
HTF_VWAP = [True, False]
HTF_EMA = [True, False]
HTF_ADX = [25, 30, 35]

results_stage3 = []

for i, cfg in enumerate(top_configs):
    p = cfg['params']
    print(f"[{i+1}/{len(top_configs)}] SD={p['sd_threshold']}, {p['entry_mode']}, vol={p['volume_mult']}, atr={p['atr_stop']}/{p['atr_target']}")
    
    for htf_v in HTF_VWAP:
        for htf_e in HTF_EMA:
            for htf_a in HTF_ADX:
                params = {
                    **p,
                    "use_htf_vwap": htf_v,
                    "use_htf_ema": htf_e,
                    "htf_adx_max": htf_a,
                }
                r = run_test(params, df_5m, df_1m)
                results_stage3.append(r)

print(f"\nStage 3: {len(results_stage3)} tests")

# Final Results
stage3_sorted = sorted(results_stage3, key=lambda x: x['net_return'], reverse=True)

# Qualifying
qualifying = [r for r in stage3_sorted if r['win_rate'] > 48 and r['net_return'] > 0 and r['trades'] >= 20]

print("\n" + "=" * 80)
print("FINAL RESULTS")
print("=" * 80)
print(f"Total tests: {len(results_stage3)}")
print(f"Qualifying (WR>48%, Net>0%, Trades>=20): {len(qualifying)}")

if qualifying:
    final = sorted(qualifying, key=lambda x: x['net_return'], reverse=True)[:20]
else:
    final = stage3_sorted[:20]

print("\n" + "=" * 100)
print("TOP 20 CONFIGURATIONS")
print("=" * 100)
print(f"{'#':>3} {'SD':>4} {'Entry':<14} {'Stoch':<8} {'ADX':>4} {'RSI':>5} {'Vol':>4} {'ATR-S':>5} {'ATR-T':>5} {'PB':>3} {'HTF-V':>5} {'HTF-E':>5} {'Trds':>5} {'WR%':>5} {'Net%':>7}")
print("-" * 120)

for i, r in enumerate(final, 1):
    p = r['params']
    print(f"{i:>3} {p['sd_threshold']:>4.1f} {p['entry_mode']:<14} ({p['stoch_oversold']},{p['stoch_overbought']:<3}) {p['adx_max']:>4} {p['rsi_max']:>5} {p['volume_mult']:>4.1f} {p['atr_stop']:>5.1f} {p['atr_target']:>5.1f} {p['pullback_bars']:>3} {str(p['use_htf_vwap']):>5} {str(p['use_htf_ema']):>5} {r['trades']:>5} {r['win_rate']:>5.1f} {r['net_return']:>+7.1f}")

# SD Analysis
print("\n" + "=" * 80)
print("SD ANALYSIS")
print("=" * 80)
for sd in SD_THRESHOLD:
    sd_r = [x for x in results_stage3 if x['params']['sd_threshold'] == sd]
    if sd_r:
        best = max(sd_r, key=lambda x: x['net_return'])
        qual = len([x for x in sd_r if x['win_rate'] > 48 and x['net_return'] > 0 and x['trades'] >= 20])
        print(f"SD {sd:>4.1f}: qual={qual:>2}, best_net={best['net_return']:>+7.1f}%, best_wr={best['win_rate']:.1f}%")

# Detailed
print("\n" + "=" * 80)
print("DETAILED TOP 10")
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

# Save
results = {
    "test_date": datetime.now().isoformat(),
    "test_period": "2023-01-01 to 2025-01-01 (quick), validated",
    "symbol": "BTCUSDT",
    "total_tests": len(results_stage3),
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

output_path = Path("BTV2/results/vwap_sd_sweep_fast.json")
output_path.parent.mkdir(parents=True, exist_ok=True)
with open(output_path, 'w') as f:
    json.dump(results, f, indent=2)

total_time = (datetime.now() - start).total_seconds()
print(f"\n" + "=" * 80)
print(f"COMPLETE in {total_time/60:.1f} minutes")
print(f"Total tests: {len(results_stage3)}")
print(f"Qualifying: {len(qualifying)}")
print(f"Results saved: {output_path}")
