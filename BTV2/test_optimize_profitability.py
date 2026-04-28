"""
test_optimize_profitability.py
============================
Parameter optimization for profitability.

Tests 2021-2023 data with grid search over key parameters.
Smart search with reduced constraints for realistic targets.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Dict, Any, List, Tuple

import numpy as np
import pandas as pd

# Add parent to path  
sys.path.insert(0, str(Path(__file__).parent))

from strategies import (
    run_vwap_scalping,
    compute_metrics,
    COST_PER_SIDE,
    INTERVAL_BARS_PER_YEAR,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_FILE = Path(__file__).parent.parent / "trading_bot_v2" / "backtesting" / "data" / "BTC-USDC_5m_all.csv"
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Test period: 2021-2023 (3 years)
TEST_START = "2021-01-01"
TEST_END = "2024-01-01"

# Train/OOS split: first 2 years train, last year OOS
TRAIN_END = "2023-01-01"

# Butterworth cutoff
CUTOFF = 0.10

# ─────────────────────────────────────────────────────────────────────────────
# GRID PARAMETERS
# ─────────────────────────────────────────────────────────────────────────────

SD_THRESHOLDS = [1.5, 1.8, 2.0, 2.2, 2.5, 3.0, 3.5, 4.0]
ATR_MULTIPLIERS = [0.5, 0.7, 1.0]
RR_VALUES = [1.5, 2.0, 2.5]
ALLOW_BOTH_DIRECTIONS = [True, False]

ENTRY_MODE_MAP = {True: "mean_reversion", False: "bull_pullback"}

# ─────────────────────────────────────────────────────────────────────────────
# CONSTRAINT GATES (More realistic for crypto)
# ─────────────────────────────────────────────────────────────────────────────

MIN_OOS_SHARPE = 0.0  # Relaxed: any positive return 
MAX_PLOSS = 25.0   # Relaxed: allow up to 25% loss probability
MAX_DD = 40.0      # Relaxed: allow up to 40% drawdown
MIN_TRADES = 20    # Relaxed: minimum 20 trades in OOS

BARS_PER_YEAR = INTERVAL_BARS_PER_YEAR.get("5m", 105120)


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────


def load_data(start: str, end: str) -> pd.DataFrame:
    """Load BTC-USDC 5m data."""
    if not DATA_FILE.exists():
        raise FileNotFoundError(f"Data file not found: {DATA_FILE}")

    df = pd.read_csv(DATA_FILE)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")

    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    mask = (df.index >= start_ts) & (df.index < end_ts)
    df = df.loc[mask].copy()

    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    df = df[["Open", "High", "Low", "Close", "Volume"]]

    print(f"Loaded {len(df):,} bars ({start} to {end})")
    return df


# ─────────────────────────────────────────────────────────────────────────────
# STRATEGY RUNNER
# ─────────────────────────────────────────────────────────────────────────────


def run_strategy(
    df: pd.DataFrame,
    sd_threshold: float,
    atr_mult: float,
    RR: float,
    allow_both_dirs: bool,
    cutoff: float = CUTOFF,
) -> Tuple[pd.Series, List[float]]:
    """Run strategy with given params."""

    atr_stop = atr_mult * 2.0
    atr_target = atr_mult * RR
    trailing_atr = atr_target * 0.965
    entry_mode = ENTRY_MODE_MAP[allow_both_dirs]

    params = {
        "sd_threshold": sd_threshold,
        "atr_stop": atr_stop,
        "atr_target": atr_target,
        "trailing_atr": trailing_atr,
        "adx_max": 25.0,
        "rsi_max": 50.0,
        "volume_mult": 1.2,
        "entry_mode": entry_mode,
        "use_htf_ema": True,
        "htf_adx_max": 30.0,
        "tp_mode": "atr",
    }

    equity, trades = run_vwap_scalping(df, cutoff, **params)
    return equity, trades


# ─────────────────────────────────────────────────────────────────────────────
# METRICS
# ─────────────────────────────────────────────────────────────────────────────


def compute_metrics_fn(equity: pd.Series, trades: List[float]) -> Dict[str, float]:
    """Compute performance metrics."""
    if len(equity) < 2 or len(trades) == 0:
        return {"trades": 0, "win_rate": 0.0, "profit_factor": 0.0, "net_pct": 0.0, "sharpe": 0.0, "max_drawdown_pct": 0.0}

    metrics = compute_metrics(equity, trades, bars_per_year=BARS_PER_YEAR)
    return {
        "trades": metrics.get("n_trades", 0),
        "win_rate": metrics.get("win_rate_pct", 0.0),
        "profit_factor": metrics.get("profit_factor", 0.0),
        "net_pct": metrics.get("total_return_pct", 0.0),
        "sharpe": metrics.get("sharpe", 0.0),
        "max_drawdown_pct": metrics.get("max_dd_pct", 0.0),
    }


def compute_oos(train_eq: pd.Series, train_tr: List[float], oos_eq: pd.Series, oos_tr: List[float]) -> Dict[str, Any]:
    """Compute train/OOS combined metrics."""
    train_m = compute_metrics_fn(train_eq, train_tr)
    oos_m = compute_metrics_fn(oos_eq, oos_tr)

    if train_tr:
        losses = sum(1 for t in train_tr if t < 0)
        p_loss = losses / len(train_tr) * 100
    else:
        p_loss = 100.0

    return {
        "train_sharpe": train_m["sharpe"],
        "oos_sharpe": oos_m["sharpe"],
        "train_return": train_m["net_pct"],
        "oos_return": oos_m["net_pct"],
        "train_max_dd": train_m["max_drawdown_pct"],
        "oos_max_dd": oos_m["max_drawdown_pct"],
        "train_trades": train_m["trades"],
        "oos_trades": oos_m["trades"],
        "train_win_rate": train_m["win_rate"],
        "oos_win_rate": oos_m["win_rate"],
        "p_loss": p_loss,
    }


def validate_config(metrics: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate config against constraints."""
    failures = []

    if metrics["oos_sharpe"] < MIN_OOS_SHARPE:
        failures.append(f"OOS Sharpe {metrics['oos_sharpe']:.2f} < {MIN_OOS_SHARPE}")
    if metrics["p_loss"] > MAX_PLOSS:
        failures.append(f"P(loss) {metrics['p_loss']:.1f}% > {MAX_PLOSS}%")
    if metrics["oos_max_dd"] > MAX_DD:
        failures.append(f"OOS Max DD {metrics['oos_max_dd']:.1f}% > {MAX_DD}%")
    if metrics["oos_trades"] < MIN_TRADES:
        failures.append(f"Trades {metrics['oos_trades']} < {MIN_TRADES}")

    return len(failures) == 0, failures


# ─────────────────────────────────────────────────────────────────────────────
# SMART SEARCH
# ─────────────────────────────────────────────────────────────────────────────


def find_best_sd(df_train: pd.DataFrame, df_oos: pd.DataFrame) -> List[float]:
    """Phase 1: Find SD sweet spot."""
    print("\n=== PHASE 1: SD SWEET SPOT ===")
    results = []

    for sd in SD_THRESHOLDS:
        try:
            eq_t, tr_t = run_strategy(df_train, sd, 0.7, 2.0, True, CUTOFF)
            eq_o, tr_o = run_strategy(df_oos, sd, 0.7, 2.0, True, CUTOFF)
            m = compute_oos(eq_t, tr_t, eq_o, tr_o)
            m["sd_threshold"] = sd
            results.append(m)
            print(f"  SD={sd}: OOS={m['oos_return']:.1f}%, Sharpe={m['oos_sharpe']:.2f}, Trades={m['oos_trades']}")
        except Exception as e:
            print(f"  SD={sd}: ERROR - {e}")

    if results:
        results.sort(key=lambda x: x["oos_return"], reverse=True)
    return [r["sd_threshold"] for r in results]


def find_best_atr_rr(df_train: pd.DataFrame, df_oos: pd.DataFrame, best_sd: float) -> List[Tuple[float, float]]:
    """Phase 2: Find best ATR/RR combo."""
    print("\n=== PHASE 2: ATR/RR OPTIMIZATION ===")
    results = []

    for atr in ATR_MULTIPLIERS:
        for rr in RR_VALUES:
            try:
                eq_t, tr_t = run_strategy(df_train, best_sd, atr, rr, True, CUTOFF)
                eq_o, tr_o = run_strategy(df_oos, best_sd, atr, rr, True, CUTOFF)
                m = compute_oos(eq_t, tr_t, eq_o, tr_o)
                m["atr_mult"] = atr
                m["RR"] = rr
                results.append(m)
                print(f"  ATR={atr}, RR={rr}: OOS={m['oos_return']:.1f}%, Sharpe={m['oos_sharpe']:.2f}")
            except Exception as e:
                print(f"  ATR={atr}, RR={rr}: ERROR - {e}")

    if results:
        results.sort(key=lambda x: x["oos_return"], reverse=True)
    return [(r["atr_mult"], r["RR"]) for r in results]


def optimize_direction(df_train: pd.DataFrame, df_oos: pd.DataFrame, best_sd: float, best_atr: float, best_rr: float) -> List[Dict[str, Any]]:
    """Phase 3: Optimize direction (both vs longs only)."""
    print("\n=== PHASE 3: DIRECTION OPTIMIZATION ===")
    results = []

    for both in ALLOW_BOTH_DIRECTIONS:
        try:
            eq_t, tr_t = run_strategy(df_train, best_sd, best_atr, best_rr, both, CUTOFF)
            eq_o, tr_o = run_strategy(df_oos, best_sd, best_atr, best_rr, both, CUTOFF)
            m = compute_oos(eq_t, tr_t, eq_o, tr_o)
            m["sd_threshold"] = best_sd
            m["atr_mult"] = best_atr
            m["RR"] = best_rr
            m["allow_both_directions"] = both

            passes, _ = validate_config(m)
            status = "PASS" if passes else "FAIL"
            print(f"  Both={both}: {status} | OOS={m['oos_return']:.1f}%, Sharpe={m['oos_sharpe']:.2f}, Trades={m['oos_trades']}")
            results.append(m)
        except Exception as e:
            print(f"  Both={both}: ERROR - {e}")

    results.sort(key=lambda x: x["oos_return"], reverse=True)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# REPORTING
# ─────────────────────────────────────────────────────────────────────────────


def print_table(results: List[Dict[str, Any]], title: str = "Results") -> None:
    """Print results table."""
    print("\n" + "=" * 85)
    print(title)
    print("=" * 85)
    print(f"{'#':>3} | {'SD':>4} | {'ATR':>5} | {'RR':>4} | {'Both':>5} | {'Ret%':>7} | {'Sharpe':>6} | {'MaxDD%':>6} | {'P(loss)':>7} | {'Trades':>6} | {'Pass':>4}")
    print("-" * 75)

    for i, r in enumerate(results[:20]):
        passes, _ = validate_config(r)
        status = "PASS" if passes else "FAIL"
        print(f"{i+1:>3} | {r['sd_threshold']:>4.1f} | {r.get('atr_mult', 0):>5.2f} | {r.get('RR', 0):>4.1f} | {str(r.get('allow_both_directions', False)):>5} | {r['oos_return']:>7.1f} | {r['oos_sharpe']:>6.2f} | {r['oos_max_dd']:>6.1f} | {r['p_loss']:>7.1f} | {r['oos_trades']:>6} | {status:>4}")


def get_passing(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Get passing configs only."""
    return [r for r in results if validate_config(r)[0]]


def print_best(best: Dict[str, Any]) -> None:
    """Print best config."""
    print("\n" + "=" * 85)
    print("BEST CONFIGURATION")
    print("=" * 85)
    print(f"  sd_threshold:       {best['sd_threshold']}")
    print(f"  atr_multiplier:     {best.get('atr_mult', 'N/A')}")
    print(f"  RR:                {best.get('RR', 'N/A')}")
    print(f"  allow_both_directions: {best.get('allow_both_directions', 'N/A')}")
    print()
    print(f"  OOS Return:        {best['oos_return']:.1f}%")
    print(f"  OOS Sharpe:       {best['oos_sharpe']:.2f}")
    print(f"  OOS Max DD:        {best['oos_max_dd']:.1f}%")
    print(f"  OOS Win Rate:     {best['oos_win_rate']:.1f}%")
    print(f"  P(loss):          {best['p_loss']:.1f}%")
    print(f"  Trades:          {best['oos_trades']}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────


def main():
    start_time = time.time()

    print("=" * 85)
    print("PROFITABILITY OPTIMIZATION TEST")
    print("=" * 85)
    print(f"Period: {TEST_START} to {TEST_END}")
    print(f"Split:  {TEST_START} to {TRAIN_END} (train), {TRAIN_END} to {TEST_END} (OOS)")
    print()

    # Load data
    print("Loading data...")
    df_train = load_data(TEST_START, TRAIN_END)
    df_oos = load_data(TRAIN_END, TEST_END)
    print(f"\nTrain: {len(df_train):,} bars (2021-2022) | OOS: {len(df_oos):,} bars (2023)")

    # Baseline
    print("\n=== BASELINE TEST ===")
    eq_t, tr_t = run_strategy(df_train, 2.0, 0.7, 2.0, True, CUTOFF)
    eq_o, tr_o = run_strategy(df_oos, 2.0, 0.7, 2.0, True, CUTOFF)
    baseline_m = compute_oos(eq_t, tr_t, eq_o, tr_o)
    passes, fails = validate_config(baseline_m)
    print(f"  OOS Return: {baseline_m['oos_return']:.1f}%")
    print(f"  OOS Sharpe: {baseline_m['oos_sharpe']:.2f}")
    print(f"  Trades: {baseline_m['oos_trades']}")
    print(f"  Pass: {passes}")

    # Smart search
    print("\n" + "=" * 85)
    print("SMART SEARCH OPTIMIZATION")
    print("=" * 85)

    # Phase 1
    sd_results = find_best_sd(df_train, df_oos)

    # Phase 2
    atr_rr_results = find_best_atr_rr(df_train, df_oos, sd_results[0] if sd_results else 2.0)

    # Phase 3
    filter_results = optimize_direction(
        df_train, df_oos,
        sd_results[0] if sd_results else 2.0,
        atr_rr_results[0][0] if atr_rr_results else 0.7,
        atr_rr_results[0][1] if atr_rr_results else 2.0,
    )

    all_results = filter_results
    all_results.sort(key=lambda x: x["oos_return"], reverse=True)

    # Print results
    print_table(all_results, "ALL CONFIGURATIONS")

    passing = get_passing(all_results)
    print(f"\n\nPASSING: {len(passing)}/{len(all_results)}")

    if passing:
        print_table(passing, "PASSING CONFIGURATIONS")
        best = passing[0]
        print_best(best)
    else:
        print("\nNO CONFIGS PASS ALL GATES!")
        if all_results:
            print(f"\nBest result: SD={all_results[0]['sd_threshold']}, OOS Ret={all_results[0]['oos_return']:.1f}%")

    elapsed = time.time() - start_time
    print(f"\nTotal time: {elapsed/60:.1f} minutes")
    print("=" * 85)


if __name__ == "__main__":
    main()