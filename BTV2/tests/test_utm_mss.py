#!/usr/bin/env python3
"""
UTM MSS/BOS Confirmation -- 5m and 15m Comparison
==================================================
Tests the full UTM confirmation chain:
  SFP wick  ->  BOS above SFP high  ->  enter on next bar open

Compares UTM (SFP only) vs UTM+MSS across both timeframes.

Run from the BTV2/tests/ directory:
    python test_utm_mss.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from strategies import run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR, COST_PER_SIDE

STORAGE_ROOT = Path("G:/Candle Data")


def load(symbol="BTCUSDT", interval="5m"):
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if not path.exists():
        print(f"  MISSING: {path}")
        return None
    df = pd.read_parquet(path)
    df.columns = [c.capitalize() for c in df.columns]
    return df


def summarise(label, trades, equity, year, bars_per_year):
    if not trades:
        return
    wins   = sum(1 for t in trades if t > 0)
    wr     = wins / len(trades) * 100
    losses = abs(sum(t for t in trades if t < 0))
    gains  = sum(t for t in trades if t > 0)
    pf     = gains / losses if losses > 0 else float("inf")
    m      = compute_metrics(equity, trades, bars_per_year=bars_per_year)
    net    = (equity.iloc[-1] - 1.0) * 100
    print(f"  {label:<48} {year}  "
          f"n={len(trades):>3}  WR={wr:>5.1f}%  "
          f"PF={pf:>5.2f}  net={net:>+7.2f}%  sharpe={m['sharpe']:>+5.2f}")


# Shared UTM base (anchored VWAP + session + SFP)
UTM_BASE = dict(
    adx_max=25.0,
    rsi_max=55.0,
    volume_mult=1.5,
    stoch_oversold=25,
    use_htf_ema=False,
    use_htf_vwap=True,
    use_anchored_vwap=True,
    use_session_filter=True,
    require_reversal_candle=True,
    tp_mode="vwap",
)

YEARS = [2020, 2021, 2022, 2023, 2024]


def run_timeframe(tf_label, df_full, bars_per_year):
    print(f"\n{'='*72}")
    print(f"  TIMEFRAME: {tf_label}  ({len(df_full):,} bars)")
    print(f"{'='*72}")
    print(f"  {'Label':<48} {'Year':>4}  {'n':>3}  {'WR%':>6}  {'PF':>5}  {'Net%':>8}  {'Sharpe':>7}")
    print("  " + "-" * 82)

    for entry_mode in ("bull_pullback", "mean_reversion"):
        for sd in (1.5, 2.0, 2.5):
            tag = f"{entry_mode}  sd={sd}"
            configs = [
                (f"UTM SFP        {tag}", {**UTM_BASE, "entry_mode": entry_mode,
                                            "sd_threshold": sd, "require_mss": False}),
                (f"UTM SFP+MSS    {tag}", {**UTM_BASE, "entry_mode": entry_mode,
                                            "sd_threshold": sd, "require_mss": True,
                                            "mss_timeout_bars": 6}),
            ]
            for label, cfg in configs:
                for year in YEARS:
                    df_y = df_full[
                        (df_full.index >= f"{year}-01-01") &
                        (df_full.index <  f"{year+1}-01-01")
                    ]
                    if len(df_y) < 100:
                        continue
                    equity, trades = run_vwap_scalping(df_y, cutoff=0.10, **cfg)
                    summarise(label, trades, equity, year, bars_per_year)
            print()


def main():
    for interval in ("5m", "15m"):
        df = load(interval=interval)
        if df is None:
            continue
        bpy = INTERVAL_BARS_PER_YEAR[interval]
        run_timeframe(interval, df, bpy)


if __name__ == "__main__":
    from strategies import INTERVAL_BARS_PER_YEAR
    main()
