"""
VWAP Scalping Two-Stage Multivariate Optimization (OPTIMIZED)
==============================================================

Stage 1: Full Multivariate Trade Discovery
- Use reduced variable combinations for speed
- Target: Find configs with 50-500 trades and >=30% WR

Stage 2: R:R Optimization on top configs

Data: BTCUSDT 5m for 2024 (for speed), 1m exit resolution
"""

from __future__ import annotations

import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm

# Import strategies for the backtest engine
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from strategies import run_vwap_scalping
from data_manager import load_local


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION - OPTIMIZED FOR SPEED
# ─────────────────────────────────────────────────────────────────────────────

# Stage 1 Variables - REDUCED for faster execution
# Using fewer values but covering key ranges
STAGE1_VARS = {
    "sd_threshold": [1.5, 2.0, 2.5],        # 3 values (was 5)
    "entry_mode": ["bull_pullback", "mean_reversion", "cross"],  # 3 modes (was 5)
    "adx_max": [25, 35],                    # 2 values (was 4)
    "rsi_max": [40, 60],                    # 2 values (was 3)
    "volume_mult": [1.0, 1.5],              # 2 values (was 3)
    "use_session_filter": [True, False],    # 2 values
    "require_reversal_candle": [False],      # Just False for speed
    "require_mss": [False],                 # Just False for speed
}

# Fixed Parameters (for Stage 1)
FIXED_PARAMS = {
    "cutoff": 0.1,
    "atr_stop": 0.7,
    "atr_target": 3.5,
    "ema_fast": 9,
    "ema_slow": 20,
    "use_trend_filter": True,
    "use_volume_filter": True,
    "use_trailing_stop": True,
    "trailing_atr": 1.2,
    "deviation_pct": 0.5,
    "momentum_bars": 2,
    "pullback_bars": 3,
    "use_htf_vwap": True,
    "use_htf_ema": False,
    "htf_adx_max": 25,
    "htf_vwap_interval": "15min",
    "htf_ema_interval": "60min",
    "stoch_oversold": 40,
    "stoch_overbought": 60,
    "use_anchored_vwap": True,
    "tp_mode": "vwap",
    "mss_timeout_bars": 6,
    "use_fvg_filter": False,
    "use_ob_filter": False,
    "use_eqhl_filter": False,
    "fvg_proximity_atr": 0.5,
    "use_cvd_filter": False,
    "cvd_window": 20,
    "use_ote": False,
    "use_dynamic_mode": False,
}

# Stage 2: R:R Ratios to test on top configs
STAGE2_RR_RATIOS = [
    {"atr_stop": 0.5, "atr_target": 1.5, "rr_ratio": "3:1"},
    {"atr_stop": 0.7, "atr_target": 2.1, "rr_ratio": "3:1"},
    {"atr_stop": 0.5, "atr_target": 2.5, "rr_ratio": "5:1"},
    {"atr_stop": 0.7, "atr_target": 3.5, "rr_ratio": "5:1"},
    {"atr_stop": 1.0, "atr_target": 3.0, "rr_ratio": "3:1"},
    {"atr_stop": 1.0, "atr_target": 5.0, "rr_ratio": "5:1"},
]

# Output paths
OUTPUT_DIR = Path(__file__).parent / "results"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Number of parallel workers
NUM_WORKERS = 4


# ─────────────────────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

def calculate_metrics(closed_trades: list[float]) -> dict:
    """Calculate comprehensive metrics from closed trades."""
    if not closed_trades:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "net_return": 0.0,
        }
    
    wins = [t for t in closed_trades if t > 0]
    losses = [t for t in closed_trades if t < 0]
    
    total_wins = sum(wins) if wins else 0
    total_losses = abs(sum(losses)) if losses else 0
    
    profit_factor = total_wins / (total_losses + 1e-10)
    win_rate = len(wins) / len(closed_trades) * 100
    
    net_return = sum(closed_trades) * 100
    
    return {
        "trades": len(closed_trades),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "net_return": net_return,
    }


def run_single_backtest(args: tuple) -> dict:
    """Run a single backtest with given parameters."""
    df_5m, df_1m, params = args
    
    # Merge fixed params with variable params
    full_params = {**FIXED_PARAMS, **params}
    
    try:
        equity, closed_trades = run_vwap_scalping(
            df=df_5m,
            df_exit=df_1m,
            **full_params,
        )
        metrics = calculate_metrics(closed_trades)
        return {"success": True, "params": params, "metrics": metrics}
    except Exception as e:
        return {"success": False, "params": params, "error": str(e)}


def generate_param_combinations(var_dict: dict) -> list[dict]:
    """Generate all combinations of variables."""
    keys = list(var_dict.keys())
    values = list(var_dict.values())
    combinations = list(product(*values))
    return [dict(zip(keys, combo)) for combo in combinations]


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 1: MULTIVARIATE SWEEP
# ─────────────────────────────────────────────────────────────────────────────

def run_stage1(df_5m: pd.DataFrame, df_1m: pd.DataFrame) -> pd.DataFrame:
    """Run Stage 1: Full multivariate trade discovery."""
    print("\n" + "="*80)
    print("STAGE 1: Full Multivariate Trade Discovery (OPTIMIZED)")
    print("="*80)
    
    # Generate all parameter combinations
    param_combinations = generate_param_combinations(STAGE1_VARS)
    total_combinations = len(param_combinations)
    
    print(f"Total parameter combinations to test: {total_combinations}")
    print(f"Variables: {list(STAGE1_VARS.keys())}")
    print(f"Parallel workers: {NUM_WORKERS}")
    print()
    
    # Prepare args for parallel execution
    args_list = [(df_5m, df_1m, params) for params in param_combinations]
    
    results = []
    start_time = time.time()
    
    # Run in parallel
    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {executor.submit(run_single_backtest, args): i for i, args in enumerate(args_list)}
        
        completed = 0
        for future in tqdm(as_completed(futures), total=total_combinations, desc="Stage 1"):
            result = future.result()
            completed += 1
            
            if result["success"]:
                metrics = result["metrics"]
                
                # Only include configs with 50-500 trades and >=30% WR
                if 50 <= metrics["trades"] <= 500 and metrics["win_rate"] >= 30.0:
                    row = {
                        "rank": 0,  # Will be set later
                        "stage": 1,
                        **result["params"],
                        "trades": metrics["trades"],
                        "win_rate": metrics["win_rate"],
                        "profit_factor": metrics["profit_factor"],
                        "net_return": metrics["net_return"],
                    }
                    results.append(row)
            
            # Progress update every 100 iterations
            if completed % 100 == 0:
                elapsed = time.time() - start_time
                rate = completed / elapsed if elapsed > 0 else 1
                remaining = (total_combinations - completed) / rate
                print(f"\n  Progress: {completed}/{total_combinations} ({100*completed/total_combinations:.1f}%)")
                print(f"  Elapsed: {elapsed/60:.1f} min, Est. remaining: {remaining/60:.1f} min")
                print(f"  Valid configs so far: {len(results)}")
    
    # Sort by trade count (descending) and assign ranks
    results_df = pd.DataFrame(results)
    if not results_df.empty:
        results_df = results_df.sort_values("trades", ascending=False).reset_index(drop=True)
        results_df["rank"] = results_df.index + 1
    
    elapsed_total = time.time() - start_time
    print(f"\nStage 1 completed in {elapsed_total/60:.1f} minutes")
    print(f"Valid configurations found (50-500 trades, >=30% WR): {len(results_df)}")
    
    # Get top 20
    top_n = min(20, len(results_df))
    if top_n > 0:
        top_results = results_df.head(top_n)
        print(f"\nTop {top_n} configurations by trade count:")
        print(top_results[["rank", "sd_threshold", "entry_mode", "adx_max", "rsi_max", 
                          "volume_mult", "use_session_filter", "trades", "win_rate", 
                          "profit_factor", "net_return"]].to_string())
    
    return results_df


# ─────────────────────────────────────────────────────────────────────────────
# STAGE 2: R:R OPTIMIZATION
# ─────────────────────────────────────────────────────────────────────────────

def run_stage2(df_5m: pd.DataFrame, df_1m: pd.DataFrame, stage1_results: pd.DataFrame) -> pd.DataFrame:
    """Run Stage 2: R:R optimization on top configs."""
    print("\n" + "="*80)
    print("STAGE 2: R:R Optimization (Profitability Tuning)")
    print("="*80)
    
    if stage1_results.empty:
        print("No valid configurations from Stage 1 to optimize!")
        return pd.DataFrame()
    
    # Get top 5 configs (or all if less than 5)
    top_n = min(5, len(stage1_results))
    top5 = stage1_results.head(top_n)
    
    print(f"\nOptimizing top {top_n} configurations from Stage 1:")
    for idx, row in top5.iterrows():
        print(f"  #{row['rank']}: sd={row['sd_threshold']}, mode={row['entry_mode']}, "
              f"adx={row['adx_max']}, rsi={row['rsi_max']}, vol={row['volume_mult']}, "
              f"session={row['use_session_filter']}")
        print(f"         Trades: {row['trades']}, WR: {row['win_rate']:.1f}%, PF: {row['profit_factor']:.2f}")
    
    print(f"\nTesting {len(STAGE2_RR_RATIOS)} R:R ratios on each config...")
    
    results = []
    start_time = time.time()
    
    # Create args for parallel execution
    args_list = []
    for rr_params in STAGE2_RR_RATIOS:
        rr_ratio = rr_params["rr_ratio"]
        
        for idx, stage1_row in top5.iterrows():
            # Create base params from Stage 1
            base_params = {
                "sd_threshold": stage1_row["sd_threshold"],
                "entry_mode": stage1_row["entry_mode"],
                "adx_max": stage1_row["adx_max"],
                "rsi_max": stage1_row["rsi_max"],
                "volume_mult": stage1_row["volume_mult"],
                "use_session_filter": stage1_row["use_session_filter"],
                "require_reversal_candle": stage1_row.get("require_reversal_candle", False),
                "require_mss": stage1_row.get("require_mss", False),
                # Override with R:R params
                "atr_stop": rr_params["atr_stop"],
                "atr_target": rr_params["atr_target"],
            }
            args_list.append((df_5m, df_1m, base_params, rr_ratio, stage1_row["rank"]))
    
    # Run in parallel
    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = [executor.submit(run_stage2_single, a) for a in args_list]
        
        for future in tqdm(as_completed(futures), total=len(futures), desc="Stage 2"):
            result = future.result()
            if result:
                results.append(result)
    
    results_df = pd.DataFrame(results)
    
    # Sort by net_return (descending)
    if not results_df.empty:
        results_df = results_df.sort_values("net_return", ascending=False).reset_index(drop=True)
    
    elapsed_total = time.time() - start_time
    print(f"\nStage 2 completed in {elapsed_total/60:.1f} minutes")
    
    # Show results
    if not results_df.empty:
        print(f"\nStage 2 Results (sorted by net return):")
        print(results_df.to_string())
        
        # Show configs that meet Stage 2 criteria
        meeting = results_df[results_df["meets_criteria"] == True]
        if not meeting.empty:
            print(f"\nConfigs meeting Stage 2 criteria (WR>40%, Net>0%, Trades>=20):")
            print(meeting.to_string())
    
    return results_df


def run_stage2_single(args: tuple) -> dict | None:
    """Run a single Stage 2 backtest."""
    df_5m, df_1m, params, rr_ratio, parent_rank = args
    
    full_params = {**FIXED_PARAMS, **params}
    
    try:
        equity, closed_trades = run_vwap_scalping(
            df=df_5m,
            df_exit=df_1m,
            **full_params,
        )
        metrics = calculate_metrics(closed_trades)
        
        # Stage 2 criteria: WR > 40%, Net > 0%, Trades >= 20
        meets_criteria = (
            metrics["win_rate"] > 40 and 
            metrics["net_return"] > 0 and 
            metrics["trades"] >= 20
        )
        
        return {
            "parent_rank": parent_rank,
            "atr_stop": params["atr_stop"],
            "atr_target": params["atr_target"],
            "rr_ratio": rr_ratio,
            "trades": metrics["trades"],
            "win_rate": metrics["win_rate"],
            "profit_factor": metrics["profit_factor"],
            "net_return": metrics["net_return"],
            "meets_criteria": meets_criteria,
        }
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# MAIN EXECUTION
# ─────────────────────────────────────────────────────────────────────────────

def main():
    """Run the complete two-stage VWAP optimization."""
    print("="*80)
    print("VWAP SCALPING TWO-STAGE MULTIVARIATE OPTIMIZATION")
    print("="*80)
    
    # Load data
    print("\nLoading BTCUSDT data...")
    df_5m = load_local("BTCUSDT", "5m")
    df_1m = load_local("BTCUSDT", "1m")
    
    if df_5m is None:
        print("ERROR: Could not load 5m data. Please run data_manager first.")
        return
    
    if df_1m is None:
        print("ERROR: Could not load 1m data. Please run data_manager first.")
        return
    
    # Filter for 2024
    df_5m = df_5m[(df_5m.index >= "2024-01-01") & (df_5m.index < "2025-01-01")]
    df_1m = df_1m[(df_1m.index >= "2024-01-01") & (df_1m.index < "2025-01-01")]
    
    print(f"  5m data: {len(df_5m)} bars ({df_5m.index[0]} to {df_5m.index[-1]})")
    print(f"  1m data: {len(df_1m)} bars ({df_1m.index[0]} to {df_1m.index[-1]})")
    
    # Run Stage 1
    stage1_results = run_stage1(df_5m, df_1m)
    
    # Save Stage 1 results
    if not stage1_results.empty:
        stage1_path = OUTPUT_DIR / "vwap_multivariate_sweep_stage1.csv"
        stage1_results.to_csv(stage1_path, index=False)
        print(f"\nStage 1 results saved to: {stage1_path}")
    
    # Run Stage 2
    stage2_results = run_stage2(df_5m, df_1m, stage1_results)
    
    # Save Stage 2 results
    if not stage2_results.empty:
        stage2_path = OUTPUT_DIR / "vwap_multivariate_sweep_stage2.csv"
        stage2_results.to_csv(stage2_path, index=False)
        print(f"Stage 2 results saved to: {stage2_path}")
    
    # Create combined output with top configs
    if not stage1_results.empty and not stage2_results.empty:
        create_final_output(stage1_results, stage2_results)
    
    print("\n" + "="*80)
    print("OPTIMIZATION COMPLETE!")
    print("="*80)


def create_final_output(stage1: pd.DataFrame, stage2: pd.DataFrame):
    """Create final output table."""
    final_results = []
    
    # Add Stage 1 results
    if not stage1.empty:
        for idx, row in stage1.head(20).iterrows():
            final_results.append({
                "Rank": row["rank"],
                "sd_threshold": row["sd_threshold"],
                "entry_mode": row["entry_mode"],
                "adx_max": row["adx_max"],
                "rsi_max": row["rsi_max"],
                "volume_mult": row["volume_mult"],
                "use_session_filter": row["use_session_filter"],
                "atr_stop": FIXED_PARAMS["atr_stop"],
                "atr_target": FIXED_PARAMS["atr_target"],
                "Trades": row["trades"],
                "WR%": f"{row['win_rate']:.1f}%",
                "PF": f"{row['profit_factor']:.2f}",
                "Net%": f"{row['net_return']:.1f}%",
                "Stage": 1,
            })
    
    # Add Stage 2 results
    if not stage2.empty:
        for idx, row in stage2.iterrows():
            final_results.append({
                "Rank": f"P{row['parent_rank']}",
                "sd_threshold": "inherited",
                "entry_mode": "inherited",
                "adx_max": "inherited",
                "rsi_max": "inherited",
                "volume_mult": "inherited",
                "use_session_filter": "inherited",
                "atr_stop": row["atr_stop"],
                "atr_target": row["atr_target"],
                "Trades": row["trades"],
                "WR%": f"{row['win_rate']:.1f}%",
                "PF": f"{row['profit_factor']:.2f}",
                "Net%": f"{row['net_return']:.1f}%",
                "Stage": f"2 ({row['rr_ratio']})",
            })
    
    final_df = pd.DataFrame(final_results)
    final_path = OUTPUT_DIR / "vwap_multivariate_sweep_results.csv"
    final_df.to_csv(final_path, index=False)
    print(f"\nFinal combined results saved to: {final_path}")
    
    # Generate markdown report
    generate_report(stage1, stage2, final_df)


def generate_report(stage1: pd.DataFrame, stage2: pd.DataFrame, final: pd.DataFrame):
    """Generate markdown report."""
    report = """# VWAP Scalping Multivariate Optimization Report

## Summary

This report documents the two-stage optimization process for the VWAP Scalping strategy.

### Stage 1: Full Multivariate Trade Discovery

**Objective**: Find configurations generating 50-500 trades with >=30% win rate.

**Variables Tested**:
- sd_threshold: [1.5, 2.0, 2.5]
- entry_mode: [bull_pullback, mean_reversion, cross]
- adx_max: [25, 35]
- rsi_max: [40, 60]
- volume_mult: [1.0, 1.5]
- use_session_filter: [True, False]
- require_reversal_candle: [False]
- require_mss: [False]

**Fixed Parameters**:
- atr_stop: 0.7
- atr_target: 3.5
- use_htf_vwap: True
- use_htf_ema: False
- htf_adx_max: 25
- tp_mode: vwap

### Stage 2: R:R Optimization

**Objective**: Maximize profitability on top configs.

**Criteria**: Win Rate > 40%, Net Return > 0%, Trades >= 20

"""
    
    # Stage 1 Results Table
    if not stage1.empty:
        report += "## Stage 1 Results (Top Configurations)\n\n"
        report += "| Rank | sd | mode | adx | rsi | vol | session | Trades | WR% | PF | Net% |\n"
        report += "|------|----|------|-----|-----|-----|---------|--------|-----|-----|------|\n"
        
        for idx, row in stage1.head(20).iterrows():
            report += f"| {row['rank']} | {row['sd_threshold']} | {row['entry_mode'][:8]} | "
            report += f"{row['adx_max']} | {row['rsi_max']} | {row['volume_mult']} | "
            report += f"{str(row['use_session_filter'])[:1]} | {row['trades']} | "
            report += f"{row['win_rate']:.1f}% | {row['profit_factor']:.2f} | {row['net_return']:.1f}% |\n"
    
    # Stage 2 Results Table
    if not stage2.empty:
        report += "\n## Stage 2 Results (R:R Optimization)\n\n"
        report += "| Parent | atr_s | atr_t | R:R | Trades | WR% | PF | Net% | Met? |\n"
        report += "|--------|-------|-------|-----|--------|-----|-----|------|------|\n"
        
        for idx, row in stage2.iterrows():
            report += f"| {row['parent_rank']} | {row['atr_stop']} | {row['atr_target']} | "
            report += f"{row['rr_ratio']} | {row['trades']} | {row['win_rate']:.1f}% | "
            report += f"{row['profit_factor']:.2f} | {row['net_return']:.1f}% | "
            report += f"{'Y' if row['meets_criteria'] else 'N'} |\n"
    
    # Best configurations
    if not stage2.empty:
        best = stage2[stage2["meets_criteria"] == True].head(5)
        if not best.empty:
            report += "\n## Best Configurations (Meeting Stage 2 Criteria)\n\n"
            for idx, row in best.iterrows():
                report += f"- **Parent Rank #{row['parent_rank']}** with {row['rr_ratio']} R:R: "
                report += f"{row['trades']} trades, {row['win_rate']:.1f}% WR, "
                report += f"{row['profit_factor']:.2f} PF, {row['net_return']:.1f}% net return\n"
    
    # Save report
    report_path = OUTPUT_DIR / "vwap_multivariate_sweep_report.md"
    with open(report_path, "w") as f:
        f.write(report)
    
    print(f"Report saved to: {report_path}")


if __name__ == "__main__":
    main()
