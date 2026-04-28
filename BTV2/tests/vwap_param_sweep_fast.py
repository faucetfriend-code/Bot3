#!/usr/bin/env python3
"""
VWAP Scalping - Streamlined Parameter Sweep
==========================================
Fast parameter testing focusing on the most impactful parameters.
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


# Load data
print("Loading data...")
df_5m = load_parquet("BTCUSDT", "5m")
df_5m = df_5m[(df_5m.index >= '2023-01-01') & (df_5m.index < '2025-01-01')]
print(f"5m: {len(df_5m)} bars")

df_1m = load_parquet("BTCUSDT", "1m")
df_1m = df_1m[(df_1m.index >= '2023-01-01') & (df_1m.index < '2025-01-01')]
print(f"1m: {len(df_1m)} bars")
print()

SD_THRESHOLD = 2.5

# Streamlined parameter sets
ENTRY_MODES = ['bull_pullback', 'mean_reversion']
STOCH_COMBOS = [(10, 90), (20, 80), (30, 70)]
ADX_COMBOS = [(16, 35), (20, 45), (30, 55), (40, 60)]
VOLUME_MULT = [1.0, 2.0]
ATR_COMBOS = [(0.5, 4.0), (1.0, 3.0), (1.5, 2.0)]

results = []
count = 0
total = len(ENTRY_MODES) * len(STOCH_COMBOS) * len(ADX_COMBOS) * len(VOLUME_MULT) * len(ATR_COMBOS)
print(f"Running {total} parameter combinations...")

for entry_mode in ENTRY_MODES:
    for stoch_oversold, stoch_overbought in STOCH_COMBOS:
        for adx_max, rsi_max in ADX_COMBOS:
            for volume_mult in VOLUME_MULT:
                for atr_stop, atr_target in ATR_COMBOS:
                    params = {
                        "sd_threshold": SD_THRESHOLD,
                        "entry_mode": entry_mode,
                        "stoch_oversold": stoch_oversold,
                        "stoch_overbought": stoch_overbought,
                        "adx_max": adx_max,
                        "rsi_max": rsi_max,
                        "volume_mult": volume_mult,
                        "atr_stop": atr_stop,
                        "atr_target": atr_target,
                        "pullback_bars": 3,
                        "use_htf_vwap": True,
                        "use_htf_ema": True,
                        "htf_adx_max": 25,
                        "df_exit": df_1m,
                    }
                    result = run_test(params, df_5m, df_1m)
                    results.append(result)
                    count += 1
                    if count % 20 == 0:
                        print(f"  Progress: {count}/{total}")

print(f"Completed {count} tests")
print()

# Sort by net return
results_sorted = sorted(results, key=lambda x: x['net_return'], reverse=True)

print("=" * 100)
print("ALL RESULTS SORTED BY NET RETURN")
print("=" * 100)
print(f"{'#':>2} {'Mode':<16} {'Stoch':<10} {'ADX':>4} {'RSI':>5} {'Vol':>5} "
      f"{'ATR-S':>6} {'ATR-T':>6} {'Trds':>5} {'WR%':>5} {'PF':>5} {'Net%':>7}")
print("-" * 100)

for i, r in enumerate(results_sorted, 1):
    p = r['params']
    print(f"{i:>2} {p['entry_mode']:<16} ({p['stoch_oversold']},{p['stoch_overbought']:<5}) "
          f"{p['adx_max']:>4} {p['rsi_max']:>5} {p['volume_mult']:>5.1f} "
          f"{p['atr_stop']:>6.1f} {p['atr_target']:>6.1f} "
          f"{r['trades']:>5} {r['win_rate']:>5.1f} {r['profit_factor']:>5.2f} {r['net_return']:>+7.1f}")

print()

# Filter qualifying
qualifying = [
    r for r in results_sorted 
    if r['win_rate'] > 45 and r['net_return'] > 0 and r['trades'] >= 20
]

print("=" * 100)
print("QUALIFYING RESULTS (WR >45%, Net>0%, Trades>=20)")
print("=" * 100)

if qualifying:
    for i, r in enumerate(qualifying[:10], 1):
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
        print(f"trades: {r['trades']}")
        print(f"win_rate: {r['win_rate']:.1f}%")
        print(f"profit_factor: {r['profit_factor']:.2f}")
        print(f"net_return: {r['net_return']:.1f}%")
else:
    print("No results met all criteria. Showing top 3:")
    for i, r in enumerate(results_sorted[:3], 1):
        p = r['params']
        print(f"\n### Best #{i}")
        print(f"entry_mode: {p['entry_mode']}")
        print(f"stoch_oversold: {p['stoch_oversold']}")
        print(f"stoch_overbought: {p['stoch_overbought']}")
        print(f"adx_max: {p['adx_max']}")
        print(f"rsi_max: {p['rsi_max']}")
        print(f"volume_mult: {p['volume_mult']}")
        print(f"atr_stop: {p['atr_stop']}")
        print(f"atr_target: {p['atr_target']}")
        print(f"trades: {r['trades']}")
        print(f"win_rate: {r['win_rate']:.1f}%")
        print(f"profit_factor: {r['profit_factor']:.2f}")
        print(f"net_return: {r['net_return']:.1f}%")

# Save results
output = {
    "test_date": datetime.now().isoformat(),
    "test_period": "2023-01-01 to 2025-01-01",
    "symbol": "BTCUSDT",
    "sd_threshold": SD_THRESHOLD,
    "qualifying": [
        {
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
            'net_return': round(r['net_return'], 1)
        }
        for r in qualifying[:10]
    ],
    "all_results": [
        {
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
            'net_return': round(r['net_return'], 1)
        }
        for r in results_sorted[:30]
    ]
}

output_path = Path("BTV2/results/vwap_param_sweep.json")
output_path.parent.mkdir(parents=True, exist_ok=True)
with open(output_path, 'w') as f:
    json.dump(output, f, indent=2)

print(f"\nResults saved to: {output_path}")
print("SWEEP COMPLETE")
