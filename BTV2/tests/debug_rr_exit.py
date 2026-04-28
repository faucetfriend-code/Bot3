#!/usr/bin/env python3
"""
Debug script to understand why high R:R isn't working.
Check actual trade outcomes and exit reasons.
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    run_vwap_scalping,
    compute_reference_levels,
    INTERVAL_BARS_PER_YEAR,
)

STORAGE_ROOT = Path("G:/Candle Data")
COST_PER_SIDE = 0.0015


def load_data():
    df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
    print(f"Loaded {len(df):,} bars (2024)")
    return df


def load_exit_data():
    df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_1m.parquet")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
    print(f"Loaded {len(df):,} bars (1m 2024)")
    return df


# Monkey-patch to track exits
_original_check_exit = None
_original_check_exit_multires = None

trade_log = []


def patched_check_exit(curr_equity, entry_equity, high, low, sl, tp, side, closed_trades, in_pos):
    global trade_log
    
    if in_pos[0]:
        # Store exit info before modifying
        prev_in_pos = in_pos[0]
        result_equity = curr_equity
        
        trade = curr_equity / entry_equity - 1.0
        if side == "long":
            exit_price = high if high >= tp else (low if low <= sl else "unknown")
        else:
            exit_price = low if low <= tp else (high if high >= sl else "unknown")
        
        # Determine exit reason
        if side == "long":
            if low <= sl:
                reason = "SL"
            elif high >= tp:
                reason = "TP"
            else:
                reason = "unknown"
        else:
            if high >= sl:
                reason = "SL"
            elif low <= tp:
                reason = "TP"
            else:
                reason = "unknown"
        
        trade_log.append({
            'side': side,
            'result': trade * 100,  # percentage
            'reason': reason,
            'sl': sl,
            'tp': tp,
        })
    
    # Original logic
    hit = False
    if side == "long":
        if low <= sl:
            hit = True
        elif high >= tp:
            hit = True
    else:
        if high >= sl:
            hit = True
        elif low <= tp:
            hit = True
    
    if hit:
        curr_equity *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_equity - 1.0)
        in_pos[0] = False
    
    return curr_equity


def patched_check_exit_multires(curr_equity, entry_equity, high, low, sl, tp, side, 
                                 closed_trades, in_pos, bar_ts=None, next_bar_ts=None, 
                                 df_exit=None):
    global trade_log
    
    if in_pos[0]:
        trade = curr_equity / entry_equity - 1.0
        
        # Determine exit reason
        if side == "long":
            if low <= sl:
                reason = "SL"
            elif high >= tp:
                reason = "TP"
            else:
                reason = "unknown"
        else:
            if high >= sl:
                reason = "SL"
            elif low <= tp:
                reason = "TP"
            else:
                reason = "unknown"
        
        trade_log.append({
            'side': side,
            'result': trade * 100,
            'reason': reason,
            'sl': sl,
            'tp': tp,
        })
    
    # Call original
    from strategies import _check_exit
    return _check_exit(curr_equity, entry_equity, high, low, sl, tp, side, closed_trades, in_pos)


# Apply patches
import strategies as strats
strats._check_exit = patched_check_exit
strats._check_exit_multires = patched_check_exit_multires


def main():
    global trade_log
    trade_log = []
    
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
    
    print("\nRunning backtest with 7:1 R:R...")
    equity, trades = run_vwap_scalping(df_5m, cutoff=0.10, **params)
    
    # Analyze trade log
    if trade_log:
        wins = [t for t in trade_log if t['result'] > 0]
        losses = [t for t in trade_log if t['result'] <= 0]
        
        print(f"\n--- Trade Analysis ---")
        print(f"Total trades: {len(trade_log)}")
        print(f"Wins: {len(wins)}, Losses: {len(losses)}")
        print(f"Win rate: {len(wins) / len(trade_log) * 100:.1f}%")
        
        tp_hits = [t for t in trade_log if t['reason'] == 'TP']
        sl_hits = [t for t in trade_log if t['reason'] == 'SL']
        unknown = [t for t in trade_log if t['reason'] == 'unknown']
        
        print(f"\nExit reasons:")
        print(f"  TP hit: {len(tp_hits)} ({len(tp_hits)/len(trade_log)*100:.1f}%)")
        print(f"  SL hit: {len(sl_hits)} ({len(sl_hits)/len(trade_log)*100:.1f}%)")
        print(f"  Unknown: {len(unknown)}")
        
        if wins:
            avg_win = sum(t['result'] for t in wins) / len(wins)
            print(f"\nAverage win: {avg_win:+.2f}%")
            print(f"Expected: ~3.5% (atr_target * ATR)")
        
        if losses:
            avg_loss = sum(t['result'] for t in losses) / len(losses)
            print(f"Average loss: {avg_loss:.2f}%")
            print(f"Expected: ~0.5% (atr_stop * ATR)")
        
        # Show some examples
        print("\n--- Sample Trades ---")
        for i, t in enumerate(trade_log[:5]):
            print(f"  {t['side']:5} | Result: {t['result']:+6.2f}% | Exit: {t['reason']:7} | SL: {t['sl']:.4f} | TP: {t['tp']:.4f}")
        
        # Check equity curve for issues
        print(f"\nEquity: start={equity.iloc[0]:.4f}, end={equity.iloc[-1]:.4f}")
    else:
        print("No trades recorded!")


if __name__ == "__main__":
    main()