#!/usr/bin/env python3
"""
VWAP High R:R Sweep — Test profitability with extreme risk-reward ratios
==========================================================================
Goal: Find configs where 35-40% win rate can still be profitable via high R:R.

Test Strategy:
- Use best configs from Stage 1 sweeps (good trade count + WR)
- Test extremely high R:R ratios (7:1, 10:1)
- Test with V4 features (SFP, nearest TP)
- Goal: Net Return > 0%, WR > 35%, Trades >= 20
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime
import json

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    run_vwap_scalping,
    compute_metrics,
    compute_reference_levels,
    INTERVAL_BARS_PER_YEAR,
)

STORAGE_ROOT = Path("G:/Candle Data")
COST_PER_SIDE = 0.0015  # 0.10% fee + 0.05% slippage


def load_data():
    """Load 5m BTCUSDT data for 2024."""
    print("Loading data...")
    df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
    print(f"Loaded {len(df):,} bars (2024)")
    return df


def load_exit_data():
    """Load 1m data for exit resolution."""
    print("Loading exit data (1m)...")
    df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_1m.parquet")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
    print(f"Loaded {len(df):,} bars (1m 2024)")
    return df


def run_test(params: dict, df_5m: pd.DataFrame, levels: pd.DataFrame, df_exit: pd.DataFrame | None = None):
    """Run a single test configuration."""
    # Merge params with defaults
    full_params = {
        # Core params - mean reversion with good WR
        "sd_threshold": 2.5,
        "atr_stop": 0.5,
        "atr_target": 3.5,
        "ema_fast": 9,
        "ema_slow": 20,
        "use_trend_filter": False,  # Disable for mean reversion
        "use_volume_filter": True,
        "volume_mult": 1.0,
        "use_trailing_stop": True,
        "trailing_atr": 1.0,
        "adx_max": 25.0,
        "rsi_max": 60.0,
        "entry_mode": "mean_reversion",
        "deviation_pct": 0.5,
        "momentum_bars": 2,
        "pullback_bars": 3,
        # Multi-timeframe
        "use_htf_vwap": True,
        "use_htf_ema": False,
        "htf_adx_max": 25.0,
        "htf_vwap_interval": "15min",
        "htf_ema_interval": "60min",
        # Stochastic
        "stoch_oversold": 20,
        "stoch_overbought": 80,
        # UTM features
        "use_anchored_vwap": True,
        "use_session_filter": True,  # Session filter for better WR
        "require_reversal_candle": False,
        "tp_mode": "vwap",
        "require_mss": False,
        "mss_timeout_bars": 6,
        # Reference levels
        "levels": levels,
        # V4 features
        "use_fvg_filter": False,
        "use_ob_filter": False,
        "use_eqhl_filter": False,
        "use_cvd_filter": False,
        "use_ote": False,
        # Exit data
        "df_exit": df_exit,
    }
    full_params.update(params)
    
    # Run backtest
    equity, trades = run_vwap_scalping(df_5m, cutoff=0.10, **full_params)
    
    # Compute metrics
    bars_per_year = INTERVAL_BARS_PER_YEAR["5m"]
    metrics = compute_metrics(equity, trades, bars_per_year=bars_per_year)
    
    # Calculate additional metrics
    wins = 0
    if trades:
        wins = sum(1 for t in trades if t > 0)
        win_rate = wins / len(trades) * 100
        gross_profit = sum(t for t in trades if t > 0)
        gross_loss = abs(sum(t for t in trades if t < 0))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        costs = len(trades) * COST_PER_SIDE * 2  # Round trip
        net_return = metrics['total_return_pct'] - costs
        
        # Debug: show first few trades and average win/loss
        avg_win = sum(t for t in trades if t > 0) / max(wins, 1) if wins > 0 else 0
        avg_loss = abs(sum(t for t in trades if t < 0) / max(len(trades) - wins, 1)) if wins < len(trades) else 0
    else:
        win_rate, profit_factor, costs, net_return, avg_win, avg_loss = 0, 0, 0, 0, 0, 0
    
    return {
        'config': params.copy(),
        'total_return': round(metrics['total_return_pct'], 2),
        'net_return': round(net_return, 2),
        'win_rate': round(win_rate, 1),
        'profit_factor': round(profit_factor, 2) if profit_factor != float('inf') else 999.99,
        'trades': len(trades),
        'sharpe': round(metrics['sharpe'], 2),
        'max_dd': round(metrics['max_dd_pct'], 1),
        'avg_win': round(avg_win * 100, 2),  # Convert to percentage
        'avg_loss': round(avg_loss * 100, 2),  # Convert to percentage
    }


def main():
    # Load data
    df_5m = load_data()
    df_exit = load_exit_data()
    
    # Pre-compute reference levels (needed for nearest TP)
    print("\nComputing reference levels...")
    levels = compute_reference_levels(df_5m)
    print("Reference levels computed.")
    
    # Test configs based on Stage 1 best performers
    # Base config: sd=2.5, mean_reversion, adx=25, rsi=60, vol=1.0, session_filter=True
    # This gave ~74 trades, 40.5% WR
    
    configs = []
    
    # === SD 2.5 Configs (Best from Stage 1: 74 trades, 40.5% WR) ===
    
    # Base R:R tests with ATR-based TP (more reliable R:R)
    configs.append({"name": "sd2.5_atr_7:1", "sd_threshold": 2.5, "atr_stop": 0.5, "atr_target": 3.5,
                    "rsi_max": 60, "require_reversal_candle": False, "tp_mode": "atr"})
    configs.append({"name": "sd2.5_atr_10:1", "sd_threshold": 2.5, "atr_stop": 0.5, "atr_target": 5.0,
                    "rsi_max": 60, "require_reversal_candle": False, "tp_mode": "atr"})
    configs.append({"name": "sd2.5_atr_15:1", "sd_threshold": 2.5, "atr_stop": 0.5, "atr_target": 7.5,
                    "rsi_max": 60, "require_reversal_candle": False, "tp_mode": "atr"})
    
    # Higher stop, same R:R (ATR mode)
    configs.append({"name": "sd2.5_stop07_7:1", "sd_threshold": 2.5, "atr_stop": 0.7, "atr_target": 4.9,
                    "rsi_max": 60, "require_reversal_candle": False, "tp_mode": "atr"})
    configs.append({"name": "sd2.5_stop07_10:1", "sd_threshold": 2.5, "atr_stop": 0.7, "atr_target": 7.0,
                    "rsi_max": 60, "require_reversal_candle": False, "tp_mode": "atr"})
    configs.append({"name": "sd2.5_stop07_15:1", "sd_threshold": 2.5, "atr_stop": 0.7, "atr_target": 10.5,
                    "rsi_max": 60, "require_reversal_candle": False, "tp_mode": "atr"})
    
    # Wide stop, high R:R (ATR mode)
    configs.append({"name": "sd2.5_stop10_7:1", "sd_threshold": 2.5, "atr_stop": 1.0, "atr_target": 7.0,
                    "rsi_max": 60, "require_reversal_candle": False, "tp_mode": "atr"})
    configs.append({"name": "sd2.5_stop10_10:1", "sd_threshold": 2.5, "atr_stop": 1.0, "atr_target": 10.0,
                    "rsi_max": 60, "require_reversal_candle": False, "tp_mode": "atr"})
    
    # With SFP (require_reversal_candle) - ATR mode
    configs.append({"name": "sd2.5_SFP_7:1", "sd_threshold": 2.5, "atr_stop": 0.5, "atr_target": 3.5,
                    "rsi_max": 60, "require_reversal_candle": True, "tp_mode": "atr"})
    configs.append({"name": "sd2.5_SFP_10:1", "sd_threshold": 2.5, "atr_stop": 0.5, "atr_target": 5.0,
                    "rsi_max": 60, "require_reversal_candle": True, "tp_mode": "atr"})
    configs.append({"name": "sd2.5_SFP_15:1", "sd_threshold": 2.5, "atr_stop": 0.5, "atr_target": 7.5,
                    "rsi_max": 60, "require_reversal_candle": True, "tp_mode": "atr"})
    
    # === SD 2.5 with rsi=40 (72 trades, 41.7% WR) ===
    configs.append({"name": "sd2.5_rsi40_7:1", "sd_threshold": 2.5, "atr_stop": 0.5, "atr_target": 3.5,
                    "rsi_max": 40, "require_reversal_candle": False, "tp_mode": "atr"})
    configs.append({"name": "sd2.5_rsi40_10:1", "sd_threshold": 2.5, "atr_stop": 0.5, "atr_target": 5.0,
                    "rsi_max": 40, "require_reversal_candle": False, "tp_mode": "atr"})
    configs.append({"name": "sd2.5_rsi40_15:1", "sd_threshold": 2.5, "atr_stop": 0.5, "atr_target": 7.5,
                    "rsi_max": 40, "require_reversal_candle": False, "tp_mode": "atr"})
    
    # === SD 2.0 Config (204 trades, 30.4% WR) ===
    configs.append({"name": "sd2.0_atr_7:1", "sd_threshold": 2.0, "atr_stop": 0.5, "atr_target": 3.5,
                    "rsi_max": 60, "require_reversal_candle": False, "tp_mode": "atr"})
    configs.append({"name": "sd2.0_atr_10:1", "sd_threshold": 2.0, "atr_stop": 0.5, "atr_target": 5.0,
                    "rsi_max": 60, "require_reversal_candle": False, "tp_mode": "atr"})
    configs.append({"name": "sd2.0_atr_15:1", "sd_threshold": 2.0, "atr_stop": 0.5, "atr_target": 7.5,
                    "rsi_max": 60, "require_reversal_candle": False, "tp_mode": "atr"})
    configs.append({"name": "sd2.0_SFP_7:1", "sd_threshold": 2.0, "atr_stop": 0.5, "atr_target": 3.5,
                    "rsi_max": 60, "require_reversal_candle": True, "tp_mode": "atr"})
    configs.append({"name": "sd2.0_SFP_10:1", "sd_threshold": 2.0, "atr_stop": 0.5, "atr_target": 5.0,
                    "rsi_max": 60, "require_reversal_candle": True, "tp_mode": "atr"})
    
    # === SD 1.5 Config (286 trades, 30.8% WR) ===
    configs.append({"name": "sd1.5_atr_7:1", "sd_threshold": 1.5, "atr_stop": 0.5, "atr_target": 3.5,
                    "rsi_max": 60, "require_reversal_candle": False, "tp_mode": "atr", "volume_mult": 1.5})
    configs.append({"name": "sd1.5_atr_10:1", "sd_threshold": 1.5, "atr_stop": 0.5, "atr_target": 5.0,
                    "rsi_max": 60, "require_reversal_candle": False, "tp_mode": "atr", "volume_mult": 1.5})
    configs.append({"name": "sd1.5_atr_15:1", "sd_threshold": 1.5, "atr_stop": 0.5, "atr_target": 7.5,
                    "rsi_max": 60, "require_reversal_candle": False, "tp_mode": "atr", "volume_mult": 1.5})
    
    # Run tests
    results = []
    print("\n" + "="*80)
    print("VWAP HIGH R:R SWEEP - Testing profitability with extreme risk-reward")
    print("="*80 + "\n")
    
    for i, cfg in enumerate(configs):
        name = cfg.pop("name")
        print(f"[{i+1}/{len(configs)}] Testing: {name}...", end=" ", flush=True)
        
        result = run_test(cfg, df_5m, levels, df_exit)
        result['name'] = name
        
        # Calculate R:R
        rr = result['config'].get('atr_target', 0) / result['config'].get('atr_stop', 1)
        result['rr'] = round(rr, 1)
        
        status = "[PASS]" if result['net_return'] > 0 else "[FAIL]"
        
        # Debug: show actual average win/loss vs expected
        if result['trades'] > 0:
            print(f"{result['trades']} trades, WR={result['win_rate']:.1f}%, "
                  f"AvgWin={result['avg_win']:+.2f}%, AvgLoss={result['avg_loss']:+.2f}%, "
                  f"PF={result['profit_factor']:.2f}, Net={result['net_return']:+.1f}% {status}")
        else:
            print(f"0 trades")
        
        results.append(result)
    
    # Sort by net return
    results.sort(key=lambda x: x['net_return'], reverse=True)
    
    # Print summary table
    print("\n" + "="*100)
    print("RESULTS SUMMARY (sorted by net return)")
    print("="*100)
    print(f"{'Config':<30} {'R:R':<6} {'Trades':<7} {'WR%':<6} {'PF':<7} {'Net%':<8} {'Status'}")
    print("-"*100)
    
    for r in results:
        status = "[PASS]" if r['net_return'] > 0 and r['win_rate'] >= 35 and r['trades'] >= 20 else ""
        print(f"{r['name']:<30} {r['rr']:<6} {r['trades']:<7} {r['win_rate']:<6.1f} "
              f"{r['profit_factor']:<7.2f} {r['net_return']:+.2f}%  {status}")
    
    # Find best config
    passing = [r for r in results if r['net_return'] > 0 and r['win_rate'] >= 35 and r['trades'] >= 20]
    
    print("\n" + "="*100)
    if passing:
        print(f"FOUND {len(passing)} PASSING CONFIGURATIONS:")
        for p in passing:
            print(f"  • {p['name']}: Net={p['net_return']:+.1f}%, WR={p['win_rate']:.1f}%, "
                  f"Trades={p['trades']}, PF={p['profit_factor']:.2f}")
    else:
        best = results[0] if results else None
        if best and best['net_return'] > 0:
            print(f"Best config: {best['name']} with Net={best['net_return']:+.1f}% "
                  f"(WR={best['win_rate']:.1f}%, Trades={best['trades']})")
        else:
            print("No profitable configs found in this sweep.")
    
    # Save results
    output_file = Path(__file__).parent / "high_rr_sweep_results.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_file}")
    
    return results


if __name__ == "__main__":
    main()