#!/usr/bin/env python3
"""
Debug: check entry, SL, TP values to see actual R:R being used
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    run_vwap_scalping,
    compute_reference_levels,
    compute_atr,
    INTERVAL_BARS_PER_YEAR,
)

STORAGE_ROOT = Path("G:/Candle Data")


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


# Track entry details
entry_log = []
exit_log = []


# Store original functions
_original_check_exit = None


def patched_check_exit(curr_equity, entry_equity, high, low, sl, tp, side, closed_trades, in_pos):
    if in_pos[0] and entry_log:
        # Record exit
        entry = entry_log[-1]
        pnl = (curr_equity / entry_equity - 1.0) * 100
        
        # Determine exit reason
        if side == "long":
            if low <= sl:
                reason = "SL"
            elif high >= tp:
                reason = "TP"
            else:
                reason = "time"
        else:
            if high >= sl:
                reason = "SL"
            elif low <= tp:
                reason = "TP"
            else:
                reason = "time"
        
        exit_log.append({
            'side': side,
            'entry_price': entry['entry_price'],
            'sl': sl,
            'tp': tp,
            'pnl': pnl,
            'reason': reason,
            'bars_held': entry['bars_in_pos'],
        })
    
    # Call original
    return _original_check_exit(curr_equity, entry_equity, high, low, sl, tp, side, closed_trades, in_pos)


# We need to patch at the strategy level to capture entries
# Let's create a wrapper

_orig_vwap_scalping = None

def patched_vwap_scalping(df, cutoff, **params):
    global entry_log, exit_log
    entry_log = []
    exit_log = []
    
    # Run original but intercept entry/exit
    # Since we can't easily intercept entry, let's add instrumentation in a different way
    
    # Actually, let's just run the backtest and then trace manually
    # by running a modified strategy that logs
    
    # For now, let's just see what parameters are being passed
    print(f"Running with: sd={params.get('sd_threshold')}, atr_stop={params.get('atr_stop')}, "
          f"atr_target={params.get('atr_target')}, tp_mode={params.get('tp_mode')}")
    
    # Just run normally
    return _orig_vwap_scalping(df, cutoff, **params)


# Alternative approach: modify the strategy temporarily
import strategies as strats
_orig_vwap_scalping = strats.run_vwap_scalping

# Check ATR values to understand scale
df = load_data()
atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
print(f"\nBTC 5m ATR stats (2024):")
print(f"  Mean: ${atr.mean():.2f}")
print(f"  Min:  ${atr.min():.2f}")
print(f"  Max:  ${atr.max():.2f}")
print(f"  Median: ${atr.median():.2f}")

# With atr_stop=0.5 and atr_target=3.5
# Expected SL distance = 0.5 * ATR = ~$70
# Expected TP distance = 3.5 * ATR = ~$490
# R:R = 7:1

print(f"\nWith atr_stop=0.5, atr_target=3.5:")
print(f"  Expected SL: 0.5 * ${atr.mean():.2f} = ${0.5 * atr.mean():.2f}")
print(f"  Expected TP: 3.5 * ${atr.mean():.2f} = ${3.5 * atr.mean():.2f}")
print(f"  Expected R:R: 7:1")

# Now run backtest with logging
# Let's patch the entry logic to log entry prices

# We'll modify the strategy to return trade details
# Actually simpler: run backtest, then manually compute what would happen with true 7:1

print("\n" + "="*50)
print("Running backtest to see actual trade outcomes...")
print("="*50)

df_5m = df
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

equity, trades = _orig_vwap_scalping(df_5m, cutoff=0.10, **params)

print(f"\nResults: {len(trades)} trades")
print(f"Win rate: {sum(1 for t in trades if t > 0) / len(trades) * 100:.1f}%")
print(f"Average trade: {np.mean(trades) * 100:.2f}%")

# Show distribution
wins = [t * 100 for t in trades if t > 0]
losses = [t * 100 for t in trades if t <= 0]

print(f"\nWins: {len(wins)}, avg={np.mean(wins):.2f}%, max={max(wins):.2f}%")
print(f"Losses: {len(losses)}, avg={np.mean(losses):.2f}%, min={min(losses):.2f}%")

# The issue is clear: avg win is ~0.17% when we expected ~3.5%
# Let's see what the ATR scaling should be

print("\n" + "="*50)
print("Analysis: Why is avg win so small?")
print("="*50)

# With 7:1 R:R and 35% WR, we expect:
# If losses are ~0.5% (atr_stop * ATR), wins should be ~3.5%
# But actual is ~0.17%

# The issue: atr_target and atr_stop are being applied to the signal bar's close
# But the exit happens within a few bars, so price doesn't move far enough

# Let's verify by checking how long trades last
# Actually we saw from earlier: ~2 bars on average

print("\nHypothesis: Trades are closing in ~2 bars on average.")
print("With 5m bars, price doesn't have time to move 3.5*ATR to hit TP.")
print("\nSolution: Need tighter R:R that fits within ~5-10 bars of movement,")
print("OR need to hold trades longer (remove time-based exit)")

# Let's try a more realistic R:R that can be achieved
print("\n" + "="*50)
print("Testing with realistic R:R (2:1)")
print("="*50)

params2 = params.copy()
params2["atr_stop"] = 0.5
params2["atr_target"] = 1.0  # 2:1 R:R

equity2, trades2 = _orig_vwap_scalping(df_5m, cutoff=0.10, **params2)

wins2 = [t * 100 for t in trades2 if t > 0]
losses2 = [t * 100 for t in trades2 if t <= 0]

print(f"Results: {len(trades2)} trades")
print(f"Win rate: {sum(1 for t in trades2 if t > 0) / len(trades2) * 100:.1f}%")
print(f"Average trade: {np.mean(trades2) * 100:.2f}%")
if wins2:
    print(f"Wins: {len(wins2)}, avg={np.mean(wins2):.2f}%")
if losses2:
    print(f"Losses: {len(losses2)}, avg={np.mean(losses2):.2f}%")

costs = len(trades2) * 0.3
net_return = (equity2.iloc[-1] / equity2.iloc[0] - 1) * 100 - costs
print(f"Net return (after costs): {net_return:+.2f}%")