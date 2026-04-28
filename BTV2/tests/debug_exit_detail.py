#!/usr/bin/env python3
"""
Debug the exit logic in detail - see what the actual SL/TP values are
and why TP is never hit.
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
    return df


def load_exit_data():
    df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_1m.parquet")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
    return df


# Patch to track entry and exit
trade_debug = []


def patched_check_exit_multires(curr_equity, entry_equity, high, low, sl, tp, side, 
                                 closed_trades, in_pos, bar_ts=None, next_bar_ts=None, 
                                 df_exit=None):
    global trade_debug
    
    # Before exit check
    if in_pos[0]:
        # Calculate current P&L
        pnl_pct = (curr_equity / entry_equity - 1.0) * 100
        
        # Determine exit reason
        if side == "long":
            if low <= sl:
                reason = "SL"
                hit = True
            elif high >= tp:
                reason = "TP"
                hit = True
            else:
                reason = f"no_hit(h={high:.0f},l={low:.0f},sl={sl:.0f},tp={tp:.0f})"
                hit = False
        else:  # short
            if high >= sl:
                reason = "SL"
                hit = True
            elif low <= tp:
                reason = "TP"
                hit = True
            else:
                reason = f"no_hit(h={high:.0f},l={low:.0f},sl={sl:.0f},tp={tp:.0f})"
                hit = False
        
        # Track this check
        trade_debug.append({
            'pnl': pnl_pct,
            'reason': reason,
            'side': side,
        })
        
        # If no hit, track what the prices were
        if not hit:
            # Print for debugging
            print(f"  {side} no hit: high={high:.2f}, low={low:.2f}, sl={sl:.2f}, tp={tp:.2f}, pnl={pnl_pct:.2f}%")
    
    # Original function call
    from strategies import _check_exit
    return _check_exit(curr_equity, entry_equity, high, low, sl, tp, side, closed_trades, in_pos)


# Apply patch
import strategies as strats
strats._check_exit_multires = patched_check_exit_multires


def main():
    global trade_debug
    trade_debug = []
    
    print("Loading data...")
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
    
    print("\nRunning backtest...")
    equity, trades = run_vwap_scalping(df_5m, cutoff=0.10, **params)
    
    # Analyze
    print(f"\nTotal trades recorded: {len(trades)}")
    print(f"Total exit checks: {len(trade_debug)}")
    
    # Count exit reasons
    reasons = {}
    for t in trade_debug:
        r = t['reason'].split('(')[0]  # Just get SL/TP/no_hit
        reasons[r] = reasons.get(r, 0) + 1
    
    print("\nExit check results:")
    for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {r}: {c}")
    
    # Look at "no_hit" cases in detail
    no_hits = [t for t in trade_debug if t['reason'].startswith('no_hit')]
    if no_hits:
        print(f"\nAnalyzing {len(no_hits)} 'no_hit' cases:")
        
        # Group by side
        long_nh = [t for t in no_hits if t['side'] == 'long']
        short_nh = [t for t in no_hits if t['side'] == 'short']
        
        print(f"  Long: {len(long_nh)}, Short: {len(short_nh)}")
        
        # For longs: TP would need high >= tp. If no hit, means high < tp
        # Let's check the PnL at these check points
        long_pnls = [t['pnl'] for t in long_nh]
        short_pnls = [t['pnl'] for t in short_nh]
        
        print(f"\n  Long 'no_hit' PnL stats: min={min(long_pnls):.2f}%, max={max(long_pnls):.2f}%, avg={np.mean(long_pnls):.2f}%")
        print(f"  Short 'no_hit' PnL stats: min={min(short_pnls):.2f}%, max={max(short_pnls):.2f}%, avg={np.mean(short_pnls):.2f}%")
        
        # Show examples
        print("\n  Sample long 'no_hit' exit checks:")
        for t in long_nh[:3]:
            print(f"    {t['reason']}, pnl={t['pnl']:.2f}%")
        
        print("\n  Sample short 'no_hit' exit checks:")
        for t in short_nh[:3]:
            print(f"    {t['reason']}, pnl={t['pnl']:.2f}%")


if __name__ == "__main__":
    main()