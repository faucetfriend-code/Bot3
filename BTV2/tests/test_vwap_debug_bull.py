#!/usr/bin/env python3
"""
VWAP Scalping Strategy - Debug bull_pullback trades
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR, COST_PER_SIDE
)

STORAGE_ROOT = Path("G:/Candle Data")


def load_parquet(symbol, interval):
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        return df
    return None


def run_year_test(df_5m, year, params, df_exit=None):
    start_date = f"{year}-01-01"
    end_date = f"{year + 1}-01-01" if year < 2025 else "2026-01-01"
    
    if year == 2025:
        end_date = "2026-01-01"
    
    df_year = df_5m[(df_5m.index >= start_date) & (df_5m.index < end_date)]
    
    if len(df_year) < 100:
        return None
    
    equity, trades = run_vwap_scalping(df_year, cutoff=0.10, df_exit=df_exit, **params)
    bars_per_year = INTERVAL_BARS_PER_YEAR["5m"]
    metrics = compute_metrics(equity, trades, bars_per_year=bars_per_year)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        costs = len(trades) * 2 * COST_PER_SIDE * 100
        net_return = metrics['total_return_pct'] - costs
    else:
        win_rate = 0
        profit_factor = 0
        costs = 0
        net_return = 0
    
    return {
        'year': year,
        'trades': len(trades),
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'net_return': net_return,
    }


def test_config(df_5m, name, params, years):
    print(f"\n{'='*60}")
    print(f"CONFIG: {name}")
    print(f"{'='*60}")
    
    results = []
    for year in years:
        result = run_year_test(df_5m, year, params)
        if result:
            results.append(result)
    
    for r in results:
        print(f"  {r['year']}: {r['trades']} trades, WR={r['win_rate']:.1f}%, PF={r['profit_factor']:.2f}, Net={r['net_return']:+.2f}%")
    
    return results


def main():
    print("Loading BTCUSDT 5m data...")
    df_5m = load_parquet("BTCUSDT", "5m")
    print(f"Total bars: {len(df_5m)}")
    
    years = [2023, 2024]
    
    # Test 1: No HTF filters at all
    config1 = {
        "name": "No HTF filters (baseline)",
        "params": {
            "entry_mode": "bull_pullback",
            "sd_threshold": 2.0,
            "adx_max": 20.0,
            "rsi_max": 45.0,
            "volume_mult": 1.5,
            "atr_stop": 0.5,
            "atr_target": 3.0,
            "use_htf_vwap": False,
            "use_htf_ema": False,
        }
    }
    
    # Test 2: Only 15m VWAP, no EMA
    config2 = {
        "name": "15m VWAP only (no 1h EMA)",
        "params": {
            "entry_mode": "bull_pullback",
            "sd_threshold": 2.0,
            "adx_max": 20.0,
            "rsi_max": 45.0,
            "volume_mult": 1.5,
            "atr_stop": 0.5,
            "atr_target": 3.0,
            "use_htf_vwap": True,
            "use_htf_ema": False,
            "htf_vwap_interval": "15min",
        }
    }
    
    # Test 3: Both HTF but looser ADX
    config3 = {
        "name": "Both HTF, looser ADX=30",
        "params": {
            "entry_mode": "bull_pullback",
            "sd_threshold": 2.0,
            "adx_max": 30.0,
            "rsi_max": 50.0,
            "volume_mult": 1.5,
            "atr_stop": 0.5,
            "atr_target": 3.0,
            "use_htf_vwap": True,
            "use_htf_ema": True,
            "htf_adx_max": 35.0,
            "htf_vwap_interval": "15min",
            "htf_ema_interval": "60min",
        }
    }
    
    # Test 4: No volume filter
    config4 = {
        "name": "No volume filter",
        "params": {
            "entry_mode": "bull_pullback",
            "sd_threshold": 2.0,
            "adx_max": 30.0,
            "rsi_max": 50.0,
            "volume_mult": 0.0,  # Disabled
            "atr_stop": 0.5,
            "atr_target": 3.0,
            "use_htf_vwap": True,
            "use_htf_ema": True,
            "htf_adx_max": 35.0,
            "htf_vwap_interval": "15min",
            "htf_ema_interval": "60min",
        }
    }
    
    # Test 5: Remove Stochastic + RSI divergence requirement (they're too strict)
    # Modify the source to make them optional? Let's try mean_reversion instead
    
    configs = [config1, config2, config3, config4]
    
    for cfg in configs:
        test_config(df_5m, cfg["name"], cfg["params"], years)
    
    print("\n" + "="*60)
    print("ANALYSIS: bull_pullback mode seems to require very specific market")
    print("conditions (1h uptrend + higher high + extreme VWAP deviation +")
    print("Stochastic oversold + RSI divergence). This rarely occurs.")
    print("="*60)


if __name__ == "__main__":
    main()
