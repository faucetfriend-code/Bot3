#!/usr/bin/env python3
"""
VWAP Scalping - Aggressive Parameter Sweep
==========================================
Find better parameter combinations with the 1h VWAP + 4h EMA/ADX configuration
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

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


# Load data
print("Loading data...")
df_5m = load_parquet("BTCUSDT", "5m")
df_5m = df_5m[(df_5m.index >= '2023-01-01') & (df_5m.index < '2025-01-01')]
print(f"5m: {len(df_5m)} bars")

df_1m = load_parquet("BTCUSDT", "1m")
df_1m = df_1m[(df_1m.index >= '2023-01-01') & (df_1m.index < '2025-01-01')]
print(f"1m: {len(df_1m)} bars")
print()


def run_test(params, desc):
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
        'desc': desc,
        'total_return': metrics['total_return_pct'],
        'net_return': net_return,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'trades': len(trades),
        'sharpe': metrics['sharpe'],
        'max_dd': metrics['max_dd_pct']
    }


# Test various parameter combinations with higher MTF
print("Testing parameter combinations with 1h VWAP + 4h EMA/ADX...\n")

results = []

# Parameter grid
sd_thresholds = [2.0, 2.2, 2.5]
adx_maxs = [15, 18, 20]
rsi_maxs = [35, 38, 40]
atr_stops = [0.5, 0.6, 0.7]
atr_targets = [2.5, 3.0, 3.5]
volume_mults = [2.0, 2.5]
htf_adx_maxs = [30, 35]

count = 0
for sd in sd_thresholds:
    for adx in adx_maxs:
        for rsi in rsi_maxs:
            for atr_s in atr_stops:
                for atr_t in atr_targets:
                    for vol in volume_mults:
                        for htf_adx in htf_adx_maxs:
                            params = {
                                "entry_mode": "mean_reversion",
                                "sd_threshold": sd,
                                "adx_max": adx,
                                "rsi_max": rsi,
                                "volume_mult": vol,
                                "atr_stop": atr_s,
                                "atr_target": atr_t,
                                "use_htf_vwap": True,
                                "use_htf_ema": True,
                                "htf_adx_max": htf_adx,
                                "htf_vwap_interval": "60min",
                                "htf_ema_interval": "240min",
                                "df_exit": df_1m,
                            }
                            desc = f"sd={sd}, adx={adx}, rsi={rsi}, atr_s={atr_s}, atr_t={atr_t}, vol={vol}, htf_adx={htf_adx}"
                            result = run_test(params, desc)
                            results.append(result)
                            count += 1

print(f"Tested {count} parameter combinations\n")

# Sort by net return
results_sorted = sorted(results, key=lambda x: x['net_return'], reverse=True)

print("="*80)
print("TOP 15 RESULTS BY NET RETURN")
print("="*80)
print(f"{'Config':<60} {'Net%':>8} {'WR%':>6} {'PF':>5} {'Trades':>7}")
print("-"*80)

for r in results_sorted[:15]:
    print(f"{r['desc'][:59]:<60} {r['net_return']:>+7.1f}% {r['win_rate']:>5.1f}% {r['profit_factor']:>4.2f} {r['trades']:>6}")

# Find positive or break-even results
positive = [r for r in results if r['net_return'] > -20]
if positive:
    print("\n" + "="*80)
    print("BEST RESULTS (Net Return > -20%)")
    print("="*80)
    for r in positive[:10]:
        print(f"  {r['desc']}")
        print(f"    Net: {r['net_return']:+.1f}%, WR: {r['win_rate']:.1f}%, PF: {r['profit_factor']:.2f}, Trades: {r['trades']}")

# Target range results
target_range = [r for r in results if 100 <= r['trades'] <= 300]
if target_range:
    target_sorted = sorted(target_range, key=lambda x: x['net_return'], reverse=True)
    print("\n" + "="*80)
    print("BEST IN TARGET RANGE (100-300 trades)")
    print("="*80)
    for r in target_sorted[:5]:
        print(f"  {r['desc']}")
        print(f"    Net: {r['net_return']:+.1f}%, WR: {r['win_rate']:.1f}%, PF: {r['profit_factor']:.2f}, Trades: {r['trades']}")

# Win rate focus
high_wr = [r for r in results if r['win_rate'] > 35 and r['trades'] >= 30]
if high_wr:
    wr_sorted = sorted(high_wr, key=lambda x: x['net_return'], reverse=True)
    print("\n" + "="*80)
    print("HIGH WIN RATE (WR > 35%, min 30 trades)")
    print("="*80)
    for r in wr_sorted[:5]:
        print(f"  {r['desc']}")
        print(f"    Net: {r['net_return']:+.1f}%, WR: {r['win_rate']:.1f}%, PF: {r['profit_factor']:.2f}, Trades: {r['trades']}")

print("\n" + "="*80)
print("TEST COMPLETE")
print("="*80)
