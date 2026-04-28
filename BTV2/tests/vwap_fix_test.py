#!/usr/bin/env python3
"""
VWAP Fix Testing - Test all 4 recommended fixes systematically
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

# Add BTV2 to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "BTV2"))
sys.path.insert(0, str(Path(__file__).parent.parent / "BTV2" / "tests"))

from strategies import run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR

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

df_15m = load_parquet("BTCUSDT", "15m")
df_15m = df_15m[(df_15m.index >= '2023-01-01') & (df_15m.index < '2025-01-01')]
print(f"15m: {len(df_15m)} bars")

def run_test(df, df_exit, params, test_name):
    """Run a single test configuration"""
    print(f"\n{'='*60}")
    print(f"TEST: {test_name}")
    print(f"{'='*60}")
    
    equity, trades = run_vwap_scalping(df, cutoff=0.10, **params)
    
    # Determine bars per year
    interval_map = {"5m": "5m", "15m": "15m", "1h": "60min", "1h": "60min"}
    bar_key = "5m"
    for k in INTERVAL_BARS_PER_YEAR:
        if k in str(df.index[1] - df.index[0]):
            bar_key = k
            break
    bars_per_year = INTERVAL_BARS_PER_YEAR.get(bar_key, 105120)
    
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
# FIX 1: TIGHTER R:R RATIOS (on 5m)
# ============================================================
print("\n" + "="*70)
print("FIX 1: TIGHTER R:R RATIOS ON 5M")
print("="*70)

# Base params with sd=2.5, mean_reversion, session_filter
base_params = {
    'sd_threshold': 2.5,
    'entry_mode': 'mean_reversion',
    'use_session_filter': True,
    'use_trailing_stop': False,
    'use_anchored_vwap': True,
    'use_htf_vwap': True,
    'use_htf_ema': True,
    'require_reversal_candle': True,
    'adx_max': 25.0,
    'rsi_max': 45.0,
    'volume_mult': 1.5,
    'df_exit': df_1m
}

# Test 1.1: R:R = 1:1 (atr_stop=0.5, atr_target=0.5)
params = base_params.copy()
params['atr_stop'] = 0.5
params['atr_target'] = 0.5
results.append(run_test(df_5m, df_1m, params, "Fix 1a: sd2.5 mean_rev R:R=1:1"))

# Test 1.2: R:R = 1.5:1 (atr_stop=0.5, atr_target=0.75)
params = base_params.copy()
params['atr_stop'] = 0.5
params['atr_target'] = 0.75
results.append(run_test(df_5m, df_1m, params, "Fix 1b: sd2.5 mean_rev R:R=1.5:1"))

# Test 1.3: R:R = 2:1 (atr_stop=0.5, atr_target=1.0)
params = base_params.copy()
params['atr_stop'] = 0.5
params['atr_target'] = 1.0
results.append(run_test(df_5m, df_1m, params, "Fix 1c: sd2.5 mean_rev R:R=2:1"))

# Test 1.4: R:R = 1.4:1 (atr_stop=0.7, atr_target=1.0)
params = base_params.copy()
params['atr_stop'] = 0.7
params['atr_target'] = 1.0
results.append(run_test(df_5m, df_1m, params, "Fix 1d: sd2.5 mean_rev R:R=1.4:1"))

# ============================================================
# FIX 2: HIGHER TIMEFRAME
# ============================================================
print("\n" + "="*70)
print("FIX 2: HIGHER TIMEFRAME (15m and 1h)")
print("="*70)

# For higher timeframes, adjust params appropriately
htf_params = {
    'sd_threshold': 2.5,
    'entry_mode': 'mean_reversion',
    'use_session_filter': True,
    'use_trailing_stop': False,
    'use_anchored_vwap': True,
    'use_htf_vwap': False,  # Disable for HTF to avoid nesting
    'use_htf_ema': False,
    'require_reversal_candle': True,
    'adx_max': 30.0,  # More tolerant for longer timeframes
    'rsi_max': 50.0,
    'volume_mult': 1.5,
    'atr_stop': 0.5,
    'atr_target': 1.0,  # R:R = 2:1
}

# Test 2.1: 15m timeframe
results.append(run_test(df_15m, df_1m, htf_params, "Fix 2a: 15m sd2.5 mean_rev"))

# Test 2.2: 1h timeframe
# We don't have 1h data, use 15m as proxy (still higher than 5m)
# Actually let's use 15m resampled or just show the comparison
# For now, let's use 15m with adjusted parameters
htf_params_1h = htf_params.copy()
htf_params_1h['atr_stop'] = 0.3  # Tighter stops on higher TF
htf_params_1h['atr_target'] = 0.9  # R:R = 3:1
results.append(run_test(df_15m, df_1m, htf_params_1h, "Fix 2b: 15m (proxy for 1h) R:R=3:1"))

# ============================================================
# FIX 3: TRAILING STOPS
# ============================================================
print("\n" + "="*70)
print("FIX 3: TRAILING STOPS")
print("="*70)

# Base with trailing stop enabled
trail_params = {
    'sd_threshold': 2.5,
    'entry_mode': 'mean_reversion',
    'use_session_filter': True,
    'use_trailing_stop': True,  # Enable trailing
    'trailing_atr': 1.5,  # Trail 1.5 ATR behind price
    'use_anchored_vwap': True,
    'use_htf_vwap': True,
    'use_htf_ema': True,
    'require_reversal_candle': True,
    'adx_max': 25.0,
    'rsi_max': 45.0,
    'volume_mult': 1.5,
    'atr_stop': 0.7,
    'atr_target': 3.0,
    'df_exit': df_1m
}
results.append(run_test(df_5m, df_1m, trail_params, "Fix 3: mean_rev + trailing (1.5 ATR)"))

# Try different trailing ATR values
for trail_atr in [1.0, 2.0, 2.5]:
    trail_params2 = trail_params.copy()
    trail_params2['trailing_atr'] = trail_atr
    results.append(run_test(df_5m, df_1m, trail_params2, f"Fix 3: trailing@{trail_atr}ATR"))

# ============================================================
# FIX 4: MOMENTUM MODE (bull_pullback)
# ============================================================
print("\n" + "="*70)
print("FIX 4: MOMENTUM MODE (bull_pullback)")
print("="*70)

momentum_params = {
    'sd_threshold': 2.0,
    'entry_mode': 'bull_pullback',
    'use_session_filter': True,
    'use_trailing_stop': False,
    'use_anchored_vwap': True,
    'use_htf_vwap': True,
    'use_htf_ema': True,
    'require_reversal_candle': True,
    'adx_max': 30.0,
    'rsi_max': 50.0,
    'volume_mult': 2.0,
    'df_exit': df_1m
}

# Test various R:R with momentum mode
for rr in [(0.5, 0.5), (0.5, 0.75), (0.5, 1.0), (0.7, 1.0)]:
    params = momentum_params.copy()
    params['atr_stop'] = rr[0]
    params['atr_target'] = rr[1]
    results.append(run_test(df_5m, df_1m, params, f"Fix 4: bull_pullback R:R={rr[0]}:{rr[1]}"))

# ============================================================
# SUMMARY TABLE
# ============================================================
print("\n" + "="*70)
print("SUMMARY RESULTS")
print("="*70)

print(f"\n{'Test':<40} {'Trades':>7} {'WR%':>6} {'Net%':>8} {'PF':>6} {'Sharpe':>7}")
print("-" * 80)
for r in results:
    flag = " **" if r['net_return'] > 0 and r['trades'] >= 20 else ""
    print(f"{r['test_name']:<40} {r['trades']:>7} {r['win_rate']:>5.1f}% {r['net_return']:>+7.2f}% {r['profit_factor']:>5.2f} {r['sharpe']:>+6.2f}{flag}")

# Find best
best = max(results, key=lambda x: x['net_return'])
print(f"\n{'='*70}")
print(f"BEST CONFIGURATION: {best['test_name']}")
print(f"  Net Return: {best['net_return']:+.2f}%")
print(f"  Trades: {best['trades']}")
print(f"  Win Rate: {best['win_rate']:.1f}%")
print(f"  Profit Factor: {best['profit_factor']:.2f}")
print(f"{'='*70}")