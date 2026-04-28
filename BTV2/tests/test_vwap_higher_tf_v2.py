#!/usr/bin/env python3
"""
VWAP Scalping - Higher Timeframe Aggressive Testing
====================================================
Testing different combinations with 1h VWAP and 4h EMA/ADX
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
    print(f"\n{'='*80}")
    print(f"TEST: {desc}")
    print(f"{'='*80}")
    
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
    
    print(f"Results:")
    print(f"  Total Return: {metrics['total_return_pct']:+.2f}%")
    print(f"  Net Return:   {net_return:+.2f}%")
    print(f"  Win Rate:     {win_rate:.1f}%")
    print(f"  Profit Factor: {profit_factor:.2f}")
    print(f"  Trades:       {len(trades)}")
    print(f"  Sharpe:       {metrics['sharpe']:.2f}")
    print(f"  Max DD:       {metrics['max_dd_pct']:.2f}%")
    
    return {
        'total_return': metrics['total_return_pct'],
        'net_return': net_return,
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'trades': len(trades),
        'sharpe': metrics['sharpe'],
        'max_dd': metrics['max_dd_pct']
    }


# More aggressive testing - focus on higher htf_adx_max (4h is smoother)
configs = [
    # Test 1: Very high htf_adx_max (almost no filter)
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.0,
        "adx_max": 18.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.6,
        "atr_target": 3.5,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 50.0,   # Very high - almost no filter
        "df_exit": df_1m,
    },
    # Test 2: No HTF filters at all (baseline comparison)
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.0,
        "adx_max": 18.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.6,
        "atr_target": 3.5,
        "use_htf_vwap": False,
        "use_htf_ema": False,
        "htf_adx_max": 25.0,
        "df_exit": df_1m,
    },
    # Test 3: Only VWAP filter, no EMA/ADX
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.0,
        "adx_max": 18.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.6,
        "atr_target": 3.5,
        "use_htf_vwap": True,
        "use_htf_ema": False,
        "htf_adx_max": 25.0,
        "df_exit": df_1m,
    },
    # Test 4: Different entry mode - momentum
    {
        "entry_mode": "momentum",
        "sd_threshold": 2.0,
        "adx_max": 18.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.6,
        "atr_target": 3.5,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 40.0,
        "df_exit": df_1m,
    },
    # Test 5: Deviation entry mode
    {
        "entry_mode": "deviation",
        "deviation_pct": 0.5,
        "sd_threshold": 2.0,
        "adx_max": 18.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.6,
        "atr_target": 3.5,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 40.0,
        "df_exit": df_1m,
    },
    # Test 6: bull_pullback mode
    {
        "entry_mode": "bull_pullback",
        "sd_threshold": 2.0,
        "adx_max": 18.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.6,
        "atr_target": 3.5,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 40.0,
        "df_exit": df_1m,
    },
    # Test 7: Very loose ADX filter on 5m
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.0,
        "adx_max": 25.0,   # Looser 5m ADX
        "rsi_max": 45.0,   # Looser RSI
        "volume_mult": 1.8,
        "atr_stop": 0.7,
        "atr_target": 3.5,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 45.0,
        "df_exit": df_1m,
    },
    # Test 8: Higher target with loose filters
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.0,
        "adx_max": 22.0,
        "rsi_max": 42.0,
        "volume_mult": 1.8,
        "atr_stop": 0.6,
        "atr_target": 4.5,   # Higher target
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 45.0,
        "df_exit": df_1m,
    },
    # Test 9: Cross entry mode
    {
        "entry_mode": "cross",
        "sd_threshold": 2.0,
        "adx_max": 20.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.6,
        "atr_target": 3.5,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 40.0,
        "df_exit": df_1m,
    },
    # Test 10: Different ATR combo
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.2,
        "adx_max": 20.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.8,   # Wider stop
        "atr_target": 3.0,  # Tighter target for higher win rate
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 40.0,
        "df_exit": df_1m,
    },
]

descs = [
    "Very high htf_adx_max 50",
    "No HTF filters (baseline)",
    "Only VWAP filter, no EMA/ADX",
    "Momentum entry mode",
    "Deviation entry mode",
    "Bull pullback mode",
    "Loose ADX/RSI filters",
    "Higher target R:R 4.5",
    "Cross entry mode",
    "Wider stop + tighter target",
]

results = []

for config, desc in zip(configs, descs):
    result = run_test(config, desc)
    results.append((desc, result))

# Summary
print("\n" + "="*80)
print("SUMMARY TABLE - Higher Timeframe Testing")
print("="*80)
print(f"{'Config':<45} {'Return%':>10} {'Net%':>10} {'WR%':>6} {'PF':>6} {'Trades':>7}")
print("-"*80)

for desc, r in results:
    print(f"{desc:<45} {r['total_return']:>+9.1f}% {r['net_return']:>+9.1f}% {r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} {r['trades']:>6}")

# Find best results
print("\n" + "="*80)
print("BEST RESULTS ANALYSIS")
print("="*80)

# Sort by net return
sorted_by_net = sorted(results, key=lambda x: x[1]['net_return'], reverse=True)
print("\nTop 3 by Net Return:")
for i, (desc, r) in enumerate(sorted_by_net[:3], 1):
    print(f"  {i}. {desc}")
    print(f"     Net: {r['net_return']:+.1f}%, WR: {r['win_rate']:.1f}%, Trades: {r['trades']}")

# Find positive returns
positive = [(d, r) for d, r in results if r['net_return'] > 0]
if positive:
    print("\nPositive Net Return Configs:")
    for desc, r in positive:
        print(f"  - {desc}: Net {r['net_return']:+.1f}%, WR {r['win_rate']:.1f}%, Trades {r['trades']}")

print("\n" + "="*80)
print("TEST COMPLETE")
print("="*80)
