#!/usr/bin/env python3
"""
VWAP Scalping MTF Backtest - Updated Strategy
Run backtest with updated MTF (Multi-Timeframe) parameters

Strategy Configuration:
- Timeframe: 5m (native)
- Entry Mode: mean_reversion (fade SD deviations from VWAP)
- MTF Filters: 
  - 15m VWAP alignment gate (resampled from 5m)
  - 1h EMA(9/21) + ADX filter (resampled from 5m)
- R:R: 3.8:1 (atr_target = 3.8 × ATR)
- Stop: 0.7 × ATR
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

# Add BTV2 to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_manager import get_candles
from strategies import (
    run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR
)
from datetime import datetime, timezone

# Configuration
STORAGE_ROOT = Path("G:/Candle Data")

def load_parquet(symbol, interval):
    """Load data from parquet cache."""
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        return df
    return None

# Load 5m data for 2023-2024
print("Loading BTCUSDT 5m data...")
df_5m = load_parquet("BTCUSDT", "5m")
if df_5m is None:
    print("5m data not found, downloading...")
    df_5m = get_candles(
        "BTCUSDT", "5m",
        datetime(2023, 1, 1, tzinfo=timezone.utc),
        datetime(2025, 1, 1, tzinfo=timezone.utc)
    )
    df_5m.columns = [c.capitalize() for c in df_5m.columns]
else:
    df_5m = df_5m[(df_5m.index >= '2023-01-01') & (df_5m.index < '2025-01-01')]

print(f"Loaded {len(df_5m)} bars (2023-2024)")
print()

# Load 1m exit data
print("Loading BTCUSDT 1m exit data...")
df_1m = load_parquet("BTCUSDT", "1m")
if df_1m is None:
    print("1m data not found, downloading...")
    df_1m = get_candles(
        "BTCUSDT", "1m",
        datetime(2023, 1, 1, tzinfo=timezone.utc),
        datetime(2025, 1, 1, tzinfo=timezone.utc)
    )
    df_1m.columns = [c.capitalize() for c in df_1m.columns]
else:
    df_1m = df_1m[(df_1m.index >= '2023-01-01') & (df_1m.index < '2025-01-01')]

print(f"Loaded {len(df_1m)} bars for exit resolution")
print()

# MTF Parameters as specified
params = {
    "entry_mode": "mean_reversion",
    "sd_threshold": 2.5,
    "adx_max": 20.0,
    "rsi_max": 40.0,
    "volume_mult": 2.0,
    "atr_stop": 0.7,
    "atr_target": 3.8,
    "use_htf_vwap": True,
    "use_htf_ema": True,
    "htf_adx_max": 25.0,
    "df_exit": df_1m,
}

print("=" * 80)
print("VWAP SCALPING MTF BACKTEST - 2023-2024")
print("=" * 80)
print()
print("Parameters:")
for k, v in params.items():
    if k != "df_exit":
        print(f"  {k}: {v}")
print()

# Run backtest
print("Running backtest...")
equity, trades = run_vwap_scalping(df_5m, cutoff=0.10, **params)

# Compute metrics
bars_per_year = INTERVAL_BARS_PER_YEAR["5m"]
metrics = compute_metrics(equity, trades, bars_per_year=bars_per_year)

# Calculate additional metrics
if trades:
    wins = sum(1 for t in trades if t > 0)
    losses = len(trades) - wins
    win_rate = wins / len(trades) * 100
    gross_profit = sum(t for t in trades if t > 0)
    gross_loss = abs(sum(t for t in trades if t < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
    
    # Costs calculation (0.15% per side = 0.30% round trip)
    costs = len(trades) * 0.30
    net_return = metrics['total_return_pct'] - costs
else:
    win_rate = 0
    profit_factor = 0
    costs = 0
    net_return = 0

print()
print("RESULTS:")
print("-" * 40)
print(f"Total Return:     {metrics['total_return_pct']:+.2f}%")
print(f"Net Return:       {net_return:+.2f}% (after costs)")
print(f"CAGR:             {metrics['cagr_pct']:+.2f}%")
print(f"Sharpe Ratio:     {metrics['sharpe']:.2f}")
print(f"Max Drawdown:    {metrics['max_dd_pct']:.2f}%")
print(f"Win Rate:         {win_rate:.1f}%")
print(f"Profit Factor:   {profit_factor:.2f}")
print(f"Total Trades:     {len(trades)}")
print(f"Trade Costs:      {costs:.2f}%")
print()

# Compare to expected improvements
print("=" * 80)
print("COMPARISON TO EXPECTED IMPROVEMENTS (from guide):")
print("-" * 40)
print(f"Win Rate:     33-41% -> {win_rate:.1f}% (target: 44-57%)")
print(f"Profit Factor: 1.0-1.2 -> {profit_factor:.2f} (target: 1.2-1.6)")
print(f"Trade count: drops 35-65% but still 1,000-3,000/yr")
print(f"Actual trades: {len(trades)}")
print("=" * 80)
