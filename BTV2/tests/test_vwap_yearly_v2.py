#!/usr/bin/env python3
"""
VWAP Scalping Strategy - Yearly Breakdown Test V2
==================================================
Testing multiple parameter configurations to find profitable years.
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
    """Load parquet data and normalize column names."""
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        return df
    return None


def run_year_test(df_5m, year, params):
    """Run VWAP scalping test for a specific year."""
    start_date = f"{year}-01-01"
    end_date = f"{year + 1}-01-01" if year < 2025 else "2026-01-01"
    
    # For 2025, use partial year
    if year == 2025:
        end_date = "2026-01-01"
    
    df_year = df_5m[(df_5m.index >= start_date) & (df_5m.index < end_date)]
    
    if len(df_year) < 100:
        return None
    
    # Run the strategy
    equity, trades = run_vwap_scalping(df_year, cutoff=0.10, **params)
    bars_per_year = INTERVAL_BARS_PER_YEAR["5m"]
    metrics = compute_metrics(equity, trades, bars_per_year=bars_per_year)
    
    # Calculate additional metrics
    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Transaction costs (entry + exit = 2 * COST_PER_SIDE per trade)
        costs = len(trades) * 2 * COST_PER_SIDE * 100  # As percentage
        net_return = metrics['total_return_pct'] - costs
    else:
        win_rate = 0
        profit_factor = 0
        costs = 0
        net_return = 0
    
    return {
        'year': year,
        'bars': len(df_year),
        'trades': len(trades),
        'win_rate': win_rate,
        'profit_factor': profit_factor,
        'gross_return': metrics['total_return_pct'],
        'net_return': net_return,
        'costs': costs,
        'sharpe': metrics['sharpe'],
        'max_dd': metrics['max_dd_pct'],
    }


def test_config(df_5m, config_name, params, years):
    """Test a configuration across specified years."""
    print(f"\n{'='*80}")
    print(f"CONFIG: {config_name}")
    print(f"{'='*80}")
    
    results = []
    for year in years:
        result = run_year_test(df_5m, year, params)
        if result:
            results.append(result)
    
    # Summary
    print(f"\n{'Year':>6} | {'Trades':>7} | {'WR%':>7} | {'PF':>7} | {'Gross%':>10} | {'Net%':>10}")
    print("-"*60)
    
    for r in results:
        print(f"{r['year']:>6} | {r['trades']:>7} | {r['win_rate']:>6.1f}% | {r['profit_factor']:>7.2f} | {r['gross_return']:>+10.2f} | {r['net_return']:>+10.2f}")
    
    # Profitable years
    profitable = [r for r in results if r['net_return'] > 0]
    acceptable = [r for r in results if r['win_rate'] >= 45]
    
    print(f"\nProfitable: {len(profitable)}/{len(results)} years")
    print(f"Acceptable WR (>=45%): {len(acceptable)}/{len(results)} years")
    
    return results


def main():
    # Load 5m data
    print("Loading BTCUSDT 5m data...")
    df_5m = load_parquet("BTCUSDT", "5m")
    print(f"Total bars: {len(df_5m)}")
    print(f"Date range: {df_5m.index[0].date()} to {df_5m.index[-1].date()}")
    
    years = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]
    
    # Config 1: Original higher MTF (user specified)
    config1 = {
        "name": "Higher MTF (1h VWAP + 4h EMA/ADX)",
        "params": {
            "entry_mode": "mean_reversion",
            "sd_threshold": 2.0,
            "adx_max": 20.0,
            "rsi_max": 40.0,
            "volume_mult": 2.0,
            "atr_stop": 0.6,
            "atr_target": 3.5,
            "use_htf_vwap": True,
            "use_htf_ema": True,
            "htf_adx_max": 35.0,
            "htf_vwap_interval": "60min",
            "htf_ema_interval": "240min",
        }
    }
    
    # Config 2: No higher timeframe filters
    config2 = {
        "name": "No HTF Filters (Baseline)",
        "params": {
            "entry_mode": "mean_reversion",
            "sd_threshold": 2.0,
            "adx_max": 20.0,
            "rsi_max": 40.0,
            "volume_mult": 2.0,
            "atr_stop": 0.6,
            "atr_target": 3.5,
            "use_htf_vwap": False,
            "use_htf_ema": False,
            "htf_adx_max": 25.0,
        }
    }
    
    # Config 3: Only 15m VWAP filter (no 4h EMA)
    config3 = {
        "name": "15m VWAP only (no 4h EMA)",
        "params": {
            "entry_mode": "mean_reversion",
            "sd_threshold": 2.0,
            "adx_max": 20.0,
            "rsi_max": 40.0,
            "volume_mult": 2.0,
            "atr_stop": 0.6,
            "atr_target": 3.5,
            "use_htf_vwap": True,
            "use_htf_ema": False,
            "htf_adx_max": 25.0,
            "htf_vwap_interval": "15min",
        }
    }
    
    # Config 4: Bull pullback mode with HTF
    config4 = {
        "name": "Bull Pullback + HTF Filters",
        "params": {
            "entry_mode": "bull_pullback",
            "sd_threshold": 2.0,
            "adx_max": 20.0,
            "rsi_max": 40.0,
            "volume_mult": 2.0,
            "atr_stop": 0.6,
            "atr_target": 3.5,
            "use_htf_vwap": True,
            "use_htf_ema": True,
            "htf_adx_max": 35.0,
            "htf_vwap_interval": "60min",
            "htf_ema_interval": "240min",
        }
    }
    
    # Config 5: Looser filters
    config5 = {
        "name": "Looser Filters (ADX 25, RSI 45)",
        "params": {
            "entry_mode": "mean_reversion",
            "sd_threshold": 1.8,
            "adx_max": 25.0,
            "rsi_max": 45.0,
            "volume_mult": 1.8,
            "atr_stop": 0.5,
            "atr_target": 3.0,
            "use_htf_vwap": True,
            "use_htf_ema": True,
            "htf_adx_max": 40.0,
            "htf_vwap_interval": "60min",
            "htf_ema_interval": "240min",
        }
    }
    
    # Config 6: Momentum mode
    config6 = {
        "name": "Momentum Mode + HTF",
        "params": {
            "entry_mode": "momentum",
            "sd_threshold": 2.0,
            "adx_max": 20.0,
            "rsi_max": 40.0,
            "volume_mult": 2.0,
            "atr_stop": 0.6,
            "atr_target": 3.5,
            "use_htf_vwap": True,
            "use_htf_ema": True,
            "htf_adx_max": 35.0,
            "htf_vwap_interval": "60min",
            "htf_ema_interval": "240min",
        }
    }
    
    configs = [config1, config2, config3, config4, config5, config6]
    
    all_results = {}
    for cfg in configs:
        results = test_config(df_5m, cfg["name"], cfg["params"], years)
        all_results[cfg["name"]] = results
    
    # Final summary
    print("\n" + "="*80)
    print("FINAL COMPARISON - Best Net Returns by Year")
    print("="*80)
    
    print(f"\n{'Year':>6} | {'Config1':>25} | {'Config2':>25} | {'Config3':>25} | {'Config4':>25} | {'Config5':>25} | {'Config6':>25}")
    print("-"*180)
    
    for i, year in enumerate(years):
        row = f"{year:>6} |"
        for cfg in configs:
            r = all_results[cfg["name"]][i] if i < len(all_results[cfg["name"]]) else None
            if r:
                row += f" {r['net_return']:>+23.1f}% |"
            else:
                row += f" {'N/A':>25} |"
        print(row)
    
    # Find best config overall
    print("\n" + "="*80)
    print("BEST CONFIGURATIONS")
    print("="*80)
    
    for cfg in configs:
        results = all_results[cfg["name"]]
        total_net = sum(r['net_return'] for r in results)
        avg_wr = np.mean([r['win_rate'] for r in results])
        profitable_years = len([r for r in results if r['net_return'] > 0])
        print(f"\n{cfg['name']}:")
        print(f"  Total Net Return: {total_net:+.1f}%")
        print(f"  Average Win Rate: {avg_wr:.1f}%")
        print(f"  Profitable Years: {profitable_years}/{len(results)}")
    
    print("\n" + "="*80)
    print("TEST COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()
