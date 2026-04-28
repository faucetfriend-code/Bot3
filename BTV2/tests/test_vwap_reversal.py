#!/usr/bin/env python3
"""
VWAP Scalping Reversal Test
============================
Test the hypothesis: if original has ~30% win rate, does reversing entry signals give ~70% win rate?

Original Logic:
- LONG: price crosses ABOVE VWAP + EMA bullish + volume surge + ranging + RSI gate
- SHORT: price crosses BELOW VWAP + EMA bearish + volume surge + ranging + RSI gate

Reversed Logic:
- SHORT: price crosses ABOVE VWAP + EMA bullish + volume surge + ranging + RSI gate
- LONG: price crosses BELOW VWAP + EMA bearish + volume surge + ranging + RSI gate
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
    compute_adx, compute_rsi, compute_ema, COST_PER_SIDE
)


def _open_long(curr_equity, entry_eq, sl, tp_ref, entry_p, stop_loss, take_profit, in_pos, side):
    """Open long position."""
    curr_equity *= (1.0 - COST_PER_SIDE)
    entry_eq[0] = curr_equity
    sl[0] = stop_loss
    tp_ref[0] = take_profit
    in_pos[0] = True
    side[0] = "long"
    return curr_equity


def _open_short(curr_equity, entry_eq, sl, tp_ref, entry_p, stop_loss, take_profit, in_pos, side):
    """Open short position."""
    curr_equity *= (1.0 - COST_PER_SIDE)
    entry_eq[0] = curr_equity
    sl[0] = stop_loss
    tp_ref[0] = take_profit
    in_pos[0] = True
    side[0] = "short"
    return curr_equity


def run_vwap_original(
    df: pd.DataFrame,
    cutoff: float = 0.10,
    sd_threshold: float = 2.5,
    atr_stop: float = 1.0,
    atr_target: float = 2.0,
    ema_fast: int = 9,
    ema_slow: int = 20,
    use_trend_filter: bool = True,
    use_volume_filter: bool = True,
    volume_mult: float = 2.0,
    use_trailing_stop: bool = True,
    trailing_atr: float = 1.0,
    adx_max: float = 22.0,
    rsi_max: float = 45.0,
    df_exit: pd.DataFrame | None = None,
):
    """
    Original VWAP Scalping - Momentum breakout strategy.
    
    Entry Rules (MOMENTUM):
    - LONG: price crosses ABOVE VWAP + EMA bullish + volume + ranging + RSI
    - SHORT: price crosses BELOW VWAP + EMA bearish + volume + ranging + RSI
    """
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
        
        is_bullish = ema_f_val > ema_m_val
        is_bearish = ema_f_val < ema_m_val
        
        above_vwap = p > vw
        below_vwap = p < vw
        
        volume_surge = v >= volume_mult * va if use_volume_filter else True
        
        ranging = adx_v < adx_max
        rsi_long_ok = r < rsi_max
        rsi_short_ok = r > (100.0 - rsi_max)
        
        # Check exits
        if in_pos[0]:
            curr_equity *= p / p0
            
            # Exit checks
            if side[0] == "long":
                if lows[i] <= sl[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif highs[i] >= tp_ref[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif use_trailing_stop:
                    new_ts = p - trailing_atr * at
                    trailing_stop[0] = max(trailing_stop[0], new_ts)
                    if lows[i] <= trailing_stop[0]:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
            else:  # short
                if highs[i] >= sl[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif lows[i] <= tp_ref[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif use_trailing_stop:
                    new_ts = p + trailing_atr * at
                    trailing_stop[0] = min(trailing_stop[0], new_ts)
                    if highs[i] >= trailing_stop[0]:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
        else:
            # ORIGINAL ENTRY LOGIC - MOMENTUM
            # LONG: price crosses ABOVE VWAP
            long_signal = False
            if ranging and rsi_long_ok:
                if use_trend_filter:
                    if above_vwap and is_bullish and volume_surge:
                        if i >= 1 and prices[i-1] < vwap.iloc[i-1]:
                            long_signal = True
                else:
                    if above_vwap and i >= 1 and prices[i-1] < vwap.iloc[i-1]:
                        long_signal = True
            
            # SHORT: price crosses BELOW VWAP
            short_signal = False
            if ranging and rsi_short_ok:
                if use_trend_filter:
                    if below_vwap and is_bearish and volume_surge:
                        if i >= 1 and prices[i-1] > vwap.iloc[i-1]:
                            short_signal = True
                else:
                    if below_vwap and i >= 1 and prices[i-1] > vwap.iloc[i-1]:
                        short_signal = True
            
            if long_signal:
                curr_equity = _open_long(
                    curr_equity, entry_eq, sl, tp_ref, o,
                    o - atr_stop * at, o + atr_target * at, in_pos, side,
                )
                if in_pos[0]:
                    trailing_stop[0] = o - trailing_atr * at
            
            elif short_signal:
                curr_equity = _open_short(
                    curr_equity, entry_eq, sl, tp_ref, o,
                    o + atr_stop * at, o - atr_target * at, in_pos, side,
                )
                if in_pos[0]:
                    trailing_stop[0] = o + trailing_atr * at
        
        equity_arr[i] = curr_equity
    
    if in_pos[0]:
        curr_equity *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
        equity_arr[-1] = curr_equity
    
    return pd.Series(equity_arr, index=df.index), closed_trades


def run_vwap_reversed(
    df: pd.DataFrame,
    cutoff: float = 0.10,
    sd_threshold: float = 2.5,
    atr_stop: float = 1.0,
    atr_target: float = 2.0,
    ema_fast: int = 9,
    ema_slow: int = 20,
    use_trend_filter: bool = True,
    use_volume_filter: bool = True,
    volume_mult: float = 2.0,
    use_trailing_stop: bool = True,
    trailing_atr: float = 1.0,
    adx_max: float = 22.0,
    rsi_max: float = 45.0,
    df_exit: pd.DataFrame | None = None,
):
    """
    Reversed VWAP Scalping - Mean reversion strategy.
    
    Entry Rules (REVERSED - MEAN REVERSION):
    - SHORT: price crosses ABOVE VWAP + EMA bullish + volume + ranging + RSI
    - LONG: price crosses BELOW VWAP + EMA bearish + volume + ranging + RSI
    
    Essentially: enter opposite direction of original signal
    """
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
        
        is_bullish = ema_f_val > ema_m_val
        is_bearish = ema_f_val < ema_m_val
        
        above_vwap = p > vw
        below_vwap = p < vw
        
        volume_surge = v >= volume_mult * va if use_volume_filter else True
        
        ranging = adx_v < adx_max
        rsi_long_ok = r < rsi_max
        rsi_short_ok = r > (100.0 - rsi_max)
        
        # Check exits
        if in_pos[0]:
            curr_equity *= p / p0
            
            # Exit checks
            if side[0] == "long":
                if lows[i] <= sl[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif highs[i] >= tp_ref[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif use_trailing_stop:
                    new_ts = p - trailing_atr * at
                    trailing_stop[0] = max(trailing_stop[0], new_ts)
                    if lows[i] <= trailing_stop[0]:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
            else:  # short
                if highs[i] >= sl[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif lows[i] <= tp_ref[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif use_trailing_stop:
                    new_ts = p + trailing_atr * at
                    trailing_stop[0] = min(trailing_stop[0], new_ts)
                    if highs[i] >= trailing_stop[0]:
                        curr_equity *= (1.0 - COST_PER_SIDE)
                        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                        in_pos[0] = False
        else:
            # REVERSED ENTRY LOGIC - MEAN REVERSION
            # SHORT when price crosses ABOVE VWAP (original goes LONG)
            short_signal = False
            if ranging and rsi_short_ok:
                if use_trend_filter:
                    if above_vwap and is_bullish and volume_surge:
                        if i >= 1 and prices[i-1] < vwap.iloc[i-1]:
                            short_signal = True
                else:
                    if above_vwap and i >= 1 and prices[i-1] < vwap.iloc[i-1]:
                        short_signal = True
            
            # LONG when price crosses BELOW VWAP (original goes SHORT)
            long_signal = False
            if ranging and rsi_long_ok:
                if use_trend_filter:
                    if below_vwap and is_bearish and volume_surge:
                        if i >= 1 and prices[i-1] > vwap.iloc[i-1]:
                            long_signal = True
                else:
                    if below_vwap and i >= 1 and prices[i-1] > vwap.iloc[i-1]:
                        long_signal = True
            
            if long_signal:
                curr_equity = _open_long(
                    curr_equity, entry_eq, sl, tp_ref, o,
                    o - atr_stop * at, o + atr_target * at, in_pos, side,
                )
                if in_pos[0]:
                    trailing_stop[0] = o - trailing_atr * at
            
            elif short_signal:
                curr_equity = _open_short(
                    curr_equity, entry_eq, sl, tp_ref, o,
                    o + atr_stop * at, o - atr_target * at, in_pos, side,
                )
                if in_pos[0]:
                    trailing_stop[0] = o + trailing_atr * at
        
        equity_arr[i] = curr_equity
    
    if in_pos[0]:
        curr_equity *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
        equity_arr[-1] = curr_equity
    
    return pd.Series(equity_arr, index=df.index), closed_trades


def compute_metrics(eq_series, trades):
    """Compute backtest metrics."""
    total_return = (eq_series.iloc[-1] / eq_series.iloc[0] - 1) * 100
    
    if trades:
        wins = [t for t in trades if t > 0]
        losses = [t for t in trades if t < 0]
        win_rate = len(wins) / len(trades) * 100
        avg_win = np.mean(wins) * 100 if wins else 0
        avg_loss = np.mean(losses) * 100 if losses else 0
        profit_factor = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 0
        
        return {
            'total_return_pct': total_return,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'num_trades': len(trades),
        }
    
    return {
        'total_return_pct': total_return,
        'win_rate': 0,
        'profit_factor': 0,
        'avg_win': 0,
        'avg_loss': 0,
        'num_trades': 0,
    }


def main():
    # Load BTCUSDT 5m data for 2024
    print("Loading BTCUSDT 5m data for 2024...")
    df = load_parquet("BTCUSDT", "5m")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= '2024-01-01') & (df.index < '2025-01-01')]
    print(f"Loaded {len(df)} bars (2024)")
    print()
    
    # Test parameters - using best params from sweep results
    # SD=2.5, ADX=35, RSI=70, ATR=0.7, Vol=1.5, Trend=False
    params = {
        'cutoff': 0.10,
        'sd_threshold': 2.5,
        'atr_stop': 0.7,  # From sweep results
        'atr_target': 2.0,
        'ema_fast': 9,
        'ema_slow': 20,
        'use_trend_filter': False,  # Best from sweep
        'use_volume_filter': True,
        'volume_mult': 1.5,  # From sweep
        'use_trailing_stop': True,
        'trailing_atr': 1.0,
        'adx_max': 35.0,  # From sweep
        'rsi_max': 70.0,  # From sweep
    }
    
    print("=" * 80)
    print("VWAP SCALPING REVERSAL TEST - BTCUSDT 5m 2024")
    print("=" * 80)
    print(f"Parameters: cutoff={params['cutoff']}, sd={params['sd_threshold']}, "
          f"atr_stop={params['atr_stop']}, atr_target={params['atr_target']}")
    print(f"           adx_max={params['adx_max']}, rsi_max={params['rsi_max']}, "
          f"trend_filter={params['use_trend_filter']}")
    print()
    
    # Run original (momentum) strategy
    print("Running ORIGINAL (momentum) strategy...")
    eq_orig, trades_orig = run_vwap_original(df, **params)
    m_orig = compute_metrics(eq_orig, trades_orig)
    
    print(f"  Trades: {m_orig['num_trades']}")
    print(f"  Win Rate: {m_orig['win_rate']:.1f}%")
    print(f"  Profit Factor: {m_orig['profit_factor']:.2f}")
    print(f"  Gross Return: {m_orig['total_return_pct']:+.1f}%")
    costs_orig = m_orig['num_trades'] * 0.30
    net_orig = m_orig['total_return_pct'] - costs_orig
    print(f"  Net Return: {net_orig:+.1f}% (after {costs_orig:.1f}% costs)")
    print()
    
    # Run reversed (mean reversion) strategy
    print("Running REVERSED (mean reversion) strategy...")
    eq_rev, trades_rev = run_vwap_reversed(df, **params)
    m_rev = compute_metrics(eq_rev, trades_rev)
    
    print(f"  Trades: {m_rev['num_trades']}")
    print(f"  Win Rate: {m_rev['win_rate']:.1f}%")
    print(f"  Profit Factor: {m_rev['profit_factor']:.2f}")
    print(f"  Gross Return: {m_rev['total_return_pct']:+.1f}%")
    costs_rev = m_rev['num_trades'] * 0.30
    net_rev = m_rev['total_return_pct'] - costs_rev
    print(f"  Net Return: {net_rev:+.1f}% (after {costs_rev:.1f}% costs)")
    print()
    
    # Summary comparison
    print("=" * 80)
    print("COMPARISON SUMMARY")
    print("=" * 80)
    print(f"{'Metric':<25} {'Original (Momentum)':<25} {'Reversed (Mean Rev)':<25}")
    print("-" * 80)
    print(f"{'Win Rate':<25} {m_orig['win_rate']:.1f}%{'':<20} {m_rev['win_rate']:.1f}%")
    print(f"{'Net Return':<25} {net_orig:+.1f}%{'':<20} {net_rev:+.1f}%")
    print(f"{'Trade Count':<25} {m_orig['num_trades']}{'':<22} {m_rev['num_trades']}")
    print(f"{'Profit Factor':<25} {m_orig['profit_factor']:.2f}{'':<21} {m_rev['profit_factor']:.2f}")
    print()
    
    # Hypothesis test
    print("=" * 80)
    print("HYPOTHESIS TEST")
    print("=" * 80)
    print(f"Original Win Rate: {m_orig['win_rate']:.1f}%")
    print(f"Reversed Win Rate: {m_rev['win_rate']:.1f}%")
    print(f"Win Rate Change: {m_rev['win_rate'] - m_orig['win_rate']:+.1f}%")
    print()
    
    # Check if hypothesis holds (reversal flips win rate)
    original_wr = m_orig['win_rate']
    reversed_wr = m_rev['win_rate']
    
    if reversed_wr > 50 and original_wr < 50:
        hypothesis_holds = "YES"
        reason = "Reversal moved win rate above 50%"
    elif reversed_wr < 50 and original_wr > 50:
        hypothesis_holds = "YES (inverse)"
        reason = "Original moved win rate above 50%"
    elif abs((100 - original_wr) - reversed_wr) < 10:
        hypothesis_holds = "PARTIALLY"
        reason = "Win rates approximately sum to ~100%"
    else:
        hypothesis_holds = "NO"
        reason = "Win rates don't show inverse relationship"
    
    print(f"Hypothesis: Reversing signals flips win rate (~30% -> ~70%)")
    print(f"Result: {hypothesis_holds}")
    print(f"Reason: {reason}")
    

if __name__ == "__main__":
    main()
