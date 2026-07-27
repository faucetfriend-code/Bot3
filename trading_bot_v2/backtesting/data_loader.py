"""
Historical Data Loader
======================

Loads OHLCV candles from local parquet cache or fetches from
public APIs (CoinGecko, Binance public, or raw CSV).

Provides the same interface as MultiTimeframeFetcher so the
live strategy code sees no difference between backtest and live.

Timeframes provided: 1m, 5m, 15m, 1h, 4h
Minimum history for regime detection: 29 x 4h candles (4.8 days)
"""

import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pandas as pd
from loguru import logger


def _autodownload_enabled() -> bool:
    """Whether loader-side auto-download is enabled (DATA_AUTODOWNLOAD)."""
    return os.getenv("DATA_AUTODOWNLOAD", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _autodownload_timeframes() -> set:
    """Timeframes the loader may auto-download (1m excluded by default).

    1m exists only for execution refinement and multi-year 1m pulls are
    huge; override with DATA_AUTODOWNLOAD_TIMEFRAMES if really wanted.
    """
    raw = os.getenv("DATA_AUTODOWNLOAD_TIMEFRAMES", "5m,15m,1h,4h")
    return {t.strip() for t in raw.split(",") if t.strip()}


class BacktestDataLoader:
    """
    Loads multi-timeframe historical candles for backtesting.

    Usage:
        loader = BacktestDataLoader(symbol="SUI-USDC", data_dir="backtesting/data")
        candles = loader.get_candles("4h", start="2024-01-01", end="2024-12-31")

    When a requested [start, end] range is not fully covered by local
    data and DATA_AUTODOWNLOAD is enabled (default), the loader asks
    CandleDownloadManager.ensure() to fetch the missing leading/trailing
    ranges from public APIs, then serves the merged store. Behaviour is
    unchanged when local data already covers the request.
    """

    SUPPORTED_TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]

    def __init__(
        self,
        symbol: str,
        data_dir: str = "backtesting/data",
        download_manager=None,
    ):
        self.symbol = symbol
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, pd.DataFrame] = {}
        self._download_manager = download_manager
        self._ensure_attempted: set = set()

    def get_candles(
        self,
        timeframe: str,
        start: str,
        end: str,
    ) -> Dict[str, List]:
        """
        Returns candles in the same format as MultiTimeframeFetcher.

        Returns dict with keys: open, high, low, close, volume, timestamp
        Each value is a list ordered oldest -> newest.
        """
        df = self._load_or_fetch(timeframe, start, end)
        if not self._covers_range(df, timeframe, start, end):
            if self._maybe_autodownload(timeframe, start, end):
                self._cache.pop(f"{self.symbol}_{timeframe}", None)
                df = self._load_or_fetch(timeframe, start, end)
        mask = (df["timestamp"] >= start) & (df["timestamp"] <= end)
        df = df[mask].copy()
        return {
            "open": df["open"].tolist(),
            "high": df["high"].tolist(),
            "low": df["low"].tolist(),
            "close": df["close"].tolist(),
            "volume": df["volume"].tolist(),
            "timestamp": df["timestamp"].tolist(),
        }

    def _load_or_fetch(self, timeframe: str, start: str, end: str) -> pd.DataFrame:
        cache_key = f"{self.symbol}_{timeframe}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        base = self.symbol.replace("/", "_")
        parquet_path = self.data_dir / f"{base}_{timeframe}.parquet"
        csv_path = self.data_dir / f"{base}_{timeframe}.csv"

        if parquet_path.exists():
            df = pd.read_parquet(parquet_path)
            logger.info(f"Loaded {len(df)} {timeframe} candles from {parquet_path}")
        elif csv_path.exists():
            df = pd.read_csv(csv_path, dtype={"timestamp": str})
            logger.info(f"Loaded {len(df)} {timeframe} candles from {csv_path}")
        elif self._maybe_autodownload(timeframe, start, end) and parquet_path.exists():
            df = pd.read_parquet(parquet_path)
            logger.info(
                f"Auto-downloaded {len(df)} {timeframe} candles to {parquet_path}"
            )
        else:
            df = self._fetch_from_api(timeframe, start, end)
            df.to_csv(csv_path, index=False)
            logger.info(f"Fetched and cached {len(df)} {timeframe} candles to {csv_path}")

        self._cache[cache_key] = df
        return df

    @staticmethod
    def _covers_range(df: pd.DataFrame, timeframe: str, start: str, end: str) -> bool:
        """Whether the loaded frame covers [start, end] at its bounds.

        Only leading/trailing coverage is checked - internal gaps are
        almost always unfillable exchange downtime and re-requesting
        them on every load would hammer the public APIs.
        """
        if df.empty:
            return False
        try:
            from ..data_manager import TF_MINUTES, _parse_dt

            step = timedelta(minutes=TF_MINUTES.get(timeframe, 5))
            first = _parse_dt(str(df["timestamp"].iloc[0]))
            last = _parse_dt(str(df["timestamp"].iloc[-1]))
            start_dt = _parse_dt(start)
            end_dt = _parse_dt(end)
        except (ValueError, TypeError):
            return True  # Unparseable bounds: keep legacy behaviour
        return first <= start_dt + step and last >= end_dt - step

    def _maybe_autodownload(self, timeframe: str, start: str, end: str) -> bool:
        """Try to download missing candles for [start, end] once.

        Gated by DATA_AUTODOWNLOAD (default true) and the timeframe
        allowlist DATA_AUTODOWNLOAD_TIMEFRAMES (default excludes 1m).
        Each (timeframe, start, end) is attempted at most once per
        loader instance.

        Returns:
            True if a download attempt added candles to the store.
        """
        if not _autodownload_enabled():
            return False
        if timeframe not in _autodownload_timeframes():
            return False
        key = (timeframe, start, end)
        if key in self._ensure_attempted:
            return False
        self._ensure_attempted.add(key)
        try:
            manager = self._download_manager
            if manager is None:
                from ..data_manager import CandleDownloadManager

                manager = CandleDownloadManager(data_dir=str(self.data_dir))
                self._download_manager = manager
            summary = manager.ensure(
                self.symbol, timeframe, start, end, include_internal_gaps=False
            )
        except Exception as e:  # noqa: BLE001 - never break a backtest on fetch
            logger.warning(
                f"Auto-download failed for {self.symbol} {timeframe} "
                f"{start}..{end}: {e}"
            )
            return False
        added = int(summary.get("added", 0))
        if added > 0:
            logger.info(
                f"Auto-downloaded {added} {timeframe} candles for "
                f"{self.symbol} covering {summary.get('requested_ranges')}"
            )
        return added > 0

    def _fetch_from_api(self, timeframe: str, start: str, end: str) -> pd.DataFrame:
        """
        Fetch historical data from public API.

        Priority order:
        1. Binance public API (most liquid, good history)
        2. CoinGecko (fallback, 1-day granularity only)
        3. Raw CSV in data_dir (manual import fallback)

        For SUI-USDC on Pacifica testnet: use Binance SUI-USDT
        as the price proxy (same underlying, negligible USDT/USDC spread).
        """
        raise NotImplementedError(
            f"Auto-fetch not yet implemented for {timeframe}. "
            f"Place a CSV file at {self.data_dir}/{self.symbol}_{timeframe}.csv "
            f"with columns: timestamp,open,high,low,close,volume"
        )
