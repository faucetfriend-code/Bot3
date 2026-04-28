#!/usr/env python3
"""
Test improved VWAP with higher SD values to find profitable zone.
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
print()

print("=" * 100)
print("TESTING HIGHER SD VALUES WITH IMPROVEMENTS")
print("=" * 100)

results = []
for sd in [5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 10.0, 12.0]:
    for n1 in [True, False]:
        eq, trades = run_vwap_scalping(
            df, 
            cutoff=0.10, 
            sd_threshold=sd,
            atr_stop=7.0,
            initial_sl_atr=1.0,
            trailing_atr=2.0,
            use_n1_entry=n1,
            use_macd_turn=True,
        )
        metrics = compute_metrics(eq, trades)
        
        if trades:
            wins = sum(1 for t in trades if t > 0)
            win_rate = wins / len(trades) * 100
            gross_profit = sum(t for t in trades if t > 0)
            gross_loss = abs(sum(t for t in trades if t < 0))
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        else:
            win_rate, profit_factor = 0, 0
        
        gross = metrics['total_return_pct']
        costs = len(trades) * 0.30
        net = gross - costs
        
        label = f"SD={sd}, N1={n1}"
        print(f"{label}: Return={gross:+.1f}%, WR={win_rate:.0f}%, PF={profit_factor:.2f}, Trades={len(trades)}, Net={net:+.1f}%")
        
        results.append((sd, n1, metrics, len(trades), win_rate, profit_factor, gross, costs, net))

# Find best
print()
print("=" * 100)
print("BEST RESULTS")
print("=" * 100)

print("\n>> POSITIVE GROSS RETURN:")
for r in sorted([x for x in results if x[6] > 0], key=lambda x: x[6], reverse=True):
    print(f"  SD={r[0]}, N1={r[1]}: Return={r[6]:+.1f}%, WR={r[4]:.0f}%, PF={r[5]:.2f}, Trades={r[3]}")

print("\n>> PROFIT FACTOR > 1.0:")
for r in sorted([x for x in results if x[5] > 1.0], key=lambda x: x[5], reverse=True):
    print(f"  SD={r[0]}, N1={r[1]}: PF={r[5]:.2f}, WR={r[4]:.0f}%, Return={r[6]:+.1f}%, Trades={r[3]}")

print("\n>> BEST NET RETURN (after costs):")
for r in sorted(results, key=lambda x: x[8], reverse=True)[:5]:
    print(f"  SD={r[0]}, N1={r[1]}: Net={r[8]:+.1f}% (Gross={r[6]:+.1f}% - Costs={r[7]:.1f}%), WR={r[4]:.0f}%, PF={r[5]:.2f}")
