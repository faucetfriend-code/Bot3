#!/usr/bin/env python3
"""
Comprehensive VWAP 5m parameter sweep with SD=2.5 fixed.
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

# Import needed functions
from strategies import (
    apply_butterworth, compute_vwap_rolling, compute_macd, 
    compute_atr, compute_rsi, compute_metrics, COST_PER_SIDE
)

def run_vwap_custom(df, cutoff, sd_threshold, atr_stop, macd_fast=12, macd_slow=26, macd_sig=9, vwap_period=20, rsi_filter=None, vol_filter=None):
    """Custom VWAP with flexible parameters."""
    fc = apply_butterworth(df["Close"], cutoff)
    vwap, vs = compute_vwap_rolling(df["High"], df["Low"], fc, df["Volume"], vwap_period)
    _, _, mh = compute_macd(fc, macd_fast, macd_slow, macd_sig)
    atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
    rsi = compute_rsi(fc, 14)
    vol_avg = df["Volume"].rolling(20).mean()

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
    tp_ref = [0.0]
    closed_trades = []

    bar_dur = df.index[1] - df.index[0] if len(df) > 1 else pd.Timedelta(hours=1)

    for i in range(1, n):
        vw = vwap.iloc[i]; vsd = vs.iloc[i]
        mhi = mh.iloc[i]; at = atr.iloc[i]
        p = prices[i]; p0 = prices[i-1]
        
        rsi_val = rsi.iloc[i] if i >= 20 else 50
        vol_mult = df["Volume"].iloc[i] / vol_avg.iloc[i] if i >= 20 else 1.0

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
            # Volume filter check
            if vol_filter and vol_mult < vol_filter:
                equity_arr[i] = curr_equity
                continue
                
            # RSI filter check
            if rsi_filter == 30 and rsi_val > 30:
                pass  # No RSI filter, proceed
            elif rsi_filter == 40 and rsi_val > 40:
                pass  # No RSI filter, proceed
                
            if p < lower_band and mhi < 0:
                curr_equity *= (1.0 - COST_PER_SIDE)
                entry_eq[0] = curr_equity
                sl[0] = p - atr_stop * at
                tp_ref[0] = float("inf")
                in_pos[0] = True
                side[0] = "long"
            elif p > upper_band and mhi > 0:
                curr_equity *= (1.0 - COST_PER_SIDE)
                entry_eq[0] = curr_equity
                sl[0] = p + atr_stop * at
                tp_ref[0] = 0.0
                in_pos[0] = True
                side[0] = "short"

        equity_arr[i] = curr_equity

    if in_pos[0]:
        curr_equity *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
        equity_arr[-1] = curr_equity

    return pd.Series(equity_arr, index=df.index), closed_trades


# Load data
df = load_parquet("BTCUSDT", "5m")
df.columns = [c.capitalize() for c in df.columns]
df = df[(df.index >= '2023-01-01') & (df.index < '2025-01-01')]
print(f"Loaded {len(df)} bars (2023-2024)")
print()

SD = 2.5
results = []

# Test 1: ATR Stop
print("=" * 100)
print("TEST 1: ATR STOP VALUES (SD=2.5, Cutoff=0.10)")
print("=" * 100)
for atr in [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0]:
    eq, trades = run_vwap_custom(df, cutoff=0.10, sd_threshold=SD, atr_stop=atr)
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
    print(f"ATR={atr}: Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Trades={len(trades)}, Net={net:+.1f}%")
    results.append(('ATR', atr, m, len(trades), wr, pf, gross, costs, net))

# Test 2: Cutoff
print()
print("=" * 100)
print("TEST 2: BUTTERWORTH CUTOFF (SD=2.5, ATR=7.0)")
print("=" * 100)
for cutoff in [0.03, 0.05, 0.07, 0.10, 0.12, 0.15, 0.20, 0.25, 0.30]:
    eq, trades = run_vwap_custom(df, cutoff=cutoff, sd_threshold=SD, atr_stop=7.0)
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
    print(f"Cutoff={cutoff:.2f}: Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Trades={len(trades)}, Net={net:+.1f}%")
    results.append(('CUTOFF', cutoff, m, len(trades), wr, pf, gross, costs, net))

# Test 3: MACD settings
print()
print("=" * 100)
print("TEST 3: MACD SETTINGS (SD=2.5, ATR=7.0, Cutoff=0.10)")
print("=" * 100)
macd_combos = [(8, 17, 9), (12, 26, 9), (5, 35, 5), (6, 19, 9), (9, 21, 9)]
for mf, ms, mg in macd_combos:
    eq, trades = run_vwap_custom(df, cutoff=0.10, sd_threshold=SD, atr_stop=7.0, macd_fast=mf, macd_slow=ms, macd_sig=mg)
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
    print(f"MACD({mf},{ms},{mg}): Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Trades={len(trades)}, Net={net:+.1f}%")
    results.append((f'MACD({mf},{ms},{mg})', f'{mf},{ms},{mg}', m, len(trades), wr, pf, gross, costs, net))

# Test 4: VWAP period
print()
print("=" * 100)
print("TEST 4: VWAP PERIOD (SD=2.5, ATR=7.0, Cutoff=0.10)")
print("=" * 100)
for vp in [10, 15, 20, 25, 30]:
    eq, trades = run_vwap_custom(df, cutoff=0.10, sd_threshold=SD, atr_stop=7.0, vwap_period=vp)
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
    print(f"VWAP Period={vp}: Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Trades={len(trades)}, Net={net:+.1f}%")
    results.append(('VWAP_PERIOD', vp, m, len(trades), wr, pf, gross, costs, net))

# Test 5: Volume filter
print()
print("=" * 100)
print("TEST 5: VOLUME FILTER (SD=2.5, ATR=7.0, Cutoff=0.10)")
print("=" * 100)
for vf in [None, 1.1, 1.2, 1.5, 2.0]:
    eq, trades = run_vwap_custom(df, cutoff=0.10, sd_threshold=SD, atr_stop=7.0, vol_filter=vf)
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
    print(f"Vol Filter={vf}: Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Trades={len(trades)}, Net={net:+.1f}%")
    results.append(('VOL_FILTER', vf, m, len(trades), wr, pf, gross, costs, net))

# Summary
print()
print("=" * 100)
print("BEST RESULTS SUMMARY")
print("=" * 100)

print("\n>> POSITIVE RETURN:")
for r in sorted([x for x in results if x[6] > 0], key=lambda x: x[6], reverse=True)[:5]:
    print(f"  {r[0]}={r[1]}: Return={r[6]:+.1f}%, WR={r[4]:.0f}%, PF={r[5]:.2f}, Trades={r[3]}")

print("\n>> WIN RATE > 50%:")
for r in sorted([x for x in results if x[4] > 50], key=lambda x: x[4], reverse=True)[:5]:
    print(f"  {r[0]}={r[1]}: WR={r[4]:.0f}%, Return={r[6]:+.1f}%, PF={r[5]:.2f}")

print("\n>> PROFIT FACTOR > 1.0:")
for r in sorted([x for x in results if x[5] > 1.0], key=lambda x: x[5], reverse=True)[:5]:
    print(f"  {r[0]}={r[1]}: PF={r[5]:.2f}, WR={r[4]:.0f}%, Return={r[6]:+.1f}%")

print("\n>> BEST NET RETURN (after costs):")
for r in sorted(results, key=lambda x: x[7], reverse=True)[:5]:
    print(f"  {r[0]}={r[1]}: Net={x[8]:+.1f}% (Gross={r[6]:+.1f}% - Costs={r[7]:.1f}%)")
