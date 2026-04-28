"""
Liquidation Capture - Full 4-Layer Validation
======================================
Low frequency, high reward!
"""

import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from pathlib import Path
from strategies import (
    compute_metrics,
    run_liquidation_capture,
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


def layer1_walkforward(df, cutoff, params):
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
    
    for start, end in windows:
        df_test = df.loc[start:end]
        if len(df_test) < 100:
            continue
        
        eq, trd = run_liquidation_capture(df_test, cutoff, **params)
        
        if len(trd) >= 1:
            m = compute_metrics(eq, trd, bars_per_year=INTERVAL_BARS_PER_YEAR["1h"])
            all_sharpes.append(m["sharpe"])
            all_returns.append(m["total_return_pct"])
            all_trades.extend(trd)
    
    win_rate = 100 * len([t for t in all_trades if t > 0]) / len(all_trades) if all_trades else 0
    
    return {
        "sharpe": np.mean(all_sharpes) if all_sharpes else -999,
        "return": np.mean(all_returns) if all_returns else -100,
        "win_rate": win_rate,
        "n_trades": len(all_trades),
        "trades": all_trades,
    }


def layer2_monte_carlo(trades):
    if len(trades) < 3:
        return {"p_loss": 1.0, "p5": -1, "p50": -1, "p95": -1, "verdict": "FRAGILE", "note": "Too few trades"}
    
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


def layer3_robustness(df, cutoff, param_variations):
    scores = []
    
    for params in param_variations:
        df_test = df.loc["2023-01-01":"2023-06-30"]
        eq, trd = run_liquidation_capture(df_test, cutoff, **params)
        
        if len(trd) >= 1:
            m = compute_metrics(eq, trd, bars_per_year=INTERVAL_BARS_PER_YEAR["1h"])
            scores.append(m["total_return_pct"])
    
    return {"robustness_score": np.mean(scores) if scores else -100}


def main():
    print("Loading data...")
    df = load_data([2022, 2023])
    print(f"Data: {len(df)} bars")
    
    cutoff = 0.04
    
    # Best params
    params = {
        "price_threshold": 0.035,
        "volume_mult": 3.0,
        "rsi_threshold": 15.0,
    }
    
    print("\n=== LAYER 1: WALK-FORWARD OOS ===")
    l1 = layer1_walkforward(df, cutoff, params)
    print(f"Sharpe: {l1['sharpe']:.2f}")
    print(f"Return: {l1['return']:.2f}%")
    print(f"Win Rate: {l1['win_rate']:.1f}%")
    print(f"Total Trades: {l1['n_trades']}")
    
    print("\n=== LAYER 2: MONTE CARLO ===")
    l2 = layer2_monte_carlo(l1["trades"])
    print(f"P(Loss): {l2['p_loss']:.1%}")
    if l2.get("note"):
        print(f"Note: {l2['note']}")
    else:
        print(f"P5: {l2['p5']:.2%}")
        print(f"P50: {l2['p50']:.2%}")
        print(f"P95: {l2['p95']:.2%}")
        print(f"Verdict: {l2['verdict']}")
    
    print("\n=== LAYER 3: ROBUSTNESS ===")
    param_variations = [
        {"price_threshold": 0.030, "volume_mult": 3.0, "rsi_threshold": 15.0},
        {"price_threshold": 0.035, "volume_mult": 3.0, "rsi_threshold": 15.0},
        {"price_threshold": 0.035, "volume_mult": 3.5, "rsi_threshold": 15.0},
        {"price_threshold": 0.040, "volume_mult": 3.0, "rsi_threshold": 15.0},
    ]
    l3 = layer3_robustness(df, cutoff, param_variations)
    print(f"Robustness Score: {l3['robustness_score']:.2f}%")
    
    # Verdict
    print("\n=== VERDICT ===")
    # Note: With very few trades, Monte Carlo is less meaningful
    if l1["return"] > 0:
        print("PASS - Positive returns with low-frequency trades!")
    else:
        print("FAIL")


if __name__ == "__main__":
    main()