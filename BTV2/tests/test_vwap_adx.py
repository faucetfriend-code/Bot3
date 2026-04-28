#!/usr/bin/env python3
"""VWAP parameter sweep focusing on ADX and trend"""

import sys
from pathlib import Path
import pandas as pd

STORAGE_ROOT = Path("G:/Candle Data")
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategies import run_vwap_scalping, compute_metrics

# Load data
print("Loading data...")
df = pd.read_parquet(STORAGE_ROOT / "BTCUSDT_5m.parquet")
df.columns = [c.capitalize() for c in df.columns]
df = df[(df.index >= "2024-01-01") & (df.index < "2025-01-01")]
print(f"Loaded {len(df):,} bars")

results = []

# Test different ADX thresholds
print("\nTesting different ADX thresholds...")
for adx in [20, 25, 30, 35, 40, 50, 60, 70, 80, 100]:
    eq, trades = run_vwap_scalping(
        df, 
        cutoff=0.10,
        sd_threshold=1.5,
        atr_stop=0.5,
        atr_target=2.0,
        ema_fast=9,
        ema_slow=20,
        use_trend_filter=False,
        use_volume_filter=False,
        volume_mult=1.0,
        use_trailing_stop=True,
        trailing_atr=1.0,
        adx_max=float(adx),
        rsi_max=70.0,
    )
    
    m = compute_metrics(eq, trades)
    gross = m['total_return_pct']
    net = gross - len(trades) * 0.30
    results.append({
        'adx': adx, 'trades': len(trades), 'wr': m['win_rate_pct'],
        'pf': m['profit_factor'], 'gross': gross, 'net': net
    })
    status = "✓" if (len(trades) >= 1000 and m['win_rate_pct'] >= 50 and net > 0) else ""
    print(f"ADX={adx}: Trades={len(trades)}, WR={m['win_rate_pct']:.1f}%, PF={m['profit_factor']:.2f}, Net={net:+.2f}% {status}")

# Test without ADX filter (very high)
print("\nTesting without ADX filter...")
eq, trades = run_vwap_scalping(
    df, 
    cutoff=0.10,
    sd_threshold=1.5,
    atr_stop=0.5,
    atr_target=2.0,
    ema_fast=9,
    ema_slow=20,
    use_trend_filter=False,
    use_volume_filter=False,
    volume_mult=1.0,
    use_trailing_stop=True,
    trailing_atr=1.0,
    adx_max=999.0,  # No ADX filter
    rsi_max=99.0,
)

m = compute_metrics(eq, trades)
gross = m['total_return_pct']
net = gross - len(trades) * 0.30
print(f"No ADX: Trades={len(trades)}, WR={m['win_rate_pct']:.1f}%, PF={m['profit_factor']:.2f}, Net={net:+.2f}%")

# Test with different RSI thresholds
print("\nTesting different RSI thresholds...")
for rsi in [30, 40, 50, 60, 70, 80, 90, 99]:
    eq, trades = run_vwap_scalping(
        df, 
        cutoff=0.10,
        sd_threshold=1.5,
        atr_stop=0.5,
        atr_target=2.0,
        ema_fast=9,
        ema_slow=20,
        use_trend_filter=False,
        use_volume_filter=False,
        volume_mult=1.0,
        use_trailing_stop=True,
        trailing_atr=1.0,
        adx_max=50.0,
        rsi_max=float(rsi),
    )
    
    m = compute_metrics(eq, trades)
    gross = m['total_return_pct']
    net = gross - len(trades) * 0.30
    status = "✓" if (len(trades) >= 1000 and m['win_rate_pct'] >= 50 and net > 0) else ""
    print(f"RSI={rsi}: Trades={len(trades)}, WR={m['win_rate_pct']:.1f}%, PF={m['profit_factor']:.2f}, Net={net:+.2f}% {status}")

# Test best combinations
print("\n\nTesting best combinations...")

configs = [
    {"sd": 1.5, "atr_stop": 0.3, "adx": 100, "rsi": 99, "vol": 1.0, "tf": False},
    {"sd": 1.5, "atr_stop": 0.5, "adx": 100, "rsi": 99, "vol": 1.0, "tf": False},
    {"sd": 2.0, "atr_stop": 0.5, "adx": 100, "rsi": 99, "vol": 1.0, "tf": False},
    {"sd": 1.5, "atr_stop": 0.3, "adx": 50, "rsi": 99, "vol": 1.0, "tf": True},
    {"sd": 1.0, "atr_stop": 0.3, "adx": 100, "rsi": 99, "vol": 1.0, "tf": False},
]

for cfg in configs:
    eq, trades = run_vwap_scalping(
        df, 
        cutoff=0.10,
        sd_threshold=cfg["sd"],
        atr_stop=cfg["atr_stop"],
        atr_target=2.0,
        ema_fast=9,
        ema_slow=20,
        use_trend_filter=cfg["tf"],
        use_volume_filter=False,
        volume_mult=cfg["vol"],
        use_trailing_stop=True,
        trailing_atr=1.0,
        adx_max=float(cfg["adx"]),
        rsi_max=float(cfg["rsi"]),
    )
    
    m = compute_metrics(eq, trades)
    gross = m['total_return_pct']
    net = gross - len(trades) * 0.30
    status = "✓" if (len(trades) >= 1000 and m['win_rate_pct'] >= 50 and net > 0) else ""
    print(f"SD={cfg['sd']}, ATR={cfg['atr_stop']}, ADX={cfg['adx']}, RSI={cfg['rsi']}, TF={cfg['tf']} | Trades={len(trades)}, WR={m['win_rate_pct']:.1f}%, PF={m['profit_factor']:.2f}, Net={net:+.2f}% {status}")
