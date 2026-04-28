#!/usr/bin/env python3
"""
VWAP Scalping - Extended Parameter Sweep with 2 years
====================================================
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


# Load data - 2 years
print("Loading data...")
df_5m = load_parquet("BTCUSDT", "5m")
df_5m = df_5m[(df_5m.index >= '2023-01-01') & (df_5m.index < '2025-01-01')]
print(f"5m: {len(df_5m)} bars (2 years)")

df_1m = load_parquet("BTCUSDT", "1m")
df_1m = df_1m[(df_1m.index >= '2023-01-01') & (df_1m.index < '2025-01-01')]
print(f"1m: {len(df_1m)} bars")
print()

SD_THRESHOLD = 2.5

# More comprehensive configs with relaxed parameters
configs = [
    # Default-like params
    {"entry_mode": "bull_pullback", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 20, "rsi_max": 40, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8},
    # Looser ADX/RSI for more trades
    {"entry_mode": "bull_pullback", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 30, "rsi_max": 50, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8},
    {"entry_mode": "bull_pullback", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 40, "rsi_max": 60, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8},
    # Different stoch thresholds
    {"entry_mode": "bull_pullback", "stoch_oversold": 15, "stoch_overbought": 85, "adx_max": 25, "rsi_max": 45, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8},
    {"entry_mode": "bull_pullback", "stoch_oversold": 25, "stoch_overbought": 75, "adx_max": 25, "rsi_max": 45, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8},
    # Mean reversion mode (more trades)
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 20, "rsi_max": 40, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8},
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 30, "rsi_max": 50, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8},
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 40, "rsi_max": 60, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8},
    {"entry_mode": "mean_reversion", "stoch_oversold": 30, "stoch_overbought": 70, "adx_max": 40, "rsi_max": 60, "volume_mult": 2.5, "atr_stop": 1.0, "atr_target": 3.0},
    # No HTF filters (more trades)
    {"entry_mode": "bull_pullback", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 20, "rsi_max": 40, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8, "use_htf_vwap": False, "use_htf_ema": False},
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 20, "rsi_max": 40, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8, "use_htf_vwap": False, "use_htf_ema": False},
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 30, "rsi_max": 50, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8, "use_htf_vwap": False, "use_htf_ema": False},
    # Volume variations
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 1.0, "atr_stop": 0.7, "atr_target": 3.8, "use_htf_vwap": False},
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 1.5, "atr_stop": 0.7, "atr_target": 3.8, "use_htf_vwap": False},
    # ATR variations
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 1.5, "atr_stop": 0.5, "atr_target": 4.0, "use_htf_vwap": False},
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 1.5, "atr_stop": 1.0, "atr_target": 5.0, "use_htf_vwap": False},
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 1.5, "atr_stop": 1.5, "atr_target": 2.0, "use_htf_vwap": False},
    # Cross mode
    {"entry_mode": "cross", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8, "use_htf_vwap": False},
    {"entry_mode": "cross", "stoch_oversold": 15, "stoch_overbought": 85, "adx_max": 30, "rsi_max": 50, "volume_mult": 1.5, "atr_stop": 1.0, "atr_target": 4.0, "use_htf_vwap": False},
    # Bear pullback
    {"entry_mode": "bear_pullback", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 30, "rsi_max": 50, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8},
    {"entry_mode": "bear_pullback", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 30, "rsi_max": 50, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8, "use_htf_vwap": False, "use_htf_ema": False},
    # Looser htf_adx_max
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8, "htf_adx_max": 35},
    {"entry_mode": "mean_reversion", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 25, "rsi_max": 45, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8, "htf_adx_max": 40},
    # Pullback bars variations
    {"entry_mode": "bull_pullback", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 30, "rsi_max": 50, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8, "pullback_bars": 2},
    {"entry_mode": "bull_pullback", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 30, "rsi_max": 50, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8, "pullback_bars": 4},
    {"entry_mode": "bull_pullback", "stoch_oversold": 20, "stoch_overbought": 80, "adx_max": 30, "rsi_max": 50, "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.8, "pullback_bars": 5, "use_htf_vwap": False, "use_htf_ema": False},
]

print(f"Testing {len(configs)} configurations on 2-year data...")

results = []
for i, cfg in enumerate(configs):
    params = {
        "sd_threshold": SD_THRESHOLD,
        "entry_mode": cfg.get("entry_mode", "bull_pullback"),
        "stoch_oversold": cfg.get("stoch_oversold", 20),
        "stoch_overbought": cfg.get("stoch_overbought", 80),
        "adx_max": cfg.get("adx_max", 20),
        "rsi_max": cfg.get("rsi_max", 40),
        "volume_mult": cfg.get("volume_mult", 2.0),
        "atr_stop": cfg.get("atr_stop", 0.7),
        "atr_target": cfg.get("atr_target", 3.8),
        "pullback_bars": cfg.get("pullback_bars", 3),
        "use_htf_vwap": cfg.get("use_htf_vwap", True),
        "use_htf_ema": cfg.get("use_htf_ema", True),
        "htf_adx_max": cfg.get("htf_adx_max", 25),
        "df_exit": df_1m,
    }
    print(f"  [{i+1}/{len(configs)}] {cfg.get('entry_mode')}, stoch=({cfg.get('stoch_oversold')},{cfg.get('stoch_overbought')}), adx={cfg.get('adx_max')}, rsi={cfg.get('rsi_max')}, htf_vwap={cfg.get('use_htf_vwap', True)}...")
    result = run_test(params, df_5m, df_1m)
    results.append(result)
    print(f"       -> trades={result['trades']}, WR={result['win_rate']:.1f}%, PF={result['profit_factor']:.2f}, Net={result['net_return']:+.1f}%")

print()

# Sort
results_sorted = sorted(results, key=lambda x: x['net_return'], reverse=True)

print("=" * 110)
print("ALL RESULTS SORTED BY NET RETURN (2023-2024)")
print("=" * 110)
print(f"{'#':>2} {'Mode':<16} {'Stoch':<10} {'ADX':>4} {'RSI':>5} {'Vol':>5} "
      f"{'ATR-S':>6} {'ATR-T':>6} {'PB':>3} {'HTF-V':>5} {'HTF-E':>5} {'HTF-A':>5} "
      f"{'Trds':>5} {'WR%':>5} {'PF':>5} {'Net%':>7}")
print("-" * 115)

for i, r in enumerate(results_sorted, 1):
    p = r['params']
    htf_v = 'F' if not p['use_htf_vwap'] else 'T'
    htf_e = 'F' if not p['use_htf_ema'] else 'T'
    print(f"{i:>2} {p['entry_mode']:<16} ({p['stoch_oversold']},{p['stoch_overbought']:<5}) "
          f"{p['adx_max']:>4} {p['rsi_max']:>5} {p['volume_mult']:>5.1f} "
          f"{p['atr_stop']:>6.1f} {p['atr_target']:>6.1f} {p['pullback_bars']:>3} "
          f"{htf_v:>5} {htf_e:>5} {p['htf_adx_max']:>5} "
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
    print(f"\nFound {len(qualifying)} qualifying configurations!\n")
    for i, r in enumerate(qualifying[:10], 1):
        p = r['params']
        print(f"### Rank #{i}")
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
    print("No results met all criteria. Showing best results with trades:")
    best_with_trades = [r for r in results_sorted if r['trades'] > 0]
    for i, r in enumerate(best_with_trades[:5], 1):
        p = r['params']
        print(f"\n### Best #{i} (with trades)")
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

output_path = Path("BTV2/results/vwap_param_sweep_2yr.json")
output_path.parent.mkdir(parents=True, exist_ok=True)
with open(output_path, 'w') as f:
    json.dump(output, f, indent=2)

print(f"\nResults saved to: {output_path}")
print("SWEEP COMPLETE")
