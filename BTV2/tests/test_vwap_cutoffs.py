#!/usr/bin/env python3
"""
Test VWAP with different Butterworth cutoffs on 5m and 1m.
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

STORAGE_ROOT = Path("G:/Candle Data")

def load_parquet(symbol, interval):
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return None

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    apply_butterworth, compute_vwap_rolling, compute_macd, 
    compute_atr, compute_metrics, COST_PER_SIDE
)

def run_vwap_simple(df, cutoff, sd_threshold, atr_stop, vwap_period=20):
    """Simple VWAP scalper."""
    fc = apply_butterworth(df["Close"], cutoff)
    vwap, vs = compute_vwap_rolling(df["High"], df["Low"], fc, df["Volume"], vwap_period)
    _, _, mh = compute_macd(fc, 12, 26, 9)
    atr = compute_atr(df["High"], df["Low"], df["Close"], 14)

    prices = df["Close"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    n = len(prices)

    equity_arr = np.ones(n, dtype=float)
    curr_equity = 1.0
    in_pos = [False]
    side = [""]
    entry_eq = [1.0]
    sl = [0.0]
    closed_trades = []

    for i in range(1, n):
        vw = vwap.iloc[i]; vsd = vs.iloc[i]
        mhi = mh.iloc[i]; at = atr.iloc[i]
        p = prices[i]; p0 = prices[i-1]

        if any(np.isnan(x) for x in (vw, vsd, mhi, at)):
            equity_arr[i] = curr_equity
            continue

        upper_band = vw + sd_threshold * vsd
        lower_band = vw - sd_threshold * vsd

        if in_pos[0]:
            curr_equity *= p / p0
            if side[0] == "long" and lows[i] <= sl[0]:
                curr_equity *= (1.0 - COST_PER_SIDE)
                closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                in_pos[0] = False
            elif side[0] == "short" and highs[i] >= sl[0]:
                curr_equity *= (1.0 - COST_PER_SIDE)
                closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                in_pos[0] = False
            elif side[0] == "long" and p >= vw:
                curr_equity *= (1.0 - COST_PER_SIDE)
                closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                in_pos[0] = False
            elif side[0] == "short" and p <= vw:
                curr_equity *= (1.0 - COST_PER_SIDE)
                closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                in_pos[0] = False
        else:
            if p < lower_band and mhi < 0:
                curr_equity *= (1.0 - COST_PER_SIDE)
                entry_eq[0] = curr_equity
                sl[0] = p - atr_stop * at
                in_pos[0] = True
                side[0] = "long"
            elif p > upper_band and mhi > 0:
                curr_equity *= (1.0 - COST_PER_SIDE)
                entry_eq[0] = curr_equity
                sl[0] = p + atr_stop * at
                in_pos[0] = True
                side[0] = "short"

        equity_arr[i] = curr_equity

    if in_pos[0]:
        curr_equity *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
        equity_arr[-1] = curr_equity

    return pd.Series(equity_arr, index=df.index), closed_trades


# Test 5m
print("=" * 90)
print("VWAP - 5m - BUTTERWORTH CUTOFF SWEEP")
print("=" * 90)

df_5m = load_parquet("BTCUSDT", "5m")
df_5m.columns = [c.capitalize() for c in df_5m.columns]
df_5m = df_5m[(df_5m.index >= '2024-01-01') & (df_5m.index < '2025-01-01')]
print(f"Loaded {len(df_5m)} bars")
print()

cutoffs = [0.125, 0.10, 0.08, 0.07, 0.06, 0.05, 0.04, 0.03, 0.02, 0.01, 0.005]
sd = 6.0  # Best from earlier tests

results_5m = []
for cutoff in cutoffs:
    eq, trades = run_vwap_simple(df_5m, cutoff=cutoff, sd_threshold=sd, atr_stop=7.0)
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
    
    print(f"Cutoff={cutoff:.3f}: Trades={len(trades)}, Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Net={net:+.1f}%")
    results_5m.append((cutoff, m, len(trades), wr, pf, gross, costs, net))

# Test 1m
print()
print("=" * 90)
print("VWAP - 1m - BUTTERWORTH CUTOFF SWEEP")
print("=" * 90)

df_1m = load_parquet("BTCUSDT", "1m")
df_1m.columns = [c.capitalize() for c in df_1m.columns]
df_1m = df_1m[(df_1m.index >= '2024-01-01') & (df_1m.index < '2025-01-01')]
print(f"Loaded {len(df_1m)} bars")
print()

for cutoff in cutoffs:
    eq, trades = run_vwap_simple(df_1m, cutoff=cutoff, sd_threshold=sd, atr_stop=7.0)
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
    
    print(f"Cutoff={cutoff:.3f}: Trades={len(trades)}, Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Net={net:+.1f}%")

# Summary
print()
print("=" * 90)
print("BEST RESULTS - 5m")
print("=" * 90)

positive = [r for r in results_5m if r[5] > 0]
if positive:
    print("\n>> POSITIVE RETURN:")
    for r in sorted(positive, key=lambda x: x[5], reverse=True):
        print(f"  Cutoff={r[0]:.3f}: Return={r[5]:+.1f}%, WR={r[3]:.0f}%, PF={r[4]:.2f}, Trades={r[2]}")

good_pf = [r for r in results_5m if r[4] > 1.0]
if good_pf:
    print("\n>> PROFIT FACTOR > 1.0:")
    for r in sorted(good_pf, key=lambda x: x[4], reverse=True):
        print(f"  Cutoff={r[0]:.3f}: PF={r[4]:.2f}, Return={r[5]:+.1f}%, WR={r[3]:.0f}%, Trades={r[2]}")

good_wr = [r for r in results_5m if r[3] > 50]
if good_wr:
    print("\n>> WIN RATE > 50%:")
    for r in sorted(good_wr, key=lambda x: x[3], reverse=True):
        print(f"  Cutoff={r[0]:.3f}: WR={r[3]:.0f}%, PF={r[4]:.2f}, Return={r[5]:+.1f}%, Trades={r[2]}")

print("\n>> TOP BY NET RETURN:")
for r in sorted(results_5m, key=lambda x: x[7], reverse=True)[:5]:
    print(f"  Cutoff={r[0]:.3f}: Net={r[7]:+.1f}% (Gross={r[5]:+.1f}% - Costs={r[6]:.1f}%)")
