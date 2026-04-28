#!/usr/bin/env python3
"""
VWAP SD Super Quick Test - Using 3 months of data for speed
"""

import sys
from pathlib import Path
import pandas as pd
import json
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR

STORAGE_ROOT = Path("G:/Candle Data")

def load_parquet(symbol, interval):
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        return df
    return None

# Use 6 months for faster testing
df_5m = load_parquet("BTCUSDT", "5m")
df_5m = df_5m[(df_5m.index >= '2024-07-01') & (df_5m.index < '2025-01-01')]
print(f"Data: {len(df_5m)} bars (Jul-Dec 2024)")

# Extended SD range
SD_THRESHOLD = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]
ENTRY_MODES = ['momentum', 'bull_pullback', 'cross', 'mean_reversion']

results = []
start = datetime.now()

print("\n=== SD SWEEP (6 months data) ===")
for sd in SD_THRESHOLD:
    for entry in ENTRY_MODES:
        params = {
            "sd_threshold": sd,
            "entry_mode": entry,
            "stoch_oversold": 20,
            "stoch_overbought": 80,
            "adx_max": 25,
            "rsi_max": 50,
            "volume_mult": 2.0,
            "atr_stop": 1.0,
            "atr_target": 3.0,
            "pullback_bars": 3,
            "use_htf_vwap": True,
            "use_htf_ema": True,
            "htf_adx_max": 25,
            "df_exit": df_5m,
        }
        
        equity, trades = run_vwap_scalping(df_5m, cutoff=0.10, **params)
        bars_per_year = INTERVAL_BARS_PER_YEAR["5m"]
        metrics = compute_metrics(equity, trades, bars_per_year=bars_per_year)
        
        if trades:
            wins = sum(1 for t in trades if t > 0)
            win_rate = wins / len(trades) * 100
            gross_profit = sum(t for t in trades if t > 0)
            gross_loss = abs(sum(t for t in trades if t < 0))
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
            costs = len(trades) * 0.30
            net_return = metrics['total_return_pct'] - costs
        else:
            win_rate, profit_factor, costs, net_return = 0, 0, 0, 0
        
        results.append({
            'sd': sd, 'entry': entry,
            'trades': len(trades), 'win_rate': win_rate,
            'profit_factor': profit_factor, 'net_return': net_return,
            'total_return': metrics['total_return_pct']
        })

print(f"\n=== COMPLETE in {(datetime.now()-start).total_seconds():.0f}s ===")

# Analysis by SD
print("\n=== SD ANALYSIS ===")
print(f"{'SD':>6} {'Qual':>6} {'Best Net%':>12} {'Best WR%':>10} {'Best Trades':>12}")
print("-" * 55)

for sd in sorted(set(r['sd'] for r in results)):
    sd_r = [x for x in results if x['sd'] == sd]
    if sd_r:
        best = max(sd_r, key=lambda x: x['net_return'])
        qual = len([x for x in sd_r if x['win_rate'] > 48 and x['net_return'] > 0 and x['trades'] >= 10])
        print(f"{sd:>6.1f} {qual:>6} {best['net_return']:>+12.1f} {best['win_rate']:>10.1f} {best['trades']:>12}")

# All results sorted
results_sorted = sorted(results, key=lambda x: x['net_return'], reverse=True)

print("\n=== TOP 20 RESULTS ===")
print(f"{'#':>3} {'SD':>5} {'Entry':<16} {'Trds':>6} {'WR%':>6} {'PF':>6} {'Net%':>8}")
print("-" * 65)

for i, r in enumerate(results_sorted[:20], 1):
    print(f"{i:>3} {r['sd']:>5.1f} {r['entry']:<16} {r['trades']:>6} {r['win_rate']:>6.1f} {r['profit_factor']:>6.2f} {r['net_return']:>+8.1f}")

# Save
with open("BTV2/results/vwap_sd_6mo_sweep.json", 'w') as f:
    json.dump(results, f, indent=2)
print("\nSaved to BTV2/results/vwap_sd_6mo_sweep.json")
