"""
VWAP Scalping Grid Search Optimizer
====================================
Fast grid search over the specified parameter space.
Saves results to optimizer_trials.jsonl and updates .env if improved.
"""

from __future__ import annotations
import json
import logging
import sys
from datetime import date, datetime
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

# Setup paths
_HERE = Path(__file__).resolve().parent  # BTV2/
_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_ROOT))

from strategies import (
    run_vwap_scalping,
    compute_metrics,
    build_windows,
    stitch_oos_equity,
    VS_DEFAULTS,
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("gridsearch")

# Constants
TRIALS_LOG = _HERE / "results" / "optimizer_trials.jsonl"
ENV_PATH = _ROOT / ".env"
DATA_DIR = Path("G:/Candle Data")
MIN_IMPROVEMENT = 0.05
MIN_OOS_TRADES = 10
WF_START = date(2018, 1, 1)
WF_END = date(2025, 12, 31)

# Parameter search space (as specified)
SEARCH_SPACE = {
    "sd_threshold": [2.5, 3.0, 3.5, 4.0, 4.5],
    "entry_mode": ["bull_pullback", "bear_pullback", "cross", "mean_reversion"],
    "atr_stop": [1.5, 2.0, 2.5, 3.0],
    "tp_mode": ["atr", "pdh"],
    "trailing_atr": [1.2, 1.5, 2.0],
}

# Fixed params (filters enabled)
FIXED_PARAMS = {
    "use_trend_filter": True,
    "use_volume_filter": True,
    "use_trailing_stop": True,
    "use_session_filter": True,
    "use_htf_ema": True,
    "use_anchored_vwap": True,
    "require_reversal_candle": True,
    "use_stoch_filter": False,
    "use_htf_vwap": True,
}


def load_data(ticker: str = "BTC-USD") -> pd.DataFrame:
    """Load 5m BTC data."""
    binance_sym = "BTCUSDT"
    parquet_path = DATA_DIR / f"{binance_sym}_5m.parquet"
    
    if not parquet_path.exists():
        raise FileNotFoundError(f"Data not found: {parquet_path}")
    
    log.info(f"Loading from {parquet_path}")
    df = pd.read_parquet(parquet_path)
    df = df.rename(columns={c: c.title() for c in df.columns if c.lower() in 
               ("open", "high", "low", "close", "volume")})
    df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()
    log.info(f"Loaded {len(df)} bars ({df.index[0].date()} -> {df.index[-1].date()})")
    return df


def evaluate_params(params: dict, df: pd.DataFrame, cutoff: float = 0.10) -> dict:
    """
    Run walk-forward evaluation for given params.
    Returns metrics dict or None if too few trades.
    """
    # Build walk-forward windows
    train_months = 12
    test_months = 3
    bars_per_year = 105120  # 5m bars per year
    
    windows = build_windows(WF_START, WF_END, train_months=train_months, test_months=test_months)
    if not windows:
        return None
    
    oos_segments = []
    all_trades = []
    window_results = []
    windows_positive = 0
    
    for w in windows:
        df_train = df.loc[str(w["train_start"]):str(w["train_end"])]
        df_test = df.loc[str(w["test_start"]):str(w["test_end"])]
        
        if len(df_train) < 30 or len(df_test) < 5:
            continue
        
        try:
            eq_oos, trades_oos = run_vwap_scalping(df_test, cutoff, **params)
        except Exception as e:
            log.debug(f"Window {w['fold']} failed: {e}")
            continue
        
        if len(trades_oos) == 0:
            continue
        
        m = compute_metrics(eq_oos, trades_oos, bars_per_year=bars_per_year)
        oos_segments.append(eq_oos)
        all_trades.extend(trades_oos)
        
        if m["sharpe"] > 0:
            windows_positive += 1
        
        window_results.append({
            "fold": w["fold"],
            "sharpe": round(m["sharpe"], 4),
            "n_trades": m["n_trades"],
            "win_rate": round(m["win_rate_pct"], 1),
        })
    
    if len(all_trades) < MIN_OOS_TRADES:
        return None
    
    # Aggregate OOS metrics
    oos_equity = stitch_oos_equity(oos_segments)
    agg = compute_metrics(oos_equity, all_trades, bars_per_year=bars_per_year)
    
    consistency_pct = (windows_positive / max(len(window_results), 1)) * 100.0
    
    return {
        "status": "ok",
        "mean_oos_sharpe": agg["sharpe"],
        "total_return_pct": round(agg["total_return_pct"], 2),
        "cagr_pct": round(agg["cagr_pct"], 2),
        "max_dd_pct": round(agg["max_dd_pct"], 2),
        "win_rate_pct": round(agg["win_rate_pct"], 1),
        "profit_factor": round(agg["profit_factor"], 3),
        "n_trades": agg["n_trades"],
        "n_windows": len(window_results),
        "consistency_pct": round(consistency_pct, 1),
        "windows": window_results,
    }


def get_prev_best() -> float:
    """Get previous best OOS Sharpe from trials log."""
    if not TRIALS_LOG.exists():
        return -999.0
    
    best = -999.0
    with open(TRIALS_LOG) as f:
        for line in f:
            try:
                t = json.loads(line)
                if t.get("strategy") == "VWAP Scalping" and t.get("status") == "ok":
                    s = t.get("mean_oos_sharpe", -999.0)
                    if s > best:
                        best = s
            except json.JSONDecodeError:
                pass
    return best


def log_trial(trial_num: int, result: dict, accepted: bool):
    """Log trial to JSONL."""
    TRIALS_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "strategy": "VWAP Scalping",
        "trial": trial_num,
        "timestamp": datetime.utcnow().isoformat(),
        "accepted": int(accepted),
        **{k: v for k, v in result.items() if k != "windows"},
    }
    with open(TRIALS_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")


def write_env_params(updates: dict):
    """Update .env with new params."""
    if not ENV_PATH.exists():
        log.warning(f".env not found")
        return
    
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    updated_keys = set()
    
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                updated_keys.add(key)
                continue
        new_lines.append(line)
    
    for key, val in updates.items():
        if key not in updated_keys:
            new_lines.append(f"# Added by grid search {datetime.utcnow().date()}")
            new_lines.append(f"{key}={val}")
    
    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    log.info(f"Updated .env: {updates}")


def run_grid_search(n_trials: int = 100):
    """Run grid search optimization."""
    # Load data
    df = load_data()
    
    # Generate parameter combinations
    param_names = list(SEARCH_SPACE.keys())
    param_values = list(SEARCH_SPACE.values())
    all_combinations = list(product(*param_values))
    log.info(f"Total parameter combinations: {len(all_combinations)}")
    
    # Get previous best
    prev_best = get_prev_best()
    log.info(f"Previous best OOS Sharpe: {prev_best:.4f}")
    
    # Run trials (limited to n_trials)
    best_result = None
    best_sharpe = -999.0
    trial_num = 0
    
    import random
    random.shuffle(all_combinations)  # Random order
    
    for combo in all_combinations[:n_trials]:
        trial_num += 1
        
        # Build params dict
        params = FIXED_PARAMS.copy()
        for i, name in enumerate(param_names):
            params[name] = combo[i]
        
        log.info(f"Trial {trial_num}: {params['sd_threshold']}, {params['entry_mode']}, "
                 f"{params['atr_stop']}, {params['tp_mode']}, {params['trailing_atr']}")
        
        # Evaluate
        result = evaluate_params(params, df)
        
        if result is None:
            log.info(f"  Trial {trial_num}: insufficient trades")
            continue
        
        log.info(f"  Sharpe: {result['mean_oos_sharpe']:.4f}, "
                 f"WR: {result['win_rate_pct']}%, "
                 f"Return: {result['total_return_pct']}%, "
                 f"Trades: {result['n_trades']}")
        
        # Track best
        if result["mean_oos_sharpe"] > best_sharpe:
            best_sharpe = result["mean_oos_sharpe"]
            best_result = result
            best_result["params"] = params.copy()
            
            # Check improvement
            improvement = best_sharpe - prev_best
            accepted = improvement >= MIN_IMPROVEMENT
            
            log.info(f"  *** New best! Sharpe: {best_sharpe:.4f}, "
                     f"Improvement: {improvement:+.4f}, "
                     f"Accepted: {accepted}")
            
            # Log trial
            log_trial(trial_num, best_result, accepted)
            
            # Update .env if accepted
            if accepted:
                env_updates = {
                    "VWAP_SD_ENTRY_THRESHOLD": str(params["sd_threshold"]),
                    "VWAP_ATR_STOP_MULTIPLIER": str(params["atr_stop"]),
                    "VWAP_ATR_TRAILING_MULTIPLIER": str(params["trailing_atr"]),
                    "VWAP_ENTRY_MODE": params["entry_mode"],
                    "VWAP_TP_MODE": params["tp_mode"],
                    "VWAP_REQUIRE_REVERSAL_CANDLE": str(params["require_reversal_candle"]).lower(),
                    "VWAP_USE_HTF_VWAP": str(params["use_htf_vwap"]).lower(),
                }
                write_env_params(env_updates)
    
    # Final summary
    log.info("=" * 60)
    log.info("GRID SEARCH COMPLETE")
    log.info(f"Total trials: {trial_num}")
    if best_result:
        log.info(f"Best OOS Sharpe: {best_sharpe:.4f}")
        log.info(f"Win rate: {best_result['win_rate_pct']}%")
        log.info(f"Total return: {best_result['total_return_pct']}%")
        log.info(f"Max drawdown: {best_result['max_dd_pct']}%")
        log.info(f"Trades: {best_result['n_trades']}")
    log.info("=" * 60)
    
    return best_result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-trials", type=int, default=100)
    args = parser.parse_args()
    
    result = run_grid_search(n_trials=args.n_trials)
    if result:
        print(f"\nBest Result:")
        print(f"  OOS Sharpe: {result['mean_oos_sharpe']:.4f}")
        print(f"  Win rate: {result['win_rate_pct']}%")
        print(f"  Return: {result['total_return_pct']}%")
        print(f"  Max DD: {result['max_dd_pct']}%")