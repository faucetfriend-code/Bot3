#!/usr/bin/env python3
"""
VWAP Final Comprehensive Test - All fixes tested properly
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
df_5m = df_5m[(df_5m.index >= '2023-01-01') & (df_5m.index < '2025-01-01')]
print(f"5m: {len(df_5m)} bars")

df_1m = load_parquet("BTCUSDT", "1m")
df_1m = df_1m[(df_1m.index >= '2023-01-01') & (df_1m.index < '2025-01-01')]
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
# FIX 1: TIGHTER R:R - sd=2.5 mean_reversion with various R:R
# ============================================================
print("\n=== FIX 1: TIGHTER R:R ===")

fix1_base = {
    'sd_threshold': 2.5,
    'entry_mode': 'mean_reversion',
    'use_session_filter': True,
    'use_trailing_stop': False,
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

for rr in [(0.5, 0.5), (0.5, 0.75), (0.5, 1.0), (0.7, 1.0)]:
    p = fix1_base.copy()
    p['atr_stop'] = rr[0]
    p['atr_target'] = rr[1]
    results.append(run_test(df_5m, p, f"sd2.5 mean_rev R:R={rr[0]}:{rr[1]}"))

# ============================================================
# FIX 2: HIGHER TIMEFRAME - 15m with mean_reversion
# ============================================================
print("\n=== FIX 2: HIGHER TIMEFRAME ===")

# Load 15m data
df_15m = load_parquet("BTCUSDT", "15m")
df_15m = df_15m[(df_15m.index >= '2023-01-01') & (df_15m.index < '2025-01-01')]
print(f"15m: {len(df_15m)} bars")

fix2_params = {
    'sd_threshold': 2.5,
    'entry_mode': 'mean_reversion',
    'use_session_filter': True,
    'use_trailing_stop': False,
    'use_anchored_vwap': True,
    'use_htf_vwap': False,  # Disable for HTF to avoid nesting
    'use_htf_ema': False,
    'require_reversal_candle': True,
    'tp_mode': 'atr',
    'adx_max': 30.0,
    'rsi_max': 50.0,
    'volume_mult': 1.5,
    'atr_stop': 0.5,
    'atr_target': 1.0,
    'df_exit': df_1m
}
results.append(run_test(df_15m, fix2_params, "15m mean_rev R:R=2:1"))

# ============================================================
# FIX 3: TRAILING STOPS - mean_reversion with trailing
# ============================================================
print("\n=== FIX 3: TRAILING STOPS ===")

fix3_base = {
    'sd_threshold': 2.5,
    'entry_mode': 'mean_reversion',
    'use_session_filter': True,
    'use_trailing_stop': True,
    'use_anchored_vwap': True,
    'use_htf_vwap': True,
    'use_htf_ema': True,
    'require_reversal_candle': True,
    'tp_mode': 'atr',
    'adx_max': 25.0,
    'rsi_max': 45.0,
    'volume_mult': 1.5,
    'atr_stop': 0.7,
    'atr_target': 3.0,
    'df_exit': df_1m
}

for trail in [1.0, 1.5, 2.0]:
    p = fix3_base.copy()
    p['trailing_atr'] = trail
    results.append(run_test(df_5m, p, f"mean_rev + trail@{trail}ATR"))

# ============================================================
# FIX 4: MOMENTUM MODE - bull_pullback with various R:R
# ============================================================
print("\n=== FIX 4: MOMENTUM MODE (bull_pullback) ===")

fix4_base = {
    'sd_threshold': 2.0,
    'entry_mode': 'bull_pullback',
    'use_session_filter': True,
    'use_trailing_stop': False,
    'use_anchored_vwap': True,
    'use_htf_vwap': True,
    'use_htf_ema': True,
    'require_reversal_candle': True,
    'tp_mode': 'atr',
    'adx_max': 30.0,
    'rsi_max': 50.0,
    'volume_mult': 2.0,
    'df_exit': df_1m
}

for rr in [(0.5, 0.5), (0.5, 0.75), (0.5, 1.0), (0.7, 1.0)]:
    p = fix4_base.copy()
    p['atr_stop'] = rr[0]
    p['atr_target'] = rr[1]
    results.append(run_test(df_5m, p, f"bull_pullback R:R={rr[0]}:{rr[1]}"))

# ============================================================
# COMBINATIONS: Try combining fixes
# ============================================================
print("\n=== COMBINATIONS ===")

# bull_pullback + trailing
combo1 = fix4_base.copy()
combo1['use_trailing_stop'] = True
combo1['trailing_atr'] = 1.5
combo1['atr_stop'] = 0.5
combo1['atr_target'] = 1.0
results.append(run_test(df_5m, combo1, "bull_pullback + trail R:R=2:1"))

# mean_reversion + trailing + tighter filters
combo2 = fix1_base.copy()
combo2['use_trailing_stop'] = True
combo2['trailing_atr'] = 1.0
combo2['atr_stop'] = 0.3
combo2['atr_target'] = 0.5
results.append(run_test(df_5m, combo2, "mean_rev + trail tight"))

# cross mode with tight stops
cross_params = {
    'entry_mode': 'cross',
    'use_session_filter': True,
    'use_trailing_stop': False,
    'use_anchored_vwap': True,
    'use_htf_vwap': True,
    'use_htf_ema': True,
    'require_reversal_candle': False,  # No reversal for cross
    'tp_mode': 'atr',
    'adx_max': 30.0,
    'rsi_max': 50.0,
    'volume_mult': 1.0,
    'atr_stop': 0.5,
    'atr_target': 0.75,
    'df_exit': df_1m
}
results.append(run_test(df_5m, cross_params, "cross tight R:R=1.5:1"))

# ============================================================
# SUMMARY
# ============================================================
print("\n" + "="*70)
print("SUMMARY - ALL TESTS")
print("="*70)

print(f"\n{'Test':<40} {'Trades':>6} {'WR%':>5} {'Net%':>8} {'PF':>5}")
print("-" * 70)
for r in sorted(results, key=lambda x: -x['net_return']):
    flag = " **" if r['net_return'] > 0 and r['trades'] >= 20 else ""
    print(f"{r['test_name']:<40} {r['trades']:>6} {r['win_rate']:>4.1f}% {r['net_return']:>+7.2f}% {r['profit_factor']:>4.2f}{flag}")

profitable = [r for r in results if r['net_return'] > 0 and r['trades'] >= 20]
if profitable:
    print(f"\n*** PROFITABLE: {len(profitable)} configurations ***")
    for r in sorted(profitable, key=lambda x: -x['net_return']):
        print(f"  {r['test_name']}: Net={r['net_return']:+.2f}%, Trades={r['trades']}, WR={r['win_rate']:.1f}%")
else:
    best = max(results, key=lambda x: x['net_return'])
    print(f"\n*** BEST (but not profitable) ***")
    print(f"  {best['test_name']}")
    print(f"  Net Return: {best['net_return']:+.2f}%")
    print(f"  Trades: {best['trades']}")
    print(f"  Win Rate: {best['win_rate']:.1f}%")
    print(f"  Profit Factor: {best['profit_factor']:.2f}")