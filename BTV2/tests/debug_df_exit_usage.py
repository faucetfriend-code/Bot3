#!/usr/bin/env python3
"""
Debug: Is df_exit actually being used in _check_exit_multires?
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


def load_data():
    df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
    df.columns = [c.capitalize() for c in df.columns]
    # Use more data to get trades
    df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
    return df


def load_exit_data():
    df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_1m.parquet")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
    return df


# Patch to see what's happening
import strategies as strats

_original_check_exit_multires = strats._check_exit_multires

multires_hits = 0
total_calls = 0
no_sub_bars = 0

def patched_check_exit_multires(curr_equity, entry_equity, high, low, sl, tp, side, 
                                 closed_trades, in_pos, bar_ts=None, next_bar_ts=None, 
                                 df_exit=None):
    global multires_hits, total_calls, no_sub_bars
    
    total_calls += 1
    
    if df_exit is not None and bar_ts is not None and next_bar_ts is not None:
        sub = df_exit.loc[(df_exit.index >= bar_ts) & (df_exit.index < next_bar_ts)]
        if len(sub) > 0:
            multires_hits += 1
            # Check if any sub-bar hit SL or TP
            for _, row in sub.iterrows():
                sh = float(row["High"])
                sl_bar = float(row["Low"])
                if side == "long":
                    if sl_bar <= sl:
                        print(f"  LONG TP hit via 1m! sl={sl:.2f}, tp={tp:.2f}, hit_bar_low={sl_bar:.2f}")
                        break
                    elif sh >= tp:
                        print(f"  LONG TP hit via 1m! high={sh:.2f} >= tp={tp:.2f}")
                        break
                else:
                    if sh >= sl:
                        print(f"  SHORT TP hit via 1m! high={sh:.2f} >= sl={sl:.2f}")
                        break
                    elif sl_bar <= tp:
                        print(f"  SHORT TP hit via 1m!")
                        break
        else:
            no_sub_bars += 1
    
    return _original_check_exit_multires(curr_equity, entry_equity, high, low, sl, tp, 
                                          side, closed_trades, in_pos, bar_ts, 
                                          next_bar_ts, df_exit)


strats._check_exit_multires = patched_check_exit_multires


# Run test
df_5m = load_data()
df_exit = load_exit_data()

print(f"5m data: {len(df_5m)} bars")
print(f"1m data: {len(df_exit)} bars")
print()

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

print(f"\nTotal trades: {len(trades)}")
print(f"Total multires calls: {total_calls}")
print(f"Calls with sub-bars: {multires_hits}")
print(f"Calls with NO sub-bars: {no_sub_bars}")