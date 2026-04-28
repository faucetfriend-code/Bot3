#!/usr/bin/env python3
"""
Test with parameters from working test_vwap_adx.py
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
print(f"Loaded {len(df):,} bars (2023-2024)")
print()

# Test parameters from test_vwap_adx.py (loose settings)
print("="*80)
print("TEST 1: test_vwap_adx.py params (loose)")
print("="*80)

eq, trades = run_vwap_scalping(
    df, 
    cutoff=0.10,
    sd_threshold=1.5,
    atr_stop=0.5,
    atr_target=2.0,
    ema_fast=9,
    ema_slow=20,
    use_trend_filter=False,
    use_volume_filter=False,
    volume_mult=1.0,
    use_trailing_stop=True,
    trailing_atr=1.0,
    adx_max=30.0,
    rsi_max=70.0,
    entry_mode="mean_reversion",
)

bars_per_year = INTERVAL_BARS_PER_YEAR["5m"]
m = compute_metrics(eq, trades, bars_per_year=bars_per_year)
gross = m['total_return_pct']
net = gross - len(trades) * 0.30
print(f"Results: Trades={len(trades)}, WR={m['win_rate_pct']:.1f}%, PF={m['profit_factor']:.2f}, Net={net:+.2f}%")
print()

# Test with no ADX filter
print("="*80)
print("TEST 2: No ADX filter (adx_max=999)")
print("="*80)

eq2, trades2 = run_vwap_scalping(
    df, 
    cutoff=0.10,
    sd_threshold=1.5,
    atr_stop=0.5,
    atr_target=2.0,
    ema_fast=9,
    ema_slow=20,
    use_trend_filter=False,
    use_volume_filter=False,
    volume_mult=1.0,
    use_trailing_stop=True,
    trailing_atr=1.0,
    adx_max=999.0,
    rsi_max=99.0,
    entry_mode="mean_reversion",
)

m2 = compute_metrics(eq2, trades2, bars_per_year=bars_per_year)
gross2 = m2['total_return_pct']
net2 = gross2 - len(trades2) * 0.30
print(f"Results: Trades={len(trades2)}, WR={m2['win_rate_pct']:.1f}%, PF={m2['profit_factor']:.2f}, Net={net2:+.2f}%")
print()

# Test with strict MTF + loose core params
print("="*80)
print("TEST 3: MTF + loose core params")
print("="*80)

eq3, trades3 = run_vwap_scalping(
    df, 
    cutoff=0.10,
    sd_threshold=1.5,
    atr_stop=0.5,
    atr_target=2.0,
    ema_fast=9,
    ema_slow=20,
    use_trend_filter=False,
    use_volume_filter=False,
    volume_mult=1.0,
    use_trailing_stop=True,
    trailing_atr=1.0,
    adx_max=30.0,
    rsi_max=70.0,
    entry_mode="mean_reversion",
    use_htf_vwap=True,
    use_htf_ema=True,
    htf_adx_max=25.0,
)

m3 = compute_metrics(eq3, trades3, bars_per_year=bars_per_year)
gross3 = m3['total_return_pct']
net3 = gross3 - len(trades3) * 0.30
print(f"Results: Trades={len(trades3)}, WR={m3['win_rate_pct']:.1f}%, PF={m3['profit_factor']:.2f}, Net={net3:+.2f}%")
print()

# Test 2024 only (closer to test_vwap_adx.py date range)
print("="*80)
print("TEST 4: 2024 only with loose params")
print("="*80)

df_2024 = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
print(f"Loaded {len(df_2024):,} bars (2024)")

eq4, trades4 = run_vwap_scalping(
    df_2024, 
    cutoff=0.10,
    sd_threshold=1.5,
    atr_stop=0.5,
    atr_target=2.0,
    ema_fast=9,
    ema_slow=20,
    use_trend_filter=False,
    use_volume_filter=False,
    volume_mult=1.0,
    use_trailing_stop=True,
    trailing_atr=1.0,
    adx_max=30.0,
    rsi_max=70.0,
    entry_mode="mean_reversion",
)

m4 = compute_metrics(eq4, trades4, bars_per_year=bars_per_year)
gross4 = m4['total_return_pct']
net4 = gross4 - len(trades4) * 0.30
print(f"Results: Trades={len(trades4)}, WR={m4['win_rate_pct']:.1f}%, PF={m4['profit_factor']:.2f}, Net={net4:+.2f}%")
