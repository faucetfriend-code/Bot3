#!/usr/bin/env python3
"""
VWAP Scalping Strategy - Test different entry modes
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


def run_year_test(df_5m, year, params):
    start_date = f"{year}-01-01"
    end_date = f"{year + 1}-01-01" if year < 2025 else "2026-01-01"
    
    df_year = df_5m[(df_5m.index >= start_date) & (df_5m.index < end_date)]
    
    if len(df_year) < 100:
        return None
    
    equity, trades = run_vwap_scalping(df_year, cutoff=0.10, **params)
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
        net_return = 0
    
    return {
        'year': year,
        'trades': len(trades),
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'net_return': net_return,
    }


def test_config(df_5m, name, params, years):
    print(f"\n{name}")
    print("-" * 50)
    
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
    
    # Test 1: deviation mode - simpler entry
    config_deviation = {
        "name": "deviation mode",
        "params": {
            "entry_mode": "deviation",
            "sd_threshold": 2.0,
            "adx_max": 25.0,
            "rsi_max": 50.0,
            "volume_mult": 1.5,
            "atr_stop": 0.5,
            "atr_target": 3.0,
            "use_htf_vwap": False,
            "use_htf_ema": False,
        }
    }
    
    # Test 2: momentum mode
    config_momentum = {
        "name": "momentum mode",
        "params": {
            "entry_mode": "momentum",
            "sd_threshold": 2.0,
            "adx_max": 25.0,
            "rsi_max": 50.0,
            "volume_mult": 1.5,
            "atr_stop": 0.5,
            "atr_target": 3.0,
            "use_htf_vwap": False,
            "use_htf_ema": False,
        }
    }
    
    # Test 3: cross mode
    config_cross = {
        "name": "cross mode",
        "params": {
            "entry_mode": "cross",
            "sd_threshold": 2.0,
            "adx_max": 25.0,
            "rsi_max": 50.0,
            "volume_mult": 1.5,
            "atr_stop": 0.5,
            "atr_target": 3.0,
            "use_htf_vwap": False,
            "use_htf_ema": False,
        }
    }
    
    # Test 4: mean_reversion with relaxed settings
    config_mr = {
        "name": "mean_reversion (relaxed)",
        "params": {
            "entry_mode": "mean_reversion",
            "sd_threshold": 1.8,
            "adx_max": 25.0,
            "rsi_max": 50.0,
            "volume_mult": 1.5,
            "atr_stop": 0.5,
            "atr_target": 2.5,
            "use_htf_vwap": False,
            "use_htf_ema": False,
        }
    }
    
    # Test 5: bull_pullback with VERY relaxed settings
    config_bull_relaxed = {
        "name": "bull_pullback (very relaxed)",
        "params": {
            "entry_mode": "bull_pullback",
            "sd_threshold": 1.5,
            "pullback_bars": 2,
            "adx_max": 30.0,
            "rsi_max": 55.0,
            "stoch_oversold": 25,
            "stoch_overbought": 75,
            "volume_mult": 1.0,
            "atr_stop": 0.4,
            "atr_target": 2.0,
            "use_htf_vwap": False,
            "use_htf_ema": False,
        }
    }
    
    configs = [config_deviation, config_momentum, config_cross, config_mr, config_bull_relaxed]
    
    all_results = []
    for cfg in configs:
        results = test_config(df_5m, cfg["name"], cfg["params"], years)
        all_results.append((cfg["name"], results))
    
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    
    for name, results in all_results:
        total = sum(r['trades'] for r in results) if results else 0
        net = sum(r['net_return'] for r in results) if results else 0
        wr = np.mean([r['win_rate'] for r in results]) if results else 0
        print(f"{name:<30}: {total:>4} trades, Net: {net:>+8.2f}%, WR: {wr:>5.1f}%")


if __name__ == "__main__":
    main()
