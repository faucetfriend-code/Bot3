#!/usr/bin/env python3
"""VWAP - Test inverted strategy"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np

STORAGE_ROOT = Path("G:/Candle Data")
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    apply_butterworth, compute_vwap_rolling, compute_atr, 
    compute_adx, compute_rsi, compute_ema, COST_PER_SIDE
)

# Load data
print("Loading data...")
df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
df.columns = [c.capitalize() for c in df.columns]
df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
print(f"Loaded {len(df):,} bars")

# Pre-compute indicators as arrays
fc = apply_butterworth(df["Close"], 0.10)
vwap, vs = compute_vwap_rolling(df["High"], df["Low"], fc, df["Volume"], 20)
atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
adx = compute_adx(df["High"], df["Low"], df["Close"], 14)
rsi = compute_rsi(fc, 14)
ema_f = compute_ema(fc, 9)
ema_m = compute_ema(fc, 20)
vol_avg = df["Volume"].rolling(20).mean()

prices = df["Close"].values.astype(float)
highs = df["High"].values.astype(float)
lows = df["Low"].values.astype(float)
opens = df["Open"].values.astype(float)
volumes = df["Volume"].values.astype(float)
vwap_arr = vwap.values.astype(np.float64)
atr_arr = atr.values.astype(np.float64)
adx_arr = adx.values.astype(np.float64)
rsi_arr = rsi.values.astype(np.float64)
ema_f_arr = ema_f.values.astype(np.float64)
ema_m_arr = ema_m.values.astype(np.float64)
vol_avg_arr = vol_avg.values.astype(np.float64)
n = len(prices)

def run_vwap_inverted(df_exit=None, **params):
    """Inverted VWAP - opposite signals"""
    equity_arr = np.ones(n, dtype=float)
    curr_equity = 1.0
    in_pos = [False]
    side = [""]
    entry_eq = [1.0]
    sl = [0.0]
    tp_ref = [0.0]
    closed_trades = []

    for i in range(1, n):
        vw = vwap_arr[i]
        at = atr_arr[i]
        adx_v = adx_arr[i]
        r = rsi_arr[i]
        p = prices[i]
        p0 = prices[i - 1]
        o = opens[i]
        v = volumes[i]
        va = vol_avg_arr[i]

        ema_f_val = ema_f_arr[i]
        ema_m_val = ema_m_arr[i]

        if np.isnan(vw) or np.isnan(at) or np.isnan(adx_v) or np.isnan(r):
            equity_arr[i] = curr_equity
            continue

        is_bullish = ema_f_val > ema_m_val
        is_bearish = ema_f_val < ema_m_val

        above_vwap = p > vw
        below_vwap = p < vw

        volume_surge = v >= params.get("volume_mult", 1.0) * va if params.get("use_volume_filter", False) else True

        ranging = adx_v < params.get("adx_max", 50.0)
        rsi_long_ok = r < params.get("rsi_max", 70.0)
        rsi_short_ok = r > (100.0 - params.get("rsi_max", 70.0))

        if in_pos[0]:
            curr_equity *= p / p0
            if side[0] == "long":
                if lows[i] <= sl[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif highs[i] >= tp_ref[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
            else:
                if highs[i] >= sl[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif lows[i] <= tp_ref[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
        else:
            # INVERTED: LONG when price crosses BELOW VWAP (mean reversion)
            # INVERTED: SHORT when price crosses ABOVE VWAP
            long_signal = False
            short_signal = False
            
            if ranging and rsi_long_ok:
                # INVERTED: Go long when price crosses BELOW VWAP (buy the dip)
                if below_vwap and i >= 1 and prices[i - 1] > vwap_arr[i - 1]:
                    if params.get("use_trend_filter", False):
                        if is_bearish and volume_surge:  # INVERTED: bearish
                            long_signal = True
                    else:
                        long_signal = True

            if ranging and rsi_short_ok:
                # INVERTED: Go short when price crosses ABOVE VWAP (sell the rip)
                if above_vwap and i >= 1 and prices[i - 1] < vwap_arr[i - 1]:
                    if params.get("use_trend_filter", False):
                        if is_bullish and volume_surge:  # INVERTED: bullish
                            short_signal = True
                    else:
                        short_signal = True

            atr_stop = params.get("atr_stop", 0.5)
            atr_target = params.get("atr_target", 2.0)
            
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


def compute_metrics(equity, trades):
    if len(equity) < 2 or len(trades) < 10:
        return None
    final = equity[-1]
    total_ret = (final / 1.0 - 1.0) * 100.0
    
    daily = np.diff(equity.values)
    daily = daily[daily != 0]
    if len(daily) < 2:
        return None
    
    import math
    sharpe = np.mean(daily) / (np.std(daily) + 1e-10) * math.sqrt(105120)
    
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    win_rate = len(wins) / len(trades) * 100.0
    gp = sum(wins) * 100.0
    gl = abs(sum(losses)) * 100.0
    pf = gp / gl if gl > 0 else 999.0
    
    return {"trades": len(trades), "win_rate_pct": win_rate, "profit_factor": pf,
            "total_return_pct": total_ret}


print("\nTesting INVERTED strategy (mean reversion)...")

results = []
for adx in [25, 35, 50, 100]:
    for rsi in [40, 60, 80]:
        for atr in [0.5, 1.0]:
            params = {
                "atr_stop": atr,
                "atr_target": 2.0,
                "use_trend_filter": False,
                "use_volume_filter": False,
                "volume_mult": 1.0,
                "adx_max": float(adx),
                "rsi_max": float(rsi),
            }
            eq, trades = run_vwap_inverted(**params)
            m = compute_metrics(eq, trades)
            if m:
                gross = m['total_return_pct']
                net = gross - len(trades) * 0.30
                results.append({**params, **m, 'gross': gross, 'net': net})
                status = "✓" if (m['trades'] >= 1000 and m['win_rate_pct'] >= 50 and net > 0) else ""
                print(f"ADX={adx}, RSI={rsi}, ATR={atr} | Trades={m['trades']}, WR={m['win_rate_pct']:.1f}%, PF={m['profit_factor']:.2f}, Net={net:+.2f}% {status}")

# Sort by net return
results.sort(key=lambda x: x['net'], reverse=True)
print("\n\nTop results:")
for r in results[:10]:
    print(f"ADX={r['adx_max']:.0f}, RSI={r['rsi_max']:.0f}, ATR={r['atr_stop']} | Trades={r['trades']}, WR={r['win_rate_pct']:.1f}%, PF={r['profit_factor']:.2f}, Net={r['net']:+.2f}%")
