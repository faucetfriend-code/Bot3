#!/usr/bin/env python3
"""
VWAP Scalping - Single-SD Sweep
Usage: python vwap_sd_single.py --sd 3.5
"""
import sys
from pathlib import Path
import pandas as pd
import time
from itertools import product

sys.path.insert(0, str(Path(__file__).parent.parent))
from strategies import run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR

STORAGE_ROOT = Path("G:/Candle Data")

def load_parquet(symbol, interval):
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return None

def run_test(params, df_5m, df_1m):
    equity, trades = run_vwap_scalping(df_5m, cutoff=0.08, **params)
    
    if not trades:
        return {
            'params': {k: v for k, v in params.items() if k != 'df_exit'},
            'trades': 0, 'win_rate': 0, 'net_return': 0,
            'profit_factor': 0, 'sharpe': 0, 'max_dd': 0
        }
    
    bars_py = INTERVAL_BARS_PER_YEAR["5m"]
    metrics = compute_metrics(equity, trades, bars_per_year=bars_py)
    
    costs = len(trades) * 0.30
    net_return = metrics['total_return_pct'] - costs
    
    return {
        'params': {k: v for k, v in params.items() if k != 'df_exit'},
        'trades': metrics['n_trades'],
        'win_rate': metrics['win_rate_pct'],
        'net_return': net_return,
        'profit_factor': metrics['profit_factor'],
        'sharpe': metrics['sharpe'],
        'max_dd': metrics['max_dd_pct']
    }

# Load data
print("Loading data...")
df_5m = load_parquet("BTCUSDT", "5m")
df_5m = df_5m[(df_5m.index >= '2024-01-01') & (df_5m.index < '2025-01-01')]
print(f"5m data: {len(df_5m):,} bars (2024 only for speed)")

df_1m = load_parquet("BTCUSDT", "1m")
if df_1m is not None:
    df_1m = df_1m[(df_1m.index >= '2024-01-01') & (df_1m.index < '2025-01-01')]
    print(f"1m data: {len(df_1m):,} bars")
else:
    df_1m = df_5m

import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--sd", type=float, required=True)
args = parser.parse_args()

print(f"\n{'='*80}")
print(f"VWAP SCALPING - SINGLE SD SWEEP: SD = {args.sd}")
print(f"{'='*80}\n")

start_time = time.time()

# Parameter ranges
ENTRY_MODES = ['bull_pullback', 'bear_pullback', 'mean_reversion', 'cross', 'momentum', 'deviation']
STOCH_OVERSOLD = [10, 15, 20, 25, 30]
STOCH_OVERBOUGHT = [70, 75, 80, 85, 90]
ADX_MAX = [16, 18, 20, 25, 30, 35, 40]
RSI_MAX = [35, 40, 45, 50, 55, 60]

# Stage 1 (capped at 300 combos)
stage1_results = []
core_keys = ["entry_mode", "stoch_oversold", "stoch_overbought", "adx_max", "rsi_max"]
core_vals = [ENTRY_MODES, STOCH_OVERSOLD, STOCH_OVERBOUGHT, ADX_MAX, RSI_MAX]
core_combos = list(product(*core_vals))[:30]  # Reduced for speed

print(f"Testing {len(core_combos)} Stage 1 combinations...")
print()

for i, combo in enumerate(core_combos):
    if i % 50 == 0:
        elapsed = time.time() - start_time
        print(f"Progress: {i}/{len(core_combos)} ({elapsed:.0f}s elapsed)")
    
    params = dict(zip(core_keys, combo))
    params["sd_threshold"] = args.sd
    params["volume_mult"] = 2.0
    params["atr_stop"] = 0.7
    params["atr_target"] = 3.8
    params["pullback_bars"] = 3
    params["use_htf_vwap"] = True
    params["use_htf_ema"] = False  # From impact report - this blocks trades when on
    params["htf_adx_max"] = 25
    params["df_exit"] = df_1m
    
    result = run_test(params, df_5m, df_1m)
    stage1_results.append(result)

elapsed = time.time() - start_time
print(f"\nStage 1 complete in {elapsed:.0f}s")

# Sort and display results
stage1_sorted = sorted(stage1_results, key=lambda x: x['net_return'], reverse=True)

# Qualifying
qualifying = [r for r in stage1_sorted if r['win_rate'] > 48 and r['net_return'] > 0 and r['trades'] >= 20]

print(f"\n{'='*80}")
print(f"SD = {args.sd} RESULTS")
print(f"{'='*80}")
print(f"Total tests: {len(stage1_results)}")
print(f"Qualifying (WR>48%, Net>0%, Trades>=20): {len(qualifying)}")
print()

# Summary by entry mode
print("BY ENTRY MODE:")
print("-" * 80)
mode_summary = {}
for r in stage1_results:
    mode = r['params']['entry_mode']
    if mode not in mode_summary:
        mode_summary[mode] = {'count': 0, 'best_net': -999, 'best_wr': 0, 'best_trades': 0}
    mode_summary[mode]['count'] += 1
    if r['net_return'] > mode_summary[mode]['best_net']:
        mode_summary[mode]['best_net'] = r['net_return']
        mode_summary[mode]['best_wr'] = r['win_rate']
        mode_summary[mode]['best_trades'] = r['trades']

for mode, s in sorted(mode_summary.items(), key=lambda x: x[1]['best_net'], reverse=True):
    print(f"  {mode:<16}: count={s['count']:>3}, best_net={s['best_net']:>+7.1f}%, best_wr={s['best_wr']:.1f}%, trades={s['best_trades']}")

print()

# Top 20
print("TOP 20 BY NET RETURN:")
print("-" * 100)
print(f"{'#':>3} {'Entry Mode':<16} {'Stoch':<8} {'ADX':>4} {'RSI':>5} {'Trds':>5} {'WR%':>5} {'PF':>5} {'Net%':>8}")
print("-" * 100)

for i, r in enumerate(stage1_sorted[:20], 1):
    p = r['params']
    print(f"{i:>3} {p['entry_mode']:<16} ({p['stoch_oversold']},{p['stoch_overbought']:<3}) "
          f"{p['adx_max']:>4} {p['rsi_max']:>5} "
          f"{r['trades']:>5} {r['win_rate']:>5.1f} {r['profit_factor']:>5.2f} {r['net_return']:>+8.1f}")

print()

# Save results
output_path = Path(f"BTV2/results/vwap_sd_{args.sd}_stage1.csv")
output_path.parent.mkdir(parents=True, exist_ok=True)
pd.DataFrame([r for r in stage1_results]).to_csv(output_path, index=False)
print(f"Results saved to: {output_path}")

print(f"\nTotal time: {time.time() - start_time:.0f}s")
