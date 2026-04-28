#!/usr/bin/env python3
"""
VWAP Fix Testing - Extended tests with tp_mode and VS_DEFAULTS baseline
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

# Load data
print("Loading data...")
df_5m = load_parquet("BTCUSDT", "5m")
df_5m = df_5m[(df_5m.index >= '2023-01-01') & (df_5m.index < '2025-01-01')]
print(f"5m: {len(df_5m)} bars")

df_1m = load_parquet("BTCUSDT", "1m")
df_1m = df_1m[(df_1m.index >= '2023-01-01') & (df_1m.index < '2025-01-01')]
print(f"1m: {len(df_1m)} bars")

def run_test(df, params, test_name):
    """Run a single test configuration"""
    print(f"\n{'='*60}")
    print(f"TEST: {test_name}")
    print(f"{'='*60}")
    
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
    
    print(f"  Trades:       {len(trades)}")
    print(f"  Win Rate:     {win_rate:.1f}%")
    print(f"  Total Return: {metrics['total_return_pct']:+.2f}%")
    print(f"  Net Return:   {net_return:+.2f}% (after {costs:.1f}% costs)")
    print(f"  Profit Factor: {profit_factor:.2f}")
    print(f"  Sharpe:       {metrics['sharpe']:.2f}")
    
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
# BASELINE: VS_DEFAULTS as-is
# ============================================================
print("\n" + "="*70)
print("BASELINE: VS_DEFAULTS")
print("="*70)

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
    'tp_mode': 'vwap',  # session VWAP target, NOT atr_target!
    'adx_max': 25.0,
    'rsi_max': 45.0,
    'volume_mult': 1.5,
    'df_exit': df_1m
}
results.append(run_test(df_5m, baseline_params, "BASELINE: VS_DEFAULTS (tp_mode=vwap)"))

# ============================================================
# TEST: tp_mode="atr" with tight R:R
# ============================================================
print("\n" + "="*70)
print("TP_MODE=ATR WITH TIGHT R:R")
print("="*70)

# Test with tp_mode="atr" (uses atr_target for TP)
atr_params = {
    'sd_threshold': 2.0,
    'entry_mode': 'bull_pullback',
    'use_session_filter': True,
    'use_trailing_stop': True,
    'trailing_atr': 1.2,
    'use_anchored_vwap': True,
    'use_htf_vwap': True,
    'use_htf_ema': True,
    'require_reversal_candle': True,
    'tp_mode': 'atr',  # USE ATR TARGET!
    'adx_max': 25.0,
    'rsi_max': 45.0,
    'volume_mult': 1.5,
    'df_exit': df_1m
}

# Test different R:R with tp_mode="atr"
for rr in [(0.5, 0.5), (0.5, 0.75), (0.5, 1.0), (0.7, 1.0), (1.0, 1.5), (1.0, 2.0)]:
    params = atr_params.copy()
    params['atr_stop'] = rr[0]
    params['atr_target'] = rr[1]
    results.append(run_test(df_5m, params, f"tp_mode=atr R:R={rr[0]}:{rr[1]}"))

# ============================================================
# TEST: mean_reversion mode with tp_mode="atr"
# ============================================================
print("\n" + "="*70)
print("MEAN_REVERSION MODE WITH tp_mode=atr")
print("="*70)

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

for rr in [(0.5, 0.5), (0.5, 0.75), (0.5, 1.0), (0.7, 1.0)]:
    params = mr_params.copy()
    params['atr_stop'] = rr[0]
    params['atr_target'] = rr[1]
    results.append(run_test(df_5m, params, f"mean_rev R:R={rr[0]}:{rr[1]}"))

# ============================================================
# TEST: Cross mode with tp_mode="atr"
# ============================================================
print("\n" + "="*70)
print("CROSS MODE WITH tp_mode=atr")
print("="*70)

cross_params = {
    'sd_threshold': 2.0,
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

for rr in [(0.5, 0.5), (0.5, 0.75), (0.5, 1.0)]:
    params = cross_params.copy()
    params['atr_stop'] = rr[0]
    params['atr_target'] = rr[1]
    results.append(run_test(df_5m, params, f"cross R:R={rr[0]}:{rr[1]}"))

# ============================================================
# TEST: Deviation mode with tp_mode="atr"
# ============================================================
print("\n" + "="*70)
print("DEVIATION MODE WITH tp_mode=atr")
print("="*70)

dev_params = {
    'sd_threshold': 2.0,
    'entry_mode': 'deviation',
    'deviation_pct': 0.5,
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
    params = dev_params.copy()
    params['atr_stop'] = rr[0]
    params['atr_target'] = rr[1]
    results.append(run_test(df_5m, params, f"deviation R:R={rr[0]}:{rr[1]}"))

# ============================================================
# SUMMARY
# ============================================================
print("\n" + "="*70)
print("ALL RESULTS SUMMARY")
print("="*70)

print(f"\n{'Test':<45} {'Trades':>6} {'WR%':>5} {'Net%':>8} {'PF':>5} {'Sharpe':>6}")
print("-" * 80)
for r in results:
    flag = " **" if r['net_return'] > 0 and r['trades'] >= 20 else ""
    print(f"{r['test_name']:<45} {r['trades']:>6} {r['win_rate']:>4.1f}% {r['net_return']:>+7.2f}% {r['profit_factor']:>4.2f} {r['sharpe']:>+5.2f}{flag}")

# Find profitable ones
profitable = [r for r in results if r['net_return'] > 0 and r['trades'] >= 20]
if profitable:
    print(f"\n{'='*70}")
    print(f"PROFITABLE CONFIGURATIONS ({len(profitable)} found):")
    print("="*70)
    for r in sorted(profitable, key=lambda x: -x['net_return']):
        print(f"  {r['test_name']}: Net={r['net_return']:+.2f}%, Trades={r['trades']}, WR={r['win_rate']:.1f}%")
else:
    best = max(results, key=lambda x: x['net_return'])
    print(f"\n{'='*70}")
    print(f"BEST (but not profitable): {best['test_name']}")
    print(f"  Net Return: {best['net_return']:+.2f}%")
    print(f"  Trades: {best['trades']}")
    print("="*70)