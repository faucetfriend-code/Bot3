"""
Portfolio-level validation
==========================

Every strategy in this bot is validated in isolation, but they all run
together in one account. This module measures what isolation cannot show.

It runs the same window twice through the same ``BacktestEngine``:

* **isolated** - one run per strategy with ``strategy_filter`` set, exactly
  what ``run_strategy_sweep.py`` does;
* **portfolio** - one run with the whole selected set enabled at once, so
  the real ``StrategyManager`` conflict resolution, the shared
  ``SimulatedExchange`` balance and the one-position-per-symbol occupancy
  rule are all in play.

Then it reports the four things nobody was measuring:

1. **Portfolio equity and drawdown vs the sum of the isolated runs.** The
   benchmark is an equal-weight blend of the isolated fractional return
   series - the naive "diversification" prediction. If the portfolio beats
   it, sharing an account helped; if it loses, the machinery cost money.
2. **A correlation matrix of per-strategy returns**, computed on the
   isolated equity curves (aligned on their shared snapshot timestamps).
   High average off-diagonal correlation means the strategies are the same
   bet wearing different hats, and their individual max-drawdowns will
   arrive on the same day.
3. **Conflict resolution: how often it fired and what it discarded.** The
   signal funnel already counts ``conflict_dropped`` per strategy;
   :class:`ConflictProbe` additionally records every invocation (candidates
   in, survivors out, regime) so "never fired" is distinguishable from
   "fired constantly and kept everything".
4. **Whether exposure limits bind in practice.** Spoiler, and the reason
   this is worth reporting: on the backtest path they do not.
   ``RiskManager.max_portfolio_exposure_pct`` is only consulted by
   GridTrading's sizing; ``BacktestEngine._execute_signal`` sizes from
   ``signal.quantity`` and never asks RiskManager. The constraint that
   actually binds is position occupancy - one position per symbol - which
   is measured here directly.

Usage
-----
    # Smoke run: one explicit window
    python -m trading_bot_v2.validation.portfolio --symbol SUI-USDC \\
        --start 2025-01-01 --end 2025-02-01

    # Chunked series (the owner's preference: several small windows)
    python -m trading_bot_v2.validation.portfolio --symbol SUI-USDC \\
        --windows 6 --window-months 2 --mode spread

    # Explicit strategy set, JSON artifact
    python -m trading_bot_v2.validation.portfolio --symbol BTC-USDC \\
        --strategies mean_reversion grid_trading vwap_scalping \\
        --windows 3 --window-months 1 --json reports/portfolio_btc.json

Running from a git worktree
---------------------------
``.env`` sets ``BACKTEST_DATA_DIR`` to a relative path and
``load_dotenv(override=True)`` clobbers shell exports, so pass the store's
absolute path with ``--data-dir`` and set ``DATA_AUTODOWNLOAD=false``.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Annualisation factor for snapshot-series Sharpe. Mirrors
#: backtesting/performance.py so the numbers printed here are directly
#: comparable to ``BacktestResult.sharpe_ratio``. (Snapshots are taken
#: every 60 five-minute candles, i.e. every 5 hours, so 8760 is not the
#: textbook constant - it is kept for comparability, not correctness.)
SNAPSHOT_PERIODS_PER_YEAR = 8760.0

#: Average off-diagonal correlation at or above which the strategies are
#: called the same bet rather than a diversified set.
CORRELATED_THRESHOLD = 0.50

#: Fraction of isolated closed trades a strategy must lose in the
#: portfolio run before crowding is named a contributing factor.
CROWDING_THRESHOLD = 0.25

#: Funnel rejection-reason prefix for the engine's execution gate.
EXEC_REASON_PREFIX = "exec:"

#: Default capital for a portfolio run (matches validation/runner.py).
DEFAULT_CAPITAL = 10000.0

DEFAULT_SYMBOL = "SUI-USDC"
DEFAULT_WINDOWS = 3
DEFAULT_WINDOW_MONTHS = 1

#: Seconds a single engine run costs per replayed calendar month.
#: Measured on this machine over SUI-USDC 5m (7 runs x 0.5 months in 12s,
#: 21 runs x 1 month in 73s). Override with --seconds-per-month.
DEFAULT_SECONDS_PER_MONTH = 3.5


# ---------------------------------------------------------------------------
# Pure analytics: equity curves
# ---------------------------------------------------------------------------


def period_returns(equity: Sequence[float]) -> List[float]:
    """Return the simple period-over-period returns of an equity curve.

    Args:
        equity: Equity values in chronological order.

    Returns:
        ``len(equity) - 1`` fractional returns; empty when the curve has
        fewer than two points. Periods with a non-positive prior equity
        contribute 0.0 rather than raising.
    """
    out: List[float] = []
    for i in range(1, len(equity)):
        prev = equity[i - 1]
        out.append((equity[i] - prev) / prev if prev > 0 else 0.0)
    return out


def max_drawdown_pct(equity: Sequence[float], peak: Optional[float] = None) -> float:
    """Return the maximum peak-to-trough drawdown of a curve, in percent.

    Args:
        equity: Equity values in chronological order.
        peak: Initial running peak (default: the first equity value).
            Pass the initial capital to match the engine, which seeds the
            peak with it so an immediate loss counts as drawdown.

    Returns:
        Max drawdown as a positive percentage (0.0 for an empty curve).
    """
    if not equity:
        return 0.0
    running = peak if peak is not None else equity[0]
    worst = 0.0
    for value in equity:
        if value > running:
            running = value
        if running > 0:
            drop = (running - value) / running
            if drop > worst:
                worst = drop
    return worst * 100.0


def annualised_sharpe(
    returns: Sequence[float], periods_per_year: float = SNAPSHOT_PERIODS_PER_YEAR
) -> float:
    """Return the annualised Sharpe of a return series (zero risk-free).

    Uses the population standard deviation, matching
    ``PerformanceTracker.finalise``.

    Args:
        returns: Period returns.
        periods_per_year: Annualisation factor.

    Returns:
        Annualised Sharpe, or 0.0 when the series is too short or flat.
    """
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / len(returns)
    std = math.sqrt(var)
    if std <= 0:
        return 0.0
    return mean / std * math.sqrt(periods_per_year)


def align_curves(
    curves: Dict[str, List[Tuple[str, float]]],
) -> Tuple[List[str], Dict[str, List[float]]]:
    """Align several timestamped equity curves onto their shared timestamps.

    Runs over the same window share a snapshot cadence, so in practice the
    intersection is the whole series; the intersection is taken anyway so
    a truncated run degrades to a shorter comparison instead of a silently
    misaligned one.

    Args:
        curves: name -> list of ``(timestamp, equity)`` in chronological
            order.

    Returns:
        ``(timestamps, {name: equity values})`` over the common
        timestamps, sorted. Empty when the inputs share nothing.
    """
    if not curves:
        return [], {}
    common: Optional[set] = None
    for points in curves.values():
        stamps = {str(ts) for ts, _ in points}
        common = stamps if common is None else (common & stamps)
    if not common:
        return [], {name: [] for name in curves}
    ordered = sorted(common)
    aligned: Dict[str, List[float]] = {}
    for name, points in curves.items():
        lookup = {str(ts): float(value) for ts, value in points}
        aligned[name] = [lookup[ts] for ts in ordered]
    return ordered, aligned


def blend_equity(
    curves: Dict[str, List[float]],
    initial_capital: float,
    weights: Optional[Dict[str, float]] = None,
) -> List[float]:
    """Return the equal-weight blend of several isolated equity curves.

    This is the "sum of the parts" benchmark: capital split evenly across
    the strategies, each run alone with its slice. Formally
    ``E(t) = C * (1 + sum_i w_i * (E_i(t)/C - 1))`` with ``w`` summing to 1.

    Args:
        curves: name -> aligned equity values (all the same length).
        initial_capital: Capital each isolated run started with.
        weights: Optional per-name weights; normalised internally.
            Default is equal weight.

    Returns:
        The blended equity curve, or an empty list when no input curve has
        any points.
    """
    names = [name for name, values in curves.items() if values]
    if not names or initial_capital <= 0:
        return []
    length = min(len(curves[name]) for name in names)
    if weights:
        total = sum(max(0.0, weights.get(name, 0.0)) for name in names)
        resolved = (
            {name: max(0.0, weights.get(name, 0.0)) / total for name in names}
            if total > 0
            else {name: 1.0 / len(names) for name in names}
        )
    else:
        resolved = {name: 1.0 / len(names) for name in names}
    blended: List[float] = []
    for i in range(length):
        excess = sum(
            resolved[name] * (curves[name][i] / initial_capital - 1.0) for name in names
        )
        blended.append(initial_capital * (1.0 + excess))
    return blended


def additive_equity(
    curves: Dict[str, List[float]], initial_capital: float
) -> List[float]:
    """Return the additive stack of isolated curves (full capital each).

    ``E(t) = C + sum_i (E_i(t) - C)``. This is what the isolated sweep
    implicitly promises when its per-strategy returns are read side by
    side: every strategy got the whole account. It is included because it
    is the comparison people make by eye, not because it is fundable.

    Args:
        curves: name -> aligned equity values.
        initial_capital: Capital each isolated run started with.

    Returns:
        The stacked equity curve, or an empty list when there is nothing
        to stack.
    """
    names = [name for name, values in curves.items() if values]
    if not names:
        return []
    length = min(len(curves[name]) for name in names)
    return [
        initial_capital + sum(curves[name][i] - initial_capital for name in names)
        for i in range(length)
    ]


# ---------------------------------------------------------------------------
# Pure analytics: correlation
# ---------------------------------------------------------------------------


def pearson(a: Sequence[float], b: Sequence[float]) -> float:
    """Return the Pearson correlation of two equal-length series.

    Args:
        a: First series.
        b: Second series.

    Returns:
        Correlation in [-1, 1]; 0.0 when either series is constant or the
        overlap is shorter than two points.
    """
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    mean_a = sum(a[:n]) / n
    mean_b = sum(b[:n]) / n
    cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    var_a = sum((a[i] - mean_a) ** 2 for i in range(n))
    var_b = sum((b[i] - mean_b) ** 2 for i in range(n))
    if var_a <= 0 or var_b <= 0:
        return 0.0
    return cov / math.sqrt(var_a * var_b)


def correlation_matrix(
    series: Dict[str, Sequence[float]],
) -> Dict[str, Dict[str, float]]:
    """Return the full Pearson correlation matrix of several series.

    Args:
        series: name -> return series.

    Returns:
        Nested dict ``matrix[row][col]``; the diagonal is 1.0 for any
        series with variance, else 0.0.
    """
    names = list(series)
    matrix: Dict[str, Dict[str, float]] = {}
    for row in names:
        matrix[row] = {}
        for col in names:
            matrix[row][col] = (
                pearson(series[row], series[col])
                if row != col
                else (1.0 if pearson(series[row], series[row]) else 0.0)
            )
    return matrix


def average_offdiagonal(matrix: Dict[str, Dict[str, float]]) -> float:
    """Return the mean of a correlation matrix's off-diagonal entries.

    Args:
        matrix: Output of :func:`correlation_matrix`.

    Returns:
        Mean off-diagonal correlation, or 0.0 for a matrix with fewer
        than two names.
    """
    names = list(matrix)
    pairs = [matrix[a][b] for i, a in enumerate(names) for b in names[i + 1 :]]
    return sum(pairs) / len(pairs) if pairs else 0.0


def most_correlated_pair(
    matrix: Dict[str, Dict[str, float]],
) -> Optional[Tuple[str, str, float]]:
    """Return the highest-correlation off-diagonal pair.

    Args:
        matrix: Output of :func:`correlation_matrix`.

    Returns:
        ``(name_a, name_b, correlation)``, or None when there is no pair.
    """
    names = list(matrix)
    best: Optional[Tuple[str, str, float]] = None
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            value = matrix[a][b]
            if best is None or value > best[2]:
                best = (a, b, value)
    return best


# ---------------------------------------------------------------------------
# Pure analytics: trade logs and funnels
# ---------------------------------------------------------------------------


def trade_attribution(trade_log: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Break a trade log down by the strategy that placed each fill.

    Args:
        trade_log: ``SimulatedExchange.trade_log`` entries.

    Returns:
        strategy key -> ``{fills, closed_trades, pnl, wins, fees}``.
        Fills with no strategy tag are collected under ``"unknown"``.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for entry in trade_log:
        name = str(entry.get("strategy") or "unknown")
        cell = out.setdefault(
            name,
            {"fills": 0, "closed_trades": 0, "pnl": 0.0, "wins": 0, "fees": 0.0},
        )
        cell["fills"] += 1
        cell["fees"] += float(entry.get("fee") or 0.0)
        pnl = float(entry.get("pnl") or 0.0)
        if pnl != 0.0:
            cell["closed_trades"] += 1
            cell["pnl"] += pnl
            if pnl > 0:
                cell["wins"] += 1
    return out


def daily_pnl_by_strategy(
    trade_log: Sequence[Dict[str, Any]],
) -> Dict[str, Dict[str, float]]:
    """Aggregate realised PnL per strategy per calendar day.

    Used for a correlation view grounded in the portfolio run itself,
    rather than in the isolated equity curves.

    Args:
        trade_log: ``SimulatedExchange.trade_log`` entries.

    Returns:
        strategy key -> ``{"YYYY-MM-DD": realised pnl}``.
    """
    out: Dict[str, Dict[str, float]] = {}
    for entry in trade_log:
        pnl = float(entry.get("pnl") or 0.0)
        if pnl == 0.0:
            continue
        name = str(entry.get("strategy") or "unknown")
        day = str(entry.get("timestamp") or "")[:10]
        if not day:
            continue
        cell = out.setdefault(name, {})
        cell[day] = cell.get(day, 0.0) + pnl
    return out


def dense_daily_series(daily: Dict[str, Dict[str, float]]) -> Dict[str, List[float]]:
    """Expand sparse per-day PnL maps onto a shared, gap-filled day axis.

    Days on which a strategy did not close a trade contribute 0.0, which
    is the honest reading: it made nothing that day.

    Args:
        daily: Output of :func:`daily_pnl_by_strategy`.

    Returns:
        strategy key -> PnL per day over the union of all observed days,
        in date order.
    """
    days = sorted({day for cell in daily.values() for day in cell})
    return {name: [cell.get(day, 0.0) for day in days] for name, cell in daily.items()}


def occupancy_stats(snapshots: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Measure how much of the run had capital committed to a position.

    Args:
        snapshots: ``BacktestResult.equity_curve`` entries, each carrying
            an ``open_positions`` count.

    Returns:
        ``{snapshots, occupied_snapshots, occupancy_pct,
        max_concurrent_positions}``.
    """
    total = len(snapshots)
    counts = [int(s.get("open_positions") or 0) for s in snapshots]
    occupied = sum(1 for c in counts if c > 0)
    return {
        "snapshots": total,
        "occupied_snapshots": occupied,
        "occupancy_pct": (occupied / total * 100.0) if total else 0.0,
        "max_concurrent_positions": max(counts) if counts else 0,
    }


def largest_fill_notional(trade_log: Sequence[Dict[str, Any]]) -> float:
    """Return the largest single-fill notional in a trade log.

    A cheap proxy for whether a portfolio exposure cap could ever bind:
    the engine never asks RiskManager to size a non-grid order, so the
    only way to know is to look at what was actually sent.

    Args:
        trade_log: ``SimulatedExchange.trade_log`` entries.

    Returns:
        Max ``quantity * fill_price`` over all fills (0.0 when empty).
    """
    worst = 0.0
    for entry in trade_log:
        try:
            notional = abs(float(entry.get("quantity") or 0.0)) * abs(
                float(entry.get("fill_price") or 0.0)
            )
        except (TypeError, ValueError):
            continue
        if notional > worst:
            worst = notional
    return worst


def conflict_stats(diagnostics: Dict[str, Any]) -> Dict[str, Any]:
    """Read conflict-resolution attrition out of a funnel payload.

    Args:
        diagnostics: ``SignalFunnel.to_dict()`` output.

    Returns:
        ``{total_dropped, by_strategy, raw_signals, drop_share_pct}``.
    """
    stages = dict(diagnostics.get("stages") or {})
    by_strategy = dict(diagnostics.get("by_strategy") or {})
    dropped = int(stages.get("conflict_dropped") or 0)
    raw = int(stages.get("raw_signals") or 0)
    per_strategy = {
        name: int(cell.get("conflict_dropped") or 0)
        for name, cell in by_strategy.items()
        if int(cell.get("conflict_dropped") or 0) > 0
    }
    return {
        "total_dropped": dropped,
        "by_strategy": per_strategy,
        "raw_signals": raw,
        "drop_share_pct": (dropped / raw * 100.0) if raw else 0.0,
    }


def exec_block_stats(diagnostics: Dict[str, Any]) -> Dict[str, int]:
    """Return the engine's execution-gate rejection counts.

    Args:
        diagnostics: ``SignalFunnel.to_dict()`` output.

    Returns:
        ``exec:``-prefixed reason -> count, descending by count.

    Note:
        These counters are global, not per-strategy: the engine calls
        ``funnel.reject(REASON_EXEC_*)`` without a ``strategy=`` kwarg.
        Attributing a crowd-out to the strategy that lost the signal
        needs that argument added in ``backtesting/engine.py``.
    """
    reasons = dict(diagnostics.get("reasons") or {})
    filtered = {
        name: int(count)
        for name, count in reasons.items()
        if str(name).startswith(EXEC_REASON_PREFIX)
    }
    return dict(sorted(filtered.items(), key=lambda kv: (-kv[1], kv[0])))


def raw_signals_by_strategy(diagnostics: Dict[str, Any]) -> Dict[str, int]:
    """Return per-strategy raw signal counts from a funnel payload.

    Args:
        diagnostics: ``SignalFunnel.to_dict()`` output.

    Returns:
        strategy key -> raw signals emitted.
    """
    by_strategy = dict(diagnostics.get("by_strategy") or {})
    return {
        name: int(cell.get("raw_signals") or 0)
        for name, cell in by_strategy.items()
        if int(cell.get("raw_signals") or 0) > 0
    }


def crowding_ratios(
    portfolio_attr: Dict[str, Dict[str, Any]],
    isolated_attr: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Compare per-strategy closed-trade counts, portfolio vs isolation.

    The retention ratio is the share of a strategy's isolated trades that
    survived into the portfolio run. Below 1.0 means the strategy was
    crowded out - the symbol was already occupied when it wanted in.

    Args:
        portfolio_attr: :func:`trade_attribution` of the portfolio run.
        isolated_attr: strategy key -> :func:`trade_attribution` cell of
            that strategy's isolated run.

    Returns:
        strategy key -> ``{isolated, portfolio, retention, pnl_isolated,
        pnl_portfolio}``.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for name, cell in isolated_attr.items():
        alone = int(cell.get("closed_trades") or 0)
        together_cell = portfolio_attr.get(name) or {}
        together = int(together_cell.get("closed_trades") or 0)
        out[name] = {
            "isolated": alone,
            "portfolio": together,
            "retention": (together / alone) if alone else 0.0,
            "pnl_isolated": float(cell.get("pnl") or 0.0),
            "pnl_portfolio": float(together_cell.get("pnl") or 0.0),
        }
    return out


# ---------------------------------------------------------------------------
# Conflict probe
# ---------------------------------------------------------------------------


def signal_strategy_key(signal: Any) -> str:
    """Best-effort strategy key for a signal (mirrors StrategyManager)."""
    strategy = getattr(signal, "strategy", None)
    return str(getattr(strategy, "value", strategy) or "unknown")


class ConflictProbe:
    """Record every ``_resolve_signal_conflicts`` invocation during a run.

    The funnel counts what conflict resolution *discarded*, but not how
    often it was *invoked* - a run with zero drops could mean it never
    fired or that it fired constantly and kept everything. Those are
    different findings, so this wraps the method for the duration of a
    backtest and records both.

    Instrumentation only: the wrapper calls through and returns the
    original result unchanged, and the patch is reverted on exit even if
    the run raises.

    Attributes:
        events: One dict per invocation with ``candidates``, ``kept``,
            ``dropped``, ``synthesized`` and ``regime``.

    Example:
        >>> class Fake:
        ...     def _resolve_signal_conflicts(self, signals, regime):
        ...         return signals[:1]
        >>> probe = ConflictProbe(target=Fake)
        >>> with probe:
        ...     _ = Fake()._resolve_signal_conflicts([], "ranging_calm")
        >>> probe.events
        []
    """

    def __init__(self, target: Optional[type] = None) -> None:
        """Initialize the probe.

        Args:
            target: Class carrying ``_resolve_signal_conflicts``. Defaults
                to the real ``StrategyManager`` (imported lazily so this
                module stays importable without the live runtime).
        """
        self._target = target
        self._original: Any = None
        self.events: List[Dict[str, Any]] = []

    def _resolve_target(self) -> type:
        if self._target is not None:
            return self._target
        from ..strategy_manager import StrategyManager

        self._target = StrategyManager
        return self._target

    def __enter__(self) -> "ConflictProbe":
        target = self._resolve_target()
        original = target._resolve_signal_conflicts
        self._original = original
        events = self.events

        def wrapper(manager: Any, signals: List[Any], regime: Any) -> List[Any]:
            survivors = original(manager, signals, regime)
            if len(signals) > 1:
                incoming = {id(s) for s in signals}
                kept = {id(s) for s in survivors}
                events.append(
                    {
                        "regime": str(getattr(regime, "value", regime) or ""),
                        "candidates": [signal_strategy_key(s) for s in signals],
                        "kept": [
                            signal_strategy_key(s)
                            for s in survivors
                            if id(s) in incoming
                        ],
                        "dropped": [
                            signal_strategy_key(s) for s in signals if id(s) not in kept
                        ],
                        "synthesized": sum(
                            1 for s in survivors if id(s) not in incoming
                        ),
                    }
                )
            return survivors

        target._resolve_signal_conflicts = wrapper
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if self._original is not None and self._target is not None:
            self._target._resolve_signal_conflicts = self._original
            self._original = None
        return False

    def summary(self) -> Dict[str, Any]:
        """Summarise the recorded invocations.

        Returns:
            ``{invocations, candidates, dropped, synthesized,
            dropped_by_strategy, won_by_strategy, by_regime}``.
        """
        dropped_by: Dict[str, int] = {}
        won_by: Dict[str, int] = {}
        by_regime: Dict[str, int] = {}
        candidates = 0
        dropped = 0
        synthesized = 0
        for event in self.events:
            candidates += len(event["candidates"])
            synthesized += int(event.get("synthesized") or 0)
            by_regime[event["regime"]] = by_regime.get(event["regime"], 0) + 1
            for name in event["dropped"]:
                dropped_by[name] = dropped_by.get(name, 0) + 1
                dropped += 1
            for name in event["kept"]:
                won_by[name] = won_by.get(name, 0) + 1
        return {
            "invocations": len(self.events),
            "candidates": candidates,
            "dropped": dropped,
            "synthesized": synthesized,
            "dropped_by_strategy": dict(
                sorted(dropped_by.items(), key=lambda kv: (-kv[1], kv[0]))
            ),
            "won_by_strategy": dict(
                sorted(won_by.items(), key=lambda kv: (-kv[1], kv[0]))
            ),
            "by_regime": dict(sorted(by_regime.items())),
        }


# ---------------------------------------------------------------------------
# Strategy set selection
# ---------------------------------------------------------------------------


def backtestable_strategies(keys: Iterable[str]) -> List[str]:
    """Filter a strategy-key list down to what the engine can drive.

    Args:
        keys: Snake_case strategy keys.

    Returns:
        The subset excluding ``NON_BACKTESTABLE_STRATEGIES`` (FundingArb
        and OrderBookImbalance depend on live-only data surfaces and are
        force-disabled by the engine anyway), order preserved.
    """
    from ..backtesting.engine import NON_BACKTESTABLE_STRATEGIES

    return [key for key in keys if key not in NON_BACKTESTABLE_STRATEGIES]


def default_strategy_set() -> List[str]:
    """Return the enabled, backtestable strategies from the environment.

    Returns:
        Snake_case keys whose ``ENABLE_*`` flag resolves true and which
        the backtest engine can actually run.
    """
    from .runner import discover_enabled_strategies

    return backtestable_strategies(discover_enabled_strategies())


@contextmanager
def strategy_enables(keys: Iterable[str]) -> Iterator[None]:
    """Force the ``ENABLE_*`` env flags to exactly one strategy set.

    ``BacktestEngine.run`` only builds explicit enable kwargs in
    single-strategy mode; with no ``strategy_filter`` the StrategyManager
    reads ``ENABLE_*`` from the environment. This context manager is
    therefore how a *subset* portfolio is selected, and it restores the
    previous environment on exit.

    Args:
        keys: Snake_case strategy keys to enable. Everything else in the
            known set is forced off.

    Yields:
        None, for the duration of the override.
    """
    from .runner import STRATEGY_ENV_FLAGS

    wanted = set(keys)
    saved: Dict[str, Optional[str]] = {}
    try:
        for key, (env_var, _default) in STRATEGY_ENV_FLAGS.items():
            saved[env_var] = os.environ.get(env_var)
            os.environ[env_var] = "true" if key in wanted else "false"
        yield
    finally:
        for env_var, previous in saved.items():
            if previous is None:
                os.environ.pop(env_var, None)
            else:
                os.environ[env_var] = previous


# ---------------------------------------------------------------------------
# Run containers
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    """One backtest run, isolated or portfolio."""

    label: str
    start: str
    end: str
    initial_capital: float
    final_equity: float = 0.0
    total_return_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    closed_trades: int = 0
    elapsed_sec: float = 0.0
    error: Optional[str] = None
    equity_points: List[Tuple[str, float]] = field(default_factory=list)
    snapshots: List[Dict[str, Any]] = field(default_factory=list)
    trade_log: List[Dict[str, Any]] = field(default_factory=list)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    conflict_events: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether the run completed without raising."""
        return self.error is None

    def to_summary(self) -> Dict[str, Any]:
        """Return a compact JSON-safe summary (no curves or trade logs)."""
        return {
            "label": self.label,
            "start": self.start,
            "end": self.end,
            "final_equity": round(self.final_equity, 2),
            "total_return_pct": round(self.total_return_pct, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "profit_factor": round(self.profit_factor, 4),
            "closed_trades": self.closed_trades,
            "elapsed_sec": round(self.elapsed_sec, 1),
            "error": self.error,
        }


@dataclass
class WindowComparison:
    """Portfolio vs sum-of-parts over a single window."""

    symbol: str
    start: str
    end: str
    initial_capital: float
    portfolio: RunResult
    isolated: Dict[str, RunResult] = field(default_factory=dict)
    blend_return_pct: float = 0.0
    blend_max_drawdown_pct: float = 0.0
    blend_sharpe: float = 0.0
    additive_return_pct: float = 0.0
    additive_max_drawdown_pct: float = 0.0
    mean_isolated_drawdown_pct: float = 0.0
    correlation: Dict[str, Dict[str, float]] = field(default_factory=dict)
    daily_correlation: Dict[str, Dict[str, float]] = field(default_factory=dict)
    conflict: Dict[str, Any] = field(default_factory=dict)
    conflict_probe: Dict[str, Any] = field(default_factory=dict)
    exec_blocks: Dict[str, int] = field(default_factory=dict)
    isolated_exec_blocks: Dict[str, int] = field(default_factory=dict)
    exposure: Dict[str, Any] = field(default_factory=dict)
    crowding: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def return_edge_pct(self) -> float:
        """Portfolio return minus the equal-weight blend return."""
        return self.portfolio.total_return_pct - self.blend_return_pct

    @property
    def drawdown_edge_pct(self) -> float:
        """Blend drawdown minus portfolio drawdown (positive = shallower)."""
        return self.blend_max_drawdown_pct - self.portfolio.max_drawdown_pct

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-safe dict of this window's findings."""
        return {
            "symbol": self.symbol,
            "start": self.start,
            "end": self.end,
            "initial_capital": self.initial_capital,
            "portfolio": self.portfolio.to_summary(),
            "isolated": {name: run.to_summary() for name, run in self.isolated.items()},
            "blend": {
                "return_pct": round(self.blend_return_pct, 4),
                "max_drawdown_pct": round(self.blend_max_drawdown_pct, 4),
                "sharpe": round(self.blend_sharpe, 4),
            },
            "additive": {
                "return_pct": round(self.additive_return_pct, 4),
                "max_drawdown_pct": round(self.additive_max_drawdown_pct, 4),
            },
            "mean_isolated_drawdown_pct": round(self.mean_isolated_drawdown_pct, 4),
            "return_edge_pct": round(self.return_edge_pct, 4),
            "drawdown_edge_pct": round(self.drawdown_edge_pct, 4),
            "correlation": self.correlation,
            "daily_correlation": self.daily_correlation,
            "conflict": self.conflict,
            "conflict_probe": self.conflict_probe,
            "exec_blocks": self.exec_blocks,
            "isolated_exec_blocks": self.isolated_exec_blocks,
            "exposure": self.exposure,
            "crowding": self.crowding,
        }


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def _result_to_run(
    label: str,
    result: Any,
    start: str,
    end: str,
    capital: float,
    elapsed: float,
) -> RunResult:
    """Convert a ``BacktestResult`` into a :class:`RunResult`.

    Args:
        label: Run label (strategy key, or "PORTFOLIO").
        result: The BacktestResult.
        start: Window start.
        end: Window end.
        capital: Initial capital.
        elapsed: Wall-clock seconds the run took.

    Returns:
        The populated RunResult.
    """
    snapshots = list(result.equity_curve or [])
    profit_factor = float(result.profit_factor)
    if math.isinf(profit_factor):
        profit_factor = 999999.0
    return RunResult(
        label=label,
        start=start,
        end=end,
        initial_capital=capital,
        final_equity=float(result.final_equity),
        total_return_pct=float(result.total_return_pct),
        max_drawdown_pct=float(result.max_drawdown_pct),
        sharpe_ratio=float(result.sharpe_ratio),
        profit_factor=profit_factor,
        closed_trades=int(result.closed_trades),
        elapsed_sec=elapsed,
        equity_points=[
            (str(s.get("timestamp")), float(s.get("equity") or 0.0)) for s in snapshots
        ],
        snapshots=snapshots,
        trade_log=list(result.trade_log or []),
        diagnostics=dict(getattr(result, "diagnostics", None) or {}),
    )


def _build_engine(data_dir: Optional[str]) -> Any:
    """Build a fresh BacktestEngine, optionally overriding the data dir.

    ``.env`` sets ``BACKTEST_DATA_DIR`` to a path relative to the main
    checkout, and ``load_dotenv(override=True)`` beats any shell export,
    so a worktree run has to inject the absolute path here.

    Args:
        data_dir: Absolute candle-store path, or None to keep config's.

    Returns:
        A new BacktestEngine.
    """
    from ..backtesting.engine import BacktestEngine

    engine = BacktestEngine()
    if data_dir:
        engine.cfg.backtest_data_dir = data_dir
    return engine


def run_isolated(
    strategy: str,
    symbol: str,
    start: str,
    end: str,
    capital: float,
    data_dir: Optional[str] = None,
) -> RunResult:
    """Backtest one strategy alone, the way run_strategy_sweep.py does.

    Args:
        strategy: Snake_case strategy key.
        symbol: Trading pair.
        start: Window start (ISO date).
        end: Window end (ISO date).
        capital: Initial capital.
        data_dir: Absolute candle-store path override.

    Returns:
        A RunResult; ``error`` is set instead of raising when the engine
        rejects the window.
    """
    t0 = time.time()
    try:
        engine = _build_engine(data_dir)
        result = engine.run(
            start=start,
            end=end,
            symbol=symbol,
            initial_capital=capital,
            strategy_filter=strategy,
        )
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return RunResult(
            label=strategy,
            start=start,
            end=end,
            initial_capital=capital,
            elapsed_sec=time.time() - t0,
            error=str(exc),
        )
    return _result_to_run(strategy, result, start, end, capital, time.time() - t0)


def run_portfolio(
    strategies: Sequence[str],
    symbol: str,
    start: str,
    end: str,
    capital: float,
    data_dir: Optional[str] = None,
) -> RunResult:
    """Backtest the whole strategy set together in one shared account.

    Runs with no ``strategy_filter``, so the real StrategyManager
    regime mapping, overlay strategies, confidence gate and conflict
    resolution all apply, and every strategy competes for the same
    balance and the same one-position-per-symbol slot.

    Args:
        strategies: Snake_case strategy keys to enable.
        symbol: Trading pair.
        start: Window start (ISO date).
        end: Window end (ISO date).
        capital: Initial capital.
        data_dir: Absolute candle-store path override.

    Returns:
        A RunResult with ``conflict_events`` populated by
        :class:`ConflictProbe`.
    """
    t0 = time.time()
    probe = ConflictProbe()
    try:
        with strategy_enables(strategies), probe:
            engine = _build_engine(data_dir)
            result = engine.run(
                start=start,
                end=end,
                symbol=symbol,
                initial_capital=capital,
                strategy_filter=None,
            )
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        return RunResult(
            label="PORTFOLIO",
            start=start,
            end=end,
            initial_capital=capital,
            elapsed_sec=time.time() - t0,
            error=str(exc),
            conflict_events=list(probe.events),
        )
    run = _result_to_run("PORTFOLIO", result, start, end, capital, time.time() - t0)
    run.conflict_events = list(probe.events)
    return run


def compare_window(
    symbol: str,
    start: str,
    end: str,
    strategies: Sequence[str],
    capital: float = DEFAULT_CAPITAL,
    data_dir: Optional[str] = None,
    progress: bool = False,
) -> WindowComparison:
    """Run one window isolated-then-together and derive every comparison.

    Args:
        symbol: Trading pair.
        start: Window start (ISO date).
        end: Window end (ISO date).
        strategies: Snake_case strategy keys.
        capital: Initial capital for every run.
        data_dir: Absolute candle-store path override.
        progress: Print a per-run progress line to stdout.

    Returns:
        The populated WindowComparison.
    """
    isolated: Dict[str, RunResult] = {}
    for key in strategies:
        if progress:
            print(f"    isolated  {key:<24}", end="", flush=True)
        run = run_isolated(key, symbol, start, end, capital, data_dir)
        isolated[key] = run
        if progress:
            note = (
                f"ERROR {run.error}"
                if not run.ok
                else f"{run.total_return_pct:+.2f}%  "
                f"{run.closed_trades} closed  {run.elapsed_sec:.0f}s"
            )
            print(f"  {note}", flush=True)

    if progress:
        print(f"    {'PORTFOLIO':<34}", end="", flush=True)
    portfolio = run_portfolio(strategies, symbol, start, end, capital, data_dir)
    if progress:
        note = (
            f"ERROR {portfolio.error}"
            if not portfolio.ok
            else f"{portfolio.total_return_pct:+.2f}%  "
            f"{portfolio.closed_trades} closed  {portfolio.elapsed_sec:.0f}s"
        )
        print(f"  {note}", flush=True)

    return build_comparison(symbol, start, end, capital, portfolio, isolated)


def build_comparison(
    symbol: str,
    start: str,
    end: str,
    capital: float,
    portfolio: RunResult,
    isolated: Dict[str, RunResult],
) -> WindowComparison:
    """Derive every portfolio-vs-parts metric from finished runs.

    Separated from :func:`compare_window` so the analytics can be tested
    against hand-built runs without touching the candle store.

    Args:
        symbol: Trading pair.
        start: Window start.
        end: Window end.
        capital: Initial capital every run started with.
        portfolio: The combined run.
        isolated: strategy key -> that strategy's solo run.

    Returns:
        The populated WindowComparison.
    """
    comparison = WindowComparison(
        symbol=symbol,
        start=start,
        end=end,
        initial_capital=capital,
        portfolio=portfolio,
        isolated=dict(isolated),
    )

    usable = {
        name: run.equity_points
        for name, run in isolated.items()
        if run.ok and run.equity_points
    }
    if usable:
        _timestamps, aligned = align_curves(usable)
        blend = blend_equity(aligned, capital)
        additive = additive_equity(aligned, capital)
        if blend:
            comparison.blend_return_pct = (blend[-1] / capital - 1.0) * 100.0
            comparison.blend_max_drawdown_pct = max_drawdown_pct(blend, peak=capital)
            comparison.blend_sharpe = annualised_sharpe(period_returns(blend))
        if additive:
            comparison.additive_return_pct = (additive[-1] / capital - 1.0) * 100.0
            comparison.additive_max_drawdown_pct = max_drawdown_pct(
                additive, peak=capital
            )
        drawdowns = [run.max_drawdown_pct for run in isolated.values() if run.ok]
        comparison.mean_isolated_drawdown_pct = (
            sum(drawdowns) / len(drawdowns) if drawdowns else 0.0
        )
        returns_by_name = {
            name: period_returns(values) for name, values in aligned.items()
        }
        # A strategy that never traded has a flat curve and no meaningful
        # correlation; leaving it in would dilute the average with zeros.
        active = {
            name: series
            for name, series in returns_by_name.items()
            if any(r != 0.0 for r in series)
        }
        comparison.correlation = correlation_matrix(active)

    comparison.daily_correlation = correlation_matrix(
        dense_daily_series(daily_pnl_by_strategy(portfolio.trade_log))
    )
    comparison.conflict = conflict_stats(portfolio.diagnostics)
    probe = ConflictProbe()
    probe.events = list(portfolio.conflict_events)
    comparison.conflict_probe = probe.summary()
    comparison.exec_blocks = exec_block_stats(portfolio.diagnostics)

    combined_isolated: Dict[str, int] = {}
    for run in isolated.values():
        for reason, count in exec_block_stats(run.diagnostics).items():
            combined_isolated[reason] = combined_isolated.get(reason, 0) + count
    comparison.isolated_exec_blocks = dict(
        sorted(combined_isolated.items(), key=lambda kv: (-kv[1], kv[0]))
    )

    portfolio_attr = trade_attribution(portfolio.trade_log)
    isolated_attr = {
        name: trade_attribution(run.trade_log).get(
            name, {"closed_trades": 0, "pnl": 0.0}
        )
        for name, run in isolated.items()
        if run.ok
    }
    comparison.crowding = crowding_ratios(portfolio_attr, isolated_attr)

    exposure = occupancy_stats(portfolio.snapshots)
    exposure["largest_fill_notional"] = round(
        largest_fill_notional(portfolio.trade_log), 2
    )
    exposure["capital"] = capital
    exposure["risk_cap_notional"] = round(capital * _max_exposure_pct(), 2)
    exposure["risk_cap_consulted"] = False
    comparison.exposure = exposure
    return comparison


def _max_exposure_pct() -> float:
    """Return the configured portfolio exposure cap as a fraction.

    Read from ``MAX_PORTFOLIO_EXPOSURE_PCT`` with the RiskManager default,
    without constructing a RiskManager (which would want a client).

    Returns:
        The cap as a fraction of account balance.
    """
    raw = os.getenv("MAX_PORTFOLIO_EXPOSURE_PCT", "")
    try:
        value = float(raw)
    except ValueError:
        return 0.15
    return value if value > 0 else 0.15


# ---------------------------------------------------------------------------
# Campaign
# ---------------------------------------------------------------------------


@dataclass
class PortfolioValidation:
    """The whole chunked campaign: one WindowComparison per window."""

    symbol: str
    strategies: List[str]
    capital: float
    windows: List[WindowComparison] = field(default_factory=list)
    elapsed_sec: float = 0.0

    def mean(self, attribute: str) -> float:
        """Mean of a numeric attribute across windows.

        Args:
            attribute: WindowComparison attribute or property name.

        Returns:
            The mean, or 0.0 when there are no windows.
        """
        values = [float(getattr(w, attribute)) for w in self.windows]
        return sum(values) / len(values) if values else 0.0

    def pooled_correlation(self) -> Dict[str, Dict[str, float]]:
        """Average each correlation cell across the windows that have it.

        Returns:
            Pooled nested correlation dict.
        """
        totals: Dict[str, Dict[str, List[float]]] = {}
        for window in self.windows:
            for row, cells in window.correlation.items():
                for col, value in cells.items():
                    totals.setdefault(row, {}).setdefault(col, []).append(value)
        return {
            row: {col: sum(values) / len(values) for col, values in cells.items()}
            for row, cells in totals.items()
        }

    def verdict(self) -> Dict[str, Any]:
        """Return the headline finding: better or worse than its parts, why.

        Returns:
            ``{headline, return_edge_pct, drawdown_edge_pct,
            avg_correlation, factors}`` where ``factors`` names the
            mechanisms that explain the gap.
        """
        return_edge = self.mean("return_edge_pct")
        drawdown_edge = self.mean("drawdown_edge_pct")
        matrix = self.pooled_correlation()
        avg_corr = average_offdiagonal(matrix)

        factors: List[str] = []
        if avg_corr >= CORRELATED_THRESHOLD:
            pair = most_correlated_pair(matrix)
            detail = (
                f" (worst pair {pair[0]}/{pair[1]} at {pair[2]:+.2f})" if pair else ""
            )
            factors.append(
                f"correlated bets: mean pairwise correlation "
                f"{avg_corr:+.2f}{detail} - the isolated drawdowns land "
                f"together, so diversification is largely notional"
            )
        crowded = self._crowded_out()
        if crowded:
            names = ", ".join(f"{name} {ratio:.0%}" for name, ratio in crowded[:4])
            factors.append(
                f"capacity crowding: these strategies kept only a fraction "
                f"of their isolated trades once sharing the account "
                f"({names}) - one position per symbol means the first "
                f"strategy in occupies the slot"
            )
        conflicts = sum(
            int(w.conflict_probe.get("invocations") or 0) for w in self.windows
        )
        dropped = sum(int(w.conflict_probe.get("dropped") or 0) for w in self.windows)
        if conflicts:
            factors.append(
                f"conflict resolution: fired {conflicts} times, discarding "
                f"{dropped} candidate signals that isolation would have "
                f"traded"
            )
        else:
            factors.append(
                "conflict resolution never fired: no bar produced two "
                "competing signals, so that path is untested in practice"
            )

        if return_edge > 0 and drawdown_edge > 0:
            headline = "PORTFOLIO BEATS ITS PARTS on both return and drawdown"
        elif return_edge > 0:
            headline = "PORTFOLIO BEATS ITS PARTS on return but runs a deeper drawdown"
        elif drawdown_edge > 0:
            headline = "PORTFOLIO IS SAFER THAN ITS PARTS but gives up return"
        else:
            headline = "PORTFOLIO IS WORSE THAN ITS PARTS on both return and drawdown"
        return {
            "headline": headline,
            "return_edge_pct": return_edge,
            "drawdown_edge_pct": drawdown_edge,
            "avg_correlation": avg_corr,
            "factors": factors,
        }

    def _crowded_out(self) -> List[Tuple[str, float]]:
        """Strategies that lost a material share of their trades together."""
        totals: Dict[str, List[int]] = {}
        for window in self.windows:
            for name, cell in window.crowding.items():
                bucket = totals.setdefault(name, [0, 0])
                bucket[0] += int(cell.get("isolated") or 0)
                bucket[1] += int(cell.get("portfolio") or 0)
        out: List[Tuple[str, float]] = []
        for name, (alone, together) in totals.items():
            if alone <= 0:
                continue
            retention = together / alone
            if retention <= 1.0 - CROWDING_THRESHOLD:
                out.append((name, retention))
        out.sort(key=lambda kv: kv[1])
        return out

    def to_dict(self) -> Dict[str, Any]:
        """Return the whole campaign as a JSON-safe dict."""
        return {
            "symbol": self.symbol,
            "strategies": list(self.strategies),
            "capital": self.capital,
            "elapsed_sec": round(self.elapsed_sec, 1),
            "verdict": self.verdict(),
            "pooled_correlation": self.pooled_correlation(),
            "windows": [w.to_dict() for w in self.windows],
        }


def run_validation(
    symbol: str,
    windows: Sequence[Tuple[str, str]],
    strategies: Sequence[str],
    capital: float = DEFAULT_CAPITAL,
    data_dir: Optional[str] = None,
    progress: bool = True,
) -> PortfolioValidation:
    """Run the portfolio comparison over a series of chunked windows.

    Args:
        symbol: Trading pair.
        windows: ``(start, end)`` ISO date pairs, oldest first.
        strategies: Snake_case strategy keys.
        capital: Initial capital for every run.
        data_dir: Absolute candle-store path override.
        progress: Print per-run progress to stdout.

    Returns:
        The populated PortfolioValidation.
    """
    campaign = PortfolioValidation(
        symbol=symbol, strategies=list(strategies), capital=capital
    )
    t0 = time.time()
    for index, (start, end) in enumerate(windows, 1):
        if progress:
            print(
                f"  [{index}/{len(windows)}] window {start} -> {end}",
                flush=True,
            )
        campaign.windows.append(
            compare_window(
                symbol=symbol,
                start=start,
                end=end,
                strategies=strategies,
                capital=capital,
                data_dir=data_dir,
                progress=progress,
            )
        )
    campaign.elapsed_sec = time.time() - t0
    return campaign


def estimate_campaign_seconds(
    n_strategies: int,
    n_windows: int,
    window_months: int,
    seconds_per_month: float,
) -> float:
    """Estimate wall-clock seconds for a full portfolio campaign.

    A window costs ``n_strategies`` isolated runs plus one portfolio run,
    and each run's cost scales with the months it replays.

    Args:
        n_strategies: Strategies in the set.
        n_windows: Windows in the series.
        window_months: Months per window.
        seconds_per_month: Measured seconds per replayed month for a
            single run.

    Returns:
        Estimated seconds (0.0 when any factor is non-positive).
    """
    runs = max(0, n_strategies) + 1
    total_months = runs * max(0, n_windows) * max(0, window_months)
    return float(total_months) * max(0.0, float(seconds_per_month))


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _fmt_pct(value: float) -> str:
    return f"{value:+.2f}%"


def format_correlation_matrix(
    matrix: Dict[str, Dict[str, float]], width: int = 22
) -> List[str]:
    """Render a correlation matrix as fixed-width text rows.

    Args:
        matrix: Output of :func:`correlation_matrix`.
        width: Column width for the row-label column.

    Returns:
        Lines of text (empty list for a matrix with fewer than two names).
    """
    names = list(matrix)
    if len(names) < 2:
        return ["  (fewer than two active strategies - no correlation)"]
    short = {name: name[:7] for name in names}
    header = " " * width + "".join(f"{short[n]:>9}" for n in names)
    lines = [header]
    for row in names:
        cells = "".join(f"{matrix[row].get(col, 0.0):>+9.2f}" for col in names)
        lines.append(f"{row[: width - 1]:<{width}}{cells}")
    return lines


def format_report(campaign: PortfolioValidation) -> str:
    """Render the whole campaign as a plain-text report.

    Args:
        campaign: A finished PortfolioValidation.

    Returns:
        The report text.
    """
    out: List[str] = []
    add = out.append

    add("")
    add(f"  Portfolio Validation  --  {campaign.symbol}")
    add(f"  Strategies : {', '.join(campaign.strategies)}")
    add(f"  Capital    : ${campaign.capital:,.0f} per run")
    add(f"  Windows    : {len(campaign.windows)}")
    add(f"  Runtime    : {campaign.elapsed_sec:.0f}s")
    add("")

    add("  === Per-window: portfolio vs sum of parts ===")
    add("")
    add(
        f"  {'Window':<24}{'Portfolio':>12}{'Blend':>10}{'Edge':>10}"
        f"{'PortDD':>9}{'BlendDD':>9}{'DDEdge':>9}{'Trades':>8}"
    )
    add("  " + "-" * 91)
    for window in campaign.windows:
        label = f"{window.start}..{window.end}"
        add(
            f"  {label:<24}"
            f"{_fmt_pct(window.portfolio.total_return_pct):>12}"
            f"{_fmt_pct(window.blend_return_pct):>10}"
            f"{_fmt_pct(window.return_edge_pct):>10}"
            f"{window.portfolio.max_drawdown_pct:>8.2f}%"
            f"{window.blend_max_drawdown_pct:>8.2f}%"
            f"{_fmt_pct(window.drawdown_edge_pct):>9}"
            f"{window.portfolio.closed_trades:>8}"
        )
    add("  " + "-" * 91)
    add(
        f"  {'MEAN':<24}"
        f"{_fmt_pct(_mean_portfolio_return(campaign)):>12}"
        f"{_fmt_pct(campaign.mean('blend_return_pct')):>10}"
        f"{_fmt_pct(campaign.mean('return_edge_pct')):>10}"
        f"{_mean_portfolio_dd(campaign):>8.2f}%"
        f"{campaign.mean('blend_max_drawdown_pct'):>8.2f}%"
        f"{_fmt_pct(campaign.mean('drawdown_edge_pct')):>9}"
    )
    add("")
    add("  Blend = equal-weight blend of the isolated runs (the naive sum-of-parts).")
    add(
        "  Additive stack (each strategy given the FULL account, which is "
        f"what reading the sweep side by side implies): "
        f"{_fmt_pct(campaign.mean('additive_return_pct'))} return, "
        f"{campaign.mean('additive_max_drawdown_pct'):.2f}% max DD."
    )
    add(
        f"  Mean isolated max drawdown: "
        f"{campaign.mean('mean_isolated_drawdown_pct'):.2f}% - if the blend "
        f"drawdown is not meaningfully below this, the strategies are "
        f"drawing down together."
    )
    add("")

    add("  === Per-strategy: isolated vs inside the portfolio ===")
    add("")
    add(
        f"  {'Strategy':<24}{'Alone ret':>11}{'Alone tr':>10}"
        f"{'Port tr':>9}{'Retained':>10}{'Alone PnL':>12}{'Port PnL':>11}"
    )
    add("  " + "-" * 87)
    aggregate: Dict[str, Dict[str, float]] = {}
    for window in campaign.windows:
        for name, cell in window.crowding.items():
            bucket = aggregate.setdefault(
                name,
                {"isolated": 0.0, "portfolio": 0.0, "pnl_i": 0.0, "pnl_p": 0.0},
            )
            bucket["isolated"] += float(cell.get("isolated") or 0)
            bucket["portfolio"] += float(cell.get("portfolio") or 0)
            bucket["pnl_i"] += float(cell.get("pnl_isolated") or 0.0)
            bucket["pnl_p"] += float(cell.get("pnl_portfolio") or 0.0)
    isolated_returns: Dict[str, List[float]] = {}
    for window in campaign.windows:
        for name, run in window.isolated.items():
            if run.ok:
                isolated_returns.setdefault(name, []).append(run.total_return_pct)
    for name in campaign.strategies:
        bucket = aggregate.get(
            name, {"isolated": 0.0, "portfolio": 0.0, "pnl_i": 0.0, "pnl_p": 0.0}
        )
        rets = isolated_returns.get(name, [])
        mean_ret = sum(rets) / len(rets) if rets else 0.0
        alone = bucket["isolated"]
        retained = (bucket["portfolio"] / alone) if alone else 0.0
        add(
            f"  {name:<24}"
            f"{_fmt_pct(mean_ret):>11}"
            f"{int(alone):>10}"
            f"{int(bucket['portfolio']):>9}"
            f"{retained:>9.0%} "
            f"{bucket['pnl_i']:>+12.2f}"
            f"{bucket['pnl_p']:>+11.2f}"
        )
    add("")

    add("  === Correlation of per-strategy returns (isolated equity) ===")
    add("")
    for line in format_correlation_matrix(campaign.pooled_correlation()):
        add("  " + line)
    add("")
    add(
        f"  Mean pairwise correlation: "
        f"{average_offdiagonal(campaign.pooled_correlation()):+.3f}"
    )
    add("")

    add("  === Conflict resolution ===")
    add("")
    total_invocations = 0
    total_dropped = 0
    dropped_by: Dict[str, int] = {}
    won_by: Dict[str, int] = {}
    for window in campaign.windows:
        probe = window.conflict_probe
        total_invocations += int(probe.get("invocations") or 0)
        total_dropped += int(probe.get("dropped") or 0)
        for name, count in (probe.get("dropped_by_strategy") or {}).items():
            dropped_by[name] = dropped_by.get(name, 0) + count
        for name, count in (probe.get("won_by_strategy") or {}).items():
            won_by[name] = won_by.get(name, 0) + count
    funnel_dropped = sum(
        int(w.conflict.get("total_dropped") or 0) for w in campaign.windows
    )
    raw_signals = sum(int(w.conflict.get("raw_signals") or 0) for w in campaign.windows)
    add(f"  Invocations (bars with >1 competing signal) : {total_invocations}")
    add(f"  Candidates discarded (probe)                : {total_dropped}")
    add(f"  Candidates discarded (funnel counter)       : {funnel_dropped}")
    add(f"  Raw signals in the portfolio run            : {raw_signals}")
    if dropped_by:
        add(
            "  Lost the resolution: "
            + ", ".join(
                f"{k}={v}" for k, v in sorted(dropped_by.items(), key=lambda kv: -kv[1])
            )
        )
    if won_by:
        add(
            "  Won the resolution : "
            + ", ".join(
                f"{k}={v}" for k, v in sorted(won_by.items(), key=lambda kv: -kv[1])
            )
        )
    if not total_invocations:
        add(
            "  Conflict resolution never fired in this campaign: no bar "
            "produced two competing signals."
        )
    add("")

    add("  === Exposure: does anything bind? ===")
    add("")
    occupancy = [
        float(w.exposure.get("occupancy_pct") or 0.0) for w in campaign.windows
    ]
    max_concurrent = max(
        [int(w.exposure.get("max_concurrent_positions") or 0) for w in campaign.windows]
        or [0]
    )
    largest = max(
        [
            float(w.exposure.get("largest_fill_notional") or 0.0)
            for w in campaign.windows
        ]
        or [0.0]
    )
    cap = campaign.capital * _max_exposure_pct()
    add(
        f"  Mean position occupancy      : "
        f"{(sum(occupancy) / len(occupancy) if occupancy else 0.0):.1f}% of "
        f"equity snapshots had a position open"
    )
    add(f"  Max concurrent positions     : {max_concurrent}")
    add(
        f"  Largest single fill notional : ${largest:,.2f}  "
        f"(exposure cap would be ${cap:,.2f})"
    )
    add(
        "  RiskManager.max_portfolio_exposure_pct is NOT consulted on the "
        "backtest sizing path:"
    )
    add(
        "  BacktestEngine._execute_signal sizes from signal.quantity (or a "
        "flat 2% of balance)"
    )
    add(
        "  and only GridTrading asks RiskManager for capital. The cap "
        "therefore cannot bind here."
    )
    add("")
    add("  Execution-gate blocks, portfolio run vs sum of the isolated runs:")
    reasons = sorted(
        set().union(
            *[set(w.exec_blocks) for w in campaign.windows] or [set()],
            *[set(w.isolated_exec_blocks) for w in campaign.windows] or [set()],
        )
    )
    if reasons:
        add(f"    {'reason':<32}{'portfolio':>12}{'isolated sum':>14}")
        for reason in reasons:
            port = sum(int(w.exec_blocks.get(reason) or 0) for w in campaign.windows)
            iso = sum(
                int(w.isolated_exec_blocks.get(reason) or 0) for w in campaign.windows
            )
            add(f"    {reason:<32}{port:>12}{iso:>14}")
    else:
        add("    (no execution-gate rejections recorded)")
    add("")

    verdict = campaign.verdict()
    add("  === Verdict ===")
    add("")
    add(f"  {verdict['headline']}")
    add(
        f"  return edge {_fmt_pct(verdict['return_edge_pct'])} | "
        f"drawdown edge {_fmt_pct(verdict['drawdown_edge_pct'])} | "
        f"mean correlation {verdict['avg_correlation']:+.2f}"
    )
    add("")
    for factor in verdict["factors"]:
        add(f"  - {factor}")
    add("")
    return "\n".join(out)


def _mean_portfolio_return(campaign: PortfolioValidation) -> float:
    """Mean portfolio total return across windows."""
    values = [w.portfolio.total_return_pct for w in campaign.windows]
    return sum(values) / len(values) if values else 0.0


def _mean_portfolio_dd(campaign: PortfolioValidation) -> float:
    """Mean portfolio max drawdown across windows."""
    values = [w.portfolio.max_drawdown_pct for w in campaign.windows]
    return sum(values) / len(values) if values else 0.0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def resolve_windows(
    symbol: str,
    args: argparse.Namespace,
    data_dir: Optional[str],
) -> List[Tuple[str, str]]:
    """Resolve the window series from CLI arguments.

    An explicit ``--start``/``--end`` pair wins; otherwise a chunked
    series is cut from the symbol's 1m coverage using the same window
    machinery as validation/runner.py.

    Args:
        symbol: Trading pair.
        args: Parsed CLI namespace.
        data_dir: Candle-store path override.

    Returns:
        ``(start, end)`` ISO date pairs, oldest first.
    """
    if args.start and args.end:
        return [(args.start, args.end)]

    from .runner import _coverage_1m_end, _coverage_1m_start, cut_windows

    resolved_dir = data_dir
    if not resolved_dir:
        from ..config import config as cfg

        resolved_dir = cfg.backtest_data_dir
    data_end = _coverage_1m_end(symbol, resolved_dir)
    data_start = _coverage_1m_start(symbol, resolved_dir)
    if data_end is None:
        raise SystemExit(
            f"No 1m candle data on disk for {symbol} under {resolved_dir}. "
            f"Pass --start/--end explicitly, or --data-dir with the absolute "
            f"path to the candle store."
        )
    if args.anchor_end:
        data_end = date.fromisoformat(args.anchor_end)
    return cut_windows(
        data_end=data_end,
        window_months=args.window_months,
        n_windows=args.windows,
        data_start=data_start,
        mode=args.mode,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m trading_bot_v2.validation.portfolio",
        description=(
            "Run the enabled strategies together and report what "
            "single-strategy validation cannot show."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--symbol", "-s", default=DEFAULT_SYMBOL)
    parser.add_argument(
        "--strategies",
        nargs="+",
        metavar="KEY",
        help=(
            "Snake_case strategy keys. Default: every ENABLE_*-true "
            "strategy the engine can backtest."
        ),
    )
    parser.add_argument("--capital", "-c", type=float, default=DEFAULT_CAPITAL)
    parser.add_argument("--start", help="Explicit window start (YYYY-MM-DD)")
    parser.add_argument("--end", help="Explicit window end (YYYY-MM-DD)")
    parser.add_argument(
        "--windows",
        type=int,
        default=DEFAULT_WINDOWS,
        help="Number of chunk windows (default: %(default)s)",
    )
    parser.add_argument(
        "--window-months",
        type=int,
        default=DEFAULT_WINDOW_MONTHS,
        help="Months per chunk window (default: %(default)s)",
    )
    parser.add_argument(
        "--mode",
        choices=("spread", "recent"),
        default="spread",
        help="Window layout across available history (default: %(default)s)",
    )
    parser.add_argument("--anchor-end", help="Override the newest window's end date")
    parser.add_argument(
        "--data-dir",
        help=(
            "Absolute path to the candle store. Required from a git "
            "worktree, where .env's relative BACKTEST_DATA_DIR does not "
            "resolve."
        ),
    )
    parser.add_argument("--json", help="Write the full findings to this path")
    parser.add_argument(
        "--quiet", action="store_true", help="Silence engine log output"
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="Print the window plan and runtime estimate, then exit",
    )
    parser.add_argument(
        "--seconds-per-month",
        type=float,
        default=DEFAULT_SECONDS_PER_MONTH,
        help="Per-run cost used for the estimate (default: %(default)s)",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument vector (default: ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """
    args = build_parser().parse_args(argv)

    if args.quiet:
        from loguru import logger

        logger.remove()

    strategies = list(args.strategies) if args.strategies else None
    if strategies is None:
        strategies = default_strategy_set()
    else:
        strategies = backtestable_strategies(strategies)
    if not strategies:
        print("No backtestable strategies selected.", file=sys.stderr)
        return 2

    windows = resolve_windows(args.symbol, args, args.data_dir)
    if not windows:
        print("No windows resolved.", file=sys.stderr)
        return 2

    print()
    print(f"  Portfolio validation  --  {args.symbol}")
    print(f"  Strategies : {', '.join(strategies)}")
    print(f"  Windows    : {len(windows)} x {args.window_months}mo")
    for start, end in windows:
        print(f"      {start} -> {end}")
    estimate = estimate_campaign_seconds(
        len(strategies),
        len(windows),
        args.window_months,
        args.seconds_per_month,
    )
    from .runner import format_duration

    print(
        f"  Estimated  : {format_duration(estimate)} "
        f"({len(strategies) + 1} runs x {len(windows)} windows)"
    )
    print()
    if args.plan:
        return 0

    campaign = run_validation(
        symbol=args.symbol,
        windows=windows,
        strategies=strategies,
        capital=args.capital,
        data_dir=args.data_dir,
        progress=True,
    )
    print(format_report(campaign))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(campaign.to_dict(), handle, indent=2)
        print(f"  Findings written to {args.json}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
