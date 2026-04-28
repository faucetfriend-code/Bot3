#!/usr/bin/env python3
"""
VWAP Redesign — Full Validation Across Multiple Periods
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
    print("VWAP REDESIGN FULL VALIDATION")
    print("=" * 70)

    df_5m = load_parquet("BTCUSDT", "5m")
    df_1m = load_parquet("BTCUSDT", "1m")

    if df_5m is None:
        print("ERROR: No 5m data")
        sys.exit(1)

    # Test periods
    TEST_PERIODS = [
        ("2020-H1", "2020-01-01", "2020-07-01"),
        ("2020-H2", "2020-07-01", "2021-01-01"),
        ("2021-H1", "2021-01-01", "2021-07-01"),
        ("2023-H2", "2023-07-01", "2024-01-01"),
        ("2024-H1", "2024-01-01", "2024-07-01"),
    ]

    BASELINE = {
        "sd_threshold": 2.0, "entry_mode": "bull_pullback", "adx_max": 30, "rsi_max": 50,
        "volume_mult": 2.0, "atr_stop": 0.7, "atr_target": 3.5, "pullback_bars": 3,
        "use_session_filter": True, "use_htf_vwap": True, "use_htf_ema": False, "htf_adx_max": 25,
        "use_trailing_stop": True, "trailing_atr": 1.2, "use_anchored_vwap": True,
        "require_reversal_candle": True, "tp_mode": "vwap",
    }

    # Best: HTF filter (Option A/E)
    BEST_A = {**BASELINE, "use_htf_ema": True, "htf_adx_max": 20}

    results = []

    for period, start, end in TEST_PERIODS:
        print(f"\n{period} ({start} to {end})...")
        
        df_5m_p = df_5m[(df_5m.index >= start) & (df_5m.index < end)]
        df_1m_p = df_1m[(df_1m.index >= start) & (df_1m.index < end)] if df_1m is not None else None
        
        if df_5m_p.empty:
            print("  No data")
            continue
            
        print(f"  Baseline...", end=" ")
        r_base = run_test(df_5m_p, df_1m_p, BASELINE)
        print(f"Net={r_base['net_pct']:+.2f}%")
        
        print(f"  Best A...", end=" ")
        r_best = run_test(df_5m_p, df_1m_p, BEST_A)
        print(f"Net={r_best['net_pct']:+.2f}%")
        
        results.append({"period": period, "Baseline": r_base, "Best_A": r_best})

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    base_total = sum(r["Baseline"]["net_pct"] for r in results)
    best_total = sum(r["Best_A"]["net_pct"] for r in results)
    
    print(f"Baseline Total Net%: {base_total:+.2f}%")
    print(f"Best A (HTF) Total Net%: {best_total:+.2f}%")
    print(f"Improvement: {best_total - base_total:+.2f}%")

    # Save
    import csv
    out_path = Path(__file__).parent.parent / "BTV2" / "results" / "vwap_redesign_comprehensive.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(out_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["period", "option", "trades", "net_pct", "win_rate", "profit_factor", "sharpe", "max_dd", "cagr"])
        
        for r in results:
            writer.writerow([r["period"], "Baseline", r["Baseline"]["trades"], r["Baseline"]["net_pct"],
                           r["Baseline"]["win_rate"], r["Baseline"]["profit_factor"], r["Baseline"]["sharpe"],
                           r["Baseline"]["max_dd"], r["Baseline"]["cagr"]])
            writer.writerow([r["period"], "Best_A", r["Best_A"]["trades"], r["Best_A"]["net_pct"],
                           r["Best_A"]["win_rate"], r["Best_A"]["profit_factor"], r["Best_A"]["sharpe"],
                           r["Best_A"]["max_dd"], r["Best_A"]["cagr"]])
    
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()