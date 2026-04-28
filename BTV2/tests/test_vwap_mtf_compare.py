#!/usr/bin/env python3
"""
VWAP Scalping - MTF Timeframe Comparison
=========================================
Compare different MTF configurations:
1. Original: 15m VWAP + 1h EMA/ADX
2. Higher: 1h VWAP + 4h EMA/ADX (as requested)
3. Intermediate: Different combinations
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
    print(f"\n{'='*70}")
    print(f"TEST: {desc}")
    print(f"{'='*70}")
    
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


# Base parameters
base_params = {
    "entry_mode": "mean_reversion",
    "sd_threshold": 2.0,
    "adx_max": 18.0,
    "rsi_max": 40.0,
    "volume_mult": 2.0,
    "atr_stop": 0.6,
    "atr_target": 3.5,
    "df_exit": df_1m,
}

# Test configurations
configs = [
    # Original MTF (15m VWAP + 1h EMA/ADX)
    {
        **base_params,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 25.0,
        "htf_vwap_interval": "15min",
        "htf_ema_interval": "60min",
    },
    # Higher MTF (1h VWAP + 4h EMA/ADX) - as requested
    {
        **base_params,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 35.0,  # Higher for smoother 4h
        "htf_vwap_interval": "60min",
        "htf_ema_interval": "240min",
    },
    # Intermediate: 1h VWAP + 1h EMA/ADX
    {
        **base_params,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 25.0,
        "htf_vwap_interval": "60min",
        "htf_ema_interval": "60min",
    },
    # Higher with higher htf_adx
    {
        **base_params,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 45.0,  # Very high
        "htf_vwap_interval": "60min",
        "htf_ema_interval": "240min",
    },
    # No HTF filters (baseline)
    {
        **base_params,
        "use_htf_vwap": False,
        "use_htf_ema": False,
        "htf_adx_max": 25.0,
        "htf_vwap_interval": "15min",
        "htf_ema_interval": "60min",
    },
    # Only VWAP filter
    {
        **base_params,
        "use_htf_vwap": True,
        "use_htf_ema": False,
        "htf_adx_max": 25.0,
        "htf_vwap_interval": "60min",
        "htf_ema_interval": "60min",
    },
]

descs = [
    "ORIGINAL: 15m VWAP + 1h EMA/ADX",
    "HIGHER: 1h VWAP + 4h EMA/ADX (as requested)",
    "1h VWAP + 1h EMA/ADX",
    "1h VWAP + 4h EMA/ADX (htf_adx=45)",
    "NO HTF FILTERS (baseline)",
    "1h VWAP ONLY (no EMA/ADX)",
]

results = []

for config, desc in zip(configs, descs):
    result = run_test(config, desc)
    results.append((desc, result))

# Summary
print("\n" + "="*80)
print("SUMMARY - MTF TIMEFRAME COMPARISON")
print("="*80)
print(f"{'Config':<45} {'Return%':>10} {'Net%':>10} {'WR%':>6} {'PF':>6} {'Trades':>7}")
print("-"*80)

for desc, r in results:
    print(f"{desc:<45} {r['total_return']:>+9.1f}% {r['net_return']:>+9.1f}% {r['win_rate']:>5.1f}% {r['profit_factor']:>5.2f} {r['trades']:>6}")

# Best results
print("\n" + "="*80)
print("BEST RESULTS")
print("="*80)

# By net return
sorted_by_net = sorted(results, key=lambda x: x[1]['net_return'], reverse=True)
print("\nTop by Net Return:")
for i, (desc, r) in enumerate(sorted_by_net[:3], 1):
    print(f"  {i}. {desc}: Net {r['net_return']:+.1f}%, WR {r['win_rate']:.1f}%, Trades {r['trades']}")

# In target range
target = [(d, r) for d, r in results if 100 <= r['trades'] <= 300]
if target:
    sorted_target = sorted(target, key=lambda x: x[1]['net_return'], reverse=True)
    print("\nIn Target Range (100-300 trades):")
    for desc, r in sorted_target:
        print(f"  - {desc}: Net {r['net_return']:+.1f}%, WR {r['win_rate']:.1f}%, Trades {r['trades']}")

print("\n" + "="*80)
print("TEST COMPLETE")
print("="*80)
