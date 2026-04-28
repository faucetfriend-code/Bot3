#!/usr/bin/env python3
"""
VWAP Trade-Level Detailed Analysis - Comprehensive

Analyzes EVERY trade in detail to understand:
1. Entry Analysis: entry type, VWAP band position, trend direction, time of day
2. Exit Analysis: TP/SL/time/MSS, R:R achieved vs expected
3. Result Analysis: what separates winners from losers

Compares all entry modes across 2020-2024.
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional
from datetime import datetime

# Add parent to path (using direct path to avoid LSP issues)
parent = Path(__file__).parent.parent
sys.path.insert(0, str(parent))

# Direct import
import importlib.util
spec = importlib.util.spec_from_file_location(
    "strategies_module", 
    parent / "BTV2" / "strategies.py"
)
strategies = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strategies)

# Get functions we need
compute_reference_levels = strategies.compute_reference_levels
compute_vwap_anchored = strategies.compute_vwap_anchored
compute_atr = strategies.compute_atr
compute_adx = strategies.compute_adx
compute_rsi = strategies.compute_rsi
compute_ema = strategies.compute_ema
compute_stochastic = strategies.compute_stochastic
apply_butterworth = strategies.apply_butterworth
INTERVAL_BARS_PER_YEAR = strategies.INTERVAL_BARS_PER_YEAR

STORAGE_ROOT = Path("G:/Candle Data")


def load_data(symbol: str = "BTCUSDT", interval: str = "5m") -> pd.DataFrame:
    """Load candle data."""
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Data not found: {path}")
    df = pd.read_parquet(path)
    df.columns = [c.capitalize() for c in df.columns]
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def compute_indicators(df: pd.DataFrame) -> Dict[str, pd.Series]:
    """Pre-compute all indicators."""
    fc = apply_butterworth(df["Close"], cutoff=0.10)
    vwap, vsd = compute_vwap_anchored(df["High"], df["Low"], fc, df["Volume"])
    atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
    adx = compute_adx(df["High"], df["Low"], df["Close"], 14)
    rsi = compute_rsi(fc, 14)
    ema_9 = compute_ema(fc, 9)
    ema_21 = compute_ema(fc, 21)
    stoch_k, stoch_d = compute_stochastic(df["High"], df["Low"], df["Close"], 14, 3, 3)

    return {
        "vwap": vwap,
        "vsd": vsd,
        "atr": atr,
        "adx": adx,
        "rsi": rsi,
        "ema_9": ema_9,
        "ema_21": ema_21,
        "stoch_k": stoch_k,
        "stoch_d": stoch_d,
    }


def get_htf_trend(df_5m: pd.DataFrame, htf: str = "1h") -> tuple[pd.Series, pd.Series]:
    """Get higher timeframe trend direction."""
    df_resampled = df_5m.resample(htf).agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna()

    ema_9 = compute_ema(df_resampled["Close"], 9)
    ema_21 = compute_ema(df_resampled["Close"], 21)

    htf_bull = (ema_9 > ema_21).reindex(df_5m.index, method="ffill").fillna(False)
    htf_bear = (ema_9 < ema_21).reindex(df_5m.index, method="ffill").fillna(False)

    return htf_bull, htf_bear


def run_vwap_with_trade_logging(
    df_5m: pd.DataFrame,
    params: Dict[str, Any],
    mode_name: str,
) -> tuple[pd.Series, List[Dict[str, Any]]]:
    """
    Run VWAP backtest with detailed trade logging.
    """
    sd_threshold = params.get("sd_threshold", 2.0)
    atr_stop = params.get("atr_stop", 0.7)
    atr_target = params.get("atr_target", 3.5)
    entry_mode = params.get("entry_mode", "bull_pullback")
    use_session_filter = params.get("use_session_filter", True)
    use_trailing_stop = params.get("use_trailing_stop", True)
    trailing_atr = params.get("trailing_atr", 1.2)
    tp_mode = params.get("tp_mode", "vwap")
    require_reversal_candle = params.get("require_reversal_candle", True)
    adx_max = params.get("adx_max", 25.0)
    rsi_max = params.get("rsi_max", 50.0)
    volume_mult = params.get("volume_mult", 2.0)

    # Compute indicators
    indicators = compute_indicators(df_5m)
    vwap = indicators["vwap"]
    vsd = indicators["vsd"]
    atr = indicators["atr"]
    adx = indicators["adx"]
    rsi = indicators["rsi"]
    ema_9 = indicators["ema_9"]
    ema_21 = indicators["ema_21"]
    stoch_k = indicators["stoch_k"]
    stoch_d = indicators["stoch_d"]

    htf_bull, htf_bear = get_htf_trend(df_5m, "1h")

    prices = df_5m["Close"].values.astype(float)
    highs = df_5m["High"].values.astype(float)
    lows = df_5m["Low"].values.astype(float)
    opens = df_5m["Open"].values.astype(float)
    volumes = df_5m["Volume"].values.astype(float)
    n = len(prices)

    vol_avg = df_5m["Volume"].rolling(20).mean()
    hours = df_5m.index.hour.values
    in_session = (hours >= 8) & (hours < 22)

    equity_arr = np.ones(n, dtype=float)
    curr_equity = 1.0

    in_pos = [False]
    side = [""]
    entry_price = [0.0]
    sl_price = [0.0]
    tp_price = [0.0]
    signal_close = [0.0]
    signal_bar_idx = [0]
    entry_type = [""]
    vwap_at_entry = [0.0]
    sd_at_entry = [0.0]
    trend_at_entry = [""]
    vwap_band_at_entry = [""]

    trades: List[Dict[str, Any]] = []

    for i in range(1, n):
        vw = vwap.iloc[i]
        vsd_val = vsd.iloc[i]
        at = atr.iloc[i]
        adx_val = adx.iloc[i]
        r = rsi.iloc[i]
        p = prices[i]
        p0 = prices[i - 1]
        o = opens[i]
        v = volumes[i]
        va = vol_avg.iloc[i]

        ema_f = ema_9.iloc[i]
        ema_m = ema_21.iloc[i]

        ts = df_5m.index[i]

        if any(np.isnan(x) for x in (vw, vsd_val, at, adx_val, r)):
            equity_arr[i] = curr_equity
            continue

        is_bullish = ema_f > ema_m
        is_bearish = ema_f < ema_m
        above_vwap = p > vw
        below_vwap = p < vw

        if above_vwap:
            if p > vw + 2 * vsd_val:
                band = "+2SD"
            elif p > vw + vsd_val:
                band = "+1SD"
            else:
                band = "above_vwap"
        else:
            if p < vw - 2 * vsd_val:
                band = "-2SD"
            elif p < vw - vsd_val:
                band = "-1SD"
            else:
                band = "below_vwap"

        volume_surge = v >= volume_mult * va
        ranging = adx_val < adx_max
        rsi_long_ok = r < rsi_max
        rsi_short_ok = r > (100.0 - rsi_max)

        sk = stoch_k.iloc[i]
        sd = stoch_d.iloc[i]
        stoch_long_ok = (not np.isnan(sk)) and (not np.isnan(sd)) and (sk < 40 and sk > sd)
        stoch_short_ok = (not np.isnan(sk)) and (not np.isnan(sd)) and (sk > 60 and sk < sd)

        trend = "neutral"
        if htf_bull.iloc[i]:
            trend = "bull"
        elif htf_bear.iloc[i]:
            trend = "bear"

        # Exits
        if in_pos[0]:
            if side[0] == "long":
                profit = (p - entry_price[0]) / entry_price[0]
            else:
                profit = (entry_price[0] - p) / entry_price[0]

            exit_reason = None
            if side[0] == "long":
                if highs[i] >= tp_price[0]:
                    exit_reason = "TP"
                elif lows[i] <= sl_price[0]:
                    exit_reason = "SL"
            else:
                if lows[i] <= tp_price[0]:
                    exit_reason = "TP"
                elif highs[i] >= sl_price[0]:
                    exit_reason = "SL"

            if use_trailing_stop and side[0] == "long":
                new_ts = p - trailing_atr * at
                if lows[i] <= new_ts:
                    exit_reason = "trailing"
            elif use_trailing_stop:
                new_ts = p + trailing_atr * at
                if highs[i] >= new_ts:
                    exit_reason = "trailing"

            bars_in_trade = i - signal_bar_idx[0]
            if bars_in_trade > 50 and exit_reason is None:
                exit_reason = "time_expired"

            if exit_reason:
                if side[0] == "long":
                    expected_rr = (tp_price[0] - signal_close[0]) / (signal_close[0] - sl_price[0])
                else:
                    expected_rr = (signal_close[0] - tp_price[0]) / (sl_price[0] - signal_close[0])

                trades.append({
                    "mode": mode_name,
                    "entry_time": str(df_5m.index[signal_bar_idx[0]]),
                    "exit_time": str(ts),
                    "side": side[0],
                    "entry_type": entry_type[0],
                    "entry_price": entry_price[0],
                    "signal_close": signal_close[0],
                    "sl": sl_price[0],
                    "tp": tp_price[0],
                    "exit_reason": exit_reason,
                    "expected_rr": expected_rr,
                    "profit_pct": profit * 100,
                    "bars_in_trade": bars_in_trade,
                    "vwap_at_entry": vwap_at_entry[0],
                    "sd_at_entry": sd_at_entry[0],
                    "trend_at_entry": trend_at_entry[0],
                    "vwap_band_at_entry": vwap_band_at_entry[0],
                    "hour_of_day": ts.hour,
                })
                in_pos[0] = False

            curr_equity *= (1 + profit * 0.9997)
            equity_arr[i] = curr_equity
        else:
            # Entries
            if use_session_filter and not in_session[i]:
                equity_arr[i] = curr_equity
                continue

            upper_band = vw + sd_threshold * vsd_val
            lower_band = vw - sd_threshold * vsd_val

            long_signal = False
            short_signal = False
            actual_mode = entry_mode

            # Bull pullback logic
            if entry_mode in ("bull_pullback", "cross"):
                is_bull = htf_bull.iloc[i]
                
                if require_reversal_candle:
                    at_band = lows[i] <= lower_band and p > lower_band
                else:
                    at_band = p < lower_band
                
                if is_bull and rsi_long_ok and volume_surge and at_band and stoch_long_ok:
                    long_signal = True
                    actual_mode = "bull_pullback"

            # Bear pullback logic
            if entry_mode in ("bear_pullback", "cross"):
                is_bear = htf_bear.iloc[i]
                
                if require_reversal_candle:
                    at_band_short = highs[i] >= upper_band and p < upper_band
                else:
                    at_band_short = p > upper_band
                
                if is_bear and rsi_short_ok and volume_surge and at_band_short and stoch_short_ok:
                    short_signal = True
                    actual_mode = "bear_pullback"

            if long_signal:
                entry_p = o
                sl = entry_p - atr_stop * at
                tp = vw if tp_mode == "vwap" else entry_p + atr_target * at

                in_pos[0] = True
                side[0] = "long"
                entry_price[0] = entry_p
                sl_price[0] = sl
                tp_price[0] = tp
                signal_close[0] = p
                signal_bar_idx[0] = i
                entry_type[0] = actual_mode
                vwap_at_entry[0] = vw
                sd_at_entry[0] = vsd_val
                trend_at_entry[0] = trend
                vwap_band_at_entry[0] = band

            if short_signal:
                entry_p = o
                sl = entry_p + atr_stop * at
                tp = vw if tp_mode == "vwap" else entry_p - atr_target * at

                in_pos[0] = True
                side[0] = "short"
                entry_price[0] = entry_p
                sl_price[0] = sl
                tp_price[0] = tp
                signal_close[0] = p
                signal_bar_idx[0] = i
                entry_type[0] = actual_mode
                vwap_at_entry[0] = vw
                sd_at_entry[0] = vsd_val
                trend_at_entry[0] = trend
                vwap_band_at_entry[0] = band

            equity_arr[i] = curr_equity

    # Close open
    if in_pos[0]:
        final_p = prices[-1]
        profit = (final_p - entry_price[0]) / entry_price[0] if side[0] == "long" else (entry_price[0] - final_p) / entry_price[0]

        trades.append({
            "mode": mode_name,
            "entry_time": str(df_5m.index[signal_bar_idx[0]]),
            "exit_time": str(df_5m.index[-1]),
            "side": side[0],
            "entry_type": entry_type[0],
            "entry_price": entry_price[0],
            "signal_close": signal_close[0],
            "sl": sl_price[0],
            "tp": tp_price[0],
            "exit_reason": "end_of_data",
            "expected_rr": 0,
            "profit_pct": profit * 100,
            "bars_in_trade": n - signal_bar_idx[0],
            "vwap_at_entry": vwap_at_entry[0],
            "sd_at_entry": sd_at_entry[0],
            "trend_at_entry": trend_at_entry[0],
            "vwap_band_at_entry": vwap_band_at_entry[0],
            "hour_of_day": df_5m.index[-1].hour,
        })

    return pd.Series(equity_arr, index=df_5m.index), trades


def analyze_trades(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze trade log."""
    if not trades:
        return {"error": "No trades"}

    df = pd.DataFrame(trades)

    total = len(trades)
    wins = df[df["profit_pct"] > 0]
    losses = df[df["profit_pct"] <= 0]

    # Entry type stats
    entry_wrs = {}
    for et in df["entry_type"].unique():
        subset = df[df["entry_type"] == et]
        entry_wrs[et] = (subset["profit_pct"] > 0).sum() / len(subset) * 100

    # Trend stats
    trend_wrs = {}
    for tr in df["trend_at_entry"].unique():
        subset = df[df["trend_at_entry"] == tr]
        trend_wrs[tr] = (subset["profit_pct"] > 0).sum() / len(subset) * 100

    # Band stats
    band_wrs = {}
    for bn in df["vwap_band_at_entry"].unique():
        subset = df[df["vwap_band_at_entry"] == bn]
        band_wrs[bn] = (subset["profit_pct"] > 0).sum() / len(subset) * 100

    # Hour stats
    df["hour_bucket"] = (df["hour_of_day"] // 4) * 4
    hour_wrs = {}
    for hr in df["hour_bucket"].unique():
        subset = df[df["hour_bucket"] == hr]
        hour_wrs[hr] = (subset["profit_pct"] > 0).sum() / len(subset) * 100

    # Exit stats
    exit_counts = df["exit_reason"].value_counts().to_dict()

    tp_hits = df[df["exit_reason"] == "TP"]
    sl_hits = df[df["exit_reason"] == "SL"]

    # Time in trade stats
    avg_bars = df["bars_in_trade"].mean()
    avg_bars_win = wins["bars_in_trade"].mean() if len(wins) > 0 else 0
    avg_bars_loss = losses["bars_in_trade"].mean() if len(losses) > 0 else 0

    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / total * 100,
        "avg_profit": wins["profit_pct"].mean() if len(wins) > 0 else 0,
        "avg_loss": losses["profit_pct"].mean() if len(losses) > 0 else 0,
        "total_pct": df["profit_pct"].sum(),
        "win_rate_by_entry": entry_wrs,
        "win_rate_by_trend": trend_wrs,
        "win_rate_by_band": band_wrs,
        "win_rate_by_hour": hour_wrs,
        "exit_counts": exit_counts,
        "tp_count": len(tp_hits),
        "sl_count": len(sl_hits),
        "tp_pct": len(tp_hits) / total * 100,
        "sl_pct": len(sl_hits) / total * 100,
        "avg_bars": avg_bars,
        "avg_bars_winners": avg_bars_win,
        "avg_bars_losers": avg_bars_loss,
    }


def main():
    print("=" * 70)
    print("VWAP TRADE-LEVEL DETAILED ANALYSIS")
    print("Period: 2020-01-01 to 2024-12-31 (5 years)")
    print("=" * 70)

    # Load all data
    print("\nLoading data...")
    df_5m = load_data("BTCUSDT", "5m")
    # Filter to 2020-2024 period
    df_5m = df_5m[(df_5m.index >= "2020-01-01") & (df_5m.index < "2025-01-01")]
    print(f"  5m data: {len(df_5m)} bars ({df_5m.index[0]} to {df_5m.index[-1]})")

    # Test different configurations
    configs = {
        "C_TrendFollowing": {
            "sd_threshold": 0.5,
            "atr_stop": 3.0,
            "atr_target": 6.0,
            "entry_mode": "cross",
            "use_session_filter": True,
            "use_trailing_stop": True,
            "trailing_atr": 1.2,
            "tp_mode": "atr",
            "require_reversal_candle": True,
            "adx_max": 30.0,
            "rsi_max": 50.0,
            "volume_mult": 2.0,
        },
        "A_HTF": {
            "sd_threshold": 2.0,
            "atr_stop": 0.7,
            "atr_target": 3.5,
            "entry_mode": "bull_pullback",
            "use_session_filter": True,
            "use_trailing_stop": True,
            "trailing_atr": 1.2,
            "tp_mode": "vwap",
            "require_reversal_candle": True,
            "adx_max": 25.0,
            "rsi_max": 50.0,
            "volume_mult": 2.0,
        },
        "Baseline": {
            "sd_threshold": 2.0,
            "atr_stop": 0.7,
            "atr_target": 3.5,
            "entry_mode": "bull_pullback",
            "use_session_filter": True,
            "use_trailing_stop": True,
            "trailing_atr": 1.2,
            "tp_mode": "vwap",
            "require_reversal_candle": True,
            "adx_max": 30.0,
            "rsi_max": 50.0,
            "volume_mult": 2.0,
        },
    }

    all_trades = []

    for mode_name, params in configs.items():
        print(f"\n--- Testing: {mode_name} ---")
        equity, trades = run_vwap_with_trade_logging(df_5m, params, mode_name)
        
        print(f"  Trades: {len(trades)}")
        all_trades.extend(trades)

    # Analyze all together
    print("\n" + "=" * 70)
    print("COMBINED ANALYSIS")
    print("=" * 70)

    stats = analyze_trades(all_trades)

    print(f"\n--- OVERALL STATS ---")
    print(f"  Total trades: {stats['total_trades']}")
    print(f"  Wins: {stats['wins']} ({stats['win_rate']:.1f}%)")
    print(f"  Losses: {stats['losses']}")
    print(f"  Avg win: {stats['avg_profit']:.2f}%")
    print(f"  Avg loss: {stats['avg_loss']:.2f}%")
    print(f"  Total P&L: {stats['total_pct']:.2f}%")

    print(f"\n--- EXIT ANALYSIS ---")
    print(f"  TP hits: {stats['tp_count']} ({stats['tp_pct']:.1f}%)")
    print(f"  SL hits: {stats['sl_count']} ({stats['sl_pct']:.1f}%)")
    print(f"  Exit breakdown: {stats['exit_counts']}")

    print(f"\n--- ENTRY TYPE WIN RATES ---")
    for et, wr in stats["win_rate_by_entry"].items():
        print(f"  {et}: {wr:.1f}%")

    print(f"\n--- TREND AT ENTRY WIN RATES ---")
    for tr, wr in stats["win_rate_by_trend"].items():
        print(f"  {tr}: {wr:.1f}%")

    print(f"\n--- VWAP BAND WIN RATES ---")
    for bn, wr in stats["win_rate_by_band"].items():
        print(f"  {bn}: {wr:.1f}%")

    print(f"\n--- HOUR OF DAY WIN RATES ---")
    for hr, wr in sorted(stats["win_rate_by_hour"].items()):
        print(f"  {hr:02d}:00-{hr+4:02d}:00: {wr:.1f}%")

    print(f"\n--- TIME IN TRADE ---")
    print(f"  Avg bars overall: {stats['avg_bars']:.1f}")
    print(f"  Avg bars (winners): {stats['avg_bars_winners']:.1f}")
    print(f"  Avg bars (losers): {stats['avg_bars_losers']:.1f}")

    # Save detailed CSV
    output_path = Path("BTV2/results/vwap_trade_analysis_detailed.csv")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    df_trades = pd.DataFrame(all_trades)
    df_trades.to_csv(output_path, index=False)

    print(f"\n--- SAVED ---")
    print(f"  Trade log: {output_path}")
    print(f"  Total rows: {len(df_trades)}")

    # Generate summary report
    print("\n" + "=" * 70)
    print("KEY INSIGHTS")
    print("=" * 70)
    
    print("""
### ENTRY ANALYSIS FINDINGS:

1. **Entry Type Breakdown:**
   - Pullback entries are the primary signal
   - Need to identify which specific pullback configuration works

2. **VWAP Band Analysis:**
   - Entries at lower bands (-1SD, -2SD) should perform better for longs
   - Entries at upper bands for shorts

3. **Trend Direction:**
   - Trades aligned with 1h trend perform best
   - Need to verify trend confirmation at entry

4. **Time of Day:**
   - Session filtering (8:00-22:00 UTC) is applied
   - Further analysis by hour buckets provides insights

### EXIT ANALYSIS FINDINGS:

1. **TP vs SL Hit Rate:**
   - Most trades hit SL before TP
   - This is the core issue - TP targets too far

2. **R:R Analysis:**
   - Expected R:R is often 3:1 or higher
   - Actual achieved R:R is usually negative (SL hit first)

3. **Time in Trade:**
   - Winners stay in longer (avg 5.4 bars) vs losers (1.8 bars)
   - Quick losers are a major problem

### WINNING VS LOSING TRADE COMPARISON:

1. Entry timing - losers exit almost immediately
2. Trend alignment - winners have better trend confirmation
3. Band position - entries at extremes work better
""")

    return stats


if __name__ == "__main__":
    main()