#!/usr/bin/env python3
"""
Debug: Check equity curve behavior
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import run_vwap_scalping

STORAGE_ROOT = Path("G:/Candle Data")

# Load data
print("Loading data...")
df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
df.columns = [c.capitalize() for c in df.columns]
df = df[(df.index >= "2023-01-01") & (df.index < "2024-03-01")]  # Shorter period
print(f"Loaded {len(df):,} bars (first 2 months for debug)")
print()

# Simple test - deviation mode
eq, trades = run_vwap_scalping(
    df, cutoff=0.10,
    sd_threshold=1.5,
    atr_stop=0.5,
    atr_target=2.0,
    adx_max=30.0,
    rsi_max=70.0,
    entry_mode="deviation",
    use_trend_filter=False,
    use_volume_filter=False,
    volume_mult=1.0,
    use_trailing_stop=True,
    trailing_atr=1.0,
)

print(f"Equity series length: {len(eq)}")
print(f"First 10 equity values: {eq.iloc[:10].values}")
print(f"Last 10 equity values: {eq.iloc[-10:].values}")
print(f"Min equity: {eq.min():.6f}")
print(f"Max equity: {eq.max():.6f}")
print(f"Final equity: {eq.iloc[-1]:.6f}")
print(f"\nNumber of trades: {len(trades)}")
if trades:
    print(f"Trade returns (first 10): {[f'{t:.4f}' for t in trades[:10]]}")
    print(f"Average trade return: {np.mean(trades)*100:.4f}%")
    print(f"Sum of trade returns: {sum(trades)*100:.2f}%")
