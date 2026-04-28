#!/usr/bin/env python3
"""
Compare original vs improved VWAP fairly.
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
print("COMPARISON: Original vs Improved (MACD Turn OFF)")
print("=" * 100)

results = []

for sd in [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0]:
    # Original logic (N1=False, Turn=False)
    eq1, trades1 = run_vwap_scalping(
        df, cutoff=0.10, sd_threshold=sd, atr_stop=7.0,
        initial_sl_atr=7.0,  # Original uses atr_stop for initial SL
        trailing_atr=2.0,
        use_n1_entry=False,
        use_macd_turn=False,
    )
    
    # Improved logic (N1=True, Turn=False)
    eq2, trades2 = run_vwap_scalping(
        df, cutoff=0.10, sd_threshold=sd, atr_stop=7.0,
        initial_sl_atr=1.0,
        trailing_atr=2.0,
        use_n1_entry=True,
        use_macd_turn=False,
    )
    
    for name, eq, trades in [("Original", eq1, trades1), ("Improved", eq2, trades2)]:
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
        
        print(f"{name} SD={sd}: Return={gross:+.1f}%, WR={win_rate:.0f}%, PF={profit_factor:.2f}, Trades={len(trades)}, Net={net:+.1f}%")
        results.append((name, sd, metrics, len(trades), win_rate, profit_factor, gross, costs, net))

# Summary
print()
print("=" * 100)
print("SUMMARY")
print("=" * 100)

print("\n>> Best Original:")
for r in sorted([x for x in results if x[0] == "Original"], key=lambda x: x[6], reverse=True)[:3]:
    print(f"  SD={r[1]}: Return={r[6]:+.1f}%, WR={r[4]:.0f}%, PF={r[5]:.2f}, Trades={r[3]}")

print("\n>> Best Improved:")
for r in sorted([x for x in results if x[0] == "Improved"], key=lambda x: x[6], reverse=True)[:3]:
    print(f"  SD={r[1]}: Return={r[6]:+.1f}%, WR={r[4]:.0f}%, PF={r[5]:.2f}, Trades={r[3]}")
