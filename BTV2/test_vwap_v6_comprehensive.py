"""
test_vwap_v6_comprehensive.py
============================
VWAP v6 Comprehensive Test - Systematic Configuration Testing

Uses built-in run_vwap_scalping function with VS_DEFAULTS as baseline.

Test Configurations:
A. v6 Baseline (VS_DEFAULTS)
B. v6 with session filter disabled
C. v6 with Phase 1 features (levels + nearest TP)
D. v6 with Phase 3 features (CVD filter or MSS)
E. Grid search over key parameters

Uses walk-forward validation (3m train / 1m test) for realistic OOS results.
Uses 1m exit resolution for accurate SL/TP detection.
Cost model: 0.30% per side (0.60% round trip)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List

import numpy as np
import pandas as pd

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.strategies import (
    run_vwap_scalping,
    compute_reference_levels,
    compute_metrics,
    COST_PER_SIDE,
    INTERVAL_BARS_PER_YEAR,
    VS_DEFAULTS,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR = Path("G:/Candle Data")
RESULTS_DIR = Path(__file__).parent / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Test period: 2023-2025 for efficiency
TEST_START = "2023-01-01"
TEST_END = "2026-01-01"

# Butterworth cutoff for noise filtering
CUTOFF = 0.10

# Cost model: 0.30% per side = 0.60% round trip
COST_PER_SIDE_TEST = 0.003  # 0.30%

# Walk-forward windows
TRAIN_MONTHS = 3
TEST_MONTHS = 1

# Minimum criteria
MIN_TRADES = 10
MIN_WIN_RATE = 40.0
MIN_NET_PCT = -10.0


# ─────────────────────────────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────────────────────────────

def load_data() -> Tuple[pd.DataFrame, Optional[pd.DataFrame]]:
    """Load 5m and 1m data for backtesting."""
    path_5m = DATA_DIR / "BTCUSDT_5m.parquet"
    df_5m = pd.read_parquet(path_5m)
    df_5m.index = pd.to_datetime(df_5m.index, utc=True)
    start_ts = pd.Timestamp(TEST_START, tz="UTC")
    end_ts = pd.Timestamp(TEST_END, tz="UTC")
    mask_5m = (df_5m.index >= start_ts) & (df_5m.index < end_ts)
    df_5m = df_5m.loc[mask_5m].copy()
    
    # Load 1m for exit resolution
    path_1m = DATA_DIR / "BTCUSDT_1m.parquet"
    df_1m = None
    if path_1m.exists():
        df_1m_tmp = pd.read_parquet(path_1m)
        df_1m_tmp.index = pd.to_datetime(df_1m_tmp.index, utc=True)
        mask_1m = (df_1m_tmp.index >= start_ts) & (df_1m_tmp.index < end_ts)
        df_1m = df_1m_tmp.loc[mask_1m].copy()
    
    return df_5m, df_1m


def load_walk_forward_window(
    df_5m: pd.DataFrame,
    df_1m: Optional[pd.DataFrame],
    train_start: pd.Timestamp,
    train_end: pd.Timestamp,
    test_start: pd.Timestamp,
    test_end: pd.Timestamp,
) -> Tuple[pd.DataFrame, pd.DataFrame, Optional[pd.DataFrame]]:
    """Load data for a specific walk-forward window."""
    train_mask = (df_5m.index >= train_start) & (df_5m.index < train_end)
    test_mask = (df_5m.index >= test_start) & (df_5m.index < test_end)
    
    df_train = df_5m.loc[train_mask].copy()
    df_test = df_5m.loc[test_mask].copy()
    
    df_exit = None
    if df_1m is not None:
        exit_mask = (df_1m.index >= test_start) & (df_1m.index < test_end)
        df_exit = df_1m.loc[exit_mask].copy()
    
    return df_train, df_test, df_exit


# ─────────────────────────────────────────────────────────────────────────────
# TEST CONFIGURATIONS
# ─────────────────────────────────────────────────────────────────────────────

def get_test_configs() -> Dict[str, Dict[str, Any]]:
    """Define all test configurations."""
    configs = {}
    
    # A. v6 Baseline with VS_DEFAULTS
    configs["A_baseline"] = {
        **VS_DEFAULTS,
    }
    
    # B. Session filter disabled
    configs["B_no_session"] = {
        **VS_DEFAULTS,
        "use_session_filter": False,
    }
    
    # C. Phase 1 features: levels + nearest TP (computed during test)
    configs["C_phase1_levels"] = {
        **VS_DEFAULTS,
        "tp_mode": "nearest",  # requires levels
    }
    
    # D. Phase 3 features: CVD filter
    configs["D_phase3_cvd"] = {
        **VS_DEFAULTS,
        "use_cvd_filter": True,
        "cvd_window": 20,
    }
    
    # E. Phase 3 features: MSS confirmation
    configs["D_phase3_mss"] = {
        **VS_DEFAULTS,
        "require_mss": True,
        "mss_timeout_bars": 6,
    }
    
    return configs


def get_grid_configs() -> List[Dict[str, Any]]:
    """Generate grid search configurations."""
    grid_configs = []
    
    # Grid parameters
    sd_thresholds = [2.5, 3.0, 3.5, 4.0]
    entry_modes = ["mean_reversion", "cross", "bull_pullback", "bear_pullback"]
    tp_modes = ["atr", "nearest"]  # nearest requires levels
    session_filters = [True, False]
    
    # Generate all combinations
    for sd in sd_thresholds:
        for entry in entry_modes:
            for tp in tp_modes:
                for session in session_filters:
                    # Skip invalid combos
                    if tp == "nearest":
                        # levels will be added at runtime
                        config = {
                            **VS_DEFAULTS,
                            "sd_threshold": sd,
                            "entry_mode": entry,
                            "tp_mode": tp,
                            "use_session_filter": session,
                            "levels": None,  # computed at runtime
                        }
                    else:
                        config = {
                            **VS_DEFAULTS,
                            "sd_threshold": sd,
                            "entry_mode": entry,
                            "tp_mode": tp,
                            "use_session_filter": session,
                        }
                    grid_configs.append(config)
    
    return grid_configs


# ─────────────────────────────────────────────────────────────────────────────
# BACKTEST ENGINE
# ──────────────────────────��──────────────────────────────────────────────────

def run_backtest(
    df: pd.DataFrame,
    df_exit: Optional[pd.DataFrame],
    config: Dict[str, Any],
    levels: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Run VWAP backtest with specified configuration."""
    
    # Prepare parameters
    params = config.copy()
    
    # Add levels if tp_mode is nearest
    if params.get("tp_mode") == "nearest" and levels is not None:
        params["levels"] = levels
    
    # Remove non-parameter keys
    params = {k: v for k, v in params.items() if v is not None or k != "levels"}
    
    # Run strategy
    equity, trades = run_vwap_scalping(df, CUTOFF, df_exit=df_exit, **params)
    
    if len(equity) < 2 or len(trades) == 0:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "net_pct": 0.0,
            "sharpe": 0.0,
            "max_dd": 0.0,
            "profit_factor": 0.0,
            "equity": equity,
            "trades_list": trades,
        }
    
    # Compute metrics
    bars_per_year = INTERVAL_BARS_PER_YEAR.get("5m", 105120)
    m = compute_metrics(equity, trades, bars_per_year=bars_per_year)
    
    return {
        "trades": m.get("n_trades", 0),
        "win_rate": m.get("win_rate_pct", 0.0),
        "net_pct": m.get("total_return_pct", 0.0),
        "sharpe": m.get("sharpe", 0.0),
        "max_dd": m.get("max_dd_pct", 0.0),
        "profit_factor": m.get("profit_factor", 0.0),
        "equity": equity,
        "trades_list": trades,
    }


def run_walk_forward(
    df_5m: pd.DataFrame,
    df_1m: Optional[pd.DataFrame],
    config: Dict[str, Any],
    use_levels: bool = False,
) -> Dict[str, Any]:
    """Run walk-forward validation."""
    
    results = []
    
    # Calculate walk-forward windows
    start_date = pd.Timestamp(TEST_START, tz="UTC")
    end_date = pd.Timestamp(TEST_END, tz="UTC")
    
    current = start_date
    while current < end_date:
        # Train window
        train_end = current + pd.DateOffset(months=TRAIN_MONTHS)
        test_start = train_end
        test_end = test_start + pd.DateOffset(months=TEST_MONTHS)
        
        if test_end > end_date:
            break
        
        # Load data
        df_train, df_test, df_exit = load_walk_forward_window(
            df_5m, df_1m,
            current, train_end,
            test_start, test_end
        )
        
        if len(df_train) < 100 or len(df_test) < 50:
            current = train_end
            continue
        
        # Compute levels if needed
        levels = None
        if use_levels:
            levels = compute_reference_levels(df_train)
        
        # Run backtest on train (in-sample for optimization)
        train_result = run_backtest(df_train, None, config, levels)
        
        # Run backtest on test (out-of-sample)
        test_result = run_backtest(df_test, df_exit, config, levels)
        
        results.append({
            "train_start": current,
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
            "train_trades": train_result["trades"],
            "train_wr": train_result["win_rate"],
            "train_net": train_result["net_pct"],
            "train_sharpe": train_result["sharpe"],
            "test_trades": test_result["trades"],
            "test_wr": test_result["win_rate"],
            "test_net": test_result["net_pct"],
            "test_sharpe": test_result["sharpe"],
            "test_max_dd": test_result["max_dd"],
        })
        
        current = train_end
    
    if not results:
        return {
            "n_windows": 0,
            "total_trades": 0,
            "avg_train_wr": 0.0,
            "avg_test_wr": 0.0,
            "avg_oos_return": 0.0,
            "avg_oos_sharpe": 0.0,
            "max_dd": 0.0,
        }
    
    # Aggregate results
    n_windows = len(results)
    total_trades = sum(r["test_trades"] for r in results)
    avg_train_wr = np.mean([r["train_wr"] for r in results])
    avg_test_wr = np.mean([r["test_wr"] for r in results])
    avg_oos_return = np.mean([r["test_net"] for r in results])
    avg_oos_sharpe = np.mean([r["test_sharpe"] for r in results])
    max_dd = max([r["test_max_dd"] for r in results]) if results else 0.0
    
    return {
        "n_windows": n_windows,
        "total_trades": total_trades,
        "avg_train_wr": avg_train_wr,
        "avg_test_wr": avg_test_wr,
        "avg_oos_return": avg_oos_return,
        "avg_oos_sharpe": avg_oos_sharpe,
        "max_dd": max_dd,
        "window_results": results,
    }


# ─────────────────────────────────────────────────────────────────────────────
# MAIN TEST RUNNER
# ─────────────────────────────────────────────────────────────────────────────

def print_results_table(results: Dict[str, Dict[str, Any]], title: str = "Results"):
    """Print results in table format."""
    print(f"\n{'=' * 120}")
    print(f"{title:^120}")
    print(f"{'=' * 120}")
    print(f"{'Config':<25} {'Trades':>8} {'WR%':>8} {'Return%':>10} {'Sharpe':>8} {'Max DD%':>10}")
    print(f"{'-' * 120}")
    
    # Sort by Sharpe descending
    sorted_configs = sorted(
        results.items(),
        key=lambda x: x[1].get("sharpe", 0),
        reverse=True
    )
    
    for config_name, metrics in sorted_configs:
        print(
            f"{config_name:<25} "
            f"{metrics['trades']:>8} "
            f"{metrics['win_rate']:>8.1f} "
            f"{metrics['net_pct']:>10.2f} "
            f"{metrics['sharpe']:>8.2f} "
            f"{metrics['max_dd']:>10.2f}"
        )
    
    print(f"{'=' * 120}")


def run_phase_tests(
    df_5m: pd.DataFrame,
    df_1m: Optional[pd.DataFrame],
) -> Dict[str, Dict[str, Any]]:
    """Run systematic phase tests."""
    
    print("\n" + "=" * 80)
    print("RUNNING PHASE TESTS")
    print("=" * 80)
    
    results = {}
    configs = get_test_configs()
    
    # Compute levels once for all configs that need them
    levels = compute_reference_levels(df_5m)
    
    for config_name, config in configs.items():
        print(f"\nRunning {config_name}...")
        start = time.time()
        
        # Determine if this config needs levels
        use_levels = config.get("tp_mode") == "nearest"
        config_levels = levels if use_levels else None
        
        # Run walk-forward
        wf_result = run_walk_forward(df_5m, df_1m, config, use_levels)
        
        # Also run full backtest for comparison
        full_result = run_backtest(df_5m, df_1m, config, config_levels)
        
        results[config_name] = {
            "trades": full_result["trades"],
            "win_rate": full_result["win_rate"],
            "net_pct": full_result["net_pct"],
            "sharpe": full_result["sharpe"],
            "max_dd": full_result["max_dd"],
            "profit_factor": full_result["profit_factor"],
            "oos_sharpe": wf_result["avg_oos_sharpe"],
            "oos_return": wf_result["avg_oos_return"],
            "oos_wr": wf_result["avg_test_wr"],
            "oos_max_dd": wf_result["max_dd"],
        }
        
        elapsed = time.time() - start
        print(
            f"  Trades: {full_result['trades']}, "
            f"WR: {full_result['win_rate']:.1f}%, "
            f"Net: {full_result['net_pct']:.2f}%, "
            f"Sharpe: {full_result['sharpe']:.2f}, "
            f"OOS Sharpe: {wf_result['avg_oos_sharpe']:.2f}, "
            f"Time: {elapsed:.1f}s"
        )
    
    return results


def run_grid_search(
    df_5m: pd.DataFrame,
    df_1m: Optional[pd.DataFrame],
    max_configs: int = 20,
) -> Dict[str, Dict[str, Any]]:
    """Run grid search over key parameters."""
    
    print("\n" + "=" * 80)
    print("RUNNING GRID SEARCH")
    print("=" * 80)
    
    results = {}
    grid_configs = get_grid_configs()[:max_configs]
    
    # Compute levels once
    levels = compute_reference_levels(df_5m)
    
    for i, config in enumerate(grid_configs):
        config_name = f"grid_{i+1}"
        print(f"\nRunning {config_name} ({i+1}/{len(grid_configs)})...")
        start = time.time()
        
        # Determine if this config needs levels
        use_levels = config.get("tp_mode") == "nearest"
        config_levels = levels if use_levels else None
        
        # Run full backtest
        result = run_backtest(df_5m, df_1m, config, config_levels)
        
        # Skip if too few trades
        if result["trades"] < MIN_TRADES:
            print(f"  Skipping: only {result['trades']} trades")
            continue
        
        results[config_name] = {
            "trades": result["trades"],
            "win_rate": result["win_rate"],
            "net_pct": result["net_pct"],
            "sharpe": result["sharpe"],
            "max_dd": result["max_dd"],
            "profit_factor": result["profit_factor"],
            "config": config,
        }
        
        elapsed = time.time() - start
        print(
            f"  SD: {config['sd_threshold']}, "
            f"Entry: {config['entry_mode']}, "
            f"TP: {config['tp_mode']}, "
            f"Session: {config['use_session_filter']}, "
            f"Trades: {result['trades']}, "
            f"WR: {result['win_rate']:.1f}%, "
            f"Net: {result['net_pct']:.2f}%, "
            f"Sharpe: {result['sharpe']:.2f}, "
            f"Time: {elapsed:.1f}s"
        )
    
    return results


def analyze_session_filter_impact(
    results: Dict[str, Dict[str, Any]],
) -> None:
    """Analyze session filter impact."""
    
    print("\n" + "=" * 80)
    print("SESSION FILTER IMPACT ANALYSIS")
    print("=" * 80)
    
    # Compare baseline vs no_session
    baseline = results.get("A_baseline", {})
    no_session = results.get("B_no_session", {})
    
    if baseline and no_session:
        print(f"\nBaseline (session filter ON):")
        print(f"  Trades: {baseline['trades']}")
        print(f"  Win Rate: {baseline['win_rate']:.1f}%")
        print(f"  Net Return: {baseline['net_pct']:.2f}%")
        print(f"  Sharpe: {baseline['sharpe']:.2f}")
        
        print(f"\nNo Session Filter (session filter OFF):")
        print(f"  Trades: {no_session['trades']}")
        print(f"  Win Rate: {no_session['win_rate']:.1f}%")
        print(f"  Net Return: {no_session['net_pct']:.2f}%")
        print(f"  Sharpe: {no_session['sharpe']:.2f}")
        
        trade_diff = no_session['trades'] - baseline['trades']
        wr_diff = no_session['win_rate'] - baseline['win_rate']
        net_diff = no_session['net_pct'] - baseline['net_pct']
        sharpe_diff = no_session['sharpe'] - baseline['sharpe']
        
        print(f"\nImpact:")
        print(f"  Trade difference: {trade_diff:+d}")
        print(f"  WR difference: {wr_diff:+.1f}%")
        print(f"  Net return difference: {net_diff:+.2f}%")
        print(f"  Sharpe difference: {sharpe_diff:+.2f}")
        
        if net_diff > 0:
            print("\n=> Session filter HURTS performance")
        else:
            print("\n=> Session filter HELPS performance")


def analyze_phase_features_impact(
    results: Dict[str, Dict[str, Any]],
) -> None:
    """Analyze phase features impact."""
    
    print("\n" + "=" * 80)
    print("PHASE FEATURES IMPACT ANALYSIS")
    print("=" * 80)
    
    baseline = results.get("A_baseline", {})
    
    if not baseline:
        return
    
    print(f"\nBaseline (v6 defaults):")
    print(f"  Trades: {baseline['trades']}")
    print(f"  Win Rate: {baseline['win_rate']:.1f}%")
    print(f"  Net Return: {baseline['net_pct']:.2f}%")
    print(f"  Sharpe: {baseline['sharpe']:.2f}")
    
    # Phase 1 features: levels + nearest TP
    phase1 = results.get("C_phase1_levels", {})
    if phase1:
        print(f"\nPhase 1 (levels + nearest TP):")
        print(f"  Trades: {phase1['trades']}")
        print(f"  Win Rate: {phase1['win_rate']:.1f}%")
        print(f"  Net Return: {phase1['net_pct']:.2f}%")
        print(f"  Sharpe: {phase1['sharpe']:.2f}")
        
        net_diff = phase1['net_pct'] - baseline['net_pct']
        sharpe_diff = phase1['sharpe'] - baseline['sharpe']
        
        print(f"  Impact: Net {net_diff:+.2f}%, Sharpe {sharpe_diff:+.2f}")
    
    # Phase 3 features: CVD filter
    phase3_cvd = results.get("D_phase3_cvd", {})
    if phase3_cvd:
        print(f"\nPhase 3 (CVD filter):")
        print(f"  Trades: {phase3_cvd['trades']}")
        print(f"  Win Rate: {phase3_cvd['win_rate']:.1f}%")
        print(f"  Net Return: {phase3_cvd['net_pct']:.2f}%")
        print(f"  Sharpe: {phase3_cvd['sharpe']:.2f}")
        
        net_diff = phase3_cvd['net_pct'] - baseline['net_pct']
        sharpe_diff = phase3_cvd['sharpe'] - baseline['sharpe']
        
        print(f"  Impact: Net {net_diff:+.2f}%, Sharpe {sharpe_diff:+.2f}")
    
    # Phase 3 features: MSS
    phase3_mss = results.get("D_phase3_mss", {})
    if phase3_mss:
        print(f"\nPhase 3 (MSS confirmation):")
        print(f"  Trades: {phase3_mss['trades']}")
        print(f"  Win Rate: {phase3_mss['win_rate']:.1f}%")
        print(f"  Net Return: {phase3_mss['net_pct']:.2f}%")
        print(f"  Sharpe: {phase3_mss['sharpe']:.2f}")
        
        net_diff = phase3_mss['net_pct'] - baseline['net_pct']
        sharpe_diff = phase3_mss['sharpe'] - baseline['sharpe']
        
        print(f"  Impact: Net {net_diff:+.2f}%, Sharpe {sharpe_diff:+.2f}")


def find_best_config(
    results: Dict[str, Dict[str, Any]],
) -> Tuple[str, Dict[str, Any]]:
    """Find the best overall configuration."""
    
    best_name = None
    best_sharpe = float("-inf")
    best_result = None
    
    for config_name, metrics in results.items():
        sharpe = metrics.get("sharpe", 0)
        trades = metrics.get("trades", 0)
        
        # Only consider configs with minimum trades
        if trades >= MIN_TRADES and sharpe > best_sharpe:
            best_sharpe = sharpe
            best_name = config_name
            best_result = metrics
    
    return best_name, best_result


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    start_time = time.time()
    
    print("=" * 80)
    print("VWAP v6 COMPREHENSIVE TEST")
    print("=" * 80)
    print(f"\nTest Period: {TEST_START} to {TEST_END}")
    print(f"Cutoff: {CUTOFF}")
    print(f"Cost per side: {COST_PER_SIDE_TEST:.3f} ({COST_PER_SIDE_TEST*100:.1f}%)")
    print(f"Walk-forward: {TRAIN_MONTHS}m train / {TEST_MONTHS}m test")
    
    # Load data
    print("\nLoading data...")
    df_5m, df_1m = load_data()
    print(f"  5m bars: {len(df_5m):,}")
    print(f"  1m bars: {len(df_1m) if df_1m is not None else 0:,}")
    
    # Run phase tests
    phase_results = run_phase_tests(df_5m, df_1m)
    
    # Run limited grid search
    grid_results = run_grid_search(df_5m, df_1m, max_configs=32)
    
    # Combine results
    all_results = {**phase_results}
    for k, v in grid_results.items():
        all_results[f"grid_{k}"] = v
    
    # Print results table
    print_results_table(phase_results, "PHASE TEST RESULTS")
    
    # Find best config
    best_name, best_result = find_best_config(all_results)
    
    if best_name and best_result:
        print("\n" + "=" * 80)
        print("BEST OVERALL CONFIGURATION")
        print("=" * 80)
        print(f"\nConfig: {best_name}")
        print(f"  Trades: {best_result['trades']}")
        print(f"  Win Rate: {best_result['win_rate']:.1f}%")
        print(f"  Net Return: {best_result['net_pct']:.2f}%")
        print(f"  Sharpe: {best_result['sharpe']:.2f}")
        print(f"  Max DD: {best_result['max_dd']:.2f}%")
        print(f"  Profit Factor: {best_result['profit_factor']:.2f}")
    
    # Analyze session filter impact
    analyze_session_filter_impact(phase_results)
    
    # Analyze phase features impact
    analyze_phase_features_impact(phase_results)
    
    # Summary
    elapsed = time.time() - start_time
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"\nTotal time: {elapsed:.1f}s ({elapsed/60:.1f}m)")
    print(f"Configurations tested: {len(all_results)}")
    
    # Save results to CSV
    results_df = pd.DataFrame(all_results).T
    results_df.index.name = "config"
    results_df.to_csv(RESULTS_DIR / "vwap_v6_comprehensive_results.csv")
    print(f"\nResults saved to: {RESULTS_DIR / 'vwap_v6_comprehensive_results.csv'}")


if __name__ == "__main__":
    main()