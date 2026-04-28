#!/usr/bin/env python3
"""
VWAP Scalping Strategy — Test Exact Analysis Parameters (2020-2024)

Tests the EXACT parameters from the winning trade analysis:
- sd_threshold: 0.5 (VERY tight - any move triggers!)
- atr_stop: 3.0
- atr_target: 6.0
- entry_mode: "cross"
- use_session_filter: True
- use_trailing_stop: True
- trailing_atr: 1.2
- tp_mode: "atr"
- require_reversal_candle: True
- adx_max: 30.0
- rsi_max: 50.0
- volume_mult: 2.0

Should achieve ~54% WR like the original analysis.
"""
import sys
from pathlib import Path
from datetime import datetime
import csv

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import run_vwap_scalping, compute_metrics, compute_atr, compute_adx, compute_rsi, apply_butterworth

STORAGE_ROOT = Path("G:/Candle Data")


def load_parquet(symbol: str, interval: str) -> pd.DataFrame | None:
    """Load parquet data."""
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    return None


def classify_exit(trade_pnl: float, realized: float) -> str:
    """Classify the exit type based on PnL pattern."""
    if realized >= trade_pnl * 0.95:
        return "TP"
    elif realized <= 0:
        return "SL"
    else:
        return "Trailing"


# THE WINNING CONFIG FROM ANALYSIS
ANALYSIS_PARAMS = {
    # Core entry - VERY TIGHT to trigger on any move
    "sd_threshold": 0.5,
    "atr_stop": 3.0,
    "atr_target": 6.0,
    # Entry mode - different from pullback
    "entry_mode": "cross",
    # Filters from analysis
    "use_session_filter": True,
    "use_trailing_stop": True,
    "trailing_atr": 1.2,
    # TP mode - ATR not VWAP!
    "tp_mode": "atr",
    "require_reversal_candle": True,
    # Additional parameters required by the function
    "adx_max": 30.0,
    "rsi_max": 50.0,
    "volume_mult": 2.0,
    # Keep these defaults that work
    "use_anchored_vwap": True,
    "use_htf_vwap": False,
    "use_htf_ema": False,
}


# CURRENT .ENV SETTINGS (for comparison)
CURRENT_ENV_PARAMS = {
    "sd_threshold": 2.5,  # Relaxed threshold
    "atr_stop": 2.0,
    "atr_target": 3.0,
    "entry_mode": "bull_pullback",
    "use_session_filter": True,
    "use_trailing_stop": True,
    "trailing_atr": 1.2,
    "tp_mode": "vwap",  # Different from analysis
    "require_reversal_candle": True,
    "adx_max": 25.0,
    "rsi_max": 45.0,
    "volume_mult": 1.5,
    "use_anchored_vwap": True,
    "use_htf_vwap": True,
    "use_htf_ema": True,
}


def main():
    print("=" * 70)
    print("VWAP EXACT ANALYSIS PARAMETERS TEST (2020-2024)")
    print("=" * 70)

    # Load data
    print("\nLoading data...")
    df_5m_full = load_parquet("BTCUSDT", "5m")
    df_1m_full = load_parquet("BTCUSDT", "1m")

    if df_5m_full is None:
        print("ERROR: Could not load 5m data from G:/Candle Data/BTCUSDT_5m.parquet")
        sys.exit(1)
    if df_1m_full is None:
        print("ERROR: Could not load 1m data from G:/Candle Data/BTCUSDT_1m.parquet")
        sys.exit(1)

    print(f"5m data: {len(df_5m_full)} bars ({df_5m_full.index[0]} to {df_5m_full.index[-1]})")
    print(f"1m data: {len(df_1m_full)} bars ({df_1m_full.index[0]} to {df_1m_full.index[-1]})")

    # Filter to 2020-2024
    start_date = "2020-01-01"
    end_date = "2025-01-01"

    df_5m = df_5m_full[(df_5m_full.index >= start_date) & (df_5m_full.index < end_date)]
    df_1m = df_1m_full[(df_1m_full.index >= start_date) & (df_1m_full.index < end_date)]

    print(f"\nTest period: {start_date} to {end_date}")
    print(f"5m bars: {len(df_5m):,}")
    print(f"1m bars: {len(df_1m):,}")

    # Test 1: Analysis exact params
    print("\n" + "=" * 70)
    print("TEST 1: ANALYSIS EXACT PARAMETERS")
    print("=" * 70)
    print(f"Params: {ANALYSIS_PARAMS}")

    equity, trades = run_vwap_scalping(
        df_5m,
        cutoff=0.10,
        df_exit=df_1m,
        **ANALYSIS_PARAMS
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

    # Calculate costs and metrics
    costs = len(trades) * 0.30
    bars_per_year = 105120  # 5m bars per year
    metrics = compute_metrics(equity, trades, bars_per_year=bars_per_year)
    net_return = metrics["total_return_pct"] - costs

    print(f"\nResults:")
    print(f"  Trades: {len(trades)}")
    print(f"  Win Rate: {win_rate:.1f}%")
    print(f"  Gross Profit: {gross_profit:+.2f}%")
    print(f"  Gross Loss: {gross_loss:+.2f}%")
    print(f"  Net Return: {net_return:+.2f}%")
    print(f"  Profit Factor: {profit_factor:.2f}")
    print(f"  Sharpe: {metrics['sharpe']:.2f}")
    print(f"  Max DD: {metrics['max_dd_pct']:.2f}%")

    # Save analysis params results
    results = []
    results.append({
        "config": "analysis_exact",
        "trades": len(trades),
        "win_rate_pct": round(win_rate, 1),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "net_return_pct": round(net_return, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999.99,
        "sharpe": round(metrics["sharpe"], 2),
        "max_dd_pct": round(metrics["max_dd_pct"], 2),
    })

    # Test 2: Current .env params for comparison
    print("\n" + "=" * 70)
    print("TEST 2: CURRENT .ENV PARAMETERS (for comparison)")
    print("=" * 70)
    print(f"Params: {CURRENT_ENV_PARAMS}")

    equity2, trades2 = run_vwap_scalping(
        df_5m,
        cutoff=0.10,
        df_exit=df_1m,
        **CURRENT_ENV_PARAMS
    )

    if trades2:
        wins2 = sum(1 for t in trades2 if t > 0)
        win_rate2 = wins2 / len(trades2) * 100
        gross_profit2 = sum(t for t in trades2 if t > 0)
        gross_loss2 = abs(sum(t for t in trades2 if t < 0))
        profit_factor2 = gross_profit2 / gross_loss2 if gross_loss2 > 0 else float("inf")
    else:
        wins2 = 0
        win_rate2 = 0
        profit_factor2 = 0

    costs2 = len(trades2) * 0.30
    metrics2 = compute_metrics(equity2, trades2, bars_per_year=bars_per_year)
    net_return2 = metrics2["total_return_pct"] - costs2

    print(f"\nResults:")
    print(f"  Trades: {len(trades2)}")
    print(f"  Win Rate: {win_rate2:.1f}%")
    print(f"  Gross Profit: {gross_profit2:+.2f}%")
    print(f"  Gross Loss: {gross_loss2:+.2f}%")
    print(f"  Net Return: {net_return2:+.2f}%")
    print(f"  Profit Factor: {profit_factor2:.2f}")
    print(f"  Sharpe: {metrics2['sharpe']:.2f}")
    print(f"  Max DD: {metrics2['max_dd_pct']:.2f}%")

    results.append({
        "config": "current_env",
        "trades": len(trades2),
        "win_rate_pct": round(win_rate2, 1),
        "gross_profit": round(gross_profit2, 2),
        "gross_loss": round(gross_loss2, 2),
        "net_return_pct": round(net_return2, 2),
        "profit_factor": round(profit_factor2, 2) if profit_factor2 != float("inf") else 999.99,
        "sharpe": round(metrics2["sharpe"], 2),
        "max_dd_pct": round(metrics2["max_dd_pct"], 2),
    })

    # Save CSV
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

    # Difference summary
    print("\n" + "=" * 70)
    print("COMPARISON SUMMARY")
    print("=" * 70)
    print(f"{'Config':<20} {'Trades':>8} {'WR%':>8} {'Net%':>10} {'PF':>8}")
    print("-" * 70)
    print(f"{'Analysis Exact':<20} {len(trades):>8} {win_rate:>7.1f}% {net_return:>+9.2f}% {profit_factor:>7.2f}")
    print(f"{'Current .env':<20} {len(trades2):>8} {win_rate2:>7.1f}% {net_return2:>+9.2f}% {profit_factor2:>7.2f}")
    print("-" * 70)
    wr_diff = win_rate - win_rate2
    print(f"{'Difference':<20} {len(trades)-len(trades2):>+8d} {wr_diff:>-7.1f}% {net_return-net_return2:>+9.2f}%")

    if win_rate >= 50:
        print("\n*** ANALYSIS PARAMETERS ACHIEVED WIN RATE TARGET! ***")
    else:
        print(f"\n*** Target was ~54% WR, got {win_rate:.1f}% ***")


if __name__ == "__main__":
    main()