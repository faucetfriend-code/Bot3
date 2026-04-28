#!/usr/bin/env python3
"""
VWAP Scalping MTF Backtest - Test with VS_DEFAULTS exact parameters
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from data_manager import get_candles
from strategies import (
    run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR, VS_DEFAULTS
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

print("\nVS_DEFAULTS:")
for k, v in VS_DEFAULTS.items():
    print(f"  {k}: {v}")
print()

# Test 1: VS_DEFAULTS exactly as defined
print("="*80)
print("TEST 1: VS_DEFAULTS EXACT")
print("="*80)

params = VS_DEFAULTS.copy()
params["df_exit"] = df_1m

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

# Test 2: With more aggressive SD threshold
print("\n" + "="*80)
print("TEST 2: sd_threshold=3.0 (higher, stricter)")
print("="*80)

params2 = VS_DEFAULTS.copy()
params2["sd_threshold"] = 3.0
params2["df_exit"] = df_1m

equity2, trades2 = run_vwap_scalping(df_5m, cutoff=0.10, **params2)
metrics2 = compute_metrics(equity2, trades2, bars_per_year=bars_per_year)

if trades2:
    wins2 = sum(1 for t in trades2 if t > 0)
    win_rate2 = wins2 / len(trades2) * 100
    gross_profit2 = sum(t for t in trades2 if t > 0)
    gross_loss2 = abs(sum(t for t in trades2 if t < 0))
    profit_factor2 = gross_profit2 / gross_loss2 if gross_loss2 > 0 else 0
    costs2 = len(trades2) * 0.30
    net_return2 = metrics2['total_return_pct'] - costs2
else:
    win_rate2, profit_factor2, costs2, net_return2 = 0, 0, 0, 0

print(f"Results:")
print(f"  Total Return: {metrics2['total_return_pct']:+.2f}%")
print(f"  Net Return:   {net_return2:+.2f}%")
print(f"  Win Rate:     {win_rate2:.1f}%")
print(f"  Profit Factor: {profit_factor2:.2f}")
print(f"  Trades:       {len(trades2)}")

# Test 3: No MTF filters (baseline comparison)
print("\n" + "="*80)
print("TEST 3: NO MTF FILTERS (baseline)")
print("="*80)

params3 = VS_DEFAULTS.copy()
params3["use_htf_vwap"] = False
params3["use_htf_ema"] = False
params3["df_exit"] = df_1m

equity3, trades3 = run_vwap_scalping(df_5m, cutoff=0.10, **params3)
metrics3 = compute_metrics(equity3, trades3, bars_per_year=bars_per_year)

if trades3:
    wins3 = sum(1 for t in trades3 if t > 0)
    win_rate3 = wins3 / len(trades3) * 100
    gross_profit3 = sum(t for t in trades3 if t > 0)
    gross_loss3 = abs(sum(t for t in trades3 if t < 0))
    profit_factor3 = gross_profit3 / gross_loss3 if gross_loss3 > 0 else 0
    costs3 = len(trades3) * 0.30
    net_return3 = metrics3['total_return_pct'] - costs3
else:
    win_rate3, profit_factor3, costs3, net_return3 = 0, 0, 0, 0

print(f"Results:")
print(f"  Total Return: {metrics3['total_return_pct']:+.2f}%")
print(f"  Net Return:   {net_return3:+.2f}%")
print(f"  Win Rate:     {win_rate3:.1f}%")
print(f"  Profit Factor: {profit_factor3:.2f}")
print(f"  Trades:       {len(trades3)}")

# Summary
print("\n" + "="*80)
print("SUMMARY")
print("="*80)
print(f"{'Config':<30} {'Return%':>10} {'Net%':>10} {'WR%':>6} {'PF':>6} {'Trades':>7}")
print("-"*80)
print(f"{'VS_DEFAULTS':<30} {metrics['total_return_pct']:>+9.1f} {net_return:>+9.1f} {win_rate:>5.1f} {profit_factor:>5.2f} {len(trades):>6}")
print(f"{'sd=3.0 stricter':<30} {metrics2['total_return_pct']:>+9.1f} {net_return2:>+9.1f} {win_rate2:>5.1f} {profit_factor2:>5.2f} {len(trades2):>6}")
print(f"{'No MTF filters':<30} {metrics3['total_return_pct']:>+9.1f} {net_return3:>+9.1f} {win_rate3:>5.1f} {profit_factor3:>5.2f} {len(trades3):>6}")
