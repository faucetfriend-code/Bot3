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

import math
import os
from pathlib import Path
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
import pandas as pd
from loguru import logger

if TYPE_CHECKING:
    from ..data_manager import CandleDownloadManager


def _autodownload_enabled() -> bool:
    """Whether loader-side auto-download is enabled (DATA_AUTODOWNLOAD)."""
    return os.getenv("DATA_AUTODOWNLOAD", "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _autodownload_timeframes() -> set[str]:
    """Timeframes the loader may auto-download (1m excluded by default).

    1m exists only for execution refinement and multi-year 1m pulls are
    huge; override with DATA_AUTODOWNLOAD_TIMEFRAMES if really wanted.
    """
    raw = os.getenv("DATA_AUTODOWNLOAD_TIMEFRAMES", "5m,15m,1h,4h")
    return {t.strip() for t in raw.split(",") if t.strip()}


def autodownload_lever(timeframe: str) -> str:
    """One sentence naming the auto-download lever for a timeframe.

    Coverage errors have to tell the reader *which* knob applies to the
    gap in front of them: 1m is excluded from auto-download by default,
    so no amount of ``DATA_AUTODOWNLOAD=true`` will fill it, whereas a
    5m gap on an offline run is one env var away from being fetched.

    Args:
        timeframe: Candle timeframe (e.g. "1m").

    Returns:
        A sentence describing why auto-download did not close the gap.
    """
    if not _autodownload_enabled():
        return (
            "Auto-download is OFF (DATA_AUTODOWNLOAD=false), so nothing was "
            "fetched; set DATA_AUTODOWNLOAD=true to let the loader try."
        )
    allowed = _autodownload_timeframes()
    if timeframe not in allowed:
        return (
            f"Auto-download is on but EXCLUDES '{timeframe}' "
            f"(DATA_AUTODOWNLOAD_TIMEFRAMES={','.join(sorted(allowed))}), so "
            f"this gap can only be closed manually."
        )
    return (
        "Auto-download is on and already ran for this range without closing "
        "the gap (the upstream API has no candles there)."
    )


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
        download_manager: Optional["CandleDownloadManager"] = None,
    ):
        self.symbol = symbol
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, pd.DataFrame] = {}
        self._download_manager = download_manager
        self._ensure_attempted: set[tuple[str, str, str]] = set()

    def get_candles(
        self,
        timeframe: str,
        start: str,
        end: str,
        warmup_candles: int = 0,
    ) -> Dict[str, List[Any]]:
        """
        Returns candles in the same format as MultiTimeframeFetcher.

        Args:
            timeframe: Candle timeframe (e.g. "4h").
            start: Window start (ISO date/datetime string).
            end: Window end (ISO date/datetime string).
            warmup_candles: Extra candles to load *before* ``start`` so that
                indicators and rolling-history slices are already saturated on
                the first replayed bar. The returned series therefore begins
                at ``start - warmup_candles * timeframe``; callers that must
                replay only [start, end] have to skip the prefix themselves.

        Returns dict with keys: open, high, low, close, volume, timestamp
        Each value is a list ordered oldest -> newest.
        """
        load_start = self.shift_start(start, timeframe, warmup_candles)
        df = self._load_or_fetch(timeframe, load_start, end)
        if not self._covers_range(df, timeframe, load_start, end):
            if self._maybe_autodownload(timeframe, load_start, end):
                self._cache.pop(f"{self.symbol}_{timeframe}", None)
                df = self._load_or_fetch(timeframe, load_start, end)
        mask = (df["timestamp"] >= load_start) & (df["timestamp"] <= end)
        df = df[mask].copy()
        return {
            "open": df["open"].tolist(),
            "high": df["high"].tolist(),
            "low": df["low"].tolist(),
            "close": df["close"].tolist(),
            "volume": df["volume"].tolist(),
            "timestamp": df["timestamp"].tolist(),
        }

    @staticmethod
    def shift_start(start: str, timeframe: str, candles: int) -> str:
        """Move a window start back by N candles of a timeframe.

        Args:
            start: Window start (ISO date/datetime string).
            timeframe: Candle timeframe (e.g. "4h").
            candles: Number of candles to reach back. Zero or negative
                returns ``start`` unchanged.

        Returns:
            Canonical "%Y-%m-%dT%H:%M:%S" timestamp string, or ``start``
            unchanged when it cannot be parsed.
        """
        if candles <= 0:
            return start
        try:
            from ..data_manager import CANONICAL_TS_FORMAT, TF_MINUTES, _parse_dt

            shifted = _parse_dt(start) - timedelta(
                minutes=TF_MINUTES.get(timeframe, 5) * candles
            )
            return shifted.strftime(CANONICAL_TS_FORMAT)
        except (ValueError, TypeError, ImportError):
            logger.warning(
                f"Could not apply {candles}-candle {timeframe} warmup to "
                f"start='{start}' - using the unshifted start"
            )
            return start

    def covers(self, timeframe: str, start: str, end: str) -> bool:
        """Whether local data (plus any allowed auto-download) covers a range.

        Mirrors the coverage logic of get_candles(): the local store is
        loaded, an auto-download is attempted when permitted, and the
        resulting frame is checked against [start, end] at its bounds.

        Args:
            timeframe: Candle timeframe (e.g. "1m").
            start: Range start (ISO date/datetime string).
            end: Range end (ISO date/datetime string).

        Returns:
            True when the store covers [start, end]; False when it does
            not or when no data can be loaded at all.
        """
        return bool(self.coverage_shortfall(timeframe, start, end)["covered"])

    def coverage_shortfall(
        self,
        timeframe: str,
        start: str,
        end: str,
        allow_download: bool = True,
    ) -> Dict[str, Any]:
        """Measure how far the local store falls short of [start, end].

        The quantified sibling of :meth:`covers`. Same load-then-maybe-
        download sequence as :meth:`get_candles`, so the answer describes
        the data a real run would actually see, but it reports *how many*
        candles are missing on each side instead of a bare boolean. That
        number is what makes a coverage failure actionable: "missing 6912
        of 8640 4h candles" says something a "not covered" never does.

        Only leading/trailing coverage is measured, matching
        :meth:`_covers_range` - internal gaps are unfillable exchange
        downtime, not a truncated window.

        Args:
            timeframe: Candle timeframe (e.g. "1m").
            start: Range start (ISO date/datetime string).
            end: Range end (ISO date/datetime string).
            allow_download: Whether an auto-download may be attempted for
                a range the local store does not cover. Pass False for a
                follow-up probe of a range already primed by an earlier
                call, so the same gap is not requested twice.

        Returns:
            Dict with keys ``covered`` (bool), ``first`` / ``last``
            (ISO strings, or None when nothing is on disk),
            ``missing_leading`` / ``missing_trailing`` (int candle counts,
            0 when that side is covered) and ``requested`` (int candles
            spanned by [start, end], 0 when the bounds are unparseable).
        """
        report: Dict[str, Any] = {
            "covered": False,
            "first": None,
            "last": None,
            "missing_leading": 0,
            "missing_trailing": 0,
            "requested": 0,
        }
        try:
            df = self._load_or_fetch(timeframe, start, end)
            if not self._covers_range(df, timeframe, start, end) and allow_download:
                if self._maybe_autodownload(timeframe, start, end):
                    self._cache.pop(f"{self.symbol}_{timeframe}", None)
                    df = self._load_or_fetch(timeframe, start, end)
        except Exception as e:  # noqa: BLE001 - absent data means "not covered"
            logger.debug(f"Coverage check failed for {self.symbol} {timeframe}: {e}")
            return report

        report["covered"] = self._covers_range(df, timeframe, start, end)
        if df.empty:
            return report
        report["first"] = str(df["timestamp"].iloc[0])
        report["last"] = str(df["timestamp"].iloc[-1])
        try:
            from ..data_manager import TF_MINUTES, _parse_dt

            minutes = TF_MINUTES.get(timeframe, 5)
            step = timedelta(minutes=minutes)
            first = _parse_dt(report["first"])
            last = _parse_dt(report["last"])
            start_dt = _parse_dt(start)
            end_dt = _parse_dt(end)
        except (ValueError, TypeError):
            return report  # Unparseable bounds: nothing to quantify

        report["requested"] = max(
            0, math.ceil((end_dt - start_dt).total_seconds() / (minutes * 60.0))
        )
        # The one-step tolerance mirrors _covers_range, so the counts are
        # zero exactly when `covered` is True.
        lead = (first - (start_dt + step)).total_seconds() / (minutes * 60.0)
        trail = ((end_dt - step) - last).total_seconds() / (minutes * 60.0)
        report["missing_leading"] = max(0, math.ceil(lead))
        report["missing_trailing"] = max(0, math.ceil(trail))
        return report

    def coverage_bounds(self, timeframe: str) -> Optional[Tuple[str, str]]:
        """First and last locally stored candle timestamps for a timeframe.

        Reads the timestamp column alone when a parquet store exists, so
        a bounds probe on a multi-million-row 1m store does not
        materialise (or cache) the full OHLCV frame. Falls back to the
        regular load path for CSV-only stores.

        Args:
            timeframe: Candle timeframe (e.g. "1m").

        Returns:
            (first_timestamp, last_timestamp) ISO strings, or None when
            no local data exists for the timeframe.
        """
        base = self.symbol.replace("/", "_")
        parquet_path = self.data_dir / f"{base}_{timeframe}.parquet"
        if parquet_path.exists():
            try:
                stamps = pd.read_parquet(parquet_path, columns=["timestamp"])[
                    "timestamp"
                ]
            except Exception as e:  # noqa: BLE001 - fall back to full load
                logger.debug(f"Timestamp-only read failed for {parquet_path}: {e}")
            else:
                if stamps.empty:
                    return None
                return str(stamps.iloc[0]), str(stamps.iloc[-1])
        try:
            df = self._load_or_fetch(timeframe, "2000-01-01", "2000-01-02")
        except Exception:  # noqa: BLE001 - no data on disk
            return None
        if df.empty:
            return None
        return str(df["timestamp"].iloc[0]), str(df["timestamp"].iloc[-1])

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
            logger.info(
                f"Fetched and cached {len(df)} {timeframe} candles to {csv_path}"
            )

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
            logger.warning(
                f"Auto-download SKIPPED for {self.symbol} {timeframe} "
                f"{start}..{end}: timeframe '{timeframe}' is excluded by "
                f"DATA_AUTODOWNLOAD_TIMEFRAMES "
                f"(={','.join(sorted(_autodownload_timeframes()))}). "
                f"Backfill manually with: python -m trading_bot_v2.data_manager "
                f"--symbols {self.symbol} --timeframes {timeframe}"
            )
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
