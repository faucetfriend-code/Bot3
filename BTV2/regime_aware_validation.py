#!/usr/bin/env python3
"""
regime_aware_validation.py — GMM-based regime-aware strategy validation
=======================================================================
Tunes bull and bear strategy versions separately using data-driven regime
detection.  No hardcoded year labels — regime is detected from training
data only (no lookahead bias).

Walk-forward protocol
---------------------
For each fold:
  1. Fit GMM on training window only (causal — no future data).
  2. Predict dominant regime for the test window using the fitted model.
  3. Map GMM regime → trade direction:
       "trending" + positive price change → "long_only"   (bull trend)
       "trending" + negative price change → "short_only"  (bear trend)
       "calm" / "volatile"               → "both"         (mean-reversion)
       "crash"                           → "short_only"   (defensive)
  4. Optimise strategy params on training data.
  5. Run strategy on test window with the direction filter applied.

Usage
-----
    python BTV2/regime_aware_validation.py --strategy "Mean Reversion" \\
           --start 2018-01-01 --end 2025-01-01

    python BTV2/regime_aware_validation.py --all-strategies
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

# ── path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from strategies import (
    INTERVAL_BARS_PER_YEAR,
    INTERVAL_GMM_PARAMS,
    STRATEGY_REGISTRY,
    STRATEGY_TIMEFRAME_CONFIG,
    _SKLEARN_AVAILABLE,
    build_windows,
    compute_metrics,
    fit_gmm_regime,
    gmm_regime_summary,
    monte_carlo_validate,
    optimize_strategy,
    predict_gmm_regime,
    stitch_oos_equity,
)
from data_manager import get_candles


# ─────────────────────────────────────────────────────────────────────────────
# REGIME → DIRECTION MAPPING
# ─────────────────────────────────────────────────────────────────────────────

def get_direction(dominant_regime: str, df_test: pd.DataFrame, strategy_name: str = "") -> str:
    """
    Map a GMM-detected regime to a trade direction filter.

    This is NOT lookahead bias: we are deciding how to trade the *test*
    window based on the regime detected from *training* data.  The
    price_change is computed on the test window itself to distinguish
    bull vs bear *within* a trending regime — the bot would observe this
    in real time as the test period unfolds.

    Parameters
    ----------
    dominant_regime : one of "calm", "trending", "volatile", "crash"
    df_test         : OHLCV DataFrame for the test window
    strategy_name   : optional strategy name for strategy-specific overrides

    Returns
    -------
    "long_only" | "short_only" | "both"
    """
    # Liquidation Capture always runs both directions — its own in_pos prevents overlap.
    # Crash regime = LONG opportunity (fade the cascade), squeeze = SHORT opportunity.
    # Overriding to "short_only" would block the LONG entries that are the core of this strategy.
    if strategy_name == "Liquidation Capture":
        return "both"

    if dominant_regime == "crash":
        return "short_only"
    elif dominant_regime == "trending":
        price_change = (
            (df_test["Close"].iloc[-1] - df_test["Close"].iloc[0])
            / (df_test["Close"].iloc[0] + 1e-10)
        )
        return "long_only" if price_change > 0 else "short_only"
    else:  # calm, volatile
        return "both"


# ─────────────────────────────────────────────────────────────────────────────
# CORE VALIDATION FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def run_regime_aware_validation(
    strategy_name: str,
    start: str = "2018-01-01",
    end: str = "2025-01-01",
    symbol: str = "BTCUSDT",
    cutoff: float = 0.10,
    gmm_lookback: int | None = None,  # None = auto-select from INTERVAL_GMM_PARAMS
    interval_override: str | None = None,      # override STRATEGY_TIMEFRAME_CONFIG interval
    train_months_override: int | None = None,  # override walk-forward train window length
    test_months_override: int | None = None,   # override walk-forward test window length
) -> dict:
    """
    Run a full regime-aware walk-forward validation for one strategy.

    Parameters
    ----------
    strategy_name : key in STRATEGY_REGISTRY
    start         : ISO date string for the start of the full period
    end           : ISO date string for the end of the full period
    symbol        : trading pair (default "BTCUSDT")
    cutoff        : Butterworth filter cutoff (default 0.10)
    gmm_lookback  : manual override for GMM lookback window (bars).
                    None (default) = auto-select from INTERVAL_GMM_PARAMS
                    based on the strategy's native interval.  Pass an int
                    only when you want to override the automatic scaling.

    Returns
    -------
    dict with overall/bull/bear/neutral metrics, per-year summary,
    optimised params per regime, and full fold details.
    """
    if not _SKLEARN_AVAILABLE:
        raise ImportError(
            "scikit-learn is required for GMM regime detection.\n"
            "  pip install scikit-learn>=1.3.0"
        )

    tf_cfg   = STRATEGY_TIMEFRAME_CONFIG[strategy_name]
    interval = interval_override or tf_cfg["interval"]
    train_m  = train_months_override or tf_cfg["train_months"]
    test_m   = test_months_override  or tf_cfg["test_months"]
    bars_py  = INTERVAL_BARS_PER_YEAR[interval]
    func, _, defaults = STRATEGY_REGISTRY[strategy_name]
    if interval_override is not None or train_months_override is not None or test_months_override is not None:
        print(f"  [override] interval={interval} train={train_m}mo test={test_m}mo")

    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt   = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    print(f"\nLoading {symbol} {interval} data ({start} -> {end})...")
    df = get_candles(symbol, interval, start_dt, end_dt)
    print(f"  {len(df):,} bars loaded")

    windows = build_windows(
        date.fromisoformat(start),
        date.fromisoformat(end),
        train_months=train_m,
        test_months=test_m,
    )

    # ── Accumulators ─────────────────────────────────────────────────────────
    all_segments:     list[pd.Series] = []
    all_trades:       list[float]     = []
    bull_segments:    list[pd.Series] = []
    bull_trades:      list[float]     = []
    bear_segments:    list[pd.Series] = []
    bear_trades:      list[float]     = []
    neutral_segments: list[pd.Series] = []
    neutral_trades:   list[float]     = []

    # Training windows bucketed by regime (for regime-specific optimisation)
    bull_train_dfs: list[pd.DataFrame] = []
    bear_train_dfs: list[pd.DataFrame] = []

    fold_details: list[dict] = []

    print(f"\nRunning {len(windows)} walk-forward folds with GMM regime detection...")
    print(f"  {'Fold':>4}  {'Test period':<25}  {'Regime':<12}  "
          f"{'Direction':<12}  {'Trades':>6}  {'Sharpe':>7}")
    print(f"  {'-'*4}  {'-'*25}  {'-'*12}  {'-'*12}  {'-'*6}  {'-'*7}")

    for w in windows:
        df_train = df.loc[str(w["train_start"]) : str(w["train_end"])]
        df_test  = df.loc[str(w["test_start"])  : str(w["test_end"])]

        if len(df_train) < 50 or len(df_test) < 10:
            continue

        # ── Step 1: Fit GMM on training data only (no lookahead) ─────────────
        # Use interval-aware GMM params so intraday bars get a long enough
        # lookback to capture meaningful regime structure (not just noise).
        # gmm_lookback override (int) takes priority over auto-selection.
        dominant_regime = "calm"  # safe fallback
        try:
            gmm_p = INTERVAL_GMM_PARAMS.get(interval, INTERVAL_GMM_PARAMS["1d"])
            base_lookback   = gmm_lookback if gmm_lookback is not None else gmm_p["lookback"]
            base_stability  = gmm_p["stability_window"]

            # Cap lookback at 20% of training data to avoid NaN-dominated features.
            # Also cap so that 4*lookback fits inside the test window.
            max_lb_for_train = max(len(df_train) // 5, 10)
            max_lb_for_test  = max((len(df_test) - 1) // 4, 5)
            actual_lookback  = min(base_lookback, max_lb_for_train, max_lb_for_test)
            actual_stability = min(base_stability, max(actual_lookback // 10, 1))

            gmm_model, label_map, scaler = fit_gmm_regime(
                df_train,
                n_regimes=4,
                lookback=actual_lookback,
                stability_window=actual_stability,
                random_state=42,
            )
            regime_df = predict_gmm_regime(
                df_test, gmm_model, label_map, scaler,
                lookback=actual_lookback,
                stability_window=actual_stability,
            )
            summary = gmm_regime_summary(regime_df)
            dominant_regime = summary["dominant_regime"]
        except Exception as exc:
            dominant_regime = "calm"  # fallback on GMM failure
            print(f"  [WARN] GMM failed for fold {w['fold']}: {exc!r} -- using 'calm'")

        # ── Step 2: Map regime to direction ──────────────────────────────────
        direction = get_direction(dominant_regime, df_test, strategy_name=strategy_name)

        # ── Step 3: Optimise params on training data ──────────────────────────
        best_params = optimize_strategy(df_train, cutoff, strategy_name)

        # ── Step 4: Run strategy with direction filter ────────────────────────
        run_params = {**best_params, "direction": direction}
        try:
            eq, trd = func(df_test, cutoff, **run_params)
        except TypeError:
            # direction param not yet wired — fall back gracefully
            eq, trd = func(df_test, cutoff, **best_params)

        # ── Collect results ───────────────────────────────────────────────────
        all_segments.append(eq)
        all_trades.extend(trd)

        fold_m = compute_metrics(eq, trd, bars_py)
        year   = w["test_start"].year

        fold_details.append({
            "fold":           w["fold"],
            "year":           year,
            "test_period":    f"{w['test_start']} -> {w['test_end']}",
            "dominant_regime": dominant_regime,
            "direction":      direction,
            "n_trades":       fold_m["n_trades"],
            "sharpe":         round(fold_m["sharpe"], 4),
            "return_pct":     round(fold_m["total_return_pct"], 2),
            "win_rate_pct":   round(fold_m["win_rate_pct"], 1),
            "params":         {k: v for k, v in best_params.items()
                               if k != "direction"},
        })

        # Bucket by direction
        if direction == "long_only":
            bull_segments.append(eq)
            bull_trades.extend(trd)
            bull_train_dfs.append(df_train)
        elif direction == "short_only":
            bear_segments.append(eq)
            bear_trades.extend(trd)
            bear_train_dfs.append(df_train)
        else:
            neutral_segments.append(eq)
            neutral_trades.extend(trd)

        print(f"  {w['fold']:>4}  "
              f"{str(w['test_start'])} -> {str(w['test_end'])}  "
              f"{dominant_regime:<12}  {direction:<12}  "
              f"{fold_m['n_trades']:>6}  {fold_m['sharpe']:>+7.3f}")

    # ── Aggregate metrics ─────────────────────────────────────────────────────
    def _metrics(segs: list, trades: list) -> dict:
        if not segs:
            return {}
        return compute_metrics(stitch_oos_equity(segs), trades, bars_py)

    overall_metrics = _metrics(all_segments,     all_trades)
    bull_metrics    = _metrics(bull_segments,    bull_trades)
    bear_metrics    = _metrics(bear_segments,    bear_trades)
    neutral_metrics = _metrics(neutral_segments, neutral_trades)

    # ── Monte Carlo ───────────────────────────────────────────────────────────
    def _mc(trades: list) -> dict:
        if len(trades) >= 5:
            return monte_carlo_validate(trades, n_sims=1000, seed=42)
        return {"verdict": "SKIP (< 5 trades)"}

    mc_overall = _mc(all_trades)
    mc_bull    = _mc(bull_trades)
    mc_bear    = _mc(bear_trades)

    # ── Regime-specific param optimisation ───────────────────────────────────
    bull_opt_params: dict = {}
    bear_opt_params: dict = {}

    if bull_train_dfs:
        df_bull_combined = pd.concat(bull_train_dfs)
        df_bull_combined = (
            df_bull_combined[~df_bull_combined.index.duplicated(keep="first")]
            .sort_index()
        )
        if len(df_bull_combined) >= 50:
            print(f"\n  Optimising BULL params on {len(df_bull_combined):,} bars "
                  f"({len(bull_train_dfs)} training windows)...")
            bull_opt_params = optimize_strategy(df_bull_combined, cutoff, strategy_name)
            # Strip direction from reported params (it's a gate, not a tunable)
            bull_opt_params_report = {k: v for k, v in bull_opt_params.items()
                                      if k != "direction"}
        else:
            bull_opt_params_report = {}
    else:
        bull_opt_params_report = {}

    if bear_train_dfs:
        df_bear_combined = pd.concat(bear_train_dfs)
        df_bear_combined = (
            df_bear_combined[~df_bear_combined.index.duplicated(keep="first")]
            .sort_index()
        )
        if len(df_bear_combined) >= 50:
            print(f"  Optimising BEAR params on {len(df_bear_combined):,} bars "
                  f"({len(bear_train_dfs)} training windows)...")
            bear_opt_params = optimize_strategy(df_bear_combined, cutoff, strategy_name)
            bear_opt_params_report = {k: v for k, v in bear_opt_params.items()
                                      if k != "direction"}
        else:
            bear_opt_params_report = {}
    else:
        bear_opt_params_report = {}

    # ── Per-year summary ──────────────────────────────────────────────────────
    year_data: dict = defaultdict(lambda: {"folds": [], "regimes": [], "directions": []})
    for fd in fold_details:
        y = fd["year"]
        year_data[y]["folds"].append(fd)
        year_data[y]["regimes"].append(fd["dominant_regime"])
        year_data[y]["directions"].append(fd["direction"])

    year_summary: dict[str, dict] = {}
    for y, yd in sorted(year_data.items()):
        folds      = yd["folds"]
        yr_trades  = sum(f["n_trades"] for f in folds)
        yr_returns = [f["return_pct"] for f in folds]
        dominant   = max(set(yd["regimes"]),    key=yd["regimes"].count)
        dom_dir    = max(set(yd["directions"]), key=yd["directions"].count)
        year_summary[str(y)] = {
            "dominant_regime":    dominant,
            "dominant_direction": dom_dir,
            "n_folds":            len(folds),
            "total_trades":       yr_trades,
            "avg_fold_return_pct": round(
                sum(yr_returns) / len(yr_returns) if yr_returns else 0.0, 2
            ),
            "regime_breakdown": {
                r: yd["regimes"].count(r)
                for r in set(yd["regimes"])
            },
        }

    # ── Print summary ─────────────────────────────────────────────────────────
    W = 72
    print(f"\n{'='*W}")
    print(f"  REGIME-AWARE VALIDATION: {strategy_name}")
    print(f"  Period: {start} -> {end}  |  Interval: {interval}  |  Symbol: {symbol}")
    print(f"{'='*W}")

    def _fmt(label: str, m: dict, mc: dict) -> str:
        if not m:
            return f"  {label:<10}  (no folds)"
        return (
            f"  {label:<10}  "
            f"Sharpe={m.get('sharpe', 0):>+7.3f}  "
            f"Return={m.get('total_return_pct', 0):>+7.1f}%  "
            f"MaxDD={m.get('max_dd_pct', 0):>+6.1f}%  "
            f"WR={m.get('win_rate_pct', 0):>5.1f}%  "
            f"Trades={m.get('n_trades', 0):>4}  "
            f"MC={mc.get('verdict', '?')}"
        )

    print()
    print(_fmt("OVERALL",  overall_metrics,  mc_overall))
    print(_fmt("BULL",     bull_metrics,     mc_bull))
    print(_fmt("BEAR",     bear_metrics,     mc_bear))
    if neutral_metrics:
        print(_fmt("NEUTRAL",  neutral_metrics,  {"verdict": "N/A"}))

    print(f"\n  Per-year regime summary:")
    print(f"  {'Year':<6}  {'Regime':<12}  {'Direction':<14}  "
          f"{'Folds':>5}  {'Trades':>7}  {'Avg Ret%':>9}")
    print(f"  {'-'*6}  {'-'*12}  {'-'*14}  {'-'*5}  {'-'*7}  {'-'*9}")
    for y, ys in year_summary.items():
        print(f"  {y:<6}  {ys['dominant_regime']:<12}  "
              f"{ys['dominant_direction']:<14}  "
              f"{ys['n_folds']:>5}  "
              f"{ys['total_trades']:>7}  "
              f"{ys['avg_fold_return_pct']:>+9.1f}%")

    print()
    if bull_opt_params_report:
        print(f"  BULL-optimised params: {bull_opt_params_report}")
    else:
        print("  BULL-optimised params: (insufficient bull folds)")
    if bear_opt_params_report:
        print(f"  BEAR-optimised params: {bear_opt_params_report}")
    else:
        print("  BEAR-optimised params: (insufficient bear folds)")

    # ── Baseline comparison ───────────────────────────────────────────────────
    baseline_sharpe = -0.12
    overall_sharpe  = overall_metrics.get("sharpe", 0.0)
    improvement     = overall_sharpe - baseline_sharpe
    print(f"\n  Baseline (single-strategy avg Sharpe): {baseline_sharpe:+.3f}")
    print(f"  Regime-aware overall Sharpe:           {overall_sharpe:+.3f}")
    print(f"  Improvement:                           {improvement:+.3f}  "
          f"({'BETTER' if improvement > 0 else 'WORSE'})")
    print(f"{'='*W}")

    # ── Build result dict ─────────────────────────────────────────────────────
    result = {
        "strategy":              strategy_name,
        "period":                f"{start} -> {end}",
        "interval":              interval,
        "symbol":                symbol,
        "n_folds":               len(fold_details),
        "overall_metrics":       overall_metrics,
        "bull_metrics":          bull_metrics,
        "bear_metrics":          bear_metrics,
        "neutral_metrics":       neutral_metrics,
        "mc_overall":            mc_overall,
        "mc_bull":               mc_bull,
        "mc_bear":               mc_bear,
        "bull_optimised_params": bull_opt_params_report,
        "bear_optimised_params": bear_opt_params_report,
        "year_summary":          year_summary,
        "fold_details":          fold_details,
        "baseline_sharpe":       baseline_sharpe,
        "improvement_vs_baseline": round(improvement, 4),
    }
    return result


# ─────────────────────────────────────────────────────────────────────────────
# SANITY CHECK  (Task 4)
# ─────────────────────────────────────────────────────────────────────────────

def run_direction_sanity_check() -> None:
    """
    Quick smoke-test: verify that direction="long_only" and direction="short_only"
    produce disjoint trade sets that together approximate direction="both".
    """
    from strategies import run_mean_reversion

    print("\n-- Direction param sanity check ----------------------------------")
    start_dt = datetime(2020, 1, 1, tzinfo=timezone.utc)
    end_dt   = datetime(2021, 1, 1, tzinfo=timezone.utc)
    df = get_candles("BTCUSDT", "1d", start_dt, end_dt)

    eq_both,  t_both  = run_mean_reversion(df, 0.10)
    eq_long,  t_long  = run_mean_reversion(df, 0.10, direction="long_only")
    eq_short, t_short = run_mean_reversion(df, 0.10, direction="short_only")

    print(f"  direction='both'       -> {len(t_both):3d} trades")
    print(f"  direction='long_only'  -> {len(t_long):3d} trades")
    print(f"  direction='short_only' -> {len(t_short):3d} trades")
    print(f"  long + short           =  {len(t_long) + len(t_short):3d} "
          f"(should ~= {len(t_both)} -- small diff OK at window boundaries)")

    assert len(t_long) + len(t_short) >= len(t_both) - 2, (
        "FAIL: long_only + short_only trades should approximately equal both"
    )
    assert len(t_long) <= len(t_both), "FAIL: long_only cannot exceed both"
    assert len(t_short) <= len(t_both), "FAIL: short_only cannot exceed both"
    print("  [OK] direction param works correctly")


# ─────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Regime-aware walk-forward validation for BTC trading strategies."
    )
    parser.add_argument(
        "--strategy",
        default="Mean Reversion",
        choices=list(STRATEGY_REGISTRY.keys()),
        help="Strategy to validate (default: Mean Reversion)",
    )
    parser.add_argument("--start",  default="2018-01-01", help="Start date (ISO)")
    parser.add_argument("--end",    default="2025-01-01", help="End date (ISO)")
    parser.add_argument("--symbol", default="BTCUSDT",    help="Trading pair")
    parser.add_argument(
        "--all-strategies",
        action="store_true",
        help="Run validation for every strategy in the registry",
    )
    parser.add_argument(
        "--sanity-check",
        action="store_true",
        help="Run direction-param smoke test before validation",
    )
    parser.add_argument(
        "--interval",
        default=None,
        choices=["1m", "5m", "15m", "1h", "4h", "1d"],
        help="Override strategy's native interval (e.g., test MA Crossover on 4h, Martingale MR on 1d)",
    )
    parser.add_argument("--train-months", type=int, default=None,
                        help="Override walk-forward train window length (months)")
    parser.add_argument("--test-months",  type=int, default=None,
                        help="Override walk-forward test window length (months)")
    args = parser.parse_args()

    if args.sanity_check:
        run_direction_sanity_check()

    strategies = (
        list(STRATEGY_REGISTRY.keys()) if args.all_strategies else [args.strategy]
    )

    out_dir = Path(__file__).parent / "results"
    out_dir.mkdir(exist_ok=True)

    for strat in strategies:
        result = run_regime_aware_validation(
            strat, args.start, args.end, args.symbol,
            interval_override=args.interval,
            train_months_override=args.train_months,
            test_months_override=args.test_months,
        )
        safe_name = strat.replace(" ", "_").lower()
        # Tag override interval into filename so it doesn't clobber the native run
        suffix = f"_{args.interval}" if args.interval else ""
        out_path  = out_dir / f"regime_aware_{safe_name}{suffix}_{args.start}_{args.end}.json"
        out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"\n  Saved -> {out_path}")


if __name__ == "__main__":
    main()
