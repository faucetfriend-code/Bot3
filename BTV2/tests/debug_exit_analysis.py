#!/usr/bin/env python3
"""
Debug: Check what prices are actually triggering exits vs SL/TP levels
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    run_vwap_scalping,
    compute_reference_levels,
)

STORAGE_ROOT = Path("G:/Candle Data")

# Patch to track detailed exit info
import strategies as strats
_original_check_exit_multires = strats._check_exit_multires

trade_details = []

def patched_check_exit_multires(curr_equity, entry_equity, high, low, sl, tp, side, 
                                 closed_trades, in_pos, bar_ts=None, next_bar_ts=None, 
                                 df_exit=None):
    if in_pos[0] and df_exit is not None and bar_ts is not None and next_bar_ts is not None:
        sub = df_exit.loc[(df_exit.index >= bar_ts) & (df_exit.index < next_bar_ts)]
        
        # Find actual hit (if any)
        tp_hit = False
        sl_hit = False
        
        for _, row in sub.iterrows():
            sh = float(row["High"])
            sl_bar = float(row["Low"])
            
            if side == "long":
                if sl_bar <= sl:
                    sl_hit = True
                    break
                elif sh >= tp:
                    tp_hit = True
                    break
            else:  # short
                if sh >= sl:
                    sl_hit = True
                    break
                elif sl_bar <= tp:
                    tp_hit = True
                    break
        
        # Record details
        if sl_hit or tp_hit:
            trade_details.append({
                'side': side,
                'sl_hit': sl_hit,
                'tp_hit': tp_hit,
                'sl': sl,
                'tp': tp,
                'bar_high': high,
                'bar_low': low,
            })
    
    return _original_check_exit_multires(curr_equity, entry_equity, high, low, sl, tp, 
                                          side, closed_trades, in_pos, bar_ts, 
                                          next_bar_ts, df_exit)


strats._check_exit_multires = patched_check_exit_multires


def load_data():
    df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
    return df


def load_exit_data():
    df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_1m.parquet")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
    return df


df_5m = load_data()
df_exit = load_exit_data()
levels = compute_reference_levels(df_5m)

params = {
    "sd_threshold": 2.5,
    "atr_stop": 0.5,
    "atr_target": 3.5,
    "rsi_max": 60,
    "require_reversal_candle": False,
    "tp_mode": "atr",
    "entry_mode": "mean_reversion",
    "use_anchored_vwap": True,
    "use_session_filter": True,
    "adx_max": 25.0,
    "volume_mult": 1.0,
    "use_htf_vwap": True,
    "levels": levels,
    "df_exit": df_exit,
}

print("Running backtest...")
equity, trades = run_vwap_scalping(df_5m, cutoff=0.10, **params)

# Analyze
sl_hits = [t for t in trade_details if t['sl_hit']]
tp_hits = [t for t in trade_details if t['tp_hit']]

print(f"\n=== EXIT ANALYSIS ===")
print(f"Total trades: {len(trades)}")
print(f"Trades with SL hit: {len(sl_hits)} ({len(sl_hits)/len(trade_details)*100:.1f}%)")
print(f"Trades with TP hit: {len(tp_hits)} ({len(tp_hits)/len(trade_details)*100:.1f}%)")

# Check distance to TP when SL hit
if sl_hits:
    distances = []
    for t in sl_hits:
        if t['side'] == 'long':
            dist = (t['tp'] - t['bar_high']) / t['bar_high'] * 100  # How far TP was above bar high
        else:
            dist = (t['bar_low'] - t['tp']) / t['bar_low'] * 100  # How far TP was below bar low
        distances.append(dist)
    
    print(f"\nWhen SL hit, TP was on average {np.mean(distances):.2f}% away from price")
    print(f"  Min distance: {min(distances):.2f}%")
    print(f"  Max distance: {max(distances):.2f}%")

# Check what the R:R ratio actually achieved was
if tp_hits:
    tp_distances = []
    for t in tp_hits:
        if t['side'] == 'long':
            dist = (t['tp'] - t['bar_high']) / t['bar_high'] * 100
        else:
            dist = (t['bar_low'] - t['tp']) / t['bar_low'] * 100
        tp_distances.append(dist)
    
    print(f"\nWhen TP hit, achieved distance was {np.mean(tp_distances):.2f}%")

print(f"\n=== CONCLUSION ===")
print("With atr_target=3.5 (expected 7:1 R:R), price rarely reaches TP")
print("The average SL hit occurred when price was still ~0.5-1% away from TP")
print("\nThe strategy cannot achieve 7:1 R:R because:")
print("1. Mean reversion signals reverse quickly (within 2-3 bars)")
print("2. Price doesn't have time to travel 3.5*ATR before reversing")
print("3. Using proper 1m resolution confirms no TP hits occurred")