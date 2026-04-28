#!/usr/bin/env python3
"""
UTM Full Chain -- 5m Mean Reversion with SFP + MSS + PDH/PDL TP
================================================================
Tests the complete UTM confirmation chain on 5m mean_reversion:
  Anchored VWAP + session filter + SFP + MSS BOS + PDH/PDL TP

Three TP modes compared:
  vwap  -- session mean (previously tested, known to underperform on R:R)
  pdh   -- previous-day high/low (UTM liquidity pool target)
  atr   -- legacy ATR multiple

Run from the BTV2/tests/ directory:
    python test_utm_5m_full.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from strategies import run_vwap_scalping, compute_metrics, INTERVAL_BARS_PER_YEAR, COST_PER_SIDE

STORAGE_ROOT = Path("G:/Candle Data")
BARS_PER_YEAR = INTERVAL_BARS_PER_YEAR["5m"]
YEARS = [2020, 2021, 2022, 2023, 2024]


def load(symbol="BTCUSDT", interval="5m"):
    path = STORAGE_ROOT / f"{symbol}_{interval}.parquet"
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
    marker = " <--" if net > 0 else ""
    print(f"  {label:<52} {year}  "
          f"n={len(trades):>3}  WR={wr:>5.1f}%  "
          f"PF={pf:>5.2f}  net={net:>+7.2f}%{marker}")


# Full UTM base
UTM = dict(
    adx_max=25.0,
    rsi_max=55.0,
    volume_mult=1.5,
    stoch_oversold=25,
    use_htf_ema=False,
    use_htf_vwap=True,
    use_anchored_vwap=True,
    use_session_filter=True,
    require_reversal_candle=True,
    entry_mode="mean_reversion",
)


def run_configs(df_full):
    print(f"\n  {'Label':<52} {'Year':>4}  {'n':>3}  {'WR%':>6}  {'PF':>5}  {'Net%':>8}")
    print("  " + "-" * 86)

    for sd in (1.5, 2.0, 2.5):
        configs = [
            # Baseline: SFP only, VWAP TP (what we've been testing)
            (f"SFP only   sd={sd}  tp=vwap",
             {**UTM, "sd_threshold": sd, "require_mss": False, "tp_mode": "vwap"}),

            # SFP + MSS, three TP modes
            (f"SFP+MSS    sd={sd}  tp=vwap",
             {**UTM, "sd_threshold": sd, "require_mss": True, "tp_mode": "vwap"}),
            (f"SFP+MSS    sd={sd}  tp=pdh ",
             {**UTM, "sd_threshold": sd, "require_mss": True, "tp_mode": "pdh"}),
            (f"SFP+MSS    sd={sd}  tp=atr ",
             {**UTM, "sd_threshold": sd, "require_mss": True, "tp_mode": "atr"}),

            # SFP only with PDH TP (skip MSS, see if PDH alone helps)
            (f"SFP only   sd={sd}  tp=pdh ",
             {**UTM, "sd_threshold": sd, "require_mss": False, "tp_mode": "pdh"}),
        ]
        for label, cfg in configs:
            for year in YEARS:
                df_y = df_full[
                    (df_full.index >= f"{year}-01-01") &
                    (df_full.index <  f"{year+1}-01-01")
                ]
                if len(df_y) < 200:
                    continue
                equity, trades = run_vwap_scalping(df_y, cutoff=0.10, **cfg)
                summarise(label, trades, equity, year)
        print()


def main():
    print("Loading BTCUSDT 5m data...")
    df_full = load()
    print(f"  {len(df_full):,} bars  "
          f"{df_full.index[0].date()} to {df_full.index[-1].date()}")
    run_configs(df_full)


if __name__ == "__main__":
    main()
