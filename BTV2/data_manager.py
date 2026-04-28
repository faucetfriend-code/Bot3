"""
data_manager.py — Local Parquet Cache + Binance Downloader
===========================================================
Downloads OHLCV candles from Binance's free public API (no key required)
and caches them as Parquet files on G:\\Candle Data\\.

Subsequent runs load from disk — no internet after first download.
Falls back to yfinance for non-crypto or unmapped tickers.

Storage layout:
    G:\\Candle Data\\
        BTCUSDT_1m.parquet
        BTCUSDT_5m.parquet
        BTCUSDT_15m.parquet
        BTCUSDT_1h.parquet
        BTCUSDT_4h.parquet
        BTCUSDT_1d.parquet
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import requests
import yfinance as yf

# ─────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────
STORAGE_ROOT = Path("G:/Candle Data")

BINANCE_KLINES_URL  = "https://api.binance.com/api/v3/klines"
BINANCE_BATCH_SIZE  = 1000          # max bars per Binance request
BINANCE_SLEEP_SEC   = 0.12          # polite rate-limit pause between batches

SUPPORTED_INTERVALS = ["1m", "5m", "15m", "1h", "4h", "1d"]

# Map yfinance ticker names → Binance symbol
TICKER_MAP: dict[str, str] = {
    "BTC-USD": "BTCUSDT",
    "ETH-USD": "ETHUSDT",
    "SOL-USD": "SOLUSDT",
    "BNB-USD": "BNBUSDT",
    "XRP-USD": "XRPUSDT",
    "ADA-USD": "ADAUSDT",
    "DOGE-USD": "DOGEUSDT",
    "AVAX-USD": "AVAXUSDT",
    "DOT-USD":  "DOTUSDT",
    "LINK-USD": "LINKUSDT",
    "MATIC-USD":"MATICUSDT",
    "LTC-USD":  "LTCUSDT",
}

# Earliest data available on Binance for each symbol (approximate)
BINANCE_GENESIS: dict[str, datetime] = {
    "BTCUSDT":  datetime(2017,  9,  1, tzinfo=timezone.utc),
    "ETHUSDT":  datetime(2017,  9,  1, tzinfo=timezone.utc),
    "BNBUSDT":  datetime(2017, 11,  1, tzinfo=timezone.utc),
    "SOLUSDT":  datetime(2020,  9,  1, tzinfo=timezone.utc),
    "XRPUSDT":  datetime(2018,  5,  1, tzinfo=timezone.utc),
    "ADAUSDT":  datetime(2018,  4,  1, tzinfo=timezone.utc),
    "DOGEUSDT": datetime(2019,  8,  1, tzinfo=timezone.utc),
    "AVAXUSDT": datetime(2020,  9,  1, tzinfo=timezone.utc),
    "DOTUSDT":  datetime(2020,  8,  1, tzinfo=timezone.utc),
    "LINKUSDT": datetime(2017, 12,  1, tzinfo=timezone.utc),
    "MATICUSDT":datetime(2019,  4,  1, tzinfo=timezone.utc),
    "LTCUSDT":  datetime(2017, 12,  1, tzinfo=timezone.utc),
}


# ─────────────────────────────────────────────────────────────────────────────
# STORAGE HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parquet_path(symbol: str, interval: str) -> Path:
    """Return the Parquet file path for a symbol+interval pair."""
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
    return STORAGE_ROOT / f"{symbol}_{interval}.parquet"


def load_local(symbol: str, interval: str) -> Optional[pd.DataFrame]:
    """Load cached Parquet file.  Returns None if file does not exist."""
    path = _parquet_path(symbol, interval)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    except Exception:
        return None


def save_local(df: pd.DataFrame, symbol: str, interval: str) -> None:
    """Save DataFrame to Parquet (zstd compression)."""
    path = _parquet_path(symbol, interval)
    df.to_parquet(path, compression="zstd")


# ─────────────────────────────────────────────────────────────────────────────
# BINANCE DOWNLOAD
# ─────────────────────────────────────────────────────────────────────────────

def _ts_ms(dt: datetime) -> int:
    """Convert datetime → millisecond timestamp (Binance API format)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _fetch_batch(symbol: str, interval: str,
                 start_ms: int, end_ms: int) -> pd.DataFrame:
    """
    Fetch one batch (≤1000 bars) from Binance klines endpoint.
    Returns empty DataFrame on error.
    """
    params = {
        "symbol":    symbol,
        "interval":  interval,
        "startTime": start_ms,
        "endTime":   end_ms,
        "limit":     BINANCE_BATCH_SIZE,
    }
    try:
        resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=20)
        resp.raise_for_status()
        raw = resp.json()
    except Exception:
        return pd.DataFrame()

    if not raw:
        return pd.DataFrame()

    rows = []
    for k in raw:
        ts = pd.Timestamp(k[0], unit="ms", tz="UTC")
        rows.append({
            "Open":   float(k[1]),
            "High":   float(k[2]),
            "Low":    float(k[3]),
            "Close":  float(k[4]),
            "Volume": float(k[5]),
        })
    idx = pd.DatetimeIndex(
        [pd.Timestamp(k[0], unit="ms", tz="UTC") for k in raw],
        name="Datetime",
    )
    return pd.DataFrame(rows, index=idx)


def download_range(
    symbol: str,
    interval: str,
    start: datetime,
    end: datetime,
    progress_cb: Optional[Callable[[datetime, datetime], None]] = None,
) -> pd.DataFrame:
    """
    Download all bars for symbol/interval between start and end.
    Paginates in BINANCE_BATCH_SIZE chunks with polite rate-limiting.
    Calls progress_cb(current_bar_time, end_time) after each batch.

    Returns combined DataFrame with UTC DatetimeIndex.
    """
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    now_ms    = _ts_ms(datetime.now(timezone.utc))
    start_ms  = _ts_ms(start)
    end_ms    = min(_ts_ms(end), now_ms)

    all_frames: list[pd.DataFrame] = []
    cursor_ms = start_ms

    while cursor_ms < end_ms:
        batch = _fetch_batch(symbol, interval, cursor_ms, end_ms)
        if batch.empty:
            break

        all_frames.append(batch)

        # Advance cursor past last bar in this batch
        last_ts_ms = int(batch.index[-1].timestamp() * 1000)
        cursor_ms  = last_ts_ms + 1   # next millisecond

        if progress_cb:
            try:
                progress_cb(batch.index[-1].to_pydatetime().replace(tzinfo=timezone.utc), end)
            except Exception:
                pass

        if len(batch) < BINANCE_BATCH_SIZE:
            break   # reached end of available data

        time.sleep(BINANCE_SLEEP_SEC)

    if not all_frames:
        return pd.DataFrame()

    combined = pd.concat(all_frames)
    combined = combined[~combined.index.duplicated(keep="last")]
    combined.sort_index(inplace=True)
    return combined


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def get_candles(
    symbol: str,
    interval: str,
    start: datetime | str,
    end:   datetime | str,
    progress_cb: Optional[Callable[[datetime, datetime], None]] = None,
) -> pd.DataFrame:
    """
    Main data accessor.  Returns OHLCV DataFrame with UTC DatetimeIndex.

    1. Loads cached Parquet (if any).
    2. Identifies missing ranges (before cache start, after cache end).
    3. Downloads only the missing portions.
    4. Merges, deduplicates, saves back to Parquet.
    5. Returns slice [start : end].

    Parameters
    ----------
    symbol      : Binance symbol, e.g. "BTCUSDT"
    interval    : one of SUPPORTED_INTERVALS
    start / end : date bounds (str "YYYY-MM-DD" or datetime)
    progress_cb : optional callback(current_dt, end_dt) called per batch
    """
    # Normalise start/end to tz-aware datetimes
    if isinstance(start, str):
        start = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    elif start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    if isinstance(end, str):
        end = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    elif end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if end > now:
        end = now

    cached = load_local(symbol, interval)

    if cached is None or cached.empty:
        # Full download
        df_new = download_range(symbol, interval, start, end, progress_cb)
        if not df_new.empty:
            save_local(df_new, symbol, interval)
        result = df_new
    else:
        cache_start = cached.index[0].to_pydatetime().replace(tzinfo=timezone.utc)
        cache_end   = cached.index[-1].to_pydatetime().replace(tzinfo=timezone.utc)

        frames_to_merge = [cached]

        # Download data before cache start (if requested)
        if start < cache_start:
            df_before = download_range(symbol, interval, start, cache_start, progress_cb)
            if not df_before.empty:
                frames_to_merge.insert(0, df_before)

        # Download data after cache end (if requested)
        if end > cache_end:
            df_after = download_range(symbol, interval, cache_end, end, progress_cb)
            if not df_after.empty:
                frames_to_merge.append(df_after)

        if len(frames_to_merge) > 1:
            result = pd.concat(frames_to_merge)
            result = result[~result.index.duplicated(keep="last")]
            result.sort_index(inplace=True)
            save_local(result, symbol, interval)
        else:
            result = cached

    if result.empty:
        return pd.DataFrame()

    # Return slice covering [start, end] — single mask avoids chained-loc length mismatch
    mask = (result.index >= pd.Timestamp(start)) & (result.index <= pd.Timestamp(end))
    return result.loc[mask]


def get_candles_yfinance(ticker: str, start: str, end: str) -> pd.DataFrame:
    """
    yfinance fallback for non-Binance tickers (equities, ETFs, etc.).
    Returns daily OHLCV with UTC-aware DatetimeIndex.
    """
    df = yf.download(ticker, start=start, end=end,
                     auto_adjust=True, progress=False)
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index, utc=True)
    return df.dropna()


# ─────────────────────────────────────────────────────────────────────────────
# CACHE INFO  (used by sidebar display)
# ─────────────────────────────────────────────────────────────────────────────

def get_cache_info(symbol: str, interval: str) -> dict:
    """
    Return metadata about a cached Parquet file.

    Returns
    -------
    {
        exists    : bool,
        path      : str,
        rows      : int,
        start     : str  (YYYY-MM-DD or ""),
        end       : str  (YYYY-MM-DD or ""),
        size_mb   : float,
    }
    """
    path = _parquet_path(symbol, interval)
    if not path.exists():
        return dict(exists=False, path=str(path), rows=0,
                    start="", end="", size_mb=0.0)

    try:
        df       = load_local(symbol, interval)
        size_mb  = path.stat().st_size / (1024 * 1024)
        if df is None or df.empty:
            return dict(exists=True, path=str(path), rows=0,
                        start="", end="", size_mb=round(size_mb, 2))
        return dict(
            exists  =True,
            path    =str(path),
            rows    =len(df),
            start   =str(df.index[0].date()),
            end     =str(df.index[-1].date()),
            size_mb =round(size_mb, 2),
        )
    except Exception:
        return dict(exists=True, path=str(path), rows=0,
                    start="", end="", size_mb=0.0)


def list_all_cache_info(symbol: str) -> dict[str, dict]:
    """
    Return cache info for all supported intervals for a given symbol.
    Used by the sidebar '📦 Data Cache' expander.
    """
    return {iv: get_cache_info(symbol, iv) for iv in SUPPORTED_INTERVALS}
