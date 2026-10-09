"""
Offline regime dwell-time analysis CLI.

Replays historical 4h candles through the MarketRegimeDetector (including
the Jul 2026 hysteresis bands, 2-count confirmation, and minimum dwell
time) and reports regime stability statistics: transition timeline,
dwell-time distribution, flips per week, and percentage of time spent in
each regime.

Pure analysis - no database writes and no event emission (the detector is
constructed without an event bus or db).

Usage:
    python -m trading_bot_v2.analysis.regime_stability \
        --symbol SUI-USDC --start 2024-01-01 --end 2024-12-31
"""

import argparse
import os
import statistics
from datetime import datetime
from typing import Any, Callable, Dict, List, cast

from trading_bot_v2.backtesting.data_loader import BacktestDataLoader
from trading_bot_v2.market_regime import MarketRegimeDetector

# Candles fed to each detection call. Must cover the ADX/ATR/BB minimums
# (29) plus the 100-period ATR percentile lookback used by the volatility
# score, so vol percentiles are stable.
WINDOW_BARS = 150

# Minimum bars before the first detection (matches detector requirement).
MIN_BARS = 29


def _parse_timestamp(raw: Any) -> datetime:
    """Parse a candle timestamp (ISO string or epoch seconds/ms)."""
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        # Heuristic: epoch ms vs s
        value = float(raw)
        if value > 1e12:
            value /= 1000.0
        return datetime.fromtimestamp(value)
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[: len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(text)


def run_analysis(symbol: str, start: str, end: str, data_dir: str) -> Dict[str, Any]:
    """Replay 4h candles through the detector and collect regime stats.

    Args:
        symbol: Trading symbol, e.g. "SUI-USDC".
        start: Inclusive start date (YYYY-MM-DD).
        end: Inclusive end date (YYYY-MM-DD).
        data_dir: Directory containing <symbol>_4h.csv or .parquet.

    Returns:
        Dict with timeline (confirmed transitions), dwell stats per regime,
        flips per week, and pct of time per regime.
    """
    loader = BacktestDataLoader(symbol=symbol, data_dir=data_dir)
    candles = loader.get_candles("4h", start=start, end=end)

    closes = candles["close"]
    n = len(closes)
    if n < MIN_BARS + 1:
        raise SystemExit(
            f"Not enough 4h candles for {symbol} in [{start}, {end}]: "
            f"got {n}, need at least {MIN_BARS + 1}"
        )

    timestamps = [_parse_timestamp(t) for t in candles["timestamp"]]

    detector = MarketRegimeDetector()  # no event bus / db: pure analysis

    bar_regimes: List[str] = []
    bar_times: List[datetime] = []
    transitions: List[Dict[str, Any]] = []
    prev_confirmed = None

    for i in range(MIN_BARS, n):
        lo = max(0, i + 1 - WINDOW_BARS)
        window = {
            "high": candles["high"][lo : i + 1],
            "low": candles["low"][lo : i + 1],
            "close": closes[lo : i + 1],
            "volume": candles["volume"][lo : i + 1],
        }
        bar_time = timestamps[i]
        detector._clock = cast(Callable[[], datetime], lambda t=bar_time: t)
        regime = detector.detect_regime_cached(symbol, window)

        bar_regimes.append(regime.value)
        bar_times.append(bar_time)

        if prev_confirmed is not None and regime != prev_confirmed:
            transitions.append(
                {
                    "time": bar_time.isoformat(),
                    "old": prev_confirmed.value,
                    "new": regime.value,
                    "adx": detector._last_calculated_adx,
                }
            )
        prev_confirmed = regime

    # Dwell segments: consecutive runs of the same confirmed regime
    segments: List[Dict[str, Any]] = []
    seg_start = 0
    for i in range(1, len(bar_regimes) + 1):
        if i == len(bar_regimes) or bar_regimes[i] != bar_regimes[seg_start]:
            duration_h = (
                bar_times[i - 1] - bar_times[seg_start]
            ).total_seconds() / 3600.0 + 4.0  # each bar covers 4h
            segments.append(
                {
                    "regime": bar_regimes[seg_start],
                    "start": bar_times[seg_start],
                    "hours": duration_h,
                }
            )
            seg_start = i

    dwell_by_regime: Dict[str, List[float]] = {}
    for seg in segments:
        dwell_by_regime.setdefault(seg["regime"], []).append(seg["hours"])

    dwell_stats = {
        regime: {
            "segments": len(hours),
            "median_h": statistics.median(hours),
            "mean_h": statistics.fmean(hours),
            "min_h": min(hours),
            "max_h": max(hours),
        }
        for regime, hours in dwell_by_regime.items()
    }

    total_hours = len(bar_regimes) * 4.0
    total_weeks = total_hours / (24.0 * 7.0)
    pct_time = {
        regime: 100.0 * sum(hours) / total_hours
        for regime, hours in dwell_by_regime.items()
    }
    flips_per_week = len(transitions) / total_weeks if total_weeks > 0 else 0.0

    return {
        "symbol": symbol,
        "start": start,
        "end": end,
        "bars": len(bar_regimes),
        "total_hours": total_hours,
        "transitions": transitions,
        "dwell_stats": dwell_stats,
        "pct_time": pct_time,
        "flips_per_week": flips_per_week,
    }


def print_report(result: Dict[str, Any]) -> None:
    """Print a human-readable regime stability report."""
    print("=" * 68)
    print(
        f"Regime stability: {result['symbol']} "
        f"{result['start']} -> {result['end']} "
        f"({result['bars']} x 4h bars, {result['total_hours']:.0f}h)"
    )
    print("=" * 68)

    transitions = result["transitions"]
    print(
        f"\nConfirmed transitions: {len(transitions)} "
        f"({result['flips_per_week']:.2f} flips/week)"
    )
    for t in transitions:
        adx = f"{t['adx']:.1f}" if t["adx"] is not None else "n/a"
        print(f"  {t['time']}  {t['old']} -> {t['new']}  (ADX={adx})")

    print("\nDwell-time distribution (hours per continuous segment):")
    header = (
        f"  {'regime':<20} {'segs':>5} {'median':>8} {'mean':>8} {'min':>7} {'max':>8}"
    )
    print(header)
    for regime, s in sorted(result["dwell_stats"].items()):
        print(
            f"  {regime:<20} {s['segments']:>5} {s['median_h']:>8.1f} "
            f"{s['mean_h']:>8.1f} {s['min_h']:>7.1f} {s['max_h']:>8.1f}"
        )

    print("\nPct of time per regime:")
    for regime, pct in sorted(result["pct_time"].items(), key=lambda kv: -kv[1]):
        print(f"  {regime:<20} {pct:>6.1f}%")
    print()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Offline regime dwell-time analysis (4h candles)"
    )
    parser.add_argument("--symbol", default="SUI-USDC")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument(
        "--data-dir",
        default=os.getenv("BACKTEST_DATA_DIR", "trading_bot_v2/backtesting/data"),
    )
    args = parser.parse_args()

    result = run_analysis(args.symbol, args.start, args.end, args.data_dir)
    print_report(result)


if __name__ == "__main__":
    main()
