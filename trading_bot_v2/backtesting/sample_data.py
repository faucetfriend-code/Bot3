"""
Synthetic sample candles
========================

Generates a small, deterministic, freely redistributable OHLCV dataset so
the backtest harness can run end to end with no network and no
proprietary data. The committed copy lives in
``trading_bot_v2/backtesting/sample_data/`` and is reproduced exactly by::

    python -m trading_bot_v2.backtesting.sample_data \\
        --out trading_bot_v2/backtesting/sample_data

The price path is a 1-minute geometric random walk stitched from regime
segments (ranging with mean-reverting pull, trending up, trending down)
so that both the ranging strategies (MeanReversion on 15m/5m) and the
trend strategies (MACrossover on 4h) see setups inside a 30-day window.
Higher timeframes are aggregated from the same 1m path, so every
timeframe is self-consistent (open/high/low/close/volume roll up
exactly). Only Python's ``random.Random`` is used, whose sequence is
stable across Python versions, so the output is byte-identical wherever
it is regenerated.

Nothing in this file resembles real market data. The symbol is
``SYN-USDC``; do not draw conclusions about any strategy from it.
"""

from __future__ import annotations

import argparse
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import pandas as pd

from ..data_manager import CANONICAL_TS_FORMAT, TF_MINUTES

DEFAULT_SYMBOL = "SYN-USDC"
DEFAULT_SEED = 7
DEFAULT_DAYS = 30
DEFAULT_START = datetime(2024, 1, 1)
DEFAULT_START_PRICE = 100.0
#: Timeframes committed with the repo. 1m is the generator's native grid
#: but is left out of the committed sample (43k rows for 30 days); pass
#: ``--timeframes 1m,5m,15m,1h,4h`` to write it as well.
DEFAULT_TIMEFRAMES: Tuple[str, ...] = ("5m", "15m", "1h", "4h")


@dataclass(frozen=True)
class RegimeSegment:
    """One stretch of the synthetic path.

    Attributes:
        days: Length of the segment in days.
        drift_per_day: Expected log-return per day (0 for ranging).
        vol_per_minute: Standard deviation of the 1m log-return.
        reversion: Pull toward the segment's anchor price per minute, as
            a fraction of the log-distance (0 = pure random walk).
    """

    days: float
    drift_per_day: float
    vol_per_minute: float
    reversion: float = 0.0


@dataclass(frozen=True)
class SampleSpec:
    """Everything that determines the generated dataset."""

    symbol: str = DEFAULT_SYMBOL
    seed: int = DEFAULT_SEED
    start: datetime = DEFAULT_START
    start_price: float = DEFAULT_START_PRICE
    base_volume: float = 1500.0
    segments: Tuple[RegimeSegment, ...] = field(
        default_factory=lambda: (
            RegimeSegment(
                days=7, drift_per_day=0.0, vol_per_minute=0.0006, reversion=0.004
            ),
            RegimeSegment(
                days=8, drift_per_day=0.012, vol_per_minute=0.0010, reversion=0.002
            ),
            RegimeSegment(
                days=6, drift_per_day=0.0, vol_per_minute=0.0007, reversion=0.004
            ),
            RegimeSegment(
                days=9, drift_per_day=-0.010, vol_per_minute=0.0012, reversion=0.002
            ),
        )
    )

    @property
    def days(self) -> float:
        """Total span in days."""
        return sum(s.days for s in self.segments)


#: Hourly log-activity AR(1): ``a_t = ACTIVITY_PERSISTENCE * a_{t-1} + noise``.
#: Activity scales volume (fully) and volatility (by its square root), so
#: volume and range cluster the way they do on a real venue instead of
#: averaging out to a flat 1.0x ratio on every higher-timeframe bar.
ACTIVITY_PERSISTENCE = 0.85
ACTIVITY_NOISE = 0.45


def _minute_bars(spec: SampleSpec) -> List[Dict[str, Any]]:
    """Walk the 1m price path and build one OHLCV row per minute."""
    rng = random.Random(spec.seed)
    rows: List[Dict[str, Any]] = []
    price = spec.start_price
    stamp = spec.start
    activity = 0.0
    for segment in spec.segments:
        anchor = math.log(price)
        drift = segment.drift_per_day / 1440.0
        for minute in range(int(round(segment.days * 1440))):
            if minute % 60 == 0:
                activity = ACTIVITY_PERSISTENCE * activity + rng.gauss(
                    0.0, ACTIVITY_NOISE
                )
            scale = math.exp(0.5 * activity)
            shock = rng.gauss(0.0, segment.vol_per_minute * scale)
            pull = -segment.reversion * (math.log(price) - anchor)
            close = price * math.exp(drift + pull + shock)
            rows.append(_bar(rng, stamp, price, close, spec, segment, activity))
            price = close
            stamp += timedelta(minutes=1)
    return rows


def _bar(
    rng: random.Random,
    stamp: datetime,
    open_: float,
    close: float,
    spec: SampleSpec,
    segment: RegimeSegment,
    activity: float,
) -> Dict[str, Any]:
    """One 1m candle around an open/close pair."""
    wick = segment.vol_per_minute * 0.6 * math.exp(0.5 * activity)
    high = max(open_, close) * (1.0 + abs(rng.gauss(0.0, wick)))
    low = min(open_, close) * (1.0 - abs(rng.gauss(0.0, wick)))
    move = abs(math.log(close / open_)) / max(segment.vol_per_minute, 1e-9)
    volume = (
        spec.base_volume * math.exp(activity + rng.gauss(0.0, 0.4)) * (1.0 + 0.5 * move)
    )
    return {
        "timestamp": stamp,
        "open": round(open_, 2),
        "high": round(high, 2),
        "low": round(low, 2),
        "close": round(close, 2),
        "volume": round(volume, 2),
    }


def _aggregate(minute: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Roll 1m rows up to a higher timeframe on the epoch-aligned grid."""
    step = TF_MINUTES[timeframe]
    if step == 1:
        out = minute.copy()
    else:
        bucket = minute["timestamp"].dt.floor(f"{step}min")
        grouped = minute.groupby(bucket, sort=True)
        out = pd.DataFrame(
            {
                "open": grouped["open"].first(),
                "high": grouped["high"].max(),
                "low": grouped["low"].min(),
                "close": grouped["close"].last(),
                "volume": grouped["volume"].sum().round(2),
            }
        ).reset_index()
    out["timestamp"] = out["timestamp"].dt.strftime(CANONICAL_TS_FORMAT)
    return out[["timestamp", "open", "high", "low", "close", "volume"]]


def generate_sample_candles(
    spec: SampleSpec = SampleSpec(),
    timeframes: Iterable[str] = DEFAULT_TIMEFRAMES,
) -> Dict[str, pd.DataFrame]:
    """Generate the synthetic dataset for the requested timeframes.

    Args:
        spec: Generation parameters; the default reproduces the committed
            sample.
        timeframes: Subset of ``TF_MINUTES`` to produce.

    Returns:
        Mapping of timeframe to canonical candle frame (timestamps as
        naive-UTC strings, OHLCV as floats, oldest first).
    """
    minute = pd.DataFrame(_minute_bars(spec))
    return {tf: _aggregate(minute, tf) for tf in timeframes}


def write_sample_dataset(
    out_dir: Path,
    spec: SampleSpec = SampleSpec(),
    timeframes: Sequence[str] = DEFAULT_TIMEFRAMES,
    gzip: bool = True,
) -> List[Path]:
    """Generate and write the dataset as ``{symbol}_{tf}.csv[.gz]`` files.

    Args:
        out_dir: Directory to write into (created if missing).
        spec: Generation parameters.
        timeframes: Timeframes to write.
        gzip: Write ``.csv.gz`` (the committed form) instead of ``.csv``.

    Returns:
        The paths written, in timeframe order.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    suffix = ".csv.gz" if gzip else ".csv"
    for tf, frame in generate_sample_candles(spec, timeframes).items():
        path = out_dir / f"{spec.symbol}_{tf}{suffix}"
        # mtime=0 keeps the gzip header deterministic so the committed
        # bytes do not churn on every regeneration.
        compression = {"method": "gzip", "mtime": 0} if gzip else None
        frame.to_csv(path, index=False, compression=compression, lineterminator="\n")
        written.append(path)
    return written


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: regenerate the sample dataset."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--out", required=True, help="Directory to write into")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--timeframes",
        default=",".join(DEFAULT_TIMEFRAMES),
        help="Comma-separated subset of 1m,5m,15m,1h,4h",
    )
    parser.add_argument(
        "--plain-csv", action="store_true", help="Write .csv instead of .csv.gz"
    )
    args = parser.parse_args(argv)
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    unknown = [t for t in timeframes if t not in TF_MINUTES]
    if unknown:
        parser.error(f"unknown timeframes {unknown}; expected {list(TF_MINUTES)}")
    spec = SampleSpec(symbol=args.symbol, seed=args.seed)
    for path in write_sample_dataset(
        Path(args.out), spec, timeframes, gzip=not args.plain_csv
    ):
        print(f"wrote {path} ({path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
