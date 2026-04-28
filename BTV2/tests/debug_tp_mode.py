#!/usr/bin/env python3
"""
Quick Debug Test - Check if strategy is generating reasonable trades
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


def main():
    # Load small sample
    df_5m = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
    df_5m.columns = [c.capitalize() for c in df_5m.columns]
    df_5m = df_5m[(df_5m.index >= "2024-01-01") & (df_5m.index < "2024-02-01")]
    print(f"Loaded {len(df_5m)} bars")

    df_1m = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_1m.parquet")
    df_1m.columns = [c.capitalize() for c in df_1m.columns]
    df_1m = df_1m[(df_1m.index >= "2024-01-01") & (df_1m.index < "2024-02-01")]

    levels = compute_reference_levels(df_5m)

    # Test with baseline params (from vwap_yearly_backtest.py)
    params = {
        "sd_threshold": 2.0,
        "entry_mode": "bull_pullback",
        "adx_max": 30,
        "rsi_max": 50,
        "volume_mult": 2.0,
        "atr_stop": 2.0,
        "atr_target": 3.5,
        "pullback_bars": 3,
        "use_session_filter": True,
        "use_htf_vwap": True,
        "use_htf_ema": False,
        "htf_adx_max": 25,
        "use_trailing_stop": True,
        "trailing_atr": 1.2,
        "use_anchored_vwap": True,
        "require_reversal_candle": True,
    }

    print("\n=== Test 1: tp_mode='atr' ===")
    params1 = params.copy()
    params1["tp_mode"] = "atr"
    equity, trades = run_vwap_scalping(df_5m, cutoff=0.10, df_exit=df_1m, levels=levels, **params1)
    print(f"Trades: {len(trades)}")
    if trades:
        print(f"First 10 trades: {trades[:10]}")
        print(f"Wins: {sum(1 for t in trades if t > 0)}, Losses: {sum(1 for t in trades if t < 0)}")
        print(f"Total: {sum(trades)*100:.2f}%")

    print("\n=== Test 2: tp_mode='vwap' ===")
    params2 = params.copy()
    params2["tp_mode"] = "vwap"
    equity, trades = run_vwap_scalping(df_5m, cutoff=0.10, df_exit=df_1m, levels=levels, **params2)
    print(f"Trades: {len(trades)}")
    if trades:
        print(f"First 10 trades: {trades[:10]}")
        print(f"Wins: {sum(1 for t in trades if t > 0)}, Losses: {sum(1 for t in trades if t < 0)}")
        print(f"Total: {sum(trades)*100:.2f}%")

    # Show equity curve
    print(f"\n=== Equity ===")
    print(f"Start: {equity.iloc[0]:.4f}, End: {equity.iloc[-1]:.4f}")
    print(f"Return: {(equity.iloc[-1]/equity.iloc[0]-1)*100:.2f}%")


if __name__ == "__main__":
    main()