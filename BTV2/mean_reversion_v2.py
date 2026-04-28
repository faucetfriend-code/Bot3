"""
Improved Mean Reversion with ATR-based exits.

This version:
1. Uses ATR-based take profit (not SMA-20)
2. Uses wider stops to let winners run
3. Only goes LONG in ranging markets (ADX filter)
"""

import numpy as np
import pandas as pd
from typing import Tuple

from strategies import (
    apply_butterworth, 
    compute_rsi, 
    compute_bollinger, 
    compute_sma, 
    compute_atr,
    compute_adx,
    COST_PER_SIDE,
    _open_long,
    _open_short,
    _check_exit,
)


def run_mean_reversion_v2(
    df: pd.DataFrame,
    cutoff: float,
    rsi_oversold: float = 20.0,
    rsi_overbought: float = 80.0,
    bb_proximity: float = 0.02,
    atr_stop: float = 2.0,
    atr_target: float = 3.0,
    adx_max: float = 25.0,      # NEW: ranging market filter
    rsi_period: int = 14,
    bb_period: int = 20,
    bb_std: float = 2.0,
) -> Tuple[pd.Series, list]:
    """
    Improved RSI + Bollinger Bands mean-reversion with ATR exits.
    
    Key improvements over v1:
    - Exit at ATR target (not SMA-20) - lets winners run
    - ADX filter - only trade in ranging markets
    - Configurable RSI period
    
    Entry LONG  : RSI < oversold AND price within bb_proximity% of lower BB AND ADX < adx_max
    Entry SHORT: RSI > overbought AND price within bb_proximity% of upper BB AND ADX < adx_max
    Exit LONG  : SL = entry - atr_stop*ATR OR TP = entry + atr_target*ATR
    Exit SHORT : SL = entry + atr_stop*ATR OR TP = entry - atr_target*ATR
    """
    fc = apply_butterworth(df["Close"], cutoff)
    rsi = compute_rsi(fc, rsi_period)
    bb_up, bb_mid, bb_lo = compute_bollinger(fc, bb_period, bb_std)
    atr = compute_atr(df["High"], df["Low"], df["Close"], 14)
    adx = compute_adx(df["High"], df["Low"], df["Close"], 14)
    sma20 = compute_sma(fc, 20)  # Still needed for some calcs

    prices = df["Close"].values.astype(float)
    highs = df["High"].values.astype(float)
    lows = df["Low"].values.astype(float)
    n = len(prices)

    equity_arr = np.ones(n, dtype=float)
    curr_equity = 1.0
    in_pos = [False]
    side = [""]
    entry_eq = [1.0]
    entry_price = [0.0]
    sl = [0.0]
    tp = [0.0]
    closed_trades = []

    for i in range(1, n):
        r = rsi.iloc[i]
        bbu = bb_up.iloc[i]
        bbl = bb_lo.iloc[i]
        at = atr.iloc[i]
        ad = adx.iloc[i] if i >= 14 else 50  # Default to trending if no ADX yet
        p = prices[i]
        p0 = prices[i - 1]
        hi = highs[i]
        lo = lows[i]

        if any(np.isnan(x) for x in (r, bbu, bbl, at)):
            equity_arr[i] = curr_equity
            continue

        if in_pos[0]:
            # Update equity for price movement
            curr_equity *= p / p0
            
            # Check SL
            if side[0] == "long":
                if lo <= sl[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif hi >= tp[0]:  # Hit TP
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
            else:  # short
                if hi >= sl[0]:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif lo <= tp[0]:  # Hit TP
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
        else:
            # Market must be ranging (ADX < threshold)
            if ad >= adx_max:
                equity_arr[i] = curr_equity
                continue
                
            prox_lo = (p - bbl) / (bbl + 1e-10)
            prox_hi = (bbu - p) / (bbu + 1e-10)
            
            # LONG: price near lower BB AND RSI oversold
            if r < rsi_oversold and 0.0 <= prox_lo <= bb_proximity:
                sl_p = p - atr_stop * at
                tp_p = p + atr_target * at
                entry_price[0] = p
                curr_equity = _open_long(
                    curr_equity, entry_eq, sl, tp, p,
                    sl_p, float("inf"), in_pos, side,
                )
                tp[0] = tp_p  # Set TP
            # SHORT: price near upper BB AND RSI overbought
            elif r > rsi_overbought and 0.0 <= prox_hi <= bb_proximity:
                sl_p = p + atr_stop * at
                tp_p = p - atr_target * at
                entry_price[0] = p
                curr_equity = _open_short(
                    curr_equity, entry_eq, sl, tp, p,
                    sl_p, 0.0, in_pos, side,
                )
                tp[0] = tp_p  # Set TP

        equity_arr[i] = curr_equity

    if in_pos[0]:
        curr_equity *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
        equity_arr[-1] = curr_equity

    return pd.Series(equity_arr, index=df.index), closed_trades


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    from strategies import compute_metrics
    
    # Load 5m and aggregate to 4hr
    df5m = pd.read_csv("../trading_bot_v2/backtesting/data/BTC-USDC_5m_2023.csv")
    df5m["timestamp"] = pd.to_datetime(df5m["timestamp"])
    df5m.columns = [c.lower() for c in df5m.columns]
    df5m = df5m.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    df5m = df5m.set_index("timestamp").sort_index()

    df4h = df5m.resample("4h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
    df4h = df4h.dropna()
    df4h.index = df4h.index.tz_localize(None)

    print("=== Improved Mean Reversion v2 on 4hr ===")
    print("Testing: rsi=15/85, bb_prox=0.02, atr_stop=2.0, atr_target=3.0, adx_max=25")
    
    eq, trd = run_mean_reversion_v2(
        df4h, 0.04,
        rsi_oversold=15,
        rsi_overbought=85,
        bb_proximity=0.02,
        atr_stop=2.0,
        atr_target=3.0,
        adx_max=25.0,
    )
    
    if len(trd) >= 3:
        m = compute_metrics(eq, trd)
        wins = [t for t in trd if t > 0]
        losses = [t for t in trd if t <= 0]
        wr = len(wins) / len(trd) * 100
        avg_win = sum(wins)/len(wins)*100 if wins else 0
        avg_loss = sum(losses)/len(losses)*100 if losses else 0
        
        print(f"Trades: {len(trd)}")
        print(f"Win Rate: {wr:.0f}%")
        print(f"Return: {m['total_return_pct']:+.1f}%")
        print(f"Sharpe: {m['sharpe']:.2f}")
        print(f"Max DD: {m['max_dd_pct']:.2f}%")
        print(f"Avg Win: {avg_win:+.2f}%")
        print(f"Avg Loss: {avg_loss:.2f}%")
    else:
        print(f"Not enough trades: {len(trd)}")