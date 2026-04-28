#!/usr/bin/env python3
"""
Full metrics test for VWAP Scalping on 5m data.
Captures: Return %, Sharpe, Max DD, Win Rate, Profit Factor, Trades.
"""
import sys
from pathlib import Path

import yfinance as yf
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import run_vwap_scalping, compute_metrics

# Download max available 5m data (60 days)
print("Downloading 60 days of 5m BTC-USD data...")
df = yf.download("BTC-USD", period="60d", interval="5m",
                 auto_adjust=True, progress=False)
df.columns = df.columns.get_level_values(0)
print(f"Loaded {len(df)} bars ({len(df)/288:.0f} trading days)")
print(f"Date range: {df.index[0]} to {df.index[-1]}")
print()

# Test different SD thresholds
sd_values = [3.0, 2.0, 1.0, 0.5]

print("=" * 90)
print(f"{'SD':<6} {'Sharpe':<8} {'Return %':<12} {'Max DD %':<10} {'Win Rate %':<12} {'PF':<8} {'Trades':<8} {'Trades/Day':<12}")
print("-" * 90)

results = []
for sd in sd_values:
    eq, trades = run_vwap_scalping(df, cutoff=0.10, sd_threshold=sd, atr_stop=7.0)
    metrics = compute_metrics(eq, trades)
    
    # Calculate win rate and profit factor
    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    else:
        win_rate = 0
        profit_factor = 0
    
    trades_per_day = len(trades) / (len(df) / 288)
    annual_trades = trades_per_day * 365
    
    print(f"{sd:<6.1f} {metrics['sharpe']:<8.3f} {metrics['total_return_pct']:<+12.1f} {metrics['max_dd_pct']:<10.1f} {win_rate:<12.1f} {profit_factor:<8.2f} {len(trades):<8} {trades_per_day:<12.1f}")
    
    results.append({
        'sd': sd,
        'sharpe': metrics['sharpe'],
        'return_pct': metrics['total_return_pct'],
        'max_dd_pct': metrics['max_dd_pct'],
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'trades': len(trades),
        'trades_per_day': trades_per_day,
        'annual_trades': annual_trades,
    })

print("-" * 90)
print()

# Summary analysis
print("ANALYSIS:")
print("-" * 50)

# Find best by different criteria
best_sharpe = max(results, key=lambda x: x['sharpe'])
best_return = max(results, key=lambda x: x['return_pct'])
best_pf = max(results, key=lambda x: x['profit_factor'])
most_trades = max(results, key=lambda x: x['trades'])

print(f"Best Sharpe:      SD={best_sharpe['sd']} ({best_sharpe['sharpe']:.3f})")
print(f"Best Return:     SD={best_return['sd']} ({best_return['return_pct']:+.1f}%)")
print(f"Best Profit F:   SD={best_pf['sd']} ({best_pf['profit_factor']:.2f})")
print(f"Most Trades:     SD={most_trades['sd']} ({most_trades['trades']} trades, {most_trades['annual_trades']:.0f}/year)")
print()

# Check if profitable
profitable = [r for r in results if r['return_pct'] > 0]
if profitable:
    print(f"All SD values profitable: {len(profitable)}/{len(results)}")
else:
    print("WARNING: No SD values profitable!")
print()

# Cost analysis (0.15% per side = 0.30% round trip)
print("COST ANALYSIS (0.30% round-trip):")
print("-" * 50)
for r in results:
    gross_return = r['return_pct']
    num_trades = r['trades']
    total_costs = num_trades * 0.30  # 0.30% per trade
    net_return = gross_return - total_costs
    print(f"SD={r['sd']}: Gross {gross_return:+.1f}% - Costs {total_costs:.1f}% = Net {net_return:+.1f}% ({num_trades} trades)")
