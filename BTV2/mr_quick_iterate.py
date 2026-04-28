"""
Mean Reversion - Quick Parameter Iteration
=====================================
Try different parameter combinations quickly.
"""

import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from pathlib import Path
from itertools import product
from mean_reversion_v2 import run_mean_reversion_v2
from strategies import compute_metrics, build_windows

ROOT = Path("..")
DATA_DIR = ROOT / "trading_bot_v2" / "backtesting" / "data"


def load_data(year=2023):
    """Load 5m data and aggregate to 1h."""
    path = DATA_DIR / f"BTC-USDC_5m_{year}.csv"
    if not path.exists():
        print(f"Data not found: {path}")
        return None
    
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    df = df.set_index("timestamp").sort_index()
    
    # Aggregate to 1h
    df_1h = df.resample("1h").agg({
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
    """Run strategy on multiple windows and return summary."""
    all_sharpes = []
    all_returns = []
    all_trades = []
    win_rates = []
    
    for start, end in windows:
        df_test = df.loc[start:end]
        if len(df_test) < 100:
            continue
            
        eq, trd = run_mean_reversion_v2(
            df_test, cutoff,
            **params
        )
        
        if len(trd) >= 3:
            m = compute_metrics(eq, trd)
            all_sharpes.append(m["sharpe"])
            all_returns.append(m["total_return_pct"])
            all_trades.append(len(trd))
            win_rates.append(m["win_rate_pct"])
    
    if not all_sharpes:
        return None
        
    return {
        "sharpe": np.mean(all_sharpes),
        "return": np.mean(all_returns),
        "win_rate": np.mean(win_rates),
        "n_trades": np.mean(all_trades),
        "n_folds": len(all_sharpes),
    }


def main():
    print("Loading data...")
    df = load_data(2023)
    if df is None:
        return
    
    # Test windows
    windows = [
        ("2023-01-01", "2023-03-31"),
        ("2023-04-01", "2023-06-30"),
        ("2023-07-01", "2023-09-30"),
        ("2023-10-01", "2023-12-31"),
    ]
    
    cutoff = 0.04
    
    # Test different parameter combinations
    # Focus on different RR ratios and ATR combinations
    param_sets = [
        # Original (from discoveries - 1:1 RR)
        {"rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02, 
         "atr_stop": 1.5, "atr_target": 1.5, "adx_max": 20},
        
        # Tighter stops for faster trades
        {"rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02, 
         "atr_stop": 1.0, "atr_target": 1.0, "adx_max": 20},
        
        # Wider target for bigger winners
        {"rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02, 
         "atr_stop": 1.5, "atr_target": 2.0, "adx_max": 20},
        
        # Tighter RSI (25/75)
        {"rsi_oversold": 25, "rsi_overbought": 75, "bb_proximity": 0.02, 
         "atr_stop": 1.5, "atr_target": 1.5, "adx_max": 20},
        
        # LOONG ONLY - RSI 30/70 (original settings without BB)
        {"rsi_oversold": 30, "rsi_overbought": 70, "bb_proximity": 0.02, 
         "atr_stop": 1.5, "atr_target": 1.5, "adx_max": 20},
        
        # Relaxed ADX
        {"rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02, 
         "atr_stop": 1.5, "atr_target": 1.5, "adx_max": 25},
        
        # Very tight stops - high RR
        {"rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02, 
         "atr_stop": 1.0, "atr_target": 2.0, "adx_max": 20},
         
        # Very tight stops - ultra tight target
        {"rsi_oversold": 20, "rsi_overbought": 80, "bb_proximity": 0.02, 
         "atr_stop": 0.8, "atr_target": 0.8, "adx_max": 20},
    ]
    
    results = []
    
    for i, params in enumerate(param_sets):
        print(f"\nTesting param set {i+1}: {params}")
        r = test_params(df, cutoff, params, windows)
        if r:
            print(f"  Sharpe: {r['sharpe']:.2f}, Return: {r['return']:.1f}%, WR: {r['win_rate']:.0f}%, Trades: {r['n_trades']:.0f}")
            results.append((params, r))
        else:
            print("  NO TRADES")
    
    # Sort by return
    results.sort(key=lambda x: x[1]["return"], reverse=True)
    
    print("\n=== RANKED BY RETURN ===")
    for params, r in results:
        print(f"Return: {r['return']:+.1f}%, Sharpe: {r['sharpe']:.2f}, WR: {r['win_rate']:.0f}%")
        print(f"  {params}")


if __name__ == "__main__":
    main()