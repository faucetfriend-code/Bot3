#!/usr/bin/env python3
"""
UTM Anchored VWAP -- 15m Timeframe Comparison
==============================================
Same LEGACY vs UTM comparison as test_utm_anchored.py but on 15m bars.
Prior tests showed 67% WR on 1h -- 15m should sit between 5m noise and 1h
signal quality with meaningful trade counts.

Run from the BTV2/tests/ directory:
    python test_utm_15m.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from strategies import run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR, COST_PER_SIDE

STORAGE_ROOT = Path("G:/Candle Data")
BARS_PER_YEAR = INTERVAL_BARS_PER_YEAR["15m"]


def load(symbol="BTCUSDT", interval="15m"):
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
    if not path.exists():
        print(f"  ERROR: {path} not found")
        return None
    df = pd.read_parquet(path)
    df.columns = [c.capitalize() for c in df.columns]
    return df


def summarise(label, trades, equity, year):
    if not trades:
        return
    wins   = sum(1 for t in trades if t > 0)
    wr     = wins / len(trades) * 100
    losses = abs(sum(t for t in trades if t < 0))
    gains  = sum(t for t in trades if t > 0)
    pf     = gains / losses if losses > 0 else float("inf")
    m      = compute_metrics(equity, trades, bars_per_year=BARS_PER_YEAR)
    net    = (equity.iloc[-1] - 1.0) * 100
    print(f"  {label:<44} {year}  "
          f"n={len(trades):>3}  WR={wr:>5.1f}%  "
          f"PF={pf:>5.2f}  net={net:>+7.2f}%  sharpe={m['sharpe']:>+5.2f}")


# Base filters -- same as 5m comparison for apples-to-apples
BASE = dict(
    adx_max=25.0,
    rsi_max=55.0,
    volume_mult=1.5,
    stoch_oversold=25,
    use_htf_ema=False,
    use_htf_vwap=True,
)


def make_configs(entry_mode, sd):
    tag = f"{entry_mode}  sd={sd}"
    legacy = {**BASE, "entry_mode": entry_mode, "sd_threshold": sd,
              "use_anchored_vwap": False, "use_session_filter": False,
              "require_reversal_candle": False, "tp_mode": "atr"}
    utm    = {**BASE, "entry_mode": entry_mode, "sd_threshold": sd,
              "use_anchored_vwap": True, "use_session_filter": True,
              "require_reversal_candle": True, "tp_mode": "vwap"}
    return [
        (f"LEGACY  {tag}", legacy),
        (f"UTM     {tag}", utm),
    ]


def main():
    print("Loading BTCUSDT 15m data...")
    df_full = load()
    if df_full is None:
        return
    print(f"  {len(df_full):,} bars  "
          f"{df_full.index[0].date()} to {df_full.index[-1].date()}\n")

    years = [2020, 2021, 2022, 2023, 2024]

    print(f"  {'Label':<44} {'Year':>4}  {'n':>3}  {'WR%':>6}  {'PF':>5}  {'Net%':>8}  {'Sharpe':>7}")
    print("  " + "-" * 78)

    for entry_mode in ("mean_reversion", "bull_pullback"):
        for sd in (1.5, 2.0, 2.5):
            configs = make_configs(entry_mode, sd)
            for label, cfg in configs:
                for year in years:
                    df_y = df_full[
                        (df_full.index >= f"{year}-01-01") &
                        (df_full.index <  f"{year+1}-01-01")
                    ]
                    if len(df_y) < 100:
                        continue
                    equity, trades = run_vwap_scalping(df_y, cutoff=0.10, **cfg)
                    summarise(label, trades, equity, year)
            print()


if __name__ == "__main__":
    main()
