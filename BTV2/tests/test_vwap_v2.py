#!/usr/bin/env python3
"""
Test VWAP Scalping V2 on 5m and 1m timeframes.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

STORAGE_ROOT = Path("G:/Candle Data")

def load_parquet(symbol, interval):
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return None

sys.path.insert(0, str(Path(__file__).parent.parent))

# Import V2 directly
from strategies import run_vwap_scalping_v2, compute_metrics

# Test 5m
print("=" * 90)
print("VWAP SCALPING V2 - 5m TIMEFRAME")
print("=" * 90)

df_5m = load_parquet("BTCUSDT", "5m")
df_5m.columns = [c.capitalize() for c in df_5m.columns]
df_5m = df_5m[(df_5m.index >= '2024-01-01') & (df_5m.index < '2025-01-01')]
print(f"Loaded {len(df_5m)} bars (2024)")
print()

# Test different SD values
for sd in [2.0, 2.5, 3.0, 3.5, 4.0, 5.0]:
    for atr in [0.5, 1.0, 1.5, 2.0]:
        eq, trades = run_vwap_scalping_v2(
            df_5m, 
            cutoff=0.10, 
            sd_threshold=sd,
            atr_stop=atr,
            atr_target=2.0,
            trailing_stop_atr=1.5,
            use_next_bar=True,
            use_reversal_filter=True,
        )
        m = compute_metrics(eq, trades)
        
        if trades:
            wins = sum(1 for t in trades if t > 0)
            wr = wins / len(trades) * 100
            gp = sum(t for t in trades if t > 0)
            gl = abs(sum(t for t in trades if t < 0))
            pf = gp / gl if gl > 0 else 0
        else:
            wr, pf = 0, 0
        
        gross = m['total_return_pct']
        costs = len(trades) * 0.30
        net = gross - costs
        
        if len(trades) >= 10:
            print(f"SD={sd}, ATR={atr}: Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Trades={len(trades)}, Net={net:+.1f}%")

# Test 1m
print()
print("=" * 90)
print("VWAP SCALPING V2 - 1m TIMEFRAME")
print("=" * 90)

df_1m = load_parquet("BTCUSDT", "1m")
df_1m.columns = [c.capitalize() for c in df_1m.columns]
df_1m = df_1m[(df_1m.index >= '2024-01-01') & (df_1m.index < '2025-01-01')]
print(f"Loaded {len(df_1m)} bars (2024)")
print()

for sd in [2.0, 2.5, 3.0, 3.5, 4.0]:
    for atr in [0.5, 1.0, 1.5]:
        eq, trades = run_vwap_scalping_v2(
            df_1m, 
            cutoff=0.10, 
            sd_threshold=sd,
            atr_stop=atr,
            atr_target=2.0,
            trailing_stop_atr=1.5,
            use_next_bar=True,
            use_reversal_filter=True,
        )
        m = compute_metrics(eq, trades)
        
        if trades:
            wins = sum(1 for t in trades if t > 0)
            wr = wins / len(trades) * 100
            gp = sum(t for t in trades if t > 0)
            gl = abs(sum(t for t in trades if t < 0))
            pf = gp / gl if gl > 0 else 0
        else:
            wr, pf = 0, 0
        
        gross = m['total_return_pct']
        costs = len(trades) * 0.30
        net = gross - costs
        
        if len(trades) >= 10:
            print(f"SD={sd}, ATR={atr}: Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Trades={len(trades)}, Net={net:+.1f}%")
