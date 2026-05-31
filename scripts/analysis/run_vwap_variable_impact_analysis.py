#!/usr/bin/env python3
"""
VWAP Parameter Sweep with Variable Impact Analysis - FAST VERSION
====================================================================
Uses 1 year of data for faster execution while still providing meaningful insights.
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
import json
from datetime import datetime
import time

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.strategies import (
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
        'net_return': net_return,
        'trades': len(trades),
        'win_rate': win_rate,
        'profit_factor': profit_factor,
    }


# Load data - USE 1 YEAR FOR SPEED
print("Loading data...")
df_5m = load_parquet("BTCUSDT", "5m")
# Use 2024 only for faster testing
df_5m = df_5m[(df_5m.index >= '2024-01-01') & (df_5m.index < '2025-01-01')]
print(f"5m: {len(df_5m)} bars (2024)")

df_1m = load_parquet("BTCUSDT", "1m")
df_1m = df_1m[(df_1m.index >= '2024-01-01') & (df_1m.index < '2025-01-01')]
print(f"1m: {len(df_1m)} bars for exit")
print()

start_time = time.time()

# ============================================================================
# PARAMETER RANGES - OPTIMIZED FOR SPEED
# ============================================================================

# SD Range (KEY VARIABLE!)
SD_THRESHOLDS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]

# Entry Mode
ENTRY_MODES = ['bull_pullback', 'bear_pullback', 'mean_reversion', 'cross', 'momentum', 'deviation']

# Stochastic - reduced
STOCH_OVERSOLD = [15, 20, 25, 30]
STOCH_OVERBOUGHT = [70, 75, 80, 85]

# Filters
ADX_MAX = [16, 20, 25, 30, 35]
RSI_MAX = [35, 40, 50, 55, 60]
VOLUME_MULT = [1.0, 1.5, 2.0]

# ATR
ATR_STOP = [0.5, 0.7, 1.0, 1.5]
ATR_TARGET = [2.0, 3.0, 4.0, 5.0]

# Pullback
PULLBACK_BARS = [2, 3, 4]

# HTF
USE_HTF_VWAP = [True, False]
USE_HTF_EMA = [True, False]
HTF_ADX_MAX = [20, 25, 30, 35]

# Storage
impact_data = {}

# Default params - SD 2.0 generates trades
default_params = {
    "sd_threshold": 2.0,  # Lower to generate trades
    "entry_mode": "bull_pullback",
    "stoch_oversold": 20,
    "stoch_overbought": 80,
    "adx_max": 25,
    "rsi_max": 50,  
    "volume_mult": 2.0,
    "atr_stop": 1.0,
    "atr_target": 4.0,
    "pullback_bars": 3,
    "use_htf_vwap": False,
    "use_htf_ema": False,
    "htf_adx_max": 25,
    "df_exit": df_1m,
}

print("=" * 80)
print("VWAP PARAMETER IMPACT ANALYSIS (FAST MODE - 2024 DATA)")
print("=" * 80)
print()

total_tests = 0

# 1. SD THRESHOLD
print("Testing SD THRESHOLD...")
for sd in SD_THRESHOLDS:
    params = {**default_params, "sd_threshold": sd}
    impact_data.setdefault('sd_threshold', {})[sd] = run_test(params, df_5m, df_1m)
    total_tests += 1
print(f"  Done")

# 2. ENTRY MODE
print("Testing ENTRY MODE...")
for mode in ENTRY_MODES:
    params = {**default_params, "entry_mode": mode}
    impact_data.setdefault('entry_mode', {})[mode] = run_test(params, df_5m, df_1m)
    total_tests += 1
print(f"  Done")

# 3. STOCHASTIC OVERSOLD
print("Testing STOCHASTIC OVERSOLD...")
for val in STOCH_OVERSOLD:
    params = {**default_params, "stoch_oversold": val}
    impact_data.setdefault('stoch_oversold', {})[val] = run_test(params, df_5m, df_1m)
    total_tests += 1
print(f"  Done")

# 4. STOCHASTIC OVERBOUGHT
print("Testing STOCHASTIC OVERBOUGHT...")
for val in STOCH_OVERBOUGHT:
    params = {**default_params, "stoch_overbought": val}
    impact_data.setdefault('stoch_overbought', {})[val] = run_test(params, df_5m, df_1m)
    total_tests += 1
print(f"  Done")

# 5. ADX MAX
print("Testing ADX MAX...")
for val in ADX_MAX:
    params = {**default_params, "adx_max": val}
    impact_data.setdefault('adx_max', {})[val] = run_test(params, df_5m, df_1m)
    total_tests += 1
print(f"  Done")

# 6. RSI MAX
print("Testing RSI MAX...")
for val in RSI_MAX:
    params = {**default_params, "rsi_max": val}
    impact_data.setdefault('rsi_max', {})[val] = run_test(params, df_5m, df_1m)
    total_tests += 1
print(f"  Done")

# 7. VOLUME MULT
print("Testing VOLUME MULT...")
for val in VOLUME_MULT:
    params = {**default_params, "volume_mult": val}
    impact_data.setdefault('volume_mult', {})[val] = run_test(params, df_5m, df_1m)
    total_tests += 1
print(f"  Done")

# 8. ATR STOP
print("Testing ATR STOP...")
for val in ATR_STOP:
    params = {**default_params, "atr_stop": val}
    impact_data.setdefault('atr_stop', {})[val] = run_test(params, df_5m, df_1m)
    total_tests += 1
print(f"  Done")

# 9. ATR TARGET
print("Testing ATR TARGET...")
for val in ATR_TARGET:
    params = {**default_params, "atr_target": val}
    impact_data.setdefault('atr_target', {})[val] = run_test(params, df_5m, df_1m)
    total_tests += 1
print(f"  Done")

# 10. PULLBACK BARS
print("Testing PULLBACK BARS...")
for val in PULLBACK_BARS:
    params = {**default_params, "pullback_bars": val}
    impact_data.setdefault('pullback_bars', {})[val] = run_test(params, df_5m, df_1m)
    total_tests += 1
print(f"  Done")

# 11. USE HTF VWAP
print("Testing USE HTF VWAP...")
for val in USE_HTF_VWAP:
    params = {**default_params, "use_htf_vwap": val}
    impact_data.setdefault('use_htf_vwap', {})[val] = run_test(params, df_5m, df_1m)
    total_tests += 1
print(f"  Done")

# 12. USE HTF EMA
print("Testing USE HTF EMA...")
for val in USE_HTF_EMA:
    params = {**default_params, "use_htf_ema": val}
    impact_data.setdefault('use_htf_ema', {})[val] = run_test(params, df_5m, df_1m)
    total_tests += 1
print(f"  Done")

# 13. HTF ADX MAX
print("Testing HTF ADX MAX...")
for val in HTF_ADX_MAX:
    params = {**default_params, "htf_adx_max": val}
    impact_data.setdefault('htf_adx_max', {})[val] = run_test(params, df_5m, df_1m)
    total_tests += 1
print(f"  Done")

print(f"\nTotal tests: {total_tests}")
print(f"Total time: {time.time()-start_time:.1f}s")


# ============================================================================
# DISPLAY RESULTS
# ============================================================================

print("\n")
print("=" * 80)
print("VARIABLE IMPACT ANALYSIS RESULTS")
print("=" * 80)
print()

importance_scores = {}

# 1. SD THRESHOLD
print("=== SD THRESHOLD IMPACT ===")
for sd in sorted(impact_data['sd_threshold'].keys()):
    r = impact_data['sd_threshold'][sd]
    print(f"SD {sd}: Avg Return = {r['net_return']:+.2f}% (N={r['trades']:.0f} trades, WR={r['win_rate']:.1f}%, PF={r['profit_factor']:.2f})")
returns = [r['net_return'] for r in impact_data['sd_threshold'].values()]
best_key = max(impact_data['sd_threshold'].keys(), key=lambda k: impact_data['sd_threshold'][k]['net_return'])
importance_scores['sd_threshold'] = {'range': max(returns)-min(returns), 'best': max(returns), 'best_val': best_key}
print()

# 2. ENTRY MODE
print("=== ENTRY MODE IMPACT ===")
for mode in ['bull_pullback', 'bear_pullback', 'mean_reversion', 'cross', 'momentum', 'deviation']:
    if mode in impact_data['entry_mode']:
        r = impact_data['entry_mode'][mode]
        print(f"{mode}: Avg Return = {r['net_return']:+.2f}% (N={r['trades']:.0f} trades, WR={r['win_rate']:.1f}%, PF={r['profit_factor']:.2f})")
returns = [r['net_return'] for r in impact_data['entry_mode'].values()]
best_key = max(impact_data['entry_mode'].keys(), key=lambda k: impact_data['entry_mode'][k]['net_return'])
importance_scores['entry_mode'] = {'range': max(returns)-min(returns), 'best': max(returns), 'best_val': best_key}
print()

# 3. STOCHASTIC OVERSOLD
print("=== STOCHASTIC OVERSOLD IMPACT ===")
for val in sorted(impact_data['stoch_oversold'].keys()):
    r = impact_data['stoch_oversold'][val]
    print(f"stoch_oversold={val}: Avg Return = {r['net_return']:+.2f}% (N={r['trades']:.0f} trades, WR={r['win_rate']:.1f}%)")
returns = [r['net_return'] for r in impact_data['stoch_oversold'].values()]
best_key = max(impact_data['stoch_oversold'].keys(), key=lambda k: impact_data['stoch_oversold'][k]['net_return'])
importance_scores['stoch_oversold'] = {'range': max(returns)-min(returns), 'best': max(returns), 'best_val': best_key}
print()

# 4. STOCHASTIC OVERBOUGHT
print("=== STOCHASTIC OVERBOUGHT IMPACT ===")
for val in sorted(impact_data['stoch_overbought'].keys()):
    r = impact_data['stoch_overbought'][val]
    print(f"stoch_overbought={val}: Avg Return = {r['net_return']:+.2f}% (N={r['trades']:.0f} trades, WR={r['win_rate']:.1f}%)")
returns = [r['net_return'] for r in impact_data['stoch_overbought'].values()]
best_key = max(impact_data['stoch_overbought'].keys(), key=lambda k: impact_data['stoch_overbought'][k]['net_return'])
importance_scores['stoch_overbought'] = {'range': max(returns)-min(returns), 'best': max(returns), 'best_val': best_key}
print()

# 5. ADX
print("=== ADX IMPACT ===")
for val in sorted(impact_data['adx_max'].keys()):
    r = impact_data['adx_max'][val]
    print(f"adx_max={val}: Avg Return = {r['net_return']:+.2f}% (N={r['trades']:.0f} trades, WR={r['win_rate']:.1f}%)")
returns = [r['net_return'] for r in impact_data['adx_max'].values()]
best_key = max(impact_data['adx_max'].keys(), key=lambda k: impact_data['adx_max'][k]['net_return'])
importance_scores['adx_max'] = {'range': max(returns)-min(returns), 'best': max(returns), 'best_val': best_key}
print()

# 6. RSI
print("=== RSI IMPACT ===")
for val in sorted(impact_data['rsi_max'].keys()):
    r = impact_data['rsi_max'][val]
    print(f"rsi_max={val}: Avg Return = {r['net_return']:+.2f}% (N={r['trades']:.0f} trades, WR={r['win_rate']:.1f}%)")
returns = [r['net_return'] for r in impact_data['rsi_max'].values()]
best_key = max(impact_data['rsi_max'].keys(), key=lambda k: impact_data['rsi_max'][k]['net_return'])
importance_scores['rsi_max'] = {'range': max(returns)-min(returns), 'best': max(returns), 'best_val': best_key}
print()

# 7. VOLUME
print("=== VOLUME IMPACT ===")
for val in sorted(impact_data['volume_mult'].keys()):
    r = impact_data['volume_mult'][val]
    print(f"volume_mult={val}: Avg Return = {r['net_return']:+.2f}% (N={r['trades']:.0f} trades, WR={r['win_rate']:.1f}%)")
returns = [r['net_return'] for r in impact_data['volume_mult'].values()]
best_key = max(impact_data['volume_mult'].keys(), key=lambda k: impact_data['volume_mult'][k]['net_return'])
importance_scores['volume_mult'] = {'range': max(returns)-min(returns), 'best': max(returns), 'best_val': best_key}
print()

# 8. ATR STOP
print("=== ATR STOP IMPACT ===")
for val in sorted(impact_data['atr_stop'].keys()):
    r = impact_data['atr_stop'][val]
    print(f"atr_stop={val}: Avg Return = {r['net_return']:+.2f}% (N={r['trades']:.0f} trades, WR={r['win_rate']:.1f}%)")
returns = [r['net_return'] for r in impact_data['atr_stop'].values()]
best_key = max(impact_data['atr_stop'].keys(), key=lambda k: impact_data['atr_stop'][k]['net_return'])
importance_scores['atr_stop'] = {'range': max(returns)-min(returns), 'best': max(returns), 'best_val': best_key}
print()

# 9. ATR TARGET
print("=== ATR TARGET IMPACT ===")
for val in sorted(impact_data['atr_target'].keys()):
    r = impact_data['atr_target'][val]
    print(f"atr_target={val}: Avg Return = {r['net_return']:+.2f}% (N={r['trades']:.0f} trades, WR={r['win_rate']:.1f}%)")
returns = [r['net_return'] for r in impact_data['atr_target'].values()]
best_key = max(impact_data['atr_target'].keys(), key=lambda k: impact_data['atr_target'][k]['net_return'])
importance_scores['atr_target'] = {'range': max(returns)-min(returns), 'best': max(returns), 'best_val': best_key}
print()

# 10. PULLBACK
print("=== PULLBACK IMPACT ===")
for val in sorted(impact_data['pullback_bars'].keys()):
    r = impact_data['pullback_bars'][val]
    print(f"pullback_bars={val}: Avg Return = {r['net_return']:+.2f}% (N={r['trades']:.0f} trades, WR={r['win_rate']:.1f}%)")
returns = [r['net_return'] for r in impact_data['pullback_bars'].values()]
best_key = max(impact_data['pullback_bars'].keys(), key=lambda k: impact_data['pullback_bars'][k]['net_return'])
importance_scores['pullback_bars'] = {'range': max(returns)-min(returns), 'best': max(returns), 'best_val': best_key}
print()

# 11. HTF VWAP
print("=== HTF VWAP IMPACT ===")
for val in sorted(impact_data['use_htf_vwap'].keys()):
    r = impact_data['use_htf_vwap'][val]
    print(f"use_htf_vwap={val}: Avg Return = {r['net_return']:+.2f}% (N={r['trades']:.0f} trades, WR={r['win_rate']:.1f}%)")
returns = [r['net_return'] for r in impact_data['use_htf_vwap'].values()]
best_key = max(impact_data['use_htf_vwap'].keys(), key=lambda k: impact_data['use_htf_vwap'][k]['net_return'])
importance_scores['use_htf_vwap'] = {'range': max(returns)-min(returns), 'best': max(returns), 'best_val': best_key}
print()

# 12. HTF EMA
print("=== HTF EMA IMPACT ===")
for val in sorted(impact_data['use_htf_ema'].keys()):
    r = impact_data['use_htf_ema'][val]
    print(f"use_htf_ema={val}: Avg Return = {r['net_return']:+.2f}% (N={r['trades']:.0f} trades, WR={r['win_rate']:.1f}%)")
returns = [r['net_return'] for r in impact_data['use_htf_ema'].values()]
best_key = max(impact_data['use_htf_ema'].keys(), key=lambda k: impact_data['use_htf_ema'][k]['net_return'])
importance_scores['use_htf_ema'] = {'range': max(returns)-min(returns), 'best': max(returns), 'best_val': best_key}
print()

# 13. HTF ADX
print("=== HTF ADX IMPACT ===")
for val in sorted(impact_data['htf_adx_max'].keys()):
    r = impact_data['htf_adx_max'][val]
    print(f"htf_adx_max={val}: Avg Return = {r['net_return']:+.2f}% (N={r['trades']:.0f} trades, WR={r['win_rate']:.1f}%)")
returns = [r['net_return'] for r in impact_data['htf_adx_max'].values()]
best_key = max(impact_data['htf_adx_max'].keys(), key=lambda k: impact_data['htf_adx_max'][k]['net_return'])
importance_scores['htf_adx_max'] = {'range': max(returns)-min(returns), 'best': max(returns), 'best_val': best_key}
print()


# ============================================================================
# IMPORTANCE RANKING
# ============================================================================

print("=" * 80)
print("VARIABLE IMPORTANCE RANKING (by return range)")
print("=" * 80)

sorted_importance = sorted(importance_scores.items(), key=lambda x: x[1]['range'], reverse=True)

print()
print(f"{'Rank':<4} {'Variable':<20} {'Return Range':>12} {'Best Value':>15} {'Best Return':>12}")
print("-" * 70)

for rank, (var_name, scores) in enumerate(sorted_importance, 1):
    print(f"{rank:<4} {var_name:<20} {scores['range']:>+12.2f}% {str(scores['best_val']):>15} {scores['best']:>+12.2f}%")

print()
print("=" * 80)
print(f"Total tests: {total_tests}")
print(f"Total time: {time.time()-start_time:.1f}s")
print("=" * 80)


# SAVE RESULTS
json_results = {}
for var_name, results in impact_data.items():
    json_results[var_name] = {}
    for key, value in results.items():
        json_results[var_name][str(key)] = value

output_data = {
    "test_date": datetime.now().isoformat(),
    "test_period": "2024-01-01 to 2025-01-01",
    "symbol": "BTCUSDT",
    "timeframe": "5m with 1m exit",
    "total_tests": total_tests,
    "total_time_seconds": time.time() - start_time,
    "variable_importance": {k: v for k, v in sorted_importance},
    "impact_results": json_results
}

output_path = Path("BTV2/results/vwap_variable_impact_analysis.json")
output_path.parent.mkdir(parents=True, exist_ok=True)
with open(output_path, 'w') as f:
    json.dump(output_data, f, indent=2)

print(f"\nResults saved to: {output_path}")
print()
print("ANALYSIS COMPLETE!")
