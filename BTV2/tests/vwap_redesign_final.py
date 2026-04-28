#!/usr/bin/env python3
"""
VWAP Redesign — Comprehensive Final Report Generation
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent / "BTV2"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR

STORAGE_ROOT = Path("G:/Candle Data")
COST_PER_TRADE = 0.003


def load_parquet(symbol: str, interval: str) -> pd.DataFrame | None:
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if path.exists():
        df = pd.read_parquet(path)
        df.columns = [c.capitalize() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    return None


def run_test(df_5m, df_exit, params):
    equity, trades = run_vwap_scalping(df_5m, cutoff=0.10, df_exit=df_exit, **params)
    
    if len(equity) < 2 or len(trades) == 0:
        return {"trades": 0, "net_pct": 0, "win_rate": 0, "profit_factor": 0, "sharpe": 0, "max_dd": 0, "cagr": 0}

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
        "trades": len(trades), "net_pct": round(net_pct, 2), "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2), "sharpe": round(m.get("sharpe", 0), 2),
        "max_dd": round(m.get("max_dd_pct", 0), 2), "cagr": round(m.get("cagr_pct", 0), 2),
    }


def main():
    print("=" * 70)
    print("VWAP REDESIGN COMPREHENSIVE FINAL TEST")
    print("=" * 70)

    df_5m = load_parquet("BTCUSDT", "5m")
    df_1m = load_parquet("BTCUSDT", "1m")

    if df_5m is None:
        print("ERROR: No 5m data")
        sys.exit(1)

    # Test all years
    TEST_PERIODS = [
        ("2018", "2018-01-01", "2019-01-01", "Bear"),
        ("2019", "2019-01-01", "2020-01-01", "Range"),
        ("2020", "2020-01-01", "2021-01-01", "Bull"),
        ("2021", "2021-01-01", "2022-01-01", "Bull"),
        ("2022", "2022-01-01", "2023-01-01", "Bear"),
        ("2023", "2023-01-01", "2024-01-01", "Range"),
        ("2024", "2024-01-01", "2025-01-01", "Bull"),
    ]

    BASELINE = {
        "sd_threshold": 2.0, "entry_mode": "bull_pullback", "adx_max": 30, "rsi_max": 50,
        "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.5, "pullback_bars": 3,
        "use_session_filter": True, "use_htf_vwap": True, "use_htf_ema": False, "htf_adx_max": 25,
        "use_trailing_stop": True, "trailing_atr": 1.2, "use_anchored_vwap": True,
        "require_reversal_candle": True, "tp_mode": "vwap",
    }

    # All options
    OPTIONS = {
        "Baseline": {**BASELINE},
        "A_HTF": {**BASELINE, "use_htf_ema": True, "htf_adx_max": 20},
        "B_StrongPB": {**BASELINE, "adx_max": 20, "rsi_max": 40, "volume_mult": 2.0,
                     "pullback_bars": 4, "require_reversal_candle": True},
        "C_Trend": {**BASELINE, "sd_threshold": 0.5, "entry_mode": "cross",
                    "atr_stop": 3.0, "atr_target": 6.0, "adx_max": 30},
        "D_Session": {**BASELINE, "entry_mode": "mean_reversion"},
        "E_Hybrid": {**BASELINE, "adx_max": 20, "rsi_max": 40, "volume_mult": 2.0,
                    "pullback_bars": 4, "use_htf_vwap": True, "use_htf_ema": True,
                    "htf_adx_max": 20, "require_reversal_candle": True},
        "F_Range": {**BASELINE, "adx_max": 20, "htf_adx_max": 15, "use_trailing_stop": False,
                   "sd_threshold": 2.5, "atr_target": 2.5},
    }

    results = []

    for year, start, end, cond in TEST_PERIODS:
        print(f"\n{year} ({cond})...")
        
        df_5m_p = df_5m[(df_5m.index >= start) & (df_5m.index < end)]
        df_1m_p = df_1m[(df_1m.index >= start) & (df_1m.index < end)] if df_1m is not None else None
        
        if df_5m_p.empty:
            continue
        
        for opt, params in OPTIONS.items():
            r = run_test(df_5m_p, df_1m_p, params)
            results.append({
                "year": year, "condition": cond, "option": opt, **r
            })
            print(f"  {opt}: Net={r['net_pct']:+.2f}%, WR={r['win_rate']:.1f}%, Trades={r['trades']}")

    # Write CSV
    results_dir = Path(__file__).parent.parent / "BTV2" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    csv_path = results_dir / "vwap_redesign_comprehensive.csv"
    
    df = pd.DataFrame(results)
    df.to_csv(csv_path, index=False)
    print(f"\n\nSaved: {csv_path}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY BY OPTION")
    print("=" * 70)
    
    summary = df.groupby("option").agg({
        "trades": "sum", "net_pct": "sum", "win_rate": "mean",
        "sharpe": "mean", "max_dd": "min",
    }).round(2)
    
    print(summary.to_string())
    
    print("\n" + "=" * 70)
    print("BEST BY MARKET CONDITION")
    print("=" * 70)
    
    for cond in df["condition"].unique():
        sub = df[df["condition"] == cond]
        if len(sub) > 0:
            best = sub.loc[sub["net_pct"].idxmax()]
            print(f"{cond}: {best['option']} ({best['net_pct']:+.2f}%, {best['win_rate']:.1f}% WR)")


if __name__ == "__main__":
    main()