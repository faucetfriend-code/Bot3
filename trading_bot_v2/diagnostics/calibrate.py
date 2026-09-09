"""
Gate-Metric Calibration Pass
============================

Replays real candles, calls every calibrated strategy's
``describe_gate_metrics`` hook once per bar, and writes the observed
distribution of each gating metric to a committed JSON artifact under
``diagnostics/calibration/``.

Those artifacts are what turns "this threshold is unreachable" from a
multi-hour manual investigation into a pruned Optuna trial. See
:mod:`trading_bot_v2.diagnostics.gate_metrics` for the schema and for how
``optimization/search_spaces.py`` consumes them.

Usage:
    python -m trading_bot_v2.diagnostics.calibrate \\
        --symbols BTC-USDC,ETH-USDC,SUI-USDC \\
        --strategies vwap_scalping,momentum_scalping,ma_crossover \\
        --start 2024-01-01 --end 2026-07-01

Two operational notes, both learned the hard way:

* ``.env`` sets ``BACKTEST_DATA_DIR`` to a RELATIVE path and
  ``load_dotenv(override=True)`` clobbers shell exports, so a run from a
  git worktree resolves to that worktree's own parquet-less directory.
  Pass ``--data-dir`` with an absolute path when you are not in the main
  checkout.
* Set ``DATA_AUTODOWNLOAD=false`` so a slow or failing network fetch
  cannot perturb the replay. That variable is NOT set by ``.env``, so a
  shell export does work for it.

Fidelity
--------

The bundles handed to the hook are built with the backtest engine's own
static slicing helpers (``_history``, ``_nearest_idx``,
``_first_index_at_or_after``), so a calibrated metric is measured from
exactly the window the strategy sees in a real backtest. That includes
the rolling history budget: a cumulative VWAP over 60 candles has a
different sigma than one over 250, so ``BACKTEST_HISTORY_LOOKBACK`` is
recorded in the artifact's provenance and an artifact is only valid for
the lookback it was built at.

Each strategy is replayed at ITS OWN primary timeframe, not at the
engine's 5m clock, except where the metric genuinely refreshes faster:
``deviation_sd`` moves with price, so VWAP is replayed at 5m (the same
cadence that produced the 52,041-bar evidence in the original
investigation), while momentum runs at 1h and MA crossover at 4h.
"""

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from ..backtesting.data_loader import BacktestDataLoader
from ..backtesting.engine import BacktestEngine
from ..strategies.ma_crossover import MACrossoverStrategy
from ..strategies.momentum_scalping import MomentumScalpingStrategy
from ..strategies.vwap_scalping import VWAPScalpingStrategy
from .gate_metrics import (
    GateMetricCollector,
    SEVERITY_UNREACHABLE,
    artifact_path,
    threshold_verdicts,
    write_artifact,
)

#: Mirrors backtesting/engine.py's default rolling history budget.
DEFAULT_HISTORY_LOOKBACK = 60

#: Default calibration window. Chosen as the longest span all three
#: maintained symbols cover (SUI's store starts 2023-05-03) while staying
#: cheap enough to re-run on demand.
DEFAULT_START = "2024-01-01"
DEFAULT_END = "2026-07-01"


@dataclass(frozen=True)
class StrategySpec:
    """How to replay one strategy for calibration.

    Attributes:
        driver: Timeframe whose bars advance the replay.
        structure: Timeframes bundled into ``multi_tf_data``.
        execution: Timeframes bundled into ``execution_tf_data``.
        factory: Builds the strategy from the shipped configuration.
        params: Extracts the parameter snapshot for provenance.
    """

    driver: str
    structure: Tuple[str, ...]
    execution: Tuple[str, ...]
    factory: Callable[[], Any]
    params: Callable[[Any], Dict[str, Any]]


def _build_momentum() -> MomentumScalpingStrategy:
    """Construct MomentumScalping from .env, as StrategyManager does.

    Unlike the other two, this strategy reads nothing from the
    environment itself - StrategyManager does it on its behalf - so the
    same reads are mirrored here. Calibrating a default-constructed
    instance would record thresholds nobody runs.

    Returns:
        A strategy configured exactly as the live bot would build it.
    """
    return MomentumScalpingStrategy(
        ema_fast=int(os.getenv("MOMENTUM_EMA_FAST", "9")),
        ema_slow=int(os.getenv("MOMENTUM_EMA_SLOW", "21")),
        rsi_period=int(os.getenv("MOMENTUM_RSI_PERIOD", "14")),
        rsi_lower=float(os.getenv("MOMENTUM_RSI_OVERSOLD", "35")),
        rsi_upper=float(os.getenv("MOMENTUM_RSI_OVERBOUGHT", "65")),
        atr_period=int(os.getenv("MOMENTUM_ATR_PERIOD", "14")),
        atr_stop_mult=float(os.getenv("MOMENTUM_ATR_STOP_MULTIPLIER", "1.5")),
        atr_target_mult=float(os.getenv("MOMENTUM_ATR_TARGET_MULTIPLIER", "2.5")),
        min_confidence=float(os.getenv("MOMENTUM_MIN_CONFIDENCE", "0.60")),
        cooldown_minutes=int(os.getenv("MOMENTUM_COOLDOWN_MINUTES", "5")),
        volume_threshold=float(os.getenv("MOMENTUM_VOLUME_MULTIPLIER", "1.2")),
        macd_fast=int(os.getenv("MOMENTUM_MACD_FAST", "12")),
        macd_slow=int(os.getenv("MOMENTUM_MACD_SLOW", "26")),
        macd_signal=int(os.getenv("MOMENTUM_MACD_SIGNAL", "9")),
        min_atr_pct=float(os.getenv("MOMENTUM_MIN_ATR_PCT", "0.0")),
        min_rrr=float(os.getenv("MOMENTUM_MIN_RRR", "1.5")),
    )


def _vwap_params(strategy: Any) -> Dict[str, Any]:
    """Parameter snapshot for VWAP scalping."""
    return {
        "sd_entry_threshold": strategy.sd_entry_threshold,
        "atr_period": strategy.atr_period,
        "min_confidence": strategy.min_confidence,
    }


def _momentum_params(strategy: Any) -> Dict[str, Any]:
    """Parameter snapshot for momentum scalping."""
    return {
        "ema_fast": strategy.ema_fast,
        "ema_slow": strategy.ema_slow,
        "atr_period": strategy.atr_period,
        "atr_stop_mult": strategy.atr_stop_mult,
        "atr_target_mult": strategy.atr_target_mult,
        "volume_threshold": strategy.volume_threshold,
        "min_atr_pct": strategy.min_atr_pct,
        "min_rrr": strategy.min_rrr,
    }


def _ma_params(strategy: Any) -> Dict[str, Any]:
    """Parameter snapshot for MA crossover."""
    return {
        "fast_ma_period": strategy.fast_ma_period,
        "slow_ma_period": strategy.slow_ma_period,
        "min_entry_bars": strategy.min_entry_bars,
        "max_entry_bars": strategy.max_entry_bars,
        "pullback_range_min": strategy.pullback_range[0],
        "pullback_range_max": strategy.pullback_range[1],
    }


#: Strategies with a ``describe_gate_metrics`` hook, and how to drive it.
CALIBRATED_STRATEGIES: Dict[str, StrategySpec] = {
    # deviation_sd moves with price, so replay at the engine's 5m clock.
    "vwap_scalping": StrategySpec(
        driver="5m",
        structure=("15m",),
        execution=("5m",),
        factory=VWAPScalpingStrategy,
        params=_vwap_params,
    ),
    # 1h primary; nothing in its gates refreshes faster.
    "momentum_scalping": StrategySpec(
        driver="1h",
        structure=("1h",),
        execution=(),
        factory=_build_momentum,
        params=_momentum_params,
    ),
    # 4h primary; both band gates are defined on 4h bars.
    "ma_crossover": StrategySpec(
        driver="4h",
        structure=("4h",),
        execution=(),
        factory=MACrossoverStrategy,
        params=_ma_params,
    ),
}


def default_data_dir() -> str:
    """Return the candle store path, honouring BACKTEST_DATA_DIR.

    Returns:
        The configured directory. Relative paths resolve against the
        current working directory, which is why ``--data-dir`` exists.
    """
    return os.getenv("BACKTEST_DATA_DIR", "trading_bot_v2/backtesting/data")


def history_lookback() -> int:
    """Return the rolling history budget to replay with.

    Returns:
        BACKTEST_HISTORY_LOOKBACK, or the engine default.
    """
    raw = os.getenv("BACKTEST_HISTORY_LOOKBACK")
    try:
        value = int(raw) if raw else DEFAULT_HISTORY_LOOKBACK
    except ValueError:
        value = DEFAULT_HISTORY_LOOKBACK
    return max(1, value)


def calibrate_strategy(
    symbol: str,
    strategy_name: str,
    start: str = DEFAULT_START,
    end: str = DEFAULT_END,
    data_dir: Optional[str] = None,
    lookback: Optional[int] = None,
) -> Dict[str, Any]:
    """Run one (symbol, strategy) calibration and return its artifact.

    Args:
        symbol: Symbol to calibrate (e.g. "BTC-USDC").
        strategy_name: Key in :data:`CALIBRATED_STRATEGIES`.
        start: Window start (ISO date).
        end: Window end (ISO date).
        data_dir: Candle store. Defaults to BACKTEST_DATA_DIR.
        lookback: Rolling history budget. Defaults to the engine's.

    Returns:
        The artifact payload (not yet written to disk).

    Raises:
        KeyError: If the strategy has no calibration spec.
    """
    spec = CALIBRATED_STRATEGIES[strategy_name]
    lookback = lookback or history_lookback()
    resolved_dir = data_dir or default_data_dir()

    timeframes: List[str] = []
    for timeframe in (spec.driver,) + spec.structure + spec.execution:
        if timeframe not in timeframes:
            timeframes.append(timeframe)

    loader = BacktestDataLoader(symbol=symbol, data_dir=resolved_dir)
    candles = {
        timeframe: loader.get_candles(timeframe, start, end, warmup_candles=lookback)
        for timeframe in timeframes
    }

    driver_ts = candles[spec.driver]["timestamp"]
    driver_close = candles[spec.driver]["close"]
    if not driver_ts:
        raise ValueError(
            f"No {spec.driver} candles for {symbol} in {start}..{end} "
            f"(data_dir={resolved_dir!r}) - point --data-dir at the real "
            f"store, it is not in git"
        )

    others = [tf for tf in timeframes if tf != spec.driver]
    sorted_ts = {tf: [str(t) for t in candles[tf]["timestamp"]] for tf in others}
    idx_map = {
        tf: {ts: i for i, ts in enumerate(candles[tf]["timestamp"])} for tf in others
    }

    strategy = spec.factory()
    collector = GateMetricCollector(strategy=strategy_name, symbol=symbol)
    replay_start = BacktestEngine._first_index_at_or_after(driver_ts, start)

    for i in range(replay_start, len(driver_ts)):
        stamp = driver_ts[i]
        multi_tf_data = {}
        for timeframe in spec.structure:
            index = (
                i
                if timeframe == spec.driver
                else BacktestEngine._nearest_idx(
                    idx_map[timeframe], sorted_ts[timeframe], stamp
                )
            )
            multi_tf_data[timeframe] = BacktestEngine._history(
                candles[timeframe], index, lookback
            )
        execution_tf_data = {}
        for timeframe in spec.execution:
            index = (
                i
                if timeframe == spec.driver
                else BacktestEngine._nearest_idx(
                    idx_map[timeframe], sorted_ts[timeframe], stamp
                )
            )
            execution_tf_data[timeframe] = BacktestEngine._history(
                candles[timeframe], index, lookback
            )
        collector.observe(
            strategy.describe_gate_metrics(
                symbol,
                multi_tf_data,
                driver_close[i],
                execution_tf_data or None,
            )
        )

    provenance = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_start": start,
        "window_end": end,
        "driver_timeframe": spec.driver,
        "structure_timeframes": list(spec.structure),
        "execution_timeframes": list(spec.execution),
        "history_lookback": lookback,
        "bars_observed": collector.bars_observed,
        "params_at_calibration": spec.params(strategy),
    }
    return collector.to_artifact(provenance)


def _format_metric_row(name: str, record: Dict[str, Any]) -> str:
    """Render one metric as a console line.

    Args:
        name: Metric name.
        record: Distribution record.

    Returns:
        A single fixed-width line.
    """
    if not record.get("count"):
        return f"  {name:<20} (no observations)"
    return (
        f"  {name:<20} n={record['count']:<8} "
        f"min={record['min']:<12.6g} p99={record.get('p99', 0):<12.6g} "
        f"max={record['max']:<12.6g} pass={record.get('pass_rate', 0):.4f}"
    )


def run(
    symbols: Sequence[str],
    strategies: Sequence[str],
    start: str,
    end: str,
    data_dir: Optional[str],
    out_dir: Optional[Path],
    lookback: Optional[int] = None,
) -> int:
    """Calibrate every (symbol, strategy) pair and write the artifacts.

    Args:
        symbols: Symbols to calibrate.
        strategies: Strategy keys.
        start: Window start.
        end: Window end.
        data_dir: Candle store override.
        out_dir: Artifact directory override.
        lookback: Rolling history budget override.

    Returns:
        Process exit code (0 on success, 1 when nothing was produced).
    """
    written = 0
    for strategy_name in strategies:
        if strategy_name not in CALIBRATED_STRATEGIES:
            print(
                f"skip {strategy_name}: no describe_gate_metrics hook",
                file=sys.stderr,
            )
            continue
        for symbol in symbols:
            print(f"\n=== {symbol} / {strategy_name} {start}..{end} ===")
            try:
                payload = calibrate_strategy(
                    symbol=symbol,
                    strategy_name=strategy_name,
                    start=start,
                    end=end,
                    data_dir=data_dir,
                    lookback=lookback,
                )
            except (ValueError, KeyError, OSError) as exc:
                print(f"  FAILED: {exc}", file=sys.stderr)
                continue

            metrics = payload.get("metrics") or {}
            bars = payload["provenance"]["bars_observed"]
            print(f"  bars replayed: {bars}")
            for name, record in metrics.items():
                print(_format_metric_row(name, record))

            path = artifact_path(strategy_name, symbol, out_dir)
            write_artifact(payload, path)
            written += 1
            print(f"  wrote {path}")

            # Immediately judge the CONFIGURED thresholds against what we
            # just measured. A live unreachable threshold is the whole
            # point of this pass, so it must not wait for an optimizer run.
            params = payload["provenance"]["params_at_calibration"]
            hard, warnings = threshold_verdicts(
                strategy_name,
                params,
                symbol=symbol,
                directory=out_dir,
            )
            for verdict in hard:
                print(f"  UNREACHABLE: {verdict.reason}")
            for verdict in warnings:
                if verdict.severity == SEVERITY_UNREACHABLE:
                    continue
                print(f"  NEAR CEILING: {verdict.reason}")

    return 0 if written else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(
        prog="python -m trading_bot_v2.diagnostics.calibrate",
        description=(
            "Record each gating metric's observed distribution to a "
            "committed JSON artifact, so an unreachable threshold is "
            "caught before a trial burns a backtest."
        ),
    )
    parser.add_argument(
        "--symbols",
        default="BTC-USDC,ETH-USDC,SUI-USDC",
        help="Comma-separated symbols.",
    )
    parser.add_argument(
        "--strategies",
        default=",".join(sorted(CALIBRATED_STRATEGIES)),
        help="Comma-separated strategy keys.",
    )
    parser.add_argument("--start", default=DEFAULT_START)
    parser.add_argument("--end", default=DEFAULT_END)
    parser.add_argument(
        "--data-dir",
        default=None,
        help=(
            "Candle store. Pass an ABSOLUTE path from a git worktree: "
            ".env sets a relative BACKTEST_DATA_DIR and clobbers shell "
            "exports."
        ),
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Artifact directory (default: diagnostics/calibration).",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=None,
        help="Rolling history budget (default: BACKTEST_HISTORY_LOOKBACK).",
    )
    args = parser.parse_args(argv)

    return run(
        symbols=[s.strip() for s in args.symbols.split(",") if s.strip()],
        strategies=[s.strip() for s in args.strategies.split(",") if s.strip()],
        start=args.start,
        end=args.end,
        data_dir=args.data_dir,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        lookback=args.lookback,
    )


if __name__ == "__main__":
    raise SystemExit(main())
