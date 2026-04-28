"""
Grid Strategy - Quick Test
==========================
"""

import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from pathlib import Path
from strategies import (
    compute_metrics,
    run_grid_trading,
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


def test_grid(df, cutoff, windows, param_sets):
    results = []
    
    for params in param_sets:
        all_trades = []
        
        for start, end in windows:
            df_test = df.loc[start:end]
            if len(df_test) < 100:
                continue
            
            eq, trd = run_grid_trading(df_test, cutoff, **params)
            all_trades.extend(trd)
        
        if all_trades:
            mean = np.mean(all_trades)
            std = np.std(all_trades)
            sharpe = mean / std if std > 0 else 0
            
            results.append((params, {
                "return": sum(all_trades),
                "sharpe": sharpe,
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
    
    param_sets = [
        {"adx_threshold": 20.0, "spacing_mult": 0.65},
        {"adx_threshold": 15.0, "spacing_mult": 0.65},
        {"adx_threshold": 25.0, "spacing_mult": 0.65},
        {"adx_threshold": 20.0, "spacing_mult": 0.50},
        {"adx_threshold": 20.0, "spacing_mult": 0.80},
    ]
    
    results = test_grid(df, cutoff, windows, param_sets)
    
    for params, r in results:
        print(f"{params}")
        print(f"  Return: {r['return']*100:+.1f}%, Sharpe: {r['sharpe']:.2f}, Trades: {r['n_trades']}")
    
    if results:
        results.sort(key=lambda x: x[1]["return"], reverse=True)
        print("\n=== BEST ===")
        best_params, best_r = results[0]
        print(f"Return: {best_r['return']*100:+.1f}%, Sharpe: {best_r['sharpe']:.2f}")


if __name__ == "__main__":
    main()