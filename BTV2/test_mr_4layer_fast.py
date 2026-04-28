"""
test_mr_4layer_fast.py — Fast 4-Layer Validation for Mean Reversion
==============================================================

A simplified faster version that uses fixed params and tests on key time windows.
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

from BTV2.strategies import (
    run_mean_reversion,
    run_vwap_scalping,
    compute_metrics,
    build_windows,
    INTERVAL_BARS_PER_YEAR,
    COST_PER_SIDE,
)
from BTV2.strategies import fit_gmm_regime, gmm_regime_summary

try:
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False

BARS_PER_YEAR = INTERVAL_BARS_PER_YEAR["5m"]

# ─── Constants ────────────────────────────────────────────────────────────────
TRADING_COST = 0.003
N_MC_SIMS = 500
TRAIN_MONTHS = 6
TEST_MONTHS = 3
ROBUSTNESS_PCT = 0.15


# ─── Data Loading ─────────────────────────────────────────────────────
def load_data_multiyear(years: list[int]) -> pd.DataFrame:
    """Load and concatenate individual year CSV files."""
    dfs = []
    for year in years:
        path = DATA_DIR / f"BTC-USDC_5m_{year}.csv"
        if path.exists():
            df_year = pd.read_csv(path)
            df_year["timestamp"] = pd.to_datetime(df_year["timestamp"])
            df_year.columns = [c.lower() for c in df_year.columns]
            df_year = df_year.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
            df_year = df_year.set_index("timestamp").sort_index()
            dfs.append(df_year)

    if not dfs:
        raise FileNotFoundError(f"No data found in {DATA_DIR}")

    return pd.concat(dfs).sort_index()


# ─── Fast 4-Layer Validation ─────────────────────────────────────────
def run_fast_validation(df: pd.DataFrame, config_name: str, params: dict) -> dict:
    """Run all 4 layers quickly."""
    cutoff = 0.04
    
    print(f"\n{'='*60}")
    print(f"Validating: {config_name}")
    print(f"Params: {params}")
    print(f"{'='*60}")

    # ─── Layer 1: Rolling 3-month OOS test (fast) ─────────────────────
    print(f"\n[Layer 1] Rolling OOS (3-month windows)...")
    
    # Test on 4 key windows: Q1, Q2, Q3, Q4 of 2023
    windows_2023 = [
        ("2023-01-01", "2023-03-31"),
        ("2023-04-01", "2023-06-30"),
        ("2023-07-01", "2023-09-30"),
        ("2023-10-01", "2023-12-31"),
    ]
    
    all_sharpes = []
    all_returns = []
    all_trades_count = []
    win_rates = []
    max_dds = []
    
    total_closed_trades = []
    
    for start, end in windows_2023:
        df_test = df.loc[start:end]
        if len(df_test) < 500:
            continue
            
        try:
            eq, trd = run_mean_reversion(df_test, cutoff, **params)
            if len(trd) >= 3:
                m = compute_metrics(eq, trd)
                all_sharpes.append(m["sharpe"])
                all_returns.append(m["total_return_pct"])
                all_trades_count.append(len(trd))
                win_rates.append(m["win_rate_pct"])
                max_dds.append(m["max_dd_pct"])
                total_closed_trades.extend(trd)
        except Exception as e:
            print(f"  Error on {start}-{end}: {e}")
            continue

    if not all_sharpes:
        print("  NO TRADES found in any window!")
        return {
            "verdict": "FAIL",
            "oos_sharpe": -999,
            "oos_return": -100,
            "oos_wr": 0,
            "oos_dd": -100,
            "total_trades": 0,
            "mc": {"p_loss": 1.0, "verdict": "FRAGILE"},
            "robustness": {"score": 0, "verdict": "UNKNOWN"},
        }

    oos_sharpe = np.mean(all_sharpes)
    oos_return = np.mean(all_returns)
    oos_wr = np.mean(win_rates)
    oos_dd = abs(min(max_dds))  # max drawdown is negative, take absolute
    
    print(f"  OOS Sharpe:  {oos_sharpe:.2f}")
    print(f"  OOS Return:  {oos_return:+.2f}%")
    print(f"  Win Rate:    {oos_wr:.1f}%")
    print(f"  Max DD:      {oos_dd:.2f}%")
    print(f"  Total trades: {sum(all_trades_count)}")

    # ─── Layer 2: Monte Carlo ───────────────────────────────────────────
    print(f"\n[Layer 2] Monte Carlo ({N_MC_SIMS} sims)...")
    
    if len(total_closed_trades) < 10:
        print("  Not enough trades for MC")
        mc_result = {"p_loss": 1.0, "verdict": "FRAGILE"}
    else:
        returns = np.array(total_closed_trades)
        mc_returns = []
        
        for _ in range(N_MC_SIMS):
            sample = np.random.choice(returns, size=len(returns), replace=True)
            mc_returns.append(np.prod(1 + sample) - 1)
        
        mc_returns = np.array(mc_returns)
        p_loss = np.mean(mc_returns < 0)
        
        mc_result = {
            "p_loss": p_loss,
            "p5": np.percentile(mc_returns, 5),
            "p50": np.percentile(mc_returns, 50),
            "p95": np.percentile(mc_returns, 95),
            "verdict": "ROBUST" if p_loss < 0.05 else ("MARGINAL" if p_loss < 0.15 else "FRAGILE"),
        }
        
        print(f"  P(Loss):    {mc_result['p_loss']*100:.1f}%")
        print(f"  P5 Return:  {mc_result['p5']*100:+.2f}%")
        print(f"  P50 Return: {mc_result['p50']*100:+.2f}%")
        print(f"  P95 Return: {mc_result['p95']*100:+.2f}%")
        print(f"  Verdict:   {mc_result['verdict']}")

    # ─── Layer 3: Robustness ───────────────────────────────────────
    print(f"\n[Layer 3] Robustness Score...")
    
    # Quick test on one window
    df_test = df.loc["2023-07-01":"2023-09-30"]
    if len(df_test) < 500:
        rob_result = {"score": 0, "verdict": "UNKNOWN", "fragile": []}
    else:
        try:
            eq_base, trd_base = run_mean_reversion(df_test, cutoff, **params)
            m_base = compute_metrics(eq_base, trd_base) if len(trd_base) >= 3 else {"sharpe": -math.inf}
            base_sharpe = m_base["sharpe"]
        except:
            base_sharpe = 0
        
        if base_sharpe <= 0:
            rob_result = {"score": 0, "verdict": "UNKNOWN", "fragile": []}
        else:
            # Test single param perturbation
            fragile = []
            for param_name in ["rsi_oversold", "rsi_overbought"]:
                base_val = params[param_name]
                params_up = params.copy()
                params_up[param_name] = base_val * (1 + ROBUSTNESS_PCT)
                
                try:
                    eq_up, trd_up = run_mean_reversion(df_test, cutoff, **params_up)
                    m_up = compute_metrics(eq_up, trd_up) if len(trd_up) >= 3 else {"sharpe": -math.inf}
                    delta = abs(m_up["sharpe"] - base_sharpe) / abs(base_sharpe)
                    if delta > 0.3:
                        fragile.append(param_name)
                except:
                    pass
            
            score = 1.0 - (len(fragile) * 0.2)
            rob_result = {
                "score": max(0, score),
                "verdict": "ROBUST" if score >= 0.8 else ("MARGINAL" if score >= 0.6 else "FRAGILE"),
                "fragile": fragile,
            }
        
        print(f"  Score:     {rob_result['score']:.2f}")
        print(f"  Fragile:   {rob_result['fragile']}")
        print(f"  Verdict:   {rob_result['verdict']}")

    # ─── Layer 4: GMM ─────────────────────────────────────────────
    print(f"\n[Layer 4] GMM Regime Detection...")
    
    if not _HAS_SKLEARN or len(total_closed_trades) < 50:
        gmm_result = {"regimes": {}, "verdict": "SKIP"}
    else:
        df_sample = df.tail(3000).copy()
        
        from BTV2.strategies import compute_atr, compute_returns
        
        close = df_sample["Close"]
        high = df_sample["High"]
        low = df_sample["Low"]
        volume = df_sample["Volume"]
        
        returns = compute_returns(close)
        vol = compute_atr(high, low, close, 14)
        vol_ratio = vol / vol.rolling(20).mean()
        volume_z = (volume - volume.rolling(20).mean()) / volume.rolling(20).std()
        mom = close.rolling(10) / close.shift(10) - 1
        
        features = pd.DataFrame({
            "vol": vol,
            "mom": mom,
            "vol_ratio": vol_ratio,
            "volume_z": volume_z,
        }).dropna()
        
        if len(features) < 100:
            gmm_result = {"regimes": {}, "verdict": "SKIP"}
        else:
            try:
                labels = fit_gmm_regime(features, n_components=4, random_state=42)
                summary = gmm_regime_summary(labels)
                gmm_result = {"regimes": summary, "verdict": "OK"}
                
                print(f"  Calm:     {summary.get('calm', 0)*100:.1f}%")
                print(f"  Trending: {summary.get('trending', 0)*100:.1f}%")
                print(f"  Volatile:{summary.get('volatile', 0)*100:.1f}%")
                print(f"  Crash:   {summary.get('crash', 0)*100:.1f}%")
            except Exception as e:
                gmm_result = {"regimes": {}, "verdict": "SKIP"}

    # ─── Final Verdict ─────────────────────────────────────────────
    print(f"\n{'='*60}")
    
    layer1_pass = oos_sharpe > 0 and oos_return > 0 and oos_wr > 40
    layer2_pass = mc_result["p_loss"] < 0.15
    layer3_pass = rob_result["score"] >= 0.6
    
    if layer1_pass and layer2_pass and layer3_pass:
        if mc_result["p_loss"] < 0.05 and rob_result["score"] >= 0.8:
            verdict = "PASS"
        else:
            verdict = "CAUTION"
    else:
        verdict = "FAIL"
    
    print(f"FINAL VERDICT: {verdict}")
    print(f"  Layer 1 (OOS Sharpe {oos_sharpe:.2f}, Return {oos_return:+.2f}%, WR {oos_wr:.0f}%): {'PASS' if layer1_pass else 'FAIL'}")
    print(f"  Layer 2 (MC P(Loss) {mc_result['p_loss']*100:.1f}%): {'PASS' if layer2_pass else 'FAIL'}")
    print(f"  Layer 3 (Robustness {rob_result['score']:.2f}): {'PASS' if layer3_pass else 'FAIL'}")
    print(f"{'='*60}")

    return {
        "config": config_name,
        "verdict": verdict,
        "params": params,
        "oos_sharpe": oos_sharpe,
        "oos_return": oos_return,
        "oos_wr": oos_wr,
        "oos_dd": oos_dd,
        "total_trades": len(total_closed_trades),
        "mc": mc_result,
        "robustness": rob_result,
        "gmm": gmm_result,
    }


# ─── Main ──────────────────────────────────────────────────────────────
def main():
    print("Loading data (2021-2023)...")
    df = load_data_multiyear([2021, 2022, 2023])
    print(f"Loaded {len(df)} bars, {df.index[0]} to {df.index[-1]}")

    # Test 3 MR configs
    configs = [
        ("MR Default (30/70/10%/3.0)", 
         {"rsi_oversold": 30.0, "rsi_overbought": 70.0, "bb_proximity": 0.10, "atr_stop": 3.0}),
        ("MR Aggressive (25/75/5%/2.5)", 
         {"rsi_oversold": 25.0, "rsi_overbought": 75.0, "bb_proximity": 0.05, "atr_stop": 2.5}),
        ("MR Conservative (35/65/20%/3.5)", 
         {"rsi_oversold": 35.0, "rsi_overbought": 65.0, "bb_proximity": 0.20, "atr_stop": 3.5}),
    ]

    results = []
    for name, params in configs:
        result = run_fast_validation(df, name, params)
        results.append(result)

    # ─── Summary Table ──────────────────────────────────────────────
    print(f"\n{'#'*60}")
    print(f"# MEAN REVERSION 4-LAYER VALIDATION SUMMARY")
    print(f"{'#'*60}")
    print(f"{'Config':<28} {'Verdict':<8} {'Sharpe':<8} {'Return':<10} {'WR':<6} {'P(Loss)':<8}")
    print(f"{'-'*60}")

    for r in results:
        print(f"{r['config']:<28} {r['verdict']:<8} {r['oos_sharpe']:<8.2f} {r['oos_return']:<+10.2f} {r['oos_wr']:<6.0f} {r['mc']['p_loss']*100:<8.1f}%")

    print(f"{'-'*60}")


if __name__ == "__main__":
    main()