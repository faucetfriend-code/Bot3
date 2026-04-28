#!/usr/bin/env python3
"""
VWAP Redesign — Quick Analytical Comparison
Uses analytical parameter-based scoring to evaluate options
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


def run_test(df_5m, df_exit, params, label):
    print(f"  {label}...", end=" ", flush=True)
    equity, trades = run_vwap_scalping(df_5m, cutoff=0.10, df_exit=df_exit, **params)
    
    if len(equity) < 2 or len(trades) == 0:
        print("No trades")
        return {"trades": 0, "net_pct": 0, "win_rate": 0, "profit_factor": 0, "sharpe": 0, "max_dd": 0}

    costs = len(trades) * COST_PER_TRADE
    gross_pct = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    net_pct = gross_pct - costs

    wins = [t for t in trades if t > 0]
    win_rate = len(wins) / len(trades) * 100

    gross_profit = sum(wins)
    gross_loss = abs(sum(t for t in trades if t < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.99

    m = compute_metrics(equity, trades, bars_per_year=INTERVAL_BARS_PER_YEAR["5m"])

    result = {
        "trades": len(trades), "net_pct": round(net_pct, 2), "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2), "sharpe": round(m.get("sharpe", 0), 2),
        "max_dd": round(m.get("max_dd_pct", 0), 2), "cagr": round(m.get("cagr_pct", 0), 2),
    }
    print(f"Trades={result['trades']}, Net={result['net_pct']:+.2f}%, WR={result['win_rate']:.1f}%")
    return result


def main():
    print("=" * 70)
    print("VWAP REDESIGN ANALYTICAL TEST")
    print("=" * 70)

    print("\nLoading data...")
    df_5m = load_parquet("BTCUSDT", "5m")
    df_1m = load_parquet("BTCUSDT", "1m")

    if df_5m is None:
        print("ERROR: No 5m data")
        sys.exit(1)

    print(f"5m: {len(df_5m)} bars")

    # Test on 6 months for speed (2020 H1 - best bull year)
    START = "2020-01-01"
    END = "2020-07-01"
    df_5m_test = df_5m[(df_5m.index >= START) & (df_5m.index < END)]
    df_1m_test = df_1m[(df_1m.index >= START) & (df_1m.index < END)] if df_1m is not None else None

    print(f"\nTest period: {START} to {END} ({len(df_5m_test)} bars)")

    BASELINE = {
        "sd_threshold": 2.0, "entry_mode": "bull_pullback", "adx_max": 30, "rsi_max": 50,
        "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.5, "pullback_bars": 3,
        "use_session_filter": True, "use_htf_vwap": True, "use_htf_ema": False, "htf_adx_max": 25,
        "use_trailing_stop": True, "trailing_atr": 1.2, "use_anchored_vwap": True,
        "require_reversal_candle": True, "tp_mode": "vwap",
    }

    # Options to test
    options = {
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

    print(f"\n{'='*50}")
    print("Testing options...")
    print(f"{'='*50}")

    for opt, params in options.items():
        m = run_test(df_5m_test, df_1m_test, params, opt)
        results.append({"option": opt, **m})

    df = pd.DataFrame(results)

    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(df.to_string(index=False))

    # Find best
    best = df.loc[df["net_pct"].idxmax()]
    print(f"\nBest by Net%: {best['option']} ({best['net_pct']:+.2f}%)")

    best_wr = df.loc[df["win_rate"].idxmax()]
    print(f"Best by Win Rate: {best_wr['option']} ({best_wr['win_rate']:.1f}%)")

    best_sharpe = df.loc[df["sharpe"].idxmax()]
    print(f"Best by Sharpe: {best_sharpe['option']} ({best_sharpe['sharpe']:.2f})")

    # Save
    out_path = Path(__file__).parent.parent / "BTV2" / "results" / "vwap_redesign_comprehensive.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"\nSaved: {out_path}")

    return df


if __name__ == "__main__":
    main()