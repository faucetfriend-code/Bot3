#!/usr/bin/env python3
"""
VWAP Scalping - Focused Parameter Tuning with Higher MTF
=========================================================
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


# Focused tests
configs = [
    # Tighter R:R 1:1
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.5,
        "adx_max": 18.0,
        "rsi_max": 35.0,
        "volume_mult": 2.5,
        "atr_stop": 0.5,
        "atr_target": 2.5,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 30.0,
        "htf_vwap_interval": "60min",
        "htf_ema_interval": "240min",
        "df_exit": df_1m,
    },
    # Very tight R:R
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.5,
        "adx_max": 15.0,
        "rsi_max": 35.0,
        "volume_mult": 2.5,
        "atr_stop": 0.4,
        "atr_target": 2.0,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 28.0,
        "htf_vwap_interval": "60min",
        "htf_ema_interval": "240min",
        "df_exit": df_1m,
    },
    # Higher R:R
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.0,
        "adx_max": 20.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.7,
        "atr_target": 4.0,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 35.0,
        "htf_vwap_interval": "60min",
        "htf_ema_interval": "240min",
        "df_exit": df_1m,
    },
    # Very strict
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.5,
        "adx_max": 15.0,
        "rsi_max": 30.0,
        "volume_mult": 3.0,
        "atr_stop": 0.5,
        "atr_target": 3.0,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 25.0,
        "htf_vwap_interval": "60min",
        "htf_ema_interval": "240min",
        "df_exit": df_1m,
    },
    # Deviation mode
    {
        "entry_mode": "deviation",
        "deviation_pct": 0.5,
        "sd_threshold": 2.0,
        "adx_max": 18.0,
        "rsi_max": 40.0,
        "volume_mult": 2.0,
        "atr_stop": 0.6,
        "atr_target": 3.0,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 35.0,
        "htf_vwap_interval": "60min",
        "htf_ema_interval": "240min",
        "df_exit": df_1m,
    },
    # Bull pullback
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
        "htf_adx_max": 35.0,
        "htf_vwap_interval": "60min",
        "htf_ema_interval": "240min",
        "df_exit": df_1m,
    },
    # Original params
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
        "htf_adx_max": 30.0,
        "htf_vwap_interval": "60min",
        "htf_ema_interval": "240min",
        "df_exit": df_1m,
    },
    # Balanced
    {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.2,
        "adx_max": 18.0,
        "rsi_max": 38.0,
        "volume_mult": 2.2,
        "atr_stop": 0.55,
        "atr_target": 3.2,
        "use_htf_vwap": True,
        "use_htf_ema": True,
        "htf_adx_max": 32.0,
        "htf_vwap_interval": "60min",
        "htf_ema_interval": "240min",
        "df_exit": df_1m,
    },
]

descs = [
    "Tighter R:R 1:1",
    "Very tight R:R 0.5:1",
    "Higher R:R",
    "Very strict filters",
    "Deviation mode",
    "Bull pullback",
    "Original params",
    "Balanced",
]

results = []
for config, desc in zip(configs, descs):
    print(f"Testing: {desc}...")
    result = run_test(config, desc)
    results.append(result)
    print(f"  -> Net: {result['net_return']:+.1f}%, WR: {result['win_rate']:.1f}%, Trades: {result['trades']}")

print("\n" + "="*80)
print("SUMMARY")
print("="*80)
print(f"{'Config':<30} {'Net%':>8} {'WR%':>6} {'PF':>5} {'Trades':>7}")
print("-"*60)

for r in results:
    print(f"{r['desc'][:29]:<30} {r['net_return']:>+7.1f}% {r['win_rate']:>5.1f}% {r['profit_factor']:>4.2f} {r['trades']:>6}")

best = sorted(results, key=lambda x: x['net_return'], reverse=True)[0]
print(f"\nBEST: {best['desc']}")
print(f"  Net: {best['net_return']:+.1f}%, WR: {best['win_rate']:.1f}%, PF: {best['profit_factor']:.2f}, Trades: {best['trades']}")
