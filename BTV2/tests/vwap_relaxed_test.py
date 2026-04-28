#!/usr/bin/env python3
"""
VWAP Fix Testing - Relaxed filters to find profitable config
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
df_5m = load_parquet("BTCUSDT", "5m")
df_5m = df_5m[(df_5m.index >= '2024-01-01') & (df_5m.index < '2025-01-01')]
print(f"5m: {len(df_5m)} bars")

df_1m = load_parquet("BTCUSDT", "1m")
df_1m = df_1m[(df_1m.index >= '2024-01-01') & (df_1m.index < '2025-01-01')]
print(f"1m: {len(df_1m)} bars")

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

# ============================================================
# TEST: Relaxed filters, various modes
# ============================================================

# Relaxed base params - no session filter, no reversal candle, looser ADX/RSI
relaxed_base = {
    'entry_mode': 'cross',
    'use_session_filter': False,  # No session filter
    'use_trailing_stop': False,   # No trailing
    'use_anchored_vwap': True,
    'use_htf_vwap': False,        # No HTF filter
    'use_htf_ema': False,
    'require_reversal_candle': False,  # No reversal candle
    'tp_mode': 'atr',
    'adx_max': 40.0,              # Much looser
    'rsi_max': 60.0,              # Much looser
    'volume_mult': 1.0,           # No volume filter
    'df_exit': df_1m
}

for rr in [(0.3, 0.3), (0.3, 0.5), (0.5, 0.5), (0.5, 0.75), (1.0, 1.0)]:
    p = relaxed_base.copy()
    p['atr_stop'] = rr[0]
    p['atr_target'] = rr[1]
    results.append(run_test(df_5m, p, f"cross relaxed R:R={rr[0]}:{rr[1]}"))

# Test mean_reversion with relaxed filters
mr_base = relaxed_base.copy()
mr_base['entry_mode'] = 'mean_reversion'
mr_base['sd_threshold'] = 2.0

for rr in [(0.3, 0.3), (0.3, 0.5), (0.5, 0.5)]:
    p = mr_base.copy()
    p['atr_stop'] = rr[0]
    p['atr_target'] = rr[1]
    results.append(run_test(df_5m, p, f"mean_rev relaxed R:R={rr[0]}:{rr[1]}"))

# Test momentum mode
mom_base = relaxed_base.copy()
mom_base['entry_mode'] = 'momentum'
mom_base['sd_threshold'] = 2.0
mom_base['momentum_bars'] = 2

for rr in [(0.3, 0.3), (0.5, 0.5)]:
    p = mom_base.copy()
    p['atr_stop'] = rr[0]
    p['atr_target'] = rr[1]
    results.append(run_test(df_5m, p, f"momentum relaxed R:R={rr[0]}:{rr[1]}"))

# Test deviation mode  
dev_base = relaxed_base.copy()
dev_base['entry_mode'] = 'deviation'
dev_base['deviation_pct'] = 0.3
dev_base['sd_threshold'] = 2.0

for rr in [(0.3, 0.3), (0.5, 0.5)]:
    p = dev_base.copy()
    p['atr_stop'] = rr[0]
    p['atr_target'] = rr[1]
    results.append(run_test(df_5m, p, f"deviation relaxed R:R={rr[0]}:{rr[1]}"))

# ============================================================
# Test: Very tight stops with no filters at all
# ============================================================
ultra_relaxed = {
    'entry_mode': 'cross',
    'use_session_filter': False,
    'use_trailing_stop': False,
    'use_anchored_vwap': False,  # Rolling VWAP
    'use_htf_vwap': False,
    'use_htf_ema': False,
    'require_reversal_candle': False,
    'tp_mode': 'atr',
    'adx_max': 50.0,
    'rsi_max': 70.0,
    'volume_mult': 1.0,
    'df_exit': df_1m,
    'sd_threshold': 1.5  # Lower threshold
}

for rr in [(0.2, 0.2), (0.2, 0.3), (0.3, 0.3)]:
    p = ultra_relaxed.copy()
    p['atr_stop'] = rr[0]
    p['atr_target'] = rr[1]
    results.append(run_test(df_5m, p, f"ultra_relaxed R:R={rr[0]}:{rr[1]}"))

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
        print(f"  {r['test_name']}: Net={r['net_return']:+.2f}%, Trades={r['trades']}")
else:
    best = max(results, key=lambda x: x['net_return'])
    print(f"\nBest: {best['test_name']} (Net={best['net_return']:+.2f}%)")