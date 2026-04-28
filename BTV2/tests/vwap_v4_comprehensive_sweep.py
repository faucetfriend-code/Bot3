#!/usr/bin/env python3
"""
VWAP Scalping V4 Comprehensive Sweep
=====================================
Compare old (baseline) vs new V4 methods with various feature combinations.

Tests:
1. Old best config (baseline)
2. Old + nearest TP
3. Old + session filter
4. Old + nearest TP + session filter
5. Old + FVG filter
6. Old + Order Block filter
7. Old + EQHL filter
8. Old + MSS
9. Old + SFP (require_reversal_candle)
10. Old + OTE
11. Old + CVD
12. Full V4 (all features enabled)

Goals:
- Win rate > 48%
- Net return > 0%
- Trade count >= 20
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
import time
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

STORAGE_ROOT = Path("G:/Candle Data")

# Cost model
COST_PER_SIDE = 0.0015  # 0.10% fee + 0.05% slippage

# Default baseline params (OLD method) - must match run_vwap_scalping defaults
# Using mean_reversion for more active trading with balanced risk-reward
BASELINE_PARAMS = {
    # Core parameters - balanced risk-reward for 2024
    "sd_threshold": 2.0,
    "atr_stop": 1.0,  # Balanced stop
    "atr_target": 2.0,  # 2:1 R:R - needs 34% win rate to break even
    "ema_fast": 9,
    "ema_slow": 20,
    "use_trend_filter": True,
    "use_volume_filter": True,
    "volume_mult": 1.5,
    "use_trailing_stop": True,
    "trailing_atr": 1.0,
    "adx_max": 30.0,
    "rsi_max": 50.0,
    "entry_mode": "mean_reversion",
    "deviation_pct": 0.5,
    "momentum_bars": 2,
    "pullback_bars": 3,
    # Multi-timeframe filters
    "use_htf_vwap": True,
    "use_htf_ema": False,
    "htf_adx_max": 25.0,
    "htf_vwap_interval": "15min",
    "htf_ema_interval": "60min",
    # Stochastic
    "stoch_oversold": 20,
    "stoch_overbought": 80,
    # UTM / anchored-VWAP (user's old config - all OFF for baseline)
    "use_anchored_vwap": True,
    "use_session_filter": False,
    "require_reversal_candle": False,
    "tp_mode": "vwap",
    "require_mss": False,
    "mss_timeout_bars": 6,
    # V4 features (all OFF for baseline)
    "use_fvg_filter": False,
    "use_ob_filter": False,
    "use_eqhl_filter": False,
    "use_cvd_filter": False,
    "use_ote": False,
}


def load_data():
    """Load 5m BTCUSDT data for 2024."""
    print("Loading data...")
    df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
    df.columns = [c.capitalize() for c in df.columns]
    df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
    print(f"Loaded {len(df):,} bars (2024)")
    return df


def compute_order_flow_data(df: pd.DataFrame):
    """Pre-compute order flow data for filters."""
    print("Computing order flow data...")
    from strategies import (
        compute_reference_levels,
        compute_fair_value_gaps,
        compute_order_blocks,
        compute_equal_highs_lows,
        compute_cvd,
    )
    
    levels = compute_reference_levels(df)
    fvg_data = compute_fair_value_gaps(df)
    ob_data = compute_order_blocks(df)
    eqhl_data = compute_equal_highs_lows(df)
    cvd_data = compute_cvd(df, window=20)
    
    print("Order flow data computed.")
    return {
        "levels": levels,
        "fvg_data": fvg_data,
        "ob_data": ob_data,
        "eqhl_data": eqhl_data,
        "cvd_data": cvd_data,
    }


def run_test(df: pd.DataFrame, name: str, params: dict, order_flow_data: dict) -> dict:
    """Run a single test configuration."""
    from strategies import run_vwap_scalping, compute_metrics, apply_butterworth
    
    # Prepare parameters
    full_params = {
        **BASELINE_PARAMS,
        **params,
    }
    
    # Add order flow data if needed
    if params.get("use_fvg_filter"):
        full_params["fvg_data"] = order_flow_data["fvg_data"]
    if params.get("use_ob_filter"):
        full_params["ob_data"] = order_flow_data["ob_data"]
    if params.get("use_eqhl_filter"):
        full_params["eqhl_data"] = order_flow_data["eqhl_data"]
    if params.get("use_cvd_filter"):
        full_params["cvd_window"] = 20
    if params.get("use_ote"):
        # OTE requires exit resolution data (1m candles)
        # For now, pass the 5m data as a fallback
        full_params["df_exit"] = df
    
    # Always add levels for pdh/nearest tp_mode
    if params.get("tp_mode") in ("pdh", "nearest"):
        full_params["levels"] = order_flow_data["levels"]
    
    try:
        equity, trades = run_vwap_scalping(df, 0.10, **full_params)
        
        # Also return if no trades
        if len(trades) == 0:
            return {
                "name": name,
                "config": params,
                "trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "net_return": 0.0,
                "sharpe": 0.0,
                "error": None,
            }
        
        if len(trades) < 3:
            return {
                "name": name,
                "config": params,
                "trades": len(trades),
                "win_rate": sum(1 for t in trades if t > 0) / len(trades) * 100 if trades else 0,
                "profit_factor": 0.0,
                "net_return": (sum(trades)) * 100,
                "sharpe": 0.0,
                "max_dd": 0.0,
                "error": None,
            }
        
        metrics = compute_metrics(equity, trades)
        
        return {
            "name": name,
            "config": params,
            "trades": metrics["n_trades"],
            "win_rate": metrics["win_rate_pct"],
            "profit_factor": metrics["profit_factor"],
            "net_return": metrics["total_return_pct"],
            "sharpe": metrics["sharpe"],
            "max_dd": metrics["max_dd_pct"],
            "error": None,
        }
    except Exception as e:
        import traceback
        return {
            "name": name,
            "config": params,
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "net_return": 0.0,
            "sharpe": 0.0,
            "error": f"{str(e)}: {traceback.format_exc(limit=500)}",
        }


def get_test_configurations():
    """Define all test configurations to run."""
    
    tests = []
    
    # Test 1: Baseline (OLD method)
    tests.append({
        "name": "01_BASELINE_OLD",
        "params": {
            "use_session_filter": False,
            "require_reversal_candle": False,
            "tp_mode": "vwap",
            "require_mss": False,
            "use_fvg_filter": False,
            "use_ob_filter": False,
            "use_eqhl_filter": False,
            "use_cvd_filter": False,
            "use_ote": False,
        }
    })
    
    # Test 2: Old + nearest TP
    tests.append({
        "name": "02_OLD_NEAREST_TP",
        "params": {
            "use_session_filter": False,
            "require_reversal_candle": False,
            "tp_mode": "nearest",
            "require_mss": False,
            "use_fvg_filter": False,
            "use_ob_filter": False,
            "use_eqhl_filter": False,
            "use_cvd_filter": False,
            "use_ote": False,
        }
    })
    
    # Test 3: Old + session filter
    tests.append({
        "name": "03_OLD_SESSION_FILTER",
        "params": {
            "use_session_filter": True,
            "require_reversal_candle": False,
            "tp_mode": "vwap",
            "require_mss": False,
            "use_fvg_filter": False,
            "use_ob_filter": False,
            "use_eqhl_filter": False,
            "use_cvd_filter": False,
            "use_ote": False,
        }
    })
    
    # Test 4: Old + nearest TP + session filter
    tests.append({
        "name": "04_OLD_NEAREST_TP_SESSION",
        "params": {
            "use_session_filter": True,
            "require_reversal_candle": False,
            "tp_mode": "nearest",
            "require_mss": False,
            "use_fvg_filter": False,
            "use_ob_filter": False,
            "use_eqhl_filter": False,
            "use_cvd_filter": False,
            "use_ote": False,
        }
    })
    
    # Test 5: Old + FVG filter
    tests.append({
        "name": "05_OLD_FVG_FILTER",
        "params": {
            "use_session_filter": False,
            "require_reversal_candle": False,
            "tp_mode": "vwap",
            "require_mss": False,
            "use_fvg_filter": True,
            "use_ob_filter": False,
            "use_eqhl_filter": False,
            "use_cvd_filter": False,
            "use_ote": False,
        }
    })
    
    # Test 6: Old + Order Block filter
    tests.append({
        "name": "06_OLD_OB_FILTER",
        "params": {
            "use_session_filter": False,
            "require_reversal_candle": False,
            "tp_mode": "vwap",
            "require_mss": False,
            "use_fvg_filter": False,
            "use_ob_filter": True,
            "use_eqhl_filter": False,
            "use_cvd_filter": False,
            "use_ote": False,
        }
    })
    
    # Test 7: Old + EQHL filter
    tests.append({
        "name": "07_OLD_EQHL_FILTER",
        "params": {
            "use_session_filter": False,
            "require_reversal_candle": False,
            "tp_mode": "vwap",
            "require_mss": False,
            "use_fvg_filter": False,
            "use_ob_filter": False,
            "use_eqhl_filter": True,
            "use_cvd_filter": False,
            "use_ote": False,
        }
    })
    
    # Test 8: Old + MSS
    tests.append({
        "name": "08_OLD_MSS",
        "params": {
            "use_session_filter": False,
            "require_reversal_candle": False,
            "tp_mode": "vwap",
            "require_mss": True,
            "use_fvg_filter": False,
            "use_ob_filter": False,
            "use_eqhl_filter": False,
            "use_cvd_filter": False,
            "use_ote": False,
        }
    })
    
    # Test 9: Old + SFP (require_reversal_candle)
    tests.append({
        "name": "09_OLD_SFP",
        "params": {
            "use_session_filter": False,
            "require_reversal_candle": True,
            "tp_mode": "vwap",
            "require_mss": False,
            "use_fvg_filter": False,
            "use_ob_filter": False,
            "use_eqhl_filter": False,
            "use_cvd_filter": False,
            "use_ote": False,
        }
    })
    
    # Test 10: Old + OTE
    tests.append({
        "name": "10_OLD_OTE",
        "params": {
            "use_session_filter": False,
            "require_reversal_candle": False,
            "tp_mode": "vwap",
            "require_mss": True,  # OTE requires MSS
            "use_fvg_filter": False,
            "use_ob_filter": False,
            "use_eqhl_filter": False,
            "use_cvd_filter": False,
            "use_ote": True,
        }
    })
    
    # Test 11: Old + CVD
    tests.append({
        "name": "11_OLD_CVD",
        "params": {
            "use_session_filter": False,
            "require_reversal_candle": False,
            "tp_mode": "vwap",
            "require_mss": False,
            "use_fvg_filter": False,
            "use_ob_filter": False,
            "use_eqhl_filter": False,
            "use_cvd_filter": True,
            "use_ote": False,
        }
    })
    
    # Test 12: Full V4 (all features)
    tests.append({
        "name": "12_FULL_V4",
        "params": {
            "use_session_filter": True,
            "require_reversal_candle": True,
            "tp_mode": "nearest",
            "require_mss": True,
            "use_fvg_filter": True,
            "use_ob_filter": True,
            "use_eqhl_filter": True,
            "use_cvd_filter": True,
            "use_ote": True,
        }
    })
    
    # Additional combinations for more data
    
    # Test 13: Session + SFP + MSS (UTM combo)
    tests.append({
        "name": "13_UTM_COMBO",
        "params": {
            "use_session_filter": True,
            "require_reversal_candle": True,
            "tp_mode": "vwap",
            "require_mss": True,
            "use_fvg_filter": False,
            "use_ob_filter": False,
            "use_eqhl_filter": False,
            "use_cvd_filter": False,
            "use_ote": False,
        }
    })
    
    # Test 14: Session + SFP + MSS + nearest TP
    tests.append({
        "name": "14_UTM_NEAREST_TP",
        "params": {
            "use_session_filter": True,
            "require_reversal_candle": True,
            "tp_mode": "nearest",
            "require_mss": True,
            "use_fvg_filter": False,
            "use_ob_filter": False,
            "use_eqhl_filter": False,
            "use_cvd_filter": False,
            "use_ote": False,
        }
    })
    
    # Test 15: Session + SFP + FVG
    tests.append({
        "name": "15_SESSION_SFP_FVG",
        "params": {
            "use_session_filter": True,
            "require_reversal_candle": True,
            "tp_mode": "vwap",
            "require_mss": False,
            "use_fvg_filter": True,
            "use_ob_filter": False,
            "use_eqhl_filter": False,
            "use_cvd_filter": False,
            "use_ote": False,
        }
    })
    
    # Test 16: Session + SFP + OB
    tests.append({
        "name": "16_SESSION_SFP_OB",
        "params": {
            "use_session_filter": True,
            "require_reversal_candle": True,
            "tp_mode": "vwap",
            "require_mss": False,
            "use_fvg_filter": False,
            "use_ob_filter": True,
            "use_eqhl_filter": False,
            "use_cvd_filter": False,
            "use_ote": False,
        }
    })
    
    # Test 17: Session + SFP + EQHL
    tests.append({
        "name": "17_SESSION_SFP_EQHL",
        "params": {
            "use_session_filter": True,
            "require_reversal_candle": True,
            "tp_mode": "vwap",
            "require_mss": False,
            "use_fvg_filter": False,
            "use_ob_filter": False,
            "use_eqhl_filter": True,
            "use_cvd_filter": False,
            "use_ote": False,
        }
    })
    
    # Test 18: All signal quality filters
    tests.append({
        "name": "18_ALL_SQ_FILTERS",
        "params": {
            "use_session_filter": True,
            "require_reversal_candle": False,
            "tp_mode": "vwap",
            "require_mss": False,
            "use_fvg_filter": True,
            "use_ob_filter": True,
            "use_eqhl_filter": True,
            "use_cvd_filter": True,
            "use_ote": False,
        }
    })
    
    # Test 19: Full V4 with PDH instead of nearest
    tests.append({
        "name": "19_FULL_V4_PDH",
        "params": {
            "use_session_filter": True,
            "require_reversal_candle": True,
            "tp_mode": "pdh",
            "require_mss": True,
            "use_fvg_filter": True,
            "use_ob_filter": True,
            "use_eqhl_filter": True,
            "use_cvd_filter": True,
            "use_ote": True,
        }
    })
    
    # Test 20: Optimized baseline (session + SFP but no other filters)
    tests.append({
        "name": "20_OPTIMIZED_BASELINE",
        "params": {
            "use_session_filter": True,
            "require_reversal_candle": True,
            "tp_mode": "nearest",
            "require_mss": False,
            "use_fvg_filter": False,
            "use_ob_filter": False,
            "use_eqhl_filter": False,
            "use_cvd_filter": False,
            "use_ote": False,
        }
    })
    
    return tests


def print_results_table(results: list[dict]):
    """Print formatted results table."""
    print("\n" + "=" * 120)
    print("VWAP SCALPING V4 COMPREHENSIVE SWEEP RESULTS")
    print("=" * 120)
    print(f"{'Test':<30} {'Trades':>8} {'Win%':>8} {'PF':>8} {'Net%':>10} {'Sharpe':>8} {'MaxDD%':>8}")
    print("-" * 120)
    
    for r in results:
        name = r["name"]
        trades = r.get("trades", 0)
        win_rate = r.get("win_rate", 0)
        pf = r.get("profit_factor", 0)
        net_ret = r.get("net_return", 0)
        sharpe = r.get("sharpe", 0)
        max_dd = r.get("max_dd", 0)
        
        if r.get("error"):
            print(f"{name:<30} {'ERROR':>8} - {r.get('error', '')[:60]}")
            continue
        
        if r.get("trades", 0) == 0:
            print(f"{name:<30} {'NO TRADES':>8}")
            continue
        
        # Highlight rows meeting goals
        marker = ""
        if trades >= 20 and win_rate >= 48 and net_ret > 0:
            marker = " ***"
        elif trades >= 20 and net_ret > 0:
            marker = " **"
        elif trades >= 20:
            marker = " *"
        
        print(f"{name:<30} {trades:>8} {win_rate:>7.1f}% {pf:>8.2f} {net_ret:>9.2f}% {sharpe:>8.2f} {max_dd:>7.1f}%{marker}")
    
    print("=" * 120)
    print("\nGoals: Trades >= 20, Win Rate >= 48%, Net Return > 0%")
    print("Legend: *** = All goals met, ** = Profitable, * = Has trades")


def analyze_results(results: list[dict]):
    """Analyze results and provide summary."""
    print("\n" + "=" * 80)
    print("ANALYSIS SUMMARY")
    print("=" * 80)
    
    # Filter out errors and empty results
    valid = [r for r in results if not r.get("error") and r.get("trades", 0) > 0]
    
    if not valid:
        print("No valid results to analyze!")
        return
    
    # Best by different metrics
    best_sharpe = max(valid, key=lambda x: x.get("sharpe", -999))
    best_winrate = max(valid, key=lambda x: x.get("win_rate", 0))
    best_pf = max(valid, key=lambda x: x.get("profit_factor", 0))
    best_return = max(valid, key=lambda x: x.get("net_return", -999))
    most_trades = max(valid, key=lambda x: x.get("trades", 0))
    
    print(f"\nBest Sharpe Ratio:  {best_sharpe['name']} ({best_sharpe.get('sharpe', 0):.2f})")
    print(f"Best Win Rate:     {best_winrate['name']} ({best_winrate.get('win_rate', 0):.1f}%)")
    print(f"Best Profit Factor: {best_pf['name']} ({best_pf.get('profit_factor', 0):.2f})")
    print(f"Best Net Return:   {best_return['name']} ({best_return.get('net_return', 0):.2f}%)")
    print(f"Most Trades:       {most_trades['name']} ({most_trades.get('trades', 0)})")
    
    # Count tests meeting goals
    meets_all = sum(1 for r in valid if r.get("trades", 0) >= 20 and r.get("win_rate", 0) >= 48 and r.get("net_return", 0) > 0)
    meets_trades = sum(1 for r in valid if r.get("trades", 0) >= 20)
    meets_winrate = sum(1 for r in valid if r.get("win_rate", 0) >= 48)
    meets_return = sum(1 for r in valid if r.get("net_return", 0) > 0)
    
    print(f"\nTests meeting goals:")
    print(f"  - All goals (trades>=20, win>=48%, return>0): {meets_all}/{len(valid)}")
    print(f"  - Trades >= 20:  {meets_trades}/{len(valid)}")
    print(f"  - Win rate >= 48%:  {meets_winrate}/{len(valid)}")
    print(f"  - Net return > 0%:  {meets_return}/{len(valid)}")
    
    # Feature impact analysis
    print("\n" + "-" * 80)
    print("FEATURE IMPACT ANALYSIS")
    print("-" * 80)
    
    # Group results by feature
    features = [
        ("session_filter", "use_session_filter"),
        ("reversal_candle", "require_reversal_candle"),
        ("mss", "require_mss"),
        ("fvg_filter", "use_fvg_filter"),
        ("ob_filter", "use_ob_filter"),
        ("eqhl_filter", "use_eqhl_filter"),
        ("cvd_filter", "use_cvd_filter"),
        ("ote", "use_ote"),
    ]
    
    for feat_name, param_name in features:
        with_feat = [r for r in valid if r["config"].get(param_name, False)]
        without_feat = [r for r in valid if not r["config"].get(param_name, False)]
        
        if with_feat and without_feat:
            avg_return_with = sum(r.get("net_return", 0) for r in with_feat) / len(with_feat)
            avg_return_without = sum(r.get("net_return", 0) for r in without_feat) / len(without_feat)
            avg_trades_with = sum(r.get("trades", 0) for r in with_feat) / len(with_feat)
            avg_trades_without = sum(r.get("trades", 0) for r in without_feat) / len(without_feat)
            
            impact = avg_return_with - avg_return_without
            trade_impact = avg_trades_with - avg_trades_without
            
            print(f"\n{feat_name}:")
            print(f"  Avg Return: {avg_return_with:.2f}% (with) vs {avg_return_without:.2f}% (without) | Impact: {impact:+.2f}%")
            print(f"  Avg Trades: {avg_trades_with:.1f} (with) vs {avg_trades_without:.1f} (without) | Impact: {trade_impact:+.1f}")


def main():
    """Main function to run all tests."""
    print("=" * 80)
    print(f"VWAP SCALPING V4 COMPREHENSIVE SWEEP")
    print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    # Load data
    df = load_data()
    
    # Pre-compute order flow data
    order_flow_data = compute_order_flow_data(df)
    
    # Get test configurations
    tests = get_test_configurations()
    print(f"\nRunning {len(tests)} test configurations...")
    
    # Run all tests
    results = []
    start_time = time.time()
    
    for i, test in enumerate(tests, 1):
        test_start = time.time()
        print(f"\n[{i}/{len(tests)}] Running: {test['name']}...")
        
        result = run_test(df, test["name"], test["params"], order_flow_data)
        results.append(result)
        
        test_time = time.time() - test_start
        print(f"  -> Trades: {result.get('trades', 0)}, Win%: {result.get('win_rate', 0):.1f}, "
              f"Net%: {result.get('net_return', 0):.2f}, PF: {result.get('profit_factor', 0):.2f}, "
              f"Time: {test_time:.1f}s")
    
    total_time = time.time() - start_time
    
    # Print results
    print_results_table(results)
    analyze_results(results)
    
    print(f"\nTotal time: {total_time:.1f}s ({total_time/60:.1f} minutes)")
    print(f"Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Save results to CSV
    csv_path = Path(__file__).parent / "vwap_v4_sweep_results.csv"
    df_results = pd.DataFrame(results)
    df_results.to_csv(csv_path, index=False)
    print(f"\nResults saved to: {csv_path}")


if __name__ == "__main__":
    main()
