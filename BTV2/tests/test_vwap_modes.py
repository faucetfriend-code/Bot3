#!/usr/bin/env python3
"""
Test VWAP with momentum (trend-following) mode instead of mean reversion
In a strong bull market, trend-following should work better than mean reversion
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR

STORAGE_ROOT = Path("G:/Candle Data")

# Load data
print("Loading data...")
df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
df.columns = [c.capitalize() for c in df.columns]
df = df[(df.index >= "2023-01-01") & (df.index < "2025-01-01")]
print(f"Loaded {len(df):,} bars")
print()

bars_per_year = INTERVAL_BARS_PER_YEAR["5m"]

# Test 1: Mean reversion (original) - should lose in bull market
print("="*80)
print("TEST 1: Mean Reversion (current)")
print("="*80)

eq1, trades1 = run_vwap_scalping(
    df, cutoff=0.10,
    sd_threshold=1.5,
    atr_stop=0.5,
    atr_target=2.0,
    adx_max=30.0,
    rsi_max=70.0,
    entry_mode="mean_reversion",
    use_trend_filter=False,
    use_volume_filter=False,
    volume_mult=1.0,
    use_trailing_stop=True,
    trailing_atr=1.0,
)
m1 = compute_metrics(eq1, trades1, bars_per_year=bars_per_year)
print(f"Trades: {len(trades1)}, WR: {m1['win_rate_pct']:.1f}%, PF: {m1['profit_factor']:.2f}, Return: {m1['total_return_pct']:+.1f}%")

# Test 2: Momentum mode (trend following) - should work better in bull market
print("\n" + "="*80)
print("TEST 2: Momentum (trend following)")
print("="*80)

eq2, trades2 = run_vwap_scalping(
    df, cutoff=0.10,
    sd_threshold=1.5,
    atr_stop=0.5,
    atr_target=2.0,
    adx_max=30.0,
    rsi_max=70.0,
    entry_mode="momentum",  # <-- Changed to momentum
    use_trend_filter=False,
    use_volume_filter=False,
    volume_mult=1.0,
    use_trailing_stop=True,
    trailing_atr=1.0,
    momentum_bars=2,
)
m2 = compute_metrics(eq2, trades2, bars_per_year=bars_per_year)
print(f"Trades: {len(trades2)}, WR: {m2['win_rate_pct']:.1f}%, PF: {m2['profit_factor']:.2f}, Return: {m2['total_return_pct']:+.1f}%")

# Test 3: Cross mode
print("\n" + "="*80)
print("TEST 3: Cross (VWAP crossover)")
print("="*80)

eq3, trades3 = run_vwap_scalping(
    df, cutoff=0.10,
    sd_threshold=1.5,
    atr_stop=0.5,
    atr_target=2.0,
    adx_max=30.0,
    rsi_max=70.0,
    entry_mode="cross",  # <-- Changed to cross
    use_trend_filter=False,
    use_volume_filter=False,
    volume_mult=1.0,
    use_trailing_stop=True,
    trailing_atr=1.0,
)
m3 = compute_metrics(eq3, trades3, bars_per_year=bars_per_year)
print(f"Trades: {len(trades3)}, WR: {m3['win_rate_pct']:.1f}%, PF: {m3['profit_factor']:.2f}, Return: {m3['total_return_pct']:+.1f}%")

# Test 4: Deviation mode  
print("\n" + "="*80)
print("TEST 4: Deviation mode")
print("="*80)

eq4, trades4 = run_vwap_scalping(
    df, cutoff=0.10,
    sd_threshold=1.5,
    atr_stop=0.5,
    atr_target=2.0,
    adx_max=30.0,
    rsi_max=70.0,
    entry_mode="deviation",  # <-- Changed to deviation
    deviation_pct=0.5,
    use_trend_filter=False,
    use_volume_filter=False,
    volume_mult=1.0,
    use_trailing_stop=True,
    trailing_atr=1.0,
)
m4 = compute_metrics(eq4, trades4, bars_per_year=bars_per_year)
print(f"Trades: {len(trades4)}, WR: {m4['win_rate_pct']:.1f}%, PF: {m4['profit_factor']:.2f}, Return: {m4['total_return_pct']:+.1f}%")

# Summary
print("\n" + "="*80)
print("SUMMARY")
print("="*80)
print(f"{'Mode':<20} {'Trades':>8} {'WR%':>6} {'PF':>6} {'Return%':>10}")
print("-"*80)
print(f"{'Mean Reversion':<20} {len(trades1):>8} {m1['win_rate_pct']:>5.1f} {m1['profit_factor']:>5.2f} {m1['total_return_pct']:>+9.1f}")
print(f"{'Momentum':<20} {len(trades2):>8} {m2['win_rate_pct']:>5.1f} {m2['profit_factor']:>5.2f} {m2['total_return_pct']:>+9.1f}")
print(f"{'Cross':<20} {len(trades3):>8} {m3['win_rate_pct']:>5.1f} {m3['profit_factor']:>5.2f} {m3['total_return_pct']:>+9.1f}")
print(f"{'Deviation':<20} {len(trades4):>8} {m4['win_rate_pct']:>5.1f} {m4['profit_factor']:>5.2f} {m4['total_return_pct']:>+9.1f}")
