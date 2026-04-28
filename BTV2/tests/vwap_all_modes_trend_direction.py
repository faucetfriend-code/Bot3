#!/usr/bin/env python3
"""
VWAP All Entry Modes + Trend Direction Detection
=================================================
Tests ALL 6 VWAP entry modes individually, then tests dynamic mode selection
using 3 different trend detection methods.

Entry Modes:
  1. bull_pullback  — Buy dips in uptrend
  2. bear_pullback  — Short rallies in downtrend
  3. mean_reversion — Fade VWAP extremes in range
  4. cross          — Trade VWAP crosses
  5. momentum       — Ride strong moves
  6. deviation      — Fade extreme deviations

Trend Detection Methods:
  1. EMA Direction  — 1h EMA(9) vs EMA(21)
  2. Price Position — Price vs 1h VWAP
  3. ADX + EMA      — ADX > 25 + EMA direction

Data: BTCUSDT 5m for 2024, 1m exit resolution
"""
import sys
import time
import math
from pathlib import Path

import numpy as np
import pandas as pd

STORAGE_ROOT = Path("G:/Candle Data")
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    run_vwap_scalping,
    compute_metrics,
    compute_ema,
    compute_vwap_rolling,
    compute_adx,
    compute_rsi,
    _resample_ohlcv,
    INTERVAL_BARS_PER_YEAR,
    COST_PER_SIDE,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_data():
    """Load BTCUSDT 5m data for 2024."""
    print("Loading BTCUSDT 5m data...")
    path = STORAGE_ROOT / "BTCUSDT_5m.parquet"
    df = pd.read_parquet(path)
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
    print(f"Loaded {len(df):,} bars (2024)")
    return df


def load_1m_exit_data():
    """Load BTCUSDT 1m data for 2024 for high-res exits."""
    print("Loading BTCUSDT 1m exit data...")
    path = STORAGE_ROOT / "BTCUSDT_1m.parquet"
    if not path.exists():
        print("  1m data not found, using 5m for exits")
        return None
    df = pd.read_parquet(path)
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
    print(f"  Loaded {len(df):,} 1m bars")
    return df


def calc_stats(trades):
    """Calculate win rate, profit factor, net return from trades list."""
    if not trades:
        return {"trades": 0, "wr": 0.0, "pf": 0.0, "gross": 0.0, "costs": 0.0, "net": 0.0}
    n = len(trades)
    wins = sum(1 for t in trades if t > 0)
    wr = wins / n * 100
    gp = sum(t for t in trades if t > 0)
    gl = abs(sum(t for t in trades if t < 0))
    pf = gp / gl if gl > 0 else float("inf")
    gross = gp - gl  # net P&L as decimal
    costs = n * COST_PER_SIDE * 2  # round-trip cost per trade
    net = gross - costs
    return {"trades": n, "wr": wr, "pf": pf, "gross": gross * 100, "costs": costs * 100, "net": net * 100}


def fmt_row(name, stats):
    """Format a result row."""
    pf_str = f"{stats['pf']:.2f}" if stats['pf'] != float("inf") else "inf"
    return f"{name:<20} {stats['trades']:>7} {stats['wr']:>6.1f} {pf_str:>7} {stats['gross']:>+8.1f}% {stats['net']:>+8.1f}%"


# ─────────────────────────────────────────────────────────────────────────────
# Base parameters (from spec)
# ─────────────────────────────────────────────────────────────────────────────
BASE_PARAMS = dict(
    sd_threshold=2.0,
    adx_max=30.0,
    rsi_max=50.0,
    volume_mult=2.0,
    atr_stop=0.7,
    atr_target=3.5,
    use_session_filter=True,
    cutoff=0.10,
    use_trend_filter=True,
    use_volume_filter=True,
    use_trailing_stop=True,
    trailing_atr=1.2,
    use_anchored_vwap=True,
    tp_mode="vwap",
    require_reversal_candle=True,
    require_mss=False,
    use_fvg_filter=False,
    use_ob_filter=False,
    use_eqhl_filter=False,
    use_cvd_filter=False,
    use_ote=False,
    use_dynamic_mode=False,  # disabled for individual mode tests
)

ENTRY_MODES = ["bull_pullback", "bear_pullback", "mean_reversion", "cross", "momentum", "deviation"]

# ─────────────────────────────────────────────────────────────────────────────
# TEST 1: Each Entry Mode Individually
# ─────────────────────────────────────────────────────────────────────────────

def test_individual_modes(df, df_exit):
    """Test all 6 entry modes with identical base params."""
    print("\n" + "=" * 100)
    print("TEST 1: Individual Entry Modes (same base params)")
    print("=" * 100)

    results = []
    for mode in ENTRY_MODES:
        t0 = time.time()
        params = {**BASE_PARAMS, "entry_mode": mode}
        eq, trades = run_vwap_scalping(df, df_exit=df_exit, **params)
        stats = calc_stats(trades)
        elapsed = time.time() - t0
        print(f"  {fmt_row(mode, stats)}  ({elapsed:.1f}s)")
        results.append({"test": "individual", "mode": mode, **stats})

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Trend Detection Functions
# ─────────────────────────────────────────────────────────────────────────────

def detect_trend_ema_direction(df_1h, idx):
    """
    Method 1: EMA Direction
    1h EMA(9) > EMA(21) → UPTREND
    1h EMA(9) < EMA(21) → DOWNTREND
    Otherwise → RANGING
    """
    ema9 = compute_ema(df_1h["Close"], 9)
    ema21 = compute_ema(df_1h["Close"], 21)
    diff = (ema9 - ema21) / (ema21 + 1e-10) * 100  # percentage difference
    # Reindex to 5m and forward-fill
    diff_5m = diff.reindex(idx, method="ffill")
    return diff_5m


def detect_trend_price_position(df_1h, df_5m):
    """
    Method 2: Price Position vs 1h VWAP
    Price > 1h VWAP → UPTREND
    Price < 1h VWAP → DOWNTREND
    Price near VWAP (within 0.1%) → RANGING
    """
    vwap_1h, _ = compute_vwap_rolling(
        df_1h["High"], df_1h["Low"], df_1h["Close"], df_1h["Volume"], 20
    )
    vwap_1h_ff = vwap_1h.reindex(df_5m.index, method="ffill")
    prices = df_5m["Close"]
    deviation = (prices - vwap_1h_ff) / (vwap_1h_ff + 1e-10) * 100
    return deviation


def detect_trend_adx_ema(df_1h, idx):
    """
    Method 3: ADX + EMA Combined
    ADX > 25 AND EMA bullish → STRONG UPTREND
    ADX > 25 AND EMA bearish → STRONG DOWNTREND
    ADX < 25 → RANGING
    Returns (ema_diff_5m, adx_5m) for combined evaluation.
    """
    ema9 = compute_ema(df_1h["Close"], 9)
    ema21 = compute_ema(df_1h["Close"], 21)
    adx_1h = compute_adx(df_1h["High"], df_1h["Low"], df_1h["Close"], 14)

    ema_diff = (ema9 - ema21) / (ema21 + 1e-10) * 100
    ema_diff_5m = ema_diff.reindex(idx, method="ffill")
    adx_5m = adx_1h.reindex(idx, method="ffill")
    return ema_diff_5m, adx_5m


def classify_trend_ema(ema_diff):
    """Classify trend from EMA direction."""
    if ema_diff > 0.05:
        return "uptrend"
    elif ema_diff < -0.05:
        return "downtrend"
    else:
        return "ranging"


def classify_trend_price(deviation):
    """Classify trend from price position vs 1h VWAP."""
    if deviation > 0.1:
        return "uptrend"
    elif deviation < -0.1:
        return "downtrend"
    else:
        return "ranging"


def classify_trend_adx_ema(ema_diff, adx_val):
    """Classify trend from ADX + EMA combined."""
    if math.isnan(adx_val):
        return "ranging"
    if adx_val > 25:
        if ema_diff > 0.05:
            return "strong_uptrend"
        elif ema_diff < -0.05:
            return "strong_downtrend"
    return "ranging"


def select_entry_mode(trend):
    """Select entry mode based on trend direction."""
    mapping = {
        "uptrend": "bull_pullback",
        "downtrend": "bear_pullback",
        "ranging": "mean_reversion",
        "strong_uptrend": "bull_pullback",
        "strong_downtrend": "bear_pullback",
    }
    return mapping.get(trend, "mean_reversion")


# ─────────────────────────────────────────────────────────────────────────────
# Dynamic Mode Selection Backtest
# ─────────────────────────────────────────────────────────────────────────────

def run_dynamic_mode_backtest(df, df_exit, trend_method):
    """
    Run VWAP backtest with dynamic entry mode selection based on trend.

    trend_method: "ema", "price", or "adx_ema"
    """
    from strategies import (
        apply_butterworth,
        compute_vwap_anchored,
        compute_atr,
        compute_adx,
        compute_rsi,
        compute_ema,
        compute_vwap_rolling,
        compute_stochastic,
        compute_reference_levels,
        _check_exit_multires,
        _open_long,
        _open_short,
    )

    cutoff = BASE_PARAMS["cutoff"]
    sd_threshold = BASE_PARAMS["sd_threshold"]
    atr_stop = BASE_PARAMS["atr_stop"]
    atr_target = BASE_PARAMS["atr_target"]
    volume_mult = BASE_PARAMS["volume_mult"]
    adx_max = BASE_PARAMS["adx_max"]
    rsi_max = BASE_PARAMS["rsi_max"]
    use_session_filter = BASE_PARAMS["use_session_filter"]
    use_volume_filter = BASE_PARAMS["use_volume_filter"]
    use_trailing_stop = BASE_PARAMS["use_trailing_stop"]
    trailing_atr = BASE_PARAMS["trailing_atr"]
    use_anchored_vwap = BASE_PARAMS["use_anchored_vwap"]
    tp_mode = BASE_PARAMS["tp_mode"]
    require_reversal_candle = BASE_PARAMS["require_reversal_candle"]
    deviation_pct = 0.5
    momentum_bars = 2

    fc = apply_butterworth(df["Close"], cutoff)

    if use_anchored_vwap:
        vwap, vs = compute_vwap_anchored(df["High"], df["Low"], fc, df["Volume"])
    else:
        vwap, vs = compute_vwap_rolling(df["High"], df["Low"], fc, df["Volume"], 20)

    atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
    adx = compute_adx(df["High"], df["Low"], df["Close"], 14)
    rsi = compute_rsi(fc, 14)
    ema_f = compute_ema(fc, 9)
    ema_m = compute_ema(fc, 20)
    vol_avg = df["Volume"].rolling(20).mean()
    stoch_k_s, stoch_d_s = compute_stochastic(df["High"], df["Low"], df["Close"], 14, 3, 3)

    # Session filter
    if use_session_filter:
        _hours = np.array(df.index.hour)
        in_session = (_hours >= 8) & (_hours < 22)
    else:
        in_session = np.ones(len(df), dtype=bool)

    # HTF data for trend detection
    df_1h = _resample_ohlcv(df, "60min")

    if trend_method == "ema":
        trend_signal = detect_trend_ema_direction(df_1h, df.index)
    elif trend_method == "price":
        trend_signal = detect_trend_price_position(df_1h, df)
    elif trend_method == "adx_ema":
        trend_signal = detect_trend_adx_ema(df_1h, df.index)
    else:
        raise ValueError(f"Unknown trend_method: {trend_method}")

    # Also compute 1h EMA for bull/bear_pullback mode confirmation
    ema9_1h = compute_ema(df_1h["Close"], 9)
    ema21_1h = compute_ema(df_1h["Close"], 21)
    htf_bull_ff = (ema9_1h > ema21_1h).reindex(df.index, method="ffill").fillna(False)
    htf_bear_ff = (ema9_1h < ema21_1h).reindex(df.index, method="ffill").fillna(False)
    vwap_1h, _ = compute_vwap_rolling(df_1h["High"], df_1h["Low"], df_1h["Close"], df_1h["Volume"], 20)
    vwap_1h_ff = vwap_1h.reindex(df.index, method="ffill")

    prices = df["Close"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    opens = df["Open"].values.astype(float)
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
        v = df["Volume"].iloc[i]
        va = vol_avg.iloc[i]

        if any(np.isnan(x) for x in (vw, vsd, at, adx_v, r)):
            equity_arr[i] = curr_equity
            continue

        # Determine trend and select entry mode
        if trend_method == "ema":
            ema_diff = trend_signal.iloc[i]
            trend = classify_trend_ema(ema_diff)
        elif trend_method == "price":
            deviation = trend_signal.iloc[i]
            trend = classify_trend_price(deviation)
        else:  # adx_ema
            ema_diff, adx_vals = trend_signal
            ema_d = ema_diff.iloc[i]
            adx_v_1h = adx_vals.iloc[i]
            trend = classify_trend_adx_ema(ema_d, adx_v_1h)

        effective_mode = select_entry_mode(trend)

        # Price relative to VWAP
        above_vwap = p > vw
        below_vwap = p < vw
        volume_surge = v >= volume_mult * va if use_volume_filter else True
        ranging = adx_v < adx_max
        rsi_long_ok = r < rsi_max
        rsi_short_ok = r > (100.0 - rsi_max)

        # Stochastic
        sk_i = stoch_k_s.iloc[i]
        sd_i = stoch_d_s.iloc[i]
        stoch_nan = np.isnan(sk_i) or np.isnan(sd_i)
        stoch_long_ok = (not stoch_nan) and bool(sk_i < 40 and sk_i > sd_i)
        stoch_short_ok = (not stoch_nan) and bool(sk_i > 60 and sk_i < sd_i)

        # Check exits
        if in_pos[0]:
            curr_equity *= p / p0
            nxt = df.index[i] + bar_dur
            curr_equity = _check_exit_multires(
                curr_equity, entry_eq[0], highs[i], lows[i],
                sl[0], tp_ref[0], side[0], closed_trades, in_pos,
                bar_ts=df.index[i], next_bar_ts=nxt, df_exit=df_exit,
            )
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
            if not in_session[i]:
                equity_arr[i] = curr_equity
                continue

            long_signal = False
            short_signal = False

            upper_band = vw + sd_threshold * vsd
            lower_band = vw - sd_threshold * vsd
            v1h = vwap_1h_ff.iloc[i] if vwap_1h_ff is not None else float("nan")
            deviation_from_vwap = (p - vw) / (vw + 1e-10) * 100.0 if vw > 0 else 0.0

            if effective_mode == "bull_pullback":
                is_bull = bool(htf_bull_ff.iloc[i])
                v1h_ok = np.isnan(v1h) or (p < v1h)
                if require_reversal_candle:
                    at_band = bool(lows[i] <= lower_band and p > lower_band)
                else:
                    at_band = bool(p < lower_band)
                if is_bull and rsi_long_ok and volume_surge:
                    if at_band and v1h_ok and stoch_long_ok:
                        long_signal = True

            elif effective_mode == "bear_pullback":
                is_bear = bool(htf_bear_ff.iloc[i])
                v1h_ok = np.isnan(v1h) or (p > v1h)
                if require_reversal_candle:
                    at_band = bool(highs[i] >= upper_band and p < upper_band)
                else:
                    at_band = bool(p > upper_band)
                if is_bear and rsi_short_ok and volume_surge:
                    if at_band and v1h_ok and stoch_short_ok:
                        short_signal = True

            elif effective_mode == "mean_reversion":
                if ranging and rsi_long_ok:
                    if require_reversal_candle:
                        mr_long_ok = bool(lows[i] <= lower_band and p > lower_band)
                    else:
                        mr_long_ok = bool(p < lower_band)
                    if mr_long_ok and volume_surge and stoch_long_ok:
                        long_signal = True
                if ranging and rsi_short_ok:
                    if require_reversal_candle:
                        mr_short_ok = bool(highs[i] >= upper_band and p < upper_band)
                    else:
                        mr_short_ok = bool(p > upper_band)
                    if mr_short_ok and volume_surge and stoch_short_ok:
                        short_signal = True

            elif effective_mode == "cross":
                if ranging and rsi_long_ok and volume_surge:
                    if require_reversal_candle:
                        cross_long_ok = bool(lows[i] <= vw and p > vw)
                    else:
                        cross_long_ok = bool(above_vwap and i >= 1 and prices[i - 1] < vwap.iloc[i - 1])
                    if cross_long_ok:
                        long_signal = True
                if ranging and rsi_short_ok and volume_surge:
                    if require_reversal_candle:
                        cross_short_ok = bool(highs[i] >= vw and p < vw)
                    else:
                        cross_short_ok = bool(below_vwap and i >= 1 and prices[i - 1] > vwap.iloc[i - 1])
                    if cross_short_ok:
                        short_signal = True

            elif effective_mode == "momentum":
                if ranging and rsi_long_ok:
                    if above_vwap and volume_surge:
                        momentum_ok = True
                        for j in range(1, min(momentum_bars + 1, i + 1)):
                            if prices[i - j] <= prices[i - j - 1]:
                                momentum_ok = False
                                break
                        if momentum_ok and i >= momentum_bars + 1:
                            long_signal = True
                if ranging and rsi_short_ok:
                    if below_vwap and volume_surge:
                        momentum_ok = True
                        for j in range(1, min(momentum_bars + 1, i + 1)):
                            if prices[i - j] >= prices[i - j - 1]:
                                momentum_ok = False
                                break
                        if momentum_ok and i >= momentum_bars + 1:
                            short_signal = True

            elif effective_mode == "deviation":
                dev_long_threshold = vw * (1.0 - deviation_pct / 100.0)
                dev_short_threshold = vw * (1.0 + deviation_pct / 100.0)
                if ranging and rsi_long_ok and volume_surge:
                    if require_reversal_candle:
                        dev_long_ok = bool(lows[i] <= dev_long_threshold and p > dev_long_threshold)
                    else:
                        dev_long_ok = bool(deviation_from_vwap < -deviation_pct)
                    if dev_long_ok:
                        long_signal = True
                if ranging and rsi_short_ok and volume_surge:
                    if require_reversal_candle:
                        dev_short_ok = bool(highs[i] >= dev_short_threshold and p < dev_short_threshold)
                    else:
                        dev_short_ok = bool(deviation_from_vwap > deviation_pct)
                    if dev_short_ok:
                        short_signal = True

            # N+1 entry
            if long_signal and i + 1 < n:
                signal_close = p
                entry_p = opens[i + 1]
                if tp_mode == "vwap":
                    _tp_l = vw
                else:
                    _tp_l = signal_close + atr_target * at
                curr_equity = _open_long(
                    curr_equity, entry_eq, sl, tp_ref, entry_p,
                    signal_close - atr_stop * at, _tp_l, in_pos, side,
                )
                if in_pos[0]:
                    entry_price[0] = entry_p
                    trailing_stop[0] = signal_close - trailing_atr * at

            elif short_signal and i + 1 < n:
                signal_close = p
                entry_p = opens[i + 1]
                if tp_mode == "vwap":
                    _tp_s = vw
                else:
                    _tp_s = signal_close - atr_target * at
                curr_equity = _open_short(
                    curr_equity, entry_eq, sl, tp_ref, entry_p,
                    signal_close + atr_stop * at, _tp_s, in_pos, side,
                )
                if in_pos[0]:
                    entry_price[0] = entry_p
                    trailing_stop[0] = signal_close + trailing_atr * at

        equity_arr[i] = curr_equity

    if in_pos[0]:
        curr_equity *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
        equity_arr[-1] = curr_equity

    return pd.Series(equity_arr, index=df.index), closed_trades


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2 & 3: Dynamic Mode Selection with all trend detection methods
# ─────────────────────────────────────────────────────────────────────────────

def test_dynamic_modes(df, df_exit):
    """Test dynamic mode selection with each trend detection method."""
    print("\n" + "=" * 100)
    print("TEST 2+3: Dynamic Mode Selection (trend-based entry mode switching)")
    print("=" * 100)

    trend_methods = ["ema", "price", "adx_ema"]
    results = []

    for method in trend_methods:
        t0 = time.time()
        eq, trades = run_dynamic_mode_backtest(df, df_exit, trend_method=method)
        stats = calc_stats(trades)
        elapsed = time.time() - t0
        method_name = {"ema": "EMA Direction", "price": "Price Position", "adx_ema": "ADX + EMA"}[method]
        print(f"  {fmt_row(method_name, stats)}  ({elapsed:.1f}s)")
        results.append({"test": "dynamic", "trend_method": method_name, **stats})

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    df = load_data()
    df_exit = load_1m_exit_data()

    bars_per_year = INTERVAL_BARS_PER_YEAR["5m"]

    all_results = []

    # TEST 1: Individual modes
    individual_results = test_individual_modes(df, df_exit)
    all_results.extend(individual_results)

    # TEST 2+3: Dynamic mode selection
    dynamic_results = test_dynamic_modes(df, df_exit)
    all_results.extend(dynamic_results)

    # ── Save CSV ──
    results_dir = Path(__file__).parent.parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    csv_path = results_dir / "vwap_all_modes_trend_direction.csv"

    rows = []
    for r in all_results:
        rows.append({
            "test_type": r["test"],
            "mode_or_method": r.get("mode", r.get("trend_method", "")),
            "trades": r["trades"],
            "win_rate_pct": round(r["wr"], 2),
            "profit_factor": round(r["pf"], 4) if r["pf"] != float("inf") else 999.99,
            "gross_pct": round(r["gross"], 2),
            "costs_pct": round(r["costs"], 2),
            "net_pct": round(r["net"], 2),
        })

    df_results = pd.DataFrame(rows)
    df_results.to_csv(csv_path, index=False)
    print(f"\nResults saved to: {csv_path}")

    # ── Save Markdown Report ──
    report_path = results_dir / "vwap_all_modes_trend_report.md"

    # Find best individual mode
    best_individual = max(individual_results, key=lambda x: x["net"])
    best_dynamic = max(dynamic_results, key=lambda x: x["net"])

    report = f"""# VWAP All Entry Modes + Trend Direction Detection Report

## Test Configuration
- **Symbol**: BTCUSDT
- **Timeframe**: 5m (2024 full year)
- **Exit Resolution**: 1m (if available, else 5m)
- **Base Parameters**:
  - `sd_threshold`: 2.0
  - `adx_max`: 30.0
  - `rsi_max`: 50.0
  - `volume_mult`: 2.0
  - `atr_stop`: 0.7
  - `atr_target`: 3.5
  - `use_session_filter`: True
  - `tp_mode`: vwap
  - `require_reversal_candle`: True

---

## Test 1: Individual Entry Modes

| Mode | Trades | WR% | PF | Gross% | Net% |
|------|--------|-----|-----|--------|------|
"""

    for r in individual_results:
        pf_str = f"{r['pf']:.2f}" if r['pf'] != float("inf") else "inf"
        report += f"| {r['mode']} | {r['trades']} | {r['wr']:.1f} | {pf_str} | {r['gross']:+.1f} | {r['net']:+.1f} |\n"

    report += f"""
**Best Individual Mode**: {best_individual['mode']} (Net: {best_individual['net']:+.1f}%, WR: {best_individual['wr']:.1f}%, Trades: {best_individual['trades']})

---

## Test 2+3: Dynamic Mode Selection (Trend-Based)

### Trend Detection Methods

| Trend Detection | Trades | WR% | PF | Gross% | Net% |
|-----------------|--------|-----|-----|--------|------|
"""

    for r in dynamic_results:
        pf_str = f"{r['pf']:.2f}" if r['pf'] != float("inf") else "inf"
        report += f"| {r['trend_method']} | {r['trades']} | {r['wr']:.1f} | {pf_str} | {r['gross']:+.1f} | {r['net']:+.1f} |\n"

    report += f"""
**Best Dynamic Method**: {best_dynamic['trend_method']} (Net: {best_dynamic['net']:+.1f}%, WR: {best_dynamic['wr']:.1f}%, Trades: {best_dynamic['trades']})

---

## Comparison: Static vs Dynamic

| Approach | Best Mode/Method | Trades | WR% | PF | Net% |
|----------|-----------------|--------|-----|-----|------|
| Static (Individual) | {best_individual['mode']} | {best_individual['trades']} | {best_individual['wr']:.1f} | {best_individual['pf']:.2f} | {best_individual['net']:+.1f} |
| Dynamic (Trend-Based) | {best_dynamic['trend_method']} | {best_dynamic['trades']} | {best_dynamic['wr']:.1f} | {best_dynamic['pf']:.2f} | {best_dynamic['net']:+.1f} |

### Verdict
"""

    if best_dynamic["net"] > best_individual["net"]:
        improvement = best_dynamic["net"] - best_individual["net"]
        report += f"**Dynamic mode selection IMPROVES results by {improvement:+.1f}% net return** over the best static mode.\n"
    else:
        degradation = best_individual["net"] - best_dynamic["net"]
        report += f"**Dynamic mode selection UNDERPERFORMS by {degradation:.1f}% net return** vs the best static mode.\n"

    report += f"""
---

## Entry Mode Selection Logic

| Trend Direction | Entry Mode | Rationale |
|-----------------|------------|-----------|
| UPTREND | bull_pullback | Buy dips in uptrend |
| DOWNTREND | bear_pullback | Short rallies in downtrend |
| RANGING | mean_reversion | Fade to VWAP in range |
| BREAKOUT | cross | Trade VWAP crosses |
| MOMENTUM | momentum | Ride strong moves |
| EXTREME | deviation | Fade extreme deviations |

## Trend Detection Methods

1. **EMA Direction**: 1h EMA(9) vs EMA(21) — simple trend filter
2. **Price Position**: Price vs 1h VWAP — mean-reversion context
3. **ADX + EMA**: ADX > 25 + EMA direction — strength + direction combined

---
*Generated: {pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")}*
"""

    report_path.write_text(report)
    print(f"Report saved to: {report_path}")

    # ── Print Summary ──
    print("\n" + "=" * 100)
    print("FINAL SUMMARY")
    print("=" * 100)
    print(f"\n{'Mode/Method':<25} {'Trades':>7} {'WR%':>6} {'PF':>7} {'Gross%':>9} {'Net%':>9}")
    print("-" * 75)
    for r in all_results:
        name = r.get("mode", r.get("trend_method", ""))
        pf_str = f"{r['pf']:.2f}" if r['pf'] != float("inf") else "inf"
        print(f"{name:<25} {r['trades']:>7} {r['wr']:>5.1f} {pf_str:>7} {r['gross']:>+8.1f}% {r['net']:>+8.1f}%")

    print(f"\n>> Best Static: {best_individual['mode']} -> Net: {best_individual['net']:+.1f}%")
    print(f">> Best Dynamic: {best_dynamic['trend_method']} -> Net: {best_dynamic['net']:+.1f}%")

    if best_dynamic["net"] > best_individual["net"]:
        print(f"\n>>> DYNAMIC MODE SELECTION WINS by {best_dynamic['net'] - best_individual['net']:+.1f}%")
    else:
        print(f"\n>>> STATIC MODE SELECTION WINS by {best_individual['net'] - best_dynamic['net']:+.1f}%")

    print("\nDone!")


if __name__ == "__main__":
    main()
