#!/usr/bin/env python3
"""
Direct VWAP test on Binance 15m and 1h data.
"""
import sys
from pathlib import Path
import pandas as pd

# Use G: drive path
STORAGE_ROOT = Path("G:/Candle Data")

def load_parquet(symbol, interval):
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        return df
    return None

sys.path.insert(0, str(Path(__file__).parent.parent))
from strategies import run_vwap_scalping, compute_metrics

# Test 15m
print("=" * 80)
print("VWAP SCALPING - 15m TIMEFRAME (Binance Data, 2024)")
print("=" * 80)

df_15m = load_parquet("BTCUSDT", "15m")
if df_15m is not None and not df_15m.empty:
    df_15m.columns = [c.capitalize() for c in df_15m.columns]
    df_15m = df_15m[(df_15m.index >= '2024-01-01') & (df_15m.index < '2025-01-01')]
    print(f"Loaded {len(df_15m)} bars (2024)")
else:
    print("ERROR: No 15m data!")
    df_15m = None

if df_15m is not None:
    print()
    print(f"{'SD':<6} {'Sharpe':<8} {'Return %':<12} {'Max DD %':<10} {'Win Rate %':<12} {'PF':<8} {'Trades':<8}")
    print("-" * 80)

    results_15m = []
    for sd in [3.0, 2.0, 1.0, 0.5]:
        eq, trades = run_vwap_scalping(df_15m, cutoff=0.10, sd_threshold=sd, atr_stop=7.0)
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
        results_15m.append((sd, metrics, len(trades), win_rate, profit_factor))

    print()
    print("COST ANALYSIS (0.30% round-trip):")
    print("-" * 50)
    for sd, m, trades, wr, pf in results_15m:
        gross = m['total_return_pct']
        costs = trades * 0.30
        net = gross - costs
        print(f"  SD={sd}: Gross {gross:+.1f}% - Costs {costs:.1f}% = Net {net:+.1f}% ({trades} trades, {wr:.0f}% WR, PF={pf:.2f})")

# Test 1h
print()
print("=" * 80)
print("VWAP SCALPING - 1h TIMEFRAME (Binance Data, 2024)")
print("=" * 80)

# First try loading from G:, then check other paths
df_1h = load_parquet("BTCUSDT", "1h")
if df_1h is None:
    # Try local path
    for local_path in ["Candle Data", "../Candle Data", "../../Candle Data"]:
        p = Path(local_path)
        if (p / "BTCUSDT_1h.parquet").exists():
            df_1h = pd.read_parquet(p / "BTCUSDT_1h.parquet")
            print(f"Loaded from {p}")
            break

if df_1h is not None and not df_1h.empty:
    df_1h.columns = [c.capitalize() for c in df_1h.columns]
    df_1h = df_1h[(df_1h.index >= '2024-01-01') & (df_1h.index < '2025-01-01')]
    print(f"Loaded {len(df_1h)} bars (2024)")
    
    print()
    print(f"{'SD':<6} {'Sharpe':<8} {'Return %':<12} {'Max DD %':<10} {'Win Rate %':<12} {'PF':<8} {'Trades':<8}")
    print("-" * 80)

    results_1h = []
    for sd in [3.0, 2.0, 1.0, 0.5]:
        eq, trades = run_vwap_scalping(df_1h, cutoff=0.10, sd_threshold=sd, atr_stop=7.0)
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
        results_1h.append((sd, metrics, len(trades), win_rate, profit_factor))

    print()
    print("COST ANALYSIS (0.30% round-trip):")
    print("-" * 50)
    for sd, m, trades, wr, pf in results_1h:
        gross = m['total_return_pct']
        costs = trades * 0.30
        net = gross - costs
        print(f"  SD={sd}: Gross {gross:+.1f}% - Costs {costs:.1f}% = Net {net:+.1f}% ({trades} trades, {wr:.0f}% WR, PF={pf:.2f})")
else:
    print("ERROR: No 1h data available!")
