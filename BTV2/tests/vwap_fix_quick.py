#!/usr/bin/env python3
"""
VWAP Fix Testing - Quick focused test with tp_mode
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

# Load data - use smaller period for faster testing
print("Loading data...")
df_5m = load_parquet("BTCUSDT", "5m")
df_5m = df_5m[(df_5m.index >= '2024-01-01') & (df_5m.index < '2025-01-01')]
print(f"5m: {len(df_5m)} bars")

df_1m = load_parquet("BTCUSDT", "1m")
df_1m = df_1m[(df_1m.index >= '2024-01-01') & (df_1m.index < '2025-01-01')]
print(f"1m: {len(df_1m)} bars")

def run_test(df, params, test_name):
    """Run a single test configuration"""
    print(f"\n{test_name}...", end=" ", flush=True)
    
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
        'sharpe': metrics['sharpe']
    }

results = []

# ============================================================
# BASELINE: VS_DEFAULTS with tp_mode=vwap
# ============================================================
baseline_params = {
    'sd_threshold': 2.0,
    'atr_stop': 0.7,
    'atr_target': 3.0,
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
    'df_exit': df_1m
}
results.append(run_test(df_5m, baseline_params, "BASELINE (vwap tp)"))

# ============================================================
# QUICK TEST: tp_mode=atr with various R:R
# ============================================================

# Test 1: bull_pullback, tight R:R
p1 = baseline_params.copy()
p1['tp_mode'] = 'atr'
p1['atr_stop'] = 0.5
p1['atr_target'] = 0.5
results.append(run_test(df_5m, p1, "bull_pullback R:R=1:1 (atr)"))

p2 = baseline_params.copy()
p2['tp_mode'] = 'atr'
p2['atr_stop'] = 0.5
p2['atr_target'] = 0.75
results.append(run_test(df_5m, p2, "bull_pullback R:R=1.5:1 (atr)"))

p3 = baseline_params.copy()
p3['tp_mode'] = 'atr'
p3['atr_stop'] = 0.5
p3['atr_target'] = 1.0
results.append(run_test(df_5m, p3, "bull_pullback R:R=2:1 (atr)"))

# Test 2: mean_reversion mode
mr_params = {
    'sd_threshold': 2.5,
    'entry_mode': 'mean_reversion',
    'use_session_filter': True,
    'use_trailing_stop': True,
    'trailing_atr': 1.2,
    'use_anchored_vwap': True,
    'use_htf_vwap': True,
    'use_htf_ema': True,
    'require_reversal_candle': True,
    'tp_mode': 'atr',
    'adx_max': 25.0,
    'rsi_max': 45.0,
    'volume_mult': 1.5,
    'df_exit': df_1m
}

for rr in [(0.5, 0.5), (0.5, 0.75), (0.5, 1.0)]:
    p = mr_params.copy()
    p['atr_stop'] = rr[0]
    p['atr_target'] = rr[1]
    results.append(run_test(df_5m, p, f"mean_rev R:R={rr[0]}:{rr[1]} (atr)"))

# Test 3: cross mode
cross_params = {
    'entry_mode': 'cross',
    'use_session_filter': True,
    'use_trailing_stop': True,
    'trailing_atr': 1.2,
    'use_anchored_vwap': True,
    'use_htf_vwap': True,
    'use_htf_ema': True,
    'require_reversal_candle': True,
    'tp_mode': 'atr',
    'adx_max': 25.0,
    'rsi_max': 45.0,
    'volume_mult': 1.5,
    'df_exit': df_1m
}

for rr in [(0.5, 0.5), (0.5, 0.75)]:
    p = cross_params.copy()
    p['atr_stop'] = rr[0]
    p['atr_target'] = rr[1]
    results.append(run_test(df_5m, p, f"cross R:R={rr[0]}:{rr[1]} (atr)"))

# ============================================================
# SUMMARY
# ============================================================
print("\n" + "="*70)
print("SUMMARY")
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