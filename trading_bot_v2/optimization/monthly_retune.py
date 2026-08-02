"""
Monthly re-tune driver: the scheduled first-of-month maintenance pass.

Runs three steps and writes a machine- and human-readable report:

  1. REFRESH  - top up the candle + funding parquet stores to now
                (subprocess: trading_bot_v2.data_manager --update).
  2. SCORECARD - backtest each shipped strategy config over the previous
                calendar month on every symbol (in-process, defaults =
                whatever .env ships), so "how did last month go" is one
                table.
  3. RETUNE   - walk-forward composite-state tuning for mean_reversion
                on a rolling window ending at the month boundary
                (subprocess: run_composite_tuning), then compare the
                fresh per-state medians against the CURRENTLY ADOPTED
                params. The adopted params ride along as a THIRD arm
                (--baseline-params) so they get an out-of-sample score
                on the same unseen test windows - without it leg 2 of
                the adoption rule is unmeasurable and the verdict is
                "no change" by construction, whatever the data says.

The driver only MEASURES. It never edits .env, never touches overlay
rows, never flips enables. Adoption stays a human/reviewer decision
under the pre-registered rule (fresh tuned must beat BOTH defaults and
the currently adopted params on OOS folds).

Usage (from Bot3 root):

    python -m trading_bot_v2.optimization.monthly_retune
    python -m trading_bot_v2.optimization.monthly_retune --month 2026-07
    python -m trading_bot_v2.optimization.monthly_retune --skip-refresh --skip-tune

Bulk subprocess logs go to --log-dir (default: the G: holding folder;
falls back to out/monthly/logs when the drive is absent).
"""

import argparse
import json
import logging
import os
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCORECARD_STRATEGIES = [
    "momentum_scalping",
    "vwap_pullback",
    "mean_reversion",
    "grid_trading",
]
DEFAULT_SYMBOLS = "BTC-USDC,ETH-USDC,SUI-USDC"
RETUNE_STRATEGY = "mean_reversion"
RETUNE_SYMBOL = "BTC-USDC"

# The currently adopted composite-state variants (frozen medians from
# the 2026-07-30 walk-forward run, cross-validated on ETH + SUI).
# Update this dict ONLY when an adoption decision is made and recorded.
ADOPTED_PARAMS: Dict[str, Dict[str, Dict[str, float]]] = {
    "mean_reversion": {
        "vol_low:trend": {
            "rsi_oversold": 32.7504,
            "rsi_overbought": 62.3545,
            "bb_std_dev": 2.7145,
            "atr_stop_multiplier": 2.3388,
            "min_confidence": 0.5228,
        },
        "vol_mid:trend": {
            "rsi_oversold": 32.7239,
            "rsi_overbought": 63.8175,
            "bb_std_dev": 2.4479,
            "atr_stop_multiplier": 1.8305,
            "min_confidence": 0.5375,
        },
    },
}

DEFAULT_LOG_DIR = r"G:\Candle Data\Temp Test holding"

#: Rolling history handed to each strategy per timeframe in the tuning
#: subprocess (run_composite_tuning's declared requirement).
RETUNE_HISTORY_LOOKBACK = "100"

#: Per-state verdicts from the pre-registered adoption rule. These name
#: what the MEASUREMENT says; the adoption decision stays human.
VERDICT_NOT_MEASURED = "not_measured"
VERDICT_NO_CHANGE = "no_change"
VERDICT_REPLACE = "replace_adopted"
VERDICT_ADOPT_FIRST = "adopt_first_time"
VERDICT_UNMEASURED_VS_ADOPTED = "unmeasured_vs_adopted"

#: Plain-English diagnosis attached whenever the tuning subprocess comes
#: back with nothing measured. This is the failure mode that actually
#: bit the first live run (2026-08-01).
REGIME_ENV_HINT = (
    "the regime env did not take. Without REGIME_MODE=volatility the "
    "backtest tags trades with ADX labels ('ranging_calm', "
    "'trending_strong') while the composite-state filter matches "
    "'vol_low'/'vol_mid'/'vol_high' exactly, so every state returns "
    "insufficient_data and the whole trial budget measures nothing"
)


def _retune_env(strategy: str) -> Dict[str, str]:
    """Build the environment the tuning subprocess requires.

    ``run_composite_tuning`` declares this env in its module docstring
    and does not set it itself; none of these keys are in .env, so the
    driver has to supply them or the run silently measures nothing.

    Only keys ABSENT from .env belong here. config.py calls
    ``load_dotenv(override=True)``, so anything .env defines (LOG_LEVEL,
    DIRECTIONAL_GATE) would be clobbered at import time - those two are
    passed as CLI flags instead and applied after that import.

    Args:
        strategy: Strategy identifier in either snake_case or display
            form (e.g. "mean_reversion").

    Returns:
        Environment overrides, to be merged over ``os.environ``.

    Raises:
        ValueError: The strategy has no known display name, which would
            make every composite state unreachable.
    """
    from ..regime_param_overlay import resolve_strategy_display_name

    display = resolve_strategy_display_name(strategy)
    if display is None:
        raise ValueError(
            f"Unknown re-tune strategy {strategy!r}: cannot build the "
            f"REGIME_VOL_STRATEGIES_* admission lists"
        )
    overrides = {
        "REGIME_MODE": "volatility",
        "BACKTEST_HISTORY_LOOKBACK": RETUNE_HISTORY_LOOKBACK,
    }
    # Admit the tuned strategy in all three terciles so all six
    # composite states (tercile x trend/neutral) are reachable.
    for suffix in ("LOW", "MID", "HIGH"):
        overrides[f"REGIME_VOL_STRATEGIES_{suffix}"] = display
        overrides[f"REGIME_VOL_WEIGHTS_{suffix}"] = f"{display}:1.0"
    return overrides


def _quiet_logging() -> None:
    """Cap both logging stacks at WARNING (drivers always do this:
    INFO wrote ~750 MB/hour over a multi-hour arm)."""
    logging.getLogger().setLevel(logging.WARNING)
    try:
        from loguru import logger as _loguru

        _loguru.remove()
        _loguru.add(sys.stderr, level="WARNING")
    except Exception:
        pass


def _month_window(month: Optional[str]) -> Tuple[str, str, str]:
    """Resolve the report month to (start_iso, end_iso_exclusive, label).

    Default is the previous calendar month relative to today.
    """
    if month:
        year, mon = (int(x) for x in month.split("-"))
    else:
        today = date.today()
        year, mon = (today.year, today.month - 1) if today.month > 1 else (
            today.year - 1, 12)
    start = date(year, mon, 1)
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    return start.isoformat(), end.isoformat(), f"{year:04d}-{mon:02d}"


def _shift_months(iso: str, months: int) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    total = (y * 12 + (m - 1)) + months
    y2, m2 = divmod(total, 12)
    return date(y2, m2 + 1, min(d, 28)).isoformat()


def _resolve_log_dir(requested: str) -> Path:
    p = Path(requested)
    try:
        p.mkdir(parents=True, exist_ok=True)
        return p
    except OSError:
        fallback = Path("out/monthly/logs")
        fallback.mkdir(parents=True, exist_ok=True)
        print(f"[monthly] log dir {requested} unavailable, "
              f"using {fallback}")
        return fallback


def _run_step(
    cmd: List[str],
    log_path: Path,
    timeout: int,
    env_overrides: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Run a subprocess with stdout+stderr appended to log_path.

    Args:
        cmd: Argument vector.
        log_path: Log sink, appended to.
        timeout: Seconds before the step is abandoned (returncode -1).
        env_overrides: Extra environment merged over ``os.environ`` for
            the child. None (the default) inherits the environment
            unchanged, which is what the refresh step wants.

    Returns:
        Step record (cmd, returncode, seconds, log, ok), plus the exact
        env_overrides used when any - so the run is reproducible from
        the report alone.
    """
    started = time.time()
    env = dict(os.environ, **env_overrides) if env_overrides else None
    with open(log_path, "a", encoding="utf-8") as sink:
        sink.write(f"\n===== {' '.join(cmd)} =====\n")
        if env_overrides:
            sink.write(f"===== env: {json.dumps(env_overrides, sort_keys=True)}"
                       f" =====\n")
        sink.flush()
        try:
            proc = subprocess.run(
                cmd, stdout=sink, stderr=subprocess.STDOUT, timeout=timeout,
                env=env,
            )
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = -1
    record: Dict[str, Any] = {
        "cmd": " ".join(cmd),
        "returncode": code,
        "seconds": round(time.time() - started, 1),
        "log": str(log_path),
        "ok": code == 0,
    }
    if env_overrides:
        record["env_overrides"] = dict(env_overrides)
    return record


def step_refresh(symbols: str, log_dir: Path, label: str) -> Dict[str, Any]:
    log_path = log_dir / f"monthly-refresh-{label}.log"
    return _run_step(
        [sys.executable, "-m", "trading_bot_v2.data_manager",
         "--symbols", symbols, "--timeframes", "1m,5m,15m,1h,4h",
         "--update", "--funding"],
        log_path, timeout=3 * 3600,
    )


def step_scorecard(
    symbols: List[str], start: str, end: str
) -> List[Dict[str, Any]]:
    """Backtest each shipped strategy over [start, end) per symbol."""
    from trading_bot_v2.backtesting.optimization_adapter import (
        OptimizationAdapter,
    )

    adapter = OptimizationAdapter()
    rows: List[Dict[str, Any]] = []
    for strategy in SCORECARD_STRATEGIES:
        for symbol in symbols:
            row: Dict[str, Any] = {
                "strategy": strategy, "symbol": symbol,
                "start": start, "end": end,
            }
            try:
                res = adapter.run_backtest(strategy, {}, start, end, symbol)
                row.update({
                    "closed_trades": res.closed_trades,
                    "profit_factor": round(res.profit_factor, 3),
                    "net_pnl": round(res.final_equity - res.initial_capital, 2),
                    "return_pct": round(res.total_return_pct, 2),
                    "win_rate_pct": round(res.win_rate_pct, 1),
                    "max_drawdown_pct": round(res.max_drawdown_pct, 2),
                    "fees": round(res.total_fees, 2),
                })
            except Exception as exc:  # keep the scorecard complete
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
            print(f"[scorecard] {strategy:20s} {symbol:9s} "
                  + (f"trades={row.get('closed_trades')} "
                     f"PF={row.get('profit_factor')} "
                     f"net={row.get('net_pnl')}"
                     if "error" not in row else f"ERROR {row['error']}"))
    return rows


def _write_baseline_file(baseline: Dict[str, Dict[str, float]]) -> Path:
    """Dump the adopted params to a temp JSON for --baseline-params."""
    fd, name = tempfile.mkstemp(prefix="adopted-", suffix=".json", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(baseline, fh, indent=2, sort_keys=True)
    return Path(name)


def step_retune(
    end: str, tune_months: int, trials: int, report_path: Path,
    log_dir: Path, label: str,
    baseline: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """Run the walk-forward re-tune with the adopted params as a 3rd arm.

    The currently adopted per-state params are handed to the tuning
    subprocess so they get scored on the SAME unseen test windows as
    the fold winners and the shipped defaults. Without that arm, leg 2
    of the adoption rule ("fresh tuned must also beat the adopted set")
    cannot be evaluated and the monthly verdict is "no change" by
    construction.

    Args:
        end: Exclusive end of the rolling tuning window (ISO date).
        tune_months: Rolling window length in months.
        trials: Optuna trials per fold.
        report_path: Where the subprocess writes its JSON report.
        log_dir: Bulk-log sink.
        label: Report month label, used in the log filename.
        baseline: Per-state baseline params. None (the default) means
            the module's ADOPTED_PARAMS for the re-tune strategy; an
            empty dict skips the arm entirely.

    Returns:
        Step record, with the window and the exact baseline params
        recorded inline (the temp file is deleted afterwards).
    """
    start = _shift_months(end, -tune_months)
    log_path = log_dir / f"monthly-retune-{label}.log"
    env_overrides = _retune_env(RETUNE_STRATEGY)
    if baseline is None:
        baseline = ADOPTED_PARAMS.get(RETUNE_STRATEGY, {})
    print(f"[retune] env: "
          f"{', '.join(f'{k}={v}' for k, v in sorted(env_overrides.items()))}")
    print(f"[retune] adopted-baseline arm: "
          f"{sorted(baseline) if baseline else 'none (no adopted params)'}")
    cmd = [
        sys.executable, "-m", "trading_bot_v2.optimization.run_composite_tuning",
        "--strategy", RETUNE_STRATEGY, "--symbol", RETUNE_SYMBOL,
        "--start", start, "--end", end,
        "--trials", str(trials),
        "--report", str(report_path),
        "--directional-gate", "enforce",
        "--log-level", "WARNING",
    ]
    baseline_path = _write_baseline_file(baseline) if baseline else None
    if baseline_path is not None:
        cmd += ["--baseline-params", str(baseline_path)]
    try:
        result = _run_step(
            cmd, log_path, timeout=8 * 3600, env_overrides=env_overrides,
        )
    finally:
        if baseline_path is not None:
            baseline_path.unlink(missing_ok=True)
    result["window"] = f"{start}..{end}"
    # Inline, so the run stays reproducible once the temp file is gone.
    result["baseline_params"] = {k: dict(v) for k, v in baseline.items()}
    return result


def _fresh_medians(report: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    """Coordinate-wise median of every fold winner, per state.

    This is the vector the adoption rule actually installs. It is NOT
    any of the arms the tuning report scores head-to-head: the fold
    winners were each fitted to their own train window, and their
    coordinate-wise median is a different vector. The tuner's
    prequential median arm (``test_median``) is what scores this
    construction out-of-sample; see docs/MEDIAN-ARM.md.
    """
    acc: Dict[str, Dict[str, List[float]]] = {}
    for fold in report.get("folds", []):
        for state, data in fold.get("states", {}).items():
            params = data.get("params") or {}
            bucket = acc.setdefault(state, {})
            for key, value in params.items():
                bucket.setdefault(key, []).append(value)
    return {
        state: {k: round(statistics.median(v), 4) for k, v in params.items()}
        for state, params in acc.items()
    }


def _measured_states(report: Dict[str, Any]) -> List[str]:
    """States that produced at least one scored out-of-sample fold.

    Args:
        report: Parsed composite-tuning report.

    Returns:
        State names with a non-zero fold count in the summary.
    """
    return [
        state
        for state, summary in (report.get("summary") or {}).items()
        if isinstance(summary, dict) and summary.get("folds")
    ]


def _adopted_oos(summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pull the baseline (adopted-params) arm out of a state summary.

    Args:
        summary: One state's entry from the tuning report's summary.

    Returns:
        The adopted arm's out-of-sample scores, or None when the report
        has no baseline arm for this state (an older report, or a state
        with no adopted params).
    """
    if not isinstance(summary, dict) or summary.get("baseline") is None:
        return None
    return {
        "score": summary.get("baseline"),
        "folds": summary.get("baseline_folds"),
        "tuned_paired": summary.get("baseline_tuned"),
        "edge_vs_adopted": summary.get("edge_vs_baseline"),
    }


def _prequential_oos(summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pull the prequential-median arm out of a state summary.

    The median arm scores the object this driver actually deploys - the
    coordinate-wise median of the fold winners - rebuilt at each fold
    from the folds strictly before it and scored on that fold's unseen
    test window. The fold-winner arm ("tuned") describes vectors nobody
    installs; when the two disagree, the median arm is the one that
    describes the deployment.

    Args:
        summary: One state's entry from the tuning report's summary.

    Returns:
        The median arm's out-of-sample scores, or None for reports
        written before the arm existed (e.g. 2026-07 and earlier).
    """
    if not isinstance(summary, dict) or summary.get("median") is None:
        return None
    return {
        "score": summary.get("median"),
        "folds": summary.get("median_folds"),
        "edge_vs_default": summary.get("edge_median_vs_default"),
        "edge_vs_tuned": summary.get("edge_median_vs_tuned"),
        "edge_vs_adopted": summary.get("edge_median_vs_baseline"),
    }


def _rule_legs(summary: Dict[str, Any], has_adopted: bool) -> Dict[str, Any]:
    """Evaluate both legs of the pre-registered adoption rule.

    Leg 1: the fresh tuned arm beats the shipped defaults OOS.
    Leg 2: the fresh tuned arm also beats the currently adopted params
    OOS. Leg 2 is only answerable when the tuning run carried the
    adopted-params arm (--baseline-params); a None means "not measured",
    which is NOT the same as "did not beat".

    Args:
        summary: One state's entry from the tuning report's summary.
        has_adopted: Whether an adopted set exists for this state.

    Returns:
        beats_default / beats_adopted (True, False or None) plus the
        mechanical verdict.
    """
    if not isinstance(summary, dict) or not summary.get("folds"):
        return {"beats_default": None, "beats_adopted": None,
                "verdict": VERDICT_NOT_MEASURED}
    edge = summary.get("edge")
    edge_adopted = summary.get("edge_vs_baseline")
    beats_default = None if edge is None else edge > 0
    beats_adopted = None if edge_adopted is None else edge_adopted > 0
    if beats_default is None:
        verdict = VERDICT_NOT_MEASURED
    elif not beats_default:
        verdict = VERDICT_NO_CHANGE  # leg 1 fails, leg 2 is moot
    elif not has_adopted:
        verdict = VERDICT_ADOPT_FIRST  # nothing adopted to beat
    elif beats_adopted is None:
        verdict = VERDICT_UNMEASURED_VS_ADOPTED
    elif beats_adopted:
        verdict = VERDICT_REPLACE
    else:
        verdict = VERDICT_NO_CHANGE
    return {"beats_default": beats_default, "beats_adopted": beats_adopted,
            "verdict": verdict}


def compare_retune(report_path: Path) -> Dict[str, Any]:
    """Three-way OOS comparison: fresh tuned vs defaults vs adopted.

    Args:
        report_path: JSON report written by run_composite_tuning.

    Returns:
        State -> comparison entry, or a dict with an "error" key when
        the report is missing or measured nothing at all. The empty case
        is a hard failure, not a clean report full of nulls: it is what a
        missing REGIME_MODE=volatility looks like from the outside.

        Each entry carries the OOS summary, the adopted arm's own OOS
        score, the fresh/adopted parameter medians and their drift, and
        an explicit boolean per adoption-rule leg plus a verdict. Drift
        is context only - a parameter moving is not evidence that the
        move is an improvement, which is exactly the confusion that made
        leg 2 unmeasurable before the baseline arm existed.
    """
    if not report_path.exists():
        return {"error": f"missing report {report_path}"}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    adopted = ADOPTED_PARAMS.get(RETUNE_STRATEGY, {})
    medians = _fresh_medians(report)
    comparison: Dict[str, Any] = {}
    for state, summary in (report.get("summary") or {}).items():
        entry: Dict[str, Any] = {
            "oos": summary,
            "fresh_median_params": medians.get(state),
            "adopted_params": adopted.get(state),
            "adopted_oos": _adopted_oos(summary),
            "prequential_median_oos": _prequential_oos(summary),
        }
        entry.update(_rule_legs(summary, state in adopted))
        if state in adopted and medians.get(state):
            drift = {
                k: round(medians[state][k] - adopted[state][k], 4)
                for k in adopted[state] if k in medians[state]
            }
            entry["median_drift_vs_adopted"] = drift
        comparison[state] = entry
    measured = _measured_states(report)
    if not measured:
        return {
            "error": (
                f"re-tune measured nothing: {len(comparison)} composite "
                f"state(s) reported, none with a single scored fold - "
                f"{REGIME_ENV_HINT}"
            ),
            "states": comparison,
        }
    return comparison


def write_report(
    out_dir: Path, label: str, payload: Dict[str, Any]
) -> Tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"retune-{label}.json"
    json_path.write_text(
        json.dumps(payload, indent=2, default=str), encoding="utf-8"
    )
    md_path = out_dir / f"retune-{label}.md"
    md_path.write_text(_render_markdown(label, payload), encoding="utf-8")
    return json_path, md_path


def _render_markdown(label: str, payload: Dict[str, Any]) -> str:
    lines = [f"# Monthly re-tune report - {label}", ""]
    refresh = payload.get("refresh")
    if refresh:
        status = "OK" if refresh.get("ok") else f"FAILED ({refresh.get('returncode')})"
        lines += [f"Data refresh: {status} in {refresh.get('seconds')}s "
                  f"(log: {refresh.get('log')})", ""]
    lines += ["## Previous-month scorecard (shipped configs)", "",
              "| strategy | symbol | trades | PF | net | ret% | win% | maxDD% |",
              "|---|---|---|---|---|---|---|---|"]
    for row in payload.get("scorecard", []):
        if "error" in row:
            lines.append(f"| {row['strategy']} | {row['symbol']} | "
                         f"ERROR: {row['error']} | | | | | |")
        else:
            lines.append(
                f"| {row['strategy']} | {row['symbol']} | "
                f"{row['closed_trades']} | {row['profit_factor']} | "
                f"{row['net_pnl']} | {row['return_pct']} | "
                f"{row['win_rate_pct']} | {row['max_drawdown_pct']} |")
    lines.append("")
    retune = payload.get("retune")
    if retune:
        status = ("OK" if retune.get("ok")
                  else f"FAILED (rc={retune.get('returncode')})")
        lines += [f"## Re-tune ({RETUNE_STRATEGY}, {retune.get('window')}): "
                  f"{status} in {retune.get('seconds')}s", ""]
    env_overrides = payload.get("env_overrides")
    if env_overrides:
        lines += ["Tuning env overrides (injected by the driver): "
                  + ", ".join(f"`{k}={v}`"
                              for k, v in sorted(env_overrides.items())), ""]
    lines += _render_comparison(payload.get("comparison") or {})
    return "\n".join(lines)


def _cell(value: Any, missing: str = "n/a") -> str:
    """Render one table cell, rounding floats and naming the absent."""
    if value is None:
        return missing
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return str(round(value, 4))
    return str(value)


def _render_comparison(comparison: Dict[str, Any]) -> List[str]:
    """Render the per-state four-arm OOS table, or the failure notice.

    Args:
        comparison: Output of ``compare_retune``.

    Returns:
        Markdown lines. An "error" comparison renders as a loud notice
        and NO table - a table of nulls reads like a valid null result.
    """
    if comparison.get("error"):
        return [f"**RE-TUNE PRODUCED NO MEASUREMENT**: {comparison['error']}",
                "", "No adoption decision is possible from this run.", ""]
    if not comparison:
        return []
    lines = [
        "| state | folds | tuned OOS | default OOS | adopted OOS | "
        "edge vs default | edge vs adopted | beats defaults | "
        "beats adopted | verdict | median OOS | median vs default | "
        "median vs tuned |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    unadopted = [s for s, e in comparison.items() if not e.get("adopted_params")]
    lines += [_comparison_row(state, entry)
              for state, entry in comparison.items()]
    return lines + _comparison_notes(unadopted)


def _comparison_row(state: str, entry: Dict[str, Any]) -> str:
    """Render one state's row of the four-arm out-of-sample table."""
    oos = entry.get("oos") or {}
    arm = entry.get("adopted_oos") or {}
    med = entry.get("prequential_median_oos") or {}
    return (
        f"| {state} | {_cell(oos.get('folds'))} | "
        f"{_cell(oos.get('tuned'))} | {_cell(oos.get('default'))} | "
        f"{_cell(arm.get('score'), 'no adopted baseline')} | "
        f"{_cell(oos.get('edge'))} | "
        f"{_cell(arm.get('edge_vs_adopted'), 'not measured')} | "
        f"{_cell(entry.get('beats_default'), 'not measured')} | "
        f"{_cell(entry.get('beats_adopted'), 'not measured')} | "
        f"{_cell(entry.get('verdict'))} | "
        f"{_cell(med.get('score'), 'not measured')} | "
        f"{_cell(med.get('edge_vs_default'), 'not measured')} | "
        f"{_cell(med.get('edge_vs_tuned'), 'not measured')} |")


def _comparison_notes(unadopted: List[str]) -> List[str]:
    """Render the prose that keeps the table from being misread."""
    lines = ["",
             "Adoption rule (pre-registered): fresh tuned params replace "
             "the adopted set ONLY if they beat BOTH the defaults (leg 1, "
             "'beats defaults') and the currently adopted params (leg 2, "
             "'beats adopted') out-of-sample. All three arms are scored "
             "on the same unseen test windows.", ""]
    lines += ["The last three columns score the object this driver "
              "actually DEPLOYS: the coordinate-wise median of the fold "
              "winners, rebuilt at each fold from the folds strictly "
              "before it (prequential, leak-free) and scored on that "
              "fold's unseen test window. The 'tuned' column is a set of "
              "per-fold winners that nothing installs. Where 'median vs "
              "tuned' is negative the fold-winner edge OVERSTATES what "
              "the deployed vector delivers. The rule above is "
              "pre-registered on the tuned arm and is NOT changed by "
              "these columns; see docs/MEDIAN-ARM.md.", ""]
    if unadopted:
        lines += ["States with NO adopted baseline to compare against: "
                  + ", ".join(unadopted)
                  + ". Leg 2 is vacuous there - a fresh set that beats the "
                    "defaults would be a first adoption, not a replacement.",
                  ""]
    lines += ["`" + VERDICT_UNMEASURED_VS_ADOPTED + "` means the run "
              "carried no adopted-params arm (--baseline-params), so leg 2 "
              "is unanswered - which is NOT the same as the fresh params "
              "losing. Full param medians and drift are in the JSON next "
              "to this file.", ""]
    return lines


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--month", default=None,
                   help="Report month YYYY-MM (default: previous month)")
    p.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    p.add_argument("--trials", type=int, default=25)
    p.add_argument("--tune-months", type=int, default=36,
                   help="Rolling re-tune window length in months")
    p.add_argument("--out-dir", default="out/monthly")
    p.add_argument("--log-dir", default=DEFAULT_LOG_DIR)
    p.add_argument("--skip-refresh", action="store_true")
    p.add_argument("--skip-scorecard", action="store_true")
    p.add_argument("--skip-tune", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    _quiet_logging()
    start, end, label = _month_window(args.month)
    out_dir = Path(args.out_dir)
    log_dir = _resolve_log_dir(args.log_dir)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    print(f"[monthly] report month {label} ({start}..{end})")

    payload: Dict[str, Any] = {
        "month": label, "window": {"start": start, "end": end},
        "generated_by": "trading_bot_v2.optimization.monthly_retune",
    }
    if not args.skip_refresh:
        print("[monthly] step 1/3: data refresh")
        payload["refresh"] = step_refresh(args.symbols, log_dir, label)
        if not payload["refresh"]["ok"]:
            print("[monthly] WARNING: refresh failed, continuing on "
                  "existing data")
    if not args.skip_scorecard:
        print("[monthly] step 2/3: previous-month scorecard")
        payload["scorecard"] = step_scorecard(symbols, start, end)
    if not args.skip_tune:
        print("[monthly] step 3/3: walk-forward re-tune "
              f"({RETUNE_STRATEGY}, {args.tune_months}mo window, "
              f"{args.trials} trials/fold)")
        report_path = out_dir / f"composite_{RETUNE_STRATEGY}_{label}.json"
        out_dir.mkdir(parents=True, exist_ok=True)
        payload["retune"] = step_retune(
            end, args.tune_months, args.trials, report_path, log_dir, label
        )
        payload["env_overrides"] = payload["retune"].get("env_overrides", {})
        payload["comparison"] = compare_retune(report_path)
        error = payload["comparison"].get("error")
        if error:
            # A clean returncode with nothing measured is still a failed
            # step - say so loudly instead of writing a report of nulls.
            payload["retune"]["ok"] = False
            payload["retune"]["error"] = error
            print(f"[monthly] ERROR: {error}")
        else:
            for state, entry in payload["comparison"].items():
                print(f"[retune] {state:>16}: "
                      f"beats_defaults={entry.get('beats_default')} "
                      f"beats_adopted={entry.get('beats_adopted')} "
                      f"-> {entry.get('verdict')}")

    json_path, md_path = write_report(out_dir, label, payload)
    print(f"[monthly] report written: {md_path} / {json_path}")
    failed = [
        k for k in ("refresh", "retune")
        if isinstance(payload.get(k), dict) and payload[k].get("ok") is False
    ]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
