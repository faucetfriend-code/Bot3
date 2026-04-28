#!/usr/bin/env python3
"""
VWAP Scalping - Higher Timeframe (1h VWAP, 4h EMA/ADX) Backtest
=================================================================
Testing new MTF configuration:
- 5m → 1h for VWAP alignment gate (instead of 15m)
- 5m → 4h for EMA/ADX filter (instead of 1h)
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


# Test configurations - HIGHER TIMEFRAME (1h VWAP, 4h EMA/ADX)
configs = [
    # Test 1: Baseline - Higher TF with recommended params
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.0,
        "adx_max": 18.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.6,
        "atr_target": 3.8,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 30.0,   # Higher for 4h (smoother)
        "df_exit": df_1m,
    },
    # Test 2: Stricter - Lower SD, stricter 5m ADX
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.2,
        "adx_max": 18.0,
        "rsi_max": 38.0,
        "volume_mult": 2.2,
        "atr_stop": 0.5,
        "atr_target": 4.0,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 32.0,
        "df_exit": df_1m,
    },
    # Test 3: Higher SD threshold - less signals
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.5,
        "adx_max": 18.0,
        "rsi_max": 35.0,
        "volume_mult": 2.5,
        "atr_stop": 0.5,
        "atr_target": 4.0,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 35.0,   # Even higher for stricter ranging
        "df_exit": df_1m,
    },
    # Test 4: Lower ATR stop - tighter risk
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.0,
        "adx_max": 20.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.5,
        "atr_target": 3.5,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 30.0,
        "df_exit": df_1m,
    },
    # Test 5: Very strict - minimal trades
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.5,
        "adx_max": 15.0,
        "rsi_max": 35.0,
        "volume_mult": 2.5,
        "atr_stop": 0.5,
        "atr_target": 4.0,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 28.0,   # Very strict for high quality
        "df_exit": df_1m,
    },
    # Test 6: Slightly looser - more signals
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 1.8,
        "adx_max": 20.0,
        "rsi_max": 42.0,
        "volume_mult": 1.8,
        "atr_stop": 0.7,
        "atr_target": 3.5,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 32.0,
        "df_exit": df_1m,
    },
    # Test 7: Moderate - balance of quality and quantity
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.2,
        "adx_max": 19.0,
        "rsi_max": 38.0,
        "volume_mult": 2.2,
        "atr_stop": 0.6,
        "atr_target": 3.8,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 32.0,
        "df_exit": df_1m,
    },
    # Test 8: Lower htf_adx_max for stricter 4h filter
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.0,
        "adx_max": 18.0,
        "rsi_max": 38.0,
        "volume_mult": 2.0,
        "atr_stop": 0.6,
        "atr_target": 3.8,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 25.0,   # Back to original 1h-level
        "df_exit": df_1m,
    },
    # Test 9: High target for higher R:R
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.3,
        "adx_max": 18.0,
        "rsi_max": 38.0,
        "volume_mult": 2.3,
        "atr_stop": 0.5,
        "atr_target": 4.5,    # Very high target
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 32.0,
        "df_exit": df_1m,
    },
    # Test 10: Target optimization
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.0,
        "adx_max": 19.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.55,
        "atr_target": 4.2,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 30.0,
        "df_exit": df_1m,
    },
]

descs = [
    "Higher TF (1h VWAP, 4h EMA) - Baseline",
    "Stricter SD 2.2 + lower RSI 38",
    "Higher SD 2.5 + strict vol 2.5",
    "Lower ATR stop 0.5",
    "Very strict (adx15, rsi35, vol2.5)",
    "Looser (sd1.8, adx20, vol1.8)",
    "Moderate balance",
    "Lower htf_adx 25",
    "High target R:R 4.5",
    "Target optimization",
]

results = []

for config, desc in zip(configs, descs):
    result = run_test(config, desc)
    results.append((desc, result))

# Summary
print("\n" + "="*80)
print("SUMMARY TABLE - Higher Timeframe (1h VWAP, 4h EMA/ADX)")
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

# Sort by win rate (min 50 trades for meaningful stat)
filtered = [(d, r) for d, r in results if r['trades'] >= 50]
if filtered:
    sorted_by_wr = sorted(filtered, key=lambda x: x[1]['win_rate'], reverse=True)
    print("\nTop 3 by Win Rate (min 50 trades):")
    for i, (desc, r) in enumerate(sorted_by_wr[:3], 1):
        print(f"  {i}. {desc}")
        print(f"     WR: {r['win_rate']:.1f}%, Net: {r['net_return']:+.1f}%, Trades: {r['trades']}")

# Sort by trade count in target range
target_range = [(d, r) for d, r in results if 100 <= r['trades'] <= 300]
if target_range:
    sorted_by_trades = sorted(target_range, key=lambda x: x[1]['net_return'], reverse=True)
    print("\nBest in Target Range (100-300 trades):")
    for i, (desc, r) in enumerate(sorted_by_trades[:3], 1):
        print(f"  {i}. {desc}")
        print(f"     Trades: {r['trades']}, Net: {r['net_return']:+.1f}%, WR: {r['win_rate']:.1f}%")

print("\n" + "="*80)
print("TEST COMPLETE")
print("="*80)
