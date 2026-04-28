#!/usr/bin/env python3
"""Direct walk-forward test on 5m data."""
import sys
from datetime import date
from pathlib import Path

import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import run_vwap_scalping, build_windows, compute_metrics, stitch_oos_equity

# Download max available 5m data (60 days)
df = yf.download("BTC-USD", start="2026-01-11", end="2026-03-12", interval="5m",
                 auto_adjust=True, progress=False)
df.columns = df.columns.get_level_values(0)
print(f"Loaded {len(df)} 5m bars")

# Build walk-forward windows (1 month train, 1 month test)
windows = build_windows(date(2026, 1, 11), date(2026, 3, 12), train_months=1, test_months=1)
print(f"Windows: {len(windows)}")
for w in windows:
    print(f"  Train: {w['train_start']} -> {w['train_end']}, Test: {w['test_start']} -> {w['test_end']}")

# Run walk-forward with fixed params
segments, all_trades = [], []
for w in windows:
    df_train = df.loc[str(w["train_start"]):str(w["train_end"])]
    df_test  = df.loc[str(w["test_start"]):str(w["test_end"])]
    print(f"  Train: {len(df_train)} bars, Test: {len(df_test)} bars")
    
    if len(df_train) < 100 or len(df_test) < 50:
        continue
    
    # Use fixed params (no optimization on training)
    eq, trd = run_vwap_scalping(df_test, cutoff=0.10, sd_threshold=2.0, atr_stop=7.0)
    print(f"    Trades: {len(trd)}")
    segments.append(eq)
    all_trades.extend(trd)

if segments:
    oos_equity = stitch_oos_equity(segments)
    metrics = compute_metrics(oos_equity, all_trades)
    print(f"\nOOS Results:")
    print(f"  Sharpe: {metrics['sharpe']:.3f}")
    print(f"  Return: {metrics['total_return_pct']:.1f}%")
    print(f"  Max DD: {metrics['max_dd_pct']:.1f}%")
    print(f"  Trades: {metrics['n_trades']}")
else:
    print("\nNo segments generated!")
