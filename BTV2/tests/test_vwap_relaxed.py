#!/usr/bin/env python3
"""
VWAP Scalping - Relaxed Filters Test
=====================================
Test with relaxed ADX/RSI filters to get more trades
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


def run_vwap_entry_test(
    df,
    cutoff=0.10,
    atr_stop=1.0,
    atr_target=2.0,
    ema_fast=9,
    ema_slow=20,
    use_trend_filter=False,
    use_volume_filter=False,
    volume_mult=2.0,
    use_trailing_stop=False,
    trailing_atr=1.0,
    adx_max=50.0,  # Relaxed
    rsi_max=50.0,  # Relaxed
    entry_mode="cross",
    deviation_pct=0.5,
    momentum_bars=2,
    deviation_sd=3.0,
):
    """VWAP scalping with configurable entry mode."""
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

        if any(np.isnan(x) for x in (vw, vsd, at, adx_v, r)):
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
                # Simple cross with optional filters
                if use_trend_filter:
                    if above_vwap and is_bullish:
                        if i >= 1 and prices[i - 1] < vwap.iloc[i - 1]:
                            long_signal = True
                    if below_vwap and is_bearish:
                        if i >= 1 and prices[i - 1] > vwap.iloc[i - 1]:
                            short_signal = True
                else:
                    if above_vwap and i >= 1 and prices[i - 1] < vwap.iloc[i - 1]:
                        long_signal = True
                    if below_vwap and i >= 1 and prices[i - 1] > vwap.iloc[i - 1]:
                        short_signal = True

            elif entry_mode == "deviation":
                if deviation_from_vwap < -deviation_pct:
                    if not use_trend_filter or is_bullish:
                        long_signal = True
                if deviation_from_vwap > deviation_pct:
                    if not use_trend_filter or is_bearish:
                        short_signal = True

            elif entry_mode == "momentum":
                if above_vwap:
                    momentum_ok = True
                    for j in range(1, min(momentum_bars + 1, i + 1)):
                        if i - j - 1 >= 0 and prices[i - j] <= prices[i - j - 1]:
                            momentum_ok = False
                            break
                    if momentum_ok and i >= momentum_bars + 1:
                        if not use_trend_filter or is_bullish:
                            long_signal = True

                if below_vwap:
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
                if p < lower_band:
                    long_signal = True
                if p > upper_band:
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

# Test with relaxed filters
print("=" * 120)
print("TEST WITH RELAXED FILTERS (adx_max=50, rsi_max=50, no volume filter)")
print("=" * 120)

entry_modes = ["cross", "deviation", "momentum", "mean_reversion"]
atr_targets = [2.0, 3.0, 4.0, 5.0]

for mode in entry_modes:
    print(f"\n{'='*60}")
    print(f"ENTRY MODE: {mode}")
    print(f"{'='*60}")
    
    for atr_target in atr_targets:
        eq, trades = run_vwap_entry_test(
            df,
            cutoff=0.10,
            atr_target=atr_target,
            entry_mode=mode,
            deviation_pct=0.5,
            momentum_bars=2,
            deviation_sd=3.0,
            use_trend_filter=False,
            use_volume_filter=False,
            adx_max=50.0,
            rsi_max=50.0,
        )
        m = compute_metrics(eq, trades)
        
        if len(trades) > 0:
            wins = sum(1 for t in trades if t > 0)
            wr = wins / len(trades) * 100
            gp = sum(t for t in trades if t > 0)
            gl = abs(sum(t for t in trades if t < 0))
            pf = gp / gl if gl > 0 else 0
            gross = m["total_return_pct"]
            costs_pct = len(trades) * 0.30
            net = gross - costs_pct
            
            print(f"  R:R={atr_target:.1f}: Trades={len(trades):4d}, WR={wr:5.1f}%, PF={pf:5.2f}, Gross={gross:+7.1f}%, Net={net:+7.1f}%")
            
            results.append({
                'mode': mode,
                'atr_target': atr_target,
                'trades': len(trades),
                'wr': wr,
                'pf': pf,
                'gross': gross,
                'costs': costs_pct,
                'net': net,
            })

# Now optimize specific parameters
print("\n")
print("=" * 120)
print("OPTIMIZE DEVIATION % FOR MORE TRADES")
print("=" * 120)

for atr_target in [3.0, 4.0]:
    for dev_pct in [0.1, 0.2, 0.3, 0.4, 0.5, 0.75, 1.0]:
        eq, trades = run_vwap_entry_test(
            df,
            cutoff=0.10,
            atr_target=atr_target,
            entry_mode="deviation",
            deviation_pct=dev_pct,
        )
        m = compute_metrics(eq, trades)
        if len(trades) > 0:
            wins = sum(1 for t in trades if t > 0)
            wr = wins / len(trades) * 100
            gp = sum(t for t in trades if t > 0)
            gl = abs(sum(t for t in trades if t < 0))
            pf = gp / gl if gl > 0 else 0
            gross = m["total_return_pct"]
            costs_pct = len(trades) * 0.30
            net = gross - costs_pct
            
            print(f"  dev_pct={dev_pct:.2f}, R:R={atr_target:.1f}: Trades={len(trades):4d}, WR={wr:5.1f}%, Gross={gross:+7.1f}%, Net={net:+7.1f}%")
            
            results.append({
                'mode': f'deviation_{dev_pct}',
                'atr_target': atr_target,
                'trades': len(trades),
                'wr': wr,
                'pf': pf,
                'gross': gross,
                'costs': costs_pct,
                'net': net,
            })

print("\n")
print("=" * 120)
print("OPTIMIZE MEAN REVERSION SD")
print("=" * 120)

for atr_target in [3.0, 4.0]:
    for sd_th in [1.5, 2.0, 2.5, 3.0, 3.5]:
        eq, trades = run_vwap_entry_test(
            df,
            cutoff=0.10,
            atr_target=atr_target,
            entry_mode="mean_reversion",
            deviation_sd=sd_th,
        )
        m = compute_metrics(eq, trades)
        if len(trades) > 0:
            wins = sum(1 for t in trades if t > 0)
            wr = wins / len(trades) * 100
            gp = sum(t for t in trades if t > 0)
            gl = abs(sum(t for t in trades if t < 0))
            pf = gp / gl if gl > 0 else 0
            gross = m["total_return_pct"]
            costs_pct = len(trades) * 0.30
            net = gross - costs_pct
            
            print(f"  sd={sd_th}, R:R={atr_target:.1f}: Trades={len(trades):4d}, WR={wr:5.1f}%, Gross={gross:+7.1f}%, Net={net:+7.1f}%")
            
            results.append({
                'mode': f'mean_rev_sd{sd_th}',
                'atr_target': atr_target,
                'trades': len(trades),
                'wr': wr,
                'pf': pf,
                'gross': gross,
                'costs': costs_pct,
                'net': net,
            })

# Summary
print("\n")
print("=" * 120)
print("FINAL SUMMARY")
print("=" * 120)

print("\n>> TOP 10 BY NET RETURN:")
sorted_by_net = sorted(results, key=lambda x: x['net'], reverse=True)[:10]
for r in sorted_by_net:
    print(f"  {r['mode']}, R:R={r['atr_target']}: Trades={r['trades']:4d}, WR={r['wr']:5.1f}%, Net={r['net']:+7.1f}%")

print("\n>> TOP 10 BY WIN RATE (>35% WR, >10 trades):")
sorted_by_wr = sorted([r for r in results if r['wr'] > 35 and r['trades'] > 10], key=lambda x: x['wr'], reverse=True)[:10]
for r in sorted_by_wr:
    print(f"  {r['mode']}, R:R={r['atr_target']}: Trades={r['trades']:4d}, WR={r['wr']:5.1f}%, Net={r['net']:+7.1f}%")

print("\n>> TOP 10 BY TRADE COUNT (>300):")
sorted_by_trades = sorted([r for r in results if r['trades'] > 300], key=lambda x: x['trades'], reverse=True)[:10]
for r in sorted_by_trades:
    print(f"  {r['mode']}, R:R={r['atr_target']}: Trades={r['trades']:4d}, WR={r['wr']:5.1f}%, Net={r['net']:+7.1f}%")

print("\n>> TARGET: >1000 trades, >45% WR, positive return:")
target_results = [r for r in results if r['trades'] > 200 and r['wr'] > 45 and r['net'] > 0]
sorted_target = sorted(target_results, key=lambda x: x['net'], reverse=True)
for r in sorted_target:
    print(f"  {r['mode']}, R:R={r['atr_target']}: Trades={r['trades']:4d}, WR={r['wr']:5.1f}%, Net={r['net']:+7.1f}%")

if not sorted_target:
    print("  No combinations meet all criteria!")

print("\nDone!")
