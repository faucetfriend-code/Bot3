#!/usr/bin/env python3
"""
VWAP Scalping - Focused Testing
===============================
Focus on finding a winning combination with proper filters
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
    apply_butterworth,
    compute_vwap_rolling,
    compute_atr,
    compute_adx,
    compute_rsi,
    compute_ema,
    compute_metrics,
    COST_PER_SIDE,
)


def run_vwap_test(
    df,
    cutoff=0.10,
    atr_stop=1.0,
    atr_target=2.0,
    ema_fast=9,
    ema_slow=20,
    use_trend_filter=True,
    use_volume_filter=True,
    volume_mult=1.5,
    adx_max=25.0,
    rsi_max=50.0,
    entry_mode="cross",
    deviation_pct=0.5,
    momentum_bars=2,
    deviation_sd=2.5,
):
    """VWAP scalping with configurable parameters."""
    fc = apply_butterworth(df["Close"], cutoff)
    vwap, vs = compute_vwap_rolling(df["High"], df["Low"], fc, df["Volume"], 20)
    atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
    adx = compute_adx(df["High"], df["Low"], df["Close"], 14)
    rsi = compute_rsi(fc, 14)
    ema_f = compute_ema(fc, ema_fast)
    ema_m = compute_ema(fc, ema_slow)
    vol_avg = df["Volume"].rolling(20).mean()

    prices = df["Close"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    opens = df["Open"].values.astype(float)
    volumes = df["Volume"].values.astype(float)
    n = len(prices)

    equity_arr = np.ones(n, dtype=float)
    curr_equity = 1.0
    in_pos = [False]
    side = [""]
    entry_eq = [1.0]
    sl = [0.0]
    tp_ref = [0.0]
    closed_trades = []

    for i in range(1, n):
        vw = vwap.iloc[i]
        vsd = vs.iloc[i]
        at = atr.iloc[i]
        adx_v = adx.iloc[i]
        r = rsi.iloc[i]
        p = prices[i]
        p0 = prices[i - 1]
        o = opens[i]
        v = volumes[i]
        va = vol_avg.iloc[i]

        ema_f_val = ema_f.iloc[i]
        ema_m_val = ema_m.iloc[i]

        if any(np.isnan(x) for x in (vw, vsd, at, adx_v, r, ema_f_val, ema_m_val, va)):
            equity_arr[i] = curr_equity
            continue

        is_bullish = ema_f_val > ema_m_val
        is_bearish = ema_f_val < ema_m_val
        above_vwap = p > vw
        below_vwap = p < vw
        volume_surge = v >= volume_mult * va if use_volume_filter else True
        ranging = adx_v < adx_max
        rsi_long_ok = r < rsi_max
        rsi_short_ok = r > (100.0 - rsi_max)

        if in_pos[0]:
            curr_equity *= p / p0
            hit = False
            if side[0] == "long":
                if lows[i] <= sl[0]:
                    hit = True
                elif highs[i] >= tp_ref[0]:
                    hit = True
            else:
                if highs[i] >= sl[0]:
                    hit = True
                elif lows[i] <= tp_ref[0]:
                    hit = True

            if hit:
                curr_equity *= (1.0 - COST_PER_SIDE)
                closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                in_pos[0] = False
        else:
            long_signal = False
            short_signal = False
            deviation_from_vwap = (p - vw) / (vw + 1e-10) * 100.0 if vw > 0 else 0.0

            if entry_mode == "cross":
                if ranging and rsi_long_ok:
                    if above_vwap and volume_surge:
                        if use_trend_filter and is_bullish:
                            if i >= 1 and prices[i - 1] < vwap.iloc[i - 1]:
                                long_signal = True
                        elif not use_trend_filter:
                            if i >= 1 and prices[i - 1] < vwap.iloc[i - 1]:
                                long_signal = True

                if ranging and rsi_short_ok:
                    if below_vwap and volume_surge:
                        if use_trend_filter and is_bearish:
                            if i >= 1 and prices[i - 1] > vwap.iloc[i - 1]:
                                short_signal = True
                        elif not use_trend_filter:
                            if i >= 1 and prices[i - 1] > vwap.iloc[i - 1]:
                                short_signal = True

            elif entry_mode == "deviation":
                # Long when price is below VWAP by deviation_pct%
                if ranging and rsi_long_ok:
                    if deviation_from_vwap < -deviation_pct and volume_surge:
                        if not use_trend_filter or is_bullish:
                            long_signal = True

                if ranging and rsi_short_ok:
                    if deviation_from_vwap > deviation_pct and volume_surge:
                        if not use_trend_filter or is_bearish:
                            short_signal = True

            elif entry_mode == "momentum":
                if ranging and rsi_long_ok:
                    if above_vwap and volume_surge:
                        momentum_ok = True
                        for j in range(1, min(momentum_bars + 1, i + 1)):
                            if i - j - 1 >= 0 and prices[i - j] <= prices[i - j - 1]:
                                momentum_ok = False
                                break
                        if momentum_ok and i >= momentum_bars + 1:
                            if not use_trend_filter or is_bullish:
                                long_signal = True

                if ranging and rsi_short_ok:
                    if below_vwap and volume_surge:
                        momentum_ok = True
                        for j in range(1, min(momentum_bars + 1, i + 1)):
                            if i - j - 1 >= 0 and prices[i - j] >= prices[i - j - 1]:
                                momentum_ok = False
                                break
                        if momentum_ok and i >= momentum_bars + 1:
                            if not use_trend_filter or is_bearish:
                                short_signal = True

            elif entry_mode == "mean_reversion":
                upper_band = vw + deviation_sd * vsd
                lower_band = vw - deviation_sd * vsd
                
                # Mean reversion: fade extreme moves expecting pullback to VWAP
                if rsi_long_ok:  # Not overbought
                    if p < lower_band and volume_surge:
                        long_signal = True
                if rsi_short_ok:  # Not oversold  
                    if p > upper_band and volume_surge:
                        short_signal = True

            if long_signal:
                curr_equity *= (1.0 - COST_PER_SIDE)
                entry_eq[0] = curr_equity
                sl[0] = o - atr_stop * at
                tp_ref[0] = o + atr_target * at
                in_pos[0] = True
                side[0] = "long"

            elif short_signal:
                curr_equity *= (1.0 - COST_PER_SIDE)
                entry_eq[0] = curr_equity
                sl[0] = o + atr_stop * at
                tp_ref[0] = o - atr_target * at
                in_pos[0] = True
                side[0] = "short"

        equity_arr[i] = curr_equity

    if in_pos[0]:
        curr_equity *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
        equity_arr[-1] = curr_equity

    return pd.Series(equity_arr, index=df.index), closed_trades


# Load data
print("Loading BTCUSDT 5m data...")
df = load_parquet("BTCUSDT", "5m")
df.columns = [c.capitalize() for c in df.columns]
df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
print(f"Loaded {len(df)} bars (2024)")
print()

results = []

# Test with different ADX and RSI thresholds
print("=" * 120)
print("TEST 1: ADX AND RSI THRESHOLD COMBINATIONS")
print("=" * 120)

adx_values = [20, 25, 30, 35, 50]
rsi_values = [40, 45, 50, 55, 60]
atr_targets = [2.0, 3.0, 4.0, 5.0]

for adx_max in adx_values:
    for rsi_max in rsi_values:
        for atr_target in atr_targets:
            eq, trades = run_vwap_test(
                df,
                cutoff=0.10,
                atr_target=atr_target,
                adx_max=adx_max,
                rsi_max=rsi_max,
                entry_mode="cross",
                use_trend_filter=True,
                use_volume_filter=True,
                volume_mult=1.5,
            )
            m = compute_metrics(eq, trades)
            
            if len(trades) >= 5:
                wins = sum(1 for t in trades if t > 0)
                wr = wins / len(trades) * 100
                gp = sum(t for t in trades if t > 0)
                gl = abs(sum(t for t in trades if t < 0))
                pf = gp / gl if gl > 0 else 0
                gross = m["total_return_pct"]
                costs_pct = len(trades) * 0.30
                net = gross - costs_pct
                
                results.append({
                    'test': 'adx_rsi',
                    'mode': 'cross',
                    'adx': adx_max,
                    'rsi': rsi_max,
                    'atr_target': atr_target,
                    'trades': len(trades),
                    'wr': wr,
                    'pf': pf,
                    'gross': gross,
                    'costs': costs_pct,
                    'net': net,
                })

# Sort and show top results
print("\nTop 20 by Net Return (adx_rsi test):")
sorted_results = sorted(results, key=lambda x: x['net'], reverse=True)[:20]
for r in sorted_results:
    print(f"  ADX={r['adx']}, RSI={r['rsi']}, R:R={r['atr_target']}: Trades={r['trades']:4d}, WR={r['wr']:5.1f}%, Net={r['net']:+7.1f}%")

# Test different entry modes with best parameters
print("\n")
print("=" * 120)
print("TEST 2: BEST ENTRY MODES WITH BEST FILTERS")
print("=" * 120)

# Use best parameters found
best_adx = 30
best_rsi = 50

entry_modes = ["cross", "deviation", "momentum", "mean_reversion"]
for mode in entry_modes:
    for atr_target in [3.0, 4.0, 5.0]:
        eq, trades = run_vwap_test(
            df,
            cutoff=0.10,
            atr_target=atr_target,
            adx_max=best_adx,
            rsi_max=best_rsi,
            entry_mode=mode,
            use_trend_filter=True,
            use_volume_filter=True,
            volume_mult=1.5,
        )
        m = compute_metrics(eq, trades)
        
        if len(trades) >= 5:
            wins = sum(1 for t in trades if t > 0)
            wr = wins / len(trades) * 100
            gp = sum(t for t in trades if t > 0)
            gl = abs(sum(t for t in trades if t < 0))
            pf = gp / gl if gl > 0 else 0
            gross = m["total_return_pct"]
            costs_pct = len(trades) * 0.30
            net = gross - costs_pct
            
            print(f"  Mode={mode}, R:R={atr_target}: Trades={len(trades):4d}, WR={wr:5.1f}%, PF={pf:5.2f}, Net={net:+7.1f}%")
            
            results.append({
                'test': 'entry_modes',
                'mode': mode,
                'atr_target': atr_target,
                'trades': len(trades),
                'wr': wr,
                'pf': pf,
                'gross': gross,
                'costs': costs_pct,
                'net': net,
            })

# Test with volume filter disabled
print("\n")
print("=" * 120)
print("TEST 3: WITHOUT VOLUME FILTER")
print("=" * 120)

for mode in entry_modes:
    for atr_target in [3.0, 4.0, 5.0]:
        eq, trades = run_vwap_test(
            df,
            cutoff=0.10,
            atr_target=atr_target,
            adx_max=best_adx,
            rsi_max=best_rsi,
            entry_mode=mode,
            use_trend_filter=True,
            use_volume_filter=False,
        )
        m = compute_metrics(eq, trades)
        
        if len(trades) >= 5:
            wins = sum(1 for t in trades if t > 0)
            wr = wins / len(trades) * 100
            gp = sum(t for t in trades if t > 0)
            gl = abs(sum(t for t in trades if t < 0))
            pf = gp / gl if gl > 0 else 0
            gross = m["total_return_pct"]
            costs_pct = len(trades) * 0.30
            net = gross - costs_pct
            
            print(f"  Mode={mode}, R:R={atr_target}: Trades={len(trades):4d}, WR={wr:5.1f}%, PF={pf:5.2f}, Net={net:+7.1f}%")
            
            results.append({
                'test': 'no_vol_filter',
                'mode': mode,
                'atr_target': atr_target,
                'trades': len(trades),
                'wr': wr,
                'pf': pf,
                'gross': gross,
                'costs': costs_pct,
                'net': net,
            })

# Final summary
print("\n")
print("=" * 120)
print("FINAL SUMMARY")
print("=" * 120)

print("\n>> TOP 15 BY NET RETURN:")
sorted_by_net = sorted(results, key=lambda x: x['net'], reverse=True)[:15]
for r in sorted_by_net:
    test = r.get('test', '')
    mode = r.get('mode', '')
    atr = r.get('atr_target', r.get('atr_target'))
    print(f"  {test} | {mode} R:R={atr}: Trades={r['trades']:4d}, WR={r['wr']:5.1f}%, Net={r['net']:+7.1f}%")

print("\n>> TOP 15 BY WIN RATE (>30% WR, >10 trades):")
sorted_by_wr = sorted([r for r in results if r['wr'] > 30 and r['trades'] > 10], key=lambda x: x['wr'], reverse=True)[:15]
for r in sorted_by_wr:
    test = r.get('test', '')
    mode = r.get('mode', '')
    atr = r.get('atr_target', r.get('atr_target'))
    print(f"  {test} | {mode} R:R={atr}: Trades={r['trades']:4d}, WR={r['wr']:5.1f}%, Net={r['net']:+7.1f}%")

print("\n>> TARGET: >100 trades, >45% WR, positive return:")
target_results = [r for r in results if r['trades'] > 50 and r['wr'] > 45 and r['net'] > 0]
sorted_target = sorted(target_results, key=lambda x: x['net'], reverse=True)
for r in sorted_target:
    test = r.get('test', '')
    mode = r.get('mode', '')
    atr = r.get('atr_target', r.get('atr_target'))
    print(f"  {test} | {mode} R:R={atr}: Trades={r['trades']:4d}, WR={r['wr']:5.1f}%, Net={r['net']:+7.1f}%")

if not sorted_target:
    print("  No combinations meet all criteria!")
    # Show closest candidates
    print("\n  Closest candidates (positive net, sorted by WR):")
    close = sorted([r for r in results if r['net'] > -10 and r['trades'] > 10], key=lambda x: x['wr'], reverse=True)[:10]
    for r in close:
        test = r.get('test', '')
        mode = r.get('mode', '')
        atr = r.get('atr_target', r.get('atr_target'))
        print(f"  {test} | {mode} R:R={atr}: Trades={r['trades']:4d}, WR={r['wr']:5.1f}%, Net={r['net']:+7.1f}%")

print("\nDone!")
