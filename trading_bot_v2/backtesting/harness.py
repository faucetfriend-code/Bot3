"""
Backtest harness
================

One entry point that runs a backtest end to end, offline, from local
candle files to a written report::

    from trading_bot_v2.backtesting.harness import HarnessConfig, run_harness

    run = run_harness(HarnessConfig(
        data_dir="trading_bot_v2/backtesting/sample_data",
        symbol="SYN-USDC", start="2024-01-08", end="2024-01-30",
        strategies=["mean_reversion", "ma_crossover"],
    ))
    print(run.summary)

Two replay modes share the same loader, exchange, cost model, fill
accounting, funding model and metrics:

``direct``
    Each registered strategy is driven through
    :class:`~.strategy_interface.StrategyAdapter` on every 5m bar and its
    raw signals go straight to the engine's order placement
    (``BacktestEngine._execute_signal``: SL/TP placement, anti-pyramiding,
    opposing-signal policy). No regime gating, no StrategyManager
    conflict resolution, no eight-flag validation. This measures the
    strategy itself and needs 5m/15m/1h/4h candles (1m optional).

``pipeline``
    Delegates to :meth:`BacktestEngine.run`, i.e. the live
    StrategyManager path with ADX regime admission and signal
    validation. Needs 1m candles as well (the engine's coverage guard
    is strict about the execution timeframes).

The loader is always :class:`BacktestDataLoader` in offline + validate
mode: no download is ever attempted, every file is checked for
ordering, duplicates, gaps, timezone and value sanity, and the
per-timeframe reports go into the written JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from loguru import logger

from ..diagnostics.funnel import (
    STAGE_BARS_EVALUATED,
    STAGE_BARS_SKIPPED_WARMUP,
    STAGE_CLOSED_TRADES,
    STAGE_EXECUTION_BLOCKED,
    STAGE_FILLS,
    STAGE_ORDERS_PLACED,
    STAGE_RAW_SIGNALS,
)
from .cost_model import CostModel
from .data_loader import BacktestDataLoader
from .engine import BacktestEngine
from .ohlcv import candle_file_for
from .performance import BacktestResult, PerformanceTracker
from .report import build_report, render_summary, write_report
from .simulated_exchange import SimulatedExchange
from .strategy_interface import StrategyAdapter, build_adapters

MODES = ("direct", "pipeline")
#: Timeframes the direct replay must have. 1m is loaded when present.
DIRECT_REQUIRED_TIMEFRAMES: Tuple[str, ...] = ("5m", "15m", "1h", "4h")
#: Primary-timeframe candles an adapter must see before it is called,
#: when the strategy does not declare ``required_history()`` itself.
DEFAULT_MIN_PRIMARY_HISTORY = 35
DEFAULT_OUTPUT_DIR = Path("backtesting/reports/harness")


@dataclass
class HarnessConfig:
    """Everything a harness run needs.

    Attributes:
        data_dir: Directory of ``{symbol}_{tf}.csv|csv.gz|parquet`` files.
        symbol: Pair to replay (file prefix).
        start: Window start, ISO date or datetime.
        end: Window end, ISO date or datetime.
        strategies: Registry keys or display names, in replay order.
        mode: "direct" or "pipeline" (see module docstring).
        initial_capital: Starting cash.
        lookback: Candles of history handed to strategies per timeframe;
            None reads ``BACKTEST_HISTORY_LOOKBACK`` from config.
        warmup: Candles loaded before ``start``; None mirrors lookback.
        strategy_params: Constructor overrides keyed by registry key.
        output_dir: Where the JSON and text report go; None skips writing.
        basename: Report file stem; None derives one from the run.
    """

    data_dir: Path
    symbol: str
    start: str
    end: str
    strategies: Sequence[str] = ("mean_reversion", "ma_crossover")
    mode: str = "direct"
    initial_capital: float = 10_000.0
    lookback: Optional[int] = None
    warmup: Optional[int] = None
    strategy_params: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    output_dir: Optional[Path] = DEFAULT_OUTPUT_DIR
    basename: Optional[str] = None

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if self.output_dir is not None:
            self.output_dir = Path(self.output_dir)
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        if not self.strategies:
            raise ValueError("at least one strategy is required")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if str(self.start) >= str(self.end):
            raise ValueError(f"start {self.start!r} must precede end {self.end!r}")

    def report_basename(self) -> str:
        """File stem for the written report."""
        if self.basename:
            return self.basename
        strategies = "+".join(self.strategies)
        return f"{self.symbol}_{self.start[:10]}_{self.end[:10]}_{strategies}"


@dataclass
class HarnessRun:
    """What :func:`run_harness` returns."""

    config: HarnessConfig
    result: BacktestResult
    report: Dict[str, Any]
    adapters: List[StrategyAdapter]
    validations: Dict[str, Any]
    json_path: Optional[Path] = None
    txt_path: Optional[Path] = None

    @property
    def summary(self) -> str:
        """The human-readable summary."""
        return render_summary(self.report)


class _ConfigProxy:
    """Live config with a few attributes overridden for one run."""

    def __init__(self, base: Any, **overrides: Any):
        self._base = base
        self._overrides = {k: v for k, v in overrides.items() if v is not None}

    def __getattr__(self, name: str) -> Any:
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._base, name)


def make_offline_loader(symbol: str, data_dir: Any) -> BacktestDataLoader:
    """Loader factory the harness hands to the engine: offline + validate."""
    return BacktestDataLoader(
        symbol=symbol, data_dir=str(data_dir), offline=True, validate=True
    )


def _engine_for(cfg: HarnessConfig) -> BacktestEngine:
    """Build an engine whose config points at the harness's store."""
    from ..config import config as live_config

    proxy = _ConfigProxy(
        live_config,
        backtest_data_dir=str(cfg.data_dir),
        backtest_history_lookback=cfg.lookback,
        backtest_warmup_candles=cfg.warmup,
        backtest_symbol=cfg.symbol,
        backtest_initial_capital=cfg.initial_capital,
    )
    return BacktestEngine(override_config=proxy, loader_factory=make_offline_loader)


class DirectReplay:
    """Replay 5m bars and drive strategy adapters without regime gating.

    Reuses the engine's run-state reset, execution policy, order
    placement and position bookkeeping, so a fill here is accounted for
    exactly as it would be in :meth:`BacktestEngine.run`.
    """

    def __init__(
        self,
        engine: BacktestEngine,
        adapters: Sequence[StrategyAdapter],
        loader: BacktestDataLoader,
        cfg: HarnessConfig,
    ):
        self.engine = engine
        self.adapters = list(adapters)
        self.loader = loader
        self.cfg = cfg
        self.warmup_skips: Dict[str, int] = {a.key: 0 for a in self.adapters}

    # -- data -------------------------------------------------------------

    def _timeframes(self) -> Tuple[str, ...]:
        """Required timeframes plus 1m when the store has it."""
        if candle_file_for(self.cfg.data_dir, self.cfg.symbol, "1m") is not None:
            return ("1m",) + DIRECT_REQUIRED_TIMEFRAMES
        return DIRECT_REQUIRED_TIMEFRAMES

    def _check_coverage(self, timeframes: Sequence[str]) -> None:
        """Refuse a window the store does not cover on any loaded timeframe.

        Raises:
            FileNotFoundError: When a required timeframe has no file at all.
            ValueError: When files exist but do not span [start, end].
        """
        missing = [
            tf
            for tf in timeframes
            if candle_file_for(self.cfg.data_dir, self.cfg.symbol, tf) is None
        ]
        if missing:
            raise FileNotFoundError(
                f"No {', '.join(missing)} candle file(s) for {self.cfg.symbol} in "
                f"{self.cfg.data_dir}; expected {self.cfg.symbol}_<tf>.csv, "
                f".csv.gz or .parquet (offline: nothing is downloaded)"
            )
        problems = []
        for tf in timeframes:
            span = self.loader.coverage_shortfall(
                tf, self.cfg.start, self.cfg.end, allow_download=False
            )
            if not span["covered"]:
                problems.append(
                    f"{tf}: store covers {span['first']} .. {span['last']}, "
                    f"missing {span['missing_leading']} leading and "
                    f"{span['missing_trailing']} trailing candles"
                )
        if problems:
            raise ValueError(
                f"{self.cfg.symbol} candles in {self.cfg.data_dir} do not cover "
                f"{self.cfg.start} .. {self.cfg.end}:\n  " + "\n  ".join(problems)
            )

    def _load(self, warmup: int) -> Dict[str, Dict[str, List[Any]]]:
        """Load every timeframe with the warmup prefix, after a coverage check."""
        timeframes = self._timeframes()
        self._check_coverage(timeframes)
        return {
            tf: self.loader.get_candles(
                tf, self.cfg.start, self.cfg.end, warmup_candles=warmup
            )
            for tf in timeframes
        }

    # -- replay -----------------------------------------------------------

    def _min_history(self, adapter: StrategyAdapter) -> int:
        """Primary-timeframe bars an adapter needs before its first call."""
        return adapter.required_history() or DEFAULT_MIN_PRIMARY_HISTORY

    def _signals_for_bar(
        self,
        bundles: Dict[str, Dict[str, List[Any]]],
        indices: Dict[str, int],
        price: float,
        sim_dt: Optional[datetime],
    ) -> List[Any]:
        """Ask every warmed-up adapter for its signals on this bar."""
        multi_tf = {tf: bundles[tf] for tf in ("15m", "1h", "4h")}
        execution_tf = {tf: bundles[tf] for tf in ("5m", "1m") if tf in bundles}
        signals: List[Any] = []
        for adapter in self.adapters:
            primary = adapter.spec.primary_timeframe
            if indices.get(primary, -1) + 1 < self._min_history(adapter):
                self.warmup_skips[adapter.key] += 1
                continue
            adapter.set_sim_time(sim_dt)
            signals.extend(
                adapter.generate_signals(
                    self.cfg.symbol, multi_tf, price, execution_tf_data=execution_tf
                )
            )
        return signals

    def run(self) -> BacktestResult:
        """Replay the window and return the finalised result."""
        engine, cfg = self.engine, self.cfg
        label = f"direct:{'+'.join(a.key for a in self.adapters)}/{cfg.symbol}"
        funnel = engine.begin_run(f"{label} {cfg.start}..{cfg.end}")
        funnel.note("mode", "direct")
        lookback = max(1, int(getattr(engine.cfg, "backtest_history_lookback", 60)))
        warmup = int(getattr(engine.cfg, "backtest_warmup_candles", 0) or 0) or lookback
        funding = engine._resolve_funding_schedule(
            cfg.symbol, funnel, cfg.start, cfg.end
        )
        exchange = SimulatedExchange(
            initial_capital=cfg.initial_capital,
            slippage_pct=engine.cfg.backtest_slippage_pct,
            taker_fee_pct=engine.cfg.backtest_taker_fee_pct,
            maker_fee_pct=engine.cfg.backtest_maker_fee_pct,
            funding_hourly_pct=engine.cfg.backtest_funding_hourly_pct,
            funding_schedule=funding,
            funding_interval_hours=engine._venue_funding_interval_hours(),
        )
        cost_model = CostModel(
            slippage_pct=engine.cfg.backtest_slippage_pct,
            taker_fee_pct=engine.cfg.backtest_taker_fee_pct,
        )
        performance = PerformanceTracker(initial_capital=cfg.initial_capital)
        candles = self._load(warmup)
        self._replay(candles, lookback, exchange, cost_model, performance, funnel)
        final_equity = float(exchange.get_account_balance()["balance"])
        result = performance.finalise(
            final_equity=final_equity,
            trade_log=exchange.trade_log,
            symbol=cfg.symbol,
            start=cfg.start,
            end=cfg.end,
            total_funding=exchange.total_funding,
        )
        funnel.set_stage(STAGE_FILLS, result.total_trades)
        funnel.set_stage(STAGE_CLOSED_TRADES, result.closed_trades)
        funnel.note("warmup_skips", dict(self.warmup_skips))
        result.diagnostics = funnel.to_dict()
        return result

    def _replay(
        self,
        candles: Dict[str, Dict[str, List[Any]]],
        lookback: int,
        exchange: SimulatedExchange,
        cost_model: CostModel,
        performance: PerformanceTracker,
        funnel: Any,
    ) -> None:
        """The bar loop. Mirrors BacktestEngine.run minus the StrategyManager."""
        engine, cfg = self.engine, self.cfg
        stamps_5m = candles["5m"]["timestamp"]
        tables = self._index_tables(candles)
        first = engine._first_index_at_or_after(stamps_5m, cfg.start)
        logger.info(
            f"Direct replay: {len(stamps_5m)} 5m candles "
            f"({first} warmup + {len(stamps_5m) - first} replayed)"
        )
        if first < len(stamps_5m):
            performance.record_snapshot(stamps_5m[first], exchange.equity(), {})
        for i in range(first, len(stamps_5m)):
            ts = str(stamps_5m[i])
            decision_ts = (
                datetime.fromisoformat(ts) + timedelta(minutes=5)
            ).isoformat()
            candle_5m = engine._candle_at(candles["5m"], i)
            engine._expire_stale_entries(exchange, i)
            exchange.advance(candle_5m, decision_ts)
            engine._sync_position_tracking(exchange, i)
            indices, bundles = self._bundles_for(candles, tables, i, ts, lookback)
            sim_dt = datetime.fromisoformat(decision_ts)
            engine._sim_dt = sim_dt
            if engine._position_trailing:
                engine._apply_trailing_stops(exchange, candle_5m)
            if engine._position_time_exit:
                engine._apply_time_exits(exchange, sim_dt)
            funnel.count(STAGE_BARS_EVALUATED)
            signals = self._signals_for_bar(
                bundles, indices, exchange._current_price, sim_dt
            )
            if not signals and self._all_warming_up(indices):
                funnel.count(STAGE_BARS_SKIPPED_WARMUP)
            self._execute(signals, exchange, cost_model, funnel, i)
            performance.record_snapshot(
                decision_ts, exchange.equity(), exchange._positions
            )

    @staticmethod
    def _index_tables(
        candles: Dict[str, Dict[str, List[Any]]],
    ) -> Dict[str, Tuple[List[str], Dict[str, int]]]:
        """Per higher timeframe: sorted timestamp strings and their index map."""
        tables = {}
        for tf in candles:
            if tf == "5m":
                continue
            sorted_ts = [str(t) for t in candles[tf]["timestamp"]]
            tables[tf] = (sorted_ts, {ts: i for i, ts in enumerate(sorted_ts)})
        return tables

    def _bundles_for(
        self,
        candles: Dict[str, Dict[str, List[Any]]],
        tables: Dict[str, Tuple[List[str], Dict[str, int]]],
        i: int,
        ts: str,
        lookback: int,
    ) -> Tuple[Dict[str, int], Dict[str, Dict[str, List[Any]]]]:
        """Completed-bar indices and history slices for every timeframe at bar i."""
        minutes = {"1m": 1, "15m": 15, "1h": 60, "4h": 240}
        indices = {
            tf: self.engine._completed_idx(idx_map, sorted_ts, ts, minutes[tf])
            for tf, (sorted_ts, idx_map) in tables.items()
        }
        indices["5m"] = i
        bundles = {
            tf: self.engine._history(candles[tf], indices[tf], lookback)
            for tf in candles
        }
        return indices, bundles

    def _all_warming_up(self, indices: Dict[str, int]) -> bool:
        """Whether no adapter has enough primary-timeframe history yet."""
        return all(
            indices.get(a.spec.primary_timeframe, -1) + 1 < self._min_history(a)
            for a in self.adapters
        )

    def _execute(
        self,
        signals: Sequence[Any],
        exchange: SimulatedExchange,
        cost_model: CostModel,
        funnel: Any,
        candle_idx: int,
    ) -> None:
        """Hand each signal to the engine's order placement."""
        for signal in signals:
            funnel.count(STAGE_RAW_SIGNALS)
            try:
                cost_model.apply(signal, exchange._current_price)
                exchange._current_strategy = signal.strategy.value
                if self.engine._execute_signal(signal, exchange, candle_idx):
                    funnel.count(STAGE_ORDERS_PLACED)
            except Exception as exc:  # noqa: BLE001 - one bad signal must not end the run
                funnel.count(STAGE_EXECUTION_BLOCKED)
                logger.debug(f"Signal execution skipped: {exc}")


def _run_pipeline(engine: BacktestEngine, cfg: HarnessConfig) -> BacktestResult:
    """Pipeline mode: the live StrategyManager path via BacktestEngine.run."""
    if candle_file_for(cfg.data_dir, cfg.symbol, "1m") is None:
        raise FileNotFoundError(
            f"pipeline mode needs 1m candles for {cfg.symbol} in {cfg.data_dir} "
            f"(the engine's coverage guard treats 1m as an execution timeframe). "
            f"Use --mode direct, or for the synthetic sample regenerate it with "
            f"`python -m trading_bot_v2.backtesting.sample_data --out <dir> "
            f"--timeframes 1m,5m,15m,1h,4h`."
        )
    return engine.run(
        start=cfg.start,
        end=cfg.end,
        symbol=cfg.symbol,
        initial_capital=cfg.initial_capital,
        strategy_filter=",".join(cfg.strategies),
    )


def run_harness(cfg: HarnessConfig) -> HarnessRun:
    """Run a backtest end to end and (optionally) write its report.

    Args:
        cfg: The run configuration.

    Returns:
        The result, the report dict, the adapters used (direct mode) and
        the paths written.

    Raises:
        FileNotFoundError: When a required candle file is missing.
        CandleDataError: When a candle file fails validation.
        ValueError: When the store does not cover the window.
    """
    adapters = build_adapters(cfg.strategies, cfg.strategy_params)
    engine = _engine_for(cfg)
    loader = make_offline_loader(cfg.symbol, cfg.data_dir)
    if cfg.mode == "direct":
        result = DirectReplay(engine, adapters, loader, cfg).run()
        validations = loader.validations
    else:
        result = _run_pipeline(engine, cfg)
        # The engine built its own loader through the factory; re-read
        # the validations by loading the same files once more (cheap:
        # the sample is small, and a real store is parquet).
        validations = _validations_for(cfg)
    report = build_report(
        result,
        run=_run_description(cfg, engine),
        validations={tf: v.to_dict() for tf, v in validations.items()},
        adapters=(
            {a.key: a.stats.to_dict() for a in adapters} if cfg.mode == "direct" else {}
        ),
    )
    run = HarnessRun(cfg, result, report, adapters, dict(validations))
    if cfg.output_dir is not None:
        run.json_path, run.txt_path = write_report(
            report, cfg.output_dir, cfg.report_basename()
        )
    return run


def _validations_for(cfg: HarnessConfig) -> Dict[str, Any]:
    """Validate every store file for the symbol (pipeline mode bookkeeping)."""
    loader = make_offline_loader(cfg.symbol, cfg.data_dir)
    for tf in ("1m",) + DIRECT_REQUIRED_TIMEFRAMES:
        if candle_file_for(cfg.data_dir, cfg.symbol, tf) is not None:
            loader.coverage_bounds(tf)
    return loader.validations


def _run_description(cfg: HarnessConfig, engine: BacktestEngine) -> Dict[str, Any]:
    """The ``run`` block of the report."""
    c = engine.cfg
    return {
        "symbol": cfg.symbol,
        "start": cfg.start,
        "end": cfg.end,
        "mode": cfg.mode,
        "strategies": list(cfg.strategies),
        "strategy_params": {k: dict(v) for k, v in cfg.strategy_params.items()},
        "data_dir": str(cfg.data_dir),
        "initial_capital": cfg.initial_capital,
        "history_lookback": int(getattr(c, "backtest_history_lookback", 60)),
        "warmup_candles": int(getattr(c, "backtest_warmup_candles", 0) or 0),
        "slippage_pct": float(c.backtest_slippage_pct),
        "taker_fee_pct": float(c.backtest_taker_fee_pct),
        "maker_fee_pct": float(c.backtest_maker_fee_pct),
        "funding_model": str(getattr(c, "backtest_funding_model", "flat") or "flat"),
        "funding_hourly_pct": float(c.backtest_funding_hourly_pct),
        "execution_policy": engine.execution_policy(),
    }
