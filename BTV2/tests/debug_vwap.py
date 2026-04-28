#!/usr/bin/env python3
"""
Debug: Simple buy-hold test to verify backtest logic
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

STORAGE_ROOT = Path("G:/Candle Data")

# Load data
print("Loading data...")
df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
df.columns = [c.capitalize() for c in df.columns]
df = df[(df.index >= "2023-01-01") & (df.index < "2025-01-01")]
print(f"Loaded {len(df):,} bars")

prices = df["Close"].values.astype(float)
n = len(prices)

# Simple buy and hold - buy at first close, sell at last close
buy_hold_return = (prices[-1] / prices[0] - 1) * 100
print(f"\nBuy-hold return (2023-2024): {buy_hold_return:+.2f}%")

# Simulate bar-by-bar equity (like the strategy does)
equity = np.ones(n, dtype=float)
for i in range(1, n):
    equity[i] = equity[i-1] * prices[i] / prices[i-1]

final_equity = equity[-1]
simulated_return = (final_equity - 1) * 100
print(f"Simulated bar-by-bar return: {simulated_return:+.2f}%")

# Now test with cost
equity_with_cost = np.ones(n, dtype=float)
cost = 0.0015  # 0.15% per side
for i in range(1, n):
    # Buy at open of i, sell at close of i (actually just hold through bar)
    equity_with_cost[i] = equity_with_cost[i-1] * prices[i] / prices[i-1]

# Add entry cost at start and exit cost at end
equity_with_cost[0] *= (1 - cost)  # buy
final_with_cost = equity_with_cost[-1] * (1 - cost)  # sell
return_with_cost = (final_with_cost - 1) * 100
print(f"With entry/exit costs: {return_with_cost:+.2f}%")

# Test what the strategy does: apply bar returns while in position
print("\n" + "="*50)
print("Now testing strategy logic with a simple long")
print("="*50)

equity = np.ones(n, dtype=float)
curr_equity = 1.0
in_pos = False
entry_price = 0.0

# Enter at bar 100, exit at bar 200
entry_bar = 100
exit_bar = 200

for i in range(1, n):
    if not in_pos and i == entry_bar:
        # Enter at open (use prev close as approximation)
        curr_equity *= (1 - cost)  # entry cost
        entry_price = prices[i-1]  # enter at previous close
        in_pos = True
        print(f"Enter long at bar {i}, price {entry_price:.2f}")
    
    if in_pos:
        # Apply bar return
        curr_equity *= prices[i] / prices[i-1]
        
        if i == exit_bar:
            curr_equity *= (1 - cost)  # exit cost
            print(f"Exit long at bar {i}, price {prices[i]:2f}")
            print(f"Return: {(curr_equity - 1) * 100:.2f}%")
            print(f"Actual price return: {(prices[i] / entry_price - 1) * 100:.2f}%")
            break

# What the strategy would do incorrectly
print("\n" + "="*50)
print("What strategy does (bug):")
print("="*50)

equity_bug = np.ones(n, dtype=float)
curr_equity_bug = 1.0

for i in range(1, n):
    # Strategy applies return FIRST, then checks entry
    curr_equity_bug *= prices[i] / prices[i-1]  # THIS IS THE BUG!
    
    if not in_pos and i == entry_bar:
        curr_equity_bug *= (1 - cost)  # entry cost
        entry_price = prices[i-1]
        in_pos = True
        print(f"Enter at bar {i}, equity is {curr_equity_bug:.6f}")
        # But we already got returns from bar 0->1, 1->2, ..., 99->100!
    
    if in_pos and i == exit_bar:
        curr_equity_bug *= (1 - cost)
        print(f"Exit at bar {i}, equity is {curr_equity_bug:.6f}")
        print(f"Return: {(curr_equity_bug - 1) * 100:.2f}%")
        break
