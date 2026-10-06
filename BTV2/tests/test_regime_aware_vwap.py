#!/usr/bin/env python3
"""
Regime-Aware VWAP Scalping Strategy Test (Final Version)
==========================================================
Tests different regime detection methods and entry modes for VWAP scalping.
Uses working entry logic patterns from test_vwap_entry_modes.py

Regime Detection Methods:
1. ADX-based: Ranging (ADX < 25), Trending (ADX > 25)
2. EMA-based: 1h EMA(9) > EMA(21) = Uptrend, < = Downtrend
3. Reference level-based: Price near prior day highs/lows
4. Combined: ADX + EMA + reference levels

Entry Modes per Regime:
- Uptrend: cross - Trend continuation entries
- Downtrend: cross - Trend continuation entries
- Ranging: deviation - Mean reversion entries
"""

import sys
sys.path.insert(0, "G:/ai-workspace/Bot3/BTV2")

import pandas as pd
import numpy as np
from typing import List, Dict, Tuple, Optional
from pathlib import Path

STORAGE_ROOT = Path("G:/Candle Data")

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


# Regime parameter configurations - matching successful patterns from test_vwap_entry_modes.py
REGIME_PARAMS = {
    "uptrend": {"sd_threshold": 2.0, "atr_stop": 1.0, "atr_target": 2.0, "entry_mode": "cross", "deviation_pct": 0.5},
    "downtrend": {"sd_threshold": 2.0, "atr_stop": 1.0, "atr_target": 2.0, "entry_mode": "cross", "deviation_pct": 0.5},
    "ranging": {"sd_threshold": 3.0, "atr_stop": 1.0, "atr_target": 2.0, "entry_mode": "deviation", "deviation_pct": 1.0},
}


def load_parquet(symbol: str, interval: str) -> Optional[pd.DataFrame]:
    """Load parquet data file."""
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return None


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample OHLCV to a different timeframe."""
    return (
        df.resample(rule, closed="left", label="left")
        .agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum"
        })
        .dropna(subset=["Close"])
    )


def run_vwap_backtest(
    df: pd.DataFrame,
    regime_method: str = "none",
    use_optimized_params: bool = False,
    sd_threshold: float = 2.5,
    atr_stop: float = 1.0,
    atr_target: float = 2.0,
    cutoff: float = 0.10,
    use_trend_filter: bool = True,
    use_volume_filter: bool = True,
    volume_mult: float = 2.0,
    use_trailing_stop: bool = True,
    trailing_atr: float = 1.0,
    adx_max: float = 22.0,
    rsi_max: float = 45.0,
    entry_mode: str = "deviation",
    deviation_pct: float = 0.5,
) -> Tuple[pd.Series, list]:
    """Run VWAP scalping backtest with regime-aware entry mode switching."""
    
    # Precompute indicators
    fc = apply_butterworth(df["Close"], cutoff)
    vwap, vsd = compute_vwap_rolling(df["High"], df["Low"], fc, df["Volume"], 20)
    atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
    adx = compute_adx(df["High"], df["Low"], df["Close"], 14)
    rsi = compute_rsi(fc, 14)
    vol_avg = df["Volume"].rolling(20).mean()

    # Precompute regime indicators
    df_1h = resample_ohlcv(df, "1h")
    fc_1h = apply_butterworth(df_1h["Close"], cutoff)
    ema_f_1h = compute_ema(fc_1h, 9)
    ema_s_1h = compute_ema(fc_1h, 21)
    
    df_daily = resample_ohlcv(df, "1D")
    pdh = df_daily["High"].shift(1)
    pdl = df_daily["Low"].shift(1)

    prices = df["Close"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    opens = df["Open"].values.astype(float)
    volumes = df["Volume"].values.astype(float)
    n = len(prices)

    equity_arr = np.ones(n, dtype=float)
    curr_equity = 1.0
    in_pos = False
    side = ""
    entry_eq = 1.0
    sl = 0.0
    tp_ref = 0.0
    trailing_stop = 0.0
    closed_trades: List[float] = []

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

        if any(pd.isna(x) for x in (vw, vsd_val, at, adx_val, r)):
            equity_arr[i] = curr_equity
            continue

        # Get EMA values for trend
        ema_f_idx = min(i, len(ema_f_1h) - 1)
        ema_s_idx = min(i, len(ema_s_1h) - 1)
        ema_f_val = ema_f_1h.iloc[ema_f_idx] if ema_f_idx >= 0 else np.nan
        ema_s_val = ema_s_1h.iloc[ema_s_idx] if ema_s_idx >= 0 else np.nan

        is_bullish = ema_f_val > ema_s_val if not (pd.isna(ema_f_val) or pd.isna(ema_s_val)) else False
        is_bearish = ema_f_val < ema_s_val if not (pd.isna(ema_f_val) or pd.isna(ema_s_val)) else False

        # Determine current regime
        current_regime = "ranging"
        
        if regime_method != "none":
            if regime_method in ["ema", "combined"]:
                if not pd.isna(ema_f_val) and not pd.isna(ema_s_val):
                    if ema_f_val > ema_s_val:
                        current_regime = "uptrend"
                    elif ema_f_val < ema_s_val:
                        current_regime = "downtrend"

            if regime_method in ["adx", "combined"]:
                is_trending = adx_val > 25.0 if not pd.isna(adx_val) else False
                if is_trending:
                    if is_bullish:
                        current_regime = "uptrend"
                    elif is_bearish:
                        current_regime = "downtrend"

            if regime_method in ["reference", "combined"]:
                daily_idx = min(i, len(pdh) - 1)
                pdh_val = pdh.iloc[daily_idx] if daily_idx >= 0 else np.nan
                pdl_val = pdl.iloc[daily_idx] if daily_idx >= 0 else np.nan
                
                if not pd.isna(pdh_val) and not pd.isna(pdl_val):
                    daily_range = pdh_val - pdl_val
                    if daily_range > 0:
                        price_pos = (p - pdl_val) / daily_range
                        if price_pos > 0.80:
                            current_regime = "uptrend"
                        elif price_pos < 0.20:
                            current_regime = "downtrend"

        # Get parameters based on regime
        if use_optimized_params and current_regime in REGIME_PARAMS:
            params = REGIME_PARAMS[current_regime]
            sd_thresh = params["sd_threshold"]
            at_stop = params["atr_stop"]
            at_target = params["atr_target"]
            mode = params["entry_mode"]
            dev_pct = params.get("deviation_pct", deviation_pct)
        else:
            sd_thresh = sd_threshold
            at_stop = atr_stop
            at_target = atr_target
            mode = entry_mode
            dev_pct = deviation_pct

        above_vwap = p > vw
        below_vwap = p < vw
        volume_surge = v >= volume_mult * va if use_volume_filter else True
        ranging = adx_val < adx_max if not pd.isna(adx_val) else True
        rsi_long_ok = r < rsi_max if not pd.isna(r) else True
        rsi_short_ok = r > (100.0 - rsi_max) if not pd.isna(r) else True

        # Calculate deviation from VWAP as percentage
        deviation_pct_val = (p - vw) / (vw + 1e-10) * 100.0 if vw > 0 else 0.0

        # Check exits first
        if in_pos:
            curr_equity *= p / p0
            hit = False
            if side == "long":
                if lows[i] <= sl:
                    hit = True
                elif highs[i] >= tp_ref:
                    hit = True
            else:
                if highs[i] >= sl:
                    hit = True
                elif lows[i] <= tp_ref:
                    hit = True

            if hit:
                curr_equity *= (1.0 - COST_PER_SIDE)
                closed_trades.append(curr_equity / entry_eq - 1.0)
                in_pos = False

            if use_trailing_stop and in_pos:
                if side == "long":
                    new_ts = p - trailing_atr * at
                    trailing_stop = max(trailing_stop, new_ts)
                    if lows[i] <= trailing_stop:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        closed_trades.append(curr_equity / entry_eq - 1.0)
                        in_pos = False
                else:
                    new_ts = p + trailing_atr * at
                    trailing_stop = min(trailing_stop, new_ts)
                    if highs[i] >= trailing_stop:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        closed_trades.append(curr_equity / entry_eq - 1.0)
                        in_pos = False
        else:
            # Entry logic based on entry_mode
            long_signal = False
            short_signal = False

            # Calculate deviation in SD units
            deviation = p - vw
            deviation_sd = abs(deviation) / vsd_val if vsd_val > 0 else 0

            if mode == "cross":
                # Price crossing VWAP - original working mode
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

            elif mode == "deviation":
                # Deviation mode - enter when price is X% below/above VWAP
                if ranging and rsi_long_ok:
                    if deviation_pct_val < -deviation_pct and volume_surge:
                        if use_trend_filter and is_bullish:
                            long_signal = True
                        elif not use_trend_filter:
                            long_signal = True

                if ranging and rsi_short_ok:
                    if deviation_pct_val > deviation_pct and volume_surge:
                        if use_trend_filter and is_bearish:
                            short_signal = True
                        elif not use_trend_filter:
                            short_signal = True

            elif mode == "mean_reversion":
                # Mean reversion using SD bands
                upper_band = vw + sd_thresh * vsd_val
                lower_band = vw - sd_thresh * vsd_val

                if ranging and rsi_long_ok:
                    if p < lower_band and volume_surge:
                        long_signal = True

                if ranging and rsi_short_ok:
                    if p > upper_band and volume_surge:
                        short_signal = True

            if long_signal:
                curr_equity *= (1.0 - COST_PER_SIDE)
                entry_eq = curr_equity
                sl = o - at_stop * at
                tp_ref = o + at_target * at
                in_pos = True
                side = "long"
                trailing_stop = o - trailing_atr * at

            elif short_signal:
                curr_equity *= (1.0 - COST_PER_SIDE)
                entry_eq = curr_equity
                sl = o + at_stop * at
                tp_ref = o - at_target * at
                in_pos = True
                side = "short"
                trailing_stop = o + trailing_atr * at

        equity_arr[i] = curr_equity

    if in_pos:
        curr_equity *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq - 1.0)
        equity_arr[-1] = curr_equity

    return pd.Series(equity_arr, index=df.index), closed_trades


def compute_results(eq: pd.Series, trades: List[float]) -> dict:
    """Compute detailed metrics from equity and trades."""
    m = compute_metrics(eq, trades)

    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    else:
        win_rate = 0
        profit_factor = 0

    gross = m["total_return_pct"]
    costs = len(trades) * 0.30
    net = gross - costs

    return {
        "trades": len(trades),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "gross_return": gross,
        "costs": costs,
        "net_return": net,
        "sharpe": m.get("sharpe_ratio", 0),
        "max_drawdown": m.get("max_drawdown", 0),
    }


def format_result(name: str, r: dict) -> str:
    """Format a result row for display."""
    return (
        f"{name:50s} | Trades: {r['trades']:4d} | "
        f"WR: {r['win_rate']:5.1f}% | PF: {r['profit_factor']:5.2f} | "
        f"Net: {r['net_return']:+7.2f}% | Sharpe: {r['sharpe']:5.2f}"
    )


def main():
    """Run all regime-aware VWAP tests."""
    print("Loading BTCUSDT 5m data for 2024...")
    df = load_parquet("BTCUSDT", "5m")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
    print(f"Loaded {len(df)} bars (2024)")
    print()

    results: List[Tuple[str, dict]] = []

    # =========================================================================
    # TEST 1: BASELINE - deviation mode (best performing in original test)
    # =========================================================================
    print("=" * 140)
    print("TEST 1: BASELINE - Deviation Entry Mode (Single Mode)")
    print("=" * 140)

    for dev_pct in [0.5, 0.75, 1.0, 1.5]:
        for rr in [2.0, 3.0]:
            print(f"Running: Dev={dev_pct}%, RR={rr}...", end=" ", flush=True)
            eq, trades = run_vwap_backtest(
                df,
                regime_method="none",
                entry_mode="deviation",
                deviation_pct=dev_pct,
                atr_target=rr,
            )
            r = compute_results(eq, trades)
            print("Done!")
            print(format_result(f"Baseline Dev={dev_pct}%, RR={rr}", r))
            results.append((f"Baseline Dev={dev_pct}% RR={rr}", r))

    print()

    # =========================================================================
    # TEST 2: ADX REGIME SWITCH
    # =========================================================================
    print("=" * 140)
    print("TEST 2: ADX REGIME SWITCH")
    print("=" * 140)

    for use_opt in [False, True]:
        print(f"Running: {'ADX + Optimized' if use_opt else 'ADX + Default'}...", end=" ", flush=True)
        eq, trades = run_vwap_backtest(
            df,
            regime_method="adx",
            use_optimized_params=use_opt,
        )
        r = compute_results(eq, trades)
        print("Done!")
        label = "ADX + Optimized" if use_opt else "ADX + Default"
        print(format_result(label, r))
        results.append((label, r))

    print()

    # =========================================================================
    # TEST 3: EMA REGIME SWITCH
    # =========================================================================
    print("=" * 140)
    print("TEST 3: EMA REGIME SWITCH (1h EMA)")
    print("=" * 140)

    for use_opt in [False, True]:
        print(f"Running: {'EMA + Optimized' if use_opt else 'EMA + Default'}...", end=" ", flush=True)
        eq, trades = run_vwap_backtest(
            df,
            regime_method="ema",
            use_optimized_params=use_opt,
        )
        r = compute_results(eq, trades)
        print("Done!")
        label = "EMA + Optimized" if use_opt else "EMA + Default"
        print(format_result(label, r))
        results.append((label, r))

    print()

    # =========================================================================
    # TEST 4: REFERENCE LEVEL REGIME
    # =========================================================================
    print("=" * 140)
    print("TEST 4: REFERENCE LEVEL REGIME")
    print("=" * 140)

    for use_opt in [False, True]:
        print(f"Running: {'Reference + Optimized' if use_opt else 'Reference + Default'}...", end=" ", flush=True)
        eq, trades = run_vwap_backtest(
            df,
            regime_method="reference",
            use_optimized_params=use_opt,
        )
        r = compute_results(eq, trades)
        print("Done!")
        label = "Reference + Optimized" if use_opt else "Reference + Default"
        print(format_result(label, r))
        results.append((label, r))

    print()

    # =========================================================================
    # TEST 5: COMBINED REGIME
    # =========================================================================
    print("=" * 140)
    print("TEST 5: COMBINED REGIME (ADX + EMA + Reference)")
    print("=" * 140)

    for use_opt in [False, True]:
        print(f"Running: {'Combined + Optimized' if use_opt else 'Combined + Default'}...", end=" ", flush=True)
        eq, trades = run_vwap_backtest(
            df,
            regime_method="combined",
            use_optimized_params=use_opt,
        )
        r = compute_results(eq, trades)
        print("Done!")
        label = "Combined + Optimized" if use_opt else "Combined + Default"
        print(format_result(label, r))
        results.append((label, r))

    print()

    # =========================================================================
    # SUMMARY
    # =========================================================================
    print("=" * 140)
    print("SUMMARY: REGIME DETECTION COMPARISON")
    print("=" * 140)
    print(f"{'Test':50s} | {'Trades':>6} | {'Win%':>5} | {'PF':>5} | {'Net%':>7} | {'Sharpe':>6}")
    print("-" * 140)

    sorted_results = sorted(results, key=lambda x: x[1]["net_return"], reverse=True)

    for name, r in sorted_results:
        print(
            f"{name:50s} | {r['trades']:6d} | "
            f"{r['win_rate']:5.1f} | {r['profit_factor']:5.2f} | "
            f"{r['net_return']:+7.2f} | {r['sharpe']:6.2f}"
        )

    # Save results
    print()
    print("Saving results to CSV...")

    rows = []
    for name, r in results:
        rows.append({
            "Test": name,
            "Trades": r["trades"],
            "Win_Rate": r["win_rate"],
            "Profit_Factor": r["profit_factor"],
            "Gross_Return": r["gross_return"],
            "Costs": r["costs"],
            "Net_Return": r["net_return"],
            "Sharpe": r["sharpe"],
            "Max_Drawdown": r["max_drawdown"],
        })

    df_results = pd.DataFrame(rows)
    output_path = Path("G:/ai-workspace/Bot3/BTV2/tests/results") / "regime_vwap_comparison_2024.csv"
    output_path.parent.mkdir(exist_ok=True)
    df_results.to_csv(output_path, index=False)
    print(f"Results saved to {output_path}")

    # Best result
    print()
    best_name, best_r = sorted_results[0]
    print(f"BEST RESULT: {best_name}")
    print(f"  Trades: {best_r['trades']}")
    print(f"  Win Rate: {best_r['win_rate']:.1f}%")
    print(f"  Profit Factor: {best_r['profit_factor']:.2f}")
    print(f"  Net Return: {best_r['net_return']:+.2f}%")
    print(f"  Sharpe: {best_r['sharpe']:.2f}")


if __name__ == "__main__":
    main()
