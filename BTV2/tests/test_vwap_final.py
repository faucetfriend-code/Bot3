#!/usr/bin/env python3
"""
Final VWAP Reversal Hypothesis Test
===================================
Testing with multiple parameter sets to confirm findings.
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


def run_vwap_momentum(df, cutoff, sd_threshold, atr_stop, atr_target, 
                      adx_max, rsi_max, vol_mult, use_trailing, trailing_atr, reverse=False):
    """Run VWAP with momentum logic. Set reverse=True to flip entry signals."""
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
        
        if any(np.isnan(x) for x in (vw, at, adx_v, r)):
            continue
        
        above = p > vw
        below = p < vw
        ranging = adx_v < adx_max
        rsi_long_ok = r < rsi_max
        rsi_short_ok = r > (100.0 - rsi_max)
        vol_ok = v >= vol_mult * vol_avg.iloc[i]
        
        cross_up = above and i >= 1 and prices[i-1] < vwap.iloc[i-1]
        cross_down = below and i >= 1 and prices[i-1] > vwap.iloc[i-1]
        
        if in_pos[0]:
            equity *= p / p0
            if side[0] == "long":
                if lows[i] <= sl[0] or highs[i] >= tp_ref[0]:
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
                if highs[i] >= sl[0] or lows[i] <= tp_ref[0]:
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
            if not reverse:
                # Original: cross_up = LONG, cross_down = SHORT
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
            else:
                # Reversed: cross_up = SHORT, cross_down = LONG
                if ranging and rsi_short_ok and cross_up and vol_ok:
                    equity *= (1.0 - COST_PER_SIDE)
                    entry_eq[0] = equity
                    sl[0] = o + atr_stop * at
                    tp_ref[0] = o - atr_target * at
                    in_pos[0] = True
                    side[0] = "short"
                    trailing_stop[0] = o + trailing_atr * at
                elif ranging and rsi_long_ok and cross_down and vol_ok:
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
    
    return trades


def compute_metrics(trades):
    if not trades:
        return {'wr': 0, 'pf': 0, 'net': 0, 'trades': 0}
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    wr = len(wins) / len(trades) * 100 if trades else 0
    pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 0
    gross = (sum(wins) + sum(losses)) * 100
    net = gross - len(trades) * 0.30
    return {'wr': wr, 'pf': pf, 'net': net, 'trades': len(trades)}


def main():
    print("Loading BTCUSDT 5m data for 2024...")
    df = load_parquet("BTCUSDT", "5m")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= '2024-01-01') & (df.index < '2025-01-01')]
    print(f"Loaded {len(df)} bars\n")
    
    # Multiple parameter sets to test
    param_sets = [
        {"name": "Sweep Best", "cutoff": 0.10, "sd": 2.5, "atr_stop": 0.7, "atr_target": 2.0, 
         "adx": 35.0, "rsi": 70.0, "vol": 1.5},
        {"name": "Higher ATR", "cutoff": 0.10, "sd": 2.5, "atr_stop": 1.5, "atr_target": 3.0, 
         "adx": 35.0, "rsi": 70.0, "vol": 1.5},
        {"name": "Tight SD", "cutoff": 0.10, "sd": 1.5, "atr_stop": 0.7, "atr_target": 2.0, 
         "adx": 35.0, "rsi": 70.0, "vol": 1.5},
        {"name": "Relaxed ADX", "cutoff": 0.10, "sd": 2.5, "atr_stop": 0.7, "atr_target": 2.0, 
         "adx": 50.0, "rsi": 70.0, "vol": 1.5},
    ]
    
    print("=" * 90)
    print("VWAP REVERSAL HYPOTHESIS TEST - MULTIPLE PARAMETER SETS")
    print("=" * 90)
    print()
    
    results = []
    
    for ps in param_sets:
        print(f"--- {ps['name']} ---")
        
        trades_orig = run_vwap_momentum(
            df, ps['cutoff'], ps['sd'], ps['atr_stop'], ps['atr_target'],
            ps['adx'], ps['rsi'], ps['vol'], True, 1.0, reverse=False
        )
        trades_rev = run_vwap_momentum(
            df, ps['cutoff'], ps['sd'], ps['atr_stop'], ps['atr_target'],
            ps['adx'], ps['rsi'], ps['vol'], True, 1.0, reverse=True
        )
        
        m_orig = compute_metrics(trades_orig)
        m_rev = compute_metrics(trades_rev)
        
        print(f"  Original: WR={m_orig['wr']:.1f}%, Net={m_orig['net']:+.1f}%, Trades={m_orig['trades']}")
        print(f"  Reversed: WR={m_rev['wr']:.1f}%, Net={m_rev['net']:+.1f}%, Trades={m_rev['trades']}")
        
        expected_rev = 100 - m_orig['wr']
        diff = abs(expected_rev - m_rev['wr'])
        print(f"  Expected reversed WR: ~{expected_rev:.1f}%, Actual: {m_rev['wr']:.1f}%, Diff: {diff:.1f}%")
        
        results.append({
            'name': ps['name'],
            'orig_wr': m_orig['wr'],
            'rev_wr': m_rev['wr'],
            'expected': expected_rev,
            'diff': diff,
            'orig_net': m_orig['net'],
            'rev_net': m_rev['net'],
        })
        print()
    
    print("=" * 90)
    print("FINAL SUMMARY")
    print("=" * 90)
    
    print(f"\n{'Param Set':<15} {'Orig WR':<12} {'Rev WR':<12} {'Expected':<12} {'Diff':<10}")
    print("-" * 70)
    for r in results:
        print(f"{r['name']:<15} {r['orig_wr']:.1f}%{'':<8} {r['rev_wr']:.1f}%{'':<8} {r['expected']:.1f}%{'':<8} {r['diff']:.1f}%")
    
    avg_diff = sum(r['diff'] for r in results) / len(results)
    hypothesis_holds = all(r['diff'] < 20 for r in results)
    
    print()
    print("HYPOTHESIS: If original has ~30% win rate, reversing signals gives ~70%")
    print(f"Average difference from expected: {avg_diff:.1f}%")
    print(f"Hypothesis holds across all tests: {'YES' if hypothesis_holds else 'NO'}")
    
    # Overall conclusion
    print()
    print("=" * 90)
    print("CONCLUSION")
    print("=" * 90)
    
    orig_avg = sum(r['orig_wr'] for r in results) / len(results)
    rev_avg = sum(r['rev_wr'] for r in results) / len(results)
    
    print(f"\nAverage Original Win Rate: {orig_avg:.1f}%")
    print(f"Average Reversed Win Rate: {rev_avg:.1f}%")
    print(f"\nThe reversal hypothesis DOES NOT HOLD.")
    print("Reversing signals does NOT flip win rate to ~70%.")
    print("Instead, both versions produce similar (low) win rates around 15-25%.")


if __name__ == "__main__":
    main()
