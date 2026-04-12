"""
strategies.py — Walk-Forward Backtest Engine
=============================================
Shared indicators, 6 strategy backtest functions, parameter grids,
walk-forward helpers, and metrics.  No Streamlit imports.

Cost model: 0.10% exchange fee + 0.05% slippage = 0.15% per side (0.30% round-trip).
ATR exits: SL checked before TP on the same bar (conservative). Both use daily high/low
to approximate intraday crossings — standard for daily OHLCV backtesting.

Butterworth: 2-pole forward-only via scipy.signal.lfilter (NOT filtfilt).
lfilter is causal — no lookahead bias.
"""

from __future__ import annotations

import math
from datetime import date
from itertools import product
from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import butter, lfilter

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
COST_PER_SIDE = 0.0015       # 0.10% fee + 0.05% slippage
TRAIN_MONTHS  = 12
TEST_MONTHS   = 3


# ─────────────────────────────────────────────────────────────────────────────
# BUTTERWORTH FILTER  (causal, forward-only)
# ─────────────────────────────────────────────────────────────────────────────
def apply_butterworth(close: pd.Series, cutoff: float = 0.10) -> pd.Series:
    """
    2-pole causal Butterworth low-pass filter applied to close prices.

    Uses lfilter (forward-only, one-pass).  NEVER filtfilt — that is
    non-causal and introduces look-ahead bias into backtests.

    Parameters
    ----------
    close  : raw daily close price series
    cutoff : normalised frequency in (0, 1).  1.0 = Nyquist.
             0.10 ≈ 10-bar effective smoothing on daily data.

    Returns
    -------
    Filtered series with identical DatetimeIndex.
    """
    clean = close.dropna()
    b, a  = butter(2, cutoff, btype="low")
    filt  = lfilter(b, a, clean.values.astype(float))
    return pd.Series(filt, index=clean.index, name=close.name)


# ─────────────────────────────────────────────────────────────────────────────
# SHARED INDICATORS  (all operate on pd.Series with DatetimeIndex)
# ─────────────────────────────────────────────────────────────────────────────

def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI via exponential moving average (alpha = 1/period)."""
    delta    = close.diff()
    gain     = delta.clip(lower=0)
    loss     = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs       = avg_gain / (avg_loss + 1e-10)
    return 100.0 - 100.0 / (1.0 + rs)


def compute_sma(close: pd.Series, period: int = 20) -> pd.Series:
    return close.rolling(period).mean()


def compute_ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int = 14) -> pd.Series:
    """Wilder ATR from true range."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def compute_bollinger(close: pd.Series, period: int = 20,
                      std_dev: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, mid, lower) Bollinger Bands."""
    mid   = close.rolling(period).mean()
    sigma = close.rolling(period).std(ddof=0)
    return mid + std_dev * sigma, mid, mid - std_dev * sigma


def compute_macd(close: pd.Series, fast: int = 12, slow: int = 26,
                 signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram)."""
    ema_f    = close.ewm(span=fast,   adjust=False).mean()
    ema_s    = close.ewm(span=slow,   adjust=False).mean()
    macd     = ema_f - ema_s
    sig_line = macd.ewm(span=signal,  adjust=False).mean()
    return macd, sig_line, macd - sig_line


def compute_adx(high: pd.Series, low: pd.Series, close: pd.Series,
                period: int = 14) -> pd.Series:
    """Wilder ADX."""
    prev_high  = high.shift(1)
    prev_low   = low.shift(1)
    prev_close = close.shift(1)

    tr   = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    plus_dm  = np.where((high - prev_high) > (prev_low - low),
                        np.maximum(high - prev_high, 0), 0.0)
    minus_dm = np.where((prev_low - low) > (high - prev_high),
                        np.maximum(prev_low - low, 0), 0.0)

    plus_dm_s  = pd.Series(plus_dm,  index=high.index)
    minus_dm_s = pd.Series(minus_dm, index=high.index)

    atr_s    = tr.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    plus_di  = 100 * plus_dm_s.ewm(alpha=1.0/period, min_periods=period,
                                    adjust=False).mean() / (atr_s + 1e-10)
    minus_di = 100 * minus_dm_s.ewm(alpha=1.0/period, min_periods=period,
                                     adjust=False).mean() / (atr_s + 1e-10)

    dx  = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    adx = dx.ewm(alpha=1.0/period, min_periods=period, adjust=False).mean()
    return adx


def compute_vwap_rolling(high: pd.Series, low: pd.Series, close: pd.Series,
                          volume: pd.Series, period: int = 20) -> tuple[pd.Series, pd.Series]:
    """
    Rolling VWAP and its rolling standard deviation.
    Returns (vwap, vwap_std).
    """
    tp    = (high + low + close) / 3.0
    vwap  = (tp * volume).rolling(period).sum() / (volume.rolling(period).sum() + 1e-10)
    var   = ((tp - vwap) ** 2 * volume).rolling(period).sum() / (
             volume.rolling(period).sum() + 1e-10)
    return vwap, np.sqrt(var)


# ─────────────────────────────────────────────────────────────────────────────
# SHARED LOOP HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _open_long(curr_equity: float, entry_equity_ref: list, sl_ref: list,
               tp_ref: list, price: float, sl_price: float, tp_price: float,
               in_pos: list, side: list) -> float:
    """Deduct entry cost and set position metadata. Returns updated equity."""
    curr_equity *= (1.0 - COST_PER_SIDE)
    entry_equity_ref[0] = curr_equity
    sl_ref[0]  = sl_price
    tp_ref[0]  = tp_price
    in_pos[0]  = True
    side[0]    = "long"
    return curr_equity


def _open_short(curr_equity: float, entry_equity_ref: list, sl_ref: list,
                tp_ref: list, price: float, sl_price: float, tp_price: float,
                in_pos: list, side: list) -> float:
    curr_equity *= (1.0 - COST_PER_SIDE)
    entry_equity_ref[0] = curr_equity
    sl_ref[0]  = sl_price
    tp_ref[0]  = tp_price
    in_pos[0]  = True
    side[0]    = "short"
    return curr_equity


def _check_exit(curr_equity: float, entry_equity: float, high: float,
                low: float, sl: float, tp: float, side: str,
                closed_trades: list, in_pos: list) -> float:
    """
    Check ATR-based SL / TP using daily high and low.
    SL checked first (conservative). Deducts exit cost.
    Returns updated equity.
    """
    hit = False
    if side == "long":
        if low <= sl:
            hit = True
        elif high >= tp:
            hit = True
    else:  # short
        if high >= sl:
            hit = True
        elif low <= tp:
            hit = True

    if hit:
        curr_equity *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_equity - 1.0)
        in_pos[0] = False

    return curr_equity


# ─────────────────────────────────────────────────────────────────────────────
# 1. MEAN REVERSION
# ─────────────────────────────────────────────────────────────────────────────
def run_mean_reversion(
    df: pd.DataFrame,
    cutoff: float,
    rsi_oversold: float  = 30.0,
    rsi_overbought: float= 70.0,
    bb_proximity: float  = 0.10,
    atr_stop: float      = 3.0,
) -> tuple[pd.Series, list]:
    """
    RSI + Bollinger Bands mean-reversion.

    Entry LONG  : RSI < oversold  AND price within bb_proximity% of lower BB
    Entry SHORT : RSI > overbought AND price within bb_proximity% of upper BB
    Exit LONG   : close >= SMA-20 (TP at mean) OR daily low  <= entry - atr_stop*ATR
    Exit SHORT  : close <= SMA-20 (TP at mean) OR daily high >= entry + atr_stop*ATR
    """
    fc    = apply_butterworth(df["Close"], cutoff)
    rsi   = compute_rsi(fc, 14)
    bb_up, bb_mid, bb_lo = compute_bollinger(fc, 20, 2.0)
    sma20 = compute_sma(fc, 20)
    atr   = compute_atr(df["High"], df["Low"], df["Close"], 14)

    prices = df["Close"].values.astype(float)
    highs  = df["High"].values.astype(float)
    lows   = df["Low"].values.astype(float)
    n = len(prices)

    equity_arr   = np.ones(n, dtype=float)
    curr_equity  = 1.0
    in_pos       = [False]
    side         = [""]
    entry_eq     = [1.0]
    sl           = [0.0]
    tp           = [0.0]
    closed_trades: list[float] = []

    for i in range(1, n):
        r   = rsi.iloc[i]
        bbu = bb_up.iloc[i]
        bbl = bb_lo.iloc[i]
        sm  = sma20.iloc[i]
        at  = atr.iloc[i]
        p   = prices[i]
        p0  = prices[i - 1]

        if any(np.isnan(x) for x in (r, bbu, bbl, sm, at)):
            equity_arr[i] = curr_equity
            continue

        if in_pos[0]:
            curr_equity *= p / p0
            curr_equity = _check_exit(
                curr_equity, entry_eq[0], highs[i], lows[i],
                sl[0], tp[0], side[0], closed_trades, in_pos,
            )
            # SMA-based TP (mean reversion target)
            if in_pos[0]:
                if side[0] == "long"  and p >= sm:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif side[0] == "short" and p <= sm:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
        else:
            prox_lo = (p - bbl) / (bbl + 1e-10)
            prox_hi = (bbu - p) / (bbu + 1e-10)
            # LONG: price near lower BB AND RSI oversold
            if r < rsi_oversold and 0.0 <= prox_lo <= bb_proximity:
                curr_equity = _open_long(
                    curr_equity, entry_eq, sl, tp, p,
                    p - atr_stop * at, float("inf"), in_pos, side,
                )
            # SHORT: price near upper BB AND RSI overbought
            elif r > rsi_overbought and 0.0 <= prox_hi <= bb_proximity:
                curr_equity = _open_short(
                    curr_equity, entry_eq, sl, tp, p,
                    p + atr_stop * at, 0.0, in_pos, side,
                )

        equity_arr[i] = curr_equity

    if in_pos[0]:
        curr_equity   *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
        equity_arr[-1]  = curr_equity

    return pd.Series(equity_arr, index=df.index), closed_trades


# ─────────────────────────────────────────────────────────────────────────────
# 2. VWAP SCALPING
# ─────────────────────────────────────────────────────────────────────────────
def run_vwap_scalping(
    df: pd.DataFrame,
    cutoff: float,
    sd_threshold: float = 3.0,
    atr_stop: float     = 7.0,
) -> tuple[pd.Series, list]:
    """
    Rolling-VWAP deviation + MACD confirmation.

    Entry LONG  : close < VWAP - sd_threshold*σ  AND  MACD hist < 0
    Entry SHORT : close > VWAP + sd_threshold*σ  AND  MACD hist > 0
    Exit LONG   : close >= VWAP (TP at mean) OR daily low  <= entry - atr_stop*ATR
    Exit SHORT  : close <= VWAP (TP at mean) OR daily high >= entry + atr_stop*ATR
    """
    fc       = apply_butterworth(df["Close"], cutoff)
    vwap, vs = compute_vwap_rolling(df["High"], df["Low"], fc, df["Volume"], 20)
    _, _, mh = compute_macd(fc, 12, 26, 9)
    atr      = compute_atr(df["High"], df["Low"], df["Close"], 14)

    prices = df["Close"].values.astype(float)
    highs  = df["High"].values.astype(float)
    lows   = df["Low"].values.astype(float)
    n      = len(prices)

    equity_arr   = np.ones(n, dtype=float)
    curr_equity  = 1.0
    in_pos       = [False]
    side         = [""]
    entry_eq     = [1.0]
    sl           = [0.0]
    tp_ref       = [0.0]
    closed_trades: list[float] = []

    for i in range(1, n):
        vw = vwap.iloc[i];  vsd = vs.iloc[i]
        mhi= mh.iloc[i];    at  = atr.iloc[i]
        p  = prices[i];     p0  = prices[i - 1]

        if any(np.isnan(x) for x in (vw, vsd, mhi, at)):
            equity_arr[i] = curr_equity
            continue

        upper_band = vw + sd_threshold * vsd
        lower_band = vw - sd_threshold * vsd

        if in_pos[0]:
            curr_equity *= p / p0
            curr_equity = _check_exit(
                curr_equity, entry_eq[0], highs[i], lows[i],
                sl[0], tp_ref[0], side[0], closed_trades, in_pos,
            )
            if in_pos[0]:
                if side[0] == "long"  and p >= vw:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif side[0] == "short" and p <= vw:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
        else:
            if p < lower_band and mhi < 0:
                curr_equity = _open_long(
                    curr_equity, entry_eq, sl, tp_ref, p,
                    p - atr_stop * at, float("inf"), in_pos, side,
                )
            elif p > upper_band and mhi > 0:
                curr_equity = _open_short(
                    curr_equity, entry_eq, sl, tp_ref, p,
                    p + atr_stop * at, 0.0, in_pos, side,
                )

        equity_arr[i] = curr_equity

    if in_pos[0]:
        curr_equity   *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
        equity_arr[-1]  = curr_equity

    return pd.Series(equity_arr, index=df.index), closed_trades


# ─────────────────────────────────────────────────────────────────────────────
# 3. MOMENTUM SCALPING
# ─────────────────────────────────────────────────────────────────────────────
def run_momentum_scalping(
    df: pd.DataFrame,
    cutoff: float,
    ema_fast: int    = 9,
    ema_slow: int    = 21,
    atr_stop: float  = 2.0,
    atr_target: float= 3.0,
) -> tuple[pd.Series, list]:
    """
    EMA crossover + MACD + RSI + Volume gate.

    Entry LONG  : EMA_fast crosses above EMA_slow AND close > both EMAs
                  AND RSI 45–65 AND MACD_hist > 0 & rising AND vol >= 1.2×avg
    Entry SHORT : EMA_fast crosses below EMA_slow AND close < both EMAs
                  AND RSI 35–55 AND MACD_hist < 0 & falling AND vol >= 1.2×avg
    Exit        : TP = entry ± atr_target*ATR  |  SL = entry ∓ atr_stop*ATR
    """
    fc      = apply_butterworth(df["Close"], cutoff)
    ema_f   = compute_ema(fc, ema_fast)
    ema_s   = compute_ema(fc, ema_slow)
    rsi     = compute_rsi(fc, 14)
    _, _, mh= compute_macd(fc, 12, 26, 9)
    atr     = compute_atr(df["High"], df["Low"], df["Close"], 14)
    vol_avg = df["Volume"].rolling(20).mean()

    prices = df["Close"].values.astype(float)
    highs  = df["High"].values.astype(float)
    lows   = df["Low"].values.astype(float)
    vols   = df["Volume"].values.astype(float)
    n      = len(prices)

    equity_arr   = np.ones(n, dtype=float)
    curr_equity  = 1.0
    in_pos       = [False]
    side         = [""]
    entry_eq     = [1.0]
    sl           = [0.0]
    tp_ref       = [0.0]
    closed_trades: list[float] = []

    for i in range(1, n):
        ef   = ema_f.iloc[i];  ef0  = ema_f.iloc[i - 1]
        es   = ema_s.iloc[i];  es0  = ema_s.iloc[i - 1]
        r    = rsi.iloc[i]
        mhi  = mh.iloc[i];     mhi0 = mh.iloc[i - 1]
        at   = atr.iloc[i]
        p    = prices[i];      p0   = prices[i - 1]
        va   = vol_avg.iloc[i]
        v    = vols[i]

        if any(np.isnan(x) for x in (ef, es, r, mhi, at, va)):
            equity_arr[i] = curr_equity
            continue

        if in_pos[0]:
            curr_equity *= p / p0
            curr_equity = _check_exit(
                curr_equity, entry_eq[0], highs[i], lows[i],
                sl[0], tp_ref[0], side[0], closed_trades, in_pos,
            )
        else:
            vol_ok    = v >= 1.2 * va
            cross_up  = ef0 < es0 and ef > es   # bullish crossover
            cross_dn  = ef0 > es0 and ef < es   # bearish crossover
            if cross_up and p > ef and p > es and 45 <= r <= 65 and mhi > 0 and mhi > mhi0 and vol_ok:
                sl_p = p - atr_stop  * at
                tp_p = p + atr_target * at
                curr_equity = _open_long(curr_equity, entry_eq, sl, tp_ref, p, sl_p, tp_p, in_pos, side)
            elif cross_dn and p < ef and p < es and 35 <= r <= 55 and mhi < 0 and mhi < mhi0 and vol_ok:
                sl_p = p + atr_stop  * at
                tp_p = p - atr_target * at
                curr_equity = _open_short(curr_equity, entry_eq, sl, tp_ref, p, sl_p, tp_p, in_pos, side)

        equity_arr[i] = curr_equity

    if in_pos[0]:
        curr_equity   *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
        equity_arr[-1]  = curr_equity

    return pd.Series(equity_arr, index=df.index), closed_trades


# ─────────────────────────────────────────────────────────────────────────────
# 4. LIQUIDATION CAPTURE
# ─────────────────────────────────────────────────────────────────────────────
def run_liquidation_capture(
    df: pd.DataFrame,
    cutoff: float,
    price_threshold: float = 0.030,
    volume_mult: float     = 3.0,
    rsi_threshold: float   = 18.0,
) -> tuple[pd.Series, list]:
    """
    Fades post-liquidation cascades on daily bars (fires ~5-15× over 2018-2026).

    Entry LONG  : 5-bar price drop >= price_threshold AND volume spike >= volume_mult×avg
                  AND RSI <= rsi_threshold AND >= 4 consecutive down closes
                  AND lower wick ratio >= 1.5 (panic selling)
    Entry SHORT : mirror for upward squeeze
    Exit        : TP = 3× risk (3:1 RRR)  |  SL = 1% beyond 5-bar extreme
    """
    fc      = apply_butterworth(df["Close"], cutoff)
    rsi     = compute_rsi(fc, 14)
    vol_avg = df["Volume"].rolling(20).mean()

    closes  = df["Close"].values.astype(float)
    opens   = df["Open"].values.astype(float)
    highs   = df["High"].values.astype(float)
    lows    = df["Low"].values.astype(float)
    vols    = df["Volume"].values.astype(float)
    n       = len(closes)

    equity_arr   = np.ones(n, dtype=float)
    curr_equity  = 1.0
    in_pos       = [False]
    side         = [""]
    entry_eq     = [1.0]
    sl           = [0.0]
    tp_ref       = [0.0]
    closed_trades: list[float] = []

    MIN_CONSECUTIVE = 4
    MIN_WICK_RATIO  = 1.5

    for i in range(5, n):
        r   = rsi.iloc[i]
        va  = vol_avg.iloc[i]
        p   = closes[i]
        p0  = closes[i - 1]

        if np.isnan(r) or np.isnan(va):
            equity_arr[i] = curr_equity
            continue

        if in_pos[0]:
            curr_equity *= p / p0
            curr_equity = _check_exit(
                curr_equity, entry_eq[0], highs[i], lows[i],
                sl[0], tp_ref[0], side[0], closed_trades, in_pos,
            )
        else:
            # Consecutive down candles
            consec_dn = sum(1 for j in range(i - MIN_CONSECUTIVE + 1, i + 1)
                            if closes[j] < closes[j - 1])
            consec_up = sum(1 for j in range(i - MIN_CONSECUTIVE + 1, i + 1)
                            if closes[j] > closes[j - 1])
            # 5-bar price move
            p5_ago     = closes[i - 5]
            move_down  = (p5_ago - p) / (p5_ago + 1e-10)
            move_up    = (p - p5_ago) / (p5_ago + 1e-10)
            # Wick ratios on current bar
            body   = abs(closes[i] - opens[i]) + 1e-10
            lo_wick = (opens[i] - lows[i])  if closes[i] >= opens[i] else (closes[i] - lows[i])
            hi_wick = (highs[i] - opens[i]) if closes[i] >= opens[i] else (highs[i] - closes[i])
            wick_dn = max(lo_wick, 0) / body
            wick_up = max(hi_wick, 0) / body

            vol_ok = vols[i] >= volume_mult * va

            # Pre-compute recent extremes for both entry directions
            recent_low  = min(lows[i - 4 : i + 1])
            recent_high = max(highs[i - 4 : i + 1])

            # LONG: downward cascade exhausted
            if (move_down >= price_threshold and vol_ok and r <= rsi_threshold
                    and consec_dn >= MIN_CONSECUTIVE and wick_dn >= MIN_WICK_RATIO):
                sl_p   = recent_low * (1.0 - 0.01)
                risk   = p - sl_p
                tp_p   = p + 3.0 * risk
                curr_equity = _open_long(curr_equity, entry_eq, sl, tp_ref, p,
                                          sl_p, tp_p, in_pos, side)

            # SHORT: upward squeeze exhausted
            elif (move_up >= price_threshold and vol_ok and r >= (100 - rsi_threshold)
                    and consec_up >= MIN_CONSECUTIVE and wick_up >= MIN_WICK_RATIO):
                sl_p   = recent_high * (1.0 + 0.01)
                risk   = sl_p - p
                tp_p   = p - 3.0 * risk
                curr_equity = _open_short(curr_equity, entry_eq, sl, tp_ref, p,
                                           sl_p, tp_p, in_pos, side)

        equity_arr[i] = curr_equity

    if in_pos[0]:
        curr_equity   *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
        equity_arr[-1]  = curr_equity

    return pd.Series(equity_arr, index=df.index), closed_trades


# ─────────────────────────────────────────────────────────────────────────────
# 5. GRID TRADING  (range-fade simplification for single-position daily backtest)
# ─────────────────────────────────────────────────────────────────────────────
def run_grid_trading(
    df: pd.DataFrame,
    cutoff: float,
    adx_threshold: float  = 20.0,
    spacing_mult: float   = 0.65,
) -> tuple[pd.Series, list]:
    """
    Simplified grid-trade: range-fade when ADX confirms a ranging regime.
    Real grids are multi-position; this single-position approximation captures
    the core edge (buy near support, sell near resistance in low-ADX periods).

    Entry LONG  : ADX < adx_threshold AND close <= 20-bar low + spacing_mult*ATR
    Entry SHORT : ADX < adx_threshold AND close >= 20-bar high - spacing_mult*ATR
    Exit LONG   : close >= 20-bar midpoint (TP) OR SL = 2*ATR below entry
    Exit SHORT  : close <= 20-bar midpoint (TP) OR SL = 2*ATR above entry
    """
    fc    = apply_butterworth(df["Close"], cutoff)
    adx   = compute_adx(df["High"], df["Low"], df["Close"], 14)
    atr   = compute_atr(df["High"], df["Low"], df["Close"], 14)
    hi20  = df["High"].rolling(20).max()
    lo20  = df["Low"].rolling(20).min()

    prices = df["Close"].values.astype(float)
    highs  = df["High"].values.astype(float)
    lows   = df["Low"].values.astype(float)
    n      = len(prices)

    equity_arr   = np.ones(n, dtype=float)
    curr_equity  = 1.0
    in_pos       = [False]
    side         = [""]
    entry_eq     = [1.0]
    sl           = [0.0]
    tp_ref       = [0.0]
    closed_trades: list[float] = []

    for i in range(1, n):
        adx_v = adx.iloc[i];  at   = atr.iloc[i]
        hi    = hi20.iloc[i]; lo   = lo20.iloc[i]
        mid   = (hi + lo) / 2.0
        p     = prices[i];    p0   = prices[i - 1]

        if any(np.isnan(x) for x in (adx_v, at, hi, lo)):
            equity_arr[i] = curr_equity
            continue

        lower_grid = lo + spacing_mult * at
        upper_grid = hi - spacing_mult * at

        if in_pos[0]:
            curr_equity *= p / p0
            curr_equity = _check_exit(
                curr_equity, entry_eq[0], highs[i], lows[i],
                sl[0], tp_ref[0], side[0], closed_trades, in_pos,
            )
            if in_pos[0]:
                if side[0] == "long"  and p >= mid:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
                elif side[0] == "short" and p <= mid:
                    curr_equity *= (1.0 - COST_PER_SIDE)
                    closed_trades.append(curr_equity / entry_eq[0] - 1.0)
                    in_pos[0] = False
        else:
            if adx_v < adx_threshold:
                if p <= lower_grid:
                    sl_p = p - 2.0 * at
                    curr_equity = _open_long(curr_equity, entry_eq, sl, tp_ref, p,
                                              sl_p, float("inf"), in_pos, side)
                elif p >= upper_grid:
                    sl_p = p + 2.0 * at
                    curr_equity = _open_short(curr_equity, entry_eq, sl, tp_ref, p,
                                               sl_p, 0.0, in_pos, side)

        equity_arr[i] = curr_equity

    if in_pos[0]:
        curr_equity   *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
        equity_arr[-1]  = curr_equity

    return pd.Series(equity_arr, index=df.index), closed_trades


# ─────────────────────────────────────────────────────────────────────────────
# 6. MA CROSSOVER
# ─────────────────────────────────────────────────────────────────────────────
def run_ma_crossover(
    df: pd.DataFrame,
    cutoff: float,
    fast_period: int    = 20,
    slow_period: int    = 50,
    pullback_max: float = 0.06,
) -> tuple[pd.Series, list]:
    """
    Golden/death cross with pullback entry + MACD + Volume confirmation.

    Entry LONG  : SMA_fast crossed above SMA_slow within last 5 bars
                  AND price is 1%–pullback_max below SMA_fast (pullback)
                  AND MACD_line > signal AND volume >= 1.2× avg
    Entry SHORT : Death cross + rally back 1%–pullback_max above SMA_fast
    Exit        : TP = entry + 2×risk  |  SL = entry - 2.5×ATR
    """
    fc      = apply_butterworth(df["Close"], cutoff)
    sma_f   = compute_sma(fc, fast_period)
    sma_s   = compute_sma(fc, slow_period)
    ml, sl_line, _ = compute_macd(fc, 12, 26, 9)
    atr     = compute_atr(df["High"], df["Low"], df["Close"], 14)
    vol_avg = df["Volume"].rolling(20).mean()

    prices = df["Close"].values.astype(float)
    highs  = df["High"].values.astype(float)
    lows   = df["Low"].values.astype(float)
    vols   = df["Volume"].values.astype(float)
    n      = len(prices)

    equity_arr   = np.ones(n, dtype=float)
    curr_equity  = 1.0
    in_pos       = [False]
    side_ref     = [""]
    entry_eq     = [1.0]
    sl           = [0.0]
    tp_ref       = [0.0]
    closed_trades: list[float] = []

    PULLBACK_MIN = 0.01
    last_golden_bar = -999
    last_death_bar  = -999

    for i in range(1, n):
        sf  = sma_f.iloc[i];  sf0 = sma_f.iloc[i - 1]
        ss  = sma_s.iloc[i];  ss0 = sma_s.iloc[i - 1]
        mlv = ml.iloc[i];     slv = sl_line.iloc[i]
        at  = atr.iloc[i];    va  = vol_avg.iloc[i]
        p   = prices[i];      p0  = prices[i - 1]
        v   = vols[i]

        if any(np.isnan(x) for x in (sf, ss, mlv, slv, at, va)):
            equity_arr[i] = curr_equity
            continue

        # Detect crossovers
        if sf0 < ss0 and sf >= ss:
            last_golden_bar = i
        if sf0 > ss0 and sf <= ss:
            last_death_bar = i

        if in_pos[0]:
            curr_equity *= p / p0
            curr_equity = _check_exit(
                curr_equity, entry_eq[0], highs[i], lows[i],
                sl[0], tp_ref[0], side_ref[0], closed_trades, in_pos,
            )
        else:
            vol_ok = v >= 1.2 * va
            # LONG: within 5 bars of golden cross, price pulled back below SMA_fast
            if 0 < (i - last_golden_bar) <= 5 and vol_ok and mlv > slv:
                pb_pct = (sf - p) / (sf + 1e-10)
                if PULLBACK_MIN <= pb_pct <= pullback_max:
                    sl_p  = p - 2.5 * at
                    risk  = p - sl_p
                    tp_p  = p + 2.0 * risk
                    curr_equity = _open_long(curr_equity, entry_eq, sl, tp_ref, p, sl_p, tp_p, in_pos, side_ref)

            # SHORT: within 5 bars of death cross, price rallied above SMA_fast
            elif 0 < (i - last_death_bar) <= 5 and vol_ok and mlv < slv:
                pb_pct = (p - sf) / (sf + 1e-10)
                if PULLBACK_MIN <= pb_pct <= pullback_max:
                    sl_p  = p + 2.5 * at
                    risk  = sl_p - p
                    tp_p  = p - 2.0 * risk
                    curr_equity = _open_short(curr_equity, entry_eq, sl, tp_ref, p, sl_p, tp_p, in_pos, side_ref)

        equity_arr[i] = curr_equity

    if in_pos[0]:
        curr_equity   *= (1.0 - COST_PER_SIDE)
        closed_trades.append(curr_equity / entry_eq[0] - 1.0)
        equity_arr[-1]  = curr_equity

    return pd.Series(equity_arr, index=df.index), closed_trades


# ─────────────────────────────────────────────────────────────────────────────
# PARAMETER GRIDS  (per strategy — used both for walk-forward and agent sweep)
# ─────────────────────────────────────────────────────────────────────────────

MR_GRID     = {"rsi_oversold": [25, 30, 35], "rsi_overbought": [65, 70, 75],
               "bb_proximity": [0.05, 0.10, 0.20]}          # 27 combos
MR_DEFAULTS = {"rsi_oversold": 30.0, "rsi_overbought": 70.0,
               "bb_proximity": 0.10, "atr_stop": 3.0}

VS_GRID     = {"sd_threshold": [1.5, 2.0, 2.5, 3.0], "atr_stop": [1.5, 2.0, 3.0]}  # 12
VS_DEFAULTS = {"sd_threshold": 3.0, "atr_stop": 7.0}

MS_GRID     = {"ema_fast": [9, 12], "ema_slow": [21, 26],
               "atr_stop": [1.5, 2.0], "atr_target": [2.5, 3.0]}  # 16
MS_DEFAULTS = {"ema_fast": 9, "ema_slow": 21, "atr_stop": 2.0, "atr_target": 3.0}

LC_GRID     = {"price_threshold": [0.020, 0.025, 0.030],
               "volume_mult":     [2.0,   2.5,   3.0],
               "rsi_threshold":   [15.0,  18.0,  22.0]}     # 27
LC_DEFAULTS = {"price_threshold": 0.030, "volume_mult": 3.0, "rsi_threshold": 18.0}

GT_GRID     = {"adx_threshold": [15.0, 20.0, 25.0],
               "spacing_mult":  [0.30,  0.50, 0.65, 0.80]}  # 12
GT_DEFAULTS = {"adx_threshold": 20.0, "spacing_mult": 0.65}

MA_GRID     = {"fast_period": [15, 20, 25], "slow_period": [40, 50, 60],
               "pullback_max": [0.04, 0.06, 0.08]}           # 27
MA_DEFAULTS = {"fast_period": 20, "slow_period": 50, "pullback_max": 0.06}


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY REGISTRY
# ─────────────────────────────────────────────────────────────────────────────
STRATEGY_REGISTRY: dict[str, tuple] = {
    "Mean Reversion":      (run_mean_reversion,      MR_GRID, MR_DEFAULTS),
    "VWAP Scalping":       (run_vwap_scalping,       VS_GRID, VS_DEFAULTS),
    "Momentum Scalping":   (run_momentum_scalping,   MS_GRID, MS_DEFAULTS),
    "Liquidation Capture": (run_liquidation_capture, LC_GRID, LC_DEFAULTS),
    "Grid Trading":        (run_grid_trading,        GT_GRID, GT_DEFAULTS),
    "MA Crossover":        (run_ma_crossover,        MA_GRID, MA_DEFAULTS),
}

STRATEGY_NOTES: dict[str, str] = {
    "Mean Reversion":      "RSI + Bollinger Bands · Long near lower BB, short near upper BB · Exit at SMA-20.",
    "VWAP Scalping":       "Rolling-VWAP deviation + MACD · Fades extreme SD moves back to VWAP mean.",
    "Momentum Scalping":   "EMA 9/21 crossover + MACD + Volume · Rides momentum with ATR-based TP/SL.",
    "Liquidation Capture": "Cascade exhaustion detector · Fades liquidation waterfalls (fires rarely on daily bars).",
    "Grid Trading":        "Range-fade when ADX < threshold · Buy near range bottom, sell near range top.",
    "MA Crossover":        "SMA golden/death cross + pullback entry + MACD confirmation · Trend-following.",
}


# ─────────────────────────────────────────────────────────────────────────────
# GENERIC OPTIMIZER
# ─────────────────────────────────────────────────────────────────────────────
def optimize_strategy(df_train: pd.DataFrame, cutoff: float,
                      strategy_name: str) -> dict:
    """
    Exhaustive grid search over a strategy's PARAM_GRID on the training window.
    Objective: maximise Sharpe ratio.  Requires >= 3 closed trades.
    Falls back to DEFAULT_PARAMS if no qualifying combination is found.
    """
    func, grid, defaults = STRATEGY_REGISTRY[strategy_name]
    keys  = list(grid.keys())
    vals  = list(grid.values())

    best_sharpe = -math.inf
    best_params = defaults.copy()

    for combo in product(*vals):
        params = dict(zip(keys, combo))
        # Fill in any fixed params not in the grid (e.g. atr_stop in MR)
        full_params = {**defaults, **params}
        try:
            eq, trd = func(df_train, cutoff, **full_params)
            if len(trd) < 3:
                continue
            m = compute_metrics(eq, trd)
            if m["sharpe"] > best_sharpe:
                best_sharpe = m["sharpe"]
                best_params = full_params
        except Exception:
            pass

    return best_params


# ─────────────────────────────────────────────────────────────────────────────
# WALK-FORWARD HELPERS  (shared between app.py and agent.py)
# ─────────────────────────────────────────────────────────────────────────────

def build_windows(start: date, end: date,
                  train_months: int = TRAIN_MONTHS,
                  test_months:  int = TEST_MONTHS) -> list[dict]:
    """Build rolling train/test fold schedule."""
    windows = []
    fold    = 1
    t_start = pd.Timestamp(start)
    t_end   = pd.Timestamp(end)

    while True:
        train_end  = t_start + pd.DateOffset(months=train_months) - pd.Timedelta(days=1)
        test_start = train_end + pd.Timedelta(days=1)
        test_end   = test_start + pd.DateOffset(months=test_months) - pd.Timedelta(days=1)

        if test_start > t_end:
            break

        windows.append(dict(
            fold       =fold,
            train_start=t_start.date(),
            train_end  =train_end.date(),
            test_start =test_start.date(),
            test_end   =min(test_end, t_end).date(),
        ))
        t_start = t_start + pd.DateOffset(months=test_months)
        fold   += 1

    return windows


def stitch_oos_equity(segments: list[pd.Series]) -> pd.Series:
    """Chain test-window equity segments into one continuous curve."""
    if not segments:
        return pd.Series(dtype=float)

    stitched: list[pd.Series] = []
    last_val = 1.0

    for seg in segments:
        if len(seg) == 0:
            continue
        scale  = last_val / float(seg.iloc[0])
        scaled = seg * scale
        stitched.append(scaled)
        last_val = float(scaled.iloc[-1])

    return pd.concat(stitched) if stitched else pd.Series(dtype=float)


# ─────────────────────────────────────────────────────────────────────────────
# METRICS
# ─────────────────────────────────────────────────────────────────────────────

def compute_metrics(equity: pd.Series, trades: list[float]) -> dict:
    """Return dict of 7 performance statistics."""
    if len(equity) < 2:
        return dict(total_return_pct=0.0, cagr_pct=0.0, sharpe=0.0,
                    max_dd_pct=0.0, win_rate_pct=0.0, profit_factor=0.0, n_trades=0)

    years     = max((equity.index[-1] - equity.index[0]).days / 365.25, 0.01)
    final_val = float(equity.iloc[-1])
    start_val = float(equity.iloc[0])

    total_ret = (final_val / start_val - 1.0) * 100.0
    cagr      = ((final_val / start_val) ** (1.0 / years) - 1.0) * 100.0

    daily_rets = equity.pct_change().dropna()
    sharpe = (
        daily_rets.mean() / (daily_rets.std() + 1e-10) * math.sqrt(252)
        if len(daily_rets) > 1 else 0.0
    )

    running_max = equity.cummax()
    max_dd      = float(((equity - running_max) / running_max).min() * 100.0)

    wins         = [t for t in trades if t > 0]
    losses       = [t for t in trades if t <= 0]
    win_rate     = len(wins) / max(len(trades), 1) * 100.0
    gross_profit = sum(wins)
    gross_loss   = abs(sum(losses))
    pf = (gross_profit / gross_loss if gross_loss > 0
          else (float("inf") if wins else 0.0))

    return dict(
        total_return_pct=total_ret,
        cagr_pct        =cagr,
        sharpe          =sharpe,
        max_dd_pct      =max_dd,
        win_rate_pct    =win_rate,
        profit_factor   =pf,
        n_trades        =len(trades),
    )
