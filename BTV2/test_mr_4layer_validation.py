"""
test_mr_4layer_validation.py — 4-Layer Mean Reversion Validation
=========================================================

4-LAYER VALIDATION SUITE for Mean Reversion strategy configs.

Layer 1 — Walk-Forward OOS:
    Rolling 6m train / 3m test windows across 2021-2023.
    Aggregated OOS Sharpe, return, win rate, max drawdown per config.

Layer 2 — Monte Carlo (500 sims):
    Shuffled trade-order simulations on the full combined trade list.
    P(loss), p5/p50/p95 percentile returns, ROBUST/MARGINAL/FRAGILE verdict.

Layer 3 — Robustness Score (+-15% param perturbation):
    Each param perturbed +-15%, Sharpe delta measured.
    Fragile params flagged, overall ROBUST score computed.

Layer 4 — GMM Regime Detection (4 regimes):
    Fit GaussianMixture on vol/mom/vol_ratio/volume_z features.
    Report calm/trending/volatile/crash distribution.

Data    : BTC-USDC_5m 2021-2023 (~315K bars)
Output  : Formatted results table per config
"""

from __future__ import annotations

import math
import random
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

# ─── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "trading_bot_v2" / "backtesting" / "data"
BTV2_DIR = ROOT / "BTV2"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BTV2_DIR))

try:
    from BTV2.strategies import (
        compute_metrics,
        fit_gmm_regime,
        predict_gmm_regime,
        gmm_regime_summary,
        INTERVAL_BARS_PER_YEAR,
        COST_PER_SIDE,
    )
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False
    COST_PER_SIDE = 0.0015
    INTERVAL_BARS_PER_YEAR = {"5m": 105120}

BARS_PER_YEAR = INTERVAL_BARS_PER_YEAR["5m"]

# ─── Constants ────────────────────────────────────────────────────────────────
TRADING_COST = 0.003   # 0.30% per side = 0.60% round trip
N_MC_SIMS = 500
TRAIN_MONTHS = 6
TEST_MONTHS = 3
ROBUSTNESS_PCT = 0.15           # +-15% perturbation


# ─── Config dataclass ──────────────────────────────────────────────────────────
@dataclass
class MRConfig:
    rsi_oversold: float
    rsi_overbought: float
    bb_proximity: float
    atr_stop: float
    name: str = ""


# ─── Data Loading ─────────────────────────────────────────────────────────────
def load_data_multiyear(years: list[int]) -> pd.DataFrame:
    """Load and concatenate individual year CSV files."""
    dfs = []
    for year in years:
        path = DATA_DIR / f"BTC-USDC_5m_{year}.csv"
        if path.exists():
            df_year = pd.read_csv(path)
            df_year["timestamp"] = pd.to_datetime(df_year["timestamp"])
            # Standardize column names
            df_year.columns = [c.lower() for c in df_year.columns]
            df_year = df_year.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
            df_year = df_year.set_index("timestamp").sort_index()
            dfs.append(df_year)

    if not dfs:
        raise FileNotFoundError(f"No data found in {DATA_DIR}")

    return pd.concat(dfs).sort_index()


# ─── Grid Search (Training) ────────────────────────────────────────────
def grid_search_train(df: pd.DataFrame, config: MRConfig) -> dict:
    """Find best params on training window (simplified - only 3 combos)."""
    from BTV2.strategies import run_mean_reversion, compute_metrics

    cutoff = 0.04

    # Quick grid: only 3 key combos
    candidates = [
        {"rsi_oversold": 30.0, "rsi_overbought": 70.0, "bb_proximity": 0.10, "atr_stop": 3.0},
        {"rsi_oversold": 25.0, "rsi_overbought": 75.0, "bb_proximity": 0.05, "atr_stop": 2.5},
        {"rsi_oversold": 35.0, "rsi_overbought": 65.0, "bb_proximity": 0.20, "atr_stop": 3.5},
    ]

    best_sharpe = -math.inf
    best_params = candidates[0]

    for params in candidates:
        try:
            eq, trd = run_mean_reversion(df, cutoff, **params)
            if len(trd) >= 3:
                m = compute_metrics(eq, trd)
                if m["sharpe"] > best_sharpe:
                    best_sharpe = m["sharpe"]
                    best_params = params
        except Exception:
            pass

    return best_params


# ─── Layer 1: Walk-Forward OOS ───────────────────────────────────────────
def run_walkforward_oos(df: pd.DataFrame, config: MRConfig) -> dict:
    """Layer 1: Rolling walk-forward validation."""
    from BTV2.strategies import run_mean_reversion, compute_metrics, build_windows

    cutoff = 0.04

    # Get timezone from df index
    tz = df.index.tz

    windows = build_windows(
        start=df.index[0].date(),
        end=df.index[-1].date(),
        train_months=TRAIN_MONTHS,
        test_months=TEST_MONTHS,
    )

    all_oos_sharpes = []
    all_oos_returns = []
    all_oos_trades = []
    win_counts = []
    max_dds = []

    for fold, w in enumerate(windows):
        train_start = pd.Timestamp(w["train_start"]).tz_localize(tz)
        train_end = pd.Timestamp(w["train_end"]).tz_localize(tz)
        test_start = pd.Timestamp(w["test_start"]).tz_localize(tz)
        test_end = pd.Timestamp(w["test_end"]).tz_localize(tz)

        # Train on this window
        df_train = df.loc[train_start:train_end].copy()
        df_test = df.loc[test_start:test_end].copy()

        if len(df_train) < 500 or len(df_test) < 500:
            continue

        # Get best params from training (or use config defaults if train fails)
        try:
            train_params = grid_search_train(df_train, config)
        except Exception:
            train_params = {"rsi_oversold": config.rsi_oversold, "rsi_overbought": config.rsi_overbought,
                            "bb_proximity": config.bb_proximity, "atr_stop": config.atr_stop}

        # Run backtest on test window
        try:
            eq, trd = run_mean_reversion(
                df_test, cutoff,
                rsi_oversold=train_params["rsi_oversold"],
                rsi_overbought=train_params["rsi_overbought"],
                bb_proximity=train_params["bb_proximity"],
                atr_stop=train_params["atr_stop"],
            )

            if len(trd) >= 3:
                m = compute_metrics(eq, trd)
                all_oos_sharpes.append(m["sharpe"])
                all_oos_returns.append(m["return"])
                all_oos_trades.append(len(trd))
                win_counts.append(m["win_rate"])
                max_dds.append(m["max_drawdown"])
        except Exception:
            pass

    if not all_oos_sharpes:
        return {"sharpe": -999, "return": -100, "win_rate": 0, "max_drawdown": -100,
                "n_folds": 0, "n_trades": 0}

    return {
        "sharpe": np.mean(all_oos_sharpes),
        "return": np.mean(all_oos_returns),
        "win_rate": np.mean(win_counts),
        "max_drawdown": np.max(max_dds),
        "n_folds": len(all_oos_sharpes),
        "n_trades": sum(all_oos_trades),
        "all_sharpes": all_oos_sharpes,
        "all_returns": all_oos_returns,
    }


# ─── Layer 2: Monte Carlo ───────────────────────────────────────────────────
def run_monte_carlo(closed_trades: list[float]) -> dict:
    """Layer 2: Bootstrap resampling of trade returns."""
    if len(closed_trades) < 10:
        return {"p_loss": 1.0, "p5": -1.0, "p50": -1.0, "p95": -1.0, "verdict": "FRAGILE"}

    returns = np.array(closed_trades)
    mc_returns = []

    for _ in range(N_MC_SIMS):
        sample = np.random.choice(returns, size=len(returns), replace=True)
        mc_returns.append(np.prod(1 + sample) - 1)

    mc_returns = np.array(mc_returns)
    p_loss = np.mean(mc_returns < 0)

    return {
        "p_loss": p_loss,
        "p5": np.percentile(mc_returns, 5),
        "p50": np.percentile(mc_returns, 50),
        "p95": np.percentile(mc_returns, 95),
        "verdict": "ROBUST" if p_loss < 0.05 else ("MARGINAL" if p_loss < 0.15 else "FRAGILE"),
    }


# ─── Layer 3: Robustness Score ──────────────────────────────────────────────
def run_robustness(df: pd.DataFrame, config: MRConfig, base_params: dict) -> dict:
    """Layer 3: Parameter perturbation test."""
    from BTV2.strategies import run_mean_reversion, compute_metrics

    cutoff = 0.04

    # Get a test window
    test_start = df.index[0] + pd.DateOffset(months=TRAIN_MONTHS)
    test_end = test_start + pd.DateOffset(months=TEST_MONTHS) - pd.DateOffset(days=1)
    df_test = df.loc[test_start:test_end].copy()

    if len(df_test) < 500:
        return {"score": 0.0, "fragile_params": [], "verdict": "UNKNOWN"}

    # Baseline
    try:
        eq_base, trd_base = run_mean_reversion(df_test, cutoff, **base_params)
        m_base = compute_metrics(eq_base, trd_base)
        base_sharpe = m_base["sharpe"]
    except Exception:
        return {"score": 0.0, "fragile_params": [], "verdict": "UNKNOWN"}

    param_names = ["rsi_oversold", "rsi_overbought", "bb_proximity", "atr_stop"]
    fragile_params = []
    sharpe_deltas = []

    for param in param_names:
        base_val = base_params[param]

        # Perturb +15%
        params_up = base_params.copy()
        params_up[param] = base_val * (1 + ROBUSTNESS_PCT)
        try:
            eq_up, trd_up = run_mean_reversion(df_test, cutoff, **params_up)
            m_up = compute_metrics(eq_up, trd_up) if len(trd_up) >= 3 else {"sharpe": -math.inf}
        except Exception:
            m_up = {"sharpe": -math.inf}

        # Perturb -15%
        params_dn = base_params.copy()
        params_dn[param] = base_val * (1 - ROBUSTNESS_PCT)
        try:
            eq_dn, trd_dn = run_mean_reversion(df_test, cutoff, **params_dn)
            m_dn = compute_metrics(eq_dn, trd_dn) if len(trd_dn) >= 3 else {"sharpe": -math.inf}
        except Exception:
            m_dn = {"sharpe": -math.inf}

        # Calculate impact
        if base_sharpe > 0:
            delta_up = (m_up["sharpe"] - base_sharpe) / abs(base_sharpe)
            delta_dn = (m_dn["sharpe"] - base_sharpe) / abs(base_sharpe)
            avg_delta = (abs(delta_up) + abs(delta_dn)) / 2
            sharpe_deltas.append(avg_delta)

            if avg_delta > 0.3:  # >30% degradation = fragile
                fragile_params.append(param)

    # Robustness score: 1 - average relative degradation
    avg_degradation = np.mean(sharpe_deltas) if sharpe_deltas else 1.0
    score = max(0.0, 1.0 - avg_degradation)

    return {
        "score": score,
        "fragile_params": fragile_params,
        "verdict": "ROBUST" if score >= 0.90 else ("MARGINAL" if score >= 0.75 else "FRAGILE"),
    }


# ─── Layer 4: GMM Regime ─────────────────────────────────────────────────────
def run_gmm_regime(closed_trades: list[float], df: pd.DataFrame) -> dict:
    """Layer 4: GMM-based regime analysis."""
    if not _HAS_SKLEARN or len(closed_trades) < 50:
        return {"regimes": {}, "verdict": "UNKNOWN"}

    # Use last 3000 bars for regime fitting
    df_sample = df.tail(3000).copy()

    # Build features
    from BTV2.strategies import compute_atr, compute_returns

    close = df_sample["Close"]
    high = df_sample["High"]
    low = df_sample["Low"]
    volume = df_sample["Volume"]

    returns = compute_returns(close)
    vol = compute_atr(high, low, close, 14)

    vol_ratio = vol / vol.rolling(20).mean()
    volume_z = (volume - volume.rolling(20).mean()) / volume.rolling(20).std()

    # Rolling momentum
    mom = close.rolling(10) / close.shift(10) - 1

    features = pd.DataFrame({
        "vol": vol,
        "mom": mom,
        "vol_ratio": vol_ratio,
        "volume_z": volume_z,
    }).dropna()

    if len(features) < 100:
        return {"regimes": {}, "verdict": "UNKNOWN"}

    try:
        labels = fit_gmm_regime(features, n_components=4, random_state=42)
        summary = gmm_regime_summary(labels)
        return {"regimes": summary, "verdict": "OK"}
    except Exception:
        return {"regimes": {}, "verdict": "UNKNOWN"}


# ─── Full Validation Runner ────────────────────────────────────────────────
def validate_config(df: pd.DataFrame, config: MRConfig) -> dict:
    """Run all 4 layers and return verdict."""
    print(f"\n{'='*60}")
    print(f"Validating: {config.name}")
    print(f"{'='*60}")

    # ─── Layer 1: Walk-Forward OOS ────────────────────────────────────────
    print(f"\n[Layer 1] Walk-Forward OOS...")
    oos = run_walkforward_oos(df, config)
    print(f"  OOS Sharpe:     {oos['sharpe']:.2f}")
    print(f"  OOS Return:   {oos['return']:+.2f}%")
    print(f"  Win Rate:     {oos['win_rate']:.1f}%")
    print(f"  Max Drawdown:  {oos['max_drawdown']:.2f}%")
    print(f"  Folds:       {oos['n_folds']}, Trades: {oos['n_trades']}")

    # ─── Full Backtest for Layers 2-4 ───────────────────────────────────
    print(f"\n[Layers 2-4] Full backtest for MC + Robustness + GMM...")
    from BTV2.strategies import run_mean_reversion, compute_metrics

    cutoff = 0.04

    # Get optimal params via grid search on 2021 data
    df_2021 = df.loc["2021-01-01":"2021-06-30"]
    best_params = grid_search_train(df_2021, config)

    # Run on all data
    eq, trd = run_mean_reversion(
        df, cutoff,
        rsi_oversold=best_params["rsi_oversold"],
        rsi_overbought=best_params["rsi_overbought"],
        bb_proximity=best_params["bb_proximity"],
        atr_stop=best_params["atr_stop"],
    )

    m = compute_metrics(eq, trd)
    print(f"  Full trades:   {len(trd)}")
    print(f"  Full Sharpe:  {m['sharpe']:.2f}")
    print(f"  Full Return:  {m['return']:+.2f}%")

    # ─── Layer 2: Monte Carlo ───────────────────────────────────────────
    print(f"\n[Layer 2] Monte Carlo ({N_MC_SIMS} sims)...")
    mc = run_monte_carlo(trd)
    print(f"  P(Loss):     {mc['p_loss']*100:.1f}%")
    print(f"  P5 Return:   {mc['p5']*100:+.2f}%")
    print(f"  P50 Return:  {mc['p50']*100:+.2f}%")
    print(f"  P95 Return:  {mc['p95']*100:+.2f}%")
    print(f"  Verdict:     {mc['verdict']}")

    # ─── Layer 3: Robustness ───────────────────────────────────────────
    print(f"\n[Layer 3] Robustness Score...")
    rob = run_robustness(df, config, best_params)
    print(f"  Score:       {rob['score']:.2f}")
    print(f"  Fragile:      {rob['fragile_params']}")
    print(f"  Verdict:      {rob['verdict']}")

    # ─── Layer 4: GMM Regime ────────────────────────────────────────────
    print(f"\n[Layer 4] GMM Regime Detection...")
    gmm = run_gmm_regime(trd, df)
    regimes = gmm.get("regimes", {})
    if regimes:
        print(f"  Calm:        {regimes.get('calm', 0)*100:.1f}%")
        print(f"  Trending:    {regimes.get('trending', 0)*100:.1f}%")
        print(f"  Volatile:    {regimes.get('volatile', 0)*100:.1f}%")
        print(f"  Crash:       {regimes.get('crash', 0)*100:.1f}%")
    else:
        print(f"  (Insufficient data)")

    # ─── Final Verdict ─────────────────────────────────────────────────
    print(f"\n{'='*60}")

    # Determine final verdict
    layer1_pass = oos["sharpe"] > 0 and oos["return"] > 0 and oos["win_rate"] > 40
    layer2_pass = mc["p_loss"] < 0.15
    layer3_pass = rob["score"] >= 0.75

    layers_pass = sum([layer1_pass, layer2_pass, layer3_pass])

    if layers_pass >= 3 and mc["p_loss"] < 0.05:
        verdict = "PASS"
    elif layers_pass >= 2:
        verdict = "CAUTION"
    else:
        verdict = "FAIL"

    print(f"FINAL VERDICT: {verdict}")
    print(f"  Layer 1 (OOS):   {'✓' if layer1_pass else '✗'}")
    print(f"  Layer 2 (MC):    {'✓' if layer2_pass else '✗'} (P(loss)={mc['p_loss']*100:.1f}%)")
    print(f"  Layer 3 (Robust):{'✓' if layer3_pass else '✗'} (score={rob['score']:.2f})")
    print(f"{'='*60}")

    return {
        "config": config.name,
        "verdict": verdict,
        "oos": oos,
        "mc": mc,
        "robustness": rob,
        "gmm": gmm,
        "best_params": best_params,
        "full_trades": len(trd),
    }


# ─── Main ────────────────────────────────────────────────────────────────
def main():
    import argparse

    parser = argparse.ArgumentParser(description="4-Layer Mean Reversion Validation")
    parser.add_argument("--config", type=str, default="default",
                      help="Config name: default, aggressive, conservative")
    args = parser.parse_args()

    print("Loading data (2021-2023)...")
    try:
        df = load_data_multiyear([2021, 2022, 2023])
        print(f"Loaded {len(df)} bars, {df.index[0]} to {df.index[-1]}")
    except Exception as e:
        print(f"Error loading data: {e}")
        return

    # Define configs to test
    configs = [
        MRConfig(
            rsi_oversold=30.0,
            rsi_overbought=70.0,
            bb_proximity=0.10,
            atr_stop=3.0,
            name="MR Default (30/70/10%/3.0)",
        ),
        MRConfig(
            rsi_oversold=25.0,
            rsi_overbought=75.0,
            bb_proximity=0.05,
            atr_stop=2.5,
            name="MR Aggressive (25/75/5%/2.5)",
        ),
        MRConfig(
            rsi_oversold=35.0,
            rsi_overbought=65.0,
            bb_proximity=0.20,
            atr_stop=3.5,
            name="MR Conservative (35/65/20%/3.5)",
        ),
    ]

    if args.config == "default":
        configs = [configs[0]]
    elif args.config == "aggressive":
        configs = [configs[1]]
    elif args.config == "conservative":
        configs = [configs[2]]

    results = []
    for config in configs:
        result = validate_config(df, config)
        results.append(result)

    # ─── Summary Table ──────────────────────────────────────────────────
    print(f"\n{'#'*60}")
    print(f"# MEAN REVERSION 4-LAYER VALIDATION SUMMARY")
    print(f"{'#'*60}")
    print(f"{'Config':<30} {'Verdict':<10} {'OOS Sharpe':<12} {'OOS Return':<12} {'P(Loss)':<10}")
    print(f"{'-'*60}")

    for r in results:
        print(f"{r['config']:<30} {r['verdict']:<10} {r['oos']['sharpe']:<12.2f} {r['oos']['return']:<+12.2f} {r['mc']['p_loss']*100:<10.1f}%")

    print(f"{'-'*60}")


if __name__ == "__main__":
    main()