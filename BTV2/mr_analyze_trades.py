"""
Analyze trade distribution for Mean Reversion
"""

import sys
sys.path.insert(0, ".")

import numpy as np
import pandas as pd
from pathlib import Path
from mean_reversion_v2 import run_mean_reversion_v2
from strategies import compute_metrics

ROOT = Path("..")
DATA_DIR = ROOT / "trading_bot_v2" / "backtesting" / "data"


def load_data(year=2023):
    path = DATA_DIR / f"BTC-USDC_5m_{year}.csv"
    if not path.exists():
        return None
    
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df.columns = [c.lower() for c in df.columns]
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    df = df.set_index("timestamp").sort_index()
    
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


def main():
    df = load_data(2023)
    
    # Best params
    params = {
        "rsi_oversold": 20, 
        "rsi_overbought": 80, 
        "bb_proximity": 0.02, 
        "atr_stop": 1.5, 
        "atr_target": 2.0,  # Wider target 
        "adx_max": 20,
    }
    
    cutoff = 0.04
    
    # Test on full year
    df_test = df.loc["2023-01-01":"2023-12-31"]
    eq, trades = run_mean_reversion_v2(df_test, cutoff, **params)
    
    print(f"Total trades: {len(trades)}")
    print(f"\nTrade distribution:")
    
    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    
    print(f"Wins: {len(wins)} ({100*len(wins)/len(trades):.1f}%)")
    print(f"Losses: {len(losses)} ({100*len(losses)/len(trades):.1f}%)")
    
    if wins:
        print(f"\nAvg win: {np.mean(wins)*100:.2f}%")
    if losses:
        print(f"Avg loss: {np.mean(losses)*100:.2f}%")
    
    print(f"\nExpected win: {np.mean(wins)*len(wins)/len(trades)*100:.2f}%")
    print(f"Expected loss: {np.mean(losses)*len(losses)/len(trades)*100:.2f}%")
    
    # Sort trades by return
    trades_sorted = sorted(trades)
    print(f"\nSmallest 5 losses: {[f'{t*100:.2f}%' for t in trades_sorted[:5]]}")
    print(f"Largest 5 wins: {[f'{t*100:.2f}%' for t in trades_sorted[-5:]]}")
    
    # Check the RR
    if wins and losses:
        avg_win = np.mean(wins)
        avg_loss = abs(np.mean(losses))
        rr = avg_win / avg_loss
        print(f"\nActual RR: {rr:.2f}:1 (target was 1.33:1 for atr_target=2.0 / atr_stop=1.5)")
        print(f"Win rate needed for breakeven: {1/(1+rr)*100:.1f}%")
    
    # Calculate P/L with costs
    total_cost = 0.0015 * 2 * len(trades)  # 0.15% per side * 2 * n trades
    print(f"\nTotal costs paid: {total_cost*100:.2f}%")
    
    gross_pnl = sum(trades)
    net_pnl = gross_pnl - total_cost
    print(f"Gross PnL: {gross_pnl*100:.2f}%")
    print(f"Net PnL: {net_pnl*100:.2f}%")
    
    # Full metrics
    m = compute_metrics(eq, trades)
    print(f"\n=== METRICS ===")
    print(f"Sharpe: {m['sharpe']:.2f}")
    print(f"Return: {m['total_return_pct']:.2f}%")
    print(f"Max DD: {m['max_dd_pct']:.2f}%")
    print(f"Win rate: {m['win_rate_pct']:.1f}%")


if __name__ == "__main__":
    main()