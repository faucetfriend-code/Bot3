#!/usr/bin/env python3
"""
Fine-tune SD threshold to find balance between profitability and trade count.
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

print("=" * 100)
print("FINE-TUNING SD FOR BALANCE (ATR=7.0, Cutoff=0.10)")
print("=" * 100)
print(f"{'SD':<6} {'Sharpe':<8} {'Return %':<12} {'Max DD %':<10} {'Win Rate':<10} {'PF':<6} {'Trades':<8} {'Annual Trades':<14}")
print("-" * 100)

results = []
for sd in [5.5, 5.75, 6.0, 6.25, 6.5, 6.75, 7.0, 7.5, 8.0]:
    eq, trades = run_vwap_scalping(df, cutoff=0.10, sd_threshold=sd, atr_stop=7.0)
    metrics = compute_metrics(eq, trades)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        annual_trades = len(trades) / 2  # 2 years
    else:
        win_rate, profit_factor, annual_trades = 0, 0, 0
    
    print(f"{sd:<6.2f} {metrics['sharpe']:<8.3f} {metrics['total_return_pct']:<+12.1f} {metrics['max_dd_pct']:<10.1f} {win_rate:<10.1f} {profit_factor:<6.2f} {len(trades):<8} {annual_trades:<14.1f}")
    results.append((sd, metrics, len(trades), win_rate, profit_factor, annual_trades))

print()
print("ANALYSIS:")
print("-" * 80)

# Find configurations meeting minimum criteria
print("\n>> Balanced (Trade Count + Profitability):")
for sd, m, trades, wr, pf, annual in results:
    if trades >= 10 and pf > 1.0:
        print(f"  SD={sd}: {trades} trades ({annual}/yr), WR={wr:.0f}%, PF={pf:.2f}, Return={m['total_return_pct']:+.1f}%")

print("\n>> Most trades with PF > 1:")
for sd, m, trades, wr, pf, annual in sorted(results, key=lambda x: x[2], reverse=True):
    if pf > 1.0:
        print(f"  SD={sd}: {trades} trades, WR={wr:.0f}%, PF={pf:.2f}, Return={m['total_return_pct']:+.1f}%")
        break

print("\n>> Best return with 20+ trades:")
for sd, m, trades, wr, pf, annual in sorted(results, key=lambda x: x[1]['total_return_pct'], reverse=True):
    if trades >= 20:
        print(f"  SD={sd}: {trades} trades, WR={wr:.0f}%, PF={pf:.2f}, Return={m['total_return_pct']:+.1f}%")
        break
