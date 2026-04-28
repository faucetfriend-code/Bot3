"""
Momentum Scalping Strategy - 4-Layer Validation
============================================
Fast EMA crossover scalping + MACD + RSI + Volume
"""

import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from pathlib import Path
from strategies import (
    compute_metrics,
    run_momentum_scalping,
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


def test_params(df, cutoff, windows, param_sets):
    results = []
    
    for params in param_sets:
        all_returns = []
        all_trades = []
        all_sharpes = []
        
        for start, end in windows:
            df_test = df.loc[start:end]
            if len(df_test) < 100:
                continue
            
            eq, trd = run_momentum_scalping(df_test, cutoff, **params)
            
            if len(trd) >= 3:
                m = compute_metrics(eq, trd, bars_per_year=INTERVAL_BARS_PER_YEAR["1h"])
                all_returns.append(m["total_return_pct"])
                all_sharpes.append(m["sharpe"])
                all_trades.extend(trd)
        
        if all_returns:
            results.append((params, {
                "return": np.mean(all_returns),
                "sharpe": np.mean(all_sharpes),
                "n_trades": len(all_trades),
                "trades": all_trades,
            }))
    
    return results


def main():
    print("Loading data...")
    df = load_data([2022, 2023])
    print(f"Data: {len(df)} bars")
    
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
    
    # Test different EMA periods
    param_sets = [
        # v1: Base (9/21)
        {"ema_fast": 9, "ema_slow": 21, "atr_stop": 2.0, "atr_target": 3.0},
        # v2: Faster (5/15)
        {"ema_fast": 5, "ema_slow": 15, "atr_stop": 2.0, "atr_target": 3.0},
        # v3: Slower (12/26)
        {"ema_fast": 12, "ema_slow": 26, "atr_stop": 2.0, "atr_target": 3.0},
        # v4: Very fast (3/9)
        {"ema_fast": 3, "ema_slow": 9, "atr_stop": 2.0, "atr_target": 3.0},
        # v5: Tighter stop
        {"ema_fast": 9, "ema_slow": 21, "atr_stop": 1.5, "atr_target": 3.0},
        # v6: Wider target
        {"ema_fast": 9, "ema_slow": 21, "atr_stop": 2.0, "atr_target": 4.0},
    ]
    
    results = test_params(df, cutoff, windows, param_sets)
    
    for params, r in results:
        print(f"{params}")
        print(f"  Return: {r['return']:+.1f}%, Sharpe: {r['sharpe']:.2f}, Trades: {r['n_trades']}")
    
    if results:
        results.sort(key=lambda x: x[1]["return"], reverse=True)
        print("\n=== BEST ===")
        best_params, best_r = results[0]
        print(f"Return: {best_r['return']:+.1f}%, Sharpe: {best_r['sharpe']:.2f}")
        print(f"Params: {best_params}")


if __name__ == "__main__":
    main()