#!/usr/bin/env python3
"""
VWAP Scalping Strategy - Yearly Breakdown Test V3
==================================================
Testing additional parameter combinations to find profitable years.
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
    if year == 2025:
        end_date = "2026-01-01"
    
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
        win_rate = profit_factor = costs = net_return = 0
    
    return {
        'year': year, 'trades': len(trades), 'win_rate': win_rate,
        'profit_factor': profit_factor, 'gross_return': metrics['total_return_pct'],
        'net_return': net_return, 'sharpe': metrics['sharpe'], 'max_dd': metrics['max_dd_pct'],
    }


def test_config(df_5m, config_name, params, years):
    print(f"\n{'='*70}")
    print(f"CONFIG: {config_name}")
    print(f"{'='*70}")
    
    results = []
    for year in years:
        result = run_year_test(df_5m, year, params)
        if result:
            results.append(result)
            print(f"  {year}: Trades={result['trades']:>3}, WR={result['win_rate']:>5.1f}%, "
                  f"PF={result['profit_factor']:.2f}, Net={result['net_return']:>+8.2f}%")
    
    profitable = [r for r in results if r['net_return'] > 0]
    print(f"\n  Summary: {len(profitable)}/{len(results)} profitable years")
    
    return results


def main():
    print("Loading BTCUSDT 5m data...")
    df_5m = load_parquet("BTCUSDT", "5m")
    print(f"Total bars: {len(df_5m)}, Range: {df_5m.index[0].date()} to {df_5m.index[-1].date()}")
    
    years = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]
    
    # More configurations to test
    configs = [
        {
            "name": "Mean Reversion + Lower SD (1.5)",
            "params": {
                "entry_mode": "mean_reversion",
                "sd_threshold": 1.5,
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
        },
        {
            "name": "Cross Entry + HTF",
            "params": {
                "entry_mode": "cross",
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
        },
        {
            "name": "Bear Pullback + HTF",
            "params": {
                "entry_mode": "bear_pullback",
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
        },
        {
            "name": "Deviation Entry + HTF",
            "params": {
                "entry_mode": "deviation",
                "deviation_pct": 0.5,
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
        },
        {
            "name": "Tighter Stop (0.4) + HTF",
            "params": {
                "entry_mode": "mean_reversion",
                "sd_threshold": 2.0,
                "adx_max": 20.0,
                "rsi_max": 40.0,
                "volume_mult": 2.0,
                "atr_stop": 0.4,
                "atr_target": 3.0,
                "use_htf_vwap": True,
                "use_htf_ema": True,
                "htf_adx_max": 35.0,
                "htf_vwap_interval": "60min",
                "htf_ema_interval": "240min",
            }
        },
        {
            "name": "Wider Target (4.5) + HTF",
            "params": {
                "entry_mode": "mean_reversion",
                "sd_threshold": 2.0,
                "adx_max": 20.0,
                "rsi_max": 40.0,
                "volume_mult": 2.0,
                "atr_stop": 0.6,
                "atr_target": 4.5,
                "use_htf_vwap": True,
                "use_htf_ema": True,
                "htf_adx_max": 35.0,
                "htf_vwap_interval": "60min",
                "htf_ema_interval": "240min",
            }
        },
    ]
    
    all_results = {}
    for cfg in configs:
        results = test_config(df_5m, cfg["name"], cfg["params"], years)
        all_results[cfg["name"]] = results
    
    # Find best configuration
    print("\n" + "="*80)
    print("RANKING BY TOTAL NET RETURN")
    print("="*80)
    
    rankings = []
    for cfg in configs:
        results = all_results[cfg["name"]]
        total_net = sum(r['net_return'] for r in results)
        avg_wr = np.mean([r['win_rate'] for r in results])
        profitable = len([r for r in results if r['net_return'] > 0])
        rankings.append({
            'name': cfg['name'],
            'total_net': total_net,
            'avg_wr': avg_wr,
            'profitable': profitable,
            'results': results
        })
    
    rankings.sort(key=lambda x: x['total_net'], reverse=True)
    
    for i, r in enumerate(rankings, 1):
        print(f"\n{i}. {r['name']}")
        print(f"   Total Net: {r['total_net']:+.1f}%, Avg WR: {r['avg_wr']:.1f}%, Profitable: {r['profitable']}/8")
    
    # Best config details
    best = rankings[0]
    print(f"\n{'='*80}")
    print(f"BEST CONFIG: {best['name']}")
    print(f"{'='*80}")
    print(f"\n{'Year':>6} | {'Trades':>7} | {'WR%':>7} | {'PF':>7} | {'Gross%':>10} | {'Net%':>10}")
    print("-"*60)
    for r in best['results']:
        print(f"{r['year']:>6} | {r['trades']:>7} | {r['win_rate']:>6.1f}% | {r['profit_factor']:>7.2f} | {r['gross_return']:>+10.2f} | {r['net_return']:>+10.2f}")
    
    print("\n" + "="*80)
    print("TEST COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()
