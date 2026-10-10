"""
Run the backtest harness from the command line.

The defaults run the committed synthetic sample end to end with no
network access and write a JSON + text report::

    python -m trading_bot_v2.backtesting.run_harness

Typical variations::

    # Another window, one strategy, custom report location
    python -m trading_bot_v2.backtesting.run_harness \\
        --start 2024-01-10 --end 2024-01-30 --strategies ma_crossover \\
        --output-dir /tmp/harness

    # Your own CSV/Parquet store (files named {SYMBOL}_{tf}.csv|csv.gz|parquet)
    python -m trading_bot_v2.backtesting.run_harness \\
        --data-dir trading_bot_v2/backtesting/data --symbol BTC-USDC \\
        --start 2024-03-01 --end 2024-03-21 --strategies mean_reversion

    # The live StrategyManager path instead of the direct adapter replay
    python -m trading_bot_v2.backtesting.run_harness --mode pipeline ...

    # What can be driven
    python -m trading_bot_v2.backtesting.run_harness --list-strategies

See docs/BACKTEST-HARNESS.md for what the two modes measure.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Sequence

from loguru import logger

from .harness import DEFAULT_OUTPUT_DIR, MODES, HarnessConfig, run_harness
from .ohlcv import CandleDataError
from .sample_data import DEFAULT_SYMBOL
from .strategy_interface import STRATEGY_REGISTRY

if TYPE_CHECKING:
    from loguru import Record

SAMPLE_DATA_DIR = Path(__file__).resolve().parent / "sample_data"
#: The committed sample spans 2024-01-01 .. 2024-01-30. Starting on the
#: 8th leaves a week of warmup so the 4h indicators are saturated.
SAMPLE_START = "2024-01-08"
SAMPLE_END = "2024-01-30"


def build_parser() -> argparse.ArgumentParser:
    """Argument parser for the harness CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m trading_bot_v2.backtesting.run_harness",
        description="Run an offline backtest from local candle files to a report.",
    )
    parser.add_argument("--data-dir", default=str(SAMPLE_DATA_DIR))
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--start", default=SAMPLE_START)
    parser.add_argument("--end", default=SAMPLE_END)
    parser.add_argument(
        "--strategies",
        default="mean_reversion,ma_crossover",
        help="Comma-separated registry keys or display names (see --list-strategies)",
    )
    parser.add_argument("--mode", choices=MODES, default="direct")
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument(
        "--lookback", type=int, default=None, help="History candles per timeframe"
    )
    parser.add_argument(
        "--warmup", type=int, default=None, help="Pre-window candles per timeframe"
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--basename", default=None, help="Report file stem")
    parser.add_argument(
        "--no-write", action="store_true", help="Print the summary, write nothing"
    )
    parser.add_argument(
        "--params",
        default=None,
        help=(
            "JSON object of constructor overrides per strategy key, e.g. "
            '\'{"ma_crossover": {"fast_ma_period": 10, "slow_ma_period": 30}}\''
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="loguru level for the harness's own modules (default INFO)",
    )
    parser.add_argument(
        "--strategy-log-level",
        default="ERROR",
        help="loguru level for strategy and engine modules (default ERROR)",
    )
    parser.add_argument("--list-strategies", action="store_true")
    return parser


def _parse_params(raw: Optional[str]) -> Dict[str, Dict[str, Any]]:
    """Parse the ``--params`` JSON object.

    Raises:
        ValueError: When it is not a JSON object of objects.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--params is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not all(
        isinstance(v, dict) for v in parsed.values()
    ):
        raise ValueError("--params must be a JSON object keyed by strategy")
    return parsed


def _list_strategies() -> None:
    """Print the registry."""
    width = max(len(k) for k in STRATEGY_REGISTRY)
    for key, spec in STRATEGY_REGISTRY.items():
        print(
            f"{key:<{width}}  {spec.display_name:<18} {spec.primary_timeframe:>3}  "
            f"{spec.description}"
        )


def _configure_logging(level: str, strategy_level: str) -> None:
    """Route loguru to stderr: harness modules at ``level``, the rest higher.

    The strategies log a WARNING per low-confidence signal, which on a
    busy replay is thousands of lines that drown the data-validation
    warnings the harness itself emits. Two thresholds keep both visible
    on demand.

    Args:
        level: Level for ``trading_bot_v2.backtesting.*`` records.
        strategy_level: Level for every other module.
    """
    harness_no = logger.level(level.upper()).no
    other_no = logger.level(strategy_level.upper()).no

    def _keep(record: Record) -> bool:
        threshold = (
            harness_no
            if str(record["name"]).startswith("trading_bot_v2.backtesting")
            else other_no
        )
        return record["level"].no >= threshold

    logger.remove()
    logger.add(sys.stderr, level=min(harness_no, other_no), filter=_keep)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point.

    Returns:
        0 on success, 2 on a data/configuration error.
    """
    args = build_parser().parse_args(argv)
    if args.list_strategies:
        _list_strategies()
        return 0
    _configure_logging(args.log_level, args.strategy_log_level)
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    try:
        cfg = HarnessConfig(
            data_dir=Path(args.data_dir),
            symbol=args.symbol,
            start=args.start,
            end=args.end,
            strategies=strategies,
            mode=args.mode,
            initial_capital=args.capital,
            lookback=args.lookback,
            warmup=args.warmup,
            strategy_params=_parse_params(args.params),
            output_dir=None if args.no_write else Path(args.output_dir),
            basename=args.basename,
        )
        run = run_harness(cfg)
    except (FileNotFoundError, CandleDataError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(run.summary, end="")
    if run.json_path is not None:
        print(f"Report written: {run.json_path}")
        print(f"Summary written: {run.txt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
