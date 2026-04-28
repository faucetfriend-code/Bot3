#!/usr/bin/env python3
"""
VWAP Scalping Strategy - Yearly Breakdown Test
================================================
Tests the higher MTF (1h/4h) configuration across each year from 2018 to 2025.
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
    
    print(f"\n  Testing {year}: {len(df_year)} bars ({start_date} to {end_date})")
    
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


def main():
    # Load 5m data
    print("Loading BTCUSDT 5m data...")
    df_5m = load_parquet("BTCUSDT", "5m")
    print(f"Total bars: {len(df_5m)}")
    print(f"Date range: {df_5m.index[0].date()} to {df_5m.index[-1].date()}")
    
    # Higher MTF Configuration (1h VWAP, 4h EMA/ADX)
    # Using parameters from user's range (mid-point)
    params = {
        "entry_mode": "mean_reversion",
        "sd_threshold": 2.0,        # User: 2.0-2.5
        "adx_max": 20.0,            # User: 18-20
        "rsi_max": 40.0,            # User: 35-40
        "volume_mult": 2.0,         # User: 2.0-2.5
        "atr_stop": 0.6,            # User: 0.5-0.7
        "atr_target": 3.5,          # User: 3.0-4.0
        "use_htf_vwap": True,       # 1h VWAP
        "use_htf_ema": True,        # 4h EMA/ADX
        "htf_adx_max": 35.0,        # User: 30-35
        "htf_vwap_interval": "60min",   # 1h for VWAP alignment
        "htf_ema_interval": "240min",   # 4h for EMA/ADX filter
    }
    
    print("\n" + "="*80)
    print("VWAP SCALPING - YEARLY BREAKDOWN TEST")
    print("Higher MTF Configuration (1h VWAP + 4h EMA/ADX)")
    print("="*80)
    print(f"\nParameters:")
    print(f"  entry_mode:     {params['entry_mode']}")
    print(f"  sd_threshold:  {params['sd_threshold']}")
    print(f"  adx_max:        {params['adx_max']}")
    print(f"  rsi_max:        {params['rsi_max']}")
    print(f"  volume_mult:   {params['volume_mult']}")
    print(f"  atr_stop:       {params['atr_stop']}")
    print(f"  atr_target:     {params['atr_target']}")
    print(f"  use_htf_vwap:   {params['use_htf_vwap']} ({params['htf_vwap_interval']})")
    print(f"  use_htf_ema:    {params['use_htf_ema']} ({params['htf_ema_interval']})")
    print(f"  htf_adx_max:    {params['htf_adx_max']}")
    print(f"\nTransaction costs: {COST_PER_SIDE * 2 * 100:.2f}% per trade")
    
    # Test each year
    years = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]
    results = []
    
    for year in years:
        result = run_year_test(df_5m, year, params)
        if result:
            results.append(result)
            print(f"  -> Trades: {result['trades']}, Win Rate: {result['win_rate']:.1f}%")
            print(f"     Gross Return: {result['gross_return']:+.2f}%, Net Return: {result['net_return']:+.2f}%")
            print(f"     Profit Factor: {result['profit_factor']:.2f}, Sharpe: {result['sharpe']:.2f}")
    
    # Summary table
    print("\n" + "="*80)
    print("YEARLY RESULTS SUMMARY")
    print("="*80)
    print(f"{'Year':>6} | {'Bars':>7} | {'Trades':>7} | {'WR%':>7} | {'PF':>7} | {'Gross%':>10} | {'Net%':>10} | {'Sharpe':>7} | {'MaxDD%':>7}")
    print("-"*100)
    
    for r in results:
        print(f"{r['year']:>6} | {r['bars']:>7} | {r['trades']:>7} | {r['win_rate']:>6.1f}% | {r['profit_factor']:>7.2f} | {r['gross_return']:>+10.2f} | {r['net_return']:>+10.2f} | {r['sharpe']:>7.2f} | {r['max_dd']:>7.2f}")
    
    # Analysis
    print("\n" + "="*80)
    print("ANALYSIS")
    print("="*80)
    
    # Profitable years (net return > 0)
    profitable = [r for r in results if r['net_return'] > 0]
    print(f"\nProfitable Years (Net Return > 0): {len(profitable)}/{len(results)}")
    for r in profitable:
        print(f"  - {r['year']}: Net {r['net_return']:+.2f}%, WR {r['win_rate']:.1f}%, PF {r['profit_factor']:.2f}")
    
    # Acceptable win rate (>= 45%)
    acceptable_wr = [r for r in results if r['win_rate'] >= 45]
    print(f"\nAcceptable Win Rate (>= 45%): {len(acceptable_wr)}/{len(results)}")
    for r in acceptable_wr:
        print(f"  - {r['year']}: WR {r['win_rate']:.1f}%, Net {r['net_return']:+.2f}%")
    
    # Best year
    if results:
        best = max(results, key=lambda x: x['net_return'])
        print(f"\nBest Year: {best['year']}")
        print(f"  Net Return: {best['net_return']:+.2f}%")
        print(f"  Win Rate: {best['win_rate']:.1f}%")
        print(f"  Profit Factor: {best['profit_factor']:.2f}")
        print(f"  Trades: {best['trades']}")
    
    # Worst year
    if results:
        worst = min(results, key=lambda x: x['net_return'])
        print(f"\nWorst Year: {worst['year']}")
        print(f"  Net Return: {worst['net_return']:+.2f}%")
        print(f"  Win Rate: {worst['win_rate']:.1f}%")
        print(f"  Profit Factor: {worst['profit_factor']:.2f}")
        print(f"  Trades: {worst['trades']}")
    
    # Overall statistics
    total_trades = sum(r['trades'] for r in results)
    avg_win_rate = np.mean([r['win_rate'] for r in results])
    avg_net_return = np.mean([r['net_return'] for r in results])
    
    print(f"\nOverall Statistics:")
    print(f"  Total Trades: {total_trades}")
    print(f"  Average Win Rate: {avg_win_rate:.1f}%")
    print(f"  Average Net Return: {avg_net_return:+.2f}%")
    
    print("\n" + "="*80)
    print("TEST COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()
