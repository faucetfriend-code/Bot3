#!/usr/bin/env python3
"""
Test corrected VWAP Scalping on 5m and 1m.
"""
import sys
from pathlib import Path
import pandas as pd

STORAGE_ROOT = Path("G:/Candle Data")

def load_parquet(symbol, interval):
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return None

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import run_vwap_scalping, compute_metrics

# Test 5m
print("=" * 90)
print("CORRECTED VWAP - 5m TIMEFRAME (2024)")
print("=" * 90)

df_5m = load_parquet("BTCUSDT", "5m")
df_5m.columns = [c.capitalize() for c in df_5m.columns]
df_5m = df_5m[(df_5m.index >= '2024-01-01') & (df_5m.index < '2025-01-01')]
print(f"Loaded {len(df_5m)} bars")
print()

# Test different parameter combinations
results = []

# Key params to test
param_sets = [
    # (sd, atr, ema_fast, ema_slow, trend, vol, vol_mult, trail, trail_atr)
    (2.0, 1.0, 9, 20, True, True, 1.5, True, 1.0),
    (2.5, 1.0, 9, 20, True, True, 1.5, True, 1.0),
    (3.0, 1.0, 9, 20, True, True, 1.5, True, 1.0),
    (2.0, 1.5, 9, 20, True, True, 1.5, True, 1.0),
    (2.5, 1.5, 9, 20, True, True, 1.5, True, 1.0),
    (3.0, 1.5, 9, 20, True, True, 1.5, True, 1.0),
    (2.0, 1.0, 9, 20, False, False, 1.0, False, 0),
    (2.5, 1.0, 9, 20, False, False, 1.0, False, 0),
    (3.0, 1.0, 9, 20, False, False, 1.0, False, 0),
    (2.0, 0.5, 9, 20, True, True, 1.2, True, 0.5),
    (2.5, 0.5, 9, 20, True, True, 1.2, True, 0.5),
    (3.0, 0.5, 9, 20, True, True, 1.2, True, 0.5),
]

for sd, atr, ema_f, ema_s, trend, vol, vol_m, trail, trail_atr in param_sets:
    eq, trades = run_vwap_scalping(
        df_5m,
        cutoff=0.10,
        sd_threshold=sd,
        atr_stop=atr,
        atr_target=2.0,
        ema_fast=ema_f,
        ema_slow=ema_s,
        use_trend_filter=trend,
        use_volume_filter=vol,
        volume_mult=vol_m,
        use_trailing_stop=trail,
        trailing_atr=trail_atr,
    )
    m = compute_metrics(eq, trades)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        wr = wins / len(trades) * 100
        gp = sum(t for t in trades if t > 0)
        gl = abs(sum(t for t in trades if t < 0))
        pf = gp / gl if gl > 0 else 0
    else:
        wr, pf = 0, 0
    
    gross = m['total_return_pct']
    costs = len(trades) * 0.30
    net = gross - costs
    
    if len(trades) >= 10:
        print(f"SD={sd}, ATR={atr}, Trend={trend}, Vol={vol}: Trades={len(trades)}, Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Net={net:+.1f}%")
        results.append((sd, atr, trend, vol, m, len(trades), wr, pf, gross, costs, net))

# Test 1m
print()
print("=" * 90)
print("CORRECTED VWAP - 1m TIMEFRAME (2024)")
print("=" * 90)

df_1m = load_parquet("BTCUSDT", "1m")
df_1m.columns = [c.capitalize() for c in df_1m.columns]
df_1m = df_1m[(df_1m.index >= '2024-01-01') & (df_1m.index < '2025-01-01')]
print(f"Loaded {len(df_1m)} bars")
print()

for sd, atr, ema_f, ema_s, trend, vol, vol_m, trail, trail_atr in param_sets[:6]:
    eq, trades = run_vwap_scalping(
        df_1m,
        cutoff=0.10,
        sd_threshold=sd,
        atr_stop=atr,
        atr_target=2.0,
        ema_fast=ema_f,
        ema_slow=ema_s,
        use_trend_filter=trend,
        use_volume_filter=vol,
        volume_mult=vol_m,
        use_trailing_stop=trail,
        trailing_atr=trail_atr,
    )
    m = compute_metrics(eq, trades)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        wr = wins / len(trades) * 100
        gp = sum(t for t in trades if t > 0)
        gl = abs(sum(t for t in trades if t < 0))
        pf = gp / gl if gl > 0 else 0
    else:
        wr, pf = 0, 0
    
    gross = m['total_return_pct']
    costs = len(trades) * 0.30
    net = gross - costs
    
    if len(trades) >= 10:
        print(f"SD={sd}, ATR={atr}, Trend={trend}, Vol={vol}: Trades={len(trades)}, Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Net={net:+.1f}%")

# Summary
print()
print("=" * 90)
print("BEST RESULTS (5m)")
print("=" * 90)

if results:
    positive = [r for r in results if r[8] > 0]
    if positive:
        print("\n>> POSITIVE RETURN:")
        for r in sorted(positive, key=lambda x: x[8], reverse=True)[:5]:
            print(f"  SD={r[0]}, ATR={r[1]}, Trend={r[2]}, Vol={r[3]}: Return={r[8]:+.1f}%, WR={r[6]:.0f}%, PF={r[7]:.2f}, Trades={r[5]}")
    
    good_wr = [r for r in results if r[6] > 50]
    if good_wr:
        print("\n>> WIN RATE > 50%:")
        for r in sorted(good_wr, key=lambda x: x[6], reverse=True)[:5]:
            print(f"  SD={r[0]}, ATR={r[1]}, Trend={r[2]}, Vol={r[3]}: WR={r[6]:.0f}%, Return={r[8]:+.1f}%, PF={r[7]:.2f}")
    
    good_pf = [r for r in results if r[7] > 1.0]
    if good_pf:
        print("\n>> PROFIT FACTOR > 1.0:")
        for r in sorted(good_pf, key=lambda x: x[7], reverse=True)[:5]:
            print(f"  SD={r[0]}, ATR={r[1]}, Trend={r[2]}, Vol={r[3]}: PF={r[7]:.2f}, WR={r[6]:.0f}%, Return={r[8]:+.1f}%")
