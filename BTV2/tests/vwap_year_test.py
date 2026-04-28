#!/usr/bin/env python3
"""
VWAP Testing - Find working year/market conditions
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from BTV2.strategies import run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR

STORAGE_ROOT = Path("G:/Candle Data")

def load_parquet(symbol, interval):
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        return df
    return None

print("Loading data...")
df_5m_full = load_parquet("BTCUSDT", "5m")
df_1m_full = load_parquet("BTCUSDT", "1m")
print(f"5m: {len(df_5m_full)} bars")

def run_test(df, params, test_name):
    print(f"{test_name}...", end=" ", flush=True)
    
    equity, trades = run_vwap_scalping(df, cutoff=0.10, **params)
    
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
    
    print(f"Trades={len(trades)}, WR={win_rate:.1f}%, Net={net_return:+.2f}%, PF={profit_factor:.2f}")
    
    return {
        'test_name': test_name,
        'trades': len(trades),
        'win_rate': win_rate,
        'total_return': metrics['total_return_pct'],
        'net_return': net_return,
        'profit_factor': profit_factor,
    }

results = []

# Best known config from previous reports
best_params = {
    'sd_threshold': 2.0,
    'entry_mode': 'bull_pullback',
    'use_session_filter': True,
    'use_trailing_stop': True,
    'trailing_atr': 1.2,
    'use_anchored_vwap': True,
    'use_htf_vwap': True,
    'use_htf_ema': True,
    'require_reversal_candle': True,
    'tp_mode': 'vwap',
    'adx_max': 25.0,
    'rsi_max': 45.0,
    'volume_mult': 1.5,
}

# Test each year
years = [('2020', '2020-01-01', '2021-01-01'),
         ('2021', '2021-01-01', '2022-01-01'),
         ('2022', '2022-01-01', '2023-01-01'),
         ('2023', '2023-01-01', '2024-01-01'),
         ('2024', '2024-01-01', '2025-01-01')]

for year, start, end in years:
    df_5m = df_5m_full[(df_5m_full.index >= start) & (df_5m_full.index < end)]
    df_1m = df_1m_full[(df_1m_full.index >= start) & (df_1m_full.index < end)]
    params = best_params.copy()
    params['df_exit'] = df_1m
    results.append(run_test(df_5m, params, f"{year} ({len(df_5m)} bars)"))

# Also test 2020-2021 combined (bull market)
df_5m = df_5m_full[(df_5m_full.index >= '2020-01-01') & (df_5m_full.index < '2022-01-01')]
df_1m = df_1m_full[(df_1m_full.index >= '2020-01-01') & (df_1m_full.index < '2022-01-01')]
params = best_params.copy()
params['df_exit'] = df_1m
results.append(run_test(df_5m, params, "2020-2021 (bull)"))

# Test 2023-2024 
df_5m = df_5m_full[(df_5m_full.index >= '2023-01-01') & (df_5m_full.index < '2025-01-01')]
df_1m = df_1m_full[(df_1m_full.index >= '2023-01-01') & (df_1m_full.index < '2025-01-01')]
params = best_params.copy()
params['df_exit'] = df_1m
results.append(run_test(df_5m, params, "2023-2024"))

# ============================================================
# Try different entry modes per year
# ============================================================
print("\n=== Different entry modes for 2020 (best year) ===")

df_5m = df_5m_full[(df_5m_full.index >= '2020-01-01') & (df_5m_full.index < '2021-01-01')]
df_1m = df_1m_full[(df_1m_full.index >= '2020-01-01') & (df_1m_full.index < '2021-01-01')]

for mode in ['bull_pullback', 'mean_reversion', 'cross', 'deviation', 'momentum']:
    params = best_params.copy()
    params['entry_mode'] = mode
    params['df_exit'] = df_1m
    results.append(run_test(df_5m, params, f"2020 {mode}"))

# ============================================================
# SUMMARY
# ============================================================
print("\n" + "="*70)
print("ALL RESULTS")
print("="*70)
print(f"\n{'Test':<40} {'Trades':>6} {'WR%':>5} {'Net%':>8} {'PF':>5}")
print("-" * 70)
for r in results:
    flag = " **" if r['net_return'] > 0 and r['trades'] >= 10 else ""
    print(f"{r['test_name']:<40} {r['trades']:>6} {r['win_rate']:>4.1f}% {r['net_return']:>+7.2f}% {r['profit_factor']:>4.2f}{flag}")

profitable = [r for r in results if r['net_return'] > 0 and r['trades'] >= 10]
if profitable:
    print(f"\n*** PROFITABLE: {len(profitable)} configurations ***")
    for r in sorted(profitable, key=lambda x: -x['net_return']):
        print(f"  {r['test_name']}: Net={r['net_return']:+.2f}%, Trades={r['trades']}, WR={r['win_rate']:.1f}%")
else:
    best = max(results, key=lambda x: x['net_return'])
    print(f"\nBest (but not profitable): {best['test_name']} (Net={best['net_return']:+.2f}%, Trades={best['trades']})")