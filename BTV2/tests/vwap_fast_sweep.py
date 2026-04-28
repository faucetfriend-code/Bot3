#!/usr/bin/env python3
"""
VWAP Scalping - FAST Parameter Sweep
=====================================
Minimal version for quick results
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
import json
from datetime import datetime
from itertools import product
from collections import defaultdict
import time

sys.path.insert(0, str(Path(__file__).parent.parent))
from strategies import run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR


def load_parquet(symbol, interval):
    path = Path("G:/Candle Data") / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        return df
    return None


def run_test(params, df_5m, df_1m):
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


def format_result(r):
    return {
        'params': r['params'],
        'trades': r['trades'],
        'win_rate': r['win_rate'],
        'profit_factor': r['profit_factor'],
        'net_return': r['net_return'],
        'sharpe': r['sharpe'],
        'max_dd': r['max_dd']
    }


# ============================================================================
# FAST CONFIG
# ============================================================================
SD_THRESHOLD = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]
ENTRY_MODES = ['bull_pullback', 'bear_pullback', 'mean_reversion', 'cross', 'momentum', 'deviation']
STOCH_OVERSOLD = [15, 20, 25]
STOCH_OVERBOUGHT = [75, 80, 85]
ADX_MAX = [20, 25, 30, 35]
RSI_MAX = [40, 45, 50, 55]
VOLUME_MULT = [1.5, 2.0, 2.5]
ATR_STOP = [0.7, 1.0, 1.5]
ATR_TARGET = [3.0, 4.0, 5.0]
PULLBACK_BARS = [2, 3, 4]
USE_HTF_VWAP = [True, False]
USE_HTF_EMA = [True, False]
HTF_ADX_MAX = [25, 30, 35]

# ============================================================================
# LOAD DATA
# ============================================================================
print("=" * 80)
print("VWAP SCALPING - FAST PARAMETER SWEEP")
print("=" * 80)
print("Loading data...")

df_5m = load_parquet("BTCUSDT", "5m")
df_5m = df_5m[(df_5m.index >= '2020-01-01') & (df_5m.index < '2025-01-01')]
print(f"5m data: {len(df_5m)} bars (2020-2025)")

df_1m = load_parquet("BTCUSDT", "1m")
if df_1m is not None:
    df_1m = df_1m[(df_1m.index >= '2020-01-01') & (df_1m.index < '2025-01-01')]
    print(f"1m data: {len(df_1m)} bars")
else:
    df_1m = df_5m

global_start = time.time()

# ============================================================================
# STAGE 1: Core params only
# ============================================================================
print("\n" + "=" * 80)
print("STAGE 1: SD + Entry Mode + Stochastic + ADX + RSI")
print("=" * 80)

stage1_results = []
core_keys = ["entry_mode", "stoch_oversold", "stoch_overbought", "adx_max", "rsi_max"]
core_vals = [ENTRY_MODES, STOCH_OVERSOLD, STOCH_OVERBOUGHT, ADX_MAX, RSI_MAX]
core_combos = list(product(*core_vals))
print(f"Stage 1 total combos: {len(core_combos) * len(SD_THRESHOLD):,}")

combo_idx = 0
for sd in SD_THRESHOLD:
    print(f"\n--- SD = {sd} ---")
    stage_start = time.time()
    
    for combo in core_combos:
        combo_idx += 1
        if combo_idx % 200 == 0:
            elapsed = time.time() - global_start
            print(f"  Progress: {combo_idx} tests ({elapsed/60:.1f} min)")
        
        # Time limit check
        if time.time() - global_start > 2700:  # 45 min total
            print("  TIME LIMIT REACHED")
            break
            
        params = dict(zip(core_keys, combo))
        params["sd_threshold"] = sd
        params["volume_mult"] = 2.0
        params["atr_stop"] = 1.0
        params["atr_target"] = 4.0
        params["pullback_bars"] = 3
        params["use_htf_vwap"] = True
        params["use_htf_ema"] = True
        params["htf_adx_max"] = 25
        params["df_exit"] = df_1m
        
        result = run_test(params, df_5m, df_1m)
        stage1_results.append(result)
    
    if time.time() - global_start > 2700:
        break

# Stage 1 summary
stage1_sorted = sorted(stage1_results, key=lambda x: x['net_return'], reverse=True)
print(f"\n\nSTAGE 1 COMPLETE: {len(stage1_results)} tests")
print("\nSTAGE 1 TOP 10:")
for i, r in enumerate(stage1_sorted[:10], 1):
    p = r['params']
    print(f"{i}. SD={p['sd_threshold']}, {p['entry_mode']}, WR={r['win_rate']:.1f}%, Net={r['net_return']:.1f}%")

top_for_stage2 = stage1_sorted[:50]
print(f"\nSelected {len(top_for_stage2)} configs for Stage 2")

# ============================================================================
# STAGE 2: Volume + ATR + Pullback
# ============================================================================
print("\n" + "=" * 80)
print("STAGE 2: Volume + ATR + Pullback")
print("=" * 80)

stage2_results = []
stage2_start = datetime.now()

for i, stage1_result in enumerate(top_for_stage2):
    p1 = stage1_result['params']
    cfg_desc = f"SD={p1['sd_threshold']}, {p1['entry_mode']}"
    print(f"[{i+1}/{len(top_for_stage2)}] {cfg_desc}")
    
    for volume_mult in VOLUME_MULT:
        for atr_stop in ATR_STOP:
            for atr_target in ATR_TARGET:
                for pullback_bars in PULLBACK_BARS:
                    if time.time() - global_start > 3600:  # 1 hour total
                        print("TIME LIMIT REACHED")
                        break
                    
                    params = {
                        "sd_threshold": p1['sd_threshold'],
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
    
    if time.time() - global_start > 3600:
        break

print(f"\nStage 2 complete: {len(stage2_results)} tests")

stage2_sorted = sorted(stage2_results, key=lambda x: x['net_return'], reverse=True)
print("\nSTAGE 2 TOP 10:")
for i, r in enumerate(stage2_sorted[:10], 1):
    p = r['params']
    print(f"{i}. SD={p['sd_threshold']}, {p['entry_mode']}, vol={p['volume_mult']}, atr_s={p['atr_stop']}, Net={r['net_return']:.1f}%")

top_configs_stage3 = stage2_sorted[:10]
print(f"\nSelected {len(top_configs_stage3)} configurations for Stage 3")

# ============================================================================
# STAGE 3: HTF Filters
# ============================================================================
print("\n" + "=" * 80)
print("STAGE 3: HTF Filters")
print("=" * 80)

stage3_results = []

for i, stage2_result in enumerate(top_configs_stage3):
    p2 = stage2_result['params']
    cfg_desc = f"SD={p2['sd_threshold']}, {p2['entry_mode']}"
    print(f"[{i+1}/{len(top_configs_stage3)}] {cfg_desc}")
    
    for use_htf_vwap in USE_HTF_VWAP:
        for use_htf_ema in USE_HTF_EMA:
            for htf_adx_max in HTF_ADX_MAX:
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
                    "use_htf_vwap": use_htf_vwap,
                    "use_htf_ema": use_htf_ema,
                    "htf_adx_max": htf_adx_max,
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

# Find qualifying configurations
qualifying = [
    r for r in stage3_sorted 
    if r['win_rate'] > 48 and r['net_return'] > 0 and r['trades'] >= 20
]

print(f"Total configurations tested: {len(stage3_results)}")
print(f"Configurations meeting all criteria (WR>48%, Net>0%, Trades>=20): {len(qualifying)}")

# ============================================================================
# TOP 20
# ============================================================================
if qualifying:
    final_top = sorted(qualifying, key=lambda x: x['net_return'], reverse=True)[:20]
else:
    final_top = stage3_sorted[:20]

print("\n" + "=" * 100)
print("TOP 20 BEST PERFORMING CONFIGURATIONS")
print("=" * 100)
print(f"{'#':>3} {'SD':>4} {'Entry Mode':<16} {'Stoch':<10} {'ADX':>4} {'RSI':>5} "
      f"{'Vol':>5} {'ATR-S':>6} {'ATR-T':>6} {'PB':>3} {'HTF-V':>6} {'HTF-E':>6} {'HTF-ADX':>7} "
      f"{'Trds':>5} {'WR%':>5} {'PF':>5} {'Net%':>7}")
print("-" * 130)

for i, r in enumerate(final_top, 1):
    p = r['params']
    print(f"{i:>3} {p['sd_threshold']:>4.1f} {p['entry_mode']:<16} "
          f"({p['stoch_oversold']},{p['stoch_overbought']:<5}) "
          f"{p['adx_max']:>4} {p['rsi_max']:>5} "
          f"{p['volume_mult']:>5.1f} {p['atr_stop']:>6.1f} {p['atr_target']:>6.1f} {p['pullback_bars']:>3} "
          f"{str(p['use_htf_vwap']):>6} {str(p['use_htf_ema']):>6} {p['htf_adx_max']:>7} "
          f"{r['trades']:>5} {r['win_rate']:>5.1f} {r['profit_factor']:>5.2f} {r['net_return']:>+7.1f}")

# ============================================================================
# SD ANALYSIS
# ============================================================================
print("\n" + "=" * 80)
print("SD THRESHOLD ANALYSIS")
print("=" * 80)

sd_analysis_dict = defaultdict(lambda: {'results': [], 'qualifying': 0})
for r in stage3_results:
    sd = r['params']['sd_threshold']
    sd_analysis_dict[sd]['results'].append(r)
    if r['win_rate'] > 48 and r['net_return'] > 0 and r['trades'] >= 20:
        sd_analysis_dict[sd]['qualifying'] += 1

print(f"\n{'SD':>6} {'Total Tests':>12} {'Qualifying':>12} {'Best Net%':>12} {'Best WR%':>10}")
print("-" * 70)

sd_summary = []
for sd in sorted(sd_analysis_dict.keys()):
    results = sd_analysis_dict[sd]['results']
    best = max(results, key=lambda x: x['net_return'])
    sd_summary.append({
        'sd': sd,
        'total': len(results),
        'qualifying': sd_analysis_dict[sd]['qualifying'],
        'best_net': best['net_return'],
        'best_wr': best['win_rate']
    })
    print(f"{sd:>6.1f} {len(results):>12} {sd_analysis_dict[sd]['qualifying']:>12} {best['net_return']:>+12.1f} {best['win_rate']:>10.1f}")

best_sd = max(sd_summary, key=lambda x: x['qualifying'] if x['qualifying'] > 0 else -999)
print(f"\nBest SD for qualifying: {best_sd['sd']} ({best_sd['qualifying']} configs)")

# ============================================================================
# DETAILED TOP 10
# ============================================================================
print("\n" + "=" * 80)
print("DETAILED TOP 10 CONFIGURATIONS")
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
    print(f"pullback_bars: {p['pullback_bars']}")
    print(f"use_htf_vwap: {p['use_htf_vwap']}")
    print(f"use_htf_ema: {p['use_htf_ema']}")
    print(f"htf_adx_max: {p['htf_adx_max']}")
    print(f"---")
    print(f"trades: {r['trades']}")
    print(f"win_rate: {r['win_rate']:.1f}%")
    print(f"profit_factor: {r['profit_factor']:.2f}")
    print(f"net_return: {r['net_return']:.1f}%")
    print(f"sharpe: {r['sharpe']:.2f}")
    print(f"max_dd: {r['max_dd']:.1f}%")

# ============================================================================
# SAVE RESULTS
# ============================================================================
total_time = time.time() - global_start

results_output = {
    "test_date": datetime.now().isoformat(),
    "test_period": "2020-01-01 to 2025-01-01",
    "symbol": "BTCUSDT",
    "timeframe": "5m with 1m exit",
    "total_tests": len(stage3_results),
    "qualifying_count": len(qualifying),
    "runtime_minutes": total_time / 60,
    "criteria": {
        "win_rate_min": 48,
        "net_return_min": 0,
        "trades_min": 20
    },
    "sd_analysis": sd_summary,
    "top_20_qualifying": [format_result(r) for r in final_top[:20]],
    "all_qualifying": [format_result(r) for r in qualifying] if qualifying else [],
}

output_path = Path("BTV2/results/vwap_fast_sweep.json")
output_path.parent.mkdir(parents=True, exist_ok=True)
with open(output_path, 'w') as f:
    json.dump(results_output, f, indent=2)

print("\n" + "=" * 80)
print("SWEEP COMPLETE")
print("=" * 80)
print(f"Total tests: {len(stage3_results)}")
print(f"Qualifying (WR>48%, Net>0%, Trades>=20): {len(qualifying)}")
print(f"Runtime: {total_time/60:.1f} minutes")
print(f"Results saved to: {output_path}")
