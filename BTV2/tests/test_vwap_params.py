#!/usr/bin/env python3
"""Quick VWAP test using existing function"""

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

# Test default parameters
print("\nTesting default parameters...")
eq, trades = run_vwap_scalping(
    df, 
    cutoff=0.10,
    sd_threshold=2.5,
    atr_stop=1.0,
    atr_target=2.0,
    ema_fast=9,
    ema_slow=20,
    use_trend_filter=True,
    use_volume_filter=True,
    volume_mult=2.0,
    use_trailing_stop=True,
    trailing_atr=1.0,
    adx_max=22.0,
    rsi_max=45.0,
)

m = compute_metrics(eq, trades)
gross = m['total_return_pct']
net = gross - len(trades) * 0.30
print(f"Default: Trades={len(trades)}, WR={m['win_rate_pct']:.1f}%, PF={m['profit_factor']:.2f}, Gross={gross:+.2f}%, Net={net:+.2f}%")

# Test with more relaxed parameters
print("\nTesting more relaxed parameters...")
eq2, trades2 = run_vwap_scalping(
    df, 
    cutoff=0.10,
    sd_threshold=1.0,  # Much lower SD
    atr_stop=0.5,
    atr_target=2.0,
    ema_fast=9,
    ema_slow=20,
    use_trend_filter=False,  # No trend filter
    use_volume_filter=True,
    volume_mult=1.0,  # Lower volume threshold
    use_trailing_stop=True,
    trailing_atr=1.0,
    adx_max=50.0,  # Much higher ADX
    rsi_max=80.0,  # Higher RSI threshold
)

m2 = compute_metrics(eq2, trades2)
gross2 = m2['total_return_pct']
net2 = gross2 - len(trades2) * 0.30
print(f"Relaxed: Trades={len(trades2)}, WR={m2['win_rate_pct']:.1f}%, PF={m2['profit_factor']:.2f}, Gross={gross2:+.2f}%, Net={net2:+.2f}%")

# Test with very tight stops
print("\nTesting tight stops...")
eq3, trades3 = run_vwap_scalping(
    df, 
    cutoff=0.10,
    sd_threshold=1.0,
    atr_stop=0.2,  # Very tight stop
    atr_target=2.0,
    ema_fast=9,
    ema_slow=20,
    use_trend_filter=False,
    use_volume_filter=False,  # No volume filter
    volume_mult=1.0,
    use_trailing_stop=False,
    trailing_atr=1.0,
    adx_max=50.0,
    rsi_max=90.0,
)

m3 = compute_metrics(eq3, trades3)
gross3 = m3['total_return_pct']
net3 = gross3 - len(trades3) * 0.30
print(f"Tight: Trades={len(trades3)}, WR={m3['win_rate_pct']:.1f}%, PF={m3['profit_factor']:.2f}, Gross={gross3:+.2f}%, Net={net3:+.2f}%")

# Test with different cutoff values
print("\nTesting different Butterworth cutoffs...")
for cutoff in [0.03, 0.05, 0.10, 0.15, 0.20]:
    eq, trades = run_vwap_scalping(
        df, 
        cutoff=cutoff,
        sd_threshold=1.5,
        atr_stop=0.5,
        atr_target=2.0,
        ema_fast=9,
        ema_slow=20,
        use_trend_filter=False,
        use_volume_filter=True,
        volume_mult=1.0,
        use_trailing_stop=True,
        trailing_atr=1.0,
        adx_max=35.0,
        rsi_max=70.0,
    )
    m = compute_metrics(eq, trades)
    gross = m['total_return_pct']
    net = gross - len(trades) * 0.30
    status = "✓" if (len(trades) >= 1000 and m['win_rate_pct'] >= 50 and net > 0) else ""
    print(f"Cutoff={cutoff:.2f}: Trades={len(trades)}, WR={m['win_rate_pct']:.1f}%, PF={m['profit_factor']:.2f}, Net={net:+.2f}% {status}")
