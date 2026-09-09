"""Historical candle download manager.

Maintains a canonical per-(symbol, timeframe) parquet store inside the
backtest data directory, downloads missing history from public keyless
APIs, detects coverage gaps, and resamples lower timeframes into higher
ones.

Canonical store format (matches the 2024 CSV files so
``BacktestDataLoader`` needs no format changes):
    columns  : timestamp, open, high, low, close, volume
    timestamp: tz-naive UTC ISO strings, "%Y-%m-%dT%H:%M:%S"
    ordering : ascending, unique timestamps
    filename : {SYMBOL}_{tf}.parquet (e.g. BTC-USDC_5m.parquet)

Data sources (public, keyless, probed 2026-07 from this machine):
    1. Binance spot klines (data-api.binance.vision, fallback
       api.binance.com). 1000 candles/page. BTCUSDT/ETHUSDT history
       starts 2017-08-17, SUIUSDT 2023-05-03. All bot timeframes.
    2. Bitstamp public OHLC (btcusd from 2011, ethusd from ~2017-08).
       1000 candles/page, used for 1h/4h only (pre-Binance history).
    3. Coinbase Exchange candles (BTC-USD from ~2015-07, ETH-USD from
       2016-05-18). 300 candles/page, 1h only (no native 4h
       granularity; 4h is resampled from 1h for that era).

Spot prices are used as a proxy for Pacifica perp prices: same
underlying, and USDT/USD/USDC quote spreads are negligible at the
timeframes used here.

Per-symbol practical floors achieved with this source chain:
    BTC-USDC: 1h/4h to 2015-01-01 (Bitstamp), 5m/15m to 2017-08-17
    ETH-USDC: 1h/4h to 2016-05-18 (Coinbase 1h, 4h resampled),
              5m/15m to 2017-08-17
    SUI-USDC: everything to 2023-05-03 (Binance listing)

For 2015-2017 only 1h and 4h are fetched (small-timeframe pages on the
pre-Binance sources are expensive and the backtest replay timeframe,
5m, does not exist there anyway).

External stores can be ingested read-only (BTV2 layout: parquet with a
UTC DatetimeIndex and Open/High/Low/Close/Volume columns, filenames
like BTCUSDT_5m.parquet), e.g. the existing cache at G:/Candle Data.

Derived from BTV2/data_manager.py (prior project phase): reuses its
Binance public-klines paging pattern (1000-bar batches, cursor =
last bar + 1ms, polite throttle), its parquet-cache-first design and
its per-symbol genesis-floor concept (BINANCE_GENESIS), generalized
here to multiple sources, gap-driven range downloads and resampling.
BTV2/ itself is treated as read-only reference.

Funding-rate store (separate from candles, same conventions):
    columns  : timestamp, funding_rate, mark_price, rate_type
    filename : {SYMBOL}_funding.parquet
    source   : Binance USD-M perpetual funding history
               (fapi.binance.com/fapi/v1/fundingRate, keyless, 1000
               settlements per page). BTCUSDT reaches back to
               2019-09-10T08:00:00Z.
    cadence  : Binance settles every 8 HOURS. The rate is stored exactly
               as published, per 8h interval. Pacifica settles HOURLY,
               so a venue mapping is required before these numbers mean
               anything about Pacifica P&L - that mapping lives in
               backtesting/funding.py and is deliberately NOT applied
               here.

CLI:
    python -m trading_bot_v2.data_manager \\
        --symbols BTC-USDC,ETH-USDC,SUI-USDC \\
        --timeframes 5m,15m,1h,4h --start 2015-01-01 [--update]
        [--coverage] [--ingest-dir "G:/Candle Data"] [--funding]

    # Funding history only, and its coverage
    python -m trading_bot_v2.data_manager --symbols BTC-USDC \\
        --funding-only --start 2019-09-01 --data-dir <abs path>
    python -m trading_bot_v2.data_manager --symbols BTC-USDC \\
        --coverage --funding --data-dir <abs path>
"""

import argparse
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd
import requests
from loguru import logger

CANONICAL_TS_FORMAT = "%Y-%m-%dT%H:%M:%S"
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
TF_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}
RESAMPLE_RULES = {"1m": "1min", "5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h"}
USER_AGENT = {"User-Agent": "Bot3-candle-manager/1.0"}
_EPOCH = datetime(1970, 1, 1)

# --- Funding-rate store -------------------------------------------------
#: Pseudo-timeframe key the funding store is reported under in coverage
#: tables. It is not a candle timeframe and never appears in TF_MINUTES.
FUNDING_KEY = "fund"
#: Canonical funding store columns. ``funding_rate`` is the rate PER
#: SOURCE INTERVAL exactly as the venue published it - no rescaling
#: happens at ingest time. ``rate_type`` preserves the venue's own label
#: ("Regular", and on Binance occasionally a special settlement) so an
#: off-schedule row can always be told apart from a regular one.
FUNDING_COLUMNS = ["timestamp", "funding_rate", "mark_price", "rate_type"]
#: Hours between settlements on the ingest source (Binance USD-M: 8).
#: This is the SOURCE cadence, not the trading venue's - see
#: backtesting/funding.py for the venue mapping.
FUNDING_SOURCE_INTERVAL_HOURS = 8
FUNDING_INTERVAL_MINUTES = FUNDING_SOURCE_INTERVAL_HOURS * 60
#: Seconds a published settlement may sit off the 8h grid and still be
#: snapped onto it. Binance stamps some settlements at HH:00:00.001;
#: that is a clock artifact, not a different settlement time.
FUNDING_GRID_TOLERANCE_S = 60


def _step_minutes(tf: str) -> int:
    """Grid step in minutes for a candle timeframe or the funding key."""
    if tf == FUNDING_KEY:
        return FUNDING_INTERVAL_MINUTES
    return TF_MINUTES[tf]


def _freq_rule(tf: str) -> str:
    """pandas offset alias for a candle timeframe or the funding key."""
    if tf == FUNDING_KEY:
        return f"{FUNDING_SOURCE_INTERVAL_HOURS}h"
    return RESAMPLE_RULES[tf]


def _parse_dt(value) -> datetime:
    """Parse a date/datetime string (or datetime) to a naive UTC datetime.

    Args:
        value: ISO date string ("2015-01-01"), ISO datetime string
            (naive or with +00:00 offset), or datetime object.

    Returns:
        tz-naive datetime interpreted as UTC.
    """
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _floor_dt(dt: datetime, tf: str) -> datetime:
    """Floor a datetime to the timeframe grid (epoch-aligned)."""
    step_min = _step_minutes(tf)
    total_min = int((dt - _EPOCH).total_seconds() // 60)
    return _EPOCH + timedelta(minutes=(total_min // step_min) * step_min)


def _ceil_dt(dt: datetime, tf: str) -> datetime:
    """Ceil a datetime to the timeframe grid (epoch-aligned)."""
    floored = _floor_dt(dt, tf)
    if floored == dt:
        return dt
    return floored + timedelta(minutes=_step_minutes(tf))


def normalize_candles(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a raw candle frame to the canonical store format.

    Handles tz-aware ("2018-01-01 00:00:00+00:00") and tz-naive
    ("2024-01-01T00:00:00") timestamp strings, coerces OHLCV to float,
    sorts ascending and drops duplicate timestamps (first wins).

    Args:
        df: Frame with at least the canonical columns.

    Returns:
        Normalized copy with canonical columns only.
    """
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    out = df[COLUMNS].copy()
    try:
        ts = pd.to_datetime(out["timestamp"], utc=True, format="ISO8601")
    except (ValueError, TypeError):
        ts = pd.to_datetime(out["timestamp"], utc=True, format="mixed")
    out["timestamp"] = ts.dt.tz_localize(None).dt.strftime(CANONICAL_TS_FORMAT)
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = out[col].astype(float)
    out = out.drop_duplicates(subset="timestamp", keep="first")
    out = out.sort_values("timestamp").reset_index(drop=True)
    return out


def merge_candles(*frames: pd.DataFrame) -> pd.DataFrame:
    """Merge candle frames; earlier frames win on duplicate timestamps.

    Args:
        *frames: Candle frames (raw or canonical).

    Returns:
        Canonical merged frame (sorted, unique timestamps).
    """
    non_empty = [normalize_candles(f) for f in frames if f is not None and not f.empty]
    if not non_empty:
        return pd.DataFrame(columns=COLUMNS)
    merged = pd.concat(non_empty, ignore_index=True)
    merged = merged.drop_duplicates(subset="timestamp", keep="first")
    return merged.sort_values("timestamp").reset_index(drop=True)


def _snap_funding_grid(ts: pd.Series) -> pd.Series:
    """Snap near-grid funding timestamps onto the exact 8h grid.

    Binance publishes most settlements at exactly 00:00/08:00/16:00 UTC
    but stamps a minority one millisecond late ("16:00:00.001"). Those
    are the same settlement, and leaving them off-grid would make every
    gap check report a spurious hole. Rows further than
    ``FUNDING_GRID_TOLERANCE_S`` from a grid point are left alone, so a
    genuinely off-schedule settlement stays visible.

    Args:
        ts: tz-naive UTC datetime series.

    Returns:
        Series with near-grid values replaced by the grid point.
    """
    step = pd.Timedelta(minutes=FUNDING_INTERVAL_MINUTES)
    nearest = ts.dt.round(step)
    within = (nearest - ts).abs() <= pd.Timedelta(seconds=FUNDING_GRID_TOLERANCE_S)
    return ts.where(~within, nearest)


def normalize_funding(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a raw funding frame to the canonical store format.

    Timestamps become tz-naive UTC canonical strings on the 8h grid,
    rates and mark prices become floats, duplicates are dropped (first
    wins) and rows are sorted ascending.

    Args:
        df: Frame with at least ``timestamp`` and ``funding_rate``.

    Returns:
        Normalized copy with canonical funding columns only.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=FUNDING_COLUMNS)
    out = df.copy()
    if "mark_price" not in out.columns:
        out["mark_price"] = float("nan")
    if "rate_type" not in out.columns:
        out["rate_type"] = "Regular"
    out = out[FUNDING_COLUMNS]
    try:
        ts = pd.to_datetime(out["timestamp"], utc=True, format="ISO8601")
    except (ValueError, TypeError):
        ts = pd.to_datetime(out["timestamp"], utc=True, format="mixed")
    ts = ts.dt.tz_localize(None)
    ts = _snap_funding_grid(ts)
    out["timestamp"] = ts.dt.strftime(CANONICAL_TS_FORMAT)
    out["funding_rate"] = out["funding_rate"].astype(float)
    out["mark_price"] = pd.to_numeric(out["mark_price"], errors="coerce")
    out["rate_type"] = out["rate_type"].fillna("Regular").astype(str)
    out = out.drop_duplicates(subset="timestamp", keep="first")
    return out.sort_values("timestamp").reset_index(drop=True)


def merge_funding(*frames: pd.DataFrame) -> pd.DataFrame:
    """Merge funding frames; earlier frames win on duplicate timestamps."""
    non_empty = [normalize_funding(f) for f in frames if f is not None and not f.empty]
    if not non_empty:
        return pd.DataFrame(columns=FUNDING_COLUMNS)
    merged = pd.concat(non_empty, ignore_index=True)
    merged = merged.drop_duplicates(subset="timestamp", keep="first")
    return merged.sort_values("timestamp").reset_index(drop=True)


@dataclass
class Coverage:
    """Coverage summary for one (symbol, timeframe) store.

    Attributes:
        symbol: Bot symbol (e.g. "BTC-USDC").
        timeframe: Timeframe key (e.g. "1h").
        start: First candle timestamp (canonical string) or None.
        end: Last candle timestamp (canonical string) or None.
        candle_count: Number of stored candles.
        gaps: Internal gaps as (first_missing, last_missing) canonical
            timestamp string pairs.
    """

    symbol: str
    timeframe: str
    start: Optional[str]
    end: Optional[str]
    candle_count: int
    gaps: List[Tuple[str, str]] = field(default_factory=list)


class _ThrottledHttpSource:
    """Shared polite-HTTP behaviour for every public keyless source.

    One throttle between requests, five attempts, exponential backoff on
    transport errors and on 429/5xx.
    """

    name = "base"
    pair_map: Dict[str, str] = {}

    def __init__(self, throttle_s: float = 0.15):
        self.throttle_s = throttle_s
        self._session = requests.Session()

    def _get_json(self, url: str, params: dict):
        """GET with throttle and exponential backoff on 429/5xx."""
        last_err: Optional[Exception] = None
        for attempt in range(5):
            if self.throttle_s > 0:
                time.sleep(self.throttle_s)
            try:
                resp = self._session.get(
                    url, params=params, headers=USER_AGENT, timeout=20
                )
            except requests.RequestException as e:
                last_err = e
                time.sleep(min(2**attempt, 8))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = RuntimeError(f"{self.name} HTTP {resp.status_code}")
                time.sleep(min(2**attempt, 8))
                continue
            resp.raise_for_status()
            return resp.json()
        raise RuntimeError(f"{self.name}: request failed after retries: {last_err}")


class CandleSource(_ThrottledHttpSource):
    """Base class for a public keyless candle source."""

    name = "base"
    supported_timeframes: Set[str] = set()
    pair_map: Dict[str, str] = {}
    floor_hints: Dict[str, datetime] = {}

    def supports(self, symbol: str, tf: str) -> bool:
        """Whether this source can serve the given symbol/timeframe."""
        return symbol in self.pair_map and tf in self.supported_timeframes

    def fetch(
        self, symbol: str, tf: str, start_dt: datetime, end_dt: datetime
    ) -> pd.DataFrame:
        """Fetch candles in [start_dt, end_dt] (naive UTC, inclusive)."""
        raise NotImplementedError


class FundingSource(_ThrottledHttpSource):
    """Base class for a public keyless perpetual-funding source."""

    name = "base-funding"
    #: Hours between settlements as this source publishes them.
    interval_hours = FUNDING_SOURCE_INTERVAL_HOURS
    floor_hints: Dict[str, datetime] = {}

    def supports(self, symbol: str) -> bool:
        """Whether this source can serve the given bot symbol."""
        return symbol in self.pair_map

    def fetch(self, symbol: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
        """Fetch settlements in [start_dt, end_dt] (naive UTC, inclusive)."""
        raise NotImplementedError


class BinanceFundingSource(FundingSource):
    """Binance USD-M perpetual funding history (fapi, keyless).

    ``GET /fapi/v1/fundingRate`` returns up to 1000 settlements per page
    as ``{symbol, fundingTime, fundingRate, markPrice, rateType}``, in
    ascending time order, paged with ``startTime``. BTCUSDT history
    reaches back to 2019-09-10T08:00:00Z (probed 2026-07).

    IMPORTANT: Binance settles every 8 HOURS. Pacifica settles every
    hour. This class ingests the SOURCE rate untouched; the venue
    mapping lives in ``backtesting/funding.py`` so the assumption is
    made once, in the open, and is configurable.
    """

    name = "binance-funding"
    interval_hours = 8
    pair_map = {
        "BTC-USDC": "BTCUSDT",
        "ETH-USDC": "ETHUSDT",
        "SUI-USDC": "SUIUSDT",
        "SOL-USDC": "SOLUSDT",
        "BNB-USDC": "BNBUSDT",
    }
    #: Earliest settlement Binance serves, probed per pair. Requests
    #: before it return the first available page anyway, so this is an
    #: efficiency hint and a documentation of the real floor.
    floor_hints = {"BTC-USDC": datetime(2019, 9, 10, 8, 0, 0)}
    urls = [
        "https://fapi.binance.com/fapi/v1/fundingRate",
    ]
    page_limit = 1000

    def fetch(self, symbol: str, start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
        """Page through funding settlements covering [start_dt, end_dt]."""
        pair = self.pair_map[symbol]
        start_ms = int(start_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(end_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        rows: List[dict] = []
        cur = start_ms
        while cur <= end_ms:
            page = self._get_json(
                self.urls[0],
                {
                    "symbol": pair,
                    "startTime": cur,
                    "endTime": end_ms,
                    "limit": self.page_limit,
                },
            )
            if not page:
                break
            rows.extend(page)
            nxt = int(page[-1]["fundingTime"]) + 1
            if nxt <= cur:
                break
            cur = nxt
            if len(page) < self.page_limit:
                break
        if not rows:
            return pd.DataFrame(columns=FUNDING_COLUMNS)
        df = pd.DataFrame(
            [
                {
                    "timestamp": datetime.fromtimestamp(
                        int(r["fundingTime"]) / 1000, tz=timezone.utc
                    ).replace(tzinfo=None),
                    "funding_rate": float(r["fundingRate"]),
                    "mark_price": float(r.get("markPrice") or "nan"),
                    "rate_type": str(r.get("rateType") or "Regular"),
                }
                for r in rows
            ]
        )
        return normalize_funding(df)


class BinanceSource(CandleSource):
    """Binance spot klines (data-api.binance.vision with .com fallback).

    1000 candles per page. Server clamps startTime to the listing date,
    so no floor hints are needed. History: BTCUSDT/ETHUSDT from
    2017-08-17, SUIUSDT from 2023-05-03 (probed).
    """

    name = "binance"
    supported_timeframes = {"1m", "5m", "15m", "1h", "4h"}
    pair_map = {
        "BTC-USDC": "BTCUSDT",
        "ETH-USDC": "ETHUSDT",
        "SUI-USDC": "SUIUSDT",
        "SOL-USDC": "SOLUSDT",
        "BNB-USDC": "BNBUSDT",
    }
    urls = [
        "https://data-api.binance.vision/api/v3/klines",
        "https://api.binance.com/api/v3/klines",
    ]
    page_limit = 1000

    def __init__(self, throttle_s: float = 0.15):
        super().__init__(throttle_s)
        self._url_idx = 0

    def _get_page(self, params: dict) -> list:
        """Fetch one klines page, failing over to the fallback URL."""
        try:
            return self._get_json(self.urls[self._url_idx], params)
        except (RuntimeError, requests.RequestException) as e:
            alt = 1 - self._url_idx
            logger.warning(
                f"binance: {self.urls[self._url_idx]} failed ({e}); "
                f"failing over to {self.urls[alt]}"
            )
            self._url_idx = alt
            return self._get_json(self.urls[self._url_idx], params)

    def fetch(
        self, symbol: str, tf: str, start_dt: datetime, end_dt: datetime
    ) -> pd.DataFrame:
        """Fetch klines pages covering [start_dt, end_dt]."""
        pair = self.pair_map[symbol]
        start_ms = int(start_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(end_dt.replace(tzinfo=timezone.utc).timestamp() * 1000)
        rows: List[list] = []
        cur = start_ms
        while cur <= end_ms:
            page = self._get_page(
                {
                    "symbol": pair,
                    "interval": tf,
                    "startTime": cur,
                    "endTime": end_ms,
                    "limit": self.page_limit,
                }
            )
            if not page:
                break
            rows.extend(page)
            cur = page[-1][0] + 1
            if len(page) < self.page_limit:
                break
        if not rows:
            return pd.DataFrame(columns=COLUMNS)
        df = pd.DataFrame(
            [
                {
                    "timestamp": datetime.fromtimestamp(
                        r[0] / 1000, tz=timezone.utc
                    ).replace(tzinfo=None),
                    "open": float(r[1]),
                    "high": float(r[2]),
                    "low": float(r[3]),
                    "close": float(r[4]),
                    "volume": float(r[5]),
                }
                for r in rows
            ]
        )
        df["timestamp"] = df["timestamp"].dt.strftime(CANONICAL_TS_FORMAT)
        return normalize_candles(df)


class BitstampSource(CandleSource):
    """Bitstamp public OHLC (btcusd from 2011, ethusd from ~2017-08).

    1000 candles per page; used only for 1h/4h pre-Binance history.
    Empty windows before a pair's listing return no rows, so requests
    are clamped to per-pair floor hints for efficiency.
    """

    name = "bitstamp"
    supported_timeframes = {"1h", "4h"}
    pair_map = {"BTC-USDC": "btcusd", "ETH-USDC": "ethusd"}
    floor_hints = {
        "BTC-USDC": datetime(2011, 8, 18),
        "ETH-USDC": datetime(2017, 7, 1),
    }
    url_template = "https://www.bitstamp.net/api/v2/ohlc/{pair}/"
    page_limit = 1000
    steps = {"1h": 3600, "4h": 14400}

    def fetch(
        self, symbol: str, tf: str, start_dt: datetime, end_dt: datetime
    ) -> pd.DataFrame:
        """Fetch OHLC pages covering [start_dt, end_dt]."""
        pair = self.pair_map[symbol]
        step = self.steps[tf]
        url = self.url_template.format(pair=pair)
        start_s = int(start_dt.replace(tzinfo=timezone.utc).timestamp())
        end_s = int(end_dt.replace(tzinfo=timezone.utc).timestamp())
        records: List[dict] = []
        cur = start_s
        while cur <= end_s:
            # NOTE: never pass "end" here - when both start and end are
            # given, Bitstamp anchors to end and returns the 1000
            # candles ENDING there, ignoring start (verified 2026-07).
            # Page forward with start only and filter client-side.
            payload = self._get_json(
                url,
                {"step": step, "limit": self.page_limit, "start": cur},
            )
            rows = payload.get("data", {}).get("ohlc", [])
            if rows:
                records.extend(
                    r for r in rows if start_s <= int(r["timestamp"]) <= end_s
                )
                last_ts = int(rows[-1]["timestamp"])
                if last_ts >= end_s or len(rows) < self.page_limit:
                    break
                cur = last_ts + step
            else:
                # Empty window (e.g. before listing): advance one page.
                cur += self.page_limit * step
        if not records:
            return pd.DataFrame(columns=COLUMNS)
        df = pd.DataFrame(
            [
                {
                    "timestamp": datetime.fromtimestamp(
                        int(r["timestamp"]), tz=timezone.utc
                    ).replace(tzinfo=None),
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": float(r["volume"]),
                }
                for r in records
            ]
        )
        df["timestamp"] = df["timestamp"].dt.strftime(CANONICAL_TS_FORMAT)
        return normalize_candles(df)


class CoinbaseSource(CandleSource):
    """Coinbase Exchange candles (300 per page, 1h only).

    Coinbase has no native 4h granularity; 4h for the Coinbase-only era
    is produced by resampling 1h. ETH-USD history starts 2016-05-18,
    BTC-USD ~2015-07 (probed; BTC pre-2015-07 comes from Bitstamp).
    """

    name = "coinbase"
    supported_timeframes = {"1h"}
    pair_map = {
        "BTC-USDC": "BTC-USD",
        "ETH-USDC": "ETH-USD",
        "SUI-USDC": "SUI-USD",
    }
    floor_hints = {
        "BTC-USDC": datetime(2015, 7, 20),
        "ETH-USDC": datetime(2016, 5, 18),
        "SUI-USDC": datetime(2023, 5, 18),
    }
    url_template = "https://api.exchange.coinbase.com/products/{pair}/candles"
    page_limit = 300
    granularity = 3600

    def fetch(
        self, symbol: str, tf: str, start_dt: datetime, end_dt: datetime
    ) -> pd.DataFrame:
        """Fetch candle windows (300 x 1h) covering [start_dt, end_dt]."""
        pair = self.pair_map[symbol]
        url = self.url_template.format(pair=pair)
        step = self.granularity
        window = self.page_limit * step
        start_s = int(start_dt.replace(tzinfo=timezone.utc).timestamp())
        end_s = int(end_dt.replace(tzinfo=timezone.utc).timestamp())
        rows: List[list] = []
        cur = start_s
        while cur <= end_s:
            win_end = min(cur + window - step, end_s)
            page = self._get_json(
                url,
                {
                    "granularity": step,
                    "start": datetime.fromtimestamp(cur, tz=timezone.utc).isoformat(),
                    "end": datetime.fromtimestamp(win_end, tz=timezone.utc).isoformat(),
                },
            )
            if isinstance(page, list):
                rows.extend(r for r in page if cur <= r[0] <= win_end)
            cur = win_end + step
        if not rows:
            return pd.DataFrame(columns=COLUMNS)
        # Coinbase rows: [time, low, high, open, close, volume]
        df = pd.DataFrame(
            [
                {
                    "timestamp": datetime.fromtimestamp(r[0], tz=timezone.utc).replace(
                        tzinfo=None
                    ),
                    "open": float(r[3]),
                    "high": float(r[2]),
                    "low": float(r[1]),
                    "close": float(r[4]),
                    "volume": float(r[5]),
                }
                for r in rows
            ]
        )
        df["timestamp"] = df["timestamp"].dt.strftime(CANONICAL_TS_FORMAT)
        return normalize_candles(df)


class CandleDownloadManager:
    """Canonical candle store with gap-driven downloading and resampling.

    Usage:
        mgr = CandleDownloadManager()
        mgr.consolidate_csvs("BTC-USDC", "5m")
        mgr.ensure("BTC-USDC", "1h", "2015-01-01", "2025-01-01")
        cov = mgr.coverage("BTC-USDC", "1h")
    """

    def __init__(
        self,
        data_dir: Optional[str] = None,
        sources: Optional[List[CandleSource]] = None,
        throttle_s: float = 0.15,
        funding_sources: Optional[List[FundingSource]] = None,
    ):
        self.data_dir = Path(
            data_dir
            or os.getenv("BACKTEST_DATA_DIR", "trading_bot_v2/backtesting/data")
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if sources is None:
            sources = [
                BinanceSource(throttle_s),
                BitstampSource(throttle_s),
                CoinbaseSource(throttle_s),
            ]
        self.sources = sources
        if funding_sources is None:
            funding_sources = [BinanceFundingSource(throttle_s)]
        self.funding_sources = funding_sources

    # ------------------------------------------------------------------
    # Store I/O
    # ------------------------------------------------------------------

    def store_path(self, symbol: str, tf: str) -> Path:
        """Canonical parquet path for a (symbol, timeframe)."""
        base = symbol.replace("/", "_")
        return self.data_dir / f"{base}_{tf}.parquet"

    def load_store(self, symbol: str, tf: str) -> pd.DataFrame:
        """Load the canonical store (empty canonical frame if absent)."""
        path = self.store_path(symbol, tf)
        if not path.exists():
            return pd.DataFrame(columns=COLUMNS)
        return pd.read_parquet(path)

    def save_store(self, symbol: str, tf: str, df: pd.DataFrame) -> None:
        """Write a canonical frame to the parquet store."""
        df = normalize_candles(df)
        df.to_parquet(self.store_path(symbol, tf), index=False)

    # ------------------------------------------------------------------
    # Funding store I/O
    # ------------------------------------------------------------------

    def funding_store_path(self, symbol: str) -> Path:
        """Canonical funding parquet path for a symbol."""
        return self.data_dir / f"{symbol.replace('/', '_')}_funding.parquet"

    def load_funding_store(self, symbol: str) -> pd.DataFrame:
        """Load the canonical funding store (empty frame if absent)."""
        path = self.funding_store_path(symbol)
        if not path.exists():
            return pd.DataFrame(columns=FUNDING_COLUMNS)
        return pd.read_parquet(path)

    def save_funding_store(self, symbol: str, df: pd.DataFrame) -> None:
        """Write a canonical funding frame to the parquet store."""
        normalize_funding(df).to_parquet(self.funding_store_path(symbol), index=False)

    def funding_coverage(self, symbol: str) -> Coverage:
        """Coverage summary for a symbol's funding store.

        Reported under the ``FUNDING_KEY`` pseudo-timeframe so it can be
        printed in the same table as the candle stores. Gaps are missing
        settlements on the source's 8h grid.

        Args:
            symbol: Bot symbol.

        Returns:
            Coverage with bounds, settlement count and internal gaps.
        """
        df = self.load_funding_store(symbol)
        if df.empty:
            return Coverage(symbol, FUNDING_KEY, None, None, 0, [])
        first = df["timestamp"].iloc[0]
        last = df["timestamp"].iloc[-1]
        gaps = self.missing_ranges(df, FUNDING_KEY, _parse_dt(first), _parse_dt(last))
        gap_strs = [
            (a.strftime(CANONICAL_TS_FORMAT), b.strftime(CANONICAL_TS_FORMAT))
            for a, b in gaps
        ]
        return Coverage(symbol, FUNDING_KEY, first, last, len(df), gap_strs)

    def _download_funding_range(
        self, symbol: str, start_dt: datetime, end_dt: datetime
    ) -> pd.DataFrame:
        """Download funding in [start_dt, end_dt] chaining sources."""
        frames: List[pd.DataFrame] = []
        for source in self.funding_sources:
            if not source.supports(symbol):
                continue
            eff_start = start_dt
            hint = source.floor_hints.get(symbol)
            if hint is not None and hint > eff_start:
                eff_start = hint
            if eff_start > end_dt:
                continue
            try:
                part = source.fetch(symbol, eff_start, end_dt)
            except (RuntimeError, requests.RequestException) as e:
                logger.warning(
                    f"{source.name} funding fetch failed for {symbol} "
                    f"{eff_start}..{end_dt}: {e}"
                )
                continue
            if part.empty:
                continue
            frames.append(part)
            logger.info(
                f"{source.name}: {len(part)} settlements for {symbol} "
                f"({part['timestamp'].iloc[0]} .. {part['timestamp'].iloc[-1]})"
            )
            break
        return merge_funding(*frames)

    def ensure_funding(
        self,
        symbol: str,
        start,
        end=None,
        include_internal_gaps: bool = True,
    ) -> Dict[str, object]:
        """Ensure the funding store covers [start, end].

        Mirrors :meth:`ensure` for candles: only missing ranges on the
        source's 8h grid are requested, progress is saved after every
        filled range, and the requested end is clamped to the last
        settlement that can already have happened.

        Args:
            symbol: Bot symbol.
            start: Range start (date/datetime/ISO string).
            end: Range end (defaults to now).
            include_internal_gaps: Also attempt to fill internal gaps.

        Returns:
            Summary dict: added (int), requested_ranges (list),
            unfilled_ranges (list).
        """
        step = timedelta(minutes=FUNDING_INTERVAL_MINUTES)
        start_dt = _parse_dt(start)
        now_clamp = _floor_dt(
            datetime.now(timezone.utc).replace(tzinfo=None), FUNDING_KEY
        )
        end_dt = _parse_dt(end) if end is not None else now_clamp
        end_dt = min(end_dt, now_clamp)
        df = self.load_funding_store(symbol)
        ranges = self.missing_ranges(df, FUNDING_KEY, start_dt, end_dt)
        if not include_internal_gaps and not df.empty:
            first = _parse_dt(df["timestamp"].iloc[0])
            last = _parse_dt(df["timestamp"].iloc[-1])
            ranges = [(a, b) for a, b in ranges if b < first or a > last]
        summary: Dict[str, object] = {
            "added": 0,
            "requested_ranges": [],
            "unfilled_ranges": [],
        }
        if not ranges:
            return summary
        added_total = 0
        for gap_start, gap_end in ranges:
            summary["requested_ranges"].append((gap_start, gap_end))
            fetched = self._download_funding_range(symbol, gap_start, gap_end + step)
            if fetched.empty:
                summary["unfilled_ranges"].append((gap_start, gap_end))
                continue
            before = len(df)
            df = merge_funding(df, fetched)
            added = len(df) - before
            added_total += added
            if added > 0:
                self.save_funding_store(symbol, df)
        summary["added"] = added_total
        logger.info(
            f"ensure_funding({symbol}, {start_dt.date()}..{end_dt.date()}): "
            f"added {added_total} settlements across "
            f"{len(summary['requested_ranges'])} missing range(s)"
        )
        return summary

    def consolidate_csvs(self, symbol: str, tf: str) -> int:
        """Merge legacy CSV files for (symbol, tf) into the parquet store.

        Picks up both the loader-visible {SYMBOL}_{tf}.csv and extras
        the loader never reads ({SYMBOL}_{tf}_2018.csv, _all.csv, ...).
        Original CSVs are left untouched.

        Args:
            symbol: Bot symbol.
            tf: Timeframe key.

        Returns:
            Number of candles added to the store.
        """
        base = symbol.replace("/", "_")
        csv_paths = sorted(self.data_dir.glob(f"{base}_{tf}.csv")) + sorted(
            self.data_dir.glob(f"{base}_{tf}_*.csv")
        )
        if not csv_paths:
            return 0
        store = self.load_store(symbol, tf)
        before = len(store)
        frames = [store]
        for path in csv_paths:
            try:
                frames.append(pd.read_csv(path, dtype={"timestamp": str}))
            except (OSError, ValueError, pd.errors.ParserError) as e:
                logger.warning(f"Skipping unreadable CSV {path}: {e}")
        merged = merge_candles(*frames)
        added = len(merged) - before
        if added > 0 or not self.store_path(symbol, tf).exists():
            self.save_store(symbol, tf, merged)
            logger.info(
                f"Consolidated {len(csv_paths)} CSV file(s) into "
                f"{self.store_path(symbol, tf).name} (+{added} candles)"
            )
        return max(added, 0)

    def ingest_external_parquet(self, path, symbol: str, tf: str) -> int:
        """Ingest an external OHLCV parquet into the canonical store.

        Accepts the BTV2/G:/Candle Data layout (UTC DatetimeIndex,
        Open/High/Low/Close/Volume columns) as well as canonical-layout
        files (timestamp column). The external file is read-only;
        existing store candles win on duplicate timestamps.

        Args:
            path: Path to the external parquet file.
            symbol: Bot symbol to ingest into (e.g. "BTC-USDC").
            tf: Timeframe key.

        Returns:
            Number of candles added to the store.
        """
        ext = pd.read_parquet(path)
        if "timestamp" not in ext.columns:
            ext = ext.reset_index()
            renames = {}
            for col in ext.columns:
                low = str(col).lower()
                if low in ("open", "high", "low", "close", "volume"):
                    renames[col] = low
                elif low in ("datetime", "date", "index", "open_time", "time"):
                    renames[col] = "timestamp"
            ext = ext.rename(columns=renames)
        if "timestamp" not in ext.columns:
            raise ValueError(f"Unrecognized external parquet layout: {path}")
        ts = pd.to_datetime(ext["timestamp"], utc=True)
        ext["timestamp"] = ts.dt.tz_localize(None).dt.strftime(CANONICAL_TS_FORMAT)
        store = self.load_store(symbol, tf)
        before = len(store)
        merged = merge_candles(store, ext[COLUMNS])
        added = len(merged) - before
        if added > 0:
            self.save_store(symbol, tf, merged)
        logger.info(
            f"Ingested {path} into {symbol} {tf}: +{added} candles "
            f"({len(ext)} external rows)"
        )
        return max(added, 0)

    # ------------------------------------------------------------------
    # Coverage / gaps
    # ------------------------------------------------------------------

    @staticmethod
    def missing_ranges(
        df: pd.DataFrame, tf: str, start_dt: datetime, end_dt: datetime
    ) -> List[Tuple[datetime, datetime]]:
        """Missing candle ranges on the tf grid within [start_dt, end_dt].

        Args:
            df: Canonical candle frame (may be empty).
            tf: Timeframe key.
            start_dt: Range start (naive UTC).
            end_dt: Range end (naive UTC, inclusive).

        Returns:
            List of (first_missing, last_missing) datetime pairs,
            covering leading, internal and trailing gaps.
        """
        step = timedelta(minutes=_step_minutes(tf))
        grid_start = _ceil_dt(start_dt, tf)
        grid_end = _floor_dt(end_dt, tf)
        if grid_start > grid_end:
            return []
        expected = pd.date_range(grid_start, grid_end, freq=_freq_rule(tf))
        if df.empty:
            present = pd.DatetimeIndex([])
        else:
            present = pd.DatetimeIndex(
                pd.to_datetime(df["timestamp"], format=CANONICAL_TS_FORMAT)
            )
        missing = expected.difference(present)
        if len(missing) == 0:
            return []
        ranges: List[Tuple[datetime, datetime]] = []
        run_start = missing[0]
        prev = missing[0]
        for ts in missing[1:]:
            if ts - prev > step:
                ranges.append((run_start.to_pydatetime(), prev.to_pydatetime()))
                run_start = ts
            prev = ts
        ranges.append((run_start.to_pydatetime(), prev.to_pydatetime()))
        return ranges

    def coverage(self, symbol: str, tf: str) -> Coverage:
        """Coverage summary (bounds, count, internal gaps) for a store."""
        df = self.load_store(symbol, tf)
        if df.empty:
            return Coverage(symbol, tf, None, None, 0, [])
        first = df["timestamp"].iloc[0]
        last = df["timestamp"].iloc[-1]
        gaps = self.missing_ranges(df, tf, _parse_dt(first), _parse_dt(last))
        gap_strs = [
            (a.strftime(CANONICAL_TS_FORMAT), b.strftime(CANONICAL_TS_FORMAT))
            for a, b in gaps
        ]
        return Coverage(symbol, tf, first, last, len(df), gap_strs)

    # ------------------------------------------------------------------
    # Downloading
    # ------------------------------------------------------------------

    def _download_range(
        self, symbol: str, tf: str, start_dt: datetime, end_dt: datetime
    ) -> pd.DataFrame:
        """Download [start_dt, end_dt] chaining sources by priority.

        The first (highest-quality) source is asked for the whole
        range; each subsequent source only fills whatever remains
        before the earliest candle obtained so far.
        """
        step = timedelta(minutes=TF_MINUTES[tf])
        frames: List[pd.DataFrame] = []
        cur_end = end_dt
        for source in self.sources:
            if not source.supports(symbol, tf):
                continue
            eff_start = start_dt
            hint = source.floor_hints.get(symbol)
            if hint is not None and hint > eff_start:
                eff_start = hint
            if eff_start > cur_end:
                continue
            try:
                part = source.fetch(symbol, tf, eff_start, cur_end)
            except (RuntimeError, requests.RequestException) as e:
                logger.warning(
                    f"{source.name} fetch failed for {symbol} {tf} "
                    f"{eff_start}..{cur_end}: {e}"
                )
                continue
            if part.empty:
                continue
            frames.append(part)
            earliest = _parse_dt(part["timestamp"].iloc[0])
            logger.info(
                f"{source.name}: {len(part)} {tf} candles for {symbol} "
                f"({part['timestamp'].iloc[0]} .. {part['timestamp'].iloc[-1]})"
            )
            if earliest <= start_dt + step:
                break
            cur_end = earliest - step
        return merge_candles(*frames)

    def ensure(
        self,
        symbol: str,
        tf: str,
        start,
        end=None,
        include_internal_gaps: bool = True,
    ) -> Dict[str, object]:
        """Ensure the store covers [start, end], downloading what's missing.

        Only missing ranges (leading, trailing and - optionally -
        internal gaps) are requested from the sources. The requested
        end is clamped to the last complete candle before "now".

        Args:
            symbol: Bot symbol.
            tf: Timeframe key.
            start: Range start (date/datetime/ISO string).
            end: Range end (defaults to now).
            include_internal_gaps: Also attempt to fill internal gaps
                (the loader's auto path disables this because remaining
                internal gaps are usually unfillable exchange downtime).

        Returns:
            Summary dict: added (int), requested_ranges (list),
            unfilled_ranges (list).
        """
        if tf not in TF_MINUTES:
            raise ValueError(f"Unsupported timeframe: {tf}")
        step = timedelta(minutes=TF_MINUTES[tf])
        start_dt = _parse_dt(start)
        now_clamp = (
            _floor_dt(datetime.now(timezone.utc).replace(tzinfo=None), tf) - step
        )
        end_dt = _parse_dt(end) if end is not None else now_clamp
        end_dt = min(end_dt, now_clamp)
        df = self.load_store(symbol, tf)
        ranges = self.missing_ranges(df, tf, start_dt, end_dt)
        if not include_internal_gaps and not df.empty:
            first = _parse_dt(df["timestamp"].iloc[0])
            last = _parse_dt(df["timestamp"].iloc[-1])
            ranges = [(a, b) for a, b in ranges if b < first or a > last]
        summary: Dict[str, object] = {
            "added": 0,
            "requested_ranges": [],
            "unfilled_ranges": [],
        }
        if not ranges:
            return summary
        added_total = 0
        for gap_start, gap_end in ranges:
            summary["requested_ranges"].append((gap_start, gap_end))
            fetched = self._download_range(symbol, tf, gap_start, gap_end)
            if fetched.empty:
                summary["unfilled_ranges"].append((gap_start, gap_end))
                continue
            before = len(df)
            df = merge_candles(df, fetched)
            added = len(df) - before
            added_total += added
            if added > 0:
                # Save after every filled gap so a long multi-gap run
                # keeps its progress even if the process dies.
                self.save_store(symbol, tf, df)
        summary["added"] = added_total
        logger.info(
            f"ensure({symbol}, {tf}, {start_dt.date()}..{end_dt.date()}): "
            f"added {added_total} candles across "
            f"{len(summary['requested_ranges'])} missing range(s)"
        )
        return summary

    # ------------------------------------------------------------------
    # Resampling
    # ------------------------------------------------------------------

    def resample_fill(
        self,
        symbol: str,
        source_tf: str,
        target_tf: str,
        min_fraction: float = 1.0,
    ) -> int:
        """Fill missing target-tf candles by resampling the source store.

        OHLCV aggregation: open=first, high=max, low=min, close=last,
        volume=sum, on left-closed left-labeled epoch-aligned buckets.
        Buckets with fewer than ceil(expected * min_fraction) source
        candles are dropped (min_fraction=1.0 keeps complete buckets
        only). Existing target candles are never overwritten.

        Args:
            symbol: Bot symbol.
            source_tf: Lower timeframe (e.g. "5m").
            target_tf: Higher timeframe (e.g. "4h").
            min_fraction: Minimum fraction of expected source candles a
                bucket needs to be emitted.

        Returns:
            Number of target candles added.
        """
        if TF_MINUTES[target_tf] % TF_MINUTES[source_tf] != 0:
            raise ValueError(f"{source_tf} does not divide {target_tf}")
        expected = TF_MINUTES[target_tf] // TF_MINUTES[source_tf]
        if expected <= 1:
            return 0
        src = self.load_store(symbol, source_tf)
        if src.empty:
            return 0
        src = src.copy()
        src["timestamp"] = pd.to_datetime(src["timestamp"], format=CANONICAL_TS_FORMAT)
        src = src.set_index("timestamp")
        agg = src.resample(RESAMPLE_RULES[target_tf], label="left", closed="left").agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            count=("close", "count"),
        )
        min_count = max(1, math.ceil(expected * min_fraction))
        agg = agg[agg["count"] >= min_count].drop(columns=["count"]).dropna()
        if agg.empty:
            return 0
        agg = agg.reset_index()
        agg["timestamp"] = agg["timestamp"].dt.strftime(CANONICAL_TS_FORMAT)
        target = self.load_store(symbol, target_tf)
        before = len(target)
        # Existing target candles win over resampled ones.
        merged = merge_candles(target, agg)
        added = len(merged) - before
        if added > 0:
            self.save_store(symbol, target_tf, merged)
            logger.info(
                f"Resampled {source_tf}->{target_tf} for {symbol}: "
                f"+{added} candles (min_fraction={min_fraction})"
            )
        return max(added, 0)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _format_coverage_table(coverages: List[Coverage]) -> str:
    """Render coverage rows as a fixed-width text table."""
    lines = [
        f"{'symbol':<10} {'tf':<4} {'first':<20} {'last':<20} "
        f"{'candles':>9} {'gaps':>5}  largest_gap",
        "-" * 92,
    ]
    for cov in coverages:
        if cov.candle_count == 0:
            lines.append(
                f"{cov.symbol:<10} {cov.timeframe:<4} {'-':<20} {'-':<20} "
                f"{0:>9} {0:>5}  -"
            )
            continue
        largest = "-"
        if cov.gaps:
            step = timedelta(minutes=_step_minutes(cov.timeframe))

            def _gap_len(g):
                return _parse_dt(g[1]) - _parse_dt(g[0]) + step

            big = max(cov.gaps, key=_gap_len)
            largest = f"{big[0]}..{big[1]}"
        lines.append(
            f"{cov.symbol:<10} {cov.timeframe:<4} {cov.start:<20} "
            f"{cov.end:<20} {cov.candle_count:>9} {len(cov.gaps):>5}  {largest}"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for backfill / update / coverage reporting."""
    parser = argparse.ArgumentParser(description="Historical candle download manager")
    parser.add_argument(
        "--symbols",
        default="BTC-USDC,ETH-USDC,SUI-USDC",
        help="Comma-separated bot symbols",
    )
    parser.add_argument(
        "--timeframes",
        default="5m,15m,1h,4h",
        help="Comma-separated timeframes (1m,5m,15m,1h,4h)",
    )
    parser.add_argument("--start", default="2015-01-01", help="Backfill start date")
    parser.add_argument("--end", default=None, help="Backfill end date (default now)")
    parser.add_argument(
        "--update",
        action="store_true",
        help="Top-up mode: only extend the trailing edge to now",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Print the coverage table and exit (no downloads)",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Data directory (default BACKTEST_DATA_DIR or "
        "trading_bot_v2/backtesting/data)",
    )
    parser.add_argument(
        "--throttle",
        type=float,
        default=0.15,
        help="Seconds between API requests (default 0.15)",
    )
    parser.add_argument(
        "--ingest-dir",
        default=None,
        help="Ingest external OHLCV parquets (BTV2 layout, e.g. "
        "G:/Candle Data) for the given symbols/timeframes, print the "
        "coverage table and exit. The external dir is read-only.",
    )
    parser.add_argument(
        "--funding",
        action="store_true",
        help="Also operate on the perpetual funding-rate store "
        "({SYMBOL}_funding.parquet). With --coverage it adds a 'fund' "
        "row per symbol; otherwise it backfills funding history "
        "alongside the candles.",
    )
    parser.add_argument(
        "--funding-only",
        action="store_true",
        help="Operate ONLY on the funding store (implies --funding); "
        "no candle download or resampling happens.",
    )
    args = parser.parse_args(argv)
    if args.funding_only:
        args.funding = True

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    for tf in timeframes:
        if tf not in TF_MINUTES:
            parser.error(f"Unknown timeframe: {tf}")

    mgr = CandleDownloadManager(data_dir=args.data_dir, throttle_s=args.throttle)

    def _coverages() -> List[Coverage]:
        """Coverage rows for the requested stores, funding last."""
        rows: List[Coverage] = []
        for sym in symbols:
            if not args.funding_only:
                rows.extend(mgr.coverage(sym, tf) for tf in timeframes)
            if args.funding:
                rows.append(mgr.funding_coverage(sym))
        return rows

    if args.coverage:
        print(_format_coverage_table(_coverages()))
        return 0

    if args.ingest_dir:
        ingest_dir = Path(args.ingest_dir)
        for symbol in symbols:
            pair = BinanceSource.pair_map.get(symbol)
            for tf in timeframes:
                candidates = []
                if pair:
                    candidates.append(ingest_dir / f"{pair}_{tf}.parquet")
                candidates.append(
                    ingest_dir / f"{symbol.replace('/', '_')}_{tf}.parquet"
                )
                for path in candidates:
                    if path.exists():
                        mgr.ingest_external_parquet(path, symbol, tf)
                        break
                else:
                    logger.info(f"No external file for {symbol} {tf}, skipped")
        coverages = [mgr.coverage(sym, tf) for sym in symbols for tf in timeframes]
        print(_format_coverage_table(coverages))
        return 0

    resample_targets = [tf for tf in ("15m", "1h", "4h") if tf in timeframes]
    for symbol in symbols:
        if not args.funding_only:
            # 1. Consolidate legacy CSVs (incl. 5m/1h resample bases).
            consolidate_tfs = sorted(
                set(timeframes) | {"5m", "1h"}, key=lambda t: TF_MINUTES[t]
            )
            for tf in consolidate_tfs:
                self_added = mgr.consolidate_csvs(symbol, tf)
                if self_added:
                    logger.info(f"{symbol} {tf}: consolidated +{self_added} from CSVs")

            # 2. Cheap pre-pass: build higher TFs from 5m (complete buckets
            #    only) before touching the network.
            for tf in resample_targets:
                mgr.resample_fill(symbol, "5m", tf, min_fraction=1.0)

            # 3. Download what is still missing.
            for tf in timeframes:
                if args.update:
                    cov = mgr.coverage(symbol, tf)
                    upd_start = cov.end or args.start
                    mgr.ensure(
                        symbol,
                        tf,
                        upd_start,
                        args.end,
                        include_internal_gaps=False,
                    )
                else:
                    mgr.ensure(symbol, tf, args.start, args.end)

            # 4. Post-pass: fill 4h holes from 1h (pre-Binance eras where no
            #    native 4h source exists, e.g. ETH via Coinbase 1h). Lenient
            #    threshold because thin early markets have missing hours.
            if "4h" in timeframes:
                mgr.resample_fill(symbol, "1h", "4h", min_fraction=0.5)
            for tf in resample_targets:
                mgr.resample_fill(symbol, "5m", tf, min_fraction=0.5)

        # 5. Funding history (opt-in): same gap-driven contract, on the
        #    source's own 8h settlement grid.
        if args.funding:
            if args.update:
                cov = mgr.funding_coverage(symbol)
                upd_start = cov.end or args.start
                mgr.ensure_funding(
                    symbol, upd_start, args.end, include_internal_gaps=False
                )
            else:
                mgr.ensure_funding(symbol, args.start, args.end)

    print(_format_coverage_table(_coverages()))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
