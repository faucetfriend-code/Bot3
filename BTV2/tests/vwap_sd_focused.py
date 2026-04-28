#!/usr/bin/env python3
"""
VWAP SD Focused Sweep - Using Historical Best Parameters
========================================================
Based on findings: sd_threshold = 3.45 worked, but recent tests used 2.5
Focus on SD 3.0-6.0 with momentum mode which showed highest WR
"""

import sys
from pathlib import Path
import pandas as pd
import json
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR

STORAGE_ROOT = Path("G:/Candle Data")

def load_parquet(symbol, interval):
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        return df
    return None

# Use 2020-2024 data (where it historically worked) + 2023-2024
print("=" * 80)
print("VWAP SD FOCUSED SWEEP - Testing Historical Best Parameters")
print("=" * 80)

# Load data - use 2023-2024
df_5m = load_parquet("BTCUSDT", "5m")
df_5m = df_5m[(df_5m.index >= '2023-01-01') & (df_5m.index < '2025-01-01')]
print(f"Data: {len(df_5m)} bars (2023-2024)")

# SD values to test - focus on higher range
SD_THRESHOLD = [3.0, 3.25, 3.45, 3.5, 3.75, 4.0, 4.25, 4.5, 5.0, 5.5, 6.0]

# Focus on momentum mode which showed highest WR
ENTRY_MODES = ['momentum', 'bull_pullback', 'cross']

# Other parameters to vary
STOCH_OS = [15, 20, 25]
STOCH_OB = [75, 80, 85]
ADX_MAX = [20, 25, 30, 35]
RSI_MAX = [40, 45, 50, 55]
VOL = [1.5, 2.0, 2.5]
ATR_S = [0.5, 0.7, 1.0]
ATR_T = [1.5, 2.0, 3.0]

results = []
start = datetime.now()

print("\n=== STAGE 1: SD + Entry Mode ===")
for sd in SD_THRESHOLD:
    print(f"\nSD = {sd}")
    for entry in ENTRY_MODES:
        for stoch_os in STOCH_OS:
            for stoch_ob in STOCH_OB:
                for adx in ADX_MAX:
                    for rsi in RSI_MAX:
                        params = {
                            "sd_threshold": sd,
                            "entry_mode": entry,
                            "stoch_oversold": stoch_os,
                            "stoch_overbought": stoch_ob,
                            "adx_max": adx,
                            "rsi_max": rsi,
                            "volume_mult": 2.0,
                            "atr_stop": 0.7,
                            "atr_target": 2.0,
                            "pullback_bars": 3,
                            "use_htf_vwap": True,
                            "use_htf_ema": True,
                            "htf_adx_max": 25,
                            "df_exit": df_5m,
                        }
                        
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
                        
                        r = {
                            'params': params.copy(),
                            'trades': len(trades),
                            'win_rate': win_rate,
                            'profit_factor': profit_factor,
                            'net_return': net_return,
                            'total_return': metrics['total_return_pct'],
                            'sharpe': metrics['sharpe'],
                            'max_dd': metrics['max_dd_pct']
                        }
                        results.append(r)
        
        elapsed = (datetime.now() - start).total_seconds()
        sd_results = [x for x in results if x['params']['sd_threshold'] == sd]
        best = max(sd_results, key=lambda x: x['net_return']) if sd_results else None
        print(f"  SD {sd}: {entry} - {len(sd_results)} tests, best net: {best['net_return']:+.1f}% ({elapsed:.0f}s)")

print(f"\n=== STAGE 1 COMPLETE: {len(results)} tests in {(datetime.now()-start).total_seconds():.0f}s ===")

# Find best configs for Stage 2
stage1_sorted = sorted(results, key=lambda x: x['net_return'], reverse=True)
top_configs = [r for r in stage1_sorted[:30] if r['trades'] >= 15]
print(f"Selected {len(top_configs)} configs for Stage 2")

print("\n=== STAGE 2: ATR Optimization ===")
results_stage2 = []

for i, cfg in enumerate(top_configs):
    p = cfg['params']
    print(f"[{i+1}/{len(top_configs)}] SD={p['sd_threshold']}, {p['entry_mode']}, stoch=({p['stoch_oversold']},{p['stoch_overbought']}), adx={p['adx_max']}, rsi={p['rsi_max']}")
    
    for vol in VOL:
        for atr_s in ATR_S:
            for atr_t in ATR_T:
                params = {
                    "sd_threshold": p['sd_threshold'],
                    "entry_mode": p['entry_mode'],
                    "stoch_oversold": p['stoch_oversold'],
                    "stoch_overbought": p['stoch_overbought'],
                    "adx_max": p['adx_max'],
                    "rsi_max": p['rsi_max'],
                    "volume_mult": vol,
                    "atr_stop": atr_s,
                    "atr_target": atr_t,
                    "pullback_bars": 3,
                    "use_htf_vwap": True,
                    "use_htf_ema": True,
                    "htf_adx_max": 25,
                    "df_exit": df_5m,
                }
                
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
                
                r = {
                    'params': params,
                    'trades': len(trades),
                    'win_rate': win_rate,
                    'profit_factor': profit_factor,
                    'net_return': net_return,
                    'total_return': metrics['total_return_pct'],
                    'sharpe': metrics['sharpe'],
                    'max_dd': metrics['max_dd_pct']
                }
                results_stage2.append(r)

print(f"\n=== STAGE 2 COMPLETE: {len(results_stage2)} tests ===")

# Final results
all_results = results + results_stage2
all_sorted = sorted(all_results, key=lambda x: x['net_return'], reverse=True)

# Qualifying
qualifying = [r for r in all_sorted if r['win_rate'] > 48 and r['net_return'] > 0 and r['trades'] >= 20]

print("\n" + "=" * 100)
print("FINAL RESULTS")
print("=" * 100)
print(f"Total tests: {len(all_results)}")
print(f"Qualifying (WR>48%, Net>0%, Trades>=20): {len(qualifying)}")

final = sorted(qualifying, key=lambda x: x['net_return'], reverse=True)[:20] if qualifying else all_sorted[:20]

print("\n" + "=" * 110)
print("TOP 20 CONFIGURATIONS")
print("=" * 110)
print(f"{'#':>3} {'SD':>5} {'Entry':<14} {'Stoch':<10} {'ADX':>4} {'RSI':>5} {'Vol':>5} {'ATR-S':>6} {'ATR-T':>6} {'Trds':>5} {'WR%':>5} {'PF':>5} {'Net%':>7}")
print("-" * 100)

for i, r in enumerate(final, 1):
    p = r['params']
    print(f"{i:>3} {p['sd_threshold']:>5.2f} {p['entry_mode']:<14} ({p['stoch_oversold']},{p['stoch_overbought']:<5}) {p['adx_max']:>4} {p['rsi_max']:>5} {p['volume_mult']:>5.1f} {p['atr_stop']:>6.1f} {p['atr_target']:>6.1f} {r['trades']:>5} {r['win_rate']:>5.1f} {r['profit_factor']:>5.2f} {r['net_return']:>+7.1f}")

# SD Analysis
print("\n" + "=" * 80)
print("SD ANALYSIS")
print("=" * 80)
for sd in sorted(set(x['params']['sd_threshold'] for x in all_results)):
    sd_r = [x for x in all_results if x['params']['sd_threshold'] == sd]
    if sd_r:
        best = max(sd_r, key=lambda x: x['net_return'])
        qual = len([x for x in sd_r if x['win_rate'] > 48 and x['net_return'] > 0 and x['trades'] >= 20])
        print(f"SD {sd:>5.2f}: qual={qual:>2}, best_net={best['net_return']:>+7.1f}%, best_wr={best['win_rate']:.1f}%, trades={best['trades']}")

# Detailed output
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
output = {
    "test_date": datetime.now().isoformat(),
    "test_period": "2023-01-01 to 2025-01-01",
    "total_tests": len(all_results),
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
        'trades': r['trades'],
        'win_rate': round(r['win_rate'], 1),
        'profit_factor': round(r['profit_factor'], 2),
        'net_return': round(r['net_return'], 1),
    } for r in final]
}

Path("BTV2/results").mkdir(parents=True, exist_ok=True)
with open("BTV2/results/vwap_sd_focused_sweep.json", 'w') as f:
    json.dump(output, f, indent=2)

print(f"\n" + "=" * 80)
print(f"COMPLETE in {(datetime.now()-start).total_seconds()/60:.1f} minutes")
print(f"Results saved: BTV2/results/vwap_sd_focused_sweep.json")
