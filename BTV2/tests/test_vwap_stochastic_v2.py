#!/usr/bin/env python3
"""
VWAP Scalping Strategy - NEW Stochastic Indicator Test
=========================================================
Testing the NEW bull_pullback mode with Stochastic filter with relaxed settings.

Goals:
- Win rate >50%
- Positive net return
- Trade count: 100-500/year
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


def test_config(df_5m, name, params, years, df_exit=None):
    print(f"\n{name}")
    print("-" * 60)
    
    results = []
    for year in years:
        result = run_year_test(df_5m, year, params, df_exit)
        if result:
            results.append(result)
    
    for r in results:
        print(f"  {r['year']}: {r['trades']} trades, WR={r['win_rate']:.1f}%, PF={r['profit_factor']:.2f}, Net={r['net_return']:+.2f}%")
    
    return results


def main():
    print("Loading BTCUSDT 5m data...")
    df_5m = load_parquet("BTCUSDT", "5m")
    print(f"Total bars: {len(df_5m)}")
    
    print("\nLoading BTCUSDT 1m data...")
    df_1m = load_parquet("BTCUSDT", "1m")
    if df_1m is not None:
        print(f"  1m bars: {len(df_1m)}")
    
    years = [2023, 2024]
    
    # Test 1: User's exact config
    config1 = {
        "name": "User Config (strict)",
        "params": {
            "entry_mode": "bull_pullback",
            "sd_threshold": 2.5,
            "pullback_bars": 3,
            "adx_max": 20.0,
            "rsi_max": 40.0,
            "stoch_oversold": 25,
            "stoch_overbought": 75,
            "volume_mult": 2.0,
            "atr_stop": 0.7,
            "atr_target": 3.8,
            "use_htf_vwap": True,
            "use_htf_ema": True,
            "htf_adx_max": 25.0,
            "htf_vwap_interval": "15min",
            "htf_ema_interval": "60min",
        }
    }
    
    # Test 2: Relaxed HTF settings
    config2 = {
        "name": "Relaxed HTF (ADX 35)",
        "params": {
            "entry_mode": "bull_pullback",
            "sd_threshold": 2.0,
            "pullback_bars": 2,
            "adx_max": 25.0,
            "rsi_max": 50.0,
            "stoch_oversold": 30,
            "stoch_overbought": 70,
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
    
    # Test 3: No HTF filters
    config3 = {
        "name": "No HTF filters",
        "params": {
            "entry_mode": "bull_pullback",
            "sd_threshold": 2.0,
            "pullback_bars": 2,
            "adx_max": 25.0,
            "rsi_max": 50.0,
            "stoch_oversold": 30,
            "stoch_overbought": 70,
            "volume_mult": 1.5,
            "atr_stop": 0.5,
            "atr_target": 3.0,
            "use_htf_vwap": False,
            "use_htf_ema": False,
        }
    }
    
    # Test 4: Only 15m VWAP (no 1h EMA)
    config4 = {
        "name": "15m VWAP only (no 1h EMA)",
        "params": {
            "entry_mode": "bull_pullback",
            "sd_threshold": 2.0,
            "pullback_bars": 2,
            "adx_max": 25.0,
            "rsi_max": 50.0,
            "stoch_oversold": 30,
            "stoch_overbought": 70,
            "volume_mult": 1.5,
            "atr_stop": 0.5,
            "atr_target": 3.0,
            "use_htf_vwap": True,
            "use_htf_ema": False,
            "htf_vwap_interval": "15min",
        }
    }
    
    # Test 5: User config but with Stochastic disabled
    config5 = {
        "name": "No Stochastic filter",
        "params": {
            "entry_mode": "bull_pullback",
            "sd_threshold": 2.0,
            "pullback_bars": 2,
            "adx_max": 25.0,
            "rsi_max": 50.0,
            "stoch_oversold": 50,  # Disabled - will always pass
            "stoch_overbought": 50,
            "volume_mult": 1.5,
            "atr_stop": 0.5,
            "atr_target": 3.0,
            "use_htf_vwap": False,
            "use_htf_ema": False,
        }
    }
    
    configs = [config1, config2, config3, config4, config5]
    
    all_results = []
    for cfg in configs:
        results = test_config(df_5m, cfg["name"], cfg["params"], years, df_1m)
        all_results.append((cfg["name"], results))
    
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print(f"{'Config':<35} | {'Trades':>8} | {'Net%':>10} | {'WR%':>8}")
    print("-"*70)
    
    for name, results in all_results:
        total = sum(r['trades'] for r in results) if results else 0
        net = sum(r['net_return'] for r in results) if results else 0
        wr = np.mean([r['win_rate'] for r in results]) if results else 0
        print(f"{name:<35} | {total:>8} | {net:>+9.2f} | {wr:>7.1f}%")
    
    print("\n" + "="*70)
    print("NOTE: The bull_pullback mode with Stochastic requires specific market")
    print("conditions that rarely occur in practice. The filter combination is very strict.")
    print("="*70)


if __name__ == "__main__":
    main()
