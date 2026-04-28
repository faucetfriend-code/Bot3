#!/usr/bin/env python3
"""
VWAP Scalping - Entry Logic & R:R Ratio Testing
=================================================
Tests different entry modes and higher R:R ratios to improve profitability.

Entry Modes:
1. "cross" - Price crosses ABOVE/BELOW VWAP (original)
2. "deviation" - Enter when price is X% below/above VWAP
3. "momentum" - Price crossing VWAP + momentum confirmation
4. "mean_reversion" - Enter when price is far from VWAP (3-4 SD)

R:R Ratios: 2.0, 3.0, 4.0, 5.0 (atr_target multipliers)
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

STORAGE_ROOT = Path("G:/Candle Data")


def load_parquet(symbol, interval):
    """Load parquet data file."""
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
    sd_threshold=2.5,
    atr_stop=1.0,
    atr_target=2.0,
    ema_fast=9,
    ema_slow=20,
    use_trend_filter=True,
    use_volume_filter=True,
    volume_mult=2.0,
    use_trailing_stop=True,
    trailing_atr=1.0,
    adx_max=22.0,
    rsi_max=45.0,
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
    entry_price = [0.0]
    trailing_stop = [0.0]
    closed_trades = []

    bar_dur = df.index[1] - df.index[0] if len(df) > 1 else pd.Timedelta(minutes=5)

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

        # Trend direction
        is_bullish = ema_f_val > ema_m_val
        is_bearish = ema_f_val < ema_m_val

        # Price relative to VWAP
        above_vwap = p > vw
        below_vwap = p < vw

        # Volume confirmation
        volume_surge = v >= volume_mult * va if use_volume_filter else True

        # Ranging + RSI gates
        ranging = adx_v < adx_max
        rsi_long_ok = r < rsi_max
        rsi_short_ok = r > (100.0 - rsi_max)

        # Check exits first
        if in_pos[0]:
            curr_equity *= p / p0
            nxt = df.index[i] + bar_dur

            # Check SL/TP using H/L
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

            # Trailing stop
            if use_trailing_stop and in_pos[0]:
                if side[0] == "long":
                    new_ts = p - trailing_atr * at
                    trailing_stop[0] = max(trailing_stop[0], new_ts)
                    if lows[i] <= trailing_stop[0]:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
                else:
                    new_ts = p + trailing_atr * at
                    trailing_stop[0] = min(trailing_stop[0], new_ts)
                    if highs[i] >= trailing_stop[0]:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
        else:
            # Entry logic - Multiple entry modes
            long_signal = False
            short_signal = False

            # Calculate price deviation from VWAP (as percentage)
            deviation_from_vwap = (p - vw) / (vw + 1e-10) * 100.0 if vw > 0 else 0.0

            if entry_mode == "cross":
                # Original: Price crosses ABOVE/BELOW VWAP
                if ranging and rsi_long_ok:
                    if use_trend_filter:
                        if above_vwap and is_bullish and volume_surge:
                            if i >= 1 and prices[i - 1] < vwap.iloc[i - 1]:
                                long_signal = True
                    else:
                        if above_vwap and i >= 1 and prices[i - 1] < vwap.iloc[i - 1]:
                            long_signal = True

                if ranging and rsi_short_ok:
                    if use_trend_filter:
                        if below_vwap and is_bearish and volume_surge:
                            if i >= 1 and prices[i - 1] > vwap.iloc[i - 1]:
                                short_signal = True
                    else:
                        if below_vwap and i >= 1 and prices[i - 1] > vwap.iloc[i - 1]:
                            short_signal = True

            elif entry_mode == "deviation":
                # Enter when price is X% below VWAP (long) or above VWAP (short)
                if ranging and rsi_long_ok:
                    if deviation_from_vwap < -deviation_pct and volume_surge:
                        if use_trend_filter and is_bullish:
                            long_signal = True
                        elif not use_trend_filter:
                            long_signal = True

                if ranging and rsi_short_ok:
                    if deviation_from_vwap > deviation_pct and volume_surge:
                        if use_trend_filter and is_bearish:
                            short_signal = True
                        elif not use_trend_filter:
                            short_signal = True

            elif entry_mode == "momentum":
                # Price crossing VWAP + price moved in entry direction for N bars
                if ranging and rsi_long_ok:
                    if above_vwap and volume_surge:
                        momentum_ok = True
                        for j in range(1, min(momentum_bars + 1, i + 1)):
                            if prices[i - j] <= prices[i - j - 1]:
                                momentum_ok = False
                                break
                        if momentum_ok and i >= momentum_bars + 1:
                            if use_trend_filter and is_bullish:
                                long_signal = True
                            elif not use_trend_filter:
                                long_signal = True

                if ranging and rsi_short_ok:
                    if below_vwap and volume_surge:
                        momentum_ok = True
                        for j in range(1, min(momentum_bars + 1, i + 1)):
                            if prices[i - j] >= prices[i - j - 1]:
                                momentum_ok = False
                                break
                        if momentum_ok and i >= momentum_bars + 1:
                            if use_trend_filter and is_bearish:
                                short_signal = True
                            elif not use_trend_filter:
                                short_signal = True

            elif entry_mode == "mean_reversion":
                # Enter when price is FAR from VWAP (e.g., 3-4 SD) expecting pullback
                upper_band = vw + deviation_sd * vsd
                lower_band = vw - deviation_sd * vsd

                if rsi_long_ok:
                    if p < lower_band and volume_surge:
                        long_signal = True

                if rsi_short_ok:
                    if p > upper_band and volume_surge:
                        short_signal = True

            if long_signal:
                curr_equity = _open_long(
                    curr_equity, entry_eq, sl, tp_ref, o,
                    o - atr_stop * at, o + atr_target * at, in_pos, side,
                )
                if in_pos[0]:
                    entry_price[0] = o
                    trailing_stop[0] = o - trailing_atr * at

            elif short_signal:
                curr_equity = _open_short(
                    curr_equity, entry_eq, sl, tp_ref, o,
                    o + atr_stop * at, o - atr_target * at, in_pos, side,
                )
                if in_pos[0]:
                    entry_price[0] = o
                    trailing_stop[0] = o + trailing_atr * at

        equity_arr[i] = curr_equity

    if in_pos[0]:
        curr_equity *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
        equity_arr[-1] = curr_equity

    return pd.Series(equity_arr, index=df.index), closed_trades


def _open_long(curr_equity, entry_eq_ref, sl_ref, tp_ref, price, sl_price, tp_price, in_pos, side):
    """Deduct entry cost and set position metadata."""
    curr_equity *= (1.0 - COST_PER_SIDE)
    entry_eq_ref[0] = curr_equity
    sl_ref[0] = sl_price
    tp_ref[0] = tp_price
    in_pos[0] = True
    side[0] = "long"
    return curr_equity


def _open_short(curr_equity, entry_eq_ref, sl_ref, tp_ref, price, sl_price, tp_price, in_pos, side):
    """Deduct entry cost and set position metadata."""
    curr_equity *= (1.0 - COST_PER_SIDE)
    entry_eq_ref[0] = curr_equity
    sl_ref[0] = sl_price
    tp_ref[0] = tp_price
    in_pos[0] = True
    side[0] = "short"
    return curr_equity


def format_result(name, m, n_trades, wr, pf, gross, net):
    """Format result row for display."""
    return f"{name}: Return={gross:+.1f}%, WR={wr:.0f}%, PF={pf:.2f}, Trades={n_trades}, Net={net:+.1f}%"


# Load data
print("Loading BTCUSDT 5m data...")
df = load_parquet("BTCUSDT", "5m")
df.columns = [c.capitalize() for c in df.columns]
# Test on 2024 data
df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
print(f"Loaded {len(df)} bars (2024)")
print()

results = []

# ============================================================================
# TEST 1: ENTRY MODES WITH DEFAULT 2:1 R:R
# ============================================================================
print("=" * 100)
print("TEST 1: ENTRY MODES (atr_target=2.0, default parameters)")
print("=" * 100)

entry_modes = ["cross", "deviation", "momentum", "mean_reversion"]

for mode in entry_modes:
    eq, trades = run_vwap_entry_test(
        df,
        cutoff=0.10,
        atr_target=2.0,
        entry_mode=mode,
        deviation_pct=0.5,
        momentum_bars=2,
        deviation_sd=3.0,
    )
    m = compute_metrics(eq, trades)
    if trades:
        wins = sum(1 for t in trades if t > 0)
        wr = wins / len(trades) * 100
        gp = sum(t for t in trades if t > 0)
        gl = abs(sum(t for t in trades if t < 0))
        pf = gp / gl if gl > 0 else 0
    else:
        wr, pf = 0, 0

    gross = m["total_return_pct"]
    costs = len(trades) * 0.30
    net = gross - costs
    print(format_result(f"Mode={mode}", m, len(trades), wr, pf, gross, net))
    results.append(("ENTRY_MODE", mode, 2.0, m, len(trades), wr, pf, gross, costs, net))

print()

# ============================================================================
# TEST 2: ENTRY MODES WITH HIGHER R:R RATIOS
# ============================================================================
print("=" * 100)
print("TEST 2: HIGHER R:R RATIOS FOR EACH ENTRY MODE")
print("=" * 100)

atr_targets = [2.0, 3.0, 4.0, 5.0]

for mode in entry_modes:
    print(f"\n--- Entry Mode: {mode} ---")
    for atr_target in atr_targets:
        eq, trades = run_vwap_entry_test(
            df,
            cutoff=0.10,
            atr_target=atr_target,
            entry_mode=mode,
            deviation_pct=0.5,
            momentum_bars=2,
            deviation_sd=3.0,
        )
        m = compute_metrics(eq, trades)
        if trades:
            wins = sum(1 for t in trades if t > 0)
            wr = wins / len(trades) * 100
            gp = sum(t for t in trades if t > 0)
            gl = abs(sum(t for t in trades if t < 0))
            pf = gp / gl if gl > 0 else 0
        else:
            wr, pf = 0, 0

        gross = m["total_return_pct"]
        costs = len(trades) * 0.30
        net = gross - costs
        print(format_result(f"R:R={atr_target}:1", m, len(trades), wr, pf, gross, net))
        results.append(
            ("R:R_TEST", mode, atr_target, m, len(trades), wr, pf, gross, costs, net)
        )

print()

# ============================================================================
# TEST 3: OPTIMIZE DEVIATION PARAMETERS
# ============================================================================
print("=" * 100)
print("TEST 3: OPTIMIZE DEVIATION PARAMETERS (mode=deviation)")
print("=" * 100)

deviation_pcts = [0.3, 0.5, 0.75, 1.0, 1.5, 2.0]
atr_target = 3.0  # Use 3:1 R:R

for dev_pct in deviation_pcts:
    eq, trades = run_vwap_entry_test(
        df,
        cutoff=0.10,
        atr_target=atr_target,
        entry_mode="deviation",
        deviation_pct=dev_pct,
    )
    m = compute_metrics(eq, trades)
    if trades:
        wins = sum(1 for t in trades if t > 0)
        wr = wins / len(trades) * 100
        gp = sum(t for t in trades if t > 0)
        gl = abs(sum(t for t in trades if t < 0))
        pf = gp / gl if gl > 0 else 0
    else:
        wr, pf = 0, 0

    gross = m["total_return_pct"]
    costs = len(trades) * 0.30
    net = gross - costs
    print(format_result(f"dev_pct={dev_pct}%", m, len(trades), wr, pf, gross, net))
    results.append(
        ("DEVIATION_PCT", "deviation", atr_target, m, len(trades), wr, pf, gross, costs, net, dev_pct)
    )

print()

# ============================================================================
# TEST 4: OPTIMIZE MOMENTUM BARS
# ============================================================================
print("=" * 100)
print("TEST 4: OPTIMIZE MOMENTUM BARS (mode=momentum)")
print("=" * 100)

momentum_bars_list = [1, 2, 3, 4, 5]
atr_target = 3.0

for mom_bars in momentum_bars_list:
    eq, trades = run_vwap_entry_test(
        df,
        cutoff=0.10,
        atr_target=atr_target,
        entry_mode="momentum",
        momentum_bars=mom_bars,
    )
    m = compute_metrics(eq, trades)
    if trades:
        wins = sum(1 for t in trades if t > 0)
        wr = wins / len(trades) * 100
        gp = sum(t for t in trades if t > 0)
        gl = abs(sum(t for t in trades if t < 0))
        pf = gp / gl if gl > 0 else 0
    else:
        wr, pf = 0, 0

    gross = m["total_return_pct"]
    costs = len(trades) * 0.30
    net = gross - costs
    print(format_result(f"mom_bars={mom_bars}", m, len(trades), wr, pf, gross, net))
    results.append(
        ("MOMENTUM_BARS", "momentum", atr_target, m, len(trades), wr, pf, gross, costs, net, mom_bars)
    )

print()

# ============================================================================
# TEST 5: OPTIMIZE MEAN-REVERSION SD THRESHOLD
# ============================================================================
print("=" * 100)
print("TEST 5: OPTIMIZE SD THRESHOLD (mode=mean_reversion)")
print("=" * 100)

sd_thresholds = [2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
atr_target = 3.0

for sd_th in sd_thresholds:
    eq, trades = run_vwap_entry_test(
        df,
        cutoff=0.10,
        atr_target=atr_target,
        entry_mode="mean_reversion",
        deviation_sd=sd_th,
    )
    m = compute_metrics(eq, trades)
    if trades:
        wins = sum(1 for t in trades if t > 0)
        wr = wins / len(trades) * 100
        gp = sum(t for t in trades if t > 0)
        gl = abs(sum(t for t in trades if t < 0))
        pf = gp / gl if gl > 0 else 0
    else:
        wr, pf = 0, 0

    gross = m["total_return_pct"]
    costs = len(trades) * 0.30
    net = gross - costs
    print(format_result(f"sd={sd_th}", m, len(trades), wr, pf, gross, net))
    results.append(
        ("SD_THRESHOLD", "mean_reversion", atr_target, m, len(trades), wr, pf, gross, costs, net, sd_th)
    )

print()

# ============================================================================
# SUMMARY
# ============================================================================
print("=" * 100)
print("SUMMARY: BEST RESULTS")
print("=" * 100)

print("\n>> POSITIVE NET RETURN:")
for r in sorted([x for x in results if len(x) > 8 and x[8] > 0], key=lambda x: x[8], reverse=True)[:10]:
    print(f"  {r[0]} {r[1]} R:R={r[2]}: Net={r[8]:+.1f}%, WR={r[5]:.0f}%, Trades={r[4]}")

print("\n>> WIN RATE > 40%:")
for r in sorted([x for x in results if len(x) > 5 and x[5] > 40], key=lambda x: x[5], reverse=True)[:10]:
    print(f"  {r[0]} {r[1]} R:R={r[2]}: WR={r[5]:.0f}%, Net={r[8]:+.1f}%")

print("\n>> >1000 TRADES/YEAR (approx 200+ in 2024):")
for r in sorted([x for x in results if len(x) > 4 and x[4] > 200], key=lambda x: x[4], reverse=True)[:10]:
    print(f"  {r[0]} {r[1]} R:R={r[2]}: Trades={r[4]}, WR={r[5]:.0f}%, Net={r[8]:+.1f}%")

print("\n>> TARGET: >1000 trades, >45% WR, positive return:")
for r in sorted(
    [x for x in results if len(x) > 8 and x[4] > 200 and x[5] > 45 and x[8] > 0],
    key=lambda x: x[8],
    reverse=True,
):
    print(f"  {r[0]} {r[1]} R:R={r[2]}: Trades={r[4]}, WR={r[5]:.0f}%, Net={r[8]:+.1f}%")

print("\nDone!")
