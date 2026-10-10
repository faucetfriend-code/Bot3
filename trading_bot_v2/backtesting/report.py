"""
Backtest results report
=======================

Turns a :class:`~trading_bot_v2.backtesting.performance.BacktestResult`
into one JSON document (the record) and one plain-text summary (the
thing a person reads). Everything the JSON holds is derived from the
result and the inputs the harness passes in; nothing is recomputed from
the data files.

JSON layout (``schema`` = ``bot3.backtest-harness/1``)::

    run          what was run: symbol, window, mode, strategies, costs
    data         per-timeframe validation (rows, span, gaps, repairs)
    metrics      return, CAGR, Sharpe, Sortino, drawdown, win rate, PF,
                 fills, closed trades, fees, funding
    drawdown     max drawdown with its peak/trough/recovery stamps and
                 the longest underwater stretch, from the equity curve
    by_strategy  fills / closed trades / PnL / fees per strategy
    by_regime    closed trades per entry regime (pipeline mode only)
    adapters     per-strategy call/signal/error counters (direct mode)
    trades       every fill the simulated exchange recorded
    equity_curve every per-bar equity snapshot
    diagnostics  the signal funnel, when the run was instrumented
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .performance import BacktestResult

SCHEMA = "bot3.backtest-harness/1"


def _json_safe(value: Any) -> Any:
    """Recursively convert a value into something ``json.dumps`` accepts."""
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):  # Enum
        return _json_safe(value.value)
    if hasattr(value, "to_dict"):
        return _json_safe(value.to_dict())
    return str(value)


def drawdown_profile(equity_curve: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Locate the deepest and the longest drawdown on an equity curve.

    Args:
        equity_curve: Snapshots with ``timestamp`` and ``equity`` keys,
            oldest first (what PerformanceTracker records).

    Returns:
        ``max_drawdown_pct`` plus the ``peak`` / ``trough`` / ``recovery``
        timestamps of that drawdown (``recovery`` None when still under
        water at the end), and ``longest_underwater_hours``.
    """
    profile: Dict[str, Any] = {
        "max_drawdown_pct": 0.0,
        "peak": None,
        "trough": None,
        "recovery": None,
        "longest_underwater_hours": 0.0,
    }
    if not equity_curve:
        return profile
    peak_equity = float(equity_curve[0]["equity"])
    peak_ts = equity_curve[0]["timestamp"]
    worst = 0.0
    worst_peak_ts: Any = peak_ts
    worst_trough_ts: Any = peak_ts
    worst_recovery: Any = None
    worst_open = False
    for snap in equity_curve:
        equity = float(snap["equity"])
        if equity >= peak_equity:
            if worst_open:
                worst_recovery = snap["timestamp"]
                worst_open = False
            _note_underwater(profile, peak_ts, snap["timestamp"])
            peak_equity, peak_ts = equity, snap["timestamp"]
            continue
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
        if dd > worst:
            worst = dd
            worst_peak_ts, worst_trough_ts = peak_ts, snap["timestamp"]
            worst_recovery, worst_open = None, True
    _note_underwater(profile, peak_ts, equity_curve[-1]["timestamp"])
    profile.update(
        max_drawdown_pct=worst * 100,
        peak=worst_peak_ts,
        trough=worst_trough_ts,
        recovery=worst_recovery,
    )
    return profile


def _note_underwater(profile: Dict[str, Any], since: Any, until: Any) -> None:
    """Record ``until - since`` as the longest underwater stretch if it is."""
    try:
        hours = (
            datetime.fromisoformat(str(until)) - datetime.fromisoformat(str(since))
        ).total_seconds() / 3600.0
    except (TypeError, ValueError):
        return
    if hours > profile["longest_underwater_hours"]:
        profile["longest_underwater_hours"] = round(hours, 2)


def metrics_block(result: BacktestResult) -> Dict[str, Any]:
    """The headline numbers, as the summary prints them."""
    return {
        "initial_capital": result.initial_capital,
        "final_equity": result.final_equity,
        "net_pnl": result.final_equity - result.initial_capital,
        "total_return_pct": result.total_return_pct,
        "cagr_pct": result.cagr_pct,
        "sharpe_ratio": result.sharpe_ratio,
        "sortino_ratio": result.sortino_ratio,
        "max_drawdown_pct": result.max_drawdown_pct,
        "calmar_ratio": result.calmar_ratio,
        "win_rate_pct": result.win_rate_pct,
        "profit_factor": (
            result.profit_factor if math.isfinite(result.profit_factor) else None
        ),
        "fills": result.total_trades,
        "closed_trades": result.closed_trades,
        "total_fees": result.total_fees,
        "avg_fee_per_fill": result.avg_fee_per_trade,
        "funding_paid": result.total_funding_paid,
    }


def build_report(
    result: BacktestResult,
    run: Mapping[str, Any],
    validations: Optional[Mapping[str, Any]] = None,
    adapters: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the JSON-safe report document.

    Args:
        result: Finalised backtest result.
        run: Description of the run (symbol, window, mode, strategies,
            cost settings); stored verbatim under ``run``.
        validations: Per-timeframe CandleValidation (or dicts).
        adapters: Per-strategy AdapterStats (or dicts), direct mode.

    Returns:
        A dict ready for ``json.dumps``.
    """
    return _json_safe(
        {
            "schema": SCHEMA,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "run": dict(run),
            "data": {tf: v for tf, v in (validations or {}).items()},
            "metrics": metrics_block(result),
            "drawdown": drawdown_profile(result.equity_curve),
            "by_strategy": result.by_strategy,
            "by_regime": result.by_regime,
            "adapters": {k: v for k, v in (adapters or {}).items()},
            "trades": list(result.trade_log),
            "equity_curve": list(result.equity_curve),
            "diagnostics": result.diagnostics,
        }
    )


def _fmt(value: Any, spec: str = ",.2f", suffix: str = "") -> str:
    """Format a number, printing ``n/a`` for None."""
    if value is None:
        return "n/a"
    return f"{value:{spec}}{suffix}"


def render_summary(report: Mapping[str, Any]) -> str:
    """Render the human-readable summary of a report dict."""
    run = report["run"]
    m = report["metrics"]
    dd = report["drawdown"]
    lines: List[str] = [
        "=" * 64,
        f"BACKTEST HARNESS  {run.get('symbol')}  {run.get('start')} -> {run.get('end')}",
        f"mode={run.get('mode')}  strategies={', '.join(run.get('strategies', []))}",
        "=" * 64,
        f"  Initial capital : {_fmt(m['initial_capital'])}",
        f"  Final equity    : {_fmt(m['final_equity'])}",
        f"  Net PnL         : {_fmt(m['net_pnl'], '+,.2f')}",
        f"  Total return    : {_fmt(m['total_return_pct'], '+.2f', '%')}",
        f"  CAGR            : {_fmt(m['cagr_pct'], '+.2f', '%')}",
        f"  Sharpe          : {_fmt(m['sharpe_ratio'], '.2f')}",
        f"  Sortino         : {_fmt(m['sortino_ratio'], '.2f')}",
        f"  Max drawdown    : {_fmt(m['max_drawdown_pct'], '.2f', '%')}"
        f"  (peak {dd.get('peak')}, trough {dd.get('trough')}, "
        f"recovered {dd.get('recovery') or 'no'})",
        f"  Longest u/water : {_fmt(dd.get('longest_underwater_hours'), '.1f', 'h')}",
        f"  Win rate        : {_fmt(m['win_rate_pct'], '.1f', '%')}",
        f"  Profit factor   : {_fmt(m['profit_factor'], '.2f')}",
        f"  Fills / closed  : {m['fills']} / {m['closed_trades']}",
        f"  Fees            : {_fmt(m['total_fees'])}",
        f"  Funding paid    : {_fmt(m['funding_paid'])}",
    ]
    lines += _strategy_lines(report)
    lines += _data_lines(report)
    lines.append("=" * 64)
    return "\n".join(lines) + "\n"


def _strategy_lines(report: Mapping[str, Any]) -> List[str]:
    """Per-strategy table rows for the summary."""
    cells = report.get("by_strategy") or {}
    adapters = report.get("adapters") or {}
    if not cells and not adapters:
        return ["", "  No fills were recorded."]
    lines = ["", "  Per strategy:"]
    names = sorted(set(cells) | set(adapters))
    for name in names:
        c = cells.get(name, {})
        a = adapters.get(name, {})
        lines.append(
            f"    {name:<20} fills={c.get('fills', 0):<4} "
            f"closed={c.get('closed_trades', 0):<4} "
            f"win={_fmt(c.get('win_rate_pct'), '.1f', '%'):<7} "
            f"pf={_fmt(c.get('profit_factor'), '.2f'):<6} "
            f"pnl={_fmt(c.get('net_pnl'), '+,.2f'):<12} "
            f"fees={_fmt(c.get('fees')):<9}"
            + (
                f" calls={a.get('calls', 0)} signals={a.get('signals', 0)} "
                f"errors={a.get('errors', 0)}"
                if a
                else ""
            )
        )
    return lines


def _data_lines(report: Mapping[str, Any]) -> List[str]:
    """Per-timeframe data validation rows for the summary."""
    data = report.get("data") or {}
    if not data:
        return []
    lines = ["", "  Data:"]
    for tf, v in data.items():
        issues = []
        for key in ("duplicates", "out_of_order", "gaps", "bad_values", "off_grid"):
            if v.get(key):
                issues.append(f"{key}={v[key]}")
        flag = "OK" if v.get("ok", True) else "FAIL"
        extra = f" [{' '.join(issues)}]" if issues else ""
        lines.append(
            f"    {tf:>3}: {flag} {v.get('rows', 0)} rows "
            f"{v.get('first')} .. {v.get('last')}{extra}"
        )
    return lines


def write_report(
    report: Mapping[str, Any], output_dir: Path, basename: str
) -> Tuple[Path, Path]:
    """Write ``<basename>.json`` and ``<basename>.txt`` into ``output_dir``.

    Args:
        report: Output of :func:`build_report`.
        output_dir: Directory, created if missing.
        basename: File stem for both files.

    Returns:
        (json_path, txt_path).
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{basename}.json"
    txt_path = output_dir / f"{basename}.txt"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n")
    txt_path.write_text(render_summary(report))
    return json_path, txt_path
