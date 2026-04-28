#!/usr/bin/env python3
"""
VWAP Reversal Test - Exact Reversal of Each Signal
===================================================
Testing: If original has ~30% win rate, does reversing signals give ~70%?

For the VWAP Scalping momentum strategy (cross above = LONG, cross below = SHORT):
- Original: 
  - LONG when crosses ABOVE VWAP + conditions
  - SHORT when crosses BELOW VWAP + conditions
- Reversed:
  - SHORT when crosses ABOVE VWAP + conditions  
  - LONG when crosses BELOW VWAP + conditions
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
    apply_butterworth, compute_vwap_rolling, compute_atr,
    compute_adx, compute_rsi, COST_PER_SIDE
)


def run_vwap_momentum_original(df, cutoff=0.10, sd_threshold=2.5, atr_stop=0.7, 
                                atr_target=2.0, adx_max=35.0, rsi_max=70.0, 
                                vol_mult=1.5, use_trailing=True, trailing_atr=1.0):
    """
    Original momentum: cross ABOVE -> go LONG, cross BELOW -> go SHORT
    """
    fc = apply_butterworth(df["Close"], cutoff)
    vwap, vs = compute_vwap_rolling(df["High"], df["Low"], fc, df["Volume"], 20)
    atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
    adx = compute_adx(df["High"], df["Low"], df["Close"], 14)
    rsi = compute_rsi(fc, 14)
    vol_avg = df["Volume"].rolling(20).mean()
    
    prices = df["Close"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    opens = df["Open"].values.astype(float)
    volumes = df["Volume"].values.astype(float)
    n = len(prices)
    
    equity = 1.0
    in_pos = [False]
    side = [""]
    entry_eq = [1.0]
    sl = [0.0]
    tp_ref = [0.0]
    trailing_stop = [0.0]
    trades = []
    
    for i in range(1, n):
        vw = vwap.iloc[i]
        at = atr.iloc[i]
        adx_v = adx.iloc[i]
        r = rsi.iloc[i]
        p = prices[i]
        p0 = prices[i-1]
        o = opens[i]
        v = volumes[i]
        va = vol_avg.iloc[i]
        
        if any(np.isnan(x) for x in (vw, at, adx_v, r)):
            continue
        
        above = p > vw
        below = p < vw
        ranging = adx_v < adx_max
        rsi_long_ok = r < rsi_max
        rsi_short_ok = r > (100.0 - rsi_max)
        vol_ok = v >= vol_mult * va
        
        # Cross detection
        cross_up = above and i >= 1 and prices[i-1] < vwap.iloc[i-1]
        cross_down = below and i >= 1 and prices[i-1] > vwap.iloc[i-1]
        
        if in_pos[0]:
            equity *= p / p0
            # Exit logic
            if side[0] == "long":
                if lows[i] <= sl[0]:
                    equity *= (1.0 - COST_PER_SIDE)
                    trades.append(equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif highs[i] >= tp_ref[0]:
                    equity *= (1.0 - COST_PER_SIDE)
                    trades.append(equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif use_trailing:
                    trailing_stop[0] = max(trailing_stop[0], p - trailing_atr * at)
                    if lows[i] <= trailing_stop[0]:
                        equity *= (1.0 - COST_PER_SIDE)
                        trades.append(equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
            else:
                if highs[i] >= sl[0]:
                    equity *= (1.0 - COST_PER_SIDE)
                    trades.append(equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif lows[i] <= tp_ref[0]:
                    equity *= (1.0 - COST_PER_SIDE)
                    trades.append(equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif use_trailing:
                    trailing_stop[0] = min(trailing_stop[0], p + trailing_atr * at)
                    if highs[i] >= trailing_stop[0]:
                        equity *= (1.0 - COST_PER_SIDE)
                        trades.append(equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
        else:
            # ORIGINAL: momentum
            if ranging and rsi_long_ok and cross_up and vol_ok:
                equity *= (1.0 - COST_PER_SIDE)
                entry_eq[0] = equity
                sl[0] = o - atr_stop * at
                tp_ref[0] = o + atr_target * at
                in_pos[0] = True
                side[0] = "long"
                trailing_stop[0] = o - trailing_atr * at
            elif ranging and rsi_short_ok and cross_down and vol_ok:
                equity *= (1.0 - COST_PER_SIDE)
                entry_eq[0] = equity
                sl[0] = o + atr_stop * at
                tp_ref[0] = o - atr_target * at
                in_pos[0] = True
                side[0] = "short"
                trailing_stop[0] = o + trailing_atr * at
    
    if in_pos[0]:
        equity *= (1.0 - COST_PER_SIDE)
        trades.append(equity / entry_eq[0] - 1.0)
    
    return equity, trades


def run_vwap_momentum_reversed(df, cutoff=0.10, sd_threshold=2.5, atr_stop=0.7, 
                               atr_target=2.0, adx_max=35.0, rsi_max=70.0, 
                               vol_mult=1.5, use_trailing=True, trailing_atr=1.0):
    """
    REVERSED momentum: cross ABOVE -> go SHORT, cross BELOW -> go LONG
    This is the exact opposite of original.
    """
    fc = apply_butterworth(df["Close"], cutoff)
    vwap, vs = compute_vwap_rolling(df["High"], df["Low"], fc, df["Volume"], 20)
    atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
    adx = compute_adx(df["High"], df["Low"], df["Close"], 14)
    rsi = compute_rsi(fc, 14)
    vol_avg = df["Volume"].rolling(20).mean()
    
    prices = df["Close"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    opens = df["Open"].values.astype(float)
    volumes = df["Volume"].values.astype(float)
    n = len(prices)
    
    equity = 1.0
    in_pos = [False]
    side = [""]
    entry_eq = [1.0]
    sl = [0.0]
    tp_ref = [0.0]
    trailing_stop = [0.0]
    trades = []
    
    for i in range(1, n):
        vw = vwap.iloc[i]
        at = atr.iloc[i]
        adx_v = adx.iloc[i]
        r = rsi.iloc[i]
        p = prices[i]
        p0 = prices[i-1]
        o = opens[i]
        v = volumes[i]
        va = vol_avg.iloc[i]
        
        if any(np.isnan(x) for x in (vw, at, adx_v, r)):
            continue
        
        above = p > vw
        below = p < vw
        ranging = adx_v < adx_max
        rsi_long_ok = r < rsi_max
        rsi_short_ok = r > (100.0 - rsi_max)
        vol_ok = v >= vol_mult * va
        
        # Cross detection
        cross_up = above and i >= 1 and prices[i-1] < vwap.iloc[i-1]
        cross_down = below and i >= 1 and prices[i-1] > vwap.iloc[i-1]
        
        if in_pos[0]:
            equity *= p / p0
            # Exit logic
            if side[0] == "long":
                if lows[i] <= sl[0]:
                    equity *= (1.0 - COST_PER_SIDE)
                    trades.append(equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif highs[i] >= tp_ref[0]:
                    equity *= (1.0 - COST_PER_SIDE)
                    trades.append(equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif use_trailing:
                    trailing_stop[0] = max(trailing_stop[0], p - trailing_atr * at)
                    if lows[i] <= trailing_stop[0]:
                        equity *= (1.0 - COST_PER_SIDE)
                        trades.append(equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
            else:
                if highs[i] >= sl[0]:
                    equity *= (1.0 - COST_PER_SIDE)
                    trades.append(equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif lows[i] <= tp_ref[0]:
                    equity *= (1.0 - COST_PER_SIDE)
                    trades.append(equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif use_trailing:
                    trailing_stop[0] = min(trailing_stop[0], p + trailing_atr * at)
                    if highs[i] >= trailing_stop[0]:
                        equity *= (1.0 - COST_PER_SIDE)
                        trades.append(equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
        else:
            # REVERSED: opposite of original
            # Original: cross_up = LONG, cross_down = SHORT
            # Reversed: cross_up = SHORT, cross_down = LONG
            if ranging and rsi_short_ok and cross_up and vol_ok:
                # Go SHORT on cross up (opposite of original)
                equity *= (1.0 - COST_PER_SIDE)
                entry_eq[0] = equity
                sl[0] = o + atr_stop * at
                tp_ref[0] = o - atr_target * at
                in_pos[0] = True
                side[0] = "short"
                trailing_stop[0] = o + trailing_atr * at
            elif ranging and rsi_long_ok and cross_down and vol_ok:
                # Go LONG on cross down (opposite of original)
                equity *= (1.0 - COST_PER_SIDE)
                entry_eq[0] = equity
                sl[0] = o - atr_stop * at
                tp_ref[0] = o + atr_target * at
                in_pos[0] = True
                side[0] = "long"
                trailing_stop[0] = o - trailing_atr * at
    
    if in_pos[0]:
        equity *= (1.0 - COST_PER_SIDE)
        trades.append(equity / entry_eq[0] - 1.0)
    
    return equity, trades


def compute_metrics(trades):
    if not trades:
        return {'wr': 0, 'pf': 0, 'net': 0, 'trades': 0}
    
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    wr = len(wins) / len(trades) * 100 if trades else 0
    pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 0
    gross = (sum(wins) + sum(losses)) * 100
    costs = len(trades) * 0.30
    net = gross - costs
    
    return {'wr': wr, 'pf': pf, 'net': net, 'trades': len(trades)}


def main():
    print("Loading BTCUSDT 5m data for 2024...")
    df = load_parquet("BTCUSDT", "5m")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= '2024-01-01') & (df.index < '2025-01-01')]
    print(f"Loaded {len(df)} bars")
    print()
    
    params = {
        'cutoff': 0.10,
        'sd_threshold': 2.5,
        'atr_stop': 0.7,
        'atr_target': 2.0,
        'adx_max': 35.0,
        'rsi_max': 70.0,
        'vol_mult': 1.5,
        'use_trailing': True,
        'trailing_atr': 1.0,
    }
    
    print("=" * 80)
    print("VWAP REVERSAL TEST - EXACT OPPOSITE SIGNALS")
    print("=" * 80)
    print(f"Params: cutoff={params['cutoff']}, sd={params['sd_threshold']}, "
          f"atr_stop={params['atr_stop']}, adx={params['adx_max']}, rsi_max={params['rsi_max']}")
    print()
    
    # Run original
    print("Running ORIGINAL (cross ABOVE=LONG, cross BELOW=SHORT)...")
    eq_orig, trades_orig = run_vwap_momentum_original(df, **params)
    m_orig = compute_metrics(trades_orig)
    print(f"  Trades: {m_orig['trades']}, Win Rate: {m_orig['wr']:.1f}%, PF: {m_orig['pf']:.2f}")
    print(f"  Net Return: {m_orig['net']:+.1f}%")
    print()
    
    # Run reversed
    print("Running REVERSED (cross ABOVE=SHORT, cross BELOW=LONG)...")
    eq_rev, trades_rev = run_vwap_momentum_reversed(df, **params)
    m_rev = compute_metrics(trades_rev)
    print(f"  Trades: {m_rev['trades']}, Win Rate: {m_rev['wr']:.1f}%, PF: {m_rev['pf']:.2f}")
    print(f"  Net Return: {m_rev['net']:+.1f}%")
    print()
    
    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"{'Metric':<25} {'Original':<20} {'Reversed':<20}")
    print("-" * 80)
    print(f"{'Win Rate':<25} {m_orig['wr']:.1f}%{'':<16} {m_rev['wr']:.1f}%")
    print(f"{'Net Return':<25} {m_orig['net']:+.1f}%{'':<16} {m_rev['net']:+.1f}%")
    print(f"{'Trade Count':<25} {m_orig['trades']}{'':<17} {m_rev['trades']}")
    print(f"{'Profit Factor':<25} {m_orig['pf']:.2f}{'':<17} {m_rev['pf']:.2f}")
    print()
    
    # Hypothesis test
    print("=" * 80)
    print("HYPOTHESIS TEST")
    print("=" * 80)
    print(f"Original Win Rate: {m_orig['wr']:.1f}%")
    print(f"Reversed Win Rate: {m_rev['wr']:.1f}%")
    print(f"100% - Original WR = {100 - m_orig['wr']:.1f}%")
    print()
    
    original_wr = m_orig['wr']
    reversed_wr = m_rev['wr']
    complement = 100 - original_wr
    
    if abs(complement - reversed_wr) < 15:
        result = "HOLDS"
        print(f"Result: HYPOTHESIS {result}")
        print(f"Reversed WR ({reversed_wr:.1f}%) is close to 100% - Original WR ({complement:.1f}%)")
    else:
        result = "DOES NOT HOLD"
        print(f"Result: HYPOTHESIS {result}")
        print(f"Expected reversed WR ~{complement:.1f}% based on hypothesis")
        print(f"Actual reversed WR: {reversed_wr:.1f}%")
        print(f"Difference: {abs(complement - reversed_wr):.1f}%")
    
    print()
    print("CONCLUSION:")
    if reversed_wr > original_wr:
        print("- Reversal IMPROVED win rate by {:.1f}%".format(reversed_wr - original_wr))
    else:
        print("- Reversal WORSENED win rate by {:.1f}%".format(original_wr - reversed_wr))


if __name__ == "__main__":
    main()
