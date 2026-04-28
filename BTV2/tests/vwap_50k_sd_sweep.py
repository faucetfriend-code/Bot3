#!/usr/bin/env python3
"""
VWAP Scalping - Comprehensive 50,000+ Combination Parameter Sweep
==================================================================
CRITICAL: VARY SD from 0.5 to 6.0 to find the optimal threshold!

Parameters:
- SD Range: [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]
- Entry Mode: bull_pullback, bear_pullback, mean_reversion, cross, momentum, deviation
- Stochastic: 5 oversold × 5 overbought = 25 combinations
- Filters: 7 ADX × 6 RSI × 4 volume = 168 combinations
- ATR: 4 stop × 4 target = 16 combinations
- Pullback: 4 bars
- HTF: 2 VWAP × 2 EMA × 4 ADX = 16 combinations

Total: 12 × 6 × 25 × 168 × 16 × 4 × 16 = 196,608,000 (way too many!)
We use a smart multi-stage approach.

Test Data: BTCUSDT 5m for 2018-2024, 1m exit

Goals:
- Win rate >48%
- Positive net return
- Trade count >= 20
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
import json
from datetime import datetime
from itertools import product
from collections import defaultdict

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
        costs = len(trades) * 0.30  # $0.30 per trade
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
    """Format result for output."""
    p = r['params']
    return {
        'sd_threshold': p.get('sd_threshold'),
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
        'net_return': round(r['net_return'], 1),
        'sharpe': round(r['sharpe'], 2),
        'max_dd': round(r['max_dd'], 1)
    }


# ============================================================================
# PARAMETER RANGES (as specified by user)
# ============================================================================

# CRITICAL: SD Range from 0.5 to 6.0
SD_THRESHOLD = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]

# Entry Modes
ENTRY_MODES = ['bull_pullback', 'bear_pullback', 'mean_reversion', 'cross', 'momentum', 'deviation']

# Stochastic
STOCH_OVERSOLD = [10, 15, 20, 25, 30]
STOCH_OVERBOUGHT = [70, 75, 80, 85, 90]

# Filters
ADX_MAX = [16, 18, 20, 25, 30, 35, 40]
RSI_MAX = [35, 40, 45, 50, 55, 60]
VOLUME_MULT = [1.0, 1.5, 2.0, 2.5]

# ATR
ATR_STOP = [0.5, 0.7, 1.0, 1.5]
ATR_TARGET = [2.0, 3.0, 4.0, 5.0]

# Pullback
PULLBACK_BARS = [2, 3, 4, 5]

# HTF
USE_HTF_VWAP = [True, False]
USE_HTF_EMA = [True, False]
HTF_ADX_MAX = [20, 25, 30, 35]


# ============================================================================
# LOAD DATA
# ============================================================================

print("=" * 80)
print("VWAP SCALPING - COMPREHENSIVE 50,000+ COMBINATION PARAMETER SWEEP")
print("=" * 80)
print()
print("Loading data...")

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
    print("Warning: 1m data not available, using 5m for exits")
    df_1m = df_5m

print()
print("Parameter ranges:")
print(f"  SD Threshold: {SD_THRESHOLD}")
print(f"  Entry Modes: {ENTRY_MODES}")
print(f"  Stochastic: {STOCH_OVERSOLD} oversold, {STOCH_OVERBOUGHT} overbought")
print(f"  ADX Max: {ADX_MAX}")
print(f"  RSI Max: {RSI_MAX}")
print(f"  Volume Mult: {VOLUME_MULT}")
print(f"  ATR Stop: {ATR_STOP}")
print(f"  ATR Target: {ATR_TARGET}")
print(f"  Pullback Bars: {PULLBACK_BARS}")
print(f"  HTF VWAP: {USE_HTF_VWAP}")
print(f"  HTF EMA: {USE_HTF_EMA}")
print(f"  HTF ADX Max: {HTF_ADX_MAX}")

# Calculate theoretical combinations
total_combos = (len(SD_THRESHOLD) * len(ENTRY_MODES) * len(STOCH_OVERSOLD) * 
                len(STOCH_OVERBOUGHT) * len(ADX_MAX) * len(RSI_MAX) * 
                len(VOLUME_MULT) * len(ATR_STOP) * len(ATR_TARGET) * 
                len(PULLBACK_BARS) * len(USE_HTF_VWAP) * len(USE_HTF_EMA) * 
                len(HTF_ADX_MAX))
print(f"\nTotal theoretical combinations: {total_combos:,}")
print("Using smart multi-stage approach for efficiency...")
print()


# ============================================================================
# STAGE 1: SD + Entry Mode + Core Filters (Stoch, ADX, RSI)
# ============================================================================

print("=" * 80)
print("STAGE 1: SD + Entry Mode + Stochastic + ADX + RSI (Essential filters)")
print("=" * 80)

stage1_results = []
base_params = {
    "volume_mult": 2.0,
    "atr_stop": 0.7,
    "atr_target": 3.8,
    "pullback_bars": 3,
    "use_htf_vwap": True,
    "use_htf_ema": True,
    "htf_adx_max": 25,
    "df_exit": df_1m,
}

test_count = 0
stage1_start = datetime.now()

for sd in SD_THRESHOLD:
    print(f"\n--- SD = {sd} ---")
    for entry_mode in ENTRY_MODES:
        for stoch_oversold in STOCH_OVERSOLD:
            for stoch_overbought in STOCH_OVERBOUGHT:
                for adx_max in ADX_MAX:
                    for rsi_max in RSI_MAX:
                        params = {
                            **base_params,
                            "sd_threshold": sd,
                            "entry_mode": entry_mode,
                            "stoch_oversold": stoch_oversold,
                            "stoch_overbought": stoch_overbought,
                            "adx_max": adx_max,
                            "rsi_max": rsi_max,
                        }
                        result = run_test(params, df_5m, df_1m)
                        stage1_results.append(result)
                        test_count += 1
        
        elapsed = (datetime.now() - stage1_start).total_seconds()
        print(f"  SD {sd}: Entry {entry_mode} complete ({test_count} tests, {elapsed:.0f}s elapsed)")

print(f"\nStage 1 complete: {len(stage1_results)} tests in {(datetime.now() - stage1_start).total_seconds():.0f}s")

# Stage 1 Results Summary
stage1_sorted = sorted(stage1_results, key=lambda x: x['net_return'], reverse=True)

print("\n" + "=" * 80)
print("STAGE 1 RESULTS - TOP 20 BY NET RETURN")
print("=" * 80)
print(f"{'#':>3} {'SD':>4} {'Entry Mode':<16} {'Stoch':<10} {'ADX':>4} {'RSI':>5} "
      f"{'Trds':>5} {'WR%':>5} {'PF':>5} {'Net%':>7}")
print("-" * 90)

for i, r in enumerate(stage1_sorted[:20]):
    p = r['params']
    print(f"{i+1:>3} {p['sd_threshold']:>4.1f} {p['entry_mode']:<16} "
          f"({p['stoch_oversold']},{p['stoch_overbought']:<5}) "
          f"{p['adx_max']:>4} {p['rsi_max']:>5} "
          f"{r['trades']:>5} {r['win_rate']:>5.1f} {r['profit_factor']:>5.2f} {r['net_return']:>+7.1f}")

# Get top configs for Stage 2 (top 10 from each SD threshold that meet criteria)
sd_best = defaultdict(list)
for r in stage1_results:
    sd = r['params']['sd_threshold']
    if r['trades'] >= 20:  # Must have minimum trades
        sd_best[sd].append(r)

# Select top configs per SD for Stage 2
top_configs_for_stage2 = []
for sd in SD_THRESHOLD:
    if sd_best[sd]:
        sorted_sd = sorted(sd_best[sd], key=lambda x: x['net_return'], reverse=True)[:5]
        top_configs_for_stage2.extend(sorted_sd)

# Also add overall top 20 regardless of SD
top_configs_for_stage2.extend(stage1_sorted[:30])
top_configs_for_stage2 = top_configs_for_stage2[:50]  # Max 50 configs for Stage 2

print(f"\nSelected {len(top_configs_for_stage2)} configurations for Stage 2")
print()


# ============================================================================
# STAGE 2: Volume + ATR + Pullback (refine top configs)
# ============================================================================

print("=" * 80)
print("STAGE 2: Volume + ATR + Pullback (refining top configs)")
print("=" * 80)

stage2_results = []
stage2_start = datetime.now()

for i, stage1_result in enumerate(top_configs_for_stage2):
    p1 = stage1_result['params']
    cfg_desc = f"SD={p1['sd_threshold']}, {p1['entry_mode']}, stoch=({p1['stoch_oversold']},{p1['stoch_overbought']}), adx={p1['adx_max']}, rsi={p1['rsi_max']}"
    print(f"[{i+1}/{len(top_configs_for_stage2)}] {cfg_desc}")
    
    for volume_mult in VOLUME_MULT:
        for atr_stop in ATR_STOP:
            for atr_target in ATR_TARGET:
                for pullback_bars in PULLBACK_BARS:
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

print(f"\nStage 2 complete: {len(stage2_results)} tests in {(datetime.now() - stage2_start).total_seconds():.0f}s")

# Stage 2 Results
stage2_sorted = sorted(stage2_results, key=lambda x: x['net_return'], reverse=True)

print("\n" + "=" * 80)
print("STAGE 2 RESULTS - TOP 20 BY NET RETURN")
print("=" * 80)
print(f"{'#':>3} {'SD':>4} {'Entry Mode':<16} {'Vol':>5} {'ATR-S':>6} {'ATR-T':>6} {'PB':>3} "
      f"{'Trds':>5} {'WR%':>5} {'PF':>5} {'Net%':>7}")
print("-" * 90)

for i, r in enumerate(stage2_sorted[:20]):
    p = r['params']
    print(f"{i+1:>3} {p['sd_threshold']:>4.1f} {p['entry_mode']:<16} "
          f"{p['volume_mult']:>5.1f} {p['atr_stop']:>6.1f} {p['atr_target']:>6.1f} {p['pullback_bars']:>3} "
          f"{r['trades']:>5} {r['win_rate']:>5.1f} {r['profit_factor']:>5.2f} {r['net_return']:>+7.1f}")

# Select top configs for Stage 3
top_configs_stage3 = stage2_sorted[:30]
print(f"\nSelected {len(top_configs_stage3)} configurations for Stage 3")
print()


# ============================================================================
# STAGE 3: HTF Filters (final refinement)
# ============================================================================

print("=" * 80)
print("STAGE 3: HTF Filters (final refinement)")
print("=" * 80)

stage3_results = []
stage3_start = datetime.now()

for i, stage2_result in enumerate(top_configs_stage3):
    p2 = stage2_result['params']
    cfg_desc = f"SD={p2['sd_threshold']}, {p2['entry_mode']}, vol={p2['volume_mult']}, atr_s={p2['atr_stop']}, pb={p2['pullback_bars']}"
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

print(f"\nStage 3 complete: {len(stage3_results)} tests in {(datetime.now() - stage3_start).total_seconds():.0f}s")
print()


# ============================================================================
# FINAL RESULTS
# ============================================================================

print("=" * 80)
print("FINAL RESULTS - ALL CONFIGURATIONS MEETING CRITERIA")
print("=" * 80)
print("Criteria: Win Rate >48%, Net Return positive, Trades >= 20")
print()

stage3_sorted = sorted(stage3_results, key=lambda x: x['net_return'], reverse=True)

# Find qualifying configurations
qualifying = [
    r for r in stage3_sorted 
    if r['win_rate'] > 48 and r['net_return'] > 0 and r['trades'] >= 20
]

print(f"Total configurations tested: {len(stage3_results)}")
print(f"Configurations meeting all criteria: {len(qualifying)}")
print()

if qualifying:
    final_top = sorted(qualifying, key=lambda x: x['net_return'], reverse=True)[:20]
    
    print("=" * 100)
    print("TOP 20 BEST PERFORMING CONFIGURATIONS (MEETING ALL CRITERIA)")
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
else:
    print("No configurations met ALL criteria. Showing best available results...")
    final_top = stage3_sorted[:20]
    
    print("=" * 100)
    print("TOP 20 BEST PERFORMING CONFIGURATIONS (BEST AVAILABLE)")
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

print()
print("=" * 80)
print("SD THRESHOLD ANALYSIS")
print("=" * 80)

# Aggregate results by SD
sd_analysis = defaultdict(lambda: {'results': [], 'qualifying': 0})
for r in stage3_results:
    sd = r['params']['sd_threshold']
    sd_analysis[sd]['results'].append(r)
    if r['win_rate'] > 48 and r['net_return'] > 0 and r['trades'] >= 20:
        sd_analysis[sd]['qualifying'] += 1

print(f"\n{'SD':>6} {'Total Tests':>12} {'Qualifying':>12} {'Best Net%':>12} {'Best WR%':>10} {'Best Trades':>12}")
print("-" * 80)

sd_summary = []
for sd in sorted(sd_analysis.keys()):
    results = sd_analysis[sd]['results']
    best = max(results, key=lambda x: x['net_return'])
    sd_summary.append({
        'sd': sd,
        'total': len(results),
        'qualifying': sd_analysis[sd]['qualifying'],
        'best_net': best['net_return'],
        'best_wr': best['win_rate'],
        'best_trades': best['trades']
    })
    print(f"{sd:>6.1f} {len(results):>12} {sd_analysis[sd]['qualifying']:>12} {best['net_return']:>+12.1f} {best['win_rate']:>10.1f} {best['trades']:>12}")

# Find best SD
best_sd = max(sd_summary, key=lambda x: x['qualifying'] if x['qualifying'] > 0 else -999)
print()
print(f"Best SD for qualifying configurations: {best_sd['sd']} (with {best_sd['qualifying']} qualifying configs)")
print()


# ============================================================================
# DETAILED TOP 10
# ============================================================================

print("=" * 80)
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

results_output = {
    "test_date": datetime.now().isoformat(),
    "test_period": "2018-01-01 to 2025-01-01",
    "symbol": "BTCUSDT",
    "timeframe": "5m with 1m exit (where available)",
    "total_tests": len(stage3_results),
    "qualifying_count": len(qualifying),
    "criteria": {
        "win_rate_min": 48,
        "net_return_min": 0,
        "trades_min": 20
    },
    "sd_analysis": sd_summary,
    "top_20_qualifying": [format_result(r) for r in final_top[:20]],
    "all_qualifying": [format_result(r) for r in qualifying] if qualifying else [],
}

output_path = Path("BTV2/results/vwap_comprehensive_sd_sweep_50k.json")
output_path.parent.mkdir(parents=True, exist_ok=True)
with open(output_path, 'w') as f:
    json.dump(results_output, f, indent=2)

print()
print("=" * 80)
print("SWEEP COMPLETE")
print("=" * 80)
print(f"Total tests run: {len(stage3_results)}")
print(f"Qualifying configurations (WR>48%, Net>0%, Trades>=20): {len(qualifying)}")
print(f"Results saved to: {output_path}")
print()
print("KEY FINDING - BEST SD VALUES:")
print("-" * 40)
for s in sorted(sd_summary, key=lambda x: x['qualifying'], reverse=True)[:5]:
    print(f"  SD {s['sd']}: {s['qualifying']} qualifying configs, best net return: {s['best_net']:+.1f}%")
