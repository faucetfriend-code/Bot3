#!/usr/bin/env python3
"""VWAP Scalping Extended Parameter Sweep"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
import time

sys.path.insert(0, str(Path(__file__).parent.parent))

STORAGE_ROOT = Path("G:/Candle Data")
COST_PER_TRADE = 0.30

print("Loading data...")
df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
df.columns = [c.capitalize() for c in df.columns]
df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
print(f"Loaded {len(df):,} bars")

print("Computing indicators...")
from strategies import (
    apply_butterworth, compute_vwap_rolling, compute_atr, 
    compute_adx, compute_rsi, compute_ema
)

fc = apply_butterworth(df["Close"], 0.10)
vwap, vs = compute_vwap_rolling(df["High"], df["Low"], fc, df["Volume"], 20)
atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
adx = compute_adx(df["High"], df["Low"], df["Close"], 14)
rsi = compute_rsi(fc, 14)
ema_f = compute_ema(fc, 9)
ema_m = compute_ema(fc, 20)
vol_avg = df["Volume"].rolling(20).mean()

prices = df["Close"].values
highs = df["High"].values
lows = df["Low"].values
opens = df["Open"].values
volumes = df["Volume"].values
vwap_arr = vwap.values
atr_arr = atr.values
adx_arr = adx.values
rsi_arr = rsi.values
ema_f_arr = ema_f.values
ema_m_arr = ema_m.values
vol_avg_arr = vol_avg.values
n = len(prices)

print(f"Starting extended sweep with {n} bars...")

results = []
start = time.time()

# Extended parameter grid with more variations
params_list = []

# Try many combinations
for sd in [1.0, 1.2, 1.5, 1.8, 2.0, 2.2, 2.5, 3.0]:
    for atr_stop in [0.3, 0.5, 0.7, 1.0, 1.5]:
        for atr_target in [1.5, 2.0, 2.5, 3.0]:
            for adx_max in [20, 25, 30, 35, 40, 50]:
                for rsi_max in [40, 50, 60, 70, 80]:
                    for vol_mult in [1.0, 1.5, 2.0]:
                        for use_trend in [True, False]:
                            # Skip unrealistic combinations
                            if atr_target <= atr_stop:
                                continue
                            params_list.append({
                                "sd": sd, "atr_stop": atr_stop, "atr_target": atr_target,
                                "adx": adx_max, "rsi": rsi_max, "vol": vol_mult, 
                                "tf": use_trend
                            })

print(f"Testing {len(params_list)} combinations...")

COST = 0.0015
count = 0

for params in params_list:
    count += 1
    sd = params["sd"]
    atr_stop = params["atr_stop"]
    atr_target = params["atr_target"]
    adx_max = params["adx"]
    rsi_max = params["rsi"]
    vol_mult = params["vol"]
    use_trend = params["tf"]
    
    equity = np.ones(n)
    curr = 1.0
    in_pos = False
    side = ""
    entry_eq = 1.0
    sl = 0.0
    tp = 0.0
    closed = []
    
    for i in range(1, n):
        vw = vwap_arr[i]
        at = atr_arr[i]
        adv = adx_arr[i]
        r = rsi_arr[i]
        p_price = prices[i]
        p0 = prices[i-1]
        o = opens[i]
        v = volumes[i]
        va = vol_avg_arr[i]
        ef = ema_f_arr[i]
        em = ema_m_arr[i]
        
        if np.isnan(vw) or np.isnan(at) or np.isnan(adv) or np.isnan(r):
            equity[i] = curr
            continue
        
        is_bull = ef > em
        is_bear = ef < em
        above = p_price > vw
        below = p_price < vw
        vol_surge = v >= vol_mult * va
        ranging = adv < adx_max
        rsi_long = r < rsi_max
        rsi_short = r > (100.0 - rsi_max)
        
        if in_pos:
            curr *= p_price / p0
            if side == "long":
                if lows[i] <= sl or highs[i] >= tp:
                    curr *= (1 - COST)
                    closed.append(curr / entry_eq - 1)
                    in_pos = False
            else:
                if highs[i] >= sl or lows[i] <= tp:
                    curr *= (1 - COST)
                    closed.append(curr / entry_eq - 1)
                    in_pos = False
        else:
            long_sig = False
            short_sig = False
            
            if ranging:
                if rsi_long and above and prices[i-1] < vwap_arr[i-1]:
                    if use_trend:
                        if is_bull and vol_surge:
                            long_sig = True
                    else:
                        long_sig = True
                
                if rsi_short and below and prices[i-1] > vwap_arr[i-1]:
                    if use_trend:
                        if is_bear and vol_surge:
                            short_sig = True
                    else:
                        short_sig = True
            
            if long_sig:
                curr *= (1 - COST)
                entry_eq = curr
                sl = o - atr_stop * at
                tp = o + atr_target * at
                in_pos = True
                side = "long"
            elif short_sig:
                curr *= (1 - COST)
                entry_eq = curr
                sl = o + atr_stop * at
                tp = o - atr_target * at
                in_pos = True
                side = "short"
        
        equity[i] = curr
    
    if in_pos:
        curr *= (1 - COST)
        closed.append(curr / entry_eq - 1)
    
    if len(closed) >= 50:
        wins = [t for t in closed if t > 0]
        losses = [t for t in closed if t <= 0]
        wr = len(wins) / len(closed) * 100
        gp = sum(wins) * 100
        gl = abs(sum(losses)) * 100
        pf = gp / gl if gl > 0 else 999
        gross = (equity[-1] / 1.0 - 1) * 100
        net = gross - len(closed) * COST_PER_TRADE
        
        results.append({
            **params,
            "trades": len(closed),
            "wr": wr,
            "pf": pf,
            "gross": gross,
            "net": net
        })
        
        if count % 500 == 0:
            elapsed = time.time() - start
            print(f"  Progress: {count}/{len(params_list)} ({elapsed/60:.1f} min)")

elapsed = time.time() - start
print(f"\n\nCompleted {len(results)} valid tests in {elapsed/60:.1f} min")

# Sort by trade count
results.sort(key=lambda x: x["trades"], reverse=True)

# Show top 20
print("\n" + "="*110)
print("TOP 20 PARAMETER COMBINATIONS (by trade count)")
print("="*110)
print(f"\n{'SD':>4} {'ATR_S':>6} {'ATR_T':>6} {'ADX':>5} {'RSI':>5} {'Vol':>5} {'TF':>4} {'Trades':>7} {'WR%':>6} {'PF':>6} {'Gross%':>9} {'Net%':>9}")
print("-"*110)

for r in results[:20]:
    status = "✓" if (r["trades"] >= 1000 and r["wr"] >= 50 and r["net"] > 0) else ""
    print(f"{r['sd']:>4.1f} {r['atr_stop']:>6.1f} {r['atr_target']:>6.1f} {r['adx']:>5.0f} {r['rsi']:>5.0f} {r['vol']:>5.1f} {str(r['tf']):>4} {r['trades']:>7} {r['wr']:>6.1f} {r['pf']:>6.2f} {r['gross']:>9.2f} {r['net']:>9.2f} {status}")

# Filtered
filtered = [r for r in results if r["trades"] >= 1000 and r["wr"] >= 50 and r["net"] > 0]
print(f"\n\nQualifying (>1000 trades, >50% WR, +net): {len(filtered)}")

if filtered:
    print("\n" + "="*110)
    print("ALL QUALIFYING COMBINATIONS")
    print("="*110)
    for r in filtered:
        print(f"SD={r['sd']:.1f}, ATR_S={r['atr_stop']:.1f}, ATR_T={r['atr_target']:.1f}, ADX={r['adx']:.0f}, RSI={r['rsi']:.0f}, Vol={r['vol']:.1f}, TF={r['tf']} | Trades={r['trades']}, WR={r['wr']:.1f}%, PF={r['pf']:.2f}, Net={r['net']:+.2f}%")

# Show best by WR
print("\n" + "="*110)
print("TOP 10 BY WIN RATE (min 500 trades)")
print("="*110)
by_wr = sorted([r for r in results if r["trades"] >= 500], key=lambda x: x["wr"], reverse=True)[:10]
for r in by_wr:
    print(f"SD={r['sd']:.1f}, ATR_S={r['atr_stop']:.1f}, ATR_T={r['atr_target']:.1f}, ADX={r['adx']:.0f}, RSI={r['rsi']:.0f}, Vol={r['vol']:.1f}, TF={r['tf']} | Trades={r['trades']}, WR={r['wr']:.1f}%, PF={r['pf']:.2f}, Net={r['net']:+.2f}%")

# Show best by net
print("\n" + "="*110)
print("TOP 10 BY NET RETURN (min 500 trades)")
print("="*110)
by_net = sorted([r for r in results if r["trades"] >= 500], key=lambda x: x["net"], reverse=True)[:10]
for r in by_net:
    print(f"SD={r['sd']:.1f}, ATR_S={r['atr_stop']:.1f}, ATR_T={r['atr_target']:.1f}, ADX={r['adx']:.0f}, RSI={r['rsi']:.0f}, Vol={r['vol']:.1f}, TF={r['tf']} | Trades={r['trades']}, WR={r['wr']:.1f}%, PF={r['pf']:.2f}, Net={r['net']:+.2f}%")
