#!/usr/bin/env python3
"""
VWAP 5m comprehensive test with SD=2.5.
Test various ATR stops and cutoffs to find profitable configuration.
"""
import sys
from pathlib import Path
import pandas as pd

STORAGE_ROOT = Path("G:/Candle Data")

def load_parquet(symbol, interval):
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return None

sys.path.insert(0, str(Path(__file__).parent.parent))
from strategies import run_vwap_scalping, compute_metrics

df = load_parquet("BTCUSDT", "5m")
df.columns = [c.capitalize() for c in df.columns]
df = df[(df.index >= '2023-01-01') & (df.index < '2025-01-01')]
print(f"Loaded {len(df)} bars (2023-2024)")

SD = 2.5

# Test 1: Various ATR stops
print()
print("=" * 100)
print(f"TEST 1: SD={SD}, Various ATR Stops (Cutoff=0.10)")
print("=" * 100)
print(f"{'ATR':<8} {'Cutoff':<8} {'Sharpe':<8} {'Return %':<12} {'Max DD %':<10} {'Win Rate':<10} {'PF':<6} {'Trades':<8}")
print("-" * 100)

results = []
for atr in [5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0, 20.0]:
    eq, trades = run_vwap_scalping(df, cutoff=0.10, sd_threshold=SD, atr_stop=atr)
    metrics = compute_metrics(eq, trades)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    else:
        win_rate, profit_factor = 0, 0
    
    print(f"{atr:<8.1f} {'0.10':<8} {metrics['sharpe']:<8.3f} {metrics['total_return_pct']:<+12.1f} {metrics['max_dd_pct']:<10.1f} {win_rate:<10.1f} {profit_factor:<6.2f} {len(trades):<8}")
    results.append(('ATR', atr, 0.10, metrics, len(trades), win_rate, profit_factor))

# Test 2: Different cutoffs
print()
print("=" * 100)
print(f"TEST 2: SD={SD}, Various Cutoffs (ATR=7.0)")
print("=" * 100)
print(f"{'ATR':<8} {'Cutoff':<8} {'Sharpe':<8} {'Return %':<12} {'Max DD %':<10} {'Win Rate':<10} {'PF':<6} {'Trades':<8}")
print("-" * 100)

for cutoff in [0.03, 0.05, 0.07, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30]:
    eq, trades = run_vwap_scalping(df, cutoff=cutoff, sd_threshold=SD, atr_stop=7.0)
    metrics = compute_metrics(eq, trades)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    else:
        win_rate, profit_factor = 0, 0
    
    print(f"{'7.0':<8} {cutoff:<8.2f} {metrics['sharpe']:<8.3f} {metrics['total_return_pct']:<+12.1f} {metrics['max_dd_pct']:<10.1f} {win_rate:<10.1f} {profit_factor:<6.2f} {len(trades):<8}")
    results.append(('CUTOFF', 7.0, cutoff, metrics, len(trades), win_rate, profit_factor))

# Test 3: Best combinations
print()
print("=" * 100)
print("TEST 3: Best Combinations")
print("=" * 100)
print(f"{'ATR':<8} {'Cutoff':<8} {'Sharpe':<8} {'Return %':<12} {'Max DD %':<10} {'Win Rate':<10} {'PF':<6} {'Trades':<8}")
print("-" * 100)

combos = [
    (5.0, 0.05),
    (5.0, 0.07),
    (5.0, 0.10),
    (6.0, 0.05),
    (6.0, 0.07),
    (7.0, 0.05),
    (7.0, 0.07),
    (8.0, 0.05),
    (10.0, 0.05),
    (12.0, 0.05),
]

for atr, cutoff in combos:
    eq, trades = run_vwap_scalping(df, cutoff=cutoff, sd_threshold=SD, atr_stop=atr)
    metrics = compute_metrics(eq, trades)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    else:
        win_rate, profit_factor = 0, 0
    
    print(f"{atr:<8.1f} {cutoff:<8.2f} {metrics['sharpe']:<8.3f} {metrics['total_return_pct']:<+12.1f} {metrics['max_dd_pct']:<10.1f} {win_rate:<10.1f} {profit_factor:<6.2f} {len(trades):<8}")
    results.append(('COMBO', atr, cutoff, metrics, len(trades), win_rate, profit_factor))

# Find best
print()
print("=" * 100)
print("ANALYSIS")
print("=" * 100)

# Filter for positive return
positive = [r for r in results if r[3]['total_return_pct'] > 0]
if positive:
    print("\n>> POSITIVE RETURN:")
    for r in sorted(positive, key=lambda x: x[3]['total_return_pct'], reverse=True)[:10]:
        print(f"  ATR={r[1]}, Cutoff={r[2]}: Return={r[3]['total_return_pct']:+.1f}%, WR={r[5]:.0f}%, PF={r[6]:.2f}, Trades={r[4]}")

# Filter for PF > 1
good_pf = [r for r in results if r[6] > 1.0]
if good_pf:
    print("\n>> PROFIT FACTOR > 1.0:")
    for r in sorted(good_pf, key=lambda x: x[6], reverse=True)[:10]:
        print(f"  ATR={r[1]}, Cutoff={r[2]}: PF={r[6]:.2f}, WR={r[5]:.0f}%, Return={r[3]['total_return_pct']:+.1f}%, Trades={r[4]}")

# Filter for WR > 50%
good_wr = [r for r in results if r[5] > 50]
if good_wr:
    print("\n>> WIN RATE > 50%:")
    for r in sorted(good_wr, key=lambda x: x[5], reverse=True)[:10]:
        print(f"  ATR={r[1]}, Cutoff={r[2]}: WR={r[5]:.0f}%, PF={r[6]:.2f}, Return={r[3]['total_return_pct']:+.1f}%, Trades={r[4]}")

# Top by return
print("\n>> TOP 10 BY RETURN:")
for r in sorted(results, key=lambda x: x[3]['total_return_pct'], reverse=True)[:10]:
    print(f"  ATR={r[1]}, Cutoff={r[2]}: Return={r[3]['total_return_pct']:+.1f}%, Sharpe={r[3]['sharpe']:.3f}, WR={r[5]:.0f}%, PF={r[6]:.2f}, Trades={r[4]}")

# Cost analysis
print()
print("=" * 100)
print("COST ANALYSIS")
print("=" * 100)
for r in sorted(results, key=lambda x: x[3]['total_return_pct'], reverse=True)[:5]:
    gross = r[3]['total_return_pct']
    trades = r[4]
    costs = trades * 0.30
    net = gross - costs
    print(f"ATR={r[1]}, Cutoff={r[2]}: Gross {gross:+.1f}% - Costs {costs:.1f}% = Net {net:+.1f}% ({trades} trades)")
