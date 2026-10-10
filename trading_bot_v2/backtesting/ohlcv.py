"""
Validated OHLCV file reader
===========================

Reads one timeframe of candles from a local CSV, gzipped CSV or Parquet
file and checks it before anything downstream replays it. The checks are
the ones that silently corrupt a backtest when skipped:

* **ordering** - timestamps must ascend; an unsorted file makes the
  engine's as-of index lookups serve candles from the wrong date.
* **duplicates** - two rows with one timestamp double-count a bar.
* **gaps** - missing bars are reported (count, first few locations) but
  never filled; a gap is real exchange downtime or a truncated download
  and inventing candles for it would be fabrication.
* **timezone** - tz-aware inputs are converted to UTC and stored as
  naive canonical ``%Y-%m-%dT%H:%M:%S`` strings, the format every other
  store in this package uses. Mixed-offset files are rejected.
* **values** - NaN/inf, non-positive prices, ``high < low`` and an
  open/close outside ``[low, high]`` are fatal; negative volume is fatal.

Nothing here touches the network. The reader is deliberately a pure
function of the file so a test can drive it with a five-row fixture.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from loguru import logger

from ..data_manager import CANONICAL_TS_FORMAT, TF_MINUTES

OHLCV_COLUMNS: Tuple[str, ...] = ("timestamp", "open", "high", "low", "close", "volume")

#: Column-name aliases accepted on input, lower-cased. The canonical name
#: always wins when both are present.
COLUMN_ALIASES: Dict[str, str] = {
    "time": "timestamp",
    "date": "timestamp",
    "datetime": "timestamp",
    "ts": "timestamp",
    "open_time": "timestamp",
    "o": "open",
    "h": "high",
    "l": "low",
    "c": "close",
    "v": "volume",
    "vol": "volume",
}

#: Gap locations kept in the report. The total is always exact; this only
#: bounds the list so a multi-year store with thousands of outages does
#: not turn the report into a dump.
MAX_GAPS_LISTED = 20

SUPPORTED_SUFFIXES = (".csv", ".csv.gz", ".parquet")


class CandleDataError(ValueError):
    """Raised when a candle file fails validation in strict mode."""


@dataclass
class CandleValidation:
    """Outcome of validating one timeframe of candles.

    Attributes:
        path: File the frame came from ("<memory>" for in-memory frames).
        timeframe: Candle timeframe the rows were checked against.
        rows: Row count after repair.
        first: Earliest timestamp (canonical string) or None when empty.
        last: Latest timestamp (canonical string) or None when empty.
        duplicates: Rows sharing a timestamp with an earlier row.
        out_of_order: Rows whose timestamp precedes the previous row.
        off_grid: Rows whose timestamp is not on the timeframe grid.
        gaps: Missing-bar count across the whole series.
        gap_locations: Up to MAX_GAPS_LISTED (timestamp, missing) pairs.
        tz_aware: Whether the input carried an explicit UTC offset.
        bad_values: Rows with NaN/inf, non-positive price, inverted
            high/low, open/close outside the bar, or negative volume.
        repaired: Whether duplicates were dropped and rows re-sorted.
        notes: Human-readable remarks accumulated during validation.
    """

    path: str
    timeframe: str
    rows: int = 0
    first: Optional[str] = None
    last: Optional[str] = None
    duplicates: int = 0
    out_of_order: int = 0
    off_grid: int = 0
    gaps: int = 0
    gap_locations: List[Tuple[str, int]] = field(default_factory=list)
    tz_aware: bool = False
    bad_values: int = 0
    repaired: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def fatal(self) -> List[str]:
        """Problems that make the frame unusable as it stands."""
        problems: List[str] = []
        if self.rows == 0:
            problems.append("no rows")
        if self.bad_values:
            problems.append(f"{self.bad_values} rows with invalid OHLCV values")
        if self.off_grid:
            problems.append(f"{self.off_grid} rows off the {self.timeframe} grid")
        if not self.repaired:
            if self.duplicates:
                problems.append(f"{self.duplicates} duplicate timestamps")
            if self.out_of_order:
                problems.append(f"{self.out_of_order} out-of-order rows")
        return problems

    @property
    def ok(self) -> bool:
        """True when nothing fatal remains (gaps are a warning, not fatal)."""
        return not self.fatal

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe view of the validation."""
        return {
            "path": self.path,
            "timeframe": self.timeframe,
            "rows": self.rows,
            "first": self.first,
            "last": self.last,
            "duplicates": self.duplicates,
            "out_of_order": self.out_of_order,
            "off_grid": self.off_grid,
            "gaps": self.gaps,
            "gap_locations": [list(g) for g in self.gap_locations],
            "tz_aware_input": self.tz_aware,
            "bad_values": self.bad_values,
            "repaired": self.repaired,
            "ok": self.ok,
            "fatal": self.fatal,
            "notes": list(self.notes),
        }

    def summary(self) -> str:
        """One line per timeframe, for the console and the text report."""
        span = f"{self.first} .. {self.last}" if self.first else "empty"
        issues = []
        if self.duplicates:
            issues.append(f"dups={self.duplicates}")
        if self.out_of_order:
            issues.append(f"unsorted={self.out_of_order}")
        if self.off_grid:
            issues.append(f"off_grid={self.off_grid}")
        if self.gaps:
            issues.append(f"gaps={self.gaps}")
        if self.bad_values:
            issues.append(f"bad_values={self.bad_values}")
        state = "OK" if self.ok else "FAIL"
        detail = f" [{' '.join(issues)}]" if issues else ""
        tz = " (tz-aware input, converted to UTC)" if self.tz_aware else ""
        return f"{self.timeframe:>3}: {state} {self.rows} rows {span}{detail}{tz}"


def read_ohlcv_file(path: Path) -> pd.DataFrame:
    """Read a raw candle file without validating it.

    Args:
        path: ``.csv``, ``.csv.gz`` or ``.parquet`` file.

    Returns:
        Frame with the canonical column names, timestamps still raw.

    Raises:
        FileNotFoundError: When the file does not exist.
        CandleDataError: When the suffix is unsupported or a required
            column is missing.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"candle file not found: {path}")
    name = path.name.lower()
    if name.endswith(".parquet"):
        df = pd.read_parquet(path)
    elif name.endswith(".csv") or name.endswith(".csv.gz"):
        df = pd.read_csv(path, dtype={"timestamp": str})
    else:
        raise CandleDataError(
            f"{path}: unsupported suffix; expected one of {SUPPORTED_SUFFIXES}"
        )
    return _canonical_columns(df, str(path))


def _canonical_columns(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """Rename aliased columns and keep only the canonical six."""
    renamed: Dict[str, str] = {}
    lowered = {str(c).strip().lower(): c for c in df.columns}
    for low, original in lowered.items():
        target = low if low in OHLCV_COLUMNS else COLUMN_ALIASES.get(low)
        if target and target not in renamed.values():
            renamed[original] = target
    out = df.rename(columns=renamed)
    missing = [c for c in OHLCV_COLUMNS if c not in out.columns]
    if missing:
        raise CandleDataError(
            f"{label}: missing columns {missing}; have {list(df.columns)}"
        )
    return out[list(OHLCV_COLUMNS)].copy()


def _parse_timestamps(raw: pd.Series, label: str) -> Tuple[pd.Series, bool]:
    """Parse timestamps to naive UTC datetimes.

    Args:
        raw: Timestamp column as read from the file.
        label: File label for error messages.

    Returns:
        (naive UTC datetime series, whether the input was tz-aware).

    Raises:
        CandleDataError: On unparseable values or mixed UTC offsets.
    """
    if pd.api.types.is_datetime64_any_dtype(raw):
        parsed = pd.to_datetime(raw)
    else:
        text = raw.astype(str).str.strip()
        try:
            parsed = pd.to_datetime(text, format="ISO8601")
        except (ValueError, TypeError):
            try:
                parsed = pd.to_datetime(text, format="mixed")
            except (ValueError, TypeError) as exc:
                raise CandleDataError(f"{label}: unparseable timestamps: {exc}")
    if parsed.isna().any():
        bad = int(parsed.isna().sum())
        raise CandleDataError(f"{label}: {bad} unparseable timestamps")
    if parsed.dt.tz is None:
        return parsed, False
    return parsed.dt.tz_convert("UTC").dt.tz_localize(None), True


def _check_values(df: pd.DataFrame) -> pd.Series:
    """Boolean mask of rows whose OHLCV values are invalid."""
    prices = df[["open", "high", "low", "close"]]
    non_finite = ~prices.apply(lambda c: c.map(math.isfinite)).all(axis=1)
    non_finite |= ~df["volume"].map(math.isfinite)
    non_positive = (prices <= 0).any(axis=1)
    inverted = df["high"] < df["low"]
    outside = (
        (df["open"] > df["high"])
        | (df["open"] < df["low"])
        | (df["close"] > df["high"])
        | (df["close"] < df["low"])
    )
    negative_volume = df["volume"] < 0
    return non_finite | non_positive | inverted | outside | negative_volume


def _count_gaps(
    stamps: List[datetime], step: timedelta
) -> Tuple[int, List[Tuple[str, int]]]:
    """Count missing bars between consecutive sorted, de-duplicated stamps."""
    total = 0
    locations: List[Tuple[str, int]] = []
    for previous, current in zip(stamps, stamps[1:]):
        missing = int((current - previous) / step) - 1
        if missing <= 0:
            continue
        total += missing
        if len(locations) < MAX_GAPS_LISTED:
            locations.append((previous.strftime(CANONICAL_TS_FORMAT), missing))
    return total, locations


def validate_candles(
    df: pd.DataFrame,
    timeframe: str,
    label: str = "<memory>",
    repair: bool = True,
) -> Tuple[pd.DataFrame, CandleValidation]:
    """Validate (and optionally repair) one timeframe of candles.

    Repair means: drop duplicate timestamps (first row wins) and sort
    ascending. Gaps and bad values are never repaired, only reported.

    Args:
        df: Frame with the canonical columns (see :func:`read_ohlcv_file`).
        timeframe: One of ``TF_MINUTES`` ("1m", "5m", "15m", "1h", "4h").
        label: Name used in the report and in error messages.
        repair: Whether to drop duplicates and re-sort.

    Returns:
        (canonical frame, validation report). The frame's ``timestamp``
        column holds naive-UTC canonical strings and OHLCV are floats.

    Raises:
        CandleDataError: When ``timeframe`` is unknown, a column is
            missing, or timestamps cannot be parsed.
    """
    if timeframe not in TF_MINUTES:
        raise CandleDataError(
            f"{label}: unknown timeframe {timeframe!r}; expected {list(TF_MINUTES)}"
        )
    report = CandleValidation(path=label, timeframe=timeframe)
    frame = _canonical_columns(df, label)
    if frame.empty:
        report.notes.append("file has no rows")
        return frame, report

    stamps, report.tz_aware = _parse_timestamps(frame["timestamp"], label)
    frame = frame.assign(timestamp=stamps)
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype(float)

    frame = _check_order(frame, report, repair)
    report.bad_values = int(_check_values(frame).sum())
    _check_grid_and_gaps(frame, report, timedelta(minutes=TF_MINUTES[timeframe]))

    frame["timestamp"] = frame["timestamp"].dt.strftime(CANONICAL_TS_FORMAT)
    report.rows = int(len(frame))
    report.first = str(frame["timestamp"].iloc[0])
    report.last = str(frame["timestamp"].iloc[-1])
    return frame, report


def _check_order(
    frame: pd.DataFrame, report: CandleValidation, repair: bool
) -> pd.DataFrame:
    """Count out-of-order and duplicate stamps; repair them when asked."""
    report.out_of_order = int((frame["timestamp"].diff() < timedelta(0)).sum())
    report.duplicates = int(frame["timestamp"].duplicated().sum())
    if repair and (report.out_of_order or report.duplicates):
        frame = frame.drop_duplicates("timestamp", keep="first")
        frame = frame.sort_values("timestamp", kind="stable")
        report.repaired = True
        report.notes.append(
            f"repaired: dropped {report.duplicates} duplicate rows, "
            f"re-sorted {report.out_of_order} out-of-order rows"
        )
    return frame.reset_index(drop=True)


def _check_grid_and_gaps(
    frame: pd.DataFrame, report: CandleValidation, step: timedelta
) -> None:
    """Count off-grid stamps and, on an ordered series, missing bars."""
    on_grid = ((frame["timestamp"] - datetime(1970, 1, 1)) % step) == timedelta(0)
    report.off_grid = int((~on_grid).sum())
    ordered = frame["timestamp"].is_monotonic_increasing and not (
        frame["timestamp"].duplicated().any()
    )
    if ordered:
        report.gaps, report.gap_locations = _count_gaps(
            list(frame["timestamp"].dt.to_pydatetime()), step
        )


def load_validated_candles(
    path: Path,
    timeframe: str,
    strict: bool = True,
    repair: bool = True,
) -> Tuple[pd.DataFrame, CandleValidation]:
    """Read a candle file and validate it in one call.

    Args:
        path: Candle file (``.csv``, ``.csv.gz`` or ``.parquet``).
        timeframe: Timeframe the rows are expected to be on.
        strict: Raise :class:`CandleDataError` when anything fatal remains
            after repair. When False the caller gets the frame and the
            report and decides for itself.
        repair: Drop duplicates and re-sort before judging.

    Returns:
        (canonical frame, validation report).

    Raises:
        FileNotFoundError: When the file does not exist.
        CandleDataError: On unreadable input, or on fatal findings when
            ``strict`` is set.
    """
    raw = read_ohlcv_file(path)
    frame, report = validate_candles(raw, timeframe, label=str(path), repair=repair)
    if report.gaps:
        logger.warning(
            f"{path}: {report.gaps} missing {timeframe} bars (first at "
            f"{report.gap_locations[0][0] if report.gap_locations else '?'}); "
            f"gaps are reported, never filled"
        )
    if strict and not report.ok:
        raise CandleDataError(f"{path}: {'; '.join(report.fatal)}")
    logger.info(report.summary())
    return frame, report


def candle_file_for(data_dir: Path, symbol: str, timeframe: str) -> Optional[Path]:
    """Locate the candle file for a symbol/timeframe in a store directory.

    Mirrors the naming the rest of the package uses
    (``{symbol}_{timeframe}.{parquet|csv|csv.gz}``, "/" in the symbol
    replaced by "_"), parquet preferred.

    Args:
        data_dir: Store directory.
        symbol: Trading pair, e.g. "BTC-USDC".
        timeframe: Candle timeframe.

    Returns:
        The first existing path, or None.
    """
    base = f"{symbol.replace('/', '_')}_{timeframe}"
    for suffix in SUPPORTED_SUFFIXES:
        candidate = Path(data_dir) / f"{base}{suffix}"
        if candidate.exists():
            return candidate
    return None
