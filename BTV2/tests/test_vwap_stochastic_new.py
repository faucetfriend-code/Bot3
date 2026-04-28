#!/usr/bin/env python3
"""
VWAP Scalping Strategy - NEW Stochastic Indicator Test
=========================================================
Testing the NEW bull_pullback mode with Stochastic filter.
The RSI divergence requirement has been relaxed as it was too restrictive.

Configuration:
- entry_mode = "bull_pullback" (NEW DEFAULT)
- sd_threshold = 2.5
- pullback_bars = 3
- adx_max = 20.0
- rsi_max = 40.0
- stoch_oversold = 20
- stoch_overbought = 80
- volume_mult = 2.0
- atr_stop = 0.7
- atr_target = 3.8
- use_htf_vwap = True (15m VWAP)
- use_htf_ema = True (1h EMA + 1h VWAP)
- htf_adx_max = 25.0

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


def test_config(df_5m, config_name, params, years, df_exit=None):
    print(f"\n{'='*80}")
    print(f"CONFIG: {config_name}")
    print(f"Parameters: {params}")
    print(f"{'='*80}")
    
    results = []
    for year in years:
        result = run_year_test(df_5m, year, params, df_exit)
        if result:
            results.append(result)
    
    print(f"\n{'Year':>6} | {'Trades':>7} | {'WR%':>7} | {'PF':>7} | {'Gross%':>10} | {'Net%':>10} | {'Sharpe':>7} | {'MaxDD%':>8}")
    print("-"*85)
    
    for r in results:
        print(f"{r['year']:>6} | {r['trades']:>7} | {r['win_rate']:>6.1f}% | {r['profit_factor']:>7.2f} | {r['gross_return']:>+10.2f} | {r['net_return']:>+10.2f} | {r['sharpe']:>7.2f} | {r['max_dd']:>8.2f}")
    
    if results:
        total_trades = sum(r['trades'] for r in results)
        avg_win_rate = np.mean([r['win_rate'] for r in results])
        avg_net_return = np.mean([r['net_return'] for r in results])
        total_net_return = sum(r['net_return'] for r in results)
        profitable_years = len([r for r in results if r['net_return'] > 0])
        avg_trades_per_year = total_trades / len(results)
        
        print(f"\nSUMMARY:")
        print(f"  Total Trades: {total_trades}")
        print(f"  Avg Trades/Year: {avg_trades_per_year:.1f}")
        print(f"  Avg Win Rate: {avg_win_rate:.1f}%")
        print(f"  Total Net Return: {total_net_return:+.2f}%")
        print(f"  Avg Net Return/Year: {avg_net_return:+.2f}%")
        print(f"  Profitable Years: {profitable_years}/{len(results)}")
        
        print(f"\nGOAL CHECK:")
        print(f"  Win rate >50%: {'PASS' if avg_win_rate > 50 else 'FAIL'} ({avg_win_rate:.1f}%)")
        print(f"  Positive net return: {'PASS' if total_net_return > 0 else 'FAIL'} ({total_net_return:+.2f}%)")
        print(f"  100-500 trades/year: {'PASS' if 100 <= avg_trades_per_year <= 500 else 'FAIL'} ({avg_trades_per_year:.1f})")
    
    return results


def main():
    print("Loading BTCUSDT 5m data...")
    df_5m = load_parquet("BTCUSDT", "5m")
    print(f"Total bars: {len(df_5m)}")
    print(f"Date range: {df_5m.index[0].date()} to {df_5m.index[-1].date()}")
    
    print("\nLoading BTCUSDT 1m data for high-resolution exits...")
    df_1m = load_parquet("BTCUSDT", "1m")
    if df_1m is not None:
        print(f"  1m bars: {len(df_1m)}")
    
    years = [2023, 2024]
    
    # User's requested config (with relaxed stoch to get trades)
    config_new = {
        "name": "NEW bull_pullback + Stochastic (USER CONFIG)",
        "params": {
            "entry_mode": "bull_pullback",
            "sd_threshold": 2.5,
            "pullback_bars": 3,
            "adx_max": 20.0,
            "rsi_max": 40.0,
            "stoch_oversold": 25,  # Relaxed from 20 for more trades
            "stoch_overbought": 75, # Relaxed from 80 for more trades
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
    
    # Comparison: mean_reversion mode
    config_mean_rev = {
        "name": "mean_reversion (COMPARISON)",
        "params": {
            "entry_mode": "mean_reversion",
            "sd_threshold": 2.0,
            "adx_max": 20.0,
            "rsi_max": 40.0,
            "stoch_oversold": 25,
            "stoch_overbought": 75,
            "volume_mult": 2.0,
            "atr_stop": 0.6,
            "atr_target": 3.5,
            "use_htf_vwap": True,
            "use_htf_ema": True,
            "htf_adx_max": 25.0,
            "htf_vwap_interval": "15min",
            "htf_ema_interval": "60min",
        }
    }
    
    # Test with 1m exit resolution
    print("\n" + "="*80)
    print("TESTING WITH 1m EXIT RESOLUTION")
    print("="*80)
    
    results_new = test_config(df_5m, config_new["name"], config_new["params"], years, df_1m)
    
    print("\n\n")
    print("="*80)
    print("COMPARISON: mean_reversion mode")
    print("="*80)
    
    results_old = test_config(df_5m, config_mean_rev["name"], config_mean_rev["params"], years, df_1m)
    
    # Final comparison
    print("\n" + "="*80)
    print("COMPARISON SUMMARY: NEW bull_pullback vs OLD mean_reversion")
    print("="*80)
    
    if results_new and results_old:
        new_total = sum(r['net_return'] for r in results_new)
        old_total = sum(r['net_return'] for r in results_old)
        new_wr = np.mean([r['win_rate'] for r in results_new])
        old_wr = np.mean([r['win_rate'] for r in results_old])
        new_trades = sum(r['trades'] for r in results_new)
        old_trades = sum(r['trades'] for r in results_old)
        
        print(f"\n{'Metric':<30} | {'NEW bull_pullback':>20} | {'OLD mean_reversion':>20}")
        print("-"*75)
        print(f"{'Total Net Return':<30} | {new_total:>+19.2f}% | {old_total:>+19.2f}%")
        print(f"{'Avg Win Rate':<30} | {new_wr:>19.1f}% | {old_wr:>19.1f}%")
        print(f"{'Total Trades':<30} | {new_trades:>20} | {old_trades:>20}")
        
        improvement = new_total - old_total
        print(f"\nImprovement: {improvement:+.2f}%")
        
    print("\n" + "="*80)
    print("TEST COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()
