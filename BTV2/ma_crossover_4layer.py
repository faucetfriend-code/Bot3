"""
MA Crossover Strategy - 4-Layer Validation
========================================
Trend following strategy: Buy on golden cross, sell on death cross.
"""

import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from pathlib import Path
from strategies import (
    compute_metrics,
    run_ma_crossover,
    build_windows,
)
from strategies import INTERVAL_BARS_PER_YEAR

ROOT = Path("..")
DATA_DIR = ROOT / "trading_bot_v2" / "backtesting" / "data"

N_MC_SIMS = 500


def load_data(years):
    dfs = []
    for year in years:
        path = DATA_DIR / f"BTC-USDC_5m_{year}.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.columns = [c.lower() for c in df.columns]
        df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
        df = df.set_index("timestamp").sort_index()
        dfs.append(df)
    
    df_all = pd.concat(dfs).sort_index()
    
    # Aggregate to 1h
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


def layer1_walkforward(df):
    """Layer 1: Rolling walk-forward OOS."""
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
    
    # Test different parameter sets
    param_sets = [
        # v1: Base (20/50)
        {"fast_period": 20, "slow_period": 50, "pullback_max": 0.06},
        # v2: Faster (15/40)
        {"fast_period": 15, "slow_period": 40, "pullback_max": 0.08},
        # v3: Slower (10/30)
        {"fast_period": 10, "slow_period": 30, "pullback_max": 0.08},
        # v4: Very fast (8/21)
        {"fast_period": 8, "slow_period": 21, "pullback_max": 0.10},
    ]
    
    results = []
    
    for params in param_sets:
        all_sharpes = []
        all_returns = []
        all_trades = []
        win_rates = []
        
        for start, end in windows:
            df_test = df.loc[start:end]
            if len(df_test) < 100:
                continue
            
            eq, trd = run_ma_crossover(
                df_test, cutoff,
                **params,
            )
        
        if len(trd) >= 3:
            m = compute_metrics(eq, trd, bars_per_year=INTERVAL_BARS_PER_YEAR["1h"])
            all_sharpes.append(m["sharpe"])
            all_returns.append(m["total_return_pct"])
            all_trades.extend(trd)
            win_rates.append(m["win_rate_pct"])
    
    return {
        "sharpe": np.mean(all_sharpes) if all_sharpes else -999,
        "return": np.mean(all_returns) if all_returns else -100,
        "win_rate": np.mean(win_rates) if win_rates else 0,
        "n_trades": len(all_trades),
        "trades": all_trades,
    }


def layer2_monte_carlo(trades):
    """Layer 2: Bootstrap Monte Carlo."""
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
        "verdict": "ROBUST" if p_loss < 0.30 else "FRAGILE",
    }


def layer3_robustness(df):
    """Layer 3: Parameter robustness."""
    cutoff = 0.04
    
    param_variations = [
        {"fast_period": 15, "slow_period": 50, "pullback_max": 0.06},
        {"fast_period": 20, "slow_period": 50, "pullback_max": 0.06},
        {"fast_period": 20, "slow_period": 60, "pullback_max": 0.06},
        {"fast_period": 25, "slow_period": 50, "pullback_max": 0.06},
        {"fast_period": 20, "slow_period": 50, "pullback_max": 0.08},
    ]
    
    scores = []
    
    for params in param_variations:
        df_test = df.loc["2023-01-01":"2023-06-30"]
        eq, trd = run_ma_crossover(df_test, cutoff, **params)
        
        if len(trd) >= 3:
            m = compute_metrics(eq, trd, bars_per_year=INTERVAL_BARS_PER_YEAR["1h"])
            scores.append(m["total_return_pct"])
    
    return {"robustness_score": np.mean(scores) if scores else -100}


def main():
    print("Loading data...")
    df = load_data([2022, 2023])
    print(f"Data: {len(df)} bars")
    
    print("\n=== LAYER 1: WALK-FORWARD OOS ===")
    l1 = layer1_walkforward(df)
    print(f"Sharpe: {l1['sharpe']:.2f}")
    print(f"Return: {l1['return']:.2f}%")
    print(f"Win Rate: {l1['win_rate']:.1f}%")
    print(f"Total Trades: {l1['n_trades']}")
    
    print("\n=== LAYER 2: MONTE CARLO ===")
    l2 = layer2_monte_carlo(l1["trades"])
    print(f"P(Loss): {l2['p_loss']:.1%}")
    print(f"P5: {l2['p5']:.2%}")
    print(f"P50: {l2['p50']:.2%}")
    print(f"P95: {l2['p95']:.2%}")
    print(f"Verdict: {l2['verdict']}")
    
    print("\n=== LAYER 3: ROBUSTNESS ===")
    l3 = layer3_robustness(df)
    print(f"Robustness Score: {l3['robustness_score']:.2f}%")
    
    # Verdict
    print("\n=== VERDICT ===")
    if l1["return"] > 0 and l2["p_loss"] < 0.30 and l3["robustness_score"] > 0:
        print("PASS")
    elif l1["return"] > 0:
        print("MARGINAL - Positive returns")
    else:
        print("FAIL")


if __name__ == "__main__":
    main()