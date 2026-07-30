"""
Composite-State Walk-Forward Tuning (regime variants, 2026-07-30)
=================================================================

Tunes per-state parameter variants for one strategy, keyed on the
COMPOSITE state (volatility tercile x directional bias) - the two axes
measured as informative - instead of the ADX labels measured as carrying
~no forward information (docs/REGIME-DISCRIMINATION.md).

Design, and why it is cheap enough to run:
- Walk-forward folds: train window -> Optuna study -> pick winners ->
  score winners on the UNSEEN test window; step forward and repeat.
- ONE shared trial pool per fold serves ALL states: every trial's
  backtest produces trades tagged with every composite state (Phase A
  plumbing), so each trial records a per-state score in user_attrs and
  each state selects its own best trial post-hoc. 6 states cost one
  study, not six.
- Out-of-sample honesty: each state's winner AND the shipped defaults
  are both scored on the fold's test window; the deliverable is
  tuned-vs-default out-of-sample, per state, aggregated across folds.

Required environment (the caller exports these; none are in .env):
    REGIME_MODE=volatility
    REGIME_VOL_STRATEGIES_LOW/MID/HIGH   include the tuned strategy so
                                         every state is reachable
    REGIME_VOL_WEIGHTS_LOW/MID/HIGH      1.0
    BACKTEST_HISTORY_LOOKBACK=100
    DIRECTIONAL_GATE=off|enforce         the arm being tuned
    LOG_LEVEL=WARNING

Usage:
    python -m trading_bot_v2.optimization.run_composite_tuning \\
        --strategy mean_reversion --symbol BTC-USDC \\
        --start 2020-07-01 --end 2026-07-01 \\
        --train-months 12 --test-months 6 --trials 25 \\
        --report out/composite_mr_off.json
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from ..backtesting.optimization_adapter import OptimizationAdapter
from .search_spaces import get_search_space

#: The six composite states: three volatility terciles crossed with
#: directional ("trend" = bull|bear, side-symmetric) vs neutral.
DEFAULT_STATES = (
    "vol_low:trend",
    "vol_low:neutral",
    "vol_mid:trend",
    "vol_mid:neutral",
    "vol_high:trend",
    "vol_high:neutral",
)

#: Trials with fewer matching trades than this in a state are not
#: eligible to be that state's winner (same floor as regime studies).
DEFAULT_MIN_STATE_TRADES = 10


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--strategy", required=True)
    p.add_argument("--symbol", default="BTC-USDC")
    p.add_argument("--start", default="2020-07-01")
    p.add_argument("--end", default="2026-07-01")
    p.add_argument("--train-months", type=int, default=12)
    p.add_argument("--test-months", type=int, default=6)
    p.add_argument("--step-months", type=int, default=None,
                   help="Fold step (default: test-months, non-overlapping tests)")
    p.add_argument("--trials", type=int, default=25)
    p.add_argument("--states", default=",".join(DEFAULT_STATES))
    p.add_argument("--min-state-trades", type=int,
                   default=DEFAULT_MIN_STATE_TRADES)
    p.add_argument(
        "--objective", default="sharpe_ratio",
        choices=(
            "sharpe_ratio", "sortino_ratio", "total_return_pct",
            "calmar_ratio", "profit_factor",
        ),
        help="Canonical long-form metric name (default: sharpe_ratio)",
    )
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--report", default=None, help="Write JSON report here")
    p.add_argument(
        "--directional-gate", default=None,
        choices=("off", "log", "enforce"),
        help=(
            "Force DIRECTIONAL_GATE for this run. Set via os.environ "
            "AFTER config import, because load_dotenv(override=True) "
            "clobbers shell exports whenever the key exists in .env - "
            "the only reliable way to run the off/enforce A/B arms."
        ),
    )
    return p.parse_args(argv)


def _add_months(iso: str, months: int) -> str:
    dt = datetime.fromisoformat(iso)
    month = dt.month - 1 + months
    return dt.replace(
        year=dt.year + month // 12, month=month % 12 + 1
    ).strftime("%Y-%m-%d")


def _folds(start: str, end: str, train_m: int, test_m: int, step_m: int):
    """Yield (train_start, train_end, test_start, test_end) folds."""
    cursor = start
    while True:
        train_end = _add_months(cursor, train_m)
        test_end = _add_months(train_end, test_m)
        if test_end > end:
            return
        yield cursor, train_end, train_end, test_end
        cursor = _add_months(cursor, step_m)


def _suggest(trial, space: Dict[str, Any]) -> Dict[str, Any]:
    """Sample one parameter set from a search-space definition."""
    params: Dict[str, Any] = {}
    for name, spec in space.items():
        if isinstance(spec, tuple) and len(spec) == 2:
            low, high = spec
            if isinstance(low, int) and isinstance(high, int):
                params[name] = trial.suggest_int(name, low, high)
            else:
                params[name] = trial.suggest_float(name, float(low), float(high))
        elif isinstance(spec, list):
            params[name] = trial.suggest_categorical(name, spec)
        else:
            raise ValueError(f"Unhandled search-space spec for {name}: {spec!r}")
    return params


def _state_scores(
    adapter: OptimizationAdapter,
    result,
    states: List[str],
    objective: str,
    capital: float,
    start: str,
    end: str,
) -> Dict[str, Dict[str, Any]]:
    """Score one backtest's trades per composite state."""
    out: Dict[str, Dict[str, Any]] = {}
    for state in states:
        trades = adapter.get_regime_trades(result, state)
        score = (
            adapter.calculate_objective_from_trades(
                trades, objective, capital, start, end
            )
            if trades
            else None
        )
        out[state] = {
            "n": len(trades),
            "score": score,
            "pnl": round(sum(t.get("pnl", 0.0) for t in trades), 4),
        }
    return out


def run(argv: Optional[List[str]] = None) -> int:
    """Run the composite walk-forward tuning and print/emit the report."""
    args = _parse_args(argv)
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    # Cap loguru at LOG_LEVEL. loguru's default sink is stderr at DEBUG
    # and this driver replays years of 5m bars per trial - the first
    # full arm wrote 18 GB of DEBUG stderr in 4 hours and filled the
    # volume (2026-07-30). Same scoped fix as validation/runner.py.
    try:
        logger.remove()
        logger.add(
            sys.stderr, level=os.getenv("LOG_LEVEL", "WARNING").upper() or "WARNING"
        )
    except Exception:
        pass

    if args.directional_gate is not None:
        # After config import, so this wins over the .env-loaded value.
        os.environ["DIRECTIONAL_GATE"] = args.directional_gate
        print(f"  DIRECTIONAL_GATE forced to '{args.directional_gate}'")

    states = [s.strip().lower() for s in args.states.split(",") if s.strip()]
    step_m = args.step_months or args.test_months
    space = get_search_space(args.strategy)
    adapter = OptimizationAdapter()

    folds = list(
        _folds(args.start, args.end, args.train_months, args.test_months, step_m)
    )
    if not folds:
        print("No folds fit the requested span.")
        return 1

    print(f"COMPOSITE TUNING: {args.strategy} {args.symbol}")
    print(f"  states : {states}")
    print(f"  folds  : {len(folds)} (train {args.train_months}mo, "
          f"test {args.test_months}mo, step {step_m}mo)")
    print(f"  trials : {args.trials}/fold, objective {args.objective}")

    report: Dict[str, Any] = {
        "strategy": args.strategy,
        "symbol": args.symbol,
        "states": states,
        "trials_per_fold": args.trials,
        "objective": args.objective,
        "folds": [],
    }

    for fold_no, (tr_s, tr_e, te_s, te_e) in enumerate(folds, 1):
        print(f"\nFOLD {fold_no}/{len(folds)}: train {tr_s}..{tr_e} "
              f"-> test {te_s}..{te_e}", flush=True)

        trial_records: List[Dict[str, Any]] = []

        def objective_fn(trial):
            params = _suggest(trial, space)
            result = adapter.run_backtest(
                args.strategy, params, tr_s, tr_e,
                symbol=args.symbol, initial_capital=args.capital,
            )
            scores = _state_scores(
                adapter, result, states, args.objective,
                args.capital, tr_s, tr_e,
            )
            trial_records.append({"params": params, "scores": scores})
            pooled = [
                s["score"] for s in scores.values() if s["score"] is not None
            ]
            return sum(pooled) / len(pooled) if pooled else -1e9

        sampler = optuna.samplers.TPESampler(seed=args.seed + fold_no)
        study = optuna.create_study(direction="maximize", sampler=sampler)
        study.optimize(objective_fn, n_trials=args.trials)

        # Per-state winner selection from the shared trial pool
        fold_out: Dict[str, Any] = {
            "train": [tr_s, tr_e], "test": [te_s, te_e], "states": {},
        }
        default_result = adapter.run_backtest(
            args.strategy, {}, te_s, te_e,
            symbol=args.symbol, initial_capital=args.capital,
        )
        default_scores = _state_scores(
            adapter, default_result, states, args.objective,
            args.capital, te_s, te_e,
        )

        # Cache test backtests: states often elect the same winner params
        test_cache: Dict[str, Any] = {}
        for state in states:
            eligible = [
                r for r in trial_records
                if r["scores"][state]["score"] is not None
                and r["scores"][state]["n"] >= args.min_state_trades
            ]
            if not eligible:
                fold_out["states"][state] = {"verdict": "insufficient_data"}
                print(f"  {state:>16}: insufficient train trades")
                continue
            winner = max(eligible, key=lambda r: r["scores"][state]["score"])
            key = json.dumps(winner["params"], sort_keys=True)
            if key not in test_cache:
                test_cache[key] = adapter.run_backtest(
                    args.strategy, winner["params"], te_s, te_e,
                    symbol=args.symbol, initial_capital=args.capital,
                )
            tuned = _state_scores(
                adapter, test_cache[key], [state], args.objective,
                args.capital, te_s, te_e,
            )[state]
            base = default_scores[state]
            fold_out["states"][state] = {
                "params": winner["params"],
                "train_score": winner["scores"][state]["score"],
                "train_n": winner["scores"][state]["n"],
                "test_tuned": tuned,
                "test_default": base,
            }
            print(
                f"  {state:>16}: train {winner['scores'][state]['score']:+.3f} "
                f"(n={winner['scores'][state]['n']}) | test tuned "
                f"{tuned['score'] if tuned['score'] is not None else float('nan'):+.3f} "
                f"(n={tuned['n']}) vs default "
                f"{base['score'] if base['score'] is not None else float('nan'):+.3f} "
                f"(n={base['n']})", flush=True,
            )

        report["folds"].append(fold_out)

    # Aggregate: mean out-of-sample tuned-vs-default per state
    print(f"\n{'=' * 72}")
    print("OUT-OF-SAMPLE SUMMARY (mean across folds)")
    print(f"{'=' * 72}")
    print(f"  {'state':>16} {'folds':>6} {'tuned':>9} {'default':>9} {'edge':>8}")
    summary: Dict[str, Any] = {}
    for state in states:
        tuned_vals, default_vals = [], []
        for fold in report["folds"]:
            cell = fold["states"].get(state) or {}
            t = (cell.get("test_tuned") or {}).get("score")
            d = (cell.get("test_default") or {}).get("score")
            if t is not None and d is not None:
                tuned_vals.append(t)
                default_vals.append(d)
        if tuned_vals:
            mt = sum(tuned_vals) / len(tuned_vals)
            md = sum(default_vals) / len(default_vals)
            summary[state] = {
                "folds": len(tuned_vals), "tuned": mt, "default": md,
                "edge": mt - md,
            }
            print(f"  {state:>16} {len(tuned_vals):>6} {mt:>+9.3f} "
                  f"{md:>+9.3f} {mt - md:>+8.3f}")
        else:
            summary[state] = {"folds": 0}
            print(f"  {state:>16} {0:>6} {'n/a':>9} {'n/a':>9} {'n/a':>8}")
    report["summary"] = summary

    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nreport -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
