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
                params.

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
import statistics
import subprocess
import sys
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


def _run_step(cmd: List[str], log_path: Path, timeout: int) -> Dict[str, Any]:
    """Run a subprocess with stdout+stderr appended to log_path."""
    started = time.time()
    with open(log_path, "a", encoding="utf-8") as sink:
        sink.write(f"\n===== {' '.join(cmd)} =====\n")
        sink.flush()
        try:
            proc = subprocess.run(
                cmd, stdout=sink, stderr=subprocess.STDOUT, timeout=timeout
            )
            code = proc.returncode
        except subprocess.TimeoutExpired:
            code = -1
    return {
        "cmd": " ".join(cmd),
        "returncode": code,
        "seconds": round(time.time() - started, 1),
        "log": str(log_path),
        "ok": code == 0,
    }


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


def step_retune(
    end: str, tune_months: int, trials: int, report_path: Path,
    log_dir: Path, label: str,
) -> Dict[str, Any]:
    start = _shift_months(end, -tune_months)
    log_path = log_dir / f"monthly-retune-{label}.log"
    result = _run_step(
        [sys.executable, "-m", "trading_bot_v2.optimization.run_composite_tuning",
         "--strategy", RETUNE_STRATEGY, "--symbol", RETUNE_SYMBOL,
         "--start", start, "--end", end,
         "--trials", str(trials),
         "--report", str(report_path),
         "--directional-gate", "enforce",
         "--log-level", "WARNING"],
        log_path, timeout=8 * 3600,
    )
    result["window"] = f"{start}..{end}"
    return result


def _fresh_medians(report: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
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


def compare_retune(report_path: Path) -> Dict[str, Any]:
    """Fresh medians + OOS summary vs the currently adopted params."""
    if not report_path.exists():
        return {"error": f"missing report {report_path}"}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    adopted = ADOPTED_PARAMS.get(RETUNE_STRATEGY, {})
    medians = _fresh_medians(report)
    comparison: Dict[str, Any] = {}
    for state, summary in report.get("summary", {}).items():
        entry: Dict[str, Any] = {
            "oos": summary,
            "fresh_median_params": medians.get(state),
            "adopted_params": adopted.get(state),
        }
        if state in adopted and medians.get(state):
            drift = {
                k: round(medians[state][k] - adopted[state][k], 4)
                for k in adopted[state] if k in medians[state]
            }
            entry["median_drift_vs_adopted"] = drift
        comparison[state] = entry
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
        status = "OK" if retune.get("ok") else f"FAILED ({retune.get('returncode')})"
        lines += [f"## Re-tune ({RETUNE_STRATEGY}, {retune.get('window')}): "
                  f"{status} in {retune.get('seconds')}s", ""]
    comparison = payload.get("comparison") or {}
    if comparison and "error" not in comparison:
        lines += ["| state | folds | tuned OOS | default OOS | edge | "
                  "adopted? |", "|---|---|---|---|---|---|"]
        for state, entry in comparison.items():
            oos = entry.get("oos", {})
            lines.append(
                f"| {state} | {oos.get('folds')} | {oos.get('tuned')} | "
                f"{oos.get('default')} | {oos.get('edge')} | "
                f"{'yes' if entry.get('adopted_params') else 'no'} |")
        lines += ["",
                  "Adoption rule (pre-registered): fresh tuned params replace "
                  "the adopted set ONLY if they beat BOTH the defaults and "
                  "the currently adopted params out-of-sample. Full param "
                  "medians and drift are in the JSON next to this file.", ""]
    return "\n".join(lines)


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
        payload["comparison"] = compare_retune(report_path)

    json_path, md_path = write_report(out_dir, label, payload)
    print(f"[monthly] report written: {md_path} / {json_path}")
    failed = [
        k for k in ("refresh", "retune")
        if isinstance(payload.get(k), dict) and payload[k].get("ok") is False
    ]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
