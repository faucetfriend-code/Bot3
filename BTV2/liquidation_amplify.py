"""
Liquidation Amplification Tests
=============================
1. Find bigger price move thresholds
2. Test position scaling (simulated)
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


def analyze_trades(trades):
    if not trades:
        return {"return": 0, "trades": []}
    total = sum(trades)
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    return {
        "return": total,
        "n_trades": len(trades),
        "win_rate": 100 * len(wins) / len(trades) if trades else 0,
        "avg_win": np.mean(wins) if wins else 0,
        "trades": trades,
    }


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
    
    # Test different thresholds
    print("\n=== TESTING DIFFERENT PRICE THRESHOLDS ===")
    thresholds = [0.030, 0.035, 0.040, 0.050, 0.060, 0.080, 0.100]
    
    best_result = None
    best_threshold = None
    
    for thresh in thresholds:
        params = {
            "price_threshold": thresh,
            "volume_mult": 3.0,
            "rsi_threshold": 15.0,
        }
        
        all_trades = []
        for start, end in windows:
            df_test = df.loc[start:end]
            if len(df_test) < 100:
                continue
            eq, trd = run_liquidation_capture(df_test, cutoff, **params)
            all_trades.extend(trd)
        
        if all_trades:
            stats = analyze_trades(all_trades)
            print(f"Threshold {thresh*100:.0f}%: {len(all_trades)} trades, Return: {stats['return']*100:+.1f}%, WR: {stats['win_rate']:.0f}%")
            print(f"  Individual: {[f'{t*100:.1f}%' for t in all_trades]}")
            
            if best_result is None or stats['return'] > best_result['return']:
                best_result = stats
                best_threshold = thresh
    
    print(f"\n=== BEST: Threshold {best_threshold*100:.0f}% ===")
    print(f"Return: {best_result['return']*100:+.1f}%")
    print(f"Trades: {best_result['n_trades']}")
    
    # Now test position scaling on best threshold
    print("\n=== POSITION SCALING SIMULATION ===")
    target_trades = best_result['trades']
    
    for pos_mult in [1, 2, 3, 5, 10]:
        scaled = [t * pos_mult for t in target_trades]
        total = sum(scaled)
        print(f"{pos_mult}x position: Return = {total*100:+.1f}%")
        
        # Monte Carlo
        if len(scaled) >= 2:
            mc = []
            for _ in range(200):
                sample = np.random.choice(scaled, size=len(scaled), replace=True)
                mc.append(sum(sample))
            p_loss = np.mean(np.array(mc) < 0) * 100
            print(f"  Monte Carlo P(Loss): {p_loss:.1f}%")


if __name__ == "__main__":
    main()