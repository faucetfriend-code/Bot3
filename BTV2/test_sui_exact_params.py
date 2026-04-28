#!/usr/bin/env python3
"""
VWAP Strategy — Test Exact Analysis Parameters on SUI (2020-2024)

The trade analysis showing 54% WR was done on SUI data, not BTC.
This test runs the exact parameters on SUI data to replicate that result.
"""
import sys
from pathlib import Path
import csv

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.strategies import run_vwap_scalping, compute_metrics


# Try both data sources
def load_sui_parquet(symbol: str, interval: str) -> pd.DataFrame | None:
    """Load from G:/Candle Data if available."""
    path = Path("G:/Candle Data") / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    return None


def load_sui_csv(symbol: str, interval: str) -> pd.DataFrame | None:
    """Load from CSV backup."""
    for base in [
        Path("trading_bot_v2/backtesting/data"),
        Path(".claude/worktrees/eager-nobel/trading_bot_v2/backtesting/data"),
    ]:
        path = base / f"{symbol}_{interval}.csv"
        if path.exists():
            df = pd.read_csv(path, parse_dates=True, index_col=0)
            # Normalize column names to Title Case
            df.columns = [c.capitalize() for c in df.columns]
            if hasattr(df.index, 'tz') and df.index.tz is None:
                df.index = df.index.tz_localize('UTC')
            return df
    return None


def main():
    print("=" * 70)
    print("VWAP ANALYSIS PARAMETERS ON SUI (2020-2024)")
    print("=" * 70)

    # Try to load SUI data from multiple sources
    print("\nLoading SUI 5m data...")
    df_5m = load_sui_parquet("SUI-USDC", "5m")
    if df_5m is None:
        df_5m = load_sui_csv("SUI-USDC", "5m")
    if df_5m is None:
        print("ERROR: Could not load SUI 5m data")
        sys.exit(1)
    
    print("\nLoading SUI 1m data...")
    df_1m = load_sui_parquet("SUI-USDC", "1m")
    if df_1m is None:
        df_1m = load_sui_csv("SUI-USDC", "1m")
    if df_1m is None:
        print("WARNING: No 1m exit data - using 5m for exits")
        df_1m = df_5m
    
    print(f"5m bars: {len(df_5m):,} ({df_5m.index[0]} to {df_5m.index[-1]})")
    print(f"1m bars: {len(df_1m):,} ({df_1m.index[0]} to {df_1m.index[-1]})")

    # Filter to 2020-2024
    df_5m = df_5m[(df_5m.index >= "2020-01-01") & (df_5m.index < "2025-01-01")]
    df_1m = df_1m[(df_1m.index >= "2020-01-01") & (df_1m.index < "2025-01-01")]

    print(f"\nTest period: 2020-01-01 to 2025-01-01")
    print(f"5m bars: {len(df_5m):,}")
    print(f"1m bars: {len(df_1m):,}")

    results = []

    # Configs based on analysis (from .env + analysis report)
    configs = [
        # Config 1: Current .env settings (for comparison)
        {
            "name": "CURRENT_ENV",
            "sd_threshold": 2.5,
            "atr_stop": 2.0,
            "atr_target": 3.0,
            "entry_mode": "bull_pullback",
            "use_session_filter": True,
            "use_trailing_stop": True,
            "trailing_atr": 1.2,
            "tp_mode": "vwap",
            "require_reversal_candle": True,
            "adx_max": 25.0,
            "rsi_max": 45.0,
            "volume_mult": 1.5,
            "use_anchored_vwap": True,
            "use_htf_vwap": True,
            "use_htf_ema": True,
            "htf_adx_max": 25.0,
        },
        # Config 2: bull_pullback (from analysis, ~52% WR expected)
        {
            "name": "BULL_PULLBACK",
            "sd_threshold": 2.0,
            "atr_stop": 0.7,
            "atr_target": 3.5,
            "entry_mode": "bull_pullback",
            "use_session_filter": True,
            "use_trailing_stop": True,
            "trailing_atr": 1.2,
            "tp_mode": "vwap",
            "require_reversal_candle": True,
            "adx_max": 30.0,
            "rsi_max": 50.0,
            "volume_mult": 2.0,
            "use_anchored_vwap": True,
            "use_htf_vwap": True,
            "use_htf_ema": True,
            "htf_adx_max": 25.0,
        },
        # Config 3: Analysis exact params
        {
            "name": "ANALYSIS_EXACT",
            "sd_threshold": 0.5,
            "atr_stop": 3.0,
            "atr_target": 6.0,
            "entry_mode": "cross",
            "use_session_filter": True,
            "use_trailing_stop": True,
            "trailing_atr": 1.2,
            "tp_mode": "atr",
            "require_reversal_candle": True,
            "adx_max": 30.0,
            "rsi_max": 50.0,
            "volume_mult": 2.0,
            "use_anchored_vwap": True,
            "use_htf_vwap": False,
            "use_htf_ema": False,
        },
    ]

    print("\n" + "=" * 70)
    for cfg in configs:
        name = cfg.pop("name")
        print(f"\n{'='*70}")
        print(f"TEST: {name}")
        print("=" * 70)
        
        equity, trades = run_vwap_scalping(
            df_5m,
            cutoff=0.10,
            df_exit=df_1m,
            **cfg
        )
        
        if trades:
            wins = sum(1 for t in trades if t > 0)
            win_rate = wins / len(trades) * 100
            gross_profit = sum(t for t in trades if t > 0)
            gross_loss = abs(sum(t for t in trades if t < 0))
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        else:
            wins = 0
            win_rate = 0
            profit_factor = 0
        
        costs = len(trades) * 0.30
        bars_per_year = 105120
        metrics = compute_metrics(equity, trades, bars_per_year=bars_per_year)
        net_return = metrics["total_return_pct"] - costs
        
        print(f"  Trades: {len(trades)}")
        print(f"  Win Rate: {win_rate:.1f}%")
        print(f"  Net Return: {net_return:+.2f}%")
        print(f"  Profit Factor: {profit_factor:.2f}")
        print(f"  Sharpe: {metrics['sharpe']:.2f}")
        
        results.append({
            "config": name,
            "symbol": "SUI",
            "trades": len(trades),
            "win_rate_pct": round(win_rate, 1),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "net_return_pct": round(net_return, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999.99,
            "sharpe": round(metrics["sharpe"], 2),
            "max_dd_pct": round(metrics["max_dd_pct"], 2),
        })

    # Save CSV
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    csv_path = results_dir / "vwap_exact_analysis_params.csv"
    fieldnames = ["config", "symbol", "trades", "win_rate_pct", "gross_profit", "gross_loss",
                "net_return_pct", "profit_factor", "sharpe", "max_dd_pct"]
    
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    
    print(f"\nCSV saved: {csv_path}")
    
    # Summary table
    print("\n" + "=" * 70)
    print("SUMMARY TABLE (SUI-USDC, 2020-2024)")
    print("=" * 70)
    print(f"{'Config':<30} {'Trades':>8} {'WR%':>8} {'Net%':>10} {'PF':>8}")
    print("-" * 70)
    for r in results:
        print(f"{r['config']:<30} {r['trades']:>8} {r['win_rate_pct']:>7.1f}% {r['net_return_pct']:>+9.2f}% {r['profit_factor']:>7.2f}")
    
    best_wr = max(results, key=lambda x: x["win_rate_pct"])
    print(f"\nBest WR: {best_wr['config']} at {best_wr['win_rate_pct']:.1f}%")
    print(f"Target WR: ~54%")


if __name__ == "__main__":
    main()