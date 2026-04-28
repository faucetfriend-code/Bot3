#!/usr/bin/env python3
"""
VWAP Scalping - Quick Test
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


def run_vwap_test(df, params):
    """VWAP scalping with configurable parameters."""
    fc = apply_butterworth(df["Close"], params.get('cutoff', 0.10))
    vwap, vs = compute_vwap_rolling(df["High"], df["Low"], fc, df["Volume"], 20)
    atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
    adx = compute_adx(df["High"], df["Low"], df["Close"], 14)
    rsi = compute_rsi(fc, 14)
    ema_f = compute_ema(fc, params.get('ema_fast', 9))
    ema_m = compute_ema(fc, params.get('ema_slow', 20))
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
        volume_mult = params.get('volume_mult', 1.5)
        use_vol = params.get('use_volume_filter', True)
        volume_surge = v >= volume_mult * va if use_vol else True
        ranging = adx_v < params.get('adx_max', 25)
        rsi_max = params.get('rsi_max', 50)
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
            
            entry_mode = params.get('entry_mode', 'cross')
            
            if entry_mode == "cross":
                if ranging and rsi_long_ok:
                    if above_vwap and volume_surge:
                        if params.get('use_trend_filter', True) and is_bullish:
                            if i >= 1 and prices[i - 1] < vwap.iloc[i - 1]:
                                long_signal = True
                        elif not params.get('use_trend_filter', True):
                            if i >= 1 and prices[i - 1] < vwap.iloc[i - 1]:
                                long_signal = True

                if ranging and rsi_short_ok:
                    if below_vwap and volume_surge:
                        if params.get('use_trend_filter', True) and is_bearish:
                            if i >= 1 and prices[i - 1] > vwap.iloc[i - 1]:
                                short_signal = True
                        elif not params.get('use_trend_filter', True):
                            if i >= 1 and prices[i - 1] > vwap.iloc[i - 1]:
                                short_signal = True

            elif entry_mode == "deviation":
                dev_pct = params.get('deviation_pct', 0.5)
                if ranging and rsi_long_ok:
                    if deviation_from_vwap < -dev_pct and volume_surge:
                        if not params.get('use_trend_filter', True) or is_bullish:
                            long_signal = True
                if ranging and rsi_short_ok:
                    if deviation_from_vwap > dev_pct and volume_surge:
                        if not params.get('use_trend_filter', True) or is_bearish:
                            short_signal = True

            elif entry_mode == "momentum":
                mom_bars = params.get('momentum_bars', 2)
                if ranging and rsi_long_ok:
                    if above_vwap and volume_surge:
                        momentum_ok = True
                        for j in range(1, min(mom_bars + 1, i + 1)):
                            if i - j - 1 >= 0 and prices[i - j] <= prices[i - j - 1]:
                                momentum_ok = False
                                break
                        if momentum_ok and i >= mom_bars + 1:
                            if not params.get('use_trend_filter', True) or is_bullish:
                                long_signal = True
                if ranging and rsi_short_ok:
                    if below_vwap and volume_surge:
                        momentum_ok = True
                        for j in range(1, min(mom_bars + 1, i + 1)):
                            if i - j - 1 >= 0 and prices[i - j] >= prices[i - j - 1]:
                                momentum_ok = False
                                break
                        if momentum_ok and i >= mom_bars + 1:
                            if not params.get('use_trend_filter', True) or is_bearish:
                                short_signal = True

            elif entry_mode == "mean_reversion":
                dev_sd = params.get('deviation_sd', 2.5)
                upper_band = vw + dev_sd * vsd
                lower_band = vw - dev_sd * vsd
                if rsi_long_ok and p < lower_band and volume_surge:
                    long_signal = True
                if rsi_short_ok and p > upper_band and volume_surge:
                    short_signal = True

            atr_stop = params.get('atr_stop', 1.0)
            atr_target = params.get('atr_target', 2.0)

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

# Quick test of key combinations
tests = [
    # Cross mode with different filters
    {"name": "cross_adx25_rsi50", "entry_mode": "cross", "adx_max": 25, "rsi_max": 50, "atr_target": 2.0},
    {"name": "cross_adx25_rsi50_RR3", "entry_mode": "cross", "adx_max": 25, "rsi_max": 50, "atr_target": 3.0},
    {"name": "cross_adx25_rsi50_RR4", "entry_mode": "cross", "adx_max": 25, "rsi_max": 50, "atr_target": 4.0},
    {"name": "cross_adx25_rsi50_RR5", "entry_mode": "cross", "adx_max": 25, "rsi_max": 50, "atr_target": 5.0},
    {"name": "cross_adx30_rsi50", "entry_mode": "cross", "adx_max": 30, "rsi_max": 50, "atr_target": 3.0},
    {"name": "cross_adx35_rsi50", "entry_mode": "cross", "adx_max": 35, "rsi_max": 50, "atr_target": 3.0},
    {"name": "cross_no_vol", "entry_mode": "cross", "adx_max": 30, "rsi_max": 50, "atr_target": 3.0, "use_volume_filter": False},
    {"name": "cross_no_trend", "entry_mode": "cross", "adx_max": 30, "rsi_max": 50, "atr_target": 3.0, "use_trend_filter": False},
    
    # Deviation mode
    {"name": "dev_0.5", "entry_mode": "deviation", "deviation_pct": 0.5, "adx_max": 30, "rsi_max": 50, "atr_target": 3.0},
    {"name": "dev_0.75", "entry_mode": "deviation", "deviation_pct": 0.75, "adx_max": 30, "rsi_max": 50, "atr_target": 3.0},
    {"name": "dev_1.0", "entry_mode": "deviation", "deviation_pct": 1.0, "adx_max": 30, "rsi_max": 50, "atr_target": 3.0},
    {"name": "dev_1.0_RR4", "entry_mode": "deviation", "deviation_pct": 1.0, "adx_max": 30, "rsi_max": 50, "atr_target": 4.0},
    {"name": "dev_1.0_RR5", "entry_mode": "deviation", "deviation_pct": 1.0, "adx_max": 30, "rsi_max": 50, "atr_target": 5.0},
    
    # Momentum mode
    {"name": "mom_2bars", "entry_mode": "momentum", "momentum_bars": 2, "adx_max": 30, "rsi_max": 50, "atr_target": 3.0},
    {"name": "mom_3bars", "entry_mode": "momentum", "momentum_bars": 3, "adx_max": 30, "rsi_max": 50, "atr_target": 3.0},
    {"name": "mom_2bars_RR4", "entry_mode": "momentum", "momentum_bars": 2, "adx_max": 30, "rsi_max": 50, "atr_target": 4.0},
    {"name": "mom_2bars_RR5", "entry_mode": "momentum", "momentum_bars": 2, "adx_max": 30, "rsi_max": 50, "atr_target": 5.0},
    
    # Mean reversion
    {"name": "mr_sd2.0", "entry_mode": "mean_reversion", "deviation_sd": 2.0, "adx_max": 30, "rsi_max": 50, "atr_target": 3.0},
    {"name": "mr_sd2.5", "entry_mode": "mean_reversion", "deviation_sd": 2.5, "adx_max": 30, "rsi_max": 50, "atr_target": 3.0},
    {"name": "mr_sd3.0", "entry_mode": "mean_reversion", "deviation_sd": 3.0, "adx_max": 30, "rsi_max": 50, "atr_target": 3.0},
    {"name": "mr_sd2.5_RR4", "entry_mode": "mean_reversion", "deviation_sd": 2.5, "adx_max": 30, "rsi_max": 50, "atr_target": 4.0},
    {"name": "mr_sd2.5_RR5", "entry_mode": "mean_reversion", "deviation_sd": 2.5, "adx_max": 30, "rsi_max": 50, "atr_target": 5.0},
]

print("Running tests...")
for test in tests:
    name = test.pop("name")
    eq, trades = run_vwap_test(df, test)
    m = compute_metrics(eq, trades)
    
    if len(trades) >= 3:
        wins = sum(1 for t in trades if t > 0)
        wr = wins / len(trades) * 100
        gp = sum(t for t in trades if t > 0)
        gl = abs(sum(t for t in trades if t < 0))
        pf = gp / gl if gl > 0 else 0
        gross = m["total_return_pct"]
        costs_pct = len(trades) * 0.30
        net = gross - costs_pct
        
        results.append({
            'name': name,
            'trades': len(trades),
            'wr': wr,
            'pf': pf,
            'gross': gross,
            'net': net,
        })
        print(f"{name}: Trades={len(trades):4d}, WR={wr:5.1f}%, PF={pf:5.2f}, Gross={gross:+7.1f}%, Net={net:+7.1f}%")
    else:
        print(f"{name}: Too few trades ({len(trades)})")

# Summary
print("\n")
print("=" * 100)
print("FINAL SUMMARY")
print("=" * 100)

print("\n>> TOP 10 BY NET RETURN:")
sorted_by_net = sorted(results, key=lambda x: x['net'], reverse=True)[:10]
for r in sorted_by_net:
    print(f"  {r['name']}: Trades={r['trades']:4d}, WR={r['wr']:5.1f}%, Net={r['net']:+7.1f}%")

print("\n>> TOP 10 BY WIN RATE (>30% WR):")
sorted_by_wr = sorted([r for r in results if r['wr'] > 30], key=lambda x: x['wr'], reverse=True)[:10]
for r in sorted_by_wr:
    print(f"  {r['name']}: Trades={r['trades']:4d}, WR={r['wr']:5.1f}%, Net={r['net']:+7.1f}%")

print("\n>> TARGET: >50 trades, >45% WR, positive net:")
target = [r for r in results if r['trades'] > 50 and r['wr'] > 45 and r['net'] > 0]
if target:
    for r in sorted(target, key=lambda x: x['net'], reverse=True):
        print(f"  {r['name']}: Trades={r['trades']:4d}, WR={r['wr']:5.1f}%, Net={r['net']:+7.1f}%")
else:
    print("  No combinations meet all criteria!")
    
print("\nDone!")
