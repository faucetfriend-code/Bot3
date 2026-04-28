#!/usr/bin/env python3
"""
Test VWAP on 1m timeframe with various SD values.
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


# Load 1m data
df = load_parquet("BTCUSDT", "1m")
df.columns = [c.capitalize() for c in df.columns]
df = df[(df.index >= '2024-01-01') & (df.index < '2025-01-01')]
print(f"Loaded {len(df)} bars (2024)")
print()

print("=" * 90)
print("VWAP SCALPING - 1m TIMEFRAME (Binance 2024)")
print("=" * 90)

results = []

for sd in [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0]:
    for atr in [3.0, 5.0, 7.0, 10.0]:
        eq, trades = run_vwap_simple(df, cutoff=0.10, sd_threshold=sd, atr_stop=atr)
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
        
        # 1m = ~1440 bars/day, 365 days
        annual_trades = len(trades) * 365 / len(df) * 1440
        
        results.append((sd, atr, m, len(trades), wr, pf, gross, costs, net, annual_trades))
        
        if len(trades) >= 50:
            print(f"SD={sd}, ATR={atr}: Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Trades={len(trades)} ({annual_trades:.0f}/yr), Net={net:+.1f}%")

# Summary
print()
print("=" * 90)
print("BEST RESULTS")
print("=" * 90)

# Positive return
positive = [r for r in results if r[6] > 0]
if positive:
    print("\n>> POSITIVE RETURN:")
    for r in sorted(positive, key=lambda x: x[6], reverse=True)[:5]:
        print(f"  SD={r[0]}, ATR={r[1]}: Return={r[6]:+.1f}%, WR={r[4]:.0f}%, PF={r[5]:.2f}, Trades={r[3]} ({r[9]:.0f}/yr)")

# Win rate > 50
good_wr = [r for r in results if r[4] > 50]
if good_wr:
    print("\n>> WIN RATE > 50%:")
    for r in sorted(good_wr, key=lambda x: x[4], reverse=True)[:5]:
        print(f"  SD={r[0]}, ATR={r[1]}: WR={r[4]:.0f}%, Return={r[6]:+.1f}%, PF={r[5]:.2f}, Trades={r[3]}")

# PF > 1
good_pf = [r for r in results if r[5] > 1.0]
if good_pf:
    print("\n>> PROFIT FACTOR > 1.0:")
    for r in sorted(good_pf, key=lambda x: x[5], reverse=True)[:5]:
        print(f"  SD={r[0]}, ATR={r[1]}: PF={r[5]:.2f}, WR={r[4]:.0f}%, Return={r[6]:+.1f}%, Trades={r[3]}")

# Best net return
print("\n>> BEST NET RETURN (500+ trades):")
for r in sorted([x for x in results if x[3] >= 500], key=lambda x: x[8], reverse=True)[:5]:
    print(f"  SD={r[0]}, ATR={r[1]}: Net={r[8]:+.1f}% (Gross={r[6]:+.1f}% - Costs={r[7]:.1f}%), WR={r[4]:.0f}%, PF={r[5]:.2f}, Trades={r[3]}")
