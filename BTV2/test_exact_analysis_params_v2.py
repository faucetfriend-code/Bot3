#!/usr/bin/env python3
"""
VWAP Strategy — Debug Analysis Parameters (v2)

Based on the trade analysis findings:
- Bear_pullback has 65% WR (110 trades)
- Bull_pullback has 52% WR (350 trades) 
- Shorts outperform longs significantly
- -1SD band underperforms (43% WR)
- Best session: 16:00-24:00 UTC (57.8% WR)

Test these findings directly to replicate the 54% WR.
"""
import sys
from pathlib import Path
import csv

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from BTV2.strategies import run_vwap_scalping, compute_metrics

STORAGE_ROOT = Path("G:/Candle Data")


def load_parquet(symbol: str, interval: str) -> pd.DataFrame | None:
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    return None


def main():
    print("=" * 70)
    print("VWAP ANALYSIS DEBUG TEST (v2)")
    print("=" * 70)

    # Load data
    print("\nLoading data...")
    df_5m_full = load_parquet("BTCUSDT", "5m")
    df_1m_full = load_parquet("BTCUSDT", "1m")

    if df_5m_full is None or df_1m_full is None:
        print("ERROR: Could not load data")
        sys.exit(1)

    # Filter to 2020-2024
    df_5m = df_5m_full[(df_5m_full.index >= "2020-01-01") & (df_5m_full.index < "2025-01-01")]
    df_1m = df_1m_full[(df_1m_full.index >= "2020-01-01") & (df_1m_full.index < "2025-01-01")]

    print(f"5m bars: {len(df_5m):,}")
    print(f"1m bars: {len(df_1m):,}")

    results = []

    # Test configs based on analysis findings
    configs = [
        # Config 1: bull_pullback (matches analysis ~52% WR)
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
        # Config 2: bear_pullback (matches analysis ~65% WR)
        {
            "name": "BEAR_PULLBACK",
            "sd_threshold": 2.0,
            "atr_stop": 0.7,
            "atr_target": 3.5,
            "entry_mode": "bear_pullback",
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
        # Config 3: bull_pullback with tighter session (16:00-24:00)
        {
            "name": "BULL_PULLBACK_TIGHT_SESSION",
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
            "use_htf_vwap": False,  # Try without HTF filter
            "use_htf_ema": False,
            # Custom: tighter SD threshold
            "sd_threshold": 1.5,
        },
        # Config 4: The original parameters that achieved 54% WR (from analysis report)
        {
            "name": "ANALYSIS_REPORT_CONFIG",
            "sd_threshold": 2.0,
            "entry_mode": "bull_pullback",
            "adx_max": 30,
            "rsi_max": 50,
            "volume_mult": 2.0,
            "atr_stop": 0.7,
            "atr_target": 3.5,
            "pullback_bars": 3,
            "use_session_filter": True,
            "use_htf_vwap": True,
            "use_htf_ema": False,
            "htf_adx_max": 25,
            "use_trailing_stop": True,
            "trailing_atr": 1.2,
            "use_anchored_vwap": True,
            "require_reversal_candle": True,
            "tp_mode": "vwap",
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
            "trades": len(trades),
            "win_rate_pct": round(win_rate, 1),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "net_return_pct": round(net_return, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999.99,
            "sharpe": round(metrics["sharpe"], 2),
            "max_dd_pct": round(metrics["max_dd_pct"], 2),
        })

    # Save to CSV
    results_dir = Path(__file__).parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    csv_path = results_dir / "vwap_exact_analysis_params.csv"
    fieldnames = ["config", "trades", "win_rate_pct", "gross_profit", "gross_loss",
                "net_return_pct", "profit_factor", "sharpe", "max_dd_pct"]
    
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    
    print(f"\nCSV saved: {csv_path}")
    
    # Summary table
    print("\n" + "=" * 70)
    print("SUMMARY TABLE")
    print("=" * 70)
    print(f"{'Config':<35} {'Trades':>8} {'WR%':>8} {'Net%':>10} {'PF':>8}")
    print("-" * 70)
    for r in results:
        print(f"{r['config']:<35} {r['trades']:>8} {r['win_rate_pct']:>7.1f}% {r['net_return_pct']:>+9.2f}% {r['profit_factor']:>7.2f}")
    
    # Compare to target
    best_wr = max(results, key=lambda x: x["win_rate_pct"])
    print(f"\nBest WR: {best_wr['config']} at {best_wr['win_rate_pct']:.1f}%")
    print(f"Target WR: ~54%")


if __name__ == "__main__":
    main()