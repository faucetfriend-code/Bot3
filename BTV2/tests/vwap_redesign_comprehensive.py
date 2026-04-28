#!/usr/bin/env python3
"""
VWAP Scalping Redesign — Comprehensive Test Suite (2018-2025)
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Any

sys.path.insert(0, str(Path(__file__).parent.parent / "BTV2"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import (
    run_vwap_scalping,
    compute_metrics,
    compute_vwap_anchored,
    compute_atr,
    compute_adx,
    compute_rsi,
    compute_ema,
    apply_butterworth,
    INTERVAL_BARS_PER_YEAR,
)

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


def compute_year_metrics(equity: pd.Series, trades: List[float]) -> Dict[str, Any]:
    if len(equity) < 2 or len(trades) == 0:
        return {"trades": 0, "net_pct": 0, "win_rate": 0, "profit_factor": 0,
                "sharpe": 0, "max_dd": 0, "cagr": 0}

    costs = len(trades) * COST_PER_TRADE
    gross_pct = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    net_pct = gross_pct - costs

    wins = [t for t in trades if t > 0]
    win_rate = len(wins) / len(trades) * 100

    gross_profit = sum(wins)
    gross_loss = abs(sum(t for t in trades if t < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.99

    bars_per_year = INTERVAL_BARS_PER_YEAR["5m"]
    m = compute_metrics(equity, trades, bars_per_year=bars_per_year)

    return {
        "trades": len(trades),
        "net_pct": round(net_pct, 2),
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "sharpe": round(m.get("sharpe", 0), 2),
        "max_dd": round(m.get("max_dd_pct", 0), 2),
        "cagr": round(m.get("cagr_pct", 0), 2),
    }


# Baseline config
BASELINE_PARAMS = {
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
}


def run_test(df_5m: pd.DataFrame, df_exit: pd.DataFrame | None,
            params: Dict, label: str) -> Dict[str, Any]:
    print(f"  {label}...", end=" ", flush=True)
    equity, trades = run_vwap_scalping(df_5m, cutoff=0.10, df_exit=df_exit, **params)
    metrics = compute_year_metrics(equity, trades)
    print(f"Trades={metrics['trades']}, Net={metrics['net_pct']:+.2f}%, WR={metrics['win_rate']:.1f}%")
    return metrics


def main():
    print("=" * 70)
    print("VWAP REDESIGN COMPREHENSIVE TEST (2018-2025)")
    print("=" * 70)

    print("\nLoading data...")
    df_5m = load_parquet("BTCUSDT", "5m")
    df_1m = load_parquet("BTCUSDT", "1m")
    df_1h = load_parquet("BTCUSDT", "1h")

    if df_5m is None:
        print("ERROR: Could not load 5m data")
        sys.exit(1)

    print(f"5m data: {len(df_5m)} bars ({df_5m.index[0]} to {df_5m.index[-1]})")

    YEARS = [
        ("2018", "2018-01-01", "2019-01-01"),
        ("2019", "2019-01-01", "2020-01-01"),
        ("2020", "2020-01-01", "2021-01-01"),
        ("2021", "2021-01-01", "2022-01-01"),
        ("2022", "2022-01-01", "2023-01-01"),
        ("2023", "2023-01-01", "2024-01-01"),
        ("2024", "2024-01-01", "2025-01-01"),
    ]

    MARKET_CONDITIONS = {
        "2018": "Bear", "2019": "Range", "2020": "Bull",
        "2021": "Bull", "2022": "Bear", "2023": "Range", "2024": "Bull",
    }

    all_results = []

    # Option configs
    options = {
        "Baseline": {**BASELINE_PARAMS},
        "A_HTF": {**BASELINE_PARAMS, "use_htf_ema": True, "htf_adx_max": 20},
        "B_StrongPullback": {**BASELINE_PARAMS, "adx_max": 20, "rsi_max": 40,
                       "volume_mult": 2.0, "pullback_bars": 4, "require_reversal_candle": True},
        "C_TrendFollow": {**BASELINE_PARAMS, "sd_threshold": 0.5, "entry_mode": "cross",
                       "atr_stop": 3.0, "atr_target": 6.0, "adx_max": 30},
        "D_Session": {**BASELINE_PARAMS, "use_session_filter": True, "entry_mode": "mean_reversion"},
        "E_Hybrid": {**BASELINE_PARAMS, "adx_max": 20, "rsi_max": 40, "volume_mult": 2.0,
                    "pullback_bars": 4, "use_htf_vwap": True, "use_htf_ema": True,
                    "htf_adx_max": 20, "require_reversal_candle": True},
        "F_RangeOnly": {**BASELINE_PARAMS, "adx_max": 20, "htf_adx_max": 15,
                      "use_trailing_stop": False, "sd_threshold": 2.5, "atr_target": 2.5},
    }

    for year, start, end in YEARS:
        print(f"\n{'='*60}")
        print(f"YEAR: {year}")
        print(f"{'='*60}")

        df_5m_y = df_5m[(df_5m.index >= start) & (df_5m.index < end)]
        df_1m_y = df_1m[(df_1m.index >= start) & (df_1m.index < end)] if df_1m is not None else None

        if df_5m_y.empty:
            continue

        for opt_name, params in options.items():
            label = f"{year}_{opt_name}"
            m = run_test(df_5m_y, df_1m_y, params, label)
            all_results.append({
                "option": opt_name, "year": year,
                "market_condition": MARKET_CONDITIONS.get(year, "Unknown"),
                **m
            })

    df_results = pd.DataFrame(all_results)

    print("\n" + "=" * 70)
    print("SUMMARY BY OPTION")
    print("=" * 70)

    option_summary = df_results.groupby("option").agg({
        "trades": "sum", "net_pct": "sum", "win_rate": "mean",
        "sharpe": "mean", "max_dd": "min",
    }).round(2)
    print(option_summary.to_string())

    results_dir = Path(__file__).parent.parent / "BTV2" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    csv_path = results_dir / "vwap_redesign_comprehensive.csv"
    df_results.to_csv(csv_path, index=False)
    print(f"\nResults saved to: {csv_path}")

    print("\n" + "=" * 70)
    print("BEST OPTIONS BY MARKET CONDITION")
    print("=" * 70)
    for cond in df_results["market_condition"].unique():
        if cond == "Unknown":
            continue
        subset = df_results[df_results["market_condition"] == cond]
        if len(subset) > 0:
            best = subset.loc[subset["net_pct"].idxmax()]
            print(f"\n{cond}: Best={best['option']} ({best['net_pct']:+.2f}%, {best['trades']} trades)")


if __name__ == "__main__":
    main()