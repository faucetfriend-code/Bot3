#!/usr/bin/env python3
"""
btv2_validate.py — Strategy Validation Suite
=============================================
Runs three validation layers on top of the standard walk-forward backtest:

    Layer 1 — Walk-forward OOS baseline  (existing)
    Layer 2 — Monte Carlo simulation     (trade-order robustness)
    Layer 3 — Robustness / fragility     (parameter-sensitivity score)
    Layer 4 — GMM regime snapshot        (optional, requires scikit-learn)

Usage
-----
    # Full validation (all layers) — VWAP Scalping on 2021-2023 BTC
    python btv2_validate.py --strategy "VWAP Scalping" --start 2021-01-01 --end 2023-12-31

    # Skip GMM (no scikit-learn needed)
    python btv2_validate.py --strategy "Mean Reversion" --no-gmm

    # Save JSON report
    python btv2_validate.py --strategy "VWAP Scalping" --json-out report.json

    # Use 1m exit resolution for VWAP Scalping (recommended)
    python btv2_validate.py --strategy "VWAP Scalping" --exit-res 1m

    # Test a specific entry mode
    python btv2_validate.py --strategy "VWAP Scalping" --entry-mode bull_pullback

Output
------
Prints a human-readable report to stdout and optionally saves JSON.
All layers feed into a single PASS / CAUTION / FAIL verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from strategies import (
    STRATEGY_REGISTRY,
    INTERVAL_BARS_PER_YEAR,
    build_windows,
    compute_metrics,
    monte_carlo_validate,
    compute_robustness_score,
    optimize_strategy,
    stitch_oos_equity,
    _SKLEARN_AVAILABLE,
)

# ── Try to import data_manager (may not exist in all environments) ──────────
try:
    from data_manager import get_candles, TICKER_MAP
    _DATA_MANAGER = True
except ImportError:
    _DATA_MANAGER = False


# ── Timeframe config (mirrors STRATEGY_TIMEFRAME_CONFIG from guide) ─────────
_TF_CFG: dict[str, dict] = {
    "Mean Reversion":      {"interval": "1d",  "train_months": 12, "test_months": 3,
                            "bars_per_year": INTERVAL_BARS_PER_YEAR["1d"]},
    "VWAP Scalping":       {"interval": "5m",  "train_months": 3,  "test_months": 1,
                            "bars_per_year": INTERVAL_BARS_PER_YEAR["5m"]},
    "Momentum Scalping":   {"interval": "15m", "train_months": 3,  "test_months": 1,
                            "bars_per_year": INTERVAL_BARS_PER_YEAR["15m"]},
    "Liquidation Capture": {"interval": "1d",  "train_months": 12, "test_months": 3,
                            "bars_per_year": INTERVAL_BARS_PER_YEAR["1d"]},
    "Grid Trading":        {"interval": "4h",  "train_months": 6,  "test_months": 2,
                            "bars_per_year": INTERVAL_BARS_PER_YEAR["4h"]},
    "MA Crossover":        {"interval": "1d",  "train_months": 12, "test_months": 3,
                            "bars_per_year": INTERVAL_BARS_PER_YEAR["1d"]},
}


def _load_data(symbol: str, interval: str, start: str, end: str) -> "pd.DataFrame":
    """Load candle data — uses data_manager if available, yfinance otherwise."""
    import pandas as pd
    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt   = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

    if _DATA_MANAGER and symbol in (TICKER_MAP or {}):
        return get_candles(symbol, interval, start_dt, end_dt)

    # Fallback: yfinance (daily only; intraday limited to recent history)
    import yfinance as yf
    ticker_sym = symbol if not symbol.endswith("USDT") else symbol[:-4] + "-USD"
    raw = yf.download(ticker_sym, start=start, end=end, interval=interval,
                      auto_adjust=True, progress=False)
    if raw.empty:
        raise ValueError(f"yfinance returned no data for {ticker_sym} {interval}")
    raw.columns = [c[0] if isinstance(c, tuple) else c for c in raw.columns]
    raw.index = pd.to_datetime(raw.index, utc=True)
    return raw


def _run_walk_forward(
    df, cutoff: float, strategy_name: str,
    train_months: int, test_months: int,
    bars_per_year: int, df_exit=None,
    extra_kwargs: dict | None = None,
) -> tuple[object, list[float], list[dict]]:
    """Walk-forward with per-fold optimisation. Returns (oos_equity, all_trades, fold_metrics)."""
    import pandas as pd
    func, _, defaults = STRATEGY_REGISTRY[strategy_name]
    kw    = {**defaults, **(extra_kwargs or {})}
    start = df.index[0].date()
    end   = df.index[-1].date()
    windows = build_windows(start, end, train_months=train_months, test_months=test_months)

    segments: list = []
    all_trades: list[float] = []
    fold_metrics: list[dict] = []

    for w in windows:
        df_train = df.loc[str(w["train_start"]): str(w["train_end"])]
        df_test  = df.loc[str(w["test_start"]):  str(w["test_end"])]
        if len(df_train) < 50 or len(df_test) < 10:
            continue

        best = optimize_strategy(df_train, cutoff, strategy_name)
        run_kw = {**kw, **best}

        df_exit_slice = None
        if df_exit is not None:
            df_exit_slice = df_exit.loc[str(w["test_start"]): str(w["test_end"])]

        try:
            eq, trd = func(df_test, cutoff, **run_kw,
                           **({"df_exit": df_exit_slice} if df_exit_slice is not None else {}))
        except TypeError:
            eq, trd = func(df_test, cutoff, **best)

        segments.append(eq)
        all_trades.extend(trd)
        m = compute_metrics(eq, trd, bars_per_year)
        fold_metrics.append({
            "fold": w["fold"],
            "test_period": f"{w['test_start']} → {w['test_end']}",
            **m,
            "params": best,
        })

    oos_eq = stitch_oos_equity(segments)
    return oos_eq, all_trades, fold_metrics


def _banner(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


def run_validation(
    strategy_name: str,
    symbol: str = "BTCUSDT",
    start: str = "2021-01-01",
    end: str   = "2023-12-31",
    cutoff: float = 0.10,
    exit_res: str = "Off",
    entry_mode: str | None = None,
    n_mc_sims: int = 1_000,
    run_gmm: bool = True,
    verbose: bool = True,
) -> dict:
    """
    Run all validation layers and return a combined results dict.

    Parameters
    ----------
    strategy_name : key in STRATEGY_REGISTRY
    symbol        : e.g. "BTCUSDT"
    start / end   : ISO date strings
    cutoff        : Butterworth cutoff
    exit_res      : "Off", "5m", or "1m" sub-bar exit resolution
    entry_mode    : override entry_mode for VWAP Scalping
    n_mc_sims     : Monte Carlo simulation count
    run_gmm       : attempt GMM regime snapshot (requires scikit-learn)
    verbose       : print progress to stdout
    """
    import pandas as pd

    tf    = _TF_CFG.get(strategy_name, _TF_CFG["Mean Reversion"])
    bpy   = tf["bars_per_year"]
    intv  = tf["interval"]

    # ── [1] Load data ────────────────────────────────────────────────────────
    if verbose:
        print(f"\n[1/4] Loading {symbol} {intv} candles  ({start} → {end})…")
    df = _load_data(symbol, intv, start, end)
    if verbose:
        print(f"      {len(df):,} bars loaded")

    df_exit = None
    if exit_res != "Off":
        if verbose:
            print(f"      Loading {exit_res} exit-resolution data…")
        try:
            df_exit = _load_data(symbol, exit_res, start, end)
            if verbose:
                print(f"      {len(df_exit):,} {exit_res} bars loaded")
        except Exception as e:
            if verbose:
                print(f"      ⚠  Exit-res load failed: {e} — continuing without")

    extra_kw: dict = {}
    if entry_mode:
        extra_kw["entry_mode"] = entry_mode

    # ── [2] Walk-forward OOS baseline ────────────────────────────────────────
    if verbose:
        print(f"\n[2/4] Walk-forward OOS  ({tf['train_months']}m train → {tf['test_months']}m test)…")
    oos_eq, all_trades, fold_metrics = _run_walk_forward(
        df, cutoff, strategy_name,
        tf["train_months"], tf["test_months"], bpy,
        df_exit=df_exit, extra_kwargs=extra_kw,
    )
    oos_m = compute_metrics(oos_eq, all_trades, bpy)

    if verbose:
        print(f"      Sharpe={oos_m['sharpe']:.3f}  "
              f"Return={oos_m['total_return_pct']:+.1f}%  "
              f"MaxDD={oos_m['max_dd_pct']:.1f}%  "
              f"Trades={oos_m['n_trades']}  "
              f"WR={oos_m['win_rate_pct']:.0f}%  "
              f"PF={oos_m['profit_factor']:.2f}")

    # ── [3a] Monte Carlo ──────────────────────────────────────────────────────
    if verbose:
        print(f"\n[3/4] Monte Carlo ({n_mc_sims:,} sims)…")
    mc = monte_carlo_validate(all_trades, n_sims=n_mc_sims)
    if verbose:
        print(f"      Verdict={mc['verdict']}  "
              f"P(loss)={mc['prob_of_loss_pct']:.1f}%  "
              f"p5={mc['p5_return_pct']:+.1f}%  "
              f"p50={mc['p50_return_pct']:+.1f}%  "
              f"p95={mc['p95_return_pct']:+.1f}%  "
              f"Worst5%DD={mc['worst5_maxdd_pct']:.1f}%")

    # ── [3b] Robustness score ─────────────────────────────────────────────────
    if verbose:
        print(f"\n      Robustness score (±15% parameter perturbation on last fold train)…")
    rob_result: dict = {}
    if fold_metrics:
        last_fold = fold_metrics[-1]
        last_train_end   = last_fold["test_period"].split(" → ")[0]
        # Use the second-to-last fold's train window as proxy for a clean IS sample
        df_last_train = df.loc[:last_train_end] if last_train_end else df

        try:
            rob_result = compute_robustness_score(
                df_last_train, cutoff, strategy_name,
                last_fold["params"], perturbation=0.15, bars_per_year=bpy,
            )
            if verbose:
                print(f"      Verdict={rob_result['verdict']}  "
                      f"Score={rob_result['score']:.2f}  "
                      f"BaseIS_Sharpe={rob_result['base_sharpe']:.3f}")
                if rob_result["fragile_params"]:
                    print(f"      Fragile params: {', '.join(rob_result['fragile_params'])}")
                else:
                    print("      All params stable under ±15% perturbation ✓")
        except Exception as e:
            if verbose:
                print(f"      ⚠  Robustness check failed: {e}")

    # ── [4] GMM regime snapshot ────────────────────────────────────────────
    gmm_summary: dict = {}
    if run_gmm and _SKLEARN_AVAILABLE:
        if verbose:
            print(f"\n[4/4] GMM regime detection snapshot…")
        try:
            from strategies import fit_gmm_regime, predict_gmm_regime, gmm_regime_summary
            lookback = min(60, len(df) // 10)
            gmm_model, label_map, scaler = fit_gmm_regime(df, lookback=lookback)
            regime_df  = predict_gmm_regime(df, gmm_model, label_map, scaler,
                                             lookback=lookback)
            gmm_summary = gmm_regime_summary(regime_df)
            if verbose:
                print(f"      Current regime: {gmm_summary['current_regime']} "
                      f"(confidence {gmm_summary['current_confidence']:.0%})")
                dist = "  ".join(f"{k}={v:.0f}%" for k, v in gmm_summary["regime_pct"].items())
                print(f"      Distribution: {dist}")
        except Exception as e:
            if verbose:
                print(f"      ⚠  GMM failed: {e}")
    elif run_gmm and not _SKLEARN_AVAILABLE:
        if verbose:
            print("\n[4/4] GMM skipped — scikit-learn not installed.")
            print("       pip install scikit-learn>=1.3.0")

    # ── Final verdict ────────────────────────────────────────────────────────
    _banner("VALIDATION VERDICT")
    verdicts = []
    if oos_m["n_trades"] >= 10 and oos_m["sharpe"] > 0.8:
        verdicts.append("OOS: PASS")
    elif oos_m["n_trades"] >= 5 and oos_m["sharpe"] > 0.0:
        verdicts.append("OOS: CAUTION")
    else:
        verdicts.append("OOS: FAIL")

    if mc.get("verdict") == "ROBUST":
        verdicts.append("MC: PASS")
    elif mc.get("verdict") == "MARGINAL":
        verdicts.append("MC: CAUTION")
    else:
        verdicts.append("MC: FAIL")

    if rob_result:
        if rob_result.get("verdict") == "ROBUST":
            verdicts.append("ROB: PASS")
        elif rob_result.get("verdict") == "MARGINAL":
            verdicts.append("ROB: CAUTION")
        else:
            verdicts.append("ROB: FAIL")

    fails    = sum(1 for v in verdicts if "FAIL"    in v)
    cautions = sum(1 for v in verdicts if "CAUTION" in v)
    overall  = "✅ PASS" if fails == 0 and cautions <= 1 else \
               "⚠  CAUTION" if fails == 0 else "❌ FAIL"

    print(f"  {overall}")
    for v in verdicts:
        marker = "✓" if "PASS" in v else ("⚠" if "CAUTION" in v else "✗")
        print(f"    {marker} {v}")

    if oos_m["n_trades"] < 10:
        print(f"\n  ⚠  Only {oos_m['n_trades']} trades — below statistical floor (≥10).")
        print("     Widen entry conditions or test over a longer date range.")
    if rob_result.get("fragile_params"):
        print(f"\n  ⚠  Fragile params: {', '.join(rob_result['fragile_params'])}")
        print("     These parameters are over-tuned. Consider removing from grid or widening.")

    # ── Build output dict ────────────────────────────────────────────────────
    return {
        "strategy":      strategy_name,
        "symbol":        symbol,
        "period":        f"{start} → {end}",
        "interval":      intv,
        "bars_per_year": bpy,
        "cutoff":        cutoff,
        "exit_res":      exit_res,
        "entry_mode":    entry_mode,
        "oos_metrics":   oos_m,
        "fold_metrics":  fold_metrics,
        "monte_carlo":   mc,
        "robustness":    rob_result,
        "gmm_summary":   gmm_summary,
        "verdicts":      verdicts,
        "overall":       overall.replace("✅ ", "").replace("⚠  ", "").replace("❌ ", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="BTV2 Strategy Validation Suite — 3-layer robustness check",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--strategy",    default="VWAP Scalping",
                        choices=list(STRATEGY_REGISTRY.keys()))
    parser.add_argument("--symbol",      default="BTCUSDT")
    parser.add_argument("--start",       default="2021-01-01")
    parser.add_argument("--end",         default=str(date.today()))
    parser.add_argument("--cutoff",      type=float, default=0.10)
    parser.add_argument("--exit-res",    default="Off", choices=["Off", "5m", "1m"],
                        help="Sub-bar exit resolution (VWAP Scalping: use 1m)")
    parser.add_argument("--entry-mode",  default=None,
                        choices=["mean_reversion", "cross", "bull_pullback",
                                 "bear_pullback", "deviation", "momentum"],
                        help="Override VWAP Scalping entry_mode")
    parser.add_argument("--mc-sims",     type=int, default=1_000,
                        help="Monte Carlo simulation count (default 1000)")
    parser.add_argument("--no-gmm",      action="store_true",
                        help="Skip GMM regime snapshot")
    parser.add_argument("--json-out",    default=None,
                        help="Write results to this JSON file")
    parser.add_argument("--quiet",       action="store_true",
                        help="Suppress per-layer progress output")
    args = parser.parse_args()

    result = run_validation(
        strategy_name = args.strategy,
        symbol        = args.symbol,
        start         = args.start,
        end           = args.end,
        cutoff        = args.cutoff,
        exit_res      = args.exit_res,
        entry_mode    = args.entry_mode,
        n_mc_sims     = args.mc_sims,
        run_gmm       = not args.no_gmm,
        verbose       = not args.quiet,
    )

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, indent=2, default=str))
        print(f"\n  Report saved → {args.json_out}")
    else:
        print("\n--- JSON RESULT ---")
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
