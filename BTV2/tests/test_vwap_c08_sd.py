#!/usr/bin/env python3
"""
Test cutoff 0.08 with different SD values to find more trades.
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


df_5m = load_parquet("BTCUSDT", "5m")
df_5m.columns = [c.capitalize() for c in df_5m.columns]
df_5m = df_5m[(df_5m.index >= '2024-01-01') & (df_5m.index < '2025-01-01')]
print(f"Loaded {len(df_5m)} bars")
print()

# Test cutoff 0.08 with various SD values
print("=" * 80)
print("CUTOFF 0.08 - SD SWEEP (5m, 2024)")
print("=" * 80)

results = []
for sd in [4.0, 4.5, 5.0, 5.5, 6.0, 6.5, 7.0]:
    for atr in [5.0, 7.0, 10.0]:
        eq, trades = run_vwap_simple(df_5m, cutoff=0.08, sd_threshold=sd, atr_stop=atr)
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
        
        if len(trades) >= 5:
            print(f"SD={sd}, ATR={atr}: Trades={len(trades)}, Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Net={net:+.1f}%")
            results.append((sd, atr, m, len(trades), wr, pf, gross, costs, net))

# Also test cutoff 0.07
print()
print("=" * 80)
print("CUTOFF 0.07 - SD SWEEP (5m, 2024)")
print("=" * 80)

for sd in [4.0, 4.5, 5.0, 5.5, 6.0]:
    for atr in [5.0, 7.0]:
        eq, trades = run_vwap_simple(df_5m, cutoff=0.07, sd_threshold=sd, atr_stop=atr)
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
        
        if len(trades) >= 5:
            print(f"SD={sd}, ATR={atr}: Trades={len(trades)}, Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Net={net:+.1f}%")
            results.append((sd, atr, m, len(trades), wr, pf, gross, costs, net))

print()
print("=" * 80)
print("BEST RESULTS")
print("=" * 80)

positive = [r for r in results if r[6] > 0]
if positive:
    print("\n>> POSITIVE RETURN:")
    for r in sorted(positive, key=lambda x: x[6], reverse=True):
        print(f"  SD={r[0]}, ATR={r[1]}: Return={r[6]:+.1f}%, WR={r[4]:.0f}%, PF={r[5]:.2f}, Trades={r[3]}")

good_pf = [r for r in results if r[5] > 1.0]
if good_pf:
    print("\n>> PROFIT FACTOR > 1.0:")
    for r in sorted(good_pf, key=lambda x: x[5], reverse=True):
        print(f"  SD={r[0]}, ATR={r[1]}: PF={r[5]:.2f}, Return={r[6]:+.1f}%, WR={r[4]:.0f}%, Trades={r[3]}")

# Best net return
print("\n>> BEST NET (min 10 trades):")
for r in sorted([x for x in results if x[3] >= 10], key=lambda x: x[7], reverse=True)[:5]:
    print(f"  SD={r[0]}, ATR={r[1]}: Net={r[7]:+.1f}% (Gross={r[6]:+.1f}% - Costs={r[7-1]:.1f}%)")
