#!/usr/bin/env python3
"""
VWAP SD Quick Test - Ultra minimal version to find best SD
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

# Use just 2024 data for maximum speed
df_5m = load_parquet("BTCUSDT", "5m")
df_5m = df_5m[(df_5m.index >= '2024-01-01') & (df_5m.index < '2025-01-01')]
print(f"Data: {len(df_5m)} bars (2024)")

SD_THRESHOLD = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]
ENTRY_MODES = ['bull_pullback', 'mean_reversion', 'cross']

results = []
start = datetime.now()

print("\n=== SD QUICK TEST ===")
for sd in SD_THRESHOLD:
    print(f"\nSD = {sd}")
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
            "atr_target": 4.0,
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
        
        r = {
            'sd': sd, 'entry': entry,
            'trades': len(trades), 'win_rate': win_rate,
            'profit_factor': profit_factor, 'net_return': net_return,
            'total_return': metrics['total_return_pct']
        }
        results.append(r)
        print(f"  {entry}: trades={len(trades)}, wr={win_rate:.1f}%, pf={profit_factor:.2f}, net={net_return:+.1f}%")

print(f"\n=== COMPLETE in {(datetime.now()-start).total_seconds():.0f}s ===")

# Summary by SD
print("\n=== BEST BY SD ===")
for sd in SD_THRESHOLD:
    sd_r = [x for x in results if x['sd'] == sd]
    if sd_r:
        best = max(sd_r, key=lambda x: x['net_return'])
        qual = len([x for x in sd_r if x['win_rate'] > 48 and x['net_return'] > 0 and x['trades'] >= 10])
        print(f"SD {sd:>4.1f}: best_net={best['net_return']:>+7.1f}%, best_wr={best['win_rate']:.1f}%, qual={qual}")

# Save results
with open("BTV2/results/vwap_sd_quick_test.json", 'w') as f:
    json.dump(results, f, indent=2)
print("\nSaved to BTV2/results/vwap_sd_quick_test.json")
