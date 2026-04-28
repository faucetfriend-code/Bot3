#!/usr/bin/env python3
"""
Check BTC trend direction - is mean reversion the wrong strategy for 2023-2024?
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

STORAGE_ROOT = Path("G:/Candle Data")

# Load data
df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
df.columns = [c.capitalize() for c in df.columns]
df = df[(df.index >= "2023-01-01") & (df.index < "2025-01-01")]
print(f"Loaded {len(df):,} bars")

prices = df["Close"].values

# Check overall trend
start_price = prices[0]
end_price = prices[-1]
total_return = (end_price / start_price - 1) * 100
print(f"\nBTC 2023-2024 return: {total_return:+.1f}%")
print(f"Start: ${start_price:.2f}, End: ${end_price:.2f}")

# Check monthly returns
df["Return"] = df["Close"].pct_change()
monthly = df.resample("M")["Return"].sum() * 100
print(f"\nMonthly returns:")
for idx, ret in monthly.items():
    print(f"  {idx.strftime('%Y-%m')}: {ret:+.1f}%")

# Count up bars vs down bars
up_bars = (df["Close"] > df["Close"].shift(1)).sum()
down_bars = (df["Close"] < df["Close"].shift(1)).sum()
print(f"\nUp bars: {up_bars} ({up_bars/len(df)*100:.1f}%)")
print(f"Down bars: {down_bars} ({down_bars/len(df)*100:.1f}%)")

# Mean reversion strategy would:
# - LONG when price < VWAP - SD (expecting bounce back up)
# - SHORT when price > VWAP + SD (expecting drop back down)
# But if BTC is in a strong uptrend, shorts will keep losing!

print("\n" + "="*50)
print("This explains the poor performance!")
print("="*50)
print("Mean reversion expects price to return to VWAP.")
print("But in a strong uptrend, price keeps going ABOVE VWAP")
print("so shorts keep losing and longs don't fully reverse.")
