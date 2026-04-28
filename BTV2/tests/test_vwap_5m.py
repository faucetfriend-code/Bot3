#!/usr/bin/env python3
"""Quick test of VWAP on 5m data."""
import sys
from pathlib import Path

import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import run_vwap_scalping

# Download 30 days of 5m data
df = yf.download("BTC-USD", start="2026-02-10", end="2026-03-12", interval="5m",
                 auto_adjust=True, progress=False)
df.columns = df.columns.get_level_values(0)
print(f"Loaded {len(df)} bars")

# Test with different SD thresholds
for sd in [3.0, 2.0, 1.0, 0.5]:
    eq, trades = run_vwap_scalping(df, cutoff=0.10, sd_threshold=sd, atr_stop=7.0)
    print(f"SD={sd}: {len(trades)} trades")
