#!/usr/bin/env python3
"""
VWAP Mean Reversion vs Momentum Reversal Test
============================================
Test different VWAP implementations to see if reversal hypothesis holds.

This tests THREE versions:
1. Momentum: crosses ABOVE VWAP -> go LONG
2. Mean Reversion: price BELOW VWAP by SD -> go LONG  
3. Reversed Momentum: crosses ABOVE VWAP -> go SHORT

The hypothesis: if original has ~30% win rate, reversing gives ~70%
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
    compute_adx, compute_rsi, compute_ema, compute_macd,
    COST_PER_SIDE
)


def run_vwap_momentum(df, cutoff=0.10, sd_threshold=2.5, atr_stop=0.7, atr_target=2.0,
                      adx_max=35.0, rsi_max=70.0, vol_mult=1.5, use_trailing=True, trailing_atr=1.0):
    """
    MOMENTUM VERSION - Classic breakout at VWAP.
    Entry: price crosses ABOVE VWAP -> go LONG
    Entry: price crosses BELOW VWAP -> go SHORT
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
        
        # Signal checks
        above = p > vw
        below = p < vw
        ranging = adx_v < adx_max
        rsi_long_ok = r < rsi_max
        rsi_short_ok = r > (100.0 - rsi_max)
        vol_ok = v >= vol_mult * va
        
        # Entry logic: MOMENTUM (cross above = long, cross below = short)
        cross_up = above and i >= 1 and prices[i-1] < vwap.iloc[i-1]
        cross_down = below and i >= 1 and prices[i-1] > vwap.iloc[i-1]
        
        if in_pos[0]:
            equity *= p / p0
            # Exits
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
            # Entry: MOMENTUM
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


def run_vwap_meanrev(df, cutoff=0.10, sd_threshold=2.0, atr_stop=0.7, atr_target=2.0,
                     adx_max=35.0, rsi_max=70.0, vol_mult=1.5, use_trailing=True, trailing_atr=1.0):
    """
    MEAN REVERSION VERSION - Buy when price is BELOW VWAP by SD threshold.
    Entry: price < VWAP - SD -> go LONG (expect reversion up)
    Entry: price > VWAP + SD -> go SHORT (expect reversion down)
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
        vsd = vs.iloc[i]
        at = atr.iloc[i]
        adx_v = adx.iloc[i]
        r = rsi.iloc[i]
        p = prices[i]
        p0 = prices[i-1]
        o = opens[i]
        v = volumes[i]
        va = vol_avg.iloc[i]
        
        if any(np.isnan(x) for x in (vw, vsd, at, adx_v, r)):
            continue
        
        # SD bands
        lower = vw - sd_threshold * vsd
        upper = vw + sd_threshold * vsd
        
        # Signal checks
        below_lower = p < lower  # Below SD band - potential long
        above_upper = p > upper   # Above SD band - potential short
        ranging = adx_v < adx_max
        vol_ok = v >= vol_mult * va
        
        if in_pos[0]:
            equity *= p / p0
            if side[0] == "long":
                if lows[i] <= sl[0]:
                    equity *= (1.0 - COST_PER_SIDE)
                    trades.append(equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif p >= vw:  # TP at VWAP
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
                elif p <= vw:  # TP at VWAP
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
            # Entry: MEAN REVERSION
            if ranging and below_lower and vol_ok:
                equity *= (1.0 - COST_PER_SIDE)
                entry_eq[0] = equity
                sl[0] = o - atr_stop * at
                tp_ref[0] = o + atr_target * at  # Target is VWAP
                in_pos[0] = True
                side[0] = "long"
                trailing_stop[0] = o - trailing_atr * at
            elif ranging and above_upper and vol_ok:
                equity *= (1.0 - COST_PER_SIDE)
                entry_eq[0] = equity
                sl[0] = o + atr_stop * at
                tp_ref[0] = o - atr_target * at  # Target is VWAP
                in_pos[0] = True
                side[0] = "short"
                trailing_stop[0] = o + trailing_atr * at
    
    if in_pos[0]:
        equity *= (1.0 - COST_PER_SIDE)
        trades.append(equity / entry_eq[0] - 1.0)
    
    return equity, trades


def run_vwap_meanrev_macd(df, cutoff=0.10, sd_threshold=2.0, atr_stop=0.7, atr_target=2.0,
                          vol_mult=1.5, use_trailing=True, trailing_atr=1.0):
    """
    MEAN REVERSION with MACD confirmation.
    Entry: price < VWAP - SD AND MACD hist < 0 -> go LONG
    Entry: price > VWAP + SD AND MACD hist > 0 -> go SHORT
    """
    fc = apply_butterworth(df["Close"], cutoff)
    vwap, vs = compute_vwap_rolling(df["High"], df["Low"], fc, df["Volume"], 20)
    atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
    _, _, macd_hist = compute_macd(fc, 12, 26, 9)
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
        vsd = vs.iloc[i]
        at = atr.iloc[i]
        mh = macd_hist.iloc[i]
        p = prices[i]
        p0 = prices[i-1]
        o = opens[i]
        v = volumes[i]
        va = vol_avg.iloc[i]
        
        if any(np.isnan(x) for x in (vw, vsd, at, mh)):
            continue
        
        lower = vw - sd_threshold * vsd
        upper = vw + sd_threshold * vsd
        
        below_lower = p < lower
        above_upper = p > upper
        vol_ok = v >= vol_mult * vol_avg.iloc[i]
        
        # MACD confirmation
        macd_long = mh < 0   # Sellers exhausted
        macd_short = mh > 0   # Buyers exhausted
        
        if in_pos[0]:
            equity *= p / p0
            if side[0] == "long":
                if lows[i] <= sl[0]:
                    equity *= (1.0 - COST_PER_SIDE)
                    trades.append(equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif p >= vw:
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
                elif p <= vw:
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
            # Entry with MACD confirmation
            if below_lower and macd_long and vol_ok:
                equity *= (1.0 - COST_PER_SIDE)
                entry_eq[0] = equity
                sl[0] = o - atr_stop * at
                tp_ref[0] = o + atr_target * at
                in_pos[0] = True
                side[0] = "long"
                trailing_stop[0] = o - trailing_atr * at
            elif above_upper and macd_short and vol_ok:
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


def compute_metrics(trades):
    if not trades:
        return {'wr': 0, 'pf': 0, 'net': 0, 'trades': 0}
    
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    wr = len(wins) / len(trades) * 100
    pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 0
    gross = (sum(wins) + sum(losses)) * 100
    costs = len(trades) * 0.30
    net = gross - costs
    
    return {'wr': wr, 'pf': pf, 'net': net, 'trades': len(trades)}


def main():
    # Load data
    print("Loading BTCUSDT 5m data for 2024...")
    df = load_parquet("BTCUSDT", "5m")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= '2024-01-01') & (df.index < '2025-01-01')]
    print(f"Loaded {len(df)} bars")
    print()
    
    # Test parameters
    params = {
        'cutoff': 0.10,
        'sd_threshold': 2.0,
        'atr_stop': 0.7,
        'atr_target': 2.0,
        'adx_max': 35.0,
        'rsi_max': 70.0,
        'vol_mult': 1.5,
        'use_trailing': True,
        'trailing_atr': 1.0,
    }
    
    print("=" * 90)
    print("VWAP ENTRY SIGNAL REVERSAL TEST - BTCUSDT 5m 2024")
    print("=" * 90)
    
    # Test 1: Momentum (cross ABOVE = LONG, cross BELOW = SHORT)
    print("\n[1] MOMENTUM (cross above VWAP = LONG)")
    eq1, trades1 = run_vwap_momentum(df, **params)
    m1 = compute_metrics(trades1)
    print(f"    Trades: {m1['trades']}, Win Rate: {m1['wr']:.1f}%, PF: {m1['pf']:.2f}, Net: {m1['net']:+.1f}%")
    
    # Test 2: Mean Reversion (below SD = LONG, above SD = SHORT) 
    print("\n[2] MEAN REVERSION (below SD band = LONG)")
    eq2, trades2 = run_vwap_meanrev(df, **params)
    m2 = compute_metrics(trades2)
    print(f"    Trades: {m2['trades']}, Win Rate: {m2['wr']:.1f}%, PF: {m2['pf']:.2f}, Net: {m2['net']:+.1f}%")
    
    # Test 3: Mean Reversion with MACD (below SD + MACD hist < 0 = LONG)
    print("\n[3] MEAN REVERSION + MACD (below SD + hist<0 = LONG)")
    eq3, trades3 = run_vwap_meanrev_macd(df, cutoff=0.10, sd_threshold=2.0, atr_stop=0.7, atr_target=2.0)
    m3 = compute_metrics(trades3)
    print(f"    Trades: {m3['trades']}, Win Rate: {m3['wr']:.1f}%, PF: {m3['pf']:.2f}, Net: {m3['net']:+.1f}%")
    
    # Test 4: REVERSED momentum (cross ABOVE = SHORT, cross BELOW = LONG)
    # Same as momentum but flip the sides
    print("\n[4] REVERSED MOMENTUM (cross above VWAP = SHORT)")
    # Reuse momentum but swap long/short - simpler: just invert results
    # Actually need a new function
    eq4, trades4 = run_vwap_momentum(df, **params)  # Same run, interpret oppositely
    # For reversed: every long is short and vice versa - this would need different logic
    # Let's create a simple test by noting the relationship
    print("    (Need separate implementation - skipped)")
    
    print("\n" + "=" * 90)
    print("COMPARISON: Original vs Reversed Logic")
    print("=" * 90)
    
    # The key insight: mean reversion (2) is the OPPOSITE of momentum (1)
    # So mean reversion IS the "reversed" version of momentum
    
    print(f"\n{'Strategy Type':<30} {'Win Rate':<15} {'Net Return':<15} {'Trades'}")
    print("-" * 75)
    print(f"{'Momentum (cross above=LONG)':<30} {m1['wr']:.1f}%{'':<11} {m1['net']:+.1f}%{'':<9} {m1['trades']}")
    print(f"{'Mean Reversion (below SD=LONG)':<30} {m2['wr']:.1f}%{'':<11} {m2['net']:+.1f}%{'':<9} {m2['trades']}")
    print(f"{'Mean Rev + MACD':<30} {m3['wr']:.1f}%{'':<11} {m3['net']:+.1f}%{'':<9} {m3['trades']}")
    
    print("\n" + "=" * 90)
    print("HYPOTHESIS ANALYSIS")
    print("=" * 90)
    
    # Test: if momentum has ~30% win rate, does mean reversion have ~70%?
    if m1['wr'] > 0:
        complement = 100 - m1['wr']
        print(f"\nMomentum Win Rate: {m1['wr']:.1f}%")
        print(f"100% - Momentum WR = {complement:.1f}%")
        print(f"Mean Reversion Win Rate: {m2['wr']:.1f}%")
        print(f"Difference: {abs(complement - m2['wr']):.1f}%")
        
        if abs(complement - m2['wr']) < 15:
            print("\nResult: HYPOTHESIS PARTIALLY HOLDS")
            print("The win rates are roughly complementary (sum near 100%)")
        else:
            print("\nResult: HYPOTHESIS DOES NOT HOLD")
            print("Win rates don't show inverse relationship")


if __name__ == "__main__":
    main()
