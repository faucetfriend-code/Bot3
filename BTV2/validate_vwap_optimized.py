#!/usr/bin/env python3
"""
VWAP Optimized Settings Validation - Correct Baseline Comparison

The "54.1% WR, +67% P&L" baseline from analysis was achieved with specific 
C_TrendFollowing config, not default .env settings.

Compares:
- Baseline: C_TrendFollowing (the config that achieved 54.1% WR, +67% P&L)
- Optimized: New settings combining all winning factors
"""

import sys
from pathlib import Path
import pandas as pd
import numpy as np
from typing import Dict, List, Any
import importlib.util

# Add paths
parent = Path(__file__).parent.parent
sys.path.insert(0, str(parent))
sys.path.insert(0, str(parent / "BTV2"))

# Direct import
spec = importlib.util.spec_from_file_location(
    "strategies_module", 
    parent / "BTV2" / "strategies.py"
)
strategies = importlib.util.module_from_spec(spec)
spec.loader.exec_module(strategies)

run_vwap_scalping = strategies.run_vwap_scalping
compute_metrics = strategies.compute_metrics
INTERVAL_BARS_PER_YEAR = strategies.INTERVAL_BARS_PER_YEAR

STORAGE_ROOT = Path("G:/Candle Data")
COST_PER_TRADE = 0.003


def load_data(symbol: str = "BTCUSDT", interval: str = "5m") -> pd.DataFrame:
    """Load candle data."""
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Data not found: {path}")
    df = pd.read_parquet(path)
    df.columns = [c.capitalize() for c in df.columns]
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def run_backtest(df_5m, df_1m, params, name: str) -> Dict:
    """Run backtest with metrics."""
    equity, trades = run_vwap_scalping(
        df_5m, 
        cutoff=0.10, 
        df_exit=df_1m,
        **params
    )
    
    if len(equity) < 2 or len(trades) == 0:
        return {
            "name": name, "trades": 0, "net_pct": 0, "win_rate": 0, 
            "profit_factor": 0, "sharpe": 0, "max_dd": 0, "cagr": 0,
        }
    
    costs = len(trades) * COST_PER_TRADE
    gross_pct = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    net_pct = gross_pct - costs
    
    wins = [t for t in trades if t > 0]
    win_rate = len(wins) / len(trades) * 100
    
    gross_profit = sum(wins)
    gross_loss = abs(sum(t for t in trades if t < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.99
    
    m = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["5m"])
    
    return {
        "name": name,
        "trades": len(trades),
        "net_pct": round(net_pct, 2),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "sharpe": round(m.get("sharpe", 0), 2),
        "max_dd": round(m.get("max_dd_pct", 0), 2),
        "cagr": round(m.get("cagr_pct", 0), 2),
    }


def run_tests():
    """Run comparison tests."""
    print("=" * 70)
    print("VWAP OPTIMIZED SETTINGS VALIDATION")
    print("=" * 70)
    
    # Load data
    print("\nLoading data...")
    df_5m = load_data("BTCUSDT", "5m")
    df_1m = load_data("BTCUSDT", "1m")
    print(f"5m data: {len(df_5m)} bars")
    
    # Test periods (matching analysis: 2020-2024)
    TEST_PERIODS = [
        ("2020", "2020-01-01", "2021-01-01"),
        ("2021", "2021-01-01", "2022-01-01"),
        ("2022", "2022-01-01", "2023-01-01"),
        ("2023", "2023-01-01", "2024-01-01"),
        ("2024", "2024-01-01", "2025-01-01"),
    ]
    
    # BASELINE: The config that achieved 54.1% WR, +67% P&L in analysis
    # From vwap_trade_analysis_detailed.csv generation (C_TrendFollowing)
    BASELINE_PARAMS = {
        "sd_threshold": 0.5,        # Very tight - almost any deviation triggers entry
        "atr_stop": 3.0,            # Much wider stop
        "atr_target": 6.0,           # Much wider target (RR = 2:1)
        "entry_mode": "cross",       # VWAP cross entries
        "use_session_filter": True,
        "use_trailing_stop": True,
        "trailing_atr": 1.2,
        "tp_mode": "atr",           # ATR-based TP (not VWAP-based)
        "require_reversal_candle": True,
        "adx_max": 30.0,
        "rsi_max": 50.0,
        "volume_mult": 2.0,
        "use_htf_ema": False,       # No HTF filter in baseline
        "use_htf_vwap": True,
        "use_anchored_vwap": True,
    }
    
    # OPTIMIZED: All winning factors combined
    # From analysis findings:
    # - prefer bear_pullback (shorts 65% WR vs longs 52%)
    # - avoid -1SD band (43% WR vs higher bands)
    # - use HTF EMA filter (1h trend)
    # - US session 16:00-24:00 UTC (57.8% WR)
    # - reversal candle required
    # - trailing is the REAL exit (not TP)
    OPTIMIZED_PARAMS = {
        "sd_threshold": 2.5,         # Avoid -1SD (underperforms at 43%)
        "atr_stop": 2.0,             # For trailing
        "atr_target": 3.0,
        "entry_mode": "cross",       # Keep cross mode from baseline
        "use_session_filter": True,
        "use_trailing_stop": True,
        "trailing_atr": 1.2,
        "tp_mode": "atr",
        "require_reversal_candle": True,
        "adx_max": 30.0,
        "rsi_max": 50.0,
        "volume_mult": 2.0,
        # NEW winning factors:
        "use_htf_ema": True,         # 1h EMA filter - MUST use
        "use_htf_vwap": True,
        "htf_adx_max": 25.0,
        "use_anchored_vwap": True,
    }
    
    # Also test the bull vs bear pullback specifically
    BEAR_PULLBACK_PARAMS = {
        **BASELINE_PARAMS,
        "entry_mode": "bear_pullback",  # Shorts only - 65% WR in bear markets
    }
    
    BULL_PULLBACK_PARAMS = {
        **BASELINE_PARAMS,
        "entry_mode": "bull_pullback",   # Longs only
    }
    
    print("\n" + "-" * 70)
    print("BASELINE (C_TrendFollowing from analysis - 54.1% WR, +67% P&L):")
    print("-" * 70)
    for k, v in BASELINE_PARAMS.items():
        print(f"  {k}: {v}")
    
    print("\n" + "-" * 70)
    print("OPTIMIZED (All winning factors combined):")
    print("-" * 70)
    for k, v in OPTIMIZED_PARAMS.items():
        print(f"  {k}: {v}")
    
    # Run tests
    results = []
    
    print("\n" + "=" * 70)
    print("RUNNING TESTS 2020-2024")
    print("=" * 70)
    
    all_results = {
        "Baseline": [],
        "Optimized": [],
        "Bear_Pullback": [],
        "Bull_Pullback": [],
    }
    
    configs = {
        "Baseline": BASELINE_PARAMS,
        "Optimized": OPTIMIZED_PARAMS,
        "Bear_Pullback": BEAR_PULLBACK_PARAMS,
        "Bull_Pullback": BULL_PULLBACK_PARAMS,
    }
    
    for year, start, end in TEST_PERIODS:
        print(f"\n--- {year} ---")
        
        df_5m_p = df_5m[(df_5m.index >= start) & (df_5m.index < end)]
        df_1m_p = df_1m[(df_1m.index >= start) & (df_1m.index < end)] if df_1m is not None else None
        
        if df_5m_p.empty:
            print(f"  No data for {year}")
            continue
        
        print(f"  Data: {len(df_5m_p)} bars")
        
        for cfg_name, params in configs.items():
            r = run_backtest(df_5m_p, df_1m_p, params, f"{cfg_name}_{year}")
            print(f"  {cfg_name}: {r['trades']} trades, {r['win_rate']:.1f}% WR, {r['net_pct']:+.2f}% P&L")
            all_results[cfg_name].append(r)
    
    # Aggregate summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    summary_data = []
    for cfg_name in ["Baseline", "Optimized", "Bear_Pullback", "Bull_Pullback"]:
        cfg_results = all_results[cfg_name]
        total_trades = sum(r["trades"] for r in cfg_results)
        total_pnl = sum(r["net_pct"] for r in cfg_results)
        avg_wr = np.mean([r["win_rate"] for r in cfg_results if r["trades"] > 0])
        
        print(f"\n{cfg_name}:")
        print(f"  Total Trades: {total_trades}")
        print(f"  Avg Win Rate: {avg_wr:.1f}%")
        print(f"  Total P&L: {total_pnl:+.2f}%")
        
        summary_data.append({
            "config": cfg_name,
            "total_trades": total_trades,
            "avg_win_rate": round(avg_wr, 1),
            "total_pnl": round(total_pnl, 2),
            "avg_sharpe": round(np.mean([r["sharpe"] for r in cfg_results if r["trades"] > 0]), 2),
            "avg_maxdd": round(np.mean([r["max_dd"] for r in cfg_results if r["trades"] > 0]), 1),
        })
    
    # Comparison to targets
    print("\n" + "-" * 70)
    print("COMPARISON TO TARGETS:")
    print("-" * 70)
    print(f"  Baseline (from analysis): 54.1% WR, +67% P&L")
    print(f"  Target: 58%+ WR, 75%+ P&L")
    
    baseline_summary = next((s for s in summary_data if s["config"] == "Baseline"), None)
    optimized_summary = next((s for s in summary_data if s["config"] == "Optimized"), None)
    
    if baseline_summary:
        print(f"\n  Achieved Baseline WR: {baseline_summary['avg_win_rate']:.1f}%")
        print(f"  Achieved Baseline P&L: {baseline_summary['total_pnl']:+.2f}%")
    
    if optimized_summary:
        print(f"\n  Achieved Optimized WR: {optimized_summary['avg_win_rate']:.1f}%")
        print(f"  Achieved Optimized P&L: {optimized_summary['total_pnl']:+.2f}%")
        
        wr_improvement = optimized_summary['avg_win_rate'] - baseline_summary['avg_win_rate']
        pnl_improvement = optimized_summary['total_pnl'] - baseline_summary['total_pnl']
        
        print(f"\n  Improvement:")
        print(f"    Win Rate: {wr_improvement:+.1f}%")
        print(f"    P&L: {pnl_improvement:+.2f}%")
    
    # Save CSV
    results_df = pd.DataFrame(summary_data)
    output_path = parent / "BTV2" / "results" / "vwap_optimized_validation.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    results_df.to_csv(output_path, index=False)
    print(f"\n\nSaved: {output_path}")
    
    # Also print yearly breakdown
    print("\n" + "=" * 70)
    print("YEARLY BREAKDOWN")
    print("=" * 70)
    
    yearly_df = []
    for year_idx, (year, start, end) in enumerate(TEST_PERIODS):
        for cfg_name in ["Baseline", "Optimized", "Bear_Pullback", "Bull_Pullback"]:
            r = all_results[cfg_name][year_idx] if year_idx < len(all_results[cfg_name]) else None
            if r:
                yearly_df.append({
                    "year": year,
                    "config": cfg_name,
                    "trades": r["trades"],
                    "win_rate": r["win_rate"],
                    "net_pct": r["net_pct"],
                    "sharpe": r["sharpe"],
                })
    
    yearly_df = pd.DataFrame(yearly_df)
    print(yearly_df.to_string(index=False))
    
    yearly_path = parent / "BTV2" / "results" / "vwap_optimized_validation_yearly.csv"
    yearly_df.to_csv(yearly_path, index=False)
    print(f"\nSaved yearly breakdown: {yearly_path}")
    
    return results_df


if __name__ == "__main__":
    run_tests()