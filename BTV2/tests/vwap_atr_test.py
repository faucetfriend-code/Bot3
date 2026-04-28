#!/usr/bin/env python3
"""
VWAP Scalping - Alternative ATR Settings Test
============================================
Testing tighter ATR stops and targets to match actual win rates.
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

# Test different ATR combinations - trying to match win rates
# If WR ~30-35%, need R:R ~2-2.5:1 to break even
configs = [
    # Tighter stops and targets (matching ~30% WR)
    {"entry_mode": "cross", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 2.0, "atr_stop": 0.3, "atr_target": 0.7, "use_htf_vwap": False},
    {"entry_mode": "cross", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 2.0, "atr_stop": 0.4, "atr_target": 1.0, "use_htf_vwap": False},
    {"entry_mode": "cross", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 2.0, "atr_stop": 0.5, "atr_target": 1.2, "use_htf_vwap": False},
    {"entry_mode": "cross", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 2.0, "atr_stop": 0.5, "atr_target": 1.5, "use_htf_vwap": False},
    {"entry_mode": "cross", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 2.0, "atr_stop": 0.6, "atr_target": 1.8, "use_htf_vwap": False},
    {"entry_mode": "cross", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 2.0, "use_htf_vwap": False},
    # Mean reversion with tighter stops
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 30, "rsi_max": 50, "volume_mult": 2.0, "atr_stop": 0.3, "atr_target": 0.7, "use_htf_vwap": False},
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 30, "rsi_max": 50, "volume_mult": 2.0, "atr_stop": 0.4, "atr_target": 1.0, "use_htf_vwap": False},
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 30, "rsi_max": 50, "volume_mult": 2.0, "atr_stop": 0.5, "atr_target": 1.5, "use_htf_vwap": False},
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 30, "rsi_max": 50, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 2.0, "use_htf_vwap": False},
    # Looser ADX for more trades
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 40, "rsi_max": 60, "volume_mult": 2.0, "atr_stop": 0.5, "atr_target": 1.5, "use_htf_vwap": False},
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 40, "rsi_max": 60, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 2.0, "use_htf_vwap": False},
    # Different stochastic
    {"entry_mode": "cross", "stoch_oversold": 15, "stoch_overbought": 85, "adx_max": 30, "rsi_max": 50, "volume_mult": 2.0, "atr_stop": 0.5, "atr_target": 1.5, "use_htf_vwap": False},
    {"entry_mode": "cross", "stoch_oversold": 25, "stoch_overbought": 75, "adx_max": 30, "rsi_max": 50, "volume_mult": 2.0, "atr_stop": 0.5, "atr_target": 1.5, "use_htf_vwap": False},
    # Without volume filter
    {"entry_mode": "cross", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 1.0, "atr_stop": 0.5, "atr_target": 1.5, "use_htf_vwap": False},
    # Different entry modes
    {"entry_mode": "deviation", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 2.0, "atr_stop": 0.5, "atr_target": 1.5, "use_htf_vwap": False},
    {"entry_mode": "momentum", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 2.0, "atr_stop": 0.5, "atr_target": 1.5, "use_htf_vwap": False},
]

print(f"Testing {len(configs)} ATR configurations...")

results = []
for i, cfg in enumerate(configs):
    params = {
        "sd_threshold": SD_THRESHOLD,
        "entry_mode": cfg.get("entry_mode", "cross"),
        "stoch_oversold": cfg.get("stoch_oversold", 20),
        "stoch_overbought": cfg.get("stoch_overbought", 80),
        "adx_max": cfg.get("adx_max", 25),
        "rsi_max": cfg.get("rsi_max", 45),
        "volume_mult": cfg.get("volume_mult", 2.0),
        "atr_stop": cfg.get("atr_stop", 0.5),
        "atr_target": cfg.get("atr_target", 1.5),
        "pullback_bars": 3,
        "use_htf_vwap": cfg.get("use_htf_vwap", False),
        "use_htf_ema": True,
        "htf_adx_max": 25,
        "df_exit": df_1m,
    }
    rr = params['atr_target'] / params['atr_stop'] if params['atr_stop'] > 0 else 0
    print(f"  [{i+1}/{len(configs)}] {cfg.get('entry_mode'):16} RR={rr:.1f}:1 stoch=({cfg.get('stoch_oversold')},{cfg.get('stoch_overbought')}) adx={cfg.get('adx_max')}...", end=" ")
    result = run_test(params, df_5m, df_1m)
    results.append(result)
    print(f"trades={result['trades']}, WR={result['win_rate']:.1f}%, PF={result['profit_factor']:.2f}, Net={result['net_return']:+.1f}%")

print()

# Sort
results_sorted = sorted(results, key=lambda x: x['net_return'], reverse=True)

print("=" * 100)
print("ALL RESULTS SORTED BY NET RETURN")
print("=" * 100)
print(f"{'#':>2} {'Mode':<16} {'Stoch':<10} {'ADX':>4} {'RSI':>5} {'RR':>5} {'Trds':>5} {'WR%':>5} {'PF':>5} {'Net%':>7}")
print("-" * 80)

for i, r in enumerate(results_sorted, 1):
    p = r['params']
    rr = p['atr_target'] / p['atr_stop'] if p['atr_stop'] > 0 else 0
    print(f"{i:>2} {p['entry_mode']:<16} ({p['stoch_oversold']},{p['stoch_overbought']:<5}) {p['adx_max']:>4} {p['rsi_max']:>5} {rr:>4.1f}:1 {r['trades']:>5} {r['win_rate']:>5.1f} {r['profit_factor']:>5.2f} {r['net_return']:>+7.1f}")

print()

# Filter qualifying
qualifying = [
    r for r in results_sorted 
    if r['win_rate'] > 45 and r['net_return'] > 0 and r['trades'] >= 20
]

print("=" * 80)
print("QUALIFYING RESULTS (WR >45%, Net>0%, Trades>=20)")
print("=" * 80)

if qualifying:
    print(f"\nFound {len(qualifying)} qualifying configurations!\n")
    for i, r in enumerate(qualifying[:10], 1):
        p = r['params']
        rr = p['atr_target'] / p['atr_stop'] if p['atr_stop'] > 0 else 0
        print(f"### Rank #{i} (RR={rr:.1f}:1)")
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
        print()
else:
    print("No results met all criteria. Showing best results:")
    for i, r in enumerate(results_sorted[:5], 1):
        p = r['params']
        rr = p['atr_target'] / p['atr_stop'] if p['atr_stop'] > 0 else 0
        print(f"\n### Best #{i} (RR={rr:.1f}:1)")
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

# Save
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
            'pullback_bars': r['params']['pullback_bars'],
            'use_htf_vwap': r['params']['use_htf_vwap'],
            'use_htf_ema': r['params']['use_htf_ema'],
            'htf_adx_max': r['params']['htf_adx_max'],
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
        for r in results_sorted
    ]
}

output_path = Path("BTV2/results/vwap_atr_test.json")
output_path.parent.mkdir(parents=True, exist_ok=True)
with open(output_path, 'w') as f:
    json.dump(output, f, indent=2)

print(f"\nResults saved to: {output_path}")
print("SWEEP COMPLETE")
