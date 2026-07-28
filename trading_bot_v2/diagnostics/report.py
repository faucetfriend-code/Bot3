"""
Funnel Reports
==============

Renders a :class:`~trading_bot_v2.diagnostics.funnel.SignalFunnel` in the
same fixed-width house style as ``validation/gate.py::print_verdict``.

The block is shown on BOTH success and failure. On a passing run it still
reports where the largest attrition happened - "82% of raw signals died at
the confidence gate" is actionable even at Sharpe 1.4.

Usage:
    from trading_bot_v2.diagnostics.report import print_funnel_report

    print_funnel_report(result.diagnostics, title="momentum_scalping")
"""

from typing import Any, Dict, List, Optional, Union

from .funnel import DROP_STAGES, STAGES, SignalFunnel
from .outcomes import suggest_fix

WIDTH = 78
_INNER = WIDTH - 4

#: Stages rendered in the report, in pipeline order.
_REPORT_STAGES = STAGES

FunnelLike = Union[Dict[str, Any], SignalFunnel, None]


def _as_dict(funnel: FunnelLike) -> Dict[str, Any]:
    """Normalise a funnel-ish argument to a to_dict() payload.

    Args:
        funnel: SignalFunnel, its to_dict() payload, or None.

    Returns:
        A dict payload (possibly empty).
    """
    if funnel is None:
        return {}
    if isinstance(funnel, dict):
        return funnel
    to_dict = getattr(funnel, "to_dict", None)
    if callable(to_dict):
        return to_dict() or {}
    return {}


def _derived(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Fill in derived fields (diagnosis/headline/...) when absent.

    Args:
        payload: A to_dict() payload, possibly stored without derived keys.

    Returns:
        The payload with derived fields guaranteed present.
    """
    if not payload or "diagnosis" in payload:
        return payload
    rebuilt = SignalFunnel.from_dict(payload).to_dict()
    # Anything the caller stored alongside the counters (e.g.
    # suggested_fix written by the optimizer) survives the rebuild.
    for key, value in payload.items():
        if key not in rebuilt:
            rebuilt[key] = value
    return rebuilt


def _attrition(stages: Dict[str, int], stage: str) -> str:
    """Return the attrition percentage string for a drop stage.

    Args:
        stages: Stage counter map.
        stage: Drop-stage name.

    Returns:
        Formatted percentage of raw signals lost, or "-".
    """
    if stage not in DROP_STAGES:
        return "-"
    raw = stages.get("raw_signals", 0)
    if raw <= 0:
        return "-"
    return f"{stages.get(stage, 0) / raw * 100:.1f}% of raw"


def render_funnel_report(
    funnel: FunnelLike,
    title: str = "",
    params: Optional[Dict[str, Any]] = None,
    max_reasons: int = 5,
) -> str:
    """Render a funnel as a fixed-width text block.

    Args:
        funnel: SignalFunnel or its to_dict() payload.
        title: Heading suffix (strategy / symbol / study).
        params: Sampled parameters, used to sharpen the suggested fix.
        max_reasons: Maximum rejection reasons listed.

    Returns:
        The report text (no trailing newline duplication).
    """
    payload = _derived(_as_dict(funnel))
    lines: List[str] = []
    bar = "=" * WIDTH
    rule = "-" * (WIDTH - 4)

    heading = "SIGNAL FUNNEL"
    label = title or str(payload.get("label") or "")
    if label:
        heading = f"{heading}: {label}"

    lines.append(bar)
    lines.append(heading)
    lines.append(bar)

    if not payload:
        lines.append("  No diagnostics recorded for this run.")
        lines.append(bar)
        return "\n".join(lines)

    stages: Dict[str, int] = payload.get("stages") or {}
    outcome = str(payload.get("diagnosis") or "unknown")
    headline = str(payload.get("headline") or "")

    lines.append(f"  {'Outcome':<26} {outcome}")
    if headline:
        for chunk in _wrap(headline, _INNER - 27):
            lines.append(f"  {'':<26} {chunk}")
    lines.append(f"  {rule}")
    lines.append(f"  {'Stage':<26} {'Count':>10}   Attrition")
    lines.append(f"  {rule}")
    for stage in _REPORT_STAGES:
        if stage not in stages:
            continue
        lines.append(
            f"  {stage:<26} {stages.get(stage, 0):>10}   "
            f"{_attrition(stages, stage)}"
        )
    lines.append(f"  {rule}")

    by_strategy: Dict[str, Dict[str, int]] = payload.get("by_strategy") or {}
    if by_strategy:
        lines.append("  BY STRATEGY")
        lines.append(
            f"  {'  strategy':<26} {'invoked':>10} {'raw':>8} {'dropped':>9}"
        )
        for name in sorted(by_strategy):
            cell = by_strategy[name] or {}
            dropped = sum(cell.get(s, 0) for s in DROP_STAGES)
            if not (
                cell.get("strategy_invoked", 0)
                or cell.get("raw_signals", 0)
                or dropped
            ):
                # Selected by the regime but disabled for this run.
                continue
            lines.append(
                f"  {'  ' + name:<26} "
                f"{cell.get('strategy_invoked', 0):>10} "
                f"{cell.get('raw_signals', 0):>8} "
                f"{dropped:>9}"
            )
        lines.append(f"  {rule}")

    regimes: Dict[str, int] = payload.get("regimes") or {}
    if regimes:
        ordered = sorted(regimes.items(), key=lambda kv: (-kv[1], kv[0]))
        desc = ", ".join(f"{k}={v}" for k, v in ordered)
        lines.append(f"  {'Regimes (bars)':<26} {desc}")
        lines.append(f"  {rule}")

    # Gate metrics are a Phase 4 (calibration) artifact. Degrade
    # gracefully: the section is simply absent when no hook supplied it.
    gate_metrics = (payload.get("notes") or {}).get("gate_metrics")
    if gate_metrics:
        lines.append("  GATE METRICS")
        for metric, info in sorted(gate_metrics.items()):
            lines.append(f"  {'  ' + str(metric):<26} {info}")
        lines.append(f"  {rule}")

    # Execution policy behind the exec:* reasons below. Absent for funnels
    # produced outside the backtest engine (e.g. the live NullFunnel).
    exec_policy = (payload.get("notes") or {}).get("execution_policy")
    if exec_policy:
        desc = ", ".join(f"{k}={v}" for k, v in sorted(exec_policy.items()))
        wrapped = _wrap(desc, _INNER - 28)
        lines.append(f"  {'Execution policy':<26} {wrapped[0]}")
        for chunk in wrapped[1:]:
            lines.append(f"  {'':<26} {chunk}")
        lines.append(f"  {rule}")

    binding = str(payload.get("binding_stage") or "")
    lines.append(f"  BINDING CONSTRAINT: {binding or 'unknown'}")
    top_reasons = payload.get("top_reasons") or []
    for entry in top_reasons[:max_reasons]:
        try:
            reason, count = entry[0], entry[1]
        except (IndexError, TypeError):
            continue
        lines.append(f"  {'  ' + str(reason):<44} {count:>10}")
    if not top_reasons:
        lines.append("    (no rejection reasons recorded)")

    fix = str(payload.get("suggested_fix") or "") or suggest_fix(payload, params)
    if fix:
        lines.append(f"  {rule}")
        wrapped = _wrap(fix, _INNER - 16)
        lines.append(f"  SUGGESTED FIX: {wrapped[0]}")
        for chunk in wrapped[1:]:
            lines.append(f"  {'':<15}{chunk}")

    lines.append(bar)
    return "\n".join(lines)


def print_funnel_report(
    funnel: FunnelLike,
    title: str = "",
    params: Optional[Dict[str, Any]] = None,
) -> None:
    """Print :func:`render_funnel_report` output.

    Args:
        funnel: SignalFunnel or its to_dict() payload.
        title: Heading suffix.
        params: Sampled parameters for the suggested fix.
    """
    print()
    print(render_funnel_report(funnel, title=title, params=params))
    print()


def funnel_one_liner(funnel: FunnelLike) -> str:
    """Return a single-cell summary, for table columns.

    Args:
        funnel: SignalFunnel or its to_dict() payload.

    Returns:
        The outcome name, or "n/a" when no diagnostics exist.
    """
    payload = _as_dict(funnel)
    if not payload:
        return "n/a"
    diagnosis = payload.get("diagnosis")
    if diagnosis:
        return str(diagnosis)
    return SignalFunnel.from_dict(payload).diagnose()


def _wrap(text: str, width: int) -> List[str]:
    """Wrap text to a width without importing textwrap for one call.

    Args:
        text: Text to wrap.
        width: Maximum line width (minimum 20).

    Returns:
        List of lines (at least one).
    """
    width = max(20, width)
    words = str(text).split()
    if not words:
        return [""]
    lines: List[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines
