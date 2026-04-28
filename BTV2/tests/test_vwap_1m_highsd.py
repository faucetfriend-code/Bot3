#!/usr/bin/env python3
"""
Test VWAP on 1m with very high SD values.
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


df = load_parquet("BTCUSDT", "1m")
df.columns = [c.capitalize() for c in df.columns]
df = df[(df.index >= '2024-01-01') & (df.index < '2025-01-01')]
print(f"Loaded {len(df)} bars (2024)")
print()

print("=" * 90)
print("VWAP 1m - HIGHER SD VALUES")
print("=" * 90)

results = []
for sd in [6.0, 7.0, 8.0, 9.0, 10.0, 12.0, 15.0, 20.0]:
    eq, trades = run_vwap_simple(df, cutoff=0.10, sd_threshold=sd, atr_stop=7.0)
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
    
    results.append((sd, m, len(trades), wr, pf, gross, costs, net))
    print(f"SD={sd}: Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Trades={len(trades)}, Net={net:+.1f}%")

# Find best with reasonable trades
print()
print("BEST (min 10 trades):")
for r in sorted([x for x in results if x[2] >= 10], key=lambda x: x[6], reverse=True):
    print(f"  SD={r[0]}: Net={r[7]:+.1f}%, Return={r[5]:+.1f}%, WR={r[3]:.0f}%, PF={r[4]:.2f}, Trades={r[2]}")
