#!/usr/bin/env python3
"""
Test VWAP on 1h data for the full period where we know it worked (2021-2025).
"""
import sys
from pathlib import Path
from datetime import date

import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import run_vwap_scalping, compute_metrics

# Download 1h data for 2021-2025
print("Downloading 1h data for 2021-2025...")
df = yf.download("BTC-USD", start="2021-01-01", end="2025-01-01", interval="1h",
                 auto_adjust=True, progress=False)
df.columns = df.columns.get_level_values(0)
print(f"Loaded {len(df)} bars")
print()

print("=" * 90)
print(f"{'SD':<6} {'Sharpe':<8} {'Return %':<12} {'Max DD %':<10} {'Win Rate %':<12} {'PF':<8} {'Trades':<8}")
print("-" * 90)

for sd in [3.0, 2.75, 2.0, 1.5, 1.0, 0.75, 0.5]:
    eq, trades = run_vwap_scalping(df, cutoff=0.10, sd_threshold=sd, atr_stop=7.0)
    metrics = compute_metrics(eq, trades)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    else:
        win_rate = 0
        profit_factor = 0
    
    print(f"{sd:<6.1f} {metrics['sharpe']:<8.3f} {metrics['total_return_pct']:<+12.1f} {metrics['max_dd_pct']:<10.1f} {win_rate:<12.1f} {profit_factor:<8.2f} {len(trades):<8}")

print("-" * 90)
print()

# Cost analysis
print("COST ANALYSIS (0.30% round-trip):")
print("-" * 50)
for sd in [3.0, 2.0, 1.0]:
    eq, trades = run_vwap_scalping(df, cutoff=0.10, sd_threshold=sd, atr_stop=7.0)
    metrics = compute_metrics(eq, trades)
    gross_return = metrics['total_return_pct']
    num_trades = len(trades)
    total_costs = num_trades * 0.30
    net_return = gross_return - total_costs
    print(f"SD={sd}: Gross {gross_return:+.1f}% - Costs {total_costs:.1f}% = Net {net_return:+.1f}% ({num_trades} trades)")
