"""Historical funding schedule for the backtest.

THE INTERVAL MISMATCH
=====================
The only perpetual-funding series with deep, free, keyless history is
Binance USD-M, which settles every **8 hours** (BTCUSDT back to
2019-09-10). The venue this bot trades, Pacifica, settles every **1
hour** - see ``exchanges/base.py::ExchangeCapabilities`` (Pacifica 1,
Blofin 8). Those are different clocks AND, potentially, different
levels: a Pacifica hourly rate is set by Pacifica's own book, not by
Binance's.

Using Binance rates to price Pacifica funding is therefore a MODELLING
ASSUMPTION, not a measurement. The cross-venue basis is unmeasured here.
What this module does is make the assumption explicit, single-sited and
configurable instead of hiding it in an arithmetic constant:

``venue_rate = observed_source_rate * scale * factor(conversion)``

``conversion`` (env ``BACKTEST_FUNDING_CONVERSION``):

* ``prorata`` (default) - ``factor = venue_interval_hours /
  source_interval_hours`` (= 1/8 for Pacifica against Binance). This
  assumes the two venues carry the same ANNUALIZED cost and differ only
  in how finely they slice it. It is the economically neutral reading:
  a perp's funding rate exists to pin the perp to spot, and that pinning
  pressure is a rate per unit TIME, not per settlement event.
* ``identity`` - ``factor = 1``. Assumes Pacifica quotes the same NUMBER
  hourly that Binance quotes 8-hourly, i.e. 8x the annualized carry.
  Almost certainly wrong economically, but it is exactly the assumption
  the shipped flat default (``BACKTEST_FUNDING_HOURLY_PCT=0.0001``, the
  mean Binance 8h BTC rate applied hourly) silently made, so it is kept
  available to reproduce and to bound that error.

``scale`` (env ``BACKTEST_FUNDING_SCALE``, default 1.0) is the knob for
the unmeasured cross-venue basis. It is 1.0 because nobody has measured
it, not because it is known to be 1.0.

CAUSALITY
=========
A settlement published at time T is the rate that was PAID at T. For a
bar at time t the schedule serves the last settlement with
``fundingTime <= t``. That is a lag of up to one source interval against
the rate a live venue would be predicting, and it is deliberate: the
alternative (using the next settlement, which covers the interval the
bar sits inside) is lookahead, and a funding strategy that only works
with lookahead is not a strategy.

The same series drives two different things, and they must not be
confused:

* ``venue_rate_at`` / ``venue_history`` - what a STRATEGY may observe.
* ``FundingSchedule.charge_for`` - what the exchange actually DEBITS an
  open position at each venue settlement time.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

#: Hours between settlements on the ingest source (Binance USD-M).
SOURCE_INTERVAL_HOURS = 8

#: Pro-rata: the observed rate is spread over the venue's finer grid.
CONVERSION_PRORATA = "prorata"
#: Identity: the observed number is used as-is per venue interval.
CONVERSION_IDENTITY = "identity"
CONVERSIONS = (CONVERSION_PRORATA, CONVERSION_IDENTITY)
DEFAULT_CONVERSION = CONVERSION_PRORATA

#: "flat" reproduces the shipped constant-rate model exactly.
#: "historical" charges the ingested series.
FUNDING_MODEL_FLAT = "flat"
FUNDING_MODEL_HISTORICAL = "historical"
FUNDING_MODELS = (FUNDING_MODEL_FLAT, FUNDING_MODEL_HISTORICAL)
#: Default is "flat" ONLY because every published result in this repo
#: was produced under it; switching the default silently would
#: invalidate the campaign without anyone noticing. It is not the
#: better model - see docs/BACKTESTING_GUIDE.md.
DEFAULT_FUNDING_MODEL = FUNDING_MODEL_FLAT

_TS_FORMAT = "%Y-%m-%dT%H:%M:%S"


def validate_conversion(value: Any) -> str:
    """Validate the source-to-venue funding conversion mode.

    Unknown values warn and fall back rather than raising, matching the
    warn-and-fall-back contract used by the engine's policy knobs.

    Args:
        value: Configured value (string or None).

    Returns:
        One of CONVERSIONS.
    """
    mode = str(value or "").strip().lower()
    if mode in CONVERSIONS:
        return mode
    if mode:
        logger.warning(
            f"BACKTEST_FUNDING_CONVERSION={value!r} is not one of "
            f"{'/'.join(CONVERSIONS)}. Falling back to "
            f"'{DEFAULT_CONVERSION}'."
        )
    return DEFAULT_CONVERSION


def validate_funding_model(value: Any) -> str:
    """Validate the BACKTEST_FUNDING_MODEL selector.

    Args:
        value: Configured value (string or None).

    Returns:
        One of FUNDING_MODELS.
    """
    mode = str(value or "").strip().lower()
    if mode in FUNDING_MODELS:
        return mode
    if mode:
        logger.warning(
            f"BACKTEST_FUNDING_MODEL={value!r} is not one of "
            f"{'/'.join(FUNDING_MODELS)}. Falling back to "
            f"'{DEFAULT_FUNDING_MODEL}'."
        )
    return DEFAULT_FUNDING_MODEL


def conversion_factor(
    conversion: str, venue_interval_hours: int, source_interval_hours: int
) -> float:
    """Multiplier turning a source-interval rate into a venue-interval one.

    Args:
        conversion: One of CONVERSIONS.
        venue_interval_hours: Settlement cadence of the traded venue.
        source_interval_hours: Settlement cadence of the ingested source.

    Returns:
        The multiplier. ``prorata`` returns venue/source; ``identity``
        returns 1.0.
    """
    if conversion == CONVERSION_IDENTITY:
        return 1.0
    if source_interval_hours <= 0:
        return 1.0
    return float(venue_interval_hours) / float(source_interval_hours)


@dataclass
class FundingSchedule:
    """A real funding series mapped onto one venue's settlement clock.

    Attributes:
        symbol: Bot symbol the series belongs to.
        times: Source settlement times, ascending, tz-naive UTC.
        rates: Source rates per source interval, aligned with ``times``.
        venue_interval_hours: Settlement cadence of the traded venue.
        conversion: One of CONVERSIONS.
        scale: Cross-venue basis multiplier (unmeasured; default 1.0).
        source_interval_hours: Settlement cadence of the source.
    """

    symbol: str
    times: List[datetime]
    rates: List[float]
    venue_interval_hours: int = 1
    conversion: str = DEFAULT_CONVERSION
    scale: float = 1.0
    source_interval_hours: int = SOURCE_INTERVAL_HOURS

    def __post_init__(self) -> None:
        if self.venue_interval_hours <= 0:
            self.venue_interval_hours = 1
        self.conversion = validate_conversion(self.conversion)
        self._factor = conversion_factor(
            self.conversion,
            self.venue_interval_hours,
            self.source_interval_hours,
        ) * float(self.scale)

    # -- introspection -------------------------------------------------

    @property
    def factor(self) -> float:
        """Total multiplier applied to every observed source rate."""
        return self._factor

    def __len__(self) -> int:
        return len(self.times)

    def describe(self) -> str:
        """One-line summary for run logs."""
        span = (
            f"{self.times[0].strftime(_TS_FORMAT)}.."
            f"{self.times[-1].strftime(_TS_FORMAT)}"
            if self.times
            else "empty"
        )
        return (
            f"{self.symbol}: {len(self.times)} settlements {span} | "
            f"source {self.source_interval_hours}h -> venue "
            f"{self.venue_interval_hours}h via {self.conversion} "
            f"x{self.scale:g} (factor {self._factor:.6g})"
        )

    # -- lookup --------------------------------------------------------

    def _index_at_or_before(self, dt: datetime) -> int:
        """Index of the last settlement at or before ``dt`` (-1 if none)."""
        from bisect import bisect_right

        return bisect_right(self.times, dt) - 1

    def observed_rate_at(self, dt: datetime) -> Optional[float]:
        """Last SETTLED source rate at or before ``dt`` (never lookahead)."""
        idx = self._index_at_or_before(dt)
        if idx < 0:
            return None
        return self.rates[idx]

    def venue_rate_at(self, dt: datetime) -> Optional[float]:
        """Rate per VENUE settlement interval applicable at ``dt``."""
        observed = self.observed_rate_at(dt)
        if observed is None:
            return None
        return observed * self._factor

    def venue_history(self, dt: datetime, limit: int = 8) -> List[Dict[str, Any]]:
        """The last ``limit`` VENUE settlements at or before ``dt``.

        Built on the venue's own grid, so an hourly venue reports one
        record per hour even though the underlying source only moves
        every 8 hours - which is what a live hourly venue would show.

        Args:
            dt: Current simulated time.
            limit: Maximum records, newest last.

        Returns:
            List of ``{"funding_time", "funding_rate", "rate_source"}``
            dicts, oldest first. Empty when ``dt`` precedes the series.
        """
        from datetime import timedelta

        if limit <= 0 or not self.times:
            return []
        step = timedelta(hours=self.venue_interval_hours)
        anchor = dt.replace(minute=0, second=0, microsecond=0)
        anchor -= timedelta(hours=anchor.hour % self.venue_interval_hours)
        out: List[Dict[str, Any]] = []
        for k in range(limit - 1, -1, -1):
            t = anchor - step * k
            rate = self.venue_rate_at(t)
            if rate is None:
                continue
            out.append(
                {
                    "funding_time": t.strftime(_TS_FORMAT),
                    "funding_rate": rate,
                    "rate_source": "binance-8h",
                }
            )
        return out

    def is_settlement_time(self, dt: datetime) -> bool:
        """Whether ``dt`` lands on a venue settlement boundary."""
        if dt.minute != 0:
            return False
        return dt.hour % self.venue_interval_hours == 0

    def charge_for(self, dt: datetime) -> Optional[float]:
        """Rate to debit an open position for the interval ending at ``dt``.

        Returns None when ``dt`` is not a venue settlement time or the
        series does not reach back that far - the caller must then charge
        nothing rather than guess.
        """
        if not self.is_settlement_time(dt):
            return None
        return self.venue_rate_at(dt)


def load_funding_schedule(
    symbol: str,
    data_dir: str,
    venue_interval_hours: int = 1,
    conversion: Optional[str] = None,
    scale: Optional[float] = None,
) -> Optional[FundingSchedule]:
    """Load ``{SYMBOL}_funding.parquet`` and map it onto a venue clock.

    Args:
        symbol: Bot symbol (e.g. "BTC-USDC").
        data_dir: Directory holding the funding parquet.
        venue_interval_hours: Traded venue's settlement cadence.
        conversion: Override for BACKTEST_FUNDING_CONVERSION.
        scale: Override for BACKTEST_FUNDING_SCALE.

    Returns:
        A FundingSchedule, or None when the store is absent or empty.
        The caller decides what "no data" means - this never invents
        rates.
    """
    import pandas as pd

    path = Path(data_dir) / f"{symbol.replace('/', '_')}_funding.parquet"
    if not path.exists():
        logger.warning(
            f"No funding store for {symbol} at {path}. Ingest it with: "
            f"python -m trading_bot_v2.data_manager --symbols {symbol} "
            f"--funding-only --start 2019-09-01 --data-dir {data_dir}"
        )
        return None
    try:
        df = pd.read_parquet(path)
    except (OSError, ValueError) as e:
        logger.error(f"Unreadable funding store {path}: {e}")
        return None
    if df.empty:
        logger.warning(f"Funding store {path} is empty")
        return None
    times = [
        t.to_pydatetime() for t in pd.to_datetime(df["timestamp"], format=_TS_FORMAT)
    ]
    rates = [float(r) for r in df["funding_rate"]]
    if conversion is None:
        conversion = os.getenv("BACKTEST_FUNDING_CONVERSION", DEFAULT_CONVERSION)
    if scale is None:
        try:
            scale = float(os.getenv("BACKTEST_FUNDING_SCALE", "1.0"))
        except ValueError:
            logger.warning("BACKTEST_FUNDING_SCALE is not a number; using 1.0")
            scale = 1.0
    schedule = FundingSchedule(
        symbol=symbol,
        times=times,
        rates=rates,
        venue_interval_hours=venue_interval_hours,
        conversion=validate_conversion(conversion),
        scale=float(scale),
    )
    logger.info(f"Funding schedule loaded | {schedule.describe()}")
    return schedule
