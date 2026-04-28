#!/usr/bin/env python3
"""
Direct SD threshold sweep test - bypasses grid optimization to test specific values.
"""
import sys
from datetime import date
from pathlib import Path

import yfinance as yf
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    run_vwap_scalping, build_windows, compute_metrics,
    stitch_oos_equity,
)

def test_sd_threshold(ticker, start, end, cutoff, sd_threshold, atr_stop=7.0):
    """Test a specific SD threshold value."""
    df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    df.columns = df.columns.get_level_values(0)
    
    windows = build_windows(date.fromisoformat(start), date.fromisoformat(end))
    segments, all_trades = [], []
    
    for w in windows:
        df_train = df.loc[str(w["train_start"]):str(w["train_end"])]
        df_test  = df.loc[str(w["test_start"]):str(w["test_end"])]
        if len(df_train) < 50 or len(df_test) < 10:
            continue
        
        # Use FIXED parameters - no optimization
        params = {"sd_threshold": sd_threshold, "atr_stop": atr_stop}
        eq, trd = run_vwap_scalping(df_test, cutoff, **params)
        segments.append(eq)
        all_trades.extend(trd)
    
    oos_equity = stitch_oos_equity(segments)
    metrics = compute_metrics(oos_equity, all_trades)
    
    return metrics

# Test all SD thresholds
sd_values = [3.0, 2.75, 2.5, 2.25, 2.0, 1.75, 1.5, 1.25, 1.0, 0.75, 0.5]
ticker = "BTC-USD"
start = "2021-01-01"
end = "2025-01-01"
cutoff = 0.10

print(f"VWAP Scalping SD Threshold Sweep | {ticker} | {start} -> {end} | Cutoff: {cutoff}")
print("=" * 80)
print(f"{'SD Threshold':<14} {'Sharpe':<8} {'Return %':<10} {'Max DD %':<10} {'Trades':<8} {'Trades/Yr':<10}")
print("-" * 80)

for sd in sd_values:
    m = test_sd_threshold(ticker, start, end, cutoff, sd)
    trades_per_year = m['n_trades'] / 4  # 4 years
    print(f"{sd:<14.2f} {m['sharpe']:<8.3f} {m['total_return_pct']:<+10.1f} {m['max_dd_pct']:<10.1f} {m['n_trades']:<8} {trades_per_year:<10.1f}")

print("-" * 80)
print("Target: 50+ trades/year (200 total for 4 years)")
