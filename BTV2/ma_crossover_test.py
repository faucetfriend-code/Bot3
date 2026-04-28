"""
MA Crossover Strategy - Testing Multiple Parameters
================================================
"""

import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from pathlib import Path
from strategies import (
    compute_metrics,
    run_ma_crossover,
)
from strategies import INTERVAL_BARS_PER_YEAR

ROOT = Path("..")
DATA_DIR = ROOT / "trading_bot_v2" / "backtesting" / "data"


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


def test_params(df, cutoff, params, windows):
    all_sharpes = []
    all_returns = []
    all_trades = []
    
    for start, end in windows:
        df_test = df.loc[start:end]
        if len(df_test) < 100:
            continue
        
        eq, trd = run_ma_crossover(df_test, cutoff, **params)
        
        if len(trd) >= 3:
            m = compute_metrics(eq, trd, bars_per_year=INTERVAL_BARS_PER_YEAR["1h"])
            all_sharpes.append(m["sharpe"])
            all_returns.append(m["total_return_pct"])
            all_trades.extend(trd)
    
    if not all_returns:
        return None
    
    return {
        "sharpe": np.mean(all_sharpes),
        "return": np.mean(all_returns),
        "n_trades": len(all_trades),
    }


def main():
    print("Loading data...")
    df = load_data([2022, 2023])
    print(f"Data: {len(df)} bars")
    
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
    
    cutoff = 0.04
    
    # Test different MA periods
    param_sets = [
        {"fast_period": 20, "slow_period": 50, "pullback_max": 0.06},
        {"fast_period": 15, "slow_period": 40, "pullback_max": 0.08},
        {"fast_period": 10, "slow_period": 30, "pullback_max": 0.10},
        {"fast_period": 8, "slow_period": 21, "pullback_max": 0.10},
        {"fast_period": 12, "slow_period": 26, "pullback_max": 0.08},
        {"fast_period": 5, "slow_period": 15, "pullback_max": 0.12},
    ]
    
    results = []
    
    for params in param_sets:
        r = test_params(df, cutoff, params, windows)
        if r:
            results.append((params, r))
            print(f"{params}: Return={r['return']:+.1f}%, Sharpe={r['sharpe']:.2f}, Trades={r['n_trades']}")
        else:
            print(f"{params}: NO TRADES")
    
    if results:
        results.sort(key=lambda x: x[1]["return"], reverse=True)
        print("\n=== BEST ===")
        best_params, best_r = results[0]
        print(f"Return: {best_r['return']:+.1f}%, Sharpe: {best_r['sharpe']:.2f}")
        print(f"Params: {best_params}")


if __name__ == "__main__":
    main()