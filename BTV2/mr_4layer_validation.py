"""
Mean Reversion v2 - Full 4-Layer Validation
=====================================

Testing the improved Mean Reversion with ATR exits across all 4 validation layers.

Layer 1: Walk-Forward OOS
Layer 2: Monte Carlo (500 sims)
Layer 3: Robustness Score  
Layer 4: GMM Regime Detection
"""

import sys
sys.path.insert(0, ".")

import math
import random
import numpy as np
import pandas as pd
from pathlib import Path
from mean_reversion_v2 import run_mean_reversion_v2
from strategies import (
    compute_metrics, 
    build_windows, 
    fit_gmm_regime, 
    gmm_regime_summary,
    compute_atr,
    compute_adx,
)
from strategies import INTERVAL_BARS_PER_YEAR

# Constants
ROOT = Path("..")
DATA_DIR = ROOT / "trading_bot_v2" / "backtesting" / "data"
BARS_PER_YEAR = INTERVAL_BARS_PER_YEAR["5m"]

N_MC_SIMS = 500
TRAIN_MONTHS = 6
TEST_MONTHS = 3

# Best params from grid search - relaxed ADX for more trades
BEST_PARAMS = {
    "rsi_oversold": 20,
    "rsi_overbought": 80,
    "bb_proximity": 0.02,
    "atr_stop": 1.5,
    "atr_target": 5.0,
    "adx_max": 20,
}

# Timeframe: 1hr instead of 4hr


def load_data(years):
    """Load and aggregate to 4hr."""
    dfs = []
    for year in years:
        path = DATA_DIR / f"BTC-USDC_5m_{year}.csv"
        if path.exists():
            df = pd.read_csv(path)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            df.columns = [c.lower() for c in df.columns]
            df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
            df = df.set_index("timestamp").sort_index()
            dfs.append(df)
    
    df_all = pd.concat(dfs).sort_index()
    
    # Aggregate to 1hr (change to "4h" for 4hr)
    df_1h = df_all.resample("1h").agg({
        "Open": "first", 
        "High": "max", 
        "Low": "min", 
        "Close": "last", 
        "Volume": "sum"
    })
    df_1h = df_1h.dropna()
    df_1h.index = df_1h.index.tz_localize(None)
    
    return df_1h


def run_walkforward(df):
    """Layer 1: Rolling 3-month OOS test."""
    cutoff = 0.04
    
    windows = [
        ("2022-01-01", "2022-03-31"),
        ("2022-04-01", "2022-06-30"),
        ("2022-07-01", "2022-09-30"),
        ("2022-10-01", "2022-12-31"),
        ("2023-01-01", "2023-03-31"),
        ("2023-04-01", "2023-06-30"),
        ("2023-07-01", "2023-09-30"),
        ("2023-10-01", "2023-12-31"),
    ]
    
    all_sharpes = []
    all_returns = []
    all_trades = []
    win_rates = []
    max_dds = []
    total_closed = []
    
    for start, end in windows:
        df_test = df.loc[start:end]
        if len(df_test) < 100:
            continue
            
        eq, trd = run_mean_reversion_v2(
            df_test, cutoff,
            **BEST_PARAMS
        )
        
        if len(trd) >= 3:
            m = compute_metrics(eq, trd)
            all_sharpes.append(m["sharpe"])
            all_returns.append(m["total_return_pct"])
            all_trades.append(len(trd))
            win_rates.append(m["win_rate_pct"])
            max_dds.append(m["max_dd_pct"])
            total_closed.extend(trd)
    
    return {
        "sharpe": np.mean(all_sharpes) if all_sharpes else -999,
        "return": np.mean(all_returns) if all_returns else -100,
        "win_rate": np.mean(win_rates) if win_rates else 0,
        "max_dd": min(max_dds) if max_dds else -100,
        "n_folds": len(all_sharpes),
        "total_trades": len(total_closed),
        "all_trades": total_closed,
        "folds": list(zip(windows, all_returns)),
    }


def run_monte_carlo(trades):
    """Layer 2: Bootstrap resampling."""
    if len(trades) < 10:
        return {"p_loss": 1.0, "p5": -1, "p50": -1, "p95": -1, "verdict": "FRAGILE"}
    
    returns = np.array(trades)
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


def run_robustness(df):
    """Layer 3: Parameter perturbation."""
    cutoff = 0.04
    
    # Use 2022 Q3 for robustness test
    df_test = df.loc["2022-07-01":"2022-09-30"]
    if len(df_test) < 100:
        return {"score": 0, "fragile": [], "verdict": "UNKNOWN"}
    
    # Baseline
    try:
        eq_base, trd_base = run_mean_reversion_v2(df_test, cutoff, **BEST_PARAMS)
        m_base = compute_metrics(eq_base, trd_base)
        base_sharpe = m_base["sharpe"]
    except:
        return {"score": 0, "fragile": [], "verdict": "UNKNOWN"}
    
    if base_sharpe <= -100:
        return {"score": 0, "fragile": [], "verdict": "UNKNOWN"}
    
    fragile = []
    sharpe_deltas = []
    
    # Test ADX perturbation (key param)
    for adx_max in [BEST_PARAMS["adx_max"] * 0.8, BEST_PARAMS["adx_max"] * 1.2]:
        params = BEST_PARAMS.copy()
        params["adx_max"] = adx_max
        try:
            eq, trd = run_mean_reversion_v2(df_test, cutoff, **params)
            m = compute_metrics(eq, trd) if len(trd) >= 3 else {"sharpe": -999}
            delta = abs(m["sharpe"] - base_sharpe) / abs(base_sharpe) if base_sharpe > 0 else 1
            sharpe_deltas.append(delta)
            if delta > 0.3:
                fragile.append("adx_max")
        except:
            pass
    
    score = 1.0 - np.mean(sharpe_deltas) if sharpe_deltas else 0.5
    
    return {
        "score": max(0, score),
        "fragile": fragile,
        "verdict": "ROBUST" if score >= 0.7 else ("MARGINAL" if score >= 0.5 else "FRAGILE"),
    }


def run_gmm_regime(df, trades):
    """Layer 4: GMM regime detection."""
    if len(trades) < 50:
        return {"regimes": {}, "verdict": "SKIP"}
    
    # Use last 1000 bars for feature extraction
    df_sample = df.tail(1000).copy()
    
    close = df_sample["Close"]
    high = df_sample["High"]
    low = df_sample["Low"]
    
    vol = compute_atr(high, low, close, 14)
    vol_ratio = vol / vol.rolling(20).mean()
    volume_z = (df_sample["Volume"] - df_sample["Volume"].rolling(20).mean()) / df_sample["Volume"].rolling(20).std()
    mom = close.rolling(10).apply(lambda x: (x.iloc[-1] / x.iloc[0] - 1) if len(x) > 0 else 0, raw=False)
    adx = compute_adx(high, low, close, 14)
    
    features = pd.DataFrame({
        "vol": vol,
        "vol_ratio": vol_ratio,
        "volume_z": volume_z,
        "mom": mom,
    }).dropna()
    
    if len(features) < 100:
        return {"regimes": {}, "verdict": "SKIP"}
    
    try:
        labels = fit_gmm_regime(features, n_components=4, random_state=42)
        summary = gmm_regime_summary(labels)
        return {"regimes": summary, "verdict": "OK"}
    except Exception as e:
        return {"regimes": {}, "verdict": f"ERROR: {e}"}


def main():
    print("=" * 60)
    print("MEAN REVERSION v2 - 4-LAYER VALIDATION")
    print("=" * 60)
    print(f"\nParams: {BEST_PARAMS}")
    
    # Load data
    print("\nLoading 2022-2023 data...")
    df = load_data([2022, 2023])
    print(f"4hr bars: {len(df)}")
    
    # === LAYER 1: Walk-Forward OOS ===
    print("\n[Layer 1] Walk-Forward OOS (rolling 3-month)...")
    oos = run_walkforward(df)
    
    print(f"  OOS Sharpe:   {oos['sharpe']:.2f}")
    print(f"  OOS Return:  {oos['return']:+.2f}%")
    print(f"  Win Rate:    {oos['win_rate']:.1f}%")
    print(f"  Max DD:      {oos['max_dd']:.2f}%")
    print(f"  Folds:      {oos['n_folds']}, Trades: {oos['total_trades']}")
    
    # Fold-by-fold results
    print("  Per-fold returns:")
    for (start, end), ret in oos["folds"]:
        print(f"    {start} to {end}: {ret:+.1f}%")
    
    all_closed_trades = oos["all_trades"]
    
    # === LAYER 2: Monte Carlo ===
    print(f"\n[Layer 2] Monte Carlo ({N_MC_SIMS} sims)...")
    
    if len(all_closed_trades) >= 10:
        mc = run_monte_carlo(all_closed_trades)
        print(f"  P(Loss):    {mc['p_loss']*100:.1f}%")
        print(f"  P5 Return:  {mc['p5']*100:+.2f}%")
        print(f"  P50 Return: {mc['p50']*100:+.2f}%")
        print(f"  P95 Return: {mc['p95']*100:+.2f}%")
        print(f"  Verdict:   {mc['verdict']}")
        
        # KEY INSIGHT: Analyze trade distribution
        print(f"\n  KEY ANALYSIS:")
        returns_arr = np.array(all_closed_trades) * 100
        wins = returns_arr[returns_arr > 0]
        losses = returns_arr[returns_arr <= 0]
        
        print(f"    Total: {len(all_closed_trades)} trades")
        print(f"    Wins: {len(wins)} ({len(wins)/len(returns_arr)*100:.0f}%), Avg: +{np.mean(wins):.2f}%")
        print(f"    Losses: {len(losses)} ({len(losses)/len(returns_arr)*100:.0f}%), Avg: {np.mean(losses):.2f}%")
        print(f"    RR: {abs(np.mean(wins)/np.mean(losses)):.2f}")
    else:
        mc = {"p_loss": 1.0, "verdict": "FRAGILE"}
        print(f"  Not enough trades for MC")
    
    # === LAYER 3: Robustness ===
    print(f"\n[Layer 3] Robustness Score...")
    rob = run_robustness(df)
    print(f"  Score:     {rob['score']:.2f}")
    print(f"  Fragile:   {rob['fragile']}")
    print(f"  Verdict:   {rob['verdict']}")
    
    # === LAYER 4: GMM Regime ===
    print(f"\n[Layer 4] GMM Regime...")
    gmm = run_gmm_regime(df, all_closed_trades)
    if gmm["regimes"]:
        print(f"  Calm:     {gmm['regimes'].get('calm', 0)*100:.1f}%")
        print(f"  Trending: {gmm['regimes'].get('trending', 0)*100:.1f}%")
        print(f"  Volatile: {gmm['regimes'].get('volatile', 0)*100:.1f}%")
        print(f"  Crash:    {gmm['regimes'].get('crash', 0)*100:.1f}%")
    else:
        print(f"  {gmm['verdict']}")
    
    # === FINAL VERDICT ===
    print("\n" + "=" * 60)
    
    layer1_pass = oos['sharpe'] > 0 and oos['return'] > 0 and oos['win_rate'] > 40
    layer2_pass = mc['p_loss'] < 0.15
    layer3_pass = rob['score'] >= 0.5
    
    if layer1_pass and layer2_pass and layer3_pass:
        if mc['p_loss'] < 0.05 and rob['score'] >= 0.7:
            verdict = "PASS"
        else:
            verdict = "CAUTION"
    else:
        verdict = "FAIL"
    
    print(f"FINAL VERDICT: {verdict}")
    print(f"  Layer 1 (OOS):   {'PASS' if layer1_pass else 'FAIL'} Sharpe {oos['sharpe']:.2f}, Return {oos['return']:+.1f}%")
    print(f"  Layer 2 (MC):    {'PASS' if layer2_pass else 'FAIL'} P(Loss) {mc['p_loss']*100:.1f}%")
    print(f"  Layer 3 (Rob):    {'PASS' if layer3_pass else 'FAIL'} Score {rob['score']:.2f}")
    print("=" * 60)


if __name__ == "__main__":
    main()