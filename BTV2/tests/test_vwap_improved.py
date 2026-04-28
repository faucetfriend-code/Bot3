#!/usr/bin/env python3
"""
Test improved VWAP Scalping with N+1 entry, MACD turning, and tighter SL.
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
print("IMPROVED VWAP SCALPING - SD=2.5 with N+1 Entry + MACD Turn + Tighter SL")
print("=" * 100)

# Test configurations
configs = [
    # (sd, atr_stop, initial_sl, trailing, n1_entry, macd_turn)
    (2.5, 7.0, 1.0, 2.0, True, True),   # Full improvements
    (2.5, 7.0, 1.0, 2.0, True, False),  # N+1 only
    (2.5, 7.0, 1.0, 2.0, False, True),  # MACD turn only
    (2.5, 7.0, 1.0, 2.0, False, False),  # Original logic
    (2.5, 7.0, 0.5, 1.5, True, True),   # Very tight SL
    (2.5, 7.0, 1.5, 3.0, True, True),   # Wider SL
    (3.0, 7.0, 1.0, 2.0, True, True),   # SD=3.0 with improvements
    (4.0, 7.0, 1.0, 2.0, True, True),   # SD=4.0 with improvements
    (5.0, 7.0, 1.0, 2.0, True, True),   # SD=5.0 with improvements
]

results = []

for sd, atr, init_sl, trail, n1, turn in configs:
    eq, trades = run_vwap_scalping(
        df, 
        cutoff=0.10, 
        sd_threshold=sd,
        atr_stop=atr,
        initial_sl_atr=init_sl,
        trailing_atr=trail,
        use_n1_entry=n1,
        use_macd_turn=turn,
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
    
    label = f"SD={sd}, ATR={atr}, SL={init_sl}, Trail={trail}, N1={n1}, Turn={turn}"
    print(f"\n{label}")
    print(f"  Sharpe: {metrics['sharpe']:.3f}, Return: {metrics['total_return_pct']:+.1f}%, MaxDD: {metrics['max_dd_pct']:.1f}%")
    print(f"  Win Rate: {win_rate:.1f}%, PF: {profit_factor:.2f}, Trades: {len(trades)}")
    
    # Cost analysis
    gross = metrics['total_return_pct']
    costs = len(trades) * 0.30
    net = gross - costs
    print(f"  Costs: {costs:.1f}%, Net Return: {net:+.1f}%")
    
    results.append((sd, atr, init_sl, trail, n1, turn, metrics, len(trades), win_rate, profit_factor, gross, costs, net))

# Find best
print()
print("=" * 100)
print("ANALYSIS")
print("=" * 100)

# Positive return
positive = [r for r in results if r[10] > 0]
if positive:
    print("\n>> POSITIVE GROSS RETURN:")
    for r in sorted(positive, key=lambda x: x[10], reverse=True):
        print(f"  SD={r[0]}, ATR={r[1]}, N1={r[4]}, Turn={r[5]}: Return={r[10]:+.1f}%, WR={r[8]:.0f}%, PF={r[9]:.2f}, Trades={r[7]}")

# Good win rate
good_wr = [r for r in results if r[8] > 50]
if good_wr:
    print("\n>> WIN RATE > 50%:")
    for r in sorted(good_wr, key=lambda x: x[8], reverse=True):
        print(f"  SD={r[0]}, ATR={r[1]}, N1={r[4]}, Turn={r[5]}: WR={r[8]:.0f}%, Return={r[10]:+.1f}%, PF={r[9]:.2f}")

# Good PF
good_pf = [r for r in results if r[9] > 1.0]
if good_pf:
    print("\n>> PROFIT FACTOR > 1.0:")
    for r in sorted(good_pf, key=lambda x: x[9], reverse=True):
        print(f"  SD={r[0]}, ATR={r[1]}, N1={r[4]}, Turn={r[5]}: PF={r[9]:.2f}, WR={r[8]:.0f}%, Return={r[10]:+.1f}%")

# Best net return
print("\n>> TOP 5 BY NET RETURN (after costs):")
for r in sorted(results, key=lambda x: x[12], reverse=True)[:5]:
    print(f"  SD={r[0]}, N1={r[4]}, Turn={r[5]}: Net={r[12]:+.1f}% (Gross={r[10]:+.1f}% - Costs={r[11]:.1f}%), WR={r[8]:.0f}%, PF={r[9]:.2f}, Trades={r[7]}")
