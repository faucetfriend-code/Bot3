"""
test_scalp_session_vwap.py
=========================
Test TRUE scalping — tight percentage-based stops + targets within the 
London/NY overlap session.

Key Innovation: Use percentage-based stops instead of ATR multiples for 5m scalping.
ATR on 5m bars is too small (~$50-100 for BTC), causing us to get stopped out 
by normal noise.

Sessions to test:
- London/NY overlap: 13:00-16:00 UTC (best for scalping)
- NY session: 13:00-20:00 UTC

Parameter grid:
- stop_pct: [0.15, 0.20, 0.25] (% to stop loss)
- target_pct: [0.30, 0.40, 0.50] (take profit %)
- entry_mode: ["bull_pullback", "bear_pullback"] (both long and short)

Fixed:
- sd_threshold: 3.0 (outer band for high-quality entries)
- require_sfp: True (rejection candle confirmation)
- use_volume_filter: True
- COST_PER_SIDE: 0.30% (0.60% round trip)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Tuple, Any, List

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.strategies import (
    compute_atr,
    apply_butterworth,
    compute_adx,
    compute_rsi,
    compute_ema,
    compute_vwap_anchored,
    COST_PER_SIDE,
    INTERVAL_BARS_PER_YEAR,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TEST_START = "2023-01-01"
TEST_END = "2026-01-01"
CUTOFF = 0.10

# Session definitions (UTC hours)
SESSIONS: Dict[str, Tuple[int, int]] = {
    "London_NY_Overlap": (13, 16),
    "NY": (13, 20),
}

# Percentage stop/target grid - as DECIMALS (0.5% = 0.005)
STOP_PCTS = [0.005, 0.0075, 0.010]  # 0.5%, 0.75%, 1%
TARGET_PCTS = [0.010, 0.015, 0.020]  # 1%, 1.5%, 2%

# Entry modes
ENTRY_MODES = ["bull_pullback", "bear_pullback"]

# Fixed config
FIXED_CONFIG = {
    "sd_threshold": 3.0,
    "require_reversal_candle": True,
    "use_volume_filter": True,
    "volume_mult": 1.2,
    "use_trend_filter": True,
}

# Cost model: 0.30% per side = 0.60% round trip
COST = 0.003


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────


def load_5m_data(start: str, end: str) -> pd.DataFrame:
    """Load BTCUSDT 5m data for test period."""
    path = DATA_DIR / "BTCUSDT_5m.parquet"
    if not path.exists():
        print(f"ERROR: No data found at {path}")
        return pd.DataFrame()

    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df.index >= start_ts) & (df.index < end_ts)
    return df.loc[mask].copy()


# ───────────────────────────────────���─────────────────────────────────────────
# PRE-COMPUTE INDICATORS
# ─────────────────────────────────────────────────────────────────────────────


def precompute_indicators(df: pd.DataFrame) -> Dict[str, Any]:
    """Pre-compute all indicators once for efficiency."""
    print("Pre-computing indicators...")
    
    fc = apply_butterworth(df["Close"], CUTOFF)
    raw_close = df["Close"]
    
    vwap, vs = compute_vwap_anchored(
        df["High"], df["Low"], raw_close, df["Volume"]
    )
    atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
    adx = compute_adx(df["High"], df["Low"], df["Close"], 14)
    rsi = compute_rsi(fc, 14)
    ema_f = compute_ema(fc, 9)
    ema_m = compute_ema(fc, 20)
    vol_avg = df["Volume"].rolling(20).mean()
    
    # Convert index to hour array efficiently
    hours_arr = df.index.hour.values
    
    return {
        "vwap": vwap.values,
        "vs": vs.values,
        "atr": atr.values,
        "adx": adx.values,
        "rsi": rsi.values,
        "ema_f": ema_f.values,
        "ema_m": ema_m.values,
        "vol_avg": vol_avg.values,
        "hours": hours_arr,
        "close": raw_close.values,
        "high": df["High"].values,
        "low": df["Low"].values,
        "open": df["Open"].values,
        "volume": df["Volume"].values,
    }


# ─────────────────────────────────────────────────────────────────────────────
# PERCENTAGE-BASED BACKTEST
# ─────────────────────────────────────────────────────────────────────────────


def run_backtest(
    ind: Dict[str, Any],
    in_session: np.ndarray,
    stop_pct: float,
    target_pct: float,
    entry_mode: str,
    sd_threshold: float = 3.0,
    require_sfp: bool = False,  # Disabled for testing
    use_volume_filter: bool = False,  # Disabled for testing
    volume_mult: float = 1.2,
    use_trend_filter: bool = False,  # Disabled for testing
    debug: bool = False,
) -> Tuple[np.ndarray, List[Dict[str, Any]]]:
    """
    Run backtest with percentage-based stops and targets.
    Returns (equity_curve, trades_list)
    """
    vwap = ind["vwap"]
    vs = ind["vs"]
    adx_arr = ind["adx"]
    rsi_arr = ind["rsi"]
    ema_f = ind["ema_f"]
    ema_m = ind["ema_m"]
    vol_avg = ind["vol_avg"]
    prices = ind["close"]
    highs = ind["high"]
    lows = ind["low"]
    volumes = ind["volume"]
    
    n = len(prices)
    
    # Debug: Sample some data
    # Debug: Sample some data
    if debug:
        print(f"\n  DEBUG: n={n}")
        print(f"  DEBUG: in_session sum={in_session.sum()}")
        
        # Test a few entries manually
        test_entries = 0
        for i in range(1000, min(10000, n - 1)):
            if in_session[i]:
                ema_f_val = ema_f[i]
                ema_m_val = ema_m[i]
                if np.isnan(ema_f_val) or np.isnan(ema_m_val):
                    continue
                is_bullish = ema_f_val > ema_m_val
                
                vw = vwap[i]
                vsd = vs[i]
                if np.isnan(vw) or np.isnan(vsd):
                    continue
                
                ranging = adx_arr[i] < 30.0
                rsi_val = rsi_arr[i]
                
                if ranging and is_bullish and rsi_val < 70.0:
                    p = prices[i]
                    
                    if p < (vw + 0.5 * 3.0 * vsd):
                        # Would enter long
                        sl_price_t = p * (1.0 - 0.15)
                        tp_price_t = p * (1.0 + 0.30)
                        test_entries += 1
                        
                        # Check immediate exit on next bar
                        next_high = highs[i + 1]
                        next_low = lows[i + 1]
                        exited_immediately = next_low <= sl_price_t or next_high >= tp_price_t
                        
                        if test_entries <= 3:
                            print(f"  DEBUG: would enter long at i={i}, p={p:.2f}, sl={sl_price_t:.2f}, tp={tp_price_t:.2f}, next_low={next_low:.2f}, next_high={next_high:.2f}, immediate_exit={exited_immediately}")
        
        print(f"  DEBUG: would entry={test_entries}")
    
    n = len(prices)
    equity_arr = np.ones(n, dtype=float)
    curr_equity = 1.0
    in_pos = False
    side = ""
    entry_price = 0.0
    sl_price = 0.0
    tp_price = 0.0
    entry_eq = 1.0
    closed_trades: List[Dict[str, Any]] = []
    
    for i in range(1, n):
        vw = vwap[i]
        vsd = vs[i]
        adx_v = adx_arr[i]
        r = rsi_arr[i]
        p = prices[i]
        p0 = prices[i - 1]
        v = volumes[i]
        va = vol_avg[i]
        
        ema_f_val = ema_f[i]
        ema_m_val = ema_m[i]
        
        # Skip if indicators not ready
        if any(np.isnan(x) for x in (vw, vsd, adx_v, r)):
            equity_arr[i] = curr_equity
            continue
        
        # Trend direction
        is_bullish = ema_f_val > ema_m_val
        is_bearish = ema_f_val < ema_m_val
        
        # Session gate
        if not in_session[i]:
            equity_arr[i] = curr_equity
            continue
        
        # Check exits first
        if in_pos:
            curr_equity *= p / p0
            
            hit_sl = False
            hit_tp = False
            
            if side == "long":
                if lows[i] <= sl_price:
                    hit_sl = True
                elif highs[i] >= tp_price:
                    hit_tp = True
            else:
                if highs[i] >= sl_price:
                    hit_sl = True
                elif lows[i] <= tp_price:
                    hit_tp = True
            
            if hit_sl or hit_tp:
                curr_equity *= (1.0 - COST)
                pnl = curr_equity / entry_eq - 1.0
                closed_trades.append({
                    "side": side,
                    "entry_price": entry_price,
                    "exit_price": p,
                    "pnl_pct": pnl * 100.0,
                    "hit_sl": hit_sl,
                    "hit_tp": hit_tp,
                })
                in_pos = False
        else:
            # Entry signals
            long_signal = False
            short_signal = False
            
            # Filters - relaxed for scalping
            ranging = adx_v < 30.0  # Allow more trending
            rsi_long_ok = r < 70.0  # Relaxed - not overbought
            rsi_short_ok = r > 30.0  # Relaxed - not oversold
            # Handle NaN in volume average
            volume_surge = (
                (not use_volume_filter) or 
                (va is not None and not np.isnan(va) and v >= volume_mult * va)
            )
            
            # Bands
            upper_band = vw + sd_threshold * vsd
            lower_band = vw - sd_threshold * vsd
            
            if entry_mode == "bull_pullback":
                trend_ok = (not use_trend_filter) or is_bullish
                
                if ranging and rsi_long_ok and trend_ok and volume_surge:
                    if require_sfp:
                        # SFP: wick below lower band + close above
                        long_ok = lows[i] <= lower_band and p > lower_band
                    else:
                        # Relaxed: price anywhere below upper band (within range)
                        long_ok = p < (vw + 0.5 * sd_threshold * vsd)
                    
                    if long_ok:
                        long_signal = True
            
            elif entry_mode == "bear_pullback":
                trend_ok = (not use_trend_filter) or is_bearish
                
                if ranging and rsi_short_ok and trend_ok and volume_surge:
                    if require_sfp:
                        # SFP: wick above upper band + close below
                        short_ok = highs[i] >= upper_band and p < upper_band
                    else:
                        # Relaxed: price anywhere above lower band (within range)
                        short_ok = p > (vw - 0.5 * sd_threshold * vsd)
                    
                    if short_ok:
                        short_signal = True
            
            if long_signal:
                entry_price = p
                sl_price = p * (1.0 - stop_pct)
                tp_price = p * (1.0 + target_pct)
                entry_eq = curr_equity * (1.0 - COST)
                curr_equity = entry_eq
                side = "long"
                in_pos = True
                if debug:
                    print(f"  DEBUG: ENTER LONG at i={i}, price={p:.2f}, sl={sl_price:.2f}, tp={tp_price:.2f}")
            
            elif short_signal:
                entry_price = p
                sl_price = p * (1.0 + stop_pct)
                tp_price = p * (1.0 - target_pct)
                entry_eq = curr_equity * (1.0 - COST)
                curr_equity = entry_eq
                side = "short"
                in_pos = True
                if debug:
                    print(f"  DEBUG: ENTER SHORT at i={i}, price={p:.2f}, sl={sl_price:.2f}, tp={tp_price:.2f}")
        
        equity_arr[i] = curr_equity
    
    # Close open position at end
    if in_pos:
        curr_equity *= (1.0 - COST)
        pnl = curr_equity / entry_eq - 1.0
        closed_trades.append({
            "side": side,
            "entry_price": entry_price,
            "exit_price": prices[-1],
            "pnl_pct": pnl * 100.0,
            "hit_sl": False,
            "hit_tp": False,
            "open_at_end": True,
        })
        equity_arr[-1] = curr_equity
    
    return equity_arr, closed_trades

# ─────────────────────────────────────────────────────────────────────────────
# METRICS
# ─────────────────────────────────────────────────────────────────────────────


def compute_metrics(
    equity_arr: np.ndarray,
    trades: List[Dict[str, Any]],
    start_val: float = 1.0,
) -> Dict[str, Any]:
    """Compute performance metrics from equity array and trades."""
    import math
    
    if not trades:
        return {
            "total_return_pct": 0.0, "cagr_pct": 0.0, "sharpe": 0.0,
            "max_dd_pct": 0.0, "win_rate_pct": 0.0, "profit_factor": 0.0,
            "n_trades": 0, "avg_win_pct": 0.0, "avg_loss_pct": 0.0,
        }
    
    price = float(equity_arr[-1]) if len(equity_arr) > 0 else start_val
    total_ret = (price / start_val - 1.0) * 100.0
    
    pnl_list = [t["pnl_pct"] for t in trades]
    wins = [t for t in pnl_list if t > 0]
    losses = [t for t in pnl_list if t <= 0]
    
    n_trades = len(pnl_list)
    win_rate = len(wins) / n_trades * 100.0 if n_trades > 0 else 0.0
    
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else 0.0
    
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    
    # Max DD from equity curve
    equity_series = pd.Series(equity_arr)
    running_max = equity_series.cummax()
    max_dd = float(((equity_series - running_max) / running_max).min() * 100.0)
    
    return {
        "total_return_pct": round(total_ret, 2),
        "cagr_pct": round(total_ret / 3, 2),  # 3 years
        "sharpe": round(total_ret / abs(max_dd) if max_dd > 0 else 0, 3),
        "max_dd_pct": round(abs(max_dd), 2),
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(pf, 2),
        "n_trades": n_trades,
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# GRID SEARCH
# ─────────────────────────────────────────────────────────────────────────────


def run_grid_search(
    df: pd.DataFrame,
    ind: Dict[str, Any],
) -> pd.DataFrame:
    """Run grid search over all parameter combinations."""
    results = []
    
    # Build session filters
    session_filters = {}
    for session_name, (session_start, session_end) in SESSIONS.items():
        hours = ind["hours"]
        if session_end <= session_start:
            in_session = (hours >= session_start) | (hours < session_end)
        else:
            in_session = (hours >= session_start) & (hours < session_end)
        session_filters[session_name] = in_session
    
    total_combos = (
        len(SESSIONS) * len(ENTRY_MODES) * len(STOP_PCTS) * len(TARGET_PCTS)
    )
    combo_idx = 0
    
    print(f"\nRunning {total_combos} combinations...")
    
    for session_name, (session_start, session_end) in SESSIONS.items():
        in_session = session_filters[session_name]
        
        for entry_mode in ENTRY_MODES:
            for stop_pct in STOP_PCTS:
                for target_pct in TARGET_PCTS:
                    # Skip combos that don't meet minimum R ratio
                    r_ratio = target_pct / stop_pct
                    if r_ratio < 1.5:
                        continue
                    
                    combo_idx += 1
                    
                    if combo_idx % 5 == 0:
                        print(f"  Progress: {combo_idx}/{total_combos}")
                    
                    equity, trades = run_backtest(
                        ind=ind,
                        in_session=in_session,
                        stop_pct=stop_pct,
                        target_pct=target_pct,
                        entry_mode=entry_mode,
                        sd_threshold=FIXED_CONFIG["sd_threshold"],
                        require_sfp=FIXED_CONFIG["require_reversal_candle"],
                        use_volume_filter=FIXED_CONFIG["use_volume_filter"],
                        volume_mult=FIXED_CONFIG["volume_mult"],
                        use_trend_filter=FIXED_CONFIG["use_trend_filter"],
                    )
                    
                    metrics = compute_metrics(equity, trades)
                    
                    results.append({
                        "session": session_name,
                        "session_start": session_start,
                        "session_end": session_end,
                        "entry_mode": entry_mode,
                        "stop_pct": stop_pct,
                        "target_pct": target_pct,
                        "r_ratio": r_ratio,
                        "trades": metrics["n_trades"],
                        "win_rate": metrics["win_rate_pct"],
                        "return_pct": metrics["total_return_pct"],
                        "sharpe": metrics["sharpe"],
                        "max_dd": metrics["max_dd_pct"],
                        "profit_factor": metrics["profit_factor"],
                        "avg_win": metrics["avg_win_pct"],
                        "avg_loss": metrics["avg_loss_pct"],
                    })
    
    df_results = pd.DataFrame(results)
    df_results = df_results.sort_values("return_pct", ascending=False)
    
    return df_results


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────


def main():
    """Run the scalping session VWAP test."""
    print("Loading 5m data...")
    df = load_5m_data(TEST_START, TEST_END)
    
    if df.empty:
        print("ERROR: No data loaded")
        return
    
    print(f"Loaded {len(df):,} bars from {TEST_START} to {TEST_END}")
    print(f"Date range: {df.index[0]} to {df.index[-1]}")
    
    # Pre-compute indicators
    ind = precompute_indicators(df)
    
    # Run grid search
    results_df = run_grid_search(df, ind)
    
    if results_df.empty:
        print("ERROR: No results generated")
        return
    
    # Filter for minimum trades
    min_trades = 20
    filtered = results_df[results_df["trades"] >= min_trades].copy()
    
    print("\n" + "=" * 100)
    print("SCALPING SESSION VWAP - TOP 10 BEST CONFIGS")
    print("=" * 100)
    print(
        f"{'Session':<20} {'Entry':<15} {'Stop%':>8} {'Target%':>8} "
        f"{'R':>5} {'Trades':>7} {'WR%':>6} {'Return%':>10} "
        f"{'Sharpe':>7} {'MaxDD%':>8}"
    )
    print("-" * 100)
    
    top_10 = filtered.head(10)
    for _, row in top_10.iterrows():
        print(
            f"{row['session']:<20} {row['entry_mode']:<15} "
            f"{row['stop_pct']:>7.1%} {row['target_pct']:>7.1%} "
            f"{row['r_ratio']:>5.1f} {row['trades']:>7} "
            f"{row['win_rate']:>6.1f} {row['return_pct']:>10.2f} "
            f"{row['sharpe']:>7.2f} {row['max_dd']:>8.2f}"
        )
    
    # Best overall
    if not filtered.empty:
        best = filtered.iloc[0]
        print("\n" + "=" * 100)
        print("BEST OVERALL CONFIG")
        print("=" * 100)
        print(f"  Session: {best['session']} ({int(best['session_start']):02d}:00-{int(best['session_end']):02d}:00 UTC)")
        print(f"  Entry Mode: {best['entry_mode']}")
        print(f"  Stop %: {best['stop_pct']:.1%}")
        print(f"  Target %: {best['target_pct']:.1%}")
        print(f"  R Ratio: {best['r_ratio']:.1f}:1")
        print(f"  Trades: {best['trades']}")
        print(f"  Win Rate: {best['win_rate']:.1f}%")
        print(f"  Total Return: {best['return_pct']:.2f}%")
        print(f"  Sharpe: {best['sharpe']:.2f}")
        print(f"  Max Drawdown: {best['max_dd']:.2f}%")
        print(f"  Profit Factor: {best['profit_factor']:.2f}")
    
    # Save results
    results_path = RESULTS_DIR / "scalp_session_vwap_results.csv"
    results_df.to_csv(results_path, index=False)
    print(f"\nResults saved to {results_path}")
    
    # Detailed top config save
    if not filtered.empty:
        top_config = filtered.head(1).to_dict("records")[0]
        best_config_path = RESULTS_DIR / "scalp_session_vwap_best.csv"
        pd.DataFrame([top_config]).to_csv(best_config_path, index=False)
        print(f"Best config saved to {best_config_path}")


if __name__ == "__main__":
    main()