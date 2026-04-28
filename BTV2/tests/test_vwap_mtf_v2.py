#!/usr/bin/env python3
"""
VWAP Scalping MTF Backtest - Testing Different Parameter Combinations
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_manager import get_candles
from strategies import (
    run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR
)
from datetime import datetime, timezone

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

# Test configurations
configs = [
    # Original (baseline)
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.5,
        "adx_max": 20.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.7,
        "atr_target": 3.8,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 25.0,
        "df_exit": df_1m,
    },
    # Lower htf_adx_max (stricter 1h ranging gate)
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.5,
        "adx_max": 20.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.7,
        "atr_target": 3.8,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 22.0,
        "df_exit": df_1m,
    },
    # Lower sd_threshold (more signals)
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.0,
        "adx_max": 20.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.7,
        "atr_target": 3.8,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 25.0,
        "df_exit": df_1m,
    },
    # Higher volume_mult (more strict volume)
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.5,
        "adx_max": 20.0,
        "rsi_max": 40.0,
        "volume_mult": 3.0,
        "atr_stop": 0.7,
        "atr_target": 3.8,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 25.0,
        "df_exit": df_1m,
    },
    # Combo: lower htf_adx_max + lower sd
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.0,
        "adx_max": 20.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.7,
        "atr_target": 3.8,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 20.0,
        "df_exit": df_1m,
    },
    # No MTF filters (baseline comparison)
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.5,
        "adx_max": 20.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.7,
        "atr_target": 3.8,
        "use_htf_vwap": False,
        "use_htf_ema": False,
        "htf_adx_max": 25.0,
        "df_exit": df_1m,
    },
    # Stricter: lower adx_max, higher volume
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.5,
        "adx_max": 15.0,
        "rsi_max": 35.0,
        "volume_mult": 2.5,
        "atr_stop": 0.7,
        "atr_target": 3.8,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 22.0,
        "df_exit": df_1m,
    },
]

results = []
descs = [
    "Original params (htf_adx=25, sd=2.5)",
    "Lower htf_adx_max=22",
    "Lower sd_threshold=2.0",
    "Higher volume_mult=3.0",
    "Combo: htf_adx=20 + sd=2.0",
    "NO MTF filters (baseline)",
    "Stricter: adx=15, rsi=35, vol=2.5",
]

for config, desc in zip(configs, descs):
    result = run_test(config, desc)
    results.append((desc, result))

# Summary
print("\n" + "="*80)
print("SUMMARY TABLE")
print("="*80)
print(f"{'Config':<40} {'Return%':>10} {'Net%':>10} {'WR%':>6} {'PF':>6} {'Trades':>7}")
print("-"*80)
for desc, r in results:
    print(f"{desc:<40} {r['total_return']:>+9.1f} {r['net_return']:>+9.1f} {r['win_rate']:>5.1f} {r['profit_factor']:>5.2f} {r['trades']:>6}")
